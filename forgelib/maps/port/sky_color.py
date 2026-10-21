"""forgelib/maps/port/sky_color.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import re
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import dxt
import mapwalk
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
WORLD = 4223024711
SKY = re.compile('_SkyColor_', re.I)
DATA = 90
SHIFT = 3.0
PAINT = [('red', (255, 0, 0)), ('orange', (255, 128, 0)), ('yellow', (255, 255, 0)), ('green', (0, 255, 0)), ('cyan', (0, 255, 255)), ('blue', (0, 0, 255)), ('magenta', (255, 0, 255)), ('white', (255, 255, 255))]

def sky_texture(objects):
    return next((o for o in objects if o['name'] and SKY.search(o['name']) and (dxt.header(o['body'])['format'] in dxt.BLOCK)))

def column(w, h, px, hour):
    t = (hour + SHIFT) / 24.0 * w - 0.5
    c0 = int(t // 1)
    f = t - c0
    a, b = (c0 % w, (c0 + 1) % w)
    return [tuple((px[(y * w + a) * 4 + k] * (1 - f) + px[(y * w + b) * 4 + k] * f for k in range(3))) for y in range(h)]

def band(w, hour):
    c0 = int(((hour + SHIFT) / 24.0 * w - 0.5) // 1)
    blocks = sorted({(c0 + d) % w // 4 for d in (0, 1)})
    return [b * 4 + i for b in blocks for i in range(4)]

def chain_dxt1(w, h, rgba, mips, old, placed):
    new = bytearray(dxt.mip_chain_dxt1(w, h, [(r_, g_, b_) for r_, g_, b_, _a in rgba], mips))
    assert len(new) == len(old), (len(new), len(old))
    bw = max(1, w // 4)
    cols = {c - c % 4 for c in placed}
    for i in range(0, bw * max(1, h // 4) * 8, 8):
        if i // 8 % bw * 4 not in cols:
            new[i:i + 8] = old[i:i + 8]
    return bytes(new)

def chain(w, h, rgba, mips, old, placed):
    new = bytearray(dxt.mip_chain_dxt5(w, h, [(r_, g_, b_, 255) for r_, g_, b_, _a in rgba], mips))
    assert len(new) == len(old), (len(new), len(old))
    bw = max(1, w // 4)
    for i in range(0, len(new), 16):
        new[i:i + 8] = old[i:i + 8]
        blk = i // 16
        if blk < bw * max(1, h // 4) and blk % bw * 4 not in {c - c % 4 for c in placed}:
            new[i + 8:i + 16] = old[i + 8:i + 16]
    return bytes(new)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--at', action='append', default=[], metavar='FROM=TO', help='the sky at FROM shows at TO')
    ap.add_argument('--night-from', help="an ACR archive whose sky at 0:00 becomes the night (Galata's)")
    ap.add_argument('--paint-bands', action='store_true', help='diagnostic, not for the chain: paint the 8 four-column bands 8 solid colours (PAINT) so the colour seen in game names the columns each hour reads')
    a = ap.parse_args(argv)
    points = [tuple((float(x) for x in s.split('='))) for s in a.at]
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    report, changed = ([], None)
    donor = None
    if a.night_from:
        wp = next((p for p in mapwalk.payloads('ACR', a.night_from, skip_errors=True) if any((o['type'] == WORLD for o in p.objects))))
        dt = sky_texture(wp.objects)
        dw, dh, dpx = dxt.decode(dt['body'])
        donor = (dt['name'], dw, dh, dpx)
    with Forge(src) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            p = mapwalk.Payload(rec, f.read(rec))
            if not any((o['type'] == WORLD for o in p.objects)):
                continue
            tex = sky_texture(p.objects)
            hd = dxt.header(tex['body'])
            assert hd['format'] in (2, 3, 4, 5), f"{tex['name']}: format {hd['format']}"
            w, h, px = dxt.decode(tex['body'])
            rgba = dxt.pixels(px)
            plan = {}
            if a.paint_bands:
                for i, (cname, rgb) in enumerate(PAINT):
                    for c in range(4 * i, 4 * i + 4):
                        plan[c] = [rgb] * h
                    report.append(f'paint: columns {4 * i}-{4 * i + 3} {cname} {rgb} (hours {4 * i * 24 / w:g}-{(4 * i + 4) * 24 / w:g} if column = hour / 24 x {w})')
            for frm, to in points:
                col = column(w, h, px, frm)
                for c in band(w, to):
                    plan[c] = col
                report.append(f'{frm:g}:00 -> {to:g}:00: columns {band(w, to)} take the sky at {frm:g}:00')
            if donor:
                name, dw, dh, dpx = donor
                assert (dw, dh) == (w, h), f"{name} is {dw}x{dh}, {tex['name']} {w}x{h}"
                col = column(dw, dh, dpx, 0.0)
                for c in band(w, 0.0):
                    assert c not in plan, f'column {c} placed twice'
                    plan[c] = col
                report.append(f"night: columns {band(w, 0.0)} take {name}'s sky at 0:00 ({a.night_from})")
            assert a.paint_bands or len(plan) == sum((len(band(w, to)) for _f, to in points)) + (len(band(w, 0.0)) if donor else 0), 'bands overlap'
            for c, col in plan.items():
                for y in range(h):
                    r_, g_, b_ = (int(round(v)) for v in col[y])
                    rgba[y * w + c] = (r_, g_, b_, rgba[y * w + c][3])
            body = tex['body']
            old = body[DATA:DATA + hd['size']]
            build = chain_dxt1 if hd['format'] in (2, 3) else chain
            body = body[:DATA] + build(w, h, rgba, hd['mips'], old, plan) + body[DATA + hd['size']:]
            report.append(f"{rec.name}: {tex['name']} {w}x{h}, {hd['mips']} mips, {len(plan)} columns rewritten")
            objs = [(o['serial'], mapwalk.object_bytes(o, body if o is tex else None), o['filter']) for o in p.objects]
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, rec.index, payload)
            changed = (rec.index, tex['name'], tex['body'], plan)
            break
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = changed is not None
    with Forge(dest) as fo, Forge(src) as fi:
        others = all((fo.read(x) == fi.read(y) for x, y in zip(fo.records, fi.records) if x.index != changed[0]))
        q = mapwalk.Payload(fo.records[changed[0]], fo.read(fo.records[changed[0]]))
        qi = mapwalk.Payload(fi.records[changed[0]], fi.read(fi.records[changed[0]]))
        nt = next((o for o in q.objects if o['name'] == changed[1]))
        ob, plan = (changed[2], changed[3])
        nb = nt['body']
        hd = dxt.header(ob)
        ok &= len(nb) == len(ob) and nb[:DATA] == ob[:DATA] and (nb[DATA + hd['size']:] == ob[DATA + hd['size']:])
        if hd['format'] in (4, 5):
            ok &= all((nb[DATA + i:DATA + i + 8] == ob[DATA + i:DATA + i + 8] for i in range(0, hd['size'], 16)))
        w, h, npx = dxt.decode(nb)
        _w, _h, opx = dxt.decode(ob)
        worst_placed = max((abs(npx[(y * w + c) * 4 + k] - plan[c][y][k]) for c in plan for y in range(h) for k in range(3)))
        worst_kept = max((abs(npx[(y * w + c) * 4 + k] - opx[(y * w + c) * 4 + k]) for c in range(w) if c not in plan for y in range(h) for k in range(3)), default=0)
        ok &= worst_placed <= 12 and worst_kept == 0
        rest = [o['body'] for o in q.objects if o['name'] != changed[1]] == [o['body'] for o in qi.objects if o['name'] != changed[1]]
        ok &= rest and others
    report.append(f"verify: texture size / header / alpha halves unchanged; placed columns within {worst_placed} of their source, other columns within {worst_kept} of the original (DXT error); the World file's other objects unchanged {rest}; every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
