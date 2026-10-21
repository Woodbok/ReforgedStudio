"""forgelib/crossmaps/layer_links.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acb
acb.patch()
import archive_fixes
import mapwalk
import xrefs
from acb_stage0 import ENTRY, layer_of, wdlm_entries
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(acb.ROOT, 'out', 'maps')

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    names = archive_fixes.layer_names('AC3')
    names.update({u: n for u, n in archive_fixes.layer_names('ACR').items() if u not in names})
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    blocks = {p.rec.name: p.objects[0]['uid'] for p in payloads if p.rec.name.startswith('DataBlock_')}
    block_uids = set(blocks.values())
    wp, wi = next(((p, i) for p in payloads for i, o in enumerate(p.objects) if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager'))
    body = wp.objects[wi]['body']
    entries = wdlm_entries(body)
    out, linked, still = ([], [], [])
    for e in entries:
        target = struct.unpack_from('<I', e, 14)[0]
        if target not in block_uids:
            name = names.get(layer_of(e))
            uid = blocks.get(f'DataBlock_{name}') if name else None
            if uid is not None:
                e = e[:14] + struct.pack('<I', uid)
                linked.append(name)
            else:
                still.append(name or f'0x{layer_of(e):08X}')
        out.append(e)
    new = body[:14] + b''.join(out)
    objs = [(o['serial'], mapwalk.object_bytes(o, new if i == wi else None), o['filter']) for i, o in enumerate(wp.objects)]
    d = mapwalk.rebuild(objs, wp.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, wp.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    with Forge(dest) as f, Forge(src) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index != wp.rec.index))
        q = mapwalk.Payload(f.records[wp.rec.index], f.read(f.records[wp.rec.index]))
        qb = q.objects[wi]['body']
    qe = wdlm_entries(qb)
    ok = len(qb) == len(body) and [layer_of(x) for x in qe] == [layer_of(x) for x in entries] and (sum((1 for x in qe if struct.unpack_from('<I', x, 14)[0] in block_uids)) == len(qe) - len(still))
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print(f"linked {len(linked)}: {', '.join(linked)}")
    print(f"no block to link {len(still)}: {', '.join(still)}")
    print(f"verify: entries {len(qe)}, naming a block file {len(qe) - len(still)}, same layers in the same order, every other record byte-identical {others}: {('OK' if ok and others else 'FAILED')}")
    if not (ok and others):
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
