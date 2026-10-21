"""forgelib/maps/port/orator_speech.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import entity
import exe_registry
import mapwalk
import merchant_stalls as ms
import npc_port
import own_entity
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from library_cell import cell_list, list_add, refs
OUT = os.path.join(ROOT, 'out', 'maps')
PREFIX = 'ACFE_GEN_Orator'
IDLE_PACK = 3609914015
CROWD_PACK_NAME = 'Npc_group_specific_orator_crowd_Pack'

def speaker_pack(acr_body, acr_names):
    mp_logic = ms.crowdlife.MP_LOGIC
    comps = [(s, e) for s, e, n in entity.components(acr_body) if n == 'GameplayCoordinatorComponent']
    speaker = [(s, e) for s, e in comps if mp_logic not in acr_body[s:e]]
    assert len(speaker) == 1, f'{len(speaker)} speaker components'
    s, e = speaker[0]
    packs = [u for u in dict.fromkeys(refs(acr_body[s:e])) if acr_names.get(u, (None, None))[1] == 'CustomActionPack']
    assert len(packs) == 1, f'speaker packs: {[hex(u) for u in packs]}'
    return packs[0]

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--acr-archive', help="ACR's archive of the map (default: --archive)")
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--into', required=True, help='the root cell')
    ap.add_argument('--uid-base', type=lambda v: int(v, 0), required=True)
    ap.add_argument('--uid-limit', type=lambda v: int(v, 0), default=256)
    a = ap.parse_args(argv)
    classes, _ = exe_registry.load(exe_registry.AC3)
    acr_names, acr_orators = ({}, {})
    for p in mapwalk.payloads('ACR', a.acr_archive or a.archive, skip_errors=True):
        for o in p.objects:
            if o['has_uid']:
                acr_names.setdefault(o['uid'], (o['name'], xrefs.cls('ACR', o['type'])))
            if (o['name'] or '').startswith(PREFIX) and xrefs.cls('ACR', o['type']) == 'Entity':
                acr_orators[o['name']] = o['body']
    want = {n: speaker_pack(b, acr_names) for n, b in acr_orators.items()}
    src = os.path.join(OUT, a.src, a.archive)
    files = npc_port.payloads(src)
    defs = npc_port.defined(files)
    fresh = npc_port.RangeUids(a.uid_base, a.uid_limit, set(defs), [p.data for _r, p, _raw in files.values()])
    copies, pack_of = ({}, {})
    for name, pk in sorted(want.items()):
        if pk in copies:
            pack_of[name] = copies[pk][0]['uid']
            continue
        assert pk in defs, f'{name}: its speaker pack 0x{pk:08X} is not in {a.archive}'
        pack = defs[pk][1]
        actions = [u for u in dict.fromkeys(refs(pack['body'])) if u in defs and classes.get(defs[u][1]['type']) == 'CustomAction']
        assert actions and classes.get(pack['type']) == 'CustomActionPack', (name, hex(pk))
        objs = [pack] + [defs[u][1] for u in actions]
        mapping = {}
        for o in objs:
            for u in own_entity.inline_uids(o['body'], classes):
                mapping.setdefault(u, fresh.take())
        out = []
        for o in objs:
            b = npc_port.replace_u32s(o['body'], mapping)
            out.append(dict(o, body=b, uid=mapping[o['uid']], serial=mapping[o['uid']]))
        copies[pk] = out
        pack_of[name] = out[0]['uid']
    image = ArchiveImage.read(src)
    done, touched = ([], set())
    for fname, (rec, p, _raw) in files.items():
        is_root = fname == a.into
        hits = [o for o in p.objects if o['name'] in pack_of and classes.get(o['type']) == 'Entity']
        if not hits and (not is_root):
            continue
        objs = []
        for o in p.objects:
            body = o['body']
            if o in hits:
                k = [i for i in range(len(body) - 3) if struct.unpack_from('<I', body, i)[0] == IDLE_PACK]
                assert len(k) == 1, (o['name'], k)
                body = body[:k[0]] + struct.pack('<I', pack_of[o['name']]) + body[k[0] + 4:]
                done.append(o['name'])
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        if is_root:
            g = p.objects[0]
            new = [c for cs in copies.values() for c in cs]
            _n, listed, _m = cell_list(g['body'])
            gb = list_add(g['body'], [c['uid'] for c in new if c['uid'] not in listed])
            objs[0] = (g['serial'], mapwalk.object_bytes(g, gb), g['filter'])
            objs += [(c['serial'], mapwalk.object_bytes(c, c['body']), None) for c in new]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, rec.index, payload)
        touched.add(rec.index)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    after = npc_port.payloads(dest)
    defs2 = Counter((o['uid'] for _r, p, _raw in after.values() for o in p.objects if o['has_uid']))
    root = after[a.into][1]
    _n, listed, _m = cell_list(root.objects[0]['body'])
    v = Counter()
    for _r, p, _raw in after.values():
        for o in p.objects:
            if o['name'] in pack_of and classes.get(o['type']) == 'Entity':
                b = o['body']
                v["orators on ACR's speech pack"] += struct.pack('<I', pack_of[o['name']]) in b and struct.pack('<I', IDLE_PACK) not in b
    new = [c for cs in copies.values() for c in cs]
    got = {o['uid']: o['body'] for o in root.objects if o['has_uid']}
    v['copies once, in the root, listed'] = sum((1 for c in new if got.get(c['uid']) == c['body'] and defs2[c['uid']] == 1 and (c['uid'] in listed)))
    acts = lambda body: [struct.unpack_from('<I', body, i + 2)[0] for i in range(len(body) - 5) if body[i:i + 2] == b'\x01\x00']
    v['action ids kept'] = sum((1 for pk, cs in copies.items() for c, o in zip(cs, [defs[pk][1]] + [defs[u][1] for u in dict.fromkeys(refs(defs[pk][1]['body'])) if u in defs and classes.get(defs[u][1]['type']) == 'CustomAction']) if classes.get(c['type']) != 'CustomAction' or [x for x in acts(c['body']) if x not in {y['uid'] for y in cs}] == [x for x in acts(o['body']) if x not in {y['uid'] for y in [defs[pk][1]]}]))
    with Forge(dest) as f, Forge(src) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index not in touched))
    ok = v["orators on ACR's speech pack"] == len(pack_of) == len(done) > 0 and v['copies once, in the root, listed'] == len(new) and others
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    for name in sorted(pack_of):
        print(f"  {name}: ACR's speaker pack 0x{want[name]:08X} -> copy 0x{pack_of[name]:08X}")
    print(f"verify: {dict(v)} of {len(new)} copied objects, every other file byte-identical {others}: {('OK' if ok else 'FAILED')}")
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
