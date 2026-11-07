#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fat12.py -- 看 / 改 FAT12 软盘镜像（给 C86S 做启动盘用）

    python fat12.py list  <img>                 # 列根目录
    python fat12.py space <img>                 # 可用空间与簇统计
    python fat12.py put   <img> <host文件> [8.3名]  # 塞一个文件进镜像
    python fat12.py get   <img> <8.3名> <输出>      # 取出一个文件

只支持 512 字节/扇区、单分区、根目录 ≤512 项的 FAT12（360K / 1.44M 都在范围内）。
写操作只动根目录 + FAT + 数据区，不动引导扇区，所以不会破坏 DOS 的 BPB。
"""
import struct
import sys


class Fat12:
    def __init__(self, path, writable=False):
        self.path = path
        self.mode = "r+b" if writable else "rb"
        self.f = open(path, self.mode)
        self.data = bytearray(self.f.read())
        bs = self.data
        self.bps = struct.unpack_from("<H", bs, 11)[0]
        self.spc = bs[13]
        self.reserved = struct.unpack_from("<H", bs, 14)[0]
        self.nfats = bs[16]
        self.root_entries = struct.unpack_from("<H", bs, 17)[0]
        self.total = struct.unpack_from("<H", bs, 19)[0] or struct.unpack_from("<I", bs, 32)[0]
        self.spf = struct.unpack_from("<H", bs, 22)[0]     # sectors per FAT
        self.spt = struct.unpack_from("<H", bs, 24)[0]
        self.heads = struct.unpack_from("<H", bs, 26)[0]
        assert self.bps == 512, "只支持 512 字节扇区"
        self.root_start = (self.reserved + self.nfats * self.spf) * self.bps
        self.root_sectors = (self.root_entries * 32 + self.bps - 1) // self.bps
        self.data_start = self.root_start + self.root_sectors * self.bps
        self.cluster_bytes = self.spc * self.bps
        self.data_clusters = (self.total * self.bps - self.data_start) // self.cluster_bytes
        self.fat_start = self.reserved * self.bps

    # ---- FAT ----
    def entry(self, cluster):
        off = self.fat_start + cluster * 3 // 2
        v = self.data[off] | (self.data[off + 1] << 8)
        return (v >> 4) if (cluster & 1) else (v & 0x0FFF)

    def set_entry(self, cluster, value):
        off = self.fat_start + cluster * 3 // 2
        v = self.data[off] | (self.data[off + 1] << 8)
        if cluster & 1:
            v = (v & 0x000F) | ((value & 0x0FFF) << 4)
        else:
            v = (v & 0xF000) | (value & 0x0FFF)
        self.data[off] = v & 0xFF
        self.data[off + 1] = (v >> 8) & 0xFF
        # 两份 FAT 都写，DOS 才不会抱怨
        for n in range(1, self.nfats):
            o2 = off + n * self.spf * self.bps
            if o2 + 1 < len(self.data):
                self.data[o2] = self.data[off]
                self.data[o2 + 1] = self.data[off + 1]

    def cluster_offset(self, cluster):
        return self.data_start + (cluster - 2) * self.cluster_bytes

    # ---- 目录 ----
    def entries(self):
        out = []
        for i in range(self.root_entries):
            off = self.root_start + i * 32
            e = self.data[off:off + 32]
            if len(e) < 32 or e[0] == 0x00:
                break
            if e[0] == 0xE5:
                out.append((i, None, e))
                continue
            if e[11] == 0x0F:
                continue
            if e[11] & 0x08:                      # 卷标
                continue
            name = (e[0:8].decode("latin-1").rstrip() + "." +
                    e[8:11].decode("latin-1").rstrip()).rstrip(".")
            attr = e[11]
            out.append((i, name, e))
        return out

    def free_cluster(self):
        for c in range(2, self.data_clusters + 2):
            if self.entry(c) == 0:
                return c
        return None

    def space(self):
        free = sum(1 for c in range(2, self.data_clusters + 2) if self.entry(c) == 0)
        return free, self.data_clusters

    def find(self, name83):
        """按 8.3 名找目录项，返回 (槽位, 首簇, 长度) 或 None。"""
        stem, ext = (name83.rsplit(".", 1) + [""])[:2] if "." in name83 else (name83, "")
        stem = stem[:8].ljust(8).upper()
        ext = ext[:3].ljust(3).upper()
        for i, name, e in self.entries():
            if name is None:
                continue
            if e[0:8].decode("latin-1") == stem and e[8:11].decode("latin-1") == ext:
                return i, struct.unpack_from("<H", e, 26)[0], struct.unpack_from("<I", e, 28)[0]
        return None

    def delete(self, name83):
        """删掉一个文件：释放簇链 + 目录项标 0xE5。返回是否真的删了。"""
        hit = self.find(name83)
        if hit is None:
            return False
        slot, start, _size = hit
        c = start
        guard = 0
        while c and c < 0xFF8 and guard < self.data_clusters:
            nxt = self.entry(c)
            self.set_entry(c, 0)
            c = nxt
            guard += 1
        self.data[self.root_start + slot * 32] = 0xE5
        return True

    def put(self, host_path, name83=None, replace=False):
        raw = open(host_path, "rb").read()
        base = name83 or host_path.replace("\\", "/").split("/")[-1].upper()
        if replace:
            self.delete(base)
        if self.find(base) is not None:
            raise RuntimeError("%s 已经在镜像里了（要覆盖请加 --replace）" % base)
        if "." in base:
            stem, ext = base.rsplit(".", 1)
        else:
            stem, ext = base, ""
        stem = stem[:8].ljust(8)
        ext = ext[:3].ljust(3)

        need = (len(raw) + self.cluster_bytes - 1) // self.cluster_bytes
        avail, total = self.space()
        if need > avail:
            raise RuntimeError("空间不够：需要 %d 簇，只剩 %d 簇（共 %d）"
                               % (need, avail, total))

        # 找空目录项
        slot = None
        for i in range(self.root_entries):
            off = self.root_start + i * 32
            if self.data[off] == 0x00 or self.data[off] == 0xE5:
                slot = i
                break
        if slot is None:
            raise RuntimeError("根目录满了")

        first = None
        prev = None
        written = 0
        for _ in range(need):
            c = self.free_cluster()
            if c is None:
                raise RuntimeError("没有空闲簇")
            if first is None:
                first = c
            if prev is not None:
                self.set_entry(prev, c)
            prev = c
            self.set_entry(c, 0xFFF)
            chunk = raw[written:written + self.cluster_bytes]
            off = self.cluster_offset(c)
            self.data[off:off + len(chunk)] = chunk
            self.data[off + len(chunk):off + self.cluster_bytes] = b"\x00" * (
                self.cluster_bytes - len(chunk))
            written += len(chunk)

        e = bytearray(32)
        e[0:8] = stem.encode("latin-1")
        e[8:11] = ext.encode("latin-1")
        e[11] = 0x20
        struct.pack_into("<H", e, 26, first if first else 0)
        struct.pack_into("<I", e, 28, len(raw))
        off = self.root_start + slot * 32
        self.data[off:off + 32] = e
        return name83 or base, len(raw), need

    def get(self, name83):
        """取出一个文件的字节。找不到抛 KeyError。"""
        hit = self.find(name83)
        if hit is None:
            raise KeyError(name83)
        _slot, start, size = hit
        out = bytearray()
        c = start
        guard = 0
        while c and c < 0xFF8 and len(out) < size and guard < self.data_clusters:
            off = self.cluster_offset(c)
            out += self.data[off:off + self.cluster_bytes]
            c = self.entry(c)
            guard += 1
        return bytes(out[:size])

    def flush(self):
        self.f.seek(0)
        self.f.write(self.data)
        self.f.truncate()
        self.f.close()


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    cmd, path = argv[1], argv[2]
    if cmd == "list":
        img = Fat12(path)
        print("%s: %d KB, 簇=%d 字节, 根目录 %d 项"
              % (path, img.total * img.bps // 1024, img.cluster_bytes,
                 img.root_entries))
        for _, name, e in img.entries():
            if name is None:
                continue
            size = struct.unpack_from("<I", e, 28)[0]
            print("  %-14s %8d" % (name, size))
    elif cmd == "space":
        img = Fat12(path)
        free, total = img.space()
        print("空闲 %d / %d 簇 = %d KB 可用" %
              (free, total, free * img.cluster_bytes // 1024))
    elif cmd == "put":
        img = Fat12(path, writable=True)
        args = [a for a in argv[3:] if not a.startswith("--")]
        name, size, clusters = img.put(args[0], args[1] if len(args) > 1 else None,
                                       replace="--replace" in argv)
        img.flush()
        print("已写入 %s (%d 字节, %d 簇)" % (name, size, clusters))
    elif cmd == "del":
        img = Fat12(path, writable=True)
        if img.delete(argv[3].upper()):
            img.flush()
            print("已删除", argv[3].upper())
        else:
            print("镜像里没有", argv[3].upper())
            return 1
    elif cmd == "get":
        img = Fat12(path)
        target = argv[3].upper()
        for _, name, e in img.entries():
            if name and name == target:
                start = struct.unpack_from("<H", e, 26)[0]
                size = struct.unpack_from("<I", e, 28)[0]
                out = bytearray()
                c = start
                while c and c < 0xFF8 and len(out) < size:
                    off = img.cluster_offset(c)
                    out += img.data[off:off + img.cluster_bytes]
                    c = img.entry(c)
                open(argv[4], "wb").write(out[:size])
                print("已取出 %s (%d 字节) -> %s" % (name, size, argv[4]))
                return 0
        print("镜像里没有", target)
        return 1
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
