"""Climbable outside: handhold rows and ledges on a building's OUTER walls only, on an invisible layer of its own.

A building can be almost all FuzzyZone collision (Fort Wolcott's barracks: 313 of its 317 collision triangles), so
nothing on it is climbable. This option makes its outside climbable while its interior stays FuzzyZone. The building's
own collision is untouched (FuzzyZone stays FuzzyZone: no free climbing or wall runs on the faces); what's added is one
invisible object per building:

* **Outer faces**: a wall face (|normal z| < 0.35) is outer when, at 3 of its 4 sample points, a horizontal ray along
  its normal from just in front of it leaves the building without hitting the building's own collision, and the
  point 1.5 m out has open sky above it (nothing of the building: an eave reaches less, while a table or wall seen
  through a door or window still has the room's ceiling over it). Interior walls face other walls or ceilings; an
  outer wall faces open air. Stock collision is wound outward, so the normal points out of the solid.
* **Handhold rows** up the outer faces (climb.rows, the multiplayer spacing: from 3.5 m, every 1.25 m, invisible beams
  with their ledges) and **ledges** along the outer walls' top creases (climb.ledges on the whole collision, kept where
  the wall face is an outer one), each as a beam too: the game only climbs to an edge with its own object's collision
  behind it.
* **The layer**: a hidden newmodel object (an invisible climbable route): its collision is the beams only
  (12 cm out from the wall, 15 cm deep, open at the back), on a climbable CollisionMaterial (Stone_Clean, else the
  template's - never FuzzyZone); its climb data the ledges along the beams' front top edges (generated).
"""
import numpy as np

from ac3editor import climb
from ac3editor.scenebuild import NOT_CLIMBABLE

WALL_NZ = 0.35
OUT_CLEAR = 1.5     # m out from an outer face: open sky above (eaves reach less; a room seen through a door doesn't)
DRAWN_GAP = 0.35    # m: a handhold only where the drawn wall is at most this far behind it (else the climber floats:
                    # low on the barracks' east / north sides its collision stands 1-2 m in front of the drawn wall)
BURIED = 0.15       # m: nor where the drawn wall stands this far in front of it (the hand would be inside the wall)
# The way up to a roof follows stock maps (measured on Boston Harbour's 134 eave ledges and Galata's 201): the
# collision under a climbable eave is flush with it (within 0.1 m, 3 m down in 90%), the last wall ledge 0.6-1.2 m below it; where a face steps OUT over
# the one below (a jutting upper storey / eave band: the barracks' 0.85 m from 8.2 m), the upper ledge is only ~0.6 m
# above the lower one (Galata: 191 step-outs of 0.9-1.5 m, up median 0.57, 90% 0.80; Boston 0.6-0.9 m: median 0.70).
ROOF_NAMES = ("Wood_Plank", "Wood_Solid", "Wood_Dirt", "Stone_Clean")   # Boston's walkable roofs: Wood_Plank 8,434 m2;
ROOF_LIFT = 0.03    # m: the walkable skin over a FuzzyZone roof (Fort Wolcott's roofs: 3,286 m2 FuzzyZone - never meant to
                    # be stood on; Boston's whole map has 7 m2 of FuzzyZone to stand on)
ROOF_CLEAR = 0.5    # m: a roof face stands at least this high over the floor under it (not a walkway; the barracks' attic
                    # ceiling is 1.3 m under its roof)
STEP_OUT = 0.3      # m: a face standing this far out over the one below it is a step out
STEP_UP = 0.6       # m: from the last ledge under a step out to the first one on the face over it
CHAIN = 0.7         # m: then ledges up that face this far apart to the top ledge (stock under-eave spacing 0.6-1.2)
UNDER_LIP = 0.25    # m: the last ledge under a step out this far under its bottom edge
LADDER = 1.2        # m: the way up a top ledge's face below, ledges this far apart (stock: next ledge 0.6-1.2 m below)
MIN_ABOVE = 2.0     # m: no row lower than this over what's in front of it (a shell can reach under the ground - the
                    # barracks' 3 m - and rows counted from its bottom would sit buried or at knee height)
CLIMBABLE_NAMES = ("Stone_Clean", "Wood_Solid", "Wood_Plank", "Stone_Dirt", "Default")


def _rays_hit(O, D, tris, max_t):
    """Per ray (origins O (m, 3), directions D (m, 3)): does it hit any triangle (n, 3, 3) within (1e-4, max_t)?"""
    hit = np.zeros(len(O), bool)
    if not len(tris):
        return hit
    v0, e1, e2 = tris[:, 0], tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    for i in range(len(O)):
        o, d = O[i], D[i]
        p = np.cross(d, e2)
        det = (e1 * p).sum(1)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = o - v0
        u = (s * p).sum(1) * inv
        q = np.cross(s, e1)
        v = (q @ d) * inv
        t = (e2 * q).sum(1) * inv
        hit[i] = bool((ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-4) & (t < max_t)).any())
    return hit


def covered(P, tris, reach=60.0):
    """Which points (m, 3) have the building's collision above them (an upward ray hits it)?"""
    P = np.asarray(P, np.float64)
    return _rays_hit(P, np.tile([0.0, 0.0, 1.0], (len(P), 1)), tris, reach)


def outer_faces(V, T, offset=0.08, need=3, clear=OUT_CLEAR):
    """-> (wall mask, outer mask) over the triangles of a building's collision (world space). A sample point is in
    the open when a horizontal ray from just in front of the face along its normal leaves the building and the point
    `clear` m out has nothing of the building above it (not a room seen through a door or window)."""
    V, T = np.asarray(V, np.float64), np.asarray(T, np.int64)
    tri = V[T]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = np.linalg.norm(n, axis=1) / 2
    n = n / np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
    wall = (np.abs(n[:, 2]) < WALL_NZ) & (area > 0.02)
    outer = np.zeros(len(T), bool)
    idx = np.nonzero(wall)[0]
    if not len(idx):
        return wall, outer
    lo, hi = V.min(0), V.max(0)
    reach = float(np.linalg.norm(hi - lo)) + 2.0
    # 4 points per face: its centre and three towards its corners
    W = np.array([[1 / 3, 1 / 3, 1 / 3], [0.6, 0.2, 0.2], [0.2, 0.6, 0.2], [0.2, 0.2, 0.6]])
    P = np.einsum("kj,fjc->fkc", W, tri[idx])                    # (faces, 4, 3)
    h = n[idx].copy()
    h[:, 2] = 0
    h /= np.maximum(np.linalg.norm(h, axis=1), 1e-12)[:, None]
    O = (P + h[:, None, :] * offset).reshape(-1, 3)
    D = np.repeat(h, 4, axis=0)
    free = ~_rays_hit(O, D, tri, reach)
    free &= ~covered((P + h[:, None, :] * clear).reshape(-1, 3) + [0, 0, 0.05], tri, reach)
    outer[idx] = free.reshape(-1, 4).sum(1) >= need
    return wall, outer


def sides(n):
    """Horizontal normals -> 'east' / 'north' / 'west' / 'south' (world axes: +x east, +y north)."""
    a = np.degrees(np.arctan2(n[:, 1], n[:, 0])) % 360
    return np.array(["east", "north", "west", "south"])[(((a + 45) % 360) // 90).astype(int)]


def _floor_under(P, tris):
    """Per point: the height of the highest triangle under it (nan: none)."""
    out = np.full(len(P), np.nan)
    if not len(tris):
        return out
    v0, e1, e2 = tris[:, 0], tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    d = np.array([0.0, 0.0, -1.0])
    p = np.cross(d, e2)
    det = (e1 * p).sum(1)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    for i, o in enumerate(P):
        s = o - v0
        u = (s * p).sum(1) * inv
        q = np.cross(s, e1)
        v = (q @ d) * inv
        t = (e2 * q).sum(1) * inv
        h = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-4)
        if h.any():
            out[i] = o[2] - t[h].min()
    return out


def _first_hits(O, D, tris, max_t=5.0):
    """Per ray: distance to the first triangle hit (nan: none within max_t)."""
    out = np.full(len(O), np.nan)
    if not len(tris):
        return out
    v0, e1, e2 = tris[:, 0], tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    for i in range(len(O)):
        o, d = O[i], D[i]
        p = np.cross(d, e2)
        det = (e1 * p).sum(1)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = o - v0
        u = (s * p).sum(1) * inv
        q = np.cross(s, e1)
        v = (q @ d) * inv
        t = (e2 * q).sum(1) * inv
        h = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-4) & (t < max_t)
        if h.any():
            out[i] = t[h].min()
    return out


def _hit(o, d, tris, max_t=5.0):
    """One ray: (distance, triangle index) of the first triangle hit, or (nan, -1)."""
    v0, e1, e2 = tris[:, 0], tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    p = np.cross(d, e2)
    det = (e1 * p).sum(1)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = o - v0
    u = (s * p).sum(1) * inv
    q = np.cross(s, e1)
    v = (q @ d) * inv
    t = (e2 * q).sum(1) * inv
    h = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-4) & (t < max_t)
    if not h.any():
        return np.nan, -1
    k = int(np.argmin(np.where(h, t, np.inf)))
    return float(t[k]), k


def ways_up(tops, tri, n, holds=(), floor_of=None, max_deep=20.0):
    """A way up to each top ledge (a roof's eave), as stock maps build it (Boston / Galata). Scanning down the face
    under the ledge:
    * a step out (the face below standing STEP_OUT m or more further back - the barracks' 0.85 m eave band): a ledge on
      the face below UNDER_LIP under the step's bottom edge, one on the face over it STEP_UP above that, then every
      CHAIN m up to the top ledge;
    * then down the face below every LADDER m to MIN_ABOVE over the floor in front (the rows, cut to the drawn walls,
      don't line up under every eave piece: without this only 1 of the barracks' 10 eave pieces is reachable),
      stopping where the face steps again or ends.
    Each piece is left out where `holds` already covers 80% of it within 0.35 m. floor_of(holds) -> floor heights.
    -> holds [(a, b, up, wall)]."""
    out = []
    up = np.array([0.0, 0.0, 1.0])
    have = [(np.asarray(a, np.float64), np.asarray(b, np.float64), np.asarray(w, np.float64)) for a, b, _u, w in holds]

    def covered_by_have(pa, pb, w):
        """-> the height of the holds covering 80% of pa-pb within 0.35 m (the lowest of them), or None."""
        ab = pb - pa
        L = max(np.linalg.norm(ab), 1e-9)
        cov = []
        for a, b, w2 in have:
            if np.dot(w, w2) < 0.9 or abs(((a + b) / 2 - (pa + pb) / 2) @ up) > 0.35 or abs(((a + b) / 2 - pa) @ w) > 0.2:
                continue
            ta, tb = sorted([(a - pa) @ ab / L, (b - pa) @ ab / L])
            lo, hi = max(ta, 0.0), min(tb, L)
            if hi > lo:
                cov.append((lo, hi, min(a[2], b[2])))
        cov.sort()
        tot, end = 0.0, 0.0
        for lo, hi, _z in cov:
            lo = max(lo, end)
            if hi > lo:
                tot += hi - lo
                end = hi
        return min(c[2] for c in cov) if tot >= 0.8 * L else None

    for a, b, _u, w in tops:
        a, b, w = np.asarray(a, np.float64), np.asarray(b, np.float64), np.asarray(w, np.float64)
        m = (a + b) / 2
        z_top = m[2]
        floor = floor_of([(a, b, up, w)])[0] if floor_of is not None else np.nan
        deep = min(max_deep, z_top - floor if not np.isnan(floor) else max_deep)
        prof = []
        for d in np.arange(0.05, deep + 1e-6, 0.05):
            t, k = _hit(m + w * 1.0 - up * d, -w, tri, 4.0)
            ok = k >= 0 and abs(n[k][2]) < WALL_NZ and np.dot(n[k][:2], w[:2]) > 0.7
            prof.append((d, 1.0 - t if ok else np.nan))    # (the face's place along w, from the ledge's line)
        if not prof:
            continue
        lip = next(((i, d, o) for i, (d, o) in enumerate(prof) if i and not np.isnan(o) and not np.isnan(prof[i - 1][1])
                    and prof[i - 1][1] - o >= STEP_OUT), None)
        chain = []
        if lip is not None:
            i, d_lip, o_low = lip
            o_up = prof[i - 1][1]                     # (the face over the step)
            z_low = z_top - d_lip + 0.05 - UNDER_LIP  # (under the step's bottom edge)
            chain.append((z_low, o_low))
            z = max(z_low + STEP_UP, z_top - d_lip + 0.05 + 0.2)
            while z < z_top - 0.45:
                chain.append((z, o_up))
                z += CHAIN
            z, o_prev, start = z_low - LADDER, o_low, i
        else:
            o0 = next((o for _d, o in prof if not np.isnan(o)), np.nan)
            if np.isnan(o0):
                continue
            z, o_prev, start = z_top - LADDER, o0, 0
        for zc, o in chain:
            pa, pb = a + w * o + up * (zc - z_top), b + w * o + up * (zc - z_top)
            if covered_by_have(pa, pb, w) is None:
                out.append((pa, pb, up, w))
        # down the face below, LADDER m under the last hold (a row there already: from that row)
        def solid(zz):                                # (the face's place at that height, or None)
            kk = int(round((z_top - zz) / 0.05)) - 1
            if kk < start or kk >= len(prof) or np.isnan(prof[kk][1]) or abs(prof[kk][1] - o_prev) > STEP_OUT:
                return None
            return prof[kk][1]
        z_last = z + LADDER
        while np.isnan(floor) or z - floor >= MIN_ABOVE:
            k = int(round((z_top - z) / 0.05)) - 1
            if k < start or k >= len(prof) or np.isnan(prof[k][1]) or abs(prof[k][1] - o_prev) > STEP_OUT:
                # the face has an opening here (a window): a hold between the last one and the hold below it
                al = np.cross(up, w)
                ta = sorted([a @ al, b @ al])
                below = [min(h[0][2], h[1][2]) for h in have if np.dot(h[2], w) > 0.9
                         and min(h[0][2], h[1][2]) < z_last - 0.3
                         and min(ta[1], max(h[0] @ al, h[1] @ al)) - max(ta[0], min(h[0] @ al, h[1] @ al)) >= 0.3]
                if below and z_last - max(below) > 1.25:
                    for dz in (1.0, 0.9, 0.8, 1.1, 0.7):
                        oo = solid(z_last - dz)
                        if oo is not None and z_last - dz - max(below) <= 1.25:
                            out.append((a + w * oo + up * (z_last - dz - z_top), b + w * oo + up * (z_last - dz - z_top), up, w))
                            break
                break
            o_prev = prof[k][1]
            pa, pb = a + w * o_prev + up * (z - z_top), b + w * o_prev + up * (z - z_top)
            zc = covered_by_have(pa, pb, w)
            if zc is None:
                out.append((pa, pb, up, w))
            else:
                z = zc
            z_last = z
            z -= LADDER
    return out


def on_drawn(holds, drawn, gap=DRAWN_GAP, step=0.4, min_len=0.5):
    """Holds [(a, b, up, wall)] cut to where something drawn lies within `gap` m of them - the wall behind, or the
    roof's own edge at an eave (sampled every `step` m, 5 cm under the ledge) - and the drawn wall doesn't stand over
    BURIED m in front of them (looking back from 1 m out: the hand would be inside it). -> (holds, metres cut)."""
    if drawn is None or not len(holds):
        return list(holds), 0.0
    out, cut = [], 0.0
    O, D, idx, ts = [], [], [], []
    for k, (a, b, up, w) in enumerate(holds):
        a, b, w = np.asarray(a, np.float64), np.asarray(b, np.float64), np.asarray(w, np.float64)
        n = max(2, int(np.ceil(np.linalg.norm(b - a) / step)) + 1)
        for t in np.linspace(0, 1, n):
            O.append(a + (b - a) * t - [0, 0, 0.05] + w * 1.0)
            D.append(-w)
            idx.append(k)
            ts.append(t)
    O, D = np.array(O), np.array(D)
    g = _first_hits(O, D, drawn) - 1.0
    near = np.full(len(O), np.inf)
    P = O + D * 1.0                                     # (the sample points themselves)
    lo_t, hi_t = drawn.min(1), drawn.max(1)
    idx, ts = np.array(idx), np.array(ts)
    for k in range(len(holds)):
        sel = np.nonzero(idx == k)[0]
        plo, phi = P[sel].min(0) - gap, P[sel].max(0) + gap
        cand = drawn[((lo_t <= phi) & (hi_t >= plo)).all(1)]
        if len(cand):
            for i in sel:
                near[i] = climb.point_tri_distance(P[i], cand).min()
    for k, (a, b, up, w) in enumerate(holds):
        sel = idx == k
        good = (near[sel] <= gap) & ~(g[sel] < -BURIED)
        t = ts[sel]
        a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
        length = float(np.linalg.norm(b - a))
        i = 0
        kept = 0.0
        while i < len(t):
            if not good[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(t) and good[j + 1]:
                j += 1
            if (t[j] - t[i]) * length >= min_len:
                out.append((a + (b - a) * t[i], a + (b - a) * t[j], up, w))
                kept += (t[j] - t[i]) * length
            i = j + 1
        cut += length - kept
    return out, cut


def roof_skin(V, T, names, floors=(), floor_at=None):
    """The building's roofs that are FuzzyZone / camera-only (NOT_CLIMBABLE names, one per triangle): up-facing
    (walkable: n.z >= 0.707) faces with nothing of the building above them, ROOF_CLEAR m or more over the floor under
    them, lifted ROOF_LIFT m along their normal - a walkable skin like Boston's Wood_Plank roofs. -> (verts,
    tris) or None."""
    if names is None:
        return None
    V, T = np.asarray(V, np.float64), np.asarray(T, np.int64)
    tri = V[T]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    a = np.linalg.norm(n, axis=1) / 2
    n /= np.maximum(2 * a, 1e-12)[:, None]
    bad = np.array([str(x or "").startswith(NOT_CLIMBABLE) for x in names], bool)
    cand = np.nonzero(bad & (n[:, 2] >= climb.SLOPE_COS) & (a > 0.01))[0]
    if not len(cand):
        return None
    c = tri[cand].mean(1)
    open_sky = ~covered(c + n[cand] * 0.05, tri)
    under = [tri] + [np.asarray(fv, np.float64)[np.asarray(ft, np.int64)] for fv, ft in floors if len(ft)]
    fz = _floor_under(c - [0, 0, 0.05], np.concatenate(under))
    if floor_at is not None:
        for i, p in enumerate(c):
            z = floor_at(p[0], p[1], p[2] - 0.3)    # (under the face itself: on its own map the map has it too)
            if z is not None and not (fz[i] >= z):
                fz[i] = z
    keep = cand[open_sky & ~np.isnan(fz) & (c[:, 2] - fz >= ROOF_CLEAR)]
    if not len(keep):
        return None
    sv = (tri[keep] + n[keep][:, None, :] * ROOF_LIFT).reshape(-1, 3)
    return sv, np.arange(len(sv)).reshape(-1, 3)


def reachable(tops, holds, floor_of, max_up=1.35, out_up=0.8, max_out=1.2):
    """Which top ledges a climber gets to from the ground by stock-sized steps (measured on Boston / Galata: straight
    up, stock's 95th percentile is 1.35 m on both maps, hundreds of steps over 1.5 m; stepping out 0.6-1.5 m, 0.57-0.8 m
    up): from one hold to the next one up on a face looking the same way, overlapping along it - at most `max_up` m straight up,
    or `out_up` m up when the next stands out up to `max_out` m - starting from a hold no higher than climb.REACH over
    the floor in front (a wall run). floor_of(holds) -> per hold, its floor's height (the higher of the floor in front
    and its wall's own foot: a building set on rooftops has the street far below). -> [bool] per top."""
    H = [(np.asarray(a, np.float64), np.asarray(b, np.float64), np.asarray(w, np.float64)) for a, b, _u, w in holds]
    if not H:
        return [False] * len(tops)
    fz = floor_of(holds)
    z = np.array([(a[2] + b[2]) / 2 for a, b, _w in H])
    start = np.isnan(fz) | (z - fz <= climb.REACH)
    up = np.array([0.0, 0.0, 1.0])

    def step(i, j):                                # can a climber on hold i get to hold j?
        (a, b, w), (a2, b2, w2) = H[i], H[j]
        if np.dot(w, w2) < 0.9:
            return False
        dz = z[j] - z[i]
        if dz <= 0.1:
            return False
        al = np.cross(up, w)
        ta, tb = sorted([a @ al, b @ al]), sorted([a2 @ al, b2 @ al])
        if min(ta[1], tb[1]) - max(ta[0], tb[0]) < 0.3:
            return False
        out = (a2 - a) @ w
        return (abs(out) <= 0.3 and dz <= max_up) or (0.3 < out <= max_out and dz <= out_up)

    order = np.argsort(z)
    got = start.copy()
    for jj in order:                               # (lowest first: a hold is reached from any reached hold below it)
        if got[jj]:
            continue
        got[jj] = any(got[i] and step(i, jj) for i in order if z[i] < z[jj])
    key = {(tuple(np.round(a, 3)), tuple(np.round(b, 3))): k for k, (a, b, _w) in enumerate(H)}
    return [bool(got[key[(tuple(np.round(np.asarray(a), 3)), tuple(np.round(np.asarray(b), 3)))]])
            if (tuple(np.round(np.asarray(a), 3)), tuple(np.round(np.asarray(b), 3))) in key else False
            for a, b, _u, _w in tops]


def layer(V, T, name, coll_mats=(), log=print, floors=(), floor_at=None, drawn=None, names=None):
    """A building's collision (world space: V (n, 3), T (m, 3)) -> (newmodel spec of its outside-climb layer or None,
    report dict). floors: [(verts, tris)] more collision to stand on (its ground part); floor_at(x, y, top) -> the
    map's walkable height there or None. Rows under MIN_ABOVE over the highest of them in front are left out.
    drawn: [(verts, tris)] the building's drawn triangles (world) - holds only where the drawn wall is (on_drawn).
    names: each collision triangle's CollisionMaterial name - its FuzzyZone roofs get a walkable skin (roof_skin)."""
    V, T = np.asarray(V, np.float64), np.asarray(T, np.int64)
    wall, outer = outer_faces(V, T)
    tri = V[T]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
    rep = dict(walls=int(wall.sum()), outer=int(outer.sum()), inner=int((wall & ~outer).sum()))
    side = sides(n[outer]) if outer.any() else np.array([])
    rep["outer_by_side"] = {s: int((side == s).sum()) for s in ("north", "east", "south", "west")}
    # handhold rows up the outer faces
    holds = climb.rows(V, T[outer], flush=True) if outer.any() else []
    if holds:
        under = [tri] + [np.asarray(fv, np.float64)[np.asarray(ft, np.int64)] for fv, ft in floors if len(ft)]
        under = np.concatenate(under)
        P = np.array([(np.asarray(a) + np.asarray(b)) / 2 + np.asarray(w) * 0.6 for a, b, _u, w in holds])
        fz = _floor_under(P, under)
        if floor_at is not None:
            for i, p in enumerate(P):
                z = floor_at(p[0], p[1], p[2])
                if z is not None and not (fz[i] >= z):
                    fz[i] = z
        keep = np.isnan(fz) | (P[:, 2] - fz >= MIN_ABOVE)
        rep["rows_low"] = int((~keep).sum())
        holds = [h for h, k in zip(holds, keep) if k]
    # ledges along the tops: level convex creases of the whole collision (a wall's top, a sloped roof's eave over its
    # soffit: counting only creases over an outer wall face would miss the eaves, and with them the roof from the long
    # sides) that face open air - the same test as outer_faces: a level ray out from just under the crease leaves the
    # building, and 1.5 m out there's nothing of it above. Each a beam at the crease (a ledge with no collision of its
    # own object behind it isn't climbed to).
    tops = []
    up = np.array([0.0, 0.0, 1.0])
    cand = []
    for a, b, n0, n1 in climb.ledges(V, T, min_len=0.5):
        a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
        if abs(b[2] - a[2]) > 0.15 * np.linalg.norm(b - a):
            continue                                  # (a gable's sloped verge: no level beam)
        w = np.array([n1[0], n1[1], 0.0])
        if np.linalg.norm(w) < 0.2:                   # (a flat soffit under the edge: the roof's own downhill way)
            w = np.array([n0[0], n0[1], 0.0])
        if np.linalg.norm(w) < 1e-3:
            continue
        w /= np.linalg.norm(w)
        cand.append((a, b, w))
    if cand:
        P = np.array([(a + b) / 2 - [0, 0, 0.1] + w * 0.08 for a, b, w in cand])
        D = np.array([w for _a, _b, w in cand])
        free = ~_rays_hit(P, D, tri, float(np.linalg.norm(V.max(0) - V.min(0))) + 2.0)
        free &= ~covered(np.array([(a + b) / 2 + w * OUT_CLEAR + [0, 0, 0.05] for a, b, w in cand]), tri)
        for (a, b, w), ok in zip(cand, free):
            if ok:
                lo_hi = sorted((a, b), key=lambda q: q @ np.cross(up, w))
                tops.append((lo_hi[0], lo_hi[1], up, w))
    rep["rows"] = len(holds)
    rep["top_ledges"] = len(tops)
    rep["top_list"] = [(round(float(a_[2]), 1), round(float(np.linalg.norm(b_ - a_)), 1), str(sides(np.array([w_]))[0]))
                       for a_, b_, _u, w_ in tops]
    log(f"climbable outside: {rep['walls']} wall faces, {rep['outer']} outer "
        f"({', '.join(f'{k} {v}' for k, v in rep['outer_by_side'].items())}), {rep['inner']} inner left alone; "
        f"{len(holds)} handhold row(s) ({rep.get('rows_low', 0)} too low left out), {len(tops)} top ledge(s)")
    D = None
    if drawn:
        D = np.concatenate([np.asarray(dv, np.float64)[np.asarray(dt, np.int64)] for dv, dt in drawn if len(dt)])

    def floor_of(hs):
        under = [tri] + [np.asarray(fv, np.float64)[np.asarray(ft, np.int64)] for fv, ft in floors if len(ft)]
        P = np.array([(np.asarray(a) + np.asarray(b)) / 2 + np.asarray(w) * 0.6 for a, b, _u, w in hs])
        f = _floor_under(P, np.concatenate(under))
        if floor_at is not None:
            for i, p in enumerate(P):
                zz = floor_at(p[0], p[1], p[2])
                if zz is not None and not (f[i] >= zz):
                    f[i] = zz
        return f
    tops_kept, _c = on_drawn(tops, D)                 # (the way up only to top ledges that stay)
    reach = ways_up(tops_kept, tri, n, holds, floor_of)
    rep["step_chain"] = len(reach)
    if reach:
        log(f"  {len(reach)} hold(s) on the ways up to top ledges (up their faces, over step-outs)")
    holds = list(holds) + tops + reach
    n_before = len(holds)
    holds, rep["cut_m"] = on_drawn(holds, D)
    rep["off_drawn"] = n_before - len(holds)
    if holds:                                   # (a piece's new middle may stand under a higher part of the building)
        sky = ~covered(np.array([(np.asarray(a) + np.asarray(b)) / 2 + np.asarray(w) * OUT_CLEAR + [0, 0, 0.05]
                                 for a, b, _u, w in holds]), tri)
        holds = [h for h, k in zip(holds, sky) if k]
    if D is not None:
        log(f"  on the drawn walls: {rep['cut_m']:.1f} m of handholds cut where nothing drawn is within {DRAWN_GAP} m "
            f"(collision standing off the drawn wall) or the drawn wall stands in front ({len(holds)} pieces left)")
    # top ledges as cut by on_drawn: the pieces of the final holds lying on a top ledge's line
    on_top = [h for h in holds if any(abs(h[0][2] - t[0][2]) < 0.02 and np.linalg.norm(np.cross(
        np.asarray(t[1]) - np.asarray(t[0]), np.asarray(h[0]) - np.asarray(t[0]))) < 0.02 * max(np.linalg.norm(
        np.asarray(t[1]) - np.asarray(t[0])), 1e-6) for t in tops)]
    outer_tri = tri[outer]
    oc = outer_tri.mean(1)

    def start_floor(hs):                          # (the floor in front, or the foot of the wall there if higher)
        f = floor_of(hs)
        for i, (a_, b_, _u, _w) in enumerate(hs):
            m_ = (np.asarray(a_) + np.asarray(b_)) / 2
            near_ = np.linalg.norm(oc[:, :2] - m_[:2], axis=1) <= max(1.0, np.linalg.norm(np.asarray(b_) - np.asarray(a_)) / 2)
            if near_.any():
                foot = float(outer_tri[near_][:, :, 2].min())
                if np.isnan(f[i]) or foot > f[i]:
                    f[i] = foot
        return f
    ok = reachable(on_top, holds, start_floor) if on_top else []
    rep["tops_reachable"] = (int(sum(ok)), len(on_top))
    rep["tops_reachable_z"] = sorted({round(float(h[0][2]), 1) for h, k in zip(on_top, ok) if k})
    rep["tops_by_z"] = {z: (sum(1 for h, k in zip(on_top, ok) if k and round(float(h[0][2]), 1) == z),
                            sum(1 for h in on_top if round(float(h[0][2]), 1) == z))
                        for z in sorted({round(float(h[0][2]), 1) for h in on_top})}
    log(f"  {sum(ok)} of {len(on_top)} top ledge piece(s) reachable from the ground by stock-sized steps")
    skin = roof_skin(V, T, names, floors, floor_at)
    roof_mat = next((nm for nm in ROOF_NAMES if nm in coll_mats), None)
    if skin is not None and roof_mat is None:
        skin = None
    rep["roof_skin"] = 0 if skin is None else len(skin[1])
    if skin is not None:
        log(f"  {len(skin[1])} FuzzyZone roof triangle(s) given a walkable {roof_mat} skin ({ROOF_LIFT * 100:.0f} cm over them)")
    if not holds:
        return None, rep
    rep["holds"] = holds
    bv, bt = climb.beams(holds)
    n_beam = len(bt)
    if skin is not None:
        bt = np.vstack([bt, skin[1] + len(bv)])
        bv = np.vstack([bv, skin[0]])
    c0 = V.mean(0)
    m4 = np.eye(4)
    m4[3, :3] = c0
    spec = dict(name=name, matrix=m4, verts=bv - c0, tris=bt, uv=None, material=None, climb=True, visible=False)
    good = [nm for nm in CLIMBABLE_NAMES if nm in coll_mats]
    if good:
        spec["collision_materials"] = [good[0]] + ([roof_mat] if skin is not None else [])
        spec["collision_material_index"] = np.r_[np.zeros(n_beam, np.int64), np.ones(len(bt) - n_beam, np.int64)] \
            if skin is not None else 0
    return spec, rep


def classify_edges(edges, V, T, clear=OUT_CLEAR):
    """Climb edges [(a, b, n0, n1, ...)] (world) against a building's collision (world V, T): -> (outside, inside) -
    inside = the point `clear` m out from the edge (along its wall's normal) has the building's collision above it
    (a room's ceiling: the same rule outer_faces uses)."""
    V, T = np.asarray(V, np.float64), np.asarray(T, np.int64)
    if not len(edges):
        return 0, 0
    P = []
    for e in edges:
        a, b, n1 = np.asarray(e[0], np.float64), np.asarray(e[1], np.float64), np.asarray(e[3], np.float64)
        h = np.array([n1[0], n1[1], 0.0])
        h /= max(np.linalg.norm(h), 1e-9)
        P.append((a + b) / 2 + h * clear + [0, 0, 0.05])
    ins = covered(P, V[T])
    return int((~ins).sum()), int(ins.sum())
