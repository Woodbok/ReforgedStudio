"""forgelib/maps/training_block.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import build_static
import mapwalk
import spawn_layers
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
BLOCK = 'DataBlock_ACFE_Training'
TRANSFORM = slice(10, 74)
DROP_PREFIX = 'PH_AC3MP_Plan_OOB'

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='S11_souk_as_plantation')
    ap.add_argument('--slot', default='AC3MP_Plantation')
    ap.add_argument('--archive', default=None, help='archive file name in the build folder (default DataPC_<slot>.forge; a standalone map keeps its own, e.g. DataPC_ACFE_Souk.forge, while --slot stays the stock archive borrowed from)')
    ap.add_argument('--prefix', default='S13')
    ap.add_argument('--keep-transforms', action='store_true', help="leave the 16 spawn points at the slot's own coordinates (S14: does the spawn position cause the post-selection hang?)")
    a = ap.parse_args(argv)
    archive = a.archive or f'DataPC_{a.slot}.forge'
    donor = f'DataPC_{a.slot}.forge'
    src = os.path.join(OUT, a.src, archive)
    report = []
    with Forge(mapwalk.archive_path('AC3', donor)) as f:
        rec = next((r for r in f.records if r.name == BLOCK))
        slot = mapwalk.Payload(rec, f.read(rec))
    image = ArchiveImage.read(src)
    training = spawn_layers.layer_mapping('Souk')[0]
    acfe_training = next((v for k, v in training.items() if spawn_layers.layer_mapping('Souk')[1][k][1] == 'ACFE_Training'))
    mine, spots = (None, [])
    with Forge(src) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            if r.name == BLOCK:
                mine = (r, p.objects[0]['uid'])
            if not p.filter_table:
                continue
            sets = spawn_layers.filter_sets(p.filter_table)
            for o in p.objects:
                k = spawn_layers.spawn_kind(o['body']) if xrefs.cls('ACR', o['type']) == 'Entity' else None
                if k and o['filter'] is not None and (acfe_training in sets[o['filter']]):
                    spots.append((o['name'], o['body'][TRANSFORM]))
    if mine is None or not spots:
        raise SystemExit(f"need the map's own {BLOCK} and at least one Training spawn")
    rec, my_uid = mine
    report.append(f"{len(spots)} Training spawn positions from the map: {', '.join((n for n, _ in spots))}")
    dropped = {o['uid'] for o in slot.objects if o['name'].startswith(DROP_PREFIX) and xrefs.cls('AC3', o['type']) == 'Entity'}
    objs, i = ([], 0)
    for pos, o in enumerate(slot.objects):
        body = o['body']
        if o['has_uid'] and o['uid'] in dropped:
            report.append(f"removed {o['name']}")
            continue
        serial = o['serial']
        if pos == 0:
            body, gone = build_static.clean_cell(body, dropped)
            body = body[:2] + struct.pack('<I', my_uid) + body[6:]
            serial = my_uid
            report.append(f"block object re-uid'd 0x{o['uid']:08X} -> 0x{my_uid:08X}; {gone} list entries removed")
        elif a.keep_transforms and (spawn_layers.spawn_kind(body) if xrefs.cls('AC3', o['type']) == 'Entity' else None):
            report.append(f"{o['name']}: kept at the slot's coordinates")
        elif spawn_layers.spawn_kind(body) if xrefs.cls('AC3', o['type']) == 'Entity' else None:
            name, tf = spots[i % len(spots)]
            before = struct.unpack_from('<3f', body, 58)
            body = body[:TRANSFORM.start] + tf + body[TRANSFORM.stop:]
            after = struct.unpack_from('<3f', body, 58)
            report.append(f"{o['name']}: ({before[0]:.1f}, {before[1]:.1f}, {before[2]:.1f}) -> ({after[0]:.1f}, {after[1]:.1f}, {after[2]:.1f}) from {name}")
            i += 1
        objs.append((serial, mapwalk.object_bytes(o, body), o['filter']))
    d = mapwalk.rebuild(objs, slot.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, archive)
    size = write(dest, image)
    written = ArchiveImage.read(dest)
    with Forge(dest) as f:
        r = f.records[rec.index]
        p = mapwalk.Payload(r, f.read(r))
        key = struct.unpack_from('<I', written.files[r.index].entry, 8)[0]
        kinds = [spawn_layers.spawn_kind(o['body']) for o in p.objects if xrefs.cls('AC3', o['type']) == 'Entity']
    initial = sum((1 for k in kinds if k and k[0] == 2))
    report.append(f"verify: {r.name}, {len(p.objects)} objects, key == first uid {key == p.objects[0]['uid']}, name == first object {p.objects[0]['name'] == r.name}, initial spawns {initial}, respawns {sum((1 for k in kinds if k and k[0] == 0))}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
