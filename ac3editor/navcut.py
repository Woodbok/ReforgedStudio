"""The navmesh where new geometry stands: crowds stop walking through it (the navmesh under it is isolated).

    navcut.apply(src, dest, blockers)        blockers: world-space solid triangles (k, 3, 3) of what was added

The navmesh reader is forgelib's `maps/navmesh.py` (691 / 691 stock NavMeshManagers round-trip byte for byte;
imported read-only - `AC3_NAVMESH` names another copy of the file).
NPCs path with A* over WayPoints (not triangles) and spawn by a vertical ray against each piece's MOPP. Here, without
renumbering anything or changing any count (one u16 pool of the managers' allocation table has no known formula):

* a navmesh TRIANGLE is isolated when something solid stands in a person's space above it (a ray from 0.3 m to 1.9 m
  over its centre hits a blocker): its edges to its neighbours become walls (0xFFE4) both ways, its three corners one
  vertex (zero area: the spawn ray finds nothing there), its piece's MOPP rebuilt (mopp.build: it finds all
  9,021 of Galata's navmesh triangles) - the table's MOPP byte count follows;
* a WAYPOINT LINK is cut when either end's waypoint lies on an isolated triangle or the straight walk between them
  crosses a blocker at knee, waist or head height: it becomes a link to its own waypoint (A* never expands a node twice,
  so it leads nowhere; the waypoint keeps its link count and the network its size).

CrowdFlows (the routes ambient crowds walk) with a point on an isolated triangle or a segment through a blocker are
disabled and reported (see plan()).
"""
import glob
import importlib.util
import math
import os
import sys
from collections import Counter

import numpy as np

from . import mapdata as M
from . import mopp

BODY = (0.3, 1.9)              # a person's space over a navmesh triangle: something solid here blocks it
CROSS_HEIGHTS = (0.4, 1.0, 1.6)
_NAV = None


def navmesh_module():
    """The forge library's maps/navmesh.py, imported read-only; sys.path left as it was."""
    global _NAV
    if _NAV is not None:
        return _NAV
    cands = [os.environ.get("AC3_NAVMESH", ""), os.path.join(M.FORGE_REPO, "maps", "navmesh.py")]
    wt = glob.glob(os.path.join(M.FORGE_REPO, ".claude", "worktrees", "*", "maps", "navmesh.py"))
    # other checkouts of the forge repo: the aa-acb one first, then the newest
    cands += sorted(wt, key=lambda f: ("aa-acb" not in f, -os.path.getmtime(f)))
    path = next((c for c in cands if c and os.path.exists(c)), None)
    if path is None:
        raise RuntimeError("forgelib's maps/navmesh.py isn't found - set AC3_NAVMESH to it")
    saved = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("ac3forge_navmesh", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
    mod.__source_path__ = path
    _NAV = mod
    return mod


class Blockers:
    """Solid triangles (k, 3, 3), world space, bucketed on a 2D grid for ray / segment tests."""

    def __init__(self, tris, cell=4.0):
        self.t = np.asarray(tris, np.float64).reshape(-1, 3, 3)
        self.cell = cell
        self.grid = {}
        if not len(self.t):
            return
        lo = np.floor(self.t[:, :, :2].min(1) / cell).astype(int)
        hi = np.floor(self.t[:, :, :2].max(1) / cell).astype(int)
        for i, (a, b) in enumerate(zip(lo, hi)):
            for x in range(a[0], b[0] + 1):
                for y in range(a[1], b[1] + 1):
                    self.grid.setdefault((x, y), []).append(i)
        self.lo, self.hi = self.t.reshape(-1, 3).min(0), self.t.reshape(-1, 3).max(0)

    def near(self, p, q):
        """Indices of blocker triangles whose buckets the 2D segment p-q passes."""
        if not len(self.t):
            return np.zeros(0, np.int64)
        a, b = np.floor(np.minimum(p[:2], q[:2]) / self.cell).astype(int), \
            np.floor(np.maximum(p[:2], q[:2]) / self.cell).astype(int)
        out = set()
        for x in range(a[0], b[0] + 1):
            for y in range(a[1], b[1] + 1):
                out.update(self.grid.get((x, y), ()))
        return np.fromiter(out, np.int64)

    def hit(self, p, q):
        """Does the 3D segment p-q cross a blocker triangle (Moller-Trumbore, both faces)?"""
        idx = self.near(p, q)
        if not len(idx):
            return False
        t = self.t[idx]
        d = np.asarray(q, np.float64) - p
        e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        h = np.cross(d, e2)
        a = (e1 * h).sum(1)
        ok = np.abs(a) > 1e-12
        f = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
        s = p - t[:, 0]
        u = f * (s * h).sum(1)
        qv = np.cross(s, e1)
        v = f * (qv * d).sum(1)
        w = f * (e2 * qv).sum(1)
        return bool((ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (w > 1e-6) & (w < 1 - 1e-6)).any())

    def crossings(self, p, q):
        """How many blocker triangles the 3D segment p-q crosses."""
        idx = self.near(p, q)
        if not len(idx):
            return 0
        t = self.t[idx]
        d = np.asarray(q, np.float64) - p
        e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        h = np.cross(d, e2)
        a = (e1 * h).sum(1)
        ok = np.abs(a) > 1e-12
        f = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
        s = p - t[:, 0]
        u = f * (s * h).sum(1)
        qv = np.cross(s, e1)
        v = f * (qv * d).sum(1)
        w = f * (e2 * qv).sum(1)
        return int((ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (w > 1e-6) & (w < 1 - 1e-6)).sum())

    def enclosed(self, c, reach=60.0):
        """Point c inside new geometry: the ray straight up crosses it an odd number of times (under a roof / inside
        a solid) AND walls stand round it at waist height in all four directions (not under an awning or a bridge)."""
        c = np.asarray(c, np.float64)
        if self.crossings(c, c + (0, 0, reach)) % 2 == 0:
            return False
        return all(self.hit(c, c + np.array(d) * reach) for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)))

    def blocks_spot(self, c):
        """Something solid in a person's space over point c, or c inside new geometry."""
        c = np.asarray(c, np.float64)
        return self.hit(c + (0, 0, BODY[0]), c + (0, 0, BODY[1])) or self.enclosed(c + (0, 0, 1.0))

    def blocks_walk(self, p, q, heights=CROSS_HEIGHTS):
        """Something solid across the walk p -> q at any of `heights` over it (navcut: knee / waist / head)."""
        p, q = np.asarray(p, np.float64), np.asarray(q, np.float64)
        return any(self.hit(p + (0, 0, h), q + (0, 0, h)) for h in heights)


def plan(nm, blockers, margin=2.0):
    """-> (isolated {(cell, piece, tri)}, cut {(cell, waypoint, link index)}, flows [(flow name, why)], ties {(cell,
    metalink index)}) for a NavMap. A flow through new geometry is disabled: its waypoints' links cut and its type-5
    link's ties to the ground waypoints (ObjectWayPoints) turned onto the flow's own waypoint."""
    B = blockers if isinstance(blockers, Blockers) else Blockers(blockers)
    if not len(B.t):
        return set(), set(), [], set()
    lo, hi = B.lo - margin, B.hi + margin
    nav = navmesh_module()
    isolated = set()
    for cell, m in nm.managers.items():
        if cell in nm.off_grid or (np.array(m.bv.hi[:2]) < lo[:2]).any() or (np.array(m.bv.lo[:2]) > hi[:2]).any():
            continue
        for pi, p in enumerate(m.pieces):
            if (np.array(p.bv.hi[:2]) < lo[:2]).any() or (np.array(p.bv.lo[:2]) > hi[:2]).any():
                continue
            for ti in range(len(p.tris)):
                c = np.mean(p.corners(ti), axis=0)
                if B.blocks_spot(c):
                    isolated.add((cell, pi, ti))
    pos = {}
    cut = set()
    reach = 48.0                                 # waypoint links run up to a few tens of metres
    for cell, m in nm.managers.items():
        if cell in nm.off_grid or (np.array(m.bv.hi[:2]) < lo[:2] - reach).any() or                 (np.array(m.bv.lo[:2]) > hi[:2] + reach).any():
            continue
        for i, w in enumerate(m.waypoints):
            pos[(cell, i)] = nm.wp_pos(cell, i)

    def near_box(p, q):
        return not ((max(p[0], q[0]) < lo[0]) or (min(p[0], q[0]) > hi[0]) or (max(p[1], q[1]) < lo[1])
                    or (min(p[1], q[1]) > hi[1]))
    dead = {(cell, i) for (cell, i) in pos
            if nm.managers[cell].waypoints[i].w[0] == nav.OWN
            and (cell, nm.managers[cell].waypoints[i].w[1], nm.managers[cell].waypoints[i].w[2]) in isolated}
    for (cell, i), p in pos.items():
        w = nm.managers[cell].waypoints[i]
        for k, (raw, tgt) in enumerate(w.links):
            _c, flags = nav.WayPoint.cost(raw)
            tc = nm.neighbour(cell, tgt & 15)
            if (cell, i) in dead:
                cut.add((cell, i, k))
                continue
            if flags & nav.WP_METALINK:
                # a crossing through a seam / object link: to its triangles' waypoints (as A* goes) - cut when every
                # one of them is dead or behind new geometry
                mm = nm.managers.get(tc)
                if not mm or (tgt >> 4) >= len(mm.metalinks) or mm.metalinks[tgt >> 4].type == 5:
                    continue
                ml = mm.metalinks[tgt >> 4]
                far = [(nm.neighbour(tcell, r & 15), r >> 4) for _u, tt, tp, tcell in ml.tris
                       if tcell in nm.managers and tp < len(nm.managers[tcell].pieces)
                       and tt < len(nm.managers[tcell].pieces[tp].tris)
                       for r in nm.managers[tcell].pieces[tp].tris[tt].waypoints]
                far = [q for q in far if q != (cell, i) and q[0] in nm.managers and q[1] < len(nm.managers[q[0]].waypoints)]
                bad = [q in dead or (near_box(p, pos.get(q) or nm.wp_pos(*q))
                                     and B.blocks_walk(p, pos.get(q) or nm.wp_pos(*q))) for q in far]
                # a seam (type 0) joins neighbouring triangles: cut when none of its far side is still reachable; an
                # object / jump link (1, 6, 7: wide, inferred) when any of it is behind new geometry
                if far and (all(bad) if ml.type == 0 else any(bad)):
                    cut.add((cell, i, k))
                continue
            t = (tc, tgt >> 4)
            if t in dead:
                cut.add((cell, i, k))
            elif tc in nm.managers and t[1] < len(nm.managers[tc].waypoints):
                q = pos.get(t) or nm.wp_pos(*t)
                if near_box(p, q) and B.blocks_walk(p, q):
                    cut.add((cell, i, k))
    flows, blocked_flows = [], []
    for fu, f in nm.flows.items():
        pts = [np.asarray(x, np.float64) for x in f.points]
        if not pts or all(((x[:2] < lo[:2]) | (x[:2] > hi[:2])).any() for x in pts):
            continue
        why = None
        for a, b in zip(pts, pts[1:]):
            if B.blocks_walk(a, b):
                why = "a stretch runs through new geometry"
                break
        for (ti, pi, tc) in (f.tris or []):
            if (tc, pi, ti) in isolated:
                why = "a point stands on an isolated triangle"
        if why:
            flows.append((f.name or fu.hex(), why))
            blocked_flows.append(f)
    # each type-5 link's ties (a flow waypoint <-> the ground waypoints near it) that are dead or cross new geometry
    tie_cut = set()                                  # (cell, metalink, objwp entry, near entry)
    for cell, m in nm.managers.items():
        if cell in nm.off_grid or (np.array(m.bv.hi[:2]) < lo[:2] - reach).any() or                 (np.array(m.bv.lo[:2]) > hi[:2] + reach).any():
            continue
        for k, ml in enumerate(m.metalinks):
            if ml.type != 5:
                continue
            for j, (_ou, _wu, (wi, wc), near) in enumerate(ml.objwp):
                if wc not in nm.managers or wi >= len(nm.managers[wc].waypoints):
                    continue
                a = pos.get((wc, wi)) or nm.wp_pos(wc, wi)
                for n, (ni, nc) in enumerate(near):
                    if nc not in nm.managers or ni >= len(nm.managers[nc].waypoints):
                        continue
                    b = pos.get((nc, ni)) or nm.wp_pos(nc, ni)
                    if (nc, ni) in dead or (near_box(a, b) and B.blocks_walk(a, b)):
                        tie_cut.add((cell, k, j, n))
    # disable the blocked flows: every link of their own waypoints, and their type-5 link's ties
    ties = set()
    for f in blocked_flows:
        for wi, wc in (f.wps or []):
            m = nm.managers.get(wc)
            if m is not None and wi < len(m.waypoints):
                cut.update((wc, wi, k) for k in range(len(m.waypoints[wi].links)))
        if f.metalink and f.metalink[0] != 0xFFFF:
            mc = nm.leaf_cell(f.metalink[1])
            if mc in nm.managers and f.metalink[0] < len(nm.managers[mc].metalinks):
                ties.add((mc, f.metalink[0]))
    return isolated, cut, flows, ties | tie_cut


def _edit_manager(m, isolated, cut, nav, ties=()):
    """Apply the plan to one Manager in place. -> (triangles isolated, links cut)."""
    n_iso = n_cut = 0
    cell = m.cell
    for tie in ties:
        if tie[0] != cell:
            continue
        ml = m.metalinks[tie[1]]
        if len(tie) == 2:                            # a disabled flow: every tie
            ml.objwp = [(ou, wu, wr, [(wr[0], wr[1])] * len(near)) for ou, wu, wr, near in ml.objwp]
        else:                                        # one tie (objwp entry j, near entry n)
            j, n = tie[2], tie[3]
            ou, wu, wr, near = ml.objwp[j]
            near = list(near)
            near[n] = (wr[0], wr[1])
            ml.objwp[j] = (ou, wu, wr, near)
    for pi, p in enumerate(m.pieces):
        mine = sorted(ti for (c, q, ti) in isolated if c == cell and q == pi)
        if not mine:
            continue
        idx = list(p.idx)
        for ti in mine:
            t = p.tris[ti]
            for e in range(3):
                nb = t.nbr[e]
                if nb < 0xFFE0:
                    o = p.tris[nb]
                    o.nbr = [0xFFE4 if x == ti else x for x in o.nbr]
                    t.nbr = [0xFFE4 if k == e else x for k, x in enumerate(t.nbr)]
            idx[3 * ti + 1] = idx[3 * ti + 2] = idx[3 * ti]
            n_iso += 1
        p.idx = idx
        v = np.array([x[:3] for x in p.verts], np.float32)
        code, (ox, oy, oz, sc) = mopp.build(v, np.array(idx, np.int64).reshape(-1, 3))
        reached, _h, _s = mopp.walk(code)
        assert sorted(t for t, _p in reached) == list(range(len(p.tris))), "navmesh MOPP misses triangles"
        p.mopp, p.mopp_os = bytes(code), (ox, oy, oz, sc)
    for i, w in enumerate(m.waypoints):
        ks = [k for (c, wi, k) in cut if c == cell and wi == i]
        if ks:
            links = list(w.links)
            for k in ks:
                raw, _t = links[k]
                links[k] = (raw & ~3, (i << 4) | nav.OWN)        # to itself: a dead end, nothing renumbered
                n_cut += 1
            w.links = links
    # the allocation table's entries we can compute (the MOPP byte count changes with new trees)
    have = m.counts()
    m.table = [(cl, z, k, have.get((cl, k), n)) for cl, z, k, n in m.table]
    return n_iso, n_cut


def apply(src, dest, blockers, log=print):
    """Isolate the navmesh under / cut it along `blockers` (world triangles), writing `dest`. -> report dict."""
    from . import archive
    nav = navmesh_module()
    nm = nav.NavMap(src)
    isolated, cut, flows, ties = plan(nm, blockers)
    totals = Counter()

    def fn(rec_name, o):
        if o["type"] != nav.MANAGER:
            return None
        m = nav.parse_manager(o["body"])
        if not any(c == m.cell for c, _p, _t in isolated) and not any(c == m.cell for c, _w, _k in cut)                 and not any(t[0] == m.cell for t in ties):
            return None
        if (m.cell in nm.off_grid) or nm.records.get(m.cell, (None,))[0] != rec_name:
            return None                              # a ported map's stray manager copy: left alone
        a, b = _edit_manager(m, isolated, cut, nav, ties)
        totals["triangles isolated"] += a
        totals["links cut"] += b
        totals["managers"] += 1
        return m.to_bytes()

    changed, same = archive.rewrite(src, dest, fn, log=lambda *_x: None)
    # read back: every manager parses and writes back identically, its table consistent; the library's own checks
    back = nav.NavMap(dest)
    res, problems = back.check()
    bad_tables = [(c, m.table_check()) for c, m in back.managers.items() if m.table_check()]
    stock_bad = {c for c, m in nm.managers.items() if m.table_check()}
    bad_tables = [x for x in bad_tables if x[0] not in stock_bad]
    fails = res.get("FAIL", 0) - nm.check()[0].get("FAIL", 0)
    report = dict(totals, records=len(changed), other_records_identical=same, check_fails=fails,
                  table_problems=len(bad_tables), flows=flows)
    ok = same and fails <= 0 and not bad_tables and totals["triangles isolated"] == len(isolated) \
        and totals["links cut"] == len(cut)
    report["ok"] = ok
    log(f"{dest}: navmesh - {totals['triangles isolated']} triangles isolated, {totals['links cut']} waypoint links cut "
        f"in {totals['managers']} cell managers; library checks {'OK' if fails <= 0 else 'FAIL'}; allocation tables "
        f"{'OK' if not bad_tables else bad_tables[:2]}; {len(flows)} crowd route(s) through new geometry disabled"
        + (": " + ", ".join(f"{n} ({w})" for n, w in flows[:4]) if flows else ""))
    return report


def world_triangles(parts, instances):
    """Blockers from a scene (scenebuild parts + instances): each instance's collision in world space."""
    out = []
    for inst in instances:
        p = parts[inst["part"]]
        col = p.get("collision")
        if isinstance(col, str):
            continue
        v, t = (col if col is not None else (p["verts"], p["tris"]))
        m = np.asarray(inst["matrix"], np.float64).reshape(4, 4)
        w = np.asarray(v, np.float64) @ m[:3, :3] + m[3, :3]
        out.append(w[np.asarray(t, np.int64)])
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def walk_test(nm, blockers, pairs=40, seed=1):
    """A* around new geometry: waypoint pairs on opposite sides of the blockers' box whose stock path crossed a
    blocker -> how many now go round / find no way through. -> Counter."""
    import random
    B = blockers if isinstance(blockers, Blockers) else Blockers(blockers)
    nm.wp_graph()
    pos = nm.wp_xyz
    c = (B.lo + B.hi) / 2
    r = max(float(np.linalg.norm((B.hi - B.lo)[:2])) / 2 + 3.0, 6.0)
    ring = [q for q, p in pos.items() if r <= math.dist(p[:2], c[:2]) <= r + 8]
    rng = random.Random(seed)
    out = Counter()
    for _ in range(pairs * 5):
        if out["pairs"] >= pairs or len(ring) < 2:
            break
        a, b = rng.sample(ring, 2)
        pa, pb = np.array(pos[a]), np.array(pos[b])
        if np.dot(pa[:2] - c[:2], pb[:2] - c[:2]) > 0:
            continue                                         # same side
        res = nm.astar(a, b)
        if res is None:
            continue
        path = [np.array(pos[q]) for q in res[1]]
        crosses = any(B.blocks_walk(x, y) for x, y in zip(path, path[1:]))
        out["pairs"] += 1
        out["path crosses new geometry"] += crosses
    return out
