"""forgelib/maps/regrid.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
from merge_cells import merge
from relocate_cells import ENTRY, FIRST, gcdb_node, gp_nodes
OUT = os.path.join(ROOT, 'out', 'maps')
OLD_ORIGIN, OLD_WIDTH = ((-512, -512), 1024)
NEW_ORIGIN, NEW_WIDTH, NEW_LEVELS = ((-128, -128), 256, 4)
FLOOR_HEIGHT_ID = 2460854129
WINDOW = 96

def levels(width, n_levels):
    out, start = ([], 0)
    for k in range(n_levels - 1, -1, -1):
        side = 2 ** k
        out.append((start, side, width // side))
        start += side * side
    return out
OLD = levels(OLD_WIDTH, 6)
NEW = levels(NEW_WIDTH, NEW_LEVELS)
NEW_NODES = sum((s * s for _f, s, _c in NEW))

def square(n, table, origin):
    for start, side, size in table:
        if n < start + side * side:
            r, c = divmod(n - start, side)
            return (size, origin[0] + c * size, origin[1] + r * size)
    raise ValueError(n)

def new_node(size, x, y):
    for start, side, s in NEW:
        if s != size:
            continue
        dx, dy = (x - NEW_ORIGIN[0], y - NEW_ORIGIN[1])
        if dx % size or dy % size:
            return None
        c, r = (dx // size, dy // size)
        return start + r * side + c if 0 <= c < side and 0 <= r < side else None
    return None

def refs(body, pool):
    out, j = (set(), body.find(b'\x01\x00', 6))
    while 0 <= j < len(body) - 5:
        u = struct.unpack_from('<I', body, j + 2)[0]
        if u in pool:
            out.add(u)
        j = body.find(b'\x01\x00', j + 1)
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='T00_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='N30')
    ap.add_argument('--dy', type=int, default=128, help='how far shift_map.py moved the map north (m)')
    ap.add_argument('--dx', type=int, default=0, help='how far shift_map.py moved the map east (m; Juderia -128: a multiple of 128, as --dy, so the 64 / 128 m nodes stay aligned)')
    ap.add_argument('--crop-heights', action='store_true', help="also crop FloorHeightData's samples to the new grid (N47: correct data, but a fountain and the kiosk drew wrongly; off until its readers are known)")
    a = ap.parse_args(argv)
    shifted = (OLD_ORIGIN[0] + a.dx, OLD_ORIGIN[1] + a.dy)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}
    world_p, world_i = next(((p, i) for p in payloads.values() for i, o in enumerate(p.objects) if xrefs.cls('ACR', o['type']) == 'World'))
    gp_p, gp_i = next(((p, i) for p in payloads.values() for i, o in enumerate(p.objects) if xrefs.cls('ACR', o['type']) == 'GridPartition'))
    gp = gp_p.objects[gp_i]['body']
    old_levels, old_n = struct.unpack_from('<II', gp, 10)
    if (old_levels, old_n) != (6, 1365) or len(gp) != FIRST + ENTRY * old_n + 4 + 1024:
        raise SystemExit(f'unexpected GridPartition: {old_levels} levels, {old_n} nodes, {len(gp)} bytes')
    keys = gp_nodes(gp)
    by_key = {p.objects[0]['uid']: p for p in payloads.values() if p.rec.name.startswith('Cell')}
    new_keys, placed, left, report = ([0] * NEW_NODES, {}, [], [])
    for n, k in enumerate(keys):
        if not k:
            continue
        p = by_key[k]
        size, x, y = square(n, OLD, shifted)
        nn = NEW_NODES - 1 if n == old_n - 1 else new_node(size, x, y)
        if nn is None:
            left.append((p.rec.name, n, size, x, y))
            continue
        if new_keys[nn]:
            raise SystemExit(f'node {nn} named twice')
        new_keys[nn] = k
        placed[p.rec.name] = (n, nn)
    side = NEW[0][1]
    lo = max(0, (-WINDOW - NEW_ORIGIN[0]) // 32)
    hi = min(side - 1, (WINDOW - NEW_ORIGIN[0]) // 32 - 1)
    merges, moved_home = ({}, [])
    for name, (old, nn) in sorted(placed.items(), key=lambda t: t[1][1]):
        if nn >= side * side:
            continue
        r, c = divmod(nn, side)
        tr, tc = (min(max(r, lo), hi), min(max(c, lo), hi))
        if (tr, tc) == (r, c):
            continue
        tn = tr * side + tc
        k = new_keys[nn]
        new_keys[nn] = 0
        if not new_keys[tn]:
            new_keys[tn] = k
            placed[name] = (old, tn)
            moved_home.append(f'{name} {nn} -> {tn}')
        else:
            merges.setdefault(tn, []).append(name)
    merged_away = {n for names in merges.values() for n in names}
    for n in merged_away:
        del placed[n]
    w = world_p.objects[world_i]['body']
    z = w.find(struct.pack('<f', -2000.0))
    if struct.unpack_from('<2f', w, z - 8) != (float(OLD_ORIGIN[0]), float(OLD_ORIGIN[1])) or struct.unpack_from('<3I', w, z + 4) != (0, OLD_WIDTH // 8, OLD_WIDTH // 8):
        raise SystemExit('GridLayout not where expected in the World record')
    w = bytearray(w)
    struct.pack_into('<2f', w, z - 8, float(NEW_ORIGIN[0]), float(NEW_ORIGIN[1]))
    struct.pack_into('<2I', w, z + 8, NEW_WIDTH // 8, NEW_WIDTH // 8)
    old_gl = struct.pack('<5i', 32, OLD_ORIGIN[0], OLD_ORIGIN[1], 0, 32)
    gl = w.find(old_gl)
    if gl < 0 or w.find(old_gl, gl + 1) >= 0:
        raise SystemExit('integer GridLayout not found exactly once in the World record')
    struct.pack_into('<5i', w, gl, 32, NEW_ORIGIN[0], NEW_ORIGIN[1], 0, NEW[0][1])
    fh = w.rfind(struct.pack('<I', FLOOR_HEIGHT_ID), 0, z)
    old_side, new_side = (OLD_WIDTH // 8, NEW_WIDTH // 8)
    if fh < 0 or struct.unpack_from('<I', w, fh + 4)[0] != old_side ** 2 or fh + 8 + old_side ** 2 + 20 != z:
        raise SystemExit('FloorHeightData not where expected in the World record')
    heights = w[fh + 8:fh + 8 + old_side ** 2]
    kept_side = new_side if a.crop_heights else old_side
    c0, r0 = ((NEW_ORIGIN[0] - OLD_ORIGIN[0]) // 8, (NEW_ORIGIN[1] - a.dy - OLD_ORIGIN[1]) // 8)
    if (NEW_ORIGIN[1] - a.dy - OLD_ORIGIN[1]) % 8 or not (0 <= r0 and r0 + new_side <= old_side):
        raise SystemExit("the new grid's height samples don't fall on the old grid's")
    cropped = b''.join((heights[(r0 + r) * old_side + c0:(r0 + r) * old_side + c0 + new_side] for r in range(new_side)))
    if a.crop_heights:
        w[fh + 4:fh + 8 + old_side ** 2] = struct.pack('<I', new_side ** 2) + cropped
        z -= old_side ** 2 - new_side ** 2
        report.append(f'FloorHeightData: {old_side} x {old_side} -> {new_side} x {new_side} samples (old rows {r0}-{r0 + new_side - 1}, columns {c0}-{c0 + new_side - 1})')
    else:
        cropped = heights
    target_of = {}
    tmpl = gp[FIRST:FIRST + ENTRY]
    entries = b''.join((tmpl[:9] + struct.pack('<I', k) + tmpl[13:] for k in new_keys))
    leaves = NEW[0][1] ** 2
    new_gp = gp[:10] + struct.pack('<II', NEW_LEVELS, NEW_NODES) + entries + struct.pack('<I', leaves) + b'_' * leaves
    changed = {world_p.rec.name: {world_i: bytes(w)}}
    changed.setdefault(gp_p.rec.name, {})[gp_i] = new_gp
    for name, (old, new) in placed.items():
        g = payloads[name].objects[0]['body']
        k = gcdb_node(g)
        if struct.unpack_from('<I', g, k)[0] != old:
            raise SystemExit(f"{name}: its GridCellDataBlock says node {struct.unpack_from('<I', g, k)[0]}, not {old}")
        changed.setdefault(name, {})[0] = g[:k] + struct.pack('<I', new) + g[k + 4:]
        target_of[new] = name
    merge_info = []
    for tn, names in merges.items():
        tname = target_of[tn]
        tp = payloads[tname]
        tp.objects[0]['body'] = changed[tname].pop(0)
        d, moved, _checks, info = merge(tp, [payloads[n] for n in names])
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, tp.rec.index, payload)
        merge_info.append(info)
        if changed[tname]:
            raise SystemExit(f'{tname}: other edits besides the merge')
        del changed[tname]
    for name, bodies in changed.items():
        p = payloads[name]
        objs = [(o['serial'], mapwalk.object_bytes(o, bodies.get(i, o['body'])), o['filter']) for i, o in enumerate(p.objects)]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    counts = Counter((square(placed[n][0], OLD, OLD_ORIGIN)[0] for n in placed))
    report.append(f"edge leaves moved inward: {moved_home or 'none'}")
    report += [f'merged: {i}' for i in merge_info]
    report.append(f"placed {len(placed)} cell files ({', '.join((f'{s} m: {c}' for s, c in sorted(counts.items())))}); left off the grid {len(left)}: " + ', '.join((f'{nm} ({s} m at {x},{y})' for nm, _n, s, x, y in left)))
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    written = ArchiveImage.read(dest)
    after = {}
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            after[r.name] = p
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
    wb = after[world_p.rec.name].objects[world_i]['body']
    v['GridLayout as planned'] = struct.unpack_from('<2f', wb, z - 8) == (float(NEW_ORIGIN[0]), float(NEW_ORIGIN[1])) and struct.unpack_from('<2I', wb, z + 8) == (NEW_WIDTH // 8, NEW_WIDTH // 8) and (struct.unpack_from('<5i', wb, gl) == (32, NEW_ORIGIN[0], NEW_ORIGIN[1], 0, NEW[0][1]))
    v['FloorHeightData as planned'] = struct.unpack_from('<I', wb, fh + 4)[0] == kept_side ** 2 and wb[fh + 8:fh + 8 + kept_side ** 2] == cropped and (fh + 8 + kept_side ** 2 + 20 == z)
    gb = after[gp_p.rec.name].objects[gp_i]['body']
    nk = gp_nodes(gb)
    v['node table'] = f"{struct.unpack_from('<II', gb, 10)}, as planned {nk == new_keys}"
    fk = {p.objects[0]['uid']: p for n, p in after.items() if n.startswith('Cell')}
    v['named nodes whose file carries that node'] = sum((1 for i, k in enumerate(nk) if k and struct.unpack_from('<I', fk[k].objects[0]['body'], gcdb_node(fk[k].objects[0]['body']))[0] == i))
    v['named nodes'] = sum((1 for k in nk if k))
    loaded = {fk[k].rec.name for k in nk if k} | {n for n in after if not n.startswith('Cell')}
    defined_loaded = {o['uid'] for n in loaded for o in after[n].objects if o['has_uid']}
    only_unloaded = {o['uid'] for n, p in after.items() if n not in loaded for o in p.objects if o['has_uid']} - defined_loaded
    v['loaded objects referencing something only unloaded cells hold'] = sum((1 for n in loaded for o in after[n].objects if refs(o['body'], only_unloaded)))
    far = 0
    for name, (_old, new) in placed.items():
        sz, x, y = square(new, NEW, NEW_ORIGIN)
        if sz != 32:
            continue
        for o in after[name].objects:
            if xrefs.cls('ACR', o['type']) in ('Entity', 'EntityGroup') and len(o['body']) >= 74:
                m = struct.unpack_from('<16f', o['body'], 10)
                if abs(m[15] - 1) < 0.001 and (m[12] or m[13]) and (not (x - 32 <= m[12] <= x + 64 and y - 32 <= m[13] <= y + 64)):
                    far += 1
    v['leaf entities more than 32 m outside their square'] = far
    v['named leaves outside the load window'] = sum((1 for i, k in enumerate(nk[:side * side]) if k and (not (lo <= i // side <= hi and lo <= i % side <= hi))))
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
