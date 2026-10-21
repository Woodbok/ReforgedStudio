"""Game profiles: what the Studio knows about each game it reads or edits (studio/games/<GAME>_<EDITION>.json).

A profile is one (game, edition): AC3/MP, ACR/MP, ACB/MP, AC4/MP. The target is the profile the Studio edits and builds
for (AC3/MP); every other configured install is a read-only source. Code asks the profile (`target()`,
`profile("ACR")`) for a game's values instead of naming them inline.

Installs come from the config (config v2: `installs` per game; a v1 config's `game_dir` / `sp_dir` / `acr_dir` are read
the same way), else from the profile's usual places (`detect`). The target's multiplayer folder is always forgelib's
`paths.AC3` - the folder Install writes to.

    python -m ac3editor.games            (the profiles and the installs found)
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GAMES_DIR = os.path.join(REPO, "studio", "games")
ORDER = ("AC3", "ACR", "ACB", "AC4")
TARGET = ("AC3", "MP")

_PROFILES = {}


def _expand(p):
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    return p.replace("%PF86%", pf86).replace("%PF%", pf)


def load_config():
    """The Studio's config (workspace.config_path()), {} without one."""
    from .workspace import config_path
    try:
        with open(config_path(), encoding="utf-8") as fh:
            c = json.load(fh)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def config_installs(cfg):
    """{game: {"root", "mp_dir", ...}} from a config of either version: v2's `installs`, plus a v1 config's keys
    (`game_dir` -> AC3's mp_dir, `sp_dir` -> AC3's sp_dir, `acr_dir` -> ACR's mp_dir) where v2 names nothing."""
    out = {g: dict(v) for g, v in (cfg.get("installs") or {}).items() if isinstance(v, dict)}
    ac3 = out.setdefault("AC3", {})
    if cfg.get("game_dir") and not ac3.get("mp_dir"):
        ac3["mp_dir"] = cfg["game_dir"]
    if cfg.get("sp_dir") and not ac3.get("sp_dir"):
        ac3["sp_dir"] = cfg["sp_dir"]
    for k in ("release_set", "pristine_dir"):
        if cfg.get(k) and not ac3.get(k):
            ac3[k] = cfg[k]
    if cfg.get("acr_dir") and not out.get("ACR", {}).get("mp_dir"):
        out.setdefault("ACR", {})["mp_dir"] = cfg["acr_dir"]
    for g, v in out.items():
        if v.get("mp_dir") and not v.get("root"):
            v["root"] = os.path.dirname(os.path.normpath(v["mp_dir"]))
    return {g: v for g, v in out.items() if v}


class Profile:
    def __init__(self, data):
        self.data = data
        self.game, self.edition = data["game"], data["edition"]
        self.key = f"{self.game}/{self.edition}"
        self.title = data.get("title", self.key)
        self.short = data.get("short", self.game)

    def __repr__(self):
        return f"<Profile {self.key}>"

    # ---- install
    @property
    def exe_name(self):
        return self.data["install"]["exe"]

    @property
    def mp_subdir(self):
        return self.data["install"].get("mp_subdir", "multi")

    def is_install(self, mp_dir):
        """True when a folder is this game's multiplayer folder (its marker archives are there)."""
        return bool(mp_dir) and all(os.path.exists(os.path.join(mp_dir, m)) for m in self.data["install"]["markers"])

    def mp_dir_of(self, folder):
        """A folder the user picked (the game folder or its multiplayer folder) -> the multiplayer folder, or None."""
        folder = os.path.normpath(folder) if folder else ""
        if not folder:
            return None
        # (the multiplayer subfolder first: a game folder holds singleplayer archives under the same names)
        for p in (os.path.join(folder, self.mp_subdir), folder):
            if self.is_install(p):
                return p
        return None

    def detect(self):
        """-> the multiplayer folder at one of the profile's usual places, or None."""
        for g in self.data["install"].get("guesses", []):
            p = self.mp_dir_of(_expand(g))
            if p:
                return p
        return None

    def install(self, cfg=None):
        """-> dict(root, mp_dir, exe, found, configured) of this game's install: the config's, else a detected one.
        AC3's multiplayer folder is forgelib's paths.AC3 (the folder Install writes to)."""
        cfg = load_config() if cfg is None else cfg
        conf = config_installs(cfg).get(self.game, {})
        mp = conf.get("mp_dir") or (self.mp_dir_of(conf["root"]) if conf.get("root") else None)
        if self.game == TARGET[0]:
            from . import mapdata  # noqa: F401  (puts forgelib on the path)
            import paths  # noqa: E402  (forgelib's, or a development setup's)
            mp = paths.AC3
        configured = bool(mp)
        if not mp:
            mp = self.detect()
        root = conf.get("root") or (os.path.dirname(os.path.normpath(mp)) if mp else "")
        return dict(root=root, mp_dir=mp or "", exe=os.path.join(root, self.exe_name) if root else "",
                    found=self.is_install(mp), configured=configured)

    # ---- archives and maps
    @property
    def shared(self):
        return tuple(self.data["archives"]["shared"])

    def is_map_file(self, base):
        a = self.data["archives"]
        return (base.startswith(a.get("map_prefix", "DataPC_")) and base.endswith(a.get("map_suffix", ".forge"))
                and base not in self.shared and not any(base.startswith(x) for x in a.get("map_exclude", ())))

    def map_files(self, mp_dir=None):
        """-> [archive path] of the install's map archives."""
        mp_dir = mp_dir or self.install()["mp_dir"]
        if not mp_dir or not os.path.isdir(mp_dir):
            return []
        return [os.path.join(mp_dir, b) for b in sorted(os.listdir(mp_dir)) if self.is_map_file(b)]

    @property
    def stock_maps(self):
        return tuple(self.data.get("maps", {}).get("stock", ()))

    def stock_file(self, m):
        return self.data["maps"]["stock_pattern"].format(m)

    @property
    def prefixes(self):
        return tuple(self.data.get("maps", {}).get("prefixes", ()))

    # ---- ids
    def id_ok(self, u):
        """True when a 32-bit uid may be given to a new object: above the low range, not in a range the game's handle
        manager never maps, low 16 bits non-zero (the uniqueness against the ids in use is the caller's)."""
        ids = self.data["ids"]
        if u < ids.get("min", 0):
            return False
        if ids.get("low16_nonzero") and not (u & 0xFFFF):
            return False
        return not any(lo <= u <= hi for lo, hi in ids.get("unmapped", ()))

    @property
    def fresh_range(self):
        """[low, high) new uids are drawn from."""
        lo, hi = self.data["ids"]["fresh"]
        return lo, hi

    # ---- capabilities
    def can(self, what):
        return bool(self.data.get("capabilities", {}).get(what))


def profile(game, edition="MP"):
    key = (game.upper(), edition.upper())
    if key not in _PROFILES:
        path = os.path.join(GAMES_DIR, f"{key[0]}_{key[1]}.json")
        if not os.path.exists(path):
            raise KeyError(f"no profile for {key[0]}/{key[1]} ({path})")
        with open(path, encoding="utf-8") as fh:
            _PROFILES[key] = Profile(json.load(fh))
    return _PROFILES[key]


def target():
    """The profile the Studio edits and builds for (AC3/MP: the only target so far)."""
    return profile(*TARGET)


def profiles():
    """Every profile there is, the target's game first."""
    out = []
    for f in sorted(os.listdir(GAMES_DIR)):
        m = re.match(r"^([A-Z0-9]+)_([A-Z]+)\.json$", f)
        if m:
            out.append(profile(m.group(1), m.group(2)))
    return sorted(out, key=lambda p: (ORDER.index(p.game) if p.game in ORDER else 99, p.edition))


def sources(cfg=None):
    """-> [(profile, install)] of the other games' installs found (read-only sources), in ORDER."""
    cfg = load_config() if cfg is None else cfg
    out = []
    for p in profiles():
        if (p.game, p.edition) == TARGET:
            continue
        inst = p.install(cfg)
        if inst["found"]:
            out.append((p, inst))
    return out


def read_only_dirs(cfg=None):
    """Every source install's folder: never written to."""
    return [i["root"] for _p, i in sources(cfg) if i["root"]]


def assert_source_untouched(path, cfg=None):
    """Refuse a write into any source install."""
    p = os.path.normcase(os.path.abspath(path))
    for ro in read_only_dirs(cfg):
        r = os.path.normcase(os.path.abspath(ro))
        if p == r or p.startswith(r + os.sep):
            raise PermissionError(f"refusing to write into the read-only install {ro}: {path}")


def main(argv):
    for p in profiles():
        i = p.install()
        state = ("configured" if i["configured"] else "found") if i["found"] else "not found"
        print(f"{p.key:7} {p.title:45} {state:10} {i['mp_dir']}")
        if i["found"] and (p.game, p.edition) != TARGET:
            print(f"        {len(p.map_files(i['mp_dir']))} map archive(s)")
    return 0


if __name__ == "__main__":
    REPO in sys.path or sys.path.insert(0, REPO)
    sys.exit(main(sys.argv[1:]))
