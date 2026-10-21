"""forgelib/audio/codebooks.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
HERE = os.path.dirname(os.path.abspath(__file__))
STANDARD = os.path.join(HERE, 'tools', 'packed_codebooks.bin')
AOTUV = os.path.join(HERE, 'tools', 'packed_codebooks_aoTuV_603.bin')

def library(path):
    d = open(path, 'rb').read()
    table = struct.unpack_from('<I', d, len(d) - 4)[0]
    offs = list(struct.unpack_from(f'<{(len(d) - table) // 4}I', d, table))
    return [d[offs[i]:offs[i + 1]] for i in range(len(offs) - 1)]

def standard_to_aotuv():
    std, aot = (library(STANDARD), library(AOTUV))
    where = {}
    for i, cb in enumerate(aot):
        where.setdefault(cb, i)
    return ({i: where[cb] for i, cb in enumerate(std) if cb in where}, len(std), len(aot))
if __name__ == '__main__':
    m, n_std, n_aot = standard_to_aotuv()
    print(f'standard {n_std} codebooks, aoTuV {n_aot}; {len(m)} standard codebooks exist identically in aoTuV')

class Bits:

    def __init__(self, b, pos=0):
        self.b, self.pos = (b, pos * 8)

    def read(self, n):
        v = 0
        for i in range(n):
            byte = self.b[self.pos >> 3]
            v |= (byte >> (self.pos & 7) & 1) << i
            self.pos += 1
        return v

def setup_codebook_ids(wem):
    pos, cs = (12, {})
    while pos + 8 <= len(wem):
        tag, size = (wem[pos:pos + 4], struct.unpack_from('<I', wem, pos + 4)[0])
        cs[tag] = (pos + 8, size)
        pos += 8 + size + (size & 1)
    if b'vorb' in cs:
        vorb = cs[b'vorb'][0]
    else:
        vorb = cs[b'fmt '][0] + 24
    setup_off = struct.unpack_from('<I', wem, vorb + 16)[0]
    data = cs[b'data'][0]
    p = data + setup_off + 2
    r = Bits(wem, p)
    n = r.read(8) + 1
    return [r.read(10) for _ in range(n)]
