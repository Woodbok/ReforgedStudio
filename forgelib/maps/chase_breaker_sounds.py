"""forgelib/maps/chase_breaker_sounds.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, 'audio'))
import chase_breakers
import mapwalk
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N63_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='N65')
    ap.add_argument('--some', action='store_true', help="a second pass over objects added later (Palazzio's wooden doors, PA10): at least one event found, not every one")
    a = ap.parse_args(argv)
    mapping = chase_breakers.EVENTS
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    changed, counts = ({}, {})
    with Forge(src) as f:
        raw_before = {r.index: f.read(r) for r in f.records}
        for rec in f.records:
            try:
                p = mapwalk.Payload(rec, raw_before[rec.index])
            except Exception:
                continue
            objs, touched = ([], [])
            for o in p.objects:
                body, c = chase_breakers.remap_body(o['body'], mapping)
                for k, v in c.items():
                    counts[k] = counts.get(k, 0) + v
                if body != o['body']:
                    touched.append(o['name'])
                objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
            if touched:
                d = mapwalk.rebuild(objs, p.filter_table)
                payload = compress(d, first_chunk=mapwalk.first_chunk(d))
                assert decompress(payload) == d
                set_payload(image, rec.index, payload)
                changed[rec.index] = (p, touched)
    report = [f'chase-breaker event {old:#010x} -> {new:#010x}: {counts.get(old, 0)} references' for old, new in mapping.items()]
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = True
    with Forge(dest) as f:
        ok &= len(f.records) == len(raw_before)
        others = all((f.read(r) == raw_before[r.index] for r in f.records if r.index not in changed))
        ok &= others
        left = 0
        for idx, (p, _touched) in changed.items():
            q = mapwalk.Payload(f.records[idx], f.read(f.records[idx]))
            ok &= len(q.objects) == len(p.objects)
            for x, y in zip(p.objects, q.objects):
                exp, _c = chase_breakers.remap_body(x['body'], mapping)
                ok &= y['body'] == exp
                left += sum(chase_breakers.remap_body(y['body'], mapping)[1].values())
    ok &= left == 0 and (any if a.some else all)((counts.get(o) for o in mapping))
    report.append(f"verify: {len(changed)} records re-encoded ({sum((len(t) for _p, t in changed.values()))} objects, each differing only in those ids); every other record byte-identical {others}; old event references left {left}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
