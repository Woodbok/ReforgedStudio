"""forgelib/spmaps/forge27.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
from collections import namedtuple
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import lzo1x
import paths
VERSION = 27
MAGIC = 1476110899
DESCRIPTOR_OFFSET = 44
ENTRY_SIZE = 20
META_SIZE = 192
NONE64 = 18446744073709551615
Record = namedtuple('Record', 'index name uid offset size')

class Forge27Error(Exception):
    pass

def sp_path(archive):
    if os.sep in archive or '/' in archive:
        return archive
    return os.path.join(paths.AC3_SINGLEPLAYER, archive)

def sp_archives():
    d = paths.AC3_SINGLEPLAYER
    return sorted((n for n in os.listdir(d) if n.startswith('DataPC_DX11') and n.endswith('.forge')))

class Forge27:

    def __init__(self, path):
        self.path = sp_path(path)
        self._fh = open(self.path, 'rb')
        head = self._fh.read(32)
        if head[:9] != b'scimitar\x00':
            raise Forge27Error(f'{self.path}: not a scimitar archive')
        self.version = struct.unpack_from('<I', head, 9)[0]
        if self.version != VERSION:
            raise Forge27Error(f'{self.path}: version {self.version}, this reader handles {VERSION}')
        self.index_offset = struct.unpack_from('<Q', head, 13)[0]
        self.records = self._read_index()
        self.by_name = {r.name: r for r in self.records}
        self.by_uid = {r.uid: r for r in self.records}

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read_index(self):
        out, seen = ([], set())
        desc = self.index_offset + DESCRIPTOR_OFFSET
        while desc not in (0, NONE64) and desc not in seen:
            seen.add(desc)
            self._fh.seek(desc)
            d = self._fh.read(48)
            n, _one, ent, nxt, first, _last, meta = struct.unpack_from('<IIQQIIQ', d, 0)
            self._fh.seek(ent)
            E = self._fh.read(n * ENTRY_SIZE)
            self._fh.seek(meta)
            M = self._fh.read(n * META_SIZE)
            for i in range(n):
                off, uid, size = struct.unpack_from('<QQI', E, i * ENTRY_SIZE)
                m = i * META_SIZE
                name = M[m + 44:m + 44 + 128].split(b'\x00')[0].decode('latin-1')
                out.append(Record(first + i, name, uid, off, size))
            desc = nxt
        return out

    def raw(self, r):
        self._fh.seek(r.offset)
        return self._fh.read(r.size)

    def payload(self, r):
        raw = self.raw(r)
        dep_len, _u, n = struct.unpack_from('<IHH', raw, 0)
        deps, o = ([], 8)
        if _u == 0:
            for _ in range(n):
                uid = struct.unpack_from('<Q', raw, o)[0]
                a, k, b = (raw[o + 8], raw[o + 9], raw[o + 10])
                deps.append((uid, a, b, struct.unpack_from(f'<{k}h', raw, o + 11)))
                o += 11 + 2 * k
        return (deps, chunks(raw, 4 + dep_len))

    def toc_uids(self, r):
        raw = self.raw(r)
        if len(raw) < 8:
            return []
        cs = chunks(raw, 4 + struct.unpack_from('<I', raw, 0)[0], limit=1)
        return [u for u, _s, _x in toc(cs[0])] if cs else []

    def objects(self, r):
        _deps, cs = self.payload(r)
        if len(cs) < 2:
            return []
        return objects(cs[0], b''.join(cs[1:]))

def chunks(buf, o=0, limit=None):
    out = []
    while o + 17 <= len(buf) and (limit is None or len(out) < limit):
        magic = struct.unpack_from('<I', buf, o)[0]
        if magic != MAGIC:
            break
        codec = buf[o + 10]
        count = struct.unpack_from('<H', buf, o + 15)[0]
        q = o + 17
        blocks = [struct.unpack_from('<HH', buf, q + 4 * i) for i in range(count)]
        q += 4 * count
        data = bytearray()
        for raw, comp in blocks:
            q += 4
            if raw == comp:
                data += buf[q:q + comp]
            elif codec in (0, 1):
                dec, used = lzo1x.decompress(buf[q:q + comp], raw)
                if used != comp:
                    raise Forge27Error(f'block at {q}: used {used} of {comp} bytes')
                data += dec
            elif codec == 2:
                import datafile
                datafile.LZO2A(buf[q:q + comp], raw, data)
            else:
                raise Forge27Error(f'codec {codec} at {o}')
            q += comp
        out.append(bytes(data))
        o = q
    return out

def toc(t):
    n = struct.unpack_from('<H', t, 0)[0]
    out, o = ([], 2)
    for _ in range(n):
        uid, size, k = struct.unpack_from('<QIH', t, o)
        o += 14
        out.append((uid, size, struct.unpack_from(f'<{k}H', t, o)))
        o += 2 * k
    if o != len(t):
        raise Forge27Error(f'table of contents ended at {o} of {len(t)}')
    return out

def objects(toc_bytes, content):
    out, o = ([], 0)
    for uid, size, _x in toc(toc_bytes):
        typ, _bsize, nlen = struct.unpack_from('<III', content, o)
        if nlen > 1024:
            raise Forge27Error(f'bad name length {nlen} at {o}')
        name = content[o + 12:o + 12 + nlen].decode('latin-1')
        out.append({'uid': uid, 'type': typ, 'name': name, 'body': content[o + 12 + nlen:o + size]})
        o += size
    if o != len(content):
        raise Forge27Error(f'objects end at {o} of {len(content)}')
    return out

def class_names():
    sys.path.insert(0, os.path.join(ROOT, 'maps'))
    import convert
    return {v: k for k, v in convert.class_ids().items()}
if __name__ == '__main__':
    import argparse
    import random
    from collections import Counter
    ap = argparse.ArgumentParser()
    ap.add_argument('archive')
    ap.add_argument('--name', help="list one record's objects")
    ap.add_argument('--sample', type=int, default=400)
    a = ap.parse_args()
    cls = class_names()
    with Forge27(a.archive) as f:
        print(f'{f.path}: {len(f.records)} files')
        if a.name:
            for o in f.objects(f.by_name[a.name]):
                print(f"  {cls.get(o['type'], hex(o['type'])):32} {len(o['body']):9} {o['uid'] and hex(o['uid'])} {o['name']}")
            sys.exit()
        C, bad = (Counter(), 0)
        random.seed(1)
        recs = f.records[1:]
        for r in random.sample(recs, min(a.sample, len(recs))):
            try:
                for o in f.objects(r):
                    C[cls.get(o['type'], hex(o['type']))] += 1
            except Forge27Error as e:
                bad += 1
                print('  ERR', r.name, e)
        print(f'sample {a.sample}: {bad} failed')
        for k, v in C.most_common(60):
            print(f'  {v:7} {k}')
