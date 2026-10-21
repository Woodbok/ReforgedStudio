"""forgelib/maps/leaf_index.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import re
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import mapwalk
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from relocate_cells import gcdb_node
OUT = os.path.join(ROOT, 'out', 'maps')
SIDE = 8
NONE = 65535
RECORD = re.compile(b'(?=(..)\\x00\\x00\\x80\\x3f....\\x76\\x34\\xec\\x4a)', re.S)

def morton(col, row):
    m = 0
    for i in range(8):
        m |= (col >> i & 1) << 2 * i | (row >> i & 1) << 2 * i + 1
    return m

def records(body):
    out = []
    for m in RECORD.finditer(body):
        k = m.start()
        if k >= 9 and all((x in (0, 1) for x in body[k - 5:k])) and (struct.unpack_from('<I', body, k - 9)[0] < 32):
            out.append(k)
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    plan, stats = ({}, Counter())
    for p in payloads:
        want = NONE
        if p.rec.name.startswith('Cell'):
            node = struct.unpack_from('<I', p.objects[0]['body'], gcdb_node(p.objects[0]['body']))[0]
            if node < SIDE * SIDE:
                row, col = divmod(node, SIDE)
                want = morton(col, row)
        bodies = {}
        for i, o in enumerate(p.objects):
            nb = None
            for k in records(o['body']):
                old = struct.unpack_from('<H', o['body'], k)[0]
                new = NONE if old == NONE else want
                stats['records'] += 1
                stats['leaf-cell records' if want != NONE else 'records elsewhere'] += 1
                if new == old:
                    continue
                nb = nb or bytearray(o['body'])
                struct.pack_into('<H', nb, k, new)
                stats['changed'] += 1
            if nb is not None:
                bodies[i] = bytes(nb)
        if not bodies:
            continue
        plan[p.rec.name] = (want, set(bodies))
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
    before = {(p.rec.name, i): o['body'] for p in payloads for i, o in enumerate(p.objects)}
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            want = plan.get(r.name, (None, ()))[0]
            for i, o in enumerate(p.objects):
                ks = records(o['body'])
                for k in ks:
                    x = struct.unpack_from('<H', o['body'], k)[0]
                    if x != NONE and x >= SIDE * SIDE:
                        v['records out of range'] += 1
                    elif x != NONE and want is not None and (x != want):
                        v['records on another leaf'] += 1
                if i in plan.get(r.name, (None, ()))[1]:
                    old, new = (before[r.name, i], o['body'])
                    diff = [j for j in range(len(new)) if old[j] != new[j]] if len(old) == len(new) else None
                    v['objects edited as planned'] += diff is not None and all((any((k <= j < k + 2 for k in ks)) for j in diff))
    v.setdefault('records out of range', 0)
    v.setdefault('records on another leaf', 0)
    report = [f'leaf index: {dict(stats)}; {len(plan)} files', f'verify: {dict(v)}']
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
