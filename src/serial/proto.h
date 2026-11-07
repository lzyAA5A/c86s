// C86S serial wire protocol.  Mirrored by tools/c86host.py -- change both.
//
// Every frame on the wire:
//   [0] 0xAA  [1] 0x55  magic
//   [2] type            u8
//   [3] seq  lo  [4] hi u16 (board -> PC only, PC sets 0)
//   [5] len  lo  [6] hi u16  payload length
//   [7 .. 7+len-1]      payload
//   [7+len]             crc8 over type..payload  (crc = crc*31 + byte)
// total = 8 + len
#ifndef C86S_PROTO_H
#define C86S_PROTO_H

#define C86_MAGIC0 0xAA
#define C86_MAGIC1 0x55
#define C86_PROTO_VER 1

/* board -> PC */
#define C86_T_HELLO 0x01
#define C86_T_VIDEO 0x02
#define C86_T_STATS 0x03
#define C86_T_BEEP  0x04
#define C86_T_LOG   0x05

/* PC -> board */
#define C86_T_KEY   0x10
#define C86_T_CMD   0x11

/* VIDEO payload: [flags u8][rows u8] then per row: [row u8][rlelen u16][rle...]
   Pixel packing: 2 pixels per byte, high nibble = even x, value = pixel & 0x0F.
   The palette interpretation depends on the flags:
     text mode  -> nibble is a CGA colour index 0..15
     gfx mode   -> nibble is a 6-bit-VGA level pair: 0=black 5=1/3 A=2/3 F=white
   (that is exactly what the emulator's renderer writes into the framebuffer) */
#define C86_VF_FULL 0x01   /* the row set is a full-screen refresh */
#define C86_VF_GFX  0x02   /* emulated graphics mode (not text) */

/* RLE: control byte c
     c <  0x80 : literal run of (c+1) bytes follows
     c >= 0x80 : next byte repeated ((c & 0x7F) + 1) times                     */

/* KEY payload: [scancode u8][flags u8] -- raw PS/2 set-1 codes, make and break
   are both sent explicitly by the PC (break = code | 0x80). */
#define C86_KF_EXT 0x01    /* emit the 0xE0 prefix first (arrows, ins/del/home) */

/* CMD payload: [cmd u8][args...] */
#define C86_CMD_RESET     0x01  /* cold-reset the guest (INT 19h) */
#define C86_CMD_FULLFRAME 0x02  /* request a full-screen refresh */
#define C86_CMD_PING      0x03  /* reply with a fresh HELLO */
#define C86_CMD_SETTIME   0x04  /* 6 args: yy mm dd hh mi ss (binary) */
/* 4 args: new baud u32 little-endian. 板子切过去后在新速率上补发一个 HELLO。
   上电永远是 C86_BAUD（921600），所以 PC 侧必须先在这个速率上谈，谈完再重开端口。*/
#define C86_CMD_SETBAUD   0x05

/* HELLO payload */
#define C86_HELLO_FIXED 6   /* ver, flags, fbw u16, fbh u16 */
#define C86_HELLO_PAL   48  /* 16 * RGB888 */
#define C86_HELLO_HDR   (C86_HELLO_FIXED + C86_HELLO_PAL)

#endif
