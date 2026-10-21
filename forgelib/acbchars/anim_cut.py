"""forgelib/acbchars/anim_cut.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import math
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'sp'))
import exe_registry
import pool_inline
import sp_anim
import stream_walk
CLS, _ = exe_registry.load(exe_registry.AC3)
ANIM_TRACK_EVENT = next((k for k, v in CLS.items() if v == 'AnimTrackEvent'))
ANIM_TRACK_DATA = next((k for k, v in CLS.items() if v == 'AnimTrackData'))
SEED_CLASSES = {k for k, v in CLS.items() if v.endswith('EventSeed')}
KIND = pool_inline.KIND

def _a(stride):
    return next((a for a in (4, 2, 1) if stride % a == 0))

def _align(n, a):
    return n + a - 1 & ~(a - 1)

def mem_size(kind, stride, y, hdr_len):
    if kind == 'A':
        end = 2 + (y - 1)
    elif kind == 'B':
        end = (2 if y <= 255 else 4) + 2 * (y - 1)
    else:
        end = 2 + hdr_len - 1
    return _align(end, _a(stride)) + stride * y

def header(body):
    n = struct.unpack_from('<I', body, 4)[0]
    recs = [list(struct.unpack_from('<IHHI', body, 8 + 12 * i)) for i in range(n)]
    c = 9 + 12 * n
    return (n, recs, c)

def tracks(body):
    n, _recs, c = header(body)
    count = struct.unpack_from('<I', body, c + 23)[0]
    marks = sp_anim.markers(body)
    starts = [p - 5 for p, k in marks if k == ANIM_TRACK_EVENT]
    td = next((p - 5 for p, k in marks if k == ANIM_TRACK_DATA))
    assert len(starts) == count, f'{len(starts)} AnimTrackEvent markers, the clip says {count}'
    assert not starts or starts[0] == c + 27, (starts[:1], c + 27)
    out = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else td
        nseed = struct.unpack_from('<I', body, s + 11)[0]
        tail = 4 + 2 + 2 * nseed
        tc = struct.unpack_from('<H', body, e - 2 * nseed - 2)[0]
        assert tc == nseed, f'track @{s}: {nseed} seeds, {tc} times'
        times = list(struct.unpack_from('<%dH' % nseed, body, e - 2 * nseed))
        seed_at = [p - 5 for p, cls in marks if s < p < e and cls in SEED_CLASSES and (body[p - 5] == 0)]
        assert len(seed_at) == nseed, f'track @{s}: {len(seed_at)} seed starts for {nseed} seeds'
        assert not seed_at or seed_at[0] == s + 15, (seed_at[0], s + 15)
        seeds = [(a, seed_at[i + 1] if i + 1 < nseed else e - tail) for i, a in enumerate(seed_at)]
        out.append((s, e, seeds, times, e - tail))
    return (out, td)

def c_shift(b):
    return b >> 6

def elements(body):
    ms = [p for p, cls in sp_anim.markers(body) if cls == sp_anim.MAPPING]
    m = ms[-1]
    n, pos = (struct.unpack_from('<I', body, m + 10)[0], m + 14)
    out = []
    for _ in range(n):
        end = stream_walk.walk_element(body, pos)
        tag = body[pos]
        kind, stride = KIND[tag]
        X, Y = struct.unpack_from('<II', body, pos + 1)
        h = pos + 9
        if kind == 'A':
            hdr = body[h:h + Y - 1]
            times = [0] + list(hdr)
        elif kind == 'B':
            hdr = body[h:h + 2 * (Y - 1)]
            times = [0] + [struct.unpack_from('<H', hdr, i)[0] for i in range(0, len(hdr), 2)]
        else:
            b = body[h]
            hdr = body[h:h + 1 + (b & 63)]
            step = c_shift(b)
            times = [i << step for i in range(8 * (b & 63)) if hdr[1 + i // 8] >> i % 8 & 1]
        data = h + len(hdr)
        assert data + stride * Y == end and len(times) == Y, (pos, hex(tag), Y, len(times))
        assert X == mem_size(kind, stride, Y, len(hdr)), f'element @{pos} tag {tag:#x}: X {X}, formula {mem_size(kind, stride, Y, len(hdr))}'
        out.append((pos, end, tag, kind, stride, Y, times, data, hdr))
        pos = end
    assert pos == len(body), (pos, len(body))
    return (out, m + 14)

def _element(tag, kind, stride, keys, mask_bytes=None, flags=0):
    y = len(keys)
    if kind == 'C' and any((t % (1 << c_shift(flags)) for t, _ in keys)):
        tag, kind = (tag - 2, 'A') if keys[-1][0] <= 255 else (tag - 1, 'B')
    if kind == 'A':
        assert all((t <= 255 for t, _ in keys)), 'a kind-A key past 255 (4.25 s)'
        hdr = bytes((t for t, _ in keys[1:]))
    elif kind == 'B':
        hdr = b''.join((struct.pack('<H', t) for t, _ in keys[1:]))
    else:
        frames = [t >> c_shift(flags) for t, _ in keys]
        nbytes = max(max(frames) // 8 + 1, mask_bytes or 0)
        mask = bytearray(nbytes)
        for f in frames:
            mask[f // 8] |= 1 << f % 8
        assert nbytes <= 63, 'a kind-C mask past 63 bytes'
        hdr = bytes([flags | nbytes]) + bytes(mask)
    x = mem_size(kind, stride, y, len(hdr))
    return bytes([tag]) + struct.pack('<II', x, y) + hdr + b''.join((d for _, d in keys))

def _lerp(a, b, f):
    return int(round(a + (b - a) * f))

def interpolate(tag, a, b, f):
    if tag in (33, 34, 35):
        return struct.pack('<3h', *[_lerp(x, y, f) for x, y in zip(struct.unpack('<3h', a), struct.unpack('<3h', b))])
    if tag in (12, 13, 14):
        ua, ub = (struct.unpack('<3H', a), struct.unpack('<3H', b))
        if (ua[0] & 32768, ua[1] & 32768) != (ub[0] & 32768, ub[1] & 32768):
            return None
        if max((abs((x & 32767) - (y & 32767)) for x, y in zip(ua, ub))) > 8192:
            return None
        return struct.pack('<3H', *[x & 32768 | _lerp(x & 32767, y & 32767, f) for x, y in zip(ua, ub)])
    if tag in (21, 22, 23):
        x, y = (struct.unpack('<H', a)[0], struct.unpack('<H', b)[0])
        if (x ^ y) & 32768 or abs((x & 32767) - (y & 32767)) > 8192:
            return None
        return struct.pack('<H', x & 32768 | _lerp(x & 32767, y & 32767, f))
    if tag in (30, 31, 32):

        def unpack(v):
            i = struct.unpack('<i', v)[0]
            return (i >> 23, _sx(i >> 11 & 4095, 12), _sx(i & 2047, 11))
        (x1, y1, z1), (x2, y2, z2) = (unpack(a), unpack(b))
        x, y, z = (_lerp(x1, x2, f), _lerp(y1, y2, f), _lerp(z1, z2, f))
        return struct.pack('<I', (x & 511) << 23 | (y & 4095) << 11 | z & 2047)
    return None

def _sx(v, bits):
    return v - (1 << bits) if v & 1 << bits - 1 else v
Q_SCALE, Q_OFFSET = struct.unpack('<2f', struct.pack('<2f', 4.3161e-05, -0.70710677))

def quat_decode(raw):
    a = struct.unpack('<3H', raw)
    v = [(x & 32767) * Q_SCALE + Q_OFFSET for x in a]
    v.insert((a[0] >> 15) + 2 * (a[1] >> 15), max(0.0, 1.0 - sum((c * c for c in v))) ** 0.5)
    return tuple(v)

def quat_encode(q):
    n = sum((c * c for c in q)) ** 0.5
    q = [c / n for c in q]
    i = max(range(4), key=lambda k: abs(q[k]))
    if q[i] < 0:
        q = [-c for c in q]
    s = [max(0, min(32767, int(round((c - Q_OFFSET) / Q_SCALE)))) for k, c in enumerate(q) if k != i]
    s[0] |= (i & 1) << 15
    s[1] |= i >> 1 << 15
    return struct.pack('<3H', *s)

def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)

def rotate_bone(body, bone_key, q):
    out = bytearray(body)
    els, _ = elements(body)
    hits = 0
    for (key, _kind), (_p, _e, tag, kind, stride, y, _t, data, _h) in zip(_mappings(body), els):
        if key != bone_key or tag // 3 not in (9 // 3, 12 // 3, 15 // 3, 21 // 3):
            continue
        assert tag in (12, 13, 14), f'bone 0x{bone_key:08X}: rotation codec {tag:#04x} not handled'
        for j in range(y):
            at = data + 6 * j
            out[at:at + 6] = quat_encode(quat_mul(quat_decode(body[at:at + 6]), q))
        hits += 1
    assert hits == 1, f'bone 0x{bone_key:08X}: {hits} rotation elements'
    return bytes(out)
Q8_SCALE, Q8_OFFSET = struct.unpack('<2f', struct.pack('<2f', 1.3487017e-06, -0.70710677))

def axis_decode(raw):
    u = struct.unpack('<H', raw)[0]
    c = (u & 32767) * 4.3161e-05 - 0.70710677
    p = max(0.0, 1.0 - c * c) ** 0.5
    return (0.0, 0.0, p, c) if u & 32768 else (0.0, 0.0, c, p)

def axis_encode(q):
    _x, _y, z, w = q
    if abs(z) <= abs(w):
        if w < 0:
            z, w = (-z, -w)
        top, c = (0, z)
    else:
        if z < 0:
            z, w = (-z, -w)
        top, c = (32768, w)
    u = max(0, min(32767, int(round((c + 0.70710677) / 4.3161e-05))))
    return struct.pack('<H', top | u)

def quat8_decode(raw):
    q = struct.unpack('<Q', raw)[0]
    v = [x * Q8_SCALE + Q8_OFFSET for x in (q >> 8 & 1048575, q >> 52 & 4095 | (q & 255) << 12, q >> 32 & 1048575)]
    v.insert(q >> 30 & 3, max(0.0, 1.0 - sum((c * c for c in v))) ** 0.5)
    return tuple(v)

def quat8_encode(q):
    n = sum((c * c for c in q)) ** 0.5
    q = [c / n for c in q]
    i = max(range(4), key=lambda k: abs(q[k]))
    if q[i] < 0:
        q = [-c for c in q]
    a, b, c = [max(0, min(1048575, int(round((x - Q8_OFFSET) / Q8_SCALE)))) for k, x in enumerate(q) if k != i]
    return struct.pack('<Q', i << 30 | a << 8 | c << 32 | b >> 12 & 255 | (b & 4095) << 52)

def vec_decode(tag, raw):
    if tag in (33, 34, 35):
        return tuple((x * 0.001 for x in struct.unpack('<3h', raw)))
    i = struct.unpack('<i', raw)[0]
    return tuple((v * 0.001 for v in (i >> 23, _sx(i >> 11 & 4095, 12), _sx(i & 2047, 11))))

def vec_encode(v):
    return struct.pack('<3h', *[max(-32768, min(32767, int(round(x * 1000)))) for x in v])

def bone_tracks(body, bone_key, axis=False):
    out = {}
    els, _ = elements(body)
    for (key, _kind), (_p, _e, tag, kind, stride, y, times, data, _h) in zip(_mappings(body), els):
        if key != bone_key:
            continue
        raws = [body[data + stride * j:data + stride * (j + 1)] for j in range(y)]
        if 30 <= tag <= 35:
            out['T'] = (list(times), [vec_decode(tag, r) for r in raws])
        elif 12 <= tag <= 14:
            out['R'] = (list(times), [quat_decode(r) for r in raws])
        elif 15 <= tag <= 17:
            out['R'] = (list(times), [quat8_decode(r) for r in raws])
        elif axis and 21 <= tag <= 23:
            out['R'] = (list(times), [axis_decode(r) for r in raws])
    return out

def replace_bone(body, bone_key, t_keys=None, r_keys=None):
    els, stream_at = elements(body)
    new_els, hits = ([], 0)
    for (key, _kind), (p, e, tag, kind, stride, y, times, data, hdr) in zip(_mappings(body), els):
        keys = None
        if key == bone_key and t_keys is not None and (30 <= tag <= 35):
            keys, fam, stride2 = ([(t, vec_encode(v)) for t, v in t_keys], 33, 6)
        elif key == bone_key and r_keys is not None and (12 <= tag <= 17 or 21 <= tag <= 23):
            keys, fam, stride2 = ([(t, quat8_encode(q)) for t, q in r_keys], 15, 8)
        if keys is None:
            new_els.append(body[p:e])
            continue
        hits += 1
        if len({d for _t, d in keys}) == 1:
            keys = keys[:1]
        kind2 = 'A' if keys[-1][0] <= 255 else 'B'
        new_els.append(_element(fam + (0 if kind2 == 'A' else 1), kind2, stride2, keys))
    assert hits == (t_keys is not None) + (r_keys is not None), f'bone 0x{bone_key:08X}: {hits} elements replaced'
    return fit_arenas(body[:stream_at] + b''.join(new_els))

def add_bone_track(body, bone_key, t_value, r_value):
    ms = [p for p, k in sp_anim.markers(body) if k == sp_anim.MAPPING]
    keys = [struct.unpack_from('<I', body, p + 4)[0] for p in ms]
    assert bone_key not in keys, f'bone 0x{bone_key:08X} already has a track'
    assert all((b - a == 12 for a, b in zip(ms, ms[1:]))) and keys == sorted(keys), 'mappings not a sorted 12-byte list'
    count_at, elems_at = (ms[0] - 8, ms[-1] + 10)
    assert struct.unpack_from('<I', body, count_at)[0] == len(ms) == struct.unpack_from('<I', body, elems_at)[0]
    i = next((k for k, v in enumerate(keys) if v > bone_key), len(keys))
    ins = ms[i] - 2 if i < len(ms) else ms[-1] + 10
    one = bytearray(body[ms[0] - 2:ms[0] + 10])
    struct.pack_into('<HIH', one, 0, 0, sp_anim.MAPPING, 0)
    struct.pack_into('<I', one, 6, bone_key)
    els, stream_at = elements(body)
    assert stream_at == elems_at + 4
    out = bytearray(body[:ins] + bytes(one) * 2 + body[ins:stream_at])
    struct.pack_into('<I', out, count_at, len(ms) + 2)
    struct.pack_into('<I', out, elems_at + 24, len(ms) + 2)
    pieces = [body[p:e] for p, e, *_r in els]
    new_els = [_element(33, 'A', 6, [(0, vec_encode(t_value))]), _element(15, 'A', 8, [(0, quat8_encode(r_value))])]
    return fit_arenas(bytes(out) + b''.join(pieces[:i] + new_els + pieces[i:]))

def set_root_flag(body, on):
    n, recs, c = header(body)
    out = bytearray(body)
    out[c + 22] = 1 if on else 0
    return bytes(out)

def add_seed_track(body, donor, seed_class, time60, uid):
    cls = next((k for k, v in CLS.items() if v == seed_class))
    trs, _ = tracks(donor)
    for s, e, seeds, times, enum_at in trs:
        hit = [(a, z, t) for (a, z), t in zip(seeds, times) if struct.unpack_from('<I', donor, a + 5)[0] == cls]
        if hit:
            break
    else:
        raise KeyError(f'no {seed_class} in the donor')
    a, z, _t = hit[0]
    seed = bytearray(donor[a:z])
    struct.pack_into('<I', seed, 1, uid)
    track = donor[s:s + 11] + struct.pack('<I', 1) + bytes(seed) + donor[enum_at:enum_at + 4] + struct.pack('<HH', 1, 2 * time60)
    return _with_tracks(body, donor, [track])

def add_seed_tracks(body, donor, seed_class, uid_of, last120):
    cls = next((k for k, v in CLS.items() if v == seed_class))
    trs, _ = tracks(donor)
    new_tracks, added = ([], [])
    for s, e, seeds, times, enum_at in trs:
        hit = [(a, z, t) for (a, z), t in zip(seeds, times) if struct.unpack_from('<I', donor, a + 5)[0] == cls]
        if not hit:
            continue
        segs, ts = ([], [])
        for a, z, t in hit:
            seed = bytearray(donor[a:z])
            struct.pack_into('<I', seed, 1, uid_of(len(added)))
            t = min(t, last120)
            segs.append(bytes(seed))
            ts.append(t)
            added.append((t, bytes(seed)))
        new_tracks.append(donor[s:s + 11] + struct.pack('<I', len(segs)) + b''.join(segs) + donor[enum_at:enum_at + 4] + struct.pack('<H', len(ts)) + b''.join((struct.pack('<H', t) for t in ts)))
    if not new_tracks:
        return (body, [])
    return (_with_tracks(body, donor, new_tracks), added)

def _with_tracks(body, donor, new_tracks):
    n, recs, c = header(body)
    _trs_b, td = tracks(body)
    count = struct.unpack_from('<I', body, c + 23)[0]
    head = bytearray(body[:c + 27])
    struct.pack_into('<I', head, c + 23, count + len(new_tracks))
    track = b''.join(new_tracks)
    new = bytes(head) + body[c + 27:td] + track + body[td:]
    _nd, recs_d, _cd = header(donor)
    have = {(k, typ) for k, _z, typ, _n in recs}
    seg = set(sp_anim_classes(track))
    event_seed = next((k for k, v in CLS.items() if v == 'EventSeed'))
    add = [r for r in recs_d if r[0] and (r[0], r[2]) not in have and (r[0] in seg and r[2] == 22 or (r[2] == 2589 and r[0] in (ANIM_TRACK_EVENT, event_seed)))]
    if add:
        n2, recs2, c2 = header(new)
        rows = [list(r) for r in recs2] + [list(r) for r in add]
        new = new[:4] + struct.pack('<I', len(rows)) + b''.join((struct.pack('<IHHI', *r) for r in rows)) + new[8 + 12 * n2:]
    out = fit_arenas(recount(new, new))
    assert not check(out), check(out)
    return out

def sp_anim_classes(seg):
    out = []
    for p in range(len(seg) - 8):
        if seg[p] == 0:
            k = struct.unpack_from('<I', seg, p + 5)[0]
            if k in CLS and k > 1048575:
                out.append(k)
    return out

def _yaw_quat(deg):
    h = math.radians(deg) / 2
    return (0.0, 0.0, math.sin(h), math.cos(h))

def _yaw_of(q):
    x, y, z, w = q
    return math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))

def _sample_lin(times, vals, t):
    if t <= times[0]:
        return vals[0]
    for k in range(len(times) - 1):
        if times[k] <= t <= times[k + 1]:
            f = (t - times[k]) / max(1, times[k + 1] - times[k])
            return tuple((a + (b - a) * f for a, b in zip(vals[k], vals[k + 1])))
    return vals[-1]

def glide(body, t_end, end_yaw=0.0):
    mv = bone_tracks(body, 0)
    moving = mv.get('T') and len({tuple((round(c, 3) for c in v)) for v in mv['T'][1]}) > 1
    if moving or (mv.get('R') and len({tuple((round(c, 4) for c in q)) for q in mv['R'][1]}) > 1):
        body = rebase(body, 0.0, 0.0, 0.0)
    ref = bone_tracks(body, REFERENCE)
    end = length60(body)
    times = sorted({t for t in ref['T'][0] if t <= end} | {0, min(t_end, end), end})

    def s(t):
        if t >= t_end:
            return 1.0
        u = t / max(1, t_end)
        return u * u * (3 - 2 * u)
    tk, rk = ([], [])
    for t in times:
        x, y, _z = _sample_lin(ref['T'][0], ref['T'][1], t)
        tk.append((t, (s(t) * x, s(t) * y, 0.0)))
        rk.append((t, _yaw_quat(s(t) * end_yaw)))
    return set_root_flag(replace_bone(body, 0, tk, rk), True)

def early_cancel(body, t_ci, donor, uid, end_yaw=0.0):
    out = glide(body, t_ci, end_yaw)
    return add_seed_track(out, donor, 'CanInterruptAnimEventSeed', min(t_ci, length60(out)), uid)

def entity_end(body):
    mv = bone_tracks(body, 0)
    x, y, _z = mv['T'][1][-1]
    return (x, y, _yaw_of(mv['R'][1][-1]) if 'R' in mv else 0.0)

def rebase(body, dx, dy, dyaw):
    n, recs, c = header(body)
    flag = body[c + 22]
    ref, mv = (bone_tracks(body, REFERENCE), bone_tracks(body, 0))
    end = length60(body)
    times = sorted({t for t in ref['T'][0] if t <= end} | {t for t in ref.get('R', ([], []))[0] if t <= end} | {0, end})
    rq = ref.get('R', ([0], [(0.0, 0.0, 0.0, 1.0)]))
    tk, rk = ([], [])
    ca, sa = (math.cos(math.radians(-dyaw)), math.sin(math.radians(-dyaw)))
    for t in times:
        x, y, z = _sample_lin(ref['T'][0], ref['T'][1], t)
        q = rq[1][max([i for i, x2 in enumerate(rq[0]) if x2 <= t] or [0])]
        if flag == 0 and mv.get('T'):
            mx, my, _mz = _sample_lin(mv['T'][0], mv['T'][1], t)
            myaw = _yaw_of(mv['R'][1][max([i for i, x2 in enumerate(mv['R'][0]) if x2 <= t] or [0])]) if mv.get('R') else 0.0
            cm, sm = (math.cos(math.radians(myaw)), math.sin(math.radians(myaw)))
            x, y = (mx + cm * x - sm * y, my + sm * x + cm * y)
            q = quat_mul(_yaw_quat(myaw), q)
        x, y = (x - dx, y - dy)
        x, y = (ca * x - sa * y, sa * x + ca * y)
        tk.append((t, (x, y, z)))
        rk.append((t, quat_mul(_yaw_quat(-dyaw), q)))
    out = replace_bone(body, REFERENCE, tk, rk)
    out = replace_bone(out, 0, [(0, (0.0, 0.0, 0.0))], [(0, (0.0, 0.0, 0.0, 1.0))])
    return set_root_flag(out, True)

def time_scale(body, length):
    end = length60(body)
    if end == length:
        return body
    f = length / end
    n, recs, c = header(body)
    trs, td = tracks(body)
    els, stream_at = elements(body)
    new_tracks = []
    for s, e, seeds, times, enum_at in trs:
        ts = [min(2 * length, int(round(t * f))) for t in times]
        out = body[s:s + 11] + struct.pack('<I', len(seeds)) + b''.join((body[a:z] for a, z in seeds))
        out += body[enum_at:enum_at + 4] + struct.pack('<H', len(seeds)) + b''.join((struct.pack('<H', t) for t in ts))
        new_tracks.append(out)
    new_els = []
    for pos, e_end, tag, kind, stride, Y, times, data, hdr in els:
        keys = {}
        for i, t in enumerate(times):
            keys[min(length, int(round(t * f)))] = body[data + stride * i:data + stride * (i + 1)]
        ks = sorted(keys.items())
        if kind == 'C':
            tag, kind = (tag - 2, 'A') if ks[-1][0] <= 255 else (tag - 1, 'B')
        if kind == 'A' and ks[-1][0] > 255:
            tag, kind = (tag + 1, 'B')
        new_els.append(_element(tag, kind, stride, ks))
    head = bytearray(body[:c + 27])
    struct.pack_into('<f', head, c + 8, length / 60.0)
    new = bytes(head) + b''.join(new_tracks) + body[td:stream_at] + b''.join(new_els)
    return fit_arenas(recount(new, body))

def fit_length(body, length):
    end = length60(body)
    n, recs, c = header(body)
    cid = struct.unpack_from('<I', body, c)[0]
    if end > length:
        return cut(body, 0, length, cid)
    if end < length:
        return join(body, hold(body, end, length - end, cid), cid)
    return body
REFERENCE = 743623600

def _window_keys(times, datas, lo, hi, tag=None):
    if len(times) == 1:
        return [(0, datas[0])]

    def at(t):
        k = max((i for i, x in enumerate(times) if x <= t))
        if times[k] == t or k + 1 >= len(times):
            return datas[k]
        f = (t - times[k]) / (times[k + 1] - times[k])
        v = interpolate(tag, datas[k], datas[k + 1], f)
        return v if v is not None else datas[k] if f < 0.5 else datas[k + 1]
    keys = [(0, at(lo))]
    keys += [(t - lo, d) for t, d in zip(times, datas) if lo < t < hi]
    if hi > lo and any((t >= hi for t in times)):
        keys.append((hi - lo, at(hi)))
    elif hi > lo and times[-1] > lo and (times[-1] < hi):
        pass
    return keys

def length60(body):
    c = header(body)[2]
    return round(struct.unpack_from('<f', body, c + 8)[0] * 60)

def cut(body, lo, hi, clip_id):
    n, recs, c = header(body)
    trs, td = tracks(body)
    els, stream_at = elements(body)
    end = length60(body)
    new_tracks = []
    for s, e, seeds, times, enum_at in trs:
        keep = [(a, z, t) for (a, z), t in zip(seeds, times) if 2 * lo <= t < 2 * hi or (hi >= end and t >= 2 * lo)]
        if not keep:
            continue
        out = body[s:s + 11] + struct.pack('<I', len(keep)) + b''.join((body[a:z] for a, z, _ in keep))
        out += body[enum_at:enum_at + 4] + struct.pack('<H', len(keep)) + b''.join((struct.pack('<H', t - 2 * lo) for _, _, t in keep))
        new_tracks.append(out)
    new_els = []
    for pos, e_end, tag, kind, stride, Y, times, data, hdr in els:
        datas = [body[data + stride * i:data + stride * (i + 1)] for i in range(Y)]
        mask_bytes = hdr[0] & 63 if kind == 'C' else None
        flags = hdr[0] & 192 if kind == 'C' else 0
        new_els.append(_element(tag, kind, stride, _window_keys(times, datas, lo, hi, tag), mask_bytes if (lo, hi) == (0, end) else None, flags))
    head = bytearray(body[:c + 27])
    struct.pack_into('<I', head, c, clip_id)
    if (lo, hi) != (0, end):
        struct.pack_into('<f', head, c + 8, (hi - lo) / 60.0)
    struct.pack_into('<I', head, c + 23, len(new_tracks))
    mid = body[td:stream_at]
    new = bytes(head) + b''.join(new_tracks) + mid + b''.join(new_els)
    return fit_arenas(recount(new, body))

def _mappings(body):
    return [struct.unpack_from('<II', body, p + 4) for p, k in sp_anim.markers(body) if k == sp_anim.MAPPING]

def _v4_to_v6(raw):
    i = struct.unpack('<i', raw)[0]
    return struct.pack('<3h', i >> 23, _sx(i >> 11 & 4095, 12), _sx(i & 2047, 11))
V4, V6 = (30 // 3, 33 // 3)
ARENA_KIND = {4: 7, 2: 5, 1: 3}

def _arena_need(els_bytes):
    need = {7: 0, 5: 0, 3: 0}
    for e in els_bytes:
        tag, x = (e[0], struct.unpack_from('<I', e, 1)[0])
        a = _a(KIND[tag][1])
        need[ARENA_KIND[a]] += -(-x // a)
    return need

def arena_need(body):
    els, _ = elements(body)
    trs, _ = tracks(body)
    need = _arena_need([body[e[0]:e[1]] for e in els])
    need[7] += len(_mappings(body))
    need[5] += sum((len(t[2]) for t in trs))
    return need

def _arenas(recs):
    return {typ: cnt for cls, _z, typ, cnt in recs if cls == 0}

def fit_arenas(body):
    n, recs, c = header(body)
    need = arena_need(body)
    recs = [list(r) for r in recs]
    for r in recs:
        if r[0] == 0 and r[2] in need:
            r[3] = max(r[3], need[r[2]])
    for t in (7, 5, 3):
        if need[t] and (not any((r[0] == 0 and r[2] == t for r in recs))):
            recs.insert(0, [0, 0, t, need[t]])
    return body[:4] + struct.pack('<I', len(recs)) + b''.join((struct.pack('<IHHI', *r) for r in recs)) + body[8 + 12 * n:]

def join(a, b, clip_id):
    na, recs_a, ca = header(a)
    nb, recs_b, cb = header(b)
    la, lb = (length60(a), length60(b))
    assert _mappings(a) == _mappings(b), "join: the two clips' tracks differ"
    assert a[ca + 20:ca + 22] == b[cb + 20:cb + 22], "join: the clips' first two flags differ"
    trs_a, td_a = tracks(a)
    trs_b, td_b = tracks(b)
    assert a[ca + 12:ca + 16] == b[cb + 12:cb + 16] and a[td_a + 9:td_a + 13] == b[td_b + 9:td_b + 13], "join: the clips' AnimationKey / layout word differ (one layout, one key: see the module notes)"
    els_a, st_a = elements(a)
    els_b, _st_b = elements(b)
    keys = [k for k, _v in _mappings(a)]
    for k, e in zip(keys, els_a):
        if k == 0:
            vals = {a[e[7] + e[4] * i:e[7] + e[4] * (i + 1)] for i in range(e[5])}
            assert len(vals) == 1 or a[ca + 22] == b[cb + 22], "join: a's key-0 (mover) track moves and the root flags differ"

    def track_parts(body, tr):
        s, e, seeds, times, enum_at = tr
        classes = frozenset((struct.unpack_from('<I', body, x + 5)[0] for x, _y in seeds))
        return (body[s:s + 11], [body[x:y] for x, y in seeds], list(times), body[enum_at:enum_at + 4], classes)
    pa = [track_parts(a, t) for t in trs_a]
    pb = [track_parts(b, t) for t in trs_b]
    used, pair = (set(), {})
    for same in (lambda x, y: x == y, lambda x, y: bool(x & y)):
        for i, (head, _s, _t, enum, classes) in enumerate(pa):
            if i in pair:
                continue
            j = next((j for j, t in enumerate(pb) if j not in used and same(t[4], classes) and (t[0][9:] == head[9:]) and (t[3] == enum)), None)
            if j is not None:
                used.add(j)
                pair[i] = j
    merged = []
    for i, (head, seeds, times, enum, _c) in enumerate(pa):
        if i in pair:
            j = pair[i]
            seeds, times = (seeds + pb[j][1], times + [t + 2 * la for t in pb[j][2]])
        merged.append((head, seeds, times, enum))
    merged += [(h, s, [t + 2 * la for t in ts], en) for j, (h, s, ts, en, _c) in enumerate(pb) if j not in used]
    new_tracks = [h + struct.pack('<I', len(s)) + b''.join(s) + en + struct.pack('<H', len(ts)) + b''.join((struct.pack('<H', t) for t in ts)) for h, s, ts, en in merged]
    new_els = []
    for ea, eb in zip(els_a, els_b):
        (_p, _e, tag_a, _k, sa, ya, ta, da, _h), (_p2, _e2, tag_b, _k2, sb, yb, tb, db, _h2) = (ea, eb)
        ra = [a[da + sa * i:da + sa * (i + 1)] for i in range(ya)]
        rb = [b[db + sb * i:db + sb * (i + 1)] for i in range(yb)]
        fa, fb = (tag_a // 3, tag_b // 3)
        if fa != fb:
            assert {fa, fb} == {V4, V6}, f"join: a track's codecs differ ({tag_a:#x} / {tag_b:#x})"
            ra = [_v4_to_v6(r) for r in ra] if fa == V4 else ra
            rb = [_v4_to_v6(r) for r in rb] if fb == V4 else rb
            fa, sa = (V6, 6)
        ka = list(zip(ta, ra))
        if ka[-1][0] < la:
            ka.append((la, ka[-1][1]))
        ks = ka + [(t + la, r) for t, r in zip(tb, rb) if t > 0]
        if len({r for _t, r in ks}) == 1:
            ks = ks[:1]
        kind = 'A' if ks[-1][0] <= 255 else 'B'
        new_els.append(_element(fa * 3 + (0 if kind == 'A' else 1), kind, sa, ks))
    have = {(cls, typ) for cls, _z, typ, _n in recs_a}
    recs = [list(r) for r in recs_a] + [list(r) for r in recs_b if r[0] != 0 and (r[0], r[2]) not in have]
    table = struct.pack('<I', len(recs)) + b''.join((struct.pack('<IHHI', *r) for r in recs))
    anim = bytearray(a[ca:ca + 27])
    struct.pack_into('<I', anim, 0, clip_id)
    struct.pack_into('<f', anim, 8, (la + lb) / 60.0)
    anim[20:23] = b[cb + 20:cb + 23]
    struct.pack_into('<I', anim, 23, len(new_tracks))
    new = a[:4] + table + a[8 + 12 * na:ca] + bytes(anim) + b''.join(new_tracks) + a[td_a:st_a] + b''.join(new_els)
    out = fit_arenas(recount(new, a))
    ev_end = tracks(out)[1]
    uids = [u for p, _k in sp_anim.markers(out) if p < ev_end and out[p - 5] == 0 and (u := struct.unpack_from('<I', out, p - 4)[0])]
    assert len(uids) == len(set(uids)), 'join: an inline object uid twice in the events'
    return out

def drop_seeds(body, names):
    drop = {k for k, v in CLS.items() if v in names}
    n, recs, c = header(body)
    trs, td = tracks(body)
    new_tracks = []
    for s, e, seeds, times, enum_at in trs:
        keep = [(x, y, t) for (x, y), t in zip(seeds, times) if struct.unpack_from('<I', body, x + 5)[0] not in drop]
        if not keep:
            continue
        out = body[s:s + 11] + struct.pack('<I', len(keep)) + b''.join((body[x:y] for x, y, _ in keep))
        out += body[enum_at:enum_at + 4] + struct.pack('<H', len(keep)) + b''.join((struct.pack('<H', t) for _, _, t in keep))
        new_tracks.append(out)
    head = bytearray(body[:c + 27])
    struct.pack_into('<I', head, c + 23, len(new_tracks))
    new = bytes(head) + b''.join(new_tracks) + body[td:]
    return recount(new, body)

def hold(body, t, length, clip_id):
    new = bytearray(cut(body, t, t, clip_id))
    n, recs, c = header(bytes(new))
    struct.pack_into('<f', new, c + 8, length / 60.0)
    return _drop_uid_seeds(bytes(new))

def _drop_uid_seeds(body):
    n, recs, c = header(body)
    trs, td = tracks(body)
    if not any((struct.unpack_from('<I', body, x + 1)[0] for _s, _e, seeds, _t, _a in trs for x, _y in seeds)):
        return body
    new_tracks = []
    for s, e, seeds, times, enum_at in trs:
        keep = [(x, y, t) for (x, y), t in zip(seeds, times) if not struct.unpack_from('<I', body, x + 1)[0]]
        if not keep:
            continue
        out = body[s:s + 11] + struct.pack('<I', len(keep)) + b''.join((body[x:y] for x, y, _ in keep))
        out += body[enum_at:enum_at + 4] + struct.pack('<H', len(keep)) + b''.join((struct.pack('<H', t) for _, _, t in keep))
        new_tracks.append(out)
    head = bytearray(body[:c + 27])
    struct.pack_into('<I', head, c + 23, len(new_tracks))
    return recount(bytes(head) + b''.join(new_tracks) + body[td:], body)

def recount(new, old):
    n, recs, c = header(new)
    trs, td = tracks(new)
    objs = {}
    for p, k in sp_anim.markers(new):
        if p < td:
            objs[k] = objs.get(k, 0) + 1
    arrays = {ANIM_TRACK_EVENT: len(trs), next((k for k, v in CLS.items() if v == 'EventSeed')): sum((len(t[2]) for t in trs))}
    rows = []
    for cls, z, typ, cnt in recs:
        if cls != 0 and typ == 22:
            cnt = objs.get(cls, 0)
        elif cls != 0 and typ == 2589 and (cls in arrays):
            cnt = arrays[cls]
        if cls != 0 and cnt == 0:
            continue
        rows.append((cls, z, typ, cnt))
    return new[:4] + struct.pack('<I', len(rows)) + b''.join((struct.pack('<IHHI', *r) for r in rows)) + new[8 + 12 * n:]

def check(body):
    problems = []
    try:
        trs, _td = tracks(body)
        elements(body)
        recs = header(body)[1]
        cap = _arenas(recs)
        problems += [f'arena type {t}: capacity {cap.get(t, 0)} < need {n}' for t, n in arena_need(body).items() if cap.get(t, 0) < n]
        problems += [f'empty event track @{t[0]}' for t in trs if not t[2]]
        problems += [f'descriptor row of class 0x{r[0]:08X} with count 0' for r in recs if r[0] and r[3] == 0]
    except AssertionError as e:
        problems.append(str(e))
    problems += sp_anim.check_stream(body)
    return problems
