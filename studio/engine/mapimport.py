"""Reforged Studio: another game's multiplayer map imported as an AC3 map - forgelib's map-port recipes run from the
Studio on the user's own installs. The result opens in the Studio like any map, to edit, build and install.

A recipe exists per map (forgelib's per-map configurations): every Revelations multiplayer map and the Brotherhood-era
ones it ships (maps/port/build_map.py; the Brotherhood-era ones through crossmaps/build_acb.py), and the four
Brotherhood maps Revelations doesn't ship (crossmaps/build_acb.py: a first conversion to Revelations' form, then the
same chain), and Black Flag's (ac4/stage0.py). Each recipe converts the source archive in place: the result keeps the
source's file name, so it installs over a game whose menus already list that map.

Everything a recipe reads comes from the user's own installs, never from converted maps shipped elsewhere: Black Flag's
recipe also reads uid indexes, inventories and an object survey of AC3's archives, uid indexes of Black Flag's shared
archives and the four games' class registries (built here, from the installs and their exes, into the workspace), and
two templates its maps reuse from Revelations' Jerusalem and Antioch - taken from the user's own imports of those
two maps, made first when missing. Its sound package is left out (its banks need a Wwise converter the Studio doesn't
carry; the archive never reads the package).

* `catalogue()`: every game the Studio knows, its install, and its maps with a recipe (`list`, in a child process: the
  recipes are forgelib modules that must not mix with the engine's own).
* `run_child(req)`: the import in a child process (minutes, gigabytes) -> <workspace>/imports/<name>/<archive>, with
  the recipe's own checks (a step that fails its verify stops the import).
* `built()`: the maps imported so far (the Maps list).

Where the recipes write: forgelib's own `out\\` folder. In a release tree its subfolders the recipes use (LINKED) are
made links into <workspace>/forge_out, so builds and indexes land in the workspace; the result is then moved to
<workspace>/imports/ and the build folder cleared.

    python studio/engine/mapimport.py list                  (the catalogue as JSON)
    python studio/engine/mapimport.py build <request.json>  (the worker runs this)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ac3editor.workspace import WORK  # noqa: E402  (the workspace: builds, caches)
from ac3editor import games  # noqa: E402

OUT = os.path.join(WORK, "imports")
FORGE_OUT = os.path.join(WORK, "forge_out")
CACHE_CAP_GB = "8"                  # the recipes' decompress cache cap (forgelib datafile; its own default is larger)
ENV_DEV = "REFORGED_STUDIO_IMPORT_IN_FORGE"     # (a development checkout: let the recipes write into its forge library)

FRIENDLY = {"constantinople": "Galata", "mtstmichel": "Mont St-Michel", "sandonato": "San Donato",
            "sanmarco": "San Marco", "villa": "Monteriggioni"}
# Black Flag's maps with a recipe (ac4/map_configs.py, which needs the class registries this module builds before it can
# be imported): key -> (title, the archive in Black Flag's install, the recipe's folder tag, the name it installs as)
AC4_RECIPES = {"tampa": ("Tampa", "DataPC_AC3MP_MSettlement.forge", "msett", None),
               "havana": ("Havana", "DataPC_ACGA_Havana.forge", "havana", None),
               "fisher": ("Fisher Village", "DataPC_ACGA_FisherVillage.forge", "fisher", None),
               "expedition": ("Expedition", "DataPC_ACGA_Expedition.forge", "expd", None),
               "prison": ("Prison", "DataPC_ACGA_Prison.forge", "prison", None),
               "piratehq": ("Pirate Headquarters", "DataPC_ACGA_Pirate_Headqua.forge", "phq", None),
               "saba": ("Saba", "DataPC_ACGA_Secret_Pirate_.forge", "saba", None),
               "kingston": ("Kingston", "DataPC_ACGA_KINGSTON_dlc.forge", "king", "DataPC_ACGA_KINGSTON_MAP.forge")}
# the Revelations maps whose own import Black Flag's recipe reads (its extra_sources: two water templates)
AC4_DEPS = (("ACR", "jerusalem"), ("ACR", "antioch"))
# forgelib out\ subfolders the recipes write: links into <workspace>/forge_out
LINKED = ("maps", "crossmaps", "uid", "clone", "survey", "reg", "ac4")
# minutes per route on a warm cache (recorded builds); the first import also indexes the installs it reads
MINUTES = {"acr": 15, "acr_ac2mp": 25, "acb": 20, "ac4": 10}
INSTALL_NOTE = ("It installs as {file}, the name the source map has in AC3: Install puts it in the game over a map of "
                "that name - an install whose menus already list it (acmpreforged's lists every map with a recipe). "
                "On a game without that map it opens, edits and builds here, but the game has no menu entry to load "
                "it from.")
AC4_NOTE = (" Black Flag's recipe reuses two water templates from Revelations' Jerusalem and Antioch: the first Black "
            "Flag import imports those two maps from your Revelations install first (about 15 minutes each), unless "
            "you have. The map's own sound banks (its ambience and map sounds) are not converted: they play where the "
            "game's sound package already carries Black Flag's map banks (acmpreforged's does); elsewhere they are "
            "missing.")


def _forge():
    from ac3editor import mapdata
    return mapdata.FORGE_REPO


def _is_dev_forge(forge):
    return os.path.isdir(os.path.join(forge, ".git"))


# --------------------------------------------------------------------------------------------- catalogue --

def _recipes():
    """[dict(game, key, title, archive, file, route)] read from forgelib's per-map configurations (in this process:
    run as the `list` child)."""
    forge = _forge()
    for p in (os.path.join(forge, "crossmaps"), os.path.join(forge, "maps", "port"), os.path.join(forge, "maps"), forge):
        if p not in sys.path:
            sys.path.insert(0, p)
    import configs as port_configs                  # maps/port's
    out = []
    for key, cfg in sorted(port_configs.MAPS.items()):
        route = "acr_ac2mp" if cfg.get("source") else "acr"
        out.append(dict(game="ACR", key=key, archive=cfg["archive"], file=cfg.get("install_as") or cfg["archive"],
                        route=route, title=FRIENDLY.get(key, key.title())))
    try:
        import acb_configs                          # crossmaps' (imports maps/port's configs itself)
        for key, cfg in sorted(acb_configs.MAPS.items()):
            out.append(dict(game="ACB", key=key, archive=cfg["archive"], file=cfg.get("install_as") or cfg["archive"],
                            route="acb", title=FRIENDLY.get(key, key.title())))
    except ImportError:
        pass
    for key, (title, archive, tag, install_as) in AC4_RECIPES.items():
        out.append(dict(game="AC4", key=key, archive=archive, file=install_as or archive, route="ac4", title=title,
                        tag=tag))
    return out


def _list_child():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "list"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("[")]
    if p.returncode or not lines:
        raise RuntimeError("reading the import recipes failed: " + (p.stderr.strip().splitlines() or ["?"])[-1])
    return json.loads(lines[-1])


def unavailable_reason(route):
    return ""


def catalogue(log=print):
    """-> dict(games=[dict(game, title, found, mp_dir, maps=[dict(key, title, archive, file, route, available,
    reason, minutes, note)])], target)."""
    recipes = _list_child()
    out = []
    for prof in games.profiles():
        if (prof.game, prof.edition) == games.TARGET or not prof.can("import_source"):
            continue
        inst = prof.install()
        maps = []
        for r in recipes:
            if r["game"] != prof.game:
                continue
            src = os.path.join(inst["mp_dir"], r["archive"]) if inst["found"] else ""
            reason = unavailable_reason(r["route"])
            if not reason and not inst["found"]:
                reason = f"no {prof.title} install (File > Settings)"
            elif not reason and not os.path.exists(src):
                reason = f"{r['archive']} isn't in the install"
            elif not reason and r["route"] in ("acr_ac2mp", "acb", "ac4") and not games.profile("ACR").install()["found"]:
                reason = "the recipe reads Revelations' install too (File > Settings)"
            ex = existing(r["game"], r["key"])
            maps.append(dict(r, available=not reason, reason=reason, minutes=MINUTES.get(r["route"], 15),
                             note=INSTALL_NOTE.format(file=r["file"]) + (AC4_NOTE if r["route"] == "ac4" else ""), source=src,
                             existing={k: ex[k] for k in ("dest", "name", "imported", "seconds", "source_changed")}
                             if ex else None))
        out.append(dict(game=prof.game, title=prof.title, found=inst["found"], mp_dir=inst["mp_dir"], maps=maps))
    return dict(games=out, target=games.target().title)


# ------------------------------------------------------------------------------------------------- build --

def default_name(req):
    return req.get("name") or f"{req['game']}_{req['key']}"


def _stamp(path):
    """A source archive's size and time: an import made from it is out of date once these change."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return dict(size=st.st_size, mtime=int(st.st_mtime))


def existing(game, key):
    """-> the latest finished import of that map (its import.json, plus source_changed: the source archive's size or
    time differ from the ones it was made from), or None. A map once imported is opened from here, never converted
    again unless asked."""
    best = None
    for name in os.listdir(OUT) if os.path.isdir(OUT) else []:
        res = os.path.join(OUT, name, "import.json")
        try:
            r = json.load(open(res, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("game") != game or r.get("key") != key or not os.path.exists(r.get("dest", "")):
            continue
        r["mtime"] = os.path.getmtime(res)
        if best is None or r["mtime"] > best["mtime"]:
            best = r
    if best is None:
        return None
    best.setdefault("imported", time.strftime("%Y-%m-%d %H:%M", time.localtime(best["mtime"])))
    then, now = best.get("source_stamp"), _stamp(best.get("source", ""))
    best["source_changed"] = bool(then and now and then != now)
    return best


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "import"


def _link_out(forge, log=print):
    """forgelib's out\\ subfolders the recipes write (LINKED: builds and indexes) as links into
    <workspace>/forge_out."""
    if _is_dev_forge(forge):
        if not os.environ.get(ENV_DEV):
            raise RuntimeError(f"the forge library here is a development checkout ({forge}): an import would write into "
                               f"its out folder. Run the import from a release tree, or set {ENV_DEV}=1 to allow it.")
        return
    os.makedirs(os.path.join(forge, "out"), exist_ok=True)
    for sub in LINKED:
        link, target = os.path.normpath(os.path.join(forge, "out", sub)), os.path.normpath(os.path.join(FORGE_OUT, sub))
        os.makedirs(target, exist_ok=True)
        if os.path.exists(link):
            real = os.path.normcase(os.path.realpath(link))
            if real == os.path.normcase(os.path.normpath(link)) or real == os.path.normcase(os.path.realpath(target)):
                continue                             # (a folder of its own, or already this workspace's: used as it is)
            os.rmdir(link)                           # (a link into another workspace - the setting changed: only the
                                                     # link goes, not what it points to)
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode:
            log(f"(couldn't link {link} to the workspace: {r.stderr.strip() or r.stdout.strip()}; writing there)")


def _env(forge, log=print):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    env.setdefault("AC3FORGE_CACHE_CAP_GB", CACHE_CAP_GB)
    # AC3's own archives, as the recipes see them (forgelib paths.foreign_archive): without other games' maps an install
    # may hold - an acmpreforged one holds these very maps under the same uids, which would read as AC3's own
    env["AC3_OWN_ARCHIVES"] = "1"
    for g in ("ACR", "ACB", "AC4"):                  # the installs the recipes read (forgelib paths.install)
        try:
            inst = games.profile(g).install()
        except KeyError:
            continue
        if inst["found"]:
            env[f"{g}_MULTI"] = inst["mp_dir"]
    return env


def _commands(recipe, name, rq=None):
    """-> [(cwd, argv)] of the recipe's commands, in order (rq: the recipe's own request file, Black Flag's)."""
    forge = _forge()
    port, cross = os.path.join(forge, "maps", "port"), os.path.join(forge, "crossmaps")
    py = sys.executable
    if recipe["route"] == "acr":
        return [(port, [py, os.path.join(port, "build_map.py"), recipe["key"], "--name", name])]
    if recipe["route"] == "acr_ac2mp":              # (stage 0 runs inside when its build is missing)
        return [(forge, [py, os.path.join(cross, "build_acb.py"), recipe["key"], "--name", name])]
    if recipe["route"] == "acb":                     # (stage 0 by itself: build_acb's import already wants its build)
        return [(forge, [py, os.path.join(cross, "acb_stage0.py"), recipe["key"], "--name", recipe["stage0"]]),
                (forge, [py, os.path.join(cross, "build_acb.py"), recipe["key"], "--name", name])]
    if recipe["route"] == "ac4":                     # (its inputs built first, then its stage 0 on them)
        me = os.path.abspath(__file__)
        return [(forge, [py, me, "ac4prep", rq]), (forge, [py, me, "ac4stage0", rq])]
    raise RuntimeError(unavailable_reason(recipe["route"]) or f"no route {recipe['route']}")


def _recipe_details(game, key):
    """The recipe's tag / stage-0 name (this process: the build child)."""
    r = next((x for x in _recipes() if x["game"] == game and x["key"] == key), None)
    if r is None:
        raise RuntimeError(f"no recipe for {game} {key}")
    if r["route"] == "ac4":
        return r                                     # (its tag is in AC4_RECIPES)
    if r["route"] in ("acr", "acr_ac2mp"):
        import configs as port_configs
        cfg = port_configs.MAPS[key]
    else:
        import acb_configs
        cfg = acb_configs.MAPS[key]
    r["tag"] = cfg["tag"]
    if r["route"] == "acb":                          # (the chain reads the stage 0 its config names: build that one)
        s0 = cfg["stage0_src"]
        r["stage0"] = s0[:-len("_" + cfg["tag"])] if s0.endswith("_" + cfg["tag"]) else s0
    return r


def _run(cwd, argv, env, log):
    log("== " + " ".join(os.path.basename(a) if i < 2 else a for i, a in enumerate(argv)))
    p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    tail, checks = [], []
    for line in p.stdout:
        s = line.rstrip()
        if not s.strip():
            continue
        log(s)
        tail = (tail + [s])[-8:]
        # each step's verify line, the final checks' lines (indented "== <check>: ..."), any PASS / FAIL
        if (s.lstrip().lower().startswith("verify") or re.match(r"^\s+== ", s) or s.startswith(("ok  ", "FAIL", "WARN"))
                or " PASS" in s or "FAIL" in s):
            checks.append(s.strip()[:300])
    code = p.wait()
    if code:
        raise RuntimeError(f"{os.path.basename(argv[1])} failed (exit {code}): " + " / ".join(tail[-3:]))
    return checks


def build(req, log=print):
    """-> dict(dest, name, game, key, title, seconds, checks). In this process (the worker runs it as a child)."""
    t0 = time.time()
    forge = _forge()
    r = _recipe_details(req["game"], req["key"])
    if unavailable_reason(r["route"]):
        raise RuntimeError(unavailable_reason(r["route"]))
    prof = games.profile(r["game"])
    inst = prof.install()
    src = os.path.join(inst["mp_dir"], r["archive"])
    if not os.path.exists(src):
        raise RuntimeError(f"{src} not found: is {prof.title} set in File > Settings?")
    stamp = _stamp(src)
    name = _safe(default_name(req))
    log(f"importing {r['title']} from {prof.title} ({src}) as {name}")
    _link_out(forge, log)
    env = _env(forge, log)
    build_name = "SI" + time.strftime("%H%M%S")
    rq = None
    if r["route"] == "ac4":                          # (the user's exes the class registries are read from)
        from ac3editor import mapdata
        exes = {"AC3": mapdata.schema_exe()}
        for g in ("AC4", "ACR", "ACB"):
            i = games.profile(g).install()
            if i["found"] and os.path.exists(i["exe"]):
                exes[g] = i["exe"]
        rq = os.path.join(OUT, name, "recipe.json")
        os.makedirs(os.path.dirname(rq), exist_ok=True)
        with open(rq, "w", encoding="utf-8") as f:
            json.dump(dict(key=r["key"], name=build_name, extra=req.get("extra") or {}, exes=exes, ac4_mp=inst["mp_dir"]),
                      f, indent=1)
    checks = []
    for cwd, argv in _commands(r, build_name, rq):
        checks += _run(cwd, argv, env, log)
    made = os.path.join(forge, "out", "maps", f"{build_name}_{r['tag']}")
    res = os.path.join(made, r["archive"] if r["route"] == "ac4" else r["file"])
    if not os.path.exists(res):
        raise RuntimeError(f"the recipe finished without {res}")
    dest_dir = os.path.join(OUT, name)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, r["file"])
    for f in os.listdir(dest_dir):                   # (an earlier import under this name)
        if f.endswith(".forge"):
            os.remove(os.path.join(dest_dir, f))
    shutil.move(res, dest)
    shutil.rmtree(made, ignore_errors=True)
    out = dict(dest=dest, name=name, game=r["game"], key=r["key"], title=r["title"], source=src, route=r["route"],
               seconds=round(time.time() - t0), checks=checks[-40:], verified=True, source_stamp=stamp,
               imported=time.strftime("%Y-%m-%d %H:%M"), run=req.get("run"))
    with open(os.path.join(dest_dir, "import.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    log(f"imported in {out['seconds']} s -> {dest}")
    return out


def run_child(req, log=print):
    """build() in a child process (minutes, gigabytes: the memory goes back when it ends) -> its result. A map imported
    before is that import (reused: true), unless the request says again; importing again replaces it in its own folder,
    and the earlier import stays listed until the new one has finished."""
    ex = existing(req["game"], req["key"])
    if ex and not req.get("again"):
        log(f"{ex['title']} was imported on {ex['imported']} ({ex['dest']}): opening that one, nothing converted"
            + (" - its source archive has changed since" if ex["source_changed"] else ""))
        return dict(ex, reused=True)
    if req["game"] == "AC4":                         # (the user's own imports of the maps its recipe reads, first)
        extra = {}
        for g, k in AC4_DEPS:
            if not existing(g, k):
                log(f"Black Flag's recipe reads Revelations' {k.title()} (two water templates): importing it first")
            d = run_child({"game": g, "key": k}, log)["dest"]
            extra[os.path.basename(d)] = d
        req = dict(req, extra=extra)
    name = _safe(req.get("name") or (ex["name"] if ex else default_name(req)))
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    rq, res = os.path.join(d, "request.json"), os.path.join(d, "import.json")
    run = f"{os.getpid()}_{time.time():.3f}"
    with open(rq, "w", encoding="utf-8") as f:
        json.dump(dict(req, name=name, run=run), f, indent=1)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "build", rq], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    tail = []
    for line in p.stdout:
        if line.strip():
            log(line.rstrip())
            tail = (tail + [line.rstrip()])[-6:]
    code = p.wait()
    try:
        out = json.load(open(res, encoding="utf-8"))
    except (OSError, ValueError):
        out = {}
    if code or out.get("run") != run:
        raise RuntimeError(f"the import failed (exit {code}): " + " / ".join(tail[-3:]))
    return out


def built():
    """-> [dict(path, title, source, kind="import")] of the maps imported so far (the Maps list)."""
    out = []
    for name in sorted(os.listdir(OUT)) if os.path.isdir(OUT) else []:
        res = os.path.join(OUT, name, "import.json")
        if not os.path.exists(res):
            continue
        try:
            r = json.load(open(res, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if os.path.exists(r.get("dest", "")):
            try:
                game = games.profile(r["game"]).title
            except KeyError:
                game = r["game"]
            out.append(dict(path=r["dest"], title=f"{r['title']} (imported, {r['game']})", kind="import",
                            source=f"imported from {game} ({os.path.basename(r['source'])})"))
    return out


# ----------------------------------------------------------------------------------------- Black Flag's recipe --

def _forge_path():
    forge = _forge()
    for p in (os.path.join(forge, "ac4chars"), os.path.join(forge, "loader"), os.path.join(forge, "maps"), forge,
              os.path.join(forge, "ac4")):
        if p not in sys.path:
            sys.path.insert(0, p)
    return forge


def ac4_prepare(rq):
    """(A child of the build.) What Black Flag's recipe reads besides the map, built from the user's installs into forgelib's
    out folders (linked to the workspace), each kept for the next import: the four games' class registries (from their
    exes), uid indexes of AC3's own archives and of Black Flag's shared ones, AC3's inventories and DataPC object survey,
    AC3's data layers and the element shapes its maps show."""
    import pickle
    req = json.load(open(rq, encoding="utf-8"))
    forge = _forge_path()
    import exe_registry
    import inventory
    import mapwalk
    import paths
    import survey_objects
    import uid_index
    out = os.path.join(forge, "out")
    reg = os.path.join(out, "reg", "class_registries.pkl")
    want = {g: _stamp(p) for g, p in sorted(req["exes"].items())}
    try:
        have = json.load(open(reg + ".json", encoding="utf-8"))
    except (OSError, ValueError):
        have = None
    if have != want or not os.path.exists(reg):
        regs = {}
        for g, p in sorted(req["exes"].items()):
            t = time.time()
            try:
                regs[g] = exe_registry.load(p)[0]
            except Exception as e:  # noqa: BLE001  (only AC3's and Black Flag's are needed: the others are extra names)
                if g in ("AC3", "AC4"):
                    raise
                print(f"class registry of {os.path.basename(p)}: not read ({e}); left out", flush=True)
                continue
            print(f"class registry of {os.path.basename(p)}: {len(regs[g])} classes ({time.time() - t:.0f} s)", flush=True)
        os.makedirs(os.path.dirname(reg), exist_ok=True)
        pickle.dump(regs, open(reg, "wb"))
        json.dump(want, open(reg + ".json", "w", encoding="utf-8"))
    T = games.target()
    for a in T.shared + ("DataPC_AC2MP_Whiteroom.forge",) + tuple(T.stock_file(m) for m in T.stock_maps):
        if os.path.exists(os.path.join(paths.AC3_SOURCE, a)):
            uid_index.load("AC3", a)
    for a in ("DataPC.forge", "DataPC_extra.forge", "DataPC_CoreFX.forge"):
        dst = os.path.join(out, "uid", f"AC4_{a}.pkl")
        if not os.path.exists(dst):
            t = time.time()
            idx = uid_index.scan(os.path.join(req["ac4_mp"], a))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            pickle.dump(idx, open(dst, "wb"))
            print(f"uid index of Black Flag's {a}: {len(idx[0])} uids ({time.time() - t:.0f} s)", flush=True)
    for a in mapwalk.map_archives("AC3"):
        inventory.load("AC3", a)
    survey_objects.cached("AC3", "DataPC.forge", paths.AC3_SOURCE)
    import common                                    # (ac4's: reads the class registries built above)
    # AC3's data layers (every DataLayer its DataPC defines): the layers a converted filter may keep
    layers = os.path.join(common.OUT, "_ac3_layers.pkl")
    if not os.path.exists(layers):
        idx = pickle.load(open(os.path.join(out, "uid", "AC3_DataPC.forge.pkl"), "rb"))[0]
        names = {u: n for u, (_r, t, n) in idx.items() if common.cls(t, "AC3") == "DataLayer"}
        os.makedirs(common.OUT, exist_ok=True)
        pickle.dump({"names": names}, open(layers, "wb"))
        print(f"AC3's data layers: {len(names)}", flush=True)
    # the element shapes AC3's own maps show (the recipe's verify compares against them)
    if not os.path.exists(os.path.join(common.OUT, "_ac3_shapes.pkl")):
        import _ac3_shapes
        _ac3_shapes.main()
    print("verify: Black Flag's recipe inputs ready (class registries, uid indexes, inventories, AC3's DataPC survey): OK",
          flush=True)


def ac4_stage0(rq):
    """(A child of the build.) Black Flag's recipe (ac4/stage0.py) on the map, its extra sources the user's own Revelations
    imports, no sound package."""
    req = json.load(open(rq, encoding="utf-8"))
    forge = _forge_path()
    import map_configs
    cfg = map_configs.MAPS[req["key"]]
    srcs = {}
    for p_, names in cfg.get("extra_sources", {}).items():
        b = os.path.basename(p_)
        if b not in req["extra"]:
            raise SystemExit(f"the recipe reads {b} from a Revelations map, and no import of it was given")
        srcs[req["extra"][b]] = names
        print(f"extra source {b}: the user's own import {req['extra'][b]} ({', '.join(names)})", flush=True)
    cfg["extra_sources"] = srcs
    cfg["sound_package"] = None
    sys.argv = [os.path.join(forge, "ac4", "stage0.py")]
    import stage0
    stage0.main(["--map", req["key"], "--name", req["name"]])


def main(argv):
    if argv[:1] == ["list"]:
        print(json.dumps(_recipes()))
        return 0
    if len(argv) >= 2 and argv[0] == "build":
        with open(argv[1], encoding="utf-8") as f:
            req = json.load(f)
        build(req)
        return 0
    if len(argv) >= 2 and argv[0] == "ac4prep":
        ac4_prepare(argv[1])
        return 0
    if len(argv) >= 2 and argv[0] == "ac4stage0":
        ac4_stage0(argv[1])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
