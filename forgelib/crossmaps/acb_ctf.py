"""forgelib/crossmaps/acb_ctf.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (os.path.join(ROOT, 'maps', 'port'), os.path.join(ROOT, 'maps'), ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import convert
import entity
import inventory
import mapwalk
import mode_spawns
import spawn_layers
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
CTF_LAYER = 3296798500
SLOT, SLOT_BLOCK = ('DataPC_AC3MP_Plantation.forge', 'DataBlock_ACFE_CTF_2')
BORDER_FROM = 'DataPC_ACFE_Constantinople.forge'
TEMPLATES = ('CTF_FLAG_01', 'CTF_FLAG_02', 'CTF_ScoringZone_01', 'CTF_ScoringZone_02')
BORDER_HALF = 20.0
ACR_SPLIT = 0.42
IDS = convert.class_ids()
CLASS_IDS = {v for v in IDS.values() if v >= 1 << 24 and v & 16777215}

def embedded(body):
    return sorted({struct.unpack_from('<I', body, k)[0] for k in range(2, len(body) - 7) if struct.unpack_from('<I', body, k + 4)[0] in CLASS_IDS and 65536 <= struct.unpack_from('<I', body, k)[0] != 4294967295 and (struct.unpack_from('<I', body, k)[0] not in CLASS_IDS)})

def team_spawns(f, oobs=None):
    out = {1: [], 2: []}
    for x in f.records:
        if x.name == 'GlobalMetaFile' or not x.size:
            continue
        p = mapwalk.Payload(x, f.read(x))
        sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
        for o in p.objects:
            if xrefs.cls('AC3', o['type']) != 'Entity' or o['filter'] is None or CTF_LAYER not in sets[o['filter']]:
                continue
            k = spawn_layers.spawn_kind(o['body'])
            if k and k[0] == 2 and (k[1] in (1, 2)):
                out[k[1]].append(struct.unpack_from('<3f', o['body'], 58))
            if oobs is not None and any((n == 'OutOfBoundsComponent' for _s, _e, n in entity.components(o['body']) or [])):
                oobs.append(o)
    return out

def balanced(area_pts, f1, f2):
    import aa_zones as AZ
    n = math.dist(f1[:2], f2[:2])
    u = ((f2[0] - f1[0]) / n, (f2[1] - f1[1]) / n)

    def sides(t):
        c = (f1[0] + u[0] * t, f1[1] + u[1] * t)
        a_, b_ = ((c[0] - u[1], c[1] + u[0]), (c[0] + u[1], c[1] - u[0]))
        h1 = AZ.clip(area_pts, a_, b_, AZ.side(a_, b_, f1[:2]) > 0)
        h2 = AZ.clip(area_pts, a_, b_, AZ.side(a_, b_, f1[:2]) <= 0)
        return (abs(AZ.area(h1)) if len(h1) > 2 else 0.0, abs(AZ.area(h2)) if len(h2) > 2 else 0.0)
    lo, hi = (n * ACR_SPLIT, n * (1 - ACR_SPLIT))
    for _ in range(60):
        mid = (lo + hi) / 2
        a1, a2 = sides(mid)
        if a1 < a2:
            lo = mid
        else:
            hi = mid
    t = (lo + hi) / 2
    a1, a2 = sides(t)
    return (t, a1, a2, abs(AZ.area(area_pts)))

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--into', default=SLOT_BLOCK, help='the CTF block (DataBlock_ACFE_CTF_2, the default; a cell is refused)')
    ap.add_argument('--uid-base', type=lambda v: int(v, 0), required=True)
    ap.add_argument('--flag1', help="X,Y,Z of team 1's artifact and scoring zone (default: its CTF team spawns' centroid)")
    ap.add_argument('--flag2', help="X,Y,Z of team 2's")
    ap.add_argument('--play-box', help='X0,Y0,X1,Y1: the play area acb_aa_zones.py will use (as its --play-box)')
    ap.add_argument('--area-box', help="X0,Y0,X1,Y1: the same for a map with no OOB entity (aa_zones' --area-box)")
    a = ap.parse_args(argv)
    if a.into != SLOT_BLOCK:
        raise SystemExit(f"--into {a.into}: AA's pieces go into {SLOT_BLOCK}, as on every AC3 map (in the root cell they never joined the World: the ACB maps' AA crash, 2026-10-11)")
    src = os.path.join(OUT, a.src, a.archive)
    report = []
    with Forge(mapwalk.archive_path('AC3', SLOT)) as f:
        r = next((x for x in f.records if x.name == SLOT_BLOCK))
        tpl = {o['name']: o for o in mapwalk.Payload(r, f.read(r)).objects if o['name'] in TEMPLATES}
    with Forge(mapwalk.archive_path('ACR', BORDER_FROM)) as f:
        r = next((x for x in f.records if x.name == SLOT_BLOCK))
        border = next((o for o in mapwalk.Payload(r, f.read(r)).objects if xrefs.cls('ACR', o['type']) == 'Entity' and o['name'] == 'CTF_Border'))
    image = ArchiveImage.read(src)
    oobs = []
    with Forge(src) as f:
        spawns = team_spawns(f, oobs)
        defined, managers, rec = (set(), [], None)
        for x in f.records:
            if x.name != 'GlobalMetaFile' and x.size:
                q = mapwalk.Payload(x, f.read(x))
                defined |= {o['uid'] for o in q.objects if o['has_uid']}
                managers += [o for o in q.objects if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager']
                if x.name == a.into:
                    rec, p = (x, q)
    if rec is None:
        raise SystemExit(f"no {a.into} in {a.archive}: the map was not built through archive_fixes (it adds the slot's blocks)")
    block_uid = p.objects[0]['uid']
    opened = struct.pack('<I', CTF_LAYER) + b'\x00' + struct.pack('<I', block_uid)
    if not any((opened in o['body'] for o in managers)):
        raise SystemExit(f"the World's WorldDataLayerManager doesn't open {a.into} ({block_uid:#010x}) for ACFE_CTF_2")
    if not spawns[1] or not spawns[2]:
        raise SystemExit(f'CTF team spawns: team 1 {len(spawns[1])}, team 2 {len(spawns[2])}')
    flags = {}
    for t, given in ((1, a.flag1), (2, a.flag2)):
        if given:
            flags[t] = tuple((float(v) for v in given.split(',')))
        else:
            pts = spawns[t]
            flags[t] = tuple((sum((q[i] for q in pts)) / len(pts) for i in range(3)))
        near = min((math.dist(flags[t][:2], q[:2]) for q in spawns[t]))
        report.append(f'team {t}: {len(spawns[t])} CTF spawns; artifact + scoring zone at ({flags[t][0]:.2f}, {flags[t][1]:.2f}, {flags[t][2]:.2f}), {near:.2f} m from the nearest')
    f1, f2 = (flags[1], flags[2])
    dx, dy = (f2[0] - f1[0], f2[1] - f1[1])
    n = math.hypot(dx, dy)
    d = (-dy / n, dx / n)
    import acb_aa_zones
    import aa_zones as AZ
    box = a.play_box or a.area_box
    if box:
        x0, y0, x1, y1 = (float(v) for v in box.split(','))
        area_pts, how = ([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], f'the box {box}')
    elif len(oobs) == 1:
        acb_aa_zones.PLAY_BOX[:] = []
        area_pts, how = (AZ.ccw(acb_aa_zones.oob_outline(oobs[0]['body'])), f"{oobs[0]['name']}'s outline")
    else:
        raise SystemExit(f'{len(oobs)} OOB entities on ACFE_CTF_2 and no --play-box / --area-box')
    t, a1, a2, total = balanced(area_pts, f1, f2)
    t_mid = n / 2
    mid = (f1[0] + dx / n * t, f1[1] + dy / n * t, (f1[2] + f2[2]) / 2)
    report.append(f'flags {n:.1f} m apart; play area: {how}, {total:.0f} m2; border {t:.1f} m from flag 1 (the bisector: {t_mid:.1f}), territories {a1:.0f} / {a2:.0f} m2 ({100 * a1 / (a1 + a2):.0f} / {100 * a2 / (a1 + a2):.0f} %); through ({mid[0]:.2f}, {mid[1]:.2f}) along ({d[0]:.3f}, {d[1]:.3f})')
    taken = set(inventory.universe('AC3')) | set(inventory.universe('ACR')) | defined
    nxt = [a.uid_base]

    def fresh():
        while nxt[0] in taken:
            nxt[0] += 1
        taken.add(nxt[0])
        return nxt[0]

    def renew(body):
        for u in embedded(body):
            body = body.replace(struct.pack('<I', u), struct.pack('<I', fresh()))
        return body

    def placed(body, xyz):
        return body[:58] + struct.pack('<3f', *xyz) + body[70:]
    new = []
    for name in TEMPLATES:
        o = tpl[name]
        t = int(name[-1])
        body = placed(renew(o['body']), flags[t])
        if name.startswith('CTF_FLAG'):
            k = body.find(struct.pack('<I', IDS['FlagComponent']))
            if k < 0 or body[k + 4] != 1:
                raise SystemExit(f"{name}: FlagComponent's position not where expected")
            body = body[:k + 5] + struct.pack('<3f', *flags[t]) + body[k + 17:]
        uid = struct.unpack_from('<I', body, 2)[0]
        new.append(dict(o, uid=uid, serial=uid, body=body))
    bbody = renew(border['body'])
    pat = struct.pack('<I', IDS['CTFWallSection'])
    ks = []
    k = bbody.find(pat)
    while k >= 0:
        ks.append(k)
        k = bbody.find(pat, k + 4)
    out = bytearray(bbody)
    for i, k in enumerate(ks):
        s = -BORDER_HALF + 2 * BORDER_HALF * i / (len(ks) - 1)
        struct.pack_into('<3f', out, k + 4, mid[0] + d[0] * s, mid[1] + d[1] * s, mid[2])
    bbody = bytes(out)
    buid = struct.unpack_from('<I', bbody, 2)[0]
    new.append(dict(border, uid=buid, serial=buid, body=bbody, body_size=border['body_size']))
    report.append(f"border: Galata's CTF_Border (raw ACR), its {len(ks)} CTFWallSection points on the line (+-{BORDER_HALF:.0f} m)")
    table = p.filter_table
    sets = spawn_layers.filter_sets(table) if table else []
    ctf = next((i for i, s in enumerate(sets) if s == {CTF_LAYER}), None)
    if ctf is None:
        table, ctf = mode_spawns.add_filter(table, [CTF_LAYER])
        report.append(f'{rec.name}: ACFE_CTF_2-only filter added (index {ctf})')
    head = p.objects[0]
    hb = head['body']
    nn = struct.unpack_from('<I', hb, 10)[0]
    end = 14 + 6 * nn
    mm, layer = struct.unpack_from('<II', hb, end)
    if len(hb) != end + 8:
        raise SystemExit(f'{rec.name}: head is not u32 N, N entries, u32 M, u32 layer ({len(hb)} bytes, N {nn})')
    if layer != CTF_LAYER:
        report.append(f"{rec.name}: its head named layer {layer:#010x} (archive_fixes' placeholder, cloned from DataBlock_z_AC2_LGS01) -> ACFE_CTF_2 {CTF_LAYER:#010x}, as every AC3 block names its own")
    add = b''.join((b'\x01\x00' + struct.pack('<I', o['uid']) for o in new))
    at = 14 + 6 * mm
    hb = hb[:10] + struct.pack('<I', nn + len(new)) + hb[14:at] + add + hb[at:end] + struct.pack('<II', mm + len(new), CTF_LAYER)
    objs = [(head['serial'], mapwalk.object_bytes(head, hb), head['filter'])]
    objs += [(o['serial'], mapwalk.object_bytes(o), o['filter']) for o in p.objects[1:]]
    objs += [(o['serial'], mapwalk.object_bytes(o), ctf) for o in new]
    d_ = mapwalk.rebuild(objs, table)
    payload = compress(d_, first_chunk=mapwalk.first_chunk(d_))
    assert decompress(payload) == d_
    set_payload(image, rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = True
    with Forge(dest) as fo, Forge(src) as fi:
        others = all((fo.read(x) == fi.read(y) for x, y in zip(fo.records, fi.records) if x.index != rec.index))
        q = mapwalk.Payload(fo.records[rec.index], fo.read(fo.records[rec.index]))
        qs = spawn_layers.filter_sets(q.filter_table)
        got = {o['uid']: o for o in q.objects if o['has_uid']}
        qb = q.objects[0]['body']
        n1 = struct.unpack_from('<I', qb, 10)[0]
        m1, layer1 = struct.unpack_from('<II', qb, 14 + 6 * n1)
        world = [qb[k:k + 6] for k in range(14, 14 + 6 * m1, 6)]
        for o in new:
            g = got.get(o['uid'])
            ok &= g is not None and g['body'] == o['body'] and (qs[g['filter']] == {CTF_LAYER})
            ok &= b'\x01\x00' + struct.pack('<I', o['uid']) in world
        ok &= (n1, m1, layer1) == (nn + len(new), mm + len(new), CTF_LAYER) and len(qb) == 14 + 6 * n1 + 8
        for o in new[:2]:
            k = o['body'].find(struct.pack('<I', IDS['FlagComponent']))
            ok &= o['body'][58:70] == o['body'][k + 5:k + 17]
        teams = sorted((entity.components(o['body'])[0][2] for o in new))
        ok &= others and len(q.objects) == len(p.objects) + len(new)
        ok &= all((math.dist(struct.unpack_from('<3f', o['body'], 58), flags[int(o['name'][-1])]) < 0.001 for o in new[:4]))
    report.append(f"verify: 5 entities in {rec.name} on ACFE_CTF_2, listed in its world part (N, M) ({nn}, {mm}) -> ({n1}, {m1}), its layer {layer1:#010x}, opened by the World's WorldDataLayerManager; placed; flags' FlagComponent copies match; every other record byte-identical {others}; components {teams}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report_ctf.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
