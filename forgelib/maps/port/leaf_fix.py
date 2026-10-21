"""forgelib/maps/port/leaf_fix.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import re
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from leaf_index import NONE, morton, records
from relocate_cells import gcdb_node
OUT = os.path.join(ROOT, 'out', 'maps')
GRID = re.compile(b'\\xed\\x83\\xb0\\x7d\\x5f\\x3f')

def family(name):
    return re.sub('(_\\d+[A-Za-z]?)+$|\\s*\\d+$', '', (name or '').strip())

def grid_side(payloads):
    worlds = [p.objects[0]['body'] for p in payloads if xrefs.cls('AC3', p.objects[0]['type']) == 'World']
    if len(worlds) != 1:
        raise SystemExit(f'{len(worlds)} World files')
    k = GRID.search(worlds[0])
    if not k:
        raise SystemExit("the World's int GridLayout not found")
    leaf, ox, oy, zero, side = struct.unpack_from('<5i', worlds[0], k.end())
    if leaf != 32 or zero != 0 or side not in (4, 8, 16, 32):
        raise SystemExit(f'int GridLayout not as expected: {(leaf, ox, oy, zero, side)}')
    return side

def wanted(p, side):
    if p.rec.name.startswith('Cell') and xrefs.cls('AC3', p.objects[0]['type']) == 'GridCellDataBlock':
        node = struct.unpack_from('<I', p.objects[0]['body'], gcdb_node(p.objects[0]['body']))[0]
        if node < side * side:
            row, col = divmod(node, side)
            return morton(col, row)
    return NONE

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--archive', required=True)
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    side = grid_side(payloads)
    stats, fams, plan = (Counter(), Counter(), {})
    for p in payloads:
        want = wanted(p, side)
        bodies = {}
        for i, o in enumerate(p.objects):
            nb = None
            for k in records(o['body']):
                old = struct.unpack_from('<H', o['body'], k)[0]
                new = NONE if old == NONE else want
                stats['records'] += 1
                if new == old:
                    continue
                nb = nb or bytearray(o['body'])
                struct.pack_into('<H', nb, k, new)
                stats['changed'] += 1
                fams[f"{family(o['name'])} {old} -> {(new if new != NONE else 'FFFF')}"] += 1
            if nb is not None:
                bodies[i] = bytes(nb)
        if not bodies:
            continue
        plan[p.rec.index] = bodies
        objs = [(o['serial'], mapwalk.object_bytes(o, bodies.get(i, o['body'])), o['filter']) for i, o in enumerate(p.objects)]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    before = {(p.rec.index, i): o['body'] for p in payloads for i, o in enumerate(p.objects)}
    with Forge(dest) as f, Forge(src) as g:
        old_recs = {r.index: r for r in g.records}
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            if r.index not in plan:
                v['untouched files byte-identical'] += f.read(r) == g.read(old_recs[r.index])
                continue
            p = mapwalk.Payload(r, f.read(r))
            want = wanted(p, side)
            for i, o in enumerate(p.objects):
                ks = records(o['body'])
                for k in ks:
                    x = struct.unpack_from('<H', o['body'], k)[0]
                    v['records out of range'] += x != NONE and x >= side * side
                    v['records on another leaf'] += x != NONE and x != want
                old, new = (before[r.index, i], o['body'])
                if i in plan[r.index]:
                    diff = [j for j in range(len(new)) if old[j] != new[j]] if len(old) == len(new) else None
                    v['objects edited only in their leaf u16s'] += diff is not None and all((any((k <= j < k + 2 for k in ks)) for j in diff))
                else:
                    v['other objects byte-identical'] += old == new
    untouched = len(payloads) - len(plan)
    others = sum((len(p.objects) for p in payloads if p.rec.index in plan)) - sum((len(b) for b in plan.values()))
    ok = v['untouched files byte-identical'] == untouched and v['records out of range'] == 0 and (v['records on another leaf'] == 0) and (v['objects edited only in their leaf u16s'] == sum((len(b) for b in plan.values()))) and (v['other objects byte-identical'] == others)
    report = [f"grid {side} x {side}: {stats['records']} records, {stats['changed']} changed in {sum((len(b) for b in plan.values()))} objects of {len(plan)} files"] + [f'   {n:4d} {k}' for k, n in fams.most_common(30)]
    report.append(f"verify: {dict(v)} of {untouched} untouched files, {others} other objects in the edited files: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
