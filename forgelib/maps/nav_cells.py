"""forgelib/maps/nav_cells.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
from regrid import NEW, NEW_ORIGIN, OLD_ORIGIN
from relocate_cells import gcdb_node
OUT = os.path.join(ROOT, 'out', 'maps')
OLD_SIDE, CELL = (32, 32)
NEW_SIDE = NEW[0][1]
REFS = {815747378: 8, 1312337253: 6, 2897755239: 6}
NAMES = {815747378: 'TriangleRef', 1312337253: 'WayPointRef', 2897755239: 'NavMeshRef'}

def edge_home(leaf):
    import regrid
    side = NEW_SIDE
    lo = max(0, (-regrid.WINDOW - regrid.NEW_ORIGIN[0]) // 32)
    hi = min(side - 1, (regrid.WINDOW - regrid.NEW_ORIGIN[0]) // 32 - 1)
    r, c = divmod(leaf, side)
    return min(max(r, lo), hi) * side + min(max(c, lo), hi)

def new_id(old, dy, dx=0):
    col, row = (old % OLD_SIDE, old // OLD_SIDE)
    x, y = (OLD_ORIGIN[0] + CELL * col + dx, OLD_ORIGIN[1] + CELL * row + dy)
    c, r = ((x - NEW_ORIGIN[0]) // CELL, (y - NEW_ORIGIN[1]) // CELL)
    return r * NEW_SIDE + c if 0 <= c < NEW_SIDE and 0 <= r < NEW_SIDE else None

def ref_sites(body):
    out = []
    for cid, off in REFS.items():
        pat = struct.pack('<I', cid)
        k = body.find(pat)
        while k >= 0:
            if not (body[k + 4:k + 6] == b'\x00\x00' and body[k + 6:k + 8] == b'\x1d\x0b') and k + off + 2 <= len(body):
                out.append((k + off, NAMES[cid]))
            k = body.find(pat, k + 1)
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--dy', type=int, default=128, help='how far shift_map.py moved the map north (m)')
    ap.add_argument('--dx', type=int, default=0, help='how far shift_map.py moved the map east (m)')
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    managers, remap, v, edge_moved = ({}, {}, Counter(), set())
    from relocate_cells import gp_nodes
    gp = next((o['body'] for p in payloads for o in p.objects if xrefs.cls('ACR', o['type']) == 'GridPartition'))
    on_grid = {k for k in gp_nodes(gp) if k}
    dead = {p.rec.name for p in payloads if p.rec.name.startswith('Cell') and p.objects[0]['uid'] not in on_grid}
    for p in payloads:
        if p.rec.name in dead:
            continue
        for i, o in enumerate(p.objects):
            if xrefs.cls('ACR', o['type']) == 'NavMeshManager':
                old = struct.unpack_from('<H', o['body'], len(o['body']) - 2)[0]
                node = struct.unpack_from('<I', p.objects[0]['body'], gcdb_node(p.objects[0]['body']))[0]
                new = new_id(old, a.dy, a.dx)
                moved_in = new is not None and new != node and (edge_home(new) == node)
                if moved_in:
                    edge_moved.add(new)
                if new is None or (new != node and (not moved_in)):
                    raise SystemExit(f'{p.rec.name}: manager {old} -> {new}, but its file is on node {node}')
                if new in remap.values():
                    raise SystemExit(f'{p.rec.name}: manager {old} -> {new}, a node another manager has')
                managers[p.rec.name, i] = old
                remap[old] = new
    plan, stats = ({}, Counter())
    for p in payloads:
        bodies = {}
        for i, o in enumerate(p.objects):
            sites = ref_sites(o['body'])
            if (p.rec.name, i) in managers:
                sites.append((len(o['body']) - 2, 'manager'))
            if not sites:
                continue
            nb = bytearray(o['body'])
            for k, kind in sites:
                old = struct.unpack_from('<H', nb, k)[0]
                if old not in remap:
                    raise SystemExit(f"{p.rec.name} {o['name']}: {kind} names cell {old}, no manager has it")
                struct.pack_into('<H', nb, k, remap[old])
                stats[kind] += 1
            bodies[i] = bytes(nb)
        if not bodies:
            continue
        plan[p.rec.name] = bodies
        objs = [(o['serial'], mapwalk.object_bytes(o, bodies.get(i, o['body'])), o['filter']) for i, o in enumerate(p.objects)]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    before = {(p.rec.name, i): o['body'] for p in payloads for i, o in enumerate(p.objects)}
    new_ids = set(remap.values())
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            node = struct.unpack_from('<I', p.objects[0]['body'], gcdb_node(p.objects[0]['body']))[0] if r.name.startswith('Cell') else None
            for i, o in enumerate(p.objects):
                b = o['body']
                if xrefs.cls('ACR', o['type']) == 'NavMeshManager':
                    mid = struct.unpack_from('<H', b, len(b) - 2)[0]
                    v['managers on their own leaf'] += mid == node
                    v['managers on their own leaf, their file moved in from the edge'] += mid in edge_moved and edge_home(mid) == node
                for k, _kind in ref_sites(b):
                    v['references naming a manager'] += struct.unpack_from('<H', b, k)[0] in new_ids
                    v['references naming no manager'] += struct.unpack_from('<H', b, k)[0] not in new_ids
                if i in plan.get(r.name, {}):
                    old = before[r.name, i]
                    diff = [j for j in range(len(b)) if old[j] != b[j]] if len(old) == len(b) else None
                    sites = {k for k, _ in ref_sites(b)} | {len(b) - 2}
                    v['objects edited as planned'] += diff is not None and all((j in sites or j - 1 in sites for j in diff))
    report = [f'cell ids: {dict(sorted(remap.items()))}', f'rewritten: {dict(stats)}; {len(plan)} files', f'verify: {dict(v)}']
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
