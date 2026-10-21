"""forgelib/maps/port/aa_records.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for _p in (ROOT, MAPS, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import entity
import mapwalk
import spawn_layers
import xrefs
CTF_LAYER = 3296798500
SWITCHED_LAYER = 1533087744
BLOCK = 'DataBlock_ACFE_CTF_2'
STOCK = ('DataPC_AC3MP_Plantation.forge', 'DataPC_AC3MP_Fort_Wolcott.forge', 'DataPC_AC3MP_Boston_Mill.forge', 'DataPC_AC3MP_Boston_Brewer.forge', 'DataPC_AC3MP_Boston_Harbou.forge', 'DataPC_AC3MP_Iceberg.forge', 'DataPC_AC3MP_Animus.forge')
JUDGED = ('flag', 'score', 'territory')
SKIP = {'DataPC.forge', 'DataPC_extra.forge', 'DataPC_CoreFX.forge', 'DataPC_Fire.forge', 'DataPC_extraparams.forge'}

def _kind(o):
    names = {n for _s, _e, n in entity.components(o['body']) or []}
    if 'FlagComponent' in names:
        return 'flag'
    if 'CTFScoreZoneComponent' in names:
        return 'score'
    if 'GameplayZoneComponent' in names:
        return 'territory'
    if 'CTFWallComponent' in names:
        return 'wall'
    if 'OutOfBoundsComponent' in names:
        return 'oob'
    k = spawn_layers.spawn_kind(o['body'])
    if k and k[0] == 2:
        return 'start'
    if k and k[0] == 0:
        return 'respawn'
    return None

def associations(body):
    n = struct.unpack_from('<I', body, 10)[0]
    return [struct.unpack_from('<I', body, 14 + 18 * i + 9) + struct.unpack_from('<I', body, 14 + 18 * i + 14) for i in range(n) if 14 + 18 * i + 18 <= len(body)]

def switched_uids(assoc):
    return {b for layer, b in assoc if layer == SWITCHED_LAYER}

def switched_records(files):
    uids = set()
    for p in files.values():
        for o in p.objects:
            if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager':
                uids |= switched_uids(associations(o['body']))
    return [r for r, p in files.items() if uids and r.startswith('DataBlock_') and p.objects and (p.objects[0]['uid'] in uids)]

def pieces(files, records=None):
    out, seen = ([], set())
    skip = set(switched_records(files)) if records is None else set()
    for rname, p in files.items():
        if rname in skip or (records is not None and rname not in records):
            continue
        sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
        for o in p.objects:
            if not o['has_uid'] or xrefs.cls('AC3', o['type']) != 'Entity' or o['uid'] in seen:
                continue
            layers = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else set()
            if CTF_LAYER not in layers:
                continue
            k = _kind(o)
            if k:
                seen.add(o['uid'])
                out.append((k, rname, o['name'], layers == {CTF_LAYER}, o['uid']))
    return out

def head_list(p):
    b = p.objects[0]['body']
    n = struct.unpack_from('<I', b, 10)[0]
    end = 14 + 6 * n
    listed = [struct.unpack_from('<I', b, k + 2)[0] for k in range(14, end, 6)]
    m, tail = struct.unpack_from('<II', b, end)
    return (n, listed, m, tail)

def block_problems(found, files, block=BLOCK, opened_for=CTF_LAYER):
    problems = []
    p = files.get(block)
    if p is None:
        return [f'no {block}']
    n, listed, m, tail = head_list(p)
    if tail != CTF_LAYER:
        problems.append(f"{block}'s head names layer {tail:#010x}, not ACFE_CTF_2 ({CTF_LAYER:#010x}: a placeholder's)")
    world = {u: i for i, u in enumerate(listed[:m])}
    unplaced = [nm for k, r, nm, _c, u in found if k in JUDGED and r == block and (u not in world)]
    if unplaced:
        problems.append(f"not in {block}'s world list (first {m} of {n} entries): {', '.join(unplaced)}")
    uid = p.objects[0]['uid']
    pat = struct.pack('<I', opened_for) + b'\x00' + struct.pack('<I', uid)
    managers = [o for q in files.values() for o in q.objects if xrefs.cls('AC3', o['type']) == 'WorldDataLayerManager']
    if managers and (not any((pat in o['body'] for o in managers))):
        layer = {CTF_LAYER: 'ACFE_CTF_2', SWITCHED_LAYER: 'ACFE_CTF_2_Switched'}.get(opened_for, f'{opened_for:#010x}')
        problems.append(f"the World's WorldDataLayerManager doesn't open {block} ({uid:#010x}) for {layer}")
    return problems

def judge(found, files=None, block=BLOCK, opened_for=CTF_LAYER):
    problems, notes = ([], [])
    counts = Counter((k for k, *_ in found))
    for k in JUDGED:
        if counts[k] != 2:
            problems.append(f'{counts[k]} {k} entities (want 2)')
    recs = sorted({r for k, r, *_ in found if k in JUDGED})
    one = recs[0] if len(recs) == 1 else None
    if len(recs) > 1:
        problems.append('split over ' + ', '.join((f"{r} ({'/'.join(sorted({k for k, rr, *_ in found if rr == r and k in JUDGED}))})" for r in recs)))
    loose = [n for k, _r, n, c, _u in found if k in JUDGED and (not c)]
    if loose:
        problems.append(f"not under an ACFE_CTF_2-only filter: {', '.join(loose)}")
    if counts['wall']:
        problems.append(f"{counts['wall']} CTFWallComponent (ACR's divider) left")
    if one and one != block:
        problems.append(f"in {one}, not {block} (a cell's pieces never joined the World: the ACB maps' AA crash)")
    if files is not None and one == block:
        problems += block_problems(found, files, block, opened_for)
    return (not problems, problems, notes, one)

def load(path):
    from forge import Forge
    files = {}
    with Forge(path) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            try:
                files[r.name] = mapwalk.Payload(r, f.read(r))
            except Exception:
                continue
    return files

def summary(found):
    rows = []
    for k in JUDGED + ('wall', 'oob', 'start', 'respawn'):
        c = Counter((r for kk, r, *_ in found if kk == k))
        if c:
            rows.append(f'{k}: ' + ', '.join((f"{r.replace('_DataBlock', '')}" + (f' x{n}' if n > 1 else '') for r, n in sorted(c.items()))))
    return '; '.join(rows)

def check_files(files, label=''):
    found = pieces(files)
    ok, problems, notes, one = judge(found, files)
    print(f"{('PASS' if ok else 'FAIL')} {label}: " + ('; '.join(problems + notes) or f'all in {one}'))
    print(f'     {summary(found)}')
    for rname in switched_records(files):
        sw = pieces(files, [rname])
        sw_ok, sw_problems, sw_notes, sw_one = judge(sw, files, rname, SWITCHED_LAYER)
        print(f"     Switched {('PASS' if sw_ok else 'FAIL')} {rname}: " + ('; '.join(sw_problems + sw_notes) or 'all in it'))
        print(f'       {summary(sw)}')
        ok = ok and sw_ok
    return ok

def check(path):
    return check_files(load(path), os.path.relpath(path, ROOT) if path.startswith(ROOT) else path)

def release_maps(set_path):
    with open(set_path, encoding='utf-8') as fh:
        files = json.load(fh)['files']
    out = []
    for f in files:
        base = os.path.basename(f['dest'].replace('\\', '/'))
        if base.lower().endswith('.forge') and base not in SKIP:
            out.append(f['src'] if os.path.isabs(f['src']) else os.path.join(ROOT, f['src']))
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('archives', nargs='*')
    ap.add_argument('--stock', action='store_true', help="AC3's own maps")
    ap.add_argument('--release', nargs='?', const=os.path.join(ROOT, 'release_set.json'), help="a release set's map archives")
    a = ap.parse_args(argv)
    paths = list(a.archives)
    if a.stock:
        paths += [mapwalk.archive_path('AC3', s) for s in STOCK]
    if a.release:
        paths += release_maps(a.release)
    bad = [p for p in paths if not check(p)]
    print(f'{len(paths) - len(bad)} of {len(paths)} PASS')
    if bad:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
