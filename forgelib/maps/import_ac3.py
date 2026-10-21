"""forgelib/maps/import_ac3.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from merge_cells import cell_list
OUT = os.path.join(ROOT, 'out', 'maps')
ROOTS = (3609914036, 3609914048, 3609914051, 3609914054, 764693179, 764693204)

def refs(b):
    out = set()
    for j in range(10, len(b) - 5):
        if b[j] == 1 and b[j + 1] in (0, 1, 2):
            out.add(struct.unpack_from('<I', b, j + 2)[0])
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--from', dest='source', default='DataPC_AC3MP_Boston_Mill.forge', help='the AC3 map archive')
    ap.add_argument('--root', action='append', type=lambda v: int(v, 0), default=None, help='uids to import (default: the N43 CustomActionPacks and marker)')
    ap.add_argument('--into', default='Cell01364_DataBlock')
    ap.add_argument('--max', type=int, default=200, help='refuse a closure bigger than this')
    a = ap.parse_args(argv)
    roots = a.root or list(ROOTS)
    source = {}
    for p in mapwalk.payloads('AC3', a.source):
        for o in p.objects:
            if o['has_uid']:
                source.setdefault(o['uid'], o)
    always = mapwalk.defined_uids('AC3', 'DataPC.forge')
    take, seen, todo = ([], set(), list(roots))
    while todo:
        u = todo.pop()
        if u in seen:
            continue
        seen.add(u)
        if u in always or u not in source:
            continue
        take.append(source[u])
        if len(take) > a.max:
            raise SystemExit(f'closure over {a.max} objects: stopping (check the roots)')
        todo.extend((v for v in refs(source[u]['body']) if v not in seen))
    missing = [u for u in roots if u not in source and u not in always]
    if missing:
        raise SystemExit(f'roots neither in {a.source} nor DataPC: {[hex(u) for u in missing]}')
    take_by_uid = {o['uid']: o for o in take}
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    defined = Counter((o['uid'] for p in payloads for o in p.objects if o['has_uid']))
    replace = {u for u in take_by_uid if defined[u]}
    append = [o for o in take if o['uid'] not in replace]
    counts, report = (Counter(), [])
    for p in payloads:
        is_lib = p.rec.name == a.into
        hits = [i for i, o in enumerate(p.objects) if o['has_uid'] and o['uid'] in replace]
        if not hits and (not is_lib):
            continue
        objs = []
        for i, o in enumerate(p.objects):
            if o['has_uid'] and o['uid'] in replace:
                s = take_by_uid[o['uid']]
                if s['type'] != o['type']:
                    raise SystemExit(f"{o['name']}: class differs between the games ({o['type']:#x} / {s['type']:#x})")
                objs.append((o['serial'], mapwalk.object_bytes(s), o['filter']))
                counts['definitions replaced'] += 1
            else:
                objs.append((o['serial'], mapwalk.object_bytes(o), o['filter']))
        if is_lib and append:
            n, entries, m, end = cell_list(p.objects[0]['body'])
            listed = {struct.unpack_from('<I', e, 2)[0] for e in entries}
            add = [b'\x01\x00' + struct.pack('<I', o['uid']) for o in append if o['uid'] not in listed]
            g = p.objects[0]['body']
            g = g[:10] + struct.pack('<I', n + len(add)) + b''.join(entries) + b''.join(add) + g[end:]
            objs[0] = (p.objects[0]['serial'], mapwalk.object_bytes(p.objects[0], g), p.objects[0]['filter'])
            objs += [(o['serial'], mapwalk.object_bytes(o), None) for o in append]
            counts['appended and listed'] += len(append)
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    for o in take:
        report.append(f"  {('replaced' if o['uid'] in replace else 'added   ')} {xrefs.cls('AC3', o['type']):22} {o['name']} ({o['uid']:#010x}, {len(o['body'])} B)")
    v = Counter()
    written = ArchiveImage.read(dest)
    bodies = {}
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            for o in p.objects:
                if o['has_uid'] and o['uid'] in take_by_uid:
                    bodies.setdefault(o['uid'], []).append((r.name, o['body']))
            if r.name == a.into:
                _n, e2, _m, _ = cell_list(p.objects[0]['body'])
                l2 = {struct.unpack_from('<I', e, 2)[0] for e in e2}
                v['added and listed'] = sum((1 for o in append if o['uid'] in l2))
    v["imported uids defined only as AC3's"] = sum((1 for u, lst in bodies.items() if all((b == take_by_uid[u]['body'] for _r, b in lst))))
    v['added uids defined once, in --into'] = sum((1 for o in append if [r for r, _b in bodies.get(o['uid'], [])] == [a.into]))
    report.insert(0, f"from {a.source}: {len(take)} objects ({len(replace)} replace ACR's copies, {len(append)} added to {a.into}); " + ', '.join((f'{k} {n}' for k, n in counts.items())))
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
