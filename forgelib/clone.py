"""forgelib/clone.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import hashlib
import os
import pickle
import random
import struct
from datafile import decompress, compress
from forge import Forge
from forge_write import add_file
from objects import walk, directory_size, rebuild, directory_mismatches
from transplant import object_at, build_object
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'out', 'clone')
TEMPLATE = 3170581626
NESTED = {881535190}
NESTED_NAMES = {'BuildRow', 'BuildColumn', 'LODDescriptor', 'MeshBone', 'TextureSelector', 'UVTransform', 'TextureFormatOptions'}

def objects_of(plain):
    return [object_at(plain, o, s) for o, s, _ in walk(plain)]

def digest(body):
    return hashlib.sha1(body).hexdigest()

def definitions_index(archive):
    path = os.path.join(CACHE, f'defsidx_{_archive_key(archive)}.pkl')
    if os.path.exists(path):
        return pickle.load(open(path, 'rb'))
    out = _definitions_index_records(archive)
    os.makedirs(CACHE, exist_ok=True)
    pickle.dump(out, open(path, 'wb'))
    return out

def _definitions_index_records(archive):
    import record_cache
    store = record_cache.Store('defs')
    out = {}
    with Forge(archive) as f:
        for r in f.records:
            raw = f.read(r)
            key = record_cache.digest(raw)
            if key not in store:
                items = []
                try:
                    d = decompress(raw)
                    for o, s, _ in walk(d):
                        ob = object_at(d, o, s)
                        items.append((ob['uid'], digest(ob['body'])))
                except Exception:
                    pass
                store.put(key, items)
            for uid, bd in store.get(key):
                out.setdefault(uid, []).append((r.name, bd))
    store.save()
    return out

def _definitions_index_full(archive):
    out = {}
    with Forge(archive) as f:
        for r in f.records:
            try:
                d = decompress(f.read(r))
                for o, s, _ in walk(d):
                    ob = object_at(d, o, s)
                    out.setdefault(ob['uid'], []).append((r.name, digest(ob['body'])))
            except Exception:
                continue
    return out

def pristine_definitions(archive, uids):
    index = definitions_index(archive)
    return {u: index[u] for u in uids if u in index}

def plan(sources, pristine_archive, class_names, keep_shared=True, always=()):
    nested_ids = NESTED | {i for i, n in class_names.items() if n in NESTED_NAMES}
    tops = {}
    for name, plain in sources.items():
        for o in objects_of(plain):
            tops.setdefault(o['uid'], []).append((name, o))
    defs = pristine_definitions(pristine_archive, set(tops))
    renew, kept = (set(), {})
    for uid, where in tops.items():
        o = where[0][1]
        if o['type'] == TEMPLATE:
            kept[uid] = 'material template'
        elif uid in always:
            renew.add(uid)
        elif keep_shared and len(defs.get(uid, [])) > 1 and all((digest(ob['body']) in {d for _f, d in defs[uid]} for _n, ob in where)):
            kept[uid] = f'shared by {len(defs[uid])} files, unchanged'
        else:
            renew.add(uid)
    changed = True
    while changed:
        changed = False
        for uid in list(kept):
            body = tops[uid][0][1]['body']
            if any((struct.pack('<I', r) in body for r in renew if r != uid)):
                del kept[uid]
                renew.add(uid)
                changed = True
    nested = set()
    for uid in renew:
        for _n, o in tops[uid]:
            b = o['body']
            for p in range(6, len(b) - 7):
                x, c = struct.unpack_from('<II', b, p)
                if c in nested_ids and x >= 65536:
                    nested.add(x)
    nested -= set(tops)
    elsewhere = nested_elsewhere(pristine_archive, set(sources), nested, nested_ids)
    for x in elsewhere:
        kept[x] = 'nested value shared with files outside the copy'
    return (renew, kept, nested - elsewhere | foreign_inline(renew, tops))

def foreign_inline(renew, tops):
    if not FOREIGN:
        return set()
    import id_census
    valid, bulk = id_census._class_tables()
    cloth = cloth_types()
    out = set()
    for uid in renew:
        for _n, o in tops[uid]:
            if o['type'] not in bulk:
                out |= {u for u, c in id_census.inline_ids(o['body'], valid) if u in FOREIGN and (o['type'] not in cloth or c in cloth[o['type']])}
    return out - set(tops)

def _archive_key(archive):
    st = os.stat(archive)
    return hashlib.sha1(repr((os.path.abspath(archive), st.st_size, int(st.st_mtime))).encode()).hexdigest()[:16]

def nested_index(archive, nested_ids):
    key = hashlib.sha1(repr((_archive_key(archive), sorted(nested_ids))).encode()).hexdigest()[:16]
    path = os.path.join(CACHE, f'nestedidx_{key}.pkl')
    if os.path.exists(path):
        return pickle.load(open(path, 'rb'))
    index = _nested_index_records(archive, nested_ids)
    os.makedirs(CACHE, exist_ok=True)
    pickle.dump(index, open(path, 'wb'))
    return index

def _nested_index_records(archive, nested_ids):
    import record_cache
    stores = {c: record_cache.Store(f'nested_{c:08X}') for c in set(nested_ids)}
    index = {}
    with Forge(archive) as f:
        for r in f.records:
            raw = f.read(r)
            if raw[4:8] != b'3\xaa\xfbW':
                continue
            key = record_cache.digest(raw)
            missing = [c for c, s in stores.items() if key not in s]
            if missing:
                try:
                    d = decompress(raw)
                except Exception:
                    d = None
                for c in missing:
                    found = set()
                    if d is not None:
                        nb = struct.pack('<I', c)
                        j = d.find(nb, 4)
                        while j >= 0:
                            found.add(struct.unpack_from('<I', d, j - 4)[0])
                            j = d.find(nb, j + 1)
                    stores[c].put(key, tuple(sorted(found)))
            for s in stores.values():
                for v in s.get(key):
                    index.setdefault(v, set()).add(r.name)
    for s in stores.values():
        s.save()
    return index

def _nested_index_full(archive, nested_ids):
    index = {}
    needles = [struct.pack('<I', n) for n in nested_ids]
    with Forge(archive) as f:
        for r in f.records:
            raw = f.read(r)
            if raw[4:8] != b'3\xaa\xfbW':
                continue
            try:
                d = decompress(raw)
            except Exception:
                continue
            for nb in needles:
                j = d.find(nb, 4)
                while j >= 0:
                    index.setdefault(struct.unpack_from('<I', d, j - 4)[0], set()).add(r.name)
                    j = d.find(nb, j + 1)
    return index

def nested_elsewhere(archive, skip_names, candidates, nested_ids):
    index = nested_index(archive, nested_ids)
    skip = set(skip_names)
    return {x for x in candidates if any((n not in skip for n in index.get(x, ())))}
ENTITY = 159662430

def entity_inner(plain):
    import id_census
    objs = objects_of(plain)
    if not objs or objs[0]['type'] != ENTITY:
        return None
    valid, _bulk = id_census._class_tables()
    tops = {o['uid'] for o in objs}
    return {u: c for u, c in id_census.inline_ids(objs[0]['body'], valid) if u not in tops}

def entity_inner_index(archive, cache=True):
    path = os.path.join(CACHE, f'entityinner_{_archive_key(archive)}.pkl')
    if cache and os.path.exists(path):
        return pickle.load(open(path, 'rb'))
    index = {}
    with Forge(archive) as f:
        for r in f.records:
            raw = f.read(r)
            if not r.name or raw[4:8] != b'3\xaa\xfbW':
                continue
            try:
                inner = entity_inner(decompress(raw))
            except Exception:
                continue
            for u in inner or ():
                index.setdefault(u, set()).add(r.name)
    if cache:
        os.makedirs(CACHE, exist_ok=True)
        pickle.dump(index, open(path, 'wb'))
    return index

def entity_inner_shared(sources, renew, archives, skip_names=()):
    indexes = [entity_inner_index(a) for a in archives]
    skip = set(skip_names)
    out = set()
    for plain in sources.values():
        objs = objects_of(plain)
        if not objs or objs[0]['type'] != ENTITY or objs[0]['uid'] not in renew:
            continue
        kept_bodies = [o['body'] for o in objs if o['uid'] not in renew]
        for u in entity_inner(plain):
            if any((n not in skip for ix in indexes for n in ix.get(u, ()))) and (not any((struct.pack('<I', u) in b for b in kept_bodies))):
                out.add(u)
    return out

def _nested_elsewhere_scan(archive, skip_names, candidates, nested_ids):
    found = set()
    with Forge(archive) as f:
        for r in f.records:
            if r.name in skip_names:
                continue
            raw = f.read(r)
            if raw[4:8] != b'3\xaa\xfbW':
                continue
            try:
                d = decompress(raw)
            except Exception:
                continue
            for x in candidates - found:
                bx = struct.pack('<I', x)
                i = d.find(bx)
                while i >= 0:
                    if i + 8 <= len(d) and struct.unpack_from('<I', d, i + 4)[0] in nested_ids:
                        found.add(x)
                        break
                    i = d.find(bx, i + 1)
    return found
RESERVED = frozenset()
RESERVED_RECORDS = frozenset()
FOREIGN = frozenset()

class reserving:

    def __init__(self, ids=(), records=(), foreign=()):
        self.ids, self.records, self.foreign = (frozenset(ids), frozenset(records), frozenset(foreign))

    def __enter__(self):
        global RESERVED, RESERVED_RECORDS, FOREIGN
        self.saved = (RESERVED, RESERVED_RECORDS, FOREIGN)
        RESERVED, RESERVED_RECORDS = (RESERVED | self.ids, RESERVED_RECORDS | self.records)
        FOREIGN = FOREIGN | self.foreign
        return self

    def __exit__(self, *exc):
        global RESERVED, RESERVED_RECORDS, FOREIGN
        RESERVED, RESERVED_RECORDS, FOREIGN = self.saved
        return False

class FreshUids:

    def __init__(self, seed, avoid_values, avoid_bytes):
        self.rng = random.Random(seed)
        self.avoid = set(avoid_values) | RESERVED
        self.blobs = avoid_bytes

    def take(self):
        while True:
            u = self.rng.randrange(268435456, 4026531840)
            if u in self.avoid:
                continue
            b = struct.pack('<I', u)
            if any((b in blob for blob in self.blobs)):
                continue
            self.avoid.add(u)
            return u
REF_PREFIXES = (b'\x01\x00', b'\x01\x01', b'\x01\x02', b'\x00\x01')
RAW_CLASSES = ('Mesh', 'TextureMap')
_raw_types = None

def raw_types():
    global _raw_types
    if _raw_types is None:
        import exe_registry
        classes, _ = exe_registry.load(exe_registry.AC3)
        _raw_types = {i for i, n in classes.items() if n in RAW_CLASSES}
    return _raw_types
_valid = None

def _valid_class_ids():
    global _valid
    if _valid is None:
        import exe_registry
        import own_entity
        classes, _ = exe_registry.load(exe_registry.AC3)
        _valid = frozenset((c for c in classes if own_entity.is_class_id(c, classes)))
    return _valid

def id_sites(body, u):
    valid = _valid_class_ids()
    pat, out = (struct.pack('<I', u), [])
    i = body.find(pat)
    while i >= 0:
        if i == 2 or body[i - 2:i] in REF_PREFIXES or (i + 8 <= len(body) and struct.unpack_from('<I', body, i + 4)[0] in valid):
            out.append(i)
        i = body.find(pat, i + 1)
    return out
CLOTH_NESTED = {'Cloth': ('MeshBone', 'ClothState', 'ClothLOD'), 'LiteRagdoll': ('LiteRagdollCapsule',), 'ClothSettings': ()}
_cloth_types = None

def cloth_types():
    global _cloth_types
    if _cloth_types is None:
        import exe_registry
        classes, _ = exe_registry.load(exe_registry.AC3)
        ids = {}
        for i, n in classes.items():
            ids.setdefault(n, set()).add(i)
        _cloth_types = {}
        for owner, nested in CLOTH_NESTED.items():
            inner = frozenset((i for n in nested for i in ids.get(n, ())))
            for i in ids.get(owner, ()):
                _cloth_types[i] = inner
    return _cloth_types

def cloth_sites(body, u, nested_classes):
    pat, out = (struct.pack('<I', u), [])
    i = body.find(pat)
    while i >= 0:
        if i == 2 or (i + 8 <= len(body) and struct.unpack_from('<I', body, i + 4)[0] in nested_classes):
            out.append(i)
        i = body.find(pat, i + 1)
    return out

def cloth_nested_ids(body, nested_classes):
    out = []
    for p in range(6, len(body) - 7):
        x, c = struct.unpack_from('<II', body, p)
        if c in nested_classes and x >= 65536 and (x not in out):
            out.append(x)
    return out

def remap_cloth(body, mapping, refs, nested_classes):
    b, n = (bytearray(body), 0)
    for old, new in mapping.items():
        if old == new:
            continue
        if old in refs:
            ob, nb = (struct.pack('<I', old), struct.pack('<I', new))
            i = b.find(ob)
            while i >= 0:
                b[i:i + 4] = nb
                n += 1
                i = b.find(ob, i + 4)
        else:
            for i in cloth_sites(body, old, nested_classes):
                struct.pack_into('<I', b, i, new)
                n += 1
    return (bytes(b), n)

def remap_at_sites(body, mapping):
    b, n = (bytearray(body), 0)
    for old, new in mapping.items():
        if old != new:
            for i in id_sites(body, old):
                struct.pack_into('<I', b, i, new)
                n += 1
    return (bytes(b), n)

def remap_payload(plain, mapping, renew):
    objs = objects_of(plain)
    repl, count = ({}, 0)
    cloth, refs = (cloth_types(), {o['uid'] for o in objs})
    for pos, o in enumerate(objs):
        if o['uid'] not in renew:
            continue
        if o['type'] in raw_types():
            body, n = remap_at_sites(o['body'], mapping)
            count += n
            repl[pos] = build_object(o['type'], o['body_size'], o['name'], body)
            continue
        if o['type'] in cloth:
            body, n = remap_cloth(o['body'], mapping, refs, cloth[o['type']])
            count += n
            repl[pos] = build_object(o['type'], o['body_size'], o['name'], body)
            continue
        body = bytearray(o['body'])
        for old, new in mapping.items():
            ob, nb = (struct.pack('<I', old), struct.pack('<I', new))
            i = body.find(ob)
            while i >= 0:
                body[i:i + 4] = nb
                count += 1
                i = body.find(ob, i + 4)
        repl[pos] = build_object(o['type'], o['body_size'], o['name'], bytes(body))
    return (rebuild(plain, repl), count)

def rename_objects(plain, rename):
    objs = objects_of(plain)
    repl = {}
    for pos, o in enumerate(objs):
        new_name = rename(o['name'])
        if new_name != o['name']:
            repl[pos] = build_object(o['type'], o['body_size'], new_name, o['body'])
    return rebuild(plain, repl) if repl else plain

def clone_payloads(sources, rename, pristine_archive, class_names, seed, extra_avoid=(), avoid_uids=(), object_names='same', keep_shared=True, external=None, always=(), inner_from=()):
    renew, kept, nested = plan(sources, pristine_archive, class_names, keep_shared, always)
    blobs = list(sources.values()) + list(extra_avoid)
    fresh = FreshUids(seed, set(avoid_uids), blobs)
    pinned = {u: (external or {})[u] for u in always if u in (external or {})}
    mapping = {u: pinned[u] if u in pinned else fresh.take() for u in sorted(renew | nested)}
    if inner_from:
        inner = entity_inner_shared(sources, renew, inner_from, {rename(n) for n in sources}) - set(mapping)
        mapping.update({u: fresh.take() for u in sorted(inner)})
    both = dict(external or {})
    both.update(mapping)
    out = {}
    for name, plain in sources.items():
        new_plain, n = remap_payload(plain, both, renew)
        obj_rename = rename if object_names == 'same' else object_names
        if obj_rename is not None:
            new_plain = rename_objects(new_plain, obj_rename)
        objs = objects_of(new_plain)
        out[rename(name)] = (new_plain, objs[0]['uid'], len(objs), n)
    return (mapping, kept, out)

def fresh_record_uids(taken, seed):
    rng = random.Random(seed)
    taken = set(taken) | RESERVED_RECORDS
    while True:
        u = rng.getrandbits(64)
        if u not in taken:
            taken.add(u)
            yield u

def clone_files(image, sources, rename, pristine_archive, class_names, seed, extra_avoid=(), avoid_uids=(), object_names='same', keep_shared=True, external=None, always=(), inner_from=()):
    plains = {n: p for n, (_i, p) in sources.items()}
    mapping, kept, copies = clone_payloads(plains, rename, pristine_archive, class_names, seed, extra_avoid, avoid_uids, object_names, keep_shared, external, always, inner_from)
    with Forge(pristine_archive) as f:
        record_uids = fresh_record_uids({r.uid for r in f.records}, seed + 1)
    report = []
    for name, (template_index, _plain) in sources.items():
        new_plain, root, n_objs, n = copies[rename(name)]
        bad = directory_mismatches(new_plain)
        assert not bad, f'{rename(name)}: directory uids do not follow the bodies: {bad[:3]}'
        payload = compress(new_plain, first_chunk=directory_size(new_plain))
        assert decompress(payload) == new_plain
        index = add_file(image, rename(name), payload, next(record_uids), template_index=template_index, entry_key=root)
        report.append((rename(name), index, root, n_objs, n))
    return (mapping, kept, report)
