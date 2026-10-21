"""forgelib/audio/character_voice.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import copy
import json
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
try:
    import hirc_convert as hc
    import media
    import rules
except ImportError:
    hc = media = rules = None
from akpk import ACR_SOUND, AC3_SOUND
from banks import load
from overlap import fnv1
VOICE_BANKS = ('BNK_VO_AI_ONO_All', 'BNK_VO_MP', 'BNK_VO_MP_NonLocalised')
LANGUAGE = 'english(us)'
WWISE_ID = 2659669886
NODE_TYPES = (2, 5, 6, 7, 9)
VORBIS = 6619138
PLAN_DIR = os.path.join(os.path.dirname(HERE), 'out', 'audio', 'voice')

def event_id(acr_event):
    return fnv1(f'acr_forge_voice_{acr_event:08x}')

def mixer_id(ac3_mixer):
    return fnv1(f'acr_forge_voice_mixer_{ac3_mixer:08x}')

def media_id(clip):
    return fnv1(f'acr_forge_voice_media_{clip:08x}')

def entries(body):
    return [(p, struct.unpack_from('<I', body, p)[0]) for p in range(4, len(body) - 3) if struct.unpack_from('<I', body, p - 4)[0] == WWISE_ID]

def acr_set_events(acr_file, set_name):
    import make_jester
    from datafile import decompress
    from forge import Forge
    from paths import ACR
    with Forge(os.path.join(ACR, 'DataPC.forge')) as f:
        rec = next((r for r in f.records if r.name == acr_file))
        body = next((o['body'] for o in make_jester.objects_of(decompress(f.read(rec))) if o['name'] == set_name))
    return [e for _p, e in entries(body)]

def with_events(body, events):
    ents = entries(body)
    assert len(ents) == len(events), (len(ents), len(events))
    b = bytearray(body)
    for (p, _old), e in zip(ents, events):
        struct.pack_into('<I', b, p, e)
    return bytes(b)

def voice_banks(game):
    pkgs, banks = load(ACR_SOUND if game == 'ACR' else AC3_SOUND)
    pk = {p.name: p for p in pkgs}
    objs, didx, ids = ({}, {}, set())
    for name in VOICE_BANKS:
        bid = fnv1(name)
        cands = [b for b in banks if b.id == bid]
        eng = [b for b in cands if str(pk[b.pkg].languages.get(b.entry.lang, '')) == LANGUAGE]
        b = (eng or cands)[0]
        data = pk[b.pkg].read(b.entry)
        hc.set_version(hc.SRC_VERSION if game == 'ACR' else hc.DST_VERSION)
        _v, items = hc.parse_bank(data)
        bad = [oid for _t, oid, _tr, sk in items if sk]
        if bad:
            raise SystemExit(f'{game} {name}: {len(bad)} objects did not parse cleanly')
        for t, oid, tr, _sk in items:
            objs.setdefault(oid, (t, tr))
        if 'DIDX' in b.chunks:
            o, s = b.chunks['DIDX']
            start = b.chunks['DATA'][0]
            for k in range(s // 12):
                mid, off, size = struct.unpack_from('<III', data, o + 12 * k)
                didx[mid] = data[start + off:start + off + size]
        ids.add(bid)
    return (objs, didx, ids)

def children(t):
    for n in t.walk():
        if n.name == 'Children':
            return [it[2] for it in n.items if it[1] == 'ulChildID']
    return []

def parent(t):
    for n in t.walk():
        if n.name == 'NodeBaseParams':
            return n.f('DirectParentID')
    return 0

def actions(t):
    return [n.f('ulActionID') for n in t.walk() if n.name == 'Action']

def target(t):
    return next((n for n in t.walk() if n.name == 'ActionInitialValues')).f('ulTargetID')

def reachable_clips(objs, event):
    if event not in objs or objs[event][0] != 4:
        return set()
    stack = [target(objs[a][1]) for a in actions(objs[event][1]) if a in objs]
    seen, clips = (set(), set())
    while stack:
        i = stack.pop()
        if i in seen or i not in objs:
            continue
        seen.add(i)
        tr = objs[i][1]
        clips |= {n.f('sourceID') for n in tr.walk() if n.name == 'AkMediaInformation'}
        stack += children(tr)
    return clips

def plan(acr_file, set_name):
    path = os.path.join(PLAN_DIR, f'{acr_file}_{set_name}.json')
    if os.path.exists(path):
        d = json.load(open(path))
        return {'slots': d['slots'], 'events': {int(k): v for k, v in d['events'].items()}, 'port': d['port']}
    slots = acr_set_events(acr_file, set_name)
    acr, _d, _i = voice_banks('ACR')
    ac3, _d3, _i3 = voice_banks('AC3')
    events, port = ({}, [])
    for e in sorted({e for e in slots if e}):
        if reachable_clips(ac3, e):
            events[e] = e
        elif e in acr:
            if not reachable_clips(acr, e):
                raise SystemExit(f'ACR event {e:#x} plays nothing in ACR either')
            events[e] = event_id(e)
            port.append(e)
        else:
            raise SystemExit(f'event {e:#x} of {set_name} is in no ACR voice bank')
    out = {'slots': slots, 'events': events, 'port': port}
    os.makedirs(PLAN_DIR, exist_ok=True)
    json.dump({'slots': slots, 'events': {str(k): v for k, v in events.items()}, 'port': port}, open(path, 'w'), indent=1)
    return out

def slot_events(acr_file, set_name):
    p = plan(acr_file, set_name)
    return [p['events'][e] if e else 0 for e in p['slots']]

def _class_ids():
    import exe_registry
    classes, _ = exe_registry.load(exe_registry.AC3)
    return {n: i for i, n in classes.items()}

def acr_sound_set(acr_file):
    import make_jester
    from datafile import decompress
    from forge import Forge
    from paths import ACR
    ids = _class_ids()
    with Forge(os.path.join(ACR, 'DataPC.forge')) as f:
        rec = next((r for r in f.records if r.name == acr_file))
        objs = make_jester.objects_of(decompress(f.read(rec)))
    eb = objs[0]
    assert eb['type'] == ids['EntityBuilder'], f"{acr_file}'s root is not an EntityBuilder"
    sets = [o['name'] for o in objs if o['type'] == ids['MultiHumanSoundSet'] and struct.pack('<I', o['uid']) in eb['body']]
    if len(sets) != 1:
        raise SystemExit(f"{acr_file}'s EntityBuilder names {len(sets)} sound sets")
    return sets[0]
KIT_BANK = 222

def bank_names():
    out = {}
    for root in (ACR_SOUND, AC3_SOUND):
        _p, banks = load(root)
        for b in banks:
            out.update(b.names)
    for i in range(100):
        for n in (f'BNK_CHR_MP_ID{i:02d}', f'BNK_CHR_MP_ID{i:02d}_DLC'):
            out.setdefault(fnv1(n), n)
    return out

def kit_bank(persona, archive):
    from bootstrap_build import Bootstrap
    kit = next((o for o in Bootstrap(archive).objects if o['name'] == f'CharacterBuildingKit_AC3MP_ID{persona}'))
    bid = struct.unpack_from('<I', kit['body'], KIT_BANK)[0]
    name = bank_names().get(bid)
    if not name:
        raise SystemExit(f"kit {persona}'s +{KIT_BANK} ({bid:#x}) is no bank either game has")
    return name

def voice_of(c):
    v = getattr(c, 'voice', None)
    if v is None or not c.own_entity:
        return None
    if v == 'auto':
        return (c.acr_file, acr_sound_set(c.acr_file))
    return (v.acr_file or c.acr_file, v.acr_set or acr_sound_set(v.acr_file or c.acr_file))

def package_voices(characters, archive):
    from bootstrap_build import Bootstrap
    kits = {o['name'] for o in Bootstrap(archive).objects if o['name'].startswith('CharacterBuildingKit_')}
    out = {}
    for c in characters:
        v = voice_of(c)
        if not v:
            continue
        persona = c.new if f'CharacterBuildingKit_AC3MP_ID{c.new}' in kits else c.donor
        bank = getattr(c, 'bank', None) or kit_bank(persona, archive)
        out.setdefault(bank, set()).update(plan(*v)['port'])
    return {b: sorted(p) for b, p in sorted(out.items())}

def package_bank_events(package, bank_name):
    from akpk import Package
    pk = Package(package)
    e = next((x for x in pk.banks if x.id == fnv1(bank_name)), None)
    if e is None:
        return set()
    from banks import chunks
    data = pk.read(e)
    o, _s = next(((o, s) for t, o, s in chunks(data) if t == 'HIRC'))
    n, p, out = (struct.unpack_from('<I', data, o)[0], o + 4, set())
    for _ in range(n):
        t, size, oid = struct.unpack_from('<BII', data, p)
        if t == 4:
            out.add(oid)
        p += 5 + size
    return out
ALWAYS_LOADED = ('Init', 'BNK_3D_GLOBAL', 'BNK_Bootstrap_Wwise_MP', 'BNK_Bootstrap_Wwise_MP_Startup', 'BNK_VO_AI_ONO_All', 'BNK_VO_MP', 'BNK_VO_MP_NonLocalised')

def event_banks():
    import pickle
    path = os.path.join(PLAN_DIR, 'event_banks.pkl')
    if os.path.exists(path):
        return pickle.load(open(path, 'rb'))
    names = bank_names()
    out = []
    for root in (AC3_SOUND, ACR_SOUND):
        _p, banks = load(root)
        idx = {}
        for b in banks:
            for t, oid, _r in b.hirc:
                if t == 4:
                    idx.setdefault(oid, set()).add(names.get(b.id, f'{b.id:#x}'))
        out.append(idx)
    os.makedirs(PLAN_DIR, exist_ok=True)
    pickle.dump(tuple(out), open(path, 'wb'))
    return tuple(out)

def clip_sound_problems(clips, his_bank, package_events, package_banks=None, sounds=None, loaded=()):
    ac3, acr = event_banks()
    always = set(ALWAYS_LOADED) | set(loaded)
    if package_banks:
        ac3 = dict(ac3)
        for e, b in package_banks.items():
            ac3[e] = set(ac3.get(e, ())) | set(b)
    out = []
    for name, body in sorted(clips.items()):
        sites, i = (set(), body.find(SOUND_TRIGGER))
        while i >= 0:
            sites.add(i + len(SOUND_TRIGGER))
            i = body.find(SOUND_TRIGGER, i + 1)
        seeds = audio_seed_sites(body)
        for p in sorted(sites | seeds):
            e = struct.unpack_from('<I', body, p)[0]
            if e in package_events or not e:
                continue
            banks = ac3.get(e)
            if banks:
                if not (banks & always or his_bank in banks or any((not b.upper().startswith('BNK_CHR_') for b in banks))):
                    out.append(f'{name} posts {e:#x}, only in {sorted(banks)} (his kit loads {his_bank})')
                    continue
                for t in sounds[1].get(e, ()) if sounds else ():
                    tb = sounds[0].get(t)
                    if tb and (not (tb & always or his_bank in tb or any((not b.upper().startswith('BNK_CHR_') for b in tb)))):
                        out.append(f'{name} posts {e:#x}, whose sound {t:#x} only {sorted(tb)} has (his kit loads {his_bank})')
            elif e in acr and any(('_MP' in b.upper() or b in ALWAYS_LOADED for b in acr[e])):
                out.append(f'{name} posts {e:#x}, an ACR MP event ({sorted(acr[e])}) AC3 lacks')
            elif p in seeds:
                out.append(f'{name} posts {e:#x} (an AudioEventSeed), which no AC3 bank nor his package bank defines')
    return sorted(set(out))
SOUND_TRIGGER = bytes.fromhex('e96c841d0000000000d04057fe00')
AUDIO_SEED = struct.pack('<I', 1444808444)
AUDIO_SEED_EVENT = struct.pack('<I', 2659669886)

def package_event_banks(packages):
    from akpk import Package
    from banks import chunks
    names = bank_names()
    out = {}
    for path in packages:
        pk = Package(path)
        for e in pk.banks:
            data = pk.read(e)
            hirc = [(o, s) for t, o, s in chunks(data) if t == 'HIRC']
            if not hirc:
                continue
            o = hirc[0][0]
            n, p = (struct.unpack_from('<I', data, o)[0], o + 4)
            for _ in range(n):
                t, size, oid = struct.unpack_from('<BII', data, p)
                if t == 4:
                    out.setdefault(oid, set()).add(names.get(e.id, f'{e.id:#010x}'))
                p += 5 + size
    return out
PLAY_ACTIONS = (1025, 1026, 1027, 1028)

def _hirc_index(banks, names, objects, plays):
    from banks import chunks as _chunks
    for bank_id, data in banks:
        hirc = [(o, sz) for t, o, sz in _chunks(data) if t == 'HIRC']
        if not hirc:
            continue
        o = hirc[0][0]
        n, p = (struct.unpack_from('<I', data, o)[0], o + 4)
        here = {}
        for _ in range(n):
            t, size, oid = struct.unpack_from('<BII', data, p)
            here[oid] = (t, data[p + 5:p + 5 + size])
            p += 5 + size
        bn = names.get(bank_id, f'{bank_id:#010x}')
        for oid, (t, raw) in here.items():
            objects.setdefault(oid, set()).add(bn)
            if t == 4:
                k = struct.unpack_from('<I', raw, 4)[0]
                for a in struct.unpack_from(f'<{k}I', raw, 8):
                    if a in here and here[a][0] == 3 and (struct.unpack_from('<H', here[a][1], 4)[0] in PLAY_ACTIONS):
                        plays.setdefault(oid, set()).add(struct.unpack_from('<I', here[a][1], 6)[0])

def sound_index(packages=()):
    import pickle
    from akpk import Package
    names = bank_names()
    path = os.path.join(PLAN_DIR, 'sound_index_ac3.pkl')
    if os.path.exists(path):
        objects, plays = pickle.load(open(path, 'rb'))
    else:
        from akpk import packages as _packages
        objects, plays = ({}, {})
        for pk in _packages(AC3_SOUND):
            _hirc_index([(e.id, pk.read(e)) for e in pk.banks], names, objects, plays)
        os.makedirs(PLAN_DIR, exist_ok=True)
        pickle.dump((objects, plays), open(path, 'wb'))
    objects = {k: set(v) for k, v in objects.items()}
    plays = {k: set(v) for k, v in plays.items()}
    for pk_path in packages:
        pk = Package(pk_path)
        _hirc_index([(e.id, pk.read(e)) for e in pk.banks], names, objects, plays)
    return (objects, plays)

def boot_bank_names(archive):
    import boot_banks
    from bootstrap_build import Bootstrap
    names = bank_names()
    body = Bootstrap(archive).one(name='AssassinSoundSettings')['body']
    return {names.get(b, f'{b:#010x}') for _o, b in boot_banks.rows(body) if b}

def audio_seed_sites(body):
    out, i = (set(), body.find(AUDIO_SEED))
    while i >= 0:
        if i >= 5 and body[i - 5] == 0 and (body[i + 39:i + 43] == AUDIO_SEED_EVENT) and (i + 47 <= len(body)):
            out.add(i + 43)
        i = body.find(AUDIO_SEED, i + 1)
    return out

class AcrSource:
    prefix = 'acr'

    def __init__(self):
        from bank_build import acr_streams as _acr_streams
        self.objs, self.didx, _ids = voice_banks('ACR')
        self._streams = _acr_streams()

    def has_stream(self, sid):
        return sid in self._streams

    def stream(self, sid):
        p, e = self._streams[sid]
        return p.read(e)

    @staticmethod
    def convert(wem):
        return media.convert(wem)

def objects(ports, bank_id, ac3_bank_objs, taken, ac3_media, report, src=None):
    import pickle
    src = src or AcrSource()
    acr, acr_didx = (src.objs, src.didx)
    ac3_voice, _d3, _i3 = voice_banks('AC3')
    ac3 = dict(ac3_voice)
    ac3.update(ac3_bank_objs)
    ac3_everywhere = {oid for _t, oid in pickle.load(open(os.path.join(hc.CACHE, 'hirc_trees_ac3.pkl'), 'rb'))}
    keep, order, ancestors, ac3_chain = (set(), [], [], [])
    act_ids, dead = ([], set())

    def is_dead(i):
        if acr[i][0] != 2:
            return False
        for n in acr[i][1].walk():
            if n.name == 'AkBankSourceData' and n.f('ulPluginID') == VORBIS:
                sid = n.o('AkMediaInformation').f('sourceID')
                if sid not in acr_didx and (not src.has_stream(sid)):
                    return True
        return False

    def take(i):
        if i in keep:
            return
        if i not in acr or acr[i][0] not in NODE_TYPES:
            raise SystemExit(f"{i:#x} is not a node in ACR's voice banks")
        keep.add(i)
        for c in children(acr[i][1]):
            if is_dead(c):
                dead.add(c)
            else:
                take(c)
        order.append(i)
    for e in ports:
        for a in actions(acr[e][1]):
            act_ids.append(a)
            tgt = target(acr[a][1])
            take(tgt)
            up, in_ac3 = (parent(acr[tgt][1]), False)
            while up:
                if not in_ac3 and up in ac3_everywhere:
                    in_ac3 = True
                if in_ac3:
                    if up not in ac3:
                        raise SystemExit(f'ancestor {up:#x} is in an AC3 bank other than the voice ones')
                    if up not in ac3_chain:
                        ac3_chain.append(up)
                    up = parent(ac3[up][1])
                else:
                    if up not in keep and up not in ancestors:
                        ancestors.append(up)
                    up = parent(acr[up][1])
    rename = {i: fnv1(f'{src.prefix}_forge_voice_{i:08x}') for i in keep | set(ancestors) | set(act_ids) | set(ports)}
    rename.update({m: mixer_id(m) for m in ac3_chain})
    clash = sorted((v for v in rename.values() if v in taken))
    if clash or len(set(rename.values())) != len(rename):
        raise SystemExit(f'new voice ids taken: {[hex(v) for v in clash]}')
    hc.set_version(hc.DST_VERSION)
    trees = {}
    for i in order + ancestors:
        t, tr = acr[i]
        tr = rules.convert(t, copy.deepcopy(tr))
        for n in tr.walk():
            if n.name == 'Children' and any((it[1] == 'ulChildID' and it[2] in dead for it in n.items)):
                n.items = [it for it in n.items if not (it[1] == 'ulChildID' and it[2] in dead)]
                n.set('ulNumChilds', sum((1 for it in n.items if it[1] == 'ulChildID')))
            if n.name == 'CAkPlayList':
                lst = n.l('pItems')
                if lst and any((x.f('ulPlayID') in dead for x in lst)):
                    lst[:] = [x for x in lst if x.f('ulPlayID') not in dead]
                    n.set('ulPlayListItem', len(lst))
        trees[i] = (t, tr)
    for m in ac3_chain:
        trees[m] = (ac3[m][0], copy.deepcopy(ac3[m][1]))
    everything = set(trees)
    for a in ancestors + ac3_chain:
        t, tr = trees[a]
        kids = sorted((i for i in everything if i != a and parent(trees[i][1]) == a))
        ch = next((n for n in tr.walk() if n.name == 'Children'))
        ch.items = [it for it in ch.items if it[1] != 'ulChildID']
        ch.set('ulNumChilds', len(kids))
        ch.items += [['f', 'ulChildID', k] for k in kids]

    def support(ids_, kind_ok, what):
        out = []
        for x in ids_:
            if x in ac3 and ac3[x][0] in kind_ok:
                out.append((ac3[x][0], copy.deepcopy(ac3[x][1])))
            elif x in ac3_everywhere:
                raise SystemExit(f'{what} {x:#x} is in an AC3 bank other than the voice ones')
            elif x in acr and acr[x][0] in kind_ok:
                out.append((acr[x][0], rules.convert(acr[x][0], copy.deepcopy(acr[x][1]))))
            else:
                raise SystemExit(f"{what} {x:#x} is in neither game's voice banks")
        return out
    all_trees = [tr for _t, tr in trees.values()]
    state_ids = sorted({n.f('ulStateInstanceID') for tr in all_trees for n in tr.walk() if n.name == 'AkState'})
    fx_ids = sorted({n.f('fxID') for tr in all_trees for n in tr.walk() if n.name == 'FXChunk'})
    atts = sorted({n.f('uAttenuationID') for tr in all_trees for n in tr.walk() if n.name == 'PositioningParams' and n.f('uAttenuationID', 0)})
    extra = support(fx_ids, (18, 19), 'effect') + support(atts, (14,), 'attenuation') + support(state_ids, (1,), 'State')
    streams, embedded, renamed_media = ({}, {}, {})
    for i, (t, tr) in trees.items():
        for n in tr.walk():
            if n.name != 'AkBankSourceData':
                continue
            mi = n.o('AkMediaInformation')
            sid = mi.f('sourceID')
            if mi.f('uFileID') == 0 and n.f('StreamType') == 0 and (sid not in acr_didx):
                continue
            nid = media_id(sid) if sid in ac3_media else sid
            renamed_media[sid] = nid
            if n.f('StreamType') == 0:
                if sid not in acr_didx:
                    raise SystemExit(f"{i:#x}: embedded clip {sid:#x} not in ACR's voice banks")
                embedded[nid] = src.convert(acr_didx[sid])
            else:
                if not src.has_stream(sid):
                    raise SystemExit(f'{i:#x}: streamed clip {sid:#x} in no {src.prefix.upper()} package')
                streams[nid] = src.convert(src.stream(sid))
                if mi.f('uFileID') == sid:
                    mi.set('uFileID', nid)
            mi.set('sourceID', nid)
            mi.set('uSourceBits', mi.f('uSourceBits') & ~1)
    for _i, (t, tr) in trees.items():
        for n in tr.walk():
            for it in n.items:
                if it[0] == 'f' and isinstance(it[2], int) and (it[2] in rename):
                    it[2] = rename[it[2]]
    brought = set(rename.values()) | set(state_ids) | set(fx_ids) | set(atts)
    for _i, (t, tr) in trees.items():
        for n in tr.walk():
            for k, name, v in n.items:
                if k == 'f' and isinstance(v, int) and (v in acr) and (v not in brought) and (v not in ac3) and (name not in ('ulID',)):
                    raise SystemExit(f"{tr.f('ulID'):#x} {n.name}.{name} -> {v:#x}: ACR-only, not brought along")
    acts = []
    for a in act_ids:
        tr = rules.convert(3, copy.deepcopy(acr[a][1]))
        for n in tr.walk():
            for it in n.items:
                if it[0] == 'f' and isinstance(it[2], int) and (it[2] in rename):
                    it[2] = rename[it[2]]
                if it[0] == 'f' and it[1] == 'fileID':
                    it[2] = bank_id
        acts.append((3, tr))
    evs = []
    for e in ports:
        a_ids = [rename[a] for a in actions(acr[e][1])]
        evs.append((4, struct.pack(f'<II{len(a_ids)}I', rename[e], len(a_ids), *a_ids)))
    by_new = {tr.f('ulID'): i for i, (_t, tr) in trees.items()}
    ordered, seen = ([], set())

    def visit(i):
        if i in seen:
            return
        seen.add(i)
        for c in children(trees[i][1]):
            if c in by_new:
                visit(by_new[c])
        ordered.append(i)
    for i in order + ancestors + ac3_chain:
        visit(i)
    nodes = [trees[i] for i in ordered]
    report.append(f'voice: {len(ports)} events ported ({len(act_ids)} actions, {len(order)} nodes, {len(ancestors)} ACR ancestors, {len(ac3_chain)} AC3 mixers copied; {len(dead)} dead Sounds dropped: {[hex(d) for d in sorted(dead)]}), {len(streams)} streamed clips, {len(embedded)} embedded, {sum((1 for s, n in renamed_media.items() if s != n))} clip ids renewed; {len(extra)} attenuations / States / effects')
    return (extra + nodes + acts + evs, streams, embedded, {e: rename[e] for e in ports})

def check(bank_data, new_events, package_streams, decode, report):
    hc.set_version(hc.DST_VERSION)
    _v, items = hc.parse_bank(bank_data)
    objs = {oid: (t, tr) for t, oid, tr, _s in items}
    media_in = set()
    o = bank_data.find(b'DIDX')
    if o >= 0:
        size = struct.unpack_from('<I', bank_data, o + 4)[0]
        media_in = {struct.unpack_from('<I', bank_data, o + 8 + 12 * k)[0] for k in range(size // 12)}
    ok, n = (True, 0)
    for acr_e, e in sorted(new_events.items()):
        if e not in objs:
            report.append(f'  voice: event {e:#x} (ACR {acr_e:#x}) missing')
            ok = False
            continue
        clips = reachable_clips(objs, e)
        if not clips:
            report.append(f'  voice: event {e:#x} reaches no clip')
            ok = False
        for c in clips:
            if c in media_in:
                n += 1
            elif c in package_streams:
                try:
                    decode(c)
                    n += 1
                except Exception:
                    report.append(f'  voice: clip {c:#x} does not decode')
                    ok = False
            else:
                report.append(f'  voice: clip {c:#x} of event {e:#x} is nowhere')
                ok = False
        up = [target(objs[a][1]) for a in actions(objs[e][1]) if a in objs]
        while up:
            i = up.pop()
            if i and i not in objs:
                report.append(f'  voice: {i:#x} (under {e:#x}) not in the bank')
                ok = False
            elif i:
                p = parent(objs[i][1])
                if p and p not in objs:
                    report.append(f'  voice: parent {p:#x} of {i:#x} not in the bank')
                    ok = False
                up += [p] if p else []
    pos = {oid: k for k, (_t, oid, _tr, _s) in enumerate(items)}
    late = [(oid, c) for _t, oid, tr, _s in items for c in children(tr) if pos.get(c, -1) > pos[oid]]
    if late:
        report.append(f'  voice: children after their parent (Wwise drops the bank): {[(hex(a), hex(b)) for a, b in late][:8]}')
        ok = False
    report.append(f'  voice: {len(new_events)} events in the bank, {n} clips reached and decoding, no child after its parent: {ok}')
    return ok
if __name__ == '__main__':
    p = plan(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else acr_sound_set(sys.argv[1]))
    for k, e in enumerate(p['slots']):
        if e:
            print(f"slot {k:2}: ACR {e:08X} -> {p['events'][e]:08X} {('ported' if e in p['port'] else 'AC3 has it')}")
    print(f"{len(p['port'])} events to port")
