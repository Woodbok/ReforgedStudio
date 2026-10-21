"""forgelib/maps/port/dlc_world.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import exe_registry
import mapwalk
import world
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
WORLD = 4223024711

def class_id(name):
    return next((t for t, v in exe_registry.load(exe_registry.AC3)[0].items() if v == name))

def count_at(body):
    import world_list
    return world_list.list_start(body)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--map', required=True, help='the ACR archive')
    ap.add_argument('--prefix', required=True)
    a = ap.parse_args(argv)
    dlc = struct.pack('<I', class_id('DLCWorldComponent'))
    acr = next((o['body'] for p in mapwalk.payloads('ACR', a.map, skip_errors=True) for o in p.objects if o['type'] == WORLD))
    acr_count = struct.unpack_from('<I', acr, count_at(acr))[0]
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    report = [f"ACR World: {acr_count} components, DLCWorldComponent {('yes' if dlc in acr else 'no')}"]
    changed = None
    with Forge(src) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            p = mapwalk.Payload(rec, f.read(rec))
            w = next((o for o in p.objects if o['type'] == WORLD), None)
            if w is None:
                continue
            at = count_at(w['body'])
            old = struct.unpack_from('<I', w['body'], at)[0]
            if dlc not in acr or dlc in w['body']:
                report.append(f'{rec.name}: count {old}, nothing to do')
                break
            new = acr_count - 1 + 1
            body = w['body'][:at] + struct.pack('<I', new) + w['body'][at + 4:]
            report.append(f"{rec.name}: component count {old} -> {new} (the DLCWorldComponent went with the World's tail)")
            objs = [(o['serial'], mapwalk.object_bytes(o, body if o is w else None), o['filter']) for o in p.objects]
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, rec.index, payload)
            changed = (rec.index, w['body'], new, at)
            break
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = True
    with Forge(dest) as fo, Forge(src) as fi:
        others = all((fo.read(x) == fi.read(y) for x, y in zip(fo.records, fi.records) if changed is None or x.index != changed[0]))
        if changed:
            q = mapwalk.Payload(fo.records[changed[0]], fo.read(fo.records[changed[0]]))
            nb = next((o['body'] for o in q.objects if o['type'] == WORLD))
            ob, new, at = (changed[1], changed[2], changed[3])
            ok &= len(nb) == len(ob) and nb[:at] == ob[:at] and (nb[at + 4:] == ob[at + 4:])
            ok &= struct.unpack_from('<I', nb, at)[0] == new == acr_count and dlc not in nb
    ok &= others
    report.append(f"verify: World only its count changed, count = ACR's {acr_count} (- DLC + MapEventManager), no DLCWorldComponent left; every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
