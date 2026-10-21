"""The maps Reforged Studio offers: the release's map archives, maps saved by tools/add_map.py, AC3's own seven.

The same list as the Blender panel's (ac3editor.blender_ui._known_maps, which imports bpy and so can't be used here).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
REPO = os.path.dirname(STUDIO)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from ac3editor.workspace import WORK  # noqa: E402  (the workspace: builds, caches)
from ac3editor import games, mapdata  # noqa: E402

FRIENDLY = {"ACFE_Souk": "Souk", "ACFE_Constantinople": "Galata", "ACFE_Antioch": "Antioch",
            "ACFE_Jerusalem": "Jerusalem", "ACFE_Dyers": "Dyers", "ACFE_Imperial": "Imperial", "ACFE_Juderia": "Juderia",
            "ACFE_Rhodes": "Rhodes", "AC2MP_Forli": "Forli", "AC2MP_Alhambra": "Alhambra",
            "AC3MP_FortMathieu": "Fort St-Mathieu", "AC3MP_Charlestown": "Charlestown", "AC2MP_SanMarco": "San Marco",
            "AC3MP_Martinique": "Martinique", "AC3MP_Boston_Harbou": "Boston Harbour",
            "AC3MP_Charlestown_dlc": "Charlestown", "AC3MP_Martinique_dlc": "Martinique", "ACGA_KINGSTON_MAP": "Kingston",
            "ACGA_Pirate_Headqua": "Pirate Headquarters", "ACR_Rome_Multi": "Rome", "ACGA_Secret_Pirate_": "Secret Pirate"}
TARGET = games.target()                     # (the game the Studio edits: its stock maps, shared archives, map prefixes)
STOCK_MAPS = TARGET.stock_maps
NOT_MAPS = TARGET.shared
PREFIXES = TARGET.prefixes


def title(path):
    base = os.path.basename(path)
    key = base[7:-6] if base.startswith("DataPC_") and base.endswith(".forge") else base
    if key in FRIENDLY:
        return FRIENDLY[key]
    for p in PREFIXES:
        if key.startswith(p):
            key = key[len(p):]
            break
    key = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key.replace("_", " "))       # MtStMichel -> Mt St Michel
    return re.sub(r"\s+", " ", key).strip()


def known_maps():
    """-> [dict(path, title, source, kind)]: the release's maps (what the game runs - AC3's own ones too where the release
    changed them), maps saved by tools/add_map.py, then AC3's untouched originals (a reference: building from one and
    installing it drops what the release changed in that map)."""
    stock, ported = [], []
    try:
        import paths  # forgelib
        pristine = getattr(paths, "_PRISTINE_SET", paths.AC3_PRISTINE)      # (a reference install only)
    except Exception:  # noqa: BLE001
        pristine = None
    for m in STOCK_MAPS:
        p = os.path.join(pristine, TARGET.stock_file(m)) if pristine else ""
        if p and os.path.exists(p):
            stock.append(dict(path=p, title=title(p), source="AC3 original (untouched reference copy)", kind="AC3"))
    from ac3editor import install
    rel = install._release_files()
    if rel:                                    # a release list (release_set.json): the release's maps
        for base, src in sorted(rel.items()):
            if not base.endswith(".forge") or base in NOT_MAPS or not os.path.exists(src):
                continue
            ported.append(dict(path=os.path.abspath(src), title=title(src),
                               source=f"release ({os.path.basename(os.path.dirname(src))})", kind="release"))
    else:                                      # none: the game folder's own maps (their kept originals once installed over)
        live = install.LIVE
        for base in sorted(os.listdir(live)) if os.path.isdir(live) else []:
            if not TARGET.is_map_file(base):
                continue
            src = install.original_of(base)
            src = src if os.path.exists(src) else os.path.join(live, base)
            ported.append(dict(path=os.path.abspath(src), title=title(src),
                               source="the game's" if src.startswith(live) else "the game's (kept original)", kind="release"))
    have = {os.path.basename(m["path"]) for m in ported}
    orig = os.path.join(WORK, "originals")
    for base in sorted(os.listdir(orig)) if os.path.isdir(orig) else []:
        if base.endswith(".forge") and base not in have:
            p = os.path.join(orig, base)
            ported.append(dict(path=p, title=title(p), source="saved original (tools/add_map.py)", kind="saved"))
    try:                                       # singleplayer areas converted in the Studio
        import sparea
        spa = sparea.built()
    except Exception:  # noqa: BLE001
        spa = []
    try:                                       # other games' maps imported in the Studio
        import mapimport
        imp = mapimport.built()
    except Exception:  # noqa: BLE001
        imp = []
    return sorted(ported, key=lambda m: m["title"].lower()) + imp + spa + stock
