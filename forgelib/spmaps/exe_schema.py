"""forgelib/spmaps/exe_schema.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import json
import os
import struct
import sys
import zlib
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import paths
from codec27 import Property, mp_schema
OUT = os.path.join(ROOT, 'out', 'spmaps')
EXE_DIR = paths.AC3_SINGLEPLAYER
REC = 32
IMAGE = (4194304, 67108864)

def record(b, o):
    if o < 0 or o + REC > len(b):
        return None
    flags, name, obj, tword, _offw, get, put, zero = struct.unpack_from('<8I', b, o)
    if not name or zero or flags >= 268435456:
        return None
    if any((v and (not IMAGE[0] <= v < IMAGE[1]) for v in (get, put))):
        return None
    return Property(flags, name, tword << 32 | obj)

def is_bits(p):
    return p.packed_type >> 32 & 12582912 == 12582912

def table_at(b, pe, o, limit=600):
    out, first = ([], pe.va(o))
    while len(out) < limit:
        if out and struct.unpack_from('<I', b, o)[0] == first:
            break
        p = record(b, o)
        if p is None:
            break
        out.append((pe.va(o), p))
        o += REC
    return out

def declared(b, pe, members_va, p1):
    o = pe.off(p1)
    if o is None:
        return None
    out, seen = ([], set())
    for i in range(3, 700):
        v = struct.unpack_from('<I', b, o + 4 * i)[0]
        if v in members_va and v not in seen:
            out.append(v)
            seen.add(v)
        elif v and pe.off(v) is None:
            break
        elif v and v not in members_va and (len(out) == len(members_va)):
            break
    return out if len(out) == len(members_va) else None

def anchors(S):
    out = {}
    for t in S.types_by_hash.values():
        for i, p in enumerate(t.properties):
            if is_bits(p):
                out[t.type_hash] = t.properties[i - 1].name_hash if i else 0
                break
    return out

def serial_order(props, anchor):
    bits = [p for p in props if is_bits(p)]
    rest = [p for p in props if not is_bits(p)]
    if not bits or anchor is None:
        return props
    if anchor == 0:
        return bits + rest
    at = next((i for i, p in enumerate(rest) if p.name_hash == anchor), None)
    if at is None:
        return props
    return rest[:at + 1] + bits + rest[at + 1:]

def find_class(b, type_hash, base_hash):
    pat = struct.pack('<II', base_hash, type_hash)
    hits, k = ([], b.find(pat))
    while k >= 0:
        hits.append(k)
        k = b.find(pat, k + 1)
    return hits

def read_exe(exe, S, A=None):
    from exe_classes import PE
    pe = PE(os.path.join(EXE_DIR, exe))
    b = pe.data
    A = anchors(S) if A is None else A
    out = {}
    for t in S.types_by_hash.values():
        best = None
        for k in find_class(b, t.type_hash, t.base_type_hash):
            table, p1 = struct.unpack_from('<II', b, k - 12)
            o = pe.off(table) if table else None
            rows = table_at(b, pe, o) if o is not None else []
            if best is None or len(rows) > len(best[0]):
                best = (rows, p1)
        if best is None:
            continue
        rows, p1 = best
        by = dict(rows)
        order = declared(b, pe, by, p1) if p1 and rows else None
        if order is not None:
            out[t.type_hash] = (t.base_type_hash, [by[v] for v in order], 'declared')
        else:
            out[t.type_hash] = (t.base_type_hash, serial_order([p for _v, p in rows], A.get(t.type_hash)), 'table')
    return (out, pe)

def check():
    S = mp_schema()
    got, _pe = read_exe('AC3MP.exe', S)
    same = differ = missing = 0
    bad = []
    for t in S.types_by_hash.values():
        if t.type_hash not in got:
            missing += 1
            continue
        ser = lambda L: [(p.flags, p.name_hash, p.packed_type) for p in L if p.flags & 33554432]
        mine, theirs = (ser(got[t.type_hash][1]), ser(t.properties))
        if mine == theirs:
            same += 1
        else:
            differ += 1
            if len(bad) < 15:
                bad.append((S.name_of(t.type_hash), len(theirs), len(mine), [S.name_of(n) for _f, n, _p in theirs][:6], [S.name_of(n) for _f, n, _p in mine][:6]))
    print(f'AC3MP.exe vs AC3_MP.schema, serialized properties in order: {same} classes identical, {differ} differ, {missing} not found (of {len(S.types_by_hash)})')
    for row in bad:
        print('  ', row)
    return differ == 0

def key(props):
    return [(p.flags, p.name_hash, p.packed_type) for p in props]

def sp_tables():
    path = os.path.join(OUT, 'AC3_SP_props.json')
    if os.path.exists(path):
        rows = json.load(open(path, encoding='utf-8'))
        return {int(h, 16): [Property(*r) for r in v['props']] for h, v in rows['changed'].items()}
    S = mp_schema()
    mp, _ = read_exe('AC3MP.exe', S)
    sp, _ = read_exe('AC3SP.exe', S)
    A = anchors(S)
    changed, rows, missing = ({}, {}, [])
    for h, t in S.types_by_hash.items():
        if h not in sp:
            missing.append(S.name_of(h))
            continue
        if h in mp and key(mp[h][1]) == key(sp[h][1]):
            continue
        props = serial_order(sp[h][1], A.get(h))
        changed[h] = props
        rows[f'{h:08x}'] = {'name': S.name_of(h), 'mp': [S.name_of(p.name_hash) for p in t.properties if p.flags & 33554432], 'sp': [S.name_of(p.name_hash) for p in props if p.flags & 33554432], 'props': key(props)}
    os.makedirs(OUT, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({'changed': rows, 'not_in_sp': missing}, fh, indent=1)
    return changed

def main():
    S = mp_schema()
    ch = sp_tables()
    rows = json.load(open(os.path.join(OUT, 'AC3_SP_props.json'), encoding='utf-8'))
    print(f"AC3SP.exe: {len(ch)} of {len(S.types_by_hash)} classes laid out differently from AC3MP.exe; {len(rows['not_in_sp'])} MP classes not in SP")
    for h, v in sorted(rows['changed'].items(), key=lambda kv: kv[1]['name']):
        if v['mp'] == v['sp']:
            continue
        gone = [n for n in v['mp'] if n not in v['sp']]
        new = [n for n in v['sp'] if n not in v['mp']]
        print(f"  {v['name']:40} serialized {len(v['mp'])} -> {len(v['sp'])}  new {new[:4]}  gone {gone[:4]}" + ('  (order)' if not gone and (not new) else ''))
if __name__ == '__main__':
    if '--check' in sys.argv:
        sys.exit(0 if check() else 1)
    main()
