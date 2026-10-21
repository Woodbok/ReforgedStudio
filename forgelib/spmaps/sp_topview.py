"""forgelib/spmaps/sp_topview.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import math
import os
import pickle
import re
import struct
import sys
import time
import zlib
from collections import Counter
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE, os.path.join(ROOT, 'modes')):
    if p not in sys.path:
        sys.path.insert(0, p)
from codec27 import DecodeError, PartialCodec64, geometry_schema
from forge27 import Forge27, Forge27Error
OUT = os.path.join(ROOT, 'out', 'spmaps')
TOPVIEW = os.path.join(OUT, 'topview')
CACHE_VERSION = 2
SKIP = re.compile('Fake|SkyDome|Sky_Dome|GFX_|Crowd|_OOB|OutOfBound|Border|_Sky|Skybox|Occlu|Mist|Fog|_TRG|Trigger|Volume|NavPoint|Portal|Blocker', re.I)
GREEN = re.compile('_RT_|Palm|Veg|Bush|Leaves|Leaf|Flower|Tree|Grass|Plant|Hedge|Ivy|Cornstalk|Fern|Pine|Oak|Birch', re.I)
MAX_SIDE = 400.0
LOWER_LOD = re.compile('_LOD[1-9]_Chunk', re.I)
H = {n: zlib.crc32(n.encode()) for n in ('Entity', 'EntityGroup', 'Mesh', 'LODSelector', 'MeshData', 'BoundingVolume')}
ENTITY = struct.pack('<I', H['Entity'])
BV = struct.pack('<I', H['BoundingVolume'])

def stem(archive):
    return os.path.basename(archive).replace('DataPC_DX11_AC3_', '').replace('.forge', '')

def entity_placement(b):
    if len(b) < 15 + 64 or b[:2] != b'\x00\x01':
        return None
    st = b[14]
    if st == 3:
        o = 15
    elif st in (1, 2, 5):
        o = 23
    else:
        return None
    m = struct.unpack_from('<16f', b, o)
    if not all((math.isfinite(v) for v in m)) or abs(m[15] - 1) > 0.001 or any((abs(m[i]) > 0.001 for i in (3, 7, 11))):
        return None
    k = b.rfind(BV)
    if k < 0 or len(b) - k < 28:
        return None
    lo, hi = (struct.unpack_from('<3f', b, k + 4), struct.unpack_from('<3f', b, k + 16))
    if not all((math.isfinite(v) and abs(v) < 10000.0 for v in lo + hi)):
        return None
    return (m, lo, hi)

def group_entities(b):
    starts, k = ([], b.find(ENTITY, 20))
    while k > 0:
        if b[k - 10:k - 8] == b'\x00\x01':
            starts.append(k - 10)
        k = b.find(ENTITY, k + 4)
    return [b[st:starts[i + 1] if i + 1 < len(starts) else len(b)] for i, st in enumerate(starts)]

def u64s(b, pool):
    a = np.frombuffer(b[:len(b) - len(b) % 1], dtype=np.uint8)
    if len(a) < 8:
        return []
    out = []
    for sh in range(8):
        n = (len(a) - sh) // 8
        if n <= 0:
            continue
        v = np.frombuffer(a[sh:sh + n * 8].tobytes(), dtype='<u8')
        at = np.minimum(np.searchsorted(pool, v), len(pool) - 1)
        for i in np.nonzero(pool[at] == v)[0]:
            out.append((sh + 8 * int(i), int(v[i])))
    return [u for _o, u in sorted(dict.fromkeys(out))]

def index(archive, log=print):
    f = Forge27(archive)
    st = os.stat(f.path)
    cache = os.path.join(OUT, 'cache', f'{stem(archive)}_entities_v{CACHE_VERSION}.pkl')
    key = (st.st_size, int(st.st_mtime))
    if os.path.exists(cache):
        with open(cache, 'rb') as fh:
            d = pickle.load(fh)
        if d.get('key') == key:
            return d
    owner = object_owners(f, log)
    pool = np.array(sorted(owner), dtype='<u8')
    cells = [r for r in f.records if r.name.startswith('Cell') and r.name.endswith('_DataBlock')]
    ents, bad, t0 = ([], Counter(), time.time())
    for i, r in enumerate(cells):
        try:
            objs = f.objects(r)
        except (Forge27Error, struct.error) as e:
            bad[type(e).__name__] += 1
            continue
        for o in objs:
            if o['type'] == H['Entity']:
                bodies = [(o['name'], o['body'])]
            elif o['type'] == H['EntityGroup']:
                bodies = [(o['name'], sb) for sb in group_entities(o['body'])]
            else:
                continue
            for name, b in bodies:
                pl = entity_placement(b)
                if pl is None:
                    bad['no placement'] += 1
                    continue
                ents.append((name, r.name, pl[0], pl[1], pl[2], u64s(b, pool)))
        if log and i % 500 == 0:
            log(f'  {i}/{len(cells)} cells, {len(ents)} entities, {time.time() - t0:.0f} s')
    d = {'key': key, 'entities': ents, 'records': {r.uid: r.name for r in f.records}, 'owner': owner, 'bad': dict(bad)}
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, 'wb') as fh:
        pickle.dump(d, fh)
    f.close()
    return d

def object_owners(f, log=print):
    owner, t0 = ({}, time.time())
    for i, r in enumerate(f.records):
        try:
            for u in f.toc_uids(r):
                owner.setdefault(u, r.uid)
        except (Forge27Error, struct.error):
            pass
        owner.setdefault(r.uid, r.uid)
        if log and i % 2000 == 0:
            log(f'  object uids: {i}/{len(f.records)} records, {time.time() - t0:.0f} s')
    return owner

def to_world(m, p):
    return tuple((p[0] * m[j] + p[1] * m[4 + j] + p[2] * m[8 + j] + m[12 + j] for j in range(3)))

def world_box(m, lo, hi):
    wc = [to_world(m, (x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    return (tuple((min((c[j] for c in wc)) for j in range(3))), tuple((max((c[j] for c in wc)) for j in range(3))))

class Meshes:

    def __init__(self, f, owner):
        self.f = f
        self.owner = owner
        self.pool = np.array(sorted(owner), dtype='<u8')
        self.C = PartialCodec64(geometry_schema())
        self.objs = {}
        self.geo = {}
        self.stats = Counter()

    def record_objects(self, uid):
        ru = self.owner.get(uid)
        if ru is None:
            return []
        if ru not in self.objs:
            r = self.f.by_uid.get(ru)
            try:
                self.objs[ru] = self.f.objects(r) if r else []
            except (Forge27Error, struct.error):
                self.objs[ru] = []
        objs = self.objs[ru]
        return objs if ru == uid else [o for o in objs if o['uid'] == uid]

    def kind(self, uid):
        for o in self.record_objects(uid):
            if o['type'] == H['Mesh']:
                return 'mesh'
            if o['type'] == H['LODSelector']:
                return 'lod'
        return None

    def mesh(self, uid):
        if uid in self.geo:
            return self.geo[uid]
        g = None
        for o in self.record_objects(uid):
            if o['type'] == H['Mesh']:
                g = self.decode(o['body'])
                break
        self.geo[uid] = g
        return g

    def decode(self, body):
        try:
            self.C.decode(body)
        except (DecodeError, KeyError, ValueError, struct.error):
            pass
        md = [x for x in self.C.made if x.type_hash == H['MeshData']]
        if not md:
            self.stats['no MeshData'] += 1
            return None
        F = md[0].fields
        try:
            st = F['VertexStride'][0]
            vb = b''.join(F['VertexBufferData'])
            ib = b''.join(F['IndexBufferData'])
            wide = F['IsIndexBuffer32bit'][0]
            prims = F['StandardPrimitives']
        except (KeyError, TypeError, IndexError):
            self.stats['incomplete MeshData'] += 1
            return None
        n = len(vb) // st if st else 0
        if not n or not ib:
            self.stats['empty'] += 1
            return None
        raw = np.frombuffer(vb[:n * st], dtype=np.uint8).reshape(n, st)[:, :8].copy().view('<i2').reshape(n, 4)
        s = int(np.bincount(np.abs(raw[:, 3].astype(np.int32))).argmax())
        fit = s >= 32767 or F['VertexFormat'][0] in (0, 1)
        if fit:
            verts = raw[:, :3].astype(np.float32)
        else:
            verts = raw[:, :3].astype(np.float32) * (raw[:, 3:4].astype(np.float32) / np.float32(32767.0))
        idx = np.frombuffer(ib, dtype='<u4' if wide else '<u2').astype(np.int64)
        tris = []
        for p in prims:
            pf = getattr(p, 'obj', None) or p
            g = {k: struct.unpack('<i', v)[0] for k, v in pf.fields.items() if isinstance(v, bytes) and len(v) == 4}
            if g.get('Type', 1) != 1:
                continue
            seg = idx[g['StartIndex']:g['StartIndex'] + 3 * g['PrimitiveCount']]
            base = g.get('MinIndex', 0)
            if base and len(seg) and (seg.min() < base):
                seg = seg + base
            tris.append(seg[:len(seg) // 3 * 3].reshape(-1, 3))
        if not tris:
            self.stats['no triangle lists'] += 1
            return None
        t = np.concatenate(tris)
        t = t[(t < n).all(axis=1)]
        self.stats['decoded'] += 1
        return (verts, t, fit)

    def best(self, refs, lo, hi):
        cands = []
        for u in refs:
            k = self.kind(u)
            if k == 'mesh':
                cands.append(u)
            elif k == 'lod':
                for o in self.record_objects(u):
                    if o['type'] == H['LODSelector']:
                        cands += u64s(o['body'], self.pool)
        brad = float(max(np.linalg.norm(lo), np.linalg.norm(hi)))
        best = None
        for u in dict.fromkeys(cands):
            g = self.mesh(u)
            if g is None:
                continue
            P, T, fit = g
            rad = float(np.linalg.norm(P, axis=1).max()) if len(P) else 0.0
            factor = 1.0
            if fit:
                if not rad:
                    continue
                factor = brad / rad
                self.stats['fitted to its box'] += 1
            elif rad > brad * getattr(self, 'fit_slack', (1.25, 0.5))[0] + getattr(self, 'fit_slack', (1.25, 0.5))[1]:
                self.stats['too big for its box'] += 1
                continue
            if best is None or len(T) > len(best[1][1]):
                best = (u, (P, T), factor)
        return best

def overview(archive, px_per_m=1.0):
    from PIL import Image, ImageDraw
    d = index(archive)
    boxes = []
    for name, _rec, m, lo, hi, _refs in d['entities']:
        if SKIP.search(name):
            continue
        a, b = world_box(m, lo, hi)
        if b[0] - a[0] > 300 or b[1] - a[1] > 300:
            continue
        boxes.append((a, b, bool(GREEN.search(name))))
    xs = sorted((v for a, b, _g in boxes for v in (a[0], b[0])))
    ys = sorted((v for a, b, _g in boxes for v in (a[1], b[1])))
    zs = sorted((b[2] for a, b, _g in boxes))
    q = lambda s, f: s[int(f * (len(s) - 1))]
    x0, x1, y0, y1 = (q(xs, 0.002) - 20, q(xs, 0.998) + 20, q(ys, 0.002) - 20, q(ys, 0.998) + 20)
    z0, z1 = (q(zs, 0.02), q(zs, 0.98))
    w, h = (int((x1 - x0) * px_per_m), int((y1 - y0) * px_per_m))
    img = Image.new('RGB', (w, h), (20, 22, 24))
    dr = ImageDraw.Draw(img)
    for a, b, g in sorted(boxes, key=lambda t: -(t[1][0] - t[0][0]) * (t[1][1] - t[0][1])):
        zt = max(0.0, min(1.0, (b[2] - z0) / max(z1 - z0, 0.001)))
        dim = 0.45 if max(b[0] - a[0], b[1] - a[1]) > 60 else 1.0
        col = (int(60 + 60 * zt), int(110 + 90 * zt), int(60 + 40 * zt)) if g else (int((90 + 150 * zt) * dim),) * 3
        dr.rectangle([(a[0] - x0) * px_per_m, (y1 - b[1]) * px_per_m, (b[0] - x0) * px_per_m, (y1 - a[1]) * px_per_m], fill=col)
    for gx in range(int(x0 // 100 * 100), int(x1) + 1, 100):
        dr.line([((gx - x0) * px_per_m, 0), ((gx - x0) * px_per_m, h)], fill=(70, 40, 40))
        dr.text(((gx - x0) * px_per_m + 2, 2), str(gx), fill=(220, 120, 120))
    for gy in range(int(y0 // 100 * 100), int(y1) + 1, 100):
        dr.line([(0, (y1 - gy) * px_per_m), (w, (y1 - gy) * px_per_m)], fill=(70, 40, 40))
        dr.text((2, (y1 - gy) * px_per_m + 2), str(gy), fill=(220, 120, 120))
    os.makedirs(TOPVIEW, exist_ok=True)
    png = os.path.join(TOPVIEW, f'{stem(archive)}_overview.png')
    img.save(png)
    with open(png[:-4] + '.json', 'w', encoding='utf-8') as fh:
        json.dump({'archive': os.path.basename(archive), 'label': 'overview', 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'px_per_m': px_per_m, 'entities': len(boxes)}, fh, indent=1)
    print(f'{len(boxes)} entities, x {x0:.0f}..{x1:.0f}, y {y0:.0f}..{y1:.0f}, z {z0:.0f}..{z1:.0f} -> {png}')
    return png

def triangles(archive, box, log=print, drawn=None, scene=None):
    d = index(archive, log)
    f = Forge27(archive)
    M = Meshes(f, d['owner'])
    out, green, n_out, kept, t0 = ([], [], 0, 0, time.time())
    sel = []
    for e in d['entities']:
        name, _rec, m, lo, hi, refs = e
        if SKIP.search(name) or LOWER_LOD.search(name) or (not refs):
            continue
        a, b = world_box(m, lo, hi)
        if b[0] - a[0] > 300 or b[1] - a[1] > 300:
            continue
        if b[0] < box[0] or a[0] > box[2] or b[1] < box[1] or (a[1] > box[3]):
            continue
        sel.append(e)
    for i, (name, _rec, m, lo, hi, refs) in enumerate(sel):
        got = M.best(refs, lo, hi)
        if got is None:
            continue
        mu, (P, T), factor = got
        Mx = np.array(m, dtype=np.float64).reshape(4, 4)
        if factor != 1.0:
            Mx = np.diag([factor, factor, factor, 1.0]) @ Mx
        W = np.c_[P.astype(np.float64), np.ones(len(P))] @ Mx
        gr = bool(GREEN.search(name))
        if scene is not None:
            scene.setdefault('meshes', {}).setdefault(mu, (P, T))
            scene.setdefault('instances', []).append((mu, Mx, gr, len(drawn) if drawn is not None else -1))
        out.append(W[T][:, :, :3].astype(np.float32))
        green.append(np.full(len(T), gr))
        n_out += len(T)
        kept += 1
        if drawn is not None:
            wa, wb = (W[:, :3].min(axis=0), W[:, :3].max(axis=0))
            drawn.append([name, f.by_uid[M.owner[mu]].name, *(round(float(v), 2) for v in (wa[0], wa[1], wb[0], wb[1], wb[2])), _rec])
        if log and i % 2000 == 0:
            log(f'  {i}/{len(sel)} entities, {n_out} triangles, {time.time() - t0:.0f} s')
    if log:
        log(f'  {kept} of {len(sel)} entities drawn; meshes: {dict(M.stats)}')
    f.close()
    if not out:
        return (np.zeros((0, 3, 3), np.float32), np.zeros(0, bool))
    return (np.concatenate(out), np.concatenate(green))

def write_scene(path, scene, box, drawn):
    cx, cy = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    order = list(scene.get('meshes', {}))
    at = {u: i for i, u in enumerate(order)}
    vs, ix, meshes, vo, io = ([], [], [], 0, 0)
    for u in order:
        P, T = scene['meshes'][u]
        vs.append(P.astype(np.float32))
        ix.append(T.astype(np.uint32).ravel())
        meshes.append([vo, len(P), io, int(T.size)])
        vo += len(P)
        io += int(T.size)
    rows = []
    for u, Mx, gr, ei in scene.get('instances', []):
        M2 = Mx.copy()
        M2[3, 0] -= cx
        M2[3, 1] -= cy
        rows.append(np.r_[M2.ravel(), float(gr), float(ei), float(at[u]), 0.0])
    V = np.concatenate(vs) if vs else np.zeros((0, 3), np.float32)
    I = np.concatenate(ix) if ix else np.zeros(0, np.uint32)
    R = np.array(rows, dtype=np.float32).reshape(-1, 20)
    head = {'origin': [cx, cy], 'box': list(box), 'meshes': meshes, 'instances': len(R), 'row_floats': 20, 'vertices': int(len(V)), 'indices': int(len(I))}
    hb = json.dumps(head).encode()
    hb += b' ' * (-(4 + len(hb)) % 4)
    with open(path, 'wb') as fh:
        fh.write(struct.pack('<I', len(hb)) + hb)
        fh.write(V.astype('<f4').tobytes())
        fh.write(I.astype('<u4').tobytes())
        fh.write(R.astype('<f4').tobytes())
    return path

def render(archive, box, label, px_per_m=4):
    from PIL import Image, ImageDraw
    sys.path.insert(0, os.path.join(ROOT, 'modes'))
    from topview import subdivide
    x0, y0, x1, y1 = box
    if max(x1 - x0, y1 - y0) > MAX_SIDE:
        raise SystemExit(f'a region is at most {MAX_SIDE:.0f} m a side (memory: ~3.5 M triangles per 250 m of city)')
    drawn, scene = ([], {})
    Tw, G = triangles(archive, box, drawn=drawn, scene=scene)
    if not len(Tw):
        raise SystemExit('nothing drawn in that box')
    top = Tw[:, :, 2].max(1)
    z0, z1 = (np.percentile(top, 2), np.percentile(top, 98))
    w, h = (int((x1 - x0) * px_per_m), int((y1 - y0) * px_per_m))
    img = Image.new('RGB', (w, h), (20, 22, 24))
    dr = ImageDraw.Draw(img)
    trees = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    dt = ImageDraw.Draw(trees)
    himg = Image.new('RGB', (w, h), (0, 0, 0))
    dh = ImageDraw.Draw(himg)
    nrm = np.cross(Tw[:, 1] - Tw[:, 0], Tw[:, 2] - Tw[:, 0])
    nl = np.linalg.norm(nrm, axis=1)
    keep = nl > 1e-09
    nrm = nrm / np.maximum(nl, 1e-09)[:, None]
    nrm *= np.where(nrm[:, 2:3] < 0, -1, 1)
    lit = 0.35 + 0.65 * np.maximum(0.0, nrm @ np.array([-0.45, 0.45, 0.77], np.float32))
    zt = np.clip((top - z0) / max(z1 - z0, 0.001), 0, 1)
    base = np.where(G[:, None], np.stack([95 + 120 * zt, 120 + 95 * zt, 85 + 60 * zt], 1), np.stack([120 + 115 * zt, 112 + 113 * zt, 100 + 110 * zt], 1))
    col = np.minimum(255, base * lit[:, None]).astype(np.uint8)
    S = np.stack([(Tw[:, :, 0] - x0) * px_per_m, (y1 - Tw[:, :, 1]) * px_per_m], 2)
    for i in np.argsort(top, kind='stable'):
        if not keep[i]:
            continue
        (dt if G[i] else dr).polygon([tuple(p) for p in S[i]], fill=tuple((int(c) for c in col[i])))
        if not G[i]:
            for t in subdivide(tuple(map(tuple, Tw[i].astype(np.float64))), edge=6.0):
                v = max(1, min(2 ** 24 - 1, int(round((sum((p[2] for p in t)) / 3 + 1000) * 100))))
                dh.polygon([((p[0] - x0) * px_per_m, (y1 - p[1]) * px_per_m) for p in t], fill=(v >> 16, v >> 8 & 255, v & 255))
    alpha = trees.getchannel('A').point(lambda v: 110 if v else 0)
    trees.putalpha(alpha)
    img = Image.alpha_composite(img.convert('RGBA'), trees).convert('RGB')
    os.makedirs(TOPVIEW, exist_ok=True)
    base_name = f'{stem(archive)}_{label}'
    png, meta, hpng = (os.path.join(TOPVIEW, base_name + s) for s in ('.png', '.json', '_height.png'))
    img.save(png)
    himg.save(hpng)
    with open(meta, 'w', encoding='utf-8') as fh:
        json.dump({'archive': os.path.basename(archive), 'source': 'sp', 'label': label, 'height_png': hpng, 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'px_per_m': px_per_m, 'triangles': int(len(Tw))}, fh, indent=1)
    with open(os.path.join(TOPVIEW, base_name + '_entities.json'), 'w', encoding='utf-8') as fh:
        json.dump(drawn, fh)
    write_scene(os.path.join(TOPVIEW, base_name + '_scene.bin'), scene, box, drawn)
    print(f'{len(Tw)} triangles, {len(drawn)} entities -> {png}')
    return (png, meta)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('archive')
    ap.add_argument('--index', action='store_true')
    ap.add_argument('--overview', action='store_true')
    ap.add_argument('--box', nargs=4, type=float, metavar=('X0', 'Y0', 'X1', 'Y1'))
    ap.add_argument('--label', default='region')
    ap.add_argument('--px', type=float, default=4, help='pixels per metre (topview: 6)')
    a = ap.parse_args(argv)
    if a.index:
        d = index(a.archive)
        print(f"{len(d['entities'])} entities; skipped {d['bad']}")
    if a.overview:
        overview(a.archive)
    if a.box:
        render(a.archive, a.box, a.label, a.px)
if __name__ == '__main__':
    main(sys.argv[1:])
