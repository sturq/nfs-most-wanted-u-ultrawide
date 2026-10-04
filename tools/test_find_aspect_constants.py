"""Self-check for find_aspect_constants.py on a tiny hand-built RPX. Run: python test_find_aspect_constants.py"""
import os
import struct
import subprocess
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from find_aspect_constants import Rpx, find_constants, find_references  # noqa: E402

RATIO = struct.pack(">f", 16 / 9)
RO_ADDR, TEXT_ADDR = 0x10000000, 0x02000000


def lis(rd, imm):
    return (15 << 26) | (rd << 21) | (imm & 0xFFFF)


def lfs(frd, ra, d):
    return (48 << 26) | (frd << 21) | (ra << 16) | (d & 0xFFFF)


def build_rpx(compress_rodata):
    ro = bytearray(0x8010)
    ro[0x4:0x8] = RATIO        # 0x10000004, loaded by func_a
    ro[0x8:0xC] = RATIO        # 0x10000008, never loaded
    ro[0x8004:0x8008] = RATIO  # 0x10008004, low half has bit 15 set, so lis uses 0x1001
    text = struct.pack(">6I", lis(3, 0x1000), lfs(1, 3, 4), 0x60000000,
                       lis(4, 0x1001), 0x60000000, lfs(2, 4, 0x8004 - 0x10000))
    strtab = b"\0func_a\0func_b\0"
    symtab = bytes(16) + struct.pack(">IIIBBH", 1, TEXT_ADDR, 8, 0x12, 0, 1) \
        + struct.pack(">IIIBBH", 8, TEXT_ADDR + 12, 12, 0x12, 0, 1)
    shstr = b"\0.text\0.rodata\0.symtab\0.strtab\0.shstrtab\0"
    rodata_flags = 0
    if compress_rodata:
        ro = struct.pack(">I", len(ro)) + zlib.compress(bytes(ro))
        rodata_flags = 0x08000000
    # name offset, type, flags, addr, data, link
    secs = [(0, 0, 0, 0, b"", 0), (1, 1, 6, TEXT_ADDR, text, 0), (7, 1, 2 | rodata_flags, RO_ADDR, bytes(ro), 0),
            (15, 2, 0, 0, symtab, 4), (23, 3, 0, 0, strtab, 0), (31, 3, 0, 0, shstr, 0)]
    body, offsets = b"", []
    for s in secs:
        offsets.append(0x34 + len(body))
        body += s[4]
    shoff = 0x34 + len(body)
    header = b"\x7fELF" + bytes(12) + bytes(16) + struct.pack(">I", shoff) + bytes(10) + struct.pack(">HHH", 40, len(secs), 5)
    table = b"".join(struct.pack(">10I", s[0], s[1], s[2], s[3], o, len(s[4]), s[5], 0, 4, 0) for s, o in zip(secs, offsets))
    return header + body + table


def check(compress):
    rpx = Rpx(build_rpx(compress))
    consts = find_constants(rpx, 16 / 9)
    assert consts == [0x10000004, 0x10000008, 0x10008004], consts
    refs = find_references(rpx, consts)
    assert refs[0x10000004] == [TEXT_ADDR + 4], refs
    assert refs[0x10000008] == [], refs
    assert refs[0x10008004] == [TEXT_ADDR + 20], refs
    assert [n for _, n in rpx.functions()] == ["func_a", "func_b"]


def check_cli():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.rpx")
        open(path, "wb").write(build_rpx(False))
        out = os.path.join(d, "patches.txt")
        tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "find_aspect_constants.py")
        subprocess.run([sys.executable, tool, path, "--patches", out, "--checksum", "0x12345678"], check=True,
                       capture_output=True)
        text = open(out).read()
        assert "moduleMatches = 0x12345678" in text and text.count(".float $aspect") == 2, text


if __name__ == "__main__":
    check(compress=False)
    check(compress=True)
    check_cli()
    print("ok")
