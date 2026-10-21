"""forgelib/crossmaps/acb_merchants.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
import convert
IDS = convert.class_ids()
ATTRACTOR = struct.pack('<I', IDS['AttractorZone'])
SOCIALIZE = struct.pack('<I', IDS['GcLCivilianSocialize'])
MP_SOCIALIZE = struct.pack('<I', IDS['GcLMPCivilianSocialize'])
PREFIX = 'AC2MP_GEN_AMerchant'
ORATOR_PREFIX = 'AC2MP_GEN_AOrator'
HERALD = struct.pack('<I', 184855069)
ORATOR_TEMPLATE = ('DataPC_ACFE_Constantinople.forge', 'ACFE_GEN_Orator_002')
ORATOR_SPEAKER_X = -2.5254
MERCHANT_X = -2.23
_done = []

def is_acb_merchant(body):
    return ATTRACTOR in body and SOCIALIZE in body and (MP_SOCIALIZE not in body)

def is_acb_orator(body):
    return is_acb_merchant(body) and HERALD in body

def placed(placement, slot):
    m = list(struct.unpack('<16f', placement))
    x, y, z = struct.unpack_from('<3f', slot, 0)
    for c in range(3):
        m[12 + c] -= x * m[c] + y * m[4 + c] + z * m[8 + c]
    return struct.pack('<16f', *m)

def stall_origin(placement, slot_x=MERCHANT_X):
    return struct.unpack_from('<3f', placed(placement, struct.pack('<3f', slot_x, 0.0, 0.0)), 48)
_orator = []

def orator_slots():
    if not _orator:
        import crowdlife
        import mapwalk
        for p in mapwalk.payloads('ACR', ORATOR_TEMPLATE[0]):
            for o in p.objects:
                if o['name'] == ORATOR_TEMPLATE[1] and o['body'][6:10] == struct.pack('<I', IDS['Entity']):
                    b = o['body']
                    mp = crowdlife.mp_slot_offsets(b)
                    npc = [k for k in crowdlife.slot_offsets(b)[0] if k not in mp]
                    assert len(npc) == 1 and len(mp) == 7, (len(npc), len(mp))
                    speaker = bytearray(b[npc[0]:npc[0] + 32])
                    assert abs(struct.unpack_from('<f', speaker, 0)[0] - ORATOR_SPEAKER_X) < 0.001
                    struct.pack_into('<f', speaker, 8, 0.0)
                    _orator.append((bytes(speaker), [b[k:k + 32] for k in mp]))
                    return _orator[0]
        raise SystemExit(f"{ORATOR_TEMPLATE[1]} not in ACR's {ORATOR_TEMPLATE[0]}")
    return _orator[0]
_slots = []

def patch():
    if _done:
        return
    import merchant_stalls
    orig_rebuild, orig_slots = (merchant_stalls.rebuild, merchant_stalls.acr_slots)

    def acr_slots(body):
        if not is_acb_merchant(body):
            return orig_slots(body)
        if is_acb_orator(body):
            return orator_slots()
        if not _slots:
            raise SystemExit("acb_merchants: an ACB merchant's slots asked for before any rebuild")
        return _slots[-1]

    def rebuild(tmpl, tmpl_world, acr_body, target_body, world, builder, alloc):
        if not is_acb_merchant(acr_body):
            return orig_rebuild(tmpl, tmpl_world, acr_body, target_body, world, builder, alloc)
        s1, _g1, s2, _g2 = merchant_stalls.blocks(tmpl)
        if is_acb_orator(acr_body):
            merchant, blend = orator_slots()
        else:
            merchant, blend = (tmpl[s1:s1 + 32], [tmpl[k:k + 32] for k in s2])
            if _slots and _slots[-1] != (merchant, blend):
                raise SystemExit('acb_merchants: two templates in one run')
            _slots[:] = [(merchant, blend)]
        target = target_body[:10] + placed(target_body[10:74], merchant) + target_body[74:]
        body, mapping = orig_rebuild(tmpl, tmpl_world, acr_body, target, world, builder, alloc)
        assert body[s1:s1 + 32] == merchant and all((body[k:k + 32] == b for k, b in zip(s2, blend)))
        m = struct.unpack_from('<16f', body, 10)
        t = [m[12 + c] + sum((struct.unpack_from('<3f', merchant)[r] * m[4 * r + c] for r in range(3))) for c in range(3)]
        want = struct.unpack_from('<3f', acr_body, 58)
        assert max((abs(a - b) for a, b in zip(t, want))) < 0.001, (t, want)
        return (body, mapping)
    merchant_stalls.acr_slots, merchant_stalls.rebuild = (acr_slots, rebuild)
    import npc_port
    orig_selector = npc_port.selector_copy

    def selector_copy(sel, builders, fresh, classes):
        b = sel['body']
        for old in builders:
            flat = b'\x1c\x00\x01\x00' + struct.pack('<I', old)
            if b.count(flat) == 1:
                b = b.replace(flat, b'\x1c\x00\x01\x01' + struct.pack('<I', old))
        return orig_selector(dict(sel, body=b), builders, fresh, classes)
    npc_port.selector_copy = selector_copy
    _done.append(True)
