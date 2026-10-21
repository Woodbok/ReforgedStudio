"""forgelib/maps/swap_template.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import mapwalk
import template_convert
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N13_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--map', default='DataPC_ACFE_Souk.forge', help="the ACR archive holding ACR's copy")
    ap.add_argument('--prefix', default='N16')
    ap.add_argument('--uid', default='0x17AB0B95', help='the template (default ACFE_Tex1_PixelLit)')
    a = ap.parse_args(argv)
    uid = int(a.uid, 16)
    acr = next((o['body'] for p in mapwalk.payloads('ACR', a.map) for o in p.objects if o['has_uid'] and o['uid'] == uid), None)
    if acr is None:
        raise SystemExit(f"0x{uid:08X} not in ACR's {a.map}")
    conv, _c = template_convert.convert(acr)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    replaced = []
    with Forge(src) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            if not any((o['has_uid'] and o['uid'] == uid for o in p.objects)):
                continue
            objs = [(o['serial'], mapwalk.object_bytes(o, conv if o['has_uid'] and o['uid'] == uid else o['body']), o['filter']) for o in p.objects]
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, r.index, payload)
            replaced.append(r.name)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = []
    with Forge(dest) as f:
        for r in f.records:
            if r.name in replaced:
                ok += [o['body'] == conv for o in mapwalk.Payload(r, f.read(r)).objects if o['has_uid'] and o['uid'] == uid]
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB); 0x{uid:08X} replaced in {replaced}; verify converted bytes: {ok}')
if __name__ == '__main__':
    main(sys.argv[1:])
