"""forgelib/maps/relocate_cells.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import convert
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
L5 = 341
WINDOW = [L5 + r * 32 + c for r in range(2, 8) for c in range(24, 30)]
MOVE = [368, 367, 400, 369, 399, 398, 366, 401, 402, 336, 335, 365, 370, 397, 306, 435, 371, 334, 403, 305, 339, 303]
ENTRY = 17
FIRST = 18

def gp_nodes(body):
    ids = convert.class_ids()
    assert body[6:10] == struct.pack('<I', ids['GridPartition'])
    n = struct.unpack_from('<I', body, 14)[0]
    gc = struct.pack('<I', ids['GridCell'])
    keys = []
    for i in range(n):
        k = FIRST + ENTRY * i
        assert body[k + 4:k + 8] == gc, f'node {i}: no GridCell at +{k}'
        keys.append(struct.unpack_from('<I', body, k + 9)[0])
    return keys

def set_node(body, i, key):
    k = FIRST + ENTRY * i + 9
    return body[:k] + struct.pack('<I', key) + body[k + 4:]

def gcdb_node(body):
    n = struct.unpack_from('<I', body, 10)[0]
    return 14 + 6 * n + 4

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N3_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='N5')
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    gp_file, gp_i = next(((p, i) for p in payloads for i, o in enumerate(p.objects) if xrefs.cls('ACR', o['type']) == 'GridPartition'))
    body = gp_file.objects[gp_i]['body']
    keys = gp_nodes(body)
    cells = {p.objects[0]['uid']: p for p in payloads if p.rec.name.startswith('Cell')}
    free = [n for n in WINDOW if keys[n] == 0]
    if len(free) < len(MOVE):
        raise SystemExit(f'only {len(free)} free window nodes for {len(MOVE)} cells')
    plan = list(zip(MOVE, free))
    report = []
    moved_files = {}
    for old, new in plan:
        key = keys[old]
        p = cells.get(key)
        if not key or p is None:
            raise SystemExit(f'node {old} names no cell file')
        g = p.objects[0]['body']
        k = gcdb_node(g)
        if struct.unpack_from('<I', g, k)[0] != old:
            raise SystemExit(f"{p.rec.name}: its GridCellDataBlock says node {struct.unpack_from('<I', g, k)[0]}, not {old}")
        body = set_node(set_node(body, new, key), old, 0)
        moved_files[p.rec.name] = (p, g[:k] + struct.pack('<I', new) + g[k + 4:], old, new)
        row, col = divmod(new - L5, 32)
        report.append(f'{p.rec.name} (node {old}) -> node {new} (row {row}, column {col}: x {(col - 26.5) * 32:+.0f}, y {(row - 4.5) * 32:+.0f})')

    def rewrite(p, bodies):
        objs = [(o['serial'], mapwalk.object_bytes(o, bodies.get(i, o['body'])), o['filter']) for i, o in enumerate(p.objects)]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    rewrite(gp_file, {gp_i: body})
    for p, g, _o, _n in moved_files.values():
        rewrite(p, {0: g})
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    written = ArchiveImage.read(dest)
    bad_keys = 0
    nodes_after = None
    gcdb_after = {}
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            bad_keys += struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']
            if r.name == gp_file.rec.name:
                nodes_after = gp_nodes(p.objects[gp_i]['body'])
            if r.name.startswith('Cell'):
                g = p.objects[0]['body']
                gcdb_after[p.objects[0]['uid']] = struct.unpack_from('<I', g, gcdb_node(g))[0]
    expect = list(keys)
    for old, new in plan:
        expect[new], expect[old] = (keys[old], 0)
    report.append(f"verify: files walk, entry keys == first uid: {bad_keys == 0}; node table as planned: {nodes_after == expect}; every node's file carries that node: {all((gcdb_after.get(k) == i for i, k in enumerate(nodes_after) if k))}; cells in the window: {sum((1 for n in WINDOW if nodes_after[n]))} of {len(WINDOW)}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
