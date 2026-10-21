"""New looks and weapons: an edited part added as a NEW look row (its own equipment item, unlock
index, name and icon) and a weapon model added as a NEW weapon variant (its own inventory item and weapon item) -
next to the stock ones instead of replacing them. Built by charedit.build into a test DataPC + DataPC_extra.

A look (as forgelib's look_rows and costume_build.new_items): the kit's slot table (Main +107, Secondary +112, Head
+117, Belt +122, Legs +127; tables.py rows) gets a row - a copy of the row the part came from, its fresh uid, the
edited part's cell on the part's new copy (a new file `<part>_S<n>`: the edited part with fresh uids - clone.clone_payloads,
the kit's composite render targets keeping theirs, as stock's copies do). Its item `Char<n>_Bodypart_<Slot>_S<k>` is a
copy of the slot's item for that row (93 bytes: +10 icon root, +14 unlock index, the slot code 27 bytes from the end,
the row 23 from the end, two UIStrings - name, description), price 0, listed in the CharacterEquipment node.

A weapon variant (as forgelib's port.new_weapon_items): an inventory item (CharacterSkinInventoryItem: a copy of the
template item's, a new id <persona><family><variant> (persona.item_id) - the family's next variant -, the new model's
root; class, holders and kill persona the template's), listed in the roster's inventory list; its unlockable
`Char<n>_Weapon_<ff>_S<k>` a copy of the template's CharacterWeapon item (the inventory id its last u32), in the
CharacterWeapon node.

Icons (as forgelib's port_character.icon_texture_file / icon_in_datapc): a copy of the template item's `_MapDesc` icon file (a
TextureMapSpec + one DXT1 TextureMap) on fresh uids with a picture drawn here (the part lit, charmat; the weapon side
on), added to DataPC_extra AND DataPC (a match looks a loadout's icon up in DataPC).

Ids, for Studio test builds: unlock indices from STUDIO_UNLOCK (15000-15999: above the indices the mod's personas,
maps and abilities use, below the unlock table's ceiling of 16,382) and string ids from STUDIO_STRINGS (a spare
range), each checked free in the base; every new uid avoids the base's id census (both archives: every object's
identity and inline ids - forgelib's id_census.entries_of, reading its per-record store where it holds a record, never
writing it). A build meant for distribution reserves its unlock indices and string ids in the mod's own registries
instead.
"""
import json
import os
import struct
import sys
import time
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import charview as C  # noqa: E402

STUDIO_UNLOCK = (15000, 15999)
STUDIO_STRINGS = (0x5AEF0000, 0x5AEFFFFF)
SLOT_CODE = {"Main": 0, "Secondary": 1, "Head": 2, "Belt": 3, "Legs": 4}
SLOT_AT = {"Main": 107, "Secondary": 112, "Head": 117, "Belt": 122, "Legs": 127}
SLOT_FROM_END, ROW_FROM_END = 27, 23
PRICE_AT = 50
ROW_HEAD = 25


class NewError(ValueError):
    pass


def u32(b, at):
    return struct.unpack_from("<I", b, at)[0]


def set_u32(b, at, v):
    out = bytearray(b)
    struct.pack_into("<I", out, at, v)
    return bytes(out)


# ------------------------------------------------------------------------------------------------ the id census --

def _census_records(args):
    """(archive, [record indices]) -> set of ids, for a worker process."""
    path, idx, known = args
    C._forge()
    import id_census
    from datafile import decompress
    from forge import Forge
    out = set()
    with Forge(path) as f:
        recs = f.records
        for i in idx:
            r = recs[i]
            try:
                raw = f.read(r)
                if raw[4:8] != id_census.PAYLOAD_MAGIC:
                    continue
                for ident, _t, _d, _n, inl in id_census.entries_of(decompress(raw)):
                    out.add(ident)
                    out.update(x for x, _c in inl)
            except Exception:  # noqa: BLE001 - not an object payload
                continue
    return out


def census(log=print):
    """Every id the default set's DataPC and DataPC_extra use (sorted uint32), cached per set in the Studio's cache."""
    F, D, E = C._forge()
    path = os.path.join(C.CACHE, C._stamp(D), "ids.npy")
    if os.path.exists(path):
        return np.load(path)
    t0 = time.time()
    import id_census
    import record_cache
    from array import array
    from forge import Forge
    ids = set()
    store = record_cache.Store(id_census.STORE)           # (read only: never saved)
    todo = {}
    for path_a in (D, E):
        with Forge(path_a) as f:
            for r in f.records:
                ids.add(r.uid & 0xFFFFFFFF)
                raw = f.read(r)
                if raw[4:8] != id_census.PAYLOAD_MAGIC:
                    continue
                key = record_cache.digest(raw)
                if key in store:
                    for ident, _t, _d, _n, b in store.get(key):
                        ids.add(ident)
                        ids.update(array("I", b)[0::2])
                else:
                    todo.setdefault(path_a, []).append(r.index)
    del store
    n_todo = sum(len(v) for v in todo.values())
    log(f"id census: {len(ids)} ids from the store, {n_todo} record(s) to read")
    if n_todo:
        from concurrent.futures import ProcessPoolExecutor
        jobs = []
        for path_a, idx in todo.items():
            for k in range(0, len(idx), 200):
                jobs.append((path_a, idx[k:k + 200], None))
        with ProcessPoolExecutor(max_workers=max(1, min(6, (os.cpu_count() or 2) - 2))) as ex:
            for s in ex.map(_census_records, jobs):
                ids |= s
    arr = np.array(sorted(i & 0xFFFFFFFF for i in ids), np.uint32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.save(path, arr)
    log(f"id census: {len(arr)} ids in {time.time() - t0:.0f} s (cached)")
    return arr


def nested_index(archive, nested_ids):
    """clone.nested_index for the Studio: {value: {file names}} of every u32 in front of a nested class id in
    `archive` - from forgelib's per-record stores where they hold a record (read only), the rest read here; cached in
    the Studio's cache (clone's own version writes into forgelib's)."""
    import hashlib
    import pickle
    import record_cache
    from datafile import decompress
    from forge import Forge
    st = os.stat(archive)
    key = hashlib.sha1(repr((os.path.abspath(archive), st.st_size, int(st.st_mtime), sorted(nested_ids))).encode()).hexdigest()[:16]
    path = os.path.join(C.CACHE, "nested", f"nestedidx_{key}.pkl")
    if os.path.exists(path):
        return pickle.load(open(path, "rb"))
    stores = {c: record_cache.Store(f"nested_{c:08X}") for c in set(nested_ids)}
    index = {}
    with Forge(archive) as f:
        for r in f.records:
            raw = f.read(r)
            if raw[4:8] != bytes.fromhex("33aafb57"):
                continue
            k = record_cache.digest(raw)
            missing = [c for c, s_ in stores.items() if k not in s_]
            got = {c: s_.get(k) for c, s_ in stores.items() if k in s_}
            if missing:
                try:
                    d = decompress(raw)
                except Exception:  # noqa: BLE001
                    d = None
                for c in missing:
                    found = set()
                    if d is not None:
                        nb = struct.pack("<I", c)
                        j = d.find(nb, 4)
                        while j >= 0:
                            found.add(struct.unpack_from("<I", d, j - 4)[0])
                            j = d.find(nb, j + 1)
                    got[c] = tuple(sorted(found))
            for vals in got.values():
                for v in vals:
                    index.setdefault(v, set()).add(r.name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pickle.dump(index, open(path, "wb"))
    return index


class Fresh:
    """Fresh 32-bit uids: not in the census, not in the blobs at hand, not in the unmapped handle range, not taken."""

    def __init__(self, census_ids, seed, blobs=()):
        self.used = census_ids
        self.blobs = list(blobs)
        self.taken = set()
        self.x = (seed * 2654435761) & 0xFFFFFFFF or 1

    def _bad(self, u):
        if u < 0x10000 or 0xF0000000 <= u <= 0xF7FFFFFF or (u & 0xFFFF) == 0 or u in self.taken:
            return True
        i = np.searchsorted(self.used, u)
        if i < len(self.used) and self.used[i] == u:
            return True
        k = struct.pack("<I", u)
        return any(k in b for b in self.blobs)

    def take(self):
        while True:
            self.x = (self.x * 1103515245 + 12345) & 0xFFFFFFFF
            u = (self.x ^ (self.x >> 13)) & 0xFFFFFFFF
            if not self._bad(u):
                self.taken.add(u)
                return u


# ------------------------------------------------------------------------------------------------ the state --

class Build:
    """The edits of one build across the two archives: changed payloads (plain), new files, the Bootstrap, texts."""

    def __init__(self, pid, census_ids, boot_plain, log=print):
        F, self.D, self.E = C._forge()
        import exe_registry
        import look_rows
        self.pid, self.log = pid, log
        self.cl, _ = exe_registry.load(exe_registry.AC3)
        self.dv, self.ev = look_rows.BuildView(self.D), look_rows.BuildView(self.E)
        self.ids = census_ids
        self.boot = boot_plain
        self.fresh = Fresh(census_ids, pid * 7919 + int(time.time()) % 100000, [boot_plain])
        self.payloads = {}           # archive -> {record index: (name, plain)}
        self.files = {}              # archive -> [dict(name, plain, key, template)]
        self.texts = {}              # string id -> text
        self.report = []
        self.unlock_used = set()
        self.string_used = set()

    # -- small things
    def objs(self):
        import clone
        return clone.objects_of(self.boot)

    def kit(self):
        return next(o for o in self.objs() if o["name"] == f"CharacterBuildingKit_AC3MP_ID{self.pid}")

    def next_unlock(self):
        import persona
        used = {u32(o["body"], 14) for o in self.objs() if persona.is_item(o)} | self.unlock_used
        for i in range(STUDIO_UNLOCK[0], STUDIO_UNLOCK[1] + 1):
            if i not in used:
                self.unlock_used.add(i)
                return i
        raise NewError("the Studio's unlock range is full")

    def next_string(self):
        if not hasattr(self, "_loc"):
            import locpack
            self._loc = locpack.LocData(locpack.package_data("English", self.D)).strings()
        for i in range(STUDIO_STRINGS[0], STUDIO_STRINGS[1] + 1):
            if i not in self._loc and i not in self.string_used:
                self.string_used.add(i)
                return i
        raise NewError("the Studio's string range is full")

    def record(self, archive, name):
        from datafile import decompress
        from forge import Forge
        cur = self.payloads.get(archive, {})
        with Forge(archive) as f:
            rec = next((r for r in f.records if r.name == name), None)
            if rec is None:
                return None, None
            if rec.index in cur:
                return rec.index, cur[rec.index][1]
            return rec.index, decompress(f.read(rec))

    def set_record(self, archive, index, name, plain):
        import objects
        assert objects.directory_mismatches(plain) == [], name
        self.payloads.setdefault(archive, {})[index] = (name, plain)

    def name_free(self, archive, name):
        from forge import Forge
        with Forge(archive) as f:
            if any(r.name == name for r in f.records):
                return False
        return all(x["name"] != name for x in self.files.get(archive, []))

    def add_file(self, archive, name, plain, key, template):
        self.files.setdefault(archive, []).append(dict(name=name, plain=plain, key=key, template=template))

    def set_boot(self, reps=None, adds=None):
        import objects
        self.boot = objects.rebuild(self.boot, reps or {}, additions=adds or [])
        assert objects.directory_mismatches(self.boot) == []
        self.fresh.blobs[0] = self.boot

    def item_strings(self, body, name, desc):
        """The item's two UIStrings (name, description) on new string ids; the texts kept for the packages."""
        import persona
        sites = persona.ui_string_sites(body)
        if len(sites) < 2:
            raise NewError("the template item has no name / description UIStrings")
        ids = []
        for site, text in zip(sites[:2], (name, desc)):
            sid = self.next_string()
            body = set_u32(body, site + 8, sid)
            self.texts[sid] = text
            ids.append(sid)
        return body, ids

    def fresh_item(self, t_body):
        """A copy of an item body with a fresh own uid and fresh UIString uids."""
        import persona
        nb = set_u32(t_body, 2, self.fresh.take())
        for off in persona.ui_string_sites(nb):
            nb = set_u32(nb, off, self.fresh.take())
        return nb

    def node_add(self, node_name, uid):
        import persona
        objs = self.objs()
        pos, node = next((i, o) for i, o in enumerate(objs) if o["type"] == persona.WAREHOUSE and o["name"] == node_name)
        nb = persona.node_append(node["body"], uid)
        self.set_boot({pos: persona.obj(node, node["name"], nb)})


# ------------------------------------------------------------------------------------------------ icons --

def _icon_file(b, template_root, px_rgb, name):
    """A copy of the `_MapDesc` icon file whose root is `template_root` (DataPC_extra) with the picture `px_rgb`
    (size x size x 3 uint8) -> (plain, new root)."""
    import clone
    import dxt
    import objects
    import transplant
    from datafile import decompress
    from forge import Forge
    src = None
    with Forge(b.E) as f:
        for r in f.records:
            if r.name and r.name.endswith("_MapDesc"):
                raw = f.read(r)
                p = decompress(raw)
                if clone.objects_of(p)[0]["uid"] == template_root:
                    src = (r, p)
                    break
    if src is None:
        raise NewError(f"no icon file with root 0x{template_root:08X}")
    rec, plain = src
    objs = clone.objects_of(plain)
    spec, tex = objs[0], objs[1]
    hd = dxt.header(tex["body"])
    size = hd["width"]
    img = np.asarray(px_rgb, np.uint8)
    if img.shape[0] != size:
        from PIL import Image
        img = np.asarray(Image.fromarray(img).resize((size, size), Image.LANCZOS))
    chain = dxt.mip_chain_dxt1_fit(size, size, [tuple(int(v) for v in px) for px in img.reshape(-1, 3)], 1)
    if len(chain) != hd["size"]:
        raise NewError(f"icon {rec.name}: {hd['size']} bytes of picture, the encoder gave {len(chain)}")
    new_spec, new_tex = b.fresh.take(), b.fresh.take()

    def renew(body):
        return body.replace(struct.pack("<I", spec["uid"]), struct.pack("<I", new_spec)).replace(
            struct.pack("<I", tex["uid"]), struct.pack("<I", new_tex))
    tex_body = renew(tex["body"][:90] + chain + tex["body"][90 + hd["size"]:])
    new_plain = objects.rebuild(plain, {0: transplant.build_object(spec["type"], spec["body_size"], name, renew(spec["body"])),
                                        1: transplant.build_object(tex["type"], tex["body_size"], name[:-len("Desc")], tex_body)})
    assert objects.directory_mismatches(new_plain) == []
    return new_plain, new_spec, rec.index, size


def _template_icon_px(b, template_root):
    """The template icon's pixels (h, w, 4) - for its background colour."""
    import clone
    from datafile import decompress
    from forge import Forge
    with Forge(b.E) as f:
        for r in f.records:
            if r.name and r.name.endswith("_MapDesc"):
                p = decompress(f.read(r))
                o = clone.objects_of(p)
                if o[0]["uid"] == template_root:
                    return C._decode(o[1]["body"])
    return None


def _compose(picture_rgba, background, size):
    """The drawn thing (RGBA, transparent around) fitted on the template's background colour -> (size, size, 3)."""
    from PIL import Image
    a = np.asarray(picture_rgba)
    ys, xs = np.nonzero(a[:, :, 3] > 8)
    bg = np.zeros((size, size, 3), np.float32)
    if background is not None:
        corner = np.concatenate([background[:4, :4, :3].reshape(-1, 3), background[-4:, -4:, :3].reshape(-1, 3)])
        bg[:] = corner.mean(0)
    else:
        bg[:] = (40, 40, 40)
    if len(xs):
        crop = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, w = crop.shape[:2]
        k = 0.9 * size / max(h, w)
        im = Image.fromarray(crop).resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
        c = np.asarray(im).astype(np.float32)
        oy, ox = (size - c.shape[0]) // 2, (size - c.shape[1]) // 2
        al = c[:, :, 3:4] / 255.0
        region = bg[oy:oy + c.shape[0], ox:ox + c.shape[1]]
        bg[oy:oy + c.shape[0], ox:ox + c.shape[1]] = region * (1 - al) + c[:, :, :3] * al
    return np.clip(bg, 0, 255).astype(np.uint8)


def _weapon_picture(objs, size=256):
    """A weapon file's LOD0 drawn side on (its long axis across), lit, on transparent -> (size, size, 4) uint8."""
    from ac3editor import mapdata as M
    import make_jester
    from PIL import Image, ImageDraw
    Cd = M.Codec(M.schema())
    meshes = sorted((o for o in objs if o["type"] == make_jester.MESH), key=lambda o: o["name"])
    extra = []
    got = M.decode_mesh(Cd.decode(meshes[0]["body"]).obj.fields, extra)
    if isinstance(got, str):
        raise NewError(got)
    V, T = np.asarray(got[0], np.float64), np.asarray(got[1], np.int64)
    uv = np.asarray(extra[0], np.float64) if extra else np.zeros((len(V), 2))
    diff = next((o for o in objs if o["type"] == make_jester.TEXTURE and make_jester.role_of(o["name"]) == "Diffuse"), None)
    tex = C._decode(diff["body"]) if diff is not None else None
    ext = V.max(0) - V.min(0)
    long_ax = int(np.argmax(ext))
    side = [a for a in range(3) if a != long_ax]
    wide_ax = max(side, key=lambda a: ext[a])
    depth_ax = [a for a in side if a != wide_ax][0]
    X, Y, Z = V[:, long_ax], V[:, wide_ax], V[:, depth_ax]
    # diagonal, as the stock icons: the long axis from lower left to upper right
    ang = np.radians(45)
    sx = X * np.cos(ang) - Y * np.sin(ang)
    sy = X * np.sin(ang) + Y * np.cos(ang)
    lo, hi = np.array([sx.min(), sy.min()]), np.array([sx.max(), sy.max()])
    k = 0.92 * size / max(hi - lo)
    px = (sx - (lo[0] + hi[0]) / 2) * k + size / 2
    py = size / 2 - (sy - (lo[1] + hi[1]) / 2) * k
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    P3 = np.stack([X, Y, Z], 1)
    L = np.array([0.3, 0.5, 0.8]) / np.linalg.norm([0.3, 0.5, 0.8])
    order = np.argsort(Z[T].mean(1))
    for t in order:
        a, b_, c = T[t]
        n = np.cross(P3[b_] - P3[a], P3[c] - P3[a])
        ln = np.linalg.norm(n)
        sh = 0.45 + 0.55 * (abs(n @ L) / ln if ln > 0 else 0.5)
        if tex is not None:
            u, v = uv[[a, b_, c]].mean(0)
            h, w = tex.shape[:2]
            col = tex[int((v % 1) * h) % h, int((u % 1) * w) % w, :3].astype(np.float64)
        else:
            col = np.array([180, 180, 180.0])
        cc = tuple(int(x) for x in np.clip(col * sh * 1.15, 0, 255)) + (255,)
        d.polygon([(px[a], py[a]), (px[b_], py[b_]), (px[c], py[c])], fill=cc)
    return np.asarray(img)


def _part_picture(objs, part, size=256):
    """A part drawn lit (charmat.render on its own objects), its front, on transparent -> (h, w, 4) uint8."""
    import charmat
    from PIL import Image
    out = os.path.join(C.CACHE, "nl1_icon.png")
    charmat.render(part, None, -1.75, 0.1, size, size, out, objs=objs)
    return np.asarray(Image.open(out).convert("RGBA"))


# ------------------------------------------------------------------------------------------------ a new look --

def add_look(b, spec):
    """spec: dict(slot, row (the row it copies), part, mesh, materials, name, desc). -> report dict."""
    import clone
    import charmesh
    import objects
    import persona
    import tables
    import transplant
    slot, row, part = spec["slot"], int(spec.get("row", 0)), spec["part"]
    if slot not in SLOT_AT:
        raise NewError(f"slot {slot}: a new look goes into Main / Secondary / Head / Belt / Legs")
    pid = b.pid
    kit = b.kit()
    kb = kit["body"]
    # 1. the part's new copy: the edited part (its meshes / material values), every uid fresh but the kit's composite
    #    render targets (stock's copies of those keep their uid in every file a look loads)
    archive = b.D if part in b.dv.by_name else b.E if part in b.ev.by_name else None
    if archive is None:
        raise NewError(f"no file {part}")
    index, plain = b.record(archive, part)
    objs = [transplant.object_at(plain, o, s) for o, s, _ in objects.walk(plain)]
    _o, bodies, _rep = C.part_bodies(part, spec.get("mesh"), spec.get("materials"), objs, log=lambda *a: None)
    if bodies:
        repl = {k: transplant.build_object(o["type"], len(bodies[o["name"]]) - 1, o["name"], bodies[o["name"]])
                for k, o in enumerate(objs) if o["name"] in bodies}
        plain = objects.rebuild(plain, repl)
    n = 1
    while not b.name_free(archive, f"{part}_S{n}"):
        n += 1
    new_name = f"{part}_S{n}"
    targets = {v for v in persona.composite_targets(kb).values() if v}
    in_file = {o["uid"] for o in objs} & targets
    clone.pristine_definitions = lambda _archive, _uids: {}        # (no shared-kept logic: every object renewed)
    clone.FreshUids = lambda seed, known, blobs: b.fresh              # (the census-checked supply)
    clone.nested_index = nested_index                                 # (its cache in the Studio's)
    rename = lambda s: new_name + s[len(part):] if s.startswith(part) else s  # noqa: E731  (file, selector, LODs)
    mapping, _kept, copies = clone.clone_payloads({part: plain}, rename, archive, b.cl, 0, extra_avoid=[b.boot],
                                                  avoid_uids=(), object_names=rename,
                                                  external={u: u for u in in_file}, always=in_file)
    new_plain, root, n_objs, _n = copies[new_name]
    old_root = b.dv.by_name.get(part) or b.ev.by_name.get(part)
    old_key = next(k for k, r in (b.dv.by_key.items() if archive == b.D else b.ev.by_key.items()) if r is old_root)
    b.add_file(archive, new_name, new_plain, root, index)
    # 2. the slot's table: a copy of row `row`, its cell on the part's old root moved onto the copy
    tname, _rows = b.dv.table(u32(kb, SLOT_AT[slot]))
    tindex, tplain = b.record(b.D, tname)
    tobj = clone.objects_of(tplain)[0]
    body = bytearray(tobj["body"])
    rows = tables.rows_of(bytes(body))
    if not 0 <= row < len(rows):
        raise NewError(f"{tname} has no row {row}")
    s0, count_at, cells = rows[row]
    end0 = cells[-1][0] + tables.cell_size(body, cells[-1][0]) if cells else count_at + 4
    new_row = bytearray(body[s0:end0])
    struct.pack_into("<I", new_row, 0, b.fresh.take())
    moved = 0
    for cp, _col, _cls, ref in cells:
        if ref == old_key:
            struct.pack_into("<I", new_row, cp - s0 + 13, root)
            moved += 1
    if not moved:
        raise NewError(f"{tname} row {row} has no cell for {part}")
    # the new row goes where the stock rows end (before the empty trailer row, as stock: its rows, then the trailer)
    last_s, last_c, last_cells = rows[-1]
    trailer_empty = not last_cells
    at = last_s if trailer_empty else (last_cells[-1][0] + tables.cell_size(body, last_cells[-1][0]))
    new_index = len(rows) - 1 if trailer_empty else len(rows)
    body[at:at] = new_row
    first = rows[0][0]
    struct.pack_into("<I", body, first - 4, u32(body, first - 4) + 1)
    after = tables.rows_of(bytes(body))
    assert len(after) == len(rows) + 1 and [c[3] for c in after[new_index][2]].count(root) == moved
    tplain = objects.rebuild(tplain, {0: transplant.build_object(tobj["type"], len(body) - 1, tobj["name"], bytes(body))})
    b.set_record(b.D, tindex, tname, tplain)
    # 3. the item: a copy of the slot's item for that row
    items = [o for o in b.objs() if o["name"].startswith(f"Char{pid}_Bodypart_") and persona.is_item(o)
             and o["body"][len(o["body"]) - SLOT_FROM_END] == SLOT_CODE[slot]]
    tmpl = next((o for o in items if u32(o["body"], len(o["body"]) - ROW_FROM_END) == row), None) or \
        next((o for o in items if u32(o["body"], len(o["body"]) - ROW_FROM_END) == 0), None)
    if tmpl is None:
        raise NewError(f"persona {pid} has no {slot} item to copy")
    nb = b.fresh_item(tmpl["body"])
    unlock = b.next_unlock()
    nb = set_u32(nb, 14, unlock)
    nb = set_u32(nb, len(nb) - ROW_FROM_END, new_index)
    nb = nb[:PRICE_AT] + bytes(4) + nb[PRICE_AT + 4:]                  # (free, as the row-0 items)
    nb, sids = b.item_strings(nb, spec.get("name") or f"{slot} {new_index + 1}", spec.get("desc") or "Made in Reforged Studio.")
    # 4. its icon: the part drawn lit on the template icon's background
    t_icon = u32(tmpl["body"], 10)
    px = _compose(_part_picture(charmesh.patched_objects(objs, bodies) if bodies else objs, part),
                  _template_icon_px(b, t_icon), 128)
    icon_name = f"AC3MP_ID{pid}_{slot}_S{n}_MapDesc"
    iplain, iroot, itemplate, isize = _icon_file(b, t_icon, px, icon_name)
    b.add_file(b.E, icon_name, iplain, iroot, itemplate)
    b.add_file(b.D, icon_name, iplain, iroot, tindex)        # (DataPC too: a match looks the icon up there)
    nb = set_u32(nb, 10, iroot)
    item_name = f"Char{pid}_Bodypart_{tmpl['name'].split('_Bodypart_')[1].rsplit('_', 1)[0]}_S{n}"
    b.set_boot(adds=[persona.obj(tmpl, item_name, nb)])
    b.node_add("CharacterEquipment", u32(nb, 2))
    rep = dict(kind="look", slot=slot, row=new_index, copied_row=row, file=new_name, objects=n_objs, root=f"{root:08X}",
               table=tname, item=item_name, unlock=unlock, strings=[f"{s:08X}" for s in sids], icon=icon_name,
               icon_size=isize, name=b.texts[sids[0]])
    b.report.append(rep)
    b.log(f"new look: {slot} row {new_index} ({new_name}, {n_objs} objects), item {item_name}, unlock {unlock}, "
          f"icon {icon_name}")
    return rep


# ------------------------------------------------------------------------------------------------ a new weapon --

def add_weapon(b, spec):
    """spec: dict(template (an own inventory item id), model (a weapon file of its class), name, desc) -> report."""
    import charweapon
    import persona
    import roster
    pid = b.pid
    t_id, model = int(spec["template"]), spec["model"]
    objs = b.objs()
    inv = {o["name"]: o for o in objs if o["type"] == persona.INVENTORY}
    t_inv = inv.get(str(t_id))
    if t_inv is None:
        raise NewError(f"no inventory item {t_id}")
    f = persona.item_fields(t_inv["body"])
    if not f or f[2] != [pid]:
        raise NewError(f"item {t_id} is not persona {pid}'s own")
    cls = f[1]
    allit = charweapon._all_items(objs, b.dv, b.ev)
    same = [x for x in allit if x["file"] == model]
    if not same or all(x["cls"] != cls for x in same):
        raise NewError(f"{model} is not a WB{cls:02d} model")
    root = charweapon.root_of(model, b.dv, b.ev)
    # the new id: the template's family, its next variant
    suffix = t_id % 10000 if t_id >= 10000 * pid else t_id % 1000
    family = suffix // 100
    have = {int(n) for n in inv if n.isdigit()}
    new_id = None
    for v in range(1, 100):
        cand = persona.item_id(pid, family * 100 + v)
        if cand not in have:
            new_id = cand
            break
    if new_id is None or new_id >= persona.ITEM_ID_LIMIT:
        raise NewError(f"no free item id in family {family:02d}")
    ib = set_u32(t_inv["body"], 2, b.fresh.take())
    ib = set_u32(ib, 10, new_id)
    ib = set_u32(ib, len(ib) - 4, root)
    b.set_boot(adds=[persona.obj(t_inv, str(new_id), ib)])
    # the roster's inventory list, as its template
    objs = b.objs()
    pos, ro = next((i, o) for i, o in enumerate(objs) if o["type"] == roster.ROSTER_TYPE)
    header, lists = roster.parse(ro["body"])
    if t_inv["uid"] in lists[1]:
        lists[1].append(u32(ib, 2))
        body = roster.unparse(header, lists)
        roster.parse(body)
        b.set_boot({pos: roster.build(ro["name"], body)})
    # the unlockable: a copy of the template's CharacterWeapon item
    objs = b.objs()
    tw = next((o for o in objs if o["name"].startswith(f"Char{pid}_") and persona.is_item(o)
               and len(o["body"]) >= 4 and u32(o["body"], len(o["body"]) - 4) == t_id), None)
    if tw is None:
        raise NewError(f"no CharacterWeapon item of persona {pid} names {t_id}")
    nb = b.fresh_item(tw["body"])
    unlock = b.next_unlock()
    nb = set_u32(nb, 14, unlock)
    nb = set_u32(nb, len(nb) - 4, new_id)
    nb = nb[:PRICE_AT] + bytes(4) + nb[PRICE_AT + 4:]
    nb, sids = b.item_strings(nb, spec.get("name") or f"WEAPON {new_id}", spec.get("desc") or "Made in Reforged Studio.")
    t_icon = u32(tw["body"], 10)
    v = b.dv if model in b.dv.by_name else b.ev
    px = _compose(_weapon_picture(v.objects(model)), _template_icon_px(b, t_icon), 64)
    icon_name = f"ID{pid}_Studio_{new_id}_MapDesc"
    iplain, iroot, itemplate, isize = _icon_file(b, t_icon, px, icon_name)
    b.add_file(b.E, icon_name, iplain, iroot, itemplate)
    from forge import Forge
    with Forge(b.D) as fh:                                  # (DataPC's copy cloned from the model's records, else the kit's table's)
        recs = {r.name: r.index for r in fh.records}
    d_template = recs.get(model) or recs[b.dv.table(u32(b.kit()["body"], SLOT_AT["Main"]))[0]]
    b.add_file(b.D, icon_name, iplain, iroot, d_template)
    nb = set_u32(nb, 10, iroot)
    base = tw["name"].rsplit("_", 1)[0]
    k = 1
    while any(o["name"] == f"{base}_S{k}" for o in objs):
        k += 1
    item_name = f"{base}_S{k}"
    b.set_boot(adds=[persona.obj(tw, item_name, nb)])
    b.node_add("CharacterWeapon", u32(nb, 2))
    rep = dict(kind="weapon", inventory=new_id, cls=cls, model=model, item=item_name, unlock=unlock,
               strings=[f"{s:08X}" for s in sids], icon=icon_name, icon_size=isize, name=b.texts[sids[0]])
    b.report.append(rep)
    b.log(f"new weapon: {item_name} -> inventory {new_id} (WB{cls:02d}, {model}), unlock {unlock}, icon {icon_name}")
    return rep


# ------------------------------------------------------------------------------------------------ texts, the check --

def add_texts(b):
    """The new names and descriptions into every LocalizationPackage of DataPC (the same text in each language)."""
    if not b.texts:
        return []
    import add_strings
    import locpack
    import objects
    import transplant
    done = []
    for r, _raw, d, t, name, body, loc in add_strings.packages(b.D):
        if not loc.blocks:
            continue
        have = loc.strings()
        clash = {i for i in b.texts if i in have and have[i] != b.texts[i]}
        if clash:
            raise NewError(f"{name}: {sorted(hex(i) for i in clash)} already hold other text")
        data = locpack.edit(loc, b.texts)
        new_body = locpack.with_data(body, data)
        plain = objects.rebuild(d, replacements={0: transplant.build_object(t, len(new_body) - 1, name, new_body)})
        loc2 = locpack.LocData(data)
        if any(loc2.lookup(i) != s for i, s in b.texts.items()) or sum(1 for i, s in have.items() if loc2.lookup(i) != s):
            raise NewError(f"{name}: the texts did not read back")
        b.set_record(b.D, r.index, name, plain)
        done.append(name)
    return done


def check(dest_dir, b):
    """Read the build back: every new file by name, the Bootstrap's new items and inventory, the texts. -> [problems]."""
    import clone
    import locpack
    import persona
    from bootstrap_build import Bootstrap
    from forge import Forge
    out = []
    for archive, files in b.files.items():
        dest = os.path.join(dest_dir, os.path.basename(archive))
        with Forge(dest) as f:
            names = {r.name for r in f.records}
        out += [f"{os.path.basename(archive)}: {x['name']} missing" for x in files if x["name"] not in names]
    boot = Bootstrap(os.path.join(dest_dir, "DataPC.forge"))
    by = {o["name"]: o for o in boot.objects}
    loc = locpack.LocData(locpack.package_data("English", os.path.join(dest_dir, "DataPC.forge")))
    for r in b.report:
        o = by.get(r["item"])
        if o is None:
            out.append(f"item {r['item']} missing")
            continue
        if u32(o["body"], 14) != r["unlock"]:
            out.append(f"item {r['item']}: unlock {u32(o['body'], 14)}")
        if r["kind"] == "weapon" and str(r["inventory"]) not in by:
            out.append(f"inventory {r['inventory']} missing")
        if loc.lookup(int(r["strings"][0], 16)) != r["name"]:
            out.append(f"item {r['item']}: its name does not read back")
    # (stock repeats some uids itself - camera / clip entries: only a new object's uid counts)
    import collections
    count = collections.Counter(o["uid"] for o in boot.objects)
    new_names = {r["item"] for r in b.report} | {str(r["inventory"]) for r in b.report if r["kind"] == "weapon"}
    dups = [o["name"] for o in boot.objects if o["name"] in new_names and count[o["uid"]] > 1]
    if dups:
        out.append(f"Bootstrap: new object(s) {dups} share a uid")
    return out
