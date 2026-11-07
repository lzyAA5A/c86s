//
//  Fake86: A portable, open-source 8086 PC emulator.
//  Copyright (C)2010-2012 Mike Chambers
//
//  This program is free software; you can redistribute it and/or
//  modify it under the terms of the GNU General Public License
//  as published by the Free Software Foundation; either version 2
//  of the License, or (at your option) any later version.
//
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
//  GNU General Public License for more details.
//
//  You should have received a copy of the GNU General Public License
//  along with this program; if not, write to the Free Software
//  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#ifndef _CPU_H
 #define _CPU_H

 #include <esp_attr.h>   // IRAM_ATTR (N2-②)
#ifdef _WIN32
#include <windows.h>
#else
#include <time.h>
#endif

#define regax 0
#define regcx 1
#define regdx 2
#define regbx 3
#define regsp 4
#define regbp 5
#define regsi 6
#define regdi 7
#define reges 0
#define regcs 1
#define regss 2
#define regds 3

//JJ #ifdef __BIG_ENDIAN__
//JJ #define regal 1
//JJ #define regah 0
//JJ #define regcl 3
//JJ #define regch 2
//JJ #define regdl 5
//JJ #define regdh 4
//JJ #define regbl 7
//JJ #define regbh 6
//JJ #else
#define regal 0
#define regah 1
#define regcl 2
#define regch 3
#define regdl 4
#define regdh 5
#define regbl 6
#define regbh 7
//JJ #endif

union _bytewordregs_ {
	uint16_t wordregs[8];
	uint8_t byteregs[8];
};

#define StepIP(x)	ip += x
#define getmem8(x, y)	read86(segbase(x) + y)
#define getmem16(x, y)	readw86(segbase(x) + y)
#define putmem8(x, y, z)	write86(segbase(x) + y, z)
#define putmem16(x, y, z)	writew86(segbase(x) + y, z)
#define signext(value)	(int16_t)(int8_t)(value)
#define signext32(value)	(int32_t)(int16_t)(value)
#define getreg16(regid)	regs.wordregs[regid]
#define getreg8(regid)	regs.byteregs[byteregtable[regid]]
#define putreg16(regid, writeval)	regs.wordregs[regid] = writeval
#define putreg8(regid, writeval)	regs.byteregs[byteregtable[regid]] = writeval
#define getsegreg(regid)	segregs[regid]
#define putsegreg(regid, writeval)	segregs[regid] = writeval
#define segbase(x)	((uint32_t) x << 4)


unsigned char GetRegAL(void);
unsigned char GetRegAH(void);
unsigned char GetRegBL(void);
unsigned char GetRegBH(void);
unsigned char GetRegCL(void);
unsigned char GetRegCH(void);
unsigned char GetRegDL(void);
unsigned char GetRegDH(void);

unsigned short int GetRegCS(void);
unsigned short int GetRegIP(void);
unsigned short int GetRegDS(void);
unsigned short int GetRegSS(void);
unsigned short int GetRegES(void);

unsigned short int GetRegSP(void);
unsigned short int GetRegBP(void);
unsigned short int GetRegSI(void);
unsigned short int GetRegDI(void);

unsigned short int GetCF(void);

void intcall86 (uint8_t intnum);



void SetRegAL(unsigned char a);
void SetRegAH(unsigned char a);
void SetRegBL(unsigned char a);
void SetRegBH(unsigned char a);
void SetRegCL(unsigned char a);
void SetRegCH(unsigned char a);
void SetRegDL(unsigned char a);
void SetRegDH(unsigned char a);

void SetRegCS(unsigned short int a);
void SetRegIP(unsigned short int a);
void SetRegDS(unsigned short int a);
void SetRegSS(unsigned short int a);
void SetRegES(unsigned short int a);

void SetRegSP(unsigned short int a);
void SetRegBP(unsigned short int a);
void SetRegSI(unsigned short int a);
void SetRegDI(unsigned short int a);

void SetCF(unsigned short int a);

// N2-②: the interpreter dispatch loop and the two memory accessors are the
// hottest code in the firmware — every guest instruction goes through all
// three. Pulling them into IRAM removes the flash instruction-cache miss from
// the inner loop on ESP32-S3.
unsigned char IRAM_ATTR read86 (unsigned int addr32);
void IRAM_ATTR write86 (unsigned int addr32, unsigned char value);
void reset86(void);
void IRAM_ATTR exec86 (uint32_t execloops);
void FuerzoParityRAM(void);

void bootstrapPoll(void);
void SetRAMTruco(void);
void ExternalSetCF(unsigned char valor);

void my_callback_speaker_func(void);

#ifdef use_lib_sna_rare
 void jj_write86_remap(unsigned int addr32, unsigned char value);
 unsigned char jj_read86_remap(unsigned int addr32);
#endif 

#ifndef use_lib_fast_push
 void push (unsigned short int pushval);
#endif

#ifndef use_lib_fast_pop
 unsigned short int pop(void);
#endif

#ifndef use_lib_fast_op_add16
 void op_add16(void);
#endif

#ifndef use_lib_fast_op_sub16
 void op_sub16(void);
#endif

#ifndef use_lib_fast_op_and8
 void op_and8(void);
#endif

#ifndef use_lib_fast_op_add8
 void op_add8(void);
#endif

#ifndef use_lib_fast_op_writew86
 void writew86 (unsigned int addr32, unsigned short int value);
#endif

#ifndef use_lib_fast_op_xor8
 void op_xor8(void);
#endif

#ifndef use_lib_fast_op_or16
 void op_or16(void);
#endif

#ifndef use_lib_fast_op_or8
 void op_or8(void);
#endif

#ifndef use_lib_fast_op_and16
 void op_and16(void);
#endif

#ifndef use_lib_fast_flag_log8
 void flag_log8 (unsigned char value);
#endif

#ifndef use_lib_fast_flag_log16
 void flag_log16 (unsigned short int value);
#endif

#ifndef use_lib_fast_flag_adc8
 void flag_adc8 (unsigned char v1, unsigned char v2, unsigned char v3);
#endif 

#ifndef use_lib_fast_flag_adc16
 void flag_adc16 (uint16_t v1, uint16_t v2, uint16_t v3);
#endif

#ifndef use_lib_fast_op_xor16
 void op_xor16(void);
#endif

#ifndef use_lib_fast_readw86
 unsigned short int readw86 (unsigned int addr32);
#endif

#ifndef use_lib_fast_flag_szp8
 void flag_szp8(unsigned char value);
#endif

#ifndef use_lib_fast_flag_szp16
 void flag_szp16(unsigned short int value);
#endif

#ifndef use_lib_fast_op_adc8
 void op_adc8(void);
#endif

#ifndef use_lib_fast_op_adc16
 void op_adc16(void);
#endif

#ifndef use_lib_fast_op_sbb8
 void op_sbb8(void)
#endif

#ifndef use_lib_fast_op_sbb16
 void op_sbb16(void);
#endif 

#ifndef use_lib_fast_modregrm
 void modregrm(void);
#endif

#ifndef use_lib_fast_readrm16
 unsigned short int readrm16 (unsigned char rmval);
#endif

#ifndef use_lib_fast_readrm8
 unsigned char readrm8 (unsigned char rmval);
#endif

#ifndef use_lib_fast_writerm16
 void writerm16 (unsigned char rmval, unsigned short int value);
#endif

#ifndef use_lib_fast_writerm8
 void writerm8 (unsigned char rmval, unsigned char value);
#endif 

#ifndef use_lib_fast_op_div8
 void op_div8 (unsigned short int valdiv, unsigned char divisor);
#endif 

// ---------------------------------------------------------------------------
// CDP86 diagnostics: INT breadcrumb trail.
// Records the last CDP_TRAIL_LEN software/hardware interrupts (int number +
// CS:IP of the caller) so a hang or a guest reboot can be explained without a
// single-stepping debugger. Filled in intcall86(); on INT 19h (guest reboot)
// the ring is copied to the g_cdp_snap_* arrays and g_cdp_snap_valid is set,
// so the trail that caused the reboot survives the BIOS POST that follows
// (POST itself issues dozens of INT 10h and would otherwise flush the ring).
// Read from the sketch's 1-second debug block.
#define CDP_TRAIL_LEN 20
extern volatile uint16_t g_cdp_trail_cs[CDP_TRAIL_LEN];
extern volatile uint16_t g_cdp_trail_ip[CDP_TRAIL_LEN];
extern volatile uint8_t  g_cdp_trail_num[CDP_TRAIL_LEN];
extern volatile uint8_t  g_cdp_trail_head;
extern volatile unsigned long g_cdp_int19_count;
extern volatile uint8_t  g_cdp_snap_valid;
extern volatile uint16_t g_cdp_snap_cs[CDP_TRAIL_LEN];
extern volatile uint16_t g_cdp_snap_ip[CDP_TRAIL_LEN];
extern volatile uint8_t  g_cdp_snap_num[CDP_TRAIL_LEN];

// CDP86 diagnostics: fine-grained execution breadcrumb.
// intcall86() never sees the guest's INTs on this machine (the booted F000 ROM
// owns the IVT), so the coarse trail above stays empty. This ring samples CS:IP
// every 16 emulated instructions and freezes the moment control reaches the
// reset vector (F000:FFF0) or ROM BASIC (F600:0000) — i.e. exactly when the
// machine reboots itself — keeping the previous ~768 instructions.
#define CDP_RING_LEN 48
extern volatile uint16_t g_cdp_ring_cs[CDP_RING_LEN];
extern volatile uint16_t g_cdp_ring_ip[CDP_RING_LEN];
extern volatile uint8_t  g_cdp_ring_head;
extern volatile uint8_t  g_cdp_ring_frozen;
extern volatile uint16_t g_cdp_ring_snap_cs[CDP_RING_LEN];
extern volatile uint16_t g_cdp_ring_snap_ip[CDP_RING_LEN];
extern volatile uint16_t g_cdp_frz_cs, g_cdp_frz_ip, g_cdp_frz_ss, g_cdp_frz_sp;

// CDP86 diagnostics: which interrupts actually flow through intcall86().
// Needed because this machine boots a real F000 ROM: some services run wholly
// inside ROM and never reach the emulator, so "INT 13h was issued" and
// "diskhandler() ran" are NOT the same statement. A histogram settles it.
extern unsigned long g_cdp_int_hist[256];
// 重启快照计数器与 16 条指令为单位的进度计数（见 cpu.cpp 的 exec86）
extern volatile unsigned long g_cdp_instrs;
extern volatile unsigned long g_cdp_reset_count;

// CDP86 diagnostics: INT 13h AH=02h read statistics (disk.cpp).
// diskhandler() unconditionally reports success (cf=0, ah=0) after readdisk(),
// so a failing/short read is invisible from the guest's side — these counters
// are the only way to see it.
extern unsigned long g_cdp_disk_reqs;
extern unsigned long g_cdp_disk_bootsec;
extern unsigned long g_cdp_disk_noimg;
extern volatile uint16_t g_cdp_disk_last_cyl, g_cdp_disk_last_sect;
extern volatile uint16_t g_cdp_disk_last_head, g_cdp_disk_last_cnt;

#endif

