"""Reading an AC3 map archive into plain data a viewer can draw. Read-only; no Blender needed.

    python -m ac3editor.mapdata <archive.forge> [--fresh]        # summary

Objects come from forgelib's map reader (forge.Forge -> maps/mapwalk: prefix, decompress, a directory whose
entries may name a data-layer filter - the World record, every Cell DataBlock and every game-mode DataBlock use one,
and plain objects.walk rejects exactly those, the map's heaviest records) and are decoded by
the Studio's own FastLoad codec (ac3codec) with the schema read from the game's AC3MP.exe (schema()). What is read:

* Entity / EntityGroup (and the entities a group inlines): `GlobalMatrix` (16 floats, translation in 12..14,
  p_world = p . M), the Visual component's `Object` (a Mesh, or a LODSelector whose `LODDescs[0].Object` is the
  LOD0 mesh), the InertComponent's `RigidBody.Shape` (collision), and the component class names.
* Mesh: `CompiledMesh.MeshData` - vertex buffer (position = int16 x, y, z, s at the vertex start; metres =
  xyz * |s| / 8 / 32767, checked against entity bounding boxes on Fort Wolcott), 16/32-bit index buffer, and
  `StandardPrimitives` (triangle lists; indices are absolute into the vertex buffer, checked on every Galata mesh).
  Format 0 (skinned) carries a bone value in s; its unit is taken as 10 / 32767 (tentative).
* MeshShape: `Vertices` (float x, y, z), `IndicesTriangles` (u16), in the owning entity's frame.

Materials: a Mesh's `CompiledMeshMaterials` hold one Material per StandardPrimitive (triangle list); a Material's
`TextureSet.Maps` (11 slots) name its TextureMaps, the colour one named `..._DiffuseMap` (else the first slot). UVs:
int16 u, v / 2048 at vertex +20 (formats 0 and 3) or +16 (format 4). Each colour TextureMap in the archive is written
once as a DDS file (<workspace>/cache/textures/: its mip chain behind a DDS header).

Meshes and shapes that live in another archive (DataPC.forge) are not resolved yet: their entities come in as
markers. The result is cached in <workspace>/cache/ (keyed by the archive's name, size and time).
"""
from .workspace import WORK
import os
import pickle
import struct
import sys
import time
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PROJECTS = os.path.dirname(REPO)
# the forge library: AC3_FORGE, else the release tree's own copy (forgelib, beside this package), else a forge
# checkout beside this repo (a development setup)
FORGE_REPO = os.environ.get("AC3_FORGE") or next(
    (p for p in (os.path.join(REPO, "forgelib"),) if os.path.exists(os.path.join(p, "forge.py"))),
    os.path.join(PROJECTS, "ac3-forge"))
for _p in (REPO, FORGE_REPO, os.path.join(FORGE_REPO, "maps")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import mapwalk  # noqa: E402  (forgelib maps/)
from datafile import decompress  # noqa: E402  (forgelib)
from forge import Forge  # noqa: E402  (forgelib)
from ac3codec import Codec, DecodeError, Obj, Ptr, Ref, Schema  # noqa: E402
from ac3codec import exe_schema, schema as _schema_model  # noqa: E402
import paths  # noqa: E402  (forgelib's, or a development setup's)
from . import spcodec  # noqa: E402
sys.modules.setdefault("codec27", spcodec)   # forgelib's spmaps readers decode with ac3codec too (spcodec.py)

CACHE = os.path.join(WORK, "cache")
CACHE_VERSION = 7                                         # (7: read with ac3codec + the exe schema)
UID_MARK = b"\x00\x01"  # a uid-bearing body starts 00 01 uid(4) type(4)
OBJECT_HEADER = 12  # u32 type, u32 body size, u32 name length


@dataclass
class Entity:
    name: str
    uid: bytes
    matrix: tuple            # 16 floats, AC3 order (translation at 12..14)
    mesh: bytes | None       # uid of the LOD0 Mesh, when the Visual names one in this archive
    visual: bytes | None     # what the Visual names (Mesh or LODSelector), resolved or not
    shapes: list             # uids of collision shapes (InertComponent.RigidBody.Shape)
    components: list         # component class names
    group: str | None = None
    record: str = ""         # the archive record it came from (a Cell, the World, a game-mode DataBlock)
    layer: int | None = None  # data-layer filter index (per-mode objects), None = always there


@dataclass
class MapData:
    archive: str
    entities: list = field(default_factory=list)
    meshes: dict = field(default_factory=dict)    # uid -> (name, verts float32 (n, 3), tris uint32 (m, 3))
    shapes: dict = field(default_factory=dict)    # uid -> (name, verts float32 (n, 3), tris uint32 (m, 3))
    skipped: dict = field(default_factory=dict)   # reason -> count
    shape_layer: dict = field(default_factory=dict)  # shape uid -> CollisionFilterInfo.Layer (1 = ordinary solid)
    mesh_extra: dict = field(default_factory=dict)  # mesh uid -> (uv float32 (n, 2), primitive per triangle, materials)
    materials: dict = field(default_factory=dict)   # material uid -> (name, DDS path of its colour texture or None)


_SCHEMA = None
SCHEMA_VERSION = 1


def schema_exe():
    """The exe the object schema is read from: paths.DEFAULT_EXE (the configured base set's AC3MP.exe, else the
    game's)."""
    return paths.DEFAULT_EXE


def schema():
    """The object schema (ac3codec.Schema), read from the game's own AC3MP.exe (ac3codec/exe_schema.py) - the Studio
    ships none. Field names: the exe's own strings plus every identifier in the Studio's code (so each name the code
    asks for resolves); unnamed fields read "0x%08X". Cached per exe (size + time) in <workspace>/cache/schema."""
    global _SCHEMA
    if _SCHEMA is None:
        exe = schema_exe()
        st = os.stat(exe)
        from . import games
        stem = os.path.splitext(games.target().exe_name)[0]
        cpath = os.path.join(CACHE, "schema", f"{stem}_{st.st_size}_{int(st.st_mtime)}_v{SCHEMA_VERSION}.json")
        if os.path.exists(cpath):
            _SCHEMA = _schema_model.load_json(cpath)
        else:
            roots = [os.path.join(REPO, *d) for d in (("ac3editor",), ("studio", "engine"), ("studio", "src"))]
            mods = os.path.join(REPO, "release", "forge_modules.json")      # (the forge modules the Studio uses)
            if os.path.exists(os.path.join(REPO, "forgelib")):
                roots.append(os.path.join(REPO, "forgelib"))
            elif os.path.exists(mods):
                import json
                with open(mods, encoding="utf-8") as fh:
                    roots += [os.path.join(FORGE_REPO, m) for m in json.load(fh)["modules"]]
            roots = [r for r in roots if os.path.exists(r)]
            model = exe_schema.read_exe(exe, extra_names=exe_schema.source_names(*roots, exts=(".py", ".cpp", ".h", ".inc")))
            os.makedirs(os.path.dirname(cpath), exist_ok=True)
            # engine processes can read the schema at the same time on a new workspace: each writes its own temporary
            # file, and one that finds the cache already written (or held open by another process) keeps that one
            part = f"{cpath}.{os.getpid()}.part"
            _schema_model.save_json(model, part)
            try:
                os.replace(part, cpath)
            except OSError:
                if not os.path.exists(cpath):
                    raise
                os.remove(part)
            _SCHEMA = Schema(model)
    return _SCHEMA


def iter_objects(archive, records=None):
    """-> (name, class hash, body) for every object of every record; `records` (a list) collects
    (record name, error or None) per record."""
    for rec_name, name, type_id, body, _filt in iter_objects_ex(archive, records):
        yield name, type_id, body


def iter_objects_ex(archive, records=None):
    """-> (record name, object name, class hash, body, data-layer filter or None)."""
    with Forge(archive) as f:
        for rec in f.records:
            if rec.name == "GlobalMetaFile":
                continue
            try:
                _prefix, payload = mapwalk.split_prefix(f.read(rec))
                d = decompress(payload)
                walked, _table = mapwalk.walk(d)
            except Exception as e:  # noqa: BLE001 - counted, not fatal
                if records is not None:
                    records.append((rec.name, f"{type(e).__name__}: {e}"))
                continue
            if records is not None:
                records.append((rec.name, None))
            for off, size, _serial, filt in walked:
                type_id, _bs, name_len = struct.unpack_from("<III", d, off)
                name = d[off + OBJECT_HEADER:off + OBJECT_HEADER + name_len].decode("latin-1")
                yield (rec.name, name, type_id, d[off + OBJECT_HEADER + name_len:off + size],
                       None if filt == mapwalk.NO_FILTER else filt)


def _i32(b):
    return struct.unpack("<i", b)[0]


def _obj(v):
    if isinstance(v, (Ptr, Ref)):
        return v.obj
    return v if isinstance(v, Obj) else None


def _link(v):
    if isinstance(v, Ref):
        return v.id
    if isinstance(v, Ptr):
        return v.link or (v.obj.id if v.obj else None)
    return None


UV_AT = {0: 20, 3: 20, 4: 16}
DDS_FOURCC = {2: b"DXT1", 3: b"DXT1", 4: b"DXT3", 5: b"DXT5"}


def dds_bytes(body):
    """A TextureMap body (forgelib's dxt.py layout) -> a DDS file, or None for formats DDS can't hold here."""
    w, h = struct.unpack_from("<II", body, 10)
    fmt = struct.unpack_from("<I", body, 22)[0]
    mips = struct.unpack_from("<I", body, 34)[0]
    size = struct.unpack_from("<I", body, 86)[0]
    if fmt not in DDS_FOURCC or not w or not h or 90 + size > len(body):
        return None
    block = 8 if DDS_FOURCC[fmt] == b"DXT1" else 16
    linear = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block
    hdr = struct.pack("<4sIIIIIII44s", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000, h, w, linear, 0,
                      max(mips, 1), bytes(44))
    pf = struct.pack("<II4sIIIII", 32, 0x4, DDS_FOURCC[fmt], 0, 0, 0, 0, 0)
    caps = struct.pack("<IIIII", 0x1000 | (0x400008 if mips > 1 else 0), 0, 0, 0, 0)
    return hdr + pf + caps + body[90:90 + size]


def decode_mesh(fields, extra=None):
    """Mesh fields -> (verts float32 (n, 3), tris uint32 (m, 3)), or a skip reason (str). `extra` (a list) receives
    (uv (n, 2), primitive index per kept triangle)."""
    md = _obj(fields["CompiledMesh"]).fields["MeshData"].fields
    fmt, stride = md["VertexFormat"][0], md["VertexStride"][0]
    vb = b"".join(md["VertexBufferData"])
    n = len(vb) // stride
    if not n:
        return "empty mesh"
    raw = np.frombuffer(vb[:n * stride], dtype=np.uint8).reshape(n, stride)[:, :8].copy().view("<i2").reshape(n, 4)
    # format 0 (skinned: haystacks, carts, elevators, NPC parts) keeps a bone value in the 4th short; its unit
    # 10 / 32767 is TENTATIVE (Galata's haystack and hay cart against their bounding boxes)
    s = 10.0 / 32767.0 if fmt == 0 else abs(int(raw[0, 3])) / 8.0 / 32767.0
    verts = raw[:, :3].astype(np.float32) * np.float32(s)
    wide = md["IsIndexBuffer32bit"][0]
    idx = np.frombuffer(b"".join(md["IndexBufferData"]), dtype="<u4" if wide else "<u2").astype(np.uint32)
    tris, prim = [], []
    for k, p in enumerate(md["StandardPrimitives"]):
        g = {kk: _i32(v) for kk, v in _obj(p).fields.items()}
        if g["Type"] != 1:
            continue
        seg = idx[g["StartIndex"]:g["StartIndex"] + 3 * g["PrimitiveCount"]]
        tris.append(seg[:len(seg) // 3 * 3].reshape(-1, 3))
        prim.append(np.full(len(tris[-1]), k, dtype=np.int32))
    if not tris:
        return "no triangle lists"
    t = np.concatenate(tris)
    pr = np.concatenate(prim)
    keep = (t < n).all(axis=1) & (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    t, pr = t[keep], pr[keep]
    if extra is not None:
        at = UV_AT.get(fmt)
        if at is not None and at + 4 <= stride:
            uv = np.frombuffer(vb[:n * stride], dtype=np.uint8).reshape(n, stride)[:, at:at + 4].copy()
            uv = uv.view("<i2").reshape(n, 2).astype(np.float32) / 2048.0
        else:
            uv = np.zeros((n, 2), dtype=np.float32)
        extra.extend([uv, pr])
    return verts, t


def decode_shape(fields):
    verts = np.frombuffer(b"".join(fields["Vertices"]), dtype="<f4").reshape(-1, 3).copy()
    tris = np.frombuffer(b"".join(fields["IndicesTriangles"]), dtype="<u2").astype(np.uint32)
    tris = tris[:len(tris) // 3 * 3].reshape(-1, 3)
    return verts, tris[(tris < len(verts)).all(axis=1)]


def _entity(S, name, o, group=None, record="", layer=None):
    comps, visual, shapes = [], None, []
    for p in o.fields.get("Components", []):
        c = _obj(p)
        if c is None:
            continue
        cn = S.name_of(c.type_hash)
        comps.append(cn)
        if cn == "Visual" and visual is None:
            visual = _link(c.fields.get("Object"))
        elif cn == "InertComponent":
            rb = c.fields.get("RigidBody")
            if isinstance(rb, Obj):
                sid = _link(rb.fields.get("Shape"))
                if sid:
                    shapes.append(sid)
    m = struct.unpack("<16f", o.fields["GlobalMatrix"])
    return Entity(name, o.id, m, None, visual, shapes, comps, group, record, layer)


def load(archive, use_cache=True, log=print):
    st = os.stat(archive)
    key = f"{os.path.basename(archive)}_{st.st_size}_{int(st.st_mtime)}_v{CACHE_VERSION}.pkl"
    cpath = os.path.join(CACHE, key)
    if use_cache and os.path.exists(cpath):
        with open(cpath, "rb") as f:
            return pickle.load(f)

    t0 = time.time()
    S = schema()
    C = Codec(S)
    md = MapData(os.path.abspath(archive))
    lods, raw_meshes, skipped, kinds = {}, {}, {}, {}
    mesh_mats, material_set, tex_sets, textures = {}, {}, {}, {}
    tex_dir = os.path.join(CACHE, "textures", os.path.basename(archive).rsplit(".", 1)[0])
    os.makedirs(tex_dir, exist_ok=True)

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    records = []
    for rec_name, name, type_id, body, layer in iter_objects_ex(archive, records):
        cls = S.name_of(type_id)
        if len(body) >= 6 and body[:2] == UID_MARK:
            kinds.setdefault(body[2:6], cls)
        if cls == "TextureMap":
            if "Diffuse" in name or name not in textures:
                path = os.path.join(tex_dir, f"{body[2:6].hex()}.dds")
                if not os.path.exists(path):
                    dds = dds_bytes(body)
                    if dds is not None:
                        with open(path, "wb") as f:
                            f.write(dds)
                if os.path.exists(path):
                    textures[body[2:6]] = (name, path)
            continue
        if cls not in ("Entity", "EntityGroup", "Mesh", "LODSelector", "MeshShape", "Material", "TextureSet"):
            continue
        try:
            o = C.decode(body).obj
        except DecodeError:
            if cls in ("Entity", "EntityGroup") and len(body) >= 74 and body[:2] == UID_MARK:
                # still placed: the GlobalMatrix is the first field (body +10). Spawn points land here (their Scene
                # component's PlayerActor uses a hand-written serializer the codec doesn't cover)
                m = struct.unpack_from("<16f", body, 10)
                md.entities.append(Entity(name, body[2:6], m, None, None, [], ["(not decoded)"], None, rec_name,
                                          layer))
            skip(f"{cls} not decodable")
            continue
        if cls == "Entity":
            md.entities.append(_entity(S, name, o, record=rec_name, layer=layer))
        elif cls == "EntityGroup":
            md.entities.append(_entity(S, name, o, record=rec_name, layer=layer))
            for i, r in enumerate(o.fields.get("Entities", [])):
                inner = _obj(r)
                if inner is not None and "GlobalMatrix" in inner.fields:
                    md.entities.append(_entity(S, f"{name}/{i}", inner, group=name, record=rec_name, layer=layer))
        elif cls == "LODSelector":
            # the nearest level that names something (UseNullObjects selectors leave LOD0 empty)
            links = [_link(_obj(d).fields.get("Object")) for d in o.fields.get("LODDescs", [])]
            lods[o.id] = next((x for x in links if x and x != bytes(4)), None)
        elif cls == "Mesh":
            extra = []
            r = decode_mesh(o.fields, extra)
            if isinstance(r, str):
                skip(r)
            else:
                raw_meshes[o.id] = (name,) + r
                mats = [_link(x) for x in o.fields.get("CompiledMeshMaterials", [])]
                md.mesh_extra[o.id] = (extra[0], extra[1], mats)
        elif cls == "Material":
            material_set[o.id] = (name, _link(o.fields.get("TextureSet")))
        elif cls == "TextureSet":
            tex_sets[o.id] = [_link(x) for x in o.fields.get("Maps", [])]
        else:
            md.shapes[o.id] = (name,) + decode_shape(o.fields)
            md.shape_layer[o.id] = struct.unpack("<I", o.fields["CollisionFilterInfo"].fields["Layer"])[0]

    used = set()
    for e in md.entities:
        mid = lods.get(e.visual, e.visual)
        if mid in raw_meshes:
            e.mesh = mid
            used.add(mid)
        elif e.visual is not None:
            skip(f"visual is a {kinds.get(lods.get(e.visual, e.visual), 'object outside this archive')}")
    md.meshes = {k: v for k, v in raw_meshes.items() if k in used}
    md.mesh_extra = {k: v for k, v in md.mesh_extra.items() if k in used}
    for mid, (mname, set_id) in material_set.items():
        maps = [m for m in tex_sets.get(set_id, []) if m and m != bytes(4)]
        diffuse = next((m for m in maps if m in textures and "Diffuse" in textures[m][0]), None)
        diffuse = diffuse or next((m for m in maps if m in textures and "Normal" not in textures[m][0]
                                   and "Specular" not in textures[m][0]), None)
        md.materials[mid] = (mname, textures[diffuse][1] if diffuse else None)
    bad = [r for r in records if r[1]]
    if bad:
        skipped["unreadable records"] = len(bad)
    md.skipped = skipped
    log(f"read {os.path.basename(archive)}: {len(md.entities)} entities, {len(md.meshes)} meshes, "
        f"{len(md.shapes)} collision shapes in {time.time() - t0:.0f} s")

    os.makedirs(CACHE, exist_ok=True)
    with open(cpath, "wb") as f:
        pickle.dump(md, f)
    return md


if __name__ == "__main__":
    # through the package, so the cache pickles ac3editor.mapdata classes, not __main__ ones
    from ac3editor import mapdata as _self
    m = _self.load(sys.argv[1], use_cache="--fresh" not in sys.argv)
    tv = sum(len(v[1]) for v in m.meshes.values())
    tt = sum(len(v[2]) for v in m.meshes.values())
    print(f"{len(m.entities)} entities ({sum(1 for e in m.entities if e.mesh)} with a mesh), {len(m.meshes)} meshes "
          f"({tv} vertices, {tt} triangles), {len(m.shapes)} collision shapes")
    print("skipped:", m.skipped)
