"""forgelib/maps/shaders.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inventory
import mapwalk
import world
import xrefs
from summary import BULK
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from transplant import remap
OUT = os.path.join(ROOT, 'out', 'maps')
SUB_RUN = 2048
ELEMENT_FLAGS = (b'\x04\x00', b'\x00\x01', b'\x01\x00')

def ac3_templates():
    uni = inventory.universe('AC3')
    wanted = {}
    for u, homes in uni.items():
        for arc, f, t, n in homes:
            if xrefs.cls('AC3', t) == 'MaterialTemplate':
                wanted.setdefault((arc, f), set()).add(u)
    bodies, names = ({}, {})
    opened = {}
    try:
        for (arc, fname), uids in wanted.items():
            if arc not in opened:
                f = Forge(mapwalk.archive_path('AC3', arc))
                opened[arc] = (f, {r.name: r for r in reversed(f.records)})
            f, by_name = opened[arc]
            rec = by_name.get(fname)
            if rec is None:
                continue
            try:
                p = mapwalk.Payload(rec, f.read(rec))
            except Exception:
                continue
            for o in p.objects:
                if o['has_uid'] and o['uid'] in uids and (o['uid'] not in bodies):
                    bodies[o['uid']] = o['body']
                    names.setdefault(o['name'], o['uid'])
    finally:
        for f, _ in opened.values():
            f.close()
    return (bodies, names)

def sub_uids(body, base):
    ids = set()
    for k, _n in world.markers(body):
        if k >= 6 and body[k - 6:k - 4] in ELEMENT_FLAGS:
            u = struct.unpack_from('<I', body, k - 4)[0]
            if base <= u < base + SUB_RUN:
                ids.add(u)
    ids.add(base)
    return ids

def relocated(body, base, new_base):
    mapping = {u: new_base + (u - base) for u in sub_uids(body, base)}
    out, _n = remap(body, mapping)
    return out

def substitute_for(name):
    return 'AC2_Tex1_PixelLit' if name.startswith('AC2') else 'ACFE_Tex1_PixelLit'

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='S5_acfe_souk_as_ac3mp_plantation')
    ap.add_argument('--slot', default='AC3MP_Plantation')
    ap.add_argument('--archive', default=None, help='archive file name in the build folder (default DataPC_<slot>.forge; a standalone map keeps its own, e.g. DataPC_ACFE_Souk.forge, while --slot stays the stock archive borrowed from)')
    ap.add_argument('--prefix', default='S6')
    a = ap.parse_args(argv)
    archive = a.archive or f'DataPC_{a.slot}.forge'
    src = os.path.join(OUT, a.src, archive)
    bodies, names = ac3_templates()
    taken = set(inventory.universe('ACR')) | set(inventory.universe('AC3'))
    mt = [t for t, n in xrefs._REG.get('ACR', {}).items() if n == 'MaterialTemplate'] or None
    report, next_base = ([], 1526726656)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    absent = {}
    for p in payloads:
        for o in p.objects:
            if xrefs.cls('ACR', o['type']) == 'MaterialTemplate' and o['has_uid'] and (o['uid'] not in bodies):
                absent[o['uid']] = (o['name'], names[substitute_for(o['name'].strip())])
    retarget = {u: sub for u, (_n, sub) in absent.items()}
    done, retargeted, dropped = ({}, 0, 0)
    for p in payloads:
        changed, objs = (False, [])
        for pos, o in enumerate(p.objects):
            body = o['body']
            cls = xrefs.cls('ACR', o['type'])
            if cls == 'MaterialTemplate' and o['has_uid']:
                u = o['uid']
                if u in bodies:
                    body = bodies[u]
                    done.setdefault(o['name'], f"AC3's own (same uid 0x{u:08X})")
                    changed = True
                elif pos > 0:
                    done.setdefault(o['name'], f'removed from {p.rec.name}; references -> 0x{retarget[u]:08X}')
                    dropped += 1
                    changed = True
                    continue
                else:
                    done.setdefault(o['name'], f'standalone file left unreferenced; references -> 0x{retarget[u]:08X}')
            elif cls not in BULK:
                body, n = remap(body, retarget)
                if n:
                    retargeted += sum(n.values())
                    changed = True
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        if changed:
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, p.rec.index, payload)
    report.append(f'{retargeted} references to the {len(absent)} templates AC3 lacks retargeted; {dropped} of those templates removed from files that load')
    for n, how in sorted(done.items()):
        report.append(f'{n:42} {how}')
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, archive)
    size = write(dest, image)
    written = ArchiveImage.read(dest)
    bad_keys = acr_left = dangling = 0
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            bad_keys += struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']
            for pos, o in enumerate(p.objects):
                cls = xrefs.cls('ACR', o['type'])
                if cls == 'MaterialTemplate' and pos > 0:
                    acr_left += o['body'] != bodies.get(o['uid'])
                elif cls not in BULK and cls != 'MaterialTemplate':
                    dangling += any((struct.pack('<I', u) in o['body'] for u in absent))
    report.append(f'verify: {len(done)} templates handled; entry keys != first object uid {bad_keys}; ACR templates left inside loading files {acr_left}; structural objects still naming an absent template {dangling}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    return 0 if not (bad_keys or acr_left or dangling) else 1
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
