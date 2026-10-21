"""forgelib/audio/ac4_seeds.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import collections
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOUND_INSTANCE = bytes.fromhex('7ff8427e')
SOUND_DESCRIPTOR = bytes.fromhex('ec3e8ebb')
AC4_FORM = SOUND_INSTANCE + bytes(6) + SOUND_DESCRIPTOR
AC3_FORM = SOUND_INSTANCE + bytes(5) + SOUND_DESCRIPTOR
AUDIO_EVENT_SEED = bytes.fromhex('fc061e56')
WWISE_ID = bytes.fromhex('7e57879e')

class SeedError(ValueError):
    pass

def _paths():
    for p in (ROOT, os.path.join(ROOT, 'acbchars'), os.path.join(ROOT, 'sp'), os.path.join(ROOT, 'ac4chars'), os.path.join(ROOT, 'ac4')):
        if p not in sys.path:
            sys.path.insert(0, p)

def seeds(body):
    _paths()
    import anim_cut
    out = []
    trs, _td = anim_cut.tracks(body)
    for _s, _e, ss, _t, _en in trs:
        for a, z in ss:
            out.append((a, z, anim_cut.CLS.get(int.from_bytes(body[a + 5:a + 9], 'little'), '?')))
    return out

def events(body):
    out = []
    for a, z, n in seeds(body):
        if n == 'AudioEventSeed':
            seg = body[a:z]
            k = seg.find(WWISE_ID)
            if k >= 0:
                out.append(int.from_bytes(seg[k + 4:k + 8], 'little'))
    return out

def to_ac3(body):
    cuts = []
    for a, z, n in seeds(body):
        if n != 'AudioEventSeed':
            continue
        seg = body[a:z]
        k = seg.find(AC4_FORM)
        if k < 0:
            if AC3_FORM in seg:
                continue
            raise SeedError(f"an AudioEventSeed at {a:#x} without AC4's SoundInstance form: {seg[:40].hex(' ')}")
        if seg.find(AC4_FORM, k + 1) >= 0:
            raise SeedError(f'an AudioEventSeed at {a:#x} with two SoundInstances')
        cuts.append(a + k + len(SOUND_INSTANCE) + 5)
    out = bytearray(body)
    for c in sorted(cuts, reverse=True):
        assert out[c] == 0
        del out[c]
    return bytes(out)

def gate():
    _paths()
    import pickle
    cache = os.path.join(ROOT, 'out', 'audio', 'ac4', '_gamefix_pairs.pkl')
    if os.path.exists(cache):
        a3, a4 = pickle.load(open(cache, 'rb'))
    else:
        sys.path.insert(0, os.path.join(ROOT, 'out', 'ac4chars'))
        import _anim_pairs as pairs
        a3, a4 = (pairs.anims_ac3({'Game Fix'}), pairs.anims_ac4({'Game Fix'}))
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        pickle.dump((a3, a4), open(cache, 'wb'))
    tally = collections.Counter()
    for name in sorted(set(a3) & set(a4)):
        y = to_ac3(a4[name])
        s3 = [(n, a3[name][a:z]) for a, z, n in seeds(a3[name]) if n.endswith('EventSeed') and 'Sound' in n or n in ('AudioEventSeed', 'MultiVoiceEventSeed')]
        s4 = [(n, y[a:z]) for a, z, n in seeds(y) if n.endswith('EventSeed') and 'Sound' in n or n in ('AudioEventSeed', 'MultiVoiceEventSeed')]
        if len(s3) != len(s4):
            tally['clips whose sound seeds AC4 re-authored (count differs)'] += 1
            continue
        for (n3, x), (n4, z) in zip(s3, s4):
            if n3 != n4:
                tally['seed class differs (re-authored)'] += 1
            elif x == z:
                tally[f"{n3}: identical to AC3's"] += 1
            elif x[:9] == z[:9] and len(x) == len(z):
                tally[f'{n3}: same layout, other content (AC4 re-authored)'] += 1
            else:
                tally[f'{n3}: LAYOUT DIFFERS'] += 1
    for k, v in sorted(tally.items()):
        print(f'  {v:5}  {k}')
    return not any(('LAYOUT' in k for k in tally))

def guns():
    _paths()
    import anim4
    import anim_cut
    import weapon_class as wc
    _r, plain = wc.read_record(wc.AC4_DATAPC, wc.AC4_RECORD)
    anims = [o for o in wc.objects(plain) if o['type'] == wc.ANIMATION]
    saved = anim4.SOUND_SEEDS
    anim4.SOUND_SEEDS = ()
    tally, posted = (collections.Counter(), collections.Counter())
    try:
        for o in anims:
            b = to_ac3(o['body'])
            for e in events(b):
                posted[e] += 1
            try:
                c = anim4.to_ac3(b)
                tally["converts and passes AC3's clip check, sound seeds kept"] += 1
                kept = sum((1 for _a, _z, n in seeds(c) if n in ('AudioEventSeed', 'MultiVoiceEventSeed')))
                tally['sound seeds kept'] += kept
            except Exception as e:
                tally[f'FAILS: {type(e).__name__}: {str(e)[:80]}'] += 1
    finally:
        anim4.SOUND_SEEDS = saved
    for k, v in sorted(tally.items()):
        print(f'  {v:5}  {k}')
    print(f'  {len(posted)} distinct events posted by {sum(posted.values())} AudioEventSeeds')
    return not any((k.startswith('FAILS') for k in tally))
if __name__ == '__main__':
    if sys.argv[1:2] == ['--gate']:
        sys.exit(0 if gate() else 1)
    if sys.argv[1:2] == ['--guns']:
        sys.exit(0 if guns() else 1)
    print(__doc__)
