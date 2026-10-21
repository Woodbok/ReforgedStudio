"""forgelib/spmaps/sp_verify.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import importlib.util
import os
import re
import struct
import subprocess
import sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import sp_convert as SC
from ac3editor import mapdata as M
FAILS, WARNS = ([], [])

def report(name, ok, msg, warn=False):
    print(f"{('ok  ' if ok else 'WARN' if warn else 'FAIL')} {name:13s} {msg}")
    if not ok:
        (WARNS if warn else FAILS).append(name)

def config(path):
    spec = importlib.util.spec_from_file_location('sp_cfg', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CONFIG

def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    path, cfg = (argv[0], config(argv[1]))
    cv = SC.Convert(cfg)
    pre = cfg.prefix + '_'
    S = M.schema()
    C = M.Codec(S)
    md = M.load(path, use_cache=False, log=lambda *a: None)
    bad_roots, big = ([], [])
    for _rec, name, t, body, _l in M.iter_objects_ex(path):
        if S.name_of(t) not in ('Entity', 'EntityGroup'):
            continue
        try:
            o = C.decode(body).obj
        except Exception:
            continue
        for e in [o] + [M._obj(r) for r in o.fields.get('Entities', [])]:
            for c in e.fields.get('Components', []) if e is not None else []:
                cf = M._obj(c)
                if cf is None or S.name_of(cf.type_hash) != 'GuidanceSystem':
                    continue
                pf = M._obj(cf.fields['Partitioner']).fields
                root = struct.unpack('<H', pf['RootIndex'])[0]
                if root >= len(pf['ListNodes']) and (not (root == 65535 and (not pf['ListNodes']))):
                    bad_roots.append(name)
                n = max(len(cf.fields.get('GuidanceObjects', [])), len(cf.fields.get('CompressPoints', [])) // 3, len(pf.get('LeafsIndex', [])))
                if n > 16383:
                    big.append((name, n))
    report('climb-roots', not bad_roots, f'{len(bad_roots)} systems with a RootIndex past their nodes {bad_roots[:3]}')
    report('climb-arrays', not big, f'{len(big)} systems past 16,383 entries {big[:3]}')
    odd = []
    for _rec, name, t, body, _l in M.iter_objects_ex(path):
        if S.name_of(t) != 'Mesh' or not name.startswith(pre):
            continue
        f = C.decode(body).obj.fields
        if f.get('Category', bytes(4)) != bytes(4) or f.get('FakeMeshGridCount', bytes(2)) != bytes(2) or f.get('UseFakeMeshDrawPrimMasking', b'\x00') != b'\x00':
            odd.append(name)
    report('mesh-headers', not odd, f'{len(odd)} new meshes with a Category / FakeMesh header {odd[:3]}')
    copies = [e.name for e in md.entities if re.search('_ED\\d{3}$', e.name or '') and (not (e.name or '').startswith(pre))]
    report('copies', not copies, f'{len(copies)} copied stock entities {copies[:3]}')
    deep = [e.name for e in md.entities if e.matrix[14] < -500]
    report('sunk-depth', not deep, f'{len(deep)} entities below -500 m {deep[:3]}')
    names = {u: n for u, (n, _d) in md.materials.items()}
    dark = []
    for _rec, name, t, body, _l in M.iter_objects_ex(path):
        if S.name_of(t) != 'Mesh' or not name.startswith(pre):
            continue
        f = C.decode(body).obj.fields
        mats = [names.get(M._link(x), '') for x in f.get('CompiledMeshMaterials', [])]
        if not any((SC.WINDOW.search(m) for m in mats)):
            continue
        mdf = M._obj(f['CompiledMesh']).fields['MeshData'].fields
        fmt, st = (mdf['VertexFormat'][0], mdf['VertexStride'][0])
        if fmt != 3:
            continue
        vb = b''.join(mdf['VertexBufferData'])
        raw = np.frombuffer(vb[:len(vb) // st * st], np.uint8).reshape(-1, st)
        if raw[:, 16:19].mean() < 128:
            dark.append(name)
    report('glass-colour', not dark, f'{len(dark)} window meshes with dark vertex colours {dark[:3]}')
    if cfg.replace_target:
        modes = {e.uid for e in md.entities if e.record.startswith('DataBlock_')}
        left = sorted({e.name for e in md.entities if e.record.startswith('Cell') and e.mesh in md.meshes and (e.uid not in modes) and (not (e.name or '').startswith(cfg.prefix)) and (e.matrix[14] > -50) and (not re.search(cfg.keep, e.name or '', re.I))})
        report('target-left', not left, f"{len(left)} of the target's objects still standing {left[:4]}")
    if cfg.standins_off:
        zs = []
        for _rec, _n, t, body, _l in M.iter_objects_ex(path):
            if S.name_of(t) == 'FakeEntities':
                for fe in C.decode(body).obj.fields['FakeEntities']:
                    e = fe.fields['FakeEntity']
                    eo = e.obj if hasattr(e, 'obj') else e
                    zs.append(struct.unpack('<16f', eo.fields['GlobalMatrix'])[14])
        report('standins', zs and max(zs) < -100, f'stand-in tiles at z {[round(z, 1) for z in zs]} (sunk < -100)')
    if getattr(cfg, 'world_lightmap', None) is not None:
        import dxt
        flat = []
        for _rec, n_, t, body, _l in M.iter_objects_ex(path):
            if S.name_of(t) == 'TextureMap' and 'WorldLightMap' in (n_ or ''):
                w_, h_, px_ = dxt.decode(body)
                a_ = np.asarray(px_, np.uint8).reshape(-1, 4)
                flat.append(int(np.ptp(a_[:, 3])) <= 8 and int(np.ptp(a_[:, :3], axis=0).max()) <= 8)
        report('lightmap', flat and all(flat), f'World lightmap uniform (no target streets / glows): {flat}')
    poly = cv.play_area()
    if cfg.boundary:
        boundaries(md, cv, cfg, path)
    if cfg.tunnels:
        tunnel_points(md, cv, poly, path)
    else:
        floor_holes(md, poly)
    if cfg.under is not None:
        entrances(md, cv, cfg)
    g = cfg.grid
    elsewhere = 0
    for e in md.entities:
        mt = re.match(re.escape(pre) + '(-?\\d+)_(-?\\d+)_', e.name or '')
        if not mt or e.group:
            continue
        col, row = (int(mt.group(1)), int(mt.group(2)))
        dim = int(round(-2 * g[0] / g[2]))
        if e.record != f'Cell{row * dim + col:05d}_DataBlock':
            elsewhere += 1
    report('own-cell', elsewhere == 0, f'{elsewhere} new models filed outside their own cell (no target cell record there)', warn=True)
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'dir_check_build.py'), path], capture_output=True, text=True, cwd=ROOT)
    last = (r.stdout.strip().splitlines() or [''])[-1]
    report('dir-check', r.returncode == 0 and '0 with mismatches, 0 with unmapped' in last, last)
    print(f'\n{len(FAILS)} FAIL, {len(WARNS)} WARN')
    return 1 if FAILS else 0

def boundaries(md, cv, cfg, path):
    from ac3editor import boundary
    A = boundary.areas(path)
    bad = []
    for a in A.values():
        want = cfg.boundary.get(a['name'])
        if want is None or a['kind'] != 'oob':
            continue
        got = np.array(a['corners'], np.float64)
        w = np.array(want, np.float64)
        far = max((min((float(np.linalg.norm(g - q)) for q in w)) for g in got)) if len(got) else 1000000000.0
        if len(got) != len(w) or far > 0.1:
            bad.append(f"{a['name']} ({len(got)} corners, farthest {far:.2f} m)")
    report('boundary', not bad, f'{len(cfg.boundary)} areas redrawn; wrong: {bad[:3]}')
    own = {}
    for a in A.values():
        if a['kind'] == 'oob' and a.get('closed'):
            for r in a['records']:
                own[r] = [tuple(map(float, c)) for c in a['corners']]
    zones = [(a, r) for a in A.values() if a['kind'] == 'zone' for r in a['records']]
    poly = cv.play_area()
    out, n, zbad = ([], 0, [])
    for e in md.entities:
        if not e.record.startswith('DataBlock_') or SC.NOT_POINTS.search(e.name or ''):
            continue
        x, y = (e.matrix[12], e.matrix[13])
        if abs(x) + abs(y) < 0.01 or e.matrix[14] < -100 or (not SC.inside(poly, (x, y))):
            continue
        n += 1
        ar = own.get(e.record, poly)
        if not SC.inside(ar, (x, y)) or SC.edge_dist(ar, (x, y)) < 1.0:
            out.append(f"{e.name} {e.record.replace('DataBlock_', '')} ({x:.1f}, {y:.1f})")
        if re.search('CTF_Flag|CTF_ScoreZone|Domination_?Point|DOM_Point|CapturePoint', e.name or '', re.I):
            mine = [a for a, r in zones if r == e.record]
            if mine and (not any((any((SC.inside([tuple(q) for q in pc], (x, y)) for pc in a['polygons'])) for a in mine))):
                zbad.append(f'{e.name} ({x:.1f}, {y:.1f})')
    report('own-area', not out, f"{n - len(out)} of {n} points inside their block's own area {out[:3]}")
    report('zone-points', not zbad, f'{len(zbad)} flags / capture points outside every zone of their block {zbad[:3]}', warn=True)

def tunnel_points(md, cv, poly, path):
    from ac3editor import boundary, navgen as NG
    own = {}
    for a in boundary.areas(path).values():
        if a['kind'] == 'oob' and a.get('closed'):
            for r in a['records']:
                own[r] = [tuple(map(float, c)) for c in a['corners']]
    tris, ent, _surf = NG.collision(md, -50.0)
    g = SC.walk_grid(tris, ent, cv.cfg.area)
    piece = SC.walk_pieces(g)
    xy = np.c_[(g.gx + 0.5) * 0.5, (g.gy + 0.5) * 0.5]
    off, pieces, n, outside = ([], set(), 0, [])
    for e in md.entities:
        if not e.record.startswith('DataBlock_') or SC.NOT_POINTS.search(e.name or ''):
            continue
        x, y, z = (e.matrix[12], e.matrix[13], e.matrix[14])
        if abs(x) + abs(y) < 0.01 or z < -100 or (not SC.inside(poly, (x, y))):
            continue
        n += 1
        if not SC.inside(own.get(e.record, poly), (x, y)):
            outside.append(f'{e.name} {e.record} ({x:.1f}, {y:.1f})')
        ok = np.nonzero((np.abs(xy[:, 0] - x) <= 0.75) & (np.abs(xy[:, 1] - y) <= 0.75) & (np.abs(g.z - z) <= 0.6))[0]
        if not len(ok):
            off.append(f'{e.name} ({x:.1f}, {y:.1f}, {z:.1f})')
        else:
            pieces.add(int(piece[ok].min()))
    report('tunnel-points', not off, f'{n - len(off)} of {n} game-mode points on walkable floor with headroom {off[:3]}')
    report('own-area', not outside, f"{len(outside)} points outside their block's own out-of-bounds area {outside[:3]}")
    report('one-network', len(pieces) <= 1, f'the points stand in {len(pieces)} player-level piece(s) {sorted(pieces)[:5]} (0 = the largest; {np.bincount(piece)[:3] * 0.25} m2)', warn=True)

def entrances(md, cv, cfg):
    from ac3editor import navgen as NG
    tris, ent, _surf = NG.collision(md, -60.0)
    g = SC.walk_grid(tris, ent, cfg.area)
    piece = SC.walk_pieces(g)
    xy = np.c_[(g.gx + 0.5) * 0.5, (g.gy + 0.5) * 0.5]
    deep = g.z < -3
    net = int(np.bincount(piece[deep]).argmax()) if deep.any() else -1
    print(f'     the network under: piece {net}, {(piece == net).sum() * 0.25:.0f} m2 (of it under -3 m: {((piece == net) & deep).sum() * 0.25:.0f} m2)')
    for k, h in enumerate(cfg.water_holes):
        H = np.asarray(h, np.float64)
        ins = SC.near_poly(xy, H, 0.0) & (xy[:, 0] >= H[:, 0].min()) & (xy[:, 0] <= H[:, 0].max())
        zs = g.z[ins]
        on = piece[ins] == net
        top = ins & (g.z > -1)
        stair = bool((piece[top] == net).any())
        bottom = bool(on[zs < -3].any()) if (zs < -3).any() else False
        bands = sorted({round(float(z)) for z in zs})
        report(f'entrance-{k}', stair or bottom, f'{ins.sum()} samples inside, heights {bands[:12]}; street level joined to the network: {stair}; floor in the network: {bottom}')

def floor_holes(md, poly):
    xs, ys = ([c[0] for c in poly], [c[1] for c in poly])
    res = 0.5
    gx, gy = np.meshgrid(np.arange(min(xs), max(xs), res) + res / 2, np.arange(min(ys), max(ys), res) + res / 2)
    pts = np.c_[gx.ravel(), gy.ravel()]
    ins = SC.near_poly(pts, poly, 0.0)
    covered = np.zeros(len(pts), bool)
    for e in md.entities:
        m = np.array(e.matrix, np.float64).reshape(4, 4)
        for s in e.shapes:
            if s not in md.shapes:
                continue
            _n, v, tr = md.shapes[s]
            if not len(tr):
                continue
            Tw = (v.astype(np.float64) @ m[:3, :3] + m[3, :3])[tr]
            n = np.cross(Tw[:, 1] - Tw[:, 0], Tw[:, 2] - Tw[:, 0])
            up = (n[:, 2] > 0.5 * np.linalg.norm(n, axis=1)) & (Tw[:, :, 2].min(1) < 60) & (Tw[:, :, 2].min(1) > -50)
            for a, b, c in Tw[up]:
                lo, hi = (np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c))
                sel = np.nonzero((pts[:, 0] >= lo[0]) & (pts[:, 0] <= hi[0]) & (pts[:, 1] >= lo[1]) & (pts[:, 1] <= hi[1]) & ~covered)[0]
                if not len(sel):
                    continue
                d = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if abs(d) < 1e-12:
                    continue
                px, py = (pts[sel, 0] - a[0], pts[sel, 1] - a[1])
                u = (px * (c[1] - a[1]) - py * (c[0] - a[0])) / d
                w = ((b[0] - a[0]) * py - (b[1] - a[1]) * px) / d
                covered[sel[(u >= 0) & (w >= 0) & (u + w <= 1)]] = True
    holes = ins & ~covered
    frac = holes.sum() / max(ins.sum(), 1)
    report('floor-holes', frac < 0.01, f'{holes.sum() * res * res:.0f} m2 of the play area ({frac:.1%}) with no upward collision at any height - the void (> 1% fails)')
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
