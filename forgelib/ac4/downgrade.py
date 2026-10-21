"""forgelib/ac4/downgrade.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import collections
import os
import struct
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import matprops
sys.path.insert(0, os.path.join(common.ROOT, 'loader'))
sys.path.insert(0, os.path.join(common.ROOT, 'maps'))
import ac4mesh
import ac4tex
import world
SOUND_INSTANCE, SOUND_DESCRIPTOR = (2118318207, 3146661612)
BONE_EXTRA = bytes.fromhex('070000000000803f')
LOD_DESCRIPTOR = 2586927267
EMPTY_SLOT = bytes.fromhex('030000000000')
SLOT_AC3, SLOT_AC4 = (25, 28)
DROP = {'ProbeLightArea', 'ShadowPreset', 'Mission', 'MissionGroup', 'MissionRoot', 'MPObjective', 'MPMessage', 'MPTip', 'PacmanParams', 'CoordinatorTreasureHunterParameters', 'SoundPackagesDLCAddon', 'MissionManagerDLCAddon', 'ContentPackage', 'WorldLightMapSettings'}

class Note(Exception):
    pass

class Replaced(Note):

    def __init__(self, msg, body):
        super().__init__(msg)
        self.body = body

def texture_set(body):
    if len(body) < 82 or body[76:82] != EMPTY_SLOT:
        raise Note('12th slot not empty' if len(body) >= 82 else 'short')
    return body[:76] + body[82:]

def lod_selector(body):
    segs = world.segments(body)
    out = bytearray()
    slots = 0
    for name, s in segs:
        if name == 'LODDescriptor':
            if len(s) < 23 or s[19:22] != bytes(3):
                raise Note(f'slot {slots} shape')
            s = s[:19] + s[22:]
            slots += 1
        out += s
    if not slots:
        raise Note('no LODDescriptor element')
    if out[-1] == 1:
        out[-1] = 0
    return bytes(out)

def _bone_family(name):
    return name == 'Bone' or name == 'Hinge' or name.endswith('Modifier')

def skeleton(body):
    segs = world.segments(body)
    last = max((i for i, (n, _) in enumerate(segs) if _bone_family(n)), default=-1)
    if last < 0:
        raise Note('no Bone element')
    out = bytearray()
    removed = 0
    for i, (name, s) in enumerate(segs):
        if _bone_family(name):
            k = len(s) - 15
            if i == last:
                k = s.rfind(BONE_EXTRA)
            if k >= 4 and s[k:k + 8] == BONE_EXTRA:
                s = s[:k] + s[k + 8:]
                removed += 1
        out += s
    if not removed:
        raise Note('no bone-family element with the 8-byte field')
    return bytes(out)

def sound_instances(body):
    cls = struct.pack('<I', SOUND_INSTANCE)
    ac4 = cls + bytes(6) + struct.pack('<I', SOUND_DESCRIPTOR)
    if ac4 not in body:
        return (body, 0)
    return (body.replace(ac4, cls + bytes(5) + struct.pack('<I', SOUND_DESCRIPTOR)), body.count(ac4))
MESH_SUBMESH = bytes.fromhex('7fde5557')
MESH_COMPILED = bytes.fromhex('95159efc')

def without_mesh_source(body):
    if len(body) < 64 or body[6:10] != bytes.fromhex('68955d41') or body[15:21] != bytes.fromhex('010000000400') or (body[25:29] != MESH_SUBMESH):
        return body
    k = body.find(bytes(9) + MESH_COMPILED, 29)
    if k < 0 or body[k - 1] != 3:
        return body
    return body[:10] + bytes(18) + body[k + 9:]

def mesh(body):
    body = without_mesh_source(body)
    try:
        stride, count, data = ac4mesh.layout(body)
    except (AssertionError, struct.error, IndexError) as e:
        raise Note(f'ac4mesh layout: {e}')
    out = bytearray(body)
    if out[45] not in (5, 6):
        raise Note(f'version {out[45]}')
    out[45] -= 2
    for v in range(count):
        p = data + v * stride
        x, y, z, w = struct.unpack_from('<4h', out, p)
        s = -1 if w < 0 else 1
        if abs(w * 8) > 32767:
            k = abs(w * 8) / 32760
            x, y, z = (max(-32767, min(32767, round(c * k))) for c in (x, y, z))
            w = 4095 * (1 if w > 0 else -1)
        struct.pack_into('<4h', out, p, x * s, y * s, z * s, w * 8)
        out[p + 15] = out[p + 11]
        if stride == 24:
            out[p + 16], out[p + 18] = (out[p + 18], out[p + 16])
    end = data + stride * count
    isize = struct.unpack_from('<I', out, end)[0]
    zeros_at = end + 4 + isize + 8
    tail = bytes(out[:zeros_at]) + bytes(8) + bytes(out[zeros_at:])
    k = tail.rfind(bytes.fromhex('3300000007000000'))
    if k <= zeros_at:
        raise Note('no 33 / 07 block in the tail')
    tail = tail[:k] + bytes.fromhex('2400000020000000') + tail[k + 12:]
    return tail[:-5] + tail[-4:]

def texture_map(body):
    try:
        return ac4tex.downgrade(body)
    except (AssertionError, struct.error, IndexError) as e:
        raise Note(f'ac4tex: {e}')
MASK_TRIPLE = b'\x01\x01\x01'
MASK_FLOATS = struct.pack('<fIf', 1.0, 0, 1.0)
NULL_REF = bytes.fromhex('030000000000')
HASH_REMAP = {}
AC4_ONLY_HASHES = {3289703503, 93993871, 1034634609, 3789238889, 1827249645, 1166952237, 3232684262, 3180154436, 1994759111, 2465650859, 1707925126, 3885935222, 2077297112, 589987981, 157090923, 182707514, 316660045}

def _mask(s):
    if len(s) != 59 or s[43:55] != MASK_FLOATS:
        return s
    return s[:19] + s[22:26] + b'\x00' + s[26:43] + s[55:59]

def _texture_selector(s):
    if len(s) >= 22 and s[12:16] == bytes(4) and (s[16:22] == NULL_REF or s[16:18] == b'\x01\x00'):
        s = s[:12] + s[16:]
    elif len(s) >= 19 and s[9:13] == bytes(4) and (s[13:19] == NULL_REF or s[13:15] == b'\x01\x00'):
        s = s[:9] + s[13:]
    if HASH_REMAP:
        out = bytearray(s)
        for i in range(0, len(out) - 12):
            if out[i + 4:i + 10] == bytes(6) and out[i + 10:i + 12] in (b'\n\x00', b'\r\x00'):
                h = struct.unpack_from('<I', out, i)[0]
                if h in HASH_REMAP:
                    struct.pack_into('<I', out, i, HASH_REMAP[h])
        s = bytes(out)
    return s
AC4_SELECTOR_TEXTURES = {2921289716}

def material(body):
    try:
        mat = matprops.parse(body, 'AC4')
        header, mat_el, mask, mask16 = mat.segments()
    except matprops.ParseError as e:
        if 'Mask16' in str(e) or 'head' in str(e):
            raise Note(f'head layout unknown, left as is: {e}')
        raise Replaced(f'stand-in: {e}', _material_stub(body))
    items = []
    for it in mat.items:
        if it.hash in AC4_ONLY_HASHES or it.texture() in AC4_SELECTOR_TEXTURES:
            continue
        if it.kind == matprops.INLINE_KIND:
            t = struct.pack('<I', it.cls) + it.body
            if it.cls == matprops.TEXTURE_SELECTOR:
                t = _texture_selector(t)
            elif it.cls == matprops.TIME_OSCILLATOR:
                t = _time_oscillator(t)
            if len(t) - 4 != matprops.INLINE['AC3'].get(it.cls):
                raise Replaced(f"stand-in: {common.cls(it.cls, 'AC4')} body of {len(t) - 4} bytes is not AC3's", _material_stub(body))
            it = matprops.Item(it.hash, it.cls, it.flags, prefix=it.prefix, body=t[4:])
        items.append(it)
    return matprops.Material(header + mat_el + _mask(mask) + mask16, items).to_bytes()

def _material_stub(body):
    m = body.find(struct.pack('<I', matprops.MASK16))
    if m < 30 or len(body) < m + 24:
        return body
    return body[:26] + _mask(body[26:m]) + body[m:m + 24] + struct.pack('<I', 0)

def _mesh_instance_data(s):
    if len(s) == 20 and s[14:20] == bytes(6):
        return s[:14]
    return s

def _compiled_mesh_instance(s):
    if len(s) != 38:
        return s
    out = bytearray(s)
    if out[8] in (5, 6):
        out[8] -= 2
    if out[14] == 51:
        out[14] = 34
    if out[18] == 7:
        out[18] = 32
    return bytes(out)

def _mesh_instance_material_info(s):
    if len(s) < 27 or s[4] != 2 or s[9] != 0 or (s[14:16] != bytes.fromhex('0100')):
        return s
    p = 20
    while p < len(s) and s[p] == 3:
        p += 1
    if p >= len(s) or s[p] != 1 or p > 24:
        return s
    k = p - 20
    p += 1
    out = s
    if len(s) >= p + 13 and s[p + 2:p + 9] == bytes(7) and (s[p + 1] < 16):
        out = s[:p + 1] + s[p + 9:]
    q = p + 9
    count_form = k == 0 and len(out) in (40, 75) and (out[p + 5:q] == bytes(4)) and (out[q] == 0)
    if count_form:
        out = out[:q] + out[q + 1:q + 4] + bytes(5) + out[q + 4:]
    t = _tails(out)
    if t == out and len(out) == (79 if count_form else 71 + k) and (out[-8:-4] == ONE_F) and (out[-11] <= 1) and (out[-21] <= 1) and all((b <= 1 for b in out[-41:-21])):
        t = _entity_trailer(out)
    if t != out:
        out = bytearray(t)
        if len(out) > 24:
            out[-24] = 0
        return bytes(out)
    return out
ONE_F = struct.pack('<f', 1.0)

def _tail_f(s):
    if len(s) >= 28 and s[-8:-4] == ONE_F and (s[-9] == 0) and (s[-11] == 1) and (s[-20:-12] == bytes(8)):
        return s[:-12] + s[-10:]
    return s

def _tail_m(s):
    if len(s) >= 28 and s[-8:-4] == ONE_F and (s[-10:-8] == b'\xff\xff') and (s[-11] == 1) and (s[-21] == 0):
        return s[:-21] + s[-20:-11] + s[-10:]
    return s

def _tail_f2(s):
    if len(s) < 30 or s[-8:-4] != ONE_F or s[-9] != 0 or (s[-11] != 1):
        return s
    j = -12
    while j > -len(s) + 6 and s[j] == 0 and (j > -17):
        j -= 1
    if s[j] != 1 or j > -14 or s[j - 1] != 0:
        return s
    return s[:j - 1] + s[j:-11] + s[-10:]
_AC3_LENGTHS = None

def ac3_lengths(name):
    global _AC3_LENGTHS
    if _AC3_LENGTHS is None:
        import pickle
        path = os.path.join(common.OUT, '_ac3_shapes.pkl')
        _AC3_LENGTHS = pickle.load(open(path, 'rb')) if os.path.exists(path) else {}
    return _AC3_LENGTHS.get(name, {})

def _tail_zeros(s, name):
    lens = ac3_lengths(name)
    if lens and len(s) not in lens and (len(s) - 2 in lens) and (len(s) >= 20) and (s[-8:-4] == ONE_F) and (s[-9] == 0) and (s[-12:-10] == bytes(2)):
        return s[:-12] + s[-10:]
    return s

def _tail_walk(s, name):
    lens = ac3_lengths(name)
    if not lens or len(s) in lens or len(s) - 2 not in lens or (len(s) < 30) or (s[-8:-4] != ONE_F) or (s[-9] != 0) or (s[-11] != 1):
        return s
    j = -12
    while j > -len(s) + 8 and s[j] == 0:
        j -= 1
    if j == -12:
        return s
    while j > -len(s) + 8 and s[j] != 0:
        j -= 1
    if s[j] != 0 or j > -14:
        return s
    return s[:j] + s[j + 1:-11] + s[-10:]

def _tails(s, name=None):
    t = _tail_f(s)
    if t != s:
        return t
    t = _tail_m(s)
    if t != s:
        return t
    t = _tail_f2(s)
    if t != s or name is None:
        return t
    t = _tail_zeros(s, name)
    return t if t != s else _tail_walk(s, name)

def _sound_actor(s):
    if len(s) == 27 and s[4:8] == bytes(4) and (s[8] == 5) and (s[13:17] == bytes(4)):
        return s[:4] + s[17:]
    return s

def _spawn_npc_params(s):
    if len(s) < 9:
        return s
    if s[8] == 1 and len(s) >= 14 and (s[9] == 0):
        return _tails(s[:14] + s[9:14] + s[14:])
    return _tails(s[:9] + bytes(5) + s[9:])
LAYER_AC4_TO_AC3 = {19: 18, 22: 21, 55: 24}

def _collision_filter_info(s):
    if len(s) in (103, 212) and s[30:34] == bytes(4):
        s = s[:30] + s[34:35] + b'\x01\x00' + s[35:]
        layer = struct.unpack_from('<I', s, 4)[0]
        if layer in LAYER_AC4_TO_AC3:
            s = s[:4] + struct.pack('<I', LAYER_AC4_TO_AC3[layer]) + s[8:]
    return s

def _zeros_dropped(at, n):

    def rule(s):
        return s[:at] + s[at + n:] if len(s) > at + n and s[at:at + n] == bytes(n) else s
    return rule

def _particle_system_instance_data(s):
    if len(s) in (37, 72) and s[14] == 1:
        out = bytearray(s[:14] + s[22:])
        if len(s) == 72:
            out = out[:52] + out[54:]
            out[38] = 0
        return bytes(out)
    return s
CK_BOX, CK_CIRCLE = ('ContextualKillBoxEditableParams', 'ContextualKillCircleEditableParams')

def _ck_long_tail(s, at):
    t = _tails(s)
    if len(t) == len(s) - 2:
        t = bytearray(t)
        t[at + 15] = 0
        return bytes(t)
    return s

def _ck_box(s):
    if len(s) in (90, 125):
        s = s[:4] + bytes(4) + s[8:60] + s[80:]
        return _ck_long_tail(s, 64) if len(s) == 105 else s
    return s

def _ck_circle(s):
    if len(s) == 77:
        s = s[:4] + bytes(4) + s[8:]
        return _ck_long_tail(s, 36)
    return s

def _time_oscillator(s):
    if len(s) >= 24 and s[16:20] == bytes(4):
        return s[:16] + s[20:]
    return s

def _anim_component(s):
    if len(s) == 95 and s[74] == 0 and (s[84] == 1):
        return s[:74] + s[75:84] + s[85:]
    return s

def _crowd_duty_region(s):
    return s[:21] + b'\x00' + s[21:] if len(s) > 21 and s[20] == 1 else s

def _wwise_id(s):
    if len(s) == 55 and s[49:51] == bytes.fromhex('0400') and (s[45:49] == bytes(4)):
        return s[:25] + s[49:]
    return s

def _entity_trailer(s):
    return s[:-21] + s[-20:-11] + s[-10:]
AC3_FLAG15_SET = frozenset({'AnimComponent', 'OLMPRestObject', 'WindForce', 'NPCAssassinGameData'})
AC3_FLAG15_CLEAR = frozenset({'CTFScoreZoneComponent', 'Checkpoint', 'ContextualKillBoxEditableParams', 'ContextualKillCircleEditableParams', 'ControlPointComponent', 'Entity', 'EntityGroup', 'FXCommand', 'FlagComponent', 'FreeRunTargetingMagnetComponent', 'GameplayCoordinatorOutput', 'GameplaySurfaceNavType', 'LightFlare', 'MeshInstanceMaterialInfo', 'MultiMeshShape', 'MultiSpawnPlayerComponent', 'PartNode', 'ParticleSystemInstanceData', 'PickupComponent', 'Reflector', 'ReflectorCullingZone', 'SpawnNPCParams', 'TriggerZone', 'WayPointRef'})
NO_CELL_INDEX = False
NO_CELL = bytes([255, 255])

def _clear_ac4_entity_flag(segs):
    out = list(segs)
    n = 0
    for i in range(len(out) - 1):
        if out[i + 1][0] != 'BoundingVolume':
            continue
        name, t = out[i]
        if not (len(t) >= 39 and t[-8:-4] == ONE_F and all((b <= 1 for b in t[-39:-19])) and all((b <= 1 for b in t[-15:-10]))):
            continue
        if t[-24] and name not in AC3_FLAG15_SET:
            t = t[:-24] + bytes(1) + t[-23:]
            n += 1
        if NO_CELL_INDEX and t[-10:-8] != NO_CELL:
            t = t[:-10] + NO_CELL + t[-8:]
            n += 1
        out[i] = (name, t)
    return (out, n)

def no_cell_index(body):
    if not NO_CELL_INDEX:
        return (body, 0)
    segs = world.segments(body)
    out, n = ([], 0)
    for i, (name, t) in enumerate(segs):
        if i + 1 < len(segs) and segs[i + 1][0] == 'BoundingVolume' and (len(t) >= 39) and (t[-8:-4] == ONE_F) and all((b <= 1 for b in t[-39:-19])) and all((b <= 1 for b in t[-15:-10])) and (t[-10:-8] != NO_CELL):
            t = t[:-10] + NO_CELL + t[-8:]
            n += 1
        out.append(t)
    return (b''.join(out) if n else body, n)

def _multi_mesh_shape(s):
    if len(s) >= 57 and s[-11] == 1 and (s[-21] <= 1) and (s[-8:-4] == ONE_F) and all((b <= 1 for b in s[-41:-21])) and all((b <= 1 for b in s[-16:-11])):
        return _entity_trailer(s)
    return s
SOUND_ELEMENTS = {'SoundInstance', 'SoundDescriptor', 'WwiseID', 'SoundEmitter', 'EventSwitchDependencies', 'SoundRtpcDependencies'}

def _hay_stack_sounds(segs):
    out = []
    i = 0
    while i < len(segs):
        name, s = segs[i]
        if name == 'HayStackComponent' and len(s) == 79:
            j = i + 1
            while j < len(segs) and segs[j][0] in SOUND_ELEMENTS:
                j += 1
            if j > i + 1 and segs[j - 1][1][-6:-4] == bytes.fromhex('0400'):
                out.append((name, s[:75] + segs[j - 1][1][-6:]))
                i = j
                continue
        out.append((name, s))
        i += 1
    return out
STATIC_DECAL = bytes.fromhex('6d7f3a2f')
DECAL_SUBTREE = {'LODSelectorInstance', 'MeshInstanceData', 'CompiledMeshInstance', 'MeshInstanceMaterialInfo'}
ENTITY_TRAILER = 35

def _decal_heads(s):
    n, k = (0, s.find(STATIC_DECAL))
    while k >= 0:
        if k >= 6 and s[k - 6:k - 4] == bytes.fromhex('0400') and (s[k + 4:k + 7] == bytes.fromhex('010100')):
            n += 1
        k = s.find(STATIC_DECAL, k + 4)
    return n
KEEP_DECALS = False
VISUAL = bytes.fromhex('298d65ec')
LOD_SELECTOR_INSTANCE = bytes.fromhex('62744301')
MESH_INSTANCE_DATA = bytes.fromhex('3b966e53')
DECAL_STATS = collections.Counter()

def _decals_as_visuals(body):
    out = bytearray(body)
    n, k = (0, body.find(STATIC_DECAL))
    while k >= 0:
        if k >= 6 and body[k - 6:k - 4] == bytes.fromhex('0400') and (body[k + 4:k + 7] == bytes.fromhex('010100')):
            nxt = body[k + 16:k + 20]
            direct = nxt == MESH_INSTANCE_DATA and body[k + 20:k + 21] == bytes((1,)) and (body[k + 21:k + 25] == body[k + 7:k + 11])
            if nxt == LOD_SELECTOR_INSTANCE or direct:
                out[k:k + 4] = VISUAL
                n += 1
                DECAL_STATS['visual (direct mesh)' if direct else 'visual (LODSelector)'] += 1
        k = body.find(STATIC_DECAL, k + 4)
    return (bytes(out), n)

def _strip_static_decals(segs):
    out = list(segs)
    n = 0
    i = 0
    while i < len(out):
        name, s = out[i]
        k = s.find(STATIC_DECAL)
        if k >= 6 and s[k - 6:k - 4] == bytes.fromhex('0400') and (s[k + 4:k + 6] == bytes.fromhex('0103')) and (len(s) - 6 - (k + 4) <= 40) and (s[-6:-4] == bytes.fromhex('0400')) and (s.find(STATIC_DECAL, k + 4) < 0):
            out[i] = (name, s[:k - 6] + s[-6:])
            h = i
            while h >= 0 and (not (out[h][0] in ('Entity', 'EntityGroup') and len(out[h][1]) >= 79)):
                h -= 1
            if h < 0:
                raise Note('StaticDecalComponent left: no owning entity header')
            hdr = bytearray(out[h][1])
            count = struct.unpack_from('<I', hdr, 69)[0]
            if count == 0:
                raise Note("StaticDecalComponent left: the owner's component count is 0")
            struct.pack_into('<I', hdr, 69, count - 1)
            out[h] = (out[h][0], bytes(hdr))
            n += 1
            DECAL_STATS['stub stripped'] += 1
            continue
        if k >= 6 and s[k - 6:k - 4] == bytes.fromhex('0400') and (s[k + 4:k + 7] == bytes.fromhex('010100')) and (len(s) == k + 16):
            j = i + 1
            while j < len(out) and out[j][0] in DECAL_SUBTREE:
                j += 1
            last = out[j - 1][1] if j - 1 > i else s[k + 12:]
            if j < len(out) and out[j][0] == 'BoundingVolume':
                rule = COMPONENT_RULES.get(out[j - 1][0]) if j - 1 > i else None
                conv = rule(last) if rule is not None else last
                tail = conv[-(ENTITY_TRAILER + 4):]
            elif len(last) >= 6 and last[-6:-4] == bytes.fromhex('0400'):
                tail = last[-6:]
            else:
                raise Note('StaticDecalComponent left: its subtree ends in an unknown way')
            removed = 1 + sum((_decal_heads(seg) for _nm, seg in out[i + 1:j]))
            out[i] = (name, s[:k - 6] + tail)
            del out[i + 1:j]
            h = i
            while h >= 0 and (not (out[h][0] in ('Entity', 'EntityGroup') and len(out[h][1]) >= 79)):
                h -= 1
            if h < 0:
                raise Note('StaticDecalComponent left: no owning entity header')
            hdr = bytearray(out[h][1])
            count = struct.unpack_from('<I', hdr, 69)[0]
            if count < removed:
                raise Note(f"StaticDecalComponent left: the owner's component count {count} < {removed} decals removed")
            struct.pack_into('<I', hdr, 69, count - removed)
            out[h] = (out[h][0], bytes(hdr))
            n += removed
            DECAL_STATS['mesh stripped'] += removed
            continue
        i += 1
    return (out, n)
STRIP_FX_OWNERS = set()
FX_SUBTREE = {'FXCommand'}

def _strip_fx_components(segs):
    out = list(segs)
    n = 0
    i = 1
    while i < len(out):
        name, s = out[i]
        prev = out[i - 1][1]
        if name == 'FXComponent' and len(prev) >= 6 and (prev[-6:-4] == bytes.fromhex('0400')):
            j = i + 1
            while j < len(out) and out[j][0] in FX_SUBTREE:
                j += 1
            if j == i + 1:
                raise Note('FXComponent left: no FXCommand after it')
            last = out[j - 1][1]
            if j < len(out) and out[j][0] == 'BoundingVolume':
                rule = COMPONENT_RULES.get(out[j - 1][0])
                conv = rule(last) if rule is not None else last
                tail = conv[-(ENTITY_TRAILER + 4):]
            elif len(last) >= 6 and last[-6:-4] == bytes.fromhex('0400'):
                tail = last[-6:]
            else:
                raise Note('FXComponent left: its subtree ends in an unknown way')
            out[i - 1] = (out[i - 1][0], prev[:-6] + tail)
            del out[i:j]
            h = i - 1
            while h >= 0 and (not (out[h][0] in ('Entity', 'EntityGroup') and len(out[h][1]) >= 79)):
                h -= 1
            if h < 0:
                raise Note('FXComponent left: no owning entity header')
            hdr = bytearray(out[h][1])
            count = struct.unpack_from('<I', hdr, 69)[0]
            if count == 0:
                raise Note("FXComponent left: the owner's component count is 0")
            struct.pack_into('<I', hdr, 69, count - 1)
            out[h] = (out[h][0], bytes(hdr))
            n += 1
            continue
        i += 1
    return (out, n)

def _light_instance(s):
    return s[:9] + s[10:] if len(s) == 14 and s[9] == 0 else s
GUIDANCE_TYPE_AC4_TO_AC3 = {15: 1, 16: 14}
AC3_GUIDANCE_TYPES_MAX = 14

def _guidance_object(s):
    if len(s) >= 37:
        word = struct.unpack_from('<I', s, 5)[0]
        t = word & 31
        if t in GUIDANCE_TYPE_AC4_TO_AC3:
            return s[:5] + struct.pack('<I', word & ~31 | GUIDANCE_TYPE_AC4_TO_AC3[t]) + s[9:]
    return s
GUIDANCE_SYSTEM = struct.pack('<I', 1437539390)

def guidance_types(body):
    out = collections.Counter()
    if GUIDANCE_SYSTEM in body:
        for name, s in world.segments(body):
            if name == 'GuidanceObject' and len(s) >= 9:
                out[struct.unpack_from('<I', s, 5)[0] & 31] += 1
    return out
COMPONENT_RULES = {'GuidanceObject': _guidance_object, 'WwiseID': _wwise_id, 'MultiMeshShape': _multi_mesh_shape, 'LightInstance': _light_instance, 'CrowdDutyRegion': _crowd_duty_region, 'MeshInstanceData': _mesh_instance_data, 'CompiledMeshInstance': _compiled_mesh_instance, 'MeshInstanceMaterialInfo': _mesh_instance_material_info, 'SpawnNPCParams': _spawn_npc_params, 'SoundActor': _sound_actor, 'CollisionFilterInfo': _collision_filter_info, 'AC2EventCondition': _zeros_dropped(6, 8), 'BooleanRuleCondition': _zeros_dropped(5, 8), 'ParticleSystemInstanceData': _particle_system_instance_data, 'AnimComponent': lambda s: _tails(_anim_component(s)), 'TimeOscillatorData': _time_oscillator, CK_BOX: _ck_box, CK_CIRCLE: _ck_circle}
for _name in ('Entity', 'EntityGroup', 'GameplaySurfaceNavType', 'OLMPRestObject', 'TriggerZone', 'FXCommand', 'MultiSpawnPlayerComponent', 'ControlPointComponent', 'FlagComponent', 'CTFScoreZoneComponent', 'WindForce', 'NPCAssassinGameData', 'HayStackComponent', 'GameplayZoneComponent', 'Checkpoint', 'PlayerSpawnActivatorComponent', 'BhvHorseData', 'Reflector', 'WayPointRef', 'AnimComponent', 'Partitioner', 'GameplayCoordinatorOutput'):
    COMPONENT_RULES[_name] = _tails

def components(body):
    n_vis = 0
    if KEEP_DECALS:
        body, n_vis = _decals_as_visuals(body)
    segs = world.segments(body)
    segs, n = _strip_static_decals(segs)
    n += n_vis
    if body[:2] == b'\x00\x01' and struct.unpack_from('<I', body, 2)[0] in STRIP_FX_OWNERS:
        segs, n_fx = _strip_fx_components(segs)
        n += n_fx
    before = len(segs)
    segs = _hay_stack_sounds(segs)
    n += before - len(segs)
    out = bytearray()
    for name, s in segs:
        rule = COMPONENT_RULES.get(name)
        if rule is not None:
            t = rule(s)
            if t == s and rule is _tails:
                t = _tails(s, name)
            n += t != s
            s = t
        out += s
    if not n:
        raise Note('no component rule applied')
    segs2, cleared = _clear_ac4_entity_flag(world.segments(bytes(out)))
    if cleared:
        out = bytearray().join((bytes(sg) for _, sg in segs2))
    return bytes(out)
LIGHT_COLOR, LIGHT_OSC, LIGHT_FLARE = (2811717613, 3974486380, 2861536950)
AC3_FROM_AC4_LIGHT_FLAGS = [0, 1, 3, 4, 6, 7, 8, 9, 10, 12, None]

def omni_light(body):
    pre = b''
    if body[:2] == bytes((0, 1)):
        pre, body = (body[:6], body[6:])
    p = 4 + 5 + 4
    if struct.unpack_from('<I', body, p)[0] != LIGHT_COLOR:
        raise ValueError('OmniLight: no Color')
    p += 4 + 16
    flags4 = body[p:p + 14]
    if len(flags4) != 14 or any((x > 1 for x in flags4)):
        raise ValueError('OmniLight: flags')
    head = body[:p]
    p += 14
    intensity = body[p:p + 4]
    p += 4
    if struct.unpack_from('<I', body, p + 4)[0] != LIGHT_OSC:
        raise ValueError('OmniLight: no TimeOscillatorData')
    p += 28
    if struct.unpack_from('<I', body, p + 4)[0] != LIGHT_FLARE:
        raise ValueError('OmniLight: no LightFlare')
    flare = body[p:p + 45]
    p += 45
    tail4 = body[p:p + 16]
    p += 24
    radii = body[p:p + 8]
    if p + 8 != len(body):
        raise ValueError('OmniLight: length')
    flags3 = bytes((flags4[i] if i is not None else 0 for i in AC3_FROM_AC4_LIGHT_FLAGS))
    return pre + head + flags3 + intensity + flare + tail4 + radii

def _light_instance_tail(seg):
    at = 4 + 37 + 2
    if len(seg) <= at + 12 or seg[at - 2:at] != bytes((1, 0)) or seg[at:at + 8] != bytes(8):
        return seg
    seg = seg[:at] + seg[at + 8:]
    t = seg[at + 4:]
    if len(t) == 45 and t[19] == 1 and (t[22] == 0) and (t[34] == 1):
        t = t[:19] + bytes(1) + t[20:22] + t[23:34] + t[35:]
    return seg[:at + 4] + t
COMPONENT_RULES['LightFlare'] = _light_instance_tail
COMPONENT_RULES['PartNode'] = _multi_mesh_shape
COMPONENT_RULES['FreeRunTargetingMagnetComponent'] = _multi_mesh_shape
COMPONENT_RULES['MetaLinkRef'] = _multi_mesh_shape

def _reflector(s):
    if len(s) >= 28 and s[24:28] == ONE_F:
        if s[15:24] != bytes(9):
            raise Note("Reflector: AC4's extra byte before the 1.0 is not zero")
        s = s[:15] + s[16:]
    t = _tails(s)
    return t if t != s else _tails(s, 'Reflector')
COMPONENT_RULES['Reflector'] = _reflector
COMPONENT_RULES['AC2MPInConflictCondition'] = _zeros_dropped(6, 8)

def _tail_m0(s):
    if len(s) >= 28 and s[-8:-4] == ONE_F and (s[-10:-8] == b'\xff\xff') and (s[-11] == 0) and (s[-21] == 0) and (s[-19:-11] == bytes(8)):
        return s[:-21] + s[-20:-11] + s[-10:]
    return s

def _surface_nav_type(s):
    t = _tails(s)
    if t != s:
        return t
    t = _tail_m0(s)
    return t if t != s else _tails(s, 'GameplaySurfaceNavType')

def _anim_component_rule(s):
    a = _anim_component(s)
    t = _tails(a)
    return t if t != a else _tail_m0(a)
COMPONENT_RULES['AnimComponent'] = _anim_component_rule
COMPONENT_RULES['GameplaySurfaceNavType'] = _surface_nav_type
COMPONENT_RULES['ReflectorCullingZone'] = _multi_mesh_shape
NIGHT_LAYER = 1967633215
_DLF_PLAIN = struct.pack('<I', 3676127342) + bytes(4) + bytes((3,))

def night_light(body):
    segs = world.segments(body)
    out = []
    vis = 0
    for name, sg in segs:
        if name == 'Visual' and len(sg) > 4 and (sg[4] == 0):
            sg = sg[:4] + bytes((1,)) + sg[5:]
            vis += 1
        out.append(sg)
    b = b''.join(out)
    if not b.endswith(_DLF_PLAIN) or vis == 0:
        raise ValueError(f'night_light: Visual enabled {vis}, plain filter at the end {b.endswith(_DLF_PLAIN)}')
    night = struct.pack('<I', 3676127342) + struct.pack('<I', 1) + bytes(4) + struct.pack('<I', 3809842145) + bytes(1) + struct.pack('<I', NIGHT_LAYER) + bytes(4) + bytes((3,))
    return b[:-len(_DLF_PLAIN)] + night
RULES = {'OmniLight': omni_light, 'TextureSet': texture_set, 'LODSelector': lod_selector, 'Skeleton': skeleton, 'Mesh': mesh, 'TextureMap': texture_map, 'Material': material, 'Entity': components, 'EntityGroup': components, 'CrowdDutyRegion': components}

def convert(cls, body):
    done = collections.Counter()
    out, n = sound_instances(body)
    if n:
        done['SoundInstance byte'] += n
    rule = RULES.get(cls)
    if rule is None:
        done['unruled'] += 1
        return (out, done)
    try:
        out = rule(out)
        done[cls] += 1
    except Replaced as e:
        out = e.body
        done[f'{cls}: {e}'] += 1
    except Note as e:
        done[f'{cls}: {e}'] += 1
    return (out, done)
SUB_OBJECT_POINT = struct.pack('<I', 2989229202)

def _cb_sub_object_point(s):
    k = s.find(SUB_OBJECT_POINT)
    if k < 25 or len(s) != k + 26 or s[k + 20:k + 22] != b'\x04\x00' or (s[k + 16:k + 20] != bytes(4)):
        return s
    return s[:k - 4] + s[k + 20:]
for _name in ('TriggerComponent', 'InertComponent', 'GuidanceSystem', 'FreeRunTargetingMagnetComponent'):
    COMPONENT_RULES.setdefault(_name, _cb_sub_object_point)
COMPONENT_RULES['FreeRunTargetingMagnetComponent'] = lambda s: _multi_mesh_shape(_cb_sub_object_point(s))
OBJECTAI_IDS = None
INVENTORY_DATA_ITEM = bytes.fromhex('426557540300000000')
LOGICAL_INVENTORY = bytes.fromhex('83d2bfc30000000000000000')

def _object_ai(s):
    if len(s) == 17 and s[4:12] == bytes.fromhex('0400000003030303') and (s[12] == 0):
        if OBJECTAI_IDS is None:
            raise Note('ObjectAI sofa form: no fresh ids')
        nxt = s[13:17]
        a, b = (next(OBJECTAI_IDS), next(OBJECTAI_IDS))
        return s[:11] + b'\x00' + struct.pack('<I', a) + INVENTORY_DATA_ITEM + b'\x00' + struct.pack('<I', b) + LOGICAL_INVENTORY + b'\x00' + nxt
    return s
COMPONENT_RULES['ObjectAI'] = _object_ai
sys.path.insert(0, os.path.join(common.ROOT, 'ac4chars'))
import skinmesh
SKIN_MARGIN = 0.05

def skinned_recentred(body):
    try:
        lay = skinmesh.layout(body)
        f = skinmesh.position_range(body, lay)
    except skinmesh.MeshError:
        return body
    if not lay['count'] or f <= skinmesh.AC3_RANGE:
        return body
    s, vb, nv = (lay['stride'], lay['vb'], lay['nv'])
    pts = [[q * f / 32768 for q in struct.unpack_from('<3h', body, vb + s * v)] for v in range(nv)]
    lim = skinmesh.AC3_RANGE - SKIN_MARGIN
    c = []
    for k in range(3):
        lo, hi = (min((p[k] for p in pts)), max((p[k] for p in pts)))
        if hi - lo > 2 * lim:
            raise Note(f"skinned extent {hi - lo:.2f} m on axis {k}: wider than AC3's 20 m")
        c.append(0.0 if max(-lo, hi) <= lim else (lo + hi) / 2)
    out = bytearray(body)
    for v, p in enumerate(pts):
        struct.pack_into('<3h', out, vb + s * v, *(max(-32767, min(32767, round((p[k] - c[k]) * 32768 / skinmesh.AC3_RANGE))) for k in range(3)))
    for at, _bone in skinmesh.entries(body, lay):
        a_b = [x / 32768 for x in struct.unpack_from('<6h', body, at + 16)]
        A, B = (a_b[:3], a_b[3:])
        C = [A[1] * B[2] - A[2] * B[1], A[2] * B[0] - A[0] * B[2], A[0] * B[1] - A[1] * B[0]]
        u = struct.unpack_from('<Q', body, at + 28)[0]
        t = skinmesh.unpack_bind(u)
        t = [round(t[k] + (c[0] * C[k] + c[1] * A[k] + c[2] * B[k]) * 8192) for k in range(3)]
        struct.pack_into('<Q', out, at + 28, skinmesh.pack_bind(t, u >> 63))
    k = bytes(out).rfind(skinmesh.TAIL4)
    struct.pack_into('<f', out, k + 8, skinmesh.AC3_RANGE)
    return bytes(out)
