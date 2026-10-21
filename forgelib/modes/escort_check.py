"""forgelib/modes/escort_check.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import archive_fixes
import dom_check
import entity
import mapwalk
import spawn_layers
import world as W
import xrefs
from forge import Forge
BLOCK = 'DataBlock_ACFE_Escort'
ESCORT = next((u for u, n in archive_fixes.layer_names('AC3').items() if n == 'ACFE_Escort'))
WORLD_CLASS = 4223024711
PATH_CLASS, NODE_CLASS = (3559388932, 1105138129)
NB_VIPS = 2
CROWD_SKINS = 10
MAX_PLAYERS = 8

def vip_skins(players=MAX_PLAYERS):
    return CROWD_SKINS - min(players, CROWD_SKINS)

def parse_tail(body):
    m = W.markers(body)
    sgm = [k for k, n in m if n == 'SupportedGameMode']
    if not sgm:
        return dict(start=None, chests=[], paths=[], end=None, error='no SupportedGameMode in the World')
    first = max(sgm) + 4 + 2 + 4 + 4
    r = _lists(body, first)
    k = first
    while r['error'] and k + 8 <= len(body):
        k += 1
        q = _lists(body, k)
        if not q['error']:
            r = q
    return r

def _lists(body, start):
    k = start
    out = dict(start=start, chests=[], paths=[], end=None, error=None)
    try:
        n = struct.unpack_from('<I', body, k)[0]
        k += 4
        if n > 16383:
            raise ValueError(f'chest count {n}')
        for _ in range(n):
            if body[k:k + 2] != b'\x01\x00':
                raise ValueError(f'chest link at +{k} is {body[k:k + 2].hex()}, not 01 00')
            out['chests'].append(struct.unpack_from('<I', body, k + 2)[0])
            k += 6
        n = struct.unpack_from('<I', body, k)[0]
        k += 4
        if n > 16383:
            raise ValueError(f'path count {n}')
        for _ in range(n):
            uid, cid, nn = struct.unpack_from('<III', body, k)
            k += 12
            if cid != PATH_CLASS:
                raise ValueError(f'path at +{k - 12}: class {cid:#x}')
            nodes = []
            for _ in range(nn):
                nu, nc, z, ref, a, b = struct.unpack_from('<IIBIBB', body, k)
                if nc != NODE_CLASS or z != 0:
                    raise ValueError(f'node at +{k}: class {nc:#x}, link kind {z}')
                nodes.append((nu, ref, a, b))
                k += 15
            out['paths'].append(dict(uid=uid, nodes=nodes))
        out['end'] = k
        if k != len(body):
            out['error'] = f'{len(body) - k} bytes after the path list'
    except (struct.error, ValueError) as e:
        out['error'] = f'tail unreadable: {e}'
    return out

def tail_bytes(chests, paths):
    b = struct.pack('<I', len(chests)) + b''.join((b'\x01\x00' + struct.pack('<I', r) for r in chests))
    b += struct.pack('<I', len(paths))
    for p in paths:
        b += struct.pack('<III', p['uid'], PATH_CLASS, len(p['nodes']))
        b += b''.join((struct.pack('<IIBIBB', nu, NODE_CLASS, 0, ref, a, bb) for nu, ref, a, bb in p['nodes']))
    return b

def world_of(path):
    with Forge(path) as f:
        r = next((r for r in f.records if r.index == 1))
        p = mapwalk.Payload(r, f.read(r))
    o = next((o for o in p.objects if o['type'] == WORLD_CLASS))
    return (o['body'], r.name)

def comps(o):
    return [n for _s, _e, n in entity.components(o['body']) or []]
DLF, DLA = (3676127342, 3809842145)

def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]

def own_filter(body):
    cs = entity.components(body) or []
    k = body.find(struct.pack('<I', DLF), cs[-1][1] if cs else 0)
    if k < 0 or k + 8 > len(body):
        return None
    n = _u32(body, k + 4)
    if n > 64 or (n and _u32(body, k + 8) != 0):
        return None
    o, acts = (k + 12 if n else k + 8, [])
    for i in range(n):
        if o + 13 > len(body) or _u32(body, o) != DLA or body[o + 4] != 0 or (_u32(body, o + 9) != 0):
            return None
        acts.append(_u32(body, o + 5))
        o += 13
        if i < n - 1:
            if _u32(body, o) != 0:
                return None
            o += 4
    return (k, o, acts)

def own_filter_bytes(layers):
    if not layers:
        return struct.pack('<II', DLF, 0)
    return struct.pack('<II', DLF, len(layers)) + bytes(4) + bytes(4).join((struct.pack('<I', DLA) + b'\x00' + struct.pack('<II', u, 0) for u in layers))

def set_own_filter(body, layers):
    f = own_filter(body)
    if f is None:
        raise ValueError('no own DataLayerFilter after the last component')
    return body[:f[0]] + own_filter_bytes(layers) + body[f[1]:]

def own_allows(body, layer):
    f = own_filter(body)
    return f is None or not f[2] or layer in f[2]

def filter_lists(table):
    import convert
    ids = convert.class_ids()
    dlf = struct.pack('<I', ids['DataLayerFilter'])
    dla = struct.pack('<I', ids['DataLayerAction'])
    return [[_u32(seg, 1) for seg in part.split(dla)[1:] if len(seg) >= 5] for part in (table or b'').split(dlf)[1:]]

def check(path):
    r = dict(archive=os.path.basename(path), path=path, problems=[], warnings=[])
    if not os.path.exists(path):
        r.update(verdict='FAIL', problems=['archive missing'])
        return r
    try:
        wbody, wrec = world_of(path)
    except (StopIteration, LookupError, Exception) as e:
        r.update(verdict='FAIL', problems=[f'no World: {e!r}'[:160]])
        return r
    t = parse_tail(wbody)
    refs = {ref for p in t['paths'] for _nu, ref, _a, _b in p['nodes']}
    chests = set(t['chests'])
    defs = {}
    world_lists, block, n_flow = (None, None, 0)
    starts, scenes, respawns = ({}, 0, 0)
    oob = 0
    dropped = {}
    with Forge(path) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            is_block, is_cell = (rec.name == BLOCK, rec.name.startswith('Cell'))
            if rec.name.startswith('DataBlock_') and (not is_block):
                continue
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
                    world_lists = bool(world_lists) or ESCORT in dom_check.u32s(o['body'])
                if cls != 'Entity' or not o['has_uid']:
                    continue
                layers = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else None
                on_escort = is_block or (layers is not None and ESCORT in layers)
                resident = is_block or ((is_cell or rec.index == 1) and (layers is None or ESCORT in layers))
                if o['uid'] in refs or o['uid'] in chests:
                    defs.setdefault(o['uid'], []).append((rec.name, cls, comps(o), resident))
                if not (is_block or is_cell):
                    continue
                cs = None
                if resident and is_cell:
                    cs = comps(o)
                    n_flow += 'CrowdFlow' in cs
                if not on_escort:
                    continue
                k = spawn_layers.spawn_kind(o['body'])
                if is_block and layers is not None and (ESCORT not in layers):
                    continue
                if not own_allows(o['body'], ESCORT):
                    kind = ('team start' if k[0] == 2 else 'respawn' if k[0] == 0 else f'type-{k[0]} spawn') if k else 'out-of-bounds entity' if 'OutOfBoundsComponent' in (cs or comps(o)) else None
                    if kind:
                        dropped.setdefault(kind, []).append(o['name'] or f"{o['uid']:#x}")
                    continue
                if k and k[0] == 2:
                    starts[k[1]] = starts.get(k[1], 0) + 1
                    scenes += 'Scene' in (cs or comps(o))
                elif k and k[0] == 0:
                    respawns += 1
                elif is_block and 'OutOfBoundsComponent' in (cs or comps(o)):
                    oob += 1
    nodes_bad, nodes_off, nodes_noflow = ([], [], [])
    for ref in sorted(refs):
        d = defs.get(ref, [])
        if not d:
            nodes_bad.append(ref)
        elif not any((res for _r, _c, _cs, res in d)):
            nodes_off.append(ref)
        elif not any(('CrowdFlow' in cs for _r, _c, cs, _res in d)):
            nodes_noflow.append(ref)
    r.update(vip_skins=vip_skins(), world=wrec, paths=len(t['paths']), nodes=sum((len(p['nodes']) for p in t['paths'])), chests=len(t['chests']), crowdflow=n_flow, starts=dict(sorted(starts.items())), scenes=scenes, respawns=respawns, oob=oob, dropped={k: len(v) for k, v in dropped.items()}, path_detail=[dict(uid=f"{p['uid']:#x}", nodes=len(p['nodes']), spawn=sum((1 for n in p['nodes'] if n[2])), checkpoints=sum((1 for n in p['nodes'] if n[3]))) for p in t['paths']])
    if t['error']:
        r['problems'].append(f"World tail: {t['error']}")
    for kind, names in sorted(dropped.items()):
        r['problems'].append(f"{len(names)} {kind}(s) on the Escort layer whose own DataLayerFilter (Entity +0xAC) leaves ACFE_Escort out - the game drops them as they join the match (0x4DE87D; R26T3's crash): " + ', '.join(names[:4]) + (' ...' if len(names) > 4 else ''))
    if world_lists is None:
        r['warnings'].append('no WorldDataLayerManager found')
    if not t['paths'] and (not starts):
        r['verdict'] = 'EMPTY'
        return r
    if not t['paths']:
        r['problems'].append("no VIP path in the World's tail (no VIP ever spawns)")
    elif len(t['paths']) < NB_VIPS:
        r['warnings'].append(f"{len(t['paths'])} path(s) for {NB_VIPS} VIPs")
    for p in t['paths']:
        if not p['nodes']:
            r['problems'].append(f"path {p['uid']:#x} has no node")
        elif not any((n[2] for n in p['nodes'])):
            r['warnings'].append(f"path {p['uid']:#x}: no node with the first flag (spawn) set")
        elif not any((n[3] for n in p['nodes'])):
            r['warnings'].append(f"path {p['uid']:#x}: no node with the second flag (checkpoint) set")
    if nodes_bad:
        r['problems'].append(f'{len(nodes_bad)} node ref(s) no entity of the archive defines: ' + ', '.join((f'{u:#x}' for u in nodes_bad[:6])))
    if nodes_off:
        r['problems'].append(f'{len(nodes_off)} node entities not resident in Escort (filtered off the layer / outside the cells): ' + ', '.join((f'{u:#x}' for u in nodes_off[:6])))
    if nodes_noflow:
        r['warnings'].append(f'{len(nodes_noflow)} node entities without a CrowdFlow component')
    missing_chests = [u for u in chests if u not in defs]
    if missing_chests:
        r['warnings'].append(f'{len(missing_chests)} chest ref(s) no entity defines')
    if n_flow == 0:
        r['problems'].append('no resident CrowdFlow entity: no crowd for the VIPs')
    if world_lists is False:
        r['problems'].append("the World's WorldDataLayerManager does not list ACFE_Escort")
    if block is None:
        r['problems'].append(f'no {BLOCK} record')
    if not starts:
        r['problems'].append('no Escort team start (type 2): the 0x580BC6 spawn-pick crash')
    else:
        if scenes < sum(starts.values()):
            r['problems'].append(f'{sum(starts.values()) - scenes} of {sum(starts.values())} team starts without an intro Scene (scene-less team starts crashed AC3, mode_spawns S11 / S12)')
        if len(starts) < 2:
            r['problems'].append(f'team starts on one index only {sorted(starts)}: two teams need two groups')
        for i, n in starts.items():
            if n < 4:
                r['warnings'].append(f'team index {i}: {n} starts (4 players per team)')
    if respawns == 0:
        r['problems'].append('no type-0 respawn on the Escort layer')
    if r['vip_skins'] < NB_VIPS:
        r['problems'].append(f"{r['vip_skins']} crowd skin(s) left for {NB_VIPS} VIPs with {MAX_PLAYERS} players (ChooseVipSkins retries forever)")
    if oob == 0:
        r['warnings'].append('no out-of-bounds entity in the Escort block')
    r['verdict'] = 'FAIL' if r['problems'] else 'WARN' if r['warnings'] else 'OK'
    return r

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('archives', nargs='*')
    ap.add_argument('--release', action='store_true')
    ap.add_argument('--only')
    ap.add_argument('--json')
    ap.add_argument('-v', action='store_true')
    a = ap.parse_args()
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
        print(f"{r['verdict']:5} {r['archive']:44} paths={r.get('paths')} nodes={r.get('nodes')} chests={r.get('chests')} crowdflow={r.get('crowdflow')} starts={r.get('starts')} scenes={r.get('scenes')} respawns={r.get('respawns')} oob={r.get('oob')} vip_skins={r.get('vip_skins')}" + (f'  - {extra}' if extra else ''), flush=True)
        if a.v:
            for d in r.get('path_detail', []):
                print(f"        path {d['uid']}: {d['nodes']} nodes, +4 set (spawn?) {d['spawn']}, +5 set (checkpoint?) {d['checkpoints']}")
    counts = {v: sum((r['verdict'] == v for r in res)) for v in ('OK', 'WARN', 'FAIL', 'EMPTY')}
    print('summary:', ', '.join((f'{n} {v}' for v, n in counts.items() if n)))
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump(res, fh, indent=1, default=str)
    sys.exit(1 if bad else 0)
if __name__ == '__main__':
    main()
