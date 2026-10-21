"""forgelib/exe_classes.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import re
import struct
import sys

class PE:

    def __init__(self, path):
        self.path = path
        self.data = open(path, 'rb').read()
        pe = struct.unpack_from('<I', self.data, 60)[0]
        assert self.data[pe:pe + 4] == b'PE\x00\x00', 'not a PE'
        n_sections = struct.unpack_from('<H', self.data, pe + 6)[0]
        opt_size = struct.unpack_from('<H', self.data, pe + 20)[0]
        self.image_base = struct.unpack_from('<I', self.data, pe + 24 + 28)[0]
        self.sections = []
        o = pe + 24 + opt_size
        for i in range(n_sections):
            name = self.data[o:o + 8].split(b'\x00')[0].decode()
            vsize, va, rawsize, raw = struct.unpack_from('<IIII', self.data, o + 8)
            self.sections.append((name, va, vsize, raw, rawsize))
            o += 40

    def va(self, file_off):
        for name, va, vsize, raw, rawsize in self.sections:
            if raw <= file_off < raw + rawsize:
                return self.image_base + va + (file_off - raw)
        return None

    def off(self, va):
        rva = va - self.image_base
        for name, sva, vsize, raw, rawsize in self.sections:
            if sva <= rva < sva + max(vsize, rawsize):
                o = raw + (rva - sva)
                return o if o < len(self.data) else None
        return None

    def section_of(self, va):
        rva = va - self.image_base
        for name, sva, vsize, raw, rawsize in self.sections:
            if sva <= rva < sva + max(vsize, rawsize):
                return name
        return '?'
STRING_RE = re.compile(b'[ -~]{4,128}\\x00')

def strings(pe, pattern=None):
    out = {}
    for m in STRING_RE.finditer(pe.data):
        s = m.group()[:-1].decode('ascii')
        if pattern and (not pattern.search(s)):
            continue
        va = pe.va(m.start())
        if va is not None:
            out.setdefault(s, va)
    return out

def pointer_sites(pe, vas):
    want = {}
    for va in vas:
        want.setdefault(struct.pack('<I', va), va)
    sites = {}
    data = pe.data
    for needle, va in want.items():
        start = 0
        while True:
            i = data.find(needle, start)
            if i < 0:
                break
            if i % 4 == 0:
                sites.setdefault(va, []).append(i)
            start = i + 1
    return sites

def dump_around(pe, off, before=8, after=8):
    lo = max(0, off - before * 4)
    hi = min(len(pe.data), off + after * 4)
    words = []
    for p in range(lo, hi, 4):
        w = struct.unpack_from('<I', pe.data, p)[0]
        mark = '*' if p == off else ' '
        words.append((p, w, mark))
    return words

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "C:\\Program Files (x86)\\Ubisoft\\Assassin's Creed III\\AC3MP.exe"
    pat = re.compile(sys.argv[2]) if len(sys.argv) > 2 else re.compile('^(ACFE|AC2|AC3)Animation')
    pe = PE(path)
    print(f'{os.path.basename(path)}  base 0x{pe.image_base:X}  {len(pe.sections)} sections')
    found = strings(pe, pat)
    print(f'{len(found)} matching strings')
    sites = pointer_sites(pe, found.values())
    by_va = {v: k for k, v in found.items()}
    for va, offs in list(sites.items())[:6]:
        print(f'\n{by_va[va]}  string at 0x{va:X}')
        for o in offs[:2]:
            print(f'  pointer at file 0x{o:X} = va 0x{pe.va(o):X} ({pe.section_of(pe.va(o))})')
            for p, w, mark in dump_around(pe, o):
                note = ''
                s = by_va.get(w)
                if s:
                    note = f'  -> {s}'
                print(f'   {mark} +{p - o:+4d}  0x{w:08X}{note}')
    print(f'\n{len(sites)}/{len(found)} strings are pointed at')
    unpointed = [by_va[v] for v in found.values() if v not in sites]
    if unpointed:
        print('not pointed at:', ', '.join(sorted(unpointed)[:10]))
if __name__ == '__main__':
    main()
