"""forgelib/id_census.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import bisect
import hashlib
import os
import re
import struct
import sys
from array import array
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import record_cache
from datafile import decompress
from forge import Forge
from objects import walk, identity_of, ObjectError, OBJECT_HEADER_SIZE
from paths import AC3_SOURCE
PAYLOAD_MAGIC = b'3\xaa\xfbW'
BOOTSTRAP = 'Game Bootstrap Settings'
ARCHIVES = ('DataPC.forge', 'DataPC_extra.forge')
BULK = ('Mesh', 'TextureMap', 'Animation', 'MaterialTemplate')
STORE = 'ids_v4'
UNMAPPED = range(4026531840, 4160749568)
UNMAPPED_STORE = 'unmapped_v1'
_classes = {}

def _class_tables():
    if not _classes:
        import numpy as np
        import exe_registry
        import own_entity
        classes, _ = exe_registry.load(exe_registry.AC3)
        _classes['valid'] = np.array(sorted((c for c in classes if own_entity.is_class_id(c, classes))), dtype=np.uint32)
        _classes['bulk'] = {i for i, n in classes.items() if n in BULK}
    return (_classes['valid'], _classes['bulk'])

def _bound_classes():
    if 'bound' not in _classes:
        import zlib
        import exe_registry
        from exe_classes import PE
        valid, _bulk = _class_tables()
        classes, _ = exe_registry.load(exe_registry.AC3)
        pe = PE(exe_registry.AC3)
        names = exe_registry.string_table(pe)
        sized = set()
        for sec, _va, _vsize, raw, rawsize in pe.sections:
            if sec not in ('.data', '.rdata'):
                continue
            for off in range(raw, min(raw + rawsize, len(pe.data)) - 16, 4):
                if struct.unpack_from('<I', pe.data, off)[0] in names:
                    _parent, cid, size = struct.unpack_from('<III', pe.data, off + 4)
                    if 0 < size <= 65536:
                        sized.add(cid)
        _classes['bound'] = {c for c in valid.tolist() if c in sized or zlib.crc32(classes[c].encode('latin-1')) & 4294967295 == c}
    return _classes['bound']

def inline_ids(body, valid):
    import numpy as np
    out = set()
    for k in range(4):
        n = (len(body) - k) // 4
        if n < 2:
            continue
        a = np.frombuffer(body, dtype='<u4', count=n, offset=k)
        hit = np.isin(a[1:], valid)
        u, c = (a[:-1][hit], a[1:][hit])
        keep = (u >= 65536) & (u & 65535 != 0)
        out.update(zip(u[keep].tolist(), c[keep].tolist()))
    return out

def payload_entries(plain):
    try:
        return (walk(plain), (0, 0))
    except ObjectError:
        pass
    count = struct.unpack_from('<H', plain, 0)[0] if len(plain) >= 2 else 0
    t0 = 2 + count * 10 + 4
    if count == 0 or t0 > len(plain):
        raise ObjectError(f'no directory of {count} in {len(plain)} bytes')
    t1 = o = t0 + struct.unpack_from('<I', plain, t0 - 4)[0]
    out = []
    for i in range(count):
        serial, size, _filter = struct.unpack_from('<IIH', plain, 2 + i * 10)
        out.append((o, size, serial))
        o += size
    if o != len(plain):
        raise ObjectError(f'directory accounts for {o} of {len(plain)} bytes')
    return (out, (t0, t1))

def unmapped_objects(plain):
    import numpy as np
    known = _bound_classes()
    ents, (t0, t1) = payload_entries(plain)
    out = [(pos, 'directory', serial, struct.unpack_from('<I', plain, off)[0]) for pos, (off, _size, serial) in enumerate(ents) if serial in UNMAPPED]
    starts = [off for off, _size, _serial in ents]
    b = np.frombuffer(plain, dtype=np.uint8)
    n = len(b) - 9
    for lo in range(0, max(n, 0), 1 << 24):
        hi = min(n, lo + (1 << 24))
        hit = b[lo + 5:hi + 5] & 248 == 240
        if not hit.any():
            continue
        kind = b[lo:hi]
        hit &= (b[lo + 1:hi + 1] == 1) & ((kind == 0) | (kind == 4))
        for p in (np.flatnonzero(hit) + lo).tolist():
            u, c = struct.unpack_from('<II', plain, p + 2)
            if c not in known:
                continue
            if t0 <= p < t1:
                out.append((None, 'inline', u, c))
                continue
            pos = bisect.bisect_right(starts, p) - 1
            if pos >= 0 and u != ents[pos][2]:
                out.append((pos, 'inline', u, c))
    return out

def entries_of(plain):
    import clone
    valid, bulk = _class_tables()
    cloth = clone.cloth_types()
    out = []
    for off, size, _serial in walk(plain):
        raw = plain[off:off + size]
        ident = identity_of(raw)
        typ, _bs, nlen = struct.unpack_from('<III', raw, 0)
        name = raw[OBJECT_HEADER_SIZE:OBJECT_HEADER_SIZE + nlen].decode('latin-1')
        body = raw[OBJECT_HEADER_SIZE + nlen:]
        inline = () if typ in bulk else sorted((p for p in inline_ids(body, valid) if p[0] != ident and (typ not in cloth or p[1] in cloth[typ])))
        out.append((ident, typ, hashlib.sha1(body).digest()[:8], name, inline))
    return out

def census(archive):
    store = record_cache.Store(STORE)
    out = []
    with Forge(archive) as f:
        for r in f.records:
            raw = f.read(r)
            if raw[4:8] != PAYLOAD_MAGIC:
                continue
            key = record_cache.digest(raw)
            if key not in store:
                try:
                    items = entries_of(decompress(raw))
                except Exception:
                    items = []
                store.put(key, [(i, t, d, n, array('I', [x for p in inl for x in p]).tobytes()) for i, t, d, n, inl in items])
            items = []
            for i, t, d, n, b in store.get(key):
                a = array('I', b)
                items.append((i, t, d, n, list(zip(a[0::2], a[1::2]))))
            out.append((r.name or '', items))
    store.save()
    return out

def unmapped_census(archive):
    store = record_cache.Store(UNMAPPED_STORE)
    out = []
    with Forge(archive) as f:
        for r in f.records:
            raw = f.read(r)
            if raw[4:8] != PAYLOAD_MAGIC:
                continue
            key = record_cache.digest(raw)
            if key not in store:
                try:
                    hits = unmapped_objects(decompress(raw))
                except Exception:
                    hits = []
                store.put(key, hits)
            if store.get(key):
                out.append((r.name or '', store.get(key)))
    store.save()
    return out

def his_record(n):
    return re.compile(f'(AC3MP_ID{n}J?_.*|ID{n}_.*|AC3MP_(Dynamic_)?Head_{n}J_.*|KillAnimTree_SkinId{n})')

def his_boot_object(n):
    return re.compile(f'(CharacterBuildingKit_AC3MP_ID{n}|AC3MP_ID{n}|(Class|Basic)Pack_ID{n}|Char{n}_.*|{n}\\d{{3,4}})')

def split(folder, n):
    rec_rx, obj_rx = (his_record(n), his_boot_object(n))
    his, others, classes = ({}, {}, {})
    for arch in ARCHIVES:
        path = os.path.join(folder, arch)
        if not os.path.exists(path):
            continue
        for rname, items in census(path):
            rec_his = rec_rx.fullmatch(rname) is not None
            where = f"{arch[:-len('.forge')]}/{rname}"
            for ident, typ, dig, oname, inline in items:
                mine = rec_his or (rname == BOOTSTRAP and obj_rx.fullmatch(oname) is not None)
                into = his if mine else others
                if ident is not None:
                    into.setdefault(ident, set()).add((where, ident, dig, None))
                    classes.setdefault(ident, set()).add(typ)
                for u, c in inline:
                    into.setdefault(u, set()).add((where, ident, dig, c))
    return (his, others, classes)

def pristine_ids():
    out = set()
    for arch in ARCHIVES:
        for _r, items in census(os.path.join(AC3_SOURCE, arch)):
            for ident, _t, _d, _n, inline in items:
                if ident is not None:
                    out.add(ident)
                out.update((u for u, _c in inline))
    return out

def reserved(base_dir, n):
    his, others, _cl = split(base_dir, n)
    stock = pristine_ids()
    return ((set(others) | stock) - (set(his) - stock), set(others) - stock - set(his))

def definers(uses, classes, u):
    own = {w for w, _i, _d, c in uses if c is None}
    return {(ident, dig) for w, ident, dig, c in uses if c is None or (c not in classes.get(u, ()) and w not in own)}

def clashes(folder, n, allow=()):
    problems, notes = ([], [])
    for arch in ARCHIVES:
        path = os.path.join(folder, arch)
        for rname, hits in unmapped_census(path) if os.path.exists(path) else ():
            for pos, how, u, _c in hits:
                problems.append(f"id 0x{u:08X} ({how}, {arch[:-len('.forge')]}/{rname} #{pos}) is in 0xF0000000 - 0xF7FFFFFF, which AC3's handle manager never maps - it loads and never resolves")
    his, others, classes = split(folder, n)
    stock = pristine_ids()
    for u in sorted(set(his) - stock):
        uses = his[u] | others.get(u, set())
        found = definers(uses, classes, u)
        if len(found) <= 1:
            continue
        where = sorted({w for w, *_ in uses})
        line = f"id 0x{u:08X} defined by {len(found)} different objects ({('another owner too' if u in others else 'all his')}): {where[:4]}"
        (notes if u in allow else problems).append(line)
    return (problems, notes)

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    folder, n = (argv[0], int(argv[1]))
    his, _others, _cl = split(folder, n)
    new = set(his) - pristine_ids()
    print(f'persona {n} in {folder}: {len(his)} ids in his files and objects, {len(new)} new to AC3')
    problems, _notes = clashes(folder, n)
    for p in problems:
        print('  CLASH', p)
    print(f'{len(problems)} clashes')
    return 1 if problems else 0
if __name__ == '__main__':
    sys.exit(main())
