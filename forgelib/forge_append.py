"""forgelib/forge_append.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import shutil
import struct
INDEX = 1046
FIRST_DESCRIPTOR = INDEX + 40
ENTRY, META, HEADER = (16, 188, 440)
ALIGN, ARCHIVE_ALIGN = (2048, 32768)
NONE = 18446744073709551615

class Chunk:

    def __init__(self, fh, at):
        fh.seek(at)
        self.count, self.flag, self.entries, self.next, self.first, self.last, self.meta, self.third = struct.unpack('<IIQQIIQQ', fh.read(48))
        self.at = at

def chunks(fh):
    out, at = ([], FIRST_DESCRIPTOR)
    while at not in (NONE, 0):
        c = Chunk(fh, at)
        out.append(c)
        at = c.next
    return out

def append_files(src, dst, files):
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copyfile(src, dst)
    with open(dst, 'r+b') as fh:
        cs = chunks(fh)
        last = cs[-1]
        fh.seek(INDEX)
        total = struct.unpack('<I', fh.read(4))[0]
        assert total == sum((c.count for c in cs)), (total, [c.count for c in cs])
        assert last.count + len(files) <= 5000, 'the last chunk is full: opening a new index chunk takes a rebuild (forge_write.add_file)'
        assert last.last - last.first + 1 == 5000

        def entry_at(index):
            c = next((c for c in cs if c.first <= index < c.first + c.count))
            return (c.entries + (index - c.first) * ENTRY, c.meta + (index - c.first) * META)
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        new_indices = []
        for f in files:
            t_entry, t_meta = entry_at(f['template'])
            fh.seek(t_entry)
            t_off = struct.unpack('<Q', fh.read(8))[0]
            fh.seek(t_meta)
            meta = bytearray(fh.read(META))
            fh.seek(t_off)
            header = bytearray(fh.read(HEADER))
            assert header[:8] == b'FILEDATA', f"template {f['template']} has no FILEDATA header"
            name = f['name'].encode('latin-1')
            assert len(name) <= 115, f['name']
            size = len(f['payload'])
            index = total
            offset = (end + ALIGN - 1) // ALIGN * ALIGN
            header[8:8 + 128] = name.ljust(128, b'\x00')
            struct.pack_into('<I', header, 391, f['key'])
            struct.pack_into('<I', header, 395, size)
            struct.pack_into('<Q', header, 399, f['uid'])
            fh.seek(end)
            fh.write(b'\x00' * (offset - end))
            fh.write(header)
            fh.write(f['payload'])
            end = offset + HEADER + size
            slot = last.count
            fh.seek(last.entries + slot * ENTRY)
            fh.write(struct.pack('<QII', offset, f['key'], size))
            struct.pack_into('<I', meta, 0, size)
            struct.pack_into('<Q', meta, 4, f['uid'])
            struct.pack_into('<I', meta, 28, 4294967295)
            struct.pack_into('<I', meta, 32, index - 1)
            meta[44:160] = name.ljust(160 - 44, b'\x00')
            fh.seek(last.meta + slot * META)
            fh.write(meta)
            _pe, prev_meta = entry_at(index - 1)
            fh.seek(prev_meta + 28)
            fh.write(struct.pack('<I', index))
            last.count += 1
            total += 1
            new_indices.append(index)
        padded = (end + ARCHIVE_ALIGN - 1) // ARCHIVE_ALIGN * ARCHIVE_ALIGN
        fh.seek(end)
        fh.write(b'\x00' * (padded - end))
        fh.truncate(padded)
        fh.seek(last.at)
        fh.write(struct.pack('<I', last.count))
        fh.seek(INDEX)
        fh.write(struct.pack('<I', total))
        third = cs[0].third
        fh.seek(third)
        lead = struct.unpack('<I', fh.read(4))[0]
        fh.seek(third)
        fh.write(struct.pack('<I', lead + len(files)))
    return new_indices
