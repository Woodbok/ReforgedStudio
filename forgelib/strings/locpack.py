"""forgelib/strings/locpack.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import paths
from datafile import decompress
from forge import Forge
import objects

def be16(b, o):
    return struct.unpack_from('>H', b, o)[0]

def be32(b, o):
    return struct.unpack_from('>I', b, o)[0]

class LocData:

    def __init__(self, data):
        self.data = data
        if len(data) < 6:
            self.h0 = self.h1 = self.h2 = 0
            self.symbols, self.blocks = ([], [])
            return
        self.h0, self.h1 = (be16(data, 0), be16(data, 2))
        self.symbols = [(be16(data, 4 + 4 * i), be16(data, 6 + 4 * i)) for i in range(self.h1)]
        p = 4 + 4 * self.h1
        self.h2 = be16(data, p)
        p += 2
        self.blocks = [(be32(data, p + 12 * i), be32(data, p + 12 * i + 4), be32(data, p + 12 * i + 8)) for i in range(self.h2)]

    def expand(self, sym, out):
        stack = [sym]
        while stack:
            s = stack.pop()
            a, b = self.symbols[s]
            if b == 0:
                out.append(a)
            else:
                stack.append(a)
                stack.append(b)

    def decode_bytes(self, start, end):
        d, out, i = (self.data, [], start)
        while i < end:
            b = d[i]
            i += 1
            if b < self.h0:
                code = b
            elif b == 255:
                code = be16(d, i)
                i += 2
            else:
                code = (b << 8 | d[i]) + self.h0 * 65281 & 65535
                i += 1
            self.expand(code + 1 & 65535, out)
        return ''.join((chr(c) for c in out))

    def block_strings(self, blk):
        first, text, index = blk
        d = self.data
        n = be16(d, index)
        out = [(first, text, text + be16(d, index + 2))]
        prev_end = be16(d, index + 2)
        for k in range(n):
            pos, end = (be16(d, index + 4 + 4 * k), be16(d, index + 6 + 4 * k))
            out.append((first + pos, text + prev_end, text + end))
            prev_end = end
        return out

    def strings(self):
        res = {}
        for blk in self.blocks:
            for sid, s, e in self.block_strings(blk):
                if e >= s:
                    res[sid] = self.decode_bytes(s, e)
        return res

    def lookup(self, sid):
        blk = None
        for b in self.blocks:
            if sid - b[0] < 0:
                break
            blk = b
        if blk is None or sid - blk[0] >= 65536:
            return None
        cache = self.__dict__.setdefault('_block_index', {})
        if blk not in cache:
            index = {}
            for i, s, e in self.block_strings(blk):
                index.setdefault(i, (s, e))
            cache[blk] = index
        span = cache[blk].get(sid)
        return self.decode_bytes(*span) if span else None

def package_data(language='English', archive=None):
    archive = archive or os.path.join(paths.AC3_PRISTINE, 'DataPC.forge')
    with Forge(archive) as f:
        rec = next((r for r in f.records if r.name == f'LocalizationPackage_{language}'))
        d = decompress(f.read(rec))
    (off, size, _s), = objects.walk(d)
    _t, name = objects.parse_object(d, off, size)
    body = bytes(d[off + 12 + len(name):off + size])
    n = struct.unpack_from('<I', body, 30)[0]
    return body[34:34 + n]

def encode_code(code, h0):
    if code < h0:
        return bytes([code])
    v = code - h0 * 65281 & 65535
    if h0 <= v >> 8 < 255:
        return bytes([v >> 8, v & 255])
    return b'\xff' + struct.pack('>H', code)

def owning_block(blocks, sid):
    owner = None
    for i, blk in enumerate(blocks):
        if blk[0] > sid:
            break
        owner = i
    if owner is not None and sid - blocks[owner][0] < 65536:
        return owner
    return None

def edit(loc, sets):
    d = loc.data
    symbols = list(loc.symbols)
    leaf = {}
    for i, (a, b) in enumerate(symbols):
        if b == 0 and i and (a not in leaf):
            leaf[a] = i

    def encode(text):
        for ch in text:
            if ord(ch) > 65535:
                raise ValueError(f"character {ch!r} is outside UTF-16's basic plane")
            if ord(ch) not in leaf:
                symbols.append((ord(ch), 0))
                leaf[ord(ch)] = len(symbols) - 1
        return b''.join((encode_code(leaf[ord(ch)] - 1, loc.h0) for ch in text))
    blocks = []
    for blk in loc.blocks:
        blocks.append([blk[0], {i - blk[0]: d[s:e] for i, s, e in loc.block_strings(blk)}])
    for sid in sorted(sets):
        if not 0 <= sid <= 4294967295:
            raise ValueError(f'string id {sid} out of range')
        k = owning_block(blocks, sid)
        if k is None:
            blocks.append([sid, {}])
            blocks.sort(key=lambda b: b[0])
            k = next((i for i, b in enumerate(blocks) if b[0] == sid))
        blocks[k][1][sid - blocks[k][0]] = encode(sets[sid])
    split = []
    for first, strs in blocks:
        pos = sorted(strs)
        assert pos[0] == 0, 'a block always holds its first id'
        cur, size = ({}, 0)
        for p in pos:
            if cur and size + len(strs[p]) > 65535:
                split.append(cur)
                cur, size = ({}, 0)
            cur[first + p] = strs[p]
            size += len(strs[p])
        split.append(cur)
    parts = []
    for chunk in split:
        ids = sorted(chunk)
        first = ids[0]
        text, ends = (b'', [])
        for i in ids:
            text += chunk[i]
            ends.append(len(text))
        if len(text) > 65535:
            raise ValueError(f"string 0x{first:X}: {len(text)} bytes, over the u16 offsets' 64 KB on its own")
        idx = struct.pack('>HH', len(ids) - 1, ends[0]) + b''.join((struct.pack('>HH', i - first, e) for i, e in zip(ids[1:], ends[1:])))
        parts.append((first, idx, text))
    if len(symbols) > 65535:
        raise ValueError('more than 65,535 symbols')
    head = struct.pack('>HH', loc.h0, len(symbols)) + b''.join((struct.pack('>HH', a, b) for a, b in symbols))
    head += struct.pack('>H', len(parts))
    off = len(head) + 12 * len(parts)
    idx_off, text_off = ([], [])
    for _f, idx, _t in parts:
        idx_off.append(off)
        off += len(idx)
    for _f, _i, txt in parts:
        text_off.append(off)
        off += len(txt)
    recs = b''.join((struct.pack('>III', f, t, i) for (f, _i, _t), t, i in zip(parts, text_off, idx_off)))
    return head + recs + b''.join((i for _f, i, _t in parts)) + b''.join((t for _f, _i, t in parts))

def rebuild(loc, additions=None):
    return edit(loc, additions or {})

def package_body(language='English', archive=None):
    archive = archive or os.path.join(paths.AC3_PRISTINE, 'DataPC.forge')
    with Forge(archive) as f:
        rec = next((r for r in f.records if r.name == f'LocalizationPackage_{language}'))
        d = decompress(f.read(rec))
    (off, size, _s), = objects.walk(d)
    t, name = objects.parse_object(d, off, size)
    return (t, name, bytes(d[off + 12 + len(name):off + size]))

def with_data(body, data):
    n = struct.unpack_from('<I', body, 30)[0]
    assert len(body) == 34 + n, 'package body is a 34-byte header + the data'
    return body[:30] + struct.pack('<I', len(data)) + data

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('language', nargs='?', default='English')
    ap.add_argument('--game', default='AC3', choices=['AC3', 'ACR'], help='ACR reads multi/DataPC_localization.forge from the ACR install')
    ap.add_argument('--archive', default=None, help="a build's DataPC.forge (AC3) instead of the pristine one")
    ap.add_argument('--id', action='append', default=[], help='string id(s) to look up (hex or decimal)')
    ap.add_argument('--find', default=None, help='print every string containing this text')
    ap.add_argument('--dump', default=None, help="write 'id<TAB>text' for every string")
    a = ap.parse_args(argv)
    archive = a.archive or (os.path.join(paths.ACR, 'DataPC_localization.forge') if a.game == 'ACR' else None)
    loc = LocData(package_data(a.language, archive))
    print(f'{a.language}: {len(loc.data):,} bytes, h0 {loc.h0}, {loc.h1} symbols, {loc.h2} blocks')
    for s in a.id:
        sid = int(s, 0)
        print(f'0x{sid:X}: {loc.lookup(sid)!r}')
    if a.find or a.dump:
        allstr = loc.strings()
        print(f'{len(allstr)} strings')
        if a.find:
            for sid, t in sorted(allstr.items()):
                if a.find.lower() in t.lower():
                    print(f'0x{sid:X}: {t!r}')
        if a.dump:
            with open(a.dump, 'w', encoding='utf-8') as fh:
                for sid, t in sorted(allstr.items()):
                    fh.write(f'0x{sid:08X}\t{t}\n')
if __name__ == '__main__':
    main(sys.argv[1:])
