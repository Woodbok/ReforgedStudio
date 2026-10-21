"""forgelib/maps/chase_breaker_check.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import mapwalk
import world
from forge import Forge
CLIPS = {'BlendPosClip', 'ActivateClip', 'SoundPlayClip', 'NothingClip'}

def parse(body):
    segs, o = ([], 0)
    for nm, x in world.segments(body):
        segs.append((o, nm, x))
        o += len(x)
    children, elements = ([], [])
    for i, (o, nm, x) in enumerate(segs):
        if nm == 'Entity' and o >= 4:
            uid = struct.unpack_from('<I', body, o - 4)[0]
            comps, gsd = (set(), None)
            for _o2, n2, x2 in segs[i + 1:]:
                if n2 == 'Entity':
                    break
                comps.add(n2)
                if n2 == 'GameStateData':
                    gsd = x2
            children.append((uid, o, comps, gsd))
        elif nm == 'ChaseBreakerElement' and len(x) >= 9:
            target = struct.unpack_from('<I', x, 5)[0]
            clips, inert = ({}, [])
            for _o2, n2, x2 in segs[i + 1:]:
                if n2 in ('ChaseBreakerElement', 'ChaseBreakerComponent', 'TriggerComponent', 'Entity'):
                    break
                if n2 in CLIPS:
                    clips[n2] = clips.get(n2, 0) + 1
                if n2 == 'InertComponent':
                    inert.append(x2)
            elements.append((target, clips, inert))
    return (children, elements)

def check_group(uid, body):
    children, elements = parse(body)
    kids = {c[0]: c for c in children}
    bad = []
    movers = []
    for k, (t, clips, inert) in enumerate(elements, 1):
        if t != uid and t not in kids:
            bad.append(f'element {k} names {t:#010x}: neither the group nor a child')
            continue
        if clips.get('BlendPosClip') and t != uid:
            movers.append(t)
            if 'Visual' not in kids[t][2]:
                bad.append(f'element {k} (BlendPosClip) drives {t:#010x}, which has no Visual')
        if clips.get('ActivateClip'):
            for x in inert:
                if struct.pack('<I', t) not in x:
                    bad.append(f"element {k}: an InertComponent doesn't name its entity {t:#010x}")
    dup = {t for t in movers if movers.count(t) > 1}
    if dup:
        bad.append('one entity driven by two door elements: ' + ', '.join((f'{t:#010x}' for t in sorted(dup))))
    for c_uid, _o, _comps, gsd in children:
        if gsd is not None and struct.pack('<I', c_uid) not in gsd:
            bad.append(f"child {c_uid:#010x}'s GameStateData doesn't name it")
    if len(set(movers)) >= 2 and (not dup):
        box, line = (trigger_box(body), door_line(body, children, set(movers)))
        if box is not None and (not box[0] - box[1] - LINE_TOL <= line <= box[0] + box[1] + LINE_TOL):
            bad.append(f'trigger box x [{box[0] - box[1]:.2f}, {box[0] + box[1]:.2f}] off the door line {line:.2f}')
    return bad
LINE_TOL = 0.1

def trigger_box(body):
    o = 0
    for nm, x in world.segments(body):
        if nm == 'TriggerZone' and len(x) >= 161 and (struct.unpack_from('<I', x, 9)[0] == 3):
            return (struct.unpack_from('<f', x, 129)[0], struct.unpack_from('<f', x, 145)[0])
        o += len(x)
    return None

def door_line(body, children, leaves):
    grot, gpos = (struct.unpack_from('<12f', body, 10), struct.unpack_from('<3f', body, 58))
    xs = [sum(((struct.unpack_from('<3f', body, eo + 52)[k] - gpos[k]) * grot[k] for k in range(3))) for uid, eo, _c, _g in children if uid in leaves]
    return sum(xs) / len(xs)

def groups(path):
    out = []
    with Forge(path) as f:
        for r in f.records:
            if not r.size or r.name == 'GlobalMetaFile':
                continue
            try:
                p = mapwalk.Payload(r, f.read(r))
            except Exception:
                continue
            for o in p.objects:
                b = o['body']
                if b'U\x18[.' not in b:
                    continue
                names = {nm for nm, _x in world.segments(b)}
                if 'ChaseBreakerElement' in names:
                    out.append((o['name'] or f"{o['uid']:#010x}", o['uid'], b))
    return out

def check(path, verbose=False):
    gs = groups(path)
    n_bad = 0
    for name, uid, body in sorted(gs):
        bad = check_group(uid, body)
        if bad:
            n_bad += 1
            print(f'  BROKEN {name} {uid:#010x}: ' + '; '.join(bad))
        elif verbose:
            print(f'  ok {name}')
    print(f'{os.path.basename(path)}: {len(gs)} chase breakers, {n_bad} broken')
    return n_bad == 0

def show(path, name):
    for n, uid, body in groups(path):
        if n == name:
            children, elements = parse(body)
            print(f'{n} {uid:#010x}')
            for c_uid, o, comps, _g in children:
                print(f"  child {c_uid:#010x} @{o}: {sorted(comps & {'Visual', 'RigidBody', 'InertComponent', 'FXComponent'})}")
            for k, (t, clips, _i) in enumerate(elements, 1):
                print(f'  element {k} -> {t:#010x} {clips}')
            print('  ' + ('; '.join(check_group(uid, body)) or 'wired right'))

def release_maps():
    rs = json.load(open(os.path.join(ROOT, 'release_set.json'), encoding='utf-8'))
    shared = {'DataPC.forge', 'DataPC_extra.forge', 'DataPC_CoreFX.forge', 'DataPC_Fire.forge'}
    return [os.path.join(ROOT, f['src']) for f in rs['files'] if f['src'].endswith('.forge') and f['dest'].split('\\')[-1] not in shared]

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('archives', nargs='*')
    ap.add_argument('--release', action='store_true')
    ap.add_argument('--show')
    ap.add_argument('-v', action='store_true')
    a = ap.parse_args(argv)
    if a.show:
        for p in a.archives:
            show(p, a.show)
        return
    ok = True
    for p in a.archives + (release_maps() if a.release else []):
        ok &= check(p, a.v)
    raise SystemExit(0 if ok else 1)
if __name__ == '__main__':
    main(sys.argv[1:])
