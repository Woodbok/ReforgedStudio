"""forgelib/maps/filler_clip.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import re
import os
import struct
import sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import convert
import leaf_index
import mapwalk
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
MAX_SIDE = 80.0
IGNORE = '_RT_|Palm|Veg|Bush|Leaves|Flower|Tree|GFX_|Crowd'

def buffers(b):
    stride = b[46]
    for first in ((0, 1, 2), (0, 0, 0)):
        for p in range(48, min(len(b) - 8, 8192)):
            v = struct.unpack_from('<I', b, p)[0]
            if not v or v % stride or p + 8 + v > len(b):
                continue
            q = p + 4 + v
            w = struct.unpack_from('<I', b, q)[0]
            if not w or w % 6 or q + 4 + w > len(b):
                continue
            idx = struct.unpack_from(f'<{w // 2}H', b, q + 4)
            if max(idx) < v // stride and idx[:3] == first:
                return (stride, p + 4, v // stride, q + 4, w // 2)
    raise ValueError('no vertex / index buffer found')

def placement(b, bv):
    k = b.rfind(bv)
    if k < 0 or len(b) - k < 28:
        return None
    return (struct.unpack_from('<16f', b, 10), struct.unpack_from('<3f', b, k + 4), struct.unpack_from('<3f', b, k + 16))

def to_world(m, p):
    return tuple((p[0] * m[j] + p[1] * m[4 + j] + p[2] * m[8 + j] + m[12 + j] for j in range(3)))

def world_box(m, lo, hi):
    wc = [to_world(m, (x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    return (tuple((min((c[j] for c in wc)) for j in range(3))), tuple((max((c[j] for c in wc)) for j in range(3))))

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--match', action='append', required=True, help="the filler Entity's exact name (repeatable)")
    ap.add_argument('--margin', type=float, default=0.25, help='box growth in x / y, m')
    ap.add_argument('--zmargin', type=float, default=0.75, help='box growth in z, m')
    ap.add_argument('--rule', choices=('corners', 'centroid'), default='corners', help="blank a face when all three corners lie inside real boxes (default; GA6) or its centroid does (GR1-GA5: the fillers' faces are big, so a centre inside a stall's or a bench's box cut the wall and floor around it: the floating bench behind the fountain)")
    ap.add_argument('--ignore', default=IGNORE, help="regex: entity names whose boxes don't count (default: vegetation, trees, effect / crowd groups)")
    ap.add_argument('--surface', type=float, default=0.5, help="also blank a face lying on a real object's own faces: 3 of 4 sample points within this many metres of a parallel real face, inside it (0: off). GA6's out-of-bounds buildings flickered: the fillers' low-detail copies of real houses, partly outside the houses' boxes (GA7)")
    a = ap.parse_args(argv)
    cls = convert.class_ids()
    bv = struct.pack('<I', cls['BoundingVolume'])
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        raw_before = {r.index: f.read(r) for r in f.records}
        payloads = {r.index: mapwalk.Payload(r, raw_before[r.index]) for r in f.records if r.size and r.name != 'GlobalMetaFile'}
    objs = [(idx, j, o) for idx, p in payloads.items() for j, o in enumerate(p.objects)]
    meshes = {o['uid']: o for _i, _j, o in objs if o['type'] == cls['Mesh'] and o.get('uid') is not None}
    visible = set(meshes) | {o['uid'] for _i, _j, o in objs if o['type'] == cls['LODSelector'] and o.get('uid') is not None}

    def refs(body, pool):
        return {u for u in (struct.unpack_from('<I', body, i)[0] for i in range(len(body) - 3)) if u in pool}
    fillers, boxes, new_ent, reals = ([], [], {}, [])
    mesh_set = set(meshes)
    selectors = {o['uid']: o for _i, _j, o in objs if o['type'] == cls['LODSelector'] and o.get('uid') is not None}
    for _i, _j, o in objs:
        if o['type'] != cls['Entity']:
            continue
        name = (o['name'] or '').strip()
        pl = placement(o['body'], bv)
        if name in a.match:
            if not pl:
                raise SystemExit(f'{name}: no BoundingVolume')
            ks = leaf_index.records(o['body'])
            if len(ks) != 1:
                raise SystemExit(f'{name}: {len(ks)} leaf-index records, expected 1')
            eb = bytearray(o['body'])
            struct.pack_into('<H', eb, ks[0], leaf_index.NONE)
            new_ent[o['uid']] = bytes(eb)
            fillers.append((name, pl, refs(o['body'], set(meshes))))
        elif pl and 'Fake' not in name and (not re.search(a.ignore, name)) and refs(o['body'], visible):
            lo, hi = world_box(*pl)
            if hi[0] - lo[0] < MAX_SIDE and hi[1] - lo[1] < MAX_SIDE:
                boxes.append((lo, hi))
                direct = refs(o['body'], mesh_set)
                via = {u for s in refs(o['body'], selectors) for u in refs(selectors[s]['body'], mesh_set)} if a.surface else set()
                reals.append((lo, hi, pl, direct | via))
    if sorted({n for n, _p, _r in fillers}) != sorted(set(a.match)):
        raise SystemExit(f'found {sorted({n for n, _p, _r in fillers})}, wanted {sorted(set(a.match))}')

    def inside(c):
        return any((lo[0] - a.margin <= c[0] <= hi[0] + a.margin and lo[1] - a.margin <= c[1] <= hi[1] + a.margin and (lo[2] - a.zmargin <= c[2] <= hi[2] + a.zmargin) for lo, hi in boxes))

    def sub(p, q):
        return (p[0] - q[0], p[1] - q[1], p[2] - q[2])

    def cross(p, q):
        return (p[1] * q[2] - p[2] * q[1], p[2] * q[0] - p[0] * q[2], p[0] * q[1] - p[1] * q[0])

    def dot(p, q):
        return p[0] * q[0] + p[1] * q[1] + p[2] * q[2]
    local_cache, tri_grid, decoded, mesh_use = ({}, defaultdict(list), set(), Counter())
    box_grid = defaultdict(list)
    for r_i, (lo, hi, _pl, _mus) in enumerate(reals):
        for gx in range(int(lo[0] // 8), int(hi[0] // 8) + 1):
            for gy in range(int(lo[1] // 8), int(hi[1] // 8) + 1):
                box_grid[gx, gy].append(r_i)

    def local_tris(mu, unit, bound):
        key = (mu, unit)
        if key not in local_cache:
            b = meshes[mu]['body']
            try:
                st, vb_, nv_, ib_, ni_ = buffers(b)
            except ValueError:
                local_cache[key] = None
                return None
            P = [struct.unpack_from('<3h', b, vb_ + st * i) for i in range(nv_)]
            ext = [max((abs(p[k]) for p in P)) * unit for k in range(3)]
            if any((e > bd * 1.2 + 0.3 for e, bd in zip(ext, bound))):
                local_cache[key] = None
            else:
                local_cache[key] = ([tuple((c * unit for c in p)) for p in P], struct.unpack_from(f'<{ni_}H', b, ib_))
        return local_cache[key]

    def index_real(r_i):
        if r_i in decoded:
            return
        decoded.add(r_i)
        _lo, _hi, (m, llo, lhi), mus = reals[r_i]
        unit = math.ceil(max((abs(v) for v in llo + lhi))) / 32767
        bound = [max(abs(llo[k]), abs(lhi[k])) for k in range(3)]
        for mu in mus:
            lt = local_tris(mu, unit, bound)
            mesh_use['decoded' if lt else "left out (unit doesn't fit)"] += 1
            if not lt:
                continue
            P, I = lt
            Wp = [to_world(m, p) for p in P]
            for t in range(0, len(I) - 2, 3):
                A, B, C = (Wp[I[t]], Wp[I[t + 1]], Wp[I[t + 2]])
                n = cross(sub(B, A), sub(C, A))
                nl = math.sqrt(dot(n, n))
                xs, ys = ((A[0], B[0], C[0]), (A[1], B[1], C[1]))
                if nl < 1e-09 or max(xs) - min(xs) > 60 or max(ys) - min(ys) > 60:
                    continue
                n = (n[0] / nl, n[1] / nl, n[2] / nl)
                for gx in range(int(min(xs) // 4), int(max(xs) // 4) + 1):
                    for gy in range(int(min(ys) // 4), int(max(ys) // 4) + 1):
                        tri_grid[gx, gy].append((A, B, C, n))

    def on_real(p, n):
        for A, B, C, n2 in tri_grid.get((int(p[0] // 4), int(p[1] // 4)), ()):
            if abs(dot(n, n2)) < 0.95:
                continue
            d = dot(sub(p, A), n2)
            if abs(d) > a.surface:
                continue
            q = (p[0] - d * n2[0], p[1] - d * n2[1], p[2] - d * n2[2])
            if dot(cross(sub(B, A), sub(q, A)), n2) >= -1e-06 and dot(cross(sub(C, B), sub(q, B)), n2) >= -1e-06 and (dot(cross(sub(A, C), sub(q, C)), n2) >= -1e-06):
                return True
        return False

    def on_surface(A, B, C):
        n = cross(sub(B, A), sub(C, A))
        nl = math.sqrt(dot(n, n))
        if nl < 1e-09:
            return False
        n = (n[0] / nl, n[1] / nl, n[2] / nl)
        xs, ys, zs = ((A[0], B[0], C[0]), (A[1], B[1], C[1]), (A[2], B[2], C[2]))
        near = {r_i for gx in range(int((min(xs) - 1) // 8), int((max(xs) + 1) // 8) + 1) for gy in range(int((min(ys) - 1) // 8), int((max(ys) + 1) // 8) + 1) for r_i in box_grid.get((gx, gy), ())}
        near = [r_i for r_i in near if reals[r_i][0][0] - 1 <= max(xs) and reals[r_i][1][0] + 1 >= min(xs) and (reals[r_i][0][1] - 1 <= max(ys)) and (reals[r_i][1][1] + 1 >= min(ys)) and (reals[r_i][0][2] - 1 <= max(zs)) and (reals[r_i][1][2] + 1 >= min(zs))]
        if not near:
            return False
        for r_i in near:
            index_real(r_i)
        pts = [tuple((A[k] * wa + B[k] * wb + C[k] * wc for k in range(3))) for wa, wb, wc in ((1 / 3, 1 / 3, 1 / 3), (0.6, 0.2, 0.2), (0.2, 0.6, 0.2), (0.2, 0.2, 0.6))]
        return sum((on_real(p, n) for p in pts)) >= 3
    report, new_mesh = ([f'{len(boxes)} real boxes (visible Entities under {MAX_SIDE:.0f} m, no stand-ins, none matching {a.ignore})'], {})
    for name, (m, lo, hi), mesh_uids in fillers:
        if len(mesh_uids) != 1:
            raise SystemExit(f'{name}: {len(mesh_uids)} meshes referenced, expected 1')
        mu = mesh_uids.pop()
        body = bytearray(meshes[mu]['body'])
        stride, vb, nv, ib, ni = buffers(body)
        unit = math.ceil(max((abs(v) for v in lo + hi))) / 32767
        pos = [to_world(m, [c * unit for c in struct.unpack_from('<3h', body, vb + stride * i)]) for i in range(nv)]
        ext = [max((abs(struct.unpack_from('<3h', body, vb + stride * i)[k]) for i in range(nv))) * unit for k in range(3)]
        bound = [max(abs(lo[k]), abs(hi[k])) for k in range(3)]
        if any((abs(e - b) > 0.05 for e, b in zip(ext, bound))):
            raise SystemExit(f"{name}: vertex extent {ext} doesn't match its box {bound}: unit guess wrong")
        idx = list(struct.unpack_from(f'<{ni}H', body, ib))
        cut = kept = surf = 0
        area_cut = area_kept = area_surf = 0.0
        for t in range(0, ni - 2, 3):
            A, B, C = (pos[idx[t + k]] for k in range(3))
            u = [B[k] - A[k] for k in range(3)]
            v = [C[k] - A[k] for k in range(3)]
            area = 0.5 * math.sqrt((u[1] * v[2] - u[2] * v[1]) ** 2 + (u[2] * v[0] - u[0] * v[2]) ** 2 + (u[0] * v[1] - u[1] * v[0]) ** 2)
            if inside(tuple(((A[k] + B[k] + C[k]) / 3 for k in range(3)))) if a.rule == 'centroid' else all((inside(P) for P in (A, B, C))):
                idx[t + 1] = idx[t + 2] = idx[t]
                cut += 1
                area_cut += area
            elif a.surface and on_surface(A, B, C):
                idx[t + 1] = idx[t + 2] = idx[t]
                surf += 1
                area_surf += area
            else:
                kept += 1
                area_kept += area
        struct.pack_into(f'<{ni}H', body, ib, *idx)
        new_mesh[mu] = bytes(body)
        report.append(f'{name}: grid leaf -> FFFF (never leaf-culled)')
        report.append(f"{name}: mesh {meshes[mu]['name'].strip()} ({mu:#010x}), {nv} vertices, {ni // 3} triangles: {cut} blanked ({area_cut:.0f} m2, inside real objects), {surf} blanked ({area_surf:.0f} m2, on real objects' faces), {kept} kept ({area_kept:.0f} m2)")
    if a.surface:
        report.append(f'surface test: real objects decoded {len(decoded)}, meshes {dict(mesh_use)}')
    new_mesh.update(new_ent)
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
    ok &= others
    report.append(f"verify: {len(new_mesh)} objects (meshes + filler entities) edited in place ({n} copies), everything else unchanged {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
