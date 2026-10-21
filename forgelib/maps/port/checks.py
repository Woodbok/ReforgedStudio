"""forgelib/maps/port/checks.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
for _p in (os.path.dirname(MAPS), MAPS, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import configs
CHECKS = ('check_order.py', 'shared_gaps.py')
SOUK = "'DataPC_ACFE_Souk.forge'"

def run(script, archive, build):
    path = os.path.join(MAPS, script)
    src = open(path, encoding='utf-8').read()
    if src.count(SOUK) != 1:
        raise SystemExit(f"{script}: expected Souk's archive name once, found {src.count(SOUK)}")
    src = src.replace(SOUK, f'"{archive}"')
    print(f'== {script} {build}', flush=True)
    sys.argv = [path, build]
    os.chdir(MAPS)
    exec(compile(src, path, 'exec'), {'__name__': '__main__', '__file__': path})

def build_path(cfg, build):
    d = os.path.join(os.path.dirname(MAPS), 'out', 'maps', build)
    for name in (cfg['archive'], cfg.get('install_as')):
        if name and os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    raise SystemExit(f"no {cfg['archive']} in {d}")

def load(cfg, build):
    import mapwalk
    from forge import Forge
    with Forge(build_path(cfg, build)) as f:
        return {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.size and r.name != 'GlobalMetaFile'}

def crowdlife_uids(cfg, build):
    import struct
    from collections import Counter
    import lift_dropped
    import merchant_stalls
    from datafile import decompress
    from forge import Forge
    from mapwalk import split_prefix
    real = {struct.pack('<I', c) for c in merchant_stalls.REAL}
    lo = lift_dropped.CROWDLIFE_UIDS
    top, seen = (struct.pack('<H', lo >> 16), Counter())
    with Forge(build_path(cfg, build)) as f:
        for r in f.records:
            if not r.size or r.name == 'GlobalMetaFile':
                continue
            d = decompress(split_prefix(f.read(r))[1])
            k = d.find(top, 2)
            while k >= 0:
                u = struct.unpack_from('<I', d, k - 2)[0]
                if lo <= u < lo + 65536 and d[k + 2:k + 6] in real:
                    seen[u] += 1
                k = d.find(top, k + 1)
    dup = sorted((u for u, n in seen.items() if n > 1))
    print(f'== crowd-life uids: {len(seen)} objects, {len(dup)} headed twice or more' + (f" ({', '.join((hex(u) for u in dup[:6]))}): a second crowd-life lift without --avoid-built" if dup else ''))
    return not dup

def drop_audit(cfg, build):
    import spawn_layers
    import survey
    feat = cfg['FEATURES']
    have = set()
    from forge import Forge
    import mapwalk
    with Forge(build_path(cfg, build)) as f:
        for r in f.records:
            if r.size and r.name != 'GlobalMetaFile':
                have |= {o['uid'] for o in mapwalk.Payload(r, f.read(r)).objects if o['has_uid']}
    m = survey.Map(mapwalk.archive_path('ACR', cfg['archive']))
    back = list((feat.get('performers') or {}).get('entities', ())) + list((feat.get('pieces') or {}).get('entities', ())) + list(feat.get('lone') or ()) + list(feat.get('tree_prefixes') or ())
    impossible = [p for pre, r, _w in survey.KNOWN_VERDICTS if r == 'impossible' for p in pre]
    lost, unexpected = ({}, {})
    for idx, i, o in m.objects():
        if i == 0 or not o['has_uid'] or m.kind[idx] == 'file' or (o['uid'] in have) or (survey.xrefs.cls('ACR', o['type']) not in ('Entity', 'EntityGroup')):
            continue
        fam = survey.family(o['name'])
        lost[fam] = lost.get(fam, 0) + 1
        expected = any((survey.re.search(p, fam.lower()) for p in survey.BY_DESIGN)) or survey.matches(fam, impossible) or (feat.get('spots') and fam.startswith('AC2MP_Merchant_Orator_plane')) or (survey.matches(o['name'], feat.get('drop') or ()) and (not survey.matches(o['name'], back))) or ((spawn_layers.spawn_kind(o['body']) or (None,))[0] == 2)
        if not expected:
            unexpected[fam] = unexpected.get(fam, 0) + 1
    print(f"== drop audit: {sum(lost.values())} of ACR's placed objects not in the build ({len(lost)} families: " + ', '.join((f'{k} {n}' for k, n in sorted(lost.items())[:12])) + ')')
    if unexpected:
        print('   UNEXPECTED (no recipe takes them, no verdict drops them - look before testing): ' + ', '.join((f'{k} {n}' for k, n in sorted(unexpected.items()))))
    return not unexpected

def dropped_objects(cfg, build, files=None):
    import inventory
    import xrefs
    from build_static import refs_in
    from summary import BULK
    files = files if files is not None else load(cfg, build)
    have = {o['uid'] for p in files.values() for o in p.objects if o['has_uid']}
    src = {}
    for fname, _pos, t, name, u, *_ in inventory.load('ACR', cfg['archive'])['rows']:
        if u is not None:
            src.setdefault(u, (xrefs.cls('ACR', t), name, fname))
    shared = {u for u, homes in inventory.universe('AC3').items() if any((h[0] in inventory.SHARED for h in homes))}
    gone = {u: v for u, v in src.items() if u not in have}
    by_cls = {}
    for u, (c, name, _f) in gone.items():
        by_cls.setdefault(c, []).append(name or f'0x{u:08X}')
    print(f"== dropped objects: {len(gone)} of ACR's {len(src)} uids not in the build (" + ', '.join((f'{c} {len(v)}' for c, v in sorted(by_cls.items(), key=lambda kv: -len(kv[1])))) + ')')
    for c, names in sorted(by_cls.items()):
        if c not in ('Entity', 'EntityGroup') and c not in BULK and (len(names) <= 12):
            print(f"   {c}: {', '.join(sorted(names))}")
    target = set(gone) - shared
    named, hits = (set(), [])
    for fname, p in files.items():
        for o in p.objects:
            c = xrefs.cls('AC3', o['type'])
            if c in BULK:
                continue
            refs = refs_in(o['body'])
            named |= refs - ({o['uid']} if o['has_uid'] else set())
            if refs & target:
                hits.append((fname, c, o, refs & target))
    bad = [h for h in hits if h[1] == 'World' or (h[2]['has_uid'] and h[2]['uid'] in named)]
    for fname, c, o, hit in hits:
        tag = 'DANGLING' if any((h[2] is o for h in bad)) else 'dead (nothing names it)'
        print(f"   {tag} {fname} / {c} {o['name']} -> " + ', '.join((f'0x{u:08X} {gone[u][0]} {gone[u][1]}' for u in sorted(hit))))
    if bad:
        print(f"   FAIL: {len(bad)} kept object(s) name a dropped one - keep it (strip what AC3 can't read) or clean the reference")
    return not bad

def crowd_state(cfg, build, files):
    import struct
    import convert
    import crowdlife
    import xrefs
    life = struct.pack('<I', convert.class_ids()['GcLMPCrowdLife'])
    n, bad = (0, [])
    for fname, p in files.items():
        for o in p.objects:
            if xrefs.cls('AC3', o['type']) not in ('Entity', 'EntityGroup') or life not in o['body']:
                continue
            n += 1
            if crowdlife.check_state(o['body']):
                bad.append(o['name'])
    import re
    fams = {}
    for name in bad:
        f_ = re.sub('[ _]?\\d+$', '', name or '')
        fams[f_] = fams.get(f_, 0) + 1
    print(f'== crowd-life state matrices: {n} objects, {len(bad)} holding a matrix that is not their placement' + (f" ({', '.join((f'{k} {v}' for k, v in sorted(fams.items())))}): a template's position kept" if bad else ''))
    return not bad

def twins(cfg, build, files):
    import stock_twins
    todo = stock_twins.plan(files)
    print(f"== twins: {len(todo)} uid(s) defined with other bytes than the root's / AC3's" + (': ' + '; '.join((f"{c} {n} 0x{u:08X} in {', '.join(ch)}" for u, (c, n, _b, ch) in sorted(todo.items()))) if todo else ''))
    return not todo

def tod_keys(cfg, build, files):
    import mapwalk
    import tod_evening
    import tod_snow
    import inventory
    from forge import Forge

    def splits(body):
        out = set()
        for s_, e_, path in tod_snow.entries(body):
            c = tod_evening.curve(body[s_:e_])
            if c and (not tod_evening.midnight_agrees(c)):
                out.add(path)
        return out
    src = set()
    rows = [r for r in inventory.load('ACR', cfg['archive'])['rows'] if r[2] == tod_snow.TOD]
    if rows:
        with Forge(mapwalk.archive_path('ACR', cfg['archive'])) as f:
            for rec in f.records:
                if rec.name in {r[0] for r in rows}:
                    for o in mapwalk.Payload(rec, f.read(rec)).objects:
                        if o['type'] == tod_snow.TOD:
                            src |= splits(o['body'])
    ok, lines = (True, [])
    for fname, p in files.items():
        for o in p.objects:
            if o['type'] != tod_snow.TOD:
                continue
            got = splits(o['body'])
            made = got - src
            ok &= not made
            lines.append(f"{o['name']}: {len(got)} curve(s) with 0:00 != 24:00, {len(got & src)} as the source has them, {len(made)} split by the chain" + (' (' + ', '.join(('/'.join((f'{x:08X}' for x in pth[:2])) for pth in sorted(made)[:6])) + ')' if made else ''))
    print('== time of day 0:00 / 24:00: ' + ('; '.join(lines) or 'no time of day'))
    return ok

def end_of_match(cfg, build, files):
    import podium
    lines, consistent, complete = podium.state(files)
    want = any((st[0] == 'port/podium.py' for stage in ('STAGE1', 'STAGE2') for st in cfg.get(stage, [])))
    print('== end of match: ' + '; '.join(lines) + ('' if want else ' (podium.py not in the chain: configs.PODIUM off)'))
    bad = podium.clone_problems(files)
    print(f'== podium copies: {len(bad)} changed off a uid' + (': ' + '; '.join(bad[:6]) if bad else ''))
    return consistent and (complete or not want) and (not bad)

def window(cfg, build):
    import struct
    import grid_window
    import inventory
    import leaf_fix
    import mapwalk
    import xrefs
    from forge import Forge
    from leaf_index import NONE, records
    with Forge(build_path(cfg, build)) as f:
        files = {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}
    shared = {u for u, h in inventory.universe('AC3').items() if any((x[0] in inventory.SHARED for x in h))}
    res = grid_window.check(files, lambda t: xrefs.cls('AC3', t), shared)
    side = leaf_fix.grid_side(list(files.values()))
    wrong = sum((1 for p in files.values() for o in p.objects for k in records(o['body']) if struct.unpack_from('<H', o['body'], k)[0] not in (NONE, leaf_fix.wanted(p, side))))
    print(f'== load window: {grid_window.summary(res)}; leaf records on another leaf {wrong}')
    return res['ok'] and wrong == 0

def zone_entities(cfg, build, files):
    import struct
    import xrefs
    import zones
    bad, n, ents = ([], 0, [])
    for fname, p in files.items():
        for o in p.objects:
            if struct.pack('<I', zones.ZONE) in o['body'] and xrefs.cls('AC3', o['type']) == 'Entity':
                n += 1
                bad += [f"{fname}/{o['name']}: {x}" for x in zones.closing_problems(o['body'])]
                ents.append((fname, o))
    need = {zones.wall_mesh_uid(o['body']) for _f, o in ents}
    objs = {o['uid']: o for p in files.values() for o in p.objects if o['has_uid'] and o['uid'] in need}
    for fname, o in ents:
        bad += [f"{fname}/{o['name']}: {x}" for x in zones.wall_problems(o['body'], objs)]
    print(f'== zone entities: {n}, {len(bad)} not reading as the exe reads them or drawn off their panels' + (': ' + '; '.join(bad) if bad else ''))
    return not bad

def aa_records(cfg, build, files):
    import aa_records as AR
    found = AR.pieces(files)
    if not found:
        print('== aa records: no Artifact Assault pieces')
        return True
    ok, problems, notes, one = AR.judge(found, files)
    print(f"== aa records: {('PASS' if ok else 'FAIL')} " + ('; '.join(problems + notes) or f'all in {one}'))
    print(f'   {AR.summary(found)}')
    return ok

def oob_modes(cfg, build, files):
    import oob_modes as OM
    ok, problems, rows = OM.judge(files)
    print(f"== out-of-bounds per mode: {('PASS' if ok else 'FAIL')}" + (': ' + '; '.join(problems) if problems else ''))
    print('   ' + ' | '.join(rows))
    return ok

def own_filters(cfg, build, files):
    import own_filters as OF
    bad = OF.faults(files)
    print(f"== own layer filters: {('PASS' if not bad else 'FAIL')}" + (f': {len(bad)} cell entities on mode layers with an empty own filter' if bad else ''))
    return not bad

def chase_breakers(cfg, build):
    import chase_breaker_check
    print('== chase breakers', flush=True)
    return chase_breaker_check.check(build_path(cfg, build))

def main(argv):
    cfg = configs.MAPS[argv[0]]
    for script in CHECKS:
        run(script, os.path.basename(build_path(cfg, argv[1])), argv[1])
    ok = crowdlife_uids(cfg, argv[1])
    files = load(cfg, argv[1])
    ok = dropped_objects(cfg, argv[1], files) and ok
    ok = crowd_state(cfg, argv[1], files) and ok
    ok = twins(cfg, argv[1], files) and ok
    ok = tod_keys(cfg, argv[1], files) and ok
    ok = end_of_match(cfg, argv[1], files) and ok
    ok = zone_entities(cfg, argv[1], files) and ok
    ok = aa_records(cfg, argv[1], files) and ok
    ok = oob_modes(cfg, argv[1], files) and ok
    ok = own_filters(cfg, argv[1], files) and ok
    del files
    ok = chase_breakers(cfg, argv[1]) and ok
    if cfg.get('FEATURES'):
        ok = drop_audit(cfg, argv[1]) and ok
    if (cfg.get('FEATURES') or {}).get('grid'):
        ok = window(cfg, argv[1]) and ok
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
