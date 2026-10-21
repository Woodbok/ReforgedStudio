"""forgelib/maps/filler_dedupe.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import re
import struct
import sys
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import convert
import filler_clip
import mapwalk
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
FILLERS = '^(ACFE_Rhodes_Souk_FakeMesh_\\d+_001|ACFE_RHODES_Souk_FakeMesh_\\d+_001|ACFE_RHO_Fakemesh_KnightHospital_001|AFCE_RHO_Matte_01A_002)$'
TILE = re.compile('FakeMesh_\\d+_001$', re.I)
HALF = 64.0

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--fillers', default=FILLERS, help="regex: the filler Entities' names")
    a = ap.parse_args(argv)
    cls = convert.class_ids()
    bv = struct.pack('<I', cls['BoundingVolume'])
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        raw_before = {r.index: f.read(r) for r in f.records}
        payloads = {r.index: mapwalk.Payload(r, raw_before[r.index]) for r in f.records if r.size and r.name != 'GlobalMetaFile'}
    objs = [o for p in payloads.values() for o in p.objects]
    meshes = {o['uid']: o for o in objs if o['type'] == cls['Mesh'] and o.get('uid') is not None}
    fillers = {}
    for o in objs:
        name = (o['name'] or '').strip()
        if o['type'] != cls['Entity'] or not re.match(a.fillers, name) or name in fillers:
            continue
        pl = filler_clip.placement(o['body'], bv)
        mu = {u for u in (struct.unpack_from('<I', o['body'], i)[0] for i in range(len(o['body']) - 3)) if u in meshes}
        if not pl or len(mu) != 1:
            continue
        m, lo, hi = pl
        mu = mu.pop()
        body = meshes[mu]['body']
        stride, vb, nv, ib, ni = filler_clip.buffers(body)
        unit = math.ceil(max((abs(v) for v in lo + hi))) / 32767
        pos = [filler_clip.to_world(m, [c * unit for c in struct.unpack_from('<3h', body, vb + stride * i)]) for i in range(nv)]
        idx = list(struct.unpack_from(f'<{ni}H', body, ib))
        tile = None
        if TILE.search(name):
            tx, ty = (m[12], m[13])
            tile = (tx - HALF, tx + HALF, ty - HALF, ty + HALF)
        faces = []
        for t in range(0, ni - 2, 3):
            if idx[t] == idx[t + 1] == idx[t + 2]:
                continue
            A, B, C = (pos[idx[t + k]] for k in range(3))
            u = [B[k] - A[k] for k in range(3)]
            v = [C[k] - A[k] for k in range(3)]
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            nl = math.sqrt(sum((x * x for x in n)))
            if nl < 1e-09:
                continue
            faces.append((t, tuple(((A[k] + B[k] + C[k]) / 3 for k in range(3))), tuple((x / nl for x in n)), nl / 2, (A, B, C)))
        fillers[name] = {'mesh': mu, 'idx': idx, 'ib': ib, 'ni': ni, 'tile': tile, 'faces': faces}

    def score(name, c):
        tile = fillers[name]['tile']
        if tile is None:
            return 2
        return 1 if tile[0] <= c[0] < tile[1] and tile[2] <= c[1] < tile[3] else 0

    def sub(p, q):
        return (p[0] - q[0], p[1] - q[1], p[2] - q[2])

    def cross(p, q):
        return (p[1] * q[2] - p[2] * q[1], p[2] * q[0] - p[0] * q[2], p[0] * q[1] - p[1] * q[0])

    def dot(p, q):
        return p[0] * q[0] + p[1] * q[1] + p[2] * q[2]
    grid = defaultdict(list)
    for name, fl in fillers.items():
        for face in fl['faces']:
            A, B, C = face[4]
            xs, ys = ((A[0], B[0], C[0]), (A[1], B[1], C[1]))
            if max(xs) - min(xs) > 80 or max(ys) - min(ys) > 80:
                continue
            for gx in range(int(min(xs) // 4), int(max(xs) // 4) + 1):
                for gy in range(int(min(ys) // 4), int(max(ys) // 4) + 1):
                    grid[gx, gy].append((name, face))
    blank = defaultdict(set)
    area = defaultdict(float)
    same_filler = 0

    def covered(p, n, name, t, own):
        for other, (t2, c2, n2, _ar2, (A2, B2, C2)) in grid.get((int(p[0] // 4), int(p[1] // 4)), ()):
            if other == name and t2 == t or t2 in blank[other] or dot(n, n2) < 0.97:
                continue
            d = dot(sub(p, A2), n2)
            if abs(d) > 0.3:
                continue
            q = (p[0] - d * n2[0], p[1] - d * n2[1], p[2] - d * n2[2])
            if dot(cross(sub(B2, A2), sub(q, A2)), n2) < -1e-06 or dot(cross(sub(C2, B2), sub(q, B2)), n2) < -1e-06 or dot(cross(sub(A2, C2), sub(q, C2)), n2) < -1e-06:
                continue
            if other != name and score(other, q) < own:
                continue
            return other
        return None
    order = sorted(((face[3], name, face) for name, fl in fillers.items() for face in fl['faces']), key=lambda x: (x[0], x[1], x[2][0]))
    for ar, name, (t, c, n, _ar, (A, B, C)) in order:
        own = score(name, c)
        pts = [tuple((A[k] * wa + B[k] * wb + C[k] * wc for k in range(3))) for wa, wb, wc in ((1 / 3, 1 / 3, 1 / 3), (0.6, 0.2, 0.2), (0.2, 0.6, 0.2), (0.2, 0.2, 0.6))]
        by = [covered(p, n, name, t, own) for p in pts]
        if sum((x is not None for x in by)) >= 3:
            blank[name].add(t)
            area[name] += ar
            same_filler += all((x == name for x in by if x is not None))
    report, new_mesh = ([f"{len(fillers)} fillers: {', '.join(sorted(fillers))}"], {})
    for name in sorted(fillers):
        fl = fillers[name]
        if not blank[name]:
            continue
        body = bytearray(meshes[fl['mesh']]['body'])
        idx = fl['idx']
        for t in blank[name]:
            idx[t + 1] = idx[t + 2] = idx[t]
        struct.pack_into(f"<{fl['ni']}H", body, fl['ib'], *idx)
        new_mesh[fl['mesh']] = bytes(body)
        report.append(f"{name}: {len(blank[name])} duplicate faces blanked ({area[name]:.0f} m2), {len(fl['faces']) - len(blank[name])} kept")
    report.append(f'{sum((len(v) for v in blank.values()))} duplicate faces blanked ({same_filler} of them copies inside their own filler); one copy of each surface kept')
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
    ok, n = (True, 0)
    with Forge(dest) as f:
        others = all((f.read(r) == raw_before[r.index] for r in f.records if r.index not in changed))
        for idx_ in changed:
            q = mapwalk.Payload(f.records[idx_], f.read(f.records[idx_]))
            for x, y in zip(payloads[idx_].objects, q.objects):
                ok &= y['body'] == new_mesh.get(x.get('uid'), x['body'])
                n += x.get('uid') in new_mesh
    ok &= others
    report.append(f"verify: {len(new_mesh)} filler meshes edited in place ({n} copies), everything else unchanged {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
