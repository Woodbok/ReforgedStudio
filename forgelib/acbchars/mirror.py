"""forgelib/acbchars/mirror.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import math
import os
import struct
import sys
import zlib
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE, os.path.join(ROOT, 'sp')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import anim_cut
import sp_anim
BONE_CLASS = struct.pack('<I', 2507411529)
REFERENCE = 743623600
CLIP_SPACE_KEYS = (0, 1)
S_MODEL = (1.0, -1.0, 1.0)
S_CLIP = (-1.0, 1.0, 1.0)
HAND_SWITCH_DATA = bytes.fromhex('8b718cce')
SEED_KIND_AT = 23

def _crc(text):
    return zlib.crc32(text.encode()) & 4294967295
SIDE_CLASSES = [('WeaponSwitchFromRefToLeftHandEventSeed', 'WeaponSwitchFromRefToRightHandEventSeed'), ('WeaponSwitchFromLeftHandToHolsterEventSeed', 'WeaponSwitchFromRightHandToHolsterEventSeed'), ('WeaponSwitchToLeftHandEventSeed', 'WeaponSwitchToRightHandEventSeed'), ('WeaponSwitchFromRef2ToLeftHandEventSeed', 'WeaponSwitchFromRef2ToRightHandEventSeed')]
KIND_SWAP = {0: 1, 1: 0, 2: 3, 3: 2, 4: 5, 5: 4, 6: 7, 7: 6}

def qmul(a, b):
    return anim_cut.quat_mul(a, b)

def qinv(q):
    return (-q[0], -q[1], -q[2], q[3])

def qrot(q, v):
    x, y, z, w = q
    m = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return tuple((sum((m[r][k] * v[k] for k in range(3))) for r in range(3)))

def reflect(q, s_left, s_right=None):
    s_right = s_right or s_left
    v = tuple((-s * c for s, c in zip(s_right, q[:3])))
    out = (v[0], v[1], v[2], q[3])
    d = tuple((a * b for a, b in zip(s_left, s_right)))
    if d != (1.0, 1.0, 1.0):
        axis = d.index(1.0)
        half = [0.0, 0.0, 0.0, 0.0]
        half[axis] = 1.0
        out = qmul(tuple(half), out)
    return out

def _canon(q):
    n = math.sqrt(sum((c * c for c in q)))
    q = tuple((c / n for c in q))
    return q if q[3] >= 0 else tuple((-c for c in q))

class Rig:

    def __init__(self, body):
        uid_of, raw = ({}, {})
        p = body.find(BONE_CLASS)
        while p >= 0:
            h = struct.unpack_from('<I', body, p + 4)[0]
            uid_of[struct.unpack_from('<I', body, p - 4)[0]] = h
            q, refs = (p + 8, [])
            for _ in range(2):
                if body[q] == 2:
                    refs.append(struct.unpack_from('<I', body, q + 1)[0])
                    q += 5
                else:
                    assert body[q] == 3, f'bone 0x{h:08X}: reference byte {body[q]}'
                    refs.append(None)
                    q += 1
            model = (struct.unpack_from('<3f', body, q), struct.unpack_from('<4f', body, q + 16))
            local = (struct.unpack_from('<3f', body, q + 32), struct.unpack_from('<4f', body, q + 48))
            raw[h] = (refs, model, local)
            p = body.find(BONE_CLASS, p + 4)
        self.parent = {h: uid_of.get(r[0]) for h, (r, _m, _l) in raw.items()}
        self.mirror_of = {h: uid_of.get(r[1]) for h, (r, _m, _l) in raw.items()}
        self.model = {h: m for h, (_r, m, _l) in raw.items()}
        self.local = {h: l for h, (_r, _m, l) in raw.items()}
        assert all((self.mirror_of[self.mirror_of[h]] == h for h in raw if self.mirror_of[h])), 'mirror references not paired'
        for h, m in self.mirror_of.items():
            if m is not None and self.parent[h] is not None:
                assert self.parent[m] == self.mirror_of[self.parent[h]], f"0x{h:08X}: the mirror's parent is not the parent's mirror"
        self.correction = {}
        for h, m in self.mirror_of.items():
            if m is None:
                continue
            c = qmul(qinv(reflect(self.model[h][1], S_MODEL)), self.model[m][1])
            self.correction[h] = _snap(c)

    def bones(self):
        return list(self.parent)

def _snap(q):
    q = _canon(q)
    best = max(range(4), key=lambda k: abs(q[k]))
    assert abs(q[best]) > 0.995, f'axis correction {q} is no half turn'
    out = [0.0, 0.0, 0.0, 0.0]
    out[best] = 1.0
    return tuple(out)

def acb_rig(archive='DataPC.forge', record='AC2MP_ID10_MainTemplate_Player'):
    import acb_source
    classes = acb_source.class_names()
    objs = acb_source.record_objects(archive, record)
    hb = next((o for o in objs if classes.get(o['type']) == 'Skeleton' and o['name'].strip() == 'Human Body'))
    return Rig(hb['body'])

def mirror_rotation(rig, src, q, held=None):
    if src in CLIP_SPACE_KEYS:
        return reflect(q, S_CLIP)
    if src == REFERENCE or rig.parent.get(src) is None:
        return reflect(q, S_CLIP, S_MODEL)
    cp = rig.correction[rig.parent[src]]
    if held:
        return qmul(qinv(cp), reflect(q, S_MODEL, held))
    return qmul(qmul(qinv(cp), reflect(q, S_MODEL)), rig.correction[src])

def mirror_hang(rig, anchor, pos, rot, held):
    ca = rig.correction[anchor]
    return (rig.mirror_of[anchor], qrot(qinv(ca), tuple((s * c for s, c in zip(S_MODEL, pos)))), qmul(qinv(ca), reflect(rot, S_MODEL, held)))

def mirror_translation(rig, src, t):
    if src in CLIP_SPACE_KEYS or src == REFERENCE or rig.parent.get(src) is None:
        return tuple((s * c for s, c in zip(S_CLIP, t)))
    cp = rig.correction[rig.parent[src]]
    return qrot(qinv(cp), tuple((s * c for s, c in zip(S_MODEL, t))))

def mirrors(rig, key):
    if key in CLIP_SPACE_KEYS:
        return key
    return rig.mirror_of.get(key)
AXIS8_SCALE = struct.unpack('<f', struct.pack('<f', 0.011223917))[0]

def axis8_decode(raw):
    b = raw[0]
    c = (b & 127) * AXIS8_SCALE - 0.70710677
    p = max(0.0, 1.0 - c * c) ** 0.5
    return (0.0, 0.0, p, c) if b & 128 else (0.0, 0.0, c, p)
Q4_SCALE = struct.unpack('<f', struct.pack('<f', 0.0013837706))[0]

def _insert(v, i):
    v = list(v)
    v.insert(i, max(0.0, 1.0 - sum((c * c for c in v))) ** 0.5)
    return tuple(v)

def quat3_decode(raw):
    return _insert([(b & 127) * AXIS8_SCALE - 0.70710677 for b in raw[:3]], (raw[0] >> 7) + 2 * (raw[1] >> 7))

def quat4_decode(raw):
    u = struct.unpack('<I', raw)[0]
    return _insert([(u >> s & 1023) * Q4_SCALE - 0.70710677 for s in (20, 10, 0)], u >> 30)

def quatf_decode(raw):
    v = struct.unpack('<3f', raw)
    u0, u1 = struct.unpack_from('<2I', raw)
    return _insert(v, (u0 & 1) + 2 * (u1 & 1))

def _decode(tag, raw):
    if 24 <= tag <= 26:
        return ('R', quatf_decode(raw))
    if 27 <= tag <= 29:
        return ('T', struct.unpack('<3f', raw))
    if 6 <= tag <= 8:
        return ('R', quat3_decode(raw))
    if 9 <= tag <= 11:
        return ('R', quat4_decode(raw))
    if 18 <= tag <= 20:
        return ('R', axis8_decode(raw))
    if 30 <= tag <= 35:
        return ('T', anim_cut.vec_decode(tag, raw))
    if 12 <= tag <= 14:
        return ('R', anim_cut.quat_decode(raw))
    if 15 <= tag <= 17:
        return ('R', anim_cut.quat8_decode(raw))
    if 21 <= tag <= 23:
        return ('R', anim_cut.axis_decode(raw))
    raise ValueError(f'codec {tag:#04x}')

def read_tracks(body):
    els, _ = anim_cut.elements(body)
    out = []
    for (key, _u), (_p, _e, tag, _kind, stride, y, times, data, _h) in zip(anim_cut._mappings(body), els):
        vals, typ = ([], None)
        for j in range(y):
            typ, v = _decode(tag, body[data + stride * j:data + stride * (j + 1)])
            vals.append(v)
        out.append((key, typ, list(times), vals, tag))
    return out

def _encode_element(typ, times, vals, tag):
    if typ == 'T':
        keys, fam, stride = ([(t, anim_cut.vec_encode(v)) for t, v in zip(times, vals)], 33, 6)
    elif 15 <= tag <= 17:
        keys, fam, stride = ([(t, anim_cut.quat8_encode(_canon(v))) for t, v in zip(times, vals)], 15, 8)
    else:
        keys, fam, stride = ([(t, anim_cut.quat_encode(_canon(v))) for t, v in zip(times, vals)], 12, 6)
    if len({d for _t, d in keys}) == 1:
        keys = keys[:1]
    kind = 'A' if keys[-1][0] <= 255 else 'B'
    return anim_cut._element(fam + (0 if kind == 'A' else 1), kind, stride, keys)

def mirror_tracks(rig, tracks, held=None):
    held = held or {}
    have = {(k, typ): (times, vals, tag) for k, typ, times, vals, tag in tracks}
    moved = {}
    out = []

    def fn(src, typ, v):
        return mirror_translation(rig, src, v) if typ == 'T' else mirror_rotation(rig, src, v, held.get(src))
    for key, typ, times, vals, tag in tracks:
        m = mirrors(rig, key)
        if m is None:
            out.append((key, typ, times, vals, tag))
        elif (m, typ) in have:
            src_t, src_v, src_tag = have[m, typ]
            out.append((key, typ, src_t, [fn(m, typ, v) for v in src_v], src_tag))
        else:
            moved[key, typ] = m
            out.append((m, typ, times, [fn(key, typ, v) for v in vals], tag))
    return (out, moved)

def _mapping_spans(body):
    ms = [p for p, k in sp_anim.markers(body) if k == sp_anim.MAPPING]
    assert all((b - a == 12 for a, b in zip(ms, ms[1:]))), 'mappings not a 12-byte list'
    return ms

def write_tracks(body, tracks, key_layout=None):
    old = anim_cut._mappings(body)
    assert len(old) == len(tracks)
    ms = _mapping_spans(body)
    els, stream_at = anim_cut.elements(body)
    head = bytearray(body[:stream_at])
    changed = [k for (k_old, _u), (k, *_r) in zip(old, tracks) if k_old != k]
    if changed:
        assert key_layout, 'the track layout changed: a fresh (AnimationKey, layout word) is needed'
        for p, (k, *_r) in zip(ms, tracks):
            struct.pack_into('<I', head, p + 4, k)
        _n, _recs, c = anim_cut.header(body)
        _trs, td = anim_cut.tracks(body)
        struct.pack_into('<I', head, c + 12, key_layout[0])
        struct.pack_into('<I', head, td + 9, key_layout[1])
    new = bytes(head) + b''.join((_encode_element(typ, times, vals, tag) for _k, typ, times, vals, tag in tracks))
    return anim_cut.fit_arenas(new)

def _sorted_tracks(tracks):
    return sorted(tracks, key=lambda t: (t[0], 0 if t[1] == 'T' else 1))

def mirror_events(body):
    out = bytearray(body)
    trs, _ = anim_cut.tracks(body)
    for _s, _e, seeds, _t, _en in trs:
        for a, z in seeds:
            if z - a >= SEED_KIND_AT + 4 and body[a + 14:a + 18] == HAND_SWITCH_DATA:
                k = struct.unpack_from('<I', body, a + SEED_KIND_AT)[0]
                assert k in KIND_SWAP, f'hand-switch kind {k}'
                struct.pack_into('<I', out, a + SEED_KIND_AT, KIND_SWAP[k])
    out = bytes(out)
    for left, right in SIDE_CLASSES:
        lc, rc = (struct.pack('<I', _crc(left)), struct.pack('<I', _crc(right)))
        tmp = b'\xff\xfe\xfd\xfc'
        assert tmp not in out
        out = out.replace(lc, tmp).replace(rc, lc).replace(tmp, rc)
    return out

def key_layout(body):
    _n, _recs, c = anim_cut.header(body)
    return (struct.unpack_from('<I', body, c + 12)[0], struct.unpack_from('<I', body, anim_cut.tracks(body)[1] + 9)[0])

def mirror_clip(body, rig, fresh_pair=None, held=None, seat=None):
    tracks = read_tracks(body)
    mirrored, renamed = mirror_tracks(rig, tracks, held)
    if seat and held:
        for la in held:
            if any((k == rig.mirror_of[la] and typ == 'R' for k, typ, *_r in mirrored)):
                compose_inverse(mirrored, rig.mirror_of[la], *seat)
    pair = None
    if renamed:
        mirrored = _sorted_tracks(mirrored)
        assert fresh_pair is not None, f'one-sided channels {renamed}: the layout changes'
        k, w = key_layout(body)
        pair = fresh_pair(f'mirror {k:08X} {w:08X} ' + ' '.join((f'{a:08X}{t}>{b:08X}' for (a, t), b in sorted(renamed.items()))))
    out = write_tracks(body, mirrored, pair)
    out = mirror_events(out)
    problems = anim_cut.check(out) or sp_anim.check_stream(out)
    assert not problems, problems
    return (out, renamed)
CAMERA_POSITION, CAMERA_ROTATION = (6, 7)
CAMERA_KEYS = (6, 7, 8, 9, 4294967291)

def mirror_camera(body):
    maps = anim_cut._mappings(body)
    assert maps and {k for k, _u in maps} <= set(CAMERA_KEYS), f'not a camera clip: keys {[hex(k) for k, _u in maps]}'
    out = bytearray(body)
    flip_q = tuple((-s for s in S_CLIP)) + (1.0,)
    els, _ = anim_cut.elements(body)
    for (key, _u), (_p, _e, tag, _kind, stride, y, _times, data, _h) in zip(maps, els):
        if key == CAMERA_POSITION:
            assert 27 <= tag <= 29 and stride == 12, f'camera position codec {tag:#04x}'
        elif key == CAMERA_ROTATION:
            assert 24 <= tag <= 26 and stride == 12, f'camera rotation codec {tag:#04x}'
        else:
            continue
        for j in range(y):
            at = data + stride * j
            if key == CAMERA_POSITION:
                flips = [i for i in range(3) if S_CLIP[i] < 0]
            else:
                u0, u1 = struct.unpack_from('<2I', body, at)
                dropped = (u0 & 1) + 2 * (u1 & 1)
                sign = flip_q[dropped]
                stored = [lane for lane in range(4) if lane != dropped]
                flips = [i for i, lane in enumerate(stored) if flip_q[lane] * sign < 0]
            for i in flips:
                out[at + 4 * i + 3] ^= 128
    out = bytes(out)
    problems = anim_cut.check(out)
    assert not problems, problems
    return out

def camera_track(body, key):
    els, _ = anim_cut.elements(body)
    for (k, _u), (_p, _e, tag, _kind, stride, y, times, data, _h) in zip(anim_cut._mappings(body), els):
        if k == key:
            return [(t, _decode(tag, body[data + stride * j:data + stride * (j + 1)])[1]) for j, t in enumerate(times)]
    return []

def camera_mirrored(a, b, tol=1e-05):
    problems = []
    for key in (CAMERA_POSITION, CAMERA_ROTATION):
        ka, kb = (camera_track(a, key), camera_track(b, key))
        if not ka or [t for t, _v in ka] != [t for t, _v in kb]:
            problems.append(f'camera key {key}: missing, or its key times differ')
            continue
        for (t, va), (_t, vb) in zip(ka, kb):
            want = tuple((s * c for s, c in zip(S_CLIP, va))) if key == CAMERA_POSITION else reflect(va, S_CLIP)
            err = max((abs(x - y) for x, y in zip(want, vb)))
            if key == CAMERA_ROTATION:
                err = min(err, max((abs(x + y) for x, y in zip(want, vb))))
            if err > tol:
                problems.append(f'camera key {key} at {t}: {vb} is not the reflection {want}')
                break
    return problems

def skeleton_binds(bodies, body_rig):
    binds, rig_of = (dict(body_rig.model), {h: -1 for h in body_rig.model})
    for i, body in enumerate(bodies):
        uid_of, raw = ({}, {})
        p = body.find(BONE_CLASS)
        while p >= 0:
            h = struct.unpack_from('<I', body, p + 4)[0]
            uid_of[struct.unpack_from('<I', body, p - 4)[0]] = h
            q, refs = (p + 8, [])
            for _ in range(2):
                if body[q] == 2:
                    refs.append(struct.unpack_from('<I', body, q + 1)[0])
                    q += 5
                else:
                    refs.append(None)
                    q += 1
            raw[h] = (refs[0], (struct.unpack_from('<3f', body, q + 32), struct.unpack_from('<4f', body, q + 48)))
            p = body.find(BONE_CLASS, p + 4)
        parent = {h: uid_of.get(r) for h, (r, _l) in raw.items()}
        memo = {}

        def model(h):
            if h not in memo:
                if parent[h] is None:
                    memo[h] = body_rig.model.get(h, ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)))
                else:
                    pp, pq = model(parent[h])
                    lp, lq = raw[h][1]
                    memo[h] = (tuple((a + b for a, b in zip(pp, qrot(pq, lp)))), qmul(pq, lq))
            return memo[h]
        for h in raw:
            if h not in binds:
                binds[h] = model(h)
                rig_of[h] = i
    return (binds, rig_of)

def outfit_partners(body_rig, binds, rig_of, tolerance=0.01):
    out, far = ({}, [])
    for h, (p, _q) in binds.items():
        if body_rig.mirror_of.get(h) is not None:
            out[h] = body_rig.mirror_of[h]
            continue
        target = tuple((s * c for s, c in zip(S_MODEL, p)))
        same = [g for g in binds if rig_of[g] == rig_of[h]]
        best = min(same, key=lambda g: sum(((a - b) ** 2 for a, b in zip(binds[g][0], target))))
        d = math.sqrt(sum(((a - b) ** 2 for a, b in zip(binds[best][0], target))))
        out[h] = best
        if d > tolerance:
            far.append((h, best, d))
    return (out, far)

def _mat(q):
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]

def _mm(a, b):
    return [[sum((a[i][k] * b[k][j] for k in range(3))) for j in range(3)] for i in range(3)]

def _mv(a, v):
    return [sum((a[i][k] * v[k] for k in range(3))) for i in range(3)]

def _tr(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]
PALETTE_AT, MESHBONE_LEN = (23, 40)

def mirror_mesh(body, binds, partner):
    sys.path.insert(0, os.path.join(ROOT, 'ac4chars'))
    import skinmesh
    lay = skinmesh.layout(body)
    assert lay['stride'] == 32 and lay['count'], (lay['stride'], lay['count'])
    unknown = {k for k in range(lay['count']) if struct.unpack_from('<I', body, PALETTE_AT + MESHBONE_LEN * k + 36)[0] not in binds}
    if unknown:
        tail = skinmesh.tail_ac3(body, lay)
        v0 = 0
        for s_, n in enumerate((skinmesh.u32(body, lay['post'] + 32 + 32 * k + 12) for k in range(lay['subs']))):
            lst = tail['lists'][min(s_, len(tail['lists']) - 1)]
            for i in range(v0, v0 + n):
                at = lay['vb'] + 32 * i
                for j, w in zip(body[at + 24:at + 28], body[at + 28:at + 32]):
                    assert not (w and j < len(lst) and (lst[j] in unknown)), f"vertex {i} weighted on bone 0x{struct.unpack_from('<I', body, PALETTE_AT + MESHBONE_LEN * lst[j] + 36)[0]:08X}, no bind"
            v0 += n
    b = bytearray(body)
    for i in range(lay['nv']):
        v = lay['vb'] + 32 * i
        x, y, z = struct.unpack_from('<3h', b, v)
        struct.pack_into('<3h', b, v, x, max(-32767, min(32767, -y)), z)
        for at in (v + 9, v + 13, v + 17):
            b[at] = 255 - b[at]
    assert lay['ni'] % 3 == 0, lay['ni']
    idx = list(struct.unpack_from(f"<{lay['ni']}H", b, lay['ib']))
    for k in range(0, len(idx), 3):
        idx[k + 1], idx[k + 2] = (idx[k + 2], idx[k + 1])
    struct.pack_into(f"<{lay['ni']}H", b, lay['ib'], *idx)
    S = [[S_MODEL[0], 0, 0], [0, S_MODEL[1], 0], [0, 0, S_MODEL[2]]]
    for k in range(lay['count']):
        e = PALETTE_AT + MESHBONE_LEN * k
        h = struct.unpack_from('<I', b, e + 36)[0]
        if h not in binds:
            continue
        a2 = partner[h]
        v6 = [c / 32768 for c in struct.unpack_from('<6h', b, e + 16)]
        A, B = (v6[:3], v6[3:])
        C = [A[1] * B[2] - A[2] * B[1], A[2] * B[0] - A[0] * B[2], A[0] * B[1] - A[1] * B[0]]
        M = [[C[r], A[r], B[r]] for r in range(3)]
        u = struct.unpack_from('<Q', b, e + 28)[0]
        t = [(f - (1 << 21) if f >= 1 << 20 else f) / 65536 for f in (u >> 21 * i & 2097151 for i in range(3))]
        (pa, qa), (pb, qb) = (binds[h], binds[a2])
        Ra, Rb = (_mat(qa), _mat(qb))
        M2 = _mm(_mm(_mm(_mm(_tr(Rb), S), Ra), M), S)
        w = [c + d for c, d in zip(_mv(Ra, t), pa)]
        t2 = _mv(_tr(Rb), [s - d for s, d in zip(_mv(S, w), pb)])
        ints = [max(-32768, min(32767, int(round(M2[r][col] * 32768)))) for col in (1, 2) for r in range(3)]
        packed = u & 1 << 63
        for i, x in enumerate(t2):
            packed |= (int(round(x * 65536)) & 2097151) << 21 * i
        struct.pack_into('<6h', b, e + 16, *ints)
        struct.pack_into('<Q', b, e + 28, packed)
        struct.pack_into('<I', b, e + 36, a2)
    return bytes(b)

def outfit_meshes(objs_by_file, log, more_skeletons=()):
    sys.path.insert(0, os.path.join(ROOT, 'ac4chars'))
    import skinmesh
    import acb_source
    classes = acb_source.class_names()
    skels = {}
    for objs in list(objs_by_file.values()) + [list(more_skeletons)]:
        for o in objs:
            if classes.get(o['type']) == 'Skeleton':
                skels.setdefault(o['name'].strip(), o['body'])
    body_rig = Rig(skels['Human Body'])
    binds, rig_of = skeleton_binds([b for n, b in skels.items() if n != 'Human Body'], body_rig)
    partner, _far = outfit_partners(body_rig, binds, rig_of)
    cloth_refs = set()
    for objs in objs_by_file.values():
        for o in objs:
            if classes.get(o['type']) == 'Cloth':
                b = o['body']
                cloth_refs |= {struct.unpack_from('<I', b, i)[0] for i in range(len(b) - 3)}
    for objs in objs_by_file.values():
        for o in objs:
            if classes.get(o['type']) == 'LODSelector' and o['uid'] in cloth_refs:
                b = o['body']
                cloth_refs |= {struct.unpack_from('<I', b, i)[0] for i in range(len(b) - 3)}
    out, names, cloths = ({}, set(), set())
    for key, objs in objs_by_file.items():
        for i, o in enumerate(objs):
            if classes.get(o['type']) != 'Mesh':
                continue
            if o['uid'] in cloth_refs:
                cloths.add(o['name'])
                continue
            try:
                lay = skinmesh.layout(o['body'])
            except Exception:
                continue
            if lay['stride'] != 32 or not lay['count']:
                continue
            hs = {struct.unpack_from('<I', o['body'], PALETTE_AT + MESHBONE_LEN * k + 36)[0] for k in range(lay['count'])}
            if not hs & set(body_rig.model):
                continue
            out.setdefault(key, {})[i] = mirror_mesh(o['body'], binds, partner)
            names.add(o['name'])
    log(f"mirror: his outfit reflected - {len(names)} skinned meshes ({', '.join(sorted(names)[:6])} ...), re-bound to the mirror bones ({len(binds)} bones: Human Body's mirrors, add-on rigs paired by position)" + (f"; cloth meshes left as they are: {', '.join(sorted(cloths))}" if cloths else ''))
    return out

def seat_offset(rig, cfg):
    (la, j), = cfg['held'].items()
    ra = rig.mirror_of[la]
    hand = rig.parent[la]
    fp, fq = cfg['seat']
    hp, hq = rig.local[hand]
    hi = qinv(hq)
    tp, tq = (qrot(hi, [a - b for a, b in zip(fp, hp)]), qmul(hi, fq))
    rp, rq = (mirror_translation(rig, la, tp), mirror_rotation(rig, la, tq, j))
    bp, bq = rig.local[ra]
    bi = qinv(bq)
    return (tuple(qrot(bi, [a - b for a, b in zip(rp, bp)])), _canon(qmul(bi, rq)))

def compose_inverse(tracks, bone, offset, then=None):
    p, q = offset
    qi = qinv(q)
    turn = qmul(qi, then) if then else qi
    shift = qrot(qi, [-c for c in p])
    ri = next((i for i, tr in enumerate(tracks) if tr[0] == bone and tr[1] == 'R'), None)
    ti = next((i for i, tr in enumerate(tracks) if tr[0] == bone and tr[1] == 'T'), None)
    assert ri is not None and ti is not None, f'bone 0x{bone:08X}: the seat needs both its channels'
    k, typ, rts, rvs, rtag = tracks[ri]
    k2, typ2, tts, tvs, ttag = tracks[ti]
    new_t = [tuple((a + b for a, b in zip(tv, qrot(_sample_q(rts, rvs, tt), shift)))) for tt, tv in zip(tts, tvs)]
    tracks[ti] = (k2, typ2, tts, new_t, ttag)
    tracks[ri] = (k, typ, rts, [qmul(r, turn) for r in rvs], rtag)

def _sample_q(ts, vs, t):
    if t <= ts[0]:
        return vs[0]
    for k in range(len(ts) - 1):
        if ts[k] <= t <= ts[k + 1]:
            f = (t - ts[k]) / (ts[k + 1] - ts[k])
            a, b = (vs[k], vs[k + 1])
            if sum((x * y for x, y in zip(a, b))) < 0:
                b = tuple((-c for c in b))
            return _canon(tuple((x + (y - x) * f for x, y in zip(a, b))))
    return vs[-1]

def weapon_seated(meshes, skeleton, clips, shift):
    import anim_cut
    roots = set()
    p = skeleton.find(BONE_CLASS)
    while p >= 0:
        if skeleton[p + 8] != 2:
            roots.add(struct.unpack_from('<I', skeleton, p + 4)[0])
        p = skeleton.find(BONE_CLASS, p + 4)
    out_m = []
    for m in meshes:
        mb = bytearray(m)
        sys.path.insert(0, os.path.join(ROOT, 'ac4chars'))
        import skinmesh
        for k in range(skinmesh.layout(m)['count']):
            e = PALETTE_AT + MESHBONE_LEN * k
            if struct.unpack_from('<I', m, e + 36)[0] not in roots:
                continue
            u = struct.unpack_from('<Q', m, e + 28)[0]
            t = [(f - (1 << 21) if f >= 1 << 20 else f) / 65536 for f in (u >> 21 * i & 2097151 for i in range(3))]
            packed = u & 1 << 63
            for i, x in enumerate(t):
                packed |= (int(round((x + shift[i]) * 65536)) & 2097151) << 21 * i
            struct.pack_into('<Q', mb, e + 28, packed)
        out_m.append(bytes(mb))
    sk = bytearray(skeleton)
    uid_of, children = ({}, [])
    p = skeleton.find(BONE_CLASS)
    while p >= 0:
        uid_of[struct.unpack_from('<I', skeleton, p - 4)[0]] = (struct.unpack_from('<I', skeleton, p + 4)[0], p)
        p = skeleton.find(BONE_CLASS, p + 4)
    root_uids = set()
    for u, (h, p) in uid_of.items():
        if skeleton[p + 8] != 2:
            root_uids.add(u)
    for u, (h, p) in uid_of.items():
        if skeleton[p + 8] == 2 and struct.unpack_from('<I', skeleton, p + 9)[0] in root_uids:
            q = p + 14 if skeleton[p + 13] == 3 else p + 18
            for t in (q, q + 32):
                struct.pack_into('<3f', sk, t, *[a + b for a, b in zip(struct.unpack_from('<3f', skeleton, t), shift)])
            children.append(h)
    out_c = []
    for c in clips:
        for h in children:
            tr = anim_cut.bone_tracks(c, h).get('T')
            if tr:
                c = anim_cut.replace_bone(c, h, t_keys=[(t, tuple((a + b for a, b in zip(v, shift)))) for t, v in zip(*tr)])
        out_c.append(c)
    return (out_m, bytes(sk), out_c)
LEFT_HANDED = {10: dict(held={2841710851: (1.0, 1.0, -1.0)}, seat=((0.07022, 0.01408, -0.04162), (0.77023, -0.04671, 0.02434, 0.63555)), fx_slots={4: 3}), 25: dict(held={2841710851: (1.0, -1.0, 1.0)}, seat=((0.12279, -0.01918, 0.04047), (0.16635, -0.02505, 0.01227, 0.98567)), fx_slots={4: 3}), 17: dict(held={2841710851: (-1.0, 1.0, 1.0)})}
FX_SEED_TAIL = 9

def fx_slots(body, swap):
    fx = next((k for k, v in anim_cut.CLS.items() if v == 'FXEventSeed'))
    out = bytearray(body)
    trs, _ = anim_cut.tracks(body)
    for _s, _e, seeds, _t, _en in trs:
        for a, z in seeds:
            if struct.unpack_from('<I', body, a + 5)[0] == fx and z - a > 40 and (body[z - FX_SEED_TAIL] in swap):
                assert body[z - 8:z] == bytes(8), body[a:z].hex()
                out[z - FX_SEED_TAIL] = swap[body[z - FX_SEED_TAIL]]
    return bytes(out)

def config(acb_id):
    return LEFT_HANDED.get(acb_id)

def held_reflection(cfg):
    j, = set(cfg['held'].values())
    return j
SIDE_WORDS = (('positionl', 'positionr'), ('footl', 'footr'), ('Left', 'Right'), ('_l_', '_r_'))

def mirror_name(name):
    out = name
    for a, b in SIDE_WORDS[:2]:
        out = out.replace(a, '\x00').replace(b, a).replace('\x00', b)
    return out
