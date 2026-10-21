"""forgelib/maps/port/sky_evening.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import mapwalk
import sky_color as sc
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
DATA = sc.DATA
DUSK = ['226,150,98', '214,140,100', '180,120,112', '140,100,116', '100,84,118', '70,66,112', '48,52,100', '36,42,88']

def stretched(rows, h):
    if len(rows) == h or h < 2 or len(rows) < 2:
        return rows
    out = []
    for y in range(h):
        t = y * (len(rows) - 1) / (h - 1)
        i = min(int(t), len(rows) - 2)
        f = t - i
        out.append(tuple((round(rows[i][k] * (1 - f) + rows[i + 1][k] * f) for k in range(3))))
    return out

def donor_rows(archive, hour):
    import re
    import dxt
    sky = re.compile('(?i)sky.*colo')
    for p in mapwalk.payloads('ACR', archive, skip_errors=True):
        if not any((o['type'] == sc.WORLD for o in p.objects)):
            continue
        for o in p.objects:
            if not (o['name'] and sky.search(o['name'])):
                continue
            hd = dxt.header(o['body'])
            if hd['format'] != 0:
                raise SystemExit(f"{archive}: {o['name']} is format {hd['format']}, not uncompressed (sky_color.py takes DXT skies)")
            w, h, b = (hd['width'], hd['height'], o['body'])
            t = (hour + sc.SHIFT) / 24.0 * w - 0.5
            c0 = int(t // 1)
            f = t - c0
            ca, cb = (c0 % w, (c0 + 1) % w)
            px = lambda x, y, k: b[DATA + (y * w + x) * 4 + (2 - k)]
            return [tuple((round(px(ca, y, k) * (1 - f) + px(cb, y, k) * f) for k in range(3))) for y in range(h)]
    raise SystemExit(f'{archive}: no World file with a sky lookup')

def levels(w, h, mips):
    out, off = ([], DATA)
    for _ in range(mips):
        out.append((off, w, h))
        off += w * h * 4
        w, h = (max(1, w // 2), max(1, h // 2))
    return (out, off)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--texture', required=True)
    ap.add_argument('--hour', type=float, default=20.0)
    ap.add_argument('--rows', help="r,g,b per row, horizon first, ';'-separated (default: DUSK, stretched to the lookup's height)")
    ap.add_argument('--from', dest='donor', help="an ACR archive whose own sky lookup gives the rows at --hour (Rome's Night: Firenze's)")
    a = ap.parse_args(argv)
    if a.donor and a.rows:
        raise SystemExit('--rows or --from, not both')
    rows = donor_rows(a.donor, a.hour) if a.donor else [tuple((int(x) for x in r.split(','))) for r in (a.rows or ';'.join(DUSK)).split(';')]
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    report, done, hit, old = ([], 0, None, None)
    with Forge(src) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            p = mapwalk.Payload(rec, f.read(rec))
            tex = next((o for o in p.objects if (o['name'] or '').strip() == a.texture), None)
            if tex is None:
                continue
            import dxt
            hd = dxt.header(tex['body'])
            w, h, mips = (hd['width'], hd['height'], hd['mips'])
            if not a.rows:
                rows = stretched(rows, h)
            assert hd['format'] == 0 and len(rows) == h, f"{a.texture}: format {hd['format']}, {w} x {h} (rows given {len(rows)})"
            t = (a.hour + sc.SHIFT) / 24.0 * w - 0.5
            c0 = int(t // 1)
            cols = sorted({c0 % w, (c0 + 1) % w})
            lv, end = levels(w, h, mips)
            body = bytearray(tex['body'])
            for c in cols:
                for y, (r, g, b) in enumerate(rows):
                    o = lv[0][0] + (y * w + c) * 4
                    body[o:o + 3] = bytes((b, g, r))
            span = {c for c in cols}
            for li in range(1, len(lv) - 1):
                (po, pw, ph), (co, cw, ch) = (lv[li - 1], lv[li])
                span = {c * cw // pw for c in span}
                for c in span:
                    for y in range(ch):
                        for k in range(3):
                            vals = [body[po + (min(ph - 1, y * ph // ch + dy) * pw + min(pw - 1, c * pw // cw + dx)) * 4 + k] for dy in range(ph // ch if ph > ch else 1) for dx in range(pw // cw if pw > cw else 1)]
                            body[co + (y * cw + c) * 4 + k] = round(sum(vals) / len(vals))
            new_objs = [(o['serial'], mapwalk.object_bytes(o, bytes(body) if o is tex else o['body']), o['filter']) for o in p.objects]
            d = mapwalk.rebuild(new_objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, rec.index, payload)
            hit = rec.index
            old = tex['body']
            report.append(f'{a.texture} ({w} x {h}, {mips} mips): hour {a.hour:g} reads columns {cols}; rows {rows}')
            done += 1
    if done != 1:
        raise SystemExit(f'{a.texture}: found in {done} files')
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = True
    with Forge(dest) as f, Forge(src) as g:
        for x, y in zip(f.records, g.records):
            if x.name == 'GlobalMetaFile' or not x.size:
                continue
            if f.read(x) != g.read(y) and x.index != hit:
                ok = False
        q = next((o for o in mapwalk.Payload(f.records[hit], f.read(f.records[hit])).objects if (o['name'] or '').strip() == a.texture))
        diff = [i for i in range(len(old)) if old[i] != q['body'][i]]
        lv, end = levels(w, h, mips)
        alpha_hit = [i for i in diff if i >= DATA and (i - DATA) % 4 == 3 and (i < end)]
        before_data = [i for i in diff if i < DATA]
        ok &= not alpha_hit and (not before_data) and (len(q['body']) == len(old))
    report.append(f"verify: {len(diff)} bytes differ (alpha bytes {len(alpha_hit)}, header bytes {len(before_data)}), every other record byte-identical: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
