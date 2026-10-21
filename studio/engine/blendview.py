"""Blend groups in Reforged Studio: the static NPC groups players blend into - what the app draws, and the build pass.

    blend_data(archive)           -> the map's blend groups (uid, name, kind, slots, props) and the stock templates
    template_of(key)              -> a template (gameplay.Thing) from its library key "blend:<archive>|<entity uid hex>"
    model_key(key)                -> the library model key of its marker disc ("<archive>|<mesh hex>#<entity hex>")
    apply(src, dest, ...)         -> moves / copies / take-outs / new groups through ac3editor.gameplay
    find_set(archive, th)         -> the set a group stands at in its own map: a merchant's stall, a barman's bar...
    set_parts(archive, th)        -> that set as parts relative to the group (templates' "set": the app places them with
                                     the group as one placed group - copies of the open map's own objects, else library
                                     models; they build as any placed model)

A blend group is an Entity in a map cell whose GameplayCoordinatorComponent runs GcLMPCrowdLife (gameplay.py
"blend_group"): a marker disc (its Visual, no collision), 3 - 8 NPC slots - a position and a facing each, relative to
the group, so they turn and move with it -, the NPC specs and, for some, props its NPCs hold (one shared entity per
prop on the map: mugs, bottles, axes, hammers, choppers). Nothing but its cell's GridCellDataBlock list names it, so a
group taken out is deleted, not sunk (gameplay.plan). The game's crowd brings NPCs to its slots at match start.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

from ac3editor import gameplay as G, mapdata  # noqa: E402

PREFIX = "blend:"
_READ = {}

# The set a blend group stands at - a merchant's stall, the barman's counter, the cook's cauldron table... -
# placed with it ("with its stall"). Per kind (the group's model name without its prefix): its pieces, each a model
# family matched by entity name; per group, the nearest such object of its own map (within SET_REACH of the group's
# middle, standing on its level) is the piece. studio/tools/blend_sets_survey.py shows every stock group's pieces:
# each kind's pieces stand at the same place relative to its group on every map (README "Blend groups' sets").
SETS = {
    "Merchant_Meat": [("stall", r"_(Stand_424|Tradingtent)_01A_\d+$"),
                      ("goods", r"_(MarketStandMeat|Merchandise_Meat)_01A_\d+$")],
    "Merchant_Fish": [("stall", r"_(Stand_424|Tradingtent)_01A_\d+$"),
                      ("goods", r"_(MarketStandFish|Merchandise_Fish)_01A_\d+$")],
    "Barman": [("bar", r"(_BRE_Bar_01A_\d+|_Meshall_bar_01A_group_\d+/2)$"), ("bar front", r"_BRE_Bar_Plug_01A_\d+$")],
    "Cooker": [("cauldron table", r"_Cauldron_Table_01A_\d+$")],
    "Sawer": [("saw horse", r"^ACMP_GEN_Saw_01a_\d+$")],
    "Timber": [("chopping block", r"^AC3MP_GEN_TimberWood_01a_\d+$")],
    "Recruter": [("politic stand", r"_Politic_Stand_6x6x4_01A_\d+$"),           # (Animus: the recruiter in his booth)
                 ("stand inside", r"_PoliticStandInterior_6x6_0\dA_\d+$"),
                 ("stand floor", r"_PoliticStandGround_6x6_01A_\d+$"), ("chair", r"^AC3MP_GEN_Chair_01a_\d+$")],
}
SET_REACH = 3.0         # m from the group's middle to the piece's box (stock: 0 - 2.7)
_BOXES = {}


def set_kind(name):
    """'AC3MP_GEN_Merchant_Meat_001' -> 'Merchant_Meat' (a SETS key) or None."""
    k = re.sub(r"^AC3MP_(GEN|[A-Z]+)_", "", G.model_name(name))
    return k if k in SETS else None


def _boxes(md):
    """{entity uid: (lo, hi)} world boxes of a map's cell objects with a model (once per map per process)."""
    if md.archive in _BOXES:
        return _BOXES[md.archive]
    out = {}
    for e in md.entities:
        if e.mesh is None or e.mesh not in md.meshes or not e.record.startswith("Cell"):
            continue
        v = np.asarray(md.meshes[e.mesh][1], np.float64)
        if not len(v):
            continue
        w = np.c_[v, np.ones(len(v))] @ np.asarray(e.matrix, np.float64).reshape(4, 4)
        out[e.uid] = (w[:, :3].min(0), w[:, :3].max(0))
    _BOXES[md.archive] = out
    return out


def find_set(archive, th):
    """The set a blend group stands at in its own map -> [dict(piece, name, entity, mesh, model_key, world 4x4,
    reach m)] (empty: its kind has none, or none found by it)."""
    kind = set_kind(th.name)
    if kind is None:
        return []
    md = mapdata.load(archive, log=lambda *_a: None)
    boxes = _boxes(md)
    gm = np.asarray(th.matrix, np.float64).reshape(4, 4)
    c = gm[3, :3]
    out = []
    for piece, rx in SETS[kind]:
        best = None
        for e in md.entities:
            if e.uid not in boxes or not re.search(rx, e.name):
                continue
            lo, hi = boxes[e.uid]
            if hi[2] < c[2] - 1.0 or lo[2] > c[2] + 2.5:          # (its own level: not the floor above / below)
                continue
            d = float(np.linalg.norm(np.maximum(0, np.maximum(lo[:2] - c[:2], c[:2] - hi[:2]))))
            o = float(np.linalg.norm(np.asarray(e.matrix[12:14]) - c[:2]))
            if d <= SET_REACH and (best is None or (d, o) < best[0]):
                best = ((d, o), e)
        if best is not None:
            e = best[1]
            out.append(dict(piece=piece, name=e.name, entity=e.uid.hex(), mesh=e.mesh.hex(), reach=round(best[0][0], 2),
                            model_key=f"{os.path.abspath(archive)}|{e.mesh.hex()}#{e.uid.hex()}",
                            world=np.asarray(e.matrix, np.float64).reshape(4, 4)))
    return out


def set_parts(archive, th):
    """A group's set as asset parts, relative to the group's own position (the pivot a placement puts where the camera
    looks; the group keeps its turn): [dict(piece, name, key, map, matrix [16])] - the app places them with it."""
    T = np.eye(4)
    T[3, :3] = np.asarray(th.matrix, np.float64).reshape(4, 4)[3, :3]
    inv = np.linalg.inv(T)
    return [dict(piece=s["piece"], name=s["name"], key=s["model_key"], map=th.map,
                 matrix=(s["world"] @ inv).reshape(-1).round(5).tolist()) for s in find_set(archive, th)]


def is_blend_key(key):
    return key.startswith(PREFIX)


def key_of(th):
    return f"{PREFIX}{os.path.abspath(th.archive)}|{th.uid.hex()}"


def split_key(key):
    archive, _s, h = key[len(PREFIX):].rpartition("|")
    return archive, bytes.fromhex(h)


def groups(archive):
    """{uid: Thing} of a map's blend groups (gameplay.read), once per file version per process."""
    path = os.path.abspath(archive)
    stamp = (path, os.path.getsize(path), os.path.getmtime(path))
    if stamp not in _READ:
        info = G.read(path)
        _READ[stamp] = {u: th for u, th in info["things"].items() if th.kind == "blend_group"}
    return _READ[stamp]


def label(name):
    """'AC3MP_GEN_Static_Group_Posing_CL_002' -> 'Static Group Posing CL'."""
    m = G.model_name(name)
    m = re.sub(r"^AC3MP_(GEN|[A-Z]+)_", "", m)
    return m.replace("_", " ").strip() or name


def _prop_names(archive, th):
    if not th.props:
        return []
    md = mapdata.load(archive, log=lambda *_a: None)
    by = {e.uid: e.name for e in md.entities}
    return [G.model_name(by.get(u, u.hex())) for u in th.props]


def _row(archive, th, mesh=None):
    return dict(uid=th.uid.hex(), name=th.name, label=label(th.name), map=th.map,
                matrix=np.asarray(th.matrix, np.float64).reshape(-1).round(5).tolist(),
                slots=[list(s) for s in th.slots], props=_prop_names(archive, th), mesh=mesh)


def templates():
    """The stock maps' blend groups, one per model per map (gameplay.catalog) -> [row + key, model_key]."""
    out = []
    meshes = {}
    for th in G.catalog("blend_group"):
        if th.archive not in meshes:
            md = mapdata.load(th.archive, log=lambda *_a: None)
            meshes[th.archive] = {e.uid: e.mesh for e in md.entities}
        mesh = meshes[th.archive].get(th.uid)
        if mesh is None or not th.records[0].startswith("Cell"):
            continue
        r = _row(th.archive, th, mesh.hex())
        r.update(key=key_of(th), model_key=f"{os.path.abspath(th.archive)}|{mesh.hex()}#{th.uid.hex()}",
                 prop_uids=[u.hex() for u in th.props], set=set_parts(th.archive, th))
        out.append(r)
    out.sort(key=lambda r: (r["label"], r["map"]))
    return out


def blend_data(archive):
    """-> dict(groups=[{uid, name, label, matrix, slots, props}], templates=[... + key, model_key, missing (props this
    map hasn't)]) for the app."""
    path = os.path.abspath(archive)
    gs = [_row(path, th) for th in groups(path).values() if th.records[0].startswith("Cell")]
    gs.sort(key=lambda r: r["name"])
    have = {e.uid.hex() for e in mapdata.load(path, log=lambda *_a: None).entities}
    ts = templates()
    for r in ts:
        # props its NPCs hold that this map hasn't (each map has one shared entity per prop): it can't go here
        r["missing"] = [n for n, u in zip(r["props"], r.pop("prop_uids")) if u not in have]
    return dict(groups=gs, templates=ts)


def template_of(key):
    archive, uid = split_key(key)
    th = groups(archive).get(uid)
    if th is None:
        raise ValueError(f"no blend group {uid.hex()} in {os.path.basename(archive)}")
    return th


def model_key(key):
    """A blend template's marker disc as a library model key (library.extract reads it as that entity's model)."""
    archive, uid = split_key(key)
    md = mapdata.load(archive, log=lambda *_a: None)
    e = next((e for e in md.entities if e.uid == uid), None)
    if e is None or e.mesh is None:
        raise ValueError(f"blend group {uid.hex()}: no marker model in {os.path.basename(archive)}")
    return f"{os.path.abspath(archive)}|{e.mesh.hex()}#{uid.hex()}"


def slot_points(archive, moves=None, removes=(), adds=()):
    """World points (x, y, z) of every blend group's NPC slots as a build leaves them: the map's groups (moved ones at
    their new place, taken-out ones left out) and the new ones ([dict(template=Thing, matrix)]). A navmesh made again
    keeps the walkable part under each (navgen `keep`): a merchant's slot is inside his stall."""
    moves, gone, pts = moves or {}, set(removes), []

    def put(th, m):
        m = np.asarray(m, np.float64).reshape(4, 4)
        pts.extend((np.array([s[0], s[1], s[2], 1.0]) @ m)[:3].tolist() for s in th.slots)
    for u, th in groups(archive).items():
        if u not in gone and th.records[0].startswith("Cell"):
            put(th, moves.get(u, th.matrix))
    for a in adds:
        put(a["template"], a["matrix"])
    return pts


def apply(src, dest, adds=(), moves=None, removes=(), log=print):
    """Blend groups added (copies of this map's or of a stock map's: [dict(template=Thing, matrix 4x4, name)]),
    moved ({uid: 4x4}), taken out ([uid]) -> (report lines, warnings). Raises on errors / a failed read-back."""
    rep = G.build(src, dest, add=list(adds), move={u: dict(matrix=m) for u, m in (moves or {}).items()},
                  remove=list(removes), pack=False, log=log)
    if rep["errors"]:
        raise RuntimeError("blend groups: " + "; ".join(rep["errors"]))
    if not rep["ok"]:
        raise RuntimeError("blend groups: the build didn't read back as planned: " + "; ".join(rep.get("fails", [])[:3]))
    lines = [f"blend groups: {len(rep['made'])} new, {len(rep['moved'])} moved, {len(rep['removed'])} taken out "
             f"(read back: yes)"]
    for m in rep["made"]:
        lines.append(f"  new blend group {m['name']} ({m['template']}) in {', '.join(m['records'])}")
    return lines, list(rep["warnings"])
