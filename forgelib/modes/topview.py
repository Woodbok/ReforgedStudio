"""forgelib/modes/topview.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import json
import math
import os
import re
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'maps')):
    if p not in sys.path:
        sys.path.insert(0, p)
import convert
import mapwalk
from filler_clip import buffers, placement, to_world, world_box
from forge import Forge
OUT = os.path.join(ROOT, 'out', 'modes', 'topview')
PX_PER_M = 6
SKIP = re.compile('Fake|SkyDome|Sky_Dome|GFX_|Crowd|_OOB|OutOfBounds|Border', re.I)
GREEN = re.compile('_RT_|Palm|Veg|Bush|Leaves|Leaf|Flower|Tree|Grass|Plant|Hedge|Ivy', re.I)

def triangles(archive_path):
    cls = convert.class_ids()
    bv = struct.pack('<I', cls['BoundingVolume'])
    objs = []
    with Forge(archive_path) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            try:
                p = mapwalk.Payload(r, f.read(r))
            except Exception:
                continue
            objs += p.objects
    meshes = {o['uid']: o for o in objs if o['type'] == cls['Mesh'] and o.get('has_uid')}
    selectors = {o['uid']: o for o in objs if o['type'] == cls['LODSelector'] and o.get('has_uid')}

    def refs(body, pool):
        return [u for u in dict.fromkeys((struct.unpack_from('<I', body, i)[0] for i in range(len(body) - 3))) if u in pool]
    decoded = {}

    def decode(mu, unit, bound):
        key = (mu, round(unit, 9))
        if key not in decoded:
            b = meshes[mu]['body']
            try:
                st, vb, nv, ib, ni = buffers(b)
                P = [struct.unpack_from('<4h', b, vb + st * i) for i in range(nv)]
                ws = Counter((abs(q[3]) for q in P)).most_common(1)[0][0]
                if ws and ws % 8 == 0:
                    unit, bound = (ws / 8 / 32767, [ws / 8] * 3)
                P = [q[:3] for q in P]
                ext = [max((abs(q[k]) for q in P)) * unit for k in range(3)]
                ok = all((e <= bd * 1.2 + 0.3 for e, bd in zip(ext, bound)))
                decoded[key] = ([tuple((c * unit for c in q)) for q in P], struct.unpack_from(f'<{ni}H', b, ib)) if ok else None
            except (ValueError, struct.error, ZeroDivisionError):
                decoded[key] = None
        return decoded[key]
    ent_id = struct.pack('<I', cls['Entity'])
    placed = []
    for o in objs:
        if o['type'] == cls['Entity'] and o.get('has_uid'):
            placed.append((o['uid'], o['name'] or '', o['body']))
        elif o['type'] == cls.get('EntityGroup') and o.get('has_uid'):
            b, starts, k = (o['body'], [], o['body'].find(ent_id, 20))
            while k > 0:
                if b[k - 6:k - 4] == b'\x00\x01':
                    starts.append(k - 6)
                k = b.find(ent_id, k + 4)
            for i, st in enumerate(starts):
                sub = b[st:starts[i + 1] if i + 1 < len(starts) else len(b)]
                placed.append((struct.unpack_from('<I', sub, 2)[0], o['name'] or '', sub))
    out, seen = ([], set())
    for uid, name, body in placed:
        if uid in seen:
            continue
        seen.add(uid)
        o = {'body': body}
        if SKIP.search(name):
            continue
        pl = placement(o['body'], bv)
        if not pl:
            continue
        lo, hi = world_box(*pl)
        if hi[0] - lo[0] > 300 or hi[1] - lo[1] > 300:
            continue
        cands = refs(o['body'], meshes)
        for s in refs(o['body'], selectors):
            cands += refs(selectors[s]['body'], meshes)
        if not cands:
            continue
        m, llo, lhi = pl
        unit = math.ceil(max((abs(v) for v in llo + lhi)) or 1) / 32767
        bound = [max(abs(llo[k]), abs(lhi[k])) for k in range(3)]
        best = None
        for mu in dict.fromkeys(cands):
            d = decode(mu, unit, bound)
            if d and (best is None or len(d[1]) > len(best[1])):
                best = d
        if not best:
            continue
        P, I = best
        W = [to_world(m, q) for q in P]
        g = bool(GREEN.search(name))
        for t in range(0, len(I) - 2, 3):
            a, b, c = (W[I[t]], W[I[t + 1]], W[I[t + 2]])
            if a != b and b != c and (a != c):
                out.append(((a, b, c), g))
    return out

def subdivide(t, edge=3.0):
    a, b, c = t
    if max(math.dist(a[:2], b[:2]), math.dist(b[:2], c[:2]), math.dist(c[:2], a[:2])) <= edge:
        return [t]
    m = lambda p, q: tuple(((p[i] + q[i]) / 2 for i in range(3)))
    ab, bc, ca = (m(a, b), m(b, c), m(c, a))
    return [u for part in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)) for u in subdivide(part, edge)]

def render(archive, source=None):
    import aa_map
    src = aa_map.archive_source(archive, source)
    os.makedirs(OUT, exist_ok=True)
    stem = archive.replace('.forge', '') if not source else f"{os.path.basename(os.path.dirname(src))}_{archive.replace('.forge', '')}"
    png, meta = (os.path.join(OUT, stem + '.png'), os.path.join(OUT, stem + '.json'))
    hpng = os.path.join(OUT, stem + '_height.png')
    if os.path.exists(png) and os.path.exists(meta) and os.path.exists(hpng):
        return (png, meta)
    from PIL import Image, ImageDraw
    tris = triangles(src)
    xs = sorted((v[0] for t, _g in tris for v in t))
    ys = sorted((v[1] for t, _g in tris for v in t))
    zs = sorted((max((v[2] for v in t)) for t, _g in tris))
    q = lambda s, f: s[int(f * (len(s) - 1))]
    x0, x1 = (q(xs, 0.005) - 20, q(xs, 0.995) + 20)
    y0, y1 = (q(ys, 0.005) - 20, q(ys, 0.995) + 20)
    z0, z1 = (q(zs, 0.02), q(zs, 0.98))
    w, h = (int((x1 - x0) * PX_PER_M), int((y1 - y0) * PX_PER_M))
    img = Image.new('RGB', (w, h), (20, 22, 24))
    d = ImageDraw.Draw(img)
    trees = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    dt = ImageDraw.Draw(trees)
    light = (-0.45, 0.45, 0.77)
    himg = Image.new('RGB', (w, h), (0, 0, 0))
    dh = ImageDraw.Draw(himg)
    hparts = []
    for (a, b, c), g in sorted(tris, key=lambda tg: max((v[2] for v in tg[0]))):
        n = ((b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]), (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]), (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        nl = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        if nl < 1e-09:
            continue
        n = (n[0] / nl, n[1] / nl, n[2] / nl)
        if n[2] < 0:
            n = (-n[0], -n[1], -n[2])
        lit = 0.35 + 0.65 * max(0.0, n[0] * light[0] + n[1] * light[1] + n[2] * light[2])
        zt = max(0.0, min(1.0, (max(a[2], b[2], c[2]) - z0) / max(z1 - z0, 0.001)))
        base = (95 + 120 * zt, 120 + 95 * zt, 85 + 60 * zt) if g else (120 + 115 * zt, 112 + 113 * zt, 100 + 110 * zt)
        col = tuple((int(min(255, v * lit)) for v in base))
        (dt if g else d).polygon([((v[0] - x0) * PX_PER_M, (y1 - v[1]) * PX_PER_M) for v in (a, b, c)], fill=col)
        if not g:
            hparts += subdivide((a, b, c))
    for t in sorted(hparts, key=lambda t: max((v[2] for v in t))):
        z = sum((v[2] for v in t)) / 3
        v = max(1, min(2 ** 24 - 1, int(round((z + 1000) * 100))))
        dh.polygon([((p[0] - x0) * PX_PER_M, (y1 - p[1]) * PX_PER_M) for p in t], fill=(v >> 16, v >> 8 & 255, v & 255))
    himg.save(hpng)
    alpha = trees.getchannel('A').point(lambda v: 110 if v else 0)
    trees.putalpha(alpha)
    img = Image.alpha_composite(img.convert('RGBA'), trees).convert('RGB')
    img.save(png)
    with open(meta, 'w', encoding='utf-8') as fh:
        json.dump({'archive': archive, 'source': src, 'height_png': hpng, 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'px_per_m': PX_PER_M, 'triangles': len(tris)}, fh, indent=1)
    return (png, meta)
if __name__ == '__main__':
    for arch in sys.argv[1:]:
        print(render(os.path.basename(arch), arch if '/' in arch or os.sep in arch else None))
