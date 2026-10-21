"""forgelib/crossmaps/acb_spawns.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import math
import os
import pickle
import struct
import sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'maps')):
    sys.path.insert(0, p)
import mapwalk
import spawn_layers
import xrefs
import acb_refs
from forge import Forge
DATALAYER = 'DataLayer'

def layer_names():
    names = {}
    for g in acb_refs.GAMES:
        for u, (_a, _f, t, n) in acb_refs.shared_index(g).items():
            if xrefs.cls('ACR', t) == DATALAYER:
                names.setdefault(u, n)
    return names

def open_spec(spec):
    game, arc = spec.split(':', 1)
    return os.path.join(acb_refs.GAMES[game], arc)

def spawns(path, names):
    out = []
    local = {}
    with Forge(path) as f:
        pls = []
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            p = mapwalk.Payload(rec, f.read(rec))
            pls.append((rec, p))
            for o in p.objects:
                if xrefs.cls('ACR', o['type']) == DATALAYER:
                    local[o['uid']] = o['name']
    names = dict(names, **local)
    for rec, p in pls:
        sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
        for o in p.objects:
            if xrefs.cls('ACR', o['type']) != 'Entity':
                continue
            k = spawn_layers.spawn_kind(o['body'])
            if not k:
                continue
            m = struct.unpack_from('<16f', o['body'], 10)
            layers = sorted((names.get(u, f'{u:#x}') for u in sets[o['filter']])) if o['filter'] is not None else []
            out.append({'file': rec.name, 'name': o['name'], 'uid': o['uid'], 'kind': k, 'layers': layers, 'pos': (m[12], m[13], m[14])})
    return out

def summary(label, sp):
    print(f'== {label}: {len(sp)} spawn points')
    by = Counter(((s['file'][:34], s['kind'], ','.join(s['layers'])[:90]) for s in sp))
    for (f, k, l), n in sorted(by.items()):
        print(f'   {n:3d}  {f:34s} type {k[0]} idx {k[1]}  {l}')
    xs = [s['pos'] for s in sp]
    if xs:
        print('   extent x %.0f..%.0f  y %.0f..%.0f  z %.0f..%.0f' % (min((p[0] for p in xs)), max((p[0] for p in xs)), min((p[1] for p in xs)), max((p[1] for p in xs)), min((p[2] for p in xs)), max((p[2] for p in xs))))

def main(argv):
    names = layer_names()
    a = spawns(open_spec(argv[0]), names)
    summary(argv[0], a)
    if '--compare' in argv:
        spec = argv[argv.index('--compare') + 1]
        b = spawns(open_spec(spec), names)
        summary(spec, b)
        dist = defaultdict(list)
        for s in a:
            d = min((math.dist(s['pos'], t['pos']) for t in b)) if b else float('inf')
            dist[s['file'][:34], s['kind']].append(d)
        print(f'== nearest {spec} spawn to each {argv[0]} spawn (m): exact = same place')
        for k, ds in sorted(dist.items()):
            print(f'   {k[0]:34s} type {k[1][0]} idx {k[1][1]}: {len(ds):3d} spawns, {sum((d < 0.01 for d in ds))} exact, median {sorted(ds)[len(ds) // 2]:.1f}')
if __name__ == '__main__':
    main(sys.argv[1:])
