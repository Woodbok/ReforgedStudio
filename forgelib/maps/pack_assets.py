"""forgelib/maps/pack_assets.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
from merge_cells import cell_list
OUT = os.path.join(ROOT, 'out', 'maps')
PACK = ('Mesh', 'TextureMap')
AC3_MAX_OBJECTS = 1622

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N8b_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--into', default='Cell01364_DataBlock')
    ap.add_argument('--prefix', default='N9')
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    lib = next((p for p in payloads if p.rec.name == a.into))
    have = {o['uid'] for o in lib.objects if o['has_uid']}
    n, entries, m, end = cell_list(lib.objects[0]['body'])
    listed = {struct.unpack_from('<I', e, 2)[0] for e in entries}
    packed, files = ([], 0)
    for p in payloads:
        if xrefs.cls('ACR', p.objects[0]['type']) not in PACK:
            continue
        files += 1
        for o in p.objects:
            if o['has_uid'] and o['uid'] not in have:
                packed.append(o)
                have.add(o['uid'])
    add = [b'\x01\x00' + struct.pack('<I', o['uid']) for o in packed if o['uid'] not in listed]
    g = lib.objects[0]['body']
    g = g[:10] + struct.pack('<I', n + len(add)) + b''.join(entries) + b''.join(add) + g[end:]
    objs = [(lib.objects[0]['serial'], mapwalk.object_bytes(lib.objects[0], g), lib.objects[0]['filter'])]
    objs += [(o['serial'], mapwalk.object_bytes(o), o['filter']) for o in lib.objects[1:]]
    objs += [(o['serial'], mapwalk.object_bytes(o), None) for o in packed]
    if len(objs) > AC3_MAX_OBJECTS:
        raise SystemExit(f"{a.into} would hold {len(objs)} objects, over AC3's largest ({AC3_MAX_OBJECTS})")
    d = mapwalk.rebuild(objs, lib.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, lib.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    cls = Counter((xrefs.cls('ACR', o['type']) for o in packed))
    report = [f"{a.into}: +{len(packed)} objects from {files} standalone files ({', '.join((f'{k} {v}' for k, v in cls.most_common()))}; {sum((len(o['body']) for o in packed)) / 1000000.0:.1f} MB); list {n} -> {n + len(add)} (M {m} unchanged); {len(objs)} objects, {len(d) / 1000000.0:.1f} MB decompressed"]
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
            if r.name == a.into:
                count = Counter((o['uid'] for o in p.objects if o['has_uid']))
                bodies = {o['uid']: o['body'] for o in p.objects if o['has_uid']}
                n2, e2, m2, _ = cell_list(p.objects[0]['body'])
                l2 = {struct.unpack_from('<I', e, 2)[0] for e in e2}
                v['packed, identical, once'] = sum((1 for o in packed if bodies.get(o['uid']) == o['body'] and count[o['uid']] == 1))
                v['packed and listed'] = sum((1 for o in packed if o['uid'] in l2))
                v['instances unchanged'] = e2[:m2] == entries[:m] and m2 == m
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
