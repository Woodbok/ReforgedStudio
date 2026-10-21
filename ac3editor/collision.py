"""Writing collision into a map archive. Each edit names an entity and changes its InertComponent's MeshShape:

* ("add", world_verts, tris): add world-space triangles (keeping the shape's own); the entity's BoundingVolume is
  widened to cover them. The BoundingVolume is a WORLD-axis box of offsets from the entity's position (not a local
  box): on Fort Wolcott's rotated entities it matches the LOD0 mesh exactly read that way in 77 cases, read locally
  in 38 (43 fit only the world-axis reading, 4 only the local one). A box widened locally on a rotated entity misses
  the new triangles and they never collide; on an unrotated one both readings agree.
* ("sink", dz): move every vertex of the shape by dz metres in Z (a knock-out probe: the object's collision leaves).
* ("move", matrix): give the entity (top-level or inside an EntityGroup) a new GlobalMatrix (4 x 4, AC3 row-vector
  order). Its own visual, collision and climb edges are stored relative to it and come along; its BoundingVolume
  (type 0: world-axis offsets) is re-derived for the new rotation, its group's BoundingVolume (type 2: a box in the
  GROUP's own frame - the three Wolcott ships share one although each is turned differently) grows to cover it, and
  its cell's merged tree is re-treed with its new member box. No scale changes (Havok shapes don't scale).
* ("remove",): a move SINK_DEPTH (150 m) straight down - other data (cell lists, navmesh, merged members) keeps
  referring to it. A MOVING body (a chase-breaker door, a lift) sunk much deeper (1 km) is outside the Havok world,
  drops out of it, and leaving the match removes it again - a crash at AC3MP+0xB76C53 (hkpRigidBody with no world).
  150 m is under any ground and inside the world.
* ("copy", matrix): a new top-level Entity copied from this one (top-level or inside a group), at `matrix`, in the
  source's record and listed in its GridCellDataBlock (ac3editor.duplicate). `id_avoid` adds archives whose ids
  the fresh ids must also avoid (the game's DataPC / DataPC_extra).

Every edited shape gets a fresh MOPP tree (ac3editor.mopp.build), local AABB and per-triangle material. Every copy
of a shape / entity (the same uid in several records) gets the same edit. Triangles must be wound outward,
right-handed, like AC3's own (an inside-out face is passed through from its back).

**Merged collision.** Most static objects (InertComponent IsMerged = 1) are also members of their cell's merged
shape: an unnamed Entity whose component's Shape is a MultiMeshShape (ContainedEntities handles, ContainedShapes
refs, one MOPP). That tree's leaves are the MEMBERS, over their world boxes (mopp.member_box); each member's own
tree then finds its triangles. A member grown beyond its old box is never asked there (a ramp added to a merged
object does nothing; on a non-merged one it works). So every merged shape holding an edited entity is re-treed here
(mopp.build_boxes).
"""
import struct

import numpy as np

from . import mapdata as M
from . import mopp
from datafile import compress, decompress  # noqa: E402  (forgelib)
from forge_write import ArchiveImage, set_payload, write  # noqa: E402  (forgelib)
from transplant import object_at  # noqa: E402  (forgelib)


SINK_DEPTH = 150.0      # "remove": metres straight down (inside the Havok world; see the docstring)

def _matrix(entity_fields):
    return np.array(struct.unpack("<16f", entity_fields["GlobalMatrix"]), dtype=np.float64).reshape(4, 4)


def to_local(world_pts, m):
    """AC3 matrices are row-vector (p_world = p_local . M): p_local = (p_world - T) . R^-1."""
    R, T = m[:3, :3], m[3, :3]
    return (np.asarray(world_pts, dtype=np.float64) - T) @ np.linalg.inv(R)


def closed(tris):
    """True when every edge is shared by exactly two triangles (a solid with an inside)."""
    from collections import Counter
    edges = Counter()
    for a, b, c in np.asarray(tris):
        for e in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted((int(e[0]), int(e[1]))))] += 1
    return bool(edges) and all(n == 2 for n in edges.values())


def outward(verts, tris):
    """True when a closed solid is wound outward, right-handed (positive signed volume)."""
    v = np.asarray(verts, dtype=np.float64)
    t = np.asarray(tris)
    return np.einsum("ij,ij->i", v[t[:, 0]], np.cross(v[t[:, 1]], v[t[:, 2]])).sum() / 6 > 0


def _shape_arrays(fields):
    v = np.frombuffer(b"".join(fields["Vertices"]), "<f4").reshape(-1, 3).astype(np.float64)
    t = np.frombuffer(b"".join(fields["IndicesTriangles"]), "<u2").reshape(-1, 3).astype(np.int64)
    mats = bytes(b"".join(fields["IndicesMaterial"]))
    return v, t, mats


def _set_shape(fields, v, t, mats):
    if len(v) > 65535:
        raise ValueError("a MeshShape holds at most 65535 vertices")
    v = np.asarray(v, dtype=np.float32)
    code, (ox, oy, oz, sc) = mopp.build(v, t)
    fields["Vertices"] = [v[i].tobytes() for i in range(len(v))]
    fields["IndicesTriangles"] = [struct.pack("<H", int(x)) for x in np.asarray(t).reshape(-1)]
    fields["IndicesMaterial"] = [bytes([b]) for b in mats]
    fields["MoppCode"] = [code[i:i + 1] for i in range(len(code))]
    fields["MoppOffsetAndScale"] = struct.pack("<4f", ox, oy, oz, sc)
    lo, hi = v.min(0), v.max(0)
    fields["MinLocalAABBox"] = struct.pack("<4f", *lo, 1.0)
    fields["MaxLocalAABBox"] = struct.pack("<4f", *hi, 1.0)
    return f"{len(t)} triangles, tree {len(code)} bytes, local box {np.round(lo, 1)} .. {np.round(hi, 1)}"


def _bv(fields):
    b = fields["BoundingVolume"].fields
    return (np.array(struct.unpack("<3f", b["Min"])), np.array(struct.unpack("<3f", b["Max"])),
            struct.unpack("<I", b["Type"])[0])


def _set_bv(fields, lo, hi):
    b = fields["BoundingVolume"].fields
    b["Min"], b["Max"] = struct.pack("<3f", *lo), struct.pack("<3f", *hi)


def _corners(lo, hi):
    return np.array([(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


def moved_bv(lo, hi, m_old, m_new):
    """An entity's type-0 BoundingVolume (world-axis offsets) after its rotation changes: back to a local box through
    the old rotation, out through the new one (conservative; unchanged when only the position moves)."""
    R0, R1 = m_old[:3, :3], m_new[:3, :3]
    if np.allclose(R0, R1, atol=1e-6):
        return lo, hi
    local = _corners(lo, hi) @ np.linalg.inv(R0)
    w = _corners(local.min(0), local.max(0)) @ R1
    return w.min(0), w.max(0)


def _multi_fields(S, ent):
    """The MultiMeshShape fields of a cell's merged-collision entity (unnamed; a component's Shape), or None."""
    for p in ent.fields.get("Components", []):
        c = M._obj(p)
        if c is None:
            continue
        sh = c.fields.get("Shape")
        if isinstance(sh, M.Obj) and S.name_of(sh.type_hash) == "MultiMeshShape":
            return sh.fields
    return None


def edit_shapes(src, dest, edits, log=print, id_avoid=(), created=None):
    """Copy archive `src` to `dest` with `edits` [(entity name, op...)] applied (module docstring). -> ok."""
    S = M.schema()
    C = M.Codec(S)
    cls = {S.name_of(h): h for h in S.types_by_hash}
    bulk = {cls[n] for n in ("TextureMap", "Animation", "MaterialTemplate", "FX", "NavMeshManager") if n in cls}
    image = ArchiveImage.read(src)
    with M.Forge(src) as f:
        raw = {r.index: f.read(r) for r in f.records}
        recs = [r for r in f.records if r.name != "GlobalMetaFile" and len(raw[r.index]) > 8]   # empty records (some ported maps have one) carry no objects
    payloads = {}

    def payload(rec):
        if rec.index not in payloads:
            d = decompress(M.mapwalk.split_prefix(raw[rec.index])[1])
            payloads[rec.index] = (d, M.mapwalk.walk(d))
        return payloads[rec.index]

    # 1. every entity's matrix, every shape's local box, every merged shape's members; each edited entity's shape
    names = {e[0] for e in edits if e[1] != "copy"}
    found, matrices, shape_box, multis = {}, {}, {}, {}
    bvs, container, uid_of = {}, {}, {}
    raw_matrices, raw_new = {}, {}                  # undecodable entities (spawn points): matrix at body +10
    copy_src = {e[0] for e in edits if e[1] == "copy"}
    sources, where, idents = {}, {}, []              # copy sources: uid -> (Obj, record index, name)
    from . import duplicate
    for rec in recs:
        d, (entries, _t) = payload(rec)
        for off, size, _s, _f in entries:
            o = object_at(d, off, size)
            body = o["body"]
            uid_here = body[2:6] if body[:2] == M.UID_MARK else None
            if copy_src and o["type"] not in (cls["Entity"], cls["EntityGroup"], cls["MeshShape"]):
                dec = None
                if o["type"] not in bulk:
                    try:
                        dec = C.decode(body).obj
                    except Exception:  # noqa: BLE001 - id census only
                        dec = None
                idents.append((uid_here, dec))
            if o["type"] == cls["MeshShape"]:
                sh = C.decode(body).obj
                idents.append((uid_here, sh))
                v = _shape_arrays(sh.fields)[0]
                shape_box[body[2:6]] = (v.min(0), v.max(0))
                continue
            if o["type"] not in (cls["Entity"], cls["EntityGroup"]):
                continue
            try:
                ent = C.decode(o["body"]).obj
            except M.DecodeError:
                idents.append((uid_here, None))
                # an entity the codec can't finish (spawn points: a Scene's PlayerActor) can still MOVE: its
                # GlobalMatrix is the first field (body +10); see raw_moved
                if o["type"] == cls["Entity"] and len(body) >= 74 and uid_here is not None:
                    rm = np.frombuffer(body[10:74], "<f4").astype(np.float64).reshape(4, 4)
                    if np.isfinite(rm).all() and abs(rm[3, 3] - 1.0) < 1e-4:
                        raw_matrices[uid_here] = rm
                        uid_of.setdefault(o["name"], uid_here)
                continue
            if copy_src:
                idents.append((uid_here, ent))
            where[ent.id] = rec.index
            if ent.id in copy_src:
                sources[ent.id] = (ent, rec.index, o["name"])
            matrices[ent.id] = _matrix(ent.fields)
            bvs[ent.id] = _bv(ent.fields)
            uid_of.setdefault(o["name"], ent.id)
            for r in ent.fields.get("Entities", []):
                inner = M._obj(r)
                if inner is not None and "GlobalMatrix" in inner.fields:
                    matrices[inner.id] = _matrix(inner.fields)
                    bvs[inner.id] = _bv(inner.fields)
                    container[inner.id] = ent.id
                    where[inner.id] = rec.index
                    if inner.id in copy_src:
                        k = [M._obj(x) for x in ent.fields.get("Entities", [])].index(inner)
                        sources[inner.id] = (inner, rec.index, f"{o['name']}_{k}")
            if _multi_fields(S, ent) is not None:
                mf = _multi_fields(S, ent)
                multis[ent.id] = [x.id for x in mf["ContainedEntities"]]
            # an owner is named by its entity name (str) or uid (4 bytes); top-level Entities only
            key = o["name"] if o["name"] in names else ent.id if ent.id in names else None
            if o["type"] == cls["Entity"] and key is not None and key not in found:
                for p in ent.fields["Components"]:
                    c = M._obj(p)
                    if c is not None and S.name_of(c.type_hash) == "InertComponent":
                        found[key] = (_matrix(ent.fields), M._link(c.fields["RigidBody"].fields["Shape"]), ent.id)
    # moves: any decodable entity, by name or uid
    new_m = {}
    for name, op, *args in edits:
        if op not in ("move", "remove"):
            continue
        eid = name if isinstance(name, bytes) else uid_of.get(name)
        if eid not in matrices and eid in raw_matrices:
            m = raw_matrices[eid]
            m1 = m.copy() if op == "remove" else np.array(args[0], dtype=np.float64).reshape(4, 4)
            if op == "remove":
                m1[3, 2] -= SINK_DEPTH
            elif not np.allclose(np.linalg.norm(m1[:3, :3], axis=1), np.linalg.norm(m[:3, :3], axis=1), atol=1e-4):
                raise ValueError(f"{name!r}: scaling isn't supported")
            raw_new[eid] = m1
            continue
        if eid not in matrices:
            raise ValueError(f"no decodable entity {name!r} to move")
        m = matrices[eid]
        if op == "remove":
            m1 = m.copy()
            m1[3, 2] -= SINK_DEPTH
        else:
            m1 = np.array(args[0], dtype=np.float64).reshape(4, 4)
            if not np.allclose(np.linalg.norm(m1[:3, :3], axis=1), np.linalg.norm(m[:3, :3], axis=1), atol=1e-4):
                raise ValueError(f"{name!r}: scaling isn't supported (Havok collision doesn't scale)")
        new_m[eid] = m1
        found[name] = (m, found[name][1], eid) if name in found else (m, None, eid)
    new_bv = {e: moved_bv(bvs[e][0], bvs[e][1], matrices[e], m1) for e, m1 in new_m.items()}
    grow = {}                                   # group uid -> extra points in the group's own frame
    for e, m1 in new_m.items():
        g = container.get(e)
        if g is not None:
            lo, hi = new_bv[e]
            w = _corners(lo + m1[3, :3], hi + m1[3, :3])
            grow.setdefault(g, []).append((w - matrices[g][3, :3]) @ np.linalg.inv(matrices[g][:3, :3]))
    old_m = dict(matrices)
    matrices.update(new_m)

    # copies: built now (fresh ids), added to their record in pass 2
    copies = {}                                 # record index -> [(new uid, name, Obj)]
    made = []
    if copy_src:
        missing_src = copy_src - set(sources)
        if missing_src:
            raise ValueError(f"can't copy (not a decodable entity): {[m.hex() for m in missing_src]}")
        fresh_used = duplicate.used_ids(idents, id_avoid)
        ordinal = {}
        for name, op, *args in edits:
            if op != "copy":
                continue
            obj, ridx, sname = sources[name]
            k = ordinal[name] = ordinal.get(name, 0) + 1
            m1 = np.array(args[0], dtype=np.float64).reshape(4, 4)
            m0 = matrices[name]
            if not np.allclose(np.linalg.norm(m1[:3, :3], axis=1), np.linalg.norm(m0[:3, :3], axis=1), atol=1e-4):
                raise ValueError(f"copy of {sname}: scaling isn't supported")
            lo, hi = moved_bv(*bvs[name][:2], m0, m1)
            fresh = duplicate.FreshIds(fresh_used, duplicate.seed(src, name, k))
            new, nid = duplicate.make_copy(obj, m1, (lo, hi), fresh)
            fresh_used = fresh.used
            cname = f"{sname}_ED{k:03d}"
            copies.setdefault(ridx, []).append((nid, cname, new))
            made.append((name, nid, cname, m1))
            if created is not None:
                created.append(nid)             # the copies' new uids, in the order of the copy edits
    missing = {n for n in names - set(found) if (n if isinstance(n, bytes) else uid_of.get(n)) not in raw_new}
    if missing:
        raise ValueError(f"no top-level entity with an InertComponent shape: "
                         f"{sorted(k if isinstance(k, str) else k.hex() for k in missing)}")
    by_shape, widen, ledges_for = {}, {}, {}
    for name, op, *args in edits:
        if op == "copy" or (op in ("move", "remove") and (name if isinstance(name, bytes) else uid_of.get(name))
                            in raw_new):
            continue
        m, sid, eid = found[name]
        m = new_m.get(eid, m)                   # owner moved in the same build: the piece stays where it was drawn
        if op in ("move", "remove", "copy"):
            continue
        if op == "add":
            wv, tris = args[:2]
            climbable = args[2] if len(args) > 2 else True
            if closed(tris) and not outward(wv, tris):
                raise ValueError(f"{name}: the added triangles are wound inside out")
            local = to_local(wv, m)
            by_shape.setdefault(sid, []).append(("add", local, np.asarray(tris)))
            widen.setdefault(eid, []).append(np.asarray(wv, dtype=np.float64) - m[3, :3])   # world-axis offsets
            if climbable:
                from . import climb
                edges = climb.ledges(local, np.asarray(tris), min_len=0.2)
                if edges:
                    ledges_for.setdefault(eid, []).extend(edges)
        elif op == "sink":
            by_shape.setdefault(sid, []).append(("sink", float(args[0])))
        else:
            raise ValueError(f"unknown op {op!r}")

    def edited(v, t, mats, ops):
        for op in ops:
            if op[0] == "add":
                _o, local, tris = op
                common = max(set(mats), key=mats.count) if mats else 0
                t = np.vstack([t, tris + len(v)])
                v = np.vstack([v, local])
                mats = mats + bytes([common]) * len(tris)
            else:
                v = v + (0.0, 0.0, op[1])
        return v, t, mats

    def apply(fields, ops):
        return _set_shape(fields, *edited(*_shape_arrays(fields), ops))

    # the merged shapes (one per cell, MultiMeshShape) whose tree picks an edited member by its world box
    for sid, ops in by_shape.items():
        lo, hi = shape_box[sid]
        v, _t, _m = edited(np.array([lo, hi]), np.zeros((0, 3), int), b"", ops)
        shape_box[sid] = (v.min(0), v.max(0))
    edited_entities = {found[n][2] for n in names if n in found}
    remerge = {mid for mid, members in multis.items() if edited_entities & set(members)}

    def rebuild_multi(ent):
        mf = _multi_fields(S, ent)
        members = [x.id for x in mf["ContainedEntities"]]
        shapes = [M._link(x) for x in mf["ContainedShapes"]]
        boxes = [mopp.member_box(*shape_box[s], matrices[e]) for e, s in zip(members, shapes)]
        code, (ox, oy, oz, sc) = mopp.build_boxes([b[0] for b in boxes], [b[1] for b in boxes])
        mf["MoppCode"] = [code[i:i + 1] for i in range(len(code))]
        mf["MoppOffsetAndScale"] = struct.pack("<4f", ox, oy, oz, sc)
        return f"merged shape of {len(members)} members re-treed ({len(code)} bytes)"

    # 2. rewrite every record holding an edited shape, entity or merged shape
    changed, report = set(), {}
    for rec in recs:
        d, (entries, table) = payload(rec)
        objs, touched = [], False
        adding = copies.get(rec.index, [])
        if adding and not any(object_at(d, off, size)["type"] == cls["GridCellDataBlock"]
                              for off, size, _s, _f in entries):
            raise ValueError(f"{rec.name} has no GridCellDataBlock: objects in it can't be copied yet")
        for off, size, serial, filt in entries:
            o = object_at(d, off, size)
            body = o["body"]
            if o["type"] == cls["Entity"] and body[2:6] in remerge:
                root = C.decode(body)
                report[body[2:6]] = rebuild_multi(root.obj)
                body = C.encode(root)
                touched = True
            elif o["type"] == cls["MeshShape"] and body[2:6] in by_shape:
                root = C.decode(body)
                report[body[2:6]] = apply(root.obj.fields, by_shape[body[2:6]])
                body = C.encode(root)
                touched = True
            elif o["type"] == cls["Entity"] and body[2:6] in raw_new:
                body = raw_moved(body, raw_matrices[body[2:6]], raw_new[body[2:6]])
                touched = True
            elif o["type"] in (cls["Entity"], cls["EntityGroup"]) and (
                    body[2:6] in widen or body[2:6] in new_m or body[2:6] in grow):
                root = C.decode(body)
                ent = root.obj
                uid = body[2:6]
                if uid in new_m:
                    ent.fields["GlobalMatrix"] = new_m[uid].astype("<f4").tobytes()
                    _set_bv(ent.fields, *new_bv[uid])
                if uid in widen:
                    lo, hi, _t = _bv(ent.fields)
                    for offs in widen[uid]:
                        lo, hi = np.minimum(lo, offs.min(0)), np.maximum(hi, offs.max(0))
                    _set_bv(ent.fields, lo, hi)
                if uid in ledges_for:
                    from . import climb
                    gs = next((M._obj(p) for p in ent.fields["Components"] if M._obj(p) is not None
                               and S.name_of(M._obj(p).type_hash) == "GuidanceSystem"), None)
                    if gs is not None:
                        climb.append(gs.fields, ledges_for[uid])
                        report[("ledges", uid)] = f"+{len(ledges_for[uid])} ledges"
                    else:
                        report[("ledges", uid)] = "owner has no GuidanceSystem: piece not climbable"
                if uid in grow:
                    for r in ent.fields.get("Entities", []):
                        inner = M._obj(r)
                        if inner is not None and inner.id in new_m:
                            inner.fields["GlobalMatrix"] = new_m[inner.id].astype("<f4").tobytes()
                            _set_bv(inner.fields, *new_bv[inner.id])
                    lo, hi, _t = _bv(ent.fields)
                    for pts in grow[uid]:
                        lo, hi = np.minimum(lo, pts.min(0)), np.maximum(hi, pts.max(0))
                    _set_bv(ent.fields, lo, hi)
                body = C.encode(root)
                touched = True
            if adding and o["type"] == cls["GridCellDataBlock"]:
                body = duplicate.list_in_cell(C, body, [nid for nid, _n, _o in adding])
                touched = True
            objs.append((serial, M.mapwalk.object_bytes(o, body), None if filt == M.mapwalk.NO_FILTER else filt))
        for nid, cname, new in adding:
            objs.append((struct.unpack("<I", nid)[0],
                         duplicate.entity_record_bytes(cls["Entity"], cname, duplicate.encode_entity(C, new)), None))
            touched = True
        if touched:
            nd = M.mapwalk.rebuild(objs, table)
            pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
            assert decompress(pl) == nd
            set_payload(image, rec.index, pl)
            changed.add(rec.index)
    write(dest, image)

    # 3. read back: edited shapes decode and their trees reach every triangle; other records untouched
    with M.Forge(dest) as f:
        same = all(f.read(r) == raw[r.index] for r in f.records if r.index not in changed)
        checked = set()
        for r in f.records:
            if r.index not in changed:
                continue
            d = decompress(M.mapwalk.split_prefix(f.read(r))[1])
            for off, size, _s, _f in M.mapwalk.walk(d)[0]:
                o = object_at(d, off, size)
                if o["type"] == cls["MeshShape"] and o["body"][2:6] in by_shape:
                    fl = C.decode(o["body"]).obj.fields
                    nt = len(fl["IndicesTriangles"]) // 3
                    reached, _h, _s2 = mopp.walk(b"".join(fl["MoppCode"]))
                    assert sorted(t for t, _p in reached) == list(range(nt))
                    checked.add(o["body"][2:6])
                if o["type"] in (cls["Entity"], cls["EntityGroup"]) and (
                        o["body"][2:6] in new_m or o["body"][2:6] in grow):
                    ent = C.decode(o["body"]).obj
                    for e in [ent] + [M._obj(r) for r in ent.fields.get("Entities", [])]:
                        if e is not None and e.id in new_m:
                            assert np.allclose(_matrix(e.fields), new_m[e.id], atol=1e-4)
                            checked.add(("moved", e.id))
                if o["type"] == cls["Entity"] and o["body"][2:6] in raw_new:
                    rm = np.frombuffer(o["body"][10:74], "<f4").reshape(4, 4)
                    assert np.allclose(rm, raw_new[o["body"][2:6]], atol=1e-4)
                    checked.add(("moved", o["body"][2:6]))
                if o["type"] == cls["Entity"] and o["body"][2:6] in remerge:
                    mf = _multi_fields(S, C.decode(o["body"]).obj)
                    code = b"".join(mf["MoppCode"])
                    members = [x.id for x in mf["ContainedEntities"]]
                    reached, _h, _s2 = mopp.walk(code)
                    assert sorted(t for t, _p in reached) == list(range(len(members)))
                    ox, oy, oz, sc = struct.unpack("<4f", mf["MoppOffsetAndScale"])
                    shapes = [M._link(x) for x in mf["ContainedShapes"]]
                    for k, (e, s) in enumerate(zip(members, shapes)):
                        if e in edited_entities:     # the edited member is found across its whole new box
                            blo, bhi = mopp.member_box(*shape_box[s], matrices[e])
                            grid = np.stack(np.meshgrid(*[np.linspace(blo[a], bhi[a], 5) for a in range(3)]), -1)
                            for c in mopp.cells(grid.reshape(-1, 3), np.array((ox, oy, oz)), sc):
                                assert k in mopp.query_point(code, c)
                    checked.add(o["body"][2:6])
    if made:
        new_ids = {nid for _s, nid, _n, _m in made}
        seen, listed = {}, set()
        with M.Forge(dest) as f:
            for r in f.records:
                if r.name == "GlobalMetaFile" or len(raw[r.index]) <= 8:
                    continue
                d = decompress(M.mapwalk.split_prefix(f.read(r))[1])
                for off, size, _s, _f in M.mapwalk.walk(d)[0]:
                    o = object_at(d, off, size)
                    b = o["body"]
                    if b[:2] == M.UID_MARK and b[2:6] in new_ids:
                        seen[b[2:6]] = seen.get(b[2:6], 0) + 1
                        e = C.decode(b).obj
                        mm = next(m for _s, nid, _n, m in made if nid == b[2:6])
                        assert np.allclose(_matrix(e.fields), mm, atol=1e-4)
                    if o["type"] == cls["GridCellDataBlock"] and r.index in copies:
                        g = C.decode(b).obj.fields
                        n_act = struct.unpack("<I", g["NumberOfObjectsToActivate"])[0]
                        listed |= {x.id for x in g["Objects"][:n_act]} & new_ids
        copies_ok = all(seen.get(u) == 1 for u in new_ids) and listed == new_ids
        for sname, nid, cname, m in made:
            log(f"copy {cname} ({nid.hex()}) of {sname.hex()} at {np.round(m[3, :3], 2)}")
        log(f"copies: each defined once and activated in its cell: {'yes' if copies_ok else 'NO'}")
    else:
        copies_ok = True
    for eid, m1 in raw_new.items():
        log(f"entity {eid.hex()} (not decodable - its matrix bytes moved): from {np.round(raw_matrices[eid][3, :3], 2)}"
            f" to {np.round(m1[3, :3], 2)}")
    for name, op, *_a in edits:
        if op == "copy" or (name if isinstance(name, bytes) else uid_of.get(name)) in raw_new:
            continue
        sid = found[name][1]
        label = name if isinstance(name, str) else f"entity {name.hex()}"
        if op in ("move", "remove"):
            eid = found[name][2]
            log(f"{label} ({op}): from {np.round(old_m[eid][3, :3], 2)} to {np.round(new_m[eid][3, :3], 2)}"
                + (f", in group {container[eid].hex()}" if eid in container else ""))
        else:
            log(f"{label} ({op}): shape {sid.hex()} -> {report.get(sid)}")
    for (kind, uid), msg in [(k, v) for k, v in report.items() if isinstance(k, tuple) and k[0] == "ledges"]:
        log(f"entity {uid.hex()}: {msg}")
    for mid in remerge:
        log(f"merged-collision entity {mid.hex()}: {report.get(mid)}")
    want = set(by_shape) | remerge | {("moved", e) for e in new_m} | {("moved", e) for e in raw_new}
    ok = same and checked == want and copies_ok
    log(f"{dest}: {len(changed)} records rewritten; edited shapes and merged trees read back: "
        f"{'yes' if checked == want else 'NO'}; other records byte-identical: {'yes' if same else 'NO'}")
    return ok


def raw_moved(body, old, new):
    """An undecodable entity body (spawn points) with its matrix moved, byte for byte: every copy of the old matrix -
    as stored at body +10 and TRANSPOSED (the 'Initial' spawns' Scene keeps one near their end, e.g. +1080) - becomes
    the new one; nothing else changes and the size stays."""
    o32, n32 = np.asarray(old, "<f4"), np.asarray(new, "<f4")
    out = bytearray(body)
    for a, b in ((o32.tobytes(), n32.tobytes()), (o32.T.copy().tobytes(), n32.T.copy().tobytes())):
        i = out.find(a)
        while i >= 0:
            out[i:i + 64] = b
            i = out.find(a, i + 64)
    if out[10:74] != n32.tobytes():
        raise ValueError(f"{body[2:6].hex()}: its matrix isn't where it should be (body +10)")
    return bytes(out)


def add_world_triangles(src, dest, entity_name, world_verts, tris, log=print):
    return edit_shapes(src, dest, [(entity_name, "add", world_verts, tris)], log=log)
