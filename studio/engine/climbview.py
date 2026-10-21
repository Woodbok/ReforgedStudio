"""Climbing in Reforged Studio: what the app draws to show where a map can be climbed.

    climb_data(archive) -> {"edges": [[x0, y0, z0, x1, y1, z1, kind, nx, ny]], "owners": [uid hex per edge],
                            "noclimb": {shape uid hex: [[first triangle, count], ..]}, "counts": {...}}
    asset_climb(folder) -> {"edges": [[...model frame...]], "noclimb": [[first, count], ..]} for a library model

Climb edges are what a climber grabs: every entity's GuidanceSystem (library.map_walk; a merged object holds its
pieces' edges) - kind 1 ledge, 4 pole, 14 beam (climb.py), other kinds passed through; (nx, ny) the ground direction
of the edge's less vertical face normal (the wall's: the side the climber hangs on; either face can come first - on
Fort Wolcott 470 of 707 edges list the top's normal first). A collision triangle on a no-climb material (scenebuild.NOT_CLIMBABLE:
FuzzyZone, Z_Camonly - stock roofs and the barracks' walls) can't be climbed or wall-run, edges or not: "noclimb" lists
those triangles per collision shape, in the order the scene cache holds them (library.shape_tri_materials' order).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

import library  # noqa: E402
from ac3editor import mapdata  # noqa: E402
from ac3editor.scenebuild import NOT_CLIMBABLE  # noqa: E402


def _uid_hex(u):
    return bytes(u).hex() if isinstance(u, (bytes, bytearray)) else int(u).to_bytes(4, "little").hex()


def _runs(flags):
    """[bool per triangle] -> [[first, count], ..] of the True runs."""
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append([start, i - start])
            start = None
    if start is not None:
        out.append([start, len(flags) - start])
    return out


def _edge_rows(edges):
    rows = []
    for a, b, n0, n1, sub in edges:
        # the wall side: of the edge's two face normals (the top's and the wall's, either order) the less vertical
        n = n0 if abs(float(n0[2])) <= abs(float(n1[2])) else n1
        h = np.asarray(n[:2], np.float64)
        h = h / max(float(np.linalg.norm(h)), 1e-9)
        rows.append([round(float(v), 3) for v in (*a[:3], *b[:3])] + [int(sub), round(float(h[0]), 3), round(float(h[1]), 3)])
    return rows


def climb_data(archive):
    path = os.path.abspath(archive)
    walk = library.map_walk(path)
    md = mapdata.load(path, log=lambda *_a: None)
    edges = _edge_rows(walk["edges"])
    owners = [_uid_hex(u) for u in walk.get("edge_owner", [])]
    noclimb, n_no, n_all = {}, 0, 0
    for sid in md.shapes:
        names = library.shape_tri_materials(md, [sid], walk)
        if not names:
            continue
        flags = [n in NOT_CLIMBABLE for n in names]
        n_all += len(flags)
        if any(flags):
            noclimb[_uid_hex(sid)] = _runs(flags)
            n_no += sum(flags)
    kinds = {}
    for r in edges:
        kinds[r[6]] = kinds.get(r[6], 0) + 1
    # a merged piece's collision is in its cell's merged object (its member shapes, in the piece's own frame)
    members = {_uid_hex(u): [_uid_hex(s) for s in sids] for u, sids in walk["members"].items()}
    return dict(edges=edges, owners=owners, noclimb=noclimb, members=members,
                counts=dict(edges=len(edges), kinds={str(k): v for k, v in sorted(kinds.items())},
                            collision_triangles=n_all, noclimb_triangles=n_no))


def asset_climb(folder):
    """A library model's own climb edges (its map's, model frame) and its no-climb collision triangles."""
    edges = library.load_edges(folder) if os.path.exists(folder) else []
    out = dict(edges=_edge_rows(edges), noclimb=[])
    got = library.load_colmats(folder) if os.path.exists(folder) else None
    if got is not None:
        names, idx, _info = got
        out["noclimb"] = _runs([names[int(i)] in NOT_CLIMBABLE for i in idx])
    return out
