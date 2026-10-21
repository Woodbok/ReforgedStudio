"""forgelib/fx_carry.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
FX_CLASS = 2185896890
FX_OBJECT_AT = 39
ANIM_HEAD = b'\x01\x02'
UID_HEAD = b'\x00\x01'
REF_PREFIXES = (b'\x01\x00', b'\x01\x01', b'\x01\x02')
SOURCE_ARCHIVES = {'ACR': ('DataPC.forge',), 'ACB': ('DataPC.forge', 'DataPC_skins_0002_00000004_dlc.forge'), 'AC4': ('DataPC.forge',)}
KEEP_INLINE = ('TextureSelector', 'TextureFormatOptions', 'UVTransform')
SUBSTITUTE = {'ACR': {}, 'AC4': {}}
_cache = {}

def _index(game, archive='DataPC.forge'):
    key = (game, archive)
    if key not in _cache:
        import uid_index
        idx = uid_index.load(game, archive)
        _cache[key] = idx[0] if isinstance(idx, tuple) else idx
    return _cache[key]

def ac3_defined():
    if 'ac3' not in _cache:
        _cache['ac3'] = set(_index('AC3')) | set(_index('AC3', 'DataPC_CoreFX.forge'))
    return _cache['ac3']

def core_fx():
    return set(_index('AC3', 'DataPC_CoreFX.forge'))

def known():
    if 'known' not in _cache:
        out = {}
        for game, archives in (('AC3', ('DataPC.forge', 'DataPC_CoreFX.forge')),) + tuple(SOURCE_ARCHIVES.items()):
            for a in archives:
                for u, v in _index(game, a).items():
                    out.setdefault(u, (game, v[1], v[2]))
        _cache['known'] = out
    return _cache['known']

def fx_uids():
    if 'fx' not in _cache:
        _cache['fx'] = {u for u, v in known().items() if v[1] == FX_CLASS}
    return _cache['fx']

def fx_seeds(body):
    if os.path.join(HERE, 'acbchars') not in sys.path:
        sys.path.append(os.path.join(HERE, 'acbchars'))
    import anim_cut
    fxseed = next((k for k, v in anim_cut.CLS.items() if v == 'FXEventSeed'))
    out = []
    trs, _td = anim_cut.tracks(body)
    for _s, _e, seeds, times, _en in trs:
        for (x, y), t in zip(seeds, times):
            if struct.unpack_from('<I', body, x + 5)[0] == fxseed and y - x >= FX_OBJECT_AT + 4:
                out.append((t, struct.unpack_from('<I', body, x + FX_OBJECT_AT)[0]))
    return out

def fired(objs, fx):
    out = set()
    for o in objs:
        b = o['body']
        if b[:2] == ANIM_HEAD:
            out |= {u for k in range(0, len(b) - 3) if (u := struct.unpack_from('<I', b, k)[0]) in fx}
    return out

def _u32s_in(body, among):
    import numpy as np
    if 'fx_np' not in _cache or _cache['fx_np'][0] is not among:
        _cache['fx_np'] = (among, np.fromiter(among, dtype='<u4', count=len(among)))
    arr, out = (_cache['fx_np'][1], set())
    for k in range(4):
        n = (len(body) - k) // 4
        if n > 0:
            a = np.frombuffer(body, dtype='<u4', count=n, offset=k)
            out.update(a[np.isin(a, arr)].tolist())
    return out

def uid_of(o):
    b = o['body']
    return struct.unpack_from('<I', b, 2)[0] if b[:2] == UID_HEAD and len(b) >= 6 else None

def refs(body, among):
    own = struct.unpack_from('<I', body, 2)[0] if body[:2] == UID_HEAD and len(body) >= 6 else None
    out = []
    for k in range(2, len(body) - 3):
        if body[k - 2:k] in REF_PREFIXES:
            u = struct.unpack_from('<I', body, k)[0]
            if u >= 65536 and u != own and (u in among) and (u not in out):
                out.append(u)
    return out

def _label(u):
    v = known().get(u)
    if v is None:
        return f'0x{u:08X}'
    import exe_registry
    classes, _ = exe_registry.load(exe_registry.AC3)
    return f"0x{u:08X} {v[2]} ({classes.get(v[1], hex(v[1]))}, {v[0]}'s)"

def problems(files, defined, extra=None):
    import clone
    raw = clone.raw_types()
    fx_all, kn = (fx_uids(), known())
    out, seeds, named = ([], {}, {})
    for fname, objs in sorted(files.items()):
        for o in objs:
            b = o['body']
            if b[:2] == ANIM_HEAD:
                for _t, u in fx_seeds(b):
                    seeds.setdefault(u, {}).setdefault(fname, []).append(o['name'])
            elif o['type'] not in raw:
                for u in _u32s_in(b, fx_all) - {uid_of(o)}:
                    if u not in defined:
                        named.setdefault((fname, u), o['name'])
    for u, where in sorted(seeds.items()):
        if u not in defined:
            for fname, clips in sorted(where.items()):
                out.append(f'fx: {fname}: {len(clips)} seed(s) fire {_label(u)}, defined nowhere (first: {clips[0]})')
    for (fname, u), oname in sorted(named.items()):
        out.append(f'fx: {fname}/{oname} names the FX {_label(u)}, defined nowhere')
    defs = {}
    for fname, objs in list(sorted(files.items())) + list(sorted((extra or {}).items())):
        for o in objs:
            u = uid_of(o)
            if u is not None:
                defs.setdefault(fname, {})[u] = o
    todo = [(fname, u) for fname, d in defs.items() for u in sorted(d) if u in seeds or (fname in files and d[u]['type'] == FX_CLASS)]
    seen, lost = (set(), {})
    while todo:
        fname, u = todo.pop(0)
        if (fname, u) in seen:
            continue
        seen.add((fname, u))
        o = defs[fname][u]
        for r in refs(o['body'], kn):
            at = fname if r in defs[fname] else next((fn for fn in defs if r in defs[fn]), None)
            if at is not None:
                todo.append((at, r))
            elif r not in defined:
                lost.setdefault((fname, o['name']), []).append(r)
    for (fname, oname), rs in sorted(lost.items()):
        out.append(f"fx: {fname}/{oname} names {', '.join((_label(r) for r in rs))} - defined nowhere (what it spawns never resolves)")
    return out

def build_defined(datapc):
    import clone
    return set(clone.definitions_index(datapc)) | core_fx()

def persona_files(datapc, n):
    import clone
    from datafile import decompress
    from forge import Forge
    files = {}
    with Forge(datapc) as f:
        for r in f.records:
            if r.name and r.name.startswith(f'AC3MP_ID{n}_'):
                files.setdefault(r.name, []).extend(clone.objects_of(decompress(f.read(r))))
        want = {u for objs in files.values() for o in objs if o['body'][:2] == ANIM_HEAD for _t, u in fx_seeds(o['body'])} - ac3_defined()
        idx = clone.definitions_index(datapc)
        names = {fn for u in want for fn, _d in idx.get(u, ())}
        extra = {}
        for r in f.records:
            if r.name in names and r.name not in extra and (r.name not in files):
                extra[r.name] = clone.objects_of(decompress(f.read(r)))
    return (files, extra)

def check(c, dest):
    files, extra = persona_files(dest, c.new)
    return problems(files, build_defined(dest), extra)

def closure(roots, lookup, have):
    kn = known()
    out, missing, seen = ([], [], set())
    todo = [r for b in roots for r in refs(b, kn)]
    while todo:
        u = todo.pop(0)
        if u in seen or u in have:
            continue
        seen.add(u)
        o = lookup(u)
        if o is None:
            missing.append(u)
            continue
        out.append(o)
        todo += refs(o['body'], kn)
    return (out, missing)

def lacking_classes(objs):
    import exe_registry
    classes, _ = exe_registry.load(exe_registry.AC3)
    return [o for o in objs if o['type'] not in classes]

def renewed(objs, take, keep=()):
    import clone
    import exe_registry
    import id_census
    classes, _ = exe_registry.load(exe_registry.AC3)
    valid, bulk = id_census._class_tables()
    raw = clone.raw_types()
    tops = [uid_of(o) for o in objs]
    inner = []
    for o in objs:
        if o['type'] not in bulk and o['type'] not in raw:
            inner += sorted((u for u, cl in id_census.inline_ids(o['body'], valid) if u not in tops and classes.get(cl) not in KEEP_INLINE and (u not in keep) and (u not in ac3_defined())))
    mapping = {}
    for u in tops + inner:
        if u not in mapping:
            mapping[u] = take()
    out = []
    for o in objs:
        if o['type'] in raw:
            body, _n = clone.remap_at_sites(o['body'], mapping)
        else:
            body = o['body']
            for old, new in mapping.items():
                body = body.replace(struct.pack('<I', old), struct.pack('<I', new))
        out.append((o['type'], o['body_size'], o['name'].strip(), body))
    return (out, mapping)

def carry_spawns(objs, lookup, take, game='ACR', only=None, shared=None):
    have = {uid_of(o) for o in objs} | ac3_defined()
    sub = SUBSTITUTE.get(game, {})
    reps, adds, lines, mapping = ({}, [], [], {})
    for i, o in enumerate(objs):
        if o['type'] != FX_CLASS or (only is not None and uid_of(o) not in only):
            continue
        body = o['body']
        for old, new in sub.items():
            body = body.replace(struct.pack('<I', old), struct.pack('<I', new))
        body = _swap(body, mapping)
        need, missing = closure([body], lookup, have | set(mapping.values()))
        need = [x for x in need if uid_of(x) not in mapping]
        if not need and (not missing) and (body == o['body']):
            continue
        bad = lacking_classes(need)
        if missing or bad:
            lines.append(f"{o['name'].strip()}: NOT carried - " + (f"no {game} object {', '.join((_label(u) for u in missing))}" if missing else f"classes AC3 lacks: {', '.join((x['name'].strip() for x in bad))}"))
            continue
        if shared is not None and need and all((uid_of(x) in shared for x in need)):
            m = {uid_of(x): shared[uid_of(x)][0] for x in need}
            copies = [shared[uid_of(x)][1] for x in need]
        else:
            copies, m = renewed(need, take)
            if shared is not None:
                for x, cp in zip(need, copies):
                    shared[uid_of(x)] = (m[uid_of(x)], cp)
        mapping.update(m)
        adds += copies
        body = _swap(body, mapping)
        reps[i] = (o['type'], o['body_size'], o['name'].strip(), body)
        lines.append(f"{o['name'].strip()} + {len(copies)} objects it spawns ({', '.join((x[2] for x in copies))})" if copies else f"{o['name'].strip()} -> the copies carried above")
    return (reps, adds, lines)

def file_closure(body, by_uid):
    out, seen = ([], set())
    todo = refs(body, by_uid)
    while todo:
        u = todo.pop(0)
        if u in seen:
            continue
        seen.add(u)
        out.append(by_uid[u])
        todo += refs(by_uid[u]['body'], by_uid)
    return out

def base_twins(files, wanted):
    out = {}
    for _name, objs in files:
        if not wanted - set(out):
            break
        by_uid = {uid_of(o): o for o in objs if uid_of(o) is not None}
        for o in objs:
            u = uid_of(o)
            if o['type'] == FX_CLASS and u in wanted and (u not in out):
                out[u] = (o, file_closure(o['body'], by_uid))
    return out

def adopt_twins(objs, twins, only=None):
    by_uid = {uid_of(o): (i, o) for i, o in enumerate(objs) if uid_of(o) is not None}
    reps, adds, added, lines, old = ({}, [], set(), [], {})
    for i, o in enumerate(objs):
        u = uid_of(o)
        if o['type'] != FX_CLASS or u not in twins or (only is not None and u not in only):
            continue
        fx, clos = twins[u]
        if fx['body'] == o['body']:
            continue
        for x in file_closure(o['body'], {k: v for k, (_i, v) in by_uid.items()}):
            old[uid_of(x)] = by_uid[uid_of(x)][0]
        reps[i] = (fx['type'], fx['body_size'], fx['name'], fx['body'])
        n_new = 0
        for x in clos:
            k = uid_of(x)
            if k in by_uid:
                if by_uid[k][1]['body'] != x['body']:
                    raise ValueError(f"{x['name'].strip()} 0x{k:08X}: the file holds another body under the twin's uid")
                continue
            if k not in added:
                added.add(k)
                adds.append((x['type'], x['body_size'], x['name'], x['body']))
                n_new += 1
        lines.append(f"{o['name'].strip()} 0x{u:08X} -> the base's copy ({n_new} of its {len(clos)} objects added)")
    if not reps:
        return ({}, [], [], [])
    cand = set(old) - added
    frontier = [reps.get(i, (o['type'], o['body_size'], o['name'], o['body']))[3] for i, o in enumerate(objs) if uid_of(o) not in cand] + [a[3] for a in adds]
    alive = set()
    while frontier and cand - alive:
        among = cand - alive
        newly = set()
        for body in frontier:
            newly |= _u32s_in(body, among)
        alive |= newly
        frontier = [objs[old[k]]['body'] for k in newly]
    drop = cand - alive
    removals = sorted((old[k] for k in drop))
    if removals:
        lines.append(f"{len(removals)} former copies removed ({', '.join((objs[j]['name'].strip() for j in removals[:6]))}{(' ...' if len(removals) > 6 else '')})")
    return (reps, adds, removals, lines)

def _swap(body, mapping):
    if not mapping:
        return body
    b = bytearray(body)
    for k in range(2, len(b) - 3):
        if bytes(b[k - 2:k]) in REF_PREFIXES:
            u = struct.unpack_from('<I', b, k)[0]
            if u in mapping:
                struct.pack_into('<I', b, k, mapping[u])
    return bytes(b)
SEED_SUBSTITUTE = {'AC4': {2078169912: 1991278917, 2078169958: 1991278917, 2078170065: 1991278917, 2078169866: 1991278917, 2078170111: 1991278917}}

def acr_fx():
    if 'acr_fx' not in _cache:
        _cache['acr_fx'] = {u for u, v in _index('ACR').items() if v[1] == FX_CLASS}
    return _cache['acr_fx']

def playable(u):
    return u in ac3_defined() or u in acr_fx()

def seed_fx(body):
    if os.path.join(HERE, 'acbchars') not in sys.path:
        sys.path.append(os.path.join(HERE, 'acbchars'))
    import anim_cut
    fxseed = next((k for k, v in anim_cut.CLS.items() if v == 'FXEventSeed'))
    out = []
    trs, _td = anim_cut.tracks(body)
    for _s, _e, seeds, times, _en in trs:
        for (x, y), t in zip(seeds, times):
            if struct.unpack_from('<I', body, x + 5)[0] == fxseed and y - x >= FX_OBJECT_AT + 4:
                out.append((x, struct.unpack_from('<I', body, x + FX_OBJECT_AT)[0], t))
    return out

def wants_seeds(body, game='AC4'):
    sub = SEED_SUBSTITUTE.get(game, {})
    return any((playable(u) or u in sub for _x, u, _t in seed_fx(body)))

def seed_rule(body, game='AC4'):
    sub = SEED_SUBSTITUTE.get(game, {})
    seeds = seed_fx(body)
    firing = {(t, u) for _x, u, t in seeds if playable(u)}
    b, swapped = (bytearray(body), 0)
    for x, u, t in seeds:
        if not playable(u) and u in sub and ((t, sub[u]) not in firing):
            struct.pack_into('<I', b, x + FX_OBJECT_AT, sub[u])
            firing.add((t, sub[u]))
            swapped += 1
    body = bytes(b)
    dropped = [u for _x, u, _t in seed_fx(body) if not playable(u)]
    if dropped:
        if os.path.join(HERE, 'ac4chars') not in sys.path:
            sys.path.append(os.path.join(HERE, 'ac4chars'))
        import anim4
        body, _n = anim4.drop_seeds_where(body, lambda seg, cls: cls == 'FXEventSeed' and (len(seg) < FX_OBJECT_AT + 4 or not playable(struct.unpack_from('<I', seg, FX_OBJECT_AT)[0])))
    return (body, {'kept': len(seeds) - swapped - len(dropped), 'swapped': swapped, 'dropped': dropped})
