"""forgelib/sp/sp_anim.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import exe_registry
_classes, _ = exe_registry.load(exe_registry.AC3)
_ids = {n: i for i, n in _classes.items()}
EVENT_TAIL = {_ids['WwiseID']: 16, _ids['ContactEvent']: 19, _ids['HumanSoundSet']: 12, _ids['PlayFacialCustomActionEvent']: 13, _ids['FXCommand']: 29}
MAPPING = _ids['AnimTrackDataMapping']
ZERO2 = bytes(2)

def clip_at(body):
    return 9 + 12 * struct.unpack_from('<I', body, 4)[0]

def markers(body):
    out = []
    for p in range(clip_at(body) + 8, len(body) - 3):
        v = struct.unpack_from('<I', body, p)[0]
        if v in _classes and v > 1048575 and (bin(v).count('1') >= 8):
            out.append((p, v))
    return out

def convert(body, inline_pool=True):
    b = bytearray(body)
    ms = [p for p, cls in markers(bytes(b)) if cls == MAPPING]
    if ms:
        at = ms[-1] + 12
        n = struct.unpack_from('<I', b, at + 2)[0]
        b[at:at + 6] = ZERO2
        struct.pack_into('<I', b, at - 2, n)
    c = clip_at(b)
    if struct.unpack_from('<f', b, c + 16)[0] == 0.0:
        struct.pack_into('<f', b, c + 16, 1.0)
    for p, cls in reversed(markers(bytes(b))):
        if cls in EVENT_TAIL:
            b[p + EVENT_TAIL[cls]:p + EVENT_TAIL[cls]] = b'\x01'
    ms = [p for p, cls in markers(bytes(b)) if cls == MAPPING]
    if ms and inline_pool:
        import pool_inline
        out, added = pool_inline.inline_stream(bytes(b), ms[-1])
        return grow_arena(out, added)
    return bytes(b)
ARENA = {1: (3, 1), 2: (5, 2), 4: (7, 4)}
ARENA_SLACK = 16

def grow_arena(body, added):
    b = bytearray(body)
    for a, nbytes in sorted(added.items()):
        if not nbytes:
            continue
        code, unit = ARENA[a]
        grow = -(-(nbytes + ARENA_SLACK) // unit)
        n = struct.unpack_from('<I', b, 4)[0]
        rows = [8 + 12 * i for i in range(n)]
        zero = [r for r in rows if struct.unpack_from('<I', b, r)[0] == 0]
        hit = [r for r in zero if struct.unpack_from('<H', b, r + 6)[0] == code]
        if hit:
            struct.pack_into('<I', b, hit[0] + 8, struct.unpack_from('<I', b, hit[0] + 8)[0] + grow)
        else:
            at = zero[-1] + 12 if zero else 8
            b[at:at] = struct.pack('<IHHI', 0, 0, code, grow)
            struct.pack_into('<I', b, 4, n + 1)
    return bytes(b)

def check_stream(body):
    import pool_inline
    import stream_walk
    ms = [p for p, cls in markers(body) if cls == MAPPING]
    if not ms:
        return ['no AnimTrackDataMapping']
    n = struct.unpack_from('<I', body, ms[-1] + 10)[0]
    pos = ms[-1] + 14
    need = {}
    try:
        for _ in range(n):
            if body[pos] == 254:
                return [f'FE reference at {pos} (not inlined)']
            tag, size = (body[pos], struct.unpack_from('<I', body, pos + 1)[0])
            a = pool_inline._a(pool_inline.KIND[tag][1])
            need[a] = need.get(a, 0) + pool_inline._align(size, a)
            pos = stream_walk.walk_element(body, pos)
    except Exception as e:
        return [f'stream element read fails at {pos}: {e}']
    if pos != len(body):
        return [f'{n} stream elements end at {pos}, the body at {len(body)}']
    k = struct.unpack_from('<I', body, 4)[0]
    cap = {}
    for i in range(k):
        r = 8 + 12 * i
        if struct.unpack_from('<I', body, r)[0] == 0:
            cap[struct.unpack_from('<H', body, r + 6)[0]] = struct.unpack_from('<I', body, r + 8)[0]
    for a, nbytes in need.items():
        code, unit = ARENA[a]
        if cap.get(code, 0) * unit < nbytes:
            return [f'arena class {a}: capacity {cap.get(code, 0) * unit} B < {nbytes} B the elements allocate']
    return []
