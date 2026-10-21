"""Where the walkable ground is, and the holes a moved or removed object leaves behind.

An example: Fort Wolcott's ground collision has NO triangles under the pillory's footprint - the pillory's own
collision (InertComponent IsGround 1) plugs the gap, and the visible ground has the same hole. Moving the pillory
drops players through the map. `holes()` finds such holes: it samples the object's old footprint where the object itself
was solid, and asks whether anything else (not moved, not removed) is walkable there near its old base.
`fill_spec()` turns a hole into a new-model slab (newmodel.add_models) in the material of the ground around it.
"""
import numpy as np


class Ground:
    """Every static collision triangle of a map in world coordinates, indexed on a 2D grid for vertical queries."""

    def __init__(self, md, cell=4.0):
        tris, owner = [], []
        for e in md.entities:
            if e.record.startswith("DataBlock_") or not e.shapes or e.shapes[0] not in md.shapes:
                continue
            m = np.array(e.matrix, dtype=np.float64).reshape(4, 4)
            v, t = md.shapes[e.shapes[0]][1].astype(np.float64), md.shapes[e.shapes[0]][2]
            w = v @ m[:3, :3] + m[3, :3]
            tris.append(w[t])
            owner += [e.uid] * len(t)
        self.t = np.concatenate(tris) if tris else np.zeros((0, 3, 3))
        self.owner = np.array(owner, dtype=object)
        n = np.cross(self.t[:, 1] - self.t[:, 0], self.t[:, 2] - self.t[:, 0])
        ln = np.maximum(np.linalg.norm(n, axis=1), 1e-12)
        self.nz = n[:, 2] / ln
        self.cell = cell
        self.grid = {}
        lo = np.floor(self.t[:, :, :2].min(1) / cell).astype(int)
        hi = np.floor(self.t[:, :, :2].max(1) / cell).astype(int)
        for i, (a, b) in enumerate(zip(lo, hi)):
            for x in range(a[0], b[0] + 1):
                for y in range(a[1], b[1] + 1):
                    self.grid.setdefault((x, y), []).append(i)

    def heights(self, x, y, exclude=(), walkable=0.5, up_only=False):
        """[(z, owner uid)] of every walkable triangle (normal z >= `walkable`; either facing unless `up_only`) above /
        below (x, y)."""
        ids = self.grid.get((int(np.floor(x / self.cell)), int(np.floor(y / self.cell))), [])
        out = []
        for i in ids:
            if (self.nz[i] if up_only else abs(self.nz[i])) < walkable or self.owner[i] in exclude:
                continue
            a, b, c = self.t[i]
            v0, v1, v2 = b[:2] - a[:2], c[:2] - a[:2], np.array([x, y]) - a[:2]
            d = v0[0] * v1[1] - v1[0] * v0[1]
            if abs(d) < 1e-12:
                continue
            u = (v2[0] * v1[1] - v1[0] * v2[1]) / d
            w = (v0[0] * v2[1] - v2[0] * v0[1]) / d
            if u >= -1e-6 and w >= -1e-6 and u + w <= 1 + 1e-6:
                out.append((a[2] + u * (b[2] - a[2]) + w * (c[2] - a[2]), self.owner[i]))
        return out


def footprint(md, e, matrix=None):
    """World collision box of entity e (at `matrix`, default its own), or None."""
    if not e.shapes or e.shapes[0] not in md.shapes:
        return None
    m = np.array(e.matrix if matrix is None else matrix, dtype=np.float64).reshape(4, 4)
    w = md.shapes[e.shapes[0]][1].astype(np.float64) @ m[:3, :3] + m[3, :3]
    return w.min(0), w.max(0)


def surface(ground, x, y, top, gone):
    """The walkable surface at (x, y): the highest upward-facing face at or below `top`, ignoring `gone`."""
    zs = [z for z, _o in ground.heights(x, y, exclude=gone, up_only=True) if z <= top + 0.1]
    return max(zs) if zs else None


def holes(md, ground, e, gone, step=0.5):
    """Where entity e (about to move / vanish) was the only thing to stand on. `gone`: uids of every object moved or
    removed in this build. Only an object reaching down to the ground around it can leave a hole (a rope on a deck
    can't). -> dict(points (k, 2), z = the surrounding ground surface, area m2) or None."""
    box = footprint(md, e)
    if box is None:
        return None
    lo, hi = box
    ring = []
    for x in np.arange(lo[0] - 1.0, hi[0] + 1.01, step):
        for y in (lo[1] - 0.75, hi[1] + 0.75):
            ring.append(surface(ground, x, y, hi[2], gone))
    for y in np.arange(lo[1] - 1.0, hi[1] + 1.01, step):
        for x in (lo[0] - 0.75, hi[0] + 0.75):
            ring.append(surface(ground, x, y, hi[2], gone))
    ring = [z for z in ring if z is not None]
    if not ring:
        return None
    z0 = float(np.median(ring))
    if lo[2] > z0 + 0.5:
        return None                                          # it stands on something - it can't plug the ground
    # uneven ground (Wolcott's barracks: the yard at 0 one side, the flag-tower platform at 4 the other - the median 3
    # matched neither): anything walkable within the range of the ground around counts
    z_lo, z_hi = float(np.percentile(ring, 10)) - 1.0, float(np.percentile(ring, 90)) + 0.5
    missing = []
    for x in np.arange(lo[0] + step / 2, hi[0], step):
        for y in np.arange(lo[1] + step / 2, hi[1], step):
            if not [z for z, o in ground.heights(x, y) if o == e.uid]:
                continue                                     # the object itself wasn't solid here
            if not [z for z, _o in ground.heights(x, y, exclude=gone, up_only=True) if z_lo <= z <= z_hi]:
                missing.append((x, y))
    if not missing:
        return None
    return {"points": np.array(missing), "z": z0, "area": len(missing) * step * step}


def merge_holes(found, step=0.5):
    """[(key, hole)] -> [([keys], hole)]: holes whose points touch become one."""
    groups = []
    for key, h in found:
        hit = [g for g in groups if np.min(np.linalg.norm(g[1]["points"][:, None] - h["points"][None], axis=2))
               <= step * 1.5]
        if not hit:
            groups.append(([key], dict(h)))
            continue
        g = hit[0]
        g[0].append(key)
        g[1]["points"] = np.vstack([g[1]["points"], h["points"]])
        g[1]["area"] += h["area"]
        for other in hit[1:]:
            g[0].extend(other[0])
            g[1]["points"] = np.vstack([g[1]["points"], other[1]["points"]])
            g[1]["area"] += other[1]["area"]
            groups.remove(other)
    return groups


OVERLAY = ("alpha", "decal", "hay", "puddle", "fx", "vegetation", "leaves", "grass")


def ground_material(md, ground, hole, gone=()):
    """The material of the visible ground around a hole: the meshes of the solid objects the ring of points just
    outside the footprint stands on, overlays (alpha / decals / hay / puddles) skipped, the most common one."""
    from collections import Counter
    by_uid = {e.uid: e for e in md.entities}
    p = hole["points"]
    lo, hi = p.min(0) - 1.0, p.max(0) + 1.0
    ring = [(x, y) for x in np.linspace(lo[0], hi[0], 9) for y in (lo[1], hi[1])]
    ring += [(x, y) for y in np.linspace(lo[1], hi[1], 9) for x in (lo[0], hi[0])]
    votes = Counter()
    for x, y in ring:
        for z, o in ground.heights(x, y, exclude=gone):
            if abs(z - hole["z"]) > 0.5:
                continue
            e = by_uid.get(o)
            if e is None or e.mesh not in md.mesh_extra:
                continue
            box = footprint(md, e)
            if box is None or (box[1] - box[0])[:2].max() < 8.0:
                continue                                     # props lying on the ground (planks...) aren't ground
            for m in md.mesh_extra[e.mesh][2]:
                if m in md.materials and md.materials[m][1] and not any(k in md.materials[m][0].lower() for k in OVERLAY):
                    votes[m] += 1
    if votes:
        return votes.most_common(1)[0][0]
    # nothing solid around it: the material covering the most ground in the map
    for e in sorted((e for e in md.entities if "Ground" in e.name and e.mesh in md.mesh_extra),
                    key=lambda e: -len(md.meshes[e.mesh][2]) if e.mesh in md.meshes else 0):
        for m in md.mesh_extra[e.mesh][2]:
            if m in md.materials and md.materials[m][1] and not any(k in md.materials[m][0].lower() for k in OVERLAY):
                return m
    return None


def fill_box(hole, pad=0.25, thickness=0.3, lift=0.0):
    """The slab that plugs a hole: (centre xyz, size xyz) in world, its top `lift` above the surrounding ground
    (0 for a collision-only fill: flush; a visible slab sits 5 cm proud - flush with the visible ground it flickers
    from depth ties)."""
    p = hole["points"]
    lo, hi = p.min(0) - 0.25 - pad, p.max(0) + 0.25 + pad
    c = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hole["z"] + lift - thickness / 2])
    return c, np.array([hi[0] - lo[0], hi[1] - lo[1], thickness])


class Visible(Ground):
    """The same vertical queries over the visible models (each entity's LOD0 mesh) instead of collision."""

    def __init__(self, md, cell=4.0):
        tris, owner = [], []
        for e in md.entities:
            if e.record.startswith("DataBlock_") or not e.mesh or e.mesh not in md.meshes:
                continue
            m = np.array(e.matrix, dtype=np.float64).reshape(4, 4)
            v, t = md.meshes[e.mesh][1].astype(np.float64), md.meshes[e.mesh][2]
            if not len(t):
                continue
            w = v @ m[:3, :3] + m[3, :3]
            tris.append(w[t])
            owner += [e.uid] * len(t)
        self.t = np.concatenate(tris) if tris else np.zeros((0, 3, 3))
        self.owner = np.array(owner, dtype=object)
        n = np.cross(self.t[:, 1] - self.t[:, 0], self.t[:, 2] - self.t[:, 0])
        self.nz = n[:, 2] / np.maximum(np.linalg.norm(n, axis=1), 1e-12)
        self.cell = cell
        self.grid = {}
        lo = np.floor(self.t[:, :, :2].min(1) / cell).astype(int)
        hi = np.floor(self.t[:, :, :2].max(1) / cell).astype(int)
        for i, (a, b) in enumerate(zip(lo, hi)):
            if (b - a).max() > 64:                          # skip the 4 km sea plane and similar
                continue
            for x in range(a[0], b[0] + 1):
                for y in range(a[1], b[1] + 1):
                    self.grid.setdefault((x, y), []).append(i)


def visible_cover(visible, hole, gone, tol=0.15):
    """Fraction of a hole's points where a visible upward surface (not of a moved / removed object) lies within
    `tol` of the surrounding ground height - i.e. the visible ground is there and only collision is missing."""
    pts = hole["points"]
    hit = 0
    for x, y in pts:
        if any(abs(z - hole["z"]) <= tol for z, _o in visible.heights(x, y, exclude=gone, up_only=True)):
            hit += 1
    return hit / max(len(pts), 1)


def floor_in_front(ground, exclude=(), out=0.6):
    """-> floor(point, wall normal): the walkable surface `out` metres in front of a wall below a world point (the
    floor a player climbing there stands on), ignoring `exclude` (the object itself), or None. For
    climb.handholds / scaling.scale_entities(floors=...)."""
    def floor(p, wall):
        w = np.array([wall[0], wall[1], 0.0])
        n = np.linalg.norm(w)
        if n < 1e-6:
            return None
        q = np.asarray(p, np.float64) + w / n * out
        zs = [z for z, _o in ground.heights(q[0], q[1], exclude=exclude, up_only=True) if z < q[2] - 0.2]
        return max(zs) if zs else None
    return floor


def covered(points, z, tris, below=1.0, above=0.5):
    """Which hole points (k, 2) a set of world triangles (n, 3, 3) - the moved objects at their NEW place - stands
    over with a walkable upward face within -below / +above of the ground height z (a building turned in place still
    covers its own footprint). -> bool (k,)."""
    T = np.asarray(tris, np.float64).reshape(-1, 3, 3)
    pts = np.asarray(points, np.float64).reshape(-1, 2)
    out = np.zeros(len(pts), bool)
    if not len(T) or not len(pts):
        return out
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    nz = n[:, 2] / np.maximum(np.linalg.norm(n, axis=1), 1e-12)
    T = T[nz >= 0.5]
    if not len(T):
        return out
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    v0, v1 = b[:, :2] - a[:, :2], c[:, :2] - a[:, :2]
    d = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
    ok = np.abs(d) > 1e-12
    for k, p in enumerate(pts):
        v2 = p - a[:, :2]
        u = np.where(ok, (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / np.where(ok, d, 1), -1)
        w = np.where(ok, (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / np.where(ok, d, 1), -1)
        inside = (u >= -1e-6) & (w >= -1e-6) & (u + w <= 1 + 1e-6)
        if inside.any():
            zz = a[inside, 2] + u[inside] * (b[inside, 2] - a[inside, 2]) + w[inside] * (c[inside, 2] - a[inside, 2])
            out[k] = bool(((zz >= z - below) & (zz <= z + above)).any())
    return out


def split_levels(ground, hole, gone, step=0.5, override=None):
    """A hole over split-level ground (e.g. a building moved off a raised platform's edge - platform 4.2 m one side,
    yard 0 the other) -> one hole per level and connected patch: each point takes the height of the walkable ground
    just around it (the commonest of the surfaces 1-2.5 m away, ignoring `gone`), points are grouped by that height
    (0.5 m) and by touching, and each group keeps its own height - one flat fill across both levels would make a 4 m
    slab over the yard."""
    pts = hole["points"]
    zs, every = [], []
    for x, y in pts:
        near = []
        for r in (0.75, 1.25, 1.75):
            for a in np.linspace(0, 2 * np.pi, 8, endpoint=False):
                qx, qy = x + r * np.cos(a), y + r * np.sin(a)
                s = override(qx, qy) if override is not None else None
                if s is None:
                    s = surface(ground, qx, qy, hole["z"] + 6.0, gone)
                if s is not None:
                    near.append(round(s * 2) / 2)
        every += near
        if near:
            vals, counts = np.unique(near, return_counts=True)
            zs.append(float(vals[counts.argmax()]))
        else:
            zs.append(round(hole["z"] * 2) / 2)
    zs = np.array(zs)
    # snap to the MAIN ground levels around (each >= 15% of every surface seen): heights in between - ramps, stairs -
    # and odd high ones - a roof - would lay stepped slabs or one far up
    if every:
        vals, counts = np.unique(every, return_counts=True)
        main = vals[counts >= 0.15 * len(every)]
        if len(main):
            zs = main[np.abs(zs[:, None] - main[None, :]).argmin(1)]
    out = []
    for z in np.unique(zs):
        sub = pts[zs == z]
        # connected patches (points touching on the sampling grid)
        left = list(range(len(sub)))
        while left:
            comp, todo = [], [left.pop(0)]
            while todo:
                i = todo.pop()
                comp.append(i)
                near = [j for j in left if np.abs(sub[j] - sub[i]).max() <= step * 1.5]
                for j in near:
                    left.remove(j)
                todo.extend(near)
            if len(comp) >= 2:
                out.append({"points": sub[comp], "z": float(z), "area": len(comp) * step * step})
    return out


def patches(points, step=0.5):
    """Hole points (k, 2) -> [indices of each touching patch]."""
    left = list(range(len(points)))
    out = []
    while left:
        comp, todo = [], [left.pop(0)]
        while todo:
            i = todo.pop()
            comp.append(i)
            near = [j for j in left if np.abs(points[j] - points[i]).max() <= step * 1.5]
            for j in near:
                left.remove(j)
            todo.extend(near)
        out.append(comp)
    return out


def fill_cells(hole, step=0.5, lift=0.0, thickness=0.3, grow=1):
    """The slab that plugs a hole, shaped like it: the hole's sampled cells (step x step, on its sampling grid) grown
    by `grow` cells so it meets the ground around, top `lift` above the hole's height, `thickness` deep - a closed,
    outward-wound volume (a bounding box would lay one level's fill over another's ground).
    -> (centre (3,) world, verts (n, 3) relative to it, quads [(a, b, c, d)])."""
    pts = np.asarray(hole["points"], np.float64)
    o = pts.min(0)
    cells = {tuple(np.rint((p - o) / step).astype(int)) for p in pts}
    for _ in range(grow):
        cells |= {(i + di, j + dj) for i, j in cells for di in (-1, 0, 1) for dj in (-1, 0, 1)}
    zt, zb = hole["z"] + lift, hole["z"] + lift - thickness
    vid, verts, quads = {}, [], []

    def v(i, j, top):
        k = (i, j, top)
        if k not in vid:
            vid[k] = len(verts)
            verts.append((o[0] + (i - 0.5) * step, o[1] + (j - 0.5) * step, zt if top else zb))
        return vid[k]
    for i, j in cells:
        quads.append((v(i, j, 1), v(i + 1, j, 1), v(i + 1, j + 1, 1), v(i, j + 1, 1)))         # top, up
        quads.append((v(i, j, 0), v(i, j + 1, 0), v(i + 1, j + 1, 0), v(i + 1, j, 0)))         # bottom, down
        if (i - 1, j) not in cells:
            quads.append((v(i, j, 0), v(i, j, 1), v(i, j + 1, 1), v(i, j + 1, 0)))
        if (i + 1, j) not in cells:
            quads.append((v(i + 1, j, 0), v(i + 1, j + 1, 0), v(i + 1, j + 1, 1), v(i + 1, j, 1)))
        if (i, j - 1) not in cells:
            quads.append((v(i, j, 0), v(i + 1, j, 0), v(i + 1, j, 1), v(i, j, 1)))
        if (i, j + 1) not in cells:
            quads.append((v(i, j + 1, 0), v(i, j + 1, 1), v(i + 1, j + 1, 1), v(i + 1, j + 1, 0)))
    verts = np.array(verts)
    c = np.r_[verts[:, :2].mean(0), zt]
    return c, verts - c, quads
