"""forgelib/sp/forge27.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
from collections import namedtuple
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import datafile
import lzo1x
from forge import MAGIC, NONE64, UnsupportedForge
VERSION = 27
DESCRIPTOR_SIZE = 48
ENTRY_SIZE = 20
META_SIZE = 192
Record = namedtuple('Record', 'index name uid timestamp offset size meta_size meta_hash next_index prev_index')

class Forge27:

    def __init__(self, path):
        self.path = path
        self._fh = open(path, 'rb')
        head = self._fh.read(32)
        if head[:9] != MAGIC:
            raise UnsupportedForge(f'{path}: not a scimitar archive')
        self.version = struct.unpack_from('<I', head, 9)[0]
        if self.version != VERSION:
            raise UnsupportedForge(f'{path}: version {self.version}, this reader handles 27')
        self.index_offset = struct.unpack_from('<Q', head, 13)[0]
        self.records = list(self._read_index())

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __len__(self):
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    def _read_index(self):
        self._fh.seek(self.index_offset)
        pre = self._fh.read(44)
        self.file_count = struct.unpack_from('<I', pre, 0)[0]
        self.per_chunk = struct.unpack_from('<I', pre, 28)[0]
        descriptor = struct.unpack_from('<Q', pre, 36)[0]
        seen = set()
        while descriptor not in (NONE64, 0):
            if descriptor in seen:
                raise UnsupportedForge(f'{self.path}: index chunk loop')
            seen.add(descriptor)
            self._fh.seek(descriptor)
            d = self._fh.read(DESCRIPTOR_SIZE)
            count = struct.unpack_from('<I', d, 0)[0]
            entry_table = struct.unpack_from('<Q', d, 8)[0]
            next_chunk = struct.unpack_from('<Q', d, 16)[0]
            first = struct.unpack_from('<I', d, 24)[0]
            meta_table = struct.unpack_from('<Q', d, 32)[0]
            self._fh.seek(entry_table)
            entries = self._fh.read(count * ENTRY_SIZE)
            self._fh.seek(meta_table)
            metas = self._fh.read(count * META_SIZE)
            if len(entries) < count * ENTRY_SIZE or len(metas) < count * META_SIZE:
                raise UnsupportedForge(f'{self.path}: truncated index chunk at 0x{descriptor:X}')
            for i in range(count):
                offset, uid, size = struct.unpack_from('<QQI', entries, i * ENTRY_SIZE)
                m = i * META_SIZE
                meta_size = struct.unpack_from('<I', metas, m)[0]
                meta_hash = struct.unpack_from('<Q', metas, m + 4)[0]
                nxt, prv = struct.unpack_from('<II', metas, m + 28)
                stamp = struct.unpack_from('<I', metas, m + 40)[0]
                name = metas[m + 44:m + META_SIZE].split(b'\x00')[0].decode('latin-1')
                yield Record(first + i, name, uid, stamp, offset, size, meta_size, meta_hash, nxt, prv)
            descriptor = next_chunk

    def raw(self, record):
        self._fh.seek(record.offset)
        return self._fh.read(record.size)

    def read(self, record):
        return decompress(self.raw(record))

def dependencies(payload):
    h = struct.unpack_from('<I', payload, 0)[0]
    n = struct.unpack_from('<H', payload, 6)[0] if h >= 4 else 0
    return [struct.unpack_from('<QBH', payload, 8 + i * 11) for i in range(n)]

def chunks(payload):
    o = 4 + struct.unpack_from('<I', payload, 0)[0]
    while o + datafile.CHUNK_HEADER_SIZE <= len(payload):
        magic, version = struct.unpack_from('<II', payload, o)
        if magic != datafile.MAGIC or version != datafile.VERSION:
            raise datafile.DatafileError(f'bad chunk header at {o}: 0x{magic:08X} 0x{version:08X}')
        codec = payload[o + 10]
        block_size = struct.unpack_from('<I', payload, o + 11)[0]
        if not block_size & 2147483648:
            raise datafile.DatafileError(f'chunk at {o} has no block table')
        q = o + datafile.CHUNK_HEADER_SIZE
        count = struct.unpack_from('<H', payload, q)[0]
        q += 2
        blocks = []
        for i in range(count):
            v = struct.unpack_from('<I', payload, q + i * 4)[0]
            blocks.append((v & 65535, v >> 16))
        q += count * 4
        yield (o, codec, blocks, q)
        o = q + sum((4 + c for _, c in blocks))

def decompress(payload):
    out = bytearray()
    for _, codec, blocks, q in chunks(payload):
        for raw, comp in blocks:
            q += 4
            data = payload[q:q + comp]
            if raw == comp:
                out += data
            elif codec in (0, 1):
                decoded, used = lzo1x.decompress(data, raw)
                if used != comp:
                    raise datafile.DatafileError(f'block at {q} consumed {used} of {comp}')
                out += decoded
            elif codec == 2:
                used = datafile._lzo2a(data, raw, out)
                if used != comp:
                    raise datafile.DatafileError(f'block at {q} consumed {used} of {comp}')
            else:
                raise datafile.DatafileError(f'codec {codec} not supported')
            q += comp
    return bytes(out)
if __name__ == '__main__':
    with Forge27(sys.argv[1]) as f:
        print(f'{f.path}: {len(f)} files (header says {f.file_count}), {f.per_chunk} per chunk')
        for r in f.records[:int(sys.argv[2]) if len(sys.argv) > 2 else 20]:
            print(f'  {r.index:6d} 0x{r.uid:016X} {r.size:10d} @0x{r.offset:X} {r.name}')
