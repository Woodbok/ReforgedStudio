"""Writing AC3 static meshes (vertex formats 3 and 4), the basis of new visible models.

Vertex layout, measured on Fort Wolcott's 1,034 static meshes:

    +0   int16 x, y, z, w   position = xyz * |w| / 8 / 32767 metres; |w| = 8 * (floor(max |coord|) + 1), one per
                            mesh (1034 of 1034); sign(w) = -handedness: w < 0 when the UV bitangent runs along
                            normal x tangent (99.9% format 3, 99.2% format 4)
    +8   u8 nx, ny, nz, a   normal, each (b - 127.5) / 127.5 (mean dot with the geometric normal 0.96-0.97);
                            a = the two-layer blend weight (a / 255: 0 = layer 1, 255 = layer 2 - the AC3MP_Tex2_*
                            vertex shaders pass normal.w / 255 to the pixel shader's blend)
    +12  u8 tx, ty, tz, b   tangent along +u, same packing (0.96-0.99); a / b mostly 0xff, equal in 75% of meshes
    +16  u8 r, g, b, a      format 3 only: vertex colour (mostly white)
    +16 / +20  int16 u, v   UV / 2048 (format 4 / format 3)

A Mesh's CompiledMesh holds one StandardPrimitive (triangle list: MinIndex = first vertex of its range, NumVertices,
StartIndex, PrimitiveCount, Type 1), one InstancingData block (NumVertices = the primitive's vertex count's LOW BYTE,
SubMeshIndex k, Material) and one CompiledMeshMaterials entry per material; ShadowPrimitives usually repeat the
standard ones.
"""
import struct

import numpy as np

STRIDE = {3: 24, 4: 20}
UV_AT = {3: 20, 4: 16}


def decode_vertices(vb, fmt):
    """Raw vertex bytes -> dict of every field (the exact quantities stored)."""
    st = STRIDE[fmt]
    n = len(vb) // st
    raw = np.frombuffer(vb[:n * st], np.uint8).reshape(n, st)
    p = raw[:, :8].copy().view("<i2").reshape(n, 4)
    out = {
        "q": p[:, :3].copy(), "w": p[:, 3].copy(),
        "n": raw[:, 8:11].copy(), "a": raw[:, 11].copy(),
        "t": raw[:, 12:15].copy(), "b": raw[:, 15].copy(),
        "uvq": raw[:, UV_AT[fmt]:UV_AT[fmt] + 4].copy().view("<i2").reshape(n, 2),
    }
    if fmt == 3:
        out["color"] = raw[:, 16:20].copy()
    return out


def pack_vertices(f, fmt):
    """The inverse of decode_vertices: dict -> raw vertex bytes."""
    st = STRIDE[fmt]
    n = len(f["q"])
    raw = np.zeros((n, st), np.uint8)
    pos = np.ascontiguousarray(np.concatenate([f["q"].astype("<i2"), f["w"].astype("<i2")[:, None]], 1))
    raw[:, :8] = pos.view(np.uint8).reshape(n, 8)
    raw[:, 8:11], raw[:, 11] = f["n"], f["a"]
    raw[:, 12:15], raw[:, 15] = f["t"], f["b"]
    raw[:, UV_AT[fmt]:UV_AT[fmt] + 4] = np.ascontiguousarray(f["uvq"].astype("<i2")).view(np.uint8).reshape(n, 4)
    if fmt == 3:
        raw[:, 16:20] = f["color"]
    return raw.tobytes()


def unit8(v):
    """Unit vectors -> the stored bytes."""
    return np.clip(np.rint(np.asarray(v, dtype=np.float64) * 127.5 + 127.5), 0, 255).astype(np.uint8)


def frame(pos, tris, uv, normals=None):
    """Area-weighted vertex normals, tangents along +u, and handedness (+1: bitangent = n x t). normals: (n, 3) the
    caller's own (e.g. a ported mesh's stored ones) - each row used instead of the computed normal, a zero row
    computed (a ported mesh's chunk-edge skirts share the street's edge vertices: the area average tilts them 20 - 70
    degrees outward and the sun lights two neighbouring pieces differently along their join)."""
    pos, uv = np.asarray(pos, np.float64), np.asarray(uv, np.float64)
    a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    e1, e2 = b - a, c - a
    fn = np.cross(e1, e2)
    du1, dv1 = (uv[tris[:, 1]] - uv[tris[:, 0]]).T
    du2, dv2 = (uv[tris[:, 2]] - uv[tris[:, 0]]).T
    det = du1 * dv2 - du2 * dv1
    ok = np.abs(det) > 1e-12
    t, bt = np.zeros_like(fn), np.zeros_like(fn)
    t[ok] = (e1[ok] * dv2[ok, None] - e2[ok] * dv1[ok, None]) / det[ok, None]
    bt[ok] = (e2[ok] * du1[ok, None] - e1[ok] * du2[ok, None]) / det[ok, None]
    vn, vt, vb = (np.zeros_like(pos) for _ in range(3))
    area = np.linalg.norm(fn, axis=1)[:, None]
    for k in range(3):
        np.add.at(vn, tris[:, k], fn)
        np.add.at(vt, tris[:, k], t * area)
        np.add.at(vb, tris[:, k], bt * area)
    unit = lambda x: x / np.maximum(np.linalg.norm(x, axis=1), 1e-12)[:, None]  # noqa: E731
    vn = unit(vn)
    if normals is not None:
        given = np.asarray(normals, np.float64)
        ln = np.linalg.norm(given, axis=1)
        use = ln > 0.5
        vn[use] = given[use] / ln[use, None]
    vt = unit(vt - vn * (vt * vn).sum(1)[:, None])          # Gram-Schmidt against the normal
    hand = np.where((np.cross(vn, vt) * vb).sum(1) >= 0, 1, -1)
    return vn, vt, hand


def quantise(pos, uv, normals, tangents, hand, fmt, color=None, blend=None):
    """Float geometry -> the stored-field dict pack_vertices takes. blend: (n,) the second layer's weight per vertex,
    0..1 (or uint8 0..255), into byte `a` (None: 255)."""
    pos = np.asarray(pos, np.float64)
    w = 8 * (int(np.floor(np.abs(pos).max())) + 1)
    s = w / 8.0 / 32767.0
    f = {
        "q": np.clip(np.rint(pos / s), -32767, 32767).astype(np.int16),
        "w": (-np.asarray(hand) * w).astype(np.int16),
        "n": unit8(normals), "a": np.full(len(pos), 255, np.uint8) if blend is None else blend_bytes(blend),
        "t": unit8(tangents), "b": np.full(len(pos), 255, np.uint8),
        "uvq": np.clip(np.rint(np.asarray(uv, np.float64) * 2048.0), -32768, 32767).astype(np.int16),
    }
    if fmt == 3:
        f["color"] = np.full((len(pos), 4), 255, np.uint8) if color is None else np.asarray(color, np.uint8)
    return f


def blend_bytes(blend):
    """Blend weights 0..1 (floats) or 0..255 (uint8) -> the stored bytes."""
    b = np.asarray(blend)
    if b.dtype == np.uint8:
        return b.copy()
    return np.clip(np.rint(b.astype(np.float64) * 255.0), 0, 255).astype(np.uint8)


def primitives(tri_groups):
    """[(vertex start, vertex count, triangles (m, 3) with ABSOLUTE indices)] in vertex order -> (index bytes,
    [dict(MinIndex, IsUsingDepthOnlyBuffers, NumVertices, StartIndex, PrimitiveCount, Type)])."""
    idx, prims, start = [], [], 0
    for v0, nv, tris in tri_groups:
        t = np.asarray(tris, dtype=np.int64).reshape(-1)
        idx.append(t)
        prims.append({"MinIndex": v0, "IsUsingDepthOnlyBuffers": 0, "NumVertices": nv, "StartIndex": start,
                      "PrimitiveCount": len(t) // 3, "Type": 1})
        start += len(t)
    allidx = np.concatenate(idx) if idx else np.zeros(0, np.int64)
    wide = allidx.size and allidx.max() > 0xFFFF
    return allidx.astype("<u4" if wide else "<u2").tobytes(), prims, bool(wide)


def set_mesh(mesh_obj, vb, ib, wide, fmt, prims, shadow_prims, materials):
    """Fill a (deep-copied template) Mesh Obj: buffers, primitives, instancing blocks and materials. `materials`:
    one material id (4 bytes) per primitive."""
    import copy
    from ac3codec import Ptr, Ref
    cm = mesh_obj.fields["CompiledMesh"].obj if isinstance(mesh_obj.fields["CompiledMesh"], Ptr) else \
        mesh_obj.fields["CompiledMesh"]
    md = cm.fields["MeshData"].fields
    md["VertexFormat"] = bytes([fmt])
    md["VertexStride"] = bytes([STRIDE[fmt]])
    md["IsIndexBuffer32bit"] = bytes([1 if wide else 0])
    md["VertexBufferData"] = [vb[i:i + 1] for i in range(len(vb))]
    md["IndexBufferData"] = [ib[i:i + 1] for i in range(len(ib))]

    def prim_list(template, specs):
        out = []
        for g in specs:
            p = copy.deepcopy(template)
            o = p.obj if hasattr(p, "obj") and p.obj is not None else p
            for k, v in g.items():
                o.fields[k] = struct.pack("<i", v)
            out.append(p)
        return out

    md["StandardPrimitives"] = prim_list(md["StandardPrimitives"][0], prims)
    md["ShadowPrimitives"] = prim_list((md["ShadowPrimitives"] or md["StandardPrimitives"])[0], shadow_prims)
    inst_t = cm.fields["InstancingData"][0]
    insts = []
    for k, (g, mat) in enumerate(zip(prims, materials)):
        p = copy.deepcopy(inst_t)
        o = p.obj if hasattr(p, "obj") and p.obj is not None else p
        o.fields["NumVertices"] = struct.pack("<H", g["NumVertices"] & 0xFF)
        o.fields["SubMeshIndex"] = bytes([k])
        o.fields["Material"].link = mat
        insts.append(p)
    cm.fields["InstancingData"] = insts
    ref_t = mesh_obj.fields["CompiledMeshMaterials"][0]
    mats = []
    for mat in materials:
        r = copy.deepcopy(ref_t)
        r.id = mat
        mats.append(r)
    mesh_obj.fields["CompiledMeshMaterials"] = mats
    return mesh_obj
