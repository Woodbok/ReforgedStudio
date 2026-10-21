"""forgelib/maps/fake_tiles.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import fake_spans
import mapwalk
import convert
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
FAKE_ENTITY = struct.pack('<I', 3615470884)
HEAD = 27
COUNT_AT = 15
TILE = 128

def morton(c, r):
    v = 0
    for i in range(16):
        v |= (c >> i & 1) << 2 * i | (r >> i & 1) << 2 * i + 1
    return v

def grid_layout(payloads):
    wid = convert.class_ids()['World']
    found = [o['body'] for p in payloads for o in p.objects if o['type'] == wid]
    if len(found) != 1:
        raise SystemExit(f'{len(found)} World records')
    leaf, ox, oy, zero, side = struct.unpack_from('<5i', found[0], 120)
    if leaf != 32 or zero != 0 or (not 1 <= side <= 64):
        raise SystemExit(f'int GridLayout not at World +120: {(leaf, ox, oy, zero, side)}')
    return (leaf, ox, oy, side)

def elements(b):
    ts = fake_spans.tiles(b)
    count = struct.unpack_from('<I', b, COUNT_AT)[0]
    if count != len(ts) or count > 4096:
        raise SystemExit(f'FakeEntities count {count} != {len(ts)} tiles found')
    out = []
    for i, (s, _e, xy) in enumerate(ts):
        start = s - 8
        end = ts[i + 1][0] - 8 if i + 1 < len(ts) else len(b)
        if b[start:start + 8] != b'\x00' * 4 + FAKE_ENTITY:
            raise SystemExit(f'tile {i}: no `u32 0, FakeEntity` before it')
        first, n = struct.unpack_from('<2I', b, end - 8)
        if n != 16:
            raise SystemExit(f'tile {i}: leaf count {n}, expected 16')
        out.append((start, end, xy, first))
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='T06_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='T07')
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    leaf, ox, oy, side = grid_layout(payloads)
    if leaf * 4 != TILE or side % 4:
        raise SystemExit(f"grid (leaf {leaf}, side {side}) isn't made of 128 m tiles")
    tiles_side = side // 4
    counts, plan = (Counter(), None)
    for p in payloads:
        for i, o in enumerate(p.objects):
            b = o['body']
            if b[6:10] != struct.pack('<I', fake_spans.FAKES):
                continue
            els = elements(b)
            kept = {}
            for start, end, (x, y), first in els:
                c, r = ((x - ox) // TILE, (y - oy) // TILE)
                if 0 <= c < tiles_side and 0 <= r < tiles_side:
                    kept[morton(c, r)] = (start, end, (x, y), first)
                else:
                    counts['tiles dropped (off the grid)'] += 1
                    counts['of them with a mesh'] += end - start > 400
            if sorted(kept) != list(range(tiles_side * tiles_side)):
                raise SystemExit(f'grid squares without a tile: {sorted(set(range(tiles_side ** 2)) - set(kept))}')
            nb = bytearray(b[:HEAD - 8])
            struct.pack_into('<I', nb, COUNT_AT, len(kept))
            for n in sorted(kept):
                start, end, xy, first = kept[n]
                el = bytearray(b[start:end])
                struct.pack_into('<I', el, len(el) - 8, 16 * n)
                nb += el
                counts[f'tile {n} at {xy}: first leaf {first} -> {16 * n}'] += 1
            plan = (p.rec.name, i, {n: kept[n] for n in kept}, b)
            objs = [(ob['serial'], mapwalk.object_bytes(ob, bytes(nb) if j == i else ob['body']), ob['filter']) for j, ob in enumerate(p.objects)]
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, p.rec.index, payload)
    if plan is None:
        raise SystemExit('no FakeEntities found')
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    rec_name, idx, kept, old = plan
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if r.name != rec_name:
                continue
            b = p.objects[idx]['body']
            els = elements(b)
            v['tiles'] = len(els)
            v['head unchanged bar the count'] = b[:COUNT_AT] == old[:COUNT_AT] and b[COUNT_AT + 4:HEAD - 8] == old[COUNT_AT + 4:HEAD - 8]
            for n, (start, end, (x, y), first) in enumerate(els):
                c, rr = ((x - ox) // TILE, (y - oy) // TILE)
                ostart, oend, _xy, _f = kept[n]
                good = morton(c, rr) == n and first == 16 * n
                good &= b[start:end - 8] == old[ostart:oend - 8] and b[end - 4:end] == old[oend - 4:oend]
                v['tiles in Morton order, first leaf 16 x index, bytes as before'] += good
    report = [f'grid: leaf {leaf} m, origin ({ox}, {oy}), {side} x {side} leaves -> {tiles_side} x {tiles_side} tiles', ', '.join((f'{k} {n}' for k, n in counts.items())), f'verify: {dict(v)}']
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
