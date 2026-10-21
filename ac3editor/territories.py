"""Artifact Assault territories edited as the game's designers think of them: the two teams' territories are one
area cut in two by a DIVIDER. Move the divider (or the area's outer edge) and both territories are rebuilt - each
cut into convex pieces, since the game tests a territory piece by piece (a point is in a zone if it is on the inner
side of every section of one of its shapes, AC3MP.exe FUN_0072e452), the cuts between pieces carrying no wall and no
FX.

A pair: the two gameplay zones of one game-mode block (the same records) that hold the block's artifacts
(FlagComponent entities) - AC3's own AA blocks, ported maps' blocks and Switched layout blocks alike. Each team keeps
the half holding its artifact; an artifact, scoring zone (CTFScoreZoneComponent) or team start (`..Initial..Team..`)
left on the other side refuses the edit.
"""
import math
import struct

import numpy as np

from . import boundary as B
from . import mapdata as M
from .newmodel import _comp

TOL = 0.1          # an edge is backed by another when its middle lies this close to it, facing the other way (the
                   # maps' cuts and dividers meet within 1 mm; 0.3 caught Souk's 1 m-wide sliver piece backing itself)


# --- plain 2D geometry (tuples) -------------------------------------------------------------------------------

def area(poly):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1])) / 2


def ccw(poly):
    return poly if area(poly) > 0 else poly[::-1]


def side(a, b, p):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def seg_dist(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1e-12)))
    return math.dist(p[:2], (a[0] + t * dx, a[1] + t * dy))


def simple(poly):
    def cross(p1, p2, q1, q2):
        return ((side(q1, q2, p1) > 0) != (side(q1, q2, p2) > 0)) and ((side(p1, p2, q1) > 0) != (side(p1, p2, q2) > 0))
    n = len(poly)
    return not any(cross(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n])
                   for i in range(n) for j in range(i + 2, n) if not (i == 0 and j == n - 1))


def drop_straight(pts, closed=True, tol=0.01):
    """Corners that don't turn (within `tol` m of the line through their neighbours) out."""
    pts = [p for i, p in enumerate(pts) if i == 0 or math.dist(p, pts[i - 1]) > 1e-6]
    if closed:
        while len(pts) > 1 and math.dist(pts[0], pts[-1]) <= 1e-6:
            pts.pop()
    out = []
    n = len(pts)
    for i, p in enumerate(pts):
        if not closed and i in (0, n - 1):
            out.append(p)
            continue
        a, b = pts[i - 1], pts[(i + 1) % n]
        if abs(side(a, b, p)) / max(math.dist(a, b), 1e-9) > tol:
            out.append(p)
    return out


def _cross(p, q, a, b):
    """Segment p-q against a-b: -> (t along p-q, u along a-b, point) or None."""
    r = (q[0] - p[0], q[1] - p[1])
    s = (b[0] - a[0], b[1] - a[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    t = ((a[0] - p[0]) * s[1] - (a[1] - p[1]) * s[0]) / den
    u = ((a[0] - p[0]) * r[1] - (a[1] - p[1]) * r[0]) / den
    if 0 <= t <= 1 and 0 <= u < 1:
        return t, u, (p[0] + t * r[0], p[1] + t * r[1])
    return None


def split_polyline(outline, line):
    """The CCW polygon `outline` cut along the polyline `line` (its end segments extended 5 km: the ends may lie
    anywhere outside) -> (left half, right half, the line inside). The line must cross the outline exactly twice."""
    def ext(a, b):
        d = math.dist(a, b)
        return (a[0] + (a[0] - b[0]) / d * 5000, a[1] + (a[1] - b[1]) / d * 5000)
    line = [tuple(p) for p in line]
    if len(line) < 2 or math.dist(line[0], line[1]) < 1e-6 or math.dist(line[-1], line[-2]) < 1e-6:
        raise ValueError("the divider needs at least two corners")
    path = [ext(line[0], line[1])] + line + [ext(line[-1], line[-2])]
    n = len(outline)
    hits = []
    for i in range(len(path) - 1):
        for j in range(n):
            c = _cross(path[i], path[i + 1], outline[j], outline[(j + 1) % n])
            if c:
                hits.append((i, c[0], j, c[1], c[2]))
    hits.sort()
    # a crossing exactly at a bend is found on both segments: once only
    hits = [h for i, h in enumerate(hits) if i == 0 or math.dist(h[4], hits[i - 1][4]) > 1e-3]
    if len(hits) != 2:
        raise ValueError(f"the divider must cross the territories' outer edge exactly twice - it crosses "
                         f"{len(hits)} times")
    (i1, _t1, j1, u1, p1), (i2, _t2, j2, u2, p2) = hits
    inner = [p1] + path[i1 + 1:i2 + 1] + [p2]

    def walk(ja, ua, jb, ub):
        out, j = [], ja
        if ja == jb and ub > ua:
            return out
        while True:
            j = (j + 1) % n
            out.append(outline[j])
            if j == jb:
                return out

    def clean(poly, closed=True):
        out = []
        for q in poly:
            if not out or math.dist(q, out[-1]) > 1e-6:
                out.append(q)
        while closed and len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-6:
            out.pop()
        return out
    inner = clean(inner, closed=False)
    left = clean(inner + walk(j2, u2, j1, u1))
    right = clean(inner[::-1] + walk(j1, u1, j2, u2))
    return ccw(left), ccw(right), inner


def triangulate(poly):
    """Ear clipping of a simple CCW polygon -> [(i, j, k)]."""
    idx = list(range(len(poly)))
    tris = []
    while len(idx) > 3:
        for n in range(len(idx)):
            i, j, k = idx[n - 1], idx[n], idx[(n + 1) % len(idx)]
            a, b, c = poly[i], poly[j], poly[k]
            if side(a, b, c) <= 1e-9:
                continue
            if any(side(a, b, poly[m]) > 0 and side(b, c, poly[m]) > 0 and side(c, a, poly[m]) > 0
                   for m in idx if m not in (i, j, k)):
                continue
            tris.append((i, j, k))
            idx.pop(n)
            break
        else:
            raise ValueError("a territory isn't a simple polygon (does the divider cross itself?)")
    tris.append(tuple(idx))
    return tris


def convex_pieces(poly):
    """A simple CCW polygon -> convex pieces (vertex index lists): its triangles merged across diagonals while the
    result stays convex (Hertel-Mehlhorn)."""
    pieces = [list(t) for t in triangulate(poly)]

    def is_convex(ids):
        pts = [poly[i] for i in ids]
        return all(side(pts[m - 1], pts[m], pts[(m + 1) % len(pts)]) >= -1e-9 for m in range(len(pts)))

    merged = True
    while merged:
        merged = False
        for x in range(len(pieces)):
            for y in range(x + 1, len(pieces)):
                A, Bp = pieces[x], pieces[y]
                for i in range(len(A)):
                    u, v = A[i], A[(i + 1) % len(A)]
                    if v in Bp and Bp[(Bp.index(v) + 1) % len(Bp)] == u:
                        ia, ib = (i + 1) % len(A), Bp.index(u)
                        cand = (A[ia:] + A[:ia])[:-1] + (Bp[ib:] + Bp[:ib])[:-1]
                        cand = [c for n_, c in enumerate(cand) if c != cand[n_ - 1]]
                        if is_convex(cand):
                            pieces[x] = cand
                            pieces.pop(y)
                            merged = True
                            break
                if merged:
                    break
            if merged:
                break
    return pieces


def member(polys, p):
    """The game's test: p inside one of the zone's convex shapes (inner side of every edge)."""
    p = np.asarray(p, np.float64)[:2]
    return any(all(np.dot(p - a, n) >= -1e-6 for a, _b, n in B.walls(np.asarray(q, np.float64))) for q in polys)


# --- a map's pairs ----------------------------------------------------------------------------------------------

def _segments(polys):
    """A zone's shapes -> [(a, b, inward normal, shape, wall index)]."""
    out = []
    for k, p in enumerate(polys):
        for w, (a, b, n) in enumerate(B.walls(np.asarray(p, np.float64))):
            out.append((tuple(a), tuple(b), tuple(n), k, w))
    return out


def _backed(s, others, own=False):
    """Segment s backed by one of `others`; own: others are s's zone's own segments - a piece never backs itself."""
    mid = ((s[0][0] + s[1][0]) / 2, (s[0][1] + s[1][1]) / 2)
    return any(t[2][0] * s[2][0] + t[2][1] * s[2][1] < -0.9 and seg_dist(t[0], t[1], mid) < TOL
               for t in others if not (own and t[3] == s[3]))


def _chain(segs):
    """Segments [(a, b)] -> one polyline (list of points), or None if they don't form exactly one chain."""
    if not segs:
        return None
    left = list(segs)
    line = list(left.pop(0))
    while left:
        for i, (a, b) in enumerate(left):
            if math.dist(line[-1], a) < 0.5:
                line.append(b)
            elif math.dist(line[-1], b) < 0.5:
                line.append(a)
            elif math.dist(line[0], b) < 0.5:
                line.insert(0, a)
            elif math.dist(line[0], a) < 0.5:
                line.insert(0, b)
            else:
                continue
            left.pop(i)
            break
        else:
            return None
    return line


def _loop(segs):
    line = _chain(segs)
    if line is None or math.dist(line[0], line[-1]) > 0.5:
        return None
    return line[:-1]


def pairs(archive, areas=None):
    """-> [dict(key, uids (a, b), name, records, outline (CCW corners), divider (polyline), anchors, starts, quiet,
    quiet_outer, zones)] for every block whose two territories touch along one divider. `anchors`: [(name, (x, y),
    uid of the zone holding it)] - the artifacts and scoring zones; `starts` the same for the team starts; `quiet`:
    {uid: none of its divider edges had FX (the other territory draws the line)}; `quiet_outer`: outer edges the map
    gives a wall but no FX (kept FX-free wherever the new territories still have them)."""
    S = M.schema()
    C = M.Codec(S)
    areas = areas if areas is not None else B.areas(archive)
    groups = {}
    for uid, a in areas.items():
        if a["kind"] == "zone":
            groups.setdefault(tuple(a["records"]), []).append(uid)
    groups = {r: u for r, u in groups.items() if len(u) == 2}
    if not groups:
        return []
    recs = {r for g in groups for r in g}
    things = {}                                       # record -> [(name, (x, y), kind)]
    for rec, name, t, body, _f in M.iter_objects_ex(archive):
        if rec not in recs or S.name_of(t) != "Entity" or body[:2] != M.UID_MARK:
            continue
        pos = struct.unpack_from("<2f", body, 58) if len(body) >= 70 else None
        if "Initial" in name and "Team" in name and "NotInitial" not in name and pos:
            things.setdefault(rec, []).append((name, pos, "start"))
            continue
        try:
            o = C.decode(body).obj
        except M.DecodeError:
            continue
        for cn, kind in (("FlagComponent", "flag"), ("CTFScoreZoneComponent", "score")):
            if _comp(S, o, cn) is not None:
                m = struct.unpack("<16f", o.fields["GlobalMatrix"])
                things.setdefault(rec, []).append((name, (m[12], m[13]), kind))
    out = []
    for records, (ua, ub) in groups.items():
        found = [x for r in records for x in things.get(r, [])]
        if not any(k == "flag" for _n, _p, k in found):
            continue                                   # two zones but no artifacts: not an AA pair
        za, zb = areas[ua], areas[ub]
        sa, sb = _segments(za["polygons"]), _segments(zb["polygons"])
        own_a = [s for s in sa if not _backed(s, sa, own=True)]  # each zone's outline (its cuts out)
        own_b = [s for s in sb if not _backed(s, sb, own=True)]
        outer = [s for s in own_a if not _backed(s, own_b)] + [s for s in own_b if not _backed(s, own_a)]
        div = [s for s in own_a if _backed(s, own_b)]
        loop = _loop([(s[0], s[1]) for s in outer])
        line = _chain([(s[0], s[1]) for s in div])
        if loop is None or line is None:
            continue                                   # territories apart, or touching in more than one place
        outline = ccw(drop_straight(loop))
        line = drop_straight(line, closed=False)
        quiet, quiet_outer = {}, []
        for uid, own, other in ((ua, own_a, own_b), (ub, own_b, own_a)):
            q = areas[uid]["quiet"]
            on = [s for s in own if _backed(s, other)]
            quiet[uid] = bool(on) and all(s[4] in q[s[3]] for s in on)
            quiet_outer += [(s[0], s[1]) for s in own if s not in on and s[4] in q[s[3]]]
        anchors, starts = [], []
        for name, p, kind in found:
            holder = next((u for u in (ua, ub) if member(areas[u]["polygons"], p)), None)
            if holder is not None:
                (starts if kind == "start" else anchors).append((name, p, holder))
        block = records[0].replace("DataBlock_ACFE_", "").replace("DataBlock_", "")
        out.append(dict(key=f"aa:{ua.hex()}:{ub.hex()}", uids=(ua, ub), name=block, records=list(records),
                        outline=outline, divider=line, anchors=anchors, starts=starts, quiet=quiet,
                        quiet_outer=quiet_outer,
                        zones={ua: za, ub: zb}))
    return out


def redivide(pair, outline=None, line=None):
    """A pair's new outer edge and / or divider (None: the map's) -> ({zone uid: convex pieces (arrays)}, {zone uid:
    per piece, the wall indices left without FX}, report lines). Raises ValueError with what to fix."""
    outline = ccw([tuple(p) for p in (pair["outline"] if outline is None else outline)])
    line = pair["divider"] if line is None else line
    if not simple(outline):
        raise ValueError("the territories' outer edge crosses itself")
    left, right, inner = split_polyline(outline, line)
    halves = (left, right)
    ua, ub = pair["uids"]
    side_of = {}
    for name, p, holder in pair["anchors"]:
        inn = [k for k, h in enumerate(halves) if B.inside(p, h)]
        if len(inn) != 1:
            raise ValueError(f"{name} would lie outside both territories - keep it inside the outer edge")
        if side_of.setdefault(holder, inn[0]) != inn[0]:
            raise ValueError(f"{name} would end up on the other side of the divider from its team's artifact")
    if len(side_of) != 2 or side_of[ua] == side_of[ub]:
        raise ValueError("both teams' artifacts would be on the same side of the divider")
    polys, quiet, report = {}, {}, []
    for uid in (ua, ub):
        half = halves[side_of[uid]]
        if not simple(half):
            raise ValueError("a territory would cross itself - does the divider cross itself?")
        pieces = [np.array([half[i] for i in pc], np.float64) for pc in convex_pieces(half)]
        polys[uid] = pieces
        q = []
        on = lambda c, d, a, b: seg_dist(c, d, a) < 0.05 and seg_dist(c, d, b) < 0.05  # noqa: E731
        for pc in pieces:
            # no FX where the map had none: its FX-free outer edges, and the divider if this territory left it to
            # the other
            q.append({w for w, (a, b, _n) in enumerate(B.walls(pc))
                      if any(on(c, d, a, b) for c, d in pair["quiet_outer"]) or
                      (pair["quiet"].get(uid) and any(on(inner[i], inner[i + 1], a, b) for i in range(len(inner) - 1)))})
        quiet[uid] = q
        report.append(f"{pair['zones'][uid]['name']}: {len(half)} corners, {abs(area(half)):.0f} m2, "
                      f"{len(pieces)} convex piece(s)")
    for name, p, holder in pair["starts"]:
        if not member(polys[holder], p):
            raise ValueError(f"team start {name} would end up outside its team's territory")
    return polys, quiet, report

