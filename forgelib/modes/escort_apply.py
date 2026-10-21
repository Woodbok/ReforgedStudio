"""forgelib/modes/escort_apply.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'maps', 'port'), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import aa_map
import aa_variant as V
import aa_zones as AZ
import dom_apply
import dom_check
import entity
import escort_check as E
import inventory
import mapwalk
import oob_modes
import paths
import spawn_layers
import xrefs
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from mode_spawns import KIT_CLASSES
OUT = os.path.join(ROOT, 'out', 'modes')
BLOCK = E.BLOCK
UID_BASE, UID_SPAN, UID_END = (1533100032, 768, 1533116416)
UNMAPPED = (4026531840, 4160749568)
SLOTS = dom_apply.SLOTS
SHORT = {'souk': 'DataPC_ACFE_Souk.forge', 'galata': 'DataPC_ACFE_Constantinople.forge', 'constantinople': 'DataPC_ACFE_Constantinople.forge', 'antioch': 'DataPC_ACFE_Antioch.forge', 'jerusalem': 'DataPC_ACFE_Jerusalem.forge', 'dyers': 'DataPC_ACFE_Dyers.forge', 'imperial': 'DataPC_ACFE_Imperial.forge', 'juderia': 'DataPC_ACFE_Juderia.forge', 'rhodes': 'DataPC_ACFE_Rhodes.forge', 'siena': 'DataPC_AC2MP_Siena.forge', 'palazzio': 'DataPC_AC2MP_Palazzio.forge', 'firenze': 'DataPC_AC2MP_Firenze.forge', 'mtstmichel': 'DataPC_AC2MP_MtStMichel.forge', 'rome': 'DataPC_ACR_Rome_Multi.forge', 'sandonato': 'DataPC_AC2MP_SanDonato.forge', 'sanmarco': 'DataPC_AC2MP_SanMarco.forge', 'forli': 'DataPC_AC2MP_Forli.forge', 'alhambra': 'DataPC_AC2MP_Alhambra.forge', 'villa': 'DataPC_AC2MP_Villa.forge', 'monteriggioni': 'DataPC_AC2MP_Villa.forge', 'pienza': 'DataPC_AC2MP_Pienza.forge'}
ACB_DLC = 'DataPC_skins_0002_00000004_dlc.forge'
ACB_TEAMVIP = {'DataPC_AC2MP_Forli.forge': 1688011185, 'DataPC_AC2MP_Alhambra.forge': 1339656153, 'DataPC_AC2MP_Villa.forge': 1349357116, 'DataPC_AC2MP_Pienza.forge': 1688012459}
AWD_TEAMVIP = 960280977
ACR_NAMES = {'DataPC_ACFE_Dyers.forge': 'DataPC_ACFE_Dyers_dlc.forge', 'DataPC_ACFE_Imperial.forge': 'DataPC_ACFE_Imperial_dlc.forge', 'DataPC_ACFE_Jerusalem.forge': 'DataPC_ACFE_Jerusalem_dlc.forge', 'DataPC_AC2MP_Siena.forge': 'DataPC_AC2MP_Siena_dlc.forge', 'DataPC_AC2MP_Firenze.forge': 'DataPC_AC2MP_Firenze_dlc.forge', 'DataPC_AC2MP_SanDonato.forge': 'DataPC_AC2MP_SanDonato_dlc.forge'}
TEMPLATE_BLOCKS = ('DataBlock_ACFE_Manhunt', 'DataBlock_ACFE_CTF_2')
PLANTATION = 'DataPC_AC3MP_Plantation.forge'

def release_source(archive):
    with open(os.path.join(ROOT, 'release_set.json'), encoding='utf-8') as fh:
        for f in json.load(fh)['files']:
            if os.path.basename(f['dest']) == archive:
                return dom_check.resolve(f['src'])
    raise SystemExit(f'{archive} is not in release_set.json (give --base)')

def slot_base(archive):
    i = SLOTS.index(archive) if archive in SLOTS else 19 + sum(archive.encode()) % 2
    base = UID_BASE + i * UID_SPAN
    assert base + UID_SPAN <= UID_END
    return base

def base_objects(prefixes):
    import parallel_read
    out = set()
    pats = sorted(prefixes)
    classes = {struct.pack('<I', c) for c in AZ.CLASS_IDS}
    for arch in (paths.DEFAULT_DATAPC, paths.DEFAULT_EXTRA):
        st = os.stat(arch)
        key = os.path.join(aa_map.CACHE, f"esc_uids.{os.path.basename(arch)}.{st.st_size}.{int(st.st_mtime)}.{'-'.join((f'{p:04x}' for p in pats))}.json")
        if os.path.exists(key):
            with open(key, encoding='utf-8') as fh:
                out |= set(json.load(fh))
            continue
        found = set()
        with Forge(arch) as f:
            records = [r for r in f.records if r.size]
        for part in parallel_read.map_records(arch, records, _base_uids, [(pats, classes)] * len(records)):
            found |= part
        os.makedirs(aa_map.CACHE, exist_ok=True)
        with open(key, 'w', encoding='utf-8') as fh:
            json.dump(sorted(found), fh)
        out |= found
    return out

def _base_uids(payload, pats, classes):
    from datafile import decompress
    found = set()
    try:
        d = decompress(mapwalk.split_prefix(payload)[1])
    except Exception:
        return found
    for pfx in pats:
        pat = struct.pack('<H', pfx)
        k = d.find(pat, 2)
        while k >= 0:
            if d[k + 2:k + 6] in classes:
                found.add(struct.unpack_from('<I', d, k - 2)[0])
            k = d.find(pat, k + 1)
    return found

def acb_multi():
    return os.path.join(os.path.dirname(os.path.dirname(paths.AC3)), "Assassin's Creed Brotherhood", 'multi')

def source_lists(archive, src_path):
    if archive in ACB_TEAMVIP:
        uid = ACB_TEAMVIP[archive]
        body = None
        with Forge(src_path) as f:
            for r in f.records:
                if not r.size or r.name == 'GlobalMetaFile':
                    continue
                try:
                    p = mapwalk.Payload(r, f.read(r))
                except Exception:
                    continue
                o = next((o for o in p.objects if o['type'] == AWD_TEAMVIP and o.get('has_uid') and (o['uid'] == uid)), None)
                if o is not None:
                    body = o['body']
                    break
        if body is None:
            raise SystemExit(f'{os.path.basename(src_path)}: no AdditionalWorldData_TeamVIP {uid:#x}')
        tail = b'\x00\x00\x00\x00' + body[10:]
        return (E._lists(tail, 0), tail, f"ACB's AdditionalWorldData_TeamVIP {uid:#x} ({os.path.basename(src_path)})")
    abody, _arec = E.world_of(src_path)
    at = E.parse_tail(abody)
    return (at, abody[at['start']:] if at['start'] is not None else b'', f"ACR's World tail ({os.path.basename(src_path)})")

def comps(o):
    return [n for _s, _e, n in entity.components(o['body']) or []]

def is_entity(o):
    return xrefs.cls('AC3', o['type']) == 'Entity'

def templates(files):

    def scan(blocks):
        by, kit, where = ({}, {}, {})
        for rname, p in blocks:
            for o in p.objects[1:]:
                if is_entity(o):
                    k = spawn_layers.spawn_kind(o['body'])
                    if k and k[0] == 2 and ('Scene' in comps(o)) and (k[1] not in where):
                        by.setdefault(k[1], []).append(o)
                elif xrefs.cls('AC3', o['type']) in KIT_CLASSES:
                    kit.setdefault(o['uid'] if o['has_uid'] else (o['name'], len(o['body'])), (rname, o))
            for i in by:
                where.setdefault(i, rname)
        return (by, kit, where)
    by, kit, where = scan([(n, files[n]) for n in TEMPLATE_BLOCKS if n in files])
    return (by, kit, where)

def build(archive, src, acr, dest, report, keep_chests=True):
    files = oob_modes.load(src)
    blk = files.get(BLOCK)
    if blk is None:
        raise SystemExit(f'no {BLOCK} in {archive}')
    wrec = next((n for n, p in files.items() if p.rec.index == 1))
    wp = files[wrec]
    wi = next((i for i, o in enumerate(wp.objects) if o['type'] == E.WORLD_CLASS))
    wbody = wp.objects[wi]['body']
    t = E.parse_tail(wbody)
    at, acr_tail, label = source_lists(archive, acr)
    if at['error'] or not at['paths']:
        raise SystemExit(f"{label}: no readable path list ({at['error']})")
    prelisted = not t['error'] and bool(t['paths']) and (wbody[t['start']:] == acr_tail)
    if not prelisted and (t['error'] or t['chests'] or t['paths'] or (t['end'] != len(wbody)) or (t['start'] + 8 != len(wbody))):
        raise SystemExit(f"{archive}: the World's tail is neither AC3's empty 8-byte ending nor ACR's lists ({t['error'] or 'other lists present'})")
    chests = at['chests'] if keep_chests else []
    if keep_chests and E.tail_bytes(chests, at['paths']) != acr_tail:
        raise SystemExit(f'{label}: the lists do not round-trip through escort_check.parse_tail / tail_bytes')
    if prelisted:
        report.append("the World carries ACR's lists already (world.KEEP_ESCORT_LISTS in the chain): kept unless a uid needs renewing")
    report.append(f"{label}: {len(at['chests'])} chests, {len(at['paths'])} paths, {sum((len(p['nodes']) for p in at['paths']))} nodes, {len(acr_tail)} bytes" + ('' if keep_chests else '; chest list written empty (--no-chests)'))
    defined, present = ({}, set())
    refs = {ref for p in at['paths'] for _n, ref, _a, _b in p['nodes']}
    own = {p['uid'] for p in at['paths']} | {n for p in at['paths'] for n, *_r in p['nodes']}
    packed = {struct.pack('<I', u): u for u in own}
    for rname, p in files.items():
        sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
        for o in p.objects:
            if prelisted and rname == wrec and (o['type'] == E.WORLD_CLASS):
                continue
            for pk, u in packed.items():
                if pk in o['body']:
                    present.add(u)
            if o['has_uid']:
                lay = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else None
                defined.setdefault(o['uid'], []).append((rname, o, lay))
    bad = []
    for ref in sorted(refs):
        d = defined.get(ref, [])
        ok = [(r, o) for r, o, lay in d if is_entity(o) and 'CrowdFlow' in comps(o) and (r.startswith('Cell') or r == BLOCK) and (lay is None or E.ESCORT in lay)]
        if not ok:
            bad.append(f"""{ref:#x} ({'; '.join((f"{r} {o['name']}" for r, o, _l in d)) or 'nowhere'})""")
    if bad:
        raise SystemExit(f"{len(bad)} node ref(s) not a resident CrowdFlow entity of the archive: {', '.join(bad[:5])}")
    cells = sorted({r for ref in refs for r, _o, _l in defined[ref]})
    report.append(f"node refs: {len(refs)} distinct CrowdFlow entities, each defined in a cell, unfiltered or on Escort ({len(cells)} cells: {', '.join(cells[:4])}{(' ...' if len(cells) > 4 else '')})")
    if keep_chests:
        cd = [c for c in set(chests) if c in defined]
        report.append(f"chest refs: {len(set(chests))} ChestSpawn uids, {len(cd)} defined in the archive ({', '.join(sorted({r for c in cd for r, _o, _l in defined[c]}))})")
    clash = sorted((u for u in own if u in defined or u in present or u in inventory.universe('AC3') or (UNMAPPED[0] <= u < UNMAPPED[1])))
    base = slot_base(archive)
    in_base = base_objects({u >> 16 for u in own} | {base >> 16})
    based = sorted(in_base & own)
    taken = set(inventory.universe('AC3')) | set(inventory.universe('ACR')) | set(defined) | own
    taken |= in_base
    nxt = [base]
    new_uids = []

    def fresh():
        while nxt[0] in taken:
            nxt[0] += 1
        if nxt[0] >= base + UID_SPAN:
            raise SystemExit("the map's uid slot is used up")
        taken.add(nxt[0])
        new_uids.append(nxt[0])
        return nxt[0]
    renew = {u: fresh() for u in sorted(set(clash) | set(based))}
    paths_ = [dict(uid=renew.get(p['uid'], p['uid']), nodes=[(renew.get(nu, nu), ref, a_, b_) for nu, ref, a_, b_ in p['nodes']]) for p in at['paths']]
    tail = E.tail_bytes(chests, paths_)
    why = {u: "in the default set's DataPC / DataPC_extra" if u in based else 'in the archive, AC3 or the unmapped range' for u in renew}
    report.append(f"path / node object uids: {len(own)} ({('ACB' if archive in ACB_TEAMVIP else 'ACR')}'s), " + ('; '.join((f'{a:#x} -> {b:#x} (taken {why[a]})' for a, b in renew.items())) + '; the rest ' if renew else '') + "none defined or named in the archive, in AC3, in the default set's DataPC / DataPC_extra, or in 0xF0000000-0xF7FFFFFF")
    tpl, kit, where = templates(files)
    old = [(i, o) for i, o in enumerate(blk.objects) if i and is_entity(o) and ((spawn_layers.spawn_kind(o['body']) or (9,))[0] == 2)]
    cell_old = []
    if not old:
        for rname, p in files.items():
            if not rname.startswith('Cell'):
                continue
            sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
            for o in p.objects[1:]:
                k = spawn_layers.spawn_kind(o['body']) if is_entity(o) else None
                lay = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else None
                if k and k[0] == 2 and lay and (E.ESCORT in lay):
                    cell_old.append((rname, o, frozenset(lay)))
        if not cell_old:
            raise SystemExit(f'{BLOCK} has no team start (type 2), nor does any cell on ACFE_Escort')
    starts = old if old else [(None, o) for _r, o, _l in cell_old]
    if any(('Scene' in comps(o) for _i, o in starts)):
        raise SystemExit("the Escort layer already has Scene'd team starts")
    need = sorted({spawn_layers.spawn_kind(o['body'])[1] for _i, o in starts})
    if all((i in tpl for i in need)):
        imap = {i: i for i in need}
    else:
        avail = sorted(tpl)
        if len(need) > len(avail):
            raise SystemExit(f'{len(need)} Escort team indices {need}, templates for {avail} only')
        imap = dict(zip(need, avail))
    kit_blocks = {where[imap[i]] for i in need}
    kits = {}
    for key, (rname, o) in kit.items():
        if rname in kit_blocks:
            kits[key] = o
    for rname in kit_blocks:
        mine = {o['uid'] if o['has_uid'] else (o['name'], len(o['body'])): o['body'] for o in files[rname].objects if xrefs.cls('AC3', o['type']) in KIT_CLASSES}
        if any((kits[k]['body'] != b for k, b in mine.items() if k in kits)) or set(mine) != set(kits):
            raise SystemExit(f"the camera kit differs between {', '.join(sorted(kit_blocks))}")
    used = {}
    repl = {}
    blk_lists = E.filter_lists(blk.filter_table)
    for i, o in sorted(starts, key=lambda x: (spawn_layers.spawn_kind(x[1]['body'])[1], x[1]['name'])):
        idx = spawn_layers.spawn_kind(o['body'])[1]
        ti = imap[idx]
        k = used.get(idx, 0)
        used[idx] = k + 1
        t_o = tpl[ti][k % len(tpl[ti])]
        ren = {u: fresh() for u in V.embedded(t_o['body'])}
        b = V.remap(t_o['body'], ren)
        b = b[:10] + o['body'][10:74] + b[74:]
        own = blk_lists[o['filter']] if old else [E.ESCORT]
        if E.ESCORT not in own:
            raise SystemExit(f"{o['name']}: its directory filter leaves ACFE_Escort out")
        b = E.set_own_filter(b, own)
        if spawn_layers.spawn_kind(b) != (2, ti) or 'Scene' not in [n for _s, _e, n in entity.components(b) or []]:
            raise SystemExit(f"{t_o['name']}: the clone is not a Scene'd index-{ti} start")
        if (E.own_filter(b) or (0, 0, None))[2] != own:
            raise SystemExit(f"{t_o['name']}: the clone's own DataLayerFilter did not take {own}")
        uid = struct.unpack_from('<I', b, 2)[0]
        repl[i if old else o['uid']] = dict(t_o, body=b, body_size=t_o['body_size'] + len(b) - len(t_o['body']), uid=uid, serial=uid, name=f'Escort_Team{ti}_Spawn_{k + 1:02d}', filter=o['filter'], old=o)
    remap = '' if all((x == y for x, y in imap.items())) else ' (team index ' + ', '.join((f'{x} -> {y}' for x, y in imap.items())) + ')'
    cells_txt = ', in ' + ', '.join(sorted({r for r, _o, _l in cell_old})) if cell_old else ''
    from_txt = ', '.join((f"index {imap[i]}: {tpl[imap[i]][0]['name']}.. ({where[imap[i]]})" for i in need))
    report.append(f"team starts: {len(starts)} scene-less {('ACB (acb_stage0)' if archive in ACB_TEAMVIP else 'ACR')} starts (indices {dict(sorted(used.items()))}{cells_txt}) -> Scene'd AC3 starts{remap} cloned from {from_txt}, fresh uids, each on its ACR start's transform, " + ('same filter' if old else f'in {BLOCK} on ACFE_Escort alone') + " (each clone's own DataLayerFilter = that filter's layers, not the template's)" + f"; camera kit {len(kits)} objects from {', '.join(sorted(kit_blocks))} (same uids and bytes)")
    have = {o['uid'] if o['has_uid'] else (o['name'], len(o['body'])) for o in blk.objects}
    added = [o for k, o in kits.items() if k not in have]
    for o in added:
        for rname, d, _lay in defined.get(o['uid'], []) if o['has_uid'] else []:
            if xrefs.cls('AC3', d['type']) == xrefs.cls('AC3', o['type']) and d['body'] != o['body']:
                raise SystemExit(f"kit {o['name']} {o['uid']:#x}: {rname} defines it with other bytes")
    oob_area, oob_needs, cell_edits = (None, [], {})
    if old:
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
        payloads = {BLOCK: V.payload_bytes(objs, blk.filter_table or b'')}
        report.append(f'{BLOCK}: {len(repl)} starts replaced in place (list N = {n} unchanged), {len(added)} kit objects appended; {len(objs)} objects (was {len(blk.objects)})')
    else:
        table, fi = oob_modes.filter_index(blk.filter_table, {E.ESCORT})
        add = [(r, fi) for r in sorted(repl.values(), key=lambda r: r['name'])]
        _blocks, areas = oob_modes.scan(files)
        if not any((a['modes'] for a in areas)):
            report.append('out of bounds: the map has no out-of-bounds area in any mode (walled in) - none for Escort either (escort_check WARN, as Palazzio)')
        elif not any(('ACFE_Escort' in a['modes'] for a in areas)):
            main = [a for a in areas if a['rec'] == 'DataBlock_ACFE_Manhunt' and 'ACFE_Manhunt' in a['modes']]
            if len({a['uid'] for a in main}) != 1:
                raise SystemExit(f"no single Manhunt out-of-bounds area to copy for Escort ({[a['name'] for a in main]})")
            a = main[0]
            ent = next((o for o in files[a['rec']].objects if o['has_uid'] and o['uid'] == a['uid']))
            root = oob_modes.root_cell(files)
            kit_uids = {o['uid'] for o in added if o['has_uid']}
            oob_needs = [o for o in oob_modes.closure(files, a['rec'], a['uid'], BLOCK, root) if o['uid'] != a['uid'] and o['uid'] not in kit_uids]
            oob_area = ent
            add.append((ent, fi))
            report.append(f"out of bounds: {a['name']} {a['uid']:#x} (the map's Manhunt area, {a['rec']}) copied into {BLOCK} on ACFE_Escort alone, body and uid unchanged, with " + (', '.join((f"{o['name'] or hex(o['uid'])} ({xrefs.cls('AC3', o['type'])})" for o in oob_needs)) or 'nothing else') + f' (what the root cell {root} defines is not copied)')
        payloads = {BLOCK: oob_modes.rewrite(blk, (), add, list(added) + oob_needs, None if table is blk.filter_table else table)}
        report.append(f"{BLOCK}: + {len(repl)} Scene'd starts" + (' + the out-of-bounds area' if oob_area else '') + f' listed (N, M + {len(add)}), + {len(added)} kit objects' + (f' + {len(oob_needs)} area objects' if oob_needs else '') + ' unfiltered')
        for rname in sorted({r for r, _o, _l in cell_old}):
            p = files[rname]
            tb = p.filter_table
            drop, refilter = (set(), {})
            for r_, o, lay in cell_old:
                if r_ != rname:
                    continue
                rest = lay - {E.ESCORT}
                if not rest:
                    drop.add(o['uid'])
                else:
                    tb, refilter[o['uid']] = oob_modes.filter_index(tb, rest)
            payloads[rname] = oob_modes.rewrite(p, drop, [], [], None if tb is p.filter_table else tb, refilter)
            cell_edits[rname] = (drop, refilter)
            report.append(f'{rname}: the {len(drop) + len(refilter)} scene-less Escort starts off ACFE_Escort: {len(refilter)} kept on their other layers, {len(drop)} removed')
    nb = wbody[:t['start']] + tail
    if nb != wbody:
        wobjs = []
        for j, o in enumerate(wp.objects):
            wobjs.append((o['serial'], mapwalk.object_bytes(o, nb if j == wi else None), o['filter']))
        payloads[wrec] = V.payload_bytes(wobjs, wp.filter_table)
        report.append(f'{wrec}: World body {len(wbody)} -> {len(nb)} bytes (' + ("ACR's lists, uids renewed" if prelisted else "AC3's 8-byte ending -> " + ("ACB's" if archive in ACB_TEAMVIP else "ACR's")) + f' {len(tail)}-byte chest + path lists)')
    else:
        report.append(f"{wrec}: World unchanged (ACR's lists already there)")
    image = ArchiveImage.read(src)
    index = {p.rec.name: p.rec.index for p in files.values()}
    for rname, pl in payloads.items():
        set_payload(image, index[rname], pl)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    write(dest, image)
    return dict(changed=set(payloads), world=wrec, world_i=wi, tail=tail, tail_start=t['start'], repl=repl, kit=added, new_uids=new_uids, defined=set(defined), imap=imap, route='block' if old else 'cells', oob=oob_area, oob_needs=oob_needs, cells=cell_edits, renew=renew)

def verify(src, dest, built, report):
    import aa_records
    import zones as Z
    ok = True
    r = E.check(dest)
    good = r['verdict'] in ('OK', 'WARN')
    ok &= good
    report.append(f"escort_check: {r['verdict']} paths={r.get('paths')} nodes={r.get('nodes')} chests={r.get('chests')} crowdflow={r.get('crowdflow')} starts={r.get('starts')} scenes={r.get('scenes')} respawns={r.get('respawns')} oob={r.get('oob')}" + ''.join((f'; {x}' for x in r['problems'] + r['warnings'])))
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
    wi = built['world_i']
    if built['world'] not in built['changed']:
        wbody2 = E.world_of(dest)[0]
        tt = E.parse_tail(wbody2)
        w_ok = not tt['error'] and wbody2[tt['start']:] == built['tail']
        report.append(f'World record unchanged, its tail = the lists wanted: {w_ok}')
        wa = wb = None
    else:
        wa, wb = (files[built['world']], before[built['world']])
    w_ok = wa is None or (len(wa.objects) == len(wb.objects) and wa.filter_table == wb.filter_table and all((o['body'] == q['body'] and o['filter'] == q['filter'] for j, (o, q) in enumerate(zip(wa.objects, wb.objects)) if j != wi)) and (wa.objects[wi]['body'][:built['tail_start']] == wb.objects[wi]['body'][:built['tail_start']]) and (wa.objects[wi]['body'][built['tail_start']:] == built['tail']))
    ok &= w_ok
    if wa is not None:
        report.append(f"World record: only the World's tail differs, = the source's lists" + (' (path / node uids renewed: ' + ', '.join((f'{a:#x} -> {b:#x}' for a, b in built['renew'].items())) + ')' if built['renew'] else '') + f': {w_ok}')
    ba, bb = (files[BLOCK], before[BLOCK])
    sa = spawn_layers.filter_sets(ba.filter_table) if ba.filter_table else []
    sb = spawn_layers.filter_sets(bb.filter_table) if bb.filter_table else []
    imap = built['imap']
    if built['route'] == 'block':
        b_ok = ba.filter_table == bb.filter_table and len(ba.objects) == len(bb.objects) + len(built['kit'])
        for j, q in enumerate(bb.objects[1:], 1):
            o = ba.objects[j]
            if j in built['repl']:
                kq = spawn_layers.spawn_kind(q['body'])
                b_ok &= spawn_layers.spawn_kind(o['body']) == (2, imap[kq[1]]) and o['body'][10:74] == q['body'][10:74] and (sa[o['filter']] == sb[q['filter']]) and ('Scene' in comps(o)) and (o['uid'] != q['uid']) and ((E.own_filter(o['body']) or (0, 0, None))[2] == E.filter_lists(ba.filter_table)[o['filter']])
            else:
                b_ok &= o['body'] == q['body'] and o['filter'] == q['filter']
        b_ok &= all((o['filter'] is None for o in ba.objects[len(bb.objects):]))
        ok &= b_ok
        report.append(f"{BLOCK}: {len(built['repl'])} starts replaced one for one (index" + ('' if all((x == y for x, y in imap.items())) else ' mapped ' + str(imap)) + f", transform, filter kept and the clone's own DataLayerFilter = it, Scene added, new uid), kit appended unfiltered, everything else unchanged: {b_ok}")
    else:
        by_uid = {o['uid']: o for o in ba.objects if o['has_uid']}
        old_same = all((by_uid.get(q['uid'], {}).get('body') == q['body'] for q in bb.objects[1:] if q['has_uid']))
        n2 = struct.unpack_from('<I', ba.objects[0]['body'], 10)[0]
        listed = {struct.unpack_from('<I', ba.objects[0]['body'], 14 + 6 * k + 2)[0] for k in range(n2)}
        inst = {o['uid'] for o in ba.objects if o['filter'] is not None}
        want = {r['uid'] for r in built['repl'].values()} | ({built['oob']['uid']} if built['oob'] else set())
        clones = [by_uid.get(r['uid']) for r in built['repl'].values()]
        c_ok = all((c is not None and 'Scene' in comps(c) and (sa[c['filter']] == {E.ESCORT}) and ((E.own_filter(c['body']) or (0, 0, None))[2] == [E.ESCORT]) and (spawn_layers.spawn_kind(c['body']) == (2, imap[spawn_layers.spawn_kind(r['old']['body'])[1]])) and (c['body'][10:74] == r['old']['body'][10:74]) for c, r in zip(clones, built['repl'].values())))
        a_ok = not built['oob'] or (by_uid[built['oob']['uid']]['body'] == built['oob']['body'] and sa[by_uid[built['oob']['uid']]['filter']] == {E.ESCORT} and all((by_uid.get(o['uid'], {}).get('body') == o['body'] for o in built['oob_needs'])))
        k_ok = all((by_uid.get(o['uid'], {}).get('body') == o['body'] and by_uid[o['uid']]['filter'] is None for o in built['kit'] if o['has_uid']))
        b_ok = old_same and listed == inst == want | {q['uid'] for q in bb.objects[1:] if q['filter'] is not None} and c_ok and a_ok and k_ok
        ok &= b_ok
        report.append(f"{BLOCK}: list == filtered instances = the {len(built['repl'])} clones" + (' + the area' if built['oob'] else '') + f" {listed == inst}; clones Scene'd, on ACFE_Escort alone (directory and own filter), index mapped {imap}, on the ACR starts' transforms {c_ok}; area body / objects as the source's {a_ok}; kit unfiltered, same bytes {k_ok}: {b_ok}")
        for rname, (drop, refilter) in built['cells'].items():
            ca, cb = (files[rname], before[rname])
            s_a = spawn_layers.filter_sets(ca.filter_table)
            s_b = spawn_layers.filter_sets(cb.filter_table)
            rest = [q for q in cb.objects if not (q['has_uid'] and q['uid'] in drop)]
            c2 = len(rest) == len(ca.objects) and all((o['body'] == q['body'] if j == 0 and (not drop) else True for j, (o, q) in enumerate(zip(ca.objects, rest))))
            for o, q in zip(ca.objects[1:], rest[1:]):
                if o['has_uid'] and o['uid'] in refilter:
                    c2 &= o['body'] == q['body'] and s_a[o['filter']] == s_b[q['filter']] - {E.ESCORT}
                else:
                    c2 &= o['body'] == q['body'] and (o['filter'] is None) == (q['filter'] is None) and (o['filter'] is None or s_a[o['filter']] == s_b[q['filter']])
            ok &= c2
            report.append(f"{rname}: only the {len(refilter)} starts' filter (- ACFE_Escort), {len(drop)} removed, everything else unchanged: {c2}")
    oob_ok2, oob_p2, oob_rows = oob_modes.judge(files)
    esc_rows = [x for x in oob_rows if x.startswith('ACFE_Escort')]
    report.append(f"out of bounds for Escort: {(esc_rows[0] if esc_rows else 'Escort not judged')}")
    zn, zbad = Z.check_archive(dest)
    ok &= not zbad
    report.append(f'zones.py check: {zn} zone entities, problems {len(zbad)}')
    rec_ok, rec_problems, _notes, _one = aa_records.judge(aa_records.pieces(files), files)
    ok &= rec_ok
    report.append(f"aa_records (Artifact Assault untouched): {('PASS' if rec_ok else 'FAIL ' + '; '.join(rec_problems))}")
    oob_ok, oob_problems, _rows = oob_modes.judge(files)
    ok &= oob_ok
    report.append(f"oob_modes: {('PASS' if oob_ok else 'FAIL ' + '; '.join(oob_problems))}")
    counts = {}
    for q in files.values():
        for o in q.objects:
            if o['has_uid']:
                counts[o['uid']] = counts.get(o['uid'], 0) + 1
    tops = [u for u in built['new_uids'] if u in counts]
    once = all((counts[u] == 1 for u in tops)) and (not any((u in built['defined'] for u in built['new_uids'])))
    unm = [hex(u) for u in built['new_uids'] if UNMAPPED[0] <= u < UNMAPPED[1]]
    ok &= once and (not unm)
    report.append(f"new uids: {len(built['new_uids'])} ({len(tops)} objects, the rest embedded), {min(built['new_uids']):#x}-{max(built['new_uids']):#x}, none the source defines, each object one body: {once}; in 0xF0000000-0xF7FFFFFF: {unm or 'none'}")
    return ok

def release_name(archive):
    return archive[:-len('_dlc.forge')] + '.forge' if archive.endswith('_dlc.forge') else archive

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('map', nargs='?', help="a short name (galata, antioch, ...) or the archive's file name")
    ap.add_argument('--name', help='build folder under out/modes/ (refused if it exists)')
    ap.add_argument('--base', help="build on this archive instead of the release's copy")
    ap.add_argument('--acr', help="ACR's archive of the map (default: ACR's install); an ACB map: ACB's DLC package holding its AdditionalWorldData_TeamVIP (default: ACB's multi/" + ACB_DLC + ')')
    ap.add_argument('--no-chests', action='store_true', help="write an empty chest list (AC3's maps have none)")
    ap.add_argument('--src', help='(a chain step) the build under out/maps/ to apply it to')
    ap.add_argument('--archive', help="(a chain step) its archive (the chain's name: Jerusalem's is ..._dlc.forge)")
    ap.add_argument('--prefix', help="(a chain step) the output's prefix")
    a = ap.parse_args(argv)
    if a.src:
        if not (a.archive and a.prefix) or a.name or a.base or a.map:
            ap.error('a chain step takes --src, --archive and --prefix only')
        chain_name = a.archive
        src = os.path.join(ROOT, 'out', 'maps', a.src, chain_name)
        out_dir = os.path.join(ROOT, 'out', 'maps', f"{a.prefix}_{a.src.split('_', 1)[1]}")
        archive = release_name(chain_name)
    else:
        if not (a.map and a.name):
            ap.error("give a map and --name (or a chain step's --src / --archive / --prefix)")
        chain_name = archive = SHORT.get(a.map.lower(), a.map)
        src = dom_check.resolve(a.base) if a.base else release_source(archive)
        out_dir = os.path.join(OUT, a.name)
        if os.path.exists(out_dir):
            raise SystemExit(f'out/modes/{a.name} exists - pick another build name (builds are never overwritten)')
    if archive in ACB_TEAMVIP:
        acr = a.acr or os.path.join(acb_multi(), ACB_DLC)
    else:
        acr = a.acr or os.path.join(paths.ACR, ACR_NAMES.get(archive, archive))
    dest = os.path.join(out_dir, chain_name)
    report = [f'{chain_name} -> {os.path.relpath(dest, ROOT)} (from {src}; the Escort lists from {acr})']
    try:
        built = build(archive, src, acr, dest, report, keep_chests=not a.no_chests)
    except SystemExit:
        if os.path.isdir(out_dir) and (not os.listdir(out_dir)):
            os.rmdir(out_dir)
        raise
    ok = verify(src, dest, built, report)
    report.append(f"verify: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report_escort.txt' if a.src else 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print('\n'.join(report))
    if a.src:
        if not ok:
            raise SystemExit('verify FAILED')
        print(f'wrote {os.path.relpath(dest, ROOT)}')
    return 0 if ok else 1
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
