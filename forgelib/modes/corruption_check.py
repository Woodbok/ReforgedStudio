"""forgelib/modes/corruption_check.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'maps', 'port'), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import dom_check
import escort_check
import mapwalk
import spawn_layers
import xrefs
from forge import Forge
BLOCK = 'DataBlock_ACFE_Corruption'
CORRUPTION = 3296798717
MAX_PLAYERS, MIN_GROUPS = (8, 2)

def check(path):
    r = dict(archive=os.path.basename(path), path=path, problems=[], warnings=[])
    if not os.path.exists(path):
        r.update(verdict='FAIL', problems=['archive missing'])
        return r
    starts, scened, respawns, oob = ({}, set(), set(), set())
    dropped = {}
    world_lists, block = (None, None)
    with Forge(path) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            is_block, is_cell, is_world = (rec.name == BLOCK, rec.name.startswith('Cell'), rec.index == 1)
            try:
                p = mapwalk.Payload(rec, f.read(rec))
            except Exception:
                continue
            if is_block:
                block = p
            sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
            for o in p.objects:
                cls = xrefs.cls('AC3', o['type'])
                if cls == 'WorldDataLayerManager':
                    world_lists = bool(world_lists) or CORRUPTION in dom_check.u32s(o['body'])
                if cls != 'Entity' or not o['has_uid']:
                    continue
                layers = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else None
                loaded = is_block or is_cell or is_world
                on = layers is None or CORRUPTION in layers if is_block else loaded and layers is not None and (CORRUPTION in layers)
                if not on:
                    continue
                cs = escort_check.comps(o)
                k = spawn_layers.spawn_kind(o['body'])
                kind = ('team start' if k[0] == 2 else 'respawn' if k[0] == 0 else None) if k else 'out-of-bounds entity' if 'OutOfBoundsComponent' in cs else None
                if kind and (not escort_check.own_allows(o['body'], CORRUPTION)):
                    dropped.setdefault(kind, set()).add(o['name'] or f"{o['uid']:#x}")
                    continue
                if k and k[0] == 2:
                    starts.setdefault(k[1], set()).add(o['uid'])
                    if 'Scene' in cs:
                        scened.add(o['uid'])
                elif k and k[0] == 0:
                    respawns.add(o['uid'])
                elif 'OutOfBoundsComponent' in cs:
                    oob.add(o['uid'])
    starts = {i: len(us) for i, us in starts.items()}
    n_starts = sum(starts.values())
    r.update(starts=dict(sorted(starts.items())), scenes=len(scened), respawns=len(respawns), oob=len(oob), dropped={k: len(v) for k, v in dropped.items()})
    for kind, names in sorted(dropped.items()):
        r['problems'].append(f"{len(names)} {kind}(s) on the Corruption layer whose own DataLayerFilter (Entity +0xAC) leaves ACFE_Corruption out - the game drops them as they join the match (0x4DE87D; Escort's R26T3 crash): " + ', '.join(sorted(names)[:4]) + (' ...' if len(names) > 4 else ''))
    if world_lists is None:
        r['warnings'].append('no WorldDataLayerManager found')
    if not n_starts:
        r['verdict'] = 'FAIL' if dropped.get('team start') else 'EMPTY'
        return r
    if world_lists is False:
        r['problems'].append("the World's WorldDataLayerManager does not list ACFE_Corruption")
    if block is None:
        r['warnings'].append(f'no {BLOCK} record')
    if len(scened) < n_starts:
        r['problems'].append(f'{n_starts - len(scened)} of {n_starts} initial spawns without an intro Scene (scene-less type-2 spawns crashed AC3, mode_spawns S11 / S12)')
    if len(starts) < MIN_GROUPS:
        r['problems'].append(f'type-2 starts on {len(starts)} index(es) {sorted(starts)}: the corrupted and the uncorrupted need {MIN_GROUPS} groups (0x580D4C)')
    lowest = starts[min(starts)]
    if lowest < MAX_PLAYERS:
        r['warnings'].append(f"{lowest} start(s) on the lowest index {min(starts)} (the uncorrupted's group, 0x580BC6) for up to {MAX_PLAYERS - 1} uncorrupted - the rest take the farthest respawn (rule 16)")
    if not respawns:
        r['problems'].append("no type-0 respawn on the Corruption layer (rule 16's fallback, the death respawns)")
    if not oob:
        r['warnings'].append('no out-of-bounds entity on the Corruption layer (no boundary)')
    r['verdict'] = 'FAIL' if r['problems'] else 'WARN' if r['warnings'] else 'OK'
    return r

def line(r):
    extra = '; '.join(r['problems'] + r['warnings'])
    return f"{r['verdict']:5} {r['archive']:44} starts={r.get('starts')} scenes={r.get('scenes')} respawns={r.get('respawns')} oob={r.get('oob')} dropped={r.get('dropped')}" + (f'  - {extra}' if extra else '')

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('archives', nargs='*')
    ap.add_argument('--release', action='store_true')
    ap.add_argument('--only')
    ap.add_argument('--json')
    a = ap.parse_args(argv)
    todo = ([(p, False) for p in dom_check.release_archives()] if a.release else []) + [(dom_check.resolve(p), True) for p in a.archives]
    if a.only:
        todo = [(p, s) for p, s in todo if a.only.lower() in os.path.basename(p).lower()]
    if not todo:
        ap.error('give archives or --release')
    res, bad = ([], 0)
    for p, strict in todo:
        r = check(p)
        res.append(r)
        bad += strict and r['verdict'] not in ('OK', 'WARN')
        print(line(r), flush=True)
    counts = {v: sum((r['verdict'] == v for r in res)) for v in ('OK', 'WARN', 'FAIL', 'EMPTY')}
    print('summary:', ', '.join((f'{n} {v}' for v, n in counts.items() if n)))
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump(res, fh, indent=1, default=str)
    return 1 if bad else 0
if __name__ == '__main__':
    sys.exit(main())
