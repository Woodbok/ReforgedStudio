"""forgelib/maps/zfight_probe.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import struct
import sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import convert
import fake_spans
import filler_clip
import mapwalk
from forge import Forge
OUT = os.path.join(ROOT, 'out', 'maps')

def buffers_any(b):
    if len(b) < 64 or not b[46]:
        raise ValueError('no vertex stride')
    try:
        return filler_clip.buffers(b)
    except ValueError:
        pass
    stride = b[46]
    for p in range(48, len(b) - 8):
        v = struct.unpack_from('<I', b, p)[0]
        if not v or v % stride or p + 8 + v > len(b):
            continue
        q = p + 4 + v
        w = struct.unpack_from('<I', b, q)[0]
        if not w or w % 6 or q + 4 + w > len(b):
            continue
        idx = struct.unpack_from(f'<{w // 2}H', b, q + 4)
        if max(idx) < v // stride and len(set(idx)) > v // stride // 2:
            return (stride, p + 4, v // stride, q + 4, w // 2)
    raise ValueError('no buffers')

def sub(p, q):
    return (p[0] - q[0], p[1] - q[1], p[2] - q[2])

def cross(p, q):
    return (p[1] * q[2] - p[2] * q[1], p[2] * q[0] - p[0] * q[2], p[0] * q[1] - p[1] * q[0])

def dot(p, q):
    return p[0] * q[0] + p[1] * q[1] + p[2] * q[2]

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--entity', required=True)
    ap.add_argument('--tol', type=float, default=0.05, help='plane distance, m')
    ap.add_argument('--radius', type=float, default=6.0)
    a = ap.parse_args(argv)
    cls = convert.class_ids()
    bv = struct.pack('<I', cls['BoundingVolume'])
    objs = []
    with Forge(os.path.join(OUT, a.build, a.archive)) as f:
        for r in f.records:
            if not r.size or r.name == 'GlobalMetaFile':
                continue
            try:
                objs += [(r.name, o) for o in mapwalk.Payload(r, f.read(r)).objects]
            except Exception:
                pass
    by = {}
    for _rn, o in objs:
        if o.get('uid') is not None:
            by.setdefault(o['uid'], o)
    mesh_set = {u for u, o in by.items() if o['type'] == cls['Mesh']}
    sels = {u: o for u, o in by.items() if o['type'] == cls['LODSelector']}

    def refs(body, pool):
        seen = []
        for u in (struct.unpack_from('<I', body, i)[0] for i in range(len(body) - 3)):
            if u in pool and u not in seen:
                seen.append(u)
        return seen

    def meshes_of(body):
        out = [(m, 'mesh') for m in refs(body, mesh_set)]
        for s in refs(body, set(sels)):
            out += [(m, f'LOD{j}') for j, m in enumerate(refs(sels[s]['body'], mesh_set))]
        return out

    def world_tris(body_or_pl, mus, pl=None):
        m, lo, hi = pl
        unit = math.ceil(max((abs(v) for v in lo + hi))) / 32767
        bound = [max(abs(lo[k]), abs(hi[k])) for k in range(3)]
        out = {}
        for mu, tag in mus:
            b = by[mu]['body']
            try:
                st, vb, nv, ib, ni = buffers_any(b)
            except ValueError:
                out[mu, tag] = None
                continue
            P = [struct.unpack_from('<3h', b, vb + st * i) for i in range(nv)]
            ext = [max((abs(p[k]) for p in P)) * unit for k in range(3)]
            if any((e > bd * 1.2 + 0.3 for e, bd in zip(ext, bound))):
                out[mu, tag] = None
                continue
            W = [filler_clip.to_world(m, [c * unit for c in p]) for p in P]
            I = struct.unpack_from(f'<{ni}H', b, ib)
            tris = []
            for t in range(0, ni - 2, 3):
                if I[t] == I[t + 1] == I[t + 2]:
                    continue
                A, B, C = (W[I[t]], W[I[t + 1]], W[I[t + 2]])
                n = cross(sub(B, A), sub(C, A))
                nl = math.sqrt(dot(n, n))
                if nl > 1e-09:
                    tris.append((A, B, C, (n[0] / nl, n[1] / nl, n[2] / nl), nl / 2))
            out[mu, tag] = tris
        return out
    target = next((o for _rn, o in objs if o['type'] == cls['Entity'] and (o['name'] or '').strip() == a.entity), None)
    if target is None:
        raise SystemExit(f'no Entity {a.entity}')
    tpl = filler_clip.placement(target['body'], bv)
    tlo, thi = filler_clip.world_box(*tpl)
    mine = world_tris(target['body'], meshes_of(target['body']), tpl)
    print(f'{a.entity}: box {[round(v, 1) for v in tlo]} .. {[round(v, 1) for v in thi]}; meshes ' + ', '.join((f"{by[mu]['name'].strip()} ({tag}, {('skipped' if t is None else len(t))} faces)" for (mu, tag), t in mine.items())))
    partners, seen_uid = ([], {target['uid']})
    for rn, o in objs:
        if o['type'] != cls['Entity'] or o['uid'] in seen_uid:
            continue
        pl = filler_clip.placement(o['body'], bv)
        if not pl:
            continue
        lo, hi = filler_clip.world_box(*pl)
        if any((lo[k] > thi[k] + a.radius or hi[k] < tlo[k] - a.radius for k in range(3))):
            continue
        seen_uid.add(o['uid'])
        partners.append(((o['name'] or '').strip() + f' [{rn}]', o['body'], pl))
    fakes = next((o for _rn, o in objs if o['type'] == 3332013873), None)
    if fakes:
        fb = fakes['body']
        for s, e, (x, y) in fake_spans.tiles(fb):
            pl = filler_clip.placement(fb[s:e], bv)
            if pl and refs(fb[s:e], mesh_set):
                partners.append((f'stand-in tile ({x},{y})', fb[s:e], pl))

    def covered(p, n, tris):
        for A, B, C, n2, _ar in tris:
            if dot(n, n2) < 0.97:
                continue
            d = dot(sub(p, A), n2)
            if abs(d) > a.tol:
                continue
            q = (p[0] - d * n2[0], p[1] - d * n2[1], p[2] - d * n2[2])
            if dot(cross(sub(B, A), sub(q, A)), n2) >= -1e-06 and dot(cross(sub(C, B), sub(q, B)), n2) >= -1e-06 and (dot(cross(sub(A, C), sub(q, C)), n2) >= -1e-06):
                return True
        return False
    results, skipped = (Counter(), Counter())
    for pname, body, pl in partners:
        theirs = world_tris(body, meshes_of(body), pl)
        for (mu2, tag2), t2 in theirs.items():
            if t2 is None:
                skipped[pname] += 1
                continue
            near = [t for t in t2 if not (max(t[0][0], t[1][0], t[2][0]) < tlo[0] - 1 or min(t[0][0], t[1][0], t[2][0]) > thi[0] + 1 or max(t[0][1], t[1][1], t[2][1]) < tlo[1] - 1 or (min(t[0][1], t[1][1], t[2][1]) > thi[1] + 1))]
            if not near:
                continue
            for (mu, tag), t1 in mine.items():
                if not t1:
                    continue
                area = 0.0
                for A, B, C, n, ar in t1:
                    pts = [tuple((A[k] * wa + B[k] * wb + C[k] * wc for k in range(3))) for wa, wb, wc in ((1 / 3, 1 / 3, 1 / 3), (0.6, 0.2, 0.2), (0.2, 0.6, 0.2), (0.2, 0.2, 0.6))]
                    if sum((covered(p, n, near) for p in pts)) >= 3:
                        area += ar
                if area > 0.01:
                    results[pname, by[mu2]['name'].strip(), tag2, tag] = area
    print(f"partners checked: {len(partners)}; meshes skipped (unit doesn't fit): {sum(skipped.values())}")
    if not results:
        print("no surface of another object lies on this one's faces within", a.tol, 'm')
    for (pname, mname, tag2, tag), area in results.most_common(30):
        print(f'  {area:7.2f} m2 of its {tag} faces lie on {pname[:60]} / {mname} ({tag2})')
if __name__ == '__main__':
    main(sys.argv[1:])
