"""forgelib/maps/tex_group.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import convert
import mapwalk
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
CATEGORY = 73

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--from', dest='old', type=int, action='append', required=True)
    ap.add_argument('--to', dest='new', type=int, required=True)
    a = ap.parse_args(argv)
    tex = convert.class_ids()['TextureMap']
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    changed, names = ({}, [])
    for p in payloads:
        bodies = {}
        for i, o in enumerate(p.objects):
            b = o['body']
            if o['type'] != tex or len(b) < CATEGORY + 4 or struct.unpack_from('<I', b, CATEGORY)[0] not in a.old:
                continue
            nb = bytearray(b)
            struct.pack_into('<I', nb, CATEGORY, a.new)
            bodies[i] = bytes(nb)
            names.append(o['name'])
        if not bodies:
            continue
        changed[p.rec.name] = set(bodies)
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
            for i in changed.get(r.name, ()):
                old, new = (before[r.name, i], p.objects[i]['body'])
                v['textures set as planned'] += struct.unpack_from('<I', new, CATEGORY)[0] == a.new and old[:CATEGORY] == new[:CATEGORY] and (old[CATEGORY + 4:] == new[CATEGORY + 4:])
    report = [f"category {' / '.join(map(str, a.old))} -> {a.new}: {len(names)} textures: " + ', '.join(sorted(set(names))), f'verify: {dict(v)}']
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
