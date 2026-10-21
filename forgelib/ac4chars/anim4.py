"""forgelib/ac4chars/anim4.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE, os.path.join(ROOT, 'acbchars'), os.path.join(ROOT, 'sp')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import anim_cut
import sp_anim
AC4_ONLY = {1374662609: 'WeaponFXEventSeed', 295650376: 'WeaponFXEvent'}
SOUND_SEEDS = ('AudioEventSeed', 'MultiVoiceEventSeed', 'SoundSetEventSeed')
OBJECT_SEEDS = ('FXEventSeed', 'PlayFacialCustomActionEventSeed')

def _know_ac4_classes():
    for k, v in AC4_ONLY.items():
        anim_cut.CLS.setdefault(k, v)
        sp_anim._classes.setdefault(k, v)
    anim_cut.SEED_CLASSES.update((k for k, v in AC4_ONLY.items() if v.endswith('EventSeed')))
_know_ac4_classes()

class ClipError(ValueError):
    pass

def seed_classes(body):
    out = {}
    trs, _td = anim_cut.tracks(body)
    for _s, _e, seeds, _times, _enum in trs:
        for a, _z in seeds:
            name = anim_cut.CLS.get(int.from_bytes(body[a + 5:a + 9], 'little'), '?')
            out[name] = out.get(name, 0) + 1
    return out

def events_only(body):
    _trs, td = anim_cut.tracks(body)
    if any((k == sp_anim.MAPPING for p, k in sp_anim.markers(body) if p > td)):
        return False
    tail = body[td + 9:]
    if len(tail) != 14 or any(tail[4:]):
        raise ClipError(f'an AnimTrackData with no mapping but a tail of {len(tail)} bytes')
    return True

def drop_seeds_where(body, pred):
    import struct
    n_drop = 0
    _n, _recs, c = anim_cut.header(body)
    trs, td = anim_cut.tracks(body)
    new_tracks = []
    for s, _e, seeds, times, enum_at in trs:
        keep = []
        for (x, y), t in zip(seeds, times):
            if pred(body[x:y], anim_cut.CLS.get(struct.unpack_from('<I', body, x + 5)[0], '?')):
                n_drop += 1
            else:
                keep.append((x, y, t))
        if not keep:
            continue
        out = body[s:s + 11] + struct.pack('<I', len(keep)) + b''.join((body[x:y] for x, y, _ in keep))
        out += body[enum_at:enum_at + 4] + struct.pack('<H', len(keep)) + b''.join((struct.pack('<H', t) for _, _, t in keep))
        new_tracks.append(out)
    if not n_drop:
        return (body, 0)
    head = bytearray(body[:c + 27])
    struct.pack_into('<I', head, c + 23, len(new_tracks))
    new = bytes(head) + b''.join(new_tracks) + body[td:]
    return (anim_cut.recount(new, body), n_drop)
FX_OBJECT_AT = 39

def to_ac3(body, keep_fx=False, keep_sound=False, fx_rule=False):
    if keep_sound:
        sys.path.insert(0, os.path.join(ROOT, 'audio'))
        import ac4_seeds
        body = ac4_seeds.to_ac3(body)
    drop = ([] if keep_sound else list(SOUND_SEEDS)) + [v for v in AC4_ONLY.values() if v.endswith('EventSeed')]
    if fx_rule and (not keep_fx):
        if ROOT not in sys.path:
            sys.path.append(ROOT)
        import fx_carry
        kept = body
        if any((n in seed_classes(body) for n in drop + ['PlayFacialCustomActionEventSeed'])):
            kept = anim_cut.drop_seeds(body, drop + ['PlayFacialCustomActionEventSeed'])
        if fx_carry.wants_seeds(kept):
            body, keep_fx, drop = (fx_carry.seed_rule(kept)[0], True, [])
    if not keep_fx:
        drop += list(OBJECT_SEEDS)
    present = seed_classes(body)
    if any((n in present for n in drop)):
        body = anim_cut.drop_seeds(body, drop)
    if events_only(body):
        return body
    body = anim_cut.fit_arenas(body)
    problems = anim_cut.check(body)
    if problems:
        raise ClipError('; '.join(problems[:4]))
    return body
