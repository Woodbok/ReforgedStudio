"""New visible objects: a mesh of your own becomes a game object with its own model, an existing material, collision and
climb edges. Built from the MOPP builder (mopp), the copier (duplicate), the mesh writer (meshenc) and the climb-edge
generator (climb):

* the template is an ordinary stock prop of the map: an Entity in a cell whose Visual shows a LODSelector (with a
  LODSelectorInstance), with a GuidanceSystem and an InertComponent whose shape is a MeshShape;
* its copy (duplicate.make_copy: fresh ids throughout, inner references remapped, IsMerged 0) gets a NEW Mesh
  (meshenc: the template mesh of a stock mesh using the chosen material, so the vertex format suits its shader;
  one triangle list; shadow list = the same), a NEW LODSelector (every LOD the template used now shows the new
  mesh - it never vanishes with distance), a NEW MeshShape (collision._set_shape: our MOPP tree) and generated
  ledges (climb.ledges / climb.write);
* per LOD instance data: no baked shading (HasAmbientOcclusion 0, empty stream - as 196 stock props), MeshHash 0,
  one material info naming the material;
* `visible: False` makes a collision-only object (collision + ledges, no model): an ordinary non-merged prop whose
  model is one zero-area triangle (ledges merged into a MERGED owner's climb data don't climb; a standalone
  object's do);
* it goes in the cell holding its position (GridLayout leaf; FakeCellIndex = Morton(column, row), as 1227 stock
  objects), listed in the cell's GridCellDataBlock: the entity in the activated block, the mesh / selector / shape
  (and the material, if the cell doesn't list it yet) after it, with the cell's resources.
"""
import contextlib
import copy
import os
import struct
import sys
import types

import numpy as np

from ac3codec import Ref, Root

from . import climb, collision, duplicate, meshenc
from . import mapdata as M
from datafile import compress, decompress  # noqa: E402  (forgelib)
from forge_write import ArchiveImage, set_payload, write  # noqa: E402  (forgelib)
from transplant import object_at  # noqa: E402  (forgelib)


def morton(col, row):
    m = 0
    for i in range(8):
        m |= ((col >> i) & 1) << (2 * i) | ((row >> i) & 1) << (2 * i + 1)
    return m


def box_uv(verts, tris, tile=2.0):
    """UVs for a mesh that has none: each triangle projected on the plane its normal faces most, `tile` metres per
    texture repeat. Returns per-corner UVs (m * 3, 2) and the matching unwelded geometry."""
    v = np.asarray(verts, np.float64)
    t = np.asarray(tris, np.int64)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.abs(np.cross(b - a, c - a))
    axis = n.argmax(1)
    corners = v[t.reshape(-1)]
    ax = np.repeat(axis, 3)
    uv = np.where(ax[:, None] == 0, corners[:, [1, 2]], np.where(ax[:, None] == 1, corners[:, [0, 2]], corners[:, [0, 1]]))
    return corners, np.arange(len(corners)).reshape(-1, 3), uv / tile


def _blend_of(mdl, n, two):
    """A model's (or LOD's) per-vertex blend weights: its own `blend`, else 0 on a two-layer material (its first layer:
    the stock byte 255 would draw the second), else None (the byte stays 255)."""
    b = mdl.get("blend")
    if b is not None:
        b = np.asarray(b)
        if b.ndim != 1 or len(b) != n:
            raise ValueError(f"{mdl.get('name', 'a LOD')}: blend has {b.shape} values for {n} vertices")
        return b
    return np.zeros(n) if two else None


def per_vertex(vals, n0, verts, tris, generated, n_mesh, fill, dtype):
    """Per-vertex values given for the caller's n0 vertices -> per vertex of the mesh written: handhold beams appended
    after them (verts / tris already hold them) get `fill`; generated UVs (box_uv) unweld every corner. A colour of
    the wrong length is dropped (None)."""
    if vals is None:
        return None
    v = np.asarray(vals)
    if len(v) != n0:
        return None
    v = v.astype(dtype)
    pad = lambda a, n: a if n <= len(a) else np.concatenate(  # noqa: E731
        [a, np.full((n - len(a),) + a.shape[1:], fill, dtype)])
    if generated:
        return pad(v, len(verts))[np.asarray(tris, np.int64).reshape(-1)]
    return pad(v, n_mesh)


def _renew(obj, fresh):
    """Fresh ids for an object (or a decoded Root) and everything nested in it (inner references follow)."""
    root = obj
    obj = obj.obj if isinstance(obj, Root) else obj
    mapping = {i: fresh.take() for i in dict.fromkeys(duplicate.object_ids(obj))}

    def renew(x):
        if hasattr(x, "fields"):
            x.id = mapping.get(x.id, x.id)
        elif hasattr(x, "link") and getattr(x, "link", None) is not None:
            x.link = mapping.get(x.link, x.link)
        elif hasattr(x, "id"):
            x.id = mapping.get(x.id, x.id)

    duplicate.walk_values(obj, renew)
    return root


def survey(src, id_avoid=(), log=print):
    """What a map holds that new models need (add_models, scenebuild): its records and payloads, ids in use, the
    grid, the decoded entities / meshes / selectors / shapes, the meshes using each material, each material's
    texture set / template, the per-cell resource copies, and the template prop. -> SimpleNamespace."""
    from . import materials                      # (it imports this module)
    S = M.schema()
    C = M.Codec(S)
    cls = {S.name_of(h): h for h in S.types_by_hash}
    bulk = {cls[n] for n in ("TextureMap", "Animation", "MaterialTemplate", "FX", "NavMeshManager") if n in cls}
    image = ArchiveImage.read(src)
    with M.Forge(src) as f:
        raw = {r.index: f.read(r) for r in f.records}
        recs = [r for r in f.records if r.name != "GlobalMetaFile" and len(raw[r.index]) > 8]   # empty records (some ported maps have one) carry no objects
    payloads = {r.index: decompress(M.mapwalk.split_prefix(raw[r.index])[1]) for r in recs}
    walked = {i: M.mapwalk.walk(d) for i, d in payloads.items()}

    # 1. what's in the map: ids in use, grid, templates, the meshes using each material
    idents, roots, cells, grid = [], {}, {}, None
    mesh_by_mat, mesh_cands = {}, {}
    mat_parts, set_maps, present = {}, {}, set()   # material -> (set, template); set -> maps; uids in this archive
    coll_mats = {}                                 # CollisionMaterial name -> uid
    mat_objs = {}                                  # material uid -> its decoded Obj (shader parameters)
    stored, obj_bytes = {}, {}                     # record -> uids it holds; uid -> (serial, bytes): materials.LOCAL
    for rec in recs:
        d = payloads[rec.index]
        for off, size, serial, _f in walked[rec.index][0]:
            o = object_at(d, off, size)
            b = o["body"]
            uid = b[2:6] if b[:2] == M.UID_MARK else None
            stored.setdefault(rec.index, set()).add(uid)
            if S.name_of(o["type"]) in materials.LOCAL and uid not in obj_bytes:
                obj_bytes[uid] = (serial, M.mapwalk.object_bytes(o))
            dec = None
            if o["type"] not in bulk:
                try:
                    dec = C.decode(b)
                except Exception:  # noqa: BLE001
                    dec = None
            idents.append((uid, dec.obj if dec else None))
            present.add(uid)
            if dec is None:
                continue
            cname = S.name_of(o["type"])
            if cname == "Material":
                mat_parts[uid] = (dec.obj.fields["TextureSet"].id, dec.obj.fields["MaterialTemplate"].id)
                mat_objs.setdefault(uid, dec.obj)
            elif cname == "CollisionMaterial":
                coll_mats.setdefault(o["name"], uid)
            elif cname == "TextureSet":
                set_maps[uid] = [x.id for x in dec.obj.fields["Maps"]]
            if cname in ("Entity", "Mesh", "LODSelector", "MeshShape"):
                roots[uid] = (rec.index, cname, o["name"], dec)
            if cname == "GridCellDataBlock":
                cells[rec.index] = rec.name
            if cname == "World":
                g = dec.obj.fields["GridLayout"].fields
                grid = (struct.unpack("<i", g["BottomLeftX"])[0], struct.unpack("<i", g["BottomLeftY"])[0],
                        struct.unpack("<i", g["CellSize"])[0], struct.unpack("<i", g["GridDimensionLevel0"])[0])
            if cname == "Mesh":
                # the donor a new mesh copies (vertex format, header): an ordinary mesh using the material. Boston
                # Harbour's Snow_MagicCube_01A_LOD0 names ~40 materials with Category 2 / UserCategory 0x15 (a cube
                # not drawn as a prop); a mesh copying its header is invisible in game (its collision still there)
                mats_ = dec.obj.fields.get("CompiledMeshMaterials", [])
                mdf = M._obj(dec.obj.fields["CompiledMesh"]).fields["MeshData"].fields
                # and the World's whole-city stand-in (<map>_FakeMesh_N: FakeMeshGridCount 16, draw-prim masking on)
                # is masked out cell by cell as the real cells load - a mesh copying it draws from afar only
                ordinary = (dec.obj.fields.get("Category", bytes(4)) == bytes(4) and len(mats_) <= 12
                            and mdf["VertexFormat"][0] in meshenc.STRIDE
                            and dec.obj.fields.get("FakeMeshGridCount", bytes(2)) == bytes(2)
                            and dec.obj.fields.get("UseFakeMeshDrawPrimMasking", b"\x00") == b"\x00"
                            and "FakeMesh" not in o["name"])
                for x in mats_:
                    mesh_cands.setdefault(M._link(x), []).append((not ordinary, uid))
    for m_, cands in mesh_cands.items():
        mesh_by_mat[m_] = min(cands, key=lambda c: c[0])[1]      # the first ordinary one, else the first
    template = _template(S, roots, cells)
    if template is None:
        raise ValueError("no stock prop to use as a template (Visual -> LODSelector, GuidanceSystem, MeshShape)")
    t_rec, _c, t_name, t_root = roots[template]
    t_ent = t_root.obj
    t_sel = roots[M._link(_comp(S, t_ent, "Visual").fields["Object"])][3]
    t_shape = roots[M._link(_comp(S, t_ent, "InertComponent").fields["RigidBody"].fields["Shape"])][3]
    log(f"template: {t_name}")
    # the material a hidden model names: the template's own LOD0 material (its vertex format suits it)
    t_lod0 = M._link(M._obj(t_sel.obj.fields["LODDescs"][0]).fields["Object"])
    hidden_mat = M._link(roots[t_lod0][3].obj.fields["CompiledMeshMaterials"][0]) if t_lod0 in roots else None
    g_template = None                           # a GuidanceObject to start an empty climb system from
    for uid, (ridx, cname, name, root) in roots.items():
        gs = _comp(S, root.obj, "GuidanceSystem") if cname == "Entity" else None
        if gs is not None and gs.fields.get("GuidanceObjects"):
            g_template = gs.fields["GuidanceObjects"][0]
            break
    used = duplicate.used_ids(idents, id_avoid)
    rec_by_name = {r.name: r.index for r in recs}
    # what a cell listing a material must list besides its set, maps and template: the textures its shader
    # parameters name (a two-layer material's second layer); and which materials blend two layers
    mat_deps = {m: [u for u in materials.param_deps(o, set_maps) if u in present] for m, o in mat_objs.items()}
    two_layer = {m for m, o in mat_objs.items() if materials.two_layer(o)}
    return types.SimpleNamespace(mat_deps=mat_deps, two_layer=two_layer, S=S, C=C, cls=cls, image=image, raw=raw, recs=recs, payloads=payloads,
                                 walked=walked, idents=idents, roots=roots,
                                 cells=cells, grid=grid, mesh_by_mat=mesh_by_mat, mat_parts=mat_parts,
                                 set_maps=set_maps, present=present, stored=stored, obj_bytes=obj_bytes,
                                 coll_mats=coll_mats,
                                 template=template, t_rec=t_rec, t_name=t_name, t_ent=t_ent, t_sel=t_sel,
                                 t_shape=t_shape, hidden_mat=hidden_mat, g_template=g_template, used=used,
                                 rec_by_name=rec_by_name)


def add_models(src, dest, models, id_avoid=(), log=print, workers=None):
    """models: [dict(name, matrix (4x4 AC3 row-vector, rotation + translation), verts (n, 3) in the object's frame,
    tris (m, 3), uv (n, 2) or None, material (4-byte uid, or a list: the first one this map can write), climb
    (bool), handholds (bool: beams down tall walls), floor (for handholds: the floor's z in the object's frame, or
    floor(point, wall normal) in that frame -> z / None; default the object's lowest point))].
    Optional:
    * edges: [(a, b, n0, n1, subtype)] climb edges placed by the caller, in the object's frame (n0 / n1 the two faces
      meeting there; a, b swapped to run along n0 x n1 as stock's; subtype 1 ledge, 4 pole, 14 beam; default 1).
      Added to the generated ledges (`climb`), which they replace along the same line; with climb=False, the only
      ones. Each needs collision behind it - warned about when there's none within 5 cm.
    * perches: [dict(a, b, kind "beam" | "pole" | "ledge", width, depth, subtypes (left, right), material)] or
      [(a, b[, kind])] - something to stand on along a -> b (its top's centre line): climb.perch's box (stock sizes)
      in the collision only, its two top edges in the climb data.
    * collision_materials: [CollisionMaterial name (or uid), ...] of this map (e.g. "Wood_Plank", "FuzzyZone"), and
      collision_material_index: one per collision triangle (of `collision`, else of `tris`), or one for all -
      written into the new MeshShape (Materials, IndicesMaterial). A name the map doesn't have is an error.
    * color: (n, 4) uint8 per vertex (format 3 meshes only: a tint, x2 in the shader - 255 = none), and
      blend: (n,) per vertex, 0..1 - a two-layer material's SECOND layer's weight (0 = the first layer only, 1 = the
      second only; written into the byte after the normal, which AC3MP_Tex2_* shaders blend by). Without blend a
      two-layer material draws its first layer.
      Both follow generated UVs and handhold beams (beam vertices: white / 0).
    * normals: (n, 3) per vertex, in the object's frame - used instead of the computed (area-weighted) normals; a zero
      row is computed (for a ported mesh's own stored normals: computed ones tilt at the edges a mesh shares with its
      skirts, lighting two neighbouring ground pieces differently along their join).
    * lods: [dict(verts, tris, uv (or None: generated), color, blend, normals (optional))] - lower-detail meshes shown at distance,
      LOD1, LOD2, ... in the object's frame, drawn with the model's material (collision and climb data stay LOD0's).
      The new LODSelector's levels that show a mesh (the template prop's: 3 on stock props) show LOD0, LOD1, ... in
      turn; a level past the last given LOD repeats it. Without `lods` every level shows the one mesh.
    * record: a cell record's name (e.g. the root cell's) to file the model in, instead of the leaf holding its
      position (FakeCellIndex 0xFFFF off the leaves).
    * lod_distances: [d0, d1, ...] metres - level k shows up to d_k (LODDescs[k].SwitchDistance); the last given
      distance is where the model stops drawing. Levels past the given distances take the last one ([35] alone =
      drawn to 35 m). Default: the template's (stock props: 20 / 40 / 80 m, then nothing - so pass e.g.
      [25, 60, 2000] for a big model that must stay visible).
    `workers`: processes building the models (default: AC3_EDITOR_WORKERS, else 4; 1 = all in this one) - each model's
    ledges, MOPP tree and mesh depend on nothing but its own geometry, and the output is the same whatever the count.
    -> [(name, new entity uid)]."""
    from . import materials                      # (it imports this module)
    sv = survey(src, id_avoid, log)
    S = sv.S
    C = sv.C
    cls = sv.cls
    image = sv.image
    raw = sv.raw
    recs = sv.recs
    payloads = sv.payloads
    walked = sv.walked
    roots = sv.roots
    cells = sv.cells
    grid = sv.grid
    mesh_by_mat = sv.mesh_by_mat
    mat_parts = sv.mat_parts
    set_maps = sv.set_maps
    present = sv.present
    stored = sv.stored
    obj_bytes = sv.obj_bytes
    t_rec = sv.t_rec
    t_ent = sv.t_ent
    t_sel = sv.t_sel
    t_shape = sv.t_shape
    hidden_mat = sv.hidden_mat
    used = sv.used
    rec_by_name = sv.rec_by_name
    adds, made = {}, []                         # record index -> [(serial, bytes)], [(name, uid)]
    lists = {}                                  # record index -> (entity uids, resource uids)
    # 1. per model, in order: its material, its template mesh and its fresh ids - drawn here in model order (each
    # model from the ids the ones before it took), so the models can then be built in any order, in
    # other processes (_one_model), and the build is the same
    n_ids = {}

    def ids_in(root):
        if id(root) not in n_ids:
            n_ids[id(root)] = len(dict.fromkeys(duplicate.object_ids(root.obj if isinstance(root, Root) else root)))
        return n_ids[id(root)]

    jobs = []
    for k, mdl in enumerate(models):
        hidden = not mdl.get("visible", True)
        if hidden:
            # a collision-only object is an ordinary prop (non-merged, Visual + GuidanceSystem + InertComponent)
            # whose model is one zero-area triangle: nothing is drawn. A stock collision-only object (no Visual,
            # merged in stock) cloned as non-merged crashes the game as the map goes live (null read at
            # AC3MP+0x7F1C2E); every collision-only object in Wolcott is merged.
            mdl = dict(mdl, material=hidden_mat, uv=None)
        mat = mdl["material"]
        if isinstance(mat, (list, tuple)):          # candidates: the first one a writable mesh of this map uses
            ok = [m for m in mat if m in mesh_by_mat and M._obj(roots[mesh_by_mat[m]][3].obj.fields["CompiledMesh"])
                  .fields["MeshData"].fields["VertexFormat"][0] in meshenc.STRIDE]
            if not ok:
                raise ValueError(f"{mdl['name']}: none of its materials can be written")
            mat = ok[0]
        # the template mesh (its vertex format suits the material's shader): one using the material, or - for a new
        # material (materials.apply) - one using the material it was made from
        fmt_mat = mat if mat in mesh_by_mat else mdl.get("base_material")
        if fmt_mat not in mesh_by_mat:
            raise ValueError(f"{mdl['name']}: material {mat.hex()} is used by no mesh of this map")
        m_uid = mesh_by_mat[fmt_mat]
        m_root = roots[m_uid][3]
        fmt = M._obj(m_root.obj.fields["CompiledMesh"]).fields["MeshData"].fields["VertexFormat"][0]
        if fmt not in meshenc.STRIDE:
            raise ValueError(f"{mdl['name']}: the material's meshes use vertex format {fmt}, not writable yet")
        # the ids _one_model takes, in its order: the mesh's, each LOD mesh's, the shape's, the selector's, the entity's
        n_lods = len(([] if hidden else mdl.get("lods")) or ())
        fresh = duplicate.FreshIds(used, duplicate.seed(src, mat, k + 1000 * len(models)), copy=False)
        ids = [fresh.take() for _ in range(ids_in(m_root) * (1 + n_lods) + ids_in(t_shape) + ids_in(t_sel)
                                           + ids_in(t_ent))]
        used = fresh.used
        holds = None
        if mdl.get("handholds") and mdl.get("climb", True) and callable(mdl.get("floor")):
            holds = _handholds(mdl)                  # (a function can't go to another process)
            mdl = dict(mdl, floor=None)
        jobs.append(dict(mdl=mdl, hidden=hidden, mat=mat, mesh=m_uid, fmt=fmt, ids=ids, holds=holds))
    ctx = types.SimpleNamespace(S=S, C=C, cls=cls, meshes={j["mesh"]: roots[j["mesh"]][3] for j in jobs},
                                t_ent=t_ent, t_sel=t_sel, t_shape=t_shape, t_rec=t_rec, coll_mats=sv.coll_mats,
                                grid=grid, cells=set(cells), rec_by_name=rec_by_name, mat_parts=mat_parts,
                                set_maps=set_maps, present=present, rec_names={r.index: r.name for r in recs},
                                two_layer=sv.two_layer, mat_deps=sv.mat_deps)
    for target, objs, eid, res, row, notes in _build(ctx, jobs, workers):
        for n in notes:
            log(n)
        adds.setdefault(target, []).extend(objs)
        ents, res_ = lists.setdefault(target, ([], []))
        ents.append(eid)
        res_.extend(res)
        made.append(row)

    # 2. write the records
    changed = set()
    for rec in recs:
        if rec.index not in adds:
            continue
        d = payloads[rec.index]
        entries, table = walked[rec.index]
        objs = []
        ents, res = lists[rec.index]
        for off, size, serial, filt in entries:
            o = object_at(d, off, size)
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
        # the cell holds its own copy of each material, texture set and texture map it lists (as stock cells do)
        for serial, data in adds[rec.index] + materials.local_copies(res, stored.setdefault(rec.index, set()), obj_bytes):
            objs.append((serial, data, None))
        nd = M.mapwalk.rebuild(objs, table)
        pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
        assert decompress(pl) == nd
        set_payload(image, rec.index, pl)
        changed.add(rec.index)
    write(dest, image)
    with M.Forge(dest) as f:
        same = all(f.read(r) == raw[r.index] for r in f.records if r.index not in changed)
    for name, eid, n_edges, n_tris, rname in made:
        log(f"new model {name} ({eid.hex()}): {n_tris} triangles, {n_edges} ledges, in {rname}")
    log(f"{dest}: {len(changed)} records rewritten; other records byte-identical: {'yes' if same else 'NO'}")
    return [(n, e) for n, e, _l, _t, _r in made], same


POOL_MIN_TRIS = 20000       # fewer triangles in all: built in this process (starting workers costs more than it saves)


class _Taken:
    """A model's fresh ids, drawn ahead in add_models (as FreshIds would have drawn them), handed out in turn."""

    def __init__(self, ids):
        self.ids = iter(ids)

    def take(self):
        return next(self.ids)


def _handholds(mdl, verts=None, tris=None):
    verts = np.asarray(mdl["verts"], np.float64) if verts is None else verts
    tris = np.asarray(mdl["tris"], np.int64) if tris is None else tris
    fz = mdl.get("floor")
    return climb.handholds(verts, tris, climb.ledges(verts, tris, min_len=0.2),
                           floor=fz if fz is None or callable(fz) else (lambda _p, _w, z=fz: z))


def _workers(workers):
    if workers is None:
        try:
            workers = int(os.environ.get("AC3_EDITOR_WORKERS", "4"))
        except ValueError:
            workers = 4
    # Blender's Python spawns Blender, not a worker
    return 1 if "bpy" in sys.modules else max(1, workers)


def _build(ctx, jobs, workers):
    """Each job built (_one_model) -> its results in job order: in this process, or in `workers` processes."""
    n = min(_workers(workers), len(jobs))
    if n <= 1 or sum(len(j["mdl"]["tris"]) for j in jobs) < POOL_MIN_TRIS:
        return [_one_model(ctx, j) for j in jobs]
    import concurrent.futures
    import multiprocessing
    data = {k: v for k, v in vars(ctx).items() if k not in ("S", "C", "cls")}
    ex = concurrent.futures.ProcessPoolExecutor(n, mp_context=multiprocessing.get_context("spawn"),
                                                initializer=_worker_init, initargs=(data,))
    try:
        with _bare_main():                          # (the workers start as the first jobs go in)
            # the biggest first, so a big one doesn't start last and leave the other workers idle at the end
            futs = {i: ex.submit(_worker_model, jobs[i])
                    for i in sorted(range(len(jobs)), key=lambda i: -len(jobs[i]["mdl"]["tris"]))}
        return [futs[i].result() for i in range(len(jobs))]
    finally:
        ex.shutdown(cancel_futures=True)


@contextlib.contextmanager
def _bare_main():
    """Spawned workers re-run the caller's script (as __mp_main__) unless __main__ has no file: a build script without
    an `if __name__ == "__main__":` guard would start its whole build again in each one. Workers need nothing of it."""
    main = sys.modules["__main__"]
    sys.modules["__main__"] = types.ModuleType("__main__")
    try:
        yield
    finally:
        sys.modules["__main__"] = main


_CTX = None


def _worker_init(data):
    global _CTX
    S = M.schema()
    _CTX = types.SimpleNamespace(S=S, C=M.Codec(S), cls={S.name_of(h): h for h in S.types_by_hash}, **data)


def _worker_model(job):
    return _one_model(_CTX, job)


def _one_model(ctx, job):
    """One new model's objects: its mesh(es), LOD selector, shape (MOPP tree) and entity (climb edges), encoded ->
    (its record index, [(serial, object bytes)], entity uid, the resources its cell lists, its `made` row, log lines).
    Depends on nothing but the job (add_models drew its ids) and the map's templates, so it can run anywhere."""
    S, C, cls = ctx.S, ctx.C, ctx.cls
    mdl, hidden, mat, fmt = job["mdl"], job["hidden"], job["mat"], job["fmt"]
    m_root = ctx.meshes[job["mesh"]]
    notes = []
    fresh = _Taken(job["ids"])
    m4 = np.array(mdl["matrix"], dtype=np.float64).reshape(4, 4)
    verts = np.asarray(mdl["verts"], np.float64)
    tris = np.asarray(mdl["tris"], np.int64)
    # handholds part-way up tall walls (climb.handholds as climb.beams: real protruding beams in the model and
    # collision whose own front edges become the ledges: flat-wall ledges are caught dropping from above
    # but can't be climbed to from below; beams can)
    holds, extra_uv = [], None
    if mdl.get("handholds") and mdl.get("climb", True):
        holds = job["holds"] if job["holds"] is not None else _handholds(mdl, verts, tris)
    if holds:
        bv, bt = climb.beams(holds)
        if mdl.get("uv") is not None and not hidden:
            cv, ct, cuv = box_uv(bv, bt)
            extra_uv = (cv, ct, cuv)
        tris = np.vstack([tris, bt + len(verts)])
        verts = np.vstack([verts, bv])

    # -- the mesh (unwelded per corner when UVs are generated)
    if hidden:
        c = verts.mean(0)
        mv, mt, uv = np.array([c, c, c]), np.array([[0, 1, 2]]), np.zeros((3, 2))
    elif mdl.get("uv") is None:
        mv, mt, uv = box_uv(verts, tris)
    else:
        mv, mt, uv = verts, tris, np.asarray(mdl["uv"], np.float64)
        if extra_uv is not None:                 # the beams: generated UVs, their own unwelded corners
            cv, ct, cuv = extra_uv
            n0 = len(mdl["verts"])
            mv, mt, uv = np.vstack([mv[:n0], cv]), np.vstack([mt[:len(mdl["tris"])], ct + n0]), np.vstack([uv[:n0], cuv])
    n0 = len(mdl["verts"])
    nrm = None if hidden else per_vertex(mdl.get("normals"), n0, verts, tris, mdl.get("uv") is None, len(mv), 0.0,
                                         np.float64)
    vn, vt, hand = meshenc.frame(mv, mt, uv, normals=nrm)
    two = mat in ctx.two_layer
    col = bl = None
    if not hidden:
        col = per_vertex(mdl.get("color"), n0, verts, tris, mdl.get("uv") is None, len(mv), 255, np.uint8)
        bl = per_vertex(_blend_of(mdl, n0, two), n0, verts, tris, mdl.get("uv") is None, len(mv), 0.0, np.float64)
    f = meshenc.quantise(mv, uv, vn, vt, hand, fmt, color=col, blend=bl)
    vb = meshenc.pack_vertices(f, fmt)
    ib, prims, wide = meshenc.primitives([(0, len(mv), mt)])
    mesh = _renew(copy.deepcopy(m_root), fresh)
    meshenc.set_mesh(mesh.obj, vb, ib, wide, fmt, prims, prims, [mat])
    mesh.obj.fields["Bones"] = []
    mid = mesh.obj.id
    # -- lower-detail meshes (`lods`), same material and vertex format
    lod_ids, lod_meshes = [mid], []
    for j, lod in enumerate(([] if hidden else mdl.get("lods")) or ()):
        lv = np.asarray(lod["verts"], np.float64)
        lt = np.asarray(lod["tris"], np.int64)
        ln0, lv0, lt0 = len(lv), lv, lt
        if lod.get("uv") is None:
            lv, lt, luv = box_uv(lv, lt)
        else:
            luv = np.asarray(lod["uv"], np.float64)
        lnrm = per_vertex(lod.get("normals"), ln0, lv0, lt0, lod.get("uv") is None, len(lv), 0.0, np.float64)
        lvn, lvt, lhand = meshenc.frame(lv, lt, luv, normals=lnrm)
        lcol = per_vertex(lod.get("color"), ln0, lv0, lt0, lod.get("uv") is None, len(lv), 255, np.uint8)
        lbl = per_vertex(_blend_of(lod, ln0, two), ln0, lv0, lt0, lod.get("uv") is None, len(lv), 0.0, np.float64)
        lf = meshenc.quantise(lv, luv, lvn, lvt, lhand, fmt, color=lcol, blend=lbl)
        lib, lprims, lwide = meshenc.primitives([(0, len(lv), lt)])
        lmesh = _renew(copy.deepcopy(m_root), fresh)
        meshenc.set_mesh(lmesh.obj, meshenc.pack_vertices(lf, fmt), lib, lwide, fmt, lprims, lprims, [mat])
        lmesh.obj.fields["Bones"] = []
        lod_ids.append(lmesh.obj.id)
        lod_meshes.append(lmesh)

    # -- collision
    shape = _renew(copy.deepcopy(ctx.t_shape), fresh)
    # collision: the model's own triangles, or a separate set (`collision`: (verts, tris) in its frame - the
    # carried ground's real collision differs from its visible mesh)
    cv_, ct_ = mdl.get("collision") or (verts, tris)
    cv_, ct_ = np.asarray(cv_, np.float64), np.asarray(ct_, np.int64)
    n_own = len(mdl["collision"][1]) if mdl.get("collision") else len(mdl["tris"])
    # perches (climb.perch): solid boxes in the collision only, their edges in the climb data
    perched = []
    for p in mdl.get("perches") or ():
        p = dict(p) if isinstance(p, dict) else dict(zip(("a", "b", "kind"), p))
        pv, pt, pe = climb.perch(p["a"], p["b"], p.get("kind", "beam"), p.get("width"), p.get("depth"),
                                 p.get("subtypes"))
        perched.append((pv, pt, pe, p.get("material")))
    refs, mat_idx = _collision_materials(ctx.coll_mats, shape.obj.fields, mdl, n_own, len(ct_) - n_own, perched)
    for pv, pt, _pe, _pm in perched:
        ct_ = np.vstack([ct_, pt + len(cv_)])
        cv_ = np.vstack([cv_, pv])
    collision._set_shape(shape.obj.fields, cv_, ct_, mat_idx)
    if refs is not None:
        shape.obj.fields["Materials"] = refs
    sid = shape.obj.id

    # -- the LOD selector: every level the template uses shows the new mesh
    sel = _renew(copy.deepcopy(ctx.t_sel), fresh)
    levels = []
    dists = list(mdl.get("lod_distances") or ())
    for d in sel.obj.fields["LODDescs"]:
        df = M._obj(d).fields
        r = df["Object"]
        levels.append(bool(r.id and r.id != bytes(4)))
        k_ = sum(levels) - 1                         # this level's index among the levels that show a mesh
        if levels[-1]:
            r.id = lod_ids[min(k_, len(lod_ids) - 1)]
            if dists and "SwitchDistance" in df:
                # a level past the given distances gets the last one (no range of its own): the template's own
                # (20 / 40 / 80 m) would draw the model on past the distance given as its last
                df["SwitchDistance"] = struct.pack("<f", float(dists[min(k_, len(dists) - 1)]))
        elif dists and "SwitchDistance" in df:      # empty levels stay past the last distance (ascending)
            df["SwitchDistance"] = struct.pack("<f", float(max(dists)) + len(levels))
    sel_id = sel.obj.id

    # -- the entity
    lo, hi = (verts @ m4[:3, :3]).min(0), (verts @ m4[:3, :3]).max(0)
    ent, eid = duplicate.make_copy(ctx.t_ent, m4, (lo, hi), fresh)
    if next(fresh.ids, None) is not None:
        raise AssertionError(f"{mdl['name']}: fewer fresh ids taken than add_models drew")
    vis = _comp(S, ent, "Visual")
    vis.fields["Object"].id = sel_id
    inst = M._obj(vis.fields["InstanceData"])
    for lvl, x in enumerate(inst.fields["LODInstanceData"]):
        mi = M._obj(x)
        if mi is None:
            continue
        mi.fields["Mesh"].link = lod_ids[min(lvl, len(lod_ids) - 1)]
        cmi = M._obj(mi.fields["CompiledMeshInstance"])
        if cmi is not None:
            cmi.fields["HasAmbientOcclusion"] = b"\x00"
            cmi.fields["VertexBuffer"] = []
            cmi.fields["VertexFormat"] = bytes([fmt])
            cmi.fields["MeshHash"] = bytes(8)
        infos = mi.fields["MaterialInfos"][:1]
        for info in infos:
            inf = M._obj(info).fields
            inf["MeshMaterial"].id = mat
            inf["InstanceMaterial"].id = mat
        mi.fields["MaterialInfos"] = infos
    inert = _comp(S, ent, "InertComponent")
    inert.fields["RigidBody"].fields["Shape"].id = sid
    gs = _comp(S, ent, "GuidanceSystem")
    edges = climb.ledges(verts, tris, min_len=0.2) if mdl.get("climb", True) else []
    placed = climb.oriented(list(mdl.get("edges") or []) + [e for _v, _t, pe, _m in perched for e in pe])
    if placed:
        # the caller's edges replace generated ones along the same line (a sill's ledge where a perch is)
        edges = [e for e in edges if not _along(e, placed)] + placed
        T = cv_[ct_]
        for a, b, _n0, _n1, sub in placed:
            if climb.point_tri_distance((a + b) / 2, T).min() > 0.05:
                notes.append(f"{mdl['name']}: climb edge {np.round(a, 2)} -> {np.round(b, 2)} (SubType {sub}) has "
                             f"no collision within 5 cm - an edge with nothing behind it is caught dropping "
                             f"from above but not climbed to")
    if edges:
        climb.write(gs.fields, edges)
    else:
        _empty_gs(gs)

    # -- its cell
    x0, y0, size, dim = ctx.grid
    col = int(np.floor((m4[3, 0] - x0) / size))
    row = int(np.floor((m4[3, 1] - y0) / size))
    node = row * dim + col if 0 <= col < dim and 0 <= row < dim else None
    target = ctx.rec_by_name.get(f"Cell{node:05d}_DataBlock") if node is not None else None
    if mdl.get("record"):
        # a cell named by the caller (e.g. the root cell, drawn wherever the camera is - the far view's always-drawn
        # models, as stock's region stand-ins); off the leaves FakeCellIndex is 0xFFFF as stock's
        target = ctx.rec_by_name.get(mdl["record"])
        if target not in ctx.cells:
            raise ValueError(f"{mdl['name']}: no cell record {mdl['record']}")
        k = int(mdl["record"][4:9]) if mdl["record"][4:9].isdigit() else -1
        ent.fields["FakeCellIndex"] = (struct.pack("<H", morton(k % dim, k // dim)) if 0 <= k < dim * dim
                                     else b"\xff\xff")
    elif target not in ctx.cells:
        target = ctx.t_rec
        ent.fields["FakeCellIndex"] = ctx.t_ent.fields["FakeCellIndex"]
    else:
        ent.fields["FakeCellIndex"] = struct.pack("<H", morton(col, row))
    base = mdl["name"]
    objs = []
    for cname, nm, root in (("Entity", base, Root(b"", 0, ent)), ("Mesh", f"{base}_LOD0", mesh),
                            *(("Mesh", f"{base}_LOD{j + 1}", lm) for j, lm in enumerate(lod_meshes)),
                            ("LODSelector", f"{base}_LODSelector", sel), ("MeshShape", f"{base}_Col", shape)):
        root.obj.flag = 1
        body = C.encode(root)
        objs.append((struct.unpack("<I", body[2:6])[0], duplicate.entity_record_bytes(cls[cname], nm, body)))
    ts, tp = ctx.mat_parts.get(mat, (None, None))
    res = [mid] + lod_ids[1:] + [sel_id, sid, mat] + [u for u in [ts] + ctx.set_maps.get(ts, []) + [tp] +
                                                      ctx.mat_deps.get(mat, []) if u and u in ctx.present]
    return target, objs, eid, res, (base, eid, len(edges), len(mt), ctx.rec_names[target]), notes


def _comp(S, ent, name):
    for p in ent.fields.get("Components", []):
        c = M._obj(p)
        if c is not None and S.name_of(c.type_hash) == name:
            return c
    return None


def _empty_gs(gs):
    """A climb system with no edges, in the stock form (Boston Harbour's WarehouseOpen_12x6x6_01A_001, Wolcott's
    Barracks_building_01A_001): RootIndex 0xFFFF, no nodes, no leaf index, a zero box. Trimming the template's node
    list but keeping its RootIndex (2 on a Boston Harbour roof) makes the engine read a node past the list, another
    system's leaf, and walk a null LeafsIndex: a crash at AC3MP+0x7F1C2E."""
    gs.fields["GuidanceObjects"], gs.fields["CompressPoints"] = [], []
    part = M._obj(gs.fields["Partitioner"])
    part.fields["RootIndex"] = struct.pack("<H", 0xFFFF)
    part.fields["LeafsIndex"] = []
    part.fields["ListNodes"] = []
    part.fields["Min"] = part.fields["Max"] = bytes(12)


def _along(edge, placed, tol=0.1):
    """Does a generated edge lie along one of the caller's (its midpoint within `tol` of it, parallel)?"""
    a, b = np.asarray(edge[0], np.float64), np.asarray(edge[1], np.float64)
    m, d = (a + b) / 2, (b - a) / max(np.linalg.norm(b - a), 1e-9)
    for p0, p1, *_r in placed:
        e = p1 - p0
        L2 = float(e @ e)
        if abs(d @ e) / np.sqrt(L2) < 0.98:
            continue
        t = np.clip((m - p0) @ e / L2, 0.0, 1.0)
        if np.linalg.norm(m - (p0 + t * e)) < tol:
            return True
    return False


def _collision_materials(coll_mats, shape_fields, mdl, n_own, n_holds, perched):
    """A new MeshShape's collision materials -> (its Materials refs, None = the template's left as they are; the
    per-triangle index bytes). `collision_materials`: names of the map's CollisionMaterials (or uids), resolved here;
    `collision_material_index`: one per triangle of the model's own collision (or one for all), into that list. Each
    MeshShape lists its materials and gives every triangle an index (IndicesMaterial, a byte; stock: up to 7 per
    shape). Collision materials aren't per-cell resources: stock shapes name ones stored in other records (FuzzyZone
    lives in one Wolcott cell and is named by shapes in 27 records). Handhold beams take index 0, perches their own
    `material` (else 0). Without either option: all zeros, the template's list (its first material)."""
    names = list(mdl.get("collision_materials") or [])
    want = [m for *_x, m in perched if m is not None]
    n_perch = sum(len(pt) for _v, pt, _e, _m in perched)
    if not names and not want:
        if mdl.get("collision_material_index") is not None:
            raise ValueError(f"{mdl['name']}: collision_material_index needs collision_materials")
        return None, bytes(n_own + n_holds + n_perch)

    def uid(n):
        if isinstance(n, (bytes, bytearray)) and len(n) == 4:
            return bytes(n)
        if n not in coll_mats:
            raise ValueError(f"{mdl['name']}: this map has no CollisionMaterial {n!r} (it has: "
                             f"{', '.join(sorted(coll_mats))})")
        return coll_mats[n]

    tmpl = shape_fields["Materials"]
    tag, extra = (tmpl[0].tag, tmpl[0].extra) if tmpl else (1, 0)
    uids = [uid(n) for n in names] if names else [x.id for x in tmpl[:1]]
    for m in want:
        if uid(m) not in uids:
            uids.append(uid(m))
    if len(uids) > 256:
        raise ValueError(f"{mdl['name']}: more than 256 collision materials in one shape")
    idx = mdl.get("collision_material_index", 0) if names else 0
    idx = np.full(n_own, idx, np.int64) if np.ndim(idx) == 0 else np.asarray(idx, np.int64).reshape(-1)
    if len(idx) != n_own:
        raise ValueError(f"{mdl['name']}: collision_material_index has {len(idx)} entries for {n_own} collision "
                         f"triangles")
    if len(idx) and (idx.min() < 0 or idx.max() >= len(uids)):
        raise ValueError(f"{mdl['name']}: collision_material_index outside 0..{len(uids) - 1}")
    per = [uids.index(uid(m)) if m is not None else 0 for _v, pt, _e, m in perched for _t in pt]
    return [Ref(tag, extra, u) for u in uids], bytes(idx.tolist() + [0] * n_holds + per)


def _cell_of(grid, m4, rec_by_name, cells, fallback):
    """-> (record index, column, row) of the leaf cell holding a position (column / row None off the grid)."""
    x0, y0, size, dim = grid
    col = int(np.floor((m4[3, 0] - x0) / size))
    row = int(np.floor((m4[3, 1] - y0) / size))
    if 0 <= col < dim and 0 <= row < dim:
        target = rec_by_name.get(f"Cell{row * dim + col:05d}_DataBlock")
        if target in cells:
            return target, col, row
    return fallback, None, None


def _template_collision(S, roots, cells):
    """A collision-only object in a cell: an Entity with a GuidanceSystem and an InertComponent (a MeshShape), no
    Visual (Wolcott: PH_AC3MP_FORT_Platform_MasterCol_01A_001)."""
    for uid, (ridx, cname, name, root) in roots.items():
        if cname != "Entity" or ridx not in cells:
            continue
        e = root.obj
        comps = {S.name_of(M._obj(p).type_hash) for p in e.fields.get("Components", []) if M._obj(p) is not None}
        if comps != {"GuidanceSystem", "InertComponent"}:
            continue
        shp = roots.get(M._link(_comp(S, e, "InertComponent").fields["RigidBody"].fields.get("Shape")))
        if shp and shp[1] == "MeshShape":
            return uid
    return None


def _template(S, roots, cells):
    """The first ordinary prop in a cell: Visual -> LODSelector + LODSelectorInstance, GuidanceSystem, a MeshShape."""
    for uid, (ridx, cname, name, root) in roots.items():
        if cname != "Entity" or ridx not in cells:
            continue
        e = root.obj
        vis, gs, inert = _comp(S, e, "Visual"), _comp(S, e, "GuidanceSystem"), _comp(S, e, "InertComponent")
        if not (vis and gs and inert):
            continue
        sel = roots.get(M._link(vis.fields.get("Object")))
        inst = M._obj(vis.fields.get("InstanceData"))
        shp = roots.get(M._link(inert.fields["RigidBody"].fields.get("Shape")))
        if (sel and sel[1] == "LODSelector" and inst is not None and S.name_of(inst.type_hash) == "LODSelectorInstance"
                and shp and shp[1] == "MeshShape" and gs.fields.get("GuidanceObjects")):
            return uid
    return None
