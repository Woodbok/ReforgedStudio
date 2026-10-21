"""A map as Reforged Studio draws it: one binary file the app memory-maps (<workspace>/cache/studio/<map>_<size>_<time>_s<v>.ac3s).

Made from ac3editor.mapdata (and ac3editor.spawns for the spawn points) once per archive version; opening the map again
reads the file only. Little-endian throughout; a string is u16 length + UTF-8 bytes.

    magic "AC3S", u32 version
    u32 n, n x str                          texture DDS paths (<workspace>/cache/textures/..., mapdata writes them)
    u32 n, n x (u32 uid, str name, i32 texture)                                                 materials
    u32 n, n x mesh: u32 uid, str name, u32 nv, u32 nt, u32 nsub,
                     f32 pos[nv*3], f32 uv[nv*2], u32 idx[nt*3] (grouped by sub),
                     nsub x (i32 material, u32 first triangle, u32 triangles)
    u32 n, n x shape: u32 uid, str name, u32 layer, u32 nv, u32 nt, f32 pos[nv*3], u32 idx[nt*3]
    u32 n, n x record: str name, str label  (label: the game mode of a DataBlock record, else "")
    u32 n, n x entity: u32 uid, str name, f32 matrix[16] (AC3 row-vector: translation in 12..14), i32 mesh,
                       u32 nshapes, nshapes x i32 shape, i32 record, i32 layer (-1: none), str group,
                       str components, u32 flags, str note
    flags: 1 gameplay volume (out of bounds, zones), 2 spawn point, 4 match start, 8 not decodable (fixed),
           16 in a game-mode record, 32 an EntityGroup head
"""
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from ac3editor.workspace import WORK  # noqa: E402  (the workspace: builds, caches)
from ac3editor import mapdata, spawns  # noqa: E402

VERSION = 1
CACHE = os.path.join(WORK, "cache", "studio")
VOLUME_COMPONENTS = {"OutOfBoundsComponent", "GameplayZoneComponent"}
F_VOLUME, F_SPAWN, F_START, F_FIXED, F_MODE, F_GROUP_HEAD = 1, 2, 4, 8, 16, 32


def cache_path(archive):
    st = os.stat(archive)
    return os.path.join(CACHE, f"{os.path.basename(archive)}_{st.st_size}_{int(st.st_mtime)}_s{VERSION}.ac3s")


def u32(uid):
    return struct.unpack("<I", uid)[0]


class _W:
    def __init__(self, f):
        self.f = f

    def u32(self, *v):
        self.f.write(struct.pack(f"<{len(v)}I", *v))

    def i32(self, *v):
        self.f.write(struct.pack(f"<{len(v)}i", *v))

    def str(self, s):
        b = (s or "").encode("utf-8")[:65535]
        self.f.write(struct.pack("<H", len(b)) + b)

    def arr(self, a, dtype):
        self.f.write(np.ascontiguousarray(a, dtype=dtype).tobytes())


def write(archive, md=None, log=print):
    """-> path of the archive's scene cache, made if missing."""
    out = cache_path(archive)
    if os.path.exists(out):
        return out
    md = md or mapdata.load(archive, log=log)
    try:
        sinfo = spawns.read(archive)
    except Exception as e:  # noqa: BLE001 - a map whose spawns don't read still opens
        log(f"spawn points not read: {type(e).__name__}: {e}")
        sinfo = dict(spawns={}, modes={})
    used_in = spawns.active(sinfo)

    textures, tex_index = [], {}
    materials, mat_index = [], {}
    for mid, (name, path) in md.materials.items():
        t = -1
        if path:
            if path not in tex_index:
                tex_index[path] = len(textures)
                textures.append(path)
            t = tex_index[path]
        mat_index[mid] = len(materials)
        materials.append((mid, name, t))
    mesh_index = {uid: k for k, uid in enumerate(md.meshes)}
    shape_index = {uid: k for k, uid in enumerate(md.shapes)}

    entities, records, rec_index = [], [], {}
    heads = {e.group for e in md.entities if e.group}
    for e in md.entities:
        per_mode = e.record.startswith("DataBlock_")
        sp = sinfo["spawns"].get(e.uid) if per_mode else None
        if sp is not None and e.uid not in used_in.get(e.record, {}):
            continue                                  # (as the Blender import: a spawn only where its mode uses it)
        if e.record not in rec_index:
            rec_index[e.record] = len(records)
            records.append((e.record, spawns.mode_label(e.record) if per_mode else ""))
        flags, note = 0, ""
        if any(c in VOLUME_COMPONENTS for c in e.components):
            flags |= F_VOLUME
        if sp is not None:
            flags |= F_SPAWN | (F_START if sp.kind[0] == spawns.START else 0)
            note = spawns.kind_label(tuple(sp.kind)) + " - used in " + ", ".join(
                spawns.mode_label(r) for r in sp.modes(sinfo))
        if e.components == ["(not decoded)"]:
            flags |= F_FIXED
        if per_mode:
            flags |= F_MODE
        if not e.group and e.name in heads:
            flags |= F_GROUP_HEAD
        entities.append((e, flags, note))

    os.makedirs(CACHE, exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        w = _W(f)
        f.write(b"AC3S")
        w.u32(VERSION)
        w.u32(len(textures))
        for p in textures:
            w.str(p)
        w.u32(len(materials))
        for mid, name, t in materials:
            w.u32(u32(mid))
            w.str(name)
            w.i32(t)
        w.u32(len(md.meshes))
        for uid, (name, v, t) in md.meshes.items():
            uv, prim, mats = md.mesh_extra.get(uid, (np.zeros((len(v), 2), np.float32),
                                                     np.zeros(len(t), np.int32), []))
            order = np.argsort(prim, kind="stable")
            t, prim = t[order], prim[order]
            subs = []
            for k in np.unique(prim):
                where = np.nonzero(prim == k)[0]
                mat = mats[k] if k < len(mats) else None
                subs.append((mat_index.get(mat, -1), int(where[0]), len(where)))
            w.u32(u32(uid))
            w.str(name)
            w.u32(len(v), len(t), len(subs))
            w.arr(v, "<f4")
            w.arr(uv, "<f4")
            w.arr(t, "<u4")
            for m, first, n in subs:
                w.i32(m)
                w.u32(first, n)
        w.u32(len(md.shapes))
        for uid, (name, v, t) in md.shapes.items():
            w.u32(u32(uid))
            w.str(name)
            w.u32(md.shape_layer.get(uid, 0), len(v), len(t))
            w.arr(v, "<f4")
            w.arr(t, "<u4")
        w.u32(len(records))
        for name, label in records:
            w.str(name)
            w.str(label)
        w.u32(len(entities))
        for e, flags, note in entities:
            w.u32(u32(e.uid))
            w.str(e.name)
            w.arr(np.asarray(e.matrix, np.float32), "<f4")
            w.i32(mesh_index.get(e.mesh, -1))
            shp = [shape_index[s] for s in e.shapes if s in shape_index]
            w.u32(len(shp))
            if shp:
                w.i32(*shp)
            w.i32(rec_index[e.record], -1 if e.layer is None else int(e.layer))
            w.str(e.group or "")
            w.str(", ".join(e.components))
            w.u32(flags)
            w.str(note)
    os.replace(tmp, out)
    log(f"scene cache: {len(entities)} objects, {len(md.meshes)} meshes, {len(textures)} textures -> "
        f"{os.path.basename(out)} ({os.path.getsize(out) / 1e6:.1f} MB)")
    return out
