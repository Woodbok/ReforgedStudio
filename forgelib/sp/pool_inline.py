"""forgelib/sp/pool_inline.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from stream_walk import _TABLE
KIND = {t: (k, s) for tags, k, s in _TABLE for t in tags}
POOL_BASE = 36376943

def _align(n, a):
    return n + a - 1 & ~(a - 1)

def _a(stride):
    return next((a for a in (4, 2, 1) if stride % a == 0))

class PoolError(Exception):
    pass

def decode(m):
    tag = m[0]
    if tag not in KIND:
        raise PoolError('tag 0x%02x' % tag)
    kind, stride = KIND[tag]
    b1 = m[1]
    if kind == 'A':
        y = b1
        if y < 1:
            raise PoolError('A element, Y = 0')
        hdr = bytes(m[2:2 + y - 1])
        end = 2 + y - 1
        shdr = hdr
    elif kind == 'B':
        if b1:
            y, hs = (b1, 2)
        else:
            y, hs = (struct.unpack_from('<H', m, 2)[0], 4)
        hdr = bytes(m[hs:hs + 2 * (y - 1)])
        end = hs + 2 * (y - 1)
        shdr = hdr
    else:
        n = b1 & 63
        hdr = bytes(m[2:2 + n])
        y = sum((bin(c).count('1') for c in hdr))
        end = 2 + n
        shdr = bytes([b1]) + hdr
    off = _align(end, _a(stride))
    size = off + stride * y
    data = bytes(m[off:size])
    if len(data) != stride * y:
        raise PoolError('element runs past the buffer')
    return (bytes([tag]) + struct.pack('<II', size, y) + shdr + data, size)

def walk_pool(read, start=1, stop_at=None, limit=1 << 22):
    out, p = ([], start)
    while p < (stop_at or limit):
        try:
            _, size = decode(read(p, 4096))
        except (PoolError, IndexError, struct.error):
            break
        out.append((p, read(p, 1)[0], size))
        p += size
    return (out, p)
POOL_FILE = os.path.join(HERE, 'acrsp_pool.bin')
_pool = None

def pool():
    global _pool
    if _pool is None:
        with open(POOL_FILE, 'rb') as f:
            _pool = f.read()
    return _pool

def element(ref):
    blob = pool()
    if not 0 < ref < len(blob):
        raise PoolError('FE offset 0x%x outside the pool' % ref)
    ser, _ = decode(blob[ref:ref + 4096])
    return ser

def inline_stream(body, mapping_marker_at):
    import stream_walk
    n = struct.unpack_from('<I', body, mapping_marker_at + 10)[0]
    pos = mapping_marker_at + 14
    out = bytearray(body[:pos])
    added = {}
    for _ in range(n):
        end = stream_walk.walk_element(body, pos)
        if body[pos] == 254:
            ser = element(struct.unpack_from('<I', body, pos + 1)[0])
            out += ser
            a = _a(KIND[ser[0]][1])
            added[a] = added.get(a, 0) + _align(struct.unpack_from('<I', ser, 1)[0], a)
        else:
            out += body[pos:end]
        pos = end
    out += body[pos:]
    return (bytes(out), added)
