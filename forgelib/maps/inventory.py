"""forgelib/maps/inventory.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import hashlib
import os
import pickle
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import uid_index
import mapwalk
from forge import Forge
CACHE = os.path.join(os.path.dirname(HERE), 'out', 'maps', 'inv')
SHARED = ('DataPC.forge', 'DataPC_extra.forge', 'DataPC_CoreFX.forge', 'DataPC_Fire.forge', 'DataPC_extraparams.forge')

def build(game, archive):
    rows, prefix = ([], {})
    with Forge(mapwalk.archive_path(game, archive)) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or rec.size == 0:
                continue
            p = mapwalk.Payload(rec, f.read(rec))
            if p.prefix:
                prefix[rec.name] = p.prefix
            for pos, o in enumerate(p.objects):
                raw = p.data[o['offset']:o['offset'] + o['size']]
                rows.append((rec.name, pos, o['type'], o['name'], o['uid'] if o['has_uid'] else None, o['filter'], o['size'], hashlib.sha1(raw).digest(), o['serial']))
    return {'rows': rows, 'prefix': prefix}

def load(game, archive):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f'{game}_{archive}.pkl')
    if not os.path.exists(path):
        print(f'  indexing {game}:{archive} ...', file=sys.stderr)
        inv = build(game, archive)
        pickle.dump(inv, open(path, 'wb'))
        return inv
    return pickle.load(open(path, 'rb'))
_UNIVERSE = {}

def universe(game):
    if game in _UNIVERSE:
        return _UNIVERSE[game]
    out = {}
    for archive in SHARED:
        if not os.path.exists(mapwalk.archive_path(game, archive)):
            continue
        index, _ = uid_index.load(game, archive)
        for u, (fname, t, name) in index.items():
            out.setdefault(u, []).append((archive, fname, t, name))
    for archive in mapwalk.map_archives(game):
        seen = set()
        for fname, _pos, t, name, u, *_ in load(game, archive)['rows']:
            if u is not None and u not in seen:
                seen.add(u)
                out.setdefault(u, []).append((archive, fname, t, name))
    _UNIVERSE[game] = out
    return out

def main(argv):
    targets = [a.split(':', 1) for a in argv] or [(g, a) for g in ('ACR', 'AC3') for a in mapwalk.map_archives(g)]
    for game, archive in targets:
        inv = load(game, archive)
        rows = inv['rows']
        print(f"{game} {archive:40} {len(rows):6} objects, {sum((r[4] is not None for r in rows)):6} with uids, {len(inv['prefix']):4} prefixed files")
if __name__ == '__main__':
    main(sys.argv[1:])
