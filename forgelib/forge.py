"""forgelib/forge.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
from collections import namedtuple
MAGIC = b'scimitar\x00'
SUPPORTED_VERSIONS = frozenset({25, 26})
DESCRIPTOR_OFFSET = 40
DESCRIPTOR_SIZE = 48
NONE64 = 18446744073709551615
ENTRY_SIZE = 16
META_SIZE = 188
PAYLOAD_HEADER_SIZE = 440
FileRecord = namedtuple('FileRecord', 'index name uid timestamp offset size meta_size next_index prev_index')

class UnsupportedForge(Exception):
    pass

class Forge:

    def __init__(self, path):
        self.path = path
        self._fh = open(path, 'rb')
        head = self._fh.read(32)
        if head[:9] != MAGIC:
            raise UnsupportedForge(f'{path}: not a scimitar archive')
        self.version = struct.unpack_from('<I', head, 9)[0]
        if self.version not in SUPPORTED_VERSIONS:
            raise UnsupportedForge(f'{path}: version {self.version}, this reader handles {sorted(SUPPORTED_VERSIONS)}')
        self.index_offset = struct.unpack_from('<Q', head, 13)[0]
        self.records = list(self._read_index())

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read_index(self):
        self._fh.seek(self.index_offset)
        self.file_count = struct.unpack_from('<I', self._fh.read(4), 0)[0]
        descriptor = self.index_offset + DESCRIPTOR_OFFSET
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
            base_index = first
            self._fh.seek(entry_table)
            entries = self._fh.read(count * ENTRY_SIZE)
            self._fh.seek(meta_table)
            metas = self._fh.read(count * META_SIZE)
            if len(entries) < count * ENTRY_SIZE or len(metas) < count * META_SIZE:
                raise UnsupportedForge(f'{self.path}: truncated index chunk at 0x{descriptor:X}')
            for i in range(count):
                offset, _unk, size = struct.unpack_from('<QII', entries, i * ENTRY_SIZE)
                m = i * META_SIZE
                meta_size = struct.unpack_from('<I', metas, m)[0]
                uid = struct.unpack_from('<Q', metas, m + 4)[0]
                nxt = struct.unpack_from('<I', metas, m + 28)[0]
                prv = struct.unpack_from('<I', metas, m + 32)[0]
                stamp = struct.unpack_from('<I', metas, m + 40)[0]
                raw_name = metas[m + 44:m + 160]
                name = raw_name.split(b'\x00')[0].decode('latin-1')
                yield FileRecord(base_index + i, name, uid, stamp, offset, size, meta_size, nxt, prv)
            descriptor = next_chunk

    def read(self, record):
        self._fh.seek(record.offset)
        head = self._fh.read(PAYLOAD_HEADER_SIZE)
        if head[:8] != b'FILEDATA':
            raise UnsupportedForge(f'{self.path}: no FILEDATA header at 0x{record.offset:X} for {record.index} ({record.name!r})')
        return self._fh.read(record.size)

    def payload_name(self, record):
        self._fh.seek(record.offset + 8)
        return self._fh.read(128).split(b'\x00')[0].decode('latin-1')

    def __len__(self):
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

def open_forge(path):
    return Forge(path)

def list_archives(directory):
    return sorted((os.path.join(directory, f) for f in os.listdir(directory) if f.lower().endswith('.forge')))
