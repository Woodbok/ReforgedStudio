"""Reforged Studio's character editing: a persona's colour schemes, meshes, materials, weapons and new looks -
previewed in the viewer, built into a test DataPC on the default set (forgelib's paths.DEFAULT_SET), which stays
read-only: the build is written under the workspace's characters/<name>/.

The colour table (kit +132, `AC3MP_ID<n>_Custom_color1`: one BuildTableExt object, its file's root; no two kits share
one - checked on every build). Layout (charview.colour_table):
* +10 u32 n columns; column k at 14 + 205 k: +98 the material's uid, +128 the property it sets (crc32 of the name:
  Custom_Color_1 the garment's tint through its mask's alpha; SpecularMainColor / Specular2ndColor a metal's colours),
  +164 the region (the column's key);
* then u32 m rows - the schemes the menu offers, then an empty trailer - each 25 bytes (its +21 u32 count) and count
  28-byte cells: +0 the region, +12 four f32 (R, G, B, 0).
The game's schemes give every cell of a row one colour; each cell is its own value, so a scheme can colour each
garment differently. An edit changes cells that exist (no cell added, no size change): the record is rebuilt with the
same objects and directory, compressed again (datafile.compress, first chunk = the directory, as every writer does),
and the archive is streamed out with that one payload changed (forgepatch: no 6 GB image in memory).

    edits = {"<scheme>": {"<region>": [r, g, b]}}       (scheme = index among the non-empty rows, as the viewer's)

    python studio/engine/charedit.py colours <id>                         the table, its columns and schemes
    python studio/engine/charedit.py build <id> '<edits json>' <name>     a test DataPC (the worker runs this)
    python studio/engine/charedit.py builds                               the builds made
"""
import json
import os
import shutil
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import charview as C  # noqa: E402  (puts the repo on sys.path)
from ac3editor.workspace import WORK  # noqa: E402  (the workspace: builds, caches)

OUT = os.path.join(WORK, "characters")
PROPS = {0xDF1F9C86: "colour", 0x199DF5E9: "metal colour", 0x58D772B9: "metal 2nd colour"}


def u32(b, at):
    return struct.unpack_from("<I", b, at)[0]


def cells(body):
    """A colour table body -> (columns [dict(col, material, prop, what, region)], schemes [dict(row, cells
    [dict(region, at (the R float's offset in the body), rgb)])]) - the non-empty rows, in order."""
    n = u32(body, 10)
    cols = []
    for k in range(n):
        c = 14 + 205 * k
        prop = u32(body, c + 128)
        cols.append(dict(col=k, material=u32(body, c + 98), prop=prop, what=PROPS.get(prop, f"0x{prop:08X}"),
                         region=u32(body, c + 164)))
    p = 14 + 205 * n
    m = u32(body, p)
    p += 4
    schemes = []
    for r in range(m):
        if p + 25 > len(body):
            break
        cnt = u32(body, p + 21)
        row = []
        for j in range(cnt):
            q = p + 25 + 28 * j
            row.append(dict(region=u32(body, q), at=q + 12, rgb=list(struct.unpack_from("<3f", body, q + 12))))
        if cnt:
            schemes.append(dict(row=r, cells=row))
        p += 25 + 28 * cnt
    return cols, schemes


def apply(body, edits):
    """-> (the body with `edits` written, [problems]). Values are clamped to 0..1 (the menu's own range)."""
    out = bytearray(body)
    _cols, schemes = cells(body)
    bad = []
    for sk, regs in (edits or {}).items():
        s = int(sk)
        if not 0 <= s < len(schemes):
            bad.append(f"scheme {s + 1}: the table has {len(schemes)}")
            continue
        have = {c["region"]: c for c in schemes[s]["cells"]}
        for rk, rgb in regs.items():
            c = have.get(int(rk))
            if c is None:
                bad.append(f"scheme {s + 1} has no cell for region {rk}")
                continue
            v = [min(1.0, max(0.0, float(x))) for x in list(rgb)[:3]]
            struct.pack_into("<3f", out, c["at"], *v)
    return bytes(out), bad


def _table(pid):
    """-> (DataPC, record name, table root uid, the kit names sharing it)."""
    F, D, _E = C._forge()
    from bootstrap_build import Bootstrap
    objs = Bootstrap(D).objects
    kits = {o["name"]: o for o in objs if o["name"].startswith("CharacterBuildingKit_AC3MP_ID")}
    kit = kits.get(f"CharacterBuildingKit_AC3MP_ID{pid}")
    if kit is None:
        raise ValueError(f"persona {pid} has no CharacterBuildingKit in {D}")
    root = u32(kit["body"], C.COLOUR_AT)
    sharing = [n for n, k in kits.items() if k is not kit and u32(k["body"], C.COLOUR_AT) == root]
    import look_rows
    dv = look_rows.BuildView(D)
    name = dv.name_of(root)
    dv.close()
    if not name:
        raise ValueError(f"persona {pid}: its colour table 0x{root:08X} is no file's root")
    return D, name, root, sharing


def _record(path, name, root):
    """-> (record index, plain payload, the table object's body offset in it, its body)."""
    import objects
    import transplant
    from datafile import decompress
    from forge import Forge
    with Forge(path) as f:
        rec = next(r for r in f.records if r.name == name)
        plain = decompress(f.read(rec))
    for off, size, _s in objects.walk(plain):
        o = transplant.object_at(plain, off, size)
        if o["uid"] == root:
            return rec.index, plain, o["body_at"], o["body"]
    raise ValueError(f"{name}: no object 0x{root:08X}")


def colours(pid):
    D, name, root, sharing = _table(pid)
    _i, _plain, _at, body = _record(D, name, root)
    cols, schemes = cells(body)
    return dict(id=pid, table=name, root=root, shared_with=sharing, columns=cols, schemes=schemes)


# ------------------------------------------------------------------------------------------------ a test build --

def _mesh_change(path, part_file, mesh, mats=None):
    """A part's edited meshes and material values -> (record index, new plain payload, {object name:
    body}, mesh report or None)."""
    import objects
    import transplant
    import charmesh
    from datafile import decompress
    from forge import Forge
    with Forge(path) as f:
        rec = next((r for r in f.records if r.name == part_file), None)
        if rec is None:
            raise ValueError(f"no file {part_file} in {os.path.basename(path)}")
        plain = decompress(f.read(rec))
    objs = [transplant.object_at(plain, off, size) for off, size, _s in objects.walk(plain)]
    _o, bodies, report = C.part_bodies(part_file, mesh, mats, objs, log=lambda *a: None)
    repl = {}
    for k, o in enumerate(objs):
        if o["name"] in bodies:
            repl[k] = transplant.build_object(o["type"], len(bodies[o["name"]]) - 1, o["name"], bodies[o["name"]])
    new_plain = objects.rebuild(plain, repl)
    if objects.directory_mismatches(new_plain):
        raise ValueError(f"{part_file}: the rebuilt record's directory doesn't match")
    return rec.index, new_plain, bodies, report


def build(pid, edits, name, log=print, meshes=None, weapons=None, materials=None, new_looks=None, new_weapons=None):
    """-> dict(dest, ...): the default set's DataPC (and DataPC_extra where an edited part lives) with the persona's
    edits, under the workspace's characters/<name>/. edits: colour edits (above); meshes: {part file: {"glb": path, "lods":
    "all" | "lod0"}} - each part's meshes rebuilt from its edited glTF (charmesh)."""
    import re
    from datafile import compress, decompress
    import objects
    import forgepatch
    import forge_write
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", name or ""):
        raise ValueError("a build name is letters, digits, _ and - only")
    meshes = meshes or {}
    weapons = weapons or {}
    materials = {p: v for p, v in (materials or {}).items() if v}
    new_looks, new_weapons = list(new_looks or []), list(new_weapons or [])
    if not edits and not meshes and not weapons and not materials and not new_looks and not new_weapons:
        raise ValueError("nothing changed: edit a colour, a material, a weapon or import a mesh first")
    t0 = time.time()
    dest_dir = os.path.join(OUT, name)
    if os.path.exists(dest_dir):
        raise ValueError(f"a character build named {name} exists already: a new build takes a new name")
    F, D, E = C._forge()
    changes = {}                     # archive -> {record index: (record name, new plain, check)}
    report = {}
    if edits:
        _D, tname, root, sharing = _table(pid)
        if sharing:
            raise ValueError(f"{tname} is also {', '.join(sharing)}'s colour table: an edit would change them too")
        index, plain, body_at, body = _record(D, tname, root)
        new_body, bad = apply(body, edits)
        if bad:
            raise ValueError("; ".join(bad))
        new_plain = plain[:body_at] + new_body + plain[body_at + len(body):]
        assert len(new_plain) == len(plain) and objects.directory_mismatches(new_plain) == []

        def check_colours(path, tname=tname, root=root, new_body=new_body):
            return [] if _record(path, tname, root)[3] == new_body else [f"{tname}: the colours did not come through"]
        changes.setdefault(D, {})[index] = (tname, new_plain, check_colours)
        report["colours"] = dict(table=tname, cells=sum(len(v) for v in edits.values()))
        log(f"{tname}: {report['colours']['cells']} colour cell(s) changed")
    if weapons:
        import charweapon
        index, new_plain, check_weapons, wrep = charweapon.change(pid, weapons, log=log)
        changes.setdefault(D, {})[index] = ("Game Bootstrap Settings", new_plain, check_weapons)
        report["weapons"] = wrep
    added = {}                       # archive -> [new file dicts]
    if new_looks or new_weapons:
        # new looks / weapon variants - the Bootstrap as the swaps above left it, every new uid off the census
        import charnew
        from bootstrap_build import Bootstrap
        boot = Bootstrap(D)
        boot_plain = changes.get(D, {}).get(boot.index, (None, boot.plain))[1]
        nb = charnew.Build(pid, charnew.census(log=log), boot_plain, log=log)
        for spec in new_looks:
            charnew.add_look(nb, spec)
        for spec in new_weapons:
            charnew.add_weapon(nb, spec)
        texts = charnew.add_texts(nb)
        log(f"texts: {len(nb.texts)} string(s) in {len(texts)} package(s)")
        prev_check = changes.get(D, {}).get(boot.index, (None, None, lambda _d: []))[2]
        changes.setdefault(D, {})[boot.index] = ("Game Bootstrap Settings", nb.boot, prev_check)
        for archive, recs in nb.payloads.items():
            for index, (rname, plain) in recs.items():
                if index in changes.get(archive, {}):
                    raise ValueError(f"{rname}: edited twice (a replace and a new look on one part?)")
                changes.setdefault(archive, {})[index] = (rname, plain, lambda _d: [])
        for archive, files in nb.files.items():
            added[archive] = files
            changes.setdefault(archive, {})
        report["new"] = nb.report
    wear = wearers() if meshes or materials else {}
    for part in sorted(set(meshes) | set(materials)):
        import charmesh
        spec = meshes.get(part)
        path, _objs = charmesh.part_objects(part)
        index, new_plain, bodies, rep = _mesh_change(path, part, spec, materials.get(part))

        def check_mesh(dest, part=part, bodies=bodies):
            import transplant
            from forge import Forge
            if not os.path.exists(dest):
                return []
            with Forge(dest) as f:
                rec = next((r for r in f.records if r.name == part), None)
                if rec is None:
                    return []
                pl = decompress(f.read(rec))
            got = {}
            for o, z, _s in objects.walk(pl):
                ob = transplant.object_at(pl, o, z)
                got[ob["name"]] = ob["body"]
            return [f"{part}: {n} did not come through" for n, b in bodies.items() if got.get(n) != b]
        changes.setdefault(path, {})[index] = (part, new_plain, check_mesh)
        others = sorted({w["persona"] for w in wear.get(part, [])} - {pid})
        if spec:
            report.setdefault("meshes", {})[part] = dict(rep, glb=spec["glb"], also_worn_by=others)
            log(f"{part}: {rep['vertices']} vertices, {rep['triangles']} triangles, {rep['sub_meshes']} sub-mesh(es), "
                f"LODs {', '.join(n.rsplit('_', 1)[-1] for n in rep['lods'])}" + (f"; also worn by {others}" if others else ""))
        if materials.get(part):
            n = sum(len(v) for v in materials[part].values())
            report.setdefault("materials", {})[part] = dict(values=n, also_worn_by=others)
            log(f"{part}: {n} material value(s)" + (f"; also worn by {others}" if others else ""))
    need = sum(os.path.getsize(a) for a in changes) + (10 << 30)
    os.makedirs(OUT, exist_ok=True)
    free = shutil.disk_usage(OUT).free
    if free < need:
        raise ValueError(f"{free / 2**30:.1f} GB free on the disk: this build takes "
                         f"{(need - (10 << 30)) / 2**30:.1f} GB (and 10 GB kept spare) - delete an older build first")
    os.makedirs(dest_dir)
    written, size = [], 0
    try:
        for archive, recs in changes.items():
            image = forgepatch.open_image(archive)
            for index, (rname, new_plain, _chk) in recs.items():
                payload = compress(new_plain, first_chunk=objects.directory_size(new_plain))
                assert decompress(payload) == new_plain
                forge_write.set_payload(image, index, payload)
            if added.get(archive):
                import clone
                from forge import Forge
                with Forge(archive) as fh:
                    taken = {r.uid for r in fh.records}
                rec_uids = clone.fresh_record_uids(taken, pid * 1009 + len(added[archive]))
                for nf in added[archive]:
                    payload = compress(nf["plain"], first_chunk=objects.directory_size(nf["plain"]))
                    assert decompress(payload) == nf["plain"]
                    forge_write.add_file(image, nf["name"], payload, next(rec_uids), template_index=nf["template"],
                                         entry_key=nf["key"])
                log(f"{os.path.basename(archive)}: {len(added[archive])} new file(s)")
            dest = os.path.join(dest_dir, os.path.basename(archive))
            log(f"writing {os.path.basename(archive)}")
            size += forgepatch.write(image, dest)
            del image
            log(f"checking {os.path.basename(archive)}: the edits read back, every other file the default set's")
            problems = verify(archive, dest, {r for r, _p, _c in recs.values()}, [x["name"] for x in added.get(archive, [])])
            for _r, _p, chk in recs.values():
                problems += chk(dest)
            if problems:
                raise ValueError("the build did not check out: " + "; ".join(problems[:6]))
            written.append(dest)
        if new_looks or new_weapons:
            problems = charnew.check(dest_dir, nb)
            if problems:
                raise ValueError("the new items did not check out: " + "; ".join(problems[:6]))
    except Exception:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    kinds = [k for k in ("colours", "meshes", "materials", "weapons", "new") if k in report]
    info = dict(name=name, persona=pid, kind="+".join(kinds), base=D, base_stamp=C._stamp(D), edits=edits or {},
                meshes=meshes, weapons=weapons, materials=materials, new_looks=new_looks, new_weapons=new_weapons,
                report=report, files=written, dest=written[0],
                size=size,
                seconds=round(time.time() - t0, 1), made=time.strftime("%Y-%m-%d %H:%M"),
                note=f"{', '.join(os.path.basename(w) for w in written)} only: the rest is the default set's")
    if edits:
        info["table"] = report["colours"]["table"]
    json.dump(info, open(os.path.join(dest_dir, "build.json"), "w", encoding="utf-8"), indent=1)
    log(f"-> {dest_dir} ({size / 2**30:.2f} GB) in {info['seconds']} s")
    return info


def verify(base, dest, changed, added=()):
    """The build against its base: the base's records in the same order (then the `added` files), every payload but
    the changed records' byte for byte the base's. -> [problems]."""
    from forge import Forge
    out = []
    with Forge(base) as a, Forge(dest) as b:
        ra, rb = a.records, b.records
        if [(r.index, r.name) for r in ra] != [(r.index, r.name) for r in rb[:len(ra)]]:
            return ["the records differ from the base's"]
        if [r.name for r in rb[len(ra):]] != list(added):
            return [f"the new files are {[r.name for r in rb[len(ra):]]}, not {list(added)}"]
        for x, y in zip(ra, rb):
            if x.name in changed:
                continue
            if x.size != y.size or a.read(x) != b.read(y):
                out.append(f"{x.name} differs from the base's")
                if len(out) > 5:
                    break
    return out


def wearers():
    """{part file: [dict(persona, slot, row)]} over every kit's part tables (cached per default set): who wears it."""
    F, D, E = C._forge()
    path = os.path.join(C.CACHE, C._stamp(D), "wearers.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    import exe_registry
    import look_rows
    from bootstrap_build import Bootstrap
    cl, _ = exe_registry.load(exe_registry.AC3)
    dv, ev = look_rows.BuildView(D), look_rows.BuildView(E)
    out = {}
    for o in Bootstrap(D).objects:
        if not o["name"].startswith("CharacterBuildingKit_AC3MP_ID"):
            continue
        pid = int(o["name"].rsplit("ID", 1)[1])
        for off, slot in C.SLOTS:
            _n, trows = dv.table(u32(o["body"], off))
            for k, cells in trows or []:
                for _c, cls, ref in cells:
                    if ref and cl.get(cls) == "GraphicObject":
                        n = dv.name_of(ref) or ev.name_of(ref)
                        if n:
                            out.setdefault(n, []).append(dict(persona=pid, slot=slot, row=k))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"))
    return out


def builds():
    """-> [build.json + exists] of the workspace's characters/*, newest first."""
    rows = []
    if os.path.isdir(OUT):
        for n in os.listdir(OUT):
            p = os.path.join(OUT, n, "build.json")
            if os.path.exists(p):
                try:
                    info = json.load(open(p, encoding="utf-8"))
                except ValueError:
                    continue
                info["exists"] = os.path.exists(info.get("dest", ""))
                rows.append(info)
    rows.sort(key=lambda r: r.get("made", ""), reverse=True)
    return rows


def delete(name):
    d = os.path.join(OUT, name)
    if not os.path.exists(d):
        return dict(deleted=None)
    if not os.path.exists(os.path.join(d, "build.json")):
        raise ValueError(f"{name} is not a character build")
    shutil.rmtree(d)
    return dict(deleted=name)


def run_child(args, log=print):
    """Run one command in a child process (its reads hold a few GB; the worker stays small), its log lines passed on
    as they come -> its JSON result (the last stdout line)."""
    import subprocess
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), *args, "--json"], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    import threading
    out = []
    reader = threading.Thread(target=lambda: out.extend(p.stdout.read().splitlines()), daemon=True)
    reader.start()
    last = ""
    for line in p.stderr:
        line = line.rstrip()
        if line:
            log(line)
            last = line
    p.wait()
    reader.join()
    lines = [ln for ln in out if ln.strip()]
    if p.returncode or not lines:
        raise RuntimeError(last or f"charedit {args[0]} failed")
    return json.loads(lines[-1])


def main(argv):
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    log = (lambda m: print(m, file=sys.stderr, flush=True)) if as_json else print
    cmd = argv[0] if argv else ""
    if cmd == "colours":
        res = colours(int(argv[1]))
    elif cmd == "build":
        res = build(int(argv[1]), json.loads(argv[2]), argv[3], log=log,
                    meshes=json.loads(argv[4]) if len(argv) > 4 else None,
                    weapons=json.loads(argv[5]) if len(argv) > 5 else None,
                    materials=json.loads(argv[6]) if len(argv) > 6 else None,
                    new_looks=json.loads(argv[7]) if len(argv) > 7 else None,
                    new_weapons=json.loads(argv[8]) if len(argv) > 8 else None)
    elif cmd == "weapons":
        import charweapon
        res = charweapon.items(int(argv[1]))
    elif cmd == "materials":
        import charmat
        res = dict(part=argv[1], materials=charmat.materials(argv[1]))
    elif cmd == "matpreview":
        import charmat
        a = json.loads(argv[1])
        objs = None
        if a.get("mesh"):
            import charmesh
            objs, bodies, _r = C.part_bodies(a["part"], a["mesh"], None, log=lambda *x: None)
            objs = charmesh.patched_objects(objs, bodies)
        out = os.path.join(C.CACHE, "matpreview", f"{a['part']}_{int(time.time() * 1000) % 100000}.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        for old in os.listdir(os.path.dirname(out)):          # (keep the folder small)
            try:
                if time.time() - os.path.getmtime(os.path.join(os.path.dirname(out), old)) > 300:
                    os.remove(os.path.join(os.path.dirname(out), old))
            except OSError:
                pass
        res = charmat.render(a["part"], a.get("edits"), float(a.get("yaw", -1.9)), float(a.get("pitch", 0.15)),
                             int(a.get("width", 360)), int(a.get("height", 520)), out, objs=objs,
                             highlight=a.get("highlight"))
    elif cmd == "wearers":
        res = wearers()
    elif cmd == "export":
        import charmesh
        res = charmesh.export(int(argv[1]), argv[2], log=log)
    elif cmd == "builds":
        res = builds()
    elif cmd == "delete":
        res = delete(argv[1])
    else:
        print(__doc__)
        return 0
    if as_json:
        print(json.dumps(res))
    else:
        print(json.dumps(res, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
