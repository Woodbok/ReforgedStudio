"""The crowds' navmesh in Reforged Studio: what the app draws, and the edits it builds.

    nav_data(archive)                     -> the navmesh's edges (by kind), waypoints and links, crowd routes
    apply_areas(src, dest, areas, ...)    -> navmesh areas: "remake" (made again from the collision, navgen) or "block"
                                             (crowds kept out: navcut under an invisible prism over the outline)
    apply_routes(src, dest, routes, ...)  -> crowd routes switched on / off, widened / narrowed (navmodel)

A request's `nav_areas`: [{"name", "poly": [[x, y], ...], "mode": "remake" | "block", "new_routes": bool}] (the
outline's convex hull, as ground regions); `routes`: [{"uid": hex, "active": bool, "width": float}].
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

from ac3editor import navcut, navgen, navmodel, terrain  # noqa: E402

EDGE_KIND = {0xFFE3: 1, 0xFFE2: 2, 0xFFE4: 3}      # 0 inner (two triangles of a piece), 1 seam, 2 drop, 3 wall
BLOCK_BELOW, BLOCK_ABOVE = 1.0, 3.0               # a block area's prism: from 1 m under its ground to 3 m over it


def uid_hex(u):
    if isinstance(u, (bytes, bytearray)):
        return bytes(u).hex()
    return int(u).to_bytes(4, "little").hex()


def _tri_area(c):
    a, b, d = (np.asarray(x[:3], np.float64) for x in c)
    return float(np.linalg.norm(np.cross(b - a, d - a))) / 2


def nav_data(archive):
    """-> dict(edges=[[x0, y0, z0, x1, y1, z1, kind]], wps=[[x, y, z]], links=[[i, j]], flows=[{uid, name, width,
    active, points, head, tail}], cells, triangles): every navmesh triangle edge once (zero-area triangles - isolated by
    navcut - left out), every waypoint and ground link once, every crowd route."""
    w = navmodel.NavWorld(archive)
    edges, seen, ntri = [], {}, 0
    for g in w.managers.values():
        for q in g.pieces:
            for t, T in enumerate(q.tris):
                c = q.corners(t)
                if _tri_area(c) < 1e-6:
                    continue
                ntri += 1
                for k in range(3):
                    a, b = c[k], c[(k + 1) % 3]
                    key = tuple(sorted((tuple(np.round(a, 2)), tuple(np.round(b, 2)))))
                    kind = EDGE_KIND.get(T.nbr[k], 0) if isinstance(T.nbr[k], int) else 0
                    if key in seen:
                        i = seen[key]
                        edges[i][6] = max(edges[i][6], kind)
                        continue
                    seen[key] = len(edges)
                    edges.append([round(float(v), 2) for v in (*a[:3], *b[:3])] + [kind])
    wps, index = [], {}
    for g in w.managers.values():
        for W in g.wps:
            if W.flowcode:
                continue
            index[id(W)] = len(wps)
            wps.append([round(float(v), 2) for v in w.wp_pos(W)])
    links = set()
    for g in w.managers.values():
        for W in g.wps:
            i = index.get(id(W))
            if i is None:
                continue
            for _raw, tgt in W.links:
                j = index.get(id(tgt))
                if j is not None and j != i:
                    links.add((min(i, j), max(i, j)))
    flows = []
    for u, F in w.flows.items():
        flows.append(dict(uid=uid_hex(u), name=F.name or "", width=round(float(F.width), 3),
                          active=bool(w.flow_active(F)), points=[[round(float(v), 2) for v in p] for p in F.points],
                          head=[uid_hex(h) for h in F.head], tail=[uid_hex(t) for t in F.tail]))
    return dict(edges=edges, wps=wps, links=sorted(links), flows=flows, cells=len(w.managers), triangles=ntri)


def block_prism(poly, z):
    """An invisible prism over a convex outline, from BLOCK_BELOW under z to BLOCK_ABOVE over it -> world triangles
    (k, 3, 3), its sides and top (navcut's blockers: what stands in a person's space)."""
    poly = terrain.hull(np.asarray(poly, np.float64))
    lo, hi = z - BLOCK_BELOW, z + BLOCK_ABOVE
    tris = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        tris += [((a[0], a[1], lo), (b[0], b[1], lo), (b[0], b[1], hi)), ((a[0], a[1], lo), (b[0], b[1], hi), (a[0], a[1], hi))]
    for i in range(1, n - 1):
        tris.append(((poly[0][0], poly[0][1], hi), (poly[i][0], poly[i][1], hi), (poly[i + 1][0], poly[i + 1][1], hi)))
    return np.array(tris, np.float64)


def apply_areas(src, dest, areas, level, keep=None, log=print):
    """Navmesh areas, one after another (each on the previous one's output) -> (report lines, warnings, blocked
    route names). level(poly) -> the area's ground height (plan.region_level). keep: world points whose walkable part
    a remade area keeps even when closed in (blend groups' NPC slots)."""
    report, warnings, blocked = [], [], []
    step = src
    for k, a in enumerate(areas):
        poly = terrain.hull(np.asarray(a["poly"], np.float64).reshape(-1, 2))
        out = dest if k == len(areas) - 1 else f"{dest}.nav{k}"
        name = a.get("name") or f"navmesh area {k + 1}"
        if a.get("mode") == "block":
            z = level(poly)
            if z is None:
                raise RuntimeError(f"{name}: no ground under it to keep crowds off")
            rep = navcut.apply(step, out, block_prism(poly, z), log=log)
            if not rep.get("ok"):
                raise RuntimeError(f"{name}: the navmesh cut didn't read back cleanly (see the log)")
            report.append(f"{name}: crowds kept out - {rep.get('triangles isolated', 0)} navmesh triangles isolated, "
                          f"{rep.get('links cut', 0)} waypoint links cut")
            for fname, _w in rep.get("flows", []):
                blocked.append(fname)
        else:
            lo, hi = poly.min(0), poly.max(0)
            flows = "generate" if a.get("new_routes") else None
            rep = navgen.regenerate(step, out, box=(lo[0], lo[1], hi[0], hi[1]), flows=flows, keep=keep, log=log)
            if rep.get("check_fails"):
                raise RuntimeError(f"{name}: the remade navmesh didn't pass its checks: {rep.get('problems')}")
            if flows and not rep.get("routes_ok", True):
                warnings.append(f"{name}: new crowd routes failed their walk check: {rep.get('route_misses', [])[:3]}")
            report.append(f"{name}: navmesh made again from the collision in cells {rep.get('cells')} (checks failed: 0)"
                          + (f", {len(rep.get('new_flows', []))} new crowd route(s)" if flows else ""))
            blocked += [b if isinstance(b, str) else str(b) for b in rep.get("blocked_flows", [])]
        if step != src and os.path.exists(step):
            os.remove(step)
        step = out
    if blocked:
        warnings.append(f"{len(blocked)} crowd route(s) through the navmesh areas switched off: " + ", ".join(
            str(b) for b in blocked[:5]))
    return report, warnings


def apply_routes(src, dest, routes, log=print):
    """Crowd routes switched on / off and widened / narrowed -> report lines. Unknown uids are an error."""
    w = navmodel.NavWorld(src)
    by_hex = {uid_hex(u): F for u, F in w.flows.items()}
    lines = []
    for r in routes:
        F = by_hex.get(r["uid"])
        if F is None:
            raise RuntimeError(f"crowd route {r['uid']}: not on this map")
        what = []
        if "active" in r and bool(w.flow_active(F)) != bool(r["active"]):
            w.set_flow_active(F, bool(r["active"]))
            what.append("on" if r["active"] else "off")
        if "width" in r and abs(float(F.width) - float(r["width"])) > 1e-3:
            w.set_flow_width(F, float(r["width"]))
            what.append(f"width {float(r['width']):.2f} m")
        if what:
            lines.append(f"crowd route {F.name or r['uid']}: " + ", ".join(what))
    w.write(src, dest, log=log)
    return lines


def check_routes(dest, routes):
    """-> (ok, lines): every route edit read back."""
    w = navmodel.NavWorld(dest)
    by_hex = {uid_hex(u): F for u, F in w.flows.items()}
    bad = []
    for r in routes:
        F = by_hex.get(r["uid"])
        if F is None or ("active" in r and bool(w.flow_active(F)) != bool(r["active"])) or \
                ("width" in r and abs(float(F.width) - float(r["width"])) > 1e-2):
            bad.append(r["uid"])
    if bad:
        return False, [f"FAIL: {len(bad)} crowd route edit(s) not in the build: {', '.join(bad[:4])}"]
    return True, [f"ok: {len(routes)} crowd route edit(s) in the build"] if routes else []
