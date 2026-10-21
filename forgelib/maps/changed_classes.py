"""forgelib/maps/changed_classes.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import pickle
import struct
import sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import class_sizes
import exe_registry
import mapwalk
import xrefs
from summary import BULK
CACHE = os.path.join(os.path.dirname(HERE), 'out', 'maps', 'changed_classes.pkl')

def changed():
    a = class_sizes.sized_registry(exe_registry.ACR)
    b = class_sizes.sized_registry(exe_registry.AC3)
    return {t: (n, s, b[t][1] if t in b else None) for t, (n, s) in a.items() if t >= 1 << 24 and (t not in b or b[t][1] != s)}

def census():
    if os.path.exists(CACHE):
        return pickle.load(open(CACHE, 'rb'))
    ch = changed()
    needles = {struct.pack('<I', t): t for t in ch}
    hits = defaultdict(Counter)
    maps = defaultdict(set)
    examples = {}
    for archive in mapwalk.map_archives('ACR'):
        for p in mapwalk.payloads('ACR', archive, skip_errors=True):
            for o in p.objects:
                host = xrefs.cls('ACR', o['type'])
                if host in BULK:
                    continue
                body = o['body']
                for nb, t in needles.items():
                    if nb in body:
                        hits[t][host] += 1
                        maps[t].add(archive)
                        examples.setdefault(t, (archive, p.rec.name, o['name']))
    out = (ch, dict(hits), dict(maps), examples)
    pickle.dump(out, open(CACHE, 'wb'))
    return out

def main():
    ch, hits, maps, examples = census()
    rows = sorted(hits, key=lambda t: -sum(hits[t].values()))
    print(f'{len(ch)} classes changed size or vanished; {len(rows)} occur in ACR map objects\n')
    print(f"{'class':34}{'ACR':>6}{'AC3':>6}{'objects':>9}{'maps':>6}  inside")
    for t in rows:
        n, sa, sb = ch[t]
        inside = ', '.join((f'{k} {v}' for k, v in hits[t].most_common(3)))
        print(f'{n:34}{sa:>6}{str(sb):>6}{sum(hits[t].values()):>9}{len(maps[t]):>6}  {inside}')
        print(f"{'':34}   e.g. {examples[t][0][7:-6]} / {examples[t][2]}")
if __name__ == '__main__':
    main()
