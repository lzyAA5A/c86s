#ifndef _CARDputer_DISPLAY_H
#define _CARDputer_DISPLAY_H

#include "gbConfig.h"

/* RGB565 palettes kept for source compatibility with the Cardputer build;
   the serial transport ships RGB888 in the HELLO frame instead. */
extern unsigned short int gb_palette_rgb565[16];
extern unsigned short int gb_palette_text_rgb565[16];

/* Flat 320x200 framebuffer, one byte per pixel (CGA colour index 0-15, or a
   6-bit VGA level for graphics modes -- see proto.h) */
extern unsigned char *gb_frame_buffer;

void cardputer_display_init(void);
void tft_blit_scaled(bool emulated_graphics_mode);
void init_tft_palette(void);
void cardputer_display_clear(unsigned char color_index);
void cardputer_update(void);
void cardputer_display_update_mode_button(void);
bool cardputer_display_navigation_active(void);
void tft_log(const char *msg);
void tft_log_num(const char *msg, unsigned long num);
void cardputer_serial_screen_dump(void);

#endif
