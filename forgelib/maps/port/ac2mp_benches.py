"""forgelib/maps/port/ac2mp_benches.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT, os.path.join(ROOT, 'crossmaps')):
    if p not in sys.path:
        sys.path.insert(0, p)
import configs
import convert
import entity
import inventory
import mapwalk
import xrefs
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')

def rest_bench_guided(body, own, gc_new, ids):
    import acb_stage0
    comps = entity.components(body)
    names = [n for _s, _e, n in comps]
    k = names.count('GuidanceSystem')
    if not k:
        return acb_stage0._rest_bench_orig(body, own, gc_new, ids)
    visuals = ['Visual'] * names.count('Visual')
    guid = ['GuidanceSystem'] * k
    if not visuals or names != ['RestObjectAttributeComponent'] + visuals + ['TriggerComponent'] + guid + ['MapMarkerComponent', 'InertComponent', 'RigidBodyComponent', 'AIComponent']:
        raise SystemExit(f'rest bench 0x{own:08X}: components {names}')
    olnet, olmp = (struct.pack('<I', ids[c]) for c in ('OLNetRestObject', 'OLMPRestObject'))
    els = []
    for s_, e_, n in comps:
        el = body[s_:e_]
        if n == 'TriggerComponent':
            el = gc_new(struct.unpack_from('<I', el, 2)[0])
        elif n == 'MapMarkerComponent':
            continue
        elif n == 'AIComponent':
            if el.count(olnet) != 1:
                raise SystemExit(f'rest bench 0x{own:08X}: AIComponent without one OLNetRestObject')
            el = el.replace(olnet, olmp)
        els.append(el)
    out = bytearray(body[:comps[0][0]] + b''.join(els) + body[comps[-1][1]:])
    struct.pack_into('<I', out, entity.LIST_MARK + 1, len(els))
    want = ['RestObjectAttributeComponent'] + visuals + ['GameplayCoordinatorComponent'] + guid + ['InertComponent', 'RigidBodyComponent', 'AIComponent']
    got = [n for _s, _e, n in entity.components(bytes(out))]
    if got != want:
        raise SystemExit(f'rest bench 0x{own:08X}: after the graft {got}')
    return (bytes(out), want)
ACR_ELEVATOR_LEN = 126
ACR_STRIP_MEMBER = {'Ambient3dSoundComponent'}

def acr_entities(payloads, report, alloc):
    import acb_stage0 as s0
    from tree_stubs import merged_members
    ids = convert.class_ids()
    rest = struct.pack('<I', ids['RestOnBenchEvent'])
    multi = struct.pack('<I', ids['MultiInertComponent'])
    members = {u for p in payloads.values() for o in p.objects if xrefs.cls('ACR', o['type']) == 'Entity' for u in merged_members(o['body'], multi)}
    changed, expect, counts = (set(), {}, {})
    gc, t_own, t_world = s0.bench_template()
    world_uid = next((o['uid'] for p in payloads.values() for o in p.objects if o['type'] == ids['World']))
    for idx, p in payloads.items():
        for o in p.objects:
            if xrefs.cls('ACR', o['type']) != 'Entity':
                continue
            comps = entity.components(o['body'])
            if not comps:
                continue
            names = [n for _s, _e, n in comps]
            body, kind, after = (o['body'], None, None)
            if 'ElevatorComponent' in names:
                s_, e_, _n = comps[names.index('ElevatorComponent')]
                el = body[s_:e_]
                if len(el) - (e_ == comps[-1][1]) == ACR_ELEVATOR_LEN and el[52:56] == s0.ELEVATOR_SPEED:
                    body, removed = s0.strip_components(body, {'Ambient3dSoundComponent'})
                    kind = "elevators (ACR's form, kept)"
                elif el.count(s0.PI) == 1 and el.find(s0.PI) == 48 and (len(el) - (e_ == comps[-1][1]) == 122):
                    body = body[:s_ + 52] + s0.ELEVATOR_SPEED + body[s_ + 52:]
                    body, removed = s0.strip_components(body, {'Ambient3dSoundComponent'})
                    kind = "elevators (ACB's form)"
                else:
                    raise SystemExit(f"{o['name']}: an ElevatorComponent in neither ACR's nor ACB's shape")
            elif 'TriggerComponent' in names and rest in body[slice(*comps[names.index('TriggerComponent')][:2])]:
                own = o['uid']
                body, after = s0.rest_bench(body, own, lambda cu, own=own: s0.bench_coordinator(gc, t_own, t_world, own, cu, world_uid, alloc), ids)
                removed = ['TriggerComponent', 'MapMarkerComponent']
                kind = 'rest benches'
            elif o['has_uid'] and o['uid'] in members and ACR_STRIP_MEMBER & set(names):
                body, removed = s0.strip_components(body, ACR_STRIP_MEMBER)
                kind = 'merged-collision members'
            if kind:
                o['body_size'] += len(body) - len(o['body'])
                o['body'] = body
                changed.add(idx)
                expect[o['uid']] = after if kind == 'rest benches' else [n for n in names if n not in removed]
                c = counts.setdefault(kind, [0, set()])
                c[0] += 1
                c[1].update(removed)
    for kind, (n, removed) in counts.items():
        report.append(f"{kind}: {n} entities, stripped {', '.join(sorted(removed))}" + (' and ElevatorComponent + f32 5.0 after pi' if kind == "elevators (ACB's form)" else '') + (f"; {s0.BENCH_TEMPLATE[1]}'s GcLMPRestObject coordinator in the Trigger's place, AI OLNetRestObject -> OLMPRestObject" if kind == 'rest benches' else ''))
    return (changed, expect)

def patch_benches():
    import acb_stage0
    if acb_stage0.rest_bench is not rest_bench_guided:
        acb_stage0._rest_bench_orig = acb_stage0.rest_bench
        acb_stage0.rest_bench = rest_bench_guided

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--archive', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--uid-base', type=lambda v: int(v, 0), default=None)
    a = ap.parse_args(argv)
    import acb_stage0
    patch_benches()
    key = next((k for k, c in configs.MAPS.items() if c['archive'] == a.archive))
    cfg = configs.MAPS[key]
    base = a.uid_base if a.uid_base is not None else cfg['uid_base'] + 61440
    src = mapwalk.archive_path('ACR', a.archive)
    taken = set(inventory.universe('ACR')) | set(inventory.universe('AC3'))
    alloc = acb_stage0.Uids(base, taken)
    report = []
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = {r.index: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}
    changed, expect = acr_entities(payloads, report, alloc)
    for idx in sorted(changed):
        p = payloads[idx]
        d = mapwalk.rebuild([(o['serial'], mapwalk.object_bytes(o), o['filter']) for o in p.objects], p.filter_table)
        set_payload(image, idx, acb_stage0.raw_payload(p, d))
    out_dir = os.path.join(OUT, f"{a.prefix}_{cfg['tag']}")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive + '.stage0')
    size = write(dest, image)
    v = {}
    with Forge(dest) as f, Forge(src) as g:
        v['others identical'] = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.index not in changed))
        v['records changed'] = len(changed)
        bench_ok, n_bench = (True, 0)
        for idx in changed:
            q = mapwalk.Payload(f.records[idx], f.read(f.records[idx]))
            old = payloads[idx]
            v[f'#{idx} prefix + object count kept'] = q.prefix == old.prefix and len(q.objects) == len(old.objects)
            for o in q.objects:
                if o['has_uid'] and o['uid'] in expect:
                    got = [n for _s, _e, n in entity.components(o['body']) or []]
                    bench_ok &= got == expect[o['uid']]
                    n_bench += 1
        v['converted entities parse, components as planned'] = bench_ok and n_bench == len(expect)
        v['entities converted'] = n_bench
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + f'\nverify: {v}\n')
    print('\n'.join(report))
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('verify:', v)
    if not all((x for x in v.values() if isinstance(x, bool))):
        raise SystemExit('verify failed')
if __name__ == '__main__':
    main(sys.argv[1:])
