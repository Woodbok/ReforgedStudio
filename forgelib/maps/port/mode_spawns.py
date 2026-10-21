"""forgelib/maps/port/mode_spawns.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import struct
import sys
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (MAPS, ROOT):
    sys.path.insert(0, p)
import mapwalk
import spawn_layers
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from library_cell import refs
OUT = os.path.join(ROOT, 'out', 'maps')
MODES = ('Training', 'Wanted', 'Assassinate', 'Deathmatch', 'Easy_Deathmatch', 'CTF_2', 'Manhunt')
TRANSFORM = slice(10, 74)
POS = 58
TRAINING = 3296798512
DEATHMATCH = 3296798502
SKIP_CLASSES = ('GridCellDataBlock', 'OutOfBoundsSettings', 'Mesh', 'Material', 'MeshShape', 'MaterialTemplate', 'TextureMap')
KIT_CLASSES = ('GraphRuleBook', 'ICameraRule', 'PathAnimationCamera', 'CutCameraTransition', 'TemporalCameraTransition', 'Animation')
TABLE, FILTER, ACTION = (4241235364, 3676127342, 3809842145)
CTF, MANHUNT = ('CTF_2', 'Manhunt')

def archive_layer_names():
    import archive_fixes
    return archive_fixes.layer_names('AC3')

def kind(o):
    return spawn_layers.spawn_kind(o['body']) if xrefs.cls('AC3', o['type']) == 'Entity' else None

def add_filter(table, layers):
    if not table:
        table = struct.pack('<III', 0, TABLE, 0)
    assert struct.unpack_from('<I', table, 4)[0] == TABLE, 'not a DataLayerFilterTable'
    n = struct.unpack_from('<I', table, 8)[0]
    f = struct.pack('<III', 0, FILTER, len(layers))
    f += b''.join((struct.pack('<II', 0, ACTION) + b'\x00' + struct.pack('<II', u, 0) for u in sorted(layers)))
    out = bytearray(table + f)
    struct.pack_into('<I', out, 8, n + 1)
    return (bytes(out), n)

def spread(cands, k):
    cands = sorted(cands)
    pos = [struct.unpack_from('<3f', tf, POS - 10) for _n, tf in cands]
    take = [0]
    while len(take) < min(k, len(cands)):
        best = max((i for i in range(len(cands)) if i not in take), key=lambda i: min((math.dist(pos[i], pos[j]) for j in take)))
        take.append(best)
    return [cands[i] for i in take]

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--slot', default='AC3MP_Plantation')
    ap.add_argument('--cells', action='store_true', help="the map's spawns are in cells, not its mode blocks (Souk); Training left as training_block.py made it")
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    report = []
    with Forge(mapwalk.archive_path('AC3', f'DataPC_{a.slot}.forge')) as f:
        slot = {r.name[15:]: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name.startswith('DataBlock_ACFE_')}
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        mine = {r.name[15:]: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name.startswith('DataBlock_ACFE_')}
    modes = [m for m in MODES if m in mine and m in slot]
    everywhere = {}
    if a.cells:
        modes = [m for m in modes if m != 'Training']
        with Forge(src) as f:
            everywhere = {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.size and r.name != 'GlobalMetaFile'}
    map_obj = {o['uid']: o for p_ in everywhere.values() for o in p_.objects if o['has_uid']}
    LAYER = {v.replace('ACFE_', ''): k for k, v in archive_layer_names().items() if v.startswith('ACFE_')}
    slot_spawn_uids = {o['uid'] for m in MODES if m in slot for o in slot[m].objects if o['has_uid']}

    def scene_less():
        out = []
        for p_ in everywhere.values():
            s = spawn_layers.filter_sets(p_.filter_table) if p_.filter_table else []
            for o in p_.objects:
                k = kind(o)
                if k and k[0] == 2 and (o['uid'] not in slot_spawn_uids) and (o['filter'] is not None) and (o['filter'] < len(s)):
                    out.append((p_, o, k, frozenset(s[o['filter']])))
        return out
    spawns, holds, kit = ({}, defaultdict(list), {})
    for m in modes:
        sets = spawn_layers.filter_sets(slot[m].filter_table)
        for o in slot[m].objects:
            k = kind(o)
            if k and k[0] == 2:
                spawns.setdefault(o['uid'], (o, frozenset(sets[o['filter']])))
                holds[m].append(o['uid'])
            elif xrefs.cls('AC3', o['type']) in KIT_CLASSES:
                kit.setdefault(o['uid'] if o['has_uid'] else (o['name'], len(o['body'])), o)
    kit = list(kit.values())

    def own(m, pred):
        if a.cells:
            out = []
            for p_ in everywhere.values():
                s = spawn_layers.filter_sets(p_.filter_table) if p_.filter_table else []
                out += [(o['name'], o['body'][TRANSFORM]) for o in p_.objects if kind(o) and o['filter'] is not None and (o['filter'] < len(s)) and pred(kind(o), s[o['filter']])]
            return out
        s = spawn_layers.filter_sets(mine[m].filter_table) if mine[m].filter_table else []
        return [(o['name'], o['body'][TRANSFORM]) for o in mine[m].objects if kind(o) and o['filter'] is not None and pred(kind(o), s[o['filter']])]
    place = {}
    groups = defaultdict(list)
    for u, (o, layers) in spawns.items():
        groups[kind(o)[1], layers].append(u)
    for (index, layers), uids in groups.items():
        uids.sort(key=lambda u: spawns[u][0]['name'])
        if a.cells and all((u in map_obj for u in uids)):
            for u in uids:
                place[u] = (map_obj[u]['name'], map_obj[u]['body'][TRANSFORM])
            report.append(f'set index {index} {sorted((xrefs_name(x) for x in layers))}: {len(uids)} spawns as the map already holds them (the Training block)')
            continue
        if a.cells and index > 0:
            mode_layer = LAYER[CTF if index in (1, 2) else MANHUNT]
            on = [k for _p, o, k, s in scene_less() if mode_layer in s]
            want_i = index - 1 + min((k[1] for k in on))
            cands = sorted(((o['name'], o['body'][TRANSFORM]) for _p, o, k, s in scene_less() if mode_layer in s and k[1] == want_i))
            source = f"the map's {xrefs_name(mode_layer)} team spawns of index {want_i}"
        elif index == 0 and TRAINING in {x for x in layers}:
            cands = sorted(own('Training', lambda k, s: k[0] == 0 and TRAINING in s))
            source = "the map's Training spawns"
        elif index == 0 and DEATHMATCH in layers:
            cands = spread(own('Deathmatch', lambda k, s: k[0] == 0 and DEATHMATCH in s), len(uids))
            source = "the map's Deathmatch respawns, spread"
        else:
            block = next((m for m in modes if uids[0] in holds[m] and any((kind(o) == (2, index) for o in mine[m].objects))), None)
            acr_index = index
            if block is None:
                block = next((m for m in modes if uids[0] in holds[m] and any((kind(o) and kind(o)[0] == 2 for o in mine[m].objects))))
                have = sorted({kind(o)[1] for o in mine[block].objects if kind(o) and kind(o)[0] == 2})
                spare = [i for i in have if i not in (1, 2, 3, 4)]
                assert spare, f'{block}: no team {index} spawns and no spare team index in {have}'
                acr_index = spare[0]
            cands = sorted(own(block, lambda k, s, i=acr_index: k == (2, i)))
            source = f"the map's team {acr_index} spawns ({block})" + (f' standing in for team {index}' if acr_index != index else '')
        if len(cands) < len(uids):
            raise SystemExit(f'{len(cands)} placements for {len(uids)} spawns ({source})')
        for u, (name, tf) in zip(uids, cands):
            place[u] = (name, tf)
        report.append(f'set index {index} {sorted((xrefs_name(x) for x in layers))}: {len(uids)} spawns on {source}: ' + ', '.join((n for u in uids for n in [place[u][0]])))
    for u, (o, _l) in spawns.items():
        body = o['body'][:TRANSFORM.start] + place[u][1] + o['body'][TRANSFORM.stop:]
        if a.cells and u in map_obj:
            body = map_obj[u]['body']
        spawns[u] = (dict(o, body=body), spawns[u][1])
    changed, expect = ({}, {})
    for m in modes:
        p = mine[m]
        table = p.filter_table
        sets = spawn_layers.filter_sets(table) if table else []
        drop = {o['uid'] for o in p.objects if kind(o) and kind(o)[0] == 2}
        have = {o['uid'] for o in p.objects if o['has_uid']}
        clash = [spawns[u][0]['name'] for u in holds[m] if u in have]
        if clash:
            raise SystemExit(f"{m}: slot spawn uids already in the map's block: {clash}")
        fidx = {}
        for u in holds[m]:
            layers = spawns[u][1]
            if layers not in fidx:
                hit = [i for i, s in enumerate(sets) if s == set(layers)]
                if hit:
                    fidx[layers] = hit[0]
                else:
                    table, fidx[layers] = add_filter(table, layers)
                    sets.append(set(layers))
        head = p.objects[0]
        b = head['body']
        n = struct.unpack_from('<I', b, 10)[0]
        end = 14 + 6 * n
        m_count = struct.unpack_from('<I', b, end)[0]
        entries = [b[k:k + 6] for k in range(14, end, 6)]
        kept = [e for e in entries if struct.unpack_from('<I', e, 2)[0] not in drop]
        gone = len(entries) - len(kept)
        add = [b'\x01\x00' + struct.pack('<I', u) for u in holds[m]]
        b = b[:10] + struct.pack('<I', len(kept) + len(add)) + b''.join(kept + add) + struct.pack('<I', m_count - gone + len(add)) + b[end + 4:]
        objs = [(head['serial'], mapwalk.object_bytes(head, b), head['filter'])]
        body_objs = [o for o in p.objects[1:] if o['uid'] not in drop or not o['has_uid']]
        last_entity = max([i for i, o in enumerate(body_objs) if xrefs.cls('AC3', o['type']) == 'Entity' and o['filter'] is not None], default=-1)
        new_entities = [(spawns[u][0]['serial'], mapwalk.object_bytes(spawns[u][0]), fidx[spawns[u][1]]) for u in holds[m]]
        if last_entity < 0:
            objs += new_entities
        for i, o in enumerate(body_objs):
            objs.append((o['serial'], mapwalk.object_bytes(o), o['filter']))
            if i == last_entity:
                objs += new_entities
        have_kit = {o['uid'] for o in body_objs if o['has_uid']}
        kit_add = [o for o in kit if not (o['has_uid'] and o['uid'] in have_kit)] if holds[m] else []
        objs += [(o['serial'], mapwalk.object_bytes(o), None) for o in kit_add]
        d = mapwalk.rebuild(objs, table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
        changed[p.rec.index] = p
        expect[m] = (holds[m], drop, len(kit_add))
        report.append(f'{m}: + {len(holds[m])} initial spawns, - {len(drop)} scene-less type-2 spawns, + {len(kit_add)} camera-kit objects; filters {sorted(set(fidx.values()))} (table {len(p.filter_table)} -> {len(table)} B)')
    stripped = {}
    if a.cells:
        rebuilt = {LAYER[m] for m in modes} | {TRAINING}
        sl = scene_less()
        gone_all = {o['uid'] for _p, o, _k, s in sl if not s - rebuilt}
        users = [(o_['name'], hex(u)) for p_ in everywhere.values() for o_ in p_.objects[1:] for u in refs(o_['body']) if u in gone_all and o_.get('uid') != u]
        if users:
            raise SystemExit(f'scene-less spawns to remove are referenced: {users}')
        for p_ in everywhere.values():
            here = [(o, s) for pp, o, _k, s in sl if pp is p_ and s & rebuilt]
            if not here:
                continue
            table = p_.filter_table
            sets = spawn_layers.filter_sets(table)
            new_filter, dropped = ({}, set())
            for o, s in here:
                rest = frozenset(s - rebuilt)
                if not rest:
                    dropped.add(o['uid'])
                    continue
                hit = [i for i, x in enumerate(sets) if x == set(rest)]
                if hit:
                    new_filter[o['uid']] = hit[0]
                else:
                    table, new_filter[o['uid']] = add_filter(table, rest)
                    sets.append(set(rest))
            head = p_.objects[0]
            b = head['body']
            n = struct.unpack_from('<I', b, 10)[0]
            end = 14 + 6 * n
            m_count = struct.unpack_from('<I', b, end)[0]
            entries = [b[k:k + 6] for k in range(14, end, 6)]
            kept = [e for e in entries if struct.unpack_from('<I', e, 2)[0] not in dropped]
            gone = len(entries) - len(kept)
            b = b[:10] + struct.pack('<I', len(kept)) + b''.join(kept) + struct.pack('<I', m_count - gone) + b[end + 4:]
            objs = [(head['serial'], mapwalk.object_bytes(head, b), head['filter'])]
            objs += [(o['serial'], mapwalk.object_bytes(o), new_filter.get(o['uid'], o['filter']) if o['has_uid'] else o['filter']) for o in p_.objects[1:] if not (o['has_uid'] and o['uid'] in dropped)]
            d = mapwalk.rebuild(objs, table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, p_.rec.index, payload)
            stripped[p_.rec.index] = (p_, dropped, new_filter)
            report.append(f"{p_.rec.name}: scene-less type-2 spawns off the rebuilt modes' layers: {len(new_filter)} kept on their other layers, {len(dropped)} removed (list N, M -{gone}); filter table {len(p_.filter_table)} -> {len(table)} B")
    slot_uids = {o['uid'] for m in modes for o in slot[m].objects if o['has_uid']}
    copied = set(spawns) | {o['uid'] for o in kit if o['has_uid']}
    bad = [(o['name'], hex(v)) for o in [spawns[u][0] for u in spawns] + kit for v in refs(o['body']) if v in slot_uids and v not in copied]
    if bad:
        raise SystemExit(f'slot spawns point at objects left out: {bad}')
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = True
    written = ArchiveImage.read(dest)
    with Forge(dest) as f, Forge(src) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index not in changed and x.index not in stripped))
        for sidx, (p_, dropped, new_filter) in stripped.items():
            q = mapwalk.Payload(f.records[sidx], f.read(f.records[sidx]))
            sets = spawn_layers.filter_sets(q.filter_table)
            before = [o for o in p_.objects if not (o['has_uid'] and o['uid'] in dropped)]
            same = len(before) == len(q.objects) and all((x['body'] == y['body'] and (y['filter'] == new_filter[x['uid']] if x['has_uid'] and x['uid'] in new_filter else y['filter'] == x['filter']) for x, y in zip(before[1:], q.objects[1:])))
            left = [o['name'] for o in q.objects if kind(o) and kind(o)[0] == 2 and (o['filter'] is not None) and sets[o['filter']] & ({LAYER[m] for m in modes} | {TRAINING}) and (o['uid'] not in spawns)]
            good = same and (not left)
            ok &= good
            report.append(f"verify {f.records[sidx].name}: {len(dropped)} removed, the rest unchanged but the stripped spawns' filter {same}; scene-less type-2 spawns left on a rebuilt mode's layer {left or 0}: {('OK' if good else 'FAILED')}")
        for idx in changed:
            r = f.records[idx]
            q = mapwalk.Payload(r, f.read(r))
            m = r.name[15:]
            sets = spawn_layers.filter_sets(q.filter_table) if q.filter_table else []
            key = struct.unpack_from('<I', written.files[idx].entry, 8)[0] == q.objects[0]['uid']
            n2 = struct.unpack_from('<I', q.objects[0]['body'], 10)[0]
            listed = {struct.unpack_from('<I', q.objects[0]['body'], 14 + 6 * k + 2)[0] for k in range(n2)}
            inst = {o['uid'] for o in q.objects if o['filter'] is not None}
            init = [o for o in q.objects if kind(o) and kind(o)[0] == 2]
            want = expect[m][0]
            layers_ok = all((sets[o['filter']] == set(spawns[o['uid']][1]) for o in init if o['uid'] in spawns))
            places = [o['body'][TRANSFORM] for o in init]
            good = key and listed == inst and (sorted((o['uid'] for o in init)) == sorted(want)) and layers_ok and (len(set(places)) == len(places))
            ok &= good
            report.append(f"verify {m}: key {key}, list == filtered instances {listed == inst}, initial spawns {len(init)} = the slot's {len(want)}, layers as the slot's {layers_ok}, placements distinct {len(set(places)) == len(places)}: {('OK' if good else 'FAILED')}")
    ok &= others
    report.append(f"verify: {len(changed)} mode blocks rebuilt; every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
_NAMES = {}

def xrefs_name(u):
    if not _NAMES:
        import archive_fixes
        _NAMES.update(archive_fixes.layer_names('AC3'))
    return _NAMES.get(u, f'{u:#x}').replace('ACFE_', '')
if __name__ == '__main__':
    main(sys.argv[1:])
