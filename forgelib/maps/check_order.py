"""forgelib/maps/check_order.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import mapwalk, xrefs, convert, relocate_cells
from forge import Forge
INERT = struct.pack('<I', convert.class_ids()['InertComponent'])
for B in sys.argv[1:]:
    files = {}
    with Forge(os.path.join(os.path.dirname(HERE), 'out', 'maps', B, 'DataPC_ACFE_Souk.forge')) as fg:
        for r in fg.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            files[r.name] = mapwalk.Payload(r, fg.read(r))
    bad = []
    ok = 0
    for n, p in files.items():
        if not n.startswith('Cell'):
            continue
        pos = {o['uid']: i for i, o in enumerate(p.objects) if o['has_uid']}
        kinds = {o['uid']: xrefs.cls('ACR', o['type']) for o in p.objects}
        for i, o in enumerate(p.objects):
            if xrefs.cls('ACR', o['type']) != 'Entity':
                continue
            b = o['body']
            k = b.find(INERT)
            if k < 0:
                continue
            for j in range(k, min(len(b) - 5, k + 120)):
                if b[j] == 1 and b[j + 1] == 0:
                    u = struct.unpack_from('<I', b, j + 2)[0]
                    if kinds.get(u) == 'MeshShape':
                        if pos[u] < i:
                            bad.append((n, o['name'], i, pos[u]))
                        else:
                            ok += 1
                        break
    print(B, 'entities with a local shape after them:', ok, ' before them (never built):', len(bad), bad[:4])
