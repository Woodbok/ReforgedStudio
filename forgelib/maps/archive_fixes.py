"""forgelib/maps/archive_fixes.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
from forge_write import ArchiveImage, add_file, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
CELL = re.compile('^Cell\\d+_DataBlock$')
EMPTY_TEMPLATE = 'DataBlock_ACFE_Hijack'

def layer_names(game):
    out = {}
    for u, homes in inventory.universe(game).items():
        for _arc, _f, t, n in homes:
            if xrefs.cls(game, t) == 'DataLayer':
                out[u] = n
    return out

def rename_payload(p, name, uid=None):
    objs = []
    for pos, o in enumerate(p.objects):
        body = o['body']
        serial = o['serial']
        if pos == 0:
            if uid is not None:
                body = body[:2] + struct.pack('<I', uid) + body[6:]
                serial = uid
            o = dict(o, name=name)
        objs.append((serial, mapwalk.object_bytes(o, body), o['filter']))
    d = mapwalk.rebuild(objs, p.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    return payload

def set_name(image, index, name):
    sf = image.files[index]
    enc = name.encode('latin-1')
    meta = bytearray(sf.meta)
    meta[44:160] = enc.ljust(160 - 44, b'\x00')
    header = bytearray(sf.header)
    header[8:8 + 128] = enc.ljust(128, b'\x00')
    image.files[index] = sf._replace(meta=meta, header=bytes(header))
    image.dirty.add(index)

def set_file_uid(image, index, uid):
    sf = image.files[index]
    meta = bytearray(sf.meta)
    struct.pack_into('<Q', meta, 4, uid)
    header = bytearray(sf.header)
    struct.pack_into('<Q', header, 399, uid)
    image.files[index] = sf._replace(meta=meta, header=bytes(header))
    image.dirty.add(index)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='S3_acfe_souk_as_ac3mp_plantation')
    ap.add_argument('--slot', default='AC3MP_Plantation')
    ap.add_argument('--archive', default=None, help='archive file name in the build folder (default DataPC_<slot>.forge; a standalone map keeps its own, e.g. DataPC_ACFE_Souk.forge, while --slot stays the stock archive borrowed from)')
    ap.add_argument('--prefix', default='S4', help='build folder prefix')
    a = ap.parse_args(argv)
    archive = a.archive or f'DataPC_{a.slot}.forge'
    donor = f'DataPC_{a.slot}.forge'
    src = os.path.join(OUT, a.src, archive)
    slot_path = mapwalk.archive_path('AC3', donor)
    report = []
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        recs = {r.name: r for r in f.records}
        payloads = {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}
    with Forge(slot_path) as f:
        slot_recs = {r.name: r for r in f.records}
        slot_meta = f.read(slot_recs['GlobalMetaFile'])
    k = recs['GlobalMetaFile'].index
    set_payload(image, k, slot_meta)
    set_file_uid(image, k, slot_recs['GlobalMetaFile'].uid)
    report.append(f"GlobalMetaFile: the slot's contents and uid 0x{slot_recs['GlobalMetaFile'].uid:016X}")
    acr, ac3 = (layer_names('ACR'), layer_names('AC3'))
    by_name = {n: u for u, n in acr.items()}
    have = set(recs)
    for name, p in payloads.items():
        if not name.startswith('DataBlock_') or CELL.match(name):
            continue
        u = by_name.get(name[len('DataBlock_'):])
        new = ac3.get(u) if u is not None else None
        if new and new != name[len('DataBlock_'):]:
            new_name = 'DataBlock_' + new
            if new_name in have:
                continue
            set_payload(image, recs[name].index, rename_payload(p, new_name))
            set_name(image, recs[name].index, new_name)
            have.discard(name)
            have.add(new_name)
            report.append(f'renamed {name} -> {new_name} (layer 0x{u:08X})')
    taken = set(inventory.universe('ACR')) | set(inventory.universe('AC3'))
    used_file_uids = {r.uid for r in recs.values()} | {r.uid for r in slot_recs.values()}
    template = payloads[EMPTY_TEMPLATE]
    tmpl_index = recs[EMPTY_TEMPLATE].index
    next_uid = 1509949440
    file_uid = 6510516211317473280
    missing = sorted((n for n in slot_recs if n.startswith('DataBlock') and (not CELL.match(n)) and (n not in have)))
    for name in missing:
        while next_uid in taken:
            next_uid += 1
        while file_uid in used_file_uids:
            file_uid += 1
        payload = rename_payload(template, name, uid=next_uid)
        add_file(image, name, payload, file_uid, template_index=tmpl_index, entry_key=next_uid)
        taken.add(next_uid)
        used_file_uids.add(file_uid)
        next_uid += 1
        file_uid += 1
    report.append(f"{len(missing)} empty placeholder DataBlocks added: {', '.join(missing)}")
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, archive)
    size = write(dest, image)
    written = ArchiveImage.read(dest)
    with Forge(dest) as f:
        names = {r.name for r in f.records}
        bad = keys = 0
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            bad += p.objects[0]['name'] != r.name
            keys += struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']
        meta_ok = f.read(next((r for r in f.records if r.name == 'GlobalMetaFile'))) == slot_meta
    still = sorted((n for n in slot_recs if n.startswith('DataBlock') and (not CELL.match(n)) and (n not in names)))
    report.append(f'verify: {len(names)} files, object/index name mismatches {bad}, entry keys != first object uid {keys}, GlobalMetaFile matches slot: {meta_ok}, slot DataBlocks still missing: {still}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    return 0 if not bad and (not keys) and meta_ok and (not still) else 1
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
