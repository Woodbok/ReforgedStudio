"""Terrain cut-and-carry: the ground under a building moves with it.

AC3's buildings are fitted to their ground - Wolcott's barracks stands half on the yard (0 m) and half on the
flag-tower ground's raised platform (4.2 m) whose gaps only the barracks' own floor fills; turned round, its ground
floor is buried and players fall through the gaps. So a moved building takes the
ground under it along, and the ground where it lands makes room:

* `cut` - for every ground object (`is_ground`: named Ground, or a flat slab) reaching under a footprint polygon
  (convex: the hull), its triangles inside are removed from every LOD mesh and from its collision; triangles crossing
  the edge are SPLIT exactly along it (`split_triangle`) - new vertices mixed from the corners, appended; each
  instance's baked shading stream (4 bytes a vertex) extended the same way (`extend_stream`). Each cut object gets its own
  new Mesh / LODSelector / MeshShape (fresh ids, in its record, listed in its cell; a mesh may be shared by other
  objects), its cell's merged collision tree re-pointed and re-treed. Returns the removed LOD0 triangles (world, with
  UVs and materials) and the removed collision triangles.
* `chunk_specs` - removed ground as newmodel specs at the building's new place: one model per material (UVs and
  vertex colours - the ground's two-layer blend - carried; a token collision triangle buried 50 m down) and one
  collision-only model with the ground's REAL removed collision (it differs from the visible mesh: the visible
  triangles would leave most of the floor without collision); no ledges.
"""
import copy
import struct

import numpy as np

from . import mapdata as M  # first: puts forgelib and ac3codec on the path
from . import collision, duplicate, mopp
from .newmodel import _comp, _renew
from ac3codec import Ref  # noqa: E402
from datafile import compress, decompress  # noqa: E402  (forgelib)
from forge_write import ArchiveImage, set_payload, write  # noqa: E402  (forgelib)
from transplant import object_at  # noqa: E402  (forgelib)


def is_ground(name, size):
    """Terrain: named Ground (AC3MP_FORT_Ground_FlagTower, ..._Ground_Trading, the visual-only upper floor
    Ground_BarraksMesshall_Upstairs) or a flat slab over 20 m across - not the water plane under the map
    (PH_AC3MP_FORT_WATER_01A_001, 10 m down: the cyan seen through holes in the ground)."""
    if any(w in name.upper() for w in ("WATER", "OCEAN", "SEA_")):
        return False
    return "Ground" in name or (max(size[0], size[1]) > 20 and size[2] < 2)


def inside_poly(pts, poly):
    """Points (k, 2) inside polygon (n, 2) -> bool (k,)."""
    pts = np.asarray(pts, np.float64)
    x, y = pts[:, 0], pts[:, 1]
    c = np.zeros(len(pts), bool)
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        cross = (y0 > y) != (y1 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = (x1 - x0) * (y - y0) / (y1 - y0) + x0
        c ^= cross & (x < xi)
    return c


def hull(points):
    """Convex hull (x, y), counter-clockwise (monotone chain)."""
    p = sorted(set(map(tuple, np.round(np.asarray(points, np.float64)[:, :2], 3))))
    if len(p) < 3:
        return np.array(p)

    def half(pts):
        h = []
        for q in pts:
            while len(h) >= 2 and ((h[-1][0] - h[-2][0]) * (q[1] - h[-2][1]) -
                                   (h[-1][1] - h[-2][1]) * (q[0] - h[-2][0])) <= 0:
                h.pop()
            h.append(q)
        return h
    lo, up = half(p), half(p[::-1])
    return np.array(lo[:-1] + up[:-1])


def _clip(pts, a, b, left=True):
    """Convex polygon [(xy (2,), bary (3,))] clipped to the left (or right) of line a -> b."""
    d = b - a
    side = lambda p: d[0] * (p[1] - a[1]) - d[1] * (p[0] - a[0])  # noqa: E731
    out = []
    n = len(pts)
    for i in range(n):
        (p0, w0), (p1, w1) = pts[i], pts[(i + 1) % n]
        s0, s1 = side(p0), side(p1)
        if not left:
            s0, s1 = -s0, -s1
        if s0 >= 0:
            out.append((p0, w0))
        if (s0 >= 0) != (s1 >= 0):
            t = s0 / (s0 - s1)
            out.append((p0 + t * (p1 - p0), w0 + t * (w1 - w0)))
    return out


def _area(pts):
    if len(pts) < 3:
        return 0.0
    x = np.array([p for p, _w in pts])
    return 0.5 * abs(float(np.sum(x[:, 0] * np.roll(x[:, 1], -1) - np.roll(x[:, 0], -1) * x[:, 1])))


def split_triangle(xy, poly, eps=1e-4):
    """A triangle's corners in world x, y (3, 2) against a CONVEX counter-clockwise polygon -> (inside pieces, outside
    pieces): convex pieces, each a list of barycentric weights (k, 3) in the triangle's own corner order (so winding is
    kept); ([], [whole]) / ([whole], []) when it lies all out / all in; None for a triangle seen edge-on from above
    (walls - decided by its centre)."""
    tri = [(np.asarray(xy[i], np.float64), np.eye(3)[i]) for i in range(3)]
    full = _area(tri)
    if full < 1e-6:
        return None
    outside, cur = [], tri
    for i in range(len(poly)):
        a, b = np.asarray(poly[i], np.float64), np.asarray(poly[(i + 1) % len(poly)], np.float64)
        piece = _clip(cur, a, b, left=False)
        if _area(piece) > eps * full:
            outside.append(piece)
        cur = _clip(cur, a, b, left=True)
        if _area(cur) <= eps * full:
            cur = []
            break
    inside = [cur] if cur and _area(cur) > eps * full else []
    if not inside:
        return [], [np.eye(3)]
    if not outside:
        return [np.eye(3)], []
    return [np.array([w for _p, w in pc]) for pc in inside], [np.array([w for _p, w in pc]) for pc in outside]


def _fan(n):
    return [(0, i, i + 1) for i in range(1, n - 1)]


class _Splitter:
    """Splits triangles of one vertex set against footprint polygons; new vertices are barycentric mixes of a triangle's
    corners, appended after the existing ones."""

    def __init__(self, world_xy, polys):
        self.xy, self.polys = world_xy, [hull(p) for p in polys]
        self.new = []                                     # (corner indices (3,), weights (3,))

    def vertex(self, tri, w):
        k = int(np.argmax(w))
        if w[k] > 1 - 1e-6:
            return int(tri[k])
        self.new.append((np.asarray(tri), np.asarray(w)))
        return self.base + len(self.new) - 1

    def run(self, tris, base):
        """-> (kept triangles (m, 3), removed triangles (r, 3)) in vertex indices; new vertices numbered from `base`
        (the ORIGINAL vertex count - the same on every call)."""
        self.base = base
        keep, gone = [], []
        for t in tris:
            res = None
            for poly in self.polys:
                res = split_triangle(self.xy[t], poly)
                if res is None or res[0]:
                    break
            if res is None:                               # edge-on: by its centre
                c = self.xy[t].mean(0)
                (gone if any(inside_poly([c], p)[0] for p in self.polys) else keep).append(tuple(t))
                continue
            ins, outs = res
            for pieces, dest in ((ins, gone), (outs, keep)):
                for wts in pieces:
                    ids = [self.vertex(t, w) for w in wts]
                    dest.extend((ids[a], ids[b], ids[c]) for a, b, c in _fan(len(ids)))
        return np.array(keep, np.int64).reshape(-1, 3), np.array(gone, np.int64).reshape(-1, 3)


def _mix_unit8(v, W):
    f = (v.astype(np.float64) - 127.5) / 127.5
    m = np.einsum("nk,nkc->nc", W, f)
    return np.clip(np.rint(m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9) * 127.5 + 127.5),
                   0, 255).astype(np.uint8)


def _mesh_cut(mesh_root, m4, polys):
    """A Mesh Root (deep-copied by the caller) -> (removed [(world tris (k, 3, 3), uv tris (k, 3, 2), material)],
    new vertices [(corner indices, weights)]) or None if nothing is inside. Triangles crossing the footprint's edge are
    split exactly along it (new vertices: barycentric mixes, appended); the rest of the mesh is untouched."""
    from . import meshenc
    md = M._obj(mesh_root.obj.fields["CompiledMesh"]).fields["MeshData"].fields
    fmt = md["VertexFormat"][0]
    if fmt not in meshenc.STRIDE:
        return None
    vb = b"".join(md["VertexBufferData"])
    f = meshenc.decode_vertices(vb, fmt)
    n = len(f["q"])
    if not n:
        return None
    sc = abs(int(f["w"][0])) / 8.0 / 32767.0
    local = f["q"].astype(np.float64) * sc
    world = local @ m4[:3, :3] + m4[3, :3]
    wide = md["IsIndexBuffer32bit"][0]
    idx = np.frombuffer(b"".join(md["IndexBufferData"]), "<u4" if wide else "<u2").astype(np.int64)
    mats = [x.id for x in mesh_root.obj.fields.get("CompiledMeshMaterials", [])]
    prims = [M._obj(p) for p in md["StandardPrimitives"]]
    sp = _Splitter(world[:, :2], polys)
    groups, removed, changed = [], [], False
    for k, p in enumerate(prims):
        g = {kk: struct.unpack("<i", v)[0] for kk, v in p.fields.items() if isinstance(v, bytes) and len(v) == 4}
        seg = idx[g["StartIndex"]:g["StartIndex"] + 3 * g["PrimitiveCount"]]
        t = seg[:len(seg) // 3 * 3].reshape(-1, 3)
        if g.get("Type", 1) != 1 or not len(t):
            groups.append((k, t, np.zeros((0, 3), np.int64)))
            continue
        keep, gone = sp.run(t, n)
        if len(gone) or len(keep) != len(t):
            changed = True
        groups.append((k, keep, gone))
    if not changed:
        return None
    # the new vertices: every field mixed from the triangle's corners
    if sp.new:
        C = np.array([c for c, _w in sp.new])
        W = np.array([w for _c, w in sp.new])
        dom = C[np.arange(len(C)), W.argmax(1)]
        add = {"q": np.rint(np.einsum("nk,nkc->nc", W, f["q"][C].astype(np.float64))).astype(np.int16),
               "w": f["w"][dom], "b": f["b"][dom],
               # a = the two-layer blend weight: mixed, as the colour
               "a": np.clip(np.rint(np.einsum("nk,nk->n", W, f["a"][C].astype(np.float64))), 0, 255).astype(np.uint8),
               "n": _mix_unit8(f["n"][C], W), "t": _mix_unit8(f["t"][C], W),
               "uvq": np.clip(np.rint(np.einsum("nk,nkc->nc", W, f["uvq"][C].astype(np.float64))), -32767, 32767)
               .astype(np.int16)}
        if "color" in f:
            add["color"] = np.clip(np.rint(np.einsum("nk,nkc->nc", W, f["color"][C].astype(np.float64))), 0, 255) \
                .astype(np.uint8)
        f = {key: np.concatenate([f[key], add[key]]) for key in f}
        vb2 = meshenc.pack_vertices(f, fmt)
        md["VertexBufferData"] = [vb2[i:i + 1] for i in range(len(vb2))]
        local = f["q"].astype(np.float64) * sc
        world = local @ m4[:3, :3] + m4[3, :3]
    uv = f["uvq"].astype(np.float64) / 2048.0
    total = len(f["q"])
    wide = wide or total > 0xFFFF
    new_idx, start = [], 0
    inst = {M._obj(x).fields["SubMeshIndex"][0]: M._obj(x) for x in
            M._obj(mesh_root.obj.fields["CompiledMesh"]).fields.get("InstancingData", [])}
    for k, keep, gone in groups:
        p = prims[k]
        if len(gone):
            removed.append((world[gone], uv[gone], mats[k] if k < len(mats) else None,
                            f["color"][gone] if "color" in f else None, f["a"][gone]))
        p.fields["StartIndex"] = struct.pack("<i", start)
        p.fields["PrimitiveCount"] = struct.pack("<i", len(keep))
        if len(keep):
            lo, hi = int(keep.min()), int(keep.max())
            p.fields["MinIndex"] = struct.pack("<i", lo)
            p.fields["NumVertices"] = struct.pack("<i", hi - lo + 1)
            if k in inst:
                inst[k].fields["NumVertices"] = struct.pack("<H", (hi - lo + 1) & 0xFF)
        new_idx.append(keep.reshape(-1))
        start += keep.size
    allidx = np.concatenate(new_idx) if new_idx else np.zeros(0, np.int64)
    md["IsIndexBuffer32bit"] = bytes([1 if wide else 0])
    ib = allidx.astype("<u4" if wide else "<u2").tobytes()
    md["IndexBufferData"] = [ib[i:i + 1] for i in range(len(ib))]
    shadow = md["ShadowPrimitives"]
    if shadow:
        o = M._obj(shadow[0])
        o.fields["StartIndex"] = struct.pack("<i", 0)
        o.fields["PrimitiveCount"] = struct.pack("<i", allidx.size // 3)
        if allidx.size:
            o.fields["MinIndex"] = struct.pack("<i", int(allidx.min()))
            o.fields["NumVertices"] = struct.pack("<i", int(allidx.max() - allidx.min() + 1))
        md["ShadowPrimitives"] = shadow[:1]
    return removed, sp.new


def extend_stream(stream, new, per=4):
    """A per-vertex instance stream (baked shading: `per` bytes a vertex) extended for appended vertices, each the
    barycentric mix of its triangle's corners."""
    if not stream or not new:
        return stream
    a = np.frombuffer(stream, np.uint8).reshape(-1, per).astype(np.float64)
    C = np.array([c for c, _w in new])
    W = np.array([w for _c, w in new])
    add = np.clip(np.rint(np.einsum("nk,nkc->nc", W, a[C])), 0, 255).astype(np.uint8)
    return stream + add.tobytes()


def cut(src, dest, polys, exclude=(), id_avoid=(), log=print):
    """polys: [polygon (n, 2) world]. Cuts every ground object (not in `exclude`) under them. -> (same,
    removed: [(world tris, uv tris, material)] from LOD0, removed collision: world tris (k, 3, 3))."""
    S = M.schema()
    C = M.Codec(S)
    cls = {S.name_of(h): h for h in S.types_by_hash}
    bulk = {cls[n] for n in ("TextureMap", "Animation", "MaterialTemplate", "FX", "NavMeshManager") if n in cls}
    polys = [np.asarray(p, np.float64) for p in polys]
    plo = np.min([p.min(0) for p in polys], 0)
    phi = np.max([p.max(0) for p in polys], 0)
    image = ArchiveImage.read(src)
    with M.Forge(src) as f:
        raw = {r.index: f.read(r) for r in f.records}
        recs = [r for r in f.records if r.name != "GlobalMetaFile" and len(raw[r.index]) > 8]
    payloads = {r.index: decompress(M.mapwalk.split_prefix(raw[r.index])[1]) for r in recs}
    walked = {i: M.mapwalk.walk(d) for i, d in payloads.items()}
    idents, roots, where, multis, matrices, shape_box, names = [], {}, {}, {}, {}, {}, {}
    for rec in recs:
        d = payloads[rec.index]
        for off, size, _s, _f in walked[rec.index][0]:
            o = object_at(d, off, size)
            b = o["body"]
            uid = b[2:6] if b[:2] == M.UID_MARK else None
            dec = None
            if o["type"] not in bulk:
                try:
                    dec = C.decode(b)
                except Exception:  # noqa: BLE001
                    dec = None
            idents.append((uid, dec.obj if dec else None))
            if dec is None:
                continue
            cname = S.name_of(o["type"])
            if cname in ("Entity", "Mesh", "LODSelector", "MeshShape"):
                roots.setdefault(uid, (cname, o["name"], dec))
                where.setdefault(uid, rec.index)
                names.setdefault(uid, o["name"])
            if cname == "MeshShape":
                v = collision._shape_arrays(dec.obj.fields)[0]
                shape_box[uid] = (v.min(0), v.max(0))
            if cname in ("Entity", "EntityGroup"):
                matrices[dec.obj.id] = collision._matrix(dec.obj.fields)
                mf = collision._multi_fields(S, dec.obj)
                if mf is not None:
                    multis[dec.obj.id] = [x.id for x in mf["ContainedEntities"]]
    used = duplicate.used_ids(idents, id_avoid)

    # the ground objects under the polygons: top-level entities in map cells, a Visual and / or a MeshShape
    targets = []
    for uid, (cname, name, root) in roots.items():
        if cname != "Entity" or uid in exclude or not M.mapwalk or recs[[r.index for r in recs].index(where[uid])] \
                .name.startswith("DataBlock_"):
            continue
        e = root.obj
        m4 = matrices[uid]
        inert = _comp(S, e, "InertComponent")
        sid = M._link(inert.fields["RigidBody"].fields["Shape"]) if inert else None
        box = None
        if sid in shape_box:
            lo, hi = shape_box[sid]
            c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            w = c @ m4[:3, :3] + m4[3, :3]
            box = (w.min(0), w.max(0))
        if box is None:
            # visual-only ground (Wolcott's Ground_BarraksMesshall_Upstairs - the barracks' and mess hall's upper
            # floor, no collision - cut with the rest): its box from its LOD0 model
            vis = _comp(S, e, "Visual")
            t_ = M._link(vis.fields["Object"]) if vis else None
            if t_ in roots and roots[t_][0] == "LODSelector":
                t_ = M._link(M._obj(roots[t_][2].obj.fields["LODDescs"][0]).fields["Object"])
            if t_ not in roots or roots[t_][0] != "Mesh":
                continue
            mdf = M._obj(roots[t_][2].obj.fields["CompiledMesh"]).fields["MeshData"].fields
            st = mdf["VertexStride"][0]
            vb = b"".join(mdf["VertexBufferData"])
            nv = len(vb) // st
            if not nv:
                continue
            q = np.frombuffer(vb[:nv * st], np.uint8).reshape(nv, st)[:, :8].copy().view("<i2").reshape(nv, 4)
            sc_ = 10.0 / 32767.0 if mdf["VertexFormat"][0] == 0 else abs(int(q[0, 3])) / 8.0 / 32767.0
            w = (q[:, :3].astype(np.float64) * sc_) @ m4[:3, :3] + m4[3, :3]
            box = (w.min(0), w.max(0))
        size = box[1] - box[0]
        if not is_ground(name, size) or (box[1][:2] < plo).any() or (box[0][:2] > phi).any():
            continue
        targets.append(uid)

    new_bodies, resources, entity_body, shape_swap = {}, {}, {}, {}
    removed_vis, removed_col = [], []
    for k, eid in enumerate(targets):
        fresh = duplicate.FreshIds(used, duplicate.seed(src, eid, 8100 + k))
        ent_root = copy.deepcopy(roots[eid][2])
        ent = ent_root.obj
        m4 = matrices[eid]
        ridx = where[eid]
        adds = new_bodies.setdefault(ridx, [])
        res = resources.setdefault(ridx, [])
        hit = False
        # collision
        inert = _comp(S, ent, "InertComponent")
        old_sid = M._link(inert.fields["RigidBody"].fields["Shape"]) if inert is not None else None
        if old_sid in roots and roots[old_sid][0] == "MeshShape":
            v, t, mats = collision._shape_arrays(roots[old_sid][2].obj.fields)
            w = v @ m4[:3, :3] + m4[3, :3]
            spl = _Splitter(w[:, :2], polys)
            mat_of = {}
            keep_t, gone_t = [], []
            for ti, tri in enumerate(t):
                kk, gg = spl.run(np.array([tri]), len(v))
                keep_t.extend(kk)
                gone_t.extend(gg)
                for x in list(kk) + list(gg):
                    mat_of[tuple(x)] = mats[ti] if ti < len(mats) else 0
            if gone_t and keep_t:
                if spl.new:
                    Cn = np.array([c for c, _w in spl.new])
                    Wn = np.array([wt for _c, wt in spl.new])
                    v = np.vstack([v, np.einsum("nk,nkc->nc", Wn, v[Cn])])
                    w = v @ m4[:3, :3] + m4[3, :3]
                keep_t, gone_t = np.array(keep_t), np.array(gone_t)
                shape = _renew(copy.deepcopy(roots[old_sid][2]), fresh)
                collision._set_shape(shape.obj.fields, v, keep_t, bytes(mat_of[tuple(x)] for x in keep_t))
                shape.obj.flag = 1
                adds.append((shape, cls["MeshShape"], names[old_sid] + "_C"))
                res.append(shape.obj.id)
                inert.fields["RigidBody"].fields["Shape"].id = shape.obj.id
                shape_box[shape.obj.id] = shape_box[old_sid]
                shape_swap[eid] = (old_sid, shape.obj.id)
                removed_col.append(w[gone_t])
                hit = True
        # visual: every LOD
        vis = _comp(S, ent, "Visual")
        target = M._link(vis.fields["Object"]) if vis else None
        mesh_map, new_verts = {}, {}
        meshes = []
        if target in roots and roots[target][0] == "LODSelector":
            sel = _renew(copy.deepcopy(roots[target][2]), fresh)
            for d in sel.obj.fields["LODDescs"]:
                r = M._obj(d).fields["Object"]
                if r.id in roots and roots[r.id][0] == "Mesh":
                    meshes.append((r, r.id))
        elif target in roots and roots[target][0] == "Mesh":
            sel, meshes = None, [(vis.fields["Object"], target)]
        lod0 = True
        for ref, mid in meshes:
            if mid not in mesh_map:
                nm = _renew(copy.deepcopy(roots[mid][2]), fresh)
                res_ = _mesh_cut(nm, m4, polys)
                if res_ is None:
                    mesh_map[mid] = None
                else:
                    mesh_map[mid] = nm
                    new_verts[mid] = res_[1]
                    if lod0:
                        removed_vis.extend(res_[0])
            lod0 = False
            if mesh_map[mid] is not None:
                ref.id = mesh_map[mid].obj.id
        if any(v is not None for v in mesh_map.values()):
            hit = True
            for mid, nm in mesh_map.items():
                if nm is not None:
                    nm.obj.flag = 1
                    adds.append((nm, cls["Mesh"], names.get(mid, "Mesh") + "_C"))
                    res.append(nm.obj.id)
            if sel is not None:
                sel.obj.flag = 1
                adds.append((sel, cls["LODSelector"], names.get(target, "LOD") + "_C"))
                res.append(sel.obj.id)
                vis.fields["Object"].id = sel.obj.id
            inst = M._obj(vis.fields.get("InstanceData"))
            items = ([inst] if inst is not None and S.name_of(inst.type_hash) == "MeshInstanceData" else
                     [M._obj(x) for x in inst.fields.get("LODInstanceData", [])] if inst is not None else [])
            for mi in items:
                old_mesh = M._link(mi.fields.get("Mesh")) if mi is not None else None
                if mi is not None and mesh_map.get(old_mesh) is not None:
                    mi.fields["Mesh"].link = mesh_map[old_mesh].obj.id
                    # the instance's baked shading: one entry per vertex - extended for the split's new vertices
                    cmi = M._obj(mi.fields.get("CompiledMeshInstance"))
                    if cmi is not None and cmi.fields.get("VertexBuffer"):
                        st = extend_stream(b"".join(cmi.fields["VertexBuffer"]), new_verts.get(old_mesh))
                        cmi.fields["VertexBuffer"] = [st[i:i + 1] for i in range(len(st))]
        if hit:
            entity_body[eid] = ent_root
            log(f"ground {roots[eid][1]}: cut")
        used = fresh.used

    remerge = {mid for mid, members in multis.items() if set(members) & set(shape_swap)}
    changed = set()
    for rec in recs:
        d = payloads[rec.index]
        entries, table = walked[rec.index]
        adds = new_bodies.get(rec.index, [])
        objs, touched = [], bool(adds)
        for off, size, serial, filt in entries:
            o = object_at(d, off, size)
            body = o["body"]
            uid = body[2:6] if body[:2] == M.UID_MARK else None
            if o["type"] == cls["Entity"] and uid in entity_body:
                body = C.encode(entity_body[uid])
                touched = True
            elif o["type"] == cls["Entity"] and uid in remerge:
                root = C.decode(body)
                mf = collision._multi_fields(S, root.obj)
                members = [x.id for x in mf["ContainedEntities"]]
                for k2, e2 in enumerate(members):
                    if e2 in shape_swap:
                        mf["ContainedShapes"][k2].id = shape_swap[e2][1]
                shapes = [M._link(x) for x in mf["ContainedShapes"]]
                boxes = [mopp.member_box(*shape_box[s], matrices[e2]) for e2, s in zip(members, shapes)]
                code, (ox, oy, oz, sc) = mopp.build_boxes([b[0] for b in boxes], [b[1] for b in boxes])
                mf["MoppCode"] = [code[i:i + 1] for i in range(len(code))]
                mf["MoppOffsetAndScale"] = struct.pack("<4f", ox, oy, oz, sc)
                body = C.encode(root)
                touched = True
            elif o["type"] == cls["GridCellDataBlock"] and adds:
                root = C.decode(body)
                g = root.obj.fields
                lst = g["Objects"]
                tag, extra = (lst[0].tag, lst[0].extra) if lst else (1, 0)
                have = {x.id for x in lst}
                g["Objects"] = lst + [Ref(tag, extra, u) for u in dict.fromkeys(resources[rec.index]) if u not in have]
                body = C.encode(root)
                touched = True
            objs.append((serial, M.mapwalk.object_bytes(o, body), None if filt == M.mapwalk.NO_FILTER else filt))
        for root, type_id, name in adds:
            body = C.encode(root)
            objs.append((struct.unpack("<I", body[2:6])[0], duplicate.entity_record_bytes(type_id, name, body), None))
        if touched:
            nd = M.mapwalk.rebuild(objs, table)
            pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
            assert decompress(pl) == nd
            set_payload(image, rec.index, pl)
            changed.add(rec.index)
    write(dest, image)
    with M.Forge(dest) as f:
        same = all(f.read(r) == raw[r.index] for r in f.records if r.index not in changed)
    log(f"{dest}: {len(entity_body)} ground object(s) cut, {sum(len(r[0]) for r in removed_vis)} visible and "
        f"{sum(len(c) for c in removed_col)} collision triangles removed, {len(remerge)} merged trees re-treed; "
        f"{len(changed)} records rewritten; other records byte-identical: {'yes' if same else 'NO'}")
    return same, removed_vis, (np.concatenate(removed_col) if removed_col else np.zeros((0, 3, 3)))


def chunk_specs(removed_vis, removed_col, transform, name="ED_Ground"):
    """Removed ground -> newmodel specs at the building's new place: one model per material (its LOD0 triangles, UVs,
    and those triangles as its collision), no ledges. transform: 4x4 AC3 row-vector, old world -> new world."""
    T = np.asarray(transform, np.float64)
    by_mat = {}
    for tris, uvs, mat, col, *bl in removed_vis:
        if mat is None:
            continue
        a = by_mat.setdefault(mat, ([], [], [], []))
        a[0].append(tris)
        a[1].append(uvs)
        a[2].append(col if col is not None else np.full(tris.shape[:2] + (4,), 255, np.uint8))
        a[3].append(bl[0] if bl else np.full(tris.shape[:2], 255, np.uint8))      # the two-layer blend byte
    specs = []
    for k, (mat, (tl, ul, cl, bll)) in enumerate(sorted(by_mat.items())):
        tris = np.concatenate(tl) @ T[:3, :3] + T[3, :3]               # (m, 3, 3) new world
        uv = np.concatenate(ul)
        col = np.concatenate(cl).reshape(-1, 4)
        origin = tris.reshape(-1, 3).mean(0)
        m4 = np.eye(4)
        m4[3, :3] = origin
        verts = (tris - origin).reshape(-1, 3)
        # its collision: one small triangle buried 50 m down - the ground's real collision is its own model below
        buried = np.array([[0.0, 0.0, -50.0], [0.1, 0.0, -50.0], [0.0, 0.1, -50.0]])
        specs.append(dict(name=f"{name}_{k:02d}", matrix=m4, verts=verts,
                          tris=np.arange(len(verts)).reshape(-1, 3), uv=uv.reshape(-1, 2), material=mat,
                          color=col, blend=np.concatenate(bll).reshape(-1), climb=False,
                          collision=(buried, np.array([[0, 1, 2]]))))
    col = np.asarray(removed_col, np.float64).reshape(-1, 3, 3)
    if len(col):
        tris = col @ T[:3, :3] + T[3, :3]
        origin = tris.reshape(-1, 3).mean(0)
        m4 = np.eye(4)
        m4[3, :3] = origin
        verts = (tris - origin).reshape(-1, 3)
        specs.append(dict(name=f"{name}_Col", matrix=m4, verts=verts, tris=np.arange(len(verts)).reshape(-1, 3),
                          uv=None, material=None, climb=False, visible=False))
    return specs


def overlap(a, b, n=30):
    """Fraction of convex polygon a's area inside polygon b (sampled)."""
    a = np.asarray(a, np.float64)
    xs = np.linspace(a[:, 0].min(), a[:, 0].max(), n)
    ys = np.linspace(a[:, 1].min(), a[:, 1].max(), n)
    g = np.array([(x, y) for x in xs for y in ys])
    ins = inside_poly(g, a)
    return float(inside_poly(g[ins], b).mean()) if ins.any() else 0.0


def difference_tris(old, new):
    """Convex polygon `old` minus convex polygon `new` -> triangles (k, 3, 2) (old fanned, then split by new)."""
    old = hull(old)
    fan = [(old[0], old[i], old[i + 1]) for i in range(1, len(old) - 1)]
    out = []
    for tri in fan:
        xy = np.array(tri)
        res = split_triangle(xy, hull(new))
        if res is None:
            continue
        _ins, outs = res
        for wts in outs:
            pts = wts @ xy
            out.extend([(pts[0], pts[a], pts[b]) for a, b in [(i, i + 1) for i in range(1, len(pts) - 1)]])
    return np.array(out, np.float64).reshape(-1, 3, 2)


def height_at(tris, x, y):
    """The highest walkable (upward) face of world triangles (n, 3, 3) above / below (x, y), or None."""
    T = np.asarray(tris, np.float64).reshape(-1, 3, 3)
    if not len(T):
        return None
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    n = np.cross(b - a, c - a)
    up = n[:, 2] / np.maximum(np.linalg.norm(n, axis=1), 1e-12) >= 0.5
    v0, v1 = b[:, :2] - a[:, :2], c[:, :2] - a[:, :2]
    d = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
    ok = up & (np.abs(d) > 1e-12)
    v2 = np.array([x, y]) - a[:, :2]
    dd = np.where(ok, d, 1.0)
    u = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / dd
    w = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / dd
    hit = ok & (u >= -1e-6) & (w >= -1e-6) & (u + w <= 1 + 1e-6)
    if not hit.any():
        return None
    return float((a[hit, 2] + u[hit] * (b[hit, 2] - a[hit, 2]) + w[hit] * (c[hit, 2] - a[hit, 2])).max())


def skirt(poly, inner, outer, step=1.0, gap=0.1, inset=0.15):
    """Walls along a footprint's edges where the ground inside and outside differ in height (a carried raised
    platform's side, open otherwise). inner / outer(x, y) -> z or None. -> world triangles (k, 3, 3),
    each wall in both windings (seen from either side)."""
    poly = hull(poly)
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
            p0, p1 = a + ts[j] * (b - a), a + ts[j + 1] * (b - a)
            q = [(p0[0], p0[1], min(i0, o0)), (p1[0], p1[1], min(i1, o1)),
                 (p1[0], p1[1], max(i1, o1)), (p0[0], p0[1], max(i0, o0))]
            tris += [(q[0], q[1], q[2]), (q[0], q[2], q[3]), (q[0], q[2], q[1]), (q[0], q[3], q[2])]
    return np.array(tris, np.float64).reshape(-1, 3, 3)


def ground_spec(tris_world, name, material, climb=False):
    """World triangles -> a newmodel spec (frame at their centre, UVs 2 m per repeat from box_uv)."""
    T = np.asarray(tris_world, np.float64).reshape(-1, 3, 3)
    origin = T.reshape(-1, 3).mean(0)
    m4 = np.eye(4)
    m4[3, :3] = origin
    verts = (T - origin).reshape(-1, 3)
    return dict(name=name, matrix=m4, verts=verts, tris=np.arange(len(verts)).reshape(-1, 3), uv=None,
                material=material, climb=climb)
