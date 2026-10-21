"""forgelib/ac4/clutter.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT, os.path.join(ROOT, 'maps')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
CC, DMI, CS, QT, LOD = (556977262, 1854372666, 4103823639, 2469854347, 3778230358)
MIMI, VISUAL, ENTITY = (2572942325, 3966078249, 159662430)
P = lambda v: struct.pack('<I', v)
CC_HEAD = P(CC) + bytes.fromhex('01 03 00 00 00 00 00 00')
DMI_MID = bytes.fromhex('03 00 00 00 00 00 01 00')
SETTINGS_LEN = 53

def u32(b, at):
    return struct.unpack_from('<I', b, at)[0]

def parse_tree(b, o):
    assert u32(b, o) == QT, (o, b[o:o + 8].hex())
    lo = struct.unpack_from('<3f', b, o + 4)
    hi = struct.unpack_from('<3f', b, o + 16)
    n = u32(b, o + 28)
    idx = list(struct.unpack_from(f'<{n}H', b, o + 32))
    o += 32 + 2 * n
    kids = []
    for _ in range(2):
        if b[o] == 3:
            kids.append(None)
            o += 1
        else:
            assert b[o] == 0, (o, b[o])
            own = u32(b, o + 1)
            node, o = parse_tree(b, o + 5)
            kids.append((own,) + node[1:])
    return ((None, lo, hi, idx, kids), o)

def parse_ac3(b, at):
    assert b[at:at + 12] == CC_HEAD, b[at:at + 16].hex(' ')
    c = {'dmi_own': u32(b, at + 12)}
    o = at + 16
    assert u32(b, o) == DMI and b[o + 4] == 1 and (b[o + 9:o + 17] == DMI_MID) and (u32(b, o + 21) == 0), b[o:o + 29].hex(' ')
    c['dynmesh'] = u32(b, o + 5)
    c['V'] = b[o + 17:o + 21]
    c['settings_own'] = u32(b, o + 25)
    o += 29
    assert u32(b, o) == CS
    c['settings'] = b[o:o + SETTINGS_LEN]
    o += SETTINGS_LEN
    n = u32(b, o)
    c['orient'] = list(struct.unpack_from(f'<{n}H', b, o + 4))
    o += 4 + 2 * n
    assert u32(b, o) == 3 * n
    c['pos'] = list(struct.unpack_from(f'<{3 * n}h', b, o + 4))
    o += 4 + 6 * n
    c['v1'] = b[o:o + 4]
    o += 4
    assert u32(b, o) == n
    c['variant'] = b[o + 4:o + 4 + n]
    o += 4 + n
    assert u32(b, o) == n
    c['tint'] = list(struct.unpack_from(f'<{n}I', b, o + 4))
    o += 4 + 4 * n
    assert u32(b, o) == n
    c['scale'] = list(struct.unpack_from(f'<{n}H', b, o + 4))
    o += 4 + 2 * n
    c['E'] = b[o:o + 4]
    o += 4
    assert b[o] == 0
    own = u32(b, o + 1)
    root, o = parse_tree(b, o + 5)
    c['tree'] = (own,) + root[1:]
    return (c, o)

def encode_tree(node):
    own, lo, hi, idx, kids = node
    out = [bytes((0,)), P(own), P(QT), struct.pack('<3f', *lo), struct.pack('<3f', *hi), P(len(idx)), struct.pack(f'<{len(idx)}H', *idx)]
    for k in kids:
        out.append(bytes((3,)) if k is None else encode_tree(k))
    return b''.join(out)

def encode_ac3(c):
    n = len(c['orient'])
    return b''.join([CC_HEAD, P(c['dmi_own']), P(DMI), bytes((1,)), P(c['dynmesh']), DMI_MID, c['V'], P(0), P(c['settings_own']), c['settings'], P(n), struct.pack(f'<{n}H', *c['orient']), P(3 * n), struct.pack(f'<{3 * n}h', *c['pos']), c['v1'], P(n), bytes(c['variant']), P(n), struct.pack(f'<{n}I', *c['tint']), P(n), struct.pack(f'<{n}H', *c['scale']), c['E'], encode_tree(c['tree'])])

def tree_nodes(node, out=None):
    out = [] if out is None else out
    out.append(node)
    for k in node[4]:
        if k is not None:
            tree_nodes(k, out)
    return out

def entity_pos(body):
    e = body.find(P(ENTITY))
    m = struct.unpack_from('<16f', body, e + 4)
    ident = all((abs(m[i] - (1.0 if i in (0, 5, 10, 15) else 0.0)) < 1e-06 for i in range(12)))
    return (m[12:15], ident)

def count_at(body):
    return body.find(P(ENTITY)) + 4 + 64 + 1

def visual_tail(body):
    import world
    at, last = (0, None)
    for name, s in world.segments(body):
        if name == 'MeshInstanceMaterialInfo':
            last = at
        at += len(s)
    assert last is not None, 'no MeshInstanceMaterialInfo'
    v = last + 22
    assert body[last + 4] == 2 and body[last + 14:last + 16] == bytes.fromhex('01 00') and (body[v - 2] == 1) and (u32(body, v + 4) == 0), body[last:v + 10].hex(' ')
    return (v + 8, v)

def insert(body, chunk_fields, cc_own):
    k, v = visual_tail(body)
    chunk = encode_ac3(dict(chunk_fields, V=body[v:v + 4]))
    out = bytearray(body[:k] + bytes.fromhex('04 00') + P(cc_own) + chunk + body[k:])
    ca = count_at(body)
    struct.pack_into('<I', out, ca, u32(body, ca) + 1)
    return bytes(out)

def extract(body):
    k, _v = visual_tail(body)
    if body[k:k + 2] != bytes.fromhex('04 00') or u32(body, k + 6) != CC:
        return None
    c, _end = parse_ac3(body, k + 6)
    return (c, u32(body, k + 2))

def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]

def quad_levels(lo, hi, S):
    L = 0
    while min(hi[0] - lo[0], hi[1] - lo[1]) / 2 ** (L + 1) >= S:
        L += 1
    return L

def build_tree(pts, lo, hi, S, ids):
    depth = 2 * quad_levels(lo, hi, S)

    def node(lo, hi, sel, d):
        own = next(ids)
        if d == depth:
            return (own, lo, hi, sorted(sel), [None, None])
        ax = d % 2
        mid = f32((lo[ax] + hi[ax]) / 2)
        a_hi = tuple((mid if j == ax else hi[j] for j in range(3)))
        b_lo = tuple((mid if j == ax else lo[j] for j in range(3)))
        a = node(lo, a_hi, [i for i in sel if pts[i][ax] < mid], d + 1)
        b = node(b_lo, hi, [i for i in sel if pts[i][ax] >= mid], d + 1)
        return (own, lo, hi, [], [a, b])
    return node(tuple(lo), tuple(hi), list(range(len(pts))), 0)

def check_tree(tree, pts):
    seen, outside = ({}, 0)
    for own, lo, hi, idx, kids in tree_nodes(tree):
        for i in idx:
            seen[i] = seen.get(i, 0) + 1
            if not all((lo[j] <= pts[i][j] <= hi[j] for j in range(3))):
                outside += 1
    return (sum((1 for v in seen.values() if v == 1)), sum((v - 1 for v in seen.values())), outside, len(pts) - len(seen))

def decode_ac4(body):
    k = body.find(P(CS))
    o = k + 4 + 1 + 16
    for i in range(5):
        assert body[o:o + 2] == bytes.fromhex('04 00') and u32(body, o + 6) == LOD, (i, body[o:o + 12].hex(' '))
        o += 56
    out = {}

    def arr(sz, fmt):
        nonlocal o
        n = u32(body, o)
        v = list(struct.unpack_from(f'<{n}{fmt}', body, o + 4))
        o += 4 + n * sz
        return v
    out['orient'] = arr(2, 'H')
    out['pos'] = arr(2, 'h')
    out['variant'] = arr(1, 'B')
    out['extra'] = arr(1, 'B')
    out['tint'] = arr(4, 'I')
    out['scale'] = arr(2, 'H')
    out['u1'] = u32(body, o)
    out['b0'] = body[o + 4]
    out['f64'], out['f0'] = struct.unpack_from('<2f', body, o + 5)
    o += 13
    out['x1'] = arr(4, 'I')
    out['vb'] = arr(4, 'I')
    out['ib'] = arr(2, 'H')
    n = u32(body, o)
    o += 4 + 6 * n
    out['E'] = struct.unpack_from('<f', body, o)[0]
    out['E_raw'] = body[o:o + 4]
    out['entity_pos'], out['identity'] = entity_pos(body)
    n = len(out['orient'])
    assert len(out['pos']) == 3 * n and len(out['scale']) == n and (len(out['tint']) == n)
    return out

def world_points(pos, E, origin):
    E = struct.unpack('<f', E)[0] if isinstance(E, bytes) else E
    return [tuple((f32(origin[j] + pos[3 * i + j] / 32767.0 * E) for j in range(3))) for i in range(len(pos) // 3)]
SUBMESH, UVCH, MESHPRIM, MID = (1465245311, 3182264947, 2775812079, 186463425)

def _arr(b, o, fmt, size):
    n = u32(b, o)
    if size == 12:
        v = [struct.unpack_from('<3f', b, o + 4 + 12 * i) for i in range(n)]
    elif size == 8:
        v = [struct.unpack_from('<2f', b, o + 4 + 8 * i) for i in range(n)]
    else:
        v = list(struct.unpack_from(f'<{n}{fmt}', b, o + 4))
    return (v, o + 4 + n * size)

def _enc(v, fmt, size):
    if size in (8, 12):
        return P(len(v)) + b''.join((struct.pack(f'<{size // 4}f', *x) for x in v))
    return P(len(v)) + struct.pack(f'<{len(v)}{fmt}', *v)

def parse_submesh(b, o):
    assert u32(b, o) == SUBMESH, b[o:o + 8].hex(' ')
    s = {'a': u32(b, o + 4)}
    o += 8
    s['idx'], o = _arr(b, o, 'I', 4)
    assert u32(b, o) == 0 and u32(b, o + 4) == 0, 'SubMesh: +0x18 / the 0x325C97EE list not empty'
    o += 8
    s['tri_u32'], o = _arr(b, o, 'I', 4)
    s['tri_u8'], o = _arr(b, o, 'B', 1)
    for k in ('pos', 'nrm', 'tan', 'bin'):
        s[k], o = _arr(b, o, 'f', 12)
    assert u32(b, o) == 0 and u32(b, o + 4) == 0
    o += 8
    s['col'], o = _arr(b, o, 'I', 4)
    assert u32(b, o) == 0 and u32(b, o + 4) == 0 and (u32(b, o + 8) == 0) and (u32(b, o + 12) == 1), b[o:o + 16].hex(' ')
    o += 16
    s['uv_own'] = u32(b, o)
    assert u32(b, o + 4) == UVCH
    s['uv_a'], s['uv_b'] = (u32(b, o + 8), b[o + 12])
    o += 13
    s['uv'], o = _arr(b, o, 'f', 8)
    assert u32(b, o) == 0 and b[o + 4:o + 6] == bytes.fromhex('01 00')
    s['mat'] = u32(b, o + 6)
    s['flag'] = b[o + 10]
    o += 11
    if b[o] == 4:
        s['next'] = u32(b, o + 2)
        o += 6
    else:
        assert b[o] == 3
        s['next'] = None
        o += 1
    return (s, o)

def encode_submesh(s):
    return b''.join([P(SUBMESH), P(s['a']), _enc(s['idx'], 'I', 4), P(0), P(0), _enc(s['tri_u32'], 'I', 4), _enc(s['tri_u8'], 'B', 1), _enc(s['pos'], 'f', 12), _enc(s['nrm'], 'f', 12), _enc(s['tan'], 'f', 12), _enc(s['bin'], 'f', 12), P(0), P(0), _enc(s['col'], 'I', 4), P(0), P(0), P(0), P(1), P(s['uv_own']), P(UVCH), P(s['uv_a']), bytes((s['uv_b'],)), _enc(s['uv'], 'f', 8), P(0), bytes.fromhex('01 00'), P(s['mat']), bytes((s['flag'],)), bytes.fromhex('04 00') + P(s['next']) if s['next'] is not None else bytes((3,))])

def parse_mesh(b):
    assert u32(b, 6) == 1096652136 and b[19:21] == bytes.fromhex('04 00'), b[:25].hex(' ')
    m = {'uid': u32(b, 2), 'head': b[6:19], 'sm1': u32(b, 21)}
    s1, o = parse_submesh(b, 25)
    s2, o = parse_submesh(b, o)
    assert s1['next'] is not None and s2['next'] is None
    m['sub'] = (s1, s2)
    k = b.find(P(MESHPRIM), o)
    m['chead'] = b[o:k]
    m['record'] = list(struct.unpack_from('<7I', b, k + 4))
    vb = k + 32
    nv = u32(b, vb) // 20
    m['vertices'] = b[vb + 4:vb + 4 + 20 * nv]
    ib = vb + 4 + 20 * nv
    ni = u32(b, ib) // 2
    m['indices'] = list(struct.unpack_from(f'<{ni}H', b, ib + 4))
    m['tail'] = b[ib + 4 + 2 * ni:]
    return m

def compile_vertices(s):
    import math
    order, seen = ([], {})
    for v in s['idx']:
        if v not in seen:
            seen[v] = len(order)
            order.append(v)
    S = max(1, math.ceil(max((abs(c) for p in s['pos'] for c in p))))
    out = []
    for v in order:
        p, n, t, bn = (s['pos'][v], s['nrm'][v], s['tan'][v], s['bin'][v])
        tn = (t[1] * n[2] - t[2] * n[1], t[2] * n[0] - t[0] * n[2], t[0] * n[1] - t[1] * n[0])
        h = 1 if sum((tn[j] * bn[j] for j in range(3))) >= 0 else -1
        col = P(s['col'][v])
        out.append(struct.pack('<4h', *(int(c / S * 32767) for c in p), 8 * S * h) + bytes((math.floor(c * 127) + 127 for c in n)) + col[:1] + bytes((math.floor(c * 127) + 127 for c in t)) + col[:1] + struct.pack('<2h', *(int(c * 2047) for c in s['uv'][v])))
    return (b''.join(out), [seen[v] for v in s['idx']])

def encode_mesh(m):
    s1, s2 = m['sub']
    vb, ib = compile_vertices(s1)
    nv, ntri = (len(vb) // 20, len(ib) // 3)
    rec = list(m['record'])
    rec[2], rec[4] = (nv, ntri)
    tail = bytearray(m['tail'])
    k = tail.find(P(MID))
    struct.pack_into('<H', tail, k + 8, nv)
    tail = bytes(tail).replace(P(m.get('tail_mat', s1['mat'])), P(s1['mat']))
    return b''.join([bytes.fromhex('00 01'), P(m['uid']), m['head'], bytes.fromhex('04 00'), P(m['sm1']), encode_submesh(s1), encode_submesh(s2), m['chead'], P(MESHPRIM), struct.pack('<7I', *rec), P(len(vb)), vb, P(2 * len(ib)), struct.pack(f'<{len(ib)}H', *ib), tail])

def ac4_piece(a):
    vb = struct.pack(f"<{len(a['vb'])}I", *a['vb'])
    n = len(vb) // 40
    grp = [vb[40 * i + 28] for i in range(n)]
    per = grp.count(0)
    copies = n // per
    assert n == per * copies and all((grp[i] == i // per for i in range(n))), 'AC4 clutter buffer: groups not contiguous'
    assert all((vb[40 * i:40 * i + 28] + vb[40 * i + 32:40 * i + 40] == vb[40 * (i % per):40 * (i % per) + 28] + vb[40 * (i % per) + 32:40 * (i % per) + 40] for i in range(n))), 'AC4 clutter buffer: copies differ'
    tris = [tuple(a['ib'][k:k + 3]) for k in range(0, len(a['ib']), 3)]
    g0 = [t for t in tris if t[0] < per]
    for c in range(1, copies):
        assert [tuple((v - per * c for v in t)) for t in tris if per * c <= t[0] < per * (c + 1)] == g0, c
    return {'pos': [struct.unpack_from('<3f', vb, 40 * i) for i in range(per)], 'nrm': [struct.unpack_from('<3f', vb, 40 * i + 12) for i in range(per)], 'rgba': [vb[40 * i + 24:40 * i + 28] for i in range(per)], 'uv': [struct.unpack_from('<2f', vb, 40 * i + 32) for i in range(per)], 'idx': [v for t in g0 for v in t], 'copies': copies}

def tangents(pos, nrm, uv, idx):
    import math
    n = len(pos)
    T, B = ([[0.0] * 3 for _ in range(n)], [[0.0] * 3 for _ in range(n)])
    for k in range(0, len(idx), 3):
        i0, i1, i2 = idx[k:k + 3]
        e1 = [pos[i1][j] - pos[i0][j] for j in range(3)]
        e2 = [pos[i2][j] - pos[i0][j] for j in range(3)]
        du1, dv1 = (uv[i1][0] - uv[i0][0], uv[i1][1] - uv[i0][1])
        du2, dv2 = (uv[i2][0] - uv[i0][0], uv[i2][1] - uv[i0][1])
        d = du1 * dv2 - du2 * dv1
        if abs(d) < 1e-12:
            continue
        r = 1.0 / d
        t = [(e1[j] * dv2 - e2[j] * dv1) * r for j in range(3)]
        bb = [(e2[j] * du1 - e1[j] * du2) * r for j in range(3)]
        for i in (i0, i1, i2):
            for j in range(3):
                T[i][j] += t[j]
                B[i][j] += bb[j]
    tan, bin_ = ([], [])
    for i in range(n):
        nn = nrm[i]
        t = T[i]
        dt = sum((t[j] * nn[j] for j in range(3)))
        t = [t[j] - nn[j] * dt for j in range(3)]
        ln = math.sqrt(sum((c * c for c in t)))
        if ln < 1e-09:
            a_ = (1.0, 0.0, 0.0) if abs(nn[0]) < 0.9 else (0.0, 1.0, 0.0)
            t = [a_[1] * nn[2] - a_[2] * nn[1], a_[2] * nn[0] - a_[0] * nn[2], a_[0] * nn[1] - a_[1] * nn[0]]
            ln = math.sqrt(sum((c * c for c in t)))
        t = [c / ln for c in t]
        tn = [t[1] * nn[2] - t[2] * nn[1], t[2] * nn[0] - t[0] * nn[2], t[0] * nn[1] - t[1] * nn[0]]
        h = 1.0 if sum((tn[j] * B[i][j] for j in range(3))) >= 0 else -1.0
        tan.append(tuple((f32(c) for c in t)))
        bin_.append(tuple((f32(h * c) for c in tn)))
    return (tan, bin_)

def author_mesh(tpl_body, uid, ids, mat, piece):
    m = parse_mesh(tpl_body)
    t1, _t2 = m['sub']
    assert set(t1['tri_u32']) == {1} and set(t1['tri_u8']) == {3}, 'template per-triangle values not uniform'
    tan, bin_ = tangents(piece['pos'], piece['nrm'], piece['uv'], piece['idx'])
    ntri = len(piece['idx']) // 3
    geo = {'idx': list(piece['idx']), 'tri_u32': [1] * ntri, 'tri_u8': [3] * ntri, 'pos': [tuple((f32(c) for c in p)) for p in piece['pos']], 'nrm': [tuple((f32(c) for c in p)) for p in piece['nrm']], 'tan': tan, 'bin': bin_, 'col': [u32(c[:3] + b'\xff', 0) for c in piece['rgba']], 'uv': [tuple((f32(c) for c in p)) for p in piece['uv']], 'mat': mat}
    s1 = dict(t1, **geo, next=ids[1])
    s2 = dict(t1, **geo, next=None)
    out = dict(m, uid=uid, sm1=ids[0], sub=(s1, s2), tail_mat=t1['mat'])
    return encode_mesh(out)

def clone_object(body, new_uid, swaps):
    b = bytearray(body)
    struct.pack_into('<I', b, 2, new_uid)
    hits = dict.fromkeys(swaps, 0)
    for k in range(6, len(b) - 5):
        if b[k] == 1 and b[k + 1] in (0, 1, 2) and (u32(b, k + 2) in swaps):
            hits[u32(b, k + 2)] += 1
            struct.pack_into('<I', b, k + 2, swaps[u32(b, k + 2)])
    missing = [hex(v) for v, n in hits.items() if not n]
    if missing:
        raise ValueError(f'clone_object: no reference to {missing}')
    applied = dict(swaps)
    applied[u32(body, 2)] = new_uid
    return (bytes(b), applied)

def gate_g3(tpl_body):
    m = parse_mesh(tpl_body)
    same = encode_mesh(m) == tpl_body
    s = m['sub'][0]
    tan, bin_ = tangents(s['pos'], s['nrm'], s['uv'], s['idx'])
    used = sorted(set(s['idx']))
    close = sum((1 for v in used if sum((tan[v][j] * s['tan'][v][j] for j in range(3))) > 0.99 and sum((bin_[v][j] * s['bin'][v][j] for j in range(3))) > 0.99))
    print(f"G3 {len(s['pos'])} source vertices, {len(s['idx']) // 3} triangles: re-authored {('IDENTICAL' if same else 'DIFFERENT')}; computed tangent + binormal within 8 deg of the stored on {close} / {len(used)}")
    return same
BV = 1256993910

def bv_world(body):
    k = body.find(P(BV))
    b = struct.unpack_from('<6f', body, k + 4)
    pos, _ident = entity_pos(body)
    return (tuple((pos[j] + b[j] for j in range(3))), tuple((pos[j] + b[3 + j] for j in range(3))))

def _box_dist(p, box):
    lo, hi = box
    return max(lo[0] - p[0], 0.0, p[0] - hi[0]) + max(lo[1] - p[1], 0.0, p[1] - hi[1])

def plan(ac4_body, hosts, template, fresh, S=None):
    a = decode_ac4(ac4_body)
    pts = world_points(a['pos'], a['E_raw'], a['entity_pos'])
    boxes = []
    for uid, name, body in hosts:
        pos, ident = entity_pos(body)
        if not ident:
            raise ValueError(f'clutter host {name} is rotated / scaled (positions are host position + offset)')
        boxes.append(bv_world(body))
    load = [0] * len(hosts)
    members = [[] for _ in hosts]
    nearest = 0
    for i, p in enumerate(pts):
        cand = [h for h, (lo, hi) in enumerate(boxes) if lo[0] <= p[0] <= hi[0] and lo[1] <= p[1] <= hi[1]]
        if not cand:
            cand = [min(range(len(hosts)), key=lambda h: _box_dist(p, boxes[h]))]
            nearest += 1
        h = min(cand, key=lambda h: (load[h], h))
        load[h] += 1
        members[h].append(i)
    settings = template['settings']
    if S is not None:
        settings = settings[:8] + struct.pack('<f', S) + settings[12:]
    S_ = struct.unpack_from('<f', settings, 8)[0]
    out, mapping, lines = ({}, {}, [])
    nxt = fresh
    for h, (uid, name, body) in enumerate(hosts):
        sel = members[h]
        if not sel:
            lines.append(f'clutter host {name}: no instance')
            continue
        origin, _ = entity_pos(body)
        off = [tuple((pts[i][j] - origin[j] for j in range(3))) for i in sel]
        E = f32(max((abs(x) for o in off for x in o)) * 1.0001)
        q = []
        for o in off:
            q += [max(-32767, min(32767, round(x / E * 32767))) for x in o]
        E_raw = struct.pack('<f', E)
        wp = world_points(q, E_raw, origin)
        step = E / 32767
        lo = tuple((f32(min((p[j] for p in wp)) - step) for j in range(3)))
        hi = tuple((f32(max((p[j] for p in wp)) + step) for j in range(3)))
        cc_own, dynmesh, dmi_own, settings_own = (nxt, nxt + 1, nxt + 2, nxt + 3)
        ids = iter(range(nxt + 4, nxt + 65536))
        tree = build_tree(wp, lo, hi, S_, ids)
        nxt = next(ids)
        out[uid] = ({'dmi_own': dmi_own, 'dynmesh': dynmesh, 'settings_own': settings_own, 'settings': settings, 'orient': [a['orient'][i] for i in sel], 'pos': q, 'v1': template['v1'], 'variant': bytes(len(sel)), 'tint': [a['tint'][i] for i in sel], 'scale': [a['scale'][i] for i in sel], 'E': E_raw, 'tree': tree}, cc_own)
        mapping[uid] = list(sel)
        n_nodes = len(tree_nodes(tree))
        lines.append(f'clutter host {name}: {len(sel)} instances, E {E:.2f}, tree {n_nodes} nodes, ids {cc_own:#x}-{nxt - 1:#x}, area {(hi[0] - lo[0]) * (hi[1] - lo[1]):.0f} m2 ({len(sel) / max(1.0, (hi[0] - lo[0]) * (hi[1] - lo[1])):.2f} / m2)')
    lines.append(f'clutter: {len(pts)} AC4 instances onto {len(out)} hosts ({nearest} outside every host box: the nearest), S {S_}, source mesh {u32(settings, 28):#010x}, ids {fresh:#x}-{nxt - 1:#x}')
    return (out, mapping, lines, nxt)

def gate_g2(bodies, mapping, ac4_body, tol=0.002):
    a = decode_ac4(ac4_body)
    ref = world_points(a['pos'], a['E_raw'], a['entity_pos'])
    seen, worst, field_bad, lines = ({}, 0.0, 0, [])
    tree_bad = 0
    for uid, sel in mapping.items():
        c, _cc = extract(bodies[uid])
        origin, _ = entity_pos(bodies[uid])
        wp = world_points(c['pos'], c['E'], origin)
        if len(wp) != len(sel):
            return (False, [f'G2 host {uid:#x}: {len(wp)} instances, planned {len(sel)}'])
        for loc, i in enumerate(sel):
            seen[i] = seen.get(i, 0) + 1
            worst = max(worst, max((abs(wp[loc][j] - ref[i][j]) for j in range(3))))
            field_bad += c['orient'][loc] != a['orient'][i] or c['tint'][loc] != a['tint'][i] or c['scale'][loc] != a['scale'][i] or (c['variant'][loc] != 0)
        once, dup, outside, un = check_tree(c['tree'], wp)
        tree_bad += dup + outside + un
        lines.append(f'G2 host {uid:#x}: {len(wp)} instances, tree: {once} indexed once, {dup} duplicates, {outside} outside their box, {un} unindexed')
    missing = len(ref) - len(seen)
    multi = sum((1 for v in seen.values() if v > 1))
    ok = not missing and (not multi) and (worst <= tol) and (not field_bad) and (not tree_bad)
    lines.append(f"G2: {len(seen)} / {len(ref)} AC4 instances placed ({missing} missing, {multi} on two hosts), worst position error {worst * 1000:.2f} mm (tol {tol * 1000:.0f}), {field_bad} with other fields changed, {tree_bad} tree faults -> {('PASS' if ok else 'FAIL')}")
    return (ok, lines)

def plantation_chunks():
    import common
    out = []
    for rec, d in common.payloads(common.ac3_archive('DataPC_AC3MP_Plantation.forge')):
        for o in common.objects_in(d):
            b = o['body']
            k = b.find(P(CC))
            if k < 0 or common.cls(o['type'], 'AC3') != 'Entity':
                continue
            c, end = parse_ac3(b, k)
            out.append((o['name'].strip(), b, k, c, end))
    return out

def strip(body):
    k, _v = visual_tail(body)
    _c, end = parse_ac3(body, k + 6)
    out = bytearray(body[:k] + body[end:])
    ca = count_at(body)
    struct.pack_into('<I', out, ca, u32(body, ca) - 1)
    return bytes(out)

def gate_g1(chunks):
    bad = 0
    for name, b, k, c, end in chunks:
        same = encode_ac3(c) == b[k:end]
        c2, cc_own = extract(b)
        back = insert(strip(b), {x: y for x, y in c2.items() if x != 'V'}, cc_own) == b
        bad += not (same and back)
        nodes = tree_nodes(c['tree'])
        print(f"G1 {name}: {len(c['orient'])} instances, {len(nodes)} nodes, chunk re-encoded {('IDENTICAL' if same else 'DIFFERENT')}, re-inserted into the stripped host {('IDENTICAL' if back else 'DIFFERENT')} (V {struct.unpack('<f', c['V'])[0]:.3f}, S {struct.unpack_from('<f', c['settings'], 8)[0]})")
    return bad == 0

def gate_tree(chunks):
    bad = 0
    for name, b, k, c, end in chunks:
        origin, ident = entity_pos(b)
        pts = world_points(c['pos'], c['E'], origin)
        own, lo, hi, idx, kids = c['tree']
        S = struct.unpack_from('<f', c['settings'], 8)[0]
        t = build_tree(pts, lo, hi, S, iter(range(own, own + 100000)))
        same = encode_tree(t) == encode_tree(c['tree'])
        once, dup, out, un = check_tree(c['tree'], pts)
        bad += not same
        print(f"tree {name}: rebuilt {('IDENTICAL' if same else 'DIFFERENT')} ({len(tree_nodes(t))} nodes, S {S}); stock: {once} indexed once, {dup} duplicates, {out} outside their box, {un} unindexed; identity {ident}")
    return bad == 0
HOSTS = ('AC3MP_MSET_Ground_Winery_Blacksmith_01A_001', 'AC3MP_MSET_Ground_Garden_01A_001', 'AC3MP_MSET_Ground_Graves_01A_001', 'AC3MP_MSET_Ground_Indians_01A_001', 'AC3MP_MSET_Ground_Quadrangle_02A_001', 'AC3MP_MSET_Ground_Graves_02A_001', 'AC3MP_MSET_Ground_Quadrangle_01A_001', 'AC3MP_MSET_Outskirts_01A_001')

def dry_tampa(build='out/maps/MS76_msett/DataPC_AC3MP_MSettlement.forge', template='AC3MP_Plan_Ground_Guest_01A_001'):
    import common
    ac4 = None
    for rec, d in common.payloads(common.ac4_archive(common.MSETTLEMENT), {'Cell00036_DataBlock'}):
        for o in common.objects_in(d):
            if o['name'].strip() == 'ACGA_MSET_Ground_Clutters_01A_001':
                ac4 = o['body']
    hosts = {}
    for rec, d in common.payloads(os.path.join(ROOT, build), {'Cell00084_DataBlock'}):
        for o in common.objects_in(d):
            if o['name'].strip() in HOSTS and common.cls(o['type'], 'AC3') == 'Entity':
                hosts[o['name'].strip()] = (o['uid'], o['name'].strip(), o['body'])
    tpl = next((c for name, b, k, c, end in plantation_chunks() if name == template))
    plan_, mapping, lines, nxt = plan(ac4, [hosts[n] for n in HOSTS], tpl, 1526345728)
    for ln in lines:
        print(ln)
    bodies = {uid: insert(hosts_body, f, cc) for uid, _n, hosts_body in hosts.values() if uid in plan_ for f, cc in [plan_[uid]]}
    ok, g2 = gate_g2(bodies, mapping, ac4)
    for ln in g2:
        print(ln)
    return ok
if __name__ == '__main__':
    ch = plantation_chunks()
    ok = gate_g1(ch)
    ok_t = gate_tree(ch)
    import common
    tpl_mesh = next((o['body'] for rec, d in common.payloads(common.ac3_archive('DataPC_AC3MP_Plantation.forge'), {'Cell00020_DataBlock'}) for o in common.objects_in(d) if o['name'].strip() == 'AC3MP_PLAN_Clutter_01A_LOD0'))
    ok3 = gate_g3(tpl_mesh)
    ok2 = dry_tampa()
    print('G1', 'PASS' if ok else 'FAIL', '| tree rule', 'PASS' if ok_t else 'FAIL', '| G3', 'PASS' if ok3 else 'FAIL', '| G2', 'PASS' if ok2 else 'FAIL')
    sys.exit(0 if ok and ok_t and ok2 and ok3 else 1)
