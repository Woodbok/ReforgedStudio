"""forgelib/maps/port/tree_stubs.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (MAPS, ROOT):
    sys.path.insert(0, p)
import build_static
import convert
import entity
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from merge_cells import cell_list
OUT = os.path.join(ROOT, 'out', 'maps')

def merged_members(body, multi_inert):
    k = body.find(multi_inert)
    if k < 0:
        return []
    for at in range(k, min(len(body) - 8, k + 64)):
        n = struct.unpack_from('<I', body, at)[0]
        end = at + 4 + 5 * n
        if 0 < n < 5000 and end + 4 <= len(body) and (struct.unpack_from('<I', body, end)[0] == n) and all((body[at + 4 + 5 * i] == 0 for i in range(n))):
            return [struct.unpack_from('<I', body, at + 5 + 5 * i)[0] for i in range(n)]
    return []

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--map', required=True, help='the ACR archive')
    ap.add_argument('--prefix', required=True)
    a = ap.parse_args(argv)
    ids = convert.class_ids()
    tree = struct.pack('<I', ids['RealTreeMeshInstanceData'])
    multi_inert = struct.pack('<I', ids['MultiInertComponent'])
    targets = convert.targets('ACR')
    unknown_in = build_static.unknown_checker()
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}
    defined = {o['uid'] for p in payloads.values() for o in p.objects if o['has_uid']}
    members = {u for p in payloads.values() for o in p.objects if xrefs.cls('AC3', o['type']) == 'Entity' for u in merged_members(o['body'], multi_inert)}
    dangling = members - defined
    report = [f'merged-collision members {len(members)}, not defined in the build {len(dangling)}']
    changed, stubs = ({}, {})
    for p in mapwalk.payloads('ACR', a.map):
        hits = [o for o in p.objects if o['has_uid'] and o['uid'] in dangling]
        if not hits:
            continue
        if xrefs.cls('ACR', p.objects[0]['type']) != 'GridCellDataBlock' or p.rec.name not in payloads:
            raise SystemExit(f'{p.rec.name}: not a cell of the build')
        mine = payloads[p.rec.name]
        work = list(mine.objects)
        head = work[0]['body']
        n, entries, m, end = cell_list(head)
        entries = list(entries)
        _an, a_entries, a_m, _ae = cell_list(p.objects[0]['body'])
        a_list = [struct.unpack_from('<I', e, 2)[0] for e in a_entries]
        acr_order = [o['uid'] for o in p.objects if o['has_uid']]
        for o in hits:
            if xrefs.cls('ACR', o['type']) != 'Entity' or tree not in o['body']:
                raise SystemExit(f"{o['name']}: a dangling member that isn't a tree entity; not handled")
            body, removed = entity.strip(o['body'], lambda name, el: tree in el)
            if removed != ['Visual']:
                raise SystemExit(f"{o['name']}: expected to strip one Visual, got {removed}")
            body, _log = convert.convert(body, targets)
            if tree in body or unknown_in(body):
                raise SystemExit(f"{o['name']}: stub still holds {sorted(unknown_in(body)) or 'the tree'}")
            here = [x['uid'] if x['has_uid'] else None for x in work]
            pos = acr_order.index(o['uid'])
            anchor = next((u for u in reversed(acr_order[:pos]) if u in here), None)
            at = here.index(anchor) + 1 if anchor is not None else 1
            stub = dict(o, body=body, body_size=o['body_size'] + len(body) - len(o['body']))
            work.insert(at, stub)
            cur = [struct.unpack_from('<I', e, 2)[0] for e in entries]
            if o['uid'] in a_list[:a_m]:
                lp = a_list.index(o['uid'])
                prev = next((u for u in reversed(a_list[:lp]) if u in cur[:m]), None)
                li = cur.index(prev) + 1 if prev is not None else 0
                entries.insert(li, b'\x01\x00' + struct.pack('<I', o['uid']))
                n, m = (n + 1, m + 1)
            stubs[o['uid']] = body
            report.append(f"{p.rec.name}: {o['name']} (0x{o['uid']:08X}) back as a stub, {len(o['body'])} -> {len(body)} B, after {('0x%08X' % anchor if anchor else 'the list')}; listed {o['uid'] in a_list[:a_m]}")
        head = head[:10] + struct.pack('<I', n) + b''.join(entries) + struct.pack('<I', m) + head[end + 4:]
        objs = [(work[0]['serial'], mapwalk.object_bytes(work[0], head), work[0]['filter'])]
        objs += [(x['serial'], mapwalk.object_bytes(x), x['filter']) for x in work[1:]]
        d = mapwalk.rebuild(objs, mine.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, mine.rec.index, payload)
        changed[mine.rec.index] = mine
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    with Forge(dest) as f:
        after = {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}
        with Forge(src) as g:
            others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index not in changed))
    defined2 = [o['uid'] for p in after.values() for o in p.objects if o['has_uid']]
    left = members - set(defined2)
    once = all((defined2.count(u) == 1 for u in stubs))
    as_built = all((o['body'] == stubs[o['uid']] for p in after.values() for o in p.objects if o['has_uid'] and o['uid'] in stubs))
    lists_ok = True
    for idx, p in changed.items():
        q = after[p.rec.name]
        n2, entries2, m2, _e = cell_list(q.objects[0]['body'])
        lists_ok &= n2 == len(entries2) and len(q.objects) == len(p.objects) + sum((1 for o in q.objects if o['has_uid'] and o['uid'] in stubs))
    ok = not left and once and as_built and lists_ok and others and (bool(stubs) == bool(dangling))
    report.append(f"verify: {len(stubs)} stubs, merged members still undefined {len(left)}, each stub once {once}, stubs as built {as_built}, cell lists consistent {lists_ok}, every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
