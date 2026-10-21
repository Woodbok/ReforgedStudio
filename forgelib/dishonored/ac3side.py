"""forgelib/dishonored/ac3side.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import hashlib
import json
import os
import sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EDITOR = os.path.join(os.path.dirname(ROOT), 'ac3-editor')
for p in (ROOT, EDITOR):
    if p not in sys.path:
        sys.path.insert(0, p)
import paths
FIRST_HOLD, HOLD_STEP, HOLD_MARGIN = (3.5, 1.25, 0.3)
POINTS = ('Chest', 'Artifact', 'ScoringZone', 'Flag', 'AA_Respawn', 'Marker', 'Capture')
PODIUM = ('Podium', 'PODIUM')

def release_map(name):
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

def mp_handholds(parts, floor=0.0, window=1.0, min_run=2.0, planes='outer', gap=0.3):
    V = np.concatenate([v for v, _t in parts])
    T = np.concatenate([np.asarray(t) + sum((len(v) for v, _ in parts[:i])) for i, (_v, t) in enumerate(parts)])
    A, B, C = (V[T[:, 0]], V[T[:, 1]], V[T[:, 2]])
    n = np.cross(B - A, C - A)
    area = np.linalg.norm(n, axis=1) / 2
    n = n / np.maximum(np.linalg.norm(n, axis=1)[:, None], 1e-09)
    cen = (A + B + C) / 3
    up = np.array([0.0, 0.0, 1.0])
    holds = []
    for ax, sgn in ((0, 1.0), (0, -1.0), (1, 1.0), (1, -1.0)):
        sel = np.nonzero((n[:, ax] * sgn > 0.95) & (np.abs(n[:, 2]) < 0.1))[0]
        if not len(sel):
            continue
        off = np.round(cen[sel, ax] * sgn, 1)
        pl = {}
        for o, a in zip(off, area[sel]):
            pl[o] = pl.get(o, 0.0) + a
        big = sorted((o for o, a in pl.items() if a > 20), reverse=True)
        if not big:
            continue
        facades = []
        for o in big:
            if planes != 'all' and facades:
                break
            if not facades or all((o < f - window for f in facades)):
                facades.append(o)
        wall = np.zeros(3)
        wall[ax] = sgn
        along = np.cross(up, wall)
        k = int(np.argmax(np.abs(along)))
        for outer in facades:
            on = sel[(cen[sel, ax] * sgn >= outer - window) & (cen[sel, ax] * sgn <= outer + 0.05)]
            tri = np.stack([A[on], B[on], C[on]], 1)
            offs = cen[on, ax] * sgn
            top = float(tri[:, :, 2].max())
            zmin = tri[:, :, 2].min(1)
            zmax = tri[:, :, 2].max(1)

            def rows_at(z):
                hit = np.nonzero((zmin <= z) & (zmax >= z))[0]
                segs = []
                for i in hit:
                    t3, o = (tri[i], offs[i])
                    s = []
                    for j in range(3):
                        p, q = (t3[j], t3[(j + 1) % 3])
                        if (p[2] - z) * (q[2] - z) <= 0 and p[2] != q[2]:
                            s.append(p[k] + (q[k] - p[k]) * (z - p[2]) / (q[2] - p[2]))
                    if len(s) >= 2:
                        segs.append((round(float(o), 2), min(s), max(s)))
                if not segs:
                    return []
                segs.sort(key=lambda r: r[1])
                runs, cur = ([], [segs[0]])
                for sg in segs[1:]:
                    if planes == 'all' and sg[1] > max((c[2] for c in cur)) + gap:
                        runs.append(cur)
                        cur = [sg]
                    else:
                        cur.append(sg)
                runs.append(cur)
                out = []
                for run in runs:
                    tot = {}
                    for o, s0, s1 in run:
                        tot[o] = tot.get(o, 0.0) + (s1 - s0)
                    good = [o for o, r in tot.items() if r >= min(min_run, 0.6 * (max((c[2] for c in run)) - run[0][1]))]
                    if not good:
                        continue
                    o = max(good)
                    lo = run[0][1] + HOLD_MARGIN
                    hi = max((c[2] for c in run)) - HOLD_MARGIN
                    if hi - lo <= 0.5:
                        continue
                    a, b = (np.zeros(3), np.zeros(3))
                    a[ax] = b[ax] = o * sgn
                    a[2] = b[2] = z
                    if along[k] > 0:
                        a[k], b[k] = (lo, hi)
                    else:
                        a[k], b[k] = (hi, lo)
                    out.append((a, b, up, wall))
                return out
            z = floor + FIRST_HOLD
            while z <= top - 0.4:
                for dz in (0.0, -0.3, 0.3, -0.5, 0.5):
                    h = rows_at(z + dz) if z + dz <= top - 0.4 else []
                    if h:
                        holds += h
                        break
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

def overlaps(path, prefix, ground):
    from ac3editor import mapdata as M
    d = M.load(path, use_cache=False)
    ours = world_boxes(d, lambda e: e.name.startswith(prefix))
    hits = set()
    for n, a, b in world_boxes(d, lambda e: not e.name.startswith(prefix) and 'WindMesh' not in e.name):
        if (b - a)[:2].max() > 40 or b[2] < ground:
            continue
        for _m, p, q in ours:
            if (a[:2] < q[:2]).all() and (b[:2] > p[:2]).all() and (a[2] < q[2]):
                hits.add(n)
    return sorted(hits)

def free_ground(archive, src, level, tol=0.2):
    from PIL import Image
    sys.path.insert(0, os.path.join(ROOT, 'modes'))
    sys.path.insert(0, os.path.join(ROOT, 'maps'))
    import topview
    _png, meta = topview.render(archive, src)
    m = json.load(open(meta, encoding='utf-8'))
    Hm = np.array(Image.open(m['height_png']).convert('RGB')).astype(np.int64)
    z = (Hm[:, :, 0] * 65536 + Hm[:, :, 1] * 256 + Hm[:, :, 2]) / 100 - 1000
    z[Hm.sum(2) == 0] = np.nan
    k = m['px_per_m']

    def ok(x, y, r):
        X0, X1 = (int((x - r - m['x0']) * k), int((x + r - m['x0']) * k))
        Y0, Y1 = (int((m['y1'] - y - r) * k), int((m['y1'] - y + r) * k))
        w = z[max(Y0, 0):Y1, max(X0, 0):X1]
        return w.size > 0 and (not np.isnan(w).any()) and (float(np.abs(w - level).max()) < tol)
    return ok

def spawn_moves(path, prefix, ok, level=0.0, clear=1.5):
    from ac3editor import mapdata as M
    d = M.load(path, use_cache=False)
    ours = world_boxes(d, lambda e: e.name.startswith(prefix))
    lo = np.min([a for _n, a, _b in ours], 0)
    hi = np.max([b for _n, _a, b in ours], 0)

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
        for gx in np.arange(lo[0] - 10, hi[0] + 10, 0.5):
            for gy in np.arange(lo[1] - 10, hi[1] + 10, 0.5):
                dd = float(np.hypot(gx - x, gy - y))
                if (best is None or dd < best[0]) and (not inside(gx, gy, clear)) and ok(gx, gy, clear):
                    best = (dd, gx, gy)
        if best is None:
            raise SystemExit(f'no open ground near the new objects for spawn {e.name}')
        n = m.copy()
        n[3, :3] = (best[1], best[2], level)
        moves.append((e.uid, n, e.name, (round(x, 1), round(y, 1)), (round(best[1], 1), round(best[2], 1))))
    return moves

def mode_points_near(path, box, clear=3.0, podium_clear=8.0):
    from ac3editor import mapdata as M
    d = M.load(path, use_cache=False)
    x0, y0, x1, y1 = box
    out = []
    for e in d.entities:
        if not e.record.startswith('DataBlock_'):
            continue
        is_pt = any((k in e.name for k in POINTS))
        is_pod = any((k in e.name for k in PODIUM))
        if not (is_pt or is_pod):
            continue
        m = np.array(e.matrix).reshape(4, 4)
        x, y = (m[3, 0], m[3, 1])
        r = podium_clear if is_pod else clear
        if x0 - r <= x <= x1 + r and y0 - r <= y <= y1 + r:
            out.append((e.name, round(float(x), 1), round(float(y), 1)))
    return sorted(set(out))

def seam_caps(parts, heights, inset=0.0):
    V = np.concatenate([v for v, _t in parts])
    verts, tris = ([], [])
    for z in heights:
        band = V[np.abs(V[:, 2] - z) < 0.6]
        if not len(band):
            continue
        x0, y0 = band[:, :2].min(0) + inset
        x1, y1 = band[:, :2].max(0) - inset
        k = len(verts)
        zc = z + 0.01
        verts += [(x0, y0, zc), (x1, y0, zc), (x1, y1, zc), (x0, y1, zc)]
        tris += [(k, k + 2, k + 1), (k, k + 3, k + 2)]
    v = np.array(verts, np.float64)
    uv = v[:, :2] / 4.0 - np.floor(v[:, :2].min(0) / 4.0)
    return (v, np.array(tris, np.int64), uv)

def leak_check(parts, side_dist=(1.0, 2.5), eye=1.7, step=0.05):
    V = np.concatenate([v for v, _t in parts])
    T = np.concatenate([np.asarray(t) + sum((len(v) for v, _ in parts[:i])) for i, (_v, t) in enumerate(parts)])
    A, B, C = (V[T[:, 0]], V[T[:, 1]], V[T[:, 2]])
    E1, E2 = (B - A, C - A)
    N = np.cross(E1, E2)
    area = np.linalg.norm(N, axis=1) / 2
    n = N / np.maximum(2 * area[:, None], 1e-12)
    cen = (A + B + C) / 3
    lo = V.min(0)
    sides = {}
    for ax, sgn in ((0, 1.0), (0, -1.0), (1, 1.0), (1, -1.0)):
        sel = np.nonzero((n[:, ax] * sgn > 0.95) & (np.abs(n[:, 2]) < 0.1))[0]
        planes = {}
        for o, a in zip(np.round(cen[sel, ax] * sgn, 1), area[sel]):
            planes[o] = planes.get(o, 0.0) + a
        big = [o for o, a in planes.items() if a > 20]
        if big:
            on = sel[np.isin(np.round(cen[sel, ax] * sgn, 1), big)]
            fv = np.concatenate([A[on], B[on], C[on]])
            sides[ax, sgn] = (min(big), max(big), fv)
    if len(sides) < 4:
        return {}
    inner_lo = np.array([-sides[0, -1.0][0] + 0.8, -sides[1, -1.0][0] + 0.8, lo[2] + 3.0])
    inner_hi = np.array([sides[0, 1.0][0] - 0.8, sides[1, 1.0][0] - 0.8, min((f[:, 2].max() for _a, _b, f in sides.values())) - 0.5])

    def cast(o, d):
        s = o - A
        q = np.cross(s, E1)
        tq = (q * E2).sum(1)
        out = []
        for dd in np.array_split(d, max(1, len(d) // 300)):
            p = np.cross(dd[:, None, :], E2[None])
            det = (E1[None] * p).sum(2)
            inv = 1 / np.where(np.abs(det) < 1e-12, 1e-12, det)
            u = (s[None] * p).sum(2) * inv
            v = dd @ q.T * inv
            t = tq[None] * inv
            out.append(((dd @ N.T < 0) & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0.0001)).any(1))
        return np.concatenate(out)

    def through_inner(o, d):
        with np.errstate(divide='ignore', invalid='ignore'):
            t0 = (inner_lo - o) / d
            t1 = (inner_hi - o) / d
        tmin = np.nanmax(np.minimum(t0, t1), 1)
        tmax = np.nanmin(np.maximum(t0, t1), 1)
        return (tmax > tmin) & (tmax > 0)
    leaks = {}
    for (ax, sgn), (_inner, outer, fv) in sides.items():
        k = 1 - ax
        span = np.arange(fv[:, k].min() + 0.6, fv[:, k].max() - 0.59, 0.5)
        zs = np.arange(lo[2] + 2.0, fv[:, 2].max(), step)
        zz, aa = np.meshgrid(zs, span)
        tgt = np.zeros((zz.size, 3))
        tgt[:, ax] = sgn * outer
        tgt[:, k] = aa.ravel()
        tgt[:, 2] = zz.ravel()
        found = set()
        for dist in side_dist:
            for along in (span[len(span) // 4], span[len(span) // 2], span[3 * len(span) // 4]):
                o = np.zeros(3)
                o[ax] = sgn * (outer + dist)
                o[k] = along
                o[2] = lo[2] + eye
                d = tgt - o
                d /= np.linalg.norm(d, axis=1)[:, None]
                bad = ~cast(o, d) & through_inner(o, d)
                for t_ in tgt[bad]:
                    found.add((round(float(t_[k]), 2), round(float(t_[2]), 2)))
        leaks[('+' if sgn > 0 else '-') + 'xy'[ax]] = sorted(found)
    return leaks

def patch_objects(src, dest, edit, log=print):
    from ac3editor import mapdata as M
    from datafile import compress, decompress
    from forge_write import ArchiveImage, set_payload, write
    from transplant import object_at
    S = M.schema()
    C = M.Codec(S)
    image = ArchiveImage.read(src)
    with M.Forge(src) as f:
        raw = {r.index: f.read(r) for r in f.records}
        recs = [r for r in f.records if r.name != 'GlobalMetaFile' and len(raw[r.index]) > 8]
    changed, count = (set(), 0)
    for rec in recs:
        d = decompress(M.mapwalk.split_prefix(raw[rec.index])[1])
        entries, table = M.mapwalk.walk(d)
        objs, touched = ([], False)
        for off, size, serial, filt in entries:
            o = object_at(d, off, size)
            body = o['body']
            cname = S.name_of(o['type'])
            if cname in ('LODSelector', 'World'):
                try:
                    root = C.decode(body)
                except Exception:
                    root = None
                if root is not None and edit(cname, o.get('name', ''), root):
                    body = C.encode(root)
                    touched = True
                    count += 1
            objs.append((serial, M.mapwalk.object_bytes(o, body), None if filt == M.mapwalk.NO_FILTER else filt))
        if touched:
            nd = M.mapwalk.rebuild(objs, table)
            pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
            assert decompress(pl) == nd
            set_payload(image, rec.index, pl)
            changed.add(rec.index)
    write(dest, image)
    with M.Forge(dest) as f:
        same = all((f.read(r) == raw[r.index] for r in f.records if r.index not in changed))
    log(f'{dest}: {count} objects patched in {len(changed)} records; other records byte-identical: {same}')
    return (count, same)

def far_lods(names, far=2000.0, backdrop=(), backdrop_far=5000.0):
    import struct as _st
    from ac3editor import mapdata as M

    def edit(cname, name, root):
        if cname == 'World':
            g = root.obj.fields['GridLayout'].fields
            g['DefaultLoadingDistance'] = bytes([255])
            g['DefaultViewingDistance'] = bytes([255])
            return True
        base = name[:-len('_LODSelector')] if name.endswith('_LODSelector') else None
        if base is None or base not in names:
            return False
        dist = backdrop_far if base in backdrop else far
        descs = [M._obj(x).fields for x in root.obj.fields['LODDescs']]
        mesh = next((f['Object'].id for f in descs if f['Object'].id and f['Object'].id != bytes(4)), None)
        if mesh is None:
            return False
        last = len(descs) - 1
        for k, f in enumerate(descs):
            has = bool(f['Object'].id and f['Object'].id != bytes(4))
            if has:
                f['SwitchDistance'] = _st.pack('<f', dist + k)
            elif k < last:
                f['SwitchDistance'] = _st.pack('<f', dist + k)
        return True
    return edit
