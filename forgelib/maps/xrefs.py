"""forgelib/maps/xrefs.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import pickle
import struct
import sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import exe_registry
import inventory
import mapwalk
from forge import Forge
MIN_REF = 65536
CACHE = os.path.join(os.path.dirname(HERE), 'out', 'maps', 'xrefs')
LOCATIONS = ('file', 'map', 'DataPC', 'other-map')
_REG = {}

def cls(game, t):
    if game not in _REG:
        _REG[game] = exe_registry.load(exe_registry.ACR if game == 'ACR' else exe_registry.AC3)[0]
    return _REG[game].get(t, f'0x{t:08X}')

def u32s(body):
    out = set()
    for a in range(4):
        n = (len(body) - a) // 4
        if n > 0:
            out.update(struct.unpack_from(f'<{n}I', body, a))
    return out

def place(uid, archive, local, homes):
    if uid in local:
        return 'file'
    arcs = {h[0] for h in homes}
    if archive in arcs:
        return 'map'
    if arcs & set(inventory.SHARED):
        return 'DataPC'
    return 'other-map'

def scan(game, archive):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f'{game}_{archive}.pkl')
    if os.path.exists(path):
        return pickle.load(open(path, 'rb'))
    uni = inventory.universe(game)
    keys = {u for u in uni if u >= MIN_REF}
    edges = []
    with Forge(mapwalk.archive_path(game, archive)) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or rec.size == 0:
                continue
            p = mapwalk.Payload(rec, f.read(rec))
            local = {o['uid'] for o in p.objects if o['has_uid']}
            for pos, o in enumerate(p.objects):
                own = o['uid'] if o['has_uid'] else None
                for u in u32s(o['body']) & keys:
                    if u != own:
                        edges.append((rec.name, pos, o['type'], o['name'], u, place(u, archive, local, uni[u])))
    pickle.dump(edges, open(path, 'wb'))
    return edges

def pool_sizes(game, archive):
    uni = inventory.universe(game)
    n = Counter()
    for u, homes in uni.items():
        if u < MIN_REF:
            continue
        arcs = {h[0] for h in homes}
        if archive in arcs:
            n['map'] += 1
        elif arcs & set(inventory.SHARED):
            n['DataPC'] += 1
        else:
            n['other-map'] += 1
    return n

def other_game_lookup(game):
    other = 'AC3' if game == 'ACR' else 'ACR'
    uni = inventory.universe(other)
    by_name = defaultdict(list)
    for u, homes in uni.items():
        for arc, _f, t, name in homes:
            by_name[t, name].append((u, arc))
    return (other, uni, by_name)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('target', help='GAME:Archive.forge')
    ap.add_argument('--list', choices=LOCATIONS, help='list the distinct targets in one location')
    a = ap.parse_args(argv)
    game, archive = a.target.split(':', 1)
    edges = scan(game, archive)
    uni = inventory.universe(game)
    pools = pool_sizes(game, archive)
    by_src = defaultdict(Counter)
    for _f, _p, t, _n, _u, loc in edges:
        by_src[cls(game, t)][loc] += 1
    print(f'== {game}:{archive}: {len(edges):,} reference edges; candidate pools ' + ', '.join((f'{k} {v:,}' for k, v in pools.items())))
    print(f"{'source class':34}" + ''.join((f'{l:>11}' for l in LOCATIONS)) + '   DataPC noise est.')
    tot = Counter()
    noise_total = 0.0
    for c, cnt in sorted(by_src.items(), key=lambda kv: -sum(kv[1].values())):
        rate = cnt['other-map'] / pools['other-map'] if pools['other-map'] else 0
        est = rate * pools['DataPC']
        noise_total += est
        tot.update(cnt)
        print(f'{c:34}' + ''.join((f'{cnt[l]:>11,}' for l in LOCATIONS)) + f'   {est:>10.1f}')
    print(f"{'TOTAL':34}" + ''.join((f'{tot[l]:>11,}' for l in LOCATIONS)) + f'   {noise_total:>10.1f}')
    ext = defaultdict(set)
    for f_, _p, t, n, u, loc in edges:
        if loc == 'DataPC':
            ext[u].add(cls(game, t))
    other, ouni, by_name = other_game_lookup(game)
    verdict = Counter()
    rows = []
    for u, src_classes in ext.items():
        arc, fname, t, name = next((h for h in uni[u] if h[0] in inventory.SHARED))
        if u in ouni:
            same = any((h[3] == name for h in ouni[u]))
            v = 'same uid, same name' if same else 'same uid, DIFFERENT object'
        elif by_name.get((t, name)):
            v = 'same name+class, new uid'
        else:
            v = f'absent from {other}'
        verdict[v] += 1
        rows.append((v, cls(game, t), name, u, arc, sorted(src_classes)))
    print(f'\n{len(ext):,} distinct {game} shared-archive targets, checked against {other}:')
    for v, n in verdict.most_common():
        print(f'   {n:6,}  {v}')
    by_cls = defaultdict(Counter)
    for v, c, *_ in rows:
        by_cls[c][v] += 1
    print(f"\n{'target class':30}" + ''.join((f'{v[:24]:>26}' for v, _ in verdict.most_common())))
    for c, cnt in sorted(by_cls.items(), key=lambda kv: -sum(kv[1].values())):
        print(f'{c:30}' + ''.join((f'{cnt[v]:>26,}' for v, _ in verdict.most_common())))
    if a.list:
        print(f'\n-- distinct targets in {a.list}:')
        seen = {}
        for f_, _p, t, n, u, loc in edges:
            if loc == a.list:
                seen.setdefault(u, []).append(f'{cls(game, t)} {n}')
        for u, srcs in sorted(seen.items(), key=lambda kv: uni[kv[0]][0][3]):
            arc, fname, t, name = uni[u][0]
            print(f'0x{u:08X}  {cls(game, t):24} {arc}:{name}   <- {len(srcs)} x, e.g. {srcs[0]}')
if __name__ == '__main__':
    main(sys.argv[1:])
