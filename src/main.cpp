// C86S -- 8086 PC emulator (Fake86 / TinyFake86 lineage) on a bare ESP32.
//
// Origin: ESP32TinyFake86 (ackerman, TTGO VGA32) -> CardPuter86 (M5Stack
// Cardputer, ESP32-S3).  This port keeps the emulator core byte-identical and
// swaps the machine layer:
//   * 320x200 framebuffer  -> RLE frame stream on UART0  (serial/c86_link.cpp)
//   * Cardputer key matrix -> PS/2 scan codes from the PC (same file)
//   * FatFS image on flash -> raw "disk" partition       (serial/cardputer_storage.cpp)
//   * I2S speaker          -> BEEP frames to the PC
//   * TFT settings menu    -> the PC client's UI
//
// The counterpart client is tools/c86host.py.

#include <Arduino.h>
#include <string.h>

#include "gbConfig.h"
#include "gbGlobals.h"
#include "hardware.h"
#include "cpu.h"
#include "render.h"
#include "video.h"
#include "timing.h"
#include "disk.h"
#include "i8253.h"
#include "i8259.h"
#include "i8237.h"
#include "guest_memory.h"
#include "cardputer_display.h"
#include "cardputer_storage.h"
#include "cardputer_speaker.h"
#include "cardputer_rtc.h"
#include "c86_link.h"
#include "proto.h"

void draw(void);
void doscrmodechange(void);

// =====================================================================
// Globals (moved verbatim from CardPuter86.ino; gbGlobals.h declares them)
// =====================================================================
unsigned char gb_invert_color = 0;
unsigned char gb_silence = 0;

unsigned char gb_delay_tick_cpu_milis = use_lib_delay_tick_cpu_milis;
unsigned char gb_auto_delay_cpu = use_lib_delay_tick_cpu_auto;
unsigned char gb_vga_poll_milis = use_lib_vga_poll_milis;
/* 10 ms instead of the Cardputer's 20 ms: the guest gets one scan code per
   poll (the emulated 8042 has a single data register), so this doubles the
   sustainable typing rate to ~50 keys/s. */
unsigned char gb_keyboard_poll_milis = 10;
unsigned char gb_timers_poll_milis = use_lib_timers_poll_milis;

unsigned char gb_frec_speaker_low = 0;
unsigned char gb_frec_speaker_high = 0;
unsigned char gb_cont_frec_speaker = 0;
volatile int gb_frecuencia01 = 0;
volatile int gb_volumen01 = 0;

unsigned char gb_use_remap_cartdridge = 0;
unsigned char gb_use_snarare_madmix = 0;

/* 0 = 4x8 glyphs -> the whole 80x25 text screen fits in 320x200.
   1 = 8x8 glyphs, which on the Cardputer's 240px LCD was fine but here would
   only ever show 40 of the 80 DOS columns.  DOS at column 41 is unusable, so
   the serial build goes wide and lets the PC client scale it up. */
unsigned char gb_font_8x8 = 0;

unsigned int lasttick;
unsigned char gb_reset = 0;
unsigned char gb_id_cur_com = 0;
unsigned char tiempo_vga = 0;
unsigned int jj_ini_cpu, jj_end_cpu, jj_ini_vga, jj_end_vga;
unsigned int gb_max_cpu_ticks, gb_min_cpu_ticks, gb_cur_cpu_ticks;
unsigned int gb_max_vga_ticks, gb_min_vga_ticks, gb_cur_vga_ticks;
unsigned long tickgap = 0;
unsigned char port3da = 0;
unsigned char gb_key_cur[80];
unsigned char gb_key_before[80];

const unsigned char palettecga[16] = {
    0x00, 0xAA, 0x00, 0xAA,
    0x00, 0xAA, 0x00, 0xAA,
    0x55, 0xFF, 0x55, 0xFF,
    0x55, 0xFF, 0x55, 0xFF
};

unsigned char gbKeepAlive = 0;
unsigned char vidmode = 5;
unsigned char gb_force_set_cga = 0;
unsigned char gb_force_load_com = 0;
unsigned char gb_force_boot = 0;

unsigned char gb_portramTiny[51];
void *gb_portTiny_write_callback[5];
void *gb_portTiny_read_callback[5];
unsigned char cf;
unsigned char hdcount = 0;
unsigned char running = 0;

unsigned char gb_video_cga[16384];

unsigned char slowsystem = 0;
unsigned short int constantw = 0, constanth = 0;
unsigned char bootdrive = 0;
unsigned char speakerenabled = 0;
unsigned char renderbenchmark = 0;
unsigned char scrmodechange = 0;

unsigned int x, y;
unsigned short int segregs[4];

volatile unsigned char keymap[256];
volatile unsigned char oldKeymap[256];

// =====================================================================
// Emulated chipset bring-up
// =====================================================================
static void emu_hw_init(void) {
    memset(gb_portramTiny, 0, sizeof(gb_portramTiny));
    memset(gb_portTiny_write_callback, 0, sizeof(gb_portTiny_write_callback));
    memset(gb_portTiny_read_callback, 0, sizeof(gb_portTiny_read_callback));
    init8253();
    init8259();
    init8237();
    initVideoPorts();
    inittiming();
    initscreen();
}

bool CreateRAM(void) { return guest_memory_init(); }
void ClearRAM(void) { guest_memory_clear(); }

static void emu_reset_guest(void) {
    ClearRAM();
    memset(gb_video_cga, 0, sizeof(gb_video_cga));
    memset(gb_key_cur, 1, sizeof(gb_key_cur));
    memset(gb_key_before, 1, sizeof(gb_key_before));
    /* 键盘控制器也要复位：0x64 的输出缓冲满标志 + 0x60 里的残留字节，
       不清的话新启动的 POST 会读到假状态并判定键盘异常 → 复位后无法输入。
       键盘环里的半截扫描码同样要丢。 */
    gb_portramTiny[fast_tiny_port_0x60] = 0;
    gb_portramTiny[fast_tiny_port_0x64] = 0;
    c86_link_flush_keys();
    running = 1;
    reset86();
    emu_hw_init();
}

// =====================================================================
// Keyboard: one PS/2 byte per guest poll
// =====================================================================
static uint32_t kbd_delivered = 0, kbd_forced = 0;

static void kbd_deliver_one(void) {
    /* 客人机还没取走上一个字节时不要覆盖它。
       256KB 分页下客人机一次键盘中断要跑十几毫秒，而这里每 10ms 就想送一个
       新字节；硬塞会把还没被读走的扫描码冲掉 —— 表现就是「打字丢字符」，
       于是 `minigame` 变成 `minig`、Enter 变成一条无效命令（用户看到的
       「进不去游戏」「Q 退不出」多半都是这么来的）。读 0x60 会清 OBF，
       所以等标志位落下再送就是天然的流控。
       万一客人机因为别的原因不再读 0x60（复位中途、被挂起），bit1 会一直
       挂着把键盘卡死，所以再加一个 ~0.5 秒的安全阀：等太久就强制清位放行。 */
    static uint16_t stall = 0;
    if (gb_portramTiny[fast_tiny_port_0x64] & 0x02) {
        if (++stall < 50) return;
        stall = 0;
        kbd_forced++;
        gb_portramTiny[fast_tiny_port_0x64] &= (uint8_t)~0x02;
    } else {
        stall = 0;
    }
    uint8_t sc = 0;
    if (!c86_link_take_key(&sc)) return;
    kbd_delivered++;
    gb_portramTiny[fast_tiny_port_0x60] = sc;
    gb_portramTiny[fast_tiny_port_0x64] |= 2;
    doirq(1);
}

// =====================================================================
// 客人机状态探针
// 上游只在串口 debug 版里打这些量（guest CS:IP / BDA 内存 / INT19 次数），
// 而这里串口被协议占满了，所以改成定期塞一条 LOG 帧。客人机黑屏时，
// 唯一能在不插屏幕的前提下知道它停在哪的办法就是这几行。
// =====================================================================
static void emu_trace(void) {
    char buf[200];
    const unsigned cs = GetRegCS();
    const unsigned ip = GetRegIP();
    const unsigned lin = (cs << 4) + ip;
    /* 显存里到底有没有字？黑屏时必须先分清是「客人机没写」还是「我们没画」。
       非零字节数 + 前 8 个字节的 hex 一起看一眼就知道。 */
    unsigned vb = 0;
    for (int i = 0; i < 4096; i++) if (gb_video_cga[i]) vb++;
    /* 帧缓冲里画的像素数：和 vb 对照就能分辨「客人机没写显存」和
       「draw() 没把显存画进帧缓冲」。 */
    unsigned fb_nz = 0;
    if (gb_frame_buffer) {
        for (int i = 0; i < FAKE86_FB_W * FAKE86_FB_H; i++)
            if (gb_frame_buffer[i]) fb_nz++;
    }
    uint32_t io_r = 0, io_w = 0, io_f = 0, io_l = 0;
    c86_storage_io_stats(&io_r, &io_w, &io_f, &io_l);
    uint32_t g_calls = 0, g_unm = 0, g_wrong = 0;
    uint8_t mnt = 0, gdrv = 0;
    c86_storage_geo_stats(&g_calls, &g_unm, &g_wrong, &mnt, &gdrv);
    snprintf(buf, sizeof(buf),
             "trace %04X:%04X op%02X%02X vid%u i19%lu ins%lu cpu%luus vb%u fb%u "
             "io%lu/%lu/%lu lba%lu geo%lu mnt%u/%02X "
             "ih13=%lu ih16=%lu ih19=%lu",
             cs, ip,
             (unsigned)read86(lin), (unsigned)read86(lin + 1),
             (unsigned)vidmode,
             (unsigned long)g_cdp_int19_count,
             (unsigned long)g_cdp_instrs,
             (unsigned long)gb_cur_cpu_ticks, vb, fb_nz,
             (unsigned long)io_r, (unsigned long)io_w, (unsigned long)io_f,
             (unsigned long)io_l,
             (unsigned long)g_calls, (unsigned)mnt, (unsigned)gdrv,
             (unsigned long)g_cdp_int_hist[0x13],
             (unsigned long)g_cdp_int_hist[0x16],
             (unsigned long)g_cdp_int_hist[0x19]);
    c86_link_log(buf);

    /* 最后一行非空文本的内容：DOS 到底停在哪个界面，看这 40 个字符最快 */
    int lastrow = -1;
    for (int r = 0; r < 25; r++) {
        for (int c = 0; c < 80; c++) {
            const unsigned char ch_ = gb_video_cga[(r * 80 + c) * 2];
            if (ch_ && ch_ != 0x20) { lastrow = r; break; }
        }
    }
    char scr[48];
    int o = 0;
    if (lastrow >= 0) {
        for (int c = 0; c < 40; c++) {
            const unsigned char ch_ = gb_video_cga[(lastrow * 80 + c) * 2];
            scr[o++] = (ch_ >= 32 && ch_ < 127) ? (char)ch_ : '.';
        }
    } else {
        scr[o++] = '('; scr[o++] = 'b'; scr[o++] = 'l'; scr[o++] = 'a';
        scr[o++] = 'n'; scr[o++] = 'k'; scr[o++] = ')';
    }
    scr[o] = 0;
    snprintf(buf, sizeof(buf), "scr r%d [%s]", lastrow, scr);
    c86_link_log(buf);
}

// =====================================================================
// 键盘通路计数：push=串口收进队列 del=投给8042 read=客人机读走
// force=安全阀强推 drop=队满丢弃。打字丢字符时看这四个数就知道死在哪一段。
// =====================================================================
extern uint32_t g_kbd_reads;
static void emu_kb_trace(void) {
    /* 正常时一声不响，只在「有字节没走到头」时开口：
       队满丢弃、安全阀强推、或者队列里的字节数比客人机读走的差得太多。
       常开的话每 2 秒刷一条，会把 GUI 的日志栏冲掉。 */
    static uint32_t last_pushed = 0, last_del = 0, last_read = 0;
    static uint32_t last_forced = 0, last_drop = 0;
    char buf[140];
    uint32_t pushed = 0, dropped = 0;
    c86_link_kb_stats(&pushed, &dropped);
    const uint32_t dp = pushed - last_pushed;
    const uint32_t dd = kbd_delivered - last_del;
    const uint32_t dr = g_kbd_reads - last_read;
    const uint32_t df = kbd_forced - last_forced;
    const uint32_t dx = dropped - last_drop;
    if (df || dx || dp > dr + 4 || dd > dr + 4) {
        snprintf(buf, sizeof(buf),
                 "kb push=%lu del=%lu read=%lu force=%lu drop=%lu",
                 (unsigned long)pushed, (unsigned long)kbd_delivered,
                 (unsigned long)g_kbd_reads, (unsigned long)kbd_forced,
                 (unsigned long)dropped);
        c86_link_log(buf);
    }
    last_pushed = pushed;
    last_del = kbd_delivered;
    last_read = g_kbd_reads;
    last_forced = kbd_forced;
    last_drop = dropped;
}

// =====================================================================
// setup
// =====================================================================
void setup() {
    setCpuFrequencyMhz(240);

    Serial.begin(C86_BAUD);
    Serial.setTxBufferSize(8192);
    c86_link_init();

    cardputer_display_init();
    if (!gb_frame_buffer) {
        while (true) {                     /* nothing else can work */
            c86_link_log("FATAL: 320x200 framebuffer alloc failed");
            c86_link_tx_service();
            delay(1000);
        }
    }
    tft_log("C86S booting (ESP32 + serial video)");

    /* disk first: it needs no heap, and mounting it before the guest RAM
       allocation keeps the big contiguous blocks free for the page cache */
    cardputer_storage_init_and_select();
    cardputer_rtc_init();

    if (!CreateRAM()) {
        char buf[96];
        snprintf(buf, sizeof(buf), "FATAL: guest RAM failed: %.60s",
                 guest_memory_diag_text());
        c86_link_log(buf);
        while (true) {
            c86_link_tx_service();
            delay(1000);
        }
    }
    {
        char buf[96];
        snprintf(buf, sizeof(buf), "guest RAM: %lu KB, %u cache pages",
                 (unsigned long)guest_memory_ram_kb(),
                 (unsigned)guest_memory_cache_pages());
        c86_link_log(buf);
    }

    ClearRAM();
    SetRAMTruco();
    bootstrapPoll();
    memset(gb_key_cur, 1, sizeof(gb_key_cur));
    memset(gb_key_before, 1, sizeof(gb_key_before));
    FuerzoParityRAM();

    bootdrive = gb_disk_image.mounted ? gb_disk_image.drive : 255;
    cardputer_storage_show_boot_status();

    cardputer_speaker_init();

    hdcount = cardputer_storage_hard_drive_count();
    write86(0x475, hdcount);

    running = 1;
    reset86();
    emu_hw_init();

    lasttick = 0;
    c86_link_send_hello();
}

// =====================================================================
// loop
// =====================================================================
void loop() {
    static uint32_t last_yield_us = 0;
    static uint32_t stat_ms = 0;
    static uint16_t frames = 0;
    static uint8_t last_mode = 0xFF;
    static uint8_t trace_div = 0;

    c86_link_rx_poll();
    c86_link_tx_service();

    {
        const uint8_t cmds = c86_link_take_cmds();
        if (cmds & C86_CMD_RESET) {
            tft_log("PC requested guest reset");
            emu_reset_guest();
        }
        /* C86_CMD_FULLFRAME needs nothing here: the link already armed
           want_full, so the next capture pushes every row. */
    }

    jj_ini_cpu = micros();
    exec86(2000);
    jj_end_cpu = micros();
    gb_cur_cpu_ticks = jj_end_cpu - jj_ini_cpu;
    if (gb_cur_cpu_ticks > gb_max_cpu_ticks) gb_max_cpu_ticks = gb_cur_cpu_ticks;

    /* keyboard poll */
    {
        static uint32_t kb_before = 0;
        const uint32_t now = millis();
        if (now - kb_before > gb_keyboard_poll_milis) {
            kb_before = now;
            kbd_deliver_one();
            if (gb_reset == 1) {
                gb_reset = 0;
                emu_reset_guest();
            }
        }
    }

    if (scrmodechange) doscrmodechange();

    /* video poll: draw() renders the framebuffer and calls tft_blit_scaled(),
       which hands the packed rows to the serial link */
    {
        static uint32_t vga_before = 0;
        const uint32_t now = millis();
        if (now - vga_before >= gb_vga_poll_milis) {
            vga_before = now;
            const bool gfx = !(vidmode == 0 || vidmode == 1 || vidmode == 2 ||
                               vidmode == 3 || vidmode == 7 || vidmode == 0x82);
            draw();
            frames++;
            last_mode = gfx ? 1 : 0;
        }
    }

    /* stats once a second */
    {
        const uint32_t now = millis();
        if (now - stat_ms >= 1000) {
            stat_ms = now;
            c86_link_send_stats((uint8_t)(frames > 250 ? 250 : frames),
                                (uint16_t)guest_memory_ram_kb(),
                                (uint16_t)(ESP.getFreeHeap() / 1024),
                                last_mode, 0);
            frames = 0;
            gb_max_cpu_ticks = 0;
            if (++trace_div >= 2) {          /* 每 2 秒一条客人机探针 */
                trace_div = 0;
                emu_trace();
                emu_kb_trace();            }
        }
    }

    /* feed the task watchdog: yield() alone never lets the IDLE task run */
    if (micros() - last_yield_us > 100000) {
        last_yield_us = micros();
        vTaskDelay(1);
    }
}
