"""forgelib/maps/port/orator_plane.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import crowdlife
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from merchant_stalls import STATE, blocks, transposed
OUT = os.path.join(ROOT, 'out', 'maps')
MARKER = 'AC2MP_Merchant_Orator_plane'
LAYOUT_SPEAKER_X = -2.5254
REACH = 6.0
SPLIT = 1.0

def position(body):
    return struct.unpack_from('<3f', body, 58)

def x_axis(body):
    return struct.unpack_from('<3f', body, 10)

def plane_for(orator_body, planes, reach=REACH):
    po, xo = (position(orator_body), x_axis(orator_body))
    best = None
    for pl in planes:
        pp = position(pl['body'])
        dx, dy = (pp[0] - po[0], pp[1] - po[1])
        d = math.hypot(dx, dy)
        if d <= reach and dx * xo[0] + dy * xo[1] > 0 and (best is None or d < best[1]):
            best = (pl, d)
    return best or (None, None)

def layout_split(orator_body, plane_body):
    po, xo, pp = (position(orator_body), x_axis(orator_body), position(plane_body))
    cx, cy = (po[0] - LAYOUT_SPEAKER_X * xo[0], po[1] - LAYOUT_SPEAKER_X * xo[1])
    return math.hypot(pp[0] - cx, pp[1] - cy)

def relaid(body, orator_pos, plane_pos):
    dx, dy = (plane_pos[0] - orator_pos[0], plane_pos[1] - orator_pos[1])
    L = math.hypot(dx, dy)
    fx, fy = (dx / L, dy / L)
    placement = struct.pack('<16f', fx, fy, 0.0, 0.0, -fy, fx, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, plane_pos[0], plane_pos[1], plane_pos[2], 1.0)
    v = (orator_pos[0] - plane_pos[0], orator_pos[1] - plane_pos[1], orator_pos[2] - plane_pos[2])
    speaker = (v[0] * fx + v[1] * fy, -v[0] * fy + v[1] * fx, v[2])
    nb = bytearray(body)
    nb[10:74] = placement
    k = crowdlife._all(bytes(nb), STATE)
    if len(k) != 1:
        raise SystemExit(f'{len(k)} GameStateData matrices in the stall')
    nb[k[0] + 8:k[0] + 72] = transposed(placement)
    s1, g1, s2, g2 = blocks(bytes(nb))
    for off in (s1, g1):
        w, fw = (struct.unpack_from('<f', nb, off + 12)[0], struct.unpack_from('<f', nb, off + 28)[0])
        struct.pack_into('<8f', nb, off, speaker[0], speaker[1], speaker[2], w, 1.0, 0.0, 0.0, fw)
    for a, b in zip(s2, g2):
        lx, ly = struct.unpack_from('<2f', body, a)
        tx, ty = (speaker[0] - lx, speaker[1] - ly)
        n = math.hypot(tx, ty)
        for off in (a, b):
            w, fw = (struct.unpack_from('<f', nb, off + 12)[0], struct.unpack_from('<f', nb, off + 28)[0])
            struct.pack_into('<8f', nb, off, lx, ly, 0.0, w, tx / n, ty / n, 0.0, fw)
    return (bytes(nb), {'speaker local': speaker, 'yaw': math.degrees(math.atan2(fy, fx)), 'plane distance': L})

def to_world(body, local):
    m = struct.unpack_from('<16f', body, 10)
    return tuple((m[12 + j] + sum((local[i] * m[4 * i + j] for i in range(3))) for j in range(3)))

def dir_world(body, local):
    m = struct.unpack_from('<16f', body, 10)
    return tuple((sum((local[i] * m[4 * i + j] for i in range(3))) for j in range(3)))

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--map', required=True, help="ACR's archive (the orators and their planes)")
    ap.add_argument('--orators', action='append', required=True, help='an orator stall to move (its entity name; survey.py lists them)')
    ap.add_argument('--marker', default=MARKER, help="name prefix of ACR's marker planes")
    ap.add_argument('--reach', type=float, default=REACH)
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    acr, planes = ({}, [])
    for p in mapwalk.payloads('ACR', a.map):
        for o in p.objects:
            if o['has_uid'] and len(o['body']) >= 74 and (xrefs.cls('ACR', o['type']) == 'Entity'):
                if o['name'] in a.orators:
                    acr[o['name']] = o
                elif (o['name'] or '').startswith(a.marker):
                    planes.append(o)
    missing = [n for n in a.orators if n not in acr]
    if missing:
        raise SystemExit(f"not in ACR's {a.map}: {missing}")
    report, edits, made = ([], {}, {})
    for p in payloads:
        for i, o in enumerate(p.objects):
            if o['name'] not in a.orators or xrefs.cls('AC3', o['type']) != 'Entity':
                continue
            ao = acr[o['name']]
            po = position(ao['body'])
            s1 = blocks(o['body'])[0]
            sp = to_world(o['body'], struct.unpack_from('<3f', o['body'], s1))
            if max((abs(x - y) for x, y in zip(sp, po))) > 0.001:
                raise SystemExit(f"{o['name']}: its speaker slot is not on ACR's orator spot ({sp} vs {po}) - not an orator stall as the orator steps build it")
            pl, d = plane_for(ao['body'], planes, a.reach)
            if pl is None:
                raise SystemExit(f"{o['name']}: no {a.marker}* plane in front of him within {a.reach} m")
            pp = position(pl['body'])
            nb, info = relaid(o['body'], po, pp)
            edits[p.rec.index, i] = nb
            made[o['name']] = (o['body'], nb, po, pp)
            sl = info['speaker local']
            report.append(f"{p.rec.name}: {o['name']}: crowd to {pl['name']} ({pp[0]:.2f}, {pp[1]:.2f}, {pp[2]:.2f}), {d:.2f} m in front of the herald ({po[0]:.2f}, {po[1]:.2f}, {po[2]:.2f}), was {layout_split(ao['body'], pl['body']):.2f} m from the layout's crowd; the crowd facing {(info['yaw'] + 180) % 360:.0f} deg; speaker slot local ({sl[0]:.2f}, {sl[1]:.2f}, {sl[2]:+.2f})")
    if sorted(made) != sorted(set(a.orators)):
        raise SystemExit(f'stalls found: {sorted(made)}, named: {sorted(set(a.orators))}')
    for p in payloads:
        if not any((idx == p.rec.index for idx, _i in edits)):
            continue
        objs = [(o['serial'], mapwalk.object_bytes(o, edits.get((p.rec.index, i))), o['filter']) for i, o in enumerate(p.objects)]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    touched = {idx for idx, _i in edits}
    with Forge(dest) as f, Forge(src) as g:
        old = {r.index: r for r in g.records}
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            if r.index not in touched:
                v['untouched files byte-identical'] += f.read(r) == g.read(old[r.index])
                continue
            p_new, p_old = (mapwalk.Payload(r, f.read(r)), mapwalk.Payload(old[r.index], g.read(old[r.index])))
            v['touched files walk'] += 1
            for x, y in zip(p_new.objects, p_old.objects):
                if x['name'] not in made:
                    v['other objects byte-identical'] += x['body'] == y['body'] and x['name'] == y['name']
                    continue
                b0, b, po, pp = made[x['name']]
                if b != x['body'] or y['body'] != b0:
                    continue
                k = b.find(STATE)
                s1, g1, s2, g2 = blocks(b)
                allowed = [(10, 74), (k + 8, k + 72)] + [(s, s + 32) for s in [s1, g1] + s2 + g2]
                diff = [j for j in range(len(b)) if b[j] != b0[j]]
                v['stalls: only placement, state matrix, slots'] += len(b) == len(b0) and all((any((lo <= j < hi for lo, hi in allowed)) for j in diff))
                v['stalls: state matrix = placement'] += b[k + 8:k + 72] == transposed(b[10:74])
                spk = to_world(b, struct.unpack_from('<3f', b, s1))
                v["stalls: speaker on ACR's orator spot"] += max((abs(m - n) for m, n in zip(spk, po))) < 0.001 and b[s1:s1 + 32] == b[g1:g1 + 32]
                ok = True
                for s, gg in zip(s2, g2):
                    lp = struct.unpack_from('<3f', b, s)
                    wp, wf = (to_world(b, lp), dir_world(b, struct.unpack_from('<3f', b, s + 16)))
                    tx, ty = (spk[0] - wp[0], spk[1] - wp[1])
                    ok &= b[s:s + 32] == b[gg:gg + 32] and abs(wp[2] - pp[2]) < 0.001 and ((wf[0] * tx + wf[1] * ty) / math.hypot(tx, ty) > 0.9999)
                v['stalls: listeners on the plane facing the speaker'] += ok
    untouched = sum((1 for p in payloads if p.rec.index not in touched))
    others = sum((len(p.objects) for p in payloads if p.rec.index in touched)) - len(made)
    ok = v['untouched files byte-identical'] == untouched and v['touched files walk'] == len(touched) and (v['other objects byte-identical'] == others) and (v['stalls: only placement, state matrix, slots'] == v['stalls: state matrix = placement'] == v["stalls: speaker on ACR's orator spot"] == v['stalls: listeners on the plane facing the speaker'] == len(made))
    report.append(f"verify: {dict(v)} of {untouched} untouched files, {others} other objects: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
