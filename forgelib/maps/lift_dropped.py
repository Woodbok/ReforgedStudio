"""forgelib/maps/lift_dropped.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import build_static
import convert
import flatten_groups
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from merge_cells import cell_list
OUT = os.path.join(ROOT, 'out', 'maps')
CROWDLIFE_UIDS = 1525743616
INSTANCE_CLASSES = ('Entity', 'EntityGroup', 'CrowdDutyRegion', 'WorldParticleData')

def children(body):
    ids = convert.class_ids()
    ent, bv = (struct.pack('<I', ids['Entity']), struct.pack('<I', ids['BoundingVolume']))
    starts = []
    k = body.find(ent, 20)
    while k > 0:
        if body[k - 6:k - 4] == b'\x00\x01':
            starts.append(k - 6)
        k = body.find(ent, k + 4)
    out = []
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else len(body)
        e = body.rfind(bv, s, nxt)
        if e <= s:
            continue
        end = e + flatten_groups.TAIL
        rest = body[end:nxt]
        plain = body[end - 1] == 3 and (end == nxt or (nxt == len(body) and rest == flatten_groups.EMPTY_LIST * (len(rest) // len(flatten_groups.EMPTY_LIST))))
        out.append((s, end, plain))
    return out

def group_raw(body, targets, unknown_in, defined=frozenset()):
    ids = convert.class_ids()
    parts = children(body)
    whole, _log = convert.convert(body, targets)
    child_uids = [struct.unpack_from('<I', body, s + 2)[0] for s, _e, _p in parts]
    if parts and (not unknown_in(whole)) and (not any((u in defined for u in child_uids))) and (len(children(whole)) == len(parts)):
        return (body, 'whole', len(parts), [])
    kept_raw, skipped = ([], [])
    for s, e, plain in parts:
        if not plain:
            skipped.append('stateful: its own GameStateData follows (not converted yet)')
            continue
        cbody, _log = convert.convert(body[s:e], targets)
        uid = struct.unpack_from('<I', cbody, 2)[0]
        bad = unknown_in(cbody)
        if bad or cbody[6:10] != struct.pack('<I', ids['Entity']) or uid in defined:
            skipped.append(', '.join(sorted(bad)) or 'already defined / not an Entity')
            continue
        kept_raw.append(body[s:e])
    if not kept_raw:
        return (None, 'none', 0, skipped)
    first, last = (parts[0][0], parts[-1][1])
    head, tail = (body[:first], body[last:])
    if struct.unpack_from('<I', head, len(head) - 4)[0] != len(parts) or unknown_in(head + tail):
        return (None, 'none', 0, skipped + [f'group head not understood ({len(kept_raw)} plain children left out)'])
    raw = head[:-4] + struct.pack('<I', len(kept_raw)) + b''.join(kept_raw) + tail
    gbody, _log = convert.convert(raw, targets)
    if unknown_in(gbody) or len(children(gbody)) != len(kept_raw):
        return (None, 'none', 0, skipped + [f"rebuilt group didn't convert cleanly ({len(kept_raw)} children left out)"])
    return (raw, 'rebuilt', len(kept_raw), skipped)

def anchor_index(before, after, present):
    for u in before:
        if u in present:
            return (present.index(u) + 1, u, 'after')
    for u in after:
        if u in present:
            return (present.index(u), u, 'before')
    return (None, None, None)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='N10_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--map', default='DataPC_ACFE_Souk.forge', help='the ACR archive the groups come from')
    ap.add_argument('--prefix', default='N11')
    ap.add_argument('--entities', action='append', default=[], help="instead of groups, lift dropped top-level Entities whose name starts with this, whole, when conversion leaves nothing AC3 can't read (N42: the blend groups)")
    ap.add_argument('--kind', default='Entity', help='with --entities: the top-level class to lift (Entity, CrowdDutyRegion, ...; N46)')
    ap.add_argument('--proven', action='append', default=[], metavar='CLASS', help="treat this changed class as proven the same in both exes (repeatable; stage 2 only). Ambient3dSoundComponent: reads as AC3's once rule 6 converts its TriggerZone (audio thread, A12); SoundActor: both readers read a counted u32 array and one value, and Souk's pigeon alarm matches Mill's field for field (G7)")
    ap.add_argument('--crowdlife', action='store_true', help="with --entities: rebuild each ACR blend group on AC3's GcLMPCrowdLife from an AC3 template group (crowdlife.py; N43)")
    ap.add_argument('--avoid-built', action='store_true', help="with --crowdlife: also avoid every crowd-life uid (0x5AF1xxxx) already in the build's bytes - the embedded objects of groups an earlier --crowdlife lift made are no top-level definitions, so a second lift handed the same uids out again (Palazzio's audience spots and static groups shared 567: PA10 crashed leaving a session, a GameplayCoordinatorComponent destroying a coordinator already freed)")
    ap.add_argument('--place-at', action='append', default=[], metavar='NAME_PREFIX', help="with --entities --crowdlife: place each rebuilt group where the nearest object of its ACR file whose name starts with this sits (within 4 m; each used once) - Palazzio's audience spots stand on the stages, their marker planes (AC2MP_Merchant_Orator_plane) on the floor in front, where ACR's crowd gathers")
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = {r.name: mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size}
    defined = {o['uid'] for p in payloads.values() for o in p.objects if o['has_uid']}
    unknown_in = build_static.unknown_checker(extra=a.proven)
    targets = convert.targets('ACR')
    ids = convert.class_ids()
    if a.crowdlife:
        import crowdlife
        import inventory
        cl_tmpl, cl_world = crowdlife.template()
        world_uid = next((o['uid'] for p in payloads.values() for o in p.objects if o['type'] == ids['World']))
        taken = set(inventory.universe('ACR')) | set(inventory.universe('AC3')) | defined
        if a.avoid_built:
            top = struct.pack('<H', CROWDLIFE_UIDS >> 16)
            for pl in payloads.values():
                k = pl.data.find(top, 2)
                while k >= 0:
                    u = struct.unpack_from('<I', pl.data, k - 2)[0]
                    if CROWDLIFE_UIDS <= u < CROWDLIFE_UIDS + 65536:
                        taken.add(u)
                    k = pl.data.find(top, k + 1)
        cl_next = [CROWDLIFE_UIDS]

        def cl_alloc():
            while cl_next[0] in taken:
                cl_next[0] += 1
            taken.add(cl_next[0])
            return cl_next[0]
    placed_at = set()
    markers = []
    if a.place_at:
        for mp in mapwalk.payloads('ACR', a.map):
            for k, m in enumerate(mp.objects):
                if m['has_uid'] and any(((m['name'] or '').startswith(x) for x in a.place_at)) and (xrefs.cls('ACR', m['type']) == 'Entity') and (len(m['body']) > 74):
                    markers.append((mp.rec.name, k, struct.unpack_from('<3f', m['body'], 58), m['body'][10:74], m['name']))
    add = {}
    report, counts = ([], Counter())
    for p in mapwalk.payloads('ACR', a.map):
        if p.rec.name not in payloads:
            continue
        a_m_uids = None
        for o in p.objects:
            if a.entities:
                if xrefs.cls('ACR', o['type']) != a.kind or o['uid'] in defined or (not any((o['name'].startswith(m) for m in a.entities))):
                    continue
                if a.crowdlife:
                    src_body = o['body']
                    if a.place_at:
                        here = struct.unpack_from('<3f', src_body, 58)
                        cands = sorted(((math.dist(here, mpos), j) for j, (mf, mk, mpos, _mx, _mn) in enumerate(markers) if (mf, mk) not in placed_at))
                        if not cands or cands[0][0] > 4.0:
                            report.append(f"{p.rec.name}: {o['name']}: no --place-at object within 4 m: not lifted")
                            continue
                        d, j = cands[0]
                        mf, mk, _mpos, mx, mn = markers[j]
                        placed_at.add((mf, mk))
                        src_body = src_body[:10] + mx + src_body[74:]
                        report.append(f"{p.rec.name}: {o['name']}: placed at {mn} ({mf}, {d:.2f} m)")
                    body, _map = crowdlife.build(src_body, cl_tmpl, cl_world, world_uid, cl_alloc)
                    counts['rebuilt on GcLMPCrowdLife'] += 1
                else:
                    body, _log = convert.convert(o['body'], targets)
                bad = unknown_in(body)
                counts['entities matched'] += 1
                if bad:
                    report.append(f"{p.rec.name}: {o['name']}: not lifted ({', '.join(sorted(bad))})")
                    continue
                defined.add(o['uid'])
                counts['entities lifted'] += 1
                kept = [(o['uid'], o['name'], body, o['filter'], o['body_size'] + len(body) - len(o['body']), o['type'])]
                if a_m_uids is None:
                    a_m_uids = []
                    if xrefs.cls('ACR', p.objects[0]['type']) == 'GridCellDataBlock':
                        _n, a_entries, a_m, _end = cell_list(p.objects[0]['body'])
                        a_m_uids = [struct.unpack_from('<I', e, 2)[0] for e in a_entries[:a_m]]
                if o['uid'] in a_m_uids:
                    pos = a_m_uids.index(o['uid'])
                    before, after = (list(reversed(a_m_uids[:pos])), a_m_uids[pos + 1:])
                else:
                    before, after = ([], [])
                add.setdefault(p.rec.name, []).append({'before': before, 'after': after, 'kept': kept})
                report.append(f"{p.rec.name}: {o['name']}: entity lifted whole")
                continue
            if xrefs.cls('ACR', o['type']) != 'EntityGroup' or o['uid'] in defined:
                continue
            parts = children(o['body'])
            whole, _log = convert.convert(o['body'], targets)
            child_uids = [struct.unpack_from('<I', o['body'], s + 2)[0] for s, _e, _p in parts]
            if parts and (not unknown_in(whole)) and (not any((u in defined for u in child_uids))) and (len(children(whole)) == len(parts)):
                defined.update(child_uids)
                defined.add(o['uid'])
                counts['groups'] += 1
                counts['groups taken whole'] += 1
                counts['children kept'] += len(parts)
                kept = [(o['uid'], o['name'], whole, o['filter'], o['body_size'] + len(whole) - len(o['body']), ids['EntityGroup'])]
                if a_m_uids is None:
                    a_m_uids = []
                    if xrefs.cls('ACR', p.objects[0]['type']) == 'GridCellDataBlock':
                        _n, a_entries, a_m, _end = cell_list(p.objects[0]['body'])
                        a_m_uids = [struct.unpack_from('<I', e, 2)[0] for e in a_entries[:a_m]]
                if o['uid'] in a_m_uids:
                    pos = a_m_uids.index(o['uid'])
                    before, after = (list(reversed(a_m_uids[:pos])), a_m_uids[pos + 1:])
                else:
                    before, after = ([], [])
                add.setdefault(p.rec.name, []).append({'before': before, 'after': after, 'kept': kept})
                report.append(f"{p.rec.name}: {o['name']}: taken whole, {len(parts)} children")
                continue
            kept_raw, skipped = ([], [])
            for i, (s, e, plain) in enumerate(parts):
                if not plain:
                    skipped.append('stateful: its own GameStateData follows (not converted yet)')
                    continue
                body, _log = convert.convert(o['body'][s:e], targets)
                uid = struct.unpack_from('<I', body, 2)[0]
                bad = unknown_in(body)
                if bad or body[6:10] != struct.pack('<I', ids['Entity']) or uid in defined:
                    skipped.append(', '.join(sorted(bad)) or 'already defined / not an Entity')
                    continue
                kept_raw.append(o['body'][s:e])
                defined.add(uid)
            counts['groups'] += 1
            kept = []
            if kept_raw:
                first, last = (parts[0][0], parts[-1][1])
                head, tail = (o['body'][:first], o['body'][last:])
                if struct.unpack_from('<I', head, len(head) - 4)[0] != len(parts) or unknown_in(head + tail):
                    skipped.append(f'group head not understood ({len(kept_raw)} plain children left out)')
                    kept_raw = []
                else:
                    raw = head[:-4] + struct.pack('<I', len(kept_raw)) + b''.join(kept_raw) + tail
                    gbody, _log = convert.convert(raw, targets)
                    if unknown_in(gbody) or len(children(gbody)) != len(kept_raw):
                        skipped.append(f"rebuilt group didn't convert cleanly ({len(kept_raw)} children left out)")
                        kept_raw = []
                    else:
                        kept = [(o['uid'], o['name'], gbody, o['filter'], o['body_size'] + len(gbody) - len(o['body']), ids['EntityGroup'])]
                        defined.add(o['uid'])
            counts['groups rebuilt'] += bool(kept)
            counts['children kept'] += len(kept_raw)
            counts['children skipped'] += len(skipped)
            if kept:
                if a_m_uids is None:
                    a_m_uids = []
                    if xrefs.cls('ACR', p.objects[0]['type']) == 'GridCellDataBlock':
                        _n, a_entries, a_m, _end = cell_list(p.objects[0]['body'])
                        a_m_uids = [struct.unpack_from('<I', e, 2)[0] for e in a_entries[:a_m]]
                if o['uid'] in a_m_uids:
                    pos = a_m_uids.index(o['uid'])
                    before, after = (list(reversed(a_m_uids[:pos])), a_m_uids[pos + 1:])
                else:
                    before, after = ([], [])
                add.setdefault(p.rec.name, []).append({'before': before, 'after': after, 'kept': kept})
            report.append(f"{p.rec.name}: {o['name']}: {('rebuilt with ' if kept else 'not rebuilt: ')}{len(kept_raw)} children" + (f"; skipped {len(skipped)} ({'; '.join(sorted(set(skipped)))})" if skipped else ''))
    for name, groups in add.items():
        p = payloads[name]
        n, entries, m, end = cell_list(p.objects[0]['body'])
        entries = list(entries)
        work = list(p.objects[1:])
        for grp in groups:
            kept = grp['kept']
            inst_entries = [b'\x01\x00' + struct.pack('<I', uid) for uid, *_ in kept]
            cur_m_uids = [struct.unpack_from('<I', x, 2)[0] for x in entries[:m]]
            in_file = {o['uid'] for o in work if o['has_uid']}
            idx, anchor_uid, side = anchor_index([u for u in grp['before'] if u in in_file], [u for u in grp['after'] if u in in_file], cur_m_uids)
            if idx is None:
                idx = m
            entries[idx:idx] = inst_entries
            n += len(inst_entries)
            m += len(inst_entries)
            if anchor_uid is not None:
                p_anchor = next((i for i, o in enumerate(work) if o['has_uid'] and o['uid'] == anchor_uid))
                phys_idx = p_anchor if side == 'after' else p_anchor - 1
            else:
                phys_idx = max((i for i, o in enumerate(work) if xrefs.cls('ACR', o['type']) in INSTANCE_CLASSES), default=-1)
            new_refs = {v for _u, _n, body, *_ in kept for v in (struct.unpack_from('<I', body, j + 2)[0] for j in range(10, len(body) - 5) if body[j] == 1 and body[j + 1] == 0)}
            move_i = [i for i, o in enumerate(work[:phys_idx + 1]) if o['has_uid'] and o['uid'] in new_refs and (xrefs.cls('ACR', o['type']) == 'MeshShape')]
            moved = [work[i] for i in move_i]
            for i in sorted(move_i, reverse=True):
                del work[i]
            phys_idx -= len(move_i)
            counts['shapes moved after the rebuilt groups'] += len(moved)
            new_objs = []
            for uid, nm, body, filt, size, cls_id in kept:
                nb = nm.encode('latin-1')
                new_objs.append({'uid': uid, 'has_uid': True, 'type': cls_id, '_raw': struct.pack('<III', cls_id, size, len(nb)) + nb + body, 'filter': filt})
            work[phys_idx + 1:phys_idx + 1] = new_objs + moved
        g = p.objects[0]['body'][:10] + struct.pack('<I', n) + b''.join(entries) + struct.pack('<I', m) + p.objects[0]['body'][end + 4:]
        objs = [(p.objects[0]['serial'], mapwalk.object_bytes(p.objects[0], g), p.objects[0]['filter'])]
        for o in work:
            if '_raw' in o:
                objs.append((o['uid'], o['_raw'], o['filter']))
            else:
                objs.append((o['serial'], mapwalk.object_bytes(o), o['filter']))
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    lifted = {uid for groups in add.values() for grp in groups for uid, *_ in grp['kept']}
    v = Counter()
    written = ArchiveImage.read(dest)
    seen = Counter()
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            head = set()
            if xrefs.cls('ACR', p.objects[0]['type']) == 'GridCellDataBlock':
                n, e, m, _ = cell_list(p.objects[0]['body'])
                head = {struct.unpack_from('<I', x, 2)[0] for x in e[:m]}
            for o in p.objects:
                if o['uid'] in lifted and xrefs.cls('ACR', o['type']) != 'EntityGroup':
                    seen[o['uid']] += 1
                    v['entities listed as instances'] += o['uid'] in head
                    v['entities holding an unconverted class'] += bool(unknown_in(o['body']))
                    v['entities'] += 1
                    continue
                if o['uid'] in lifted:
                    seen[o['uid']] += 1
                    ch = children(o['body'])
                    v['groups listed as instances'] += o['uid'] in head
                    v['groups holding an unconverted class'] += bool(unknown_in(o['body']))
                    v['groups, EntityGroup with head count == children'] += xrefs.cls('ACR', o['type']) == 'EntityGroup' and bool(ch) and (struct.unpack_from('<I', o['body'], ch[0][0] - 4)[0] == len(ch))
                    v['children'] += len(ch)
    v['lifted objects defined once'] = sum((1 for u in lifted if seen[u] == 1))
    v['groups'] = len(lifted)
    report.append(', '.join((f'{k} {n}' for k, n in counts.items())))
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
