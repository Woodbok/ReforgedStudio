"""forgelib/maps/import_templates.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import inventory
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
MAP = {'ACFE_Tex2_PixelLit': 'AC3MP_Tex2_PixelLit', 'AC2_Tex2_PixelLit': 'AC3MP_Tex2_PixelLit', 'ACFE_Tex1_PixelLit_VertexAnim': 'AC3MP_Tex1_PixelLit_VertexAnim', 'AC2_Tex1_PixelLit_VertexAnim': 'AC3MP_Tex1_PixelLit_VertexAnim', 'ACFE_Tex1_PixelLit_VertexAnim_Color': 'AC3MP_Tex1_PixelLit_VertexAnim_VColor', 'ACFE_Tex1_VertexLit_LODFadeOut': 'AC3MP_Tex1_VertexLit_LODFadeOut', 'AC2_Tex1_VertexLit_LODFadeOut': 'AC3MP_Tex1_VertexLit_LODFadeOut', 'AC2MP_Tex1_VertexLit_LODFadeOut': 'AC3MP_Tex1_VertexLit_LODFadeOut', 'ACFE_Tex1_PixelLit_Specular_Cubemap': 'AC3MP_Tex1_PixelLit_Specular_Cubemap', 'ACFE_WaterSea': 'AC3MP_WaterSea_OLD', 'ACFE_Windows_PixelLit': 'AC3MP_Window_PixelLit_Alpha', 'GEN_DiffuseAlpha': 'AC3MP_Tex1_PixelLit_Alpha', 'ACFE_Tex1_VertexLit': 'AC2_Tex1_VertexLit', 'ACFE_Tex1_Unlit': 'AC2_Tex1_Unlit'}
MAP_ACR_ERA = {'ACFE_Tex1_VertexLit': 'AC2_Tex1_VertexLit', 'ACFE_Tex1_VertexLit_LODFadeOut': 'AC2_Tex1_VertexLit', 'AC2_Tex1_VertexLit_LODFadeOut': 'AC2_Tex1_VertexLit', 'AC2MP_Tex1_VertexLit_LODFadeOut': 'AC2_Tex1_VertexLit', 'ACFE_Tex1_PixelLit_Specular_Cubemap': 'AC2_Tex1_PixelLit_Specular_Cubemap', 'ACFE_Tex1_Unlit': 'AC2_Tex1_Unlit'}
STAND_INS = ('ACFE_Tex1_PixelLit', 'AC2_Tex1_PixelLit')

def refs(body):
    out = []
    for j in range(10, len(body) - 5):
        if body[j] == 1 and body[j + 1] in (0, 1, 2):
            out.append((j + 2, struct.unpack_from('<I', body, j + 2)[0]))
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N11_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--map', default='DataPC_ACFE_Souk.forge', help='the ACR archive (original templates)')
    ap.add_argument('--world', default='ACFE_Souk', help='the World file, where the templates go')
    ap.add_argument('--prefix', default='N12')
    ap.add_argument('--only', default=None, help='comma-separated ACR template names to handle (default all)')
    ap.add_argument('--era', choices=('ac3', 'acr'), default='ac3', help="ac3: the AC3MP_* counterparts (N12, invisible); acr: AC3's ACR/AC2-era templates only (N13)")
    a = ap.parse_args(argv)
    only = set(a.only.split(',')) if a.only else None
    base = MAP_ACR_ERA if a.era == 'acr' else MAP
    mapping = {k: v for k, v in base.items() if only is None or k in only}
    acr = inventory.universe('ACR')
    ac3 = inventory.universe('AC3')

    def tname(game, u):
        U = acr if game == 'ACR' else ac3
        h = U.get(u)
        return h[0][3].strip() if h and xrefs.cls(game, h[0][2]) == 'MaterialTemplate' else None
    orig = {}
    for p in mapwalk.payloads('ACR', a.map):
        for o in p.objects:
            if xrefs.cls('ACR', o['type']) == 'Material':
                for _k, u in refs(o['body']):
                    n = tname('ACR', u)
                    if n:
                        orig[o['uid']] = n
                        break
    want = sorted(set(mapping.values()))
    found = {}
    for u, homes in ac3.items():
        for arc, f, t, n in homes:
            n = n.strip()
            if n in want and xrefs.cls('AC3', t) == 'MaterialTemplate':
                shared = arc == 'DataPC.forge'
                if n not in found or (shared and found[n][1] is not None):
                    found[n] = (u, None if shared else arc)
    missing = [n for n in want if n not in found]
    if missing:
        raise SystemExit(f'no AC3 template named {missing}')
    stand_in = {}
    for u, homes in ac3.items():
        if tname('AC3', u) in STAND_INS:
            stand_in[u] = tname('AC3', u)
    copies = {}
    for n, (u, arc) in found.items():
        if arc is None:
            continue
        for p in mapwalk.payloads('AC3', arc, skip_errors=True):
            for o in p.objects:
                if o['has_uid'] and o['uid'] == u:
                    copies[n] = o
            if n in copies:
                break
    report = [f'templates: ' + ', '.join((f"{n} 0x{u:08X} ({('copied from ' + arc if arc else 'AC3 DataPC, shared')})" for n, (u, arc) in sorted(found.items())))]
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    defined = {o['uid'] for p in payloads for o in p.objects if o['has_uid']}
    counts = Counter()
    for p in payloads:
        changed = {}
        for i, o in enumerate(p.objects):
            if xrefs.cls('ACR', o['type']) != 'Material':
                continue
            new = mapping.get(orig.get(o['uid'], ''))
            if not new:
                continue
            body = bytearray(o['body'])
            for k, u in refs(o['body']):
                if u in stand_in:
                    struct.pack_into('<I', body, k, found[new][0])
                    changed[i] = bytes(body)
                    counts[f"{orig[o['uid']]} -> {new}"] += 1
                    break
        add = []
        if p.rec.name == a.world:
            add = [o for n, o in sorted(copies.items()) if o['uid'] not in defined]
        if not changed and (not add):
            continue
        objs = [(o['serial'], mapwalk.object_bytes(o, changed.get(i, o['body'])), o['filter']) for i, o in enumerate(p.objects)]
        objs += [(o['serial'], mapwalk.object_bytes(o), None) for o in add]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
        counts['templates copied'] += len(add)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    report += [f'{k}: {v}' for k, v in sorted(counts.items())]
    v = Counter()
    written = ArchiveImage.read(dest)
    seen = Counter()
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            for o in p.objects:
                for n, c in copies.items():
                    if o['has_uid'] and o['uid'] == c['uid']:
                        seen[n] += 1
                        v['copies identical'] += o['body'] == c['body']
                if xrefs.cls('ACR', o['type']) == 'Material' and mapping.get(orig.get(o['uid'], '')):
                    us = {u for _k, u in refs(o['body'])}
                    v['mapped materials'] += 1
                    v['mapped materials on their new template'] += found[mapping[orig[o['uid']]]][0] in us
                    v['mapped materials still on a stand-in'] += bool(us & set(stand_in))
    v['copies defined once'] = sum((1 for n in copies if seen[n] == 1))
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
