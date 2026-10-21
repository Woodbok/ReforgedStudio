"""forgelib/exe_patch.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
PRISTINE = os.path.join(os.path.dirname(paths.AC3_PRISTINE), 'AC3MP.exe')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
SHIPPED = [75, 76, 77, 78, 80, 81, 82, 83, 84, 85, 86, 87, 88, 90, 91, 93, 94, 95, 96]
FREE = [79, 89, 92]
TABLE_VA = 22421536
MOV_EBX = 8101035
CMP_EDI = 8101067
MASK_JZ = 8101049
CAVE_RUN = (19223571, 237)
CAVE_VA = 19223572
MAX_ENTRIES = 32
FILTER_LAST_CMP = 6732530
FILTER_ELSE = 6732535
FILTER_ELSE_REST = 6732541
FILTER_USABLE = 6732556
FILTER_CAVE = 19223712
ELSE_BYTES = b'\x8bM\xf8\x8dE\xfc'
SWITCHES = [(6300416, 6301046, 6301040), (6302373, 6303478, 6303010), (6361856, 6362765, 6361871), (6697118, 6697741, 6697704), (7124971, 7125263, 7125028), (6301178, 6301966, 6301341), (6301341, 6302054, 6301549), (6301549, 6302142, 6301757), (6301757, 6302230, 6301172)]
HEAD_DEFAULTS = 19651760
HEAD_ROW = 36

class PE:

    def __init__(self, data):
        self.data = bytearray(data)
        self.pe = struct.unpack_from('<I', data, 60)[0]
        n = struct.unpack_from('<H', data, self.pe + 6)[0]
        opt = struct.unpack_from('<H', data, self.pe + 20)[0]
        self.base = struct.unpack_from('<I', data, self.pe + 24 + 28)[0]
        self.sections = []
        for i in range(n):
            o = self.pe + 24 + opt + 40 * i
            vsize, va, rsize, rptr = struct.unpack_from('<IIII', data, o + 8)
            self.sections.append((data[o:o + 8].rstrip(b'\x00').decode(), self.base + va, vsize, rsize, rptr))
        self.security_dir = self.pe + 24 + 96 + 4 * 8
        self.touched = set()

    def offset(self, va, n=1):
        for name, sva, vsize, rsize, rptr in self.sections:
            if sva <= va and va + n <= sva + min(vsize, rsize):
                return rptr + va - sva
        raise ValueError(f'VA 0x{va:X} is not file-backed')

    def read(self, va, n):
        o = self.offset(va, n)
        return bytes(self.data[o:o + n])

    def write(self, va, blob):
        o = self.offset(va, len(blob))
        self.data[o:o + len(blob)] = blob
        self.touched |= set(range(o, o + len(blob)))

def expect(pe, va, want, what):
    got = pe.read(va, len(want))
    if got != want:
        sys.exit(f'{what} at 0x{va:X}: expected {want.hex()}, found {got.hex()} - not the exe this patch was written for')

def rel32(frm_next, to):
    return struct.pack('<i', to - frm_next)

def filter_code(new_ids):
    code, va = (bytearray(), FILTER_CAVE)
    for pid in new_ids:
        assert 0 < pid < 128
        code += b'\x83\xf8' + bytes([pid])
        code += b'\x0f\x84' + rel32(va + len(code) + 6, FILTER_USABLE)
    code += ELSE_BYTES
    code += b'\xe9' + rel32(va + len(code) + 5, FILTER_ELSE_REST)
    return bytes(code)

def patch(ids, no_mask_test, front=False, personas=(), extra=()):
    ids = list(ids) + [n for n, _d in personas if n not in ids] + [e for e in extra if e not in ids]
    added = [i for i in ids if i not in SHIPPED]
    table = added + SHIPPED if front else SHIPPED + added
    if len(table) > MAX_ENTRIES:
        sys.exit(f"{len(table)} entries; the walker's mask is 32 bits")
    for new, donor in personas:
        if new not in FREE or donor not in SHIPPED:
            sys.exit(f"--persona {new}:{donor}: the new id must be one of {FREE} (inside the switches' range) and the donor a shipped persona")
    pe = PE(open(PRISTINE, 'rb').read())
    original = bytes(pe.data)
    expect(pe, TABLE_VA, struct.pack('<19I', *SHIPPED), 'persona table')
    expect(pe, MOV_EBX, b'\xbb' + struct.pack('<I', TABLE_VA), 'MOV EBX, table')
    expect(pe, CMP_EDI, b'\x83\xff\x13', 'CMP EDI, 19')
    expect(pe, MASK_JZ, b't\x0c', 'JZ over the append')
    expect(pe, CAVE_RUN[0] - 1, b'\xc3', 'RET before the cave')
    expect(pe, CAVE_RUN[0], b'\xcc' * CAVE_RUN[1], 'INT3 cave')
    assert CAVE_VA + 4 * MAX_ENTRIES <= FILTER_CAVE
    pe.write(CAVE_VA, struct.pack(f'<{len(table)}I', *table))
    pe.write(MOV_EBX + 1, struct.pack('<I', CAVE_VA))
    pe.write(CMP_EDI + 2, bytes([len(table)]))
    if no_mask_test:
        pe.write(MASK_JZ, b'\x90\x90')
    if personas or extra:
        new_ids = [n for n, _d in personas] + list(extra)
        for e in extra:
            if 75 <= e <= 96 or e in SHIPPED:
                sys.exit(f'--extra {e}: inside 75..96 use --persona NEW:DONOR')
        expect(pe, FILTER_LAST_CMP, b'\x83\xf8`t\x15', "the filter's last test")
        expect(pe, FILTER_ELSE, ELSE_BYTES + b'P', "the filter's else branch")
        expect(pe, FILTER_USABLE, b'\x8bM\xf8\x8dE\xfcP', "the filter's usable branch")
        code = filter_code(new_ids)
        assert FILTER_CAVE + len(code) <= CAVE_RUN[0] + CAVE_RUN[1], len(code)
        pe.write(FILTER_CAVE, code)
        pe.write(FILTER_ELSE, b'\xe9' + rel32(FILTER_ELSE + 5, FILTER_CAVE) + b'\x90')
        for site, table_va, default in SWITCHES if personas else ():
            entries = struct.unpack('<22I', pe.read(table_va, 88))
            for new, donor in personas:
                if entries[new - 75] != default:
                    sys.exit(f'switch 0x{site:X}: slot {new} is not the default case')
                pe.write(table_va + 4 * (new - 75), struct.pack('<I', entries[donor - 75]))
        for new, donor in personas:
            pe.write(HEAD_DEFAULTS + HEAD_ROW * (new - 75), pe.read(HEAD_DEFAULTS + HEAD_ROW * (donor - 75), HEAD_ROW))
    cert_off, cert_size = struct.unpack_from('<II', pe.data, pe.security_dir)
    assert cert_off + cert_size == len(pe.data), (hex(cert_off), cert_size, len(pe.data))
    struct.pack_into('<II', pe.data, pe.security_dir, 0, 0)
    pe.touched |= set(range(pe.security_dir, pe.security_dir + 8))
    del pe.data[cert_off:]
    stray = [i for i in range(len(pe.data)) if pe.data[i] != original[i] and i not in pe.touched]
    assert not stray, [hex(i) for i in stray[:8]]
    return (bytes(pe.data), table)

def describe(path):
    pe = PE(open(path, 'rb').read())
    ptr = struct.unpack('<I', pe.read(MOV_EBX + 1, 4))[0]
    n = pe.read(CMP_EDI + 2, 1)[0]
    ids = list(struct.unpack(f'<{n}I', pe.read(ptr, 4 * n)))
    mask = 'intact' if pe.read(MASK_JZ, 2) == b't\x0c' else 'NOPped (every entry appended)'
    signed = 'signed' if struct.unpack_from('<II', pe.data, pe.security_dir)[1] else 'no certificate'
    patched_filter = pe.read(FILTER_ELSE, 1) == b'\xe9'
    usable = []
    if patched_filter:
        code = pe.read(FILTER_CAVE, 96)
        k = 0
        while code[k:k + 2] == b'\x83\xf8':
            usable.append(code[k + 2])
            k += 9
    rerouted = {}
    for site, table_va, default in SWITCHES:
        entries = struct.unpack('<22I', pe.read(table_va, 88))
        for pid in FREE:
            if entries[pid - 75] != default:
                donor = [75 + i for i, e in enumerate(entries) if e == entries[pid - 75] and 75 + i != pid]
                rerouted.setdefault(pid, set()).update(donor)
    heads = {}
    for pid in FREE:
        row = pe.read(HEAD_DEFAULTS + HEAD_ROW * (pid - 75), HEAD_ROW)
        same = [75 + i for i in range(22) if 75 + i != pid and pe.read(HEAD_DEFAULTS + HEAD_ROW * i, HEAD_ROW) == row]
        if same:
            heads[pid] = same
    state = 'pristine' if (ptr, n) == (TABLE_VA, 19) and mask == 'intact' and (not patched_filter) and (not rerouted) and (79 not in heads) else 'PATCHED'
    print(f'{path}\n  {state}: session table at 0x{ptr:X}, {n} entries {ids}\n  mask test {mask}; {signed}')
    print(f"  usability filter: {('also accepts ' + str(usable) if patched_filter else 'the stock 19')}")
    print(f"  switches: {', '.join((f'{p} as {sorted(d)}' for p, d in rerouted.items())) or 'stock'}")
    print(f"  head-set defaults: {', '.join((f'{p} as {d}' for p, d in heads.items())) or 'stock'}")
    import exe_skin_cache
    print(f"  skin-cache check (exe_skin_cache.py): {('present' if exe_skin_cache.is_patched(pe) else 'ABSENT')}")

def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('build', nargs='?')
    p.add_argument('--ids', type=int, nargs='*', default=[])
    p.add_argument('--persona', action='append', default=[], metavar='NEW:DONOR', help='a new persona id and the shipped one it is treated like (79:87)')
    p.add_argument('--extra', type=int, action='append', default=[], metavar='ID', help='a new id outside 75..96 (session table + usability filter only)')
    p.add_argument('--no-mask-test', action='store_true')
    p.add_argument('--front', action='store_true', help='new ids first in the table, not last')
    p.add_argument('--no-skin-cache', action='store_true', help="leave out exe_skin_cache.py's check (only to reproduce X4 and older exes: without it a new persona can hang greyed-out on selection - HANDOFF, 'The spawn hang, caught')")
    p.add_argument('--check', metavar='PATH')
    a = p.parse_args()
    if a.check:
        describe(a.check)
        return 0
    if not a.build:
        p.error('give a build name, or --check PATH')
    personas = [tuple((int(x) for x in s.split(':'))) for s in a.persona]
    data, table = patch(a.ids, a.no_mask_test, a.front, personas, a.extra)
    if (personas or a.extra or a.ids) and (not a.no_skin_cache):
        import exe_skin_cache
        pe = PE(data)
        exe_skin_cache.patch(pe)
        data = bytes(pe.data)
    dest = os.path.join(OUT, a.build)
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, 'AC3MP.exe')
    with open(path, 'wb') as fh:
        fh.write(data)
    print(f"{len(table)} personas {table}, mask test {('removed' if a.no_mask_test else 'kept')} -> {path}")
    describe(path)
    return 0
if __name__ == '__main__':
    sys.exit(main())
