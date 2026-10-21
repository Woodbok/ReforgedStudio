"""forgelib/audio/acb_music.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import collections
import copy
import os
import pickle
import struct
import sys
import zlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import paths
import hirc_convert as hc
import akpk
from banks import chunks
from overlap import fnv1
ACB_SOUND = os.path.join(os.path.dirname(os.path.dirname(paths.AC3)), "Assassin's Creed Brotherhood", 'SoundData', 'pc')
ACB_VERSION = 48
MUSIC_BANK = 'BNK_Bootstrap_Wwise_MP_Startup'
ACR_PHASE_GROUP, ACR_LAYER_GROUP, MAP_GROUP = (1896437164, 1914173396, 3979479726)
ACB_ROOT = 99104429
ACB_PHASE_GROUP = 2203405182
A_THEME, A_MID, A_HIGH, A_START, A_END_1, A_END_2 = (231174514, 1532182106, 504047562, 3461392737, 1907594529, 1175884700)
PHASE_MAP_A19 = {1030475738: A_THEME, 4159759944: A_THEME, 3984178565: A_THEME, 2553971516: A_MID, 2225075240: A_MID, 3283539198: A_HIGH, 526690563: A_HIGH, 554972178: A_HIGH, 3407027058: A_START, 804291365: A_END_1, 3964660193: A_END_2}
PHASE_MAP_PATTERN = {1030475738: A_THEME, 4159759944: A_THEME, 3984178565: A_THEME, 3283539198: A_THEME, 526690563: A_THEME, 2553971516: A_START, 2225075240: A_START, 804291365: A_START, 3964660193: A_START, 3407027058: A_MID, 554972178: A_HIGH}
SLOT_MAP_STATE = 2317209868
PATTERN = True
PATTERN_GAIN = (-12.0, 6.0)
NEXT_BAR_PHASES = (4159759944, 2553971516)

def phase_map():
    return PHASE_MAP_PATTERN if PATTERN else PHASE_MAP_A19
MAPS = {'BNK_WRLD_MP_Forli': 678324730, 'BNK_WRLD_MP_Alhambra': 1590104542, 'BNK_WRLD_MP_Villa': 813397834, 'BNK_WRLD_MP_Pienza': 1369385525}

def _cache_path():
    return os.path.join(hc.CACHE, 'acb_music_v48_keyed.pkl')

def acb_bank():
    streams, data = ({}, None)
    for p in akpk.packages(ACB_SOUND):
        for e in p.streams:
            streams.setdefault(e.id, (p, e))
        for e in p.banks:
            if e.id == fnv1(MUSIC_BANK) and data is None:
                data = p.read(e)
    if data is None:
        raise SystemExit("ACB's music bank is not in its packages")
    return (data, streams)

def objects():
    data, _ = acb_bank()
    key = (len(data), zlib.crc32(data))
    path = _cache_path()
    if os.path.exists(path):
        k, out = pickle.load(open(path, 'rb'))
        if k == key:
            return out
    hc.set_version(ACB_VERSION)
    _v, items = hc.parse_bank(data)
    out = {oid: (t, tr) for t, oid, tr, bad in items if not bad}
    pickle.dump((key, out), open(path, 'wb'))
    return out

def convert48(hirc_type, tree):
    t = copy.deepcopy(tree)
    if hirc_type == 1:
        for n in t.walk():
            if n.name == 'AkStdParameters':
                n.items = [it for it in n.items if not (it[1].startswith('e') and it[1].endswith('ValueMeaning'))]
        return t
    for n in list(t.walk()):
        if n.name == 'InitialRTPC' and n.l('pRTPCMgr'):
            kept = [r for r in n.l('pRTPCMgr') if not r.f('FXID', 0)]
            if len(kept) != len(n.l('pRTPCMgr')):
                n.l('pRTPCMgr')[:] = kept
                n.set('ulNumRTPC', len(kept))
        if n.name == 'RTPC':
            for name in ('FXID', '_bIsRendered'):
                if n.has(name):
                    n.pop(name)
        if n.name == 'NodeInitialFxParams' and n.has('pFXChunk'):
            chunks_ = [c for c in n.l('pFXChunk') if not c.has('ulPresetSize')]
            if len(chunks_) != len(n.l('pFXChunk')):
                n.l('pFXChunk')[:] = chunks_
                n.set('uNumFx', len(chunks_))
                if not chunks_ and n.has('bitsFXBypass'):
                    n.pop('bitsFXBypass')
        sc = n.o('StateChunk')
        if sc is None or not sc.has('ulNumStates'):
            continue
        init = n.o('NodeInitialParams')
        gid = init.pop('ulStateGroupID')
        sync, states = (sc.f('eStateSyncType'), sc.l('pStates'))
        sts = []
        for it in states:
            if it.f('bIsCustom'):
                sts.append(hc.T('AkState', [['f', 'ulStateID', it.f('ulStateID')], ['f', 'ulStateInstanceID', it.f('ulStateInstanceID')]]))
        if sts:
            group = hc.T('AkStateGroupChunk', [['f', 'ulStateGroupID', gid], ['f', 'eStateSyncType', sync], ['f', 'ulNumStates', len(sts)], ['l', 'pStates', sts]])
            sc.items[:] = [['f', 'ulNumStateGroups', 1], ['l', 'pStateChunks', [group]]]
        else:
            sc.items[:] = [['f', 'ulNumStateGroups', 0], ['l', 'pStateChunks', []]]
    return t

def slots(acr, state=SLOT_MAP_STATE, every=False):
    import music_override as mo
    out = []
    for sid, (t, tr) in acr.items():
        if t != 12 or mo.switch_body(tr).f('ulGroupID') != MAP_GROUP:
            continue
        if not every and (not any((a.f('switchID') == state and a.f('nodeID') for a in mo.assocs(tr)))):
            continue
        p1 = mo.parent(tr)
        if p1 not in acr:
            continue
        g1 = mo.switch_body(acr[p1][1]).f('ulGroupID')
        if g1 == ACR_LAYER_GROUP:
            layer = next((a.f('switchID') for a in mo.assocs(acr[p1][1]) if a.f('nodeID') == sid))
            p2 = mo.parent(acr[p1][1])
            if every and p2 != mo.ACR_PHASE:
                continue
            phase = next((a.f('switchID') for a in mo.assocs(acr[p2][1]) if a.f('nodeID') == p1))
        elif g1 == ACR_PHASE_GROUP:
            if every and p1 != mo.ACR_PHASE:
                continue
            layer = None
            phase = next((a.f('switchID') for a in mo.assocs(acr[p1][1]) if a.f('nodeID') == sid))
        else:
            if every:
                continue
            raise SystemExit(f'map switch {sid:#x} hangs under group {g1:#x}')
        out.append((sid, phase, layer))
    return sorted(out)

def branches(acb, state):
    import music_override as mo
    out = {}
    for sid, (t, tr) in acb.items():
        if t != 12 or mo.switch_body(tr).f('ulGroupID') != MAP_GROUP:
            continue
        node = next((a.f('nodeID') for a in mo.assocs(tr) if a.f('switchID') == state and a.f('nodeID')), None)
        if node is None:
            continue
        p = mo.parent(tr)
        assert mo.switch_body(acb[p][1]).f('ulGroupID') == ACB_PHASE_GROUP, hex(p)
        phase = next((a.f('switchID') for a in mo.assocs(acb[p][1]) if a.f('nodeID') == sid))
        out[phase] = node
    return out

def subtree(objs, root):
    out, stack = ([], [root])
    import music_override as mo
    while stack:
        i = stack.pop(0)
        out.append(i)
        stack += mo.children(objs[i][1])
    return out

def inject(acr, ids, bank, report):
    import music_override as mo
    state = MAPS[bank]
    acb = objects()
    found = branches(acb, state)
    if set(found) != {A_THEME, A_MID, A_HIGH, A_START, A_END_1, A_END_2}:
        raise SystemExit(f"ACB's music for {state:#x}: phases {sorted((hex(p) for p in found))}")
    sl = slots(acr, every=PATTERN)
    if len(sl) != (18 if PATTERN else 16):
        raise SystemExit(f"{len(sl)} slots under ACR's map switches (expected {(18 if PATTERN else 16)})")
    if PATTERN:
        phase_map_check(acb, found)
    unmapped = sorted({hex(p) for _s, p, _l in sl} - {hex(p) for p in phase_map()})
    if unmapped:
        raise SystemExit(f'phases without an ACB phase: {unmapped}')
    conv = {}

    def get(i):
        if i not in conv:
            conv[i] = (convert48(acb[i][0], acb[i][1]), acb[i][0])
        return conv[i]
    clips, states_added, made = (set(), set(), 0)
    for sid, phase, layer in sl:
        root = found[phase_map()[phase]]
        tree = subtree(acb, root)
        new = {i: ids.new(f'acr_forge_acbmusic_{state:08x}_{sid:08x}_{i:08x}') for i in tree}
        for i in tree:
            tr, t = get(i)
            tr = copy.deepcopy(tr)
            mo.remap(tr, new)
            tr.set('ulID', new[i])
            if i == root:
                mo.set_parent(tr, sid)
            acr[new[i]] = (t, tr)
            made += 1
            for n in tr.walk():
                if n.name == 'AkMediaInformation':
                    clips.add(n.f('sourceID'))
                if n.name == 'AkState' and n.f('ulStateInstanceID') and (n.f('ulStateInstanceID') not in acr):
                    states_added.add(n.f('ulStateInstanceID'))
        if PATTERN:
            report.append(pattern_slot(acr, new[root], phase, layer, acb_bank()[1]))
        sw = acr[sid][1]
        pairs = [(a.f('switchID'), a.f('nodeID')) for a in mo.assocs(sw)]
        mo.set_assocs(sw, pairs + [(state, new[root])])
        mo.set_children(sw, mo.children(sw) + [new[root]])
    for i in sorted(states_added):
        if i not in acb or acb[i][0] != 1:
            raise SystemExit(f"state instance {i:#x} is not a State object of ACB's bank")
        tr, t = get(i)
        acr[i] = (t, copy.deepcopy(tr))
    if PATTERN:
        report.append(next_bar_rules(acr, made_segments(acr, state)))
    _data, streams = acb_bank()
    missing = [c for c in clips if c not in streams]
    if missing:
        raise SystemExit(f"clips not in ACB's packages: {[hex(c) for c in missing]}")
    report.append(f"{bank}: ACB's music for state {state:#x}: {len(found)} phase branches copied into {len(sl)} slots ({made} objects, {len(states_added)} State objects, {len(clips)} clips)")
    return {c: streams[c][0].read(streams[c][1]) for c in sorted(clips)}

def phase_map_check(acb, found):
    import music_pattern as mp
    classes = mp.phase_classes()
    for phase, branch in sorted(phase_map().items()):
        root = found[branch]
        objs = {i: (acb[i][0], convert48(acb[i][0], acb[i][1])) for i in subtree(acb, root)}
        if not mp.loops_forever(objs[root][1]):
            raise SystemExit(f"PHASE_MAP {phase:#x} -> {branch:#x}: a one-shot (AC3's slots loop)")
        if classes.get(phase) == 'base' and (not mp.has_base(objs, root)):
            raise SystemExit(f"PHASE_MAP {phase:#x} -> {branch:#x}: no base track for one of AC3's base phases")

def pattern_slot(acr, root, phase, layer, streams):
    import music_pattern as mp
    env, ref = (mp.rtpc_envelope(), mp.reference_level())
    ids = subtree(acr, root)
    objs = {i: acr[i] for i in ids}
    capped = 0
    for i in ids:
        if acr[i][0] != 11:
            continue
        for r in mp.volume_rtpcs(acr[i][1]):
            top = env.get(r.f('RTPCID', 0))
            if top is None:
                continue
            for p in r.l('pRTPCMgr'):
                if p.f('To') > top:
                    p.set('To', top)
                    capped += 1

    def clip(src):
        if src not in streams:
            return None
        pk, e = streams[src]
        return mp.clip_rms(f'acb:{src}', pk.read(e))
    line = f"  slot {phase:#010x}/{layer or 0:#x}: {capped} layer points capped at AC3's boost"
    if mp.phase_classes().get(phase) == 'base':
        lvl = mp.base_level(objs, root, clip, with_container=False)
        if lvl is None:
            raise SystemExit(f'slot {phase:#x}: no base level')
        lo, hi = PATTERN_GAIN
        v = round(max(lo, min(hi, ref - lvl)) * 2) / 2
        before = mp.volume(acr[root][1])
        mp.set_volume(acr[root][1], v)
        line += f'; container {before:+.1f} -> {v:+.1f} dB (base {lvl + v:.1f} dBFS, AC3 {ref:.1f})'
    return line

def made_segments(acr, state):
    import music_override as mo
    out = []
    for sid, (t, tr) in acr.items():
        if t != 12 or mo.switch_body(tr).f('ulGroupID') != MAP_GROUP:
            continue
        for a in mo.assocs(tr):
            if a.f('switchID') == state and a.f('nodeID'):
                out += [c for c in mo.children(acr[a.f('nodeID')][1]) if acr.get(c, (0,))[0] == 10]
    return sorted(set(out))

def next_bar_rules(acr, segs):
    import music_override as mo
    tr = acr[mo.ACR_PHASE][1]
    mt = next((n for n in tr.walk() if n.name == 'MusicTransNodeParams'))
    rules = mt.l('pRules')
    dsts = {a.f('nodeID') for a in mo.assocs(tr) if a.f('switchID') in NEXT_BAR_PHASES}
    template = next((r for r in rules if r.f('dstID') in dsts and r.f('srcID') not in (-1, 4294967295) and (r.o('AkMusicTransSrcRule').f('eSyncType') == 2)))
    have = {(r.f('srcID'), r.f('dstID')) for r in rules}
    added = 0
    for sg in segs:
        for d in sorted(dsts):
            if (sg, d) in have:
                continue
            r = copy.deepcopy(template)
            r.set('srcID', sg)
            r.set('dstID', d)
            rules.append(r)
            added += 1
    mt.set('numRules', len(rules))
    return f'  ACR phase switch: + {added} rules (the segments -> {len(dsts)} exit-cue phases at the next bar)'

def _check():
    import music_override as mo
    from akpk import ACR_SOUND
    a = objects()
    _b, data = mo.bank_of(ACR_SOUND, mo.BANK)
    r = {oid: (t, tr) for t, oid, tr in mo.parsed(53, data)}
    shared = sorted(set(a) & set(r))
    tot, same, rest = (collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter))
    for o in shared:
        t = a[o][0]
        d = hc.tree_diff(convert48(t, a[o][1]), r[o][1])
        tot[t] += 1
        same[t] += not d
        for x in d:
            rest[t][x.split(':')[0].split('.')[-1] + ':' + x.split(': ')[-1][:30] if False else x[:110]] += 1
    names = {1: 'State', 2: 'Sound', 3: 'Action', 4: 'Event', 5: 'RanSeq', 7: 'ActorMixer', 9: 'Layer', 10: 'MusicSegment', 11: 'MusicTrack', 13: 'MusicRanSeq', 14: 'Attenuation'}
    for t in sorted(tot):
        print(f'{names.get(t, t):13} shared {tot[t]:3}  identical after v48 -> v53: {same[t]:3}')
        for x, c in rest[t].most_common(4):
            print(f'      {c:3} {x}')
    import rules
    ok = True
    hc.set_version(hc.DST_VERSION)
    for bank, state in MAPS.items():
        for phase, root in branches(a, state).items():
            for i in subtree(a, root):
                t, tr = (a[i][0], convert48(a[i][0], a[i][1]))
                tr = rules.convert(t, tr)
                body, dropped = hc.write_item(t, tr)
                if dropped:
                    print(f'{bank} {i:#x} type {t} would drop {dropped}')
                    ok = False
    print("Forli's objects write as v65 with nothing dropped:", ok)
    return ok
if __name__ == '__main__':
    if sys.argv[1:2] == ['--check']:
        sys.exit(0 if _check() else 1)
    print(__doc__)
