"""forgelib/maps/port/stock_twins.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import collections
import os
import re
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import inventory
import mapwalk
import paths
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
CLASSES = ('Material', 'TextureMap', 'TextureSet', 'Skeleton', 'MaterialTemplate', 'Mesh', 'ContactTable', 'LODSelector', 'HumanSoundSet', 'RagdollNew')
CELL = re.compile('^Cell\\d+_DataBlock$')

def stock_bodies(uids):
    u3 = inventory.universe('AC3')
    want = collections.defaultdict(set)
    for u in uids:
        for h in u3.get(u, []):
            if h[0] in inventory.SHARED:
                want[h[0], h[1]].add(u)
                break
    out = {}
    for (arch, fname), us in want.items():
        with Forge(os.path.join(paths.AC3_SOURCE, arch)) as f:
            r = next((r for r in f.records if r.name == fname))
            for o in mapwalk.Payload(r, f.read(r)).objects:
                if o['has_uid'] and o['uid'] in us and (o['uid'] not in out):
                    out[o['uid']] = (arch, fname, o['body'])
    return out

def ac3_bodies(uids):
    u3 = inventory.universe('AC3')
    want = collections.defaultdict(set)
    for u in uids:
        for h in u3.get(u, []):
            want[h[0], h[1]].add(u)
    out = collections.defaultdict(set)
    by_arch = collections.defaultdict(dict)
    for (arch, fname), us in want.items():
        by_arch[arch][fname] = us
    for arch, files in by_arch.items():
        with Forge(os.path.join(paths.AC3_SOURCE, arch)) as f:
            for r in f.records:
                if r.name in files:
                    for o in mapwalk.Payload(r, f.read(r)).objects:
                        if o['has_uid'] and o['uid'] in files[r.name]:
                            out[o['uid']].add(o['body'])
    return out

def plan(files):
    defs = collections.defaultdict(list)
    for fname, p in files.items():
        for o in p.objects:
            if o['has_uid'] and xrefs.cls('AC3', o['type']) in CLASSES:
                defs[o['uid']].append((fname, o['body'], o['type'], o['name']))
    u3 = inventory.universe('AC3')
    in_shared = {u for u in defs if any((h[0] in inventory.SHARED for h in u3.get(u, [])))}
    cand = {u for u, L in defs.items() if u in in_shared or (len(L) > 1 and len({x[1] for x in L}) > 1)}
    if not cand:
        return {}
    stock = stock_bodies(cand & in_shared)
    ac3 = ac3_bodies({u for u in cand if u in u3})
    out = {}
    for u in sorted(cand):
        L = defs[u]
        known = ac3.get(u, set())
        wrong = [x for x in L if x[1] not in known]
        if not wrong or (not known and len({x[1] for x in L}) == 1):
            continue
        if u in stock:
            canon = stock[u][2]
        elif any((x[1] in known for x in L)):
            canon = next((x[1] for x in sorted(L, key=lambda x: not CELL.match(x[0])) if x[1] in known))
        else:
            canon = sorted(L, key=lambda x: (not CELL.match(x[0]), not x[0].startswith('DataBlock_')))[0][1]
        change = sorted({x[0] for x in L if x[1] != canon and x[1] not in known})
        if change:
            out[u] = (xrefs.cls('AC3', L[0][2]), L[0][3], canon, change)
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    with Forge(src) as f:
        files = {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}
    todo = plan(files)
    by_file = collections.defaultdict(dict)
    for u, (_c, _n, canon, change) in todo.items():
        for fname in change:
            by_file[fname][u] = canon
    image = ArchiveImage.read(src)
    report, changed = ([], {})
    for fname, repl in sorted(by_file.items()):
        p = files[fname]
        new = {id(o): repl[o['uid']] for o in p.objects if o['has_uid'] and o['uid'] in repl and (o['body'] != repl[o['uid']])}
        if not new:
            continue
        for o in p.objects:
            if id(o) in new:
                report.append(f"{fname}: {xrefs.cls('AC3', o['type'])} {o['name']} {o['uid']:#010x} ({len(o['body'])} B) -> the root's / AC3's ({len(new[id(o)])} B)")
        objs = [(o['serial'], mapwalk.object_bytes(o, new.get(id(o))), o['filter']) for o in p.objects]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
        changed[p.rec.index] = {o['uid'] for o in p.objects if id(o) in new}
    del files
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    with Forge(dest) as fo, Forge(src) as fi:
        others = all((fo.read(x) == fi.read(y) for x, y in zip(fo.records, fi.records) if x.index not in changed))
        other_objs = True
        for idx, uids in changed.items():
            q = mapwalk.Payload(fo.records[idx], fo.read(fo.records[idx]))
            qi = mapwalk.Payload(fi.records[idx], fi.read(fi.records[idx]))
            for o, oi in zip(q.objects, qi.objects):
                if not (o['has_uid'] and o['uid'] in uids) and o['body'] != oi['body']:
                    other_objs = False
        left = plan({r.name: mapwalk.Payload(r, fo.read(r)) for r in fo.records if r.name != 'GlobalMetaFile' and r.size})
    ok = not left and other_objs and others
    report.append(f"verify: {sum((len(v) for v in changed.values()))} objects in {len(changed)} files now the root's / AC3's bytes (uids still twinned {[hex(u) for u in left]}); their files' other objects unchanged {other_objs}; every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
