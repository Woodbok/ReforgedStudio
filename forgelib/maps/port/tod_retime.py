"""forgelib/maps/port/tod_retime.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import mapwalk
import tod_snow
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from tod_evening import HOUR, KINDS, curve, evaluate, find_tod
OUT = os.path.join(ROOT, 'out', 'maps')
DAY = 24 * HOUR

def plan(points):
    out = {int(round(t * HOUR)): int(round(f * HOUR)) for f, t in points}
    assert len(out) == len(points) and all((0 <= t <= DAY for t in out)), f'bad --at hours: {points}'
    if 0 in out and DAY not in out:
        out[DAY] = out[0]
    if DAY in out and 0 not in out:
        out[0] = out[DAY]
    out.setdefault(0, 0)
    out.setdefault(DAY, DAY)
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--at', action='append', required=True, metavar='FROM=TO', help='the look authored at FROM (hours) shows at TO (repeatable)')
    a = ap.parse_args(argv)
    points = [tuple((float(x) for x in s.split('='))) for s in a.at]
    where = plan(points)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    report = [f"place: {', '.join((f'{x:g}:00 -> {y:g}:00' for x, y in points))}; keys at {', '.join((f'{t / HOUR:g}' for t in sorted(where)))}"]
    changed = {}
    with Forge(src) as fg:
        for rec in fg.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            p = mapwalk.Payload(rec, fg.read(rec))
            tod = find_tod(p)
            if tod is None:
                continue
            b = tod['body']
            out, last, placed, kept = (bytearray(), 0, 0, 0)
            for s_, e_, _path in tod_snow.entries(b):
                c = curve(b[s_:e_])
                if not c:
                    continue
                k, cls, keys, vals, end = c
                new = {t: evaluate(cls, keys, vals, old) for t, old in where.items()}
                if len(set(new.values())) == 1 and len(set(vals)) == 1:
                    kept += 1
                    continue
                ticks = sorted(new)
                fmt = KINDS[cls][0]
                seg = b[s_:e_]
                seg = seg[:k] + struct.pack('<II', cls, len(ticks)) + struct.pack(f'<{len(ticks) - 1}H', *ticks[1:]) + b''.join((struct.pack(fmt, *new[t]) for t in ticks)) + seg[end:]
                out += b[last:s_] + seg
                last = e_
                placed += 1
            body = bytes(out + b[last:])
            report.append(f"{rec.name}: {tod['name']}: {placed} curves placed, {kept} constant ones left as they are")
            objs = [(o['serial'], mapwalk.object_bytes(o, body if o is tod else None), o['filter']) for o in p.objects]
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, rec.index, payload)
            changed[rec.index] = (tod, body)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = bool(changed)
    checks = total = 0
    with Forge(dest) as fo, Forge(src) as fi:
        others = all((fo.read(x) == fi.read(y) for x, y in zip(fo.records, fi.records) if x.index not in changed))
        for idx, (old, _new) in changed.items():
            q = mapwalk.Payload(fo.records[idx], fo.read(fo.records[idx]))
            nb = find_tod(q)['body']
            ne, oe = (tod_snow.entries(nb), tod_snow.entries(old['body']))
            ok &= [x[2] for x in ne] == [x[2] for x in oe]
            for (ns, nend, _p), (os_, oend, _q) in zip(ne, oe):
                cn, co = (curve(nb[ns:nend]), curve(old['body'][os_:oend]))
                if not co:
                    continue
                for t, src_t in where.items():
                    total += 1
                    got, want = (evaluate(cn[1], cn[2], cn[3], t), evaluate(co[1], co[2], co[3], src_t))
                    checks += all((abs(x - y) < 0.0001 for x, y in zip(got, want)))
    ok &= checks == total and others
    report.append(f"verify: TOD entries unchanged; each placed hour reads its source look {checks} of {total}; every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
