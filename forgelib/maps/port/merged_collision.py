"""forgelib/maps/port/merged_collision.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import convert
import inventory
import lift_dropped
import mapwalk
import xrefs
from forge import Forge
from tree_stubs import merged_members
OUT = os.path.join(ROOT, 'out', 'maps')

def load(path):
    with Forge(path) as f:
        return {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}

def merged(payloads, game, multi):
    return {o['uid']: (n, o['body']) for n, p in payloads.items() for o in p.objects if o['has_uid'] and xrefs.cls(game, o['type']) == 'Entity' and merged_members(o['body'], multi)}

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--map', required=True, help='the ACR archive')
    ap.add_argument('--prefix', required=True)
    a = ap.parse_args(argv)
    multi = struct.pack('<I', convert.class_ids()['MultiInertComponent'])
    src = load(os.path.join(OUT, a.src, a.archive))
    defined = {o['uid'] for p in src.values() for o in p.objects if o['has_uid']}
    acr = merged(dict(((p.rec.name, p) for p in mapwalk.payloads('ACR', a.map))), 'ACR', multi)
    missing = {u: v for u, v in acr.items() if u not in defined and v[0] in src}
    wanted = {u for u, (_n, body) in missing.items() if all((m in defined for m in merged_members(body, multi)))}
    report = [f'merged-collision entities: ACR {len(acr)}, in the build {len(acr) - len(missing)}, missing {len(missing)}' + ''.join((f"; {n} {u:#010x} ({len(merged_members(b, multi))} members, {('lifting' if u in wanted else 'NOT lifted: a member is undefined')})" for u, (n, b) in sorted(missing.items(), key=lambda x: x[1][0])))]
    prefix, rest = (a.prefix, a.src.split('_', 1)[1])
    dest_dir = os.path.join(OUT, f'{prefix}_{rest}')
    orig = mapwalk.payloads

    def narrowed(game, archive, skip_errors=False):
        for p in orig(game, archive, skip_errors):
            p.objects = [o for i, o in enumerate(p.objects) if i == 0 or (o['has_uid'] and o['uid'] in wanted)]
            yield p
    mapwalk.payloads = narrowed
    try:
        lift_dropped.main(['--src', a.src, '--archive', a.archive, '--map', a.map, '--prefix', prefix, '--entities', ''])
    finally:
        mapwalk.payloads = orig
    out = load(os.path.join(dest_dir, a.archive))
    count = Counter((o['uid'] for p in out.values() for o in p.objects if o['has_uid']))
    built = set(count)
    shared = set(inventory.SHARED)
    u3 = inventory.universe('AC3')
    targets = convert.targets('ACR')
    v = Counter()
    for u, (n, body) in acr.items():
        v['ACR merged entities defined once'] += count[u] == 1
    for u in wanted:
        n, body = acr[u]
        o = next((o for o in out[n].objects if o['has_uid'] and o['uid'] == u))
        v['lifted, as converted'] += o['body'] == convert.convert(body, targets)[0]
        mem = merged_members(o['body'], multi)
        v['members'] += len(mem)
        v['members undefined'] += sum((1 for m in mem if m not in built))
        shapes = [struct.unpack_from('<I', o['body'], j + 2)[0] for j in range(len(o['body']) - 6) if o['body'][j:j + 2] == b'\x01\x00']
        v['01 00 references undefined'] += sum((1 for s in shapes if s >= 268435456 and s not in built and (not any((h[0] in shared for h in u3.get(s, [])))) and (s in inventory.universe('ACR'))))
    new = set(count) - defined
    v['objects added'] = len(new)
    v["objects added that aren't the wanted entities"] = len(new - wanted)
    touched = {missing[u][0] for u in wanted}
    v['other files byte-identical'] = all(([o['body'] for o in out[n].objects] == [o['body'] for o in src[n].objects] for n in src if n not in touched))
    ok = v['ACR merged entities defined once'] == len(acr) - len(set(missing) - wanted) and v['lifted, as converted'] == len(wanted) and (not v['members undefined']) and (not v['01 00 references undefined']) and (not v["objects added that aren't the wanted entities"]) and v['other files byte-identical']
    report.append(f"verify: {dict(v)}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(dest_dir, 'report.txt'), 'a', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
