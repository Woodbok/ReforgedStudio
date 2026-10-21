"""forgelib/maps/fake_mesh_leaf.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import convert
import fake_spans
import fake_tiles
import mapwalk
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
TAIL = 14
FIRST_AT = 12
SPANS = 16

def mesh_tail(body):
    return struct.unpack_from('<BBHHHHI', body, len(body) - TAIL)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N57_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='N61')
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    mesh_id = convert.class_ids()['Mesh']
    fakes = [(p, o) for p in payloads for o in p.objects if o['body'][6:10] == struct.pack('<I', fake_spans.FAKES)]
    if len(fakes) != 1:
        raise SystemExit(f'{len(fakes)} FakeEntities objects, expected 1')
    p_fe, o_fe = fakes[0]
    els = fake_tiles.elements(o_fe['body'])
    meshes = {}
    for p in payloads:
        for i, o in enumerate(p.objects):
            if o['type'] == mesh_id and o.get('has_uid', True) and (len(o['body']) > TAIL) and (mesh_tail(o['body'])[3] == SPANS):
                meshes[o['uid']] = (p, i, o)
    counts, plan = (Counter(), {})
    for n, (start, end, xy, first) in enumerate(els):
        if first != SPANS * n:
            raise SystemExit(f'tile {n} at {xy}: record first leaf {first}, expected {SPANS * n} (run fake_tiles.py first)')
        seg = o_fe['body'][start:end]
        refs = [u for u in meshes if struct.pack('<I', u) in seg]
        if len(refs) != 1:
            raise SystemExit(f'tile {n} at {xy}: {len(refs)} span meshes referenced, expected 1')
        u = refs[0]
        old = mesh_tail(meshes[u][2]['body'])[2]
        plan[u] = (n, first, old)
        counts[f"tile {n} at {xy}: {meshes[u][2]['name']} first leaf {old} -> {first}"] += 1
    for u, (_p, _i, o) in meshes.items():
        if u not in plan:
            counts[f"span mesh no kept tile references, left as is: {o['name']} (first leaf {mesh_tail(o['body'])[2]})"] += 1
    changed = {}
    for u, (n, first, old) in plan.items():
        p, i, o = meshes[u]
        nb = bytearray(o['body'])
        struct.pack_into('<H', nb, len(nb) - FIRST_AT, first)
        changed.setdefault(p.rec.index, {})[i] = bytes(nb)
    for p in payloads:
        if p.rec.index not in changed:
            continue
        objs = [(ob['serial'], mapwalk.object_bytes(ob, changed[p.rec.index].get(j, ob['body'])), ob['filter']) for j, ob in enumerate(p.objects)]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    old_bodies = {(p.rec.index, j): ob['body'] for p in payloads for j, ob in enumerate(p.objects)}
    v = Counter()
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            for j, ob in enumerate(p.objects):
                old = old_bodies[r.index, j]
                if j in changed.get(r.index, {}):
                    t = mesh_tail(ob['body'])
                    good = t[2] == plan[ob['uid']][1] and t[3] == SPANS and (ob['body'][:-FIRST_AT] == old[:-FIRST_AT]) and (ob['body'][-FIRST_AT + 2:] == old[-FIRST_AT + 2:])
                    v['span meshes pointed at their tile (2 bytes each)'] += good
                    v['not as planned'] += not good
                else:
                    v['other objects unchanged'] += ob['body'] == old
                    v['other objects CHANGED'] += ob['body'] != old
    report = [', '.join((f'{k} {n}' for k, n in counts.items())), f'verify: {dict(v)}']
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
