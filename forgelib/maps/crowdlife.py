"""forgelib/maps/crowdlife.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import convert
import mapwalk
TEMPLATE_MAP = 'DataPC_AC3MP_Boston_Mill.forge'
TEMPLATE = 'AC3MP_GEN_Static_Group_089'
SLOTS = 7
IDS = convert.class_ids()
SPB = struct.pack('<I', IDS['SpawnPositionBlobParams'])
GPOS = struct.pack('<I', IDS['GameplayPosition'])
WORLD = IDS['World']
STATE_MATRIX = b'G\x00\x00\x00\x01\x00\x00\x00'
STATE_TAGS = (b'F\x00\x00\x00\x01\x00\x00\x00', STATE_MATRIX)
_CLASS_IDS = {struct.pack('<I', v) for v in IDS.values() if v >= 1 << 24}

def template():
    for p in mapwalk.payloads('AC3', TEMPLATE_MAP):
        for o in p.objects:
            if o['name'] == TEMPLATE:
                b = o['body']
                return (b, _world_of(b))
    raise SystemExit(f'{TEMPLATE} not in {TEMPLATE_MAP}')

def _world_of(b):
    gc = struct.pack('<I', IDS['GameplayCoordinator'])
    k = b.find(gc)
    if k < 0 or b[k + 8:k + 11] != b'\x03\x01\x00':
        raise SystemExit("template: GameplayCoordinator's World link not where expected")
    return struct.unpack_from('<I', b, k + 11)[0]

def embedded_uids(b):
    own = struct.unpack_from('<I', b, 2)[0]
    out = {own}
    for j in range(0, len(b) - 8):
        if b[j + 4:j + 8] in _CLASS_IDS:
            v = struct.unpack_from('<I', b, j)[0]
            if abs(v - own) < 1024:
                out.add(v)
    return out

def transposed(placement):
    m = struct.unpack('<16f', placement)
    return struct.pack('<16f', *(m[r + 4 * c] for r in range(4) for c in range(4)))

def state_matrices(b):
    own = transposed(b[10:74])
    return sorted((k + 8 for tag in STATE_TAGS for k in _all(b, tag) if b[k + 8:k + 72] == own))

def place_state(body, tmpl):
    mats = state_matrices(tmpl)
    other = [k + 8 for tag in STATE_TAGS for k in _all(tmpl, tag) if k + 8 not in mats]
    if other:
        raise SystemExit(f'template: a GameStateData matrix at +{other[0]} that is not its placement - not handled')
    new = transposed(bytes(body[10:74]))
    for k in mats:
        body[k:k + 64] = new
    return len(mats)

def check_state(body):
    own = state_matrices(body)
    return [k + 8 for tag in STATE_TAGS for k in _all(body, tag) if k + 8 not in own]

def _all(b, pat):
    out, k = ([], b.find(pat))
    while k >= 0:
        out.append(k)
        k = b.find(pat, k + 1)
    return out

def slot_offsets(b):
    spb = [k + 4 + 8 for k in _all(b, SPB)]
    gp = [k + 4 for k in _all(b, GPOS)]
    return (spb, gp)
MP_LOGIC = struct.pack('<I', IDS['GcLMPCivilianSocialize'])

def mp_slot_offsets(acr_body):
    import entity
    comps = entity.components(acr_body)
    if not comps:
        raise SystemExit('ACR entity: components not found')
    mp = [(s, e) for s, e, n in comps if n == 'GameplayCoordinatorComponent' and MP_LOGIC in acr_body[s:e]]
    if len(mp) != 1:
        raise SystemExit(f'ACR entity: {len(mp)} MP blend-group components')
    s, e = mp[0]
    return [k for k in slot_offsets(acr_body)[0] if s <= k < e]

def build(acr_body, tmpl, tmpl_world, new_world, alloc):
    a_spb = mp_slot_offsets(acr_body)
    t_spb, t_gp = slot_offsets(tmpl)
    if len(a_spb) != SLOTS or len(t_spb) != SLOTS or len(t_gp) != SLOTS:
        raise SystemExit(f'slot counts: ACR {len(a_spb)}, template {len(t_spb)} / {len(t_gp)}, expected {SLOTS}')
    body = bytearray(tmpl)
    own_t = struct.unpack_from('<I', tmpl, 2)[0]
    own_a = struct.unpack_from('<I', acr_body, 2)[0]
    mapping = {u: own_a if u == own_t else alloc() for u in sorted(embedded_uids(tmpl))}
    for old, new in mapping.items():
        pat, rep = (struct.pack('<I', old), struct.pack('<I', new))
        for k in _all(bytes(body), pat):
            body[k:k + 4] = rep
    wk = _all(bytes(body), struct.pack('<I', tmpl_world))
    if len(wk) != 1:
        raise SystemExit(f'template names its World {len(wk)} times')
    struct.pack_into('<I', body, wk[0], new_world)
    body[10:74] = acr_body[10:74]
    for i in range(SLOTS):
        slot = acr_body[a_spb[i]:a_spb[i] + 32]
        body[t_spb[i]:t_spb[i] + 32] = slot
        body[t_gp[i]:t_gp[i] + 32] = slot
    place_state(body, tmpl)
    if check_state(bytes(body)):
        raise SystemExit('rebuilt group: a GameStateData matrix still not its placement')
    return (bytes(body), mapping)
