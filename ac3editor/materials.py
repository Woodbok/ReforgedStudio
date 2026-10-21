"""Textures: new materials made from your own images, and giving map objects other materials.

Measured on Fort Wolcott:

* Each object's Visual names its materials per LOD in `MaterialInfos` (MeshMaterial = the mesh's own, InstanceMaterial
  = what this object draws with). Designers override per object (540 of 5293 infos: snowy wood vs `..._NOSNOW`), so
  a swap sets InstanceMaterial where MeshMaterial is the one being replaced - the mesh is untouched.
* A Material names a MaterialTemplate (the shader) and a TextureSet whose `Maps` slot 0 is the colour map
  (`..._DiffuseMap`, DXT1 / DXT5 / DXT3), slot 1 the normal map (plain RGB, flat = (128, 128, 255), DXT1), slot 2 a
  specular map (6 sets). The main templates draw both static vertex formats (3 and 4), so a material can go on any
  static mesh whose format its template is seen with in the map.
* A TextureMap body (forgelib's dxt.py): a 90-byte header (uid, width, height, format, mip count, chain size), the mip
  chain, a 61-byte tail - no other ids. A new one copies a stock map of the same role, size and format (header and
  tail kept) with our pixels encoded in its place.
* Cells mostly list resources stored in their own record (resources are duplicated per cell), the rest in the World
  record or another (often a parent) cell: new objects are defined once, in the base material's record, and listed
  in each cell that uses them, with the material's TextureSet, its maps and its template (as stock cells list them:
  294 of 321 maps).
* A Material's DynamicProperties are its shader's parameters, keyed by the CRC32 of the shader's own names (PARAM):
  texture parameters (TextureSpecificationMethod 0 = the material's TextureSet, map MapType; 1 = the named
  TextureSet's; 2 = the named texture), colours, scales. Two-layer ground (`*_Multi`, shaders `AC3MP_Tex2_*`) keeps
  its SECOND layer only there: `Layer1_Diffuse` / `Layer1_Normal`.
"""
import copy
import struct
import sys
import zlib

import numpy as np

from . import mapdata as M  # first: puts forgelib and ac3codec on the path
from . import duplicate
from ac3codec import Ref  # noqa: E402
from .newmodel import _comp, _renew
from datafile import compress, decompress  # noqa: E402  (forgelib)
from forge_write import ArchiveImage, set_payload, write  # noqa: E402  (forgelib)
from transplant import object_at  # noqa: E402  (forgelib)

import dxt  # noqa: E402  (forgelib: headers, decoding to check)


def resize(rgba, w, h):
    """(H, W, 4) uint8, top row first -> (h, w, 4): box-filtered down, bilinear up."""
    a = np.asarray(rgba, np.float64)
    H, W = a.shape[:2]
    # box-filter down by whole factors first (keeps detail from aliasing)
    while H >= 2 * h and W >= 2 * w and H % 2 == 0 and W % 2 == 0:
        a = (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) / 4
        H, W = a.shape[:2]
    if (H, W) != (h, w):
        y = (np.arange(h) + 0.5) * H / h - 0.5
        x = (np.arange(w) + 0.5) * W / w - 0.5
        y0 = np.clip(np.floor(y).astype(int), 0, H - 1)
        x0 = np.clip(np.floor(x).astype(int), 0, W - 1)
        y1, x1 = np.clip(y0 + 1, 0, H - 1), np.clip(x0 + 1, 0, W - 1)
        fy = np.clip(y - y0, 0, 1)[:, None, None]
        fx = np.clip(x - x0, 0, 1)[None, :, None]
        a = (a[y0][:, x0] * (1 - fy) * (1 - fx) + a[y0][:, x1] * (1 - fy) * fx
             + a[y1][:, x0] * fy * (1 - fx) + a[y1][:, x1] * fy * fx)
    return np.clip(np.rint(a), 0, 255).astype(np.uint8)


def _blocks(px):
    """(h, w, c) -> (h/4 * w/4, 16, c) 4x4 blocks, row-major blocks and pixels (edges padded)."""
    h, w, c = px.shape
    H, W = (h + 3) // 4 * 4, (w + 3) // 4 * 4
    if (H, W) != (h, w):
        px = np.pad(px, ((0, H - h), (0, W - w), (0, 0)), mode="edge")
    return px.reshape(H // 4, 4, W // 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(-1, 16, c)


def _565(c):
    c = np.clip(np.rint(c), 0, 255).astype(np.int64)
    return ((c[..., 0] * 31 + 127) // 255) << 11 | ((c[..., 1] * 63 + 127) // 255) << 5 | ((c[..., 2] * 31 + 127) // 255)


def _unpack565(v):
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.float64)


def encode_colour(px):
    """(h, w, 3) -> DXT1 colour blocks (8 bytes each): endpoints at the block's extremes along its main colour axis,
    always 4-colour mode (c0 > c1)."""
    B = _blocks(np.asarray(px, np.float64))                      # (n, 16, 3)
    mean = B.mean(1, keepdims=True)
    d = B - mean
    cov = np.einsum("npi,npj->nij", d, d)
    axis = np.ones((len(B), 3)) / np.sqrt(3)
    for _ in range(4):                                           # power iteration: the main axis
        axis = np.einsum("nij,nj->ni", cov, axis)
        axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-9)
    t = np.einsum("npi,ni->np", d, axis)
    hi = mean[:, 0] + axis * t.max(1, keepdims=True)
    lo = mean[:, 0] + axis * t.min(1, keepdims=True)
    c0, c1 = _565(hi), _565(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    same = c0 == c1
    c0 = np.where(same & (c0 < 0xFFFF), c0 + 1, c0)              # keep 4-colour mode (c0 > c1)
    c1 = np.where(same & (c0 == 0xFFFF) & (c1 == 0xFFFF), 0xFFFE, c1)
    p0, p1 = _unpack565(c0), _unpack565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)        # (n, 4, 3)
    idx = ((B[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1).argmin(-1)  # (n, 16)
    bits = (idx.astype(np.uint64) << (2 * np.arange(16, dtype=np.uint64))).sum(1)
    out = np.zeros((len(B), 8), np.uint8)
    out[:, 0:2] = c0.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 2:4] = c1.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 4:8] = bits.astype("<u4").view(np.uint8).reshape(-1, 4)
    return out


def encode_colour_cutout(px, a, cut=128):
    """(h, w, 3) colour + (h, w) alpha -> DXT1 blocks with 1-bit alpha: a block with a pixel below `cut` is written in
    3-colour mode (c0 <= c1: palette c0, c1, their mean, TRANSPARENT), its see-through pixels on index 3; every other
    block as encode_colour. (Cut-outs written in 4-colour mode come out opaque.)"""
    out = encode_colour(px)
    A = _blocks(np.asarray(a, np.float64)[:, :, None])[:, :, 0]
    holes = (A < cut).any(1)
    if not holes.any():
        return out
    B = _blocks(np.asarray(px, np.float64))[holes]
    clear = A[holes] < cut
    opaque = ~clear
    w = opaque[:, :, None].astype(np.float64)
    n = np.maximum(w.sum(1), 1)
    mean = (B * w).sum(1) / n                                    # (k, 3): the opaque pixels' mean / main axis
    d = (B - mean[:, None, :]) * w
    cov = np.einsum("npi,npj->nij", d, d)
    axis = np.ones((len(B), 3)) / np.sqrt(3)
    for _ in range(4):
        axis = np.einsum("nij,nj->ni", cov, axis)
        axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-9)
    t = np.einsum("npi,ni->np", B - mean[:, None, :], axis)
    t = np.where(opaque, t, 0.0)
    hi = mean + axis * t.max(1, keepdims=True)
    lo = mean + axis * t.min(1, keepdims=True)
    c0, c1 = _565(hi), _565(lo)
    c0, c1 = np.minimum(c0, c1), np.maximum(c0, c1)              # 3-colour mode: c0 <= c1
    p0, p1 = _unpack565(c0), _unpack565(c1)
    pal = np.stack([p0, p1, (p0 + p1) / 2], 1)                   # (k, 3, 3)
    idx = ((B[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1).argmin(-1)
    idx = np.where(clear, 3, idx)
    bits = (idx.astype(np.uint64) << (2 * np.arange(16, dtype=np.uint64))).sum(1)
    sub = np.zeros((len(B), 8), np.uint8)
    sub[:, 0:2] = c0.astype("<u2").view(np.uint8).reshape(-1, 2)
    sub[:, 2:4] = c1.astype("<u2").view(np.uint8).reshape(-1, 2)
    sub[:, 4:8] = bits.astype("<u4").view(np.uint8).reshape(-1, 4)
    out[holes] = sub
    return out


def encode_alpha5(a):
    """(h, w) alpha -> DXT5 alpha blocks (8 bytes): a0 = max > a1 = min, 8-level palette."""
    A = _blocks(np.asarray(a, np.float64)[:, :, None])[:, :, 0]
    a0, a1 = np.rint(A.max(1)).astype(np.int64), np.rint(A.min(1)).astype(np.int64)
    a1 = np.where(a0 == a1, np.maximum(a0 - 1, 0), a1)
    a0 = np.where(a0 == a1, a1 + 1, a0)
    lv = np.stack([a0, a1] + [((7 - k) * a0 + k * a1) / 7 for k in range(1, 7)], 1).astype(np.float64)
    idx = np.abs(A[:, :, None] - lv[:, None, :]).argmin(-1).astype(np.uint64)
    bits = (idx << (3 * np.arange(16, dtype=np.uint64))).sum(1)
    out = np.zeros((len(A), 8), np.uint8)
    out[:, 0], out[:, 1] = a0, a1
    for k in range(6):
        out[:, 2 + k] = (bits >> np.uint64(8 * k)) & np.uint64(0xFF)
    return out


def encode_alpha3(a):
    """(h, w) alpha -> DXT3 explicit 4-bit alpha blocks (8 bytes)."""
    A = np.rint(_blocks(np.asarray(a, np.float64)[:, :, None])[:, :, 0] / 17).astype(np.uint64)
    bits = (A << (4 * np.arange(16, dtype=np.uint64))).sum(1)
    return bits.astype("<u8").view(np.uint8).reshape(-1, 8)


def mip_chain(px, fmt, levels):
    """(h, w, 4) uint8 -> the DXT mip chain (format 2 / 3 = DXT1, 4 = DXT3, 5 = DXT5), box-filtered levels."""
    a = np.asarray(px, np.float64)
    out = bytearray()
    cutout = fmt in (2, 3) and (a[:, :, 3] < 128).any()        # DXT1 with see-through pixels: 1-bit alpha
    for _ in range(levels):
        col = encode_colour_cutout(a[:, :, :3], a[:, :, 3]) if cutout else encode_colour(a[:, :, :3])
        if fmt in (2, 3):
            out += col.tobytes()
        else:
            al = (encode_alpha5 if fmt == 5 else encode_alpha3)(a[:, :, 3])
            out += np.concatenate([al, col], 1).tobytes()
        h, w = a.shape[:2]
        if h > 1 or w > 1:
            if h % 2:
                a = np.concatenate([a, a[-1:]], 0)
            if w % 2:
                a = np.concatenate([a, a[:, -1:]], 1)
            a = (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) / 4 if h > 1 and w > 1 else                 (a[0::2] + a[1::2]) / 2 if h > 1 else (a[:, 0::2] + a[:, 1::2]) / 2
    return bytes(out)


def texture_body(donor, rgba):
    """A stock TextureMap body + an image (H, W, 4) uint8 -> a body of the same size and format with the image
    (resized to the donor's size) in its mip chain. The uid is the donor's; the caller renews it."""
    hd = dxt.header(donor)
    w, h, fmt, mips = hd["width"], hd["height"], hd["format"], max(hd["mips"], 1)
    px = resize(rgba, w, h)
    if fmt not in (2, 3, 4, 5):
        raise ValueError(f"texture format {fmt} can't be written")
    chain = mip_chain(px, fmt, mips)
    if len(chain) != hd["size"]:
        raise ValueError(f"mip chain {len(chain)} bytes, the donor's is {hd['size']}")
    return donor[:90] + bytes(chain) + donor[90 + hd["size"]:]


def normal_map(img, green="+y", two_channel=None):
    """A tangent-space normal map in AC3's convention -> (H, W, 4) uint8. AC3's: plain RGB, GREEN UP (+y = the picture's
    up: on 60 stock textures where it shows, green tracks brightness rising down the picture with the opposite sign to
    red and rising right); Unity's is the same, DirectX / Unreal's (Dishonored) has green
    flipped: green="-y". two_channel (BC5 / Unity's RG maps, blue rebuilt as sqrt(1 - x2 - y2)): None = when blue is
    near zero everywhere. Every texel renormalised."""
    a = np.asarray(img)
    if a.dtype != np.uint8:
        a = np.clip(np.rint(a * (255.0 if a.max() <= 1.0 else 1.0)), 0, 255).astype(np.uint8)
    rgb = a[:, :, :3].astype(np.float64) / 127.5 - 1.0
    if two_channel is None:
        two_channel = a.shape[2] < 3 or float(a[:, :, 2].mean()) < 30
    if green == "-y":
        rgb[:, :, 1] = -rgb[:, :, 1]
    if two_channel:
        rgb[:, :, 2] = np.sqrt(np.clip(1.0 - rgb[:, :, 0] ** 2 - rgb[:, :, 1] ** 2, 0.0, 1.0))
    rgb /= np.maximum(np.linalg.norm(rgb, axis=2, keepdims=True), 1e-6)
    out = np.empty(a.shape[:2] + (4,), np.uint8)
    out[:, :, :3] = np.clip(np.rint((rgb + 1.0) * 127.5), 0, 255)
    out[:, :, 3] = 255
    return out


def flat_normal(w, h):
    return np.tile(np.array([128, 128, 255, 255], np.uint8), (h, w, 1))


# shader parameters by name (DynamicProperties keys = CRC32 of the names in the shaders' constant tables; measured on
# Boston Harbour's AC3MP_Tex2_* / Tex1 templates)
PARAM = {n: zlib.crc32(n.encode()) for n in (
    "Layer0_Diffuse", "Layer0_Normal", "Layer0_Color", "Layer0_UVScale",
    "Layer1_Diffuse", "Layer1_Normal", "Layer1_Color", "Layer1_UVScale",
    "PixelBlenderScale", "PixelBlenderBias")}
NO_SET = (3, 0, bytes(4))                           # the null TextureSet reference of a method-2 texture parameter


def param_index(mat_obj, name):
    """-> index of a shader parameter in a Material's DynamicProperties, or None."""
    h = PARAM[name]
    for i, d in enumerate(getattr(mat_obj, "dyn", None) or ()):
        if d[0] == h:
            return i
    return None


def param_value(mat_obj, name):
    i = param_index(mat_obj, name)
    return None if i is None else mat_obj.dyn[i][3]


def set_param_floats(mat_obj, name, values):
    """Overwrite the first len(values) floats of a float / float4 parameter (kept as it is if absent). -> done?"""
    i = param_index(mat_obj, name)
    if i is None:
        return False
    n_, oh, bits, old = mat_obj.dyn[i]
    vals = list(struct.unpack(f"<{len(old) // 4}f", old))
    vals[:len(values)] = [float(v) for v in values]
    mat_obj.dyn[i] = (n_, oh, bits, struct.pack(f"<{len(vals)}f", *vals))
    return True


def two_layer(mat_obj):
    """A two-layer (blended) material: its shader has a second colour map (AC3MP_Tex2_*: `*_Multi` ground)."""
    return param_index(mat_obj, "Layer1_Diffuse") is not None


def texture_params(mat_obj):
    """-> [(method, map type, TextureSet uid or None, texture uid or None)] of a Material's texture parameters."""
    out = []
    for d in getattr(mat_obj, "dyn", None) or ():
        v = d[3]
        f = getattr(v, "fields", None)
        if not f or "TextureSpecificationMethod" not in f:
            continue
        ts, tx = f["TextureSet"].id, f["Texture"].id
        out.append((f["TextureSpecificationMethod"][0], f["MapType"][0],
                    ts if ts and ts != bytes(4) else None, tx if tx and tx != bytes(4) else None))
    return out


def param_deps(mat_obj, set_maps):
    """Every TextureSet / texture a material's shader parameters name (with the sets' maps) - a cell listing the
    material lists these too (a two-layer material's second layer is named only here)."""
    out = []
    for _m, _t, ts, tx in texture_params(mat_obj):
        if ts is not None:
            out += [ts] + [u for u in set_maps.get(ts, []) if u and u != bytes(4)]
        if tx is not None:
            out.append(tx)
    return list(dict.fromkeys(out))


def param_texture(mat_obj, name, set_maps):
    """-> the texture uid a texture parameter draws (method 0: the material's own set, 1: the named set, map MapType;
    2: the named texture), or None."""
    v = param_value(mat_obj, name)
    f = getattr(v, "fields", None)
    if not f or "TextureSpecificationMethod" not in f:
        return None
    meth, mtype = f["TextureSpecificationMethod"][0], f["MapType"][0]
    ts = mat_obj.fields["TextureSet"].id if meth == 0 else f["TextureSet"].id if meth == 1 else None
    maps = set_maps.get(ts, []) if ts is not None else []
    if meth in (0, 1) and mtype < len(maps) and maps[mtype] and maps[mtype] != bytes(4):
        return maps[mtype]
    tx = f["Texture"].id
    return tx if tx and tx != bytes(4) else None


def point_param(mat_obj, name, texture, texture_set=None):
    """Make a texture parameter draw `texture`: from `texture_set` (method 1) when given, else directly (method 2, as
    stock's WoodPlanksDirty / Mud normal layers). -> done?"""
    v = param_value(mat_obj, name)
    f = getattr(v, "fields", None)
    if not f or "TextureSpecificationMethod" not in f:
        return False
    f["TextureSpecificationMethod"] = struct.pack("<I", 1 if texture_set is not None else 2)
    f["TextureSet"] = Ref(1, 0, texture_set) if texture_set is not None else Ref(*NO_SET)
    f["Texture"].id = texture
    return True


LOCAL = ("Material", "TextureSet", "TextureMap")     # stored in every cell record that lists them (stock: 621
                                                     # TextureMap objects for 230 ids); kept in one cell only, the
                                                     # other cells' objects draw magenta and a new block isn't drawn


def local_copies(uids, stored, obj_bytes):
    """[(serial, object bytes)] of each uid in `uids` that a record must hold a copy of (a LOCAL class, known in
    `obj_bytes`, not yet in `stored` - which this updates)."""
    out = []
    for u in dict.fromkeys(uids):
        if u in obj_bytes and u not in stored:
            out.append(obj_bytes[u])
            stored.add(u)
    return out


def apply(src, dest, swaps=None, new=None, id_avoid=(), log=print, far=True):
    """swaps: {entity uid (top-level or in a group, in a map cell): {mesh material uid: new material uid or a key of
    `new`}}. new: {key: dict(name, base (a material uid of this map), rgba (H, W, 4) uint8 top row first, normal ((H,
    W, 4) uint8 in AC3's convention - normal_map; None: flat), alpha_test / two_sided (None: the base's))} - each a
    new Material + TextureSet + colour map (the base's, re-imaged) + a flat normal map, other maps the base's.
    Two layers (a two-layer base: a `*_Multi` material, shader AC3MP_Tex2_*): rgba2 = the SECOND layer's colour
    (same convention as rgba), normal2 (None: flat), both new maps the material names directly; blend_scale /
    blend_bias = the shader's PixelBlenderScale / Bias (None: the base's - stock 4..32, a hard edge; 0 / 0: the
    layers fade linearly with the vertex weight), uv_scale2 = (u, v) repeats of layer 2 per layer-1 repeat (None:
    the base's, stock 1, 1). With rgba2 the second layer's tint (Layer1_Color) is set to the first's, so both
    pictures are drawn alike. The alpha of rgba / rgba2 is each layer's HEIGHT for the blend (255 everywhere: the
    weights alone decide). Per-vertex weights: newmodel.add_models `blend` (0 = layer 1 only, 1 = layer 2 only).
    Shared maps (no new textures - a blend of two pictures other materials already draw): share1 = a material (a key
    of `new` or a uid of this map) whose colour / normal maps are layer 1's; share2 = the same for layer 2 (a
    two-layer base). layer_color = (r, g, b): both layers' tint (Layer0_Color / Layer1_Color; 0.5 = none, as the
    one-layer AC3MP_Tex1 bases) - stock *_Multi bases carry darker tints (0.27 - 0.48). params = {name in PARAM:
    floats} - any of the shader's float parameters, written after everything above (a ported material's own values).
    far: a swapped object's far level (LOD2: the atlas model, one atlas material, 40 - 80 m) shows its LOD1 model
    with the new materials instead - its own copy of its LODSelector (as new models, which show their model at
    every level), so it doesn't keep its old look at a distance; beyond that the map's merged far meshes (FakeMesh)
    still show it as it was.
    -> ({key: new material uid}, same) - same: every record we didn't mean to touch is byte-identical."""
    swaps, new = dict(swaps or {}), dict(new or {})
    S = M.schema()
    C = M.Codec(S)
    cls = {S.name_of(h): h for h in S.types_by_hash}
    bulk = {cls[n] for n in ("TextureMap", "Animation", "MaterialTemplate", "FX", "NavMeshManager") if n in cls}
    image = ArchiveImage.read(src)
    with M.Forge(src) as f:
        raw = {r.index: f.read(r) for r in f.records}
        recs = [r for r in f.records if r.name != "GlobalMetaFile" and len(raw[r.index]) > 8]
    payloads = {r.index: decompress(M.mapwalk.split_prefix(raw[r.index])[1]) for r in recs}
    walked = {i: M.mapwalk.walk(d) for i, d in payloads.items()}

    # 1. the map: ids, materials, sets, texture bodies, mesh formats per template, where entities and materials live
    idents, mats, sets, tex, names, where = [], {}, {}, {}, {}, {}
    mesh_fmt, mesh_mats, cells, ent_rec = {}, {}, {}, {}
    sels, ent_sel = {}, {}                     # LODSelector uid -> decoded; entity uid -> its Visual's selector
    stored, obj_bytes = {}, {}                 # record index -> uids it holds; uid -> (serial, bytes) of a LOCAL one
    for rec in recs:
        d = payloads[rec.index]
        for off, size, serial, _f in walked[rec.index][0]:
            o = object_at(d, off, size)
            b = o["body"]
            uid = b[2:6] if b[:2] == M.UID_MARK else None
            cname = S.name_of(o["type"])
            names[uid] = o["name"]
            where.setdefault(uid, rec.index)
            stored.setdefault(rec.index, set()).add(uid)
            if cname in LOCAL and uid not in obj_bytes:
                obj_bytes[uid] = (serial, M.mapwalk.object_bytes(o))
            if cname == "TextureMap":
                tex[uid] = b
                idents.append((uid, None))
                continue
            dec = None
            if o["type"] not in bulk:
                try:
                    dec = C.decode(b)
                except Exception:  # noqa: BLE001
                    dec = None
            idents.append((uid, dec.obj if dec else None))
            if dec is None:
                continue
            if cname == "Material":
                mats[uid] = dec
            elif cname == "TextureSet":
                sets[uid] = dec
            elif cname == "Mesh":
                mesh_fmt[uid] = M._obj(dec.obj.fields["CompiledMesh"]).fields["MeshData"].fields["VertexFormat"][0]
                mesh_mats[uid] = [x.id for x in dec.obj.fields.get("CompiledMeshMaterials", [])]
            elif cname == "GridCellDataBlock":
                cells[rec.index] = rec.name
            elif cname == "LODSelector":
                sels[uid] = dec
            elif cname in ("Entity", "EntityGroup"):
                ent_rec[dec.obj.id] = rec.index
                for e_ in [dec.obj] + [M._obj(r) for r in dec.obj.fields.get("Entities", [])]:
                    if e_ is None:
                        continue
                    ent_rec[e_.id] = rec.index
                    v_ = _comp(S, e_, "Visual") if "Components" in e_.fields else None
                    if v_ is not None and v_.fields.get("Object") is not None:
                        ent_sel[e_.id] = M._link(v_.fields["Object"])
    template_of = lambda m: mats[m].obj.fields["MaterialTemplate"].id  # noqa: E731
    fmts = {}
    for mesh, ms in mesh_mats.items():
        for m in ms:
            if m in mats:
                fmts.setdefault(template_of(m), set()).add(mesh_fmt[mesh])
    used = duplicate.used_ids(idents, id_avoid)
    set_maps_ = {u: [x.id for x in s_.obj.fields["Maps"]] for u, s_ in sets.items()}

    # 2. new materials
    made, add_objs = {}, {}                    # key -> uid; record index -> [(serial, bytes)]
    closure = {}                               # material uid -> [uids a cell must list with it]
    shared = set()                             # textures made by this call (a later spec may share them)

    def _maps_of(ref):
        uid = made.get(ref, ref)
        if uid not in mats:
            raise ValueError(f"shared maps: {ref!r} is no material of this map (nor a key made before it)")
        return set_maps_.get(mats[uid].obj.fields["TextureSet"].id, [])
    # specs sharing another's maps come last (the rest keep their order: their ids don't move)
    order = sorted(new.items(), key=lambda kv: (bool(kv[1].get("share1") or kv[1].get("share2")), kv[0]))
    for k, (key, spec) in enumerate(order):
        base = spec["base"]
        if base not in mats:
            raise ValueError(f"{spec['name']}: base material {base.hex()} isn't in this map")
        fresh = duplicate.FreshIds(used, duplicate.seed(src, base, 9100 + k))
        bset = mats[base].obj.fields["TextureSet"].id
        if bset not in sets:
            raise ValueError(f"{spec['name']}: its base material's texture set isn't in this map")
        nset = _renew(copy.deepcopy(sets[bset]), fresh)
        maps = nset.obj.fields["Maps"]
        rec_idx = where[base]
        out = add_objs.setdefault(rec_idx, [])
        new_maps = []
        nrm = spec.get("normal")                   # (H, W, 4) uint8 in AC3's convention (normal_map), else flat
        src1 = _maps_of(spec["share1"]) if spec.get("share1") is not None else None
        for slot, pixels, suffix in ((0, None if src1 is not None else np.asarray(spec["rgba"], np.uint8), "DiffuseMap"),
                                     (1, None if nrm is None else np.asarray(nrm, np.uint8), "NormalMap")):
            if slot >= len(maps) or maps[slot].id not in tex:
                continue
            if src1 is not None:
                if slot < len(src1) and src1[slot] and src1[slot] != bytes(4):
                    maps[slot].id = src1[slot]
                    new_maps.append(src1[slot])
                continue
            donor = tex[maps[slot].id]
            if pixels is None:
                hd = dxt.header(donor)
                pixels = flat_normal(hd["width"], hd["height"])
            tid = fresh.take()
            body = bytearray(texture_body(donor, pixels))
            body[2:6] = tid
            obj_bytes[tid] = (struct.unpack("<I", tid)[0],
                              duplicate.entity_record_bytes(cls["TextureMap"], f"{spec['name']}_{suffix}", bytes(body)))
            out.append(obj_bytes[tid])
            maps[slot].id = tid
            new_maps.append(tid)
        mat = _renew(copy.deepcopy(mats[base]), fresh)
        mat.obj.fields["TextureSet"].id = nset.obj.id
        # the shader's own texture parameters for layer 1 follow the new set (method 0 reads the material's set; a
        # method-1 parameter names a set itself - PavingStone_Mud_Spec's normal names the stock one)
        for slot, pname in ((0, "Layer0_Diffuse"), (1, "Layer0_Normal")):
            p = getattr(param_value(mat.obj, pname), "fields", None)
            if p and "TextureSpecificationMethod" in p and slot < len(maps) and maps[slot].id in new_maps:
                if p["TextureSpecificationMethod"][0] == 1:
                    p["TextureSet"] = Ref(1, 0, nset.obj.id)
                elif p["TextureSpecificationMethod"][0] == 2:
                    p["TextureSpecificationMethod"] = struct.pack("<I", 0)
                    p["TextureSet"] = Ref(*NO_SET)
                p["Texture"].id = maps[slot].id
        # the second layer (two-layer bases): its own new maps, named directly by the shader's Layer1 parameters
        rgba2, nrm2 = spec.get("rgba2"), spec.get("normal2")
        if rgba2 is not None or nrm2 is not None or any(spec.get(k) is not None for k in
                                                        ("blend_scale", "blend_bias", "uv_scale2", "share2")):
            if not two_layer(mats[base].obj):
                raise ValueError(f"{spec['name']}: a second layer needs a two-layer base (a *_Multi material, shader "
                                 f"AC3MP_Tex2_*); {names.get(base, base.hex()).strip()} has one layer")
        layer2 = []
        if rgba2 is not None:
            layer2.append(("Layer1_Diffuse", np.asarray(rgba2, np.uint8), "Layer1_DiffuseMap"))
        if rgba2 is not None or nrm2 is not None:
            layer2.append(("Layer1_Normal", None if nrm2 is None else np.asarray(nrm2, np.uint8), "Layer1_NormalMap"))
        for pname, pixels, suffix in layer2:
            donor_id = param_texture(mats[base].obj, pname, set_maps_)
            if donor_id not in tex and pname == "Layer1_Normal" and len(maps) > 1:
                donor_id = param_texture(mats[base].obj, "Layer0_Normal", set_maps_)
            if donor_id not in tex:
                raise ValueError(f"{spec['name']}: its base's {pname} texture isn't in this map")
            donor = tex[donor_id]
            if pixels is None:
                hd = dxt.header(donor)
                pixels = flat_normal(hd["width"], hd["height"])
            tid = fresh.take()
            body = bytearray(texture_body(donor, pixels))
            body[2:6] = tid
            obj_bytes[tid] = (struct.unpack("<I", tid)[0],
                              duplicate.entity_record_bytes(cls["TextureMap"], f"{spec['name']}_{suffix}", bytes(body)))
            out.append(obj_bytes[tid])
            point_param(mat.obj, pname, tid)
            new_maps.append(tid)
        if spec.get("share2") is not None:
            src2 = _maps_of(spec["share2"])
            for slot, pname in ((0, "Layer1_Diffuse"), (1, "Layer1_Normal")):
                if slot < len(src2) and src2[slot] and src2[slot] != bytes(4):
                    point_param(mat.obj, pname, src2[slot])
                    new_maps.append(src2[slot])
        if rgba2 is not None or spec.get("share2") is not None:
            c0 = param_value(mat.obj, "Layer0_Color")
            if isinstance(c0, bytes) and len(c0) >= 12:
                set_param_floats(mat.obj, "Layer1_Color", struct.unpack_from("<3f", c0))
        if spec.get("layer_color") is not None:
            for pname in ("Layer0_Color", "Layer1_Color"):
                set_param_floats(mat.obj, pname, list(spec["layer_color"])[:3])
        for key_, pname in (("blend_scale", "PixelBlenderScale"), ("blend_bias", "PixelBlenderBias")):
            if spec.get(key_) is not None:
                set_param_floats(mat.obj, pname, [spec[key_]])
        if spec.get("uv_scale2") is not None:
            set_param_floats(mat.obj, "Layer1_UVScale", list(spec["uv_scale2"])[:2])
        # params: {PARAM name: floats} written last (a ported material's own values, e.g. a singleplayer material's
        # layer tints, blend and layer UV scales); a name the base's shader lacks is skipped
        for pname, vals in (spec.get("params") or {}).items():
            set_param_floats(mat.obj, pname, list(vals))
        # the Material's own switches (grass on an opaque base draws its whole cards): alpha test on
        # the colour map's alpha (stock cut-outs: AlphaTestEnabled 1, AlphaTestValue 0x80) and two-sided drawing
        mf = mat.obj.fields
        if spec.get("alpha_test") is not None and "AlphaTestEnabled" in mf:
            mf["AlphaTestEnabled"] = b"\x01" if spec["alpha_test"] else b"\x00"
            if spec["alpha_test"]:
                mf["AlphaTestValue"] = bytes([int(spec.get("alpha_ref", 0x80)) & 0xFF])
        if spec.get("two_sided") is not None and "TwoSided" in mf:
            mf["TwoSided"] = b"\x01" if spec["two_sided"] else b"\x00"
        for root, cname, nm in ((nset, "TextureSet", f"{spec['name']}_TextureSet"), (mat, "Material", spec["name"])):
            root.obj.flag = 1
            body = C.encode(root)
            obj_bytes[root.obj.id] = (struct.unpack("<I", body[2:6])[0], duplicate.entity_record_bytes(cls[cname], nm, body))
            out.append(obj_bytes[root.obj.id])
        mine = [u for u in new_maps if u not in shared and u not in where]
        stored.setdefault(rec_idx, set()).update([mat.obj.id, nset.obj.id] + mine)
        made[key] = mat.obj.id
        mats[mat.obj.id] = mat
        tp = template_of(base)
        set_maps_[nset.obj.id] = [m.id for m in maps]
        closure[mat.obj.id] = list(dict.fromkeys(
            [mat.obj.id, nset.obj.id] + [m.id for m in maps if m.id and m.id != bytes(4)] +
            ([tp] if tp in where else []) +
            [u for u in param_deps(mat.obj, set_maps_) if u in where or u in new_maps or u in shared]))
        shared.update(mine)
        used = fresh.used
        log(f"new material {spec['name']} ({mat.obj.id.hex()}) from {names.get(base)}: "
            f"{len(mine)} new texture map(s) ({len(new_maps) - len(mine)} shared) in {recs[[r.index for r in recs].index(rec_idx)].name}")

    def closure_of(m):
        if m not in closure:
            ts = mats[m].obj.fields["TextureSet"].id
            tp = template_of(m)
            closure[m] = list(dict.fromkeys(
                [m] + ([ts] if ts in where else []) +
                [x.id for x in sets[ts].obj.fields["Maps"] if ts in sets and x.id in where] + ([tp] if tp in where else [])
                + [u for u in param_deps(mats[m].obj, set_maps_) if u in where]))
        return closure[m]

    # 3. swaps: check them, and what each cell must list
    plan, listing = {}, {}                     # entity uid -> {from: to}; record index -> [uids]
    for eid, sw in swaps.items():
        if eid not in ent_rec:
            raise ValueError(f"{eid.hex()}: no such entity")
        rec_idx = ent_rec[eid]
        if rec_idx not in cells:
            raise ValueError(f"{names.get(eid, eid.hex())}: only objects in the map cells can change material yet")
        plan[eid] = {}
        for a, b in sw.items():
            b = made.get(b, b)
            if b not in mats:
                raise ValueError(f"{names.get(eid)}: material {b.hex() if isinstance(b, bytes) else b} isn't in this map")
            plan[eid][a] = b
            listing.setdefault(rec_idx, []).extend(closure_of(b))

    # the far level: each swapped object with an atlas LOD2 gets its own selector showing LOD1 there
    far_sel = {}                                 # entity uid -> its new selector uid
    if far:
        for eid in plan:
            sid = ent_sel.get(eid)
            if sid not in sels:
                continue
            descs = [M._obj(d) for d in sels[sid].obj.fields["LODDescs"]]
            if len(descs) < 3 or descs[2] is None or M._link(descs[2].fields["Object"]) in (None, bytes(4)):
                continue
            fresh = duplicate.FreshIds(used, duplicate.seed(src, eid, 7700))
            nsel = _renew(copy.deepcopy(sels[sid]), fresh)
            nd = [M._obj(d) for d in nsel.obj.fields["LODDescs"]]
            nd[2].fields["Object"].id = M._link(nd[1].fields["Object"])
            if "IsUltraLow" in nd[2].fields:
                nd[2].fields["IsUltraLow"] = b"\x00"
            nsel.obj.flag = 1
            body = C.encode(nsel)
            add_objs.setdefault(ent_rec[eid], []).append(
                (struct.unpack("<I", body[2:6])[0],
                 duplicate.entity_record_bytes(cls["LODSelector"], f"{names.get(sid, 'LODSelector')}_ED", body)))
            listing.setdefault(ent_rec[eid], []).append(nsel.obj.id)
            far_sel[eid] = nsel.obj.id
            used = fresh.used

    def patch(ent):
        """Swap an entity's instance materials (every LOD). -> number of infos changed."""
        sw = plan.get(ent.id)
        vis = _comp(S, ent, "Visual") if sw else None
        if vis is None:
            return 0
        inst = M._obj(vis.fields.get("InstanceData"))
        if inst is None:
            return 0
        items = [inst] if S.name_of(inst.type_hash) == "MeshInstanceData" else \
            [M._obj(x) for x in inst.fields.get("LODInstanceData", [])]
        n = 0
        for mi in items:
            for info in (mi.fields.get("MaterialInfos", []) if mi is not None else []):
                f = M._obj(info).fields
                mesh = M._link(mi.fields.get("Mesh"))
                if f["MeshMaterial"].id in sw:
                    to = sw[f["MeshMaterial"].id]
                    if mesh in mesh_fmt and mesh_fmt[mesh] not in fmts.get(template_of(to), {mesh_fmt[mesh]}):
                        raise ValueError(f"{names.get(ent.id)}: {names.get(to)}'s shader isn't used on this mesh's "
                                         f"vertex format ({mesh_fmt[mesh]}) anywhere in the map")
                    f["InstanceMaterial"].id = to
                    n += 1
        if n and ent.id in far_sel and S.name_of(inst.type_hash) == "LODSelectorInstance":
            lods = inst.fields["LODInstanceData"]
            if len(lods) >= 3 and M._obj(lods[1]) is not None:
                fresh = duplicate.FreshIds(used, duplicate.seed(src, ent.id, 7701))
                lods[2] = _renew(copy.deepcopy(lods[1]), fresh)
                vis.fields["Object"].id = far_sel[ent.id]
                ls = inst.fields.get("LODSelector")
                if ls is not None:
                    if hasattr(ls, "id"):
                        ls.id = far_sel[ent.id]
                    if hasattr(ls, "link") and ls.link is not None:
                        ls.link = far_sel[ent.id]
                done["far"] = done.get("far", 0) + 1
        return n

    # 4. write
    changed, done = set(), {}
    for rec in recs:
        d = payloads[rec.index]
        entries, table = walked[rec.index]
        want = listing.get(rec.index, [])
        adds = add_objs.get(rec.index, []) + local_copies(want, stored.setdefault(rec.index, set()), obj_bytes)
        objs, touched = [], bool(adds)
        for off, size, serial, filt in entries:
            o = object_at(d, off, size)
            body = o["body"]
            uid = body[2:6] if body[:2] == M.UID_MARK else None
            if o["type"] in (cls["Entity"], cls["EntityGroup"]) and uid in ent_rec and \
                    (uid in plan or o["type"] == cls["EntityGroup"]):
                root = C.decode(body)
                n = patch(root.obj)
                for r in root.obj.fields.get("Entities", []):
                    inner = M._obj(r)
                    if inner is not None:
                        k = patch(inner)
                        done[inner.id] = done.get(inner.id, 0) + k
                        n += k
                if n:
                    done[root.obj.id] = done.get(root.obj.id, 0) + n
                    body = C.encode(root)
                    touched = True
            elif o["type"] == cls["GridCellDataBlock"] and want:
                root = C.decode(body)
                g = root.obj.fields
                lst = g["Objects"]
                tag, extra = (lst[0].tag, lst[0].extra) if lst else (1, 0)
                have = {x.id for x in lst}
                tail = [Ref(tag, extra, u) for u in dict.fromkeys(want) if u not in have]
                if tail:
                    g["Objects"] = lst + tail
                    body = C.encode(root)
                    touched = True
            objs.append((serial, M.mapwalk.object_bytes(o, body), None if filt == M.mapwalk.NO_FILTER else filt))
        for serial, data in adds:
            objs.append((serial, data, None))
        if touched:
            nd = M.mapwalk.rebuild(objs, table)
            pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
            assert decompress(pl) == nd
            set_payload(image, rec.index, pl)
            changed.add(rec.index)
    missing = [names.get(e, e.hex()) for e in plan if not done.get(e)]
    if missing:
        raise ValueError(f"no material slot to change on: {', '.join(missing)} (is the material really on its mesh?)")
    write(dest, image)
    with M.Forge(dest) as f:
        same = all(f.read(r) == raw[r.index] for r in f.records if r.index not in changed)
    for eid in plan:
        log(f"{names.get(eid, eid.hex())}: {done[eid]} material slot(s) changed")
    log(f"{dest}: {len(changed)} records rewritten; other records byte-identical: {'yes' if same else 'NO'}")
    return made, same
