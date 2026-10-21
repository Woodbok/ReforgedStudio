"""forgelib/maps/port/grid_shift.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import grid_window
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from regrid import FLOOR_HEIGHT_ID
OUT = os.path.join(ROOT, 'out', 'maps')

def origins(body):
    k = grid_window.GRID.search(body)
    if not k:
        raise SystemExit("the World's int GridLayout not found")
    gi = k.end()
    leaf, ox, oy, zero, side = struct.unpack_from('<5i', body, gi)
    z = body.find(struct.pack('<f', -2000.0))
    if z < 0 or body.find(struct.pack('<f', -2000.0), z + 1) >= 0:
        raise SystemExit("the float block's z = -2000 not found exactly once")
    fx, fy = struct.unpack_from('<2f', body, z - 8)
    fh = body.rfind(struct.pack('<I', FLOOR_HEIGHT_ID), 0, z)
    n = struct.unpack_from('<I', body, fh + 4)[0] if fh >= 0 else None
    if fh < 0 or fh + 8 + n + 20 != z:
        raise SystemExit('FloorHeightData not where expected (its samples, then u32 0, scale, offset, origin x / y, -2000)')
    if (fx, fy) != (float(ox), float(oy)) or zero != 0:
        raise SystemExit(f'the two origins disagree: int ({ox}, {oy}), float ({fx}, {fy})')
    return (gi, z - 8)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--dx', type=int, required=True)
    ap.add_argument('--dy', type=int, required=True)
    a = ap.parse_args(argv)
    if a.dx % 8 or a.dy % 8:
        raise SystemExit('--dx / --dy must be whole 8 m height samples (whole 32 m leaves in practice)')
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    worlds = [(p, i) for p in payloads for i, o in enumerate(p.objects) if xrefs.cls('AC3', o['type']) == 'World']
    if len(worlds) != 1:
        raise SystemExit(f'{len(worlds)} World records')
    p, wi = worlds[0]
    body = bytearray(p.objects[wi]['body'])
    gi, fo = origins(bytes(body))
    leaf, ox, oy, zero, side = struct.unpack_from('<5i', body, gi)
    struct.pack_into('<2i', body, gi + 4, ox + a.dx, oy + a.dy)
    struct.pack_into('<2f', body, fo, float(ox + a.dx), float(oy + a.dy))
    objs = [(o['serial'], mapwalk.object_bytes(o, bytes(body) if i == wi else o['body']), o['filter']) for i, o in enumerate(p.objects)]
    d = mapwalk.rebuild(objs, p.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = True
    with Forge(dest) as f, Forge(src) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index != p.rec.index))
        q = next((mapwalk.Payload(r, f.read(r)) for r in f.records if r.index == p.rec.index))
        nb, ob = (q.objects[wi]['body'], p.objects[wi]['body'])
        diff = [j for j in range(len(nb)) if nb[j] != ob[j]] if len(nb) == len(ob) else None
        gi2, fo2 = origins(nb)
        moved = struct.unpack_from('<5i', nb, gi2) == (leaf, ox + a.dx, oy + a.dy, zero, side) and struct.unpack_from('<2f', nb, fo2) == (float(ox + a.dx), float(oy + a.dy))
        only = diff is not None and all((gi + 4 <= j < gi + 12 or fo <= j < fo + 8 for j in diff))
        rest = all((x['body'] == y['body'] for k, (x, y) in enumerate(zip(q.objects, p.objects)) if k != wi))
        ok = others and moved and only and rest
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print(f"grid origin ({ox}, {oy}) -> ({ox + a.dx}, {oy + a.dy}), {side} x {side} of {leaf} m; FloorHeightData's origin with it")
    print(f"verify: origins moved {moved}, the World record otherwise byte-identical {only}, its file's other objects {rest}, every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
