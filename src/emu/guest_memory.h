#ifndef CARDPUTER_GUEST_MEMORY_H
#define CARDPUTER_GUEST_MEMORY_H

#include <Arduino.h>

bool guest_memory_init(void);
// 兼容旧接口：guest_memory_512k_enabled() 现在表示「分页模式是否开启」
// （= guest RAM 大于 128 KB）。设置菜单改用下面的 ram_kb 三档接口。
bool guest_memory_set_512k_enabled(bool enabled);
bool guest_memory_512k_enabled(void);
// 客机内存三档：128 KB（不分页，常驻）/ 256 KB（分页，DOS 5.0 默认）/ 512 KB（分页，很慢）
uint32_t guest_memory_ram_kb(void);
bool guest_memory_set_ram_kb(uint32_t kb);
// 用户选了 128 KB 但开机时凑不齐 32 页常驻 → 本次开机实际退成 256 KB 分页。
// 给 boot 提示用，避免「设置了却没生效」被静默吞掉。
bool guest_memory_ram_fell_back(void);
uint32_t guest_memory_size(void);
void guest_memory_clear(void);
uint8_t guest_memory_read(uint32_t address);
void guest_memory_write(uint32_t address, uint8_t value);

/* ---- hot path -------------------------------------------------------------
   read86()/write86() run for every guest instruction byte AND every operand, so
   they cannot afford two calls into flash plus a page-table walk that also
   bumps a 64-bit LRU stamp. These accessors keep the page touched by the last
   access in a register-sized cache: a hit costs one compare, one load, one
   store. Instruction fetch and linear scans (MS-DOS counting free clusters in
   the FAT is exactly that) hit ~100% of the time.

   gb_gm_data / gb_gm_limit mirror cache_data / guest_memory_size(). Use the
   slow guest_memory_read()/write() whenever gb_gm_data is null (init failed). */
extern uint8_t *gb_gm_data;      /* page cache base, null before init        */
extern uint32_t gb_gm_limit;     /* guest RAM size in bytes (<= gb_max_ram)  */
extern uint16_t gb_gm_hot_page;  /* page held by gb_gm_hot_base, 0xFFFF none  */
extern uint8_t *gb_gm_hot_base;  /* &cache_data[slot * PAGE_SIZE]            */
extern bool    *gb_gm_hot_dirty; /* &slot_dirty[slot]                        */

/* Flash-resident slow path: map `page` into the hot cache. */
bool guest_hot_page_map(uint16_t page);

#define GUEST_PAGE_SHIFT 12
#define GUEST_PAGE_MASK 0x0FFF

__attribute__((always_inline)) static inline uint8_t gm_hot_read(uint32_t address) {
    const uint16_t page = (uint16_t)(address >> GUEST_PAGE_SHIFT);
    if (page != gb_gm_hot_page && !guest_hot_page_map(page)) return 0;
    return gb_gm_hot_base[address & GUEST_PAGE_MASK];
}

__attribute__((always_inline)) static inline void gm_hot_write(uint32_t address,
                                                              uint8_t value) {
    const uint16_t page = (uint16_t)(address >> GUEST_PAGE_SHIFT);
    if (page != gb_gm_hot_page && !guest_hot_page_map(page)) return;
    gb_gm_hot_base[address & GUEST_PAGE_MASK] = value;
    *gb_gm_hot_dirty = true;
}
// 运行时实际分配到的页缓存页数（每页 4KB）
uint8_t guest_memory_cache_pages(void);
// 一次性诊断文本（512KB 决策链），由 boot 日志在串口可用后打印
const char *guest_memory_diag_text(void);
// CDP86 诊断：512KB 分页层往返自检（写满再读回逐字节比对）+ 换页开销统计
bool guest_memory_selftest(void);
const char *guest_memory_selftest_text(void);
void guest_memory_swap_stats(uint32_t *ops, uint32_t *us_ops, uint32_t *loads,
                             uint32_t *us_loads);

#endif
