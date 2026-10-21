"""forgelib/modes/aa_variant.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import bisect
import json
import math
import os
import shutil
import struct
import subprocess
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'maps', 'port')):
    if p not in sys.path:
        sys.path.insert(0, p)
import aa_map
import aa_zones as AZ
import convert
import entity
import inventory
import mapwalk
import objects
import paths
import spawn_layers
import xrefs
import zones as Z
from bootstrap_build import Bootstrap, read_back_objects
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, add_file, set_payload, write
from persona import UISTRING, node_append, node_entries, obj, set_u32, u32
OUT = os.path.join(ROOT, 'out', 'modes')
LAYOUTS = os.path.join(HERE, 'layouts')
IDS = convert.class_ids()
CLASS_IDS = AZ.CLASS_IDS - {33554432}
CTF_LAYER = AZ.CTF_LAYER
CTF_BLOCK = 'DataBlock_ACFE_CTF_2'
GROUP = 'ACFE_Gamemodes'
ASSOC = 62819144
UID_BLOCK = 1533083648
VARIANTS = {'wolcott_reversed': dict(archive='DataPC_AC3MP_Fort_Wolcott.forge', mpworld=3526959583, unlock=3526959582, label='Fort_Wolcott_Reversed', layer_name='Fort_Wolcott_Reversed_CTF', name_id=1525022723, name_text='FORT WOLCOTT - REVERSED', unlock_index=4994, flags={1: (-34.4, 33.6, 0.0), 2: (30.1, -42.1, 0.0)}, starts={1: 'Manhunt_InitialSpw_NW_00{}', 2: 'Manhunt_InitialSpw_SE_00{}'}, border=dict(through=(6.0, -13.0), angle=51.0, offset=14.0), zc=0.0, height=60.0)}

def embedded(body):
    return sorted({struct.unpack_from('<I', body, k)[0] for k in range(2, len(body) - 7) if struct.unpack_from('<I', body, k + 4)[0] in CLASS_IDS and 33554432 <= struct.unpack_from('<I', body, k)[0] != 4294967295 and (struct.unpack_from('<I', body, k)[0] not in CLASS_IDS)})

def remap(body, mapping):
    for u, v in mapping.items():
        body = body.replace(struct.pack('<I', u), struct.pack('<I', v))
    return body

def is_entity(o):
    return xrefs.cls('AC3', o['type']) == 'Entity'

def payload_bytes(objs, table):
    d = mapwalk.rebuild(objs, table)
    data = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(data) == d
    return data

def territory_outline(stock_zones):
    segs = []
    for z in stock_zones:
        for c in z['convex']:
            for s in c:
                f = Z.section_fields(s['raw'])
                c0, d, w = (f['v0'], f['v3'], f['pair'][0])
                segs.append(((c0[0] - d[0] * w / 2, c0[1] - d[1] * w / 2), (c0[0] + d[0] * w / 2, c0[1] + d[1] * w / 2), f['v2'][:2]))

    def backed(s):
        mid = ((s[0][0] + s[1][0]) / 2, (s[0][1] + s[1][1]) / 2)
        for t in segs:
            if t is s or t[2][0] * s[2][0] + t[2][1] * s[2][1] > -0.9:
                continue
            if AZ.seg_dist(t[0], t[1], mid) < 0.3:
                return True
        return False
    outer = [(a, b) for a, b, n in segs if not backed((a, b, n))]
    loop, left = ([outer[0][0], outer[0][1]], outer[1:])
    while left:
        end = loop[-1]
        i = min(range(len(left)), key=lambda i: min(math.dist(end, left[i][0]), math.dist(end, left[i][1])))
        a_, b_ = left.pop(i)
        if min(math.dist(end, a_), math.dist(end, b_)) > 0.5:
            raise SystemExit("the stock territories' outer edge doesn't chain")
        loop.append(b_ if math.dist(end, a_) <= math.dist(end, b_) else a_)
    if math.dist(loop[0], loop[-1]) > 0.5:
        raise SystemExit("the stock territories' outer edge doesn't close")
    loop.pop()
    pts = [p for i, p in enumerate(loop) if abs(AZ.side(loop[i - 1], p, loop[(i + 1) % len(loop)])) / math.dist(loop[i - 1], loop[(i + 1) % len(loop)]) > 0.05]
    return AZ.ccw(pts)

def load_variant(name):
    if name in VARIANTS:
        return VARIANTS[name]
    path = os.path.join(LAYOUTS, f'{name}.json')
    if not os.path.exists(path):
        raise SystemExit(f'no variant {name}: neither in VARIANTS nor {path}')
    with open(path, encoding='utf-8') as fh:
        cfg = json.load(fh)
    cfg['flags'] = {int(t): tuple(v) for t, v in cfg['flags'].items()}
    cfg['starts'] = {int(t): v for t, v in cfg['starts'].items()}
    if 'divider' in cfg:
        cfg['divider'] = [tuple(p) for p in cfg['divider']]
    cfg.setdefault('layer_name', f'{name}_CTF')
    cfg.setdefault('zc', 0.0)
    cfg.setdefault('height', 60.0)
    return cfg

def start_names(spec):
    names = [spec.format(n) for n in range(1, 5)] if isinstance(spec, str) else list(spec)
    if len(names) != 4:
        raise SystemExit(f'team starts: need 4 placements, got {len(names)}')
    return names

def _cross(p, q, a, b):
    r = (q[0] - p[0], q[1] - p[1])
    s = (b[0] - a[0], b[1] - a[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    t = ((a[0] - p[0]) * s[1] - (a[1] - p[1]) * s[0]) / den
    u = ((a[0] - p[0]) * r[1] - (a[1] - p[1]) * r[0]) / den
    if 0 <= t <= 1 and 0 <= u < 1:
        return (t, u, (p[0] + t * r[0], p[1] + t * r[1]))
    return None

def split_polyline(area, line):

    def ext(a, b):
        d = math.dist(a, b)
        return (a[0] + (a[0] - b[0]) / d * 5000, a[1] + (a[1] - b[1]) / d * 5000)
    path = [ext(line[0], line[1])] + list(line) + [ext(line[-1], line[-2])]
    n = len(area)
    hits = []
    for i in range(len(path) - 1):
        for j in range(n):
            c = _cross(path[i], path[i + 1], area[j], area[(j + 1) % n])
            if c:
                hits.append((i, c[0], j, c[1], c[2]))
    hits.sort()
    hits = [h for i, h in enumerate(hits) if i == 0 or math.dist(h[4], hits[i - 1][4]) > 0.001]
    if len(hits) != 2:
        raise SystemExit(f"the divider must cross the territories' outer edge exactly twice: it crosses {len(hits)} times")
    (i1, _t1, j1, u1, p1), (i2, _t2, j2, u2, p2) = hits
    inner = [p1] + path[i1 + 1:i2 + 1] + [p2]

    def walk(ja, ua, jb, ub):
        out, j = ([], ja)
        if ja == jb and ub > ua:
            return out
        while True:
            j = (j + 1) % n
            out.append(area[j])
            if j == jb:
                return out

    def clean(poly, closed=True):
        out = []
        for q in poly:
            if not out or math.dist(q, out[-1]) > 1e-06:
                out.append(q)
        while closed and len(out) > 1 and (math.dist(out[0], out[-1]) <= 1e-06):
            out.pop()
        return out
    inner = clean(inner, closed=False)
    left = clean(inner + walk(j2, u2, j1, u1))
    right = clean(inner[::-1] + walk(j1, u1, j2, u2))
    return (AZ.ccw(left), AZ.ccw(right), inner)

def split_halves(cfg, area_pts):
    halves = {}
    if 'divider' in cfg:
        a_half, b_half, inner = split_polyline(area_pts, cfg['divider'])
        for t in (1, 2):
            fp = cfg['flags'][t][:2]
            mine = [h for h in (a_half, b_half) if AZ.inside(h, fp)]
            if len(mine) != 1:
                raise SystemExit(f"team {t}'s flag {fp} isn't on one side of the divider")
            halves[t] = mine[0]
        if halves[1] is halves[2]:
            raise SystemExit('both flags are on the same side of the divider')
        path, how = (inner, f"divider with {len(cfg['divider'])} points")
    else:
        bd = cfg['border']
        ang = math.radians(bd['angle'])
        dv = (math.cos(ang), math.sin(ang))
        c = (bd['through'][0] - dv[1] * bd['offset'], bd['through'][1] + dv[0] * bd['offset'])
        la, lb = ((c[0] - dv[0] * 5000, c[1] - dv[1] * 5000), (c[0] + dv[0] * 5000, c[1] + dv[1] * 5000))
        for t in (1, 2):
            fp = cfg['flags'][t][:2]
            halves[t] = AZ.ccw(AZ.clip(area_pts, la, lb, AZ.side(la, lb, fp) > 0))
        path, how = ([la, lb], f"border through ({c[0]:.1f}, {c[1]:.1f}) at {bd['angle']} degrees")
    for t in (1, 2):
        if not AZ.simple(halves[t]) or not AZ.inside(halves[t], cfg['flags'][t][:2]):
            raise SystemExit(f'team {t}: its half is not a simple polygon holding its flag')
    if abs(sum((AZ.area(h) for h in halves.values())) - AZ.area(area_pts)) > 1.0:
        raise SystemExit("the two halves don't add up to the territories' outline")
    return (halves, path, how)

def zone_convex(half, zc, h):
    pieces = AZ.convex_pieces(half)
    outline = {(half[i], half[(i + 1) % len(half)]) for i in range(len(half))}
    convex, quads = ([], [])
    for pc in pieces:
        pts = [half[i] for i in pc]
        secs = []
        for p_, q_ in zip(pts, pts[1:] + pts[:1]):
            on = (p_, q_) in outline
            for pa, pb in AZ.panels(p_, q_):
                secs.append(AZ.section(pa, pb, zc, h, on))
                if on:
                    quads.append((pa, pb))
        convex.append(secs)
    return (convex, quads, pieces)

def path_dist(path, p):
    return min((AZ.seg_dist(a, b, p) for a, b in zip(path, path[1:])))

def layout_checks(cfg, zones, starts, resp, area_pts, path, oob, report, png, bg=None, ctf=None):
    ok = True
    mem = lambda t, q: AZ.member(zones[t]['convex'], q)
    for t in (1, 2):
        fp = cfg['flags'][t]
        good = mem(t, fp) and (not mem(3 - t, fp))
        ok &= good
        report.append(f"team {t}'s flag {fp} in its own territory only: {good}")
    st = {t: sum((1 for q in starts[t] if mem(t, q) and (not mem(3 - t, q)))) for t in (1, 2)}
    ok &= st == {1: 4, 2: 4} and all((len(starts[t]) == 4 for t in (1, 2)))
    report.append(f'team starts in their own territory only: team 1 {st[1]} of 4, team 2 {st[2]} of 4')
    side_n = {t: sum((1 for q in resp if mem(t, q))) for t in (1, 2)}
    ok &= side_n[1] > 0 and side_n[2] > 0
    report.append(f'AA respawns (type 0) by territory: team 1 {side_n[1]}, team 2 {side_n[2]} of {len(resp)} (each team respawns only inside its own territory: none on a side fails)')
    xs, ys = ([q[0] for q in area_pts], [q[1] for q in area_pts])
    grid = [(x, y, cfg['zc']) for x in range(int(min(xs)), int(max(xs)) + 1, 2) for y in range(int(min(ys)), int(max(ys)) + 1, 2) if AZ.inside(area_pts, (x, y))]
    cnt = [mem(1, q) + mem(2, q) for q in grid]
    on_line = sum((1 for q, c in zip(grid, cnt) if c != 1 and path_dist(path, q) < 0.01))
    rim = sum((1 for q, c in zip(grid, cnt) if c != 1 and path_dist(path, q) >= 0.01 and (min((AZ.seg_dist(a_, b_, q) for a_, b_ in zip(area_pts, area_pts[1:] + area_pts[:1]))) < 0.01)))
    bad = sum((1 for c in cnt if c != 1)) - on_line - rim
    ok &= bad == 0
    report.append(f'territories sampled every 2 m: {len(grid)} points, in exactly one {cnt.count(1)}, on the divider {on_line}, on the outer edge {rim}, wrong {bad}')
    ox, oy = ([q[0] for q in oob], [q[1] for q in oob])
    play = [(x + 0.5, y + 0.5, cfg['zc']) for x in range(int(min(ox)) - 1, int(max(ox)) + 1) for y in range(int(min(oy)) - 1, int(max(oy)) + 1) if AZ.inside(oob, (x + 0.5, y + 0.5))]
    share = {t: sum((1 for q in play if mem(t, q))) / len(play) for t in (1, 2)}
    d = {t: path_dist(path, cfg['flags'][t][:2]) for t in (1, 2)}
    report.append(f"play area (the OOB outline, unchanged) by team: {share[1]:.3f} / {share[2]:.3f} (in neither: {1 - share[1] - share[2]:.3f}, outside the stock territories' outer edge); flags {math.dist(cfg['flags'][1][:2], cfg['flags'][2][:2]):.1f} m apart, {d[1]:.1f} / {d[2]:.1f} m from the divider")
    stats = f'play area {share[1] * 100:.0f} / {share[2] * 100:.0f} %   respawns {side_n[1]} / {side_n[2]}   flags {d[1]:.0f} / {d[2]:.0f} m from the divider'
    if ctf:
        for i, q in enumerate(ctf):
            dd = [math.dist(q[:2], cfg['flags'][t][:2]) for t in (1, 2)]
            report.append(f'True CTF spot {i + 1} {tuple(q)}: in the play area {AZ.inside(oob, q[:2])}, {dd[0]:.1f} / {dd[1]:.1f} m from the two bases')
    plan_picture(png, cfg, area_pts, path, zones, starts, resp, oob, stats, bg=bg, ctf=ctf)
    return ok

def footprints(src):
    out = []
    with Forge(src) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size or (not (r.name.startswith('Cell') or r.name.endswith('_Fakes'))):
                continue
            try:
                p = mapwalk.Payload(r, f.read(r))
            except Exception:
                continue
            for o in p.objects:
                b = o['body']
                if not is_entity(o) or len(b) < 74:
                    continue
                names = {n for _s, _e, n in entity.components(b) or []}
                if not names & {'InertComponent', 'RigidBody', 'MultiInertComponent'}:
                    continue
                k = b.rfind(struct.pack('<I', IDS['BoundingVolume']))
                if k < 0:
                    continue
                v = struct.unpack_from('<6f', b, k + 4)
                m = struct.unpack_from('<16f', b, 10)
                if v[5] - v[2] < 1.5 or (v[3] - v[0]) * (v[4] - v[1]) > 6000:
                    continue
                corners = [(m[12] + x * m[0] + y * m[4], m[13] + x * m[1] + y * m[5]) for x, y in ((v[0], v[1]), (v[3], v[1]), (v[3], v[4]), (v[0], v[4]))]
                out.append((corners, m[14] + v[5]))
    return out

def plan_picture(png, cfg, area_pts, path, zones, starts, resp, oob, stats, bg=None, ctf=None, size=1100):
    from PIL import Image, ImageDraw
    oob_xy = [(q[0], q[1]) for q in oob]
    xs = [q[0] for q in oob_xy] + [cfg['flags'][t][0] for t in (1, 2)]
    ys = [q[1] for q in oob_xy] + [cfg['flags'][t][1] for t in (1, 2)]
    pad = 12
    x0, x1, y0, y1 = (min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad)
    span = max(x1 - x0, y1 - y0)
    margin = 40
    sc = (size - 2 * margin) / span
    P = lambda x, y: (margin + (x - x0) * sc, margin + (y1 - y) * sc)
    W = lambda x: (x - margin) / sc + x0
    H = lambda y: y1 - (y - margin) / sc
    img = Image.new('RGB', (size, size + 30), (24, 24, 24))
    if bg:
        tv = Image.open(bg['png'])
        k = bg['px_per_m']
        crop = tv.crop((int((x0 - bg['x0']) * k), int((bg['y1'] - (y0 + span)) * k), int((x0 + span - bg['x0']) * k), int((bg['y1'] - y0) * k)))
        img.paste(crop.resize((size - 2 * margin, size - 2 * margin)), (margin, margin))
    tint = Image.new('RGBA', img.size, (0, 0, 0, 0))
    px = tint.load()
    for Y in range(margin, size - margin):
        for X in range(margin, size - margin):
            q = (W(X), H(Y), cfg['zc'])
            if AZ.member(zones[1]['convex'], q):
                px[X, Y] = (230, 40, 40, 60 if bg else 150)
            elif AZ.member(zones[2]['convex'], q):
                px[X, Y] = (40, 90, 240, 60 if bg else 150)
    img = Image.alpha_composite(img.convert('RGBA'), tint).convert('RGB')
    d = ImageDraw.Draw(img, 'RGBA')
    for gx in range(int(math.floor(x0 / 10) * 10), int(x1) + 1, 10):
        d.line([P(gx, y0), P(gx, y0 + span)], fill=(255, 255, 255, 35 if gx % 50 else 80))
        if gx % 20 == 0:
            d.text((P(gx, y1)[0] - 8, 8), str(gx), fill=(200, 200, 200))
    for gy in range(int(math.floor(y0 / 10) * 10), int(y0 + span) + 1, 10):
        d.line([P(x0, gy), P(x0 + span, gy)], fill=(255, 255, 255, 35 if gy % 50 else 80))
        if gy % 20 == 0:
            d.text((6, P(x0, gy)[1] - 6), str(gy), fill=(200, 200, 200))
    d.line([P(*q) for q in oob_xy + oob_xy[:1]], fill=(255, 255, 255, 220), width=1)
    d.line([P(*q[:2]) for q in path], fill=(255, 220, 0, 255), width=3)
    for i, q in enumerate(cfg.get('divider', [])):
        c = P(*q)
        d.ellipse([c[0] - 5, c[1] - 5, c[0] + 5, c[1] + 5], outline=(255, 220, 0), width=2)
        d.text((c[0] + 7, c[1] - 7), f'{i} ({q[0]:g}, {q[1]:g})', fill=(255, 230, 120))

    def arrow(q, facing, col, r):
        c = P(q[0], q[1])
        if facing is None:
            return
        f = math.radians(facing)
        d.line([c, (c[0] + math.cos(f) * (r + 7), c[1] - math.sin(f) * (r + 7))], fill=col, width=2)
    for q in resp:
        c = P(q[0], q[1])
        arrow(q, q[3] if len(q) > 3 else None, (220, 220, 220), 3)
        d.ellipse([c[0] - 3, c[1] - 3, c[0] + 3, c[1] + 3], fill=(200, 200, 200), outline=(40, 40, 40))
    for t, col in ((1, (255, 110, 110)), (2, (120, 160, 255))):
        for q in starts.get(t, []):
            c = P(q[0], q[1])
            arrow(q, q[3] if len(q) > 3 else None, col, 4)
            d.rectangle([c[0] - 4, c[1] - 4, c[0] + 4, c[1] + 4], fill=(255, 255, 255), outline=col, width=2)
        f_ = cfg['flags'][t]
        c = P(f_[0], f_[1])
        d.ellipse([c[0] - 8, c[1] - 8, c[0] + 8, c[1] + 8], fill=(255, 220, 0), outline=col, width=3)
        d.text((c[0] + 10, c[1] + 4), f'team {t} flag ({f_[0]:g}, {f_[1]:g}, {f_[2]:g})', fill=col)
    for i, q in enumerate(ctf or []):
        c = P(q[0], q[1])
        d.polygon([(c[0], c[1] - 8), (c[0] + 8, c[1]), (c[0], c[1] + 8), (c[0] - 8, c[1])], fill=(240, 190, 40), outline=(60, 40, 0))
        d.text((c[0] + 10, c[1] - 6), f'CTF {i + 1}', fill=(250, 210, 90))
    d.text((margin, size + 6), f"{cfg.get('title', '')}   {stats}   (north = +y up, 10 m grid)", fill=(230, 230, 230))
    img.save(png)

class _Block:

    def __init__(self, rec, objects, filter_table, prefix):
        self.rec, self.objects, self.filter_table, self.prefix = (rec, objects, filter_table, prefix)

def import_foreign(blk, foreign, fresh, new_uids, report):
    from mode_spawns import add_filter
    table = blk.filter_table
    sets = spawn_layers.filter_sets(table)
    aa = next((i for i, s in enumerate(sets) if s == {CTF_LAYER}), None)
    if aa is None:
        table, aa = add_filter(table, [CTF_LAYER])
    have = {o['uid'] for o in blk.objects if o['has_uid']}
    ents, extra, seen, copied = ([], [], set(), set())
    for rname, p, mine in foreign:
        pool = {o['uid']: o for o in p.objects if o['has_uid']}
        for o in mine:
            if o['uid'] in seen:
                continue
            seen.add(o['uid'])
            body = o['body']
            ren = {u: fresh() for u in embedded(body)}
            body = remap(body, ren)
            new_uids.extend(ren.values())
            uid = struct.unpack_from('<I', body, 2)[0]
            ents.append(dict(o, uid=uid, serial=uid, body=body, filter=aa, body_size=o['body_size'] + len(body) - len(o['body'])))
            if AZ.comp(o['body'], 'Visual') and AZ.comp(o['body'], 'GameplayZoneComponent'):
                vs = AZ.comp(o['body'], 'Visual')[0]
                mu = struct.unpack_from('<I', o['body'], vs + 13)[0]
                if mu in pool and mu not in have and (mu not in copied):
                    copied.add(mu)
                    extra.append(dict(pool[mu], filter=None))
            if rname.startswith('Cell'):
                continue
            todo = [o['body']]
            while todo:
                b = todo.pop()
                for u in {struct.unpack_from('<I', b, k)[0] for k in range(len(b) - 3)}:
                    q = pool.get(u)
                    if q is None or u in have or u in copied or (u == o['uid']) or is_entity(q):
                        continue
                    copied.add(u)
                    extra.append(dict(q, filter=None))
                    todo.append(q['body'])
    head = dict(blk.objects[0])
    b = head['body']
    n = struct.unpack_from('<I', b, 10)[0]
    end = 14 + 6 * n
    m = struct.unpack_from('<I', b, end)[0]
    b = b[:10] + struct.pack('<I', n + len(ents)) + b[14:end] + b''.join((b'\x01\x00' + struct.pack('<I', e['uid']) for e in ents)) + struct.pack('<I', m + len(ents)) + b[end + 4:]
    head['body_size'] = head['body_size'] + len(b) - len(head['body'])
    head['body'] = b
    rest = list(blk.objects[1:])
    last = max((i for i, o in enumerate(rest) if is_entity(o)))
    objs = [head] + rest[:last + 1] + ents + rest[last + 1:] + extra
    report.append(f"AA's pieces outside the CTF block copied in: {len(ents)} entities (fresh uids) from {', '.join(sorted({r for r, _p, _m in foreign}))}; {len(extra)} objects they need from mode blocks")
    return _Block(blk.rec, objs, table, blk.prefix)

def build_archive(cfg, src, dest, L, B, fresh, report):
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        recs = {r.name: r for r in f.records}
        block_rec = recs[CTF_BLOCK]
        blk = mapwalk.Payload(block_rec, f.read(block_rec))
        world_p, named, foreign = (None, {}, [])
        blk_uids = {o['uid'] for o in blk.objects if o['has_uid']}
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            if any((xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager' for o in p.objects)):
                world_p = p
            if r.name.startswith('DataBlock_'):
                for o in p.objects:
                    if is_entity(o):
                        named.setdefault(o['name'], o)
            if r.name != CTF_BLOCK and p.filter_table:
                fs = spawn_layers.filter_sets(p.filter_table)
                mine = [o for o in p.objects if is_entity(o) and o['filter'] is not None and (CTF_LAYER in fs[o['filter']]) and (o['uid'] not in blk_uids)]
                if mine:
                    foreign.append((r.name, p, mine))
        defined = set()
        for r in f.records:
            if r.name != 'GlobalMetaFile' and r.size:
                defined |= {o['uid'] for o in mapwalk.Payload(r, f.read(r)).objects if o['has_uid']}
    assert not blk.prefix and (not world_p.prefix), 'a record with a prefix'
    new_uids = [L, B]
    if foreign:
        blk = import_foreign(blk, foreign, fresh, new_uids, report)
    sets = spawn_layers.filter_sets(blk.filter_table)
    aa_only = {i for i, s in enumerate(sets) if s == {CTF_LAYER}}
    ents = {o['name']: o for o in blk.objects if is_entity(o)}
    flags = {}
    for o in blk.objects:
        if is_entity(o) and AZ.comp(o['body'], 'FlagComponent'):
            k = o['body'].find(struct.pack('<I', IDS['FlagComponent']))
            flags[o['body'][k + 21] + 1] = o
    if sorted(flags) != [1, 2]:
        raise SystemExit(f'flags by team: {sorted(flags)}')
    scoring = {t: next((o for o in blk.objects if is_entity(o) and AZ.comp(o['body'], 'CTFScoreZoneComponent') and (math.dist(AZ.pos(o)[:2], AZ.pos(flags[t])[:2]) < 1.0))) for t in (1, 2)}
    zone_ents = [o for o in blk.objects if is_entity(o) and AZ.comp(o['body'], 'GameplayZoneComponent')]
    stock_zones = [Z.parse(o['body'][slice(*AZ.comp(o['body'], 'GameplayZoneComponent'))]) for o in zone_ents]
    by_team_zone = {}
    for o, z in zip(zone_ents, stock_zones):
        t = next((t for t in (1, 2) if AZ.member(z['convex'], AZ.pos(flags[t]))))
        by_team_zone[t] = o
    starts = {t: sorted((o for o in blk.objects if is_entity(o) and spawn_layers.spawn_kind(o['body']) == (2, t)), key=lambda o: o['name']) for t in (1, 2)}

    def target_list(spec):
        if isinstance(spec, str) or all((isinstance(e, str) for e in spec)):
            return [named[n_] for n_ in start_names(spec)]
        if len(spec) != 4:
            raise SystemExit(f'team starts: need 4, got {len(spec)}')
        return [e if isinstance(e, dict) and 'at' in e else named[e] for e in spec]
    targets = {t: target_list(cfg['starts'][t]) for t in (1, 2)}
    if any((len(starts[t]) != 4 for t in (1, 2))):
        raise SystemExit('expected 4 team starts per team')
    area_pts = territory_outline(stock_zones)
    halves, path, how = split_halves(cfg, area_pts)
    report.append(f"territories' outer edge (stock, kept): {len(area_pts)} corners, {AZ.area(area_pts):.0f} m2; {how}")
    zc, h = (cfg['zc'], cfg['height'])
    tmesh_uid = {}
    mapping = {}
    replaced = {}
    for t in (1, 2):
        tent = by_team_zone[t]
        vs, _ve = AZ.comp(tent['body'], 'Visual')
        tmesh_uid[t] = struct.unpack_from('<I', tent['body'], vs + 13)[0]
    meshes = {o['uid']: o for o in blk.objects if o['has_uid'] and o['uid'] in tmesh_uid.values()}
    shapes_out = set()
    zones_built = {}
    for t in (1, 2):
        half = halves[t]
        tent = by_team_zone[t]
        convex, quads, pieces = zone_convex(half, zc, h)
        origin = (sum((p_[0] for p_ in half)) / len(half), sum((p_[1] for p_ in half)) / len(half), zc)
        tmesh = meshes[tmesh_uid[t]]
        mesh_uid = fresh()
        mbody, R, (lo, hi) = AZ.build_mesh(tmesh['body'], quads, origin, zc, h)
        mbody = mbody[:2] + struct.pack('<I', mesh_uid) + mbody[6:]
        mesh = dict(tmesh, uid=mesh_uid, serial=mesh_uid, body=mbody, body_size=tmesh['body_size'] + len(mbody) - len(tmesh['body']))
        ic = AZ.comp(tent['body'], 'InertComponent')
        if ic:
            shapes_out |= {u for u in (struct.unpack_from('<I', tent['body'], k)[0] for k in range(ic[0], ic[1] - 3)) if u in {o['uid'] for o in blk.objects if o['has_uid'] and xrefs.cls('AC3', o['type']) == 'MeshShape'}}
            body, removed = entity.strip(tent['body'], lambda n, el: n == 'InertComponent')
            if removed != ['InertComponent']:
                raise SystemExit(f"{tent['name']}: InertComponent not stripped")
        else:
            body = tent['body']
        rot = struct.unpack_from('<12f', body, 10)
        if max((abs(a - b) for a, b in zip(rot, (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)))) > 0.0001:
            body = body[:10] + struct.pack('<12f', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0) + body[58:]
        renew = {u: fresh() for u in embedded(body)}
        body = remap(body, renew)
        mapping.update(renew)
        body = body.replace(struct.pack('<I', tmesh['uid']), struct.pack('<I', mesh_uid))
        zs2, ze2 = AZ.comp(body, 'GameplayZoneComponent')
        z = Z.parse(body[zs2:ze2])
        z = dict(z, convex=convex, border=[])
        zel = Z.write(z)
        body = body[:zs2] + zel + body[zs2 + z['end']:]
        body = body[:58] + struct.pack('<3f', *origin) + body[70:]
        bv = body.find(struct.pack('<I', IDS['BoundingVolume']), 70)
        body = body[:bv + 4] + struct.pack('<6f', -R, -R, lo - zc, R, R, hi - zc) + body[bv + 28:]
        bad = Z.wall_problems(body, {mesh_uid: mesh})
        if bad:
            raise SystemExit(f"{tent['name']}: {bad[0]}")
        uid = struct.unpack_from('<I', body, 2)[0]
        replaced[id(tent)] = dict(tent, uid=uid, serial=uid, body=body, body_size=tent['body_size'] + len(body) - len(tent['body']))
        replaced[id(tmesh)] = mesh
        zones_built[t] = Z.parse(zel)
        new_uids += [mesh_uid] + list(renew.values())
        report.append(f"team {t} territory: {len(half)} corners, {AZ.area(half):.0f} m2, {len(pieces)} convex pieces, {sum((len(x) for x in convex))} panels ({len(quads)} on its outline, {sum((len(s['fx']) for x in convex for s in x))} FX points), wall mesh {4 * len(quads)} vertices, R {R} m; from {tent['name']} ({len(renew)} embedded uids renewed)")

    def renewed(o, edit=None):
        body = o['body']
        ren = {u: fresh() for u in embedded(body)}
        body = remap(body, ren)
        if edit:
            body = edit(body)
        mapping.update(ren)
        new_uids.extend(ren.values())
        uid = struct.unpack_from('<I', body, 2)[0]
        replaced[id(o)] = dict(o, uid=uid, serial=uid, body=body, body_size=o['body_size'] + len(body) - len(o['body']))

    def at(xyz):

        def edit(body):
            return body[:58] + struct.pack('<3f', *xyz) + body[70:]
        return edit
    for t in (1, 2):
        xyz = cfg['flags'][t]

        def flag_edit(body, xyz=xyz):
            body = at(xyz)(body)
            k = body.find(struct.pack('<I', IDS['FlagComponent']))
            if k < 0 or body[k + 4] != 1:
                raise SystemExit("FlagComponent's home not where expected")
            return body[:k + 5] + struct.pack('<3f', *xyz) + body[k + 17:]
        renewed(flags[t], flag_edit)
        renewed(scoring[t], at(xyz))
        for o, tgt in zip(starts[t], targets[t]):
            if 'at' in tgt:
                renewed(o, lambda body, e=tgt: body[:10] + aa_map.placement_bytes(body[10:74], e['at'], e['facing']) + body[74:])
            else:
                renewed(o, lambda body, tgt=tgt: body[:10] + tgt['body'][10:74] + body[74:])
    added, dropped_resp = ([], set())
    if 'respawns' in cfg:
        cur = {o['name']: o for o in blk.objects[1:] if is_entity(o) and (spawn_layers.spawn_kind(o['body']) or (9,))[0] == 0}
        spot = {n: (struct.unpack_from('<3f', o['body'], 58), aa_map.facing_of(struct.unpack_from('<16f', o['body'], 10))) for n, o in cur.items()}
        kept, edits = (set(), [])
        for e in cfg['respawns']:
            s = spot.get(e.get('from'))
            if s is not None and e['from'] not in kept and (math.dist(s[0], e['at']) < 0.01) and (abs((s[1] - e['facing'] + 180) % 360 - 180) < 0.1):
                kept.add(e['from'])
            else:
                edits.append(e)
        dropped_resp = {id(cur[n]) for n in set(cur) - kept}
        aa_filter = next((i for i in aa_only))
        for k, e in enumerate(edits, 1):
            tmpl = cur.get(e.get('from')) or cur[sorted(cur)[0]]
            body = tmpl['body']
            ren = {u: fresh() for u in embedded(body)}
            body = remap(body, ren)
            body = body[:10] + aa_map.placement_bytes(body[10:74], e['at'], e['facing']) + body[74:]
            new_uids.extend(ren.values())
            uid = struct.unpack_from('<I', body, 2)[0]
            added.append(dict(tmpl, uid=uid, serial=uid, name=f'AA_Respawn_{k:02d}', body=body, filter=aa_filter, body_size=tmpl['body_size'] + len(body) - len(tmpl['body'])))
        report.append(f"respawns: {len(kept)} kept, {len(dropped_resp)} out of the block (moved or removed), {len(added)} new on the new layer only (moved or added); {len(cfg['respawns'])} in the layout")
    moved = {id(o) for o in list(flags.values()) + list(scoring.values()) + starts[1] + starts[2]} | {id(o) for o in zone_ents}
    others_aa = [o for o in blk.objects[1:] if is_entity(o) and o['filter'] in aa_only and (id(o) not in moved) and (id(o) not in dropped_resp)]
    for o in others_aa:
        renewed(o)
    report.append(f"AA-only entities under fresh uids: flags + scoring zones + team starts moved, territories rebuilt, {len(others_aa)} other(s) kept in place ({', '.join((o['name'] for o in others_aa))})")
    dropped = set(tmesh_uid.values()) | shapes_out
    rest = [o for o in blk.objects[1:] if id(o) not in replaced and id(o) not in dropped_resp]
    stray = sorted({hex(u) for o in rest for u in list(mapping) + sorted(dropped) if struct.pack('<I', u) in o['body'] and o['uid'] not in dropped})
    if stray:
        raise SystemExit(f'other objects of the block name renewed / dropped uids: {stray}')
    head = blk.objects[0]
    hb = head['body']
    n = u32(hb, 10)
    end = 14 + 6 * n
    if len(hb) != end + 8:
        raise SystemExit('block header: not {uid, list, count, layer}')
    gone = {o['uid'] for o in blk.objects[1:] if id(o) in dropped_resp}
    listed = [u32(hb, k + 2) for k in range(14, end, 6) if u32(hb, k + 2) not in gone]
    listed = [mapping.get(u, u) for u in listed] + [o['uid'] for o in added]
    entries = b''.join((b'\x01\x00' + struct.pack('<I', u) for u in listed))
    m_count = u32(hb, end) - len(gone) + len(added)
    if listed == [mapping.get(u32(hb, k + 2), u32(hb, k + 2)) for k in range(14, end, 6)]:
        hb2 = hb[:2] + struct.pack('<I', B) + hb[6:14] + entries + hb[end:end + 4] + struct.pack('<I', L)
    else:
        hb2 = hb[:2] + struct.pack('<I', B) + hb[6:10] + struct.pack('<I', len(listed)) + entries + struct.pack('<I', m_count) + struct.pack('<I', L)
    objs = [(B, mapwalk.object_bytes(head, hb2), head['filter'])]
    last_ent = max((i for i, o in enumerate(blk.objects) if is_entity(o) and id(o) not in dropped_resp))
    for i, o in enumerate(blk.objects[1:], 1):
        if (o['has_uid'] and o['uid'] in dropped) and id(o) not in replaced or id(o) in dropped_resp:
            continue
        o2 = replaced.get(id(o), o)
        objs.append((o2['serial'], mapwalk.object_bytes(o2), o['filter']))
        if i == last_ent:
            objs += [(e['serial'], mapwalk.object_bytes(e), e['filter']) for e in added]
    table = blk.filter_table.replace(struct.pack('<I', CTF_LAYER), struct.pack('<I', L))
    block_payload = payload_bytes(objs, table)
    report.append(f"block DataBlock_{cfg['layer_name']} 0x{B:08X}: {len(objs)} objects ({len(blk.objects)} stock, {len(dropped)} dropped: the two old wall meshes and their Havok shapes), filter table: the stock layer swapped for 0x{L:08X} at {blk.filter_table.count(struct.pack('<I', CTF_LAYER))} places")
    wobjs = []
    for o in world_p.objects:
        b = o['body']
        if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager':
            cnt = u32(b, 10)
            if len(b) != 14 + 18 * cnt:
                raise SystemExit('WorldDataLayerManager: not count + 18-byte entries')
            layers = [u32(b, 14 + 18 * i + 9) for i in range(cnt)]
            if layers != sorted(layers):
                raise SystemExit("WorldDataLayerManager: the associations aren't in layer uid order")
            at = bisect.bisect_right(layers, L)
            entry = struct.pack('<II', 0, ASSOC) + b'\x00' + struct.pack('<I', L) + b'\x00' + struct.pack('<I', B)
            b = b[:10] + struct.pack('<I', cnt + 1) + b[14:14 + 18 * at] + entry + b[14 + 18 * at:]
            report.append(f'{world_p.rec.name}: WorldDataLayerManager {cnt} -> {cnt + 1} associations (0x{L:08X} -> 0x{B:08X}, at {at} in layer uid order)')
        wobjs.append((o['serial'], mapwalk.object_bytes(o, b), o['filter']))
    set_payload(image, world_p.rec.index, payload_bytes(wobjs, world_p.filter_table))
    used = {r.uid for r in recs.values()}
    file_uid = UID_BLOCK << 32 | B
    while file_uid in used:
        file_uid += 1
    add_file(image, f"DataBlock_{cfg['layer_name']}", block_payload, file_uid, template_index=block_rec.index, entry_key=B)
    if dest:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        write(dest, image)
    clash = sorted((hex(u) for u in new_uids if u in defined))
    if clash:
        raise SystemExit(f'new uids the archive already defines: {clash}')
    return dict(area=area_pts, path=path, block_rec=block_rec, world_rec=world_p.rec, new_uids=new_uids, ctf_refs=blk.filter_table.count(struct.pack('<I', CTF_LAYER)), halves=halves, oob=next((o for o in blk.objects if is_entity(o) and AZ.comp(o['body'], 'OutOfBoundsComponent'))), zones={t: zones_built[t] for t in (1, 2)}, block_objects=[replaced.get(id(o), o) for o in blk.objects[1:] if not (o['has_uid'] and o['uid'] in dropped)])

def verify_archive(cfg, src, dest, L, B, built, report):
    ok = True
    with Forge(src) as g, Forge(dest) as f:
        same = len(f.records) == len(g.records) + 1 and all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index != built['world_rec'].index))
        nb = f.records[-1]
        p = mapwalk.Payload(nb, f.read(nb))
        wp = mapwalk.Payload(f.records[built['world_rec'].index], f.read(f.records[built['world_rec'].index]))
        gw = mapwalk.Payload(g.records[built['world_rec'].index], g.read(g.records[built['world_rec'].index]))
        counts = {}
        for x in f.records:
            if x.name != 'GlobalMetaFile' and x.size:
                for o in mapwalk.Payload(x, f.read(x)).objects:
                    if o['has_uid']:
                        counts[o['uid']] = counts.get(o['uid'], 0) + 1
    ok &= same
    report.append(f'every other record byte-identical, one file more: {same}')
    wdiff = [o['name'] for o, q in zip(wp.objects, gw.objects) if o['body'] != q['body']]
    w_ok = wdiff == ['(null)_WorldDataLayerManager'] or (len(wdiff) == 1 and wdiff[0].endswith('WorldDataLayerManager'))
    wdlm = next((o for o in wp.objects if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager'))['body']
    assoc = [(u32(wdlm, 14 + 18 * i + 9), u32(wdlm, 14 + 18 * i + 14)) for i in range(u32(wdlm, 10))]
    w_ok &= assoc.count((L, B)) == 1 and [a for a, _b in assoc] == sorted((a for a, _b in assoc))
    ok &= w_ok
    report.append(f"World file: only the WorldDataLayerManager differs, one association new layer -> new block, the associations in layer uid order (the exe's binary search): {w_ok}")
    hb = p.objects[0]['body']
    n = u32(hb, 10)
    listed = [u32(hb, 14 + 6 * i + 2) for i in range(n)]
    ents = {o['uid']: o for o in p.objects if is_entity(o)}
    h_ok = nb.name == f"DataBlock_{cfg['layer_name']}" and u32(hb, 2) == B and (u32(hb, 14 + 6 * n + 4) == L) and (sorted(listed) == sorted(ents)) and (struct.pack('<I', CTF_LAYER) not in p.filter_table) and (p.filter_table.count(struct.pack('<I', L)) == built['ctf_refs'])
    ok &= h_ok
    report.append(f'new block: name, uid, layer, entity list = its {len(ents)} entities, filter table on the new layer only: {h_ok}')
    tops = [o['uid'] for o in p.objects if o['has_uid'] and o['uid'] in set(built['new_uids'])]
    once = all((counts.get(u, 0) == 1 for u in tops)) and L not in counts
    ok &= once
    report.append(f"every new object uid defined once in the archive ({len(tops)} objects, {len(built['new_uids'])} new uids with the embedded ones; the layer uid only referenced): {once}")
    zones = {}
    for o in p.objects:
        if is_entity(o) and AZ.comp(o['body'], 'GameplayZoneComponent'):
            s_, e_ = AZ.comp(o['body'], 'GameplayZoneComponent')
            zz = Z.parse(o['body'][s_:e_])
            closing = Z.closing_problems(o['body'])
            ok &= Z.write(zz) == o['body'][s_:s_ + zz['end']] and (not closing)
            report += [f"{o['name']}: {x}" for x in closing]
            zones[o['name']] = zz
    flags = {}
    for o in p.objects:
        if is_entity(o) and AZ.comp(o['body'], 'FlagComponent'):
            k = o['body'].find(struct.pack('<I', IDS['FlagComponent']))
            t = o['body'][k + 21] + 1
            home = struct.unpack_from('<3f', o['body'], k + 5)
            flags[t] = o
            good = max((abs(a - b) for a, b in zip(AZ.pos(o), cfg['flags'][t]))) < 0.0001 and max((abs(a - b) for a, b in zip(home, cfg['flags'][t]))) < 0.0001
            ok &= good
            report.append(f"{o['name']} (team {t}) at {cfg['flags'][t]}, home copy the same: {good}")
    zt = {}
    for t, fo in flags.items():
        ins = [n_ for n_, zz in zones.items() if AZ.member(zz['convex'], AZ.pos(fo))]
        zt[t] = ins[0] if len(ins) == 1 else None
    for o in p.objects:
        if is_entity(o) and AZ.comp(o['body'], 'CTFScoreZoneComponent'):
            t = next((t for t, fo in flags.items() if math.dist(AZ.pos(o), AZ.pos(fo)) < 0.001))
            report.append(f"{o['name']} on team {t}'s flag")
    if None in zt.values():
        report.append('a flag is not in exactly one territory')
        return False

    def placements(kind):
        out = []
        for o in p.objects:
            k = spawn_layers.spawn_kind(o['body']) if is_entity(o) else None
            if k and (k[0], k[1] if k[0] == 2 else 0) == kind:
                mm = struct.unpack_from('<16f', o['body'], 10)
                out.append((mm[12:15], aa_map.facing_of(mm)))
        return out

    def matches(want, got):
        got = list(got)
        for e in want:
            i = next((i for i, (at, f) in enumerate(got) if math.dist(at, e['at']) < 0.01 and abs((f - e['facing'] + 180) % 360 - 180) < 0.1), None)
            if i is None:
                return False
            got.pop(i)
        return not got
    explicit = {t: cfg['starts'][t] for t in (1, 2) if not isinstance(cfg['starts'][t], str) and all((isinstance(e, dict) and 'at' in e for e in cfg['starts'][t]))}
    for t, want in explicit.items():
        good = matches(want, placements((2, t)))
        ok &= good
        report.append(f"team {t}'s starts as laid out: {good}")
    if 'respawns' in cfg:
        good = matches(cfg['respawns'], placements((0, 0)))
        ok &= good
        report.append(f"respawns as laid out ({len(cfg['respawns'])}): {good}")
    starts, resp = ({1: [], 2: []}, [])
    for o in p.objects:
        k = spawn_layers.spawn_kind(o['body']) if is_entity(o) else None
        if k and k[0] == 2 and (k[1] in (1, 2)):
            starts[k[1]].append(AZ.pos(o))
        elif k and k[0] == 0:
            resp.append(AZ.pos(o))
    try:
        oob = AZ.ccw(AZ.oob_outline(built['oob']['body']))
    except SystemExit:
        oob = None
    if oob is None or abs(AZ.area(oob)) < 0.3 * abs(AZ.area(built['area'])):
        oob = AZ.ccw(built['area'])
    ok &= layout_checks(cfg, {t: zones[zt[t]] for t in (1, 2)}, starts, resp, built['area'], built['path'], oob, report, os.path.join(os.path.dirname(dest), 'aa_zones.png'))
    return ok

def build_datapc(cfg, base, L, fresh, step_name, report):
    boot = Bootstrap(base)
    by_uid = {u32(o['body'], 2): o for o in boot.objects if len(o['body']) >= 6 and o['body'][:2] == b'\x00\x01'}
    mpw, unl = (by_uid[cfg['mpworld']], by_uid[cfg['unlock']])
    used = {u32(o['body'], 14) for o in boot.objects if o['type'] == unl['type']}
    if cfg['unlock_index'] in used:
        raise SystemExit(f"unlock index {cfg['unlock_index']} already used")
    taken_bytes = boot.plain
    new_mpw, new_unl = (fresh(taken_bytes), fresh(taken_bytes))

    def strings(body):
        cls = struct.pack('<I', UISTRING)
        k, i = (body.find(cls), 0)
        while k >= 4:
            body = set_u32(body, k - 4, fresh(taken_bytes))
            if i == 0:
                body = set_u32(body, k + 4, cfg['name_id'])
            i += 1
            k = body.find(cls, k + 1)
        return (body, i)
    body = set_u32(mpw['body'], 2, new_mpw)
    body, ns = strings(body)
    assert ns == 2
    mpw2 = obj(mpw, f"MpWorld_{cfg['label']}", body)
    body = set_u32(unl['body'], 2, new_unl)
    body = set_u32(body, 14, cfg['unlock_index'])
    assert body.count(struct.pack('<I', cfg['mpworld'])) == 1
    body = body.replace(struct.pack('<I', cfg['mpworld']), struct.pack('<I', new_mpw))
    body, ns2 = strings(body)
    assert ns2 == 2
    unl2 = obj(unl, f"Map_{cfg['label']}", body)
    stock = by_uid[CTF_LAYER]
    sb = stock['body']
    name_end = 14 + u32(sb, 10) + 1
    if sb[name_end:] != bytes(10):
        raise SystemExit("ACFE_CTF_2 isn't the expected empty-list layer")
    lbody = sb[:2] + struct.pack('<I', L) + sb[6:name_end] + struct.pack('<I', 1) + b'\x00' + struct.pack('<I', new_mpw) + sb[name_end + 4:]
    layer = obj(stock, cfg['layer_name'], lbody)
    group = boot.one(name=GROUP)
    gb = group['body']
    gn = u32(gb, 10)
    if len(gb) != 14 + 6 * gn + 1:
        raise SystemExit(f'{GROUP}: not count + entries + one byte')
    gb2 = gb[:10] + struct.pack('<I', gn + 1) + gb[14:14 + 6 * gn] + b'\x01\x00' + struct.pack('<I', L) + gb[14 + 6 * gn:]
    maps = boot.one(name='Maps')
    plain = objects.rebuild(boot.plain, replacements={boot.position(maps): obj(maps, maps['name'], node_append(maps['body'], new_unl)), boot.position(group): obj(group, group['name'], gb2)}, additions=[mpw2, unl2, layer])
    dest = boot.emit(plain, os.path.join('modes', step_name))
    report.append(f"""DataPC: MpWorld_{cfg['label']} 0x{new_mpw:08X} ({new_mpw}: serve this in a playlist) on the stock World; Map_{cfg['label']} 0x{new_unl:08X}, unlock index {cfg['unlock_index']}, name 0x{cfg['name_id']:08X}, the stock description; DataLayer {cfg['layer_name']} 0x{L:08X} ("ctf_2", MpWorld list = the new entry) in {GROUP} ({gn} -> {gn + 1})""")
    plain2, objs2 = read_back_objects(dest)
    by2 = {}
    for o in objs2:
        if len(o['body']) >= 6 and o['body'][:2] == b'\x00\x01':
            by2.setdefault(u32(o['body'], 2), []).append(o)
    m2 = next((o for o in objs2 if o['name'] == 'Maps'))
    g2 = next((o for o in objs2 if o['name'] == GROUP))
    l2 = by2.get(L, [None])[0]
    ok = all((len(by2.get(u, [])) == 1 for u in (new_mpw, new_unl, L))) and new_unl in node_entries(m2['body']) and (struct.pack('<I', L) in g2['body']) and (l2 is not None) and (struct.pack('<I', new_mpw) in l2['body']) and (b'ctf_2\x00' in l2['body']) and (u32(by2[new_unl][0]['body'], 14) == cfg['unlock_index']) and (not objects.directory_mismatches(plain2))
    report.append(f"DataPC verify (each new object once, Maps + {GROUP} list them, the layer names the entry, unlock index, directories): {('OK' if ok else 'FAILED')}")
    return (dest, new_mpw, ok)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('variant', help=f'one of {sorted(VARIANTS)} or a modes/layouts/<name>.json')
    ap.add_argument('--name', required=True, help='build folder under out/modes/')
    ap.add_argument('--base', default=paths.DEFAULT_DATAPC, help="the DataPC.forge to add the entry to (default: the default set's, paths.DEFAULT_DATAPC)")
    ap.add_argument('--archive-only', action='store_true', help="build and check the map archive only (the Switched block), no DataPC entry: for layouts without a menu entry of their own (the Switched mode's DataPC side comes with the mode)")
    ap.add_argument('--map-from', help="with --archive-only: build on this map archive instead of the layout's own `source` (e.g. a balanced ACB build, so one archive holds both sets)")
    ap.add_argument('--preview', action='store_true', help='only the layout: the checks and the picture (out/modes/<name>/plan.png), nothing written but those')
    a = ap.parse_args(argv)
    cfg = load_variant(a.variant)
    if a.map_from:
        cfg = dict(cfg, source=a.map_from)
    out_dir = os.path.join(OUT, a.name)
    if a.archive_only and os.path.exists(out_dir):
        raise SystemExit(f'out/modes/{a.name} exists - pick another build name (builds are never overwritten)')
    os.makedirs(out_dir, exist_ok=True)
    report = [f'{a.variant} -> out/modes/{a.name}']
    taken = set(inventory.universe('AC3')) | set(inventory.universe('ACR'))
    nxt = [UID_BLOCK]

    def fresh(avoid=b''):
        while nxt[0] in taken or (avoid and struct.pack('<I', nxt[0]) in avoid):
            nxt[0] += 1
        if nxt[0] >> 16 != UID_BLOCK >> 16:
            raise SystemExit('uid block exhausted')
        taken.add(nxt[0])
        return nxt[0]
    src = aa_map.archive_source(cfg['archive'], cfg.get('source'))
    if a.preview:
        import topview
        m = aa_map.read(cfg['archive'], cfg.get('source'))
        starts, resp = aa_map.normalise(cfg, m)
        area = [tuple(q) for q in m['area']]
        halves, path, how = split_halves(cfg, area)
        report.append(f"{m['source']}: the territories' outer edge (stock, kept) {len(area)} corners, {AZ.area(area):.0f} m2; {how}")
        zones = {t: {'convex': zone_convex(halves[t], cfg['zc'], cfg['height'])[0]} for t in (1, 2)}
        tv_png, tv_meta = topview.render(cfg['archive'], cfg.get('source'))
        with open(tv_meta, encoding='utf-8') as fh:
            bg = json.load(fh)
        bg['png'] = tv_png
        ok = layout_checks(cfg, zones, {t: [list(e['at']) + [e['facing']] for e in starts[t]] for t in (1, 2)}, [list(e['at']) + [e['facing']] for e in resp], area, path, [tuple(q) for q in m['oob']], report, os.path.join(out_dir, 'plan.png'), bg=bg, ctf=cfg.get('ctf_spots'))
        report.append(f"preview: {('OK' if ok else 'FAILED')} - picture out/modes/{a.name}/plan.png")
        with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
            fh.write('\n'.join(report) + '\n')
        print('\n'.join(report))
        return 0 if ok else 1
    boot_plain = Bootstrap(a.base).plain
    if a.archive_only:
        from make_switched import SWITCHED_LAYER
        taken.add(SWITCHED_LAYER)
        L, B = (SWITCHED_LAYER, fresh(boot_plain))
    else:
        L, B = (fresh(boot_plain), fresh(boot_plain))
    dest = os.path.join(out_dir, cfg['archive'])
    built = build_archive(cfg, src, dest, L, B, lambda: fresh(boot_plain), report)
    ok = verify_archive(cfg, src, dest, L, B, built, report)
    if a.archive_only:
        report.append(f"archive only (layer 0x{L:08X}, block 0x{B:08X}): verify {('OK' if ok else 'FAILED')}")
        with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
            fh.write('\n'.join(report) + '\n')
        print('\n'.join(report))
        return 0 if ok else 1
    step = f'{a.name}_entry'
    dpc, mpw, ok2 = build_datapc(cfg, a.base, L, fresh, step, report)
    ok &= ok2
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'strings', 'add_strings.py'), '--base', dpc, '--out', out_dir, '--owner', 'modes', '--what', f"modes/aa_variant.py: the {cfg['label']} menu entry's name", '--add', f"0x{cfg['name_id']:08X}={cfg['name_text']}"], capture_output=True, text=True)
    report.append('add_strings: ' + (r.stdout.strip().splitlines() or ['(no output)'])[-1])
    ok &= r.returncode == 0 and os.path.exists(os.path.join(out_dir, 'DataPC.forge'))
    shutil.rmtree(os.path.dirname(dpc), ignore_errors=True)
    report.append(f"""server: "extra_maps": {{"8": [..., {mpw}]}} (MpWorld_{cfg['label']})""")
    report.append(f"verify: {('OK' if ok else 'FAILED')}")
    report.append('picture: aa_zones.png (team 1 red, team 2 blue, flags yellow, team starts white, respawns grey)')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print('\n'.join(report))
    return 0 if ok else 1
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
