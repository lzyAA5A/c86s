# NOTICE — 来源、第三方组件与不包含的内容

**English summary at the bottom.**

## 本项目

C86S —— 适合 ESP32 的 DOS 模拟器（画面走串口）。
Copyright (C) 2026 lzyAA5A。以 GNU GPL v3.0 或更高版本发布，全文见 [LICENSE](LICENSE)。

## 血统与原始署名

C86S 是下面这条链上的衍生作品，原始署名全部保留：

| 层级 | 项目 | 作者 / 权利人 | 许可 |
| --- | --- | --- | --- |
| 1 | **Fake86** —— 8086 PC 模拟器 | Mike Chambers | GPL |
| 2 | **ESP32TinyFake86** —— ESP32 移植 | Ackerman | 随上游 |
| 3 | **CardPuter86** —— M5Stack Cardputer（ESP32-S3）版本 | Copyright (C) 2026 Clearwave | GPL-3.0-or-later |
| 4 | **C86S** —— 本项目：搬到裸 ESP32，画面改走串口 | Copyright (C) 2026 lzyAA5A | GPL-3.0-or-later |

`src/emu/` 下的模拟器内核文件来自第 3 层。其中 **12 个文件被本移植修改过**
（`cpu.cpp` `cpu.h` `disk.cpp` `gbConfig.h` `guest_memory.cpp` `guest_memory.h`
`hardware.h` `ports.cpp` `render.cpp` `render.h` `timing.cpp` `video.cpp`，
主要是无 PSRAM 环境下的客机内存分页与配置裁剪），这些修改同样以 GPL-3.0-or-later 提供，
完整源码即在本仓库内。

## 第三方素材

* **Adafruit Classic 5x7 字模**（`glcdfont.c`）—— BSD 许可，
  <https://github.com/adafruit/Adafruit-GFX-Library>
* **Tom Thumb 3x5 字体** —— Robey Pointer，CC0，
  <https://opengameart.org/content/tom-thumb-tiny-ascii-font-3x5>
* `src/emu/dataFlash/bios/biospcxt.h`、`src/emu/dataFlash/rom/rombasic.h`、
  `src/emu/dataFlash/rom/videorom.h`、`src/emu/dataFlash/font/fontasciivga.h`
  等 ROM/字库数据随上游 CardPuter86 一并提供，用于模拟 PC/XT 的固件环境。

## 本仓库刻意**不包含**的内容

| 不包含 | 原因 |
| --- | --- |
| 任何 DOS 系统盘镜像（MS-DOS / PC-DOS / Compaq DOS，如 `cardputer86.img`、`dos211.img`） | 这些系统盘是各自权利人的作品，不能随开源工程再分发。请自备合法镜像，用 `flash.ps1` / `esptool` 写到 `0x600000`。 |
| 板上原厂固件的整片备份（`backup/*.bin`） | 那是厂商发布的固件，不是本项目的产物。 |
| 上游工程里未被本移植使用的内置游戏数据（`dataFlash/com/*.h`、`dataFlash/gbcom.h`） | 这些内嵌的是早期商业 DOS 游戏的可执行体，本移植的构建链不引用它们，因此既不编译也不转发。 |
| 第三方 DOS C/C++ 编译器工具链 | 有各自的许可，不属于本项目。 |

如果你要完整复刻上游 CardPuter86 的构建（含上述游戏数据），请直接使用上游仓库：
<https://github.com/Clearwavelet/CardPuter86>

---

## English summary

**C86S** is a derivative work of **CardPuter86** (Copyright (C) 2026 Clearwave, GPL-3.0-or-later),
which derives from **Fake86** by Mike Chambers and **ESP32TinyFake86** by Ackerman.
C86S itself is Copyright (C) 2026 lzyAA5A and is released under **GPL-3.0-or-later** (see `LICENSE`).

Twelve files under `src/emu/` were modified by this port (guest-memory paging and configuration
for a board without PSRAM); those modifications are provided here in full source form, as the GPL
requires.

Third-party assets: Adafruit Classic 5x7 glyphs (BSD), Tom Thumb 3x5 font by Robey Pointer (CC0),
and the ROM/font data files carried over from upstream CardPuter86.

**Deliberately not included**: any DOS system disk image (MS-DOS / PC-DOS / Compaq DOS — those
belong to their respective owners; bring your own bootable image), the stock vendor firmware
backup, the embedded commercial-game blobs from upstream that this build does not reference
(`dataFlash/com/*.h`, `dataFlash/gbcom.h`), and any third-party DOS compiler toolchain.
