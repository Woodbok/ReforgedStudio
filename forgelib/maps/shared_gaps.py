"""forgelib/maps/shared_gaps.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import sys, struct, collections
import os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import convert, inventory, mapwalk
from forge import Forge
from summary import BULK
names = {v: k for k, v in convert.class_ids().items()}
SHARED = set(inventory.SHARED)
acr, ac3 = (inventory.universe('ACR'), inventory.universe('AC3'))
build = sys.argv[1]
local, bodies = (set(), [])
with Forge(os.path.join(os.path.dirname(HERE), 'out', 'maps', build, 'DataPC_ACFE_Souk.forge')) as f:
    for r in f.records:
        if not r.size or r.name == 'GlobalMetaFile':
            continue
        try:
            p = mapwalk.Payload(r, f.read(r))
        except Exception:
            continue
        for o in p.objects:
            if o.get('uid') is not None:
                local.add(o['uid'])
            bodies.append(o)
missing = collections.defaultdict(set)
for o in bodies:
    if names.get(o['type']) in BULK:
        continue
    b = o['body']
    for i in range(len(b) - 5):
        if b[i] != 1 or b[i + 1] > 2:
            continue
        u = struct.unpack_from('<I', b, i + 2)[0]
        if u in local or u not in acr:
            continue
        homes = {h[0] for h in acr[u]}
        if not homes <= SHARED:
            continue
        if any((h[0] in SHARED for h in ac3.get(u, []))):
            continue
        missing[u].add(o['name'])
for u, users in sorted(missing.items(), key=lambda kv: -len(kv[1])):
    h = acr[u][0]
    print(f"{u:#010x} {h[3][:50]:50} [{names.get(h[2], hex(h[2]))}] ACR {h[0][7:]}  AC3 elsewhere: {[x[0][7:] for x in ac3.get(u, [])][:2]} | used by {len(users)}: {', '.join(sorted(users))[:80]}")
print(len(missing), 'missing')
