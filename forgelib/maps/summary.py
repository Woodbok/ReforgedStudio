"""forgelib/maps/summary.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import inventory
import mapwalk
import xrefs
BULK = {'TextureMap', 'Mesh', 'Animation', 'MaterialTemplate', 'Skeleton', 'RealTreeMesh', 'MeshShape', 'NavMeshManager', 'FakeEntities', 'Cloth'}

def verdicts(game, archive):
    edges = xrefs.scan(game, archive)
    uni = inventory.universe(game)
    _other, ouni, by_name = xrefs.other_game_lookup(game)
    sources = {}
    locs = Counter()
    for _f, _p, t, _n, u, loc in edges:
        locs[loc] += 1
        if loc == 'DataPC':
            sources.setdefault(u, set()).add(xrefs.cls(game, t))
    out = Counter()
    problems = []
    for u, srcs in sources.items():
        _arc, _f, t, name = next((h for h in uni[u] if h[0] in inventory.SHARED))
        if u in ouni:
            v = 'same uid' if any((h[3] == name for h in ouni[u])) else 'same uid, renamed/other'
        elif by_name.get((t, name)):
            v = 'remap by name'
        else:
            v = 'absent'
        real = bool(srcs - BULK)
        out[v, real] += 1
        if real and v != 'same uid':
            problems.append((v, xrefs.cls(game, t), name, u, sorted(srcs - BULK)))
    return (locs, out, problems)

def main():
    print(f"{'map':32}{'file':>8}{'map':>8}{'shared':>8}{'other':>7}  {'targets':>7} {'same uid':>9} {'real problems':>14} {'bulk-only noise':>16}")
    allp = []
    for game in ('ACR', 'AC3'):
        for archive in mapwalk.map_archives(game):
            if not os.path.exists(os.path.join(xrefs.CACHE, f'{game}_{archive}.pkl')):
                continue
            locs, out, problems = verdicts(game, archive)
            same = out['same uid', True] + out['same uid', False]
            real = sum((n for (v, r), n in out.items() if r and v != 'same uid'))
            noise = sum((n for (v, r), n in out.items() if not r and v != 'same uid'))
            print(f"{game} {archive[7:-6]:28}{locs['file']:>8,}{locs['map']:>8,}{locs['DataPC']:>8,}{locs['other-map']:>7,}  {sum(out.values()):>7,} {same:>9,} {real:>14,} {noise:>16,}")
            allp += [(game, archive[7:-6]) + p for p in problems]
    print('\nstructural references into the shared archives that do not resolve to the same uid:')
    for g, a, v, c, name, u, srcs in allp:
        print(f"  {g} {a:24} {v:24} {c:22} 0x{u:08X} {name:44} <- {', '.join(srcs)}")
if __name__ == '__main__':
    main()
