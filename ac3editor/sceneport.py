"""A whole area from another game (or another AC3 map) as new objects on an AC3 map, in one call. The entry point
for porting a scene (its forms: build_scene's docstring; on disk: save / load):

    from ac3editor import sceneport
    rep = sceneport.build_scene(src_archive, dest_archive, scene, placement="single")   # dry_run=True: estimate only
    sceneport.save(folder, scene) / sceneport.load(folder)                               # the on-disk form

Steps: the scene's two accepted shapes (instanced: parts + instances; flat: one placed model each, identical
geometry found and shared) -> parts with scale and mirroring baked in (Havok shapes don't scale; a mirrored instance
gets its own part, triangles re-wound) and parts too big for one collision shape cut on a grid -> materials: stock
ones by name / uid, new ones from pictures (identical pictures shared; a picture with see-through pixels goes on the
cut-out base and keeps them: DXT1 1-bit alpha or the base's DXT5) -> the size estimate (refuses over `max_mb`) ->
materials.apply -> scenebuild.build -> archive.pack (LZO, as the game's own files: rewritten records are otherwise
stored at their raw size).
"""
import collections
import hashlib
import json
import os

import numpy as np

from . import archive, climb, materials, newmodel, scenebuild
from . import mapdata as M


# ------------------------------------------------------------------------------------------------ the scene --

def load_image(img):
    """A picture (path, or an (H, W, 3 / 4) array) -> (H, W, 4) uint8, top row first."""
    if isinstance(img, np.ndarray):
        a = img
    else:
        from PIL import Image
        with Image.open(img) as im:
            a = np.asarray(im.convert("RGBA"))
    if a.ndim == 3 and a.shape[2] == 3:
        a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, a.dtype)], 2)
    if a.dtype != np.uint8:
        a = np.clip(np.rint(a * (255.0 if a.max() <= 1.0 else 1.0)), 0, 255).astype(np.uint8)
    return a


def is_cutout(rgba, clear=0.002, soft=0.1):
    """A picture whose alpha cuts holes (leaves, railings, posters): some pixels see-through (alpha < 128) and the
    alpha mostly all-or-nothing. Many game textures keep a blend / gloss mask in alpha instead (smooth values):
    those are opaque."""
    a = rgba[:, :, 3]
    return bool((a < 128).mean() > clear and ((a > 24) & (a < 232)).mean() < soft)


def _geo_hash(*arrays):
    h = hashlib.sha1()
    for a in arrays:
        if a is not None:
            a = np.ascontiguousarray(a)
            h.update(str(a.shape).encode())
            h.update(a.tobytes())
    return h.hexdigest()[:16]


def from_flat(models):
    """The flat form - [dict(name, verts, tris, uv, material (key) or materials + tri_mat, matrix, collision?, climb?,
    holds?)] - as parts + instances: models with identical geometry, UVs and materials share one part."""
    parts, instances = {}, []
    for m in models:
        mats = list(m.get("materials") or [m["material"]])
        key = _geo_hash(np.asarray(m["verts"], np.float32), np.asarray(m["tris"], np.int32),
                        None if m.get("uv") is None else np.asarray(m["uv"], np.float32),
                        None if m.get("tri_mat") is None else np.asarray(m["tri_mat"], np.int32)) + "|" + "|".join(mats)
        if key not in parts:
            p = {k: v for k, v in m.items() if k not in ("matrix", "material")}
            p["materials"] = mats
            parts[key] = p
        instances.append(dict(part=key, name=m["name"], matrix=m["matrix"]))
    return parts, instances


def _decompose(m4):
    """AC3 row-vector 4x4 -> (rotation rows (3, 3), per-axis scale (3,) - a mirror flips the first)."""
    m = np.asarray(m4, np.float64).reshape(4, 4)
    s = np.linalg.norm(m[:3, :3], axis=1)
    r = m[:3, :3] / s[:, None]
    if np.linalg.det(r) < 0:
        s[0], r[0] = -s[0], -r[0]
    return r, s


LEDGE_BUDGET = 500          # ledges per object, room left under stock's biggest climb system (636) for handholds


def _climb_sized(p, max_verts):
    """split_part, then again smaller while a piece's generated ledges pass LEDGE_BUDGET (stock's biggest climb
    system: 636 edges - climb.STOCK_MAX)."""
    pieces = scenebuild.split_part(p, max_verts)
    if not p.get("climb", True) or isinstance(p.get("collision"), (tuple, str)):
        return pieces
    out = []
    for pp, off in pieces:
        v, t = scenebuild.weld(pp["verts"], pp["tris"])
        n = len(climb.ledges(v, t, min_len=0.2))
        if n <= LEDGE_BUDGET or len(v) < 200:
            out.append((pp, off))
            continue
        cap = max(200, int(len(v) * LEDGE_BUDGET / n * 0.8))
        for sub, off2 in _climb_sized(dict(pp, name=pp["name"]), cap) if cap < len(v) else [(pp, np.zeros(3))]:
            out.append((sub, off + off2))
    return out


def prepare(parts, instances, max_verts=60000, log=print):
    """Parts + instances as scenebuild takes them: each instance's scale (and mirroring) baked into a part of its own
    per distinct scale, every matrix left rotation + translation; big parts cut (scenebuild.split_part)."""
    baked, out_inst = {}, []
    for inst in instances:
        r, s = _decompose(inst["matrix"])
        key = (inst["part"], tuple(np.round(s, 4)))
        if key not in baked:
            p = dict(parts[inst["part"]])
            v = np.asarray(p["verts"], np.float64) * s
            t = np.asarray(p["tris"], np.int64)
            if np.prod(np.sign(s)) < 0:
                t = t[:, ::-1]
            p["verts"], p["tris"] = v, t
            if isinstance(p.get("collision"), (tuple, list)):
                cv, ct = p["collision"]
                ct = np.asarray(ct)[:, ::-1] if np.prod(np.sign(s)) < 0 else ct
                p["collision"] = (np.asarray(cv, np.float64) * s, ct)
            if p.get("holds"):
                p["holds"] = [tuple(np.asarray(x, np.float64) * (s if k < 2 else np.sign(s)) for k, x in enumerate(h))
                              for h in p["holds"]]
            if p.get("edges"):                       # climb edges given by the caller (a model's own): scaled too
                def _n(x, s=s):
                    x = np.asarray(x, np.float64) / s
                    return x / max(np.linalg.norm(x), 1e-9)
                p["edges"] = [(np.asarray(e[0], np.float64) * s, np.asarray(e[1], np.float64) * s, _n(e[2]), _n(e[3]))
                              + tuple(e[4:]) for e in p["edges"]]
                if np.prod(np.sign(s)) < 0:          # mirrored: a -> b runs along n0 x n1 again
                    p["edges"] = [(e[1], e[0]) + tuple(e[2:]) for e in p["edges"]]
            if not np.allclose(s, 1):
                p["name"] = f"{p['name']}_S{len(baked):03d}"
            baked[key] = _climb_sized(p, max_verts)
        m = np.eye(4)
        m[:3, :3] = r
        m[3, :3] = np.asarray(inst["matrix"], np.float64).reshape(4, 4)[3, :3]
        for j, (_pp, off) in enumerate(baked[key]):
            mj = m.copy()
            mj[3, :3] = m[3, :3] + off @ r
            out_inst.append(dict(inst, part=f"{key[0]}#{key[1]}#{j}", matrix=mj,
                                 name=inst["name"] if j == 0 else f"{inst['name']}_{j}"))
    out_parts = {f"{k[0]}#{k[1]}#{j}": pp for k, pieces in baked.items() for j, (pp, _o) in enumerate(pieces)}
    log(f"scene: {len(instances)} objects, {len(parts)} parts -> {len(out_parts)} parts after scale / size, "
        f"{len(out_inst)} objects")
    return out_parts, out_inst


def open_tops(parts, instances, cell=0.5, below=2.5, above=3.0, need=0.5):
    """Climbable objects whose top players can reach (taller than the first handhold row) but nothing walkable covers:
    sampled every `cell` m inside each one's footprint (its box less 0.5 m), a point is covered when some up-facing
    surface of ANY object of the scene lies between `below` m under its top and `above` m over it (a roof brought in
    as an object of its own counts). E.g. a house's walls brought in without their roof object: climbers stand on the
    wall tops and fall inside. -> [(object name, covered share)]."""
    up_tris = []
    for inst in instances:
        p = parts[inst["part"]]
        m = np.asarray(inst["matrix"], np.float64).reshape(4, 4)
        v = np.asarray(p["verts"], np.float64) @ m[:3, :3] + m[3, :3]
        t = np.asarray(p["tris"], np.int64)
        tri = v[t]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        nz = n[:, 2] / np.maximum(np.linalg.norm(n, axis=1), 1e-12)
        up_tris.append(tri[nz > 0.7])
    up = np.concatenate(up_tris) if up_tris else np.zeros((0, 3, 3))
    out = []
    for inst in instances:
        p = parts[inst["part"]]
        if not p.get("climb", True) or p.get("collision") == "none":
            continue
        m = np.asarray(inst["matrix"], np.float64).reshape(4, 4)
        v = np.asarray(p["verts"], np.float64) @ m[:3, :3] + m[3, :3]
        lo, hi = v.min(0), v.max(0)
        if hi[2] - lo[2] < climb.FIRST_ROW + 0.4 or (hi - lo)[:2].min() < 2.0:
            continue
        xs = np.arange(lo[0] + 0.5, hi[0] - 0.5, cell)
        ys = np.arange(lo[1] + 0.5, hi[1] - 0.5, cell)
        if not len(xs) or not len(ys):
            continue
        near = up[(up[:, :, 2].max(1) >= hi[2] - below) & (up[:, :, 2].min(1) <= hi[2] + above)
                  & (up[:, :, 0].max(1) >= lo[0]) & (up[:, :, 0].min(1) <= hi[0])
                  & (up[:, :, 1].max(1) >= lo[1]) & (up[:, :, 1].min(1) <= hi[1])]
        P = np.array([(x, y) for x in xs for y in ys])
        cov = np.zeros(len(P), bool)
        for tri in near:
            a, b, c = tri[:, :2]
            v0, v1 = b - a, c - a
            d = v0[0] * v1[1] - v1[0] * v0[1]
            if abs(d) < 1e-12:
                continue
            w = P - a
            u_ = (w[:, 0] * v1[1] - v1[0] * w[:, 1]) / d
            v_ = (v0[0] * w[:, 1] - w[:, 0] * v0[1]) / d
            cov |= (u_ >= 0) & (v_ >= 0) & (u_ + v_ <= 1)
        share = float(cov.mean())
        if share < need:
            out.append((inst["name"], round(share, 2)))
    return out


# ------------------------------------------------------------------------------------------------ materials --

def material_table(src):
    """-> {material uid: dict(name, template name, colour map (w, h, DXT format), writable, uses)} of a map."""
    import dxt  # noqa: E402  (forgelib)
    md = M.load(src, log=lambda *_x: None)
    sv = newmodel.survey(src, log=lambda *_x: None)
    tmpl, mat_fields = {}, {}
    for _rec, name, t, body, _f in M.iter_objects_ex(src):
        cn = sv.S.name_of(t) if t in sv.S.types_by_hash else None
        if cn == "MaterialTemplate":
            tmpl[body[2:6]] = name
        elif cn == "Material" and body[2:6] not in mat_fields:
            f = sv.C.decode(body).obj.fields
            mat_fields[body[2:6]] = dict(alpha_test=f.get("AlphaTestEnabled", b"\x00") != b"\x00",
                                         two_sided=f.get("TwoSided", b"\x00") != b"\x00")
    uses = collections.Counter(m for _u, (_uv, _p, mats) in md.mesh_extra.items() for m in mats)
    out = {}
    for m, (name, _path) in md.materials.items():
        ts, tp = sv.mat_parts.get(m, (None, None))
        maps = sv.set_maps.get(ts, [])
        tex = None
        if maps and maps[0] in sv.obj_bytes:
            data = sv.obj_bytes[maps[0]][1]
            try:
                hd = dxt.header(data[12 + int.from_bytes(data[8:12], "little"):])
                tex = (hd["width"], hd["height"], hd["format"])
            except Exception:  # noqa: BLE001 - not a readable map: no texture info
                tex = None
        fmt = None
        if m in sv.mesh_by_mat:
            fmt = M._obj(sv.roots[sv.mesh_by_mat[m]][3].obj.fields["CompiledMesh"]).fields["MeshData"] \
                .fields["VertexFormat"][0]
        flags = mat_fields.get(m, {})
        out[m] = dict(name=name.strip(), template=tmpl.get(tp, ""), texture=tex, uses=uses[m],
                      writable=fmt in scenebuild.meshenc.STRIDE, **flags)
    return out


def default_bases(table):
    """(opaque base uid, cut-out base uid): the most used writable material on a shader without seasons (no snow on
    foreign walls) - opaque: a DXT1 colour map; cut-out: an 'Alpha' material (else any with a DXT5 map)."""
    def pick(ok):
        c = [(t["uses"], m) for m, t in table.items() if t["writable"] and t["texture"] and ok(t)]
        return max(c)[1] if c else None
    plain = lambda t: "season" not in t["template"].lower() and "atlas" not in t["name"].lower()  # noqa: E731
    opaque = pick(lambda t: plain(t) and t["texture"][2] in (2, 3)) or pick(lambda t: t["texture"][2] in (2, 3))
    cut = (pick(lambda t: "season" not in t["template"].lower() and "alpha" in t["name"].lower())
           or pick(lambda t: "alpha" in t["name"].lower()) or pick(lambda t: t["texture"][2] == 5) or opaque)
    return opaque, cut


def resolve(table, ref):
    """A material given by uid (bytes / hex) or name -> uid."""
    if isinstance(ref, bytes) and ref in table:
        return ref
    if isinstance(ref, str):
        try:
            u = bytes.fromhex(ref)
            if u in table:
                return u
        except ValueError:
            pass
        hits = [m for m, t in table.items() if t["name"] == ref.strip()]
        if hits:
            return hits[0]
    raise ValueError(f"no material {ref!r} in this map")


def plan_materials(src, scene_mats, base=None, cutout_base=None, table=None):
    """scene materials {key: dict(stock=name | uid) or dict(image=path | array, base=name | uid optional)} ->
    ({key: stock uid}, {new key: materials.apply `new` entry}, {key: new key}, report). Identical pictures on the same
    base become one material."""
    table = table or material_table(src)
    d_opaque, d_cut = default_bases(table)
    opaque = resolve(table, base) if base else d_opaque
    cut = resolve(table, cutout_base) if cutout_base else d_cut
    stock, new, alias, rep = {}, {}, {}, collections.Counter()
    by_pixels = {}
    for key, spec in scene_mats.items():
        if spec.get("stock"):
            stock[key] = resolve(table, spec["stock"])
            rep["stock"] += 1
            continue
        rgba = load_image(spec["image"])
        see_through = is_cutout(rgba) if spec.get("cutout") is None else bool(spec["cutout"])
        if not see_through:
            rgba = rgba.copy()
            rgba[:, :, 3] = 255                  # a mask in alpha (blend / gloss), not holes: drawn opaque
        b = resolve(table, spec["base"]) if spec.get("base") else (cut if see_through else opaque)
        two = spec.get("two_sided", see_through) if spec.get("two_sided") is not None else see_through
        nrm = None
        if spec.get("normal") is not None:
            nrm = materials.normal_map(load_image(spec["normal"]), green=spec.get("normal_green", "+y"),
                                       two_channel=spec.get("normal_two_channel"))
            rep["normal maps"] += 1
        h = _geo_hash(rgba) + b.hex() + str((see_through, two)) + (_geo_hash(nrm) if nrm is not None else "")
        if h not in by_pixels:
            nk = f"scene_{len(by_pixels):04d}"
            name = "ED_" + "".join(c if c.isalnum() else "_" for c in str(spec.get("name", key)))[:40]
            # cut-outs: the Material's alpha test switched on (stock: AlphaTestEnabled 1, value 0x80) whatever the
            # base does; two-sided as the scene says (leaves, cloth: both faces)
            new[nk] = dict(name=name, base=b, rgba=rgba, alpha_test=see_through, two_sided=two, normal=nrm)
            by_pixels[h] = nk
            rep["cut-out" if see_through else "opaque"] += 1
        else:
            rep["shared picture"] += 1
        alias[key] = by_pixels[h]
    rep["bases"] = f"opaque {table[opaque]['name']}, cut-out {table[cut]['name']}"
    return stock, new, alias, rep


# ------------------------------------------------------------------------------------------------ the build --

def build_scene(src, dest, scene, placement="single", base=None, cutout_base=None, pack=True, dry_run=False,
                max_mb=600, id_avoid=None, handholds="rows", navmesh="generate", flows="auto", nav_keep=None,
                log=print):
    """scene: dict(parts, instances, materials) (the instanced form: scenebuild.build's parts and instances) or
    dict(models, materials) (the flat form: from_flat). handholds: True - every
    part without its own `holds` gets the editor's (climb.handholds: beams down walls out of reach); parts' `holds`
    [(a, b, up, wall)] become invisible collision beams unless the
    part says holds_visible. handholds="rows" (default): the MP climbing rows up every big facade, invisible
    (climb.rows: no ladders, a face climbs fast); "beams": visible beams down walls out of reach
    (climb.handholds); False: none. navmesh: "generate" (default) - the navmesh of every cell the scene
    touches made again from the collision (navgen: crowds walk round AND on the new geometry, routes re-tied; a route
    running through new geometry is rerouted round it, or switched off); True - the simpler cut only (navcut: crowds stop
    walking through it); False - untouched. flows (with navmesh="generate" only): "generate" - new crowd routes
    (CrowdFlows) along the open streets of those cells where no usable route of the map walks (navflows: centre lines,
    joined at junctions and to the map's routes nearby, each walked and joined by A* offline; the build refuses if
    one isn't); "auto" (default) - "generate" with navmesh="generate", and if the new routes fail their
    A* check the navmesh is made again without them (report["navmesh"]["new_flows_dropped"] = the misses); None -
    the map's routes only. nav_keep: world points whose walkable part the generated navmesh keeps even when closed in
    (navgen `keep`: blend groups' NPC slots - a merchant inside his stall). -> report dict (estimate, and when built:
    build report, navmesh report - with new_flows [(name, uid)] - sizes)."""
    if flows not in (None, "auto", "generate"):
        raise ValueError(f'flows: "auto", "generate" or None, not {flows!r}')
    if flows == "generate" and navmesh != "generate":
        raise ValueError('flows="generate" needs navmesh="generate" (new routes stand on the generated navmesh)')
    auto = flows == "auto"
    flows = ("generate" if navmesh == "generate" else None) if auto else flows
    if "models" in scene:
        parts, instances = from_flat(scene["models"])
    else:
        parts, instances = scene["parts"], scene["instances"]
    id_avoid = id_avoid if id_avoid is not None else _id_archives()
    table = material_table(src)
    stock, new, alias, mrep = plan_materials(src, scene.get("materials", {}), base, cutout_base, table)
    log(f"materials: {dict(mrep)}")
    parts, instances = prepare(parts, instances, log=log)
    tops = open_tops(parts, instances)
    for name, share in tops[:10]:
        log(f"WARNING: {name}: its top is open ({share:.0%} covered by anything walkable) - players who climb it fall "
            f"inside; bring its roof in too (a separate object in many games), or set climb=False")
    # the material keys: stock uids now; new ones once materials.apply has made them (a placeholder uid until then,
    # for the estimate: its base, whose maps are the same size)
    def mats_of(p, made=None):
        out, bases = [], {}
        for k in p["materials"]:
            if k in stock:
                out.append(stock[k])
            elif k in alias:
                nk = alias[k]
                u = made[nk] if made else new[nk]["base"]
                out.append(u)
                bases[u] = new[nk]["base"]
            else:
                raise ValueError(f"{p['name']}: material {k!r} isn't in the scene's materials")
        return out, bases
    for p in parts.values():
        p["_scene_materials"] = list(p["materials"])
        p["materials"], p["base_materials"] = mats_of(p)
    est = scenebuild.estimate(src, parts, instances, placement, log=log)
    # (new materials' maps are their base's size: the estimate, counting the base's, is right for them too)
    report = dict(estimate=est, materials=dict(mrep), parts=len(parts), objects=len(instances), open_tops=tops)
    if est["total"] / 2**20 > max_mb:
        raise ValueError(f"the build would add about {est['total'] / 2**20:.0f} MB (over max_mb={max_mb}): try "
                         f"placement='single' or fewer / smaller textures")
    if dry_run:
        return report
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    step = src
    if new:
        made, same = materials.apply(src, dest + ".materials", new=new, id_avoid=id_avoid, log=log)
        if not same:
            raise RuntimeError("the new materials changed more than their own records")
        step = dest + ".materials"
        # the parts again, now naming the new materials
        for p in parts.values():
            p["materials"], p["base_materials"] = mats_of(dict(p, materials=p["_scene_materials"]), made)
    if handholds:
        n_rows = 0
        for p in parts.values():
            if not p.get("holds") and p.get("climb", True) and p.get("collision") != "none":
                v, t, keep = scenebuild.weld(*(p["collision"] if isinstance(p.get("collision"), tuple)
                                               else (p["verts"], p["tris"])), keep_mask=True)
                t = t[scenebuild.climbable_mask(p, len(t), keep)]     # no rows / beams on FuzzyZone walls
                if handholds == "beams":
                    p["holds"] = climb.handholds(v, t, climb.ledges(v, t, min_len=0.2))
                    p["holds_visible"] = True
                else:
                    p["holds"] = climb.rows(v, t)
                    p["holds_visible"] = False
                n_rows += len(p["holds"])
        log(f"handholds ({'beams' if handholds == 'beams' else 'rows'}): {n_rows} on "
            f"{sum(1 for p in parts.values() if p.get('holds'))} part(s)")
    ok, brep = scenebuild.build(step, dest, parts, instances, id_avoid=id_avoid, log=log, placement=placement)
    if step != src:
        os.remove(step)
    if not ok:
        raise RuntimeError("the scene didn't read back as built (see the log)")
    report["build"] = dict(brep)
    if navmesh == "generate":
        from . import navcut, navgen
        tris = navcut.world_triangles(parts, instances)
        nv = navgen.regenerate_under(dest, dest + ".nav", tris, flows=flows, keep=nav_keep, log=log)
        if not nv["ok"] and auto and flows and nv.get("check_fails") == 0:
            dropped = nv.get("route_misses") or [f"route check {nv.get('route_check')}"]
            log(f"new crowd routes failed their A* check ({dropped[:3]}): navmesh made again without them")
            os.remove(dest + ".nav")
            nv = navgen.regenerate_under(dest, dest + ".nav", tris, flows=None, keep=nav_keep, log=log)
            nv["new_flows_dropped"] = dropped
        if not nv["ok"]:
            os.remove(dest + ".nav")
            raise RuntimeError(f"the generated navmesh didn't pass its checks: {nv.get('problems')} "
                               f"{nv.get('route_misses', '')}")
        os.replace(dest + ".nav", dest)
        report["navmesh"] = {k: v for k, v in nv.items() if k != "blocked_flows"}
        report["crowd_routes_disabled"] = nv.get("blocked_flows", [])
    elif navmesh:
        from . import navcut
        nv = navcut.apply(dest, dest + ".nav", navcut.world_triangles(parts, instances), log=log)
        if not nv["ok"]:
            os.remove(dest + ".nav")
            raise RuntimeError("the navmesh cut didn't read back (see the log)")
        os.replace(dest + ".nav", dest)
        report["navmesh"] = {k: v for k, v in nv.items() if k != "flows"}
        report["crowd_routes_disabled"] = nv["flows"]
    if pack:
        report["size"] = archive.pack(dest, src, log=log)
    report["file_mb"] = os.path.getsize(dest) / 2**20
    return report


def _id_archives():
    from . import install
    return [os.path.join(install.LIVE, a) for a in ("DataPC.forge", "DataPC_extra.forge")]


# ------------------------------------------------------------------------------------------------ on disk --

def save(folder, scene):
    """scene -> folder/scene.json (names, materials, matrices, part settings) + folder/arrays.npz (geometry);
    pictures given as arrays are written as PNGs, paths kept as they are (relative to the folder when inside it)."""
    os.makedirs(folder, exist_ok=True)
    arrays, js = {}, {"format": "ac3editor-scene/1"}
    if "models" in scene:
        parts, instances = from_flat(scene["models"])
    else:
        parts, instances = scene["parts"], scene["instances"]
    jp = {}
    for i, (k, p) in enumerate(parts.items()):
        e = {}
        for f, v in p.items():
            if isinstance(v, np.ndarray) or f in ("verts", "tris", "uv", "tri_mat"):
                if v is not None:
                    arrays[f"p{i}_{f}"] = np.asarray(v)
                    e[f] = f"p{i}_{f}"
            elif f == "collision" and isinstance(v, (tuple, list)):
                arrays[f"p{i}_cv"], arrays[f"p{i}_ct"] = np.asarray(v[0]), np.asarray(v[1])
                e[f] = [f"p{i}_cv", f"p{i}_ct"]
            elif f == "holds" and v:
                arrays[f"p{i}_holds"] = np.asarray(v, np.float64)
                e[f] = f"p{i}_holds"
            else:
                e[f] = v
        jp[str(k)] = e
    js["parts"] = jp
    js["instances"] = [dict(part=str(x["part"]), name=x["name"],
                            matrix=np.asarray(x["matrix"], np.float64).reshape(-1).tolist()) for x in instances]
    jm = {}
    for k, m in scene.get("materials", {}).items():
        e = dict(m)
        if isinstance(e.get("image"), np.ndarray):
            from PIL import Image
            fn = f"tex_{len(jm):04d}.png"
            Image.fromarray(load_image(e["image"])).save(os.path.join(folder, fn))
            e["image"] = fn
        elif isinstance(e.get("image"), str) and os.path.abspath(e["image"]).startswith(os.path.abspath(folder)):
            e["image"] = os.path.relpath(e["image"], folder)
        if isinstance(e.get("stock"), bytes):
            e["stock"] = e["stock"].hex()
        jm[k] = e
    js["materials"] = jm
    np.savez_compressed(os.path.join(folder, "arrays.npz"), **arrays)
    with open(os.path.join(folder, "scene.json"), "w", encoding="utf-8") as fh:
        json.dump(js, fh, indent=1)


def load(folder):
    """The scene a folder holds (save's form) -> dict(parts, instances, materials)."""
    with open(os.path.join(folder, "scene.json"), encoding="utf-8") as fh:
        js = json.load(fh)
    arr = np.load(os.path.join(folder, "arrays.npz"))
    parts = {}
    for k, e in js["parts"].items():
        p = {}
        for f, v in e.items():
            if f in ("verts", "tris", "uv", "tri_mat", "holds") and isinstance(v, str):
                p[f] = arr[v]
                if f == "holds":
                    p[f] = [tuple(h) for h in arr[v]]
            elif f == "collision" and isinstance(v, list):
                p[f] = (arr[v[0]], arr[v[1]])
            else:
                p[f] = v
        parts[k] = p
    inst = [dict(part=x["part"], name=x["name"], matrix=np.array(x["matrix"]).reshape(4, 4)) for x in js["instances"]]
    mats = {}
    for k, m in js.get("materials", {}).items():
        m = dict(m)
        if isinstance(m.get("image"), str) and not os.path.isabs(m["image"]):
            m["image"] = os.path.join(folder, m["image"])
        mats[k] = m
    return dict(parts=parts, instances=inst, materials=mats)
