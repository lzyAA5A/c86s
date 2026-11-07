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
// timing.c: critical functions to provide accurate timing for the
//   system timer interrupt, and to generate new audio output samples.

#include "gbConfig.h"
#include "config.h"
#include "gbGlobals.h"
#include "timing.h"
//#include <SDL/SDL.h>
#include <stdint.h>
#include <stdio.h>
#include <Arduino.h>
//#ifdef _WIN32
// #include <Windows.h>
// LARGE_INTEGER queryperf;
//#else
// #include <sys/time.h>
// struct timeval tv;
//#endif
#include "i8253.h"
#include "i8259.h"
#include "cpu.h"
//JJ #include "blaster.h"

//JJ extern struct blaster_s blaster;
extern struct i8253_s i8253;
//extern void doirq (uint8_t irqnum);
//JJ extern void tickaudio(); //no audio
#ifdef use_lib_disneysound
 //JJ extern void tickssource();
#endif 
#ifdef use_lib_adlib
 //JJ extern void tickadlib();
#endif 
//JJ extern void tickBlaster(); //no audio

unsigned long hostfreq = 1000000;
//lasttick = 0, curtick = 0, scanlinetiming, lastscanlinetick, curscanline = 0;
//lasti8253tick,i8253tickgap, 
//uint64_t lastsampletick, gensamplerate;
//ssourceticks, sampleticks, lastssourcetick, adlibticks, lastadlibtick, lastblastertick
//unsigned long int gb_jj_cont_timer=0;

unsigned int jj_cur_ms_tick, jj_last_ms_tick;
//,jj_lastscanline_ms_tick,jj_lasti8253_ms_tick;

//uint16_t pit0counter = 65535; //No lo necesito
extern uint64_t totalexec;
extern uint32_t speed;
extern uint8_t slowsystem;
//port3da
//doaudio, 

void inittiming() {
//#ifdef _WIN32
	//QueryPerformanceFrequency (&queryperf);
	//hostfreq = queryperf.QuadPart;
	//QueryPerformanceCounter (&queryperf);
	//curtick = queryperf.QuadPart;	
//#else
	//hostfreq = 1000000;
	//gettimeofday (&tv, NULL);
	//curtick = (uint64_t) tv.tv_sec * (uint64_t) 1000000 + (uint64_t) tv.tv_usec;
//#endif


 //JJ curtick = gb_jj_cont_timer;    
   //curtick = micros(); //No lo necesito
   //lasttick= curtick; //No lo necesito


	//lasti8253tick = 
  //lastscanlinetick = lasttick = curtick; //No lo necesito
	//lastsampletick = lastblastertick = lastadlibtick = lastssourcetick
	//scanlinetiming = hostfreq / 31500; // No lo necesito
	//ssourceticks = hostfreq / 8000;
	//adlibticks = hostfreq / 48000;
	//JJif (doaudio) sampleticks = hostfreq / gensamplerate;
//JJ	else sampleticks = -1;
    //sampleticks = 0; // Sin audio
	// i8253tickgap = hostfreq / 119318;	 //Ya no lo necesito Fuerzo 54 ms

  jj_last_ms_tick= jj_cur_ms_tick= millis();
  //jj_lastscanline_ms_tick= jj_lasti8253_ms_tick
}


unsigned int localscanline=0;


void timing()
{//Funcion Alleycat y Digger 
 unsigned char i8253chan;
 jj_cur_ms_tick= millis();
 // Elapsed milliseconds since the last *poll*, as an unsigned subtraction:
 // last-first would underflow to ~4.29e9 and make every test below "always
 // true" (the interrupt then fired once per millisecond = 1000 Hz instead of
 // the intended 18.2 Hz). See the N7 notes: that raced the guest's BIOS tick
 // counter and, together with the old RTC clobber in bootstrapPoll(), made
 // *((unsigned long far *)0x400006C) useless for guest programs.
 unsigned int auxCurTick= (jj_cur_ms_tick - jj_last_ms_tick);
 {
  localscanline++;
  if (localscanline > 479) port3da = 8;			
  else port3da = 0;
  if (localscanline & 1) port3da |= 1;
  if (localscanline>525)
   localscanline=0;
 }

 // PIT channel counters keep their historical cadence (one decrement step per
 // millisecond) so programs that calibrate by reading ports 0x40..0x42 behave
 // exactly as before. Only the timer *interrupt* below moves to 18.2 Hz.
 if (auxCurTick >= 1)
 {
  for (i8253chan=0; i8253chan<3; i8253chan++)
  {
   if (i8253.active[i8253chan]) 
   {
    if (i8253.counter[i8253chan] < 10)
     i8253.counter[i8253chan] = i8253.chandata[i8253chan];
    i8253.counter[i8253chan] -= 10;
   }
  }   
 }

 // 54 ms poll: BIOS timer interrupt (18.2 Hz, what INT 08 / the BDA tick
 // counter expect) plus the bootstrap watchdog. This used to run at 1 kHz.
 if (auxCurTick >= gb_timers_poll_milis)
 {
  jj_last_ms_tick = jj_cur_ms_tick;
  bootstrapPoll();  //Cada 54 milis
  if (i8253.active[0])
  {
   doirq(0);
  }
 }

}


