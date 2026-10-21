"""forgelib/modes/aa_map.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import json
import math
import os
import pickle
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'maps', 'port'), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import aa_records as AR
import aa_zones as AZ
import convert
import mapwalk
import spawn_layers
import xrefs
import zones as Z
from forge import Forge
CTF = 3296798500
CACHE = os.path.join(ROOT, 'out', 'modes', 'cache')
IDS = convert.class_ids()

def archive_source(archive, source=None):
    if source:
        p = source if os.path.isabs(source) else os.path.join(ROOT, source)
        if not os.path.exists(p):
            raise SystemExit(f'{source}: no such archive')
        return p
    with open(os.path.join(ROOT, 'release_set.json'), encoding='utf-8') as fh:
        for f in json.load(fh)['files']:
            if os.path.basename(f['dest']).lower() == archive.lower():
                return f['src'] if os.path.isabs(f['src']) else os.path.join(ROOT, f['src'])
    p = mapwalk.archive_path('AC3', archive)
    if not os.path.exists(p):
        raise SystemExit(f'{archive}: not in release_set.json nor in the install')
    return p

def facing_of(m):
    return round(math.degrees(math.atan2(m[5], m[4])), 2)

def placement_bytes(old, at, facing):
    m = list(struct.unpack('<16f', old))
    f = math.radians(facing)
    m[0:3] = [math.sin(f), -math.cos(f), 0.0]
    m[4:7] = [math.cos(f), math.sin(f), 0.0]
    m[8:11] = [0.0, 0.0, 1.0]
    m[12:15] = list(at)
    return struct.pack('<16f', *m)

def _spot(name, o):
    m = struct.unpack_from('<16f', o['body'], 10)
    return {'name': name, 'at': [round(m[12], 3), round(m[13], 3), round(m[14], 3)], 'facing': facing_of(m)}

def read(archive, source=None):
    src = archive_source(archive, source)
    st = os.stat(src)
    key = os.path.join(CACHE, f'{archive}.{st.st_size}.{int(st.st_mtime)}.{abs(hash(os.path.normcase(src))) % 10 ** 8}.aamap2.pkl')
    if os.path.exists(key):
        with open(key, 'rb') as fh:
            return pickle.load(fh)
    flags, scores, zones, oob, starts, resp, spots, recs = ({}, [], [], None, {1: [], 2: []}, [], {}, set())
    seen = set()
    switched = None
    with Forge(src) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            try:
                p = mapwalk.Payload(r, f.read(r))
            except Exception:
                continue
            if r.name.startswith('DataBlock_') and p.objects:
                if switched is None:
                    import assoc_order
                    switched = AR.switched_uids(assoc_order.associations(src)[1] or [])
                if p.objects[0]['uid'] in switched:
                    continue
            sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
            for o in p.objects:
                c = xrefs.cls('AC3', o['type'])
                if c == 'WorldDataLayerManager' and switched is None:
                    switched = AR.switched_uids(AR.associations(o['body']))
                if c != 'Entity' or not o['has_uid']:
                    continue
                b = o['body']
                k = spawn_layers.spawn_kind(b)
                layers = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else set()
                if k and o['name'] not in spots:
                    spots[o['name']] = dict(_spot(o['name'], o), kind=list(k), aa=CTF in layers)
                if CTF not in layers or o['uid'] in seen:
                    continue
                seen.add(o['uid'])
                recs.add(r.name)
                if AZ.comp(b, 'FlagComponent'):
                    kk = b.find(struct.pack('<I', IDS['FlagComponent']))
                    flags[b[kk + 21] + 1] = {'name': o['name'], 'at': list(AZ.pos(o))}
                elif AZ.comp(b, 'CTFScoreZoneComponent'):
                    scores.append(list(AZ.pos(o)))
                elif AZ.comp(b, 'GameplayZoneComponent'):
                    zones.append((o['name'], Z.parse(b[slice(*AZ.comp(b, 'GameplayZoneComponent'))])))
                elif AZ.comp(b, 'OutOfBoundsComponent') and oob is None:
                    try:
                        oob = AZ.ccw(AZ.oob_outline(b))
                    except SystemExit:
                        pts = [q for q in AZ.section_points(b, 'OutOfBoundsSection')]
                        oob = hull([(q[0], q[1]) for q in pts])
                elif k and k[0] == 2 and (k[1] in (1, 2)):
                    starts[k[1]].append(_spot(o['name'], o))
                elif k and k[0] == 0:
                    resp.append(_spot(o['name'], o))
    if sorted(flags) != [1, 2] or len(zones) != 2:
        import glob
        builds = sorted(glob.glob(os.path.join(ROOT, 'out', 'maps', '*', archive)), key=os.path.getmtime, reverse=True)
        hint = '; builds of this map, newest first: ' + ', '.join((os.path.relpath(b, ROOT).replace(os.sep, '/') for b in builds[:3])) if builds else ''
        raise SystemExit(f'{(os.path.relpath(src, ROOT) if src.startswith(ROOT) else archive)}: no complete Artifact Assault ({len(flags)} flags, {len(zones)} territories) - this copy has none{hint}')
    import aa_variant as V
    area = V.territory_outline([z for _n, z in zones])
    if oob is None or abs(AZ.area(oob)) < 0.3 * abs(AZ.area(area)):
        oob = AZ.ccw(area)
    by_team = {}
    for name, z in zones:
        t = next((t for t in (1, 2) if AZ.member(z['convex'], flags[t]['at'])), None)
        by_team[t] = z
    out = {'archive': archive, 'source': src, 'source_arg': source, 'records': sorted(recs), 'flags': flags, 'scores': scores, 'oob': [list(q) for q in oob], 'area': [list(q) for q in area], 'divider': stock_divider(by_team), 'starts': {t: sorted(starts[t], key=lambda s: s['name']) for t in (1, 2)}, 'respawns': sorted(resp, key=lambda s: s['name']), 'spots': sorted(spots.values(), key=lambda s: s['name']), 'stock_zones': by_team}
    os.makedirs(CACHE, exist_ok=True)
    with open(key, 'wb') as fh:
        pickle.dump(out, fh)
    return out

def hull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and AZ.side(h[-2], h[-1], p) <= 0:
                h.pop()
            h.append(p)
        return h
    lo, up = (half(pts), half(pts[::-1]))
    return lo[:-1] + up[:-1]

def stock_divider(by_team):

    def segs(z):
        out = []
        for c in z['convex']:
            for s in c:
                f = Z.section_fields(s['raw'])
                c0, d, w = (f['v0'], f['v3'], f['pair'][0])
                out.append(((c0[0] - d[0] * w / 2, c0[1] - d[1] * w / 2), (c0[0] + d[0] * w / 2, c0[1] + d[1] * w / 2)))
        return out
    if None in by_team:
        return []
    a, b = (segs(by_team[1]), segs(by_team[2]))
    shared = []
    for p, q in a:
        mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        if any((AZ.seg_dist(r, s, mid) < 0.3 for r, s in b)):
            shared.append((p, q))
    if not shared:
        return []
    line = [shared[0][0], shared[0][1]]
    left = shared[1:]
    while left:
        best = None
        for i, (p, q) in enumerate(left):
            for end, front in ((line[-1], False), (line[0], True)):
                for x, y in ((p, q), (q, p)):
                    d = math.dist(end, x)
                    if best is None or d < best[0]:
                        best = (d, i, front, y)
        d, i, front, y = best
        if d > 0.6:
            break
        left.pop(i)
        if front:
            line.insert(0, y)
        else:
            line.append(y)
    pts = [line[0]] + [p for i, p in enumerate(line[1:-1], 1) if abs(AZ.side(line[i - 1], p, line[i + 1])) / max(math.dist(line[i - 1], line[i + 1]), 1e-06) > 0.05] + [line[-1]]
    pts = [p for i, p in enumerate(pts) if i == 0 or math.dist(p, pts[i - 1]) > 0.05]
    return [[round(x, 2), round(y, 2)] for x, y in pts]

def stock_layout(m, title=''):
    out = {'title': title or f"{m['archive'][7:-6]} - Artifact Assault (from the stock layout)", 'archive': m['archive'], 'flags': {str(t): [round(v, 3) for v in m['flags'][t]['at']] for t in (1, 2)}, 'starts': {str(t): [{'from': s['name'], 'at': s['at'], 'facing': s['facing']} for s in m['starts'][t]] for t in (1, 2)}, 'respawns': [{'from': s['name'], 'at': s['at'], 'facing': s['facing']} for s in m['respawns']], 'divider': m['divider']}
    if m.get('source_arg'):
        out['source'] = m['source_arg'].replace(os.sep, '/')
    return out

def normalise(cfg, m):
    spots = {s['name']: s for s in m['spots']}
    starts = {}
    for t in (1, 2):
        spec = cfg['starts'][t] if t in cfg['starts'] else cfg['starts'][str(t)]
        if isinstance(spec, str):
            spec = [spec.format(n) for n in range(1, 5)]
        out = []
        for e in spec:
            if isinstance(e, str):
                s = spots[e]
                out.append({'from': e, 'at': list(s['at']), 'facing': s['facing']})
            else:
                out.append(dict(e))
        starts[t] = out
    resp = [dict(e) for e in cfg['respawns']] if 'respawns' in cfg else [{'from': s['name'], 'at': s['at'], 'facing': s['facing']} for s in m['respawns']]
    return (starts, resp)

def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('archive', help="an archive name (DataPC_AC3MP_Boston_Mill.forge) or a build's archive path")
    ap.add_argument('--new', metavar='LAYOUT', help='write modes/layouts/<LAYOUT>.json from the stock layout')
    a = ap.parse_args(argv)
    name, source = (os.path.basename(a.archive), a.archive) if os.sep in a.archive or '/' in a.archive else (a.archive, None)
    m = read(name, source)
    print(f"{a.archive} ({m['source']}): flags {[m['flags'][t]['at'] for t in (1, 2)]}, starts 4 + 4, {len(m['respawns'])} respawns, divider {len(m['divider'])} points, pieces in {', '.join(m['records'])}")
    if a.new:
        path = os.path.join(HERE, 'layouts', f'{a.new}.json')
        if os.path.exists(path):
            raise SystemExit(f'{path} exists')
        with open(path, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(stock_layout(m), fh, indent=2)
            fh.write('\n')
        print(f'wrote {os.path.relpath(path, ROOT)}')
if __name__ == '__main__':
    main(sys.argv[1:])
