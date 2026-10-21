"""forgelib/ac4/fuzzy_walls.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import collections
import math
import re
import struct
import numpy as np
FUZZY_ZONE = 2355599376
REACH_XY, REACH_UP = (1.0, 4.2)
MANTLE = 2.0
UP_NZ = 0.7
HEADROOM = 1.8
STEEP_NZ = 0.6
LOW_PROP = 1.8
WALL_NZ = 0.3
KEEP_DEFAULT = '(?i)tree|branch|liana|root|palm|banana|mango|jatoba|vine|elevator'
GUIDANCE_SYSTEM = struct.pack('<I', 1437539390)

def _segments(body):
    import world
    return world.segments(body)

def _matrix(seg, at):
    return np.array(struct.unpack_from('<16f', seg, at), dtype=np.float64).reshape(4, 4)

def cfi_shape(seg):
    if len(seg) < 99 or seg[12:14] != b'\x01\x00':
        return None
    return (struct.unpack_from('<I', seg, 14)[0], _matrix(seg, 35 if len(seg) in (103, 212) else 33))

def placements(segs):
    out, owner = ([], None)
    for name, s in segs:
        if name in ('Entity', 'EntityGroup') and len(s) >= 68:
            owner = _matrix(s, 4)
        elif name == 'CollisionFilterInfo' and owner is not None:
            r = cfi_shape(s)
            if r:
                out.append((owner, r[0], r[1]))
    return out

def _unq(q, lo, hi):
    v = (q + 32768) * 0.005
    return min((v + k * 327.68 for k in (-2, -1, 0, 1)), key=lambda x: 0 if lo - 1 <= x <= hi + 1 else abs(x - (lo + hi) / 2) + 1)

def guidance_points(segs):
    out, owner, i = ([], None, 0)
    while i < len(segs):
        name, s = segs[i]
        if name in ('Entity', 'EntityGroup') and len(s) >= 68:
            owner = _matrix(s, 4)
        if name != 'GuidanceSystem' or owner is None:
            i += 1
            continue
        j = i + 1
        while j < len(segs) and segs[j][0] == 'GuidanceObject':
            j += 1
        objs = [segs[k][1] for k in range(i + 1, j)]
        part = next((segs[k][1] for k in range(j, min(j + 4, len(segs))) if segs[k][0] == 'Partitioner'), None)
        i = j
        if not objs or part is None:
            continue
        try:
            cnt = struct.unpack_from('<I', part, 6)[0]
            lo = struct.unpack_from('<3f', part, 10 + 2 * cnt)
            hi = struct.unpack_from('<3f', part, 22 + 2 * cnt)
            n = struct.unpack_from('<I', objs[-1], 33)[0]
            q = struct.unpack_from(f'<{n}h', objs[-1], 37)
        except struct.error:
            continue
        pts = np.array([[_unq(q[k + c], lo[c], hi[c]) for c in range(3)] for k in range(0, n - n % 3, 3)]).reshape(-1, 3)
        if not len(pts):
            continue
        wp = pts @ owner[:3, :3] + owner[3, :3]
        for g in objs:
            if len(g) < 17:
                continue
            a, b = struct.unpack_from('<II', g, 9)
            a, b = (a & 8191, b & 8191)
            if a < len(wp) and b < len(wp):
                for f in (0.0, 0.25, 0.5, 0.75, 1.0):
                    out.append(wp[a] + f * (wp[b] - wp[a]))
    return out

def parse_shape(sb):
    nv = struct.unpack_from('<I', sb, 10)[0]
    V = np.frombuffer(sb, '<f4', 3 * nv, 14).reshape(-1, 3).astype(np.float64)
    q = 14 + 12 * nv
    mo = struct.unpack_from('<I', sb, q)[0]
    q += 4 + mo + 16
    ni = struct.unpack_from('<I', sb, q)[0]
    I = np.frombuffer(sb, '<u2', ni - ni % 3, q + 4).reshape(-1, 3)
    q += 4 + 2 * ni
    nm = struct.unpack_from('<I', sb, q)[0]
    mat_at = q + 4
    M = sb[mat_at:mat_at + nm]
    q = mat_at + nm
    nt = struct.unpack_from('<I', sb, q)[0]
    T = [struct.unpack_from('<I', sb, q + 4 + 6 * i + 2)[0] for i in range(nt)]
    return (V, I, mat_at, M, T, q)

class GuidanceGrid:

    def __init__(self, points):
        self.c = collections.defaultdict(list)
        for p in points:
            q, owner = p if isinstance(p, tuple) else (p, None)
            self.c[math.floor(q[0] / 2), math.floor(q[1] / 2)].append((q, owner))

    def near(self, lo, hi):
        out = set()
        for cx in range(math.floor((lo[0] - REACH_XY) / 2), math.floor((hi[0] + REACH_XY) / 2) + 1):
            for cy in range(math.floor((lo[1] - REACH_XY) / 2), math.floor((hi[1] + REACH_XY) / 2) + 1):
                for p, owner in self.c.get((cx, cy), ()):
                    if lo[0] - REACH_XY <= p[0] <= hi[0] + REACH_XY and lo[1] - REACH_XY <= p[1] <= hi[1] + REACH_XY and (lo[2] - 0.5 <= p[2] <= lo[2] + REACH_UP):
                        out.add(owner)
        return out

class UpFaces:

    def __init__(self):
        self.c = collections.defaultdict(list)

    def add(self, W):
        N = np.cross(W[:, 1] - W[:, 0], W[:, 2] - W[:, 0])
        L = np.linalg.norm(N, axis=1)
        up = (L > 1e-09) & (N[:, 2] > UP_NZ * np.maximum(L, 1e-12)) & np.isfinite(W).all(axis=(1, 2))
        for t in W[up]:
            lo, hi = (t.min(axis=0), t.max(axis=0))
            for cx in range(math.floor(lo[0] / 2), math.floor(hi[0] / 2) + 1):
                for cy in range(math.floor(lo[1] / 2), math.floor(hi[1] / 2) + 1):
                    self.c[cx, cy].append(t)

    def heights(self, x, y):
        out = []
        for t in self.c.get((math.floor(x / 2), math.floor(y / 2)), ()):
            a, b, c = t
            d = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
            if abs(d) < 1e-12:
                continue
            u = ((x - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (y - a[1])) / d
            v = ((b[0] - a[0]) * (y - a[1]) - (x - a[0]) * (b[1] - a[1])) / d
            if u >= -0.0001 and v >= -0.0001 and (u + v <= 1 + 0.0001):
                out.append(a[2] + u * (b[2] - a[2]) + v * (c[2] - a[2]))
        return out

class Walls:

    def __init__(self):
        self.c = collections.defaultdict(list)

    def add(self, W):
        N = np.cross(W[:, 1] - W[:, 0], W[:, 2] - W[:, 0])
        L = np.linalg.norm(N, axis=1)
        v = (L > 1e-09) & (np.abs(N[:, 2]) < WALL_NZ * np.maximum(L, 1e-12)) & np.isfinite(W).all(axis=(1, 2))
        for t in W[v]:
            lo, hi = (t.min(axis=0), t.max(axis=0))
            for cx in range(math.floor(lo[0] / 2), math.floor(hi[0] / 2) + 1):
                for cy in range(math.floor(lo[1] / 2), math.floor(hi[1] / 2) + 1):
                    self.c[cx, cy].append(t)

    def hit(self, o, d, tmax):
        for t in self.c.get((math.floor(o[0] / 2), math.floor(o[1] / 2)), ()):
            e1, e2 = (t[1] - t[0], t[2] - t[0])
            p = np.cross(d, e2)
            det = e1 @ p
            if abs(det) < 1e-12:
                continue
            tv = o - t[0]
            u = tv @ p / det
            if u < 0 or u > 1:
                continue
            q = np.cross(tv, e1)
            v = d @ q / det
            if v < 0 or u + v > 1:
                continue
            if 0 <= e2 @ q / det <= tmax:
                return True
        return False

def _leans_on_wall(tri, n, walls):
    nh = np.array([n[0], n[1], 0.0])
    L = np.linalg.norm(nh)
    if L < 1e-06:
        return False
    nh /= L
    top = tri[np.argmax(tri[:, 2])]
    for dz in (0.3, 0.8):
        if walls.hit(top + np.array([0, 0, dz]) + nh * 0.1, -nh, 0.6):
            return True
    return False

def _climbable(tri, n, ups, grid, owner=None):
    nh = np.array([n[0], n[1]])
    L = np.linalg.norm(nh)
    if L < 1e-06:
        return True
    nh /= L
    b = tri[np.argmin(tri[:, 2])]
    lo, hi = (tri.min(axis=0), tri.max(axis=0))
    top = hi[2]
    f = b[:2] + nh * 0.4
    hs = sorted((h for h in ups.heights(f[0], f[1])))
    floors = [h for i, h in enumerate(hs) if b[2] - 0.3 <= h <= top - 0.3 and all((h2 - h >= HEADROOM or h2 - h <= 0.05 for h2 in hs[i + 1:]))]
    start = max(b[2], min(floors)) if floors else b[2]
    owners = grid.near(np.array([lo[0], lo[1], start]), hi)
    if owners:
        return 'own' if owner in owners else 'other'
    if top - start > MANTLE:
        return False
    tv = tri[tri[:, 2] >= top - 0.1][:, :2].mean(axis=0)
    for d in (0.15, 0.35, 0.6):
        q = tv - nh * d
        if any((abs(h - top) <= 0.3 for h in ups.heights(q[0], q[1]))):
            return 'own'
    return False

def plan(bodies, shape_body, material_name, keep=KEEP_DEFAULT, force=None):
    keep_re = re.compile(keep) if keep else None
    pts = []
    for _n, b in bodies:
        if GUIDANCE_SYSTEM in b:
            pts += [(p, _n) for p in guidance_points(list(_segments(b)))]
    grid = GuidanceGrid(pts)
    per_shape = collections.defaultdict(list)
    for n, b in bodies:
        for m, u, sm in placements(list(_segments(b))):
            per_shape[u].append((sm @ m, n))
    parsed, ups, walls = ({}, UpFaces(), Walls())
    for u, pl in per_shape.items():
        sb = shape_body(u)
        if not sb:
            continue
        try:
            parsed[u] = parse_shape(sb)
        except (struct.error, ValueError):
            continue
        V, I = (parsed[u][0], parsed[u][1])
        for Mw, _n in pl:
            ups.add(V[I] @ Mw[:3, :3] + Mw[3, :3])
            walls.add(V[I] @ Mw[:3, :3] + Mw[3, :3])
    out, stats = ({}, collections.Counter())
    for u, pl in per_shape.items():
        if u not in parsed:
            continue
        V, I, _mat_at, M, T, _q = parsed[u]
        if not len(I) or len(M) < len(I):
            continue
        fuzzy_ok = np.array([material_name(T[x]).startswith(('FuzzyZone', 'Z_Cam')) if x < len(T) else True for x in M[:len(I)]])
        cand, votes, n_pl, own = (None, np.zeros(len(I), int), 0, np.zeros(len(I), bool))
        for Mw, name in pl:
            W = V[I] @ Mw[:3, :3] + Mw[3, :3]
            if not np.isfinite(W).all():
                cand = np.zeros(len(I), bool)
                break
            N = np.cross(W[:, 1] - W[:, 0], W[:, 2] - W[:, 0])
            L = np.linalg.norm(N, axis=1)
            anz = np.abs(N[:, 2]) / np.maximum(L, 1e-12)
            wall = (L > 1e-08) & (anz < WALL_NZ)
            steep = (L > 1e-08) & (anz >= WALL_NZ) & (anz < STEEP_NZ) & (N[:, 2] > 0)
            for t in np.nonzero(steep & ~fuzzy_ok)[0]:
                if np.isfinite(W[t]).all() and _leans_on_wall(W[t], N[t] / L[t], walls):
                    wall[t] = True
                    stats['steep faces leaning on a wall'] += 1
            here = wall & ~fuzzy_ok
            if keep_re and keep_re.search(name):
                here[:] = False
                stats['kept by name'] += 1
            if W[:, :, 2].max() - W[:, :, 2].min() < LOW_PROP:
                here[:] = False
            for t in np.nonzero(here)[0]:
                r = _climbable(W[t], N[t] / max(L[t], 1e-12), ups, grid, name)
                if r:
                    here[t] = False
                    own[t] |= r == 'own'
            votes += here
            n_pl += 1
            cand = here if cand is None else cand
        if cand is not None and n_pl:
            cand = (votes * 2 > n_pl) & ~own
            stats['triangles unclimbable at some placements, kept by the vote'] += int(((votes > 0) & ~cand).sum())
            stats['triangles climbable at some placements, retyped by the vote'] += int((cand & (votes < n_pl)).sum())
        if cand is not None and cand.any():
            out[u] = sorted((int(t) for t in np.nonzero(cand)[0]))
            stats['shapes changed'] += 1
            stats['triangles to FuzzyZone'] += int(cand.sum())
    for u, tl in (force or {}).items():
        if u in parsed:
            out[u] = sorted(set(out.get(u, [])) | set(tl))
            stats['forced triangles'] += len(tl)
    stats['guidance points'] = len(pts)
    return (out, stats)

def apply(sb, tris, fuzzy=FUZZY_ZONE):
    V, I, mat_at, M, T, q = parse_shape(sb)
    b = bytearray(sb)
    if fuzzy in T:
        k = T.index(fuzzy)
    else:
        k = len(T)
        if k > 255:
            raise ValueError('material table full')
        end = q + 4 + 6 * len(T)
        b[end:end] = b'\x01\x00' + struct.pack('<I', fuzzy)
        struct.pack_into('<I', b, q, len(T) + 1)
    for t in tris:
        b[mat_at + t] = k
    return bytes(b)
