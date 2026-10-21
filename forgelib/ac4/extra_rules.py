"""forgelib/ac4/extra_rules.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import pickle
import struct
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
AC4_CAMERA_SUB = struct.pack('<I', 3437118763)
CONDITIONS = (struct.pack('<I', 959367171), struct.pack('<I', 1928140310), struct.pack('<I', 547610257), struct.pack('<I', 3768459427))
CAMERA_CLASSES = ('ICameraRule', 'PathAnimationCamera', 'FixedCamera2', 'TemporalCameraTransition')

class Note(Exception):
    pass

def collision_material(body):
    if len(body) != 46:
        raise Note(f'CollisionMaterial {len(body)} B')
    v = body[38]
    if 7 <= v <= 18:
        raise Note(f'CollisionMaterial type byte {v}: inside the range the pairs never show')
    if v >= 19:
        return body[:38] + bytes([v - 1]) + body[39:]
    return body

def _strip_camera_subs(body):
    out = bytearray()
    i = 0
    n = 0
    while i < len(body):
        if body[i:i + 5] == bytes(5) and body[i + 9:i + 13] == AC4_CAMERA_SUB and (i + 17 <= len(body)):
            i += 17
            n += 1
            continue
        out.append(body[i])
        i += 1
    return (bytes(out), n)

def camera(body):
    out, n = _strip_camera_subs(body)
    for cond in CONDITIONS:
        k = out.find(cond)
        while k >= 0:
            if out[k + 4:k + 8] == b'\x01\x00\x00\x00' and out[k + 8:k + 16] == bytes(8):
                out = out[:k + 8] + out[k + 16:]
                n += 1
            k = out.find(cond, k + 4)
    return out
RULES = {'CollisionMaterial': collision_material}
for _c in CAMERA_CLASSES:
    RULES[_c] = camera

def convert(cls, body):
    rule = RULES.get(cls)
    if rule is None:
        return (body, None)
    try:
        return (rule(body), None)
    except Note as e:
        return (body, str(e))

def main(argv):
    lines = []
    keep = lambda o: common.cls(o['type'], 'AC4') in RULES
    tot = {}
    for m, (a3, a4) in common.SHARED_MAPS.items():
        x, _ = common.load(common.ac3_archive(a3), keep=keep)
        y, _ = common.load(common.ac4_archive(a4), keep=keep)
        lines.append(f'== {m}')
        per = {}
        for k in sorted(set(x) & set(y), key=str):
            c = common.cls(x[k][1]['type'], 'AC4')
            bx, by = (x[k][1]['body'], y[k][1]['body'])
            ours, note = convert(c, by)
            if ours == bx:
                v = 'exact'
            elif note:
                v = 'note'
            elif ours == bx:
                v = 'exact'
            elif len(ours) == len(bx):
                v = 'samelen'
            else:
                v = 'MISMATCH'
            per.setdefault(c, {}).setdefault(v, []).append((x[k][1]['name'], note, len(bx), len(ours)))
        for c, d in sorted(per.items()):
            lines.append(f'   {c:26} ' + '  '.join((f'{v} {len(lst)}' for v, lst in sorted(d.items()))))
            for v in ('MISMATCH', 'note'):
                for name, note, l3, lo in d.get(v, [])[:4]:
                    lines.append(f"      {v}: {name} AC3 {l3} ours {lo} {note or ''}")
            for v, lst in d.items():
                tot.setdefault(c, {}).setdefault(v, 0)
                tot[c][v] += len(lst)
    lines.append('== all maps: ' + '; '.join((f'{c}: ' + ', '.join((f'{v} {n}' for v, n in sorted(d.items()))) for c, d in sorted(tot.items()))))
    common.report('extra_rules_gate.txt', lines)
    print('\n'.join(lines))
if __name__ == '__main__':
    main(sys.argv[1:])
