"""forgelib/maps/port/zones.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import math
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (MAPS, ROOT):
    sys.path.insert(0, p)
import entity
import mapwalk
import xrefs
ZONE, CONVEX, SECTION, FX = (4255499137, 3962683792, 4108565151, 2219506216)
AC3_MAPS = ('DataPC_AC3MP_Plantation.forge', 'DataPC_AC3MP_Boston_Mill.forge', 'DataPC_AC3MP_Animus.forge', 'DataPC_AC3MP_Iceberg.forge', 'DataPC_AC3MP_Boston_Brewer.forge', 'DataPC_AC3MP_Boston_Harbou.forge', 'DataPC_AC3MP_Fort_Wolcott.forge')

class Reader:

    def __init__(self, b, at):
        self.b, self.at = (b, at)

    def u8(self):
        v = self.b[self.at]
        self.at += 1
        return v

    def u32(self):
        v = struct.unpack_from('<I', self.b, self.at)[0]
        self.at += 4
        return v

    def raw(self, n):
        v = self.b[self.at:self.at + n]
        self.at += n
        return v

    def expect(self, cls):
        z, c = (self.u32(), self.u32())
        if z != 0 or c != cls:
            raise ValueError(f'at {self.at - 8}: expected 0, {cls:#x}; got {z:#x}, {c:#x}')

def read_section(r):
    r.expect(SECTION)
    raw = r.raw(120)
    fx = []
    for _ in range(r.u32()):
        r.expect(FX)
        fx.append(r.raw(24))
    return {'raw': raw, 'fx': fx}

def parse(el):
    r = Reader(el, 0)
    flags, uid, cls = (r.raw(2), r.u32(), r.u32())
    if cls != ZONE:
        raise ValueError('not a GameplayZoneComponent')
    z = {'flags': flags, 'uid': uid, 'base': r.u8(), 'settings': r.raw(6), 'a': r.u32(), 'b': r.u32(), 'visual': r.raw(5), 'convex': [], 'border': []}
    for _ in range(r.u32()):
        r.expect(CONVEX)
        z['convex'].append([read_section(r) for _ in range(r.u32())])
    z['border'] = [read_section(r) for _ in range(r.u32())]
    z['other'] = r.raw(5)
    z['end'] = r.at
    return z

def write_section(s):
    out = struct.pack('<II', 0, SECTION) + s['raw'] + struct.pack('<I', len(s['fx']))
    return out + b''.join((struct.pack('<II', 0, FX) + f for f in s['fx']))

def write(z):
    out = z['flags'] + struct.pack('<II', z['uid'], ZONE) + bytes([z['base']]) + z['settings']
    out += struct.pack('<II', z['a'], z['b']) + z['visual'] + struct.pack('<I', len(z['convex']))
    for secs in z['convex']:
        out += struct.pack('<III', 0, CONVEX, len(secs)) + b''.join((write_section(s) for s in secs))
    out += struct.pack('<I', len(z['border'])) + b''.join((write_section(s) for s in z['border']))
    return out + z['other']

def section_fields(raw):
    f = struct.unpack('<30f', raw)
    return {'v0': f[0:4], 'v1': f[4:8], 'v2': f[8:12], 'v3': f[12:16], 'pair': f[16:18], 'v4': f[18:22], 'v5': f[22:26], 'v6': f[26:30]}

def zones_of(archive):
    out = []
    for p in mapwalk.payloads('AC3', archive, skip_errors=True):
        for o in p.objects:
            if xrefs.cls('AC3', o['type']) != 'Entity':
                continue
            comps = entity.components(o['body']) or []
            for s, e, n in comps:
                if n == 'GameplayZoneComponent':
                    el = o['body'][s:e]
                    out.append((p.rec.name, o['name'], el, parse(el)))
    return out

def closing_problems(body):
    comps = entity.components(body)
    out = []
    for s, e, n in comps or []:
        if n != 'GameplayZoneComponent':
            continue
        want = entity.exact_end(comps, e) - s
        try:
            got = parse(body[s:e])['end']
        except (ValueError, struct.error, IndexError) as x:
            out.append(f'zone at +{s} does not read: {x}')
            continue
        if got != want:
            out.append(f'zone at +{s} reads {got} bytes where its own are {want}' + (": the entity's first closing byte is missing" if got == want + 1 else ''))
    return out
WALL_TOL = 0.05

def mesh_vertices(mbody):
    nv = struct.unpack_from('<I', mbody, 67)[0]
    out = []
    for k in range(nv):
        x, y, z, w = struct.unpack_from('<4h', mbody, 91 + 20 * k)
        r = abs(w) / 8 / 32767
        out.append((x * r, y * r, z * r))
    return out

def panel_segments(z):
    out = []
    for secs in z['convex']:
        for s in secs:
            f = section_fields(s['raw'])
            c, d, w = (f['v0'], f['v3'], f['pair'][0])
            out.append(((c[0] - d[0] * w / 2, c[1] - d[1] * w / 2), (c[0] + d[0] * w / 2, c[1] + d[1] * w / 2)))
    return out

def _seg_dist(a, b, p):
    dx, dy = (b[0] - a[0], b[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1)))
    return ((p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2) ** 0.5

def wall_offset(body, mbody, transform=True):
    zc = next(((s, e) for s, e, n in entity.components(body) or [] if n == 'GameplayZoneComponent'), None)
    if zc is None:
        return None
    segs = panel_segments(parse(body[zc[0]:zc[1]]))
    if not segs:
        return None
    rows = struct.unpack_from('<12f', body, 10)
    X, Y = (rows[0:3], rows[4:7])
    px, py, _pz = struct.unpack_from('<3f', body, 58)
    worst = 0.0
    for lx, ly, lz in mesh_vertices(mbody):
        if transform:
            p = (px + lx * X[0] + ly * Y[0] + lz * rows[8], py + lx * X[1] + ly * Y[1] + lz * rows[9])
        else:
            p = (px + lx, py + ly)
        worst = max(worst, min((_seg_dist(a, b, p) for a, b in segs)))
    return worst

def wall_mesh_uid(body):
    vis = next((s for s, e, n in entity.components(body) or [] if n == 'Visual'), None)
    return None if vis is None else struct.unpack_from('<I', body, vis + 13)[0]

def wall_problems(body, objects):
    if struct.pack('<I', ZONE) not in body:
        return []
    uid = wall_mesh_uid(body)
    if uid is None:
        return []
    mesh = objects.get(uid)
    if mesh is None:
        return [f"its Visual's mesh {uid:#010x} isn't in the archive"]
    off = wall_offset(body, mesh['body'])
    if off is None or off <= WALL_TOL:
        return []
    flat = wall_offset(body, mesh['body'], transform=False)
    rows = struct.unpack_from('<3f', body, 10)
    return [f'its wall is drawn {off:.2f} m off its own panels (entity turned {math.degrees(math.atan2(rows[1], rows[0])):.1f} deg; {flat:.2f} m read in world axes)']

def check_archive(path):
    from forge import Forge
    n, bad, ents, objs = (0, [], [], {})
    pat = struct.pack('<I', ZONE)
    with Forge(os.path.abspath(path)) as f:
        for r in f.records:
            if not r.size or r.name == 'GlobalMetaFile':
                continue
            p = mapwalk.Payload(r, f.read(r))
            found = []
            for o in p.objects:
                if pat not in o['body'] or xrefs.cls('AC3', o['type']) != 'Entity':
                    continue
                n += 1
                bad += [(r.name, o['name'], x) for x in closing_problems(o['body'])]
                found.append(o)
            if found:
                objs.update(((o['uid'], o) for o in p.objects if o['has_uid']))
                ents += [(r.name, o) for o in found]
        missing = {wall_mesh_uid(o['body']) for _r, o in ents} - set(objs) - {None}
        if missing:
            for r in f.records:
                if r.size and r.name != 'GlobalMetaFile':
                    objs.update(((o['uid'], o) for o in mapwalk.Payload(r, f.read(r)).objects if o['has_uid'] and o['uid'] in missing))
    for rname, o in ents:
        bad += [(rname, o['name'], x) for x in wall_problems(o['body'], objs)]
    return (n, bad)

def main(argv):
    if argv[:1] == ['--check']:
        failed = False
        for path in argv[1:]:
            n, bad = check_archive(path)
            failed |= bool(bad)
            print(f'{os.path.basename(path)}: {n} zone entities, {len(bad)} wrong')
            for fname, name, x in bad:
                print(f'   {fname} / {name}: {x}')
        if failed:
            raise SystemExit(1)
        return
    if argv[:1] == ['--dump']:
        arch = next((a for a in AC3_MAPS if argv[1].lower() in a.lower()))
        for fname, name, el, z in zones_of(arch):
            print(f"== {fname} {name}: a {z['a']:#x} b {z['b']:#x}, convex {[len(c) for c in z['convex']]}, border {len(z['border'])}")
            for label, secs in [(f'convex{i}', c) for i, c in enumerate(z['convex'])] + [('border', z['border'])]:
                for s in secs:
                    f = section_fields(s['raw'])
                    r = lambda v: '(' + ', '.join((f'{x:.2f}' for x in v)) + ')'
                    print(f"   {label:8} v0 {r(f['v0'])} v1 {r(f['v1'])} v2 {r(f['v2'])} v3 {r(f['v3'])} pair {r(f['pair'])} v4 {r(f['v4'])} v5 {r(f['v5'])} v6 {r(f['v6'])} fx {len(s['fx'])}")
        return
    total, c = (0, Counter())
    for arch in AC3_MAPS:
        for fname, name, el, z in zones_of(arch):
            total += 1
            ok = z['end'] == len(el) and write(z) == el
            c[ok] += 1
            print(f"{arch[13:]:18} {fname:28} {name[:40]:40} convex {[len(x) for x in z['convex']]} border {len(z['border'])} fx {sum((len(s['fx']) for x in z['convex'] for s in x))}+{sum((len(s['fx']) for s in z['border']))} round trip {ok}")
    print(f'{total} zones, round trip exact {c[True]}, failed {c[False]}')
if __name__ == '__main__':
    main(sys.argv[1:])
