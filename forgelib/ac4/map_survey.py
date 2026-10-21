"""forgelib/ac4/map_survey.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import collections
import os
import re
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'loader'), os.path.join(ROOT, 'ac4chars')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import common
import world
GRID_PARTITION = 237978692

def grid_last_node(path, cells):
    for rec, d in common.payloads(path):
        if rec.name.startswith('Cell'):
            continue
        k = d.find(struct.pack('<I', GRID_PARTITION))
        while k >= 0:
            levels, nodes = struct.unpack_from('<II', d, k + 4)
            if 1 <= levels <= 8 and nodes == sum((4 ** i for i in range(levels))):
                name = f'Cell{nodes - 1:05d}_DataBlock'
                if name not in cells:
                    raise SystemExit(f"the grid's last node {name} has no cell file")
                return name
            k = d.find(struct.pack('<I', GRID_PARTITION), k + 4)
    raise SystemExit('no GridPartition found')

def main(name):
    import clutter as C
    import downgrade
    import tod
    path = common.ac4_archive(common.AC4_MAPS.get(name, name))
    recs, objs, by_cls = ({}, {}, collections.defaultdict(list))
    for rec, d in common.payloads(path):
        ol = list(common.objects_in(d))
        recs[rec.name] = ol
        for o in ol:
            c = common.cls(o['type'], 'AC4')
            by_cls[c].append((o['name'].strip(), rec.name, o))
            if o.get('uid') is not None:
                objs.setdefault(o['uid'], (rec.name, o))
    print('archive', path)
    cells = {r: len(v) for r, v in recs.items() if r.startswith('Cell')}
    root = grid_last_node(path, cells)
    print(f'records {len(recs)}, cells {len(cells)}, root {root} ({cells[root]} objects)')
    print('World / Fakes / blocks:', [r for r in recs if not r.startswith('Cell') and (not r.startswith('DataBlock_') or re.search('Sun|Rain|PVE|Scene_Intro|Next_Gen', r))])
    for c in ('World', 'WorldAmbiance', 'LayeredSky', 'PropertyControllerData', 'MapEvent'):
        print(f'{c}:', [(n, r) for n, r, _ in by_cls.get(c, [])])
    for n, r, o in by_cls.get('MapEvent', []):
        ws = [hex(struct.unpack_from('<I', s, 4)[0]) for nm, s in world.segments(o['body']) if nm == 'WwiseID' and len(s) >= 8]
        print(f"   {n}: wwise {[w for w in ws if w != '0x0']}")
    wn, wr, W = by_cls['World'][0]
    banks = [hex(struct.unpack_from('<I', s, 4)[0]) for nm, s in world.segments(W['body']) if nm == 'WwiseID' and len(s) >= 8]
    print('World sound banks (map_sound.py MAPS):', banks)
    import rain
    for nm, s in world.segments(W['body']):
        if nm == 'WorldParticleSystem':
            own, regs, proto, fx, tail = rain.parse_wps(s)
            print('   WPS', objs.get(proto, (None, {'name': hex(proto)}))[1]['name'].strip(), len(regs), 'regions')
    for n, r, o in by_cls.get('PropertyControllerData', []):
        try:
            cur = tod.parse(o['body']).get((tod.LS, 4228852420))
            if cur and cur['keys']:
                print(f'   {n}: noon sun {tod._at(cur, 12 * tod.KEYS_PER_HOUR)[0]:.2f}')
        except Exception as e:
            print(f'   {n}: {e}')
    clut = [o for n, r, o in by_cls.get('Entity', []) if '_Ground_Clutter' in n]
    if clut:
        a = C.decode_ac4(clut[0]['body'])
        pts = C.world_points(a['pos'], a['E_raw'], a['entity_pos'])
        print(f"clutter {clut[0]['name'].strip()}: {len(pts)} instances, identity {a['identity']}")
        vis = struct.pack('<I', 3966078249)
        hosts = []
        for o in recs[root]:
            if common.cls(o['type'], 'AC4') != 'Entity' or vis not in o['body'] or '_Ground_Clutter' in o['name']:
                continue
            try:
                lo, hi = C.bv_world(o['body'])
                ident = C.entity_pos(o['body'])[1]
            except Exception:
                continue
            cover = sum((1 for p in pts if lo[0] <= p[0] <= hi[0] and lo[1] <= p[1] <= hi[1]))
            if cover and (hi[0] - lo[0]) * (hi[1] - lo[1]) > 400:
                hosts.append((cover, o['name'].strip(), ident))
        for h in sorted(hosts, reverse=True)[:14]:
            print('   host?', h)
    pig = collections.Counter((re.sub('_?\\d+$', '', n) for n, r, o in by_cls.get('EntityGroup', []) if 'Pigeon' in n))
    print('pigeon groups:', dict(pig))
    print('glow-shader / water materials (fountain fixes):', sorted({n for n, r, o in by_cls.get('Material', []) if re.search('fountain|Water|Waterfal', n, re.I)}))
    bad = collections.Counter()
    for n, r, o in by_cls.get('Mesh', []):
        if 'ID99' in n:
            continue
        try:
            downgrade.mesh(o['body'])
        except downgrade.Note as e:
            bad[n, str(e)[:50]] += 1
        except Exception as e:
            bad[n, 'EXC ' + str(e)[:50]] += 1
    print('non-crowd meshes the Mesh rule refuses:', bad.most_common(20))
    spec = {}
    for rec, d in common.payloads(os.path.join(common.AC4, 'DataPC_extra.forge')):
        if rec.name.endswith('MapDesc'):
            ol = common.objects_in(d)
            if ol and ol[0].get('uid') is not None:
                spec[ol[0]['uid']] = rec.name
    wuid = W['uid']
    for rec, d in common.payloads(os.path.join(common.AC4, 'DataPC.forge'), {'Game Bootstrap Settings'}):
        ol = common.objects_in(d)
        mpw = [o for o in ol if struct.pack('<I', wuid) in o['body'] and 'MpWorld' in o['name']]
        mpw_uids = {o['uid'] for o in mpw}
        for o in ol:
            if o in mpw or any((struct.pack('<I', u) in o['body'] for u in mpw_uids)):
                b = o['body']
                pics = [spec[u32] for u32 in (struct.unpack_from('<I', b, k)[0] for k in range(6, len(b) - 3)) if u32 in spec]
                print(f"AC4 entry {o['name'].strip()} {o['uid']:#x} pictures {pics}")
if __name__ == '__main__':
    main(sys.argv[1])
