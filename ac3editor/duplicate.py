"""Copying a map object (an Entity, top-level or inside an EntityGroup) to a new place.

A copy is a new top-level Entity in the source's record:

* a deep copy of the decoded source, with a FRESH id for the entity and for every object nested in it (its
  components and their parts: inner ids shared with the source break the game's object lookups), and every
  reference inside it to one of those ids remapped; references outward (its Mesh, LODSelector, MeshShape,
  materials, sounds) stay shared, as the stock game shares them between instances;
* its GlobalMatrix set, its BoundingVolume re-derived (collision.moved_bv), and its InertComponent's IsMerged set to
  0 - its collision stands on its own rigid body (the non-merged route), not in the cell's merged tree;
* listed in the record's GridCellDataBlock: `Objects` holds the cell's world objects first (Entities, groups, crowd
  regions - the first `NumberOfObjectsToActivate` entries) then its resources (meshes, textures...); the copy goes in
  at position N and N grows by one. A record without one (the World, a game-mode DataBlock) can't take copies yet.

Fresh ids avoid every id the map archive and the game's DataPC / DataPC_extra use (identities and inline object
ids) and the range the game's handle manager never maps (0xF0000000 and up); they are
drawn from a generator seeded by the archive and the copy, so the same scene builds the same ids.
"""
import copy
import hashlib
import os
import random
import struct

from ac3codec import Handle, Obj, Ptr, Ref, Root

from . import mapdata as M

LOW, HIGH = 0x10000000, 0xF0000000


def walk_values(v, fn):
    """Calls fn on every Obj / Ptr / Ref / Handle in a decoded value tree."""
    if isinstance(v, Obj):
        fn(v)
        for x in v.fields.values():
            walk_values(x, fn)
        for _n, _o, _b, val in v.dyn or []:
            walk_values(val, fn)
    elif isinstance(v, (Ptr, Ref)):
        fn(v)
        if v.obj is not None:
            walk_values(v.obj, fn)
    elif isinstance(v, Handle):
        fn(v)
    elif isinstance(v, list):
        # an array decodes to one element kind (fastload._value): a list starting with bytes is a primitive array
        # (vertex / index buffers, MOPP codes - 62M of Boston Harbour's 66M values), nothing in it to visit
        if v and type(v[0]) is bytes:
            return
        for x in v:
            walk_values(x, fn)


def object_ids(obj):
    out = []
    walk_values(obj, lambda x: out.append(x.id) if isinstance(x, Obj) and x.id and x.id != bytes(4) else None)
    return out


class FreshIds:
    def __init__(self, used, seed, copy=True):
        # copy=False: take from (and add to) the caller's own set (add_models: one generator per model, ~0.5M ids)
        self.used = set(used) if copy else used
        self.rng = random.Random(seed)

    def take(self):
        while True:
            u = self.rng.randrange(LOW, HIGH)
            b = struct.pack("<I", u)
            if u not in self.used and (u & 0xFFFF) and b not in self.used:
                self.used.add(u)
                self.used.add(b)
                return b


def used_ids(archive_objects, extra_archives=()):
    """Every id in use: identities (body +2) and inline object ids of the given decoded / raw objects, plus every
    id forgelib's census finds in the extra archives (DataPC, DataPC_extra). Ints and 4-byte strings both."""
    used = set()
    for ident, obj in archive_objects:
        if ident:
            used.add(ident)
        if obj is not None:
            used.update(object_ids(obj))
    for arch in extra_archives:
        if os.path.exists(arch):
            used.update(archive_ids(arch))
    return used | {struct.unpack("<I", u)[0] for u in used if isinstance(u, bytes)}


_ARCHIVE_IDS = {}


def archive_ids(arch):
    """The ids forgelib's census finds in an archive (DataPC: 427k), as ints - kept per path + size + mtime in this
    process and in <cache>/archive_ids (the census is ~9 s an archive, and a big build asks for it several times:
    collision.edit_shapes, materials.apply, add_models)."""
    import numpy as np
    st = os.stat(arch)
    key = f"{os.path.abspath(arch).lower()}|{st.st_size}|{st.st_mtime_ns}"
    if key in _ARCHIVE_IDS:
        return _ARCHIVE_IDS[key]
    path = os.path.join(M.CACHE, "archive_ids", hashlib.sha1(key.encode()).hexdigest()[:20] + ".npy")
    ids = None
    if os.path.exists(path):
        try:
            ids = np.load(path)
        except Exception:  # noqa: BLE001 - a damaged cache file is only a cold cache
            ids = None
    if ids is None:
        import id_census  # forgelib
        found = set()
        for _r, items in id_census.census(arch):
            for ident, _t, _d, _n, inline in items:
                if ident is not None:
                    found.add(ident)
                found.update(u for u, _c in inline)
        ids = np.array(sorted(found), np.uint32)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp.npy"        # atomic: two builds at once at worst both compute it
        np.save(tmp, ids)
        try:
            os.replace(tmp, path)
        except OSError:
            os.remove(tmp)
    _ARCHIVE_IDS[key] = out = frozenset(ids.tolist())
    return out


def make_copy(src, matrix, bv, fresh):
    """-> (new Obj, its id): a deep copy of decoded Entity `src` with fresh ids, `matrix` (16 floats / 4x4, AC3
    order) and BoundingVolume `bv` (lo, hi)."""
    import numpy as np
    new = copy.deepcopy(src)
    mapping = {i: fresh.take() for i in dict.fromkeys(object_ids(new))}

    def renew(x):
        if isinstance(x, Obj):
            x.id = mapping.get(x.id, x.id)
        elif isinstance(x, Ref):
            x.id = mapping.get(x.id, x.id)
        elif isinstance(x, Ptr) and x.link is not None:
            x.link = mapping.get(x.link, x.link)
        elif isinstance(x, Handle):
            x.id = mapping.get(x.id, x.id)

    walk_values(new, renew)
    new.flag = 1
    new.fields["GlobalMatrix"] = np.asarray(matrix, dtype="<f4").reshape(-1).tobytes()
    b = new.fields["BoundingVolume"].fields
    b["Min"], b["Max"] = struct.pack("<3f", *bv[0]), struct.pack("<3f", *bv[1])
    for p in new.fields.get("Components", []):
        c = M._obj(p)
        if c is not None and "IsMerged" in c.fields:
            c.fields["IsMerged"] = b"\x00"
    return new, new.id


def seed(archive, src_id, k):
    return int.from_bytes(hashlib.sha1(f"{os.path.basename(archive)}|{src_id.hex()}|{k}".encode()).digest()[:8], "big")


def encode_entity(C, obj):
    return C.encode(Root(b"", 0, obj))


def entity_record_bytes(type_id, name, body):
    """An object as it sits in a payload: u32 type, u32 body size (len - 1 for uid-bearing classes, as every stock
    one - as forgelib's mapwalk.object_bytes), u32 name length, name, body."""
    nm = name.encode("latin-1")
    return struct.pack("<III", type_id, len(body) - 1, len(nm)) + nm + body


def list_in_cell(C, gcdb_body, new_ids):
    """GridCellDataBlock body -> new body with `new_ids` inserted at the end of its activated block."""
    root = C.decode(gcdb_body)
    f = root.obj.fields
    n = struct.unpack("<I", f["NumberOfObjectsToActivate"])[0]
    lst = f["Objects"]
    tag, extra = (lst[0].tag, lst[0].extra) if lst else (1, 0)
    f["Objects"] = lst[:n] + [Ref(tag, extra, u) for u in new_ids] + lst[n:]
    f["NumberOfObjectsToActivate"] = struct.pack("<I", n + len(new_ids))
    return C.encode(root)
