"""forgelib/maps/port/own_filters.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import os
import re
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for _p in (ROOT, MAPS, HERE, os.path.join(ROOT, 'modes')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import archive_fixes
import escort_check
import mapwalk
import xrefs
OUT = os.path.join(ROOT, 'out', 'maps')
CELL = re.compile('^Cell\\d+_DataBlock$')
SKIP = {'DataPC.forge', 'DataPC_extra.forge', 'DataPC_CoreFX.forge', 'DataPC_Fire.forge', 'DataPC_extraparams.forge'}
_MODES = None

def mode_layers():
    global _MODES
    if _MODES is None:
        _MODES = {u for u, n in archive_fixes.layer_names('AC3').items() if n.startswith('ACFE_')}
    return _MODES

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

def faults(files):
    out = []
    for rname, p in files.items():
        if not CELL.match(rname) or not p.filter_table:
            continue
        lists = escort_check.filter_lists(p.filter_table)
        for o in p.objects[1:]:
            if not o['has_uid'] or o['filter'] is None or xrefs.cls('AC3', o['type']) != 'Entity':
                continue
            d = lists[o['filter']] if o['filter'] < len(lists) else []
            if not mode_layers() & set(d):
                continue
            f = escort_check.own_filter(o['body'])
            if f is not None and f[2] != d:
                out.append((rname, o, d))
    return out

def check(path, label=None):
    bad = faults(load(path))
    by = {}
    for rname, o, _d in bad:
        by[rname] = by.get(rname, 0) + 1
    print(f"{('FAIL' if bad else 'PASS')} {label or path}" + (": own filter isn't the directory layers - " + ', '.join((f'{n} in {r}' for r, n in sorted(by.items()))) if bad else ''))
    return not bad

def release_maps(set_path):
    with open(set_path, encoding='utf-8') as fh:
        files = json.load(fh)['files']
    out = []
    for f in files:
        base = os.path.basename(f['dest'].replace('\\', '/'))
        if base.lower().endswith('.forge') and base not in SKIP:
            out.append(f['src'] if os.path.isabs(f['src']) else os.path.join(ROOT, f['src']))
    return out

def step(src, dest):
    from datafile import compress, decompress
    from forge import Forge
    from forge_write import ArchiveImage, set_payload, write
    files = load(src)
    bad = faults(files)
    fix = {}
    for rname, o, d in bad:
        fix.setdefault(rname, {})[o['uid']] = d
    image = ArchiveImage.read(src)
    for rname, todo in fix.items():
        p = files[rname]
        objs = []
        for o in p.objects:
            body = o['body']
            if o['has_uid'] and o['uid'] in todo and (o is not p.objects[0]):
                body = escort_check.set_own_filter(body, todo[o['uid']])
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        d_ = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d_, first_chunk=mapwalk.first_chunk(d_))
        assert decompress(payload) == d_
        set_payload(image, p.rec.index, payload)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    size = write(dest, image)
    after = load(dest)
    ok = not faults(after)
    with Forge(dest) as f, Forge(src) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.name not in fix))
    ok &= others
    for rname, todo in fix.items():
        old, new = (files[rname].objects, after[rname].objects)
        ok &= len(old) == len(new) and after[rname].filter_table == files[rname].filter_table
        for a, b in zip(old, new):
            ok &= (a['uid'] if a['has_uid'] else None) == (b['uid'] if b['has_uid'] else None) and a['filter'] == b['filter']
            if a['has_uid'] and a['uid'] in todo and (a is not old[0]):
                fa, fb = (escort_check.own_filter(a['body']), escort_check.own_filter(b['body']))
                ok &= fb is not None and fb[2] == todo[a['uid']] and (b['body'][:fb[0]] == a['body'][:fa[0]]) and (b['body'][fb[1]:] == a['body'][fa[1]:])
            else:
                ok &= a['body'] == b['body']
    names = archive_fixes.layer_names('AC3')
    sets = {}
    for _r, o, d in bad:
        k = ', '.join((names.get(u, hex(u)) for u in d))
        sets[k] = sets.get(k, 0) + 1
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    for rname in sorted(fix):
        print(f"   {rname}: {len(fix[rname])} entities' own filters written")
    for k, n in sorted(sets.items()):
        print(f'      {n:3d} x {k}')
    print(f"   verify: bodies changed only in their own filter, objects / order / directory filters kept, every other record byte-identical {others}, no fault left: {('OK' if ok else 'FAILED')}")
    return (len(bad), ok)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('archives', nargs='*')
    ap.add_argument('--check', action='store_true', help='read-only: judge the archives')
    ap.add_argument('--release', nargs='?', const=os.path.join(ROOT, 'release_set.json'), help="with --check: a release set's map archives")
    ap.add_argument('--src')
    ap.add_argument('--archive')
    ap.add_argument('--prefix')
    ap.add_argument('--in', dest='inp')
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    if a.check:
        paths = list(a.archives) + (release_maps(a.release) if a.release else [])
        bad = [p for p in paths if not check(p, os.path.relpath(p, ROOT) if p.startswith(ROOT) else p)]
        print(f'{len(paths) - len(bad)} of {len(paths)} PASS')
        if bad:
            raise SystemExit(1)
        return
    if a.inp and a.out:
        src, dest = (a.inp, a.out)
    elif a.src and a.archive and a.prefix:
        src = os.path.join(OUT, a.src, a.archive)
        dest = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1], a.archive)
    else:
        ap.error('the step needs --src, --archive and --prefix, or --in and --out (or --check)')
    _n, ok = step(src, dest)
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
