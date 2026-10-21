"""forgelib/maps/lod_scale.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
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
SLOT = bytes.fromhex('9a3160a3')

def slots(body):
    out = []
    k = body.find(SLOT)
    while k > 0:
        if body[k - 5] == 0 and body[k + 4] in (1, 3) and (k + 14 <= len(body)):
            out.append((k + 10, struct.unpack_from('<f', body, k + 10)[0]))
        k = body.find(SLOT, k + 4)
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N22_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='N23')
    ap.add_argument('--match', action='append', default=[], help='LODSelectors whose name is exactly this')
    ap.add_argument('--all', action='store_true', help="every LODSelector in the archive (N51: less popping and fewer atlas-textured far LODs up close; the distances are AC3's own, 20 / 40 / 80 / 160 m)")
    ap.add_argument('--scale', type=float, default=10.0)
    a = ap.parse_args(argv)
    if not a.match and (not a.all):
        ap.error('give --match NAME or --all')
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    shared = set()
    if a.all:
        import inventory
        shared = {u for u, homes in inventory.universe('AC3').items() if any((h[0] in inventory.SHARED for h in homes))}
    report, changed = ([], {})
    skipped = Counter()
    for p in payloads:
        for o in p.objects:
            if a.all and o['has_uid'] and (o['uid'] in shared) and (xrefs.cls('ACR', o['type']) == 'LODSelector'):
                skipped[o['name']] += 1
        hits = [i for i, o in enumerate(p.objects) if xrefs.cls('ACR', o['type']) == 'LODSelector' and (a.all or o['name'] in a.match) and (not (o['has_uid'] and o['uid'] in shared))]
        if not hits:
            continue
        objs = []
        for i, o in enumerate(p.objects):
            body = o['body']
            if i in hits:
                b = bytearray(body)
                old = slots(body)
                for off, d in old:
                    if d > 0 and math.isfinite(d):
                        struct.pack_into('<f', b, off, d * a.scale)
                body = bytes(b)
                changed[p.rec.name, o['uid']] = [d for _o, d in old]
                report.append(f"{p.rec.name}: {o['name']}: {[d for _o, d in old]} -> {[d for _o, d in slots(body)]}")
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    written = ArchiveImage.read(dest)
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            for o in p.objects:
                old = changed.get((r.name, o['uid']))
                if old is not None:
                    new = [d for _o, d in slots(o['body'])]
                    ok = len(new) == len(old) and all((abs(n - (d * a.scale if d > 0 else d)) < 0.001 for n, d in zip(new, old)))
                    v['scaled as planned'] += ok
                    v['not as planned'] += not ok
    report.append(f"LODSelectors scaled: {len(changed)}; left as AC3's shared archives define them: {sum(skipped.values())} ({', '.join(sorted(skipped)[:8])}); verify: {dict(v)}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
