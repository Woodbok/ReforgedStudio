"""Resizing map objects. Havok collision doesn't scale (and the engine's own matrix scale is not relied on), so a
scaled object gets NEW resources with the scale baked in, all built by the Studio's mesh, MOPP and climb writers:

* every mesh of its Visual (each LOD of its LODSelector, or its one Mesh) copied with positions scaled along the
  object's own axes, normals through the inverse scale and tangents through the scale (renormalised), the vertex
  count and order unchanged - so its per-LOD baked shading still matches - and a new |w| for the new size;
* a new LODSelector naming those meshes; the per-LOD instance data pointing at them;
* a new MeshShape with the scaled triangles and a fresh MOPP tree;
* its climb edges' points scaled and their face normals corrected (each edge still runs along normal0 x normal1);
* its BoundingVolume (world-axis offsets) from the scaled LOD0 mesh; its cell's merged tree re-pointed at the new
  shape and re-treed with its new member box.

The entity's own GlobalMatrix stays unscaled (rotation + translation). Top-level entities with a MeshShape only.
"""
import copy
import struct

import numpy as np

from ac3codec import Ref, Root

from . import climb, collision, duplicate, meshenc, mopp
from . import mapdata as M
from .newmodel import _comp, _renew
from datafile import compress, decompress  # noqa: E402  (forgelib)
from forge_write import ArchiveImage, set_payload, write  # noqa: E402  (forgelib)
from transplant import object_at  # noqa: E402  (forgelib)


def scaled_mesh(mesh_root, S, fresh):
    """A decoded Mesh Root -> a scaled copy (new ids). S: (sx, sy, sz) > 0 along the mesh's own axes."""
    new = _renew(copy.deepcopy(mesh_root), fresh)
    md = M._obj(new.obj.fields["CompiledMesh"]).fields["MeshData"].fields
    fmt = md["VertexFormat"][0]
    if fmt not in meshenc.STRIDE:
        raise ValueError(f"vertex format {fmt} can't be rescaled yet")
    vb = b"".join(md["VertexBufferData"])
    f = meshenc.decode_vertices(vb, fmt)
    w = np.abs(f["w"].astype(np.float64))
    pos = f["q"].astype(np.float64) * (w[:, None] / 8.0 / 32767.0) * S
    big = 8 * (int(np.floor(np.abs(pos).max())) + 1)
    f["q"] = np.clip(np.rint(pos / (big / 8.0 / 32767.0)), -32767, 32767).astype(np.int16)
    f["w"] = (np.sign(f["w"]).astype(np.int64) * big).astype(np.int16)
    f["w"][f["w"] == 0] = big
    n = (f["n"].astype(np.float64) - 127.5) / 127.5 / S
    t = (f["t"].astype(np.float64) - 127.5) / 127.5 * S
    unit = lambda x: x / np.maximum(np.linalg.norm(x, axis=1), 1e-12)[:, None]  # noqa: E731
    f["n"], f["t"] = meshenc.unit8(unit(n)), meshenc.unit8(unit(t))
    out = meshenc.pack_vertices(f, fmt)
    md["VertexBufferData"] = [out[i:i + 1] for i in range(len(out))]
    return new, pos


def scaled_edges(gs_fields, S):
    pts, edges = climb.read(gs_fields)
    out = []
    for a, b, sub, n0, n1 in edges:
        pa, pb = pts[a] * S, pts[b] * S
        m0, m1 = n0 / S, n1 / S
        m0, m1 = m0 / max(np.linalg.norm(m0), 1e-12), m1 / max(np.linalg.norm(m1), 1e-12)
        if np.dot(pb - pa, np.cross(m0, m1)) < 0:
            pa, pb = pb, pa
        out.append((pa, pb, m0, m1, sub))
    return out


def scale_entities(src, dest, items, id_avoid=(), log=print):
    """items: [(entity uid (top-level), (sx, sy, sz))]. -> ok."""
    S_ = M.schema()
    C = M.Codec(S_)
    cls = {S_.name_of(h): h for h in S_.types_by_hash}
    bulk = {cls[n] for n in ("TextureMap", "Animation", "MaterialTemplate", "FX", "NavMeshManager") if n in cls}
    image = ArchiveImage.read(src)
    with M.Forge(src) as f:
        raw = {r.index: f.read(r) for r in f.records}
        recs = [r for r in f.records if r.name != "GlobalMetaFile" and len(raw[r.index]) > 8]
    payloads = {r.index: decompress(M.mapwalk.split_prefix(raw[r.index])[1]) for r in recs}
    walked = {i: M.mapwalk.walk(d) for i, d in payloads.items()}

    want = {u: np.asarray(s, dtype=np.float64) for u, s in items}
    if any((s <= 0).any() for s in want.values()):
        raise ValueError("negative or zero scale (mirroring) isn't supported")
    idents, roots, where, multis, matrices, shape_box = [], {}, {}, {}, {}, {}
    for rec in recs:
        d = payloads[rec.index]
        for off, size, _s, _f in walked[rec.index][0]:
            o = object_at(d, off, size)
            b = o["body"]
            uid = b[2:6] if b[:2] == M.UID_MARK else None
            dec = None
            if o["type"] not in bulk:
                try:
                    dec = C.decode(b)
                except Exception:  # noqa: BLE001
                    dec = None
            idents.append((uid, dec.obj if dec else None))
            if dec is None:
                continue
            cname = S_.name_of(o["type"])
            if cname in ("Entity", "Mesh", "LODSelector", "MeshShape"):
                roots[uid] = (cname, o["name"], dec)
                where.setdefault(uid, rec.index)
            if cname == "MeshShape":
                v = collision._shape_arrays(dec.obj.fields)[0]
                shape_box[uid] = (v.min(0), v.max(0))
            if cname in ("Entity", "EntityGroup"):
                matrices[dec.obj.id] = collision._matrix(dec.obj.fields)
                for r in dec.obj.fields.get("Entities", []):
                    inner = M._obj(r)
                    if inner is not None and "GlobalMatrix" in inner.fields:
                        matrices[inner.id] = collision._matrix(inner.fields)
                mf = collision._multi_fields(S_, dec.obj)
                if mf is not None:
                    multis[dec.obj.id] = [x.id for x in mf["ContainedEntities"]]
    used = duplicate.used_ids(idents, id_avoid)

    new_bodies = {}          # record index -> [(serial, bytes)]
    resources = {}           # record index -> [uid]
    entity_body = {}         # entity uid -> new Root
    shape_swap = {}          # entity uid -> (old shape, new shape)
    for k, (eid, S) in enumerate(want.items()):
        if eid not in roots or roots[eid][0] != "Entity":
            raise ValueError(f"{eid.hex()}: only top-level entities can be scaled")
        fresh = duplicate.FreshIds(used, duplicate.seed(src, eid, 7000 + k))
        ent_root = copy.deepcopy(roots[eid][2])
        ent = ent_root.obj
        ridx = where[eid]
        adds = new_bodies.setdefault(ridx, [])
        res = resources.setdefault(ridx, [])
        # visual
        vis = _comp(S_, ent, "Visual")
        target = M._link(vis.fields["Object"]) if vis else None
        mesh_map, lod0_pos = {}, None
        if target in roots and roots[target][0] == "LODSelector":
            sel = _renew(copy.deepcopy(roots[target][2]), fresh)
            for d in sel.obj.fields["LODDescs"]:
                r = M._obj(d).fields["Object"]
                if r.id in roots and roots[r.id][0] == "Mesh":
                    if r.id not in mesh_map:
                        nm, pos = scaled_mesh(roots[r.id][2], S, fresh)
                        mesh_map[r.id] = nm
                        lod0_pos = pos if lod0_pos is None else lod0_pos
                    r.id = mesh_map[r.id].obj.id
            sel.obj.flag = 1
            adds.append((sel, cls["LODSelector"], roots[target][1] + "_S"))
            res.append(sel.obj.id)
            vis.fields["Object"].id = sel.obj.id
        elif target in roots and roots[target][0] == "Mesh":
            nm, lod0_pos = scaled_mesh(roots[target][2], S, fresh)
            mesh_map[target] = nm
            vis.fields["Object"].id = nm.obj.id
        for old, nm in mesh_map.items():
            nm.obj.flag = 1
            adds.append((nm, cls["Mesh"], roots[old][1] + "_S"))
            res.append(nm.obj.id)
        if vis is not None:
            inst = M._obj(vis.fields.get("InstanceData"))
            items_ = ([inst] if inst is not None and S_.name_of(inst.type_hash) == "MeshInstanceData" else
                      [M._obj(x) for x in inst.fields.get("LODInstanceData", [])] if inst is not None else [])
            for mi in items_:
                if mi is not None and M._link(mi.fields.get("Mesh")) in mesh_map:
                    mi.fields["Mesh"].link = mesh_map[M._link(mi.fields["Mesh"])].obj.id
        # collision
        inert = _comp(S_, ent, "InertComponent")
        if inert is None:
            raise ValueError(f"{eid.hex()}: no collision to scale")
        old_sid = M._link(inert.fields["RigidBody"].fields["Shape"])
        if old_sid not in roots or roots[old_sid][0] != "MeshShape":
            raise ValueError(f"{eid.hex()}: its collision isn't a MeshShape")
        shape = _renew(copy.deepcopy(roots[old_sid][2]), fresh)
        v, t, mats = collision._shape_arrays(shape.obj.fields)
        collision._set_shape(shape.obj.fields, v * S, t, mats)
        shape.obj.flag = 1
        adds.append((shape, cls["MeshShape"], roots[old_sid][1] + "_S"))
        res.append(shape.obj.id)
        inert.fields["RigidBody"].fields["Shape"].id = shape.obj.id
        shape_box[shape.obj.id] = ((v * S).min(0), (v * S).max(0))
        shape_swap[eid] = (old_sid, shape.obj.id)
        # climb edges
        gs = _comp(S_, ent, "GuidanceSystem")
        if gs is not None and gs.fields.get("GuidanceObjects"):
            climb.write(gs.fields, scaled_edges(gs.fields, S))
        # bounding volume
        m = matrices[eid]
        src_pts = lod0_pos if lod0_pos is not None else v * S
        offs = src_pts @ m[:3, :3]
        b = ent.fields["BoundingVolume"].fields
        b["Min"], b["Max"] = struct.pack("<3f", *offs.min(0)), struct.pack("<3f", *offs.max(0))
        entity_body[eid] = ent_root
        used = fresh.used

    remerge = {mid for mid, members in multis.items() if set(members) & set(shape_swap)}
    changed = set()
    for rec in recs:
        d = payloads[rec.index]
        entries, table = walked[rec.index]
        adds = new_bodies.get(rec.index, [])
        objs, touched = [], bool(adds)
        for off, size, serial, filt in entries:
            o = object_at(d, off, size)
            body = o["body"]
            uid = body[2:6] if body[:2] == M.UID_MARK else None
            if o["type"] == cls["Entity"] and uid in entity_body:
                body = C.encode(entity_body[uid])
                touched = True
            elif o["type"] == cls["Entity"] and uid in remerge:
                root = C.decode(body)
                mf = collision._multi_fields(S_, root.obj)
                members = [x.id for x in mf["ContainedEntities"]]
                for k2, e2 in enumerate(members):
                    if e2 in shape_swap:
                        mf["ContainedShapes"][k2].id = shape_swap[e2][1]
                shapes = [M._link(x) for x in mf["ContainedShapes"]]
                boxes = [mopp.member_box(*shape_box[s], matrices[e2]) for e2, s in zip(members, shapes)]
                code, (ox, oy, oz, sc) = mopp.build_boxes([bx[0] for bx in boxes], [bx[1] for bx in boxes])
                mf["MoppCode"] = [code[i:i + 1] for i in range(len(code))]
                mf["MoppOffsetAndScale"] = struct.pack("<4f", ox, oy, oz, sc)
                body = C.encode(root)
                touched = True
            elif o["type"] == cls["GridCellDataBlock"] and resources.get(rec.index):
                root = C.decode(body)
                g = root.obj.fields
                lst = g["Objects"]
                tag, extra = (lst[0].tag, lst[0].extra) if lst else (1, 0)
                have = {x.id for x in lst}
                g["Objects"] = lst + [Ref(tag, extra, u) for u in dict.fromkeys(resources[rec.index]) if u not in have]
                body = C.encode(root)
                touched = True
            objs.append((serial, M.mapwalk.object_bytes(o, body), None if filt == M.mapwalk.NO_FILTER else filt))
        for root, type_id, name in adds:
            body = C.encode(root)
            objs.append((struct.unpack("<I", body[2:6])[0], duplicate.entity_record_bytes(type_id, name, body), None))
        if touched:
            nd = M.mapwalk.rebuild(objs, table)
            pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
            assert decompress(pl) == nd
            set_payload(image, rec.index, pl)
            changed.add(rec.index)
    write(dest, image)
    with M.Forge(dest) as f:
        same = all(f.read(r) == raw[r.index] for r in f.records if r.index not in changed)
    for eid, S in want.items():
        log(f"scaled entity {eid.hex()} by {np.round(S, 3)}: {len([1 for _ in [0]])} entity, new shape "
            f"{shape_swap[eid][1].hex()}")
    log(f"{dest}: {len(changed)} records rewritten ({len(remerge)} merged trees re-pointed); other records "
        f"byte-identical: {'yes' if same else 'NO'}")
    return same
