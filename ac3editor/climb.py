"""Climb edges (the GuidanceSystem component): reading them, and generating ledges from a collision mesh.

Format (measured on Fort Wolcott's 188 GuidanceSystems, 671 edges):

* `CompressPoints`: u16 x, y, z per point, each (q - 0x8000) * 5 mm, in the entity's own frame (every edge endpoint
  within 5 cm of a vertex of the entity's collision mesh, median 4 mm - the step).
* `GuidanceObjects`: one per edge - Index0 -> Index1 (points), SubType (1 ledge: 638, 4 pole: 29, 14 beam: 4 - perch()),
  Valid,
  DecN4Normal0 / 1: int16 x, y, z, 0 with 510 = 1.0 - the two faces meeting at the edge, ordered so that the edge's
  direction = normal0 x normal1 (671 of 671).
* `Partitioner`: Min / Max = the points' box, LeafsIndex = a permutation of the edges, ListNodes = one leaf node
  (NodeType 0, Index0 0, Index1 = edge count) in 187 of 188.
* `EdgeFilter`: identical in all 188 (WallGround and GroundCeiling edges on, slope cos 0.707, wall angle 22.5 deg,
  slope angle 45 deg) - the settings of the bake-time generator.
"""
import struct

import numpy as np

STEP = 0.005
BIAS = 32768
NORMAL = 510.0
SLOPE_COS = 0.7071                      # EdgeFilter.SlopeCosAngle: a face this flat or flatter is ground


def read(gs_fields):
    """GuidanceSystem fields (fastload) -> (points (n, 3) local, [(i0, i1, subtype, n0, n1)])."""
    q = np.frombuffer(b"".join(gs_fields["CompressPoints"]), "<u2").astype(np.float64)
    pts = (q.reshape(-1, 3) - BIAS) * STEP
    edges = []
    for x in gs_fields["GuidanceObjects"]:
        g = x.obj if hasattr(x, "obj") and x.obj is not None else x
        f = g.fields
        n0 = np.array(struct.unpack("<4h", f["DecN4Normal0"])[:3]) / NORMAL
        n1 = np.array(struct.unpack("<4h", f["DecN4Normal1"])[:3]) / NORMAL
        edges.append((struct.unpack("<I", f["Index0"])[0], struct.unpack("<I", f["Index1"])[0],
                      struct.unpack("<I", f["SubType"])[0], n0, n1))
    return pts, edges


def ledges(verts, tris, min_len=0.3, crease_cos=0.9):
    """Convex edges where a ground face (normal z >= SLOPE_COS) meets a wall face (below it), from a collision mesh
    in the entity's frame. -> [(a, b, n0, n1)] with direction a->b = n0 x n1, collinear runs merged."""
    v = np.asarray(verts, np.float64)
    t = np.asarray(tris, np.int64)
    fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    ln = np.linalg.norm(fn, axis=1)
    ok = ln > 1e-9
    fn[ok] /= ln[ok, None]
    # weld vertices (collision meshes repeat positions)
    key = {tuple(np.round(p, 3)): i for i, p in enumerate(v)}
    weld = np.array([key[tuple(np.round(p, 3))] for p in v])
    faces = {}
    for f, tri in enumerate(t):
        if not ok[f]:
            continue
        for k in range(3):
            a, b = weld[tri[k]], weld[tri[(k + 1) % 3]]
            faces.setdefault((min(a, b), max(a, b)), []).append(f)
    found = []
    for (a, b), fs in faces.items():
        if len(fs) != 2:
            continue
        f0, f1 = fs
        n_a, n_b = fn[f0], fn[f1]
        if np.dot(n_a, n_b) > crease_cos:
            continue                                   # not a real crease
        ground, wall = (f0, f1) if n_a[2] >= n_b[2] else (f1, f0)
        if fn[ground][2] < SLOPE_COS or fn[wall][2] >= SLOPE_COS:
            continue
        # convex: the wall's third vertex lies below the ground plane
        third = [x for x in t[wall] if weld[x] not in (a, b)]
        if not third or np.dot(v[third[0]] - v[a], fn[ground]) > -1e-4:
            continue
        n0, n1 = fn[ground], fn[wall]
        d = np.cross(n0, n1)
        pa, pb = v[a], v[b]
        if np.dot(pb - pa, d) < 0:
            pa, pb = pb, pa
        found.append((pa, pb, n0, n1))
    return merge_collinear(found, min_len)


def merge_collinear(edges, min_len):
    """Joins edges that continue each other with the same face normals; drops the short ones. Same result, in the same
    order, as merge_collinear_slow (the all-pairs loop, kept for the equivalence check): a start-point hash on a 1 mm
    grid finds the candidates (the all-pairs loop dominates the build time of a big imported map)."""
    edges = [list(e) for e in edges]
    merged = True
    while merged:
        merged = False
        n = len(edges)
        starts = np.array([e[0] for e in edges], np.float64).reshape(-1, 3)
        index = {}
        for j, k in enumerate(map(tuple, np.floor(starts / 1e-3).astype(np.int64))):
            index.setdefault(k, []).append(j)
        offs = [(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)]
        for i in range(n):
            if edges[i] is None:
                continue
            pos = 0
            while True:
                a0, a1, n0, n1 = edges[i]
                k = np.floor(np.asarray(a1, np.float64) / 1e-3).astype(np.int64)
                cand = sorted(j for o in offs for j in index.get((k[0] + o[0], k[1] + o[1], k[2] + o[2]), ())
                              if j >= pos and j != i and edges[j] is not None)
                hit = None
                for j in cand:
                    b0, b1, m0, m1 = edges[j]
                    if np.linalg.norm(a1 - b0) < 1e-3 and np.dot(n0, m0) > 0.999 and np.dot(n1, m1) > 0.999:
                        hit = j
                        break
                if hit is None:
                    break
                edges[i] = [a0, edges[hit][1], n0, n1]
                edges[hit] = None
                merged = True
                pos = hit + 1
        edges = [e for e in edges if e is not None]
    return [tuple(e) for e in edges if np.linalg.norm(e[1] - e[0]) >= min_len]


def merge_collinear_slow(edges, min_len):
    """The original all-pairs version (reference for merge_collinear's equivalence check)."""
    edges = [list(e) for e in edges]
    merged = True
    while merged:
        merged = False
        for i in range(len(edges)):
            for j in range(len(edges)):
                if i == j or edges[i] is None or edges[j] is None:
                    continue
                a0, a1, n0, n1 = edges[i]
                b0, b1, m0, m1 = edges[j]
                if np.linalg.norm(a1 - b0) < 1e-3 and np.dot(n0, m0) > 0.999 and np.dot(n1, m1) > 0.999:
                    edges[i] = [a0, b1, n0, n1]
                    edges[j] = None
                    merged = True
        edges = [e for e in edges if e is not None]
    return [tuple(e) for e in edges if np.linalg.norm(e[1] - e[0]) >= min_len]


class World:
    """Every static collision triangle of a map in world coordinates, bucketed on a 4 m grid, for clearance tests."""

    def __init__(self, tris_world, cell=4.0):
        self.t = np.asarray(tris_world, np.float64)          # (n, 3, 3)
        self.cell = cell
        self.grid = {}
        lo = np.floor(self.t.min(1) / cell).astype(int)
        hi = np.floor(self.t.max(1) / cell).astype(int)
        for i, (a, b) in enumerate(zip(lo, hi)):
            for x in range(a[0], b[0] + 1):
                for y in range(a[1], b[1] + 1):
                    for z in range(a[2], b[2] + 1):
                        self.grid.setdefault((x, y, z), []).append(i)

    def near(self, p, r):
        c = np.floor(np.asarray(p) / self.cell).astype(int)
        k = int(np.ceil(r / self.cell))
        ids = set()
        for x in range(c[0] - k, c[0] + k + 1):
            for y in range(c[1] - k, c[1] + k + 1):
                for z in range(c[2] - k, c[2] + k + 1):
                    ids.update(self.grid.get((x, y, z), ()))
        return self.t[sorted(ids)] if ids else np.zeros((0, 3, 3))

    def blocked(self, p, r, skip=None):
        """Is any triangle (other than those in `skip`, a set of triangle-centroid keys) within r of point p?"""
        T = self.near(p, r)
        if not len(T):
            return False
        if skip:
            keep = [k for k, t in enumerate(T) if tuple(np.round(t.mean(0), 3)) not in skip]
            T = T[keep]
        return bool(len(T)) and point_tri_distance(np.asarray(p, np.float64), T).min() < r


def point_tri_distance(p, T):
    """Distance from p to each triangle of T (n, 3, 3)."""
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    ab, ac, ap = b - a, c - a, p - a
    n = np.cross(ab, ac)
    nn = np.maximum((n * n).sum(1), 1e-18)
    # barycentric of the projection
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = p - b
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = p - c
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    denom = np.maximum(va + vb + vc, 1e-18)
    v, w = vb / denom, vc / denom
    inside = (va >= 0) & (vb >= 0) & (vc >= 0)
    proj = a + v[:, None] * ab + w[:, None] * ac
    dist = np.where(inside, np.abs((ap * n).sum(1)) / np.sqrt(nn), np.inf)
    for s0, s1 in ((a, b), (b, c), (c, a)):              # outside: nearest point on the edges
        e = s1 - s0
        t = np.clip(((p - s0) * e).sum(1) / np.maximum((e * e).sum(1), 1e-18), 0, 1)
        q = s0 + t[:, None] * e
        dist = np.minimum(dist, np.linalg.norm(p - q, axis=1))
    del proj
    return dist


def hang_clear(world, a, b, wall_normal, own=None, out=0.35, depths=(0.4,), radius=0.15, need=0.75):
    """Is there room for a player hanging from edge a-b (world) on the wall side? Samples points `out` metres out
    from the wall and `depths` below the edge; `need` = the fraction of samples that must be free."""
    wn = np.asarray(wall_normal, np.float64)
    wn = wn - np.array([0, 0, wn[2]])
    if np.linalg.norm(wn) < 1e-6:
        return True
    wn /= np.linalg.norm(wn)
    free, total = 0, 0
    for f in np.linspace(0.15, 0.85, 4):
        base = a + f * (b - a) + wn * out
        for d in depths:
            total += 1
            free += not world.blocked(base - np.array([0, 0, d]), radius, own)
    return free >= need * total


def write(gs_fields, edges, subtype=1, template=None):
    """Fill a (deep-copied template) GuidanceSystem's fields with `edges` [(a, b, n0, n1[, subtype])] (entity frame).
    `template`: a GuidanceObject to copy when the system has none left."""
    import copy
    pts, index = [], {}

    def pid(p):
        q = tuple(np.clip(np.rint(np.asarray(p) / STEP) + BIAS, 0, 65535).astype(int))
        if q not in index:
            index[q] = len(pts)
            pts.append(q)
        return index[q]

    tmpl = gs_fields["GuidanceObjects"][0] if gs_fields["GuidanceObjects"] else template
    if tmpl is None:
        raise ValueError("an empty GuidanceSystem needs a template GuidanceObject")
    objs = []
    for e in edges:
        a, b, n0, n1 = e[:4]
        g = copy.deepcopy(tmpl)
        f = (g.obj if hasattr(g, "obj") and g.obj is not None else g).fields
        f["Valid"] = b"\x01"
        f["SubType"] = struct.pack("<I", e[4] if len(e) > 4 else subtype)
        f["Index0"], f["Index1"] = struct.pack("<I", pid(a)), struct.pack("<I", pid(b))
        f["DecN4Normal0"] = struct.pack("<4h", *np.rint(np.asarray(n0) * NORMAL).astype(int), 0)
        f["DecN4Normal1"] = struct.pack("<4h", *np.rint(np.asarray(n1) * NORMAL).astype(int), 0)
        objs.append(g)
    gs_fields["GuidanceObjects"] = objs
    gs_fields["CompressPoints"] = [struct.pack("<H", c) for p in pts for c in p]
    part = gs_fields["Partitioner"]
    pf = (part.obj if hasattr(part, "obj") and part.obj is not None else part).fields
    P = (np.array(pts, dtype=np.float64) - BIAS) * STEP
    pf["Min"] = struct.pack("<3f", *P.min(0))
    pf["Max"] = struct.pack("<3f", *P.max(0))
    proto = copy.deepcopy(pf["ListNodes"][0]) if pf["ListNodes"] else None
    if proto is None:
        raise ValueError("the GuidanceSystem template has no partitioner node to copy")
    ends = np.array([[np.asarray(e[0], np.float64)[:3], np.asarray(e[1], np.float64)[:3]] for e in edges]) \
        if len(edges) else np.zeros((0, 2, 3))
    nodes, leafs, root = partition(ends)
    built = []
    for t, i0, i1, mid in nodes:
        node = copy.deepcopy(proto)
        nf = (node.obj if hasattr(node, "obj") and node.obj is not None else node).fields
        nf["NodeType"] = struct.pack("<I", t)
        nf["Index0"], nf["Index1"] = struct.pack("<H", i0), struct.pack("<H", i1)
        if "Middle" in nf:
            nf["Middle"] = struct.pack("<f", mid)
        built.append(node)
    pf["RootIndex"] = struct.pack("<H", root)
    pf["LeafsIndex"] = [struct.pack("<H", i) for i in leafs]
    pf["ListNodes"] = built
    return gs_fields


LEAF_MAX = 220      # stock's biggest leaf (19,058 systems on 44 archives); a system with more
                    # edges gets a tree as stock builds them
LEAF_TARGET = 32


def partition(ends, leaf_max=None, target=LEAF_TARGET):
    """The climb partitioner for edges `ends` (n, 2, 3) - stock's kd-tree (decoded from Souk's ramparts): a leaf
    (NodeType 0) is a range Index0..Index1 of LeafsIndex; a split (NodeType 1 / 2 / 3 = x / y / z) sends what lies
    below `Middle` to node Index0 and the rest to Index1, an edge crossing the plane to both; nodes are listed children
    first, RootIndex names the top. Up to `leaf_max` edges: one leaf (the commonest stock form). -> ([(type, index0,
    index1, middle)], LeafsIndex, root)."""
    n = len(ends)
    leaf_max = LEAF_MAX if leaf_max is None else leaf_max
    if n <= leaf_max:
        return [(0, 0, n, 0.0)], list(range(n)), 0
    nodes, leafs = [], []

    def build(ids, depth):
        lo, hi = ends[ids].min(axis=(0, 1)), ends[ids].max(axis=(0, 1))
        if len(ids) <= target or depth > 24:
            nodes.append((0, len(leafs), len(leafs) + len(ids), 0.0))
            leafs.extend(int(i) for i in ids)
            return len(nodes) - 1
        mids = ends[ids].mean(1)
        for ax in np.argsort(-(hi - lo)):            # the longest side first; another if it can't divide
            mid = float(np.median(mids[:, ax]))
            a = ids[ends[ids, :, ax].min(1) < mid]
            b = ids[ends[ids, :, ax].max(1) >= mid]
            if len(a) < len(ids) and len(b) < len(ids):
                break
        else:
            a = b = None
        if a is not None and len(a) + len(b) > 1.5 * len(ids) and len(ids) <= leaf_max:
            a = b = None                             # edges crossing the plane go to both halves: long overlapping
            # runs (e.g. 603 edges of long planks) double at every level past 65,535 entries - one leaf instead
        if a is None:
            nodes.append((0, len(leafs), len(leafs) + len(ids), 0.0))
            leafs.extend(int(i) for i in ids)
            return len(nodes) - 1
        ia = build(a, depth + 1)
        ib = build(b, depth + 1)
        nodes.append((int(ax) + 1, ia, ib, mid))
        return len(nodes) - 1

    root = build(np.arange(n), 0)
    if len(nodes) > 0xFFFF or len(leafs) > 0xFFFF:
        raise ValueError(f"{n} climb edges make a partitioner too big")
    return nodes, leafs, root


STOCK_MAX = dict(edges=636, points=1195, nodes=96, leaf=LEAF_MAX)   # the biggest of 19,058 stock systems


def check(gs_fields):
    """A GuidanceSystem's consistency as the engine reads it -> (problems, warnings, stats). Problems: an index out of
    range (an edge's points, a leaf past LeafsIndex, LeafsIndex past the edges, a split naming a missing node, the
    root - a RootIndex past a trimmed node list crashes the game; a split's child 0xFFFF = empty side, as
    stock), an edge in no leaf, a cycle; empty systems
    must have RootIndex 0xFFFF and no nodes. Warnings: bigger than any stock system (STOCK_MAX)."""
    probs, warns = [], []
    f = gs_fields
    n_pts = len(f["CompressPoints"]) // 3
    objs = [x.obj if hasattr(x, "obj") and x.obj is not None else x for x in f["GuidanceObjects"]]
    n = len(objs)
    for k, g in enumerate(objs):
        i0, i1 = struct.unpack("<I", g.fields["Index0"])[0], struct.unpack("<I", g.fields["Index1"])[0]
        if i0 >= n_pts or i1 >= n_pts:
            probs.append(f"edge {k}: point {max(i0, i1)} of {n_pts}")
    part = f["Partitioner"]
    pf = (part.obj if hasattr(part, "obj") and part.obj is not None else part).fields
    nodes = []
    for nd in pf["ListNodes"]:
        nf = (nd.obj if hasattr(nd, "obj") and nd.obj is not None else nd).fields
        nodes.append((struct.unpack("<I", nf["NodeType"])[0], struct.unpack("<H", nf["Index0"])[0],
                      struct.unpack("<H", nf["Index1"])[0]))
    li = [struct.unpack("<H", x)[0] for x in pf["LeafsIndex"]]
    root = struct.unpack("<H", pf["RootIndex"])[0]
    if n == 0:
        if root != 0xFFFF or nodes:
            probs.append(f"no edges but RootIndex {root:#x} and {len(nodes)} node(s) (stock: 0xFFFF, none)")
        return probs, warns, dict(edges=0, nodes=len(nodes), leaf=0, points=n_pts)
    if root >= len(nodes):
        probs.append(f"RootIndex {root} past {len(nodes)} node(s)")
    if any(i >= n for i in li):
        probs.append(f"LeafsIndex names edge {max(li)} of {n}")
    seen, covered, biggest = set(), set(), 0
    stack = [root] if root < len(nodes) else []
    while stack:
        k = stack.pop()
        if k in seen:
            probs.append(f"node {k} reached twice (a cycle or a shared child)")
            continue
        seen.add(k)
        t, a, b = nodes[k]
        if t == 0:
            if a > b or b > len(li):
                probs.append(f"leaf {k}: {a}..{b} past LeafsIndex ({len(li)})")
                continue
            covered.update(li[a:b])
            biggest = max(biggest, b - a)
        elif t in (1, 2, 3):
            for c in (a, b):
                if c == 0xFFFF:
                    continue                     # nothing on that side (stock: 23 splits on 44 archives)
                if c >= len(nodes):
                    probs.append(f"split {k} names node {c} of {len(nodes)}")
                else:
                    stack.append(c)
        else:
            probs.append(f"node {k}: type {t}")
    if len(covered) < n and root < len(nodes):
        probs.append(f"{n - len(covered)} edge(s) in no leaf")
    stats = dict(edges=n, nodes=len(nodes), leaf=biggest, points=n_pts)
    for k, v in stats.items():
        if v > STOCK_MAX[k]:
            warns.append(f"{v} {k} (stock's biggest: {STOCK_MAX[k]})")
    return probs, warns, stats


REACH = 3.6         # the highest ledge a wall run reaches from the floor (stock lowest ledges: mostly 3-4 m up; a
                    # 3.5 m block can be climbed from the ground, a 5.4 m crate can't)
HOLD_STEP = 1.25    # stock spacing of ledges stacked on one face (the commonest gap)


def _in_tris(p, T, tol=0.01):
    """Is point p (on their plane) inside any of triangles T (n, 3, 3)?"""
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = np.maximum(d00 * d11 - d01 * d01, 1e-18)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    e = tol / np.sqrt(np.maximum(np.minimum(d00, d11), 1e-18))
    return bool(((v >= -e) & (w >= -e) & (v + w <= 1 + e)).any())


def handholds(verts, tris, edges, floor=None, reach=REACH, step=HOLD_STEP, margin=0.05, lowest=0.8):
    """Handholds down the walls of a tall object, so it can be climbed from the floor: stock tall structures have
    ledges part-way up (sills, beams); our generated ledges are top edges only. For each level ledge on a vertical
    wall more than `reach` above the floor in front of it, a ledge every `step` metres down the same wall until one
    is within reach (none closer than `lowest` to the floor). Each runs along the wall where the wall is really there
    (its coplanar faces), `margin` in from the ends. A wall that already has a ledge part-way down (stock sills) gets
    none.

    verts / tris / edges [(a, b, n0, n1, ...)]: the object's collision and ledges, entity frame. floor(point, wall
    normal) -> the floor's z below a ledge in that frame (None: nothing to stand on there - no handholds); default
    the collision's lowest point.
    -> [(a, b, up, wall normal)] - flat-wall ledges (direction a->b = up x wall, as climb.write wants)."""
    v = np.asarray(verts, np.float64)
    t = np.asarray(tris, np.int64)
    T = v[t]
    fn = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(fn, axis=1)
    ok = ln > 1e-9
    fn[ok] /= ln[ok, None]
    up = np.array([0.0, 0.0, 1.0])
    base = v[:, 2].min()
    level = []                                   # (z, a, b, wall normal (horizontal unit), plane offset)
    for e in edges:
        a, b, n0, n1 = (np.asarray(x, np.float64) for x in e[:4])
        wall = n1 if n1[2] < n0[2] else n0
        if abs(wall[2]) > 0.2 or abs(a[2] - b[2]) > 0.05:
            continue                             # not a level ledge on a (near) vertical wall
        wh = np.array([wall[0], wall[1], 0.0])
        wh /= np.linalg.norm(wh)
        level.append(((a[2] + b[2]) / 2, a, b, wh))
    out = []
    for z_top, a, b, wh in sorted(level, key=lambda x: -x[0]):
        mid = (a + b) / 2
        fz = floor(mid, wh) if floor is not None else base
        if fz is None:
            continue                             # nothing to stand on in front (backed against a rampart...)
        if z_top - fz <= reach:
            continue
        # this wall's own faces: coplanar with the wall just under the ledge
        d0 = float(np.dot(mid, wh))
        on = ok & (fn @ wh > 0.98) & (np.abs(T[:, 0] @ wh - d0) < 0.02)
        W = T[on]
        if not len(W):
            continue
        # a ledge already part-way down this stretch of wall (a stock sill): leave it to the designers
        span = lambda p, q: (min(np.dot(p, np.cross(up, wh)), np.dot(q, np.cross(up, wh))),  # noqa: E731
                             max(np.dot(p, np.cross(up, wh)), np.dot(q, np.cross(up, wh))))
        s0, s1 = span(a, b)
        below = [x for x in level if x[0] < z_top - 0.3 and np.dot(x[3], wh) > 0.98
                 and abs(np.dot((x[1] + x[2]) / 2, wh) - d0) < 0.1 and min(s1, span(x[1], x[2])[1])
                 - max(s0, span(x[1], x[2])[0]) > 0.3]
        if below:
            continue
        z = z_top - step
        while z >= fz + lowest:
            pa, pb = a.copy(), b.copy()
            pa[2] = pb[2] = z
            f = np.linspace(0.0, 1.0, 41)
            hit = np.array([_in_tris(pa + x * (pb - pa), W) for x in f])
            if hit.any():
                # the longest run of samples on the wall
                best, cur = (0, -1), None
                for i, h in enumerate(hit):
                    if h:
                        cur = i if cur is None else cur
                        if i - cur > best[1] - best[0]:
                            best = (cur, i)
                    else:
                        cur = None
                qa, qb = pa + f[best[0]] * (pb - pa), pa + f[best[1]] * (pb - pa)
                L = np.linalg.norm(qb - qa)
                if L > 2 * margin + 0.3:
                    d = (qb - qa) / L
                    qa, qb = qa + d * margin, qb - d * margin
                    if np.dot(qb - qa, np.cross(up, wh)) < 0:
                        qa, qb = qb, qa
                    out.append((qa, qb, up.copy(), wh.copy()))
            if z <= fz + reach:
                break
            z -= step
    return out


def beams(holds, depth=0.12, height=0.15):
    """Visible / solid handhold beams for flat-wall ledges [(a, b, up, wall)]: a box `depth` out from the wall and
    `height` deep under each ledge, open at the back (against the wall - so its back top edge isn't a crease and the
    ledge generator finds only the front top edge). -> (verts (n, 3), tris (m, 3)), outward wound."""
    V, Tr = [], []
    for a, b, up, wall in holds:
        a, b, up, wall = (np.asarray(x, np.float64) for x in (a, b, up, wall))
        d = depth * wall
        h = height * up
        # 0 a-back-top, 1 b-back-top, 2 b-front-top, 3 a-front-top, 4..7 the same at the bottom
        p = [a, b, b + d, a + d, a - h, b - h, b + d - h, a + d - h]
        k = len(V)
        V.extend(p)
        quads = [(0, 3, 2, 1), (3, 7, 6, 2), (4, 5, 6, 7), (0, 4, 7, 3), (1, 2, 6, 5)]  # top, front, bottom, ends
        for q in quads:
            tri1, tri2 = (q[0], q[1], q[2]), (q[0], q[2], q[3])
            for tr in (tri1, tri2):
                Tr.append([k + i for i in tr])
    V = np.array(V, np.float64).reshape(-1, 3)
    Tr = np.array(Tr, np.int64).reshape(-1, 3)
    # wind each face outward: away from the beam's centre line
    for i, tr in enumerate(Tr):
        P = V[tr]
        n = np.cross(P[1] - P[0], P[2] - P[0])
        hold = holds[i // 10]
        c = (np.asarray(hold[0]) + np.asarray(hold[1])) / 2 + 0.5 * depth * np.asarray(hold[3]) \
            - 0.5 * height * np.asarray(hold[2])
        if np.dot(n, P.mean(0) - c) < 0:
            Tr[i] = tr[::-1]
    return V, Tr


def oriented(edges):
    """Caller-placed edges [(a, b, n0, n1[, subtype])] as climb.write wants them: normals made unit, a and b swapped
    where the edge runs against n0 x n1 (stock: 671 of 671 run with it), subtype 1 (ledge) when not given."""
    out = []
    for e in edges:
        a, b, n0, n1 = (np.asarray(x, np.float64)[:3] for x in e[:4])
        n0, n1 = n0 / np.linalg.norm(n0), n1 / np.linalg.norm(n1)
        if np.linalg.norm(b - a) < 1e-3:
            raise ValueError(f"a climb edge of zero length at {np.round(a, 2)}")
        if np.linalg.norm(np.cross(n0, n1)) < 0.05:
            raise ValueError(f"climb edge {np.round(a, 2)} -> {np.round(b, 2)}: its two face normals are parallel")
        if np.dot(b - a, np.cross(n0, n1)) < 0:
            a, b = b, a
        out.append((a, b, n0, n1, int(e[4]) if len(e) > 4 else 1))
    return out


# stock poles and beams to stand on (measured on Fort Wolcott): Prop_Pole_3m (SubType 4) is a box 0.10 m wide,
# 0.30 m deep, Pole_Beam (SubType 14) 0.22 m wide, 0.35 m deep; each has its two top long edges in its climb data
# (normals: the top and that side), nothing else special (same EdgeFilter, one partitioner leaf); their collision
# material is Wood_Solid_NoCAM_NoLOS (the camera passes through, no line-of-sight block)
PERCH = dict(pole=(0.10, 0.30, 4), beam=(0.22, 0.35, 14), ledge=(0.22, 0.35, 1))


def perch(a, b, kind="beam", width=None, depth=None, subtypes=None):
    """Something to stand on along a -> b (its top's centre line, entity frame; level or sloped): a solid box `width`
    wide and `depth` deep under the line, as stock's poles and beams (PERCH), and its two top long edges as climb
    edges. subtypes = (left, right): the SubType of each - left = the side up x (b - a) points to; default the kind's
    for both (e.g. a window sill: (14, 1) - a beam to stand on inside, a hang ledge outside; 0: no edge that side).
    -> (verts (8, 3), tris (12, 3) outward wound, edges [(a, b, n0, n1, subtype)])."""
    w0, d0, sub = PERCH[kind]
    w = w0 if width is None else float(width)
    d = d0 if depth is None else float(depth)
    sl, sr = (sub, sub) if subtypes is None else subtypes
    a, b = np.asarray(a, np.float64)[:3], np.asarray(b, np.float64)[:3]
    u = (b - a) / np.linalg.norm(b - a)
    s = np.cross([0.0, 0.0, 1.0], u)
    if np.linalg.norm(s) < 0.1:
        raise ValueError("a perch can't be (near) vertical")
    s /= np.linalg.norm(s)
    t = np.cross(u, s)                                  # the top's normal (up, tilted with a sloped perch)
    hw = s * w / 2
    # 0 a-left-top, 1 b-left-top, 2 b-right-top, 3 a-right-top, 4..7 the same at the bottom
    top = [a + hw, b + hw, b - hw, a - hw]
    V = np.array(top + [p - t * d for p in top])
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (3, 7, 6, 2), (0, 4, 7, 3), (1, 2, 6, 5)]
    T = []
    c = V.mean(0)
    for q in quads:
        for tri in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
            P = V[list(tri)]
            if np.dot(np.cross(P[1] - P[0], P[2] - P[0]), P.mean(0) - c) < 0:
                tri = tri[::-1]
            T.append(tri)
    # as stock's pole: left (n0 side, n1 top), right (n0 top, n1 side) - both run a -> b
    edges = [(V[0], V[1], s, t, sl), (V[3], V[2], t, -s, sr)]
    return V, np.array(T, np.int64), [e for e in edges if e[4]]


def append(gs_fields, new_edges, template=None):
    """Adds `new_edges` [(a, b, n0, n1)] (entity frame) to a GuidanceSystem, keeping its own edges and their types."""
    pts, edges = read(gs_fields)
    keep = [(pts[a], pts[b], n0, n1, sub) for a, b, sub, n0, n1 in edges]
    tmpl = gs_fields["GuidanceObjects"][0] if gs_fields["GuidanceObjects"] else template
    return write(gs_fields, keep + [tuple(e[:4]) + (1,) for e in new_edges], template=tmpl)


FIRST_ROW, ROW_MARGIN = 3.5, 0.3    # the MP climbing rows: the first row's height, the inset from a facade's ends


def rows(verts, tris, floor=None, window=1.0, min_run=2.0, gap=0.3, min_area=20.0, step=HOLD_STEP, flush=False):
    """Handhold rows up a building's facades in the multiplayer climbing style (no ladders; invisible handholds so a
    face climbs fast), for walls facing any way: a row every
    `step` m from FIRST_ROW above the floor while the wall goes on 0.4 m higher, across the facade's width at that
    height, ROW_MARGIN in from its ends, split where the wall has a gap over `gap` m (no beam across the air); a
    facade = the vertical faces within `window` m of one of a side's big planes (> `min_area` m2), sides found by the
    walls' facing (2-degree bins), a row nudged up to 0.5 m off cornices / sills. verts / tris: the building in its
    own frame. flush: each row piece on its own face - a facade whose wall steps back (recesses between
    buttresses, a plinth) gets a piece per depth instead of one beam at its outermost plane hanging in front of the
    recesses (on a buttressed building, up to a quarter of the rows would hang 0.6-1.1 m out from the drawn wall).
    -> holds [(a, b, up, wall)] for beams() (scenebuild: invisible collision beams, the ledges on them)."""
    V, T = np.asarray(verts, np.float64), np.asarray(tris, np.int64)
    if not len(T):
        return []
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    n = np.cross(B - A, C - A)
    area = np.linalg.norm(n, axis=1) / 2
    n = n / np.maximum(np.linalg.norm(n, axis=1)[:, None], 1e-9)
    cen = (A + B + C) / 3
    floor = float(V[:, 2].min()) if floor is None else float(floor)
    up = np.array([0.0, 0.0, 1.0])
    vert = np.nonzero((np.abs(n[:, 2]) < 0.1) & (area > 1e-6))[0]
    if not len(vert):
        return []
    az = np.degrees(np.arctan2(n[vert, 1], n[vert, 0])) % 360
    bins = np.rint(az / 2).astype(int) % 180
    weight = np.bincount(bins, weights=area[vert], minlength=180)
    holds, used = [], np.zeros(180, bool)
    for b in np.argsort(-weight):
        if weight[b] < min_area or used[b]:
            continue
        near = [(b + d) % 180 for d in (-2, -1, 0, 1, 2)]
        used[near] = True
        ang = np.radians(np.average(az[np.isin(bins, near)], weights=area[vert][np.isin(bins, near)]))
        wall = np.array([np.cos(ang), np.sin(ang), 0.0])
        along = np.cross(up, wall)
        sel = vert[(n[vert] @ wall > 0.97)]
        if not len(sel):
            continue
        off = cen[sel] @ wall
        pl = {}
        for o, a in zip(np.round(off, 1), area[sel]):
            pl[o] = pl.get(o, 0.0) + a
        facades = []
        for o in sorted((o for o, a in pl.items() if a > min_area), reverse=True):
            if all(o < f - window for f in facades):
                facades.append(o)
        for outer in facades:
            on = sel[(off >= outer - window) & (off <= outer + 0.05)]
            tri = np.stack([A[on], B[on], C[on]], 1)
            offs = cen[on] @ wall
            top = float(tri[:, :, 2].max())
            zmin, zmax = tri[:, :, 2].min(1), tri[:, :, 2].max(1)

            def rows_at(z, tri=tri, offs=offs, zmin=zmin, zmax=zmax, along=along, wall=wall):
                segs = []
                for i in np.nonzero((zmin <= z) & (zmax >= z))[0]:
                    s = []
                    for j in range(3):
                        p, q = tri[i, j], tri[i, (j + 1) % 3]
                        if (p[2] - z) * (q[2] - z) <= 0 and p[2] != q[2]:
                            s.append(float((p + (q - p) * (z - p[2]) / (q[2] - p[2])) @ along))
                    if len(s) >= 2:
                        segs.append((round(float(offs[i]), 2), min(s), max(s)))
                if not segs:
                    return []
                segs.sort(key=lambda r: r[1])
                runs, cur = [], [segs[0]]
                for sg in segs[1:]:
                    if sg[1] > max(c[2] for c in cur) + gap:
                        runs.append(cur)
                        cur = [sg]
                    else:
                        cur.append(sg)
                runs.append(cur)
                out = []
                if flush:
                    by_o = {}
                    for o, s0, s1 in segs:
                        by_o.setdefault(round(o / 0.05) * 0.05, []).append((s0, s1, o))
                    for _k, iv in by_o.items():
                        iv.sort()
                        cur = [iv[0]]
                        for x in iv[1:] + [None]:
                            if x is not None and x[0] <= max(c[1] for c in cur) + 0.05:
                                cur.append(x)
                                continue
                            lo, hi = cur[0][0] + ROW_MARGIN, max(c[1] for c in cur) - ROW_MARGIN
                            o = max(c[2] for c in cur)
                            if hi - lo > 0.5:
                                out.append((o * wall + lo * along + z * up, o * wall + hi * along + z * up, up, wall))
                            cur = [x] if x is not None else []
                    return out
                for run in runs:
                    tot = {}
                    for o, s0, s1 in run:
                        tot[o] = tot.get(o, 0.0) + (s1 - s0)
                    width = max(c[2] for c in run) - run[0][1]
                    good = [o for o, r in tot.items() if r >= min(min_run, 0.6 * width)]
                    if not good:
                        continue
                    o = max(good)
                    lo, hi = run[0][1] + ROW_MARGIN, max(c[2] for c in run) - ROW_MARGIN
                    if hi - lo <= 0.5:
                        continue
                    a = o * wall + lo * along + z * up
                    b_ = o * wall + hi * along + z * up
                    out.append((a, b_, up, wall))       # low -> high along the wall
                return out

            z = floor + FIRST_ROW
            while z <= top - 0.4:
                for dz in (0.0, -0.3, 0.3, -0.5, 0.5):
                    h = rows_at(z + dz) if z + dz <= top - 0.4 else []
                    if h:
                        holds += h
                        break
                z += step
    return holds
