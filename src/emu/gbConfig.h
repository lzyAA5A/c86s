#ifndef _GB_CONFIG_H
 #define _GB_CONFIG_H

 #define true 1
 #define false 0

 // ============================================
 // Cardputer platform defines
 // ============================================
 #define use_lib_cardputer
 #define use_lib_tft
 #define use_lib_i2s
 #define use_lib_sdcard

 // Display dimensions
 #define CARDputer_SCREEN_W 240
 #define CARDputer_SCREEN_H 135
 #define FAKE86_FB_W 320
 #define FAKE86_FB_H 200

 //Debug i2s
 //#define use_lib_debug_i2s

 //Cuando uso capturadora usb
 //#define use_lib_capture_usb

 //Corrige teclados que no se inicializan(solucion dcrespo3D) - not needed for Cardputer
 //#define FIX_PERIBOARD_NOT_INITING

 //#define use_lib_sna_rare
 //#define use_lib_speaker_cpu

 //Section Optimice
 #define use_lib_fast_push
 #define use_lib_fast_pop
 #define use_lib_fast_op_add16
 #define use_lib_fast_op_sub16
 #define use_lib_fast_op_and8
 #define use_lib_fast_op_add8
 #define use_lib_fast_op_writew86
 #define use_lib_fast_op_xor8
 #define use_lib_fast_op_or16
 #define use_lib_fast_op_or8
 #define use_lib_fast_op_and16
 #define use_lib_fast_flag_log8
 #define use_lib_fast_flag_log16
 //#define use_lib_fast_flag_adc8
 //#define use_lib_fast_flag_adc16
 //#define use_lib_fast_flag_add8
 //#define use_lib_fast_flag_add16
 //#define use_lib_fast_flag_sbb8
 //#define use_lib_fast_flag_sbb16
 #define use_lib_fast_op_xor16
 //#define use_lib_fast_flag_sub8
 //#define use_lib_fast_flag_sub16
 #define use_lib_fast_op_sub8

 #define use_lib_fast_readw86
 #define use_lib_fast_flag_szp8
 #define use_lib_fast_flag_szp16

 #define use_lib_fast_op_adc8
 #define use_lib_fast_op_adc16
 #define use_lib_fast_op_sbb8
 #define use_lib_fast_op_sbb16

 #define use_lib_fast_modregrm
 #define use_lib_fast_readrm16
 #define use_lib_fast_readrm8
 #define use_lib_fast_writerm16
 #define use_lib_fast_writerm8
 #define use_lib_fast_op_div8


 #define use_lib_fast_decodeflagsword
 #define use_lib_fast_makeflagsword

 //#define use_lib_fast_doirq

 //#define use_lib_adlib
 //#define use_lib_disneysound
 //#define use_lib_mouse
 //#define use_lib_net

 //Usar 1 solo nucleo
 #define use_lib_singlecore

 //#define use_lib_snapshot
 //#define use_lib_limit_256KB

 #define gb_max_portram 0x3FF
 #define use_lib_limit_portram

 // Guest RAM backed by a 128 KB SRAM page cache and Flash swap.
 // 实测结论（2026-xx，dbglean 仪表）：flash 换一次 4KB 页 = wl_erase_range +
 // wl_write ≈ 48.5ms（擦除是物理下限），读回仅 0.39ms。因此「一次扫完全部
 // guest RAM」的代价 = (总页数-常驻页数) × 48.5ms：
 //   512KB → 128 页，96 次淘汰 ≈ 4.7s；Boot ROM 的 POST 内存测试就要扫两遍，
 //           实测 40 秒都走不完一遍，机器看起来像卡死（屏幕只有 POST 画面）。
 //   256KB → 64 页，32 次淘汰 ≈ 1.6s，POST 能过；而 256KB 正好是 MS-DOS 5.0
 //           的最低要求，所以默认取 256KB。
 // 现在 128/256/512 三档是运行期设置（设置菜单 More settings → Guest RAM，
 // NVS 键 ramkb），本宏只作为「上限/缺省」的编译期参考（页表按 512KB 开好）。
 #define gb_max_ram 262144

 //#define use_lib_not_use_callback_port
 #define use_lib_fast_boot

 //options debug
 //#define use_lib_debug_interrupt



 //milisegundos espera en cada frame
 #define use_lib_delay_tick_cpu_auto 0
 #define use_lib_delay_tick_cpu_milis 0
 #define use_lib_vga_poll_milis 41
 #define use_lib_keyboard_poll_milis 20
 #define use_lib_timers_poll_milis 54

 //Logs
 // NOTE: this used to be an unconditional #define, which made the "-dbg/-dbglean
 // only" contract in cardputer_kbd.cpp:201 and cardputer_display.h a lie -- the
 // shipping lean build compiled the serial screen dump as well.  On lean Serial
 // maps to UART0 (ARDUINO_USB_MODE=0) where nobody reads it, so it silently
 // burnt up to ~67 ms per screen change (775 bytes into a 256-byte TX ring at
 // 115200) and it is exactly what dragged a debug-only stack overflow into the
 // production image.  The -dbg / -dbglean environments pass
 // -Duse_lib_log_serial=1 on the compiler command line instead.
 //#define use_lib_log_serial

#endif
