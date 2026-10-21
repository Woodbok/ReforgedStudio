"""forgelib/spmaps/sp1_build.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import math
import os
import sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EDITOR = os.path.join(os.path.dirname(ROOT), 'ac3-editor')
for p in (ROOT, HERE, EDITOR):
    if p not in sys.path:
        sys.path.insert(0, p)
import paths
import sp_assets
import sp_topview as T
SP_MAP = 'DataPC_DX11_AC3_Boston.forge'
ARCHIVE = 'DataPC_AC3MP_Fort_Wolcott.forge'

def release_map(name):
    import hashlib
    import json
    if not os.path.exists(os.path.join(ROOT, 'release_set.json')):
        return paths.base_file(name)
    with open(os.path.join(ROOT, 'release_set.json'), encoding='utf-8') as fh:
        for f in json.load(fh)['files']:
            if f['dest'].replace('\\', '/').endswith('/' + name):
                src = os.path.join(ROOT, f['src'])
                h = hashlib.sha256()
                with open(src, 'rb') as fh2:
                    for blk in iter(lambda: fh2.read(1 << 22), b''):
                        h.update(blk)
                if h.hexdigest() != f['sha256']:
                    raise SystemExit(f"{src} is not the release's file (sha256 differs) - release_set.py --check")
                return src
    return os.path.join(paths.AC3_PRISTINE, name)
SRC = release_map(ARCHIVE)
OUT = os.path.join(ROOT, 'out', 'spmaps', 'SP1b_wolcott_bostonhouse_mpclimb', 'DataPC_AC3MP_Fort_Wolcott.forge')
WOOD = bytes.fromhex('54c0fbcd')
HOUSE = 'BOS_CS_House_15x12x8_01A_002'
TAKE = ('BOS_CS_House_15x12x8_01A_002', 'BOS_CS_Roof_15x12x8_01A_002', 'GEN_ALL_Lantern_01A_001')
DEST = (-42.0, -26.0, 4.2)
TURN = 45.0
REMOVE = ('AC3MP_FORT_Crane_01A_001', 'AC3MP_FORT_Crane_02A_001', 'AC3MP_FORT_Platform_CannonStack_01A_001', 'AC3MP_FORT_Warehouse_cratestack_01A_048', 'AC3MP_GEN_Static_Group_Posing_003', 'AC3MP_FORT_Bench_01A_012', 'AC3MP_FORT_Platform_CrateStack_02A_008', 'AC3MP_FORT_Ropes_01A_002', 'AC3MP_FORT_Warehouse_barrels_01A_005')

def placement(house_m):
    c = np.array(house_m[12:15])
    a = math.radians(TURN)
    R = np.eye(4)
    R[0, :2] = (math.cos(a), math.sin(a))
    R[1, :2] = (-math.sin(a), math.cos(a))
    A, B = (np.eye(4), np.eye(4))
    A[3, :3] = -c
    B[3, :3] = DEST
    return A @ R @ B

def models():
    d = T.index(SP_MAP, log=None)
    A = sp_assets.Assets(SP_MAP)
    Ms = T.Meshes(A.f, d['owner'])
    house = min((e for e in d['entities'] if e[0] == HOUSE), key=lambda e: math.hypot(e[2][12] - 197.2, e[2][13] + 499.0))
    hc = np.array(house[2][12:14])
    ents = {}
    for e in d['entities']:
        if e[0] in TAKE:
            dist = float(np.hypot(*np.array(e[2][12:14]) - hc))
            if dist < 30 and (e[0] not in ents or dist < ents[e[0]][0]):
                ents[e[0]] = (dist, e)
    ents = {k: v[1] for k, v in ents.items()}
    missing = set(TAKE) - set(ents)
    if missing:
        raise SystemExit(f'not in {SP_MAP} near the house: {sorted(missing)}')
    P = placement(ents[HOUSE][2])
    out, mats = ([], {})
    for name in TAKE:
        _n, _rec, m, lo, hi, refs = ents[name]
        got = Ms.best(refs, lo, hi)
        if got is None or got[2] != 1.0:
            print(f'  {name}: no static mesh - left out')
            continue
        mu = got[0]
        Mx = np.array(m, dtype=np.float64).reshape(4, 4) @ P
        for part in A.mesh_parts(mu):
            mat = part['material']
            if mat not in mats:
                tu = A.material_texture_uid(mat) if mat else None
                tx = A.texture_rgba(tu, max_size=512) if tu else None
                mo = A.obj(mat) if mat else None
                mats[mat] = (mo['name'] if mo else 'none', tx[1] if tx else None)
            out.append(dict(name=f"SP_{name}_{part['primitive']}", matrix=Mx, verts=part['verts'], tris=np.asarray(part['tris']), uv=part['uv'], sp_material=mat, climb=True))
            print(f"  {name} part {part['primitive']}: {len(part['tris'])} triangles, {mats[mat][0]}")
    return (out, mats)
FIRST_HOLD, HOLD_STEP, HOLD_MARGIN = (3.5, 1.25, 0.3)

def mp_handholds(parts, floor=0.0, gaps=False):
    V = np.concatenate([v for v, _t in parts])
    T = np.concatenate([np.asarray(t) + sum((len(v) for v, _ in parts[:i])) for i, (_v, t) in enumerate(parts)])
    A, B_, C = (V[T[:, 0]], V[T[:, 1]], V[T[:, 2]])
    n = np.cross(B_ - A, C - A)
    area = np.linalg.norm(n, axis=1) / 2
    n = n / np.maximum(np.linalg.norm(n, axis=1)[:, None], 1e-09)
    cen = (A + B_ + C) / 3
    up = np.array([0.0, 0.0, 1.0])
    holds = []
    for ax, sgn in ((0, 1.0), (0, -1.0), (1, 1.0), (1, -1.0)):
        sel = np.nonzero((n[:, ax] * sgn > 0.95) & (np.abs(n[:, 2]) < 0.1))[0]
        if not len(sel):
            continue
        off = np.round(cen[sel, ax] * sgn, 1)
        planes = {}
        for o, a in zip(off, area[sel]):
            planes[o] = planes.get(o, 0.0) + a
        big = {o: a for o, a in planes.items() if a > 20}
        if not big:
            continue
        outer = max(big)
        o = max((p for p in big if p >= outer - 0.3), key=lambda p: big[p])
        on = sel[np.abs(cen[sel, ax] * sgn - o) < 0.08]
        wall = np.zeros(3)
        wall[ax] = sgn
        along = np.cross(up, wall)
        k = int(np.argmax(np.abs(along)))
        plane = float(np.mean(np.r_[A[on, ax], B_[on, ax], C[on, ax]]))
        tri = np.stack([A[on], B_[on], C[on]], 1)
        top = float(tri[:, :, 2].max())
        z = floor + FIRST_HOLD
        while z <= top - 0.4:
            s = []
            for t3 in tri:
                for i in range(3):
                    p, q = (t3[i], t3[(i + 1) % 3])
                    if (p[2] - z) * (q[2] - z) <= 0 and p[2] != q[2]:
                        s.append(p[k] + (q[k] - p[k]) * (z - p[2]) / (q[2] - p[2]))
            spans = [(min(s), max(s))] if len(s) >= 2 else []
            if gaps and spans:
                segs = []
                for t3 in tri:
                    c = [t3[i][k] + (t3[(i + 1) % 3][k] - t3[i][k]) * (z - t3[i][2]) / (t3[(i + 1) % 3][2] - t3[i][2]) for i in range(3) if (t3[i][2] - z) * (t3[(i + 1) % 3][2] - z) <= 0 and t3[i][2] != t3[(i + 1) % 3][2]]
                    if len(c) >= 2:
                        segs.append((min(c), max(c)))
                segs.sort()
                spans = []
                for lo_, hi_ in segs:
                    if spans and lo_ <= spans[-1][1] + 0.05:
                        spans[-1] = (spans[-1][0], max(spans[-1][1], hi_))
                    else:
                        spans.append((lo_, hi_))
            for s_lo, s_hi in spans:
                if s_hi - s_lo <= 2 * HOLD_MARGIN + 0.5:
                    continue
                lo, hi = (s_lo + HOLD_MARGIN, s_hi - HOLD_MARGIN)
                a, b = (np.zeros(3), np.zeros(3))
                a[ax] = b[ax] = plane
                a[2] = b[2] = z
                if along[k] > 0:
                    a[k], b[k] = (lo, hi)
                else:
                    a[k], b[k] = (hi, lo)
                holds.append((a, b, up, wall))
            z += HOLD_STEP
    return holds

def world_boxes(d, pick):
    out = []
    for e in d.entities:
        if not pick(e) or not e.mesh or e.mesh not in d.meshes:
            continue
        v = d.meshes[e.mesh][1]
        W = np.c_[v, np.ones(len(v))] @ np.array(e.matrix).reshape(4, 4)
        out.append((e.name, W[:, :3].min(0), W[:, :3].max(0)))
    return out

def overlaps(path, ground=0.3):
    from ac3editor import mapdata as M
    d = M.load(path, use_cache=False)
    ours = world_boxes(d, lambda e: e.name.startswith('SP_'))
    hits = set()
    for n, a, b in world_boxes(d, lambda e: not e.name.startswith('SP_') and 'WindMesh' not in e.name):
        if (b - a)[:2].max() > 40 or b[2] < ground:
            continue
        for _m, p, q in ours:
            if (a[:2] < q[:2]).all() and (b[:2] > p[:2]).all() and (a[2] < q[2]):
                hits.add(n)
    return sorted(hits)

def free_ground():
    import json
    from PIL import Image
    sys.path.insert(0, os.path.join(ROOT, 'modes'))
    sys.path.insert(0, os.path.join(ROOT, 'maps'))
    import topview
    _png, meta = topview.render(ARCHIVE, SRC)
    m = json.load(open(meta, encoding='utf-8'))
    Hm = np.array(Image.open(m['height_png']).convert('RGB')).astype(np.int64)
    z = (Hm[:, :, 0] * 65536 + Hm[:, :, 1] * 256 + Hm[:, :, 2]) / 100 - 1000
    z[Hm.sum(2) == 0] = np.nan
    k = m['px_per_m']

    def ok(x, y, r):
        X0, X1 = (int((x - r - m['x0']) * k), int((x + r - m['x0']) * k))
        Y0, Y1 = (int((m['y1'] - y - r) * k), int((m['y1'] - y + r) * k))
        w = z[max(Y0, 0):Y1, max(X0, 0):X1]
        return w.size > 0 and (not np.isnan(w).any()) and (float(np.abs(w).max()) < 0.2)
    return ok

def spawn_moves(path, clear=1.5):
    from ac3editor import mapdata as M
    d = M.load(path, use_cache=False)
    ours = world_boxes(d, lambda e: e.name.startswith('SP_'))
    lo = np.min([a for _n, a, _b in ours], 0)
    hi = np.max([b for _n, _a, b in ours], 0)
    ok = free_ground()

    def inside(x, y, pad):
        return any((a[0] - pad <= x <= b[0] + pad and a[1] - pad <= y <= b[1] + pad for _n, a, b in ours))
    moves, seen = ([], set())
    for e in d.entities:
        if not (e.record.startswith('DataBlock_') and ('SPW' in e.name or 'Spawn' in e.name)) or e.uid in seen:
            continue
        seen.add(e.uid)
        m = np.array(e.matrix, dtype=np.float64).reshape(4, 4)
        x, y = (m[3, 0], m[3, 1])
        if not inside(x, y, 0.3):
            continue
        best = None
        for gx in np.arange(lo[0] - 8, hi[0] + 8, 0.5):
            for gy in np.arange(lo[1] - 8, hi[1] + 8, 0.5):
                dd = float(np.hypot(gx - x, gy - y))
                if (best is None or dd < best[0]) and (not inside(gx, gy, clear)) and ok(gx, gy, clear):
                    best = (dd, gx, gy)
        if best is None:
            raise SystemExit(f'no open ground near the house for spawn {e.name}')
        n = m.copy()
        n[3, :3] = (best[1], best[2], 0.0)
        moves.append((e.uid, n, e.name, (round(x, 1), round(y, 1)), (round(best[1], 1), round(best[2], 1))))
    return moves

def removals(path):
    from ac3editor import mapdata as M
    d = M.load(path, use_cache=False)
    out = []
    for e in d.entities:
        if e.record.startswith('DataBlock_'):
            continue
        if e.name in REMOVE or (e.group in REMOVE and '/' in e.name):
            out.append((e.uid, e.name))
    found = {n.split('/')[0] for _u, n in out}
    if set(REMOVE) - found:
        raise SystemExit(f'not in the map: {sorted(set(REMOVE) - found)}')
    return out

def main():
    from ac3editor import mapdata as M, materials, newmodel
    parts, mats = models()
    avoid = [paths.DEFAULT_DATAPC, paths.DEFAULT_EXTRA]
    new = {}
    for k, (u, (name, rgba)) in enumerate(mats.items()):
        if rgba is None:
            continue
        nm = ('SP_' + name)[:60]
        if rgba.shape[2] == 4:
            rgba = rgba.copy()
            rgba[:, :, 3] = 255
        new[f'm{k}'] = dict(name=nm, base=WOOD, rgba=rgba)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    mid = OUT + '.materials.forge'
    made_mats, same1 = materials.apply(SRC, mid, new=new, id_avoid=avoid)
    key_of = {u: f'm{k}' for k, u in enumerate(mats) if f'm{k}' in new}
    models_ = []
    for p in parts:
        k = key_of.get(p['sp_material'])
        models_.append(dict(name=p['name'], matrix=p['matrix'], verts=p['verts'], tris=p['tris'], uv=p['uv'], material=made_mats[k] if k else WOOD, base_material=WOOD, climb=p['climb']))
    from ac3editor import climb
    hp = [p for p in parts if p['name'].startswith(f'SP_{HOUSE}_') or p['name'].startswith('SP_BOS_CS_Roof_15x12x8')]
    M0 = np.asarray(hp[0]['matrix'], np.float64)
    inv = np.linalg.inv(M0)
    frame = [((np.c_[p['verts'], np.ones(len(p['verts']))] @ (np.asarray(p['matrix']) @ inv))[:, :3], p['tris']) for p in hp]
    holds = mp_handholds(frame)
    bv, bt = climb.beams(holds)
    rows = {}
    for a, _b, _u, w in holds:
        rows.setdefault(tuple(np.round(w, 1)), []).append(round(float(a[2]), 2))
    print(f'MP handholds: {len(holds)} rows - ' + '; '.join((f'wall {w}: z {v}' for w, v in rows.items())))
    models_.append(dict(name=f'SP_{HOUSE}_MPHandholds', matrix=M0, verts=bv, tris=np.asarray(bt), uv=None, material=WOOD, visible=False, climb=True))
    staged = OUT + '.models.forge'
    made, same2 = newmodel.add_models(mid, staged, models_, id_avoid=avoid)
    os.remove(mid)
    from ac3editor import collision
    moves = spawn_moves(staged)
    for _u, _m, name, a, b in moves:
        print(f'  spawn {name}: {a} -> {b}')
    gone = removals(staged)
    for _u, name in gone:
        print(f'  removed {name}')
    edits = [(u, 'move', m) for u, m, *_r in moves] + [(u, 'remove') for u, _n in gone]
    if edits:
        if not collision.edit_shapes(staged, OUT, edits, id_avoid=avoid):
            raise SystemExit('moving the spawns / removing the props failed')
        os.remove(staged)
    else:
        os.replace(staged, OUT)
    left = [n for _u, _m, n, *_r in spawn_moves(OUT)]
    print(f"{len(moves)} spawn point(s) moved out of the house; still inside: {left or 'none'}")
    after = {e.uid: e for e in M.load(OUT, use_cache=False).entities}
    ok = 0
    for name, eid in made:
        e = after.get(eid)
        good = e is not None and e.mesh and e.shapes
        ok += bool(good)
        if not good:
            print(f'  {name}: mesh {bool(e and e.mesh)}, collision {bool(e and e.shapes)}')
    print('stock objects the new ones overlap (boxes, above the ground):', ', '.join(overlaps(OUT, DEST[2] + 0.3)) or 'none')
    print(f'{ok} of {len(made)} new objects read back with their mesh and collision; {len(new)} new materials; untouched records byte-identical: {same1 and same2}')
    print(f'-> {OUT}')
    return 0 if ok == len(made) and same1 and same2 else 1
if __name__ == '__main__':
    sys.exit(main())
