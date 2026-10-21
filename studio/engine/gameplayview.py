"""Gameplay objects in Reforged Studio: the map's hiding spots, benches, chase breakers, lifts, blend groups, Artifact
Assault artifacts / scoring zones and Domination points - what the app draws and selects as one object - and the build
pass that moves / copies / takes them out or places the stock maps' ones, through ac3editor.gameplay rather than
collision.edit_shapes: their stored state (GameStateData), map icons, FX companions and merged collision move with
them, copies get fresh ids for everything nested.

    data(archive)             -> the map's gameplay objects (uid, kind, members, companions...) and the stock templates
                                 to place (Library > Gameplay)
    things(archive)           -> {uid: gameplay.Thing} (cached per file version)
    template_of(key)          -> a template from its key "gp:<archive>|<uid hex>"
    parts(key)                -> its models relative to it [(library model key, 4x4)] (the asset scene draws them)
    route(...)                -> the build request's edits sorted: which go through the gameplay engine
    apply(src, dest, ...)     -> the pass (one gameplay.build): report lines, warnings

A gameplay object is a top-level Entity or EntityGroup (gameplay.read). An EntityGroup's head is an object of its own in
the scene (its members name it as their group): moving the head moves the object - its members and its FX companion
go with it (the app selects them all on a click: what the app sends for them is checked to agree). Artifacts, scoring
zones and Domination points can only be moved (the game needs one artifact per team and three points); a moved
Domination point carries its zone along (zones="follow"). Removing one sinks it (gameplay.SINK, inside the Havok world)
- a blend group is deleted from its cell instead.
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

from ac3editor import gameplay as G, mapdata, spawns  # noqa: E402

PREFIX = "gp:"
LIB_KINDS = ("hiding_spot", "bench", "chase_breaker", "lift")       # the stock ones the Library offers
MOVE_ONLY = G.MODE_KINDS
KIND_LABEL = {"hiding_spot": "hiding spot", "bench": "bench", "chase_breaker": "chase breaker", "lift": "lift",
              "artifact": "artifact", "scoring_zone": "scoring zone", "domination_point": "Domination point",
              "blend_group": "blend group"}
_READ = {}


def things(archive):
    """{uid: Thing} of a map's gameplay objects (every kind), once per file version per process."""
    path = os.path.abspath(archive)
    stamp = (path, os.path.getsize(path), os.path.getmtime(path))
    if stamp not in _READ:
        _READ[stamp] = G.read(path)
    return _READ[stamp]["things"]


def info_of(archive):
    things(archive)
    path = os.path.abspath(archive)
    return _READ[(path, os.path.getsize(path), os.path.getmtime(path))]


def is_key(key):
    return key.startswith(PREFIX)


def key_of(th):
    return f"{PREFIX}{os.path.abspath(th.archive)}|{th.uid.hex()}"


def split_key(key):
    archive, _s, h = key[len(PREFIX):].rpartition("|")
    return archive, bytes.fromhex(h)


def template_of(key):
    archive, uid = split_key(key)
    for th in G.catalog():
        if th.uid == uid and os.path.normcase(os.path.abspath(th.archive)) == os.path.normcase(os.path.abspath(archive)):
            return th
    th = things(archive).get(uid)
    if th is None:
        raise ValueError(f"no gameplay object {uid.hex()} in {os.path.basename(archive)}")
    return th


def plural(kind):
    w = KIND_LABEL.get(kind, kind)
    return w + ("es" if w.endswith("ch") else "s")


def label(th):
    """'hiding spot: HayCart 01a', 'Domination point B', 'artifact team 1'."""
    k = KIND_LABEL.get(th.kind, th.kind)
    if th.kind == "domination_point" and th.hud in (1, 2, 3):
        return f"{k} {'ABC'[th.hud - 1]}"
    if th.team in (1, 2):
        return f"{k}, team {th.team}"
    m = re.sub(r"^AC3MP_(GEN|[A-Z]+)_", "", th.model)
    return f"{k}: {m.replace('_', ' ').strip()}"


def _row(th, info=None):
    return dict(uid=th.uid.hex(), name=th.name, kind=th.kind, label=label(th), map=th.map,
                matrix=np.asarray(th.matrix, np.float64).reshape(-1).round(5).tolist(),
                members=[u.hex() for u in th.members], companions=[u.hex() for u in th.companions],
                records=list(th.records), team=th.team, hud=th.hud, move_only=th.kind in MOVE_ONLY,
                modes=[spawns.mode_label(r) for r in (th.modes(info) if info and th.kind in MOVE_ONLY else [])])


def parts(key):
    """The models of a template relative to it: [(library model key "<archive>|<mesh>#<entity>", 4x4)] - the object
    itself when it shows a model, and every member that does."""
    th = template_of(key)
    md = mapdata.load(th.archive, log=lambda *_a: None)
    by = {}
    for e in md.entities:
        by.setdefault(e.uid, e)
    inv = np.linalg.inv(np.asarray(th.matrix, np.float64).reshape(4, 4))
    out = []
    for u in [th.uid] + list(th.members):
        e = by.get(u)
        if e is None or e.mesh is None or e.mesh not in md.meshes:
            continue
        out.append((f"{os.path.abspath(th.archive)}|{e.mesh.hex()}#{u.hex()}",
                    np.asarray(e.matrix, np.float64).reshape(4, 4) @ inv))
    return out


def templates():
    """The stock maps' hiding spots, benches, chase breakers and lifts (gameplay.catalog: one per model per map)."""
    out = []
    for kind in LIB_KINDS:
        for th in G.catalog(kind):
            if not th.records[0].startswith("Cell"):
                continue
            r = _row(th)
            r.update(key=key_of(th), model=th.model, size=None)
            if th.box is not None:
                lo, hi = np.asarray(th.box[0], np.float64), np.asarray(th.box[1], np.float64)
                r["size"] = np.round(hi - lo, 2).tolist()
            out.append(r)
    out.sort(key=lambda r: (LIB_KINDS.index(r["kind"]), r["model"], r["map"]))
    return out


def data(archive):
    """-> dict(things=[rows], templates=[rows]) for the app."""
    info = info_of(archive)
    rows = [_row(th, info) for th in info["things"].values()]
    rows.sort(key=lambda r: (r["kind"], r["name"]))
    return dict(things=rows, templates=templates())


# ---------------------------------------------------------------------------------------------- the build --

def route(ths, moved, removed, copied, places, log=print):
    """The request's map edits that are gameplay objects -> dict(
        moves {uid: 4x4}, removes [uid], adds [dict(template, matrix, name)],
        consumed {entity uid}: members / companions the gameplay engine moves with their object (not edit_shapes'),
        consumed_copies {index into copied}: a copied object's member copies (the engine copies its members),
        riders {uid: 4x4}: where the read-back must find each member / companion,
        warnings [str]).
    moved [(entity, 4x4, key)], removed [entity], copied [(entity, 4x4)] as plan.parse gives them; places: the "gp:"
    places. Raises RuntimeError for what the engine can't do (removing an artifact...).

    An object's PARTS edited without its head (Select Attached and box select pick only pieces with a model: a
    copied bell tower brings its chase breakers' door leaves but not their group heads or FX entities) still make the
    whole object's edit: parts moved by one rigid move move the object, a part taken out takes the object out, part
    copies that agree on one move make one copy of the object (FX companion included) - never edit_shapes copies of
    loose leaves."""
    out = dict(moves={}, removes=[], adds=[], consumed=set(), consumed_copies=set(), riders={}, warnings=[])
    if not ths:
        return out
    member_of = {}
    for th in ths.values():
        for u in list(th.members) + list(th.companions):
            member_of.setdefault(u, th.uid)
    named = {}
    for th in ths.values():
        for c in th.companions:
            named[c] = named.get(c, 0) + 1
    md = mapdata.load(next(iter(ths.values())).archive, log=lambda *_a: None)
    ent = {}
    for e in md.entities:
        ent.setdefault(e.uid, e)
    mv = {e.uid: m for e, m, _k in moved}

    def followers(th):
        return list(th.members) + [c for c in th.companions if named.get(c) == 1]
    for e, m, _k in moved:
        th = ths.get(e.uid)
        if th is None:
            continue
        out["moves"][th.uid] = np.asarray(m, np.float64).reshape(4, 4)
        D = np.linalg.inv(np.asarray(th.matrix, np.float64).reshape(4, 4)) @ out["moves"][th.uid]
        apart = []
        for u in followers(th):
            out["consumed"].add(u)
            if u not in ent:
                continue
            want = np.asarray(ent[u].matrix, np.float64).reshape(4, 4) @ D
            out["riders"][u] = want
            if u in mv and not np.allclose(mv[u], want, atol=2e-3):
                apart.append(ent[u].name)
        if apart:
            out["warnings"].append(f"{th.name}: {len(apart)} part(s) moved apart from it ({', '.join(apart[:3])}) - "
                                   "they go with the object as it is")

    def m44(m):
        return np.asarray(m, np.float64).reshape(4, 4)
    # parts moved without their head: one rigid move for all of them -> the object moves (its head and the rest follow)
    by_thing = {}
    for e, m, _k in moved:
        t = member_of.get(e.uid)
        if t is not None and t not in out["moves"] and e.uid not in ths:
            by_thing.setdefault(t, []).append(e.uid)
    for t, parts in by_thing.items():
        th = ths[t]
        Ds = [np.linalg.inv(m44(ent[u].matrix)) @ m44(mv[u]) for u in parts if u in ent]
        if not Ds or any(not np.allclose(D, Ds[0], atol=2e-3) for D in Ds):
            out["warnings"].append(f"{th.name}: its parts moved apart from one another - left as separate objects "
                                   "(move the whole object: one click picks it)")
            continue
        D = Ds[0]
        out["moves"][t] = m44(th.matrix) @ D
        out["riders"][t] = out["moves"][t]
        for u in followers(th):
            out["consumed"].add(u)
            if u in ent:
                out["riders"][u] = m44(ent[u].matrix) @ D
    gone = {e.uid for e in removed}
    for e in removed:
        th = ths.get(e.uid)
        if th is None:
            continue
        if th.kind in MOVE_ONLY:
            raise RuntimeError(f"{th.name}: {KIND_LABEL[th.kind]}s can't be taken out (the game needs one artifact per "
                               "team and three Domination points) - move it instead")
        out["removes"].append(th.uid)
        if th.kind == "blend_group":
            continue
        for u in followers(th):
            out["consumed"].add(u)
            if u in ent and u not in gone:
                sunk = np.asarray(ent[u].matrix, np.float64).reshape(4, 4).copy()
                sunk[3, 2] -= G.SINK
                out["riders"][u] = sunk
    # a part taken out without its head: the whole object goes (a chase breaker missing a leaf would still swing)
    for t in sorted({member_of[u] for u in gone if u in member_of and u not in ths} - set(out["removes"]) -
                    set(out["moves"])):
        th = ths[t]
        if th.kind in MOVE_ONLY:
            continue
        out["removes"].append(t)
        out["warnings"].append(f"{th.name}: a part of it was taken out - the whole object goes")
        if th.kind == "blend_group":
            continue
        for u in [t] + followers(th):
            out["consumed"].add(u)
            if u in ent and u not in gone:
                sunk = m44(ent[u].matrix).copy()
                sunk[3, 2] -= G.SINK
                out["riders"][u] = sunk
    for k, (e, m) in enumerate(copied):
        th = ths.get(e.uid)
        if th is None:
            continue
        if th.kind in MOVE_ONLY:
            raise RuntimeError(f"{th.name}: {KIND_LABEL[th.kind]}s can't be copied (one artifact per team, three "
                               "Domination points) - move it instead")
        m = np.asarray(m, np.float64).reshape(4, 4)
        out["adds"].append(dict(template=th, matrix=m, name=f"{G.model_name(th.name)}_ED{len(out['adds']) + 1:03d}"))
        out["consumed_copies"].add(k)
        D = np.linalg.inv(np.asarray(th.matrix, np.float64).reshape(4, 4)) @ m
        for j, (e2, m2) in enumerate(copied):
            if j in out["consumed_copies"] or member_of.get(e2.uid) != th.uid or e2.uid not in ent:
                continue
            if np.allclose(np.asarray(m2, np.float64).reshape(4, 4),
                           np.asarray(ent[e2.uid].matrix, np.float64).reshape(4, 4) @ D, atol=2e-3):
                out["consumed_copies"].add(j)
    # part copies without their head's copy: those agreeing on one move make one copy of the whole object
    for j, (e, m) in enumerate(copied):
        t = member_of.get(e.uid)
        if j in out["consumed_copies"] or t is None or e.uid in ths or e.uid not in ent:
            continue
        th = ths[t]
        if th.kind in MOVE_ONLY:
            continue
        D = np.linalg.inv(m44(ent[e.uid].matrix)) @ m44(m)
        mine = [k for k, (e2, m2) in enumerate(copied) if k not in out["consumed_copies"] and member_of.get(e2.uid) == t
                and e2.uid in ent and np.allclose(m44(m2), m44(ent[e2.uid].matrix) @ D, atol=2e-3)]
        out["consumed_copies"].update(mine)
        out["adds"].append(dict(template=th, matrix=m44(th.matrix) @ D,
                                name=f"{G.model_name(th.name)}_ED{len(out['adds']) + 1:03d}"))
    for pl in places:
        th = template_of(pl["key"])
        out["adds"].append(dict(template=th, matrix=np.asarray(pl["matrix"], np.float64).reshape(4, 4),
                                name=f"{G.model_name(th.name)}_ED{len(out['adds']) + 1:03d}"))
    return out


def apply(src, dest, adds=(), moves=None, removes=(), ths=None, log=print):
    """One gameplay.build for every gameplay edit (blend groups too): -> (report lines, warnings). A moved Domination
    point carries its zone along. Raises on errors / a failed read-back."""
    moves = moves or {}
    ths = ths if ths is not None else things(src)
    dom = any(ths[u].kind == "domination_point" for u in moves if u in ths)
    rep = G.build(src, dest, add=list(adds), move={u: dict(matrix=m) for u, m in moves.items()}, remove=list(removes),
                  zones="follow" if dom else None, pack=False, log=log)
    if rep["errors"]:
        raise RuntimeError("gameplay objects: " + "; ".join(rep["errors"]))
    if not rep["ok"]:
        raise RuntimeError("gameplay objects: the build didn't read back as planned: " + "; ".join(rep.get("fails", [])[:3]))
    kinds = {}
    for u in moves:
        if u in ths:
            kinds.setdefault(ths[u].kind, [0, 0, 0])[0] += 1
    for u in removes:
        if u in ths:
            kinds.setdefault(ths[u].kind, [0, 0, 0])[2] += 1
    for a in adds:
        kinds.setdefault(a["template"].kind, [0, 0, 0])[1] += 1
    lines = ["gameplay objects (read back: yes): " + "; ".join(
        f"{plural(k)} {n[1]} new, {n[0]} moved, {n[2]} taken out" for k, n in sorted(kinds.items()))]
    for m in rep["made"]:
        lines.append(f"  new {m['name']} ({m['template']}) in {', '.join(m['records'])}")
    if rep.get("zones"):
        lines.append(f"  {len(rep['zones'])} Domination zone(s) moved with their point")
    return lines, list(rep["warnings"])


# ---------------------------------------------------------------------------------------- the asset scene --

def member_keys(key):
    """The library model keys a placed template is drawn from (library.ensure reads them)."""
    return [k for k, _m in parts(key)]


def scene_of(key, folders):
    """A template as one asset-scene entry: its members' models (library asset folders) put together at their places
    relative to it -> (sceneport-form scene {parts: {asset}, materials}, meta). Drawing only: the build copies the
    object itself (gameplay.build)."""
    from ac3editor import sceneport
    th = template_of(key)
    verts, tris, uvs, tmat, cverts, ctris, mats, mat_keys = [], [], [], [], [], [], {}, []
    nv = ncv = 0
    for mk, rel in parts(key):
        sc = sceneport.load(folders[mk])
        p = sc["parts"]["asset"]
        v = np.asarray(p["verts"], np.float64)
        w = (np.c_[v, np.ones(len(v))] @ rel)[:, :3]
        base = len(mat_keys)
        for m in p["materials"]:
            nk = f"{len(mat_keys)}_{m}"
            mats[nk] = sc["materials"][m]
            mat_keys.append(nk)
        verts.append(w)
        tris.append(np.asarray(p["tris"], np.int64) + nv)
        uvs.append(np.zeros((len(v), 2)) if p.get("uv") is None else np.asarray(p["uv"], np.float64))
        tmat.append(np.asarray(p["tri_mat"], np.int64) + base)
        nv += len(v)
        if isinstance(p.get("collision"), tuple):
            cv = np.asarray(p["collision"][0], np.float64)
            cverts.append((np.c_[cv, np.ones(len(cv))] @ rel)[:, :3])
            ctris.append(np.asarray(p["collision"][1], np.int64) + ncv)
            ncv += len(cv)
    if not verts:
        raise ValueError(f"{th.name}: no model to draw")
    part = dict(materials=mat_keys, verts=np.vstack(verts), tris=np.vstack(tris), uv=np.vstack(uvs),
                tri_mat=np.concatenate(tmat),
                collision=(np.vstack(cverts), np.vstack(ctris)) if cverts else None)
    first = parts(key)[0][0]
    mesh = first.rpartition("|")[2].partition("#")[0]
    meta = dict(name=th.name, mesh=mesh, map=th.map, nt=int(len(part["tris"])), collision="own",
                collision_tris=int(len(part["collision"][1])) if cverts else 0, native_edges=0,
                gameplay=dict(kind=th.kind, label=label(th), name=th.name, map=th.map, members=len(th.members)))
    return dict(parts=dict(asset=part), materials=mats), meta
