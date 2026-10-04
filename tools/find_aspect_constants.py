#!/usr/bin/env python3
"""Find the 16:9 aspect ratio constants in a Wii U executable (.rpx) and write a Cemu patches.txt.

Scans .rodata for the float 1.7777778 and keeps the ones the game code actually loads
(a `lis` followed by `lfs`, `lfd`, `addi` or `lwz` with the same register).

    python find_aspect_constants.py Hawaii.rpx
    python find_aspect_constants.py Hawaii.rpx --patches patches.txt --checksum 0xfc370b5b --name MyGame_Ultrawide

Read the checksum from Cemu's log.txt after starting the game once:
    Loaded module 'hawaii' with checksum 0xfc370b5b
"""
import argparse
import bisect
import struct
import sys
import zlib

SHF_RPL_ZLIB = 0x08000000
LOADS = {48: "lfs", 50: "lfd", 14: "addi", 32: "lwz"}
SEARCH_WINDOW = 40  # instructions after the lis


class Rpx:
    def __init__(self, data):
        if data[:4] != b"\x7fELF":
            sys.exit("not an RPX/ELF file")
        self.data = data
        shoff, = struct.unpack(">I", data[0x20:0x24])
        shentsize, shnum, shstrndx = struct.unpack(">HHH", data[0x2E:0x34])
        self.sections = [struct.unpack(">10I", data[shoff + i * shentsize:shoff + i * shentsize + 40])
                         for i in range(shnum)]
        names = self.raw(shstrndx)
        self.index = {names[s[0]:names.index(b"\0", s[0])].decode(): i for i, s in enumerate(self.sections)}

    def raw(self, i):
        _, _, flags, _, off, size = self.sections[i][:6]
        b = self.data[off:off + size]
        if flags & SHF_RPL_ZLIB and len(b) >= 4:
            b = zlib.decompress(b[4:])  # first 4 bytes hold the inflated size
        return b

    def section(self, name):
        i = self.index.get(name)
        if i is None:
            sys.exit(f"section {name} not found")
        return self.sections[i][3], self.raw(i)

    def functions(self):
        """Sorted (address, name) of function symbols, empty if the file is stripped."""
        if ".symtab" not in self.index:
            return []
        i = self.index[".symtab"]
        table, strings = self.raw(i), self.raw(self.sections[i][6])  # sh_link points at the string table
        out = []
        for k in range(0, len(table) - 15, 16):
            name, value, _, info, _, _ = struct.unpack(">IIIBBH", table[k:k + 16])
            if info & 0xF == 2 and value:  # STT_FUNC
                out.append((value, strings[name:strings.find(b"\0", name)].decode("latin-1")))
        return sorted(out)


def find_constants(rpx, ratio):
    ro_addr, ro = rpx.section(".rodata")
    pattern = struct.pack(">f", ratio)
    found = []
    i = ro.find(pattern)
    while i != -1:
        if i % 4 == 0:
            found.append(ro_addr + i)
        i = ro.find(pattern, i + 1)
    return found


def find_references(rpx, constants):
    text_addr, text = rpx.section(".text")
    words = struct.unpack(">%dI" % (len(text) // 4), text[:len(text) // 4 * 4])
    by_high = {}
    for c in constants:
        lo = c & 0xFFFF
        high = ((c >> 16) + (1 if lo & 0x8000 else 0)) & 0xFFFF  # @ha: lo is sign-extended
        by_high.setdefault(high, []).append(c)
    refs = {c: [] for c in constants}
    for j, w in enumerate(words):
        if w >> 26 != 15 or (w >> 16) & 31 != 0 or w & 0xFFFF not in by_high:  # lis rD, high
            continue
        reg = (w >> 21) & 31
        for k in range(1, SEARCH_WINDOW):
            if j + k >= len(words):
                break
            w2 = words[j + k]
            if w2 >> 26 in LOADS and (w2 >> 16) & 31 == reg:
                lo = w2 & 0xFFFF
                addr = (((w & 0xFFFF) << 16) + (lo - 0x10000 if lo & 0x8000 else lo)) & 0xFFFFFFFF
                if addr in refs:
                    refs[addr].append(text_addr + (j + k) * 4)
    return refs


def parse_ratio(text):
    if "/" in text:
        a, b = text.split("/")
        return float(a) / float(b)
    return float(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rpx")
    ap.add_argument("--ratio", default="16/9", help="aspect ratio to look for (default 16/9)")
    ap.add_argument("--all", action="store_true", help="also patch constants no code reference was found for")
    ap.add_argument("--patches", help="write a Cemu patches.txt to this path")
    ap.add_argument("--checksum", help="module checksum from Cemu's log.txt, for moduleMatches")
    ap.add_argument("--name", default="Ultrawide", help="patch group name")
    args = ap.parse_args()

    rpx = Rpx(open(args.rpx, "rb").read())
    ratio = parse_ratio(args.ratio)
    constants = find_constants(rpx, ratio)
    refs = find_references(rpx, constants)
    funcs = rpx.functions()
    starts = [f[0] for f in funcs]

    def owner(addr):
        j = bisect.bisect_right(starts, addr) - 1
        return funcs[j][1] if j >= 0 else ""

    used = [c for c in constants if refs[c]]
    print(f"{len(constants)} constants equal to {ratio:.7f} in .rodata, {len(used)} loaded by code")
    for c in constants:
        names = sorted({owner(a) for a in refs[c]} - {""})
        print(f"0x{c:08x}  {len(refs[c]):3d} refs  {', '.join(names)[:120]}")

    if args.patches:
        if not args.checksum:
            sys.exit("--patches needs --checksum (see Cemu's log.txt)")
        chosen = constants if args.all else used
        lines = [f"[{args.name}]", f"moduleMatches = {args.checksum}", "",
                 f"# Every {args.ratio} float in .rodata that the code loads, set to the target aspect ratio."]
        lines += [f"0x{c:08x} = .float $aspect" for c in chosen]
        with open(args.patches, "w", newline="\r\n") as f:
            f.write("\n".join(lines) + "\n")
        print(f"wrote {args.patches} with {len(chosen)} patches")


if __name__ == "__main__":
    main()
