"""forgelib/modes/assoc_order.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'maps', 'port')):
    if p not in sys.path:
        sys.path.insert(0, p)
import mapwalk
import oob_modes
import xrefs
from forge import Forge

def associations(path):
    with Forge(path) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size or r.name.startswith('DataBlock_'):
                continue
            try:
                p = mapwalk.Payload(r, f.read(r))
            except Exception:
                continue
            for o in p.objects:
                if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager':
                    b = o['body']
                    n = struct.unpack_from('<I', b, 10)[0]
                    if len(b) != 14 + 18 * n:
                        raise SystemExit(f'{path}: WorldDataLayerManager: not count + 18-byte entries')
                    return (r.name, [struct.unpack_from('<I', b, 14 + 18 * i + 9) + struct.unpack_from('<I', b, 14 + 18 * i + 14) for i in range(n)])
    return (None, None)

def check(path):
    rec, assoc = associations(path)
    name = os.path.basename(path)
    if assoc is None:
        print(f'NONE     {name}: no WorldDataLayerManager')
        return True
    layers = [a for a, _b in assoc]
    bad = next((i for i in range(1, len(layers)) if layers[i] < layers[i - 1]), None)
    if bad is None:
        print(f'OK       {name}: {len(assoc)} associations in layer uid order ({rec})')
        return True
    print(f'UNSORTED {name}: {len(assoc)} associations, #{bad} 0x{layers[bad]:08X} after #{bad - 1} 0x{layers[bad - 1]:08X} ({sum((1 for i in range(1, len(layers)) if layers[i] < layers[i - 1]))} descents; {rec})')
    return False

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--release', nargs='?', const=os.path.join(ROOT, 'release_set.json'))
    ap.add_argument('--root', default=ROOT, help="where a release's relative out/ paths live (a worktree: the main repo)")
    a = ap.parse_args(argv)
    todo = []
    if a.release:
        todo += [os.path.join(a.root, os.path.relpath(p, ROOT)) for p in oob_modes.release_maps(a.release)]
    for p in a.paths:
        todo += sorted((os.path.join(p, n) for n in os.listdir(p) if n.endswith('.forge') and (not n.startswith('DataPC.')) and (not n.startswith('DataPC_extra')))) if os.path.isdir(p) else [p]
    ok = all([check(p) for p in todo])
    print(f"{len(todo)} archives: {('OK' if ok else 'UNSORTED associations')}")
    return 0 if ok else 1
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
