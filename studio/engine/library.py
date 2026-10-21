"""Reforged Studio's asset library: every model of every map in the release, to place in the open map.

An ASSET is one model (a Mesh an object of a map cell shows) of one map archive; its key is "<archive>|<mesh uid hex>".

* **Index** (per map, <workspace>/cache/studio/library/<map>_<size>_<time>_l<v>.json): every model a cell object shows - name,
  size, triangles, how many objects show it, its materials, the object it was read from (the "representative": the
  first plain cell object showing it) and whether that one can be copied (same-map placements are copies of it -
  duplicate.py, with the map's own collision and climb data). Made from ac3editor.mapdata, one map per child process
  (a map's data is 0.5-3 GB in memory while it's read; the child's memory goes back when it ends).
* **Extract** (per asset, <workspace>/cache/studio/assets/<map>_<size>_<time>/<mesh uid>/): the model as a scene part
  (SCENE_FORMAT.md, sceneport.save's form: scene.json + arrays.npz), its colour textures copied in as DDS pictures,
  its collision the representative's own solid shapes (layer 1) when it has any that fit the model, else None (the
  visible triangles). Also a child process per source map.
* **Climbing** (a placement's "climb"): "native" (default for models) - the representative's own climb edges
  (its GuidanceSystem, read with the model: climb.npz), none if it has none; "generated" (default for primitives) -
  ledges on its collision's top edges and MP handhold rows up big facades; "none" (default for ground parts).
* **Ground** ("ground:" + JSON {"a": archive, "fp": footprint polygon (source world x, y), "ex": [uids not to take]}):
  the ground objects under a building's footprint, cut to it (terrain.cut, as the carry does) - a building asset's floors
  and plot, e.g. Fort Wolcott's barracks' upper floor, which is a ground object it shares with the mess hall. Kept as
  the source's triangles + materials (ground.npz) in a folder like a model's; built on its own map as the carry's
  models (stock materials, vertex colours: terrain.chunk_specs), on another map through sceneport.
* **Asset scene** (<workspace>/cache/studio/assets/scene_<hash>.ac3s): the assets the open project uses, in the scene cache
  format the app memory-maps (scene_cache.py), mesh k / entity k = asset k - the app draws placed models from it
  before they are built.
* **Build** (plan.py, "places"): sceneport.build_scene puts the parts into the map - a new material per colour picture
  on the map's own base material, the visible (or the source's) triangles as collision, climb edges on top edges
  (climb.ledges), MP handhold rows up big facades, the navmesh of the cells it touches made again.

    python studio/engine/library.py index <archive>                 one map's index (the worker runs this)
    python studio/engine/library.py extract <archive> <mesh hex>... models of one map as asset folders
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ac3editor.workspace import WORK  # noqa: E402  (the workspace: builds, caches)
from ac3editor import mapdata  # noqa: E402

INDEX_VERSION = 2           # the index's format (blend-group markers left out); a new value re-indexes every map
ASSET_VERSION = 8           # the asset folders' format; a new value re-extracts. An asset carries: its own climb data
                            # (climb.npz), merged objects' climb data included; a merged piece's collision = its member
                            # shape in the cell's merged object; own collision reaching up to 10 m past the model; each
                            # collision triangle's CollisionMaterial by name (colmats.json); a building part's own
                            # collision whatever its size, and none (not its visible triangles) when it has none
NOT_CLIMBABLE = ("FuzzyZone", "Z_Camonly")   # collision materials no wall climb / run happens on (README "Collision
                                            # materials"): never given generated ledges or handhold rows
BLEND = "GameplayCoordinatorComponent"


def is_blend_marker(components):
    """A blend group's marker (the disc that poses a static NPC group: GameplayCoordinatorComponent, no collision -
    AC3MP_GEN_Static_Group_Posing_*): left out of the library and of assets (blend groups are their own kind). A bench whose
    coordinator seats NPCs has collision and stays (a plain bench)."""
    comps = list(components or [])
    return BLEND in comps and "InertComponent" not in comps
ASSET_SCENE_VERSION = 2     # the asset scene's format (meshes carry their source uid)
LIB = os.path.join(WORK, "cache", "studio", "library")
ASSETS = os.path.join(WORK, "cache", "studio", "assets")
SHAPE_MARGIN = 10.0         # a representative's collision is the model's own when it lies within its box + 10 m (a
                            # tighter margin drops real collision: Fort Wolcott's stair prop has a 4.2 m landing past
                            # its model, which carries the barracks' upper-floor middle)
SOLID_LAYER = 1


def _stamp(archive):
    st = os.stat(archive)
    return f"{os.path.basename(archive)}_{st.st_size}_{int(st.st_mtime)}"


def index_path(archive):
    return os.path.join(LIB, f"{_stamp(archive)}_l{INDEX_VERSION}.json")


def asset_dir(archive, mesh_hex):
    """mesh_hex: "<mesh uid>" (the model as its first plain object has it) or "<mesh uid>#<entity uid>" (as that one
    object of the map has it: its own collision and climb data - a building's parts)."""
    return os.path.join(ASSETS, f"{_stamp(archive)}_a{ASSET_VERSION}", mesh_hex.replace("#", "_"))


def key_of(archive, mesh_hex):
    return f"{os.path.abspath(archive)}|{mesh_hex}"


def split_key(key):
    archive, mesh_hex = key.rsplit("|", 1)
    return archive, mesh_hex


def _python():
    return sys.executable


def _child(args, log=print):
    """Runs this file in a child process; its output goes to the log. -> exit code."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([_python(), os.path.abspath(__file__)] + args, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for line in p.stdout:
        if line.strip():
            log(line.rstrip())
    return p.wait()


# ------------------------------------------------------------------------------------------------ the index --

def _cell_objects(md):
    """Entities of the map cells showing a model this archive holds, the plain ones first (top-level, decodable)."""
    ents = [e for e in md.entities if e.record.startswith("Cell") and e.mesh in md.meshes]
    return sorted(ents, key=lambda e: (e.components == ["(not decoded)"], e.group is not None))


def _solid_shapes(md, e, box):
    """The representative's own solid collision, (verts, tris) in its frame, when it fits the model's box (box None:
    whatever it is - a building part read as ITS object: the barracks' piece /14 carries the whole shell's FuzzyZone
    collision, far past its own model)."""
    vs, ts, n = [], [], 0
    for s in e.shapes:
        if s not in md.shapes or md.shape_layer.get(s, SOLID_LAYER) != SOLID_LAYER:
            continue
        _name, v, t = md.shapes[s]
        if not len(t):
            continue
        if box is not None and ((v.min(0) < box[0] - SHAPE_MARGIN).any() or (v.max(0) > box[1] + SHAPE_MARGIN).any()):
            return None
        vs.append(v.astype(np.float64))
        ts.append(t.astype(np.int64) + n)
        n += len(v)
    if not vs:
        return None
    return np.vstack(vs), np.vstack(ts)


def build_index(archive, log=print):
    """-> the index dict of one map (and writes it)."""
    import splib
    if splib.is_sp(archive):                    # a singleplayer archive (splib.py)
        os.makedirs(LIB, exist_ok=True)
        return splib.build_index(archive, index_path(archive), log=log)
    from maps import title
    md = mapdata.load(archive, log=log)
    reps, uses, blend = {}, {}, set()
    for e in _cell_objects(md):
        if is_blend_marker(e.components):
            blend.add(e.mesh)
            continue
        uses[e.mesh] = uses.get(e.mesh, 0) + 1
        reps.setdefault(e.mesh, e)
    entries = []
    for mid, e in reps.items():
        name, v, t = md.meshes[mid]
        if not len(t) or not len(v):
            continue
        uv, prim, mats = md.mesh_extra.get(mid, (None, np.zeros(len(t), np.int32), []))
        lo, hi = v.min(0).astype(np.float64), v.max(0).astype(np.float64)
        names, textured = [], 0
        for k in sorted(set(int(p) for p in prim)):
            m = mats[k] if k < len(mats) else None
            mname, path = md.materials.get(m, ("(outside this archive)", None))
            names.append(mname.strip())
            textured += int(path is not None and os.path.exists(path))
        col = _solid_shapes(md, e, (lo, hi))
        entries.append(dict(
            mesh=mid.hex(), name=name, nv=int(len(v)), nt=int(len(t)),
            size=[round(float(x), 2) for x in hi - lo], lo=[round(float(x), 3) for x in lo],
            uses=uses[mid], materials=names[:6], n_materials=len(names), textured=textured,
            rep=e.uid.hex(), rep_name=e.name, group=e.group or "",
            copyable=e.components != ["(not decoded)"],
            collision="own" if col is not None else ("other" if e.shapes else "none"),
            ground="Ground" in name or "Terrain" in name))
    entries.sort(key=lambda x: x["name"].lower())
    out = dict(version=INDEX_VERSION, archive=os.path.abspath(archive), title=title(archive), entries=entries)
    os.makedirs(LIB, exist_ok=True)
    tmp = index_path(archive) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f)
    os.replace(tmp, index_path(archive))
    log(f"library: {title(archive)}: {len(entries)} models ({len(blend - set(reps))} blend-group marker model(s) left out)")
    return out


class Indexer:
    """Indexes the release's maps in the background, one child process at a time (the worker answers other
    requests meanwhile). state(maps) -> what's ready."""

    def __init__(self, log=print):
        self.log = log
        self.thread = None
        self.failed = {}
        self.current = None
        self.lock = threading.Lock()

    def state(self, maps_, prefer=None, wanted=()):
        """wanted: SP archives to index (they are big - Boston takes about 6 minutes the first time - so they are
        only read when asked for; an MP map always is)."""
        out, missing = [], []
        for m in maps_:
            p = m["path"]
            ready = os.path.exists(index_path(p))
            want = m.get("kind") != "sp" or p in wanted or ready
            if not ready and want and p not in self.failed:
                missing.append(p)
            out.append(dict(archive=p, title=m["title"], kind=m.get("kind", ""), sp=m.get("kind") == "sp", wanted=want,
                            index=index_path(p) if ready else None, error=self.failed.get(p),
                            indexing=p == self.current))
        missing.sort(key=lambda p: next(m["title"] for m in maps_ if m["path"] == p) != prefer)
        with self.lock:
            if missing and (self.thread is None or not self.thread.is_alive()):
                self.thread = threading.Thread(target=self._run, args=(missing,), daemon=True)
                self.thread.start()
        busy = self.thread is not None and self.thread.is_alive()
        return dict(maps=out, busy=busy, missing=len(missing))

    def _run(self, paths):
        for p in paths:
            if os.path.exists(index_path(p)):
                continue
            self.current = p
            self.log(f"library: indexing {os.path.basename(p)} ({paths.index(p) + 1} of {len(paths)})...")
            code = _child(["index", p], log=self.log)
            if code or not os.path.exists(index_path(p)):
                self.failed[p] = f"indexing failed (exit {code})"
        self.current = None


# ---------------------------------------------------------------------------------------------- extraction --

NATIVE_NEAR = 0.2          # a climb edge belongs to a model when both its ends are this close to the model's surface


_WALKS = {}


def map_walk(archive):
    """One decode of a map's entities (cached per process): {"edges": every climb edge in world space [(a, b, n0, n1,
    subtype)] - each entity's own GuidanceSystem and the merged objects' - "members": {member uid: [MeshShape uid]}
    (the cells' merged collision: a building's EntityGroup pieces and collision-only objects keep their collision
    there, not in their own InertComponent), "info": {entity uid: (name, has a Visual, 4x4 matrix)}, "edge_owner": per
    edge the uid of the entity whose GuidanceSystem holds it (a merged object holds its pieces' edges)}."""
    if archive in _WALKS:
        return _WALKS[archive]
    from ac3editor import climb, collision
    S = mapdata.schema()
    C = mapdata.Codec(S)
    edges, members, info, shape_mats, colmats, owners = [], {}, {}, {}, {}, []
    for _rec, name, t, body, _layer in mapdata.iter_objects_ex(archive):
        if len(body) < 6 or t not in S.types_by_hash:
            continue
        cn = S.name_of(t)
        if cn not in ("Entity", "EntityGroup", "MeshShape", "CollisionMaterial"):
            continue
        try:
            o = C.decode(body).obj
        except Exception:  # noqa: BLE001 - undecodable: nothing we can read
            continue
        if cn == "MeshShape":
            shape_mats[o.id] = ([mapdata._link(x) for x in o.fields.get("Materials", [])],
                                bytes(b"".join(o.fields.get("IndicesMaterial", []))))
            continue
        if cn == "CollisionMaterial":
            cf = o.fields.get("CollisionFilterInfo")
            layer = struct.unpack("<I", cf.fields["Layer"])[0] if cf is not None and "Layer" in cf.fields else None
            colmats[o.id] = (name, layer, o.fields.get("SoundType", b"").hex())
            continue
        for k, e in enumerate([o] + [mapdata._obj(r) for r in o.fields.get("Entities", [])]):
            if e is None or "GlobalMatrix" not in getattr(e, "fields", {}):
                continue
            M4 = np.array(struct.unpack("<16f", e.fields["GlobalMatrix"]), np.float64).reshape(4, 4)
            comps = [mapdata._obj(c) for c in e.fields.get("Components", [])]
            comps = [c for c in comps if c is not None]
            info[e.id] = (name if k == 0 else f"{name}/{k - 1}", any(S.name_of(c.type_hash) == "Visual" for c in comps), M4)
            mf = collision._multi_fields(S, e)
            if mf is not None:
                for m, sh in zip(mf["ContainedEntities"], mf["ContainedShapes"]):
                    sid = mapdata._link(sh)
                    if sid:
                        members.setdefault(m.id, []).append(sid)
            R = M4[:3, :3]
            NR = np.linalg.inv(R).T                     # normals: inverse-transpose
            for g in comps:
                if S.name_of(g.type_hash) != "GuidanceSystem":
                    continue
                pts, es = climb.read(g.fields)
                if not len(es):
                    continue
                W = pts @ R + M4[3, :3]
                for i0, i1, sub, n0, n1 in es:
                    if i0 < len(W) and i1 < len(W):
                        m0, m1 = n0 @ NR, n1 @ NR
                        edges.append((W[i0], W[i1], m0 / max(np.linalg.norm(m0), 1e-9), m1 / max(np.linalg.norm(m1), 1e-9), sub))
                        owners.append(e.id)
    _WALKS[archive] = dict(edges=edges, members=members, info=info, shape_mats=shape_mats, colmats=colmats,
                           edge_owner=owners)
    return _WALKS[archive]


def world_climb_edges(archive):
    """Every climb edge of a map in world space (map_walk)."""
    return map_walk(archive)["edges"]


def shape_tri_materials(md, sids, walk):
    """Per collision triangle (the shapes concatenated as _solid_shapes / merged_collision do: solid, non-empty) its
    CollisionMaterial name ("" where the shape's material list doesn't read)."""
    out = []
    for sid in sids:
        if sid not in md.shapes or md.shape_layer.get(sid, SOLID_LAYER) != SOLID_LAYER:
            continue
        _nm, _v, t = md.shapes[sid]
        if not len(t):
            continue
        mats, idx = walk["shape_mats"].get(sid, ([], b""))
        names = [walk["colmats"].get(m, ("",))[0] for m in mats]
        if len(idx) >= len(t) and names:
            out += [names[i] if i < len(names) else "" for i in idx[:len(t)]]
        else:
            out += [names[0] if names else ""] * len(t)
    return out


def merged_collision(md, e, walk):
    """A merged piece's own collision - its member shape(s) in the cell's merged object, solid ones - as (verts,
    tris) in its frame, or None. (Fort Wolcott's barracks: its pieces' collision is all there; the stair prop's
    includes the 4.2 m landing its model doesn't draw.)"""
    vs, ts, n = [], [], 0
    for sid in walk["members"].get(e.uid, []):
        if sid not in md.shapes or md.shape_layer.get(sid, SOLID_LAYER) != SOLID_LAYER:
            continue
        _nm, v, t = md.shapes[sid]
        if not len(t):
            continue
        vs.append(v.astype(np.float64))
        ts.append(t.astype(np.int64) + n)
        n += len(v)
    if not vs:
        return None
    return np.vstack(vs), np.vstack(ts)


def native_climb(md, reps, archive, log=print):
    """The climb edges models have in their map: reps {mesh uid: the entity read for it} -> {mesh uid: [(a, b, n0,
    n1, subtype)] in the model's frame}: the map's climb edges (world_climb_edges) lying on that entity's model
    surface (both ends within NATIVE_NEAR of it) - its own climb data or what its building's merged object holds
    for it."""
    from ac3editor import climb
    if not reps:
        return {}
    E = world_climb_edges(archive)
    if not E:
        return {}
    A = np.array([e[0] for e in E])
    B = np.array([e[1] for e in E])
    out = {}
    for mid, e in reps.items():
        _n, v, t = md.meshes[e.mesh]
        M = np.array(e.matrix, np.float64).reshape(4, 4)
        W = v.astype(np.float64) @ M[:3, :3] + M[3, :3]
        lo, hi = W.min(0) - NATIVE_NEAR, W.max(0) + NATIVE_NEAR
        cand = np.nonzero(((A >= lo) & (A <= hi) & (B >= lo) & (B <= hi)).all(1))[0]
        if not len(cand):
            continue
        T = W[t.astype(np.int64)]
        inv = np.linalg.inv(M[:3, :3])
        mine = []
        for k in cand:
            a, b = A[k], B[k]
            def near(p, T=T):
                sel = ((T.min(1) - NATIVE_NEAR <= p) & (T.max(1) + NATIVE_NEAR >= p)).all(1)
                return float(climb.point_tri_distance(p, T[sel]).min()) if sel.any() else 1e9
            if near(a) <= NATIVE_NEAR and near(b) <= NATIVE_NEAR:
                la, lb = (a - M[3, :3]) @ inv, (b - M[3, :3]) @ inv
                n0, n1 = E[k][2] @ M[:3, :3].T, E[k][3] @ M[:3, :3].T
                mine.append((la, lb, n0 / max(np.linalg.norm(n0), 1e-9), n1 / max(np.linalg.norm(n1), 1e-9), E[k][4]))
        if mine:
            out[mid] = mine
    log(f"climb data: {len(E)} edge(s) in the map, {sum(len(x) for x in out.values())} on the {len(reps)} model(s) read")
    return out


def save_colmats(folder, per_tri, walk):
    """Per collision triangle its CollisionMaterial name (colmats.json: the names with their layer / sound type;
    colmat_idx.npy: an index per triangle)."""
    names = sorted(set(per_tri))
    info = {}
    for _uid, (n, layer, sound) in walk["colmats"].items():
        if n in names and n not in info:
            info[n] = [layer, sound]
    with open(os.path.join(folder, "colmats.json"), "w", encoding="utf-8") as f:
        json.dump(dict(names=names, info=info), f)
    np.save(os.path.join(folder, "colmat_idx.npy"), np.array([names.index(n) for n in per_tri], np.int32))


def load_colmats(folder):
    """-> (names, per-triangle index, info) or None."""
    p = os.path.join(folder, "colmats.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        j = json.load(f)
    return j["names"], np.load(os.path.join(folder, "colmat_idx.npy")), j.get("info", {})


def collision_material_table(archive):
    """A map's CollisionMaterials: {name: (layer, sound type)}."""
    S = mapdata.schema()
    C = mapdata.Codec(S)
    out = {}
    for _rec, name, t, body, _layer in mapdata.iter_objects_ex(archive):
        if t not in S.types_by_hash or S.name_of(t) != "CollisionMaterial" or name in out:
            continue
        try:
            o = C.decode(body).obj
        except Exception:  # noqa: BLE001
            continue
        cf = o.fields.get("CollisionFilterInfo")
        layer = struct.unpack("<I", cf.fields["Layer"])[0] if cf is not None and "Layer" in cf.fields else None
        out[name] = (layer, o.fields.get("SoundType", b"").hex())
    return out


def resolve_collision_materials(scene, archive, log=print):
    """The scene's parts' source collision-material names -> the target map's: the same name, else one with the same
    layer and sound type, else the same layer; what doesn't resolve keeps the template's (index 0) and is reported.
    -> report lines."""
    table = collision_material_table(archive)
    by_ls, by_l = {}, {}
    for n, (layer, sound) in sorted(table.items()):
        by_ls.setdefault((layer, sound), n)
        by_l.setdefault(layer, n)
    mapped, missing, tris = {}, {}, 0
    for p in scene["parts"].values():
        names = p.get("collision_materials")
        if not names:
            continue
        info = p.pop("_colmat_info", {})
        out = []
        for n in names:
            if n in table or not n:
                out.append(n or None)
                continue
            layer, sound = (info.get(n) or [None, None])[:2]
            alt = by_ls.get((layer, sound)) or by_l.get(layer)
            if alt:
                mapped[n] = alt
            else:
                missing[n] = missing.get(n, 0) + 1
            out.append(alt)
        p["collision_materials"] = out
        tris += len(p.get("collision_material_index", []))
    lines = [f"collision materials: {tris} triangle(s) carry their own map's"]
    if mapped:
        lines.append("collision materials mapped by layer: " + ", ".join(f"{a} -> {b}" for a, b in mapped.items()))
    if missing:
        lines.append("collision materials this map hasn't (their triangles take the template's): " + ", ".join(missing))
    for line in lines:
        log(line)
    return lines, missing


def save_edges(folder, edges):
    E = list(edges or [])
    np.savez_compressed(os.path.join(folder, "climb.npz"),
                        a=np.array([e[0] for e in E], np.float64).reshape(-1, 3),
                        b=np.array([e[1] for e in E], np.float64).reshape(-1, 3),
                        n0=np.array([e[2] for e in E], np.float64).reshape(-1, 3),
                        n1=np.array([e[3] for e in E], np.float64).reshape(-1, 3),
                        sub=np.array([e[4] if len(e) > 4 else 1 for e in E], np.int64))


def load_edges(folder):
    """An asset's own climb edges ([] when it has none, or isn't a model)."""
    p = os.path.join(folder, "climb.npz")
    if not os.path.exists(p):
        return []
    z = np.load(p)
    return [(z["a"][i], z["b"][i], z["n0"][i], z["n1"][i], int(z["sub"][i])) for i in range(len(z["sub"]))]


def extract(archive, mesh_hexes, log=print):
    """Models of one map -> asset folders (see the module docstring). -> [folder]."""
    import splib
    if splib.is_sp(archive):                    # SP models (splib.py)
        return splib.extract(archive, mesh_hexes, lambda h: asset_dir(archive, h), ASSET_VERSION, log=log)
    from ac3editor import sceneport
    md = mapdata.load(archive, log=log)
    reps = {}
    for e in _cell_objects(md):
        reps.setdefault(e.mesh, e)
    by_uid = {e.uid: e for e in md.entities}

    def rep_for(h):
        m, _sep, ent = h.partition("#")
        e = by_uid.get(bytes.fromhex(ent)) if ent else None
        return e if e is not None and e.mesh == bytes.fromhex(m) else reps.get(bytes.fromhex(m))
    todo = [h for h in mesh_hexes if not os.path.exists(os.path.join(asset_dir(archive, h), "asset.json"))]
    native = native_climb(md, {h: rep_for(h) for h in todo if rep_for(h) is not None and rep_for(h).mesh in md.meshes},
                          archive, log=log)
    out = []
    for h in mesh_hexes:
        mid = bytes.fromhex(h.partition("#")[0])
        folder = asset_dir(archive, h)
        if os.path.exists(os.path.join(folder, "asset.json")):
            out.append(folder)
            continue
        if mid not in md.meshes:
            raise ValueError(f"{os.path.basename(archive)} has no model {h}")
        name, v, t = md.meshes[mid]
        uv, prim, mats = md.mesh_extra.get(mid, (None, np.zeros(len(t), np.int32), []))
        e = rep_for(h)
        lo, hi = v.min(0).astype(np.float64), v.max(0).astype(np.float64)
        col = _solid_shapes(md, e, None if "#" in h else (lo, hi)) if e is not None else None
        merged = False
        walk = map_walk(archive)
        col_names = shape_tri_materials(md, e.shapes, walk) if col is not None else None
        if col is None and e is not None:
            col = merged_collision(md, e, walk)
            merged = col is not None
            col_names = shape_tri_materials(md, walk["members"].get(e.uid, []), walk) if merged else None
        # a building part read as ITS object, with no collision of its own: none, as in its own map - its collision
        # is elsewhere, e.g. the barracks' wall pieces are covered by piece /14's FuzzyZone shell (their visible
        # triangles as collision, on the template's climbable material, would make the whole interior climbable)
        nocol = col is None and "#" in h
        tmp = folder + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        used = sorted(set(int(p) for p in prim))
        keys, mat_specs, mat_info = [], {}, []
        for k in used:
            m = mats[k] if k < len(mats) else None
            mname, path = md.materials.get(m, ("untextured", None))
            key = m.hex() if m else f"none{k}"
            keys.append(key)
            if key in mat_specs:
                continue
            if path and os.path.exists(path):
                fn = f"{key}.dds"
                shutil.copyfile(path, os.path.join(tmp, fn))
                img = fn
            else:
                fn = f"{key}.png"
                from PIL import Image
                Image.fromarray(np.full((8, 8, 4), (128, 128, 124, 255), np.uint8)).save(os.path.join(tmp, fn))
                img = fn
            mat_specs[key] = dict(image=os.path.join(tmp, img), name=mname.strip())
            mat_info.append(dict(key=key, name=mname.strip(), texture=img, textured=img.endswith(".dds")))
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name)[:36]
        part = dict(name=f"ED_Lib_{safe}", verts=v.astype(np.float64), tris=t.astype(np.int64),
                    uv=None if uv is None else uv.astype(np.float64),
                    tri_mat=np.searchsorted(used, prim).astype(np.int64), materials=keys,
                    collision="none" if nocol else col, climb=True)
        sceneport.save(tmp, dict(parts={"asset": part}, instances=[], materials=mat_specs))
        if col is not None and col_names is not None and len(col_names) == len(col[1]):
            save_colmats(tmp, col_names, walk)
        edges = native.get(h, [])
        save_edges(tmp, edges)
        meta = dict(version=ASSET_VERSION, archive=os.path.abspath(archive), mesh=h.partition("#")[0], name=name, native_edges=len(edges),
                    nv=int(len(v)), nt=int(len(t)), lo=lo.tolist(), hi=hi.tolist(),
                    collision="merged" if merged else "own" if col is not None else "none" if nocol else "visible",
                    collision_tris=int(len(col[1])) if col is not None else int(len(t)),
                    rep=e.uid.hex() if e is not None else None, materials=mat_info)
        with open(os.path.join(tmp, "asset.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(os.path.dirname(folder), exist_ok=True)
        os.replace(tmp, folder)
        log(f"asset: {name} from {os.path.basename(archive)} ({len(t)} triangles, {len(mat_info)} material(s), "
            f"collision {meta['collision']}, {len(edges)} climb edge(s) of its own)")
        out.append(folder)
    return out


PRIM_KINDS = {"wall": "Wall", "box": "Box", "slab": "Floor slab"}


def is_prim(key):
    return key.startswith("prim:")


def prim_spec(key):
    """A primitive's key ("prim:" + JSON) -> its spec: {"kind": wall | box | slab, "w": x size, "d": y size, "h": z
    size (m), "m": the library model whose material textures it, "mk": that material's key, "mn": its name}."""
    return json.loads(key[5:])


def prim_dir(key):
    return os.path.join(ASSETS, "prim", hashlib.sha1(key.encode()).hexdigest()[:20])


def box(w, d, h, tile=2.0):
    """A box w (x) by d (y) by h (z) standing on the origin (its bottom middle at 0): 24 vertices (a face each), 12
    triangles wound outward, UVs a repeat every `tile` m on every face. -> (verts, tris, uv)."""
    x, y = w / 2, d / 2
    faces = [  # 4 corners counter-clockwise seen from outside, the u and v axes
        ([(-x, -y, 0), (-x, y, 0), (x, y, 0), (x, -y, 0)], 0, 1),          # bottom (-z)
        ([(-x, -y, h), (x, -y, h), (x, y, h), (-x, y, h)], 0, 1),          # top
        ([(-x, -y, 0), (x, -y, 0), (x, -y, h), (-x, -y, h)], 0, 2),        # -y
        ([(x, y, 0), (-x, y, 0), (-x, y, h), (x, y, h)], 0, 2),            # +y
        ([(x, -y, 0), (x, y, 0), (x, y, h), (x, -y, h)], 1, 2),            # +x
        ([(-x, y, 0), (-x, -y, 0), (-x, -y, h), (-x, y, h)], 1, 2),        # -x
    ]
    verts, tris, uv = [], [], []
    for corners, ua, va in faces:
        b = len(verts)
        for c in corners:
            verts.append(c)
            uv.append((c[ua] / tile, -c[va] / tile))
        tris += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
    return np.array(verts, np.float64), np.array(tris, np.int64), np.array(uv, np.float64)


def make_prim(key, log=print):
    """A primitive's asset folder (extract's form): the box, the picture of its material copied from the model it
    comes from; collision = the box (its visible triangles), climb edges on its top edges."""
    from ac3editor import sceneport
    folder = prim_dir(key)
    if os.path.exists(os.path.join(folder, "asset.json")):
        return folder
    sp = prim_spec(key)
    src = ensure([sp["m"]], log=log)[sp["m"]]
    src_mats = sceneport.load(src)["materials"]
    if sp["mk"] not in src_mats:
        raise ValueError(f"{sp['m']} has no material {sp['mk']}")
    w, d, h = (max(0.05, float(sp[k])) for k in ("w", "d", "h"))
    v, t, uv = box(w, d, h)
    tmp = folder + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    img = src_mats[sp["mk"]]["image"]
    fn = "material" + os.path.splitext(img)[1]
    shutil.copyfile(img, os.path.join(tmp, fn))
    kind = PRIM_KINDS.get(sp.get("kind"), "Box")
    name = f"{kind} {w:g} x {d:g} x {h:g} m"
    part = dict(name="ED_Prim_" + kind.replace(" ", ""), verts=v, tris=t, uv=uv,
                tri_mat=np.zeros(len(t), np.int64), materials=["m"], collision=None, climb=True)
    sceneport.save(tmp, dict(parts={"asset": part}, instances=[],
                             materials={"m": dict(image=os.path.join(tmp, fn), name=sp.get("mn", "material"))}))
    meta = dict(version=ASSET_VERSION, archive="", primitive=sp, mesh=hashlib.sha1(key.encode()).hexdigest()[:8],
                name=name, nv=len(v), nt=len(t), lo=v.min(0).tolist(), hi=v.max(0).tolist(), collision="visible",
                collision_tris=len(t), rep=None,
                materials=[dict(key="m", name=sp.get("mn", "material"), texture=fn, textured=fn.endswith(".dds"))])
    with open(os.path.join(tmp, "asset.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(os.path.dirname(folder), exist_ok=True)
    os.replace(tmp, folder)
    log(f"primitive: {name} in {sp.get('mn', 'its material')}")
    return folder


def is_ground(key):
    return key.startswith("ground:")


def ground_spec_of(key):
    return json.loads(key[7:])


def ground_dir(key):
    return os.path.join(ASSETS, f"ground_a{ASSET_VERSION}", hashlib.sha1(key.encode()).hexdigest()[:20])


def make_ground(key, log=print):
    """A ground key's asset folder (run in a child process: it reads the whole source map). terrain.cut on a scratch
    copy gives the visible triangles (with UVs, material, vertex colours) and collision triangles of every ground
    object under the footprint, cut to it; the scratch copy is deleted."""
    from ac3editor import sceneport, terrain
    folder = ground_dir(key)
    if os.path.exists(os.path.join(folder, "asset.json")):
        return folder
    sp = ground_spec_of(key)
    archive = sp["a"]
    fp = np.asarray(sp["fp"], np.float64)
    ex = {bytes.fromhex(h) for h in sp.get("ex", [])}
    os.makedirs(os.path.dirname(folder), exist_ok=True)
    scratch = folder + ".cut.forge"
    try:
        _same, vis, col = terrain.cut(archive, scratch, [fp], exclude=ex, log=log)
    finally:
        if os.path.exists(scratch):
            os.remove(scratch)
    md = mapdata.load(archive, log=log)
    # collision-only objects kept in the cells' merged collision (no model: terrain.cut doesn't see them - Fort Wolcott's
    # Ground_FlagTower walkway at 4.2 m by the barracks): their triangles whose middle is under the footprint
    walk = map_walk(archive)
    extra = []
    for uid, sids in walk["members"].items():
        name, has_vis, M4 = walk["info"].get(uid, ("", True, None))
        if has_vis or M4 is None or uid in ex:
            continue
        for sid in sids:
            if sid not in md.shapes or md.shape_layer.get(sid, SOLID_LAYER) != SOLID_LAYER:
                continue
            _nm, v, t = md.shapes[sid]
            W = (v.astype(np.float64) @ M4[:3, :3] + M4[3, :3])[t.astype(np.int64)]
            inside = terrain.inside_poly(W.mean(1)[:, :2], fp)
            if inside.any():
                extra.append(W[inside])
                log(f"ground: {int(inside.sum())} collision triangle(s) of {name} (collision only, merged)")
    if extra:
        col = np.concatenate([np.asarray(col, np.float64).reshape(-1, 3, 3)] + extra)
    vis = [v for v in vis if v[2] is not None and len(v[0])]
    mats = sorted({v[2] for v in vis})
    tmp = folder + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    mat_specs, mat_info = {}, []
    for m in mats:
        mname, path = md.materials.get(m, ("untextured", None))
        if path and os.path.exists(path):
            fn = f"{m.hex()}.dds"
            shutil.copyfile(path, os.path.join(tmp, fn))
        else:
            fn = f"{m.hex()}.png"
            from PIL import Image
            Image.fromarray(np.full((8, 8, 4), (128, 128, 124, 255), np.uint8)).save(os.path.join(tmp, fn))
        mat_specs[m.hex()] = dict(image=os.path.join(tmp, fn), name=mname.strip())
        mat_info.append(dict(key=m.hex(), name=mname.strip(), texture=fn, textured=fn.endswith(".dds")))
    if vis:
        tris = np.concatenate([np.asarray(v[0], np.float64).reshape(-1, 3, 3) for v in vis])
        uvs = np.concatenate([np.asarray(v[1], np.float64).reshape(-1, 3, 2) for v in vis])
        cols = np.concatenate([np.asarray(v[3], np.uint8).reshape(-1, 3, 4) if v[3] is not None
                               else np.full((len(v[0]), 3, 4), 255, np.uint8) for v in vis])
        tmat = np.concatenate([np.full(len(v[0]), mats.index(v[2]), np.int64) for v in vis])
    else:
        tris, uvs, cols, tmat = np.zeros((0, 3, 3)), np.zeros((0, 3, 2)), np.zeros((0, 3, 4), np.uint8), np.zeros(0, np.int64)
    col = np.asarray(col, np.float64).reshape(-1, 3, 3)
    np.savez_compressed(os.path.join(tmp, "ground.npz"), tris=tris, uvs=uvs, cols=cols, tmat=tmat, col=col,
                        mats=np.array([m.hex() for m in mats]))
    verts = tris.reshape(-1, 3)
    part = dict(name="ED_LibGround", verts=verts, tris=np.arange(len(verts)).reshape(-1, 3), uv=uvs.reshape(-1, 2),
                tri_mat=tmat, materials=[m.hex() for m in mats],
                collision=(col.reshape(-1, 3), np.arange(len(col) * 3).reshape(-1, 3)) if len(col) else "none",
                climb=False)
    sceneport.save(tmp, dict(parts={"asset": part}, instances=[], materials=mat_specs))
    lo = verts.min(0).tolist() if len(verts) else [0, 0, 0]
    hi = verts.max(0).tolist() if len(verts) else [0, 0, 0]
    meta = dict(version=ASSET_VERSION, archive=os.path.abspath(archive), ground=sp, mesh=hashlib.sha1(key.encode()).hexdigest()[:8],
                name=f"Ground under the footprint ({len(tris)} triangles)", nv=len(verts), nt=len(tris), lo=lo, hi=hi,
                collision="own", collision_tris=len(col), rep=None, materials=mat_info)
    with open(os.path.join(tmp, "asset.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    shutil.rmtree(folder, ignore_errors=True)
    os.replace(tmp, folder)
    log(f"ground: {len(tris)} visible / {len(col)} collision triangles under the footprint, {len(mats)} material(s)")
    return folder


def ground_models(key, matrix, name, solid=True):
    """A ground part placed on ITS OWN map -> newmodel specs (the carry's form: stock materials and vertex colours,
    the ground's collision as a model of its own). matrix: source world -> this place."""
    from ac3editor import terrain
    z = np.load(os.path.join(ensure([key])[key], "ground.npz"))
    mats = [bytes.fromhex(str(m)) for m in z["mats"]]
    vis = [(z["tris"][z["tmat"] == k], z["uvs"][z["tmat"] == k], m, z["cols"][z["tmat"] == k]) for k, m in enumerate(mats)]
    return terrain.chunk_specs(vis, z["col"] if solid else np.zeros((0, 3, 3)), np.asarray(matrix, np.float64).reshape(4, 4),
                               name=name)


def ensure(keys, log=print):
    """Asset folders for these keys: models extracted when missing (a child process per source map), primitives made
    here. -> {key: folder}."""
    by_map, out, prims, grounds = {}, {}, [], []
    for key in keys:
        if is_prim(key):
            prims.append(key)
            continue
        if is_ground(key):
            grounds.append(key)
            continue
        if key.startswith("gp:"):             # a stock gameplay object: drawn from its members' models
            import gameplayview
            out.update(ensure(gameplayview.member_keys(key), log=log))
            out[key] = os.path.join(ASSETS, "gp", hashlib.sha1(key.encode()).hexdigest()[:16])
            continue
        if key.startswith("blend:"):          # a blend group: its marker disc, read as that entity's model
            import blendview
            archive, h = split_key(blendview.model_key(key))
        else:
            archive, h = split_key(key)
        folder = asset_dir(archive, h)
        out[key] = folder
        if not os.path.exists(os.path.join(folder, "asset.json")):
            by_map.setdefault(archive, []).append(h)
    for archive, hs in by_map.items():
        log(f"library: reading {len(hs)} model(s) from {os.path.basename(archive)}...")
        code = _child(["extract", archive] + hs, log=log)
        missing = [h for h in hs if not os.path.exists(os.path.join(asset_dir(archive, h), "asset.json"))]
        if code or missing:
            raise RuntimeError(f"couldn't read {len(missing)} model(s) from {os.path.basename(archive)}")
    for key in prims:
        out[key] = make_prim(key, log=log)
    for key in grounds:
        folder = ground_dir(key)
        if not os.path.exists(os.path.join(folder, "asset.json")):
            os.makedirs(os.path.dirname(folder), exist_ok=True)
            arg = folder + ".key.json"
            with open(arg, "w", encoding="utf-8") as f:
                f.write(key)
            log(f"library: cutting the ground under a footprint from {os.path.basename(ground_spec_of(key)['a'])}...")
            code = _child(["ground", arg], log=log)
            os.remove(arg)
            if code or not os.path.exists(os.path.join(folder, "asset.json")):
                raise RuntimeError("couldn't cut the ground under the footprint (see the log)")
        out[key] = folder
    return out


def meta_of(folder):
    with open(os.path.join(folder, "asset.json"), encoding="utf-8") as f:
        return json.load(f)


# -------------------------------------------------------------------------------------------- the app's view --

def asset_scene(keys, log=print):
    """The assets as one scene cache file (scene_cache.py's format): mesh k, shape k (its collision) and entity k
    (at the origin) = keys[k]. -> (path, [meta])."""
    from ac3editor import sceneport
    import scene_cache as SC
    folders = ensure(keys, log=log)
    h = hashlib.sha1("\n".join(os.path.relpath(folders[k], ASSETS) for k in keys).encode()).hexdigest()[:16]
    out = os.path.join(ASSETS, f"scene_{h}_s{SC.VERSION}_{ASSET_SCENE_VERSION}.ac3s")
    gp = {}                                     # (stock gameplay objects, put together from their members)
    for k in keys:
        if k.startswith("gp:"):
            import gameplayview
            gp[k] = gameplayview.scene_of(k, folders)
    metas = [gp[k][1] if k in gp else meta_of(folders[k]) for k in keys]
    import climbview                               # (each model's own climb edges and no-climb triangles)
    metas = [m if k in gp else dict(m, climb=climbview.asset_climb(folders[k])) for m, k in zip(metas, keys)]
    for k, key in enumerate(keys):
        if key.startswith("blend:"):           # a blend group: its NPC slots etc. for the app to draw
            import blendview
            th = blendview.template_of(key)
            metas[k] = dict(metas[k], blend=dict(label=blendview.label(th.name), name=th.name, map=th.map,
                                                 slots=[list(s) for s in th.slots]))
    if os.path.exists(out):
        return out, metas
    textures, materials, meshes, shapes = [], [], [], []
    for k, key in enumerate(keys):
        sc = gp[key][0] if key in gp else sceneport.load(folders[key])
        p = sc["parts"]["asset"]
        mat_base = len(materials)
        for mk in p["materials"]:
            spec = sc["materials"][mk]
            t = -1
            if spec["image"].lower().endswith(".dds"):
                t = len(textures)
                textures.append(spec["image"])
            materials.append((k * 1000 + len(materials) - mat_base, spec.get("name", mk), t))
        v = np.asarray(p["verts"], np.float32)
        tr = np.asarray(p["tris"], np.uint32)
        uv = np.zeros((len(v), 2), np.float32) if p.get("uv") is None else np.asarray(p["uv"], np.float32)
        tm = np.asarray(p["tri_mat"], np.int64)
        order = np.argsort(tm, kind="stable")
        tr, tm = tr[order], tm[order]
        subs = [(mat_base + int(m), int(np.nonzero(tm == m)[0][0]), int((tm == m).sum())) for m in np.unique(tm)]
        u = int.from_bytes(bytes.fromhex(metas[k]["mesh"]), "little")
        meshes.append(((u ^ (u >> 32)) & 0xFFFFFFFF, metas[k]["name"], v, uv, tr, subs))   # (SP uids are 64-bit)
        if isinstance(p.get("collision"), tuple):
            cv, ct = p["collision"]
        else:
            cv, ct = v, tr
        shapes.append((k, metas[k]["name"], np.asarray(cv, np.float32), np.asarray(ct, np.uint32)))
    os.makedirs(ASSETS, exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        w = SC._W(f)
        f.write(b"AC3S")
        w.u32(SC.VERSION)
        w.u32(len(textures))
        for p in textures:
            w.str(p)
        w.u32(len(materials))
        for uid, name, t in materials:
            w.u32(uid)
            w.str(name)
            w.i32(t)
        w.u32(len(meshes))
        for uid, name, v, uv, tr, subs in meshes:
            w.u32(uid)
            w.str(name)
            w.u32(len(v), len(tr), len(subs))
            w.arr(v, "<f4")
            w.arr(uv, "<f4")
            w.arr(tr, "<u4")
            for m, first, n in subs:
                w.i32(m)
                w.u32(first, n)
        w.u32(len(shapes))
        for uid, name, v, t in shapes:
            w.u32(uid)
            w.str(name)
            w.u32(SOLID_LAYER, len(v), len(t))
            w.arr(v, "<f4")
            w.arr(t, "<u4")
        w.u32(1)
        w.str("Library")
        w.str("")
        w.u32(len(keys))
        for k, meta in enumerate(metas):
            w.u32(k)
            w.str(meta["name"])
            w.arr(np.eye(4, dtype=np.float32), "<f4")
            w.i32(k)
            w.u32(1)
            w.i32(k)
            w.i32(0, -1)
            w.str("")
            w.str("")
            w.u32(0)
            w.str("")
    os.replace(tmp, out)
    log(f"asset scene: {len(keys)} model(s), {len(textures)} texture(s) -> {os.path.basename(out)}")
    return out, metas


# ------------------------------------------------------------------------------------------------ the build --

CLIMB_MODES = ("native", "generated", "none", "outside")   # outside: its own map's + plan's outer-wall layer


def climb_of(place):
    """A placement's climbing: its "climb", else the default of its kind (models: their own map's; primitives:
    generated; ground: none)."""
    c = place.get("climb")
    if c in CLIMB_MODES:
        return c
    key = place["key"]
    if not is_prim(key) and not is_ground(key) and "|" in key and _is_sp_key(key):
        return "generated"                      # (SP climbs sill by sill - MP models get handhold rows)
    return "generated" if is_prim(key) else "none" if is_ground(key) else "native"


def _is_sp_key(key):
    import splib
    return splib.is_sp(split_key(key)[0])


def scene_for(places, log=print, stats=None):
    """Placed library models -> a sceneport scene (one part per asset, solidity and climbing, one instance per
    placement). Instance names: ED_Lib_<model>_<n> (the read-back finds them by name). Climbing: "native" - the
    model's own climb edges (part["edges"], no generated ledges or rows); "generated" - climb=True (ledges + rows);
    "none"; "outside" - as "native" here (plan.outside_specs adds the outer walls' invisible layer). Ground parts
    (verts in their map's world) are moved to a frame at their middle."""
    from ac3editor import sceneport
    folders = ensure([p["key"] for p in places], log=log)
    parts, instances, mats = {}, [], {}
    native_edges = 0
    for n, pl in enumerate(places):
        key = pl["key"]
        solid, mode = pl.get("solid", True), climb_of(pl)
        if mode == "outside":           # (the model itself as in its own map; plan adds the outer walls' layer)
            mode = "native"
        pk = f"{key}|{int(solid)}|{mode}"
        shift = np.zeros(3)
        if pk not in parts:
            sc = sceneport.load(folders[key])
            p = dict(sc["parts"]["asset"])
            tag = hashlib.sha1(key.encode()).hexdigest()[:8]
            renamed = []
            for mk in p["materials"]:
                nk = f"{tag}_{mk}"
                mats[nk] = sc["materials"][mk]
                renamed.append(nk)
            p["materials"] = renamed
            if not solid:
                p["collision"] = "none"
            cm = load_colmats(folders[key]) if solid else None
            if cm is not None:
                p["collision_materials"], p["collision_material_index"], p["_colmat_info"] = list(cm[0]), cm[1], cm[2]
            if mode == "generated" or (mode == "native" and is_prim(key)):     # (a primitive's own = generated)
                p["climb"] = True
            elif mode == "native" and not is_prim(key) and not is_ground(key):
                p["climb"] = False
                p["edges"] = load_edges(folders[key])
            else:
                p["climb"] = False
                p["edges"] = [] if mode == "native" else None
            if is_ground(key) and len(p["verts"]):
                c0 = np.asarray(p["verts"], np.float64).mean(0)
                p["verts"] = np.asarray(p["verts"], np.float64) - c0
                if isinstance(p.get("collision"), tuple):
                    p["collision"] = (np.asarray(p["collision"][0], np.float64) - c0, p["collision"][1])
                p["_shift"] = c0
            parts[pk] = p
        p = parts[pk]
        if p.get("edges"):
            native_edges += len(p["edges"])
        m = np.asarray(pl["matrix"], np.float64).reshape(4, 4)     # (a scaled map object: sceneport bakes the scale)
        if p.get("_shift") is not None:
            t = np.eye(4)
            t[3, :3] = p["_shift"]
            m = t @ m
        instances.append(dict(part=pk, name=place_name(p["name"], n), matrix=m))
    for p in parts.values():
        p.pop("_shift", None)
    if stats is not None:
        stats["native_edges"] = native_edges
    return dict(parts=parts, instances=instances, materials=mats)


def place_name(part_name, n):
    return f"{part_name[:40]}_{n:03d}"


# ------------------------------------------------------------------------------------------------- the CLI --

def main(argv):
    if len(argv) >= 2 and argv[0] == "index":
        build_index(argv[1])
        return 0
    if len(argv) >= 3 and argv[0] == "extract":
        extract(argv[1], argv[2:])
        return 0
    if len(argv) >= 2 and argv[0] == "ground":
        with open(argv[1], encoding="utf-8") as f:
            make_ground(f.read())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
