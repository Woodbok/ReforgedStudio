"""forgelib/maps/build_static.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import re
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import changed_classes
import convert
import coverage
import entity
import inventory
import mapwalk
import world
import xrefs
from datafile import compress, decompress
from forge_write import ArchiveImage, set_payload, write
from summary import BULK
OUT = os.path.join(ROOT, 'out', 'maps')
REF_PREFIXES = (b'\x01\x00', b'\x01\x01', b'\x01\x02')
STAGE1_RULES = [r for r in convert.RULES if r[0] not in ('chase breaker', 'group scene byte')]
STAGE1_RULED = set(convert.GREW_ONE_BYTE)

def unknown_checker(stage1=False, extra=()):
    ch = changed_classes.changed()
    ids = {struct.pack('<I', t): n for t, (n, _a, _b) in ch.items() if n not in ('Animation', 'World', 'MaterialTemplate', 'LayeredSky')}
    skip = coverage.PROVEN_SAME | (STAGE1_RULED if stage1 else coverage.RULED) | set(extra)

    def unknown_in(data):
        return {n for nb, n in ids.items() if nb in data} - skip
    return unknown_in

def refs_to(body, uids):
    hit = set()
    for u in uids:
        ub = struct.pack('<I', u)
        i = body.find(ub, 2)
        while i >= 2:
            if body[i - 2:i] in REF_PREFIXES:
                hit.add(u)
                break
            i = body.find(ub, i + 1)
    return hit

def strip_elements(body, bad, game='ACR'):
    xrefs.cls(game, 0)
    reg = xrefs._REG[game]
    by_name = {}
    for t, n in reg.items():
        if t >= 1 << 24:
            by_name.setdefault(n, t)
    par = convert.parents(game)
    heads = []
    for name, t in by_name.items():
        tb = struct.pack('<I', t)
        k = body.find(tb, 5)
        while k >= 5:
            if body[k - 5] in (0, 1):
                heads.append((k - 5, name))
            k = body.find(tb, k + 1)
    heads.sort()
    cuts, gone = ([], [])
    for b in sorted(bad):
        t = by_name.get(b)
        if t is None:
            return (None, f"{b}: not in {game}'s registry")
        tb = struct.pack('<I', t)
        at = {q for q, n in heads if n == b}
        k = body.find(tb)
        while k >= 0:
            if k - 5 not in at:
                return (None, f"{b} at +{k} is not a list element's head")
            k = body.find(tb, k + 1)
        for q in sorted(at):
            anc, found = (par.get(b), None)
            while anc and found is None:
                sib = [h for h, n in heads if convert.descends(n, anc, game)]
                for i, s0 in enumerate(sib):
                    n = struct.unpack_from('<I', body, s0 - 4)[0] if s0 >= 4 else 0
                    if 0 < n <= len(sib) - i and q in sib[i:i + n] and all((sib[j + 1] - sib[j] >= 9 for j in range(i, i + n - 1))):
                        found = (s0 - 4, sib[i:i + n])
                        break
                anc = par.get(anc)
            if found is None:
                return (None, f'{b} at +{q + 5}: no list of its family round it')
            cpos, elems = found
            j = elems.index(q)
            if j + 1 < len(elems):
                end = elems[j + 1]
            else:
                lens = {elems[x + 1] - elems[x] for x in range(len(elems) - 1)}
                if len(lens) != 1:
                    return (None, f"{b} at +{q + 5}: the list's last element, lengths differ - its end is not known")
                end = q + lens.pop()
                if end > len(body):
                    return (None, f"{b} at +{q + 5}: the list's last element runs past the body")
            cuts.append((cpos, q, end))
            gone.append((b, struct.unpack_from('<I', body, q + 1)[0]))
    if not cuts:
        return (None, 'nothing to cut')
    out = bytearray(body)
    for cpos, q, end in sorted(cuts, key=lambda c: -c[1]):
        del out[q:end]
        struct.pack_into('<I', out, cpos, struct.unpack_from('<I', out, cpos)[0] - 1)
    return (bytes(out), gone)

def clean_cell(body, dropped):
    n = struct.unpack_from('<I', body, 10)[0]
    end = 14 + 6 * n
    m = struct.unpack_from('<I', body, end)[0]
    keep, gone, inst = ([], 0, 0)
    for i, k in enumerate(range(14, end, 6)):
        if struct.unpack_from('<I', body, k + 2)[0] in dropped:
            gone += 1
            inst += i < m
        else:
            keep.append(body[k:k + 6])
    if not gone:
        return (body, 0)
    return (body[:10] + struct.pack('<I', n - gone) + b''.join(keep) + struct.pack('<I', m - inst) + body[end + 4:], gone)

def clean_region_layouts(body, dropped):
    ids = convert.class_ids()
    k = body.find(struct.pack('<I', ids['RegionLayoutManager']))
    n = struct.unpack_from('<I', body, k + 4)[0]
    start, end = (k + 8, k + 8 + 6 * n)
    keep = [body[i:i + 6] for i in range(start, end, 6) if struct.unpack_from('<I', body, i + 2)[0] not in dropped]
    if len(keep) == n:
        return (body, 0)
    return (body[:k + 4] + struct.pack('<I', len(keep)) + b''.join(keep) + body[end:], n - len(keep))

def ac3_bank(archive):
    p, i = world.world('AC3', archive)
    b = p.objects[i]['body']
    k = b.find(struct.pack('<I', convert.class_ids()['WwiseID']))
    return struct.unpack_from('<I', b, k + 4)[0]

def set_bank(body, bank):
    k = body.find(struct.pack('<I', convert.class_ids()['WwiseID']))
    old = struct.unpack_from('<I', body, k + 4)[0]
    return (body[:k + 4] + struct.pack('<I', bank) + body[k + 8:], old)

def mode_uids(names):
    found = {}
    for u, homes in inventory.universe('AC3').items():
        for arc, f, t, n in homes:
            if f == 'Game Bootstrap Settings' and xrefs.cls('AC3', t) == 'UnlockableGameMode':
                found[n] = u
    missing = [n for n in names if n not in found]
    if missing:
        raise SystemExit(f'no UnlockableGameMode named {missing}; have {sorted(found)}')
    return [found[n] for n in names]

def plan(archive, current=False, proven=(), drop_trees=True, crowdlife_prefixes=(), skip_entities=(), skip_classes=()):
    game = 'ACR'
    targets = convert.targets(game)
    unknown_in = unknown_checker(stage1=not current, extra=proven)
    remap = world.mode_remap(game)
    report = []
    counts = Counter()

    def unknown_in_layout(name, lay):
        return bool({n for n, _l in lay} & unknown_classes)
    ch = changed_classes.changed()
    unknown_classes = {n for _t, (n, _a, _b) in ch.items()} - coverage.PROVEN_SAME - (coverage.RULED | set(proven) if current else STAGE1_RULED) - {'Animation', 'World', 'MaterialTemplate', 'LayeredSky'}
    ac3_has = {n for _t, (n, _a, b) in ch.items() if b is not None}
    tree_id = struct.pack('<I', convert.class_ids()['RealTreeMeshInstanceData'])
    payloads = list(mapwalk.payloads(game, archive))
    objs = [(p, o) for p in payloads for o in p.objects]
    bodies = {}
    final = set()
    dropped = set()
    if current:
        import lift_dropped
    if crowdlife_prefixes:
        import crowdlife
        cl_tmpl, cl_world = crowdlife.template()
        world_uid = next((o['uid'] for _p, o in objs if o['type'] == convert.class_ids()['World']))
        cl_taken = set(inventory.universe('ACR')) | set(inventory.universe('AC3')) | {o['uid'] for _p, o in objs if o['has_uid']}
        cl_next = [lift_dropped.CROWDLIFE_UIDS]

        def cl_alloc():
            while cl_next[0] in cl_taken:
                cl_next[0] += 1
            cl_taken.add(cl_next[0])
            return cl_next[0]
    for p, o in objs:
        cls = xrefs.cls(game, o['type'])
        if cls in BULK or cls in ('MaterialTemplate', 'Animation', 'World'):
            continue
        if cls == 'UnlockableGameMode' and o['uid'] in remap:
            dropped.add(o['uid'])
            report.append(f"drop  {cls:22} {o['name']}  (map's stale copy; World now points at Game Bootstrap Settings)")
            continue
        if skip_classes and cls in skip_classes:
            dropped.add(o['uid'])
            counts[f'{cls} objects dropped (--skip-class)'] += 1
            report.append(f"drop  {cls:22} {o['name']}  (--skip-class)")
            continue
        if skip_entities and cls in ('Entity', 'EntityGroup') and any(((o['name'] or '').startswith(s) for s in skip_entities)):
            dropped.add(o['uid'])
            report.append(f"drop  {cls:22} {o['name']}  (--skip-entity)")
            continue
        if drop_trees and cls in ('Entity', 'EntityGroup') and (tree_id in o['body']):
            dropped.add(o['uid'])
            counts['tree entities dropped'] += 1
            report.append(f"drop  {cls:22} {o['name']}  (places a RealTreeMesh)")
            continue
        if cls == 'Entity' and crowdlife_prefixes and any(((o['name'] or '').startswith(s) for s in crowdlife_prefixes)):
            body, _map = crowdlife.build(o['body'], cl_tmpl, cl_world, world_uid, cl_alloc)
            if not unknown_in(body):
                bodies[id(o)] = body
                final.add(id(o))
                counts['entities rebuilt on GcLMPCrowdLife'] += 1
                report.append(f"crowd {cls:22} {o['name']}  (rebuilt on GcLMPCrowdLife)")
                continue
        if not unknown_in(o['body']):
            continue
        if cls == 'EntityGroup' and current:
            raw, how, nkept, skipped = lift_dropped.group_raw(o['body'], targets, unknown_in)
            if raw is not None:
                bodies[id(o)] = raw
                counts[f'groups converted in place ({how})'] += 1
                report.append(f"group {cls:22} {o['name']}  ({how}, {nkept} children" + (f"; skipped {len(skipped)}: {'; '.join(sorted(set(skipped)))}" if skipped else '') + ')')
                continue
        if cls == 'Entity':
            lays = entity.element_layouts(o['body'])
            if lays is not None:
                flagged = [(n, lay) for n, lay in lays if unknown_in_layout(n, lay)]
                known = entity.ac3_layouts(sorted({n for n, _ in flagged})) if flagged else {}
                if all((lay in known[n] for n, lay in flagged)):
                    counts['entities kept: components match AC3 layouts'] += 1
                    continue
                report.append(f"drop  {cls:22} {o['name']}  (layout not in AC3: {', '.join(sorted({n for n, lay in flagged if lay not in known[n]}))})")
                dropped.add(o['uid'])
                continue
        bad = unknown_in(o['body'])
        if cls not in ('Entity', 'EntityGroup') and (not bad & ac3_has):
            body, gone = strip_elements(o['body'], bad)
            if body is not None and (not unknown_in(body)):
                bodies[id(o)] = body
                counts['objects kept, unknown list elements cut'] += 1
                report.append(f"strip {cls:22} {o['name']}  ({len(gone)} element(s) cut: {', '.join((f'{c} 0x{u:08X}' for c, u in gone))})")
                continue
            why = gone if body is None else 'classes left after the cut'
        else:
            why = None
        dropped.add(o['uid'])
        report.append(f"drop  {cls:22} {o['name']}  ({', '.join(sorted(bad))})" + (f' [not cut: {why}]' if why else ''))
    first = {id(p.objects[0]) for p in payloads}
    for p in payloads:
        o = p.objects[0]
        if o['has_uid'] and o['uid'] in dropped:
            dropped.discard(o['uid'])
            report.append(f"keep  {xrefs.cls(game, o['type']):22} {o['name']}  (first object of its file; left unreferenced)")
    while True:
        grew = False
        for p, o in objs:
            if o['uid'] in dropped and o['has_uid']:
                continue
            cls = xrefs.cls(game, o['type'])
            if cls in BULK or cls in ('GridCellDataBlock', 'World', 'MaterialTemplate', 'Animation'):
                continue
            body = bodies.get(id(o), o['body'])
            if id(o) in first:
                continue
            if cls == 'RegionLayout':
                hit = {u for u in dropped if struct.pack('<I', u) in body[6:]}
            else:
                hit = refs_to(body, dropped)
            if not hit:
                continue
            dropped.add(o['uid'])
            grew = True
            report.append(f"drop  {cls:22} {o['name']}  (pointed at dropped objects)")
        if not grew:
            break
    return (payloads, objs, bodies, final, dropped, report, counts)

def dangling(objs, dropped, bodies=None):
    bodies = bodies or {}
    out = []
    for _p, o in objs:
        if o['has_uid'] and o['uid'] in dropped:
            continue
        cls = xrefs.cls('ACR', o['type'])
        if cls in BULK or cls == 'GridCellDataBlock':
            continue
        hit = {u for u in refs_in(bodies.get(id(o), o['body'])) if u in dropped}
        if hit:
            out.append((cls, o['name'], hit))
    return out
_REF = re.compile(b'(?=\\x01[\\x00\\x01\\x02](....))', re.S)

def refs_in(body):
    return {struct.unpack('<I', m.group(1))[0] for m in _REF.finditer(body, 6)}

def build(archive, bank_from, out_dir, extra_modes=(), drop_trees=True, current=False, proven=(), crowdlife_prefixes=(), skip_entities=(), bank_id=None, skip_classes=()):
    game = 'ACR'
    targets = convert.targets(game)
    rules = convert.RULES if current else STAGE1_RULES
    remap = world.mode_remap(game)
    taken = set(inventory.universe('ACR')) | set(inventory.universe('AC3'))
    payloads, objs, bodies, final, dropped, report, counts = plan(archive, current, proven, drop_trees, crowdlife_prefixes, skip_entities, skip_classes)
    bank = bank_id if bank_id is not None else ac3_bank(bank_from)
    image = ArchiveImage.read(mapwalk.archive_path(game, archive))
    for p in payloads:
        new = []
        for o in p.objects:
            if o['has_uid'] and o['uid'] in dropped:
                counts['objects dropped'] += 1
                continue
            cls = xrefs.cls(game, o['type'])
            if id(o) in final:
                body, log = (bodies[id(o)], {})
            else:
                body, log = convert.convert(bodies.get(id(o), o['body']), targets, rules)
            for k, v in log.items():
                counts[f'rule: {k}'] += v
            if cls == 'World':
                body, wlog = world.convert_world(body, remap, taken)
                body, added = world.add_modes(body, extra_modes, taken)
                wlog += [f'SupportedGameMode added: 0x{u:08X}' for u in added]
                body, n = clean_region_layouts(body, dropped)
                body, old = set_bank(body, bank)
                report += [f'world {line}' for line in wlog]
                report.append(f'world RegionLayoutManager: {n} dropped layout(s) removed')
                report.append(f'world ambience bank 0x{old:08X} -> 0x{bank:08X} ({bank_from})')
            elif cls == 'GridCellDataBlock':
                body, n = clean_cell(body, dropped)
                counts['cell list entries removed'] += n
            if cls not in BULK:
                hit = {u for u in refs_in(body) if u in dropped}
                if hit:
                    counts['kept objects still referencing a dropped one'] += 1
                    first = o is p.objects[0] and cls != 'World'
                    report.append(f"DANGLING {cls:19} {o['name']} -> " + ', '.join((f'0x{u:08X}' for u in sorted(hit))) + ("  (its file's first object: kept for the file, must stay unnamed - checks.py's dropped_objects fails the build if anything names it)" if first else ''))
            new.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        if not new:
            raise ValueError(f'{p.rec.name}: every object dropped - the file would be empty')
        d = mapwalk.rebuild(new, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
        counts['files written'] += 1
        counts['files that lost their prefix'] += bool(p.prefix)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, archive)
    size = write(dest, image)
    return (dest, size, counts, report, dropped)

def verify(dest, dropped=(), current=False, proven=()):
    needles = [struct.pack('<I', u) for u in dropped]
    unknown_in = unknown_checker(stage1=not current, extra=proven)
    c = Counter()
    from forge import Forge
    with Forge(dest) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or rec.size == 0:
                continue
            p = mapwalk.Payload(rec, f.read(rec))
            c['files'] += 1
            c['prefixed'] += bool(p.prefix)
            for o in p.objects:
                c['objects'] += 1
                cls = xrefs.cls('ACR', o['type'])
                if cls in BULK or cls in ('MaterialTemplate', 'Animation', 'World'):
                    continue
                if unknown_in(o['body']):
                    c['objects still holding an unknown class'] += 1
                if any((nb in o['body'][6:] for nb in needles)):
                    c['objects still mentioning a dropped uid'] += 1
    return c

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('map', nargs='?', default='ACFE_Souk')
    ap.add_argument('--bank-from', default='DataPC_AC3MP_Plantation.forge')
    ap.add_argument('--name', default=None, help='build folder under out/maps/')
    ap.add_argument('--modes', default='GameMode Training', help='extra game modes the World declares, comma-separated Bootstrap names (default: Training - mode 11, the solo private session)')
    ap.add_argument('--current', action='store_true', help="today's rules, and convert in place what stage 2's lift_dropped.py used to restore")
    ap.add_argument('--proven', action='append', default=[], metavar='CLASS', help="with --current: treat this changed class as proven (lift_dropped.py --proven's classes)")
    ap.add_argument('--keep-trees', action='store_true', help='keep the entities placing a RealTreeMesh')
    ap.add_argument('--crowdlife', action='append', default=[], metavar='PREFIX', help="rebuild dropped Entities with this name prefix on AC3's GcLMPCrowdLife (N43)")
    ap.add_argument('--skip-entity', action='append', default=[], metavar='PREFIX', help='drop Entities / EntityGroups whose name starts with this')
    ap.add_argument('--skip-class', action='append', default=[], metavar='CLASS', help='drop every object of this class (its references go with them)')
    ap.add_argument('--bank', type=lambda v: int(v, 0), default=None, help="the World's sound bank id")
    ap.add_argument('--keep-escort-lists', action='store_true', help="keep ACR's chest + Escort path lists in the World's tail (world.KEEP_ESCORT_LISTS: the default since R26T5, modes/ESCORT.md)")
    ap.add_argument('--no-escort-lists', action='store_true', help="write AC3's 8-byte World ending instead (the pre-R26T5 behaviour)")
    a = ap.parse_args(argv)
    if a.keep_escort_lists:
        world.KEEP_ESCORT_LISTS = True
    if a.no_escort_lists:
        world.KEEP_ESCORT_LISTS = False
    archive = f'DataPC_{a.map}.forge'
    out_dir = os.path.join(OUT, a.name or f'S1_{a.map.lower()}_static')
    extra = mode_uids([n.strip() for n in a.modes.split(',') if n.strip()])
    dest, size, counts, report, dropped = build(archive, a.bank_from, out_dir, extra, drop_trees=not a.keep_trees, current=a.current, proven=tuple(a.proven), crowdlife_prefixes=tuple(a.crowdlife), skip_entities=tuple(a.skip_entity), bank_id=a.bank, skip_classes=tuple(a.skip_class))
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n\n')
        fh.write('\n'.join((f'{k}: {v}' for k, v in sorted(counts.items()))) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    for k, v in sorted(counts.items()):
        print(f'   {k}: {v}')
    for line in report:
        if line.startswith(('strip ', 'DANGLING ')):
            print('   ' + line)
    v = verify(dest, dropped, a.current, tuple(a.proven))
    print('verify:', dict(v))
    with open(os.path.join(out_dir, 'report.txt'), 'a', encoding='utf-8') as fh:
        fh.write(f'verify: {dict(v)}\n')
if __name__ == '__main__':
    main(sys.argv[1:])
