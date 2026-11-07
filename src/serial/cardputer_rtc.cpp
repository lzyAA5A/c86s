// CMOS clock for the emulated machine.
//
// The 掌控板 has no battery-backed RTC accessible from here, so the clock is a
// software base time plus millis().  The PC client can push the host wall clock
// over the wire (C86_CMD_SETTIME) at connect time, which is the only way the
// guest ever sees a correct date.

#include <Arduino.h>
#include <string.h>
#include <stdio.h>

#include "cardputer_rtc.h"

static CardputerRtcTime base = {2024, 1, 1, 0, 0, 0};
static uint32_t base_millis = 0;
static uint8_t selected_reg = 0;

static uint8_t to_bcd(uint8_t v) {
    return (uint8_t)(((v / 10) << 4) | (v % 10));
}
static uint8_t from_bcd(uint8_t v) {
    return (uint8_t)(((v >> 4) * 10) + (v & 0x0F));
}

void cardputer_rtc_init(void) {
    base_millis = millis();
}

CardputerRtcTime cardputer_rtc_now(void) {
    CardputerRtcTime t = base;
    uint32_t secs = (millis() - base_millis) / 1000u;
    t.second = (uint8_t)(t.second + secs % 60);
    if (t.second >= 60) t.second = (uint8_t)(t.second % 60);
    uint32_t minutes = secs / 60u;
    t.minute = (uint8_t)(t.minute + minutes % 60);
    if (t.minute >= 60) t.minute = (uint8_t)(t.minute % 60);
    t.hour = (uint8_t)((t.hour + (minutes / 60u)) % 24u);
    return t;
}

bool cardputer_rtc_set(const CardputerRtcTime &time) {
    base = time;
    base_millis = millis();
    return true;
}

bool cardputer_rtc_persist_now(void) { return true; }

void cardputer_rtc_select_register(uint8_t reg) {
    selected_reg = (uint8_t)(reg & 0x7F);
}

uint8_t cardputer_rtc_read_selected(void) {
    const CardputerRtcTime t = cardputer_rtc_now();
    switch (selected_reg) {
        case 0x00: return to_bcd(t.second);
        case 0x02: return to_bcd(t.minute);
        case 0x04: return to_bcd(t.hour);
        case 0x06: return 0x01;                        /* weekday */
        case 0x07: return to_bcd(t.day);
        case 0x08: return to_bcd(t.month);
        case 0x09: return to_bcd((uint8_t)(t.year % 100));
        case 0x0A: return 0x26;                        /* status A: UIP=0 */
        case 0x0B: return 0x02;                        /* status B: 24h, BCD */
        case 0x32: return to_bcd((uint8_t)(t.year / 100));
        default:   return 0x00;
    }
}

bool cardputer_rtc_parse_yyyymmddhhmmss(const char *text, CardputerRtcTime *out) {
    if (!text || !out || strlen(text) < 14) return false;
    char f[5];
    memcpy(f, text, 4);     f[4] = 0;
    out->year = (uint16_t)atoi(f);
    out->month = (uint8_t)atoi(text + 4);
    out->day = (uint8_t)atoi(text + 6);
    out->hour = (uint8_t)atoi(text + 8);
    out->minute = (uint8_t)atoi(text + 10);
    out->second = (uint8_t)atoi(text + 12);
    return out->month >= 1 && out->month <= 12;
}

void cardputer_rtc_format(const CardputerRtcTime &time, char *buffer, size_t size) {
    if (!buffer || size == 0) return;
    snprintf(buffer, size, "%04u-%02u-%02u %02u:%02u:%02u",
             (unsigned)time.year, (unsigned)time.month, (unsigned)time.day,
             (unsigned)time.hour, (unsigned)time.minute, (unsigned)time.second);
}

uint8_t cardputer_rtc_bcd(uint8_t v) { return to_bcd(v); }
uint8_t cardputer_rtc_unbcd(uint8_t v) { return from_bcd(v); }
