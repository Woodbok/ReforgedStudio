"""A changed copy of a big archive (DataPC.forge: 6 GB) written without holding it in memory.

forgelib's forge_write is the archive writer: ArchiveImage.read takes the whole archive into memory and
_build emits the new one into a second buffer - 12 GB for a 6 GB DataPC. Here the same image is read over a
memory map (every payload a view of the file, nothing copied: forge_write's own read(), handed the map), and the
archive is emitted as forge_write._build lays it out, straight to disk: the first 0x1C1800 bytes (header, chunk 0's
tables) kept in memory and written last, the files streamed in storage order, a later index chunk's tables and
descriptor written in place once every offset is known.

    image = open_image(path)             forge_write.ArchiveImage, payloads = views of the file
    forge_write.set_payload(image, i, payload)
    write(image, dest)                   -> bytes written

Checked: an image written unchanged reproduces the archive byte for byte (`python forgepatch.py identity <archive>`),
as forge_write.rebuild does.
"""
import hashlib
import mmap
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _forge():
    sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
    from ac3editor import mapdata as M
    if M.FORGE_REPO not in sys.path:
        sys.path.insert(0, M.FORGE_REPO)
    import forge_write
    return forge_write


class _Mapped:
    """forge_write.ArchiveImage.read's `open(path, "rb").read()`, answered with a view of a memory map."""

    def __init__(self, view):
        self.view = view

    def read(self):
        return self.view


def open_image(path):
    """-> forge_write.ArchiveImage of `path`, its payloads views of a read-only memory map (kept open with the image)."""
    W = _forge()
    fh = open(path, "rb")
    mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    view = memoryview(mm)
    W.open = lambda _p, _mode="rb": _Mapped(view)          # (module-level name: read() alone sees it)
    try:
        image = W.ArchiveImage.read(path)
    finally:
        del W.open
    image._mapped = (fh, mm, view)
    return image


def write(image, dest):
    """Emit `image` to `dest` as forge_write._build lays it out, streamed. -> the archive's size."""
    W = _forge()
    from forge import DESCRIPTOR_OFFSET, DESCRIPTOR_SIZE, NONE64, PAYLOAD_HEADER_SIZE, ENTRY_SIZE, META_SIZE
    CF = W.CHUNK_FILES
    count = len(image.files)
    n_chunks = max(1, -(-count // CF))
    if len(image.blocks) != n_chunks - 1:
        raise W.WriteError(f"{count} files need {n_chunks} index chunks, the image has {len(image.blocks) + 1}")
    blocks = {b.before: b for b in image.blocks}
    if len(blocks) != len(image.blocks) or (image.order and image.order[0] in blocks) \
            or any(b not in image.order for b in blocks):
        raise W.WriteError("index chunk blocks misplaced")
    block_chunk = {b.before: k for k, b in enumerate(image.blocks, 1)}
    tables = [(W.INDEX_OFFSET + DESCRIPTOR_OFFSET, W.ENTRY_TABLE, W.META_TABLE)] + [None] * len(image.blocks)

    head = bytearray(W.DATA_START)
    head[0:9] = W.MAGIC
    struct.pack_into("<I", head, 9, W.VERSION)
    struct.pack_into("<Q", head, 13, W.INDEX_OFFSET)
    struct.pack_into("<I", head, 21, 0x10)
    struct.pack_into("<I", head, 25, 1)
    h = W.INDEX_OFFSET
    struct.pack_into("<I", head, h + 0x00, count)
    struct.pack_into("<I", head, h + 0x04, 1)
    struct.pack_into("<Q", head, h + 0x08, 0)
    struct.pack_into("<Q", head, h + 0x10, image.chunk_field_10)
    struct.pack_into("<I", head, h + 0x18, CF)
    struct.pack_into("<I", head, h + 0x1C, n_chunks)
    struct.pack_into("<Q", head, h + 0x20, h + DESCRIPTOR_OFFSET)
    head[W.THIRD_TABLE:W.DATA_START] = image.third

    late = []                                               # (position past the head, bytes): later chunks' tables

    def put(at, data):
        if at + len(data) <= W.DATA_START:
            head[at:at + len(data)] = data
        else:
            assert at >= W.DATA_START, "a table straddles the head"
            late.append((at, bytes(data)))

    tmp = dest + ".part"
    with open(tmp, "wb") as fh:
        fh.write(bytes(W.DATA_START))
        fh.write(image.data_prefix)
        offset = W.DATA_START + len(image.data_prefix)
        offsets = [0] * count
        for pos, i in enumerate(image.order):
            sf = image.files[i]
            offsets[i] = offset
            fh.write(sf.header)
            fh.write(sf.payload)
            offset += PAYLOAD_HEADER_SIZE + len(sf.payload)
            if pos < len(image.gaps):
                k = block_chunk.get(image.order[pos + 1])
                if k is not None:
                    block = image.blocks[k - 1]
                    if i not in image.dirty:
                        fh.write(block.lead)
                        offset += len(block.lead)
                    if len(block.third) != W.THIRD_SIZE:
                        raise W.WriteError(f"index chunk {k}'s third table is {len(block.third)} bytes")
                    tables[k] = (offset, offset + DESCRIPTOR_SIZE, offset + DESCRIPTOR_SIZE + CF * ENTRY_SIZE)
                    fh.write(bytes(W.CHUNK_BLOCK - W.THIRD_SIZE))
                    fh.write(block.third)
                    offset += W.CHUNK_BLOCK
                if i in image.dirty:
                    padded = W._align(offset, W.PAYLOAD_ALIGN)
                    fh.write(bytes(padded - offset))
                    offset = padded
                    orphan = image.orphans.get(pos, b"")
                    fh.write(orphan)
                    offset += len(orphan)
                else:
                    fh.write(image.gaps[pos])
                    offset += len(image.gaps[pos])
        if not (image.order and image.order[-1] in image.dirty):
            fh.write(image.tail)
            offset += len(image.tail)
        end = W._align(offset, W.ARCHIVE_ALIGN)
        fh.write(bytes(end - offset))
        if fh.tell() != end:
            raise W.WriteError(f"streamed {fh.tell()} bytes, laid out {end}")

        # entry and meta tables, per chunk (the slots past the last file stay zero, as _build leaves them)
        for k in range(n_chunks):
            first = k * CF
            ent = bytearray(CF * ENTRY_SIZE)
            met = bytearray(CF * META_SIZE)
            for slot, i in enumerate(range(first, min(count, first + CF))):
                sf = image.files[i]
                entry = bytearray(sf.entry)
                struct.pack_into("<Q", entry, 0, offsets[i])
                ent[slot * ENTRY_SIZE:(slot + 1) * ENTRY_SIZE] = entry
                met[slot * META_SIZE:(slot + 1) * META_SIZE] = sf.meta
            _d, entries, metas = tables[k]
            if k == 0:
                # chunk 0's tables only cover the slots _build writes; the rest of the head is the image's
                n = min(count, CF)
                put(entries, ent[:n * ENTRY_SIZE])
                put(metas, met[:n * META_SIZE])
            else:
                put(entries, ent)
                put(metas, met)
        flags = [image.chunk0_flag] + [b.flag for b in image.blocks]
        for k, (d, entries, metas) in enumerate(tables):
            first = k * CF
            nxt = tables[k + 1][0] if k + 1 < len(tables) else NONE64
            put(d, struct.pack("<IIQQIIQQ", min(CF, count - first), flags[k], entries, nxt, first,
                               first + CF - 1, metas, metas + CF * META_SIZE))
        for at, data in late:
            fh.seek(at)
            fh.write(data)
        fh.seek(0)
        fh.write(head)
    os.replace(tmp, dest)
    return end


def sha1(path, chunk=1 << 24):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "identity":
        src, dest = sys.argv[2], sys.argv[3]
        n = write(open_image(src), dest)
        a, b = sha1(src), sha1(dest)
        print(f"{n} bytes; {'IDENTICAL' if a == b else 'DIFFERENT'} ({a} / {b})")
        sys.exit(0 if a == b else 1)
    print(__doc__)
