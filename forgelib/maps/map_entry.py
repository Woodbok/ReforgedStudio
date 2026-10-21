"""forgelib/maps/map_entry.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import objects
import paths
from bootstrap_build import Bootstrap, read_back_objects
from persona import UISTRING, Uids, is_item, node_append, node_entries, obj, set_u32, u32
MPWORLD_DONOR, UNLOCK_DONOR = (3526959590, 3526959589)
WORLD_DONOR, WORLD_NEW = (3510158065, 2886628803)
PICTURES = {1609142200: 1486974815}
UID_BASE = 1523580928

def replace_u32(body, old, new):
    a, b = (struct.pack('<I', old), struct.pack('<I', new))
    return (body.replace(a, b), body.count(a))
TIME_AT = 15

def fresh_strings(body, uids, ids=(None, None)):
    cls = struct.pack('<I', UISTRING)
    k, n = (body.find(cls), 0)
    while k >= 4:
        body = set_u32(body, k - 4, uids.after(UID_BASE))
        if n < len(ids) and ids[n] is not None:
            body = set_u32(body, k + 4, ids[n])
        n += 1
        k = body.find(cls, k + 1)
    return (body, n)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=paths.DEFAULT_DATAPC, help="DataPC.forge to build on (default: the default set's, paths.DEFAULT_DATAPC; 'pristine' for the untouched game's)")
    ap.add_argument('--name', default='NE1_souk_entry', help='build folder under out/maps/')
    ap.add_argument('--name-id', type=lambda v: int(v, 0), default=None, help="string id for the name (both new objects' first UIString; default: Plantation's)")
    ap.add_argument('--desc-id', type=lambda v: int(v, 0), default=None, help="string id for the description (the second UIString; default: Plantation's)")
    ap.add_argument('--label', default='ACFE_Souk', help='suffix for the new objects\' names (not "Souk": AC3 already has an ACR-era MpWorld_Souk)')
    ap.add_argument('--hour', type=float, default=None, help="the hour the map starts at (MpWorld +15, the float after its World uid; default: the donor's, Plantation 12.0). ACR's Souk is 20.0 (dlcmaps/set_time.py found the field)")
    a = ap.parse_args(argv)
    if a.base == 'pristine':
        a.base = None
    boot = Bootstrap(a.base)
    uids = Uids(boot.plain, boot.archive)
    by_uid = {u32(o['body'], 2): o for o in boot.objects if len(o['body']) >= 6 and o['body'][:2] == b'\x00\x01'}
    mpw, unl = (by_uid[MPWORLD_DONOR], by_uid[UNLOCK_DONOR])
    maps = boot.one(name='Maps')
    report = [f"donors: {mpw['name']} ({len(mpw['body'])} B), {unl['name']} ({len(unl['body'])} B); Maps node {len(node_entries(maps['body']))} entries"]
    unl_type = unl['type']
    used = {u32(o['body'], 14) for o in boot.objects if o['type'] == unl_type or is_item(o)}
    index = max(used) + 1
    while index in used:
        index += 1
    new_mpw_uid = uids.after(UID_BASE)
    new_unl_uid = uids.after(UID_BASE)
    body = set_u32(mpw['body'], 2, new_mpw_uid)
    body, nw = replace_u32(body, WORLD_DONOR, WORLD_NEW)
    body, npic = replace_u32(body, *next(iter(PICTURES.items())))
    body, nstr = fresh_strings(body, uids, (a.name_id, a.desc_id))
    assert nw == 1 and npic >= 1 and (nstr == 2), (nw, npic, nstr)
    if a.hour is not None:
        assert u32(body, TIME_AT - 4) == WORLD_NEW, 'MpWorld layout: World uid not just before the hour'
        old_hour = struct.unpack_from('<f', body, TIME_AT)[0]
        body = body[:TIME_AT] + struct.pack('<f', a.hour) + body[TIME_AT + 4:]
        report.append(f'MpWorld_{a.label}: start hour {old_hour} -> {a.hour}')
    new_mpw = obj(mpw, f'MpWorld_{a.label}', body)
    report.append(f'MpWorld_{a.label}: uid 0x{new_mpw_uid:08X}, World 0x{WORLD_DONOR:08X} -> 0x{WORLD_NEW:08X}, {npic} picture refs -> Souk_MapDesc, {nstr} UIString uids fresh')
    body = set_u32(unl['body'], 2, new_unl_uid)
    body = set_u32(body, 14, index)
    body, nm = replace_u32(body, MPWORLD_DONOR, new_mpw_uid)
    body, npic2 = replace_u32(body, *next(iter(PICTURES.items())))
    body, nstr2 = fresh_strings(body, uids, (a.name_id, a.desc_id))
    assert nm == 1 and npic2 == 1 and (nstr2 == 2), (nm, npic2, nstr2)
    new_unl = obj(unl, f'Map_{a.label}', body)
    report.append(f"Map_{a.label}: uid 0x{new_unl_uid:08X}, unlock index {u32(unl['body'], 14)} -> {index}, MpWorld -> 0x{new_mpw_uid:08X}, picture -> Souk_MapDesc, {nstr2} UIString uids fresh")
    maps_body = node_append(maps['body'], new_unl_uid)
    new_maps = obj(maps, maps['name'], maps_body)
    plain = objects.rebuild(boot.plain, replacements={boot.position(maps): new_maps}, additions=[new_mpw, new_unl])
    dest = boot.emit(plain, os.path.join('maps', a.name))
    plain2, objs2 = read_back_objects(dest)
    by2 = {}
    for o in objs2:
        if len(o['body']) >= 6 and o['body'][:2] == b'\x00\x01':
            by2.setdefault(u32(o['body'], 2), []).append(o)
    m2 = next((o for o in objs2 if o['name'] == 'Maps'))
    strings = [u32(b, k - 4) for b in (new_mpw, new_unl) for k in range(4, len(b)) if b[k:k + 4] == struct.pack('<I', UISTRING)]
    count = lambda u: plain2.count(struct.pack('<I', u))
    exact = count(new_mpw_uid) == 3 and count(new_unl_uid) == 3 and all((count(u) == 1 for u in strings))
    mism = objects.directory_mismatches(plain2)
    names = [o['name'] for o in objs2]
    report.append(f"verify: MpWorld once {len(by2.get(new_mpw_uid, [])) == 1}, UnlockableMap once {len(by2.get(new_unl_uid, [])) == 1}; Maps node {len(node_entries(maps['body']))} -> {len(node_entries(m2['body']))} entries, lists the new map {new_unl_uid in node_entries(m2['body'])}; uid occurrences exact {exact} (object uids 3 = body + directory + one reference, {len(strings)} string uids 1 each); new names unique {names.count(f'MpWorld_{a.label}') == 1 and names.count(f'Map_{a.label}') == 1}; directory mismatches {len(mism)}")
    if a.name_id is not None or a.desc_id is not None:
        report.append(f'string ids: name {a.name_id and hex(a.name_id)}, description {a.desc_id and hex(a.desc_id)} (add them to the LocalizationPackages with strings/add_strings.py)')
    report.append(f'server: serve MpWorld_{a.label} = {new_mpw_uid} (0x{new_mpw_uid:08X}) in a playlist')
    out_dir = os.path.dirname(dest)
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
