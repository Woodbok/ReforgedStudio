"""forgelib/maps/layer_tails.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import mapwalk
import spawn_layers
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
TAIL = bytes.fromhex('e19315e300')

def remap_tails(body, mapping):
    b = bytearray(body)
    hits = {}
    i = b.find(TAIL)
    while i >= 0:
        u = struct.unpack_from('<I', b, i + 5)[0]
        if u in mapping:
            struct.pack_into('<I', b, i + 5, mapping[u])
            hits[u] = hits.get(u, 0) + 1
        i = b.find(TAIL, i + 1)
    return (bytes(b), hits)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='S13_souk_as_plantation')
    ap.add_argument('--slot', default='AC3MP_Plantation')
    ap.add_argument('--archive', default=None, help='archive file name in the build folder (default DataPC_<slot>.forge; a standalone map keeps its own, e.g. DataPC_ACFE_Souk.forge, while --slot stays the stock archive borrowed from)')
    ap.add_argument('--map', default='Souk')
    ap.add_argument('--prefix', default='S16')
    a = ap.parse_args(argv)
    archive = a.archive or f'DataPC_{a.slot}.forge'
    src = os.path.join(OUT, a.src, archive)
    mapping, names = spawn_layers.layer_mapping(a.map)
    report = [f'{n} (0x{u:08X}) -> {t} (0x{mapping[u]:08X})' for u, (n, t) in sorted(names.items(), key=lambda kv: kv[1])]
    image = ArchiveImage.read(src)
    totals, changed_files, changed_objects, touched = ({}, 0, 0, [])
    with Forge(src) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            objs, dirty = ([], False)
            for o in p.objects:
                body, hits = remap_tails(o['body'], mapping)
                if hits:
                    dirty = True
                    changed_objects += 1
                    touched.append((r.index, o['name']))
                    for u, n in hits.items():
                        totals[u] = totals.get(u, 0) + n
                objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
            if not dirty:
                continue
            d = mapwalk.rebuild(objs, p.filter_table)
            assert len(d) == len(p.data)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, r.index, payload)
            changed_files += 1
    for u, n in sorted(totals.items(), key=lambda kv: names[kv[0]][0]):
        report.append(f'tail refs {names[u][0]} -> {names[u][1]}: {n}')
    report.append(f'{changed_objects} objects in {changed_files} files rewritten')
    report += [f'  {name}' for _i, name in touched if name and ('OOB' in name or 'Spawn' in name)]
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, archive)
    size = write(dest, image)
    left = bad_keys = diff_shape = 0
    out_image = ArchiveImage.read(dest)
    with Forge(src) as fs, Forge(dest) as fd:
        for rs, rd in zip(fs.records, fd.records):
            if rs.name == 'GlobalMetaFile' or not rs.size:
                continue
            q = mapwalk.Payload(rd, fd.read(rd))
            for o in q.objects:
                b = o['body']
                i = b.find(TAIL)
                while i >= 0:
                    left += struct.unpack_from('<I', b, i + 5)[0] in mapping
                    i = b.find(TAIL, i + 1)
            if q.objects and q.objects[0]['uid'] is not None:
                bad_keys += struct.unpack_from('<I', out_image.files[rd.index].entry, 8)[0] != q.objects[0]['uid']
            if rd.index in {i for i, _n in touched}:
                p = mapwalk.Payload(rs, fs.read(rs))
                diff_shape += [len(o['body']) for o in p.objects] != [len(o['body']) for o in q.objects]
    report.append(f'verify: map-specific layers left in entity tails {left}; files whose object sizes changed {diff_shape}; entry keys != first object uid {bad_keys}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
