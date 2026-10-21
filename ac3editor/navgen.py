"""Navmesh generated from collision: where new geometry stands, crowds get new ground to walk on, a waypoint graph to
path over and their routes (CrowdFlows) tied to it.

    navgen.regenerate(src, dest, cells=[...] or box=(x0, y0, x1, y1)[, flows="generate"])     -> report dict

What stock navmeshes are (measured on Fort Wolcott):
pieces lie exactly on their source object's collision (vertex z - collision z: 0.000), one per object per 32 m cell,
clipped to the cell; they reach walls with no margin; only where crowds walk (ground, benches, low props - no roofs,
ramparts or interiors); triangles counter-clockwise seen from above, neighbours share reversed edges. Border codes:
0xFFE3 = a seam, always carrying a type-0 metalink (side A / side B triangles, edge refs from both sides); 0xFFE2 /
0xFFE4 = ledge / wall (the engine's edge walk only asks for a neighbour or a metalink). Waypoints: a sparse graph (a
waypoint every ~10 m, links median 9.8 m, cost ~1.05 x distance), each triangle listing nearby waypoints A* starts
from. A CrowdFlow: one type-5 metalink (its triangles = the flow's TriangleRefs, one ObjectWayPoint per point naming
the flow's own waypoint and ~7 ground waypoints near it, each of which links back to the metalink at cost 0).

Generation, for whole grid cells (`cells`, or the cells a `box` touches; each must have a NavMeshManager):

1. The collision of the archive (ac3editor.mapdata: every static InertComponent shape on layer 1) is rasterised on a
   global 0.5 m grid: surfaces = up-facing triangles (<= 42 deg) sampled at each square's centre, obstacles = every
   triangle's height range over each square it touches. A surface sample is walkable when nothing stands between
   STEP (0.4 m) and HEIGHT (1.9 m) above it and no other surface lies within STEP above it (a low platform's top wins).
2. Walkable samples link to their 4 neighbours within STEP in height; pieces = linked samples of one object in one
   cell; the region keeps what joins the stock navmesh around it (or its largest part): roofs, crate tops and closed
   interiors drop out - but a part under a `keep` point stays (a merchant stands inside his stall, closed by its
   collision walls, on a navmesh island of its own - stock Mill's piece under AC3MP_GEN_Merchant_Meat_001's merchant;
   without it the merchant never comes).
3. Each piece is meshed by greedy rectangles (flat to FLAT, at most MAX_RECT squares a side), vertices shared along
   linked edges, T-junctions split, rectangles fanned; borders classified (seam to another piece / to the stock
   navmesh across the region's edge = 0xFFE3 + type-0 metalinks; a drop = 0xFFE2; else 0xFFE4).
4. Waypoints: a geodesic Poisson-disk over the walkable squares (radius WP_RADIUS, open spots first); links between
   neighbouring regions with a clear straight walk (a waypoint added at their boundary when there is none), shortcuts
   within LINK_REACH; links across the region's edge to stock waypoints; each triangle's list = the nearest
   waypoints it sees.
5. CrowdFlows with a point in the region: TriangleRefs anchored again, their type-5 link's ties rebuilt (ground
   waypoints seen from each point within TIE_REACH). A route through what was added (`new`: a stretch hitting it at
   knee / waist / head height, or no clear walk over the squares with it within REROUTE_NEAR - a rail, a low wall -
   or a point inside it) is REROUTED round it (`_reroute`: inner points by it dropped, ends moved clear, each stretch
   through it given the corners of a walk round it over the grid as new points - crowds steer along a route's
   own stretch first, so on one through a block they walk into it a while); an end inside it with no clear square
   within 3 m is dropped (the route shortened, its joins there dropped). A route that can't be rerouted (a stretch
   from outside the region, no walk round, over 15 points, under 4 m left) is untied and switched off (`Active` 0,
   dropped from the Head / Tail lists naming it: an untied route is still walked) - `blocked_flows`. A route by
   new geometry (within half its width + 0.5 m) is narrowed to twice the clearance there, not under 2 m (crowds
   spread across a flow's width: a 4 m route over a 3 m deck walks NPCs off its edge).
6. flows="generate": NEW CrowdFlows along the region's open streets where no usable route of the map walks
   (ac3editor/navflows.py: centre lines of the walkable squares, joined at junctions and to the map's routes nearby,
   each a stock flow entity cloned with fresh ids, its type-5 metalink, flow waypoints and ties), checked by A* over
   the written archive (`report["route_check"]`).
"""
import heapq
import math
from collections import Counter, defaultdict

import numpy as np

from . import mapdata as M
from . import navmodel
from .navmodel import NTri

RES = 0.5
STEP = 0.4
HEIGHT = 1.9
MAX_SLOPE = 42.0
FLAT = 0.08
MAX_RECT = 16
WP_RADIUS = 5.0
LINK_REACH = 16.0
LINK_MAX = 10
LIST_REACH = 14.0
LIST_MAX = 8
TIE_REACH = 20.0
TIE_MAX = 10
SEAM_RUN = 8                        # unit segments (4 m) per seam metalink
WALK_HEIGHTS = tuple(np.round(np.arange(0.3, 1.95, 0.1), 2))   # a person's height, every 0.1 m: a route stretch
                                    # with new geometry across it at any of these is through it
REROUTE_CLEAR = 2.0                 # m: a rerouted stretch keeps off walls where it can (string-pulled with half)
REROUTE_MOVE = 3.0                  # m: a route end inside / by new geometry moves to a clear square this near
REROUTE_NEAR = 1.0                  # m: a route point this near new geometry is "by" it (dropped, or an end moved)
REROUTE_DETOUR, REROUTE_EXTRA = 3.0, 30.0   # a walk round longer than 3 x the stretch + 30 m: not rerouted
REROUTE_MAX_POINTS = 15             # stock routes have up to 15 points
REROUTE_MIN_LEN = 4.0               # m: a route shortened (a buried end dropped) to less than this is untied instead
REROUTE_WIDTH_MIN = 2.0             # m: a route by new geometry is narrowed to its clearance, not under this (stock 2-4)
MOVING = ("ChaseBreaker", "_CB_", "Elevator", "Lift", "Door")


# --------------------------------------------------------------------------------------------- collision

def collision(md, zmin):
    """-> (tris (k, 3, 3) world, entity index per triangle, surface-eligible per entity) of every static layer-1
    shape. A moving body (chase-breaker doors, lifts) blocks but is never ground."""
    tris, ent = [], []
    # chase breakers' members (door leaves): crowds walk through them in stock (Mill's market houses, Animus's arches)
    breakers = {e.name for e in md.entities if "ChaseBreakerComponent" in e.components}
    for ei, e in enumerate(md.entities):
        if not e.shapes or "InertComponent" not in e.components or e.group in breakers:
            continue
        m = np.array(e.matrix, np.float64).reshape(4, 4)
        for s in e.shapes:
            if s not in md.shapes or md.shape_layer.get(s) != 1:
                continue
            _n, v, t = md.shapes[s]
            if not len(t):
                continue
            T = (v.astype(np.float64) @ m[:3, :3] + m[3, :3])[t]
            if T[:, :, 2].max() < zmin:
                continue                                # sunk out of the map (removed objects)
            tris.append(T)
            ent.append(np.full(len(T), ei, np.int64))
    surf = np.array([not any(k in (e.name or "") for k in MOVING) and "RigidBodyComponent" not in e.components
                     or "AIComponent" in e.components for e in md.entities], bool)
    if not tris:
        return np.zeros((0, 3, 3)), np.zeros(0, np.int64), surf
    return np.concatenate(tris), np.concatenate(ent), surf


class Grid:
    """The rasterised region: surface samples (walkable or not), their links, pieces."""

    def __init__(self, tris, ent, surf_ok, box):
        self.box = box                                   # x0, y0, x1, y1 (world) - whole cells
        x0, y0, x1, y1 = box
        self.g0 = (int(round(x0 / RES)), int(round(y0 / RES)))
        self.nx, self.ny = int(round((x1 - x0) / RES)), int(round((y1 - y0) / RES))
        lo, hi = tris.min(1), tris.max(1)
        near = (hi[:, 0] >= x0 - 1) & (lo[:, 0] <= x1 + 1) & (hi[:, 1] >= y0 - 1) & (lo[:, 1] <= y1 + 1)
        self.tris, self.ent = tris[near], ent[near]
        self.surf_ok = surf_ok
        self._raster()

    def key(self, gx, gy):
        return (gx - self.g0[0]) * self.ny + (gy - self.g0[1])

    def _raster(self):
        """Centre rays: every non-vertical triangle's height at each square centre under it (surfaces = up-facing
        walkable ones; the rest are what stands over a spot). Link crossings: each triangle's cross-section on the
        lines through the square centres (rows y = const, columns x = const), as height ranges over each centre-to-
        centre segment it spans - what stands between two neighbouring spots."""
        T, n = self.tris, len(self.tris)
        nrm = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        ln = np.linalg.norm(nrm, axis=1)
        ok = ln > 1e-12
        nz = np.where(ok, nrm[:, 2] / np.where(ok, ln, 1), 0)
        up = (nz >= math.cos(math.radians(MAX_SLOPE))) & self.surf_ok[self.ent]
        gx0, gy0 = self.g0
        H_k, H_z, H_t = [], [], []                  # centre-ray hits
        X_k, X_lo, X_hi, X_t = [], [], [], []       # link crossings: link key = square key * 2 + (0: +x, 1: +y)
        for i in range(n):
            t = T[i]
            lo, hi = t.min(0), t.max(0)
            ax0 = max(int(math.floor(lo[0] / RES - 0.5)), gx0)
            ax1 = min(int(math.floor(hi[0] / RES - 0.5)) + 1, gx0 + self.nx - 1)
            ay0 = max(int(math.floor(lo[1] / RES - 0.5)), gy0)
            ay1 = min(int(math.floor(hi[1] / RES - 0.5)) + 1, gy0 + self.ny - 1)
            if ok[i] and abs(nz[i]) > 1e-3 and ax1 >= ax0 and ay1 >= ay0:
                gx, gy = np.meshgrid(np.arange(ax0, ax1 + 1), np.arange(ay0, ay1 + 1), indexing="ij")
                gx, gy = gx.ravel(), gy.ravel()
                cx, cy = (gx + 0.5) * RES, (gy + 0.5) * RES
                a, b, c = t[0], t[1], t[2]
                v0, v1 = b[:2] - a[:2], c[:2] - a[:2]
                den = v0[0] * v1[1] - v1[0] * v0[1]
                if abs(den) > 1e-12:
                    px, py = cx - a[0], cy - a[1]
                    l1 = (px * v1[1] - v1[0] * py) / den
                    l2 = (v0[0] * py - px * v0[1]) / den
                    ins = (l1 >= -1e-9) & (l2 >= -1e-9) & (l1 + l2 <= 1 + 1e-9)
                    if ins.any():
                        H_k.append((gx[ins] - gx0) * self.ny + (gy[ins] - gy0))
                        H_z.append(a[2] + l1[ins] * (b[2] - a[2]) + l2[ins] * (c[2] - a[2]))
                        H_t.append(np.full(int(ins.sum()), i))
            # cross-sections on centre lines: axis 0 = rows (y fixed, links +x), axis 1 = columns (x fixed, links +y)
            for axis in (0, 1):
                o = 1 - axis
                g_lo = gy0 if axis == 0 else gx0
                g_n = self.ny if axis == 0 else self.nx
                l0 = max(int(math.ceil(lo[o] / RES - 0.5)), g_lo)
                l1_ = min(int(math.floor(hi[o] / RES - 0.5)), g_lo + g_n - 1)
                if l1_ < l0:
                    continue
                lines = np.arange(l0, l1_ + 1)
                Y = (lines + 0.5) * RES
                al_l, z_l = [], []
                for e in range(3):
                    A, B = t[e], t[(e + 1) % 3]
                    d = B[o] - A[o]
                    if abs(d) > 1e-12:
                        s_ = (Y - A[o]) / d
                        good = (s_ >= -1e-9) & (s_ <= 1 + 1e-9)
                        al_l.append(np.where(good, A[axis] + s_ * (B[axis] - A[axis]), np.nan))
                        z_l.append(np.where(good, A[2] + s_ * (B[2] - A[2]), np.nan))
                    else:                           # an edge lying along a line: both its ends are on it
                        on = np.abs(Y - A[o]) < 1e-9
                        for P in (A, B):
                            al_l.append(np.where(on, P[axis], np.nan))
                            z_l.append(np.where(on, P[2], np.nan))
                al = np.stack(al_l, 1)
                zz = np.stack(z_l, 1)
                have = ~np.isnan(al)
                rows = np.nonzero(have.any(1))[0]
                for li in rows:
                    h_ = have[li]
                    A_ = al[li][h_]
                    Z_ = zz[li][h_]
                    s0, s1 = A_.min(), A_.max()
                    za, zb = Z_[A_.argmin()], Z_[A_.argmax()]
                    g_a = gx0 if axis == 0 else gy0
                    n_a = self.nx if axis == 0 else self.ny
                    # links j -> j + 1 span centres (j + 0.5) RES .. (j + 1.5) RES
                    j0 = max(int(math.floor(s0 / RES - 1.5)), g_a)
                    j1 = min(int(math.floor(s1 / RES - 0.5)), g_a + n_a - 2)
                    if j1 < j0:
                        continue
                    js = np.arange(j0, j1 + 1)
                    u0 = np.maximum((js + 0.5) * RES, s0)
                    u1 = np.minimum((js + 1.5) * RES, s1)
                    keep = u1 >= u0 - 1e-9
                    if not keep.any():
                        continue
                    js, u0, u1 = js[keep], u0[keep], u1[keep]
                    if s1 - s0 > 1e-6:
                        f0, f1 = (u0 - s0) / (s1 - s0), (u1 - s0) / (s1 - s0)
                        z0_ = za + f0 * (zb - za)
                        z1_ = za + f1 * (zb - za)
                        zl, zh = np.minimum(z0_, z1_), np.maximum(z0_, z1_)
                    else:                           # an upright cross-section (a wall across the line)
                        zl = np.full(len(js), Z_.min())
                        zh = np.full(len(js), Z_.max())
                    line = int(lines[li])
                    if axis == 0:
                        keys = ((js - gx0) * self.ny + (line - gy0)) * 2
                    else:
                        keys = ((line - gx0) * self.ny + (js - gy0)) * 2 + 1
                    X_k.append(keys)
                    X_lo.append(zl)
                    X_hi.append(zh)
                    X_t.append(np.full(len(js), i))
        cat = lambda L, dt: np.concatenate(L) if L else np.zeros(0, dt)
        hk, hz, ht = cat(H_k, np.int64), cat(H_z, np.float64), cat(H_t, np.int64)
        # surfaces: up-facing walkable hits, one per height per square (triangles sharing an edge -> once)
        is_up = up[ht] if len(ht) else np.zeros(0, bool)
        sk, sz, st = hk[is_up], hz[is_up], ht[is_up]
        order = np.lexsort((sz, sk))
        sk, sz, st = sk[order], sz[order], st[order]
        dup = np.zeros(len(sk), bool)
        dup[1:] = (sk[1:] == sk[:-1]) & (np.abs(sz[1:] - sz[:-1]) < 0.05)
        sk, sz, st = sk[~dup], sz[~dup], st[~dup]
        # every centre-ray hit over each surface sample
        oo = np.argsort(hk, kind="stable")
        okk = hk[oo]
        a = np.searchsorted(okk, sk, "left")
        b = np.searchsorted(okk, sk, "right")
        cnt = b - a
        rep = np.repeat(np.arange(len(sk)), cnt)
        base = np.repeat(a - np.concatenate([[0], np.cumsum(cnt)[:-1]]), cnt)
        idx = oo[np.arange(cnt.sum()) + base]
        dz = hz[idx] - sz[rep]
        other = ht[idx] != st[rep]
        blocked = np.bincount(rep[other & (dz > STEP) & (dz < HEIGHT)], minlength=len(sk)) > 0
        # something within (0.05, STEP] over this one: the sample lies under a low top (the top is the ground there)
        under = np.bincount(rep[other & (dz > 0.05) & (dz <= STEP)], minlength=len(sk)) > 0
        covered = np.bincount(rep[other & (dz >= HEIGHT) & (dz < 60)], minlength=len(sk)) > 0
        walk = ~blocked & ~under
        self.k, self.z, self.t = sk[walk], sz[walk], st[walk]
        self.covered = covered[walk]
        self.e = self.ent[self.t]
        self.gx = self.k // self.ny + gx0
        self.gy = self.k % self.ny + gy0
        self.n = len(self.k)
        self.at = defaultdict(list)
        for i, k in enumerate(self.k.tolist()):
            self.at[k].append(i)
        nr = nrm[self.t]
        pb, pc = -nr[:, 0] / nr[:, 2], -nr[:, 1] / nr[:, 2]
        t0 = T[self.t, 0]
        self.plane = np.stack([t0[:, 2] - pb * t0[:, 0] - pc * t0[:, 1], pb, pc], 1)
        xk, xlo, xhi, xt = cat(X_k, np.int64), cat(X_lo, np.float64), cat(X_hi, np.float64), cat(X_t, np.int64)
        xo = np.argsort(xk, kind="stable")
        self.xk, self.xlo, self.xhi, self.xt = xk[xo], xlo[xo], xhi[xo], xt[xo]
        self._links()

    def _crossing(self, lk, i, j):
        """Does something stand on the segment between samples i and j (link key lk)? Their own triangles and
        anything up to the lower one's step height don't count."""
        a = np.searchsorted(self.xk, lk, "left")
        b = np.searchsorted(self.xk, lk, "right")
        if a == b:
            return False
        zl, zh, tt = self.xlo[a:b], self.xhi[a:b], self.xt[a:b]
        z0, z1 = min(self.z[i], self.z[j]), max(self.z[i], self.z[j])
        own = (tt == self.t[i]) | (tt == self.t[j])
        return bool((~own & (zh > z0 + STEP + 0.02) & (zl < z1 + HEIGHT)).any())

    def _links(self):
        """nb[i][d] = the walkable sample next to i in direction d (0 +x, 1 +y, 2 -x, 3 -y) within STEP with nothing
        standing between them, or -1."""
        nb = np.full((self.n, 4), -1, np.int64)
        D = ((1, 0), (0, 1), (-1, 0), (0, -1))
        for i in range(self.n):
            gx, gy, z = int(self.gx[i]), int(self.gy[i]), self.z[i]
            for d, (dx, dy) in enumerate(D[:2]):
                x, y = gx + dx, gy + dy
                if not (0 <= x - self.g0[0] < self.nx and 0 <= y - self.g0[1] < self.ny):
                    continue
                best, bd = -1, STEP
                for j in self.at.get(self.key(x, y), ()):
                    dz = abs(self.z[j] - z)
                    if dz <= bd:
                        best, bd = j, dz
                if best >= 0 and nb[best, d + 2] < 0 and not self._crossing(self.key(gx, gy) * 2 + d, i, best):
                    nb[i, d] = best
                    nb[best, d + 2] = i
        self.nb = nb

    def z_at(self, i, x, y):
        a, b, c = self.plane[i]
        return a + b * x + c * y


# --------------------------------------------------------------------------------------------- the stock mesh outside

class MeshIndex:
    """Point location on navmesh triangles (2D grid of 2 m buckets): which triangle is under (x, y) near height z."""

    def __init__(self, tris, cs=2.0):
        self.tris = tris                                   # [(NTri, corners (3, 3))]
        self.cs = cs
        self.g = defaultdict(list)
        for j, (_t, q) in enumerate(tris):
            lo, hi = q[:, :2].min(0), q[:, :2].max(0)
            for x in range(int(math.floor(lo[0] / cs)), int(math.floor(hi[0] / cs)) + 1):
                for y in range(int(math.floor(lo[1] / cs)), int(math.floor(hi[1] / cs)) + 1):
                    self.g[(x, y)].append(j)

    def locate(self, p, dz=1.0):
        best, bd = None, dz
        for j in self.g.get((int(math.floor(p[0] / self.cs)), int(math.floor(p[1] / self.cs))), ()):
            t, q = self.tris[j]
            a, b, c = q
            v0, v1 = b[:2] - a[:2], c[:2] - a[:2]
            den = v0[0] * v1[1] - v1[0] * v0[1]
            if abs(den) < 1e-12:
                continue
            px, py = p[0] - a[0], p[1] - a[1]
            l1 = (px * v1[1] - v1[0] * py) / den
            l2 = (v0[0] * py - px * v0[1]) / den
            if l1 >= -1e-4 and l2 >= -1e-4 and l1 + l2 <= 1 + 1e-4:
                z = a[2] + l1 * (b[2] - a[2]) + l2 * (c[2] - a[2])
                if abs(z - p[2]) <= bd:
                    best, bd = (t, z), abs(z - p[2])
        return best


def _inside(q, p, eps=1e-6):
    """Is 2D point p inside triangle q (3, 3+) (CCW or CW)?"""
    d = [(q[k][0] - p[0]) * (q[(k + 1) % 3][1] - p[1]) - (q[k][1] - p[1]) * (q[(k + 1) % 3][0] - p[0]) for k in range(3)]
    return all(v >= -eps for v in d) or all(v <= eps for v in d)


def _runs(segs):
    """Unit segments ((x0, y0), (x1, y1)) grouped into straight consecutive runs of at most SEAM_RUN."""
    left = set(segs)
    out = []
    for s in sorted(segs):
        if s not in left:
            continue
        run = [s]
        left.discard(s)
        a, b = s
        d = (b[0] - a[0], b[1] - a[1])
        while len(run) < SEAM_RUN:
            n = (b, (b[0] + d[0], b[1] + d[1]))
            if n not in left:
                break
            run.append(n)
            left.discard(n)
            b = n[1]
        out.append(run)
    return out


# --------------------------------------------------------------------------------------------- the generator

class Gen:
    def __init__(self, world, md, cells, log=print):
        self.w, self.md, self.log = world, md, log
        self.cells = sorted(cells)
        missing = [c for c in self.cells if c not in world.managers]
        if missing:
            raise ValueError(f"cells without a NavMeshManager can't be generated yet: {missing}")
        corners = [world.cell_corner(c) for c in self.cells]
        s = world.size
        self.cellbox = {c: (x, y, x + s, y + s) for c, (x, y) in zip(self.cells, corners)}
        self.box = (min(x for x, _y in corners), min(y for _x, y in corners),
                    max(x for x, _y in corners) + s, max(y for _x, y in corners) + s)
        self.report = Counter()
        self.new = None                   # navcut.Blockers of what was added (regenerate_under): routes through it untied
        self.keep = []                    # world points (x, y, z) whose walkable part stays even if closed in

    def in_region(self, x, y):
        c = self.w.cell_at(x, y)
        return c in self.cellbox

    # ---- 1-2: raster, pieces, keep
    def raster(self):
        w = self.w
        zs = [v[2] for g in w.managers.values() for p in g.pieces for v in p.verts]
        zmin = (min(zs) if zs else -50.0) - 30.0
        tris, ent, surf = collision(self.md, zmin)
        self.G = G = Grid(tris, ent, surf, self.box)
        # the cell of each sample; inside the region? (the box can hold cells that aren't in the region)
        cx = (G.gx + 0.5) * RES
        cy = (G.gy + 0.5) * RES
        self.cell = np.array([w.cell_at(x, y) for x, y in zip(cx, cy)])
        inreg = np.isin(self.cell, self.cells)
        # samples outside the region's cells are dropped (links to them cut)
        self.alive = inreg.copy()
        self.report["walkable squares"] = int(inreg.sum())
        # the stock navmesh outside the region (kept)
        out_tris = []
        for g in w.managers.values():
            if g.cell in self.cellbox:
                continue
            for p in g.pieces:
                for ti, t in enumerate(p.tris):
                    out_tris.append((t, np.array(p.corners(ti))))
        self.stock = MeshIndex(out_tris)

    def _union_pieces(self):
        G = self.G
        par = np.arange(G.n)

        def f(x):
            r = x
            while par[r] != r:
                r = par[r]
            while par[x] != r:
                par[x], x = r, par[x]
            return r
        for i in range(G.n):
            if not self.alive[i]:
                continue
            for d in (0, 1):
                j = G.nb[i, d]
                if j >= 0 and self.alive[j] and G.e[j] == G.e[i] and self.cell[j] == self.cell[i]:
                    a, b = f(i), f(j)
                    if a != b:
                        par[a] = b
        return np.array([f(i) for i in range(G.n)])

    def components(self):
        """Region components over every link; keep those touching the stock navmesh across the region's edge (else
        the largest) plus bench tops (AIComponent)."""
        G = self.G
        par = np.arange(G.n)

        def f(x):
            r = x
            while par[r] != r:
                r = par[r]
            while par[x] != r:
                par[x], x = r, par[x]
            return r
        for i in range(G.n):
            if not self.alive[i]:
                continue
            for d in (0, 1):
                j = G.nb[i, d]
                if j >= 0 and self.alive[j]:
                    a, b = f(i), f(j)
                    if a != b:
                        par[a] = b
        root = np.array([f(i) for i in range(G.n)])
        # touching stock: a sample at the region's edge with stock ground just across within STEP
        touch = set()
        D = ((1, 0), (0, 1), (-1, 0), (0, -1))
        for i in np.nonzero(self.alive)[0]:
            for d, (dx, dy) in enumerate(D):
                x = (G.gx[i] + 0.5 + dx * 0.6) * RES
                y = (G.gy[i] + 0.5 + dy * 0.6) * RES
                if self.in_region(x, y):
                    continue
                if self.stock.locate((x, y, G.z[i]), dz=STEP):
                    touch.add(root[i])
                    break
        sizes = Counter(root[self.alive].tolist())
        keep = set(touch)
        if not keep and sizes:
            keep.add(sizes.most_common(1)[0][0])
        bench = {r for i, r in zip(range(G.n), root) if self.alive[i] and "AIComponent" in
                 self.md.entities[G.e[i]].components}
        keep |= bench
        # the parts NPCs are placed on: a blend group's slots (a merchant behind his counter, closed in by the stall)
        held = set()
        for x, y, z in self.keep:
            if not self.in_region(x, y):
                continue
            hit = np.nonzero(self.alive & (G.gx == int(math.floor(x / RES))) & (G.gy == int(math.floor(y / RES))) &
                             (np.abs(G.z - z) <= STEP))[0]
            if len(hit):
                held.add(root[hit[np.argmin(np.abs(G.z[hit] - z))]])
        self.report["region parts kept for NPC slots"] = len(held - keep)
        keep |= held
        self.report["region parts kept"] = len(keep)
        self.report["region parts dropped"] = len(sizes) - len(keep & set(sizes))
        self.report["squares dropped (roofs, tops, interiors)"] = int(sum(v for r, v in sizes.items() if r not in keep))
        self.alive &= np.isin(root, list(keep))
        # links to dead samples cut
        dead = ~self.alive
        G.nb[np.isin(G.nb, np.nonzero(dead)[0])] = -1
        G.nb[dead] = -1
        self.pid = self._union_pieces()

    # ---- 3: meshing
    def mesh(self):
        G, w = self.G, self.w
        alive = np.nonzero(self.alive)[0]
        by_piece = defaultdict(list)
        for i in alive:
            by_piece[int(self.pid[i])].append(int(i))
        # corner union-find over linked edges (same piece only)
        cpar = {}

        def cf(x):
            cpar.setdefault(x, x)
            r = x
            while cpar[r] != r:
                r = cpar[r]
            while cpar[x] != r:
                cpar[x], x = r, cpar[x]
            return r

        def cu(a, b):
            a, b = cf(a), cf(b)
            if a != b:
                cpar[a] = b
        for i in alive:
            j = G.nb[i, 0]
            if j >= 0 and self.pid[j] == self.pid[i]:
                cu((i, 1), (j, 0))
                cu((i, 2), (j, 3))
            j = G.nb[i, 1]
            if j >= 0 and self.pid[j] == self.pid[i]:
                cu((i, 3), (j, 0))
                cu((i, 2), (j, 1))
        CORNER = ((0, 0), (1, 0), (1, 1), (0, 1))

        def corner_of(i, px, py):
            """The corner slot of square i at grid point (px, py)."""
            return CORNER.index((px - int(G.gx[i]), py - int(G.gy[i])))
        self.pieces = []
        self.piece_sq = {}
        self.sample_tri = {}                   # sample -> (NTri, corners) of the triangle over its centre
        self.seg_owner = {}                    # ((px, py), (qx, qy), piece key) -> (NTri, e, inside sample)
        for pk, members in by_piece.items():
            mem = set(members)
            sq = {(int(G.gx[i]), int(G.gy[i])): i for i in members}
            used = set()
            rects = []
            for i in sorted(members, key=lambda i: (G.gy[i], G.gx[i])):
                if i in used:
                    continue
                gx, gy = int(G.gx[i]), int(G.gy[i])

                def flat(j):
                    return abs(G.z[j] - G.z_at(i, (G.gx[j] + 0.5) * RES, (G.gy[j] + 0.5) * RES)) <= FLAT
                row = [i]
                while len(row) < MAX_RECT:
                    j = G.nb[row[-1], 0]
                    if j < 0 or j not in mem or j in used or not flat(j):
                        break
                    row.append(j)
                rows = [row]
                while len(rows) < MAX_RECT:
                    nxt = []
                    for j in rows[-1]:
                        k = G.nb[j, 1]
                        if k < 0 or k not in mem or k in used or not flat(k):
                            break
                        if nxt and G.nb[nxt[-1], 0] != k:
                            break
                        nxt.append(k)
                    if len(nxt) != len(row):
                        break
                    rows.append(nxt)
                for r in rows:
                    used.update(r)
                rects.append((gx, gy, len(row), len(rows), i, [j for r in rows for j in r]))
            # corners of rectangles (roots), for T-junctions
            rect_roots = set()
            for gx, gy, wd, ht, i0, sqs in rects:
                for px, py, s in ((gx, gy, sq[(gx, gy)]), (gx + wd, gy, sq[(gx + wd - 1, gy)]),
                                  (gx + wd, gy + ht, sq[(gx + wd - 1, gy + ht - 1)]), (gx, gy + ht, sq[(gx, gy + ht - 1)])):
                    rect_roots.add(cf((s, corner_of(s, px, py))))
            verts, vindex, zacc = [], {}, defaultdict(list)
            polys = []
            for gx, gy, wd, ht, i0, sqs in rects:
                # boundary walk, counter-clockwise: bottom (+x), right (+y), top (-x), left (-y)
                pts = []
                for k in range(wd):
                    pts.append((gx + k, gy, sq[(gx + k, gy)]))
                for k in range(ht):
                    pts.append((gx + wd, gy + k, sq[(gx + wd - 1, gy + k)]))
                for k in range(wd):
                    pts.append((gx + wd - k, gy + ht, sq[(gx + wd - 1 - k, gy + ht - 1)]))
                for k in range(ht):
                    pts.append((gx, gy + ht - k, sq[(gx, gy + ht - 1 - k)]))
                corners4 = {(gx, gy), (gx + wd, gy), (gx + wd, gy + ht), (gx, gy + ht)}
                poly = []
                for px, py, s in pts:
                    r = cf((s, corner_of(s, px, py)))
                    if (px, py) in corners4 or r in rect_roots:
                        if r not in vindex:
                            vindex[r] = len(verts)
                            verts.append([px * RES, py * RES, 0.0])
                        zacc[vindex[r]].append(G.z_at(i0, px * RES, py * RES))
                        poly.append((vindex[r], px, py))
                polys.append((poly, i0, sqs, (gx, gy, wd, ht)))
            for v, zz in zacc.items():
                verts[v][2] = float(np.mean(zz))
            idx, tri_rect = [], []
            for poly, i0, sqs, rect in polys:
                if len(poly) == 4:
                    a, b, c, d = [p[0] for p in poly]
                    idx += [a, b, c, a, c, d]
                    tri_rect += [rect, rect]
                else:
                    gx, gy, wd, ht = rect
                    cx, cy = (gx + wd / 2) * RES, (gy + ht / 2) * RES
                    ci = len(verts)
                    verts.append([cx, cy, float(G.z_at(i0, cx, cy))])
                    for k in range(len(poly)):
                        idx += [poly[k][0], poly[(k + 1) % len(poly)][0], ci]
                        tri_rect.append(rect)
            cell = int(self.cell[members[0]])
            mgr = w.managers[cell]
            src = int.from_bytes(self.md.entities[int(G.e[members[0]])].uid, "little")
            piece = w.add_piece(mgr, verts, idx, src)
            piece._key = pk
            V = np.array(verts)
            # neighbours inside the piece: reversed edges
            emap = {}
            for ti in range(len(idx) // 3):
                for e in range(3):
                    emap[(idx[3 * ti + e], idx[3 * ti + (e + 1) % 3])] = (ti, e)
            for (a, b), (ti, e) in emap.items():
                o = emap.get((b, a))
                if o is not None:
                    piece.tris[ti].nbr[e] = piece.tris[o[0]]
            # each sample's triangle (the one over its centre) and each boundary unit segment's owner
            vpos = {v: (round(V[v, 0] / RES), round(V[v, 1] / RES)) for v in range(len(verts))}
            for ti in range(len(idx) // 3):
                q = V[[idx[3 * ti], idx[3 * ti + 1], idx[3 * ti + 2]]]
                gx, gy, wd, ht = tri_rect[ti]
                for e in range(3):
                    if isinstance(piece.tris[ti].nbr[e], NTri):
                        continue
                    a, b = idx[3 * ti + e], idx[3 * ti + (e + 1) % 3]
                    if a not in vpos or b not in vpos or a >= len(verts) or b >= len(verts):
                        continue
                    pa, pb = vpos[a], vpos[b]
                    if abs(V[a, 0] - pa[0] * RES) > 1e-6 or abs(V[a, 1] - pa[1] * RES) > 1e-6 or \
                            abs(V[b, 0] - pb[0] * RES) > 1e-6 or abs(V[b, 1] - pb[1] * RES) > 1e-6:
                        continue                         # an edge to a fan centre is never a border
                    dx, dy = np.sign(pb[0] - pa[0]), np.sign(pb[1] - pa[1])
                    n = int(abs(pb[0] - pa[0]) + abs(pb[1] - pa[1]))
                    for k in range(n):
                        s0 = (pa[0] + dx * k, pa[1] + dy * k)
                        s1 = (pa[0] + dx * (k + 1), pa[1] + dy * (k + 1))
                        # the inside square of a CCW boundary edge lies to its left
                        if dx > 0:
                            ins = (s0[0], s0[1])
                        elif dx < 0:
                            ins = (s1[0], s1[1] - 1)
                        elif dy > 0:
                            ins = (s0[0] - 1, s0[1])
                        else:
                            ins = (s1[0], s1[1])
                        isq = sq.get(ins)
                        if isq is None:
                            continue
                        self.seg_owner[(min(s0, s1), max(s0, s1), pk)] = (piece.tris[ti], e, isq, (dx, dy))
            piece._ti = {id(t): k for k, t in enumerate(piece.tris)}
            self._sample_tris(piece, V, idx, tri_rect, sq)
            self.piece_sq[pk] = sq
            self.pieces.append(piece)
        self.report["pieces"] = len(self.pieces)
        self.report["triangles"] = sum(len(p.tris) for p in self.pieces)

    def _sample_tris(self, piece, V, idx, tri_rect, sq):
        """sample_tri[sample] = (NTri, z) - the triangle of the piece over the sample's centre."""
        G = self.G
        by_rect = defaultdict(list)
        for ti, r in enumerate(tri_rect):
            by_rect[r].append(ti)
        for (gx, gy, wd, ht), tis in by_rect.items():
            Q = [V[[idx[3 * t], idx[3 * t + 1], idx[3 * t + 2]]] for t in tis]
            for x in range(gx, gx + wd):
                for y in range(gy, gy + ht):
                    i = sq.get((x, y))
                    if i is None:
                        continue
                    p = ((x + 0.5) * RES, (y + 0.5) * RES)
                    for t, q in zip(tis, Q):
                        if _inside(q, p):
                            self.sample_tri[i] = piece.tris[t]
                            break
                    else:
                        self.sample_tri[i] = piece.tris[tis[0]]

    # ---- 3b: borders and seams
    def borders(self):
        G, w = self.G, self.w
        D = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
        # the outward direction of a CCW edge (dx, dy) is its right side: (dy, -dx)
        runs = defaultdict(list)            # (piece key a, piece key b) -> [(segment, tri a, e a)]
        stock_segs = []                     # (segment, tri, e, inside sample, outward)
        for (s0, s1, pk), (t, e, isq, (dx, dy)) in self.seg_owner.items():
            out = (int(dy), int(-dx))
            j = G.nb[isq, D[out]]
            if j >= 0 and self.alive[j] and self.pid[j] != pk:
                runs[(pk, int(self.pid[j]))].append(((s0, s1), t, e))
                continue
            # across the region's edge: stock ground?
            mx = ((s0[0] + s1[0]) / 2 + out[0] * 0.2) * RES
            my = ((s0[1] + s1[1]) / 2 + out[1] * 0.2) * RES
            if not self.in_region(mx, my):
                stock_segs.append(((s0, s1), t, e, isq, out))
                continue
            # a drop: walkable ground lower than a step just across?
            k = G.key(int(G.gx[isq]) + out[0], int(G.gy[isq]) + out[1])
            lower = [jj for jj in G.at.get(k, ()) if self.alive[jj] and G.z[jj] < G.z[isq] - STEP]
            t.nbr[e] = 0xFFE2 if lower else 0xFFE4
        # seams between new pieces: per pair, per straight line, runs of <= SEAM_RUN unit segments
        done = set()
        n_seams = 0
        for (pa, pb), segs in runs.items():
            if (pb, pa) in done:
                continue
            done.add((pa, pb))
            other = {s: (t, e) for s, t, e in runs.get((pb, pa), [])}
            for group in _runs([s for s, _t, _e in segs]):
                A = {}
                B = {}
                for s in group:
                    for s2, t, e in segs:
                        if s2 == s:
                            A[id(t)] = (t, e)
                    if s in other:
                        t, e = other[s]
                        B[id(t)] = (t, e)
                if not A or not B:
                    continue
                ta = next(iter(A.values()))[0]
                L = w.add_link(ta.piece.mgr, navmodel.SEAM, [t for t, _e in A.values()], [t for t, _e in B.values()])
                for t, e in list(A.values()) + list(B.values()):
                    t.nbr[e] = 0xFFE3
                    if L not in t.edge[e]:
                        t.edge[e].append(L)
                n_seams += 1
        # seams to the stock navmesh across the region's edge: one per stock border edge on the region's edge line,
        # side A that stock triangle, side B the new triangles along it at about its height
        n_stock = 0
        by_line = defaultdict(list)
        for (s0, s1), t, e, isq, out in stock_segs:
            axis = 0 if out[0] else 1                   # the coordinate constant along the edge
            by_line[(axis, s0[axis])].append(((s0, s1), t, e, isq))
        stock_edges = self._stock_border_edges()
        for (axis, line), segs in by_line.items():
            for a, b, st, se in stock_edges.get((axis, line), []):
                lo, hi = min(a[1 - axis], b[1 - axis]), max(a[1 - axis], b[1 - axis])
                mine = {}
                for (s0, s1), t, e, isq in segs:
                    u0, u1 = sorted((s0[1 - axis] * RES, s1[1 - axis] * RES))
                    if u0 < hi - 1e-6 and u1 > lo + 1e-6:
                        um = (max(u0, lo) + min(u1, hi)) / 2
                        f = (um - a[1 - axis]) / (b[1 - axis] - a[1 - axis]) if b[1 - axis] != a[1 - axis] else 0.5
                        zs = a[2] + f * (b[2] - a[2])
                        if abs(G.z[isq] - zs) <= STEP:
                            mine[id(t)] = (t, e)
                if not mine:
                    continue
                L = w.add_link(st.piece.mgr, navmodel.SEAM, [st], [t for t, _e in mine.values()])
                st.nbr[se] = 0xFFE3
                st.edge[se].append(L)
                for t, e in mine.values():
                    t.nbr[e] = 0xFFE3
                    if L not in t.edge[e]:
                        t.edge[e].append(L)
                n_stock += 1
        # new edges at the region's edge with no stock seam: walls
        for (s0, s1), t, e, isq, out in stock_segs:
            if t.nbr[e] != 0xFFE3:
                t.nbr[e] = 0xFFE4
        # leftover edges never classified (fan rims etc.)
        for p in self.pieces:
            for t in p.tris:
                for e in range(3):
                    if not isinstance(t.nbr[e], NTri) and t.nbr[e] == 0xFFE3 and not t.edge[e]:
                        t.nbr[e] = 0xFFE4
        self.report["seams between new pieces"] = n_seams
        self.report["seams to the stock navmesh"] = n_stock

    def _stock_border_edges(self):
        """Stock triangle edges lying on the region's outer edge lines: {(axis, grid line): [(a, b, tri, e)]}."""
        out = defaultdict(list)
        x0, y0, x1, y1 = self.box
        for g in self.w.managers.values():
            if g.cell in self.cellbox:
                continue
            gx0, gy0 = g.bv.lo[0], g.bv.lo[1]
            if g.bv.hi[0] < x0 - 1 or g.bv.lo[0] > x1 + 1 or g.bv.hi[1] < y0 - 1 or g.bv.lo[1] > y1 + 1:
                continue
            for p in g.pieces:
                for ti, t in enumerate(p.tris):
                    if all(isinstance(n, NTri) for n in t.nbr):
                        continue
                    q = p.corners(ti)
                    for e in range(3):
                        if isinstance(t.nbr[e], NTri):
                            continue
                        a, b = np.array(q[e]), np.array(q[(e + 1) % 3])
                        for axis in (0, 1):
                            if abs(a[axis] - b[axis]) < 1e-3:
                                line = a[axis] / RES
                                if abs(line - round(line)) < 1e-3:
                                    # on a cell line bordering the region?
                                    mid = (a + b) / 2
                                    off = np.zeros(2)
                                    off[axis] = 0.25
                                    if self.in_region(*(mid[:2] + off)) or self.in_region(*(mid[:2] - off)):
                                        out[(axis, int(round(line)))].append((a, b, t, e))
        return out

    # ---- 4: waypoints
    def los(self, a, b, ok=None):
        """A clear straight walk from sample a to sample b over the region's squares (4-neighbour steps along the
        segment, every step a link; with `ok`, every square on the way but b passing ok(sample))."""
        G = self.G
        if a == b:
            return True
        x0, y0 = (G.gx[a] + 0.5), (G.gy[a] + 0.5)
        x1, y1 = (G.gx[b] + 0.5), (G.gy[b] + 0.5)
        dx, dy = x1 - x0, y1 - y0
        sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        tdx = abs(1 / dx) if dx else math.inf
        tdy = abs(1 / dy) if dy else math.inf
        tx, ty = tdx * 0.5, tdy * 0.5
        cur = a
        cx, cy = int(G.gx[a]), int(G.gy[a])
        ex, ey = int(G.gx[b]), int(G.gy[b])
        dirx, diry = (0 if sx > 0 else 2), (1 if sy > 0 else 3)
        for _ in range(abs(ex - cx) + abs(ey - cy) + 2):
            if (cx, cy) == (ex, ey):
                return cur == b or abs(G.z[cur] - G.z[b]) <= STEP
            if abs(tx - ty) < 1e-9:                # through a corner: either way round
                j = G.nb[cur, dirx]
                k = G.nb[j, diry] if j >= 0 else -1
                if k < 0:
                    j = G.nb[cur, diry]
                    k = G.nb[j, dirx] if j >= 0 else -1
                if k < 0:
                    return False
                cur, cx, cy = k, cx + sx, cy + sy
                tx += tdx
                ty += tdy
            elif tx < ty:
                cur = G.nb[cur, dirx]
                cx += sx
                tx += tdx
            else:
                cur = G.nb[cur, diry]
                cy += sy
                ty += tdy
            if cur < 0:
                return False
            if ok is not None and cur != b and not ok(cur):
                return False
        return False

    def los_out(self, a, q):
        """A clear walk from region sample a to world point q (outside the region, or anywhere): over the squares
        while inside, then point by point over the stock navmesh (within STEP from one point to the next)."""
        G = self.G
        p = np.array([(G.gx[a] + 0.5) * RES, (G.gy[a] + 0.5) * RES, G.z[a]])
        q = np.asarray(q, np.float64)
        n = max(2, int(math.dist(p[:2], q[:2]) / (RES / 2)))
        cur, z = a, G.z[a]
        for k in range(1, n + 1):
            x = p + (q - p) * k / n
            if self.in_region(x[0], x[1]):
                gx, gy = int(math.floor(x[0] / RES)), int(math.floor(x[1] / RES))
                if cur is None:
                    return False                       # back into the region from outside: not needed
                if (gx, gy) != (int(G.gx[cur]), int(G.gy[cur])):
                    nxt = -1
                    for j in G.nb[cur]:
                        if j >= 0 and (int(G.gx[j]), int(G.gy[j])) == (gx, gy):
                            nxt = j
                    if nxt < 0:                        # a diagonal step: via either side
                        for j in G.nb[cur]:
                            if j < 0:
                                continue
                            for k2 in G.nb[j]:
                                if k2 >= 0 and (int(G.gx[k2]), int(G.gy[k2])) == (gx, gy):
                                    nxt = k2
                    if nxt < 0:
                        return False
                    cur = nxt
                z = G.z[cur]
            else:
                cur = None
                hit = self.stock.locate((x[0], x[1], z), dz=STEP)
                if hit is None:
                    return False
                z = hit[1]
        return abs(z - q[2]) <= 1.0

    def waypoints(self):
        G, w = self.G, self.w
        alive = np.nonzero(self.alive)[0]
        self.wps, self.wp_sample, self.wp_pos, self.stock_wps = [], [], np.zeros((0, 3)), []
        self.label = np.full(G.n, -1)
        if not len(alive):
            return
        # clearance (squares to the nearest square with a missing link)
        clear = np.full(G.n, np.inf)
        dq = []
        for i in alive:
            if (G.nb[i] < 0).any():
                clear[i] = 0
                dq.append(i)
        k = 0
        while k < len(dq):
            i = dq[k]
            k += 1
            for j in G.nb[i]:
                if j >= 0 and clear[j] > clear[i] + 1:
                    clear[j] = clear[i] + 1
                    dq.append(j)
        order = sorted(alive.tolist(), key=lambda i: (-min(clear[i], 50), G.gy[i], G.gx[i]))
        R = WP_RADIUS / RES
        dist = np.full(G.n, np.inf)
        seeds = []
        for s in order:
            if dist[s] <= R:
                continue
            seeds.append(s)
            dist[s] = 0
            h = [(0.0, s)]
            while h:
                d, i = heapq.heappop(h)
                if d > dist[i] or d > R:
                    continue
                for j in G.nb[i]:
                    if j >= 0 and d + 1 < dist[j]:
                        dist[j] = d + 1
                        heapq.heappush(h, (d + 1, j))
        # regions: a labelled multi-source search with parents
        label = np.full(G.n, -1)
        dist = np.full(G.n, np.inf)
        par = np.full(G.n, -1)
        h = []
        for k, s in enumerate(seeds):
            label[s], dist[s] = k, 0
            h.append((0.0, s))
        heapq.heapify(h)
        while h:
            d, i = heapq.heappop(h)
            if d > dist[i]:
                continue
            for j in G.nb[i]:
                if j >= 0 and d + 1 < dist[j]:
                    dist[j], label[j], par[j] = d + 1, label[i], i
                    heapq.heappush(h, (d + 1, j))
        adj = {}
        for i in alive:
            for j in G.nb[i][:2]:
                if j >= 0 and label[i] != label[j] and label[i] >= 0 and label[j] >= 0:
                    a, b = (i, j) if label[i] < label[j] else (j, i)
                    key = (int(label[a]), int(label[b]))
                    c = dist[a] + dist[b]
                    if key not in adj or c < adj[key][0]:
                        adj[key] = (c, a, b)
        links = set()

        def chain(s):
            out = [s]
            while par[out[-1]] >= 0:
                out.append(par[out[-1]])
            return out
        added = 0
        for (la, lb), (_c, a, b) in adj.items():
            A, B = seeds[la], seeds[lb]
            if self.los(A, B):
                links.add((min(la, lb), max(la, lb)))
                continue
            path = chain(a)[::-1] + chain(b)          # A ... a, b ... B
            # string-pull: from each stop, the farthest point on the path it sees
            stops = [0]
            while stops[-1] < len(path) - 1:
                cur = stops[-1]
                nxt = cur + 1
                for k in range(len(path) - 1, cur + 1, -1):
                    if self.los(path[cur], path[k]):
                        nxt = k
                        break
                stops.append(nxt)
            ids = []
            for st in stops:
                s = path[st]
                if s == A:
                    ids.append(la)
                elif s == B:
                    ids.append(lb)
                else:
                    seeds.append(s)
                    ids.append(len(seeds) - 1)
                    added += 1
            for u, v in zip(ids, ids[1:]):
                links.add((min(u, v), max(u, v)))
        # shortcuts within LINK_REACH
        pos = np.array([[(G.gx[s] + 0.5) * RES, (G.gy[s] + 0.5) * RES, G.z[s]] for s in seeds])
        deg = Counter()
        for u, v in links:
            deg[u] += 1
            deg[v] += 1
        bucket = defaultdict(list)
        for k, p in enumerate(pos):
            bucket[(int(p[0] // LINK_REACH), int(p[1] // LINK_REACH))].append(k)
        for k, p in enumerate(pos):
            bx, by = int(p[0] // LINK_REACH), int(p[1] // LINK_REACH)
            cand = [j for x in (bx - 1, bx, bx + 1) for y in (by - 1, by, by + 1) for j in bucket.get((x, y), ())
                    if j > k and (k, j) not in links]
            cand.sort(key=lambda j: np.linalg.norm(pos[j] - p))
            for j in cand:
                if deg[k] >= LINK_MAX:
                    break
                if np.linalg.norm(pos[j] - p) > LINK_REACH or deg[j] >= LINK_MAX:
                    continue
                if self.los(seeds[k], seeds[j]):
                    links.add((k, j))
                    deg[k] += 1
                    deg[j] += 1
        # waypoint objects
        for k, s in enumerate(seeds):
            t = self.sample_tri[s]
            self.wps.append(w.add_wp(t.piece.mgr, t, pos[k]))
        far = [(u, v) for u, v in links if not w.near(self.wps[u].mgr.cell, self.wps[v].mgr.cell)]
        if far:
            raise RuntimeError(f"{len(far)} waypoint links span more than one cell")
        for u, v in sorted(links):
            c = w.cost(float(np.linalg.norm(pos[u] - pos[v])))
            self.wps[u].links.append((c, self.wps[v]))
            self.wps[v].links.append((c, self.wps[u]))
        self.wp_sample, self.wp_pos, self.label = seeds, pos, label
        self.report["waypoints"] = len(self.wps)
        self.report["waypoints added at corners"] = added
        self.report["waypoint links"] = len(links)
        # across the region's edge: stock ground waypoints near it, linked to new ones they see
        x0, y0, x1, y1 = self.box
        stock = []
        for g in w.managers.values():
            if g.cell in self.cellbox:
                continue
            for W in g.wps:
                if W.flowcode or W.tri is None:
                    continue
                q = np.array(w.wp_pos(W))
                if x0 - LINK_REACH <= q[0] <= x1 + LINK_REACH and y0 - LINK_REACH <= q[1] <= y1 + LINK_REACH:
                    stock.append((W, q))
        self.stock_wps = stock
        n_x = 0
        for W, q in stock:
            m = 0
            for k in sorted(range(len(self.wps)), key=lambda k: np.linalg.norm(pos[k][:2] - q[:2])):
                d = float(np.linalg.norm(pos[k] - q))
                if d > LINK_REACH or m >= 4 or len(W.links) >= 250:
                    break
                if w.near(W.mgr.cell, self.wps[k].mgr.cell) and self.los_out(seeds[k], q):
                    c = w.cost(d)
                    W.links.append((c, self.wps[k]))
                    self.wps[k].links.append((c, W))
                    m += 1
                    n_x += 1
        self.report["links to stock waypoints"] = n_x

    def lists(self):
        """Each new triangle's waypoint list: the nearest waypoints its centre's square sees, at most LIST_MAX; stock
        triangles near the region left with fewer than 2 get new ones they see."""
        G, w = self.G, self.w
        pos, seeds = self.wp_pos, self.wp_sample
        bucket = defaultdict(list)
        for k, p in enumerate(pos):
            bucket[(int(p[0] // LIST_REACH), int(p[1] // LIST_REACH))].append(k)
        empty = 0
        for piece in self.pieces:
            sq = self.piece_sq[piece._key]
            for ti, t in enumerate(piece.tris):
                c = np.array(piece.corners(ti)).mean(0)
                home = sq.get((int(math.floor(c[0] / RES)), int(math.floor(c[1] / RES))))
                if home is None:
                    home = min(sq.values(), key=lambda i: (G.gx[i] + 0.5 - c[0] / RES) ** 2
                               + (G.gy[i] + 0.5 - c[1] / RES) ** 2)
                bx, by = int(c[0] // LIST_REACH), int(c[1] // LIST_REACH)
                cand = [k for x in (bx - 1, bx, bx + 1) for y in (by - 1, by, by + 1) for k in bucket.get((x, y), ())]
                cand.sort(key=lambda k: np.linalg.norm(pos[k] - c))
                out = []
                for k in cand:
                    if len(out) >= LIST_MAX or np.linalg.norm(pos[k] - c) > LIST_REACH:
                        break
                    if w.near(t.piece.mgr.cell, self.wps[k].mgr.cell) and self.los(home, seeds[k]):
                        out.append(self.wps[k])
                if len(out) < LIST_MAX:
                    for W, sp in sorted(self.stock_wps, key=lambda x: np.linalg.norm(x[1] - c)):
                        if len(out) >= LIST_MAX or np.linalg.norm(sp - c) > LIST_REACH / 2:
                            break
                        if w.near(t.piece.mgr.cell, W.mgr.cell) and self.los_out(home, sp):
                            out.append(W)
                if not out:
                    if self.label[home] >= 0 and w.near(t.piece.mgr.cell, self.wps[self.label[home]].mgr.cell):
                        out.append(self.wps[self.label[home]])
                    empty += 1
                t.wps = out
        self.report["triangles seeing no waypoint (own region's taken)"] = empty
        fixed = 0
        for g in w.managers.values():
            if g.cell in self.cellbox:
                continue
            for p in g.pieces:
                for ti, t in enumerate(p.tris):
                    if len(t.wps) >= 2:
                        continue
                    c = np.array(p.corners(ti)).mean(0)
                    if not (self.box[0] - LIST_REACH <= c[0] <= self.box[2] + LIST_REACH and
                            self.box[1] - LIST_REACH <= c[1] <= self.box[3] + LIST_REACH):
                        continue
                    for k in sorted(range(len(self.wps)), key=lambda k: np.linalg.norm(pos[k] - c))[:6]:
                        if np.linalg.norm(pos[k] - c) > LIST_REACH:
                            break
                        if self.wps[k] not in t.wps and w.near(g.cell, self.wps[k].mgr.cell) and                                 self.los_out(seeds[k], c):
                            t.wps.append(self.wps[k])
                            fixed += 1
        self.report["stock triangles given new waypoints"] = fixed

    # ---- 5: crowd routes
    def anchor(self, p):
        """-> (triangle, z, region sample or None) under world point p (new mesh or stock), or None."""
        G = self.G
        if self.in_region(p[0], p[1]):
            k = G.key(int(math.floor(p[0] / RES)), int(math.floor(p[1] / RES)))
            best = None
            for i in G.at.get(k, ()):
                if self.alive[i] and abs(G.z[i] - p[2]) <= 1.5 and \
                        (best is None or abs(G.z[i] - p[2]) < abs(G.z[best] - p[2])):
                    best = i
            return (self.sample_tri[best], G.z[best], best) if best is not None else None
        hit = self.stock.locate(p, dz=1.5)
        return (hit[0], hit[1], None) if hit else None

    def flows(self):
        w, nav = self.w, self.w.nav
        x0, y0, x1, y1 = self.box
        R = TIE_REACH
        touched = blocked = 0
        self.blocked_flows = []
        self.blocked_uids = set()
        self.rerouted = []
        self.shortened = []
        self.narrowed = []
        for F in w.flows.values():
            near_box = any(x0 - R <= p[0] <= x1 + R and y0 - R <= p[1] <= y1 + R for p in F.points)
            if not near_box and all(t is not None for t in F.tris):
                continue
            touched += 1
            anchors = [self.anchor(p) for p in F.points]
            if self.new is not None and isinstance(F.link, navmodel.NLink) and \
                    (any(a is None for a in anchors) or self._crosses_new(F.points, anchors)):
                done = self._reroute(F, anchors)
                if done is not None:
                    anchors = done
            bad = any(a is None for a in anchors) or self._crosses_new(F.points, anchors)
            if not bad and self.new is not None and self._narrow(F, anchors) is not None:
                self.narrowed.append(F.name or hex(F.uid))
            new_tris = []
            for p, a, old in zip(F.points, anchors, F.tris):
                if a is not None:
                    new_tris.append(a[0])
                elif isinstance(old, NTri):
                    new_tris.append(old)
                else:
                    new_tris.append(self._nearest_tri(p))
            F.tris = new_tris
            L = F.link
            if not isinstance(L, navmodel.NLink):
                continue
            for g in w.managers.values():
                for W in g.wps:
                    if not W.flowcode and W.links and any(x is L for _r, x in W.links):
                        W.links = [(r, x) for r, x in W.links if x is not L]
            uids = [u for u, _t in L.tris] + [w.new_uid() for _ in range(max(0, len(new_tris) - len(L.tris)))]
            L.tris = [(u, t) for u, t in zip(uids, new_tris)]
            L.next_start = 0
            objwp = []
            for j, (ou, wu, fw, _near) in enumerate(L.objwp):
                near = [] if bad or j >= len(anchors) else                     [W for W in self._ties(F.points[j], anchors[j]) if w.near(W.mgr.cell, L.mgr.cell)]
                for W in near:
                    W.links.append((nav.WP_METALINK, L))
                objwp.append((ou, wu, fw, near))
            L.objwp = objwp
            if bad:
                blocked += 1
                self.blocked_flows.append(F.name or hex(F.uid))
                self.blocked_uids.add(F.uid)
        for u in self.blocked_uids:                     # off for the crowds too (an untied route is still walked)
            w.set_flow_active(w.flows[u], False)
            self._drop_joins(w.flows[u])
        self.report["flows re-anchored"] = touched
        self.report["flows blocked (through new geometry: no ties)"] = blocked
        self.report["flows rerouted round new geometry"] = len(self.rerouted)
        self.report["flows shortened (an end inside new geometry dropped)"] = len(self.shortened)
        self.report["flows narrowed by new geometry"] = len(self.narrowed)

    # ---- 5b: routes through new geometry, rerouted round it
    def _clearance(self):
        """Metres from each region sample to the nearest square with a missing link (a wall, a drop)."""
        if getattr(self, "_clear", None) is None:
            G = self.G
            c = np.full(G.n, np.inf)
            dq = [int(i) for i in np.nonzero(self.alive)[0] if (G.nb[i] < 0).any()]
            for i in dq:
                c[i] = 0
            k = 0
            while k < len(dq):
                i = dq[k]
                k += 1
                for j in G.nb[i]:
                    if j >= 0 and c[j] > c[i] + 1:
                        c[j] = c[i] + 1
                        dq.append(int(j))
            self._clear = c * RES
        return self._clear

    def _free_spot(self, p, reach=None, ok=None):
        """The region sample nearest world point p (within `reach`, about its height) that new geometry leaves free
        (or that passes ok(world point))."""
        G = self.G
        reach = REROUTE_MOVE if reach is None else reach
        gx, gy = int(math.floor(p[0] / RES)), int(math.floor(p[1] / RES))
        r = int(math.ceil(reach / RES))
        best, bd = None, reach
        for x in range(gx - r, gx + r + 1):
            for y in range(gy - r, gy + r + 1):
                if not (0 <= x - G.g0[0] < G.nx and 0 <= y - G.g0[1] < G.ny):
                    continue
                for i in G.at.get(G.key(x, y), ()):
                    if not self.alive[i] or abs(G.z[i] - p[2]) > 1.5:
                        continue
                    q = np.array([(G.gx[i] + 0.5) * RES, (G.gy[i] + 0.5) * RES, G.z[i]])
                    d = math.dist(q[:2], p[:2])
                    if d < bd and (ok(q) if ok is not None else not self.new.blocks_spot(q)):
                        best, bd = i, d
        return best

    def _path(self, a, b):
        """A walk over the region's squares from sample a to sample b, kept off walls where it can (A*, each step
        dearer within REROUTE_CLEAR of a wall), string-pulled with shortcuts no nearer the walls than the walk (at most
        REROUTE_CLEAR / 2 asked) -> the samples of its corners (a ... b), or None."""
        G = self.G
        clear = self._clearance()
        bx, by = float(G.gx[b]), float(G.gy[b])
        straight = math.hypot(G.gx[a] - bx, G.gy[a] - by)
        limit = straight * REROUTE_DETOUR + REROUTE_EXTRA / RES
        h = lambda i: math.hypot(G.gx[i] - bx, G.gy[i] - by)
        best, prev = {a: 0.0}, {}
        heap = [(h(a), 0.0, a)]
        n = 0
        while heap and n < 200000:
            _f, d, i = heapq.heappop(heap)
            if i == b:
                break
            if d > best.get(i, math.inf) or d > limit * 3:
                continue
            n += 1
            for j in G.nb[i]:
                j = int(j)
                if j < 0 or not self.alive[j]:
                    continue
                nd = d + 1.0 + 1.5 * max(0.0, REROUTE_CLEAR - clear[j]) / RES
                if nd < best.get(j, math.inf):
                    best[j] = nd
                    prev[j] = i
                    heapq.heappush(heap, (nd + h(j), nd, j))
        if b not in prev and a != b:
            return None
        path = [b]
        while path[-1] != a:
            path.append(prev[path[-1]])
        path = path[::-1]
        if len(path) > limit:
            return None
        pc = [float(clear[i]) for i in path]
        stops = [0]
        while stops[-1] < len(path) - 1:
            cur = stops[-1]
            nxt = cur + 1
            for k in range(len(path) - 1, cur + 1, -1):
                # a shortcut no nearer the walls than the walk it replaces (or REROUTE_CLEAR / 2)
                need = min(REROUTE_CLEAR / 2, min(pc[cur + 1:k]) if k > cur + 1 else 0.0)
                if self.los(path[cur], path[k], ok=lambda i: clear[i] >= need - 1e-9):
                    nxt = k
                    break
            stops.append(nxt)
        return [path[k] for k in stops]

    def _near_new(self, q, r=None):
        """Is world point q inside new geometry, or within r (REROUTE_NEAR) of it at a person's height?"""
        r = REROUTE_NEAR if r is None else r
        q = np.asarray(q, np.float64)
        return self.new.blocks_spot(q) or any(
            self.new.blocks_spot(q + (r * math.cos(t), r * math.sin(t), 0.0)) for t in np.arange(8) * math.pi / 4)

    def _reroute(self, F, anchors):
        """Route F runs through new geometry: an inner point inside it or within REROUTE_NEAR of it is dropped, an end
        point there moves to the nearest square clear of it (within REROUTE_MOVE: its joins stay) - or, with none,
        is dropped (the route shortened, its joins at that end dropped both ways: e.g. a route beginning inside a new
        house) - and each stretch through it gets the corners of a walk round it as new points; the flow entity is
        rewritten (navmodel.set_flow_points). -> the new points' anchors, or None (left as it was: untied)."""
        G, w = self.G, self.w
        pts, anc, idx = [], [], []
        last = len(F.points) - 1
        cut = set()                                     # ends dropped: 0 (Head), -1 (Tail)
        for j, (p, a) in enumerate(zip(F.points, anchors)):
            p = np.array(p, np.float64)
            on = p if a is None else np.array([p[0], p[1], a[1]])
            if a is None or (a[2] is not None and self._near_new(on)):
                if not self.in_region(p[0], p[1]):
                    return None
                if 0 < j < last:
                    continue                             # an inner point: the walk round replaces it
                i = self._free_spot(on, ok=lambda q: not self._near_new(q))
                if i is None:
                    cut.add(0 if j == 0 else -1)
                    continue
                p = np.array([(G.gx[i] + 0.5) * RES, (G.gy[i] + 0.5) * RES, G.z[i]])
                a = (self.sample_tri[i], float(G.z[i]), i)
            pts.append(p)
            anc.append(a)
            idx.append(j)
        if len(pts) < 2 or (cut and math.dist(pts[0][:2], pts[-1][:2]) < REROUTE_MIN_LEN):
            return None
        new_pts, new_anc, keep = [pts[0]], [anc[0]], [idx[0]]
        for j in range(len(pts) - 1):
            a = np.array([pts[j][0], pts[j][1], anc[j][1]])
            b = np.array([pts[j + 1][0], pts[j + 1][1], anc[j + 1][1]])
            if self._through_new(a, b, anc[j], anc[j + 1]):
                if anc[j][2] is None or anc[j + 1][2] is None:
                    return None                          # a stretch from outside the region: not rerouted here
                corners = self._path(anc[j][2], anc[j + 1][2])
                if corners is None:
                    return None
                for i in corners[1:-1]:
                    q = np.array([(G.gx[i] + 0.5) * RES, (G.gy[i] + 0.5) * RES, G.z[i]])
                    if math.dist(q[:2], new_pts[-1][:2]) < 1.0:
                        continue
                    new_pts.append(q)
                    new_anc.append((self.sample_tri[i], float(G.z[i]), i))
                    keep.append(None)
            new_pts.append(pts[j + 1])
            new_anc.append(anc[j + 1])
            keep.append(idx[j + 1])
        if len(new_pts) > REROUTE_MAX_POINTS:
            return None
        pts3 = [np.array([p[0], p[1], a[1]]) for p, a in zip(new_pts, new_anc)]
        if any(self._through_new(pts3[k], pts3[k + 1], new_anc[k], new_anc[k + 1]) for k in range(len(pts3) - 1)):
            return None
        w.set_flow_points(F, [tuple(float(x) for x in p) for p in new_pts], keep)
        for end in cut:
            self._drop_joins(F, end)
        self.rerouted.append(F.name or hex(F.uid))
        if cut:
            self.shortened.append(F.name or hex(F.uid))
        return new_anc

    def _drop_joins(self, F, end=None):
        """F's joins at `end` (0 Head, -1 Tail; None both) dropped both ways: the joined flows' lists lose F."""
        w = self.w
        for e in ((0, -1) if end is None else (end,)):
            for u in list(F.head if e == 0 else F.tail):
                G = w.flows.get(u)
                if G is not None and G is not F and (F.uid in G.head or F.uid in G.tail):
                    w.set_joins(G, head=[x for x in G.head if x != F.uid], tail=[x for x in G.tail if x != F.uid])
        if end is not None:
            w.set_joins(F, head=[] if end == 0 else None, tail=[] if end == -1 else None)

    def _narrow(self, F, anchors):
        """A route by new geometry (within half its width + 0.5 m) keeps to the room there: FlowWidth cut to twice the
        clearance (to a wall / drop) of the squares under its stretches by it, not under REROUTE_WIDTH_MIN (crowds
        spread across a flow's width: a 4 m route over a 3 m deck walks NPCs off its edge). -> new width or None."""
        r = F.width / 2 + 0.5
        pts = [np.array([p[0], p[1], a[1] if a is not None else p[2]]) for p, a in zip(F.points, anchors)]
        clear = self._clearance()
        least = math.inf
        for a, b in zip(pts, pts[1:]):
            if not self._stretch_near_new(a, b, r, underfoot=True):
                continue
            n = max(2, int(math.dist(a[:2], b[:2]) / RES) + 1)
            for t in np.linspace(0.0, 1.0, n):
                h = self.anchor(a + (b - a) * t)
                if h is not None and h[2] is not None:
                    least = min(least, float(clear[h[2]]))
        if least == math.inf:
            return None
        width = max(REROUTE_WIDTH_MIN, min(F.width, 2 * least))
        if width >= F.width - 0.05:
            return None
        self.w.set_flow_width(F, width)
        return width

    def _crosses_new(self, points, anchors):
        """Does a stretch of a route run through new geometry (`_through_new`)? Crowds on such a route walk into
        the new thing a while before the navmesh takes them round - rerouted (_reroute), else untied."""
        if self.new is None:
            return False
        pts = [np.array([p[0], p[1], a[1] if a is not None else p[2]]) for p, a in zip(points, anchors)]
        return any(self._through_new(pts[k], pts[k + 1], anchors[k], anchors[k + 1]) for k in range(len(pts) - 1))

    def _through_new(self, a, b, A, B):
        """Does the stretch a-b (world points on their ground; anchors A, B) run through new geometry: hitting it
        anywhere in a person's height (WALK_HEIGHTS, every 0.1 m from 0.3 to 1.9 m over its ground; knee / waist / head
        alone miss a rail at 0.5-0.9 m), or no clear walk over the squares with new geometry within REROUTE_NEAR of
        it (what the heights still miss: a new drop, something thin between them - not a stock quirk away from
        anything new)?"""
        if self.new.blocks_walk(a, b, WALK_HEIGHTS):
            return True
        return not self.clear_walk(a, b, A, B) and self._stretch_near_new(a, b)

    def clear_walk(self, a, b, A=None, B=None):
        """A clear walk from world point a to b: over the squares between region samples, los_out from the region to
        stock ground, stock to stock assumed (A, B: their anchors, found when not given)."""
        A = self.anchor(a) if A is None else A
        B = self.anchor(b) if B is None else B
        if A is None or B is None:
            return False
        if A[2] is not None and B[2] is not None:
            return self.los(A[2], B[2])
        if A[2] is not None:
            return self.los_out(A[2], (b[0], b[1], B[1]))
        if B[2] is not None:
            return self.los_out(B[2], (a[0], a[1], A[1]))
        return True

    def _stretch_near_new(self, a, b, r=None, underfoot=False):
        """Does new geometry come within r (REROUTE_NEAR, 2D) of stretch a-b, between its ground and head height (with
        underfoot, from 1 m below its ground: what it stands on counts)?"""
        r = REROUTE_NEAR if r is None else r
        T = self.new
        if not len(T.t):
            return False
        a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
        lo, hi = np.minimum(a, b) - r, np.maximum(a, b) + r
        idx = T.near(lo, hi)
        if not len(idx):
            return False
        t = T.t[idx]
        z0, z1 = min(a[2], b[2]) + (-1.0 if underfoot else 0.1), max(a[2], b[2]) + 1.9
        t = t[(t[:, :, 2].max(1) >= z0) & (t[:, :, 2].min(1) <= z1) &
              (t[:, :, :2].max(1) >= lo[:2]).all(1) & (t[:, :, :2].min(1) <= hi[:2]).all(1)]
        return any(_seg_tri_2d(a[:2], b[:2], x[:, :2]) <= r for x in t)

    def _nearest_tri(self, p):
        best, bd = None, math.inf
        for piece in self.pieces:
            for ti, t in enumerate(piece.tris):
                d = np.linalg.norm(np.array(piece.corners(ti)).mean(0) - p)
                if d < bd:
                    best, bd = t, d
        return best if best is not None else self.stock.tris[0][0]

    def _ties(self, p, anchor):
        """Ground waypoints a flow point at p ties to: seen from it within TIE_REACH, nearest first."""
        p = np.asarray(p, np.float64)
        cands = [(float(np.linalg.norm(self.wp_pos[k] - p)), self.wps[k], self.wp_sample[k], None)
                 for k in range(len(self.wps))]
        cands += [(float(np.linalg.norm(q - p)), W, None, q) for W, q in self.stock_wps]
        cands.sort(key=lambda c: c[0])
        _tri, z, s = anchor
        out = []
        for d, W, smp, q in cands:
            if d > TIE_REACH or len(out) >= TIE_MAX:
                break
            if s is not None:                            # the point is on new ground
                ok = self.los(s, smp) if smp is not None else self.los_out(s, q)
            elif smp is not None:                        # on stock ground, a new waypoint
                ok = self.los_out(smp, (p[0], p[1], z))
            else:                                        # stock to stock: keep what's close
                ok = d < 8.0
            if ok:
                out.append(W)
        return out


def _seg_seg_2d(p, q, r, s):
    """2D distance between segments p-q and r-s."""
    def cross(u, v):
        return u[0] * v[1] - u[1] * v[0]
    d1, d2, w = q - p, s - r, r - p
    den = cross(d1, d2)
    if abs(den) > 1e-12:
        t, u = cross(w, d2) / den, cross(w, d1) / den
        if 0 <= t <= 1 and 0 <= u <= 1:
            return 0.0

    def pt_seg(x, a, b):
        ab = b - a
        L = float(ab @ ab)
        t = 0.0 if L == 0 else min(1.0, max(0.0, float((x - a) @ ab) / L))
        return float(np.linalg.norm(x - (a + t * ab)))
    return min(pt_seg(p, r, s), pt_seg(q, r, s), pt_seg(r, p, q), pt_seg(s, p, q))


def _seg_tri_2d(p, q, tri):
    """2D distance between segment p-q and triangle tri (3, 2): 0 if it touches or lies inside."""
    def inside(x):
        sg = [(tri[(k + 1) % 3][0] - tri[k][0]) * (x[1] - tri[k][1]) -
              (tri[(k + 1) % 3][1] - tri[k][1]) * (x[0] - tri[k][0]) for k in range(3)]
        return min(sg) >= 0 or max(sg) <= 0
    if inside(p) or inside(q):
        return 0.0
    return min(_seg_seg_2d(p, q, tri[k], tri[(k + 1) % 3]) for k in range(3))


def regenerate(src, dest, cells=None, box=None, new=None, flows=None, keep=None, log=print):
    """Regenerate the navmesh of `cells` (or the cells `box` = (x0, y0, x1, y1) touches) of archive `src` from its
    collision; write `dest`. new: world triangles (k, 3, 3) of what was added - a crowd route with a stretch through
    them is untied. keep: world points (x, y, z) - a walkable part under one is kept even when closed in (blend groups'
    NPC slots: a merchant inside his stall). flows: None - the map's crowd routes re-tied (step 5); "generate" - also
    new routes over the
    region's open streets (ac3editor.navflows), checked by A* (every new route walked end to end, every join
    reached). -> report dict (with the library's checks on `dest`)."""
    if flows not in (None, False, "keep", "generate"):
        raise ValueError(f"flows: None or 'generate', not {flows!r}")
    w = navmodel.NavWorld(src)
    if cells is None:
        x0, y0, x1, y1 = box
        xs = list(np.arange(x0, x1, w.size / 2)) + [x1]
        ys = list(np.arange(y0, y1, w.size / 2)) + [y1]
        cells = sorted({w.cell_at(x, y) for x in xs for y in ys} & set(w.managers))
    if not cells:
        import shutil
        shutil.copyfile(src, dest)
        log(f"{dest}: no navmesh cell there - navmesh as it was")
        return dict(cells=[], check_fails=0, problems=[], blocked_flows=[], note="no navmesh cell there")
    md = M.load(src, log=lambda *a: None)
    g = Gen(w, md, cells, log=log)
    g.keep = [tuple(float(v) for v in p[:3]) for p in (keep if keep is not None else ())]
    if new is not None and len(new):
        from . import navcut
        g.new = navcut.Blockers(new)
    dropped = w.drop_pieces([p for c in cells for p in w.managers[c].pieces])
    g.report.update({f"stock {k} dropped": v for k, v in dropped.items()})
    for step in (g.raster, g.components, g.mesh, g.borders, g.waypoints, g.lists, g.flows):
        step()
    made = []
    if flows == "generate":
        from . import navflows
        made, frep = navflows.generate(g, log=log)
        g.report.update(frep)
    w.write(src, dest, log=log)
    nm = w.nav.NavMap(dest)
    res, problems = nm.check()
    rep = dict(g.report, cells=cells, check_fails=sum(v for k, v in res.items() if k.endswith("FAIL")),
               problems=problems[:10], blocked_flows=g.blocked_flows,
               rerouted_flows=getattr(g, "rerouted", []), shortened_flows=getattr(g, "shortened", []),
               narrowed_flows=getattr(g, "narrowed", []))
    if flows == "generate":
        rc, miss = navflows.route_check(nm, [F.uid for F in made])
        rep.update(new_flows=[(F.name, F.uid) for F in made], route_check=dict(rc), route_misses=miss[:20],
                   routes_ok=not miss and rc["walks"] == len(made))
    log(f"{dest}: navmesh regenerated in cells {cells} - " + ", ".join(f"{k} {v}" for k, v in g.report.items()))
    return rep


def regenerate_under(src, dest, tris, margin=2.0, flows=None, keep=None, log=print):
    """Regenerate the navmesh of every cell (with a NavMeshManager) that world triangles `tris` (k, 3, 3) - new or
    moved geometry - touch, `margin` metres round them. -> report dict with ok (the library's checks pass)."""
    tris = np.asarray(tris, np.float64).reshape(-1, 3, 3)
    if not len(tris):
        import shutil
        shutil.copyfile(src, dest)
        return dict(ok=True, cells=[], note="nothing new: navmesh as it was")
    lo, hi = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
    rep = regenerate(src, dest, box=(lo[0] - margin, lo[1] - margin, hi[0] + margin, hi[1] + margin), new=tris,
                     flows=flows, keep=keep, log=log)
    rep["ok"] = rep["check_fails"] == 0 and rep.get("routes_ok", True)
    return rep
