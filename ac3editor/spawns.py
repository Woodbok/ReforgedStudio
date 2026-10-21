"""Spawn points: read, and added / removed / moved per game mode.

    python -m ac3editor.spawns <archive.forge>            # every mode's spawns

What a spawn is (checked on every map):

* an Entity in one or more game-mode blocks (`DataBlock_ACFE_<Mode>` records), the SAME uid and bytes in each, with a
  MultiSpawnPlayerComponent: SpawnType 0 = a RESPAWN (where players come back after dying), 2 = a match START (one per
  player; it also carries a Scene - the intro camera - with a PlayerActor the codec can't read, so starts stay raw);
  TeamIndex 0 = everyone, 1 / 2 the teams (AA, Domination), 3 / 4 Manhunt's. Respawns decode fully.
* which modes use it: each mode block is loaded by ONE data layer (the World's WorldDataLayerManager: layer -> block,
  1:1 on every map checked); in a block, an entity is created only if its data-layer filter names the block's layer -
  twice: the record directory's filter index (into the record's DataLayerFilterTable) and the filter embedded in the
  entity itself (with only the directory's filter right, the spawn is never created). Stock shares respawns
  between modes this way (Wolcott's SPW_Wanted_* are in seven blocks, each naming the layers of the modes using it).
* a block lists its world objects in its GridCellDataBlock (`Objects`: the first NumberOfObjectsToActivate are the
  world part; a new entity must go in there - elsewhere it crashes the game); nothing else references a spawn.

`apply` takes the wanted placement of each spawn in each mode it is used in (None = gone from that mode) and new
spawns copied from a template in chosen modes (any game-mode block - one without a filter table gets one in the form
of another block's). A spawn changed in only some of its modes is SPLIT: it keeps the modes where it stays put (its
layers trimmed), and a copy with fresh ids takes the others.
Match starts the same way, as raw bytes (their Scene's PlayerActor doesn't decode): `start_copy`
gives a copy fresh ids for everything nested in it (`start_inner_ids`: an "id followed by a registered class
id" scan - 22 on a Wolcott start: the entity, its components, the intro Scene's actors, clips and checkpoints), moves
both stored matrices and rewrites its own layer filter (`start_layers`); a block receiving a start gets the intro
camera kit its Scene names (`kit_of`: GraphRuleBook, ICameraRules, PathAnimationCameras, transitions, Animations - one
per block, the same ids on every map; Animations are known only by their directory serial) if it lacks it.
Rules the game imposes, checked by `problems`: a mode keeps at least one respawn; every team of a mode
(everyone, in the free-for-all modes) keeps a match start - none crashes the game at match start (0x580BC6 /
0x580D4C) -, a count other than the map had warns (stock: 8, or 4 per team); starts on distinct spots (two of one
team on one spot: their intro cameras fight); Artifact
Assault respawns only count inside their team's territory (each needs one; < 4 warns); a spawn outside its mode's
out-of-bounds area kills on arrival.
"""
import collections
import copy
import os
import re
import struct
import sys

import numpy as np

from . import collision, duplicate
from . import mapdata as M
from datafile import compress, decompress  # noqa: E402  (forgelib)
from forge_write import ArchiveImage, set_payload, write  # noqa: E402  (forgelib)
from transplant import object_at  # noqa: E402  (forgelib)

RESPAWN, START = 0, 2
FILTER_CLASS = struct.pack("<I", 0xDB1D406E)     # DataLayerFilter
ACTION_CLASS = struct.pack("<I", 0xE31593E1)     # DataLayerAction
TABLE_HEAD = b"\x00\x01"                         # a filter table is an object body without its 00 01 mark


def mode_label(record):
    label = record[len("DataBlock_"):] if record.startswith("DataBlock_") else record
    return label[len("ACFE_"):] if label.startswith("ACFE_") else label


def kind_label(kind):
    t, team = kind
    what = "start" if t == START else "respawn" if t == RESPAWN else f"type {t}"
    return what if team == 0 else f"{what}, team {team}"


def _mspc(body, mspc_class):
    """-> (SpawnType, TeamIndex) from the MultiSpawnPlayerComponent's bytes (u16 flags, uid, class, Active, u32
    SpawnType, TeamIndex), or None."""
    i = body.find(mspc_class, 74)
    while i >= 0:
        s = i - 6
        if s >= 74 and body[s + 10] == 1 and len(body) > s + 16:
            return struct.unpack_from("<I", body, s + 11)[0], body[s + 15]
        i = body.find(mspc_class, i + 1)
    return None


def embedded_layers(body):
    """The entity's own DataLayerFilter: (offset of its action count, [layer uid bytes]) - the LAST filter in the
    body whose actions all parse (a start's Scene comes first)."""
    found = None
    for mt in re.finditer(re.escape(FILTER_CLASS), body):
        p = mt.end()
        if p + 4 > len(body):
            continue
        n = struct.unpack_from("<I", body, p)[0]
        if not 0 < n < 64 or p + 4 + n * 17 > len(body):
            continue
        q, layers = p + 4, []
        for _k in range(n):
            if body[q:q + 4] != bytes(4) or body[q + 4:q + 8] != ACTION_CLASS or body[q + 8] != 0:
                break
            layers.append(body[q + 9:q + 13])
            q += 17
        else:
            found = (p, layers)
    return found


def decode_table(C, table):
    return C.decode(TABLE_HEAD + table) if table else None


def table_sets(C, table):
    """-> [[layer uid bytes]] per filter of a record's DataLayerFilterTable."""
    root = decode_table(C, table)
    if root is None:
        return []
    return [[a.fields["Layer"].id for a in M._obj(f).fields["LayerActions"]] if M._obj(f) is not None
            else [a.fields["Layer"].id for a in f.fields["LayerActions"]] for f in root.obj.fields["Filters"]]


class Spawn:
    def __init__(self, uid, name, record, matrix, kind, layers, decodable):
        self.uid, self.name, self.matrix, self.kind, self.layers = uid, name, matrix, kind, list(layers)
        self.records = {record: name}       # block -> its name there
        self.decodable = decodable

    def modes(self, info):
        """The blocks where it is created: it is in the block AND names the block's layer."""
        return [r for r in self.records if info["modes"].get(r, {}).get("layer") in self.layers]

    def __repr__(self):
        return f"<Spawn {self.name} {self.uid.hex()} {kind_label(self.kind)} in {sorted(self.records)}>"


def read(archive):
    """-> dict(spawns={uid: Spawn}, modes={record: dict(layer, label, block)}, archive)."""
    S = M.schema()
    C = M.Codec(S)
    cls = {S.name_of(h): h for h in S.types_by_hash}
    mspc = struct.pack("<I", cls["MultiSpawnPlayerComponent"])
    assoc, block_rec, spawns = {}, {}, {}           # block uid -> layer; record -> (block uid, its layer)
    for rec, name, t, body, _filt in M.iter_objects_ex(archive):
        if t == cls["WorldDataLayerManager"]:
            for x in C.decode(body).obj.fields["LayersConfig"]:
                a = M._obj(x).fields
                assoc[a["DataBlock"].id] = a["Layer"].id
            continue
        if not rec.startswith("DataBlock"):
            continue
        if t == cls["GridCellDataBlock"]:
            if rec not in block_rec:
                block_rec[rec] = (body[2:6], C.decode(body).obj.fields["OwnerRelatedIndex"])
            continue
        if t != cls["Entity"] or body[:2] != M.UID_MARK or mspc not in body:
            continue
        kind = _mspc(body, mspc)
        emb = embedded_layers(body)
        if kind is None or emb is None:
            continue
        uid = body[2:6]
        if uid in spawns:
            spawns[uid].records[rec] = name
            continue
        try:
            C.decode(body)
            ok = True
        except M.DecodeError:
            ok = False
        m = np.frombuffer(body[10:74], "<f4").astype(np.float64).reshape(4, 4)
        spawns[uid] = Spawn(uid, name, rec, m, kind, emb[1], ok)
    modes = {}
    for rec, (buid, own) in block_rec.items():
        # the layer an entity's filters must name to be created in this block: the block head's own
        # (OwnerRelatedIndex). The World's WorldDataLayerManager pairs the stock blocks with that same layer; AC3's
        # Switched AA block is paired with a layer of its own but its head
        # and every filter in it name the AA layer
        layer = own if own != bytes(4) else assoc.get(buid)
        if layer is not None:
            modes[rec] = dict(layer=layer, label=mode_label(rec), block=buid, loaded_by=assoc.get(buid))
    return dict(spawns=spawns, modes=modes, archive=archive)


def active(info):
    """-> {mode record: {uid: Spawn}} - the spawns each mode creates."""
    out = collections.defaultdict(dict)
    for u, sp in info["spawns"].items():
        for r in sp.modes(info):
            out[r][u] = sp
    return out


def _key(m):
    return tuple(np.round(np.asarray(m, dtype=np.float64).reshape(-1), 3))


def level(m):
    """A spawn's placement made upright: its facing (local +y) kept, turned about z only (as every stock spawn)."""
    m = np.asarray(m, dtype=np.float64).reshape(4, 4)
    f = np.arctan2(m[1, 1], m[1, 0])
    out = np.eye(4)
    out[0, :3] = (np.sin(f), -np.cos(f), 0.0)
    out[1, :3] = (np.cos(f), np.sin(f), 0.0)
    out[3, :3] = m[3, :3]
    return out


def plan(info, placed, added):
    """placed: {uid: {mode record: 4x4 or None (gone)}} - modes not named keep the spawn as it is; added: [(template
    uid, [mode records], 4x4)]. -> (changes, final) where changes = dict(move={uid: 4x4}, layers={uid: [layers]},
    drop={uid: [records it leaves]}, copies=[(template uid, [records], 4x4)]) and final = {mode: {key: (kind, 4x4)}}
    the spawns each mode creates afterwards (key: uid or ('new', k)). Raises ValueError for what can't be done."""
    sp_all, modes = info["spawns"], info["modes"]
    move, layers, drop, copies = {}, {}, {}, []
    for uid, per in placed.items():
        sp = sp_all[uid]
        now = {r: sp.matrix for r in sp.modes(info)}
        for r in per:
            if r not in now:
                raise ValueError(f"{sp.name} isn't used in {mode_label(r)}")
        want = {r: (per[r] if r in per else now[r]) for r in now}
        if all(m is not None and np.allclose(m, sp.matrix, atol=1e-4) for m in want.values()):
            continue
        groups = collections.OrderedDict()
        for r, m in want.items():
            if m is not None:
                groups.setdefault(_key(m), [np.asarray(m, dtype=np.float64).reshape(4, 4), []])[1].append(r)
        gone = [r for r, m in want.items() if m is None]
        # match starts too: removed, split and copied as raw bytes (start_copy) - `problems` keeps every
        # team of every mode at least one (none crashes the game at match start)
        if not groups:                          # gone from every mode: the entity goes from every block holding it
            drop[uid] = list(sp.records)
            continue
        stay = _key(sp.matrix)
        keep = stay if stay in groups else max(groups, key=lambda k: len(groups[k][1]))
        m_keep, r_keep = groups.pop(keep)
        if keep != stay:
            move[uid] = m_keep
        leaving = gone + [r for _m, rs in groups.values() for r in rs]
        if leaving:
            # out of those blocks; a layer only goes when no block it stays in needs it (AC3's Switched AA block
            # shares the AA layer with CTF_2)
            drop[uid] = leaving
            kept = {modes[r]["layer"] for r in sp.records if r not in leaving and r in modes}
            out = {modes[r]["layer"] for r in leaving} - kept
            if out:
                layers[uid] = [x for x in sp.layers if x not in out]
        for m, rs in groups.values():
            copies.append((uid, rs, m))
    for tuid, rs, m in added:
        for r in rs:
            if r not in modes:
                raise ValueError(f"{r} isn't a game-mode block")
        copies.append((tuid, list(rs), np.asarray(m, dtype=np.float64).reshape(4, 4)))
    final = {}
    for r, here in active(info).items():
        final[r] = {u: (sp.kind, sp.matrix) for u, sp in here.items()}
    for uid, rs in drop.items():
        for r in rs:
            final.get(r, {}).pop(uid, None)
    for uid, m in move.items():
        for r in final:
            if uid in final[r]:
                final[r][uid] = (final[r][uid][0], m)
    for k, (tuid, rs, m) in enumerate(copies):
        for r in rs:
            final.setdefault(r, {})[("new", k)] = (sp_all[tuid].kind, m)
    return dict(move=move, layers=layers, drop=drop, copies=copies), final


def problems(info, final, areas=None, pairs=None, floor=None, changed=None):
    """-> (errors, warnings) for the spawns each mode will create (plan's `final`). areas: boundary.areas (out of
    bounds), pairs: territories.pairs (Artifact Assault), floor(x, y, top) -> ground height or None; `changed`: the
    keys of new / moved spawns (only they are checked against the ground and the boundary)."""
    errors, warnings = [], []
    before = active(info)
    changed = set(changed or ())
    for r, here in sorted(final.items()):
        lab = mode_label(r)
        n_before = sum(1 for sp in before.get(r, {}).values() if sp.kind[0] == RESPAWN)
        resp = [(k, m) for k, (kind, m) in here.items() if kind[0] == RESPAWN]
        if n_before and not resp:
            errors.append(f"{lab}: no respawn points left - players who die would have nowhere to come back")
        # match starts per team (stock: 8 in the free-for-all modes, 4 per team in the team modes): a team - or a
        # free-for-all mode - with none crashes the game at match start (0x580BC6 / 0x580D4C)
        st0 = collections.Counter(sp.kind[1] for sp in before.get(r, {}).values() if sp.kind[0] == START)
        st1 = collections.Counter(kind[1] for kind, _m in here.values() if kind[0] == START)
        for team in sorted(set(st0) | set(st1)):
            who = "everyone" if team == 0 else f"team {team}"
            if st0[team] and not st1[team]:
                errors.append(f"{lab}: no match start left for {who} - the game crashes at match start")
            elif st1[team] != st0[team]:
                warnings.append(f"{lab}: {st1[team]} match start(s) for {who} (it had {st0[team]}; stock maps have "
                                f"{8 if team == 0 else 4})")
        if _stacked([(kind, m) for kind, m in here.values()]) > _stacked(
                [(sp.kind, sp.matrix) for sp in before.get(r, {}).values()]):
            errors.append(f"{lab}: two match starts of one team on one spot (their intro cameras fight)")
        for a in (areas or {}).values():
            if a.get("kind") != "oob" or r not in a["records"]:
                continue
            from . import boundary
            for k, (kind, m) in here.items():
                if k not in changed:
                    continue
                ok, d = boundary.inside_area(m[3, :2], a["corners"], a["closed"], a["ref"])
                if not ok:
                    errors.append(f"{lab}: a {kind_label(kind)} at ({m[3, 0]:.1f}, {m[3, 1]:.1f}) is out of bounds "
                                  f"- players spawning there die at once")
                elif d < 3.0:
                    warnings.append(f"{lab}: a {kind_label(kind)} is only {d:.1f} m from the boundary")
        for pair in pairs or ():
            if r not in pair["records"]:
                continue
            from . import territories
            for zuid in pair["uids"]:
                polys = pair["zones"][zuid]["polygons"]
                n = sum(1 for _k, m in resp if territories.member(polys, m[3, :2]))
                n0 = sum(1 for sp in before.get(r, {}).values()
                         if sp.kind[0] == RESPAWN and territories.member(polys, sp.matrix[3, :2]))
                team = pair["zones"][zuid]["name"]
                if n == 0 and n0 > 0:
                    errors.append(f"{lab}: no respawn inside {team} - Artifact Assault respawns players only in "
                                  f"their own territory")
                elif n < 4 and n < n0:
                    warnings.append(f"{lab}: only {n} respawn(s) inside {team} (it had {n0})")
    if floor is not None:
        seen = set()
        for r, here in final.items():
            for k, (kind, m) in here.items():
                if k not in changed or (k, _key(m)) in seen:
                    continue
                seen.add((k, _key(m)))
                x, y, z = m[3, :3]
                g = floor(x, y, z + 1.0)
                if g is None or z - g > 1.0:
                    warnings.append(f"{mode_label(r)}: a {kind_label(kind)} at ({x:.1f}, {y:.1f}, {z:.1f}) has no "
                                    f"ground under it" + ("" if g is None else f" (the ground is {z - g:.1f} m down)"))
                elif g - z > 0.25:
                    warnings.append(f"{mode_label(r)}: a {kind_label(kind)} at ({x:.1f}, {y:.1f}) is "
                                    f"{g - z:.1f} m under the ground")
    return errors, warnings


def _stacked(items, tol=1.0):
    """Pairs of match starts of the same team closer than `tol` (stock Manhunt puts different teams' on one spot)."""
    st = [(kind[1], m[3, :3]) for kind, m in items if kind[0] == START]
    return sum(1 for i in range(len(st)) for j in range(i)
               if st[i][0] == st[j][0] and np.linalg.norm(st[i][1] - st[j][1]) < tol)


def _set_layers(C, body, layers):
    """A decodable spawn body with its embedded data-layer filter naming exactly `layers`."""
    root = C.decode(body)
    acts = root.obj.fields["DataLayerFilter"].fields["LayerActions"]
    proto = acts[0]
    new = []
    for lay in layers:
        a = copy.deepcopy(proto)
        a.fields["Layer"].id = lay
        new.append(a)
    root.obj.fields["DataLayerFilter"].fields["LayerActions"] = new
    return C.encode(root)


def start_layers(body, layers):
    """A raw spawn body (a match start: its Scene doesn't decode) with its own DataLayerFilter - the last one whose
    actions parse (embedded_layers) - naming exactly `layers`: u32 count, then per layer 17 bytes (u32 0,
    DataLayerAction, 00, layer, the stock 4-byte tail) - the stock form."""
    p, old = embedded_layers(body)
    tail = body[p + 4 + 13:p + 4 + 17] if old else bytes(4)
    acts = b"".join(bytes(4) + ACTION_CLASS + b"\x00" + lay + tail for lay in layers)
    out = body[:p] + struct.pack("<I", len(layers)) + acts + body[p + 4 + 17 * len(old):]
    if embedded_layers(out)[1] != list(layers):
        raise ValueError(f"{body[2:6].hex()}: its own layer filter didn't take")
    return out


def _set_layers_any(C, body, layers):
    try:
        return _set_layers(C, body, layers)
    except M.DecodeError:
        return start_layers(body, layers)


def start_inner_ids(body, S=None):
    """The ids of the objects nested in a raw entity body - its own uid, its components and their parts, a start's
    Scene, actors, clips, checkpoints (one start of Wolcott's: 22) -, in body order: every u32 >= 0x02000000 followed
    by a registered class id (>= 1 << 24 with its low 24 bits not 0, not 0x02000000: a MultiSpawnPlayerComponent's flags)
    and not itself a class id. One guard more: a hit naming the base class `Object` counts only within 16 of another
    hit's id (nested ids come in runs; Wolcott's starts have float data that reads as an id + `Object`)."""
    S = S or M.schema()
    classes = {h for h in S.types_by_hash if h >= 1 << 24 and h & 0xFFFFFF and h != 0x02000000}
    obj_hash = next((h for h in S.types_by_hash if S.name_of(h) == "Object"), None)
    hits = []
    for k in range(2, len(body) - 7):
        v, h = struct.unpack_from("<II", body, k)
        if h in classes and v >= 0x02000000 and v != 0xFFFFFFFF and v not in classes:
            hits.append((k, v, h))
    real = {v for _k, v, h in hits if h != obj_hash}
    for _k, v, h in hits:
        if h == obj_hash and any(abs(v - w) <= 16 for w in real):
            real.add(v)
    out = []
    for _k, v, _h in hits:
        b = struct.pack("<I", v)
        if v in real and b not in out:
            out.append(b)
    return out


def start_copy(body, fresh, m_old, m_new, layers, S=None):
    """A copy of a raw spawn body (a match start) with fresh ids for everything nested in it (start_inner_ids,
    replaced wherever they occur in it), placed at `m_new` (both stored copies: collision.raw_moved)
    and its own filter naming `layers`. -> (body, {old id: new id})."""
    mapping = {u: fresh.take() for u in start_inner_ids(body, S)}
    out = body
    for u, v in mapping.items():
        out = out.replace(u, v)
    if out[2:6] != mapping.get(body[2:6]):
        raise ValueError(f"{body[2:6].hex()}: the copy's own uid didn't change")
    out = collision.raw_moved(out, m_old, m_new)
    return start_layers(out, layers), mapping


def _filter_index(C, table_root, layers, proto=None):
    """Index of the filter naming exactly `layers` (as a set) in a decoded filter table, appended if missing (copied
    from its first filter, or `proto` for an empty table). -> (index, its layer order, table changed)."""
    fl = table_root.obj.fields["Filters"]
    for i, f in enumerate(fl):
        fo = M._obj(f) if M._obj(f) is not None else f
        have = [a.fields["Layer"].id for a in fo.fields["LayerActions"]]
        if set(have) == set(layers) and len(have) == len(layers):
            return i, have, False
    f = copy.deepcopy(fl[0] if fl else proto)
    fo = M._obj(f) if M._obj(f) is not None else f
    proto = fo.fields["LayerActions"][0]
    acts = []
    for lay in layers:
        a = copy.deepcopy(proto)
        a.fields["Layer"].id = lay
        acts.append(a)
    fo.fields["LayerActions"] = acts
    fl.append(f)
    return len(fl) - 1, list(layers), True


def _borrowed_table(C, payloads, recs):
    """An empty decoded DataLayerFilterTable in the form of the first game-mode block's of the map, and a filter of it
    to copy new ones from. -> (table root, prototype filter)."""
    for rec in recs:
        if not rec.name.startswith("DataBlock_ACFE_"):
            continue
        _d, (_e, table) = payloads[rec.index]
        root = decode_table(C, table)
        if root is not None and root.obj.fields["Filters"]:
            root = copy.deepcopy(root)
            proto = root.obj.fields["Filters"][0]
            root.obj.fields["Filters"] = []
            return root, proto
    raise ValueError("no game-mode block of this map has a filter table to copy")


def _strip_trailing_number(name):
    return re.sub(r"_\d+$", "", name)


def apply(src, dest, info, changes, final, log=print):
    """Write `changes` (plan's, with its `final` to read back against) into a copy of `src`. -> (ok, new uids in the order of changes['copies'])."""
    S = M.schema()
    C = M.Codec(S)
    cls = {S.name_of(h): h for h in S.types_by_hash}
    sp_all, modes = info["spawns"], info["modes"]
    image = ArchiveImage.read(src)
    with M.Forge(src) as f:
        raw = {r.index: f.read(r) for r in f.records}
        recs = [r for r in f.records if r.name != "GlobalMetaFile" and len(raw[r.index]) > 8]
    payloads = {}
    for rec in recs:
        d = decompress(M.mapwalk.split_prefix(raw[rec.index])[1])
        payloads[rec.index] = (d, M.mapwalk.walk(d))
    blob = b"".join(d for d, _w in payloads.values())

    # the template bodies (the same in every block - checked)
    bodies = {}
    for rec in recs:
        if not rec.name.startswith("DataBlock"):
            continue
        d, (entries, _t) = payloads[rec.index]
        for off, size, _s, _f in entries:
            o = object_at(d, off, size)
            u = o["body"][2:6]
            if o["type"] == cls["Entity"] and u in sp_all:
                if u in bodies and bodies[u] != o["body"]:
                    raise ValueError(f"{sp_all[u].name}: its blocks hold different bytes - not edited")
                bodies[u] = o["body"]
    # every edited spawn's new body
    new_body = {}
    for uid in set(changes["move"]) | set(changes["layers"]):
        sp = sp_all[uid]
        b = bodies[uid]
        if uid in changes["layers"]:
            b = _set_layers_any(C, b, changes["layers"][uid])
        if uid in changes["move"]:
            b = collision.raw_moved(b, sp.matrix, changes["move"][uid])
        new_body[uid] = b
    # the copies: fresh ids (nowhere in this map, nor in DataPC / DataPC_extra), the template's layers cut to the
    # modes they go to
    avoid = duplicate.used_ids([], [a for a in _id_archives() if os.path.exists(a)])
    made, adding = [], collections.defaultdict(list)       # record -> [(uid, name, body)]
    names_used = collections.Counter()
    kit_needs = collections.defaultdict(set)               # record -> templates of the starts copied into it
    starts_made = {}                                       # new start uid -> (template inner ids, template uid)
    for k, (tuid, rs, m) in enumerate(changes["copies"]):
        sp = sp_all[tuid]
        fresh = _Fresh(avoid, duplicate.seed(src, tuid, 1000 + k), blob)
        lay = [modes[r]["layer"] for r in rs]
        if sp.decodable:
            obj = C.decode(bodies[tuid]).obj
            new, nid = duplicate.make_copy(obj, m, _bv_of(obj), fresh)
            body = _set_layers(C, duplicate.encode_entity(C, new), lay)
        else:
            # a match start: raw bytes (its Scene's PlayerActor doesn't decode), fresh inner ids; the objects its
            # intro Scene names (the block's camera kit - the same ids in every block of every map) must be in each
            # block it goes to
            body, mp = start_copy(bodies[tuid], fresh, sp.matrix, m, lay, S)
            nid = body[2:6]
            starts_made[nid] = (list(mp), tuid)
            for r in rs:
                kit_needs[r].add(tuid)
        avoid = fresh.used
        base = _strip_trailing_number(sp.name)
        names_used[base] += 1
        name = f"{base}_ED{names_used[base]:03d}"
        for r in rs:
            adding[r].append((nid, name, body, lay))
        made.append(nid)
    kit_adds, kits = _kits(S, C, cls, payloads, recs, kit_needs, bodies)
    changes = dict(changes, starts_made={n: (old, kits[t]) for n, (old, t) in starts_made.items()})

    # rewrite each block touched
    touched_recs = set(adding)
    for uid in set(new_body) | set(changes["drop"]):
        touched_recs |= set(sp_all[uid].records)
    drop_in = collections.defaultdict(set)
    for uid, rs in changes["drop"].items():
        for r in rs:
            drop_in[r].add(uid)
    changed = set()
    for rec in recs:
        if rec.name not in touched_recs:
            continue
        d, (entries, table) = payloads[rec.index]
        troot = decode_table(C, table)
        table_changed = False
        out, last_entity = [], -1
        gone = drop_in.get(rec.name, set())
        for off, size, serial, filt in entries:
            o = object_at(d, off, size)
            body = o["body"]
            u = body[2:6] if body[:2] == M.UID_MARK else None
            filt = None if filt == M.mapwalk.NO_FILTER else filt
            if o["type"] == cls["Entity"] and u in gone:
                continue
            if o["type"] == cls["Entity"] and u in new_body:
                body = new_body[u]
                if u in changes["layers"]:
                    filt, order, tc = _filter_index(C, troot, changes["layers"][u])
                    table_changed |= tc
                    body = _set_layers_any(C, body, order)
            elif o["type"] == cls["GridCellDataBlock"] and (gone or adding.get(rec.name)):
                body = _relist(C, body, gone, [nid for nid, *_x in adding.get(rec.name, [])])
            elif u is not None and gone and any(g in body[6:] for g in gone):
                raise ValueError(f"{rec.name}: {o['name']} refers to a spawn being removed - not removed")
            if o["type"] == cls["Entity"]:
                last_entity = len(out)
            out.append((serial, M.mapwalk.object_bytes(o, body), filt))
        ins = []
        proto = None
        if adding.get(rec.name) and troot is None:
            # a block without a filter table (Wolcott's Hijack and Escort on some maps: the record's table slot holds
            # size 0): one in the form of another block's of this map, holding only the filters
            # the new spawns need
            if rec.name not in info["modes"] or not rec.name.startswith("DataBlock_ACFE_"):
                raise ValueError(f"{rec.name} isn't a multiplayer game-mode block")
            troot, proto = _borrowed_table(C, payloads, recs)
            table_changed = True
        for nid, name, body, lay in adding.get(rec.name, []):
            filt, order, tc = _filter_index(C, troot, lay, proto)
            table_changed |= tc
            body = _set_layers_any(C, body, order)
            ins.append((struct.unpack("<I", nid)[0], duplicate.entity_record_bytes(cls["Entity"], name, body), filt))
        out[last_entity + 1:last_entity + 1] = ins
        out.extend((serial, data, None) for serial, data in kit_adds.get(rec.name, []))
        new_table = C.encode(troot)[2:] if table_changed else table
        nd = M.mapwalk.rebuild(out, new_table)
        pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
        assert decompress(pl) == nd
        set_payload(image, rec.index, pl)
        changed.add(rec.index)
    write(dest, image)

    # read back: every mode creates exactly the planned spawns; the records' lists, filters and other bytes right
    ok = verify(src, dest, info, changes, final, made, changed, raw, log)
    return ok, made


# a block's intro-camera kit (1 GraphRuleBook, 2 ICameraRules, 4
# PathAnimationCameras, 2 transitions, 4 Animations - one per block, shared by its starts, the same ids everywhere)
KIT_CLASSES = ("GraphRuleBook", "ICameraRule", "PathAnimationCamera", "CutCameraTransition",
               "TemporalCameraTransition", "Animation", "CameraGraphRuleBook")


def kit_of(S, body, objects):
    """The kit objects a start's raw body names, followed through the kit (animations name nothing): ids of
    `objects` ({uid: (class name, body)} of the mode blocks) in KIT_CLASSES."""
    found, todo = [], [body]
    while todo:
        b = todo.pop()
        for i in range(6, len(b) - 3):
            u = b[i:i + 4]
            if u in objects and objects[u][0] in KIT_CLASSES and u not in found:
                found.append(u)
                if objects[u][0] != "Animation":
                    todo.append(objects[u][1])
    return found


def _kits(S, C, cls, payloads, recs, needs, bodies):
    """-> {record name: [(serial, object bytes)]}: the kit objects the starts copied into each record name and the
    record doesn't hold, copied (same ids, as every block's) from the block holding them - unfiltered and outside the
    block's list (the form that works in game for Escort, Chest Capture and Domination starts)."""
    if not needs:
        return {}, {}
    objects, where, held = {}, {}, collections.defaultdict(set)
    for rec in recs:
        if not rec.name.startswith("DataBlock"):
            continue
        d, (entries, _t) = payloads[rec.index]
        for off, size, serial, _f in entries:
            o = object_at(d, off, size)
            # an object's directory serial is its uid (62 of 62 in Wolcott's Training block) - Animations, whose bodies
            # carry none (hand-written serializer), are known only by it (the path cameras name them so)
            u = struct.pack("<I", serial)
            held[rec.name].add(u)
            if u not in objects:
                objects[u] = (S.name_of(o["type"]), o["body"])
                where[u] = (serial, M.mapwalk.object_bytes(o))
    out, kits = {}, {}
    for r, tuids in needs.items():
        want = []
        for t in sorted(tuids):
            if t not in kits:
                kits[t] = kit_of(S, bodies[t], objects)
            want += [u for u in kits[t] if u not in want]
        out[r] = [where[u] for u in want if u not in held[r]]
    return out, kits


class _Fresh(duplicate.FreshIds):
    """Fresh ids that also appear nowhere in the map's payloads (as an identity, a link or anything else)."""

    def __init__(self, used, seed, blob):
        super().__init__(used, seed)
        self.blob = blob

    def take(self):
        while True:
            b = super().take()
            if b not in self.blob:
                return b


def _bv_of(obj):
    b = obj.fields["BoundingVolume"].fields
    return struct.unpack("<3f", b["Min"]), struct.unpack("<3f", b["Max"])


def _id_archives():
    from . import install
    return [os.path.join(install.LIVE, a) for a in ("DataPC.forge", "DataPC_extra.forge")]


def _relist(C, gcdb_body, gone, new_ids):
    """GridCellDataBlock: `gone` uids out of its Objects (and the activated count), `new_ids` in at the end of the
    activated part."""
    body = gcdb_body
    if gone:
        root = C.decode(body)
        f = root.obj.fields
        n = struct.unpack("<I", f["NumberOfObjectsToActivate"])[0]
        keep, n2 = [], n
        for i, x in enumerate(f["Objects"]):
            if x.id in gone:
                n2 -= i < n
                continue
            keep.append(x)
        f["Objects"] = keep
        f["NumberOfObjectsToActivate"] = struct.pack("<I", n2)
        body = C.encode(root)
    if new_ids:
        body = duplicate.list_in_cell(C, body, new_ids)
    return body


def verify(src, dest, info, changes, final, made, changed, raw_src, log=print):
    """dest read back: other records byte-identical; in the changed blocks every object that isn't an edited spawn
    or the block list byte-identical, every spawn's directory filter = its embedded filter, the block list's world
    part = the block's entities; each mode creates the planned spawns at the planned places."""
    S = M.schema()
    C = M.Codec(S)
    cls = {S.name_of(h): h for h in S.types_by_hash}
    edited = set(changes["move"]) | set(changes["layers"]) | set(changes["drop"]) | set(made)
    starts_made = changes.get("starts_made", {})         # new start uid -> (its template's inner ids, its kit)
    kit_ids = {x for _o, kit in starts_made.values() for x in kit}
    fails = []
    with M.Forge(dest) as f:
        for r in f.records:
            if r.name == "GlobalMetaFile" or len(raw_src.get(r.index, b"")) <= 8:
                continue
            new = f.read(r)
            if r.index not in changed:
                if new != raw_src[r.index]:
                    fails.append(f"{r.name} changed")
                continue
            d0 = decompress(M.mapwalk.split_prefix(raw_src[r.index])[1])
            d1 = decompress(M.mapwalk.split_prefix(new)[1])
            e0, _t0 = M.mapwalk.walk(d0)
            e1, t1 = M.mapwalk.walk(d1)
            sets = table_sets(C, t1)
            old_objs = {}
            for off, size, serial, _fl in e0:
                o = object_at(d0, off, size)
                old_objs[(serial, o["name"])] = o["body"]
            listed, n_act, ents, held, new_starts = None, 0, [], set(), []
            for off, size, serial, filt in e1:
                o = object_at(d1, off, size)
                b = o["body"]
                u = b[2:6] if b[:2] == M.UID_MARK else None
                if o["type"] == cls["GridCellDataBlock"]:
                    gf = C.decode(b).obj.fields
                    listed = [x.id for x in gf["Objects"]]
                    n_act = struct.unpack("<I", gf["NumberOfObjectsToActivate"])[0]
                    continue
                held.add(struct.pack("<I", serial))     # directory serial = uid (Animations have no other)
                if o["type"] == cls["Entity"]:
                    ents.append(u)
                if u in edited:
                    emb = embedded_layers(b)
                    dl = None if filt == M.mapwalk.NO_FILTER else sets[filt]
                    if emb is None or dl is None or set(emb[1]) != set(dl):
                        fails.append(f"{r.name}: {o['name']}: directory filter != its own")
                    if u in starts_made:
                        old_ids, kit = starts_made[u]
                        if any(x in b for x in old_ids):
                            fails.append(f"{r.name}: {o['name']}: still names its template's inner ids")
                        new_starts.append((r.name, o["name"], kit))
                    try:
                        root = C.decode(b)
                    except M.DecodeError:
                        if u in starts_made or (u in info["spawns"] and not info["spawns"][u].decodable):
                            continue                    # a match start: raw (its Scene's PlayerActor)
                        raise
                    if C.encode(root) != b:
                        fails.append(f"{r.name}: {o['name']} doesn't re-encode")
                    continue
                if (serial, o["name"]) not in old_objs and struct.pack("<I", serial) in kit_ids:
                    continue                            # a kit object a new start brought (checked below)
                if old_objs.get((serial, o["name"])) != b:
                    fails.append(f"{r.name}: {o['name']} changed")
            for rn, nm, kit in new_starts:
                missing = [x.hex() for x in kit if x not in held]
                if missing:
                    fails.append(f"{rn}: {nm}: its intro camera kit isn't in the block ({missing[:3]})")
            if listed is not None:
                world = set(listed[:n_act])
                for u in ents:
                    if u in edited and u not in world:
                        fails.append(f"{r.name}: {u.hex()} not in the block's world part")
                for u in edited:
                    if u in listed and u not in ents:
                        fails.append(f"{r.name}: {u.hex()} listed but not there")
    after = read(dest)
    want = {r: {(made[k[1]] if isinstance(k, tuple) else k): v for k, v in here.items()} for r, here in final.items()}
    got = active(after)
    for r in set(want) | set(got):
        w = {u: (k, _key(m)) for u, (k, m) in want.get(r, {}).items()}
        g = {u: (sp.kind, _key(sp.matrix)) for u, sp in got.get(r, {}).items()}
        if w != g:
            fails.append(f"{mode_label(r)}: spawns {len(g)} (planned {len(w)}) - "
                         f"{sorted(x.hex() for x in set(w) ^ set(g))[:4]}")
    for nid in made:
        if nid not in after["spawns"]:
            fails.append(f"new spawn {nid.hex()} missing")
    for msg in fails[:20]:
        log(f"  READ-BACK: {msg}")
    log(f"{dest}: spawns - {len(changes['move'])} moved, {len(changes['drop'])} leaving modes, "
        f"{len(made)} new; {len(changed)} blocks rewritten; read back: {'yes' if not fails else 'NO'}")
    return not fails


def summary(info):
    lines = []
    for r, here in sorted(active(info).items(), key=lambda x: mode_label(x[0])):
        c = collections.Counter(kind_label(sp.kind) for sp in here.values())
        shared = sum(1 for sp in here.values() if len(sp.modes(info)) > 1)
        lines.append(f"{mode_label(r):28} " + ", ".join(f"{n} {k}" for k, n in sorted(c.items()))
                     + f"   ({shared} shared with other modes)")
    return lines


def main(argv):
    info = read(argv[0])
    for line in summary(info):
        print(line)
    print(f"{len(info['spawns'])} spawn entities, {sum(not s.decodable for s in info['spawns'].values())} raw "
          f"(match starts)")


if __name__ == "__main__":
    main(sys.argv[1:])
