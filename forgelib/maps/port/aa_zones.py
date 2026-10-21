"""forgelib/maps/port/aa_zones.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    sys.path.insert(0, p)
import convert
import entity
import inventory
import mapwalk
import spawn_layers
import xrefs
import zones as Z
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from library_cell import cell_list, refs as convert_refs
OUT = os.path.join(ROOT, 'out', 'maps')
BLOCK = 'DataBlock_ACFE_CTF_2'
CTF_LAYER = 3296798500
IDS = convert.class_ids()
MAX_PANEL = 90.0
UV_PER_M = 683.2
UID_BASE = 1523777536
CLASS_IDS = {v for v in IDS.values() if v >= 1 << 24 and v & 16777215}
TEMPLATE_ENTITY = 'PROTO_AC3MP_OOB_NEW_CTF_Team01_001'
IDENTITY = struct.pack('<12f', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)

def area(poly):
    return sum((a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))) / 2

def ccw(poly):
    return poly if area(poly) > 0 else poly[::-1]

def side(a, b, p):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

def seg_dist(a, b, p):
    dx, dy = (b[0] - a[0], b[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1e-12)))
    return math.dist(p[:2], (a[0] + t * dx, a[1] + t * dy))

def inside(poly, p):
    c = False
    for a, b in zip(poly, poly[1:] + poly[:1]):
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
            c = not c
    return c

def seg_cross(p1, p2, q1, q2):
    d1, d2 = (side(q1, q2, p1), side(q1, q2, p2))
    d3, d4 = (side(p1, p2, q1), side(p1, p2, q2))
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)

def simple(poly):
    n = len(poly)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if seg_cross(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n]):
                return False
    return True

def clip(poly, a, b, keep_left):
    out = []
    s = lambda p: side(a, b, p) * (1 if keep_left else -1)
    for p, q in zip(poly, poly[1:] + poly[:1]):
        sp, sq = (s(p), s(q))
        if sp >= 0:
            out.append(p)
        if (sp >= 0) != (sq >= 0):
            t = sp / (sp - sq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    clean = []
    for p in out:
        if not clean or math.dist(p, clean[-1]) > 0.0001:
            clean.append(p)
    if clean and math.dist(clean[0], clean[-1]) <= 0.0001:
        clean.pop()
    return clean

def triangulate(poly):
    idx = list(range(len(poly)))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < 100000:
        guard += 1
        for n in range(len(idx)):
            i, j, k = (idx[n - 1], idx[n], idx[(n + 1) % len(idx)])
            a, b, c = (poly[i], poly[j], poly[k])
            if side(a, b, c) <= 1e-09:
                continue
            if any((side(a, b, poly[m]) > 0 and side(b, c, poly[m]) > 0 and (side(c, a, poly[m]) > 0) for m in idx if m not in (i, j, k))):
                continue
            tris.append((i, j, k))
            idx.pop(n)
            break
        else:
            raise SystemExit('ear clipping stuck: polygon not simple?')
    tris.append(tuple(idx))
    return tris

def convex_pieces(poly):
    pieces = [list(t) for t in triangulate(poly)]

    def is_convex(ids):
        pts = [poly[i] for i in ids]
        return all((side(pts[m - 1], pts[m], pts[(m + 1) % len(pts)]) >= -1e-09 for m in range(len(pts))))
    merged = True
    while merged:
        merged = False
        for x in range(len(pieces)):
            for y in range(x + 1, len(pieces)):
                A, B = (pieces[x], pieces[y])
                for i in range(len(A)):
                    u, v = (A[i], A[(i + 1) % len(A)])
                    if v in B and B[(B.index(v) + 1) % len(B)] == u:
                        ia, ib = ((i + 1) % len(A), B.index(u))
                        new = A[ia:] + A[:ia]
                        rest = B[ib:] + B[:ib]
                        cand = new[:-1] + rest[:-1]
                        cand = [c for n_, c in enumerate(cand) if c != cand[n_ - 1]]
                        if is_convex(cand):
                            pieces[x] = cand
                            pieces.pop(y)
                            merged = True
                            break
                if merged:
                    break
            if merged:
                break
    return pieces

def panels(a, b):
    n = max(1, math.ceil(math.dist(a, b) / MAX_PANEL))
    return [((a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n), (a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n)) for i in range(n)]

def section(a, b, zc, h, outline):
    w = math.dist(a, b)
    d = ((b[0] - a[0]) / w, (b[1] - a[1]) / w)
    nrm = (-d[1], d[0])
    th = math.atan2(d[1], d[0])
    c = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    raw = struct.pack('<4f', c[0], c[1], zc, 0.0)
    raw += struct.pack('<4f', 0.0, 0.0, math.sin(th / 2), math.cos(th / 2))
    raw += struct.pack('<4f', nrm[0], nrm[1], 0.0, 0.0)
    raw += struct.pack('<4f', d[0], d[1], 0.0, 0.0)
    raw += struct.pack('<2f', w, h) + bytes(48)
    fx = []
    if outline:
        n = math.ceil(w / 10)
        sp = w / n
        for i in range(n):
            t = -w / 2 + sp * (i + 0.5)
            fx.append(struct.pack('<4f2f', c[0] + d[0] * t, c[1] + d[1] * t, zc, 0.0, sp, h))
    return {'raw': raw, 'fx': fx}

def nbyte(x):
    return max(0, min(254, int((x + 1.0) * 127.5)))

def build_mesh(tmpl, quads, origin, zc, h):
    verts, idx = ([], [])
    lo, hi = (zc - h / 2, zc + h / 2)
    loc = [((a[0] - origin[0], a[1] - origin[1]), (b[0] - origin[0], b[1] - origin[1])) for a, b in quads]
    ext = max((max((abs(x) for p in (a, b) for x in p)) for a, b in loc))
    ext = max(ext, abs(lo - origin[2]), abs(hi - origin[2]))
    R = math.ceil(ext)
    q = lambda v: int(round(v / R * 32767))
    for (a, b), (wa, wb) in zip(loc, quads):
        w = math.dist(a, b)
        d = ((b[0] - a[0]) / w, (b[1] - a[1]) / w)
        out = (d[1], -d[0])
        nb = bytes([nbyte(out[0]), nbyte(out[1]), 127, 255])
        tb = bytes([nbyte(d[0]), nbyte(d[1]), 127, 255])
        base = len(verts)
        for (p, u), z in (((a, -w / 2), lo), ((b, w / 2), lo), ((a, -w / 2), hi), ((b, w / 2), hi)):
            verts.append(struct.pack('<4h', q(p[0]), q(p[1]), q(z - origin[2]), -8 * R) + nb + tb + struct.pack('<2h', int(round(u * UV_PER_M)), int(round((z - zc) * UV_PER_M))))
        idx += [base, base + 1, base + 2, base + 1, base + 3, base + 2]
    b = tmpl
    nv0 = struct.unpack_from('<I', b, 87)[0]
    ib0 = 91 + nv0
    nib0 = struct.unpack_from('<I', b, ib0)[0]
    tail = bytearray(b[ib0 + 4 + nib0:])
    k = tail.find(struct.pack('<I', 186463425))
    struct.pack_into('<H', tail, k + 8, len(verts))
    head = bytearray(b[:91])
    struct.pack_into('<I', head, 67, len(verts))
    struct.pack_into('<I', head, 75, len(idx) // 3)
    struct.pack_into('<I', head, 87, 20 * len(verts))
    body = bytes(head) + b''.join(verts) + struct.pack('<I', 2 * len(idx)) + struct.pack(f'<{len(idx)}H', *idx) + bytes(tail)
    return (body, R, (lo, hi))

def comp(body, name):
    return next(((s, e) for s, e, n in entity.components(body) or [] if n == name), None)

def pos(o):
    return struct.unpack_from('<3f', o['body'], 58)

def section_points(body, cname):
    pat = struct.pack('<I', IDS[cname])
    out, k = ([], body.find(pat))
    while k >= 0:
        out.append(struct.unpack_from('<3f', body, k + 4))
        k = body.find(pat, k + 4)
    return out

def oob_outline(body):
    pat = struct.pack('<I', IDS['OutOfBoundsSection'])
    segs, k = ([], body.find(pat))
    while k >= 0:
        v = struct.unpack_from('<16f', body, k + 4)
        c, nrm, w = ((v[0], v[1]), (v[8], v[9]), v[12])
        t = (-nrm[1], nrm[0])
        segs.append(((c[0] - t[0] * w / 2, c[1] - t[1] * w / 2), (c[0] + t[0] * w / 2, c[1] + t[1] * w / 2)))
        k = body.find(pat, k + 4)
    loop = [segs[0][0], segs[0][1]]
    left = segs[1:]
    while left:
        end = loop[-1]
        best = min(range(len(left)), key=lambda i: min(math.dist(end, left[i][0]), math.dist(end, left[i][1])))
        a_, b_ = left.pop(best)
        gap = min(math.dist(end, a_), math.dist(end, b_))
        if gap > 1.0:
            raise SystemExit(f"OOB segments don't chain: a {gap:.2f} m gap")
        loop.append(b_ if math.dist(end, a_) <= math.dist(end, b_) else a_)
    if math.dist(loop[0], loop[-1]) > 1.0:
        raise SystemExit(f"OOB outline doesn't close ({math.dist(loop[0], loop[-1]):.2f} m)")
    loop.pop()
    out = []
    for i, p in enumerate(loop):
        a_, b_ = (loop[i - 1], loop[(i + 1) % len(loop)])
        if abs(side(a_, p, b_)) / max(math.dist(a_, b_), 1e-09) > 0.05:
            out.append(p)
    return out

def member(convex, p):
    for secs in convex:
        if all((sum(((p[i] - struct.unpack_from('<3f', s['raw'], 0)[i]) * struct.unpack_from('<3f', s['raw'], 32)[i] for i in range(3))) >= 0 for s in secs)):
            return True
    return False

def set_ref(b, at, uid):
    return b[:at] + struct.pack('<I', uid) + b[at + 4:]

def picture(path, zones, area_pts, objs, size=640):
    import dxt
    xs = [p[0] for p in area_pts]
    ys = [p[1] for p in area_pts]
    pad = 15
    x0, x1, y0, y1 = (min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad)
    span = max(x1 - x0, y1 - y0)
    img = bytearray(bytes((32, 32, 32, 255)) * size * size)
    cols = [(200, 60, 60), (60, 110, 220)]
    for Y in range(size):
        for X in range(size):
            p = (x0 + X / (size - 1) * span, y1 - Y / (size - 1) * span, 0.0)
            for zi, z in enumerate(zones):
                if member(z['convex'], p):
                    img[4 * (Y * size + X):4 * (Y * size + X) + 3] = bytes(cols[zi])

    def dot(x, y, c, r):
        cx, cy = (int((x - x0) / span * (size - 1)), int((y1 - y) / span * (size - 1)))
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if 0 <= cx + dx < size and 0 <= cy + dy < size and (dx * dx + dy * dy <= r * r):
                    img[4 * ((cy + dy) * size + cx + dx):4 * ((cy + dy) * size + cx + dx) + 3] = bytes(c)
    for o in objs:
        if xrefs.cls('AC3', o['type']) != 'Entity':
            continue
        k = spawn_layers.spawn_kind(o['body'])
        q = pos(o)
        if comp(o['body'], 'FlagComponent'):
            dot(q[0], q[1], (255, 255, 0), 6)
        elif k and k[0] == 2:
            dot(q[0], q[1], (255, 255, 255), 3)
        elif k and k[0] == 0:
            dot(q[0], q[1], (120, 120, 120), 2)
    dxt.write_png(path, size, size, bytes(img), alpha=False)

def rewrite(p, drop, add_ents, new_objs, ctf, table=None):
    drop = set(drop or ())
    head = p.objects[0]
    b = head['body']
    n = struct.unpack_from('<I', b, 10)[0]
    end = 14 + 6 * n
    m = struct.unpack_from('<I', b, end)[0]
    entries = [b[k:k + 6] for k in range(14, end, 6)]
    world = [e for e in entries[:m] if struct.unpack_from('<I', e, 2)[0] not in drop]
    deps = [e for e in entries[m:] if struct.unpack_from('<I', e, 2)[0] not in drop]
    add = [b'\x01\x00' + struct.pack('<I', e['uid']) for e in add_ents]
    b = b[:10] + struct.pack('<I', len(world) + len(add) + len(deps)) + b''.join(world + add + deps) + struct.pack('<I', len(world) + len(add)) + b[end + 4:]
    objs = [(head['serial'], mapwalk.object_bytes(head, b), head['filter'])]
    rest = [o for o in p.objects[1:] if not o['has_uid'] or o['uid'] not in drop]
    last_entity = -1
    if add_ents:
        last_entity = max((i for i, o in enumerate(rest) if xrefs.cls('AC3', o['type']) == 'Entity' and o['filter'] is not None))
    for i, o in enumerate(rest):
        objs.append((o['serial'], mapwalk.object_bytes(o), o['filter']))
        if i == last_entity:
            objs += [(e['serial'], mapwalk.object_bytes(e), ctf) for e in add_ents]
    objs += [(o['serial'], mapwalk.object_bytes(o), None) for o in new_objs]
    d_ = mapwalk.rebuild(objs, p.filter_table if table is None else table)
    payload = compress(d_, first_chunk=mapwalk.first_chunk(d_))
    assert decompress(payload) == d_
    return payload

def gather(f, a):
    defined, kept, found, into = (set(), {}, {'wall': [], 'oob': [], 'flags': [], 'spawns': [], 'scores': []}, None)
    for x in f.records:
        if x.name == 'GlobalMetaFile' or not x.size:
            continue
        p = mapwalk.Payload(x, f.read(x))
        defined |= {o['uid'] for o in p.objects if o['has_uid']}
        if x.name == a.into:
            into = x.index
            kept[x.index] = (x, p)
        elif not a.cells:
            continue
        sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
        for o in p.objects:
            if xrefs.cls('AC3', o['type']) != 'Entity':
                continue
            if a.cells and (o['filter'] is None or CTF_LAYER not in sets[o['filter']]):
                continue
            for key, hit in (('wall', comp(o['body'], 'CTFWallComponent')), ('oob', comp(o['body'], 'OutOfBoundsComponent')), ('flags', comp(o['body'], 'FlagComponent')), ('scores', comp(o['body'], 'CTFScoreZoneComponent')), ('spawns', spawn_layers.spawn_kind(o['body']))):
                if hit:
                    found[key].append((x.index, o))
                    kept.setdefault(x.index, (x, p))
    if into is None:
        raise SystemExit(f'no {a.into} in the archive')
    if a.oob_from and (not found['oob']):
        for x in f.records:
            if x.name == a.oob_from and x.size:
                for o in mapwalk.Payload(x, f.read(x)).objects:
                    if xrefs.cls('AC3', o['type']) == 'Entity' and comp(o['body'], 'OutOfBoundsComponent'):
                        found['oob'].append((x.index, o))
    if a.area_box and (not found['oob']):
        found['oob'].append((into, {'name': 'play-area box', 'body': None}))
    wall, oob, flags = (found['wall'], found['oob'], found['flags'])
    if len(wall) != 1 or len(oob) != 1 or len(flags) != 2:
        raise SystemExit(f'need one CTF wall, one OOB area, two flags: {len(wall)}, {len(oob)}, {len(flags)}')
    return (kept, into, wall[0][1], wall[0][0], oob[0], [o for _i, o in flags], [o for _i, o in found['spawns']], defined, flags + found['scores'])

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--slot', default='AC3MP_Plantation')
    ap.add_argument('--cells', action='store_true', help='find the CTF border, flags, team spawns and play area in any record by their ACFE_CTF_2 layer (maps that keep them in cells, like Souk); run it after any shift of the map')
    ap.add_argument('--into', default=BLOCK, help='the record that gets the zone entities (default the CTF block)')
    ap.add_argument('--oob-from', default=None, metavar='RECORD', help="read the play area's outline from this record when the CTF block holds no OOB entity (Palazzio: the Deathmatch block's); nothing is written to it")
    ap.add_argument('--area-box', default=None, metavar='X0,Y0,X1,Y1', help='a map with no out-of-bounds outline for this mode (Palazzio: only its Deathmatch arena has one, smaller than the CTF area): the play area is this rectangle (metres), which must hold both flags, the CTF spawns and the border')
    ap.add_argument('--uid-base', type=lambda s: int(s, 0), default=UID_BASE, help="first uid tried for the new objects (a range of the map's own)")
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    report = []
    with Forge(mapwalk.archive_path('AC3', f'DataPC_{a.slot}.forge')) as f:
        r = next((x for x in f.records if x.name == BLOCK))
        slot = mapwalk.Payload(r, f.read(r))
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        kept, into, wall, wall_rec, (oob_rec, oob), flags, spawns, defined, ctf_pieces = gather(f, a)
    rec, mine = kept[into]
    if oob['body'] is None:
        x0, y0, x1, y1 = (float(v) for v in a.area_box.split(','))
        area_pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    else:
        area_pts = ccw(oob_outline(oob['body']))
    line = [(x, y) for x, y, _z in section_points(wall['body'], 'CTFWallSection')]
    if not simple(area_pts):
        raise SystemExit('the play area polygon is not simple')
    a0, a1 = (line[0], line[-1])
    dev = max((abs(side(a0, a1, p)) / math.dist(a0, a1) for p in line))
    if dev > 0.5:
        raise SystemExit(f"ACR's border bends ({dev:.2f} m off the straight line): only a straight split is handled")
    d = ((a1[0] - a0[0]) / math.dist(a0, a1), (a1[1] - a0[1]) / math.dist(a0, a1))
    far = 5000.0
    la, lb = ((a0[0] - d[0] * far, a0[1] - d[1] * far), (a1[0] + d[0] * far, a1[1] + d[1] * far))
    report.append(f"play area: {oob['name']}, {len(area_pts)} points, {area(area_pts):.0f} m2; border: {wall['name']}, {len(line)} points from ({a0[0]:.1f}, {a0[1]:.1f}) to ({a1[0]:.1f}, {a1[1]:.1f}), straight within {dev:.2f} m")
    if a.cells:
        report.append(f"from the ACFE_CTF_2 layer: play area in {kept[oob_rec][0].name}, border in {kept[wall_rec][0].name}, flags in {', '.join((kept[i][0].name for i in sorted({i for i, (x, p) in kept.items() for o in flags if o in p.objects})))}, {len(spawns)} spawn points; zones into {rec.name}")
    flags.sort(key=lambda o: o['name'])
    zs = [pos(o)[2] for o in spawns + flags]
    lo_z, hi_z = (min(zs) - 10.0, max(zs) + 30.0)
    zc, h = ((lo_z + hi_z) / 2, hi_z - lo_z)
    halves = []
    for fo in flags:
        fp = pos(fo)[:2]
        left = side(la, lb, fp) > 0
        half = clip(area_pts, la, lb, left)
        half = ccw(half)
        if not simple(half) or not inside(half, fp):
            raise SystemExit(f"{fo['name']}: its half is not a simple polygon holding the flag")
        halves.append((fo, half))
    if abs(sum((area(hh) for _f, hh in halves)) - area(area_pts)) > 1.0:
        raise SystemExit("the two halves don't add up to the play area")
    sobj = {o['uid']: o for o in slot.objects if o['has_uid']}
    tent = next((o for o in slot.objects if o['name'] == TEMPLATE_ENTITY))
    vs, ve = comp(tent['body'], 'Visual')
    tmesh = sobj[struct.unpack_from('<I', tent['body'], vs + 13)[0]]
    zs_, ze_ = comp(tent['body'], 'GameplayZoneComponent')
    tz = Z.parse(tent['body'][zs_:ze_])
    tset = sobj[struct.unpack_from('<I', tz['settings'], 2)[0]]
    material = sobj[struct.unpack_from('<I', tset['body'], 34)[0]]
    if xrefs.cls('AC3', material['type']) != 'Material':
        raise SystemExit("the settings' last reference isn't the border Material")
    taken = set(inventory.universe('AC3')) | set(inventory.universe('ACR')) | defined
    nxt = [a.uid_base]

    def fresh():
        while nxt[0] in taken:
            nxt[0] += 1
        taken.add(nxt[0])
        return nxt[0]
    new_objs, new_ents, parsed = ([], [], [])
    have_material = material['uid'] in {o['uid'] for o in mine.objects if o['has_uid']}
    for team, (fo, half) in enumerate(halves, 1):
        pieces = convex_pieces(half)
        outline = {(half[i], half[(i + 1) % len(half)]) for i in range(len(half))}
        convex, quads = ([], [])
        for pc in pieces:
            pts = [half[i] for i in pc]
            secs = []
            for p, q in zip(pts, pts[1:] + pts[:1]):
                on = (p, q) in outline
                for pa, pb in panels(p, q):
                    secs.append(section(pa, pb, zc, h, on))
                    if on:
                        quads.append((pa, pb))
            convex.append(secs)
        cx = sum((p[0] for p in half)) / len(half)
        cy = sum((p[1] for p in half)) / len(half)
        origin = (cx, cy, zc)
        mesh_uid, set_uid = (fresh(), fresh())
        mbody, R, (lo, hi) = build_mesh(tmesh['body'], quads, origin, zc, h)
        mbody = mbody[:2] + struct.pack('<I', mesh_uid) + mbody[6:]
        name = f"{fo['name']}_Zone"
        mesh = dict(tmesh, uid=mesh_uid, serial=mesh_uid, name=f'{name} GameplayZone Mesh', body=mbody, body_size=tmesh['body_size'] + len(mbody) - len(tmesh['body']))
        sbody = tset['body'][:2] + struct.pack('<I', set_uid) + tset['body'][6:]
        settings = dict(tset, uid=set_uid, serial=set_uid, body=sbody)
        body, removed = entity.strip(tent['body'], lambda n, el: n == 'InertComponent')
        if removed != ['InertComponent']:
            raise SystemExit('template entity: InertComponent not stripped')
        embedded = sorted({struct.unpack_from('<I', body, k)[0] for k in range(2, len(body) - 7) if struct.unpack_from('<I', body, k + 4)[0] in CLASS_IDS and struct.unpack_from('<I', body, k)[0] >= 65536 and (struct.unpack_from('<I', body, k)[0] != 4294967295) and (struct.unpack_from('<I', body, k)[0] not in CLASS_IDS)})
        renew = {u: fresh() for u in embedded}
        for u, v in renew.items():
            body = body.replace(struct.pack('<I', u), struct.pack('<I', v))
        ent_uid = struct.unpack_from('<I', body, 2)[0]
        body = body.replace(struct.pack('<I', tmesh['uid']), struct.pack('<I', mesh_uid))
        zs2, ze2 = comp(body, 'GameplayZoneComponent')
        z = Z.parse(body[zs2:ze2])
        z = dict(z, settings=z['settings'][:2] + struct.pack('<I', set_uid), convex=[[{'raw': s_['raw'], 'fx': s_['fx']} for s_ in secs] for secs in convex], border=[])
        zel = Z.write(z)
        body = body[:zs2] + zel + body[zs2 + z['end']:]
        body = body[:10] + IDENTITY + struct.pack('<3f', *origin) + body[70:]
        bv = body.find(struct.pack('<I', IDS['BoundingVolume']), 70)
        body = body[:bv + 4] + struct.pack('<6f', -R, -R, lo - zc, R, R, hi - zc) + body[bv + 28:]
        bad = Z.wall_problems(body, {mesh_uid: mesh})
        if bad:
            raise SystemExit(f'{name}: {bad[0]}')
        ent = dict(tent, uid=ent_uid, serial=ent_uid, name=name, body=body, body_size=tent['body_size'] + len(body) - len(tent['body']))
        new_ents.append(ent)
        new_objs += [mesh, settings]
        parsed.append((fo, Z.parse(zel), half))
        report.append(f'   renewed {len(renew)} embedded uids of the template entity')
        report.append(f"team {team} ({fo['name']} at ({pos(fo)[0]:.1f}, {pos(fo)[1]:.1f})): {len(half)} outline points, {area(half):.0f} m2, {len(pieces)} convex pieces, {sum((len(c) for c in convex))} panels ({len(quads)} on the outline, {sum((len(s['fx']) for c in convex for s in c))} FX points), wall mesh {4 * len(quads)} vertices, R {R} m, z {lo:.1f}..{hi:.1f}")
    if not have_material:
        new_objs.append(material)
    pieces_out = [(i, o) for i, o in ctf_pieces if i != into]
    if len({o['uid'] for _i, o in ctf_pieces}) != len(ctf_pieces):
        raise SystemExit('a flag / score zone uid defined twice')
    cell_uids = {o['uid'] for i, (x, p) in kept.items() if i != into for o in p.objects if o['has_uid']}
    for _i, o in pieces_out:
        inner = {u for u in convert_refs(o['body']) if u in cell_uids and u != o['uid']}
        if inner:
            raise SystemExit(f"{o['name']} names objects of its own cell ({', '.join(map(hex, sorted(inner)))}): not moved")
    moved = [o for _i, o in pieces_out]
    drops = {}
    for i, o in pieces_out:
        drops.setdefault(i, set()).add(o['uid'])
    drops.setdefault(wall_rec, set()).add(wall['uid'])
    table = mine.filter_table
    sets = spawn_layers.filter_sets(table) if table else []
    ctf = next((i for i, s in enumerate(sets) if s == {CTF_LAYER}), None)
    if ctf is None:
        if not a.cells:
            raise SystemExit(f'{rec.name} has no ACFE_CTF_2-only filter')
        import mode_spawns
        table, ctf = mode_spawns.add_filter(table, [CTF_LAYER])
        report.append(f'{rec.name}: ACFE_CTF_2-only filter added (index {ctf})')
    oob_copy, oob_objs = ([], [])
    if a.cells and rec.name.startswith('DataBlock_') and (oob['body'] is not None) and (oob_rec != into) and (not any((xrefs.cls('AC3', o['type']) == 'Entity' and comp(o['body'], 'OutOfBoundsComponent') for o in mine.objects))):
        src_p = kept[oob_rec][1]
        here = {o['uid'] for o in mine.objects if o['has_uid']}
        by_uid = {o['uid']: o for o in src_p.objects[1:] if o['has_uid']}
        need, todo = (set(), [oob['uid']])
        while todo:
            u = todo.pop()
            if u in need or u not in by_uid or u in here:
                continue
            need.add(u)
            todo += [v for v in convert_refs(by_uid[u]['body']) if v != u]
        oob_copy = [oob]
        oob_objs = [o for o in src_p.objects[1:] if o['has_uid'] and o['uid'] in need and (o['uid'] != oob['uid'])]
        if any((xrefs.cls('AC3', o['type']) == 'Entity' for o in oob_objs)):
            raise SystemExit(f"{oob['name']}: names another entity of {kept[oob_rec][0].name}; not copied")
        report.append(f"play area {oob['name']} copied into {rec.name} from {kept[oob_rec][0].name} with " + (', '.join((f"{o['name'] or hex(o['uid'])} ({xrefs.cls('AC3', o['type'])})" for o in oob_objs)) or 'nothing else'))
    set_payload(image, rec.index, rewrite(mine, drops.get(into), moved + oob_copy + new_ents, oob_objs + new_objs, ctf, None if table is mine.filter_table else table))
    touched = {into}
    for i, uids in sorted(drops.items()):
        if i != into:
            set_payload(image, i, rewrite(kept[i][1], uids, [], [], None))
            touched.add(i)
    if moved:
        report.append(f'moved into {rec.name}: ' + ', '.join((f"{o['name']} (from {kept[i][0].name})" for i, o in pieces_out)))
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = True
    with Forge(dest) as f, Forge(src) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index not in touched))
        q = mapwalk.Payload(f.records[rec.index], f.read(f.records[rec.index]))
        outq = {i: mapwalk.Payload(f.records[i], f.read(f.records[i])) for i in touched if i != into}
    wq = outq.get(wall_rec, q)
    for i, uids in sorted(drops.items()):
        if i == into:
            continue
        wp, cq = (kept[i][1], outq[i])
        before = [mapwalk.object_bytes(o) for o in wp.objects[1:] if not o['has_uid'] or o['uid'] not in uids]
        after = [mapwalk.object_bytes(o) for o in cq.objects[1:]]
        n0, l0, m0 = cell_list(wp.objects[0]['body'])
        n1, l1, m1 = cell_list(cq.objects[0]['body'])
        cell_ok = before == after and l1 == [u for u in l0 if u not in uids] and ((n1, m1) == (n0 - sum((u in uids for u in l0)), m0 - sum((u in uids for u in l0[:m0])))) and (cq.filter_table == wp.filter_table)
        ok &= cell_ok
        names = [o['name'] for o in wp.objects if o['has_uid'] and o['uid'] in uids]
        report.append(f"{kept[i][0].name}: {', '.join(names)} out, everything else unchanged and in order, list (N, M) ({n0}, {m0}) -> ({n1}, {m1}): {cell_ok}")
    qsets = spawn_layers.filter_sets(q.filter_table)
    for o in moved:
        g_ = next((x for x in q.objects if x['has_uid'] and x['uid'] == o['uid']), None)
        good = g_ is not None and g_['body'] == o['body'] and (qsets[g_['filter']] == {CTF_LAYER})
        ok &= good
        report.append(f"{o['name']} in {rec.name}, body unchanged, ACFE_CTF_2-only filter: {good}")
    for o in oob_copy + oob_objs:
        g_ = next((x for x in q.objects if x['has_uid'] and x['uid'] == o['uid']), None)
        good = g_ is not None and g_['body'] == o['body'] and (o not in oob_copy or qsets[g_['filter']] == {CTF_LAYER})
        ok &= good
        report.append(f"{o['name'] or hex(o['uid'])} copied into {rec.name}, body unchanged: {good}")
    if a.cells and rec.name.startswith('DataBlock_') and (oob['body'] is not None):
        own = [o for o in q.objects if xrefs.cls('AC3', o['type']) == 'Entity' and comp(o['body'], 'OutOfBoundsComponent') and (o['filter'] is not None) and (CTF_LAYER in qsets[o['filter']])]
        outl = ccw(oob_outline(own[0]['body'])) if own else None
        outside = [] if outl is None else [o['name'] for o in flags + spawns if not inside(outl, pos(o))]
        ok &= bool(own) and (not outside)
        report.append(f"out-of-bounds area in {rec.name} for Artifact Assault: {(own[0]['name'] if own else 'NONE')}" + (f'; AA pieces outside it: {outside}' if outside else '; every flag / CTF spawn inside it' if own else ''))
    zones = []
    for o in q.objects:
        if xrefs.cls('AC3', o['type']) == 'Entity' and comp(o['body'], 'GameplayZoneComponent'):
            s_, e_ = comp(o['body'], 'GameplayZoneComponent')
            zz = Z.parse(o['body'][s_:e_])
            closing = Z.closing_problems(o['body'])
            ok &= Z.write(zz) == o['body'][s_:s_ + zz['end']] and (not closing)
            report += [f"{o['name']}: {x}" for x in closing]
            zones.append((o['name'], zz))
    ok &= len(zones) == 2 and (not any((comp(o['body'], 'CTFWallComponent') for o in q.objects + wq.objects if xrefs.cls('AC3', o['type']) == 'Entity')))
    checks = []
    for (fo, zz, half), (zname, written) in zip(parsed, zones):
        fp = pos(fo)
        mine_in = member(written['convex'], fp)
        other = [w for n_, w in zones if n_ != zname][0]
        other_in = member(other['convex'], fp)
        ok &= mine_in and (not other_in)
        checks.append(f"{fo['name']} in its zone {mine_in}, in the other {other_in}")
    team_ok = team_n = 0
    for o in spawns if a.cells else q.objects:
        k = spawn_layers.spawn_kind(o['body']) if xrefs.cls('AC3', o['type']) == 'Entity' else None
        if k and k[0] == 2 and (k[1] in (1, 2)):
            p_ = pos(o)
            good = member(zones[k[1] - 1][1]['convex'], p_) and (not member(zones[2 - k[1]][1]['convex'], p_))
            team_ok += good
            team_n += 1
            ok &= good
    ok &= team_n > 0
    xs = [p[0] for p in area_pts]
    ys = [p[1] for p in area_pts]
    grid = [(x, y, zc) for x in range(int(min(xs)), int(max(xs)) + 1, 2) for y in range(int(min(ys)), int(max(ys)) + 1, 2) if inside(area_pts, (x, y))]
    counts = [sum((member(zz['convex'], p) for _n, zz in zones)) for p in grid]
    edge = sum((1 for p, c in zip(grid, counts) if c != 1 and abs(side(la, lb, p)) / math.dist(la, lb) < 0.01))
    rim = sum((1 for p, c in zip(grid, counts) if c != 1 and abs(side(la, lb, p)) / math.dist(la, lb) >= 0.01 and (min((seg_dist(a_, b_, p) for a_, b_ in zip(area_pts, area_pts[1:] + area_pts[:1]))) < 0.01)))
    bad = sum((1 for c in counts if c != 1)) - edge - rim
    ok &= bad == 0 and others
    shared = set(inventory.SHARED)
    u3 = inventory.universe('AC3')
    with Forge(dest) as f:
        built, worlds = (set(), set())
        for x in f.records:
            if x.name != 'GlobalMetaFile' and x.size:
                objs_ = mapwalk.Payload(x, f.read(x)).objects
                built |= {o['uid'] for o in objs_ if o['has_uid']}
                if any((xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager' for o in objs_)):
                    worlds.add(x.name)

    def scan(o):
        b_ = o['body']
        if xrefs.cls('AC3', o['type']) == 'Mesh':
            return b_[:91] + b_[91 + struct.unpack_from('<I', b_, 87)[0]:]
        c_ = comp(b_, 'GameplayZoneComponent') if xrefs.cls('AC3', o['type']) == 'Entity' else None
        return b_[:c_[0] + 20] + b_[c_[1]:] if c_ else b_

    def known(v):
        return v in built or any((h_[0] in shared for h_ in u3.get(v, [])))
    unresolved = set()
    for o in new_objs + new_ents:
        if xrefs.cls('AC3', o['type']) == 'GameplayZoneSettings':
            cands = {k: struct.unpack_from('<I', o['body'], k + 2)[0] for k in (16, 32)}
        else:
            sb = scan(o)
            cands = {j: struct.unpack_from('<I', sb, j + 2)[0] for j in range(6, len(sb) - 5) if sb[j] == 1 and sb[j + 1] in (0, 1, 2)}
        good = {j for j, v in cands.items() if known(v)}
        unresolved |= {hex(v) for j, v in cands.items() if v >= 268435456 and (not known(v)) and (not any((abs(j - g) <= 2 for g in good)))}
    unresolved = sorted(unresolved)
    ok &= not unresolved
    import aa_records
    with Forge(dest) as f:
        rfiles = {x.name: mapwalk.Payload(x, f.read(x)) for x in f.records if x.index in touched or x.name == BLOCK or x.name in worlds}
        rec_ok, rec_problems, rec_notes, _one = aa_records.judge(aa_records.pieces(rfiles), rfiles)
    ok &= rec_ok
    report.append(f"flags / score zones / territories in one record (aa_records): {'; '.join(rec_problems + rec_notes) or 'yes, ' + _one}")
    report.append(f"references of the new objects not resolving in the build or AC3's shared archives: {unresolved}")
    report += checks
    report.append(f'team spawns (type 2, teams 1-2) inside their own zone only: {team_ok} of {team_n}')
    report.append(f"play area sampled every 2 m: {len(grid)} points, in exactly one zone {counts.count(1)}, on the border line itself {edge}, on the play area's outline {rim}, wrong {bad}")
    report.append(f"verify: zones re-read byte-exact, CTF_Border gone, flags / spawns / area as above, every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    picture(os.path.join(out_dir, 'aa_zones.png'), [zz for _n, zz in zones], area_pts, flags + spawns if a.cells else q.objects)
    report.append('picture: aa_zones.png (team 1 red, team 2 blue, flags yellow, team spawns white, respawns grey)')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
