"""forgelib/ac4/rain.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
WPS_CLASS = 577822314
WPD_CLASS = 1462817550
BV_CLASS = 1256993910
LIST_END = bytes((3,)) + bytes(9)

def u32(b, k):
    return struct.unpack_from('<I', b, k)[0]

def parse_wps(seg):
    if u32(seg, 0) != WPS_CLASS or seg[4] != 0:
        raise ValueError('not a WorldParticleSystem')
    owner, n = (u32(seg, 5), u32(seg, 9))
    regs = [u32(seg, 13 + 5 * j + 1) for j in range(n)]
    p = 13 + 5 * n
    if seg[p:p + 2] != bytes((1, 0)):
        raise ValueError('no prototype link')
    return (owner, regs, u32(seg, p + 2), seg[p + 6:p + 8], seg[p + 8:])

def wps_segment(owner, wpd_uids, proto, fx, tail):
    return struct.pack('<I', WPS_CLASS) + bytes(1) + struct.pack('<I', owner) + struct.pack('<I', len(wpd_uids)) + b''.join((bytes(1) + struct.pack('<I', u) for u in wpd_uids)) + bytes((1, 0)) + struct.pack('<I', proto) + fx + tail

def wpd_points(body):
    bv = body.find(struct.pack('<I', BV_CLASS))
    box = struct.unpack_from('<6f', body, bv + 4)
    n = u32(body, bv + 4 + 28)
    return (box, [struct.unpack_from('<3f', body, bv + 36 + 12 * i) for i in range(n)])

def wpd_body(uid, points, pad):
    mn = [min((p[i] for p in points)) - pad[i] for i in range(3)]
    mx = [max((p[i] for p in points)) + pad[3 + i] for i in range(3)]
    return bytes((0, 1)) + struct.pack('<I', uid) + struct.pack('<I', WPD_CLASS) + struct.pack('<I', uid + 1) + struct.pack('<I', BV_CLASS) + struct.pack('<7f', *mn, *mx, 0.0) + struct.pack('<I', len(points)) + b''.join((struct.pack('<3f', *p) for p in points)) + bytes(4)

def thin(points, step=5.0):
    seen, out = (set(), [])
    for p in points:
        k = (int(p[0] // step), int(p[1] // step))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out

def pad_of(box, points):
    mn = [min((p[i] for p in points)) for i in range(3)]
    mx = [max((p[i] for p in points)) for i in range(3)]
    return (mn[0] - box[0], mn[1] - box[1], mn[2] - box[2], box[3] - mx[0], box[4] - mx[1], box[5] - mx[2])

def plan(ripple_sets, high_set):
    every, low = ({}, {})
    for i, s in enumerate(ripple_sets):
        for cell, pts in s.items():
            every.setdefault(cell, []).extend(pts)
            if i != high_set:
                low.setdefault(cell, []).extend(pts)
    high = ripple_sets[high_set]
    th = lambda d: {c: thin(p) for c, p in d.items() if p}
    return [('all surfaces', every), ('ground, 5 m', th(low)), ('roofs, 5 m', th(high)), ('roofs, 5 m', th(high)), ('all surfaces, 5 m', th(every))]

def extend_world(segs, owner, new):
    names = [n for n, _ in segs]
    ao = names.index('AOSettings')
    wps = [i for i, n in enumerate(names) if n == 'WorldParticleSystem']
    if not wps or wps[0] != ao + 1 or wps != list(range(wps[0], wps[0] + len(wps))):
        raise ValueError('WorldParticleSystems not right after AOSettings')
    out = [s for _, s in segs]
    a = out[ao]
    count = u32(a, len(a) - 9)
    if count != len(wps):
        raise ValueError(f'AOSettings counts {count}, {len(wps)} systems follow')
    out[ao] = a[:len(a) - 9] + struct.pack('<I', count + len(new)) + a[len(a) - 5:]
    last = out[wps[-1]]
    if not last.endswith(LIST_END):
        raise ValueError('the last system does not end the list')
    out[wps[-1]] = last[:-len(LIST_END)] + bytes(1) + struct.pack('<I', new[0][0])
    added = []
    for k, (own, regs, proto, fx) in enumerate(new):
        tail = bytes(1) + struct.pack('<I', new[k + 1][0]) if k + 1 < len(new) else LIST_END
        added.append(wps_segment(owner, regs, proto, fx, tail))
    out[wps[-1] + 1:wps[-1] + 1] = added
    return b''.join(out)
