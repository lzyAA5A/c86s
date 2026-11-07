// Raw-flash disk backend.
//
// The Cardputer build mounted a FAT filesystem (esp_vfs_fat + wear levelling)
// and then read image files out of it -- ~96 KB of code, plus a hard dependency
// on getting a >10 KB contiguous heap block before anything else in setup().
// Here the guest disk *is* a flash partition: no filesystem, no VFS, no heap
// pressure.  Writes land in a 64-sector RAM cache (LRU-ish); the image region
// itself is never erased, so a crashed guest cannot brick the boot medium.

#include <Arduino.h>
#include <string.h>
#include <esp_partition.h>

#include "cardputer_storage.h"
#include "cardputer_display.h"

CardputerDiskImage gb_disk_image;
CardputerDiskImage gb_disk_drives[CARDPUTER_DRIVE_COUNT];

static const esp_partition_t *disk_part = nullptr;
static uint32_t image_size = 0;
static uint16_t geom_cyl = 0;
static uint8_t geom_heads = 0;
static uint8_t geom_sects = 0;

#define WC_N 64
struct WcEnt {
    bool used;
    uint32_t lba;
    uint8_t data[512];
};
static WcEnt wc[WC_N];
static uint8_t wc_next = 0;

/* IO 探针：客人机到底有没有在向这张盘要扇区、要的是哪一个、失败没有。
   「BIOS 说没系统盘」时这是唯一能分辨「没读」和「读了但读到的东西不对」的量。 */
static uint32_t io_reads = 0, io_writes = 0, io_fails = 0, io_last_lba = 0;
static uint32_t geo_calls = 0, geo_fail_unmounted = 0, geo_fail_drive = 0;

void c86_storage_io_stats(uint32_t *reads, uint32_t *writes, uint32_t *fails,
                          uint32_t *last_lba) {
    if (reads) *reads = io_reads;
    if (writes) *writes = io_writes;
    if (fails) *fails = io_fails;
    if (last_lba) *last_lba = io_last_lba;
}

void c86_storage_geo_stats(uint32_t *calls, uint32_t *unmounted,
                           uint32_t *wrong_drive, uint8_t *mounted_flag,
                           uint8_t *image_drive) {
    if (calls) *calls = geo_calls;
    if (unmounted) *unmounted = geo_fail_unmounted;
    if (wrong_drive) *wrong_drive = geo_fail_drive;
    if (mounted_flag) *mounted_flag = gb_disk_image.mounted ? 1 : 0;
    if (image_drive) *image_drive = gb_disk_image.drive;
}

static bool part_read(uint32_t lba, uint8_t *buf) {
    if (!disk_part) return false;
    if ((uint64_t)lba * 512u + 512u > image_size) return false;
    return esp_partition_read(disk_part, (size_t)lba * 512u, buf, 512) == ESP_OK;
}

static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

bool cardputer_storage_init_and_select(void) {
    memset(&gb_disk_image, 0, sizeof(gb_disk_image));
    memset(gb_disk_drives, 0, sizeof(gb_disk_drives));
    for (int i = 0; i < WC_N; i++) wc[i].used = false;

    disk_part = esp_partition_find_first((esp_partition_type_t)ESP_PARTITION_TYPE_DATA,
                                        (esp_partition_subtype_t)0x99, "disk");
    if (!disk_part) {
        tft_log("disk: no 'disk' partition");
        return false;
    }

    uint8_t bs[512];
    if (esp_partition_read(disk_part, 0, bs, 512) != ESP_OK) {
        tft_log("disk: read failed");
        return false;
    }
    if (bs[0] != 0xEB && bs[0] != 0xE9) {
        tft_log("disk: no boot signature (empty image?)");
        return false;
    }

    const uint16_t bps = le16(bs + 11);
    const uint16_t spt = le16(bs + 24);
    const uint16_t heads = le16(bs + 26);
    uint32_t total = le16(bs + 19);
    if (total == 0) total = le32(bs + 32);

    if (bps != 512 || spt == 0 || heads == 0 || total == 0) {
        tft_log("disk: BPB unusable");
        return false;
    }

    image_size = total * 512u;
    if (image_size > disk_part->size) image_size = disk_part->size;

    const bool hard = image_size > 2949120u;      /* anything bigger than 2.88 MB */
    if (hard) {
        geom_sects = 63;
        geom_heads = 16;
        geom_cyl = (uint16_t)(image_size / (63u * 16u * 512u));
        gb_disk_image.drive = 0x80;
    } else {
        geom_sects = (uint8_t)spt;
        geom_heads = (uint8_t)heads;
        geom_cyl = (uint16_t)(total / ((uint32_t)spt * heads));
        gb_disk_image.drive = 0x00;
    }

    gb_disk_image.mounted = true;
    gb_disk_image.storage = CARDPUTER_STORAGE_FLASH;
    gb_disk_image.size = image_size;
    gb_disk_image.cylinders = geom_cyl;
    gb_disk_image.sectors = geom_sects;
    gb_disk_image.heads = geom_heads;
    strncpy(gb_disk_image.path, "flash:disk", CARDPUTER_IMAGE_PATH_LEN - 1);

    const int slot = (gb_disk_image.drive == 0x00) ? 0
                   : (gb_disk_image.drive == 0x80) ? 2 : 0;
    gb_disk_drives[slot] = gb_disk_image;
    return true;
}

void cardputer_storage_show_boot_status(void) {
    char buf[112];
    if (!gb_disk_image.mounted) {
        tft_log("boot: no disk -> ROM BASIC");
        return;
    }
    snprintf(buf, sizeof(buf), "boot: %s drv%02X %lux%ux%u %luKB",
             gb_disk_image.path, gb_disk_image.drive,
             (unsigned long)geom_cyl, (unsigned)geom_heads,
             (unsigned)geom_sects, (unsigned long)(image_size / 1024));
    tft_log(buf);
    /* 把引导扇区头几个字节打出来：挂上了但读到的不是那张盘时，一眼就能看出来。
       注意 part_read 固定读 512 字节，缓冲不能只给 8 个 —— 只给 16 字节时
       栈会被写穿，芯片表现为 setup() 里 SW_CPU_RESET 崩溃循环。 */
    static uint8_t bs[512];
    if (part_read(0, bs)) {
        snprintf(buf, sizeof(buf), "sector0: %02X %02X %02X %02X %02X %02X %02X %02X",
                 bs[0], bs[1], bs[2], bs[3], bs[4], bs[5], bs[6], bs[7]);
        tft_log(buf);
    } else {
        tft_log("sector0: read FAILED");
    }
}

bool cardputer_storage_read_sector(uint8_t drive, unsigned long lba,
                                   unsigned char *buffer) {
    io_reads++;
    io_last_lba = (uint32_t)lba;
    if (!gb_disk_image.mounted || buffer == nullptr) { io_fails++; return false; }
    if (drive != gb_disk_image.drive) { io_fails++; return false; }
    for (int i = 0; i < WC_N; i++) {
        if (wc[i].used && wc[i].lba == (uint32_t)lba) {
            memcpy(buffer, wc[i].data, 512);
            return true;
        }
    }
    const bool ok = part_read((uint32_t)lba, buffer);
    if (!ok) io_fails++;
    return ok;
}

bool cardputer_storage_read_sector(unsigned long lba, unsigned char *buffer) {
    return cardputer_storage_read_sector(gb_disk_image.drive, lba, buffer);
}

bool cardputer_storage_write_sector(uint8_t drive, unsigned long lba,
                                    const unsigned char *buffer) {
    io_writes++;
    if (!gb_disk_image.mounted || buffer == nullptr) return false;
    if (drive != gb_disk_image.drive) return false;
    if ((uint64_t)lba * 512u + 512u > image_size) return false;
    WcEnt &e = wc[wc_next];
    wc_next = (uint8_t)((wc_next + 1) % WC_N);
    e.used = true;
    e.lba = (uint32_t)lba;
    memcpy(e.data, buffer, 512);
    return true;
}

bool cardputer_storage_write_sector(unsigned long lba, const unsigned char *buffer) {
    return cardputer_storage_write_sector(gb_disk_image.drive, lba, buffer);
}

bool cardputer_storage_drive_geometry(uint8_t drive, uint16_t *cylinders,
                                      uint8_t *heads, uint8_t *sectors) {
    geo_calls++;
    if (!gb_disk_image.mounted) { geo_fail_unmounted++; return false; }
    if (drive != gb_disk_image.drive) { geo_fail_drive++; return false; }
    if (cylinders) *cylinders = geom_cyl;
    if (heads) *heads = geom_heads;
    if (sectors) *sectors = geom_sects;
    return true;
}

uint8_t cardputer_storage_hard_drive_count(void) {
    return (gb_disk_image.mounted && gb_disk_image.drive >= 0x80) ? 1 : 0;
}

/* The Cardputer build could mirror the boot image into a second drive slot
   (B: on the SD card).  There is no second medium here, so the answer is
   always "no" -- cpu.cpp asks this to decide whether to advertise B: to the
   guest. */
bool cardputer_storage_second_drive_present(void) { return false; }
