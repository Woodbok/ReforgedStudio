"""forgelib/maps/fake_spans.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
FAKES = 3332013873
ENTITY = struct.pack('<I', 159662430)
SPAN = struct.pack('<I', 3880219322)

def tiles(body):
    starts = []
    k = body.find(ENTITY)
    while k > 0:
        if body[k - 6:k - 4] == b'\x00\x01':
            starts.append(k - 6)
        k = body.find(ENTITY, k + 4)
    out = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(body)
        m = struct.unpack_from('<16f', body, s + 10)
        out.append((s, e, (round(m[12]), round(m[13]))))
    return out

def spans(body, s, e):
    out, k = ([], body.find(SPAN, s))
    while 0 < k < e:
        out.append(k)
        k = body.find(SPAN, k + 4)
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N21_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='N25')
    ap.add_argument('--tile', action='append', required=True, help='tile centre x,y (m), e.g. 64,-64')
    a = ap.parse_args(argv)
    want = {tuple((int(v) for v in t.split(','))) for t in a.tile}
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    report, done = ([], Counter())
    for p in payloads:
        hits = [i for i, o in enumerate(p.objects) if o['type'] == FAKES]
        if not hits:
            continue
        objs = []
        for i, o in enumerate(p.objects):
            body = o['body']
            if i in hits:
                b = bytearray(body)
                for s, e, xy in tiles(body):
                    if xy not in want:
                        continue
                    sp = spans(body, s, e)
                    tris = sum((struct.unpack_from('<I', body, k + 8)[0] for k in sp))
                    for k in sp:
                        struct.pack_into('<I', b, k + 8, 0)
                    done[xy] = len(sp)
                    report.append(f"{p.rec.name}: {o['name']}: tile {xy}: {len(sp)} spans, {tris} indices -> 0")
                assert len(b) == len(body)
                body = bytes(b)
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    missing = want - set(done)
    if missing:
        raise SystemExit(f'no stand-in tile at {sorted(missing)}')
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    written = ArchiveImage.read(dest)
    before = {o['uid'] if o['has_uid'] else o['serial']: o['body'] for p in payloads for o in p.objects if o['type'] == FAKES}
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            for o in p.objects:
                if o['type'] != FAKES:
                    continue
                old = before[o['uid'] if o['has_uid'] else o['serial']]
                for (s, e, xy), (s0, e0, _) in zip(tiles(o['body']), tiles(old)):
                    if xy in want:
                        v['chosen tiles, all spans 0'] += all((struct.unpack_from('<I', o['body'], k + 8)[0] == 0 for k in spans(o['body'], s, e)))
                    else:
                        v['other tiles unchanged'] += o['body'][s:e] == old[s0:e0]
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
