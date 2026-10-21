"""forgelib/maps/port/tod_evening.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    sys.path.insert(0, p)
import mapwalk
import tod_snow
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
KINDS = {3486935725: ('<f', 1, True), 2347352579: ('<4f', 4, True), 1480703275: ('<B', 1, False), 793583881: ('<I', 4, False)}
HOUR = 120
DAY = 24 * HOUR
DIRECTION = {(617819384, 2133844690), (617819384, 697817303), (617819384, 1953315074), (617819384, 3115195862), (617819384, 518929955), (617819384, 2874537749)}
BASE_HOUR = 12

def curve(seg):
    for cls, (fmt, _n, _lin) in KINDS.items():
        k = seg.find(struct.pack('<I', cls))
        if k < 0:
            continue
        n = struct.unpack_from('<I', seg, k + 4)[0]
        keys = [0] + list(struct.unpack_from(f'<{n - 1}H', seg, k + 8))
        size = struct.calcsize(fmt)
        v0 = k + 8 + 2 * (n - 1)
        vals = [struct.unpack_from(fmt, seg, v0 + i * size) for i in range(n)]
        return (k, cls, keys, vals, v0 + n * size)
    return None

def evaluate(cls, keys, vals, t):
    lin = KINDS[cls][2]
    if t <= keys[0]:
        return vals[0]
    for i in range(len(keys) - 1):
        if keys[i] <= t <= keys[i + 1]:
            if t == keys[i + 1]:
                return vals[i + 1]
            if not lin or keys[i + 1] == keys[i]:
                return vals[i]
            w = (t - keys[i]) / (keys[i + 1] - keys[i])
            return tuple((a + (b - a) * w for a, b in zip(vals[i], vals[i + 1])))
    return vals[-1]
ALSO = set()
MIX = [1.0]

def target(cls, path, keys, vals, skeys, svals, tick):
    own = evaluate(cls, keys, vals, BASE_HOUR * HOUR)
    if not from_donor(cls, path):
        return own
    d = evaluate(cls, skeys, svals, tick)
    if cls == 2347352579 and MIX[0] != 1.0:
        return tuple((MIX[0] * x + (1 - MIX[0]) * y for x, y in zip(d, own)))
    return d

def from_donor(cls, path):
    return cls == 2347352579 or tuple(path[:2]) in DIRECTION or any(('/'.join((f'{x:08X}' for x in path)).startswith(q) for q in ALSO))

def merge(gseg, sseg, at, path=()):
    g, s = (curve(gseg), curve(sseg))
    if not g or not s or g[1] != s[1]:
        return (None, 'kinds differ')
    k, cls, keys, vals, end = g
    _k, _c, skeys, svals, _e = s
    pts = dict(zip(keys, vals))
    for pin in (12 * HOUR, 24 * HOUR):
        pts.setdefault(pin, evaluate(cls, keys, vals, pin))
    tick = int(round(at * HOUR))
    pts[tick] = target(cls, path, keys, vals, skeys, svals, tick)
    for a_, b_ in ((0, DAY), (DAY, 0)):
        if tick == a_:
            pts[b_] = pts[tick]
    new_keys = sorted(pts)
    fmt = KINDS[cls][0]
    body = struct.pack('<II', cls, len(new_keys)) + struct.pack(f'<{len(new_keys) - 1}H', *new_keys[1:])
    body += b''.join((struct.pack(fmt, *pts[t]) for t in new_keys))
    return (gseg[:k] + body + gseg[end:], None)

def midnight_agrees(c):
    _k, cls, keys, vals, _e = c
    a_, b_ = (evaluate(cls, keys, vals, 0), evaluate(cls, keys, vals, DAY))
    return all((abs(x - y) < 0.0001 for x, y in zip(a_, b_)))

def find_tod(p):
    return next((o for o in p.objects if o['type'] == tod_snow.TOD), None)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--from', dest='source', default='DataPC_ACFE_Souk.forge', help='the ACR archive with the evening')
    ap.add_argument('--hour', type=float, default=20.0)
    ap.add_argument('--also', action='append', default=[], metavar='PATH', help="one more property path (hex, '/'-joined prefix) taken from the donor: Jerusalem's Night takes Galata's midnight sky flag, disc size and glow (the moon) with the colours and direction")
    ap.add_argument('--mix', type=float, default=1.0, help="the donor's share of each colour at the hour, the rest the map's own at noon (Jerusalem's Night: Galata's midnight alone read a bit too dark blue, JE4)")
    a = ap.parse_args(argv)
    ALSO.update((q.upper() for q in a.also))
    MIX[0] = a.mix
    src = os.path.join(OUT, a.src, a.archive)
    donor = next((find_tod(p) for p in mapwalk.payloads('ACR', a.source, skip_errors=True) if find_tod(p)))
    dents = {path: donor['body'][s_:e_] for s_, e_, path in tod_snow.entries(donor['body'])}
    image = ArchiveImage.read(src)
    report = [f"evening from {a.source}: {donor['name']}, {len(dents)} curves, taken at {a.hour:g}:00"]
    changed, expect = ({}, {})
    with Forge(src) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            p = mapwalk.Payload(rec, f.read(rec))
            tod = find_tod(p)
            if tod is None:
                continue
            b = tod['body']
            ents = tod_snow.entries(b)
            out, done, skipped = (bytearray(b[:ents[0][0]]), 0, [])
            for s_, e_, path in ents:
                seg = b[s_:e_]
                if path in dents:
                    new, why = merge(seg, dents[path], a.hour, path)
                    if new is None:
                        skipped.append(('/'.join((f'{x:08X}' for x in path)), why))
                        new = seg
                    else:
                        done += 1
                    out += new
                else:
                    skipped.append(('/'.join((f'{x:08X}' for x in path)), 'not in the donor'))
                    out += seg
            body = bytes(out)
            report.append(f"{rec.name}: {tod['name']}: {done} of {len(ents)} curves given the {a.hour:g}:00 key; left alone {skipped}")
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
    at = int(round(a.hour * HOUR))
    pinned = [t for t in (0, 12 * HOUR, DAY) if t != at and (not (at in (0, DAY) and t in (0, DAY)))]
    with Forge(dest) as f, Forge(src) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index not in changed))
        for idx, (old_tod, _new) in changed.items():
            q = mapwalk.Payload(f.records[idx], f.read(f.records[idx]))
            nb = find_tod(q)['body']
            olds = {path: old_tod['body'][s_:e_] for s_, e_, path in tod_snow.entries(old_tod['body'])}
            news = tod_snow.entries(nb)
            ok &= [p_ for _s, _e, p_ in news] == list(olds)
            same = evening = split = 0
            for s_, e_, path in news:
                c_new, c_old = (curve(nb[s_:e_]), curve(olds[path]))
                if not c_new:
                    continue
                split += not midnight_agrees(c_new) and midnight_agrees(c_old)
                for t in pinned:
                    a_, b_ = (evaluate(c_new[1], c_new[2], c_new[3], t), evaluate(c_old[1], c_old[2], c_old[3], t))
                    same += all((abs(x - y) < 0.0001 for x, y in zip(a_, b_)))
                if path in dents:
                    c_d = curve(dents[path])
                    want = target(c_old[1], path, c_old[2], c_old[3], c_d[2], c_d[3], int(round(a.hour * HOUR)))
                    got = evaluate(c_new[1], c_new[2], c_new[3], a.hour * HOUR)
                    evening += all((abs(x - y) < 0.0001 for x, y in zip(got, want)))
            total = sum((1 for _s, _e, p_ in news if curve(nb[_s:_e]))) * len(pinned)
            ok &= same == total and split == 0
            report.append(f"verify {q.rec.name}: values at {' / '.join((f'{t // HOUR}:00' for t in pinned))} unchanged {same} of {total}; {a.hour:g}:00 as planned (the donor's colours / sun direction, the map's own noon otherwise) {evening}; 0:00 / 24:00 split apart {split}; from the donor {sum((1 for _s, _e, p_ in news if p_ in dents and curve(nb[_s:_e]) and from_donor(curve(nb[_s:_e])[1], p_)))}")
    ok &= others
    report.append(f"verify: every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
