"""forgelib/modes/chest_check.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import escort_check
import mapwalk
import spawn_layers
import xrefs
from forge import Forge
BLOCK = 'DataBlock_ACFE_Chest_Capture'
CHEST_LAYER = 3296798508
PICKUP_BUILDER, PICKUP_ENTITY, PICKUP_MESH = (866852841, 866854943, 3897217118)
ACTIVE, PREV = (3, 6)

def check(path):
    r = dict(archive=os.path.basename(path), path=path, problems=[], warnings=[])
    if not os.path.exists(path):
        r.update(verdict='FAIL', problems=['archive missing'])
        return r
    try:
        wbody, wrec = escort_check.world_of(path)
    except Exception as e:
        r.update(verdict='FAIL', problems=[f'no World: {e!r}'[:160]])
        return r
    tail = escort_check.parse_tail(wbody)
    link = b'\x01\x00' + struct.pack('<I', PICKUP_BUILDER) in wbody
    kit = set()
    points, points_unfiltered, points_other = (set(), set(), set())
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
                if is_world and o['uid'] in (PICKUP_BUILDER, PICKUP_ENTITY, PICKUP_MESH):
                    kit.add(o['uid'])
                cls = xrefs.cls('AC3', o['type'])
                if cls == 'WorldDataLayerManager':
                    world_lists = bool(world_lists) or CHEST_LAYER in dom_check.u32s(o['body'])
                if cls != 'Entity' or not o['has_uid']:
                    continue
                layers = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else None
                loaded = is_block or is_cell or is_world
                on = layers is None or CHEST_LAYER in layers if is_block else loaded and layers is not None and (CHEST_LAYER in layers)
                k = spawn_layers.spawn_kind(o['body'])
                cs = escort_check.comps(o)
                kind = ('chest point' if k[0] == 3 else 'team start' if k[0] == 2 else 'respawn' if k[0] == 0 else None) if k else 'out-of-bounds entity' if 'OutOfBoundsComponent' in cs else None
                if k and k[0] == 3:
                    unf = loaded and (not is_block) and (layers is None)
                    if (on or unf) and (not escort_check.own_allows(o['body'], CHEST_LAYER)):
                        dropped.setdefault(kind, set()).add(o['name'] or f"{o['uid']:#x}")
                        continue
                    (points if on else points_unfiltered if unf else points_other).add(o['uid'])
                    continue
                if not on:
                    continue
                if kind and (not escort_check.own_allows(o['body'], CHEST_LAYER)):
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
    points_other -= points | points_unfiltered
    points_unfiltered -= points
    starts = {i: len(us) for i, us in starts.items()}
    points, points_unfiltered, points_other = (len(points), len(points_unfiltered), len(points_other))
    scenes, respawns, oob = (len(scened), len(respawns), len(oob))
    usable = points + points_unfiltered
    r.update(world=wrec, points=points, points_unfiltered=points_unfiltered, points_other_layers=points_other, kit=len(kit), world_link=link, starts=dict(sorted(starts.items())), scenes=scenes, respawns=respawns, oob=oob, chest_list=len(tail['chests']), dropped={k_: len(v) for k_, v in dropped.items()})
    for kind, names in sorted(dropped.items()):
        r['problems'].append(f"{len(names)} {kind}(s) on the chest layer whose own DataLayerFilter (Entity +0xAC) leaves ACFE_Chest_Capture out - the game drops them as they join the match (0x4DE87D; Escort's R26T3 crash): " + ', '.join(sorted(names)[:4]) + (' ...' if len(names) > 4 else ''))
    if world_lists is None:
        r['warnings'].append('no WorldDataLayerManager found')
    if not usable and (not starts) and (not dropped):
        r['verdict'] = 'EMPTY'
        if points_other:
            r['warnings'].append(f'{points_other} type-3 point(s) on other layers only')
        return r
    if not usable:
        r['problems'].append('no type-3 chest point on the chest layer (no chest ever appears)' + (f'; {points_other} on other layers' if points_other else ''))
    elif usable < ACTIVE + PREV:
        r['warnings'].append(f'{usable} chest points (< {ACTIVE} active + {PREV} previous positions)')
    if len(kit) < 3:
        r['problems'].append(f'pickup kit incomplete in the World record ({len(kit)} / 3: AC2MP_Pickup builder, entity, mesh)')
    if not link:
        r['problems'].append('the World does not link the AC2MP_Pickup EntityBuilder (World+0x10): chests are never drawn')
    if world_lists is False:
        r['problems'].append("the World's WorldDataLayerManager does not list ACFE_Chest_Capture")
    if block is None:
        r['warnings'].append(f'no {BLOCK} record')
    if not starts:
        r['problems'].append('no team start (type 2) on the chest layer: the 0x580BC6 spawn-pick crash')
    else:
        if scenes < sum(starts.values()):
            r['problems'].append(f'{sum(starts.values()) - scenes} of {sum(starts.values())} team starts without an intro Scene (scene-less team starts crashed AC3, mode_spawns S11 / S12)')
        if len(starts) < 2:
            r['problems'].append(f'team starts on one index only {sorted(starts)}: two teams need two groups')
    if respawns == 0:
        r['problems'].append('no type-0 respawn on the chest layer')
    if oob == 0:
        r['warnings'].append('no out-of-bounds entity on the chest layer (no boundary)')
    r['verdict'] = 'FAIL' if r['problems'] else 'WARN' if r['warnings'] else 'OK'
    return r

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
        extra = '; '.join(r['problems'] + r['warnings'])
        print(f"{r['verdict']:5} {r['archive']:44} points={r.get('points')}+{r.get('points_unfiltered')} unfiltered (other layers {r.get('points_other_layers')}) kit={r.get('kit')} link={r.get('world_link')} starts={r.get('starts')} scenes={r.get('scenes')} respawns={r.get('respawns')} oob={r.get('oob')} chest_list={r.get('chest_list')}" + (f'  - {extra}' if extra else ''), flush=True)
    counts = {v: sum((r['verdict'] == v for r in res)) for v in ('OK', 'WARN', 'FAIL', 'EMPTY')}
    print('summary:', ', '.join((f'{n} {v}' for v, n in counts.items() if n)))
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump(res, fh, indent=1, default=str)
    return 1 if bad else 0
if __name__ == '__main__':
    sys.exit(main())
