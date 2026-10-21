"""forgelib/modes/dom_apply.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import bisect
import json
import math
import os
import struct
import sys
import zlib
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'maps', 'port'), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import aa_map
import aa_variant as V
import aa_zones as AZ
import dom_map as D
import entity
import inventory
import mapwalk
import oob_modes
import spawn_layers
import xrefs
import zones as Z
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from mode_spawns import KIT_CLASSES
OUT = os.path.join(ROOT, 'out', 'modes')
BLOCK, DOM = (D.BLOCK, D.DOM_LAYER)
TEMPLATE = 'DataPC_AC3MP_Plantation.forge'
ASSOC = V.ASSOC
IDENTITY = struct.pack('<12f', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)
UID_BASE, UID_SPAN = (1533116416, 1024)
UNMAPPED = (4026531840, 4160749568)
SLOTS = ('DataPC_ACFE_Souk.forge', 'DataPC_ACFE_Constantinople.forge', 'DataPC_ACFE_Antioch.forge', 'DataPC_ACFE_Jerusalem.forge', 'DataPC_ACFE_Dyers.forge', 'DataPC_ACFE_Imperial.forge', 'DataPC_ACFE_Juderia.forge', 'DataPC_ACFE_Rhodes.forge', 'DataPC_AC2MP_Forli.forge', 'DataPC_AC2MP_Alhambra.forge', 'DataPC_AC2MP_Villa.forge', 'DataPC_AC2MP_Pienza.forge', 'DataPC_AC2MP_Siena.forge', 'DataPC_AC2MP_Palazzio.forge', 'DataPC_AC2MP_Firenze.forge', 'DataPC_AC2MP_MtStMichel.forge', 'DataPC_ACR_Rome_Multi.forge', 'DataPC_AC2MP_SanDonato.forge', 'DataPC_AC2MP_SanMarco.forge')
COLORS = {'A': (235, 70, 70), 'B': (240, 200, 60), 'C': (70, 120, 245)}

def base_taken(lo=UID_BASE, hi=UID_BASE + 32768):
    import paths
    from datafile import decompress
    out = set()
    pat = struct.pack('<H', lo >> 16)
    classes = {struct.pack('<I', c) for c in AZ.CLASS_IDS}
    for arch in (paths.DEFAULT_DATAPC, paths.DEFAULT_EXTRA):
        st = os.stat(arch)
        key = os.path.join(aa_map.CACHE, f'domuids.{os.path.basename(arch)}.{st.st_size}.{int(st.st_mtime)}.{lo:x}.{hi:x}.json')
        if os.path.exists(key):
            with open(key, encoding='utf-8') as fh:
                out |= set(json.load(fh))
            continue
        found = set()
        with Forge(arch) as f:
            for r in f.records:
                if not r.size:
                    continue
                try:
                    d = decompress(mapwalk.split_prefix(f.read(r))[1])
                except Exception:
                    continue
                k = d.find(pat, 2)
                while k >= 0:
                    u = struct.unpack_from('<I', d, k - 2)[0]
                    if lo <= u < hi and d[k + 2:k + 6] in classes:
                        found.add(u)
                    k = d.find(pat, k + 1)
        os.makedirs(aa_map.CACHE, exist_ok=True)
        with open(key, 'w', encoding='utf-8') as fh:
            json.dump(sorted(found), fh)
        out |= found
    return out

def slot_base(archive):
    i = SLOTS.index(archive) if archive in SLOTS else 19 + zlib.crc32(archive.encode()) % 13
    return UID_BASE + i * UID_SPAN

def is_entity(o):
    return xrefs.cls('AC3', o['type']) == 'Entity'

def renew(body, fresh, new_uids):
    ren = {u: fresh() for u in V.embedded(body)}
    new_uids += list(ren.values())
    return (V.remap(body, ren), ren)

def obj(o, body, name=None, uid=None):
    uid = struct.unpack_from('<I', body, 2)[0] if uid is None and body[:2] == b'\x00\x01' else uid
    out = dict(o, body=body, body_size=o['body_size'] + len(body) - len(o['body']), name=name or o['name'])
    if uid is not None:
        out.update(uid=uid, serial=uid)
    return out

def placed(body, at, facing):
    return body[:10] + aa_map.placement_bytes(body[10:74], at, facing) + body[74:]

def picture(png, cfg, m, regions, lines, member=None, size=1000, bg=None):
    from PIL import Image, ImageDraw
    area = [tuple(q) for q in D.play_area(cfg, m)]
    xs, ys = ([q[0] for q in area], [q[1] for q in area])
    pad = 12
    x0, x1, y0, y1 = (min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad)
    span = max(x1 - x0, y1 - y0)
    margin = 40
    sc = (size - 2 * margin) / span
    P = lambda x, y: (margin + (x - x0) * sc, margin + (y0 + span - y) * sc)
    img = Image.new('RGB', (size, size + 30), (24, 24, 24))
    if bg:
        tv = Image.open(bg['png'])
        k = bg['px_per_m']
        crop = tv.crop((int((x0 - bg['x0']) * k), int((bg['y1'] - (y0 + span)) * k), int((x0 + span - bg['x0']) * k), int((bg['y1'] - y0) * k)))
        img.paste(crop.resize((size - 2 * margin, size - 2 * margin)), (margin, margin))
    tint = Image.new('RGBA', img.size, (0, 0, 0, 0))
    px = tint.load()
    for Y in range(margin, size - margin, 2):
        for X in range(margin, size - margin, 2):
            q = ((X - margin) / sc + x0, y0 + span - (Y - margin) / sc)
            L = next((L for L in D.LETTERS if member(L, q)), None) if member else next((L for L in D.LETTERS if L in regions and AZ.inside(regions[L], q)), None)
            if L:
                c = COLORS[L] + (70 if bg else 140,)
                for dx in (0, 1):
                    for dy in (0, 1):
                        px[X + dx, Y + dy] = c
    img = Image.alpha_composite(img.convert('RGBA'), tint).convert('RGB')
    d = ImageDraw.Draw(img, 'RGBA')
    for gx in range(int(math.floor(x0 / 10) * 10), int(x0 + span) + 1, 10):
        d.line([P(gx, y0), P(gx, y0 + span)], fill=(255, 255, 255, 35 if gx % 50 else 80))
        if gx % 20 == 0:
            d.text((P(gx, y0)[0] - 8, 8), str(gx), fill=(200, 200, 200))
    for gy in range(int(math.floor(y0 / 10) * 10), int(y0 + span) + 1, 10):
        d.line([P(x0, gy), P(x0 + span, gy)], fill=(255, 255, 255, 35 if gy % 50 else 80))
        if gy % 20 == 0:
            d.text((6, P(x0, gy)[1] - 6), str(gy), fill=(200, 200, 200))
    d.line([P(*q) for q in area + area[:1]], fill=(255, 255, 255, 220), width=1)
    for ln in lines:
        d.line([P(*q[:2]) for q in ln], fill=(255, 220, 0, 255), width=3)
    for r in cfg.get('respawns', []):
        c = P(*r['at'][:2])
        L = next((L for L in D.LETTERS if L in regions and AZ.inside(regions[L], r['at'][:2])), None)
        d.ellipse([c[0] - 3, c[1] - 3, c[0] + 3, c[1] + 3], fill=COLORS[L] if L else (200, 200, 200), outline=(20, 20, 20))
    for t, col in ((1, (255, 110, 110)), (2, (120, 160, 255))):
        for s in cfg['starts'].get(str(t), []):
            c = P(*s['at'][:2])
            f = math.radians(s['facing'])
            d.line([c, (c[0] + math.cos(f) * 11, c[1] - math.sin(f) * 11)], fill=col, width=2)
            d.rectangle([c[0] - 4, c[1] - 4, c[0] + 4, c[1] + 4], fill=(255, 255, 255), outline=col, width=2)
    for L, at in cfg['points'].items():
        c = P(*at[:2])
        r8 = D.CONTROL_DISTANCE * sc
        d.ellipse([c[0] - r8, c[1] - r8, c[0] + r8, c[1] + r8], outline=COLORS[L] + (255,), width=2)
        d.ellipse([c[0] - 9, c[1] - 9, c[0] + 9, c[1] + 9], fill=COLORS[L], outline=(20, 20, 20), width=2)
        d.text((c[0] - 3, c[1] - 6), L, fill=(10, 10, 10))
        d.text((c[0] + 12, c[1] - 6), f'{L} ({at[0]:g}, {at[1]:g}, {at[2]:g})', fill=(240, 240, 240))
    d.text((margin, size + 6), f"{cfg.get('title', '')}   (north = +y up, 10 m grid; circles = the 8 m capture distance)", fill=(230, 230, 230))
    img.save(png)

def template_block():
    with Forge(mapwalk.archive_path('AC3', TEMPLATE)) as f:
        r = next((r for r in f.records if r.name == BLOCK))
        return mapwalk.Payload(r, f.read(r))

def build(cfg, m, regions, dest, report):
    src = m['source']
    files = oob_modes.load(src)
    blk = files.get(BLOCK)
    if blk is None:
        raise SystemExit(f"no {BLOCK} in {cfg['archive']} (the ported maps all carry archive_fixes' placeholder)")
    hb = blk.objects[0]['body']
    if len(blk.objects) != 1 or len(hb) != 22 or struct.unpack_from('<II', hb, 10) != (0, 0):
        raise SystemExit(f'{BLOCK} is not the empty placeholder ({len(blk.objects)} objects): this map has a Domination')
    world = [p for p in files.values() if any((xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager' for o in p.objects))]
    if len(world) != 1:
        raise SystemExit(f'{len(world)} records with a WorldDataLayerManager')
    world = world[0]
    defined = {}
    for rname, p in files.items():
        for o in p.objects:
            if o['has_uid']:
                defined.setdefault(o['uid'], []).append((rname, o))
    taken = set(inventory.universe('AC3')) | set(inventory.universe('ACR')) | set(defined) | base_taken()
    base = slot_base(cfg['archive'])
    nxt = [base]
    new_uids = []

    def fresh():
        while nxt[0] in taken:
            nxt[0] += 1
        if nxt[0] >= base + UID_SPAN:
            raise SystemExit("the map's uid slot is used up")
        taken.add(nxt[0])
        return nxt[0]
    tpl = template_block()
    tobj = {o['name']: o for o in tpl.objects[1:]}
    ents, deps, dep_keys = ([], [], set())

    def dep(o, check=True):
        key = o['uid'] if o['has_uid'] else (o['name'], len(o['body']))
        if key in dep_keys:
            return
        if o['has_uid'] and check:
            for rname, q in defined.get(o['uid'], []):
                if q['body'] != o['body'] and xrefs.cls('AC3', q['type']) == xrefs.cls('AC3', o['type']):
                    raise SystemExit(f"{o['name'] or hex(o['uid'])} {o['uid']:#x}: {rname} defines it with other bytes")
        dep_keys.add(key)
        deps.append(o)
    zc, hgt = (float(cfg.get('zc', m['zc'])), float(cfg.get('height', m['height'])))
    mat = tobj['AC3MP_MAT_DominationBorder']
    dep(mat)
    for i, L in enumerate(D.LETTERS):
        tz = tobj[f'PROTO_AC3MP_OOB_Domination_0{i + 1}_001']
        b, removed = entity.strip(tz['body'], lambda n, el: n == 'InertComponent')
        if removed != ['InertComponent']:
            raise SystemExit(f"{tz['name']}: InertComponent not stripped")
        b = b[:10] + IDENTITY + b[58:]
        zs, ze = AZ.comp(b, 'GameplayZoneComponent')
        z = Z.parse(b[zs:ze])
        set_uid = struct.unpack_from('<I', z['settings'], 2)[0]
        vs, _ve = AZ.comp(b, 'Visual')
        mesh_uid = struct.unpack_from('<I', b, vs + 13)[0]
        new_set, new_mesh = (fresh(), fresh())
        new_uids += [new_set, new_mesh]
        for old_u, new_u in ((set_uid, new_set), (mesh_uid, new_mesh)):
            if not b.count(struct.pack('<I', old_u)):
                raise SystemExit(f"{tz['name']}: {old_u:#x} named {b.count(struct.pack('<I', old_u))} times")
            b = b.replace(struct.pack('<I', old_u), struct.pack('<I', new_u))
        b, ren = renew(b, fresh, new_uids)
        sobj = tobj_uid(tpl, set_uid)
        dep(obj(sobj, sobj['body'][:2] + struct.pack('<I', new_set) + sobj['body'][6:], uid=new_set))
        region = regions[L]
        convex, quads, pieces = V.zone_convex(region, zc, hgt)
        origin = (sum((q[0] for q in region)) / len(region), sum((q[1] for q in region)) / len(region), zc)
        tmesh = tobj_uid(tpl, mesh_uid)
        mbody, R, (lo, hi) = AZ.build_mesh(tmesh['body'], quads, origin, zc, hgt)
        mbody = mbody[:2] + struct.pack('<I', new_mesh) + mbody[6:]
        mesh = obj(tmesh, mbody, name=f'Domination_Zone_{L} GameplayZone Mesh', uid=new_mesh)
        dep(mesh)
        zs, ze = AZ.comp(b, 'GameplayZoneComponent')
        zz = dict(Z.parse(b[zs:ze]), convex=convex, border=[])
        b = b[:zs] + Z.write(zz) + b[zs + zz['end']:]
        b = b[:58] + struct.pack('<3f', *origin) + b[70:]
        bv = b.find(struct.pack('<I', V.IDS['BoundingVolume']), 70)
        b = b[:bv + 4] + struct.pack('<6f', -R, -R, lo - zc, R, R, hi - zc) + b[bv + 28:]
        bad = Z.wall_problems(b, {new_mesh: mesh}) + Z.closing_problems(b)
        if bad:
            raise SystemExit(f'zone {L}: {bad[0]}')
        ents.append((obj(tz, b, name=f'Domination_Zone_{L}'), {DOM}))
        report.append(f"zone {L}: {len(region)} corners, {AZ.area(region):.0f} m2, {len(pieces)} convex pieces, {sum((len(c) for c in convex))} panels, wall mesh {4 * len(quads)} vertices (R {R} m), centre ({origin[0]:.1f}, {origin[1]:.1f}, {zc:g}), {hgt:g} m tall, no rotation, no InertComponent; from {tz['name']} ({len(ren)} embedded uids renewed)")
    for i, L in enumerate(D.LETTERS):
        tp = tobj[f'Domination_Point_0{i + 1}']
        k = AZ.comp(tp['body'], 'ControlPointComponent')[0]
        if struct.unpack_from('<I', tp['body'], k + 15)[0] != i + 1:
            raise SystemExit(f"{tp['name']}: point id not at component +15")
        b = tp['body'][:k + 15] + struct.pack('<I', i + 1) + tp['body'][k + 19:]
        b, _ren = renew(b, fresh, new_uids)
        b = b[:10] + IDENTITY + struct.pack('<3f', *cfg['points'][L]) + b[70:]
        if not all((struct.pack('<I', u) in b for u in (2501227407, 2907915792))):
            raise SystemExit('a control point lost its CoreFX references')
        ents.append((obj(tp, b, name=f'Domination_Point_{L}'), {DOM}))
    report.append('points: ' + ', '.join((f"{L} (id {i + 1}) {cfg['points'][L]}" for i, L in enumerate(D.LETTERS))))
    for t in (1, 2):
        for k, e in enumerate(cfg['starts'][str(t)], 1):
            ts = tobj[f'Domination_Team{t}_Spawn_00{k}']
            b, _ren = renew(ts['body'], fresh, new_uids)
            if spawn_layers.spawn_kind(b) != (2, t):
                raise SystemExit(f"{ts['name']}: not a team {t} start")
            ents.append((obj(ts, placed(b, e['at'], e['facing'])), {DOM}))
    kit = [o for o in tpl.objects[1:] if xrefs.cls('AC3', o['type']) in KIT_CLASSES]
    for o in kit:
        dep(o)
    report.append(f"team starts: 4 + 4 from Plantation's Domination_Team1/2_Spawn (Scene each) at the layout's placements; intro camera kit {len(kit)} objects (same uids and bytes as every block's)")
    where = {}
    for rname, p in files.items():
        for o in p.objects:
            if is_entity(o) and o['has_uid'] and ((spawn_layers.spawn_kind(o['body']) or (9,))[0] == 0):
                where.setdefault(o['name'], []).append((rname, o))
    cur = {s['name']: s for s in m['respawns']}
    kept, edits = ([], [])
    for e in cfg['respawns']:
        s = cur.get(e.get('from'))
        if s is not None and e['from'] not in kept and (math.dist(s['at'], e['at']) < 0.01) and (abs((s['facing'] - e['facing'] + 180) % 360 - 180) < 0.1):
            kept.append(e['from'])
        else:
            edits.append(e)
    cell_ext = {}
    n_copy = 0
    for name in kept:
        hits = [(r, o) for r, o in where[name] if math.dist(AZ.pos(o), cur[name]['at']) < 0.01]
        cells = [(r, o) for r, o in hits if not r.startswith('DataBlock_')]
        if cells:
            for r, o in cells:
                cell_ext.setdefault(r, set()).add(o['uid'])
        else:
            r, o = hits[0]
            p = files[r]
            layers = set(spawn_layers.filter_sets(p.filter_table)[o['filter']]) if o['filter'] is not None else set()
            ents.append((o, layers | {DOM}))
            n_copy += 1
    for k, e in enumerate(edits, 1):
        tn = e.get('from') if e.get('from') in where else sorted(cur)[0]
        _r, o = where[tn][0]
        b, _ren = renew(o['body'], fresh, new_uids)
        ents.append((obj(o, placed(b, e['at'], e['facing']), name=f'Dom_Respawn_{k:02d}'), {DOM}))
    report.append(f"respawns: {len(cfg['respawns'])} in the layout: {len(kept)} kept where the map has them ({sum((len(v) for v in cell_ext.values()))} in cells: their filter + Domination; {n_copy} copied from a mode block), {len(edits)} moved / added (new, Domination only); {len(cur) - len(kept)} of the map's AA respawns left off the layer")
    _blocks, areas = oob_modes.scan(files)
    pick = [a for a in areas if 'ACFE_Domination' in a['modes'] and a['where'] in ('block', 'cell')]
    why = 'names ACFE_Domination'
    if not pick:
        pick = [a for a in areas if 'ACFE_CTF_2' in a['modes'] and a['where'] in ('block', 'cell')]
        why = "Artifact Assault's (no area names Domination)"
    if pick:
        by_uid = {}
        for a in pick:
            by_uid.setdefault(a['uid'], []).append(a)
        root = oob_modes.root_cell(files)
        for uid, copies in sorted(by_uid.items()):
            a = min(copies, key=lambda a: (a['where'] != 'block', -len(a['modes']), a['rec']))
            o = next((q for q in files[a['rec']].objects if q['has_uid'] and q['uid'] == uid))
            need = oob_modes.closure(files, a['rec'], uid, BLOCK, root)
            for q in need:
                if q['uid'] != uid:
                    dep(q, check=False)
            ents.append((o, set(a['layers']) | {DOM}))
            report.append(f"out of bounds: {a['name']} {uid:#x} ({why}) from {a['rec']}, with {len(need) - 1} object(s) it needs; filter + Domination")
    else:
        report.append('out of bounds: the map has no area to copy (dom_check warns)')
    table = blk.filter_table or b''
    fidx = []
    for _o, layers in ents:
        table, i = oob_modes.filter_index(table, sorted(layers))
        fidx.append(i)
    uids = [o['uid'] for o, _l in ents]
    if len(set(uids)) != len(uids):
        raise SystemExit('an entity twice in the block')
    head = blk.objects[0]
    hb2 = hb[:10] + struct.pack('<I', len(uids)) + b''.join((b'\x01\x00' + struct.pack('<I', u) for u in uids)) + struct.pack('<II', len(uids), DOM)
    objs = [(head['serial'], mapwalk.object_bytes(head, hb2), head['filter'])]
    objs += [(o['serial'], mapwalk.object_bytes(o), fi) for (o, _l), fi in zip(ents, fidx)]
    objs += [(o['serial'], mapwalk.object_bytes(o), None) for o in deps]
    payloads = {BLOCK: V.payload_bytes(objs, table)}
    report.append(f"{BLOCK} {head['uid']:#x}: {len(ents)} entities listed (N = M), layer 0x{DOM:08X} (was 0x{struct.unpack_from('<I', hb, 18)[0]:08X}), {len(deps)} objects they need, {len(spawn_layers.filter_sets(table))} filters")
    wobjs = []
    for o in world.objects:
        b = o['body']
        if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager':
            cnt = struct.unpack_from('<I', b, 10)[0]
            if len(b) != 14 + 18 * cnt:
                raise SystemExit('WorldDataLayerManager: not count + 18-byte entries')
            layers = [struct.unpack_from('<I', b, 14 + 18 * i + 9)[0] for i in range(cnt)]
            if DOM in layers:
                raise SystemExit('the World already lists ACFE_Domination')
            if layers != sorted(layers):
                raise SystemExit("WorldDataLayerManager: the associations aren't in layer uid order")
            at = bisect.bisect_right(layers, DOM)
            entry = struct.pack('<II', 0, ASSOC) + b'\x00' + struct.pack('<I', DOM) + b'\x00' + struct.pack('<I', head['uid'])
            b = b[:10] + struct.pack('<I', cnt + 1) + b[14:14 + 18 * at] + entry + b[14 + 18 * at:]
            report.append(f"{world.rec.name}: WorldDataLayerManager {cnt} -> {cnt + 1} associations (ACFE_Domination 0x{DOM:08X} -> {BLOCK} {head['uid']:#x}, at {at} in layer uid order)")
        wobjs.append((o['serial'], mapwalk.object_bytes(o, b), o['filter']))
    payloads[world.rec.name] = V.payload_bytes(wobjs, world.filter_table)
    for rname, us in sorted(cell_ext.items()):
        p = files[rname]
        table, sets = (p.filter_table, spawn_layers.filter_sets(p.filter_table))
        newf = {}
        for o in p.objects:
            if o['has_uid'] and o['uid'] in us:
                table, newf[o['uid']] = oob_modes.filter_index(table, sorted(set(sets[o['filter']]) | {DOM}))
        cobjs = [(o['serial'], mapwalk.object_bytes(o), newf.get(o['uid'], o['filter']) if o['has_uid'] else o['filter']) for o in p.objects]
        payloads[rname] = V.payload_bytes(cobjs, table)
        report.append(f"{rname}: {len(us)} respawns' filter + Domination ({len(set(newf.values()))} filter(s) added or reused; bodies unchanged)")
    image = ArchiveImage.read(src)
    index = {p.rec.name: p.rec.index for p in files.values()}
    for rname, pl in payloads.items():
        set_payload(image, index[rname], pl)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    write(dest, image)
    return dict(changed=set(payloads), cells=cell_ext, new_uids=new_uids, block_uid=head['uid'], world=world.rec.name, defined=set(defined))

def tobj_uid(p, uid):
    return next((o for o in p.objects if o['has_uid'] and o['uid'] == uid))

def verify(cfg, m, regions, src, dest, built, report):
    import aa_records
    import dom_check
    ok = True
    r = dom_check.check(dest)
    good = r['verdict'] == 'OK'
    ok &= good
    report.append(f"dom_check: {r['verdict']} points={r.get('points')} zones={r.get('zones')} starts={r.get('starts')} respawns={r.get('respawns')} oob={r.get('oob')}" + ''.join((f'; {x}' for x in r['problems'] + r['warnings'])))
    files = oob_modes.load(dest)
    with Forge(src) as g, Forge(dest) as f:
        same = len(f.records) == len(g.records) and all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.name not in built['changed']))
        before = {x.name: mapwalk.Payload(x, g.read(x)) for x in g.records if x.name in built['changed'] and x.name != BLOCK}
    ok &= same
    report.append(f"every record but {', '.join(sorted(built['changed']))} byte-identical: {same}")
    wa, wb = (files[built['world']], before[built['world']])
    wdiff = [o['name'] for o, q in zip(wa.objects, wb.objects) if o['body'] != q['body']]
    w_ok = len(wa.objects) == len(wb.objects) and len(wdiff) == 1 and wdiff[0].endswith('WorldDataLayerManager') and all((o['filter'] == q['filter'] for o, q in zip(wa.objects, wb.objects)))
    wdlm = next((o for o in wa.objects if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager'))['body']
    assoc = [(struct.unpack_from('<I', wdlm, 14 + 18 * i + 9)[0], struct.unpack_from('<I', wdlm, 14 + 18 * i + 14)[0]) for i in range(struct.unpack_from('<I', wdlm, 10)[0])]
    w_ok &= assoc.count((DOM, built['block_uid'])) == 1 and [a for a, _b in assoc] == sorted((a for a, _b in assoc))
    ok &= w_ok
    report.append(f"World file: only the WorldDataLayerManager differs, one association ACFE_Domination -> the block, the associations in layer uid order (the exe's binary search): {w_ok}")
    for rname, us in built['cells'].items():
        a_, b_ = (files[rname], before[rname])
        sa = spawn_layers.filter_sets(a_.filter_table)
        sb = spawn_layers.filter_sets(b_.filter_table)
        c_ok = len(a_.objects) == len(b_.objects) and all((o['body'] == q['body'] and o['name'] == q['name'] and (sa[o['filter']] == sb[q['filter']] | {DOM} if q['has_uid'] and q['uid'] in us else o['filter'] == q['filter']) for o, q in zip(a_.objects, b_.objects)))
        ok &= c_ok
        report.append(f'{rname}: bodies unchanged, only the {len(us)} respawns gained ACFE_Domination: {c_ok}')
    p = files[BLOCK]
    zones, pts, starts = ({}, {}, {1: [], 2: []})
    for o in p.objects:
        if not is_entity(o):
            continue
        b = o['body']
        if AZ.comp(b, 'GameplayZoneComponent'):
            zones[o['name']] = Z.parse(b[slice(*AZ.comp(b, 'GameplayZoneComponent'))])
        elif AZ.comp(b, 'ControlPointComponent'):
            k = AZ.comp(b, 'ControlPointComponent')[0]
            pts[struct.unpack_from('<I', b, k + 15)[0]] = (o['name'], AZ.pos(o))
        else:
            kd = spawn_layers.spawn_kind(b)
            if kd and kd[0] == 2:
                mm = struct.unpack_from('<16f', b, 10)
                starts[kd[1]].append((mm[12:15], aa_map.facing_of(mm)))
    zone_of = {}
    for i, L in enumerate(D.LETTERS):
        name, at = pts.get(i + 1, (None, None))
        good = at is not None and max((abs(a - b) for a, b in zip(at, cfg['points'][L]))) < 0.01
        ins = [n for n, z in zones.items() if AZ.member(z['convex'], at)] if at else []
        good &= ins == [f'Domination_Zone_{L}']
        zone_of[L] = zones.get(f'Domination_Zone_{L}')
        ok &= good
        report.append(f"point {L}: id {i + 1}, at {cfg['points'][L]}, in exactly one zone, its own ({ins}): {good}")

    def matches(want, got):
        got = list(got)
        for e in want:
            i = next((i for i, (at, fc) in enumerate(got) if math.dist(at, e['at']) < 0.01 and abs((fc - e['facing'] + 180) % 360 - 180) < 0.1), None)
            if i is None:
                return False
            got.pop(i)
        return not got
    mem = lambda L, q: zone_of[L] is not None and AZ.member(zone_of[L]['convex'], q)
    for t, L in ((1, 'A'), (2, 'C')):
        good = matches(cfg['starts'][str(t)], starts[t]) and all((mem(L, at) for at, _f in starts[t]))
        ok &= good
        report.append(f"team {t}'s 4 starts as laid out, each in zone {L}: {good}")
    d2 = D.read_domination(dest)
    got = [(s['at'], s['facing']) for s in d2['respawns']]
    good = matches(cfg['respawns'], got)
    per = {L: sum((mem(L, s['at']) for s in d2['respawns'])) for L in D.LETTERS}
    good &= all(per.values())
    ok &= good
    report.append(f"respawns on the Domination layer = the layout's ({len(got)}), per zone {per}: {good}")
    area = D.play_area(cfg, m)
    xs, ys = ([q[0] for q in area], [q[1] for q in area])
    grid = [(x, y, 0.0) for x in range(int(min(xs)), int(max(xs)) + 1, 2) for y in range(int(min(ys)), int(max(ys)) + 1, 2) if AZ.inside(area, (x, y))]
    cnt = [sum((mem(L, q) for L in D.LETTERS)) for q in grid]
    edges = [(a, b) for ln in D.split3(area, cfg['dividers'])[1] for a, b in zip(ln, ln[1:])]
    edges += list(zip(area, area[1:] + area[:1]))
    wrong = sum((1 for q, c in zip(grid, cnt) if c != 1 and min((AZ.seg_dist(a, b, q) for a, b in edges)) >= 0.01))
    ok &= wrong == 0
    report.append(f'play area sampled every 2 m: {len(grid)} points, in exactly one zone {cnt.count(1)}, on a divider or the edge {sum((1 for c in cnt if c != 1)) - wrong}, wrong {wrong}')
    zn, zbad = Z.check_archive(dest)
    ok &= not zbad
    report.append(f'zones.py check: {zn} zone entities, problems {len(zbad)}' + ''.join((f'; {n}: {x}' for _f, n, x in zbad)))
    rec_ok, rec_problems, _notes, one = aa_records.judge(aa_records.pieces(files), files)
    ok &= rec_ok
    report.append(f"aa_records (Artifact Assault untouched): {('PASS' if rec_ok else 'FAIL ' + '; '.join(rec_problems))}")
    oob_ok, oob_problems, rows = oob_modes.judge(files)
    ok &= oob_ok
    report.append(f"oob_modes: {('PASS' if oob_ok else 'FAIL ' + '; '.join(oob_problems))} | " + ' | '.join(rows))
    counts = {}
    for q in files.values():
        for o in q.objects:
            if o['has_uid']:
                counts[o['uid']] = counts.get(o['uid'], 0) + 1
    tops = [u for u in built['new_uids'] if u in counts]
    once = all((counts[u] == 1 for u in tops)) and (not any((u in built['defined'] for u in built['new_uids'])))
    unm = [hex(u) for u in built['new_uids'] if UNMAPPED[0] <= u < UNMAPPED[1]]
    blk_uids = [o['uid'] for o in p.objects if o['has_uid']]
    ok &= once and (not unm)
    report.append(f"new uids: {len(built['new_uids'])} ({len(tops)} objects, the rest embedded), {min(built['new_uids']):#x}-{max(built['new_uids']):#x}, none the source defines, each object one body: {once}; in 0xF0000000-0xF7FFFFFF: {unm or 'none'}; block objects {len(blk_uids)}")
    return ok

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('layout', help='modes/layouts/<layout>.json with `use: domination`')
    ap.add_argument('--name', required=True, help='build folder under out/modes/ (refused if it exists)')
    ap.add_argument('--base', help="build on this archive instead of the layout's `source` / the release's copy")
    ap.add_argument('--preview', action='store_true', help='only the checks and the picture (out/modes/<name>/plan.png)')
    a = ap.parse_args(argv)
    cfg = D.load_layout(a.layout)
    if a.base:
        cfg['source'] = a.base
    out_dir = os.path.join(OUT, a.name)
    if not a.preview and os.path.exists(out_dir):
        raise SystemExit(f'out/modes/{a.name} exists - pick another build name (builds are never overwritten)')
    m = D.read(cfg['archive'], cfg.get('source'))
    report = [f"{a.layout} -> out/modes/{a.name}/{cfg['archive']} (from {(os.path.relpath(m['source'], ROOT) if m['source'].startswith(ROOT) else m['source'])})"]
    h = D.height_of(cfg['archive'], cfg.get('source'))
    chk = D.checks(cfg, m, h)
    st = chk['stats']
    if st.get('share'):
        report.append(f"zones' share of the play area A / B / C: {st['share']['A']:.3f} / {st['share']['B']:.3f} / {st['share']['C']:.3f}; respawns per zone {st.get('respawns')}; open floor within 8 m of each point {st.get('clear')}")
    report += [f'PROBLEM: {x}' for x in chk['problems']] + [f'warning: {x}' for x in chk['warnings']]
    bg = dict(h.t, png=h.t['height_png'].replace('_height.png', '.png'))
    ok = not chk['problems']
    if not a.preview and (not ok):
        report.append("verify: FAILED (the layout's checks; nothing built - fix the layout, or --preview for the picture)")
        print('\n'.join(report))
        return 1
    if a.preview:
        os.makedirs(out_dir, exist_ok=True)
        if chk['regions']:
            picture(os.path.join(out_dir, 'plan.png'), cfg, m, chk['regions'], chk['lines'], bg=bg)
        report.append(f"preview: {('OK' if ok else 'FAILED')}" + (f' - picture out/modes/{a.name}/plan.png' if chk['regions'] else ''))
        with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
            fh.write('\n'.join(report) + '\n')
        print('\n'.join(report))
        return 0 if ok else 1
    if m['dom']['state'] != 'EMPTY':
        raise SystemExit(f"{cfg['archive']} already has a Domination ({m['dom']['state']}): AC3's and AC4's maps keep theirs, and a build is made from the release's archive (or --base: an archive without one)")
    dest = os.path.join(out_dir, cfg['archive'])
    try:
        built = build(cfg, m, chk['regions'], dest, report)
    except SystemExit:
        if os.path.isdir(out_dir) and (not os.listdir(out_dir)):
            os.rmdir(out_dir)
        raise
    ok = verify(cfg, m, chk['regions'], m['source'], dest, built, report)
    zs = D.read_domination(dest)
    picture(os.path.join(out_dir, 'dom_zones.png'), cfg, m, chk['regions'], chk['lines'], bg=bg)
    report.append(f"picture: dom_zones.png (zones A red / B gold / C blue, points with their 8 m circles, team starts squares, respawns dots); state read back: {zs['state']}")
    report.append(f"verify: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print('\n'.join(report))
    return 0 if ok else 1
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
