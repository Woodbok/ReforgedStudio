"""Where the game lives: the paths every forgelib module uses, from Reforged Studio's config.

The config is a JSON file (config_path(): %APPDATA%\\Reforged Studio\\config.json); the Studio's first-run setup
writes it:

    {"version":   2,
     "target":    {"game": "AC3", "edition": "MP"} - the game the Studio edits,
     "installs":  {"AC3": {"root":         the game folder,
                           "mp_dir":       its multiplayer folder (...\\Assassin's Creed III\\multi) - read, and written
                                           by Install,
                           "sp_dir":       the singleplayer install (default: mp_dir's parent),
                           "release_set":  a release_set.json listing the files the game should hold (optional; without
                                           one the Studio keeps the game's own files in <workspace>\\originals the first
                                           time it installs over each),
                           "pristine_dir": an untouched reference copy (optional)},
                   "ACR" / "ACB" / "AC4": {"root", "mp_dir"} - other games' installs, read only (map imports)},
     "workspace": where the Studio keeps its builds, caches and backups (default: Documents\\Reforged Studio),
     "blender":   blender.exe (optional),
     "game_dir", "sp_dir", "release_set", "pristine_dir", "acr_dir": the AC3 / ACR values under their first names (a
                  config written before `installs` has only these; the setup keeps writing both)}

BASE files (what a build starts from - never a Studio build installed in the game): base_file(name) is the
release_set's file, else the original the Studio kept, else the game's live file. DEFAULT_SET is the folder the base
DataPC.forge (and DataPC_extra.forge with it) is read from.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = HERE                                     # (the copied modules' own folder: their out\ caches go under it)
_PF86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")


def config_path():
    """REFORGED_STUDIO_CONFIG (or the older AC3STUDIO_CONFIG), else %APPDATA%\\Reforged Studio\\config.json - or, when
    only a config from before the rename exists, %APPDATA%\\AC3 Studio\\config.json."""
    env = os.environ.get("REFORGED_STUDIO_CONFIG") or os.environ.get("AC3STUDIO_CONFIG")
    if env:
        return env
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    new, old = (os.path.join(appdata, d, "config.json") for d in ("Reforged Studio", "AC3 Studio"))
    return old if os.path.exists(old) and not os.path.exists(new) else new


def load_config():
    try:
        with open(config_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


CONFIG = load_config()
INSTALLS = {g: v for g, v in (CONFIG.get("installs") or {}).items() if isinstance(v, dict)}


def _ac3(key, first_name):
    """An AC3 install value: installs.AC3.<key>, else the config's first-version key."""
    return INSTALLS.get("AC3", {}).get(key) or CONFIG.get(first_name) or ""


# the live multiplayer folder: the one the game runs, and the only one the Studio writes to (Install / put back)
AC3 = _ac3("mp_dir", "game_dir") or os.environ.get("AC3_MULTI") or os.path.join(
    _PF86, "Ubisoft", "Ubisoft Game Launcher", "games", "Assassin's Creed III", "multi")
AC3_SINGLEPLAYER = _ac3("sp_dir", "sp_dir") or os.path.dirname(AC3)
WORKSPACE = CONFIG.get("workspace") or os.path.join(os.path.expanduser("~"), "Documents", "Reforged Studio")
ORIGINALS = os.path.join(WORKSPACE, "originals")
RELEASE_SET = _ac3("release_set", "release_set")
BLENDER = CONFIG.get("blender") or ""
# an untouched reference install (optional, read only). Without one, the game's own folder stands in: stock archives
# are read from here
_PRISTINE_SET = _ac3("pristine_dir", "pristine_dir") or os.environ.get("AC3_PRISTINE_MULTI") or ""
AC3_PRISTINE = _PRISTINE_SET or AC3
AC3_SOURCE = AC3_PRISTINE


def install(game):
    """-> dict(root, mp_dir) of another game's install (ACR, ACB, AC4): <GAME>_MULTI from the environment, else the
    config's (installs.<GAME>, or a first-version config's acr_dir); empty strings when none is set."""
    game = game.upper()
    conf = INSTALLS.get(game, {})
    mp = os.environ.get(f"{game}_MULTI") or conf.get("mp_dir") or (CONFIG.get("acr_dir") if game == "ACR" else "") or ""
    root = conf.get("root") or ""
    if not mp and root:
        mp = os.path.join(root, "multi")
    if mp and (not root or os.environ.get(f"{game}_MULTI")):
        root = os.path.dirname(os.path.normpath(mp))
    return dict(root=root, mp_dir=mp)


def install_exe(game, exe):
    """-> the path of another game's exe (beside its multiplayer folder), "" without an install."""
    root = install(game)["root"]
    return os.path.join(root, exe) if root else ""


_FOREIGN = re.compile(r"^DataPC_(ACFE_|ACR_|ACGA|AC2MP_(?!Whiteroom))", re.I)


def foreign_archive(game, name):
    """True for a map another game shipped first, in AC3's folder, while AC3_OWN_ARCHIVES is set (the map-import
    recipes: the uid universe they check every carried object against is AC3's own data, and an install may hold
    ports of the very maps being converted, under the same uids)."""
    return game == "AC3" and bool(os.environ.get("AC3_OWN_ARCHIVES")) and bool(_FOREIGN.match(name))


ACR = install("ACR")["mp_dir"]
ACR_EXTRACTED = os.path.join(ACR, "Extracted") if ACR else ""


def release_files():
    """{file name: absolute source} of the configured release_set.json ({} without one)."""
    if not RELEASE_SET or not os.path.exists(RELEASE_SET):
        return {}
    try:
        files = json.load(open(RELEASE_SET, encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError):
        return {}
    root = os.path.dirname(os.path.abspath(RELEASE_SET))
    out = {}
    for e in files:
        src = e["src"] if os.path.isabs(e["src"]) else os.path.join(root, e["src"])
        out[os.path.basename(e["dest"].replace("\\", "/"))] = src
    return out


def base_file(name):
    """The file a build of `name` starts from: the release's, else the original the Studio kept, else the live one."""
    rel = release_files().get(name)
    if rel and os.path.exists(rel):
        return rel
    kept = os.path.join(ORIGINALS, name)
    if os.path.exists(kept):
        return kept
    return os.path.join(AC3, name)


DEFAULT_DATAPC = base_file("DataPC.forge")
DEFAULT_SET = os.path.dirname(DEFAULT_DATAPC)
DEFAULT_EXTRA = os.path.join(DEFAULT_SET, "DataPC_extra.forge")
if not os.path.exists(DEFAULT_EXTRA):
    DEFAULT_EXTRA = base_file("DataPC_extra.forge")
# the exe beside the base DataPC (a release folder holds one), else the game's (the multi folder's parent)
DEFAULT_EXE = next((p for p in (os.path.join(DEFAULT_SET, "AC3MP.exe"), os.path.join(os.path.dirname(AC3), "AC3MP.exe"))
                    if os.path.exists(p)), os.path.join(os.path.dirname(AC3), "AC3MP.exe"))
AUDIO_PACKAGE = "sounds_zz_acr_mp.pck"
DEFAULT_AUDIO = os.path.join(DEFAULT_SET, AUDIO_PACKAGE)
# every other game's install and the reference copy (never the game's own folder: Install writes there)
READ_ONLY = tuple(p for p in [install(g)["root"] for g in ("ACR", "ACB", "AC4")] + [ACR, _PRISTINE_SET] if p)


def assert_writable(path):
    """Refuse to write into a read-only reference install."""
    p = os.path.normcase(os.path.abspath(path))
    for ro in READ_ONLY:
        r = os.path.normcase(os.path.abspath(ro))
        if p == r or p.startswith(r + os.sep):
            raise PermissionError(f"refusing to write into the read-only install {ro}: {path}")


def check():
    """-> [problems] of the configuration (the first-run setup shows them)."""
    out = []
    if not os.path.exists(os.path.join(AC3, "DataPC.forge")):
        out.append(f"no DataPC.forge in the game folder {AC3}")
    if not os.path.exists(DEFAULT_EXE):
        out.append(f"no AC3MP.exe beside the game folder ({DEFAULT_EXE})")
    return out
