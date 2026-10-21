"""forgelib/maps/tod_snow.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import mapwalk
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
TOD = 844440094
ENTRY = bytes.fromhex('20360a05')
REF = bytes.fromhex('ba50871a')
SNOW = (617819384, 2097821208, 3232351376)
NEXT_CLASS = 3135613939

def entries(body):
    starts = []
    at = body.find(ENTRY, 18)
    while at >= 0:
        starts.append(at)
        at = body.find(ENTRY, at + 4)
    assert starts and starts[0] == 18, 'TOD body does not start its entries at +0x12'
    count = struct.unpack_from('<I', body, 10)[0]
    assert count == len(starts), f'count {count} != {len(starts)} entries'
    out = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(body)
        path, at = ([], body.find(REF, s))
        while 0 <= at < e:
            path.append(struct.unpack_from('<I', body, at + 4)[0])
            at = body.find(REF, at + 8)
        out.append((s, e, tuple(path)))
    return out

def snow_entry(archive):
    with Forge(mapwalk.archive_path('AC3', archive)) as f:
        for r in f.records:
            if not r.size or r.name == 'GlobalMetaFile':
                continue
            try:
                p = mapwalk.Payload(r, f.read(r))
            except Exception:
                continue
            for o in p.objects:
                if o['type'] == TOD:
                    for s, e, path in entries(o['body']):
                        if path == SNOW:
                            return (o['name'], o['body'][s:e])
    raise SystemExit(f'no TOD entry for {SNOW} in {archive}')

def curve(seg):
    p = seg.find(bytes.fromhex('03c2e98b'))
    n = struct.unpack_from('<I', seg, p + 4)[0]
    keys = (0,) + struct.unpack_from(f'<{n - 1}H', seg, p + 8)
    vals = struct.unpack_from(f'<{4 * n}f', seg, p + 8 + 2 * (n - 1))
    return [(k / 120, tuple((round(v, 3) for v in vals[4 * i:4 * i + 4]))) for i, k in enumerate(keys)]

def with_snow(body, seg):
    es = entries(body)
    if any((path == SNOW for _s, _e, path in es)):
        return None
    at = next((s for s, _e, path in es if path and path[0] == NEXT_CLASS))
    new = bytearray(body[:at] + seg + body[at:])
    struct.pack_into('<I', new, 10, len(es) + 1)
    return bytes(new)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N67_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='W1')
    ap.add_argument('--ac3', default='DataPC_AC3MP_Plantation.forge', help='the AC3 map to take the entry from')
    a = ap.parse_args(argv)
    their_name, seg = snow_entry(a.ac3)
    report = [f"entry {'/'.join((f'{x:#x}' for x in SNOW))} from {a.ac3} {their_name}: {len(seg)} bytes, curve {curve(seg)}"]
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    changed, expect = ({}, {})
    with Forge(src) as f:
        raw_before = {r.index: f.read(r) for r in f.records}
        for rec in f.records:
            try:
                p = mapwalk.Payload(rec, raw_before[rec.index])
            except Exception:
                continue
            objs, hit = ([], False)
            for o in p.objects:
                body = o['body']
                if o['type'] == TOD:
                    new = with_snow(body, seg)
                    n = len(entries(body))
                    report.append(f"{rec.name}: {o['name']} {o['uid']:#x}: {n} entries -> " + ('unchanged (has it)' if new is None else f'{n + 1}, +{len(seg)} bytes'))
                    if new is not None:
                        body, hit = (new, True)
                        expect[rec.index, o['uid']] = new
                objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
            if hit:
                if p.prefix:
                    raise SystemExit(f'{rec.name} has a uid-pair prefix; not handled')
                d = mapwalk.rebuild(objs, p.filter_table)
                payload = compress(d, first_chunk=mapwalk.first_chunk(d))
                assert decompress(payload) == d
                set_payload(image, rec.index, payload)
                changed[rec.index] = p
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    ok = bool(changed)
    with Forge(dest) as f:
        ok &= len(f.records) == len(raw_before)
        others = all((f.read(r) == raw_before[r.index] for r in f.records if r.index not in changed))
        for idx, p in changed.items():
            q = mapwalk.Payload(f.records[idx], f.read(f.records[idx]))
            ok &= len(q.objects) == len(p.objects)
            for x, y in zip(p.objects, q.objects):
                want = expect.get((idx, x['uid']))
                if want is not None:
                    es = entries(y['body'])
                    ok &= y['body'] == want and sum((path == SNOW for _s, _e, path in es)) == 1
                    ok &= [path for _s, _e, path in es if path != SNOW] == [path for _s, _e, path in entries(x['body'])]
                else:
                    ok &= y['body'] == x['body']
    ok &= others
    report.append(f"verify: {len(expect)} TOD(s) gained the entry, their other entries in order; other objects of their files unchanged; every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
