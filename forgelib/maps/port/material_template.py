"""forgelib/maps/port/material_template.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import inventory
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
MATERIAL = 2244483011
TEMPLATE_AT = 12

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--material', action='append', required=True, metavar='MATERIAL=TEMPLATE')
    a = ap.parse_args(argv)
    want = dict((m.split('=', 1) for m in a.material))
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    templates = {}
    for p in payloads:
        for o in p.objects:
            if xrefs.cls('AC3', o['type']) == 'MaterialTemplate' and o['has_uid']:
                templates.setdefault(o['name'], o['uid'])
    shared = {h[3]: u for u, homes in inventory.universe('AC3').items() for h in homes if len(h) > 3 and h[0] in inventory.SHARED and (xrefs.cls('AC3', h[2]) == 'MaterialTemplate')}
    target = {}
    for m, t in want.items():
        uid = templates.get(t, shared.get(t))
        if uid is None:
            raise SystemExit(f"template {t} is neither in the archive nor in AC3's shared archives")
        target[m] = uid
    changed, report, touched = ({}, [], set())
    for p in payloads:
        objs, hit = ([], False)
        for i, o in enumerate(p.objects):
            body = o['body']
            if o['name'] in target and xrefs.cls('AC3', o['type']) == 'Material':
                if struct.unpack_from('<I', body, 6)[0] != MATERIAL or body[10:12] != b'\x01\x00':
                    raise SystemExit(f"{o['name']}: no `01 00 <template>` at +10")
                old = struct.unpack_from('<I', body, TEMPLATE_AT)[0]
                body = body[:TEMPLATE_AT] + struct.pack('<I', target[o['name']]) + body[TEMPLATE_AT + 4:]
                changed[p.rec.index, i] = target[o['name']]
                touched.add(p.rec.index)
                report.append(f"{p.rec.name}: {o['name']}: template {old:#010x} -> {target[o['name']]:#010x} ({want[o['name']]})")
                hit = True
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        if hit:
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, p.rec.index, payload)
    found = {p.objects[i]['name'] for p in payloads for ri, i in changed if ri == p.rec.index}
    if found != set(target):
        raise SystemExit(f'materials not found: {sorted(set(target) - found)}')
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok, n = (True, 0)
    written = ArchiveImage.read(dest)
    with Forge(dest) as f, Forge(src) as g:
        old_recs = {r.index: r for r in g.records}
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            q = mapwalk.Payload(r, f.read(r))
            n += 1
            ok &= struct.unpack_from('<I', written.files[r.index].entry, 8)[0] == q.objects[0]['uid']
            if r.index not in touched:
                ok &= f.read(r) == g.read(old_recs[r.index])
                continue
            p0 = mapwalk.Payload(old_recs[r.index], g.read(old_recs[r.index]))
            for i, (o, o0) in enumerate(zip(q.objects, p0.objects)):
                b, b0 = (o['body'], o0['body'])
                if (r.index, i) in changed:
                    ok &= len(b) == len(b0) and struct.unpack_from('<I', b, TEMPLATE_AT)[0] == changed[r.index, i] and (b[:TEMPLATE_AT] == b0[:TEMPLATE_AT]) and (b[TEMPLATE_AT + 4:] == b0[TEMPLATE_AT + 4:])
                else:
                    ok &= b == b0
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    print(f"verify: {n} files walk, entry keys, {len(changed)} materials changed only in their template reference, every other object / file byte-identical: {('OK' if ok else 'FAILED')}")
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
