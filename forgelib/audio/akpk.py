"""forgelib/audio/akpk.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import paths
ACR_SOUND = os.path.join(os.path.dirname(paths.ACR), 'SoundData', 'pc')
AC3_SOUND = os.path.join(paths.AC3_SOURCE, 'sounddata', 'Pc')
AC3_SP_SOUND = os.path.join(paths.AC3_SINGLEPLAYER, 'SoundData', 'pc')

class Entry:
    __slots__ = ('kind', 'id', 'block', 'size', 'start', 'lang', 'offset')

    def __init__(self, kind, id_, block, size, start, lang):
        self.kind, self.id, self.block, self.size = (kind, id_, block, size)
        self.start, self.lang = (start, lang)
        self.offset = start * block

    def __repr__(self):
        return f'<{self.kind} {self.id:#x} @{self.offset:#x} +{self.size}>'

class Package:

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        with open(path, 'rb') as f:
            head = f.read(8)
            if head[:4] != b'AKPK':
                raise ValueError(f'{path}: not AKPK')
            self.header_size, = struct.unpack_from('<I', head, 4)
            h = f.read(self.header_size)
        self.version, lang_sz, banks_sz, streams_sz = struct.unpack_from('<4I', h, 0)
        if 16 + lang_sz + banks_sz + streams_sz == self.header_size:
            ext_sz, pos = (0, 16)
            self.tables = 3
        else:
            ext_sz, = struct.unpack_from('<I', h, 16)
            pos = 20
            self.tables = 4
            if pos + lang_sz + banks_sz + streams_sz + ext_sz != self.header_size:
                raise ValueError(f'{path}: header sizes do not add up')
        self.languages = self._langs(h[pos:pos + lang_sz])
        pos += lang_sz
        self.banks = self._lut(h[pos:pos + banks_sz], 'bank', False)
        pos += banks_sz
        self.streams = self._lut(h[pos:pos + streams_sz], 'stream', False)
        pos += streams_sz
        self.externals = self._lut(h[pos:pos + ext_sz], 'external', True) if ext_sz else []

    @staticmethod
    def _langs(b):
        n, = struct.unpack_from('<I', b, 0)
        out = {}
        for i in range(n):
            off, lid = struct.unpack_from('<II', b, 4 + 8 * i)
            end = off
            while b[end:end + 2] != b'\x00\x00':
                end += 2
            out[lid] = b[off:end].decode('utf-16-le')
        return out

    @staticmethod
    def _lut(b, kind, wide):
        if not b:
            return []
        n, = struct.unpack_from('<I', b, 0)
        if not n:
            return []
        step = (len(b) - 4) // n
        fmt = {(True, 24): '<QIIII', (False, 20): '<IIIII', (False, 24): '<IIQII'}[wide, step]
        return [Entry(kind, *struct.unpack_from(fmt, b, 4 + step * i)) for i in range(n)]

    def read(self, entry, limit=None):
        with open(self.path, 'rb') as f:
            f.seek(entry.offset)
            return f.read(entry.size if limit is None else min(limit, entry.size))

def packages(root):
    return [Package(os.path.join(root, n)) for n in sorted(os.listdir(root)) if n.endswith('.pck')]
if __name__ == '__main__':
    for game, root in (('ACR', ACR_SOUND), ('AC3MP', AC3_SOUND), ('AC3SP', AC3_SP_SOUND)):
        print(f'== {game}  {root}')
        for p in packages(root):
            langs = ','.join(sorted(set(p.languages.values())))
            print(f'  {p.name:26} tables={p.tables} banks={len(p.banks):5} streams={len(p.streams):6} externals={len(p.externals):6}  [{langs}]')
