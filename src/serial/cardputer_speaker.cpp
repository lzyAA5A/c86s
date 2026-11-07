// PC-speaker bridge: the 8253 channel 2 gate never becomes audio on the board
// (an ESP32 has no DAC wired to anything here), so it is forwarded to the PC
// client as a BEEP frame and played there.

#include <Arduino.h>
#include "cardputer_speaker.h"
#include "c86_link.h"

#define PIT_HZ 1193182UL

void cardputer_speaker_init(void) {}

void cardputer_speaker_write_sample(int16_t sample) { (void)sample; }

void cardputer_speaker_mute(void) {}

bool cardputer_speaker_task_running(void) { return false; }

void cardputer_speaker_set_pc_speaker(uint8_t gate, uint8_t data,
                                      uint32_t divisor) {
    (void)data;
    static uint8_t last_gate = 0;
    if (gate == last_gate) return;
    last_gate = gate;
    if (!gate) return;
    if (divisor == 0) return;
    uint32_t f = PIT_HZ / divisor;
    if (f < 40 || f > 12000) return;
    c86_link_beep((uint16_t)f, 90);
}
