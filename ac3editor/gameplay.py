"""Gameplay objects from AC3's stock maps placed on any map: hiding spots, benches, chase breakers, lifts, Artifact
Assault artifacts and scoring zones, Domination points - copied with fresh ids into the right cell or game-mode blocks,
moved, or taken away. An offline Python API (no Blender):

    from ac3editor import gameplay as G
    G.catalog("bench")                                  # stock templates of a kind: [Thing] (key "Boston_Mill:...")
    info = G.read(archive)                              # a map's own gameplay objects ({uid: Thing}) and its modes
    rep = G.build(src, dest,
                  add=[dict(template="Boston_Mill:AC3MP_MILL_Bench_01A", at=(x, y, z), yaw=90)],
                  move={uid_or_name: dict(at=(x, y, z), yaw=0)}, remove=[uid_or_name])
    rep["ok"], rep["errors"], rep["warnings"], rep["made"]

What a gameplay object is (from a census of the stock maps):

* kinds, by component: `hiding_spot` HayStackComponent (hay carts, flower / fur / cotton stacks, bales - an
  EntityGroup whose head carries it), `bench` RestObjectAttributeComponent (+ GameplayCoordinator + AIComponent: the
  blend bench; merged-collision members on almost every map), `chase_breaker` ChaseBreakerComponent (+ Trigger; a
  group of door leaves; its FX lives on a separate entity the breaker names - ElementForFx - copied / moved with it),
  `lift` ElevatorComponent; in game-mode blocks: `artifact` FlagComponent (team byte TeamOwner 0 / 1, OriginalPosition
  = its position), `scoring_zone` CTFScoreZoneComponent, `domination_point` ControlPointComponent (hudId 1-3 = A-C).
* an object and everything nested in it gets FRESH ids (duplicate.py's rule); every entity of it has a GameStateData
  (ResetData) whose DescBuffer names its components by uid (renewed too) and whose DataBuffer holds its matrix
  transposed (moved too - the stored state a round reset goes back to);
* what it names outside itself: the map's World (benches' coordinators) becomes the destination's World; another
  entity (a chase breaker's FX) is copied along as a COMPANION; resources (LODSelector, Mesh, Material, TextureSet,
  TextureMap, MeshShape, Skeleton, Animation, FX ...) are copied into the record the copy goes to, unless that record
  holds them already (stock cells keep their own copy of what they use) - keeping their ids when the
  destination and DataPC don't use them, else fresh ones; what neither map defines (DataPC's FX) stays shared;
* where a copy goes: a cell kind into the 32 m cell under it (world part of its GridCellDataBlock, as duplicate.py's
  copies); a mode kind (or any kind given `modes`) into each named game-mode block, its own DataLayerFilter and the
  record directory's filter naming exactly those blocks' layers (spawns.py's rule);
* hiding spots are listed in the World record's MapWorldInfo (PermanentMarkers, MarkerType 4: the map's hay icons) -
  a copy gets a marker, a moved one's marker follows, a removed one's goes;
* copies stand on their own rigid body (IsMerged 0, as collision.py's copies); a moved or removed MERGED member
  (a stock bench) has its cell's merged tree re-treed;
* removing sinks the object 150 m (SINK) (collision.edit_shapes' remove - every reference to it stays valid; a dropped
  merged member / navmesh link target crashes the game). Artifacts, scoring zones and Domination points
  can't be removed: the CTF manager holds exactly one artifact per team (a missing one: AC3MP+0x745A8F) and the HUD
  exactly three points.

Rules checked (`problems`), as the game applies them: Artifact Assault - one artifact per team per block, each
inside its own team's territory and the two in different territories (a team's objects claim the territory holding
them at match start), a scoring zone in its team's territory; Domination - three points, ids 1-3, each inside its own
zone (points claim the zone holding them; `zones="follow"` carries a moved point's zone along, boundary.rebuild);
new and moved objects inside their modes' out-of-bounds area and on the ground (warnings).
"""
import collections
import copy
import os
import pickle
import re
import struct
import types

import numpy as np

from . import mapdata as M  # first: puts forgelib and ac3codec on the path
from . import collision, duplicate, games, mopp, spawns
from .newmodel import _comp, _cell_of, morton
from ac3codec import Handle, Obj, Ptr, Ref, Root  # noqa: E402
from datafile import compress, decompress  # noqa: E402  (forgelib)
from forge_write import ArchiveImage, set_payload, write  # noqa: E402  (forgelib)
from transplant import object_at  # noqa: E402  (forgelib)

KINDS = collections.OrderedDict([
    ("hiding_spot", "HayStackComponent"),
    ("bench", "RestObjectAttributeComponent"),
    ("chase_breaker", "ChaseBreakerComponent"),
    ("lift", "ElevatorComponent"),
    ("artifact", "FlagComponent"),
    ("scoring_zone", "CTFScoreZoneComponent"),
    ("domination_point", "ControlPointComponent"),
    # last: a bench's coordinator seats NPCs too (it is a bench). A blend group is a GameplayCoordinator whose logic is
    # GcLMPCrowdLife (the static NPC groups players blend into, wall leaners, drinkers, merchants...): 3 - 8 NPC
    # slots relative to the group; the PvE target coordinator (GcLMPTargetCoordinator, a mode block's) is not one
    ("blend_group", "GameplayCoordinatorComponent"),
])
MODE_KINDS = ("artifact", "scoring_zone", "domination_point")
STOCK = games.target().stock_maps
SINK = 150.0      # removed objects sink this far: collision.SINK_DEPTH (a chase-breaker door / lift sunk 1 km is outside
                  # the Havok world and crashes the game when the match is left)
CATALOG_VERSION = 6                                # the cached catalog's Thing layout (a new version rebuilds the cache)
HAY_MARKER = 4
# classes a gameplay object may name that are not resources to carry: map-level objects
MAP_LEVEL = {"GridCellDataBlock", "WorldDataLayerManager", "MapWorldInfo", "NavMeshManager", "BuildTable",
             "EntityBuilder", "CrowdDutyRegion", "WorldArea", "WorldAmbiance", "GridPartition", "FakeEntities"}
# resources whose bodies aren't decoded (big, or hand-written serializers): FX / MaterialTemplate are scanned for
# the source map's identities; textures and animations name nothing
RAW_SCAN = {"FX", "MaterialTemplate"}
NO_REFS = {"TextureMap", "Animation", "NavMeshManager"}
# GameStateData property types (every stock map): bool, vec4, transposed matrix, u32 (Brewery's chandelier)
GSD_SIZE = {0x01: 1, 0x35: 16, 0x41: 64, 0x65: 4}
GSD_TRANSFORM = bytes.fromhex("de099329")          # the entity's own matrix property


class Thing:
    """A gameplay object of a map: an Entity or EntityGroup (top level) with one of KINDS' components."""

    def __init__(self, kind, uid, name, record, matrix, cls, archive):
        self.kind, self.uid, self.name, self.cls, self.archive = kind, uid, name, cls, archive
        self.records = [record]
        self.matrix = matrix
        self.members = []                     # uids of a group's entities
        self.companions = []                  # other top-level entities it names (a chase breaker's FX)
        self.team = None                      # artifact / scoring zone: 1 or 2
        self.hud = None                       # domination point: 1-3
        self.layers = []                      # its own DataLayerFilter's layers
        self.merged = False                   # a member (or a member of it) is in a merged collision tree
        self.box = None                       # world box of its collision / bounding volume
        self.doorway = None                   # two-leaf chase breaker: _doorway's (centre, axis, width, side)
        self.slots = []                       # blend group: its NPC slots [(x, y, z, fx, fy, fz)], group-relative
        self.props = []                       # blend group: the map's prop entities it hands its NPCs (mugs, axes:
                                              # one shared entity per prop for the whole map) - named, never copied

    @property
    def map(self):
        return short_name(self.archive)

    @property
    def model(self):
        return model_name(self.name)

    @property
    def key(self):
        return f"{self.map}:{self.name}"

    def modes(self, info):
        """The mode blocks where it is created (in the block and naming the block's layer)."""
        return [r for r in self.records if info["modes"].get(r, {}).get("layer") in self.layers]

    def __repr__(self):
        extra = ""
        if self.team:
            extra = f" team {self.team}"
        elif self.hud is not None:
            extra = f" {'ABC'[self.hud - 1] if self.hud in (1, 2, 3) else self.hud}"
        return f"<{self.kind}{extra} {self.key} {self.uid.hex()} at {np.round(self.matrix[3, :3], 1).tolist()}>"


def short_name(archive):
    b = os.path.basename(archive)
    for p in ("DataPC_AC3MP_", "DataPC_"):
        if b.startswith(p):
            b = b[len(p):]
    return b[:-len(".forge")] if b.endswith(".forge") else b


def model_name(name):
    """A placed object's model: its name without the instance number ('AC3MP_FORT_HayCart_01a_011' ->
    'AC3MP_FORT_HayCart_01a', 'ACFE_RHO_GEN_HayCart_01a Group_003' -> 'ACFE_RHO_GEN_HayCart_01a Group')."""
    return re.sub(r"_\d+$", "", name)


def _comps(S, ent):
    return [M._obj(p) for p in ent.fields.get("Components", []) if M._obj(p) is not None]


def _members(ent):
    return [M._obj(e) for e in ent.fields.get("Entities", []) or [] if M._obj(e) is not None]


def _bundle_ents(ent):
    """The entity and its group members (each with a GlobalMatrix)."""
    return [ent] + [e for e in _members(ent) if "GlobalMatrix" in e.fields]


def _kind_of(S, ent, body=None):
    found = {S.name_of(c.type_hash) for e in _bundle_ents(ent) for c in _comps(S, e)}
    for k, cn in KINDS.items():
        if cn in found:
            if k == "blend_group" and (body is None or _class_bytes(S, "GcLMPCrowdLife") not in body):
                return None
            return k
    return None


_CLASS_BYTES = {}


def _class_bytes(S, name):
    """A class's id as it is stored in a body (u32 little-endian)."""
    if name not in _CLASS_BYTES:
        h = next(h for h in S.types_by_hash if S.name_of(h) == name)
        _CLASS_BYTES[name] = struct.pack("<I", h)
    return _CLASS_BYTES[name]


def blend_slots(S, body):
    """A blend group's NPC slots: each SpawnPositionBlobParams' 32 bytes (a position and a facing, group-relative; the
    CrowdLife station's GameplayPositions repeat them) -> [(x, y, z, fx, fy, fz)]."""
    pat, out = _class_bytes(S, "SpawnPositionBlobParams"), []
    k = body.find(pat)
    while k >= 0:
        v = struct.unpack_from("<8f", body, k + 12)
        out.append(tuple(round(float(x), 4) for x in (*v[:3], *v[4:7])))
        k = body.find(pat, k + 1)
    return out


def _component(S, ent, cname):
    for e in _bundle_ents(ent):
        c = _comp(S, e, cname)
        if c is not None:
            return c
    return None


def _doorway(S, obj):
    """A chase breaker's opening, from its two door leaves (the elements a BlendPosClip turns about z - every stock
    double door / shutter): (centre (3,), unit axis from hinge to hinge (3,), width m, rest side +1 / -1 - which side
    of the opening (normal = z x axis) the open leaves lie on, [each leaf at rest: (hinge, free edge)]), world. None
    for other breakers (platforms, chandeliers, arches, single leaves)."""
    cb = _component(S, obj, "ChaseBreakerComponent")
    if cb is None:
        return None
    ents = {e.id: e for e in _bundle_ents(obj)}
    leaves = []
    for el in cb.fields.get("Doors", []):
        ef = M._obj(el).fields if M._obj(el) is not None else None
        e = ents.get(ef["Element"].id) if ef is not None and isinstance(ef.get("Element"), Handle) else None
        if e is None:
            continue
        turns = []
        for c in [M._obj(x) for x in ef.get("OpeningClip", []) + ef.get("ClosingClip", [])]:
            if c is not None and S.name_of(c.type_hash) == "BlendPosClip":
                x, y, z, w = struct.unpack("<4f", c.fields["TargetRotation"][:16])
                turns.append(abs(2 * np.arctan2(z, w)))
        if turns and max(turns) > np.radians(20):
            lo, hi, t = collision._bv(e.fields)
            m = collision._matrix(e.fields)
            # type-0 BoundingVolume: world-axis offsets from the hinge - its centre points into the leaf
            into = (np.asarray(lo) + np.asarray(hi)) / 2.0 if t == 0 else \
                (np.asarray(lo) + np.asarray(hi)) / 2.0 @ m[:3, :3]
            tip = m[3, :3] + 2.0 * into * (1.0, 1.0, 0.0)       # the leaf's free edge, at hinge height
            leaves.append((m[3, :3], into, (m[3, :3].copy(), tip)))
    if len(leaves) != 2:
        return None
    a, b = leaves[0][0], leaves[1][0]
    axis = (b - a) * (1.0, 1.0, 0.0)
    width = float(np.linalg.norm(axis))
    if width < 0.5:
        return None
    axis /= width
    normal = np.cross((0.0, 0.0, 1.0), axis)
    side = sum(float(np.dot(into, normal)) for _p, into, _b in leaves)
    return (a + b) / 2.0, axis, width, 1 if side >= 0 else -1, [bx for _p, _i, bx in leaves]


def doorway_placement(tpl, target):
    """The 4x4 for template chase breaker `tpl` (a Thing with a doorway) in the opening of chase breaker `target`:
    hinge line on hinge line, centred, its open leaves on the same side as the target's. -> (4x4, width difference)."""
    if tpl.doorway is None or target.doorway is None:
        raise ValueError(f"{tpl.key if tpl.doorway is None else target.name}: not a two-leaf door chase breaker")
    ct, ut, wt, st = tpl.doorway[:4]
    cd, ud, wd, sd = target.doorway[:4]
    if st != sd:
        ud = -ud
    a = np.arctan2(ud[1], ud[0]) - np.arctan2(ut[1], ut[0])
    R = np.eye(4)
    R[:2, :2] = [[np.cos(a), np.sin(a)], [-np.sin(a), np.cos(a)]]
    T0, T1 = np.eye(4), np.eye(4)
    T0[3, :3], T1[3, :3] = -ct, cd
    return tpl.matrix @ (T0 @ R @ T1), wt - wd


def _refs(obj):
    """Every id an object tree names (Ref / Ptr link / Handle), with the ids it defines itself."""
    names, defs = set(), set()

    def fn(x):
        if isinstance(x, Obj):
            if x.id and x.id != bytes(4):
                defs.add(x.id)
        elif isinstance(x, Ref):
            if x.id and x.id != bytes(4):
                names.add(x.id)
        elif isinstance(x, Ptr):
            if x.link is not None and x.link != bytes(4):
                names.add(x.link)
        elif isinstance(x, Handle):
            if isinstance(x.id, bytes) and x.id != bytes(4):
                names.add(x.id)
    duplicate.walk_values(obj, fn)
    return names - defs, defs


def _world_box(S, ent, shape_boxes):
    """World box of an object: its (and its members') collision boxes through their matrices, else the bounding
    volumes."""
    pts = []
    for e in _bundle_ents(ent):
        m = collision._matrix(e.fields)
        for c in _comps(S, e):
            rb = c.fields.get("RigidBody")
            if isinstance(rb, Obj):
                sid = M._link(rb.fields.get("Shape"))
                if sid in shape_boxes:
                    lo, hi = shape_boxes[sid]
                    pts.extend(mopp.member_box(lo, hi, m))
    if not pts:
        lo, hi, t = collision._bv(ent.fields)
        m = collision._matrix(ent.fields)
        if t == 0:
            pts = [lo + m[3, :3], hi + m[3, :3]]
        else:
            pts = list(mopp.member_box(lo, hi, m))
    p = np.array(pts)
    return p.min(0), p.max(0)


# --------------------------------------------------------------------------------------------------------- reading

def read(archive, log=None):
    """-> dict(things={uid: Thing}, modes (spawns.read's: {block record: dict(layer, label, ...)}), world (uid),
    markers {entity uid: marker position}, archive). Things in several mode blocks (the same uid) are one Thing."""
    S = M.schema()
    C = M.Codec(S)
    things, ents, merged_members, shape_boxes = {}, set(), set(), {}
    world, markers, pending = None, {}, []
    for rec, name, t, body, _f in M.iter_objects_ex(archive):
        cn = S.name_of(t)
        if cn == "World":
            world = body[2:6]
            continue
        if cn == "MeshShape":
            if body[2:6] not in shape_boxes:
                try:
                    v = collision._shape_arrays(C.decode(body).obj.fields)[0]
                    shape_boxes[body[2:6]] = (v.min(0), v.max(0))
                except Exception:  # noqa: BLE001 - a box is a nicety
                    pass
            continue
        if cn == "MapWorldInfo":
            for p in C.decode(body).obj.fields.get("PermanentMarkers", []):
                mk = M._obj(p)
                if mk is not None:
                    key = M._obj(mk.fields["Key"]).fields["Entity"].id
                    markers[key] = struct.unpack("<3f", mk.fields["MarkerPosition"][:12])
            continue
        if cn not in ("Entity", "EntityGroup") or body[:2] != M.UID_MARK:
            continue
        uid = body[2:6]
        ents.add(uid)
        if uid in things:
            if rec not in things[uid].records:
                things[uid].records.append(rec)
            continue
        if not (rec.startswith("Cell") or rec.startswith("DataBlock")):
            continue
        try:
            o = C.decode(body).obj
        except M.DecodeError:
            continue
        mf = collision._multi_fields(S, o)
        if mf is not None:
            merged_members |= {x.id for x in mf["ContainedEntities"]}
            continue
        kind = _kind_of(S, o, body)
        if kind is None:
            continue
        th = Thing(kind, uid, name, rec, collision._matrix(o.fields), cn, archive)
        if kind == "blend_group":
            th.slots = blend_slots(S, body)
        th.members = [e.id for e in _members(o)]
        lf = o.fields.get("DataLayerFilter")
        if isinstance(lf, Obj):
            th.layers = [a.fields["Layer"].id for a in lf.fields.get("LayerActions", [])]
        if kind in ("artifact", "scoring_zone"):
            c = _component(S, o, KINDS[kind])
            th.team = struct.unpack("<I", c.fields["TeamOwner"])[0] + 1
        elif kind == "domination_point":
            th.hud = struct.unpack("<I", _component(S, o, KINDS[kind]).fields["hudId"])[0]
        names, _defs = _refs(o)
        pending.append((th, o, names))
        things[uid] = th
    for th, o, names in pending:
        th.companions = sorted(u for u in names if u in ents and u != th.uid and u not in th.members)
        if th.kind == "blend_group":
            th.props, th.companions = th.companions, []
        th.merged = bool(merged_members & ({th.uid} | set(th.members)))
        th.box = _world_box(S, o, shape_boxes)
        if th.kind == "chase_breaker":
            th.doorway = _doorway(S, o)
    modes = spawns.read(archive)["modes"]
    return dict(things=things, modes=modes, world=world, markers=markers, archive=archive)


_EDGES = {}


def climb_edges(archive):
    """Every climb edge of a map in world space (the GuidanceSystems of its cell / mode-block entities and group
    members): ((k, 2, 3) segment ends, (k,) uid of the top-level object each belongs to). Once per archive per
    process."""
    key = (os.path.abspath(archive), os.path.getsize(archive), os.path.getmtime(archive))
    if key not in _EDGES:
        from . import climb
        S = M.schema()
        C = M.Codec(S)
        segs, owners = [], []
        for rec, _name, t, body, _f in M.iter_objects_ex(archive):
            if S.name_of(t) not in ("Entity", "EntityGroup") or not (rec.startswith("Cell") or
                                                                      rec.startswith("DataBlock")):
                continue
            try:
                o = C.decode(body).obj
            except M.DecodeError:
                continue
            for e in _bundle_ents(o):
                gs = _comp(S, e, "GuidanceSystem")
                if gs is None or not gs.fields.get("GuidanceObjects"):
                    continue
                pts, edges = climb.read(gs.fields)
                m = collision._matrix(e.fields)
                w = pts @ m[:3, :3] + m[3, :3]
                new = [(w[i0], w[i1]) for i0, i1, *_r in edges if i0 < len(w) and i1 < len(w)]
                segs.extend(new)
                owners.extend([o.id] * len(new))
        _EDGES[key] = (np.array(segs, dtype=np.float64).reshape(-1, 2, 3), np.array(owners, dtype=object))
    return _EDGES[key]


def lift_ledge(edges, m, back=(-2.5, 0.5), side=1.5, up=(5.5, 10.5), own=None):
    """Is there a climb edge (climb_edges' pair; not the lift's own, uid `own`) where a lift at 4x4 `m` drops
    players - in its local frame x within +-side, y within `back` (stock lifts: the building on the local -y side),
    z `up` m over its base (8 m lifts land 8 - 9 m up)?"""
    segs, owners = edges
    if own is not None:
        segs = segs[owners != own]
    if not len(segs):
        return False
    R = m[:3, :3] / np.linalg.norm(m[:3, :3], axis=1)[:, None]
    for f in np.linspace(0.0, 1.0, 11):
        p = (segs[:, 0] + (segs[:, 1] - segs[:, 0]) * f - m[3, :3]) @ R.T
        if np.any((np.abs(p[:, 0]) <= side) & (p[:, 1] >= back[0]) & (p[:, 1] <= back[1]) &
                  (p[:, 2] >= up[0]) & (p[:, 2] <= up[1])):
            return True
    return False


def summary(info):
    lines = []
    by = collections.defaultdict(list)
    for th in info["things"].values():
        by[th.kind].append(th)
    for k in KINDS:
        here = by.get(k, [])
        if not here:
            continue
        if k in MODE_KINDS:
            per = collections.Counter(spawns.mode_label(r) for th in here for r in th.modes(info))
            lines.append(f"{k:17} {len(here):4}   " + ", ".join(f"{m} {n}" for m, n in sorted(per.items())))
        else:
            models = collections.Counter(th.model for th in here)
            lines.append(f"{k:17} {len(here):4}   {len(models)} models: " +
                         ", ".join(f"{m} x{n}" for m, n in models.most_common(4)))
    return lines


# --------------------------------------------------------------------------------------------------------- catalog

def stock_maps():
    from . import install
    T = games.target()
    return [os.path.join(install.PRISTINE, T.stock_file(m)) for m in STOCK
            if os.path.exists(os.path.join(install.PRISTINE, T.stock_file(m)))]


def catalog(kind=None, maps=None, refresh=False):
    """The stock maps' gameplay objects as templates: one Thing per model (its first instance in a cell - or in a
    mode block for the mode kinds), keyed '<map>:<name>' (Thing.key); `kind` filters. Cached in <workspace>/cache."""
    maps = maps or stock_maps()
    stamp = [(os.path.basename(p), os.path.getsize(p), int(os.path.getmtime(p))) for p in maps]
    path = os.path.join(M.CACHE, "gameplay_catalog.pkl")
    cat = None
    if not refresh and os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                got = pickle.load(fh)
        except Exception:  # noqa: BLE001 - a stale cache is rebuilt
            got = {}
        if got.get("stamp") == stamp and got.get("version") == CATALOG_VERSION:
            cat = got["things"]
    if cat is None:
        cat = []
        for p in maps:
            info = read(p)
            seen = set()
            for th in sorted(info["things"].values(), key=lambda t: (not t.records[0].startswith("Cell"), t.name)):
                key = (th.kind, th.model if th.kind not in MODE_KINDS else th.name)
                if key in seen:
                    continue
                seen.add(key)
                cat.append(th)
        os.makedirs(M.CACHE, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(dict(stamp=stamp, version=CATALOG_VERSION, things=cat), fh)
    return [t for t in cat if kind is None or t.kind == kind]


def find(template, cat=None):
    """A template: a Thing, '<map>:<model or name>' (map as Thing.map: 'Fort_Wolcott', 'Boston_Mill' ...), or
    (archive path, name or uid bytes) for an object of any map."""
    if isinstance(template, Thing):
        return template
    if isinstance(template, tuple):
        arch, which = template
        info = read(arch)
        for th in info["things"].values():
            if which in (th.uid, th.name, th.model):
                return th
        raise ValueError(f"no gameplay object {which!r} in {os.path.basename(arch)}")
    mp, _s, nm = template.partition(":")
    for th in cat if cat is not None else catalog():
        if th.map.lower() == mp.lower() and nm in (th.name, th.model):
            return th
    raise ValueError(f"no stock gameplay object {template!r} (catalog(): '<map>:<model>', maps {', '.join(STOCK)})")


def placement(template_matrix, at=None, yaw=None, matrix=None):
    """The 4x4 for a copy / move: `matrix` as given, or the template's own turned to heading `yaw` (degrees CCW from
    world +x of its local +x axis; default: as it was) and moved to `at` (x, y, z; default: where it was)."""
    m0 = np.asarray(template_matrix, dtype=np.float64).reshape(4, 4)
    if matrix is not None:
        m = np.asarray(matrix, dtype=np.float64).reshape(4, 4)
        if not np.allclose(np.linalg.norm(m[:3, :3], axis=1), np.linalg.norm(m0[:3, :3], axis=1), atol=1e-3):
            raise ValueError("gameplay objects can't be scaled (Havok collision doesn't scale)")
        return m
    m = m0.copy()
    if yaw is not None:
        cur = np.degrees(np.arctan2(m0[0, 1], m0[0, 0]))
        a = np.radians(float(yaw) - cur)
        rz = np.array([[np.cos(a), np.sin(a), 0], [-np.sin(a), np.cos(a), 0], [0, 0, 1]])
        m[:3, :3] = m0[:3, :3] @ rz
    if at is not None:
        m[3, :3] = at
    return m


# ------------------------------------------------------------------------------------------------- archive index

class _Archive:
    """A map archive held decompressed: records, walked directories, and where each identity is."""

    def __init__(self, path, image=False):
        self.path = path
        self.S = M.schema()
        self.C = M.Codec(self.S)
        self.image = ArchiveImage.read(path) if image else None
        with M.Forge(path) as f:
            self.raw = {r.index: f.read(r) for r in f.records}
            self.recs = [r for r in f.records if r.name != "GlobalMetaFile" and len(self.raw[r.index]) > 8]
        self.payloads, self.walked, self.where, self.cls_of = {}, {}, {}, {}
        self.rec_by_name = {r.name: r.index for r in self.recs}
        for rec in self.recs:
            d = decompress(M.mapwalk.split_prefix(self.raw[rec.index])[1])
            self.payloads[rec.index] = d
            self.walked[rec.index] = M.mapwalk.walk(d)
            for k, (off, size, _s, _f) in enumerate(self.walked[rec.index][0]):
                o = object_at(d, off, size)
                if o["body"][:2] == M.UID_MARK:
                    u = o["body"][2:6]
                    self.where.setdefault(u, []).append((rec.index, k))
                    self.cls_of.setdefault(u, self.S.name_of(o["type"]))
        self._blob = None

    def name_of(self, rec_index):
        return next(r.name for r in self.recs if r.index == rec_index)

    def obj(self, rec_index, k):
        d = self.payloads[rec_index]
        off, size, _s, _f = self.walked[rec_index][0][k]
        return object_at(d, off, size)

    def body(self, uid):
        r, k = self.where[uid][0]
        return self.obj(r, k)

    @property
    def blob(self):
        if self._blob is None:
            self._blob = b"".join(self.payloads.values())
        return self._blob

    def first(self, cname):
        for u, c in self.cls_of.items():
            if c == cname:
                return u
        return None


_GLOBAL = {}


def _global_ids():
    """Every id the live DataPC / DataPC_extra / DataPC_CoreFX / DataPC_Fire use (forgelib's id census; the FX a
    flag, scoring zone, Domination point or chase breaker names are CoreFX's), ints and bytes, once per process."""
    if "ids" not in _GLOBAL:
        live = os.path.dirname(spawns._id_archives()[0])
        arcs = [os.path.join(live, a) for a in ("DataPC.forge", "DataPC_extra.forge", "DataPC_CoreFX.forge",
                                                 "DataPC_Fire.forge")]
        ids = duplicate.used_ids([], [a for a in arcs if os.path.exists(a)])
        _GLOBAL["ids"] = ids | {struct.pack("<I", u) for u in ids if isinstance(u, int)}
    return _GLOBAL["ids"]


# ----------------------------------------------------------------------------------------------- copies and moves

def _b(lst):
    return bytes(x[0] if isinstance(x, (bytes, bytearray)) else x for x in lst)


def _gsd_fix(ent, mapping, D):
    """An entity's GameStateData: component uids in DescBuffer renewed by `mapping`, every matrix property (type
    0x41, stored transposed) moved by D (row-vector: m_new = m_old @ D)."""
    rd = M._obj(ent.fields.get("ResetData")) if "ResetData" in ent.fields else None
    if rd is None:
        return
    f = rd.fields
    desc, data = bytearray(_b(f["DescBuffer"])), bytearray(_b(f["DataBuffer"]))
    n = struct.unpack("<I", f["PropertyCount"])[0]
    off = 4
    for k in range(n):
        p = 4 + 10 * k
        ty = desc[p + 1]
        u = bytes(desc[p + 2:p + 6])
        if u in mapping:
            desc[p + 2:p + 6] = mapping[u]
        if ty not in GSD_SIZE:
            raise ValueError(f"GameStateData property type {ty:#x} unknown - can't move it")
        if ty == 0x41 and D is not None:
            st = np.frombuffer(bytes(data[off:off + 64]), "<f4").astype(np.float64).reshape(4, 4).T
            data[off:off + 64] = (st @ D).T.astype("<f4").tobytes()
        off += GSD_SIZE[ty]
    if off != len(data):
        raise ValueError("GameStateData data size doesn't match its properties")
    f["DescBuffer"] = [bytes([x]) for x in desc]
    f["DataBuffer"] = [bytes([x]) for x in data]


def _gsd_check(ent, foreign=()):
    """Read-back of an entity's GameStateData: its own-matrix property = its GlobalMatrix (transposed), and no
    property names an id in `foreign` (the template's ids a copy must not keep). -> [problems]."""
    rd = M._obj(ent.fields.get("ResetData")) if "ResetData" in ent.fields else None
    if rd is None:
        return []
    out = []
    f = rd.fields
    desc, data = _b(f["DescBuffer"]), _b(f["DataBuffer"])
    off = 4
    for k in range(struct.unpack("<I", f["PropertyCount"])[0]):
        p = 4 + 10 * k
        ty, u, h = desc[p + 1], desc[p + 2:p + 6], desc[p + 6:p + 10]
        if u in foreign:
            out.append(f"its state names the template's {u.hex()}")
        if ty == 0x41 and h == GSD_TRANSFORM:
            st = np.frombuffer(data[off:off + 64], "<f4").reshape(4, 4).T
            if not np.allclose(st, collision._matrix(ent.fields), atol=1e-3):
                out.append("its stored state isn't at its place")
        off += GSD_SIZE.get(ty, 0)
    return out


def _move_tree(S, obj, D, cell_index=None):
    """Every entity of an object (it, its members) moved by D: GlobalMatrix, type-0 BoundingVolume, GameStateData
    matrices, FlagComponent.OriginalPosition; FakeCellIndex set (when given) where it isn't 0xFFFF."""
    for e in _bundle_ents(obj):
        m0 = collision._matrix(e.fields)
        m1 = m0 @ D
        e.fields["GlobalMatrix"] = m1.astype("<f4").tobytes()
        lo, hi, t = collision._bv(e.fields)
        if t == 0:
            collision._set_bv(e.fields, *collision.moved_bv(lo, hi, m0, m1))
        _gsd_fix(e, {}, D)
        fc = _comp(S, e, "FlagComponent")
        if fc is not None:
            fc.fields["OriginalPosition"] = struct.pack("<4f", *m1[3, :3], 0.0)
        if cell_index is not None and e.fields.get("FakeCellIndex") not in (None, b"\xff\xff"):
            e.fields["FakeCellIndex"] = struct.pack("<H", cell_index)


def _blockers(S, C, AR, obj, D):
    """World triangles (k, 3, 3) of an object's solid collision (its entities' InertComponent MeshShapes, from
    archive AR) after moving it by D - what crowds must not walk through (navcut)."""
    out = []
    for e in _bundle_ents(obj):
        m = collision._matrix(e.fields) @ D
        c = _comp(S, e, "InertComponent")
        sid = M._link(c.fields["RigidBody"].fields.get("Shape")) if c is not None else None
        if sid is None or AR.cls_of.get(sid) != "MeshShape":
            continue
        v, t, _m = collision._shape_arrays(C.decode(AR.body(sid)["body"]).obj.fields)
        w = np.asarray(v, np.float64) @ m[:3, :3] + m[3, :3]
        out.append(w[np.asarray(t, np.int64)])
    return out


def _renew(obj, mapping):
    def renew(x):
        if isinstance(x, Obj):
            x.id = mapping.get(x.id, x.id)
        elif isinstance(x, Ref):
            x.id = mapping.get(x.id, x.id)
        elif isinstance(x, Ptr) and x.link is not None:
            x.link = mapping.get(x.link, x.link)
        elif isinstance(x, Handle):
            x.id = mapping.get(x.id, x.id)
    duplicate.walk_values(obj, renew)
    for e in [obj] + _members(obj):
        if "ResetData" in e.fields:
            _gsd_fix(e, mapping, None)


def _set_layers(obj, layers, proto):
    lf = obj.fields.get("DataLayerFilter")
    if not isinstance(lf, Obj):
        if layers:
            raise ValueError("the object has no DataLayerFilter - it can't go into a game-mode block")
        return
    acts = []
    for lay in layers:
        a = copy.deepcopy(proto)
        a.fields["Layer"].id = lay
        acts.append(a)
    lf.fields["LayerActions"] = acts


# --------------------------------------------------------------------------------------------------------- planning

def plan(info, add=(), move=None, remove=(), cat=None):
    """Resolve a request against the map read by `read`. add: [dict(template, at / yaw / matrix - or doorway (uid /
    name of one of the map's two-leaf door chase breakers: the copy goes into its opening, hinges on its hinges, and
    it is removed unless keep_old), modes (block records or mode labels - mode kinds: default every block holding
    that kind; other kinds: default a cell), team (artifact / scoring zone: 1 / 2, default the template's), hud
    (domination point: 1-3), name)]. move: {uid / name: 4x4 or
    dict(at, yaw, matrix)}. remove: [uid / name]. -> dict(adds, moves {uid: (D, new 4x4)}, removes, final) - final:
    {uid or ('new', k): (kind, 4x4, records, team, hud)} after the edit. Raises ValueError for what can't be done."""
    things, modes = info["things"], info["modes"]
    by_name = {}
    for th in things.values():
        by_name.setdefault(th.name, th)

    def resolve(x):
        th = things.get(x) if isinstance(x, bytes) else by_name.get(x)
        if th is None:
            raise ValueError(f"no gameplay object {x if isinstance(x, str) else x.hex()} on this map")
        return th

    def blocks(want, kind):
        out = []
        for w in want:
            r = w if w in modes else next((r for r in modes if spawns.mode_label(r).lower() == str(w).lower()), None)
            if r is None:
                have = ", ".join(sorted(spawns.mode_label(r) for r in modes))
                raise ValueError(f"{w!r} isn't a game-mode block of this map ({have})")
            out.append(r)
        return out

    adds = []
    remove = list(remove)
    for k, a in enumerate(add):
        tpl = find(a["template"], cat)
        width = target = None
        if isinstance(a.get("doorway"), dict):
            # into a doorway described by the caller (a new building's): centre (x, y, z) - the floor in the middle of
            # the opening -, axis (along the opening), width (default the template's), side (+1 / -1: which side of
            # z x axis the open leaves rest on)
            d = a["doorway"]
            ax = np.array([d["axis"][0], d["axis"][1], 0.0], np.float64)
            spot = types.SimpleNamespace(name="the given doorway", doorway=(
                np.asarray(d["centre"], np.float64), ax / np.linalg.norm(ax),
                float(d.get("width", tpl.doorway[2] if tpl.doorway else 0.0)), 1 if d.get("side", 1) >= 0 else -1))
            m, width = doorway_placement(tpl, spot)
        elif a.get("doorway") is not None:
            # into the opening of one of the map's own chase breakers, which goes (unless keep_old)
            target = resolve(a["doorway"])
            m, width = doorway_placement(tpl, target)
            if not a.get("keep_old") and target.uid not in remove and target.name not in remove:
                remove.append(target.uid)
        else:
            m = placement(tpl.matrix, a.get("at"), a.get("yaw"), a.get("matrix"))
        want = a.get("modes")
        if want is None and tpl.kind in MODE_KINDS:
            want = sorted({r for th in things.values() if th.kind == tpl.kind for r in th.modes(info)})
            if not want:
                raise ValueError(f"{tpl.key}: this map has no game-mode block with a {tpl.kind} - name `modes`")
        recs = blocks(want, tpl.kind) if want else None
        team = a.get("team", tpl.team)
        hud = a.get("hud", tpl.hud)
        if tpl.kind in ("artifact", "scoring_zone") and team not in (1, 2):
            raise ValueError(f"{tpl.key}: team must be 1 or 2")
        if tpl.kind == "domination_point" and hud not in (1, 2, 3):
            raise ValueError(f"{tpl.key}: hud must be 1, 2 or 3 (A, B, C)")
        adds.append(dict(template=tpl, matrix=m, records=recs, team=team, hud=hud, width=width,
                         doorway=target.uid if target is not None else None,
                         name=a.get("name") or f"{tpl.model}_GP{k + 1:03d}"))
    moves = {}
    for x, want in (move or {}).items():
        th = resolve(x)
        m1 = placement(th.matrix, want.get("at"), want.get("yaw"), want.get("matrix")) if isinstance(want, dict) \
            else placement(th.matrix, matrix=want)
        moves[th.uid] = (np.linalg.inv(th.matrix) @ m1, m1)
    removes, deletes = [], []
    for x in remove:
        th = resolve(x)
        if th.uid in removes:
            continue                            # named twice (by name and uid, or as a doorway's old door)
        if th.kind in MODE_KINDS:
            raise ValueError(f"{th.name}: {th.kind.replace('_', ' ')}s can't be removed (the game needs one artifact "
                             f"per team and three Domination points) - move it instead")
        if th.uid in moves:
            raise ValueError(f"{th.name}: both moved and removed")
        removes.append(th.uid)
        if th.kind == "blend_group":
            # taken out of its cell (record and GridCellDataBlock list): only that list names a blend group, and sunk
            # its NPCs would still be sent to it
            deletes.append(th.uid)
            continue
        m1 = th.matrix.copy()
        m1[3, 2] -= SINK
        moves[th.uid] = (np.linalg.inv(th.matrix) @ m1, m1)
    # companions follow their object (a chase breaker's FX); one named by two objects stays put
    named = collections.Counter(c for th in things.values() for c in th.companions)
    follow = {}
    for uid, (D, _m) in moves.items():
        for c in things[uid].companions:
            if named[c] == 1:
                follow[c] = D
    final = {}
    for uid, th in things.items():
        if uid in deletes:
            continue
        m = moves[uid][1] if uid in moves else th.matrix
        final[uid] = (th.kind, m, th.modes(info) if th.kind in MODE_KINDS else th.records, th.team, th.hud)
    for k, a in enumerate(adds):
        final[("new", k)] = (a["template"].kind, a["matrix"], a["records"] or [], a["team"], a["hud"])
    return dict(adds=adds, moves=moves, removes=removes, deletes=deletes, follow=follow, final=final)


def zone_moves(info, p, areas):
    """zones="follow": each moved Domination point's zone (the one holding it now) carried along with it.
    -> {zone uid: [polygons]}."""
    out = {}
    for uid, (D, m1) in p["moves"].items():
        th = info["things"][uid]
        if th.kind != "domination_point":
            continue
        z = _zone_holding(th.matrix[3, :2], th.modes(info), areas)
        if z is None:
            continue
        if z in out:
            raise ValueError(f"two moved points share zone {areas[z]['name']}")
        out[z] = [np.asarray(poly, dtype=np.float64) @ D[:2, :2] + D[3, :2] for poly in areas[z]["polygons"]]
    return out


def _zone_holding(p, records, areas, exclude=()):
    from . import territories
    for zuid, a in areas.items():
        if a["kind"] == "zone" and zuid not in exclude and set(records) & set(a["records"]) and \
                territories.member(a["polygons"], p):
            return zuid
    return None


def _overlap(ground, box, D, base, own, step=0.5, inset=0.25, lo=0.3, hi=1.8):
    """The first point of something solid (a collision triangle of another object, any slope) inside a world box
    moved by D, between `lo` and `hi` m over `base` - or None. Vertical lines through the footprint (floors, table
    tops, roofs) and horizontal lines across it at three heights (walls: a vertical line inside a solid wall meets
    only its top and bottom)."""
    b0, b1 = np.asarray(box[0], np.float64), np.asarray(box[1], np.float64)
    cs = np.array([(x, y, z, 1.0) for x in (b0[0], b1[0]) for y in (b0[1], b1[1]) for z in (b0[2], b1[2])]) @ D
    b0, b1 = cs[:, :3].min(0) + inset, cs[:, :3].max(0) - inset
    xs = np.arange(b0[0], b1[0] + 1e-6, step) if b1[0] > b0[0] else np.array([(b0[0] + b1[0]) / 2])
    ys = np.arange(b0[1], b1[1] + 1e-6, step) if b1[1] > b0[1] else np.array([(b0[1] + b1[1]) / 2])
    for x in xs:
        for y in ys:
            for z, o in ground.heights(x, y, exclude=own, walkable=0.0):
                if base + lo < z < base + hi:
                    return (x, y, z)
    if hi - lo > 3.0:
        return None                            # an open-sky test: the vertical lines are the question
    segs = []
    for h in np.linspace(base + lo + 0.2, base + hi - 0.2, 3):
        segs += [((xs[0], y, h), (xs[-1], y, h)) for y in ys] + [((x, ys[0], h), (x, ys[-1], h)) for x in xs]
    return _segments_hit(ground, segs, own)


def _segments_hit(ground, segs, own):
    """The first point where one of the segments [(a, b)] crosses a steep collision face (walls, sides: |normal z|
    < 0.7 - floors and slopes are vertical tests' business) of an object not in `own`, or None."""
    segs = [(np.asarray(a, np.float64), np.asarray(b, np.float64)) for a, b in segs]
    if not segs:
        return None
    pts = np.array([p for s in segs for p in s])
    c = ground.cell
    ids = set()
    for gx in range(int(np.floor(pts[:, 0].min() / c)), int(np.floor(pts[:, 0].max() / c)) + 1):
        for gy in range(int(np.floor(pts[:, 1].min() / c)), int(np.floor(pts[:, 1].max() / c)) + 1):
            ids.update(ground.grid.get((gx, gy), []))
    ids = np.array([i for i in ids if ground.owner[i] not in own and abs(ground.nz[i]) < 0.7], dtype=np.int64)
    if not len(ids):
        return None
    T = ground.t[ids]
    e1, e2 = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    for a, b in segs:
        a, b = np.array(a), np.array(b)
        d = b - a
        if not np.any(d):
            continue
        pv = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, pv)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = a - T[:, 0]
        u = np.einsum("ij,ij->i", tv, pv) * inv
        qv = np.cross(tv, e1)
        v = (qv @ d) * inv
        t = np.einsum("ij,ij->i", e2, qv) * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t >= 0) & (t <= 1)
        if hit.any():
            k = int(np.argmax(hit))
            return tuple(a + d * t[k])
    return None


def problems(info, p, areas=None, pairs=None, ground=None, zones=None, edges=None):
    """-> (errors, warnings) for the planned state (plan's `final`). areas: boundary.areas (out of bounds, zones);
    pairs: territories.pairs (Artifact Assault); ground: ground.Ground of the map (new / moved objects should stand
    on something); zones: {zone uid: polygons} the build gives Domination zones (zone_moves); edges: climb_edges of
    the map (a lift needs a ledge where it drops players)."""
    from . import boundary, territories
    errors, warnings = [], []
    final = p["final"]
    changed = set(p["moves"]) | {k for k in final if isinstance(k, tuple)}
    gone = {x for u in p["removes"] for x in (u, *info["things"][u].members, *info["things"][u].companions)}
    for k, a in enumerate(p["adds"]):
        if a.get("width") is not None and abs(a["width"]) > 0.25:
            warnings.append(f"{a['name']}: its doorway is {abs(a['width']):.2f} m "
                            f"{'wider' if a['width'] > 0 else 'narrower'} than the opening it goes into")
    areas = dict(areas or {})
    for z, polys in (zones or {}).items():
        areas[z] = dict(areas[z], polygons=polys)

    def label(key):
        if isinstance(key, tuple):
            return p["adds"][key[1]]["name"]
        return info["things"][key].name
    # Artifact Assault: per block
    blocks = collections.defaultdict(lambda: collections.defaultdict(list))
    for key, (kind, m, recs, team, hud) in final.items():
        if kind in MODE_KINDS:
            for r in recs:
                blocks[r][kind].append((key, m, team, hud))
    for r, here in sorted(blocks.items()):
        lab = spawns.mode_label(r)
        flags = here.get("artifact", [])
        if flags:
            for t in (1, 2):
                n = sum(1 for f in flags if f[2] == t)
                if n != 1:
                    errors.append(f"{lab}: {n} artifacts of team {t} - the game holds exactly one per team")
            pair = next((pr for pr in pairs or () if r in pr["records"]), None)
            if pair is not None:
                terr = {}
                for key, m, team, _h in flags:
                    # a territory belongs to the team whose artifact stands in it (match start binds by position)
                    inside = [z for z in pair["uids"] if territories.member(pair["zones"][z]["polygons"], m[3, :2])]
                    if len(inside) != 1:
                        errors.append(f"{lab}: {label(key)} (team {team}) is in {len(inside)} territories - a team's "
                                      f"artifact must stand in exactly one, its own")
                    else:
                        terr[team] = inside[0]
                if len(terr) == 2 and terr[1] == terr[2]:
                    errors.append(f"{lab}: both teams' artifacts stand in one territory")
                for key, m, team, _h in here.get("scoring_zone", []):
                    z = terr.get(team)
                    if z is not None and not territories.member(pair["zones"][z]["polygons"], m[3, :2]):
                        errors.append(f"{lab}: {label(key)} (team {team}) is outside its team's territory")
            for key, m, team, _h in here.get("scoring_zone", []):
                own = [f for f in flags if f[2] == team]
                if own and np.linalg.norm(own[0][1][3, :2] - m[3, :2]) > 1.0 and key in changed:
                    warnings.append(f"{lab}: {label(key)} is {np.linalg.norm(own[0][1][3, :2] - m[3, :2]):.1f} m "
                                    f"from its team's artifact (stock maps put it on the artifact)")
        pts = here.get("domination_point", [])
        if pts:
            if len(pts) != 3:
                errors.append(f"{lab}: {len(pts)} Domination points - the HUD takes exactly three")
            huds = sorted(h for _k, _m, _t, h in pts)
            if huds != [1, 2, 3][:len(huds)] or len(set(huds)) != len(huds):
                errors.append(f"{lab}: Domination point ids {huds} - they must be 1, 2, 3 (A, B, C)")
            held = {}
            for key, m, _t, hud in pts:
                z = _zone_holding(m[3, :2], [r], areas)
                if z is None:
                    errors.append(f"{lab}: {label(key)} stands in no Domination zone - a point claims the zone "
                                  f"holding it")
                elif z in held:
                    errors.append(f"{lab}: {label(key)} and {label(held[z])} stand in the same zone")
                else:
                    held[z] = key
    # new / moved objects in each other's way (footprints, shrunk by 0.25 m)
    foot = {}
    for key in changed:
        if key in p["removes"] or final[key][0] in MODE_KINDS:
            continue
        if isinstance(key, tuple):
            tpl = p["adds"][key[1]]["template"]
            box, D = tpl.box, np.linalg.inv(tpl.matrix) @ final[key][1]
        else:
            box, D = info["things"][key].box, p["moves"][key][0]
        if box is not None:
            cs = np.array([(x, y, z, 1.0) for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1])
                           for z in (box[0][2], box[1][2])]) @ D
            foot[key] = (cs[:, :2].min(0) + 0.25, cs[:, :2].max(0) - 0.25)
    keys = sorted(foot, key=str)
    for i, a in enumerate(keys):
        for b in keys[:i]:
            if np.all(foot[a][0] < foot[b][1]) and np.all(foot[b][0] < foot[a][1]):
                warnings.append(f"{label(a)} and {label(b)} stand in each other's way")
    # out of bounds, ground
    for key in sorted(changed, key=str):
        kind, m, recs, _t, _h = final[key]
        if key in p["removes"]:
            continue
        xy = m[3, :2]
        oob = [a for a in areas.values() if a.get("kind") == "oob" and (not recs or set(recs) & set(a["records"])
                                                                         or kind not in MODE_KINDS)]
        if oob:
            res = [boundary.inside_area(xy, a["corners"], a["closed"], a["ref"]) for a in oob]
            if kind in MODE_KINDS:
                for a, (ok, d) in zip(oob, res):
                    if set(recs) & set(a["records"]) and not ok:
                        errors.append(f"{label(key)} at ({xy[0]:.1f}, {xy[1]:.1f}) is out of bounds in "
                                      f"{', '.join(spawns.mode_label(r) for r in set(recs) & set(a['records']))}")
            elif not any(ok for ok, _d in res):
                warnings.append(f"{label(key)} at ({xy[0]:.1f}, {xy[1]:.1f}) is out of bounds in every mode")
            if kind == "lift":
                # it lifts players onto the roof behind it (stock lifts: local -y, 1 - 2 m back, 8 - 9 m up)
                top = xy - m[1, :2] / max(np.linalg.norm(m[1, :2]), 1e-9) * 1.5
                bad = [a for a, (ok, _d) in zip(oob, res) if ok and not boundary.inside_area(
                    top, a["corners"], a["closed"], a["ref"])[0]]
                if bad:
                    where = ", ".join(sorted({spawns.mode_label(r) for a in bad for r in a["records"]}))
                    warnings.append(f"{label(key)} lifts players out of bounds (its landing at ({top[0]:.1f}, "
                                    f"{top[1]:.1f}) is outside {where})")
        if kind == "lift" and edges is not None and not lift_ledge(edges, m, own=None if isinstance(key, tuple)
                                                                     else key):
            # every stock lift (28) drops players at a ledge of the building behind it; at a plain roof edge a lift
            # throws the player up and lets them fall straight back down
            warnings.append(f"{label(key)} has no ledge to catch where it drops players (5.5 - 10.5 m up, within "
                            f"2.5 m behind it) - players are thrown up and fall back down")
        if kind == "blend_group":
            # its NPCs stand on its slots: each on the ground (not floating, not buried) with room to stand (nothing
            # solid 0.3 - 1.8 m over it within 0.15 m: stock wall leaners and drinkers stand 0.2 - 0.3 m from their
            # wall / table - every stock group of Wolcott and Mill passes) - the group's disc has no collision
            if ground is not None:
                from . import ground as G
                th = p["adds"][key[1]]["template"] if isinstance(key, tuple) else info["things"][key]
                off, blocked = [], []
                for s in th.slots:
                    w = np.array([s[0], s[1], s[2], 1.0]) @ m
                    z = G.surface(ground, w[0], w[1], w[2] + 0.5, ())
                    if z is None or abs(w[2] - z) > 0.4:
                        off.append(w[:3] if z is None else (w[0], w[1], z - w[2]))
                    hit = _overlap(ground, ((w[0] - 0.4, w[1] - 0.4, w[2]), (w[0] + 0.4, w[1] + 0.4, w[2] + 2.0)),
                                   np.eye(4), w[2], gone)
                    if hit is not None:
                        blocked.append(hit)
                if off:
                    warnings.append(f"{label(key)}: {len(off)} of its {len(th.slots)} NPC slot(s) not on the ground "
                                    f"(first at ({off[0][0]:.1f}, {off[0][1]:.1f}))")
                if blocked:
                    b = blocked[0]
                    warnings.append(f"{label(key)}: {len(blocked)} of its {len(th.slots)} NPC slot(s) inside something "
                                    f"solid (first at ({b[0]:.1f}, {b[1]:.1f}, {b[2]:.1f}))")
            continue
        if ground is not None:
            # something else solid where it stands (0.3 - 1.8 m over its base, inside its box shrunk by 0.25 m; the
            # mode-block objects have no collision of their own: 1.5 m round them, where players stand to use them)
            if kind in MODE_KINDS:
                box, D, own = (m[3, :3] - (1.0, 1.0, 0.0), m[3, :3] + (1.0, 1.0, 2.0)), np.eye(4), set()
            elif isinstance(key, tuple):
                tpl = p["adds"][key[1]]["template"]
                box, D, own = tpl.box, np.linalg.inv(tpl.matrix) @ m, set()
            else:
                th = info["things"][key]
                box, D, own = th.box, p["moves"][key][0], {th.uid, *th.members, *th.companions}
            own = own | gone                    # what this build removes isn't in the way
            if isinstance(key, tuple) and p["adds"][key[1]].get("width") is not None:
                # in a doorway its whole box spans the opening's jambs: its open door leaves must be as clear as the
                # opening's own door's were (open leaves sit flush on walls and frames - 36 of the 89 stock doors
                # touch something along their middle in their own place, so only a change is a finding)
                def leaf_hit(leaves, DD):
                    for h, t in leaves:
                        h, t = np.append(h, 1.0) @ DD, np.append(t, 1.0) @ DD
                        a, b = h[:3] + (t[:3] - h[:3]) * 0.15, h[:3] + (t[:3] - h[:3]) * 0.85
                        hit = _segments_hit(ground, [(a + (0, 0, dz), b + (0, 0, dz)) for dz in (0.5, 1.0, 1.5)],
                                            own)
                        if hit is not None:
                            return hit
                    return None
                old_uid = p["adds"][key[1]]["doorway"]
                old = info["things"][old_uid] if old_uid is not None else None
                if old is None or leaf_hit(old.doorway[4], np.eye(4)) is None:
                    hit = leaf_hit(tpl.doorway[4], D)
                    if hit is not None:
                        warnings.append(f"{label(key)}: an open door leaf runs into something solid at "
                                        f"({hit[0]:.1f}, {hit[1]:.1f}, {hit[2]:.1f})"
                                        + (" (the opening's own door's leaves are clear)" if old else ""))
                box = None
            if box is not None:
                hit = _overlap(ground, box, D, m[3, 2], own)
                if hit is not None:
                    warnings.append(f"{label(key)} at ({xy[0]:.1f}, {xy[1]:.1f}) overlaps something solid at "
                                    f"({hit[0]:.1f}, {hit[1]:.1f}, {hit[2]:.1f})")
                if kind == "hiding_spot":
                    # players dive in from above (leap of faith): open sky over it
                    hit = _overlap(ground, box, D, m[3, 2], own, lo=2.5, hi=30.0)
                    if hit is not None:
                        warnings.append(f"{label(key)} at ({xy[0]:.1f}, {xy[1]:.1f}) has something over it at "
                                        f"({hit[0]:.1f}, {hit[1]:.1f}, {hit[2]:.1f}) - no diving in from above")
        if ground is not None:
            from . import ground as G
            z = G.surface(ground, xy[0], xy[1], m[3, 2] + 0.5, ())
            if z is None or m[3, 2] - z > 0.5:
                warnings.append(f"{label(key)} at ({xy[0]:.1f}, {xy[1]:.1f}, {m[3, 2]:.1f}) has no ground under it"
                                + ("" if z is None else f" (the ground is {m[3, 2] - z:.1f} m down)"))
            elif z - m[3, 2] > 0.3:
                warnings.append(f"{label(key)} at ({xy[0]:.1f}, {xy[1]:.1f}) is {z - m[3, 2]:.1f} m under the ground")
    return errors, warnings


# ------------------------------------------------------------------------------------------------------------ apply

def apply(src, dest, info, p, log=print):
    """Write plan `p` into a copy of map `src` at `dest`; read back. -> dict(ok, made [(name, uid, records)],
    moved [uid], removed [uid], resources {record: n}, fails)."""
    A = _Archive(src, image=True)
    S, C = A.S, A.C
    cls = {S.name_of(h): h for h in S.types_by_hash}
    modes = info["modes"]
    world = info["world"]
    gl = _global_ids()
    avoid = set(gl)
    fresh = spawns._Fresh(avoid, duplicate.seed(src, b"GPLY", len(p["adds"])), A.blob)

    # the grid (cell kinds go to the cell under them)
    grid = cells = None
    for u, c in A.cls_of.items():
        if c == "World":
            g = C.decode(A.body(u)["body"]).obj.fields["GridLayout"].fields
            grid = tuple(struct.unpack("<i", g[k])[0] for k in ("BottomLeftX", "BottomLeftY", "CellSize",
                                                                  "GridDimensionLevel0"))
    cells = {r.index for r in A.recs if r.name.startswith("Cell")}
    proto_action = None                        # a DataLayerAction to build filters from
    for th in info["things"].values():
        if th.layers:
            proto_action = C.decode(A.body(th.uid)["body"]).obj.fields["DataLayerFilter"].fields["LayerActions"][0]
            break
    for r in A.recs if proto_action is None else ():
        if not r.name.startswith("DataBlock"):
            continue
        for k in range(len(A.walked[r.index][0])):
            o = A.obj(r.index, k)
            if o["type"] != cls["Entity"] or spawns.FILTER_CLASS not in o["body"]:
                continue
            try:
                acts = C.decode(o["body"]).obj.fields["DataLayerFilter"].fields["LayerActions"]
            except Exception:  # noqa: BLE001 - spawn starts don't decode
                continue
            if acts:
                proto_action = acts[0]
                break
        if proto_action is not None:
            break

    # 1. the copies: per source map, the template bundles and their resources
    adds_in = collections.defaultdict(list)    # record index -> [(serial, bytes, filt, uid, is_world)]
    made, marker_adds = {}, []                 # add index -> what was made
    res_memo = {}                              # (source archive, id) -> id in dest
    gsd_ok = set()                             # entities (new ids) whose stored state matched their place
    blockers = []                              # world collision triangles of new / moved objects (navcut)
    by_src = collections.defaultdict(list)
    for k, a in enumerate(p["adds"]):
        by_src[a["template"].archive].append(k)
    stored = collections.defaultdict(set)      # record index -> identities it holds
    for u, places in A.where.items():
        for ri, _k in places:
            stored[ri].add(u)
    for sarch, ks in by_src.items():
        same = os.path.normcase(os.path.abspath(sarch)) == os.path.normcase(os.path.abspath(src))
        SA = A if same else _Archive(sarch)
        s_world = SA.first("World")
        s_markers = {}
        mwi = SA.first("MapWorldInfo")
        if mwi is not None:
            for x in C.decode(SA.body(mwi)["body"]).obj.fields.get("PermanentMarkers", []):
                mk = M._obj(x)
                if mk is not None:
                    s_markers[M._obj(mk.fields["Key"]).fields["Entity"].id] = mk
        for k in ks:
            a = p["adds"][k]
            tpl = a["template"]
            main = C.decode(SA.body(tpl.uid)["body"]).obj
            # the bundle: the object and the entities it names (companions), recursively
            bundle, todo, resources = [(tpl.uid, main)], [main], {}
            seen = {tpl.uid}
            while todo:
                o = todo.pop()
                names, _d = _refs(o)
                for u in sorted(names):
                    c = SA.cls_of.get(u)
                    if c is None or u in seen:
                        continue
                    if c in ("Entity", "EntityGroup"):
                        seen.add(u)
                        if tpl.kind == "blend_group" and u in tpl.props:
                            # a prop its NPCs pick up: the map's one shared entity, named as it is
                            if A.cls_of.get(u) not in ("Entity", "EntityGroup"):
                                raise ValueError(f"{a['name']}: this map has no {SA.body(u)['name']} for its NPCs to "
                                                 f"hold - pick another blend group")
                            continue
                        obj = C.decode(SA.body(u)["body"]).obj
                        bundle.append((u, obj))
                        todo.append(obj)
                    elif c == "World" or c in MAP_LEVEL:
                        continue
                    else:
                        seen.add(u)
                        resources[u] = c
            # resources named by resources
            stack = list(resources)
            while stack:
                u = stack.pop()
                c = resources[u]
                if c in NO_REFS:
                    continue
                body = SA.body(u)["body"]
                if c in RAW_SCAN:
                    names = {body[i:i + 4] for i in range(6, len(body) - 3)} & set(SA.cls_of)
                else:
                    try:
                        names, _d = _refs(C.decode(body).obj)
                    except Exception:  # noqa: BLE001 - an undecodable resource: scanned
                        names = {body[i:i + 4] for i in range(6, len(body) - 3)} & set(SA.cls_of)
                for v in sorted(names):
                    cv = SA.cls_of.get(v)
                    if cv is None or v in resources or v == u or cv in ("Entity", "EntityGroup", "World") or \
                            cv in MAP_LEVEL:
                        continue
                    resources[v] = cv
                    stack.append(v)
            # where it goes
            m_new = a["matrix"]
            D = np.linalg.inv(tpl.matrix) @ m_new
            if a["records"]:
                targets = [A.rec_by_name[r] for r in a["records"]]
                cell_ix = None
            else:
                t, _c, _r = _cell_of(grid, m_new, A.rec_by_name, cells, None)
                col, row = (int(np.floor((m_new[3, i] - grid[i]) / grid[2])) for i in (0, 1))
                cell_ix = morton(col, row) if 0 <= col < grid[3] and 0 <= row < grid[3] else None
                if t is None:
                    # no leaf cell record there (the ACR ports number their cells otherwise: Souk's start at
                    # Cell00302): the record of the nearest gameplay object standing in a cell
                    near = [th for th in info["things"].values() if th.records[0] in A.rec_by_name
                            and A.rec_by_name[th.records[0]] in cells]
                    if not near:
                        raise ValueError(f"{a['name']}: no cell record for ({m_new[3, 0]:.1f}, {m_new[3, 1]:.1f})")
                    th = min(near, key=lambda q: np.linalg.norm(q.matrix[3, :2] - m_new[3, :2]))
                    t = A.rec_by_name[th.records[0]]
                targets = [t]
            # resource ids in dest: kept, or fresh when the destination / DataPC use the id for something else
            rmap = {}
            for u in resources:
                key = (sarch, u)
                if key not in res_memo:
                    sbody = SA.body(u)["body"]
                    if u in A.where and A.body(u)["body"] == sbody:
                        res_memo[key] = u
                    elif same:
                        res_memo[key] = u
                    elif u in A.where or u in A.blob or u in avoid or struct.unpack("<I", u)[0] in avoid:
                        res_memo[key] = fresh.take()
                    else:
                        res_memo[key] = u
                        avoid.add(u)
                rmap[u] = res_memo[key]
            # the instances: fresh ids for everything nested, World -> the destination's
            inner = {}
            for _u, o in bundle:
                for i in duplicate.object_ids(o):
                    inner.setdefault(i, None)
            # ids it names that nothing defines (a chase breaker's clips' ControlledActor links - neither the map nor
            # DataPC / CoreFX hold them): renewed as well, so two copies never share one
            outside = set()
            for _u, o in bundle:
                outside |= _refs(o)[0]
            for u in sorted(outside - set(inner)):
                if u not in SA.cls_of and u not in gl:
                    inner.setdefault(u, None)
            mapping = {i: fresh.take() for i in inner}
            mapping.update({u: v for u, v in rmap.items() if u != v})
            if s_world is not None and world is not None:
                mapping[s_world] = world
            new_objs = []
            for j, (u, o) in enumerate(bundle):
                for e in _bundle_ents(o):           # its stored state matched its place: it must after the copy
                    if not _gsd_check(e):
                        gsd_ok.add(mapping[e.id])
                blockers.extend(_blockers(S, C, SA, o, D))
                n = copy.deepcopy(o)
                _renew(n, mapping)
                _move_tree(S, n, D, cell_ix)
                for e in _bundle_ents(n):
                    for c in _comps(S, e):
                        if "IsMerged" in c.fields:
                            c.fields["IsMerged"] = b"\x00"
                if j == 0:
                    if tpl.kind in ("artifact", "scoring_zone"):
                        _component(S, n, KINDS[tpl.kind]).fields["TeamOwner"] = struct.pack("<I", a["team"] - 1)
                    if tpl.kind == "domination_point":
                        _component(S, n, KINDS[tpl.kind]).fields["hudId"] = struct.pack("<I", a["hud"])
                layers = [modes[r]["layer"] for r in a["records"]] if a["records"] else []
                if layers and proto_action is None:
                    raise ValueError("no data-layer filter on this map to copy")
                _set_layers(n, layers, proto_action)
                for e in _members(n):
                    if isinstance(e.fields.get("DataLayerFilter"), Obj) and e.fields["DataLayerFilter"].fields.get(
                            "LayerActions"):
                        _set_layers(e, layers, proto_action)
                cname = SA.cls_of[u]
                nm = a["name"] if j == 0 else f"{a['name']}_{SA.body(u)['name'] or 'part'}"
                new_objs.append((n.id, nm, cname, n, layers))
            # bodies
            rchg = {u: v for u, v in rmap.items() if u != v}
            for ti in targets:
                for nid, nm, cname, n, layers in new_objs:
                    body = C.encode(Root(b"", 0, n))
                    adds_in[ti].append(("ent", nid, duplicate.entity_record_bytes(cls[cname], nm, body), layers))
                for u, c in resources.items():
                    nid = rmap[u]
                    if nid in stored[ti]:
                        continue
                    stored[ti].add(nid)
                    so = SA.body(u)
                    body = so["body"]
                    if any(v in body[6:] for v in rchg):
                        if c in RAW_SCAN or c in NO_REFS:
                            bb = bytearray(body)
                            for old, new in rchg.items():
                                i = bb.find(old, 6)
                                while i >= 0:
                                    bb[i:i + 4] = new
                                    i = bb.find(old, i + 4)
                            body = bytes(bb)
                        else:
                            root = C.decode(body)
                            _renew(root.obj, rchg)
                            body = C.encode(root)
                    if nid != u:
                        body = body[:2] + nid + body[6:]
                    adds_in[ti].append(("res", nid, M.mapwalk.object_bytes(so, body), None))
            if tpl.kind == "hiding_spot":
                marker_adds.append((new_objs[0][0], m_new, s_markers.get(tpl.uid)))
            made[k] = (dict(name=a["name"], uid=new_objs[0][0], kind=tpl.kind, template=tpl.key,
                             records=[A.name_of(t) for t in targets], companions=[x[0] for x in new_objs[1:]],
                             needs=sorted(set(rmap.values())), resources=len(resources), src_world=s_world,
                             transplanted=sum(1 for u in resources if u not in A.where),
                             old_ids=set(inner) | ({s_world} if s_world not in (None, world) else set())))
        if not same:
            del SA

    # 2. moves (removes are moves 150 m down): every entity of the object, its companions, merged trees
    moving = {u: D for u, (D, _m) in p["moves"].items()}
    moving.update({u: D for u, D in p["follow"].items() if u not in moving})
    moved_ids = {}
    for u in moving:
        th = info["things"].get(u)
        moved_ids[u] = [u] + (th.members if th else [])
    remerge = set()
    multi_info = {}
    if moving:
        all_moved = {e for ids in moved_ids.values() for e in ids}
        for r in A.recs:
            for k, (off, size, _s, _f) in enumerate(A.walked[r.index][0]):
                o = A.obj(r.index, k)
                if o["type"] != cls["Entity"] or o["body"][:2] != M.UID_MARK:
                    continue
                if not any(e in o["body"] for e in all_moved):
                    continue
                try:
                    ent = C.decode(o["body"]).obj
                except M.DecodeError:
                    continue
                mf = collision._multi_fields(S, ent)
                if mf is not None and all_moved & {x.id for x in mf["ContainedEntities"]}:
                    remerge.add(ent.id)
    if remerge:
        # every member's matrix and every member shape's local box
        mats, boxes = {}, {}
        need_shapes = set()
        for r in A.recs:
            for k, (off, size, _s, _f) in enumerate(A.walked[r.index][0]):
                o = A.obj(r.index, k)
                if o["type"] in (cls["Entity"], cls["EntityGroup"]) and o["body"][:2] == M.UID_MARK:
                    try:
                        ent = C.decode(o["body"]).obj
                    except M.DecodeError:
                        continue
                    for e in _bundle_ents(ent):
                        mats.setdefault(e.id, collision._matrix(e.fields))
                    if ent.id in remerge:
                        mf = collision._multi_fields(S, ent)
                        need_shapes |= {M._link(x) for x in mf["ContainedShapes"]}
        for u in need_shapes:
            if u in A.where:
                try:
                    v = collision._shape_arrays(C.decode(A.body(u)["body"]).obj.fields)[0]
                    boxes[u] = (v.min(0), v.max(0))
                except Exception as e:  # noqa: BLE001
                    raise ValueError(f"merged member shape {u.hex()} can't be read ({e})") from None
        for u, ids in moved_ids.items():
            for e in ids:
                if e in mats:
                    mats[e] = mats[e] @ moving[u]
        multi_info = dict(mats=mats, boxes=boxes)

    new_body = {}                              # uid -> (old body, new body) of a moved object, the same in every record

    def moved_body(o):
        u = o["body"][2:6]
        if u not in new_body:
            root = C.decode(o["body"])
            gsd_ok.update(e.id for e in _bundle_ents(root.obj) if not _gsd_check(e))
            if u not in p["removes"]:
                blockers.extend(_blockers(S, C, A, root.obj, moving[u]))
            _move_tree(S, root.obj, moving[u])
            new_body[u] = (o["body"], C.encode(root))
        elif new_body[u][0] != o["body"]:
            raise ValueError(f"{o['name']}: its blocks hold different bytes - not moved")
        return new_body[u][1]

    def retree(o):
        root = C.decode(o["body"])
        mf = collision._multi_fields(S, root.obj)
        members = [x.id for x in mf["ContainedEntities"]]
        shapes = [M._link(x) for x in mf["ContainedShapes"]]
        bx = [mopp.member_box(*multi_info["boxes"][s], multi_info["mats"][e]) for e, s in zip(members, shapes)]
        code, (ox, oy, oz, sc) = mopp.build_boxes([b[0] for b in bx], [b[1] for b in bx])
        mf["MoppCode"] = [code[i:i + 1] for i in range(len(code))]
        mf["MoppOffsetAndScale"] = struct.pack("<4f", ox, oy, oz, sc)
        return C.encode(root)

    # 3. MapWorldInfo: markers for new hiding spots, moved ones follow, removed ones go
    marker_body = None
    mwi = A.first("MapWorldInfo")
    hay_moves = {u: m for u, (D, m) in p["moves"].items() if info["things"][u].kind == "hiding_spot"}
    if mwi is not None and (marker_adds or hay_moves):
        root = C.decode(A.body(mwi)["body"])
        lst = root.obj.fields["PermanentMarkers"]
        keep = []
        proto = proto_any = None
        for x in lst:
            mk = M._obj(x)
            key = M._obj(mk.fields["Key"]).fields["Entity"].id if mk is not None else None
            if mk is not None and mk.fields["MarkerType"] == struct.pack("<I", HAY_MARKER):
                proto = proto or x
            if mk is not None and key is not None:
                proto_any = proto_any or x
            if key in p["removes"]:
                continue
            if key in hay_moves:
                pos = mk.fields["MarkerPosition"]
                mk.fields["MarkerPosition"] = struct.pack("<3f", *hay_moves[key][3, :3]) + pos[12:]
            keep.append(x)
        for nid, m, smk in marker_adds:
            if (proto or proto_any) is None:
                log("  note: this map lists no map marker to copy - new hiding spots get none")
                break
            x = copy.deepcopy(proto or proto_any)
            mk = M._obj(x)
            mk.fields["MarkerType"] = smk.fields["MarkerType"] if smk is not None else struct.pack("<I", HAY_MARKER)
            ids = duplicate.object_ids(mk)
            mp = {i: fresh.take() for i in ids}
            _renew(mk, mp)
            M._obj(mk.fields["Key"]).fields["Entity"].id = nid
            mk.fields["MarkerPosition"] = struct.pack("<3f", *m[3, :3]) + mk.fields["MarkerPosition"][12:]
            keep.append(x)
        root.obj.fields["PermanentMarkers"] = keep
        marker_body = C.encode(root)
    elif marker_adds:
        log("  note: this map has no MapWorldInfo - new hiding spots get no map marker")

    # 4. rewrite the records
    changed = set()
    for rec in A.recs:
        ri = rec.index
        adding = adds_in.get(ri, [])
        d = A.payloads[ri]
        entries, table = A.walked[ri]
        touched = bool(adding)
        troot = spawns.decode_table(C, table) if any(x[3] for x in adding) else None
        table_changed = False
        out, last_entity, gcdb_at, dropped = [], -1, None, []
        for off, size, serial, filt in entries:
            o = object_at(d, off, size)
            body = o["body"]
            u = body[2:6] if body[:2] == M.UID_MARK else None
            filt = None if filt == M.mapwalk.NO_FILTER else filt
            if o["type"] in (cls["Entity"], cls["EntityGroup"]) and u in p.get("deletes", ()):
                dropped.append(u)                  # (a blend group taken out)
                touched = True
                continue
            if o["type"] in (cls["Entity"], cls["EntityGroup"]) and u in moving:
                body = moved_body(o)
                touched = True
            elif o["type"] == cls["Entity"] and u in remerge:
                body = retree(o)
                touched = True
            elif u is not None and u == mwi and marker_body is not None:
                body = marker_body
                touched = True
            if o["type"] == cls["GridCellDataBlock"]:
                gcdb_at = len(out)
            if o["type"] in (cls["Entity"], cls["EntityGroup"]):
                last_entity = len(out)
            out.append([serial, M.mapwalk.object_bytes(o, body) if body is not o["body"] else
                        M.mapwalk.object_bytes(o), filt, o])
        if adding:
            if gcdb_at is None:
                raise ValueError(f"{rec.name} has no GridCellDataBlock - objects can't be added to it")
            ents = [(nid, data, lay) for what, nid, data, lay in adding if what == "ent"]
            ress = [(nid, data) for what, nid, data, _l in adding if what == "res"]
            ins = []
            for nid, data, lay in ents:
                filt = None
                if lay:
                    filt, order, tc = spawns._filter_index(C, troot, lay)
                    table_changed |= tc
                    if order != lay:      # the directory filter's order: the entity's own filter in the same order
                        data = _reorder_layers(C, cls, data, order, proto_action)
                ins.append([struct.unpack("<I", nid)[0], data, filt, None])
            out[last_entity + 1:last_entity + 1] = ins
            out.extend([struct.unpack("<I", nid)[0], data, None, None] for nid, data in ress)
        if adding or dropped:
            gi = next((i for i, x in enumerate(out) if x[3] is not None and x[3]["type"] == cls["GridCellDataBlock"]),
                      None)
            if gi is None:
                raise ValueError(f"{rec.name} has no GridCellDataBlock - objects can't be taken out of it")
            g = out[gi][3]
            gb = g["body"]
            if adding:
                gb = _relist(C, gb, [n for n, _d, _l in ents], [n for n, _d in ress])
            if dropped:
                gb = _unlist(C, gb, dropped)
            out[gi][1] = M.mapwalk.object_bytes(g, gb)
        if not touched:
            continue
        new_table = C.encode(troot)[2:] if table_changed else table
        nd = M.mapwalk.rebuild([(s, b, f) for s, b, f, _o in out], new_table)
        pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
        assert decompress(pl) == nd
        set_payload(A.image, ri, pl)
        changed.add(ri)
    write(dest, A.image)
    rep = dict(made=[made[k] for k in sorted(made)], moved=[u for u in p["moves"] if u not in p["removes"]],
               removed=list(p["removes"]),
               followed=list(p["follow"]), retreed=sorted(x.hex() for x in remerge),
               records=sorted(A.name_of(i) for i in changed),
               blockers=np.concatenate(blockers) if blockers else np.zeros((0, 3, 3)))
    rep["fails"] = verify(src, dest, info, p, rep, A, changed, gsd_ok, log)
    rep["ok"] = not rep["fails"]
    return rep


def _reorder_layers(C, cls, data, order, proto):
    """An object's record bytes with its own filter's layers in `order`."""
    type_id, _bs, nl = struct.unpack_from("<III", data, 0)
    name, body = data[12:12 + nl], data[12 + nl:]
    root = C.decode(body)
    _set_layers(root.obj, order, proto)
    return duplicate.entity_record_bytes(type_id, name.decode("latin-1"), C.encode(root))


def _relist(C, gcdb_body, new_ents, new_res):
    root = C.decode(gcdb_body)
    f = root.obj.fields
    n = struct.unpack("<I", f["NumberOfObjectsToActivate"])[0]
    lst = f["Objects"]
    tag, extra = (lst[0].tag, lst[0].extra) if lst else (1, 0)
    have = {x.id for x in lst}
    f["Objects"] = lst[:n] + [Ref(tag, extra, u) for u in new_ents] + lst[n:] + \
        [Ref(tag, extra, u) for u in dict.fromkeys(new_res) if u not in have]
    f["NumberOfObjectsToActivate"] = struct.pack("<I", n + len(new_ents))
    return C.encode(root)


def _unlist(C, gcdb_body, gone):
    """A cell's GridCellDataBlock without the entities taken out of it (its world part one shorter for each)."""
    root = C.decode(gcdb_body)
    f = root.obj.fields
    n = struct.unpack("<I", f["NumberOfObjectsToActivate"])[0]
    lst = f["Objects"]
    gone = set(gone)
    f["Objects"] = [x for x in lst if x.id not in gone]
    f["NumberOfObjectsToActivate"] = struct.pack("<I", n - sum(1 for x in lst[:n] if x.id in gone))
    return C.encode(root)


# --------------------------------------------------------------------------------------------------------- read back

def verify(src, dest, info, p, rep, A, changed, gsd_ok=(), log=print):
    """dest read back: other records byte-identical; in rewritten records every object we didn't mean to touch
    byte-identical; each new object defined once per target record, in its world part, with its directory filter =
    its own filter; every resource and companion it needs in each of its records; its stored state (GameStateData)
    at its place and naming none of the template's ids; re-treed merged trees reaching every member; the map read
    again: every new object of its kind / team / id where planned, moved ones at their new place, sunk ones 150 m
    down, hiding spots' markers."""
    S, C = A.S, A.C
    cls = {S.name_of(h): h for h in S.types_by_hash}
    fails = []
    new_ids = {m["uid"] for m in rep["made"]} | {c for m in rep["made"] for c in m["companions"]}
    edited = set(p["moves"]) | set(p["follow"]) | {u for x in rep["retreed"] for u in [bytes.fromhex(x)]}
    defined = collections.Counter()
    with M.Forge(dest) as f:
        for r in f.records:
            if r.name == "GlobalMetaFile" or len(A.raw.get(r.index, b"")) <= 8:
                continue
            new = f.read(r)
            if r.index not in changed:
                if new != A.raw[r.index]:
                    fails.append(f"{r.name} changed")
                continue
            d1 = decompress(M.mapwalk.split_prefix(new)[1])
            e1, t1 = M.mapwalk.walk(d1)
            sets = spawns.table_sets(C, t1)
            old = {}
            for off, size, serial, _fl in A.walked[r.index][0]:
                o = object_at(A.payloads[r.index], off, size)
                old[(serial, o["name"])] = o["body"]
            listed, n_act, here = None, 0, set()
            for off, size, serial, filt in e1:
                o = object_at(d1, off, size)
                b = o["body"]
                u = b[2:6] if b[:2] == M.UID_MARK else None
                here.add(u)
                if o["type"] == cls["GridCellDataBlock"]:
                    g = C.decode(b).obj.fields
                    listed = [x.id for x in g["Objects"]]
                    n_act = struct.unpack("<I", g["NumberOfObjectsToActivate"])[0]
                    continue
                if u in new_ids:
                    defined[(u, r.name)] += 1
                    root = C.decode(b)
                    if C.encode(root) != b:
                        fails.append(f"{r.name}: {o['name']} doesn't re-encode")
                    lf = root.obj.fields.get("DataLayerFilter")
                    own = [a.fields["Layer"].id for a in lf.fields["LayerActions"]] if isinstance(lf, Obj) else []
                    dl = [] if filt == M.mapwalk.NO_FILTER else sets[filt]
                    if set(own) != set(dl):
                        fails.append(f"{r.name}: {o['name']}: directory filter != its own")
                    continue
                if (serial, o["name"]) in old and old[(serial, o["name"])] == b:
                    continue
                if u in edited or o["type"] == cls["MapWorldInfo"] or (serial, o["name"]) not in old:
                    continue                    # edited, or a resource copy (checked below)
                fails.append(f"{r.name}: {o['name']} changed")
            dels = set(p.get("deletes", ()))
            if dels & here:
                fails.append(f"{r.name}: still holds a blend group taken out")
            if listed is not None and dels & set(listed):
                fails.append(f"{r.name}: still lists a blend group taken out")
            if listed is not None:
                world_part = set(listed[:n_act])
                for u in new_ids & here:
                    if u not in world_part:
                        fails.append(f"{r.name}: new {u.hex()} not in the block's world part")
                if world_part & new_ids - here:
                    fails.append(f"{r.name}: lists new objects it doesn't hold")
    for m in rep["made"]:
        for rn in m["records"]:
            for u in [m["uid"]] + m["companions"]:
                if defined[(u, rn)] != 1:
                    fails.append(f"{m['name']}: {u.hex()} defined {defined[(u, rn)]} times in {rn}")
    # every resource a new object needs (its closure in the source map) is in each record it went to, and the
    # entities it names are there too; no new object names its template's ids; stored states; merged trees
    if rep["made"] or p["moves"]:
        B = _Archive(dest)

        def decoded(u):
            ri, k = B.where[u][0]
            return C.decode(B.obj(ri, k)["body"]).obj
        for m in rep["made"]:
            for x in m["needs"] + m["companions"]:
                held = {B.name_of(i) for i, _k in B.where.get(x, [])}
                if not set(m["records"]) <= held:
                    fails.append(f"{m['name']}: {x.hex()} ({B.cls_of.get(x, 'undefined')}) not in "
                                 f"{sorted(set(m['records']) - held)}")
            for u in [m["uid"]] + m["companions"]:
                o = decoded(u)
                names, _d = _refs(o)
                if names & m["old_ids"]:
                    old = sorted(x.hex() for x in names & m["old_ids"])[:3]
                    fails.append(f"{m['name']}: names its template's {old}")
                for e in _bundle_ents(o):
                    for msg in _gsd_check(e, m["old_ids"]):
                        if e.id in gsd_ok or "names" in msg:
                            fails.append(f"{m['name']}: {e.id.hex()}: {msg}")
        for u in set(p["moves"]) | set(p["follow"]):
            for e in _bundle_ents(decoded(u)):
                if e.id in gsd_ok:
                    fails.extend(f"{info['things'].get(u, u)}: {e.id.hex()}: {msg}" for msg in _gsd_check(e))
        for x in rep["retreed"]:
            mf = collision._multi_fields(S, decoded(bytes.fromhex(x)))
            reached, _h, _s = mopp.walk(b"".join(mf["MoppCode"]))
            if sorted(t for t, _p in reached) != list(range(len(mf["ContainedEntities"]))):
                fails.append(f"merged tree {x}: doesn't reach every member")
        del B
    after = read(dest)
    for k, a in enumerate(p["adds"]):
        m = rep["made"][k]
        th = after["things"].get(m["uid"])
        if th is None:
            fails.append(f"{m['name']}: not found as a {a['template'].kind}")
            continue
        if th.kind != a["template"].kind or not np.allclose(th.matrix, a["matrix"], atol=1e-3):
            fails.append(f"{m['name']}: {th.kind} at {np.round(th.matrix[3, :3], 2)} (planned "
                         f"{np.round(a['matrix'][3, :3], 2)})")
        if a["records"] and sorted(th.modes(after)) != sorted(a["records"]):
            fails.append(f"{m['name']}: created in {sorted(th.modes(after))}, planned {sorted(a['records'])}")
        if th.kind in ("artifact", "scoring_zone") and th.team != a["team"]:
            fails.append(f"{m['name']}: team {th.team}, planned {a['team']}")
        if th.kind == "domination_point" and th.hud != a["hud"]:
            fails.append(f"{m['name']}: point id {th.hud}, planned {a['hud']}")
        if th.kind == "hiding_spot" and info["markers"] and m["uid"] not in after["markers"]:
            fails.append(f"{m['name']}: no map marker")
    for u, (D, m1) in p["moves"].items():
        th = after["things"].get(u)
        if th is None or not np.allclose(th.matrix, m1, atol=1e-3):
            fails.append(f"{info['things'][u].name}: not at its planned place")
        if info["things"][u].kind == "hiding_spot" and u in info["markers"]:
            if u in p["removes"]:
                if u in after["markers"]:
                    fails.append(f"{info['things'][u].name}: removed but still on the map")
            elif u not in after["markers"] or not np.allclose(after["markers"][u], m1[3, :3], atol=1e-3):
                fails.append(f"{info['things'][u].name}: its map marker didn't follow")
    for u in p.get("deletes", ()):
        if u in after["things"]:
            fails.append(f"{info['things'][u].name}: taken out but still on the map")
    for k, a in enumerate(p["adds"]):
        th = after["things"].get(rep["made"][k]["uid"])
        if a["template"].kind == "blend_group" and th is not None and th.slots != a["template"].slots:
            fails.append(f"{rep['made'][k]['name']}: its NPC slots aren't its template's")
    for u, th in info["things"].items():
        if u not in p["moves"] and u not in p["follow"] and u in after["things"] and \
                not np.allclose(after["things"][u].matrix, th.matrix, atol=1e-5):
            fails.append(f"{th.name} moved but wasn't meant to")
    for msg in fails[:20]:
        log(f"  READ-BACK: {msg}")
    log(f"{dest}: gameplay objects - {len(rep['made'])} new, {len(rep['moved'])} moved, {len(rep['removed'])} "
        f"removed; {len(rep['retreed'])} merged trees re-treed; {len(changed)} records rewritten; read back: "
        f"{'yes' if not fails else 'NO'}")
    return fails


# ------------------------------------------------------------------------------------------------------------ build

def build(src, dest, add=(), move=None, remove=(), zones=None, navmesh=False, pack=True, dry_run=False, ground=True,
          log=print, force=False):
    """Plan, check and write a map with gameplay objects added / moved / removed (module docstring); then Domination
    zones (zones="follow": each moved point's zone moves with it; or {zone uid: [polygons]}); then, with navmesh=True,
    the navmesh cut under the new / moved objects' collision (navcut.apply: crowds stop walking through them), with
    navmesh="generate" the navmesh of the cells they touch made again from the collision (navgen); then LZO-pack.
    Refuses on errors unless force. -> report dict (ok, errors, warnings, made, moved, removed, blockers, ...)."""
    from . import boundary, territories
    info = read(src)
    p = plan(info, add, move, remove)
    areas = boundary.areas(src)
    zpolys = zone_moves(info, p, areas) if zones == "follow" else dict(zones or {})
    gr = None
    if ground:
        from . import ground as G
        gr = G.Ground(M.load(src, log=lambda *_a: None))
    lifts = any(a["template"].kind == "lift" for a in p["adds"]) or \
        any(info["things"][u].kind == "lift" for u in p["moves"] if u not in p["removes"])
    edges = climb_edges(src) if ground and lifts else None
    errors, warnings = problems(info, p, areas=areas, pairs=territories.pairs(src, areas), ground=gr, zones=zpolys,
                                edges=edges)
    for e in errors:
        log(f"  ERROR: {e}")
    for w in warnings:
        log(f"  warning: {w}")
    rep = dict(ok=False, errors=errors, warnings=warnings, plan=p, zones=zpolys)
    if dry_run or (errors and not force):
        if errors and not dry_run:
            log("  not built (errors above)")
        return rep
    # the steps after the objects (zones, navmesh) each read the previous step's file
    steps = []
    if zpolys:
        steps.append(("zones", lambda a, b: boundary.rebuild(a, b, zpolys, log=log)))
    if navmesh:
        def cut(a, b):
            from . import navcut
            if not len(rep["blockers"]):
                import shutil
                shutil.copyfile(a, b)
                return True
            if navmesh == "generate":
                from . import navgen
                r = navgen.regenerate_under(a, b, rep["blockers"], log=log)
            else:
                r = navcut.apply(a, b, rep["blockers"], log=log)
            rep["navmesh"] = r
            return bool(r.get("ok", True))
        steps.append(("navmesh", cut))
    cur = dest + ".gameplay" if steps else dest
    rep.update(apply(src, cur, info, p, log=log))
    for k, (what, fn) in enumerate(steps):
        nxt = dest if k == len(steps) - 1 else f"{dest}.{what}"
        if rep["ok"]:
            rep["ok"] = bool(fn(cur, nxt))
        else:
            os.replace(cur, nxt)
        if os.path.exists(cur) and cur != dest:
            os.remove(cur)
        cur = nxt
    if pack and rep["ok"]:
        from . import archive
        archive.pack(dest, src, log=log)
    return rep


def main(argv):
    from ac3editor import gameplay as G         # (the cached catalog pickles ac3editor.gameplay.Thing, not __main__'s)
    if argv[:1] == ["--catalog"]:
        for th in G.catalog(argv[1] if len(argv) > 1 else None):
            print(th)
        return
    info = G.read(argv[0])
    for line in G.summary(info):
        print(line)


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
