#!/usr/bin/env python3
"""List or extract files from a Cemu .wua archive (ZArchive format).

    python wua_extract.py GAME.wua --list
    python wua_extract.py GAME.wua --list 0005000e10128800_v32/code
    python wua_extract.py GAME.wua 0005000e10128800_v32/code/Hawaii.rpx -o Hawaii.rpx

Needs the zstandard package: pip install zstandard
"""
import argparse
import os
import struct
import sys

import zstandard

FOOTER_SIZE = 144
MAGIC = 0x169F52D6
BLOCK_SIZE = 65536


class Wua:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.f.seek(0, 2)
        footer_pos = self.f.tell() - FOOTER_SIZE
        self.f.seek(footer_pos)
        footer = self.f.read(FOOTER_SIZE)
        if struct.unpack(">I", footer[-4:])[0] != MAGIC:
            sys.exit(f"{path} is not a .wua file")
        sections = [struct.unpack(">QQ", footer[i * 16:i * 16 + 16]) for i in range(6)]
        (self.data_off, _), offsets, names, tree = sections[0], sections[1], sections[2], sections[3]
        self.offset_records = self._read(*offsets)
        self.names = self._read(*names)
        self.tree = self._read(*tree)
        self.zstd = zstandard.ZstdDecompressor()

    def _read(self, off, size):
        self.f.seek(off)
        return self.f.read(size)

    def _name(self, off):
        if off == 0x7FFFFFFF:
            return ""
        length = self.names[off] & 0x7F
        if self.names[off] & 0x80:
            length |= self.names[off + 1] << 7
            off += 1
        return self.names[off + 1:off + 1 + length].decode("utf-8", "replace")

    def _entry(self, i):
        a, b, c, d, e = struct.unpack(">IIIHH", self.tree[i * 16:i * 16 + 16])
        # (is_file, name, start or offset low, count or size low, size high, offset high)
        return (a >> 31) & 1, self._name(a & 0x7FFFFFFF), b, c, d, e

    def _find(self, path):
        idx = 0
        for part in [p for p in path.strip("/").split("/") if p]:
            _, _, start, count, _, _ = self._entry(idx)
            for j in range(start, start + count):
                if self._entry(j)[1].lower() == part.lower():
                    idx = j
                    break
            else:
                return None
        return self._entry(idx)

    def listdir(self, path=""):
        entry = self._find(path)
        if entry is None or entry[0]:
            sys.exit(f"not a directory: {path}")
        return [self._entry(j)[1] for j in range(entry[2], entry[2] + entry[3])]

    def _block(self, n):
        rec = self.offset_records[(n // 16) * 40:(n // 16) * 40 + 40]
        base, *sizes = struct.unpack(">Q16H", rec)
        pos = base + sum(s + 1 for s in sizes[:n % 16])
        size = sizes[n % 16] + 1
        raw = self._read(self.data_off + pos, size)
        # blocks that did not shrink are stored uncompressed
        return raw if size == BLOCK_SIZE else self.zstd.decompress(raw, max_output_size=BLOCK_SIZE)

    def read(self, path):
        entry = self._find(path)
        if entry is None or not entry[0]:
            sys.exit(f"file not found: {path}")
        offset = entry[2] | (entry[5] << 32)
        size = entry[3] | (entry[4] << 32)
        out = bytearray()
        while len(out) < size:
            pos = offset + len(out)
            block = self._block(pos // BLOCK_SIZE)
            start = pos % BLOCK_SIZE
            out += block[start:start + size - len(out)]
        return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wua")
    ap.add_argument("path", nargs="?", default="", help="file or folder inside the archive")
    ap.add_argument("--list", action="store_true", help="list a folder instead of extracting")
    ap.add_argument("-o", "--output", help="where to write the extracted file")
    args = ap.parse_args()

    wua = Wua(args.wua)
    if args.list or not args.path:
        for name in wua.listdir(args.path):
            print(name)
        return
    data = wua.read(args.path)
    out = args.output or os.path.basename(args.path)
    with open(out, "wb") as f:
        f.write(data)
    print(f"wrote {out} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
