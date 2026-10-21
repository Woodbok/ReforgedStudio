"""New crowd routes (CrowdFlows) over the open streets of a region whose navmesh navgen made again.

    navgen.regenerate(src, dest, cells=[...], flows="generate")      (sceneport.build_scene(..., flows="generate"))

What stock routes are (measured on AC3's 7 maps): a CrowdFlow is a
polyline of 2-15 points (median segment 2 m, p90 3 m), 1-33 m long, 2.0-4.0 m wide (4 on streets, 2.1 in alleys),
bidirectional; its Head / Tail lists name the flows joined at its first / last point - at a junction every end lists
the others (cliques, 90%) and the ends stop short of the junction's centre (joined ends 3-4 m apart); dead ends are
rare (Wolcott none, Iceberg 19 of 290 ends).

Generation (`generate(gen)`, after navgen's steps; `gen` = navgen.Gen):

1. A 2D picture of the region's walkable squares (navgen's grid, every height) with small unwalkable spots inside the
   streets (posts, barrels: up to HOLE m^2) filled; walls = the region's unwalkable squares (the region's edge is open:
   streets run on into the stock navmesh).
2. The streets' centre lines: an exact distance transform with nearest-wall indices; medial-axis anchors = squares
   whose two nearest walls lie RIDGE apart and at least MIN_CLEAR from either; homotopic thinning (lowest clearance
   first, anchors kept, then a pass that only keeps branch tips) -> one-square-wide lines keeping every loop round a
   block.
3. Lines a usable stock route already walks (within COVER, its segments clear of new walls) are left to it.
4. A graph (junctions, ends, branches); dead ends shorter than MIN_SPUR dropped (an end at the region's open edge or by
   a stock route's end is not dead), junctions closer than JUNCTION_MERGE merged.
5. Each branch -> points on its squares (Douglas-Peucker, then SEG_MIN-SEG_MAX apart), ends trimmed END_GAP short of
   a junction, split into flows of at most MAX_POINTS points / MAX_LEN m (joined end to end); width from the clearance.
6. Joins: every end at a junction lists the others; an end within JOIN_REACH of a usable stock route's end (and a
   clear walk to it) is joined both ways. Each flow: navmodel.add_flow (a stock flow cloned with fresh ids, its type-5
   metalink in the manager of the cell its middle point stands in, its flow waypoints), TriangleRefs = the generated
   triangle under each point, ties = navgen's (ground waypoints each point sees within TIE_REACH, linked back at cost 0).
"""
import heapq
import math
from collections import Counter, defaultdict

import numpy as np

from . import navmodel

RES = 0.5
MARGIN = 12.0               # m of the stock ground round the region in the picture
HOLE = 2.0                  # m^2: smaller unwalkable spots inside a street (posts, barrels) are filled: no loop
HOLE_SPAN = 1.5             # m: ... and no wider than this (a thin wall is not a spot)
MIN_CLEAR = 1.0             # m from the nearest wall for a centre-line anchor (a 2 m wide passage)
RIDGE = 2.5                 # m: an anchor's two nearest walls at least this far apart (the medial axis' significance)
MIN_SPUR = 8.0              # m: dead ends shorter than this are dropped
JUNCTION_MERGE = 6.0        # m: junctions closer than this are one
MIN_LOOP = 20.0             # m: a loop shorter than this (round a cart, a tree) keeps only its shorter side
SEG_MIN, SEG_MAX = 2.0, 4.0     # m between points (stock: median 2.0, p90 2.9)
SIMPLIFY = 0.35             # m: Douglas-Peucker tolerance on the centre line
MAX_POINTS = 10
MAX_LEN = 30.0
END_GAP = 1.75              # m short of a junction's centre (stock joined ends sit 3-4 m apart)
JOIN_REACH = 6.0            # m: a new end joins a stock route's end this near
COVER = 2.5                 # m: centre line this near a usable stock route is left to it
LEVEL = 1.5                 # m: heights within this count as the same level (covers, joins)
STEP = 0.4                  # m: navgen's step
WIDTH = (2.0, 4.0)
D8 = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))


def _simple_table():
    """SIMPLE[mask] (8 neighbours, bit k = D8[k]): removing the centre keeps the picture's topology - one 8-connected
    foreground component round it and one 4-connected background component 4-adjacent to it."""
    def comps(pts, adj):
        pts, out = set(pts), []
        while pts:
            s = pts.pop()
            st, c = [s], {s}
            while st:
                a = st.pop()
                for b in [b for b in pts if adj(a, b)]:
                    pts.discard(b)
                    c.add(b)
                    st.append(b)
            out.append(c)
        return out
    tab = np.zeros(256, bool)
    for m in range(256):
        fg = [D8[k] for k in range(8) if m >> k & 1]
        bg = [D8[k] for k in range(8) if not m >> k & 1]
        c8 = comps(fg, lambda a, b: max(abs(a[0] - b[0]), abs(a[1] - b[1])) == 1)
        c4 = [c for c in comps(bg, lambda a, b: abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1)
              if any(abs(x) + abs(y) == 1 for x, y in c)]
        tab[m] = len(c8) == 1 and len(c4) == 1
    return tab


SIMPLE = _simple_table()


def _dt1(f):
    """1D squared distance transform (Felzenszwalb) of f (0 at sources, inf elsewhere) -> (d, nearest source)."""
    n = len(f)
    inf = math.inf
    d, a = [inf] * n, [-1] * n
    v, z = [0] * n, [0.0] * (n + 1)
    k = -1
    for q in range(n):
        if f[q] == inf:
            continue
        if k < 0:
            k, v[0], z[0], z[1] = 0, q, -inf, inf
            continue
        while True:
            p = v[k]
            s = ((f[q] + q * q) - (f[p] + p * p)) / (2 * q - 2 * p)
            if s <= z[k]:
                k -= 1
            else:
                break
        k += 1
        v[k], z[k], z[k + 1] = q, s, inf
    if k < 0:
        return d, a
    j = 0
    for x in range(n):
        while z[j + 1] < x:
            j += 1
        p = v[j]
        d[x], a[x] = (x - p) ** 2 + f[p], p
    return d, a


def edt(src):
    """Exact Euclidean distance (in squares) from every square to the nearest True one of `src` (nx, ny), and that
    square's indices (fx, fy; -1 where there is none)."""
    nx, ny = src.shape
    inf = math.inf
    g = np.full((nx, ny), inf)
    gy = np.full((nx, ny), -1, np.int64)
    for x in range(nx):
        d, a = _dt1([0.0 if s else inf for s in src[x]])
        g[x], gy[x] = d, a
    D = np.full((nx, ny), inf)
    fx = np.full((nx, ny), -1, np.int64)
    fy = np.full((nx, ny), -1, np.int64)
    for y in range(ny):
        d, a = _dt1(g[:, y].tolist())
        for x in range(nx):
            if a[x] >= 0:
                D[x, y], fx[x, y], fy[x, y] = d[x], a[x], gy[a[x], y]
    return np.sqrt(D), fx, fy


def _dp(pts, tol):
    """Douglas-Peucker over (n, k) points -> kept indices (first and last always)."""
    keep = {0, len(pts) - 1}
    st = [(0, len(pts) - 1)]
    while st:
        a, b = st.pop()
        if b <= a + 1:
            continue
        A, B = pts[a], pts[b]
        ab = B - A
        L = float(ab @ ab)
        seg = pts[a + 1:b] - A
        t = np.clip(seg @ ab / L, 0, 1) if L > 0 else np.zeros(len(seg))
        dist = np.linalg.norm(seg - t[:, None] * ab, axis=1)
        k = int(np.argmax(dist))
        if dist[k] > tol:
            keep.add(a + 1 + k)
            st += [(a, a + 1 + k), (a + 1 + k, b)]
    return sorted(keep)


class Routes:
    """The new route network of one navgen.Gen region (module docstring)."""

    def __init__(self, gen):
        self.gen, self.w, self.G = gen, gen.w, gen.G
        self.report = Counter()
        self.smp = {}                                   # id(new NFlow) -> the region sample under each point

    # ---- 1. the picture
    def picture(self):
        """The walkable picture: the region's squares (navgen's grid) and, MARGIN round it, the stock navmesh's
        ground - so the centre lines run on across the region's edge instead of hugging it (thinning pushes lines to
        the highest clearance: an edge with no wall beyond it would be that)."""
        G, gen, w = self.G, self.gen, self.w
        m = int(round(MARGIN / RES))
        nx, ny = G.nx + 2 * m, G.ny + 2 * m
        self.o = ox, oy = G.g0[0] - m, G.g0[1] - m
        self.occ = occ = np.zeros((nx, ny), bool)
        self.pix = {}                                 # (ix, iy) -> [the region sample there: the lowest]
        for i in np.nonzero(gen.alive)[0]:
            p = (int(G.gx[i]) - ox, int(G.gy[i]) - oy)
            if p not in self.pix or G.z[i] < G.z[self.pix[p][0]]:
                self.pix[p] = [int(i)]
        # one level: a square whose sample isn't linked to its neighbour's is a wall in the picture - the higher one
        # at a drop (a platform's rim, a roof's edge), one of the two where something thin stands between them
        rim = 0
        for p, (i,) in self.pix.items():
            for d, (dx, dy) in enumerate(((1, 0), (0, 1), (-1, 0), (0, -1))):
                q = self.pix.get((p[0] + dx, p[1] + dy))
                if q is None or G.nb[i, d] == q[0]:
                    continue
                dz = G.z[i] - G.z[q[0]]
                if dz > STEP or (abs(dz) <= STEP and d < 2):
                    rim += 1
                    break
            else:
                occ[p] = True
        self.pix = {p: v for p, v in self.pix.items() if occ[p]}
        self.report["squares at a drop or a thin wall (walls in the picture)"] = rim
        xs = (np.arange(nx) + ox + 0.5) * RES
        ys = (np.arange(ny) + oy + 0.5) * RES
        self.inreg = np.array([[w.cell_at(x, y) in gen.cellbox for y in ys] for x in xs], bool)
        # the stock ground round it (2D: any height)
        X0, Y0, X1, Y1 = xs[0] - RES, ys[0] - RES, xs[-1] + RES, ys[-1] + RES
        for _t, q in gen.stock.tris:
            lo, hi = q[:, :2].min(0), q[:, :2].max(0)
            if hi[0] < X0 or lo[0] > X1 or hi[1] < Y0 or lo[1] > Y1:
                continue
            a0 = max(int(math.floor(lo[0] / RES - 0.5)) - ox, 0)
            a1 = min(int(math.floor(hi[0] / RES - 0.5)) + 1 - ox, nx - 1)
            b0 = max(int(math.floor(lo[1] / RES - 0.5)) - oy, 0)
            b1 = min(int(math.floor(hi[1] / RES - 0.5)) + 1 - oy, ny - 1)
            if a1 < a0 or b1 < b0:
                continue
            gx, gy = np.meshgrid(np.arange(a0, a1 + 1), np.arange(b0, b1 + 1), indexing="ij")
            cx, cy = xs[gx], ys[gy]
            A, B, C = q[0], q[1], q[2]
            v0, v1 = B[:2] - A[:2], C[:2] - A[:2]
            den = v0[0] * v1[1] - v1[0] * v0[1]
            if abs(den) < 1e-12:
                continue
            px, py = cx - A[0], cy - A[1]
            l1 = (px * v1[1] - v1[0] * py) / den
            l2 = (v0[0] * py - px * v0[1]) / den
            ins = (l1 >= -1e-6) & (l2 >= -1e-6) & (l1 + l2 <= 1 + 1e-6) & ~self.inreg[gx, gy]
            occ[gx[ins], gy[ins]] = True
        # small unwalkable spots inside the streets: filled (4-connected background parts off the picture's edge, up
        # to HOLE m^2)
        seen = np.zeros((nx, ny), bool)
        filled = 0
        lim = HOLE / (RES * RES)
        for sx, sy in zip(*np.nonzero(~occ)):
            if seen[sx, sy]:
                continue
            part, st, edge = [], [(sx, sy)], False
            seen[sx, sy] = True
            while st:
                x, y = st.pop()
                part.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    u, v = x + dx, y + dy
                    if not (0 <= u < nx and 0 <= v < ny):
                        edge = True
                        continue
                    if not occ[u, v] and not seen[u, v]:
                        seen[u, v] = True
                        st.append((u, v))
            span = max(max(x for x, _y in part) - min(x for x, _y in part),
                       max(y for _x, y in part) - min(y for _x, y in part)) + 1
            if not edge and len(part) <= lim and span * RES <= HOLE_SPAN:
                for p in part:
                    occ[p] = True
                filled += self.inreg[part[0]]
        self.report["small spots walked round (filled)"] = int(filled)
        self.wall = ~occ
        D, self.fx, self.fy = edt(self.wall)
        self.clear = np.minimum(D, 100.0) * RES          # metres to the nearest wall

    # ---- 2. centre lines
    def centre_lines(self):
        occ, clear, fx, fy = self.occ, self.clear, self.fx, self.fy
        nx, ny = occ.shape
        anchor = np.zeros((nx, ny), bool)
        rid = (RIDGE / RES) ** 2
        for axis in (0, 1):
            a = (slice(0, nx - 1), slice(None)) if axis == 0 else (slice(None), slice(0, ny - 1))
            b = (slice(1, nx), slice(None)) if axis == 0 else (slice(None), slice(1, ny))
            both = occ[a] & occ[b] & (fx[a] >= 0) & (fx[b] >= 0)
            sep = (fx[a] - fx[b]) ** 2 + (fy[a] - fy[b]) ** 2
            ridge = both & (sep >= rid)
            pick_a = clear[a] >= clear[b]
            anchor[a] |= ridge & pick_a & (clear[a] >= MIN_CLEAR)
            anchor[b] |= ridge & ~pick_a & (clear[b] >= MIN_CLEAR)
        self.report["centre-line anchors"] = int(anchor.sum())
        fg = np.zeros((nx + 2, ny + 2), bool)
        fg[1:-1, 1:-1] = occ
        anc = np.zeros_like(fg)
        anc[1:-1, 1:-1] = anchor
        cl = np.zeros(fg.shape)
        cl[1:-1, 1:-1] = clear

        def code(x, y):
            m = 0
            for k, (dx, dy) in enumerate(D8):
                if fg[x + dx, y + dy]:
                    m |= 1 << k
            return m

        def thin(keep_anchors):
            h = [(cl[x, y], x, y) for x, y in zip(*np.nonzero(fg)) if not (keep_anchors and anc[x, y])]
            heapq.heapify(h)
            while h:
                _c, x, y = heapq.heappop(h)
                if not fg[x, y] or (keep_anchors and anc[x, y]):
                    continue
                m = code(x, y)
                if not SIMPLE[m] or (not keep_anchors and bin(m).count("1") < 2):
                    continue
                fg[x, y] = False
                for dx, dy in D8:
                    u, v = x + dx, y + dy
                    if fg[u, v] and not (keep_anchors and anc[u, v]):
                        heapq.heappush(h, (cl[u, v], u, v))
        thin(True)
        thin(False)
        self.skel = {(int(x) - 1, int(y) - 1) for x, y in zip(*np.nonzero(fg))}
        # the region's part (squares with region samples: not the stock ground round it, not filled spots)
        self.skel = {p for p in self.skel if self.pix.get(p)}
        self.report["centre-line squares"] = len(self.skel)

    # ---- 3. stock routes
    def stock_routes(self):
        """Usable stock flows near the region (not blocked, every segment a clear walk) and their segments / ends;
        centre-line squares a usable route walks are left to it. A route with a stretch through new geometry was
        already rerouted or untied by navgen (step 5, Gen._through_new); one with a stretch that is no clear walk with
        nothing new by it (the picture's quirks on stock ground: Animus 1, Iceberg 3 untouched) is left as it is - tied,
        not used here."""
        gen, w = self.gen, self.w
        x0, y0, x1, y1 = gen.box
        R = 30.0
        blocked = getattr(gen, "blocked_uids", set())
        self.usable, segs, crossing = [], [], 0
        for F in w.flows.values():
            if F.new or not any(x0 - R <= p[0] <= x1 + R and y0 - R <= p[1] <= y1 + R for p in F.points):
                continue
            if F.uid in blocked or not isinstance(F.link, navmodel.NLink):
                continue
            ok = all(self._clear_walk(a, b) for a, b in zip(F.points, F.points[1:]))
            if not ok:
                crossing += 1
                continue
            self.usable.append(F)
            segs += [(np.array(a), np.array(b)) for a, b in zip(F.points, F.points[1:])]
        self.report["stock routes with a shut stretch, nothing new by it (left as they are)"] = crossing
        if not segs or not self.skel:
            return
        sk = sorted(self.skel)
        P = np.array([self._xyz(p) for p in sk])
        gone = np.zeros(len(sk), bool)
        for a, b in segs:
            ab = b - a
            L = float(ab[:2] @ ab[:2])
            t = np.clip(((P[:, :2] - a[:2]) @ ab[:2]) / L, 0, 1) if L > 0 else np.zeros(len(P))
            q = a + t[:, None] * ab
            gone |= (np.linalg.norm(P[:, :2] - q[:, :2], axis=1) < COVER) & (np.abs(P[:, 2] - q[:, 2]) < LEVEL)
        self.report["centre-line squares a stock route walks"] = int(gone.sum())
        self.skel = {p for p, g in zip(sk, gone) if not g}

    def _clear_walk(self, a, b):
        return self.gen.clear_walk(a, b)

    def _sample(self, p, near_z=None):
        c = self.pix.get(p)
        if not c:
            return None
        if near_z is None:
            return min(c, key=lambda i: self.G.z[i])
        return min(c, key=lambda i: abs(self.G.z[i] - near_z))

    def _xyz(self, p, s=None):
        x0, y0 = self.o
        s = self._sample(p) if s is None else s
        return ((p[0] + x0 + 0.5) * RES, (p[1] + y0 + 0.5) * RES, float(self.G.z[s]))

    # ---- 4. the graph
    def graph(self):
        sk = self.skel
        nx, ny = self.occ.shape

        def nbrs(p):
            out = []
            for dx, dy in D8:
                q = (p[0] + dx, p[1] + dy)
                if q not in sk:
                    continue
                if dx and dy and ((p[0] + dx, p[1]) in sk or (p[0], p[1] + dy) in sk):
                    continue                              # a diagonal another neighbour already bridges
                out.append(q)
            return out
        nb = {p: nbrs(p) for p in sk}
        nodes = {p for p in sk if len(nb[p]) != 2}
        cl = {}
        for p in sorted(nodes):
            if p in cl:
                continue
            cl[p] = p
            st = [p]
            while st:
                a = st.pop()
                for q in nb[a]:
                    if q in nodes and q not in cl:
                        cl[q] = p
                        st.append(q)
        edges, used = [], set()
        for p in sorted(nodes):
            for q in nb[p]:
                if (p, q) in used or (q in nodes and cl[q] == cl[p]):
                    continue
                path, prev, cur = [p, q], p, q
                used.add((p, q))
                while cur not in nodes:
                    nxt = [r for r in nb[cur] if r != prev]
                    if not nxt:
                        break
                    prev, cur = cur, nxt[0]
                    path.append(cur)
                    if cur == p:
                        break
                used.add((cur, prev))
                if cur in nodes:
                    edges.append([cl[p], cl[cur], path])
        # loops with no junction
        on = {q for _a, _b, path in edges for q in path} | nodes
        for p in sorted(sk):
            if p in on:
                continue
            cl[p] = p
            path, prev, cur = [p], None, p
            while True:
                nxt = [r for r in nb[cur] if r != prev and (r not in on or r == p)]
                if not nxt:
                    break
                prev, cur = cur, nxt[0]
                path.append(cur)
                on.add(cur)
                if cur == p:
                    break
            on.add(p)
            if len(path) > 2:
                edges.append([p, p, path])
        # a node's kind: an end (one square, one branch) at the region's open edge / near a stock route's end
        self.node_px = defaultdict(list)
        for p, c in cl.items():
            self.node_px[c].append(p)
        ends = [(np.array(F.points[e]), F, e) for F in self.usable for e in (0, -1)]
        self.protect = {}
        for c, pxs in self.node_px.items():
            if len(pxs) != 1 or len(nb[pxs[0]]) != 1:
                continue
            x, y = pxs[0]
            edge = any(not (0 <= x + dx < nx and 0 <= y + dy < ny) or not self.inreg[x + dx, y + dy]
                       for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2))
            p = np.array(self._xyz(pxs[0]))
            stock = any(np.linalg.norm(q[:2] - p[:2]) < JOIN_REACH and abs(q[2] - p[2]) < LEVEL for q, _F, _e in ends)
            if edge or stock:
                self.protect[c] = "edge" if edge else "stock"
        self.edges = [[a, b, path, self._length(path)] for a, b, path in edges]
        self._prune()
        self.report["route branches"] = len(self.edges)

    def _spread(self, a, b):
        """The widest distance (m) between squares of junctions a and b were they one."""
        P = np.array(self.node_px[a] + self.node_px[b], np.float64)
        return float(np.linalg.norm(P[:, None] - P[None], axis=2).max()) * RES if len(P) > 1 else 0.0

    @staticmethod
    def _length(path):
        return sum(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(path, path[1:])) * RES

    def _prune(self):
        changed = True
        dropped = merged = 0
        while changed:
            changed = False
            deg = Counter()
            for a, b, _p, _L in self.edges:
                deg[a] += 1
                deg[b] += 1
            for k, (a, b, path, L) in enumerate(self.edges):
                if a == b:
                    if L < MIN_LOOP:
                        del self.edges[k]
                        dropped += 1
                        changed = True
                        break
                    continue
                spur = [n for n in (a, b) if deg[n] == 1 and n not in self.protect]
                lone = deg[a] == 1 and deg[b] == 1
                if (spur and L < MIN_SPUR and (lone or len(spur) == 1)) or (lone and L < MIN_SPUR):
                    del self.edges[k]
                    dropped += 1
                    changed = True
                    break
                if deg[a] >= 3 and deg[b] >= 3 and L < JUNCTION_MERGE and self._spread(a, b) < JUNCTION_MERGE:
                    del self.edges[k]
                    for e in self.edges:
                        e[0] = a if e[0] == b else e[0]
                        e[1] = a if e[1] == b else e[1]
                    self.node_px[a] += self.node_px.pop(b, [])
                    merged += 1
                    changed = True
                    break
            if changed:
                continue
            # two branches between the same junctions round a small obstacle: the shorter kept
            pairs = defaultdict(list)
            for k, (a, b, _p, L) in enumerate(self.edges):
                if a != b:
                    pairs[(min(a, b), max(a, b))].append((L, k))
            for _ab, ks in pairs.items():
                ks.sort()
                if len(ks) >= 2 and ks[0][0] + ks[1][0] < MIN_LOOP:
                    del self.edges[ks[1][1]]
                    dropped += 1
                    changed = True
                    break
            if changed:
                continue
            # a node left with two branches: one branch
            for n, d in deg.items():
                if d != 2 or n in self.protect:
                    continue
                inc = [k for k, e in enumerate(self.edges) if n in (e[0], e[1])]
                if len(inc) != 2:
                    continue
                e1, e2 = self.edges[inc[0]], self.edges[inc[1]]
                p1 = e1[2] if e1[1] == n else e1[2][::-1]
                o1 = e1[0] if e1[1] == n else e1[1]
                p2 = e2[2] if e2[0] == n else e2[2][::-1]
                o2 = e2[1] if e2[0] == n else e2[0]
                path = p1 + p2[1:] if p1[-1] == p2[0] else p1 + p2
                for k in sorted(inc, reverse=True):
                    del self.edges[k]
                self.edges.append([o1, o2, path, self._length(path)])
                changed = True
                break
        self.report["dead ends dropped"] = dropped
        self.report["junctions merged"] = merged

    # ---- 5. polylines -> flows
    def flows(self):
        gen, w, G = self.gen, self.w, self.G
        nav = w.nav
        deg = Counter()
        for a, b, _p, _L in self.edges:
            deg[a] += 1
            deg[b] += 1
        at_node = defaultdict(list)                  # node -> [(flow, 'head' / 'tail')]
        open_ends = []                               # (flow, end, point, sample)
        made, nxt = [], {}
        for a, b, path, L in self.edges:
            for piece, (ja, jb) in self._points(path, junction_a=deg[a] >= 3 or a == b, junction_b=deg[b] >= 3 or a == b):
                chain = self._split(piece)
                width = self._width(path)
                prev = None
                for k, pts in enumerate(chain):
                    F = self._make(pts, width)
                    made.append(F)
                    if prev is not None:
                        nxt[id(prev)] = F
                    if k == 0:
                        (at_node[a].append((F, "head")) if ja else open_ends.append((F, "head")))
                    if k == len(chain) - 1:
                        (at_node[b].append((F, "tail")) if jb else open_ends.append((F, "tail")))
                    prev = F
        if not made:
            self.report["routes made"] = 0
            return []
        lists = {id(F): {"head": [], "tail": []} for F in made}
        for F in made:
            if id(F) in nxt:
                lists[id(F)]["tail"].append(nxt[id(F)].uid)
                lists[id(nxt[id(F)])]["head"].append(F.uid)
        n_junction = 0
        for n, ends in at_node.items():
            for F, e in ends:
                for G2, _e2 in ends:
                    if G2 is not F and G2.uid not in lists[id(F)][e]:
                        lists[id(F)][e].append(G2.uid)
                        n_junction += 1
        # loose ends: joined to usable stock routes' ends near them (both ways)
        stock_add = defaultdict(lambda: {"head": [], "tail": []})
        n_stock = dead = 0
        ends = [(np.array(F.points[0 if e == "head" else -1]), F, e) for F in self.usable for e in ("head", "tail")]
        for F, e in open_ends:
            p = np.array(F.points[0 if e == "head" else -1])
            s = self.smp[id(F)][0 if e == "head" else -1]
            cands = sorted(((float(np.linalg.norm(q[:2] - p[:2])), q, S, se) for q, S, se in ends
                            if abs(q[2] - p[2]) < LEVEL), key=lambda c: c[0])
            got = 0
            for d, q, S, se in cands:
                if d > JOIN_REACH or got >= 2:
                    break
                if not gen.los_out(s, q):
                    continue
                lists[id(F)][e].append(S.uid)
                stock_add[S.uid][se].append(F.uid)
                got += 1
                n_stock += 1
            dead += got == 0
        for F in made:
            w.set_joins(F, head=lists[id(F)]["head"], tail=lists[id(F)]["tail"])
        for u, add in stock_add.items():
            S = w.flows[u]
            w.set_joins(S, head=S.head + [x for x in add["head"] if x not in S.head],
                        tail=S.tail + [x for x in add["tail"] if x not in S.tail])
        # ties: ground waypoints each point sees
        n_ties = 0
        for F in made:
            L = F.link
            objwp = []
            for j, (ou, wu, fw, _near) in enumerate(L.objwp):
                s = self.smp[id(F)][j]
                anchor = (gen.sample_tri[s], float(G.z[s]), s)
                near = [W for W in gen._ties(F.points[j], anchor) if w.near(W.mgr.cell, L.mgr.cell)]
                for W in near:
                    W.links.append((nav.WP_METALINK, L))
                n_ties += len(near)
                objwp.append((ou, wu, fw, near))
            L.objwp = objwp
        self.report["routes made"] = len(made)
        self.report["route points"] = sum(len(F.points) for F in made)
        self.report["route metres"] = round(sum(math.dist(a, b) for F in made for a, b in zip(F.points, F.points[1:])))
        self.report["route joins at junctions"] = n_junction
        self.report["route joins to stock routes"] = n_stock
        self.report["route dead ends"] = dead
        self.report["route ties to ground waypoints"] = n_ties
        return made

    def _points(self, path, junction_a, junction_b):
        """A branch's squares -> [(points [(xyz, sample)], (end a at the junction, end b at the junction))]: one
        piece, or more where the branch changes level (a 2D crossing of a bridge and the street under it)."""
        pieces, cur, prev_z = [], [], None
        for p in path:
            s = self._sample(p, prev_z)
            if s is None:
                continue
            z = float(self.G.z[s])
            if prev_z is not None and abs(z - prev_z) > 2 * 0.4:
                pieces.append(cur)
                cur = []
            cur.append((p, s))
            prev_z = z
        if cur:
            pieces.append(cur)
        out = []
        for k, sq in enumerate(pieces):
            ja = junction_a and k == 0
            jb = junction_b and k == len(pieces) - 1
            pts = self._polyline(sq, ja, jb)
            if pts is not None:
                out.append((pts, (ja, jb)))
        return out

    def _polyline(self, sq, trim_a, trim_b):
        xyz = np.array([self._xyz(p, s) for p, s in sq])
        if len(xyz) < 2:
            return None
        arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(xyz[:, :2], axis=0), axis=1))])
        total = arc[-1]
        gap = min(END_GAP, max(0.0, (total - SEG_MIN) / 2))
        lo = gap if trim_a else 0.0
        hi = total - gap if trim_b else total
        if hi - lo < SEG_MIN * 0.75:
            return None
        i0 = int(np.searchsorted(arc, lo - 1e-9))
        i1 = int(np.searchsorted(arc, hi + 1e-9, "right")) - 1
        idx = list(range(i0, i1 + 1))
        if len(idx) < 2:
            return None
        sub = xyz[idx] * (1.0, 1.0, 2.0)                 # height changes count double
        keep = [idx[k] for k in _dp(sub, SIMPLIFY)]
        # at most SEG_MAX apart (points added on the squares), at least SEG_MIN (inner points dropped)
        full = [keep[0]]
        for a, b in zip(keep, keep[1:]):
            n = int(math.ceil((arc[b] - arc[a]) / SEG_MAX - 1e-9))
            for k in range(1, n):
                t = arc[a] + (arc[b] - arc[a]) * k / n
                full.append(int(np.argmin(np.abs(arc - t))))
            full.append(b)
        out = [full[0]]
        for k in full[1:-1]:
            if arc[k] - arc[out[-1]] >= SEG_MIN:
                out.append(k)
        if arc[full[-1]] - arc[out[-1]] < SEG_MIN and len(out) > 1:
            out.pop()
        out.append(full[-1])
        out = list(dict.fromkeys(out))
        if len(out) < 2:
            return None
        # every segment a clear walk over the region's squares: else points of the centre line put back between
        gen = self.gen
        k = 0
        while k < len(out) - 1:
            a, b = out[k], out[k + 1]
            if b - a > 1 and not gen.los(sq[a][1], sq[b][1]):
                out.insert(k + 1, (a + b) // 2)
                continue
            k += 1
        return [(tuple(xyz[k]), sq[k][1], sq[k][0]) for k in out]

    def _split(self, pts):
        """Points -> one or more chained flows (at most MAX_POINTS points, MAX_LEN m each), sharing their joints."""
        n_seg = len(pts) - 1
        length = sum(math.dist(a[0], b[0]) for a, b in zip(pts, pts[1:]))
        n = max(1, math.ceil(n_seg / (MAX_POINTS - 1)), math.ceil(length / MAX_LEN))
        n = min(n, n_seg)
        cut = [round(k * n_seg / n) for k in range(n + 1)]
        return [pts[a:b + 1] for a, b in zip(cut, cut[1:])]

    def _width(self, path):
        c = np.median([self.clear[p] for p in path])
        return round(float(min(WIDTH[1], max(WIDTH[0], 2 * c - 1.0))), 1)

    def _make(self, pts, width):
        w, gen = self.w, self.gen
        xyz = [p for p, _s, _q in pts]
        mid = xyz[len(xyz) // 2]
        cell = w.cell_at(mid[0], mid[1])
        mgr = w.managers.get(cell) or w.managers[gen.cells[0]]
        F = w.add_flow(xyz, mgr, width=width)
        self.smp[id(F)] = [s for _p, s, _q in pts]
        w.set_flow_tris(F, [gen.sample_tri[s] for s in self.smp[id(F)]])
        return F


def generate(gen, log=print):
    """New routes over the region of navgen.Gen `gen` (after its steps). -> (new NFlows, report Counter)."""
    r = Routes(gen)
    r.picture()
    r.centre_lines()
    r.stock_routes()
    r.graph()
    made = r.flows()
    return made, r.report


def route_check(nm, uids):
    """Offline A* (the navmesh library's NavMap over the written archive) for new flows `uids`: each flow walked from its
    first to its last point, and every join (Head / Tail) from this end to the joined flow's end that names it (else
    its nearer end). -> Counter (walks, walks reached, joins, joins reached) and the misses."""
    c, miss = Counter(), []
    for u in uids:
        f = nm.flows.get(u)
        if f is None:
            c["missing"] += 1
            miss.append(f"{u:#x} not in the archive")
            continue
        c["walks"] += 1
        if nm.astar(nm.flow_end_wps(u, 0), nm.flow_end_wps(u, -1)) is not None:
            c["walks reached"] += 1
        else:
            miss.append(f"{f.name}: no ground path from its first point to its last")
        for end, lst in ((0, f.head), (-1, f.tail)):
            for x in lst:
                g = nm.flows.get(x)
                if g is None:
                    c["joins to nothing"] += 1
                    continue
                if u in g.head and u not in g.tail:
                    e2 = 0
                elif u in g.tail and u not in g.head:
                    e2 = -1
                else:
                    p = f.points[end]
                    e2 = 0 if math.dist(p, g.points[0]) <= math.dist(p, g.points[-1]) else -1
                c["joins"] += 1
                c["joins back-listed"] += u in (g.head if e2 == 0 else g.tail)
                if nm.astar(nm.flow_end_wps(u, end), nm.flow_end_wps(x, e2)) is not None:
                    c["joins reached"] += 1
                else:
                    miss.append(f"{f.name} -> {g.name}: no ground path")
    return c, miss
