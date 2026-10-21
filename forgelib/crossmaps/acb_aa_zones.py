"""forgelib/crossmaps/acb_aa_zones.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (os.path.join(ROOT, 'maps', 'port'), os.path.join(ROOT, 'maps'), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import aa_zones as AZ
_original = AZ.oob_outline

def hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = ([], [])
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]

def endpoints(body):
    pat = struct.pack('<I', AZ.IDS['OutOfBoundsSection'])
    out, k = ([], body.find(pat))
    while k >= 0:
        v = struct.unpack_from('<16f', body, k + 4)
        c, nrm, w = ((v[0], v[1]), (v[8], v[9]), v[12])
        t = (-nrm[1], nrm[0])
        out += [(round(c[0] - t[0] * w / 2, 3), round(c[1] - t[1] * w / 2, 3)), (round(c[0] + t[0] * w / 2, 3), round(c[1] + t[1] * w / 2, 3))]
        k = body.find(pat, k + 4)
    return out
PLAY_BOX = []

def oob_outline(body):
    if PLAY_BOX:
        x0, y0, x1, y1 = PLAY_BOX
        print(f"play area: the box ({x0:g}, {y0:g}) - ({x1:g}, {y1:g}) in place of the OOB entity's outline (--play-box)")
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    try:
        return _original(body)
    except SystemExit as e:
        h = hull(endpoints(body))
        print(f'OOB outline: {e} - the convex hull of its {len(endpoints(body))} section ends stands in ({len(h)} corners, {abs(AZ.area(h)):.0f} m2)')
        return h
AZ.oob_outline = oob_outline

def main(argv):
    argv = list(argv)
    PLAY_BOX[:] = []
    if '--play-box' in argv:
        i = argv.index('--play-box')
        PLAY_BOX[:] = [float(v) for v in argv[i + 1].split(',')]
        argv = argv[:i] + argv[i + 2:]
    AZ.main(argv)
if __name__ == '__main__':
    main(sys.argv[1:])
