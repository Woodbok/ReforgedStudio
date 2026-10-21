"""forgelib/maps/shift_map.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
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
import convert
import mapwalk
import xrefs
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
IDS = convert.class_ids()
PLACED = {struct.pack('<I', IDS['Entity']), struct.pack('<I', IDS['EntityGroup'])}
SECTIONS = {struct.pack('<I', IDS['OutOfBoundsSection']): 'OutOfBoundsSection', struct.pack('<I', IDS['CTFWallSection']): 'CTFWallSection'}

def sections(b):
    out = []
    for cid, name in SECTIONS.items():
        k = b.find(cid)
        while k > 0:
            if k + 16 <= len(b):
                pt = struct.unpack_from('<3f', b, k + 4)
                if all((not math.isnan(v) and abs(v) < 5000 for v in pt)):
                    out.append((k + 4, name))
            k = b.find(cid, k + 4)
    return out
MULTI_INERT = struct.pack('<I', IDS['MultiInertComponent'])
MULTI_MESH = struct.pack('<I', IDS['MultiMeshShape'])

def mopp_info(b):
    if MULTI_INERT not in b:
        return None
    k = b.find(MULTI_MESH)
    if k < 0 or k + 8 > len(b):
        return None
    n = struct.unpack_from('<I', b, k + 4)[0]
    q = k + 8 + 5 * n
    if n > 1000 or q + 4 > len(b) or struct.unpack_from('<I', b, q)[0] != n:
        return None
    q += 4 + 6 * n
    if q + 4 > len(b):
        return None
    size = struct.unpack_from('<I', b, q)[0]
    info = q + 4 + size
    if size > len(b) or info + 16 > len(b):
        return None
    pt = struct.unpack_from('<4f', b, info)
    if not all((not math.isnan(v) for v in pt)) or not all((abs(v) < 5000 for v in pt[:3])) or pt[3] <= 0:
        return None
    return info
GAME_STATE = struct.pack('<I', IDS['GameStateData'])
STATE_MATRIX = b'G\x00\x00\x00\x01\x00\x00\x00'
STATE_TAGS = (b'F\x00\x00\x00\x01\x00\x00\x00', STATE_MATRIX)

def state_matrices(b, x, y):
    out, k = ([], b.find(GAME_STATE))
    while k > 0:
        for tag in STATE_TAGS:
            j = b.find(tag, k, k + 200)
            if j > 0 and j + 8 + 64 <= len(b):
                m = struct.unpack_from('<16f', b, j + 8)
                if abs(m[3] - x) < 0.001 and abs(m[7] - y) < 0.001 and (m[15] == 1.0) and (j + 8 not in out):
                    out.append(j + 8)
        k = b.find(GAME_STATE, k + 4)
    return sorted(out)
NAVMESH_MANAGER = IDS['NavMeshManager']
NAVMESH = struct.pack('<I', IDS['NavMesh'])
BOUNDS = struct.pack('<I', IDS['BoundingVolume'])

def nav_points(b):
    pts = set()
    k = b.find(NAVMESH)
    while k >= 0:
        if k + 8 <= len(b):
            n = struct.unpack_from('<I', b, k + 4)[0]
            if 1 <= n <= 50000 and k + 8 + 16 * n <= len(b):
                verts = [k + 8 + 16 * i for i in range(n)]
                vals = [struct.unpack_from('<4f', b, o) for o in verts]
                if all((v[3] == 0.0 and all((not math.isnan(c) and abs(c) < 5000 for c in v[:3])) for v in vals)):
                    pts.update(verts)
        k = b.find(NAVMESH, k + 1)
    j = b.find(BOUNDS)
    while j >= 0:
        if j + 4 + 24 <= len(b):
            mn = struct.unpack_from('<3f', b, j + 4)
            mx = struct.unpack_from('<3f', b, j + 16)
            if all((not math.isnan(c) and abs(c) < 5000 for c in mn + mx)) and all((mn[i] <= mx[i] for i in range(3))):
                pts.update((j + 4, j + 16))
                o = j - 4 - 16
                if o >= 0:
                    org = struct.unpack_from('<3f', b, o)
                    if all((abs(org[i] - mn[i]) < 0.001 for i in range(3))):
                        pts.add(o)
        j = b.find(BOUNDS, j + 1)
    return sorted(pts)
REGION_LAYOUT = IDS['RegionLayout']
REGION_CELL = struct.pack('<I', IDS['RegionCell'])
MESH_SHAPE = struct.pack('<I', IDS['MeshShape'])
FILTER_INFO = struct.pack('<I', IDS['CollisionFilterInfo'])

def region_points(b):
    pts = set()
    k = b.find(REGION_CELL)
    while k >= 0:
        if b[k - 6:k - 4] != b'\x00\x01' and k + 70 <= len(b):
            v = struct.unpack_from('<8f', b, k + 38)
            if v[3] == 0 and v[7] == 0 and (v[0] <= v[4]) and (v[1] <= v[5]) and all((abs(c) < 5000 for c in v)):
                pts.update((k + 38, k + 54))
        k = b.find(REGION_CELL, k + 4)
    k = b.find(MESH_SHAPE)
    while k >= 0:
        if b[k - 6:k - 4] == b'\x00\x01':
            j = k + 4
            n = struct.unpack_from('<I', b, j)[0]
            if not 0 < n < 20000:
                raise SystemExit(f'region MeshShape at {k}: vertex count {n}')
            pts.update((j + 4 + 12 * i for i in range(n)))
            j += 4 + 12 * n
            m = struct.unpack_from('<I', b, j)[0]
            j += 4 + m
            pts.add(j)
            j += 16
            t = struct.unpack_from('<I', b, j)[0]
            j += 4 + 2 * t
            q = struct.unpack_from('<I', b, j)[0]
            j += 4 + q
            f = b.find(FILTER_INFO, j, j + 40)
            if f < 0:
                raise SystemExit(f'region MeshShape at {k}: no CollisionFilterInfo')
            pts.update((f + 12, f + 28))
        k = b.find(MESH_SHAPE, k + 4)
    return sorted(pts)

def is_transform(b, s):
    if s + 74 > len(b):
        return False
    m = struct.unpack_from('<16f', b, s + 10)
    if any((math.isnan(v) for v in m)):
        return False
    if abs(m[3]) > 0.0001 or abs(m[7]) > 0.0001 or abs(m[11]) > 0.0001 or (abs(m[15] - 1) > 0.0001):
        return False
    if not all((0.01 < math.sqrt(sum((m[4 * r + i] ** 2 for i in range(3)))) < 100 for r in range(3))):
        return False
    return all((abs(m[12 + i]) < 5000 for i in range(3)))

def placements(b):
    out, j = ([], b.find(b'\x00\x01'))
    while 0 <= j < len(b) - 80:
        if b[j + 6:j + 10] in PLACED and is_transform(b, j):
            out.append(j)
        j = b.find(b'\x00\x01', j + 1)
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='T00_souk')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix', default='T01')
    ap.add_argument('--dx', type=float, default=0.0)
    ap.add_argument('--dy', type=float, default=128.0)
    ap.add_argument('--placed-only', action='store_true', help='move only what the world places (listed instances, the stand-in tiles; the navmesh and regions as always)')
    a = ap.parse_args(argv)
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    with Forge(src) as f:
        payloads = [mapwalk.Payload(r, f.read(r)) for r in f.records if r.name != 'GlobalMetaFile' and r.size]
    stock = set()
    if a.placed_only:
        import inventory
        from merge_cells import cell_list
        stock = {u for u, h in inventory.universe('AC3').items() if any((x[0] in inventory.SHARED for x in h))}
    instances = {}

    def placed(p, o):
        if o['has_uid'] and o['uid'] in stock:
            return "an object AC3's shared archives define"
        first = xrefs.cls('ACR', p.objects[0]['type'])
        if first == 'GridCellDataBlock':
            if p.rec.name not in instances:
                _n, entries, m, _end = cell_list(p.objects[0]['body'])
                instances[p.rec.name] = {struct.unpack_from('<I', e, 2)[0] for e in entries[:m]}
            if o['has_uid'] and o['uid'] in instances[p.rec.name]:
                return None
            return "a cell's piece / unlisted object"
        if first == 'World':
            return None if xrefs.cls('ACR', o['type']) == 'FakeEntities' else 'a World singleton (effects, pickups)'
        return 'an object of a file no cell places (NPC / prop templates)'
    counts, planned, planned_secs, planned_mopp, planned_mats, planned_nav = (Counter(), {}, {}, {}, {}, {})
    for p in payloads:
        bodies = {}
        for i, o in enumerate(p.objects):
            b = o['body']
            if o['type'] == NAVMESH_MANAGER:
                pts = nav_points(b)
                if pts:
                    nb = bytearray(b)
                    for k in pts:
                        x, y = struct.unpack_from('<2f', b, k)
                        struct.pack_into('<2f', nb, k, x + a.dx, y + a.dy)
                    bodies[i] = bytes(nb)
                    planned_nav[p.rec.name, i] = pts
                    counts['NavMeshManagers moved'] += 1
                    counts['navmesh points moved (vertices, origins, box corners)'] += len(pts)
                continue
            if o['type'] == REGION_LAYOUT:
                pts = region_points(b)
                if pts:
                    nb = bytearray(b)
                    for k in pts:
                        x, y = struct.unpack_from('<2f', b, k)
                        struct.pack_into('<2f', nb, k, x + a.dx, y + a.dy)
                    bodies[i] = bytes(nb)
                    planned_nav[p.rec.name, i] = pts
                    counts[f"RegionLayout {o['name']} points moved (cell bounds, region shapes)"] += len(pts)
                continue
            offs = placements(b)
            if not offs:
                continue
            if a.placed_only:
                why = placed(p, o)
                if why:
                    counts[f'left as built: {why}'] += 1
                    continue
            nb = bytearray(b)
            mi = mopp_info(b) if xrefs.cls('ACR', o['type']) == 'Entity' else None
            if mi is not None:
                x, y = struct.unpack_from('<2f', b, mi)
                struct.pack_into('<2f', nb, mi, x + a.dx, y + a.dy)
                counts['merged-collision MOPP offsets moved (entity left at the origin)'] += 1
                bodies[i] = bytes(nb)
                planned[p.rec.name, i] = []
                planned_mopp[p.rec.name, i] = mi
                continue
            for s in offs:
                x, y = struct.unpack_from('<2f', b, s + 58)
                struct.pack_into('<2f', nb, s + 58, x + a.dx, y + a.dy)
                if s == 0 and xrefs.cls('ACR', o['type']) == 'Entity' and (abs(x) > 1 or abs(y) > 1):
                    pair = struct.pack('<2f', x, y)
                    k = b.find(pair, 74)
                    while k > 0:
                        struct.pack_into('<2f', nb, k, x + a.dx, y + a.dy)
                        counts['repeated positions moved'] += 1
                        k = b.find(pair, k + 8)
            px, py = struct.unpack_from('<2f', b, offs[0] + 58) if offs[0] == 0 else (None, None)
            mats = state_matrices(b, px, py) if px is not None else []
            for k in mats:
                struct.pack_into('<f', nb, k + 12, px + a.dx)
                struct.pack_into('<f', nb, k + 28, py + a.dy)
                counts['GameStateData matrices moved'] += 1
            if mats:
                planned_mats[p.rec.name, i] = mats
            secs = sections(b) if xrefs.cls('ACR', o['type']) == 'Entity' else []
            for k, name in secs:
                x, y = struct.unpack_from('<2f', b, k)
                struct.pack_into('<2f', nb, k, x + a.dx, y + a.dy)
                counts[f'{name} points moved'] += 1
            counts[f"{xrefs.cls('ACR', o['type']) or hex(o['type'])}: placements moved"] += len(offs)
            bodies[i] = bytes(nb)
            planned[p.rec.name, i] = offs
            if secs:
                planned_secs[p.rec.name, i] = [k for k, _n in secs]
        if not bodies:
            continue
        objs = [(o['serial'], mapwalk.object_bytes(o, bodies.get(i, o['body'])), o['filter']) for i, o in enumerate(p.objects)]
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, p.rec.index, payload)
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    v = Counter()
    written = ArchiveImage.read(dest)
    before = {(p.rec.name, i): o['body'] for p in payloads for i, o in enumerate(p.objects) if (p.rec.name, i) in planned or (p.rec.name, i) in planned_nav}
    with Forge(dest) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            p = mapwalk.Payload(r, f.read(r))
            v['files'] += 1
            if struct.unpack_from('<I', written.files[r.index].entry, 8)[0] != p.objects[0]['uid']:
                v['entry key != first uid'] += 1
            for i, o in enumerate(p.objects):
                pts = planned_nav.get((r.name, i))
                if pts is not None:
                    old, new = (before[r.name, i], o['body'])
                    good = len(old) == len(new)
                    moved = set()
                    for k in pts:
                        ox, oy = struct.unpack_from('<2f', old, k)
                        nx, ny = struct.unpack_from('<2f', new, k)
                        good &= abs(nx - ox - a.dx) < 0.001 and abs(ny - oy - a.dy) < 0.001
                        moved.update(range(k, k + 8))
                    good &= all((old[j] == new[j] for j in range(len(old)) if j not in moved))
                    v['navmesh / region layouts moved as planned'] += good
                    v['navmesh / region layouts not as planned'] += not good
                    continue
                offs = planned.get((r.name, i))
                if offs is None:
                    continue
                old, new = (before[r.name, i], o['body'])
                ok = len(old) == len(new)
                for s in offs:
                    ox, oy, oz = struct.unpack_from('<3f', old, s + 58)
                    nx, ny, nz = struct.unpack_from('<3f', new, s + 58)
                    ok &= abs(nx - ox - a.dx) < 0.001 and abs(ny - oy - a.dy) < 0.001 and (nz == oz)
                    ok &= old[s + 10:s + 58] == new[s + 10:s + 58]
                for k in planned_secs.get((r.name, i), []):
                    ox, oy, oz = struct.unpack_from('<3f', old, k)
                    nx, ny, nz = struct.unpack_from('<3f', new, k)
                    good = abs(nx - ox - a.dx) < 0.001 and abs(ny - oy - a.dy) < 0.001 and (nz == oz)
                    v['section points moved as planned'] += good
                    ok &= good
                for k in planned_mats.get((r.name, i), []):
                    om, nm = (struct.unpack_from('<16f', old, k), struct.unpack_from('<16f', new, k))
                    good = abs(nm[3] - om[3] - a.dx) < 0.001 and abs(nm[7] - om[7] - a.dy) < 0.001
                    good &= all((nm[j] == om[j] for j in range(16) if j not in (3, 7)))
                    v['GameStateData matrices moved as planned'] += good
                    ok &= good
                k = planned_mopp.get((r.name, i))
                if k is not None:
                    ox, oy, oz, osc = struct.unpack_from('<4f', old, k)
                    nx, ny, nz, nsc = struct.unpack_from('<4f', new, k)
                    good = abs(nx - ox - a.dx) < 0.001 and abs(ny - oy - a.dy) < 0.001 and ((nz, nsc) == (oz, osc))
                    good &= old[:k] == new[:k] and old[k + 8:] == new[k + 8:]
                    v['MOPP offsets moved as planned'] += good
                    ok &= good
                v['objects moved as planned'] += ok
                v['objects not as planned'] += not ok
    report = [f'moved by ({a.dx}, {a.dy}): ' + ', '.join((f'{k} {n}' for k, n in counts.most_common())), 'not moved: MapWorldInfo (map markers), CrowdDutyRegion, CrowdFlow (FlowPointPos)', f'verify: {dict(v)}']
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report))
if __name__ == '__main__':
    main(sys.argv[1:])
