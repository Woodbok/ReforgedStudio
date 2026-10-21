"""forgelib/modes/dom_map.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import aa_map
import aa_zones as AZ
import mapwalk
import spawn_layers
import xrefs
import zones as Z
from forge import Forge
BLOCK = 'DataBlock_ACFE_Domination'
DOM_LAYER = 1145357182
LETTERS = ('A', 'B', 'C')
LAYOUTS = os.path.join(HERE, 'layouts')
CACHE = aa_map.CACHE
CONTROL_DISTANCE = 8.0
CLEAR_FAIL, CLEAR_WARN, CLEAR_DZ = (0.25, 0.5, 1.5)

def _spot(o):
    m = struct.unpack_from('<16f', o['body'], 10)
    return {'from': o['name'], 'at': [round(m[12], 3), round(m[13], 3), round(m[14], 3)], 'facing': aa_map.facing_of(m)}

def zone_polys(z):
    out = []
    for secs in z['convex']:
        pts = []
        for s in secs:
            f = Z.section_fields(s['raw'])
            c, d, w = (f['v0'], f['v3'], f['pair'][0])
            pts += [(c[0] - d[0] * w / 2, c[1] - d[1] * w / 2), (c[0] + d[0] * w / 2, c[1] + d[1] * w / 2)]
        h = aa_map.hull([(round(x, 3), round(y, 3)) for x, y in pts])
        out.append([[round(x, 2), round(y, 2)] for x, y in h])
    return out

def read_domination(src):
    st = os.stat(src)
    key = os.path.join(CACHE, f'{os.path.basename(src)}.{st.st_size}.{int(st.st_mtime)}.{abs(hash(os.path.normcase(src))) % 10 ** 8}.dom1.pkl')
    if os.path.exists(key):
        with open(key, 'rb') as fh:
            return pickle.load(fh)
    out = dict(points=[], zones=[], starts={1: [], 2: []}, respawns=[], oob=0, world_lists=False, block=None, block_uid=None, block_layer=None)
    seen = set()
    with Forge(src) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            is_block = r.name == BLOCK
            if r.name.startswith('DataBlock_') and (not is_block):
                continue
            try:
                p = mapwalk.Payload(r, f.read(r))
            except Exception:
                continue
            sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
            if is_block:
                hb = p.objects[0]['body']
                n = struct.unpack_from('<I', hb, 10)[0]
                out['block'], out['block_uid'] = (r.name, p.objects[0]['uid'])
                if len(hb) == 14 + 6 * n + 8:
                    out['block_layer'] = struct.unpack_from('<I', hb, 14 + 6 * n + 4)[0]
            for o in p.objects:
                c = xrefs.cls('AC3', o['type'])
                if c == 'WorldDataLayerManager':
                    b = o['body']
                    out['world_lists'] |= any((struct.unpack_from('<I', b, k)[0] == DOM_LAYER for k in range(len(b) - 3)))
                if c != 'Entity' or not o['has_uid']:
                    continue
                b = o['body']
                layers = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else set()
                k = spawn_layers.spawn_kind(b)
                if k and k[0] == 0 and (DOM_LAYER in layers) and (o['uid'] not in seen):
                    seen.add(o['uid'])
                    out['respawns'].append(dict(_spot(o), record=r.name))
                if not is_block:
                    continue
                cp = AZ.comp(b, 'ControlPointComponent')
                if cp:
                    out['points'].append({'id': struct.unpack_from('<I', b, cp[0] + 15)[0], 'name': o['name'], 'at': [round(v, 3) for v in AZ.pos(o)]})
                elif AZ.comp(b, 'GameplayZoneComponent'):
                    z = Z.parse(b[slice(*AZ.comp(b, 'GameplayZoneComponent'))])
                    out['zones'].append({'name': o['name'], 'polys': zone_polys(z)})
                elif AZ.comp(b, 'OutOfBoundsComponent'):
                    out['oob'] += 1
                elif k and k[0] == 2 and (k[1] in (1, 2)):
                    out['starts'][k[1]].append(_spot(o))
    out['points'].sort(key=lambda q: q['id'])
    for t in (1, 2):
        out['starts'][t].sort(key=lambda s: s['from'])
    out['respawns'].sort(key=lambda s: s['from'])
    have = bool(out['points'] or out['zones'] or any(out['starts'].values()))
    out['state'] = 'EMPTY' if not have else 'COMPLETE' if len(out['points']) == 3 and len(out['zones']) == 3 and all(out['starts'].values()) and out['respawns'] and out['world_lists'] else 'PARTIAL'
    os.makedirs(CACHE, exist_ok=True)
    with open(key, 'wb') as fh:
        pickle.dump(out, fh)
    return out

def read(archive, source=None):
    m = dict(aa_map.read(archive, source))
    m['dom'] = read_domination(m['source'])
    zs = [z for z in m['stock_zones'].values() if z]
    f = Z.section_fields(zs[0]['convex'][0][0]['raw']) if zs else None
    m['zc'], m['height'] = (round(f['v0'][2], 2), round(f['pair'][1], 2)) if f else (0.0, 60.0)
    return m

def start_groups(m):
    groups = {}
    for s in m['spots']:
        if s['kind'][0] == 2 and s['kind'][1] > 0:
            groups.setdefault(s['name'].rstrip('0123456789').rstrip('_'), []).append(s)
    out = []
    for key, g in sorted(groups.items()):
        g = sorted(g, key=lambda s: s['name'])
        for i in range(0, len(g) - 3, 4):
            out.append({'label': key if len(g) == 4 else f'{key} {i // 4 + 1}', 'starts': [{'from': s['name'], 'at': list(s['at']), 'facing': s['facing']} for s in g[i:i + 4]]})
    return out

def load_layout(name):
    path = name if name.endswith('.json') and os.path.exists(name) else os.path.join(LAYOUTS, f'{name}.json')
    if not os.path.exists(path):
        raise SystemExit(f'no layout {name} ({path})')
    with open(path, encoding='utf-8') as fh:
        cfg = json.load(fh)
    if cfg.get('use') != 'domination':
        raise SystemExit(f"{name} is `use: {cfg.get('use')}`, not a Domination layout")
    return cfg

def play_area(cfg, m):
    return AZ.ccw([tuple(q) for q in cfg.get('area') or m['area']])

def split3(area, dividers):
    import aa_variant as V
    if len(dividers) != 2 or any((len(d) < 2 for d in dividers)):
        raise SystemExit('two dividers of at least 2 points each are needed')
    d1, d2 = [[tuple(q[:2]) for q in d] for d in dividers]
    try:
        a1, b1, in1 = V.split_polyline(area, d1)
    except SystemExit as e:
        raise SystemExit(f'divider 1: {e}')
    tries = []
    for half, other in ((a1, b1), (b1, a1)):
        try:
            x, y, in2 = V.split_polyline(half, d2)
        except SystemExit:
            continue
        if min(abs(AZ.area(x)), abs(AZ.area(y))) < 1.0:
            continue
        tries.append((sum((AZ.inside(half, q) for q in d2)), [other, x, y], in2))
    if not tries:
        raise SystemExit("divider 2 must cross the play area's outline exactly twice, on one side of divider 1 (the two dividers must not cross)")
    _n, regions, in2 = max(tries, key=lambda t: t[0])
    if abs(sum((AZ.area(r) for r in regions)) - AZ.area(area)) > 1.0:
        raise SystemExit("the three zones don't add up to the play area")
    return ([AZ.ccw(r) for r in regions], [in1, in2])

def assign(regions, points):
    out, used = ({}, set())
    for L in LETTERS:
        hits = [i for i, r in enumerate(regions) if AZ.inside(r, points[L][:2])]
        if len(hits) != 1:
            raise SystemExit(f'point {L} lies in {len(hits)} zones' + (' (outside the play area)' if not hits else ''))
        if hits[0] in used:
            raise SystemExit(f'point {L} shares a zone with another point: each zone holds exactly one')
        used.add(hits[0])
        out[L] = regions[hits[0]]
    return out

class Height:

    def __init__(self, meta):
        from PIL import Image
        self.t = meta
        self.img = Image.open(meta['height_png']).convert('RGB')
        self.px = self.img.load()

    def z(self, x, y):
        k = self.t['px_per_m']
        X, Y = (int((x - self.t['x0']) * k), int((self.t['y1'] - y) * k))
        if X < 0 or Y < 0 or X >= self.img.width or (Y >= self.img.height):
            return None
        r, g, b = self.px[X, Y]
        v = r << 16 | g << 8 | b
        return v / 100 - 1000 if v else None

    def clear(self, at, radius=CONTROL_DISTANCE, step=0.5, dz=CLEAR_DZ):
        n = good = 0
        r = int(radius / step)
        for i in range(-r, r + 1):
            for j in range(-r, r + 1):
                if (i * step) ** 2 + (j * step) ** 2 > radius ** 2:
                    continue
                n += 1
                z = self.z(at[0] + i * step, at[1] + j * step)
                good += z is not None and abs(z - at[2]) <= dz
        return good / max(n, 1)

def height_of(archive, source=None):
    import topview
    _png, meta = topview.render(archive, source)
    with open(meta, encoding='utf-8') as fh:
        return Height(json.load(fh))

def checks(cfg, m, height=None):
    P, W = ([], [])
    out = dict(problems=P, warnings=W, regions={}, lines=[], stats={})
    pts = cfg.get('points') or {}
    if sorted(pts) != list(LETTERS):
        P.append(f'points {sorted(pts)}: need exactly A, B, C (ids 1, 2, 3)')
        return out
    area = play_area(cfg, m)
    try:
        regions, lines = split3(area, cfg.get('dividers') or [])
        reg = assign(regions, pts)
    except SystemExit as e:
        P.append(str(e))
        return out
    out['regions'], out['lines'] = (reg, lines)
    total = AZ.area(area)
    out['stats']['share'] = {L: round(AZ.area(reg[L]) / total, 3) for L in LETTERS}
    for t, L in ((1, 'A'), (2, 'C')):
        st = cfg['starts'].get(str(t)) or cfg['starts'].get(t) or []
        if len(st) != 4:
            P.append(f'team {t}: {len(st)} starts, need 4')
        inn = sum((AZ.inside(reg[L], s['at'][:2]) for s in st))
        out['stats'][f'starts{t}'] = inn
        if inn != len(st):
            P.append(f"team {t}: {len(st) - inn} of its starts outside zone {L} (team {t} starts in {L}'s zone)")
    resp = cfg.get('respawns') or []
    per = {L: sum((AZ.inside(reg[L], r['at'][:2]) for r in resp)) for L in LETTERS}
    out['stats']['respawns'] = per
    for L in LETTERS:
        if per[L] == 0:
            P.append(f"zone {L} has no respawn (respawn rule 20 scores the spawns in each point's zone)")
        elif per[L] < 3:
            W.append(f'zone {L} has only {per[L]} respawn(s)')
    outside = sum((not any((AZ.inside(reg[L], r['at'][:2]) for L in LETTERS)) for r in resp))
    if outside:
        W.append(f'{outside} respawn(s) outside every zone (they still spawn; rule 20 ignores their zone)')
    for i, L in enumerate(LETTERS):
        for L2 in LETTERS[i + 1:]:
            d = math.dist(pts[L][:2], pts[L2][:2])
            if d < 2 * CONTROL_DISTANCE + 4:
                P.append(f'points {L} and {L2} are {d:.1f} m apart: their capture circles ({CONTROL_DISTANCE:g} m) overlap')
            elif d < 30:
                W.append(f'points {L} and {L2} are only {d:.0f} m apart')
    for L in LETTERS:
        d = min((AZ.seg_dist(a, b, pts[L][:2]) for ln in lines for a, b in zip(ln, ln[1:])))
        if d < CONTROL_DISTANCE:
            W.append(f'point {L} is {d:.1f} m from a divider: its {CONTROL_DISTANCE:g} m capture circle reaches the next zone')
    clear = {}
    if height is not None:
        for L in LETTERS:
            c = height.clear(pts[L])
            clear[L] = round(c, 2)
            if c < CLEAR_FAIL:
                P.append(f'point {L}: only {c * 100:.0f} % of the ground within {CONTROL_DISTANCE:g} m is open floor at its height (walls, roofs or drops): move it to open ground')
            elif c < CLEAR_WARN:
                W.append(f'point {L}: {c * 100:.0f} % of the ground within {CONTROL_DISTANCE:g} m is open floor at its height')
    out['stats']['clear'] = clear
    share = out['stats']['share']
    if max(share.values()) - min(share.values()) > 0.2:
        W.append('the zones differ by more than 20 % of the play area')
    return out

def draft(m, title=''):
    area = AZ.ccw([tuple(q) for q in m['area']])
    xs, ys = ([q[0] for q in area], [q[1] for q in area])
    samples = [(x + 0.5, y + 0.5) for x in range(int(min(xs)) - 1, int(max(xs)) + 1) for y in range(int(min(ys)) - 1, int(max(ys)) + 1) if AZ.inside(area, (x + 0.5, y + 0.5))]
    mx = sum((p[0] for p in samples)) / len(samples)
    my = sum((p[1] for p in samples)) / len(samples)
    sxx = sum(((p[0] - mx) ** 2 for p in samples))
    syy = sum(((p[1] - my) ** 2 for p in samples))
    sxy = sum(((p[0] - mx) * (p[1] - my) for p in samples))
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    u = (math.cos(ang), math.sin(ang))
    v = (-u[1], u[0])
    s = sorted(((p[0] - mx) * u[0] + (p[1] - my) * u[1] for p in samples))
    half = max((math.dist((mx, my), q) for q in area)) + 15
    dividers = []
    for frac in (1 / 3, 2 / 3):
        q = s[int(frac * (len(s) - 1))]
        c = (mx + u[0] * q, my + u[1] * q)
        dividers.append([[round(c[0] - v[0] * half, 1), round(c[1] - v[1] * half, 1)], [round(c[0] + v[0] * half, 1), round(c[1] + v[1] * half, 1)]])
    regions, lines = split3(area, dividers)
    proj = lambda p: (p[0] - mx) * u[0] + (p[1] - my) * u[1]
    order = sorted(regions, key=lambda r: proj((sum((q[0] for q in r)) / len(r), sum((q[1] for q in r)) / len(r))))
    f1, f2 = (m['flags'][1]['at'], m['flags'][2]['at'])
    if proj(f1) > proj(f2):
        order = order[::-1]
    cands = [dict(name=s_['name'], at=list(s_['at']), facing=s_['facing']) for s_ in m['spots']]
    cands += [dict(name=f'AA team {t} artifact', at=list(m['flags'][t]['at']), facing=0.0) for t in (1, 2)]

    def edge_dist(r, p):
        return min((AZ.seg_dist(a, b, p) for a, b in zip(r, r[1:] + r[:1])))
    points = {}
    for L, r in zip(LETTERS, order):
        cen = sample_centroid(r, samples)
        mine = [c for c in cands if AZ.inside(r, c['at'][:2])]
        if not mine:
            raise SystemExit(f'no spawn spot in zone {L}: lay the points by hand')
        deep = [c for c in mine if edge_dist(r, c['at']) >= 10] or sorted(mine, key=lambda c: -edge_dist(r, c['at']))[:1]
        best = min(deep, key=lambda c: math.dist(c['at'][:2], cen))
        points[L] = [round(v_, 3) for v_ in best['at']]
    starts = {}
    groups = start_groups(m)
    for t, L in ((1, 'A'), (2, 'C')):
        r = order[LETTERS.index(L)]
        inside_groups = [g for g in groups if all((AZ.inside(r, s_['at'][:2]) for s_ in g['starts']))]
        if inside_groups:
            g = min(inside_groups, key=lambda g: math.dist(points[L][:2], g['starts'][0]['at'][:2]))
            starts[str(t)] = [dict(e) for e in g['starts']]
            continue
        pool = sorted((c for c in cands if AZ.inside(r, c['at'][:2]) and (not c['name'].startswith('AA team'))), key=lambda c: math.dist(c['at'][:2], points[L][:2]))
        pick = []
        for c in pool:
            if all((math.dist(c['at'][:2], p['at'][:2]) >= 1.5 for p in pick)):
                pick.append({'from': c['name'], 'at': c['at'], 'facing': c['facing']})
            if len(pick) == 4:
                break
        if len(pick) < 4:
            raise SystemExit(f"fewer than 4 spawn spots in zone {L} for team {t}'s starts: place them by hand")
        starts[str(t)] = pick
    lay = {'title': title or f"{m['archive'][7:-6]} - Domination (draft)", 'archive': m['archive'], 'use': 'domination', 'points': points, 'dividers': dividers, 'starts': starts, 'respawns': [{'from': s_['name'], 'at': s_['at'], 'facing': s_['facing']} for s_ in m['respawns']], 'zc': m['zc'], 'height': m['height']}
    if m.get('source_arg'):
        lay['source'] = m['source_arg'].replace(os.sep, '/')
    return lay

def sample_centroid(r, samples):
    inn = [p for p in samples if AZ.inside(r, p)]
    if not inn:
        return (sum((q[0] for q in r)) / len(r), sum((q[1] for q in r)) / len(r))
    return (sum((p[0] for p in inn)) / len(inn), sum((p[1] for p in inn)) / len(inn))

def own_layout(m, title=''):
    d = m['dom']
    pts = {LETTERS[p['id'] - 1]: p['at'] for p in d['points'] if 1 <= p['id'] <= 3}
    return {'title': title or f"{m['archive'][7:-6]} - its own Domination (read-only)", 'archive': m['archive'], 'use': 'domination', 'readonly': True, 'points': pts, 'dividers': [], 'zones': [{'name': z['name'], 'polys': z['polys']} for z in d['zones']], 'starts': {str(t): [dict(s, at=list(s['at'])) for s in d['starts'][t]] for t in (1, 2)}, 'respawns': [{'from': s['from'], 'at': s['at'], 'facing': s['facing']} for s in d['respawns']], 'zc': m['zc'], 'height': m['height']}

def new_layout(name, mp):
    import re
    if not re.fullmatch('[A-Za-z0-9_\\-]+', name or ''):
        raise SystemExit('a layout name is letters, digits, _ and - only')
    path = os.path.join(LAYOUTS, f'{name}.json')
    if os.path.exists(path):
        raise SystemExit(f'{os.path.relpath(path, ROOT)} exists - open it, or pick another name')
    arch, source = (os.path.basename(mp), mp) if '/' in mp or os.sep in mp else (mp, None)
    m = read(arch, source)
    if m['dom']['state'] != 'EMPTY':
        raise SystemExit(f"{arch} has a Domination of its own ({m['dom']['state']}): AC3's and AC4's maps keep theirs - view it read-only instead")
    lay = draft(m)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(lay, fh, indent=2)
        fh.write('\n')
    return path

def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('archive', help="an archive name (DataPC_AC2MP_Forli.forge) or a build's archive path")
    ap.add_argument('--new', metavar='LAYOUT', help='write a draft modes/layouts/<LAYOUT>.json')
    a = ap.parse_args(argv)
    name, source = (os.path.basename(a.archive), a.archive) if os.sep in a.archive or '/' in a.archive else (a.archive, None)
    if a.new:
        print(f'wrote {os.path.relpath(new_layout(a.new, a.archive), ROOT)} (a draft: adjust it in modes/layout_editor.py)')
        return
    m = read(name, source)
    d = m['dom']
    print(f"{a.archive} ({m['source']}): Domination {d['state']}; World lists the layer {d['world_lists']}; points {[(p['id'], p['at']) for p in d['points']]}; {len(d['zones'])} zones; starts {len(d['starts'][1])} + {len(d['starts'][2])}; {len(d['respawns'])} respawns on the layer; block layer {d['block_layer'] and hex(d['block_layer'])}")
if __name__ == '__main__':
    main(sys.argv[1:])
