"""Reforged Studio's character materials: a part's material values (specular, fresnel, the tint, saturation, normal
strength, rim, emissive, snow, the emblem's placement) edited - previewed on the part itself, built into a test
DataPC on the default set (charedit.build).

A Material (class 0x85C817C3) stores its template's parameters as entries: u32 property hash (crc32 of the editor
name), 6 zero bytes, the kind (0x0A float, 0x0D float3) and the value(s) - forgelib's jester_materials.properties.
An edit writes values in place (no size change). The names: jester_materials.PROPERTY_NAMES' fifteen, and the rest
matched by crc32 of their editor names: Emissive_Color, emissive_factor, Normal Factor, Rim_Color, Rim_Factor,
Spec_Rim_Factor, Snow Color, Snow Factor, U offset, V offset, Flag_Scale_U, Flag_Scale_V (the cape emblem's placement).

The preview (render): the part's LOD0 mesh drawn on the CPU (its own normals, tangents, UVs) with its diffuse, RVBMask,
normal and specular maps and the material values, by the character shading the templates use - the mask channels R
skin, G clothes, B metal, A the colour tint (diffuse x lerp(1, Custom_Color_1, A)); per channel a specular lobe (factor x
(N.H)^power x the specular map) in SpecularMainColor (metal: Specular2ndColor where set), the fresnel rim
((G (1 - N.V))^power x factor + (R (1 - N.V))^power x factor) x the sky colour, the clothes' cubemap term, emissive,
rim, snow. APPROXIMATE: this is not a decompilation of AC3's shaders - the terms move the way the game's do, the
amounts are a guide. The tint (Custom_Color_1 on a material no colour scheme binds) also shows in the 3D viewer.

    materials(part) -> [dict(uid, name, props [dict(hash, name, kind, value)], maps {role: texture})]
    edits {material uid hex: {property hash hex: value or [r, g, b]}}
    render(part, edits, yaw, pitch) -> png
"""
import json
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import charview as C  # noqa: E402

MATERIAL = 0x85C817C3
TEXTURE = 0xA2B7E917
EXTRA_NAMES = {0x4D27E0F3: "Emissive_Color", 0x9F185F7A: "emissive_factor", 0xC8391D08: "Normal Factor",
               0x0305C139: "Rim_Color", 0x6B8E6D5D: "Rim_Factor", 0x8D0AC595: "Spec_Rim_Factor",
               0x6315C67F: "Snow Color", 0xF45199FF: "Snow Factor", 0x5F917024: "U offset", 0xD11E77C7: "V offset",
               0x55A5C77A: "Flag_Scale_U", 0xCCAC96C0: "Flag_Scale_V"}
# the order the editor lists them in
ORDER = ["Custom_Color_1", "Diffuse SaturationFX", "Normal Factor",
         "SpecularMainColor", "Specular2ndColor", "Specular Factor Skin", "Specular Power Skin",
         "Specular Factor Clothes", "Specular Power Clothes", "Specular Factor Metal", "Specular Power Metal",
         "CubeMap Factor Clothes", "Fresnel Clothes Factor", "Fresnel Clothes Power", "Fresnel Skin Factor",
         "Fresnel Skin Power", "Rim_Color", "Rim_Factor", "Spec_Rim_Factor", "Emissive_Color", "emissive_factor",
         "Snow Color", "Snow Factor", "U offset", "V offset", "Flag_Scale_U", "Flag_Scale_V"]


def _jm():
    C._forge()
    import jester_materials
    return jester_materials


def names():
    jm = _jm()
    out = dict(jm.PROPERTY_NAMES)
    out.update(EXTRA_NAMES)
    return out


def _uid(o):
    return C._uid(o["uid"])


def material_maps(objs, material):
    """{role: TextureMap object} the material draws (its body's and its TextureSet's references)."""
    import make_jester
    by_uid = {_uid(o): o for o in objs}
    out = {}
    bodies = [material["body"]]
    for p in range(len(material["body"]) - 3):
        o = by_uid.get(C.u32(material["body"], p))
        if o is not None and o["type"] != TEXTURE and o is not material:
            bodies.append(o["body"])
    for b in bodies:
        for p in range(len(b) - 3):
            o = by_uid.get(C.u32(b, p))
            if o is not None and o["type"] == TEXTURE:
                r = make_jester.role_of(o["name"])
                if r and r not in out:
                    out[r] = o
    return out


def materials(part, objs=None):
    import charmesh
    if objs is None:
        _a, objs = charmesh.part_objects(part)
    jm, nm = _jm(), names()
    out = []
    for o in objs:
        if o["type"] != MATERIAL:
            continue
        props = jm.properties(o["body"])
        rows = []
        for h, (_off, kind, v) in props.items():
            rows.append(dict(hash=f"{h:08X}", name=nm.get(h, f"0x{h:08X}"), kind="float3" if kind == jm.FLOAT3 else "float",
                             value=list(v) if isinstance(v, tuple) else v))
        rows.sort(key=lambda r: (ORDER.index(r["name"]) if r["name"] in ORDER else 99, r["name"]))
        maps = {r: t["name"] for r, t in material_maps(objs, o).items()}
        out.append(dict(uid=f"{_uid(o):08X}", name=o["name"], props=rows, maps=maps))
    return out


def apply(objs, edits):
    """{object name: new body} of the part's materials with `edits` ({uid hex: {hash hex: value}}) written. Unknown
    materials / properties and kind mismatches raise."""
    jm = _jm()
    by_uid = {f"{_uid(o):08X}": o for o in objs if o["type"] == MATERIAL}
    out = {}
    for mu, vals in (edits or {}).items():
        o = by_uid.get(mu.upper())
        if o is None:
            raise ValueError(f"no material {mu} in the part")
        props = jm.properties(o["body"])
        want = {}
        for hk, v in vals.items():
            h = int(hk, 16)
            if h not in props:
                raise ValueError(f"{o['name']} has no property {hk}")
            kind = props[h][1]
            if kind == jm.FLOAT3:
                v = tuple(float(x) for x in v)
                if len(v) != 3:
                    raise ValueError(f"{o['name']} {hk}: three values")
            else:
                v = float(v[0] if isinstance(v, (list, tuple)) else v)
            want[h] = v
        body = jm.with_values(o["body"], want)
        assert len(body) == len(o["body"])
        out[o["name"]] = body
    return out


# ------------------------------------------------------------------------------------------------ the preview --

def _decoded(tex, size=None):
    """A TextureMap -> float rgba (h, w, 4) 0..1, cached as .npy under the Studio's cache (by its uid and length)."""
    F, D, _E = C._forge()
    folder = os.path.join(C.CACHE, C._stamp(D), "tex")
    p = os.path.join(folder, f"{_uid(tex):08X}_{len(tex['body'])}.npy")
    if os.path.exists(p):
        return np.load(p)
    rgba = C._decode(tex["body"])
    if rgba is None:
        return None
    if max(rgba.shape[:2]) > 1024:
        rgba = rgba[::2, ::2]
    os.makedirs(folder, exist_ok=True)
    np.save(p, rgba)
    return rgba


def _sample(img, uv):
    """Nearest texel of img (h, w, 4 uint8) at uv (n, 2) (wrapped) -> (n, 4) float 0..1."""
    h, w = img.shape[:2]
    x = np.floor(np.mod(uv[:, 0], 1.0) * w).astype(np.int64) % w
    y = np.floor(np.mod(uv[:, 1], 1.0) * h).astype(np.int64) % h
    return img[y, x].astype(np.float32) / 255.0


def _val(props, name, default):
    v = props.get(name)
    if v is None:
        return np.array(default, np.float32) if isinstance(default, (list, tuple)) else default
    return np.array(v, np.float32) if isinstance(v, (list, tuple)) else float(v)


def render(part, edits=None, yaw=-1.9, pitch=0.15, width=360, height=520, out=None, objs=None, highlight=None):
    """The part lit with its material values (edits applied) -> PNG path (and the time it took)."""
    import charmesh
    from PIL import Image
    t0 = time.time()
    if objs is None:
        _a, objs = charmesh.part_objects(part)
    bodies = apply(objs, edits)
    objs = [dict(o, body=bodies[o["name"]]) if o["name"] in bodies else o for o in objs]
    m = charmesh.read_mesh(charmesh.lod_meshes(objs, part)[0]["body"])
    jm, nm = _jm(), names()
    by_uid = {_uid(o): o for o in objs}
    # the view: the viewer's orbit (z up), orthographic, fitted to the part
    P = m["pos"]
    lo, hi = P.min(0), P.max(0)
    c = (lo + hi) / 2
    fwd = -np.array([np.cos(yaw) * np.cos(pitch), np.sin(yaw) * np.cos(pitch), np.sin(pitch)])   # eye -> centre
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right) + 1e-9
    up = np.cross(right, fwd)
    rel = P - c
    sx, sy, sz = rel @ right, rel @ up, rel @ fwd
    ext = max(np.abs(sx).max(), np.abs(sy).max() * width / height, 1e-3) * 1.08
    px = (sx / ext * 0.5 + 0.5) * width
    py = (0.5 - sy / ext * 0.5 * width / height) * height
    zbuf = np.full((height, width), np.inf, np.float32)
    tri_id = np.full((height, width), -1, np.int64)
    bary = np.zeros((height, width, 3), np.float32)
    tris, tmat = [], []
    for s in m["subs"]:
        tris.append(s["tris"])
        tmat += [s["material"]] * len(s["tris"])
    T = np.concatenate(tris) if tris else np.zeros((0, 3), np.int64)
    tmat = np.array(tmat, np.int64)
    for t, (a, b, cc) in enumerate(T):
        xs, ys = px[[a, b, cc]], py[[a, b, cc]]
        x0, x1 = max(int(np.floor(xs.min())), 0), min(int(np.ceil(xs.max())), width - 1)
        y0, y1 = max(int(np.floor(ys.min())), 0), min(int(np.ceil(ys.max())), height - 1)
        if x0 > x1 or y0 > y1:
            continue
        den = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(den) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / den
        w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        z = w0 * sz[a] + w1 * sz[b] + w2 * sz[cc]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        win = inside & (z < sub)
        sub[win] = z[win]
        tri_id[y0:y1 + 1, x0:x1 + 1][win] = t
        bary[y0:y1 + 1, x0:x1 + 1][win] = np.stack([w0, w1, w2], -1)[win]
    img = np.zeros((height, width, 4), np.float32)
    hit = tri_id >= 0
    if hit.any():
        ti = tri_id[hit]
        bw = bary[hit]
        idx = T[ti]

        def interp(attr):
            return (attr[idx[:, 0]] * bw[:, 0:1] + attr[idx[:, 1]] * bw[:, 1:2] + attr[idx[:, 2]] * bw[:, 2:3])
        uv = interp(m["uv"])
        Nv = interp(m["nrm"])
        Tv = interp(m["tan"])
        Bv = interp(m["bin"])
        V = -fwd
        L = np.array([-0.35, 0.0, 0.0]) + 0.6 * V + np.array([0, 0, 0.75])
        L = L / np.linalg.norm(L)
        H = (L + V) / np.linalg.norm(L + V)
        col = np.zeros((len(ti), 3), np.float32)
        alpha = np.ones(len(ti), np.float32)
        mats = tmat[ti]
        sky = np.array([0.62, 0.70, 0.82], np.float32)
        for mu in np.unique(mats):
            sel = mats == mu
            mo = by_uid.get(int(mu))
            props = {nm.get(h, h): v for h, (_o, _k, v) in jm.properties(mo["body"]).items()} if mo else {}
            maps = material_maps(objs, mo) if mo else {}
            u = uv[sel]
            dif = _sample(_decoded(maps["Diffuse"]), u) if "Diffuse" in maps and _decoded(maps["Diffuse"]) is not None \
                else np.full((sel.sum(), 4), 0.6, np.float32)
            msk = _sample(_decoded(maps["Mask"]), u) if "Mask" in maps and _decoded(maps["Mask"]) is not None \
                else np.tile(np.array([0, 1, 0, 0], np.float32), (sel.sum(), 1))
            spm = _sample(_decoded(maps["Specular"]), u) if "Specular" in maps and _decoded(maps["Specular"]) is not None \
                else np.full((sel.sum(), 4), 0.5, np.float32)
            n = Nv[sel]
            if "Normal" in maps and _decoded(maps["Normal"]) is not None:
                nt = _sample(_decoded(maps["Normal"]), u)[:, :3] * 2 - 1
                k = _val(props, "Normal Factor", 1.0)
                n = n * nt[:, 2:3] + (Tv[sel] * nt[:, 0:1] + Bv[sel] * nt[:, 1:2]) * k
            n = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-6)
            facing = n @ V
            n[facing < 0] *= -1                       # (two-sided cloth: the side we see)
            ndl = np.clip(n @ L, 0, 1)
            ndh = np.clip(n @ H, 0, 1)
            ndv = np.clip(np.abs(n @ V), 0, 1)
            base = dif[:, :3]
            tint = _val(props, "Custom_Color_1", [1, 1, 1])
            base = base * (1 - msk[:, 3:4] + msk[:, 3:4] * tint)
            sat = _val(props, "Diffuse SaturationFX", 1.0)
            lum = base @ np.array([0.299, 0.587, 0.114], np.float32)
            base = lum[:, None] + (base - lum[:, None]) * sat
            light = 0.38 + 0.85 * ndl[:, None]
            c_ = base * light
            spec_main = _val(props, "SpecularMainColor", [1, 1, 1])
            spec_2nd = _val(props, "Specular2ndColor", [0, 0, 0])
            metal_col = spec_2nd if np.max(spec_2nd) > 0.01 else spec_main
            sl = spm[:, :3].mean(1)
            for ch, (fac, pw, colr) in enumerate([("Specular Factor Skin", "Specular Power Skin", spec_main),
                                                   ("Specular Factor Clothes", "Specular Power Clothes", spec_main),
                                                   ("Specular Factor Metal", "Specular Power Metal", metal_col)]):
                f = _val(props, fac, 0.0)
                pw_ = max(_val(props, pw, 8.0), 0.5)
                c_ += (msk[:, ch] * f * sl * ndh ** pw_)[:, None] * colr * ndl[:, None]
            rim = ((msk[:, 1] * (1 - ndv)) ** max(_val(props, "Fresnel Clothes Power", 1.0), 0.05) * _val(props, "Fresnel Clothes Factor", 0.0)
                   + (msk[:, 0] * (1 - ndv)) ** max(_val(props, "Fresnel Skin Power", 1.0), 0.05) * _val(props, "Fresnel Skin Factor", 0.0))
            c_ += rim[:, None] * sky
            c_ += (msk[:, 1] * _val(props, "CubeMap Factor Clothes", 0.0) * (0.5 + 0.5 * n[:, 2]))[:, None] * sky * 0.6
            c_ += ((1 - ndv) ** 2 * _val(props, "Rim_Factor", 0.0) * 0.25)[:, None] * _val(props, "Rim_Color", [0, 0, 0])
            c_ += _val(props, "emissive_factor", 0.0) * _val(props, "Emissive_Color", [0, 0, 0])[None, :] * np.ones_like(c_)
            snow = _val(props, "Snow Factor", 0.0) * np.clip(n[:, 2], 0, 1)
            c_ = c_ * (1 - snow[:, None]) + snow[:, None] * _val(props, "Snow Color", [1, 1, 1]) * light
            if highlight and f"{int(mu):08X}" != highlight.upper():
                c_ = c_ * 0.35 + 0.1
            col[sel] = c_
            alpha[sel] = 1.0 if dif.shape[1] < 4 or dif[:, 3].min() > 0.99 else np.where(dif[:, 3] < 0.4, 0.0, 1.0)
        img[hit, :3] = np.clip(col, 0, 1)
        img[hit, 3] = alpha
    png = out or os.path.join(C.CACHE, "material_preview.png")
    os.makedirs(os.path.dirname(png), exist_ok=True)
    px8 = (img * 255).astype(np.uint8)
    Image.fromarray(px8).save(png)
    dds = png[:-4] + ".dds"                            # (the app's picture: uncompressed RGBA)
    write_rgba_dds(dds, px8)
    return dict(png=png, image=dds, width=width, height=height, seconds=round(time.time() - t0, 2), triangles=int(len(T)))


def write_rgba_dds(path, px):
    """An (h, w, 4) uint8 picture as an uncompressed 32-bit RGBA DDS (the Studio's load_dds reads it)."""
    h, w = px.shape[:2]
    hdr = struct.pack("<4sIIIIIII44s", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x8 | 0x1000, h, w, w * 4, 0, 1, bytes(44))
    pf = struct.pack("<IIIIIIII", 32, 0x40 | 0x1, 0, 32, 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)
    caps = struct.pack("<IIIII", 0x1000, 0, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(hdr + pf + caps + np.ascontiguousarray(px).tobytes())


def main(argv):
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if argv and argv[0] == "materials":
        res = dict(part=argv[1], materials=materials(argv[1]))
    elif argv and argv[0] == "render":
        a = json.loads(argv[1])
        res = render(a["part"], a.get("edits"), float(a.get("yaw", -1.9)), float(a.get("pitch", 0.15)),
                     int(a.get("width", 360)), int(a.get("height", 520)), a.get("out"), highlight=a.get("highlight"))
    else:
        print(__doc__)
        return 0
    print(json.dumps(res) if as_json else json.dumps(res, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
