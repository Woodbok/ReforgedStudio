"""forgelib/crossmaps/acb_rules.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acb
acb.patch()
import world
CFI_EXTRA = struct.pack('<I', 5)
TRIOVIZ = 2365545647
ONE = struct.pack('<f', 1.0)
FIVE = struct.pack('<f', 5.0)
FX_TYPE_GROWN = struct.pack('<I', 2338409748)
FX_INSERT = bytes.fromhex('01000000e60af3570000000000000700')

def convert(cls, body, uid):
    segs = world.segments(body)
    if len(segs) == 1:
        return (body, Counter())
    out, log = ([], Counter())
    for i, (name, seg) in enumerate(segs):
        if name == 'CollisionFilterInfo' and len(seg) in (48, 52, 54) and (seg[12:16] == CFI_EXTRA):
            seg = seg[:12] + seg[16:]
            log['CollisionFilterInfo -u32'] += 1
        elif name == 'MotionBlurSettings' and cls == 'WorldAmbiance' and (len(seg) == 30) and (not any((n == 'Trioviz3DSettings' for n, _s in segs))):
            seg = seg[:6] + struct.pack('<II', uid(), TRIOVIZ) + b'\x00' + seg[6:]
            log['WorldAmbiance +Trioviz3DSettings'] += 1
        elif name == 'BlobStrategyParams' and len(seg) == 25 and (seg[16] == 3):
            seg = seg[:17] + b'\x03' + seg[17:]
            log['BlobStrategyParams +u8 3'] += 1
        elif name == 'FreeRunCornerTurnComponent' and len(seg) == 15:
            seg = seg[:9] + FIVE + seg[9:]
            log['FreeRunCornerTurnComponent +f32 5.0'] += 1
        elif name == 'FXCommand' and len(seg) >= 24 and (seg[12:16] == FX_TYPE_GROWN) and (seg[20:36] != FX_INSERT):
            seg = seg[:20] + FX_INSERT + seg[20:]
            log['FXCommand 0x8B614D14 +16 B'] += 1
        elif (name, len(seg)) in (('Bone', 148), ('SkeletonPoseBone', 32), ('SpringBoxModifier', 131)):
            seg = seg + ONE
            log[f'{name} +f32 1.0'] += 1
        out.append(seg)
    return (b''.join(out), log)
UNRULED = {'Hinge', 'CompressBoneModifier', 'HingeBoneModifier'}
FROM_ACR = {'Skeleton', 'Cloth', 'HumanSoundSet', 'BuildTable', 'RagdollNew', 'BodyPartMapping', 'BodyPartTemplate', 'ContactTable', 'LookAtDataGeneral', 'ReactionPack', 'MultiReactionPack', 'AnimSet', 'MaterialTemplate', 'Material'}
CHAIN_IMPORTS = {1921337557, 397085589}
ANIMATION = 262342271

def clip_id(body):
    n = struct.unpack_from('<I', body, 4)[0]
    return struct.unpack_from('<I', body, 9 + 12 * n)[0]

def acr_copies(want, want_clips=()):
    import mapwalk
    out, clips = ({}, {})
    want_clips = set(want_clips)
    for a in mapwalk.map_archives('ACR'):
        if a in acb.ACB_MAPS:
            continue
        for p in mapwalk.payloads('ACR', a, skip_errors=True):
            for o in p.objects:
                if o['has_uid'] and (o['uid'], o['name']) in want and (o['uid'] not in out):
                    out[o['uid']] = (o['body'], o['body_size'])
                elif o['type'] == ANIMATION and want_clips:
                    k = (o['name'], clip_id(o['body']))
                    if k in want_clips and k not in clips:
                        clips[k] = (o['body'], o['body_size'])
        if len(out) == len(want) and len(clips) == len(want_clips):
            break
    return (out, clips)

def gate(maps=('Firenze', 'Palazzio')):
    from acb_formats import objs, B, R, PAIRS
    uids = iter(range(1528758272, 1528823808))
    rows = Counter()
    rules = Counter()
    worse = []
    for m in maps:
        a_name, r_name = PAIRS[m]
        a, r = (objs(os.path.join(B, a_name)), objs(os.path.join(R, r_name)))
        for u in set(a) & set(r):
            cls, name, ba = a[u]
            br = r[u][2]
            new, log = convert(cls, ba, lambda: next(uids))
            rules.update(log)
            if cls == 'WorldAmbiance' and log:
                k = new.find(struct.pack('<I', TRIOVIZ))
                new = new[:k - 4] + br[k - 4:k] + new[k:]
            before, after = (ba == br, new == br)
            rows[cls, before, after] += 1
            if before and (not after):
                worse.append((m, cls, name))
            if not after and len(new) == len(br) and (len(ba) != len(br)):
                rows[cls, 'size fixed, content differs'] += 1
    return (rows, rules, worse)

def main(argv):
    rows, rules, worse = gate()
    print('rules applied:', dict(rules))
    classes = sorted({k[0] for k in rows})
    print(f"{'class':28} {'same before':>12} {'same after':>11} {'differ':>7} {'size fixed, content differs':>28}")
    for c in classes:
        same_b = sum((n for k, n in rows.items() if k[0] == c and len(k) == 3 and k[1]))
        same_a = sum((n for k, n in rows.items() if k[0] == c and len(k) == 3 and k[2]))
        diff = sum((n for k, n in rows.items() if k[0] == c and len(k) == 3 and (not k[2])))
        fixed = rows.get((c, 'size fixed, content differs'), 0)
        if same_b != same_a or diff:
            print(f'{c:28} {same_b:12d} {same_a:11d} {diff:7d} {fixed:28d}')
    print('identical before, not after (a rule broke them):', len(worse), worse[:10])
if __name__ == '__main__':
    main(sys.argv[1:])
