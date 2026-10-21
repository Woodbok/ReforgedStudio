"""forgelib/modes/dom_check.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import os
import struct
import subprocess
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'maps', 'port')):
    sys.path.insert(0, _p)
import aa_zones as AZ
import archive_fixes
import entity
import mapwalk
import paths
import spawn_layers
import xrefs
import zones as Z
from forge import Forge
BLOCK = 'DataBlock_ACFE_Domination'
DOM_LAYER = next((u for u, n in archive_fixes.layer_names('AC3').items() if n == 'ACFE_Domination'))
FLAGDOM, ZONECYL = (2501227407, 2907915792)
AC3_OWN = ('Plantation', 'Fort_Wolcott', 'Boston_Mill', 'Boston_Brewer', 'Boston_Harbou', 'Animus', 'Iceberg')
SHARED = {'DataPC.forge', 'DataPC_extra.forge', 'DataPC_CoreFX.forge', 'DataPC_Fire.forge', 'DataPC_extraparams.forge'}

def main_checkout():
    try:
        common = subprocess.run(['git', 'rev-parse', '--git-common-dir'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        return os.path.dirname(os.path.abspath(os.path.join(ROOT, common)))
    except (OSError, subprocess.CalledProcessError):
        return ROOT

def resolve(p):
    for base in (None, ROOT, main_checkout()):
        q = p if base is None else os.path.join(base, p)
        if os.path.exists(q):
            return q
    return p

def release_archives():
    out = [os.path.join(paths.AC3_SOURCE, f'DataPC_AC3MP_{a}.forge') for a in AC3_OWN]
    with open(os.path.join(ROOT, 'release_set.json'), encoding='utf-8') as fh:
        for f in json.load(fh)['files']:
            n = os.path.basename(f['dest'])
            if n.startswith('DataPC_') and n.endswith('.forge') and (n not in SHARED):
                out.append(resolve(f['src']))
    return out

def u32s(b):
    return {struct.unpack_from('<I', b, k)[0] for k in range(len(b) - 3)}

def check(path):
    r = dict(archive=os.path.basename(path), path=path, problems=[], warnings=[])
    if not os.path.exists(path):
        r.update(verdict='FAIL', problems=['archive missing'])
        return r
    block, world_lists, cells, cell_respawns = (None, None, [], 0)
    with Forge(path) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            is_block = rec.name == BLOCK
            if rec.name.startswith('Cell'):
                cells.append(rec)
                continue
            if not is_block and rec.name.startswith('DataBlock_'):
                continue
            try:
                p = mapwalk.Payload(rec, f.read(rec))
            except Exception as e:
                if is_block:
                    r['problems'].append(f'{BLOCK} unreadable: {e!r}'[:160])
                continue
            if is_block:
                block = p
                continue
            for o in p.objects:
                if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager':
                    world_lists = bool(world_lists) or DOM_LAYER in u32s(o['body'])
        if block is not None and len(block.objects) > 1 and (not any(((spawn_layers.spawn_kind(o['body']) or (9,))[0] == 0 for o in block.objects[1:] if xrefs.cls('AC3', o['type']) == 'Entity'))):
            for rec in cells:
                try:
                    p = mapwalk.Payload(rec, f.read(rec))
                except Exception:
                    continue
                sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
                cell_respawns += sum((1 for o in p.objects if xrefs.cls('AC3', o['type']) == 'Entity' and o['filter'] is not None and (o['filter'] < len(sets)) and (DOM_LAYER in sets[o['filter']]) and ((spawn_layers.spawn_kind(o['body']) or (9,))[0] == 0)))
    if world_lists is None:
        r['warnings'].append('no WorldDataLayerManager found')
    elif not world_lists:
        r['problems'].append("the World's WorldDataLayerManager does not list ACFE_Domination")
    if block is None:
        r['problems'].append(f'no {BLOCK} record')
        r['verdict'] = 'FAIL'
        return r
    points, zones, starts, scenes, respawns, oob = ([], [], {1: 0, 2: 0}, 0, 0, 0)
    for o in block.objects[1:]:
        if xrefs.cls('AC3', o['type']) != 'Entity':
            continue
        b = o['body']
        cs = [n for _s, _e, n in entity.components(b) or []]
        if 'ControlPointComponent' in cs:
            k = AZ.comp(b, 'ControlPointComponent')
            refs = u32s(b)
            points.append(dict(name=o['name'], id=struct.unpack_from('<I', b, k[0] + 15)[0], at=AZ.pos(o), fx=FLAGDOM in refs and ZONECYL in refs))
        elif 'GameplayZoneComponent' in cs:
            try:
                zones.append(dict(name=o['name'], convex=Z.parse(b[slice(*AZ.comp(b, 'GameplayZoneComponent'))])['convex']))
            except Exception as e:
                r['problems'].append(f"zone {o['name']} unreadable: {e!r}"[:160])
        elif 'OutOfBoundsComponent' in cs:
            oob += 1
        else:
            k = spawn_layers.spawn_kind(b)
            if k and k[0] == 2 and (k[1] in (1, 2)):
                starts[k[1]] += 1
                scenes += 'Scene' in cs
            elif k and k[0] == 0:
                respawns += 1
    respawns += cell_respawns
    r.update(points=sorted((p['id'] for p in points)), zones=len(zones), starts=starts, respawns=respawns, oob=oob)
    if cell_respawns:
        r['respawns_in_cells'] = cell_respawns
    if not points and (not zones) and (not any(starts.values())):
        r['verdict'] = 'EMPTY'
        return r
    if sorted((p['id'] for p in points)) != [1, 2, 3]:
        r['problems'].append(f"control point ids {sorted((p['id'] for p in points))}, need exactly 1 / 2 / 3")
    for p in points:
        if not p['fx']:
            r['problems'].append(f"point {p['id']} ({p['name']}) lacks a CoreFX Domination effect reference")
        inside = [z['name'] for z in zones if AZ.member(z['convex'], p['at'])]
        if len(inside) != 1:
            r['problems'].append(f"point {p['id']} lies in {len(inside)} zones {inside}, need exactly 1")
    if len(zones) != 3:
        r['problems'].append(f'{len(zones)} zones, need 3')
    for z in zones:
        n = sum((AZ.member(z['convex'], p['at']) for p in points))
        if n != 1:
            r['warnings'].append(f"zone {z['name']} holds {n} points")
    for team, n in starts.items():
        if n == 0:
            r['problems'].append(f'no team {team} start (type 2): the 0x580BC6 spawn-pick crash')
        elif n != 4:
            r['warnings'].append(f'team {team}: {n} starts (stock 4)')
    if scenes < sum(starts.values()):
        r['warnings'].append(f'{sum(starts.values()) - scenes} team start(s) without an intro Scene')
    if respawns == 0:
        r['problems'].append('no type-0 respawns')
    if oob == 0:
        r['warnings'].append('no out-of-bounds entity')
    r['verdict'] = 'FAIL' if r['problems'] else 'WARN' if r['warnings'] else 'OK'
    return r

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('archives', nargs='*')
    ap.add_argument('--release', action='store_true')
    ap.add_argument('--only')
    ap.add_argument('--json')
    a = ap.parse_args()
    todo = ([(p, False) for p in release_archives()] if a.release else []) + [(resolve(p), True) for p in a.archives]
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
        print(f"{r['verdict']:5} {r['archive']:44} points={r.get('points')} zones={r.get('zones')} starts={r.get('starts')} respawns={r.get('respawns')}" + (f" ({r['respawns_in_cells']} in cells)" if r.get('respawns_in_cells') else '') + f" oob={r.get('oob')}" + (f'  - {extra}' if extra else ''), flush=True)
    counts = {v: sum((r['verdict'] == v for r in res)) for v in ('OK', 'WARN', 'FAIL', 'EMPTY')}
    print('summary:', ', '.join((f'{n} {v}' for v, n in counts.items() if n)))
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump(res, fh, indent=1, default=str)
    sys.exit(1 if bad else 0)
if __name__ == '__main__':
    main()
