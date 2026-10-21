"""forgelib/crossmaps/acb_refs.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import pickle
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'maps'))
import mapwalk
import paths
import uid_index
import xrefs
import exe_registry
from summary import BULK
CLS = exe_registry.load(exe_registry.ACR)[0]
from forge import Forge
ACB = os.path.join(os.path.dirname(os.path.dirname(paths.AC3)), "Assassin's Creed Brotherhood", 'multi')
SHARED = ('DataPC.forge', 'DataPC_extra.forge', 'DataPC_CoreFX.forge', 'DataPC_Fire.forge', 'DataPC_extraparams.forge')
GAMES = {'ACB': ACB, 'ACR': paths.ACR, 'AC3': paths.AC3_SOURCE}

def shared_index(game):
    out = {}
    for a in SHARED:
        path = os.path.join(GAMES[game], a)
        if not os.path.exists(path):
            continue
        cache = os.path.join(uid_index.CACHE, f'{game}_{a}.pkl')
        if os.path.exists(cache):
            idx = pickle.load(open(cache, 'rb'))
            idx = idx[0] if isinstance(idx, tuple) else idx
        else:
            print(f'  indexing {game}:{a} ...', file=sys.stderr, flush=True)
            idx = uid_index.scan(path)
            idx = idx[0] if isinstance(idx, tuple) else idx
            os.makedirs(uid_index.CACHE, exist_ok=True)
            pickle.dump(idx, open(cache, 'wb'))
        for u, v in idx.items():
            out.setdefault(u, (a,) + tuple(v))
    return out

def deps(path):
    defined, need = (set(), {})
    with Forge(path) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            p = mapwalk.Payload(rec, f.read(rec))
            defined |= {o['uid'] for o in p.objects if o['has_uid']}
            for o in p.objects:
                if CLS.get(o['type']) in BULK:
                    continue
                own = o['uid'] if o['has_uid'] else None
                for u in xrefs.u32s(o['body']):
                    if u >= xrefs.MIN_REF and u != own:
                        need.setdefault(u, []).append((rec.name, CLS.get(o['type'], hex(o['type'])), o['name']))
    return (defined, need)

def main(argv):
    maps = argv or ['Forli', 'Villa', 'Pienza_dlc', 'Alhambra_dlc', 'Palazzio']
    idx = {g: shared_index(g) for g in GAMES}
    for m in maps:
        defined, need = deps(os.path.join(ACB, f'DataPC_AC2MP_{m}.forge'))
        ext = {u: w for u, w in need.items() if u not in defined and any((u in idx[g] for g in GAMES))}
        where = Counter()
        acb_only = []
        for u in ext:
            key = tuple((g for g in GAMES if u in idx[g]))
            where[key] += 1
            if key == ('ACB',) or not key:
                acb_only.append(u)
        print(f'{m}: {len(need)} dependencies, {len(ext)} outside the archive')
        for key, n in where.most_common():
            print(f"   {' + '.join(key) or 'nowhere':15s} {n}")
        for u in sorted(acb_only, key=lambda u: idx['ACB'][u][3])[:60]:
            arc, _f, t, name = idx['ACB'][u]
            srcs = Counter((c for _r, c, _n in ext[u]))
            print(f'      ACB only: {arc:18s} {CLS.get(t, hex(t)):22s} {name[:44]:44s} <- {dict(srcs)}')
        missing_ac3 = [u for u in ext if u in idx['ACR'] and u not in idx['AC3']]
        print(f"   in ACR's shared but not AC3's: {len(missing_ac3)}")
if __name__ == '__main__':
    main(sys.argv[1:])
