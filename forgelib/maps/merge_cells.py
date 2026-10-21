"""forgelib/maps/merge_cells.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import convert
import mapwalk
import spawn_layers
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
SKIP = ('RealTreeMesh',)
LIBRARY_CELLS = ('Cell01111_DataBlock', 'Cell01112_DataBlock', 'Cell01127_DataBlock', 'Cell01128_DataBlock', 'Cell01308_DataBlock')
INSTANCE_CLASSES = (None, 'Entity', 'EntityGroup', 'CrowdDutyRegion', 'WorldParticleData')

def filters(table):
    if not table:
        return (None, [])
    ids = convert.class_ids()
    assert table[4:8] == struct.pack('<I', ids['DataLayerFilterTable'])
    count = struct.unpack_from('<I', table, 8)[0]
    out, k = ([], 12)
    for _ in range(count):
        assert table[k + 4:k + 8] == struct.pack('<I', ids['DataLayerFilter'])
        n = struct.unpack_from('<I', table, k + 8)[0]
        end = k + 12 + 17 * n
        out.append(bytes(table[k:end]))
        k = end
    if k != len(table):
        raise ValueError(f'filter table: parsed {k} of {len(table)} bytes')
    return (bytes(table[:8]), out)

def table_bytes(head, fl):
    return head + struct.pack('<I', len(fl)) + b''.join(fl) if fl else b''

def cell_list(body):
    n = struct.unpack_from('<I', body, 10)[0]
    end = 14 + 6 * n
    return (n, [body[k:k + 6] for k in range(14, end, 6)], struct.unpack_from('<I', body, end)[0], end)

def merge(target, sources):
    head, tfl = filters(target.filter_table)
    n_filters = len(tfl)
    have = {o['uid'] for o in target.objects if o['has_uid']}
    t_n, t_entries, t_m, t_end = cell_list(target.objects[0]['body'])
    listed = {struct.unpack_from('<I', e, 2)[0] for e in t_entries}
    moved, skipped_same, skipped_diff = ([], 0, 0)
    first_body = {o['uid']: o['body'] for o in target.objects if o['has_uid']}
    new_entries, new_instances, filter_check = ([], [], [])
    skipped_class = Counter()
    for p in sources:
        _h, sfl = filters(p.filter_table)
        if head is None and sfl:
            head = _h
        for o in p.objects[1:]:
            if xrefs.cls('ACR', o['type']) in SKIP:
                skipped_class[xrefs.cls('ACR', o['type'])] += 1
                continue
            if o['has_uid'] and o['uid'] in have:
                if first_body.get(o['uid']) == o['body']:
                    skipped_same += 1
                else:
                    skipped_diff += 1
                continue
            filt = o['filter']
            if filt is not None:
                fb = sfl[filt]
                if fb not in tfl:
                    tfl.append(fb)
                filt = tfl.index(fb)
                filter_check.append((o['uid'], spawn_layers.filter_sets(p.filter_table)[o['filter']]))
            moved.append((o, filt))
            if o['has_uid']:
                have.add(o['uid'])
                first_body[o['uid']] = o['body']
        _n, entries, m_src, _end = cell_list(p.objects[0]['body'])
        for pos, e in enumerate(entries):
            u = struct.unpack_from('<I', e, 2)[0]
            if u not in listed:
                listed.add(u)
                (new_instances if pos < m_src else new_entries).append(e)
    skipped_uids = {o['uid'] for p in sources for o in p.objects[1:] if xrefs.cls('ACR', o['type']) in SKIP}
    new_instances = [e for e in new_instances if struct.unpack_from('<I', e, 2)[0] not in skipped_uids]
    new_entries = [e for e in new_entries if struct.unpack_from('<I', e, 2)[0] not in skipped_uids]
    gbody = target.objects[0]['body']
    gbody = gbody[:10] + struct.pack('<I', t_n + len(new_instances) + len(new_entries)) + b''.join(t_entries[:t_m]) + b''.join(new_instances) + b''.join(t_entries[t_m:]) + b''.join(new_entries) + struct.pack('<I', t_m + len(new_instances)) + gbody[t_end + 4:]
    objs = [(target.objects[0]['serial'], mapwalk.object_bytes(target.objects[0], gbody), target.objects[0]['filter'])]
    objs += [(o['serial'], mapwalk.object_bytes(o), o['filter']) for o in target.objects[1:]]
    objs += [(o['serial'], mapwalk.object_bytes(o), filt) for o, filt in moved]
    d = mapwalk.rebuild(objs, table_bytes(head, tfl))
    cls = Counter((xrefs.cls('ACR', o['type']) for o, _f in moved))
    info = f"{target.rec.name} <- {', '.join((p.rec.name[:9] for p in sources))}: +{len(moved)} objects ({', '.join((f'{k} {v}' for k, v in cls.most_common(6)))}); {skipped_same} identical duplicates skipped{(f', {skipped_diff} same-uid different bytes skipped' if skipped_diff else '')}; list +{len(new_instances)} instances (after its {t_m}), +{len(new_entries)} pieces; filters {n_filters} -> {len(tfl)}{(f'; not merged: {dict(skipped_class)}' if skipped_class else '')}"
    return (d, moved, filter_check, info)

def centroid(p):
    pts = []
    for o in p.objects:
        if xrefs.cls('ACR', o['type']) in ('Entity', 'EntityGroup'):
            m = struct.unpack_from('<16f', o['body'], 10)
            if abs(m[15] - 1) < 0.001 and (m[12] or m[13]):
                pts.append((m[12], m[13]))
    return (sum((x for x, _ in pts)) / len(pts), sum((y for _, y in pts)) / len(pts)) if pts else None

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N1_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--into', default='Cell01364_DataBlock', help='the always-loaded cell (the last grid node)')
    ap.add_argument('--prefix', default='N4')
    ap.add_argument('--outside-window', action='store_true', help="merge only the cells whose grid node is outside AC3's loading window")
    ap.add_argument('--neighbours', action='store_true', help='with --outside-window: merge each into the nearest ordinary cell inside the window, never into --into (N8b)')
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    byname = {p.rec.name: p for p in payloads}
    lib = byname[a.into]
    sources = [p for p in payloads if p.rec.name.startswith('Cell') and p is not lib]
    inside = []
    if a.outside_window:
        import relocate_cells
        gp_file, gp_i = next(((p, i) for p in payloads for i, o in enumerate(p.objects) if xrefs.cls('ACR', o['type']) == 'GridPartition'))
        nodes = relocate_cells.gp_nodes(gp_file.objects[gp_i]['body'])
        node_of = {k: i for i, k in enumerate(nodes) if k}
        window = set(relocate_cells.WINDOW)
        inside = [p for p in sources if node_of.get(p.objects[0]['uid']) in window]
        sources = [p for p in sources if node_of.get(p.objects[0]['uid']) not in window and p.rec.name not in LIBRARY_CELLS and (cell_list(p.objects[0]['body'])[2] > 0)]
    groups = {}
    if a.neighbours:
        spots = [(p, centroid(p)) for p in inside if cell_list(p.objects[0]['body'])[2] > 0]
        spots = [(p, c) for p, c in spots if c]
        for p in sources:
            c = centroid(p)
            if c is None:
                continue
            t = min(spots, key=lambda s: (s[1][0] - c[0]) ** 2 + (s[1][1] - c[1]) ** 2)[0]
            groups.setdefault(t.rec.name, []).append(p)
    else:
        groups[lib.rec.name] = sources
    report, all_moved, all_checks = ([], {}, {})
    for tname, srcs in groups.items():
        d, moved, checks, info = merge(byname[tname], srcs)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, byname[tname].rec.index, payload)
        all_moved[tname], all_checks[tname] = (moved, checks)
        report.append(info)
    merged_sources = [p for srcs in groups.values() for p in srcs]
    for p in merged_sources:
        g = p.objects[0]
        n, _e, _m, end = cell_list(g['body'])
        body = g['body'][:10] + struct.pack('<I', 0) + struct.pack('<I', 0) + g['body'][end + 4:]
        d = mapwalk.rebuild([(g['serial'], mapwalk.object_bytes(g, body), g['filter'])], b'')
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    written = ArchiveImage.read(dest)
    src_names = {p.rec.name for p in merged_sources}
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            if r.name in all_moved:
                count = Counter((o['uid'] for o in p.objects if o['has_uid']))
                v['uids defined twice in a target'] += sum((1 for c in count.values() if c > 1))
                bodies = {o['uid']: o for o in p.objects if o['has_uid']}
                v['moved objects'] += sum((1 for o, _f in all_moved[r.name] if o['has_uid']))
                v['moved objects present, identical'] += sum((1 for o, _f in all_moved[r.name] if o['has_uid'] and bodies.get(o['uid'], {}).get('body') == o['body']))
                sets = spawn_layers.filter_sets(p.filter_table)
                v['moved filtered objects'] += len(all_checks[r.name])
                v['moved filters with the same layers'] += sum((1 for u, s in all_checks[r.name] if sets[bodies[u]['filter']] == s))
                n, e, m, _end = cell_list(p.objects[0]['body'])
                kinds = {o['uid']: xrefs.cls('ACR', o['type']) for o in p.objects}
                v['target list heads that are pieces'] += sum((1 for x in e[:m] if kinds.get(struct.unpack_from('<I', x, 2)[0]) not in INSTANCE_CLASSES))
            elif r.name in src_names:
                n, _e, m, _end = cell_list(p.objects[0]['body'])
                v['source cells emptied'] += len(p.objects) == 1 and n == 0 and (m == 0)
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
