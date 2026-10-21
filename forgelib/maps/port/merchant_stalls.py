"""forgelib/maps/port/merchant_stalls.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import convert
import crowdlife
import mapwalk
import stock_twins
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from library_cell import cell_list, list_add, refs
OUT = os.path.join(ROOT, 'out', 'maps')
PREFIX = 'ACFE_GEN_Merchant'
GENERIC_MERCHANT = 3518325269
IDS = convert.class_ids()
REAL = {v: k for k, v in IDS.items() if v >= 1 << 24 and k[:1].isupper() and (not k.isupper()) and (k not in ('SSLv2', 'TLSv1', 'SEED'))}
SPB, GPOS, NPC = (struct.pack('<I', IDS[k]) for k in ('SpawnPositionBlobParams', 'GameplayPosition', 'SpawnNPCParams'))
STATE = b'G\x00\x00\x00\x01\x00\x00\x00'

def load(game, path):
    out = []
    with Forge(path) as f:
        for r in f.records:
            if r.name != 'GlobalMetaFile' and r.size:
                out.append(mapwalk.Payload(r, f.read(r)))
    return out

def _held(body, uids):
    try:
        import numpy as np
    except ImportError:
        return _held_py(body, uids)
    if _HELD.get('uids') is not uids or _HELD.get('n') != len(uids):
        arr = np.array(sorted(uids), dtype=np.uint32)
        top = np.zeros(1 << 16, dtype=bool)
        top[arr >> 16] = True
        _HELD.update(uids=uids, n=len(uids), arr=arr, top=top)
    arr, top = (_HELD['arr'], _HELD['top'])
    found = set()
    if not len(arr):
        return found
    for j in range(4):
        m = (len(body) - j) // 4
        if m <= 0:
            continue
        v = np.frombuffer(body, dtype='<u4', count=m, offset=j)
        idx = np.flatnonzero(top[v >> 16])
        if idx.size:
            vals = v[idx]
            found.update(vals[arr[np.minimum(np.searchsorted(arr, vals), len(arr) - 1)] == vals].tolist())
    return found
_HELD = {}

def _held_py(body, uids):
    return {u for u in uids if struct.pack('<I', u) in body}

def embedded(body):
    out = set()
    for j in range(0, len(body) - 8):
        c = struct.unpack_from('<I', body, j + 4)[0]
        if c in REAL:
            v = struct.unpack_from('<I', body, j)[0]
            if v >= 65536 and v not in REAL:
                out.add(v)
    return out

def blocks(body):
    spb, gp = crowdlife.slot_offsets(body)
    if len(spb) != 8 or len(gp) != 8:
        raise SystemExit(f'template: {len(spb)} spawn blobs / {len(gp)} stations, expected 8 / 8 (a two-block stall)')
    return (spb[0], gp[0], spb[1:], gp[1:])

def acr_slots(acr_body):
    mp = crowdlife.mp_slot_offsets(acr_body)
    npc = [k for k in crowdlife.slot_offsets(acr_body)[0] if k not in mp]
    if len(npc) != 1:
        raise SystemExit(f'ACR stall: {len(npc)} merchant slots')
    return (acr_body[npc[0]:npc[0] + 32], [acr_body[k:k + 32] for k in mp])

def transposed(placement):
    m = struct.unpack('<16f', placement)
    return struct.pack('<16f', *(m[r + 4 * c] for r in range(4) for c in range(4)))

def rebuild(tmpl, tmpl_world, acr_body, target_body, world, builder, alloc):
    body = bytearray(tmpl)
    own_t, own = (struct.unpack_from('<I', tmpl, 2)[0], struct.unpack_from('<I', target_body, 2)[0])
    mapping = {u: own if u == own_t else alloc() for u in sorted(embedded(tmpl) | crowdlife.embedded_uids(tmpl))}
    for old, new in mapping.items():
        pat, rep = (struct.pack('<I', old), struct.pack('<I', new))
        for k in crowdlife._all(bytes(body), pat):
            body[k:k + 4] = rep
    wk = crowdlife._all(bytes(body), struct.pack('<I', tmpl_world))
    if len(wk) != 1:
        raise SystemExit(f'template names its World {len(wk)} times')
    struct.pack_into('<I', body, wk[0], world)
    body[10:74] = target_body[10:74]
    s1, g1, s2, g2 = blocks(bytes(body))
    merchant, blend = acr_slots(acr_body)
    body[s1:s1 + 32] = merchant
    body[g1:g1 + 32] = merchant
    for i in range(7):
        body[s2[i]:s2[i] + 32] = blend[i]
        body[g2[i]:g2[i] + 32] = blend[i]
    k = crowdlife._all(bytes(body), STATE)
    if len(k) != 1:
        raise SystemExit(f'template: {len(k)} GameStateData matrices')
    body[k[0] + 8:k[0] + 72] = transposed(body[10:74])
    gm = struct.pack('<I', GENERIC_MERCHANT)
    names = b'\x01\x00' + gm + b'\x00' + gm
    if bytes(body).count(names) != 1:
        raise SystemExit("template: block 1's SpawnNPCParams not `01 00 uid 00 uid` on AC3's merchant builder")
    b = struct.pack('<I', builder)
    j = bytes(body).find(names)
    body[j:j + 11] = b'\x01\x00' + b + b'\x00' + b
    return (bytes(body), mapping)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--acr-archive', help="ACR's archive of the map (default: --archive)")
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--from', dest='source', default='DataPC_AC3MP_Boston_Mill.forge', help='the AC3 map with the template')
    ap.add_argument('--template', default='AC3MP_GEN_Merchant_014')
    ap.add_argument('--into', required=True, help='the root cell')
    ap.add_argument('--uid-base', type=lambda v: int(v, 0), required=True)
    ap.add_argument('--uid-limit', type=lambda v: int(v, 0), default=8192, help='size of the uid range (Souk: 0x5AD4C000..0x5AD4DFFF; 0x5AD4EE2E is an npc_port uid)')
    ap.add_argument('--builder', type=lambda v: int(v, 0), default=GENERIC_MERCHANT)
    ap.add_argument('--stalls', default=PREFIX, help="the entities rebuilt, by name prefix (ACR's orator groups, ACFE_GEN_Orator: the same shape - a 7-slot blend group and one speaker slot)")
    ap.add_argument('--present', choices=('archive', 'root'), default='archive', help="an object the archive defines counts as there: in any file (archive; Souk's SM3) or only in a file the map always loads (root: the root cell, its listed files, data blocks, the World; the other ported maps)")
    a = ap.parse_args(argv)
    a.stalls = tuple(a.stalls.split(','))
    mill = load('AC3', mapwalk.archive_path('AC3', a.source))
    source = {}
    for p in mill:
        for o in p.objects:
            if o['has_uid']:
                source.setdefault(o['uid'], o)
    tmpl = next((o for p in mill for o in p.objects if o['name'] == a.template))['body']
    tmpl_world = crowdlife._world_of(tmpl)
    corefx = mapwalk.defined_uids('AC3', 'DataPC_CoreFX.forge')
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    payloads = load('AC3', src)
    target = {}
    for p in payloads:
        for o in p.objects:
            if o['has_uid']:
                target.setdefault(o['uid'], []).append((p, o))
    world = next((o['uid'] for p in payloads for o in p.objects if xrefs.cls('AC3', o['type']) == 'World'))
    loaded_names, always_names, reach = (None, None, None)
    if a.present == 'root':
        import exe_registry
        import npc_port
        files = {}
        for p in payloads:
            files.setdefault(p.rec.name, (p.rec, p, None))
        classes = exe_registry.load(exe_registry.AC3)[0]
        loaded_names = set(npc_port.loaded_files(files, classes)[0])
        base, reach = npc_port.always_loaded(files, classes, a.into)
        always_names = set(base)
    own_t = struct.unpack_from('<I', tmpl, 2)[0]
    todo = [u for u in set(refs(tmpl)) if u in source and u != own_t and (u != tmpl_world)]
    carry, replace, same, seen = ([], [], 0, set())
    while todo:
        u = todo.pop()
        if u in seen:
            continue
        seen.add(u)
        if u < 65536 or u in corefx or u not in source or (xrefs.cls('AC3', source[u]['type']) == 'World'):
            continue
        if u in target and (reach is None or u in reach):
            if all((o['body'] == source[u]['body'] for _p, o in target[u])):
                same += 1
                continue
            replace.append(u)
        else:
            carry.append(u)
            if u in target and any((o['body'] != source[u]['body'] for p_, o in target[u] if p_.rec.name in loaded_names)):
                replace.append(u)
        todo.extend((v for v in refs(source[u]['body']) if v not in seen))
    if a.builder != GENERIC_MERCHANT and a.builder not in target:
        raise SystemExit(f'--builder {a.builder:#x} is not in {a.archive}')
    stock = stock_twins.stock_bodies(replace + carry)
    for u in replace:
        if u in stock and stock[u][2] != source[u]['body']:
            raise SystemExit(f"{source[u]['name']}: the archive's copy differs from AC3's and AC3's archives disagree")
    datapc_other = [source[u]['name'] for u in carry if u in stock and stock[u][2] != source[u]['body']]
    acr = {}
    for p in load('ACR', mapwalk.archive_path('ACR', a.acr_archive or a.archive)):
        for o in p.objects:
            if (o['name'] or '').startswith(a.stalls):
                acr[o['name']] = o['body']
    used = set(target) | set(source)
    lo, hi = (a.uid_base, a.uid_base + a.uid_limit)
    for p in payloads:
        d, top = (p.data, struct.pack('<H', lo >> 16))
        k = d.find(top, 2)
        while k >= 0:
            u = struct.unpack_from('<I', d, k - 2)[0]
            if lo <= u < hi:
                used.add(u)
            k = d.find(top, k + 1)
    nxt = [a.uid_base]

    def alloc():
        while nxt[0] in used:
            nxt[0] += 1
        if nxt[0] >= a.uid_base + a.uid_limit:
            raise SystemExit('uid range exhausted')
        used.add(nxt[0])
        return nxt[0]
    stalls, fresh = ({}, [])
    for p in payloads:
        for o in p.objects:
            if (o['name'] or '').startswith(a.stalls) and xrefs.cls('AC3', o['type']) == 'Entity':
                if o['name'] not in acr:
                    raise SystemExit(f"{o['name']}: not in ACR's {a.archive}")
                body, mapping = rebuild(tmpl, tmpl_world, acr[o['name']], o['body'], world, a.builder, alloc)
                stalls[id(o)] = body
                fresh += [v for k, v in mapping.items() if k != own_t]
    if len(fresh) != len(set(fresh)):
        raise SystemExit('a fresh uid was handed out twice')
    by_uid = {u: source[u] for u in carry + replace}
    counts = Counter()
    for p in payloads:
        is_root = p.rec.name == a.into
        touched = is_root or any((id(o) in stalls or (o['has_uid'] and o['uid'] in replace) for o in p.objects))
        if not touched:
            continue
        objs = []
        for o in p.objects:
            if id(o) in stalls:
                objs.append((o['serial'], mapwalk.object_bytes(o, stalls[id(o)]), o['filter']))
                counts['stalls rebuilt'] += 1
            elif o['has_uid'] and o['uid'] in replace:
                objs.append((o['serial'], mapwalk.object_bytes(o, by_uid[o['uid']]['body']), o['filter']))
                counts["definitions given AC3's bytes"] += 1
            else:
                objs.append((o['serial'], mapwalk.object_bytes(o), o['filter']))
        if is_root:
            _n, listed, _m = cell_list(p.objects[0]['body'])
            g = list_add(p.objects[0]['body'], [u for u in carry if u not in listed])
            objs[0] = (p.objects[0]['serial'], mapwalk.object_bytes(p.objects[0], g), p.objects[0]['filter'])
            objs += [(source[u]['serial'], mapwalk.object_bytes(source[u]), None) for u in carry]
            counts['carried into the root cell'] += len(carry)
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    after = load('AC3', dest)
    defs = {}
    for p in after:
        for o in p.objects:
            if o['has_uid']:
                defs.setdefault(o['uid'], []).append((p.rec.name, o['body']))
    root = next((p for p in after if p.rec.name == a.into))
    _n, listed, _m = cell_list(root.objects[0]['body'])
    v = Counter()
    v["carried: once, in the root, listed, AC3's bytes"] = sum((1 for u in carry if [r for r, _b in defs.get(u, [])].count(a.into) == 1 and u in listed and (next((b for r, b in defs[u] if r == a.into)) == source[u]['body']) and all((r == a.into or (always_names is not None and r not in always_names) for r, _b in defs[u])) and all((b == source[u]['body'] for r, b in defs[u] if loaded_names is not None and r in loaded_names))))
    v["replaced: only AC3's bytes"] = sum((1 for u in replace if all((b == source[u]['body'] for _r, b in defs[u]))))
    b_names = b'\x01\x00' + struct.pack('<I', a.builder) + b'\x00' + struct.pack('<I', a.builder)
    for p in after:
        for o in p.objects:
            if (o['name'] or '').startswith(a.stalls) and xrefs.cls('AC3', o['type']) == 'Entity':
                b = o['body']
                s1, g1, s2, g2 = blocks(b)
                merchant, blend = acr_slots(acr[o['name']])
                k = b.find(STATE)
                ok = b.count(b_names) == 1 and b[s1:s1 + 32] == merchant == b[g1:g1 + 32] and all((b[s2[i]:s2[i] + 32] == blend[i] == b[g2[i]:g2[i] + 32] for i in range(7))) and (b[k + 8:k + 72] == transposed(b[10:74])) and (struct.pack('<I', world) in b)
                v['stalls: builder, slots, state matrix, World'] += ok
    holders = Counter()
    fresh_set = set(fresh)
    for p in after:
        for o in p.objects:
            holders.update(_held(o['body'], fresh_set))
    v['fresh uids: each in exactly one object'] = sum((1 for u in fresh if holders[u] == 1))
    with Forge(dest) as f, Forge(src) as g:
        changed = {p.rec.index for p in payloads if p.rec.name == a.into or any((id(o) in stalls or (o['has_uid'] and o['uid'] in replace) for o in p.objects))}
        v['untouched files byte-identical'] = sum((1 for x, y in zip(f.records, g.records) if x.index not in changed and f.read(x) == g.read(y)))
        untouched = sum((1 for x in g.records if x.index not in changed))
    ok = v["carried: once, in the root, listed, AC3's bytes"] == len(carry) and v["replaced: only AC3's bytes"] == len(replace) and (v['stalls: builder, slots, state matrix, World'] == len(stalls) == len(acr)) and (v['fresh uids: each in exactly one object'] == len(fresh)) and (v['untouched files byte-identical'] == untouched)
    unloaded_twins = [u for u in carry if u in target]
    unloaded_other = [source[u]['name'] for u in unloaded_twins if any((o['body'] != source[u]['body'] for _p, o in target[u]))]
    report = [f'present = {a.present}' + (f": {len(unloaded_twins)} carried objects also in unloaded files ({', '.join((source[u]['name'] for u in unloaded_twins[:12]))}{(' ...' if len(unloaded_twins) > 12 else '')}); of them with other bytes there (left, unloaded): {unloaded_other}" if reach is not None else ''), f"from {a.source}: {len(carry)} objects carried into {a.into}, {len(replace)} definitions given AC3's bytes ({', '.join((source[u]['name'] for u in replace))}), {same} already identical; {Counter((xrefs.cls('AC3', source[u]['type']) for u in carry)).most_common()}", f"carried with Mill's bytes where DataPC's differ (as stock Mill): {datapc_other}", f'{len(stalls)} stalls on {a.template}, builder {a.builder:#010x}; fresh uids {len(fresh)} ' + (f'({min(fresh):#010x}..{max(fresh):#010x})' if fresh else "(no stall: the merchant alone, Palazzio's performers)"), f"verify: {dict(v)} of {untouched} untouched files: {('OK' if ok else 'FAILED')}"]
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
