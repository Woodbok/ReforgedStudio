"""A whole scene as new map objects: the bulk form of newmodel, for maps brought in from other games.

    build(src, dest, parts, instances)

A scene from elsewhere (glTF / FBX / OBJ through Blender) is many objects showing fewer meshes, most with several
materials. As stock AC3 does it:

* each PART (one mesh at one scale) becomes ONE Mesh - one triangle list per material (CompiledMeshMaterials,
  InstancingData and the instances' MaterialInfos one per list, as every stock multi-material model: 1,032 in
  Wolcott) -, one LODSelector (every level the template uses shows it) and one MeshShape (its collision, our MOPP);
* each INSTANCE (an object showing a part) is an Entity copied from the template prop (fresh ids), its matrix
  rotation + translation only (Havok shapes don't scale: scale is baked into the part), its climb edges the part's;
* the Mesh / LODSelector / MeshShape and the material closure are stored in every cell record holding an instance of
  the part (stock stores shared meshes per cell too: 226 of Wolcott's 646 meshes are in 2+ cells).

Limits handled here: UVs are int16 / 2048 (|u|, |v| < 16) - each triangle is shifted by whole tiles toward 0 (corners
unwelded where needed), what still doesn't fit is clamped and counted; one vertex format per mesh - a part whose
materials need different formats is split; a MeshShape holds at most 65535 vertices (callers split bigger parts:
split_part). Collision per part: the visible triangles (default), its own (verts, tris), or none (a token triangle
50 m down, as the carried ground's chunks).
"""
import collections
import copy
import struct

import numpy as np

from ac3codec import Ref, Root

from . import climb, collision, duplicate, meshenc, mopp, newmodel
from . import mapdata as M
from datafile import compress, decompress  # noqa: E402  (forgelib)
from forge_write import set_payload, write  # noqa: E402  (forgelib)
from transplant import object_at  # noqa: E402  (forgelib)

MULTI_LIST = False                              # one model per material; True: one model, a list per material
UV_LIMIT = 15.99                                # |u|, |v| < 16 (int16 / 2048)
TOKEN = (np.array([[0.0, 0.0, -50.0], [0.01, 0.0, -50.0], [0.0, 0.01, -50.0]]), np.array([[0, 1, 2]]))


def fit_uvs(verts, tris, uv):
    """Each triangle's UVs shifted by whole tiles so its centre sits in [0, 1); corners shared by triangles with
    different shifts are unwelded. -> (verts, tris, uv, triangles still out of range - clamped)."""
    verts, tris, uv = np.asarray(verts, np.float64), np.asarray(tris, np.int64), np.asarray(uv, np.float64)
    if not len(tris) or np.abs(uv).max() <= UV_LIMIT:
        return verts, tris, uv, 0
    shift = np.floor(uv[tris].mean(1))                       # (m, 2) per triangle
    keys = np.concatenate([tris.reshape(-1, 1), np.repeat(shift, 3, axis=0)], axis=1)
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    v2 = verts[uniq[:, 0].astype(np.int64)]
    uv2 = uv[uniq[:, 0].astype(np.int64)] - uniq[:, 1:3]
    bad = int((np.abs(uv2[inv.reshape(-1, 3)]) > UV_LIMIT).any(axis=(1, 2)).sum())
    return v2, inv.reshape(-1, 3), np.clip(uv2, -UV_LIMIT, UV_LIMIT), bad


NOT_CLIMBABLE = ("FuzzyZone", "Z_Camonly")   # collision materials no climb / wall run happens on: no generated ledges


def weld(verts, tris, tol=1e-4, keep_mask=False):
    """Collision vertices merged by position (a mesh's corners are split by UV seams; Havok wants one). keep_mask:
    also return which input triangles were kept (degenerate ones are dropped)."""
    q = np.round(np.asarray(verts, np.float64) / tol).astype(np.int64)
    _u, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    t = inv.reshape(-1)[np.asarray(tris, np.int64)]
    keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    if keep_mask:
        return np.asarray(verts, np.float64)[first], t[keep], keep
    return np.asarray(verts, np.float64)[first], t[keep]


def collision_material_shape(sv, part, n_tris, keep=None):
    """A part's collision materials (`collision_materials`: CollisionMaterial names of this map, or None for the
    template's; `collision_material_index`: one per collision triangle - of its own collision, else of its visible
    triangles) -> (names to list after the template's first, per-triangle index bytes: 0 = the template's / handhold
    beams, 1 + k = names[k]) or None without them. `keep`: the triangles weld kept."""
    names = part.get("collision_materials")
    idx = part.get("collision_material_index")
    if not names or idx is None:
        return None
    idx = np.asarray(idx, np.int64).reshape(-1)
    if keep is not None and len(keep) == len(idx):
        idx = idx[keep]
    if len(idx) > n_tris:
        idx = idx[:n_tris]
    used = [n for n in dict.fromkeys(names) if n is not None and n in sv.coll_mats]
    slot = {n: 1 + used.index(n) for n in used}
    per = [slot.get(names[i], 0) if 0 <= i < len(names) else 0 for i in idx]
    per += [0] * (n_tris - len(per))
    return used, bytes(per)


def climbable_mask(part, n_tris, keep=None):
    """Which collision triangles may get generated ledges / rows: not on a NOT_CLIMBABLE material."""
    names = part.get("collision_materials")
    idx = part.get("collision_material_index")
    ok = np.ones(n_tris, bool)
    if not names or idx is None:
        return ok
    idx = np.asarray(idx, np.int64).reshape(-1)
    if keep is not None and len(keep) == len(idx):
        idx = idx[keep]
    bad = np.array([(names[i] or "").startswith(NOT_CLIMBABLE) if 0 <= i < len(names) else False for i in idx], bool)
    ok[:len(bad)] = ~bad[:n_tris]
    return ok


def split_part(part, max_verts=60000):
    """A part too big for one MeshShape (65535 vertices) or one 16-bit mesh -> parts cut on a grid in its own frame
    (triangles go by their centre), each under `max_verts` welded vertices. -> [(part, offset (3,))]: the piece's
    vertices are relative to `offset`, which its instances add to their translation."""
    v, t = np.asarray(part["verts"], np.float64), np.asarray(part["tris"], np.int64)
    if len(weld(v, t)[0]) <= max_verts and len(v) <= 65535:
        return [(part, np.zeros(3))]
    c = v[t].mean(1)
    lo, hi = c.min(0), c.max(0)
    n = 2
    while True:
        cell = np.maximum((hi[:2] - lo[:2]) / n, 1e-6)
        ij = np.minimum(((c[:, :2] - lo[:2]) / cell).astype(np.int64), n - 1)
        key = ij[:, 0] * n + ij[:, 1]
        pieces = []
        for k in np.unique(key):
            sel = np.nonzero(key == k)[0]
            used = np.unique(t[sel])
            pieces.append((sel, used))
        if all(len(used) <= max_verts for _s, used in pieces) or n > 64:
            break
        n *= 2
    out = []
    for sel, used in pieces:
        remap = np.full(len(v), -1, np.int64)
        remap[used] = np.arange(len(used))
        off = v[used].mean(0)
        p = dict(part, verts=v[used] - off, tris=remap[t[sel]], name=f"{part['name']}_{len(out):02d}")
        if part.get("uv") is not None:
            p["uv"] = np.asarray(part["uv"])[used]
        if part.get("tri_mat") is not None:
            p["tri_mat"] = np.asarray(part["tri_mat"])[sel]
        if part.get("collision_material_index") is not None and not isinstance(part.get("collision"), tuple):
            p["collision_material_index"] = np.asarray(part["collision_material_index"])[sel]   # (per visible triangle)
        if isinstance(part.get("collision"), tuple):
            raise ValueError(f"{part['name']}: too big, and its own collision can't be split yet")
        out.append((p, off))
    if part.get("edges"):                            # the caller's climb edges: each to the piece nearest its middle
        boxes = [(pp["verts"].min(0) + o, pp["verts"].max(0) + o) for pp, o in out]
        per = [[] for _ in out]
        for e in part["edges"]:
            mid = (np.asarray(e[0]) + np.asarray(e[1])) / 2
            d = [np.linalg.norm(np.maximum(0, np.maximum(lo - mid, mid - hi))) for lo, hi in boxes]
            j = int(np.argmin(d))
            per[j].append((np.asarray(e[0]) - out[j][1], np.asarray(e[1]) - out[j][1]) + tuple(e[2:]))
        for (pp, _o), es in zip(out, per):
            pp["edges"] = es
    return out


def _formats(sv, part):
    """-> {material uid: vertex format} for a part's materials (the format of a stock mesh using it, or using the
    material it was made from - `base_materials`)."""
    out = {}
    for mat in part["materials"]:
        fmt_mat = mat if mat in sv.mesh_by_mat else part.get("base_materials", {}).get(mat)
        if fmt_mat not in sv.mesh_by_mat:
            raise ValueError(f"{part['name']}: material {mat.hex()} is used by no mesh of this map")
        m_root = sv.roots[sv.mesh_by_mat[fmt_mat]][3]
        fmt = M._obj(m_root.obj.fields["CompiledMesh"]).fields["MeshData"].fields["VertexFormat"][0]
        if fmt not in meshenc.STRIDE:
            raise ValueError(f"{part['name']}: material {mat.hex()}'s meshes use vertex format {fmt}, not writable")
        out[mat] = (fmt, sv.mesh_by_mat[fmt_mat])
    return out


def home_cell(sv, m4, placement="position", centre=None):
    """-> (record index, FakeCellIndex bytes) for an object at `m4`. placement: "position" - the 32 m cell under it
    (stock; resources are copied into every cell using them); "single" - one cell for the whole scene (the cell under
    `centre`): each texture, mesh and shape stored once (e.g. 183 MB where per-cell placement takes 290); an int k
    - k x k blocks of cells, each block's objects in the block's middle cell (fewer copies, still local)."""
    x0, y0, size, dim = sv.grid
    x, y = (centre if placement == "single" and centre is not None else m4[3, :2])
    col, row = int(np.floor((x - x0) / size)), int(np.floor((y - y0) / size))
    if isinstance(placement, int) and placement > 1:
        col = min(dim - 1, max(0, (col // placement) * placement + placement // 2))
        row = min(dim - 1, max(0, (row // placement) * placement + placement // 2))
    cands = sorted(((abs(c - col) + abs(r - row), c, r) for c in range(max(0, col - 2), min(dim, col + 3))
                    for r in range(max(0, row - 2), min(dim, row + 3))))
    for _d, c, r in cands:                       # the nearest cell that exists (stock maps leave some out)
        target = sv.rec_by_name.get(f"Cell{r * dim + c:05d}_DataBlock")
        if target in sv.cells:
            return target, struct.pack("<H", newmodel.morton(c, r))
    return sv.t_rec, sv.roots[sv.template][3].obj.fields["FakeCellIndex"]


def estimate(src, parts, instances, placement="position", sv=None, log=print):
    """What build() would add, without writing: bytes per record (meshes, shapes, entities, and the material /
    texture copies each record would need), and the totals. Approximate for geometry (vertex stride 28, MOPP ~10
    bytes a triangle), exact for texture copies. -> dict(total, textures, geometry, per_record {name: bytes},
    texture_copies)."""
    sv = sv or newmodel.survey(src, log=lambda *_x: None)
    centre = np.mean([np.asarray(i["matrix"]).reshape(4, 4)[3, :2] for i in instances], axis=0) if instances else None
    geo = {}
    for pk, p in parts.items():
        nv, nt = len(p["verts"]), len(p["tris"])
        geo[pk] = nv * 28 + nt * 6 + nv * 12 + nt * 7 + nt * 10 + 2000
    per, tex_copies = collections.Counter(), 0
    res_in = collections.defaultdict(set)
    for inst in instances:
        m4 = np.asarray(inst["matrix"], np.float64).reshape(4, 4)
        rec, _f = home_cell(sv, m4, placement, centre)
        part = parts[inst["part"]]
        per[rec] += 2500
        if ("part", inst["part"]) not in res_in[rec]:
            res_in[rec].add(("part", inst["part"]))
            per[rec] += geo[inst["part"]]
        for mat in part["materials"]:
            ts, tp = sv.mat_parts.get(mat, (None, None))
            for u in [mat, ts, tp] + sv.set_maps.get(ts, []) + sv.mat_deps.get(mat, []):
                if u and u in sv.obj_bytes and u not in sv.stored.get(rec, ()) and u not in res_in[rec]:
                    res_in[rec].add(u)
                    per[rec] += len(sv.obj_bytes[u][1])
                    tex_copies += 1
    names = {r.index: r.name for r in sv.recs}
    geometry = 2500 * len(instances) + sum(geo[k[1]] for rs in res_in.values() for k in rs if isinstance(k, tuple))
    total = sum(per.values())
    out = dict(total=total, geometry=geometry, textures=total - geometry, texture_copies=tex_copies,
               per_record={names[r]: b for r, b in per.most_common()}, placement=placement)
    log(f"estimate ({placement}): +{total / 2**20:.0f} MB raw in {len(per)} records - geometry "
        f"{geometry / 2**20:.0f} MB, material / texture copies {(total - geometry) / 2**20:.0f} MB ({tex_copies})")
    return out


def build(src, dest, parts, instances, id_avoid=(), log=print, placement="position"):
    """parts: {key: dict(name, verts (n, 3), tris (m, 3), uv (n, 2) or None, tri_mat (m,) index into `materials`,
    materials [uid], base_materials {new material uid: the stock one it copies}, collision: None (the visible
    triangles) / (verts, tris) / "none", climb (bool, default True))}; instances: [dict(part, name, matrix (4x4 AC3
    row-vector: rotation + translation))]; placement: see home_cell. -> (ok, report dict)."""
    from . import materials
    sv = newmodel.survey(src, id_avoid, log=log)
    S, C, cls = sv.S, sv.C, sv.cls
    centre = np.mean([np.asarray(i["matrix"]).reshape(4, 4)[3, :2] for i in instances], axis=0) if instances else None
    t_ent, t_sel, t_shape = sv.t_ent, sv.t_sel, sv.t_shape
    used = sv.used
    report = collections.Counter()

    # 1. each part: its mesh(es) - one per vertex format -, selector, shape, ledges
    made = {}                                   # part key -> [dict(mesh, sel, shape, mats, edges, box, objs)]
    for pk, part in parts.items():
        verts = np.asarray(part["verts"], np.float64)
        tris = np.asarray(part["tris"], np.int64)
        tri_mat = np.zeros(len(tris), np.int64) if part.get("tri_mat") is None else np.asarray(part["tri_mat"])
        uv = part.get("uv")
        if uv is None:
            verts, tris, uv = newmodel.box_uv(verts, tris)
            tri_mat = np.zeros(len(tris), np.int64) if part.get("tri_mat") is None else tri_mat
        verts, tris, uv, bad = fit_uvs(verts, tris, uv)
        report["uv clamped triangles"] += bad
        # handholds (climb.beams of `holds` [(a, b, up, wall)]): solid always, drawn if holds_visible (in the part's
        # most used material, generated UVs) - climb.rows' climbing rows are invisible
        holds = part.get("holds") or []
        if holds:
            bv, bt = climb.beams(holds)
            report["handhold beams"] += len(holds)
            if part.get("holds_visible"):
                hv, ht, huv = newmodel.box_uv(bv, bt)
                common = int(np.bincount(tri_mat).argmax()) if len(tri_mat) else 0
                tris = np.vstack([tris, ht + len(verts)])
                tri_mat = np.concatenate([tri_mat, np.full(len(ht), common, np.int64)])
                verts, uv = np.vstack([verts, hv]), np.vstack([uv, huv])
        fmts = _formats(sv, part)
        # one model per material, one triangle list each (lists of the same material merged): models with several
        # lists draw see-through in game, the single-list model (newmodel's form) doesn't. MULTI_LIST keeps the
        # stock-like multi-list form for study.
        by_fmt = collections.OrderedDict()
        for k, mat in enumerate(part["materials"]):
            if (tri_mat == k).any():
                key = fmts[mat][0] if MULTI_LIST else (fmts[mat][0], mat)
                by_fmt.setdefault(key, []).append(k)
        # collision once per part (welded), ledges from it
        col = part.get("collision")
        keep = None
        if isinstance(col, str) and col == "none":
            cv, ct = TOKEN
        elif col is not None:
            cv, ct, keep = weld(*col, keep_mask=True)
        else:
            cv, ct, keep = weld(verts, tris, keep_mask=True)
        n_own_col = len(ct)
        if holds and not (isinstance(col, str) and col == "none") and not part.get("holds_visible"):
            cv, ct = np.vstack([cv, bv]), np.vstack([ct, bt + len(cv)])
        if len(cv) > 65535:
            raise ValueError(f"{part['name']}: {len(cv)} collision vertices (a MeshShape holds 65535) - split it")
        cmat = collision_material_shape(sv, part, len(ct), keep) if not isinstance(col, str) else None
        if cmat is not None:
            report["collision triangles with their own material"] += n_own_col
        if part.get("edges") is not None:            # the caller's (e.g. a model's own climb data): no generated ledges
            edges = [tuple(e) for e in part["edges"]]
        else:
            # generated ledges: not on FuzzyZone (stock's no-climb building walls) or camera-only collision
            ok_t = climbable_mask(part, len(ct), keep) if not isinstance(col, str) else np.ones(len(ct), bool)
            edges = climb.ledges(cv, ct[ok_t], min_len=0.2) if part.get("climb", True) and col != "none" and ok_t.any() else []
        pieces = []
        for gi, (key, mats_k) in enumerate(by_fmt.items()):
            fmt = key if MULTI_LIST else key[0]
            fresh = duplicate.FreshIds(used, duplicate.seed(src, pk.encode() if isinstance(pk, str) else bytes(pk), gi))
            # vertex ranges per material (each list's vertices contiguous: MinIndex / NumVertices)
            vb_parts, groups, mats, start = [], [], [], 0
            for k in (mats_k if MULTI_LIST else [mats_k]):
                sel = np.nonzero(np.isin(tri_mat, k))[0]
                k = k if MULTI_LIST else k[0]
                usedv = np.unique(tris[sel])
                remap = np.full(len(verts), -1, np.int64)
                remap[usedv] = np.arange(len(usedv)) + start
                vb_parts.append(usedv)
                groups.append((start, len(usedv), remap[tris[sel]]))
                mats.append(part["materials"][k])
                start += len(usedv)
            order = np.concatenate(vb_parts)
            mv, muv = verts[order], uv[order]
            mt = np.concatenate([g[2] for g in groups])
            vn, vt, hand = meshenc.frame(mv, mt, muv)
            f = meshenc.quantise(mv, muv, vn, vt, hand, fmt)
            vb = meshenc.pack_vertices(f, fmt)
            ib, prims, wide = meshenc.primitives(groups)
            mesh = newmodel._renew(copy.deepcopy(sv.roots[fmts[mats[0]][1]][3]), fresh)
            # the shadow (depth) list: ONE over the whole mesh, as stock multi-material meshes (Wolcott: 1,004 of
            # 1,034); with one per material the model draws see-through
            shadow = [{"MinIndex": 0, "IsUsingDepthOnlyBuffers": 0, "NumVertices": len(mv), "StartIndex": 0,
                       "PrimitiveCount": len(mt), "Type": 1}]
            meshenc.set_mesh(mesh.obj, vb, ib, wide, fmt, prims, shadow, mats)
            mesh.obj.fields["Bones"] = []
            sel_ = newmodel._renew(copy.deepcopy(t_sel), fresh)
            for d in sel_.obj.fields["LODDescs"]:
                r = M._obj(d).fields["Object"]
                if r.id and r.id != bytes(4):
                    r.id = mesh.obj.id
            # collision: on the first format's piece only (one body per instance)
            shape = None
            if gi == 0:
                shape = newmodel._renew(copy.deepcopy(t_shape), fresh)
                if cmat is not None:                 # each triangle its own map's collision material (e.g. FuzzyZone)
                    used_names, per = cmat
                    tmpl = shape.obj.fields["Materials"]
                    tag, extra = (tmpl[0].tag, tmpl[0].extra) if tmpl else (1, 0)
                    shape.obj.fields["Materials"] = list(tmpl[:1]) + [Ref(tag, extra, sv.coll_mats[n]) for n in used_names]
                    collision._set_shape(shape.obj.fields, cv, ct, per)
                else:
                    collision._set_shape(shape.obj.fields, cv, ct, bytes(len(ct)))
            else:
                shape = newmodel._renew(copy.deepcopy(t_shape), fresh)
                collision._set_shape(shape.obj.fields, *TOKEN, bytes(1))
            objs = []
            for cname, nm, root in (("Mesh", f"{part['name']}_{gi}_LOD0", mesh),
                                    ("LODSelector", f"{part['name']}_{gi}_LODSelector", sel_),
                                    ("MeshShape", f"{part['name']}_{gi}_Col", shape)):
                root.obj.flag = 1
                body = C.encode(root)
                objs.append((struct.unpack("<I", body[2:6])[0], duplicate.entity_record_bytes(cls[cname], nm, body)))
            pieces.append(dict(mesh=mesh.obj.id, sel=sel_.obj.id, shape=shape.obj.id, mats=mats, fmt=fmt,
                               edges=edges if gi == 0 else [], objs=objs, box=(mv.min(0), mv.max(0)),
                               n_tris=len(mt)))
            used = fresh.used
            report["meshes"] += 1
            report["mesh triangles"] += len(mt)
        made[pk] = pieces
        report["parts"] += 1
        report["collision triangles"] += len(ct)

    # 2. each instance: entities (one per piece) in the cell holding it
    adds, lists, out_cells = {}, {}, collections.Counter()
    cell_res = collections.defaultdict(dict)    # record index -> {uid: object bytes} (part resources, once per cell)
    new_ents = []
    for k, inst in enumerate(instances):
        m4 = np.asarray(inst["matrix"], np.float64).reshape(4, 4)
        if not np.allclose(np.linalg.norm(m4[:3, :3], axis=1), 1, atol=1e-3):
            raise ValueError(f"{inst['name']}: its matrix scales (bake the scale into its part)")
        target, fake = home_cell(sv, m4, placement, centre)
        for pi, pc in enumerate(made[inst["part"]]):
            fresh = duplicate.FreshIds(used, duplicate.seed(src, pc["mesh"], k * 8 + pi))
            lo, hi = pc["box"]
            corners = np.array([[a, b, c] for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])])
            w = corners @ m4[:3, :3]
            ent, eid = duplicate.make_copy(t_ent, m4, (w.min(0), w.max(0)), fresh)
            used = fresh.used
            vis = newmodel._comp(S, ent, "Visual")
            vis.fields["Object"].id = pc["sel"]
            inst_d = M._obj(vis.fields["InstanceData"])
            for x in inst_d.fields["LODInstanceData"]:
                mi = M._obj(x)
                if mi is None:
                    continue
                mi.fields["Mesh"].link = pc["mesh"]
                cmi = M._obj(mi.fields["CompiledMeshInstance"])
                if cmi is not None:
                    cmi.fields["HasAmbientOcclusion"] = b"\x00"
                    cmi.fields["VertexBuffer"] = []
                    cmi.fields["VertexFormat"] = bytes([pc["fmt"]])
                    cmi.fields["MeshHash"] = bytes(8)
                proto = mi.fields["MaterialInfos"][0]
                infos = []
                for mat in pc["mats"]:
                    info = copy.deepcopy(proto)
                    inf = M._obj(info).fields
                    inf["MeshMaterial"].id = mat
                    inf["InstanceMaterial"].id = mat
                    infos.append(info)
                mi.fields["MaterialInfos"] = infos
            newmodel._comp(S, ent, "InertComponent").fields["RigidBody"].fields["Shape"].id = pc["shape"]
            gs = newmodel._comp(S, ent, "GuidanceSystem")
            if pc["edges"]:
                climb.write(gs.fields, pc["edges"])
            else:
                newmodel._empty_gs(gs)
            ent.fields["FakeCellIndex"] = fake
            ent.flag = 1
            body = C.encode(Root(b"", 0, ent))
            adds.setdefault(target, []).append(
                (struct.unpack("<I", eid)[0], duplicate.entity_record_bytes(cls["Entity"], inst["name"] if pi == 0
                                                                          else f"{inst['name']}_{pi}", body)))
            ents, res = lists.setdefault(target, ([], []))
            ents.append(eid)
            for serial, data in pc["objs"]:
                cell_res[target].setdefault(serial, data)
            res.extend([pc["mesh"], pc["sel"], pc["shape"]])
            for mat in pc["mats"]:
                ts, tp = sv.mat_parts.get(mat, (None, None))
                res.extend([mat] + [u for u in [ts] + sv.set_maps.get(ts, []) + [tp] + sv.mat_deps.get(mat, [])
                                    if u and u in sv.present])
            new_ents.append((eid, target, pc))
            out_cells[target] += 1
        report["instances"] += 1
    report["entities"] = len(new_ents)

    # 3. write the records (as newmodel: entities in the activated block, resources after, local copies)
    changed = set()
    for rec in sv.recs:
        if rec.index not in adds:
            continue
        d = sv.payloads[rec.index]
        entries, table = sv.walked[rec.index]
        ents, res = lists[rec.index]
        objs = []
        for off, size_, serial, filt in entries:
            o = object_at(d, off, size_)
            body = o["body"]
            if o["type"] == cls["GridCellDataBlock"]:
                root = C.decode(body)
                g = root.obj.fields
                n = struct.unpack("<I", g["NumberOfObjectsToActivate"])[0]
                lst = g["Objects"]
                tag, extra = (lst[0].tag, lst[0].extra) if lst else (1, 0)
                have = {x.id for x in lst}
                tail = [Ref(tag, extra, u) for u in dict.fromkeys(res) if u not in have]
                g["Objects"] = lst[:n] + [Ref(tag, extra, u) for u in ents] + lst[n:] + tail
                g["NumberOfObjectsToActivate"] = struct.pack("<I", n + len(ents))
                body = C.encode(root)
            objs.append((serial, M.mapwalk.object_bytes(o, body), None if filt == M.mapwalk.NO_FILTER else filt))
        for serial, data in adds[rec.index] + list(cell_res[rec.index].items()) + materials.local_copies(
                res, sv.stored.setdefault(rec.index, set()), sv.obj_bytes):
            objs.append((serial, data, None))
        nd = M.mapwalk.rebuild(objs, table)
        pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
        assert decompress(pl) == nd
        set_payload(sv.image, rec.index, pl)
        changed.add(rec.index)
    write(dest, sv.image)

    # 4. read back: other records byte-identical; every new entity decodes and finds its selector, mesh and shape in
    # its own record; every new shape's tree reaches all its triangles
    with M.Forge(dest) as f:
        same = all(f.read(r) == sv.raw[r.index] for r in f.records if r.index not in changed)
        found = collections.defaultdict(set)
        trees_ok = True
        for r in f.records:
            if r.index not in changed:
                continue
            dd = decompress(M.mapwalk.split_prefix(f.read(r))[1])
            for off, size_, _s, _f in M.mapwalk.walk(dd)[0]:
                o = object_at(dd, off, size_)
                uid = o["body"][2:6]
                found[r.index].add(uid)
                if o["type"] == cls["MeshShape"] and any(uid == pc["shape"] for ps in made.values() for pc in ps):
                    fl = C.decode(o["body"]).obj.fields
                    nt = len(fl["IndicesTriangles"]) // 3
                    reached, _h, _s2 = mopp.walk(b"".join(fl["MoppCode"]))
                    trees_ok &= sorted(t for t, _p in reached) == list(range(nt))
    missing = [(eid.hex(), what) for eid, rec, pc in new_ents
               for what in ("entity", "mesh", "sel", "shape")
               if (eid if what == "entity" else pc[what]) not in found[rec]]
    ok = same and trees_ok and not missing
    log(f"{dest}: {report['parts']} parts -> {report['meshes']} meshes ({report['mesh triangles']} triangles), "
        f"{report['entities']} entities for {report['instances']} instances in {len(changed)} records; "
        f"{report['collision triangles']} collision triangles; UV-clamped triangles {report['uv clamped triangles']}; "
        f"read back: {'yes' if ok else 'NO'}" + (f" (missing {missing[:3]})" if missing else ""))
    report["records"] = len(changed)
    return ok, report
