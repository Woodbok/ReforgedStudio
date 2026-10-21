"""forgelib/maps/crowd_region.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import build_static
import convert
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
HEADER_FLAGS = (b'\x04\x00', b'\x00\x01', b'\x00\x00')

def framed(body, name):
    import changed_classes
    for t, (n, _a, _b) in changed_classes.changed().items():
        if n != name:
            continue
        nb = struct.pack('<I', t)
        k = body.find(nb, 6)
        while k >= 6:
            if body[k - 6:k - 4] in HEADER_FLAGS:
                return True
            k = body.find(nb, k + 1)
    return False

def manager_list(body):
    k = body.find(struct.pack('<I', convert.class_ids()['RegionLayoutManager']))
    if k < 0:
        raise SystemExit('no RegionLayoutManager in the World record')
    n = struct.unpack_from('<I', body, k + 4)[0]
    if n > 64:
        raise SystemExit(f'RegionLayoutManager count {n}: not a list')
    uids = []
    for i in range(n):
        e = body[k + 8 + 6 * i:k + 14 + 6 * i]
        if e[:2] != b'\x01\x00':
            raise SystemExit('RegionLayoutManager entry not `01 00 uid`')
        uids.append(struct.unpack_from('<I', e, 2)[0])
    return (k + 4, uids)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--map', default='DataPC_ACFE_Souk.forge', help='the ACR archive')
    ap.add_argument('--world-file', default='ACFE_Souk')
    ap.add_argument('--layout', default='CrowdRegion')
    a = ap.parse_args(argv)
    ids = convert.class_ids()
    acr = next((p for p in mapwalk.payloads('ACR', a.map) if p.rec.name == a.world_file))
    idx = next((i for i, o in enumerate(acr.objects) if o['name'] == a.layout and xrefs.cls('ACR', o['type']) == 'RegionLayout'))
    lay = acr.objects[idx]
    acr_world = next((o for o in acr.objects if o['type'] == ids['World']))
    _c, acr_list = manager_list(acr_world['body'])
    if lay['uid'] not in acr_list:
        raise SystemExit("ACR's own manager list doesn't name the layout")
    body, log = convert.convert(lay['body'], convert.targets('ACR'))
    bad = {n for n in build_static.unknown_checker()(body) if framed(body, n)}
    if bad:
        raise SystemExit(f'layout still holds unconverted classes: {sorted(bad)}')
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    p = next((p for p in payloads if p.rec.name == a.world_file))
    if any((o['has_uid'] and o['uid'] == lay['uid'] for o in p.objects)):
        raise SystemExit('the build already defines the layout')
    wi = next((i for i, o in enumerate(p.objects) if o['type'] == ids['World']))
    wbody = p.objects[wi]['body']
    cnt_at, cur_list = manager_list(wbody)
    pos = acr_list.index(lay['uid'])
    before = [u for u in reversed(acr_list[:pos]) if u in cur_list]
    at = cur_list.index(before[0]) + 1 if before else 0
    new_list = cur_list[:at] + [lay['uid']] + cur_list[at:]
    entries = b''.join((b'\x01\x00' + struct.pack('<I', u) for u in new_list))
    end = cnt_at + 4 + 6 * len(cur_list)
    wbody = wbody[:cnt_at] + struct.pack('<I', len(new_list)) + entries + wbody[end:]
    prev = [o['uid'] for o in reversed(acr.objects[:idx]) if o['has_uid']]
    have = [o['uid'] if o['has_uid'] else None for o in p.objects]
    ins = next((have.index(u) + 1 for u in prev if u in have), len(p.objects))
    new_obj = dict(lay, body=body)
    objs = []
    for i, o in enumerate(p.objects):
        if i == ins:
            objs.append((lay['serial'], mapwalk.object_bytes(new_obj, body), lay['filter']))
        objs.append((o['serial'], mapwalk.object_bytes(o, wbody if i == wi else o['body']), o['filter']))
    if ins == len(p.objects):
        objs.append((lay['serial'], mapwalk.object_bytes(new_obj, body), lay['filter']))
    d = mapwalk.rebuild(objs, p.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    defined = set()
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            q = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            for o in q.objects:
                if o['has_uid']:
                    defined.add(o['uid'])
                    if o['uid'] == lay['uid']:
                        v['layout defined'] += 1
                        v['layout bytes as converted'] += o['body'] == body
                if r.name == a.world_file and o['type'] == ids['World']:
                    v['manager list as planned'] = manager_list(o['body'])[1] == new_list
    import inventory
    acr_uids = set(inventory.universe('ACR'))
    named = {struct.unpack_from('<I', body, j + 2)[0] for j in range(len(body) - 6) if body[j] in (0, 1, 2) and body[j + 1] in (0, 1, 2)}
    named &= acr_uids
    v['uids the layout names that ACR defines'] = len(named)
    v['... not defined in the build'] = len(named - defined)
    report = [f"{a.world_file}: {a.layout} ({lay['uid']:#010x}, {len(body)} B) added at object {ins}; RegionLayoutManager {len(cur_list)} -> {len(new_list)} (index {at}); rules {log}", f'verify: {dict(v)}']
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
