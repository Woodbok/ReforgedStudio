"""Reforged Studio: a singleplayer area converted to an MP map - forgelib's SP converter (spmaps/sp_convert.py) run
from the Studio on an area picked on the SP map's overview, onto a host MP map. The result opens in the Studio like
any map, to touch up.

The recipe is the converter's whole-map surface recipe: no MP assets, the host's layout replaced, SP's own materials /
two-layer ground / normals / far view, MP handhold climbing, the host's boundaries redrawn round the new area, its
game-mode points moved onto open street. It doesn't add hand-made extras such as tunnel layers, street stairs and
shafts, or doorways.

* `overview(sp_map)`: the whole SP map from above (every placed object's box, shaded by height, a 100 m grid:
  sp_topview.overview) as a DDS the app draws, with its frame.
* `preview(sp_map, box)`: the area (and 50 m round it) from above in detail (sp_topview.render: shaded triangles)
  as a DDS, its height map, the street level (the median of the open ground's height) and what it holds.
* `build(req)`: the conversion, in a child process (big areas take minutes and gigabytes; its memory goes back when it
  ends) -> <workspace>/sparea/<name>/<host archive>. A host is a map whose base materials and boundaries the recipe knows:
  Boston Harbour (HOSTS).

Everything spmaps writes (its caches, renders) goes under the Studio's cache (<workspace>/cache/studio/sp/), seeded
from spmaps' own where it already made them; nothing is written into forgelib's folders.

    python studio/engine/sparea.py build <request.json>       (the worker runs this)
"""
import dataclasses
import hashlib
import json
import math
import os
import shutil
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ac3editor.workspace import WORK  # noqa: E402  (the workspace: builds, caches)
import splib  # noqa: E402

OUT = os.path.join(WORK, "sparea")
MAX_SIDE = 160.0                    # m: an area's largest side (Boston Harbour's play area is ~100 x 140; memory)
SP_SEA = -3.0                       # SP's sea level (Boston and New York)


def _chamfered(x0, y0, x1, y1, c):
    return [(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1), (x0 + c, y1), (x0, y1 - c),
            (x0, y0 + c)]


# the hosts the recipe knows: their archive, play-area OOB entity (+ the other boundaries redrawn with it), World grid,
# the middle of their play area, and the base materials / sea the converter's new materials and planes go on
HOSTS = {
    "Boston Harbour": dict(
        archive="DataPC_AC3MP_Boston_Harbou.forge", area_name="AC3MP_BH_OOB_001",
        inner=("AC3MP_BH_DM_OOB_001", "AC3MP_BH_Tuto_OOB_001"), grid=(-128.0, -128.0, 32.0), middle=(-2.0, -4.0),
        decal_bases=("AC3MP_BOS_Puddle_01a", "AC3MP_BOS_GroundItems_01a"), sea_material="AC3MP_BOS_Water_Sea_01A"),
}


# ------------------------------------------------------------------------------------------------ spmaps --

def _spmaps():
    """spmaps' modules with their output (renders, caches) pointed at the Studio's cache."""
    T, SA, theirs = splib._readers()
    mine = os.path.join(splib.CACHE, "topview")
    if T.TOPVIEW != mine:
        T.TOPVIEW = mine
    os.makedirs(mine, exist_ok=True)
    return T, theirs


def _seed_topview(T, stem, label):
    """spmaps' renders of the same region (same file names) copied to ours once."""
    src_dir = os.path.join(os.path.dirname(os.path.dirname(splib._readers()[2])), "topview")
    for suffix in (".png", ".json", "_height.png", "_entities.json", "_scene.bin"):
        s, d = os.path.join(src_dir, f"{stem}_{label}{suffix}"), os.path.join(T.TOPVIEW, f"{stem}_{label}{suffix}")
        if os.path.exists(s) and not os.path.exists(d):
            shutil.copyfile(s, d)
    meta = os.path.join(T.TOPVIEW, f"{stem}_{label}.json")
    if os.path.exists(meta):                  # (its height map's path is spmaps' own: point it at our copy)
        m = json.load(open(meta, encoding="utf-8"))
        hp = os.path.join(T.TOPVIEW, f"{stem}_{label}_height.png")
        if m.get("height_png") != hp and os.path.exists(hp):
            m["height_png"] = hp
            json.dump(m, open(meta, "w", encoding="utf-8"), indent=1)


def _dds_size(png, max_side=4096):
    """-> (w, h, k): the DDS's size for a render and its scale (DDS pixels per render pixel)."""
    W, H = Image_size(png)
    k = min(1.0, max_side / max(W, H))
    return int(W * k) // 4 * 4, int(H * k) // 4 * 4, k


def _dds(png, dds, max_side=4096):
    """A render (PNG) as a DXT1 DDS the app can draw (scaled to at most max_side, sides cut to multiples of 4)."""
    from PIL import Image
    w, h, k = _dds_size(png, max_side)
    im = Image.open(png).convert("RGBA")
    if k < 1.0:
        im = im.resize((int(im.size[0] * k), int(im.size[1] * k)), Image.BILINEAR)
    a = np.asarray(im)[:h, :w].copy()
    a[:, :, 3] = 255
    if not os.path.exists(dds) or os.path.getmtime(dds) < os.path.getmtime(png):
        splib.write_dds(dds, a)
    return w, h, k


def Image_size(png):
    from PIL import Image
    with Image.open(png) as im:
        return im.size


def _framed(png, meta):
    """A render + its meta -> the DDS and its edges in SP metres."""
    dds = png[:-4] + ".dds"
    w, h, k = _dds(png, dds)
    ppm = meta["px_per_m"] * k
    return dict(image=dds, w=w, h=h, x0=meta["x0"], y1=meta["y1"], x1=meta["x0"] + w / ppm, y0=meta["y1"] - h / ppm)


def overview(sp_map, log=print):
    """-> dict(image (DDS), w, h, x0, y0, x1, y1 (SP world metres at the image's edges), title)."""
    T, _theirs = _spmaps()
    stem = T.stem(sp_map)
    _seed_topview(T, stem, "overview")
    png = os.path.join(T.TOPVIEW, f"{stem}_overview.png")
    if not os.path.exists(png):
        splib._seed(T, splib._readers()[2], sp_map, "entities", T.CACHE_VERSION)
        T.overview(sp_map)
    meta = json.load(open(png[:-4] + ".json", encoding="utf-8"))
    return dict(_framed(png, meta), title=splib.title(sp_map), sp_map=sp_map)


def _label(box):
    return "studio_" + hashlib.sha1(json.dumps([round(v, 1) for v in box]).encode()).hexdigest()[:10]


def _region(box):
    return (box[0] - 50.0, box[1] - 50.0, box[2] + 50.0, box[3] + 50.0)


def preview(sp_map, box, log=print):
    """The area and 50 m round it from above -> dict(image, w, h, x0, y0, x1, y1, street_z, triangles, label)."""
    box = tuple(float(v) for v in box)
    if max(box[2] - box[0], box[3] - box[1]) > MAX_SIDE or min(box[2] - box[0], box[3] - box[1]) < 20:
        raise ValueError(f"an area is 20 - {MAX_SIDE:.0f} m a side (this one: {box[2] - box[0]:.0f} x {box[3] - box[1]:.0f} m)")
    T, _theirs = _spmaps()
    stem, label = T.stem(sp_map), _label(box)
    meta_p = os.path.join(T.TOPVIEW, f"{stem}_{label}.json")
    _seed_topview(T, stem, label)
    if not os.path.exists(meta_p):
        splib._seed(T, splib._readers()[2], sp_map, "entities", T.CACHE_VERSION)
        T.render(sp_map, _region(box), label, px_per_m=2)
    _seed_topview(T, stem, label)
    m = json.load(open(meta_p, encoding="utf-8"))
    return dict(_framed(meta_p[:-5] + ".png", m), street_z=street_level(m, box), triangles=m.get("triangles", 0),
                label=label, box=list(box), open_water=round(open_water(m, box), 3))


def open_water(m, box):
    """The share of the area where nothing stands above SP's sea (open water, gaps): the conversion has no floor
    there (spmaps' sp_verify floor-holes fails a build past 1 %) - e.g. a box on Boston's North End east side that
    reaches into the harbour."""
    from PIL import Image
    Hm = np.array(Image.open(m["height_png"]).convert("RGB")).astype(np.int64)
    z = (Hm[:, :, 0] * 65536 + Hm[:, :, 1] * 256 + Hm[:, :, 2]) / 100.0 - 1000.0
    k = m["px_per_m"]
    X0, X1 = int((box[0] - m["x0"]) * k), int((box[2] - m["x0"]) * k)
    Y0, Y1 = int((m["y1"] - box[3]) * k), int((m["y1"] - box[1]) * k)
    sub, hz = z[max(Y0, 0):Y1, max(X0, 0):X1], Hm[max(Y0, 0):Y1, max(X0, 0):X1].sum(2) > 0
    if not sub.size:
        return 0.0
    return float(((~hz) | (sub <= SP_SEA + 0.5)).mean())


def street_level(m, box):
    """The area's street level: the commonest height (0.25 m) of its surfaces in the low half - SP New York's Wall
    Street is at 4.1 - 4.9 (a shift of about -4.5), Boston's North End at ~0 (no shift)."""
    from PIL import Image
    Hm = np.array(Image.open(m["height_png"]).convert("RGB")).astype(np.int64)
    z = (Hm[:, :, 0] * 65536 + Hm[:, :, 1] * 256 + Hm[:, :, 2]) / 100.0 - 1000.0
    k = m["px_per_m"]
    X0, X1 = int((box[0] - m["x0"]) * k), int((box[2] - m["x0"]) * k)
    Y0, Y1 = int((m["y1"] - box[3]) * k), int((m["y1"] - box[1]) * k)
    v = z[max(Y0, 0):Y1, max(X0, 0):X1][Hm[max(Y0, 0):Y1, max(X0, 0):X1].sum(2) > 0]
    v = v[v > SP_SEA + 0.5]
    if not len(v):
        return 0.0
    low = v[v <= np.percentile(v, 50)]
    vals, counts = np.unique(np.round(low * 4) / 4, return_counts=True)
    return float(vals[counts.argmax()])


# ------------------------------------------------------------------------------------------------- build --

def default_name(req):
    box = req["box"]
    return req.get("name") or f"SPA_{splib.title(req['sp_map'])[4:].replace(' ', '').replace(':', '')}_{int(box[0])}_{int(box[1])}"


def run_child(req, log=print):
    """build() in a child process (minutes, gigabytes: the memory goes back when it ends) -> its result."""
    import subprocess
    name = default_name(req)
    os.makedirs(os.path.join(OUT, name), exist_ok=True)
    rq = os.path.join(OUT, name, "request.json")
    with open(rq, "w", encoding="utf-8") as f:
        json.dump(dict(req, name=name), f, indent=1)
    res = os.path.join(OUT, name, "sparea.json")
    if os.path.exists(res):
        os.remove(res)
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
    if code or not os.path.exists(res):
        raise RuntimeError(f"the conversion failed (exit {code}): " + " / ".join(tail[-3:]))
    with open(res, encoding="utf-8") as f:
        out = json.load(f)
    # spmaps' own checks (sp_verify.py: read-only; the failures known to break a converted map in game), on a config
    # module made from the request
    cfgmod = os.path.join(OUT, name, "sparea_config.py")
    with open(cfgmod, "w", encoding="utf-8") as f:
        f.write("import json, sys\nsys.path.insert(0, %r)\nimport sparea\nCONFIG = sparea.config(json.load(open(%r)))\n"
                % (HERE, rq))
    import splib as _s
    ver = os.path.join(_s.mapdata.FORGE_REPO, "spmaps", "sp_verify.py")
    p = subprocess.run([sys.executable, ver, out["dest"], cfgmod], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    checks = [ln.rstrip() for ln in p.stdout.splitlines() if ln[:4] in ("ok  ", "FAIL", "WARN")]
    for ln in checks:
        log("  " + ln)
    out.update(checks=checks, fails=[ln for ln in checks if ln.startswith("FAIL")], verified=p.returncode == 0)
    with open(res, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def built():
    """-> [dict(path, title, source, kind="sparea")] of the areas converted so far (the Maps list)."""
    out = []
    for name in sorted(os.listdir(OUT)) if os.path.isdir(OUT) else []:
        res = os.path.join(OUT, name, "sparea.json")
        if not os.path.exists(res):
            continue
        r = json.load(open(res, encoding="utf-8"))
        if os.path.exists(r["dest"]):
            out.append(dict(path=r["dest"], title="SP area: " + name, kind="sparea",
                            source=f"singleplayer area ({splib.title(r['sp_map'])[4:]} onto {r['host']})"))
    return out


def config(req):
    """The request -> a spmaps Config (the whole-map surface recipe on the host)."""
    T, _theirs = _spmaps()
    import sp_convert as SC
    host = HOSTS[req.get("host", "Boston Harbour")]
    box = tuple(float(v) for v in req["box"])
    w, h = box[2] - box[0], box[3] - box[1]
    mx, my = host["middle"]
    area = (round(mx - w / 2, 2), round(my - h / 2, 2), round(mx + w / 2, 2), round(my + h / 2, 2))
    sz = float(req.get("street_z", 0.0))
    shift = (area[0] - box[0], area[1] - box[1], -sz)
    main = _chamfered(*area, min(12.0, min(w, h) * 0.1))
    cx, cy = (area[0] + area[2]) / 2, (area[1] + area[3]) / 2
    inner = [(cx - w * 0.2, cy - h * 0.2), (cx + w * 0.2, cy - h * 0.2), (cx + w * 0.2, cy + h * 0.2), (cx - w * 0.2, cy + h * 0.2)]
    boundary = {host["area_name"]: main}
    for n in host["inner"]:
        boundary[n] = inner
    name = default_name(req)
    label = _label(box)
    return SC.Config(
        name=name, sp_map=os.path.basename(req["sp_map"]), box=box, archive=host["archive"], area=area,
        area_name=host["area_name"], grid=host["grid"], prefix="SPA", shift=shift,
        height_label=label, height_region=_region(box),
        water="", min_top=-10.0 + sz, boundary=boundary, nearest_cell=True, whole_buildings=True,
        fill_alpha_holes=True, remove_ring=30.0, two_layer=True, far_ground_gap=3.0, far_ground_drop=0.3,
        hold_gaps=True, decal_bases=host["decal_bases"], two_sided_cloth=True, select_visible=True,
        two_layer_blend=(0.0, 0.0), world_lightmap=(180, (18, 12, 5)), sp_normals=True, two_layer_own=True,
        prop_boxes=True, keep=r"Sky|_OOB|OOB_", replace_target=True, mp_twins=False, mat_twins=False,
        standins_off=True, view_distance=255, far_window=0.0, far_lod=1, far_lod_radius=300.0, far_radius=450.0,
        far_erode=0.0, sea=(SP_SEA - sz, 2000.0, 100.0, host["sea_material"]), mesh_fit_slack=(1.6, 1.0), dedupe=True)


def build(req, log=print):
    """-> dict(dest, name, stats, moved points, seconds). In this process (the worker runs it as a child)."""
    t0 = time.time()
    T, _theirs = _spmaps()
    import sp_convert as SC
    cfg = config(req)
    _seed_topview(T, T.stem(cfg.sp_map), cfg.height_label)
    log(f"SP area: {splib.title(req['sp_map'])} x {cfg.box[0]:.0f}..{cfg.box[2]:.0f}, y {cfg.box[1]:.0f}..{cfg.box[3]:.0f} "
        f"(street level {-cfg.shift[2]:.2f}) onto {req.get('host', 'Boston Harbour')} as {cfg.name}")
    cv = SC.Convert(cfg)
    cv.out = os.path.join(OUT, cfg.name, cfg.archive)
    os.makedirs(os.path.dirname(cv.out), exist_ok=True)
    P = cv.plan(log=log)
    log(f"plan: {P['stats']}")
    moves = cv.build(P, log=log)
    # the converter stores its records unpacked (Wall Street: 1.49 GB): packed as the game's own files are
    from ac3editor import archive
    size0, size1 = archive.pack(cv.out, cv.src, log=log)
    log(f"packed: {size0 / 1e6:.0f} MB -> {size1 / 1e6:.0f} MB")
    out = dict(dest=cv.out, name=cfg.name, stats={k: (v if isinstance(v, (int, float, str)) else str(v))
                                                 for k, v in P["stats"].items()},
               points_moved=len(moves), seconds=round(time.time() - t0), area=list(cfg.area), shift=list(cfg.shift),
               host=req.get("host", "Boston Harbour"), sp_map=req["sp_map"], box=list(cfg.box))
    with open(os.path.join(OUT, cfg.name, "sparea.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    log(f"SP area built in {out['seconds']} s -> {cv.out}")
    return out


def main(argv):
    if len(argv) >= 2 and argv[0] == "build":
        with open(argv[1], encoding="utf-8") as f:
            req = json.load(f)
        build(req)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
