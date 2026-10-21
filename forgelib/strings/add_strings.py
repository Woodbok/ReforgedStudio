"""forgelib/strings/add_strings.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import datetime
import json
import re
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import locpack
import objects
import paths
from datafile import compress, decompress, first_chunk_raw_size
from forge import Forge
from forge_write import ArchiveImage, set_payload, build
from transplant import build_object
REGISTRY = os.path.join(HERE, 'ids.json')
ACR_LOC = os.path.join(paths.ACR, 'DataPC_localization.forge')

def load_registry():
    with open(REGISTRY, encoding='utf-8') as fh:
        return json.load(fh)

def save_registry(reg):
    with open(REGISTRY, 'w', encoding='utf-8') as fh:
        json.dump(reg, fh, indent=2, ensure_ascii=False)
        fh.write('\n')

def packages(archive):
    out = []
    with Forge(archive) as f:
        for r in f.records:
            if not r.name.startswith('LocalizationPackage_'):
                continue
            raw = f.read(r)
            d = decompress(raw)
            (off, size, _s), = objects.walk(d)
            t, name = objects.parse_object(d, off, size)
            body = bytes(d[off + 12 + len(name):off + size])
            n = struct.unpack_from('<I', body, 30)[0]
            out.append((r, raw, d, t, name, body, locpack.LocData(body[34:34 + n])))
    return out

def acr_texts(ids, donor=ACR_LOC):
    acr = {name: loc.strings() for _r, _raw, _d, _t, name, _b, loc in packages(donor)}
    eng = acr['LocalizationPackage_English']
    missing = [hex(i) for i in ids if i not in eng]
    if missing:
        raise SystemExit(f"not in ACR's English text: {missing}")
    return (acr, eng)

def caps_markup(text):
    return re.sub('\\[b\\](.*?)\\[/b\\]', lambda m: m.group(1).upper(), text, flags=re.S)

def main(argv):
    ap = argparse.ArgumentParser(description='Add or port localized strings into a DataPC.forge.')
    ap.add_argument('--base', default=paths.DEFAULT_DATAPC)
    ap.add_argument('--out', required=True, help='output folder (DataPC.forge is written into it)')
    ap.add_argument('--add', action='append', default=[], help='id=text, e.g. 0x5AE00000="SOUK"')
    ap.add_argument('--json', default=None, help='{"0x5AE10000": "text", ...}')
    ap.add_argument('--from-acr', nargs='*', default=[], help='ACR string ids to port under their own ids')
    ap.add_argument('--from-acr-as', action='append', default=[], metavar='ACR_ID=NEW_ID', help="ACR's text for ACR_ID (every language) under NEW_ID, an id of --owner's range: for ACR ids whose AC3 block has no room left")
    ap.add_argument('--owner', default=None, help="your thread's range in strings/ids.json (needed for --add, --from-acr-as)")
    ap.add_argument('--what', default='', help='what the new ids are for (recorded in ids.json)')
    ap.add_argument('--donor', default=ACR_LOC, help="the archive --from-acr / --from-acr-as read the text from (default ACR's localization; e.g. Brotherhood's multi/DataPC.forge.vanilla for its own text)")
    ap.add_argument('--caps-markup', action='store_true', help="the donor's [b]...[/b] emphasis as AC3's capitals (AC4's text), every language")
    ap.add_argument('--replace', action='store_true', help='allow overwriting ids AC3 already has')
    ap.add_argument('--no-register', action='store_true', help="don't record the ids in strings/ids.json")
    a = ap.parse_args(argv)
    reg = load_registry()
    new = {}
    if a.json:
        with open(a.json, encoding='utf-8') as fh:
            new.update({int(k, 0): v for k, v in json.load(fh).items()})
    for p in a.add:
        k, v = p.split('=', 1)
        new[int(k, 0)] = v
    ported = [int(x, 0) for x in a.from_acr]
    remap = {}
    for p in a.from_acr_as:
        k, v = p.split('=', 1)
        remap[int(v, 0)] = int(k, 0)
    if not new and (not ported) and (not remap):
        sys.exit('nothing to add: use --add, --json, --from-acr or --from-acr-as')
    if remap and a.owner not in reg['ranges']:
        sys.exit(f"--from-acr-as needs --owner, one of {sorted(reg['ranges'])} (strings/ids.json)")
    pk = packages(a.base)
    base_ids = set(pk[0][6].strings()) if pk else set()
    for _r, _raw, _d, _t, _n, _b, loc in pk[1:]:
        base_ids |= set(loc.strings())
    if new:
        if a.owner not in reg['ranges']:
            sys.exit(f"--owner must be one of {sorted(reg['ranges'])} (strings/ids.json)")
        lo, hi = (int(x, 0) for x in reg['ranges'][a.owner])
        for i, t in new.items():
            if not lo <= i <= hi and (not (a.replace and i in base_ids)):
                sys.exit(f"0x{i:X} is outside {a.owner}'s range 0x{lo:X}..0x{hi:X}")
            prev = reg['assigned'].get(f'0x{i:X}')
            if prev and (prev['owner'] != a.owner or prev['text'] != t):
                sys.exit(f"0x{i:X} is registered to {prev['owner']} as {prev['text']!r}")
    clash = [hex(i) for i in list(new) + ported + list(remap) if i in base_ids]
    if clash and (not a.replace):
        sys.exit(f"AC3 already has these ids (use --replace only if you mean to change AC3's text): {clash}")
    acr, acr_eng = acr_texts(ported + list(remap.values()), a.donor) if ported or remap else ({}, {})
    if a.caps_markup:
        acr = {name: {i: caps_markup(t) for i, t in texts.items()} for name, texts in acr.items()}
        acr_eng = acr['LocalizationPackage_English']
    if remap:
        lo, hi = (int(x, 0) for x in reg['ranges'][a.owner])
        for i, src in remap.items():
            if not lo <= i <= hi:
                sys.exit(f"0x{i:X} is outside {a.owner}'s range 0x{lo:X}..0x{hi:X}")
            prev = reg['assigned'].get(f'0x{i:X}')
            if prev and (prev['owner'] != a.owner or (prev['text'] != acr_eng[src] and caps_markup(prev['text']) != acr_eng[src])):
                sys.exit(f"0x{i:X} is registered to {prev['owner']} as {prev['text']!r}")
    image = ArchiveImage.read(a.base)
    report = []
    ok_all = True
    for r, raw, d, t, name, body, loc in pk:
        if not loc.blocks:
            report.append(f'{name}: empty package, left as it is')
            continue
        sets = dict(new)
        for i in ported:
            sets[i] = acr.get(name, {}).get(i, acr_eng[i])
        for i, src in remap.items():
            sets[i] = acr.get(name, {}).get(src, acr_eng[src])
        data = locpack.edit(loc, sets)
        new_body = locpack.with_data(body, data)
        plain = objects.rebuild(d, replacements={0: build_object(t, len(new_body) - 1, name, new_body)})
        assert first_chunk_raw_size(raw) == objects.directory_size(d), f'{name}: first chunk is not the directory'
        payload = compress(plain, first_chunk=objects.directory_size(plain))
        assert decompress(payload) == plain
        set_payload(image, r.index, payload)
        loc2 = locpack.LocData(data)
        old = loc.strings()
        bad_old = sum((1 for i, s in old.items() if i not in sets and loc2.lookup(i) != s))
        bad_new = [hex(i) for i, s in sets.items() if loc2.lookup(i) != s]
        ok_all &= not bad_old and (not bad_new)
        report.append(f'{name}: {len(old)} strings, {len(loc.data):,} -> {len(data):,} bytes; old strings changed {bad_old}; new ids wrong {bad_new}')
    os.makedirs(a.out, exist_ok=True)
    dest = os.path.join(a.out, 'DataPC.forge')
    with open(dest, 'wb') as fh:
        fh.write(build(image))
    bad = 0
    with Forge(dest) as f:
        for r in f.records:
            p = f.read(r)
            if len(p) >= 12 and p[4:8] == b'3\xaa\xfbW':
                try:
                    decompress(p)
                except Exception:
                    bad += 1
    report.append(f'verify: {len(pk)} packages, all old strings intact and all new ids decode {ok_all}; payloads that no longer decompress {bad}')
    if ok_all and (not bad) and (not a.no_register):
        today = datetime.date.today().isoformat()
        for i, s in new.items():
            reg['assigned'][f'0x{i:X}'] = {'owner': a.owner, 'text': s, 'what': a.what, 'added': today}
        for i in ported:
            reg['assigned'].setdefault(f'0x{i:X}', {'owner': 'acr-port', 'text': acr_eng[i], 'what': a.what, 'added': today})
        for i, src in remap.items():
            reg['assigned'][f'0x{i:X}'] = {'owner': a.owner, 'text': acr_eng[src], 'what': f"{a.what} (ACR 0x{src:X}'s text, every language)".strip(), 'added': today}
        save_registry(reg)
        report.append(f'registered {len(new) + len(ported) + len(remap)} ids in strings/ids.json')
    with open(os.path.join(a.out, 'strings_report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest}')
    print('\n'.join(report[-4:]))
if __name__ == '__main__':
    main(sys.argv[1:])
