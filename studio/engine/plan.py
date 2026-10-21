"""Reforged Studio's edits -> a test map archive, without Blender.

The same passes and rules as the Blender panel's Apply (ac3editor.blender_edit.apply_edits) for the edit kinds the
studio has so far: objects moved / turned, removed, copied; Select Building (a building's group and everything inside
it); spawn points standing in / on a moved object follow it in every mode; a building moved as one piece carries its
ground along (terrain cut-and-carry). The helpers marked "as blender_edit" are copies of that module's Blender-free
logic (it imports bpy) - keep the two in step.

An edit request (JSON from the app):
    {"archive": path,
     "moves":   [{"uid": hex, "matrix": [16 floats, AC3 row-vector], "building": key or null}],
     "removes": [hex],
     "copies":  [{"uid": hex, "matrix": [16]}],
     "places":  [{"key": "<archive>|<mesh uid hex>" | "prim:..." | "ground:..." | "blend:<archive>|<entity hex>",
                  "matrix": [16], "solid": true,
                  "climb": "native" | "generated" | "none" | "outside", "group": placed asset's name}],
     "climbable": [hex],                 map objects made climbable (generated ledges + handholds, invisible)
     "climbable_outside": [hex],         map buildings climbable outside: rows + ledges on their outer walls
     "carry_ground": true,
     "legacy_footprint": false,        (true: the Blender panel's footprint - the hull of every part, eaves too)
     "map_settings": {"tod": {control: {"v", "all"}}, "water_level": m, "view_distance": m}}
                                       (mapsettings.py: lighting, kill height, view distance)

"places" are models from the asset library (library.py) - any map's - built by sceneport.build_scene as new objects:
their textures as new materials on the map's base material, collision from the source's own shapes or the visible
triangles, climb edges and handhold rows, the navmesh of the cells they touch made again. Studio only: the Blender
panel has no library (its new models go through newmodel / sceneport by script).

Blend groups (blendview.py) - the map's own moved / removed / copied, and "blend:" places (a stock map's group) -
go through ac3editor.gameplay instead: their stored state, NPC slots and props stay right; one taken out is deleted.
"""
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ac3editor.workspace import WORK  # noqa: E402  (the workspace: builds, caches)
from ac3editor import collision, ground, install, mapdata, newmodel, sceneport, spawns, terrain  # noqa: E402
from ac3editor import boundary, territories  # noqa: E402
from ac3editor.gameplay import model_name as G_model  # noqa: E402

import library  # noqa: E402  (studio/engine)

# ------------------------------------------------------------------ per-archive caches (as blender_edit) --

_GROUND, _SPAWNS, _AREAS, _PAIRS = {}, {}, {}, {}


def ground_of(path):
    """(MapData, collision Ground index, visible-model index) of a map archive, built once per worker."""
    if path not in _GROUND:
        md = mapdata.load(path)
        _GROUND[path] = (md, ground.Ground(md), ground.Visible(md))
    return _GROUND[path]


def spawns_of(path):
    if path not in _SPAWNS:
        _SPAWNS[path] = spawns.read(path)
    return _SPAWNS[path]


def areas_of(path):
    if path not in _AREAS:
        _AREAS[path] = boundary.areas(path)
    return _AREAS[path]


def pairs_of(path):
    if path not in _PAIRS:
        _PAIRS[path] = {p["key"]: p for p in territories.pairs(path, areas_of(path))}
    return _PAIRS[path]


def forget(path):
    for c in (_GROUND, _SPAWNS, _AREAS, _PAIRS):
        c.pop(path, None)


# ------------------------------------------------------------------------------- map objects as data --

def m4(m):
    return np.array(m, dtype=np.float64).reshape(4, 4)


def is_spawn_marker(sinfo, e):
    return e.record.startswith("DataBlock_") and e.uid in sinfo["spawns"]


def entity_box(md, e, matrix=None):
    """World bounds of a map object as the Blender panel sees it (world_box): a model's local box corners moved to
    the world; anything without a model, its position."""
    m = m4(e.matrix if matrix is None else matrix)
    if e.mesh in md.meshes and len(md.meshes[e.mesh][1]):
        v = md.meshes[e.mesh][1]
        lo, hi = v.min(0).astype(np.float64), v.max(0).astype(np.float64)
        c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        w = c @ m[:3, :3] + m[3, :3]
        return w.min(0), w.max(0)
    return m[3, :3].copy(), m[3, :3].copy()


def map_objects(md, sinfo):
    """Movable map objects (as blender_edit.map_objects): every entity but the spawn markers, first of each uid."""
    seen, out = set(), []
    for e in md.entities:
        if e.uid in seen or is_spawn_marker(sinfo, e):
            continue
        seen.add(e.uid)
        out.append(e)
    return out


def building_of(md, sinfo, selected, current=None, removed=(), share=0.5):
    """As blender_edit.building: a whole building from any piece of it - every member of the selection's groups,
    then every map object at least half inside their combined box (slabs only when wholly inside), not game-mode
    objects, nothing bigger than the building. `current`: {uid: matrix} for objects already moved. -> [uid]."""
    current = current or {}
    objs = map_objects(md, sinfo)
    by_uid = {e.uid: e for e in objs}
    removed = set(removed)

    def box(e):
        return entity_box(md, e, current.get(e.uid))

    sel = [by_uid[u] for u in selected if u in by_uid]
    groups = {e.group for e in sel if e.group}
    core = {e.uid for e in sel} | {e.uid for e in objs if groups and e.group in groups}
    core = [by_uid[u] for u in core if u not in removed]
    if not core:
        return []
    boxes = [box(e) for e in core]
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    size = hi - lo
    sel_uids = {e.uid for e in sel}
    out = [e.uid for e in core if e.uid not in sel_uids]
    core_uids = {e.uid for e in core}
    for e in objs:
        if e.uid in core_uids or e.uid in removed or e.record.startswith("DataBlock_"):
            continue
        olo, ohi = box(e)
        osz = ohi - olo
        if (osz[:2] > size[:2] + 0.5).any() or ohi[2] > hi[2] + 1.5:
            continue
        inter = np.maximum(0.0, np.minimum(ohi[:2], hi[:2]) - np.maximum(olo[:2], lo[:2]))
        part = float(inter[0] * inter[1]) / float(np.prod(np.maximum(osz[:2], 0.05)))
        slab = "Ground" in e.name or (osz[:2].max() > 20 and osz[2] < 2)
        if part >= (0.95 if slab else share):
            out.append(e.uid)
    return out


def object_box(md, e, matrix=None):
    """As blender_edit.object_box: world box of an entity's collision and LOD0 model together, or None."""
    m = np.array(e.matrix if matrix is None else matrix, dtype=np.float64).reshape(4, 4)
    pts = []
    if e.shapes and e.shapes[0] in md.shapes:
        pts.append(md.shapes[e.shapes[0]][1].astype(np.float64))
    if e.mesh in md.meshes and len(md.meshes[e.mesh][1]):
        pts.append(md.meshes[e.mesh][1].astype(np.float64))
    if not pts:
        return None
    w = np.vstack(pts) @ m[:3, :3] + m[3, :3]
    return w.min(0), w.max(0)


def flat_level(gr, old, gone):
    """As blender_edit.flat_level."""
    ring, c0 = [], old.mean(0)
    for p in list(old) + [(old[i] + old[(i + 1) % len(old)]) / 2 for i in range(len(old))]:
        q = p + (p - c0) / max(np.linalg.norm(p - c0), 1e-6) * 1.5
        z = ground.surface(gr, q[0], q[1], 1e9, gone)
        if z is not None:
            ring.append(z)
    return float(min(ring)) if ring else None


def ground_near(md, gr, poly, gone):
    """As blender_edit.ground_near."""
    poly = np.asarray(poly, np.float64)
    xs = np.linspace(poly[:, 0].min(), poly[:, 0].max(), 12)
    ys = np.linspace(poly[:, 1].min(), poly[:, 1].max(), 12)
    g = np.array([(x, y) for x in xs for y in ys])
    g = g[terrain.inside_poly(g, poly)]
    zs = [z for x, y in poly for z in [ground.surface(gr, x, y, 1e9, gone)] if z is not None]
    return ground.ground_material(md, gr, {"points": g, "z": float(np.median(zs)) if zs else 0.0}, gone)


FOOT_LIFT = 2.0      # a building's footprint: its parts at most this high above the ground under them
FOOT_CELL = 0.25     # (one test per 25 cm cell: the lowest point of the building in it)


def ground_footprint(gr, pts, exclude):
    """The points of a building that stand on the ground: in each 25 cm cell its lowest point, kept when it is at most
    FOOT_LIFT above the walkable surface under it (the building itself not counted) or nothing is under it. The roof's
    overhang and eaves sit high above the yard and drop out: the hull of every part (as blender_edit) can reach ~1 m
    past the walls, which cuts the yard under the eaves where the building lands and carries a strip of the walkway it
    leaves round to float above the yard (holes, grey skirt walls)."""
    keys = np.floor(pts[:, :2] / FOOT_CELL).astype(np.int64)
    order = np.lexsort((pts[:, 2], keys[:, 1], keys[:, 0]))
    k = keys[order]
    first = np.r_[True, (k[1:] != k[:-1]).any(axis=1)]
    low = pts[order][first]
    keep = []
    for x, y, z in low:
        g = ground.surface(gr, x, y, z + 0.1, exclude)
        if g is None or z - g <= FOOT_LIFT:
            keep.append((x, y))
    return np.array(keep) if len(keep) >= 3 else pts[:, :2]


def moved_buildings(md, moved, gr=None, legacy=False):
    """As blender_edit.moved_buildings, on data: moved [(entity, new 4x4, building key or None)] -> [(key, old
    footprint, new footprint, transform, uids)] for buildings moved as one rigid piece. The footprint: the convex hull
    of the building's parts that stand on the ground (ground_footprint, with `gr`); `legacy` (or no `gr`): of all its
    parts, as the Blender panel (for comparisons with the panel's result)."""
    keys = {}
    for e, m, key in moved:
        if key:
            keys.setdefault(key, []).append((e, m))
    out = []
    for key, items in keys.items():
        e0, n0 = items[0]
        T = np.linalg.inv(m4(e0.matrix)) @ n0
        if any(not np.allclose(np.linalg.inv(m4(e.matrix)) @ n, T, atol=1e-3) for e, n in items):
            continue
        core = [e for e, _n in items if e.group] or [e for e, _n in items]
        pts = []
        for e in core:
            m = m4(e.matrix)
            for arr in ([md.meshes[e.mesh][1]] if e.mesh in md.meshes else []) + \
                       ([md.shapes[e.shapes[0]][1]] if e.shapes and e.shapes[0] in md.shapes else []):
                pts.append(arr.astype(np.float64) @ m[:3, :3] + m[3, :3])
        if not pts:
            continue
        pts = np.vstack(pts)
        if gr is not None and not legacy:
            pts = ground_footprint(gr, pts, {e.uid for e, _n in items})
        old = terrain.hull(pts)
        new = terrain.hull((np.c_[old, np.zeros(len(old))] @ T[:3, :3] + T[3, :3])[:, :2])
        out.append((key, old, new, T, {e.uid for e, _n in items}))
    return out


def building_ground(archive, uids, current=None):
    """The ground a building stands on, for Make asset from building: {archive, uids (its map objects), current
    {uid: matrix} (moved ones)} -> {footprint: the hull of its parts that stand on the ground (plan.ground_footprint:
    not the eaves) at its ORIGINAL place, x / y in the map's world; exclude: its own objects (parts of the asset
    already); T: original place -> where it is now (identity unless the building was moved as one piece)}."""
    path = os.path.abspath(archive)
    md, gr, _v = ground_of(path)
    by_uid = {e.uid: e for e in md.entities}
    ents = [by_uid[bytes.fromhex(h)] for h in uids if bytes.fromhex(h) in by_uid]
    if not ents:
        raise ValueError("no map objects in the selection")
    current = {bytes.fromhex(k): np.array(v, np.float64).reshape(4, 4) for k, v in (current or {}).items()}
    core = [e for e in ents if e.group] or ents
    pts = []
    for e in core:
        m = m4(e.matrix)
        for arr in ([md.meshes[e.mesh][1]] if e.mesh in md.meshes else []) +                    ([md.shapes[e.shapes[0]][1]] if e.shapes and e.shapes[0] in md.shapes else []):
            pts.append(arr.astype(np.float64) @ m[:3, :3] + m[3, :3])
    if not pts:
        raise ValueError("the building has no models")
    own = {e.uid for e in ents}
    fp = terrain.hull(ground_footprint(gr, np.vstack(pts), own))
    e0 = core[0]
    T = np.linalg.inv(m4(e0.matrix)) @ current.get(e0.uid, m4(e0.matrix))
    return dict(footprint=[[round(float(x), 3), round(float(y), 3)] for x, y in fp], exclude=sorted(u.hex() for u in own),
                T=[float(x) for x in T.reshape(-1)])


def spawn_follow(md, moved, exclude=()):
    """As blender_edit.spawn_follow: moved [(uid, old 4x4, new 4x4)] -> ([(spawn uid, new 4x4)], [(moved uid,
    spawn names it now covers)])."""
    by_uid = {e.uid: e for e in md.entities}
    spawn_ents = {}
    for e in md.entities:
        if e.record.startswith("DataBlock_") and ("SPW" in e.name or "Spawn" in e.name) and e.uid not in exclude:
            spawn_ents.setdefault(e.uid, e)
    moves_, landed = [], []
    boxes = []
    for uid, old, new in moved:
        e = by_uid.get(uid)
        box = object_box(md, e, old) if e is not None else None
        if box is not None:
            boxes.append((float(np.prod(np.maximum(box[1] - box[0], 0.1))), box, old, new, uid, e))
    for su, sp in spawn_ents.items():
        p = np.array(sp.matrix, dtype=np.float64).reshape(4, 4)
        best = None
        for vol, (lo, hi), old, new, _u, _e in boxes:
            q = p[3, :3]
            if (q[:2] >= lo[:2] - 0.3).all() and (q[:2] <= hi[:2] + 0.3).all() and lo[2] - 0.5 <= q[2] <= hi[2] + 1.5:
                if best is None or vol < best[0]:
                    best = (vol, old, new)
        if best is not None:
            moves_.append((su, p @ np.linalg.inv(best[1]) @ best[2]))
    following = {u for u, _m in moves_}
    for vol, _b, old, new, uid, e in boxes:
        if not e.shapes:
            continue
        lo, hi = object_box(md, e, new)
        hit = [sp.name for su, sp in spawn_ents.items() if su not in following and
               (np.array(sp.matrix).reshape(4, 4)[3, :2] > lo[:2]).all() and
               (np.array(sp.matrix).reshape(4, 4)[3, :2] < hi[:2]).all() and
               lo[2] - 0.5 <= np.array(sp.matrix).reshape(4, 4)[3, 2] <= hi[2]]
        if hit:
            landed.append((uid, hit))
    return moves_, landed


# ----------------------------------------------------------------------------------------------- build --

# ---------------------------------------------------------------------------- ground regions (raise / lower) --

RIDE_ABOVE = 0.6     # an object rides with a region when its base is at most this far above what it stands on
RIDE_BELOW = 1.5     # ... or sunk at most this far into it
MAX_DZ = 20.0
FLAT = 0.25         # thinner than this (decals, plank piles, blend groups' discs): judged by its middle at an edge


def region_poly(r):
    """A request region's outline -> its convex hull (k, 2), counter-clockwise (terrain.cut / skirt need convex)."""
    pts = np.asarray(r["poly"], np.float64).reshape(-1, 2)
    if len(pts) < 3:
        raise RuntimeError(f"region {r.get('name', '?')}: needs at least 3 corners")
    poly = terrain.hull(pts)
    if len(poly) < 3 or abs(float(np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - np.roll(poly[:, 0], -1) * poly[:, 1]))) < 2.0:
        raise RuntimeError(f"region {r.get('name', '?')}: its area is under 1 m2")
    return poly


def region_level(gr, poly):
    """The ground level of a region: the median walkable surface under a grid of its points (None: nothing)."""
    lo, hi = poly.min(0), poly.max(0)
    pts = [(x, y) for x in np.linspace(lo[0], hi[0], 9) for y in np.linspace(lo[1], hi[1], 9)]
    pts = [p for p, ins in zip(pts, terrain.inside_poly(np.array(pts), poly)) if ins]
    zs = [z for x, y in pts for z in [ground.surface(gr, x, y, 1e9, ())] if z is not None]
    return float(np.median(zs)) if zs else None


def region_riders(md, sinfo, gr, poly, exclude):
    """The map objects standing in a region: models (not collision-only objects or markers - a cell's master
    collision has no model, and its box would make it ride and lift unrelated objects), wholly inside its outline
    (their model AND collision, from above), their base within RIDE_ABOVE / RIDE_BELOW of what is under its middle
    (the ground, or another rider - a crate on a table) or, with nothing under it but itself (an object that is its
    own floor), of the region's ground level; not ground themselves. -> (riders [entity], straddling
    [entity]: standing on it but only partly inside - they stay)."""
    riders, straddle = [], []
    level = region_level(gr, poly)
    for e in map_objects(md, sinfo):
        if e.uid in exclude or e.mesh not in md.meshes:
            continue
        box = object_box(md, e)
        if box is None:
            continue
        lo, hi = box
        size = hi - lo
        if terrain.is_ground(e.name, size):
            continue
        corners = np.array([[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]])
        inside = terrain.inside_poly(corners, poly)
        if not inside.any():
            continue
        c = (lo + hi) / 2
        base = entity_box(md, e)[0][2]               # (its model's bottom: collision may reach metres
        g = ground.surface(gr, c[0], c[1], base + RIDE_ABOVE, {e.uid})   # underground)
        if g is None:
            g = level
        if g is None or not (-RIDE_BELOW <= base - g <= RIDE_ABOVE):
            continue
        (riders if inside.all() else straddle).append(e)
    return riders, straddle


def region_spawns(sinfo, gr, poly, already):
    """Spawn points standing on the ground inside a region (not already following a moved object). -> [uid]."""
    out = []
    for uid, sp in sinfo["spawns"].items():
        if uid in already:
            continue
        p = np.asarray(sp.matrix, np.float64).reshape(4, 4)[3, :3]
        if not terrain.inside_poly([p[:2]], poly)[0]:
            continue
        g = ground.surface(gr, p[0], p[1], p[2] + 0.5, ())
        if g is not None and abs(p[2] - g) <= 1.0:
            out.append(uid)
    return out


def region_rim(poly, inner, outer, step=1.0, lip=0.4, gap=0.1, inset=0.15):
    """Climbable collision along a raised / lowered region's edge (the skirt walls are two-sided and can't be
    climbed: a ledge needs ONE wall face meeting ONE walkable face at a convex edge, climb.ledges):
    per step along each edge, a wall face (facing the low side) from the low ground to the high one and a `lip` m wide
    walkable strip on the high side sharing its top edge. -> world triangles (k, 3, 3) for a collision-only model."""
    poly = terrain.hull(poly)
    c = poly.mean(0)
    tris = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        L = np.linalg.norm(b - a)
        if L < 1e-3:
            continue
        nrm = np.array([b[1] - a[1], a[0] - b[0]]) / L
        if np.dot(nrm, (a + b) / 2 - c) < 0:
            nrm = -nrm                                       # outward
        k = max(1, int(np.ceil(L / step)))
        ts = np.linspace(0, 1, k + 1)
        zs = []
        for t in ts:
            p = a + t * (b - a)
            zi, zo = inner(*(p - nrm * inset)), outer(*(p + nrm * inset))
            zs.append(None if zi is None or zo is None else (zi, zo))
        for j in range(k):
            if zs[j] is None or zs[j + 1] is None:
                continue
            (i0, o0), (i1, o1) = zs[j], zs[j + 1]
            if max(abs(i0 - o0), abs(i1 - o1)) < gap:
                continue
            raised = (i0 + i1) > (o0 + o1)                    # the high side: inside (raised) or outside (a pit)
            p0, p1 = a + ts[j] * (b - a), a + ts[j + 1] * (b - a)
            h0, h1 = max(i0, o0), max(i1, o1)
            l0, l1 = min(i0, o0), min(i1, o1)
            towards_low = nrm if raised else -nrm
            wall = [(p0[0], p0[1], l0), (p1[0], p1[1], l1), (p1[0], p1[1], h1), (p0[0], p0[1], h0)]
            q0, q1 = p0 - towards_low * lip, p1 - towards_low * lip
            top = [(p0[0], p0[1], h0), (p1[0], p1[1], h1), (q1[0], q1[1], h1), (q0[0], q0[1], h0)]
            for quad, want in ((wall, np.r_[towards_low, 0.0]), (top, np.array([0.0, 0.0, 1.0]))):
                for tri in ((quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3])):
                    t3 = np.array(tri, np.float64)
                    n = np.cross(t3[1] - t3[0], t3[2] - t3[0])
                    tris.append(t3 if np.dot(n, want) >= 0 else t3[::-1])
    return np.array(tris, np.float64).reshape(-1, 3, 3)


def id_archives():
    """The archives whose ids new objects must avoid: the release set's DataPC / DataPC_extra when an optional
    `paths` module names them (the release every build installs onto), else the live game's. A configured release set
    wins so a build doesn't depend on whatever happens to be installed."""
    try:
        import paths  # optional: names the release set's archives
        mine = [paths.DEFAULT_DATAPC, paths.DEFAULT_EXTRA]
        if all(os.path.exists(p) for p in mine):
            return mine
    except Exception:  # noqa: BLE001
        pass
    return [os.path.join(install.LIVE, a) for a in ("DataPC.forge", "DataPC_extra.forge")]


def climb_spec(md, e, matrix, name, archive=None):
    """A map object made climbable as a newmodel spec: an invisible model at its place whose collision is the object's
    own solid collision (else its member shape in the cell's merged object, else its model's triangles), with
    generated ledges and handhold beams (climb=True, handholds=True: an invisible climbable box). Each collision
    triangle keeps its own CollisionMaterial (a copy on the template's climbable material over a FuzzyZone wall
    would make that wall climbable). The object itself is untouched. -> spec or None."""
    m = m4(matrix)
    pts = None
    shp = [s for s in e.shapes if s in md.shapes and md.shape_layer.get(s, 1) == 1 and len(md.shapes[s][2])]
    walk = library.map_walk(archive) if archive else None
    if not shp and walk is not None:
        shp = [s for s in walk["members"].get(e.uid, []) if s in md.shapes and md.shape_layer.get(s, 1) == 1
               and len(md.shapes[s][2])]
    names = library.shape_tri_materials(md, shp, walk) if shp and walk is not None else None
    if shp:
        vs, ts, n = [], [], 0
        for sid in shp:
            _nm, v, t = md.shapes[sid]
            vs.append(v.astype(np.float64))
            ts.append(t.astype(np.int64) + n)
            n += len(v)
        pts = (np.vstack(vs), np.vstack(ts))
    elif e.mesh in md.meshes:
        _nm, v, t = md.meshes[e.mesh]
        pts = (v.astype(np.float64), t.astype(np.int64))
    if pts is None or not len(pts[1]):
        return None
    sc = np.linalg.norm(m[:3, :3], axis=1)
    rot = m[:3, :3] / np.maximum(sc, 1e-9)[:, None]
    v, t = pts[0] * sc, pts[1]
    if np.linalg.det(rot) < 0:
        rot[0], v[:, 0] = -rot[0], -v[:, 0]
    rigid = np.eye(4)
    rigid[:3, :3] = rot
    rigid[3, :3] = m[3, :3]
    spec = dict(name=name, matrix=rigid, verts=v, tris=t, uv=None, material=None, climb=True, handholds=True,
                visible=False)
    if names and len(names) == len(t) and all(names):
        uniq = sorted(set(names))
        # index 0 is what newmodel gives the handhold beams: a climbable material first (not FuzzyZone)
        base = next((n for n in uniq if not n.startswith(library.NOT_CLIMBABLE)), None) or next((n for n, _l, _s in walk["colmats"].values() if n in ("Stone_Clean", "Wood_Solid", "Wood_Plank")), None)
        if base:
            uniq = [base] + [n for n in uniq if n != base]
        spec["collision_materials"] = uniq
        spec["collision_material_index"] = np.array([uniq.index(n) for n in names], np.int64)
    return spec


def world_collision(md, e, matrix, archive):
    """A map object's own solid collision (else its member shapes in its cell's merged object) in world space at
    `matrix`. -> (verts (n, 3), tris (m, 3)) or None."""
    shp = [s for s in e.shapes if s in md.shapes and md.shape_layer.get(s, 1) == 1 and len(md.shapes[s][2])]
    if not shp:
        walk = library.map_walk(archive)
        shp = [s for s in walk["members"].get(e.uid, []) if s in md.shapes and md.shape_layer.get(s, 1) == 1
               and len(md.shapes[s][2])]
    if not shp:
        return None
    vs, ts, n = [], [], 0
    for sid in shp:
        _nm, v, t = md.shapes[sid]
        vs.append(v.astype(np.float64))
        ts.append(t.astype(np.int64) + n)
        n += len(v)
    v = np.vstack(vs)
    return np.c_[v, np.ones(len(v))] @ m4(matrix)[:, :3], np.vstack(ts)


def scene_collision(scene, inst):
    """A scene instance's collision in world space (its part's own collision, else its visible triangles). -> (verts,
    tris) or None (no collision)."""
    p = scene["parts"][inst["part"]]
    col = p.get("collision")
    if isinstance(col, str) and col == "none":
        return None
    v, t = col if isinstance(col, tuple) else (p["verts"], p["tris"])
    v = np.asarray(v, np.float64)
    if not len(v) or not len(t):
        return None
    return np.c_[v, np.ones(len(v))] @ np.asarray(inst["matrix"], np.float64).reshape(4, 4)[:, :3], np.asarray(t, np.int64)


def _solid_shapes_of(md, e, archive):
    """world_collision's shapes: a map object's own solid shapes, else its member shapes in its cell's merged object."""
    shp = [s for s in e.shapes if s in md.shapes and md.shape_layer.get(s, 1) == 1 and len(md.shapes[s][2])]
    if not shp:
        walk = library.map_walk(archive)
        shp = [s for s in walk["members"].get(e.uid, []) if s in md.shapes and md.shape_layer.get(s, 1) == 1
               and len(md.shapes[s][2])]
    return shp


def world_names(md, e, archive):
    """The CollisionMaterial name of each of world_collision's triangles, or None."""
    shp = _solid_shapes_of(md, e, archive)
    if not shp:
        return None
    return library.shape_tri_materials(md, shp, library.map_walk(archive))


def scene_names(scene, inst):
    """The source CollisionMaterial name of each of scene_collision's triangles, or None."""
    p = scene["parts"][inst["part"]]
    names, idx = p.get("collision_materials"), p.get("collision_material_index")
    if not names or idx is None or not isinstance(p.get("collision"), tuple):
        return None
    return [names[i] if 0 <= i < len(names) else None for i in np.asarray(idx, np.int64).reshape(-1)]


def world_drawn(md, e, matrix):
    """A map object's drawn triangles (its model, LOD0) in world space. -> (verts, tris) or None."""
    if e.mesh not in md.meshes:
        return None
    _nm, v, t = md.meshes[e.mesh]
    if not len(t):
        return None
    return np.c_[v.astype(np.float64), np.ones(len(v))] @ m4(matrix)[:, :3], t.astype(np.int64)


def scene_drawn(scene, inst):
    """A scene instance's drawn triangles in world space. -> (verts, tris) or None."""
    p = scene["parts"][inst["part"]]
    v = np.asarray(p["verts"], np.float64)
    if not len(v) or not len(p["tris"]):
        return None
    return np.c_[v, np.ones(len(v))] @ np.asarray(inst["matrix"], np.float64).reshape(4, 4)[:, :3], np.asarray(p["tris"], np.int64)


def outside_groups(req, md, sinfo, scene, places, src):
    """The request's climbable-outside buildings: placed assets (by group) and copies with climb "outside", map
    buildings in place (every piece of the building of each piece named in "climbable_outside"). -> {name: [(verts,
    tris) world or None]}, in a fixed order (the layers are numbered in it); a placed asset's ground parts' collision
    under {name + FLOORS}: something to stand on in front of its walls, not part of the building."""
    objs = {e.uid: e for e in md.entities}
    current_of = {bytes.fromhex(mv["uid"]): mv["matrix"] for mv in req.get("moves", [])}
    outside = {}
    if scene:
        for inst, pl in zip(scene["instances"], places):
            if library.climb_of(pl) == "outside" and not library.is_ground(pl["key"]):
                outside.setdefault(pl.get("group") or inst["name"], []).append(scene_collision(scene, inst))
                outside.setdefault((pl.get("group") or inst["name"]) + NAMES, []).append(scene_names(scene, inst))
        for inst, pl in zip(scene["instances"], places):
            if library.is_ground(pl["key"]) and pl.get("group") in outside:
                outside.setdefault(pl["group"] + FLOORS, []).append(scene_collision(scene, inst))
            elif library.climb_of(pl) == "outside":
                outside.setdefault((pl.get("group") or inst["name"]) + DRAWN, []).append(scene_drawn(scene, inst))
    for c in req.get("copies", []):
        e = objs.get(bytes.fromhex(c["uid"]))
        if c.get("climb") == "outside" and e is not None:
            outside.setdefault(c.get("group") or f"copy of {e.name}", []).append(world_collision(md, e, c["matrix"], src))
            outside.setdefault((c.get("group") or f"copy of {e.name}") + NAMES, []).append(world_names(md, e, src))
            outside.setdefault((c.get("group") or f"copy of {e.name}") + DRAWN, []).append(world_drawn(md, e, c["matrix"]))
    seen = set()
    for h in req.get("climbable_outside", []):
        u = bytes.fromhex(h)
        if u in seen or u not in objs:
            continue
        bld = [u] + building_of(md, sinfo, [u], current=current_of)
        seen.update(bld)
        name = objs[u].group or objs[u].name
        outside.setdefault(f"{name} (in place)", []).extend(
            world_collision(md, objs[b], current_of.get(b, objs[b].matrix), src) for b in bld if b in objs)
        outside.setdefault(f"{name} (in place)" + NAMES, []).extend(world_names(md, objs[b], src) for b in bld if b in objs)
        outside.setdefault(f"{name} (in place)" + DRAWN, []).extend(
            world_drawn(md, objs[b], current_of.get(b, objs[b].matrix)) for b in bld if b in objs)
    return outside


FLOORS = "\0floors"     # outside_groups: a building's floors' key (its name + this)
DRAWN = "\0drawn"       # and its drawn triangles' (holds only where a drawn wall is)
NAMES = "\0names"       # and its collision triangles' CollisionMaterial names, piece by piece (its FuzzyZone roofs)


def outside_specs(groups, archive, log=print):
    """Climbable outside: {building name: [(verts, tris) world]} -> (newmodel specs ED_ClimbOut<nn> - one
    invisible layer of handhold rows and ledges on each building's outer walls (climbout.layer) - report lines)."""
    import climbout
    table = library.collision_material_table(archive)
    gr = ground_of(archive)[1]
    specs, lines = [], []
    for name, pieces in groups.items():
        if name.endswith(FLOORS) or name.endswith(DRAWN) or name.endswith(NAMES):
            continue
        named = groups.get(name + NAMES, [None] * len(pieces))
        names = []
        for p, nm in zip(pieces, named):
            if p is not None:
                names += list(nm) if nm is not None and len(nm) == len(p[1]) else [None] * len(p[1])
        pieces = [p for p in pieces if p is not None]
        if not pieces:
            lines.append(f"{name}: climbable outside - no collision to climb")
            continue
        vs, ts, n = [], [], 0
        for v, t in pieces:
            vs.append(v)
            ts.append(t + n)
            n += len(v)
        floors = [p for p in groups.get(name + FLOORS, []) if p is not None]
        sp, rep = climbout.layer(np.vstack(vs), np.vstack(ts), f"ED_ClimbOut{len(specs):02d}", table, log=log,
                                 floors=floors, floor_at=lambda x, y, top: ground.surface(gr, x, y, top, ()),
                                 drawn=[p for p in groups.get(name + DRAWN, []) if p is not None],
                                 names=names if any(names) else None)
        lines.append(f"{name}: climbable outside - {rep['walls']} wall faces: {rep['outer']} outer ("
                     + ", ".join(f"{k} {v}" for k, v in rep["outer_by_side"].items())
                     + f"), {rep['inner']} inner (nothing added); {rep['rows']} handhold row(s) "
                     f"({rep.get('rows_low', 0)} too low left out), "
                     f"{rep.get('cut_m', 0.0):.0f} m cut where no drawn wall is at them, "
                     f"{rep.get('step_chain', 0)} on the ways up to the eaves, "
                     f"{rep.get('roof_skin', 0)} FuzzyZone roof triangles made walkable, "
                     f"{rep.get('tops_reachable', (0, 0))[0]} of {rep.get('tops_reachable', (0, 0))[1]} top ledge pieces "
                     f"reachable from the ground, "
                     f"{rep['top_ledges']} top ledge(s)" + ("" if sp else " - nothing to add"))
        if sp is not None:
            sp["_building"] = name
            sp["_reach"] = rep.get("tops_reachable", (0, 0))
            sp["_reach_z"] = rep.get("tops_by_z", {})
            specs.append(sp)
    return specs, lines


def parse(req, md, sinfo):
    """The request -> (moved [(entity, new 4x4, key)], removed [entity], copied [(entity, 4x4)], problems)."""
    objs = {e.uid: e for e in map_objects(md, sinfo)}
    order = {e.uid: k for k, e in enumerate(md.entities)}
    moved, removed, copied, problems = [], [], [], []

    def get(h):
        e = objs.get(bytes.fromhex(h))
        if e is None:
            problems.append(f"{h}: not a movable object of this map")
        return e

    for mv in req.get("moves", []):
        e = get(mv["uid"])
        if e is None:
            continue
        new = m4(mv["matrix"])
        sc = np.linalg.norm(new[:3, :3], axis=1) / np.maximum(np.linalg.norm(m4(e.matrix)[:3, :3], axis=1), 1e-9)
        if not np.allclose(sc, 1, atol=1e-3):
            problems.append(f"{e.name}: resizing isn't in the studio yet")
        elif not np.allclose(new, m4(e.matrix), atol=1e-4):
            moved.append((e, new, mv.get("building")))
    for h in req.get("removes", []):
        e = get(h)
        if e is not None:
            removed.append(e)
    for c in req.get("copies", []):
        e = get(c["uid"])
        if e is None:
            continue
        if e.components == ["(not decoded)"]:
            problems.append(f"{e.name}: can't be copied (its entity doesn't decode)")
        elif not e.record.startswith("Cell"):
            problems.append(f"{e.name}: objects outside the map cells (game-mode / World) can't be copied yet")
        else:
            copied.append((e, m4(c["matrix"])))
    moved.sort(key=lambda x: order[x[0].uid])
    removed.sort(key=lambda e: order[e.uid])
    return moved, removed, copied, problems


def build(req, log=print, out_root=None):
    """Build a test map from the request. -> dict(dest, report lines, warnings)."""
    src = os.path.abspath(req["archive"])
    md, gr, _vis = ground_of(src)
    sinfo = spawns_of(src)
    moved, removed, copied, problems = parse(req, md, sinfo)
    # (blend groups and stock gameplay objects: the gameplay pass)
    places = [pl for pl in req.get("places", []) if not pl["key"].startswith(("blend:", "gp:"))]
    # a ground part on its own map goes in as the carry's models (stock materials, vertex colours); the rest - and
    # ground from other maps - through sceneport
    own_ground = [pl for pl in places if library.is_ground(pl["key"]) and
                  os.path.normcase(os.path.abspath(library.ground_spec_of(pl["key"])["a"])) == os.path.normcase(src)]
    places = [pl for pl in places if pl not in own_ground]
    stats = {}
    scene = library.scene_for(places, log=log, stats=stats) if places else None
    extra_specs, added = [], []
    for k, pl in enumerate(own_ground):
        got = library.ground_models(pl["key"], pl["matrix"], f"ED_LibGround{k:02d}", pl.get("solid", True))
        extra_specs += got
        added += [g["name"] for g in got]
    # map objects (and copies) made climbable: an invisible climbable copy of their collision each
    objs = {e.uid: e for e in md.entities}
    current_of = {bytes.fromhex(mv["uid"]): mv["matrix"] for mv in req.get("moves", [])}
    climbers = [(objs.get(bytes.fromhex(h)), current_of.get(bytes.fromhex(h))) for h in req.get("climbable", [])]
    climbers += [(objs.get(bytes.fromhex(c["uid"])), c["matrix"]) for c in req.get("copies", [])
                 if c.get("climb") == "generated"]
    # climbable outside: one invisible layer per building
    outside = outside_groups(req, md, sinfo, scene, places, src)
    out_specs, out_lines = outside_specs(outside, src, log=log) if outside else ([], [])
    extra_specs += out_specs
    added += [sp["name"] for sp in out_specs]
    n_climb = 0
    for e, mtx in climbers:
        if e is None:
            continue
        sp = climb_spec(md, e, mtx if mtx is not None else e.matrix, f"ED_Climb{n_climb:03d}", src)
        if sp is not None:
            extra_specs.append(sp)
            added.append(sp["name"])
            n_climb += 1
    if problems:
        raise RuntimeError("; ".join(problems))
    warnings, report = [], []
    # ground regions raised / lowered: the objects standing in each ride along (moves), its ground is carried
    regions, rode, dropped = [], {}, set()
    touched = {e.uid for e, _m, _k in moved} | {e.uid for e in removed}
    for r in req.get("regions", []):
        dz = float(r.get("dz", 0.0))
        if abs(dz) < 0.01:
            continue
        if abs(dz) > MAX_DZ:
            raise RuntimeError(f"region {r.get('name', '?')}: {dz:+.1f} m - at most {MAX_DZ:.0f} m up or down")
        poly = region_poly(r)
        riders, straddle = region_riders(md, sinfo, gr, poly, touched)
        # flat things across a PIT's edge (decals, plank piles, blend groups' discs - left alone they hang over it):
        # their middle inside -> they go down (the part outside ends up under the ground round it); their middle
        # outside -> cosmetic decals (no collision) are taken out; the rest stay, named. A raised area keeps them all
        # where they are (its ground covers their inside part)
        flat_ride, flat_drop, stay, blend = [], [], [], []
        for e in straddle:
            lo, hi = object_box(md, e)
            c = (lo + hi) / 2
            if "GameplayCoordinatorComponent" in e.components:
                blend.append(e)
            if hi[2] - lo[2] < FLAT and dz < 0 and terrain.inside_poly([c[:2]], poly)[0]:
                flat_ride.append(e)                  # (raised: they stay - the area's ground covers their inside part)
            elif hi[2] - lo[2] < FLAT and dz < 0 and not e.shapes and "GameplayCoordinatorComponent" not in e.components:
                flat_drop.append(e)
            else:
                stay.append(e)
        riders += flat_ride
        for e in flat_drop:
            removed.append(e)
            touched.add(e.uid)
            dropped.add(e.uid)
        straddle = stay
        T = np.eye(4)
        T[3, 2] = dz
        for e in riders:
            moved.append((e, m4(e.matrix) @ T, None))
            touched.add(e.uid)
            rode[e.uid] = m4(e.matrix) @ T
        name = r.get("name") or f"region {len(regions) + 1}"
        area = abs(float(np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - np.roll(poly[:, 0], -1) * poly[:, 1]))) / 2
        regions.append(dict(name=name, poly=poly, dz=dz, T=T, riders={e.uid for e in riders}))
        line = f"{name}: {dz:+.2f} m over {area:.0f} m2, {len(riders)} object(s) on it ride along"
        log(line)
        report.append(line)
        if flat_ride:
            report.append(f"{name}: {len(flat_ride)} flat thing(s) across its edge go with it (their middle is inside)")
        if flat_drop:
            report.append(f"{name}: {len(flat_drop)} ground decal(s) across the pit's edge taken out: "
                          + ", ".join(e.name for e in flat_drop[:4]) + (" ..." if len(flat_drop) > 4 else ""))
        if straddle:
            warnings.append(f"{name}: {len(straddle)} object(s) stand across its edge and stay where they are: "
                            + ", ".join(e.name for e in straddle[:5]) + (" ..." if len(straddle) > 5 else ""))
        for e in blend:
            warnings.append(f"{name}: the blend group {e.name} stands across its edge ("
                            + ("it goes with the area" if e in flat_ride else "it stays") + ") - its outline drapes over "
                            "the edge and NPCs posing at that side may stand against the wall: move the area's corner "
                            "or the group")
    # ground sculpted (sculpt.py) - what stands on it in each area goes up / down with the ground under its middle
    import sculpt
    strokes = sculpt.strokes_of(req)
    sc_areas = sculpt.areas(strokes)
    for k, (poly, group) in enumerate(sc_areas):
        sc_riders, _straddle = region_riders(md, sinfo, gr, poly, touched)
        n_rode = 0
        for e in sc_riders:
            box = object_box(md, e)
            c = (box[0] + box[1]) / 2
            d = float(sculpt.dz_at(strokes, c[0], c[1]))
            if abs(d) < 0.01:
                continue
            T = np.eye(4)
            T[3, 2] = d
            moved.append((e, m4(e.matrix) @ T, None))
            touched.add(e.uid)
            rode[e.uid] = m4(e.matrix) @ T
            n_rode += 1
        hs = [h for _x, _y, _r, h in group]
        line = (f"sculpted area {k + 1}: {len(group)} stroke(s), {min(hs):+.2f} .. {max(hs):+.2f} m each, "
                f"{n_rode} object(s) on it move with the ground")
        log(line)
        report.append(line)
    order = {e.uid: k for k, e in enumerate(md.entities)}
    moved.sort(key=lambda x: order[x[0].uid])
    nav_areas, routes = req.get("nav_areas", []), req.get("routes", [])
    import mapsettings
    settings = req.get("map_settings") or {}
    want_settings = mapsettings.wants(settings)
    if not moved and not removed and not copied and not places and not regions and not extra_specs \
            and not nav_areas and not routes and not strokes and len(places) == len(req.get("places", [])) \
            and not want_settings and not req.get("spawns") and not req.get("boundaries"):
        raise RuntimeError("nothing to build: no moved, removed, copied or placed objects, no ground raised / lowered, "
                           "no navmesh or crowd route edits, no map settings")
    gone = {e.uid for e in removed}
    # gameplay objects - blend groups, hiding spots, benches, chase breakers, lifts, Artifact Assault artifacts
    # / scoring zones, Domination points - go through the gameplay engine (their stored state, FX companions, map icons
    # and merged collision move with them; copies get fresh ids for everything nested; a blend group taken out is deleted
    # from its cell; a moved Domination point carries its zone), not collision.edit_shapes
    import blendview
    import gameplayview
    gp_places = [pl for pl in req.get("places", []) if gameplayview.is_key(pl["key"])]
    bg_places = [pl for pl in req.get("places", []) if blendview.is_blend_key(pl["key"])]
    ths = gameplayview.things(src) if moved or removed or copied or gp_places else {}
    try:
        gpr = gameplayview.route(ths, moved, removed, copied, gp_places, log=log)
    except (ValueError, RuntimeError) as e:
        raise RuntimeError(str(e)) from None
    th_moves, th_removes, th_adds = gpr["moves"], gpr["removes"], gpr["adds"]
    for pl in bg_places:
        th = blendview.template_of(pl["key"])
        th_adds.append(dict(template=th, matrix=m4(pl["matrix"]), name=f"{G_model(th.name)}_ED{len(th_adds) + 1:03d}"))
    warnings += gpr["warnings"]
    bg = {u for u, th in ths.items() if th.kind == "blend_group"}
    bg_moves = {u: m for u, m in th_moves.items() if u in bg}
    bg_removes = [u for u in th_removes if u in bg]
    bg_adds = [a for a in th_adds if a["template"].kind == "blend_group"]
    not_edits = set(th_moves) | set(th_removes) | gpr["consumed"]
    # every blend group's NPC slots as the build leaves them - a navmesh made again here keeps the ground they
    # stand on even when it's closed in (a merchant behind his counter, inside his stall: stock maps give him a navmesh
    # island of his own, and without one he never appears)
    nav_keep = (blendview.slot_points(src, moves=bg_moves, removes=bg_removes, adds=bg_adds)
                if places or regions or nav_areas or strokes else [])
    edits = [(e.uid, "move", m) for e, m, _k in moved if e.uid not in not_edits]
    follow, landed = spawn_follow(md, [(e.uid, m4(e.matrix), m) for e, m, _k in moved
                                       if not e.record.startswith("DataBlock_") and e.uid not in bg])
    sp_placed, followers = {}, set()
    for su, m in follow:
        if su in sinfo["spawns"]:
            sp_placed[su] = {r: spawns.level(m) for r in sinfo["spawns"][su].modes(sinfo)}
            followers.add(su)
    if follow:
        log(f"{len(follow)} spawn point(s) move with the objects they stand in / on")
        report.append(f"{len(follow)} spawn point(s) follow the objects they stand in / on")
    for rg in regions:
        mine = region_spawns(sinfo, gr, rg["poly"], set(sp_placed))
        for su in mine:
            m = np.asarray(sinfo["spawns"][su].matrix, np.float64).reshape(4, 4) @ rg["T"]
            sp_placed[su] = {r: spawns.level(m) for r in sinfo["spawns"][su].modes(sinfo)}
            followers.add(su)
        if mine:
            report.append(f"{rg['name']}: {len(mine)} spawn point(s) on it move {rg['dz']:+.2f} m")
    for poly, _group in sc_areas:                    # (by the sculpted ground's change under each)
        mine = region_spawns(sinfo, gr, poly, set(sp_placed))
        for su in mine:
            m = np.asarray(sinfo["spawns"][su].matrix, np.float64).reshape(4, 4).copy()
            m[3, 2] += float(sculpt.dz_at(strokes, m[3, 0], m[3, 1]))
            sp_placed[su] = {r: spawns.level(m) for r in sinfo["spawns"][su].modes(sinfo)}
            followers.add(su)
        if mine:
            report.append(f"{len(mine)} spawn point(s) on the sculpted ground move with it")
    # spawn points moved / removed / added by hand, per mode (a hand edit wins over a follow in that mode)
    import spawnview
    try:
        u_placed, u_added = spawnview.placed_of(req.get("spawns"), sinfo)
    except ValueError as e:
        raise RuntimeError(str(e)) from None
    for su, per in u_placed.items():
        sp_placed.setdefault(su, {}).update(per)
        followers.discard(su)
    b_req = req.get("boundaries") or {}
    s_areas = spawnview.with_boundaries(areas_of(src), b_req)
    sp_changes = sp_final = None
    if sp_placed or u_added:
        try:
            sp_changes, sp_final = spawns.plan(sinfo, sp_placed, u_added)
        except ValueError as e:
            raise RuntimeError(str(e)) from None
        n_cp = len(sp_changes["copies"])
        changed = (set(sp_changes["move"]) - followers) | {
            ("new", k) for k, (t, _r, _m) in enumerate(sp_changes["copies"]) if t in u_placed or k >= n_cp - len(u_added)}
        errors, sp_warnings = spawns.problems(
            sinfo, sp_final, areas=s_areas, pairs=list(pairs_of(src).values()),
            floor=lambda x, y, top: ground.surface(gr, x, y, top, ()), changed=changed)
        if errors:
            raise RuntimeError("; ".join(errors))
        warnings += sp_warnings
        if req.get("spawns"):
            sq = req["spawns"]
            report.append(f"spawn points by hand: {len(sq.get('move', []))} moved, {len(sq.get('remove', []))} removed, "
                          f"{len(sq.get('add', []))} added")
    # out-of-bounds boundaries with new corners - every spawn their modes keep stays inside
    if b_req:
        final_now = sp_final if sp_final is not None else {
            r: {u: (sp.kind, sp.matrix) for u, sp in here.items()} for r, here in spawns.active(sinfo).items()}
        errors, b_warnings = spawnview.boundary_problems(areas_of(src), b_req, final_now)
        if errors:
            raise RuntimeError("; ".join(errors))
        warnings += b_warnings
    by_name = {e.uid: e.name for e, _m, _k in moved}
    for uid, hit in landed:
        warnings.append(f"{by_name[uid]} now stands on {len(hit)} spawn point(s): {', '.join(hit[:4])}")
    for e in removed:
        if e.uid not in not_edits:
            edits.append((e.uid, "remove"))
    for k, (e, m) in enumerate(copied):
        if k not in gpr["consumed_copies"]:
            edits.append((e.uid, "copy", m))

    title = mapdata_title(src).replace(" ", "_")
    root = out_root or os.path.join(WORK, "edits")
    dest = os.path.join(root, f"{title}_{time.strftime('%Y%m%d_%H%M%S')}", os.path.basename(src))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    avoid = id_archives()
    specs = list(extra_specs)
    carry = moved_buildings(md, moved, gr, legacy=req.get("legacy_footprint", False)) \
        if req.get("carry_ground", True) else []
    if carry:
        log(f"{len(carry)} building(s) carry their ground: " + ", ".join(k for k, *_r in carry))
    # a region is a carry that keeps its outline and goes straight up / down
    carry += [(rg["name"], rg["poly"], rg["poly"], rg["T"], rg["riders"]) for rg in regions]
    walls = []
    # the holes removed / moved objects leave, plugged (as the Blender panel's Check Holes)
    plugs = []
    if req.get("plug_holes", True) and (moved or removed):
        t0 = time.time()
        plugs = find_plugs(md, gr, _vis, [(e, m4(m), k) for e, m, k in moved],
                           list(removed), carry,
                           landing_extra=[poly for poly, _g in sc_areas])
        if plugs:
            got = plug_specs(plugs)
            specs += got
            added += [sp["name"] for sp in got]
            n_seen = sum(1 for _n, h, _m in plugs if h.get("visible_only"))
            n_col = sum(1 for sp in got if not sp.get("visible", True))
            line = (f"{len(plugs)} hole(s) the removed / moved objects left plugged ({len(plugs) - n_seen - n_col} slab(s) in "
                    f"the ground's material, {n_col} in the collision only, {n_seen} seen-through only - drawn, the "
                    f"ground's collision runs on under them): "
                    + ", ".join(f"{n} {h['area']:.1f} m2" for n, h, _m in plugs[:6]) + (" ..." if len(plugs) > 6 else ""))
        else:
            line = "holes: every removed / moved object leaves something to stand on"
        log(line + f" ({time.time() - t0:.0f} s)")
        report.append(line)
    # (blend groups after the models: a carried building's floor - and a raised area's ground - is in place by then, so
    # its groups' slots are checked on the ground they will stand on)
    passes = [p for p, want in (("edits", bool(edits)), ("spawns", sp_changes is not None), ("boundary", bool(b_req)),
                                ("terrain", bool(carry)), ("sculpt", bool(sc_areas)),
                                ("models", bool(carry) or bool(specs) or bool(sc_areas)),
                                ("blend", bool(th_moves or th_removes or th_adds)),
                                ("navmesh", bool(regions) or bool(sc_areas)),
                                ("navareas", bool(nav_areas)), ("mapsettings", want_settings),
                                ("places", bool(places)), ("routes", bool(routes)))
              if want]
    current, created = src, []
    for k, name in enumerate(passes):
        out = dest if k == len(passes) - 1 else f"{dest}.{name}"
        if name == "edits":
            if not collision.edit_shapes(current, out, edits, log=log, id_avoid=avoid, created=created):
                raise RuntimeError("the build didn't read back cleanly (see the log)")
        elif name == "blend":
            before = _pickles(out) | _pickles(current)
            lines, warns = gameplayview.apply(current, out, th_adds, th_moves, th_removes, ths=ths, log=log)
            for p in (_pickles(out) | _pickles(current)) - before:   # (its ground read of the in-between file)
                os.remove(os.path.join(mapdata.CACHE, p))
            report += lines
            warnings += warns
        elif name == "spawns":
            ok, _made = spawns.apply(current, out, sinfo, sp_changes, sp_final, log=log)
            if not ok:
                raise RuntimeError("the spawn points didn't read back as planned (see the log)")
        elif name == "boundary":
            polys = {bytes.fromhex(h): np.asarray(c, np.float64) for h, c in b_req.items()}
            if not boundary.rebuild(current, out, polys, log=log):
                raise RuntimeError("the boundary rebuild changed more than its own records (see the log)")
            ar = areas_of(src)
            report.append(f"{len(polys)} boundary(ies) rebuilt: " + ", ".join(
                f"{ar[u]['name']} ({len(c)} corners, {', '.join(spawns.mode_label(r) for r in ar[u]['records'][:4])}"
                f"{' ...' if len(ar[u]['records']) > 4 else ''})" for u, c in polys.items()))
        elif name == "terrain":
            specs += carry_ground(md, gr, src, current, out, dest, carry,
                                  {e.uid for e, _m, _k in moved} | gone, avoid, log, report, walls_out=walls,
                                  rims={rg["name"] for rg in regions})
        elif name == "sculpt":
            # each area's ground cut out (visible + collision), split to 1 m, bent by the strokes, put back
            step = current
            gone_s = {e.uid for e, _m, _k in moved} | gone
            for k2, (poly, group) in enumerate(sc_areas):
                nxt = out if k2 == len(sc_areas) - 1 else f"{dest}.sculpt{k2}"
                same, vis, col = terrain.cut(step, nxt, [poly], exclude=gone_s, id_avoid=avoid, log=log)
                if not same:
                    raise RuntimeError("the ground cut changed more than its own records (see the log)")
                got = sculpt.specs(vis, col, strokes, f"ED_Sculpt{k2:02d}")
                specs += got
                n_vis = sum(len(sp["tris"]) for sp in got if sp.get("material") is not None)
                line = (f"sculpted area {k2 + 1}: {sum(len(v[0]) for v in vis)} ground triangle(s) cut, {n_vis} put back "
                        f"bent ({len(got)} model(s))")
                log(line)
                report.append(line)
                if step not in (current, src) and os.path.exists(step):
                    os.remove(step)
                step = nxt
        elif name == "models":
            _made, same = newmodel.add_models(current, out, specs, id_avoid=avoid, log=log)
            if not same:
                raise RuntimeError("the new models changed more than their own records (see the log)")
        elif name == "navmesh":
            # the crowds' ground made again under each region from the collision: its new height, its skirt walls
            # as obstacles (a route across a raised edge is rerouted round it or switched off - navgen step 5)
            from ac3editor import navgen
            pts = np.vstack([rg["poly"] for rg in regions] + [poly for poly, _g in sc_areas])
            lo, hi = pts.min(0) - 3.0, pts.max(0) + 3.0
            before = _pickles(out)
            nv = navgen.regenerate(current, out, box=(lo[0], lo[1], hi[0], hi[1]),
                                   new=np.asarray(walls).reshape(-1, 3, 3) if walls else None, keep=nav_keep, log=log)
            for p in _pickles(out) - before:
                os.remove(os.path.join(mapdata.CACHE, p))
            if current != src:
                _drop_cache(current)
            if nv.get("check_fails"):
                raise RuntimeError(f"the navmesh made under the region(s) didn't pass its checks: {nv.get('problems')}")
            report.append(f"navmesh made again under the region(s) / sculpted ground: cells {nv.get('cells')} (checks failed: 0)")
            if nv.get("blocked_flows"):
                warnings.append(f"{len(nv['blocked_flows'])} crowd route(s) across a region's edge switched off")
        elif name == "navareas":
            # navmesh areas made again from the collision / crowds kept out (navview), before library models
            # (their own navmesh pass follows) and route edits (last: nothing after them re-ties the routes)
            import navview
            before = _pickles(out)
            lines, warns = navview.apply_areas(current, out, nav_areas, lambda poly: region_level(gr, poly),
                                               keep=nav_keep, log=log)
            for p in _pickles(out) - before:
                os.remove(os.path.join(mapdata.CACHE, p))
            if current != src:
                _drop_cache(current)
            report += lines
            warnings += warns
        elif name == "routes":
            import navview
            report += navview.apply_routes(current, out, routes, log=log)
        elif name == "mapsettings":
            # the lighting (the map's time-of-day table), the kill height, the view distance - before the library
            # models' pass (each pass writes its records stored; the build is packed at the end)
            ms_lines = mapsettings.apply(current, out, settings, log=log)
            report += [x for x in ms_lines if not x.startswith("warning: ")]
            warnings += [x[len("warning: "):] for x in ms_lines if x.startswith("warning: ")]
        else:
            # the navmesh is made again from the collision of the archive it's given (SCENE_FORMAT.md)
            before = _pickles(out)
            # each collision triangle's own map's material, named as this map names it (FuzzyZone walls stay
            # unclimbable)
            cm_lines, cm_missing = library.resolve_collision_materials(scene, current, log=log)
            report += cm_lines
            if cm_missing:
                warnings.append("collision materials this map hasn't: " + ", ".join(cm_missing) +
                                " - those triangles took the template's")
            rep = sceneport.build_scene(current, out, scene, placement="position", id_avoid=avoid,
                                        handholds="rows", navmesh="generate", flows=None, nav_keep=nav_keep, log=log)
            for p in _pickles(out) - before:      # (navgen reads the build before it's packed: a pickle nothing uses)
                os.remove(os.path.join(mapdata.CACHE, p))
            report += place_report(rep, warnings)
            if current != src:
                _drop_cache(current)
        if current != src:
            os.remove(current)
        current = out
    # every record a pass rewrote is stored uncompressed (datafile.compress writes stored blocks): packed as the game's
    # own files are (archive.pack, as gameplay.build does; unpacked, a build can be nearly twice the map's size)
    from ac3editor import archive
    size0, size1 = archive.pack(dest, src, log=log)
    report.append(f"packed: {size0 / 1e6:.0f} MB -> {size1 / 1e6:.0f} MB")
    report.insert(0, f"{len(moved)} moved, {len(removed)} removed, {len(copied)} copied, {len(places)} placed"
                     + (f", {len(regions)} ground region(s)" if regions else "")
                     + (f", {len(nav_areas)} navmesh area(s)" if nav_areas else "")
                     + (f", {len(routes)} crowd route edit(s)" if routes else "")
                     + (", map settings" if want_settings else ""))
    if scene:
        report.append(f"climbing of the placed models: {stats.get('native_edges', 0)} climb edge(s) of their own carried "
                      f"(\"as in its own map\"); generated ones on {sum(1 for pl in places if library.climb_of(pl) == 'generated')} "
                      f"placement(s); none on {sum(1 for pl in places if library.climb_of(pl) == 'none')}")
    if n_climb:
        report.append(f"{n_climb} object(s) made climbable (invisible climbable copies of their collision)")
    report += out_lines
    if own_ground:
        report.append(f"{len(own_ground)} ground part(s) on their own map: {len([a for a in added if a.startswith('ED_LibGround')])} model(s)")
    return dict(dest=dest, report=report, warnings=warnings, created=[c.hex() for c in created],
                outside=[dict(name=sp["name"], building=sp["_building"]) for sp in out_specs],
                placed=[dict(name=i["name"], matrix=np.asarray(i["matrix"]).reshape(-1).tolist(),
                             solid=pl.get("solid", True) and scene["parts"][i["part"]].get("collision") != "none")
                        for i, pl in zip(scene["instances"], places)] if scene else [], added=added,
                _carry=carry, _moved={e.uid for e, _m, _k in moved} | gone, _riders={**rode, **gpr["riders"]},
                _dropped=dropped, _plugs=[(h["points"].tolist(), h["z"], "seen" if h.get("visible_only") else
                         "collision" if h.get("visible_cover", 0) >= 0.8 else "slab") for _n, h, _m in plugs])


def place_report(rep, warnings):
    """sceneport's report -> the Build tab's lines."""
    b = rep.get("build", {})
    lines = [f"{b.get('instances', 0)} library model(s) placed: {b.get('meshes', 0)} mesh(es), "
             f"{b.get('mesh triangles', 0)} triangles, {b.get('collision triangles', 0)} collision triangles"
             + (f", {b['handhold beams']} handhold rows" if b.get("handhold beams") else "")]
    m = rep.get("materials", {})
    lines.append("materials: " + ", ".join(f"{k} {v}" for k, v in m.items() if k != "bases"))
    nv = rep.get("navmesh")
    if nv:
        lines.append(f"navmesh made again under them: {nv.get('cells', nv.get('pieces', '?'))} "
                     f"(checks failed: {nv.get('check_fails', 0)})")
    if rep.get("crowd_routes_disabled"):
        warnings.append(f"{len(rep['crowd_routes_disabled'])} crowd route(s) through the placed models switched off")
    for name, share in rep.get("open_tops", [])[:5]:
        warnings.append(f"{name}: its top is open ({share:.0%} covered) - climbers who reach it fall inside")
    return lines


def carry_ground(md, gr, src, current, out, dest, carry, gone, avoid, log, report, walls_out=None, rims=()):
    """As the terrain pass of blender_edit.apply_edits: each building's ground cut along its footprint and carried,
    the ground where it lands cut away, skirts where heights meet, a relocated building's old place made flat.
    Writes `out`; -> the new-model specs (carried ground, skirts, fills) for the models pass."""
    specs = []
    step = current
    for k, (key, old, new, T, _uids) in enumerate(carry):
        relocate = terrain.overlap(old, new) < 0.9
        a, b = f"{dest}.cut{k}a", f"{dest}.cut{k}b"
        same1, vis, col = terrain.cut(step, a, [old], exclude=gone, id_avoid=avoid,
                                      log=log if relocate else (lambda *_x: None))
        if not same1:
            raise RuntimeError("the ground cut changed more than its own records (see the log)")
        base = a if relocate else step
        if not relocate:
            os.remove(a)
        specs += terrain.chunk_specs(vis, col, T, name=f"ED_Ground{k:02d}")
        line = f"{key}: {sum(len(v[0]) for v in vis)} ground triangles carried along" + \
            (" (relocated: its old place cut too)" if relocate else "")
        log(line)
        report.append(line)
        same2, _v, _c = terrain.cut(base, b, [new], exclude=gone, id_avoid=avoid, log=log)
        if not same2:
            raise RuntimeError("the ground cut changed more than its own records (see the log)")
        for f_ in {step, base} - {current, src}:
            if os.path.exists(f_):
                os.remove(f_)
        step = b
        col_new = np.asarray(col).reshape(-1, 3, 3) @ T[:3, :3] + T[3, :3] if len(col) else np.zeros((0, 3, 3))
        inner = lambda x, y, c=col_new: terrain.height_at(c, x, y)  # noqa: E731
        z_flat = flat_level(gr, old, gone) if relocate else None
        z_cap = float(col_new[:, :, 2].max()) + 4.5 if len(col_new) else 1e9

        def outer_near(x, y, z_flat=z_flat, old=old, new=new, z_cap=z_cap):
            if z_flat is not None and terrain.inside_poly([[x, y]], old)[0] \
                    and not terrain.inside_poly([[x, y]], new)[0]:
                return z_flat
            return ground.surface(gr, x, y, z_cap, gone)
        walls = terrain.skirt(new, inner, lambda x, y: outer_near(x, y))
        if walls_out is not None and len(walls):
            walls_out.extend(walls)
        if key in rims:                               # a raised / lowered region: climbable along its edge
            rim = region_rim(new, inner, lambda x, y: outer_near(x, y))
            if len(rim):
                sp = terrain.ground_spec(rim, f"ED_GroundRim{k:02d}", ground_near(md, gr, new, gone), climb=True)
                sp["visible"] = False
                specs.append(sp)
                report.append(f"{key}: a climbable rim along its edge ({len(rim) // 4} piece(s))")
        if len(walls):
            specs.append(terrain.ground_spec(walls, f"ED_GroundSkirt{k:02d}", ground_near(md, gr, new, gone)))
            report.append(f"{key}: {len(walls) // 4} skirt wall piece(s) where its ground meets the ground around")
        if relocate:
            diff = terrain.difference_tris(old, new)
            if len(diff) and z_flat is not None:
                z0 = z_flat
                fill = np.concatenate([diff, np.full(diff.shape[:2] + (1,), z0)], axis=2)
                up = np.cross(fill[:, 1] - fill[:, 0], fill[:, 2] - fill[:, 0])[:, 2] > 0
                fill[~up] = fill[~up][:, ::-1]
                mat = ground_near(md, gr, old, gone)
                specs.append(terrain.ground_spec(fill, f"ED_GroundFill{k:02d}", mat))
                walls = terrain.skirt(
                    old, lambda x, y, z0=z0, new=new: None if terrain.inside_poly([[x, y]], new)[0] else z0,
                    lambda x, y, z0=z0: ground.surface(gr, x, y, z0 + 6.0, gone))
                if len(walls):
                    specs.append(terrain.ground_spec(walls, f"ED_GroundFillSkirt{k:02d}", mat))
                report.append(f"{key}: its old place made flat ground at {z0:.2f} m ({len(diff)} triangles)")
    os.replace(step, out)
    return specs


def find_plugs(md, gr, vis, moved, removed, carry, landing_extra=()):
    """As blender_edit.find_holes: where a moved / removed object was the only thing to stand on (ground.holes - an
    object's collision can plug a gap in the map's ground) and nothing covers it now: the moved objects at their
    new place, a carried building's new footprint (and a relocated one's old place, made flat ground), a raised /
    lowered / sculpted area (its ground is remade). Also where it was seen through to the ground's collision
    (visible_holes: drawn only) and where NOTHING is left above the sea (open_holes: a cleared map's raised floors,
    rampart tops, stair wells). Ground pieces count too (a hole to the sea is never wanted); nothing over 120 m. -> [(name,
    hole, material uid)] - one per level and patch (ground.split_levels)."""
    objs = [e for e, _m, _k in moved] + list(removed)
    gone = {e.uid for e in objs}
    now_tris = []
    for e, m, _k in moved:
        if e.shapes and e.shapes[0] in md.shapes:
            v, t = md.shapes[e.shapes[0]][1].astype(np.float64), md.shapes[e.shapes[0]][2]
            now_tris.append((v @ m[:3, :3] + m[3, :3])[t])
    now_tris = np.concatenate(now_tris) if now_tris else np.zeros((0, 3, 3))
    landing = [new for _k, _o, new, _T, _u in carry]
    landing += [old for _k, old, new, _T, _u in carry if terrain.overlap(old, new) < 0.9]
    landing += [np.asarray(p, np.float64) for p in landing_extra]
    flats = [(old, new, flat_level(gr, old, gone)) for _k, old, new, _T, _u in carry if terrain.overlap(old, new) < 0.9]

    def flat_override(x, y):
        for old, new, z in flats:
            if z is not None and terrain.inside_poly([[x, y]], old)[0] and not terrain.inside_poly([[x, y]], new)[0]:
                return z
        return None
    def trim(h):                                      # (what the moved objects / carried ground cover now)
        if h is not None and len(now_tris):
            keep = ~ground.covered(h["points"], h["z"], now_tris)
            h = dict(h, points=h["points"][keep], area=h["area"] * keep.mean()) if keep.any() else None
        if h is not None and landing:
            keep = ~np.any([terrain.inside_poly(h["points"], poly) for poly in landing], axis=0)
            h = dict(h, points=h["points"][keep], area=h["area"] * keep.mean()) if keep.any() else None
        return h
    fv, fg = FastHeights(vis, gone), FastHeights(gr, gone)
    found, seen = [], []
    for e in objs:
        box = object_box(md, e)
        if box is None or (box[1] - box[0])[:2].max() > 120:
            continue                                  # (the outskirts, the sea)
        h = trim(ground.holes(md, gr, e, gone) if ground.footprint(md, e) is not None else None)
        have = {tuple(np.round(p * 2).astype(int)) for p in h["points"]} if h is not None else set()
        hv = trim(visible_holes(md, gr, fv, e, gone, skip=have))
        if hv is not None:
            seen.append((e, hv))
            have |= {tuple(np.round(p * 2).astype(int)) for p in hv["points"]}
        ho = trim(open_holes(md, gr, fv, fg, e, gone, skip=have))
        if ho is not None:
            h = ho if h is None else dict(h, points=np.vstack([h["points"], ho["points"]]), area=h["area"] + ho["area"])
        if h is not None:
            found.append((e, h))
    out = []
    for es, hh0 in ground.merge_holes(seen):
        main = max(es, key=lambda e: float(np.prod((lambda b: b[1][:2] - b[0][:2])(object_box(md, e)))))
        for h in (ground.split_levels(gr, hh0, gone, override=flat_override) if flats else split_levels_fast(fg, hh0)):
            h["visible_only"] = True
            out.append((main.name, h, ground.ground_material(md, gr, h, gone)))
    for es, hh0 in ground.merge_holes(found):
        main = max(es, key=lambda e: float(np.prod((lambda b: b[1][:2] - b[0][:2])(object_box(md, e)))))
        for h in (ground.split_levels(gr, hh0, gone, override=flat_override) if flats else split_levels_fast(fg, hh0)):
            h["visible_cover"] = ground.visible_cover(vis, h, gone)
            out.append((main.name, h, ground.ground_material(md, gr, h, gone)))
    return out


class FastHeights:
    """Vertical queries over a ground.Ground / ground.Visible index for many points at once (numpy, per 4 m cell) -
    ground.heights answers one point at a time (~1 ms each in dense cells: minutes for a map cleared of
    buildings). `gone`: the owners moved / removed in this build."""

    def __init__(self, index, gone):
        self.ix = index
        self.gone_t = np.array([o in gone for o in index.owner], bool)
        self.cells = {}

    def _cell(self, key):
        c = self.cells.get(key)
        if c is None:
            ids = np.array(self.ix.grid.get(key, []), np.int64)
            T = self.ix.t[ids] if len(ids) else np.zeros((0, 3, 3))
            a = T[:, 0]
            v0, v1 = T[:, 1, :2] - a[:, :2], T[:, 2, :2] - a[:, :2]
            d = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
            ok = np.abs(d) > 1e-12
            c = (ids[ok], a[ok], v0[ok], v1[ok], d[ok], (T[ok, 1, 2] - a[ok, 2]), (T[ok, 2, 2] - a[ok, 2]))
            self.cells[key] = c
        return c

    def query(self, pts, walkable=0.5):
        """pts (P, 2) -> (point index, z, triangle) for every walkable triangle (|normal z| >= walkable) over / under
        each point."""
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        if not len(pts):
            return np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64)
        keys = np.floor(pts / self.ix.cell).astype(np.int64)
        uk, inv = np.unique(keys, axis=0, return_inverse=True)
        inv = inv.reshape(-1)
        out_p, out_z, out_t = [], [], []
        for k, (kx, ky) in enumerate(uk):
            ids, a, v0, v1, d, dz1, dz2 = self._cell((int(kx), int(ky)))
            if not len(ids):
                continue
            sel = np.nonzero(inv == k)[0]
            v2 = pts[sel][:, None, :] - a[None, :, :2]
            u = (v2[..., 0] * v1[None, :, 1] - v1[None, :, 0] * v2[..., 1]) / d
            w = (v0[None, :, 0] * v2[..., 1] - v2[..., 0] * v0[None, :, 1]) / d
            inside = (u >= -1e-6) & (w >= -1e-6) & (u + w <= 1 + 1e-6) & (np.abs(self.ix.nz[ids]) >= walkable)[None, :]
            pi, ti = np.nonzero(inside)
            out_p.append(sel[pi])
            out_z.append(a[ti, 2] + u[pi, ti] * dz1[ti] + w[pi, ti] * dz2[ti])
            out_t.append(ids[ti])
        if not out_p:
            return np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64)
        return np.concatenate(out_p), np.concatenate(out_z), np.concatenate(out_t)

    def flags(self, pts, uid, zmin=-1e9, zmax=1e9):
        """-> (mine, others): per point, a face of `uid` / of anything not gone with zmin <= z <= zmax."""
        n = len(pts)
        mine, others = np.zeros(n, bool), np.zeros(n, bool)
        pi, z, t = self.query(pts)
        ok = (z >= zmin) & (z <= zmax)
        own = self.ix.owner[t] == uid if len(t) else np.zeros(0, bool)
        mine[pi[ok & own]] = True
        others[pi[ok & ~self.gone_t[t]]] = True
        return mine, others


def split_levels_fast(fg, hole, step=0.5):
    """ground.split_levels (no override) on FastHeights: each point takes the commonest height (0.5 m) of the walkable
    collision 0.75 - 1.75 m round it (gone left out, at most 6 m over the hole), snapped to the main levels round the
    hole (each >= 15 % of every surface seen); points grouped by height and by touching (8 neighbours on the grid).
    -> [hole] one per level and patch (2 points or more)."""
    pts = np.asarray(hole["points"], np.float64)
    top = hole["z"] + 6.0
    ang = np.linspace(0, 2 * np.pi, 8, endpoint=False)
    offs = np.array([(r * np.cos(t), r * np.sin(t)) for r in (0.75, 1.25, 1.75) for t in ang])
    S = (pts[:, None, :] + offs[None]).reshape(-1, 2)
    pi, z, t = fg.query(S)
    ok = (fg.ix.nz[t] >= 0.5) & ~fg.gone_t[t] & (z <= top + 0.1)
    best = np.full(len(S), -np.inf)
    np.maximum.at(best, pi[ok], z[ok])
    best = best.reshape(len(pts), len(offs))
    zs, every = np.empty(len(pts)), []
    for k in range(len(pts)):
        near = np.round(best[k][np.isfinite(best[k])] * 2) / 2
        every.append(near)
        if len(near):
            vals, counts = np.unique(near, return_counts=True)
            zs[k] = vals[counts.argmax()]
        else:
            zs[k] = round(hole["z"] * 2) / 2
    every = np.concatenate(every) if every else np.zeros(0)
    if len(every):
        vals, counts = np.unique(every, return_counts=True)
        main = vals[counts >= 0.15 * len(every)]
        if len(main):
            zs = main[np.abs(zs[:, None] - main[None, :]).argmin(1)]
    cells = np.rint((pts - pts.min(0)) / step).astype(np.int64)
    out = []
    for lvl in np.unique(zs):
        idx = np.nonzero(zs == lvl)[0]
        at = {(int(cells[i, 0]), int(cells[i, 1])): i for i in idx}
        seen = set()
        for i in idx:
            c0 = (int(cells[i, 0]), int(cells[i, 1]))
            if c0 in seen:
                continue
            comp, todo = [], [c0]
            seen.add(c0)
            while todo:
                c = todo.pop()
                comp.append(at[c])
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        n = (c[0] + dx, c[1] + dy)
                        if n in at and n not in seen:
                            seen.add(n)
                            todo.append(n)
            if len(comp) >= 2:
                out.append({"points": pts[comp], "z": float(lvl), "area": len(comp) * step * step})
    return out


def _ring(gr, lo, hi, gone, floor=None):
    ring = []
    for x in np.arange(lo[0] - 1.0, hi[0] + 1.01, 1.0):
        for y in (lo[1] - 0.75, hi[1] + 0.75):
            ring.append(ground.surface(gr, x, y, hi[2], gone))
    for y in np.arange(lo[1] - 1.0, hi[1] + 1.01, 1.0):
        for x in (lo[0] - 0.75, hi[0] + 0.75):
            ring.append(ground.surface(gr, x, y, hi[2], gone))
    return [z for z in ring if z is not None and (floor is None or z > floor)]


def _grid(lo, hi, step, skip):
    xs, ys = np.arange(lo[0] + step / 2, hi[0], step), np.arange(lo[1] + step / 2, hi[1], step)
    if not len(xs) or not len(ys):
        return np.zeros((0, 2))
    g = np.array(np.meshgrid(xs, ys, indexing="ij")).reshape(2, -1).T
    if skip:
        keep = np.array([(int(round(x * 2)), int(round(y * 2))) not in skip for x, y in g], bool)
        g = g[keep]
    return g


def open_holes(md, gr, fv, fg, e, gone, skip=None, step=0.5, floor=-5.0):
    """Where e was drawn or solid and nothing else - no model, no collision, not moved / removed - is left above `floor`
    (the sea plane): you'd see and fall through to the water - e.g. a cleared map's raised yards, rampart tops and
    stair wells, outside ground.holes' window round the ground's height. fv / fg: FastHeights over
    the visible models / the collision; skip: (round(2x), round(2y)) already found. The height is the ground's round it
    (ground.split_levels gives each patch its own). -> dict(points, z, area) or None."""
    box = object_box(md, e)
    if box is None:
        return None
    lo, hi = box
    ring = _ring(gr, lo, hi, gone, floor)
    if not ring:
        return None
    g = _grid(lo, hi, step, skip)
    if not len(g):
        return None
    mv, ov = fv.flags(g, e.uid, zmin=floor)
    mg, og = fg.flags(g, e.uid, zmin=floor)
    miss = g[(mv | mg) & ~ov & ~og]
    if not len(miss):
        return None
    return {"points": miss, "z": float(np.median(ring)), "area": len(miss) * step * step}


def visible_holes(md, gr, fv, e, gone, skip=None, step=0.5):
    """ground.holes for the visible models: where e's model was what you saw at ground level and no other visible model
    (not moved / removed) is there - the ground's collision may run on under a building whose floor alone was drawn
    (grey see-through rectangles where cleared buildings stood). -> dict(points, z, area) or None."""
    if not e.mesh or e.mesh not in md.meshes:
        return None
    m = np.array(e.matrix, dtype=np.float64).reshape(4, 4)
    w = md.meshes[e.mesh][1].astype(np.float64) @ m[:3, :3] + m[3, :3]
    lo, hi = w.min(0), w.max(0)
    if (hi - lo)[:2].max() > 120:
        return None
    ring = _ring(gr, lo, hi, gone)
    if not ring:
        return None
    z0 = float(np.median(ring))
    if lo[2] > z0 + 0.5:
        return None
    z_lo, z_hi = float(np.percentile(ring, 10)) - 1.0, float(np.percentile(ring, 90)) + 0.5
    g = _grid(lo, hi, step, skip)
    if not len(g):
        return None
    mine, others = fv.flags(g, e.uid, zmin=z_lo, zmax=z_hi)
    miss = g[mine & ~others]
    if not len(miss):
        return None
    return {"points": miss, "z": z0, "area": len(miss) * step * step}


def plug_specs(plugs):
    """[(name, hole, material)] -> newmodel specs: a slab shaped like each hole (ground.fill_cells), in the ground's
    material 5 cm proud (flush, it flickers: depth ties), or - where the visible ground is still there (>= 80 %) and only
    the collision is missing - an invisible one flush."""
    specs = []
    for k, (name, h, mat) in enumerate(plugs):
        collision_only = h.get("visible_cover", 0) >= 0.8 and not h.get("visible_only")
        c, v, quads = ground.fill_cells(h, lift=0.0 if collision_only else 0.05)
        w = v + c
        tris = [(w[a], w[b], w[cc]) for a, b, cc, _d in quads] + [(w[a], w[cc], w[d]) for a, _b, cc, d in quads]
        sp = terrain.ground_spec(np.array(tris), f"ED_Plug{k:03d}", mat)
        if h.get("visible_only") and mat is not None:     # (drawn only: one buried triangle as its collision)
            sp["collision"] = (np.array([[0.0, 0.0, -50.0], [0.1, 0.0, -50.0], [0.0, 0.1, -50.0]]), np.array([[0, 1, 2]]))
        elif collision_only or mat is None:
            sp["visible"] = False
        specs.append(sp)
    return specs


def mapdata_title(path):
    from maps import title  # studio/engine/maps.py
    return title(path)


# ------------------------------------------------------------------------------------------- read-back --

def verify(req, dest, log=print, carry=None, moved=None, placed=None, added=None, riders=None, dropped=None, plugs=None):
    """Reads the build back (mapdata, not cached) and checks every edit landed: moved objects at their new place,
    removed ones gone from view, copies present, nothing else moved but spawn points; library models (`placed`:
    build()'s list) at their place with their model, its textures and collision, and every climb system they carry
    as the engine reads it (climb.check); with `carry` (build's _carry), the holes the carried buildings left
    (holes.py); `riders` (build's _riders: objects riding a raised / lowered region -> their new matrix).
    -> (ok, lines, holes found or None)."""
    src = os.path.abspath(req["archive"])
    md0 = ground_of(src)[0]
    md1 = mapdata.load(dest, use_cache=False, log=lambda *_a: None)
    _drop_cache(dest)
    lines, ok = [], True
    before = {(e.uid, e.record): m4(e.matrix) for e in md0.entities}
    after = {(e.uid, e.record): m4(e.matrix) for e in md1.entities}
    want = {bytes.fromhex(mv["uid"]): m4(mv["matrix"]) for mv in req.get("moves", [])}
    bad = [u.hex() for u, m in want.items()
           if not any(k[0] == u and np.allclose(v, m, atol=1e-3) for k, v in after.items())]
    if bad:
        ok = False
        lines.append(f"FAIL: {len(bad)} moved object(s) not at their new place: {', '.join(bad[:5])}")
    else:
        lines.append(f"ok: {len(want)} moved object(s) at their new place")
    if riders:
        off = [u.hex() for u, m in riders.items()
               if not any(k[0] == u and np.allclose(v, m, atol=1e-3) for k, v in after.items())]
        if off:
            ok = False
            lines.append(f"FAIL: {len(off)} object(s) on a raised / lowered region or part of a gameplay object not moved "
                         f"with it")
        else:
            lines.append(f"ok: {len(riders)} object(s) moved with what they belong to (a raised / lowered region, a "
                         f"gameplay object's parts and FX)")
        want = {**want, **riders}
    gone = [bytes.fromhex(h) for h in req.get("removes", [])] + sorted(dropped or ())   # (+ a pit's edge decals)
    still = [u.hex() for u in gone
             if any(k[0] == u and np.allclose(v, before.get(k, v * 0), atol=1e-3) for k, v in after.items())]
    if still:
        ok = False
        lines.append(f"FAIL: {len(still)} removed object(s) still in place")
    elif gone:
        lines.append(f"ok: {len(gone)} removed object(s) out of the map")
    for c in req.get("copies", []):
        m = m4(c["matrix"])
        if not any(np.allclose(v, m, atol=1e-3) for k, v in after.items() if k not in before):
            ok = False
            lines.append(f"FAIL: the copy of {c['uid']} isn't in the build")
    if req.get("copies"):
        lines.append(f"copies checked: {len(req['copies'])}")
    others = [k for k in before if k in after and k[0] not in want and k[0] not in gone
              and not np.allclose(before[k], after[k], atol=1e-3)]
    spawns_moved = [k for k in others if k[1].startswith("DataBlock_")]
    rest = [k for k in others if not k[1].startswith("DataBlock_")]
    if spawns_moved:
        lines.append(f"{len({k[0] for k in spawns_moved})} spawn point(s) followed")
    if rest:
        ok = False
        lines.append(f"FAIL: {len(rest)} object(s) moved that weren't edited")
    chunks = [e.name for e in md1.entities if e.name.startswith("ED_Ground")]
    if chunks:
        lines.append(f"{len(chunks)} carried-ground / skirt object(s) added")
    if placed:
        good, more = check_placed(md1, dest, placed)
        ok &= good
        lines += more
    if added:
        have = {e.name for e in md1.entities}
        missing = [a for a in added if a not in have]
        if missing:
            ok = False
            lines.append(f"FAIL: {len(missing)} added model(s) not in the build: {', '.join(missing[:4])}")
        climbers = [a for a in added if a.startswith("ED_Climb")]
        if climbers:
            from ac3editor import climb
            sys.path.insert(0, os.path.join(REPO, "tools"))
            import climb_census
            import climbout
            n_sys = n_edges = 0
            out_sides = {}
            for name, f in climb_census.systems(dest):
                if name in climbers:
                    probs, _w, st = climb.check(f)
                    n_sys += 1
                    n_edges += st["edges"]
                    if probs:
                        ok = False
                        lines.append(f"FAIL: {name}'s climb data: {'; '.join(probs[:2])}")
                    if name.startswith("ED_ClimbOut"):     # (a layer is only moved, not turned: its normals are world)
                        _pts, es = climb.read(f)
                        n1 = np.array([e[4] for e in es]).reshape(-1, 3)
                        out_sides[name] = {k: int((climbout.sides(n1) == k).sum()) if len(n1) else 0
                                           for k in ("north", "east", "south", "west")}
            if n_edges == 0:
                ok = False
                lines.append("FAIL: the objects made climbable have no climb edges")
            else:
                lines.append(f"ok: {len(climbers)} object(s) made climbable: {n_sys} climb system(s), {n_edges} edge(s)")
            for name, by in sorted(out_sides.items()):
                lines.append(f"ok: climbable outside {name}: {sum(by.values())} ledge(s) on its outer walls ("
                             + ", ".join(f"{k} {v}" for k, v in by.items()) + ")")
            if any(a.startswith("ED_ClimbOut") for a in climbers) and not any(out_sides.values()):
                ok = False
                lines.append("FAIL: the climbable-outside layer(s) have no climb data")
        grounds = [a for a in added if a.startswith("ED_LibGround")]
        if grounds and not missing:
            lines.append(f"ok: {len(grounds)} ground model(s) on their own map")
    for line in lines:
        log(line)
    if req.get("sculpts"):
        import sculpt
        st = sculpt.strokes_of(req)
        if st:
            s_ok, s_lines = sculpt.check(ground_of(src)[0], ground_of(src)[1], md1, ground.Ground(md1), st)
            ok = ok and s_ok
            lines += s_lines
            for line in s_lines:
                log(line)
    if req.get("routes"):
        import navview
        r_ok, r_lines = navview.check_routes(dest, req["routes"])
        ok = ok and r_ok
        lines += r_lines
    if req.get("spawns") or req.get("boundaries"):
        import spawnview
        s_ok, s_lines = spawnview.verify(src, dest, req)
        ok = ok and s_ok
        lines += s_lines
        for line in s_lines:
            log(line)
    import mapsettings
    if mapsettings.wants(req.get("map_settings")):
        m_ok, m_lines = mapsettings.verify(src, dest, req["map_settings"])
        ok = ok and m_ok
        lines += m_lines
        for line in m_lines:
            log(line)
    if plugs:
        g1 = ground.Ground(md1)
        miss = 0
        for pts, z, kind in plugs:
            if kind == "seen":
                continue                              # (the ground's own collision under it)
            for x, y in pts[:: max(1, len(pts) // 12)]:
                if not any(abs(zz - z) <= 0.1 for zz, _o in g1.heights(x, y, up_only=True)):
                    miss += 1
        if miss:
            ok = False
            lines.append(f"FAIL: {miss} point(s) of the plugged holes with nothing to stand on")
        else:
            lines.append(f"ok: {len(plugs)} plugged hole(s) - something to stand on at the ground's height")
        log(lines[-1])
    found = None
    if carry:
        import holes
        md0, g0, v0 = ground_of(src)
        found = holes.find(md0, g0, v0, md1, carry, moved or set())
        for line in holes.summary(found):
            log(line)
            lines.append(line)
    return ok, lines, found


def check_placed(md1, dest, placed):
    """The library models in a read-back build -> (ok, lines). A model is written as one object per material (and a
    big one cut in pieces): <name>, <name>_1, <name>_2 ... all at its place."""
    from ac3editor import climb
    pieces = {}
    for e in md1.entities:
        head, _sep, tail = e.name.rpartition("_")
        for key in {e.name, head if tail.isdigit() else e.name}:
            pieces.setdefault(key, []).append(e)
    ok, lines, n_tex, n_mat, n_col, n_pieces = True, [], 0, 0, 0, 0
    for p in placed:
        ents = [e for e in pieces.get(p["name"], []) if e.name == p["name"] or e.name[len(p["name"]) + 1:].isdigit()]
        ents = list({e.uid: e for e in ents}.values())
        if not ents:
            ok = False
            lines.append(f"FAIL: library model {p['name']} isn't in the build")
            continue
        n_pieces += len(ents)
        first = next((e for e in ents if e.name == p["name"]), ents[0])
        if not np.allclose(m4(first.matrix), m4(p["matrix"]), atol=1e-3):
            ok = False
            lines.append(f"FAIL: {p['name']} isn't at its place")
        for e in ents:
            if e.mesh not in md1.meshes:
                ok = False
                lines.append(f"FAIL: {e.name} shows no model")
                continue
            for m in set(md1.mesh_extra[e.mesh][2]) if e.mesh in md1.mesh_extra else ():
                n_mat += 1
                path = md1.materials.get(m, ("", None))[1]
                n_tex += bool(path and os.path.exists(path))
        n_col += (not p.get("solid", True)) or any(s in md1.shapes and len(md1.shapes[s][2]) > 1
                                                    for e in ents for s in e.shapes)
    if n_tex < n_mat:
        ok = False
        lines.append(f"FAIL: {n_mat - n_tex} of the placed models' {n_mat} material(s) read back without a texture")
    if n_col < len(placed):
        ok = False
        lines.append(f"FAIL: {len(placed) - n_col} placed model(s) without collision")
    if ok:
        lines.append(f"ok: {len(placed)} library model(s) at their place ({n_pieces} object(s)), {n_tex} of "
                     f"{n_mat} material(s) with their texture, collision on each one that has its own")
    # their climb systems (the pieces a big model is cut into are named <name>_<k>)
    names = tuple(p["name"] for p in placed)
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import climb_census
    systems = edges = 0
    for name, f in climb_census.systems(dest):
        if not name.startswith(names):
            continue
        probs, _warns, stats = climb.check(f)
        systems += 1
        edges += stats["edges"]
        if probs:
            ok = False
            lines.append(f"FAIL: {name}'s climb data: {'; '.join(probs[:2])}")
    lines.append(f"climb: {systems} system(s), {edges} edge(s) on the placed models, all as the engine reads them"
                 if ok else f"climb: {systems} system(s), {edges} edge(s)")
    return ok, lines


def _pickles(archive):
    """mapdata's cache files of archives named as this one."""
    base = os.path.basename(archive) + "_"
    return {f for f in os.listdir(mapdata.CACHE) if f.startswith(base) and f.endswith(".pkl")}         if os.path.isdir(mapdata.CACHE) else set()


def _drop_cache(archive):
    """mapdata.load writes a pickle per archive version: a build's read-back needn't keep one."""
    st = os.stat(archive)
    p = os.path.join(mapdata.CACHE, f"{os.path.basename(archive)}_{st.st_size}_{int(st.st_mtime)}"
                                    f"_v{mapdata.CACHE_VERSION}.pkl")
    if os.path.exists(p):
        os.remove(p)
