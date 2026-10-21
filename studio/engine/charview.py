"""Reforged Studio's character viewer: a persona drawn as it is built - its default look's parts (Main, Secondary,
Head, Belt, Legs; a costume when it has one), their materials, its default weapon - from the default set's DataPC
(paths.DEFAULT_SET), read-only.

The chain (forgelib's readers, imported read-only; their decompress cache is left off in this process):
* personas: the `Game Bootstrap Settings` record (bootstrap_build.Bootstrap): CharacterCustomization objects
  `AC3MP_ID<n>` (persona.CUST) - id = u32 6 bytes from the end, name = the UIString at persona.ui_string_sites[0]
  (+8) looked up in the English LocalizationPackage (strings/locpack); the kit `CharacterBuildingKit_AC3MP_ID<n>`.
* parts: the kit's part tables (+107 Main, +112 Secondary, +117 Head, +122 Belt, +127 Legs, +147 Costumes_Temp:
  look_rows.PART_TABLES / COSTUMES_AT) read with look_rows.BuildView.table: row 0 = the default look, each
  GraphicObject cell names a part file whose root is a LODSelector of the same name; a TextureSet cell in the head row
  = the face's own maps (the head's material draws runtime render targets).
* meshes: part_render.load_mesh (skinned: metres = q x 10 / 32768, bind pose, z up), every part moved onto the binds
  most of them share (part_render.persona_reference / triangles: some ACR parts are authored on a skeleton placed
  elsewhere); textures: each sub-mesh's Material -> its Diffuse TextureMap (make_jester.role_of) -> a DDS
  (ac3editor mapdata.dds_bytes: the stored DXT data, no decoding).
* weapon: kit +172 = the default weapon's inventory id -> its Inventory object (persona.INVENTORY) -> the weapon
  file (its root) -> its LOD0 Mesh, a static mesh (ac3editor mapdata.decode_mesh), drawn beside the body.

    python studio/engine/charview.py personas               the default set's personas (cached)
    python studio/engine/charview.py character <id>         one persona's scene (the worker runs this as a child)
"""
import json
import os
import re
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
from ac3editor import mapdata as M  # noqa: E402  (puts forgelib on the path)

CACHE = os.path.join(WORK, "cache", "studio", "chars")
VERSION = 17
SLOTS = [(107, "Main"), (112, "Secondary"), (117, "Head"), (122, "Belt"), (127, "Legs"), (147, "Costume"),
         (152, "Weather")]
STYLE = ("Main", "Secondary", "Head", "Belt", "Legs")
# the kit's +152 "Winter" table: row 0 the ordinary look's own pieces - the bare SKIN (Lady Maverick's forearms between
# her short sleeves and gloves: AC3MP_ID75_Skin), fur bases - row 1 the snow maps'
WEAPON_AT = 172


def _forge():
    os.environ["AC3FORGE_NO_DECOMP_CACHE"] = "1"          # (read-only: nothing written into forgelib's cache)
    F = M.FORGE_REPO
    for p in (F, os.path.join(F, "ac4chars"), os.path.join(F, "strings")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import paths
    d = paths.DEFAULT_SET
    return F, os.path.join(d, "DataPC.forge"), os.path.join(d, "DataPC_extra.forge")


def _stamp(path):
    st = os.stat(path)
    return f"{st.st_size}_{int(st.st_mtime)}"


def u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


# ------------------------------------------------------------------------------------------------ personas --

def personas(log=print):
    """-> [dict(id, name, internal, kit)] of the default set, cached per DataPC."""
    F, D, E = _forge()
    out = os.path.join(CACHE, f"personas_{_stamp(D)}_v{VERSION}.json")
    if os.path.exists(out):
        return json.load(open(out, encoding="utf-8"))
    import persona
    import locpack
    from bootstrap_build import Bootstrap
    objs = Bootstrap(D).objects
    loc = locpack.LocData(locpack.package_data("English", D))
    kits = {u32(o["body"], 14) for o in objs if o["name"].startswith("CharacterBuildingKit_AC3MP_ID")}
    rows = []
    for o in objs:
        if o["type"] != persona.CUST or not re.match(r"AC3MP_ID\d+$", o["name"]):
            continue
        b = o["body"]
        pid = u32(b, len(b) - 6)
        name = ""
        sites = persona.ui_string_sites(b)
        if sites:
            try:
                name = loc.lookup(u32(b, sites[0] + 8)) or ""
            except Exception:  # noqa: BLE001
                name = ""
        rows.append(dict(id=pid, name=(name or o["name"]).title(), internal=o["name"], kit=pid in kits))
    rows.sort(key=lambda r: r["id"])
    os.makedirs(CACHE, exist_ok=True)
    json.dump(rows, open(out, "w", encoding="utf-8"), indent=1)
    log(f"characters: {len(rows)} personas in {os.path.basename(os.path.dirname(D))}")
    return rows


# ------------------------------------------------------------------------------------------------ a persona --

class _Source:
    """part_render.Source for AC3's DataPC / DataPC_extra (look_rows.BuildView: records by name, root uid -> record)."""

    def __init__(self, *views):
        import exe_registry
        cl, _ = exe_registry.load(exe_registry.AC3)
        self.material = next(k for k, n in cl.items() if n == "Material")
        self.lodsel = next(k for k, n in cl.items() if n == "LODSelector")
        self.views = views

    def objects(self, name):
        if name in OVERRIDES:                                # (a part drawn from its imported mesh)
            return OVERRIDES[name]
        for v in self.views:
            if name in v.by_name:
                return v.objects(name)
        return []

    def by_uid(self, _u):
        return None


OVERRIDES = {}                 # part file -> its objects with the imported meshes (charmesh.patched_objects)


def _mesh_sig(meshes):
    """A short key of the imported meshes asked for (path, its time, LODs) - part of the cache names."""
    import hashlib
    if not meshes:
        return ""
    key = json.dumps({k: [v.get("glb"), int(os.path.getmtime(v["glb"])) if os.path.exists(v.get("glb", "")) else 0,
                          v.get("lods", "all")] for k, v in sorted(meshes.items())})
    return "_m" + hashlib.sha1(key.encode()).hexdigest()[:10]


def part_bodies(part, mesh=None, mats=None, objs=None, log=print):
    """A part file's edited bodies: its meshes from an imported glTF ({"glb", "lods"}) and its materials' values
    ({uid hex: {hash hex: value}}) -> (objs, {object name: body}, mesh report or None)."""
    import charmesh
    if objs is None:
        _a, objs = charmesh.part_objects(part)
    bodies, rep = {}, None
    if mesh:
        b, rep = charmesh.mesh_bodies(part, mesh["glb"], mesh.get("lods", "all"), objs)
        bodies.update(b)
        log(f"{part}: drawn from {os.path.basename(mesh['glb'])} ({rep['vertices']} vertices, {rep['triangles']} triangles)")
    if mats:
        import charmat
        bodies.update(charmat.apply(objs, mats))
    return objs, bodies, rep


def _set_meshes(meshes, log=print, materials=None):
    """OVERRIDES from imported meshes and material edits -> [problems] (a part that fails is drawn as
    stored)."""
    OVERRIDES.clear()
    bad = []
    import charmesh
    for part in sorted(set(meshes or {}) | set(materials or {})):
        try:
            objs, bodies, _rep = part_bodies(part, (meshes or {}).get(part), (materials or {}).get(part), log=log)
            OVERRIDES[part] = charmesh.patched_objects(objs, bodies)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{part}: {e}")
    return bad


def _diffuse(objs_by_uid, material):
    """A Material object -> its Diffuse TextureMap object (the TextureMaps its body names, by role), or None."""
    import make_jester
    b = material["body"]
    for p in range(len(b) - 3):
        o = objs_by_uid.get(u32(b, p))
        if o is not None and o["type"] == make_jester.TEXTURE and make_jester.role_of(o["name"]) == "Diffuse":
            return o
    return None


CC1 = 0xDF1F9C86              # crc32("Custom_Color_1"): the material's tint (shader CustomColor1_N)
COLOUR_AT = 132               # the kit's colour table (Custom_color1: one BuildTableExt object)


def _uid(u):
    return struct.unpack("<I", u)[0] if isinstance(u, (bytes, bytearray)) else int(u)


def colour_table(view_objects, table_body):
    """A kit's Custom_color1 body -> ({material uid: region}, [ {region: (r, g, b)} per scheme ]). Layout (as forgelib's
    acr_colours.py reads it): +10 u32 n columns; column k at 14 + 205 k: +98 material uid,
    +128 property hash (Custom_Color_1 here; some bind the specular colours), +164 region; then u32 m rows (schemes +
    an empty trailer): each +21 u32 count, then count x 28-byte cells: +0 region, +12 four f32 (R, G, B, 0)."""
    b = table_body
    n = u32(b, 10)
    bind = {}
    for k in range(n):
        c = 14 + 205 * k
        if c + 168 > len(b):
            break
        if u32(b, c + 128) == CC1:
            bind[u32(b, c + 98)] = u32(b, c + 164)
    p = 14 + 205 * n
    m = u32(b, p)
    p += 4
    schemes = []
    for _ in range(m):
        if p + 25 > len(b):
            break
        cnt = u32(b, p + 21)
        cells = {}
        for j in range(cnt):
            q = p + 25 + 28 * j
            cells[u32(b, q)] = struct.unpack_from("<3f", b, q + 12)
        if cnt:
            schemes.append(cells)
        p += 25 + 28 * cnt
    return bind, schemes


def _decode(tex_body):
    """An MP TextureMap body -> rgba (h, w, 4) of its top mip (spmaps' numpy DXT decoder)."""
    import splib
    _T, sp_assets, _theirs = splib._readers()
    w, h = struct.unpack_from("<II", tex_body, 10)
    fmt = struct.unpack_from("<I", tex_body, 22)[0]
    size = struct.unpack_from("<I", tex_body, 86)[0]
    if fmt not in (2, 3, 4, 5):
        return None
    return sp_assets.decode_dxt(tex_body[90:90 + size], w, h, fmt)


def character(pid, rows=None, log=print, colours=None, meshes=None, weapons=None, materials=None):
    """-> dict(scene (an .ac3s the app maps), parts [dict(slot, row, file, triangles, textures)], tables {slot: [row
    labels]}, rows {slot: row}, weapon, ...), written under the Studio's cache. rows: {slot: row} - the look per slot
    (default 0), the costume row (default: 1 where the exe makes costumes add to the style, else none), the weather
    row (0 ordinary, 1 winter). A costume that replaces the style (stock behaviour) leaves the style slots out."""
    F, D, E = _forge()
    rows = {k: int(v) for k, v in (rows or {}).items()}
    mesh_specs = dict(meshes or {})    # (the imported meshes asked for: `meshes` below is the loaded part meshes)
    sig = "_".join(f"{k[:2]}{rows[k]}" for k in sorted(rows))
    if colours:                        # colour edits previewed (charedit's {scheme: {region: rgb}})
        import hashlib
        sig += "_e" + hashlib.sha1(json.dumps(colours, sort_keys=True).encode()).hexdigest()[:10]
    sig += _mesh_sig(meshes)           # imported meshes drawn
    if WEAPON_K != "I":
        sig += "_k" + WEAPON_K
    if materials:                      # material values ({part: {material: {property: value}}})
        import hashlib
        sig += "_v" + hashlib.sha1(json.dumps(materials, sort_keys=True).encode()).hexdigest()[:10]
    if weapons:                        # items drawn with another model ({item id: weapon file})
        import hashlib
        sig += "_w" + hashlib.sha1(json.dumps(weapons, sort_keys=True).encode()).hexdigest()[:10]
    folder = os.path.join(CACHE, _stamp(D), str(pid))
    res = os.path.join(folder, f"character_v{VERSION}{'_' + sig if sig else ''}.json")
    if os.path.exists(res):
        return json.load(open(res, encoding="utf-8"))
    t0 = time.time()
    mesh_problems = _set_meshes(meshes, log, materials)
    import exe_registry
    import look_rows
    import make_jester
    import part_render
    import persona
    from bootstrap_build import Bootstrap
    cl, _ = exe_registry.load(exe_registry.AC3)
    dv, ev = look_rows.BuildView(D), look_rows.BuildView(E)
    objs = Bootstrap(D).objects
    kit = next((o for o in objs if o["name"] == f"CharacterBuildingKit_AC3MP_ID{pid}"), None)
    if kit is None:
        raise ValueError(f"persona {pid} has no CharacterBuildingKit in {D}")
    kb = kit["body"]
    src = _Source(dv, ev)
    # a costume replaces the style in game, unless the exe flags the persona's costume as adding to it (exe_costume:
    # the AC4 ports - Blackbeard's coat and legs are costume parts)
    costume_adds = False
    try:
        import exe_costume
        from exe_patch import PE
        import paths
        exe = getattr(paths, "DEFAULT_EXE", "") or os.path.join(os.path.dirname(D), "AC3MP.exe")
        costume_adds = pid in exe_costume.flagged(PE(open(exe, "rb").read()))
    except Exception:  # noqa: BLE001
        pass
    want = {sl: 0 for sl in STYLE}
    want.update(Costume=0, Weather=0, Colours=0)
    want.update(rows)
    parts, face_set, tables, costume_sets, rigs = [], None, {}, [], []
    replaced = want["Costume"] > 0 and not costume_adds
    for off, slot in SLOTS:
        root = u32(kb, off)
        tname, trows = dv.table(root)
        labels = []
        for k, cells in trows or []:
            gos = [dv.name_of(ref) or ev.name_of(ref) for _c, cls, ref in cells if ref and cl.get(cls) == "GraphicObject"]
            gos = [g for g in gos if g]
            labels.append(dict(row=k, label=", ".join(g.replace(f"AC3MP_ID{pid}_", "") for g in gos[:3]) or "(empty)",
                               parts=len(gos)))
            if k != want[slot] or (replaced and slot in STYLE):
                continue
            for _col, cls, ref in cells:
                if not ref:
                    continue
                kind = cl.get(cls)
                if kind == "GraphicObject":
                    n = dv.name_of(ref) or ev.name_of(ref)
                    if n:
                        parts.append(dict(slot=slot, row=k, file=n, table=tname))
                elif kind == "Skeleton":
                    n = dv.name_of(ref) or ev.name_of(ref)
                    if n and n not in rigs:
                        rigs.append(n)
                elif kind == "TextureSet" and slot in ("Head", "Costume"):
                    n = dv.name_of(ref) or ev.name_of(ref)
                    if n and "Head" in n and (slot == "Head" or face_set is None):
                        face_set = n
                    elif n and slot == "Costume" and n not in costume_sets:
                        costume_sets.append(n)
        tables[slot] = labels
    costume = [p for p in parts if p["slot"] == "Costume"]
    style = [p for p in parts if p["slot"] != "Costume"]
    # the colour schemes (kit +132): which materials they tint, each scheme's colour per region
    bind, schemes = {}, []
    ccols, cschemes = [], []
    troot = u32(kb, COLOUR_AT)
    tname = dv.name_of(troot)
    if tname:
        tobj = next((o for o in dv.objects(tname) if _uid(o["uid"]) == troot), None)
        if tobj is not None:
            tbody = tobj["body"]
            if colours:
                import charedit
                tbody, _bad = charedit.apply(tbody, colours)
            bind, schemes = colour_table(None, tbody)
            import charedit
            ccols, cschemes = charedit.cells(tbody)
    tables["Colours"] = [dict(row=k, label="scheme %d (%s)" % (k + 1, ", ".join(
        "%d %d %d" % tuple(int(round(255 * c)) for c in v) for v in list(sc.values())[:1])), parts=len(sc))
        for k, sc in enumerate(schemes)]
    scheme = schemes[want["Colours"]] if want["Colours"] < len(schemes) else {}
    # the textures: patch part_render's texture reader to hand back the TextureMap object (no decoding: its DDS)
    tex_of = {}

    tint_of = {}                       # diffuse uid -> (mask TextureMap, tint (r, g, b)) of the material drawing it
    mat_names = {}                     # material uid -> (its name, the part file) (what a colour column tints)

    def tex_obj(material, by_uid, _src):
        o = _diffuse(by_uid, material)
        if o is not None:
            tex_of[o["uid"]] = o
            mat_names.setdefault(_uid(material["uid"]), material["name"])
            if o["uid"] not in tint_of:
                # the tint: the scheme's colour where the colour table binds this material, else its own stored
                # Custom_Color_1, else none (the template default 1, 1, 1)
                import jester_materials
                region = bind.get(_uid(material["uid"]))
                c = scheme.get(region) if region is not None else None
                if c is None:
                    pv = jester_materials.properties(material["body"]).get(CC1)
                    c = tuple(pv[2]) if pv and isinstance(pv[2], tuple) else None
                b = material["body"]
                mask = None
                for q in range(len(b) - 3):
                    mo = by_uid.get(u32(b, q))
                    if mo is not None and mo["type"] == make_jester.TEXTURE and make_jester.role_of(mo["name"]) == "Mask":
                        mask = mo
                        break
                tint_of[o["uid"]] = (mask, c)
            return ("map", o["uid"], o["name"])
        return None
    part_render._texture = tex_obj
    cells = [(p["file"], p["file"]) for p in style + costume]
    drawn = set(part_render.drawn(cells))
    meshes = {}
    for f, s in cells:
        if (f, s) in drawn:
            m = part_render.load_mesh(src, f, s)
            if m:
                meshes[f] = m
    ref = part_render.reference(list(meshes.values()))
    ref_palette = [(bone, rt[0], rt[1]) for bone, rt in ref.items()]          # (the binds the weapon hangs off)
    # the face: the head row's TextureSet -> its Diffuse map (for head sub-meshes whose material draws render targets)
    face = None
    if face_set:
        fobjs = src.objects(face_set)
        face = next((o for o in fobjs if o["type"] == make_jester.TEXTURE and make_jester.role_of(o["name"]) == "Diffuse"),
                    None)
        if face is not None:
            tex_of[face["uid"]] = face
    def set_diffuse(name):
        o = next((o for o in src.objects(name) if o["type"] == make_jester.TEXTURE and make_jester.role_of(o["name"]) == "Diffuse"),
                 None)
        if o is not None:
            tex_of[o["uid"]] = o
        return o

    def costume_key(part):
        """A costume part with no colour map of its own: its row's TextureSet - a hat's the hat set, a feather's the
        feathers', the rest the row's first garment set (Blackbeard's C_Part / C_Lower_Coat: the Templar costume set)."""
        n = part["file"].lower()
        pick = None
        for word in ("hat", "feather"):
            if word in n:
                pick = next((c for c in costume_sets if word in c.lower()), None)
        if pick is None:
            pick = next((c for c in costume_sets if not any(w in c.lower() for w in ("hat", "head", "feather"))),
                        costume_sets[0] if costume_sets else None)
        o = set_diffuse(pick) if pick else None
        return o["uid"] if o is not None else None
    os.makedirs(folder, exist_ok=True)
    textures, tex_index = [], {}

    tinted = []
    esig = sig[sig.index("_e"):] if "_e" in sig else ""      # (an edit's bake gets its own file: the app keys by path)

    def tex_file(o):
        if o["uid"] in tex_index:
            return tex_index[o["uid"]]
        mask, c = tint_of.get(o["uid"], (None, None))
        if mask is not None and c is not None and any(abs(v - 1.0) > 0.01 for v in c[:3]):
            # diffuse x lerp(1, C, mask.a) (the pixel shaders' tint), baked; diffuse alpha kept
            rgba, mk = _decode(o["body"]), _decode(mask["body"])
            if rgba is not None and mk is not None and mk[:, :, 3].max() > 0:
                from PIL import Image
                if mk.shape[:2] != rgba.shape[:2]:
                    mk = np.asarray(Image.fromarray(mk).resize((rgba.shape[1], rgba.shape[0]), Image.BILINEAR))
                if max(rgba.shape[:2]) > 1024:          # (the viewer: 1024 at most - the encoder is numpy)
                    k = 1024 / max(rgba.shape[:2])
                    size = (int(rgba.shape[1] * k), int(rgba.shape[0] * k))
                    rgba = np.asarray(Image.fromarray(rgba).resize(size, Image.BILINEAR))
                    mk = np.asarray(Image.fromarray(mk).resize(size, Image.BILINEAR))
                a = mk[:, :, 3:4].astype(np.float32) / 255.0
                out = rgba.astype(np.float32)
                out[:, :, :3] = out[:, :, :3] * (1.0 - a + a * np.array(c[:3], np.float32))
                out = np.clip(np.round(out), 0, 255).astype(np.uint8)
                import splib
                p = os.path.join(folder, f"{_uid(o['uid']):08x}_s{want['Colours']}{esig}.dds")
                splib.write_dds(p, out)
                tex_index[o["uid"]] = len(textures)
                textures.append(p)
                tinted.append(o["name"])
                return tex_index[o["uid"]]
        dds = M.dds_bytes(o["body"])
        if dds is None:
            tex_index[o["uid"]] = -1
            return -1
        p = os.path.join(folder, f"{struct.unpack('<I', o['uid'])[0] if isinstance(o['uid'], bytes) else o['uid']:08x}.dds")
        with open(p, "wb") as fh:
            fh.write(dds)
        tex_index[o["uid"]] = len(textures)
        textures.append(p)
        return tex_index[o["uid"]]
    scene_parts = []
    for p in style + costume:
        m = meshes.get(p["file"])
        if m is None:
            p["triangles"] = 0
            continue
        tris = part_render.triangles(m, ref)
        V = np.array([[v[0], v[1], v[2]] for t in tris for v in t[:3]], np.float32)
        UV = np.array([[v[3], v[4]] for t in tris for v in t[:3]], np.float32)
        keys = []
        TM = []
        for t in tris:
            tx = t[3]
            key = tx[1] if tx else (face["uid"] if (face is not None and (p["slot"] == "Head" or "Head" in p["file"]))
                                    else costume_key(p) if p["slot"] == "Costume" else None)
            if key not in keys:
                keys.append(key)
            TM.append(keys.index(key))
        mats = []
        for key in keys:
            o = tex_of.get(key) if key is not None else None
            mats.append((o["name"] if o else "untextured", tex_file(o) if o else -1))
        p["triangles"] = len(tris)
        p["textures"] = [n for n, _t in mats]
        scene_parts.append(dict(name=p["file"], slot=p["slot"], verts=V, uv=UV,
                                tris=np.arange(len(V), dtype=np.uint32).reshape(-1, 3), tri_mat=np.array(TM), mats=mats))
    # the default weapon: kit +172 -> its Inventory object -> its file's LOD0 static mesh
    weapon = None
    wid = int(rows.get("Item") or u32(kb, WEAPON_AT))      # (rows "Item" = which item to draw; default the kit's)
    inv = next((o for o in objs if o["name"] == str(wid) and o["type"] == persona.INVENTORY), None)
    if inv is not None:
        wfile = dv.name_of(u32(inv["body"], len(inv["body"]) - 4)) or ev.name_of(u32(inv["body"], len(inv["body"]) - 4))
        stock = wfile
        wfile = (weapons or {}).get(str(wid)) or wfile
        if wfile:
            weapon = dict(id=wid, file=wfile, stock=stock, edited=wfile != stock, default=u32(kb, WEAPON_AT))
            try:
                _EV[:] = [ev]
                # the attach bone's bind (the hand's prop bone / the holster's WeaponRef), from the parts' shared
                # binds (part_render.reference) and the rigs' local transforms
                at = int(want.get("WeaponAt", 0))
                f = persona.item_fields(inv["body"])
                cls = f[1] if f else 1
                place = None
                weapon.update(at=min(at, 2), at_name=WEAPON_AT_NAMES[min(at, 2)], cls=cls)
                if at in (0, 1):
                    binds = {}
                    for bone, (c1, c2, c3), tt in ref_palette:
                        Mb = np.array([c1, c2, c3], np.float64).T
                        binds[bone] = (Mb.T, -Mb.T @ np.asarray(tt, np.float64))
                    main = f"AC3MP_ID{pid}_Main_Player" if f"AC3MP_ID{pid}_Main_Player" in dv.by_name else "AC3MP_ID87_Main_Player"
                    hb = next(o for o in dv.objects(main) if o["name"] == "Human Body")
                    locs = {h: (ph, lp, lq) for h, ph, lp, lq in skeleton_bones(hb["body"])}
                    bone = HAND_PROP
                    if at == 1:
                        rig, rb = attach_rig(dv, ev, cl, kb, cls)
                        weapon["rig"] = rig
                        for h, ph, lp, lq in rb:
                            if h not in binds:
                                locs[h] = (ph, lp, lq)
                        bone = WEAPON_REF
                    place = attach_bind(bone, binds, locs)
                    if place is None:
                        weapon["at_error"] = f"no bind for bone {bone:08X}: drawn beside the body"
                    else:
                        weapon["bone"] = f"{bone:08X}"
                wp = _weapon_part(dv, wfile, make_jester, tex_file, place)
                if wp is not None:
                    np.save(res[:-5] + "_weapon.npy", wp.pop("raw"))      # (the animation poses it from these)
                    scene_parts.append(wp)
                    weapon["triangles"] = int(len(wp["tris"]))
                    weapon["textures"] = [n for n, _t in wp["mats"]]
            except Exception as e:  # noqa: BLE001
                weapon["error"] = str(e)
    drawn_mats = set(mat_names)
    # the colour table's columns (what each tints, by the materials drawn) and the chosen scheme's cells
    colour_edit = None
    if ccols:
        try:
            for u, (n, sl, rk) in _material_names(dv, ev, cl, kb, pid, os.path.join(CACHE, _stamp(D), str(pid)),
                                                  src.material, weapon and weapon.get("file")).items():
                if u not in mat_names:
                    mat_names[u] = f"{n} ({sl} look {rk + 1})" if sl != "Weapon" else f"{n} (weapon)"
        except Exception as e:  # noqa: BLE001
            log(f"material names: {e}")
        cs = cschemes[want["Colours"]] if want["Colours"] < len(cschemes) else None
        cols = []
        for c in ccols:
            cols.append(dict(region=c["region"], what=c["what"], material=f"0x{c['material']:08X}",
                             name=mat_names.get(c["material"], ""), drawn=c["material"] in drawn_mats))
        colour_edit = dict(table=tname, columns=cols, scheme=want["Colours"],
                           cells=[dict(region=x["region"], rgb=x["rgb"]) for x in (cs["cells"] if cs else [])])
    scene = res[:-5] + ".ac3s"
    _write_scene(scene, scene_parts, textures)
    out = dict(id=pid, scene=scene, parts=style + costume, face=face_set, weapon=weapon, textures=len(textures),
               costume_adds=costume_adds, tables=tables, rows=want, tinted=tinted, sex=int(kb[0x12]), rigs=rigs,
               scheme=[list(v) for v in scheme.values()][:1], colour_edit=colour_edit, colours=colours or {},
               meshes=mesh_specs, mesh_problems=mesh_problems, materials=materials or {},
               triangles=int(sum(len(sp["tris"]) for sp in scene_parts)), seconds=round(time.time() - t0, 1),
               build=os.path.basename(os.path.dirname(D)), part_names=[sp["name"] for sp in scene_parts],
               part_slots=[sp["slot"] for sp in scene_parts])
    json.dump(out, open(res, "w", encoding="utf-8"), indent=1)
    log(f"character {pid}: {len(scene_parts)} part(s), {out['triangles']} triangles, {len(textures)} texture(s) "
        f"in {out['seconds']} s")
    return out


def _material_names(dv, ev, cl, kb, pid, folder, material_cls, weapon_file=None):
    """{material uid: (name, slot, row)} over every row of the kit's part tables (what a colour column tints,
    drawn or not), cached per persona."""
    path = os.path.join(folder, "materials.json")
    if os.path.exists(path):
        return {int(k): tuple(v) for k, v in json.load(open(path, encoding="utf-8")).items()}
    out = {}
    for off, slot in SLOTS + [(137, "Weapon add-on"), (142, "Weapon add-on (silent)")]:
        _n, trows = dv.table(u32(kb, off))
        for k, cells in trows or []:
            for _c, cls, ref in cells:
                if not ref or cl.get(cls) != "GraphicObject":
                    continue
                n = dv.name_of(ref) or ev.name_of(ref)
                if not n:
                    continue
                for v in (dv, ev):
                    for o in v.objects(n):
                        if o["type"] == material_cls:
                            out.setdefault(_uid(o["uid"]), (o["name"], slot, k))
    if weapon_file:
        for o in dv.objects(weapon_file):
            if o["type"] == material_cls:
                out.setdefault(_uid(o["uid"]), (o["name"], "Weapon", 0))
    os.makedirs(folder, exist_ok=True)
    json.dump({str(k): v for k, v in out.items()}, open(path, "w", encoding="utf-8"))
    return out


_EV = []                       # (DataPC_extra's view, for weapon files living there)

# Where the weapon is drawn (rows "WeaponAt": 0 the hand, 1 the holster, 2 beside the body). Every menu clip drives one bone on each hand beside the fingers (the right's 0x3FB256E5, the left's 0xA9611103: the
# hands' prop bones) - the weapon's place in the hand; the holster rigs (the kit's +137 table, row class - 1:
# AC3MP_ID75_WB01 ...) chain Hips -> (a helper) -> WeaponRef (crc32 "WeaponRef" = 0x5D2A1DA3), which no clip drives.
# A weapon mesh's origin is its grip, its blade along its own z.
HAND_PROP, HAND, HIPS, WEAPON_REF = 0x3FB256E5, 0x75F94D30, 0xDED10611, 0x5D2A1DA3
ADDON_AT = 137
WEAPON_AT_NAMES = ("in the hand", "in the holster", "beside the body")
# the weapon's axes in its bone's frame (loc = K v): "I" as stored, "P" (y, z, x), "PT" (z, x, y) - chosen by eye on the
# weapon-selection clips
WEAPON_K = os.environ.get("CHARVIEW_WEAPON_K", "I")


def weapon_local(V, k=None):
    """Weapon-mesh vertices -> its attach bone's local frame (skeleton axes)."""
    V = np.asarray(V, np.float64)
    k = k or WEAPON_K
    if k == "P":
        return V[:, [1, 2, 0]]
    if k == "PT":
        return V[:, [2, 0, 1]]
    if k == "NZ":                                   # (blade down the other way: z flipped, x flipped)
        return V * np.array([-1.0, 1.0, -1.0])
    return V


def _qmat_np(q):
    return np.asarray(_qmat(tuple(q)), np.float64)


def attach_rig(dv, ev, cl, kb, cls):
    """The class's holster rig: (record name, [(hash, parent, local pos, local quat)]) from the kit's +137 table row
    class - 1, or (None, [])."""
    _n, trows = dv.table(u32(kb, ADDON_AT))
    for k, cells in trows or []:
        if k != cls - 1:
            continue
        for _c, c, ref in cells:
            if ref and cl.get(c) == "Skeleton":
                name = dv.name_of(ref) or ev.name_of(ref)
                v = dv if name in dv.by_name else ev
                for o in v.objects(name) if name else []:
                    if b_find(o["body"], BONE, 0) >= 0:
                        return name, skeleton_bones(o["body"])
    return None, []


def attach_bind(bone, binds, locals_):
    """A bone's bind (R, origin) in the skeleton frame: its palette bind if a mesh has it, else its parent's bind and
    its rig's local transform (locals_: {hash: (parent, pos, quat)})."""
    if bone in binds:
        return binds[bone]
    if bone not in locals_:
        return None
    ph, lp, lq = locals_[bone]
    p = attach_bind(ph, binds, locals_) if ph is not None else (np.eye(3), np.zeros(3))
    if p is None:
        return None
    R, o = p
    return R @ _qmat_np(lq), R @ np.asarray(lp, np.float64) + o


def _weapon_part(dv, wfile, make_jester, tex_file, place=None):
    """A weapon file's LOD0 static mesh as a scene part, laid beside the body (x + 0.6 m, upright)."""
    S = M.schema()
    C = M.Codec(S)
    objs = dv.objects(wfile) if wfile in dv.by_name else _EV[0].objects(wfile) if _EV and wfile in _EV[0].by_name else []
    meshes = sorted((o for o in objs if o["type"] == make_jester.MESH), key=lambda o: o["name"])
    if not meshes:
        return None
    fields = C.decode(meshes[0]["body"]).obj.fields
    extra = []
    got = M.decode_mesh(fields, extra)
    if isinstance(got, str):
        raise ValueError(f"{wfile}: {got}")
    v, t = got
    uv = extra[0] if extra else None                        # (decode_mesh's extra: [uv, primitive per triangle])
    diff = next((o for o in objs if o["type"] == make_jester.TEXTURE and make_jester.role_of(o["name"]) == "Diffuse"), None)
    mats = [(diff["name"] if diff else "untextured", tex_file(diff) if diff else -1)]
    V = np.asarray(v, np.float32)
    raw = V.copy()
    if place is not None:                          # on its attach bone - S = R (K v) + o; the mesh frame v = (S2, S0, S1)
        R, o = place
        S = weapon_local(raw) @ R.T + o
        V = S[:, [2, 0, 1]].astype(np.float32)
        return dict(name=wfile, slot="Weapon", verts=V, raw=raw, uv=np.asarray(uv if uv is not None else np.zeros((len(V), 2)), np.float32),
                    tris=np.asarray(t, np.uint32), tri_mat=np.zeros(len(t), np.int64), mats=mats)
    # upright beside the body: its long axis up, its bottom at the floor
    ext = V.max(0) - V.min(0)
    ax = int(np.argmax(ext))
    if ax != 2:
        order = [0, 1, 2]
        order[ax], order[2] = order[2], order[ax]
        V = V[:, order]
    V = V - np.array([(V[:, 0].min() + V[:, 0].max()) / 2 - 0.6, (V[:, 1].min() + V[:, 1].max()) / 2, V[:, 2].min()],
                     np.float32)
    return dict(name=wfile, slot="Weapon", verts=V, raw=raw, uv=np.asarray(uv if uv is not None else np.zeros((len(V), 2)), np.float32),
                tris=np.asarray(t, np.uint32), tri_mat=np.zeros(len(t), np.int64), mats=mats)


def _write_scene(path, parts, textures):
    """The scene cache (scene_cache.py's format, as library.asset_scene writes it): mesh k / entity k = part k at
    the origin, no collision."""
    import scene_cache as SC
    materials, meshes = [], []
    for k, p in enumerate(parts):
        base = len(materials)
        for j, (name, t) in enumerate(p["mats"]):
            materials.append((k * 1000 + j, name, t))
        tr, tm = np.asarray(p["tris"], np.uint32), np.asarray(p["tri_mat"], np.int64)
        order = np.argsort(tm, kind="stable")
        tr, tm = tr[order], tm[order]
        subs = [(base + int(m), int(np.nonzero(tm == m)[0][0]), int((tm == m).sum())) for m in np.unique(tm)]
        meshes.append((k + 1, p["name"], np.asarray(p["verts"], np.float32), np.asarray(p["uv"], np.float32), tr, subs))
    tmp = path + ".tmp"
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
        w.u32(0)                                   # (no collision shapes)
        w.u32(1)
        w.str("Character")
        w.str("")
        w.u32(len(parts))
        for k, p in enumerate(parts):
            w.u32(k + 1)
            w.str(p["name"])
            w.arr(np.eye(4, dtype=np.float32), "<f4")
            w.i32(k)
            w.u32(0)
            w.i32(0, -1)
            w.str("")
            w.str("")
            w.u32(0)
            w.str("")
    os.replace(tmp, path)


# -------------------------------------------------------------------------------------------------- animation --
BONE = 0x95741049             # the Bone class; a Skeleton's bones: <uid><Bone><name hash><parent ref><mirror ref>
                              # <model transform 32 B><local transform 32 B><12 B> (refs: 02 <uid> or 03 = none)
FPS = 30.0
AXIS_MODE = "abs"             # one-axis rotation tracks (codecs 0x15-0x17): "abs" the local rotation itself, "post" the
                              # rest's then the turn, "pre" the turn then the rest's (CHARVIEW_AXIS_MODE to try)


def skeleton_bones(body):
    """A Skeleton body -> [(hash, parent hash or None, local pos (3), local quat (x, y, z, w))] parents first (AC3's
    `Human Body` has 75 bones, the same in every persona; the second transform is the local one)."""
    out, uid_hash = [], {}
    p = 0
    while True:
        p = b_find(body, BONE, p)
        if p < 0:
            break
        uid, h = u32(body, p - 4), u32(body, p + 4)
        q = p + 8
        refs = []
        for _ in range(2):
            if body[q] == 2:
                refs.append(u32(body, q + 1))
                q += 5
            else:
                refs.append(None)
                q += 1
        if q + 64 > len(body):
            break
        lpos = struct.unpack_from("<3f", body, q + 32)
        lq = struct.unpack_from("<4f", body, q + 48)
        uid_hash[uid] = h
        out.append([h, refs[0], lpos, lq])
        p = q + 64
    bones = []
    for h, parent_uid, lpos, lq in out:
        ph = uid_hash.get(parent_uid) if parent_uid is not None else None
        bones.append((h, ph if ph != h else None, lpos, lq))
    return bones


def b_find(body, cls, start):
    return body.find(struct.pack("<I", cls), start)


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _qmat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]], np.float64)


def _mat_quat(R):
    """A rotation matrix (column vectors) -> (x, y, z, w)."""
    R = np.asarray(R, np.float64)
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        q = ((R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, 0.25 * s)
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        q = (0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s, (R[2, 1] - R[1, 2]) / s)
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        q = ((R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s, (R[0, 2] - R[2, 0]) / s)
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        q = ((R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s, (R[1, 0] - R[0, 1]) / s)
    q = np.asarray(q)
    return tuple(q / np.linalg.norm(q))


def _sample(times, vals, t, quat):
    """A track at time t (1/60 s): linear between keys (quaternions: normalised, the shorter way), held past its ends."""
    if len(times) == 1 or t <= times[0]:
        return vals[0]
    if t >= times[-1]:
        return vals[-1]
    i = int(np.searchsorted(times, t, side="right")) - 1
    t0, t1 = times[i], times[i + 1]
    f = (t - t0) / max(t1 - t0, 1e-9)
    a, b = np.asarray(vals[i], np.float64), np.asarray(vals[i + 1], np.float64)
    if quat:
        if float(np.dot(a, b)) < 0:
            b = -b
        q = a * (1 - f) + b * f
        return tuple(q / max(np.linalg.norm(q), 1e-12))
    return tuple(a * (1 - f) + b * f)


def clips(dv):
    """-> {clip name: record} of the menu clips (AC3MP_Menu_Common, then Menu_GotoGame)."""
    out = {}
    for rec in ("AC3MP_Menu_Common", "AC3MP_Menu_GotoGame"):
        for o in dv.objects(rec) if rec in dv.by_name else []:
            if o["body"][:2] == b"\x01\x02":
                out.setdefault(o["name"], rec)
    return out


def default_clip(pid, names):
    """The persona's own menu idle (AC3MP_ID75_Menu_idle, ID87_WB01_Menu_Idle, ID101_Menu_Menu_idle ...), else 87's."""
    own = [n for n in names if re.search(rf"(?i)(^|_)ID{pid}_(.*_)?menu_?idle$", n)]
    return own[0] if own else ("ID87_WB01_Menu_Idle" if "ID87_WB01_Menu_Idle" in names else None)


def animation(pid, rows=None, clip=None, log=print, meshes=None, materials=None):
    """-> dict(file (.ac3anim the app reads), clip, clips [names], frames, bones, seconds): the persona's look (as
    character(pid, rows) draws it) skinned by `clip` (default: its own menu idle). The file: 'AC3A', u32 version 1,
    nbones, nframes, f32 fps, u32 nparts; per part: u32 scene entity k, u32 corner vertices, then per corner 4 x (u16
    bone, u16 0, f32 weight, 3 f32 bone-local position); then nframes x nbones x 12 f32 (3 x 3 rotation row-major,
    then position: column vectors, the skeleton frame - z up, x forward). A corner's posed position =
    sum w (R_b p_local + P_b). Each part skins through its own palette (no shared binds needed)."""
    F, D, E = _forge()
    sys.path.insert(0, os.path.join(F, "acbchars"))
    info = character(pid, rows, log=log, meshes=meshes, materials=materials)
    # (character() answers from its cache without loading the overrides: load them here so the clip skins the
    # imported meshes, not the stock ones)
    _set_meshes(meshes, log, materials)
    t0 = time.time()
    import look_rows
    import mirror
    import part_render
    dv, ev = look_rows.BuildView(D), look_rows.BuildView(E)
    names = clips(dv)
    if clip == "@weapon":
        # the class's weapon-selection pose for the persona's sex (kit +0x12: 1 female) - the weapon held up
        cls = (info.get("weapon") or {}).get("cls", 1)
        sexes = ["female", "male"] if info.get("sex") == 1 else ["male", "female"]
        pick = None
        for sx in sexes:
            cand = sorted(n for n in names if f"WB{cls:02d}_Menu_Custom_Weapon_Selection_{sx}" in n and "Facial" not in n)
            if cand:
                pick = min(cand, key=lambda n: (not n.startswith(("ID99_", "AC3MP_ID99_")), len(n), n))   # (AC3's own)
                break
        clip = pick
    clip = clip or default_clip(pid, names)
    if clip not in names:
        raise ValueError(f"no menu clip {clip!r}")
    sig = re.sub(r"[^A-Za-z0-9_]", "_", clip)[:60]
    out_path = info["scene"][:-5] + f"_{sig}.ac3anim"
    res = out_path + ".json"
    if os.path.exists(res):
        return dict(json.load(open(res, encoding="utf-8")), clips=sorted(names))
    # the skeleton: Human Body (any persona's Main_Player: the same body) + the add-on rigs the look names
    main = f"AC3MP_ID{pid}_Main_Player" if f"AC3MP_ID{pid}_Main_Player" in dv.by_name else "AC3MP_ID87_Main_Player"
    hb = next(o for o in dv.objects(main) if o["name"] == "Human Body")
    bones = skeleton_bones(hb["body"])
    have = {b[0] for b in bones}
    addon_recs = {main}                                 # (its other rigs: the head's, FX)
    addon_recs.update(info.get("rigs") or [])           # the rows' Skeleton cells (coat, hands, hair ...)
    for part in info["parts"]:                          # (the part files' own Skeleton objects + *_Addon records)
        addon_recs.add(part["file"])
    for rec in list(dv.by_name):
        if rec.startswith(f"AC3MP_ID{pid}_") and "Addon" in rec:
            addon_recs.add(rec)
    w_info = info.get("weapon") or {}
    if w_info.get("rig"):
        addon_recs.add(w_info["rig"])                   # (the holster rig - WeaponRef)
    for rec in sorted(addon_recs):
        objs = dv.objects(rec) if rec in dv.by_name else (ev.objects(rec) if rec in ev.by_name else [])
        for o in objs:
            if b_find(o["body"], BONE, 0) < 0 or o["name"] == "Human Body":
                continue
            for h, ph, lp, lq in skeleton_bones(o["body"]):
                if h not in have and (ph is None or ph in have):
                    bones.append((h, ph, lp, lq))
                    have.add(h)
    # the add-on rigs' bones the clip doesn't drive (skirts, coat tails, strings: the game moves them by physics) hang
    # off their parent as they do in the meshes' bind, not at the rig's authored angle: local = bind(parent)^-1 bind(b),
    # the binds from the parts' palettes (loc = M V + t: a bone's bind frame is M^T, its origin -M^T t)
    src0 = _Source(dv, ev)
    part_render._texture = lambda *a: None
    binds = {}
    for name, slot in zip(info["part_names"], info["part_slots"]):
        if slot == "Weapon":
            continue
        try:
            mm = part_render.load_mesh(src0, name, name)
        except Exception:  # noqa: BLE001
            mm = None
        for bone, (c1, c2, c3), tt in (mm or {}).get("palette", []):
            if bone not in binds:
                Mb = np.array([c1, c2, c3], np.float64).T          # columns c1 c2 c3
                binds[bone] = (Mb, np.asarray(tt, np.float64))
    hb_set = {b[0] for b in skeleton_bones(hb["body"])}
    driven = set()
    clip_body = next(o for o in dv.objects(names[clip]) if o["name"] == clip)["body"]
    for key, typ, _ti, _v, _g in mirror.read_tracks(clip_body):
        driven.add(key)
    hung = 0
    for i, (h, ph, lp, lq) in enumerate(bones):
        if h in hb_set or h in driven or ph is None or h not in binds or ph not in binds:
            continue
        Mp, tp = binds[ph]
        Mb, tb = binds[h]
        Rl = Mp @ Mb.T                                  # the bone's frame in its parent's
        pl = Mp @ (-Mb.T @ tb) + tp                     # its origin in its parent's
        bones[i] = (h, ph, tuple(pl), _mat_quat(Rl))
        hung += 1
    index = {b[0]: i for i, b in enumerate(bones)}
    # the clip
    cobj = next(o for o in dv.objects(names[clip]) if o["name"] == clip)
    body = cobj["body"]
    n = u32(body, 4)
    length = struct.unpack_from("<f", body, 9 + 12 * n + 8)[0]
    tracks, axis_tracks = {}, set()
    for key, typ, times, vals, tag in mirror.read_tracks(body):
        if key and typ in ("T", "R"):
            tracks[(key, typ)] = (np.asarray(times, np.float64), vals)
            if typ == "R" and 0x15 <= tag <= 0x17:
                axis_tracks.add(key)                    # (one-axis rotations: see AXIS_MODE)
    axis_mode = os.environ.get("CHARVIEW_AXIS_MODE", AXIS_MODE)
    root = bones[0][0]
    nframes = max(2, int(round(length * FPS)))
    frames = np.zeros((nframes, len(bones), 12), np.float32)
    for f in range(nframes):
        t = f / FPS * 60.0
        world = []
        for i, (h, ph, lp, lq) in enumerate(bones):
            pos = _sample(*tracks[(h, "T")], t, False) if (h, "T") in tracks else lp
            rot = _sample(*tracks[(h, "R")], t, True) if (h, "R") in tracks else lq
            if h in axis_tracks and axis_mode == "skip":
                rot = lq
            elif h in axis_tracks and axis_mode != "abs":
                rot = _qmul(lq, rot) if axis_mode == "post" else _qmul(rot, lq)
            if h == root:                               # (root motion: the height kept, no drift, no turn)
                pos, rot = (0.0, 0.0, pos[2]), (0.0, 0.0, 0.0, 1.0)
            if ph is None or ph not in index:
                P, Q = np.asarray(pos, np.float64), rot
            else:
                Pp, Qp = world[index[ph]]
                P, Q = Pp + _qmat(Qp) @ np.asarray(pos, np.float64), _qmul(Qp, rot)
            world.append((P, Q))
            frames[f, i, :9] = _qmat(Q).reshape(-1)
            frames[f, i, 9:] = P
    # the parts: each corner's influences, from the part's raw mesh and its own palette (character()'s corner order)
    src = _Source(dv, ev)
    part_render._texture = lambda *a: None
    scene_names = info["part_names"]
    blobs, missing = [], set()
    for k, name in enumerate(scene_names):
        if info["part_slots"][k] == "Weapon":
            # rigid on its attach bone (weight 1): loc = K v, the bone posed by the clip
            wraw = info["scene"][:-5] + "_weapon.npy"
            bone = int(w_info.get("bone", "0"), 16) if w_info.get("bone") else None
            if bone is None or bone not in index or not os.path.exists(wraw):
                continue
            loc = weapon_local(np.load(wraw)).astype(np.float32)
            rec = np.zeros((len(loc), 4, 5), np.float32)
            bone_ix = np.zeros((len(loc), 4), np.uint16)
            rec[:, 0, 0] = 1.0
            rec[:, 0, 2:5] = loc
            bone_ix[:, 0] = index[bone]
            blobs.append((k, len(loc), bone_ix, rec))
            continue
        m = part_render.load_mesh(src, name, name)
        if m is None:
            continue
        palette_of = _vertex_palette(src, name, part_render)
        corners = []
        nv = len(m["verts"])
        for v0, i0, tn, _tex in m["subs"]:
            for t_ in range(tn):
                tri = [m["idx"][i0 + 3 * t_ + j] for j in range(3)]
                if max(v0 + a for a in tri) < nv:
                    corners += [v0 + a for a in tri]
        rec = np.zeros((len(corners), 4, 5), np.float32)
        bone_ix = np.zeros((len(corners), 4), np.uint16)
        pal = m["palette"]
        for ci, vi in enumerate(corners):
            x, y, z = m["verts"][vi][:3]
            sk = palette_of[vi] if palette_of is not None else m["skin"][vi]
            infl = [(pk, w) for pk, w in sk if pk < len(pal) and pal[pk][0] in index][:4]
            missing.update(pal[pk][0] for pk, _w in sk if pk < len(pal) and pal[pk][0] not in index)
            wsum = sum(w for _p, w in infl) or 1.0
            for j, (pk, w) in enumerate(infl):
                bone, (c1, c2, c3), tt = pal[pk]
                if bone not in index:
                    missing.add(bone)
                    continue
                loc = (c1[0] * y + c2[0] * z + c3[0] * x + tt[0], c1[1] * y + c2[1] * z + c3[1] * x + tt[1],
                       c1[2] * y + c2[2] * z + c3[2] * x + tt[2])
                bone_ix[ci, j] = index[bone]
                rec[ci, j, 0] = w / wsum
                rec[ci, j, 2:5] = loc
        blobs.append((k, len(corners), bone_ix, rec))
    with open(out_path, "wb") as fh:
        fh.write(b"AC3A")
        fh.write(struct.pack("<IIIfI", 1, len(bones), nframes, FPS, len(blobs)))
        for k, nc, bix, rec in blobs:
            fh.write(struct.pack("<II", k, nc))
            arr = np.zeros((nc, 4, 5), np.float32)
            arr[:, :, :] = rec
            raw = arr.view(np.uint32).copy()
            raw[:, :, 1] = 0
            raw[:, :, 0] = arr[:, :, 0].view(np.uint32)
            # per influence: u16 bone, u16 0, f32 w, 3 f32 loc
            out = bytearray()
            for ci in range(nc):
                for j in range(4):
                    out += struct.pack("<HHf3f", int(bix[ci, j]), 0, float(rec[ci, j, 0]), *map(float, rec[ci, j, 2:5]))
            fh.write(out)
        fh.write(frames.tobytes())
    r = dict(file=out_path, clip=clip, frames=nframes, bones=len(bones), seconds=round(length, 2), hung=hung,
             missing_bones=len(missing), parts=len(blobs), made_in=round(time.time() - t0, 1))
    json.dump(r, open(res, "w", encoding="utf-8"), indent=1)
    log(f"animation {pid}: {clip}, {nframes} frames, {len(bones)} bones ({len(missing)} palette bone(s) not in the "
        f"skeleton), {len(blobs)} part(s), {r['made_in']} s")
    return dict(r, clips=sorted(names))


def _vertex_palette(src, name, part_render):
    """Each vertex's (palette index, weight) - its bone bytes through the bone list of the sub-mesh whose TRIANGLES use
    it. part_render.load_mesh takes a vertex's sub-mesh from the records' first-vertex / count, but sub-meshes can all
    say first vertex 0 with absolute indices (Lady Maverick's MainPart_V1: 0 / 1168 and 0 / 1208 of 2376), which would
    give a vertex the other sub-mesh's list - invisible in the bind pose, arms and coat torn apart once posed."""
    import skinmesh
    objs = src.objects(name)
    by_uid = {o["uid"]: o for o in objs}
    sel = next((o for o in objs if o["name"] == name and o["type"] == src.lodsel), None)
    meshes = [o for o in part_render._refs(sel["body"], by_uid) if o["type"] == skinmesh.MESH] if sel else []
    if not meshes:
        return None
    b = meshes[0]["body"]
    lay = skinmesh.layout(b)
    vb, st, nv = lay["vb"], lay["stride"], lay["nv"]
    lists = _bone_lists15(b, lay)
    rec = lay["post"] + 32
    subs = [tuple(struct.unpack_from("<I", b, rec + 32 * k + q)[0] for q in (8, 12, 16, 20)) for k in range(lay["subs"])]
    idx = struct.unpack_from(f"<{lay['ni']}H", b, lay["ib"])
    sub_of = [None] * nv
    for k, (v0, _vn, i0, tn) in enumerate(subs):
        for i in idx[i0:i0 + 3 * tn]:
            if v0 + i < nv and sub_of[v0 + i] is None:
                sub_of[v0 + i] = k
    out = []
    for i in range(nv):
        k = sub_of[i] if sub_of[i] is not None else 0
        lst = lists[k]
        bi, wi = b[vb + st * i + 24:vb + st * i + 28], b[vb + st * i + 28:vb + st * i + 32]
        out.append(tuple((lst[bi[j]], wi[j] / 255) for j in range(4) if wi[j] and bi[j] < len(lst)))
    return out


BONE_LIST = 0x0B1D34C1


def _bone_lists15(b, lay):
    """Each sub-mesh's bone list: after the 0x0B1D34C1 marker, a u16 count at +5 and the palette indices from **+15**
    (the first is the root, 0). part_render._bone_lists reads from +16, one entry late with the last dropped, which
    gives every vertex the next bone (unnoticed in the bind pose, torn limbs once posed). Identity when unreadable."""
    lists, p = [], lay["end"]
    while len(lists) < lay["subs"]:
        p = b.find(struct.pack("<I", BONE_LIST), p)
        if p < 0:
            break
        n = struct.unpack_from("<H", b, p + 5)[0]
        lst = list(b[p + 15:p + 15 + n])
        lists.append(lst if len(lst) == n and all(i < lay["count"] for i in lst) else None)
        p += 15 + n
    return [lists[k] if k < len(lists) and lists[k] is not None else list(range(lay["count"]))
            for k in range(lay["subs"])]


def run_child(args, log=print):
    """This file in a child process (forgelib's readers with their decompress cache off stay out of the worker);
    its last stdout line is the JSON answer."""
    import subprocess
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    p = subprocess.run([sys.executable, os.path.abspath(__file__)] + args + ["--json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    for ln in lines[:-1]:
        log(ln)
    if p.returncode or not lines:
        raise RuntimeError("characters: " + (p.stderr.strip().splitlines() or ["failed"])[-1])
    return json.loads(lines[-1])


def main(argv):
    if "--json" in argv:
        argv = [a for a in argv if a != "--json"]
        if argv and argv[0] == "personas":
            print(json.dumps(dict(personas=personas())))
            return 0
        if len(argv) >= 2 and argv[0] == "character":
            print(json.dumps(character(int(argv[1]), json.loads(argv[2]) if len(argv) > 2 else None,
                                       colours=json.loads(argv[3]) if len(argv) > 3 and argv[3] else None,
                                       meshes=json.loads(argv[4]) if len(argv) > 4 and argv[4] else None,
                                       weapons=json.loads(argv[5]) if len(argv) > 5 and argv[5] else None,
                                       materials=json.loads(argv[6]) if len(argv) > 6 and argv[6] else None)))
            return 0
        if len(argv) >= 2 and argv[0] == "animation":
            print(json.dumps(animation(int(argv[1]), json.loads(argv[2]) if len(argv) > 2 else None,
                                       argv[3] if len(argv) > 3 and argv[3] else None,
                                       meshes=json.loads(argv[4]) if len(argv) > 4 and argv[4] else None,
                                       materials=json.loads(argv[5]) if len(argv) > 5 and argv[5] else None)))
            return 0
    if argv and argv[0] == "personas":
        for r in personas():
            print(f"{r['id']:4d}  {r['name']:30s} {r['internal']}{'' if r['kit'] else '  (no kit)'}")
        return 0
    if len(argv) >= 2 and argv[0] == "character":
        r = character(int(argv[1]))
        print(json.dumps({k: v for k, v in r.items() if k != "parts"}, indent=1))
        for p in r["parts"]:
            print(f"  {p['slot']:10s} row {p['row']}  {p['file']:44s} {p.get('triangles', 0):6d}  {p.get('textures', [])}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
