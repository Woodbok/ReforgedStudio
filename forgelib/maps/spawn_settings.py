"""forgelib/maps/spawn_settings.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
SPAWN_SETTINGS = struct.pack('<I', 4144040988)
POSITION = 12

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--dx', type=float, default=0.0)
    ap.add_argument('--dy', type=float, default=128.0)
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    p, i = next(((p, i) for p in payloads for i, o in enumerate(p.objects) if xrefs.cls('ACR', o['type']) == 'World'))
    b = p.objects[i]['body']
    k = b.find(SPAWN_SETTINGS)
    if k < 0 or b.find(SPAWN_SETTINGS, k + 1) >= 0:
        raise SystemExit('SpawnSettings not found exactly once in the World record')
    old = struct.unpack_from('<3f', b, k + POSITION)
    nb = bytearray(b)
    struct.pack_into('<2f', nb, k + POSITION, old[0] + a.dx, old[1] + a.dy)
    objs = [(o['serial'], mapwalk.object_bytes(o, bytes(nb) if j == i else o['body']), o['filter']) for j, o in enumerate(p.objects)]
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
            src_p = before[r.index]
            for j, o in enumerate(q.objects):
                ob = src_p.objects[j]['body']
                if ob == o['body']:
                    continue
                diff = [x for x in range(len(ob)) if ob[x] != o['body'][x]] if len(ob) == len(o['body']) else None
                v['objects changed as planned'] += r.index == p.rec.index and j == i and (diff is not None) and all((k + POSITION <= x < k + POSITION + 8 for x in diff))
                v['objects changed otherwise'] += not (r.index == p.rec.index and j == i)
    new = struct.unpack_from('<3f', nb, k + POSITION)
    report = [f'SpawnSettings position {tuple((round(x, 2) for x in old))} -> {tuple((round(x, 2) for x in new))}', f'verify: {dict(v)}']
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
