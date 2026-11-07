#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""c86mgr.py -- C86S 管理器（一键启动模拟器 + 虚拟软盘管理）

为什么单独做一个：c86host.py 只管"跑起来"，改软盘要 esptool 读写板子的 disk
分区，两件事都要独占 COM3，散在命令行里很容易互相打架（模拟器开着 → 刷盘失败）。
这里把它们收在一个窗口里，操作前自动腾出串口。

功能：
  1. 一键启动 / 停止模拟器窗口（c86host.py），串口、缩放、链路码率可调。
  2. 一键烧录：bootloader(0x1000) + 分区表(0x8000) + 固件(0x10000)，可选
     同时写软盘镜像(0x600000)、可选先整片备份；写前自动擦 swap 分区，
     写完硬复位。等价于 flash.ps1，但不用记命令行。
  3. 写回备份：把 backup\gankong204-full.bin 整片写回（恢复原厂 MINI-OS）。
  4. 三个码率：链路（模拟器画面）、烧录/上传、读取，都是 esptool/UART 的实际参数。
  5. 下载：把板子 0x600000 处的 1.44MB 软盘镜像读到本地 .img
     （分段读 + 重试：整片一次读在 921600 下会被 serial 流打断）。
  6. 上传：把本地 .img 写回板子（写前先关掉模拟器，写完板子自动复位）。
  7. 修改：在本地镜像里 列出 / 导出 / 导入 / 删除 文件（FAT12 根目录）。
  8. 「未上传」标记：改过镜像没写回板子时标题带 *，避免改完忘了刷。
  9. 串口 / 缩放 / 三个码率 会记在 mgr.cfg 里，下次打开还是这套。

零依赖：只用标准库 + tkinter（和 c86host.py 一致，本机 pip 装不了东西）。

命令行也能用（不带参数就是开窗口）：
    python c86mgr.py                          # 打开管理器
    python c86mgr.py flash COM3               # 一键烧录（固件+软盘镜像）
    python c86mgr.py flash COM3 --no-image    # 只烧固件
    python c86mgr.py flash COM3 --baud 460800 --backup   # 换码率 + 先备份
    python c86mgr.py restore COM3             # 把备份整片写回
    python c86mgr.py dl  COM3 out.img         # 只下载
    python c86mgr.py up  COM3 in.img          # 只上传
    python c86mgr.py ls  in.img               # 只列文件
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                    # repo root
sys.path.insert(0, HERE)

import fat12                                    # noqa: E402

# 板子上 disk 分区：partitions_c86s.csv 里是 disk 0x600000/0x200000，
# 但里面实际是一张 1.44MB 软盘镜像，多出来的尾部不参与 FAT12。
DISK_OFF = 0x600000
IMG_BYTES = 1474560                             # 0x168000 = 1.44MB
CHUNK = 0x80000                                 # 512KB：整片读会 digest mismatch
BAUD_READ = 230400                              # 读用低速更稳
BAUD_WRITE = 921600

# 烧录布局（和 flash.ps1 一致；ESP32 的 bootloader 在 0x1000，不是 S3 的 0x0）
BOOT_OFF, PART_OFF, APP_OFF = 0x1000, 0x8000, 0x10000
SWAP_OFF, SWAP_BYTES = 0x310000, 0x90000        # 旧固件的 littlefs，刷前擦掉
FLASH_BYTES = 0x800000                          # 8MB
FW_DIR = os.path.join(ROOT, ".pio", "build", "c86s")
BACKUP_PATH = os.path.join(ROOT, "backup", "gankong204-full.bin")
CFG_PATH = os.path.join(HERE, "mgr.cfg")

# 可选码率：921600 是这块板 CP2104 的甜点；低速率给不稳的线材兜底
BAUD_CHOICES = ("921600", "460800", "230400", "115200")
DEF_LINK_BAUD = "921600"                        # 模拟器链路（固件支持运行时切换）
DEF_UP_BAUD = "921600"                          # 烧录 / 上传
DEF_RD_BAUD = "230400"                          # 读取（高速读容易 digest mismatch）

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def load_cfg():
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:                            # noqa: BLE001
        return {}


def save_cfg(data):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    except Exception:                            # noqa: BLE001
        pass


# ---------------------------------------------------------------- esptool

def find_esptool():
    """按顺序找 esptool：环境变量 → 已知安装点 → PATH。"""
    env = os.environ.get("C86S_ESPTOOL")
    if env and os.path.exists(env):
        return env
    cands = [
        os.path.join(os.environ.get("PLATFORMIO_CORE_DIR", ""),
                     "packages", "tool-esptoolpy", "esptool.py"),
        os.path.join(HERE, "esptool.exe"),
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    for name in ("esptool.exe", "esptool"):
        p = shutil.which(name)
        if p:
            return p
    return None


def find_c86host():
    return os.path.join(HERE, "c86host.py")


def start_emulator(port, scale=3, log=print, baud=None):
    """起一个模拟器窗口，返回 Popen。

    抽成模块级函数（不嵌在 GUI 里）是为了能脱离窗口自测：Tk 的 ttk 控件
    不接受 PostMessage 模拟鼠标点击（实测点了没反应），按钮链路只能这样验。
    """
    args = [sys.executable, find_c86host(),
            "--port", str(port), "--scale", str(scale)]
    if baud:
        args += ["--baud", str(baud)]
    p = subprocess.Popen(args, cwd=ROOT, creationflags=CREATE_NO_WINDOW)
    log("模拟器已启动 pid=%d（c86host.py @ %s%s，缩放 %s）"
        % (p.pid, port, " @ %s bps" % baud if baud else "", scale))
    return p


def watch_emulator(p, log=print, delay=3.0):
    """几秒后看一眼：进程已经退了就把退出码报出来（串口被占是最常见原因）。"""
    def run():
        time.sleep(delay)
        rc = p.poll()
        if rc is not None:
            log("!! 模拟器起来后立刻退了（退出码 %d）——多半是该串口被别的程序占着"
                % rc)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------- 板子操作
# 这些函数不碰 Tk，方便命令行复用，也方便无窗口自测。

class Board:
    def __init__(self, log=print):
        self.log = log
        self.esptool = find_esptool()

    def need_esptool(self):
        if not self.esptool:
            raise RuntimeError(
                "找不到 esptool。请设置环境变量 C86S_ESPTOOL 指向 esptool.exe，"
                "或 pip 安装 esptool。")
        return self.esptool

    def _run(self, args, what):
        exe = self.need_esptool()
        cmd = [exe] + args
        self.log("$ " + " ".join(cmd))
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW)
        tail = []
        for line in p.stdout:
            line = line.rstrip()
            if line:
                self.log("  " + line)
                tail.append(line)
                if len(tail) > 40:
                    tail.pop(0)
        rc = p.wait()
        if rc != 0:
            raise RuntimeError("%s 失败（esptool 退出码 %d）" % (what, rc))
        return tail

    def download(self, port, dest, retries=3, baud=None):
        """板子 → 本地镜像（分段读，失败重试）。"""
        baud = int(baud or BAUD_READ)
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        got = bytearray()
        off = DISK_OFF
        left = IMG_BYTES
        tmp = dest + ".part"
        while left > 0:
            n = min(CHUNK, left)
            ok = False
            for attempt in range(1, retries + 1):
                try:
                    self._run(["-p", port, "-b", str(baud), "read-flash",
                               hex(off), hex(n), tmp], "读取 0x%X" % off)
                    with open(tmp, "rb") as fh:
                        blob = fh.read()
                    if len(blob) == n:
                        got += blob
                        ok = True
                        break
                    self.log("  长度不对（%d/%d），重试" % (len(blob), n))
                except RuntimeError as exc:
                    self.log("  第 %d 次失败：%s" % (attempt, exc))
                time.sleep(1.0)
            if not ok:
                raise RuntimeError("读取 0x%X 处 %d 字节连续失败" % (off, n))
            off += n
            left -= n
        try:
            os.remove(tmp)
        except OSError:
            pass
        with open(dest, "wb") as fh:
            fh.write(got)
        self.log("已下载 %d 字节 -> %s" % (len(got), dest))
        return dest

    def upload(self, port, src, baud=None):
        """本地镜像 → 板子。写前请确保模拟器已关闭（否则串口被占）。"""
        baud = int(baud or BAUD_WRITE)
        size = os.path.getsize(src)
        if size != IMG_BYTES:
            raise RuntimeError("镜像大小 %d 字节，必须是 %d（1.44MB）"
                               % (size, IMG_BYTES))
        self._run(["-p", port, "-b", str(baud), "write-flash",
                   hex(DISK_OFF), src], "写入软盘")
        self.log("已上传 %s 到 0x%X" % (src, DISK_OFF))
        return src

    # ---- 固件烧录 ----
    def check_firmware(self, fw_dir=None):
        """返回 (bootloader, partitions, firmware) 三个 bin 的路径，缺哪个就报哪个。"""
        fw_dir = fw_dir or FW_DIR
        parts = {}
        missing = []
        for key, name, off in (
                ("boot", "bootloader.bin", BOOT_OFF),
                ("part", "partitions.bin", PART_OFF),
                ("app", "firmware.bin", APP_OFF)):
            p = os.path.join(fw_dir, name)
            if os.path.exists(p):
                parts[key] = (p, off)
            else:
                missing.append(p)
        if missing:
            raise RuntimeError(
                "固件没编译好，缺：\n  %s\n先编译：\n  "
                "$env:PLATFORMIO_CORE_DIR='F:\\1231\\_tmp\\pio-core'\n  "
                "F:\\1231\\_tmp\\pioenv\\Scripts\\platformio.exe run "
                "-d F:\\1231\\_tmp\\C86S -e c86s"
                % "\n  ".join(missing))
        return parts

    def flash(self, port, baud=None, fw_dir=None, image=None,
              erase_swap=True, backup=None):
        """一键烧录：bootloader/分区表/应用 (+ 可选软盘镜像)，最后硬复位。

        顺序照 flash.ps1：备份(可选) -> 擦 swap -> 三件套 -> 软盘 -> 硬复位。
        --flash-mode 必须是 dio：qio 在这块板子上会 rst:0x7 (TG0WDT_SYS_RST)
        黑屏循环（docs/BUILD.md 的老结论，实测复现过）。
        """
        baud = int(baud or BAUD_WRITE)
        parts = self.check_firmware(fw_dir)
        base = ["--chip", "esp32", "-p", port, "-b", str(baud)]
        no_reset = base + ["--before", "default-reset", "--after", "no-reset"]

        if backup:
            if os.path.exists(backup):
                self.log("备份已存在：%s（跳过）" % backup)
            else:
                os.makedirs(os.path.dirname(os.path.abspath(backup)),
                            exist_ok=True)
                self.log("整片备份 8MB -> %s（约 2-3 分钟）" % backup)
                self._run(no_reset + ["read-flash", "0x0", hex(FLASH_BYTES),
                                      backup], "整片备份")

        if erase_swap:
            self._run(no_reset + ["erase-region", hex(SWAP_OFF),
                                  hex(SWAP_BYTES)], "擦 swap 分区")

        # 最后一个动作才硬复位。不写软盘时就让这条写命令自己复位（别像最初那样
        # 追加一条 "flash-id --before no-reset"：那是在 flasher stub 里再起一个
        # esptool，实测连不上，报 No serial data received）。
        last_after = "no-reset" if image else "hard-reset"
        self._run(base + ["--before", "default-reset", "--after", last_after,
                          "write-flash",
                          "--flash-mode", "dio",
                          "--flash-freq", "80m",
                          "--flash-size", "8MB",
                          hex(BOOT_OFF), parts["boot"][0],
                          hex(PART_OFF), parts["part"][0],
                          hex(APP_OFF), parts["app"][0]],
                  "写 bootloader/分区表/固件")

        if image:
            size = os.path.getsize(image)
            if size != IMG_BYTES:
                raise RuntimeError("软盘镜像 %d 字节，必须是 %d"
                                   % (size, IMG_BYTES))
            self._run(base + ["--before", "default-reset",
                              "--after", "hard-reset",
                              "write-flash", hex(DISK_OFF), image],
                      "写软盘镜像")
        app_size = os.path.getsize(parts["app"][0])
        self.log("烧录完成（应用 %.0f KB）——板子已复位，可以启动模拟器了"
                 % (app_size / 1024.0))

    def restore(self, port, baud=None, backup=None):
        """把整片备份写回板子（恢复原厂 MINI-OS）。"""
        baud = int(baud or BAUD_WRITE)
        backup = backup or BACKUP_PATH
        if not os.path.exists(backup):
            raise RuntimeError("没有备份文件：%s" % backup)
        size = os.path.getsize(backup)
        self.log("把 %s（%d 字节）写回去，约需数分钟…" % (backup, size))
        self._run(["--chip", "esp32", "-p", port, "-b", str(baud),
                   "--before", "default-reset", "--after", "hard-reset",
                   "write-flash", "0x0", backup], "恢复备份")
        self.log("已恢复备份")


# ---------------------------------------------------------------- 镜像文件

def image_files(path):
    """列出镜像里的文件：[(名字, 字节数, 首簇)] + 空闲信息。"""
    img = fat12.Fat12(path)
    try:
        out = []
        for _slot, name, e in img.entries():
            if name is None:
                continue
            out.append((name, struct.unpack_from("<I", e, 28)[0],
                        struct.unpack_from("<H", e, 26)[0]))
        free, total = img.space()
        return out, free * img.cluster_bytes, total * img.cluster_bytes
    finally:
        img.f.close()

def image_sane(path):
    """粗校验：FAT12 引导扇区 + 大小。返回 (ok, 说明)。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return False, "读不到文件：%s" % exc
    if size < IMG_BYTES:
        return False, "只有 %d 字节，不是 1.44MB 软盘镜像" % size
    with open(path, "rb") as fh:
        head = fh.read(64)
    bps = struct.unpack_from("<H", head, 11)[0]
    if head[0] not in (0xEB, 0xE9) or bps != 512:
        return False, "引导扇区不像 FAT12（首字节 0x%02X，%d 字节/扇区）" % (head[0], bps)
    if b"FAT12" not in head:
        return False, "BPB 里没有 FAT12 标记"
    return True, "%d 字节，FAT12，%d 扇区/簇" % (size, head[13])


def save83(name):
    """把任意文件名压成 8.3（FAT12 根目录只认这个）。"""
    base = name.replace("\\", "/").split("/")[-1].upper()
    if "." in base:
        stem, ext = base.rsplit(".", 1)
    else:
        stem, ext = base, ""
    stem, ext = stem[:8], ext[:3]
    return stem + ("." + ext if ext else "")


# ---------------------------------------------------------------- 窗口

def main_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    import c86host as ch                      # 只用来列串口

    root = tk.Tk()
    root.title("C86S 管理器")
    root.geometry("920x660")

    cfg = load_cfg()
    state = {
        "emu": None,          # 模拟器子进程
        "busy": False,
        "img": tk.StringVar(value=os.path.join(ROOT, "data", "cardputer86.img")),
        "port": tk.StringVar(value=cfg.get("port", "COM3")),
        "scale": tk.StringVar(value=str(cfg.get("scale", 3))),
        "link_baud": tk.StringVar(value=cfg.get("link_baud", DEF_LINK_BAUD)),
        "up_baud": tk.StringVar(value=cfg.get("up_baud", DEF_UP_BAUD)),
        "rd_baud": tk.StringVar(value=cfg.get("rd_baud", DEF_RD_BAUD)),
        "flash_img": tk.BooleanVar(value=bool(cfg.get("flash_img", True))),
        "want_backup": tk.BooleanVar(value=bool(cfg.get("want_backup", False))),
        "dirty": False,
        "free": tk.StringVar(value="—"),
        "status": tk.StringVar(value="就绪"),
    }
    board = Board(log=lambda s: log_line(s))

    # ---- 日志 ----
    def log_line(text):
        log.configure(state="normal")
        log.insert("end", text + "\n")
        log.see("end")
        log.configure(state="disabled")

    def set_status(text):
        state["status"].set(text)

    def refresh_title():
        root.title("C86S 管理器" + (" *" if state["dirty"] else ""))

    # ---- 顶部：串口 + 模拟器 ----
    top = ttk.Frame(root, padding=(8, 8, 8, 4))
    top.pack(fill="x")
    ttk.Label(top, text="串口").pack(side="left")
    port_box = ttk.Combobox(top, textvariable=state["port"], width=8)
    port_box.pack(side="left", padx=(4, 4))

    def refresh_ports():
        vals = [v for v, _n in ch.list_serial_ports()]
        port_box["values"] = vals
        if vals and state["port"].get() not in vals:
            state["port"].set(vals[0])
        log_line("串口：%s" % (", ".join(vals) or "（没找到）"))

    ttk.Button(top, text="刷新", width=6, command=refresh_ports).pack(side="left")
    ttk.Label(top, text="   缩放").pack(side="left")
    ttk.Spinbox(top, from_=1, to=6, width=4,
                textvariable=state["scale"]).pack(side="left", padx=(4, 12))

    emu_btn = ttk.Button(top, text="▶ 启动模拟器")
    emu_btn.pack(side="left")
    stop_btn = ttk.Button(top, text="■ 停止")
    stop_btn.pack(side="left", padx=4)

    # ---- 码率 ----
    rate = ttk.Frame(root, padding=(8, 0, 8, 2))
    rate.pack(fill="x")
    ttk.Label(rate, text="链路码率").pack(side="left")
    ttk.Combobox(rate, textvariable=state["link_baud"], width=7,
                 values=BAUD_CHOICES).pack(side="left", padx=(4, 10))
    ttk.Label(rate, text="烧录/上传").pack(side="left")
    ttk.Combobox(rate, textvariable=state["up_baud"], width=7,
                 values=BAUD_CHOICES).pack(side="left", padx=(4, 10))
    ttk.Label(rate, text="读取").pack(side="left")
    ttk.Combobox(rate, textvariable=state["rd_baud"], width=7,
                 values=BAUD_CHOICES).pack(side="left", padx=(4, 10))
    ttk.Label(rate, foreground="#666",
              text="链路码率要板子固件支持（旧固件会自动退回 921600）").pack(
                  side="left")

    # ---- 固件烧录 ----
    fbox = ttk.LabelFrame(
        root, text="固件烧录（bootloader 0x1000 / 分区表 0x8000 / 应用 0x10000）",
        padding=8)
    fbox.pack(fill="x", padx=8, pady=(2, 0))

    fw_line = ttk.Frame(fbox)
    fw_line.pack(fill="x")
    fw_text = tk.StringVar(value="固件目录 %s" % FW_DIR)
    ttk.Label(fw_line, textvariable=fw_text).pack(side="left")
    fw_state = tk.StringVar(value="")

    def refresh_fw_state():
        try:
            parts = board.check_firmware()
        except RuntimeError:
            fw_text.set("固件目录 %s" % FW_DIR)
            fw_state.set("未编译 —— 先 pio run -e c86s")
            return
        size = os.path.getsize(parts["app"][0])
        stamp = time.strftime("%m-%d %H:%M",
                              time.localtime(os.path.getmtime(parts["app"][0])))
        fw_state.set("已编译 %s，firmware.bin %.0f KB（%s）"
                     % ("✓", size / 1024.0, stamp))

    ttk.Label(fw_line, textvariable=fw_state, foreground="#080").pack(
        side="left", padx=(10, 0))

    fline = ttk.Frame(fbox)
    fline.pack(fill="x", pady=(6, 0))
    ttk.Checkbutton(fline, text="同时写软盘镜像（用上面的本地镜像）",
                    variable=state["flash_img"]).pack(side="left")
    ttk.Checkbutton(fline, text="先整片备份（没有备份时）",
                    variable=state["want_backup"]).pack(side="left", padx=(12, 0))
    flash_btn = ttk.Button(fline, text="⚡ 一键烧录")
    flash_btn.pack(side="left", padx=(12, 6))
    restore_btn = ttk.Button(fline, text="写回备份（恢复原厂）")
    restore_btn.pack(side="left")

    # ---- 软盘区 ----
    box = ttk.LabelFrame(root, text="虚拟软盘（板子 0x600000，1.44MB）",
                         padding=8)
    box.pack(fill="both", expand=True, padx=8, pady=(4, 4))

    row = ttk.Frame(box)
    row.pack(fill="x")
    ttk.Label(row, text="本地镜像").pack(side="left")
    ttk.Entry(row, textvariable=state["img"]).pack(side="left", fill="x",
                                                   expand=True, padx=4)

    def browse_img():
        p = filedialog.askopenfilename(
            title="选择软盘镜像", initialdir=os.path.join(ROOT, "data"),
            filetypes=[("软盘镜像", "*.img"), ("所有文件", "*.*")])
        if p:
            state["img"].set(p)
            reload_list()

    ttk.Button(row, text="浏览…", width=8, command=browse_img).pack(side="left")

    btns = ttk.Frame(box)
    btns.pack(fill="x", pady=(6, 4))

    dl_btn = ttk.Button(btns, text="从板子下载…")
    up_btn = ttk.Button(btns, text="上传到板子")
    exp_btn = ttk.Button(btns, text="导出选中…")
    imp_btn = ttk.Button(btns, text="导入文件…")
    del_btn = ttk.Button(btns, text="删除选中")
    rel_btn = ttk.Button(btns, text="刷新列表")
    for b in (dl_btn, up_btn, exp_btn, imp_btn, del_btn, rel_btn):
        b.pack(side="left", padx=(0, 6))

    cols = ("name", "size", "cluster")
    tree = ttk.Treeview(box, columns=cols, show="headings", height=13)
    for c, t, w in (("name", "文件名 (8.3)", 260), ("size", "字节", 110),
                    ("cluster", "首簇", 80)):
        tree.heading(c, text=t)
        tree.column(c, width=w, anchor="w" if c == "name" else "e")
    tree.pack(fill="both", expand=True)
    ttk.Label(box, textvariable=state["free"]).pack(anchor="w", pady=(4, 0))

    # ---- 状态栏 + 日志 ----
    bar = ttk.Frame(root, padding=(8, 0))
    bar.pack(fill="x")
    ttk.Label(bar, textvariable=state["status"]).pack(side="left")

    log = tk.Text(root, height=9, bg="#101014", fg="#8f8",
                  font=("Consolas", 9))
    log.pack(fill="both", expand=False, padx=8, pady=(2, 8))
    log.configure(state="disabled")

    # ---------------- 业务 ----------------
    def guard(fn):
        """统一处理"忙"和异常，别让 Tk 回调把错误吞掉。"""
        def wrap(*a, **k):
            if state["busy"]:
                set_status("上一个操作还没结束")
                return
            state["busy"] = True
            set_status("执行中…")
            try:
                fn(*a, **k)
            except Exception as exc:              # noqa: BLE001
                log_line("!! %s" % exc)
                set_status("失败：%s" % exc)
                messagebox.showerror("出错", str(exc))
            finally:
                state["busy"] = False
        return wrap

    def in_thread(fn, done=None):
        """esptool 很慢，放到后台线程跑，别冻住窗口。"""
        def finish():
            # busy 必须在这里复位：'busy' 一旦不归位，此后每个 guard 操作
            # 都会被「上一个操作还没结束」挡掉（第一次自测就踩到了）。
            state["busy"] = False
            set_buttons(True)
            if done:
                done()

        def work():
            try:
                fn()
            except Exception as exc:              # noqa: BLE001
                root.after(0, lambda: log_line("!! %s" % exc))
                root.after(0, lambda: set_status("失败：%s" % exc))
                root.after(0, lambda: messagebox.showerror("出错", str(exc)))
            finally:
                root.after(0, finish)
        state["busy"] = True
        set_buttons(False)
        set_status("执行中…（esptool 输出见下方日志）")
        threading.Thread(target=work, daemon=True).start()

    def set_buttons(on):
        for b in (dl_btn, up_btn, exp_btn, imp_btn, del_btn,
                  flash_btn, restore_btn):
            b.configure(state="normal" if on else "disabled")
        emu_btn.configure(state="normal" if on else "disabled")

    def reload_list():
        path = state["img"].get()
        for i in tree.get_children():
            tree.delete(i)
        if not os.path.exists(path):
            state["free"].set("镜像不存在：%s" % path)
            return
        ok, why = image_sane(path)
        if not ok:
            state["free"].set("镜像不可用：%s" % why)
            log_line("镜像不可用：%s" % why)
            return
        try:
            files, free, total = image_files(path)
        except Exception as exc:                  # noqa: BLE001
            state["free"].set("列目录失败：%s" % exc)
            return
        for name, size, cluster in sorted(files):
            tree.insert("", "end", values=(name, size, cluster))
        state["free"].set("共 %d 个文件，空闲 %.0f KB / %.0f KB（%s）"
                          % (len(files), free / 1024.0, total / 1024.0, why))
        set_status("已载入 %s" % os.path.basename(path))

    def stop_emu(silent=False):
        p = state["emu"]
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:                     # noqa: BLE001
                p.kill()
            log_line("模拟器已停止")
        state["emu"] = None
        if not silent:
            set_status("模拟器已停止")

    def start_emu():
        if state["emu"] and state["emu"].poll() is None:
            set_status("模拟器已经在跑了")
            return
        safe_log = lambda s: root.after(0, log_line, s)   # noqa: E731
        p = start_emulator(state["port"].get(), state["scale"].get(),
                           log=safe_log, baud=state["link_baud"].get())
        state["emu"] = p
        watch_emulator(p, log=safe_log)
        set_status("模拟器运行中（链路 %s）" % state["link_baud"].get())

    @guard
    def on_start():
        start_emu()

    @guard
    def on_stop():
        stop_emu()

    @guard
    def on_download():
        dest = filedialog.asksaveasfilename(
            title="下载软盘到…", initialdir=os.path.join(ROOT, "data"),
            initialfile="board.img", defaultextension=".img",
            filetypes=[("软盘镜像", "*.img")])
        if not dest:
            return
        if state["emu"] and state["emu"].poll() is None:
            if not messagebox.askyesno("需要串口",
                                       "模拟器正在占用 %s，先关掉它再下载？"
                                       % state["port"].get()):
                return
            stop_emu(silent=True)
        port = state["port"].get()

        def job():
            board.download(port, dest, baud=state["rd_baud"].get())
            ok, why = image_sane(dest)
            root.after(0, lambda: log_line(
                "校验：%s（%s）" % ("通过" if ok else "失败", why)))
            root.after(0, lambda: state["img"].set(dest))
            root.after(0, lambda: state.__setitem__("dirty", False))
            root.after(0, refresh_title)
            root.after(0, reload_list)

        in_thread(job, done=lambda: set_status("下载完成"))

    @guard
    def on_upload():
        src = state["img"].get()
        if not os.path.exists(src):
            raise RuntimeError("本地镜像不存在：%s" % src)
        ok, why = image_sane(src)
        if not ok:
            raise RuntimeError("本地镜像不像 FAT12 软盘：%s" % why)
        if state["emu"] and state["emu"].poll() is None:
            if not messagebox.askyesno("需要串口",
                                       "模拟器正在占用 %s，先关掉它再上传？"
                                       % state["port"].get()):
                return
            stop_emu(silent=True)
        if not messagebox.askyesno(
                "确认上传",
                "把\n  %s\n写入板子 0x%X（覆盖板上现有软盘）。\n继续？"
                % (os.path.basename(src), DISK_OFF)):
            return
        port = state["port"].get()

        def job():
            board.upload(port, src, baud=state["up_baud"].get())
            root.after(0, lambda: state.__setitem__("dirty", False))
            root.after(0, refresh_title)

        in_thread(job, done=lambda: set_status("上传完成（板子已复位）"))

    def ensure_serial_free(action):
        """烧录类操作要独占总口：模拟器开着就先关掉。返回 False = 用户取消。"""
        if state["emu"] and state["emu"].poll() is None:
            if not messagebox.askyesno(
                    "需要串口",
                    "模拟器正在占用 %s，先关掉它再%s？"
                    % (state["port"].get(), action)):
                return False
            stop_emu(silent=True)
        return True

    @guard
    def on_flash():
        parts = board.check_firmware()
        img = state["img"].get() if state["flash_img"].get() else None
        if img:
            ok, why = image_sane(img)
            if not ok:
                raise RuntimeError("要一起写进板子的镜像不可用：%s" % why)
        if not ensure_serial_free("烧录"):
            return
        plan = ["bootloader  0x%X  %s" % (BOOT_OFF,
                                         os.path.basename(parts["boot"][0])),
                "分区表      0x%X  %s" % (PART_OFF,
                                          os.path.basename(parts["part"][0])),
                "应用        0x%X  %s（%.0f KB）"
                % (APP_OFF, os.path.basename(parts["app"][0]),
                   os.path.getsize(parts["app"][0]) / 1024.0),
                "擦 swap     0x%X  %d KB" % (SWAP_OFF, SWAP_BYTES // 1024)]
        if img:
            plan.append("软盘镜像    0x%X  %s" % (DISK_OFF,
                                                 os.path.basename(img)))
        if state["want_backup"].get() and not os.path.exists(BACKUP_PATH):
            plan.append("整片备份    8MB -> %s（约 2-3 分钟）" % BACKUP_PATH)
        if not messagebox.askyesno(
                "确认烧录",
                "往 %s 写：\n  %s\n\n速率 %s。板上现有固件会被覆盖，继续？"
                % (state["port"].get(), "\n  ".join(plan),
                   state["up_baud"].get())):
            return
        port = state["port"].get()
        baud = state["up_baud"].get()
        backup = BACKUP_PATH if state["want_backup"].get() else None

        def job():
            board.flash(port, baud=baud, image=img, backup=backup)
            root.after(0, lambda: state.__setitem__("dirty", False))
            root.after(0, refresh_title)
            root.after(0, refresh_fw_state)

        in_thread(job, done=lambda: set_status("烧录完成（板子已复位）"))

    @guard
    def on_restore():
        if not os.path.exists(BACKUP_PATH):
            raise RuntimeError("没有备份文件：%s" % BACKUP_PATH)
        if not ensure_serial_free("恢复备份"):
            return
        if not messagebox.askyesno(
                "确认恢复",
                "把整片备份\n  %s\n（%d 字节）写回 %s 的 0x0。\n"
                "这会覆盖 C86S 固件和软盘，恢复成原厂 MINI-OS，继续？"
                % (BACKUP_PATH, os.path.getsize(BACKUP_PATH),
                   state["port"].get())):
            return
        port = state["port"].get()
        baud = state["up_baud"].get()

        def job():
            board.restore(port, baud=baud)

        in_thread(job, done=lambda: set_status("已恢复备份（板子已复位）"))

    @guard
    def on_export():
        sel = tree.selection()
        if not sel:
            raise RuntimeError("先在列表里选一个文件")
        outdir = filedialog.askdirectory(title="导出到哪个文件夹")
        if not outdir:
            return
        img = fat12.Fat12(state["img"].get())
        try:
            for item in sel:
                name = tree.item(item, "values")[0]
                blob = img.get(name)
                dst = os.path.join(outdir, name)
                with open(dst, "wb") as fh:
                    fh.write(blob)
                log_line("导出 %s（%d 字节）-> %s" % (name, len(blob), dst))
        finally:
            img.f.close()
        set_status("已导出 %d 个文件" % len(sel))

    @guard
    def on_import():
        picks = filedialog.askopenfilenames(title="选择要放进镜像的文件")
        if not picks:
            return
        path = state["img"].get()
        ok, why = image_sane(path)
        if not ok:
            raise RuntimeError("镜像不可用：%s" % why)
        img = fat12.Fat12(path, writable=True)
        added = 0
        try:
            for p in picks:
                name = save83(os.path.basename(p))
                if img.find(name) is not None:
                    if not messagebox.askyesno(
                            "已存在", "镜像里已有 %s，覆盖？" % name):
                        continue
                _n, size, clusters = img.put(p, name, replace=True)
                log_line("导入 %s -> %s（%d 字节，%d 簇）"
                         % (os.path.basename(p), name, size, clusters))
                added += 1
        finally:
            img.flush()
        state["dirty"] = True
        refresh_title()
        reload_list()
        set_status("已加入 %d 个文件（记得上传到板子）" % added)

    @guard
    def on_delete():
        sel = tree.selection()
        if not sel:
            raise RuntimeError("先在列表里选一个文件")
        names = [tree.item(i, "values")[0] for i in sel]
        if not messagebox.askyesno("确认删除", "从镜像里删掉：\n%s\n（改完记得上传）"
                                   % "\n".join(names)):
            return
        img = fat12.Fat12(state["img"].get(), writable=True)
        try:
            for n in names:
                if img.delete(n):
                    log_line("已删除 %s" % n)
        finally:
            img.flush()
        state["dirty"] = True
        refresh_title()
        reload_list()
        set_status("已删除（记得上传到板子）")

    emu_btn.configure(command=on_start)
    stop_btn.configure(command=on_stop)
    dl_btn.configure(command=on_download)
    up_btn.configure(command=on_upload)
    exp_btn.configure(command=on_export)
    imp_btn.configure(command=on_import)
    del_btn.configure(command=on_delete)
    rel_btn.configure(command=reload_list)
    flash_btn.configure(command=on_flash)
    restore_btn.configure(command=on_restore)

    def on_close():
        """记下这次的串口/缩放/码率，下次打开还是这套。"""
        save_cfg({"port": state["port"].get(),
                  "scale": state["scale"].get(),
                  "link_baud": state["link_baud"].get(),
                  "up_baud": state["up_baud"].get(),
                  "rd_baud": state["rd_baud"].get(),
                  "flash_img": bool(state["flash_img"].get()),
                  "want_backup": bool(state["want_backup"].get())})
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    refresh_ports()
    reload_list()
    refresh_fw_state()
    refresh_title()
    log_line("提示：改完镜像要点「上传到板子」才算写到硬件；上传/下载都会先关掉模拟器腾出串口。")
    log_line("提示：「一键烧录」= bootloader+分区表+固件(+软盘镜像)，板子会自动复位。")
    root.mainloop()


# ---------------------------------------------------------------- 命令行

def main_cli(argv):
    cmd = argv[1]
    log = print
    b = Board(log=log)
    opts = argv[3:]

    def opt(name, default=None):
        if name in opts:
            i = opts.index(name)
            if i + 1 < len(opts):
                return opts[i + 1]
        return default

    if cmd == "flash" and len(argv) >= 3:
        port = argv[2]
        image = opt("--image", os.path.join(ROOT, "data", "cardputer86.img"))
        if "--no-image" in opts:
            image = None
        b.flash(port, baud=opt("--baud", BAUD_WRITE), image=image,
                backup=BACKUP_PATH if "--backup" in opts else None)
        return 0
    if cmd == "restore" and len(argv) >= 3:
        b.restore(argv[2], baud=opt("--baud", BAUD_WRITE))
        return 0
    if cmd == "dl" and len(argv) >= 3:
        port = argv[2]
        dest = argv[3] if len(argv) > 3 else os.path.join(ROOT, "data", "board.img")
        b.download(port, dest, baud=opt("--baud", BAUD_READ))
        print("校验:", image_sane(dest))
        return 0
    if cmd == "up" and len(argv) >= 3:
        port = argv[2]
        src = argv[3] if len(argv) > 3 else os.path.join(ROOT, "data",
                                                         "cardputer86.img")
        b.upload(port, src, baud=opt("--baud", BAUD_WRITE))
        return 0
    if cmd == "ls" and len(argv) >= 3:
        files, free, total = image_files(argv[2])
        for name, size, _c in sorted(files):
            print("  %-14s %8d" % (name, size))
        print("空闲 %.0f KB / %.0f KB" % (free / 1024.0, total / 1024.0))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main_cli(sys.argv))
    main_gui()
