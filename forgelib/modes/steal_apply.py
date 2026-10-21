"""forgelib/modes/steal_apply.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'maps', 'port'), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import aa_variant as V
import chest_check
import corruption_check
import dom_apply
import dom_check
import entity
import escort_apply
import escort_check as E
import inventory
import mapwalk
import oob_modes
import spawn_layers
import steal_check as S
import xrefs
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from mode_spawns import KIT_CLASSES
OUT = os.path.join(ROOT, 'out', 'modes')
OUT_MAPS = os.path.join(ROOT, 'out', 'maps')
BLOCK, TEMPLATE_BLOCK = (S.BLOCK, 'DataBlock_ACFE_Wanted')
HIJACK = S.HIJACK
UID_BASE, UID_SPAN, UID_END = (1534001152, 768, 1534017280)
UNMAPPED = (4026531840, 4160749568)
SHORT = escort_apply.SHORT

def comps(o):
    return [n for _s, _e, n in entity.components(o['body']) or []]

def slot_base(archive):
    archive = escort_apply.release_name(archive)
    if archive not in dom_apply.SLOTS:
        raise SystemExit(f'{archive}: no uid slot (dom_apply.SLOTS) - add one first')
    base = UID_BASE + dom_apply.SLOTS.index(archive) * UID_SPAN
    assert base + UID_SPAN <= UID_END
    return base

def kit_key(o):
    return o['uid'] if o['has_uid'] else (o['name'], len(o['body']))

def build(archive, src, dest, report):
    files = oob_modes.load(src)
    blk, tpl_blk = (files.get(BLOCK), files.get(TEMPLATE_BLOCK))
    if blk is None or tpl_blk is None:
        raise SystemExit(f'{archive}: no {BLOCK} / {TEMPLATE_BLOCK}')
    old2 = [o for o in blk.objects[1:] if xrefs.cls('AC3', o['type']) == 'Entity' and (spawn_layers.spawn_kind(o['body']) or (9,))[0] == 2]
    if any(('Scene' in comps(o) for o in old2)):
        raise SystemExit(f"{BLOCK} already has Scene'd type-2 spawns")
    tpl = sorted((o for o in tpl_blk.objects[1:] if xrefs.cls('AC3', o['type']) == 'Entity' and spawn_layers.spawn_kind(o['body']) == (2, 0) and ('Scene' in comps(o))), key=lambda o: o['name'])
    if len(tpl) != 8:
        raise SystemExit(f"{TEMPLATE_BLOCK}: {len(tpl)} Scene'd type-2 index-0 spawns, not mode_spawns' 8")
    kit = [o for o in tpl_blk.objects if xrefs.cls('AC3', o['type']) in KIT_CLASSES]
    if len(kit) != 13:
        raise SystemExit(f'{TEMPLATE_BLOCK}: {len(kit)} camera-kit objects, not 13')
    defined = {}
    for rname, p in files.items():
        for o in p.objects:
            if o['has_uid']:
                defined.setdefault(o['uid'], []).append((rname, o))
    base = slot_base(archive)
    taken = set(inventory.universe('AC3')) | set(inventory.universe('ACR')) | set(defined)
    taken |= escort_apply.base_objects({base >> 16})
    nxt, new_uids = ([base], [])

    def fresh():
        while nxt[0] in taken:
            nxt[0] += 1
        if nxt[0] >= base + UID_SPAN:
            raise SystemExit("the map's uid slot is used up")
        taken.add(nxt[0])
        new_uids.append(nxt[0])
        return nxt[0]
    drop = {o['uid'] for o in old2}
    if drop:
        hit = names_of(files, drop, skip_heads=(BLOCK,))
        if hit:
            raise SystemExit(f'{len(hit)} scene-less Hijack start(s) named by another object: {hit[:4]}')
        report.append(f'{BLOCK}: {len(drop)} scene-less type-2 start(s) ACR left (' + ', '.join(sorted((o['name'] for o in old2))) + ') removed (list and object)')
    table, fi = oob_modes.filter_index(blk.filter_table, {HIJACK})
    if table is not blk.filter_table:
        report.append(f'{BLOCK}: a DataLayerFilter {{ACFE_Hijack}} added to the table (index {fi})')
    own = E.filter_lists(table)[fi]
    if own != [HIJACK]:
        raise SystemExit(f'{BLOCK}: filter {fi} lists {own}, not ACFE_Hijack alone')
    clones = []
    for k, t_o in enumerate(tpl, 1):
        ren = {u: fresh() for u in V.embedded(t_o['body'])}
        b = E.set_own_filter(V.remap(t_o['body'], ren), own)
        if spawn_layers.spawn_kind(b) != (2, 0) or 'Scene' not in [n for _s, _e, n in entity.components(b) or []]:
            raise SystemExit(f"{t_o['name']}: the clone is not a Scene'd type-2 spawn")
        if (E.own_filter(b) or (0, 0, None))[2] != own or b[10:74] != t_o['body'][10:74]:
            raise SystemExit(f"{t_o['name']}: the clone's own filter / transform did not come out right")
        uid = struct.unpack_from('<I', b, 2)[0]
        clones.append(dict(t_o, body=b, body_size=t_o['body_size'] + len(b) - len(t_o['body']), uid=uid, serial=uid, name=f'Hijack_Initial_Spawn_{k:02d}', old=t_o))
    report.append(f"initial spawns: {TEMPLATE_BLOCK}'s 8 Scene'd type-2 index-0 spawns ({tpl[0]['name']} .. {tpl[-1]['name']}, own filter {[spawn_layers_name(u) for u in (E.own_filter(tpl[0]['body']) or (0, 0, []))[2]]}) cloned into {BLOCK} as Hijack_Initial_Spawn_01-08: fresh uids, transforms and Scenes as they are, directory filter {fi} {{ACFE_Hijack}}, own DataLayerFilter [ACFE_Hijack]")
    have = {kit_key(o) for o in blk.objects}
    added_kit = [o for o in kit if kit_key(o) not in have]
    for o in added_kit:
        for rname, d in defined.get(o['uid'], []) if o['has_uid'] else []:
            if xrefs.cls('AC3', d['type']) == xrefs.cls('AC3', o['type']) and d['body'] != o['body']:
                raise SystemExit(f"kit {o['name']} {o['uid']:#x}: {rname} defines it with other bytes")
    report.append(f"camera kit: {len(added_kit)} of the {len(kit)} objects appended unfiltered (= {TEMPLATE_BLOCK}'s uids and bytes)")
    add = [(c, fi) for c in clones]
    needs = []
    _blocks, areas = oob_modes.scan(files)
    mine = [a for a in areas if a['rec'] == BLOCK and ('ACFE_Hijack' in a['modes'] or not a['any_known'])]
    by_uid = {o['uid']: o for o in blk.objects if o['has_uid']}
    good = [a for a in mine if E.own_allows(by_uid[a['uid']]['body'], HIJACK)]
    oob_area = None
    if good:
        report.append(f"out of bounds: {', '.join(sorted({a['name'] for a in good}))} in {BLOCK} names ACFE_Hijack (directory and own filter) - kept")
    else:
        src_a = [a for a in areas if a['rec'] == TEMPLATE_BLOCK and 'ACFE_Wanted' in a['modes']]
        if len({a['uid'] for a in src_a}) > 1:
            raise SystemExit(f"no Hijack out-of-bounds area and no single Wanted area to copy ({[a['name'] for a in src_a]})")
    if not good and (not src_a):
        report.append('out of bounds: the map has no Wanted out-of-bounds area and none names ACFE_Hijack (walled in: Palazzio) - none for Steal either (steal_check WARN)')
    elif not good:
        a = src_a[0]
        ent = next((o for o in files[a['rec']].objects if o['has_uid'] and o['uid'] == a['uid']))
        if not E.own_allows(ent['body'], HIJACK):
            raise SystemExit(f"the Wanted area {a['name']}'s own filter leaves ACFE_Hijack out: a copy would be dropped")
        root = oob_modes.root_cell(files)
        kit_uids = {o['uid'] for o in added_kit if o['has_uid']}
        needs = [o for o in oob_modes.closure(files, a['rec'], a['uid'], BLOCK, root) if o['uid'] != a['uid'] and o['uid'] not in kit_uids]
        oob_area = ent
        add.append((ent, fi))
        report.append(f"out of bounds: {a['name']} {a['uid']:#x} (the map's Wanted area) copied into {BLOCK} on {{ACFE_Hijack}}, body and uid unchanged, with {len(needs)} objects it needs")
    root = oob_modes.root_cell(files)
    moved, cells = ([], {})
    for rname, p in files.items():
        if not rname.startswith('Cell') or rname == root:
            continue
        sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
        for o in p.objects[1:]:
            lay = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else None
            if xrefs.cls('AC3', o['type']) == 'Entity' and o['has_uid'] and lay and (HIJACK in lay) and ('HijackComponent' in comps(o)):
                if not E.own_allows(o['body'], HIJACK):
                    raise SystemExit(f"{o['name']}: its own DataLayerFilter leaves ACFE_Hijack out")
                need = oob_modes.closure(files, rname, o['uid'], BLOCK, root)
                if [x['uid'] for x in need] != [o['uid']]:
                    raise SystemExit(f"{o['name']}: needs objects of {rname} the root cell lacks: {[x['name'] for x in need[1:]][:4]}")
                moved.append(dict(o, cell=rname))
                cells.setdefault(rname, set()).add(o['uid'])
    payloads = {}
    if moved:
        hit = names_of(files, {o['uid'] for o in moved}, skip_heads=tuple(cells))
        if hit:
            raise SystemExit(f'{len(hit)} artifact(s) named by another object: {hit[:4]}')
        add += [(o, fi) for o in moved]
        for rname, uids in cells.items():
            payloads[rname] = oob_modes.rewrite(files[rname], uids, [], [])
        report.append(f"artifacts: {len(moved)} HijackComponent entities moved from the streaming cell(s) {', '.join(sorted(cells))} (root cell {root}) into {BLOCK} on {{ACFE_Hijack}}, bodies unchanged ({', '.join((o['name'] for o in moved))})")
    payloads[BLOCK] = oob_modes.rewrite(blk, drop, add, list(added_kit) + needs, None if table is blk.filter_table else table)
    report.append(f'{BLOCK}: list N {oob_modes.list_n(blk)} -> {oob_modes.list_n(blk) - len(drop) + len(add)}, objects {len(blk.objects)} -> {len(blk.objects) - len(drop) + len(add) + len(added_kit) + len(needs)}')
    image = ArchiveImage.read(src)
    index = {p.rec.name: p.rec.index for p in files.values()}
    for rname, pl in payloads.items():
        set_payload(image, index[rname], pl)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    write(dest, image)
    return dict(clones=clones, kit=added_kit, needs=needs, oob=oob_area, fi=fi, table=table, new_uids=new_uids, defined=set(defined), drop=drop, moved=moved, cells=cells, changed=set(payloads))

def names_of(files, uids, skip_heads=()):
    pats = {u: [bytes([1, kk]) + struct.pack('<I', u) for kk in (0, 1, 2)] for u in uids}
    hit = []
    for rname, p in files.items():
        for j, o in enumerate(p.objects):
            if j == 0 and rname in skip_heads:
                continue
            hit += [f"{u:#x} in {rname} {o['name']}" for u, ps in pats.items() if o.get('uid') != u and any((x in o['body'] for x in ps))]
    return hit

def spawn_layers_name(u):
    import archive_fixes
    return archive_fixes.layer_names('AC3').get(u, f'{u:#x}')

def verify(src, dest, built, report):
    import aa_records
    import zones as Z
    ok = True
    r0, r1 = (S.check(src), S.check(dest))
    good = r1['verdict'] in ('OK', 'WARN') and r0['verdict'] in ('FAIL', 'EMPTY')
    ok &= good
    report.append(f"steal_check: {r1['verdict']} (was {r0['verdict']}): " + S.line(r1).split(None, 1)[1])
    for label, fn in (('escort_check', E.check), ('chest_check', chest_check.check), ('corruption_check', corruption_check.check), ('dom_check', dom_check.check)):
        a, b = (fn(src), fn(dest))
        same = (a['verdict'], a['problems'], a['warnings']) == (b['verdict'], b['problems'], b['warnings'])
        ok &= same
        report.append(f"{label} unchanged: {b['verdict']} (was {a['verdict']}): {same}")
    files = oob_modes.load(dest)
    changed = built['changed']
    with Forge(src) as g, Forge(dest) as f:
        same = len(f.records) == len(g.records) and all((x.name == y.name and (x.name in changed or f.read(x) == g.read(y)) for x, y in zip(f.records, g.records)))
        befores = {x.name: mapwalk.Payload(x, g.read(x)) for x in g.records if x.name in changed}
    ok &= same
    report.append(f"every record but {', '.join(sorted(changed))} byte-identical: {same}")
    ba, bb = (files[BLOCK], befores[BLOCK])
    sa = spawn_layers.filter_sets(ba.filter_table) if ba.filter_table else []
    sb = spawn_layers.filter_sets(bb.filter_table) if bb.filter_table else []
    by_a = {}
    for o in ba.objects[1:]:
        by_a.setdefault(kit_key(o), []).append(o)
    old_same = all((any((o['body'] == q['body'] and (o['filter'] is None) == (q['filter'] is None) and (o['filter'] is None or sa[o['filter']] == sb[q['filter']]) for o in by_a.get(kit_key(q), []))) for q in bb.objects[1:] if not (q['has_uid'] and q['uid'] in built['drop'])))
    gone = not any((u in by_a for u in built['drop']))
    n_a = oob_modes.list_n(ba)
    listed = {struct.unpack_from('<I', ba.objects[0]['body'], 14 + 6 * k + 2)[0] for k in range(n_a)}
    listed0 = {struct.unpack_from('<I', bb.objects[0]['body'], 14 + 6 * k + 2)[0] for k in range(oob_modes.list_n(bb))}
    want = {c['uid'] for c in built['clones']} | ({built['oob']['uid']} if built['oob'] else set()) | {o['uid'] for o in built['moved']}
    listed0 -= built['drop']
    c_ok = True
    for c in built['clones']:
        o = by_a.get(c['uid'], [None])[0]
        c_ok &= o is not None and o['body'] == c['body'] and (sa[o['filter']] == {HIJACK}) and ((E.own_filter(o['body']) or (0, 0, None))[2] == [HIJACK]) and ('Scene' in comps(o)) and (spawn_layers.spawn_kind(o['body']) == (2, 0)) and (o['body'][10:74] == c['old']['body'][10:74])
    k_ok = all((any((x['body'] == o['body'] and x['filter'] is None for x in by_a.get(kit_key(o), []))) for o in built['kit']))
    m_ok = all((any((x['body'] == o['body'] and sa[x['filter']] == {HIJACK} for x in by_a.get(o['uid'], []))) for o in built['moved']))
    b_ok = old_same and gone and (listed == listed0 | want) and c_ok and k_ok and m_ok
    ok &= b_ok
    report.append(f"{BLOCK}: old objects byte-identical with their filters {old_same}; the {len(built['drop'])} scene-less start(s) gone {gone}; list = old - those + the {len(want)} new {listed == listed0 | want}; clones Scene'd type-2, directory + own filter ACFE_Hijack alone, the Wanted transforms {c_ok}; kit = the Wanted block's, unfiltered {k_ok}; moved artifacts = their cells' bytes on {{ACFE_Hijack}} {m_ok}: {b_ok}")
    for rname, uids in built['cells'].items():
        ca, cb_ = (files[rname], befores[rname])
        rest = [q for q in cb_.objects[1:] if not (q['has_uid'] and q['uid'] in uids)]
        sa_, sb_ = (spawn_layers.filter_sets(x.filter_table) if x.filter_table else [] for x in (ca, cb_))
        c2 = len(ca.objects) == len(rest) + 1 and oob_modes.list_n(ca) == oob_modes.list_n(cb_) - len(uids) and all((o['body'] == q['body'] and (o['filter'] is None) == (q['filter'] is None) and (o['filter'] is None or sa_[o['filter']] == sb_[q['filter']]) for o, q in zip(ca.objects[1:], rest)))
        ok &= c2
        report.append(f'{rname}: only the {len(uids)} artifact(s) gone (list and object): {c2}')
    o1, p1, rows1 = oob_modes.judge(files)
    ok &= o1
    report.append(f"oob_modes: {('PASS' if o1 else 'FAIL ' + '; '.join(p1))}" + ''.join((f'; {x}' for x in rows1 if x.startswith('ACFE_Hijack'))))
    zn, zbad = Z.check_archive(dest)
    ok &= not zbad
    report.append(f'zones.py check: {zn} zone entities, problems {len(zbad)}')
    rec_ok, rec_problems, _n, _one = aa_records.judge(aa_records.pieces(files), files)
    ok &= rec_ok
    report.append(f"aa_records (Artifact Assault untouched): {('PASS' if rec_ok else 'FAIL ' + '; '.join(rec_problems))}")
    counts = {}
    for q in files.values():
        for o in q.objects:
            if o['has_uid']:
                counts[o['uid']] = counts.get(o['uid'], 0) + 1
    tops = [u for u in built['new_uids'] if u in counts]
    once = all((counts[u] == 1 for u in tops)) and (not any((u in built['defined'] for u in built['new_uids'])))
    unm = [hex(u) for u in built['new_uids'] if UNMAPPED[0] <= u < UNMAPPED[1]]
    ok &= once and (not unm) and (len(tops) == len(built['clones']))
    report.append(f"new uids: {len(built['new_uids'])} ({len(tops)} objects, the rest embedded), {min(built['new_uids']):#x}-{max(built['new_uids']):#x}, none the source defines, each object one body: {once}; in 0xF0000000-0xF7FFFFFF: {unm or 'none'}")
    return ok

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('map', nargs='?', help="a short name (galata, ...) or the archive's file name")
    ap.add_argument('--name', help='build folder under out/modes/ (refused if it exists)')
    ap.add_argument('--base', help="build on this archive instead of the release's copy")
    ap.add_argument('--src', help='(a chain step) the build under out/maps/ to apply it to')
    ap.add_argument('--archive', help="(a chain step) its archive (the chain's name)")
    ap.add_argument('--prefix', help="(a chain step) the output's prefix")
    a = ap.parse_args(argv)
    if a.src:
        if not (a.archive and a.prefix) or a.name or a.base or a.map:
            ap.error('a chain step takes --src, --archive and --prefix only')
        archive = a.archive
        src = os.path.join(OUT_MAPS, a.src, archive)
        out_dir = os.path.join(OUT_MAPS, f"{a.prefix}_{a.src.split('_', 1)[1]}")
    else:
        if not (a.map and a.name):
            ap.error("give a map and --name (or a chain step's --src / --archive / --prefix)")
        archive = SHORT.get(a.map.lower(), a.map)
        src = dom_check.resolve(a.base) if a.base else escort_apply.release_source(archive)
        out_dir = os.path.join(OUT, a.name)
        if os.path.exists(out_dir):
            raise SystemExit(f'out/modes/{a.name} exists - pick another build name (builds are never overwritten)')
    dest = os.path.join(out_dir, archive)
    report = [f'{archive} -> {os.path.relpath(dest, ROOT)} (from {src})']
    try:
        built = build(archive, src, dest, report)
    except SystemExit:
        if os.path.isdir(out_dir) and (not os.listdir(out_dir)):
            os.rmdir(out_dir)
        raise
    ok = verify(src, dest, built, report)
    report.append(f"verify: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report_steal.txt' if a.src else 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print('\n'.join(report))
    if a.src:
        if not ok:
            raise SystemExit('verify FAILED')
        print(f'wrote {os.path.relpath(dest, ROOT)}')
    return 0 if ok else 1
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
