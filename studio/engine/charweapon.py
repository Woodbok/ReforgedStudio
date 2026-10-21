"""Reforged Studio's character weapons: a persona's weapon drawn with another model of the same class - previewed in
the viewer, built into a test DataPC on the default set (charedit.build).

A persona's weapons are inventory items (persona.INVENTORY, in `Game Bootstrap Settings`): +10 the item id, +18 its
class (WBnn: the kills, the cameras and the holster row - the kit's weapon add-on table, +137, row class - 1 - go by
it), +22 n and n persona ids that may hold it, then the kill persona (whose kill tree plays with it), a 00 byte, and the
weapon file's root (an Entity: LODSelector, Mesh LODs, Material, maps) - persona.item_fields. The kit's +172 is the
default weapon's id; a player's loadout picks among the persona's items of each class (their variants).

A swap points one of the persona's OWN items (its holder list is the persona alone) at another weapon file whose items
are of the same class: the item id, its class, holders and kill persona stay - the profile, the kills, the holster are
untouched; only the model changes. The game shares weapon files between items itself (ACFE_Weapon_990301: 25 items).
Not changed: the customisation menu's weapon icon (the old weapon's picture).

    items(pid) -> dict(default, items [own items + the shared ones it may hold], candidates {class: [model files]})
    edits = {"<item id>": "<weapon file>"}
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import charview as C  # noqa: E402

ENTITY = 0x0984415E
CACHE_V = 1


def u32(b, at):
    return struct.unpack_from("<I", b, at)[0]


class WeaponError(ValueError):
    pass


def _all_items(objs, dv, ev):
    """[dict(id, cls, who, killer, file, root, pos)] of every inventory item of the Bootstrap objects."""
    import persona
    out = []
    for i, o in enumerate(objs):
        if o["type"] != persona.INVENTORY:
            continue
        f = persona.item_fields(o["body"])
        if not f:
            continue
        iid, cls, who, killer = f
        root = u32(o["body"], len(o["body"]) - 4)
        out.append(dict(id=iid, cls=cls, who=who, killer=killer, root=root, pos=i,
                        file=dv.name_of(root) or ev.name_of(root) or ""))
    return out


def items(pid):
    """-> dict(default (kit +172), items [the persona's: own (editable) and shared], candidates {class: [dict(file,
    items, holders)]}) - cached per default set."""
    F, D, E = C._forge()
    path = os.path.join(C.CACHE, C._stamp(D), str(pid), f"weapons_v{CACHE_V}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    import look_rows
    from bootstrap_build import Bootstrap
    dv, ev = look_rows.BuildView(D), look_rows.BuildView(E)
    objs = Bootstrap(D).objects
    kit = next((o for o in objs if o["name"] == f"CharacterBuildingKit_AC3MP_ID{pid}"), None)
    if kit is None:
        raise WeaponError(f"persona {pid} has no kit")
    allit = _all_items(objs, dv, ev)
    mine = []
    for it in allit:
        if pid in it["who"]:
            own = it["who"] == [pid]
            mine.append(dict(id=it["id"], cls=it["cls"], file=it["file"], killer=it["killer"], own=own,
                             holders=len(it["who"])))
    mine.sort(key=lambda r: (not r["own"], r["cls"], r["id"]))
    classes = {r["cls"] for r in mine}
    cands = {}
    for it in allit:
        if it["cls"] not in classes or not it["file"]:
            continue
        # a model: a file whose root is an Entity with a Mesh (a weapon you can see)
        c = cands.setdefault(str(it["cls"]), {}).setdefault(it["file"], dict(file=it["file"], items=[], holders=set()))
        c["items"].append(it["id"])
        c["holders"].update(it["who"])
    out_c = {}
    for cls, files in cands.items():
        rows = []
        for f, c in sorted(files.items()):
            objs_f = dv.objects(f) if f in dv.by_name else ev.objects(f)
            if not any(o["type"] == 0x415D9568 for o in objs_f):
                continue
            rows.append(dict(file=f, items=sorted(c["items"]), holders=sorted(c["holders"])))
        out_c[cls] = rows
    res = dict(id=pid, default=u32(kit["body"], C.WEAPON_AT), items=mine, candidates=out_c)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(res, open(path, "w", encoding="utf-8"), indent=1)
    return res


def root_of(file_name, dv, ev):
    """A weapon file's root uid (its index key), or None."""
    for v in (dv, ev):
        rec = v.by_name.get(file_name)
        if rec is None:
            continue
        for key, r in v.by_key.items():
            if r is rec:
                return key
    return None


def change(pid, edits, log=print):
    """-> (Bootstrap record index, new plain payload, check(dest) -> [problems], report) with each edited item's weapon
    root pointed at its new file. Same size: the u32 written in place."""
    import look_rows
    import objects
    from bootstrap_build import Bootstrap
    F, D, E = C._forge()
    dv, ev = look_rows.BuildView(D), look_rows.BuildView(E)
    boot = Bootstrap(D)
    allit = {it["id"]: it for it in _all_items(boot.objects, dv, ev)}
    plain = bytearray(boot.plain)
    report, want = [], {}
    for k, f in (edits or {}).items():
        iid = int(k)
        it = allit.get(iid)
        if it is None:
            raise WeaponError(f"no inventory item {iid}")
        if it["who"] != [pid]:
            raise WeaponError(f"item {iid} is held by {it['who'] or 'everyone'}: only the persona's own items change")
        same = [x for x in allit.values() if x["file"] == f]
        if not same:
            raise WeaponError(f"{f}: no inventory item draws it")
        if all(x["cls"] != it["cls"] for x in same):
            raise WeaponError(f"{f} is class {sorted({x['cls'] for x in same})}, item {iid} WB{it['cls']:02d}")
        root = root_of(f, dv, ev)
        if root is None:
            raise WeaponError(f"{f}: no root uid")
        o = boot.objects[it["pos"]]
        at = o["body_at"] + len(o["body"]) - 4
        assert u32(plain, at) == it["root"]
        struct.pack_into("<I", plain, at, root)
        want[iid] = root
        report.append(dict(item=iid, cls=it["cls"], old=it["file"], new=f))
        log(f"item {iid} (WB{it['cls']:02d}): {it['file']} -> {f}")
    plain = bytes(plain)
    assert len(plain) == len(boot.plain) and objects.directory_mismatches(plain) == []

    def check(dest):
        if not dest.endswith("DataPC.forge"):
            return []
        b2 = Bootstrap(dest)
        got = {it["id"]: it["root"] for it in _all_items(b2.objects, dv, ev)}
        return [f"item {i}: the new model did not come through" for i, r in want.items() if got.get(i) != r]
    return boot.index, plain, check, report


def main(argv):
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if argv and argv[0] == "items":
        res = items(int(argv[1]))
    else:
        print(__doc__)
        return 0
    print(json.dumps(res) if as_json else json.dumps(res, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
