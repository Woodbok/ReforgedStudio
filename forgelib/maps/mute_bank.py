"""forgelib/maps/mute_bank.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import build_static
import convert
import mapwalk
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='S13_souk_as_plantation')
    ap.add_argument('--slot', default='AC3MP_Plantation')
    ap.add_argument('--archive', help="the archive's file name (default DataPC_<slot>.forge)")
    ap.add_argument('--prefix', default='S15')
    ap.add_argument('--bank', type=lambda s: int(s, 0), default=0)
    a = ap.parse_args(argv)
    archive = a.archive or f'DataPC_{a.slot}.forge'
    src = os.path.join(OUT, a.src, archive)
    ids = convert.class_ids()
    image = ArchiveImage.read(src)
    report = []
    with Forge(src) as f:
        rec = f.records[1]
        p = mapwalk.Payload(rec, f.read(rec))
        objs = []
        for o in p.objects:
            body = o['body']
            if o['type'] == ids['World']:
                body, old = build_static.set_bank(body, a.bank)
                report.append(f"World {o['name']}: ambience bank 0x{old:08X} -> 0x{a.bank:08X}")
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
    d = mapwalk.rebuild(objs, p.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, archive)
    size = write(dest, image)
    with Forge(dest) as f:
        r = f.records[1]
        q = mapwalk.Payload(r, f.read(r))
        w = next((o for o in q.objects if o['type'] == ids['World']))
        k = w['body'].find(struct.pack('<I', ids['WwiseID']))
        bank = struct.unpack_from('<I', w['body'], k + 4)[0]
        same = sum((1 for x, y in zip(p.objects, q.objects) if x['body'] == y['body']))
    key_ok = struct.unpack_from('<I', ArchiveImage.read(dest).files[r.index].entry, 8)[0] == q.objects[0]['uid']
    ok = bank == a.bank and same == len(p.objects) - 1 and key_ok
    report.append(f"verify: World bank now 0x{bank:08X} ({bank == a.bank}); {same}/{len(p.objects)} World-file objects unchanged (expect all but 1); entry key == first uid {key_ok}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
