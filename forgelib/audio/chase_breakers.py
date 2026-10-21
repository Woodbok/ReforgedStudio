"""forgelib/audio/chase_breakers.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import copy
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import hirc_convert as hc
    import media
    import rules
except ImportError:
    hc = media = rules = None
from overlap import fnv1
EVENTS = {old: fnv1(f'acr_forge_chasebreaker_{old:08x}') for old in (3655559871, 2887084370)}
MIXER = 647244656
GLOBAL_BANK = 'BNK_Bootstrap_Wwise_MP'
ALIGN = 16

def _children(t):
    for n in t.walk():
        if n.name == 'Children':
            return [it[2] for it in n.items if it[1] == 'ulChildID']
    return []

def _remap(t, ids):
    for n in t.walk():
        for it in n.items:
            if it[0] == 'f' and isinstance(it[2], int) and (it[2] in ids):
                it[2] = ids[it[2]]

def objects(acr_objs, acr_data, acr_didx, ac3_objs, ids, report):
    new_ids, trees, media_out = ({}, [], {})
    plan = []
    for ev, nev in EVENTS.items():
        acts = [n.f('ulActionID') for n in acr_objs[ev][1].walk() if n.name == 'Action']
        if len(acts) != 1:
            raise SystemExit(f'event {ev:#x}: {len(acts)} actions, expected one Play')
        act = acts[0]
        target = next((n for n in acr_objs[act][1].walk() if n.name == 'ActionInitialValues')).f('ulTargetID')
        if acr_objs[target][0] != 5:
            raise SystemExit(f'event {ev:#x} plays {target:#x}, not a RanSeqCntr')
        sounds = _children(acr_objs[target][1])
        plan.append((ev, nev, act, target, sounds))
        new_ids[ev] = nev
        for i in [act, target] + sounds:
            new_ids[i] = ids.new(f'acr_forge_chasebreaker_obj_{i:08x}')
    new_mixer = ids.new(f'acr_forge_chasebreaker_obj_{MIXER:08x}')
    new_ids[MIXER] = new_mixer
    for _ev, _nev, _act, target, sounds in plan:
        for s in sounds:
            for n in acr_objs[s][1].walk():
                if n.name == 'AkMediaInformation':
                    mid = n.f('sourceID')
                    if mid not in acr_didx:
                        raise SystemExit(f"sound {s:#x}: clip {mid:#x} is not embedded in ACR's bank")
                    nid = ids.new(f'acr_forge_chasebreaker_media_{mid:08x}')
                    new_ids[mid] = nid
                    off, size = acr_didx[mid]
                    media_out[nid] = media.convert(acr_data[off:off + size])
    hc.set_version(hc.DST_VERSION)
    for ev, nev, act, target, sounds in plan:
        for s in sounds:
            t = rules.convert(2, copy.deepcopy(acr_objs[s][1]))
            _remap(t, new_ids)
            t.set('ulID', new_ids[s])
            trees.append((2, t))
        t = rules.convert(5, copy.deepcopy(acr_objs[target][1]))
        _remap(t, new_ids)
        t.set('ulID', new_ids[target])
        trees.append((5, t))
    mixer = copy.deepcopy(ac3_objs[MIXER][1])
    mixer.set('ulID', new_mixer)
    ch = next((n for n in mixer.walk() if n.name == 'Children'))
    kids = sorted((new_ids[target] for _e, _n, _a, target, _s in plan))
    ch.items = [it for it in ch.items if it[1] != 'ulChildID']
    ch.set('ulNumChilds', len(kids))
    ch.items += [['f', 'ulChildID', k] for k in kids]
    trees.append((7, mixer))
    for ev, nev, act, target, sounds in plan:
        t = rules.convert(3, copy.deepcopy(acr_objs[act][1]))
        _remap(t, new_ids)
        t.set('ulID', new_ids[act])
        trees.append((3, t))
    for ev, nev, act, target, sounds in plan:
        trees.append((4, struct.pack('<III', nev, 1, new_ids[act])))
    known = set(new_ids.values()) | set(ac3_objs)
    for t_, t in trees:
        if t_ == 4:
            continue
        for n in t.walk():
            for k, name, v in n.items:
                if k == 'f' and isinstance(v, int) and (v in acr_objs) and (v not in known) and (name not in ('uFileID',)):
                    raise SystemExit(f"{t.f('ulID'):#x} {n.name}.{name} -> {v:#x}: ACR-only, not brought along")
    atts = sorted({n.f('uAttenuationID') for t_, t in trees if t_ != 4 for n in t.walk() if n.name == 'PositioningParams' and n.f('uAttenuationID', 0)})
    for a in atts:
        if a not in ac3_objs or ac3_objs[a][0] != 14:
            raise SystemExit(f"attenuation {a:#x} is not in AC3's {GLOBAL_BANK}")
    trees[:0] = [(14, copy.deepcopy(ac3_objs[a][1])) for a in atts]
    report.append(f"chase breakers: attenuations copied (same id, as AC3's map banks do): {[hex(a) for a in atts]}")
    report.append(f"chase breakers: events {', '.join((f'{o:#x} -> {n:#x}' for o, n in EVENTS.items()))}; {sum((1 for t, _ in trees if t == 2))} sounds, {sum((1 for t, _ in trees if t == 5))} containers, mixer {MIXER:#x} -> {new_mixer:#x}; {len(media_out)} embedded clips")
    return (trees, media_out)

def add_to_bank(data, bank_id, trees, media_in, chunks_of=None, hirc_of=None):
    import bank_build
    return bank_build.embed(data, bank_id, trees, media_in)
WWISE_ID = 2659669886

def remap_body(body, mapping=None):
    mapping = EVENTS if mapping is None else mapping
    cls = struct.pack('<I', WWISE_ID)
    out, counts = (bytearray(body), {})
    for old, new in mapping.items():
        pat = cls + struct.pack('<I', old)
        k = out.find(pat)
        while k >= 0:
            struct.pack_into('<I', out, k + 4, new)
            counts[old] = counts.get(old, 0) + 1
            k = out.find(pat, k + 8)
    return (bytes(out), counts)
