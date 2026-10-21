"""Putting a test build into the live game and back.

The live game is `paths.AC3`: the game's multiplayer folder from the Studio's config (forgelib/paths.py, or a
development setup's own paths module). What put-back copies in:
* the file a configured release_set.json lists for that name, else
* the game's own file, kept in <workspace>\\originals the first time the Studio installed over it (keep_original), else
* an untouched reference install's (paths.AC3_PRISTINE, when one is configured).
"""
import json
import os
import shutil

from . import mapdata as M
from .workspace import WORK

import paths  # noqa: E402  (forgelib's, or a development setup's)

LIVE = paths.AC3
PRISTINE = getattr(paths, "_PRISTINE_SET", getattr(paths, "AC3_PRISTINE", "")) or ""   # (a reference install only)
ORIGINALS = getattr(paths, "ORIGINALS", None) or os.path.join(WORK, "originals")
# files whose originals are kept together (a build of one reads the other as it was)
TOGETHER = {"DataPC.forge": ("DataPC_extra.forge",), "DataPC_extra.forge": ("DataPC.forge",)}


def _release_files():
    if hasattr(paths, "release_files"):
        return paths.release_files()
    try:                                          # (a development setup: release_set.json in the forge repo)
        with open(os.path.join(M.FORGE_REPO, "release_set.json")) as f:
            files = json.load(f)["files"]
    except (OSError, ValueError, KeyError):
        return {}
    return {os.path.basename(e["dest"].replace("\\", "/")): (e["src"] if os.path.isabs(e["src"])
                                                            else os.path.join(M.FORGE_REPO, e["src"])) for e in files}


def original_of(name):
    """-> path of the file put-back copies in for archive `name` (it may not exist: nothing kept yet)."""
    rel = _release_files().get(name)
    if rel and os.path.exists(rel):
        return rel
    kept = os.path.join(ORIGINALS, name)
    if os.path.exists(kept) or not PRISTINE:
        return kept
    return os.path.join(PRISTINE, name)


def keep_original(name, log=None):
    """Before the Studio first writes over the game's `name`: keep the game's own copy in <workspace>\\originals (and
    its partner: DataPC with DataPC_extra), unless a release_set or an earlier keep already has one. -> [kept]."""
    out = []
    for n in (name,) + TOGETHER.get(name, ()):
        rel = _release_files().get(n)
        if rel and os.path.exists(rel):
            continue
        kept, live = os.path.join(ORIGINALS, n), os.path.join(LIVE, n)
        if os.path.exists(kept) or not os.path.exists(live):
            continue
        os.makedirs(ORIGINALS, exist_ok=True)
        if log:
            log(f"keeping the game's own {n} ({os.path.getsize(live) / 2**20:.0f} MB) in {ORIGINALS} - put-back copies it in")
        shutil.copyfile(live, kept + ".part")
        os.replace(kept + ".part", kept)
        out.append(n)
    return out


FIX = ("the game's {name} is owned by Administrators now (something running as administrator replaced it), so your "
       "account can't write it. Once, in PowerShell run as administrator: icacls \"{dest}\" /setowner \"{user}\" "
       "then icacls \"{dest}\" /grant \"{user}:F\"")


def _copy(src, dest):
    try:
        shutil.copyfile(src, dest)
    except PermissionError:
        user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
        raise PermissionError(FIX.format(name=os.path.basename(dest), dest=dest, user=user)) from None


def install(path, log=None):
    name = os.path.basename(path)
    dest = os.path.join(LIVE, name)
    if not os.path.exists(dest):
        raise FileNotFoundError(f"the game has no {name}")
    keep_original(name, log)
    _copy(path, dest)
    return dest


def restore(name):
    src = original_of(name)
    if not os.path.exists(src):
        raise FileNotFoundError(f"no original for {name} (the Studio never installed over it)")
    dest = os.path.join(LIVE, name)
    _copy(src, dest)
    return src, dest


def is_original(name):
    """True when the live file has the original's size and bytes (cheap size check first)."""
    live, orig = os.path.join(LIVE, name), original_of(name)
    if not os.path.exists(live):
        return False
    if not os.path.exists(orig):
        return True                                # (nothing kept: the Studio never wrote over it)
    if os.path.getsize(live) != os.path.getsize(orig):
        return False
    with open(live, "rb") as a, open(orig, "rb") as b:
        while True:
            x, y = a.read(1 << 22), b.read(1 << 22)
            if x != y:
                return False
            if not x:
                return True
