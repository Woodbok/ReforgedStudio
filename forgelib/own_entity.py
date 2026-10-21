"""forgelib/own_entity.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import glob
import os
import pickle
import re
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import clone
import exe_registry
import make_jester_79 as g2
from datafile import compress, decompress
from forge import Forge
from forge_write import add_file, set_payload
from objects import rebuild, directory_size, directory_mismatches
from paths import ACR, AC3_SOURCE
from transplant import build_object
ANIMSET_ENTRY, ANIMSET_HEAD = (20, 20)
_acr_cache = {}

def donor_private(objs, donor, archive):
    defs = clone.pristine_definitions(archive, {o['uid'] for o in objs})
    prefix = f'AC3MP_ID{donor}_'
    return [o['uid'] for o in objs if o['body'][:2] != b'\x01\x02' and defs.get(o['uid']) and all(((f or '').startswith(prefix) for f, _d in defs[o['uid']]))]

def is_class_id(c, classes):
    return c in classes and bin(c).count('1') >= 8 and c & 65535 and c >> 16

def inline_uids(body, classes):
    out = []
    for p in range(0, len(body) - 7):
        u, c = struct.unpack_from('<II', body, p)
        if u >= 65536 and is_class_id(c, classes) and (u not in out):
            out.append(u)
    return out

def donor_only_values(values, donor, archive):
    import hashlib
    key = hashlib.sha1(struct.pack(f'<{len(values)}I', *sorted(values))).hexdigest()[:16]
    cache = os.path.join(HERE, 'out', 'uid', f'donoronly_{donor}_{key}.pkl')
    if os.path.exists(cache):
        return pickle.load(open(cache, 'rb'))
    from array import array
    elsewhere, want = (set(), set(values))
    with Forge(archive) as f:
        for r in f.records:
            if (r.name or '').startswith(f'AC3MP_ID{donor}_'):
                continue
            raw = f.read(r)
            if raw[4:8] != g2.PAYLOAD_MAGIC:
                continue
            try:
                d = decompress(raw)
            except Exception:
                continue
            for off in range(4):
                a = array('I')
                a.frombytes(d[off:off + (len(d) - off) // 4 * 4])
                elsewhere |= want.intersection(a)
                del a
            if elsewhere == want:
                break
    out = set(values) - elsewhere
    pickle.dump(out, open(cache, 'wb'))
    return out

def animset_slots(body):
    n = struct.unpack_from('<I', body, 16)[0]
    assert len(body) == ANIMSET_HEAD + ANIMSET_ENTRY * n, 'an AnimSet body is its entries and nothing else'
    return {struct.unpack_from('<I', body, ANIMSET_HEAD + ANIMSET_ENTRY * i + 10)[0]: (ANIMSET_HEAD + ANIMSET_ENTRY * i, struct.unpack_from('<I', body, ANIMSET_HEAD + ANIMSET_ENTRY * i + 16)[0]) for i in range(n)}

def acr_objects(fname):
    if fname not in _acr_cache:
        with Forge(os.path.join(ACR, 'DataPC.forge')) as f:
            _acr_cache[fname] = [o for r in f.records if r.name == fname for o in clone.objects_of(decompress(f.read(r)))]
            assert _acr_cache[fname], fname
    return _acr_cache[fname]

def acr_clip(fname, which):
    objs = acr_objects(fname)
    src = next((o for o in objs if o['body'][:2] == b'\x01\x02' and (o['name'] == which if isinstance(which, str) else g2.clip_id(o['body']) == which)))
    body = g2.acr_to_ac3_anim(src['body'])
    assert len(body) == len(src['body']) - 1 + 4 * src['body'].count(g2.SOUND_TRIGGER)
    body, silenced = silence_foreign_events(body)
    if silenced:
        SILENCED.append((src['name'], silenced))
        print(f"entity: ACR clip {src['name']} posts {', '.join((f'{e:#x}' for e in silenced))} - ACR MP event(s) AC3 lacks (an AI bank's): trigger silenced")
    return {'type': src['type'], 'name': src['name'], 'body': body}
SILENCED = []
_FOREIGN = {}

def foreign_event(e):
    if not _FOREIGN:
        sys.path.insert(0, os.path.join(HERE, 'audio'))
        import character_voice as cv
        ac3, acr = cv.event_banks()
        _FOREIGN['ac3'], _FOREIGN['acr'], _FOREIGN['always'] = (ac3, acr, set(cv.ALWAYS_LOADED))
    if e in _FOREIGN['ac3'] or e not in _FOREIGN['acr']:
        return False
    return any(('_MP' in b.upper() or b in _FOREIGN['always'] for b in _FOREIGN['acr'][e]))

def silence_foreign_events(body):
    out, silenced = (bytearray(body), [])
    i = out.find(g2.SOUND_TRIGGER)
    while i >= 0:
        at = i + len(g2.SOUND_TRIGGER)
        e = struct.unpack_from('<I', out, at)[0]
        if e and foreign_event(e):
            struct.pack_into('<I', out, at, 0)
            silenced.append(e)
        i = out.find(g2.SOUND_TRIGGER, at)
    return (bytes(out), silenced)
_STOCK_CLIPS = {}

def stock_clip(clip):
    if not _STOCK_CLIPS:
        import id_census
        archive = os.path.join(AC3_SOURCE, 'DataPC.forge')
        for rname, items in id_census.census(archive):
            for ident, _t, _d, _n, _inl in items:
                if ident is not None:
                    _STOCK_CLIPS.setdefault(ident, rname)
    rname = _STOCK_CLIPS[clip]
    with Forge(os.path.join(AC3_SOURCE, 'DataPC.forge')) as f:
        r = next((r for r in f.records if r.name == rname))
        o = next((o for o in clone.objects_of(decompress(f.read(r))) if o['body'][:2] == b'\x01\x02' and g2.clip_id(o['body']) == clip))
    return {'type': o['type'], 'name': o['name'], 'body': o['body']}

def wanted_slots(e, donor_slots, present, log):
    out = {}
    if e.acr_animset:
        fname, aset_name = e.acr_animset
        objs = acr_objects(fname)
        acr = animset_slots(next((o for o in objs if o['name'] == aset_name))['body'])
        names = {g2.clip_id(o['body']): o['name'] for o in objs if o['body'][:2] == b'\x01\x02'}
        skipped = []
        for key, (_at, clip) in acr.items():
            if key not in donor_slots:
                skipped.append(names.get(clip, hex(clip)))
            elif clip in present:
                if donor_slots[key][1] != clip:
                    out[key] = clip
            else:
                out[key] = (fname, clip, clip)
        log(f"entity: his ACR AnimSet ({aset_name}): {len(acr)} slots, {len(out)} differ from the donor's, {len(skipped)} keys the donor lacks skipped {sorted(set(skipped))}")
    out.update(e.slots)
    return out

def facial_slots(c, donor_slots, donor_names, wanted, log):
    e = c.own_entity
    if not e.acr_animset:
        return {}
    files = [e.acr_animset[0]] + ([c.kill_set.acr_file] if c.kill_set else [])
    acr_names = {}
    for fname in files:
        for o in acr_objects(fname):
            if o['body'][:2] == b'\x01\x02':
                acr_names.setdefault(o['name'], (fname, g2.clip_id(o['body'])))
    clip_name = {}
    for fname in files:
        for o in acr_objects(fname):
            if o['body'][:2] == b'\x01\x02':
                clip_name.setdefault(g2.clip_id(o['body']), o['name'])
    body_key = {donor_names.get(clip): key for key, (_at, clip) in donor_slots.items()}
    out = {}
    for key, (_at, clip) in donor_slots.items():
        name = donor_names.get(clip, '')
        if key in wanted or not name.endswith('_Facial'):
            continue
        spec = wanted.get(body_key.get(name[:-len('_Facial')]))
        if not isinstance(spec, tuple):
            continue
        acr_body = spec[1] if isinstance(spec[1], str) else clip_name.get(spec[1])
        hit = acr_names.get(f'{acr_body}_Facial_Facial')
        if hit:
            out[key] = (hit[0], f'{acr_body}_Facial_Facial', hit[1])
    if out:
        log(f"entity: {len(out)} facial slots take his ACR faces ({', '.join(sorted((s[1] for s in out.values())))})")
    return out

def neutral_faces(c, donor_slots, donor_names, wanted):
    e = c.own_entity
    if not (e and e.face_rig):
        return {}
    body_key = {donor_names.get(clip): key for key, (_at, clip) in donor_slots.items()}
    out = {}
    for key, (_at, clip) in donor_slots.items():
        name = donor_names.get(clip, '')
        if key in wanted or not name.endswith('_Facial'):
            continue
        bk = body_key.get(name[:-len('_Facial')])
        if bk is not None and bk != key:
            out[key] = bk
    return out
BONE = 2507411529
BONE_TRANSFORMS = (10, 64)
EYE_MESH = re.compile('_Eyes?_LOD\\d$', re.I)

def collapsed_mesh(body):
    from weapon_icon import mesh_buffers
    stride, vb, nv, _tris = mesh_buffers(body)
    out = bytearray(body)
    for k in range(nv):
        struct.pack_into('<3h', out, vb + stride * k, 0, 0, 0)
    return (bytes(out), nv)

def face_rig_body(body, acr_body):
    key = struct.pack('<I', BONE)

    def sites(b):
        out, i = ({}, b.find(key))
        while i >= 0:
            out[struct.unpack_from('<I', b, i + 4)[0]] = i + 4
            i = b.find(key, i + 1)
        return out
    mine, acr = (sites(body), sites(acr_body))
    out, n = (bytearray(body), 0)
    at, size = BONE_TRANSFORMS
    for bone, p in mine.items():
        q = acr.get(bone)
        if q is None:
            continue
        if not (body[p + 4] == acr_body[q + 4] == 2 and body[p + 9] == acr_body[q + 9] == 3):
            assert body[p + 4:p + 4 + at + size - 6] == acr_body[q + 4:q + 4 + at + size - 6], f'bone 0x{bone:08X}'
            continue
        new = acr_body[q + at:q + at + size]
        if out[p + at:p + at + size] != new:
            out[p + at:p + at + size] = new
            n += 1
    return (bytes(out), n)
POSE_GROUP, POSE, POSE_BONE = (3371740159, 547156082, 119336528)

def skeleton_poses(body):
    g = body.find(struct.pack('<I', POSE_GROUP))
    assert g > 0 and body.count(struct.pack('<I', POSE_GROUP)) == 1
    n, p, poses = (struct.unpack_from('<I', body, g + 4)[0], g + 8, [])
    start = p
    for _k in range(n):
        uid, cls, h, cnt = struct.unpack_from('<IIII', body, p)
        assert cls == POSE, f'pose at {p}'
        p, entries = (p + 16, [])
        for _j in range(cnt):
            euid, ecls, bone = struct.unpack_from('<III', body, p)
            assert ecls == POSE_BONE, f'pose bone at {p}'
            entries.append((euid, bone, body[p + 12:p + 24]))
            p += 24
        poses.append((uid, h, entries))
    return (start, p, poses)

def face_poses_body(body, acr_body, fresh):
    start, end, mine = skeleton_poses(body)
    _s, _e, acr = skeleton_poses(acr_body)
    acr_by_hash = {h: entries for _u, h, entries in acr}
    acr_only = [h for _u, h, _en in acr if h not in {h2 for _u2, h2, _e2 in mine}]
    free = [i for i, (_u, h, _en) in enumerate(mine) if h not in acr_by_hash]
    assert len(acr_only) <= len(free), 'more ACR-only poses than AC3-only slots'
    mine = [(u, acr_only[free.index(i)] if i in free[:len(acr_only)] else h, en) for i, (u, h, en) in enumerate(mine)]
    rig = set()
    k = body.find(struct.pack('<I', BONE))
    while k >= 0:
        rig.add(struct.unpack_from('<I', body, k + 4)[0])
        k = body.find(struct.pack('<I', BONE), k + 1)
    out, ported, emptied = (bytearray(), 0, 0)
    for uid, h, entries in mine:
        new = [(b, d) for _u, b, d in acr_by_hash.get(h, []) if b in rig]
        if h in acr_by_hash:
            ported += 1
        else:
            emptied += 1
        uids = [u for u, _b, _d in entries] + [fresh.take() for _x in range(max(0, len(new) - len(entries)))]
        out += struct.pack('<IIII', uid, POSE, h, len(new))
        for (b, d), u in zip(new, uids):
            out += struct.pack('<III', u, POSE_BONE, b) + d
    new_body = body[:start] + bytes(out) + body[end:]
    assert len(skeleton_poses(new_body)[2]) == len(mine)
    return (new_body, ported, emptied)
_TWIN_CACHE = {}

def base_clip_twins(base, ids):
    if not ids:
        return {}
    key = (os.path.abspath(base), os.path.getmtime(base))
    if key not in _TWIN_CACHE:
        found = {}
        with Forge(base) as f:
            for r in f.records:
                n = r.name or ''
                if not (n.startswith('AC3MP_ID') or n.startswith('AC3MP_Menu_')):
                    continue
                try:
                    objs = clone.objects_of(decompress(f.read(r)))
                except Exception:
                    continue
                for o in objs:
                    if o['body'][:2] == b'\x01\x02':
                        found.setdefault(g2.clip_id(o['body']), (build_object(o['type'], g2.anim_body_size(o['body']), o['name'], o['body']), n))
        _TWIN_CACHE[key] = found
    return {k: v for k, v in _TWIN_CACHE[key].items() if k in ids}

def build_copies(c, base, log):
    e = c.own_entity
    classes, _ = exe_registry.load(exe_registry.AC3)
    archive = os.path.join(AC3_SOURCE, 'DataPC.forge')
    names = [f'AC3MP_ID{c.donor}_{s}' for s in e.files]
    his_names = [f'AC3MP_ID{c.new}_{s}' for s in e.files]
    with Forge(base) as f:
        recs = {r.name: r for r in f.records}
        plains = [decompress(f.read(recs[n])) for n in names]
        indices = [recs[n].index for n in names]
        boot_plain = bytearray(decompress(f.read(recs['Game Bootstrap Settings'])))
        his = [o for n in his_names if n in recs for o in clone.objects_of(decompress(f.read(recs[n])))]
    files = [clone.objects_of(p) for p in plains]
    aset_uid = None
    for objs in files:
        eb = objs[0]
        assert classes.get(eb['type']) == 'EntityBuilder', eb['name']
        uid = next((o['uid'] for o in objs if classes.get(o['type']) == 'AnimSet' and struct.pack('<I', o['uid']) in eb['body']))
        assert aset_uid in (None, uid), "the donor's files name different AnimSets"
        aset_uid = uid
    asets = [next((o for o in objs if o['uid'] == aset_uid)) for objs in files]
    assert all((a['body'] == asets[0]['body'] for a in asets)), "the donor's AnimSet differs between files"
    private = []
    for objs in files:
        private += [u for u in donor_private(objs, c.donor, archive) if u not in private]
    shared_sets = []
    if voice_events(c):
        classes, _ = exe_registry.load(exe_registry.AC3)
        shared_sets = sorted({o['uid'] for objs in files for o in objs if classes.get(o['type']) == 'MultiHumanSoundSet' and o['uid'] not in private})
        private += shared_sets
    for objs in files:
        assert objs[0]['uid'] in private and aset_uid in private, 'EntityBuilder / AnimSet not private'
        renew = set(private) & {o['uid'] for o in objs}
        for o in objs:
            if o['uid'] not in renew and o['body'][:2] != b'\x01\x02':
                named = [hex(u) for u in renew if struct.pack('<I', u) in o['body']]
                assert not named, (f"shared object {o['name']} names private ones", named)
    known = set(g2.all_directory_values(archive))
    for pk in sorted(glob.glob(os.path.join(HERE, 'out', 'uid', 'AC3_*.pkl'))):
        known |= set(pickle.load(open(pk, 'rb'))[0])
    donor_uids = {o['uid'] for objs in files for o in objs}
    for o in his:
        if o['uid'] not in donor_uids:
            boot_plain = boot_plain.replace(struct.pack('<I', o['uid']), bytes(4))
    fresh = clone.FreshUids(c.new + 6000, known, [plains[0], bytes(boot_plain)])
    body = bytearray(asets[0]['body'])
    slots = animset_slots(bytes(body))
    present = {g2.clip_id(o['body']) for o in files[0] if o['body'][:2] == b'\x01\x02'}
    clips = {}
    stock_carried = []
    wanted = wanted_slots(e, slots, present, log)
    donor_names = {g2.clip_id(o['body']): o['name'] for o in files[0] if o['body'][:2] == b'\x01\x02'}
    wanted.update(facial_slots(c, slots, donor_names, wanted, log))
    neutral = neutral_faces(c, slots, donor_names, wanted)
    for key, spec in wanted.items():
        if key not in slots:
            sys.exit(f"own_entity: the donor's AnimSet has no key 0x{key:08X}")
        if isinstance(spec, int):
            clip = spec
            assert clip in present, f'clip 0x{clip:08X} is not in {names[0]}'
        else:
            fname, which, clip = spec
            if clip in present:
                sys.exit(f'own_entity: clip id 0x{clip:08X} is already in {names[0]} (G4b)')
            if clip in known and (not (isinstance(which, int) and which == clip)):
                try:
                    o = stock_clip(clip)
                except (KeyError, StopIteration):
                    o = None
                if o is not None and isinstance(which, str) and (o['name'] == which):
                    wanted[key] = (fname, clip, clip)
                    spec = wanted[key]
                    which = clip
                else:
                    new_clip = fresh.take()
                    log(f'entity: ACR clip {which} keeps no id of its own in AC3 (0x{clip:08X} is taken) - carried as 0x{new_clip:08X}')
                    src = acr_clip(fname, which)
                    nb = g2.clip_with_id(src['body'], new_clip)
                    clips[new_clip] = build_object(src['type'], g2.anim_body_size(nb), src['name'], nb)
                    clip = new_clip
            if clip not in clips:
                if clip in known:
                    try:
                        o = stock_clip(clip)
                    except (KeyError, StopIteration):
                        o = None
                    if o is None:
                        src = acr_clip(fname, which)
                        new_clip = fresh.take()
                        log(f"entity: ACR clip {src['name']} (0x{clip:08X}: another object's id in AC3) carried as 0x{new_clip:08X}")
                        nb = g2.clip_with_id(src['body'], new_clip)
                        clips[new_clip] = build_object(src['type'], g2.anim_body_size(nb), src['name'], nb)
                        clip = new_clip
                    else:
                        stock_carried.append(o['name'])
                        clips[clip] = build_object(o['type'], g2.anim_body_size(o['body']), o['name'], o['body'])
                else:
                    o = acr_clip(fname, which)
                    nb = g2.clip_with_id(o['body'], clip)
                    clips[clip] = build_object(o['type'], g2.anim_body_size(nb), o['name'], nb)
        struct.pack_into('<I', body, slots[key][0] + 16, clip)
    for key, bk in neutral.items():
        struct.pack_into('<I', body, slots[key][0] + 16, struct.unpack_from('<I', body, slots[bk][0] + 16)[0])
    if neutral:
        log(f"entity: {len(neutral)} facial slots had no face of his ({', '.join(sorted((donor_names.get(slots[k][1], hex(k)) for k in neutral)))}) - their body slot's clip (no face pose: AC3's own no-face slot), not the donor's face")
    if stock_carried:
        log(f"entity: {len(stock_carried)} of his ACR clips are AC3's too (same clip id): AC3's bytes carried ({', '.join(sorted(stock_carried)[:4])}{('...' if len(stock_carried) > 4 else '')})")
    n = struct.unpack_from('<I', body, 16)[0]
    for i in range(n):
        struct.pack_into('<I', body, ANIMSET_HEAD + ANIMSET_ENTRY * i, fresh.take())
    mapping = {u: fresh.take() for u in private}
    for sid in shared_sets:
        body0 = next((o['body'] for objs in files for o in objs if o['uid'] == sid))
        for u in inline_uids(body0, exe_registry.load(exe_registry.AC3)[0]):
            if u not in mapping:
                mapping[u] = fresh.take()
    if shared_sets:
        log(f"entity: the donor's sound set is shared ({[hex(u) for u in shared_sets]}): his own copy, entries renewed")
    if e.renew_nested:
        classes, _ = exe_registry.load(exe_registry.AC3)
        nested = []
        for objs in files:
            for o in objs:
                if o['uid'] in mapping and o['uid'] != aset_uid:
                    nested += [u for u in inline_uids(o['body'], classes) if u not in mapping and u not in nested]
        only = donor_only_values(nested, c.donor, archive)
        blocked = {u for objs in files for o in objs if o['uid'] not in mapping and o['body'][:2] != b'\x01\x02' for u in only if struct.pack('<I', u) in o['body']}
        for u in nested:
            if u in only and u not in blocked:
                mapping[u] = fresh.take()
        log(f"entity: nested ids of the private objects: {len(nested)}, {len(only)} only in the donor's files, {len(blocked)} named by a shared object (kept), {len(nested) - len(only)} shared by other files (kept); {len(only) - len(blocked)} renewed")
    new_slots = animset_slots(bytes(body))
    voice = voice_events(c)
    if voice:
        sets = [o for objs in files for o in objs if classes.get(o['type']) == 'MultiHumanSoundSet']
        assert sets and all((o['uid'] in mapping and o['body'] == sets[0]['body'] for o in sets)), "voice: the donor's files should carry one private sound set"
        log(f"entity: his voice - ACR {voice_set(c)[1]}, {sum((1 for e in voice if e))} of {len(voice)} slots named, {len({e for e in voice if e} - {e for _p, e in voice_entries(sets[0]['body'])})} events the donor's set doesn't name")
    out, face_poses = ([], {})
    for name, his_name, plain, objs, index in zip(names, his_names, plains, files, indices):
        repl = {}
        for pos, o in enumerate(objs):
            assert not (getattr(e, 'hide_eyes', False) and classes.get(o['type']) == 'Mesh' and EYE_MESH.search(o['name']) and (o['uid'] not in mapping)), f"hide_eyes: {o['name']} in {name} is not renewed (shared with the donor)"
            if o['uid'] not in mapping:
                continue
            b = bytes(body) if o['uid'] == aset_uid else o['body']
            if voice and classes.get(o['type']) == 'MultiHumanSoundSet':
                b = voice_body(b, voice)
            if getattr(e, 'hide_eyes', False) and classes.get(o['type']) == 'Mesh' and EYE_MESH.search(o['name']):
                b, nv = collapsed_mesh(b)
                log(f"entity: {his_name} {o['name']} - the donor's eyeball collapsed ({nv} vertices; his eyes are ACR's)")
            if e.face_rig and classes.get(o['type']) == 'Skeleton' and (o['name'] == f'AC3MP_ID{c.donor}_Head'):
                acr_sk = next((x for x in acr_objects(e.face_rig[0]) if x['name'] == e.face_rig[1] and classes.get(x['type']) == 'Skeleton'))
                b, nb = face_rig_body(b, acr_sk['body'])
                if o['uid'] not in face_poses:
                    face_poses[o['uid']] = face_poses_body(b, acr_sk['body'], fresh)
                b, n_ported, n_empty = face_poses[o['uid']]
                log(f"entity: {his_name} face rig - {nb} bones take ACR {e.face_rig[1]}'s transforms; expression poses: {n_ported} ACR's, {n_empty} only the donor's rig has emptied")
            for old, new in mapping.items():
                b = b.replace(struct.pack('<I', old), struct.pack('<I', new))
            assert o['body_size'] == len(o['body']) - 1, o['name']
            repl[pos] = build_object(o['type'], len(b) - 1, rename_object(o['name'], c), b)
        have = {g2.clip_id(o['body']) for o in objs if o['body'][:2] == b'\x01\x02'}
        for k, (theirs, where) in base_clip_twins(base, set(clips) - have).items():
            if theirs != clips[k]:
                clips[k] = theirs
                log(f'entity: clip 0x{k:08X} as {where} carries it (one body per clip id)')
        additions = [clips[k] for k in sorted(clips) if k not in have]
        new_plain = rebuild(plain, repl, additions=additions)
        assert directory_mismatches(new_plain) == []
        new_objs = clone.objects_of(new_plain)
        leftover = [hex(u) for u in mapping if any((struct.pack('<I', u) in o['body'] for o in new_objs if o['body'][:2] != b'\x01\x02'))]
        assert not leftover, f'donor-private uids still named in {his_name}: {leftover}'
        have = {g2.clip_id(o['body']) for o in new_objs if o['body'][:2] == b'\x01\x02'}
        missing = [hex(clip) for _k, (_at, clip) in new_slots.items() if clip not in have]
        assert not missing, f'{his_name}: AnimSet clips not in the file {missing}'
        out.append((his_name, new_plain, new_objs[0]['uid'], index))
        log(f'entity: {his_name} = {name} with {len(repl)} private objects renewed, {len(additions)} clips appended')
    return (out, mapping)

def _character_voice():
    sys.path.insert(0, os.path.join(HERE, 'audio'))
    import character_voice
    return character_voice

def voice_set(c):
    return _character_voice().voice_of(c)

def voice_events(c):
    v = voice_set(c)
    return _character_voice().slot_events(*v) if v else None

def voice_entries(body):
    sys.path.insert(0, os.path.join(HERE, 'audio'))
    import character_voice
    return character_voice.entries(body)

def voice_body(body, events):
    sys.path.insert(0, os.path.join(HERE, 'audio'))
    import character_voice
    return character_voice.with_events(body, events)

def rename_object(name, c):
    return name.replace(f'ID{c.donor}_', f'ID{c.new}_') if name else name

def apply(c, image, base, kit_body, log):
    if not c.own_entity:
        return (kit_body, {})
    copies, mapping = build_copies(c, base, log)
    with Forge(base) as f:
        recs = {r.name: r for r in f.records}
        record_uids = clone.fresh_record_uids({r.uid for r in f.records}, c.new + 6001)
        for name, plain, root, template in copies:
            payload = compress(plain, first_chunk=directory_size(plain))
            assert decompress(payload) == plain
            if name in recs:
                if decompress(f.read(recs[name])) != plain:
                    set_payload(image, recs[name].index, payload)
                    log(f'entity: {name} rewritten')
                else:
                    log(f'entity: {name} unchanged')
            else:
                index = add_file(image, name, payload, next(record_uids), template_index=template, entry_key=root)
                log(f'entity: {name} added (#{index})')
    named = {}
    for old, new in mapping.items():
        ob, nb = (struct.pack('<I', old), struct.pack('<I', new))
        if ob in kit_body:
            assert kit_body.count(ob) == 1, hex(old)
            kit_body = kit_body.replace(ob, nb)
            named[old] = new
        elif nb in kit_body:
            named[old] = new
    return (kit_body, named)

def check(c, dest):
    if not c.own_entity:
        return []
    from bootstrap_build import Bootstrap
    classes, _ = exe_registry.load(exe_registry.AC3)
    kit = next((o for o in Bootstrap(dest).objects if o['name'] == f'CharacterBuildingKit_AC3MP_ID{c.new}'))
    problems, bodies = ([], [])
    with Forge(dest) as f:
        recs = {r.name: r for r in f.records}
        for s in c.own_entity.files:
            name = f'AC3MP_ID{c.new}_{s}'
            if name not in recs:
                problems.append(f'entity: {name} not in the build')
                continue
            objs = clone.objects_of(decompress(f.read(recs[name])))
            eb = objs[0]
            aset = next((o for o in objs if classes.get(o['type']) == 'AnimSet' and struct.pack('<I', o['uid']) in eb['body']))
            bodies.append(aset['body'])
            for what, uid in (('EntityBuilder', eb['uid']), ('AnimSet', aset['uid'])):
                if struct.pack('<I', uid) not in kit['body']:
                    problems.append(f"entity: kit {c.new} does not name {name}'s {what} 0x{uid:08X}")
            present = {g2.clip_id(o['body']) for o in objs if o['body'][:2] == b'\x01\x02'}
            slots = animset_slots(aset['body'])
            problems += [f'entity: {name}: AnimSet names clip 0x{clip:08X}, not in the file' for _k, (_at, clip) in slots.items() if clip not in present]
            clip_names = {g2.clip_id(o['body']): o['name'] for o in objs if o['body'][:2] == b'\x01\x02'}
            for key, spec in c.own_entity.slots.items():
                want = spec if isinstance(spec, int) else spec[2]
                if slots[key][1] != want:
                    if isinstance(spec, tuple) and isinstance(spec[1], str) and (clip_names.get(slots[key][1]) == spec[1]):
                        continue
                    problems.append(f'entity: {name}: slot 0x{key:08X} plays 0x{slots[key][1]:08X}, not 0x{want:08X}')
    if len(set(bodies)) > 1:
        problems.append('entity: his files carry different AnimSets')
    voice = voice_events(c)
    if voice:
        with Forge(dest) as f:
            recs = {r.name: r for r in f.records}
            for s in c.own_entity.files:
                name = f'AC3MP_ID{c.new}_{s}'
                if name not in recs:
                    continue
                objs = clone.objects_of(decompress(f.read(recs[name])))
                sets = [o for o in objs if classes.get(o['type']) == 'MultiHumanSoundSet' and struct.pack('<I', o['uid']) in objs[0]['body']]
                if len(sets) != 1:
                    problems.append(f"voice: {name}'s EntityBuilder names {len(sets)} sound sets")
                elif [e for _p, e in voice_entries(sets[0]['body'])] != voice:
                    problems.append(f"voice: {name}'s sound set does not name his ACR events")
    return problems
