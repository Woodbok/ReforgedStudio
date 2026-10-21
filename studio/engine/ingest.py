"""Reads the user's game once, after the first-run setup (worker `ingest`, a child process): the configuration's
problems, the maps the game folder holds, the personas (the Characters workspace's list), and the id census the
character builds draw new uids clear of - every result cached in the workspace, so later uses are instant.

    python studio/engine/ingest.py [--json]
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def ingest(log=print):
    t0 = time.time()
    from ac3editor import mapdata  # noqa: F401  (puts the forge code on the path)
    import paths
    problems = list(paths.check()) if hasattr(paths, "check") else []
    out = dict(game=paths.AC3, workspace=getattr(paths, "WORKSPACE", ""), problems=problems)
    if problems:
        return out
    import maps
    ms = maps.known_maps()
    out["maps"] = len(ms)
    log(f"ingest: {len(ms)} maps in {paths.AC3}")
    import charview
    ps = charview.personas(log=log)
    out["personas"] = len(ps)
    import charnew
    ids = charnew.census(log=log)
    out["ids"] = int(len(ids))
    out["seconds"] = round(time.time() - t0, 1)
    log(f"ingest: done in {out['seconds']} s")
    return out


if __name__ == "__main__":
    as_json = "--json" in sys.argv
    log = (lambda m: print(m, file=sys.stderr, flush=True)) if as_json else print
    try:
        res = ingest(log)
    except Exception as e:  # noqa: BLE001
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(res) if as_json else json.dumps(res, indent=1))
