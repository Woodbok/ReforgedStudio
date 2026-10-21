"""forgelib/maps/drop_entities.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import build_static
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N9_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='N10')
    ap.add_argument('--match', action='append', required=True, help='drop Entities whose name starts with this')
    ap.add_argument('--groups', action='store_true', help='also EntityGroups (default: Entities only)')
    ap.add_argument('--any-class', action='store_true', help="objects of any class whose name starts with --match (a dropped group's own resources: San Marco VE2's soft body)")
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]

    def hit(o):
        return (a.any_class or xrefs.cls('ACR', o['type']) in (('Entity', 'EntityGroup') if a.groups else ('Entity',))) and any(((o['name'] or '').startswith(m) for m in a.match))
    dropped, report, lists = (set(), [], Counter())
    for p in payloads:
        gone = [o for o in p.objects[1:] if hit(o)]
        if not gone:
            continue
        uids = {o['uid'] for o in gone}
        objs = []
        for i, o in enumerate(p.objects):
            if o in gone:
                continue
            body = o['body']
            if i == 0 and xrefs.cls('ACR', o['type']) == 'GridCellDataBlock':
                body, n = build_static.clean_cell(body, uids)
                lists[p.rec.name] += n
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
        dropped |= uids
        report.append(f"{p.rec.name}: -{len(gone)} ({', '.join((o['name'] for o in gone))}); list entries -{lists[p.rec.name]}")
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    written = ArchiveImage.read(dest)
    needles = [struct.pack('<I', u) for u in dropped]
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            for o in p.objects:
                if o['has_uid'] and o['uid'] in dropped:
                    v['dropped uid still defined'] += 1
                elif xrefs.cls('ACR', o['type']) == 'GridCellDataBlock' and any((nb in o['body'] for nb in needles)):
                    v['dropped uid still listed'] += 1
    report.append(f'dropped {len(dropped)} entities; verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
