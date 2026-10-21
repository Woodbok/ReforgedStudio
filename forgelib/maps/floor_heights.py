"""forgelib/maps/floor_heights.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
from regrid import FLOOR_HEIGHT_ID, NEW_ORIGIN, NEW_WIDTH, OLD_ORIGIN, OLD_WIDTH
OUT = os.path.join(ROOT, 'out', 'maps')

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--dy', type=int, default=128)
    ap.add_argument('--dx', type=int, default=0)
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    p, i = next(((p, i) for p in payloads for i, o in enumerate(p.objects) if xrefs.cls('ACR', o['type']) == 'World'))
    w = bytearray(p.objects[i]['body'])
    old_side, new_side = (OLD_WIDTH // 8, NEW_WIDTH // 8)
    fh = w.find(struct.pack('<I', FLOOR_HEIGHT_ID))
    if fh < 0 or w.find(struct.pack('<I', FLOOR_HEIGHT_ID), fh + 1) >= 0:
        raise SystemExit('FloorHeightData not found exactly once in the World record')
    n = struct.unpack_from('<I', w, fh + 4)[0]
    tail = fh + 8 + n
    dims = struct.unpack_from('<2I', w, tail + 28)
    origin = struct.unpack_from('<2f', w, tail + 12)
    if n != old_side ** 2 or dims != (new_side, new_side) or origin != (float(NEW_ORIGIN[0]), float(NEW_ORIGIN[1])):
        raise SystemExit(f'FloorHeightData: {n} samples under a {dims} header at {origin}: not a regridded, uncropped Souk')
    heights = bytes(w[fh + 8:tail])
    c0, r0 = ((NEW_ORIGIN[0] - a.dx - OLD_ORIGIN[0]) // 8, (NEW_ORIGIN[1] - a.dy - OLD_ORIGIN[1]) // 8)
    cropped = b''.join((heights[(r0 + r) * old_side + c0:(r0 + r) * old_side + c0 + new_side] for r in range(new_side)))
    w[fh + 4:tail] = struct.pack('<I', new_side ** 2) + cropped
    objs = [(o['serial'], mapwalk.object_bytes(o, bytes(w) if j == i else o['body']), o['filter']) for j, o in enumerate(p.objects)]
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
                if o['body'] == before[r.index].objects[j]['body']:
                    continue
                if r.index == p.rec.index and j == i:
                    b = o['body']
                    v['FloorHeightData as planned'] += struct.unpack_from('<I', b, fh + 4)[0] == new_side ** 2 and b[fh + 8:fh + 8 + new_side ** 2] == cropped and (b[fh + 8 + new_side ** 2:] == heights_tail(before[r.index].objects[j]['body'], tail))
                else:
                    v['objects changed otherwise'] += 1
    v.setdefault('objects changed otherwise', 0)
    report = [f'FloorHeightData {old_side} x {old_side} -> {new_side} x {new_side} (old rows {r0}-{r0 + new_side - 1}, columns {c0}-{c0 + new_side - 1})', f'verify: {dict(v)}']
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh_:
        fh_.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))

def heights_tail(body, tail):
    return body[tail:]
if __name__ == '__main__':
    main(sys.argv[1:])
