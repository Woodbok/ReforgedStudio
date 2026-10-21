"""forgelib/persona.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import roster
from forge import Forge
from transplant import build_object
KIT, ANIM, CUST = (roster.KIT_TYPE, 1915110132, 366827312)
WAREHOUSE, CLASSPACK, INVENTORY = (3518431960, 2535734019, 1743342695)
UISTRING = 2175206493

class Uids:

    def __init__(self, plain, archive):
        self.plain = plain
        with Forge(archive) as f:
            self.taken = {r.uid for r in f.records}

    def after(self, donor_uid):
        import clone
        u = donor_uid + 1
        while u in self.taken or u in clone.RESERVED or struct.pack('<I', u) in self.plain:
            u += 1
        self.taken.add(u)
        return u

def u32(body, off):
    return struct.unpack_from('<I', body, off)[0]

def set_u32(body, off, value):
    out = bytearray(body)
    struct.pack_into('<I', out, off, value)
    return bytes(out)

def obj(o, name, body):
    return build_object(o['type'], len(body) - 1, name, body)

def node_entries(body):
    n = u32(body, 14)
    assert len(body) == 18 + n * 6 + 4, 'node body is count + entries + class id'
    return [u32(body, 20 + i * 6) for i in range(n)]

def node_append(body, uid):
    b = bytearray(body)
    n = u32(b, 14)
    end = 18 + n * 6
    assert len(b) == end + 4, 'node body is count + entries + class id'
    b[end:end] = b'\x01\x00' + struct.pack('<I', uid)
    struct.pack_into('<I', b, 14, n + 1)
    return bytes(b)

def is_item(o):
    return len(o['body']) >= 26 and u32(o['body'], 22) == UISTRING

def ui_string_sites(body):
    key, out, i = (struct.pack('<I', UISTRING), [], body.find(struct.pack('<I', UISTRING), 6))
    while i >= 0:
        out.append(i - 4)
        i = body.find(key, i + 4)
    return out

def refresh_strings(body, uids):
    own = u32(body, 2)
    for off in ui_string_sites(body):
        body = set_u32(body, off, uids.after(own))
    return body

def node_prepend(body, uid):
    b = bytearray(body)
    n = u32(b, 14)
    assert len(b) == 18 + n * 6 + 4, 'node body is count + entries + class id'
    b[18:18] = b'\x01\x00' + struct.pack('<I', uid)
    struct.pack_into('<I', b, 14, n + 1)
    return bytes(b)
KIT_REFS_AT = 22
SOUNDBANK = 488068217

def kit_bank_at(body):
    i = body.find(struct.pack('<I', SOUNDBANK), 100)
    return i - 4 if i >= 104 else None

def kit_bank_uid(body):
    at = kit_bank_at(body)
    return u32(body, at) if at is not None else 0

def with_kit_bank_uid(body, uid):
    assert not 4026531840 <= uid <= 4160749567, f'0x{uid:08X}: an unmapped handle id'
    return set_u32(body, kit_bank_at(body), uid)

def kit_refs_of(body):
    out, p = ([], KIT_REFS_AT)
    while p + 5 <= len(body) and body[p] in (0, 1):
        out.append((p + 1, u32(body, p + 1)))
        p += 5
    return out

def pack_entries(body):
    found = []
    for n in range(64):
        at = len(body) - 13 - 5 * n
        if at < 26:
            break
        if u32(body, at) == n and u32(body, len(body) - 9) == 0 and all((body[at + 4 + 5 * k] in (0, 1) for k in range(n))):
            found.append((at, [(at + 5 + 5 * k, u32(body, at + 5 + 5 * k)) for k in range(n)]))
    assert len(found) == 1, f'pack body: {len(found)} readings of its item list'
    return found[0]

def own_pack_items(body, item_map, drop):
    at, entries = pack_entries(body)
    out, changes = (bytearray(body), [])
    for off, u in reversed(entries):
        if u in item_map:
            struct.pack_into('<I', out, off, item_map[u])
            changes.append((u, item_map[u]))
        elif u in drop:
            del out[off - 1:off + 4]
            struct.pack_into('<I', out, at, u32(out, at) - 1)
            changes.append((u, None))
    out = bytes(out)
    assert pack_entries(out)[0] == at
    return (out, changes[::-1])

def clone_persona(objects, uids, donor_id, new_id, kit_refs=None, class_names=None, front=False, skip=None, first_index=None):
    by_name = {}
    for o in objects:
        by_name.setdefault(o['name'], []).append(o)
    pos = {id(o): i for i, o in enumerate(objects)}

    def one(name, type_id):
        hits = [o for o in by_name.get(name, []) if o['type'] == type_id]
        assert len(hits) == 1, (name, len(hits))
        return hits[0]
    donor_kit = one(f'CharacterBuildingKit_AC3MP_ID{donor_id}', KIT)
    donor_cust = one(f'AC3MP_ID{donor_id}', CUST)
    ro = next((o for o in objects if o['type'] == roster.ROSTER_TYPE))
    skins = one('Skins', WAREHOUSE)
    packs_node = one('UnlockableClassPack', WAREHOUSE)
    assert u32(donor_cust['body'], 82) == donor_id and u32(donor_kit['body'], 14) == donor_id
    for o in objects:
        assert not o['name'].startswith((f'Char{new_id}_', f'AC3MP_ID{new_id}', f'CharacterBuildingKit_AC3MP_ID{new_id}')), o['name']
        assert not (o['type'] == INVENTORY and o['name'].startswith(str(new_id))), o['name']
    kit_b = struct.pack('<I', donor_kit['uid'])
    cust_b = struct.pack('<I', donor_cust['uid'])
    additions, replacements, summary = ([], {}, {'donor': donor_id, 'id': new_id})
    node_bodies = {}
    used = {u32(o['body'], 14) for o in objects if is_item(o)}
    next_index = [1 + max(used)]
    if first_index is not None:
        next_index[0] = first_index
    summary['first_index'] = next_index[0]

    def item_index(body):
        assert next_index[0] not in used, f"unlock index {next_index[0]} is already the base's"
        body = set_u32(body, 14, next_index[0])
        used.add(next_index[0])
        next_index[0] += 1
        return body

    def append_to(node, uid):
        p = pos[id(node)]
        node_bodies[p] = node_append(node_bodies.get(p, node['body']), uid)
    kit_uid = uids.after(donor_kit['uid'])
    kb = set_u32(set_u32(donor_kit['body'], 2, kit_uid), 14, new_id)
    if kit_bank_at(kb) is not None:
        kb = with_kit_bank_uid(kb, uids.after(kit_uid))
    for old, new in (kit_refs or {}).items():
        ob, nb = (struct.pack('<I', old), struct.pack('<I', new))
        assert kb.count(ob) == 1, f'kit mentions 0x{old:08X} {kb.count(ob)} times'
        kb = kb.replace(ob, nb)
    additions.append(obj(donor_kit, f'CharacterBuildingKit_AC3MP_ID{new_id}', kb))
    summary['kit'] = kit_uid
    cust_uid = uids.after(donor_cust['uid'])
    cb = set_u32(donor_cust['body'], 2, cust_uid)
    for off in (18, 56):
        assert u32(cb, off + 4) == UISTRING, off
        cb = set_u32(cb, off, uids.after(cust_uid))
    cb = set_u32(item_index(cb), 82, new_id)
    additions.append(obj(donor_cust, f'AC3MP_ID{new_id}', cb))
    if front:
        p = pos[id(skins)]
        node_bodies[p] = node_prepend(node_bodies.get(p, skins['body']), cust_uid)
    else:
        append_to(skins, cust_uid)
    summary.update(customization=cust_uid, index=u32(cb, 14))
    packs = [o for o in objects if o['type'] == CLASSPACK and o['body'].endswith(cust_b)]
    assert len(packs) == 2, [p['name'] for p in packs]
    pack_at = []
    for p in packs:
        pu = uids.after(p['uid'])
        pb = set_u32(p['body'], 2, pu)
        for off in (18, 34):
            assert u32(pb, off + 4) == UISTRING, (p['name'], off)
            pb = set_u32(pb, off, uids.after(pu))
        pb = item_index(pb)
        pb = pb[:-4] + struct.pack('<I', cust_uid)
        prefix = p['name'].split('_', 1)[0]
        pack_at.append((len(additions), p, f'{prefix}_ID{new_id}', pb))
        additions.append(obj(p, f'{prefix}_ID{new_id}', pb))
        append_to(packs_node, pu)
    summary['packs'] = len(packs)
    nodes = {o['name']: o for o in objects if o['type'] == WAREHOUSE}
    per_class = {}
    item_map, cut_items = ({}, set())
    for o in objects:
        if not o['name'].startswith(f'Char{donor_id}_'):
            continue
        if skip and skip(o['name']):
            cut_items.add(o['uid'])
            continue
        nu = uids.after(o['uid'])
        item_map[o['uid']] = nu
        nb = set_u32(o['body'], 2, nu)
        assert is_item(o), o['name']
        if o['type'] == ANIM:
            assert nb.count(kit_b) == 1, o['name']
            nb = nb.replace(kit_b, struct.pack('<I', kit_uid))
        else:
            assert nb.count(kit_b) == 1 and ui_string_sites(nb)[:1] == [18], o['name']
            nb = nb.replace(kit_b, struct.pack('<I', kit_uid))
        nb = item_index(refresh_strings(nb, uids))
        additions.append(obj(o, o['name'].replace(f'Char{donor_id}_', f'Char{new_id}_'), nb))
        per_class.setdefault(o['type'], []).append(nu)
    for type_id, new_uids in per_class.items():
        cname = (class_names or {}).get(type_id)
        node = nodes.get(cname)
        assert node is not None, f'no WarehouseNode for class 0x{type_id:08X} ({cname})'
        assert u32(node['body'], len(node['body']) - 4) == type_id, cname
        for nu in new_uids:
            append_to(node, nu)
    summary['objects'] = {(class_names or {}).get(t, hex(t)): len(v) for t, v in per_class.items()}
    for at, p, name, pb in pack_at:
        pb, changes = own_pack_items(pb, item_map, cut_items)
        additions[at] = obj(p, name, pb)
        summary.setdefault('pack_items', []).append((name, changes))
    header, lists = roster.parse(ro['body'])
    lists[0] = [kit_uid] + lists[0] if front else lists[0] + [kit_uid]
    items = sorted((o for o in objects if o['type'] == INVENTORY and o['name'].startswith(str(donor_id))), key=lambda o: o['name'])
    assert len(items) == 7 or (donor_id >= 100 and items), [o['name'] for o in items]
    for o in items:
        ib = set_u32(o['body'], 2, uids.after(o['uid']))
        old_id = u32(ib, 10)
        assert str(old_id) == o['name']
        assert old_id // 10000 == donor_id or old_id // 1000 == donor_id, (old_id, donor_id)
        new_item = item_id(new_id, old_id % 10000)
        ib = set_u32(ib, 10, new_item)
        for off in (26, 30):
            if u32(ib, off) == donor_id:
                ib = set_u32(ib, off, new_id)
        additions.append(obj(o, str(new_item), ib))
        lists[1] = lists[1] + [u32(ib, 2)]
    summary['items'] = len(items)
    summary['last_index'] = next_index[0] - 1
    new_roster = roster.unparse(header, lists)
    roster.parse(new_roster)
    replacements[pos[id(ro)]] = roster.build(ro['name'], new_roster)
    for p, body in node_bodies.items():
        replacements[p] = obj(objects[p], objects[p]['name'], body)
    return (replacements, additions, summary)
PART_TABLE = 2700445916
ITEM_ID_LIMIT = 1 << 20

def item_id(persona_id, suffix):
    if suffix >= 1000:
        suffix %= 1000
    classic = persona_id * 10000 + suffix
    if classic < ITEM_ID_LIMIT:
        return classic
    assert suffix < 1000 and persona_id < 510, f'persona {persona_id} item {suffix:04d}: no compact id below 2^20'
    return persona_id * 1000 + suffix

def item_fields(body):
    if len(body) < 35 or len(body) != 35 + 4 * u32(body, 22):
        return None
    n = u32(body, 22)
    return (u32(body, 10), u32(body, 18), [u32(body, 26 + 4 * k) for k in range(n)], u32(body, 26 + 4 * n))

def is_his_item(o, new_id):
    if o['type'] != INVENTORY or len(o['body']) < 14:
        return False
    iid = u32(o['body'], 10)
    return o['name'] == str(iid) and new_id < 510 and (iid == item_id(new_id, iid % 10000))

def own_items(objects, new_id, donor_id, main_class=None):
    reps, lines = ({}, [])
    for i, o in enumerate(objects):
        f = item_fields(o['body']) if is_his_item(o, new_id) else None
        if not f or not f[2]:
            continue
        iid, cls, who, killer = f
        b = o['body']
        for k, p in enumerate(who):
            if p != new_id:
                b = set_u32(b, 26 + 4 * k, new_id)
        if killer == donor_id or (main_class is not None and cls == main_class and (killer != new_id)):
            b = set_u32(b, 26 + 4 * len(who), new_id)
        if b != o['body']:
            reps[i] = b
            got = item_fields(b)
            lines.append(f'{iid} (WB{cls:02d}):' + (f' personas {tuple(who)} -> {tuple(got[2])}' if got[2] != who else '') + (f' kill persona {killer} -> {got[3]}' if got[3] != killer else ''))
    return (reps, lines)

def foreign_items(objects, new_id, main_class=None):
    out = []
    for o in objects:
        f = item_fields(o['body']) if is_his_item(o, new_id) else None
        if not f or not f[2]:
            continue
        iid, cls, who, killer = f
        if set(who) != {new_id}:
            out.append(f'inventory item {iid} of persona {new_id} lists personas {tuple(who)} (persona.own_items)')
        if main_class is not None and cls == main_class and (killer != new_id):
            out.append(f'inventory item {iid} is his main class WB{cls:02d} with kill persona {killer}: his own kills never play with it (persona.own_items)')
    return out

def wide_item_ids(objects, new_id):
    out = []
    for o in objects:
        if o['type'] != INVENTORY or len(o['body']) < 30:
            continue
        iid, n = (u32(o['body'], 10), u32(o['body'], 22))
        allowed = [u32(o['body'], 26 + 4 * k) for k in range(min(n, 16))]
        if new_id in allowed and iid >= ITEM_ID_LIMIT:
            out.append(f"inventory item {iid} of persona {new_id} passes 2^20: the lineup's session copy of a loadout keeps 20 bits ({iid & ITEM_ID_LIMIT - 1}) and never validates his skin (persona.item_id)")
    return out
COMPOSITE_TARGETS = {33: 'emblem map', 38: 'generated face diffuse', 43: 'generated face normal'}

def composite_targets(kit_body):
    return {off: u32(kit_body, off) for off in COMPOSITE_TARGETS}

def shared_composite_targets(objects, new_id):
    kits = {o['name']: o['body'] for o in objects if o['type'] == KIT}
    mine = composite_targets(kits[f'CharacterBuildingKit_AC3MP_ID{new_id}'])
    out = []
    for name, body in kits.items():
        if name == f'CharacterBuildingKit_AC3MP_ID{new_id}' or len(body) < 47:
            continue
        for off, u in composite_targets(body).items():
            if u and u == mine[off]:
                out.append(f"kit {new_id}'s {COMPOSITE_TARGETS[off]} (+{off}) 0x{u:08X} is {name}'s too")
    return out

def shared_part_tables(archive, objects, new_id, donor_id):
    from datafile import decompress
    import clone
    kits = {o['name']: o['body'] for o in objects if o['type'] == KIT}
    new = kits[f'CharacterBuildingKit_AC3MP_ID{new_id}']
    donor = kits[f'CharacterBuildingKit_AC3MP_ID{donor_id}']

    def named(body):
        return {u32(body, p) for p in range(len(body) - 3)}
    return sorted((n for n, _i in shared_part_table_records(archive, objects, new_id, donor_id)))

def shared_part_table_records(archive, objects, new_id, donor_id):
    from datafile import decompress
    import clone
    kits = {o['name']: o['body'] for o in objects if o['type'] == KIT}
    new = kits[f'CharacterBuildingKit_AC3MP_ID{new_id}']
    donor = kits[f'CharacterBuildingKit_AC3MP_ID{donor_id}']

    def named(body):
        return {u32(body, p) for p in range(len(body) - 3)}
    both = named(new) & named(donor)
    out = []
    with Forge(archive) as f:
        for r in f.records:
            if f'ID{donor_id}' not in (r.name or ''):
                continue
            try:
                root = clone.objects_of(decompress(f.read(r)))[0]
            except Exception:
                continue
            if root['type'] == PART_TABLE and root['uid'] in both:
                out.append((r.name, r.index))
    return sorted(out)

def own_part_tables(image, archive, plain, new_id, donor_id, classes, seed, taken, log=print):
    import clone
    from datafile import decompress
    from objects import rebuild as rebuild_plain
    objs = clone.objects_of(plain)
    shared = shared_part_table_records(archive, objs, new_id, donor_id)
    if not shared:
        return (plain, {})
    with Forge(archive) as f:
        recs = {r.index: r for r in f.records}
        sources = {n: (i, decompress(f.read(recs[i]))) for n, i in shared}
    shared = [n for n, _i in shared]
    tag = f'_ID{donor_id}_'
    assert all((tag in n for n in shared)), shared
    rename = lambda n: n.replace(tag, f'_ID{new_id}_')
    from paths import AC3_SOURCE
    mapping, _kept, rep = clone.clone_files(image, sources, rename, os.path.join(AC3_SOURCE, 'DataPC.forge'), classes, seed=seed, extra_avoid=[plain], avoid_uids=taken, object_names=rename)
    kpos, kit = next(((i, o) for i, o in enumerate(objs) if o['name'] == f'CharacterBuildingKit_AC3MP_ID{new_id}'))
    kb, refs = (kit['body'], {})
    for name, (_i, p) in sources.items():
        named = [o['uid'] for o in clone.objects_of(p) if struct.pack('<I', o['uid']) in kb]
        assert len(named) == 1, f'{name}: the kit names {len(named)} of its objects'
        old = named[0]
        new = mapping.get(old)
        assert new and new != old, f"{name}: 0x{old:08X} kept the donor's uid (shared elsewhere?)"
        assert kb.count(struct.pack('<I', old)) == 1
        kb = kb.replace(struct.pack('<I', old), struct.pack('<I', new))
        refs[old] = new
        log(f'  {rename(name)}: 0x{old:08X} -> 0x{new:08X}')
    return (rebuild_plain(plain, {kpos: obj(kit, kit['name'], kb)}), refs)

def check_persona(objects, new_id, donor_id=None, kit_refs=None):
    problems = []
    by_name = {o['name']: o for o in objects}
    kit = by_name.get(f'CharacterBuildingKit_AC3MP_ID{new_id}')
    cust = by_name.get(f'AC3MP_ID{new_id}')
    if kit is None or cust is None:
        return [f'kit or customization for {new_id} missing']
    if u32(kit['body'], 14) != new_id:
        problems.append('kit persona id')
    if donor_id is not None:
        donor = by_name[f'CharacterBuildingKit_AC3MP_ID{donor_id}']['body']
        want = set_u32(set_u32(donor, 2, kit['uid']), 14, new_id)
        for old, new in (kit_refs or {}).items():
            want = want.replace(struct.pack('<I', old), struct.pack('<I', new))
        if kit_bank_at(want) is not None and kit_bank_at(want) == kit_bank_at(kit['body']):
            want = set_u32(want, kit_bank_at(want), kit_bank_uid(kit['body']))
        if kit['body'] != want:
            at = sorted({i - (i - 23) % 5 for i in range(min(len(want), len(kit['body']))) if want[i] != kit['body'][i]})
            problems.append("kit differs from the donor's beyond uid, id and kit_refs" + ''.join((f"; +{o}: 0x{u32(want, o):08X} -> 0x{u32(kit['body'], o):08X}" for o in at[:6])))
    if u32(cust['body'], len(cust['body']) - 6) != new_id:
        problems.append('customization persona id')
    index_count = {}
    for o in objects:
        if is_item(o):
            index_count[u32(o['body'], 14)] = index_count.get(u32(o['body'], 14), 0) + 1
    for o in objects:
        if is_item(o) and o['name'].startswith((f'AC3MP_ID{new_id}', f'Char{new_id}_', f'BasicPack_ID{new_id}', f'ClassPack_ID{new_id}')) and (index_count[u32(o['body'], 14)] != 1):
            problems.append(f"{o['name']}: item index {u32(o['body'], 14)} shared")
    subs = {}
    for o in objects:
        for off in (18, 34, 56):
            if len(o['body']) >= off + 8 and u32(o['body'], off + 4) == UISTRING:
                subs.setdefault(u32(o['body'], off), []).append(o['name'])
    for o in objects:
        if o['name'].startswith((f'AC3MP_ID{new_id}', f'Char{new_id}_')):
            for off in (18, 34, 56):
                if len(o['body']) >= off + 8 and u32(o['body'], off + 4) == UISTRING and (len(subs[u32(o['body'], off)]) > 1):
                    problems.append(f"{o['name']}: nested UIString uid at +{off} shared")
    ro = next((o for o in objects if o['type'] == roster.ROSTER_TYPE))
    _, lists = roster.parse(ro['body'])
    if kit['uid'] not in lists[0]:
        problems.append('kit not in roster')
    skins = next((o for o in objects if o['name'] == 'Skins' and o['type'] == WAREHOUSE))
    if cust['uid'] not in node_entries(skins['body']):
        problems.append('customization not in Skins')
    listed = set()
    for o in objects:
        if o['type'] == WAREHOUSE:
            try:
                listed.update(node_entries(o['body']))
            except AssertionError:
                pass
    for o in objects:
        if o['name'].startswith((f'Char{new_id}_', f'BasicPack_ID{new_id}', f'ClassPack_ID{new_id}')) and o['uid'] not in listed:
            problems.append(f"{o['name']} in no WarehouseNode")
        if o['type'] == INVENTORY and o['name'].startswith(str(new_id)) and (o['uid'] not in lists[1]):
            problems.append(f"item {o['name']} not in roster list 2")
    return problems
