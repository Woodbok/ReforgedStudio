"""forgelib/make_jester.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forge import Forge
from datafile import decompress, compress, first_chunk_raw_size
from objects import walk, rebuild
from transplant import object_at, references, transplant
from forge_write import ArchiveImage, set_payload, build
from paths import ACR, AC3_SOURCE as AC3
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
MESH, MATERIAL, TEXTURE = (1096652136, 2244483011, 2729961751)
DONOR_FILE = 'ACFE_ID70_Main_Player'
PART_MAP = {'Torso': 'MainPart_V1', 'Arms': 'SecondaryPart_V1', 'Legs': 'Legs_V1', 'Belt': 'Belt_V1', 'Head': 'Head_V1', 'Turban': 'Hairs?_V1'}
SLOT_RE = {part: re.compile(f'^{pat}$', re.I) for part, pat in PART_MAP.items()}
PERSONA_RE = re.compile('^AC3MP[ _]?_?ID(\\d+)_(.+)$')
LOD_RE = re.compile('_LOD(\\d)$')
ROLES = (('Mask', ('mask1map', 'rvbmask', 'rbvmask', 'rgbmask', 'rbgmask', 'rvb_mask', 'green_mask')), ('Diffuse', ('diffusemap',)), ('Normal', ('normalmap',)), ('Specular', ('specularmap',)))

def role_of(name):
    low = name.lower()
    for role, needles in ROLES:
        if any((n in low for n in needles)):
            return role
    return None

def objects_of(d):
    return [object_at(d, off, size) for off, size, _s in walk(d)]

def load_donor_kits():
    with Forge(os.path.join(ACR, 'DataPC.forge')) as f:
        rec = [r for r in f.records if r.name == DONOR_FILE][0]
        donor = decompress(f.read(rec))
    dobjs = objects_of(donor)
    dby_uid = {o['uid']: o for o in dobjs}
    dby_name = {o['name']: o for o in dobjs}
    duids = set(dby_uid)
    kits = {}
    for part in PART_MAP:
        lods = {}
        for lod in range(4):
            o = dby_name.get(f'ACFE_ID70_{part}_LOD{lod}')
            if o:
                lods[lod] = o
        if not lods:
            print(f'donor {part}: no meshes, skipped')
            continue
        mats = [u for u in references(lods[0], duids) if dby_uid[u]['type'] == MATERIAL]
        if len(mats) != 1:
            print(f'donor {part}: {len(mats)} materials, skipped')
            continue
        mat = dby_uid[mats[0]]
        maps = {}
        for u in references(mat, duids):
            o = dby_uid[u]
            if o['type'] == TEXTURE and role_of(o['name']):
                maps[role_of(o['name'])] = o
        kits[part] = {'lods': lods, 'material': mats[0], 'maps': maps}
        print(f"donor {part:7} {mat['name']:26} {len(lods)} LODs, maps {sorted(maps)}")
    print()
    return kits

def transplant_into(image, f, kits, only=()):
    done, skipped = ([], [])
    if True:
        only = set(only)
        slots = {}
        for rec in f.records:
            m = PERSONA_RE.match(rec.name or '')
            if not m:
                continue
            pid = int(m.group(1))
            if only and pid not in only:
                continue
            for part, rx in SLOT_RE.items():
                if rx.match(m.group(2)):
                    slots.setdefault(pid, {})[part] = rec
        for pid in sorted(slots):
            for part in PART_MAP:
                rec = slots[pid].get(part)
                if rec is None or part not in kits:
                    continue
                result = transplant_file(image, f, rec, kits[part])
                if isinstance(result, str):
                    skipped.append(f'{rec.name} ({result})')
                else:
                    done.append((pid, part, rec.name, result))
    return (done, skipped)

def transplant_file(image, f, rec, kit):
    original = f.read(rec)
    plain = decompress(original)
    objs = objects_of(plain)
    mats = [o for o in objs if o['type'] == MATERIAL]
    if len(mats) != 1:
        return f'{len(mats)} materials'
    host_mat = mats[0]
    host_uids = {o['uid'] for o in objs}
    host_maps = {}
    for u in references(host_mat, host_uids):
        o = next((x for x in objs if x['uid'] == u), None)
        if o is not None and o['type'] == TEXTURE and role_of(o['name']):
            host_maps.setdefault(role_of(o['name']), o)
    repl = {}
    for pos, obj in enumerate(objs):
        if obj['type'] == MESH:
            lm = LOD_RE.search(obj['name'])
            if lm and int(lm.group(1)) in kit['lods']:
                new, _ = transplant(kit['lods'][int(lm.group(1))], obj, {kit['material']: host_mat['uid']})
                repl[pos] = new
        elif obj['type'] == TEXTURE:
            r = role_of(obj['name'])
            if r and r in kit['maps'] and (host_maps.get(r) is obj):
                new, _ = transplant(kit['maps'][r], obj, {})
                repl[pos] = new
    if not repl:
        return 'nothing matched'
    new_plain = rebuild(plain, repl)
    set_payload(image, rec.index, compress(new_plain, first_chunk=first_chunk_raw_size(original)))
    return len(repl)

def main():
    kits = load_donor_kits()
    ac3_path = os.path.join(AC3, 'DataPC.forge')
    image = ArchiveImage.read(ac3_path)
    with Forge(ac3_path) as f:
        done, skipped = transplant_into(image, f, kits, {int(a) for a in sys.argv[1:] if a.isdigit()})
    by_persona = {}
    for pid, part, name, n in done:
        by_persona.setdefault(pid, []).append(part)
    for pid in sorted(by_persona):
        print(f"  ID{pid}: {', '.join(by_persona[pid])}")
    print(f'\n{len(done)} part files across {len(by_persona)} personas; {len(skipped)} skipped')
    for s in skipped[:6]:
        print(f'    skipped {s}')
    print('building (~1 GB)...')
    data = build(image)
    os.makedirs(OUT, exist_ok=True)
    dest = os.path.join(OUT, 'DataPC.forge')
    with open(dest, 'wb') as fh:
        fh.write(data)
    print(f'  {os.path.getsize(ac3_path):,} -> {len(data):,} bytes -> {dest}')
    print('\nverifying...')
    problems = []
    with Forge(dest) as f:
        for pid, part, name, n in done:
            rec = [r for r in f.records if r.name == name][0]
            objs = objects_of(decompress(f.read(rec)))
            uids = {o['uid'] for o in objs}
            mat = [o for o in objs if o['type'] == MATERIAL][0]
            for o in objs:
                if o['type'] == MESH and mat['uid'] not in references(o, uids):
                    problems.append(f"{name}/{o['name']}: lost its material")
            for u in references(mat, uids):
                if u not in uids:
                    problems.append(f'{name}: material has a dangling ref')
        print(f'  {len(done)} payloads: meshes keep their material')
        bad = 0
        for r in f.records:
            p = f.read(r)
            if len(p) >= 12 and p[4:8] == b'3\xaa\xfbW':
                try:
                    decompress(p)
                except Exception:
                    bad += 1
        print(f'  {len(f)} files, {bad} that no longer decompress')
        if bad:
            problems.append(f'{bad} payloads broken')
    print(f'\n{len(problems)} problems')
    for p in problems[:6]:
        print(f'   {p}')
    return 1 if problems else 0
if __name__ == '__main__':
    sys.exit(main())
