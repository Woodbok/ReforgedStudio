"""Holes a build left: every surface level that was there before and is gone after (studio/tools/hole_map.py draws
them; plan.build reports them; the app marks them in the viewport).

On a grid round each building that carried its ground: inside the building's new outline a point is compared with
the point it came from (the move's transform), outside with the same point (the moved objects left out). A level - a
walkable face (floor, ground, platform, stair top) - present before and missing after (0.3 m) is a hole: in the
visible models (you see through, the water plane shows) and / or in the collision (you fall through). Levels below
-5 m (the water plane) don't count.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

from ac3editor import ground, terrain  # noqa: E402

STEP = 0.5
MARGIN = 6.0
TOL = 0.3
FLOOR = -5.0


def _levels(index, x, y, exclude=()):
    return sorted({round(z, 2) for z, _o in index.heights(x, y, exclude=exclude) if z > FLOOR})


def find(md0, g0, v0, md1, carry, moved_uids, step=STEP):
    """carry: plan.moved_buildings' list. -> dict(visible=[(x, y, z)], collision=[(x, y, z)], grid=(xs, ys),
    masks=(visible, collision) bool (len(ys), len(xs)), area=m2 per point) over all the carried buildings."""
    g1, v1 = ground.Ground(md1), ground.Visible(md1)
    out = dict(visible=[], collision=[], area=step * step, regions=[])
    for _key, old, new, T, _u in carry:
        Ti = np.linalg.inv(T)
        lo = np.minimum(old.min(0), new.min(0)) - MARGIN
        hi = np.maximum(old.max(0), new.max(0)) + MARGIN
        xs, ys = np.arange(lo[0], hi[0], step), np.arange(lo[1], hi[1], step)
        pts = np.array([(x, y) for y in ys for x in xs])
        inside = terrain.inside_poly(pts, new).reshape(len(ys), len(xs))
        masks = {k: np.zeros((len(ys), len(xs)), bool) for k in ("visible", "collision")}
        edges = [(new[i], new[(i + 1) % len(new)]) for i in range(len(new))]
        same_outline = old.shape == new.shape and np.allclose(old, new)

        def on_edge(x, y):
            """(a grid point on the outline itself: the cut line - inside or out is a coin toss there)"""
            p = np.array([x, y])
            for a_, b_ in edges:
                d = b_ - a_
                t = np.clip(np.dot(p - a_, d) / max(np.dot(d, d), 1e-12), 0, 1)
                if np.linalg.norm(p - (a_ + t * d)) < 0.05:
                    return True
            return False
        for j, y in enumerate(ys):
            for i, x in enumerate(xs):
                if on_edge(x, y):
                    continue
                if inside[j, i]:
                    q = np.array([x, y, 0.0]) @ Ti[:3, :3] + Ti[3, :3]
                    src, excl, dz = (q[0], q[1]), (), T[3, 2]        # (a raised / lowered region: its levels + dz)
                else:
                    # outside: what stays; a raised / lowered region (same outline) moves nothing outside it, so
                    # nothing is left out there (a moved master collision object can lift platforms outside the
                    # outline, and leaving it out would hide that)
                    src, excl, dz = (x, y), (() if same_outline else moved_uids), 0.0
                for i0, i1, kind in ((v0, v1, "visible"), (g0, g1, "collision")):
                    now = _levels(i1, x, y)
                    for z in _levels(i0, *src, exclude=excl):
                        if not any(abs(z + dz - n) < TOL or abs(z - n) < TOL for n in now):
                            masks[kind][j, i] = True
                            out[kind].append((float(x), float(y), float(z)))
        out["regions"].append(dict(xs=xs, ys=ys, old=old, new=new, **{f"{k}_mask": m for k, m in masks.items()}))
    return out


def summary(found):
    a = found["area"]
    lines = []
    for kind, what in (("collision", "fall through"), ("visible", "see through")):
        pts = found[kind]
        cells = {(round(x, 2), round(y, 2)) for x, y, _z in pts}
        if cells:
            zs = np.array([z for _x, _y, z in pts])
            lines.append(f"holes ({what}): {len(cells) * a:.1f} m2, at "
                         + ", ".join(f"{int(z)} m" for z in np.unique(np.round(zs))[:6]))
        else:
            lines.append(f"holes ({what}): none")
    return lines
