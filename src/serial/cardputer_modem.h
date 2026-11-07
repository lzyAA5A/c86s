#ifndef CARDPUTER_MODEM_H
#define CARDPUTER_MODEM_H

#include <Arduino.h>

#ifdef CARDPUTER86_NO_MODEM
// ---------------------------------------------------------------------------
// 精简构建（env:cardputer86-lean）
//
// Cardputer 无 PSRAM，512KB guest 内存只能靠 flash 换页，页缓存大小直接决定
// 缺页频率。腾 SRAM 的唯一办法是把用不到的子系统从链接里摘掉。
// 这一句 #include <WiFi.h> 的代价实测是：静态 25KB（g_cnxMgr/ftm_initiator/
// dns_table/s_wifi_nvs/gWpaSm/g_ic/sockets 等）+ 运行时 lwIP/WiFi 堆 20-40KB
// + Flash 390KB。Cardputer 跑 DOS 不需要联网。
//
// 代价：模拟串口/WiFi modem 不可用（DOS 的 COM1 仍在，但没有真实承载）。
// ---------------------------------------------------------------------------
static inline void cardputer_modem_init(void) {}
static inline void cardputer_modem_poll(void) {}
static inline bool cardputer_modem_save_wifi(const char *, const char *) { return false; }
static inline bool cardputer_modem_wifi_configured(void) { return false; }
static inline bool cardputer_modem_wifi_connected(void) { return false; }
static inline const char *cardputer_modem_wifi_ssid(void) { return ""; }
static inline void cardputer_modem_pause_wifi(void) {}
static inline void cardputer_modem_resume_wifi(void) {}
static inline bool cardputer_modem_port(uint16_t) { return false; }
static inline void cardputer_modem_write(uint16_t, uint8_t) {}
static inline uint8_t cardputer_modem_read(uint16_t) { return 0xFF; }
#else
void cardputer_modem_init(void);
void cardputer_modem_poll(void);
bool cardputer_modem_save_wifi(const char *ssid, const char *pass);
bool cardputer_modem_wifi_configured(void);
bool cardputer_modem_wifi_connected(void);
const char *cardputer_modem_wifi_ssid(void);
void cardputer_modem_pause_wifi(void);
void cardputer_modem_resume_wifi(void);
bool cardputer_modem_port(uint16_t port);
void cardputer_modem_write(uint16_t port, uint8_t value);
uint8_t cardputer_modem_read(uint16_t port);
#ifdef use_lib_log_serial
// 仅调试固件：强制起射频（不配 SSID 也能 AT+WIFISCAN），用于远程验收
bool cardputer_modem_debug_start_radio(void);
// 串口侧验收通道（0x01 'G' / 0x01 'H'）：不经过 guest 键盘就能看网络状态、
// 跑一次完整的 HTTP GET。guest 侧 WTERM 路径依赖串口->PS/2 注入，是另一回事。
void cardputer_modem_debug_net_report(void);
void cardputer_modem_debug_http_get(const char *host, const char *path);
#endif
#endif

#endif
