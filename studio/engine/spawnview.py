"""Spawn points per game mode and out-of-bounds boundaries in Reforged Studio, on ac3editor.spawns / boundary.

    boundary_data(archive)        -> every out-of-bounds area and gameplay zone of a map for the app (corners, modes)
    placed_of(req_spawns, sinfo)  -> the request's spawn edits as spawns.plan's (placed, added)
    with_boundaries(areas, b)     -> boundary.areas with the request's new corners
    check(archive, req)           -> (errors, warnings): spawns.problems on what every mode keeps, the new boundaries
    verify(src, dest, req)        -> (ok, lines): the read-back

The request's keys:
    "spawns": {"move":   [{"uid": hex, "record": "DataBlock_ACFE_<mode>", "matrix": [16]}],
               "remove": [{"uid": hex, "record": ...}],
               "add":    [{"template": hex, "records": [...], "matrix": [16]}]}
A spawn appears in the app once per mode block that uses it (the scene cache's spawn markers); moving or removing it
there changes it in that mode only - spawns.plan splits a spawn shared by several modes (a fresh-id copy takes the
changed modes). An added spawn is a copy of one of the mode's own (its kind and team: respawn / match start, team 1 /
2): match starts are copied as raw bytes with their intro camera kit (spawns.start_copy).
    "boundaries": {area uid hex: [[x, y], ...]}      an out-of-bounds area's new corners (world x, y; closed or an open
                                                      chain, as the area was) - boundary.rebuild writes every copy
Gameplay zones (Domination areas, Artifact Assault territories) are drawn, not edited here: a Domination zone follows
its point (gameplayview), territories are cut by their divider (the Blender panel's territories.py).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

from ac3editor import boundary, spawns  # noqa: E402

CLEAR = 3.0          # a spawn closer than this to a new boundary (and closer than before) is refused (blender_edit)


def m4(m):
    return np.asarray(m, np.float64).reshape(4, 4)


def boundary_data(archive):
    """-> [dict(uid, name, kind (oob / zone), records, modes, closed, corners [[x, y]], polygons, z (the entity's
    height), zlo, zhi)]."""
    out = []
    for uid, a in boundary.areas(os.path.abspath(archive)).items():
        out.append(dict(uid=uid.hex(), name=a["name"], kind=a["kind"], records=list(a["records"]),
                        modes=[spawns.mode_label(r) for r in a["records"]], closed=bool(a["closed"]),
                        corners=np.round(np.asarray(a["corners"], np.float64), 3).tolist(),
                        polygons=[np.round(np.asarray(p, np.float64), 3).tolist() for p in a.get("polygons", [])],
                        z=float(a["matrix"][3, 2]), zlo=float(a.get("zlo", -30.0)), zhi=float(a.get("zhi", 30.0))))
    out.sort(key=lambda r: (r["kind"], r["name"]))
    return out


def placed_of(req_spawns, sinfo):
    """-> (placed {uid: {record: 4x4 upright or None}}, added [(template uid, [records], 4x4)]); raises ValueError for
    a spawn / mode this map hasn't."""
    s = req_spawns or {}
    placed, added = {}, []

    def spawn(h):
        u = bytes.fromhex(h)
        if u not in sinfo["spawns"]:
            raise ValueError(f"no spawn point {h} on this map")
        return u
    for mv in s.get("move", []):
        u = spawn(mv["uid"])
        placed.setdefault(u, {})[mv["record"]] = spawns.level(m4(mv["matrix"]))
    for rm in s.get("remove", []):
        u = spawn(rm["uid"])
        placed.setdefault(u, {})[rm["record"]] = None
    for ad in s.get("add", []):
        u = spawn(ad["template"])
        added.append((u, list(ad["records"]), spawns.level(m4(ad["matrix"]))))
    return placed, added


def with_boundaries(areas, b):
    """boundary.areas with the request's new corners (uid hex -> corners) in place of the map's."""
    out = dict(areas)
    for h, corners in (b or {}).items():
        u = bytes.fromhex(h)
        if u in out:
            out[u] = dict(out[u], corners=np.asarray(corners, np.float64), polygons=[np.asarray(corners, np.float64)])
    return out


def _crosses(p, q, a, b):
    def side(o, x, y):
        return (x[0] - o[0]) * (y[1] - o[1]) - (x[1] - o[1]) * (y[0] - o[0])
    return side(p, q, a) * side(p, q, b) < 0 and side(a, b, p) * side(a, b, q) < 0


def boundary_problems(areas, b, final=None):
    """-> (errors, warnings) for the request's new boundaries: enough corners, no edge crossing another, and every
    spawn its modes keep (spawns.plan's `final`, else the map's) inside and clear of the walls."""
    errors, warnings = [], []
    for h, corners in (b or {}).items():
        u = bytes.fromhex(h)
        a = areas.get(u)
        if a is None:
            errors.append(f"{h}: not an out-of-bounds area of this map")
            continue
        if a["kind"] != "oob":
            errors.append(f"{a['name']}: a gameplay zone - not edited here (a Domination zone follows its point)")
            continue
        c = np.asarray(corners, np.float64)
        closed = a["closed"]
        if len(c) < (3 if closed else 2):
            errors.append(f"{a['name']}: {len(c)} corner(s) - a boundary needs at least {3 if closed else 2}")
            continue
        edges = [(c[i], c[(i + 1) % len(c)]) for i in range(len(c) if closed else len(c) - 1)]
        cross = sum(1 for i in range(len(edges)) for j in range(i + 2, len(edges))
                    if not (closed and i == 0 and j == len(edges) - 1) and _crosses(*edges[i], *edges[j]))
        if cross:
            errors.append(f"{a['name']}: its edges cross ({cross}x) - the walls would fold over")
        if closed and abs(boundary.signed_area(c)) < 100:
            errors.append(f"{a['name']}: under 100 m2 inside")
        for r in a["records"]:
            here = (final or {}).get(r)
            if here is None:
                continue
            for k, (kind, m) in here.items():
                p = m4(m)[3, :2]
                ok, d = boundary.inside_area(p, c, closed, a["ref"])
                lab = f"{spawns.mode_label(r)}: a {spawns.kind_label(kind)} at ({p[0]:.1f}, {p[1]:.1f})"
                if not ok:
                    errors.append(f"{lab} would be out of bounds ({a['name']}) - players spawning there die at once")
                elif d < CLEAR:
                    _ok0, d0 = boundary.inside_area(p, a["corners"], closed, a["ref"])
                    if d < d0 - 0.01:
                        errors.append(f"{lab} would be only {d:.1f} m from the boundary (it was {d0:.1f})")
    return errors, warnings


def check(archive, req, sinfo=None, areas=None, pairs=None, floor=None):
    """-> (errors, warnings, changes, final): the request's spawn edits (spawns.plan / problems) and boundaries."""
    sinfo = sinfo or spawns.read(archive)
    areas = areas if areas is not None else boundary.areas(archive)
    errors, warnings = [], []
    changes = final = None
    try:
        placed, added = placed_of(req.get("spawns"), sinfo)
    except ValueError as e:
        return [str(e)], [], None, None
    new_areas = with_boundaries(areas, req.get("boundaries"))
    if placed or added:
        try:
            changes, final = spawns.plan(sinfo, placed, added)
        except ValueError as e:
            return [str(e)], [], None, None
        changed = set(changes["move"]) | {("new", k) for k in range(len(changes["copies"]))}
        e1, w1 = spawns.problems(sinfo, final, areas=new_areas, pairs=pairs, floor=floor, changed=changed)
        errors += e1
        warnings += w1
    if req.get("boundaries"):
        if final is None:
            final = {r: {u: (sp.kind, sp.matrix) for u, sp in here.items()} for r, here in spawns.active(sinfo).items()}
        e2, w2 = boundary_problems(areas, req["boundaries"], final)
        errors += e2
        warnings += w2
    return errors, warnings, changes, final


def verify(src, dest, req):
    """The read-back: every spawn edit in the modes it was made in (moved ones at their place, removed ones no longer
    created there, added ones present), the new boundaries' corners (5 cm). -> (ok, lines)."""
    ok, lines = True, []
    s = req.get("spawns") or {}
    if s:
        before = spawns.read(src)
        after = spawns.read(dest)
        act = spawns.active(after)
        bad = []
        for mv in s.get("move", []):
            want = spawns.level(m4(mv["matrix"]))
            if not any(np.allclose(sp.matrix, want, atol=2e-3) for sp in act.get(mv["record"], {}).values()):
                bad.append(f"moved {mv['uid']} not at its place in {spawns.mode_label(mv['record'])}")
        for rm in s.get("remove", []):
            u = bytes.fromhex(rm["uid"])
            m0 = before["spawns"][u].matrix
            if u in act.get(rm["record"], {}) or any(np.allclose(sp.matrix, m0, atol=2e-3)
                                                     for sp in act.get(rm["record"], {}).values()):
                bad.append(f"removed {rm['uid']} still in {spawns.mode_label(rm['record'])}")
        for ad in s.get("add", []):
            want = spawns.level(m4(ad["matrix"]))
            kind = before["spawns"][bytes.fromhex(ad["template"])].kind
            for r in ad["records"]:
                if not any(sp.kind == kind and np.allclose(sp.matrix, want, atol=2e-3) for sp in act.get(r, {}).values()):
                    bad.append(f"added {spawns.kind_label(kind)} not in {spawns.mode_label(r)}")
        if bad:
            ok = False
            lines.append(f"FAIL: spawn points: {len(bad)} edit(s) not read back: " + "; ".join(bad[:4]))
        else:
            lines.append(f"ok: spawn points read back - {len(s.get('move', []))} moved, {len(s.get('remove', []))} "
                         f"removed, {len(s.get('add', []))} added, each in its mode")
    b = req.get("boundaries") or {}
    if b:
        got = boundary.areas(dest)
        bad = []
        for h, corners in b.items():
            a = got.get(bytes.fromhex(h))
            c = np.asarray(corners, np.float64)
            if a is None:
                bad.append(h)
                continue
            g = np.asarray(a["corners"], np.float64)
            near = len(g) == len(c) and all(np.min(np.linalg.norm(g - p, axis=1)) < 0.05 for p in c)
            if not near:
                bad.append(f"{a['name']} ({len(g)} corners, want {len(c)})")
        if bad:
            ok = False
            lines.append(f"FAIL: boundaries not as drawn: {', '.join(bad)}")
        else:
            lines.append(f"ok: {len(b)} boundary(ies) read back with their new corners")
    return ok, lines
