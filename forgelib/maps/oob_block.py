"""forgelib/maps/oob_block.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import build_static
import mapwalk
import spawn_layers
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
BLOCK = 'DataBlock_ACFE_Training'

def list_entry_add(body, uid):
    n = struct.unpack_from('<I', body, 10)[0]
    end = 14 + 6 * n
    m = struct.unpack_from('<I', body, end)[0]
    return body[:10] + struct.pack('<I', n + 1) + body[14:end] + b'\x01\x00' + struct.pack('<I', uid) + struct.pack('<I', m + 1) + body[end + 4:]

def header(body):
    n = struct.unpack_from('<I', body, 10)[0]
    end = 14 + 6 * n
    uids = [struct.unpack_from('<I', body, k + 2)[0] for k in range(14, end, 6)]
    return (n, uids, struct.unpack_from('<I', body, end)[0])

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='S16_souk_as_plantation')
    ap.add_argument('--slot', default='AC3MP_Plantation')
    ap.add_argument('--archive', default=None, help='archive file name in the build folder (default DataPC_<slot>.forge; a standalone map keeps its own, e.g. DataPC_ACFE_Souk.forge, while --slot stays the stock archive borrowed from)')
    ap.add_argument('--cell', default='Cell00400_DataBlock')
    ap.add_argument('--entity', default='ACFE_RHO_SOU_OOB_01A_001')
    ap.add_argument('--filter', type=int, default=2, help="the Training block's filter for the Entity")
    ap.add_argument('--prefix', default='S17')
    a = ap.parse_args(argv)
    archive = a.archive or f'DataPC_{a.slot}.forge'
    src = os.path.join(OUT, a.src, archive)
    image = ArchiveImage.read(src)
    report = []
    with Forge(src) as f:
        recs = {r.name: r for r in f.records}
        cell_rec, block_rec = (recs[a.cell], recs[BLOCK])
        cell = mapwalk.Payload(cell_rec, f.read(cell_rec))
        block = mapwalk.Payload(block_rec, f.read(block_rec))
    ent = next((o for o in cell.objects if o['name'] == a.entity and o['filter'] is not None))
    in_block = {o['uid'] for o in block.objects if o['uid']}
    moved_uids = {ent['uid']} | {o['uid'] for o in cell.objects if o['uid'] and o is not ent and (o['uid'] not in in_block) and (struct.pack('<I', o['uid']) in ent['body'])}
    moved = [o for o in cell.objects if o['uid'] in moved_uids]
    report.append('moving ' + ', '.join((f"{o['name']} (0x{o['uid']:08X}, {len(o['body'])} B)" for o in moved)))
    sets = spawn_layers.filter_sets(block.filter_table)
    training = next((v for k, v in spawn_layers.layer_mapping('Souk')[0].items() if spawn_layers.layer_mapping('Souk')[1][k][1] == 'ACFE_Training'))
    if training not in sets[a.filter]:
        raise SystemExit(f"block filter {a.filter} doesn't cover ACFE_Training")
    objs = []
    for pos, o in enumerate(cell.objects):
        if o['uid'] in moved_uids:
            continue
        body = o['body']
        if pos == 0:
            body, gone = build_static.clean_cell(body, {ent['uid']})
            report.append(f'{a.cell}: {gone} list entry removed')
        objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
    d = mapwalk.rebuild(objs, cell.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, cell_rec.index, payload)
    objs = []
    for pos, o in enumerate(block.objects):
        body = o['body']
        if pos == 0:
            body = list_entry_add(body, ent['uid'])
        objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
    for o in moved:
        objs.append((o['serial'], mapwalk.object_bytes(o, o['body']), a.filter if o is ent else None))
    d = mapwalk.rebuild(objs, block.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, block_rec.index, payload)
    report.append(f'{BLOCK}: +{len(moved)} objects, the Entity on filter {a.filter} ({len(sets[a.filter])} layers incl. ACFE_Training), list entry added')
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, archive)
    size = write(dest, image)
    written = ArchiveImage.read(dest)
    count = {u: 0 for u in moved_uids}
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            for o in p.objects:
                if o['uid'] in count:
                    count[o['uid']] += 1
            if r.name == a.cell:
                c = p
            elif r.name == BLOCK:
                b = p
        keys = all((struct.unpack_from('<I', written.files[r.index].entry, 8)[0] == q.objects[0]['uid'] for r, q in ((recs[a.cell], c), (recs[BLOCK], b))))
    n_c, uids_c, m_c = header(c.objects[0]['body'])
    n_b, uids_b, m_b = header(b.objects[0]['body'])
    moved_ok = all((any((x['uid'] == o['uid'] and x['body'] == o['body'] for x in b.objects)) for o in moved))
    ent_filter = next((x['filter'] for x in b.objects if x['uid'] == ent['uid']))
    report.append(f"verify: {a.cell} {len(c.objects)} objects (was {len(cell.objects)}), list N={n_c} M={m_c}, entity listed {ent['uid'] in uids_c}; {BLOCK} {len(b.objects)} objects (was {len(block.objects)}), list N={n_b} M={m_b}, entity listed {ent['uid'] in uids_b}, filter {ent_filter}; moved bodies identical {moved_ok}; each moved uid defined once {all((v == 1 for v in count.values()))}; entry keys == first uid {keys}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
