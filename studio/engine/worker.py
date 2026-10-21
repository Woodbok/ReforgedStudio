"""Reforged Studio's engine process: the app starts it and talks to it over stdin / stdout, one JSON object per line.

    request:  {"id": 1, "cmd": "open", ...}
    replies:  {"log": "text"}                                  any number, while the command runs
              {"id": 1, "ok": true, "result": {...}}           or {"id": 1, "ok": false, "error": "text"}

Commands: hello, maps, open {archive}, select_building {archive, uids, current, removed}, build {request: see
plan.py}, install {dest}, restore {archive}, status {archive}, library {prefer: map title} (the asset library's index
state: indexes missing maps in the background), assets {keys} (the asset scene the app draws library models from),
nav {archive} (the crowds' navmesh), blend {archive} (blend groups and the stock ones to place), climb {archive}
(climbable edges), mapsettings {archive} / mapsettings_preview {archive, settings} (the Map panel: lighting, kill
height, view distance), boundaries {archive} / gameplay {archive} / map_check {archive, request} (spawn points per
mode, boundaries, gameplay objects), plus the character and SP-area commands below, quit. Everything the engine prints goes to the app as
log lines (stdout carries only the protocol).

    python studio/engine/worker.py          (the app runs this; by hand: type requests, one per line)
"""
import json
import os
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ac3editor.workspace import WORK  # noqa: E402  (the workspace: builds, caches)
PROTOCOL = 1
_out = open(sys.stdout.fileno(), "w", encoding="utf-8", newline="\n", closefd=False)
_lock = threading.Lock()


def send(obj):
    line = json.dumps(obj, ensure_ascii=False)
    with _lock:
        _out.write(line + "\n")
        _out.flush()


def log(*parts):
    send({"log": " ".join(str(p) for p in parts)})


class _LogStream:
    """sys.stdout / sys.stderr while the engine runs: whole lines become log messages."""

    def __init__(self):
        self.buf = ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                log(line.rstrip())
        return len(s)

    def flush(self):
        pass


sys.stdout = _LogStream()

import numpy as np  # noqa: E402

import library  # noqa: E402
import maps  # noqa: E402
import plan  # noqa: E402
import scene_cache  # noqa: E402
from ac3editor import install, mapdata  # noqa: E402


def cmd_hello(_a):
    return dict(protocol=PROTOCOL, repo=REPO, python=sys.version.split()[0])


def cmd_maps(_a):
    return dict(maps=maps.known_maps())


def cmd_open(a):
    path = os.path.abspath(a["archive"])
    md = mapdata.load(path, log=log)
    cache = scene_cache.write(path, md, log=log)
    return dict(archive=path, title=maps.title(path), cache=cache, skipped=md.skipped)


_indexer = None


def cmd_library(a):
    """The release's maps (and saved ones) with their library index files; missing ones are indexed in the
    background, `prefer` (a map title) first."""
    global _indexer
    if _indexer is None:
        _indexer = library.Indexer(log=log)
    import splib
    ms = [m for m in maps.known_maps() if m["kind"] != "AC3"] + splib.sp_maps()      # (the install's SP maps)
    return _indexer.state(ms, prefer=a.get("prefer"), wanted=set(a.get("sp_wanted", [])))


def cmd_personas(_a):
    """The game's personas (charview.py, in a child process)."""
    import charview
    return charview.run_child(["personas"], log=log)


def cmd_character(a):
    """One persona's default look and weapon as a scene cache the app draws."""
    import charview
    return charview.run_child(["character", str(int(a["persona"])), json.dumps(a.get("rows") or {}),
                               json.dumps(a.get("colours") or {}), json.dumps(a.get("meshes") or {}),
                               json.dumps(a.get("weapons") or {}), json.dumps(a.get("materials") or {})], log=log)


# character edits built into a test DataPC (<workspace>/characters/<name>/), installed and put back
CHAR_INSTALLED = os.path.join(WORK, "characters", "installed.json")


def _char_installed():
    """-> the build installed in the game (its name), while the live DataPC is still the file it put there."""
    try:
        rec = json.load(open(CHAR_INSTALLED, encoding="utf-8"))
        st = os.stat(os.path.join(install.LIVE, "DataPC.forge"))
        return rec["name"] if (st.st_size, int(st.st_mtime)) == (rec["size"], rec["mtime"]) else None
    except (OSError, ValueError, KeyError):
        return None


def cmd_char_build(a):
    import charedit
    return charedit.run_child(["build", str(int(a["persona"])), json.dumps(a.get("colours") or {}), a["name"],
                               json.dumps(a.get("meshes") or {}), json.dumps(a.get("weapons") or {}),
                               json.dumps(a.get("materials") or {}), json.dumps(a.get("new_looks") or []),
                               json.dumps(a.get("new_weapons") or [])], log=log)


def cmd_ingest(_a):
    """Read the user's game once after setup (maps, personas, the id census) - a child process."""
    import subprocess
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "ingest.py"), "--json"], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = []
    t = threading.Thread(target=lambda: out.extend(p.stdout.read().splitlines()), daemon=True)
    t.start()
    last = ""
    for line in p.stderr:
        if line.strip():
            log(line.rstrip())
            last = line.rstrip()
    p.wait()
    t.join()
    lines = [x for x in out if x.strip()]
    if p.returncode or not lines:
        raise RuntimeError(last or "reading the game failed")
    return json.loads(lines[-1])


def cmd_char_materials(a):
    """{part} -> its materials, their values (named) and maps."""
    import charedit
    return charedit.run_child(["materials", a["part"]], log=log)


def cmd_char_material_preview(a):
    """{part, edits (its materials'), yaw, pitch, mesh (an imported glTF spec), highlight} -> a PNG of the part lit."""
    import charedit
    return charedit.run_child(["matpreview", json.dumps(a)], log=log)


def cmd_char_weapons(a):
    """{persona} -> its weapon items and the models of each class it could wear (charweapon.items)."""
    import charedit
    return charedit.run_child(["weapons", str(int(a["persona"]))], log=log)


# a part's mesh out to Blender (glTF) and the edited one back
def cmd_char_export(a):
    """{persona, part} -> the part's .glb (charmesh.export, a child) + the edited .glb Blender writes beside it."""
    import charedit
    res = charedit.run_child(["export", str(int(a["persona"])), a["part"]], log=log)
    res.update(_edited(a["part"]))
    res["worn_by"] = sorted({w["persona"] for w in charedit.wearers().get(a["part"], [])})
    return res


def _edited(part):
    import charmesh
    folder = os.path.join(charmesh.OUT, part)
    p = os.path.join(folder, part + "_edit.glb")
    return dict(folder=folder, edited=p if os.path.exists(p) else None,
                edited_time=time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))) if os.path.exists(p) else None)


def cmd_char_edited(a):
    """{part} -> where its edited .glb is, if Blender has written one."""
    return _edited(a["part"])


def blender_exe():
    """AC3STUDIO_BLENDER, else the newest Blender under Program Files."""
    import glob
    env = os.environ.get("AC3STUDIO_BLENDER")
    if env and os.path.exists(env):
        return env
    try:
        import paths                                  # (the config's "blender")
        if getattr(paths, "BLENDER", "") and os.path.exists(paths.BLENDER):
            return paths.BLENDER
    except Exception:  # noqa: BLE001
        pass
    found = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"))
    return found[-1] if found else None


def cmd_char_blender(a):
    """{glb} -> Blender opened on it: the default scene emptied, the part imported, saved as <part>_edit.blend so its
    File > Export > glTF suggests <part>_edit.glb (the name the Studio looks for)."""
    import subprocess
    exe = blender_exe()
    if not exe:
        raise FileNotFoundError("no Blender found (set AC3STUDIO_BLENDER to blender.exe)")
    glb = a["glb"]
    blend = glb[:-4] + "_edit.blend"
    if os.path.exists(blend):
        args = [exe, blend]
    else:
        expr = ("import bpy\n"
                "for o in list(bpy.data.objects): bpy.data.objects.remove(o)\n"
                f"bpy.ops.import_scene.gltf(filepath={glb!r})\n"
                f"bpy.ops.wm.save_as_mainfile(filepath={blend!r})\n")
        args = [exe, "--python-expr", expr]
    subprocess.Popen(args, cwd=os.path.dirname(glb))
    return dict(blender=exe, blend=blend)


def cmd_char_builds(_a):
    import charedit
    return dict(builds=charedit.builds(), installed=_char_installed(), out=charedit.OUT)


def cmd_char_delete(a):
    import charedit
    if _char_installed() == a["name"]:
        raise ValueError(f"{a['name']} is in the game: put the release's DataPC back first")
    return charedit.delete(a["name"])


def cmd_char_install(a):
    import charedit
    b = next((x for x in charedit.builds() if x["name"] == a["name"]), None)
    if b is None or not b.get("exists"):
        raise ValueError(f"no build {a['name']!r}")
    files = b.get("files") or [b["dest"]]               # (DataPC_extra too where the build added files there)
    lives = [install.install(f) for f in files]
    st = os.stat(lives[0])
    json.dump(dict(name=b["name"], size=st.st_size, mtime=int(st.st_mtime), dest=b["dest"],
                   files=[os.path.basename(f) for f in files]), open(CHAR_INSTALLED, "w", encoding="utf-8"))
    return dict(live=lives[0], lives=lives, installed=b["name"])


def cmd_char_restore(_a):
    try:
        names = json.load(open(CHAR_INSTALLED, encoding="utf-8")).get("files") or ["DataPC.forge"]
    except (OSError, ValueError):
        names = ["DataPC.forge"]
    for n in names:
        if n != "DataPC.forge":
            install.restore(n)
    src, dest = install.restore("DataPC.forge")
    try:
        os.remove(CHAR_INSTALLED)
    except OSError:
        pass
    return dict(original=src, live=dest)


def cmd_animation(a):
    """The persona's look skinned by a clip (default: its menu idle) -> an .ac3anim the app plays."""
    import charview
    return charview.run_child(["animation", str(int(a["persona"])), json.dumps(a.get("rows") or {}), a.get("clip") or "",
                               json.dumps(a.get("meshes") or {}), json.dumps(a.get("materials") or {})], log=log)


def cmd_sp_maps(_a):
    """The install's SP maps and the hosts an area can go onto."""
    import splib
    import sparea
    return dict(maps=splib.sp_maps(), hosts=list(sparea.HOSTS))


def cmd_sp_overview(a):
    import sparea
    return sparea.overview(a["sp_map"], log=log)


def cmd_sp_preview(a):
    import sparea
    return sparea.preview(a["sp_map"], a["box"], log=log)


def cmd_sp_build(a):
    """The SP-area conversion (a child process; minutes) -> its result (dest: the new map's archive)."""
    import sparea
    return sparea.run_child(a["request"], log=log)


def cmd_assets(a):
    path, metas = library.asset_scene(a["keys"], log=log)
    return dict(cache=path, keys=a["keys"], assets=metas)


def cmd_building_ground(a):
    return plan.building_ground(a["archive"], a["uids"], a.get("current", {}))


def cmd_select_building(a):
    path = os.path.abspath(a["archive"])
    md = plan.ground_of(path)[0]
    sinfo = plan.spawns_of(path)
    current = {bytes.fromhex(k): np.array(v, dtype=np.float64).reshape(4, 4) for k, v in a.get("current", {}).items()}
    sel = [bytes.fromhex(h) for h in a["uids"]]
    found = plan.building_of(md, sinfo, sel, current, [bytes.fromhex(h) for h in a.get("removed", [])])
    by_uid = {e.uid: e for e in md.entities}
    act = by_uid.get(sel[-1]) if sel else None
    key = (act.group or act.name) if act is not None else None
    return dict(uids=[u.hex() for u in found], key=key)


def cmd_nav(a):
    """The crowds' navmesh of a map for the viewport: edges by kind, waypoints and links, crowd routes."""
    import navview
    return navview.nav_data(os.path.abspath(a["archive"]))


def cmd_climb(a):
    """Where the open map can be climbed: every climb edge (and its owner), the no-climb collision triangles."""
    import climbview
    return climbview.climb_data(os.path.abspath(a["archive"]))


def cmd_blend(a):
    """The map's blend groups (their NPC slots) and the stock maps' blend groups to place."""
    import blendview
    return blendview.blend_data(os.path.abspath(a["archive"]))


def cmd_mapsettings(a):
    """The Map panel: the map's lighting controls, presets and curves, its kill height and view distance."""
    import mapsettings
    return mapsettings.read(os.path.abspath(a["archive"]))


def cmd_mapsettings_preview(a):
    """The curves as the panel's settings would leave them, and what they'd warn about."""
    import mapsettings
    path = os.path.abspath(a["archive"])
    res = mapsettings.preview(path, a.get("settings") or {})
    res["errors"], res["warnings"] = mapsettings.check(path, a.get("settings") or {})
    return res


def cmd_boundaries(a):
    """Every out-of-bounds area and gameplay zone of a map, with its corners and modes."""
    import spawnview
    return dict(areas=spawnview.boundary_data(os.path.abspath(a["archive"])))


def cmd_gameplay(a):
    """The map's gameplay objects (each with its members / companions) and the stock ones to place."""
    import gameplayview
    return gameplayview.data(os.path.abspath(a["archive"]))


def cmd_map_check(a):
    """The spawn and boundary edits of a request checked as the build will (spawns.problems, the new boundaries):
    -> {errors, warnings, counts: {mode record: {kind label: n}}}."""
    import spawnview
    from ac3editor import ground, spawns
    path = os.path.abspath(a["archive"])
    req = a.get("request") or {}
    sinfo = plan.spawns_of(path)
    _md, gr, _v = plan.ground_of(path)
    errors, warnings, _ch, final = spawnview.check(
        path, req, sinfo=sinfo, areas=plan.areas_of(path), pairs=list(plan.pairs_of(path).values()),
        floor=lambda x, y, top: ground.surface(gr, x, y, top, ()))
    if final is None:
        final = {r: {u: (sp.kind, sp.matrix) for u, sp in here.items()} for r, here in spawns.active(sinfo).items()}
    counts = {}
    for r, here in final.items():
        c = counts.setdefault(r, {})
        for kind, _m in here.values():
            k = spawns.kind_label(tuple(kind))
            c[k] = c.get(k, 0) + 1
    return dict(errors=errors, warnings=warnings, counts=counts)


def cmd_build(a):
    req = a["request"]
    res = plan.build(req, log=log)
    carry, moved = res.pop("_carry"), res.pop("_moved")
    plugs = res.pop("_plugs", None)
    # the plugged holes for the viewport - (x, y) cells of 0.5 m, their height, collision only or not
    res["plugs"] = [dict(z=round(z, 2), kind=kind, cells=[[round(x, 2), round(y, 2)] for x, y in pts[:6000]])
                    for pts, z, kind in (plugs or [])]
    ok, lines, found = plan.verify(req, res["dest"], log=log, carry=carry, moved=moved, placed=res.get("placed"),
                                   added=res.get("added"), riders=res.pop("_riders", None), plugs=plugs,
                                   dropped=res.pop("_dropped", None))
    res.update(verified=ok, checks=lines)
    if found is not None:
        # the hole points for the viewport (x, y, z), at most 4000 of each kind
        res["holes"] = {k: [[round(v, 2) for v in p] for p in found[k][:4000]] for k in ("visible", "collision")}
    return res


def cmd_install(a):
    return dict(live=install.install(a["dest"]))


def cmd_restore(a):
    src, dest = install.restore(os.path.basename(a["archive"]))
    return dict(original=src, live=dest)


def cmd_status(a):
    name = os.path.basename(a["archive"])
    live = os.path.join(install.LIVE, name)
    release = install.original_of(name)
    return dict(live=live, exists=os.path.exists(live), original=install.is_original(name), release=release,
                game=os.path.dirname(install.LIVE), exe=os.path.join(os.path.dirname(install.LIVE), "AC3MP.exe"),
                from_release=os.path.normcase(os.path.abspath(release)) == os.path.normcase(os.path.abspath(a["archive"])))


COMMANDS = {k[4:]: v for k, v in globals().items() if k.startswith("cmd_")}


def main():
    send({"log": f"engine ready (protocol {PROTOCOL}, Python {sys.version.split()[0]})"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as e:
            send({"id": None, "ok": False, "error": f"bad request: {e}"})
            continue
        rid, cmd = req.get("id"), req.get("cmd")
        if cmd == "quit":
            send({"id": rid, "ok": True, "result": {}})
            break
        fn = COMMANDS.get(cmd)
        if fn is None:
            send({"id": rid, "ok": False, "error": f"unknown command {cmd!r}"})
            continue
        try:
            send({"id": rid, "ok": True, "result": fn(req)})
        except Exception as e:  # noqa: BLE001 - every failure goes back to the app
            for tl in traceback.format_exc().rstrip().split("\n"):
                log(tl)
            send({"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
