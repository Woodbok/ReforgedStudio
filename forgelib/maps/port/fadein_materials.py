"""forgelib/maps/port/fadein_materials.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import convert
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
FADE_TEMPLATE, CLONE_TEMPLATE = ('ACFE_Realtree', 'AC3MP_Tex1_PixelLit')

def common(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    a = ap.parse_args(argv)
    cls = convert.class_ids()
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        raw_before = {r.index: f.read(r) for r in f.records}
        payloads = [mapwalk.Payload(r, raw_before[r.index]) for r in f.records if r.size and r.name != 'GlobalMetaFile']
    by = {}
    for p in payloads:
        for o in p.objects:
            if o.get('uid') is not None:
                by.setdefault(o['uid'], o)

    def refs(o, cname):
        b = o['body']
        return [u for u in dict.fromkeys((struct.unpack_from('<I', b, i)[0] for i in range(len(b) - 3))) if u in by and u != o['uid'] and (xrefs.cls('AC3', by[u]['type']) == cname)]

    def template(o):
        t = refs(o, 'MaterialTemplate')
        return (by[t[0]]['name'] or '').strip() if t else None
    mats = [o for p in payloads for o in p.objects if o['type'] == cls['Material'] and o.get('uid') is not None]
    clones = [(o, refs(o, 'TextureSet')) for o in mats if (o['name'] or '').strip().endswith('_AC3') and template(o) == CLONE_TEMPLATE]
    new_body, report, rewritten = ({}, [], 0)
    fades = [o for o in mats if (o['name'] or '').strip().endswith('FadeIn') and template(o) == FADE_TEMPLATE]
    for fo in fades:
        name = (fo['name'] or '').strip()
        tsets = refs(fo, 'TextureSet')
        cand = [(co, common(name.replace('FadeIn', ''), (co['name'] or '').strip().replace('FadeOut_AC3', ''))) for co, ts in clones if ts == tsets and 'FadeOut' in (co['name'] or '')]
        if not cand:
            if not clones:
                raise SystemExit(f'{name}: no FadeOut_AC3 clone with its TextureSet (run after realtree_convert.py)')
            report.append(f'{name}: kept as is (no FadeOut_AC3 clone with its TextureSet: no real tree of the species here)')
            continue
        best = max((c for _o, c in cand))
        top = {co['uid']: co for co, c in cand if c == best}
        if len(top) != 1:
            raise SystemExit(f"{name}: {len(top)} equally close clones {[(top[u]['name'] or '').strip() for u in top]}")
        co = next(iter(top.values()))
        cuid = struct.pack('<I', co['uid'])
        if co['body'].count(cuid) != 1:
            raise SystemExit(f"{co['name']}: its uid occurs {co['body'].count(cuid)} times in its body")
        for p in payloads:
            for o in p.objects:
                if o['type'] == cls['Material'] and o.get('uid') == fo['uid']:
                    new_body[id(o)] = co['body'].replace(cuid, struct.pack('<I', fo['uid']))
                    rewritten += 1
        report.append(f"{name} ({len(fo['body'])} B, {template(fo)}) <- {(co['name'] or '').strip()} ({len(co['body'])} B)")
    if not fades:
        raise SystemExit('no FadeIn materials on ACFE_Realtree in this archive: nothing to do')
    changed = set()
    for p in payloads:
        if not any((id(o) in new_body for o in p.objects)):
            continue
        objs = [(o['serial'], mapwalk.object_bytes(o, new_body.get(id(o), o['body'])), o['filter']) for o in p.objects]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
        changed.add(p.rec.index)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok_obj = other_obj = 0
    bad = 0
    with Forge(dest) as f:
        for r in f.records:
            if not r.size or r.name == 'GlobalMetaFile':
                continue
            if r.index not in changed:
                bad += f.read(r) != raw_before[r.index]
                continue
            q = mapwalk.Payload(r, f.read(r))
            old = next((p for p in payloads if p.rec.index == r.index))
            for x, y in zip(old.objects, q.objects):
                if id(x) in new_body:
                    ok_obj += y['body'] == new_body[id(x)]
                else:
                    other_obj += y['body'] == x['body']
                    bad += y['body'] != x['body']
    ok = ok_obj == rewritten and bad == 0
    report.append(f"verify: {rewritten} material definitions rewritten and read back {ok_obj == rewritten}; every other object / record unchanged {bad == 0} ({other_obj} objects compared): {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
    return dest
if __name__ == '__main__':
    main(sys.argv[1:])
