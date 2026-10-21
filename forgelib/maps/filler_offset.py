"""forgelib/maps/filler_offset.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import re
import struct
import sys
import time
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import convert
import fake_spans
import filler_clip
import filler_dedupe
import mapwalk
import zfight_probe
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
FAKES = 3332013873
MATTE_CM, FILLER_CM, TILES_CM = (4.0, 6.0, 20.0)
DEPTHS = {'AFCE_RHO_Matte_01A_002': MATTE_CM}
MAX_FACTOR = 2.0
NEIGHBOUR = -0.05
COINCIDENT = 0.01
CLEAR, PUSH_TO = (0.04, 0.05)
CLEAR_SELF = 0.025
PASSES = 5
PARALLEL = 0.995
CELL = 4.0
MAX_SIDE = 80.0

def depth_of(name, overrides):
    if name in overrides:
        return overrides[name] / 100
    return DEPTHS.get(name, FILLER_CM) / 100

def sub(p, q):
    return (p[0] - q[0], p[1] - q[1], p[2] - q[2])

def cross(p, q):
    return (p[1] * q[2] - p[2] * q[1], p[2] * q[0] - p[0] * q[2], p[0] * q[1] - p[1] * q[0])

def dot(p, q):
    return p[0] * q[0] + p[1] * q[1] + p[2] * q[2]

def unit_vec(p):
    ln = math.sqrt(dot(p, p))
    return (p[0] / ln, p[1] / ln, p[2] / ln) if ln > 1e-12 else None

def ray_hit(o, u, A, B, C):
    e1, e2 = (sub(B, A), sub(C, A))
    pv = cross(u, e2)
    det = dot(e1, pv)
    if abs(det) < 1e-12:
        return None
    inv = 1 / det
    tv = sub(o, A)
    a = dot(tv, pv) * inv
    if a < -1e-06 or a > 1 + 1e-06:
        return None
    qv = cross(tv, e1)
    b = dot(u, qv) * inv
    if b < -1e-06 or a + b > 1 + 1e-06:
        return None
    return dot(e2, qv) * inv

def samples(A, B, C, area=0.0):
    ws = [(1 / 3, 1 / 3, 1 / 3), (0.6, 0.2, 0.2), (0.2, 0.6, 0.2), (0.2, 0.2, 0.6)]
    k = min(10, int(math.sqrt(area) / 0.5))
    if k >= 3:
        ws += [(i / k, j / k, 1 - i / k - j / k) for i in range(1, k) for j in range(1, k - i)]
    return [tuple((A[m] * wa + B[m] * wb + C[m] * wc for m in range(3))) for wa, wb, wc in ws]

def erode(body, unit, depth, debug=None):
    st, vb, nv, ib, ni = zfight_probe.buffers_any(body)
    raw = [struct.unpack_from('<3h', body, vb + st * i) for i in range(nv)]
    pos = [(p[0] * unit, p[1] * unit, p[2] * unit) for p in raw]
    I = struct.unpack_from(f'<{ni}H', body, ib)
    faces, rec_faces = ([], defaultdict(list))
    grid = defaultdict(list)
    for t in range(0, ni - 2, 3):
        a, b, c = (I[t], I[t + 1], I[t + 2])
        if a == b == c:
            continue
        A, B, C = (pos[a], pos[b], pos[c])
        n = unit_vec(cross(sub(B, A), sub(C, A)))
        if n is None:
            continue
        f = len(faces)
        faces.append((a, b, c, n))
        for r in (a, b, c):
            rec_faces[r].append(f)
        xs, ys = ((A[0], B[0], C[0]), (A[1], B[1], C[1]))
        for gx in range(int(min(xs) // 2), int(max(xs) // 2) + 1):
            for gy in range(int(min(ys) // 2), int(max(ys) // 2) + 1):
                grid[gx, gy].append(f)
    weld = defaultdict(list)
    for r in rec_faces:
        weld[raw[r]].append(r)
    stats = {'records': nv, 'in faces': len(rec_faces), 'moved': 0, 'cancelled': 0, 'capped': 0, 'max_D': 0.0, 'sum_D': 0.0, 'faces': len(faces), 'min_ratio': 9.0, 'max_ratio': 0.0, 'sharp': 0}
    new = bytearray(body)
    for r, fl in rec_faces.items():
        own = [faces[f][3] for f in fl]
        mean = unit_vec(tuple((sum((n[k] for n in own)) for k in range(3))))
        if mean is None:
            stats['cancelled'] += 1
            continue
        pool = list(own)
        for r2 in weld[raw[r]]:
            if r2 != r:
                pool += [faces[f][3] for f in rec_faces[r2] if dot(faces[f][3], mean) > NEIGHBOUR]
        reps = []
        for n in pool:
            if all((dot(n, q) < 0.98 for q in reps)):
                reps.append(n)
        navg = unit_vec(tuple((sum((n[k] for n in reps)) for k in range(3))))
        if navg is None:
            stats['cancelled'] += 1
            continue
        mind = max(1 / MAX_FACTOR, min((dot(n, navg) for n in reps)))
        mind_own = min((dot(n, navg) for n in own))
        if mind_own < 1 / MAX_FACTOR:
            stats['sharp'] += 1
        D = depth / mind
        p, u = (pos[r], (-navg[0], -navg[1], -navg[2]))
        reach = 2 * D + COINCIDENT
        cells = {(int((p[0] + s * u[0] * reach) // 2), int((p[1] + s * u[1] * reach) // 2)) for s in (0, 0.5, 1)}
        cells |= {(gx + dx, gy + dy) for gx, gy in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        tmin = None
        for cell in cells:
            for f in grid.get(cell, ()):
                a, b, c, n = faces[f]
                if dot(n, u) < 0.3:
                    continue
                t = ray_hit(p, u, pos[a], pos[b], pos[c])
                if t is not None and COINCIDENT < t < reach and (tmin is None or t < tmin):
                    tmin = t
        if tmin is not None:
            cap = max(depth / max(mind_own, 1 / MAX_FACTOR), tmin / 2 - 0.005)
            if cap < D:
                D = cap
                stats['capped'] += 1
        ratio = D * mind_own / depth
        if debug is not None:
            debug[r] = (D, ratio, own, reps, D < depth / mind)
        stats['min_ratio'] = min(stats['min_ratio'], ratio)
        stats['max_ratio'] = max(stats['max_ratio'], D / depth)
        q = [raw[r][k] + int(round(-D * navg[k] / unit)) for k in range(3)]
        q = [max(-32767, min(32767, v)) for v in q]
        struct.pack_into('<3h', new, vb + st * r, *q)
        stats['moved'] += 1
        stats['max_D'] = max(stats['max_D'], D)
        stats['sum_D'] += D
    return (bytes(new), stats)

def world_faces(body, m, unit):
    st, vb, nv, ib, ni = zfight_probe.buffers_any(body)
    raw = [struct.unpack_from('<3h', body, vb + st * i) for i in range(nv)]
    W = [filler_clip.to_world(m, (p[0] * unit, p[1] * unit, p[2] * unit)) for p in raw]
    I = struct.unpack_from(f'<{ni}H', body, ib)
    faces = []
    for t in range(0, ni - 2, 3):
        a, b, c = (I[t], I[t + 1], I[t + 2])
        if a == b == c:
            continue
        n = cross(sub(W[b], W[a]), sub(W[c], W[a]))
        nl = math.sqrt(dot(n, n))
        if nl > 1e-09:
            faces.append((a, b, c, (n[0] / nl, n[1] / nl, n[2] / nl), nl / 2))
    return (st, vb, raw, W, faces)

class Surfaces:

    def __init__(self, cell=CELL):
        self.grid = defaultdict(list)
        self.cell = cell
        self.count = 0

    def add(self, W, faces, label, ids=False):
        cell = self.cell
        for i, (a, b, c, n, _ar) in enumerate(faces):
            A, B, C = (W[a], W[b], W[c])
            xs, ys = ((A[0], B[0], C[0]), (A[1], B[1], C[1]))
            if max(xs) - min(xs) > MAX_SIDE or max(ys) - min(ys) > MAX_SIDE:
                continue
            self.count += 1
            for gx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
                for gy in range(int(min(ys) // cell), int(max(ys) // cell) + 1):
                    self.grid[gx, gy].append((A, B, C, n, (label, i) if ids else label))

    def touch(self, p):
        pass

    def nearest(self, p, n, tol, exclude=None):
        self.touch(p)
        best = None
        for A, B, C, n2, label in self.grid.get((int(p[0] // self.cell), int(p[1] // self.cell)), ()):
            if label == exclude or dot(n, n2) < PARALLEL:
                continue
            d = dot(sub(p, A), n2)
            if abs(d) > tol or (best is not None and abs(d) >= abs(best[0])):
                continue
            q = (p[0] - d * n2[0], p[1] - d * n2[1], p[2] - d * n2[2])
            if dot(cross(sub(B, A), sub(q, A)), n2) >= -1e-06 and dot(cross(sub(C, B), sub(q, B)), n2) >= -1e-06 and (dot(cross(sub(A, C), sub(q, C)), n2) >= -1e-06):
                best = (d, label)
        return best

class RealSurfaces(Surfaces):

    def __init__(self, reals, by):
        super().__init__(cell=1.0)
        self.reals, self.by, self.decoded, self.skipped = (reals, by, set(), 0)
        self.boxes = defaultdict(list)
        for i, (lo, hi, _pl, _name, _mus) in enumerate(reals):
            for gx in range(int(lo[0] // CELL), int(hi[0] // CELL) + 1):
                for gy in range(int(lo[1] // CELL), int(hi[1] // CELL) + 1):
                    self.boxes[gx, gy].append(i)

    def touch(self, p):
        for i in self.boxes.get((int(p[0] // CELL), int(p[1] // CELL)), ()):
            if i in self.decoded:
                continue
            lo, hi, pl, name, mus = self.reals[i]
            if not (lo[0] - 1 <= p[0] <= hi[0] + 1 and lo[1] - 1 <= p[1] <= hi[1] + 1 and (lo[2] - 1 <= p[2] <= hi[2] + 1)):
                continue
            self.decoded.add(i)
            m, blo, bhi = pl
            unit = math.ceil(max((abs(v) for v in blo + bhi))) / 32767
            bound = [max(abs(blo[k]), abs(bhi[k])) for k in range(3)]
            for mu in mus:
                try:
                    _st, _vb, raw, W, faces = world_faces(self.by[mu]['body'], m, unit)
                except (ValueError, struct.error, IndexError):
                    self.skipped += 1
                    continue
                ext = [max((abs(q[k]) for q in raw)) * unit for k in range(3)]
                if any((e > bd * 1.2 + 0.3 for e, bd in zip(ext, bound))):
                    self.skipped += 1
                    continue
                self.add(W, faces, name)

class Several(Surfaces):

    def __init__(self, sets):
        super().__init__()
        self.sets = sets

    def nearest(self, p, n, tol, exclude=None):
        hits = [s.nearest(p, n, tol, exclude) for s in self.sets]
        hits = [h for h in hits if h is not None]
        return min(hits, key=lambda h: abs(h[0])) if hits else None

def rigid(m):
    r = [m[0:3], m[4:7], m[8:11]]
    return all((abs(dot(r[i], r[j]) - (1.0 if i == j else 0.0)) < 0.0001 for i in range(3) for j in range(3))) and dot(cross(r[0], r[1]), r[2]) > 0

def is_translation(m):
    return all((abs(m[i] - (1.0 if i in (0, 5, 10) else 0.0)) <= 0.0001 for i in range(11)))

def repair(body, m, unit, label, partners, done, passes=PASSES):
    st, vb, raw, W, faces = world_faces(body, m, unit)
    t = (m[12], m[13], m[14])
    local = [(w[0] - t[0], w[1] - t[1], w[2] - t[2]) for w in W]
    rotated = not is_translation(m)
    moved_recs = set()
    lo = [min((w[k] for w in W)) - CLEAR for k in range(3)]
    hi = [max((w[k] for w in W)) + CLEAR for k in range(3)]
    reverse = [(lab, A, B, C, n, ar) for dlo, dhi, dfaces in done if all((dlo[k] <= hi[k] and dhi[k] >= lo[k] for k in range(3))) for lab, A, B, C, n, ar in dfaces if all((min(A[k], B[k], C[k]) <= hi[k] and max(A[k], B[k], C[k]) >= lo[k] for k in range(3)))]
    pushed, area, labels = (set(), 0.0, Counter())
    rec_faces = defaultdict(list)
    for i, (a, b, c, _n, _ar) in enumerate(faces):
        for r in (a, b, c):
            rec_faces[r].append(i)
    candidates = range(len(faces))
    for _ in range(passes):
        cur = [tuple((q[k] + t[k] for k in range(3))) for q in local]
        own = Surfaces()
        own.add(cur, faces, label, ids=True)
        pushes, first = ({}, {})
        for i in candidates:
            a, b, c, _n, ar = faces[i]
            A, B, C = (cur[a], cur[b], cur[c])
            n = unit_vec(cross(sub(B, A), sub(C, A)))
            if n is None:
                continue
            hits = []
            for p in samples(A, B, C, ar):
                h = partners.nearest(p, n, CLEAR)
                h2 = own.nearest(p, n, CLEAR_SELF, exclude=(label, i))
                if h2 is not None:
                    j, ar2 = (h2[1][1], faces[h2[1][1]][4])
                    if h2[0] > 0.002 or (abs(h2[0]) <= 0.002 and (ar < ar2 or (ar == ar2 and i < j))):
                        if h is None or abs(h2[0]) < abs(h[0]):
                            h = (h2[0], 'itself')
                if h is not None:
                    hits.append(h)
            if len(hits) < (1 if ar < 0.5 else 2):
                continue
            d = sorted((h[0] for h in hits))[len(hits) // 2]
            pushes[i] = max(pushes.get(i, 0.0), PUSH_TO + d)
            first.setdefault(i, hits[0][1])
        for lab, A, B, C, n, ar in reverse:
            for p in samples(A, B, C, ar):
                h = own.nearest(p, n, CLEAR)
                if h is None:
                    continue
                j = h[1][1]
                pushes[j] = max(pushes.get(j, 0.0), PUSH_TO - h[0])
                first.setdefault(j, lab)
        if not pushes:
            break
        move = defaultdict(lambda: [0.0, 0.0, 0.0])
        for i, delta in pushes.items():
            a, b, c, _n, ar = faces[i]
            n = unit_vec(cross(sub(cur[b], cur[a]), sub(cur[c], cur[a])))
            for r in (a, b, c):
                for k in range(3):
                    move[r][k] -= delta * n[k]
            if i not in pushed:
                pushed.add(i)
                area += ar
                labels[first[i]] += 1
        for r, v in move.items():
            ln = math.sqrt(dot(v, v))
            if ln > PUSH_TO + CLEAR:
                v = [x * (PUSH_TO + CLEAR) / ln for x in v]
            local[r] = tuple((local[r][k] + v[k] for k in range(3)))
            moved_recs.add(r)
        candidates = sorted({i for r in move for i in rec_faces[r]})
    new = bytearray(body)
    far = 0.0
    for r in range(len(raw)):
        if rotated:
            if r not in moved_recs:
                continue
            q = [int(round(sum((local[r][j] * m[4 * k + j] for j in range(3))) / unit)) for k in range(3)]
        else:
            q = [int(round(local[r][k] / unit)) for k in range(3)]
        if q != list(raw[r]):
            far = max(far, math.sqrt(sum((((q[k] - raw[r][k]) * unit) ** 2 for k in range(3)))))
            q = [max(-32767, min(32767, v)) for v in q]
            struct.pack_into('<3h', new, vb + st * r, *q)
    labels['max push'] = far
    return (bytes(new), len(pushed), area, labels)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--fillers', default=filler_dedupe.FILLERS, help="regex: the filler Entities' names")
    ap.add_argument('--offset', action='append', default=[], metavar='NAME=CM', help="a filler's depth, cm (overrides)")
    ap.add_argument('--tiles', type=float, default=TILES_CM, help="the World stand-in tiles' depth, cm (0: leave them)")
    ap.add_argument('--no-repair', action='store_true', help='skip the repair pass (diagnostics)')
    a = ap.parse_args(argv)
    t0 = time.time()
    overrides = {}
    for s in a.offset:
        k, v = s.split('=')
        overrides[k] = float(v)
    cls = convert.class_ids()
    bv = struct.pack('<I', cls['BoundingVolume'])
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        raw_before = {r.index: f.read(r) for r in f.records}
        payloads = {r.index: mapwalk.Payload(r, raw_before[r.index]) for r in f.records if r.size and r.name != 'GlobalMetaFile'}
    objs = [o for p in payloads.values() for o in p.objects]
    by = {}
    for o in objs:
        if o.get('uid') is not None:
            by.setdefault(o['uid'], o)
    meshes = {u: o for u, o in by.items() if o['type'] == cls['Mesh']}
    sels = {u: o for u, o in by.items() if o['type'] == cls['LODSelector']}

    def refs(body, pool):
        seen = []
        for u in (struct.unpack_from('<I', body, i)[0] for i in range(len(body) - 3)):
            if u in pool and u not in seen:
                seen.append(u)
        return seen

    def mesh_ref(body):
        mu = refs(body, meshes)
        return mu[0] if len(mu) == 1 else None

    def translation_only(name, m):
        if any((abs(m[i] - (1.0 if i in (0, 5, 10) else 0.0)) > 0.0001 for i in range(11))) and (not rigid(m)):
            raise SystemExit(f'{name}: placement is not rigid; erosion assumes an unscaled placement')
    targets, reals = ([], [])
    seen = set()
    for o in objs:
        if o['type'] != cls['Entity']:
            continue
        name = (o['name'] or '').strip()
        pl = filler_clip.placement(o['body'], bv)
        if not pl:
            continue
        if re.match(a.fillers, name):
            mu = mesh_ref(o['body'])
            if name in seen or mu is None:
                continue
            seen.add(name)
            m, lo, hi = pl
            translation_only(name, m)
            unit = math.ceil(max((abs(v) for v in lo + hi))) / 32767
            targets.append((name, 'F', mu, pl, unit, depth_of(name, overrides)))
        elif 'Fake' not in name:
            mus = refs(o['body'], meshes)
            for s in refs(o['body'], sels):
                mus += refs(sels[s]['body'], meshes)
            if mus:
                lo, hi = filler_clip.world_box(*pl)
                if hi[0] - lo[0] < MAX_SIDE and hi[1] - lo[1] < MAX_SIDE:
                    reals.append((lo, hi, pl, name, mus))
    if a.tiles:
        fakes = next((o for o in objs if o['type'] == FAKES), None)
        if fakes is None:
            raise SystemExit('no FakeEntities object')
        fb = fakes['body']
        for s, e, (x, y) in fake_spans.tiles(fb):
            pl = filler_clip.placement(fb[s:e], bv)
            mu = mesh_ref(fb[s:e])
            if not pl or mu is None:
                continue
            m, lo, hi = pl
            translation_only(f'tile ({x},{y})', m)
            unit = math.ceil(max((abs(v) for v in lo + hi))) / 32767
            targets.append((f'stand-in tile ({x},{y})', 'T', mu, pl, unit, a.tiles / 100))
    if len({mu for _l, _k, mu, _p, _u, _d in targets}) != len(targets):
        raise SystemExit('a mesh is shared by two targets')
    report, new_mesh, expect = ([f'{len(targets)} meshes eroded (depth per mesh; every copy edited in place)'], {}, {})
    for label, kind, mu, pl, unit, depth in sorted(targets, key=lambda t: t[5]):
        body, st = erode(meshes[mu]['body'], unit, depth)
        new_mesh[mu] = body
        expect[mu] = (unit, depth, st)
        mean = st['sum_D'] / st['moved'] if st['moved'] else 0
        report.append(f"{label}: mesh {meshes[mu]['name'].strip()} ({mu:#010x}), depth {depth * 100:.0f} cm: {st['moved']} of {st['in faces']} records moved (mean {mean * 100:.1f} cm, max {st['max_D'] * 100:.1f} cm; planes displaced {st['min_ratio']:.2f}-{st['max_ratio']:.2f} x depth), {st['capped']} capped (thin walls), {st['sharp']} sharp, {st['cancelled']} left (front / back in one record); {st['faces']} live faces")
    if not a.no_repair:
        real_s = RealSurfaces(reals, by)
        done_s, done = (Surfaces(), [])
        partners = Several([real_s, done_s])
        for label, kind, mu, pl, unit, _depth in sorted(targets, key=lambda t: (t[1], t[5])):
            body, n_faces, area, labels = repair(new_mesh[mu], pl[0], unit, label, partners, done)
            new_mesh[mu] = body
            far = labels.pop('max push')
            if n_faces:
                top = ', '.join((f'{k} x{v}' for k, v in labels.most_common(3)))
                report.append(f'repair: {label}: {n_faces} faces ({area:.1f} m2) pushed clear of {top}; a record moved at most {far * 100:.0f} cm')
            _st, _vb, _raw, W, faces = world_faces(body, pl[0], unit)
            done_s.add(W, faces, label)
            done.append(([min((w[k] for w in W)) for k in range(3)], [max((w[k] for w in W)) for k in range(3)], [(label, W[a], W[b], W[c], n, ar) for a, b, c, n, ar in faces]))
        report.append(f"repair: real objects decoded {len(real_s.decoded)} of {len(reals)} ({real_s.skipped} meshes skipped: unit doesn't fit), {real_s.count} real faces, {done_s.count} filler / tile faces")
    changed = set()
    for idx_, p in payloads.items():
        if not any((o.get('uid') in new_mesh for o in p.objects)):
            continue
        out = [(o['serial'], mapwalk.object_bytes(o, new_mesh.get(o.get('uid'), o['body'])), o['filter']) for o in p.objects]
        d = mapwalk.rebuild(out, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, idx_, payload)
        changed.add(idx_)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok, n = (bool(new_mesh), 0)
    with Forge(dest) as f:
        others = all((f.read(r) == raw_before[r.index] for r in f.records if r.index not in changed))
        for idx_ in changed:
            q = mapwalk.Payload(f.records[idx_], f.read(f.records[idx_]))
            for x, y in zip(payloads[idx_].objects, q.objects):
                ok &= y['body'] == new_mesh.get(x.get('uid'), x['body'])
                n += x.get('uid') in new_mesh
    geo_ok = True
    for mu, (unit, depth, st) in expect.items():
        b0, b1 = (meshes[mu]['body'], new_mesh[mu])
        s0, vb, nv, _ib, _ni = zfight_probe.buffers_any(b0)
        s1, vb1, nv1, _ib1, _ni1 = zfight_probe.buffers_any(b1)
        geo_ok &= (s0, vb, nv) == (s1, vb1, nv1) and len(b0) == len(b1)
        lim = depth * MAX_FACTOR + PASSES * (PUSH_TO + CLEAR) + unit * 2
        big = 0
        for i in range(nv):
            p0 = struct.unpack_from('<3h', b0, vb + s0 * i)
            p1 = struct.unpack_from('<3h', b1, vb + s0 * i)
            dd = math.sqrt(sum((((p1[k] - p0[k]) * unit) ** 2 for k in range(3))))
            big += dd > lim
        geo_ok &= big == 0
    ok &= others and geo_ok
    report.append(f"verify: {len(new_mesh)} meshes edited in place ({n} copies), same layout and size, every record within 2 x depth (+ repair) of its old position {geo_ok}, everything else unchanged {others}: {('OK' if ok else 'FAILED')} ({time.time() - t0:.0f} s)")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
