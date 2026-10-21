"""forgelib/maps/coverage.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import changed_classes
import convert
import mapwalk
import xrefs
from summary import BULK
PROVEN_SAME = {'MeshInstanceData', 'SkeletonComponent', 'SoundEmitter', 'SoundEmitterComponent', 'AudioEventSeed', 'BhvGenericNPC', 'CharacterAI', 'Human', 'BipedComponent', 'BhvAssassin', 'hkGizmoAttribute', 'SFX_MenuWS_Money_Selected', 'TargetingManager', 'WorldEngineData', 'LayeredSky'}
RULED = set(convert.GREW_ONE_BYTE) | set(convert.CHASE_BREAKER_RULED) | set(convert.READS_SAME)

def classify(game, archive, targets, changed_ids):
    buckets = Counter()
    size = Counter()
    unknown = defaultdict(Counter)
    for p in mapwalk.payloads(game, archive, skip_errors=True):
        for o in p.objects:
            cls = xrefs.cls(game, o['type'])
            body = o['body']
            if cls == 'MaterialTemplate':
                b = 'shader'
            elif cls == 'Animation':
                b = 'animation'
            elif cls in BULK:
                b = 'asset'
            elif cls == 'World':
                b = 'world'
            else:
                inside = {n for nb, n in changed_ids.items() if nb in body} - PROVEN_SAME
                if inside - RULED:
                    b = 'unknown'
                    for n in inside - RULED:
                        unknown[n][cls] += 1
                else:
                    conv, log = convert.convert(body, targets)
                    b = 'ruled' if conv != body else 'clean'
            buckets[b] += 1
            size[b] += len(body)
    return (buckets, size, unknown)

def main(argv):
    targets = convert.targets('ACR')
    ch = changed_classes.changed()
    changed_ids = {struct.pack('<I', t): n for t, (n, _a, _b) in ch.items() if n not in ('Animation', 'World', 'MaterialTemplate')}
    todo = [a.split(':', 1) for a in argv] or [('ACR', a) for a in mapwalk.map_archives('ACR')]
    order = ('asset', 'clean', 'ruled', 'world', 'animation', 'shader', 'unknown')
    print(f"{'map':22}" + ''.join((f'{b:>15}' for b in order)))
    total_unknown = defaultdict(Counter)
    for game, archive in todo:
        buckets, size, unknown = classify(game, archive, targets, changed_ids)
        print(f'{archive[7:-6][:22]:22}' + ''.join((f'{buckets[b]:>7,}{size[b] / 1000000.0:>6.1f}MB' for b in order)), flush=True)
        for n, c in unknown.items():
            total_unknown[n].update(c)
    print('\nunknown, by changed class (objects containing it, all maps listed):')
    for n, c in sorted(total_unknown.items(), key=lambda kv: -sum(kv[1].values())):
        print(f'   {n:32} {sum(c.values()):6,}   in ' + ', '.join((f'{k} {v}' for k, v in c.most_common(3))))
if __name__ == '__main__':
    main(sys.argv[1:])
