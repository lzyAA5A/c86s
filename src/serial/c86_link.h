// Serial transport for C86S: video out, keystrokes in, small control frames.
#ifndef C86_LINK_H
#define C86_LINK_H

#include <stdint.h>

/* 921600 is the sweet spot for the CP2104 bridge on the 掌控板 2.0.4: it is
   exactly divisible from 80 MHz APB and moves ~92 KB/s, which is enough for a
   full 320x200 text screen every ~25 ms after RLE. */
#ifndef C86_BAUD
#define C86_BAUD 921600
#endif

void c86_link_init(void);

/* drain pending video / small frames into the UART; call every loop() */
void c86_link_tx_service(void);
/* parse inbound PC frames; call every loop() */
void c86_link_rx_poll(void);

/* Called from tft_blit_scaled(): pack + RLE the dirty rows of gb_frame_buffer
   into the TX buffer.  Returns false when a previous frame is still draining
   (frame drop -- the row signatures were not updated, so nothing is lost). */
bool c86_link_video_capture(bool graphics_mode);
/* 让下一帧起把所有行都当脏的（上电、模式切换、PC 点"重画"） */
void c86_link_request_full(void);

void c86_link_send_hello(void);
void c86_link_send_stats(uint8_t fps, uint16_t guest_kb, uint16_t heap_kb,
                         uint8_t mode, uint8_t drops);
void c86_link_log(const char *text);
void c86_link_beep(uint16_t freq_hz, uint16_t ms);

/* queued PC keystrokes: raw PS/2 bytes, one per guest keyboard poll */
bool c86_link_take_key(uint8_t *out);
void c86_link_flush_keys(void);
void c86_link_kb_stats(uint32_t *pushed, uint32_t *dropped);
/* pending commands (bit mask of cmd ids) */
uint8_t c86_link_take_cmds(void);

bool c86_link_busy(void);

/* 运行时换链路速率（PC 发 CMD_SETBAUD）：丢掉还没发完的旧帧、等 TX 排空、
   切 UART、清接收状态机，最后在新速率上补发一个 HELLO 供 PC 确认。
   非法速率（<9600 或 >3M）直接忽略。 */
void c86_link_set_baud(uint32_t baud);

#endif
