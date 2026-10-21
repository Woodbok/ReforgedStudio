"""forgelib/audio/rules.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
from hirc_convert import T
VOLUME, LFE, PITCH, LPF, PRIORITY, DIST_OFFSET, LOOP = (0, 1, 2, 3, 4, 5, 6)
FEEDBACK_VOLUME, FEEDBACK_LPF, PAN_LR, PAN_FR, CENTER_PCT = (7, 8, 10, 11, 12)
DELAY_TIME, TRANSITION_TIME = (13, 14)
PROPS = 'AkPropBundle<AkPropValue,unsigned char>'
RANGED = 'AkPropBundle<RANGED_MODIFIERS<AkPropValue>>'

def bundles(props, ranged):
    p = T(PROPS, [['f', 'cProps', len(props)], ['l', 'pProps', [T('AkPropBundle', [['f', 'pID', k], ['f', 'pValue', props[k]]]) for k in sorted(props)]]])
    r = T(RANGED, [['f', 'cProps', len(ranged)], ['l', 'pProps', [T('AkPropBundle', [['f', 'pID', k], ['f', 'min', ranged[k][0]], ['f', 'max', ranged[k][1]]]) for k in sorted(ranged)]]])
    return (p, r)

def _put(props, ranged, pid, value, default, lo=0, hi=0, cast=float):
    if value is not None and value != default:
        props[pid] = cast(value)
    if lo or hi:
        ranged[pid] = (cast(lo), cast(hi))

def node_params(owner):
    base = owner.o('NodeBaseParams')
    init, pos = (base.o('NodeInitialParams'), base.o('PositioningParams'))
    props, ranged = ({}, {})
    for name, pid in (('Volume', VOLUME), ('LFE', LFE), ('Pitch', PITCH), ('LPF', LPF)):
        _put(props, ranged, pid, init.pop(name), 0.0, init.pop(name + '.min'), init.pop(name + '.max'))
    _put(props, ranged, PRIORITY, base.pop('ucPriority'), 50)
    _put(props, ranged, DIST_OFFSET, base.pop('iDistOffset'), -10)
    if owner.has('Loop'):
        _put(props, ranged, LOOP, owner.pop('Loop'), 1, owner.pop('LoopMod.min'), owner.pop('LoopMod.max'), cast=int)
    fb = base.o('FeedbackInfo')
    if fb is not None and fb.has('fFeedbackVolume'):
        _put(props, ranged, FEEDBACK_VOLUME, fb.pop('fFeedbackVolume'), 0.0, fb.pop('fFeedbackModifierMin'), fb.pop('fFeedbackModifierMax'))
        _put(props, ranged, FEEDBACK_LPF, fb.pop('fFeedbackLPF'), 0.0, fb.pop('fFeedbackLPFModMin'), fb.pop('fFeedbackLPFModMax'))
    if pos.has('uCenterPct'):
        center, rl, fr = (pos.pop('uCenterPct'), pos.pop('fPAN_RL'), pos.pop('fPAN_FR'))
        _put(props, ranged, CENTER_PCT, center, 0)
        if rl != 0:
            props[PAN_LR], props[PAN_FR] = (float(rl), float(fr))
        elif fr not in (0, 100):
            props[PAN_FR] = float(fr)
    p, r = bundles(props, ranged)
    init.items[:0] = [['o', PROPS, p], ['o', RANGED, r]]

def adv_settings(adv):
    adv.set('bUseVirtualBehavior', 0, after='bKillNewest')
    adv.set('bIsGlobalLimit', 0, after='u16MaxNumInstance')
FX_TYPES = (18, 19)

def convert(hirc_type, t):
    if hirc_type == 3:
        action(t)
        return t
    for node in list(t.walk()):
        if node.name == 'RTPC' and hirc_type in FX_TYPES:
            continue
        if node.o('NodeBaseParams') is not None and node.o('NodeBaseParams').o('NodeInitialParams') is not None:
            node_params(node)
        fn = NODE_RULES.get(node.name)
        if fn:
            fn(node)
    return t

def _action_type_map():
    from wwiser.parser import wdefs
    new = {}
    for k, v in wdefs.AkActionType_062.enum.items():
        new.setdefault(v, k)
    return {k: new[v] for k, v in wdefs.AkActionType_056.enum.items() if v in new}
ACTION_TYPES = None
SET_PROP_ACTIONS = (8, 9, 10, 11, 12, 13, 14, 15)
PARAM_NODES = ('PlayActionParams', 'ActiveActionParams', 'ValueActionParams')

def action(t):
    global ACTION_TYPES
    if ACTION_TYPES is None:
        ACTION_TYPES = _action_type_map()
    at = t.f('ulActionType')
    if at not in ACTION_TYPES:
        raise ValueError(f'action type {at:#x} has no v65 equivalent')
    t.set('ulActionType', ACTION_TYPES[at])
    init = t.o('ActionInitialValues')
    props, ranged = ({}, {})
    _put(props, ranged, DELAY_TIME, init.pop('tDelay'), 0, init.pop('tDelayMin'), init.pop('tDelayMax'), cast=int)
    init.pop('ulSubSectionSize')
    params = next((init.o(n) for n in PARAM_NODES if init.o(n) is not None), None)
    if params is not None and params.has('TTime'):
        _put(props, ranged, TRANSITION_TIME, params.pop('TTime'), 0, params.pop('TTimeMin'), params.pop('TTimeMax'), cast=int)
    p, r = bundles(props, ranged)
    init.set(PROPS, p, kind='o', after='ulTargetID')
    init.set(RANGED, r, kind='o', after=PROPS)
    if params is not None:
        spec = params.o('ActionSpecificParams')
        if spec is not None:
            raw = spec.f('_reserved')
            params.pop('ActionSpecificParams')
            if params.name == 'ValueActionParams' and ACTION_TYPES[at] >> 8 in SET_PROP_ACTIONS:
                import struct
                meaning, base, lo, hi = struct.unpack('<Ifff', raw)
                params.set('AkPropActionSpecificParams', T('AkPropActionSpecificParams', [['f', 'eValueMeaning', meaning], ['o', 'RandomizerModifier', T('RandomizerModifier', [['f', 'base', base], ['f', 'min', lo], ['f', 'max', hi]])]]), kind='o', after='byBitVector')
        if params.name == 'PlayActionParams' and params.o('ExceptParams') is not None:
            assert params.o('ExceptParams').f('ulExceptionListSize') == 0
            params.pop('ExceptParams')
    for node in t.walk():
        if node.name in ('PauseActionSpecificParams', 'ResumeActionSpecificParams') and node.has('b32IsMaster'):
            node.items = [['f', 'byBitVector', node.f('b32IsMaster') & 255]]

def _by_name(old, new):
    inv = {}
    for k, v in new.enum.items():
        inv.setdefault(v, k)
    return {k: inv[v] for k, v in old.enum.items() if v in inv}
_ENUMS = {}

def enum_map(kind):
    if kind not in _ENUMS:
        from wwiser.parser import wdefs
        _ENUMS[kind] = {'rtpc': lambda: _by_name(wdefs.AkRTPC_ParameterID_053, wdefs.AkRTPC_ParameterID_065), 'scaling': lambda: _by_name(wdefs.AkCurveScaling_062, wdefs.AkCurveScaling_065)}[kind]()
    return _ENUMS[kind]

def rtpc(r):
    if r.has('ParamID'):
        m = enum_map('rtpc')
        if r.f('ParamID') not in m:
            raise ValueError(f"RTPC parameter {r.f('ParamID')} has no v65 equivalent")
        r.set('ParamID', m[r.f('ParamID')])
    if r.has('eScaling'):
        r.set('eScaling', enum_map('scaling').get(r.f('eScaling'), r.f('eScaling')))

def bus_params(p):
    props, ranged = ({}, {})
    for name, pid in (('VolumeMain', VOLUME), ('LFEVolumeMain', LFE), ('PitchMain', PITCH), ('LPFMain', LPF)):
        _put(props, ranged, pid, p.pop(name), 0.0)
    p.items.insert(0, ['o', PROPS, bundles(props, ranged)[0]])
    p.set('bIsEnvBus', p.pop('bIsEnvironmental'))
    p.set('bUseVirtualBehavior', 0)

def state_values(v):
    std = v.pop('AkStdParameters')
    from wwiser.parser import wdefs
    ids = {name: k for k, name in wdefs.AkRTPC_ParameterID_065.enum.items()}
    vals = {}
    for field, name in (('Volume', 'Volume'), ('LFEVolume', 'LFE'), ('Pitch', 'Pitch'), ('LPF', 'LPF')):
        if std.f(field) != 0:
            vals[ids[name]] = float(std.f(field))
    v.set('AkPropBundle<float>', T('AkPropBundle<float>', [['f', 'cProps', len(vals)], ['l', 'pProps', [T('AkPropBundle', [['f', 'pID', k], ['f', 'pValue', vals[k]]]) for k in sorted(vals)]]]), kind='o')

def _setter(**fields):

    def fn(node):
        for k, v in fields.items():
            node.set(k, v)
    return fn

def _dropper(*names):

    def fn(node):
        for n in names:
            if node.has(n):
                node.pop(n)
    return fn

def music_track(v):
    v.set('numClipAutomationItem', 0)
    v.set('pItems', [], kind='l')

def _fnv1(s):
    h = 2166136261
    for ch in s.lower().encode():
        h = h * 16777619 & 4294967295 ^ ch
    return h
MARKER_NAMES = ('Entry Cue', 'Exit Cue')

def music_marker(m):
    i = m.f('id')
    if i >= len(MARKER_NAMES):
        m.set('uStringSize', 0)
        return
    m.set('id', _fnv1(MARKER_NAMES[i]))
    m.set('uStringSize', 0)

def attenuation(v):
    v.pop('curveToUse[2]')
    for k in (3, 4):
        it = v._find(f'curveToUse[{k}]')
        it[1] = f'curveToUse[{k - 1}]'

def music_playlist_item(item):
    item.set('Weight', item.f('Weight') * 1000)

def fx_base(v):
    if v.o('AkToneGenParams') is not None and (not v.o('AkToneGenParams').has('uChannelMask')):
        v.o('AkToneGenParams').set('uChannelMask', 4)
        v.set('uSize', v.f('uSize') + 4)

def playlist_item(item):
    item.set('weight', item.pop('ucWeight') * 1000, after='ulPlayID')
NODE_RULES = {'AdvSettingsParams': adv_settings, 'AkPlaylistItem': playlist_item, 'RTPC': rtpc, 'BusInitialParams': bus_params, 'StateInitialValues': state_values, 'MusicTrackInitialValues': music_track, 'CAkStinger': _setter(uCueFilterHash=0), 'AkMusicMarkerWwise': music_marker, 'AkMusicRanSeqPlaylistItem': music_playlist_item, 'AkMusicTransSrcRule': _setter(uMarkerID=0), 'AkMusicTransDstRule': _setter(uMarkerID=0, bDestMatchSourceCueName=0), 'LayerInitialValues': _dropper('fCrossfadingRTPCDefaultValue'), 'AttenuationInitialValues': attenuation, 'FxBaseInitialValues': fx_base}
