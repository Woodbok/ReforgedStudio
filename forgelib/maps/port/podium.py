"""forgelib/maps/port/podium.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import inventory
import mapwalk
import xrefs
from aa_zones import CLASS_IDS
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from library_cell import refs
from mode_spawns import TRANSFORM, add_filter, kind
OUT = os.path.join(ROOT, 'out', 'maps')
PODIUM = {'FFA': 1889480925, 'Team': 1889480927}
ACCOLADES = ('01_Glutton', '02_Stalwart', '05_Practiced', '06_Lurker', '10_Visionnary', '11_Despot', '12_Mole', '13_Expert', '14_Unstoppable', '15_Stunner', '16_ExcapeArtist', '17_Immortal', '18_Slayer', '19_Gargoyle', '20_Savior', '21_Wingman')
SPOT_BLOCK = {'FFA': 'DataBlock_ACFE_Wanted', 'Team': 'DataBlock_ACFE_CTF_2'}
ENTRY, ENTRY_CLASS = (18, 62819144)
RULE_WORLD = 30
SHARED = ('DataPC.forge', 'DataPC_extra.forge', 'DataPC_CoreFX.forge', 'DataPC_Fire.forge', 'DataPC_extraparams.forge')
UNMAPPED = (4026531840, 4160749568)
LEAF = struct.pack('<f', 1.0) + struct.pack('<I', 1256993910)

def wdlm_entries(body):
    n = struct.unpack_from('<I', body, 10)[0]
    if 14 + ENTRY * n != len(body):
        raise SystemExit(f"WorldDataLayerManager: {n} entries don't fill {len(body)} bytes")
    out = [body[14 + ENTRY * k:14 + ENTRY * (k + 1)] for k in range(n)]
    for e in out:
        if struct.unpack_from('<II', e, 0) != (0, ENTRY_CLASS) or e[8] or e[13]:
            raise SystemExit(f"WorldDataLayerManager entry not in the known shape: {e.hex(' ')}")
    return out

def entry(layer, block):
    return struct.pack('<II', 0, ENTRY_CLASS) + b'\x00' + struct.pack('<I', layer) + b'\x00' + struct.pack('<I', block)

def head_parts(body):
    n = struct.unpack_from('<I', body, 10)[0]
    end = 14 + 6 * n
    if len(body) != end + 8:
        raise SystemExit(f'data block head of {len(body)} bytes: not 10 + list + M + layer')
    return ([struct.unpack_from('<I', body, k + 2)[0] for k in range(14, end, 6)], struct.unpack_from('<I', body, end)[0], struct.unpack_from('<I', body, end + 4)[0])

def head_body(body, uids, layer):
    return body[:10] + struct.pack('<I', len(uids)) + b''.join((b'\x01\x00' + struct.pack('<I', u) for u in uids)) + struct.pack('<II', len(uids), layer)
MIN_NESTED = 16777216

def embedded(body, sites=False):
    out = {}
    for k in range(2, len(body) - 7):
        u, c = struct.unpack_from('<II', body, k)
        if c in CLASS_IDS and u >= MIN_NESTED and (u != 4294967295) and (u not in CLASS_IDS):
            out.setdefault(u, []).append(k)
    return out if sites else set(out)

def renewal_problems(clone, s_uids, universe):
    defs = {}
    for o in clone:
        for u, ks in embedded(o['body'], sites=True).items():
            if u not in s_uids and u not in universe:
                defs.setdefault(u, []).extend(((o['name'].strip(), k) for k in ks))
    return [f'0x{u:08X} defined {len(v)} times ({v[0][0]} +{v[0][1]} ...)' for u, v in sorted(defs.items()) if len(v) != 1]

def clone_problems(files, slot=None):
    if slot is None:
        slot = {}
        with Forge(mapwalk.archive_path('AC3', 'DataPC_AC3MP_Plantation.forge')) as f:
            for r in f.records:
                if r.name in (f'DataBlock_Podium_{k}' for k in PODIUM):
                    slot[r.name] = mapwalk.Payload(r, f.read(r))
                elif r.name == 'AC3MP_Plantation':
                    w = mapwalk.Payload(r, f.read(r))
                    slot['World'] = next((o['uid'] for o in w.objects if xrefs.cls('AC3', o['type']) == 'World'))
    out = []
    for n_ in PODIUM:
        name = f'DataBlock_Podium_{n_}'
        p, sp = (files.get(name), slot.get(name))
        if not p or not sp or len(p.objects) <= 1:
            continue
        sp_uids = {o['uid'] for o in sp.objects if o['has_uid']}
        genuine = sp_uids | {slot.get('World', 0)}
        for o in sp.objects[1:]:
            genuine |= embedded(o['body'])
        by_key = {}
        for o in sp.objects[1:]:
            by_key.setdefault((o['name'], o['type'], len(o['body'])), []).append(o)
        for o in p.objects[1:]:
            if not o['has_uid'] or o['uid'] in sp_uids:
                continue
            cands = by_key.get((o['name'], o['type'], len(o['body'])), [])
            if not cands:
                out.append(f"{name}/{o['name'].strip()}: no slot object of its name, class and size")
                continue
            a, b = (cands[-1]['body'], o['body'])
            ok_bytes = set(range(TRANSFORM.start, TRANSFORM.stop))
            if xrefs.cls('AC3', o['type']) == 'ICameraRule':
                ok_bytes |= set(range(RULE_WORLD, RULE_WORLD + 4))
            for k in range(len(a) - 3):
                if a[k:k + 4] != b[k:k + 4] and struct.unpack_from('<I', a, k)[0] in genuine:
                    ok_bytes |= set(range(k, k + 4))
            bad = [k for k in range(len(a)) if a[k] != b[k] and k not in ok_bytes]
            if bad:
                k = bad[0]
                out.append(f"{name}/{o['name'].strip()}: {len(bad)} bytes changed off any uid (first +{k}: slot {a[max(0, k - 6):k + 6].hex(' ')} -> {b[max(0, k - 6):k + 6].hex(' ')})")
    return out

def moved(body, anchor, target, yaw):
    m = list(struct.unpack_from('<16f', body, TRANSFORM.start))
    c, s = (math.cos(math.radians(yaw)), math.sin(math.radians(yaw)))

    def rot(x, y, z):
        return (x * c - y * s, x * s + y * c, z)
    for r in range(3):
        m[4 * r:4 * r + 3] = rot(*m[4 * r:4 * r + 3])
    t = rot(*(m[12 + i] - anchor[i] for i in range(3)))
    m[12:15] = [t[i] + target[i] for i in range(3)]
    return body[:TRANSFORM.start] + struct.pack('<16f', *m) + body[TRANSFORM.stop:]

def names(body, u):
    return struct.pack('<I', u) in body

def words(body):
    return {struct.unpack_from('<I', body, k)[0] for k in range(len(body) - 3)}

def pos(body):
    return struct.unpack_from('<3f', body, TRANSFORM.start + 48)

def spot(p, report, label):
    sp = [(o['name'], pos(o['body'])) for o in p.objects if kind(o) and kind(o)[0] == 2]
    if not sp:
        raise SystemExit(f'{p.rec.name}: no initial spawns to place the {label} podium on (run after mode_spawns.py, or --at-*)')
    cx, cy = (sum((q[0] for _n, q in sp)) / len(sp), sum((q[1] for _n, q in sp)) / len(sp))
    name, q = min(sp, key=lambda x: (math.hypot(x[1][0] - cx, x[1][1] - cy), x[0]))
    report.append(f'{label}: {len(sp)} initial spawns of {p.rec.name}, centroid ({cx:.1f}, {cy:.1f}); nearest {name.strip()} ({q[0]:.1f}, {q[1]:.1f}, {q[2]:.2f})')
    return q

def state(files):
    import archive_fixes
    by_name = {v: k for k, v in archive_fixes.layer_names('AC3').items()}
    layers = dict(PODIUM, **{n_: by_name[n_] for n_ in ACCOLADES})
    wdlm = next((o for p in files.values() for o in p.objects if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager'), None)
    listed = {struct.unpack_from('<I', e, 9)[0]: struct.unpack_from('<I', e, 14)[0] for e in wdlm_entries(wdlm['body'])} if wdlm else {}
    heads = {p.objects[0]['uid']: (name, head_parts(p.objects[0]['body'])[2]) for name, p in files.items() if name.startswith('DataBlock_') and p.objects}
    bad = [n_ for n_, lay in layers.items() if lay in listed and heads.get(listed[lay], (None, None))[1] != lay]
    on = [n_ for n_, lay in layers.items() if lay in listed]
    pods = {}
    for n_, lay in PODIUM.items():
        p = files.get(f'DataBlock_Podium_{n_}')
        sets = __import__('spawn_layers').filter_sets(p.filter_table) if p and p.filter_table else []
        pods[n_] = (len(p.objects) - 1 if p else None, sum((1 for o in (p.objects if p else []) if xrefs.cls('AC3', o['type']) == 'Entity' and o['filter'] is not None and (sets[o['filter']] == {lay}))))
    complete = len(on) == len(layers) and all((v[1] >= 2 for v in pods.values()))
    lines = [f'end-of-match layers listed by the World {len(on)} / {len(layers)}' + (f" ({', '.join(on)})" if on and len(on) < len(layers) else '') + (f"; listed on a block that doesn't name them: {bad}" if bad else ''), 'podium blocks: ' + ', '.join((f'{n_} {c} objects, {e} entities on its layer' for n_, (c, e) in pods.items()))]
    return (lines, not bad, complete)

def at_arg(s):
    v = [float(x) for x in s.split(',')]
    if len(v) not in (3, 4):
        raise argparse.ArgumentTypeError('X,Y,Z[,YAW_DEG]')
    return (tuple(v[:3]), v[3] if len(v) == 4 else 0.0)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--slot', default='AC3MP_Plantation')
    ap.add_argument('--uid-base', type=lambda v: int(v, 0), required=True)
    ap.add_argument('--uid-limit', type=lambda v: int(v, 0), default=1024)
    ap.add_argument('--at-ffa', type=at_arg, help="X,Y,Z[,YAW_DEG]: the FFA podium's spot (default: the rule)")
    ap.add_argument('--at-team', type=at_arg, help="X,Y,Z[,YAW_DEG]: the Team podium's spot (default: the rule)")
    ap.add_argument('--layers-only', action='store_true', help="step 1 alone: the 18 layers listed, the podium blocks left empty (Animus' podium blocks; a diagnosis build)")
    ap.add_argument('--dry-run', action='store_true', help='build and verify in memory; write only the report')
    ap.add_argument('--report', help="the report's folder (default: the output folder)")
    a = ap.parse_args(argv)
    if UNMAPPED[0] <= a.uid_base < UNMAPPED[1] or UNMAPPED[0] <= a.uid_base + a.uid_limit - 1 < UNMAPPED[1]:
        raise SystemExit('--uid-base in 0xF0000000-0xF7FFFFFF: AC3 never maps those ids')
    src = os.path.join(OUT, a.src, a.archive)
    report = []
    import archive_fixes
    layer_name = archive_fixes.layer_names('AC3')
    by_name = {v: k for k, v in layer_name.items()}
    layers = dict(PODIUM)
    for n_ in ACCOLADES:
        layers[n_] = by_name[n_]
    slot_path = mapwalk.archive_path('AC3', f'DataPC_{a.slot}.forge')
    slot, slot_world = ({}, None)
    with Forge(slot_path) as f:
        for r in f.records:
            if r.name in (f'DataBlock_Podium_{k}' for k in PODIUM):
                slot[r.name[17:]] = mapwalk.Payload(r, f.read(r))
            elif r.name == a.slot:
                w = mapwalk.Payload(r, f.read(r))
                slot_world = next((o['uid'] for o in w.objects if xrefs.cls('AC3', o['type']) == 'World'))
    universe = inventory.universe('AC3')
    acr = inventory.universe('ACR')
    shared = {u for u, hs in universe.items() if any((h[0] in SHARED for h in hs))}
    slot_file = os.path.basename(slot_path)
    image = ArchiveImage.read(src)
    want = {f'DataBlock_Podium_{k}' for k in PODIUM} | set(SPOT_BLOCK.values()) | {f'DataBlock_{n_}' for n_ in layers}
    mine, world_p, defined, raw_hits, already = ({}, None, set(), set(), {})
    slot_kit = {o['uid']: o['body'] for sp in slot.values() for o in sp.objects[1:] if o['has_uid']}
    lo, hi = (a.uid_base, a.uid_base + a.uid_limit)
    top = struct.pack('<H', lo >> 16)
    with Forge(src) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            d = p.data
            k = d.find(top, 2)
            while k >= 0:
                u = struct.unpack_from('<I', d, k - 2)[0]
                if lo <= u < hi:
                    raw_hits.add(u)
                k = d.find(top, k + 1)
            defined |= {o['uid'] for o in p.objects if o['has_uid']} | {o['serial'] for o in p.objects}
            for o in p.objects:
                if o['has_uid'] and o['uid'] in slot_kit:
                    already.setdefault(o['uid'], []).append((r.name, o['body']))
            if r.name in want:
                mine[r.name] = p
            if any((xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager' for o in p.objects)):
                world_p = p
            else:
                del p
    if world_p is None:
        raise SystemExit('no WorldDataLayerManager in the archive')
    wi = next((i for i, o in enumerate(world_p.objects) if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager'))
    world_uid = next((o['uid'] for o in world_p.objects if xrefs.cls('AC3', o['type']) == 'World'))
    report.append(f'map World 0x{world_uid:08X} ({world_p.rec.name}); slot {a.slot} World 0x{slot_world:08X}')
    taken = set(universe) | set(acr) | defined | raw_hits
    nxt = [lo]
    fresh_all = []

    def fresh():
        while nxt[0] in taken:
            nxt[0] += 1
        if nxt[0] >= hi:
            raise SystemExit('uid range exhausted')
        taken.add(nxt[0])
        fresh_all.append(nxt[0])
        return nxt[0]
    entries = wdlm_entries(world_p.objects[wi]['body'])
    by_layer = {struct.unpack_from('<I', e, 9)[0]: i for i, e in enumerate(entries)}
    heads, linked = ({}, [])
    for n_, lay in layers.items():
        blk = f'DataBlock_Podium_{n_}' if n_ in PODIUM else f'DataBlock_{n_}'
        if blk not in mine:
            raise SystemExit(f'{blk}: not in the archive (archive_fixes.py makes it)')
        h = mine[blk].objects[0]
        heads[blk] = (h, lay)
        if lay in by_layer:
            e = entries[by_layer[lay]]
            if struct.unpack_from('<I', e, 14)[0] != h['uid']:
                raise SystemExit(f"{n_}: listed on 0x{struct.unpack_from('<I', e, 14)[0]:08X}, not {blk} (0x{h['uid']:08X})")
            continue
        entries.append(entry(lay, h['uid']))
        linked.append(f'Podium_{n_}' if n_ in PODIUM else n_)
    entries.sort(key=lambda e: struct.unpack_from('<I', e, 9)[0])
    old_wdlm = world_p.objects[wi]['body']
    new_wdlm = old_wdlm[:10] + struct.pack('<I', len(entries)) + b''.join(entries)
    report.append(f"WorldDataLayerManager: {len(by_layer)} -> {len(entries)} entries (+ {', '.join(linked) or 'none'})")
    kit_bytes, fresh_objs, blocks_out, expect = ({}, {}, {}, {})
    for n_, sp in ({} if a.layers_only else slot).items():
        objs = sp.objects
        s_uids = {o['uid'] for o in objs if o['has_uid']}
        own = [o for o in objs[1:] if o['has_uid'] and {h[0] for h in universe.get(o['uid'], [])} == {slot_file}]
        book = next((o for o in objs if xrefs.cls('AC3', o['type']) == 'CameraGraphRuleBook'))
        rule = [o for o in objs if xrefs.cls('AC3', o['type']) == 'ICameraRule' and struct.unpack_from('<I', o['body'], RULE_WORLD)[0] == slot_world]
        if len(rule) != 1:
            raise SystemExit(f"{n_}: {len(rule)} ICameraRules on the slot's World (+{RULE_WORLD})")
        rule = rule[0]
        cams = [o for o in objs if o['has_uid'] and xrefs.cls('AC3', o['type']) == 'PathAnimationCamera' and names(rule['body'], o['uid'])]
        if len(cams) != 1:
            raise SystemExit(f'{n_}: the slot rule names {len(cams)} cameras')
        cam = cams[0]
        ents = [o for o in own if xrefs.cls('AC3', o['type']) == 'Entity']
        refpos = [o for o in ents if names(cam['body'], o['uid'])]
        if len(ents) != 2 or len(refpos) != 1:
            raise SystemExit(f'{n_}: {len(ents)} own entities, {len(refpos)} named by the slot camera (want 2 / 1)')
        refpos = refpos[0]
        outro = next((o for o in ents if o is not refpos))
        if not names(outro['body'], book['uid']):
            raise SystemExit(f"{n_}: the outro entity doesn't name the rule book")
        clone = own + [rule, cam, book]
        bad = renewal_problems(clone, s_uids, universe)
        if bad:
            raise SystemExit(f"{n_}: values read as nested uids that aren't (renewing them rewrites data): {bad[:4]}")
        mapping = {}
        for o in clone:
            for u in sorted({o['uid']} | embedded(o['body']) - s_uids - set(universe)):
                if u not in mapping:
                    mapping[u] = fresh()
        anchor = pos(refpos['body'])
        at = getattr(a, f'at_{n_.lower()}')
        if at:
            target, yaw = at
            report.append(f'{n_}: --at ({target[0]:.1f}, {target[1]:.1f}, {target[2]:.2f}), yaw {yaw:g}')
        else:
            target, yaw = (spot(mine[SPOT_BLOCK[n_]], report, n_), 0.0)
        new = {}
        for o in clone:
            b = o['body']
            if o is rule:
                b = b[:RULE_WORLD] + struct.pack('<I', world_uid) + b[RULE_WORLD + 4:]
            for u, v in mapping.items():
                b = b.replace(struct.pack('<I', u), struct.pack('<I', v))
            if o is refpos or o is outro:
                b = moved(b, anchor, target, yaw)
            new[o['uid']] = dict(o, uid=mapping[o['uid']], serial=mapping[o['uid']], body=b, body_size=o['body_size'] + len(b) - len(o['body']))
        fresh_objs[n_] = (new, mapping, {'refpos': refpos['uid'], 'outro': outro['uid'], 'rule': rule['uid'], 'cam': cam['uid'], 'book': book['uid']})
        blk = mine[f'DataBlock_Podium_{n_}']
        if len(blk.objects) != 1:
            raise SystemExit(f'{blk.rec.name}: {len(blk.objects)} objects - not the empty placeholder')
        table, fi = add_filter(b'', {PODIUM[n_]})
        inst = [new[o['uid']]['uid'] for o in objs[1:] if o['has_uid'] and o['uid'] in new and (o['filter'] is not None)]
        h = blk.objects[0]
        out = [(h['serial'], mapwalk.object_bytes(h, head_body(h['body'], inst, PODIUM[n_])), None)]
        for o in objs[1:]:
            if o in own:
                q = new[o['uid']]
                out.append((q['serial'], mapwalk.object_bytes(q), fi if o['filter'] is not None else None))
                continue
            out.append((o['serial'], mapwalk.object_bytes(o), None))
            kit_bytes[n_, o['uid'] if o['has_uid'] else o['serial']] = o['body']
            if o['has_uid'] and o['uid'] in new:
                q = new[o['uid']]
                out.append((q['serial'], mapwalk.object_bytes(q), None))
        blocks_out[blk.rec.name] = mapwalk.rebuild(out, table)
        expect[blk.rec.name] = (n_, inst)
        report.append(f"{n_}: {len(objs) - 1} slot objects ({len(own)} the slot's own: fresh copies; + fresh rule, camera, rule book); {len(mapping)} uids renewed; RefPos ({anchor[0]:.1f}, {anchor[1]:.1f}, {anchor[2]:.2f}) -> ({target[0]:.1f}, {target[1]:.1f}, {target[2]:.2f})")
    for blk, (h, lay) in heads.items():
        if blk in blocks_out:
            continue
        p = mine[blk]
        uids, m_, old = head_parts(h['body'])
        if old == lay:
            continue
        b = h['body'][:-4] + struct.pack('<I', lay)
        objs = [(h['serial'], mapwalk.object_bytes(h, b), h['filter'])]
        objs += [(o['serial'], mapwalk.object_bytes(o), o['filter']) for o in p.objects[1:]]
        blocks_out[blk] = mapwalk.rebuild(objs, p.filter_table)
    report.append(f"block heads given their own layer: {sorted((b[10:] for b in blocks_out if 'Podium' not in b))}")
    objs = [(o['serial'], mapwalk.object_bytes(o, new_wdlm if i == wi else None), o['filter']) for i, o in enumerate(world_p.objects)]
    world_d = mapwalk.rebuild(objs, world_p.filter_table)
    ok = True
    changed = {world_p.rec.name: world_d, **blocks_out}
    new_p = {}
    for name, d in changed.items():
        rec = world_p.rec if name == world_p.rec.name else mine[name].rec
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        new_p[name] = (rec, payload, mapwalk.Payload(rec, payload))
    if len(set(fresh_all)) != len(fresh_all) or any((not lo <= u < hi or UNMAPPED[0] <= u < UNMAPPED[1] for u in fresh_all)):
        ok = False
        report.append('verify fresh uids: FAILED (duplicate, out of range or unmapped)')
    all_defined = set(defined)
    for _r, _pl, q in new_p.values():
        all_defined |= {o['uid'] for o in q.objects if o['has_uid']} | {o['serial'] for o in q.objects}
        all_defined |= {u for o in q.objects for u in embedded(o['body'])}
    with Forge(slot_path) as f:
        slot_defined = set()
        for r in f.records:
            if r.size and r.name != 'GlobalMetaFile':
                try:
                    q = mapwalk.Payload(r, f.read(r))
                except mapwalk.MapWalkError:
                    continue
                slot_defined |= {o['uid'] for o in q.objects if o['has_uid']} | {o['serial'] for o in q.objects}
    known = set(universe) | slot_defined
    fresh_set = set(fresh_all)
    for name, (n_, inst) in expect.items():
        q = new_p[name][2]
        new, mapping, role = fresh_objs[n_]
        h_uids, m_, lay = head_parts(q.objects[0]['body'])
        filt = [o['uid'] for o in q.objects if o['filter'] is not None]
        import spawn_layers
        sets = spawn_layers.filter_sets(q.filter_table)
        kit_ok = all((next((o['body'] for o in q.objects if (o['uid'] if o['has_uid'] else o['serial']) == k), None) == b for (nn, k), b in kit_bytes.items() if nn == n_))
        byuid = {o['uid']: o for o in q.objects if o['has_uid']}
        r_rule, r_cam, r_book = (byuid[mapping[role[x]]] for x in ('rule', 'cam', 'book'))
        r_ref, r_out = (byuid[mapping[role['refpos']]], byuid[mapping[role['outro']]])
        links = struct.unpack_from('<I', r_rule['body'], RULE_WORLD)[0] == world_uid and names(r_rule['body'], r_cam['uid']) and names(r_cam['body'], r_ref['uid']) and names(r_out['body'], r_book['uid']) and names(r_book['body'], r_rule['uid']) and (not names(r_book['body'], role['rule']))
        olds = set(mapping)
        stale = [o['name'].strip() for o in new.values() if any((struct.pack('<I', u) in o['body'] for u in olds))]
        leaf = all((o['body'][o['body'].find(LEAF) - 2:o['body'].find(LEAF)] == b'\xff\xff' for o in (r_ref, r_out) if o['body'].find(LEAF) >= 2))
        slot_nested = {u for o in slot[n_].objects for u in embedded(o['body'])}
        slot_dangling = {(o['uid'] if o['has_uid'] else o['serial'], u) for o in slot[n_].objects for u in words(o['body']) if u in known and u not in slot_defined and (u not in slot_nested) and (u not in shared)}
        unresolved, stock_dangling, slot_only = ([], 0, 0)
        for o in q.objects[1:]:
            key = o['uid'] if o['has_uid'] else o['serial']
            back = {v: k for k, v in mapping.items()}.get(key, key)
            for u in words(o['body']):
                if u in known or u in fresh_set:
                    if u in all_defined or u in shared:
                        continue
                    if (back, u) in slot_dangling:
                        stock_dangling += 1
                        continue
                    if kit_bytes.get((n_, key)) == o['body'] and (u == slot_world or {h[0] for h in universe.get(u, [])} == {slot_file}):
                        slot_only += 1
                        continue
                    unresolved.append((o['name'].strip()[:40], f'0x{u:08X}'))
        good = sorted(h_uids) == sorted(filt) == sorted(inst) and m_ == len(inst) and (lay == PODIUM[n_]) and (sets == [{PODIUM[n_]}]) and kit_ok and links and (not stale) and leaf and (not unresolved)
        ok &= good
        report.append(f"verify {name}: head lists the {len(inst)} filtered instances {sorted(h_uids) == sorted(filt)}, names its layer {lay == PODIUM[n_]}, filter {sets == [{PODIUM[n_]}]}; kit as the slot's {kit_ok}; rule on this World -> camera -> RefPos, outro -> book -> rule {links}; no slot-own uid left {not stale}; leaf FFFF {leaf}; references unresolved {unresolved or 0} (dangling in the slot too: {stock_dangling}; the kit naming the slot's own objects, as on every other AC3 map: {slot_only}): {('OK' if good else 'FAILED')}")
    cp = clone_problems({name: new_p[name][2] for name in expect}, dict({f'DataBlock_Podium_{k}': v for k, v in slot.items()}, World=slot_world))
    ok &= not cp
    report.append(f"verify fresh copies changed only at uids, matrices and the rule's World: {cp[:4] or 'OK'}" + (': FAILED' if cp else ''))
    clash = sorted({f'0x{u:08X} in {n}' for u, hs in already.items() for n, b in hs if b != slot_kit[u]})
    ok &= not clash
    report.append(f"verify kit uids the map already defined: {len(already)}, with other bytes: {clash or 0}: {('OK' if not clash else 'FAILED')}")
    wq = new_p[world_p.rec.name][2]
    we = wdlm_entries(wq.objects[wi]['body'])
    lays = [struct.unpack_from('<I', e, 9)[0] for e in we]
    block_heads = {}
    for name in heads:
        hb = (new_p[name][2] if name in new_p else mine[name]).objects[0]
        block_heads[hb['uid']] = head_parts(hb['body'])[2]
    listed = {struct.unpack_from('<I', e, 9)[0]: struct.unpack_from('<I', e, 14)[0] for e in we}
    w_good = lays == sorted(lays) and len(set(lays)) == len(lays) and all((block_heads.get(listed.get(lay)) == lay for lay in layers.values())) and all((o['body'] == p_o['body'] for i, (o, p_o) in enumerate(zip(wq.objects, world_p.objects)) if i != wi))
    ok &= w_good
    report.append(f"verify World: manager sorted, {len(layers)} end-of-match layers on blocks naming them, the rest of the record unchanged: {('OK' if w_good else 'FAILED')}")
    view = dict(mine, **{name: q for name, (_r, _pl, q) in new_p.items()})
    lines, consistent, complete = state(view)
    good = consistent and (complete or a.layers_only)
    ok &= good
    report.append(f"verify end of match (checks.end_of_match): {'; '.join(lines)}: {('OK' if good else 'FAILED')}")
    report.append(f"verify fresh uids: {len(fresh_all)} in 0x{lo:08X}..0x{hi - 1:08X}, unique, free: {('OK' if len(set(fresh_all)) == len(fresh_all) else 'FAILED')}")
    report.append(f"verify: {len(changed)} records changed (World, {len(blocks_out)} data blocks); every other record untouched: {('OK' if ok else 'FAILED')}")
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    rep_dir = a.report or out_dir
    os.makedirs(rep_dir, exist_ok=True)
    if not a.dry_run and ok:
        for _name, (rec, payload, _q) in new_p.items():
            set_payload(image, rec.index, payload)
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, a.archive)
        size = write(dest, image)
        with Forge(dest) as f, Forge(src) as g:
            touched = {rec.index for rec, _p, _q in new_p.values()}
            same = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index not in touched))
        report.append(f"verify written: every other record byte-identical {same}: {('OK' if same else 'FAILED')}")
        ok &= same
        print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    with open(os.path.join(rep_dir, 'report.txt' if not a.dry_run else f'podium_{a.prefix}.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
