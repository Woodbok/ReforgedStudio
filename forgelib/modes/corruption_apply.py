"""forgelib/modes/corruption_apply.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import corruption_check as C
import dom_apply
import dom_check
import entity
import escort_apply
import escort_check as E
import inventory
import mapwalk
import oob_modes
import spawn_layers
import steal_check
import xrefs
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from mode_spawns import KIT_CLASSES
OUT = os.path.join(ROOT, 'out', 'modes')
OUT_MAPS = os.path.join(ROOT, 'out', 'maps')
BLOCK, CORRUPTION = (C.BLOCK, C.CORRUPTION)
ESCORT_BLOCK, ESCORT = (E.BLOCK, E.ESCORT)
CHEST_BLOCK = chest_check.BLOCK
UID_BASE, UID_SPAN, UID_END = (1534017536, 768, 1534033664)
UNMAPPED = (4026531840, 4160749568)
SHORT = escort_apply.SHORT
LAYOUTS = ('lowest8', 'acr')

def comps(o):
    return [n for _s, _e, n in entity.components(o['body']) or []]

def is_entity(o):
    return xrefs.cls('AC3', o['type']) == 'Entity'

def kind(o):
    return spawn_layers.spawn_kind(o['body']) if is_entity(o) else None

def slot_base(archive):
    archive = escort_apply.release_name(archive)
    if archive not in dom_apply.SLOTS:
        raise SystemExit(f'{archive}: no uid slot (dom_apply.SLOTS) - add one first')
    base = UID_BASE + dom_apply.SLOTS.index(archive) * UID_SPAN
    assert base + UID_SPAN <= UID_END
    return base

def kit_key(o):
    return o['uid'] if o['has_uid'] else (o['name'], len(o['body']))

def with_body(o, b, **kw):
    return dict(o, body=b, body_size=o['body_size'] + len(b) - len(o['body']), **kw)

def own_of(body):
    return (E.own_filter(body) or (0, 0, None))[2]

def layout_map(need, avail, layout):
    if layout == 'acr' or len(need) < 3:
        if all((i in avail for i in need)):
            return {i: i for i in need}
        if len(need) > len(avail):
            raise SystemExit(f'{len(need)} Corruption start indices {need}, templates for {avail} only')
        return dict(zip(need, avail))
    if len(need) - 1 > len(avail):
        raise SystemExit(f'{len(need)} Corruption start indices {need}, templates for {avail} only')
    m = {need[0]: avail[0], need[1]: avail[0]}
    free = [a for a in avail[1:]]
    for i in need[2:]:
        t = i if i in free else free[0]
        free.remove(t)
        m[i] = t
    return m

def own_minus(body, layer, fallback):
    f = E.own_filter(body)
    if f is None or layer not in f[2]:
        return body
    rest = [u for u in f[2] if u != layer]
    return E.set_own_filter(body, rest or list(fallback))

def build(archive, src, dest, report, layout='lowest8'):
    files = oob_modes.load(src)
    blk = files.get(BLOCK)
    if blk is None:
        raise SystemExit(f'{archive}: no {BLOCK}')
    blk_lists = E.filter_lists(blk.filter_table) if blk.filter_table else []
    eb = files.get(ESCORT_BLOCK)
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
    old = [(i, o) for i, o in enumerate(blk.objects) if i and kind(o) and (kind(o)[0] == 2) and (o['filter'] is None or CORRUPTION in blk_lists[o['filter']])]
    cell_old = []
    acb = []
    if old:
        route = 'block'
    else:
        for rname, p in files.items():
            if not rname.startswith('Cell'):
                continue
            lists = E.filter_lists(p.filter_table) if p.filter_table else []
            for o in p.objects[1:]:
                k = kind(o)
                lay = lists[o['filter']] if o['filter'] is not None and o['filter'] < len(lists) else None
                if k and k[0] == 2 and lay and (CORRUPTION in lay):
                    cell_old.append((rname, o, lay))
        if cell_old:
            route = 'cells'
        else:
            el = E.filter_lists(eb.filter_table) if eb is not None and eb.filter_table else []
            acb = [(i, o) for i, o in enumerate(eb.objects) if i and kind(o) and (kind(o)[0] == 2) and ('Scene' in comps(o)) and (o['filter'] is not None) and (el[o['filter']] == [ESCORT])] if eb is not None else []
            if not acb:
                raise SystemExit(f"no type-2 start on ACFE_Corruption ({BLOCK}, the cells) nor Scene'd Escort starts on ACFE_Escort alone to share")
            if any((own_of(o['body']) != [ESCORT] for _i, o in acb)):
                raise SystemExit("an Escort start's own filter is not [ACFE_Escort]")
            route = 'acb'
    starts = old if route == 'block' else [(None, o) for _r, o, _l in cell_old] if route == 'cells' else acb
    if route != 'acb' and any(('Scene' in comps(o) for _i, o in starts)):
        raise SystemExit("the Corruption layer already has Scene'd type-2 starts")
    tpl, kit, where = escort_apply.templates(files)
    need = sorted({kind(o)[1] for _i, o in starts})
    imap = layout_map(need, sorted(tpl), layout)
    esc = {}
    if route == 'block':
        for bn in (ESCORT_BLOCK, CHEST_BLOCK):
            p = files.get(bn)
            if p is None:
                continue
            pl = E.filter_lists(p.filter_table) if p.filter_table else []
            for o in p.objects[1:]:
                k = kind(o)
                if k and k[0] == 2 and ('Scene' in comps(o)) and (o['filter'] is not None):
                    lst = esc.setdefault(o['body'][10:74], [])
                    if all((q['uid'] != o['uid'] for q, _l in lst)):
                        lst.append((o, pl[o['filter']]))
    c_table, e_table = (blk.filter_table, eb.filter_table if eb is not None else None)
    if route == 'acb':
        e_table, e_fi = oob_modes.filter_index(eb.filter_table, {ESCORT, CORRUPTION})
        shared_own = E.filter_lists(e_table)[e_fi]
        c_table, c_fi_shared = oob_modes.filter_index(c_table, {ESCORT, CORRUPTION})
        if E.filter_lists(c_table)[c_fi_shared] != shared_own:
            raise SystemExit("the two blocks' {Escort, Corruption} filters list the layers in different orders")
    if route != 'block':
        c_table, c_fi = oob_modes.filter_index(c_table, {CORRUPTION})
    used, repl, kit_idx = ({}, {}, set())
    n_reused = n_shared = 0
    shared = {}
    for i, o in sorted(starts, key=lambda x: (kind(x[1])[1], x[1]['name'])):
        idx = kind(o)[1]
        ti = imap[idx]
        key = i if route != 'cells' else o['uid']
        if route == 'block':
            if o['filter'] is None:
                raise SystemExit(f"{o['name']}: an unfiltered type-2 start in {BLOCK}")
            want = blk_lists[o['filter']]
            if CORRUPTION not in want:
                raise SystemExit(f"{o['name']}: its directory filter leaves ACFE_Corruption out")
            m = [(c, lst) for c, lst in esc.get(o['body'][10:74], []) if lst == want and own_of(c['body']) == want and (kind(c) == (2, ti))]
            if len(m) == 1:
                c = m[0][0]
                repl[key] = dict(c, filter=o['filter'], old=o, how='reused')
                kit_idx.add(ti)
                n_reused += 1
                continue
            fi = o['filter']
        elif route == 'acb':
            if idx == ti:
                nb = E.set_own_filter(o['body'], shared_own)
                c = with_body(o, nb)
                shared[i] = c
                repl[key] = dict(c, filter=c_fi_shared, old=o, how='shared')
                kit_idx.add(ti)
                n_shared += 1
                continue
            want, fi = ([CORRUPTION], c_fi)
        else:
            want, fi = ([CORRUPTION], c_fi)
        k = used.get(ti, 0)
        used[ti] = k + 1
        t_o = tpl[ti][k % len(tpl[ti])]
        ren = {u: fresh() for u in V.embedded(t_o['body'])}
        b = V.remap(t_o['body'], ren)
        b = b[:10] + o['body'][10:74] + b[74:]
        b = E.set_own_filter(b, want)
        if spawn_layers.spawn_kind(b) != (2, ti) or 'Scene' not in [n for _s, _e, n in entity.components(b) or []]:
            raise SystemExit(f"{t_o['name']}: the clone is not a Scene'd index-{ti} start")
        if own_of(b) != want:
            raise SystemExit(f"{t_o['name']}: the clone's own DataLayerFilter did not take {want}")
        uid = struct.unpack_from('<I', b, 2)[0]
        kit_idx.add(ti)
        repl[key] = dict(with_body(t_o, b), uid=uid, serial=uid, name=f'Corruption_Team{ti}_Spawn_{k + 1:02d}', filter=fi, old=o, how='fresh')
    kit_blocks = {where[t] for t in kit_idx if t in where}
    kits = {key: o for key, (rname, o) in kit.items() if rname in kit_blocks}
    for rname in kit_blocks:
        mine = {kit_key(o): o['body'] for o in files[rname].objects if xrefs.cls('AC3', o['type']) in KIT_CLASSES}
        if any((kits[k]['body'] != b for k, b in mine.items() if k in kits)) or set(mine) != set(kits):
            raise SystemExit(f"the camera kit differs between {', '.join(sorted(kit_blocks))}")
    if len(kits) != 13:
        raise SystemExit(f"{len(kits)} camera-kit objects in {', '.join(sorted(kit_blocks))}, not 13")
    if n_reused or n_shared:
        ek = {o['uid']: o['body'] for o in eb.objects if o['has_uid'] and xrefs.cls('AC3', o['type']) in KIT_CLASSES}
        if any((ek.get(o['uid']) != o['body'] for o in kits.values() if o['has_uid'])):
            raise SystemExit("the Escort block's camera kit is not the template block's")
    have = {kit_key(o) for o in blk.objects}
    added = [o for k, o in kits.items() if k not in have]
    for o in added:
        for rname, d in defined.get(o['uid'], []) if o['has_uid'] else []:
            if xrefs.cls('AC3', d['type']) == xrefs.cls('AC3', o['type']) and d['body'] != o['body']:
                raise SystemExit(f"kit {o['name']} {o['uid']:#x}: {rname} defines it with other bytes")
    import archive_fixes
    ln = archive_fixes.layer_names('AC3')
    got = {}
    for r in repl.values():
        got[kind(r)[1]] = got.get(kind(r)[1], 0) + 1
    remap = '' if all((x == y for x, y in imap.items())) else ' (index ' + ', '.join((f'{x} -> {y}' for x, y in imap.items())) + ')'
    counts = {h: sum((r['how'] == h for r in repl.values())) for h in ('reused', 'shared', 'fresh')}
    owns = sorted({tuple(own_of(r['body'])) for r in repl.values()})
    report.append(f'route {route}, layout {layout}: {len(starts)} ' + {'block': f'scene-less ACR starts in {BLOCK}', 'cells': 'scene-less ACR starts in ' + ', '.join(sorted({r for r, _o, _l in cell_old})), 'acb': f"Scene'd Escort starts in {ESCORT_BLOCK} (the positions)"}[route] + f" (indices {dict(sorted(((i, sum((kind(o)[1] == i for _j, o in starts))) for i in need)))}){remap} -> Corruption starts {dict(sorted(got.items()))}: {counts['reused']} = the Scene'd Escort / Chest start on the same transform (same uid and bytes), {counts['shared']} Escort starts shared (directory and own filter -> Escort + Corruption), {counts['fresh']} fresh clones of " + ', '.join((f"index {t}: {tpl[t][0]['name']}.." for t in sorted(set(imap.values())))) + f" ({', '.join(sorted(kit_blocks))}); own filters: " + '; '.join(('[' + ', '.join((ln.get(u, f'{u:#x}') for u in t)) + ']' for t in owns)) + f'; camera kit {len(kits)} objects ({len(added)} appended)')
    payloads = {}
    oob_area, oob_needs = (None, [])
    if route != 'block':
        _blocks, areas = oob_modes.scan(files)
        on = [a for a in areas if a['rec'] == BLOCK or (a['where'] == 'cell' and CORRUPTION in a['layers'])]
        kit_uids = {o['uid'] for o in added if o['has_uid']}
        if on:
            report.append(f"out of bounds: {', '.join(sorted({a['name'] for a in on}))} on the Corruption layer already")
        else:
            main_ = [a for a in areas if a['rec'] == 'DataBlock_ACFE_Manhunt' and 'ACFE_Manhunt' in a['modes']]
            if not main_:
                report.append('out of bounds: the map has no Manhunt out-of-bounds area (walled in: Villa) - none for Corruption either (corruption_check WARN)')
            else:
                if len({a['uid'] for a in main_}) != 1:
                    raise SystemExit(f"no single Manhunt out-of-bounds area to copy ({[a['name'] for a in main_]})")
                a = main_[0]
                ent = next((o for o in files[a['rec']].objects if o['has_uid'] and o['uid'] == a['uid']))
                if not E.own_allows(ent['body'], CORRUPTION):
                    raise SystemExit(f"{a['name']}: its own DataLayerFilter leaves ACFE_Corruption out")
                root = oob_modes.root_cell(files)
                oob_needs = [o for o in oob_modes.closure(files, a['rec'], a['uid'], BLOCK, root) if o['uid'] != a['uid'] and o['uid'] not in kit_uids]
                oob_area = ent
                report.append(f"out of bounds: {a['name']} {a['uid']:#x} (the map's Manhunt area) copied into {BLOCK} on ACFE_Corruption alone, body and uid unchanged (its own filter lets it in), with {len(oob_needs)} objects it needs")
    if route == 'block':
        head = blk.objects[0]
        hb = head['body']
        n = struct.unpack_from('<I', hb, 10)[0]
        lst = [struct.unpack_from('<I', hb, 14 + 6 * j + 2)[0] for j in range(n)]
        swap = {r['old']['uid']: r['uid'] for r in repl.values()}
        if not set(swap) <= set(lst):
            raise SystemExit("a replaced start is not in the block's list")
        hb2 = hb[:14] + b''.join((b'\x01\x00' + struct.pack('<I', swap.get(u, u)) for u in lst)) + hb[14 + 6 * n:]
        objs = [(head['serial'], mapwalk.object_bytes(head, hb2), head['filter'])]
        for i, o in enumerate(blk.objects[1:], 1):
            q = repl.get(i, o)
            objs.append((q['serial'], mapwalk.object_bytes(q), q['filter']))
        for o in added:
            objs.append((o['serial'], mapwalk.object_bytes(o), None))
        payloads[BLOCK] = V.payload_bytes(objs, blk.filter_table or b'')
        report.append(f'{BLOCK}: {len(repl)} starts replaced in place (list N = {n} unchanged), {len(added)} kit objects appended; {len(objs)} objects (was {len(blk.objects)})')
    else:
        add = [(r, r['filter']) for r in sorted(repl.values(), key=lambda r: (r['how'] != 'shared', r['name']))]
        if oob_area is not None:
            add.append((oob_area, c_fi))
        payloads[BLOCK] = oob_modes.rewrite(blk, (), add, list(added) + oob_needs, None if c_table is blk.filter_table else c_table)
        report.append(f'{BLOCK}: + {len(repl)} starts' + (' + the out-of-bounds area' if oob_area is not None else '') + f' listed, + {len(added)} kit objects' + (f' + {len(oob_needs)} area objects' if oob_needs else '') + ' unfiltered')
    if shared:

        class _P:
            pass
        q = _P()
        q.objects = [shared.get(i, o) for i, o in enumerate(eb.objects)]
        q.filter_table = eb.filter_table
        payloads[ESCORT_BLOCK] = oob_modes.rewrite(q, (), [], [], None if e_table is eb.filter_table else e_table, {c['uid']: e_fi for c in shared.values()})
        report.append(f'{ESCORT_BLOCK}: the {len(shared)} shared starts on {{ACFE_Escort, ACFE_Corruption}} (directory filter and own filter), nothing else')
    cell_edits = {}
    for rname in sorted({r for r, _o, _l in cell_old}):
        p = files[rname]
        tb = p.filter_table
        drop, refilter, rebody = (set(), {}, {})
        for r_, o, lay in cell_old:
            if r_ != rname:
                continue
            rest = [u for u in lay if u != CORRUPTION]
            if not rest:
                drop.add(o['uid'])
                continue
            tb, refilter[o['uid']] = oob_modes.filter_index(tb, set(rest))
            nb = own_minus(o['body'], CORRUPTION, E.filter_lists(tb)[refilter[o['uid']]])
            if nb != o['body']:
                rebody[o['uid']] = nb
        if drop:
            pats = {u: [bytes([1, kk]) + struct.pack('<I', u) for kk in (0, 1, 2)] for u in drop}
            hit = [f"{u:#x} in {r2} {q['name']}" for r2, p2 in files.items() for q in p2.objects[1:] for u, ps in pats.items() if q.get('uid') != u and any((x in q['body'] for x in ps))]
            if hit:
                raise SystemExit(f'{len(hit)} removed cell start(s) named by another object: {hit[:4]}')
        q = p
        if rebody:

            class _Q:
                pass
            q = _Q()
            q.objects = [with_body(o, rebody[o['uid']]) if o['has_uid'] and o['uid'] in rebody else o for o in p.objects]
            q.filter_table = p.filter_table
        payloads[rname] = oob_modes.rewrite(q, drop, [], [], None if tb is p.filter_table else tb, refilter)
        cell_edits[rname] = (drop, refilter, rebody)
        report.append(f'{rname}: the {len(drop) + len(refilter)} scene-less starts off ACFE_Corruption (directory and own filter): {len(refilter)} kept on their other layers ({len(rebody)} own filters edited), {len(drop)} removed')
    image = ArchiveImage.read(src)
    index = {p.rec.name: p.rec.index for p in files.values()}
    for rname, pl in payloads.items():
        set_payload(image, index[rname], pl)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    write(dest, image)
    return dict(route=route, layout=layout, changed=set(payloads), repl=repl, kit=added, new_uids=new_uids, defined=set(defined), imap=imap, shared=shared, oob=oob_area, oob_needs=oob_needs, cells=cell_edits, starts=len(starts))

def verify(src, dest, built, report):
    import aa_records
    import assoc_order
    import zones as Z
    ok = True
    r0, r1 = (C.check(src), C.check(dest))
    good = r1['verdict'] in ('OK', 'WARN') and r0['verdict'] in ('FAIL', 'EMPTY')
    ok &= good
    report.append(f"corruption_check: {r1['verdict']} (was {r0['verdict']}): " + C.line(r1).split(None, 1)[1])
    keys = ('verdict', 'problems', 'warnings', 'paths', 'nodes', 'chests', 'starts', 'scenes', 'respawns', 'oob')
    for label, fn in (('escort_check', E.check), ('chest_check', chest_check.check), ('steal_check', steal_check.check), ('dom_check', dom_check.check)):
        a, b = (fn(src), fn(dest))
        same = all((a.get(k) == b.get(k) for k in keys)) if label != 'dom_check' else (a['verdict'], a['problems'], a['warnings']) == (b['verdict'], b['problems'], b['warnings'])
        ok &= same
        report.append(f"{label} unchanged: {b['verdict']} (was {a['verdict']}): {same}")
    files = oob_modes.load(dest)
    changed = built['changed']
    with Forge(src) as g, Forge(dest) as f:
        same = len(f.records) == len(g.records) and all((x.name == y.name and (x.name in changed or f.read(x) == g.read(y)) for x, y in zip(f.records, g.records)))
        before = {x.name: mapwalk.Payload(x, g.read(x)) for x in g.records if x.name in changed}
    ok &= same
    report.append(f"every record but {', '.join(sorted(changed))} byte-identical: {same}")
    ba, bb = (files[BLOCK], before[BLOCK])
    la = E.filter_lists(ba.filter_table) if ba.filter_table else []
    lb = E.filter_lists(bb.filter_table) if bb.filter_table else []
    by_a = {}
    for o in ba.objects[1:]:
        by_a.setdefault(kit_key(o), []).append(o)
    replaced = {r['old']['uid'] for r in built['repl'].values()} if built['route'] == 'block' else set()
    old_same = all((any((o['body'] == q['body'] and (o['filter'] is None) == (q['filter'] is None) and (o['filter'] is None or la[o['filter']] == lb[q['filter']]) for o in by_a.get(kit_key(q), []))) for q in bb.objects[1:] if not (q['has_uid'] and q['uid'] in replaced)))
    others = {}
    for bn in (ESCORT_BLOCK, CHEST_BLOCK):
        p = files.get(bn)
        for o in p.objects[1:] if p else []:
            if o['has_uid']:
                others[o['uid']] = o['body']
    c_ok, n_on = (True, 0)
    for r in built['repl'].values():
        o = by_a.get(r['uid'], [None])[0]
        old_o = r['old']
        lay = la[o['filter']] if o is not None and o['filter'] is not None else None
        c_ok &= o is not None and o['body'] == r['body'] and (lay is not None) and (CORRUPTION in lay) and (own_of(o['body']) == lay) and ('Scene' in comps(o)) and (spawn_layers.spawn_kind(o['body']) == (2, built['imap'][spawn_layers.spawn_kind(old_o['body'])[1]])) and (o['body'][10:74] == old_o['body'][10:74])
        if r['how'] in ('reused', 'shared'):
            c_ok &= others.get(r['uid']) == o['body']
        if built['route'] == 'block':
            c_ok &= lay == lb[old_o['filter']]
    for o in ba.objects[1:]:
        k = kind(o)
        if k and k[0] == 2 and (o['filter'] is None or CORRUPTION in la[o['filter']]):
            n_on += 1
    c_ok &= n_on == len(built['repl']) == built['starts']
    n2 = oob_modes.list_n(ba)
    listed = [struct.unpack_from('<I', ba.objects[0]['body'], 14 + 6 * k + 2)[0] for k in range(n2)]
    listed0 = [struct.unpack_from('<I', bb.objects[0]['body'], 14 + 6 * k + 2)[0] for k in range(oob_modes.list_n(bb))]
    if built['route'] == 'block':
        swap = {r['old']['uid']: r['uid'] for r in built['repl'].values()}
        list_ok = listed == [swap.get(u, u) for u in listed0]
    else:
        want = {r['uid'] for r in built['repl'].values()} | ({built['oob']['uid']} if built['oob'] else set())
        list_ok = set(listed) == set(listed0) | want and len(listed) == len(listed0) + len(want)
        if built['oob']:
            ao = by_a.get(built['oob']['uid'], [None])[0]
            list_ok &= ao is not None and ao['body'] == built['oob']['body'] and (la[ao['filter']] == [CORRUPTION]) and all((any((x['body'] == q['body'] for x in by_a.get(kit_key(q), []))) for q in built['oob_needs']))
    k_ok = all((any((x['body'] == o['body'] and x['filter'] is None for x in by_a.get(kit_key(o), []))) for o in built['kit']))
    b_ok = old_same and c_ok and list_ok and k_ok
    ok &= b_ok
    report.append(f"{BLOCK}: other objects byte-identical with their filters {old_same}; the {len(built['repl'])} starts Scene'd type-2 of their layout index on their sources' transforms, own filter = directory filter naming Corruption, reused / shared = the Escort / Chest blocks' bytes, the only type-2 starts on the layer {c_ok}; list {list_ok}; kit = the template block's, unfiltered {k_ok}: {b_ok}")
    if built['shared']:
        ea, eb_ = (files[ESCORT_BLOCK], before[ESCORT_BLOCK])
        sa = E.filter_lists(ea.filter_table)
        sb = E.filter_lists(eb_.filter_table)
        su = {c['uid'] for c in built['shared'].values()}
        e_ok = len(ea.objects) == len(eb_.objects) and ea.objects[0]['body'] == eb_.objects[0]['body']
        for o, q in zip(ea.objects[1:], eb_.objects[1:]):
            if o['has_uid'] and o['uid'] in su:
                e_ok &= o['body'] == E.set_own_filter(q['body'], sa[o['filter']]) and sorted(sa[o['filter']]) == sorted(sb[q['filter']] + [CORRUPTION]) and (own_of(o['body']) == sa[o['filter']])
            else:
                e_ok &= o['body'] == q['body'] and (o['filter'] is None) == (q['filter'] is None) and (o['filter'] is None or sa[o['filter']] == sb[q['filter']])
        ok &= e_ok
        report.append(f"{ESCORT_BLOCK}: only the {len(su)} shared starts' directory + own filter (+ ACFE_Corruption): {e_ok}")
    for rname, (drop, refilter, rebody) in built['cells'].items():
        ca, cbf = (files[rname], before[rname])
        s_a = spawn_layers.filter_sets(ca.filter_table)
        s_b = spawn_layers.filter_sets(cbf.filter_table)
        rest = [q for q in cbf.objects if not (q['has_uid'] and q['uid'] in drop)]
        c2 = len(rest) == len(ca.objects)
        for o, q in zip(ca.objects[1:], rest[1:]):
            if o['has_uid'] and o['uid'] in refilter:
                c2 &= o['body'] == rebody.get(o['uid'], q['body']) and s_a[o['filter']] == s_b[q['filter']] - {CORRUPTION} and (CORRUPTION not in (own_of(o['body']) or []))
            else:
                c2 &= o['body'] == q['body'] and (o['filter'] is None) == (q['filter'] is None) and (o['filter'] is None or s_a[o['filter']] == s_b[q['filter']])
        ok &= c2
        report.append(f"{rname}: only the {len(refilter)} starts' filters (- ACFE_Corruption), {len(drop)} removed: {c2}")
    oob_ok, oob_problems, oob_rows = oob_modes.judge(files)
    ok &= oob_ok
    report.append(f"oob_modes: {('PASS' if oob_ok else 'FAIL ' + '; '.join(oob_problems))}" + ''.join((f'; {x}' for x in oob_rows if x.startswith('ACFE_Corruption'))))
    zn, zbad = Z.check_archive(dest)
    ok &= not zbad
    report.append(f'zones.py check: {zn} zone entities, problems {len(zbad)}')
    rec_ok, rec_problems, _n, _one = aa_records.judge(aa_records.pieces(files), files)
    ok &= rec_ok
    report.append(f"aa_records (Artifact Assault untouched): {('PASS' if rec_ok else 'FAIL ' + '; '.join(rec_problems))}")
    _rec, assoc = assoc_order.associations(dest)
    layers = [a for a, _b in assoc or []]
    a_ok = bool(layers) and layers == sorted(layers) and (CORRUPTION in layers)
    ok &= a_ok
    report.append(f'WorldDataLayerManager: {len(layers)} associations in layer uid order, ACFE_Corruption among them: {a_ok}')
    counts = {}
    for q in files.values():
        for o in q.objects:
            if o['has_uid']:
                counts[o['uid']] = counts.get(o['uid'], 0) + 1
    tops = [u for u in built['new_uids'] if u in counts]
    once = all((counts[u] == 1 for u in tops)) and (not any((u in built['defined'] for u in built['new_uids'])))
    unm = [hex(u) for u in built['new_uids'] if UNMAPPED[0] <= u < UNMAPPED[1]]
    n_fresh = sum((r['how'] == 'fresh' for r in built['repl'].values()))
    ok &= once and (not unm) and (len(tops) == n_fresh)
    report.append(f"new uids: {len(built['new_uids'])} ({len(tops)} objects, the rest embedded)" + (f", {min(built['new_uids']):#x}-{max(built['new_uids']):#x}" if built['new_uids'] else '') + f", none the source defines, each object one body: {once}; in 0xF0000000-0xF7FFFFFF: {unm or 'none'}")
    return ok

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('map', nargs='?', help="a short name (galata, forli, ...) or the archive's file name")
    ap.add_argument('--name', help='build folder under out/modes/ (refused if it exists)')
    ap.add_argument('--base', help="build on this archive instead of the release's copy")
    ap.add_argument('--layout', choices=LAYOUTS, default='lowest8', help="lowest8 (default): 8+ starts on the lowest index; acr: ACR's 4 x 4 grouping (CR1's Galata)")
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
        built = build(archive, src, dest, report, layout=a.layout)
    except SystemExit:
        if os.path.isdir(out_dir) and (not os.listdir(out_dir)):
            os.rmdir(out_dir)
        raise
    ok = verify(src, dest, built, report)
    report.append(f"verify: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report_corruption.txt' if a.src else 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print('\n'.join(report))
    if a.src:
        if not ok:
            raise SystemExit('verify FAILED')
        print(f'wrote {os.path.relpath(dest, ROOT)}')
    return 0 if ok else 1
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
