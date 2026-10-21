"""forgelib/maps/port/oob_modes.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import os
import re
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for _p in (ROOT, MAPS, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import archive_fixes
import convert
import mapwalk
import spawn_layers
import xrefs
OUT = os.path.join(ROOT, 'out', 'maps')
CELL = re.compile('^Cell\\d+_DataBlock$')
_IDS = convert.class_ids()
OOB_COMP = struct.pack('<I', _IDS['OutOfBoundsComponent'])
SKIP = {'DataPC.forge', 'DataPC_extra.forge', 'DataPC_CoreFX.forge', 'DataPC_Fire.forge', 'DataPC_extraparams.forge'}
_KNOWN = None
OFFERED = ('ACFE_Training', 'ACFE_Wanted', 'ACFE_Manhunt', 'ACFE_Easy_Deathmatch', 'ACFE_Deathmatch', 'ACFE_Assassinate', 'ACFE_CTF_2', 'ACFE_Domination', 'ACFE_Escort', 'ACFE_Chest_Capture', 'ACFE_Corruption', 'ACFE_Hijack')

def known():
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = archive_fixes.layer_names('AC3')
    return _KNOWN

def mode_layers():
    return {u: n for u, n in known().items() if n.startswith('ACFE_')}

def list_n(p):
    return struct.unpack_from('<I', p.objects[0]['body'], 10)[0] if p.objects else 0

def scan(files):
    modes = mode_layers()
    by_name = {n: u for u, n in modes.items()}
    blocks = {}
    for rname, p in files.items():
        if rname.startswith('DataBlock_') and rname[10:] in by_name:
            blocks[rname[10:]] = (rname, list_n(p) > 0)
    areas = []
    for rname, p in files.items():
        sets = spawn_layers.filter_sets(p.filter_table) if p.filter_table else []
        listed = None
        for o in p.objects[1:]:
            if not o['has_uid'] or OOB_COMP not in o['body'] or xrefs.cls('AC3', o['type']) != 'Entity':
                continue
            if listed is None:
                b = p.objects[0]['body']
                listed = {struct.unpack_from('<I', b, k + 2)[0] for k in range(14, 14 + 6 * list_n(p), 6)}
            layers = sets[o['filter']] if o['filter'] is not None and o['filter'] < len(sets) else set()
            where = 'block' if rname.startswith('DataBlock_') and rname[10:] in by_name else 'cell' if CELL.match(rname) else 'other'
            areas.append(dict(rec=rname, name=o['name'], uid=o['uid'], layers=set(layers), filter=o['filter'], modes={modes[l] for l in layers if l in modes}, any_known=any((l in known() for l in layers)), where=where, listed=o['uid'] in listed))
    return (blocks, areas)

def judge(files):
    blocks, areas = scan(files)
    problems, rows = ([], [])
    named = {m for a in areas for m in a['modes']}
    for m, (rname, playable) in sorted(blocks.items()):
        if not playable or m not in OFFERED:
            continue
        own = [a for a in areas if a['rec'] == rname and (m in a['modes'] or not a['any_known'])]
        if m not in named and (not own):
            rows.append(f'{m}: none (no area of the map names it)')
            continue
        rows.append(f'{m}: ' + (', '.join(sorted({a['name'] for a in own})) or 'NONE'))
        if not own:
            problems.append(f'{m} has no out-of-bounds area of its own')
    for a in areas:
        if a['where'] == 'cell' and (not a['any_known']):
            problems.append(f"always on: {a['name']} {a['uid']:#x} in {a['rec']}")
        elif a['where'] == 'cell' and a['modes']:
            twin = sorted({b['rec'][10:] for b in areas if b['uid'] == a['uid'] and b['where'] == 'block'})
            rows.append(f"mode area in a cell: {a['name']} in {a['rec']} ({'/'.join(sorted(a['modes']))})" + (f", also in blocks {', '.join(twin)}" if twin else ''))
            if twin:
                problems.append(f"{a['name']} placed in {a['rec']} and in the blocks {', '.join(twin)}")
    if not any((p for _r, p in blocks.values())):
        rows.append('no playable mode block')
    return (not problems, problems, rows)

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

def check(path, label=None):
    ok, problems, rows = judge(load(path))
    print(f"{('PASS' if ok else 'FAIL')} {label or path}" + (': ' + '; '.join(problems) if problems else ''))
    for r in rows:
        print(f'     {r}')
    return ok

def release_maps(set_path):
    with open(set_path, encoding='utf-8') as fh:
        files = json.load(fh)['files']
    out = []
    for f in files:
        base = os.path.basename(f['dest'].replace('\\', '/'))
        if base.lower().endswith('.forge') and base not in SKIP:
            out.append(f['src'] if os.path.isabs(f['src']) else os.path.join(ROOT, f['src']))
    return out

def refs(body):
    out, k = ([], body.find(b'\x01', 10))
    while 0 <= k < len(body) - 5:
        if body[k + 1] in (0, 1, 2):
            out.append(struct.unpack_from('<I', body, k + 2)[0])
        k = body.find(b'\x01', k + 1)
    return out

def filter_index(table, layers):
    import mode_spawns
    sets = spawn_layers.filter_sets(table) if table else []
    i = next((i for i, s in enumerate(sets) if s == set(layers)), None)
    if i is not None:
        return (table, i)
    return mode_spawns.add_filter(table, sorted(layers))

def rewrite(p, drop, add, new_objs, table=None, refilter=None):
    refilter = refilter or {}
    from datafile import compress, decompress
    drop = set(drop or ())
    head = p.objects[0]
    b = head['body']
    n = struct.unpack_from('<I', b, 10)[0]
    end = 14 + 6 * n
    m = struct.unpack_from('<I', b, end)[0]
    entries = [b[k:k + 6] for k in range(14, end, 6)]
    kept = [e for e in entries if struct.unpack_from('<I', e, 2)[0] not in drop]
    plus = [b'\x01\x00' + struct.pack('<I', e['uid']) for e, _f in add]
    b = b[:10] + struct.pack('<I', len(kept) + len(plus)) + b''.join(kept + plus) + struct.pack('<I', m - (len(entries) - len(kept)) + len(plus)) + b[end + 4:]
    objs = [(head['serial'], mapwalk.object_bytes(head, b), head['filter'])]
    rest = [o for o in p.objects[1:] if not o['has_uid'] or o['uid'] not in drop]
    last = max((i for i, o in enumerate(rest) if xrefs.cls('AC3', o['type']) == 'Entity' and o['filter'] is not None), default=-1)
    if add and last < 0:
        objs += [(e['serial'], mapwalk.object_bytes(e), fi) for e, fi in add]
    for i, o in enumerate(rest):
        objs.append((o['serial'], mapwalk.object_bytes(o), refilter.get(o['uid'], o['filter']) if o['has_uid'] else o['filter']))
        if i == last:
            objs += [(e['serial'], mapwalk.object_bytes(e), fi) for e, fi in add]
    objs += [(o['serial'], mapwalk.object_bytes(o), None) for o in new_objs]
    d_ = mapwalk.rebuild(objs, p.filter_table if table is None else table)
    payload = compress(d_, first_chunk=mapwalk.first_chunk(d_))
    assert decompress(payload) == d_
    return payload

def closure(files, src, uid, target, root):
    order = [src] + sorted((r for r in files if r.startswith('DataBlock_'))) + sorted(files)
    defs = {}
    for r in order:
        if r == target:
            continue
        for o in files[r].objects[1:]:
            if o['has_uid']:
                defs.setdefault(o['uid'], o)
    in_target = {o['uid'] for o in files[target].objects if o['has_uid']}
    in_root = {o['uid']: o for o in files[root].objects if o['has_uid']} if root in files else {}
    need, seen, todo = ([], set(), [uid])
    while todo:
        u = todo.pop()
        if u in seen or u in in_target:
            continue
        seen.add(u)
        o = in_root.get(u) if u != uid else None
        if o is None:
            o = defs.get(u)
            if o is None:
                continue
            need.append(o)
        todo += [v for v in refs(o['body']) if v != u]
    return need

def root_cell(files):
    cells = [r for r in files if CELL.match(r)]
    return max(cells, key=lambda r: list_n(files[r])) if cells else None

def plan(files, root=None, extend=()):
    root = root or root_cell(files)
    blocks, areas = scan(files)
    playable = {m: r for m, (r, ok) in blocks.items() if ok}
    todo, report = ({}, [])

    def at(r):
        return todo.setdefault(r, {'drop': set(), 'add': [], 'objs': [], 'refilter': {}})
    named = {m for a in areas for m in a['modes']}
    want = [m for m in extend if m in playable and m not in named]
    if want:
        uids = sorted({a['uid'] for a in areas if a['modes'] and a['where'] in ('block', 'cell')})
        if len(uids) > 1:
            raise SystemExit(f"--extend-modes {', '.join(want)}: the map has {len(uids)} mode areas ({', '.join((hex(u) for u in uids))}) - which one serves them is a choice, not a rule")
        if not uids:
            report.append(f"extend: no mode area to serve {', '.join(want)} (walled in) - left as it is")
        else:
            by_name = {n: u for u, n in mode_layers().items()}
            extra = {by_name[m] for m in want}
            for a in areas:
                if a['uid'] == uids[0]:
                    a['layers'] = a['layers'] | extra
                    a['modes'] = a['modes'] | set(want)
                    at(a['rec'])['refilter'][a['uid']] = a['layers']
                    report.append(f"extend: {a['name']} {a['uid']:#x} in {a['rec']} serves {', '.join(want)} too")
    by_uid = {}
    for a in areas:
        if a['modes'] and a['where'] in ('block', 'cell'):
            by_uid.setdefault(a['uid'], []).append(a)
    for uid, copies in sorted(by_uid.items()):
        src = min(copies, key=lambda a: (a['where'] != 'block', -len(a['modes']), a['rec']))
        sp = files[src['rec']]
        ent = next((o for o in sp.objects if o['has_uid'] and o['uid'] == uid))
        for m in sorted(src['modes']):
            r = playable.get(m)
            if r is None or any((c['rec'] == r for c in copies)):
                continue
            if any((x['rec'] == r and (m in x['modes'] or not x['any_known']) for x in areas)):
                continue
            have = {o['uid'] for o in at(r)['objs']}
            need = [o for o in closure(files, src['rec'], uid, r, root) if o['uid'] not in have]
            b_ = sp.objects[0]['body']
            placed = {struct.unpack_from('<I', b_, k + 2)[0] for k in range(14, 14 + 6 * list_n(sp), 6)}
            if any((o['uid'] in placed for o in need if o['uid'] != uid)):
                raise SystemExit(f"{src['name']}: names a placed entity of {src['rec']}; not copied")
            at(r)['add'].append((ent, src['layers']))
            at(r)['objs'] += [o for o in need if o['uid'] != uid]
            copies.append(dict(src, rec=r, where='block'))
            report.append(f"{src['name']} {uid:#x} -> {r} (from {src['rec']}" + (', with ' + ', '.join((f"{o['name'] or hex(o['uid'])} ({xrefs.cls('AC3', o['type'])})" for o in need if o['uid'] != uid)) if len(need) > 1 else '') + ')')
    for uid, copies in sorted(by_uid.items()):
        cells = [c for c in copies if c['where'] == 'cell']
        if not cells:
            continue
        named = {m for c in copies for m in c['modes']} & set(playable)
        if not all((any((c['rec'] == playable[m] for c in copies)) for m in named)):
            continue
        for c in cells:
            at(c['rec'])['drop'].add(uid)
            report.append(f"{c['name']} {uid:#x} out of {c['rec']} (in the blocks of {', '.join(sorted(named)) or 'no playable mode'})")
    for a in areas:
        if a['where'] == 'cell' and (not a['any_known']):
            at(a['rec'])['drop'].add(a['uid'])
            report.append(f"always on: {a['name']} {a['uid']:#x} out of {a['rec']} (layers {', '.join((known().get(l, hex(l)) for l in sorted(a['layers']))) or 'none'})")
    return (todo, report)

def apply(files, todo):
    report = []
    dropped = {u for t in todo.values() for u in t['drop']}
    if dropped:
        pats = {u: struct.pack('<I', u) for u in dropped}
        for rname, p in files.items():
            for o in p.objects[1:]:
                if o['has_uid'] and o['uid'] in dropped:
                    continue
                for u, pat in pats.items():
                    if pat in o['body'] and u in refs(o['body']):
                        raise SystemExit(f"{rname} / {o['name'] or hex(o['uid'])} names the out-of-bounds entity {u:#x}: not dropped")
    out = {}
    for rname, t in todo.items():
        p = files[rname]
        drop = set(t['drop'])
        if drop:
            local = {o['uid']: o for o in p.objects[1:] if o['has_uid']}
            cand, stack = (set(), [u for u in drop])
            while stack:
                u = stack.pop()
                for v in refs(local[u]['body']) if u in local else []:
                    if v in local and v not in drop and (v not in cand):
                        cand.add(v)
                        stack.append(v)
            for v in sorted(cand):
                pat = struct.pack('<I', v)
                used = any((pat in o['body'] and v in refs(o['body']) for q in files.values() for o in q.objects if not (q is p and o['has_uid'] and (o['uid'] in drop or o['uid'] in cand)) and (not (o['has_uid'] and o['uid'] == v))))
                if not used:
                    drop.add(v)
            extra = sorted(drop - set(t['drop']))
            if extra:
                report.append(f'{rname}: with them ' + ', '.join((f"{local[v]['name'] or hex(v)} ({xrefs.cls('AC3', local[v]['type'])})" for v in extra)))
        table = p.filter_table
        refilter = {}
        for uid, layers in t['refilter'].items():
            table, refilter[uid] = filter_index(table, layers)
        add = []
        for ent, layers in t['add']:
            table, fi = filter_index(table, layers)
            add.append((ent, fi))
        out[rname] = rewrite(p, drop, add, t['objs'], None if table is p.filter_table else table, refilter)
    return (out, report)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('archives', nargs='*')
    ap.add_argument('--check', action='store_true', help='read-only: judge the archives')
    ap.add_argument('--stock', action='store_true', help="with --check: AC3's own maps")
    ap.add_argument('--release', nargs='?', const=os.path.join(ROOT, 'release_set.json'), help="with --check: a release set's map archives")
    ap.add_argument('--src')
    ap.add_argument('--archive')
    ap.add_argument('--prefix')
    ap.add_argument('--library', help='the step: the always-loaded root cell (default: the cell with the longest list)')
    ap.add_argument('--dry-run', action='store_true', help='the step: print the plan, write nothing (--src may be a path)')
    ap.add_argument('--extend-modes', default='', metavar='MODE[,MODE]', help="the step: these mode layers (ACFE_Deathmatch, ...) join the filter of the map's one mode area when their block is playable and no area names them (step 0; the ACB maps)")
    a = ap.parse_args(argv)
    extend = [m for m in a.extend_modes.split(',') if m]
    if [m for m in extend if m not in OFFERED]:
        ap.error(f"--extend-modes: {extend} - each must be an offered mode layer ({', '.join(OFFERED)})")
    if a.check:
        import aa_records
        paths = list(a.archives)
        if a.stock:
            paths += [mapwalk.archive_path('AC3', s) for s in aa_records.STOCK]
        if a.release:
            paths += release_maps(a.release)
        bad = [p for p in paths if not check(p, os.path.relpath(p, ROOT) if p.startswith(ROOT) else p)]
        print(f'{len(paths) - len(bad)} of {len(paths)} PASS')
        if bad:
            raise SystemExit(1)
        return
    if a.dry_run:
        files = load(a.src if os.path.isfile(a.src) else os.path.join(OUT, a.src, a.archive))
        todo, report = plan(files, a.library, extend)
        _payloads, more = apply(files, todo)
        print(f'root cell (not copied from): {a.library or root_cell(files)}')
        print('\n'.join(('   ' + r for r in report + more)) or '   nothing to do')
        return
    if not (a.src and a.archive and a.prefix):
        ap.error('the step needs --src, --archive and --prefix (or --check)')
    from forge import Forge
    from forge_write import ArchiveImage, set_payload, write
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    files = load(src)
    todo, report = plan(files, a.library, extend)
    report.insert(0, f'root cell (not copied from): {a.library or root_cell(files)}')
    payloads, more = apply(files, todo)
    report += more
    index = {p.rec.name: p.rec.index for p in files.values()}
    for rname, pl in payloads.items():
        set_payload(image, index[rname], pl)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    after = load(dest)
    ok = True
    with Forge(dest) as f, Forge(src) as g:
        others = all((f.read(x) == g.read(y) for x, y in zip(f.records, g.records) if x.name not in payloads))
    ok &= others
    for rname, t in todo.items():
        q = after[rname]
        sets = spawn_layers.filter_sets(q.filter_table) if q.filter_table else []
        got = {o['uid']: o for o in q.objects if o['has_uid']}
        for ent, layers in t['add']:
            g_ = got.get(ent['uid'])
            good = g_ is not None and g_['body'] == ent['body'] and (sets[g_['filter']] == set(layers))
            ok &= good
        for o in t['objs']:
            ok &= o['uid'] in got and got[o['uid']]['body'] == o['body']
        before = {o['uid']: o for o in files[rname].objects if o['has_uid']}
        for uid, layers in t['refilter'].items():
            g_ = got.get(uid)
            ok &= g_ is not None and g_['body'] == before[uid]['body'] and (sets[g_['filter']] == set(layers))
    gone = {u for t in todo.values() for u in t['drop']} - {o['uid'] for p in after.values() for o in p.objects if o['has_uid']}
    dangling = sorted({hex(u) for p in after.values() for o in p.objects for u in gone if struct.pack('<I', u) in o['body'] and u in refs(o['body'])})
    ok &= not dangling
    passed, problems, rows = judge(after)
    ok &= passed
    report.append(f"names of a dropped object left: {dangling or 'none'}")
    report.append('check: ' + ('PASS' if passed else 'FAIL ' + '; '.join(problems)) + ' | ' + ' | '.join(rows))
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(('   ' + r for r in report)) if report else '   nothing to do')
    print(f"   verify: {sum((len(t['add']) for t in todo.values()))} copies body-identical under their filters, {sum((len(t['refilter']) for t in todo.values()))} kept in place under a wider filter, {sum((len(t['drop']) for t in todo.values()))} placements out, every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
