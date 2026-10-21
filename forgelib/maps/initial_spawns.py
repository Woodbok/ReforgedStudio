"""forgelib/maps/initial_spawns.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
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
OUT = os.path.join(ROOT, 'out', 'maps')
BLOCK = 'DataBlock_ACFE_Training'
MATRIX = slice(10, 74)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--move', action='append', required=True, metavar='NAME=SOURCE')
    a = ap.parse_args(argv)
    moves = dict((m.split('=', 1) for m in a.move))
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    blocks = [p for p in payloads if p.rec.name == BLOCK]
    if len(blocks) != 1:
        raise SystemExit(f'{len(blocks)} files named {BLOCK}')
    p = blocks[0]
    by_name = {o['name']: i for i, o in enumerate(p.objects)}
    for n in list(moves) + list(moves.values()):
        if n not in by_name:
            raise SystemExit(f'no {n} in {BLOCK}')
    bodies, report = ({}, [])
    for name, source in moves.items():
        i, s = (by_name[name], by_name[source])
        b = bytearray(p.objects[i]['body'])
        old = struct.unpack_from('<3f', b, 58)
        b[MATRIX] = p.objects[s]['body'][MATRIX]
        bodies[i] = bytes(b)
        report.append(f"{name}: {tuple((round(x, 1) for x in old))} -> {source}'s {tuple((round(x, 1) for x in struct.unpack_from('<3f', b, 58)))}")
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
    before = {q.rec.index: q for q in payloads}
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            q = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            for j, o in enumerate(q.objects):
                old = before[r.index].objects[j]['body']
                if o['body'] == old:
                    continue
                ok = r.index == p.rec.index and j in bodies and (o['body'][MATRIX] == p.objects[by_name[moves[o['name']]]]['body'][MATRIX]) and (o['body'][:10] == old[:10]) and (o['body'][74:] == old[74:])
                v['spawns moved as planned' if ok else 'objects changed otherwise'] += 1
    v.setdefault('objects changed otherwise', 0)
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
