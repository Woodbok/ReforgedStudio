"""Reforged Studio's map settings: the open map's lighting (its time-of-day table), its kill height and view distance.

    read(archive)                 -> what the Map panel shows: the curated controls, presets, every curve by hour,
                                     the World's WaterLevel / DeathHeight / viewing and loading distances
    preview(archive, settings)    -> the curves as `settings` would leave them (the panel's after-values)
    apply(src, dest, settings)    -> the build pass (plan.py "map_settings"): writes dest
    verify(src, dest, settings)   -> (ok, lines): the read-back

Lighting is a World's `PropertyControllerData` (class 0x3255221E): ~84 curves, each a property path (crc32 of the
editor names: LayeredSky / SunLightColor / Color...) keyed at hour x 120 - sun colour, intensity and direction, the
world's and the characters' ambient (6 faces + Scale), fog colour / near / far / density, the post grade (gamma,
brightness, saturation, contrast), the AssassinFilter's desaturation towards its tint (the sepia), sky colours. A map
can hold more than one (Fort Wolcott's snow, Boston Mill's rain overlays: a few curves each); the map's own is the one
with the most curves. The format code comes from forgelib: `ac4/tod.py` (parse: every curve's class, keys, values,
tail) and `maps/tod_snow.py` (entries); `_entry` rebuilds one entry from its parsed curve and is checked to give the
stock bytes back before any edit. Names for the read-only view: forgelib's witcher3/tod_w3.names(), else hex.

The settings (the build request's "map_settings"):
    {"tod": {"<control id>": {"v": value, "all": false}, ...},       (all: every key, else the day keys 4.5 - 19.5 h)
     "water_level": -15.0 | null,                                    (the World's WaterLevel: the kill height)
     "view_distance": 63 | null,                                     (GridLayout DefaultViewingDistance, m, 1 - 255)
     "stand_ins": false | null}                                      (false: the World's stand-in tiles switched off)
A control's op on each key's value x: "mul" x * v (a colour's r, g, b; alpha kept), "toward" x + (target - x) * v
(target: a number, "white", or "sky" - the map's own sky colour at that key), "add" x + v (degrees). The "Clear day"
preset (id LT3): the AssassinFilter's desaturation off (every key); on the day keys gamma halfway to 1, brightness 1.0,
world ambient x 0.7, character ambient x 0.8, the sun 35 % towards white and x 1.1, fog 70 % towards the sky colour,
near x 1.6, far x 2.33, density x 0.75.

The kill height and view distance are set with forgelib's spmaps/sp_convert.set_water_level / set_view_distance: a
player below WaterLevel dies (Wolcott -15, Boston Harbour -10); the World's DeathHeight (-500) is NOT the kill height
(it never kills). View distance: stock 63 m (the camera's cell +- 2; farther cells are drawn by the World's stand-in
tiles); 255 draws every loaded cell in full. The loading distance is left as it is (setting both to 255 hangs the game
on the lineup).

Stand-ins off (a bare map): the World's far-view tiles - one merged low-detail mesh per 128 m square, drawn for every
cell farther than the view distance - still show the map as it was; `stand_ins: false` points each tile's first leaf
past every record so none draws (forgelib's witcher3/fakes.hide_stand_ins). Farther cells then show sky: pair it with
view distance 255 (every loaded cell, +-96 m, drawn).

The Studio's own renderer doesn't show AC3's lighting (no sun, fog or grade): the panel says so; it's judged in game.
"""
import os
import shutil
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ac3editor import archive as A, mapdata as M  # noqa: E402  (mapdata first: forgelib on the path)

FORGE = M.FORGE_REPO
for _p in (os.path.join(FORGE, "ac4"), os.path.join(FORGE, "maps")):
    if _p not in sys.path:
        sys.path.append(_p)
import tod  # noqa: E402  (forgelib ac4/tod.py: parse, _at, SIZES, REF)
import tod_snow  # noqa: E402  (forgelib maps/tod_snow.py: entries, TOD)

TOD_CLASS = tod_snow.TOD
H = lambda n: zlib.crc32(n.encode())                        # noqa: E731  (path ids: crc32 of the editor names)
LS, FOG, POST, COL = 0x24D32CF8, 0xBAE5ABF3, 0x5FB26017, 0xC0A9C490
HOUR = tod.KEYS_PER_HOUR
DAY = (4.5, 19.5)                                           # day keys: strictly between
AMBIENCES = (("Night", 0.0), ("Day", 12.0), ("Evening", 20.0))   # the menu's three ambiences
FACES = ("Top", "Down", "Front", "Back", "Left", "Right")
SKY = (LS, 0x02C2D99C, COL)                                 # the sky colour the Clear day preset pulls the fog towards

# the Map panel's controls: id, group, label, paths, op, target, neutral, range, format, tip
CONTROLS = [
    dict(id="sun_white", group="Sun", label="Sun colour towards white", paths=[(LS, H("SunLightColor"), COL)],
         op="toward", target="white", lo=0, hi=1, fmt="%.2f", tip="0 = the map's colour, 1 = pure white"),
    dict(id="sun_int", group="Sun", label="Sun intensity", paths=[(LS, H("SunLightIntensity"))],
         op="mul", lo=0, hi=3, fmt="x %.2f"),
    dict(id="csun_white", group="Sun", label="Character sun towards white",
         paths=[(LS, H("CharacterSunLightColor"), COL)], op="toward", target="white", lo=0, hi=1, fmt="%.2f",
         tip="The sun on characters (a light of its own)"),
    dict(id="csun_int", group="Sun", label="Character sun intensity", paths=[(LS, H("CharacterSunLightIntensity"))],
         op="mul", lo=0, hi=3, fmt="x %.2f"),
    dict(id="sun_az", group="Sun", label="Sun direction (turn)", op="add", lo=-180, hi=180, fmt="%+.0f deg",
         paths=[(LS, H("SunLightAzimuth")), (LS, H("CharacterSunLightAzimuth")), (LS, 0x1EEE3E23)],
         tip="Turns the sun, the characters' sun and the sky's sun disc together"),
    dict(id="sun_el", group="Sun", label="Sun height", op="add", lo=-40, hi=30, fmt="%+.0f deg",
         paths=[(LS, H("SunLightElevation")), (LS, H("CharacterSunLightElevation")), (LS, 0xAB55F715)],
         tip="Raises / lowers the sun, the characters' sun and the sky's sun disc together"),
    dict(id="amb_world", group="Ambient (shade)", label="World ambient", op="mul", lo=0, hi=2, fmt="x %.2f",
         paths=[(LS, H("EnvironmentAmbient"), H("Scale"))], tip="The light in the shade: lower = darker shadows"),
    dict(id="amb_char", group="Ambient (shade)", label="Character ambient", op="mul", lo=0, hi=2, fmt="x %.2f",
         paths=[(LS, H("CharacterAmbient"), H("Scale"))]),
    dict(id="amb_world_faces", group="Ambient (shade)", label="World ambient colours (6 faces)", op="mul", lo=0, hi=2,
         fmt="x %.2f", paths=[(LS, H("EnvironmentAmbient"), H(f)) for f in FACES]),
    dict(id="amb_char_faces", group="Ambient (shade)", label="Character ambient colours (6 faces)", op="mul", lo=0,
         hi=2, fmt="x %.2f", paths=[(LS, H("CharacterAmbient"), H(f)) for f in FACES]),
    dict(id="fog_sky", group="Fog", label="Fog colour towards the sky's", paths=[(FOG, H("FogColor"), COL)],
         op="toward", target="sky", lo=0, hi=1, fmt="%.2f", tip="The sky colour at the same hour (LayeredSky 02C2D99C)"),
    dict(id="fog_near", group="Fog", label="Fog starts (near)", paths=[(FOG, H("FogNear"))], op="mul", lo=0, hi=5,
         fmt="x %.2f"),
    dict(id="fog_far", group="Fog", label="Fog full (far)", paths=[(FOG, H("FogFar"))], op="mul", lo=0.1, hi=5,
         fmt="x %.2f"),
    dict(id="fog_dens", group="Fog", label="Fog density", paths=[(FOG, H("FogDensity"))], op="mul", lo=0, hi=2,
         fmt="x %.2f"),
    dict(id="gamma", group="Grade", label="Gamma towards 1", op="toward", target=1.0, lo=0, hi=1, fmt="%.2f",
         paths=[(POST, H("ColorBalanceSettings"), H("Gamma"))], tip="Stock maps run 1.4 - 1.6 (washed out); 1 = none"),
    dict(id="bright", group="Grade", label="Brightness towards 1", op="toward", target=1.0, lo=0, hi=1, fmt="%.2f",
         paths=[(POST, H("ColourControlSettings"), H("Brightness"))]),
    dict(id="sat", group="Grade", label="Saturation", op="mul", lo=0, hi=2, fmt="x %.2f",
         paths=[(POST, H("ColourControlSettings"), H("Saturation"))]),
    dict(id="contrast", group="Grade", label="Contrast", op="mul", lo=0.5, hi=1.5, fmt="x %.2f",
         paths=[(POST, H("ColourControlSettings"), H("Contrast"))]),
    dict(id="desat", group="Assassin filter (sepia)", label="Desaturation off", op="toward", target=0.0, lo=0, hi=1,
         fmt="%.2f", paths=[(POST, H("AssassinFilterSettings"), H("DesaturateIntensity"))],
         tip="Stock: half of every pixel pulled towards the tint below; 1 = no pull"),
    dict(id="tint_white", group="Assassin filter (sepia)", label="Its tint towards white", op="toward",
         target="white", lo=0, hi=1, fmt="%.2f", paths=[(POST, H("AssassinFilterSettings"), 0xB0C02CE4, COL)]),
    dict(id="sky_col", group="Sky", label="Sky colour", op="mul", lo=0, hi=2, fmt="x %.2f", paths=[SKY],
         tip="LayeredSky 02C2D99C (the colour the Clear day preset's fog goes towards)"),
    dict(id="sky_col2", group="Sky", label="Sky colour 2 (warm)", op="mul", lo=0, hi=2, fmt="x %.2f",
         paths=[(LS, 0xCB26735F, COL)], tip="LayeredSky CB26735F (warm at noon on most maps)"),
]
for _c in CONTROLS:
    _c["neutral"] = 1.0 if _c["op"] == "mul" else 0.0
BY_ID = {c["id"]: c for c in CONTROLS}

# presets: {control: (value, every key?)}; "As the map has it" = none
LT3 = {"desat": (1.0, True), "gamma": (0.5, False), "bright": (1.0, False), "amb_world": (0.7, False),
       "amb_char": (0.8, False), "sun_white": (0.35, False), "sun_int": (1.1, False), "fog_sky": (0.7, False),
       "fog_near": (40 / 25, False), "fog_far": (350 / 150, False), "fog_dens": (0.6 / 0.8, False)}
PRESETS = [
    dict(id="map", label="As the map has it", tip="No lighting change", tod={}),
    dict(id="LT3", label="Clear day", tip="A clear-day look: "
         "no sepia, truer sun and shade, fog towards the sky colour", tod=LT3),
    dict(id="LT4a", label="Clear day, lighter shade", tip="Clear day with the world ambient x 0.85 instead of x 0.7 "
         "(lighter dark areas)", tod=dict(LT3, amb_world=(0.85, False))),
]


# --------------------------------------------------------------------------------------------- the table --

def _is_day(k):
    return DAY[0] < k / HOUR < DAY[1]


def _entry(seg, c, vals):
    """One TOD entry with its curve's values replaced (keys, count, extra and tail as they are)."""
    k = seg.rfind(tod.REF) + 42 + 3
    fmt, _sz = tod.SIZES[c["cls"]]
    out = bytearray(seg[:k]) + struct.pack("<IHH", c["cls"], c["n"], c.get("extra", 0))
    out += struct.pack(f"<{c['n'] - 1}H", *c["keys"][1:])
    for v in vals:
        out += struct.pack("<" + fmt, *v)
    return bytes(out + c["tail"])


def find_tods(archive):
    """[(record, name, body)] of every PropertyControllerData in the archive."""
    return [(rec, name, body) for rec, name, t, body, _f in M.iter_objects_ex(archive) if t == TOD_CLASS]


def main_tod(archive):
    """The map's own time-of-day table: the PropertyControllerData with the most curves -> (record, name, body)."""
    tods = find_tods(archive)
    if not tods:
        raise ValueError(f"{os.path.basename(archive)} has no time-of-day table (PropertyControllerData)")
    return max(tods, key=lambda x: len(tod_snow.entries(x[2])))


_NAMER = []


def namer():
    """path -> 'LayeredSky / SunLightColor / C0A9C490' (forgelib's witcher3/tod_w3.names when its names file is
    present; without it, our controls' names and hex)."""
    if not _NAMER:
        fn = None
        try:
            sys.path.append(os.path.join(FORGE, "witcher3"))
            sys.path.append(os.path.join(FORGE, "maps", "port"))
            import tod_w3
            fn = tod_w3.names()
        except Exception:  # noqa: BLE001 - the names are a nicety
            fn = None
        own = {LS: "LayeredSky", FOG: "Fog", POST: "PostFX"}
        for n in ("SunLightColor", "CharacterSunLightColor", "SunLightIntensity", "CharacterSunLightIntensity",
                  "SunLightAzimuth", "SunLightElevation", "CharacterSunLightAzimuth", "CharacterSunLightElevation",
                  "EnvironmentAmbient", "CharacterAmbient", "Scale", "Tint", "Saturation", "FogColor", "FogNear",
                  "FogFar", "FogDensity", "ColorBalanceSettings", "ColourControlSettings", "Gamma", "Brightness",
                  "Contrast", "AssassinFilterSettings", "DesaturateIntensity") + FACES:
            own[H(n)] = n
        own[COL] = "Color"

        def name(path):
            got = fn(path) if fn else " / ".join(f"{x:08X}" for x in path)
            parts = got.split(" / ")
            return " / ".join(own.get(x, p) if p == f"{x:08X}" else p for x, p in zip(path, parts))
        _NAMER.append(name)
    return _NAMER[0]


def _sky_at(curves, k):
    c = curves.get(SKY)
    return tod._at(c, k) if c and c["keys"] is not None else None


def _value(c, op, v, target, k, x, sky):
    """One key's new value."""
    if op == "add":
        return tuple(a + v for a in x)
    if c["cls"] == tod.VEC4:
        rgb, rest = x[:3], x[3:]
        if op == "mul":
            return tuple(a * v for a in rgb) + rest
        t = (1.0, 1.0, 1.0) if target == "white" else (sky[:3] if target == "sky" and sky else rgb) \
            if isinstance(target, str) else (float(target),) * 3
        return tuple(a + (b - a) * v for a, b in zip(rgb, t)) + rest
    if op == "mul":
        return tuple(a * v for a in x)
    t = float(target) if not isinstance(target, str) else x[0]
    return tuple(a + (t - a) * v for a in x)


def edits_of(settings):
    """settings["tod"] -> [(control, value, every key?)] for the controls that change something."""
    out = []
    for cid, s in (settings or {}).get("tod", {}).items():
        c = BY_ID.get(cid)
        if c is None:
            raise ValueError(f"no lighting control {cid!r}")
        v = float(s["v"] if isinstance(s, dict) else s)
        if abs(v - c["neutral"]) > 1e-6:
            out.append((c, v, bool(s.get("all", False)) if isinstance(s, dict) else False))
    return out


def edited_curves(body, settings):
    """-> (curves as parsed, {path: new values}, [problems]) for the table `body`."""
    curves = tod.parse(body)
    new, problems = {}, []
    for c, v, every in edits_of(settings):
        for path in c["paths"]:
            cur = curves.get(path)
            if cur is None or cur["keys"] is None:
                problems.append(f"{c['label']}: this map's table has no {namer()(path)}")
                continue
            if cur["cls"] not in (tod.FLOAT, tod.VEC4):
                problems.append(f"{c['label']}: {namer()(path)} isn't a number or a colour")
                continue
            vals = new.get(path, list(cur["vals"]))
            new[path] = [_value(cur, c["op"], v, c.get("target"), k, x, _sky_at(curves, k)) if (every or _is_day(k))
                         else x for k, x in zip(cur["keys"], vals)]
    return curves, new, problems


def edit_body(body, settings):
    """The table with the settings applied -> (new body, {path: (old values, new values)}). Every other entry
    byte-identical; the entry writer is checked to give the stock bytes back first."""
    curves, new, problems = edited_curves(body, settings)
    if problems:
        raise ValueError("; ".join(problems))
    out, changed = bytearray(body[:0x12]), {}
    for s, e, path in tod_snow.entries(body):
        seg = body[s:e]
        if path in new and [tuple(v) for v in new[path]] != [tuple(v) for v in curves[path]["vals"]]:
            c = curves[path]
            if _entry(seg, c, c["vals"]) != seg:
                raise ValueError(f"{namer()(path)}: the entry writer doesn't give the stock bytes back")
            seg = _entry(seg, c, new[path])
            changed[path] = (c["vals"], new[path])
        out += seg
    nb = bytes(out)
    if [p for _s, _e, p in tod_snow.entries(nb)] != [p for _s, _e, p in tod_snow.entries(body)]:
        raise ValueError("the table's curves changed order")
    return nb, changed


# --------------------------------------------------------------------------------------------- the World --

def world_values(archive):
    """-> dict(water_level, death_height, view_distance, loading_distance) from the World."""
    S = M.schema()
    C = M.Codec(S)
    for _rec, _name, t, body, _f in M.iter_objects_ex(archive):
        if S.name_of(t) != "World":
            continue
        f = C.decode(body).obj.fields
        g = f["GridLayout"].fields
        dh = f.get("WorldEngineData")
        return dict(water_level=struct.unpack("<f", f["WaterLevel"])[0],
                    death_height=struct.unpack("<f", dh.fields["DeathHeight"])[0] if dh is not None else None,
                    view_distance=g["DefaultViewingDistance"][0], loading_distance=g["DefaultLoadingDistance"][0],
                    **stand_ins_of(archive))
    raise ValueError(f"{os.path.basename(archive)} has no World")


ST_TAIL, ST_FIRST_AT, ST_SPANS, ST_NOWHERE = 14, 12, 16, 0x7FF0      # (as forgelib's witcher3/fakes.py)


def stand_ins_of(archive):
    """-> dict(stand_ins=number of stand-in tile meshes, stand_ins_on=how many still draw)."""
    S = M.schema()
    n = on = 0
    for _rec, _name, t, body, _f in M.iter_objects_ex(archive):
        if S.name_of(t) != "Mesh" or len(body) <= ST_TAIL:
            continue
        first, spans = struct.unpack_from("<HH", body, len(body) - ST_FIRST_AT)
        if spans == ST_SPANS:
            n += 1
            on += first != ST_NOWHERE
    return dict(stand_ins=n, stand_ins_on=on)


def _fakes():
    """forgelib's witcher3/fakes (hide_stand_ins), imported as _sp_convert is."""
    if "fakes" in sys.modules:
        return sys.modules["fakes"]
    saved = list(sys.path)
    sys.path[:0] = [os.path.join(FORGE, "witcher3"), os.path.join(FORGE, "maps"), FORGE]
    try:
        import fakes
    finally:
        sys.path[:] = saved
    return fakes


def _sp_convert():
    """forgelib's spmaps/sp_convert (set_water_level, set_view_distance), imported without leaving its sys.path
    changes behind (it puts an ac3-editor checkout first; AC3_EDITOR keeps this one in use)."""
    if "sp_convert" in sys.modules:
        return sys.modules["sp_convert"]
    saved, env = list(sys.path), os.environ.get("AC3_EDITOR")
    os.environ["AC3_EDITOR"] = REPO
    sys.path.insert(0, os.path.join(FORGE, "spmaps"))
    try:
        import sp_convert
    finally:
        sys.path[:] = saved
        if env is None:
            os.environ.pop("AC3_EDITOR", None)
        else:
            os.environ["AC3_EDITOR"] = env
    return sp_convert


# ------------------------------------------------------------------------------------------- the app's view --

def _fmt_val(v):
    return [round(float(x), 4) for x in v]


def curve_rows(curves, new=None):
    """Every curve for the read-only view: name, kind, keys (hours), values, and the three ambience hours' values
    (Night 0, Day 12, Evening 20) - before and, with `new`, after."""
    nm = namer()
    rows = []
    for path, c in curves.items():
        if c["keys"] is None:
            rows.append(dict(path="/".join(f"{x:08X}" for x in path), name=nm(path), kind="?", hours=[], vals=[]))
            continue
        kind = {tod.FLOAT: "number", tod.VEC4: "colour", tod.BYTE: "switch", tod.U32: "number (held)"}[c["cls"]]
        r = dict(path="/".join(f"{x:08X}" for x in path), name=nm(path), kind=kind,
                 hours=[round(k / HOUR, 2) for k in c["keys"]], vals=[_fmt_val(v) for v in c["vals"]],
                 at=[_fmt_val(tod._at(c, h * HOUR)) for _n, h in AMBIENCES])
        if new and path in new:
            c2 = dict(c, vals=new[path])
            r["after"] = [_fmt_val(v) for v in new[path]]
            r["at_after"] = [_fmt_val(tod._at(c2, h * HOUR)) for _n, h in AMBIENCES]
        rows.append(r)
    return rows


def read(archive):
    """The Map panel's data for a map."""
    path = os.path.abspath(archive)
    rec, name, body = main_tod(path)
    curves = tod.parse(body)
    others = [n for _r, n, b in find_tods(path) if n != name]
    ctl = []
    for c in CONTROLS:
        have = [p for p in c["paths"] if p in curves and curves[p]["keys"] is not None]
        ctl.append(dict({k: v for k, v in c.items() if k != "paths"}, paths=["/".join(f"{x:08X}" for x in p) for p in c["paths"]],
                        missing=len(c["paths"]) - len(have),
                        now=[_fmt_val(tod._at(curves[have[0]], h * HOUR)) for _n, h in AMBIENCES] if have else []))
    return dict(archive=path, tod=name, record=rec, n_curves=len(curves), others=others,
                ambiences=[dict(name=n, hour=h) for n, h in AMBIENCES], day=list(DAY),
                controls=ctl, presets=[dict(id=p["id"], label=p["label"], tip=p["tip"],
                                            tod={k: dict(v=v, all=a) for k, (v, a) in p["tod"].items()})
                                       for p in PRESETS],
                curves=curve_rows(curves), world=world_values(path))


def preview(archive, settings):
    """The curves as the settings would leave them -> dict(curves (rows with after-values), changed, problems)."""
    _rec, _name, body = main_tod(os.path.abspath(archive))
    curves, new, problems = edited_curves(body, settings)
    changed = sum(1 for p, v in new.items() if [tuple(x) for x in v] != [tuple(x) for x in curves[p]["vals"]])
    # day-keys-only controls whose curves have no day key on this map (Wolcott's Brightness: keyed at 0 / 24 only):
    # they change nothing unless set to every key
    untouched = [c["id"] for c, _v, every in edits_of(settings) if not every and not any(
        _is_day(k) for p in c["paths"] if p in curves and curves[p]["keys"] for k in curves[p]["keys"])]
    return dict(curves=curve_rows(curves, new), changed=changed, problems=problems, untouched=untouched)


# ------------------------------------------------------------------------------------------------ the build --

def wants(settings):
    """Does the request change anything here?"""
    s = settings or {}
    return bool(edits_of(s)) or s.get("water_level") is not None or s.get("view_distance") is not None \
        or s.get("stand_ins") is False


def check(archive, settings):
    """-> (errors, warnings) before building."""
    errors, warnings = [], []
    s = settings or {}
    try:
        _rec, _name, body = main_tod(archive)
        _c, _n, problems = edited_curves(body, s)
        errors += problems
    except ValueError as e:
        if edits_of(s):
            errors.append(str(e))
    w = world_values(archive)
    wl = s.get("water_level")
    if wl is not None:
        wl = float(wl)
        if not -1000 < wl < 1000:
            errors.append(f"kill height {wl:.1f} m: out of range")
        elif w["death_height"] is not None and wl < w["death_height"]:
            warnings.append(f"kill height {wl:.1f} m is under the World's DeathHeight ({w['death_height']:.0f} m)")
        if wl > w["water_level"] + 0.01:
            warnings.append(f"kill height raised {wl - w['water_level']:+.2f} m: anyone below {wl:.2f} m dies - "
                            "check no street, spawn or roof route is that low")
    vd = s.get("view_distance")
    if vd is not None and not 1 <= int(vd) <= 255:
        errors.append(f"view distance {vd}: it's one byte, 1 - 255 m")
    return errors, warnings


def apply(src, dest, settings, log=print):
    """The build pass: src -> dest with the lighting edits (one record rewritten, the rest byte-identical), then the
    World's WaterLevel and viewing distance set. -> report lines."""
    s = settings or {}
    errors, warns = check(src, s)
    if errors:
        raise RuntimeError("map settings: " + "; ".join(errors))
    lines = []
    edits = edits_of(s)
    if edits:
        rec0, name0, _b = main_tod(src)
        done = {}

        def fn(rec, o):
            if rec != rec0 or o["type"] != TOD_CLASS or (o.get("name") or "").strip() != name0.strip():
                return None
            nb, changed = edit_body(o["body"], s)
            done.update(changed)
            return nb
        _changed, same = A.rewrite(src, dest, fn, log=log)
        if not same:
            raise RuntimeError("map settings: the lighting edit changed more than its own record")
        lines.append(f"lighting: {name0}: {len(done)} curve(s) changed by {len(edits)} control(s) ("
                     + ", ".join(f"{c['label']} {c['fmt'] % v}{' every key' if a else ''}" for c, v, a in edits) + ")")
    else:
        shutil.copyfile(src, dest)
    w = world_values(src)
    spc = None
    if s.get("water_level") is not None and abs(float(s["water_level"]) - w["water_level"]) > 1e-4:
        spc = spc or _sp_convert()
        spc.set_water_level(dest, float(s["water_level"]), log=log)
        lines.append(f"kill height (World WaterLevel): {w['water_level']:.2f} -> {float(s['water_level']):.2f} m")
    if s.get("view_distance") is not None and int(s["view_distance"]) != w["view_distance"]:
        spc = spc or _sp_convert()
        spc.set_view_distance(dest, int(s["view_distance"]), log=log)
        lines.append(f"view distance (full detail): {w['view_distance']} -> {int(s['view_distance'])} m "
                     f"(loading distance {w['loading_distance']} m kept)")
    if s.get("stand_ins") is False and w["stand_ins_on"]:
        tmp = dest + ".standins"
        n = _fakes().hide_stand_ins(dest, tmp, log=log)
        os.replace(tmp, dest)
        lines.append(f"far view: {n} stand-in tile(s) switched off (cells past the view distance show sky)")
    for x in warns:
        lines.append("warning: " + x)
    return lines


def verify(src, dest, settings):
    """The read-back: every edited curve holds the values the settings give (to 1e-4), every other curve of the table
    and every other table as it was; the World's values as set. -> (ok, lines)."""
    s = settings or {}
    ok, lines = True, []
    if edits_of(s):
        _r, name0, b0 = main_tod(src)
        _r1, name1, b1 = main_tod(dest)
        want, _changed = edit_body(b0, s)
        got = tod.parse(b1)
        exp = tod.parse(want)
        bad = [p for p in exp if p not in got or got[p]["keys"] != exp[p]["keys"] or any(
            abs(a - b) > 1e-4 for va, vb in zip(got[p]["vals"] or [], exp[p]["vals"] or []) for a, b in zip(va, vb))]
        if bad or name1 != name0:
            ok = False
            lines.append(f"FAIL: lighting: {len(bad)} curve(s) of {name0} not as set: "
                         + ", ".join(namer()(p) for p in bad[:4]))
        else:
            n = sum(1 for p in exp if exp[p]["seg"] != tod.parse(b0)[p]["seg"])
            lines.append(f"ok: lighting: {name0} read back - {n} curve(s) as set, the other {len(exp) - n} as they were")
        others0 = {n: b for _r, n, b in find_tods(src) if n != name0}
        others1 = {n: b for _r, n, b in find_tods(dest) if n != name0}
        if others0 != others1:
            ok = False
            lines.append("FAIL: lighting: another time-of-day table changed")
    w0, w1 = world_values(src), world_values(dest)
    if s.get("water_level") is not None:
        want = float(s["water_level"])
        if abs(w1["water_level"] - want) > 1e-4:
            ok = False
            lines.append(f"FAIL: kill height reads {w1['water_level']:.2f} m, not {want:.2f}")
        else:
            lines.append(f"ok: kill height (World WaterLevel) {w1['water_level']:.2f} m (was {w0['water_level']:.2f})")
    elif abs(w1["water_level"] - w0["water_level"]) > 1e-4:
        ok = False
        lines.append("FAIL: the kill height changed")
    if s.get("view_distance") is not None:
        if w1["view_distance"] != int(s["view_distance"]) or w1["loading_distance"] != w0["loading_distance"]:
            ok = False
            lines.append(f"FAIL: view distance reads {w1['view_distance']} m (loading {w1['loading_distance']} m)")
        else:
            lines.append(f"ok: view distance {w1['view_distance']} m (was {w0['view_distance']}; loading "
                         f"{w1['loading_distance']} m as it was)")
    elif w1["view_distance"] != w0["view_distance"]:
        ok = False
        lines.append("FAIL: the view distance changed")
    if s.get("stand_ins") is False:
        if w1["stand_ins_on"]:
            ok = False
            lines.append(f"FAIL: {w1['stand_ins_on']} of {w1['stand_ins']} stand-in tile(s) still draw")
        else:
            lines.append(f"ok: far view: {w1['stand_ins']} stand-in tile(s) off (were {w0['stand_ins_on']} on)")
    elif w1["stand_ins_on"] != w0["stand_ins_on"]:
        ok = False
        lines.append("FAIL: the stand-in tiles changed")
    return ok, lines
