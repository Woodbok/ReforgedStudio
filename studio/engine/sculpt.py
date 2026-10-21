"""Ground sculpting in Reforged Studio: hills and hollows brushed into a map's ground.

A request's `sculpts`: [{"x", "y", "r": radius (m), "h": height (m, + up / - down)}] - brush strokes. They add up: the
ground's change at (x, y) is dz(x, y) = sum over strokes of h * falloff(distance / r), falloff a smooth bump (1 at the
middle, 0 at and past the edge, flat at both ends). Each cluster of overlapping strokes is one area (the convex hull
of its circles); in the build (plan.py, pass "sculpt"):

1. the ground under each area is cut out (terrain.cut - the cut-and-carry the ground areas and moved buildings
   use): its visible triangles (UVs, material, vertex colours, the two-layer blend) and its collision;
2. both are split into triangles no longer than 1 m across (longest-edge halving, every vertex attribute interpolated:
   a few big ground triangles can't bend into a hill) and each vertex is raised / lowered by dz - the area's edge has
   dz = 0, so it meets the ground around it without a seam or skirt walls;
3. they go back in as new models (terrain.chunk_specs' layout: one per material, the collision its own hidden model);
4. what stands on the ground in an area - models wholly inside it, spawn points - goes up / down by dz under its
   middle (plan.py, as the ground areas' riders), and the crowds' navmesh is made again over it.

`check(dest, strokes)`: read back - the ground's height at points across each area is the old one + dz (visible and
collision), and no point there has nothing under it.
"""
import numpy as np

from ac3editor import terrain

MAX_EDGE = 1.0          # (m) triangles longer than this are halved before bending
MAX_STROKES = 200
MAX_R = 60.0


def strokes_of(req):
    out = []
    for s in req.get("sculpts", [])[:MAX_STROKES]:
        r = float(s.get("r", 0))
        h = float(s.get("h", 0))
        if r < 0.5 or abs(h) < 0.005:
            continue
        out.append((float(s["x"]), float(s["y"]), min(r, MAX_R), max(-30.0, min(30.0, h))))
    return out


def falloff(t):
    """1 at t = 0, 0 at t >= 1, smooth (cosine bump)."""
    t = np.clip(np.asarray(t, np.float64), 0.0, 1.0)
    return 0.5 * (1.0 + np.cos(np.pi * t))


def dz_at(strokes, x, y):
    x = np.asarray(x, np.float64)
    y = np.asarray(y, np.float64)
    d = np.zeros(np.broadcast(x, y).shape)
    for sx, sy, r, h in strokes:
        d = d + h * falloff(np.hypot(x - sx, y - sy) / r)
    return d


def circle(x, y, r, n=32):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.c_[x + r * np.cos(a), y + r * np.sin(a)]


def areas(strokes):
    """Clusters of overlapping strokes -> [(convex polygon (n, 2), [strokes])]."""
    left = list(strokes)
    out = []
    while left:
        group = [left.pop(0)]
        grown = True
        while grown:
            grown = False
            for s in list(left):
                if any(np.hypot(s[0] - g[0], s[1] - g[1]) < s[2] + g[2] for g in group):
                    group.append(s)
                    left.remove(s)
                    grown = True
        pts = np.vstack([circle(x, y, r) for x, y, r, _h in group])
        out.append((terrain.hull(pts), group))
    return out


def subdivide(tris, attrs=(), max_edge=MAX_EDGE):
    """Triangles (m, 3, 3) with per-vertex attributes [(m, 3, k)] -> the same, every triangle's longest edge halved
    until none is longer than max_edge (its attributes interpolated at the new vertex)."""
    tris = np.asarray(tris, np.float64)
    attrs = [np.asarray(a, np.float64) for a in attrs]
    done_t, done_a = [], [[] for _ in attrs]
    for _round in range(24):
        if not len(tris):
            break
        e = np.stack([np.linalg.norm(tris[:, (i + 1) % 3, :2] - tris[:, i, :2], axis=1) for i in range(3)], axis=1)
        longest = e.argmax(1)
        big = e.max(1) > max_edge
        done_t.append(tris[~big])
        for k, a in enumerate(attrs):
            done_a[k].append(a[~big])
        tris, attrs = tris[big], [a[big] for a in attrs]
        longest = longest[big]
        if not len(tris):
            break
        n = len(tris)
        i0 = longest
        i1 = (longest + 1) % 3
        i2 = (longest + 2) % 3
        rows = np.arange(n)

        def split(v):
            a, b, c = v[rows, i0], v[rows, i1], v[rows, i2]
            m = (a + b) / 2
            return np.concatenate([np.stack([a, m, c], 1), np.stack([m, b, c], 1)])
        tris = split(tris)
        attrs = [split(a) for a in attrs]
    done_t.append(tris)
    for k, a in enumerate(attrs):
        done_a[k].append(a)
    return np.concatenate(done_t), [np.concatenate(x) for x in done_a]


def bend(tris, strokes):
    t = np.array(tris, np.float64)
    t[:, :, 2] += dz_at(strokes, t[:, :, 0], t[:, :, 1])
    return t


def specs(removed_vis, removed_col, strokes, name):
    """The cut ground, split and bent -> newmodel specs (chunk_specs' layout: one model per material, the collision
    its own hidden model)."""
    out_vis = []
    for tris, uvs, mat, col, *bl in removed_vis:
        if mat is None or not len(tris):
            continue
        tris = np.asarray(tris, np.float64)
        col = np.asarray(col if col is not None else np.full(tris.shape[:2] + (4,), 255, np.uint8), np.float64)
        blend = np.asarray(bl[0] if bl else np.full(tris.shape[:2], 255, np.uint8), np.float64)[:, :, None]
        st, (su, sc, sb) = subdivide(tris, [np.asarray(uvs, np.float64), col, blend])
        out_vis.append((bend(st, strokes), su, mat, np.clip(np.round(sc), 0, 255).astype(np.uint8),
                        np.clip(np.round(sb[:, :, 0]), 0, 255).astype(np.uint8)))
    col = np.asarray(removed_col, np.float64).reshape(-1, 3, 3)
    if len(col):
        col, _ = subdivide(col)
        col = bend(col, strokes)
    return terrain.chunk_specs(out_vis, col, np.eye(4), name=name)


def check(md0, gr0, md1, gr1, strokes, samples=7):
    """Read back: across each stroke the new ground (the ED_Sculpt collision) stands at the old ground (the map's
    Ground pieces' collision there) + dz within 0.12 m, and nowhere that had ground has none. Roofs, floors and props
    over the ground don't count. -> (ok, lines)."""
    n0 = {e.uid: e.name for e in md0.entities}
    n1 = {e.uid: e.name for e in md1.entities}
    bad, missing, n = [], 0, 0
    for sx, sy, r, _h in strokes:
        for gx in np.linspace(-0.85, 0.85, samples):
            for gy in np.linspace(-0.85, 0.85, samples):
                if gx * gx + gy * gy > 0.75:
                    continue
                x, y = sx + gx * r, sy + gy * r
                old = [z for z, o in gr0.heights(x, y) if "Ground" in n0.get(o, "")]
                if not old:
                    continue
                want = max(old) + float(dz_at(strokes, x, y))
                new = [z for z, o in gr1.heights(x, y) if n1.get(o, "").startswith("ED_Sculpt")]
                n += 1
                if not new:
                    missing += 1
                    continue
                z1 = min(new, key=lambda z: abs(z - want))
                if abs(z1 - want) > 0.12:
                    bad.append((x, y, z1 - want))
    ok = not missing and not bad and n > 0
    lines = [f"{'ok' if ok else 'FAIL'}: sculpted ground read back at {n} point(s): {n - missing - len(bad)} at the "
             f"planned height, {len(bad)} off, {missing} with no ground"]
    if bad:
        worst = max(bad, key=lambda b: abs(b[2]))
        lines.append(f"  worst: ({worst[0]:.1f}, {worst[1]:.1f}) {worst[2]:+.2f} m")
    return ok, lines
