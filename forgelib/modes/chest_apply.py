"""forgelib/modes/chest_apply.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import chest_check as C
import dom_check
import entity
import escort_apply as EA
import escort_check as E
import inventory
import mapwalk
import oob_modes
import spawn_layers
import xrefs
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from mode_spawns import KIT_CLASSES
OUT = os.path.join(ROOT, 'out', 'modes')
OUT_MAPS = os.path.join(ROOT, 'out', 'maps')
BLOCK = C.BLOCK
LAYER = C.CHEST_LAYER
TEAMWANTED = 'DataBlock_gamemode_teamwanted'
UID_BASE, UID_SPAN, UID_END = (1533607936, 1024, 1533673216)
UNMAPPED = EA.UNMAPPED
SLOTS = EA.SLOTS
ESCORT_BUILD = 'R26T4_{map}'

def release_name(archive):
    return archive[:-len('_dlc.forge')] + '.forge' if archive.endswith('_dlc.forge') else archive

def slot_base(archive):
    i = SLOTS.index(archive) if archive in SLOTS else 19 + sum(archive.encode()) % 2
    base = UID_BASE + i * UID_SPAN
    assert base + UID_SPAN <= UID_END
    return base

def lay_of(p, o, sets=None):
    sets = sets if sets is not None else spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
    return sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else None

def kind(o):
    return spawn_layers.spawn_kind(o['body']) if EA.is_entity(o) else None

def mspc(body):
    cs = entity.components(body) or []
    return next(((s, e) for s, e, n in cs if n == 'MultiSpawnPlayerComponent'), None)

def with_body(o, b, **kw):
    return dict(o, body=b, body_size=o['body_size'] + len(b) - len(o['body']), **kw)

def own_minus(body, layer, fallback):
    f = E.own_filter(body)
    if f is None or layer not in f[2]:
        return body
    rest = [u for u in f[2] if u != layer]
    return E.set_own_filter(body, rest or list(fallback))

def build(archive, src, dest, report):
    rel = release_name(archive)
    files = oob_modes.load(src)
    cb = files.get(BLOCK)
    if cb is None:
        raise SystemExit(f'no {BLOCK} in {archive}')
    cb_lists = E.filter_lists(cb.filter_table) if cb.filter_table else []
    eb = files.get(E.BLOCK)
    defined = {}
    for rname, p in files.items():
        sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
        for o in p.objects:
            if o['has_uid']:
                defined.setdefault(o['uid'], []).append((rname, o, lay_of(p, o, sets)))
    base = slot_base(rel)
    in_base = EA.base_objects({base >> 16})
    taken = set(inventory.universe('AC3')) | set(inventory.universe('ACR')) | set(defined) | in_base
    nxt, new_uids = ([base], [])

    def fresh():
        while nxt[0] in taken:
            nxt[0] += 1
        if nxt[0] >= base + UID_SPAN:
            raise SystemExit("the map's uid slot is used up")
        taken.add(nxt[0])
        new_uids.append(nxt[0])
        return nxt[0]
    tpl, kit, where = EA.templates(files)
    payloads, changed_notes = ({}, {})
    old = [(i, o) for i, o in enumerate(cb.objects) if i and kind(o) and (kind(o)[0] == 2) and (o['filter'] is None or LAYER in cb_lists[o['filter']])]
    cell_old = []
    if not old:
        for rname, p in files.items():
            if not rname.startswith('Cell'):
                continue
            lists = E.filter_lists(p.filter_table) if p.filter_table else []
            for o in p.objects[1:]:
                k = kind(o)
                lay = lists[o['filter']] if o['filter'] is not None and o['filter'] < len(lists) else None
                if k and k[0] == 2 and lay and (LAYER in lay):
                    cell_old.append((rname, o, lay))
        if not cell_old:
            raise SystemExit(f'{BLOCK} has no team start (type 2), nor does any cell on {LAYER:#x}')
    starts = old if old else [(None, o) for _r, o, _l in cell_old]
    if any(('Scene' in EA.comps(o) for _i, o in starts)):
        raise SystemExit("the chest layer already has Scene'd team starts")
    need = sorted({kind(o)[1] for _i, o in starts})
    if all((i in tpl for i in need)):
        imap = {i: i for i in need}
    else:
        avail = sorted(tpl)
        if len(need) > len(avail):
            raise SystemExit(f'{len(need)} team indices {need}, templates for {avail} only')
        imap = dict(zip(need, avail))
    esc = {}
    if eb is not None and old:
        el = E.filter_lists(eb.filter_table) if eb.filter_table else []
        for o in eb.objects[1:]:
            k = kind(o)
            if k and k[0] == 2 and ('Scene' in EA.comps(o)) and (o['filter'] is not None):
                esc.setdefault(o['body'][10:74], []).append((o, el[o['filter']]))
    used, repl, reused, kit_idx = ({}, {}, 0, set())
    for i, o in sorted(starts, key=lambda x: (kind(x[1])[1], x[1]['name'])):
        idx = kind(o)[1]
        if old and o['filter'] is None:
            raise SystemExit(f"{o['name']}: an unfiltered team start in {BLOCK}")
        want = cb_lists[o['filter']] if old else [LAYER]
        if LAYER not in want:
            raise SystemExit(f"{o['name']}: its directory filter leaves ACFE_Chest_Capture out")
        m = esc.get(o['body'][10:74], [])
        if old and len(m) == 1 and (m[0][1] == want) and ((E.own_filter(m[0][0]['body']) or (0, 0, None))[2] == want):
            c = m[0][0]
            repl[i] = dict(c, filter=o['filter'], old=o, reused=True)
            kit_idx.add(kind(c)[1])
            reused += 1
            continue
        ti = imap[idx]
        k = used.get(idx, 0)
        used[idx] = k + 1
        t_o = tpl[ti][k % len(tpl[ti])]
        ren = {u: fresh() for u in V.embedded(t_o['body'])}
        b = V.remap(t_o['body'], ren)
        b = b[:10] + o['body'][10:74] + b[74:]
        b = E.set_own_filter(b, want)
        if spawn_layers.spawn_kind(b) != (2, ti) or 'Scene' not in [n for _s, _e, n in entity.components(b) or []]:
            raise SystemExit(f"{t_o['name']}: the clone is not a Scene'd index-{ti} start")
        if (E.own_filter(b) or (0, 0, None))[2] != want:
            raise SystemExit(f"{t_o['name']}: the clone's own DataLayerFilter did not take")
        uid = struct.unpack_from('<I', b, 2)[0]
        kit_idx.add(ti)
        repl[i if old else o['uid']] = dict(with_body(t_o, b), uid=uid, serial=uid, name=f'Chest_Team{ti}_Spawn_{k + 1:02d}', filter=o['filter'], old=o, reused=False)
    kit_blocks = {where[t] for t in kit_idx if t in where}
    kits = {key: o for key, (rname, o) in kit.items() if rname in kit_blocks}
    for rname in kit_blocks:
        mine = {o['uid'] if o['has_uid'] else (o['name'], len(o['body'])): o['body'] for o in files[rname].objects if xrefs.cls('AC3', o['type']) in KIT_CLASSES}
        if any((kits[k]['body'] != b for k, b in mine.items() if k in kits)) or set(mine) != set(kits):
            raise SystemExit(f"the camera kit differs between {', '.join(sorted(kit_blocks))}")
    if eb is not None and reused:
        ek = {o['uid']: o['body'] for o in eb.objects if o['has_uid'] and xrefs.cls('AC3', o['type']) in KIT_CLASSES}
        if any((ek.get(o['uid']) != o['body'] for o in kits.values() if o['has_uid'])):
            raise SystemExit("the Escort block's camera kit is not the template block's")
    have = {o['uid'] if o['has_uid'] else (o['name'], len(o['body'])) for o in cb.objects}
    added = [o for key, o in kits.items() if key not in have]
    for o in added:
        for rname, d, _lay in defined.get(o['uid'], []) if o['has_uid'] else []:
            if xrefs.cls('AC3', d['type']) == xrefs.cls('AC3', o['type']) and d['body'] != o['body']:
                raise SystemExit(f"kit {o['name']} {o['uid']:#x}: {rname} defines it with other bytes")
    remap_txt = '' if all((x == y for x, y in imap.items())) else ' (team index ' + ', '.join((f'{x} -> {y}' for x, y in imap.items())) + ')'
    if old:
        report.append(f"team starts (block route): {len(starts)} scene-less ACR starts in {BLOCK} -> {reused} = the Scene'd Escort start on the same transform (same uid and bytes; directory = own filter = the same layers, incl. Escort and Chest_Capture), {len(starts) - reused} fresh clone(s){remap_txt}; camera kit {len(kits)} objects from {', '.join(sorted(kit_blocks))} ({len(added)} appended)")
    else:
        report.append(f"team starts (cell route): {len(starts)} scene-less starts in {', '.join(sorted({r for r, _o, _l in cell_old}))} -> Scene'd clones{remap_txt} in {BLOCK} on ACFE_Chest_Capture alone (directory and own filter), each on its cell start's transform; camera kit {len(kits)} objects from {', '.join(sorted(kit_blocks))}")
    pts_on, pts_tw = ([], [])
    for rname, p in files.items():
        sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
        for o in p.objects[1:]:
            k = kind(o)
            if not (k and k[0] == 3):
                continue
            lay = lay_of(p, o, sets)
            if rname == BLOCK and (lay is None or LAYER in lay) or ((rname.startswith('Cell') or p.rec.index == 1) and (lay is None or LAYER in lay)):
                pts_on.append((rname, o))
            elif rname == TEAMWANTED:
                pts_tw.append(o)
    bad_own = [o['name'] for _r, o in pts_on if not E.own_allows(o['body'], LAYER)]
    if bad_own:
        raise SystemExit(f'{len(bad_own)} chest point(s) on the chest layer whose own filter leaves it out: {bad_own[:4]}')
    moved = []
    if not pts_on:
        if not pts_tw:
            raise SystemExit('no type-3 chest point on the chest layer nor in ' + TEAMWANTED)
        for o in pts_tw:
            s, e = mspc(o['body'])
            if o['body'][s + 16] != 0:
                raise SystemExit(f"{o['name']}: spawn byte (MSPC +16) {o['body'][s + 16]}, not ACR's working 0")
            nb = E.set_own_filter(o['body'], [LAYER])
            moved.append(with_body(o, nb, old=o))
        pats = {o['uid']: [bytes([1, kk]) + struct.pack('<I', o['uid']) for kk in (0, 1, 2)] for o in moved}
        hit = []
        for rname, p in files.items():
            for o in p.objects[1:]:
                hit += [f"{u:#x} in {rname} {o['name']}" for u, ps in pats.items() if u != o.get('uid') and any((x in o['body'] for x in ps))]
        if hit:
            raise SystemExit(f'{len(hit)} teamwanted point(s) named by another object: {hit[:4]}')
        report.append(f"chest points: {len(moved)} type-3 points moved from {TEAMWANTED} (ACB's dead teamwanted layer) into {BLOCK} on ACFE_Chest_Capture, body unchanged but the own filter (-> ACFE_Chest_Capture); spawn byte (MSPC +16) 0 as ACR's working chests")
    else:
        report.append(f"chest points: {len({o['uid'] for _r, o in pts_on})} on the chest layer already ({', '.join(sorted({r for r, _o in pts_on}))}), own filters naming it or empty")
    _blocks, areas = oob_modes.scan(files)
    on_chest = [a for a in areas if a['rec'] == BLOCK or (a['where'] == 'cell' and LAYER in a['layers'])]
    oob_area, oob_needs = (None, [])
    kit_uids = {o['uid'] for o in added if o['has_uid']}
    if on_chest:
        report.append(f"out of bounds: {', '.join(sorted({a['name'] for a in on_chest}))} on the chest layer already")
    else:
        main = [a for a in areas if a['rec'] == 'DataBlock_ACFE_Manhunt' and 'ACFE_Manhunt' in a['modes']]
        if not main:
            report.append('out of bounds: the map has no Manhunt out-of-bounds area (walled in: Palazzio, Villa) - none for Chest Capture either (chest_check WARN)')
        else:
            if len({a['uid'] for a in main}) != 1:
                raise SystemExit(f"no single Manhunt out-of-bounds area to copy ({[a['name'] for a in main]})")
            a = main[0]
            ent = next((o for o in files[a['rec']].objects if o['has_uid'] and o['uid'] == a['uid']))
            if not E.own_allows(ent['body'], LAYER):
                raise SystemExit(f"{a['name']}: its own DataLayerFilter leaves ACFE_Chest_Capture out (a copy with the same uid must keep its body)")
            root = oob_modes.root_cell(files)
            oob_needs = [o for o in oob_modes.closure(files, a['rec'], a['uid'], BLOCK, root) if o['uid'] != a['uid'] and o['uid'] not in kit_uids]
            oob_area = ent
            report.append(f"out of bounds: {a['name']} {a['uid']:#x} (the map's Manhunt area) copied into {BLOCK} on ACFE_Chest_Capture alone, body and uid unchanged (its own filter lets it in), with " + (', '.join((f"{o['name'] or hex(o['uid'])} ({xrefs.cls('AC3', o['type'])})" for o in oob_needs)) or 'nothing else') + f' (what the root cell {root} defines is not copied)')
    if old and (not moved) and (oob_area is None):
        head = cb.objects[0]
        hb = head['body']
        n = struct.unpack_from('<I', hb, 10)[0]
        lst = [struct.unpack_from('<I', hb, 14 + 6 * j + 2)[0] for j in range(n)]
        swap = {r['old']['uid']: r['uid'] for r in repl.values()}
        if not set(swap) <= set(lst):
            raise SystemExit("a replaced start is not in the block's list")
        hb2 = hb[:14] + b''.join((b'\x01\x00' + struct.pack('<I', swap.get(u, u)) for u in lst)) + hb[14 + 6 * n:]
        objs = [(head['serial'], mapwalk.object_bytes(head, hb2), head['filter'])]
        for i, o in enumerate(cb.objects[1:], 1):
            q = repl.get(i, o)
            objs.append((q['serial'], mapwalk.object_bytes(q), q['filter']))
        for o in added:
            objs.append((o['serial'], mapwalk.object_bytes(o), None))
        payloads[BLOCK] = V.payload_bytes(objs, cb.filter_table or b'')
        report.append(f'{BLOCK}: {len(repl)} starts replaced in place (list N = {n} unchanged), {len(added)} kit objects appended; {len(objs)} objects (was {len(cb.objects)})')
    elif old:
        raise SystemExit('a block-route map needing chest points or an out-of-bounds area: not a case this knows')
    else:
        table, fi = oob_modes.filter_index(cb.filter_table, {LAYER})
        add = [(r, fi) for r in sorted(repl.values(), key=lambda r: r['name'])]
        add += [(o, fi) for o in moved]
        if oob_area is not None:
            add.append((oob_area, fi))
        payloads[BLOCK] = oob_modes.rewrite(cb, (), add, list(added) + oob_needs, None if table is cb.filter_table else table)
        report.append(f"{BLOCK}: + {len(repl)} Scene'd starts" + (f' + {len(moved)} chest points' if moved else '') + (' + the out-of-bounds area' if oob_area is not None else '') + f' listed on ACFE_Chest_Capture, + {len(added)} kit objects' + (f' + {len(oob_needs)} area objects' if oob_needs else '') + ' unfiltered')
    cell_edits = {}
    for rname in sorted({r for r, _o, _l in cell_old}):
        p = files[rname]
        tb = p.filter_table
        drop, refilter, rebody = (set(), {}, {})
        for r_, o, lay in cell_old:
            if r_ != rname:
                continue
            rest = [u for u in lay if u != LAYER]
            if not rest:
                drop.add(o['uid'])
                continue
            tb, refilter[o['uid']] = oob_modes.filter_index(tb, set(rest))
            nb = own_minus(o['body'], LAYER, E.filter_lists(tb)[refilter[o['uid']]])
            if nb != o['body']:
                rebody[o['uid']] = nb
        q = p
        if rebody:

            class _P:
                pass
            q = _P()
            q.objects = [with_body(o, rebody[o['uid']]) if o['has_uid'] and o['uid'] in rebody else o for o in p.objects]
            q.filter_table = p.filter_table
        payloads[rname] = oob_modes.rewrite(q, drop, [], [], None if tb is p.filter_table else tb, refilter)
        cell_edits[rname] = (drop, refilter, rebody)
        report.append(f'{rname}: the {len(drop) + len(refilter)} scene-less starts off ACFE_Chest_Capture (directory and own filter): {len(refilter)} kept on their other layers ({len(rebody)} own filters edited), {len(drop)} removed')
    if moved:
        tw = files[TEAMWANTED]
        payloads[TEAMWANTED] = oob_modes.rewrite(tw, {o['uid'] for o in moved}, [], [])
        report.append(f'{TEAMWANTED}: the {len(moved)} points removed (list N {oob_modes.list_n(tw)} -> {oob_modes.list_n(tw) - len(moved)})')
    image = ArchiveImage.read(src)
    index = {p.rec.name: p.rec.index for p in files.values()}
    for rname, pl in payloads.items():
        set_payload(image, index[rname], pl)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    write(dest, image)
    return dict(changed=set(payloads), repl=repl, kit=added, new_uids=new_uids, defined=set(defined), imap=imap, route='block' if old else 'cells', oob=oob_area, oob_needs=oob_needs, cells=cell_edits, moved=moved, reused=reused)

def verify(src, dest, built, report):
    import aa_records
    import assoc_order
    import zones as Z
    ok = True
    r = C.check(dest)
    good = r['verdict'] in ('OK', 'WARN')
    ok &= good
    report.append(f"chest_check: {r['verdict']} points={r.get('points')}+{r.get('points_unfiltered')} kit={r.get('kit')} link={r.get('world_link')} starts={r.get('starts')} scenes={r.get('scenes')} respawns={r.get('respawns')} oob={r.get('oob')}" + ''.join((f'; {x}' for x in r['problems'] + r['warnings'])))
    e0, e1 = (E.check(src), E.check(dest))
    keys = ('verdict', 'problems', 'warnings', 'paths', 'nodes', 'chests', 'starts', 'scenes', 'respawns', 'oob')
    same_esc = all((e0.get(k) == e1.get(k) for k in keys))
    ok &= same_esc
    report.append(f"escort_check unchanged: {e1['verdict']} (was {e0['verdict']}): {same_esc}")
    d0, d1 = (dom_check.check(src), dom_check.check(dest))
    same_dom = (d0['verdict'], d0['problems'], d0['warnings']) == (d1['verdict'], d1['problems'], d1['warnings'])
    ok &= same_dom
    report.append(f"dom_check unchanged: {d1['verdict']} (was {d0['verdict']}): {same_dom}")
    files = oob_modes.load(dest)
    with Forge(src) as g, Forge(dest) as f:
        same = len(f.records) == len(g.records) and all((x.name == y.name and (x.name in built['changed'] or f.read(x) == g.read(y)) for x, y in zip(f.records, g.records)))
        before = {x.name: mapwalk.Payload(x, g.read(x)) for x in g.records if x.name in built['changed']}
    ok &= same
    report.append(f"every record but {', '.join(sorted(built['changed']))} byte-identical: {same}")
    ba, bb = (files[BLOCK], before[BLOCK])
    la = E.filter_lists(ba.filter_table) if ba.filter_table else []
    lb = E.filter_lists(bb.filter_table) if bb.filter_table else []
    imap = built['imap']
    if built['route'] == 'block':
        b_ok = ba.filter_table == bb.filter_table and len(ba.objects) == len(bb.objects) + len(built['kit'])
        eb = files.get(E.BLOCK)
        esc_by = {o['uid']: o['body'] for o in (eb.objects if eb else []) if o['has_uid']}
        for j, q in enumerate(bb.objects[1:], 1):
            o = ba.objects[j]
            if j in built['repl']:
                kq = spawn_layers.spawn_kind(q['body'])
                own = (E.own_filter(o['body']) or (0, 0, None))[2]
                b_ok &= o['body'][10:74] == q['body'][10:74] and la[o['filter']] == lb[q['filter']] and ('Scene' in EA.comps(o)) and (o['uid'] != q['uid']) and (own == la[o['filter']]) and (LAYER in own) and (spawn_layers.spawn_kind(o['body'])[0] == 2)
                if built['repl'][j]['reused']:
                    b_ok &= esc_by.get(o['uid']) == o['body']
                else:
                    b_ok &= spawn_layers.spawn_kind(o['body']) == (2, imap[kq[1]])
            else:
                b_ok &= o['body'] == q['body'] and o['filter'] == q['filter']
        b_ok &= all((o['filter'] is None for o in ba.objects[len(bb.objects):]))
        ok &= b_ok
        report.append(f"{BLOCK}: {len(built['repl'])} starts replaced one for one ({built['reused']} = the Escort block's start, same uid and bytes), transform and directory filter kept, own filter = it (naming ACFE_Chest_Capture), Scene'd, kit appended unfiltered, everything else unchanged: {b_ok}")
    else:
        by_uid = {o['uid']: o for o in ba.objects if o['has_uid']}
        old_same = all((by_uid.get(q['uid'], {}).get('body') == q['body'] for q in bb.objects[1:] if q['has_uid']))
        n2 = oob_modes.list_n(ba)
        listed = {struct.unpack_from('<I', ba.objects[0]['body'], 14 + 6 * k + 2)[0] for k in range(n2)}
        inst = {o['uid'] for o in ba.objects if o['filter'] is not None}
        want = {r['uid'] for r in built['repl'].values()} | {o['uid'] for o in built['moved']} | ({built['oob']['uid']} if built['oob'] else set())
        clones = [by_uid.get(r['uid']) for r in built['repl'].values()]
        c_ok = all((c is not None and 'Scene' in EA.comps(c) and (la[c['filter']] == [LAYER]) and ((E.own_filter(c['body']) or (0, 0, None))[2] == [LAYER]) and (spawn_layers.spawn_kind(c['body']) == (2, imap[spawn_layers.spawn_kind(r['old']['body'])[1]])) and (c['body'][10:74] == r['old']['body'][10:74]) for c, r in zip(clones, built['repl'].values())))
        m_ok = all((by_uid.get(o['uid']) is not None and la[by_uid[o['uid']]['filter']] == [LAYER] and (E.set_own_filter(o['old']['body'], [LAYER]) == by_uid[o['uid']]['body']) and ((E.own_filter(by_uid[o['uid']]['body']) or (0, 0, None))[2] == [LAYER]) for o in built['moved']))
        a_ok = not built['oob'] or (by_uid[built['oob']['uid']]['body'] == built['oob']['body'] and la[by_uid[built['oob']['uid']]['filter']] == [LAYER] and all((by_uid.get(o['uid'], {}).get('body') == o['body'] for o in built['oob_needs'])))
        k_ok = all((by_uid.get(o['uid'], {}).get('body') == o['body'] and by_uid[o['uid']]['filter'] is None for o in built['kit'] if o['has_uid']))
        b_ok = old_same and listed == inst == want | {q['uid'] for q in bb.objects[1:] if q['filter'] is not None} and c_ok and m_ok and a_ok and k_ok
        ok &= b_ok
        report.append(f"{BLOCK}: list == filtered instances = the {len(built['repl'])} clones" + (f" + {len(built['moved'])} points" if built['moved'] else '') + (' + the area' if built['oob'] else '') + f" {listed == inst}; clones Scene'd on ACFE_Chest_Capture alone (directory and own filter), index mapped {imap}, on the old starts' transforms {c_ok}; points = their old bodies with the own filter ACFE_Chest_Capture {m_ok}; area and its objects as the source's {a_ok}; kit unfiltered, same bytes {k_ok}: {b_ok}")
        for rname, (drop, refilter, rebody) in built['cells'].items():
            ca, cbf = (files[rname], before[rname])
            s_a = spawn_layers.filter_sets(ca.filter_table)
            s_b = spawn_layers.filter_sets(cbf.filter_table)
            rest = [q for q in cbf.objects if not (q['has_uid'] and q['uid'] in drop)]
            c2 = len(rest) == len(ca.objects)
            for o, q in zip(ca.objects[1:], rest[1:]):
                if o['has_uid'] and o['uid'] in refilter:
                    c2 &= o['body'] == rebody.get(o['uid'], q['body']) and s_a[o['filter']] == s_b[q['filter']] - {LAYER} and (LAYER not in (E.own_filter(o['body']) or (0, 0, []))[2])
                else:
                    c2 &= o['body'] == q['body'] and (o['filter'] is None) == (q['filter'] is None) and (o['filter'] is None or s_a[o['filter']] == s_b[q['filter']])
            ok &= c2
            report.append(f"{rname}: only the {len(refilter)} starts' filters (- ACFE_Chest_Capture, directory and own), {len(drop)} removed, everything else unchanged: {c2}")
    if built['moved']:
        ta, tb = (files[TEAMWANTED], before[TEAMWANTED])
        gone = {o['uid'] for o in built['moved']}
        rest = [q for q in tb.objects[1:] if not (q['has_uid'] and q['uid'] in gone)]
        t_ok = oob_modes.list_n(ta) == oob_modes.list_n(tb) - len(gone) and len(ta.objects) == len(rest) + 1 and all((o['body'] == q['body'] for o, q in zip(ta.objects[1:], rest)))
        ok &= t_ok
        report.append(f'{TEAMWANTED}: only the {len(gone)} points gone: {t_ok}')
    zn, zbad = Z.check_archive(dest)
    ok &= not zbad
    report.append(f'zones.py check: {zn} zone entities, problems {len(zbad)}')
    rec_ok, rec_problems, _notes, _one = aa_records.judge(aa_records.pieces(files), files)
    ok &= rec_ok
    report.append(f"aa_records (Artifact Assault untouched): {('PASS' if rec_ok else 'FAIL ' + '; '.join(rec_problems))}")
    oob_ok, oob_problems, oob_rows = oob_modes.judge(files)
    ok &= oob_ok
    report.append(f"oob_modes: {('PASS' if oob_ok else 'FAIL ' + '; '.join(oob_problems))}" + ''.join((f'; {x}' for x in oob_rows if x.startswith('ACFE_Chest_Capture'))))
    _rec, assoc = assoc_order.associations(dest)
    layers = [a for a, _b in assoc or []]
    a_ok = bool(layers) and layers == sorted(layers) and (LAYER in layers)
    ok &= a_ok
    report.append(f'WorldDataLayerManager: {len(layers)} associations in layer uid order, ACFE_Chest_Capture among them: {a_ok}')
    counts = {}
    for q in files.values():
        for o in q.objects:
            if o['has_uid']:
                counts[o['uid']] = counts.get(o['uid'], 0) + 1
    tops = [u for u in built['new_uids'] if u in counts]
    once = all((counts[u] == 1 for u in tops)) and (not any((u in built['defined'] for u in built['new_uids'])))
    unm = [hex(u) for u in built['new_uids'] if UNMAPPED[0] <= u < UNMAPPED[1]]
    ok &= once and (not unm)
    report.append(f"new uids: {len(built['new_uids'])} ({len(tops)} objects, the rest embedded)" + (f", {min(built['new_uids']):#x}-{max(built['new_uids']):#x}" if built['new_uids'] else '') + f", none the source defines, each object one body: {once}; in 0xF0000000-0xF7FFFFFF: {unm or 'none'}")
    return ok

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('map', nargs='?', help="a short name (galata, forli, ...) or the archive's file name")
    ap.add_argument('--name', help='build folder under out/modes/ (refused if it exists)')
    ap.add_argument('--base', help="build on this archive instead of the map's Escort build / the release's copy")
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
        archive = EA.SHORT.get(a.map.lower(), a.map)
        if a.base:
            src = dom_check.resolve(a.base)
        else:
            short = next((k for k, v in EA.SHORT.items() if v == archive and k not in ('constantinople', 'monteriggioni')), None)
            cand = os.path.join(OUT, ESCORT_BUILD.format(map=short), archive) if short else None
            src = cand if cand and os.path.exists(cand) else EA.release_source(archive)
        out_dir = os.path.join(OUT, a.name)
        if os.path.exists(out_dir):
            raise SystemExit(f'out/modes/{a.name} exists - pick another build name (builds are never overwritten)')
    dest = os.path.join(out_dir, archive)
    report = [f'{archive} -> {os.path.relpath(dest, ROOT)} (from {os.path.relpath(src, ROOT)})']
    try:
        built = build(archive, src, dest, report)
    except SystemExit:
        if os.path.isdir(out_dir) and (not os.listdir(out_dir)):
            os.rmdir(out_dir)
        raise
    ok = verify(src, dest, built, report)
    report.append(f"verify: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report_chest.txt' if a.src else 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print('\n'.join(report))
    if a.src:
        if not ok:
            raise SystemExit('verify FAILED')
        print(f'wrote {os.path.relpath(dest, ROOT)}')
    return 0 if ok else 1
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
