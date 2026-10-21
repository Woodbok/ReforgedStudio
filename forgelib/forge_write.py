"""forgelib/forge_write.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
from collections import namedtuple
from forge import Forge, ENTRY_SIZE, META_SIZE, PAYLOAD_HEADER_SIZE, MAGIC, DESCRIPTOR_OFFSET, DESCRIPTOR_SIZE, NONE64
INDEX_OFFSET = 1046
INDEX_HEADER_SIZE = 88
ENTRY_TABLE = 1134
META_TABLE = 81134
THIRD_TABLE = 1021134
DATA_START = 1841152
MAX_FILES = 5000
CHUNK_FILES = MAX_FILES
PAYLOAD_ALIGN = 2048
ARCHIVE_ALIGN = 32768
VERSION = 25
THIRD_SIZE = DATA_START - THIRD_TABLE
THIRD_TAIL = 820000
CHUNK_BLOCK = DESCRIPTOR_SIZE + CHUNK_FILES * (ENTRY_SIZE + META_SIZE) + THIRD_SIZE
StoredFile = namedtuple('StoredFile', 'entry meta header payload')

class ChunkBlock:

    def __init__(self, before, flag, third, lead=b''):
        self.before = before
        self.flag = flag
        self.third = bytearray(third)
        self.lead = lead

class WriteError(Exception):
    pass

class ArchiveImage:

    def __init__(self, files, third, order, chunk_field_10, data_prefix=b'', gaps=None, tail=b'', gap_starts=None, blocks=None, chunk0_flag=1):
        self.files = files
        self.third = third
        self.blocks = blocks or []
        self.chunk0_flag = chunk0_flag
        self.data_prefix = data_prefix
        self.gaps = gaps or []
        self.tail = tail
        self.gap_starts = gap_starts or []
        self.orphans = {}
        self.order = order
        self.chunk_field_10 = chunk_field_10
        self.dirty = set()

    @classmethod
    def read(cls, path):
        raw = open(path, 'rb').read()
        with Forge(path) as f:
            if f.index_offset != INDEX_OFFSET:
                raise WriteError(f'{path}: index at 0x{f.index_offset:X}, expected 0x{INDEX_OFFSET:X}')
            records = f.records
        chunks = _read_chunks(raw, path)
        if len(records) != sum((c[1] for c in chunks)) or any((r.index != n for n, r in enumerate(records))):
            raise WriteError(f"{path}: the chunks' indices are not 0..{len(records) - 1}")
        files = []
        for rec in records:
            k, slot = divmod(rec.index, CHUNK_FILES)
            entries, meta_table = (chunks[k][2], chunks[k][3])
            entry = raw[entries + slot * ENTRY_SIZE:entries + (slot + 1) * ENTRY_SIZE]
            meta = raw[meta_table + slot * META_SIZE:meta_table + (slot + 1) * META_SIZE]
            header = raw[rec.offset:rec.offset + PAYLOAD_HEADER_SIZE]
            payload = raw[rec.offset + PAYLOAD_HEADER_SIZE:rec.offset + PAYLOAD_HEADER_SIZE + rec.size]
            files.append(StoredFile(bytearray(entry), bytearray(meta), header, payload))
        offsets = [rec.offset for rec in records]
        order = sorted(range(len(records)), key=lambda i: offsets[i])
        third = raw[THIRD_TABLE:DATA_START]
        chunk_field_10 = struct.unpack_from('<Q', raw, INDEX_OFFSET + 16)[0]
        first = offsets[order[0]] if order else DATA_START
        prefix = raw[DATA_START:first]

        def end_of(idx):
            return offsets[idx] + PAYLOAD_HEADER_SIZE + len(files[idx].payload)
        gaps, gap_starts = ([], [])
        for pos, idx in enumerate(order):
            end = end_of(idx)
            nxt = offsets[order[pos + 1]] if pos + 1 < len(order) else len(raw)
            gaps.append(raw[end:nxt])
            gap_starts.append(end)
        blocks = []
        for k, (at, _count, _e, _m, flag, third_at) in enumerate(chunks[1:], 1):
            pos = next((p for p, idx in enumerate(order) if offsets[idx] > at), None)
            if not pos:
                raise WriteError(f'{path}: index chunk {k} is not stored between two files')
            prev_end = end_of(order[pos - 1])
            block_end = third_at + THIRD_SIZE
            if not (prev_end <= at and block_end <= offsets[order[pos]]):
                raise WriteError(f'{path}: index chunk {k} overlaps a file')
            blocks.append(ChunkBlock(order[pos], flag, raw[third_at:block_end], raw[prev_end:at]))
            gaps[pos - 1] = raw[block_end:offsets[order[pos]]]
            gap_starts[pos - 1] = block_end
        tail = gaps.pop() if gaps else b''
        gap_starts = gap_starts[:len(gaps)]
        return cls(files, bytearray(third), order, chunk_field_10, prefix, gaps, tail, gap_starts, blocks, chunks[0][4])

def _read_chunks(raw, path):
    h = INDEX_OFFSET
    n_chunks = struct.unpack_from('<I', raw, h + 28)[0]
    if struct.unpack_from('<I', raw, h + 24)[0] != CHUNK_FILES or struct.unpack_from('<Q', raw, h + 32)[0] != h + DESCRIPTOR_OFFSET:
        raise WriteError(f'{path}: unexpected index header')
    out, at = ([], h + DESCRIPTOR_OFFSET)
    while at not in (NONE64, 0):
        k = len(out)
        if k > n_chunks:
            raise WriteError(f"{path}: index chunk chain longer than the header's {n_chunks}")
        count, flag, entries, nxt, first, last, meta, third = struct.unpack_from('<IIQQIIQQ', raw, at)
        if k == 0:
            want = (ENTRY_TABLE, META_TABLE, THIRD_TABLE)
        else:
            want = (at + DESCRIPTOR_SIZE, at + DESCRIPTOR_SIZE + CHUNK_FILES * ENTRY_SIZE, at + DESCRIPTOR_SIZE + CHUNK_FILES * (ENTRY_SIZE + META_SIZE))
        if (entries, meta, third) != want or first != k * CHUNK_FILES or last != first + CHUNK_FILES - 1 or (count > CHUNK_FILES) or (nxt not in (NONE64, 0) and count != CHUNK_FILES):
            raise WriteError(f'{path}: index chunk {k} at 0x{at:X} is not laid out as the writer emits it')
        out.append((at, count, entries, meta, flag, third))
        at = nxt
    if len(out) != n_chunks:
        raise WriteError(f'{path}: the index header says {n_chunks} chunks, {len(out)} are chained')
    return out

def _align(value, boundary):
    return (value + boundary - 1) // boundary * boundary

def set_payload(image, index, data):
    sf = image.files[index]
    size = len(data)
    entry = bytearray(sf.entry)
    struct.pack_into('<I', entry, 12, size)
    meta = bytearray(sf.meta)
    struct.pack_into('<I', meta, 0, size)
    header = bytearray(sf.header)
    struct.pack_into('<I', header, 395, size)
    image.files[index] = StoredFile(entry, meta, bytes(header), data)
    pos = image.order.index(index)
    if pos < len(image.gaps):
        gap = image.gaps[pos]
        if any(gap):
            start = image.gap_starts[pos] if pos < len(image.gap_starts) else None
            lead = None if start is None else _align(start, PAYLOAD_ALIGN) - start
            if lead is None or (len(gap) - lead) % PAYLOAD_ALIGN:
                raise WriteError(f"the gap after file {index} holds data that is not the file's slack followed by whole 0x800-aligned blocks; not preserved")
            if any(gap[lead:]):
                image.orphans[pos] = gap[lead:]
        image.gaps[pos] = b''
    image.dirty.add(index)
    return image

def set_entry_key(image, index, key):
    for i, sf in enumerate(image.files):
        if i != index and struct.unpack_from('<I', sf.entry, 8)[0] == key:
            raise WriteError(f"entry key 0x{key:08X} is already file {i}'s")
    sf = image.files[index]
    entry = bytearray(sf.entry)
    struct.pack_into('<I', entry, 8, key)
    header = bytearray(sf.header)
    struct.pack_into('<I', header, 391, key)
    image.files[index] = sf._replace(entry=entry, header=bytes(header))
    image.dirty.add(index)
    return image

def add_file(image, name, payload, uid, template_index=0, entry_key=None):
    index = len(image.files)
    if index and index % CHUNK_FILES == 0:
        _open_chunk(image, index)
    template = image.files[template_index]
    size = len(payload)
    encoded = name.encode('latin-1')
    if len(encoded) > 115:
        raise WriteError(f'name {name!r} is too long for the meta record')
    if entry_key is None:
        used = {struct.unpack_from('<I', sf.entry, 8)[0] for sf in image.files}
        entry_key = max(used) + 1
        while entry_key in used:
            entry_key += 1
    entry = bytearray(template.entry)
    struct.pack_into('<I', entry, 8, entry_key)
    struct.pack_into('<I', entry, 12, size)
    meta = bytearray(template.meta)
    struct.pack_into('<I', meta, 0, size)
    struct.pack_into('<Q', meta, 4, uid)
    struct.pack_into('<I', meta, 28, 4294967295)
    struct.pack_into('<I', meta, 32, index - 1)
    meta[44:160] = encoded.ljust(160 - 44, b'\x00')
    header = bytearray(template.header)
    header[8:8 + 128] = encoded.ljust(128, b'\x00')
    struct.pack_into('<I', header, 391, entry_key)
    struct.pack_into('<I', header, 395, size)
    struct.pack_into('<Q', header, 399, uid)
    if image.files:
        prev = image.files[index - 1]
        prev_meta = bytearray(prev.meta)
        struct.pack_into('<I', prev_meta, 28, index)
        image.files[index - 1] = prev._replace(meta=prev_meta)
    image.files.append(StoredFile(entry, meta, bytes(header), bytes(payload)))
    if image.order:
        image.dirty.add(image.order[-1])
        image.gaps.append(b'')
    image.tail = b''
    image.order.append(index)
    image.dirty.add(index)
    struct.pack_into('<I', image.third, 0, struct.unpack_from('<I', image.third, 0)[0] + 1)
    return index

def _open_chunk(image, index):
    if len(image.blocks) != index // CHUNK_FILES - 1:
        raise WriteError(f'{index} files but {len(image.blocks) + 1} index chunks')
    before = index - 1
    pos = image.order.index(before)
    if pos == 0:
        raise WriteError(f'file {before} is stored first; no place for an index chunk')
    ahead = image.order[pos - 1]
    if any(image.gaps[pos - 1]) and ahead not in image.dirty:
        raise WriteError(f'the gap after file {ahead} holds data; a new index chunk would drop it')
    third = bytearray(THIRD_SIZE)
    third[THIRD_TAIL:] = image.third[THIRD_TAIL:]
    image.blocks.append(ChunkBlock(before, 0, third))
    image.gaps[pos - 1] = b''
    image.dirty.add(ahead)

def build(image):
    return bytes(_build(image))

def _build(image):
    count = len(image.files)
    n_chunks = max(1, -(-count // CHUNK_FILES))
    if len(image.blocks) != n_chunks - 1:
        raise WriteError(f'{count} files need {n_chunks} index chunks, the image has {len(image.blocks) + 1}')
    blocks = {b.before: b for b in image.blocks}
    if len(blocks) != len(image.blocks) or (image.order and image.order[0] in blocks) or any((b not in image.order for b in blocks)):
        raise WriteError('index chunk blocks misplaced')
    block_chunk = {b.before: k for k, b in enumerate(image.blocks, 1)}
    tables = [(INDEX_OFFSET + DESCRIPTOR_OFFSET, ENTRY_TABLE, META_TABLE)] + [None] * len(image.blocks)
    out = bytearray(DATA_START)
    out[0:9] = MAGIC
    struct.pack_into('<I', out, 9, VERSION)
    struct.pack_into('<Q', out, 13, INDEX_OFFSET)
    struct.pack_into('<I', out, 21, 16)
    struct.pack_into('<I', out, 25, 1)
    h = INDEX_OFFSET
    struct.pack_into('<I', out, h + 0, count)
    struct.pack_into('<I', out, h + 4, 1)
    struct.pack_into('<Q', out, h + 8, 0)
    struct.pack_into('<Q', out, h + 16, image.chunk_field_10)
    struct.pack_into('<I', out, h + 24, CHUNK_FILES)
    struct.pack_into('<I', out, h + 28, n_chunks)
    struct.pack_into('<Q', out, h + 32, h + DESCRIPTOR_OFFSET)
    out[THIRD_TABLE:DATA_START] = image.third
    out += image.data_prefix
    offset = DATA_START + len(image.data_prefix)
    offsets = [0] * count
    for pos, i in enumerate(image.order):
        sf = image.files[i]
        offsets[i] = offset
        out += sf.header
        out += sf.payload
        offset += PAYLOAD_HEADER_SIZE + len(sf.payload)
        if pos < len(image.gaps):
            k = block_chunk.get(image.order[pos + 1])
            if k is not None:
                block = image.blocks[k - 1]
                if i not in image.dirty:
                    out += block.lead
                    offset += len(block.lead)
                if len(block.third) != THIRD_SIZE:
                    raise WriteError(f"index chunk {k}'s third table is {len(block.third)} bytes, not {THIRD_SIZE}")
                tables[k] = (offset, offset + DESCRIPTOR_SIZE, offset + DESCRIPTOR_SIZE + CHUNK_FILES * ENTRY_SIZE)
                out += bytes(CHUNK_BLOCK - THIRD_SIZE)
                out += block.third
                offset += CHUNK_BLOCK
            if i in image.dirty:
                padded = _align(offset, PAYLOAD_ALIGN)
                out += bytes(padded - offset)
                offset = padded
                orphan = image.orphans.get(pos, b'')
                out += orphan
                offset += len(orphan)
            else:
                out += image.gaps[pos]
                offset += len(image.gaps[pos])
    for i, sf in enumerate(image.files):
        k, slot = divmod(i, CHUNK_FILES)
        _d, entries, metas = tables[k]
        entry = bytearray(sf.entry)
        struct.pack_into('<Q', entry, 0, offsets[i])
        out[entries + slot * ENTRY_SIZE:entries + (slot + 1) * ENTRY_SIZE] = entry
        out[metas + slot * META_SIZE:metas + (slot + 1) * META_SIZE] = sf.meta
    flags = [image.chunk0_flag] + [b.flag for b in image.blocks]
    for k, (d, entries, metas) in enumerate(tables):
        first = k * CHUNK_FILES
        nxt = tables[k + 1][0] if k + 1 < len(tables) else NONE64
        struct.pack_into('<IIQQIIQQ', out, d, min(CHUNK_FILES, count - first), flags[k], entries, nxt, first, first + CHUNK_FILES - 1, metas, metas + CHUNK_FILES * META_SIZE)
    if not (image.order and image.order[-1] in image.dirty):
        out += image.tail
    out += b'\x00' * (_align(len(out), ARCHIVE_ALIGN) - len(out))
    return out

def rebuild(path):
    return build(ArchiveImage.read(path))

def write(path, image):
    from paths import assert_writable
    assert_writable(path)
    data = _build(image)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(data)
    os.replace(tmp, path)
    return len(data)
