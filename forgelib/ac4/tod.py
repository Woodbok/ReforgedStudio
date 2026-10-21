"""forgelib/ac4/tod.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(ROOT, 'maps'), ROOT]
import tod_snow
REF = bytes.fromhex('ba50871a')
VEC4, FLOAT, BYTE, U32 = (2347352579, 3486935725, 1480703275, 793583881)
SIZES = {FLOAT: ('f', 4), VEC4: ('4f', 16), BYTE: ('B', 1), U32: ('I', 4)}
LS, FOG, C = (617819384, 3135613939, 3232351376)
KEEP_AC3 = {(LS, 168625806, C), (LS, 2489946925, C), (LS, 1546146951), (LS, 1359145931, 763824768)}
MAPPED = {(LS, 2133844690): ((LS, 518929955), 1.0), (LS, 1953315074): ((LS, 518929955), 1.0), (LS, 697817303): ((LS, 2874537749), 1.0), (LS, 3115195862): ((LS, 2874537749), 1.0), (LS, 4024993586, C): ((LS, 3796431409, C), 1.0), (LS, 1815539653): ((LS, 4228852420), 0.88), (LS, 1924545678, C): ((LS, 3796431409, C), 1.0), (FOG, 2204796701, C): ((FOG, 4052796060, C), 1.0), (FOG, 2671633884): ((FOG, 2007492812), 1.0), (FOG, 1672141936): ((FOG, 1098121015), 1.0)}
NIGHT_FROM_AC3 = {(LS, 3627959164), (LS, 4228852420)}
KEYS_PER_HOUR = 120

def _at(e, t):
    ks, vs = (e['keys'], e['vals'])
    for i in range(len(ks) - 1):
        if ks[i] <= t <= ks[i + 1]:
            if ks[i + 1] == ks[i] or e['cls'] in (BYTE, U32):
                return vs[i] if t < ks[i + 1] else vs[i + 1]
            w = (t - ks[i]) / (ks[i + 1] - ks[i])
            return tuple((a + (b - a) * w for a, b in zip(vs[i], vs[i + 1])))
    return vs[-1]

def _night_from(src, ac3):
    vals = []
    for k, v in zip(src['keys'], src['vals']):
        h = k / KEYS_PER_HOUR
        vals.append(_at(ac3, k) if h <= 4.5 or h >= 19.5 else v)
    return dict(src, vals=vals)

def parse(body):
    out = {}
    for s, e, path in tod_snow.entries(body):
        seg = body[s:e]
        last = seg.rfind(REF)
        k = last + 42 + 3
        cls = struct.unpack_from('<I', seg, k)[0]
        n, extra = struct.unpack_from('<HH', seg, k + 4)
        if cls not in SIZES or n > 200:
            out[path] = dict(seg=seg, cls=cls, n=n, keys=None, vals=None, tail=seg[k:])
            continue
        keys = [0] + list(struct.unpack_from(f'<{n - 1}H', seg, k + 8))
        fmt, sz = SIZES[cls]
        v0 = k + 8 + 2 * (n - 1)
        vals = [struct.unpack_from('<' + fmt, seg, v0 + i * sz) for i in range(n)]
        out[path] = dict(seg=seg, cls=cls, n=n, extra=extra, keys=keys, vals=vals, tail=seg[v0 + n * sz:])
    return out

def _entry(head_seg, src, scale=1.0):
    k = head_seg.rfind(REF) + 42 + 3
    cls = struct.unpack_from('<I', head_seg, k)[0]
    if cls != src['cls'] or src['keys'] is None:
        raise ValueError(f"curve class {cls:08X} vs source {src['cls']:08X}")
    fmt, _ = SIZES[cls]
    out = bytearray(head_seg[:k])
    out += struct.pack('<IHH', cls, src['n'], 0)
    out += struct.pack(f"<{src['n'] - 1}H", *src['keys'][1:])
    for v in src['vals']:
        if cls == VEC4:
            v = (v[0], v[1], v[2], 1.0)
        elif scale != 1.0:
            v = tuple((x * scale for x in v))
        out += struct.pack('<' + fmt, *v)
    out += src['tail']
    return bytes(out)
SUN_PATHS = {(LS, 4228852420), (LS, 1815539653)}

def _day_scale(curve, f):
    vals = [tuple((x * f for x in v)) if 4.5 < k / KEYS_PER_HOUR < 19.5 else v for k, v in zip(curve['keys'], curve['vals'])]
    return dict(curve, vals=vals)

def convert(ac3_body, ac4_body, uid=None, night_ac3=False, sun_scale=1.0):
    src = parse(ac4_body)
    a3 = parse(ac3_body)
    out = bytearray(ac3_body[:18])
    log = {'shared': 0, 'mapped': 0, 'kept': 0}
    for s, e, path in tod_snow.entries(ac3_body):
        seg = ac3_body[s:e]
        if path in src and path not in KEEP_AC3 and (src[path]['keys'] is not None):
            cur = src[path]
            if night_ac3 and path in NIGHT_FROM_AC3 and (a3[path]['keys'] is not None):
                cur = _night_from(cur, a3[path])
                log['night from AC3'] = log.get('night from AC3', 0) + 1
            if sun_scale != 1.0 and path in SUN_PATHS:
                cur = _day_scale(cur, sun_scale)
                log['sun scaled'] = log.get('sun scaled', 0) + 1
            out += _entry(seg, cur)
            log['shared'] += 1
        elif path in MAPPED and MAPPED[path][0] in src:
            sp, sc = MAPPED[path]
            cur = src[sp]
            if sun_scale != 1.0 and path in SUN_PATHS:
                cur = _day_scale(cur, sun_scale)
                log['sun scaled'] = log.get('sun scaled', 0) + 1
            out += _entry(seg, cur, sc)
            log['mapped'] += 1
        else:
            out += seg
            log['kept'] += 1
    body = bytes(out)
    if uid is not None:
        assert body[:2] == b'\x00\x01'
        body = body[:2] + struct.pack('<I', uid) + body[6:]
    check(body, ac3_body)
    return (body, log)

def check(body, ac3_body):
    a, b = (tod_snow.entries(ac3_body), tod_snow.entries(body))
    if [p for _, _, p in a] != [p for _, _, p in b]:
        raise ValueError("TR1: the paths differ from the AC3 template's")
    bad = [p for p, v in parse(body).items() if v['keys'] is None]
    if bad:
        raise ValueError(f'TR1: {len(bad)} curves do not parse')
if __name__ == '__main__':
    sys.path.insert(0, HERE)
    import common

    def body_of(path, name):
        for rec, d in common.payloads(path):
            for o in common.objects_in(d):
                if o['name'].strip() == name:
                    return o['body']
        raise SystemExit(f'{name} not in {path}')
    plan = body_of(common.ac3_archive('DataPC_AC3MP_Plantation.forge'), 'AC3MP_PLAN_TOD')
    for label, path, name in (('Tampa base', os.path.join(common.AC4, common.MSETTLEMENT), 'AC3MP_MSET_TOD'), ('Tampa weather', os.path.join(common.AC4, common.MSETTLEMENT), 'ACGA_MSET_Weather_TOD'), ('AC4 Plantation (control)', os.path.join(common.AC4, common.SHARED_MAPS['Plantation'][1]), 'ACGA_PLAN_TOD')):
        body, log = convert(plan, body_of(path, name))
        print(f"{label}: {log}, {len(body)} B (Plantation's {len(plan)}): gate passed")
