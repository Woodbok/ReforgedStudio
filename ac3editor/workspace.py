"""Where the Studio keeps what it makes: builds, caches, the game's originals it kept, assets' renders.

The config's "workspace" (config_path(): REFORGED_STUDIO_CONFIG, else %APPDATA%\\Reforged Studio\\config.json; the
older AC3STUDIO_CONFIG and %APPDATA%\\AC3 Studio\\config.json are still read; forgelib/paths.py reads the same file).
Without a config file at all (a development checkout before the first-run setup): the repo's own out\\ folder. With a
config that names none: Documents\\Reforged Studio.
"""
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path():
    """REFORGED_STUDIO_CONFIG (or the older AC3STUDIO_CONFIG), else %APPDATA%\\Reforged Studio\\config.json - or, when
    only a config from before the rename exists, %APPDATA%\\AC3 Studio\\config.json."""
    env = os.environ.get("REFORGED_STUDIO_CONFIG") or os.environ.get("AC3STUDIO_CONFIG")
    if env:
        return env
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    new, old = (os.path.join(appdata, d, "config.json") for d in ("Reforged Studio", "AC3 Studio"))
    return old if os.path.exists(old) and not os.path.exists(new) else new


def is_release_tree():
    """A release tree carries its forge library in forgelib\\; a development checkout (or AC3_FORGE set) runs on an
    external forge library and its paths, and keeps its work in the repo's out\\ folder whatever the config says."""
    return os.path.exists(os.path.join(REPO, "forgelib", "forge.py")) and not os.environ.get("AC3_FORGE")


def _root():
    p = config_path()
    if not is_release_tree() or not os.path.exists(p):
        return os.path.join(REPO, "out")
    try:
        cfg = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    return cfg.get("workspace") or os.path.join(os.path.expanduser("~"), "Documents", "Reforged Studio")


WORK = _root()
