"""Character mesh editing: a part's mesh out to Blender as glTF (skinned: its bones, weights, UVs, colour map) and an
edited glTF back in as AC3's skinned Mesh - previewed in the viewer, built into a test DataPC on the default set
(charedit.build).

The skinned Mesh (class 0x415D9568), read and written through the editor's schema codec (ac3editor mapdata.Codec - it
reproduces every stock body byte for byte), the fields measured on the default set's part meshes:
* Bones: the palette - per entry the inverse bind (PackedInitialMatrixInverse: six int16 = two columns of the rotation,
  the third their cross product, and three 21-bit fields / 65536 = the translation) and the BoneID (the skeleton bone's
  hash). Kept as it is: an import maps the glTF's joints back onto these entries by name and adds none.
* CompiledMesh.MeshData: VertexFormat 0, VertexStride 32 - per vertex: int16 x y z (metres = q x 10 / 32768) and w
  (32767); u8 normal, 255; u8 tangent, 255; u8 binormal, 255 (each (b - 127.5) / 127.5; the binormal carries the
  handedness: w is 32767 on every vertex); int16 u v (/ 2048); four bone bytes (indices into the sub-mesh's bone
  table) and four weight bytes summing to 255. Indices: u16, absolute (into the whole vertex buffer).
  StandardPrimitives / ShadowPrimitives: per sub-mesh MinIndex (its first vertex), NumVertices, StartIndex,
  PrimitiveCount, Type 1 - the shadow list the same.
* CompiledMesh.InstancingData: per sub-mesh NumBones, HwBoneToMeshBoneTable (42 palette indices, the first 0),
  NumVertices (its vertex count's low byte, as every stock mesh), Material; CompiledMeshMaterials one per sub-mesh.
  At most 42 bones per sub-mesh: an import splits a material's triangles into as many sub-meshes as that needs.

Frames: the mesh's vertices are in its own frame (x y z, z up); the skeleton's is (y, z, x) (charview); glTF's is y up
(x, z, -y). A joint's node = its bind (the palette entry inverted), parented to its nearest palette ancestor in the
persona's rig, so Blender shows the skeleton as it is.

    export(pid, part_file) -> dict(glb, ...)      <workspace>/characters/meshes/<part>/<part>.glb (+ .json: its source)
    mesh_bodies(part_file, glb, lods) -> {mesh object name: new body}    (the import; checked: decoded back)
"""
import copy
import json
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ac3editor.workspace import WORK  # noqa: E402  (the workspace: builds, caches)
import charview as C  # noqa: E402

OUT = os.path.join(WORK, "characters", "meshes")
MESH = 0x415D9568
UNIT = 10.0 / 32768
UVS = 2048.0
TABLE = 42                       # HwBoneToMeshBoneTable slots: bones per sub-mesh
G = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], np.float64)      # mesh frame -> glTF (y up)
P = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], np.float64)       # mesh frame -> skeleton frame


class MeshEditError(ValueError):
    pass


def _codec():
    from ac3editor import mapdata as M
    return M, M.Codec(M.schema())


def _views():
    F, D, E = C._forge()
    import look_rows
    return D, E, look_rows.BuildView(D), look_rows.BuildView(E)


def part_objects(part_file):
    """-> (archive path, [objects]) of a part file in the default set (DataPC first, then DataPC_extra)."""
    D, E, dv, ev = _views()
    for path, v in ((D, dv), (E, ev)):
        if part_file in v.by_name:
            return path, v.objects(part_file)
    raise MeshEditError(f"no file {part_file} in the default set")


def lod_meshes(objs, part_file):
    """The part's Mesh objects, LOD0 first (by the LODSelector's order of naming them, else by name)."""
    meshes = [o for o in objs if o["type"] == MESH]
    if not meshes:
        raise MeshEditError(f"{part_file}: no Mesh")
    return sorted(meshes, key=lambda o: (0 if o["name"].endswith("_LOD0") else 1, o["name"]))


# ------------------------------------------------------------------------------------------------ reading a mesh --

def read_mesh(body):
    """A skinned Mesh body -> dict(pos (n, 3) metres, nrm, tan, bin (n, 3), uv (n, 2), joints (n, 4) palette indices,
    weights (n, 4) 0..1, subs [dict(first, count, start, tris, bones [palette indices], material uid)], palette
    [(bone id, M (3 x 3: loc = M S + t), t)], cloth bool)."""
    M, Cd = _codec()
    root = Cd.decode(body)
    o = root.obj
    cm = M._obj(o.fields["CompiledMesh"])
    md = cm.fields["MeshData"].fields
    if md["VertexStride"] != b"\x20" or md["VertexFormat"] != b"\x00":
        raise MeshEditError(f"vertex format {md['VertexFormat'].hex()} stride {md['VertexStride'][0]}: not a skinned part")
    vb = b"".join(md["VertexBufferData"])
    n = len(vb) // 32
    r = np.frombuffer(vb, np.uint8).reshape(n, 32)
    q = r[:, :8].copy().view("<i2").reshape(n, 4)
    un = lambda a: (r[:, a:a + 3].astype(np.float64) - 127.5) / 127.5  # noqa: E731
    wide = md["IsIndexBuffer32bit"] != b"\x00"
    ib = b"".join(md["IndexBufferData"])
    idx = np.frombuffer(ib, "<u4" if wide else "<u2").astype(np.int64)
    prims = [{k: struct.unpack("<i", v)[0] for k, v in M._obj(p).fields.items()} for p in md["StandardPrimitives"]]
    insts = [M._obj(x) for x in cm.fields["InstancingData"]]
    subs = []
    for k, pr in enumerate(prims):
        inst = insts[k].fields if k < len(insts) else None
        nb = inst["NumBones"][0] if inst else 0
        table = list(b"".join(inst["HwBoneToMeshBoneTable"]))[:nb] if inst else []
        tris = idx[pr["StartIndex"]:pr["StartIndex"] + 3 * pr["PrimitiveCount"]].reshape(-1, 3)
        if len(tris) and tris.max() < pr["NumVertices"] and pr["MinIndex"] and tris.min() < pr["MinIndex"]:
            tris = tris + pr["MinIndex"]                      # relative indices (no stock mesh uses them)
        mat = M._link(inst["Material"]) if inst else None
        subs.append(dict(first=pr["MinIndex"], count=pr["NumVertices"], start=pr["StartIndex"], tris=tris,
                         bones=table, material=struct.unpack("<I", mat)[0] if isinstance(mat, (bytes, bytearray)) and len(mat) == 4 else mat))
    # each vertex's bone bytes go through the table of the sub-mesh whose triangles use it
    sub_of = np.full(n, -1, np.int64)
    for k, s in enumerate(subs):
        v = np.unique(s["tris"].reshape(-1))
        v = v[(v < n)]
        sub_of[v[sub_of[v] < 0]] = k
    joints = np.zeros((n, 4), np.int64)
    weights = r[:, 28:32].astype(np.float64) / 255.0
    merged = 0
    for i in range(n):
        k = sub_of[i] if sub_of[i] >= 0 else 0
        tab = subs[k]["bones"] if subs else []
        for j in range(4):
            b = r[i, 24 + j]
            if weights[i, j] > 0:
                joints[i, j] = tab[b] if b < len(tab) else 0
        # one bone in two slots (stock: Blackbeard's head) -> one slot with both weights (glTF importers keep one)
        for j in range(4):
            for jj in range(j):
                if weights[i, j] > 0 and weights[i, jj] > 0 and joints[i, j] == joints[i, jj]:
                    weights[i, jj] += weights[i, j]
                    weights[i, j] = 0
                    joints[i, j] = 0
                    merged += 1
    palette = []
    import part_render
    import skinmesh
    lay = skinmesh.layout(body)
    for bone, (c1, c2, c3), t in part_render._palette(body, lay):
        palette.append((bone, np.array([c1, c2, c3], np.float64).T, np.asarray(t, np.float64)))
    cloth = any(o.fields.get(k, b"\x00\x00") not in (b"\x00", b"\x00\x00") for k in ("Dynamic", "DynamicMeshVertexCount"))
    return dict(pos=q[:, :3].astype(np.float64) * UNIT, w=q[:, 3], nrm=un(8), tan=un(12), bin=un(16),
                uv=r[:, 20:24].copy().view("<i2").reshape(n, 2).astype(np.float64) / UVS, joints=joints, weights=weights,
                subs=subs, palette=palette, cloth=cloth, root=root, sub_of=sub_of, merged=merged)


# ------------------------------------------------------------------------------------------------ glTF --

def _orthonormal(R):
    u, _s, vt = np.linalg.svd(R)
    out = u @ vt
    if np.linalg.det(out) < 0:
        u[:, -1] *= -1
        out = u @ vt
    return out


def _quat(R):
    return C._mat_quat(R)                           # (x, y, z, w)


def bind_globals(palette):
    """Each palette entry's bind in glTF's frame: (R (3 x 3), t (3)) - the inverse of the stored inverse bind,
    carried mesh frame -> glTF."""
    out = []
    for _bone, Mb, t in palette:
        Rs = _orthonormal(Mb).T                       # bind rotation in the skeleton frame
        ts = -Rs @ t
        Rv, tv = P.T @ Rs @ P, P.T @ ts              # in the mesh frame
        out.append((G @ Rv @ G.T, G @ tv))
    return out


def rig_parents(pid, part_file):
    """{bone id: parent bone id} over the persona's rig (Human Body, the part file's and the persona's add-on rigs)."""
    D, E, dv, ev = _views()
    main = f"AC3MP_ID{pid}_Main_Player" if f"AC3MP_ID{pid}_Main_Player" in dv.by_name else "AC3MP_ID87_Main_Player"
    parent = {}
    recs = [main, part_file] + [r for r in dv.by_name if r.startswith(f"AC3MP_ID{pid}_") and "Addon" in r]
    for rec in recs:
        for v in (dv, ev):
            if rec not in v.by_name:
                continue
            for o in v.objects(rec):
                if C.b_find(o["body"], C.BONE, 0) < 0:
                    continue
                for h, ph, _lp, _lq in C.skeleton_bones(o["body"]):
                    parent.setdefault(h, ph)
    return parent


def persona_of(part_file):
    import re
    m = re.match(r"AC3MP_ID(\d+)_", part_file)
    return int(m.group(1)) if m else 87


def _parents(pid, palette, part_file):
    """palette index -> the palette index of its nearest ancestor in the persona's rig (None: a root)."""
    parent = rig_parents(pid, part_file)
    at = {}
    for k, (bone, _m, _t) in enumerate(palette):
        at.setdefault(bone, k)
    out = {}
    for k, (bone, _m, _t) in enumerate(palette):
        p, seen = parent.get(bone), set()
        while p is not None and p not in at and p not in seen:
            seen.add(p)
            p = parent.get(p)
        out[k] = at.get(p) if p is not None and at.get(p) != k else None
    return out


class _Glb:
    def __init__(self):
        self.bin = bytearray()
        self.j = dict(asset=dict(version="2.0", generator="Reforged Studio"), buffers=[], bufferViews=[], accessors=[],
                      meshes=[], nodes=[], skins=[], materials=[], textures=[], images=[], samplers=[], scenes=[],
                      scene=0)

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        v = dict(buffer=0, byteOffset=len(self.bin), byteLength=len(data))
        if target:
            v["target"] = target
        self.bin += data
        self.j["bufferViews"].append(v)
        return len(self.j["bufferViews"]) - 1

    def acc(self, arr, ctype, typ, target=None, normalized=False, minmax=False):
        a = np.ascontiguousarray(arr)
        view = self.view(a.tobytes(), target)
        d = dict(bufferView=view, componentType=ctype, count=int(a.shape[0]), type=typ)
        if normalized:
            d["normalized"] = True
        if minmax:
            d["min"] = [float(x) for x in a.min(0)]
            d["max"] = [float(x) for x in a.max(0)]
        self.j["accessors"].append(d)
        return len(self.j["accessors"]) - 1

    def write(self, path):
        self.j["buffers"] = [dict(byteLength=len(self.bin))]
        for k in ("textures", "images", "samplers", "skins", "materials"):
            if not self.j[k]:
                del self.j[k]
        js = json.dumps(self.j, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        bn = bytes(self.bin) + bytes(-len(self.bin) % 4)
        with open(path, "wb") as fh:
            fh.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bn)))
            fh.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            fh.write(struct.pack("<II", len(bn), 0x004E4942) + bn)


def joint_name(k, bone):
    return f"{k:03d}_{bone:08X}"


def material_name(uid, name):
    return f"{uid:08X} {name}"


def export(pid, part_file, log=print):
    """A part's LOD0 mesh -> <workspace>/characters/meshes/<part>/<part>.glb: one glTF primitive per sub-mesh (its material
    named '<uid> <material name>' with the colour map as a PNG), its palette as joints (named '<palette index>_<bone
    id>') at their binds, the weights. -> dict(glb, vertices, triangles, sub_meshes, bones, ...)."""
    t0 = time.time()
    D, E, dv, ev = _views()
    v = dv if part_file in dv.by_name else ev if part_file in ev.by_name else None
    if v is None:
        raise MeshEditError(f"no file {part_file} in the default set")
    objs = v.objects(part_file)
    by_uid = {C._uid(o["uid"]): o for o in objs}
    lod0 = lod_meshes(objs, part_file)[0]
    m = read_mesh(lod0["body"])
    folder = os.path.join(OUT, part_file)
    os.makedirs(folder, exist_ok=True)
    g = _Glb()
    # the joints
    binds = bind_globals(m["palette"])
    parents = _parents(pid, m["palette"], part_file)
    first_joint = 0
    for k, (bone, _M, _t) in enumerate(m["palette"]):
        R, t = binds[k]
        p = parents[k]
        if p is not None:
            Rp, tp = binds[p]
            Rl, tl = Rp.T @ R, Rp.T @ (t - tp)
        else:
            Rl, tl = R, t
        qx, qy, qz, qw = _quat(Rl)
        g.j["nodes"].append(dict(name=joint_name(k, bone), translation=[float(x) for x in tl],
                                 rotation=[float(qx), float(qy), float(qz), float(qw)]))
    for k in range(len(m["palette"])):
        kids = [c for c, p in parents.items() if p == k]
        if kids:
            g.j["nodes"][first_joint + k]["children"] = kids
    # inverse binds from the nodes as glTF will compose them (the rotations exactly orthonormal)
    glob = {}

    def world(k):
        if k in glob:
            return glob[k]
        nd = g.j["nodes"][k]
        R = C._qmat(tuple(nd["rotation"]))
        t = np.asarray(nd["translation"])
        p = parents[k]
        if p is not None:
            Rp, tp = world(p)
            R, t = Rp @ R, Rp @ t + tp
        glob[k] = (R, t)
        return glob[k]
    ibm = np.zeros((len(m["palette"]), 16), np.float32)
    for k in range(len(m["palette"])):
        R, t = world(k)
        Mx = np.eye(4)
        Mx[:3, :3], Mx[:3, 3] = R, t
        ibm[k] = np.linalg.inv(Mx).T.reshape(-1)        # column-major
    skin_acc = g.acc(ibm, 5126, "MAT4")
    # the materials: the colour map as PNG
    from PIL import Image
    import io
    mats = {}
    for s in m["subs"]:
        u = s["material"]
        if u in mats:
            continue
        mo = by_uid.get(u)
        name = mo["name"] if mo else "material"
        md = dict(name=material_name(u or 0, name), doubleSided=True,
                  pbrMetallicRoughness=dict(metallicFactor=0.0, roughnessFactor=1.0))
        diff = C._diffuse(by_uid, mo) if mo else None
        if diff is not None:
            rgba = C._decode(diff["body"])
            if rgba is not None:
                im = Image.fromarray(rgba)
                buf = io.BytesIO()
                im.save(buf, "PNG")
                if not g.j["samplers"]:
                    g.j["samplers"].append(dict(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497))
                g.j["images"].append(dict(name=diff["name"], mimeType="image/png", bufferView=g.view(buf.getvalue())))
                g.j["textures"].append(dict(source=len(g.j["images"]) - 1, sampler=0))
                md["pbrMetallicRoughness"]["baseColorTexture"] = dict(index=len(g.j["textures"]) - 1)
                if "Alpha" in name or "Hair" in name:
                    md["alphaMode"] = "MASK"
        g.j["materials"].append(md)
        mats[u] = len(g.j["materials"]) - 1
    # one primitive per sub-mesh, its own vertices
    prims = []
    hand = np.sign(np.einsum("ij,ij->i", np.cross(m["nrm"], m["tan"]), m["bin"]))
    hand[hand == 0] = 1
    for s in m["subs"]:
        used = np.unique(s["tris"].reshape(-1))
        remap = {int(a): i for i, a in enumerate(used)}
        tri = np.vectorize(remap.get)(s["tris"]).astype(np.uint32) if len(used) else np.zeros((0, 3), np.uint32)
        pos = (m["pos"][used] @ G.T).astype(np.float32)
        nrm = m["nrm"][used] @ G.T
        nrm = (nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)).astype(np.float32)
        tan = m["tan"][used] @ G.T
        tan = tan / np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-9)
        tan4 = np.concatenate([tan, hand[used, None]], 1).astype(np.float32)
        w = m["weights"][used]
        w = (w / np.maximum(w.sum(1, keepdims=True), 1e-9)).astype(np.float32)
        attrs = dict(POSITION=g.acc(pos, 5126, "VEC3", 34962, minmax=True), NORMAL=g.acc(nrm, 5126, "VEC3", 34962),
                     TANGENT=g.acc(tan4, 5126, "VEC4", 34962), TEXCOORD_0=g.acc(m["uv"][used].astype(np.float32), 5126, "VEC2", 34962),
                     JOINTS_0=g.acc(m["joints"][used].astype(np.uint16), 5123, "VEC4", 34962),
                     WEIGHTS_0=g.acc(w, 5126, "VEC4", 34962))
        prims.append(dict(attributes=attrs, indices=g.acc(tri.reshape(-1), 5125, "SCALAR", 34963),
                          material=mats[s["material"]], mode=4))
    g.j["meshes"].append(dict(name=lod0["name"], primitives=prims))
    roots = [k for k, p in parents.items() if p is None]
    mesh_node = len(g.j["nodes"])
    g.j["nodes"].append(dict(name=part_file, mesh=0, skin=0))
    g.j["skins"].append(dict(name="AC3_" + part_file, joints=list(range(len(m["palette"]))), inverseBindMatrices=skin_acc))
    arm = len(g.j["nodes"])
    g.j["nodes"].append(dict(name="AC3_Skeleton", children=roots))
    g.j["scenes"].append(dict(name=part_file, nodes=[arm, mesh_node]))
    g.j["extras"] = dict(ac3studio=dict(persona=pid, part=part_file, mesh=lod0["name"],
                                        palette=[f"{b:08X}" for b, _m, _t in m["palette"]]))
    glb = os.path.join(folder, part_file + ".glb")
    g.write(glb)
    info = dict(glb=glb, persona=pid, part=part_file, mesh=lod0["name"], lods=[o["name"] for o in lod_meshes(objs, part_file)],
                vertices=int(len(m["pos"])), triangles=int(sum(len(s["tris"]) for s in m["subs"])), sub_meshes=len(m["subs"]),
                bones=len(m["palette"]), materials=[g.j["materials"][i]["name"] for i in mats.values()], cloth=m["cloth"],
                base=D, base_stamp=C._stamp(D), seconds=round(time.time() - t0, 1))
    json.dump(info, open(os.path.join(folder, part_file + ".json"), "w", encoding="utf-8"), indent=1)
    log(f"{part_file}: {info['vertices']} vertices, {info['triangles']} triangles, {info['bones']} bones -> {glb}")
    return info


# ------------------------------------------------------------------------------------------------ glTF back in --

COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(path):
    """-> (json, accessor reader). glTF binary only (Blender: File > Export > glTF 2.0, format glb)."""
    raw = open(path, "rb").read()
    if raw[:4] != b"glTF":
        raise MeshEditError(f"{os.path.basename(path)} is not a .glb (export glTF Binary from Blender)")
    p, js, bn = 12, None, b""
    while p < len(raw):
        ln, kind = struct.unpack_from("<II", raw, p)
        chunk = raw[p + 8:p + 8 + ln]
        if kind == 0x4E4F534A:
            js = json.loads(chunk)
        elif kind == 0x004E4942:
            bn = chunk
        p += 8 + ln

    def acc(i):
        a = js["accessors"][i]
        if "sparse" in a:
            raise MeshEditError("sparse accessors are not read")
        dt = np.dtype(COMP[a["componentType"]]).newbyteorder("<")
        nc = NCOMP[a["type"]]
        if "bufferView" not in a:
            return np.zeros((a["count"], nc), dt)
        v = js["bufferViews"][a["bufferView"]]
        off = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = v.get("byteStride", dt.itemsize * nc)
        out = np.ndarray((a["count"], nc), dt, buffer=bn, offset=off, strides=(stride, dt.itemsize)).copy()
        if a.get("normalized") and dt.kind in "iu":
            out = out.astype(np.float64) / np.iinfo(dt).max
        return out
    return js, acc


def read_edit(glb, palette, materials):
    """An edited .glb -> dict(pos, nrm, tan (or None), hand, uv, joints (palette indices), weights, mat (per triangle:
    material uid), tris) in the mesh frame. palette: [bone id] (joints map back by name), materials: {uid}."""
    js, acc = read_glb(glb)
    nodes = js.get("nodes", [])
    skinned = [n for n in nodes if "mesh" in n and "skin" in n]
    if not skinned:
        raise MeshEditError("no skinned mesh in the file (keep the armature: export with Skinning on)")
    if len(skinned) > 1:
        raise MeshEditError(f"{len(skinned)} skinned meshes in the file: join them into one (Ctrl+J) first")
    node = skinned[0]
    skin = js["skins"][node["skin"]]
    joint_pal = []
    for ni in skin["joints"]:
        name = nodes[ni].get("name", "")
        try:
            k, bone = name.split("_", 1)
            k, bone = int(k), int(bone[:8], 16)
        except ValueError:
            raise MeshEditError(f"joint {name!r}: not one of the part's bones (new bones aren't supported)") from None
        if k >= len(palette) or palette[k] != bone:
            raise MeshEditError(f"joint {name!r}: not this part's palette entry {k}")
        joint_pal.append(k)
    mats = js.get("materials", [])
    out = dict(pos=[], nrm=[], tan=[], uv=[], joints=[], weights=[], mat=[], tris=[])
    base, has_tan = 0, True
    Gi = G.T                                          # glTF -> mesh frame
    for pr in js["meshes"][node["mesh"]]["primitives"]:
        if pr.get("mode", 4) != 4:
            raise MeshEditError("only triangles")
        a = pr["attributes"]
        for need in ("POSITION", "NORMAL", "TEXCOORD_0", "JOINTS_0", "WEIGHTS_0"):
            if need not in a:
                raise MeshEditError(f"a primitive without {need} (export with UVs, normals and skinning)")
        mname = mats[pr["material"]]["name"] if "material" in pr else ""
        try:
            uid = int(mname.split(" ", 1)[0], 16)
        except ValueError:
            uid = None
        if uid not in materials:
            raise MeshEditError(f"material {mname!r}: use the part's own materials (new ones aren't supported)")
        pos = acc(a["POSITION"]).astype(np.float64)
        n = len(pos)
        idx = acc(pr["indices"]).reshape(-1).astype(np.int64) if "indices" in pr else np.arange(n)
        out["pos"].append(pos @ Gi.T)
        out["nrm"].append(acc(a["NORMAL"]).astype(np.float64) @ Gi.T)
        if "TANGENT" in a:
            t4 = acc(a["TANGENT"]).astype(np.float64)
            out["tan"].append(np.concatenate([t4[:, :3] @ Gi.T, t4[:, 3:4]], 1))
        else:
            has_tan = False
        out["uv"].append(acc(a["TEXCOORD_0"]).astype(np.float64))
        j = acc(a["JOINTS_0"]).astype(np.int64)
        w = acc(a["WEIGHTS_0"]).astype(np.float64)
        for extra in ("JOINTS_1", "WEIGHTS_1"):
            if extra in a and np.any(acc(a["WEIGHTS_1"]) > 0) and extra == "WEIGHTS_1":
                raise MeshEditError("more than 4 bone weights per vertex: limit them to 4 in Blender's export")
        jp = np.array(joint_pal, np.int64)
        if j.size and j.max() >= len(jp):
            raise MeshEditError("a joint index past the skin's joints")
        out["joints"].append(jp[j] if j.size else j)
        out["weights"].append(w)
        tri = idx.reshape(-1, 3) + base
        out["tris"].append(tri)
        out["mat"].append(np.full(len(tri), uid, np.int64))
        base += n
    res = {k: np.concatenate(v) for k, v in out.items() if v}
    if not has_tan:
        res.pop("tan", None)
    return res


def _unit8(v):
    v = np.asarray(v, np.float64)
    v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
    return np.clip(np.rint(v * 127.5 + 127.5), 0, 255).astype(np.uint8)


def _tangents(pos, nrm, uv, tris):
    """Per-vertex tangents along +u and handedness, from the triangles (where the glTF carries none)."""
    from ac3editor import meshenc
    _vn, vt, hand = meshenc.frame(pos, tris, uv, normals=nrm)
    return vt, hand


def build_body(template_body, e, parent=None):
    """The template Mesh (the part's LOD k: its own palette kept) with the edit's geometry -> (body, report). The
    edit's bones (e["bones"]: bone ids) go onto the template's palette entries; a bone a far LOD's palette lacks goes
    to its nearest ancestor there (parent: {bone id: parent id})."""
    M, Cd = _codec()
    import part_render
    import skinmesh
    tfull = part_render._palette(template_body, skinmesh.layout(template_body))
    tpal = [b for b, _c, _t in tfull]
    torig = np.array([-np.array([c1, c2, c3], np.float64) @ np.asarray(t, np.float64) for _b, (c1, c2, c3), t in tfull])
    at = {}
    for k, b in enumerate(tpal):
        at.setdefault(b, k)
    moved = set()

    def entry(bone):
        b, seen = bone, set()
        while b is not None and b not in at and b not in seen:
            seen.add(b)
            b = (parent or {}).get(b)
        if b is None or b not in at:
            # no ancestor in this LOD's palette (an ACR rig the persona's rig doesn't hold): its nearest bind origin
            o = (e.get("origins") or {}).get(bone)
            if o is None or not len(torig):
                raise MeshEditError(f"bone {bone:08X} (or an ancestor) is not in this mesh's palette")
            moved.add(bone)
            return int(np.argmin(np.linalg.norm(torig - o, axis=1)))
        if b != bone:
            moved.add(bone)
        return at[b]
    ebones = np.asarray(e["bones"], np.int64)
    cache = {}
    j_all = np.zeros(ebones.shape, np.int64)
    for i, b in np.ndenumerate(ebones):
        b = int(b)
        if b not in cache:
            cache[b] = entry(b)
        j_all[i] = cache[b]
    root = Cd.decode(template_body)
    o = root.obj
    cm = M._obj(o.fields["CompiledMesh"])
    md = cm.fields["MeshData"].fields
    pos, nrm, uv = e["pos"], e["nrm"], e["uv"]
    if np.abs(pos).max() >= 10.0:
        raise MeshEditError(f"a vertex {np.abs(pos).max():.2f} m from the origin: AC3's skinned meshes hold +-10 m")
    if "tan" in e:
        tan, hand = e["tan"][:, :3], np.where(e["tan"][:, 3] < 0, -1.0, 1.0)
    else:
        tan, hand = _tangents(pos, nrm, uv, e["tris"])
    nn = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
    tan = tan - nn * np.einsum("ij,ij->i", tan, nn)[:, None]           # (orthogonal to the normal)
    bn = np.cross(nn, tan) * hand[:, None]
    # weights: the four largest, as bytes summing to 255
    w = np.asarray(e["weights"], np.float64).copy()
    j = j_all
    w[w < 1e-4] = 0
    order = np.argsort(-w, axis=1)
    w = np.take_along_axis(w, order, 1)
    j = np.take_along_axis(j, order, 1)
    s = w.sum(1, keepdims=True)
    if np.any(s[:, 0] <= 0):
        raise MeshEditError(f"{int(np.sum(s[:, 0] <= 0))} vertices weighted to no bone (assign them in Blender)")
    wb = np.rint(w / s * 255).astype(np.int64)                         # (a stock k / 255 comes back as k)
    rem = 255 - wb.sum(1)
    wb[:, 0] += rem
    if np.any(wb[:, 0] < 0):
        raise MeshEditError("weights that don't add up")
    j[wb == 0] = 0
    # sub-meshes: per material, triangles batched so no batch needs more than 42 bones (bone 0 first in each table)
    batches = []
    for uid in dict.fromkeys(e["mat"].tolist()):
        tri_ix = np.nonzero(e["mat"] == uid)[0]
        cur, bones = [], {0}
        for t in tri_ix:
            vs = e["tris"][t]
            need = {int(b) for v in vs for b, x in zip(j[v], wb[v]) if x}
            if len(bones | need) > TABLE:
                batches.append((uid, cur, bones))
                cur, bones = [], {0}
            if len(need | {0}) > TABLE:
                raise MeshEditError("one triangle's corners use more than 42 bones")
            cur.append(t)
            bones |= need
        if cur:
            batches.append((uid, cur, bones))
    vb, ib, prims, insts_spec, first = [], [], [], [], 0
    report = dict(vertices=0, triangles=0, sub_meshes=len(batches), bones_folded=len(moved))
    for uid, tlist, bones in batches:
        table = [0] + sorted(b for b in bones if b != 0)
        slot = {b: i for i, b in enumerate(table)}
        tris = e["tris"][tlist]
        used, inv = np.unique(tris.reshape(-1), return_inverse=True)
        n = len(used)
        raw = np.zeros((n, 32), np.uint8)
        q = np.clip(np.rint(pos[used] / UNIT), -32767, 32767).astype("<i2")
        p4 = np.concatenate([q, np.full((n, 1), 32767, "<i2")], 1)
        raw[:, :8] = np.ascontiguousarray(p4).view(np.uint8).reshape(n, 8)
        raw[:, 8:11], raw[:, 11] = _unit8(nn[used]), 255
        raw[:, 12:15], raw[:, 15] = _unit8(tan[used]), 255
        raw[:, 16:19], raw[:, 19] = _unit8(bn[used]), 255
        raw[:, 20:24] = np.ascontiguousarray(np.clip(np.rint(uv[used] * UVS), -32768, 32767).astype("<i2")).view(np.uint8).reshape(n, 4)
        raw[:, 24:28] = np.vectorize(lambda b: slot.get(int(b), 0))(j[used]).astype(np.uint8)
        raw[:, 28:32] = wb[used].astype(np.uint8)
        vb.append(raw.tobytes())
        loc = inv.reshape(-1, 3) + first
        prims.append(dict(MinIndex=first, IsUsingDepthOnlyBuffers=0, NumVertices=n, StartIndex=sum(len(x) for x in ib) // 2,
                          PrimitiveCount=len(loc), Type=1))
        ib.append(loc.astype("<u2" if first + n <= 0xFFFF else "<u4").reshape(-1).tobytes())
        insts_spec.append((uid, table, n))
        first += n
        report["vertices"] += n
        report["triangles"] += len(loc)
    if first > 0xFFFF:
        raise MeshEditError(f"{first} vertices: more than 65,535 in one part")
    vbb, ibb = b"".join(vb), b"".join(ib)
    md["VertexBufferData"] = [vbb[i:i + 1] for i in range(len(vbb))]
    md["IndexBufferData"] = [ibb[i:i + 1] for i in range(len(ibb))]
    md["IsIndexBuffer32bit"] = b"\x00"

    def prim_list(template, specs):
        out = []
        for spec in specs:
            p = copy.deepcopy(template)
            po = M._obj(p)
            for k, v in spec.items():
                po.fields[k] = struct.pack("<i", v)
            out.append(p)
        return out
    md["StandardPrimitives"] = prim_list(md["StandardPrimitives"][0], prims)
    md["ShadowPrimitives"] = prim_list((md["ShadowPrimitives"] or md["StandardPrimitives"])[0], prims)
    inst_t = cm.fields["InstancingData"][0]
    insts = []
    for uid, table, n in insts_spec:
        p = copy.deepcopy(inst_t)
        po = M._obj(p)
        po.fields["NumBones"] = bytes([len(table)])
        po.fields["HwBoneToMeshBoneTable"] = [bytes([b]) for b in (table + [0] * TABLE)[:TABLE]]
        po.fields["NumVertices"] = struct.pack("<H", n & 0xFF)
        po.fields["SubMeshIndex"] = b"\x00"
        po.fields["Material"].link = struct.pack("<I", uid)
        insts.append(p)
    cm.fields["InstancingData"] = insts
    ref_t = o.fields["CompiledMeshMaterials"][0]
    refs = []
    for uid, _t, _n in insts_spec:
        r = copy.deepcopy(ref_t)
        r.id = struct.pack("<I", uid)
        refs.append(r)
    o.fields["CompiledMeshMaterials"] = refs
    return Cd.encode(root), report


def mesh_bodies(part_file, glb, lods="all", objs=None):
    """The part's meshes with the edited geometry -> ({mesh object name: new body}, report). lods: "all" - every LOD
    gets the edit (a far LOD of the old shape would pop in), "lod0" - only LOD0. Each body is decoded back and compared
    with the edit (positions within a quantum, the triangles, the weights) and checked by skinmesh.verify (forgelib)."""
    if objs is None:
        _a, objs = part_objects(part_file)
    meshes = lod_meshes(objs, part_file)
    m0 = read_mesh(meshes[0]["body"])
    if m0["cloth"]:
        raise MeshEditError(f"{part_file} is a cloth mesh (simulated in game): not editable yet")
    palette = [b for b, _m, _t in m0["palette"]]
    materials = {s["material"] for s in m0["subs"]}
    e = read_edit(glb, palette, materials)
    e["bones"] = np.asarray(palette, np.int64)[e["joints"]]
    # each bone's bind origin (skeleton frame: -M^T t), for far LODs lacking a bone and its ancestors
    e["origins"] = {}
    for b, Mb, t in m0["palette"]:
        e["origins"].setdefault(b, -Mb.T @ t)
    parent = rig_parents(persona_of(part_file), part_file)
    out, report = {}, None
    import skinmesh
    for k, mo in enumerate(meshes if lods == "all" else meshes[:1]):
        body, rep = build_body(mo["body"], e, parent)
        back = read_mesh(body)
        if [b for b, _m, _t in back["palette"]] != [b for b, _m, _t in read_mesh(mo["body"])["palette"]]:
            raise MeshEditError(f"{mo['name']}: the palette changed")
        if back["pos"].shape[0] != rep["vertices"] or sum(len(s["tris"]) for s in back["subs"]) != rep["triangles"]:
            raise MeshEditError(f"{mo['name']}: read back {back['pos'].shape[0]} vertices")
        bad = skinmesh.verify(body)
        if bad:
            raise MeshEditError(f"{mo['name']}: {'; '.join(bad)}")
        out[mo["name"]] = body
        if report is None:
            report = rep
        report.setdefault("far_lods", []).append(dict(lod=mo["name"], bones_folded=rep["bones_folded"],
                                                      sub_meshes=rep["sub_meshes"]))
    report["lods"] = list(out)
    report["materials"] = len(materials)
    return out, report


def patched_objects(objs, bodies):
    """The part file's objects with the meshes' bodies replaced (for the viewer's preview)."""
    return [dict(o, body=bodies[o["name"]], body_size=len(bodies[o["name"]]) - 1) if o["name"] in bodies else o
            for o in objs]


def main(argv):
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    log = (lambda m: print(m, file=sys.stderr, flush=True)) if as_json else print
    if argv and argv[0] == "export":
        res = export(int(argv[1]), argv[2], log=log)
    elif argv and argv[0] == "check":
        bodies, rep = mesh_bodies(argv[1], argv[2], argv[3] if len(argv) > 3 else "all")
        res = dict(rep, sizes={k: len(v) for k, v in bodies.items()})
    else:
        print(__doc__)
        return 0
    print(json.dumps(res) if as_json else json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
