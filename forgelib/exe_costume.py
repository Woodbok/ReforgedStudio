"""forgelib/exe_costume.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import struct
import sys
from exe_patch import PE, rel32
SITE = 6682849
ORIGINAL = bytes.fromhex('837920007430')
OVERRIDE = 6682855
DONE = 6682903
CAVE = 19253568
CAVE_SIZE = 192
BITMAP = CAVE + 64

def cave_code():
    code = b'\x83y \x00'
    code += b'\x0f\x84' + rel32(CAVE + len(code) + 6, DONE)
    code += b'\x8bE\x08'
    code += b'\x0f\xa3\x05' + struct.pack('<I', BITMAP)
    code += b'\x0f\x83' + rel32(CAVE + len(code) + 6, OVERRIDE)
    code += b'\x8b\x03'
    code += b'\x83`$\x00'
    code += b'\xe9' + rel32(CAVE + len(code) + 5, DONE)
    assert len(code) <= BITMAP - CAVE
    return code + b'\xcc' * (BITMAP - CAVE - len(code))

def site_code():
    return b'\xe9' + rel32(SITE + 5, CAVE) + b'\x90'

def flagged(pe):
    if pe.read(SITE, 6) != site_code():
        return []
    bits = pe.read(BITMAP, 32)
    return [i for i in range(256) if bits[i // 8] >> i % 8 & 1]

def patch(pe, personas):
    site, cave = (pe.read(SITE, 6), pe.read(CAVE, CAVE_SIZE))
    if site == ORIGINAL:
        if cave != b'\xcc' * CAVE_SIZE:
            sys.exit(f'exe_costume: the cave 0x{CAVE:X} is not free INT3 padding')
        pe.write(CAVE, cave_code() + bytes(32))
        pe.write(SITE, site_code())
    elif site != site_code() or pe.read(CAVE, BITMAP - CAVE) != cave_code():
        sys.exit(f'exe_costume: 0x{SITE:X} / the cave hold something else: {site.hex()}')
    bits = bytearray(pe.read(BITMAP, 32))
    for p in personas:
        assert 0 <= p < 256, p
        bits[p // 8] |= 1 << p % 8
    pe.write(BITMAP, bytes(bits))
    return pe

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('exe', nargs='?')
    ap.add_argument('out', nargs='?')
    ap.add_argument('--persona', type=int, action='append', default=[])
    ap.add_argument('--check', metavar='EXE')
    a = ap.parse_args(argv)
    if a.check:
        print(f"{a.check}: costume adds to the outfit for {flagged(PE(open(a.check, 'rb').read())) or 'nobody'}")
        return 0
    if not (a.exe and a.out and a.persona):
        ap.error('IN_EXE OUT_EXE --persona N')
    pe = patch(PE(open(a.exe, 'rb').read()), a.persona)
    with open(a.out, 'wb') as fh:
        fh.write(bytes(pe.data))
    print(f'{a.out}: costume adds to the outfit for {flagged(pe)}')
    return 0
if __name__ == '__main__':
    sys.exit(main())
