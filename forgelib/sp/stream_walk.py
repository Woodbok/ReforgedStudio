"""forgelib/sp/stream_walk.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
_U32 = struct.Struct('<I')

class WalkError(Exception):
    pass

def _u32(buf, pos):
    if pos + 4 > len(buf):
        raise WalkError('u32 past end at %d' % pos)
    return (_U32.unpack_from(buf, pos)[0], pos + 4)

def _make_reader(kind, stride):

    def reader(buf, pos):
        _x, pos = _u32(buf, pos)
        y, pos = _u32(buf, pos)
        if kind == 'A':
            if y > 1:
                pos += y - 1
        elif kind == 'B':
            if y > 1:
                pos += 2 * (y - 1)
        elif kind == 'C':
            if pos >= len(buf):
                raise WalkError('u8 past end at %d' % pos)
            b = buf[pos]
            pos += 1 + (b & 63)
        else:
            raise WalkError(kind)
        pos += stride * y
        if pos > len(buf):
            raise WalkError('element runs past end (pos %d > %d)' % (pos, len(buf)))
        return pos
    reader.kind = kind
    reader.stride = stride
    return reader
_TABLE = [((0,), 'A', 16), ((1,), 'B', 16), ((2,), 'C', 16), ((3, 21, 42), 'A', 2), ((4, 22, 43), 'B', 2), ((5, 23, 44), 'C', 2), ((6,), 'A', 3), ((7,), 'B', 3), ((8,), 'C', 3), ((9, 30, 36), 'A', 4), ((10, 31, 37), 'B', 4), ((11, 32, 38), 'C', 4), ((12, 33), 'A', 6), ((13, 34), 'B', 6), ((14, 35), 'C', 6), ((15,), 'A', 8), ((16,), 'B', 8), ((17,), 'C', 8), ((18, 39, 45, 48, 51), 'A', 1), ((19, 40, 46, 49, 52), 'B', 1), ((20, 41, 47, 50, 53), 'C', 1), ((24, 27), 'A', 12), ((25, 28), 'B', 12), ((26, 29), 'C', 12)]
READERS = {}
for _tags, _k, _s in _TABLE:
    _r = _make_reader(_k, _s)
    for _t in _tags:
        READERS[_t] = _r

def walk_element(buf, pos):
    if pos >= len(buf):
        raise WalkError('tag past end at %d' % pos)
    tag = buf[pos]
    pos += 1
    if tag == 254:
        if pos + 4 > len(buf):
            raise WalkError('0xFE u32 past end')
        return pos + 4
    rd = READERS.get(tag)
    if rd is None:
        raise WalkError('unknown tag 0x%02x at %d' % (tag, pos - 1))
    return rd(buf, pos)

def walk_all(buf, pos):
    tags = []
    while pos < len(buf):
        tags.append(buf[pos])
        pos = walk_element(buf, pos)
    return (pos, len(tags), tags)
if __name__ == '__main__':
    import pickle
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/fxhits.pkl'
    marker = struct.pack('<I', 1698474614)
    ok = 0
    tot = 0
    for rec, name, body in pickle.load(open(path, 'rb')):
        tot += 1
        i = body.rfind(marker)
        if i < 0:
            continue
        start = i + 12 + 2
        try:
            p, n, _ = walk_all(body, start)
            ok += p == len(body)
        except WalkError:
            pass
    print(ok, '/', tot)
