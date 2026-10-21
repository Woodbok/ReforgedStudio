"""forgelib/witcher3/topview.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import os
import pickle
import re
import sys
import time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'spmaps'), os.path.join(ROOT, 'modes')):
    if p not in sys.path:
        sys.path.insert(0, p)
from witcher3 import bundles, meshes, scene, terrain
TOPVIEW = os.path.join(bundles.OUT, 'topview')
CACHE_VERSION = 3
MAX_SIDE = 600.0
WORLD_LAYERS = {'kaer_morhen': 'levels\\kaer_morhen\\', 'novigrad': 'levels\\novigrad\\', 'skellige': 'levels\\skellige\\', 'prolog_village': 'levels\\prolog_village\\', 'prolog_village_winter': 'levels\\prolog_village_winter\\', 'wyzima_castle': 'levels\\wyzima_castle\\', 'island_of_mist': 'levels\\island_of_mist\\', 'the_spiral': 'levels\\the_spiral\\', 'bob': 'dlc\\bob\\data\\levels\\bob\\'}
SKIP = re.compile('^sounds\\\\|^fx\\\\|\\\\fx\\\\|_proxy|\\\\decals?\\\\(?!hay\\\\|cobweb)|_decal|invisible|blocker|occluder|_shadow\\b|_shadow_?mesh|\\\\shadow_mesh_|collision_only|volume|weather_volume|blockout|explorations_reference|^engine\\\\', re.I)
GREEN = re.compile('vegetation|\\\\trees?\\\\|_tree|bush|\\\\ivy|_ivy|grass|plant|fern|moss|leaves|branch|roots|flower', re.I)

def index(world, log=print):
    cp = os.path.join(bundles.CACHE, 'place_%s.pkl' % world)
    stamp = (CACHE_VERSION, len(bundles.index()))
    if os.path.exists(cp):
        with open(cp, 'rb') as f:
            d = pickle.load(f)
        if d.get('stamp') == stamp:
            return d
    t0 = time.time()
    lays = scene.layers(WORLD_LAYERS[world])
    paths, at, rows = ([], {}, [])
    for li, l in enumerate(lays):
        try:
            ps = scene.layer_placements(l, kinds=(1, 7))
        except Exception as ex:
            log('  %s: %r' % (l, ex))
            continue
        for p in ps:
            if p.path is None or p.box is None:
                continue
            if p.path not in at:
                at[p.path] = len(paths)
                paths.append(p.path)
            lo, hi = p.world_box()
            rows.append(np.r_[at[p.path], li, p.matrix.ravel(), p.pos, lo, hi])
        if log and li % 500 == 0:
            log('  %d / %d layers, %d placements, %.0f s' % (li, len(lays), len(rows), time.time() - t0))
    try:
        infos = scene.layer_infos(terrain.WORLDS[world])
    except Exception:
        infos = {}
    shown = [infos.get(l, {}).get('visible', True) and infos.get(l, {}).get('tag') != 'LBT_Ignored' for l in lays]
    d = {'stamp': stamp, 'paths': paths, 'layers': lays, 'shown': shown, 'rows': np.array(rows, np.float64).reshape(-1, 20)}
    with open(cp, 'wb') as f:
        pickle.dump(d, f, protocol=4)
    if log:
        log('%s: %d layers, %d placements of %d meshes, %.0f s' % (world, len(lays), len(rows), len(paths), time.time() - t0))
    return d

def stem(world):
    return 'w3_' + world

def hillshade(h, step):
    gy, gx = np.gradient(np.nan_to_num(h, nan=np.nanmin(h) if np.isfinite(h).any() else 0), step)
    n = np.stack([-gx, -gy, np.ones_like(gx)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return 0.35 + 0.65 * np.maximum(0, n @ np.array([-0.45, 0.45, 0.77]))

def overview(world, px_per_m=0.5):
    from PIL import Image, ImageDraw
    d = index(world)
    R = d['rows']
    keep = np.array([not SKIP.search(p) for p in d['paths']])[R[:, 0].astype(int)]
    keep &= np.array(d['shown'])[R[:, 1].astype(int)]
    lo, hi = (R[keep, 14:17], R[keep, 17:20])
    size = (hi - lo)[:, :2].max(1)
    sel = size < 300
    lo, hi = (lo[sel], hi[sel])
    x0, y0 = (np.percentile(lo[:, 0], 0.2) - 50, np.percentile(lo[:, 1], 0.2) - 50)
    x1, y1 = (np.percentile(hi[:, 0], 99.8) + 50, np.percentile(hi[:, 1], 99.8) + 50)
    w, h = (int((x1 - x0) * px_per_m), int((y1 - y0) * px_per_m))
    img = Image.new('RGB', (w, h), (20, 22, 24))
    try:
        T = terrain.Terrain(world)
        mip = int(np.clip(np.floor(np.log2(1.0 / (px_per_m * T.tile_size / T.res))), 0, 3))
        H, hx, hy, s = T.region((x0, y0, x1, y1), mip)
        sh = hillshade(H, s)
        zt = np.clip((H - np.nanpercentile(H, 2)) / max(1, np.nanpercentile(H, 98) - np.nanpercentile(H, 2)), 0, 1)
        rgb = np.stack([70 + 60 * zt, 80 + 60 * zt, 60 + 50 * zt], -1) * sh[..., None]
        rgb[np.isnan(H)] = (20, 22, 24)
        tim = Image.fromarray(np.clip(rgb[::-1], 0, 255).astype(np.uint8))
        tw, th = (int(H.shape[1] * s * px_per_m), int(H.shape[0] * s * px_per_m))
        tim = tim.resize((max(1, tw), max(1, th)), Image.BILINEAR)
        img.paste(tim, (int((hx - x0) * px_per_m), int((y1 - (hy + H.shape[0] * s)) * px_per_m)))
    except Exception as ex:
        print('terrain:', ex)
    dr = ImageDraw.Draw(img)
    zs = hi[:, 2]
    z0, z1 = (np.percentile(zs, 2), np.percentile(zs, 98))
    order = np.argsort(zs)
    names = np.array(d['paths'], dtype=object)[R[keep][sel][:, 0].astype(int)]
    for i in order:
        a, b = (lo[i], hi[i])
        zt = max(0.0, min(1.0, (b[2] - z0) / max(z1 - z0, 0.001)))
        g = bool(GREEN.search(names[i]))
        col = (int(60 + 60 * zt), int(130 + 90 * zt), int(60 + 40 * zt)) if g else (int(150 + 100 * zt),) * 3
        dr.rectangle([(a[0] - x0) * px_per_m, (y1 - b[1]) * px_per_m, (b[0] - x0) * px_per_m, (y1 - a[1]) * px_per_m], outline=col)
    for gx in range(int(x0 // 100 * 100), int(x1) + 1, 100):
        c = (120, 50, 50) if gx % 500 == 0 else (60, 35, 35)
        dr.line([((gx - x0) * px_per_m, 0), ((gx - x0) * px_per_m, h)], fill=c)
        if gx % 500 == 0:
            dr.text(((gx - x0) * px_per_m + 2, 2), str(gx), fill=(230, 130, 130))
    for gy in range(int(y0 // 100 * 100), int(y1) + 1, 100):
        c = (120, 50, 50) if gy % 500 == 0 else (60, 35, 35)
        dr.line([(0, (y1 - gy) * px_per_m), (w, (y1 - gy) * px_per_m)], fill=c)
        if gy % 500 == 0:
            dr.text((2, (y1 - gy) * px_per_m + 2), str(gy), fill=(230, 130, 130))
    os.makedirs(TOPVIEW, exist_ok=True)
    png = os.path.join(TOPVIEW, '%s_overview.png' % stem(world))
    img.save(png)
    with open(png[:-4] + '.json', 'w', encoding='utf-8') as fh:
        json.dump({'archive': world, 'label': 'overview', 'x0': float(x0), 'y0': float(y0), 'x1': float(x1), 'y1': float(y1), 'px_per_m': px_per_m, 'entities': int(len(lo))}, fh, indent=1)
    print('%d placements, x %.0f..%.0f, y %.0f..%.0f -> %s' % (len(lo), x0, x1, y0, y1, png))
    return png

class MeshCache:

    def __init__(self, lod=0):
        self.lod = lod
        self.m = {}
        self.fail = 0

    def get(self, path):
        if path not in self.m:
            try:
                m = meshes.Mesh(path)
                have = sorted({m.chunk_lod(i) for i in range(len(m.chunks))})
                lod = max([x for x in have if x <= self.lod] or have[:1] or [0])
                ch = m.lod_chunks(lod)
                Ps, Ts, n = ([], [], 0)
                for i in ch:
                    P, _uv, _n, T = m.chunk(i)
                    if T.size and T.max() >= len(P):
                        raise ValueError('chunk %d: index past its vertices' % i)
                    Ps.append(P)
                    Ts.append(T + n)
                    n += len(P)
                self.m[path] = (np.concatenate(Ps), np.concatenate(Ts)) if Ps else None
            except Exception:
                self.fail += 1
                self.m[path] = None
        return self.m[path]

def select(world, box, all_layers=False):
    d = index(world)
    R = d['rows']
    lo, hi = (R[:, 14:17], R[:, 17:20])
    ov = (hi[:, 0] >= box[0]) & (lo[:, 0] <= box[2]) & (hi[:, 1] >= box[1]) & (lo[:, 1] <= box[3])
    ov &= (hi - lo)[:, :2].max(1) < 400
    out = []
    for i in np.nonzero(ov)[0]:
        p = d['paths'][int(R[i, 0])]
        if SKIP.search(p) or not (all_layers or d['shown'][int(R[i, 1])]):
            continue
        out.append((p, d['layers'][int(R[i, 1])], R[i, 2:11].reshape(3, 3), R[i, 11:14], lo[i], hi[i]))
    return out

def triangles(world, box, log=print, drawn=None, scene_=None, terrain_mip=1, lod=1):
    sel = select(world, box)
    M = MeshCache(lod)
    out, kind = ([], [])
    t0 = time.time()
    for k, (p, layer, m, pos, lo, hi) in enumerate(sel):
        got = M.get(p)
        if got is None:
            continue
        P, T = got
        W = P.astype(np.float64) @ m + pos
        g = bool(GREEN.search(p))
        out.append(W[T].astype(np.float32))
        kind.append(np.full(len(T), 1 if g else 0, np.int8))
        if scene_ is not None:
            Mx = np.eye(4)
            Mx[:3, :3] = m
            Mx[3, :3] = pos
            scene_.setdefault('meshes', {}).setdefault(p, (P, T))
            scene_.setdefault('instances', []).append((p, Mx, g, len(drawn) if drawn is not None else -1))
        if drawn is not None:
            short = p.rsplit('\\', 1)[-1]
            lay = layer.replace(WORLD_LAYERS[world], '')
            drawn.append([short, lay, *(round(float(v), 2) for v in (lo[0], lo[1], hi[0], hi[1], hi[2])), p])
    if log:
        log('  %d placements, %d meshes (%d unreadable), %.0f s' % (len(sel), len(M.m), M.fail, time.time() - t0))
    try:
        TR = terrain.Terrain(world)
        V, TT = TR.mesh(box, terrain_mip)
        if len(TT):
            out.append(V[TT].astype(np.float32))
            kind.append(np.full(len(TT), 2, np.int8))
            if scene_ is not None:
                scene_.setdefault('meshes', {})['<terrain>'] = (V.astype(np.float32), TT)
                scene_.setdefault('instances', []).append(('<terrain>', np.eye(4), False, -1))
            if log:
                log('  terrain: %d triangles (mip %d)' % (len(TT), terrain_mip))
    except Exception as ex:
        if log:
            log('  terrain: %r' % ex)
    if not out:
        return (np.zeros((0, 3, 3), np.float32), np.zeros(0, np.int8))
    return (np.concatenate(out), np.concatenate(kind))

def render(world, box, label, px_per_m=4, terrain_mip=1, lod=1):
    from PIL import Image, ImageDraw
    from topview import subdivide
    import sp_topview as SPT
    x0, y0, x1, y1 = box
    if max(x1 - x0, y1 - y0) > MAX_SIDE:
        raise SystemExit('a region is at most %.0f m a side' % MAX_SIDE)
    drawn, sc = ([], {})
    Tw, K = triangles(world, box, drawn=drawn, scene_=sc, terrain_mip=terrain_mip, lod=lod)
    if not len(Tw):
        raise SystemExit('nothing drawn in that box')
    top = Tw[:, :, 2].max(1)
    z0, z1 = (np.percentile(top, 2), np.percentile(top, 98))
    w, h = (int((x1 - x0) * px_per_m), int((y1 - y0) * px_per_m))
    img = Image.new('RGB', (w, h), (20, 22, 24))
    dr = ImageDraw.Draw(img)
    himg = Image.new('RGB', (w, h), (0, 0, 0))
    dh = ImageDraw.Draw(himg)
    nrm = np.cross(Tw[:, 1] - Tw[:, 0], Tw[:, 2] - Tw[:, 0])
    nl = np.linalg.norm(nrm, axis=1)
    keep = nl > 1e-09
    nrm = nrm / np.maximum(nl, 1e-09)[:, None]
    nrm *= np.where(nrm[:, 2:3] < 0, -1, 1)
    lit = 0.35 + 0.65 * np.maximum(0.0, nrm @ np.array([-0.45, 0.45, 0.77], np.float32))
    zt = np.clip((top - z0) / max(z1 - z0, 0.001), 0, 1)
    base = np.select([K[:, None] == 1, K[:, None] == 2], [np.stack([70 + 90 * zt, 120 + 95 * zt, 60 + 50 * zt], 1), np.stack([95 + 70 * zt, 92 + 66 * zt, 78 + 55 * zt], 1)], np.stack([140 + 110 * zt, 132 + 113 * zt, 120 + 110 * zt], 1))
    col = np.minimum(255, base * lit[:, None]).astype(np.uint8)
    S = np.stack([(Tw[:, :, 0] - x0) * px_per_m, (y1 - Tw[:, :, 1]) * px_per_m], 2)
    inside = (S[:, :, 0].max(1) >= 0) & (S[:, :, 0].min(1) <= w) & (S[:, :, 1].max(1) >= 0) & (S[:, :, 1].min(1) <= h)
    for i in np.argsort(top, kind='stable'):
        if not keep[i] or not inside[i]:
            continue
        dr.polygon([tuple(p) for p in S[i]], fill=tuple((int(c) for c in col[i])))
        if K[i] != 1:
            for t in subdivide(tuple(map(tuple, Tw[i].astype(np.float64))), edge=6.0):
                v = max(1, min(2 ** 24 - 1, int(round((sum((p[2] for p in t)) / 3 + 1000) * 100))))
                dh.polygon([((p[0] - x0) * px_per_m, (y1 - p[1]) * px_per_m) for p in t], fill=(v >> 16, v >> 8 & 255, v & 255))
    os.makedirs(TOPVIEW, exist_ok=True)
    base_name = '%s_%s' % (stem(world), label)
    png, meta, hpng = (os.path.join(TOPVIEW, base_name + s) for s in ('.png', '.json', '_height.png'))
    img.save(png)
    himg.save(hpng)
    with open(meta, 'w', encoding='utf-8') as fh:
        json.dump({'archive': world, 'source': 'witcher3', 'label': label, 'height_png': hpng, 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'px_per_m': px_per_m, 'lod': lod, 'triangles': int(len(Tw))}, fh, indent=1)
    with open(os.path.join(TOPVIEW, base_name + '_entities.json'), 'w', encoding='utf-8') as fh:
        json.dump(drawn, fh)
    SPT.write_scene(os.path.join(TOPVIEW, base_name + '_scene.bin'), sc, box, drawn)
    print('%d triangles (%d terrain), %d meshes placed -> %s' % (len(Tw), int((K == 2).sum()), len(drawn), png))
    return (png, meta)

def stats(world, box):
    sel = select(world, box)
    M = MeshCache()
    tri = 0
    for p, *_ in sel:
        got = M.get(p)
        if got:
            tri += len(got[1])
    vs = sum((len(v[0]) for v in M.m.values() if v))
    print('%d placements, %d distinct meshes, %d triangles placed, %d vertices stored' % (len(sel), len(M.m), tri, vs))

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('world', choices=sorted(WORLD_LAYERS))
    ap.add_argument('--index', action='store_true')
    ap.add_argument('--overview', action='store_true')
    ap.add_argument('--ov-px', type=float, default=0.5)
    ap.add_argument('--box', type=float, nargs=4)
    ap.add_argument('--label')
    ap.add_argument('--px', type=float, default=4)
    ap.add_argument('--terrain-mip', type=int, default=1)
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--lod', type=int, default=1)
    a = ap.parse_args(argv)
    if a.index:
        index(a.world)
    if a.overview:
        overview(a.world, a.ov_px)
    if a.box and a.stats:
        stats(a.world, a.box)
    elif a.box:
        render(a.world, a.box, a.label or 'region', a.px, a.terrain_mip, a.lod)
if __name__ == '__main__':
    main(sys.argv[1:])
