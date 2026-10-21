"""forgelib/crossmaps/acb_sky.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (os.path.join(ROOT, 'maps', 'port'), os.path.join(ROOT, 'maps'), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import dxt
import mapwalk
import sky_color as sc
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')

def resample(col, h):
    return [col[min(len(col) - 1, int(y * len(col) / h))] for y in range(h)]

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--texture', required=True, help="the sky-colour lookup texture's name in the World file")
    ap.add_argument('--night-from', help='an ACR archive whose sky at 0:00 becomes the night')
    ap.add_argument('--evening', help='R,G,B: the mean colour of the sky around 20:00')
    ap.add_argument('--max-error', type=float, default=12.0, help="the verify's bound on the DXT error of a placed texel (Villa's 32 x 16 lookup: white haze at the horizon, the evening's scaled rows clip there and a 4-row block holds a bent gradient - 18.8 levels; Forli 12)")
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    donor = None
    if a.night_from:
        wp = next((p for p in mapwalk.payloads('ACR', a.night_from, skip_errors=True) if any((o['type'] == sc.WORLD for o in p.objects))))
        dt = sc.sky_texture(wp.objects)
        dw, dh, dpx = dxt.decode(dt['body'])
        donor = (dt['name'], dw, dh, dpx)
    report, changed = ([], None)
    with Forge(src) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            p = mapwalk.Payload(rec, f.read(rec))
            tex = next((o for o in p.objects if o['name'] == a.texture), None)
            if tex is None:
                continue
            hd = dxt.header(tex['body'])
            assert hd['format'] in (2, 3, 4, 5), f"{tex['name']}: format {hd['format']}"
            w, h, px = dxt.decode(tex['body'])
            rgba = dxt.pixels(px)
            plan = {}
            if donor:
                name, dw, dh, dpx = donor
                col = resample(sc.column(dw, dh, dpx, 0.0), h)
                for c in sc.band(w, 0.0):
                    plan[c] = col
                report.append(f"night: columns {sc.band(w, 0.0)} take {name}'s sky at 0:00 ({a.night_from}), {dh} rows -> {h}")
            if a.evening:
                target = tuple((float(x) for x in a.evening.split(',')))
                for c in sc.band(w, 20.0):
                    if c in plan:
                        raise SystemExit(f'column {c} placed twice')
                    rows = [tuple((rgba[y * w + c][k] for k in range(3))) for y in range(h)]
                    mean = [sum((r[k] for r in rows)) / h for k in range(3)]
                    plan[c] = [tuple((max(0.0, min(255.0, r[k] * target[k] / mean[k])) for k in range(3))) for r in rows]
                report.append(f'evening: columns {sc.band(w, 20.0)} scaled to the mean colour {a.evening}')
            for c, col in plan.items():
                for y in range(h):
                    rgba[y * w + c] = tuple((int(round(v)) for v in col[y])) + (rgba[y * w + c][3],)
            body = tex['body']
            old = body[sc.DATA:sc.DATA + hd['size']]
            build = sc.chain_dxt1 if hd['format'] in (2, 3) else sc.chain
            body = body[:sc.DATA] + build(w, h, rgba, hd['mips'], old, plan) + body[sc.DATA + hd['size']:]
            report.append(f"{rec.name}: {tex['name']} {w}x{h}, {hd['mips']} mips, {len(plan)} columns rewritten")
            objs = [(o['serial'], mapwalk.object_bytes(o, body if o is tex else None), o['filter']) for o in p.objects]
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, rec.index, payload)
            changed = (rec.index, tex['name'], tex['body'], plan)
            break
    if changed is None:
        raise SystemExit(f'{a.texture} not found')
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = True
    with Forge(dest) as fo, Forge(src) as fi:
        others = all((fo.read(x) == fi.read(y) for x, y in zip(fo.records, fi.records) if x.index != changed[0]))
        q = mapwalk.Payload(fo.records[changed[0]], fo.read(fo.records[changed[0]]))
        qi = mapwalk.Payload(fi.records[changed[0]], fi.read(fi.records[changed[0]]))
        nt = next((o for o in q.objects if o['name'] == changed[1]))
        ob, plan = (changed[2], changed[3])
        nb = nt['body']
        hd = dxt.header(ob)
        ok &= len(nb) == len(ob) and nb[:sc.DATA] == ob[:sc.DATA] and (nb[sc.DATA + hd['size']:] == ob[sc.DATA + hd['size']:])
        if hd['format'] in (4, 5):
            ok &= all((nb[sc.DATA + i:sc.DATA + i + 8] == ob[sc.DATA + i:sc.DATA + i + 8] for i in range(0, hd['size'], 16)))
        w, h, npx = dxt.decode(nb)
        _w, _h, opx = dxt.decode(ob)
        worst_placed = max((abs(npx[(y * w + c) * 4 + k] - plan[c][y][k]) for c in plan for y in range(h) for k in range(3)))
        worst_kept = max((abs(npx[(y * w + c) * 4 + k] - opx[(y * w + c) * 4 + k]) for c in range(w) if c not in plan for y in range(h) for k in range(3)), default=0)
        ok &= worst_placed <= a.max_error and worst_kept == 0
        rest = [o['body'] for o in q.objects if o['name'] != changed[1]] == [o['body'] for o in qi.objects if o['name'] != changed[1]]
        ok &= rest and others
    report.append(f"verify: header / alpha halves unchanged; placed columns within {worst_placed} of their source, other columns within {worst_kept} of the original (DXT error); the World file's other objects unchanged {rest}; every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
