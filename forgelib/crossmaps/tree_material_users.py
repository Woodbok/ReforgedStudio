"""forgelib/crossmaps/tree_material_users.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acb
acb.patch()
import mapwalk
import xrefs
import zfight_probe
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(acb.ROOT, 'out', 'maps')
SUFFIX = '_AC3'
USERS = ('Mesh', 'Entity', 'EntityGroup')

def sites(body, uid, cls):
    pat = struct.pack('<I', uid)
    ks, k = ([], body.find(pat))
    while k >= 0:
        ks.append(k)
        k = body.find(pat, k + 1)
    if cls == 'Mesh':
        try:
            st, vb, nv, ib, ni = zfight_probe.buffers_any(body)
            ks = [k for k in ks if not (vb <= k < vb + st * nv or ib <= k < ib + 2 * ni)]
        except ValueError:
            pass
    return ks

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    mats = {}
    tree_named = set()
    for p in payloads:
        for o in p.objects:
            c = xrefs.cls('AC3', o['type'])
            if c == 'Material' and o['has_uid']:
                mats.setdefault(o['name'], o['uid'])
            if c == 'RealTreeMesh':
                b = o['body']
                tree_named |= {struct.unpack_from('<I', b, k + 2)[0] for k in range(len(b) - 5) if b[k] == 1 and b[k + 1] in (0, 1)}
    pairs = {mats[n[:-len(SUFFIX)]]: (u, n) for n, u in mats.items() if n.endswith(SUFFIX) and u in tree_named and (n[:-len(SUFFIX)] in mats)}
    print('  tree clones: ' + ', '.join((f'{n[:-len(SUFFIX)]} 0x{x:08X} -> {n} 0x{u:08X}' for x, (u, n) in pairs.items())))
    moved, touched = (Counter(), set())
    for p in payloads:
        objs, changed = ([], False)
        for o in p.objects:
            body, c = (o['body'], xrefs.cls('AC3', o['type']))
            if c in USERS:
                for x, (u, n) in pairs.items():
                    ks = sites(body, x, c)
                    if ks:
                        b = bytearray(body)
                        for k in ks:
                            struct.pack_into('<I', b, k, u)
                        body = bytes(b)
                        moved[o['name'], c] += len(ks)
                        changed = True
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        if changed:
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, p.rec.index, payload)
            touched.add(p.rec.index)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    for (n, c), k in sorted(moved.items()):
        print(f'  {c} {n}: {k} references onto the clone')
    left, same_len = ([], True)
    with Forge(dest) as f, Forge(src) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index not in touched))
        for r in f.records:
            if r.index not in touched:
                continue
            q = mapwalk.Payload(r, f.read(r))
            old = next((p for p in payloads if p.rec.index == r.index))
            for o, o0 in zip(q.objects, old.objects):
                c = xrefs.cls('AC3', o['type'])
                same_len &= len(o['body']) == len(o0['body'])
                if c in USERS and any((sites(o['body'], x, c) for x in pairs)):
                    left.append(o['name'])
    ok = not left and same_len and others and bool(pairs)
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print(f"verify: {len(pairs)} tree clones; {sum(moved.values())} references in {len(moved)} objects onto them; originals still named by a Mesh / Entity / EntityGroup {len(left)}; sizes unchanged {same_len}; every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
