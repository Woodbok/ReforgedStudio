"""forgelib/witcher3/fakes.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'maps')):
    if p not in sys.path:
        sys.path.insert(0, p)
import convert
import mapwalk
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
TAIL, FIRST_AT, SPANS = (14, 12, 16)
NOWHERE = 32752

def hide_stand_ins(src, dest, log=print):
    mesh_id = convert.class_ids()['Mesh']
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    changed, names = ({}, [])
    for p in payloads:
        for i, o in enumerate(p.objects):
            b = o['body']
            if o['type'] != mesh_id or len(b) <= TAIL:
                continue
            first, spans = struct.unpack_from('<HH', b, len(b) - FIRST_AT)
            if spans != SPANS:
                continue
            nb = bytearray(b)
            struct.pack_into('<H', nb, len(nb) - FIRST_AT, NOWHERE)
            changed.setdefault(p.rec.index, {})[i] = bytes(nb)
            names.append('%s (first leaf %d)' % (o['name'], first))
    for p in payloads:
        if p.rec.index not in changed:
            continue
        objs = [(ob['serial'], mapwalk.object_bytes(ob, changed[p.rec.index].get(j, ob['body'])), ob['filter']) for j, ob in enumerate(p.objects)]
        d = mapwalk.rebuild(objs, p.filter_table)
        pl = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(pl) == d
        set_payload(image, p.rec.index, pl)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    write(dest, image)
    old = {(p.rec.index, j): ob['body'] for p in payloads for j, ob in enumerate(p.objects)}
    v = Counter()
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            for j, ob in enumerate(p.objects):
                o = old[r.index, j]
                if j in changed.get(r.index, {}):
                    v['off'] += struct.unpack_from('<H', ob['body'], len(ob['body']) - FIRST_AT)[0] == NOWHERE and ob['body'][:-FIRST_AT] == o[:-FIRST_AT]
                else:
                    v['same'] += ob['body'] == o
                    v['CHANGED'] += ob['body'] != o
    log('stand-ins switched off: %s; check: %s' % (', '.join(names), dict(v)))
    if v['CHANGED'] or v['off'] != len(names):
        raise SystemExit('stand-in patch check failed')
    return len(names)
if __name__ == '__main__':
    hide_stand_ins(sys.argv[1], sys.argv[2])
