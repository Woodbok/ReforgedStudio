"""forgelib/ac4/pigeons.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import difflib
import struct
import sys
sys.path[:0] = []

def u32(b, k):
    return struct.unpack_from('<I', b, k)[0]

def _segments(body):
    import world
    return world.segments(body)

def _locals(body, gid):
    hi = gid >> 12
    return {k: u32(body, k) for k in range(len(body) - 3) if u32(body, k) >> 12 == hi}

def _offsets(segs):
    out, at = ([], 0)
    for n, s in segs:
        out.append(at)
        at += len(s)
    return out

def _own_uids(segs, gid):
    out = [gid]
    for k in range(1, len(segs)):
        v = u32(segs[k - 1][1], len(segs[k - 1][1]) - 4)
        out.append(v if v not in (0, 4294967295) else None)
    return out

def rebuild(ac3_tpl, ac4_tpl_conv, target_conv, fresh):
    g3, g4, gt = (u32(ac3_tpl, 2), u32(ac4_tpl_conv, 2), u32(target_conv, 2))
    s3, s4, st = (_segments(ac3_tpl), _segments(ac4_tpl_conv), _segments(target_conv))
    if [(n, len(s)) for n, s in s4] != [(n, len(s)) for n, s in st]:
        raise ValueError("the template's AC4 copy and the target differ in layout")
    own3, own4, ownt = (_own_uids(s3, g3), _own_uids(s4, g4), _own_uids(st, gt))
    m4t = {a: b for a, b in zip(own4, ownt) if a is not None and b is not None}
    sm = difflib.SequenceMatcher(None, [n for n, _ in s3], [n for n, _ in s4], autojunk=False)
    m34, paired = ({}, {})
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != 'equal':
            continue
        for k in range(i2 - i1):
            paired[i1 + k] = j1 + k
            if own3[i1 + k] is not None and own4[j1 + k] is not None:
                m34[own3[i1 + k]] = own4[j1 + k]
    m3t, back = ({g3: gt}, {})
    for v in own3:
        if v is None or v in m3t:
            continue
        if v in m34 and m34[v] in m4t:
            m3t[v] = m4t[m34[v]]
        else:
            f = next(fresh)
            m3t[v] = f
            back[f] = v

    def in_target_space(seg, m):
        out = bytearray(seg)
        for off in range(len(seg) - 3):
            v = u32(seg, off)
            if v in m:
                struct.pack_into('<I', out, off, m[v])
        return bytes(out)
    loc_t = {v for v in ownt if v is not None}
    out = []
    for i, (n, a) in enumerate(s3):
        a_t = in_target_space(a, m3t)
        j = paired.get(i)
        if j is None or len(s4[j][1]) != len(a):
            out.append(a_t)
            continue
        t = bytearray(st[j][1])
        for off in range(len(a_t) - 3):
            v = u32(a_t, off)
            if (v in loc_t or v in back) and v != u32(t, off) and (u32(t, off) in loc_t):
                struct.pack_into('<I', t, off, v)
        out.append(bytes(t))
    return (b''.join(out), back)
FUSED_HEAD = bytes.fromhex('9e1a1260010000000000000000000000000000000000000000000000000000000000000000000000000000000000003f000300000000010100')
AUDIO_SPLIT = ('EntityDescriptor', 'AudioEventSeed', 'AudioEvent', 'SoundInstance', 'SoundDescriptor', 'WwiseID', 'AudioEventSeed', 'AudioEvent', 'SoundInstance', 'SoundDescriptor', 'WwiseID')

def fuse_audio(body):
    segs = _segments(body)
    out, i, n = ([], 0, 0)
    while i < len(segs):
        names = tuple((nm for nm, _ in segs[i:i + 11]))
        if names == AUDIO_SPLIT and len(segs[i][1]) == 37:
            out.append(FUSED_HEAD + segs[i + 10][1][-4:])
            i += 11
            n += 1
            continue
        out.append(segs[i][1])
        i += 1
    return (b''.join(out), n)

def gate_fuse(pairs):
    good = 0
    for key, u, a3, b4 in pairs:
        s3, s4 = (_segments(a3), _segments(b4))
        i = next((k for k in range(len(s3)) if tuple((nm for nm, _ in s3[k:k + 11])) == AUDIO_SPLIT))
        j = next((k for k, (nm, x) in enumerate(s4) if nm == 'EntityDescriptor' and len(x) == 61))
        fused, n = fuse_audio(b''.join((x for _, x in s3[i:i + 11])))
        good += n == 1 and fused == s4[j][1]
    print(f"gate_fuse: {good} / {len(pairs)} AC3 split regions fused = their AC4 copy's descriptor")
    return good == len(pairs)

def gate():
    import os
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), 'maps'), os.path.dirname(HERE)]
    import common
    import downgrade
    import world
    pairs = []
    for key in ('Charlestown', 'Martinique', 'FortMathieu'):
        a3, a4 = common.SHARED_MAPS[key]

        def objs(path, game):
            out = {}
            for rec, d in common.payloads(path):
                for o in common.objects_in(d):
                    if 'AnimalAlarm_Pigeon' in o['name'] and common.cls(o['type'], game) == 'EntityGroup':
                        out[o['uid']] = o['body']
            return out
        A, B = (objs(common.ac3_archive(a3), 'AC3'), objs(os.path.join(common.AC4, a4), 'AC4'))
        for u in sorted(set(A) & set(B)):
            pairs.append((key, u, A[u], downgrade.convert('EntityGroup', B[u])[0]))
    sig = lambda b: tuple(((n, len(s)) for n, s in _segments(b)))
    exact = near = bad = 0
    for k, (key, u, a3, b4) in enumerate(pairs):
        tpl = next(((kk, uu, aa, bb) for kk, uu, aa, bb in pairs if uu != u and sig(bb) == sig(b4)), None)
        if tpl is None:
            continue
        fresh = iter(range(2130706432, 2130710528))
        try:
            got, back = rebuild(tpl[2], tpl[3], b4, fresh)
        except ValueError as e:
            bad += 1
            print('  error', key, hex(u), e)
            continue
        if got == a3:
            exact += 1
            continue
        sg, sa = (_segments(got), _segments(a3))
        if [(n, len(x)) for n, x in sg] != [(n, len(x)) for n, x in sa]:
            bad += 1
            print('  layout differs', key, hex(u))
            continue
        ids_g = {v for v in _own_uids(sg, u32(got, 2)) if v is not None}
        ids_a = {v for v in _own_uids(sa, u32(a3, 2)) if v is not None}
        s4x = _segments(b4)
        smx = difflib.SequenceMatcher(None, [n for n, _ in sa], [n for n, _ in s4x], autojunk=False)
        allowed = set()
        offs_a = _offsets(sa)
        for op, i1, i2, j1, j2 in smx.get_opcodes():
            if op == 'equal':
                for kk in range(i2 - i1):
                    x_, y_ = (sa[i1 + kk][1], s4x[j1 + kk][1])
                    if len(x_) == len(y_):
                        allowed |= {offs_a[i1 + kk] + q for q in range(len(x_)) if x_[q] != y_[q]}
        ren, inv, ok = ({}, {}, True)
        mg, ma = (bytearray(got), bytearray(a3))
        for off in range(len(got) - 3):
            v, w = (u32(got, off), u32(a3, off))
            if v in ids_g and w in ids_a:
                if ren.setdefault(v, w) != w or inv.setdefault(w, v) != v:
                    ok = False
                mg[off:off + 4] = ma[off:off + 4] = bytes(4)
        left = [q for q in range(len(mg)) if mg[q] != ma[q] and q not in allowed]
        if ok and (not left):
            near += 1
        else:
            bad += 1
            print('  differs', key, hex(u), 'renaming consistent', ok, 'bytes left', len(left), left[:8])
    print(f"gate: {exact} byte-identical, {near} identical up to a one-to-one renaming of the element ids and the target's own re-cooked values, {bad} differ (of {len(pairs)} pairs)")
    return bad == 0 and gate_fuse(pairs)
if __name__ == '__main__':
    sys.exit(0 if gate() else 1)
