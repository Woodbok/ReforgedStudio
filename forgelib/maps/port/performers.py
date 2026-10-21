"""forgelib/maps/port/performers.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import build_static
import convert
import inventory
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from lift_dropped import anchor_index
from merge_cells import cell_list
OUT = os.path.join(ROOT, 'out', 'maps')

def refs(body):
    return {struct.unpack_from('<I', body, j + 2)[0] for j in range(2, len(body) - 5) if body[j] == 1 and body[j + 1] == 0}

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--map', required=True, help='the ACR archive the performers come from')
    ap.add_argument('--entities', action='append', default=[], help='name prefix of top-level Entities / EntityGroups')
    ap.add_argument('--packs', action='append', default=[], help='name of an unlisted object (prop ObjectPack) to copy')
    ap.add_argument('--builder', action='append', default=[], metavar='OLD=NEW', help="repoint the spawned NPC's builder")
    ap.add_argument('--carry', action='append', default=[], metavar='FILE', help="a file no cell lists (the fire-spitter's ActionBlock + clips, the torch / bottle packs): its objects copied, byte for byte, into --into, listed (Animations not), as npc_port carries the merchant's")
    ap.add_argument('--into', default='Cell00084_DataBlock', help='the root cell (--carry)')
    ap.add_argument('--strip', action='append', default=[], metavar='CLASS', help="a component class cut out of the added Entities (the fiddle prop's WeaponComponent: ACR's reads two more Entity handles than AC3's; the flute and the bow are plain props without one)")
    ap.add_argument('--proven', action='append', default=[], metavar='CLASS')
    a = ap.parse_args(argv)
    repoint = {int(o, 0): int(n, 0) for o, n in (b.split('=') for b in a.builder)}
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}
    defined = {o['uid'] for p in payloads.values() for o in p.objects if o['has_uid']}
    unknown_in = build_static.unknown_checker(extra=a.proven)
    targets = convert.targets('ACR')
    rules = convert.RULES + [('scene mid byte', convert.EXTRA_RULES['scene mid byte'])]
    ids = convert.class_ids()
    instance = {ids[n] for n in ('Entity', 'EntityGroup')}
    report, counts, added = ([], Counter(), {})
    plan = {}
    acr_carry = {}
    for p in mapwalk.payloads('ACR', a.map):
        if p.rec.name in a.carry:
            acr_carry[p.rec.name] = p.objects
        if p.rec.name not in payloads:
            continue
        acr_uids = [o['uid'] if o['has_uid'] else None for o in p.objects]
        a_m_uids = []
        if xrefs.cls('ACR', p.objects[0]['type']) == 'GridCellDataBlock':
            _n, a_entries, a_m, _end = cell_list(p.objects[0]['body'])
            a_m_uids = [struct.unpack_from('<I', e, 2)[0] for e in a_entries[:a_m]]
        for i, o in enumerate(p.objects):
            if not o['has_uid'] or o['uid'] in defined:
                continue
            name = o['name'] or ''
            is_perf = o['type'] in instance and any((name.startswith(m) for m in a.entities))
            is_pack = name in a.packs
            if not (is_perf or is_pack):
                continue
            body, log = convert.convert(o['body'], targets, rules)
            if a.strip and o['type'] == ids['Entity']:
                import entity
                body, cut = entity.strip(body, lambda n, _b: n in a.strip)
                if cut:
                    log['stripped ' + '+'.join(cut)] = len(cut)
            sites = 0
            for old, new in repoint.items():
                ob, nb = (struct.pack('<I', old), struct.pack('<I', new))
                sites += body.count(ob)
                body = body.replace(ob, nb)
            bad = unknown_in(body)
            if bad:
                report.append(f"{p.rec.name}: {name}: not added ({', '.join(sorted(bad))})")
                counts['refused'] += 1
                continue
            listed = is_perf and o['uid'] in a_m_uids
            if listed:
                pos = a_m_uids.index(o['uid'])
                before, after = (list(reversed(a_m_uids[:pos])), a_m_uids[pos + 1:])
            else:
                before, after = ([], [])
            nb = name.encode('latin-1')
            obj = {'uid': o['uid'], 'has_uid': True, 'type': o['type'], 'filter': o['filter'], '_raw': struct.pack('<III', o['type'], o['body_size'] + len(body) - len(o['body']), len(nb)) + nb + body}
            plan.setdefault(p.rec.name, []).append((i, obj, listed, before, after, acr_uids))
            added[o['uid']] = (p.rec.name, name, listed, body)
            counts['performers' if is_perf else 'packs'] += 1
            report.append(f"{p.rec.name}: {name}: {('performer' if is_perf else 'pack')}, +{len(body) - len(o['body'])} bytes, rules {dict(((k, v) for k, v in log.items() if v))}, builder sites {sites}")
    carried = {}
    anim = ids['Animation']
    in_root = {o['uid']: o['body'] for o in payloads[a.into].objects if o['has_uid']}
    for fname in a.carry:
        built = {o['serial']: o for o in payloads[fname].objects}
        from_acr = there = 0
        for ao in acr_carry.get(fname, payloads[fname].objects):
            o = built.get(ao['serial'])
            if o is None:
                b, _log = convert.convert(ao['body'], targets, rules)
                o = dict(ao, body=b, body_size=ao['body_size'] + len(b) - len(ao['body']))
                from_acr += 1
            if a.strip and o['type'] == ids['Entity']:
                import entity
                sb, cut = entity.strip(o['body'], lambda n, _b: n in a.strip)
                if cut:
                    o = dict(o, body=sb, body_size=o['body_size'] + len(sb) - len(o['body']))
                    report.append(f"{fname}: {o['name']}: stripped {'+'.join(cut)}")
            if o['has_uid'] and o['uid'] in in_root:
                if in_root[o['uid']] != o['body']:
                    raise SystemExit(f"{fname}: {o['name']} {o['uid']:#010x} is in {a.into} already, with other bytes")
                carried[o['uid']] = o['body']
                counts['carried objects the root already held'] += 1
                there += 1
                continue
            obj = {'uid': o['uid'] if o['has_uid'] else None, 'has_uid': o['has_uid'], 'type': o['type'], 'filter': o['filter'], '_raw': mapwalk.object_bytes(o), '_carry': True, 'serial': o['serial']}
            plan.setdefault(a.into, []).append((None, obj, o['type'] != anim, None, None, None))
            if o['has_uid']:
                carried[o['uid']] = o['body']
            counts['carried objects'] += 1
        report.append(f'{fname}: {len(acr_carry.get(fname, payloads[fname].objects))} objects carried into {a.into}' + (f" ({from_acr} from ACR's file: not in the build)" if from_acr else '') + (f' ({there} there already)' if there else ''))
    for fname, items in plan.items():
        p = payloads[fname]
        n, entries, m, end = cell_list(p.objects[0]['body'])
        entries = list(entries)
        work = list(p.objects[1:])
        for i, obj, listed, before, after, acr_uids in sorted(items, key=lambda t: (t[0] is None, t[0] or 0)):
            if obj.get('_carry'):
                work.append(obj)
                if listed:
                    entries.append(b'\x01\x00' + struct.pack('<I', obj['uid']))
                    n += 1
                continue
            if listed:
                cur_m_uids = [struct.unpack_from('<I', x, 2)[0] for x in entries[:m]]
                idx, _anchor, _side = anchor_index(before, after, cur_m_uids)
                idx = m if idx is None else idx
                entries[idx:idx] = [b'\x01\x00' + struct.pack('<I', obj['uid'])]
                n += 1
                m += 1
            here = {o['uid']: k for k, o in enumerate(work) if o['has_uid']}
            prev = next((here[u] for u in reversed(acr_uids[:i]) if u in here), None)
            if prev is not None:
                pos = prev + 1
            else:
                pos = next((here[u] for u in acr_uids[i + 1:] if u in here), len(work))
            work.insert(pos, obj)
            named = refs(obj['_raw'])
            move = [k for k in range(pos) if work[k]['has_uid'] and work[k]['uid'] in named and (xrefs.cls('ACR', work[k]['type']) == 'MeshShape')]
            if move:
                moved = [work[k] for k in move]
                for k in reversed(move):
                    del work[k]
                pos -= len(move)
                work[pos + 1:pos + 1] = moved
                counts['MeshShapes moved after the object that uses them'] += len(moved)
        g = p.objects[0]['body'][:10] + struct.pack('<I', n) + b''.join(entries) + struct.pack('<I', m) + p.objects[0]['body'][end + 4:]
        objs = [(p.objects[0]['serial'], mapwalk.object_bytes(p.objects[0], g), p.objects[0]['filter'])]
        for o in work:
            objs.append((o.get('serial', o['uid']), o['_raw'], o['filter']) if '_raw' in o else (o['serial'], mapwalk.object_bytes(o), o['filter']))
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    written = ArchiveImage.read(dest)
    seen, all_defined = (Counter(), set())
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            head = set()
            if xrefs.cls('ACR', p.objects[0]['type']) == 'GridCellDataBlock':
                _n, e, m, _ = cell_list(p.objects[0]['body'])
                head = {struct.unpack_from('<I', x, 2)[0] for x in e[:m]}
            for o in p.objects:
                if o['has_uid']:
                    all_defined.add(o['uid'])
                if o['has_uid'] and o['uid'] in carried and (r.name == a.into):
                    v['carried, in the root byte for byte'] += o['body'] == carried[o['uid']]
                    v['carried, listed in the root'] += o['uid'] in set((struct.unpack_from('<I', x, 2)[0] for x in e))
                if o['has_uid'] and o['uid'] in added:
                    seen[o['uid']] += 1
                    listed = added[o['uid']][2]
                    v['listed as expected'] += (o['uid'] in head) == listed
                    v["holding a class AC3 can't read"] += bool(unknown_in(o['body']))
                    v['body as built'] += o['body'] == added[o['uid']][3]
    uni, acr = (inventory.universe('AC3'), set(targets))
    missing = Counter()
    for u, (_f, name, _l, body) in added.items():
        for t in refs(body):
            if t in acr and t not in all_defined and (t not in uni):
                missing[f'{name}: {t:#010x}'] += 1
    v['added'] = len(added)
    v['defined once'] = sum((1 for u in added if seen[u] == 1))
    v['named uids defined nowhere'] = len(missing)
    report.append(', '.join((f'{k} {c}' for k, c in counts.items())))
    report += [f'  undefined: {k}' for k in sorted(missing)]
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    ok = v['carried, in the root byte for byte'] == v['carried, listed in the root'] == len(carried) and (not v['entry key != first uid']) and (v['defined once'] == len(added) == v['listed as expected'] == v['body as built']) and (not v["holding a class AC3 can't read"])
    if not ok:
        raise SystemExit('verify failed')
if __name__ == '__main__':
    main(sys.argv[1:])
