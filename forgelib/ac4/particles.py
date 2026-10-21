"""forgelib/ac4/particles.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
B, U = ('b', 'I')
AC3_FIELDS = [('flag0', B), ('flag1', U), ('type3', U), ('mode2', U)] + [(f'flag{i}', B) for i in range(7, 12)] + [('sel3a', U), ('sel2a', U), ('sel2b', U), ('sel2c', U), ('flag21', B), ('sel2d', U)] + [(f'flag{i}', B) for i in range(24, 30)] + [('sel3b', U), ('sel2e', U), ('flag35', B), ('flag36', B), ('flag37', U), ('flag38', B), ('flag39', B), ('u78', U), ('w250a', B), ('w250b', B), ('w250c', B), ('b252', B), ('v10', '16x'), ('b253', B), ('v88', '12x'), ('col224', 'C'), ('col220', 'C'), ('b254', B), ('fa0', U), ('b255', B), ('fa8', U), ('b256', B), ('f94', U), ('b257', B), ('fd4', U), ('fd8', U), ('fdc', U), ('fe0', U), ('v60', '16x'), ('m20', '64x'), ('fe4', '12x'), ('ff0', U), ('ff4', U), ('ff8', U), ('ffc', U), ('v100', '12x'), ('f10c', U), ('f110', U), ('b258', B), ('v134', '8x'), ('f13c', U), ('f140', U), ('a144', '12x'), ('a150', '12x'), ('a15c', '32x'), ('a114', '32x'), ('a17c', '48x'), ('a1ac', '48x'), ('col238', '4C'), ('a1dc', '16x'), ('a1ec', '16x'), ('a1fc', '16x'), ('a20c', '16x'), ('mesh21c', 'R'), ('mat7c', 'R'), ('b259', B), ('b25a', B), ('f80', U), ('f84', U), ('f98', U), ('f9c', U), ('fa4a', U), ('fa4b', U), ('wind228', 'W'), ('ac', '32x'), ('fcc', U), ('fd0', U), ('arr248', 'A')]
AC4_FIELDS = [('flag0', B), ('sel3a', U), ('flag4', B), ('flag5', B), ('flag6', B), ('sel2a', U)] + [(f'flag{i}', B) for i in range(9, 17)] + [('sel3b', U), ('sel2b', U), ('sel2c', U), ('sel2d', U), ('flag27', B), ('sel2e', U), ('flag30', B), ('sel2f', U)] + [(f'g{i}', B) for i in range(1, 7)] + [('gsel3', U), ('g10', B), ('g11', B), ('gsel2', U), ('g14', B), ('g15', B), ('g16', B), ('gsel4a', U), ('gsel4b', U), ('b310', B), ('v50', '16x'), ('b311', B), ('b312', B), ('f84', U), ('f88', U), ('f8c', U), ('b313', B), ('b316', B), ('f98', U), ('f9c', U), ('b314', B), ('fa4', U), ('b315', B), ('fac', U), ('fb0', U), ('fb4', U), ('f94', U), ('fbc', U), ('fc0', U), ('fc4', U), ('fc8', U), ('v60', '16x'), ('m10', '64x'), ('a108', '12x'), ('f114', U), ('f118', U), ('f11c', U), ('f120', U), ('v124', '12x'), ('f130', U), ('f134', U), ('b317', B), ('v13c', '8x'), ('f144', U), ('f148', U), ('f14c', U), ('f150', U), ('a154', '28x'), ('a170', '28x'), ('a18c', '64x'), ('a1cc', '64x'), ('col2d4', '8C'), ('b31a', B), ('b31b', B), ('f20c', U), ('f210', U), ('a214', '32x'), ('a234', '32x'), ('a254', '32x'), ('a274', '32x'), ('a294', '32x'), ('a2b4', '32x'), ('mat78', 'R'), ('b318', B), ('b319', B), ('f7c', U), ('f80', U), ('ref90', '5x'), ('fa0', U), ('fa8', U), ('fb8a', U), ('f148b', U), ('f144b', U), ('wind2f4', 'W'), ('acc', '56x'), ('f104', U), ('f138', U), ('arr308', 'A')]
EMITTER = 2704677564
ICOLOR = 4162874103
AC3_TRAILER = AC4_TRAILER = 42

class Note(Exception):
    pass

def parse(b, at, fields):
    out = []
    for name, k in fields:
        s = at
        if k in ('b',):
            at += 1
        elif k == 'I':
            at += 4
        elif k.endswith('x'):
            at += int(k[:-1])
        elif k.endswith('C'):
            for _ in range(int(k[:-1] or 1)):
                if struct.unpack_from('<I', b, at + 4)[0] != ICOLOR:
                    raise Note(f'{name}: no IColor at {at}')
                at += 12
        elif k == 'R':
            if b[at] == 0:
                raise Note(f'{name}: an embedded object at {at}')
            at += 6
        elif k == 'W':
            at += 15
        elif k == 'A':
            n = struct.unpack_from('<I', b, at)[0]
            at += 4
            for _ in range(n):
                at += 8 + 11
        out.append((name, s, at))
        if at > len(b):
            raise Note(f'{name}: past the end')
    return (out, at)

def system(b, game):
    head = 15 if game == 'AC3' else 17
    fields = AC3_FIELDS if game == 'AC3' else AC4_FIELDS
    n = struct.unpack_from('<I', b, head)[0]
    at, ems = (head + 4, [])
    for _ in range(n):
        if struct.unpack_from('<I', b, at + 5)[0] != EMITTER:
            raise Note(f'no ParticleEmitter at {at}')
        f, end = parse(b, at + 9, fields)
        ems.append((at, f, end))
        at = end
    if len(b) - at != 42:
        raise Note(f'{len(b) - at} trailing bytes, not 42')
    return (ems, at)
KEYS = (0, 2, 5, 7)
GROUPS = ((0, 1), (2, 3, 4), (5, 6))
COPY = {'flag0': 'flag0', 'type3': 'sel3a', 'mode2': 'sel2a', 'flag8': 'flag10', 'flag9': 'flag12', 'flag10': 'flag14', 'flag11': 'flag16', 'sel3a': 'sel3b', 'sel2a': 'sel2b', 'sel2b': 'sel2c', 'sel2c': 'sel2e', 'sel2d': 'sel2f', 'flag24': 'g1', 'flag25': 'g2', 'flag26': 'g3', 'flag27': 'g4', 'flag28': 'g5', 'flag29': 'g6', 'sel3b': 'gsel3', 'flag35': 'g10', 'flag36': 'g11', 'flag37': 'gsel2', 'flag38': 'g14', 'flag39': 'g15', 'b252': 'b310', 'v10': 'v50', 'b254': 'b313', 'fa0': 'f98', 'b255': 'b316', 'fa8': 'f9c', 'b256': 'b314', 'f94': 'fa4', 'b257': 'b315', 'fd4': 'fac', 'fd8': 'fb0', 'fe0': 'fc0', 'v60': 'v60', 'm20': 'm10', 'fe4': 'a108', 'ff0': 'f114', 'ff4': 'f118', 'ff8': 'f11c', 'ffc': 'f120', 'v100': 'v124', 'f10c': 'f130', 'f110': 'f134', 'b258': 'b317', 'v134': 'v13c', 'f13c': 'f144b', 'f140': 'f148b', 'mat7c': 'mat78', 'b259': 'b318', 'b25a': 'b319', 'f80': 'f7c', 'f84': 'f80', 'wind228': 'wind2f4', 'fcc': 'f104', 'fd0': 'f138', 'arr248': 'arr308'}
KEYED = {'a15c': ('a18c', 8), 'a114': ('a1cc', 8), 'col238': ('col2d4', 12), 'a1dc': ('a214', 4), 'a1ec': ('a234', 4), 'a1fc': ('a254', 4), 'a20c': ('a274', 4)}
F1 = struct.pack('<f', 1.0)

def _col(rgba):
    return bytes(4) + struct.pack('<I', ICOLOR) + bytes.fromhex(rgba)
DEFAULT = {'flag1': bytes(4), 'flag7': bytes(1), 'flag21': bytes(1), 'sel2e': bytes(4), 'u78': F1, 'w250c': bytes((1,)), 'b253': bytes(1), 'v88': bytes(8) + F1, 'col224': _col('ffffffff'), 'col220': _col('000000ff'), 'fdc': struct.pack('<f', 200.0), 'a17c': F1 * 12, 'a1ac': F1 * 12, 'mesh21c': bytes((3,)) + bytes(5), 'f98': bytes(4), 'f9c': bytes(4), 'fa4a': bytes(4), 'fa4b': bytes(4)}

def emitter(d4):
    out = []
    life = struct.unpack('<f', d4['f150'])[0]
    for k3, _kind in AC3_FIELDS:
        if k3 in COPY:
            v = d4[COPY[k3]]
        elif k3 in KEYED:
            k4, w = KEYED[k3]
            v = b''.join((d4[k4][i * w:(i + 1) * w] for i in KEYS))
        elif k3 in ('a144', 'a150'):
            seg = struct.unpack('<7f', d4['a154' if k3 == 'a144' else 'a170'])
            v = struct.pack('<3f', *(sum((seg[i] for i in g)) * life for g in GROUPS))
        elif k3 in ('w250a', 'w250b'):
            c = struct.unpack('<8f', d4['a294'])
            on = any(c)
            if k3 == 'w250a':
                v = bytes((1 if on else 0,))
            else:
                v = bytes((max(0, min(255, round(c[0]))) if on else 128,))
        elif k3 == 'ac':
            v = d4['acc'][:32]
        else:
            v = DEFAULT[k3]
        out.append(v)
    return b''.join(out)

def convert(b):
    ems, t = system(b, 'AC4')
    out = [b[:11], b[13:21]]
    for s, f, _e in ems:
        out.append(b[s:s + 9])
        out.append(emitter({k: b[x:y] for k, x, y in f}))
    out.append(b[t:])
    body = b''.join(out)
    system(body, 'AC3')
    return body

def tint(b, colours):
    ems, _t = system(b, 'AC3')
    out = bytearray(b)
    for i, (_s, f, _e) in enumerate(ems):
        if i in colours:
            x = {k: (x_, y_) for k, x_, y_ in f}['col238'][0]
            for j in range(4):
                out[x + 12 * j + 8:x + 12 * j + 11] = bytes(colours[i])
    return bytes(out)

def main():
    import collections
    import pickle
    r = pickle.load(open('out/ac4/psys/psys.pkl', 'rb'))
    for u, (n, b, _rec) in r['ac3'].items():
        system(b, 'AC3')
    for u, (n, b, _rec) in r['tampa'].items():
        convert(b)
    fields = collections.Counter()
    for u, (n, a, b, m) in r['pairs'].items():
        c = convert(b)
        if len(c) != len(a):
            raise SystemExit(f'{n}: {len(c)} bytes, AC3 {len(a)}')
        e3, _t = system(a, 'AC3')
        for _s, f, _e in e3:
            for k, x, y in f:
                if a[x:y] != c[x:y]:
                    fields[k] += 1
    print(f"AC3's {len(r['ac3'])} systems read; {len(r['tampa'])} Tampa Bay systems convert; {len(r['pairs'])} shared pairs the same length; fields differing (emitters of 128): {fields.most_common()}")
if __name__ == '__main__':
    main()
