#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
c86host.py -- C86S 电脑端交接程序

给「掌控板 2.0.4 (ESP32 + 8MB) 跑 8086 模拟器、画面走串口」这套东西配的
上位机：把串口上的 RLE 帧还原成 320x200 画面，把键盘敲击翻译成 PS/2 扫描码
发回板子。

刻意零第三方依赖：
  * 串口 —— ctypes 直接调 Win32 API（CreateFile/SetCommState/ReadFile/WriteFile）。
    这台机器的 pip 源返回 403，装不了 pyserial，也不该让用户为跑个 DEMO 去装东西。
  * 画面 —— tkinter + base64 编码的 PPM 直接塞给 PhotoImage（Tk 8.6 原生支持 PPM）。
    没有 Pillow 也能出图，代价是每帧多一次 base64。

用法：
    python c86host.py                      # 自动找 CP210x，921600，3 倍放大
    python c86host.py --port COM3 --scale 4
    python c86host.py --selftest 8         # 无界面自检：收 8 秒，存 screenshot.ppm
"""
import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import threading
import time
import tkinter
import zlib
from tkinter import filedialog

# =====================================================================
# 协议常量（必须与 src/serial/proto.h 一致）
# =====================================================================
MAGIC = b"\xaa\x55"
T_HELLO, T_VIDEO, T_STATS, T_BEEP, T_LOG = 0x01, 0x02, 0x03, 0x04, 0x05
T_KEY, T_CMD = 0x10, 0x11
CMD_RESET, CMD_FULLFRAME, CMD_PING, CMD_SETTIME = 0x01, 0x02, 0x03, 0x04
CMD_SETBAUD = 0x05
BOOT_BAUD = 921600               # 固件上电固定用这个速率（C86_BAUD）
BAUD_PROBE = (921600, 460800, 230400, 115200)   # 找板子时依次试探的速率
KF_EXT = 0x01
VF_FULL, VF_GFX = 0x01, 0x02
FB_W, FB_H = 320, 200
ROW_BYTES = FB_W // 2            # 每行 160 字节，2 像素/字节
HELLO_HDR = 6 + 48               # ver,flags,w,h + 16*RGB888


def crc8(data):
    c = 0
    for b in data:
        c = (c * 31 + b) & 0xFF
    return c


def build_frame(ftype, payload=b"", seq=0):
    head = bytes([ftype, seq & 0xFF, (seq >> 8) & 0xFF,
                  len(payload) & 0xFF, (len(payload) >> 8) & 0xFF])
    return MAGIC + head + payload + bytes([crc8(head + payload)])


def key_frame(scancode, ext=False):
    return build_frame(T_KEY, bytes([scancode, KF_EXT if ext else 0]))


def cmd_frame(cmd, args=b""):
    return build_frame(T_CMD, bytes([cmd]) + args)


def raw_to_png(w, h, rgb, level=1):
    """最小的 PNG 编码器（zlib 是标准库）。

    为什么不用 PPM：tk.PhotoImage 的 PPM 处理器不吃 base64 -data，只吃文件，
    每帧落盘太脏。PNG 处理器吃 base64，实测 320x200 一帧编码+加载约 3 ms。
    """
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)                       # filter type 0
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), level)) +
            chunk(b"IEND", b""))


class FrameParser:
    """按魔数重同步的字节流解析器。板子上电时 115200 的 bootloader 日志在
    921600 下是一堆乱码，靠魔数把它们吃掉。"""

    def __init__(self):
        self.buf = bytearray()
        self.bad_crc = 0
        self.resync = 0

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(MAGIC)
            if i < 0:
                if len(self.buf) > 1:
                    del self.buf[:-1]
                break
            if i > 0:
                del self.buf[:i]
                self.resync += 1
            if len(self.buf) < 7:
                break
            ftype = self.buf[2]
            seq = self.buf[3] | (self.buf[4] << 8)
            length = self.buf[5] | (self.buf[6] << 8)
            if length > 20000:                 # 明显是噪声
                del self.buf[:1]
                continue
            total = 8 + length
            if len(self.buf) < total:
                break
            payload = bytes(self.buf[7:7 + length])
            got = self.buf[7 + length]
            if got == crc8(self.buf[2:7 + length]):
                out.append((ftype, seq, payload))
            else:
                self.bad_crc += 1
            del self.buf[:total]
        if len(self.buf) > 65536:
            self.buf.clear()
        return out


# =====================================================================
# Win32 串口（ctypes，无 pyserial）
# =====================================================================
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PURGE_TXCLEAR, PURGE_RXCLEAR = 0x0004, 0x0008


class DCB(ctypes.Structure):
    _fields_ = [
        ("DCBlength", wt.DWORD),
        ("BaudRate", wt.DWORD),
        ("fBinary", wt.DWORD, 1),
        ("fParity", wt.DWORD, 1),
        ("fOutxCtsFlow", wt.DWORD, 1),
        ("fOutxDsrFlow", wt.DWORD, 1),
        ("fDtrControl", wt.DWORD, 2),
        ("fDsrSensitivity", wt.DWORD, 1),
        ("fTXContinueOnXoff", wt.DWORD, 1),
        ("fOutX", wt.DWORD, 1),
        ("fInX", wt.DWORD, 1),
        ("fErrorChar", wt.DWORD, 1),
        ("fNull", wt.DWORD, 1),
        ("fRtsControl", wt.DWORD, 2),
        ("fAbortOnError", wt.DWORD, 1),
        ("fDummy2", wt.DWORD, 17),
        ("wReserved", wt.WORD),
        ("XonLim", wt.WORD),
        ("XoffLim", wt.WORD),
        ("ByteSize", ctypes.c_ubyte),
        ("Parity", ctypes.c_ubyte),
        ("StopBits", ctypes.c_ubyte),
        ("XonChar", ctypes.c_char),
        ("XoffChar", ctypes.c_char),
        ("ErrorChar", ctypes.c_char),
        ("EofChar", ctypes.c_char),
        ("EvtChar", ctypes.c_char),
        ("wReserved1", wt.WORD),
    ]


class COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [
        ("ReadIntervalTimeout", wt.DWORD),
        ("ReadTotalTimeoutMultiplier", wt.DWORD),
        ("ReadTotalTimeoutConstant", wt.DWORD),
        ("WriteTotalTimeoutMultiplier", wt.DWORD),
        ("WriteTotalTimeoutConstant", wt.DWORD),
    ]


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
k32.GetCommState.argtypes = [wt.HANDLE, ctypes.POINTER(DCB)]
k32.SetCommState.argtypes = [wt.HANDLE, ctypes.POINTER(DCB)]
k32.SetCommTimeouts.argtypes = [wt.HANDLE, ctypes.POINTER(COMMTIMEOUTS)]
k32.SetupComm.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
k32.PurgeComm.argtypes = [wt.HANDLE, wt.DWORD]
k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                         ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                          ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
k32.CloseHandle.argtypes = [wt.HANDLE]


class SerialPort:
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        path = port if port.startswith("\\\\.\\") else "\\\\.\\" + port
        self.h = k32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, None,
                                 OPEN_EXISTING, 0, None)
        if not self.h or self.h == INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(),
                          "无法打开 %s（被别的程序占着？）" % port)
        self.lock = threading.Lock()
        k32.SetupComm(self.h, 1 << 16, 1 << 16)
        dcb = DCB()
        dcb.DCBlength = ctypes.sizeof(DCB)
        if not k32.GetCommState(self.h, ctypes.byref(dcb)):
            raise OSError(ctypes.get_last_error(), "GetCommState 失败")
        dcb.BaudRate = baud
        dcb.ByteSize = 8
        dcb.Parity = 0          # NOPARITY
        dcb.StopBits = 0        # ONESTOPBIT
        dcb.fBinary = 1
        dcb.fParity = 0
        dcb.fOutxCtsFlow = 0
        dcb.fOutxDsrFlow = 0
        dcb.fOutX = 0
        dcb.fInX = 0
        dcb.fDtrControl = 0     # 不拉 DTR：CP2104 的 DTR/RTS 可能挂到 EN/IO0，
        dcb.fRtsControl = 0     # 一打开就复位板子会让人以为固件崩了
        dcb.fAbortOnError = 0
        if not k32.SetCommState(self.h, ctypes.byref(dcb)):
            raise OSError(ctypes.get_last_error(), "SetCommState 失败（波特率不支持？）")
        tmo = COMMTIMEOUTS(0xFFFFFFFF, 0, 0, 0, 3000)   # 读不阻塞，写最多 3s
        k32.SetCommTimeouts(self.h, ctypes.byref(tmo))
        k32.PurgeComm(self.h, PURGE_TXCLEAR | PURGE_RXCLEAR)

    def read(self, n=4096):
        buf = ctypes.create_string_buffer(n)
        got = wt.DWORD(0)
        with self.lock:
            ok = k32.ReadFile(self.h, buf, n, ctypes.byref(got), None)
        if not ok:
            return None
        return buf.raw[:got.value]

    def write(self, data):
        if not data:
            return 0
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        wrote = wt.DWORD(0)
        with self.lock:
            ok = k32.WriteFile(self.h, buf, len(data), ctypes.byref(wrote), None)
        if not ok:
            raise OSError(ctypes.get_last_error(), "WriteFile 失败（串口被占用？）")
        if wrote.value != len(data):
            raise OSError(0, "只写进 %d/%d 字节" % (wrote.value, len(data)))
        return wrote.value

    def close(self):
        try:
            k32.CloseHandle(self.h)
        except Exception:
            pass
        self.h = None


def list_serial_ports():
    """从注册表拿串口列表；再尽量标出哪个是 CP210x（掌控板的 USB 桥）。"""
    ports = []
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DEVICEMAP\SERIALCOMM")
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            ports.append((value, name))
            i += 1
    except OSError:
        pass
    return ports


def pick_port(explicit=None):
    if explicit:
        return explicit
    ports = list_serial_ports()
    if not ports:
        return None
    for value, name in ports:
        if "Silab" in name or "CP210" in name:
            return value
    return ports[0][0]


def _wait_hello(ser, timeout):
    """在一个刚打开的端口上要一次 HELLO：发 PING，等 HELLO 帧。
    返回 True/False。用独立的 FrameParser，不碰 App 的状态。"""
    parser = FrameParser()
    deadline = time.time() + timeout
    try:
        ser.write(cmd_frame(CMD_PING))
    except OSError:
        return False
    while time.time() < deadline:
        data = ser.read(4096)
        if not data:
            time.sleep(0.02)
            continue
        for ftype, _seq, _pl in parser.feed(data):
            if ftype == T_HELLO:
                return True
    return False


def _probe_baud(port, baud, timeout):
    """在 baud 上开一下端口问 HELLO，通不通都关上。"""
    try:
        ser = SerialPort(port, baud)
    except OSError:
        return False
    try:
        return _wait_hello(ser, timeout)
    except OSError:
        return False
    finally:
        try:
            ser.close()
        except Exception:                            # noqa: BLE001
            pass


def resolve_baud(port, want, log=None, timeout=0.6):
    """找出板子现在到底在哪个速率上说话；都探不到就返回 want（App 会显示未连接）。

    为什么需要：板子只在收到 SETBAUD 时换速率，它不记得"上次是谁把它改走的"。
    c86host 正常退出会交回 921600，被强杀就交不回——此时板子还停在 230400 之类
    的速率上，按 921600 连它一句话都不应，看起来像板子坏了。
    """
    say = log or (lambda _s: None)
    if _probe_baud(port, want, timeout):
        return want
    for b in BAUD_PROBE:
        if b == want:
            continue
        if _probe_baud(port, b, timeout):
            say("板子在 %d 上%s，按它连"
                % (b, "" if b == BOOT_BAUD else "（上次会话改的速率）"))
            return b
    return want


def negotiate_baud(port, want, log=None, timeout=1.0):
    """把链路速率切到 want，返回实际可用的速率。

    板子上电是 BOOT_BAUD(921600)，SETBAUD 是绝对速率（和当前速率无关），
    所以流程是：先找到板子现在在哪个速率 -> 在那儿发 SETBAUD(want) -> 用 want 确认。
    确认不到（老固件没这条指令 / 线材撑不住）就退回原来那个速率。
    """
    say = log or (lambda _s: None)
    cur = resolve_baud(port, want, say, 0.6)
    if cur == want:
        return want
    try:
        ser = SerialPort(port, cur)
    except OSError as exc:
        say("打开 %s @ %d 失败：%s" % (port, cur, exc))
        return cur
    try:
        ser.write(cmd_frame(CMD_SETBAUD, struct.pack("<I", want)))
        time.sleep(0.15)
    except OSError as exc:
        say("发送 SETBAUD 失败：%s" % (exc,))
        return cur
    finally:
        try:
            ser.close()
        except Exception:                            # noqa: BLE001
            pass
    if _probe_baud(port, want, timeout):
        say("链路速率 %d -> %d" % (cur, want))
        return want
    say("板子没接受 %d（固件可能没有 SETBAUD），仍用 %d" % (want, cur))
    return cur


# =====================================================================
# 画面解码
# =====================================================================
def rle_decode(src):
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        i += 1
        cnt = (c & 0x7F) + 1
        if c & 0x80:
            if i >= n:
                break
            out.extend(bytes([src[i]]) * cnt)
            i += 1
        else:
            out.extend(src[i:i + cnt])
            i += cnt
    return out


class Renderer:
    def __init__(self):
        self.fb4 = bytearray(ROW_BYTES * FB_H)
        self.rgb = bytearray(FB_W * FB_H * 3)
        self.palette = [(0, 0, 0)] * 16
        self.mode_gfx = False
        self.tbl = self._build_tbl()
        self.dirty = True

    def set_palette(self, pal48):
        self.palette = [tuple(pal48[i * 3:i * 3 + 3]) for i in range(16)]
        self.tbl = self._build_tbl()
        self.dirty = True

    def _build_tbl(self):
        """256 项查找表：一个打包字节（两个像素）展开成 6 个 RGB 字节。

        文字模式：半字节就是 CGA 调色板索引。
        图形模式：模拟器往 framebuffer 里写的是 6 位 VGA 电平（0x00/0x55/
        0xAA/0xFF），低半字节因此只会是 0/5/A/F，按灰度展开才对。
        """
        gfx = {0x0: (0, 0, 0), 0x5: (85, 85, 85),
               0xA: (170, 170, 170), 0xF: (255, 255, 255)}
        tbl = []
        for b in range(256):
            hi, lo = (b >> 4) & 0xF, b & 0xF
            if self.mode_gfx:
                a = gfx.get(hi, self.palette[hi])
                c = gfx.get(lo, self.palette[lo])
            else:
                a = self.palette[hi]
                c = self.palette[lo]
            tbl.append(bytes(a) + bytes(c))
        return tbl

    def apply_video(self, payload):
        if len(payload) < 2:
            return
        flags = payload[0]
        rows = payload[1]
        gfx = bool(flags & VF_GFX)
        if gfx != self.mode_gfx:
            # 模式变了只换查找表；这一帧的行照常上屏。板子那边用"代数"标记
            # 脏行，切模式后的每一行都会陆续推过来，不会漏。
            self.mode_gfx = gfx
            self.tbl = self._build_tbl()
        off = 2
        stride = FB_W * 3
        for _ in range(rows):
            if off + 3 > len(payload):
                break
            r = payload[off]
            rl = payload[off + 1] | (payload[off + 2] << 8)
            off += 3
            raw = rle_decode(payload[off:off + rl])
            off += rl
            if len(raw) != ROW_BYTES or r >= FB_H:
                continue
            self.fb4[r * ROW_BYTES:(r + 1) * ROW_BYTES] = raw
            self.rgb[r * stride:(r + 1) * stride] = b"".join(
                [self.tbl[b] for b in raw])
            self.dirty = True

    def ppm(self):
        return b"P6\n%d %d\n255\n" % (FB_W, FB_H) + bytes(self.rgb)


# =====================================================================
# 键盘：Tk 事件 -> PS/2 set-1 扫描码
# =====================================================================
LETTER_SC = {
    'a': 0x1E, 'b': 0x30, 'c': 0x2E, 'd': 0x20, 'e': 0x12, 'f': 0x21,
    'g': 0x22, 'h': 0x23, 'i': 0x17, 'j': 0x24, 'k': 0x25, 'l': 0x26,
    'm': 0x32, 'n': 0x31, 'o': 0x18, 'p': 0x19, 'q': 0x10, 'r': 0x13,
    's': 0x1F, 't': 0x14, 'u': 0x16, 'v': 0x2F, 'w': 0x11, 'x': 0x2D,
    'y': 0x15, 'z': 0x2C,
}
DIGIT_SC = {'1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05, '5': 0x06,
            '6': 0x07, '7': 0x08, '8': 0x09, '9': 0x0A, '0': 0x0B}
PUNCT_SC = {
    '-': 0x0C, '=': 0x0D, '[': 0x1A, ']': 0x1B, ';': 0x27, "'": 0x28,
    '`': 0x29, '\\': 0x2B, ',': 0x33, '.': 0x34, '/': 0x35, ' ': 0x39,
}
SHIFTED_PUNCT = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6',
    '&': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=', '{': '[', '}': ']', ':': ';', '"': "'",
    '~': '`', '|': '\\', '<': ',', '>': '.', '?': '/',
}
KEYSYM_SC = {
    "Return": (0x1C, False), "KP_Enter": (0x1C, False),
    "BackSpace": (0x0E, False), "Tab": (0x0F, False),
    "Escape": (0x01, False), "Delete": (0x53, True),
    "Left": (0x4B, True), "Right": (0x4D, True),
    "Up": (0x48, True), "Down": (0x50, True),
    "Home": (0x47, True), "End": (0x4F, True),
    "Prior": (0x49, True), "Next": (0x51, True), "Insert": (0x52, True),
    "Shift_L": (0x2A, False), "Shift_R": (0x36, False),
    "Control_L": (0x1D, False), "Control_R": (0x1D, True),
    "Alt_L": (0x38, False), "Alt_R": (0x38, True),
    "Caps_Lock": (0x3A, False), "Num_Lock": (0x45, False),
    "Scroll_Lock": (0x46, False), "Pause": (0x1D, False),
}
for _i in range(1, 11):
    KEYSYM_SC["F%d" % _i] = (0x3A + _i, False)      # F1=0x3B .. F10=0x44
KEYSYM_SC["F11"] = (0x57, False)
KEYSYM_SC["F12"] = (0x58, False)


def char_to_key(ch):
    """返回 (扫描码, 是否需要 Shift)；无法映射返回 None。"""
    if len(ch) != 1:
        return None
    low = ch.lower()
    if low in LETTER_SC:
        return LETTER_SC[low], ch.isupper()
    if ch in DIGIT_SC:
        return DIGIT_SC[ch], False
    if ch in PUNCT_SC:
        return PUNCT_SC[ch], False
    if ch in SHIFTED_PUNCT:
        base = SHIFTED_PUNCT[ch]
        if base in DIGIT_SC:
            return DIGIT_SC[base], True
        return PUNCT_SC[base], True
    return None


# Tk 对 Ctrl+Break 报的 keysym（Windows 上可能是 Cancel 或 Break），客人机按 Del 处理
BREAK_KEYSYMS = ("Cancel", "Break")


def resolve_key(ev):
    """把一个 Tk 键盘事件解析成 (扫描码, 扩展位, 是否需要 Shift)；认不出返回 None。

    三条来源，按优先级：

    1. **可打印字符** —— 字母数字标点都走 `char_to_key`（它们不在 KEYSYM_SC 里）；
    2. **KEYSYM_SC** —— 方向键 / 功能键 / 回车这些非打印键；
    3. **单字符 keysym 兜底** —— 这是 Ctrl/Alt 组合键唯一能走通的路：
       Ctrl+C 的 `event.char` 是 `'\\x03'`（不可打印，被第 1 条拒掉），
       而 `'c'` 又不在 KEYSYM_SC 里，不兜底就一个扫描码都发不出去
       （实测现象：按 Ctrl+C 只有修饰键的 0x1D/0x9D，字母根本没发）。
    """
    char = getattr(ev, "char", "") or ""
    if char and len(char) == 1 and ord(char) >= 0x20:
        m = char_to_key(char)
        if m:
            return m[0], False, m[1]
    m = KEYSYM_SC.get(ev.keysym)
    if m:
        return m[0], m[1], False
    if len(ev.keysym) == 1:
        m = char_to_key(ev.keysym)
        if m:
            return m[0], False, m[1]
    if ev.keysym in BREAK_KEYSYMS:
        return 0x53, True, False
    return None


# =====================================================================
# 主窗口
# =====================================================================
class App:
    def __init__(self, root, port, baud, scale, keydebug=False):
        self.root = root
        self.port_name = port
        self.baud = baud
        self.scale = scale
        self.keydebug = keydebug
        self.ser = None
        self.parser = FrameParser()
        self.rnd = Renderer()
        self.alive = True
        self.shift_down = False
        self.auto_shift = set()
        self.ctrl_down = False
        self.alt_down = False
        self.auto_ctrl = set()      # 自动按下的 Ctrl，记扫描码，松开时补 0x9D
        self.auto_alt = set()
        self.pressed = set()
        self.heal_count = 0         # 补发修饰键 break 的次数（诊断用）
        self.last_key_at = time.monotonic()  # 最近一次键盘事件的时刻（看门狗用）
        self.last_open_try = 0.0    # 上次尝试打开串口的时刻（自动重连用）
        self.open_tries = 0
        self.frames = 0
        self.fps = 0
        self.stat = {}
        self.last_frame_at = 0
        self.photo = None
        self.photo_zoom = None

        root.title("C86S - 掌控板 2.0.4 上的 8086 PC")
        root.configure(bg="#101014")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        bar = tkinter.Frame(root, bg="#101014")
        bar.pack(side="top", fill="x", padx=6, pady=4)

        def btn(text, cmd, **kw):
            b = tkinter.Button(bar, text=text, command=cmd, bg="#1e1e26",
                               fg="#d8d8e0", activebackground="#2d2d3a",
                               activeforeground="#ffffff", relief="flat",
                               padx=8, pady=3, **kw)
            b.pack(side="left", padx=2)
            return b

        btn("重连", self.reconnect)
        btn("复位机身", lambda: self.send(cmd_frame(CMD_RESET)))
        btn("Ctrl+Alt+Del", self.send_cad)
        btn("重画全屏", lambda: self.send(cmd_frame(CMD_FULLFRAME)))
        btn("松开修饰键", self.on_release_mods)
        btn("粘贴文字…", self.paste_text)
        btn("存截图", self.save_png)
        tkinter.Label(bar, text=" 缩放", bg="#101014", fg="#888").pack(side="left")
        self.scale_var = tkinter.IntVar(value=scale)
        sp = tkinter.Spinbox(bar, from_=1, to=4, width=3,
                             textvariable=self.scale_var, command=self.on_scale)
        sp.pack(side="left")

        self.status = tkinter.StringVar(value="未连接")
        tkinter.Label(root, textvariable=self.status, anchor="w",
                      bg="#181820", fg="#9ad", font=("Consolas", 9)).pack(
            side="bottom", fill="x")

        self.screen = tkinter.Label(root, bg="#000000", bd=0,
                                    highlightthickness=0)
        self.screen.pack(side="top")

        self.log = tkinter.Text(root, height=5, bg="#0c0c10", fg="#7a7",
                                font=("Consolas", 8), bd=0, state="disabled")
        self.log.pack(side="bottom", fill="x")

        root.bind_all("<KeyPress>", self.on_key_press)
        root.bind_all("<KeyRelease>", self.on_key_release)
        # 窗口重新拿到焦点时先补一遍修饰键的 break 码：失焦瞬间松开的 Shift
        # 我们收不到 KeyRelease，客人机会一直当作 Shift 按着。
        root.bind_all("<FocusIn>", lambda _e: self.release_all_modifiers())
        # 失焦时同样补一遍：中文 IME 的候选窗一弹出，keyup 就进了别人的窗口，
        # 等焦点回来时 Shift 早就松开了，可我们和客人机都还记着「按着」。
        root.bind_all("<FocusOut>", lambda _e: self.release_all_modifiers())
        self.screen.focus_set()

        self.open_port()
        self.root.after(16, self.tick)

    # ---------------- 串口 ----------------
    def dbg(self, text):
        """--keydebug 时的排查口：写 stderr（重定向到文件就能事后看），
        不碰界面上的日志栏，免得调试信息把用户的日志冲掉。"""
        if not self.keydebug:
            return
        try:
            sys.stderr.write("[dbg] %s\n" % (text,))
            sys.stderr.flush()
        except Exception:
            pass

    def open_port(self, quick=False):
        """打开串口。quick=True 是自动重连用的快速路径：只按上次的速率问一句
        HELLO（0.25 秒），不通立刻返回，免得每 4 秒把界面卡住 2 秒多。"""
        self.last_open_try = time.monotonic()
        if self.ser:
            self.ser.close()
            self.ser = None
        port = pick_port(self.port_name)
        if not port:
            self.set_status("没有找到任何串口 —— 板子插上了吗？（插上后会自动连）")
            return
        if quick and not _probe_baud(port, self.baud, 0.25):
            self.set_status("等待 %s …（插上板子会自动连，也可以点「重连」）" % port)
            return
        # 先找出板子现在在哪个速率，再协商到我们想要的（谈不成退回原速率）
        self.baud = negotiate_baud(port, self.baud, log=self.append_log)
        try:
            self.ser = SerialPort(port, self.baud)
        except OSError as exc:
            self.dbg("open_port 失败: %r" % (exc,))
            self.set_status("打开 %s 失败：%s（插上板子后会自动重试）"
                            % (port, exc))
            return
        self.dbg("open_port 成功 %s @ %d" % (port, self.baud))
        self.port_name = port
        self.parser = FrameParser()
        self.frames = 0
        self.have_palette = False
        self.ping_tries = 0
        self.append_log("已连接 %s @ %d" % (port, self.baud))
        # PING 让板子补发 HELLO（调色板在里面）；FULLFRAME 让它把整屏推一遍。
        # 板子可能早就开完机了，不主动要就永远拿不到调色板——屏幕会全黑。
        self.send(cmd_frame(CMD_PING))
        self.send(cmd_frame(CMD_FULLFRAME))
        self.release_all_modifiers()
        threading.Thread(target=self.read_loop, daemon=True).start()

    def reconnect(self):
        self.open_port()

    def read_loop(self):
        ser = self.ser
        while self.alive and self.ser is ser:
            data = ser.read(8192)
            if data is None:
                time.sleep(0.05)
                continue
            if not data:
                time.sleep(0.002)
                continue
            for ftype, seq, payload in self.parser.feed(data):
                self.handle_frame(ftype, payload)

    def send(self, data):
        if not self.ser:
            self.dbg("send 丢弃 %d 字节（串口未连接）" % len(data))
            return
        try:
            n = self.ser.write(data)
            self.dbg("send %d 字节（写入 %s）payload=%s"
                     % (len(data), n, bytes(data[:10]).hex()))
        except Exception as exc:
            # 以前这里是裸 pass：写失败就静默丢键，表现成"界面收不到输入"。
            self.dbg("send 失败: %r" % (exc,))
            self.set_status("串口写失败：%s" % (exc,))

    # ---------------- 帧处理（读线程） ----------------
    def handle_frame(self, ftype, payload):
        if ftype == T_VIDEO:
            self.rnd.apply_video(payload)
            self.frames += 1
            self.last_frame_at = time.time()
        elif ftype == T_HELLO:
            if len(payload) >= HELLO_HDR:
                pal = payload[6:HELLO_HDR]
                self.rnd.set_palette(pal)
                self.have_palette = True
                self.append_log("板子握手: ver=%d %dx%d  %s" % (
                    payload[0],
                    payload[2] | (payload[3] << 8),
                    payload[4] | (payload[5] << 8),
                    payload[HELLO_HDR:].decode("ascii", "replace")))
                self.send(cmd_frame(CMD_FULLFRAME))
        elif ftype == T_LOG:
            self.append_log(payload.decode("utf-8", "replace"))
        elif ftype == T_STATS:
            if len(payload) >= 7:
                self.stat = {
                    "fps": payload[0],
                    "mode": payload[1],
                    "guest": payload[2] | (payload[3] << 8),
                    "heap": payload[4] | (payload[5] << 8),
                    "drop": payload[6],
                }
        elif ftype == T_BEEP:
            pass      # 主板小喇叭的声音，PC 侧暂时不播

    def append_log(self, text):
        def do():
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            if int(self.log.index("end-1c").split(".")[0]) > 200:
                self.log.delete("1.0", "50.0")
            self.log.see("end")
            self.log.configure(state="disabled")
        try:
            self.root.after(0, do)
        except Exception:
            pass

    def set_status(self, text):
        try:
            self.root.after(0, lambda: self.status.set(text))
        except Exception:
            pass

    # ---------------- 定时刷屏 ----------------
    def tick(self):
        if not self.alive:
            return
        if self.rnd.dirty:
            self.rnd.dirty = False
            try:
                self.blit()
            except Exception as exc:
                self.append_log("渲染失败: %s" % exc)
        now = time.time()
        if now - getattr(self, "_last_fps", 0) >= 1.0:
            self._last_fps = now
            if not self.have_palette and self.ping_tries < 15:
                self.ping_tries += 1
                self.send(cmd_frame(CMD_PING))
                self.send(cmd_frame(CMD_FULLFRAME))
            s = self.stat
            self.status.set(
                "%s @%d   %d fps   guest %sKB   heap %sKB   %s   drop %s   "
                "bad_crc %d   帧 %d%s" % (
                    self.port_name or "-", self.baud, s.get("fps", 0),
                    s.get("guest", "?"), s.get("heap", "?"),
                    "GFX" if self.rnd.mode_gfx else "TEXT",
                    s.get("drop", 0), self.parser.bad_crc, self.frames,
                    ("   自愈 %d" % self.heal_count) if self.heal_count else ""))
        # 板子没插 / 刚拔了又插上：4 秒一次自动重连（快速路径，不卡界面）
        if self.ser is None and self.alive:
            if time.monotonic() - self.last_open_try > 4.0:
                self.open_tries += 1
                self.open_port(quick=True)
        self._mod_watchdog()
        self.root.after(16, self.tick)

    def _mod_watchdog(self):
        """兜底：记账认为 Shift/Ctrl/Alt 按着，却 5 秒没有任何键盘事件。

        真人按住修饰键超过 5 秒而不碰别的键极少见；一旦出现，多半是 keyup
        丢在 IME 或别的窗口里了。此时补一次 break —— 就算真的按着，下一个
        上档字符会自带一圈 Shift，打字结果不受影响。
        """
        if not (self.shift_down or self.ctrl_down or self.alt_down):
            return
        if time.monotonic() - self.last_key_at < 5.0:
            return
        stale = []
        if self.shift_down:
            stale.append(0x2A)
        if self.ctrl_down:
            stale.append(0x1D)
        if self.alt_down:
            stale.append(0x38)
        self._heal_mods(stale, "按住超过 5 秒没有后续按键")

    def blit(self):
        scale = max(1, int(self.scale_var.get()))
        data = base64.b64encode(raw_to_png(FB_W, FB_H, self.rnd.rgb))
        img = tkinter.PhotoImage(data=data, format="png")
        if scale != 1:
            img = img.zoom(scale, scale)
        self.photo = img
        self.screen.configure(image=img)

    def on_scale(self):
        self.rnd.dirty = True

    # ---------------- 键盘 ----------------
    # 这块被报了三次（「数字变成上面的符号」「无法输入 / . 变 <>」「Shift 锁死
    # 很容易出现」），根因始终只有一个：**Tk 的 KeyRelease 会丢**。本机装着中文
    # IME（窗口树里有 `MSCTFIME UI`），一部分 keyup 会被它吃掉；窗口失焦那一
    # 瞬间松开的键也收不到。只要漏一次，两边状态就一起跑偏、而且一直错下去：
    #   * 我们记账以为 Shift 还按着 → 上档字符走「只发键本身」的分支，可客人机
    #     的标志位其实已经清了 → 该出 '!' 的出了 '1'；
    #   * 或者反过来，客人机 BIOS 的 Shift 位（0x40:0x17）一直挂着 → 未上档的
    #     '1' 被翻译成 '!'、',' 变 '<'、'.' 变 '>'。
    # 前几版都在「怎么把 release 配对做对」上打转，可 release 本身就不可靠。
    # 这一版的原则是**不信任记账，每个按键自带干净状态**：
    #   1) 上档字符永远是「Shift make → 键 → Shift break」的原子三连，不看记账
    #      （记账错了也不影响结果，物理按住 Shift 时多这一对也不会累积 ——
    #      BIOS 只是置位/清位）；
    #   2) 未上档的可打印字符，发送前先补一对 Shift break（0xAA 左 + 0xB6 右：
    #      BIOS 的 0x417 里左右 Shift 是两个独立标志位，只清左边清不掉右边）——
    #      对没按下的键是幂等的，客人机那边就算还挂着 Shift（早先漏发的 break）
    #      也一并清掉；
    #   3) 记账与「系统给的字符」冲突时以字符为准，顺手补上丢失的 break ——
    #      可打印且未上档 ⇒ Shift 没按着；裸字母（keysym 与 char 同一个字母）
    #      ⇒ Ctrl/Alt 没按着；
    #   4) 方向键/功能键不动修饰键状态，Shift+方向键、Ctrl+方向键这类真·按住
    #      场景保持可用；
    #   5) 兜底：记账认为按着、却 5 秒没有任何键盘事件 → tick 里补一次 break。
    def _heal_mods(self, scs, reason):
        """给客人机补发修饰键 break，并把本地记账改回真实状态（幂等）。

        注意 Shift 要**左右两个 break 都发**（0xAA + 0xB6）：BIOS 的 0x40:0x17
        里左 Shift 占 bit1、右 Shift 占 bit0，是**两个独立标志位**。只发 0xAA
        只能清掉左边；用户按的是右边 Shift 时，标志位一直挂着 —— ',' 就永远
        是 '<'。这一条是本轮「Shift 锁死还在」的直接原因。
        """
        todo = [sc for sc in scs if sc]
        if not todo:
            return
        shift = (0x2A in todo) or (0x36 in todo)
        if shift:
            self.send(key_frame(0x2A | 0x80))       # 0xAA 左 Shift 抬起
            self.send(key_frame(0x36 | 0x80))       # 0xB6 右 Shift 抬起
            self.shift_down = False
            self.auto_shift.clear()
        if 0x1D in todo:
            self.send(key_frame(0x1D | 0x80))
            self.ctrl_down = False
            self.auto_ctrl.clear()
        if 0x38 in todo:
            self.send(key_frame(0x38 | 0x80))
            self.alt_down = False
            self.auto_alt.clear()
        self.heal_count += 1
        self.dbg("自愈 %s（第 %d 次）" % (reason, self.heal_count))
        if self.heal_count % 5 == 1:          # 留痕但不刷屏
            self.append_log("已补发修饰键释放码：%s（累计 %d 次）"
                            % (reason, self.heal_count))

    def on_key_press(self, ev):
        ks = ev.keysym
        self.last_key_at = time.monotonic()
        self.dbg("KeyPress keysym=%r char=%r state=%d 跟踪 shift=%s ctrl=%s alt=%s"
                 % (ks, ev.char, getattr(ev, "state", 0),
                    self.shift_down, self.ctrl_down, self.alt_down))
        if ks in ("Shift_L", "Shift_R"):
            if self.shift_down:
                return
            self.shift_down = True
            self.send(key_frame(KEYSYM_SC[ks][0]))
            return
        if ks in ("Control_L", "Control_R"):
            if self.ctrl_down:
                return
            self.ctrl_down = True
            self.send(key_frame(0x1D))
            return
        if ks in ("Alt_L", "Alt_R"):
            if self.alt_down:
                return
            self.alt_down = True
            self.send(key_frame(0x38))
            return

        r = resolve_key(ev)
        if r is None:
            return
        sc, ext, need_shift = r
        char = getattr(ev, "char", "") or ""
        printable = len(char) == 1 and ord(char) >= 0x20
        # 「裸字母」：char 与 keysym 是同一个字母，说明没有任何修饰键参与翻译
        bare_letter = (printable and char.isalpha()
                       and len(ks) == 1 and ks.lower() == char.lower())

        # --- 以字符为证据自愈：字符是操作系统给的，比我们的记账可信 ---
        stale = []
        if self.shift_down and printable and not need_shift:
            stale.append(0x2A)                 # 有 Shift 时字符必然是上档形态
        if self.ctrl_down and bare_letter:
            stale.append(0x1D)                 # 有 Ctrl 时字母是控制字符
        if self.alt_down and bare_letter:
            stale.append(0x38)
        if stale:
            self._heal_mods(stale, "修饰键释放事件丢失")

        if need_shift:
            # 原子三连，不查记账
            self.send(key_frame(0x2A))
            self.send(key_frame(sc, ext))
            self.send(key_frame(0x2A | 0x80))
            return
        if printable:
            # 未上档的可打印字符：先无条件清一次 Shift —— **左右两个 break 都发**，
            # 因为 BIOS 的 0x417 里左/右 Shift 是两个独立标志位（bit1/bit0），只发
            # 0xAA 清不掉右边那个。这一步是「Shift 锁死」的兜底：哪怕上一次的
            # 0xAA/0xB6 在串口上丢了、客人机还挂着 Shift，这一次也会把它清掉，
            # '1' 不会变成 '!'。多发是幂等的（BIOS 只是清位）。
            self.send(key_frame(0x2A | 0x80))
            self.send(key_frame(0x36 | 0x80))
            self.send(key_frame(sc, ext))
            return
        self.send(key_frame(sc, ext))

    def on_key_release(self, ev):
        ks = ev.keysym
        self.last_key_at = time.monotonic()
        if ks in ("Shift_L", "Shift_R"):
            # break 无条件发：记账可能已经被自愈改回 False，但多发一个 break
            # 对 BIOS 是幂等的，少发一个才会让客人机永远认为 Shift 按着。
            self.shift_down = False
            self.send(key_frame(KEYSYM_SC[ks][0] | 0x80))
            return
        if ks in ("Control_L", "Control_R"):
            if not self.ctrl_down:
                return
            self.ctrl_down = False
            self.send(key_frame(0x1D | 0x80))
            return
        if ks in ("Alt_L", "Alt_R"):
            if not self.alt_down:
                return
            self.alt_down = False
            self.send(key_frame(0x38 | 0x80))
            return

        # 只需发键自己的 break。自动修饰键在按下那一刻就已经闭合，这里不再配对
        # 销账 —— 老实现正是在这里依赖 release 能认出同一个键，认不出就永久跑偏。
        r = resolve_key(ev)
        if r is None:
            return
        sc, ext, _ = r
        self.send(key_frame(sc | 0x80, ext))

    def release_all_modifiers(self):
        """把所有修饰键的 break 码打一遍。

        窗口失焦时松开 Shift、或某个 break 码在串口上丢了，客人机就会一直
        当作 Shift 按着。这里做一次无条件复位（重复的 break 对 8042 无害）。
        """
        for sc in (0x2A, 0x36, 0x1D, 0x38):
            self.send(key_frame(sc | 0x80))
        self.auto_shift.clear()
        self.auto_ctrl.clear()
        self.auto_alt.clear()
        self.pressed.clear()
        self.shift_down = False
        self.ctrl_down = False
        self.alt_down = False

    def send_cad(self):
        for sc in (0x1D, 0x38, 0x53):
            self.send(key_frame(sc, sc == 0x53))
        for sc in (0x53, 0x38, 0x1D):
            self.send(key_frame(sc | 0x80, sc == 0x53))

    def on_release_mods(self):
        self.release_all_modifiers()
        self.append_log("已补发 Shift/Ctrl/Alt 的释放码")

    def paste_text(self):
        win = tkinter.Toplevel(self.root)
        win.title("输入要发给 DOS 的文字")
        win.configure(bg="#101014")
        txt = tkinter.Text(win, width=60, height=4, bg="#181820", fg="#ddd")
        txt.pack(padx=8, pady=8)
        txt.focus_set()

        def go():
            text = txt.get("1.0", "end").rstrip("\n")
            win.destroy()
            self.type_text(text)

        tkinter.Button(win, text="发送", command=go).pack(pady=(0, 8))

    def type_text(self, text):
        """逐字符发 make/break，带间隔 —— 客人机一次轮询只吃一个扫描码。

        走的是和手动打字同一套「自带干净状态」的约定：上档字符 Shift 三连，
        未上档字符前面先补一个 Shift break。粘贴是最容易出现「Shift 残留」
        的场合（一次几十个字符，中途错一个就全歪），所以这里也不信任记账。
        """
        def worker():
            for ch in text:
                if ch in "\r\n":
                    self.send(key_frame(0x1C))
                    time.sleep(0.02)
                    self.send(key_frame(0x1C | 0x80))
                else:
                    mapped = char_to_key(ch)
                    if not mapped:
                        continue
                    sc, need_shift = mapped
                    if need_shift:
                        self.send(key_frame(0x2A))
                        time.sleep(0.02)
                    else:
                        # 左右 Shift 的 break 都要补（BIOS 是两个独立标志位）
                        self.send(key_frame(0x2A | 0x80))
                        self.send(key_frame(0x36 | 0x80))
                        time.sleep(0.02)
                    self.send(key_frame(sc))
                    time.sleep(0.02)
                    self.send(key_frame(sc | 0x80))
                    if need_shift:
                        time.sleep(0.02)
                        self.send(key_frame(0x2A | 0x80))
                time.sleep(0.03)
        threading.Thread(target=worker, daemon=True).start()

    def save_png(self):
        path = filedialog.asksaveasfilename(defaultextension=".png",
                                            filetypes=[("PNG", "*.png")])
        if not path:
            return
        with open(path, "wb") as fh:
            fh.write(raw_to_png(FB_W, FB_H, self.rnd.rgb, level=6))
        self.append_log("截图已存 %s" % path)

    def on_close(self):
        self.alive = False
        # 退出前把链路交回上电速率：否则板子停在 460800 上，下次别人按
        # 921600 连它时会"一句话都不应"，看起来像板子死了。
        if self.ser and self.baud != BOOT_BAUD:
            try:
                self.ser.write(cmd_frame(CMD_SETBAUD,
                                         struct.pack("<I", BOOT_BAUD)))
                time.sleep(0.12)
            except Exception:                        # noqa: BLE001
                pass
        if self.ser:
            self.ser.close()
        self.root.destroy()


# =====================================================================
# 无界面自检
# =====================================================================
def selftest(port, baud, seconds):
    port = pick_port(port)
    if not port:
        print("没有串口可用")
        return 1
    baud = negotiate_baud(port, baud, log=lambda s: print(s))
    print("打开 %s @ %d" % (port, baud))
    ser = SerialPort(port, baud)
    ser.write(cmd_frame(CMD_PING))
    ser.write(cmd_frame(CMD_FULLFRAME))
    parser = FrameParser()
    rnd = Renderer()
    counts = {}
    t0 = time.time()
    bytes_in = 0
    while time.time() - t0 < seconds:
        data = ser.read(8192)
        if not data:
            time.sleep(0.005)
            continue
        bytes_in += len(data)
        for ftype, seq, payload in parser.feed(data):
            counts[ftype] = counts.get(ftype, 0) + 1
            if ftype == T_VIDEO:
                rnd.apply_video(payload)
            elif ftype == T_HELLO and len(payload) >= HELLO_HDR:
                rnd.set_palette(payload[6:HELLO_HDR])
                print("HELLO:", payload[HELLO_HDR:].decode("ascii", "replace"))
            elif ftype == T_LOG:
                print("LOG  :", payload.decode("utf-8", "replace"))
            elif ftype == T_STATS and len(payload) >= 7:
                print("STATS: fps=%d guest=%dKB heap=%dKB mode=%d drop=%d" % (
                    payload[0], payload[2] | (payload[3] << 8),
                    payload[4] | (payload[5] << 8), payload[1], payload[6]))
    ser.close()
    dt = time.time() - t0
    print("--- %.1f 秒：%d 字节进 (%.1f KB/s)，帧统计 %s，坏CRC %d" % (
        dt, bytes_in, bytes_in / 1024 / dt, counts, parser.bad_crc))
    out = "screenshot.png"
    with open(out, "wb") as fh:
        fh.write(raw_to_png(FB_W, FB_H, rnd.rgb, level=6))
    print("画面已存", os.path.abspath(out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="C86S 上位机（串口画面 + 键盘）")
    ap.add_argument("--port", default=None, help="串口名，缺省自动找 CP210x")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--selftest", type=float, default=None,
                    help="无界面自检：收 N 秒后存 screenshot.ppm")
    ap.add_argument("--keydebug", action="store_true",
                    help="把每个按键事件和每次串口写打到 stderr（排查输入失效）")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest(args.port, args.baud, args.selftest)

    root = tkinter.Tk()
    App(root, args.port, args.baud, args.scale, keydebug=args.keydebug)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
