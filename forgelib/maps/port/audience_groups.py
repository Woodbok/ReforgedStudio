"""forgelib/maps/port/audience_groups.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import crowdlife
import inventory
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from lift_dropped import anchor_index
from merchant_stalls import STATE, embedded, transposed
from merge_cells import cell_list
OUT = os.path.join(ROOT, 'out', 'maps')
TEMPLATE_MAP, TEMPLATE = ('DataPC_AC3MP_Boston_Mill.forge', 'AC3MP_GEN_Wall_Reader_015')

def pos(body):
    return struct.unpack_from('<3f', body, 58)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--map', required=True, help="ACR's archive (the spots and planes)")
    ap.add_argument('--spots', action='append', required=True, help="name prefix of ACR's audience spots")
    ap.add_argument('--marker', required=True, help='name prefix of their marker planes')
    ap.add_argument('--uid-base', type=lambda v: int(v, 0), required=True)
    ap.add_argument('--uid-limit', type=lambda v: int(v, 0), default=4096)
    ap.add_argument('--template', default=TEMPLATE)
    a = ap.parse_args(argv)
    tmpl = None
    for p in mapwalk.payloads('AC3', TEMPLATE_MAP):
        for o in p.objects:
            if o['name'] == a.template:
                tmpl, tmpl_size = (o['body'], o['body_size'])
    if tmpl is None:
        raise SystemExit(f'{a.template} not in {TEMPLATE_MAP}')
    t_world = crowdlife._world_of(tmpl)
    t_spb, t_gp = crowdlife.slot_offsets(tmpl)
    if not t_spb or len(t_spb) != len(t_gp) or len(crowdlife._all(tmpl, STATE)) != 1:
        raise SystemExit('template: slots / state matrix not as expected')
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        recs = [r for r in f.records if r.name != 'GlobalMetaFile' and r.size]
        payloads = {r.name: mapwalk.Payload(r, f.read(r)) for r in recs}
    defined = {o['uid'] for p in payloads.values() for o in p.objects if o['has_uid']}
    world = next((o['uid'] for p in payloads.values() for o in p.objects if xrefs.cls('AC3', o['type']) == 'World'))
    tmpl_refs = {struct.unpack_from('<I', tmpl, j + 2)[0] for j in range(2, len(tmpl) - 6) if tmpl[j] == 1 and tmpl[j + 1] == 0}
    mill_defs = {u for u, homes in inventory.universe('AC3').items() if any((h[0] == TEMPLATE_MAP for h in homes))}
    missing = sorted((u for u in (tmpl_refs & mill_defs) - {t_world} if u not in defined))
    if missing:
        raise SystemExit(f'template names objects the build lacks (import_ac3 from Mill first): {[hex(u) for u in missing]}')
    taken = set(inventory.universe('ACR')) | set(inventory.universe('AC3')) | defined
    lo, hi = (a.uid_base, a.uid_base + a.uid_limit)
    top = struct.pack('<H', lo >> 16)
    for p in payloads.values():
        k = p.data.find(top, 2)
        while k >= 0:
            u = struct.unpack_from('<I', p.data, k - 2)[0]
            if lo <= u < hi:
                taken.add(u)
            k = p.data.find(top, k + 1)
    nxt = [lo]

    def alloc():
        while nxt[0] in taken:
            nxt[0] += 1
        if nxt[0] >= hi:
            raise SystemExit('uid range exhausted')
        taken.add(nxt[0])
        return nxt[0]
    spots, planes, acr_files = ([], [], {})
    for p in mapwalk.payloads('ACR', a.map):
        acr_files[p.rec.name] = p
        for i, o in enumerate(p.objects):
            if not o['has_uid'] or xrefs.cls('ACR', o['type']) != 'Entity' or len(o['body']) < 74:
                continue
            if any(((o['name'] or '').startswith(s) for s in a.spots)):
                spots.append((p.rec.name, i, o))
            elif (o['name'] or '').startswith(a.marker):
                planes.append(o)
    report, counts, plan, made = ([], Counter(), {}, {})
    used = set()
    for fname, i, o in spots:
        if o['uid'] in defined:
            report.append(f"{o['name']}: already in the build - left")
            continue
        sp = pos(o['body'])
        cands = sorted(((math.hypot(pp[0] - sp[0], pp[1] - sp[1]), k) for k, pl in enumerate(planes) for pp in [pos(pl['body'])] if k not in used))
        if not cands or cands[0][0] > 4.0:
            report.append(f"{o['name']}: no marker plane within 4 m - not built")
            counts['not built'] += 1
            continue
        d, k = cands[0]
        used.add(k)
        pl = planes[k]
        pp = pos(pl['body'])
        fx, fy = (sp[0] - pp[0], sp[1] - pp[1])
        L = math.hypot(fx, fy)
        if L < 0.001:
            ax = struct.unpack_from('<3f', pl['body'], 10)
            fx, fy, L = (ax[0], ax[1], math.hypot(ax[0], ax[1]))
        fx, fy = (fx / L, fy / L)
        placement = struct.pack('<16f', fx, fy, 0.0, 0.0, -fy, fx, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, pp[0], pp[1], pp[2], 1.0)
        body = bytearray(tmpl)
        own_t = struct.unpack_from('<I', tmpl, 2)[0]
        mapping = {u: o['uid'] if u == own_t else alloc() for u in sorted(embedded(tmpl) | crowdlife.embedded_uids(tmpl))}
        for old, new in mapping.items():
            pat, rep = (struct.pack('<I', old), struct.pack('<I', new))
            for kk in crowdlife._all(bytes(body), pat):
                body[kk:kk + 4] = rep
        wk = crowdlife._all(bytes(body), struct.pack('<I', t_world))
        if len(wk) != 1:
            raise SystemExit(f'template names its World {len(wk)} times')
        struct.pack_into('<I', body, wk[0], world)
        body[10:74] = placement
        sk = crowdlife._all(bytes(body), STATE)
        body[sk[0] + 8:sk[0] + 72] = transposed(bytes(body[10:74]))
        body = bytes(body)
        nb = (o['name'] or '').encode('latin-1')
        obj = {'uid': o['uid'], 'has_uid': True, 'type': o['type'], 'filter': o['filter'], '_raw': struct.pack('<III', o['type'], tmpl_size, len(nb)) + nb + body}
        made[o['uid']] = (fname, o['name'], body, set(mapping.values()) - {o['uid']})
        plan.setdefault(fname, []).append((i, obj))
        counts['audience groups'] += 1
        report.append(f"{fname}: {o['name']}: at {pl['name']} ({pp[0]:.2f}, {pp[1]:.2f}, {pp[2]:.2f}) facing the spot ({math.degrees(math.atan2(fy, fx)):.0f} deg), {len(t_spb)} NPCs")
    for fname, items in plan.items():
        p = payloads[fname]
        ap_ = acr_files[fname]
        acr_uids = [x['uid'] if x['has_uid'] else None for x in ap_.objects]
        _n, a_entries, a_m, _end = cell_list(ap_.objects[0]['body'])
        a_m_uids = [struct.unpack_from('<I', e, 2)[0] for e in a_entries[:a_m]]
        n, entries, m, end = cell_list(p.objects[0]['body'])
        entries = list(entries)
        work = list(p.objects[1:])
        for i, obj in sorted(items, key=lambda t: t[0]):
            if obj['uid'] in a_m_uids:
                q = a_m_uids.index(obj['uid'])
                cur = [struct.unpack_from('<I', x, 2)[0] for x in entries[:m]]
                idx, _a, _s = anchor_index(list(reversed(a_m_uids[:q])), a_m_uids[q + 1:], cur)
                idx = m if idx is None else idx
            else:
                idx = m
            entries[idx:idx] = [b'\x01\x00' + struct.pack('<I', obj['uid'])]
            n += 1
            m += 1
            here = {x['uid']: k for k, x in enumerate(work) if x['has_uid']}
            prev = next((here[u] for u in reversed(acr_uids[:i]) if u in here), None)
            work.insert(prev + 1 if prev is not None else len(work), obj)
        g = p.objects[0]['body'][:10] + struct.pack('<I', n) + b''.join(entries) + struct.pack('<I', m) + p.objects[0]['body'][end + 4:]
        objs = [(p.objects[0]['serial'], mapwalk.object_bytes(p.objects[0], g), p.objects[0]['filter'])]
        for x in work:
            objs.append((x['uid'], x['_raw'], x['filter']) if '_raw' in x else (x['serial'], mapwalk.object_bytes(x), x['filter']))
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    seen = Counter()
    fresh = set().union(*(e for *_x, e in made.values())) if made else set()
    expect = Counter({u: b.count(struct.pack('<I', u)) for _f, _n, b, e in made.values() for u in e})
    found = Counter()
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            head = set()
            if xrefs.cls('AC3', p.objects[0]['type']) == 'GridCellDataBlock':
                _n, e, mm, _ = cell_list(p.objects[0]['body'])
                head = {struct.unpack_from('<I', x, 2)[0] for x in e[:mm]}
            for u in fresh:
                found[u] += p.data.count(struct.pack('<I', u))
            for o in p.objects:
                if o['has_uid'] and o['uid'] in made:
                    seen[o['uid']] += 1
                    v['groups listed'] += o['uid'] in head
                    v['groups as built'] += o['body'] == made[o['uid']][2]
    v['groups defined once'] = sum((1 for u in made if seen[u] == 1))
    v['fresh uids used by other objects'] = sum((1 for u in fresh if found[u] != expect[u]))
    report.append(', '.join((f'{k} {c}' for k, c in counts.items())))
    report.append(f'verify: {dict(v)}')
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
    if not (v['groups defined once'] == v['groups listed'] == v['groups as built'] == len(made) and v['fresh uids used by other objects'] == 0):
        raise SystemExit('verify failed')
if __name__ == '__main__':
    main(sys.argv[1:])
