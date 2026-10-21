"""Reforged Studio's singleplayer library: the SP world's models (Boston, New York, the Frontier, Homestead, ...) as
library assets to place in MP maps. Read from the game install, never shipped: the Studio holds no game data -
pointed at an install (AC3_GAME_DIR, else the singleplayer, reference or live install forgelib's paths names), it
reads what's there.

The SP archives are a newer container (v27: 64-bit ids, LZO1X) - read with forgelib's spmaps readers (forge27,
codec27, sp_topview, sp_assets), imported read-only. Their caches go to the Studio's own cache
(<workspace>/cache/studio/sp/), seeded once from spmaps' own cache when it has the same archive (nothing is written
into forgelib's folders).

* **Index** (library.index_path, as an MP map's): every model the archive's Cell DataBlocks place - one entry per mesh
  record an entity names (records named ..._LOD<n>: the lowest-numbered one is the model). Its size is the first
  entity's own box, `uses` how many entities show it. Left out (sp_topview.SKIP and more): stand-ins / fakes, skies,
  effects, occluders, shadow proxies, god rays, mission layers (only the always-there world is read). Triangle counts
  aren't known until a model is read (0 in the list).
* **Extract** (library.asset_dir): the mesh's triangle lists (sp_assets.mesh_parts: vertices in metres, UVs, each
  list's material) as one scene part; each material's colour map (spmaps' sp_texture rules: a flat-colour map takes the
  material's tint) as a DDS (DXT1, DXT5 with alpha) the Studio draws; collision = its visible triangles (none for
  foliage); no climb edges of its own - SP climbs sill by sill, MP by invisible handhold rows: SP models default to
  "generated" climbing.
  Skinned meshes (formats 0 / 1: flags, cloth, trees that sway) are left out.

    python studio/engine/splib.py maps                      the install's SP archives
    python studio/engine/splib.py index <archive>           (library.py index runs this for an SP archive)
"""
import json
import os
import re
import shutil
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ac3editor.workspace import WORK  # noqa: E402  (the workspace: builds, caches)
from ac3editor import mapdata  # noqa: E402  (forgelib on the path)

CACHE = os.path.join(WORK, "cache", "studio", "sp")
SHARED = ("DataPC_DX11.forge", "DataPC_DX11_extra.forge")
NOT_MAPS = ("DataPC_DX11.forge", "DataPC_DX11_extra.forge", "DataPC_DX11_extra_chr.forge",
            "DataPC_DX11_TitleScreen.forge")
FRIENDLY = {"BunkerHill": "Bunker Hill", "NewYork": "New York", "NewYork_Under": "New York underground",
            "Boston_Under": "Boston underground", "FortWolcott": "Fort Wolcott (SP)", "GhostShip": "Ghost ship",
            "ModernTower": "Modern tower", "OakIsland": "Oak Island", "PadreIsland": "Padre Island",
            "SpiritJourney": "Spirit journey", "FCTemple": "First Civ temple", "NavCarSea": "Naval: Caribbean",
            "NavChesaBay": "Naval: Chesapeake", "NavGenOcean": "Naval: open ocean", "NavNACoast": "Naval: N. American coast",
            "NavPrivCar": "Naval: privateer", "WhiteRoom": "White room"}
LEAVE = re.compile(r"Fake|SkyDome|Sky_Dome|GFX_|Crowd|_OOB|OutOfBound|Border|_Sky|Skybox|Occlu|Mist|Fog|_TRG|Trigger|"
                   r"Volume|NavPoint|Portal|Blocker|PROXY|_SHW|GodRay|Matte|Collision|_COL_|LowRes|Shadow", re.I)
FOLIAGE = re.compile(r"_RT_|Palm|Veg|Bush|Leaves|Leaf|Flower|Tree|Grass|Plant|Hedge|Ivy|Cornstalk|Fern|Pine|Oak|Birch|"
                     r"Spruce|Sumac|Twig|Weed|Shrub|Moss", re.I)
LOD = re.compile(r"^(.*)_LOD(\d+)$")
INDEX_VERSION = 1


def game_dir():
    """The install the SP archives are read from: AC3_GAME_DIR, else the one of the reference / live installs that
    holds DataPC_DX11.forge (the shared SP archive)."""
    cands = [os.environ.get("AC3_GAME_DIR")]
    try:
        import paths  # forgelib's (the config's singleplayer folder)
        cands += [getattr(paths, "AC3_SINGLEPLAYER", "")]
        cands += [os.path.dirname(p) for p in (paths.AC3_PRISTINE, paths.AC3) if p]
    except Exception:  # noqa: BLE001
        pass
    for d in cands:
        if d and os.path.exists(os.path.join(d, SHARED[0])):
            return d
    return None


def is_sp(archive):
    b = os.path.basename(archive)
    return b.startswith("DataPC_DX11_") and b.endswith(".forge")


def title(archive):
    key = os.path.basename(archive)[len("DataPC_DX11_"):-len(".forge")]
    key = key[4:] if key.startswith("AC3_") else key
    return "SP: " + FRIENDLY.get(key, re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key.replace("_", " ")))


def sp_maps():
    """-> [dict(path, title, source, kind="sp")] of the install's SP archives."""
    d = game_dir()
    if not d:
        return []
    out = []
    for n in sorted(os.listdir(d)):
        if n.startswith("DataPC_DX11_") and n.endswith(".forge") and n not in NOT_MAPS:
            p = os.path.join(d, n)
            out.append(dict(path=p, title=title(p), source="singleplayer (read from the install)", kind="sp"))
    return out


# ------------------------------------------------------------------------------------------- the readers --

def _readers():
    """spmaps' modules, their caches pointed at the Studio's (seeded from spmaps' own where it has them)."""
    sp = os.path.join(mapdata.FORGE_REPO, "spmaps")
    if sp not in sys.path:
        sys.path.insert(0, sp)
    import sp_topview as T
    import sp_assets as SA
    theirs = os.path.join(T.OUT, "cache")
    if T.OUT != CACHE:
        T.OUT = CACHE
    os.makedirs(os.path.join(CACHE, "cache"), exist_ok=True)
    return T, SA, theirs


def _seed(T, theirs, archive, kind, version):
    """spmaps' cache file for this archive (same name, same archive stamp inside) copied to ours once."""
    name = f"{T.stem(archive)}_{kind}_v{version}.pkl"
    src, dst = os.path.join(theirs, name), os.path.join(CACHE, "cache", name)
    if not os.path.exists(dst) and os.path.exists(src):
        shutil.copyfile(src, dst)


def _model_ref(refs, owner, records):
    """The model an entity shows: of the mesh records it names (..._LOD<n>), the lowest LOD -> (uid, base name)."""
    best = None
    for u in refs:
        if owner.get(u) != u:
            continue
        m = LOD.match(records.get(u, ""))
        if m and (best is None or int(m.group(2)) < best[2]):
            best = (u, m.group(1), int(m.group(2)))
    return best


def build_index(archive, out_path, log=print):
    """-> the library index of an SP archive (library.build_index's form), written to out_path."""
    T, _SA, theirs = _readers()
    _seed(T, theirs, archive, "entities", T.CACHE_VERSION)
    d = T.index(archive, log=log)
    owner, records = d["owner"], d["records"]
    reps, uses = {}, {}
    for name, rec, m, lo, hi, refs in d["entities"]:
        if LEAVE.search(name):
            continue
        r = _model_ref(refs, owner, records)
        if r is None or LEAVE.search(r[1]):
            continue
        uses[r[0]] = uses.get(r[0], 0) + 1
        if r[0] not in reps:
            reps[r[0]] = (r[1], name, lo, hi)
    entries = []
    for u, (mname, ename, lo, hi) in reps.items():
        lo, hi = np.asarray(lo, np.float64), np.asarray(hi, np.float64)
        size = hi - lo
        if not (size > 0).any() or size.max() > 300:
            continue
        entries.append(dict(mesh=f"{u:016x}", name=mname, nv=0, nt=0,
                            size=[round(float(x), 2) for x in size], lo=[round(float(x), 3) for x in lo],
                            uses=uses[u], materials=[], n_materials=0, textured=0, rep="", rep_name=ename, group="",
                            copyable=False, collision="none" if FOLIAGE.search(mname) else "visible",
                            ground=bool(re.search(r"Ground|Terrain|Chunk|Road|Street", mname, re.I)), sp=True))
    entries.sort(key=lambda x: x["name"].lower())
    out = dict(version=INDEX_VERSION, archive=os.path.abspath(archive), title=title(archive), entries=entries, sp=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f)
    os.replace(tmp, out_path)
    log(f"library: {title(archive)}: {len(entries)} models ({len(d['entities'])} placed objects read)")
    return out


# ------------------------------------------------------------------------------------------- extraction --

DDS_DXT = {1: b"DXT1", 5: b"DXT5"}


def write_dds(path, rgba):
    """(h, w, 4) uint8, top row first -> a one-mip DDS: DXT1 when opaque, else DXT5 (materials' encoders)."""
    from ac3editor import materials
    a = rgba[:, :, 3]
    h, w = rgba.shape[:2]
    if (a == 255).all():
        blocks, four, bs = materials.encode_colour(rgba[:, :, :3]), b"DXT1", 8
    else:
        blocks = np.concatenate([materials.encode_alpha5(a), materials.encode_colour(rgba[:, :, :3])], 1)
        four, bs = b"DXT5", 16
    linear = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bs
    hdr = struct.pack("<4sIIIIIII44s", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000, h, w, linear, 0, 1, bytes(44))
    pf = struct.pack("<II4sIIIII", 32, 0x4, four, 0, 0, 0, 0, 0)
    caps = struct.pack("<IIIII", 0x1000, 0, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(hdr + pf + caps + blocks.tobytes())


def _texture(A, mat_uid, max_size=512):
    """An SP material's colour map by spmaps' rules (sp_convert: sp_texture, bake_layers, fill_holes - copied, not
    imported: the converter rearranges sys.path for its own builds): a flat-colour map takes the material's Layer0
    tint; a two-layer "_Multi" material's first layer is blended over its second by its alpha, then opaque (the first
    layer alone shows a pale smear); black holes SP cuts in a mostly-solid texture are filled from round them.
    -> (name, rgba) or None."""
    from PIL import Image
    tu = A.material_texture_uid(mat_uid) if mat_uid else None
    tx = A.texture_rgba(tu, max_size=max_size) if tu else None
    if not tx:
        return None
    name, rgba = tx[0], np.array(tx[1], copy=True)
    if re.search(r"FlatColor", name or "", re.I):
        c = A.material_params(mat_uid).get("Layer0_Color")
        if c:
            rgba[:, :, :3] = np.clip(np.round(rgba[:, :, :3] * np.array(c[:3]) * 2.0), 0, 255).astype(np.uint8)
    lay = A.material_layers(mat_uid)
    if len(lay) >= 2:
        tb = A.texture_rgba(lay[1], max_size=max_size)
        if tb:
            b2 = np.array(Image.fromarray(tb[1]).resize((rgba.shape[1], rgba.shape[0]), Image.BILINEAR), np.float32)
            a = rgba[:, :, 3:4].astype(np.float32) / 255.0
            rgba[:, :, :3] = np.clip(np.round(rgba[:, :, :3].astype(np.float32) * a + b2[:, :, :3] * (1 - a)), 0,
                                     255).astype(np.uint8)
            rgba[:, :, 3] = 255
    hole = rgba[:, :, 3] < 128
    if 0 < hole.mean() <= 0.25 and float(rgba[:, :, :3][hole].mean()) <= 24:
        col, solid = rgba[:, :, :3].astype(np.float32), ~hole
        for _ in range(64):
            if solid.all():
                break
            acc, n = np.zeros_like(col), np.zeros(hole.shape, np.float32)
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                sv = np.roll(solid, (dy, dx), (0, 1))
                acc += np.roll(col, (dy, dx), (0, 1)) * sv[:, :, None]
                n += sv
            grow = ~solid & (n > 0)
            col[grow] = acc[grow] / n[grow][:, None]
            solid = solid | grow
        if not solid.all():
            col[~solid] = col[solid].mean(0)
        rgba[:, :, :3] = np.clip(np.round(col), 0, 255).astype(np.uint8)
    return name, rgba


def extract(archive, hexes, folder_of, version, log=print):
    """SP models -> asset folders (library.extract's form). folder_of(hex) -> the folder. -> [folder]."""
    from ac3editor import sceneport
    T, SA, theirs = _readers()
    for a in (archive,) + tuple(os.path.join(os.path.dirname(archive), s) for s in SHARED):
        _seed(T, theirs, a, "owners", 1)
    shared = tuple(os.path.join(os.path.dirname(archive), s) for s in SHARED)
    A = SA.Assets(archive, shared=shared)
    out = []
    for h in hexes:
        folder = folder_of(h)
        if os.path.exists(os.path.join(folder, "asset.json")):
            out.append(folder)
            continue
        uid = int(h, 16)
        o = A.obj(uid)
        name = re.sub(r"_LOD\d+$", "", o["name"]) if o else h
        parts = A.mesh_parts(uid)
        if not parts:
            log(f"asset: {name} from {os.path.basename(archive)}: no static mesh to read (skinned or not a mesh) - left out")
            continue
        tmp = folder + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        V, Tr, U, TM, keys, mat_specs, mat_info = [], [], [], [], [], {}, []
        n = 0
        for p in parts:
            mu = p["material"]
            key = f"{mu:016x}" if mu else f"none{p['primitive']}"
            if key not in mat_specs:
                mname = (A.obj(mu) or {}).get("name", "untextured") if mu else "untextured"
                tx = _texture(A, mu) if mu else None
                if tx is not None:
                    fn = f"{key}.dds"
                    write_dds(os.path.join(tmp, fn), tx[1])
                else:
                    fn = f"{key}.png"
                    from PIL import Image
                    Image.fromarray(np.full((8, 8, 4), (128, 128, 124, 255), np.uint8)).save(os.path.join(tmp, fn))
                mat_specs[key] = dict(image=os.path.join(tmp, fn), name=mname.strip())
                mat_info.append(dict(key=key, name=mname.strip(), texture=fn, textured=fn.endswith(".dds")))
                keys.append(key)
            V.append(np.asarray(p["verts"], np.float64))
            Tr.append(np.asarray(p["tris"], np.int64) + n)
            U.append(np.asarray(p["uv"], np.float64))
            TM.append(np.full(len(p["tris"]), keys.index(key), np.int64))
            n += len(p["verts"])
        v, t = np.vstack(V), np.vstack(Tr)
        lo, hi = v.min(0), v.max(0)
        foliage = bool(FOLIAGE.search(name))
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name)[:36]
        part = dict(name=f"ED_Lib_{safe}", verts=v, tris=t, uv=np.vstack(U), tri_mat=np.concatenate(TM),
                    materials=keys, collision="none" if foliage else None, climb=True)
        sceneport.save(tmp, dict(parts={"asset": part}, instances=[], materials=mat_specs))
        import library
        library.save_edges(tmp, [])
        meta = dict(version=version, archive=os.path.abspath(archive), mesh=h, name=name, native_edges=0,
                    nv=int(len(v)), nt=int(len(t)), lo=lo.tolist(), hi=hi.tolist(), sp=True,
                    collision="none" if foliage else "visible", collision_tris=0 if foliage else int(len(t)),
                    rep=None, materials=mat_info)
        with open(os.path.join(tmp, "asset.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(os.path.dirname(folder), exist_ok=True)
        os.replace(tmp, folder)
        log(f"asset: {name} from {os.path.basename(archive)} ({len(t)} triangles, {len(mat_info)} material(s), "
            f"collision {meta['collision']}; SP: MP handhold rows when climbable)")
        out.append(folder)
    return out


def main(argv):
    if argv and argv[0] == "maps":
        print("game folder:", game_dir())
        for m in sp_maps():
            print(f"  {m['title']:32s} {os.path.getsize(m['path']) / 1e6:6.0f} MB  {m['path']}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
