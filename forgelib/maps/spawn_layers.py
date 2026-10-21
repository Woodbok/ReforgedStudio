"""forgelib/maps/spawn_layers.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import re
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inventory
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from transplant import remap
OUT = os.path.join(ROOT, 'out', 'maps')
MODE_RENAMES = {'CTF': 'CTF_2'}

def layer_mapping(map_name):
    layers = {}
    for u, homes in inventory.universe('AC3').items():
        for _arc, _f, t, n in homes:
            if xrefs.cls('AC3', t) == 'DataLayer':
                layers.setdefault(n, u)
    rx = re.compile(f'^[A-Za-z0-9]+_{re.escape(map_name)}_(.+)$')
    out, names = ({}, {})
    for n, u in layers.items():
        m = rx.match(n)
        if not m:
            continue
        target = 'ACFE_' + MODE_RENAMES.get(m.group(1), m.group(1))
        if target in layers:
            out[u] = layers[target]
            names[u] = (n, target)
    return (out, names)

def filter_sets(table):
    import convert
    ids = convert.class_ids()
    dlf = struct.pack('<I', ids['DataLayerFilter'])
    dla = struct.pack('<I', ids['DataLayerAction'])
    return [{struct.unpack_from('<I', seg, 1)[0] for seg in part.split(dla)[1:] if len(seg) >= 5} for part in table.split(dlf)[1:]]

def spawn_kind(body):
    import entity
    comps = entity.components(body) or []
    for i, (s, e, n) in enumerate(comps):
        if n == 'MultiSpawnPlayerComponent':
            return (body[s + 11], body[s + 15])
    return None

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='S9_souk_as_plantation')
    ap.add_argument('--slot', default='AC3MP_Plantation')
    ap.add_argument('--archive', default=None, help='archive file name in the build folder (default DataPC_<slot>.forge; a standalone map keeps its own, e.g. DataPC_ACFE_Souk.forge, while --slot stays the stock archive borrowed from)')
    ap.add_argument('--map', default='Souk')
    ap.add_argument('--prefix', default='S10')
    ap.add_argument('--initial-for', nargs='*', default=['ACFE_Training'], help='modes that must have initial (type 2) spawns')
    a = ap.parse_args(argv)
    archive = a.archive or f'DataPC_{a.slot}.forge'
    src = os.path.join(OUT, a.src, archive)
    mapping, names = layer_mapping(a.map)
    report = [f'{n} (0x{u:08X}) -> {t} (0x{mapping[u]:08X})' for u, (n, t) in sorted(names.items(), key=lambda kv: kv[1])]
    layer_uid = {t: mapping[u] for u, (_n, t) in names.items()}
    image = ArchiveImage.read(src)
    tables = edits = moved = 0
    with Forge(src) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            if not p.filter_table:
                continue
            table, n = remap(p.filter_table, mapping)
            if not n:
                continue
            tables += 1
            edits += sum(n.values())
            sets = filter_sets(table)
            kinds = {id(o): spawn_kind(o['body']) for o in p.objects}
            filt = {id(o): o['filter'] for o in p.objects}
            for mode in a.initial_for:
                lu = layer_uid.get(mode)
                if lu is None:
                    continue
                has = any((k and k[0] == 2 and (filt[id(o)] is not None) and (lu in sets[filt[id(o)]]) for o in p.objects for k in [kinds[id(o)]]))
                if has:
                    continue
                for o in p.objects:
                    k = kinds[id(o)]
                    if not (k and k[0] == 2 and (k[1] == 0)) or filt[id(o)] is None:
                        continue
                    need = sets[filt[id(o)]] | {lu}
                    target = next((i for i, st in enumerate(sets) if need <= st), None)
                    if target is None:
                        report.append(f"no filter covers {o['name']}'s modes plus {mode}; left")
                        continue
                    report.append(f"{o['name']}: filter {filt[id(o)]} -> {target} (initial spawn for {mode})")
                    filt[id(o)] = target
                    moved += 1
            objs = [(o['serial'], mapwalk.object_bytes(o), filt[id(o)]) for o in p.objects]
            d = mapwalk.rebuild(objs, table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, r.index, payload)
    report.append(f"{edits} layer references rewritten in {tables} filter tables; {moved} initial spawns moved onto a filter covering {', '.join(a.initial_for)}")
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, archive)
    size = write(dest, image)
    written = ArchiveImage.read(dest)
    left = bad_keys = 0
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            left += sum((p.filter_table.count(struct.pack('<I', u)) for u in mapping))
            bad_keys += struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']
    report.append(f'verify: map-specific layers left in filter tables {left}; entry keys != first object uid {bad_keys}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    return 0 if not (left or bad_keys) else 1
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
