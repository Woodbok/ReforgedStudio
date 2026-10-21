"""forgelib/maps/convert.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
KIND1 = {'Mesh', 'MaterialTemplate', 'BuildTable', 'EntityBuilder', 'ActionBlock', 'ObjectPack'}
KIND_FLOOR = 65536
MIN_TARGET = int(os.environ.get('AC3FORGE_KIND_FLOOR', '0'), 0)

def kind_of(cls):
    return 2 if cls == 'TextureMap' else 1 if cls in KIND1 else None

def clear_kinds(body, targets):
    out = bytearray(body)
    n = 0
    i = body.find(b'\x01', 2)
    while 0 <= i <= len(body) - 6:
        k = body[i + 1]
        uid = struct.unpack_from('<I', body, i + 2)[0]
        cls = targets.get(uid) if k in (1, 2) and uid >= MIN_TARGET else None
        if cls and kind_of(cls) == k:
            out[i + 1] = 0
            n += 1
            i = body.find(b'\x01', i + 6)
        else:
            i = body.find(b'\x01', i + 1)
    return (bytes(out), n)
_IDS = {}

def class_ids():
    if not _IDS:
        import xrefs
        xrefs.cls('AC3', 0)
        _IDS.update({v: k for k, v in xrefs._REG['AC3'].items() if k >= 1 << 24})
    return _IDS
_PARENTS = {}

def parents(game='AC3'):
    if game not in _PARENTS:
        import exe_registry
        from exe_classes import PE
        pe = PE(exe_registry.ACR if game == 'ACR' else exe_registry.AC3)
        names = exe_registry.string_table(pe)
        rec = {}
        for off in exe_registry.record_offsets(pe, names):
            text = names[struct.unpack_from('<I', pe.data, off)[0]]
            parent, cid, size = struct.unpack_from('<III', pe.data, off + 4)
            if cid >= 1 << 24 and cid != 4294967295 and (size <= 65536):
                rec[cid] = (text, parent)
        _PARENTS[game] = {text: rec[parent][0] if parent in rec else None for cid, (text, parent) in rec.items()}
    return _PARENTS[game]

def descends(name, ancestor, game='AC3'):
    p = parents(game)
    seen = 0
    while name and seen < 32:
        if name == ancestor:
            return True
        name = p.get(name)
        seen += 1
    return False
ENTITY_TRAILER = 38
GREW_ONE_BYTE = ('Scene', 'SceneSpawningComponent')

def scene_byte(body, targets):
    ids = class_ids()
    if body[6:10] != struct.pack('<I', ids['Entity']):
        return (body, 0)
    bv = body.find(struct.pack('<I', ids['BoundingVolume']))
    if bv < ENTITY_TRAILER:
        return (body, 0)
    rev = {v: k for k, v in ids.items()}
    last = None
    for k in range(20, bv - 3):
        name = rev.get(struct.unpack_from('<I', body, k)[0])
        if name and (name.endswith('Component') or name in ('Scene', 'Visual', 'MeshInstanceData', 'TriggerZone')):
            last = name
    if last not in GREW_ONE_BYTE:
        return (body, 0)
    at = bv - ENTITY_TRAILER
    return (body[:at] + b'\x00' + body[at:], 1)
ANIMATION = struct.pack('<I', 262342271)
ANIMATION_DROPPED_BYTE = 19

def animation_byte(body, targets):
    if body[:2] != b'\x01\x02':
        return (body, 0)
    c = body.find(ANIMATION, 0, 256)
    if c < 0:
        return (body, 0)
    at = c + ANIMATION_DROPPED_BYTE
    return (body[:at] + body[at + 1:], 1)
AC3_SKY_COLOR = struct.pack('<I4f', 0, 0.5, 0.5, 1.0, 0.5)

def sky_color(body, targets):
    ids = class_ids()
    if body[6:10] != struct.pack('<I', ids['LayeredSky']):
        return (body, 0)
    color = struct.pack('<I', ids['Color'])
    at = [i for i in range(len(body) - 3) if body[i:i + 4] == color]
    if len(at) != 5:
        return (body, 0)
    k = at[-1] - 4
    return (body[:k] + struct.pack('<I', 0) + color + AC3_SKY_COLOR[4:] + body[k:], 1)

def spawn_byte(body, targets):
    ids = class_ids()
    if body[6:10] != struct.pack('<I', ids['Entity']):
        return (body, 0)
    import entity
    comps = entity.components(body)
    if not comps:
        return (body, 0)
    out, n = (bytearray(body), 0)
    for i, (s, e, name) in reversed(list(enumerate(comps))):
        if name != 'MultiSpawnPlayerComponent':
            continue
        end = e - 1 if i == len(comps) - 1 else e
        if end - (s + 10) != 6:
            continue
        kind = body[s + 11]
        out[end:end] = b'\x01' if kind == 0 else b'\x00'
        n += 1
    return (bytes(out), n)
READS_SAME = ('ChaseBreakerElement', 'ChaseBreakerEventSeed', 'ActivateEventSeed', 'GameplayCoordinator', 'GcLMPCivilianSocialize', 'GcLCivilianSocialize', 'SpawnNPCParams', 'SpawningSpecification', 'CrowdFractionCharacter', 'FreeRunCornerTurnComponent')
CHASE_BREAKER_RULED = ('ActivateAction', 'TriggerZone', 'ChaseBreakerComponent')
AC3_ACTIVATE_ACTION_NEW = b'\xff\xff\xff\xff'
AC3_TRIGGER_ZONE_NEW = bytes(16) + struct.pack('<2f', 2.0, 2.0)
AC3_CHASE_BREAKER_NEW = struct.pack('<f', 0.5)
NULL_LINK = b'\x00' + bytes(4)

def _framed(body, k):
    return k >= 6 and (body[k - 6:k - 4] == b'\x04\x00' or body[k - 5] == 0)

def chase_breaker(body, targets):
    ids = class_ids()
    aa, tz, cb = (struct.pack('<I', ids[n]) for n in ('ActivateAction', 'TriggerZone', 'ChaseBreakerComponent'))
    if cb not in body and aa not in body and (tz not in body):
        return (body, 0)
    inserts = []
    k = body.find(aa)
    while k >= 0:
        if _framed(body, k) and k + 20 <= len(body) and (body[k + 16:k + 20] != AC3_ACTIVATE_ACTION_NEW):
            inserts.append((k + 16, AC3_ACTIVATE_ACTION_NEW))
        k = body.find(aa, k + 4)
    k = body.find(tz)
    while k >= 0:
        at = k + 4 + 157
        if _framed(body, k) and at + 24 <= len(body) and (body[at + 16:at + 24] != AC3_TRIGGER_ZONE_NEW[16:]):
            inserts.append((at, AC3_TRIGGER_ZONE_NEW))
        k = body.find(tz, k + 4)
    el = struct.pack('<I', ids['ChaseBreakerElement'])
    k = body.find(el)
    while k >= 0:
        if k >= 6 and body[k - 5] == 0 and (body[k - 6:k - 4] != b'\x04\x00'):
            inserts.append((k - 5, b'\x04'))
        k = body.find(el, k + 4)
    k = body.find(cb)
    comp_ids = None
    while k >= 0:
        if body[k - 6:k - 4] == b'\x04\x00':
            if comp_ids is None:
                comp_ids = {struct.pack('<I', v) for n, v in ids.items() if descends(n, 'Component')}
            j, end = (k + 4, None)
            while j < len(body) - 10:
                j = body.find(b'\x04\x00', j)
                if j < 0:
                    break
                if body[j + 6:j + 10] in comp_ids and body[j + 6:j + 10] != cb:
                    end = j
                    break
                j += 1
            if end is not None:
                a, b = struct.unpack_from('<2f', body, end - 8)
                ac3_form = body[end - 5] == 0 and struct.unpack_from('<f', body, end - 13)[0] == 0.5
                if not ac3_form and 0 < a < 100 and (0 <= b < 100):
                    inserts.append((end - 4, AC3_CHASE_BREAKER_NEW))
                    inserts.append((end, NULL_LINK))
        k = body.find(cb, k + 4)
    if not inserts:
        return (body, 0)
    out = bytearray(body)
    for at, data in sorted(inserts, reverse=True):
        out[at:at] = data
    return (bytes(out), len(inserts))
GROUP_HEAD_COMPONENTS = 74

def group_scene_byte(body, targets):
    ids = class_ids()
    h = GROUP_HEAD_COMPONENTS
    if body[6:10] != struct.pack('<I', ids['EntityGroup']) or len(body) < h + 15 or body[h] != 3:
        return (body, 0)
    count = struct.unpack_from('<I', body, h + 1)[0]
    if count != 1 or body[h + 5:h + 7] != b'\x04\x00':
        return (body, 0)
    first = struct.unpack_from('<I', body, h + 11)[0]
    if first not in (ids['Scene'], ids['SceneSpawningComponent']):
        return (body, 0)
    bv = body.find(struct.pack('<I', ids['BoundingVolume']), h)
    if bv < h + ENTITY_TRAILER:
        return (body, 0)
    at = bv - ENTITY_TRAILER
    return (body[:at] + b'\x00' + body[at:], 1)

def _child_entities(body):
    ids = class_ids()
    ent, bv = (struct.pack('<I', ids['Entity']), struct.pack('<I', ids['BoundingVolume']))
    starts, k = ([], body.find(ent, 20))
    while k > 0:
        if body[k - 6:k - 4] == b'\x00\x01':
            starts.append(k - 6)
        k = body.find(ent, k + 4)
    out = []
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else len(body)
        e = body.rfind(bv, s, nxt)
        if e > s:
            out.append((s, e + 4))
    return out

def _group_head_components(body):
    import entity
    ids = class_ids()
    if len(body) < entity.FIRST + 10 or body[entity.LIST_MARK] != 3:
        return None
    count = struct.unpack_from('<I', body, entity.LIST_MARK + 1)[0]
    if count == 0:
        return []
    bv = body.find(struct.pack('<I', ids['BoundingVolume']), entity.FIRST)
    if bv < entity.FIRST + ENTITY_TRAILER:
        return None
    end = bv - ENTITY_TRAILER
    rev = {v: k for k, v in ids.items()}
    starts = []
    for k in range(entity.FIRST, end - 10 + 1):
        if body[k:k + 2] in entity.FLAGS:
            name = rev.get(struct.unpack_from('<I', body, k + 6)[0])
            if name and entity._is_component(name):
                starts.append((k, name))
    if len(starts) != count or starts[0][0] != entity.FIRST:
        return None
    return [(k, starts[i + 1][0] if i + 1 < len(starts) else end, name) for i, (k, name) in enumerate(starts)]

def scene_mid_byte(body, targets):
    import entity
    ids = class_ids()

    def inserts(ent, last_too):
        comps = entity.components(ent)
        if not comps:
            return []
        return [e for i, (_s, e, name) in enumerate(comps) if name in GREW_ONE_BYTE and (last_too or i < len(comps) - 1)]
    if body[6:10] == struct.pack('<I', ids['Entity']):
        at = inserts(body, last_too=False)
    elif body[6:10] == struct.pack('<I', ids['EntityGroup']):
        at = [s + k for s, e in _child_entities(body) for k in inserts(body[s:e], last_too=True)]
        head = _group_head_components(body)
        if head and (not (len(head) == 1 and head[0][2] in GREW_ONE_BYTE)):
            at += [e for _s, e, name in head if name in GREW_ONE_BYTE]
    else:
        return (body, 0)
    out = bytearray(body)
    for k in sorted(at, reverse=True):
        out[k:k] = b'\x00'
    return (bytes(out), len(at))
RULES = [('dependency kinds', clear_kinds), ('scene byte', scene_byte), ('animation byte', animation_byte), ('sky color', sky_color), ('spawn byte', spawn_byte), ('chase breaker', chase_breaker), ('group scene byte', group_scene_byte)]
EXTRA_RULES = {'scene mid byte': scene_mid_byte}

def convert(body, targets, rules=None):
    log = {}
    for name, fn in rules or RULES:
        body, n = fn(body, targets)
        log[name] = n
    return (body, log)

def targets(game):
    import inventory
    import xrefs
    return {u: xrefs.cls(game, homes[0][2]) for u, homes in inventory.universe(game).items()}
