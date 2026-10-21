"""forgelib/part_render.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'ac4chars'))
import dxt
import make_jester
import part_icons
import skinmesh
from datafile import decompress
from forge import Forge
from paths import ACR
UNIT = 10.0 / 32768
UV = 2048.0
LIGHT = (0.3, -0.6, 0.75)
SKIP = ('teeth', 'eye', 'tongue')

class Source:

    def __init__(self):
        import acr_survey
        self.f = Forge(os.path.join(ACR, 'DataPC.forge'))
        self.recs = {r.name: r for r in self.f.records if r.name}
        self.files = {}
        self.index = acr_survey.acr_index()
        self.material = acr_survey.class_id('Material')
        self.lodsel = acr_survey.class_id('LODSelector')

    def objects(self, name):
        if name not in self.files:
            self.files[name] = make_jester.objects_of(decompress(self.f.read(self.recs[name])))
        return self.files[name]

    def by_uid(self, uid):
        hit = self.index.get(uid)
        if not hit:
            return None
        fname = hit[0] if isinstance(hit, (tuple, list)) else hit
        if fname not in self.recs:
            return None
        return next((o for o in self.objects(fname) if o['uid'] == uid), None)

    def close(self):
        self.f.close()

def _refs(body, by_uid):
    out = []
    for p in range(len(body) - 3):
        o = by_uid.get(struct.unpack_from('<I', body, p)[0])
        if o is not None and o not in out:
            out.append(o)
    return out

def _texture(material, by_uid, src):
    b = material['body']
    for p in range(len(b) - 3):
        u = struct.unpack_from('<I', b, p)[0]
        o = by_uid.get(u)
        if o is None and u > 4096:
            o = src.by_uid(u) if src else None
        if o and o['type'] == make_jester.TEXTURE and (make_jester.role_of(o['name']) == 'Diffuse'):
            w, h, px = dxt.decode(o['body'])
            low = sum((1 for i in range(3, len(px), 4) if px[i] < 16))
            return (w, h, px, low >= 0.05 * w * h)
    return None
BONE_LIST = 186463425

def _palette(b, lay):
    out = []
    for off, bone in skinmesh.entries(b, lay):
        r = [v / 32767 for v in struct.unpack_from('<6h', b, off + 16)]
        c1, c2 = (tuple(r[:3]), tuple(r[3:]))
        c3 = (c1[1] * c2[2] - c1[2] * c2[1], c1[2] * c2[0] - c1[0] * c2[2], c1[0] * c2[1] - c1[1] * c2[0])
        t = tuple((x / 65536 for x in skinmesh.unpack_bind(struct.unpack_from('<Q', b, off + 28)[0])))
        out.append((bone, (c1, c2, c3), t))
    return out

def _bone_lists(b, lay):
    lists, p = ([], lay['end'])
    while len(lists) < lay['subs']:
        p = b.find(struct.pack('<I', BONE_LIST), p)
        if p < 0:
            break
        n = struct.unpack_from('<H', b, p + 5)[0]
        lst = list(b[p + 16:p + 16 + n])
        lists.append(lst if len(lst) == n and all((i < lay['count'] for i in lst)) else None)
        p += 16 + n
    return [lists[k] if k < len(lists) and lists[k] is not None else list(range(lay['count'])) for k in range(lay['subs'])]

def load_mesh(src, acr_file, lodselector):
    objs = src.objects(acr_file)
    by_uid = {o['uid']: o for o in objs}
    sel = next((o for o in objs if o['name'] == lodselector and o['type'] == src.lodsel), None)
    meshes = [o for o in _refs(sel['body'], by_uid) if o['type'] == skinmesh.MESH] if sel else []
    if not meshes:
        meshes = [o for o in objs if o['type'] == skinmesh.MESH and o['name'].endswith('_LOD0')][:1]
    if not meshes:
        return None
    b = meshes[0]['body']
    lay = skinmesh.layout(b)
    vb, st, nv = (lay['vb'], lay['stride'], lay['nv'])
    lists = _bone_lists(b, lay)
    rec = lay['post'] + 32
    subs_raw = [tuple((struct.unpack_from('<I', b, rec + 32 * k + q)[0] for q in (8, 12, 16, 20))) for k in range(lay['subs'])]
    sub_of = [0] * nv
    for k, (v0, vn, _i0, _tn) in enumerate(subs_raw):
        for i in range(v0, min(nv, v0 + vn)):
            sub_of[i] = k
    verts, skin = ([], [])
    for i in range(nv):
        x, y, z = struct.unpack_from('<3h', b, vb + st * i)
        u, v = struct.unpack_from('<2h', b, vb + st * i + 20)
        verts.append((x * UNIT, y * UNIT, z * UNIT, u / UV, v / UV))
        bi, wi = (b[vb + st * i + 24:vb + st * i + 28], b[vb + st * i + 28:vb + st * i + 32])
        lst = lists[sub_of[i]]
        skin.append(tuple(((lst[bi[j]] if bi[j] < len(lst) else bi[j], wi[j] / 255) for j in range(4) if wi[j])))
    mats = [o for o in _refs(b, by_uid) if o['type'] == src.material]
    if not mats:
        mats = [o for o in (src.by_uid(struct.unpack_from('<I', b, p)[0]) for p in range(len(b) - 3) if struct.unpack_from('<I', b, p)[0] > 4096) if o is not None and o['type'] == src.material][:lay['subs']]
    texs = [_texture(m, by_uid, src) for m in mats]
    subs = [(v0, i0, tn, texs[k] if k < len(texs) else texs[0] if texs else None) for k, (v0, _vn, i0, tn) in enumerate(subs_raw)]
    return {'verts': verts, 'skin': skin, 'palette': _palette(b, lay), 'subs': subs, 'idx': struct.unpack_from(f"<{lay['ni']}H", b, lay['ib'])}

def _key(r, t):
    return (tuple((round(v, 3) for c in r for v in c)), tuple((round(v, 3) for v in t)))

def reference(meshes):
    votes = {}
    for m in meshes:
        for bone, r, t in m['palette']:
            k = _key(r, t)
            votes.setdefault(bone, {}).setdefault(k, [0, r, t])[0] += 1
    return {bone: max(v.values(), key=lambda e: e[0])[1:] for bone, v in votes.items()}

def triangles(m, ref=None):
    moves = {}
    for k, (bone, r, t) in enumerate(m['palette']):
        rr, tr = (ref or {}).get(bone, (r, t))
        if _key(rr, tr) != _key(r, t):
            moves[k] = (r, t, rr, tr)
    verts = m['verts']
    if moves:
        out = []
        for v, sk in zip(verts, m['skin']):
            if not any((k in moves for k, _w in sk)):
                out.append(v)
                continue
            V = (v[1], v[2], v[0])
            acc, wsum = ([0.0, 0.0, 0.0], 0.0)
            for k, w in sk:
                if k in moves:
                    r, t, rr, tr = moves[k]
                    p = tuple((sum((r[c][row] * V[c] for c in range(3))) + t[row] for row in range(3)))
                    d = tuple((p[row] - tr[row] for row in range(3)))
                    q = tuple((sum((rr[col][row] * d[row] for row in range(3))) for col in range(3)))
                else:
                    q = V
                for j in range(3):
                    acc[j] += w * q[j]
                wsum += w
            q = tuple((a / wsum for a in acc)) if wsum else V
            out.append((q[2], q[0], q[1], v[3], v[4]))
        verts = out
    tris, idx, nv = ([], m['idx'], len(verts))
    for v0, i0, tn, tex in m['subs']:
        for t in range(tn):
            a, b_, c = (idx[i0 + 3 * t + j] for j in range(3))
            if max(v0 + a, v0 + b_, v0 + c) < nv:
                tris.append((verts[v0 + a], verts[v0 + b_], verts[v0 + c], tex))
    return tris

def cell_triangles(src, acr_file, lodselector, ref=None):
    m = load_mesh(src, acr_file, lodselector)
    return triangles(m, ref) if m else []

def render(tris, size=128, ss=2, margin=0.06):
    P = [[(-v[0], -v[2], v[1]) for v in t[:3]] for t in tris]
    xs = [p[0] for t in P for p in t]
    ys = [p[1] for t in P for p in t]
    x0, x1, y0, y1 = (min(xs), max(xs), min(ys), max(ys))
    span = max(x1 - x0, y1 - y0) * (1 + 2 * margin) or 1.0
    cx, cy = ((x0 + x1) / 2, (y0 + y1) / 2)
    S = size * ss
    scale = S / span
    canvas = [None] * (S * S)
    depth = [1000000000.0] * (S * S)
    ln = math.sqrt(sum((c * c for c in LIGHT)))
    L = tuple((c / ln for c in LIGHT))
    for (a, b, c), t in zip(P, tris):
        tex = t[3]
        (ax_, ay_, az_), (bx_, by_, bz_), (cx_, cy_, cz_) = (v[:3] for v in t[:3])
        ux, uy, uz = (bx_ - ax_, by_ - ay_, bz_ - az_)
        vx, vy, vz = (cx_ - ax_, cy_ - ay_, cz_ - az_)
        nx, ny, nz = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        nl = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        shade = 0.55 + 0.6 * abs(nx * L[0] + ny * L[1] + nz * L[2]) / nl
        pa = ((a[0] - cx) * scale + S / 2, (a[1] - cy) * scale + S / 2, a[2])
        pb = ((b[0] - cx) * scale + S / 2, (b[1] - cy) * scale + S / 2, b[2])
        pc = ((c[0] - cx) * scale + S / 2, (c[1] - cy) * scale + S / 2, c[2])
        den = (pb[1] - pc[1]) * (pa[0] - pc[0]) + (pc[0] - pb[0]) * (pa[1] - pc[1])
        if abs(den) < 1e-12:
            continue
        for py in range(max(0, int(min(pa[1], pb[1], pc[1]))), min(S - 1, int(max(pa[1], pb[1], pc[1])) + 1) + 1):
            for px in range(max(0, int(min(pa[0], pb[0], pc[0]))), min(S - 1, int(max(pa[0], pb[0], pc[0])) + 1) + 1):
                sx, sy = (px + 0.5, py + 0.5)
                l1 = ((pb[1] - pc[1]) * (sx - pc[0]) + (pc[0] - pb[0]) * (sy - pc[1])) / den
                l2 = ((pc[1] - pa[1]) * (sx - pc[0]) + (pa[0] - pc[0]) * (sy - pc[1])) / den
                l3 = 1 - l1 - l2
                if l1 < 0 or l2 < 0 or l3 < 0:
                    continue
                k = py * S + px
                d = l1 * pa[2] + l2 * pb[2] + l3 * pc[2]
                if d >= depth[k]:
                    continue
                if tex:
                    tw, th, tpx, cut = tex
                    u = l1 * t[0][3] + l2 * t[1][3] + l3 * t[2][3]
                    v = l1 * t[0][4] + l2 * t[1][4] + l3 * t[2][4]
                    q = (int(v % 1.0 * th) % th * tw + int(u % 1.0 * tw) % tw) * 4
                    if cut and tpx[q + 3] < 128:
                        continue
                    col = tuple((tpx[q + j] * shade for j in range(3)))
                else:
                    col = (180 * shade,) * 3
                depth[k] = d
                canvas[k] = col
    lums = sorted((sum(c) / 3 for c in canvas if c is not None))
    gain = 200.0 / max(1.0, lums[int(len(lums) * 0.97)]) if lums else 1.0

    def expose(c):
        return [255 * min(1.0, v * gain / 255) ** 0.85 for v in c]
    out = bytearray()
    for y in range(size):
        for x in range(size):
            cells = [canvas[(y * ss + j) * S + x * ss + i] for j in range(ss) for i in range(ss)]
            hit = [expose(c) for c in cells if c is not None]
            cover = len(hit) / len(cells)
            col = [sum((c[j] for c in hit)) / len(hit) if hit else 255 for j in range(3)]
            out += bytes((int(round(cover * col[j] + (1 - cover) * 255)) for j in range(3))) + b'\xff'
    return bytes(out)
_BADGES = {}

def badge_of(part, persona):
    key = (part, persona)
    if key in _BADGES:
        return _BADGES[key]
    g_mask, g_rgba, _n = part_icons.slot_badge(part)
    out = (g_mask, g_rgba)
    if persona is not None:
        d_mask, d_rgba, _n = part_icons.slot_badge(part, persona)
        gg = {i for i in range(4096) if g_mask[i] and g_rgba[i * 4] < 128}
        dg = {i for i in range(4096) if d_mask[i] and d_rgba[i * 4] < 128}
        if len(gg & dg) < 0.6 * len(gg):
            seen, keep = (set(), set())
            for s in dg:
                if s in seen:
                    continue
                comp, stack = ([], [s])
                seen.add(s)
                while stack:
                    i = stack.pop()
                    comp.append(i)
                    x, y = (i % 64, i // 64)
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            j = (y + dy) * 64 + x + dx
                            if 0 <= x + dx < 64 and 0 <= y + dy < 64 and (j in dg) and (j not in seen):
                                seen.add(j)
                                stack.append(j)
                if len(comp) >= 0.2 * len(dg):
                    keep |= set(comp)
            mask, rgba = (bytearray(4096), bytearray(b'\xff' * 4096 * 4))
            for i in range(4096):
                x, y = (i % 64, i // 64)
                if i in keep:
                    mask[i] = 1
                    rgba[i * 4:i * 4 + 3] = b'\x00\x00\x00'
                elif d_mask[i] and any(((y + dy) * 64 + x + dx in keep for dx in (-1, 0, 1) for dy in (-1, 0, 1))):
                    mask[i] = 1
            out = (bytes(mask), bytes(rgba))
    _BADGES[key] = out
    return out

def drawn(cells):
    return [(f, s) for f, s in cells if not any((k in s.lower() for k in SKIP))]

def persona_reference(src, cells):
    return reference([m for m in (load_mesh(src, f, s) for f, s in drawn(cells)) if m])

def icon(src, cells, badge=None, badge_from=None, ref=None, size=64):
    meshes = [m for m in (load_mesh(src, f, s) for f, s in drawn(cells)) if m]
    ref = ref or reference(meshes)
    tris = [t for m in meshes for t in triangles(m, ref)]
    if not tris:
        raise ValueError(f'part_render: nothing to draw in {cells}')
    if badge == 'Secondary':
        one = [t for t in tris if t[0][0] + t[1][0] + t[2][0] >= 0]
        if len(one) >= 0.3 * len(tris):
            tris = one
    pic = render(tris, size=max(128, size))
    if size == 64:
        pic = part_icons.crop_scaled(128, 128, pic, (0, 0, 128, 128))
    assert len(pic) == size * size * 4, (size, len(pic))
    stored = bytearray(part_icons.flip(size, size, pic))
    assert not (badge and size != 64), 'a badged icon is 64 px'
    if badge:
        mask, med = badge_of(badge, badge_from)
        for i in range(64 * 64):
            if mask[i]:
                stored[i * 4:i * 4 + 4] = med[i * 4:i * 4 + 4]
    return bytes(stored)

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('parts', nargs='+', help='ACR part files (each drawn by its LODSelector of the same name)')
    ap.add_argument('--badge', help='a slot badge: Head, Main, Secondary, Legs, Belt')
    ap.add_argument('--persona', type=int, help='whose own badge (part_icons.slot_badge)')
    a = ap.parse_args(argv)
    src = Source()
    px = icon(src, [(p, p) for p in a.parts], a.badge, a.persona)
    src.close()
    up = part_icons.flip(64, 64, px)
    os.makedirs(part_icons.OUT, exist_ok=True)
    path = os.path.join(part_icons.OUT, f'{a.parts[0]}_render.png')
    big = bytearray()
    for y in range(256):
        for x in range(256):
            q = (y // 4 * 64 + x // 4) * 4
            big += up[q:q + 4]
    part_icons.png(path, 256, 256, bytes(big))
    print(path)
if __name__ == '__main__':
    main()
