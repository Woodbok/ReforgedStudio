"""forgelib/crossmaps/world_materials.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acb
acb.patch()
import mapwalk
import uid_index
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from import_ac3 import refs
OUT = os.path.join(acb.ROOT, 'out', 'maps')
STAND_IN = 1921337557

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    wp = next((p for p in payloads if any((xrefs.cls('AC3', o['type']) == 'World' for o in p.objects))))
    templates = {o['uid']: p.rec.name for p in payloads for o in p.objects if o['has_uid'] and xrefs.cls('AC3', o['type']) == 'MaterialTemplate'}
    in_world = {u for u, n in templates.items() if n == wp.rec.name}
    datapc = {u for u, (_f, t, _n) in uid_index.load('AC3', 'DataPC.forge')[0].items() if xrefs.cls('AC3', t) == 'MaterialTemplate'}
    if STAND_IN not in in_world:
        raise SystemExit('the World file has no AC2_Tex1_PixelLit (stage 0 step 9)')
    objs, moved = ([], [])
    for o in wp.objects:
        body = o['body']
        if xrefs.cls('AC3', o['type']) == 'Material':
            for t in refs(body):
                if t in templates and t not in in_world and (t not in datapc):
                    old = b'\x01\x00' + struct.pack('<I', t)
                    if body.count(old) != 1:
                        raise SystemExit(f"{o['name']}: template 0x{t:08X} named {body.count(old)} times as 01 00 uid")
                    body = body.replace(old, b'\x01\x00' + struct.pack('<I', STAND_IN))
                    moved.append((o['name'], t, templates[t]))
        objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
    if moved:
        d = mapwalk.rebuild(objs, wp.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, wp.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    for n, t, where in moved:
        print(f"  {n}: template 0x{t:08X} (in {where}) -> AC2_Tex1_PixelLit (the World file's)")
    with Forge(dest) as f, Forge(src) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index != wp.rec.index))
        q = mapwalk.Payload(f.records[wp.rec.index], f.read(f.records[wp.rec.index]))
    left = [o['name'] for o in q.objects if xrefs.cls('AC3', o['type']) == 'Material' and any((t in templates and t not in in_world and (t not in datapc) for t in refs(o['body'])))]
    same = [len(x['body']) for x in q.objects] == [len(x['body']) for x in wp.objects]
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print(f"verify: {len(moved)} World-file materials onto the World file's AC2_Tex1_PixelLit; left on a later-loading template {len(left)}; object sizes unchanged {same}; every other record byte-identical {others}: {('OK' if not left and same and others else 'FAILED')}")
    if left or not same or (not others):
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
