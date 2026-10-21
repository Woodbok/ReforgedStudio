"""forgelib/make_jester_79.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import glob
import os
import pickle
import re
import struct
import sys
import clone
import datafile
import forge_append
import exe_registry
import persona
from bootstrap_build import Bootstrap, OUT, ARCHIVE, read_back_objects
from datafile import decompress, compress
from forge import Forge
from forge_write import ArchiveImage, set_payload, build, add_file
from objects import rebuild, directory_size, directory_mismatches
from paths import AC3_SOURCE, ACR
import dxt
from tables import rows_of
from transplant import build_object
DONOR, NEW = (87, 79)
FILES = [f'AC3MP_ID{DONOR}_{n}' for n in ('MainPartTable', 'SecondaryPartTable', 'HeadTable', 'BeltTable', 'LegsTable', 'Winter', 'MainPart_V1', 'SecondaryPart_V1', 'Legs_v1', 'belt_V1', 'Hairs_V1', 'Masque_V2')]
WEAPON_TABLES = [f'AC3MP_ID{DONOR}_{n}' for n in ('WeaponAddonBasic', 'WeaponAddOnSilent')]
FACE_SHARED = [f'AC3MP_ID{DONOR}_Head_V1', f'AC3MP_Dynamic_Head_{DONOR}_MapDesc', f'AC3MP_Head_{DONOR}_MapDesc']
PRISTINE_EXTRA = os.path.join(AC3_SOURCE, 'DataPC_extra.forge')
PAYLOAD_MAGIC = bytes.fromhex('33aafb57')

def face_rename(name):
    return name.replace(f'ID{DONOR}', f'ID{NEW}J').replace(f'_{DONOR}_', f'_{NEW}J_')
face_unrename = {}

def face_closure(kit_body, head_table_plain):
    want = {u for _o, u in persona.kit_refs_of(kit_body)}
    for o in clone.objects_of(head_table_plain):
        for _s, _c, cells in rows_of(o['body']):
            want |= {c[3] for c in cells if c[3]}
    roots, plains, index = ({}, {}, {})
    with Forge(PRISTINE_EXTRA) as f:
        for r in f.records:
            if f'{DONOR}' not in (r.name or ''):
                continue
            raw = f.read(r)
            if raw[4:8] != PAYLOAD_MAGIC:
                continue
            d = decompress(raw)
            roots[clone.objects_of(d)[0]['uid']] = r.name
            plains[r.name] = d
            index[r.name] = r.index
    todo, seen = ([u for u in want if u in roots], set())
    while todo:
        u = todo.pop()
        if u in seen:
            continue
        seen.add(u)
        for o in clone.objects_of(plains[roots[u]]):
            todo += [x for x in roots if x != u and struct.pack('<I', x) in o['body']]
    names = sorted((roots[u] for u in seen))
    for n in names:
        face_unrename[face_rename(n)] = n
    return ({n: plains[n] for n in names}, {n: index[n] for n in names})
PORTRAITS = ('AC3MP_Dynamic_Head_{}_MapDesc', 'AC3MP_Head_{}_MapDesc')
PORTRAIT_SOURCES = {'icon': ('acfe_head_id70_MapDesc', 'acfe_head_id70_Map', (22, 24, 76)), 'render': ('favoriteSkin_ID70_MapDesc', 'favoriteSkin_ID70_Map', (215, 100, 110))}
PORTRAIT_ART = 96
BACKDROP = ((70, 62, 54), (22, 20, 18))

def portrait_source(kind):
    fname, oname, crop = PORTRAIT_SOURCES[kind]
    with Forge(os.path.join(ACR, 'DataPC_extra.forge')) as f:
        r = next((r for r in f.records if r.name == fname))
        body = next((o for o in clone.objects_of(decompress(f.read(r))) if o['name'] == oname))['body']
    w, h, px = dxt.decode(body)
    return (w, h, b''.join((px[y * w * 4:(y + 1) * w * 4] for y in reversed(range(h)))), crop)

def portrait_art(source, out):
    w, _h, px, (x0, y0, size) = source
    t0, t1 = BACKDROP
    flat = []
    for sy in range(size):
        bg = [a + (b - a) * sy / (size - 1) for a, b in zip(t0, t1)]
        for sx in range(size):
            i = ((y0 + sy) * w + x0 + sx) * 4
            a = px[i + 3] / 255
            flat.append([a * px[i + k] + (1 - a) * bg[k] for k in range(3)])
    art = []
    for oy in range(out):
        for ox in range(out):
            if out > size:
                fx = min(max((ox + 0.5) * size / out - 0.5, 0), size - 1)
                fy = min(max((oy + 0.5) * size / out - 0.5, 0), size - 1)
                x1, y1 = (int(fx), int(fy))
                x2, y2 = (min(x1 + 1, size - 1), min(y1 + 1, size - 1))
                dx, dy = (fx - x1, fy - y1)
                col = [(flat[y1 * size + x1][k] * (1 - dx) + flat[y1 * size + x2][k] * dx) * (1 - dy) + (flat[y2 * size + x1][k] * (1 - dx) + flat[y2 * size + x2][k] * dx) * dy for k in range(3)]
            else:
                sx0, sx1 = (int(ox * size / out), max(int(ox * size / out) + 1, int((ox + 1) * size / out)))
                sy0, sy1 = (int(oy * size / out), max(int(oy * size / out) + 1, int((oy + 1) * size / out)))
                cells = [flat[sy * size + sx] for sy in range(sy0, sy1) for sx in range(sx0, sx1)]
                col = [sum((c[k] for c in cells)) / len(cells) for k in range(3)]
            art.append(tuple((int(round(c)) for c in col)) + (255,))
    return art

def with_portrait(plain, source):
    objs = clone.objects_of(plain)
    pos, o = next(((i, o) for i, o in enumerate(objs) if o['name'].endswith('_Map')))
    body, hd = (o['body'], dxt.header(o['body']))
    w = hd['width']
    assert hd['format'] == 5 and hd['height'] == w, hd
    if w > PORTRAIT_ART:
        art = portrait_art(source, PORTRAIT_ART)
        canvas = [(0, 0, 0, 0)] * (w * w)
        for y in range(PORTRAIT_ART):
            canvas[y * w:y * w + PORTRAIT_ART] = art[y * PORTRAIT_ART:(y + 1) * PORTRAIT_ART]
    else:
        canvas = portrait_art(source, w)
    stored = [p for y in reversed(range(w)) for p in canvas[y * w:(y + 1) * w]]
    chain = dxt.mip_chain_dxt5(w, w, stored, hd['mips'])
    assert len(chain) == hd['size'], (len(chain), hd)
    new = body[:90] + chain + body[90 + hd['size']:]
    return rebuild(plain, {pos: build_object(o['type'], o['body_size'], o['name'], new)})
TAUNT_SOURCE = (651, 'AC3MP_ID87', 'ID70_WB01_G2G_Basic_Front_Run2All_KP2_TR_Taunt_ass')
TAUNT_CLIP = 1149651374
TAUNT_KEYS = (2567147248, 2567147263, 2567147278, 2567147253, 2567147268, 2567147283, 2567147258, 2567147273, 2567147288)
TAUNT_SLOT = dict(zip(('Free1', 'Free2', 'Free3', 'WB99_1', 'WB99_2', 'WB99_3', 'Cust1', 'Cust2', 'Cust3'), TAUNT_KEYS))
ONE_TAUNT_CUT = f'Char{DONOR}_Move_Taunt_0[23]'
ENTITY_FILES = ('Main_Player', 'Main_NPC')

def clip_id(body):
    n = struct.unpack_from('<I', body, 4)[0]
    return struct.unpack_from('<I', body, 9 + 12 * n)[0]

def taunt_clip(pristine):
    idx, fname, oname = TAUNT_SOURCE
    with Forge(pristine) as f:
        r = f.records[idx]
        assert r.name == fname, r.name
        o = next((o for o in clone.objects_of(decompress(f.read(r))) if o['name'] == oname))
    assert clip_id(o['body']) == TAUNT_CLIP
    return o

def own_animset(body, fresh, taunt=True, renew_entries=True, clip=TAUNT_CLIP, keys=TAUNT_KEYS, per_key=None):
    body = bytearray(body)
    n = struct.unpack_from('<I', body, 16)[0]
    assert len(body) == 20 + 20 * n, 'an AnimSet body is its entries and nothing else'
    new_uid = fresh.take()
    struct.pack_into('<I', body, 2, new_uid)
    done = []
    for i in range(n):
        at = 20 + 20 * i
        if renew_entries:
            struct.pack_into('<I', body, at, fresh.take())
        key = struct.unpack_from('<I', body, at + 10)[0]
        if key in keys and taunt:
            struct.pack_into('<I', body, at + 16, (per_key or {}).get(key, clip))
            done.append(key)
    assert not taunt or sorted(done) == sorted(keys), [hex(k) for k in done]
    return (bytes(body), new_uid)

def first_taunt_clip(body, key=None):
    key = TAUNT_KEYS[0] if key is None else key
    n = struct.unpack_from('<I', body, 16)[0]
    for i in range(n):
        if struct.unpack_from('<I', body, 20 + 20 * i + 10)[0] == key:
            return struct.unpack_from('<I', body, 20 + 20 * i + 16)[0]
    raise AssertionError('no Free_Taunt_01 slot')

def clip_with_id(body, new_id):
    old = clip_id(body)
    assert body.count(struct.pack('<I', old)) == 1
    n = struct.unpack_from('<I', body, 4)[0]
    body = bytearray(body)
    struct.pack_into('<I', body, 9 + 12 * n, new_id)
    return bytes(body)

def anim_body_size(body):
    return len(body) - (8 + 12 * struct.unpack_from('<I', body, 4)[0])
SOUND_TRIGGER = bytes.fromhex('e96c841d0000000000d04057fe00')

def acr_to_ac3_anim(body):
    c = 9 + 12 * struct.unpack_from('<I', body, 4)[0]
    b = body[:c + 23] + body[c + 24:]
    i = b.find(SOUND_TRIGGER, c)
    if i < 0:
        return b
    out, p = (bytearray(), 0)
    while i >= 0:
        cut = i + len(SOUND_TRIGGER) + 12
        out += b[p:cut] + bytes(4)
        p = cut
        i = b.find(SOUND_TRIGGER, cut)
    return bytes(out + b[p:])
ACR_TAUNT = ('Game Fix', 'ID99_Free_Taunt_Custom02_Male', 2567147263)

def acr_free_taunt02():
    fname, oname, cid = ACR_TAUNT
    with Forge(os.path.join(ACR, ARCHIVE)) as f:
        r = next((r for r in f.records if r.name == fname))
        src = next((o for o in clone.objects_of(decompress(f.read(r))) if o['name'] == oname))
        acr_kill = {o['name']: o['body'] for k in f.records if k.name == 'ACFE_ID70' for o in clone.objects_of(decompress(f.read(k)))}
    with Forge(os.path.join(AC3_SOURCE, ARCHIVE)) as f:
        ac3_kill = {o['name']: o['body'] for o in clone.objects_of(decompress(f.read(f.records[TAUNT_SOURCE[0]])))}
    probe = TAUNT_SOURCE[2]
    assert acr_to_ac3_anim(acr_kill[probe]) == ac3_kill[probe], 'ACR -> AC3 animation rule'
    body = acr_to_ac3_anim(src['body'])
    assert clip_id(body) == cid and len(body) == len(src['body']) - 1
    return {'type': src['type'], 'name': oname, 'body': body, 'body_size': anim_body_size(body)}

def all_directory_values(archive):
    cache = os.path.join(OUT, 'uid', 'dirvalues_AC3.pkl')
    if os.path.exists(cache):
        return pickle.load(open(cache, 'rb'))
    from objects import walk
    vals = set()
    with Forge(archive) as f:
        for r in f.records:
            raw = f.read(r)
            if raw[4:8] != PAYLOAD_MAGIC:
                continue
            try:
                vals |= {serial for _off, _size, serial in walk(decompress(raw))}
            except Exception:
                continue
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    pickle.dump(vals, open(cache, 'wb'))
    return vals

def entity_copies(pristine, classes, fresh, which=ENTITY_FILES, animset='taunt', renew_entries=True, taunt_from='kill', keys=TAUNT_KEYS, fresh_clip_keys=()):
    if taunt_from == 'acr-free02':
        clip = acr_free_taunt02()
    else:
        clip = taunt_clip(pristine)
    want_clip = clip_id(clip['body'])
    additions = [build_object(clip['type'], anim_body_size(clip['body']), clip['name'], clip['body'])] if animset == 'taunt' and (not taunt_from.startswith('87')) else []
    per_key = {}
    if fresh_clip_keys:
        assert animset == 'taunt' and (not taunt_from.startswith('87'))
        if set(keys) <= set(fresh_clip_keys):
            additions = []
        taken = all_directory_values(pristine)
        for key in fresh_clip_keys:
            new_id = fresh.take()
            while new_id in taken:
                new_id = fresh.take()
            body = clip_with_id(clip['body'], new_id)
            slot = next((n for n, k in TAUNT_SLOT.items() if k == key))
            additions.append(build_object(clip['type'], anim_body_size(body), f"{clip['name']}_{NEW}_{slot}", body))
            per_key[key] = new_id
    with Forge(pristine) as f:
        recs = {r.name: r for r in f.records}
        plains = {s: (recs[f'AC3MP_ID{DONOR}_{s}'].index, decompress(f.read(recs[f'AC3MP_ID{DONOR}_{s}']))) for s in which}
    first = clone.objects_of(plains[which[0]][1])
    aset = next((o for o in first if classes.get(o['type']) == 'AnimSet' and struct.pack('<I', o['uid']) in first[0]['body']))
    if animset == 'shared':
        aset_body, new_aset = (aset['body'], aset['uid'])
        mapping = {}
    else:
        value = want_clip if not taunt_from.startswith('87') else first_taunt_clip(aset['body'], TAUNT_SLOT['Free2'] if taunt_from == '87-free2' else None)
        aset_body, new_aset = own_animset(aset['body'], fresh, taunt=animset == 'taunt', renew_entries=renew_entries, clip=value, keys=keys, per_key=per_key)
        mapping = {aset['uid']: new_aset}
    out = {}
    ob_aset, nb_aset = (struct.pack('<I', aset['uid']), struct.pack('<I', new_aset))
    for s, (index, plain) in plains.items():
        objs = clone.objects_of(plain)
        eb = objs[0]
        assert classes.get(eb['type']) == 'EntityBuilder', eb['name']
        new_eb = fresh.take()
        mapping[eb['uid']] = new_eb
        ob_eb = struct.pack('<I', eb['uid'])
        assert eb['body'].count(ob_eb) >= 2 and eb['body'].count(ob_aset) == 1, s
        body = eb['body'].replace(ob_eb, struct.pack('<I', new_eb)).replace(ob_aset, nb_aset)
        repl = {0: build_object(eb['type'], eb['body_size'], eb['name'], body)}
        for pos, o in enumerate(objs):
            if o['body'][:2] == b'\x00\x01' and o['uid'] == aset['uid']:
                assert o['body'] == aset['body'], "87's AnimSet is one object in both files"
                if animset != 'shared':
                    repl[pos] = build_object(o['type'], o['body_size'], o['name'], aset_body)
            elif pos and (ob_eb in o['body'] or ob_aset in o['body']):
                raise AssertionError(f"{s}/{o['name']} names the EntityBuilder or AnimSet")
        assert len(repl) == (1 if animset == 'shared' else 2), s
        new = rebuild(plain, repl, additions=additions)
        assert directory_mismatches(new) == []
        new_objs = clone.objects_of(new)
        changed = [i for i, (a, b) in enumerate(zip(objs, new_objs)) if a['body'] != b['body']]
        assert changed == sorted(repl), (s, changed)
        assert len(new_objs) == len(objs) + len(additions), s
        if additions:
            added = new_objs[len(objs):]
            copies = added[len(added) - len(per_key):]
            if len(added) > len(per_key):
                assert added[0]['body'] == clip['body'] and clip_id(added[0]['body']) == want_clip
            assert all((o['body_size'] == anim_body_size(o['body']) for o in added))
            assert [clip_id(o['body']) for o in copies] == list(per_key.values())
            assert all((clip_with_id(o['body'], want_clip) == clip['body'] for o in copies))
        out[f'AC3MP_ID{NEW}_{s}'] = (index, new, new_eb)
    return (out, mapping)

def with_ui_text(body, texts):
    out, p = (bytearray(body[:18]), 18)
    for k in range(2):
        uid, cls, sid, n = struct.unpack_from('<IIII', body, p)
        assert cls == persona.UISTRING, (k, hex(cls))
        size = 16 + (2 * (n + 1) if n else 0)
        if k in texts:
            text = texts[k]
            out += struct.pack('<IIII', uid, cls, 0, len(text)) + text.encode('utf-16-le') + b'\x00\x00'
        else:
            out += body[p:p + size]
        p += size
    return bytes(out + body[p:])

def with_ui_ids(body, ids):
    body, p = (bytearray(body), 18)
    for k in range(2):
        uid, cls, sid, n = struct.unpack_from('<IIII', body, p)
        assert cls == persona.UISTRING, (k, hex(cls))
        if k in ids:
            struct.pack_into('<I', body, p + 8, ids[k])
        p += 16 + (2 * (n + 1) if n else 0)
    return bytes(body)
ONE_LOOK_CUT = f'Char{DONOR}_(Bodypart_\\w+_0[23]|Costume_(?!Empty$)\\w+|Color_0[23]|Face_\\w+|Warpaintcolor_\\d+)'

def rename(name):
    return name.replace(f'ID{DONOR}_', f'ID{NEW}_').replace(f'_{DONOR}_', f'_{NEW}_').replace(f'AC3MP_ID{DONOR}', f'AC3MP_ID{NEW}')

def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('build', nargs='?', default='G2c_jester79')
    p.add_argument('--base', default=os.path.join(OUT, 'E4_unlock_all', ARCHIVE))
    p.add_argument('--jester', default=os.path.join(OUT, 'F13_jester_all19', ARCHIVE))
    p.add_argument('--remap', choices=('all', 'heads', 'tables', 'none'), default='all', help="which of the kit's references follow the copies: all, only the two head descriptors, only the part tables, or none (the files are added either way; a bisect after G2/G2b crashed)")
    p.add_argument('--own-face', action='store_true', help="copy the face (render targets, generated maps, head sets) into DataPC_extra as 79's own instead of sharing ID87's (G2e)")
    p.add_argument('--keep-object-names', action='store_true', help='rename only the files, not the objects inside them (G2n)')
    p.add_argument('--pack', action='store_true', help="write every new or edited payload LZO2A-compressed, not stored (G2e's leak: stored payloads are 2.6x the size of the stock ones)")
    p.add_argument('--on-donor', action='store_true', help="add no persona: point ID87's own kit at the copies (a bisect after G2ez: is the leak in loading cloned files, or in persona 79?)")
    p.add_argument('--portrait', action='store_true', help="with --own-face: 79's character-select portraits become the Jester's, in the stock layout")
    p.add_argument('--portrait-from', choices=sorted(PORTRAIT_SOURCES), default='icon', help="icon: ACR's character-select picture (acfe_head_id70, default); render: a crop of his favourite-skin render")
    p.add_argument('--taunt', action='store_true', help="79 gets its own Main_Player/Main_NPC with the Jester's ACR taunt (his kill-taunt clip) on every taunt slot")
    p.add_argument('--stock-look', action='store_true', help="leave the F-series look (the Coyote Man's materials, the painted mask): only to reproduce releases 1-5")
    p.add_argument('--one-look', action='store_true', help='offer 79 no outfit, costume, colour or facial choices (ONE_LOOK_CUT)')
    p.add_argument('--one-taunt', action='store_true', help='offer 79 one taunt choice, not three (ONE_TAUNT_CUT: Move_Taunt_02/03 not cloned)')
    p.add_argument('--fresh-clip-slots', default='', help='of --taunt-slots: slots that get their own copy of his clip, under a new clip id')
    p.add_argument('--taunt-slots', default=','.join(TAUNT_SLOT), help='with --animset taunt: the slots his clip goes in, of ' + ','.join(TAUNT_SLOT))
    p.add_argument('--entity', choices=('both', 'player', 'npc'), default='both', help="with --taunt: which of 87's entity files 79 gets its own copy of")
    p.add_argument('--animset', choices=('taunt', 'plain', 'shared'), default='taunt', help="with --taunt: the copies' AnimSet - renewed with his taunt (default), renewed unchanged, or 87's own (only the EntityBuilder is new)")
    p.add_argument('--keep-entry-uids', action='store_true', help="with --animset taunt/plain: keep 87's AnimSet entry uids (only the AnimSet's own is new)")
    p.add_argument('--gamefix-stub', action='store_true', help="with --taunt-from acr-free02: also put the full clip in place of AC3's empty ID99_Free_Taunt_Custom02_Male in Game Fix")
    p.add_argument('--taunt-from', choices=('kill', '87', '87-free2', 'acr-free02'), default='kill', help="the taunt slots' clip: his ACR kill taunt (appended to the entity file), or 87's Free_Taunt_01 clip already in the file (a control)")
    p.add_argument('--name', help="79's name as the customization's inline text (string id 0)")
    p.add_argument('--description', help="79's description, the same way")
    p.add_argument('--name-id', type=lambda v: int(v, 0), help="string id for 79's name (the Jester's ACR one: 0x5276A 'THE JESTER'); add its text with strings/add_strings.py --from-acr")
    p.add_argument('--desc-id', type=lambda v: int(v, 0), help="string id for 79's description (ACR's: 0x4D260)")
    p.add_argument('--also', action='append', default=[], metavar='SUFFIX', help="copy AC3MP_ID87_<SUFFIX> as 79's own too, e.g. Custom_color1 (the colour table, which names the V1 materials by uid) or Costumes_Temp (names the turban file's root)")
    a = p.parse_args()
    datafile.PACK = a.pack
    also = [f'AC3MP_ID{DONOR}_{n}' for n in a.also]
    FILES.extend(also)
    FILES.extend((n for n in WEAPON_TABLES if n not in FILES))
    classes, _ = exe_registry.load(exe_registry.AC3)
    pristine = os.path.join(AC3_SOURCE, ARCHIVE)
    boot = Bootstrap(a.base)
    image = ArchiveImage.read(boot.archive)
    print(f'base {a.base}; Jester files from {a.jester}')
    with Forge(boot.archive) as f:
        index_of = {r.name: r.index for r in f.records}
    with Forge(a.jester) as f:
        recs = {r.name: r for r in f.records}
        sources = {n: (index_of[n], decompress(f.read(recs[n]))) for n in FILES}
    known = set()
    for pk in glob.glob(os.path.join(OUT, 'uid', 'AC3_*.pkl')):
        known |= set(pickle.load(open(pk, 'rb'))[0])
    donor_kit = next((o for o in boot.objects if o['name'] == f'CharacterBuildingKit_AC3MP_ID{DONOR}'))
    face_mapping, face_copies = ({}, {})
    if a.own_face:
        face_sources, face_index = face_closure(donor_kit['body'], sources[f'AC3MP_ID{DONOR}_HeadTable'][1])
        face_mapping, face_kept, face_copies = clone.clone_payloads(face_sources, face_rename, PRISTINE_EXTRA, classes, seed=NEW + 1000, extra_avoid=[boot.plain] + [p for _i, p in sources.values()], avoid_uids=known, keep_shared=False)
        for name, (plain, root, n, repl) in face_copies.items():
            print(f'  + DataPC_extra {name}: {n} objects, root 0x{root:08X}, {repl} references renewed')
        print(f'  face: {len(face_copies)} files, {len(face_mapping)} uids renewed; kept: {sorted(set(face_kept.values()))}')
        if a.portrait:
            src = portrait_source(a.portrait_from)
            for tmpl in PORTRAITS:
                name = tmpl.format(f'{NEW}J')
                plain, root, n, repl = face_copies[name]
                face_copies[name] = (with_portrait(plain, src), root, n, repl)
                tex = next((o for o in clone.objects_of(face_copies[name][0]) if o['name'].endswith('_Map')))
                w, h, rgba = dxt.decode(tex['body'])
                png = os.path.join(OUT, 'tex', 'portrait', f"{tex['name']}.png")
                os.makedirs(os.path.dirname(png), exist_ok=True)
                dxt.write_png(png, w, h, rgba, alpha=True)
                print(f'  portrait: {name} <- ACR {PORTRAIT_SOURCES[a.portrait_from][1]} crop {PORTRAIT_SOURCES[a.portrait_from][2]} ({w}x{h}) -> {png}')
        known |= set(face_mapping.values())
    elif a.portrait:
        p.error('--portrait needs --own-face (the portraits live in the face files)')
    mapping, kept, report = clone.clone_files(image, sources, rename, pristine, classes, seed=NEW, extra_avoid=[boot.plain], avoid_uids=known, object_names=None if a.keep_object_names else rename, external=face_mapping)
    for name, index, root, n, repl in report:
        print(f'  + #{index} {name}: {n} objects, root 0x{root:08X}, {repl} references renewed')
    print(f'  {len(mapping)} uids renewed; kept shared: {sorted(set((v for v in kept.values())))}')
    look_mask = None
    if not a.stock_look:
        import jester_materials
        import mask_gold
        copies = {name: index for name, index, _root, _n, _r in report}

        def read(name):
            return (copies[name], image.files[copies[name]].payload)
        change = mask_gold.bake(mask_gold.SHIPPED_LOOK)
        look_mask = change['diffuse']
        index, payload, lines = mask_gold.masque_payload(None, change, read=read)
        set_payload(image, index, payload)
        for index, payload, more in jester_materials.part_payloads(None, materials=True, read=read):
            set_payload(image, index, payload)
            lines += more
        print('\n'.join((f'  look: {x}' for x in lines)))
    taunt_refs = {}
    if a.taunt:
        fresh = clone.FreshUids(NEW + 2000, known | set(mapping.values()) | set(face_mapping.values()), [boot.plain])
        which = {'both': ENTITY_FILES, 'player': ENTITY_FILES[:1], 'npc': ENTITY_FILES[1:]}[a.entity]
        entity, taunt_refs = entity_copies(pristine, classes, fresh, which, a.animset, renew_entries=not a.keep_entry_uids, taunt_from=a.taunt_from, keys=tuple((TAUNT_SLOT[s] for s in a.taunt_slots.split(','))), fresh_clip_keys=tuple((TAUNT_SLOT[s] for s in a.fresh_clip_slots.split(',') if s)))
        with Forge(pristine) as f:
            rec_uids = clone.fresh_record_uids({r.uid for r in f.records}, NEW + 2001)
        for name, (template, plain, root) in entity.items():
            index = add_file(image, name, compress(plain, first_chunk=directory_size(plain)), next(rec_uids), template_index=template, entry_key=root)
            print(f"  + #{index} {name}: 87's byte for byte but its EntityBuilder (0x{root:08X}) and AnimSet")
        what = {'taunt': f'AnimSet slots {a.taunt_slots} -> ' + {'kill': f'clip 0x{TAUNT_CLIP:08X} (kill set, appended)', '87': "87's Free_Taunt_01 clip", '87-free2': "87's Free_Taunt_02 clip", 'acr-free02': f"ACR's {ACR_TAUNT[1]} (0x{ACR_TAUNT[2]:08X}, converted, appended)"}[a.taunt_from] + (", 87's entry uids kept" if a.keep_entry_uids else ''), 'plain': 'AnimSet renewed, entries unchanged', 'shared': "AnimSet stays 87's"}[a.animset]
        print(f"  taunt: {what}; renewed {', '.join((f'0x{o:08X}->0x{n:08X}' for o, n in taunt_refs.items()))}")
    if a.gamefix_stub:
        assert a.taunt_from == 'acr-free02', '--gamefix-stub goes with --taunt-from acr-free02'
        clip = acr_free_taunt02()
        with Forge(boot.archive) as f:
            r = next((r for r in f.records if r.name == ACR_TAUNT[0]))
            gf_index, gf_plain = (r.index, decompress(f.read(r)))
        gf_objs = clone.objects_of(gf_plain)
        pos = next((i for i, o in enumerate(gf_objs) if o['name'] == ACR_TAUNT[1]))
        assert len(gf_objs[pos]['body']) == 59 and clip_id(gf_objs[pos]['body']) == ACR_TAUNT[2], 'the stub'
        assert gf_objs[pos]['type'] == clip['type']
        new_gf = rebuild(gf_plain, {pos: build_object(clip['type'], clip['body_size'], clip['name'], clip['body'])})
        assert directory_mismatches(new_gf) == []
        set_payload(image, gf_index, compress(new_gf, first_chunk=directory_size(new_gf)))
        print(f"  Game Fix #{gf_index}: {ACR_TAUNT[1]} stub (59 B) -> ACR's clip ({len(clip['body'])} B)")
    kit_refs = {old: new for old, new in list(mapping.items()) + list(face_mapping.items()) + list(taunt_refs.items()) if struct.pack('<I', old) in donor_kit['body']}
    assert set(taunt_refs) <= set(kit_refs), "the kit names 87's AnimSet"
    heads = {o['uid'] for n in FILES if n.endswith('MapDesc') for o in clone.objects_of(sources[n][1])}
    if a.remap == 'heads':
        kit_refs = {o: n for o, n in kit_refs.items() if o in heads}
    elif a.remap == 'tables':
        kit_refs = {o: n for o, n in kit_refs.items() if o not in heads}
    elif a.remap == 'none':
        kit_refs = {}
    if a.on_donor:
        kb = donor_kit['body']
        for old, new in kit_refs.items():
            ob, nb = (struct.pack('<I', old), struct.pack('<I', new))
            assert kb.count(ob) == 1, f'kit mentions 0x{old:08X} {kb.count(ob)} times'
            kb = kb.replace(ob, nb)
        replacements = {boot.objects.index(donor_kit): build_object(donor_kit['type'], donor_kit['body_size'], donor_kit['name'], kb)}
        additions = []
        print(f"persona {DONOR}'s own kit: {len(kit_refs)} references remapped to the copies; no new persona")
    else:
        uids = persona.Uids(boot.plain, boot.archive)
        uids.taken |= set(mapping.values())
        cuts = ([ONE_LOOK_CUT] if a.one_look else []) + ([ONE_TAUNT_CUT] if a.one_taunt else [])
        skip = (lambda n: any((re.fullmatch(c, n) for c in cuts))) if cuts else None
        replacements, additions, summary = persona.clone_persona(boot.objects, uids, DONOR, NEW, kit_refs=kit_refs, class_names=classes, skip=skip)
        if cuts:
            print(f"  one look/taunt: not offered - {sum((1 for o in boot.objects if skip(o['name'])))} of 87's items; kept {summary['objects']}")
        print(f"persona {NEW}: kit 0x{summary['kit']:08X}, {len(kit_refs)} kit references remapped, items {summary['first_index']}..{summary['last_index']}")
    new_plain = rebuild(boot.plain, replacements, additions=additions)
    texts = {k: t for k, t in ((0, a.name), (1, a.description)) if t}
    ids = {k: v for k, v in ((0, a.name_id), (1, a.desc_id)) if v is not None}
    if texts or ids:
        objs = clone.objects_of(new_plain)
        pos, cust = next(((i, o) for i, o in enumerate(objs) if o['name'] == f'AC3MP_ID{NEW}' and classes.get(o['type']) == 'CharacterCustomization'))
        body = with_ui_ids(with_ui_text(cust['body'], texts), ids)
        texts = {**texts, **{k: f'string id 0x{v:X}' for k, v in ids.items()}}
        new_plain = rebuild(new_plain, {pos: build_object(cust['type'], len(body) - 1, cust['name'], body)})
        assert directory_mismatches(new_plain) == []
        print(f"  customization {cust['name']}: UIStrings {texts}")
    payload = compress(new_plain, first_chunk=directory_size(new_plain))
    assert decompress(payload) == new_plain
    set_payload(image, boot.index, payload)
    data = build(image)
    dest_dir = os.path.join(OUT, a.build)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, ARCHIVE)
    with open(dest, 'wb') as fh:
        fh.write(data)
    print(f'  {os.path.getsize(boot.archive):,} -> {len(data):,} bytes -> {dest}')
    extra_dest = None
    if face_copies:
        extra_dest = os.path.join(dest_dir, 'DataPC_extra.forge')
        with Forge(PRISTINE_EXTRA) as f:
            rec_uids = clone.fresh_record_uids({r.uid for r in f.records}, NEW + 1001)
        files = []
        for name, (plain, root, _n, _r) in face_copies.items():
            assert not directory_mismatches(plain), (name, directory_mismatches(plain)[:3])
            payload = compress(plain, first_chunk=directory_size(plain))
            assert decompress(payload) == plain
            files.append({'name': name, 'payload': payload, 'uid': next(rec_uids), 'key': root, 'template': face_index[face_unrename[name]]})
        forge_append.append_files(PRISTINE_EXTRA, extra_dest, files)
        print(f'  DataPC_extra: {len(files)} files appended -> {extra_dest}')
    problems = []
    _, objs = read_back_objects(dest)
    if a.on_donor:
        kit = next((o for o in objs if o['name'] == f'CharacterBuildingKit_AC3MP_ID{DONOR}'))
        kb = kit['body']
        problems += [f'kit 87 still points at 0x{o:08X}' for o in kit_refs if struct.pack('<I', o) in kb]
        problems += [f'kit 87 lacks 0x{n:08X}' for n in kit_refs.values() if struct.pack('<I', n) not in kb]
        if any((o['name'] == f'CharacterBuildingKit_AC3MP_ID{NEW}' for o in objs)):
            problems.append(f'persona {NEW} exists')
    else:
        problems += persona.check_persona(objs, NEW, DONOR, kit_refs)
        problems += [f"kit {NEW} still names {DONOR}'s part table {n} (the spawn hang)" for n in persona.shared_part_tables(dest, objs, NEW, DONOR)]
    if look_mask is not None:
        import jester_materials
        import mask_gold
        problems += [f'look: {x}' for x in jester_materials.check(dest)]
        problems += [f'look: {x}' for x in mask_gold.check(dest, look_mask)]
    with Forge(dest) as f, Forge(boot.archive) as base:
        recs = {r.name: r for r in f.records}
        img2 = ArchiveImage.read(dest)
        new_files = {}
        for name, index, root, _n, _r in report:
            r = recs[name]
            key = struct.unpack_from('<I', img2.files[r.index].entry, 8)[0]
            plain = decompress(f.read(r))
            first = clone.objects_of(plain)[0]['uid']
            if key != root or first != root:
                problems.append(f'{name}: entry key 0x{key:08X}, root 0x{first:08X}, expected 0x{root:08X}')
            new_files[name] = plain
        copies = {o['uid'] for p in new_files.values() for o in clone.objects_of(p)}
        stock = set()
        for r in base.records:
            if (r.name or '').startswith(f'AC3MP_ID{DONOR}_'):
                stock |= {o['uid'] for o in clone.objects_of(decompress(base.read(r)))}
        for name, plain in new_files.items():
            if name.endswith(('Table', 'Winter')) or name in {rename(n) for n in also + WEAPON_TABLES}:
                continue
            for o in clone.objects_of(plain):
                for u in stock - copies:
                    if u >= 16777216 and struct.pack('<I', u) in o['body'] and (u != o['uid']):
                        problems.append(f"{name}/{o['name']}: still points at ID87's 0x{u:08X}")
        unresolved = []
        for name, plain in new_files.items():
            if not name.endswith(('Table', 'Winter')):
                continue
            for o in clone.objects_of(plain):
                for _s, _c, cells in rows_of(o['body']):
                    for _p, col, _cls, ref in cells:
                        if ref and ref not in copies and (ref not in stock):
                            unresolved.append((name, col, ref))
        third = struct.unpack_from('<I', open(dest, 'rb').read(1021134 + 4), 1021134)[0]
        if third != len(f.records) - 1:
            problems.append(f'third table says {third}, archive holds {len(f.records)} files')
        keys = {struct.unpack_from('<I', sf.entry, 8)[0] for sf in img2.files}
        if extra_dest:
            with Forge(extra_dest) as fx:
                for r in fx.records:
                    raw = fx.read(r)
                    if raw[4:8] == PAYLOAD_MAGIC:
                        keys.add(clone.objects_of(decompress(raw))[0]['uid'])
                new_extra = {r.name for r in fx.records} & set(face_copies)
                if len(new_extra) != len(face_copies):
                    problems.append(f'DataPC_extra holds {len(new_extra)} of {len(face_copies)} face files')
        else:
            with Forge(PRISTINE_EXTRA) as fx:
                for r in fx.records:
                    raw = fx.read(r)
                    if raw[4:8] == PAYLOAD_MAGIC:
                        keys.add(clone.objects_of(decompress(raw))[0]['uid'])
        for name, col, ref in unresolved:
            if ref not in keys:
                problems.append(f'{name} col {col}: 0x{ref:08X} resolves nowhere')
        for u in kit_refs.values():
            if u not in keys and u not in copies and (u not in taunt_refs.values()):
                problems.append(f"redirected kit reference 0x{u:08X} is no file's root")
        for r in base.records:
            if (r.name or '').startswith(f'AC3MP_ID{DONOR}_') and f.read(recs[r.name]) != base.read(r):
                problems.append(f'{r.name} differs from the base')
        print(f"  read back: {len(f.records)} files, {len(new_files)} new; {('kit 87 on the copies' if a.on_donor else f'persona {NEW}')} {('complete' if not problems else '')}")
    for pr in problems[:12]:
        print('   PROBLEM', pr)
    print('OK' if not problems else 'PROBLEMS')
    return 1 if problems else 0
if __name__ == '__main__':
    sys.exit(main())
