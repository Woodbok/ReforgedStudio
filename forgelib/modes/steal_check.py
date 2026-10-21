"""forgelib/modes/steal_check.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'maps', 'port'), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import dom_check
import entity
import escort_check
import mapwalk
import spawn_layers
import xrefs
from forge import Forge
BLOCK = 'DataBlock_ACFE_Hijack'
HIJACK = 3296798498
HIJACK_COMPONENT = 3125174415
ARTIFACT_FX = 31971950
TWO_ARTIFACTS, MAX_PLAYERS = (2, 8)

def hijack_fx(o):
    for s, e, n in entity.components(o['body']) or []:
        if n == 'HijackComponent':
            c = o['body'][s:e]
            k = c.rfind(b'\x01\x00', 0, len(c) - 5)
            return struct.unpack_from('<I', c, k + 2)[0] if k >= 0 else None
    return None

def check(path):
    r = dict(archive=os.path.basename(path), path=path, problems=[], warnings=[])
    if not os.path.exists(path):
        r.update(verdict='FAIL', problems=['archive missing'])
        return r
    arts, arts_unf, arts_other, fx_bad = (set(), set(), set(), set())
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
                    world_lists = bool(world_lists) or HIJACK in dom_check.u32s(o['body'])
                if cls != 'Entity' or not o['has_uid']:
                    continue
                layers = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else None
                loaded = is_block or is_cell or is_world
                on = layers is None or HIJACK in layers if is_block else loaded and layers is not None and (HIJACK in layers)
                cs = escort_check.comps(o)
                if 'HijackComponent' in cs:
                    unf = (is_cell or is_world) and layers is None
                    if (on or unf) and (not escort_check.own_allows(o['body'], HIJACK)):
                        dropped.setdefault('artifact', set()).add(o['name'] or f"{o['uid']:#x}")
                        continue
                    (arts if on else arts_unf if unf else arts_other).add(o['uid'])
                    if (on or unf) and hijack_fx(o) != ARTIFACT_FX:
                        fx_bad.add(o['name'] or f"{o['uid']:#x}")
                    continue
                if not on:
                    continue
                k = spawn_layers.spawn_kind(o['body'])
                kind = ('team start' if k[0] == 2 else 'respawn' if k[0] == 0 else None) if k else 'out-of-bounds entity' if 'OutOfBoundsComponent' in cs else None
                if kind and (not escort_check.own_allows(o['body'], HIJACK)):
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
    arts_unf -= arts
    arts_other -= arts | arts_unf
    starts = {i: len(us) for i, us in starts.items()}
    n_starts = sum(starts.values())
    usable = len(arts) + len(arts_unf)
    r.update(artifacts=len(arts), artifacts_unfiltered=len(arts_unf), artifacts_other_layers=len(arts_other), starts=dict(sorted(starts.items())), scenes=len(scened), respawns=len(respawns), oob=len(oob), dropped={k: len(v) for k, v in dropped.items()})
    for kind, names in sorted(dropped.items()):
        r['problems'].append(f"{len(names)} {kind}(s) on the Hijack layer whose own DataLayerFilter (Entity +0xAC) leaves ACFE_Hijack out - the game drops them as they join the match (0x4DE87D; Escort's R26T3 crash): " + ', '.join(sorted(names)[:4]) + (' ...' if len(names) > 4 else ''))
    if world_lists is None:
        r['warnings'].append('no WorldDataLayerManager found')
    if not usable and (not n_starts):
        r['verdict'] = 'EMPTY'
        if arts_other:
            r['warnings'].append(f'{len(arts_other)} artifact(s) on other layers only')
        return r
    if not usable:
        r['problems'].append('no HijackComponent artifact on the Hijack layer (nothing to steal)' + (f'; {len(arts_other)} on other layers' if arts_other else ''))
    elif usable < TWO_ARTIFACTS:
        r['warnings'].append(f'{usable} artifact(s): a match of 5+ players plays {TWO_ARTIFACTS}')
    if arts_unf:
        r['warnings'].append(f'{len(arts_unf)} artifact(s) in a cell / the World with no filter (loaded in every mode)')
    if fx_bad:
        r['warnings'].append(f"{len(fx_bad)} artifact(s) not linking ACR's FX 0x{ARTIFACT_FX:08X}: " + ', '.join(sorted(fx_bad)[:4]))
    if world_lists is False:
        r['problems'].append("the World's WorldDataLayerManager does not list ACFE_Hijack")
    if block is None:
        r['warnings'].append(f'no {BLOCK} record')
    if not n_starts:
        r['problems'].append('no initial spawn (type 2) on the Hijack layer: rule 12 (0x57FB4F) finds no spawn point')
    else:
        if len(scened) < n_starts:
            r['problems'].append(f'{n_starts - len(scened)} of {n_starts} initial spawns without an intro Scene (scene-less type-2 spawns crashed AC3, mode_spawns S11 / S12)')
        if n_starts < MAX_PLAYERS:
            r['warnings'].append(f'{n_starts} initial spawn(s) for up to {MAX_PLAYERS} players')
    if not respawns:
        r['problems'].append('no type-0 respawn on the Hijack layer')
    if not oob:
        r['warnings'].append('no out-of-bounds entity on the Hijack layer (no boundary)')
    r['verdict'] = 'FAIL' if r['problems'] else 'WARN' if r['warnings'] else 'OK'
    return r

def line(r):
    extra = '; '.join(r['problems'] + r['warnings'])
    return f"{r['verdict']:5} {r['archive']:44} artifacts={r.get('artifacts')}+{r.get('artifacts_unfiltered')} unfiltered (other layers {r.get('artifacts_other_layers')}) starts={r.get('starts')} scenes={r.get('scenes')} respawns={r.get('respawns')} oob={r.get('oob')}" + (f'  - {extra}' if extra else '')

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
