"""forgelib/maps/acr_templates.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import convert
import import_templates
import inventory
import mapwalk
import template_convert
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N11_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--map', default='DataPC_ACFE_Souk.forge', help='the ACR archive')
    ap.add_argument('--world', default='ACFE_Souk')
    ap.add_argument('--into', default='ACFE_Souk', help='where the converted templates go: the World file (N14/N15, ignored in game) or Cell01364_DataBlock as listed pieces (N17)')
    ap.add_argument('--prefix', default='N14')
    ap.add_argument('--only', default=None, help='comma-separated ACR template names (default: every ACR-only one)')
    ap.add_argument('--from-substitutes', action='store_true', help="also repoint materials on N13's ACR-era AC3 substitutes (N18)")
    a = ap.parse_args(argv)
    only = set(a.only.split(',')) if a.only else None
    ac3 = inventory.universe('AC3')
    tid = convert.class_ids()['MaterialTemplate']
    tmpl, orig = ({}, {})
    for p in mapwalk.payloads('ACR', a.map):
        for o in p.objects:
            c = xrefs.cls('ACR', o['type'])
            if c == 'MaterialTemplate' and o['uid'] not in ac3:
                n = (o['name'] or '').strip()
                if only is None or n in only:
                    tmpl.setdefault(o['uid'], o)
            elif c == 'Material':
                for _k, u in import_templates.refs(o['body']):
                    h = inventory.universe('ACR').get(u)
                    if h and xrefs.cls('ACR', h[0][2]) == 'MaterialTemplate':
                        orig[o['uid']] = u
                        break
    converted = {}
    for u, o in tmpl.items():
        body, _c = template_convert.convert(o['body'])
        converted[u] = (o['name'], body)
    from_names = set(import_templates.STAND_INS)
    if a.from_substitutes:
        from_names |= set(import_templates.MAP_ACR_ERA.values())
    stand_in = {u for u, h in ac3.items() if xrefs.cls('AC3', h[0][2]) == 'MaterialTemplate' and h[0][3].strip() in from_names}
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    defined = {o['uid'] for p in payloads for o in p.objects if o['has_uid']}

    def repoint(o):
        body = bytearray(o['body'])
        for k, u in import_templates.refs(o['body']):
            if u in stand_in:
                struct.pack_into('<I', body, k, orig[o['uid']])
                break
        return bytes(body)
    referrer = {}
    for p in payloads:
        for o in p.objects:
            if xrefs.cls('ACR', o['type']) == 'Material' and orig.get(o['uid']) in converted:
                referrer.setdefault(orig[o['uid']], o)
    counts = Counter()
    for p in payloads:
        changed = {}
        for i, o in enumerate(p.objects):
            c = xrefs.cls('ACR', o['type'])
            if c == 'MaterialTemplate' and o['uid'] in converted:
                changed[i] = converted[o['uid']][1]
                counts['standalone/in-archive copies replaced'] += 1
            elif c == 'Material' and orig.get(o['uid']) in converted:
                body = bytearray(o['body'])
                for k, u in import_templates.refs(o['body']):
                    if u in stand_in:
                        struct.pack_into('<I', body, k, orig[o['uid']])
                        changed[i] = bytes(body)
                        counts['material copies repointed'] += 1
                        break
        add = []
        if p.rec.name == a.into:
            here = {o['uid'] for o in p.objects if o['has_uid']}
            add = [(u, n, b) for u, (n, b) in converted.items() if u not in here]
        if not changed and (not add):
            continue
        objs = [(o['serial'], mapwalk.object_bytes(o, changed.get(i, o['body'])), o['filter']) for i, o in enumerate(p.objects)]
        add_done = False
        if add and a.into != a.world:
            g = objs[0]
            gb = changed.get(0, p.objects[0]['body'])
            n0 = struct.unpack_from('<I', gb, 10)[0]
            end = 14 + 6 * n0
            listed = {struct.unpack_from('<I', gb, k + 2)[0] for k in range(14, end, 6)}
            new_e = b''.join((b'\x01\x00' + struct.pack('<I', u) for u, _n, _b in add if u not in listed))
            gb = gb[:10] + struct.pack('<I', n0 + len(new_e) // 6) + gb[14:end] + new_e + gb[end:]
            objs[0] = (g[0], mapwalk.object_bytes(p.objects[0], gb), g[2])
            for u, n, b in add:
                nb = n.encode('latin-1')
                objs.append((u, struct.pack('<III', tid, len(b) - 1, len(nb)) + nb + b, None))
            add_done = True
        for u, n, b in add if not add_done else []:
            ref = referrer.get(u)
            if ref is not None and ref['uid'] not in here:
                objs.append((ref['serial'], mapwalk.object_bytes(ref, repoint(ref)), None))
                here.add(ref['uid'])
                counts['referrer materials placed before a template'] += 1
            nb = n.encode('latin-1')
            objs.append((u, struct.pack('<III', tid, len(b) - 1, len(nb)) + nb + b, None))
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
        counts[f'templates added to {a.into}'] += len(add)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    report = ['templates: ' + ', '.join(sorted((n.strip() for n, _b in converted.values())))]
    report += [f'{k}: {v}' for k, v in sorted(counts.items())]
    v = Counter()
    written = ArchiveImage.read(dest)
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            for o in p.objects:
                if r.name == a.into and o['uid'] in converted:
                    v[f'in {a.into}, converted'] += o['body'] == converted[o['uid']][1]
                if xrefs.cls('ACR', o['type']) == 'Material' and orig.get(o['uid']) in converted:
                    us = {u for _k, u in import_templates.refs(o['body'])}
                    v['materials'] += 1
                    v['materials on their ACR template'] += orig[o['uid']] in us
                    v['materials still on a stand-in'] += bool(us & stand_in)
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
