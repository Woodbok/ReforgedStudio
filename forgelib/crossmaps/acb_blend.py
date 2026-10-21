"""forgelib/crossmaps/acb_blend.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
import convert
import crowdlife
IDS = convert.class_ids()
ATTRACTOR = struct.pack('<I', IDS['AttractorZone'])
_done = []

def is_acb_group(body):
    return ATTRACTOR in body and crowdlife.MP_LOGIC not in body

def build(acb_body, tmpl, tmpl_world, new_world, alloc):
    t_spb, t_gp = crowdlife.slot_offsets(tmpl)
    if len(t_spb) != crowdlife.SLOTS or len(t_gp) != crowdlife.SLOTS:
        raise SystemExit(f'template slot counts {len(t_spb)} / {len(t_gp)}, expected {crowdlife.SLOTS}')
    if acb_body[:2] != tmpl[:2] or acb_body[6:10] != tmpl[6:10]:
        raise SystemExit("ACB group: Entity header not the template's")
    body = bytearray(tmpl)
    own_t = struct.unpack_from('<I', tmpl, 2)[0]
    own_a = struct.unpack_from('<I', acb_body, 2)[0]
    mapping = {u: own_a if u == own_t else alloc() for u in sorted(crowdlife.embedded_uids(tmpl))}
    for old, new in mapping.items():
        pat, rep = (struct.pack('<I', old), struct.pack('<I', new))
        for k in crowdlife._all(bytes(body), pat):
            body[k:k + 4] = rep
    wk = crowdlife._all(bytes(body), struct.pack('<I', tmpl_world))
    if len(wk) != 1:
        raise SystemExit(f'template names its World {len(wk)} times')
    struct.pack_into('<I', body, wk[0], new_world)
    body[10:74] = acb_body[10:74]
    crowdlife.place_state(body, tmpl)
    if crowdlife.check_state(bytes(body)):
        raise SystemExit('rebuilt group: a GameStateData matrix still not its placement')
    assert all((body[k:k + 32] == tmpl[k:k + 32] for k in t_spb + t_gp))
    assert body[10:74] == acb_body[10:74] and struct.unpack_from('<I', body, 2)[0] == own_a
    return (bytes(body), mapping)

def patch():
    if _done:
        return
    orig = crowdlife.build

    def routed(src_body, tmpl, tmpl_world, new_world, alloc):
        if is_acb_group(src_body):
            return build(src_body, tmpl, tmpl_world, new_world, alloc)
        return orig(src_body, tmpl, tmpl_world, new_world, alloc)
    crowdlife.build = routed
    _done.append(True)
