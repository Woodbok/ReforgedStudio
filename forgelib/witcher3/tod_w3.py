"""forgelib/witcher3/tod_w3.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import pickle
import numpy as np
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'maps', 'port'), os.path.join(ROOT, 'ac4')):
    if p not in sys.path:
        sys.path.insert(0, p)
import mapwalk
import tod_snow
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from tod_evening import HOUR, KINDS, curve, evaluate
TOD_NAME = 'AC3MP_Boston2_TOD'
NAMES = os.path.join(ROOT, 'out', 'ac4', 'research_sky', 'names4.pkl')
W = 1.0
DAY, EVE = ((12, 16), (0, 24))
SKY_TOP, SKY_SIDE, GROUND = ((0.85, 0.82, 0.77), (0.72, 0.77, 0.82), (0.18, 0.18, 0.17))
SKY_TOP_E, SKY_SIDE_E, GROUND_E = ((0.79, 0.85, 0.94), (0.76, 0.84, 0.99), (0.12, 0.13, 0.15))
SUN, SUN_E = ((0.91, 0.9, 0.89), (0.98, 0.62, 0.49))
LOOK = {'LayeredSky / SunLightColor / C0A9C490': ('hue', SUN, SUN_E), 'LayeredSky / CharacterSunLightColor / C0A9C490': ('hue', SUN, SUN_E), 'LayeredSky / SunFlareColor / C0A9C490': ('hue', SUN, SUN_E), 'LayeredSky / EnvironmentAmbient / Top': ('hue', SKY_TOP, SKY_TOP_E), 'LayeredSky / EnvironmentAmbient / Front': ('hue', SKY_SIDE, SKY_SIDE_E), 'LayeredSky / EnvironmentAmbient / Back': ('hue', SKY_SIDE, SKY_SIDE_E), 'LayeredSky / EnvironmentAmbient / Left': ('hue', SKY_SIDE, SKY_SIDE_E), 'LayeredSky / EnvironmentAmbient / Right': ('hue', SKY_SIDE, SKY_SIDE_E), 'LayeredSky / EnvironmentAmbient / Down': ('hue', GROUND, GROUND_E), 'LayeredSky / CharacterAmbient / Top': ('hue', SKY_TOP, SKY_TOP_E), 'LayeredSky / CharacterAmbient / Front': ('hue', SKY_SIDE, SKY_SIDE_E), 'LayeredSky / CharacterAmbient / Back': ('hue', SKY_SIDE, SKY_SIDE_E), 'LayeredSky / CharacterAmbient / Left': ('hue', SKY_SIDE, SKY_SIDE_E), 'LayeredSky / CharacterAmbient / Right': ('hue', SKY_SIDE, SKY_SIDE_E), 'LayeredSky / CharacterAmbient / Down': ('hue', GROUND, GROUND_E), 'Fog / FogColor / C0A9C490': ('hue', (0.6, 0.69, 0.84), (0.5, 0.65, 0.84)), 'Fog / FogNear': ((30.0,), (25.0,)), 'Fog / FogFar': ((400.0,), (300.0,)), 'Fog / FogDensity': ((0.5,), (0.6,)), '5FB26017 / ColourControlSettings / Saturation': ((1.1,), (1.05,)), '5FB26017 / ColourControlSettings / Contrast': ((1.05,), (1.07,)), '5FB26017 / AssassinFilterSettings / A2924366': ((0.35,), (0.4,)), '5FB26017 / AssassinFilterSettings / B0C02CE4 / C0A9C490': ((0.8, 0.85, 0.88, W), (0.85, 0.8, 0.75, W))}

def lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

def recolour(old, rgb):
    k = lum(old) / max(lum(rgb), 1e-06)
    return tuple((x * k for x in rgb)) + tuple(old[3:])

def names():
    sys.path.insert(0, os.path.join(ROOT, 'ac4'))
    import common
    N = {k: sorted(v)[0] if isinstance(v, set) else v for k, v in pickle.load(open(NAMES, 'rb')).items()}
    for bad in (2053632513, 3815678907, 554446067):
        N.pop(bad, None)
    reg = {**common.REG.get('AC4', {}), **common.REG.get('AC3', {})}
    return lambda path: ' / '.join((N.get(x) or reg.get(x) or '%08X' % x for x in path))

def _find_tod(path):
    with Forge(path) as fg:
        for rec in fg.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            p = mapwalk.Payload(rec, fg.read(rec))
            tods = [o for o in p.objects if (o['name'] or '').strip() == TOD_NAME]
            if tods:
                return (rec, p, tods[0])
    raise SystemExit('%s: no %s' % (path, TOD_NAME))

def reference_curves(nm):
    sys.path.insert(0, os.path.join(ROOT, 'dishonored'))
    import ac3side
    _r, _p, t = _find_tod(ac3side.release_map('DataPC_AC3MP_Boston_Mill.forge'))
    b = t['body']
    return ({nm(path): curve(b[s_:e_]) for s_, e_, path in tod_snow.entries(b)}, b)
DAY_H = (4.5, 19.5)
SUN_SCALE = float(os.environ.get('KM_SUN', '0.85'))
HUE = {'LayeredSky / SunLightColor / C0A9C490': SUN, 'LayeredSky / CharacterSunLightColor / C0A9C490': SUN, 'LayeredSky / SunFlareColor / C0A9C490': SUN, 'LayeredSky / EnvironmentAmbient / Top': SKY_TOP, 'LayeredSky / CharacterAmbient / Top': SKY_TOP, 'LayeredSky / EnvironmentAmbient / Down': GROUND, 'LayeredSky / CharacterAmbient / Down': GROUND}
for _f in ('Front', 'Back', 'Left', 'Right'):
    HUE['LayeredSky / EnvironmentAmbient / ' + _f] = SKY_SIDE
    HUE['LayeredSky / CharacterAmbient / ' + _f] = SKY_SIDE
SKY = 'LayeredSky / 02C2D99C / C0A9C490'

def v3_value(name, h, x, sky):
    day = DAY_H[0] < h < DAY_H[1]
    if name == '5FB26017 / AssassinFilterSettings / A2924366':
        return (0.0,)
    if not day:
        return None
    if name == '5FB26017 / ColorBalanceSettings / Gamma':
        return (1 + (x[0] - 1) * 0.5,)
    if name == '5FB26017 / ColourControlSettings / Brightness':
        return (1.0,)
    if name == 'LayeredSky / EnvironmentAmbient / Scale':
        return (x[0] * 0.7,)
    if name == 'LayeredSky / CharacterAmbient / Scale':
        return (x[0] * 0.8,)
    if name in ('LayeredSky / SunLightIntensity',):
        return (x[0] * SUN_SCALE,)
    if name == 'Fog / FogColor / C0A9C490':
        return tuple((c + (t - c) * 0.7 for c, t in zip(x[:3], sky[:3]))) + tuple(x[3:])
    if name == 'Fog / FogNear':
        return (x[0] * 40 / 25,)
    if name == 'Fog / FogFar':
        return (x[0] * 350 / 150,)
    if name == 'Fog / FogDensity':
        return (x[0] * 0.6 / 0.8,)
    if name == WARM_TINT:
        return recolour(x, (0.92, 0.95, 1.0))
    if name in HUE:
        if 'SunLightColor' in name or 'SunFlareColor' in name:
            x = tuple((c + (1 - c) * 0.35 for c in x[:3])) + tuple(x[3:])
        return recolour(x, HUE[name])
    return None
SKY_TEX = 'ACFE_Rhodes_Skydome_LUT_color_Map'
SKY_COLS = range(8, 12)
SKY_ROWS = [(212, 218, 228), (192, 207, 224), (168, 194, 220), (143, 180, 214), (118, 165, 206), (96, 150, 197), (78, 137, 188), (64, 126, 180)]
DOME_TEX = 'ACFE_Skydome_01_Map'
LAYERED = 'AC3MP_Boston2_LayeredSky'
SKY_FROM, SKY_TO = ('AC3MP_MILL_SkyDome_01A', 'ACFE_Rhodes_SkyDome_Layer0')

def uid_of(o):
    return struct.unpack_from('<I', o['body'], 2)[0]

def layered_body(objects):
    by = {(o['name'] or '').strip(): o for o in objects}
    ls, a, b = (by[LAYERED], uid_of(by[SKY_FROM]), uid_of(by[SKY_TO]))
    body = ls['body']
    at = [i for i in range(6, len(body) - 3) if struct.unpack_from('<I', body, i)[0] == a]
    if len(at) != 1 or at[0] < len(body) - 16:
        raise SystemExit('%s: %s referenced at %s, expected once at its end' % (LAYERED, SKY_FROM, at))
    return body[:at[0]] + struct.pack('<I', b) + body[at[0] + 4:]
WARM_TINT = 'LayeredSky / CB26735F / C0A9C490'

def sky_body(body):
    import dxt
    from ac3editor import materials as AM
    w, h, px = dxt.decode(body)
    a = np.frombuffer(bytes(px), np.uint8).reshape(h, w, 4).copy()
    for c in SKY_COLS:
        for y in range(h):
            a[y, c, :3] = SKY_ROWS[min(y * len(SKY_ROWS) // h, len(SKY_ROWS) - 1)]
    return (AM.texture_body(body, a), a)

def apply_v3(src, dest, log=print):
    nm = names()
    ref, b = reference_curves(nm)
    sky = ref[SKY]
    image = ArchiveImage.read(src)
    rec, p, tod = _find_tod(src)
    if [x[2] for x in tod_snow.entries(b)] != [x[2] for x in tod_snow.entries(tod['body'])]:
        raise SystemExit('%s: its TOD paths differ from the release Mill TOD' % src)
    out, last, done = (bytearray(), 0, {})
    for s_, e_, path in tod_snow.entries(b):
        name = nm(path)
        c = curve(b[s_:e_])
        if not c:
            continue
        k, cls, keys, vals, end = c
        new = []
        for t, x in zip(keys, vals):
            v = v3_value(name, t / HOUR, x, evaluate(sky[1], sky[2], sky[3], t))
            new.append(tuple(v[:len(x)]) if v is not None else tuple(x))
        if new == [tuple(x) for x in vals]:
            continue
        fmt = KINDS[cls][0]
        seg = b[s_:e_]
        seg = seg[:k] + struct.pack('<II', cls, len(keys)) + struct.pack('<%dH' % (len(keys) - 1), *keys[1:]) + b''.join((struct.pack(fmt, *v) for v in new)) + seg[end:]
        out += b[last:s_] + seg
        last = e_
        done[name] = (keys, new)
    body = bytes(out + b[last:])
    sky = next((o for o in p.objects if (o['name'] or '').strip() == SKY_TEX), None)
    sky_new, sky_px = sky_body(sky['body']) if sky is not None else (None, None)
    dome = next((o for o in p.objects if (o['name'] or '').strip() == DOME_TEX), None)
    dome_new = None
    if dome is not None:
        from ac3editor import materials as AM
        from witcher3 import sky as skypano
        dome_new = AM.texture_body(dome['body'], skypano.cloud_bias())
    lsky = next((o for o in p.objects if (o['name'] or '').strip() == LAYERED))
    new_body = {id(tod): body, id(lsky): layered_body(p.objects)}
    if sky is not None:
        new_body[id(sky)] = sky_new
    if dome is not None:
        new_body[id(dome)] = dome_new
    objs = [(o['serial'], mapwalk.object_bytes(o, new_body.get(id(o))), o['filter']) for o in p.objects]
    d = mapwalk.rebuild(objs, p.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, rec.index, payload)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    write(dest, image)
    if sky is not None:
        import dxt
        with Forge(dest) as fo:
            q = mapwalk.Payload(fo.records[rec.index], fo.read(fo.records[rec.index]))
            sb = [o for o in q.objects if (o['name'] or '').strip() == SKY_TEX][0]['body']
        w, h, px = dxt.decode(sb)
        got = np.frombuffer(bytes(px), np.uint8).reshape(h, w, 4)
        err = int(np.abs(got[:, list(SKY_COLS), :3].astype(int) - sky_px[:, list(SKY_COLS), :3].astype(int)).max())
        log('sky: %s columns %d-%d painted (horizon %s -> zenith %s); read back within %d / 255 (DXT5); dome %s %s' % (SKY_TEX, SKY_COLS[0], SKY_COLS[-1], SKY_ROWS[0], SKY_ROWS[-1], err, DOME_TEX, 'replaced by witcher3/sky.py cloud_bias' if dome is not None else 'NOT FOUND'))
        with Forge(dest) as fo:
            q = mapwalk.Payload(fo.records[rec.index], fo.read(fo.records[rec.index]))
            by = {(o['name'] or '').strip(): o for o in q.objects}
            refs = [i for i in range(6, len(by[LAYERED]['body']) - 3) if struct.unpack_from('<I', by[LAYERED]['body'], i)[0] == uid_of(by[SKY_TO])]
        log('sky: %s layer material -> %s (%08X at +%s)' % (LAYERED, SKY_TO, uid_of(by[SKY_TO]), refs))
        if len(refs) != 1:
            raise SystemExit('LayeredSky swap failed')
    ok = 0
    with Forge(dest) as fo, Forge(src) as fi:
        others = all((fo.read(x) == fi.read(y) for x, y in zip(fo.records, fi.records) if x.index != rec.index))
        q = mapwalk.Payload(fo.records[rec.index], fo.read(fo.records[rec.index]))
        nb = [o for o in q.objects if (o['name'] or '').strip() == TOD_NAME][0]['body']
        for ns, nend, path in tod_snow.entries(nb):
            name = nm(path)
            if name in done:
                cn = curve(nb[ns:nend])
                ok += all((all((abs(x - y) < 0.0001 for x, y in zip(v, w))) for v, w in zip(cn[3], done[name][1])))
    good = others and ok == len(done)
    log('time of day v4 (LT3, sun x %.2f, + W3 hues) %s: %d curves changed, read back %d; other records identical %s: %s' % (SUN_SCALE, TOD_NAME, len(done), ok, others, 'OK' if good else 'FAILED'))
    for n in ('LayeredSky / SunLightColor / C0A9C490', 'LayeredSky / EnvironmentAmbient / Front', '5FB26017 / ColorBalanceSettings / Gamma', 'Fog / FogFar'):
        if n in done:
            ks, vs = done[n]
            noon = [tuple((round(x, 3) for x in v)) for t, v in zip(ks, vs) if abs(t / HOUR - 12) < 0.2]
            log('  %s at 12 h: %s' % (n, noon))
    if not good:
        raise SystemExit('time-of-day check failed')
    return len(done)

def apply(src, dest, look=None, log=print):
    look = dict(LOOK if look is None else look)
    nm = names()
    ref, ref_body = reference_curves(nm)
    image = ArchiveImage.read(src)
    rec, p, tod = _find_tod(src)
    if [x[2] for x in tod_snow.entries(ref_body)] != [x[2] for x in tod_snow.entries(tod['body'])]:
        raise SystemExit('%s: its TOD paths differ from the release Mill TOD' % src)
    b = ref_body
    out, last, done = (bytearray(), 0, {})
    for s_, e_, path in tod_snow.entries(b):
        name = nm(path)
        if name not in look:
            continue
        c = curve(b[s_:e_])
        if not c:
            raise SystemExit('%s: curve not read' % name)
        k, cls, keys, vals, end = c
        spec = look[name]
        n = len(vals[0])
        if spec[0] == 'hue':
            _k, rcls, rkeys, rvals, _e = ref[name]
            day = recolour(evaluate(rcls, rkeys, rvals, 12 * HOUR), spec[1])
            eve = recolour(evaluate(rcls, rkeys, rvals, 0), spec[2])
        else:
            day, eve = spec
        day, eve = (tuple(day[:n]), tuple(eve[:n]))
        at = {0: eve, 12 * HOUR: day, 16 * HOUR: day, 24 * HOUR: eve}
        ticks = sorted(at)
        fmt = KINDS[cls][0]
        seg = b[s_:e_]
        seg = seg[:k] + struct.pack('<II', cls, len(ticks)) + struct.pack('<%dH' % (len(ticks) - 1), *ticks[1:]) + b''.join((struct.pack(fmt, *at[t]) for t in ticks)) + seg[end:]
        out += b[last:s_] + seg
        last = e_
        done[name] = at
    missing = sorted(set(look) - set(done))
    if missing:
        raise SystemExit('curves not found in %s: %s' % (TOD_NAME, missing))
    body = bytes(out + b[last:])
    objs = [(o['serial'], mapwalk.object_bytes(o, body if o is tod else None), o['filter']) for o in p.objects]
    d = mapwalk.rebuild(objs, p.filter_table)
    payload = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(payload) == d
    set_payload(image, rec.index, payload)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    write(dest, image)
    ok = 0
    with Forge(dest) as fo, Forge(src) as fi:
        others = all((fo.read(x) == fi.read(y) for x, y in zip(fo.records, fi.records) if x.index != rec.index))
        q = mapwalk.Payload(fo.records[rec.index], fo.read(fo.records[rec.index]))
        nb = [o for o in q.objects if (o['name'] or '').strip() == TOD_NAME][0]['body']
        ne, oe = (tod_snow.entries(nb), tod_snow.entries(b))
        same_paths = [x[2] for x in ne] == [x[2] for x in oe]
        kept = 0
        for (ns, nend, path), (os_, oend, _p) in zip(ne, oe):
            name = nm(path)
            if name in done:
                cn = curve(nb[ns:nend])
                ok += all((all((abs(x - y) < 0.0001 for x, y in zip(evaluate(cn[1], cn[2], cn[3], t), v))) for t, v in done[name].items()))
            else:
                kept += nb[ns:nend] == b[os_:oend]
        other_objs = all((o2['body'] == o1['body'] for o1, o2 in zip(p.objects, q.objects) if o1 is not tod))
    good = others and same_paths and other_objs and (ok == len(done)) and (kept == len(ne) - len(done))
    log('time of day %s: %d curves set (day 12-16 h, low sun 0 / 24 h), read back %d / %d; %d others unchanged; other objects / records identical %s / %s: %s' % (TOD_NAME, len(done), ok, len(done), kept, other_objs, others, 'OK' if good else 'FAILED'))
    if not good:
        raise SystemExit('time-of-day check failed')
    return len(done)
if __name__ == '__main__':
    (apply_v3 if '--v2' not in sys.argv else apply)(sys.argv[1], sys.argv[2])
