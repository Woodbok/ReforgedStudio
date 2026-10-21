"""forgelib/maps/port/grid_window.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import math
import os
import re
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
for p in (HERE, MAPS, os.path.dirname(MAPS)):
    if p not in sys.path:
        sys.path.insert(0, p)
import convert
import library_cell
from relocate_cells import gcdb_node
GRID = re.compile(b'\\xed\\x83\\xb0\\x7d\\x5f\\x3f')
WINDOW = 96.0
MARGIN = 8.0
IDS = convert.class_ids()
BV = struct.pack('<I', IDS['BoundingVolume'])
OOBS = struct.pack('<I', IDS['OutOfBoundsSection'])
FAKES = struct.pack('<I', 3332013873)
FAKE_ENTITY = struct.pack('<I', 3615470884)

def layout(world_body):
    k = GRID.search(world_body)
    if not k:
        raise SystemExit("the World's int GridLayout not found")
    leaf, ox, oy, _z, side = struct.unpack_from('<5i', world_body, k.end())
    return (leaf, ox, oy, side)

def levels(leaf, side):
    out, first, s, size = ([], 0, side, leaf)
    while s >= 1:
        out.append((first, s, size))
        first += s * s
        s //= 2
        size *= 2
    return out

def node_square(node, lv, origin):
    for first, s, size in lv:
        if first <= node < first + s * s:
            row, col = divmod(node - first, s)
            x0, y0 = (origin[0] + col * size, origin[1] + row * size)
            return (size, (x0, y0, x0 + size, y0 + size))
    return (None, None)

def world_box(body):
    k = body.rfind(BV)
    if k < 0 or len(body) < 74:
        return None
    v = struct.unpack_from('<6f', body, k + 4)
    m = struct.unpack_from('<16f', body, 10)
    pts = [tuple((m[12 + j] + x * m[j] + y * m[4 + j] + z * m[8 + j] for j in range(3))) for x in (v[0], v[3]) for y in (v[1], v[4]) for z in (v[2], v[5])]
    return (tuple((min((p[i] for p in pts)) for i in range(3))), tuple((max((p[i] for p in pts)) for i in range(3))))

def check(files, cls, shared=frozenset(), window=WINDOW, margin=MARGIN, center=(0.0, 0.0)):
    world = [p.objects[0]['body'] for p in files.values() if cls(p.objects[0]['type']) == 'World']
    if len(world) != 1:
        raise SystemExit(f'{len(world)} World files')
    leaf, ox, oy, side = layout(world[0])
    lv = levels(leaf, side)
    cx, cy = center
    root_node = lv[-1][0]
    loaded, off = (set(), set())
    for n, p in files.items():
        first = cls(p.objects[0]['type'])
        if n.startswith('Cell') and first == 'GridCellDataBlock':
            node = struct.unpack_from('<I', p.objects[0]['body'], gcdb_node(p.objects[0]['body']))[0]
            _size, sq = node_square(node, lv, (ox, oy))
            over = sq is not None and sq[0] < cx + window and (sq[2] > cx - window) and (sq[1] < cy + window) and (sq[3] > cy - window)
            (loaded if node == root_node or over else off).add(n)
        elif n.startswith('DataBlock') or first == 'World':
            loaded.add(n)
    pts = []
    tiles = [0, 0]
    for p in files.values():
        for o in p.objects:
            b = o['body']
            k = b.find(OOBS)
            while k >= 0:
                pts.append(struct.unpack_from('<2f', b, k + 4))
                k = b.find(OOBS, k + 4)
            if b[6:10] == FAKES:
                starts = [j for j in range(len(b) - 8) if b[j:j + 8] == b'\x00' * 4 + FAKE_ENTITY]
                tiles[0] += len(starts)
                tiles[1] += sum((1 for i, s in enumerate(starts) if (starts[i + 1] if i + 1 < len(starts) else len(b)) - s > 400))
    play = (min((x for x, _ in pts)), min((y for _, y in pts)), max((x for x, _ in pts)), max((y for _, y in pts))) if pts else None
    play_inside = play is not None and cx - window <= play[0] and (play[2] <= cx + window) and (cy - window <= play[1]) and (play[3] <= cy + window)
    near = []
    if play:
        for n in off:
            for o in files[n].objects[1:]:
                if not o['has_uid'] or cls(o['type']) not in ('Entity', 'EntityGroup') or len(o['body']) < 80:
                    continue
                box = world_box(o['body'])
                if not box:
                    continue
                dx = max(play[0] - box[1][0], box[0][0] - play[2], 0.0)
                dy = max(play[1] - box[1][1], box[0][1] - play[3], 0.0)
                g = math.hypot(dx, dy)
                if g <= margin:
                    near.append((g, o['name'], n, box))
    where = {}
    for n, p in files.items():
        for o in p.objects:
            if o['has_uid']:
                where.setdefault(o['uid'], []).append((n, o))
    todo = [u for n in loaded for o in files[n].objects if cls(o['type']) in ('Entity', 'EntityGroup') for u in library_cell.refs(o['body'])]
    seen, refs_off = (set(), [])
    while todo:
        u = todo.pop()
        if u in seen or u < 65536 or u not in where:
            continue
        seen.add(u)
        homes = where[u]
        live = [o for n, o in homes if n in loaded]
        if not live and u not in shared and any((n in off for n, _o in homes)):
            refs_off.append((u, homes[0][1]['name'], cls(homes[0][1]['type']), sorted({n for n, _o in homes})[:3]))
        o = (live or [homes[0][1]])[0]
        if cls(o['type']) not in ('Mesh', 'TextureMap', 'Animation'):
            todo.extend(library_cell.refs(o['body']))
    return {'grid': (leaf, ox, oy, side), 'loaded': loaded, 'off': off, 'play': play, 'play_inside': play_inside, 'near_play': sorted(near), 'refs_off': refs_off, 'tiles': tuple(tiles), 'ok': play_inside and (not near) and (not refs_off)}

def summary(r, window=WINDOW, margin=MARGIN):
    leaf, ox, oy, side = r['grid']
    play = r['play']
    return f"grid {side} x {side} of {leaf} m at ({ox}, {oy}): {len(r['loaded'])} files loaded, {len(r['off'])} cells never (outside +-{window:g} m); play area " + (f'x {play[0]:.0f}..{play[2]:.0f}, y {play[1]:.0f}..{play[3]:.0f}' if play else 'none') + (' inside the window' if r['play_inside'] else ' NOT inside the window') + f"; never-loaded objects within {margin:g} m of it {len(r['near_play'])}" + (' (' + ', '.join((f'{n} {g:.1f} m' for g, n, _f, _b in r['near_play'][:4])) + ')' if r['near_play'] else '') + f"; reached only from never-loaded cells {len(r['refs_off'])}; stand-in tiles {r['tiles'][0]} ({r['tiles'][1]} with a mesh)"
