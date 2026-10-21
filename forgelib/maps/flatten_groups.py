"""forgelib/maps/flatten_groups.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from summary import BULK
OUT = os.path.join(ROOT, 'out', 'maps')
FAMILIES = ('ACFE_RHO_SOU_Shop_664_01A', 'ACFE_RHO_SOU_Shop_664_01B', 'ACFE_RHO_SOU_ShopFuzzy_664_01A', 'ACFE_RHO_SOU_ShopFuzzy_664_01B', 'ACFE_RHO_SOU_Store_934_01A', 'ACFE_RHO_SOU_Narguile_66_01A', 'ACFE_RHO_ORI_Lamp_01A', 'ACFE_RHO_lamp_02A', 'ACFE_RHO_Chimney_01A', 'ACFE_RHO_Chimney_02A')
TAIL = 77
EMPTY_LIST = b'\x03' + b'\x00' * 5

def family(name):
    return next((f for f in FAMILIES if name.startswith(f + ' ') or name.startswith(f + '_Group')), None)

def children(body):
    ids = convert.class_ids()
    ent, bv = (struct.pack('<I', ids['Entity']), struct.pack('<I', ids['BoundingVolume']))
    starts = []
    k = body.find(ent, 20)
    while k > 0:
        if body[k - 6:k - 4] == b'\x00\x01':
            starts.append(k - 6)
        k = body.find(ent, k + 4)
    if not starts:
        return None
    out = []
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else len(body)
        e = body.rfind(bv, s, nxt) + TAIL
        if e - TAIL < s or (i + 1 < len(starts) and e != nxt):
            return None
        out.append((s, e))
    rest = body[out[-1][1]:]
    if len(rest) % len(EMPTY_LIST) or rest != EMPTY_LIST * (len(rest) // len(EMPTY_LIST)):
        return None
    return out

def cell_list(body):
    n = struct.unpack_from('<I', body, 10)[0]
    end = 14 + 6 * n
    return (n, [struct.unpack_from('<I', body, k + 2)[0] for k in range(14, end, 6)], struct.unpack_from('<I', body, end)[0])

def relist(body, group, kids):
    n, uids, m = cell_list(body)
    end = 14 + 6 * n
    i = uids.index(group)
    entries = [body[k:k + 6] for k in range(14, end, 6)]
    entries[i:i + 1] = [b'\x01\x00' + struct.pack('<I', u) for u in kids]
    grow = len(kids) - 1
    return body[:10] + struct.pack('<I', n + grow) + b''.join(entries) + struct.pack('<I', m + grow) + body[end + 4:]

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N1_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='N2')
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    ids = convert.class_ids()
    report, counts = ([], Counter())
    lifted = {}
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    defined = Counter((o['uid'] for p in payloads for o in p.objects if o['has_uid']))
    for p in payloads:
        groups = [o for o in p.objects[1:] if xrefs.cls('ACR', o['type']) == 'EntityGroup' and family(o['name'])]
        if not groups:
            continue
        if xrefs.cls('ACR', p.objects[0]['type']) != 'GridCellDataBlock':
            raise SystemExit(f'{p.rec.name}: groups to lift but no GridCellDataBlock first')
        objs, head = ([], p.objects[0]['body'])
        for o in p.objects[1:]:
            parts = children(o['body']) if o in groups else None
            if o in groups and parts is None:
                report.append(f"skip  {o['name']}: children don't split cleanly")
                counts['groups skipped'] += 1
            if not parts:
                objs.append((o['serial'], mapwalk.object_bytes(o, o['body']), o['filter']))
                continue
            kids = []
            for i, (s, e) in enumerate(parts):
                body = o['body'][s:e]
                uid = struct.unpack_from('<I', body, 2)[0]
                if body[6:10] != struct.pack('<I', ids['Entity']) or body[74] != 3 or defined[uid]:
                    raise SystemExit(f"{o['name']} child {i}: not a standalone Entity (0x{uid:08X})")
                name = f"{o['name']}_{i}".encode('latin-1')
                data = struct.pack('<III', ids['Entity'], len(body) - 1, len(name)) + name + body
                objs.append((uid, data, o['filter']))
                kids.append(uid)
            head = relist(head, o['uid'], kids)
            lifted[o['uid']] = kids
            counts['groups lifted'] += 1
            counts['entities lifted'] += len(kids)
            counts[f"lifted: {family(o['name'])}"] += 1
        first = p.objects[0]
        objs.insert(0, (first['serial'], mapwalk.object_bytes(first, head), first['filter']))
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
        counts['files rewritten'] += 1
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    kids_all = {u for ks in lifted.values() for u in ks}
    gone = {struct.pack('<I', g) for g in lifted}
    seen = Counter()
    written = ArchiveImage.read(dest)
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            listed = set()
            if xrefs.cls('ACR', p.objects[0]['type']) == 'GridCellDataBlock':
                n, uids, m = cell_list(p.objects[0]['body'])
                listed = set(uids)
                v['lists naming a lifted group'] += bool(listed & set(lifted))
            for o in p.objects:
                if o['uid'] in lifted and o['has_uid']:
                    v['lifted groups still present'] += 1
                if o['uid'] in kids_all:
                    seen[o['uid']] += 1
                    v['children not listed in their cell'] += o['uid'] not in listed
                    v['children not Entities'] += xrefs.cls('ACR', o['type']) != 'Entity'
                cls = xrefs.cls('ACR', o['type'])
                if cls not in BULK and cls != 'GridCellDataBlock' and any((g in o['body'][6:] for g in gone)):
                    v['objects mentioning a lifted group'] += 1
    v['children defined once'] = sum((1 for u in kids_all if seen[u] == 1))
    v['children'] = len(kids_all)
    for k, val in sorted(counts.items()):
        report.append(f'{k}: {val}')
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
