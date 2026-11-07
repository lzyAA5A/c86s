#ifndef _RENDER_H
 #define _RENDER_H

 #include "gbConfig.h"     // FAKE86_FB_W / FAKE86_FB_H
 #include "gbGlobals.h"    // gb_frame_buffer

 // ===============================================
 // Fast putpixel into the flat framebuffer
 // ===============================================
 // These used to be non-static `inline` definitions living in render.cpp only.
 // A plain inline definition is *not* a usable out-of-line symbol for other
 // translation units, and whether GCC emits an external copy is an optimisation
 // decision: under -Os it happened to emit one (so osd.cpp linked), under -O2 it
 // inlined every use and emitted none, which broke the link with
 // "undefined reference to jj_fast_putpixel(int, int, unsigned char)".
 // static inline in the header gives every TU the body and keeps the hot
 // rendering path inlined in render.cpp.
 static inline void jj_fast_putpixel(int x, int y, unsigned char c) {
     if (x < 0 || x >= FAKE86_FB_W || y < 0 || y >= FAKE86_FB_H) return;
     gb_frame_buffer[y * FAKE86_FB_W + x] = c;
 }

 static inline void jj_fast_putpixel_text(int x, int y, unsigned char c) {
     if (x < 0 || x >= FAKE86_FB_W || y < 0 || y >= FAKE86_FB_H) return;
     gb_frame_buffer[y * FAKE86_FB_W + x] = c;
 }

 unsigned char initscreen ();
 void SDLprintChar4x8(char car,int x,int y,unsigned char color,unsigned char backcolor);
 void InitPaletaCGA(void);
 void InitPaletaCGA2(void);
 void InitPaletaCGAgray(void);
 void InitPaletaPCJR(void);

#endif
