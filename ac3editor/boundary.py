"""Map boundaries: each game mode's out-of-bounds area - and the gameplay zones (Domination's areas, Artifact
Assault's team territories: GameplayZoneComponent) - read as polygons and rebuilt from new ones.

Zones (measured on Wolcott): `Shapes`, each a CLOSED polygon whose `Sections` are its whole edges in no order
(GlobalPosition = edge centre, GlobalRotation / GlobalNormal as the out-of-bounds sections - normal = local y,
INWARD on all 10 shapes -, SideVector = local x along the edge, Size = (length, 60), Base / Distant / BaseToDistant
zero) with `FXSections` - the edge in equal panels of <= 10 m (GlobalPosition, Size). Mesh / MeshShape: the walls
+-30 m (material AC3MP_MAT_DominationBorder, v = z / 3 - 1). The game tests membership shape by shape, each a CONVEX
piece: an Artifact Assault territory is cut into several, and a cut (an edge two shapes share) has a section but no FX
panels and no wall (stock Wolcott's territories: 4 and 6 cuts; the ported maps' AA territories and the Switched
blocks' are cut into convex pieces everywhere). Those have no InertComponent
(no MeshShape: membership never reads it); their wall height comes from the wall mesh.

Measured on Fort Wolcott:

* Every game-mode block (DataBlock_ACFE_<mode>) holds one out-of-bounds Entity: InertComponent + OutOfBoundsComponent
  + Visual. Wolcott has two: `Proto_AC3MP_OOB_iteration1_001` (the same uid in 11 mode blocks) and
  `Proto_AC3MP_OOB_DEATHMATCH_001` (Deathmatch, Easy Deathmatch). Each block also holds its own copy of the area's
  Mesh, MeshShape, `AC2MP_Oob` Material and OutOfBoundsSettings.
* The area is (on 34 of the 36 areas of the 20 maps) a closed polygon of vertical walls round the playable ground (Wolcott's: 28 walls on the inside of the
  fort walls; every spawn inside). OutOfBoundsComponent.Sections: one per wall PANEL - each wall cut into equal
  panels about 4.7 m wide; Forli's and Iceberg's main areas are OPEN chains (each wall's inside: the nearest stock
  panel's normal) - with GlobalPosition (the panel's centre, world, z = the entity's), GlobalRotation (a
  quaternion x, y, z, w turning local +y onto the inward normal; local x runs along the wall), GlobalNormal (inward,
  horizontal), Size (panel width, 60). The game drains a player's health while the nearest section is far / on the
  wrong side - with no area at all it kills at once.
* (Constantinople, Antioch, Jerusalem, Dyers, Imperial: the wall Mesh also has copies in Cell00084 and in a record of
  its own - rebuild rewrites every copy.)
* The Mesh (Visual -> Mesh directly; vertex format 4, no baked shading) and the MeshShape are the same walls from
  -30 to +30 m in the entity's frame: collision faces point INWARD; mesh UVs u = distance along the walls / 10,
  v = z / 10 - 1 (|u| < 16: the int16 / 2048 storage - each wall restarts u, keeping its phase).
"""
import copy
import struct

import numpy as np

from . import mapdata as M  # first: puts forgelib and ac3codec on the path
from . import collision, meshenc
from .newmodel import _comp
from datafile import compress, decompress  # noqa: E402  (forgelib)
from forge_write import ArchiveImage, set_payload, write  # noqa: E402  (forgelib)
from transplant import object_at  # noqa: E402  (forgelib)

PANEL = 4.7          # stock panel width (4.57-4.71 m on Wolcott)
HEIGHT = 30.0        # walls from -HEIGHT to +HEIGHT about the entity


def corners_of(shape_verts, shape_tris, matrix):
    """A stock area's collision -> (its corners in world x, y, closed). A closed polygon doesn't repeat its first
    corner; an open chain (Forli's strip, Iceberg's main area: 2 loose ends) runs end to end."""
    v = np.asarray(shape_verts, np.float64)
    m = np.asarray(matrix, np.float64).reshape(4, 4)
    top = [i for i in range(len(v)) if v[i, 2] > 0]
    # weld corners within 5 cm (the stock seam corner is stored twice, a hair apart)
    reps, key = [], {}
    for i in top:
        j = next((k for k, r in enumerate(reps) if np.linalg.norm(v[i, :2] - r) < 0.05), None)
        if j is None:
            reps.append(v[i, :2])
            j = len(reps) - 1
        key[i] = j
    adj = {}
    for tr in shape_tris:
        for k in range(3):
            a, b = int(tr[k]), int(tr[(k + 1) % 3])
            if a in key and b in key and key[a] != key[b]:
                adj.setdefault(key[a], set()).add(key[b])
                adj.setdefault(key[b], set()).add(key[a])
    ends = sorted(k for k, s in adj.items() if len(s) == 1)
    start = ends[0] if ends else min(adj)
    chain, prev = [start], None
    while True:
        nxt = sorted(b for b in adj[chain[-1]] if b != prev and b not in chain)
        if not nxt:
            break
        prev = chain[-1]
        chain.append(nxt[0])
    w = np.array([reps[k] for k in chain], np.float64) @ m[:2, :2] + m[3, :2]
    return w, not ends


def signed_area(p):
    p = np.asarray(p, np.float64)
    return 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]))


def inside(p, poly):
    """Point-in-polygon (x, y)."""
    x, y = p[0], p[1]
    n = len(poly)
    c = False
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            c = not c
    return c


def walls(corners, closed=True, ref=None):
    """Corners (world x, y) -> [(a, b, inward normal (x, y))] per wall. A closed polygon's inside comes from its
    winding; an open chain's from `ref` = (stock section positions (n, 2), inward normals (n, 2)): each wall faces
    the way the stock panel nearest its middle faces."""
    p = np.asarray(corners, np.float64)
    ccw = signed_area(p) > 0
    out = []
    side = 1.0
    for i in range(len(p) if closed else len(p) - 1):
        a, b = p[i], p[(i + 1) % len(p)]
        d = b - a
        L = np.linalg.norm(d)
        if L < 1e-3:
            continue
        left = np.array([-d[1], d[0]]) / L
        if closed:
            n = left if ccw else -left
        else:
            if ref is not None and len(ref[0]):
                k = int(np.linalg.norm(ref[0] - (a + b) / 2, axis=1).argmin())
                side = 1.0 if np.dot(left, ref[1][k]) >= 0 else -1.0
            n = left * side
        out.append((a, b, n))
    return out


def inside_area(p, corners, closed=True, ref=None):
    """-> (inside?, distance to the nearest wall) for a world point: in a closed polygon, or (an open chain) on the
    inward side of its nearest wall."""
    p = np.asarray(p, np.float64)[:2]
    best = None
    for a, b, n in walls(corners, closed, ref):
        t = np.clip(np.dot(p - a, b - a) / np.dot(b - a, b - a), 0, 1)
        q = a + t * (b - a)
        d = float(np.linalg.norm(p - q))
        if best is None or d < best[0]:
            best = (d, float(np.dot(p - q, n)))
    if best is None:
        return False, 0.0
    return (inside(p, corners) if closed else best[1] >= 0), best[0]


def sections(corners, z, closed=True, ref=None, height=2 * HEIGHT):
    """-> [(position (4), quaternion (4), normal (4), size (2))]: each wall cut into equal panels of <= PANEL m."""
    out = []
    for a, b, n in walls(corners, closed, ref):
        L = np.linalg.norm(b - a)
        k = max(1, int(np.ceil(L / PANEL - 1e-6)))
        th = np.arctan2(-n[0], n[1])                     # R_z(th) . (0, 1, 0) = n
        q = (0.0, 0.0, float(np.sin(th / 2)), float(np.cos(th / 2)))
        for j in range(k):
            c = a + (b - a) * (j + 0.5) / k
            out.append(((float(c[0]), float(c[1]), float(z), 0.0), q, (float(n[0]), float(n[1]), 0.0, 0.0),
                        (float(L / k), float(height))))
    return out


def wall_geometry(corners, m4, closed=True, ref=None, zlo=-HEIGHT, zhi=HEIGHT, uvs=10.0, skip=()):
    """-> (collision verts (n, 3), tris inward-facing, mesh verts (per corner), mesh tris, mesh uvs) in the entity's
    frame (rotation + translation m4, AC3 row-vector); walls from zlo to zhi in that frame (stock: +-30; Forli's
    -11.5 .. 25.5, Iceberg's -15 .. 39). `skip`: indices into walls() left without a wall (a zone's internal edges)."""
    inv = np.linalg.inv(m4)
    to_local = lambda p: np.asarray(p, np.float64) @ inv[:3, :3] + inv[3, :3]  # noqa: E731
    cv, ct, mv, mt, uv = [], [], [], [], []
    run = 0.0
    # v = z / uvs - 1; a wall too tall for |v| < 16 (Martinique's zones, -20 .. 52.7 m at 3 m a repeat: 16.6) loses
    # whole repeats - the pattern's phase kept - toward its middle (stock Martinique centres v there too)
    vlo, vhi = zlo / uvs - 1, zhi / uvs - 1
    if max(abs(vlo), abs(vhi)) >= 15.99:
        mid = float(np.round((vlo + vhi) / 2))
        vlo, vhi = vlo - mid, vhi - mid
    for w, (a, b, n) in enumerate(walls(corners, closed, ref)):
        if w in skip:
            continue
        L = float(np.linalg.norm(b - a))
        z0, z1 = m4[3, 2] + zlo, m4[3, 2] + zhi
        quad = to_local([(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)])
        k = len(cv)
        # inward-facing: (b - a) x up points right of travel; flip the winding when inward is to the left
        f = np.cross(np.r_[b - a, 0.0], [0.0, 0.0, 1.0])[:2]
        tris = [(0, 1, 2), (0, 2, 3)] if np.dot(f, n) > 0 else [(0, 2, 1), (0, 3, 2)]
        cv.extend(quad)
        ct.extend([(k + i, k + j, k + l) for i, j, l in tris])
        # the mesh: the wall in pieces of <= 40 m, u restarting on each (keeping the pattern's phase) - UVs are stored
        # as int16 / 2048, so |u| < 16: u run along the whole outline clips past 160 m (stretched stripes)
        pieces = max(1, int(np.ceil(L / 40.0)))
        for j in range(pieces):
            pa, pb = a + (b - a) * j / pieces, a + (b - a) * (j + 1) / pieces
            km = len(mv)
            mv.extend(to_local([(pa[0], pa[1], z0), (pb[0], pb[1], z0), (pb[0], pb[1], z1), (pa[0], pa[1], z1)]))
            mt.extend([(km + i, km + j2, km + l) for i, j2, l in tris])
            u0 = (run / uvs) % 1.0
            u1 = u0 + L / pieces / uvs
            uv.extend([(u0, vlo), (u1, vlo), (u1, vhi), (u0, vhi)])
            run += L / pieces
    # a convex piece in a territory's middle (every edge a cut) has no wall at all
    return (np.array(cv).reshape(-1, 3), np.array(ct, np.int64).reshape(-1, 3), np.array(mv).reshape(-1, 3),
            np.array(mt, np.int64).reshape(-1, 3), np.array(uv).reshape(-1, 2))


ZONE_UV = 3.0       # zone walls (AC3MP_MAT_DominationBorder): v = z / 3 - 1 on Wolcott's Domination zones
FX_PANEL = 10.0     # zone FX panels: each edge cut into equal panels of <= 10 m (67.3 m -> 7, 30.0 -> 4)


def internal_edges(polys, tol=0.1):
    """A zone's shapes (closed polygons) -> per shape, the indices into walls(shape) of its INTERNAL edges (cuts): those
    another shape of the zone backs - facing the other way, the edge's middle on it (within `tol` m; the maps' cuts
    meet within 1 mm, and one may meet two edges of its neighbour, a T: AC4's Pirate HQ). A territory is cut into
    convex shapes (the exe's membership test is per convex shape); the cuts carry sections but, on AC3's own maps, no
    wall and no FX panels (stock Fort Wolcott's AA territories: 4 and 6 such edges; ported layouts': every cut).
    AC4's Pirate HQ draws both on two short ones - rebuild keeps what the map does on its own edges."""
    ws = [[(a, b, n) for a, b, n in walls(np.asarray(p, np.float64))] for p in polys]

    def dist(a, b, p):
        t = np.clip(np.dot(p - a, b - a) / max(np.dot(b - a, b - a), 1e-12), 0, 1)
        return float(np.linalg.norm(p - (a + t * (b - a))))
    out = []
    for k, wk in enumerate(ws):
        out.append({i for i, (a, b, n) in enumerate(wk)
                    if any(j != k and np.dot(n, m) < -0.9 and dist(c, d, (a + b) / 2) < tol
                           for j, wj in enumerate(ws) for c, d, m in wj)})
    return out


def convex(poly, tol=0.25):
    """A closed polygon convex as the game sees a zone shape: every corner on the inner side of every edge's line
    (within `tol` m - stock Mount Vernon's territory bends 0.17 degrees along a 99 m edge, 0.1 m)."""
    p = np.asarray(poly, np.float64)
    return all(float(((p - a) @ n).min()) >= -tol for a, _b, n in walls(p))


def zone_quiet(gz_fields, polys):
    """A zone's shapes -> per shape, the indices into walls(shape) of the edges the map gives no FX panels: its cuts,
    and on the DLC maps (Charlestown, Martinique) some walled edges too - the line one team's territory shares with
    the other's (the other draws it)."""
    out = []
    for sh, p in zip(gz_fields["Shapes"], polys):
        ws = walls(p)
        q = set()
        for x in M._obj(sh).fields["Sections"]:
            f = M._obj(x).fields
            if f["FXSections"]:
                continue
            c = np.array(struct.unpack("<4f", f["GlobalPosition"])[:2])
            k = min(range(len(ws)), key=lambda i: np.linalg.norm((ws[i][0] + ws[i][1]) / 2 - c), default=None)
            if k is not None and np.linalg.norm((ws[k][0] + ws[k][1]) / 2 - c) < 0.5:
                q.add(k)
        out.append(q)
    return out


def zone_polygons(gz_fields):
    """A GameplayZoneComponent -> its shapes as closed polygons (world x, y): each section is one whole edge (centre
    GlobalPosition, along SideVector, length Size[0]), stored in no order - stitched end to end."""
    out = []
    for sh in gz_fields["Shapes"]:
        edges = []
        for x in M._obj(sh).fields["Sections"]:
            f = M._obj(x).fields
            p = np.array(struct.unpack("<4f", f["GlobalPosition"])[:2])
            sv = np.array(struct.unpack("<4f", f["SideVector"])[:2])
            L = struct.unpack("<2f", f["Size"])[0]
            edges.append((p - sv * L / 2, p + sv * L / 2))
        if not edges:
            continue
        loop = [edges.pop(0)]
        while edges:
            end = loop[-1][1]
            k = next((i for i, e in enumerate(edges)
                      if np.linalg.norm(e[0] - end) < 0.5 or np.linalg.norm(e[1] - end) < 0.5), None)
            if k is None:
                break
            a, b = edges.pop(k)
            loop.append((a, b) if np.linalg.norm(a - end) < 0.5 else (b, a))
        out.append(np.array([a for a, _b in loop]))
    return out


def areas(archive):
    """-> {entity uid: dict(name, records [mode block names], matrix (4x4), corners (k, 2) world, closed, ref (stock
    section positions, normals), zlo / zhi (its walls' height range, entity frame), height (section size), mesh,
    shape)} for every out-of-bounds area of a map."""
    S = M.schema()
    C = M.Codec(S)
    out, shapes, meshes = {}, {}, {}
    for rec, name, t, body, _f in M.iter_objects_ex(archive):
        cn = S.name_of(t)
        if cn == "MeshShape" and rec.startswith("DataBlock_"):
            shapes.setdefault(body[2:6], body)
        if cn == "Mesh" and rec.startswith("DataBlock_"):
            meshes.setdefault(body[2:6], body)
        if cn != "Entity" or not rec.startswith("DataBlock_"):
            continue
        try:
            o = C.decode(body).obj
        except M.DecodeError:
            continue
        oc = _comp(S, o, "OutOfBoundsComponent")
        gz = _comp(S, o, "GameplayZoneComponent") if oc is None else None
        if oc is None and gz is None:
            continue
        a = out.setdefault(o.id, dict(name=name, records=[], matrix=collision._matrix(o.fields),
                                      kind="oob" if oc is not None else "zone"))
        a["records"].append(rec)
        if gz is not None:
            if "polygons" not in a:
                a["polygons"] = zone_polygons(gz.fields)
                a["quiet"] = zone_quiet(gz.fields, a["polygons"])
                a["corners"], a["closed"], a["ref"] = a["polygons"][0], True, None
                sec0 = M._obj(M._obj(gz.fields["Shapes"][0]).fields["Sections"][0]).fields
                a["height"] = struct.unpack("<2f", sec0["Size"])[1]
            # ported layouts' territories (the ported maps' Artifact Assault, AC3's Switched blocks) have no
            # InertComponent: the game's membership test reads only the sections, so they ship without collision
            inert = _comp(S, o, "InertComponent")
            a["shape"] = M._link(inert.fields["RigidBody"].fields["Shape"]) if inert is not None else None
            a["mesh"] = _comp(S, o, "Visual").fields["Object"].id
            if a["shape"] is None:
                bv = o.fields["BoundingVolume"].fields
                a["bv_z"] = (struct.unpack("<3f", bv["Min"])[2], struct.unpack("<3f", bv["Max"])[2])
            continue
        if "ref" not in a:
            sec = [M._obj(x).fields for x in oc.fields["Sections"]]
            a["ref"] = (np.array([struct.unpack("<4f", f["GlobalPosition"])[:2] for f in sec]).reshape(-1, 2),
                        np.array([struct.unpack("<4f", f["GlobalNormal"])[:2] for f in sec]).reshape(-1, 2))
            a["height"] = struct.unpack("<2f", sec[0]["Size"])[1] if sec else 2 * HEIGHT
        inert = _comp(S, o, "InertComponent")
        a["shape"] = M._link(inert.fields["RigidBody"].fields["Shape"])
        a["mesh"] = _comp(S, o, "Visual").fields["Object"].id
    for uid, a in out.items():
        if a["shape"] is None:
            # a zone without collision: the wall height from its wall mesh (the entity's frame), else its bounds
            mv = M.decode_mesh(C.decode(meshes[a["mesh"]]).obj.fields) if a["mesh"] in meshes else "absent"
            a["zlo"], a["zhi"] = ((float(mv[0][:, 2].min()), float(mv[0][:, 2].max())) if not isinstance(mv, str)
                                  else a["bv_z"])
            a.pop("bv_z", None)
            continue
        root = C.decode(shapes[a["shape"]])
        v, t, _m = collision._shape_arrays(root.obj.fields)
        if a["kind"] == "oob":
            a["corners"], a["closed"] = corners_of(v, t, a["matrix"])
            a["polygons"] = [a["corners"]]
        a["zlo"], a["zhi"] = float(v[:, 2].min()), float(v[:, 2].max())
    return out


def spawn_problems(md, area, corners, clear=3.0):
    """Spawn points of the area's modes (mapdata entities named SPW / Spawn in its mode blocks) outside the new
    outline, or within `clear` m of it AND closer than the map's own outline has them (stock Galata keeps three Hijack
    spawns 1.6 - 2.7 m from its wall) - players spawning outside would be killed out of bounds."""
    bad = []
    if area.get("kind") == "zone":
        return []                            # a gameplay zone doesn't kill: its spawns may lie anywhere
    for e in md.entities:
        if e.record not in area["records"] or not ("SPW" in e.name or "Spawn" in e.name):
            continue
        p = np.array(e.matrix, dtype=np.float64).reshape(4, 4)[3, :2]
        ok, d = inside_area(p, corners, area["closed"], area["ref"])
        if ok and d < clear:
            _ok0, d0 = inside_area(p, area["corners"], area["closed"], area["ref"])
            ok = d >= d0 - 0.01
        if not ok:
            bad.append(e.name)
    return sorted(set(bad))


def rebuild(src, dest, polygons, log=print, quiet=None):
    """polygons: {area entity uid: corners (k, 2) world - closed or an open chain, as the area was; for a gameplay
    zone (Domination / Artifact Assault): a list of closed polygons, one per shape}. quiet: {zone uid: per shape, the
    wall indices to leave without FX} (territories.redivide's; default: as the map had them, zone_quiet). Rewrites, in every record holding them,
    the area's Entity (sections, BoundingVolume), its MeshShape (walls, our MOPP) and its Mesh (walls, UVs as stock).
    Ids stay. -> same (every other record byte-identical)."""
    S = M.schema()
    C = M.Codec(S)
    cls = {S.name_of(h): h for h in S.types_by_hash}
    image = ArchiveImage.read(src)
    with M.Forge(src) as f:
        raw = {r.index: f.read(r) for r in f.records}
        recs = [r for r in f.records if r.name != "GlobalMetaFile" and len(raw[r.index]) > 8]
    info = areas(src)
    for uid, q in (quiet or {}).items():
        info[uid]["quiet_new"] = q
    for uid in polygons:
        if uid not in info:
            raise ValueError(f"{uid.hex()} isn't an out-of-bounds area or a gameplay zone of this map")
    new_body = {}                                   # uid -> new body (entity / mesh / shape)
    changed = set()
    for rec in recs:
        d = decompress(M.mapwalk.split_prefix(raw[rec.index])[1])
        entries, table = M.mapwalk.walk(d)
        objs, touched = [], False
        for off, size, serial, filt in entries:
            o = object_at(d, off, size)
            body = o["body"]
            uid = body[2:6] if body[:2] == M.UID_MARK else None
            # every copy: the ACR-ported maps also keep the wall mesh in the root cell and in a record of its own
            if uid is not None:
                if uid not in new_body:
                    nb = _rebuilt(S, C, cls, o, body, uid, polygons, info, log)
                    if nb is not None:
                        new_body[uid] = nb
                if uid in new_body and o["type"] in (cls["Entity"], cls["Mesh"], cls["MeshShape"]):
                    body = new_body[uid]
                    touched = True
            objs.append((serial, M.mapwalk.object_bytes(o, body), None if filt == M.mapwalk.NO_FILTER else filt))
        if touched:
            nd = M.mapwalk.rebuild(objs, table)
            pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
            assert decompress(pl) == nd
            set_payload(image, rec.index, pl)
            changed.add(rec.index)
    write(dest, image)
    with M.Forge(dest) as f:
        same = all(f.read(r) == raw[r.index] for r in f.records if r.index not in changed)
    log(f"{dest}: {len(changed)} records rewritten; other records byte-identical: {'yes' if same else 'NO'}")
    return same


def _quiet(poly, a, k):
    """The edges of a zone's new shape k the map had without FX, and those it had WITH FX (zone_quiet): by index while
    the shape keeps its corner count (corners moved), else the old edges it still has, end to end. -> (quiet, loud)."""
    old = a["polygons"][k] if k < len(a["polygons"]) else None
    if old is None:
        return set(), set()
    ow = walls(old)
    if len(poly) == len(old):
        q = set(a["quiet"][k])
        return q, set(range(len(ow))) - q
    match = lambda keep: {i for i, (p, q, _n) in enumerate(walls(poly))  # noqa: E731
                          if any((np.linalg.norm(p - c) < 0.1 and np.linalg.norm(q - d) < 0.1) or
                                 (np.linalg.norm(p - d) < 0.1 and np.linalg.norm(q - c) < 0.1) for c, d in keep)}
    return (match([ow[i][:2] for i in a["quiet"][k]]),
            match([ow[i][:2] for i in range(len(ow)) if i not in a["quiet"][k]]))


def _rebuilt(S, C, cls, o, body, uid, polygons, info, log):
    """The new body of an area's Entity / Mesh / MeshShape, or None if this object isn't one being rebuilt."""
    owner = next((e for e in polygons if e == uid or info[e]["mesh"] == uid or info[e]["shape"] == uid), None)
    if owner is None:
        return None
    a = info[owner]
    m4 = np.asarray(a["matrix"], np.float64).reshape(4, 4)
    if a["kind"] == "zone":
        polys = [np.asarray(p, np.float64) for p in polygons[owner]]
        for k, p in enumerate(polys):
            if not convex(p):
                raise ValueError(f"{a['name']} part {k + 1} isn't convex: the game tests a zone shape by shape, each "
                                 f"a convex piece - split it into convex parts")
        inner = internal_edges(polys)
        # a cut has no wall and no FX - unless the map draws both on that edge (AC4's Pirate HQ: a 5 m seam inside
        # a territory): the map's own data where there is some, the geometry for new edges
        quiet, bare = [], []
        for k, p in enumerate(polys):
            q, loud = (set(a["quiet_new"][k]), set()) if "quiet_new" in a else _quiet(p, a, k)
            quiet.append((q | inner[k]) - loud)
            bare.append(inner[k] - loud)
        parts = [wall_geometry(p, m4, True, None, a["zlo"], a["zhi"], uvs=ZONE_UV, skip=bare[k])
                 for k, p in enumerate(polys)]
        cv, ct, mv, mt, uv = [], [], [], [], []
        for pc, pt, pmv, pmt, puv in parts:
            ct.append(pt + sum(len(x) for x in cv))
            mt.append(pmt + sum(len(x) for x in mv))
            cv.append(pc)
            mv.append(pmv)
            uv.append(puv)
        cv, ct, mv, mt, uv = (np.vstack(cv), np.vstack(ct), np.vstack(mv), np.vstack(mt), np.vstack(uv))
        poly = polys[0]
    else:
        poly = np.asarray(polygons[owner], np.float64)
        cv, ct, mv, mt, uv = wall_geometry(poly, m4, a["closed"], a["ref"], a["zlo"], a["zhi"])
    root = C.decode(body)
    if o["type"] == cls["MeshShape"]:
        _v, _t, mats = collision._shape_arrays(root.obj.fields)
        collision._set_shape(root.obj.fields, cv, ct, bytes([mats[0] if len(mats) else 0]) * len(ct))
        log(f"{a['name']}: collision {len(ct)} triangles")
    elif o["type"] == cls["Mesh"]:
        md = M._obj(root.obj.fields["CompiledMesh"]).fields["MeshData"].fields
        fmt = md["VertexFormat"][0]
        mat = [x.id for x in root.obj.fields["CompiledMeshMaterials"]]
        vn, vt, hand = meshenc.frame(mv, mt, uv)
        q = meshenc.quantise(mv, uv, vn, vt, hand, fmt)
        vb = meshenc.pack_vertices(q, fmt)
        ib, prims, wide = meshenc.primitives([(0, len(mv), mt)])
        meshenc.set_mesh(root.obj, vb, ib, wide, fmt, prims, prims, mat)
        log(f"{a['name']}: mesh {len(mt)} triangles")
    elif a["kind"] == "zone":
        gz = _comp(S, root.obj, "GameplayZoneComponent")
        t_shape = gz.fields["Shapes"][0]
        t_sec = M._obj(t_shape).fields["Sections"][0]
        # an FX panel template from any section: a cut (internal edge) has none, and may come first
        t_fx = next(fx for sh_ in gz.fields["Shapes"] for x in M._obj(sh_).fields["Sections"]
                    for fx in M._obj(x).fields["FXSections"])
        shapes = []
        for k_, poly_ in enumerate(polys):
            sh = copy.deepcopy(t_shape)
            secs_ = []
            for w_, (a_, b_, n) in enumerate(walls(poly_)):
                L = float(np.linalg.norm(b_ - a_))
                th = np.arctan2(-n[0], n[1])                     # R_z(th): local y -> the inward normal
                c = (a_ + b_) / 2
                g = copy.deepcopy(t_sec)
                f = M._obj(g).fields
                f["GlobalPosition"] = struct.pack("<4f", c[0], c[1], m4[3, 2], 0.0)
                f["GlobalRotation"] = struct.pack("<4f", 0.0, 0.0, np.sin(th / 2), np.cos(th / 2))
                f["GlobalNormal"] = struct.pack("<4f", n[0], n[1], 0.0, 0.0)
                f["SideVector"] = struct.pack("<4f", np.cos(th), np.sin(th), 0.0, 0.0)
                f["Size"] = struct.pack("<2f", L, a["height"])
                k = 0 if w_ in quiet[k_] else max(1, int(np.ceil(L / FX_PANEL - 1e-6)))
                fxs = []
                for j in range(k):
                    fx = copy.deepcopy(t_fx)
                    pc = a_ + (b_ - a_) * (j + 0.5) / k
                    M._obj(fx).fields["GlobalPosition"] = struct.pack("<4f", pc[0], pc[1], m4[3, 2], 0.0)
                    M._obj(fx).fields["Size"] = struct.pack("<2f", L / k, a["height"])
                    fxs.append(fx)
                f["FXSections"] = fxs
                secs_.append(g)
            M._obj(sh).fields["Sections"] = secs_
            shapes.append(sh)
        gz.fields["Shapes"] = shapes
        offs = (mv @ m4[:3, :3])
        bv = root.obj.fields["BoundingVolume"].fields
        bv["Min"], bv["Max"] = struct.pack("<3f", *offs.min(0)), struct.pack("<3f", *offs.max(0))
        log(f"{a['name']}: {len(polys)} shape(s), {sum(len(p) for p in polys)} corners, "
            f"{sum(len(x) for x in inner)} internal edges, {sum(len(x) for x in quiet)} without FX")
    else:
        oc = _comp(S, root.obj, "OutOfBoundsComponent")
        secs = sections(poly, m4[3, 2], a["closed"], a["ref"], a["height"])
        tmpl = oc.fields["Sections"][0]
        new = []
        for p, q, n, sz in secs:
            g = copy.deepcopy(tmpl)
            f = M._obj(g).fields
            f["GlobalPosition"] = struct.pack("<4f", *p)
            f["GlobalRotation"] = struct.pack("<4f", *q)
            f["GlobalNormal"] = struct.pack("<4f", *n)
            f["Size"] = struct.pack("<2f", *sz)
            new.append(g)
        oc.fields["Sections"] = new
        offs = (mv @ m4[:3, :3])
        bv = root.obj.fields["BoundingVolume"].fields
        bv["Min"], bv["Max"] = struct.pack("<3f", *offs.min(0)), struct.pack("<3f", *offs.max(0))
        log(f"{a['name']}: {len(secs)} sections, {len(poly)} corners")
    return C.encode(root)
