"""forgelib/maps/library_cell.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
LIBRARY_CELLS = ('Cell01111_DataBlock', 'Cell01112_DataBlock', 'Cell01127_DataBlock', 'Cell01128_DataBlock', 'Cell01308_DataBlock')

def refs(body):
    out = []
    for j in range(10, len(body) - 5):
        if body[j] == 1 and body[j + 1] in (0, 1, 2):
            out.append(struct.unpack_from('<I', body, j + 2)[0])
    return out

def cell_list(body):
    n = struct.unpack_from('<I', body, 10)[0]
    end = 14 + 6 * n
    return (n, [struct.unpack_from('<I', body, k + 2)[0] for k in range(14, end, 6)], struct.unpack_from('<I', body, end)[0])

def list_add(body, uids):
    n = struct.unpack_from('<I', body, 10)[0]
    end = 14 + 6 * n
    add = b''.join((b'\x01\x00' + struct.pack('<I', u) for u in uids))
    return body[:10] + struct.pack('<I', n + len(uids)) + body[14:end] + add + body[end:]

def sideways_report(payloads, lib):
    where = defaultdict(set)
    for p in payloads:
        for o in p.objects:
            if o['has_uid']:
                where[o['uid']].add(p.rec.name)
    c = Counter()
    for p in payloads:
        if not p.rec.name.startswith('Cell'):
            continue
        local = {o['uid'] for o in p.objects}
        for o in p.objects:
            if xrefs.cls('ACR', o['type']) not in ('Entity', 'EntityGroup'):
                continue
            for u in refs(o['body']):
                if u in local or u not in where:
                    continue
                homes = where[u]
                if lib in homes:
                    c['in the library cell'] += 1
                elif all((h.startswith('Cell') for h in homes)):
                    c['only in other streaming cells'] += 1
                else:
                    c['in a non-cell file'] += 1
    return c

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N1_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--library', default='Cell01364_DataBlock', help="the last grid node's file")
    ap.add_argument('--prefix', default='N3')
    ap.add_argument('--off-grid', action='store_true', help="after a regrid (configs.souk_chain library_after_regrid): what the files on the new grid reach - through their own pieces too - that only cell files the regrid left off the grid define (Juderia's cedar model: an edge cell merged inward, its pieces left behind)")
    ap.add_argument('--no-list-copies', dest='list_copies', action='store_false', help="leave the copies out of the cell list (N3's behaviour; the game ignores them)")
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    byname = {p.rec.name: p for p in payloads}
    lib = byname[a.library]
    where = defaultdict(set)
    first = {}
    own_file = set()
    for p in payloads:
        own_file.add(p.objects[0]['uid'])
        for o in p.objects:
            if o['has_uid']:
                where[o['uid']].add(p.rec.name)
                first.setdefault(o['uid'], o)
    have = {o['uid'] for o in lib.objects if o['has_uid']}

    def streaming_only(u):
        return u in where and u not in have and all((h.startswith('Cell') for h in where[u])) and (u not in own_file)
    need = set()
    for p in payloads:
        if not p.rec.name.startswith('Cell') or p is lib:
            continue
        local = {o['uid'] for o in p.objects}
        for o in p.objects:
            if xrefs.cls('ACR', o['type']) in ('Entity', 'EntityGroup'):
                need |= {u for u in refs(o['body']) if u not in local and streaming_only(u)}
    if a.off_grid:
        from relocate_cells import gp_nodes
        gp = next((o['body'] for p in payloads for o in p.objects if xrefs.cls('ACR', o['type']) == 'GridPartition'))
        on = {k for k in gp_nodes(gp) if k}
        dead = {p.rec.name for p in payloads if p.rec.name.startswith('Cell') and p.objects[0]['uid'] not in on}

        def dead_only(u):
            return u in where and u not in have and (u not in own_file) and all((h in dead for h in where[u]))
        need = set()
        for p in payloads:
            if not p.rec.name.startswith('Cell') or p is lib or p.rec.name in dead:
                continue
            local = {o['uid']: o for o in p.objects if o['has_uid']}
            todo = [u for o in p.objects if xrefs.cls('ACR', o['type']) in ('Entity', 'EntityGroup') for u in refs(o['body'])]
            seen = set()
            while todo:
                u = todo.pop()
                if u in seen:
                    continue
                seen.add(u)
                if dead_only(u):
                    need.add(u)
                elif u in local:
                    todo.extend(refs(local[u]['body']))
        streaming_only = dead_only
    frontier = set(need)
    while frontier:
        nxt = {v for u in frontier for v in refs(first[u]['body']) if v not in need and streaming_only(v)}
        need |= nxt
        frontier = nxt
    need = {u for u in need if xrefs.cls('ACR', first[u]['type']) != 'GridCellDataBlock'}
    _n, listed, _m = cell_list(lib.objects[0]['body'])
    deps = {v for u in need for v in refs(first[u]['body']) if v in own_file}
    for name in LIBRARY_CELLS:
        p = byname.get(name)
        if p:
            loc = {o['uid'] for o in p.objects}
            deps |= {u for u in cell_list(p.objects[0]['body'])[1] if u not in loc and u in own_file}
    deps = sorted(deps - set(listed) - have)
    if a.list_copies:
        deps = sorted(set(deps) | need - set(listed))
    objs = []
    for i, o in enumerate(lib.objects):
        body = list_add(o['body'], deps) if i == 0 else o['body']
        objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
    order = sorted(need, key=lambda u: (sorted(where[u])[0], u))
    for u in order:
        o = first[u]
        objs.append((o['serial'], mapwalk.object_bytes(o), None))
    d = mapwalk.rebuild(objs, lib.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, lib.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    report = [f"{a.library}: +{len(need)} objects ({sum((len(first[u]['body']) for u in need)):,} bytes), +{len(deps)} list entries (the copies{(' and' if a.list_copies else ' not listed;')} the file-level objects they need)"]
    report.append('copied: ' + ', '.join((f'{k} {v}' for k, v in Counter((xrefs.cls('ACR', first[u]['type']) for u in need)).most_common())))
    report.append('copied from: ' + ', '.join((f'{k} {v}' for k, v in Counter((sorted(where[u])[0] for u in need)).most_common(8))))
    report.append('list entries: ' + ', '.join((f'{k} {v}' for k, v in Counter((xrefs.cls('ACR', first[u]['type']) for u in deps if u in first)).most_common())))
    report.append(f'before: {dict(sideways_report(payloads, a.library))}')
    v = Counter()
    written = ArchiveImage.read(dest)
    with Forge(dest) as f:
        after = []
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            after.append(p)
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            if r.name == a.library:
                count = Counter((o['uid'] for o in p.objects))
                v['copies defined once'] = sum((1 for u in need if count[u] == 1))
                v['copies identical'] = sum((1 for o in p.objects if o['uid'] in need and o['body'] == first[o['uid']]['body']))
                n, uids, m = cell_list(p.objects[0]['body'])
                v['list N'], v['list M'] = (n, m)
                v['deps listed'] = sum((1 for u in deps if u in uids))
    report.append(f'after: {dict(sideways_report(after, a.library))}')
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
