// C86S serial link implementation.
//
// Why this file exists: the Cardputer build blitted the emulator framebuffer
// into an LGFX sprite and pushed it to an ST7789 panel.  Here the same 320x200
// byte framebuffer is packed to 4 bpp, RLE compressed per row, and streamed out
// of UART0.  Rows are deduplicated against a per-row FNV signature so an idle
// DOS prompt costs zero bytes and a typed character costs one row (~30-80 B).

#include <Arduino.h>
#include <string.h>

#include "c86_link.h"
#include "proto.h"
#include "gbConfig.h"
#include "gbGlobals.h"

#define FB_W FAKE86_FB_W
#define FB_H FAKE86_FB_H
#define ROW_BYTES (FB_W / 2)          /* 160 bytes per packed row */

#define TX_HEADER 7                   /* magic(2)+type+seq(2)+len(2) */
#define TXCAP 16384
#define PAYLOAD_BUDGET 12000          /* stop adding rows past this */
#define ROW_MAX_RLE (ROW_BYTES + 4)

static uint8_t txcap[TXCAP];
static uint16_t tx_len = 0, tx_pos = 0;
static bool tx_busy = false;
/* 脏行判定用「代数 + 内容签名」两级：
   一次整屏刷新把 gen 加一，于是所有行立刻变成"本代没发过"，发过的行记下
   row_gen = gen。这样刷新被预算切成好几帧也不会漏——没轮到的行下一代继续是脏的。
   早先只用 want_full 布尔，一帧装不下时后面的行再也不会重发，画面会残。 */
static uint32_t row_sig[FB_H];
static uint16_t row_gen[FB_H];
static uint16_t gen = 1;
static bool pass_full = true;
static uint16_t tx_seq = 0;
static uint8_t drop_count = 0;

/* 小帧（hello/stats/log/beep）走 8 槽环形队列，不是单槽：单槽时开机阶段
   （fb 分配 / 挂盘 / 客机内存 / 引导状态）连着好几条日志，前面的一条会被后面
   的覆盖，接上去看不到到底停在哪一步。 */
#define MSG_SLOTS 8
#define MSG_MAX 200
static uint8_t msgbuf[MSG_SLOTS][MSG_MAX];
static uint16_t msg_len[MSG_SLOTS];
static uint8_t msg_head = 0, msg_tail = 0, msg_count = 0;
static uint16_t msg_pos = 0;              /* 当前槽已发到第几字节 */

/* ---- inbound parser ---- */
static uint8_t rx_state = 0;
static uint8_t rx_type = 0;
static uint16_t rx_seq = 0, rx_len = 0, rx_got = 0;
static uint8_t rx_pl[64];

/* ---- key queue ---- */
#define KQ_LEN 128
static uint8_t kq[KQ_LEN];
static volatile uint16_t kq_head = 0, kq_tail = 0;
static volatile uint32_t kq_pushed = 0, kq_dropped = 0;
static volatile uint8_t cmd_pending = 0;

/* classic CGA 16-colour palette as RGB888 -- the PC paints with this */
static const uint8_t cga_rgb[16][3] = {
    {  0,   0,   0}, {  0,   0, 170}, {  0, 170,   0}, {  0, 170, 170},
    {170,   0,   0}, {170,   0, 170}, {170,  85,   0}, {170, 170, 170},
    { 85,  85,  85}, { 85,  85, 255}, { 85, 255,  85}, { 85, 255, 255},
    {255,  85,  85}, {255,  85, 255}, {255, 255,  85}, {255, 255, 255}
};

static uint8_t crc8_from(const uint8_t *p, int n) {
    uint8_t c = 0;
    for (int i = 0; i < n; i++) c = (uint8_t)(c * 31u + p[i]);
    return c;
}

static void put_header(uint8_t *b, uint8_t type, uint16_t seq, uint16_t len) {
    b[0] = C86_MAGIC0;
    b[1] = C86_MAGIC1;
    b[2] = type;
    b[3] = (uint8_t)(seq & 0xFF);
    b[4] = (uint8_t)(seq >> 8);
    b[5] = (uint8_t)(len & 0xFF);
    b[6] = (uint8_t)(len >> 8);
}

static void tx_begin(uint16_t total) {
    tx_len = total;
    tx_pos = 0;
    tx_busy = total > 0;
}

static void small_frame(uint8_t type, const uint8_t *payload, uint16_t len) {
    if (len + TX_HEADER + 1 > MSG_MAX) len = MSG_MAX - TX_HEADER - 1;
    if (msg_count == MSG_SLOTS) {                 /* 满了丢最老的 */
        msg_tail = (uint8_t)((msg_tail + 1) % MSG_SLOTS);
        msg_count--;
        msg_pos = 0;                              /* 被丢的正是发了一半的那槽 */
    }
    uint8_t *slot = msgbuf[msg_head];
    put_header(slot, type, tx_seq++, len);
    if (len) memcpy(slot + TX_HEADER, payload, len);
    slot[TX_HEADER + len] = crc8_from(slot + 2, 5 + (int)len);
    msg_len[msg_head] = (uint16_t)(TX_HEADER + len + 1);
    msg_head = (uint8_t)((msg_head + 1) % MSG_SLOTS);
    msg_count++;
}

/* ------------------------------------------------------------------ */
/* RLE                                                                */
/* ------------------------------------------------------------------ */
static void rle_encode(const uint8_t *src, int n, uint8_t *dst, int *outlen) {
    int i = 0, o = 0;
    while (i < n) {
        int j = i;
        while (j + 1 < n && src[j + 1] == src[i] && (j + 1 - i) < 128) j++;
        int run = j - i + 1;
        if (run >= 3) {
            dst[o++] = (uint8_t)(0x80 | (run - 1));
            dst[o++] = src[i];
            i = j + 1;
        } else {
            const int start = i;
            while (i < n) {
                if (i + 2 < n && src[i] == src[i + 1] && src[i + 1] == src[i + 2])
                    break;
                i++;
                if (i - start == 128) break;
            }
            int lit = i - start;
            if (lit <= 0) { lit = 1; i = start + 1; }
            dst[o++] = (uint8_t)(lit - 1);
            memcpy(dst + o, src + start, (size_t)lit);
            o += lit;
        }
    }
    *outlen = o;
}

/* ------------------------------------------------------------------ */
/* video                                                              */
/* ------------------------------------------------------------------ */
void c86_link_request_full(void) {
    gen++;
    if (gen == 0) {                     /* 绕回：把过期标记一起清掉 */
        memset(row_gen, 0, sizeof(row_gen));
        gen = 1;
    }
    pass_full = true;
}

bool c86_link_video_capture(bool graphics_mode) {
    if (tx_busy) { if (drop_count < 255) drop_count++; return false; }
    if (!gb_frame_buffer) return false;

    uint8_t *base = txcap + TX_HEADER;
    uint8_t *p = base + 2;                 /* [flags][rows] */
    int rows = 0;

    for (int y = 0; y < FB_H; y++) {
        const uint8_t *src = gb_frame_buffer + (size_t)y * FB_W;
        uint8_t rb[ROW_BYTES];
        uint32_t sig = 2166136261u;

        for (int x = 0; x < ROW_BYTES; x++) {
            uint8_t v = (uint8_t)(((src[2 * x] & 0x0F) << 4) |
                                  (src[2 * x + 1] & 0x0F));
            rb[x] = v;
            sig = (sig ^ v) * 16777619u;
        }
        if (row_gen[y] == gen && sig == row_sig[y]) continue;

        int rl = 0;
        rle_encode(rb, ROW_BYTES, p + 3, &rl);
        if ((int)(p - base) + 3 + rl > PAYLOAD_BUDGET) break;   /* next frame */
        if (rows >= 255) break;

        row_gen[y] = gen;
        row_sig[y] = sig;
        *p++ = (uint8_t)y;
        *p++ = (uint8_t)(rl & 0xFF);
        *p++ = (uint8_t)(rl >> 8);
        p += rl;
        rows++;
    }

    if (rows == 0) return false;

    /* 整屏刷新这一代发完了吗？发完就把 FULL 标志摘掉（仅作提示用） */
    if (pass_full) {
        bool remaining = false;
        for (int y = 0; y < FB_H; y++) {
            if (row_gen[y] != gen) { remaining = true; break; }
        }
        if (!remaining) pass_full = false;
    }

    base[0] = (uint8_t)((pass_full ? C86_VF_FULL : 0) |
                        (graphics_mode ? C86_VF_GFX : 0));
    base[1] = (uint8_t)rows;

    const uint16_t len = (uint16_t)(p - base);
    put_header(txcap, C86_T_VIDEO, tx_seq++, len);
    txcap[TX_HEADER + len] = crc8_from(txcap + 2, 5 + (int)len);
    tx_begin((uint16_t)(TX_HEADER + len + 1));
    return true;
}

bool c86_link_busy(void) { return tx_busy || msg_count > 0; }

/* 发送调度：
   1) 正在发的视频帧优先，且必须整帧发完才轮到消息 —— 把一条消息插进视频帧中间
      会让对端的帧解析器把后半帧当噪声丢掉。
   2) 消息按游标分块推，不再要求"一次就能全部塞进发送缓冲"。老写法要求
      availableForWrite() >= len+4，而视频路径总把环灌到只剩 16 字节，
      于是超过 16 字节的消息永远发不出去（开机日志和 trace 全被饿死）。 */
void c86_link_tx_service(void) {
    for (int guard = 0; guard < 64; guard++) {
        const int room = Serial.availableForWrite();
        if (room <= 2) return;

        if (tx_busy) {
            int chunk = (int)(tx_len - tx_pos);
            if (chunk <= 0) { tx_busy = false; continue; }
            if (chunk > room) chunk = room;
            Serial.write(txcap + tx_pos, (size_t)chunk);
            tx_pos = (uint16_t)(tx_pos + chunk);
            if (tx_pos >= tx_len) tx_busy = false;
            continue;
        }

        if (msg_count == 0) return;

        const uint16_t n = msg_len[msg_tail];
        int chunk = (int)n - (int)msg_pos;
        if (chunk <= 0) {
            msg_tail = (uint8_t)((msg_tail + 1) % MSG_SLOTS);
            msg_count--;
            msg_pos = 0;
            continue;
        }
        if (chunk > room) chunk = room;
        Serial.write(msgbuf[msg_tail] + msg_pos, (size_t)chunk);
        msg_pos = (uint16_t)(msg_pos + chunk);
        if (msg_pos >= n) {
            msg_tail = (uint8_t)((msg_tail + 1) % MSG_SLOTS);
            msg_count--;
            msg_pos = 0;
        }
    }
}

/* ------------------------------------------------------------------ */
/* small frames                                                       */
/* ------------------------------------------------------------------ */
void c86_link_send_hello(void) {
    uint8_t pl[C86_HELLO_HDR + 40];    int o = 0;
    pl[o++] = C86_PROTO_VER;
    pl[o++] = 0;
    pl[o++] = (uint8_t)(FB_W & 0xFF);
    pl[o++] = (uint8_t)(FB_W >> 8);
    pl[o++] = (uint8_t)(FB_H & 0xFF);
    pl[o++] = (uint8_t)(FB_H >> 8);
    for (int i = 0; i < 16; i++) {
        pl[o++] = cga_rgb[i][0];
        pl[o++] = cga_rgb[i][1];
        pl[o++] = cga_rgb[i][2];
    }
    const char *id = "C86S esp32-8mb";
    const int n = (int)strlen(id);
    memcpy(pl + o, id, (size_t)n);
    o += n;
    small_frame(C86_T_HELLO, pl, (uint16_t)o);
}

void c86_link_send_stats(uint8_t fps, uint16_t guest_kb, uint16_t heap_kb,
                         uint8_t mode, uint8_t drops) {
    uint8_t pl[7];
    pl[0] = fps;
    pl[1] = mode;
    pl[2] = (uint8_t)(guest_kb & 0xFF);
    pl[3] = (uint8_t)(guest_kb >> 8);
    pl[4] = (uint8_t)(heap_kb & 0xFF);
    pl[5] = (uint8_t)(heap_kb >> 8);
    pl[6] = drops;
    small_frame(C86_T_STATS, pl, sizeof(pl));
}

void c86_link_log(const char *text) {
    if (!text) return;
    int n = (int)strlen(text);
    if (n > 200) n = 200;
    small_frame(C86_T_LOG, (const uint8_t *)text, (uint16_t)n);
}

void c86_link_beep(uint16_t freq_hz, uint16_t ms) {
    static uint32_t last_ms = 0;
    const uint32_t now = millis();
    if (now - last_ms < 120) return;        /* PC speaker gates chatter */
    last_ms = now;
    uint8_t pl[4];
    pl[0] = (uint8_t)(freq_hz & 0xFF);
    pl[1] = (uint8_t)(freq_hz >> 8);
    pl[2] = (uint8_t)(ms & 0xFF);
    pl[3] = (uint8_t)(ms >> 8);
    small_frame(C86_T_BEEP, pl, sizeof(pl));
}

/* ------------------------------------------------------------------ */
/* inbound                                                            */
/* ------------------------------------------------------------------ */
static void kq_push(uint8_t b) {
    const uint16_t next = (uint16_t)((kq_head + 1) % KQ_LEN);
    if (next == kq_tail) { kq_dropped++; return; }   /* full: drop */
    kq[kq_head] = b;
    kq_head = next;
    kq_pushed++;
}

void c86_link_kb_stats(uint32_t *pushed, uint32_t *dropped) {
    if (pushed) *pushed = kq_pushed;
    if (dropped) *dropped = kq_dropped;
}

bool c86_link_take_key(uint8_t *out) {
    if (kq_tail == kq_head) return false;
    *out = kq[kq_tail];
    kq_tail = (uint16_t)((kq_tail + 1) % KQ_LEN);
    return true;
}

/* 客人机复位时把环清空：否则上一次会话残留的扫描码（可能有半个扩展键
   前缀 0xE0）会被新启动的 POST 当成真实按键吃掉，造成错键。 */
void c86_link_flush_keys(void) {
    kq_head = 0;
    kq_tail = 0;
    kq_pushed = 0;
    kq_dropped = 0;
}

uint8_t c86_link_take_cmds(void) {
    const uint8_t c = cmd_pending;
    cmd_pending = 0;
    return c;
}

/* PC 要求换链路速率：先把老速率上排队的帧丢掉（对端已经切走了，发过去就是乱码），
   等 TX 排空再切 UART，然后在新速率上补一个 HELLO 让 PC 确认握手成功。 */
static uint32_t cur_baud = C86_BAUD;     /* 当前实际速率，不是编译期默认值 */

void c86_link_set_baud(uint32_t baud) {
    if (baud < 9600 || baud > 3000000) return;   /* 错帧或离谱的值，忽略 */
    tx_busy = false;
    msg_head = msg_tail = msg_count = 0;
    Serial.flush();                              /* 等 TX FIFO 真的排空 */
    /* 注意：这里必须和"当前速率"比，不能和 C86_BAUD 比。早先写成
       `if (baud == C86_BAUD) return;` 的后果是：板子已经被切到 230400 后，
       PC 想切回 921600 会被当成"本来就是上电速率"直接 return，既不切换也不
       回 HELLO —— PC 侧只能判成"板子没接受"。 */
    if (baud != cur_baud) {
        Serial.updateBaudRate(baud);
        cur_baud = baud;
    }
    rx_state = 0;
    rx_got = 0;
    c86_link_send_hello();
    for (int i = 0; i < 8; i++) c86_link_tx_service();
}

static void dispatch_inbound(void) {
    if (rx_type == C86_T_KEY && rx_len >= 2) {
        const uint8_t sc = rx_pl[0];
        const uint8_t fl = rx_pl[1];
        if (fl & C86_KF_EXT) kq_push(0xE0);
        kq_push(sc);
    } else if (rx_type == C86_T_CMD && rx_len >= 1) {
        switch (rx_pl[0]) {
            case C86_CMD_RESET:     cmd_pending |= C86_CMD_RESET; break;
            case C86_CMD_FULLFRAME: c86_link_request_full();
                                    cmd_pending |= C86_CMD_FULLFRAME; break;
            case C86_CMD_PING:      c86_link_send_hello(); break;
            case C86_CMD_SETBAUD:
                if (rx_len >= 5) {
                    const uint32_t nb = (uint32_t)rx_pl[1]
                                      | ((uint32_t)rx_pl[2] << 8)
                                      | ((uint32_t)rx_pl[3] << 16)
                                      | ((uint32_t)rx_pl[4] << 24);
                    c86_link_set_baud(nb);
                }
                break;
            default: break;
        }
    }
}

void c86_link_rx_poll(void) {
    while (Serial.available() > 0) {
        const uint8_t c = (uint8_t)Serial.read();
        switch (rx_state) {
            case 0: if (c == C86_MAGIC0) rx_state = 1; break;
            case 1: rx_state = (c == C86_MAGIC1) ? 2 : (c == C86_MAGIC0 ? 1 : 0); break;
            case 2: rx_type = c; rx_state = 3; break;
            case 3: rx_seq = c; rx_state = 4; break;
            case 4: rx_seq |= (uint16_t)(c << 8); rx_state = 5; break;
            case 5: rx_len = c; rx_state = 6; break;
            case 6:
                rx_len |= (uint16_t)(c << 8);
                rx_got = 0;
                if (rx_len > sizeof(rx_pl)) rx_state = 0;
                else rx_state = (rx_len == 0) ? 8 : 7;
                break;
            case 7:
                rx_pl[rx_got++] = c;
                if (rx_got >= rx_len) rx_state = 8;
                break;
            case 8:
            default:
                dispatch_inbound();          /* c is the crc byte, not checked */
                rx_state = 0;
                break;
        }
    }
}

void c86_link_init(void) {
    memset(row_sig, 0, sizeof(row_sig));
    memset(row_gen, 0, sizeof(row_gen));     /* 第 1 代：所有行都是脏的 */
    gen = 1;
    pass_full = true;
    tx_busy = false;
    msg_head = msg_tail = msg_count = 0;
    msg_pos = 0;
    tx_seq = 0;
}
