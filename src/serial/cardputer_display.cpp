// Serial display backend: replaces the M5GFX/ST7789 sprite of the Cardputer
// build.  The renderer (render.cpp) still fills the same flat framebuffer; the
// only thing that changes is where the pixels go -- out of UART0.

#include <Arduino.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cardputer_display.h"
#include "gbConfig.h"
#include "gbGlobals.h"
#include "c86_link.h"

unsigned short int gb_palette_rgb565[16];
unsigned short int gb_palette_text_rgb565[16];

unsigned char *gb_frame_buffer = NULL;

static const unsigned char cga6[16] = {
    0x00, 0x15, 0x2A, 0x3F, 0x21, 0x19, 0x10, 0x1E,
    0x05, 0x01, 0x16, 0x15, 0x15, 0x2E, 0x25, 0x2A
};

static unsigned short int vga6_to_rgb565(unsigned char v) {
    const unsigned short r2 = (v >> 4) & 0x03;
    const unsigned short g2 = (v >> 2) & 0x03;
    const unsigned short b2 = v & 0x03;
    const unsigned short r5 = (unsigned short)((r2 << 3) | (r2 << 1) | (r2 >> 1));
    const unsigned short g6 = (unsigned short)((g2 << 4) | (g2 << 2) | g2);
    const unsigned short b5 = (unsigned short)((b2 << 3) | (b2 << 1) | (b2 >> 1));
    return (unsigned short)((r5 << 11) | (g6 << 5) | b5);
}

void init_tft_palette(void) {
    for (int i = 0; i < 16; i++) {
        gb_palette_rgb565[i] = vga6_to_rgb565(cga6[i]);
        gb_palette_text_rgb565[i] = vga6_to_rgb565(cga6[i]);
    }
}

void cardputer_display_init(void) {
    gb_frame_buffer = (unsigned char *)malloc(FAKE86_FB_W * FAKE86_FB_H);
    if (gb_frame_buffer) {
        memset(gb_frame_buffer, 0, FAKE86_FB_W * FAKE86_FB_H);
    }
    init_tft_palette();
}

void cardputer_display_clear(unsigned char color_index) {
    if (gb_frame_buffer) {
        memset(gb_frame_buffer, color_index, FAKE86_FB_W * FAKE86_FB_H);
    }
}

/* render.cpp calls this at the end of draw(): that is the only hook the
   emulator gives us, and it is exactly the point where the framebuffer is
   complete for the current frame. */
void tft_blit_scaled(bool emulated_graphics_mode) {
    c86_link_video_capture(emulated_graphics_mode);
}

void cardputer_update(void) {}
void cardputer_display_update_mode_button(void) {}
bool cardputer_display_navigation_active(void) { return false; }
void cardputer_serial_screen_dump(void) {}

void tft_log(const char *msg) { c86_link_log(msg); }

void tft_log_num(const char *msg, unsigned long num) {
    char buf[64];
    snprintf(buf, sizeof(buf), "%s%lu", msg ? msg : "", num);
    c86_link_log(buf);
}
