"""forgelib/crossmaps/acb_stage0.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acb
acb.patch()
import acb_configs
import convert
import inventory
import mapwalk
import mode_spawns
import spawn_layers
import world
import world_list
import xrefs
import acb_spawns
from build_static import mode_uids
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(acb.ROOT, 'out', 'crossmaps')
MODES = ('GameMode Deathmatch', 'GameMode EasyDeatmatch')
LAYER_DONOR = 'DataPC_ACFE_Constantinople.forge'
ENTRY, ENTRY_CLASS = (18, 62819144)
RESPAWN = ('Assassinate', 'CTF_2', 'Chest_Capture', 'Corruption', 'Escort', 'Manhunt', 'Wanted', 'Training', 'Deathmatch', 'Easy_Deathmatch')
TEAM = ('Chest_Capture', 'Escort', 'Manhunt')

def wdlm_entries(body):
    n = struct.unpack_from('<I', body, 10)[0]
    if 14 + ENTRY * n != len(body):
        raise SystemExit(f"WorldDataLayerManager: {n} entries don't fill {len(body)} bytes")
    out = [body[14 + ENTRY * k:14 + ENTRY * (k + 1)] for k in range(n)]
    for e in out:
        if struct.unpack_from('<II', e, 0) != (0, ENTRY_CLASS) or e[8] or e[13]:
            raise SystemExit(f"WorldDataLayerManager entry not in the known shape: {e.hex(' ')}")
    return out

def layer_of(e):
    return struct.unpack_from('<I', e, 9)[0]

def donor_layers():
    for p in mapwalk.payloads('ACR', LAYER_DONOR):
        for o in p.objects:
            if xrefs.cls('ACR', o['type']) == 'WorldDataLayerManager':
                return [layer_of(e) for e in wdlm_entries(o['body'])]
    raise SystemExit(f'no WorldDataLayerManager in {LAYER_DONOR}')

class Uids:

    def __init__(self, base, taken):
        self.next, self.taken = (base, taken)

    def __call__(self):
        while self.next in self.taken:
            self.next += 1
        self.taken.add(self.next)
        return self.next

def edit_world(body, modes, uid, report):
    ids = convert.class_ids()
    m = world.markers(body)
    names = [n for _k, n in m]
    if 'GameModeInfo' in names:
        raise SystemExit('the World already has a GameModeInfo: not an ACB World')
    at_count = world_list.list_start(body)
    tr = [k for k, n in m if n == 'TraineeGameplayCoordinatorWorldComponent']
    if len(tr) != 1 or body[tr[0] + 4] != 3:
        raise SystemExit('no single TraineeGameplayCoordinatorWorldComponent ending the list')
    at = tr[0] + 5
    tail = body[at:]
    if tail and (not (tail[0] == 0 and world.markers(tail[5:9] + b'\x00\x00\x00\x00'))):
        raise SystemExit(f"after the Trainee component: {tail[:16].hex(' ')} (expected the end or 00 uid class)")
    blob = b'\x00' + struct.pack('<IIfI', uid(), ids['GameModeInfo'], 1.0, len(modes))
    for mode in modes:
        blob += struct.pack('<IIHIf', uid(), ids['SupportedGameMode'], 1, mode, 1.0)
    out = bytearray(body[:at] + blob + tail)
    count = struct.unpack_from('<I', out, at_count)[0]
    struct.pack_into('<I', out, at_count, count + 1)
    report.append(f"World: GameModeInfo + {len(modes)} SupportedGameModes ({', '.join((f'0x{u:08X}' for u in modes))}) at +{at}" + (f' ahead of {len(tail)} bytes (DLC component)' if tail else ' (the end)') + f'; component count {count} -> {count + 1}')
    return bytes(out)

def edit_layers(body, order, uid, names, report):
    entries = wdlm_entries(body)
    have = {layer_of(e) for e in entries}
    pos = {u: i for i, u in enumerate(order)}
    missing = [u for u in order if u not in have]
    out = list(entries)
    for u in missing:
        before = [layer_of(e) for e in out]
        anchor = max((i for i, v in enumerate(before) if v in pos and pos[v] < pos[u]), default=-1)
        e = struct.pack('<II', 0, ENTRY_CLASS) + b'\x00' + struct.pack('<I', u) + b'\x00' + struct.pack('<I', uid())
        out.insert(anchor + 1, e)
    report.append(f'WorldDataLayerManager: {len(entries)} -> {len(out)} layers (+ ' + ', '.join((names.get(u, f'0x{u:08X}') for u in missing)) + ')')
    return (body[:10] + struct.pack('<I', len(out)) + b''.join(out), missing)
BENCH_TEMPLATE = ('DataPC_AC3MP_Boston_Mill.forge', 'AC3MP_MILL_Bench_01A_007')
STRIP_MEMBER = {'Ambient3dSoundComponent', 'FreeRunCornerTurnComponent'}
BEAM_PREFIX = ('AC2_GEN_WoodBeam', 'WoodBeam_', 'GEN_WoodBeam')
PI = struct.pack('<f', 3.14159274)
ELEVATOR_SPEED = struct.pack('<f', 5.0)

def strip_components(body, names):
    import entity
    comps = entity.components(body)
    if comps is None:
        raise SystemExit("an entity whose component list doesn't parse")
    out = bytearray(body)
    removed = []
    for i, (s_, e_, n) in reversed(list(enumerate(comps))):
        if n in names:
            del out[s_:e_ - 1 if i == len(comps) - 1 else e_]
            removed.append(n)
    if removed:
        count = struct.unpack_from('<I', out, entity.LIST_MARK + 1)[0]
        struct.pack_into('<I', out, entity.LIST_MARK + 1, count - len(removed))
    return (bytes(out), removed[::-1])

def bench_template():
    import entity
    for p in mapwalk.payloads('AC3', BENCH_TEMPLATE[0]):
        for o in p.objects:
            if o['name'] == BENCH_TEMPLATE[1] and xrefs.cls('AC3', o['type']) == 'Entity':
                comps = entity.components(o['body'])
                names = [n for _s, _e, n in comps]
                if names != ['RestObjectAttributeComponent', 'GameplayCoordinatorComponent', 'Visual', 'Visual', 'InertComponent', 'RigidBodyComponent', 'AIComponent']:
                    raise SystemExit(f'{BENCH_TEMPLATE[1]}: components {names}')
                s_, e_, _n = comps[1]
                gc = o['body'][s_:e_]
                ids = convert.class_ids()
                if struct.pack('<I', ids['GcLMPRestObject']) not in gc or gc[25:28] != b'\x03\x01\x00':
                    raise SystemExit(f'{BENCH_TEMPLATE[1]}: coordinator not GcLMPRestObject / World link not at +25')
                return (gc, o['uid'], struct.unpack_from('<I', gc, 28)[0])
    raise SystemExit(f'{BENCH_TEMPLATE[1]} not in {BENCH_TEMPLATE[0]}')

def bench_coordinator(gc, t_own, t_world, own, comp_uid, world_uid, alloc):
    out = bytearray(gc)
    fresh = {}
    for k in range(len(gc) - 3):
        v = struct.unpack_from('<I', gc, k)[0]
        d = v - t_own
        if 0 <= d < 256:
            if k == 2:
                new = comp_uid
            elif d == 0:
                new = own
            else:
                new = fresh.setdefault(d, alloc())
            struct.pack_into('<I', out, k, new)
    if struct.unpack_from('<I', out, 28)[0] != t_world:
        raise SystemExit('bench coordinator: World link moved')
    struct.pack_into('<I', out, 28, world_uid)
    if struct.pack('<I', t_world) in out:
        raise SystemExit("bench coordinator: names the template's World elsewhere")
    return bytes(out)

def rest_bench(body, own, gc_new, ids):
    import entity
    comps = entity.components(body)
    names = [n for _s, _e, n in comps]
    visuals = ['Visual'] * names.count('Visual')
    eagle = ['EagleVisionComponent'] if 'EagleVisionComponent' in names else []
    if not visuals or names != ['RestObjectAttributeComponent'] + visuals + ['TriggerComponent'] + eagle + ['MapMarkerComponent', 'InertComponent', 'RigidBodyComponent', 'AIComponent']:
        raise SystemExit(f'rest bench 0x{own:08X}: components {names}')
    olnet, olmp = (struct.pack('<I', ids[k]) for k in ('OLNetRestObject', 'OLMPRestObject'))
    els = []
    for s_, e_, n in comps:
        el = body[s_:e_]
        if n == 'TriggerComponent':
            el = gc_new(struct.unpack_from('<I', el, 2)[0])
        elif n in ('MapMarkerComponent', 'EagleVisionComponent'):
            continue
        elif n == 'AIComponent':
            if el.count(olnet) != 1:
                raise SystemExit(f'rest bench 0x{own:08X}: AIComponent without one OLNetRestObject')
            el = el.replace(olnet, olmp)
        els.append(el)
    out = bytearray(body[:comps[0][0]] + b''.join(els) + body[comps[-1][1]:])
    struct.pack_into('<I', out, entity.LIST_MARK + 1, len(els))
    got = [n for _s, _e, n in entity.components(bytes(out))]
    want = ['RestObjectAttributeComponent'] + visuals + ['GameplayCoordinatorComponent', 'InertComponent', 'RigidBodyComponent', 'AIComponent']
    if got != want:
        raise SystemExit(f'rest bench 0x{own:08X}: after the graft {got}')
    return (bytes(out), want)

def acr_entities(payloads, report, alloc):
    import entity
    from tree_stubs import merged_members
    ids = convert.class_ids()
    rest = struct.pack('<I', ids['RestOnBenchEvent'])
    multi = struct.pack('<I', ids['MultiInertComponent'])
    members = {u for p in payloads.values() for o in p.objects if xrefs.cls('ACR', o['type']) == 'Entity' for u in merged_members(o['body'], multi)}
    changed, expect, counts = (set(), {}, {})
    gc, t_own, t_world = bench_template()
    world_uid = next((o['uid'] for p in payloads.values() for o in p.objects if o['type'] == ids['World']))
    for idx, p in payloads.items():
        for o in p.objects:
            if xrefs.cls('ACR', o['type']) != 'Entity':
                continue
            comps = entity.components(o['body'])
            if not comps:
                continue
            names = [n for _s, _e, n in comps]
            body, kind = (o['body'], None)
            if 'ElevatorComponent' in names:
                s_, e_, _n = comps[names.index('ElevatorComponent')]
                el = body[s_:e_]
                if el.count(PI) != 1 or el.find(PI) != 48 or len(el) - (e_ == comps[-1][1]) != 122:
                    raise SystemExit(f"{o['name']}: an ElevatorComponent not in ACB's shape")
                body = body[:s_ + 52] + ELEVATOR_SPEED + body[s_ + 52:]
                body, removed = strip_components(body, {'Ambient3dSoundComponent'})
                kind = 'elevators'
            elif 'TriggerComponent' in names and rest in body[slice(*comps[names.index('TriggerComponent')][:2])]:
                own = o['uid']
                body, after = rest_bench(body, own, lambda cu: bench_coordinator(gc, t_own, t_world, own, cu, world_uid, alloc), ids)
                removed = ['TriggerComponent', 'MapMarkerComponent'] + [n for n in names if n == 'EagleVisionComponent']
                kind = 'rest benches'
            elif o['has_uid'] and o['uid'] in members and STRIP_MEMBER & set(names):
                body, removed = strip_components(body, STRIP_MEMBER)
                kind = 'merged-collision members'
            elif (o['name'] or '').startswith(BEAM_PREFIX) and 'Ambient3dSoundComponent' in names:
                body, removed = strip_components(body, {'Ambient3dSoundComponent'})
                kind = 'beams (not merged members)'
            if kind:
                o['body_size'] += len(body) - len(o['body'])
                o['body'] = body
                changed.add(idx)
                expect[o['uid']] = after if kind == 'rest benches' else [n for n in names if n not in removed]
                c = counts.setdefault(kind, [0, set()])
                c[0] += 1
                c[1].update(removed)
    for kind, (n, removed) in counts.items():
        report.append(f"{kind}: {n} entities, stripped {', '.join(sorted(removed))}" + (' and ElevatorComponent + f32 5.0 after pi' if kind == 'elevators' else '') + (f"; {BENCH_TEMPLATE[1]}'s GcLMPRestObject coordinator in the Trigger's place, AI OLNetRestObject -> OLMPRestObject" if kind == 'rest benches' else ''))
    return (changed, expect)

def acr_formats(payloads, report, uid):
    import acb_rules
    from collections import Counter
    want = {(o['uid'], o['name']) for p in payloads.values() for o in p.objects if o['has_uid'] and xrefs.cls('ACR', o['type']) in acb_rules.FROM_ACR}
    clips = {(o['name'], acb_rules.clip_id(o['body'])) for p in payloads.values() for o in p.objects if o['type'] == acb_rules.ANIMATION}
    acr, acr_clips = acb_rules.acr_copies(want, clips)
    missing = sorted(clips - set(acr_clips))
    if missing:
        raise SystemExit(f"{len(missing)} Animations ACR doesn't ship (ACB's clips are ACR-re-encoded, no rule): {missing[:5]}")
    import uid_index
    from import_ac3 import refs
    defined = {o['uid'] for p in payloads.values() for o in p.objects if o['has_uid']}
    known = set(inventory.universe('ACR')) | set(inventory.universe('AC3'))
    ac3_shared = set()
    for s_ in ('DataPC.forge', 'DataPC_extra.forge', 'DataPC_CoreFX.forge', 'DataPC_Fire.forge', 'DataPC_extraparams.forge'):
        ac3_shared |= set(uid_index.load('AC3', s_)[0])
    open_refs = Counter()
    for u in list(acr):
        miss = {v for v in refs(acr[u][0]) if v in known} - defined - ac3_shared - acb_rules.CHAIN_IMPORTS
        if miss:
            open_refs[next((x['name'] for p in payloads.values() for x in p.objects if x['has_uid'] and x['uid'] == u))] += 1
            del acr[u]
    changed, taken, rules, kept = (set(), Counter(), Counter(), Counter())
    for idx, p in payloads.items():
        for o in p.objects:
            cls = xrefs.cls('ACR', o['type'])
            copy = None
            if o['type'] == acb_rules.ANIMATION:
                copy = acr_clips[o['name'], acb_rules.clip_id(o['body'])]
                cls = 'Animation'
            elif cls in acb_rules.FROM_ACR and o['has_uid'] and (o['uid'] in acr):
                copy = acr[o['uid']]
            if copy is not None:
                body, body_size = copy
                if body == o['body']:
                    kept[cls] += 1
                    continue
                taken[cls] += 1
                o['body'], o['body_size'] = (body, body_size)
                changed.add(idx)
                continue
            if cls == 'Skeleton' and acb_rules.UNRULED & {n for n, _s in world.segments(o['body'])}:
                raise SystemExit(f"{o['name']}: a Skeleton with Hinge / CompressBoneModifier that ACR doesn't ship")
            body, log = acb_rules.convert(cls, o['body'], uid)
            rules.update(log)
            if body == o['body']:
                continue
            o['body_size'] += len(body) - len(o['body'])
            o['body'] = body
            changed.add(idx)
    report.append("ACR's own copies: " + ', '.join((f'{c} {n}' for c, n in sorted(taken.items()))) + ' (already identical: ' + ', '.join((f'{c} {n}' for c, n in sorted(kept.items()))) + ')')
    report.append('ACB -> ACR formats: ' + ', '.join((f'{r} {n}' for r, n in sorted(rules.items()))))
    report.append(f'ACR copies left out (references outside the map): {sum(open_refs.values())} {sorted(open_refs)[:8]}')
    return changed

def drop_chase_breakers(payloads, report, alloc, archive):
    import acb_chasebreakers
    from build_static import clean_cell
    import chase_breaker_check
    built, _left = acb_chasebreakers.for_map(archive, alloc)
    acr_form = {u: b for _n, u, _c, b, _ref, _k in built}
    for n, u, _c, b, _ref, _k in built:
        bad = chase_breaker_check.check_group(u, b)
        if bad:
            raise SystemExit(f"step 8: {n} wired wrong: {'; '.join(bad)}")
    changed, gone, rebuilt = (set(), [], [])
    for idx, p in payloads.items():
        drop = set()
        for o in p.objects[1:]:
            if o['has_uid'] and xrefs.cls('ACR', o['type']) == 'EntityGroup':
                segs = {n for n, _s in world.segments(o['body'])}
                if 'ChaseBreakerEventSeed' in segs or ({'Scene', 'LogicActor'} <= segs and 'Pigeon' not in o['name']):
                    if o['uid'] in acr_form:
                        body = acr_form[o['uid']]
                        o['body_size'] += len(body) - len(o['body'])
                        o['body'] = body
                        rebuilt.append(o['name'])
                        changed.add(idx)
                    else:
                        drop.add(o['uid'])
                        gone.append(o['name'])
        if not drop:
            continue
        head = p.objects[0]
        if xrefs.cls('ACR', head['type']) != 'GridCellDataBlock':
            raise SystemExit(f'{p.rec.name}: a chase breaker outside a cell')
        body, removed = clean_cell(head['body'], drop)
        if removed != len(drop):
            raise SystemExit(f'{p.rec.name}: {len(drop)} chase breakers but {removed} list entries')
        head['body_size'] += len(body) - len(head['body'])
        head['body'] = body
        p.objects = [o for o in p.objects if not (o['has_uid'] and o['uid'] in drop)]
        changed.add(idx)
    if len(rebuilt) != len(acr_form):
        raise SystemExit(f'step 8: {len(acr_form)} ACR forms built, {len(rebuilt)} placed')
    report.append(f"chase breakers in ACR's form: {len(rebuilt)} ({', '.join(sorted({g.rsplit('_', 1)[0] for g in rebuilt}))}); left out (no ACR template): {len(gone)} ({', '.join(sorted({g.rsplit('_', 1)[0] for g in gone}))})")
    return changed
ACR_ADDITIONS_FROM = 'DataPC_AC2MP_Firenze_dlc.forge'
ACR_ADDED_FILES = ('ACFE_Characters_Eye',)
ACR_ADDED_OBJECTS = {'AC2MP_M_Head_Fat': (1613785084,)}
ACR_ADDED_FROM = {'AC2MP_CF_Merchants_M_B_Spanish': ('AC2MP_CF_Merchants_M_B', (1985774593,))}

def acr_additions(payloads, image, report):
    from forge_write import add_file
    defined = {o['uid'] for p in payloads.values() for o in p.objects if o['has_uid']}
    by_name = {p.rec.name: p for p in payloads.values()}
    rec_uids = {p.rec.uid for p in payloads.values()}
    changed, lines = (set(), [])
    with Forge(mapwalk.archive_path('ACR', ACR_ADDITIONS_FROM)) as f:
        recs = {r.name: r for r in f.records if r.size}
        for name in ACR_ADDED_FILES:
            if name in by_name:
                continue
            r = recs[name]
            raw = f.read(r)
            src = mapwalk.Payload(r, raw)
            if any((o['has_uid'] and o['uid'] in defined for o in src.objects)):
                raise SystemExit(f'step 6b: {name} defines a uid the map has')
            if r.uid in rec_uids:
                raise SystemExit(f"step 6b: {name}'s record uid is one of the map's")
            tmpl = next((p.rec.index for p in payloads.values() if p.rec.name in ACR_ADDED_OBJECTS))
            add_file(image, name, raw, r.uid, template_index=tmpl, entry_key=src.objects[0]['uid'])
            defined |= {o['uid'] for o in src.objects if o['has_uid']}
            lines.append(f"file {name} ({', '.join((o['name'] for o in src.objects))})")
        for name, uids in ACR_ADDED_OBJECTS.items():
            p = by_name[name]
            src = mapwalk.Payload(recs[name], f.read(recs[name]))
            for u in uids:
                if u in defined:
                    continue
                o = next((x for x in src.objects if x['has_uid'] and x['uid'] == u))
                if o['filter'] is not None and src.filter_table != p.filter_table:
                    raise SystemExit(f"step 6b: {o['name']} has a filter and the tables differ")
                p.objects.append(o)
                defined.add(u)
                changed.add(p.rec.index)
                lines.append(f"{name} + {o['name']}")
        for name, (src_name, uids) in ACR_ADDED_FROM.items():
            if name not in by_name:
                continue
            p = by_name[name]
            src = mapwalk.Payload(recs[src_name], f.read(recs[src_name]))
            for u in uids:
                if u in defined:
                    continue
                o = next((x for x in src.objects if x['has_uid'] and x['uid'] == u))
                if o['filter'] is not None and src.filter_table != p.filter_table:
                    raise SystemExit(f"step 6b: {o['name']} has a filter and the tables differ")
                p.objects.append(o)
                defined.add(u)
                changed.add(p.rec.index)
                lines.append(f"{name} + {o['name']} (from {src_name})")
    report.append(f"ACR's additions (from {ACR_ADDITIONS_FROM}): " + ('; '.join(lines) or 'none needed'))
    return changed

def dlc_descriptors(payloads, alloc, report):
    ids = convert.class_ids()
    in_ac3 = set(inventory.universe('AC3'))
    worlds = [(p, o) for p in payloads.values() for o in p.objects if o['type'] == ids['MpWorld'] and o['has_uid'] and (o['uid'] in in_ac3)]
    if not worlds:
        return set()
    pkgs = [(p, o) for p in payloads.values() for o in p.objects if o['type'] == ids['ContentPackage']]
    if len(pkgs) != 1:
        raise SystemExit(f'step 6c: {len(pkgs)} ContentPackage objects')
    changed, lines, renew = (set(), [], {})
    for p, o in worlds:
        old, new = (o['uid'], alloc())
        if struct.unpack_from('<I', o['body'], 2)[0] != old:
            raise SystemExit(f"step 6c: {o['name']}'s uid is not at body + 2")
        o['body'] = o['body'][:2] + struct.pack('<I', new) + o['body'][6:]
        o['uid'] = new
        renew[old] = new
        changed.add(p.rec.index)
        lines.append(f"{o['name']} {old:#010x} -> {new:#010x}")
    pp, po = pkgs[0]
    body = po['body']
    for old, new in renew.items():
        if body.count(struct.pack('<I', old)) != 1:
            raise SystemExit(f"step 6c: the descriptor names {old:#010x} {body.count(struct.pack('<I', old))} times")
        body = body.replace(struct.pack('<I', old), struct.pack('<I', new))
    po['body'] = body
    changed.add(pp.rec.index)
    for p in payloads.values():
        for o in p.objects:
            if o is not po and (not o['type'] == ids['MpWorld']) and any((struct.pack('<I', u) in o['body'] for u in renew)):
                raise SystemExit(f"step 6c: {o['name']} names an old MpWorld uid")
    report.append('DLC descriptors: ' + '; '.join(lines) + " (AC3's DataPC holds the old uids; the descriptor follows)")
    return changed

def tod_dedupe(payloads, report):
    import tod_evening
    import tod_snow
    changed, lines = (set(), [])
    for p in payloads.values():
        tod = next((o for o in p.objects if o['type'] == tod_snow.TOD), None)
        if tod is None:
            continue
        b = tod['body']
        ents = tod_snow.entries(b)
        out, n_fixed = (bytearray(b[:ents[0][0]]), 0)
        for s_, e_, path in ents:
            seg = b[s_:e_]
            c = tod_evening.curve(seg)
            if c:
                k, cls, keys, vals, end = c
                keep = [i for i in range(len(keys)) if i == 0 or keys[i] != keys[i - 1]]
                if len(keep) != len(keys):
                    fmt = tod_evening.KINDS[cls][0]
                    nk = [keys[i] for i in keep]
                    body = struct.pack('<II', cls, len(nk)) + struct.pack(f'<{len(nk) - 1}H', *nk[1:])
                    body += b''.join((struct.pack(fmt, *vals[i]) for i in keep))
                    seg = seg[:k] + body + seg[end:]
                    n_fixed += 1
                    lines.append('/'.join((f'{x:08X}' for x in path)))
            out += seg
        if n_fixed:
            if ents[-1][1] != len(b):
                raise SystemExit('step 6d: bytes after the last time-of-day entry')
            tod['body_size'] += len(out) - len(b)
            tod['body'] = bytes(out)
            changed.add(p.rec.index)
    if lines:
        report.append(f"time-of-day curves with a repeated key, the duplicate dropped: {', '.join(lines)}")
    return changed
MERCHANT_PLANE = 'AC2MP_Merchant_Orator_plane_'
PLANE_REACH = 1.5
BENCH_PLANE = 'AC2MP_Bench_plane'
BENCH_PLANE_REACH = 2.5

def snap_bench_planes(payloads, report):
    import math
    ents = [(p, o) for p in payloads.values() for o in p.objects if xrefs.cls('ACR', o['type']) == 'Entity' and len(o['body']) > 74]
    import entity
    benches = [(o['name'], o['body'][10:74]) for _p, o in ents if 'RestObjectAttributeComponent' in [c[2] for c in entity.components(o['body']) or []]]
    planes = [(p, o) for p, o in ents if (o['name'] or '').startswith(BENCH_PLANE)]
    if not planes:
        return set()
    changed, snapped, left = (set(), [], [])
    for p, o in planes:
        pm = struct.unpack_from('<16f', o['body'], 10)
        best = None
        for bn, bmat in benches:
            m = struct.unpack('<16f', bmat)
            dv = [pm[12 + j] - m[12 + j] for j in range(3)]
            lx, ly = (sum((dv[j] * m[4 * r + j] for j in range(3))) for r in (0, 1))
            turn = math.degrees(math.atan2(sum((pm[j] * m[4 + j] for j in range(3))), sum((pm[j] * m[j] for j in range(3)))))
            if 0 <= lx <= BENCH_PLANE_REACH and abs(ly) < 0.5 and (min(abs(turn), 180 - abs(turn)) < 10):
                if best is None or lx < best[0]:
                    best = (lx, bn, bmat)
        if best is None:
            left.append(o['name'])
            continue
        o['body'] = o['body'][:10] + best[2] + o['body'][74:]
        changed.add(p.rec.index)
        snapped.append(f"{o['name']} -> {best[1]} ({best[0]:.2f} m)")
    report.append(f"bench marker planes on their benches' placement: {len(snapped)} ({'; '.join(snapped)}); no bench: {len(left)} {left}")
    return changed

def drop_merchant_planes(payloads, report):
    import math
    import acb_merchants
    from build_static import clean_cell
    ents = [(p, o) for p in payloads.values() for o in p.objects if xrefs.cls('ACR', o['type']) == 'Entity']
    origins = {o['name']: acb_merchants.stall_origin(o['body'][10:74]) for _p, o in ents if (o['name'] or '').startswith(acb_merchants.PREFIX)}
    origins.update({o['name']: acb_merchants.stall_origin(o['body'][10:74], acb_merchants.ORATOR_SPEAKER_X) for _p, o in ents if (o['name'] or '').startswith(acb_merchants.ORATOR_PREFIX)})
    planes = [(p, o, struct.unpack_from('<3f', o['body'], 58)) for p, o in ents if (o['name'] or '').startswith(MERCHANT_PLANE)]
    drop, lines = ({}, [])
    for m, s in sorted(origins.items()):
        near = sorted(((math.dist(pos, s), p.rec.index, o) for p, o, pos in planes))
        if not near or near[0][0] > PLANE_REACH:
            lines.append(f'{m} none within {PLANE_REACH} m')
            continue
        d, idx, o = near[0]
        drop.setdefault(idx, set()).add(o['uid'])
        lines.append(f"{m} {o['name']} {d:.2f} m")
    uids = {struct.pack('<I', u) for us in drop.values() for u in us}
    for p in payloads.values():
        for o in p.objects[1:]:
            if not (o['has_uid'] and struct.pack('<I', o['uid']) in uids) and any((u in o['body'] for u in uids)):
                raise SystemExit(f"step 10: {o['name']} names a merchant plane")
    changed = set()
    for idx, us in drop.items():
        p = payloads[idx]
        head = p.objects[0]
        body, removed = clean_cell(head['body'], us)
        if removed != len(us):
            raise SystemExit(f'{p.rec.name}: {len(us)} merchant planes but {removed} list entries')
        head['body_size'] += len(body) - len(head['body'])
        head['body'] = body
        p.objects = [o for o in p.objects if not (o['has_uid'] and o['uid'] in us)]
        changed.add(idx)
    report.append(f'merchant / orator planes left out: {sum(map(len, drop.values()))} of {len(origins)} NPCs (' + '; '.join(lines) + ')')
    return changed
WORLD_TEMPLATES = {1921337557: 'AC2_Tex1_PixelLit'}

def world_templates(wp, report):
    import acb_rules
    from import_ac3 import refs
    have = {o['uid'] for o in wp.objects if o['has_uid']}
    named = set()
    for o in wp.objects:
        if xrefs.cls('ACR', o['type']) == 'Material':
            named |= refs(o['body']) & set(WORLD_TEMPLATES)
    want = named - have
    if not want:
        return False
    for a in mapwalk.map_archives('ACR'):
        if a in acb.ACB_MAPS or not want:
            continue
        for p in mapwalk.payloads('ACR', a, skip_errors=True):
            for o in p.objects:
                if o['has_uid'] and o['uid'] in want and (o['name'] == WORLD_TEMPLATES[o['uid']]):
                    at = next((i for i, x in enumerate(wp.objects) if xrefs.cls('ACR', x['type']) == 'MaterialTemplate'))
                    wp.objects.insert(at, dict(o))
                    want.discard(o['uid'])
                    report.append(f"World file: ACR's {o['name']} (0x{o['uid']:08X}, from {a}) inserted at #{at}, before {wp.objects[at + 1]['name']}")
            if not want:
                break
    if want:
        raise SystemExit(f'no ACR copy of {sorted((hex(u) for u in want))}')
    return True

def step4_oob(payloads, oob, acfe, image, changed, report):
    oob_cell, oob_entity = oob
    op = next((p for p in payloads.values() if p.rec.name == oob_cell))
    table = op.filter_table
    sets = spawn_layers.filter_sets(table) if table else []
    layers = frozenset((acfe[m] for m in RESPAWN))
    hit = [i for i, s_ in enumerate(sets) if s_ == set(layers)]
    if hit:
        oob_filter = hit[0]
    else:
        table, oob_filter = mode_spawns.add_filter(table, layers)
    ents = [o for o in op.objects if o['name'] == oob_entity and xrefs.cls('ACR', o['type']) == 'Entity']
    if len(ents) != 1 or ents[0]['filter'] is not None or b'OutOfBounds' not in b''.join((o['name'].encode() for o in op.objects)):
        raise SystemExit(f'{oob_cell}: expected one unfiltered {oob_entity} beside its OutOfBounds objects')
    objs = [(o['serial'], mapwalk.object_bytes(o), oob_filter if o is ents[0] else o['filter']) for o in op.objects]
    d = mapwalk.rebuild(objs, table)
    set_payload(image, op.rec.index, raw_payload(op, d))
    changed[op.rec.index] = op
    report.append(f"{oob_cell}: {oob_entity} -> filter {oob_filter} (the respawns' layers)")
    return op

def raw_payload(p, d):
    c = compress(d, first_chunk=mapwalk.first_chunk(d))
    assert decompress(c) == d
    if not p.prefix:
        return c
    assert c[:4] == b'\x00\x00\x00\x00'
    return struct.pack('<I', len(p.prefix)) + b''.join((struct.pack('<II', u, k) for u, k in p.prefix)) + c[4:]

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('map', choices=sorted(acb_configs.MAPS))
    ap.add_argument('--name', required=True, help='prefix: out/crossmaps/<name>_<tag>/')
    ap.add_argument('--uid-base', type=lambda v: int(v, 0), default=None, help="first uid for new World / layer elements (default: the map's range + 0xF000)")
    a = ap.parse_args(argv)
    cfg = acb_configs.MAPS[a.map]
    archive, root_cell = (cfg['archive'], cfg['root'])
    src = os.path.join(acb.ACB, archive)
    names = acb_spawns.layer_names()
    by_name = {}
    for u, n in names.items():
        by_name.setdefault(n, u)
    acfe = {m: by_name[f'ACFE_{m}'] for m in set(RESPAWN) | set(TEAM) | {'CTF_2'}}
    taken = set(inventory.universe('ACR')) | set(inventory.universe('AC3'))
    uid = Uids(a.uid_base or cfg['uid_base'] + 61440, taken)
    modes = mode_uids(list(MODES))
    order = donor_layers()
    report = []
    image = ArchiveImage.read(src)
    changed = {}
    with Forge(src) as f:
        payloads = {r.index: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}
    ids = convert.class_ids()
    changed5, expect5 = acr_entities(payloads, report, uid)
    changed5 |= snap_bench_planes(payloads, report)
    changed5 |= acr_formats(payloads, report, uid)
    changed5 |= acr_additions(payloads, image, report)
    changed5 |= dlc_descriptors(payloads, uid, report)
    changed5 |= tod_dedupe(payloads, report)
    changed5 |= drop_chase_breakers(payloads, report, uid, archive)
    changed5 |= drop_merchant_planes(payloads, report)
    wp9 = next((p for p in payloads.values() if any((o['type'] == convert.class_ids()['World'] for o in p.objects))))
    if world_templates(wp9, report):
        changed5.add(wp9.rec.index)
    wp = next((p for p in payloads.values() if any((o['type'] == ids['World'] for o in p.objects))))
    objs = []
    for o in wp.objects:
        body = o['body']
        if o['type'] == ids['World']:
            body = edit_world(body, modes, uid, report)
        elif xrefs.cls('ACR', o['type']) == 'WorldDataLayerManager':
            body, added_layers = edit_layers(body, order, uid, names, report)
        objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
    d = mapwalk.rebuild(objs, wp.filter_table)
    set_payload(image, wp.rec.index, raw_payload(wp, d))
    changed[wp.rec.index] = wp
    cp = next((p for p in payloads.values() if p.rec.name == root_cell))
    table = cp.filter_table
    sets = spawn_layers.filter_sets(table) if table else []
    fidx, counts, objs = ({}, {}, [])
    for o in cp.objects:
        filt = o['filter']
        k = spawn_layers.spawn_kind(o['body']) if xrefs.cls('ACR', o['type']) == 'Entity' else None
        if k and filt is None and (k[0] in (0, 2)):
            if k[0] == 0:
                want = RESPAWN
            elif k[1] in (1, 2):
                want = TEAM + ('CTF_2',)
            else:
                want = TEAM
            layers = frozenset((acfe[m] for m in want))
            if layers not in fidx:
                hit = [i for i, s in enumerate(sets) if s == set(layers)]
                if hit:
                    fidx[layers] = hit[0]
                else:
                    table, fidx[layers] = mode_spawns.add_filter(table, layers)
                    sets.append(set(layers))
            filt = fidx[layers]
            counts[k, want] = counts.get((k, want), 0) + 1
        elif k and filt is not None:
            raise SystemExit(f"{o['name']}: a spawn that already has a layer - not an ACB root cell")
        objs.append((o['serial'], mapwalk.object_bytes(o), filt))
    d = mapwalk.rebuild(objs, table)
    set_payload(image, cp.rec.index, raw_payload(cp, d))
    changed[cp.rec.index] = cp
    for (k, want), n in sorted(counts.items()):
        report.append(f"{root_cell}: {n} spawns type {k[0]} index {k[1]} -> filter {fidx[frozenset((acfe[m] for m in want))]} ({', '.join(want)})")
    op = None
    if cfg['oob']:
        op = step4_oob(payloads, cfg['oob'], acfe, image, changed, report)
    for idx in sorted(changed5 - set(changed)):
        p = payloads[idx]
        d = mapwalk.rebuild([(o['serial'], mapwalk.object_bytes(o), o['filter']) for o in p.objects], p.filter_table)
        set_payload(image, idx, raw_payload(p, d))
        changed[idx] = p
    out_dir = os.path.join(OUT, f"{a.name}_{cfg['tag']}")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, acb.stage0_name(archive))
    size = write(dest, image)
    v = {}
    with Forge(dest) as f, Forge(src) as g:
        v['others identical'] = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index not in changed))
        for idx, old in changed.items():
            q = mapwalk.Payload(f.records[idx], f.read(f.records[idx]))
            v[f'#{idx} prefix kept'] = q.prefix == old.prefix
            v[f'#{idx} objects'] = len(q.objects) == len(old.objects)
            for o in q.objects:
                segs = {n for n, _s in world.segments(o['body'])} if xrefs.cls('ACR', o['type']) == 'EntityGroup' else set()
                if 'ChaseBreakerEventSeed' in segs and 'ChaseBreakerComponent' not in segs:
                    v['no ACB-form chase breaker left'] = False
            for o in q.objects:
                if o['type'] == ids['World']:
                    m = world.markers(o['body'])
                    sgm = [struct.unpack_from('<I', o['body'], k + 6)[0] for k, n in m if n == 'SupportedGameMode']
                    v['World modes'] = sgm == modes
                    v['World count +1'] = struct.unpack_from('<I', o['body'], world_list.list_start(o['body']))[0] == struct.unpack_from('<I', next((x for x in old.objects if x['type'] == ids['World']))['body'], world_list.list_start(next((x for x in old.objects if x['type'] == ids['World']))['body']))[0] + 1
                elif xrefs.cls('ACR', o['type']) == 'WorldDataLayerManager':
                    ls = [layer_of(e) for e in wdlm_entries(o['body'])]
                    v['layers: every ACR layer, once'] = set(order) <= set(ls) and len(ls) == len(set(ls))
            if idx == wp.rec.index:
                have = {o['uid'] for o in q.objects if o['has_uid']}
                v["World file holds its materials' pixel-lit template"] = all((u in have for u in WORLD_TEMPLATES))
            if idx == cp.rec.index:
                qs = spawn_layers.filter_sets(q.filter_table)
                spawns_ok = True
                for o in q.objects:
                    k = spawn_layers.spawn_kind(o['body']) if xrefs.cls('ACR', o['type']) == 'Entity' else None
                    if k and k[0] in (0, 2):
                        want = RESPAWN if k[0] == 0 else TEAM + (('CTF_2',) if k[1] in (1, 2) else ())
                        spawns_ok &= o['filter'] is not None and qs[o['filter']] == {acfe[m] for m in want}
                v['root spawns on their layers'] = spawns_ok
            if op is not None and idx == op.rec.index:
                qs = spawn_layers.filter_sets(q.filter_table)
                e = next((o for o in q.objects if o['name'] == cfg['oob'][1] and xrefs.cls('ACR', o['type']) == 'Entity'))
                v['OOB entity on the mode layers'] = e['filter'] is not None and qs[e['filter']] == {acfe[m] for m in RESPAWN}
            for o in q.objects:
                if o['has_uid'] and o['uid'] in expect5:
                    import entity
                    got = [n for _s, _e, n in entity.components(o['body']) or []]
                    v.setdefault('step-5 entities parse, components as planned', True)
                    v['step-5 entities parse, components as planned'] &= got == expect5[o['uid']]
                    if 'ElevatorComponent' in got:
                        el = next((o['body'][a_:b_] for a_, b_, n in entity.components(o['body']) if n == 'ElevatorComponent'))
                        v.setdefault("elevators in ACR's shape", True)
                        v["elevators in ACR's shape"] &= el[48:56] == PI + ELEVATOR_SPEED
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + f'\nverify: {v}\n')
    print('\n'.join(report))
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('verify:', v)
    if not all(v.values()):
        raise SystemExit('verify failed')
if __name__ == '__main__':
    main(sys.argv[1:])
