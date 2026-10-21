"""forgelib/jester_materials.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dxt
import make_jester
import objects
from datafile import compress, decompress, first_chunk_raw_size
from forge import Forge
from transplant import build_object, references
PARTS = {'AC3MP_ID79_MainPart_V1': 'Torso', 'AC3MP_ID79_SecondaryPart_V1': 'Arms', 'AC3MP_ID79_Legs_v1': 'Legs', 'AC3MP_ID79_belt_V1': 'Belt'}
FLOAT, FLOAT3 = (b'\n\x00', b'\r\x00')
PROPERTY_NAMES = {1340403338: 'Diffuse SaturationFX', 429782505: 'SpecularMainColor', 1490514617: 'Specular2ndColor', 3743390854: 'Custom_Color_1', 1804279304: 'Specular Factor Skin', 33826407: 'Specular Factor Clothes', 887075822: 'Specular Factor Metal', 909430555: 'Specular Power Skin', 3751371088: 'Specular Power Clothes', 2956742113: 'Specular Power Metal', 3132447058: 'CubeMap Factor Clothes', 2469303768: 'Fresnel Clothes Power', 3017716122: 'Fresnel Clothes Factor', 4227388023: 'Fresnel Skin Power', 4125088164: 'Fresnel Skin Factor'}
BY_NAME = {v: k for k, v in PROPERTY_NAMES.items()}
LEAVE = {'Custom_Color_1'}
SCALED = {'Fresnel Clothes Factor': 1, 'Fresnel Skin Factor': 0}
DENY = set()

def properties(body):
    out = {}
    for i in range(len(body) - 23):
        kind = body[i + 10:i + 12]
        if body[i + 4:i + 10] == bytes(6) and kind in (FLOAT, FLOAT3):
            v = struct.unpack_from('<f', body, i + 12)[0] if kind == FLOAT else struct.unpack_from('<3f', body, i + 12)
            out[struct.unpack_from('<I', body, i)[0]] = (i + 12, kind, v)
    return out

def with_values(body, values):
    body = bytearray(body)
    props = properties(bytes(body))
    for h, v in values.items():
        off, kind, _old = props[h]
        struct.pack_into('<f' if kind == FLOAT else '<3f', body, off, *(v if kind == FLOAT3 else (v,)))
    return bytes(body)

def channel_mean_luminance(maps, channel):
    w, h, d = dxt.decode(maps['Diffuse']['body'])
    if 'Mask' not in maps:
        return sum((0.299 * d[q] + 0.587 * d[q + 1] + 0.114 * d[q + 2] for q in range(0, len(d), 16))) / 255 / (len(d) // 16)
    mw, mh, m = dxt.decode(maps['Mask']['body'])
    num = den = 0.0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            q = (y * w + x) * 4
            wt = m[(y * mh // h * mw + x * mw // w) * 4 + channel] / 255
            num += wt * (0.299 * d[q] + 0.587 * d[q + 1] + 0.114 * d[q + 2]) / 255
            den += wt
    return num / den if den else 0.0

def ported_values(acr_body, ac3_body, maps, colour=False):
    acr, ac3 = (properties(acr_body), properties(ac3_body))
    out, lines = ({}, [])
    leave = set() if colour else LEAVE
    order = list(PROPERTY_NAMES) + sorted((set(acr) & set(ac3)) - set(PROPERTY_NAMES))
    for h in order:
        name = PROPERTY_NAMES.get(h, f'0x{h:08X}')
        if name in leave or h in DENY or h not in acr or (h not in ac3):
            continue
        if acr[h][1] != ac3[h][1]:
            continue
        v = acr[h][2]
        note = ''
        if name in SCALED:
            k = channel_mean_luminance(maps, SCALED[name])
            v, note = (v * k, f' (ACR {acr[h][2]:.3f} x mean diffuse {k:.3f})')
        out[h] = v
        fmt = (lambda x: f'{x:.3f}') if acr[h][1] == FLOAT else lambda x: str(tuple((round(c, 3) for c in x)))
        lines.append(f'{name}: {fmt(ac3[h][2])} -> {fmt(v)}{note}')
    return (out, lines)

def acr_mask_body(host_body, acr_mask_body):
    hd = dxt.header(host_body)
    assert hd['format'] == 5, hd
    w, h = (hd['width'], hd['height'])
    mw, mh, m = dxt.decode(acr_mask_body)
    rgba = []
    for y in range(h):
        for x in range(w):
            q = (y * mh // h * mw + x * mw // w) * 4
            rgba.append((m[q], m[q + 1], m[q + 2], 0))
    chain = dxt.mip_chain_dxt5(w, h, rgba, hd['mips'])
    assert len(chain) == hd['size'], (len(chain), hd['size'])
    body = host_body[:90] + chain + host_body[90 + hd['size']:]
    assert len(body) == len(host_body)
    return body

def acr_materials():
    from paths import ACR
    with Forge(os.path.join(ACR, 'DataPC.forge')) as f:
        rec = next((r for r in f.records if r.name == make_jester.DONOR_FILE))
        return {o['uid']: o for o in make_jester.objects_of(decompress(f.read(rec)))}

def part_payloads(archive, materials=True, masks=False, kits=None, read=None):
    import mask_gold
    kits = kits or make_jester.load_donor_kits()
    acr_objs = acr_materials()
    read = read or mask_gold.archive_reader(archive)
    out = []
    for fname, part in PARTS.items():
        index, original = read(fname)
        new_plain, lines = part_plain(decompress(original), kits[part], acr_objs, materials, masks)
        first = objects.directory_size(new_plain)
        assert first == first_chunk_raw_size(original)
        payload = compress(new_plain, first_chunk=first)
        assert decompress(payload) == new_plain
        out.append((index, payload, [f'{fname}: {x}' for x in lines]))
    return out

def part_plain(plain, kit, acr_objs, materials=True, masks=False, colour=False):
    objs = make_jester.objects_of(plain)
    uids = {o['uid'] for o in objs}
    (mpos, mat), = [(i, o) for i, o in enumerate(objs) if o['type'] == make_jester.MATERIAL]
    reps, lines = ({}, [])
    if materials:
        values, lines = ported_values(acr_objs[kit['material']]['body'], mat['body'], kit['maps'], colour)
        body = with_values(mat['body'], values)
        reps[mpos] = build_object(mat['type'], mat['body_size'], mat['name'], body)
        lines = [f"{mat['name']}: {x}" for x in lines]
    if masks:
        used = set(references(mat, uids))
        hits = [(i, o) for i, o in enumerate(objs) if o['type'] == make_jester.TEXTURE and o['uid'] in used and (make_jester.role_of(o['name']) == 'Mask')]
        acr_mask = kit['maps'].get('Mask')
        if hits and acr_mask and (hits[0][1]['body'][6:] == acr_mask['body'][6:]):
            lines.append(f"{hits[0][1]['name']}: already ACR's {acr_mask['name']} - left")
        elif hits and acr_mask:
            (tpos, tex), = hits
            body = acr_mask_body(tex['body'], acr_mask['body'])
            reps[tpos] = build_object(tex['type'], tex['body_size'], tex['name'], body)
            lines.append(f"{tex['name']}: ACR's {acr_mask['name']} RGB, alpha 0")
        else:
            lines.append('no mask texture on its material - left')
    new_plain = objects.rebuild(plain, reps)
    assert objects.directory_mismatches(new_plain) == []
    return (new_plain, lines)

def check(archive, kits=None):
    import mask_gold
    kits = kits or make_jester.load_donor_kits()
    acr_objs = acr_materials()
    read = mask_gold.archive_reader(archive)
    problems = []
    for fname, part in PARTS.items():
        _i, raw = read(fname)
        mat, = [o for o in make_jester.objects_of(decompress(raw)) if o['type'] == make_jester.MATERIAL]
        want, _lines = ported_values(acr_objs[kits[part]['material']]['body'], mat['body'], kits[part]['maps'])
        have = properties(mat['body'])
        for h, v in want.items():
            got = have[h][2]
            same = abs(got - v) < 1e-05 if isinstance(v, float) else all((abs(a - b) < 1e-05 for a, b in zip(got, v)))
            if not same:
                problems.append(f"{fname} {PROPERTY_NAMES.get(h, f'0x{h:08X}')}: {got}, ACR's port is {v}")
    return problems

def main():
    base = os.path.join(HERE, 'out', 'R5_name_cachefix', 'DataPC.forge')
    for _i, _p, lines in part_payloads(base, materials=True, masks=True):
        print('\n'.join(lines))
if __name__ == '__main__':
    main()
