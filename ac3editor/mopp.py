"""Havok MOPP code (the triangle lookup tree in every AC3 MeshShape): disassembly, queries, checks.

Opcode meanings start from pyffi's partial map (niftools, Bethesda games' Havok) and are confirmed or corrected
against AC3's own shapes.
"""
import struct
from collections import Counter


class MoppError(Exception):
    pass


def walk(code, visit=None, start=0):
    """Full traversal of every branch. -> (triangles reached [(tri, path)], opcode histogram, bytes visited).

    `path` is the list of (opcode, args) constraints from the root to that triangle, for bound checks.
    """
    hist = Counter()
    seen = set()
    out = []
    stack = [(start, 0, ())]
    n = len(code)
    while stack:
        i, toff, path = stack.pop()
        while True:
            if i >= n:
                raise MoppError(f"ran off the end at {i}")
            op = code[i]
            hist[op] += 1
            seen.add(i)
            if 0x30 <= op < 0x50:
                out.append((op - 0x30 + toff, path))
                break
            if op == 0x50:
                out.append((code[i + 1] + toff, path))
                break
            if op == 0x51:
                out.append(((code[i + 1] << 8 | code[i + 2]) + toff, path))
                break
            if op == 0x52:
                out.append(((code[i + 1] << 16 | code[i + 2] << 8 | code[i + 3]) + toff, path))
                break
            if op == 0x53:
                out.append(((code[i + 1] << 24 | code[i + 2] << 16 | code[i + 3] << 8 | code[i + 4]) + toff, path))
                break
            if op == 0x09:
                toff += code[i + 1]
                i += 2
            elif op == 0x0A:
                toff += code[i + 1] << 8 | code[i + 2]
                i += 3
            elif op == 0x0B:
                toff = code[i + 1] << 24 | code[i + 2] << 16 | code[i + 3] << 8 | code[i + 4]
                i += 5
            elif op == 0x05:
                i += 2 + code[i + 1]
            elif op == 0x06:
                i += 3 + (code[i + 1] << 8 | code[i + 2])
            elif 0x10 <= op <= 0x1C:
                a, b, j = code[i + 1], code[i + 2], code[i + 3]
                stack.append((i + 4 + j, toff, path + (("ge", op, b),)))
                path = path + (("le", op, a),)
                i += 4
            elif 0x20 <= op <= 0x22:
                a, j = code[i + 1], code[i + 2]
                stack.append((i + 3 + j, toff, path + (("ge1", op, a),)))
                path = path + (("lt1", op, a),)
                i += 3
            elif 0x23 <= op <= 0x25:
                a, b = code[i + 1], code[i + 2]
                j1 = code[i + 3] << 8 | code[i + 4]
                j2 = code[i + 5] << 8 | code[i + 6]
                stack.append((i + 7 + j2, toff, path + (("ge", op - 0x13, b),)))
                path = path + (("le", op - 0x13, a),)
                i += 7 + j1
            elif 0x26 <= op <= 0x28:
                path = path + (("bound", op, code[i + 1], code[i + 2]),)
                i += 3
            elif op == 0x70:
                # absolute 32-bit jump (big-endian), not in pyffi's map: every one of AC3's 877 stock trees then
                # reaches each of its triangles exactly once; read as relative, 312 break
                i = code[i + 1] << 24 | code[i + 2] << 16 | code[i + 3] << 8 | code[i + 4]
            elif 0x01 <= op <= 0x04:
                path = path + (("rescale", op, code[i + 1], code[i + 2], code[i + 3]),)
                i += 4
            else:
                raise MoppError(f"unknown opcode 0x{op:02X} at {i}")
    return out, hist, seen


# ---------------------------------------------------------------------------------------------- the grid --
#
# Measured on AC3's 877 stock trees: offset = the vertices' minimum,
# scale = 254 * 65536 / (longest side), q = (v - offset) * scale, and every test compares the top byte of q
# (cell = q >> 16, 0..254). Bounds and "<= a" limits are exact on the high side; Havok's builder leaves up to one
# cell of slack on the low side. The code is padded to a multiple of 16 bytes with 0xCD.

PAD = 0xCD
CELL = 65536.0


def grid(verts):
    """-> (offset xyz, scale) as AC3 stores them in MoppOffsetAndScale."""
    import numpy as np
    v = np.asarray(verts, dtype=np.float64)
    lo = v.min(0)
    ext = float((v.max(0) - lo).max())
    return lo, (254.0 * CELL / ext if ext > 0 else 1.0)


def cells(verts, offset, scale):
    import numpy as np
    q = (np.asarray(verts, dtype=np.float64) - offset) * scale / CELL
    return np.clip(np.floor(q), 0, 255).astype(int)


def _leaf(t):
    if t < 32:
        return bytes([0x30 + t])
    if t < 256:
        return bytes([0x50, t])
    if t < 65536:
        return bytes([0x51, t >> 8, t & 255])
    return bytes([0x52, t >> 16, (t >> 8) & 255, t & 255])


class _Code:
    """Relocatable code: bytes + absolute-jump fixups (position of the 4 address bytes, target offset)."""

    def __init__(self):
        self.data = bytearray()
        self.fixups = []

    def add(self, other):
        base = len(self.data)
        self.data += other.data
        self.fixups += [(p + base, t + base) for p, t in other.fixups]


def build(verts, tris, slack=1):
    """-> (code bytes padded to 16 with 0xCD, (ox, oy, oz, scale)). A median-split tree over triangle cell boxes,
    plain-axis tests only (bounds 0x26-0x28, splits 0x10-0x12), absolute 0x70 jumps for big left branches; every
    limit is widened by `slack` cells on both sides (Havok's own trees are exact on the high side)."""
    import numpy as np
    offset, scale = grid(verts)
    c = cells(verts, offset, scale)
    tris = np.asarray(tris)
    return _tree(c[tris].min(1), c[tris].max(1), offset, scale, slack)


def build_boxes(lo, hi, slack=1):
    """A MultiMeshShape's tree: one leaf per member, over the members' world boxes (lo, hi: (n, 3)). Offset = the
    boxes' minimum, scale = 254 * 65536 / longest side - as all 93 merged trees of AC3's stock maps."""
    import numpy as np
    lo, hi = np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64)
    offset, scale = grid(np.vstack([lo, hi]))
    return _tree(cells(lo, offset, scale), cells(hi, offset, scale), offset, scale, slack)


def member_box(local_lo, local_hi, matrix):
    """A merged member's world box: its local box's 8 corners through the entity matrix (row-vector), axis-aligned.
    Matches the offset / scale of all 93 stock merged trees exactly; the exact triangles' box only 50."""
    import numpy as np
    lo, hi = local_lo, local_hi
    cs = np.array([(x, y, z, 1.0) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    w = (cs @ np.asarray(matrix, dtype=np.float64))[:, :3]
    return w.min(0), w.max(0)


def _tree(tlo, thi, offset, scale, slack):
    import numpy as np
    cen = (tlo + thi) / 2.0

    def node(ids, box):
        out = _Code()
        lo = np.maximum(tlo[ids].min(0) - slack, 0)
        hi = np.minimum(thi[ids].max(0) + slack, 255)
        for a in (2, 1, 0):                       # tighten the box where it shrank (Z first, like Havok)
            if lo[a] > box[0][a] or hi[a] < box[1][a]:
                out.data += bytes([0x26 + a, int(lo[a]), int(hi[a])])
        box = (np.maximum(box[0], lo), np.minimum(box[1], hi))
        if len(ids) == 1:
            out.data += _leaf(int(ids[0]))
            return out
        spread = cen[ids].max(0) - cen[ids].min(0)
        a = int(np.argmax(spread))
        order = ids[np.argsort(cen[ids][:, a], kind="stable")]
        half = len(order) // 2
        left, right = order[:half], order[half:]
        if spread[a] > 0:
            la = int(min(thi[left][:, a].max() + slack, 255))
            rb = int(max(tlo[right][:, a].min() - slack, 0))
        else:                                     # no separating axis at this resolution: visit both sides
            la, rb = 255, 0
        lbox = (box[0].copy(), box[1].copy())
        rbox = (box[0].copy(), box[1].copy())
        lbox[1][a] = min(lbox[1][a], la)
        rbox[0][a] = max(rbox[0][a], rb)
        lc, rc = node(left, lbox), node(right, rbox)
        if len(lc.data) <= 255:
            out.data += bytes([0x10 + a, la, rb, len(lc.data)])
            out.add(lc)
            out.add(rc)
        else:
            # the left branch jumps (0x70, absolute) past the right one
            out.data += bytes([0x10 + a, la, rb, 5, 0x70, 0, 0, 0, 0])
            at = len(out.data) - 4
            out.add(rc)
            out.fixups.append((at, len(out.data)))
            out.add(lc)
        return out

    full = (np.zeros(3, int), np.full(3, 255))
    code = node(np.arange(len(tlo)), full)
    for p, t in code.fixups:
        code.data[p:p + 4] = t.to_bytes(4, "big")
    data = bytes(code.data)
    data += bytes([PAD]) * (-len(data) % 16)
    return data, (float(offset[0]), float(offset[1]), float(offset[2]), float(scale))


def query_point(code, cell_xyz):
    """Triangles a point query at integer cell coordinates visits. Plain-axis opcodes as measured (bounds allow the
    one cell of low-side slack); diagonal splits and rescales are treated as 'visit both' / 'pass', so on Havok's
    own trees this over-approximates."""
    found = []
    stack = [(0, 0)]
    x = cell_xyz
    n = len(code)
    while stack:
        i, toff = stack.pop()
        while i < n:
            op = code[i]
            if 0x30 <= op < 0x50:
                found.append(op - 0x30 + toff)
                break
            if op == 0x50:
                found.append(code[i + 1] + toff)
                break
            if op == 0x51:
                found.append((code[i + 1] << 8 | code[i + 2]) + toff)
                break
            if op == 0x52:
                found.append((code[i + 1] << 16 | code[i + 2] << 8 | code[i + 3]) + toff)
                break
            if op == 0x53:
                found.append(int.from_bytes(code[i + 1:i + 5], "big") + toff)
                break
            if op == 0x09:
                toff += code[i + 1]
                i += 2
            elif op == 0x0A:
                toff += code[i + 1] << 8 | code[i + 2]
                i += 3
            elif op == 0x0B:
                toff = int.from_bytes(code[i + 1:i + 5], "big")
                i += 5
            elif op == 0x05:
                i += 2 + code[i + 1]
            elif op == 0x06:
                i += 3 + (code[i + 1] << 8 | code[i + 2])
            elif op == 0x70:
                i = int.from_bytes(code[i + 1:i + 5], "big")
            elif 0x10 <= op <= 0x12:
                a, b, j = code[i + 1], code[i + 2], code[i + 3]
                c = x[op - 0x10]
                if c >= b - 1:
                    stack.append((i + 4 + j, toff))
                if c <= a:
                    i += 4
                else:
                    break
            elif 0x13 <= op <= 0x1C:
                stack.append((i + 4 + code[i + 3], toff))
                i += 4
            elif 0x20 <= op <= 0x22:
                a, j = code[i + 1], code[i + 2]
                c = x[op - 0x20]
                if c >= a - 1:
                    stack.append((i + 3 + j, toff))
                if c <= a + 1:
                    i += 3
                else:
                    break
            elif 0x23 <= op <= 0x25:
                stack.append((i + 7 + (code[i + 5] << 8 | code[i + 6]), toff))
                i += 7 + (code[i + 3] << 8 | code[i + 4])
            elif 0x26 <= op <= 0x28:
                c = x[op - 0x26]
                if c < code[i + 1] - 1 or c > code[i + 2]:
                    break
                i += 3
            elif 0x01 <= op <= 0x04:
                # zoom (not decoded yet): stop testing below it, i.e. accept everything under it
                found += _everything_below(code, i + 4, toff)
                break
            else:
                raise MoppError(f"unknown opcode 0x{op:02X} at {i}")
    return found


def _everything_below(code, start, toff):
    out, _h, _s = walk(code, start=start)
    return [t + toff for t, _p in out]
