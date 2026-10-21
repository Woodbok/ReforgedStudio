"""forgelib/npc_port.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, 'maps')]
import exe_registry
import mapwalk
import paths
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, add_file, set_payload, write
OUT = os.path.join(HERE, 'out', 'merchants')
SOUK = os.path.join(HERE, 'out', 'maps', 'GA12_souk', 'DataPC_ACFE_Souk.forge')
MILL = 'DataPC_AC3MP_Boston_Mill.forge'
SELECTOR, GENERIC_MERCHANT = (333838149, 3518325269)
BODY_A, BODY_B = (333838310, 333838311)
SELECTOR_FILE = 'ACFE_CF_Merchants_Turkish'
SELECTOR_CELL_OBJECTS = ('ACFE_CF_Merchants_Turkish', 'Foley_Citizen')
MERCHANT_FILES = ('ACFE_CF_Merchants_m_A_Turkish', 'ACFE_CF_Merchants_M_B_Turkish', 'CF_Merchants_m_A_Hats', 'CF_Merchants_m_A_Acessories', 'CF_Merchants_m_A_Sleeves', 'CF_Merchants_m_A_Tiling', 'CF_Merchants_M_B_Sleeves', 'AC2MP_CF_Merchants_M_B_Acessories', 'AC2MP_M_Head_Average', 'AC2MP_M_Head_Fat', 'ACFE_Characters_Eye')

def payloads(path):
    out = {}
    with Forge(path) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            raw = f.read(r)
            try:
                out.setdefault(r.name, (r, mapwalk.Payload(r, raw), raw))
            except Exception:
                continue
    return out

def defined(files):
    out = {}
    for name, (_r, p, _raw) in files.items():
        for o in p.objects:
            if o['has_uid']:
                out.setdefault(o['uid'], (name, o))
    return out

def selector_file(souk):
    for name, (_r, p, _raw) in souk.items():
        objs = [o for o in p.objects if o['name'] in SELECTOR_CELL_OBJECTS and o['has_uid']]
        if any((o['uid'] == SELECTOR for o in objs)):
            objs.sort(key=lambda o: o['uid'] != SELECTOR)
            assert [o['name'] for o in objs] == list(SELECTOR_CELL_OBJECTS), [o['name'] for o in objs]
            d = mapwalk.rebuild([(o['serial'], mapwalk.object_bytes(o), None) for o in objs])
            return (name, d)
    raise SystemExit(f'no Souk file defines the selector 0x{SELECTOR:08X}')

def mill_test(build, log=print):
    classes, _ = exe_registry.load(exe_registry.AC3)
    src = os.path.join(paths.AC3_SOURCE, MILL)
    souk, mill = (payloads(SOUK), payloads(src))
    mill_defs = defined(mill)
    missing = [n for n in MERCHANT_FILES if n not in souk]
    if missing:
        raise SystemExit(f'Souk ({SOUK}) lacks {missing}')
    cell, _sel_plain = selector_file(souk)
    from collections import Counter
    carried, dropped = ({}, Counter())
    sources = {n: souk[n][1].objects for n in MERCHANT_FILES}
    sources[SELECTOR_FILE] = [o for o in souk[cell][1].objects if o['name'] in SELECTOR_CELL_OBJECTS and o['has_uid']]
    import make_jester_79 as g2
    import inventory
    mill_clips = {g2.clip_id(o['body']) for _r, p, _raw in mill.values() for o in p.objects if o['body'][:2] == b'\x01\x02'}
    uni = inventory.universe('AC3')
    shared_ids = {u for u, homes in uni.items() if any((h[0] in inventory.SHARED for h in homes))}
    shared_clips = set(g2.all_directory_values(os.path.join(paths.AC3_SOURCE, 'DataPC.forge')))
    for n, objs in sources.items():
        keep = []
        for o in objs:
            if o['has_uid'] and (o['uid'] in mill_defs or o['uid'] in shared_ids) or (o['body'][:2] == b'\x01\x02' and (g2.clip_id(o['body']) in mill_clips or g2.clip_id(o['body']) in shared_clips)):
                dropped[classes.get(o['type'], hex(o['type']))] += 1
            else:
                keep.append(o)
        if not keep or keep[0] is not objs[0]:
            log(f"  {n}: its root is AC3's already - not carried ({len(keep)} other objects {[o['name'] for o in keep][:4]})")
            assert not keep, f"{n}: its root is AC3's but it has objects of its own"
            continue
        carried[n] = keep
    log(f"carried: {len(carried)} files, {sum((len(v) for v in carried.values()))} objects; {sum(dropped.values())} left to Mill's own copies {dict(dropped.most_common(8))}")
    import library_cell
    roots = [objs[0]['uid'] for objs in carried.values()]
    image = ArchiveImage.read(src)
    old = struct.pack('<I', GENERIC_MERCHANT)
    rewired, listed = ([], [])
    for name, (rec, p, _raw) in mill.items():
        changed = False
        objs = []
        stall_here = any((o['has_uid'] and o['uid'] != GENERIC_MERCHANT and (old in o['body']) and (o['name'] or '').startswith('AC3MP_GEN_Merchant') for o in p.objects))
        for o in p.objects:
            body = o['body']
            if o['has_uid'] and o['uid'] != GENERIC_MERCHANT and (old in body) and (o['name'] or '').startswith('AC3MP_GEN_Merchant'):
                n = body.count(old)
                assert n % 2 == 0, (o['name'], n)
                target = BODY_B if '_Meat_' in o['name'] else SELECTOR
                body = body.replace(old, struct.pack('<I', target))
                rewired.append(f"{o['name']} -> 0x{target:08X} ({n // 2} slot{('s' if n > 2 else '')})")
                changed = True
            elif stall_here and o is p.objects[0]:
                assert classes.get(o['type']) == 'GridCellDataBlock', (name, o['name'])
                _n, have, _m = library_cell.cell_list(body)
                new_roots = [u for u in roots if u not in have]
                body = library_cell.list_add(body, new_roots)
                listed.append(f'{name} +{len(new_roots)}')
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        if changed:
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, rec.index, payload)
    if not rewired:
        raise SystemExit('no Mill merchant stall names the Generic_Merchant builder')
    log(f'stalls rewired to the Turkish merchant: {len(rewired)} {rewired}')
    log(f'cells listing the merchant files: {listed}')
    import clone
    template = next((r.index for n, (r, _p, _raw) in mill.items() if n.startswith('Cell')))
    record_uids = clone.fresh_record_uids({r.uid for r, _p, _raw in mill.values()}, 331875500872)
    for n, objs in carried.items():
        d = mapwalk.rebuild([(o['serial'], mapwalk.object_bytes(o), o['filter']) for o in objs])
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        add_file(image, n, payload, next(record_uids), template_index=template, entry_key=objs[0]['uid'])
    dest_dir = os.path.join(OUT, build)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, MILL)
    size = write(dest, image)
    log(f'-> {dest} ({size:,} bytes): stock Mill + {len(carried)} merchant files')
    return dest
MERCHANT_CELL = 'Cell00029_DataBlock'
MAIN_TABLE, HATS_TABLE = (3518326768, 3518328136)
TRANSPLANT = {3588593384: 1065713419, 3588592558: 3044898144, 3588592170: 1596753410, 3588592899: 1065713418}
STALL_ROUTE = {'AC3MP_GEN_Merchant_014': None, 'AC3MP_GEN_Merchant_013': 'copy', 'AC3MP_GEN_Merchant_Meat_001': 'transplant', 'AC3MP_GEN_Merchant_Meat_002': 'transplant'}

def copy_builder(mill_defs, fresh, classes, parts, label):
    import own_entity
    objs = [dict(mill_defs[u][1]) for u in (GENERIC_MERCHANT, MAIN_TABLE, HATS_TABLE)]
    mapping = {}
    for o in objs:
        for u in own_entity.inline_uids(o['body'], classes):
            mapping.setdefault(u, fresh.take())
    out = []
    for o in objs:
        b = o['body']
        for old, new in list(mapping.items()) + list(parts.items()):
            b = b.replace(struct.pack('<I', old), struct.pack('<I', new))
        o = dict(o, body=b, uid=mapping[o['uid']], name=f"{label}_{o['name']}", serial=mapping[o['uid']])
        out.append(o)
    return (out[0]['uid'], out)
SKELETONS = (2938561726, 1985774593)

def skeleton_donor(uid):
    for arc in sorted(mapwalk.map_archives('ACR')):
        for p in mapwalk.payloads('ACR', arc, skip_errors=True):
            for o in p.objects:
                if o['has_uid'] and o['uid'] == uid:
                    return (arc, o)
    raise SystemExit(f'add-on skeleton 0x{uid:08X}: in no ACR map archive')
BUILD_COLUMN, BUILD_ROW = (914593288, 881535190)
REF_CELL = bytes.fromhex('00001c000100')

def add_columns(body, template_col, new, fresh):
    table = struct.unpack_from('<I', body, 2)[0]
    key = b'\x01\x00' + struct.pack('<I', template_col) + b'\x01' + struct.pack('<I', table)
    assert body.count(key) == 1, 'template column not found once'
    start = body.find(key) - 24
    col = body[start:start + 57]
    assert struct.unpack_from('<I', col, 4)[0] == BUILD_COLUMN and col[-10:] == bytes.fromhex('00001c00030000000000')
    rows = [i for i in range(len(body) - 4) if struct.unpack_from('<I', body, i)[0] == BUILD_ROW]
    assert len(rows) == 1, 'one row expected'
    r = rows[0]
    rows_at = r - 8
    assert struct.unpack_from('<I', body, rows_at)[0] == 1
    ncols = struct.unpack_from('<I', body, 10)[0]
    ncells = struct.unpack_from('<I', body, r + 17)[0]
    cells_end = r + 21 + 18 * ncells
    cols, cells = (b'', b'')
    for cid, cls, ref in new:
        c = bytearray(col)
        struct.pack_into('<I', c, 0, fresh.take())
        struct.pack_into('<I', c, 26, cid)
        struct.pack_into('<I', c, 43, cls)
        cols += bytes(c)
        cells += struct.pack('<II', cid, cls) + REF_CELL + struct.pack('<I', ref)
    b = bytearray(body[:rows_at] + cols + body[rows_at:cells_end] + cells + body[cells_end:])
    struct.pack_into('<I', b, 10, ncols + len(new))
    struct.pack_into('<I', b, r + len(cols) + 17, ncells + len(new))
    return bytes(b)
HEAD_TABLE, CROWD_HEADS = (184853339, 3518327888)
CELL_PAYLOAD = {28: 6, 13: 16, 10: 4, 25: 4}

def table_rows(body):
    out, i = ([], body.find(struct.pack('<I', BUILD_ROW)))
    while i >= 0:
        n = struct.unpack_from('<I', body, i + 17)[0]
        p, cells = (i + 21, [])
        for _ in range(n):
            kind = body[p + 10]
            assert body[p + 8:p + 10] == b'\x00\x00' and body[p + 11] == 0 and (kind in CELL_PAYLOAD), body[p:p + 16].hex()
            ln = 12 + CELL_PAYLOAD[kind]
            cells.append((p, ln, struct.unpack_from('<I', body, p)[0]))
            p += ln
        out.append((i - 4, i + 17, cells))
        i = body.find(struct.pack('<I', BUILD_ROW), p)
    return out

def keep_cells(body, keep):
    b = body
    for _row, count_at, cells in reversed(table_rows(body)):
        kept = [c for c in cells if c[2] in keep]
        for p, ln, _col in reversed([c for c in cells if c[2] not in keep]):
            b = b[:p] + b[p + ln:]
        b = b[:count_at] + struct.pack('<I', len(kept)) + b[count_at + 4:]
    return b

def own_table(obj, fresh, classes, name):
    import own_entity
    mapping = {u: fresh.take() for u in own_entity.inline_uids(obj['body'], classes)}
    b = obj['body']
    for old, new in mapping.items():
        b = b.replace(struct.pack('<I', old), struct.pack('<I', new))
    return dict(obj, body=b, uid=mapping[obj['uid']], serial=mapping[obj['uid']], name=name)
TRANSPLANT_FAT = {3588593384: 1065713427, 3588592558: 3044898208, 3588592170: 1596753399, 3588592899: 1065713418}
HEAD_TABLE_FAT = 184854594
FAT_ROUTE = {'AC3MP_GEN_Merchant_014': 'transplant', 'AC3MP_GEN_Merchant_013': 'fat', 'AC3MP_GEN_Merchant_Meat_001': 'selector', 'AC3MP_GEN_Merchant_Meat_002': 'selector'}
ACC = {'hats': 1354580816, 'acc_A': 1354580818, 'sleeves_A': 1354580819, 'acc_B': 359864499, 'sleeves_B': 1354581336}
FAT_ANIMSET = 837309417
VARIETY = {'A': {26: 3094122882, 27: 1354580913, 28: 1164821530, 29: 672958758, 30: 1562511463}, 'B': {26: 1354580935, 27: 1354580913, 28: 1164821530, 29: 672958758}}

def replace_u32s(body, mapping):
    for old, new in mapping.items():
        body = body.replace(struct.pack('<I', old), struct.pack('<I', new))
    return body
REF_PREFIXES = (b'\x01\x00', b'\x01\x01', b'\x01\x02', b'\x00\x01')

def id_sites(body, u, classes=None):
    import own_entity
    pat, out = (struct.pack('<I', u), [])
    i = body.find(pat)
    while i >= 0:
        if i == 2 or body[i - 2:i] in REF_PREFIXES or (classes is not None and i + 8 <= len(body) and own_entity.is_class_id(struct.unpack_from('<I', body, i + 4)[0], classes)):
            out.append(i)
        i = body.find(pat, i + 1)
    return out

def diff_windows(a, b, block=4096):
    for base in range(0, len(a), block):
        if a[base:base + block] != b[base:base + block]:
            yield from range(max(0, base - 3), min(len(a), base + block))

def replace_refs(body, mapping, classes=None):
    b = bytearray(body)
    for old, new in mapping.items():
        for i in id_sites(body, old, classes):
            struct.pack_into('<I', b, i, new)
    return bytes(b)

def selector_copy(sel, builders, fresh, classes):
    b = sel['body']
    tail = b'\x01\x00\x00\x00\x01\x00' + struct.pack('<I', 2744156443)
    assert b.endswith(tail), b[-16:].hex()
    b = b[:-len(tail)] + b'\x00\x00\x00\x00'
    for old in builders:
        assert b.count(struct.pack('<I', old)) == 1
    o = own_table(dict(sel, body=replace_u32s(b, builders), body_size=sel['body_size'] + len(b) - len(sel['body'])), fresh, classes, f"ACFE_Turkish_{sel['name']}")
    assert all((struct.pack('<I', v) in o['body'] for v in builders.values()))
    return o

def part_closure(souk_defs, roots, skip):
    import library_cell
    need, todo = ([], list(roots))
    while todo:
        u = todo.pop()
        if u in skip or u not in souk_defs or u in need:
            continue
        need.append(u)
        todo += library_cell.refs(souk_defs[u][1]['body'])
    return need
ALWAYS_LOADED = ('DataPC_CoreFX.forge',)
ENGINE_UIDS = 65536

def always_available(uni):
    return {u for u, h in uni.items() if u < ENGINE_UIDS or any((x[0] in ALWAYS_LOADED for x in h))}

def could_be_id(u):
    return u >= 65536 and bool(u & 65535)

def renew_body(o, mapping):
    global _CLASSES
    if _CLASSES is None:
        _CLASSES = exe_registry.load(exe_registry.AC3)[0]
    if _CLASSES.get(o['type']) in ('Mesh', 'TextureMap'):
        return replace_refs(o['body'], mapping)
    return replace_u32s(o['body'], mapping)
_CLASSES = None

def own_copies(objs, elsewhere, fresh, log=print, classes=None):
    renew = {o['uid']: fresh.take() for o in objs if o['uid'] in elsewhere}
    if classes is not None:
        import own_entity
        skeleton = next((k for k, v in classes.items() if v == 'Skeleton'))
        for o in objs:
            if o['type'] == skeleton:
                for u in [u for u in own_entity.inline_uids(o['body'], classes) if u != o['uid'] and could_be_id(u)]:
                    renew.setdefault(u, fresh.take())
    out = []
    for o in objs:
        b = renew_body(o, renew)
        hits = [f"0x{old:08X} x{o['body'].count(struct.pack('<I', old))}" for old in renew if struct.pack('<I', old) in o['body']]
        if o['uid'] in renew:
            assert b[:2] == b'\x00\x01' and struct.unpack_from('<I', b, 2)[0] == renew[o['uid']], o['name']
            o = dict(o, uid=renew[o['uid']], serial=renew[o['uid']])
        if hits:
            log(f"   {o['name']}: repointed {', '.join(hits[:6])}" + (f' ... ({len(hits)} ids)' if len(hits) > 6 else ''))
        out.append(dict(o, body=b))
    return (out, renew)

def check_closure(parts, target_defs, uni, classes, log=print):
    import library_cell
    have = {o['uid'] for o in parts} | set(target_defs) | always_available(uni)
    bad = sorted({(o['name'], r) for o in parts if classes.get(o['type']) != 'Animation' for r in library_cell.refs(o['body']) if r in uni and r not in have})
    for name, r in bad:
        log(f'   UNRESOLVED in the map: {name} -> 0x{r:08X} {uni[r][0][3]} ({uni[r][0][0]}/{uni[r][0][1]})')
    assert not bad, f'{len(bad)} references left outside the map'
    log(f'closure: {len(parts)} carried objects name nothing outside Mill / CoreFX / engine uids')
RAW_DATA = ('Mesh', 'TextureMap', 'Animation')
BULK = RAW_DATA + ('MaterialTemplate',)
_VALID_CLASSES = {}
_HEADER_VALUES = {}

def _valid_classes(classes):
    import numpy as np
    import own_entity
    k = id(classes)
    if k not in _VALID_CLASSES:
        _VALID_CLASSES[k] = np.array(sorted((c for c in classes if own_entity.is_class_id(c, classes))), dtype=np.uint32)
    return _VALID_CLASSES[k]

def _member(a, valid):
    import numpy as np
    hit = _MEMBER_TABLES.get(id(valid))
    if hit is None or hit[0] is not valid:
        top = np.zeros(1 << 16, dtype=bool)
        top[valid >> 16] = True
        hit = _MEMBER_TABLES[id(valid)] = (valid, top)
    top = hit[1]
    out = np.zeros(len(a), dtype=bool)
    idx = np.flatnonzero(top[a >> 16])
    if idx.size and len(valid):
        v = a[idx]
        out[idx] = valid[np.minimum(np.searchsorted(valid, v), len(valid) - 1)] == v
    return out
_MEMBER_TABLES = {}

def _header_pairs(blob, valid):
    import numpy as np
    out = []
    for k in range(4):
        n = (len(blob) - k) // 4
        if n < 2:
            continue
        a = np.frombuffer(blob, dtype='<u4', count=n, offset=k)
        for j in np.nonzero(_member(a[1:], valid))[0].tolist():
            out.append((k + 4 * j, int(a[j]), int(a[j + 1])))
    out.sort()
    return out

def _header_values(target, classes):
    import numpy as np
    key = (id(target), len(target))
    if key not in _HEADER_VALUES:
        valid = _valid_classes(classes)
        vals = set()
        for _name, (_r, p, _raw) in target.items():
            blob = b''.join((o['body'] for o in p.objects))
            for k in range(4):
                n = (len(blob) - k) // 4
                if n < 2:
                    continue
                a = np.frombuffer(blob, dtype='<u4', count=n, offset=k)
                vals.update(np.unique(a[:-1][_member(a[1:], valid)]).tolist())
        _HEADER_VALUES[key] = vals
    return _HEADER_VALUES[key]

def nested_clashes(added, target, classes):
    import clone
    valid = _valid_classes(classes)
    ids = {}
    for o in added:
        if classes.get(o['type']) in RAW_DATA:
            continue
        for _p, u, c in _header_pairs(o['body'], valid):
            if u >= 65536 and u & 65535:
                ids.setdefault(u, (o['name'], c))
    nested_cls = {c for _n, c in ids.values()}
    hits = {}
    for arch in (paths.DEFAULT_DATAPC, paths.DEFAULT_EXTRA):
        index = clone.nested_index(arch, nested_cls)
        for u in ids:
            if index.get(u):
                hits.setdefault(u, set()).update((f'{os.path.basename(arch)}/{n}' for n in index[u]))
    stock_shared = set(ids) & _header_values(target, classes)
    return (ids, {u: w for u, w in hits.items() if u not in stock_shared}, stock_shared)

def _nested_clashes_bytewise(added, target, classes):
    import clone
    import own_entity
    ids = {}
    for o in added:
        if classes.get(o['type']) in RAW_DATA:
            continue
        b = o['body']
        for p in range(0, len(b) - 7):
            u, c = struct.unpack_from('<II', b, p)
            if u >= 65536 and u & 65535 and own_entity.is_class_id(c, classes):
                ids.setdefault(u, (o['name'], c))
    nested_cls = {c for _n, c in ids.values()}
    hits = {}
    for arch in (paths.DEFAULT_DATAPC, paths.DEFAULT_EXTRA):
        index = clone.nested_index(arch, nested_cls)
        for u in ids:
            if index.get(u):
                hits.setdefault(u, set()).update((f'{os.path.basename(arch)}/{n}' for n in index[u]))
    stock_shared = set()
    for _name, (_r, p, _raw) in target.items():
        blob = b''.join((o['body'] for o in p.objects))
        for u in ids.keys() - stock_shared:
            nb, i = (struct.pack('<I', u), blob.find(struct.pack('<I', u)))
            while i >= 0:
                if i + 8 <= len(blob) and own_entity.is_class_id(struct.unpack_from('<I', blob, i + 4)[0], classes):
                    stock_shared.add(u)
                    break
                i = blob.find(nb, i + 1)
    return (ids, {u: w for u, w in hits.items() if u not in stock_shared}, stock_shared)

def check_nested(added, target, classes, log=print):
    ids, bad, stock_shared = nested_clashes(added, target, classes)
    persona_shared = {u for u, w in bad.items() if len(w) >= 2}
    bad = {u: w for u, w in bad.items() if u not in persona_shared}
    stock_shared = set(stock_shared) | persona_shared
    for u, where in sorted(bad.items()):
        log(f'   SHARED with the default set: 0x{u:08X} ({ids[u][0]}) -> {sorted(where)[:3]}')
    assert not bad, f'{len(bad)} added uids are also object uids in the default set'
    kept = sorted(stock_shared)
    log(f"nested: {len(ids)} object uids added (own + nested); none in the default set but {len(kept)} the stock map's own files or the default set's share already (" + ', '.join((f'0x{u:08X} {ids[u][0]}' for u in kept[:6])) + ('...' if len(kept) > 6 else '') + ')')

def twin_clips(clips, ids, loaded, log=print):
    from collections import Counter
    carried = {o['serial']: o['body'] for o in clips}
    twins = Counter()
    for n, (_r, p, _raw) in loaded.items():
        for o in p.objects:
            if o['serial'] in ids:
                if o['body'] != carried.get(o['serial']):
                    raise SystemExit(f"clip {o['name']} (id 0x{o['serial']:08X}) is loaded from {n} with other bytes than the copy carried")
                twins[n] += 1
    if twins:
        log(f'clips also loaded from {dict(twins)}: byte-identical twins of the copies carried into the root')

def build_merchant(mill_defs, souk, souk_defs, mill, uni, fresh, classes, own=True, rig=True, heads=True, fat=True, acc=True, variety=True, log=print, available=None, closure_defs=None):
    import inventory
    import own_entity
    shared = {u for u, h in uni.items() if any((x[0] in inventory.SHARED for x in h))}
    available_in = available
    copy_uid, copy_objs = copy_builder(mill_defs, fresh, classes, {}, 'Control')
    tp_uid, tp_objs = copy_builder(mill_defs, fresh, classes, TRANSPLANT, 'ACFE_Turkish')
    if own:
        available = set(mill_defs) | always_available(uni) if available_in is None else set(available_in)
        need = part_closure(souk_defs, list(TRANSPLANT.values()), available)
        parts, renew = own_copies([souk_defs[u][1] for u in need], set(uni), fresh, log)
        log(f'own copies (AC3 defines them only outside Mill): ' + ', '.join((f"{souk_defs[k][1]['name']} 0x{k:08X}->0x{v:08X}" for k, v in renew.items())))
    else:
        need = part_closure(souk_defs, list(TRANSPLANT.values()), set(mill_defs) | shared)
        parts = [souk_defs[u][1] for u in need]
    from collections import Counter
    log(f"transplant parts from Souk: {len(parts)} objects {dict(Counter((classes.get(o['type'], hex(o['type'])) for o in parts)))}")
    missing = [hex(v) for v in TRANSPLANT.values() if v not in need]
    assert not missing, f'part LODSelectors not carried: {missing}'
    if rig:
        assert own
        sk_need = part_closure(souk_defs, list(SKELETONS), available | set(need))
        sk_objs, sk_renew = own_copies([souk_defs[u][1] for u in sk_need], set(uni), fresh, log, classes)
        parts += sk_objs
        skeleton_cls = next((k for k, v in classes.items() if v == 'Skeleton'))
        cols = [(12 + k, skeleton_cls, sk_renew.get(u, u)) for k, u in enumerate(SKELETONS)]
        main = tp_objs[1]
        nb = add_columns(main['body'], 11, cols, fresh)
        tp_objs[1] = dict(main, body=nb, body_size=main['body_size'] + len(nb) - len(main['body']))
        log(f"rig: {main['name']} + Skeleton columns " + ', '.join((f"{c} -> {souk_defs[u][1]['name']} 0x{r:08X}" for (c, _k, r), u in zip(cols, SKELETONS))) + f' ({len(sk_objs)} objects carried)')
    if heads:
        assert rig
        table_cls = next((k for k, v in classes.items() if v == 'BuildTable'))
        hd_need = part_closure(souk_defs, [HEAD_TABLE], available | set(need) | set(sk_need))
        hd_parts = [souk_defs[u][1] for u in hd_need if u != HEAD_TABLE]
        hd_objs, hd_renew = own_copies(hd_parts, set(uni), fresh, log, classes)
        _ids, clash, _st = nested_clashes(hd_objs, mill, classes)
        more = {u: fresh.take() for u in sorted(clash)}
        for k, o in enumerate(hd_objs):
            hd_objs[k] = dict(o, body=renew_body(o, more))
        hd_renew.update(more)
        log(f'heads: {len(more)} nested ids renewed (the default set defines them too: ' + ', '.join(sorted({_ids[u][0] for u in more})) + ')')
        head = souk_defs[HEAD_TABLE][1]
        head_body = head['body']
        for old, new in hd_renew.items():
            head_body = head_body.replace(struct.pack('<I', old), struct.pack('<I', new))
        head_copy = own_table(dict(head, body=head_body), fresh, classes, f"ACFE_Turkish_{head['name']}")
        voice = mill_defs[CROWD_HEADS][1]
        voice_copy = own_table(voice, fresh, classes, f"ACFE_Turkish_{voice['name']}_Voices")
        vb = keep_cells(voice_copy['body'], {4})
        voice_copy = dict(voice_copy, body=vb, body_size=voice_copy['body_size'] + len(vb) - len(voice_copy['body']))
        builder = tp_objs[0]
        bb = builder['body']
        assert bb.count(struct.pack('<I', CROWD_HEADS)) == 1
        bb = bb.replace(struct.pack('<I', CROWD_HEADS), struct.pack('<I', voice_copy['uid']))
        bb = add_columns(bb, 22, [(23, table_cls, head_copy['uid'])], fresh)
        tp_objs[0] = dict(builder, body=bb, body_size=builder['body_size'] + len(bb) - len(builder['body']))
        parts += hd_objs + [head_copy, voice_copy]
        log(f"heads: {builder['name']} col 21 -> {voice_copy['name']} 0x{voice_copy['uid']:08X} (voice cells only), + col 23 -> {head_copy['name']} 0x{head_copy['uid']:08X} ({len(hd_objs)} head objects)")
        for rs, _c, cells in table_rows(head_copy['body']):
            log('   ACR head row: ' + ', '.join((f'c{c}' for _p, _l, c in cells)))
    selector_uid = fat_uid = None
    if fat:
        assert heads
        fat_uid, fat_objs = copy_builder(mill_defs, fresh, classes, TRANSPLANT_FAT, 'ACFE_Turkish_Fat')
        prior = {**renew, **sk_renew, **hd_renew}
        carried = set(need) | set(sk_need) | set(hd_need) | {HEAD_TABLE}
        fb_need = part_closure(souk_defs, list(TRANSPLANT_FAT.values()) + [HEAD_TABLE_FAT], available | carried)
        fb_objs, fb_renew = own_copies([souk_defs[u][1] for u in fb_need if u != HEAD_TABLE_FAT], set(uni), fresh, log, classes)
        _ids, clash, _st = nested_clashes(fb_objs, mill, classes)
        fb_renew.update({u: fresh.take() for u in sorted(clash)})
        mapping = {**prior, **fb_renew}
        fb_objs = [dict(o, body=renew_body(o, mapping)) for o in fb_objs]
        head = souk_defs[HEAD_TABLE_FAT][1]
        fat_head = own_table(dict(head, body=replace_u32s(head['body'], mapping)), fresh, classes, f"ACFE_Turkish_{head['name']}")
        nb = add_columns(fat_objs[1]['body'], 11, cols, fresh)
        fat_objs[1] = dict(fat_objs[1], body=nb, body_size=fat_objs[1]['body_size'] + len(nb) - len(fat_objs[1]['body']))
        bb = fat_objs[0]['body']
        assert bb.count(struct.pack('<I', CROWD_HEADS)) == 1
        bb = bb.replace(struct.pack('<I', CROWD_HEADS), struct.pack('<I', voice_copy['uid']))
        bb = add_columns(bb, 22, [(23, table_cls, fat_head['uid'])], fresh)
        fat_objs[0] = dict(fat_objs[0], body=bb, body_size=fat_objs[0]['body_size'] + len(bb) - len(fat_objs[0]['body']))
        sel = selector_copy(souk_defs[SELECTOR][1], {BODY_A: tp_uid, BODY_B: fat_uid}, fresh, classes)
        selector_uid = sel['uid']
        tp_objs += fat_objs + [sel]
        parts += fb_objs + [fat_head]
        log(f"fat: builder 0x{fat_uid:08X} ({len(fb_objs)} objects carried, {len(clash)} nested ids renewed; {fat_head['name']} 0x{fat_head['uid']:08X}); selector 0x{selector_uid:08X}: A 0x{tp_uid:08X} / B 0x{fat_uid:08X}")
    if acc:
        assert fat
        prior = {**renew, **sk_renew, **hd_renew, **fb_renew}
        carried = set(need) | set(sk_need) | set(hd_need) | set(fb_need) | {HEAD_TABLE, HEAD_TABLE_FAT}
        roots = list(ACC.values()) + [FAT_ANIMSET]
        ac_need = part_closure(souk_defs, roots, available | carried)
        ac_objs, ac_renew = own_copies([souk_defs[u][1] for u in ac_need if u not in ACC.values()], set(uni), fresh, log, classes)
        _ids, clash, _st = nested_clashes(ac_objs, mill, classes)
        ac_renew.update({u: fresh.take() for u in sorted(clash)})
        mapping = {**prior, **ac_renew}
        ac_objs = [dict(o, body=renew_body(o, mapping)) for o in ac_objs]
        tables = {k: own_table(dict(souk_defs[u][1], body=replace_u32s(souk_defs[u][1]['body'], mapping)), fresh, classes, f"ACFE_Turkish_{souk_defs[u][1]['name']}") for k, u in ACC.items()}
        animset = mapping.get(FAT_ANIMSET, FAT_ANIMSET)
        clip_ids = {v[1] for v in own_entity.animset_slots(souk_defs[FAT_ANIMSET][1]['body']).values()}
        clips = [o for _n, (_r, p, _raw) in souk.items() for o in p.objects if o['serial'] in clip_ids]
        assert len(clips) == len(clip_ids), (len(clips), len(clip_ids))
        twin_clips(clips, clip_ids, mill, log)
        animset_cls = next((k for k, v in classes.items() if v == 'AnimSet'))
        bi = {o['uid']: k for k, o in enumerate(tp_objs)}
        for builder_uid, sfx, main_uid in ((tp_uid, 'A', tp_objs[1]['uid']), (fat_uid, 'B', fat_objs[1]['uid'])):
            k = bi[builder_uid]
            bb = tp_objs[k]['body']
            old_hats = [o['uid'] for o in tp_objs if o['name'].endswith('Merchants_Hats') and struct.pack('<I', o['uid']) in bb]
            assert len(old_hats) == 1
            bb = bb.replace(struct.pack('<I', old_hats[0]), struct.pack('<I', tables['hats']['uid']))
            bb = add_columns(bb, 22, [(24, table_cls, tables[f'acc_{sfx}']['uid']), (25, table_cls, tables[f'sleeves_{sfx}']['uid'])], fresh)
            tp_objs[k] = dict(tp_objs[k], body=bb, body_size=tp_objs[k]['body_size'] + len(bb) - len(tp_objs[k]['body']))
            if sfx == 'B':
                m = bi[main_uid]
                nb = add_columns(tp_objs[m]['body'], 11, [(14, animset_cls, animset)], fresh)
                tp_objs[m] = dict(tp_objs[m], body=nb, body_size=tp_objs[m]['body_size'] + len(nb) - len(tp_objs[m]['body']))
        tp_objs = [o for o in tp_objs if not o['name'].endswith('Merchants_Hats')]
        parts += ac_objs + list(tables.values()) + clips
        log(f'accessories: {len(ac_objs)} objects ({len(clash)} nested ids renewed), tables ' + ', '.join((f"{k} 0x{t['uid']:08X}" for k, t in tables.items())) + f"; B's Main + AnimSet 0x{animset:08X} ({len(clips)} clips: " + ', '.join(sorted((o['name'] for o in clips))) + ')')
    if variety:
        assert acc
        prior = {**prior, **ac_renew}
        carried |= set(ac_need) | set(ACC.values()) | {FAT_ANIMSET}
        roots = sorted({u for cols_ in VARIETY.values() for u in cols_.values()})
        vr_need = part_closure(souk_defs, roots, available | carried)
        vr_objs, vr_renew = own_copies([souk_defs[u][1] for u in vr_need if u not in roots], set(uni), fresh, log, classes)
        _ids, clash, _st = nested_clashes(vr_objs, mill, classes)
        vr_renew.update({u: fresh.take() for u in sorted(clash)})
        mapping = {**prior, **vr_renew}
        vr_objs = [dict(o, body=renew_body(o, mapping)) for o in vr_objs]
        vtables = {u: own_table(dict(souk_defs[u][1], body=replace_u32s(souk_defs[u][1]['body'], mapping)), fresh, classes, f"ACFE_Turkish_{souk_defs[u][1]['name']}") for u in roots}
        set_ids = {v[1] for o in vr_objs if classes.get(o['type']) == 'AnimSet' for v in own_entity.animset_slots(o['body']).values()}
        vclips = [o for _n, (_r, p, _raw) in souk.items() for o in p.objects if o['serial'] in set_ids]
        assert len({o['serial'] for o in vclips}) == len(set_ids), (len(vclips), len(set_ids))
        twin_clips(vclips, set_ids, mill, log)
        bi = {o['uid']: k for k, o in enumerate(tp_objs)}
        for builder_uid, sfx in ((tp_uid, 'A'), (fat_uid, 'B')):
            k = bi[builder_uid]
            bb = add_columns(tp_objs[k]['body'], 22, [(c, table_cls, vtables[u]['uid']) for c, u in sorted(VARIETY[sfx].items())], fresh)
            tp_objs[k] = dict(tp_objs[k], body=bb, body_size=tp_objs[k]['body_size'] + len(bb) - len(tp_objs[k]['body']))
        parts += vr_objs + list(vtables.values()) + vclips
        log(f'variety: {len(vr_objs)} objects ({len(clash)} nested ids renewed), tables ' + ', '.join((f"{t['name']} 0x{t['uid']:08X}" for t in vtables.values())) + f'; {len(vclips)} clips (' + ', '.join(sorted((o['name'] for o in vclips))) + ')')
    if own:
        check_closure(parts, mill_defs if closure_defs is None else closure_defs, uni, classes, log)
    written = (tp_objs if heads else copy_objs + tp_objs) + parts
    if rig:
        check_nested(written, mill, classes, log)
    return {'copy_uid': copy_uid, 'copy_objs': copy_objs, 'tp_uid': tp_uid, 'tp_objs': tp_objs, 'fat_uid': fat_uid, 'selector_uid': selector_uid, 'parts': parts, 'written': written}
HERALD_BODY, HERALD_HEADS, HERALD_COLORS = (348368141, 333838330, 348368282)
HERALD_VARIETY = {26: HERALD_COLORS, 28: 1164821530, 29: 672958758}
HERALD_DROP_BEARDS = True
HERALD_DIFFUSE_FROM_SLOT = True
HERALD_ONLY_HEAD = None
SLOT0 = struct.pack('<I', 0) + b'\x03\x00\x00\x00\x00\x00\x01\x00'

def diffuse_from_slot(objs, classes, log=print):
    by_uid = {o['uid']: k for k, o in enumerate(objs)}
    out = list(objs)
    for o in objs:
        b = o['body']
        if classes.get(o['type']) != 'Material' or b[16:18] != b'\x01\x00':
            continue
        tset, k = (struct.unpack_from('<I', b, 18)[0], b.find(SLOT0))
        if k < 0 or tset not in by_uid or classes.get(objs[by_uid[tset]]['type']) != 'TextureSet':
            continue
        slot = struct.unpack_from('<I', b, k + len(SLOT0))[0]
        s = out[by_uid[tset]]
        diff = struct.unpack_from('<I', s['body'], 12)[0]
        names = {x['uid']: x['name'] for x in objs}
        if slot != diff and names.get(slot, '').endswith('DiffuseMap') and names.get(diff, '').endswith('DiffuseMap'):
            assert s['body'][10:12] == b'\x01\x00', s['name']
            nb = s['body'][:12] + struct.pack('<I', slot) + s['body'][16:]
            out[by_uid[tset]] = dict(s, body=nb)
            log(f"diffuse from the slot: {o['name']}'s set {s['name']} diffuse {names[diff]} -> {names[slot]}")
    return out

def build_herald(mill_defs, souk, souk_defs, mill, uni, fresh, classes, log=print, available=None, closure_defs=None):
    from collections import Counter
    table_cls = next((k for k, v in classes.items() if v == 'BuildTable'))
    skeleton_cls = next((k for k, v in classes.items() if v == 'Skeleton'))
    available = set(available) if available is not None else set(mill_defs) | always_available(uni)
    b_uid, b_objs = copy_builder(mill_defs, fresh, classes, {}, 'ACFE_Herald')
    builder, main_copy, _hats_copy = b_objs

    def carry(roots, done, skip_roots=True):
        need = part_closure(souk_defs, roots, available | done)
        objs, renew = own_copies([souk_defs[u][1] for u in need if not (skip_roots and u in roots)], set(uni), fresh, log, classes)
        _ids, clash, _st = nested_clashes(objs, mill, classes)
        renew.update({u: fresh.take() for u in sorted(clash)})
        return (need, objs, renew, len(clash))
    body = souk_defs[HERALD_BODY][1]
    bd_need, bd_objs, mapping, n1 = carry([HERALD_BODY], set())
    sk_need, sk_objs, sk_renew, n2 = carry(list(SKELETONS), set(bd_need), skip_roots=False)
    mapping.update(sk_renew)
    parts = [dict(o, body=renew_body(o, mapping)) for o in bd_objs + sk_objs]
    bcopy = own_table(dict(body, body=replace_u32s(body['body'], mapping)), fresh, classes, f"ACFE_Herald_{body['name']}")
    nb = add_columns(bcopy['body'], 14, [(17 + k, skeleton_cls, mapping.get(u, u)) for k, u in enumerate(SKELETONS)], fresh)
    bcopy = dict(bcopy, body=nb, body_size=bcopy['body_size'] + len(nb) - len(bcopy['body']))
    log(f"herald body: {bcopy['name']} 0x{bcopy['uid']:08X} ({len(bd_objs)} parts, {len(sk_objs)} rig objects, {n1 + n2} nested ids renewed): " + ', '.join((f'c{c}' for _p, _l, c in table_rows(bcopy['body'])[0][2])))
    done = set(bd_need) | set(sk_need)
    head = souk_defs[HERALD_HEADS][1]
    hd_need, hd_objs, hd_renew, n3 = carry([HERALD_HEADS], done)
    mapping.update(hd_renew)
    hd_objs = [dict(o, body=renew_body(o, mapping)) for o in hd_objs]
    head_copy = own_table(dict(head, body=replace_u32s(head['body'], mapping)), fresh, classes, f"ACFE_Herald_{head['name']}")
    if HERALD_ONLY_HEAD is not None:
        rows = table_rows(head_copy['body'])
        hb = head_copy['body']
        first = rows[0][0]
        count_at = first - 4
        assert struct.unpack_from('<I', hb, count_at)[0] == len(rows)
        ends = [r[0] for r in rows[1:]] + [max((p + ln for p, ln, _c in rows[-1][2]))]
        k = HERALD_ONLY_HEAD
        hb = hb[:count_at] + struct.pack('<I', 1) + hb[count_at + 4:first] + hb[rows[k][0]:ends[k]] + hb[ends[-1]:]
        head_copy = dict(head_copy, body=hb, body_size=head_copy['body_size'] + len(hb) - len(head_copy['body']))
        assert len(table_rows(hb)) == 1
        log(f'herald heads: DIAGNOSTIC - only row {k}')
    if HERALD_DROP_BEARDS:
        hb = keep_cells(head_copy['body'], {c for _rs, _c, cells in table_rows(head_copy['body']) for _p, _l, c in cells} - {4})
        head_copy = dict(head_copy, body=hb, body_size=head_copy['body_size'] + len(hb) - len(head_copy['body']))
        log('herald heads: the beard cells (col 4, AC2MP_Beards) dropped')
    done |= set(hd_need)
    roots = sorted(set(HERALD_VARIETY.values()))
    vr_need, vr_objs, vr_renew, n4 = carry(roots, done)
    mapping.update(vr_renew)
    vr_objs = [dict(o, body=renew_body(o, mapping)) for o in vr_objs]
    vtables = {u: own_table(dict(souk_defs[u][1], body=replace_u32s(souk_defs[u][1]['body'], mapping)), fresh, classes, f"ACFE_Herald_{souk_defs[u][1]['name']}") for u in roots}
    voice = own_table(mill_defs[CROWD_HEADS][1], fresh, classes, f"ACFE_Herald_{mill_defs[CROWD_HEADS][1]['name']}_Voices")
    vb = keep_cells(voice['body'], {4})
    voice = dict(voice, body=vb, body_size=voice['body_size'] + len(vb) - len(voice['body']))
    bb = builder['body']
    for old, new in ((main_copy['uid'], bcopy['uid']), (CROWD_HEADS, voice['uid'])):
        assert bb.count(struct.pack('<I', old)) == 1, hex(old)
        bb = bb.replace(struct.pack('<I', old), struct.pack('<I', new))
    bb = add_columns(bb, 22, [(23, table_cls, head_copy['uid'])] + [(c, table_cls, vtables[u]['uid']) for c, u in sorted(HERALD_VARIETY.items())], fresh)
    bb = keep_cells(bb, {14, 20, 21, 23} | set(HERALD_VARIETY))
    builder = dict(builder, body=bb, body_size=builder['body_size'] + len(bb) - len(builder['body']))
    cells = [c for _p, _l, c in table_rows(bb)[0][2]]
    assert sorted(cells) == sorted([14, 20, 21, 23] + list(HERALD_VARIETY)), cells
    parts += hd_objs + vr_objs + [bcopy, head_copy, voice] + list(vtables.values())
    if HERALD_DIFFUSE_FROM_SLOT:
        parts = diffuse_from_slot(parts, classes, log)
    log(f"herald heads: {head_copy['name']} 0x{head_copy['uid']:08X} ({len(hd_objs)} objects, {n3} nested ids renewed); variety: " + ', '.join((f"c{c} {vtables[u]['name']}" for c, u in sorted(HERALD_VARIETY.items()))) + f' ({len(vr_objs)} objects, {n4} nested ids renewed); builder 0x{b_uid:08X} cells {sorted(cells)}')
    log(f"herald parts: {len(parts)} objects {dict(Counter((classes.get(o['type'], hex(o['type'])) for o in parts)))}")
    check_closure(parts, mill_defs if closure_defs is None else closure_defs, uni, classes, log)
    written = [builder] + parts
    check_nested(written, mill, classes, log)
    return {'tp_uid': b_uid, 'fat_uid': None, 'selector_uid': b_uid, 'parts': parts, 'written': written}
PERFORMER_SKIP_TABLES = {2744156443, 479589267}
BEARDS_TABLE = 937654240
UNIVERSAL_HEAD = 3403676410

def builder_parts(body):
    rows = table_rows(body)
    assert len(rows) == 1, 'one inline row expected'
    cells = [(c, struct.unpack_from('<I', body, p + 4)[0], struct.unpack_from('<I', body, p + 14)[0]) for p, ln, c in rows[0][2] if body[p + 10] == 28]
    end = max((p + ln for p, ln, _c in rows[0][2])) if rows[0][2] else rows[0][1] + 4
    tail = body[end:]
    assert tail[:2] == b'\x01\x00', tail[:16].hex()
    n = struct.unpack_from('<I', tail, 6)[0]
    tables = [struct.unpack_from('<I', tail, 12 + 6 * k)[0] for k in range(n)]
    assert all((tail[10 + 6 * k:12 + 6 * k] == b'\x01\x00' for k in range(n))), tail.hex()
    return (cells, tables)

def drop_ref_cells(body, uids):
    rows = table_rows(body)
    cols = {c for _r, _n, cells in rows for p, _l, c in cells if body[p + 10] == 28 and struct.unpack_from('<I', body, p + 14)[0] in uids}
    if not cols:
        return body
    return keep_cells(body, {c for _r, _n, cells in rows for _p, _l, c in cells} - cols)

def keep_row(body, k):
    rows = table_rows(body)
    first, count_at = (rows[0][0], rows[0][0] - 4)
    assert struct.unpack_from('<I', body, count_at)[0] == len(rows) and 0 <= k < len(rows), (len(rows), k)
    ends = [r[0] for r in rows[1:]] + [max((p + ln for p, ln, _c in rows[-1][2]))]
    return body[:count_at] + struct.pack('<I', 1) + body[count_at + 4:first] + body[rows[k][0]:ends[k]] + body[ends[-1]:]

def build_performer(acr_builder, mill_defs, souk, souk_defs, mill, uni, fresh, classes, log=print, available=None, closure_defs=None, pin_rows=None):
    from collections import Counter
    import own_entity
    table_cls = next((k for k, v in classes.items() if v == 'BuildTable'))
    skeleton_cls = next((k for k, v in classes.items() if v == 'Skeleton'))
    available = set(available) if available is not None else set(mill_defs) | always_available(uni)
    src = souk_defs[acr_builder][1]
    label = f"ACR_{src['name']}"
    inline, tables = builder_parts(src['body'])
    keep_tables = [u for u in tables if u in souk_defs and u not in PERFORMER_SKIP_TABLES and ('voice' not in souk_defs[u][1]['name'].lower())]
    skipped = [souk_defs[u][1]['name'] if u in souk_defs else f'0x{u:08X} (undefined)' for u in tables if u not in keep_tables]
    inline = [(c, k, r) for c, k, r in inline if r not in SKELETONS]
    if UNIVERSAL_HEAD not in part_closure(souk_defs, [r for _c, _k, r in inline] + keep_tables, set()):
        inline.append((0, next((k for k, v in classes.items() if v == 'GraphicObject')), UNIVERSAL_HEAD))
    log(f"{src['name']}: inline cells " + ', '.join((f"c{c} {classes.get(k, hex(k))} {(souk_defs[r][1]['name'] if r in souk_defs else hex(r))}" for c, k, r in inline)) + '; tables ' + ', '.join((souk_defs[u][1]['name'] for u in keep_tables)) + f"; left out: {', '.join(skipped)}")
    b_uid, (builder, main_copy, _hats) = copy_builder(mill_defs, fresh, classes, {}, label)
    roots = [r for _c, _k, r in inline] + list(SKELETONS) + keep_tables
    need = part_closure(souk_defs, roots, available)
    objs, mapping = own_copies([souk_defs[u][1] for u in need if u not in keep_tables], set(uni), fresh, log, classes)
    _ids, clash, _st = nested_clashes(objs, mill, classes)
    mapping.update({u: fresh.take() for u in sorted(clash)})
    parts = [dict(o, body=renew_body(o, mapping)) for o in objs]
    for k, o in enumerate(parts):
        if classes.get(o['type']) == 'BuildTable' and o['name'] in (pin_rows or {}):
            nb = keep_row(o['body'], pin_rows[o['name']])
            parts[k] = dict(o, body=nb, body_size=o['body_size'] + len(nb) - len(o['body']))
            log(f"   {o['name']}: pinned to row {pin_rows[o['name']]}")
    for k, o in enumerate(parts):
        if classes.get(o['type']) == 'BuildTable' and struct.pack('<I', mapping.get(BEARDS_TABLE, BEARDS_TABLE)) in o['body']:
            nb = drop_ref_cells(o['body'], {mapping.get(BEARDS_TABLE, BEARDS_TABLE)})
            if nb != o['body']:
                parts[k] = dict(o, body=nb, body_size=o['body_size'] + len(nb) - len(o['body']))
                log(f"   {o['name']}: beard cells dropped")
    tcopies = []
    for u in keep_tables:
        t = souk_defs[u][1]
        tb = drop_ref_cells(replace_u32s(t['body'], mapping), {mapping.get(BEARDS_TABLE, BEARDS_TABLE)})
        tcopies.append(own_table(dict(t, body=tb, body_size=t['body_size'] + len(tb) - len(t['body'])), fresh, classes, f"{label}_{t['name']}"))
    tmap = {u: t['uid'] for u, t in zip(keep_tables, tcopies)}
    parts = [dict(o, body=replace_u32s(o['body'], tmap)) if any((struct.pack('<I', u) in o['body'] for u in tmap)) else o for o in parts]
    mb = keep_cells(main_copy['body'], set())
    cols = [(12 + k, skeleton_cls, mapping.get(u, u)) for k, u in enumerate(SKELETONS)]
    cols += [(30 + k, cls_, mapping.get(r, r)) for k, (_c, cls_, r) in enumerate(inline)]
    mb = add_columns(mb, 11, cols, fresh)
    main = dict(main_copy, body=mb, body_size=main_copy['body_size'] + len(mb) - len(main_copy['body']))
    female = any((souk_defs[u][1]['name'] == 'IsFemale' for u in keep_tables))
    voice = own_table(mill_defs[CROWD_HEADS][1], fresh, classes, f"{label}_{mill_defs[CROWD_HEADS][1]['name']}_Voices")
    vb = keep_cells(voice['body'], {4})
    voice = dict(voice, body=vb, body_size=voice['body_size'] + len(vb) - len(voice['body']))
    import library_cell
    mute = female or not all((r in available for r in library_cell.refs(voice['body']) if r in uni))
    if mute and (not female):
        log(f"{label}: no voice cells (AC3's crowd voice sets are not in the map)")
    bb = builder['body']
    assert bb.count(struct.pack('<I', CROWD_HEADS)) == 1
    bb = bb.replace(struct.pack('<I', CROWD_HEADS), struct.pack('<I', voice['uid']))
    new_cols = [(23 + k, table_cls, t['uid']) for k, t in enumerate(tcopies)]
    bb = add_columns(bb, 22, new_cols, fresh)
    base_cols = {14, 20} if mute else {14, 20, 21}
    bb = keep_cells(bb, base_cols | {c for c, _k, _r in new_cols})
    builder = dict(builder, body=bb, body_size=builder['body_size'] + len(bb) - len(builder['body']))
    cells = sorted((c for _p, _l, c in table_rows(bb)[0][2]))
    assert cells == sorted(list(base_cols) + [c for c, _k, _r in new_cols]), cells
    set_ids = {v[1] for o in parts if classes.get(o['type']) == 'AnimSet' for v in own_entity.animset_slots(o['body']).values()}
    have = {o['serial'] for _n, (_r, p, _raw) in mill.items() for o in p.objects}
    clips, seen = ([], set())
    for _n, (_r, p, _raw) in souk.items():
        for o in p.objects:
            if o['serial'] in set_ids and o['serial'] not in have and (o['serial'] not in seen) and (classes.get(o['type']) == 'Animation'):
                clips.append(o)
                seen.add(o['serial'])
    parts += [main] + ([] if mute else [voice]) + tcopies + clips
    parts = diffuse_from_slot(parts, classes, log)
    log(f"{label}: builder 0x{b_uid:08X} cells {cells}; Main 0x{main['uid']:08X} + " + ', '.join((f'c{c} {classes.get(k, hex(k))}' for c, k, _r in cols)) + f'; {len(tcopies)} tables; {len(clips)} clips carried, {len(set_ids) - len(clips)} already in the map; {len(clash)} nested ids renewed')
    log(f"{label} parts: {len(parts)} objects {dict(Counter((classes.get(o['type'], hex(o['type'])) for o in parts)))}")
    check_closure([o for o in parts if classes.get(o['type']) != 'Animation'], mill_defs if closure_defs is None else closure_defs, uni, classes, log)
    written = [builder] + parts
    check_nested(written, mill, classes, log)
    return {'tp_uid': b_uid, 'fat_uid': None, 'selector_uid': b_uid, 'parts': parts, 'written': written, 'replaces': acr_builder}

def mill_probe(build, own=True, rig=True, heads=True, fat=True, acc=True, variety=True, log=print):
    import clone
    import inventory
    classes, _ = exe_registry.load(exe_registry.AC3)
    src = os.path.join(paths.AC3_SOURCE, MILL)
    souk, mill = (payloads(SOUK), payloads(src))
    souk_defs, mill_defs = (defined(souk), defined(mill))
    uni = inventory.universe('AC3')
    fresh = clone.FreshUids(81036112, set(uni) | set(mill_defs) | set(souk_defs), [])
    m = build_merchant(mill_defs, souk, souk_defs, mill, uni, fresh, classes, own, rig, heads, fat, acc, variety, log)
    copy_uid, copy_objs, tp_uid = (m['copy_uid'], m['copy_objs'], m['tp_uid'])
    fat_uid, selector_uid, written = (m['fat_uid'], m['selector_uid'], m['written'])
    image = ArchiveImage.read(src)
    routes = {'copy': copy_uid, 'transplant': tp_uid, 'fat': fat_uid, 'selector': selector_uid}
    stall_route = (FAT_ROUTE if fat else {k: 'transplant' for k in STALL_ROUTE}) if heads else STALL_ROUTE
    done = []
    for name, (rec, p, _raw) in mill.items():
        changed = name == MERCHANT_CELL
        objs = []
        for o in p.objects:
            body = o['body']
            route = stall_route.get(o['name']) if o['has_uid'] else None
            if route:
                old = struct.pack('<I', GENERIC_MERCHANT)
                assert body.count(old) == 2, (o['name'], body.count(old))
                body = body.replace(old, struct.pack('<I', routes[route]))
                done.append(f"{o['name']} -> {route} 0x{routes[route]:08X}")
                changed = True
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        if name == MERCHANT_CELL:
            for o in written:
                assert o['body'][:2] == b'\x01\x02' or o['body_size'] == len(o['body']) - 1, (o['name'], o['body_size'], len(o['body']))
                objs.append((o.get('serial', o['uid']), mapwalk.object_bytes(o), None))
        if changed:
            d = mapwalk.rebuild(objs, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, rec.index, payload)
    log(f'stalls: {done}')
    log(f'{MERCHANT_CELL}: + {len(written)} objects (transplant builder 0x{tp_uid:08X}' + ('' if heads else f', control builder 0x{copy_uid:08X}') + ')')
    dest_dir = os.path.join(OUT, build)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, MILL)
    size = write(dest, image)
    log(f'-> {dest} ({size:,} bytes)')
    return dest

class RangeUids:

    def __init__(self, lo, size, avoid, blobs):
        self.lo, self.hi, self.next = (lo, lo + size, lo)
        self.avoid = {u for u in avoid if lo <= u < lo + size}
        top = struct.pack('<H', lo >> 16)
        for d in blobs:
            k = d.find(top, 2)
            while k >= 0:
                u = struct.unpack_from('<I', d, k - 2)[0]
                if lo <= u < self.hi:
                    self.avoid.add(u)
                k = d.find(top, k + 1)

    def take(self):
        while self.next in self.avoid:
            self.next += 1
        if self.next >= self.hi:
            raise SystemExit(f'uid range 0x{self.lo:08X}..0x{self.hi - 1:08X} exhausted')
        u, self.next = (self.next, self.next + 1)
        return u

def loaded_files(files, classes):
    import library_cell
    whole = {n: v for n, v in files.items() if n.startswith(('Cell', 'DataBlock')) or classes.get(v[1].objects[0]['type']) == 'World'}
    listed = set()
    for n, (_r, p, _raw) in whole.items():
        if n.startswith('Cell') and classes.get(p.objects[0]['type']) == 'GridCellDataBlock':
            listed |= set(library_cell.cell_list(p.objects[0]['body'])[1])
    reach = {o['uid'] for _r, p, _raw in whole.values() for o in p.objects if o['has_uid']}
    reach |= {p.objects[0]['uid'] for n, (_r, p, _raw) in files.items() if n not in whole and p.objects[0]['has_uid'] and (p.objects[0]['uid'] in listed)}
    return (whole, reach)

def always_loaded(files, classes, root):
    import library_cell
    whole = {n: v for n, v in files.items() if n == root or n.startswith('DataBlock') or classes.get(v[1].objects[0]['type']) == 'World'}
    listed = set(library_cell.cell_list(files[root][1].objects[0]['body'])[1])
    reach = {o['uid'] for _r, p, _raw in whole.values() for o in p.objects if o['has_uid']}
    reach |= {p.objects[0]['uid'] for n, (_r, p, _raw) in files.items() if n not in whole and p.objects[0]['has_uid'] and (p.objects[0]['uid'] in listed)}
    return (whole, reach)

def map_merchant(argv, log=print):
    import inventory
    import library_cell
    import own_entity
    from collections import Counter
    ap = argparse.ArgumentParser(prog='npc_port.py map')
    ap.add_argument('--src', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--into', required=True, help='the root cell')
    ap.add_argument('--uid-base', type=lambda v: int(v, 0), required=True)
    ap.add_argument('--uid-limit', type=lambda v: int(v, 0), default=65536)
    ap.add_argument('--stalls', default='ACFE_GEN_Merchant', help="the stall entities' name prefix")
    ap.add_argument('--acr-builder', type=lambda v: int(v, 0), help='--npc performer: the ACR builder the performers name')
    ap.add_argument('--pin-row', action='append', default=[], metavar='TABLE=ROW', help='--npc performer: cut a carried table (by name) to one row')
    ap.add_argument('--npc', choices=('merchant', 'herald', 'performer'), default='merchant', help="the model: ACR's Turkish merchant (MH11; the stalls) or ACR's herald (OR2; the orator groups, merchant_stalls.py --stalls ACFE_GEN_Orator)")
    ap.add_argument('--available', choices=('loaded', 'root'), default='loaded', help="what the model may name without carrying it: what the map loads, streaming cells included (loaded; Souk's SM3), or only what it always loads (root: the root cell, the files it lists, data blocks, the World - the other ported maps; Antioch's orator cell holds merchant parts)")
    a = ap.parse_args(argv)
    a.stalls = tuple(a.stalls.split(','))
    maps_out = os.path.join(HERE, 'out', 'maps')
    target = os.path.join(maps_out, a.src, a.archive)
    classes, _ = exe_registry.load(exe_registry.AC3)
    files = payloads(target)
    whole, reach = loaded_files(files, classes)
    base, base_reach = always_loaded(files, classes, a.into) if a.available == 'root' else (whole, reach)
    uni = inventory.universe('AC3')
    available = base_reach | always_available(uni)
    souk_defs, mill_defs = (defined(files), defined(base))
    for u in SKELETONS:
        if u not in souk_defs:
            arc, o = skeleton_donor(u)
            souk_defs[u] = (f'({arc})', o)
            log(f"{o['name']} 0x{u:08X}: not in this map's files - ACR's copy from {arc} (the same bytes in every ACR map that has it)")
    if a.npc == 'merchant' and SELECTOR not in souk_defs:
        hit = next(((p.rec.name, o) for p in mapwalk.payloads('ACR', a.archive) for o in p.objects if o['has_uid'] and o['uid'] == SELECTOR), None)
        if hit is None:
            raise SystemExit(f"the merchants' selector 0x{SELECTOR:08X} is in neither the build nor ACR's {a.archive}")
        souk_defs[SELECTOR] = (f'(ACR {hit[0]})', hit[1])
        log(f"{hit[1]['name']} 0x{SELECTOR:08X}: lost in stage 1 (only the dropped merchant named it) - ACR's own copy from {hit[0]}")
    for u in (GENERIC_MERCHANT, MAIN_TABLE, HATS_TABLE, CROWD_HEADS):
        assert u in mill_defs, f'0x{u:08X} not in a loaded file: run merchant_stalls.py first'
    fresh = RangeUids(a.uid_base, a.uid_limit, set(uni) | set(souk_defs), [p.data for _r, p, _raw in files.values()])
    if a.npc == 'performer':

        def build(*args, **kw):
            return build_performer(a.acr_builder, *args, pin_rows={t: int(r) for t, r in (x.rsplit('=', 1) for x in a.pin_row)}, **kw)
    else:
        build = build_herald if a.npc == 'herald' else build_merchant
    m = build(mill_defs, files, souk_defs, whole, uni, fresh, classes, log=log, available=available, closure_defs=available)
    written, selector = (m['written'], m['selector_uid'])
    unloaded = [p for n, (_r, p, _raw) in files.items() if n not in base]
    registered = {o['uid'] for p in unloaded for o in p.objects if o['has_uid']}
    for p in unloaded:
        for o in p.objects:
            if classes.get(o['type']) not in RAW_DATA:
                registered.update(own_entity.inline_uids(o['body'], classes))
    clips = {o['serial'] for o in written if classes.get(o['type']) == 'Animation'}
    in_range = lambda u: a.uid_base <= u < a.uid_base + a.uid_limit
    cand = set()
    for o in written:
        if classes.get(o['type']) == 'Animation':
            continue
        assert o.get('serial', o['uid']) == o['uid'], (o['name'], o.get('serial'), o['uid'])
        inline = [] if classes.get(o['type']) in BULK else [u for u in own_entity.inline_uids(o['body'], classes) if u != o['uid'] and could_be_id(u)]
        if o['uid'] in registered and (not in_range(o['uid'])) and (o['uid'] not in available):
            cand.add(o['uid'])
        ids = inline
        cand |= {u for u in ids if u in registered and could_be_id(u) and (not in_range(u)) and (u not in clips) and (u not in available)}
    renew = {u: fresh.take() for u in sorted(cand)}
    final = []
    for o in written:
        if classes.get(o['type']) == 'Animation':
            final.append(o)
            continue
        b = replace_refs(o['body'], renew) if classes.get(o['type']) in BULK else replace_u32s(o['body'], renew)
        u = renew.get(o['uid'], o['uid'])
        final.append(dict(o, body=b, uid=u, serial=u))
    log(f"renewed {len(renew)} ids carried out of the unloaded merchant files ({sum((1 for o in written if o['uid'] in renew))} objects' own)")
    stray = []
    for before, after_o in zip(written, final):
        a0, b0 = (before['body'], after_o['body'])
        assert len(a0) == len(b0), before['name']
        bulk = classes.get(before['type']) in BULK
        for k in diff_windows(a0, b0):
            if k + 4 <= len(a0) and a0[k:k + 4] != b0[k:k + 4]:
                u = struct.unpack_from('<I', a0, k)[0]
                if u in renew and struct.unpack_from('<I', b0, k)[0] == renew[u]:
                    if k != 2 and (not could_be_id(u)) or (bulk and k not in id_sites(a0, u)):
                        stray.append((before['name'], k, hex(u)))
    changed_bytes_ok = all((before['body'][k] == after_o['body'][k] or any((struct.unpack_from('<I', before['body'], s_)[0] in renew for s_ in range(max(0, k - 3), k + 1) if s_ + 4 <= len(before['body']))) for before, after_o in zip(written, final) for k in diff_windows(before['body'], after_o['body'])))
    assert changed_bytes_ok, 'the renewal changed a byte that belongs to no renewed id'
    assert not stray, f'the renewal changed a round float or bulk data: {stray[:8]}'
    log(f'renewal: only renewed ids changed; no round float, bulk data only at id sites ({len(final)} objects)')
    kept = Counter((o['name'] for o in final if classes.get(o['type']) not in BULK + ('Animation',) for u in renew if struct.pack('<I', u) in o['body']))
    log(f"renewed ids left by value in structured objects: {dict(kept) or 'none'}")
    left = [(o['name'], hex(u)) for o in final if classes.get(o['type']) != 'Animation' for u in [o['uid']] + ([] if classes.get(o['type']) in BULK else own_entity.inline_uids(o['body'], classes)) if u in registered and (u == o['uid'] or could_be_id(u)) and (u not in clips) and (u not in available)]
    assert not left, f'original ids left: {left[:8]}'
    own_ids = {o['uid'] for o in final}
    dangling = sorted({(o['name'], hex(r)) for o in final if classes.get(o['type']) != 'Animation' for r in library_cell.refs(o['body']) if r in souk_defs and r not in available and (r not in own_ids)})
    assert not dangling, f'references into unloaded files: {dangling[:8]}'
    check_nested(final, whole, classes, log)
    for o in final:
        assert classes.get(o['type']) == 'Animation' or o['body_size'] == len(o['body']) - 1, (o['name'], o['body_size'])
    image = ArchiveImage.read(target)
    old = struct.pack('<I', m.get('replaces', GENERIC_MERCHANT))

    def is_stall(o):
        return (o['name'] or '').startswith(a.stalls) and (classes.get(o['type']) == 'Entity' if a.npc != 'performer' else classes.get(o['type']) in ('Entity', 'EntityGroup') and old in o['body'])
    replaced, unlist_in = (m.get('replaces'), set())
    if replaced is not None and (not any((classes.get(o['type']) not in BULK + ('GridCellDataBlock',) and (not (o['has_uid'] and o['uid'] == replaced)) and (not is_stall(o)) and (replaced in library_cell.refs(o['body'])) for _r, p, _raw in files.values() for o in p.objects))):
        unlist_in = {n for n, (_r, p, _raw) in files.items() if classes.get(p.objects[0]['type']) == 'GridCellDataBlock' and replaced in library_cell.cell_list(p.objects[0]['body'])[1]}
    stalls = []
    for name, (rec, p, _raw) in files.items():
        is_root = name == a.into
        hits = [o for o in p.objects if is_stall(o)]
        if not hits and (not is_root) and (name not in unlist_in):
            continue
        objs = []
        for o in p.objects:
            body = o['body']
            if o in hits:
                assert body.count(old) == 2, (o['name'], body.count(old))
                body = body.replace(old, struct.pack('<I', selector))
                stalls.append(o['name'])
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        g = p.objects[0]
        gb = g['body']
        if is_root:
            _n, listed, _m = library_cell.cell_list(gb)
            gb = library_cell.list_add(gb, [o['uid'] for o in final if classes.get(o['type']) != 'Animation' and o['uid'] not in listed])
            objs += [(o['serial'], mapwalk.object_bytes(o), None) for o in final]
        if name in unlist_in:
            from build_static import clean_cell
            gb, _gone = clean_cell(gb, {replaced})
            log(f"{name}: ACR's builder 0x{replaced:08X} off its list ({_gone} entry)")
        if gb is not g['body']:
            objs[0] = (g['serial'], mapwalk.object_bytes(g, gb), g['filter'])
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, rec.index, payload)
    dest_dir = os.path.join(maps_out, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, a.archive)
    size = write(dest, image)
    after = payloads(dest)
    defs = Counter((o['uid'] for _r, p, _raw in after.values() for o in p.objects if o['has_uid']))
    root = after[a.into][1]
    got = {o['uid']: o['body'] for o in root.objects if o['has_uid']}
    by_serial = {o['serial']: o['body'] for o in root.objects}
    _n, listed2, _m = library_cell.cell_list(root.objects[0]['body'])
    v = Counter()
    v['model in the root, byte for byte'] = sum((1 for o in final if (by_serial.get(o['serial']) if classes.get(o['type']) == 'Animation' else got.get(o['uid'])) == o['body']))
    v['listed'] = sum((1 for o in final if classes.get(o['type']) == 'Animation' or o['uid'] in listed2))
    v['defined once'] = sum((1 for o in final if classes.get(o['type']) == 'Animation' or defs[o['uid']] == 1))
    v['stalls on the selector'] = sum((1 for _r, p, _raw in after.values() for o in p.objects if o['name'] in stalls and o['body'].count(struct.pack('<I', selector)) == 2 and (old not in o['body'])))
    if unlist_in:
        v['cells still listing the replaced ACR builder'] = sum((1 for _r, p, _raw in after.values() if classes.get(p.objects[0]['type']) == 'GridCellDataBlock' and replaced in library_cell.cell_list(p.objects[0]['body'])[1]))
    touched = {files[n][0].index for n in files if n == a.into or n in unlist_in or any(((o['name'] or '').startswith(a.stalls) for o in files[n][1].objects))}
    with Forge(dest) as f, Forge(target) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index not in touched))
    ok = v['model in the root, byte for byte'] == v['listed'] == v['defined once'] == len(final) and v['stalls on the selector'] == len(stalls) > 0 and others and (not v['cells still listing the replaced ACR builder'])
    log(f"{len(final)} objects into {a.into} ({dict(Counter((classes.get(o['type'], hex(o['type'])) for o in final)))}); selector 0x{selector:08X}" + (f" (A 0x{m['tp_uid']:08X}, B 0x{m['fat_uid']:08X})" if m['fat_uid'] else '') + f'; {len(stalls)} stalls; uids 0x{a.uid_base:08X}..0x{fresh.next - 1:08X}')
    log(f'wrote {dest} ({size:,} bytes)')
    log(f"verify: {dict(v)}, every other file byte-identical {others}: {('OK' if ok else 'FAILED')}")
    if not ok:
        raise SystemExit(1)
    return dest

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == 'map':
        map_merchant(argv[1:])
        return 0
    if argv and argv[0].startswith('--'):
        map_merchant(argv)
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('mill-test', help="Souk's merchant at Mill's stalls")
    m.add_argument('build')
    q = sub.add_parser('mill-probe', help="the transplant (AC3's merchant builder, ACR's parts) + controls")
    q.add_argument('build')
    q.add_argument('--mh5-rule', action='store_true', help="MH5's closure (skip what AC3's shared archives define): reproduces MH5")
    q.add_argument('--no-rig', action='store_true', help="without ACR's add-on skeletons: reproduces MH6")
    q.add_argument('--no-heads', action='store_true', help="without ACR's heads, with the controls: reproduces MH7b")
    q.add_argument('--no-fat', action='store_true', help='without the second body and the selector: reproduces MH8')
    q.add_argument('--no-acc', action='store_true', help="without ACR's accessories / sleeves / hats / fat AnimSet: reproduces MH9")
    q.add_argument('--no-variety', action='store_true', help="without ACR's colour / pattern / eye / size / stance tables: reproduces MH10")
    a = ap.parse_args(argv)
    if a.cmd == 'mill-test':
        mill_test(a.build)
    elif a.cmd == 'mill-probe':
        mill_probe(a.build, own=not a.mh5_rule, rig=not (a.mh5_rule or a.no_rig), heads=not (a.mh5_rule or a.no_rig or a.no_heads), fat=not (a.mh5_rule or a.no_rig or a.no_heads or a.no_fat), acc=not (a.mh5_rule or a.no_rig or a.no_heads or a.no_fat or a.no_acc), variety=not (a.mh5_rule or a.no_rig or a.no_heads or a.no_fat or a.no_acc or a.no_variety))
    return 0
if __name__ == '__main__':
    sys.exit(main())
