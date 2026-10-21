"""forgelib/maps/port/root_closure.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (MAPS, ROOT):
    sys.path.insert(0, p)
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from library_cell import cell_list, list_add, refs
OUT = os.path.join(ROOT, 'out', 'maps')

def streaming_only_refs(payloads, lib_name, world_name, layers=False):
    kinds = ('Cell', 'DataBlock') if layers else ('Cell',)
    where, copies, own_file = (defaultdict(set), defaultdict(list), set())
    for p in payloads:
        own_file.add(p.objects[0]['uid'])
        for o in p.objects:
            if o['has_uid']:
                where[o['uid']].add(p.rec.name)
                copies[o['uid']].append(o)

    def streaming_only(u):
        return u in where and lib_name not in where[u] and (world_name not in where[u]) and all((h.startswith(kinds) for h in where[u])) and (u not in own_file) and (xrefs.cls('AC3', copies[u][0]['type']) != 'GridCellDataBlock')
    byname = {p.rec.name: p for p in payloads}
    need = set()
    for name in (lib_name, world_name):
        for o in byname[name].objects:
            if xrefs.cls('AC3', o['type']) != 'GridCellDataBlock':
                need |= {u for u in refs(o['body']) if streaming_only(u)}
    frontier = set(need)
    while frontier:
        nxt = {v for u in frontier for v in refs(copies[u][0]['body']) if v not in need and streaming_only(v)}
        need |= nxt
        frontier = nxt
    return (need, where, copies)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--library', required=True, help="the root cell (the last grid node's file)")
    ap.add_argument('--world', help="the World file (default: the archive's World record)")
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--layers', action='store_true', help='data-layer files (DataBlock_*) count as streaming: what only cells and layers hold is copied too')
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    byname = {p.rec.name: p for p in payloads}
    world = a.world or next((p.rec.name for p in payloads if xrefs.cls('AC3', p.objects[0]['type']) == 'World'))
    lib = byname[a.library]
    need, where, copies = streaming_only_refs(payloads, a.library, world, a.layers)
    differ = sorted((copies[u][0]['name'] for u in need if any((c['body'] != copies[u][0]['body'] for c in copies[u]))))
    if differ:
        raise SystemExit(f'copies differ between cells, not copied: {differ}')
    report = [f"root / World references to streaming-only objects, with their closure: {len(need)} ({', '.join((f'{k} {v}' for k, v in Counter((xrefs.cls('AC3', copies[u][0]['type']) for u in need)).most_common()))})"]
    report += [f"  {xrefs.cls('AC3', copies[u][0]['type'])} {copies[u][0]['name']} (0x{u:08X}) from {sorted(where[u])[0]}" for u in sorted(need, key=lambda u: copies[u][0]['name'])]
    _n, listed, _m = cell_list(lib.objects[0]['body'])
    add = sorted(need - set(listed))
    objs = []
    for i, o in enumerate(lib.objects):
        body = list_add(o['body'], add) if i == 0 else o['body']
        objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
    for u in sorted(need, key=lambda u: (sorted(where[u])[0], u)):
        o = copies[u][0]
        objs.append((o['serial'], mapwalk.object_bytes(o), None))
    d = mapwalk.rebuild(objs, lib.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, lib.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    with Forge(dest) as f, Forge(src) as g:
        after = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index != lib.rec.index))
    left, _w, _c = streaming_only_refs(after, a.library, world, a.layers)
    root = next((p for p in after if p.rec.name == a.library))
    in_root = Counter((o['uid'] for o in root.objects if o['has_uid']))
    _n2, listed2, _m2 = cell_list(root.objects[0]['body'])
    same = all((o['body'] == copies[o['uid']][0]['body'] for o in root.objects[len(lib.objects):]))
    own = all((x['body'] == y['body'] for x, y in zip(lib.objects[1:], root.objects[1:len(lib.objects)])))
    ok = not left and all((in_root[u] == 1 for u in need)) and (set(need) <= set(listed2)) and same and own and others and (len(root.objects) == len(lib.objects) + len(need))
    report.append(f"verify: {a.library} +{len(need)} objects, each once and listed, byte-identical to its source {same}; its own objects unchanged {own}; streaming-only references left {len(left)}; every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
