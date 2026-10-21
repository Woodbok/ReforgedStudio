"""forgelib/crossmaps/acb_formats.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acb
acb.patch()
import mapwalk
import world
import xrefs
from forge import Forge
B = acb.ACB
R = __import__('paths').ACR
PAIRS = {'Firenze': ('DataPC_AC2MP_Firenze.forge', 'DataPC_AC2MP_Firenze_dlc.forge'), 'Palazzio': ('DataPC_AC2MP_Palazzio.forge', 'DataPC_AC2MP_Palazzio.forge'), 'Siena': ('DataPC_AC2MP_Siena.forge', 'DataPC_AC2MP_Siena_dlc.forge'), 'SanDonato': ('DataPC_AC2MP_SanDonato.forge', 'DataPC_AC2MP_SanDonato_dlc.forge'), 'SanMarco': ('DataPC_AC2MP_SanMarco.forge', 'DataPC_AC2MP_SanMarco.forge'), 'MtStMichel': ('DataPC_AC2MP_MtStMichel_dlc.forge', 'DataPC_AC2MP_MtStMichel.forge'), 'Rome': ('DataPC_ACR_Rome_Multi.forge', 'DataPC_ACR_Rome_Multi.forge')}
SKIP = {'MaterialTemplate', 'Mesh', 'TextureMap', 'Animation', 'GridCellDataBlock', 'BuildTable'}

def objs(path):
    out = {}
    with Forge(path) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            try:
                p = mapwalk.Payload(rec, f.read(rec))
            except Exception:
                continue
            for o in p.objects:
                if o['has_uid']:
                    out.setdefault(o['uid'], (xrefs.cls('ACR', o['type']), o['name'], o['body']))
    return out

def shape(body):
    return [(n, len(s)) for n, s in world.segments(body)]

def main(argv):
    maps = argv or ['Firenze', 'Palazzio']
    changes = defaultdict(Counter)
    example = {}
    for m in maps:
        a_name, r_name = PAIRS[m]
        a, r = (objs(os.path.join(B, a_name)), objs(os.path.join(R, r_name)))
        for u in set(a) & set(r):
            ca, na, ba = a[u]
            _cr, _nr, br = r[u]
            if ca in SKIP or len(ba) == len(br):
                continue
            sa, sr = (shape(ba), shape(br))
            i = next((k for k, (x, y) in enumerate(zip(sa, sr)) if x != y), min(len(sa), len(sr)))
            key = (tuple(sa[i:i + 2]), tuple(sr[i:i + 3]), len(br) - len(ba))
            changes[ca][key] += 1
            example.setdefault((ca, key), (m, na))
        print(f'{m}: {len(set(a) & set(r))} shared uids', flush=True)
    for ca, keys in sorted(changes.items(), key=lambda kv: -sum(kv[1].values())):
        print(f'== {ca}: {sum(keys.values())} objects changed size')
        for key, n in keys.most_common(6):
            print(f'   {n:4d}x  ACB {key[0]} -> ACR {key[1]}  ({key[2]:+d} B)  e.g. {example[ca, key]}')
if __name__ == '__main__':
    main(sys.argv[1:])
