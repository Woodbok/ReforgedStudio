"""forgelib/spmaps/sp_convert.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import math
import os
import re
import struct
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EDITOR = os.environ.get('AC3_EDITOR') or os.path.join(os.path.dirname(ROOT), 'ac3-editor')
for p in (ROOT, HERE, EDITOR):
    if p not in sys.path:
        sys.path.insert(0, p)
import paths
import sp_assets
import sp_topview as T
import sp1_build as SP1
if sys.path[0] != EDITOR:
    sys.path.remove(EDITOR) if EDITOR in sys.path else None
    sys.path.insert(0, EDITOR)
MAX_VERTS = 60000
SINK = 150.0
GROUND_MARGIN = 2.0
UNDER = (np.array([[0.0, 0.0, -50.0], [0.01, 0.0, -50.0], [0.0, 0.01, -50.0]]), np.array([[0, 1, 2]]))
PLAIN = {'Visual', 'GuidanceSystem', 'InertComponent'}
GROUND = re.compile('_Ground_.*_LOD0_Chunk_', re.I)
SKIN_OK = re.compile('Branch_AmericanElm', re.I)
DROP = re.compile('Water_?Puddle|^GFX_|Flags|Glow|Placeholder|OutOfMemory|Caustic|Flames|EagleVision', re.I)
NO_TWIN = re.compile('Windows|FabricDirty|WoodPanelShops', re.I)
CUTOUT = re.compile('_AT(_|$)|_VA(_|$)|Leaves|Weeds|FishNet|Hay|Flags|Puddle|BirdShit|Decal|CobWeb|Chains', re.I)
FOLIAGE = re.compile('Leaves|Weeds|FishNet|Hay|Flags|CobWeb', re.I)
DECAL = re.compile('_VA(_|$)|Puddle|BirdShit|GarbageGround|Decal_|_Decal', re.I)
PUDDLE = re.compile('Puddle', re.I)
CLOTH = re.compile('Fabric|Tarp|Canvas|Tent|Sail|Awning|Cloth|Banner', re.I)
LEAVES = re.compile('Leaves', re.I)
WINDOW = re.compile('Window', re.I)
NOSOLID = re.compile('_VA(_|$)|Leaves|Weeds|FishNet|Hay|Flags|Puddle|BirdShit|Decal|Poster|Garbage|CobWeb|Chains', re.I)
BUILDING = re.compile('House|Building|_LM_|Church|Warehouse|Tavern|Hall|Shop|Store|Appendice|DocksWall|Docks_', re.I)
ROOF = re.compile('Roof', re.I)
SEL_SKIP = re.compile('SkyDome|Sky_Dome|GFX_|_OOB|OutOfBound|OutofBound|Border|_Sky|Skybox|Mist|Fog|_TRG|Trigger|Volume|NavPoint|Portal|_OCCLUDE|FakeExterior|Fake_Visual|Ground_Fake|Matte_Fake|Fake_Boat', re.I)
FAR_EXTRA = re.compile('Matte_Fake|Fake_Boat_\\d|Ground_Fake_01_LOD0_Chunk', re.I)
BUILDING_PART = re.compile('House|Building|_LM_|Church|Warehouse|Tavern|Hall|Shop|Store|Appendice|Roof|Floor|Chimney|Balcony|Lantern_Wall|Wall|Awning|Shed|Stair', re.I)
NOT_PART = re.compile('Deciduous|Tree|Elm|Branch|Vegeta|Leaves|Ivy', re.I)
PROP_BOX = re.compile('Wheelbarrow|Barrow|Cart|Wagon|Plank|Crate|Barrel|Sack|Basket|Bench|Table|Chair|Stool|Box|Package|Caisse|Pile|Log|Tool|Bucket|Trough|Pallet|Cask|Keg|Cage|Wheel|Anvil|Ladder', re.I)
PROP_MAX = 4.5
STANDIN = re.compile('Outskirts_\\d', re.I)
NO_FACADE = re.compile('Docks|FlatRoof', re.I)
FENCE = re.compile('Fence', re.I)
WELL = re.compile('HideSpot_Well', re.I)
PROPS = re.compile('Crate|Barrel|Caisse|Sack|Package|BrickPile|Construction_Wastes|WoodPlank|Plank_|Barricade|Table|Pile|Box', re.I)
TWIN_SKIP = re.compile('Vegeta|BrokenItems|Puddle|Poster|Flag|Leaves|Weeds|StreetDirt', re.I)
SYSTEMS = re.compile('Crowd|NavFlow|Podium|Pickup|Environment|TopViewCam|Snd_|GFX_|Debug|Spawn|SPW|Camera|Trigger', re.I)
LAYOUT = re.compile('Static_Group|Static_Protest|Static_Drinkers|Recruter|Wall_Reader|Wall_Static|Merchant_|Orator|AnimalAlarm|Smoke_Chimney', re.I)
NOT_POINTS = re.compile('OOB|_DOMI_|_AA_0\\d|Podium_OUTRO|GFX_FX_CollisionMaterial', re.I)

@dataclass
class Config:
    name: str
    sp_map: str
    box: tuple
    archive: str
    area: tuple
    area_name: str
    grid: tuple
    prefix: str = 'SP2'
    keep: str = 'Outskirts|Water|Ocean|Sky|_OOB|OOB_'
    base_name: str = 'AC3MP_BOS_Wood_01a'
    alpha_bases: tuple = ('AC3MP_BOS_PostersAndFlags_01a', 'AC3MP_BOS_Atlas_Alpha_01A', 'AC3MP_BOS_Tree_Veg_01A', 'AC3MP_BOS_WeedsCity_01a')
    foliage_bases: tuple = ('AC3MP_BOS_Tree_Veg_01A', 'AC3MP_BOS_Atlas_Alpha_01A')
    card_bases: tuple = ('AC3MP_BOS_Atlas_Alpha_01A',)
    multi_base: str = 'AC3MP_BOS_GroundSand_Mud_01a_Multi'
    water: str = 'AC3MP_BOS_Water_Sea_01A'
    water_z: float = -3.0
    mp_prefix: str = '^AC3MP_(BOS|GEN)_'
    sp_prefix: str = '^(BOS|GEN)_(ALL|DT|PO|MT|CS|LM|DOW)_'
    backdrop: float = 30.0
    ground_fill: float = 30.0
    interiors: bool = False
    height_label: str = ''
    height_region: tuple = ()
    shift: tuple = field(default=None)
    tunnels: bool = False
    texture_from: dict = field(default_factory=dict)
    drop_objects: str = ''
    drop_near: tuple = ()
    min_top: float = None
    under: object = None
    water_holes: tuple = ()
    world_water_level: float = None
    boundary: dict = field(default_factory=dict)
    nearest_cell: bool = False
    whole_buildings: bool = False
    fill_alpha_holes: bool = False
    remove_ring: float = 0.0
    flat_multi: bool = False
    prop_boxes: bool = False
    replace_target: bool = False
    mp_twins: bool = True
    mat_twins: bool = True
    far_radius: float = 0.0
    far_erode: float = 0.2
    far_grid_lod: int = 0
    sea_z: float = None
    view_distance: int = None
    far_window: float = 0.0
    far_lod: int = 0
    far_lod_radius: float = 0.0
    two_layer: bool = False
    two_layer_blend: tuple = None
    sp_normals: bool = False
    two_layer_own: bool = False
    world_lightmap: tuple = None
    far_ground_gap: float = 0.0
    far_ground_drop: float = 0.0
    hold_gaps: bool = False
    select_visible: bool = False
    decal_bases: tuple = ()
    two_sided_cloth: bool = False
    sea: tuple = ()
    mesh_fit_slack: tuple = (1.25, 0.5)
    dedupe: bool = False
    standins_off: bool = False

    def __post_init__(self):
        if self.shift is None:
            self.shift = (self.area[0] - self.box[0], self.area[1] - self.box[1], 0.0)
        if not self.height_label:
            self.height_label = self.name
        if not self.height_region:
            b = self.box
            self.height_region = (b[0] - 50.0, b[1] - 50.0, b[2] + 50.0, b[3] + 50.0)

def at(o):
    m = np.eye(4)
    m[3, :3] = o
    return m

def normal_bytes(p, Mx, n, on):
    nb = np.full((n, 3), 128, np.uint8)
    nv = p.get('normals')
    if not on or nv is None or len(nv) != n:
        return nb
    R = np.asarray(Mx, np.float64)[:3, :3]
    try:
        w = np.asarray(nv, np.float64) @ np.linalg.inv(R).T
    except np.linalg.LinAlgError:
        return nb
    ln = np.linalg.norm(w, axis=1)
    ok = ln > 1e-06
    nb[ok] = np.clip(np.rint(w[ok] / ln[ok, None] * 127.5 + 127.5), 0, 255).astype(np.uint8)
    return nb

def split(V, Tr, U, Cl, cap=MAX_VERTS):
    if len(V) < cap:
        yield (V, Tr, U, Cl)
        return
    start = 0
    while start < len(Tr):
        used, end = ({}, start)
        while end < len(Tr) and len(used) + 3 <= cap:
            for i in Tr[end]:
                used.setdefault(int(i), len(used))
            end += 1
        keys = np.fromiter(used.keys(), np.int64)
        remap = np.full(len(V), -1, np.int64)
        remap[keys] = np.arange(len(keys))
        yield (V[keys], remap[Tr[start:end]], U[keys], Cl[keys])
        start = end

def slab(x0, y0, x1, y1, z0, z1):
    v = np.array([[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)], np.float64)
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    c = v.mean(0)
    tris = []
    for a, b, cc, dd in quads:
        for tr in ((a, b, cc), (a, cc, dd)):
            q = v[list(tr)]
            if np.dot(np.cross(q[1] - q[0], q[2] - q[0]), q.mean(0) - c) < 0:
                tr = tr[::-1]
            tris.append(tr)
    return (v, np.array(tris))

def fence_tops(parts, near=0.3):
    V = np.concatenate([v for v, _t in parts])
    lo, hi = (V.min(0), V.max(0))
    top = V[V[:, 2] > hi[2] - 0.5]
    if len(top) < 4:
        return []
    out = []
    for ax in (0, 1):
        o = 1 - ax
        if hi[ax] - lo[ax] < 1.0:
            continue
        offs = [(lo[o] + hi[o]) / 2] if hi[o] - lo[o] < 0.6 else [lo[o], hi[o]]
        for off in offs:
            sel = top[np.abs(top[:, o] - off) < near + (hi[o] - lo[o] < 0.6) * 0.3]
            if len(sel) < 4:
                continue
            span = sel[:, ax].max() - sel[:, ax].min()
            if span < 0.6 * (hi[ax] - lo[ax]):
                continue
            z = float(np.percentile(sel[:, 2], 80))
            a, b = (np.zeros(3), np.zeros(3))
            a[ax], b[ax] = (sel[:, ax].min() + 0.05, sel[:, ax].max() - 0.05)
            a[o] = b[o] = float(np.median(sel[:, o]))
            a[2] = b[2] = z
            out.append((a, b))
    return out

def interior_box(parts, inset=0.4):
    V = np.concatenate([v for v, _t in parts])
    T_ = np.concatenate([np.asarray(t) + sum((len(v) for v, _ in parts[:i])) for i, (_v, t) in enumerate(parts)])
    P = V[T_]
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ln = np.linalg.norm(n, axis=1)
    wall = (np.abs(n[:, 2]) < 0.1 * np.maximum(ln, 1e-09)) & (ln > 0.05)
    if wall.sum() < 4:
        return None
    W = P[wall].reshape(-1, 3)
    lo, hi = (W.min(0), W.max(0))
    x0, y0, x1, y1 = (lo[0] + inset, lo[1] + inset, hi[0] - inset, hi[1] - inset)
    z0, z1 = (max(lo[2], 0.0), 0.85 * hi[2])
    if x1 - x0 < 1.0 or y1 - y0 < 1.0 or z1 - z0 < 1.5:
        return None
    return slab(x0, y0, x1, y1, z0, z1)

def eave_holds(parts, depth=0.12, min_z=2.0):
    V = np.concatenate([v for v, _t in parts])
    T_ = np.concatenate([np.asarray(t) + sum((len(v) for v, _ in parts[:i])) for i, (_v, t) in enumerate(parts)])
    P = V[T_]
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ln = np.linalg.norm(n, axis=1)
    n = n / np.maximum(ln, 1e-09)[:, None]
    roof = np.nonzero((n[:, 2] > 0.25) & (n[:, 2] < 0.97) & (ln > 0.1))[0]

    def key(x):
        return tuple(np.round(x, 2))
    count = Counter()
    for i in roof:
        for e in range(3):
            count[frozenset((key(P[i, e]), key(P[i, (e + 1) % 3])))] += 1
    segs = defaultdict(list)
    zaxis = np.array([0.0, 0.0, 1.0])
    for i in roof:
        out = np.array([n[i, 0], n[i, 1], 0.0])
        if np.linalg.norm(out) < 0.2:
            continue
        out /= np.linalg.norm(out)
        along = np.cross(zaxis, out)
        for e in range(3):
            p, q, r = (P[i, e], P[i, (e + 1) % 3], P[i, (e + 2) % 3])
            if count[frozenset((key(p), key(q)))] != 1 or abs(p[2] - q[2]) > 0.15 or np.linalg.norm(q - p) < 0.3:
                continue
            z = (p[2] + q[2]) / 2
            if z < min_z or r[2] < z + 0.1 or np.dot(out, (p + q) / 2 - r) <= 0:
                continue
            o = round(float(np.dot((p + q) / 2, out)), 1)
            k = (round(float(out[0]), 1), round(float(out[1]), 1), round(z, 1), o)
            a_, b_ = (float(np.dot(p, along)), float(np.dot(q, along)))
            segs[k].append((min(a_, b_), max(a_, b_), out, along, z, o))
    holds = []
    for _k, ss in segs.items():
        ss.sort(key=lambda x: x[0])
        merged = []
        for lo, hi, out, along, z, o in ss:
            if merged and lo <= merged[-1][1] + 0.05:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi, out, along, z, o])
        for lo, hi, out, along, z, o in merged:
            if hi - lo < 1.0:
                continue
            base = out * (o - depth) + np.array([0.0, 0.0, z - 0.03])
            holds.append((base + along * lo, base + along * hi, zaxis, out))
    return holds

def near_poly(pts, poly, margin):
    pts, poly = (np.asarray(pts, np.float64), np.asarray(poly, np.float64))
    x, y = (pts[:, 0:1], pts[:, 1:2])
    a, b = (poly, np.roll(poly, 1, axis=0))
    cross = ((a[:, 1] > y) != (b[:, 1] > y)) & (x < (b[:, 0] - a[:, 0]) * (y - a[:, 1]) / np.where(b[:, 1] == a[:, 1], 1e-12, b[:, 1] - a[:, 1]) + a[:, 0])
    ins = cross.sum(1) % 2 == 1
    d = b - a
    t = np.clip(((x - a[:, 0]) * d[:, 0] + (y - a[:, 1]) * d[:, 1]) / np.maximum((d ** 2).sum(1), 1e-09), 0, 1)
    dist = np.hypot(a[:, 0] + t * d[:, 0] - x, a[:, 1] + t * d[:, 1] - y).min(1)
    return ins | (dist <= margin)

def inside(poly, p):
    c = False
    for i in range(len(poly)):
        a, b = (poly[i], poly[i - 1])
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
            c = not c
    return c

def edge_dist(poly, p):
    best = 1000000000.0
    for i in range(len(poly)):
        a, b = (np.array(poly[i - 1]), np.array(poly[i]))
        d = b - a
        t = max(0.0, min(1.0, float(np.dot(np.array(p) - a, d) / max(np.dot(d, d), 1e-09))))
        best = min(best, float(np.linalg.norm(np.array(p) - (a + t * d))))
    return best

def mat_key(n):
    n = n.lower().strip()
    n = re.sub('^(ac3mp_|ac2mp_)', '', n)
    n = re.sub('_pc$', '', n)
    toks = [t for t in n.split('_') if t not in ('bos', 'all', 'cs', 'dt', 'po', 'mt', 'gen', 'lm', 'mil', 'ny')]
    return '_'.join((re.sub('^(\\d+)[a-z]$', '\\1', t) for t in toks))
CLIMB_DZ = 1.6

def walk_pieces(g):
    par = np.arange(g.n)

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a
    for i in range(g.n):
        for j in g.nb[i]:
            if j >= 0:
                par[find(i)] = find(j)
        for dx, dy in ((1, 0), (0, 1)):
            for j in g.at.get(g.key(int(g.gx[i]) + dx, int(g.gy[i]) + dy), ()):
                if g.g0[0] <= g.gx[i] + dx < g.g0[0] + g.nx and g.g0[1] <= g.gy[i] + dy < g.g0[1] + g.ny and (0.4 < abs(g.z[j] - g.z[i]) <= CLIMB_DZ):
                    par[find(i)] = find(j)
    root = np.array([find(i) for i in range(g.n)])
    u, inv, cnt = np.unique(root, return_inverse=True, return_counts=True)
    rank = np.empty(len(u), np.int64)
    rank[np.argsort(-cnt)] = np.arange(len(u))
    return rank[inv]

def walk_grid(tris, ent, box):
    from ac3editor import navgen as NG
    return NG.Grid(np.asarray(tris, np.float64), np.asarray(ent, np.int64), np.ones(int(ent.max()) + 1, bool), (math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3])))

def climb_roots(path):
    from ac3editor import mapdata as M
    S = M.schema()
    C = M.Codec(S)
    ob = M._obj
    bad = []
    for _rec, name, t, body, _f in M.iter_objects_ex(path):
        if S.name_of(t) not in ('Entity', 'EntityGroup'):
            continue
        try:
            o = C.decode(body).obj
        except Exception:
            continue
        for e in [o] + [ob(r) for r in o.fields.get('Entities', [])]:
            for c in e.fields.get('Components', []) if e is not None else []:
                cf = ob(c)
                if cf is None or S.name_of(cf.type_hash) != 'GuidanceSystem':
                    continue
                pf = ob(cf.fields['Partitioner']).fields
                root = struct.unpack('<H', pf['RootIndex'])[0]
                if root >= len(pf['ListNodes']) and (not (root == 65535 and (not pf['ListNodes']))):
                    bad.append(name)
    return bad

def set_water_level(path, level, log=print):
    from ac3editor import archive, mapdata as M
    S = M.schema()
    C = M.Codec(S)
    done = []

    def fn(_rec, o):
        if S.name_of(o['type']) != 'World':
            return None
        body = o['body']
        f0 = C.decode(body).obj.fields
        old = f0['WaterLevel']
        at = [i for i in range(min(len(body), 96) - 3) if body[i:i + 4] == old]
        if len(at) != 1:
            raise SystemExit(f"WaterLevel bytes {old.hex()} found {len(at)} times near the World body's start")
        new = body[:at[0]] + struct.pack('<f', level) + body[at[0] + 4:]
        f1 = C.decode(new).obj.fields
        if struct.unpack('<f', f1['WaterLevel'])[0] != level or any((repr(f1[k]) != repr(f0[k]) for k in f0 if k != 'WaterLevel')):
            raise SystemExit('WaterLevel edit changed something else')
        done.append((struct.unpack('<f', old)[0], level))
        return new
    tmp = path + '.wl.forge'
    archive.rewrite(path, tmp, fn, log=lambda *_a: None)
    os.replace(tmp, path)
    if not done:
        raise SystemExit('no World object found')
    log(f'World WaterLevel {done[0][0]} -> {done[0][1]}')

def coarsest_mesh(Ms, refs, lo, hi):
    cands = []
    for u in refs:
        k = Ms.kind(u)
        if k == 'mesh':
            cands.append(u)
        elif k == 'lod':
            for o in Ms.record_objects(u):
                if o['type'] == T.H['LODSelector']:
                    cands += T.u64s(o['body'], Ms.pool)
    brad = float(max(np.linalg.norm(lo), np.linalg.norm(hi)))
    best = None
    for u in dict.fromkeys(cands):
        g = Ms.mesh(u)
        if g is None or g[2] or (not len(g[1])):
            continue
        rad = float(np.linalg.norm(g[0], axis=1).max()) if len(g[0]) else 0.0
        if rad > brad * 1.25 + 0.5:
            continue
        if best is None or len(g[1]) < best[1]:
            best = (u, len(g[1]))
    return best[0] if best else None

def lod_mesh(Ms, refs, lo, hi, k):
    cands = []
    for u in refs:
        kd = Ms.kind(u)
        if kd == 'mesh':
            cands.append(u)
        elif kd == 'lod':
            for o in Ms.record_objects(u):
                if o['type'] == T.H['LODSelector']:
                    cands += T.u64s(o['body'], Ms.pool)
    brad = float(max(np.linalg.norm(lo), np.linalg.norm(hi)))
    a, b = getattr(Ms, 'fit_slack', (1.25, 0.5))
    ok = []
    for u in dict.fromkeys(cands):
        g = Ms.mesh(u)
        if g is None or g[2] or (not len(g[1])):
            continue
        rad = float(np.linalg.norm(g[0], axis=1).max()) if len(g[0]) else 0.0
        if rad > brad * a + b:
            continue
        ok.append((len(g[1]), u))
    if not ok:
        return None
    ok.sort(reverse=True)
    return ok[min(k, len(ok) - 1)][1]

def set_world_lightmap(path, spec, log=print):
    from ac3editor import archive, mapdata as M, materials
    S = M.schema()
    alpha, rgb = spec
    done = []

    def fn(_rec, o):
        if S.name_of(o['type']) != 'TextureMap' or 'WorldLightMap' not in (o['name'] or ''):
            return None
        img = np.zeros((512, 512, 4), np.uint8)
        img[:, :, :3] = np.asarray(rgb, np.uint8)
        img[:, :, 3] = alpha
        done.append(o['name'])
        return materials.texture_body(o['body'], img)
    tmp = path + '.lm.forge'
    archive.rewrite(path, tmp, fn, log=lambda *_a: None)
    os.replace(tmp, path)
    if not done:
        raise SystemExit('no WorldLightMap texture in the map (R-LIGHTMAP-OWN)')
    log(f'World lightmap re-imaged uniform (alpha {alpha}, rgb {tuple(rgb)}): {done}')

def set_view_distance(path, metres, log=print):
    sys.path.insert(0, os.path.join(ROOT, 'dishonored'))
    import ac3side
    seen = []

    def view(cname, _name, root):
        if cname != 'World':
            return False
        g = root.obj.fields['GridLayout'].fields
        seen.append((g['DefaultViewingDistance'][0], g['DefaultLoadingDistance'][0]))
        g['DefaultViewingDistance'] = bytes([metres])
        return True
    tmp = path + '.vd.forge'
    n, same = ac3side.patch_objects(path, tmp, view, log=lambda *_a: None)
    if n != 1 or not same:
        raise SystemExit(f'World patch: {n} objects changed, other records identical: {same}')
    os.replace(tmp, path)
    log(f'World DefaultViewingDistance {seen[0][0]} -> {metres} m (loading {seen[0][1]} m kept)')

def sink_standins(path, log=print, depth=150.0):
    from ac3editor import archive, mapdata as M
    S = M.schema()
    C = M.Codec(S)
    done = []

    def fn(_rec, o):
        if S.name_of(o['type']) != 'FakeEntities':
            return None
        body = o['body']
        f0 = C.decode(body).obj
        new = bytearray(body)
        for fe in f0.fields['FakeEntities']:
            e = fe.fields['FakeEntity']
            eo = e.obj if hasattr(e, 'obj') else e
            gm = eo.fields['GlobalMatrix']
            at_ = [i for i in range(len(body) - len(gm) + 1) if body[i:i + len(gm)] == gm]
            if len(at_) != 1:
                raise SystemExit(f"a stand-in tile's matrix found {len(at_)} times in the FakeEntities body")
            mm = list(struct.unpack('<16f', gm))
            mm[14] -= depth
            new[at_[0]:at_[0] + len(gm)] = struct.pack('<16f', *mm)
            done.append(mm[12:15])
        f1 = C.decode(bytes(new)).obj
        if len(f1.fields['FakeEntities']) != len(f0.fields['FakeEntities']):
            raise SystemExit('stand-in edit changed the tile count')
        return bytes(new)
    tmp = path + '.fk.forge'
    archive.rewrite(path, tmp, fn, log=lambda *_a: None)
    os.replace(tmp, path)
    log(f'stand-in tiles sunk {depth:.0f} m: {len(done)} ({[tuple((round(v, 1) for v in d)) for d in done]})')
DENSITY_MAX = 1500

def collision_density(P, log=print, limit=DENSITY_MAX):
    vox, who = (Counter(), defaultdict(Counter))
    items = []
    for m in P['models']:
        if m.get('facade') == 'T' or m.get('record') or NOSOLID.search(P['textures'].get(m['sp_material'], ('',))[0] or ''):
            continue
        V, Tr = m['collision'] if 'collision' in m else (m['verts'], m['tris'])
        items.append((m['name'], np.asarray(V, np.float64) + m['matrix'][3, :3], np.asarray(Tr)))
    for s in P['twin_solids']:
        M_ = s['matrix']
        items.append((s['name'], np.asarray(s['verts'], np.float64) @ M_[:3, :3] + M_[3, :3], np.asarray(s['tris'])))
    for name, W, Tr in items:
        if not len(Tr):
            continue
        keys, k = np.unique(np.floor(W[Tr].mean(1)).astype(np.int64), axis=0, return_counts=True)
        for key, cnt in zip(map(tuple, keys), k):
            vox[key] += int(cnt)
            who[key][name] += int(cnt)
    peak = vox.most_common(1)[0] if vox else ((0, 0, 0), 0)
    for key, s in vox.most_common(3):
        log(f'  {s} collision triangles in the cubic metre at {key}: {who[key].most_common(2)}')
    log(f'collision density: at most {peak[1]} triangles in a cubic metre (limit {limit}, R-CONTACTS)')
    if peak[1] > limit:
        raise SystemExit("collision too dense there: a query would overrun the engine's contact scratch (0x86EC7F)")
    return peak[1]
FLATCOLOR = re.compile('FlatColor', re.I)

def sp_texture(A, mat_uid, max_size=512):
    tu = A.material_texture_uid(mat_uid) if mat_uid else None
    tx = A.texture_rgba(tu, max_size=max_size) if tu else None
    if tx and FLATCOLOR.search(tx[0] or ''):
        c = A.material_params(mat_uid).get('Layer0_Color')
        if c:
            rgba = np.array(tx[1], copy=True)
            rgba[:, :, :3] = np.clip(np.round(rgba[:, :, :3] * np.array(c[:3]) * 2.0), 0, 255).astype(np.uint8)
            tx = (tx[0], rgba)
    return tx

def second_layer(A, mat_uid, out, max_size=512):
    lay = A.material_layers(mat_uid)
    if len(lay) < 2:
        return
    tb = A.texture_rgba(lay[1], max_size=max_size)
    if tb:
        out[mat_uid] = tb[1]

def bake_layers(A, mat_uid, rgba):
    from PIL import Image
    lay = A.material_layers(mat_uid)
    if len(lay) < 2:
        return rgba
    tb = A.texture_rgba(lay[1], max_size=512)
    if not tb:
        return rgba
    b = np.array(Image.fromarray(tb[1]).resize((rgba.shape[1], rgba.shape[0]), Image.BILINEAR), np.float32)
    a = rgba[:, :, 3:4].astype(np.float32) / 255.0
    out = rgba.copy()
    out[:, :, :3] = np.clip(np.round(rgba[:, :, :3].astype(np.float32) * a + b[:, :, :3] * (1 - a)), 0, 255).astype(np.uint8)
    out[:, :, 3] = 255
    return out

def fill_holes(rgba, max_holes=0.25, dark=24, steps=64):
    hole = rgba[:, :, 3] < 128
    frac = float(hole.mean())
    if not 0 < frac <= max_holes or float(rgba[:, :, :3][hole].mean()) > dark:
        return 0.0
    col = rgba[:, :, :3].astype(np.float32)
    solid = ~hole
    for _ in range(steps):
        if solid.all():
            break
        acc = np.zeros_like(col)
        n = np.zeros(hole.shape, np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sv = np.roll(solid, (dy, dx), (0, 1))
            acc += np.roll(col, (dy, dx), (0, 1)) * sv[:, :, None]
            n += sv
        grow = ~solid & (n > 0)
        col[grow] = acc[grow] / n[grow][:, None]
        solid = solid | grow
    if not solid.all():
        col[~solid] = col[solid].mean(0)
    rgba[:, :, :3] = np.clip(np.round(col), 0, 255).astype(np.uint8)
    return frac

def merge_under(P, PU, top):
    P = dict(P)
    P['models'] = P['models'] + PU['models']
    P['textures'] = {**PU['textures'], **P['textures']}
    holds = defaultdict(list)
    for src in (P['holds'], PU['holds']):
        for t, h in src.items():
            holds[t] += h
    P['holds'] = holds
    perches = defaultdict(list)
    for src in (P['perches'], PU['perches']):
        for t, h in src.items():
            perches[t] += h
    P['perches'] = dict(perches)
    P['twin_solids'] = P['twin_solids'] + PU['twin_solids']
    P['tunnel'], P['tunnel_top'] = (PU['tunnel'], top)
    P['stats'] = dict(P['stats'], under=PU['stats'])
    return P

class Convert:

    def __init__(self, cfg):
        self.cfg = cfg
        self.out = os.path.join(ROOT, 'out', 'spmaps', cfg.name, cfg.archive)
        self.stock = os.path.join(paths.AC3_PRISTINE, cfg.archive)
        self.keep = re.compile(cfg.keep, re.I)
        self._src = None

    @property
    def src(self):
        if self._src is None:
            self._src = SP1.release_map(self.cfg.archive)
        return self._src

    def to_bh(self, world):
        return world + np.array(self.cfg.shift)

    def tile_of(self, x, y):
        g = self.cfg.grid
        t = (int(math.floor((x - g[0]) / g[2])), int(math.floor((y - g[1]) / g[2])))
        if not self.cfg.nearest_cell:
            return t
        cells = self.cell_tiles()
        if t in cells:
            return t
        return min(cells, key=lambda c: ((g[0] + (c[0] + 0.5) * g[2] - x) ** 2 + (g[1] + (c[1] + 0.5) * g[2] - y) ** 2, c))

    def cell_tiles(self):
        if getattr(self, '_cells', None) is None:
            from ac3editor import mapdata as M
            g = self.cfg.grid
            dim = int(round(-2 * g[0] / g[2]))
            names = {e.record for e in M.load(self.src, log=lambda *a: None).entities}
            self._cells = set()
            for n in names:
                if n.startswith('Cell') and n.endswith('_DataBlock'):
                    k = int(n[4:9])
                    if k < dim * dim:
                        self._cells.add((k % dim, k // dim))
        return self._cells

    def tile_origin(self, t):
        g = self.cfg.grid
        return np.array([g[0] + (t[0] + 0.5) * g[2], g[1] + (t[1] + 0.5) * g[2], 0.0])

    def play_area(self):
        from ac3editor import boundary
        if self.cfg.area_name in self.cfg.boundary:
            return [tuple(map(float, c)) for c in self.cfg.boundary[self.cfg.area_name]]
        a = next((v for v in boundary.areas(self.stock).values() if v['name'] == self.cfg.area_name))
        return [tuple(map(float, c)) for c in a['corners']]

    def sp_objects(self, log=print):
        c_ = self.cfg
        BOX, FILL, BACK = (c_.box, c_.ground_fill, c_.backdrop)
        d = T.index(c_.sp_map, log=None)
        A = sp_assets.Assets(c_.sp_map)
        Ms = T.Meshes(A.f, d['owner'])
        Ms.fit_slack = c_.mesh_fit_slack
        out, skipped = ([], Counter())
        taken = None
        drop = re.compile(c_.drop_objects, re.I) if c_.drop_objects else None
        cands = []
        for name, rec, m, lo, hi, refs in d['entities']:
            if (SEL_SKIP if c_.select_visible else T.SKIP).search(name) or T.LOWER_LOD.search(name) or (not refs):
                continue
            if drop is not None and drop.search(name):
                skipped['left out by name (drop_objects)'] += 1
                continue
            a, b = T.world_box(m, lo, hi)
            if c_.min_top is not None and b[2] < c_.min_top:
                skipped['under min_top (SP mission copies under the map)'] += 1
                continue
            c = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            pl = np.array(m, dtype=np.float64).reshape(4, 4)[3, :2]
            if any((re.search(rx, name, re.I) and min(math.hypot(c[0] + c_.shift[0] - x, c[1] + c_.shift[1] - y), math.hypot(pl[0] + c_.shift[0] - x, pl[1] + c_.shift[1] - y)) <= r for rx, x, y, r in c_.drop_near)):
                skipped['left out by name and place (drop_near)'] += 1
                continue
            state = 'in'
            if GROUND.search(name):
                if not (a[0] < BOX[2] + FILL and b[0] > BOX[0] - FILL and (a[1] < BOX[3] + FILL) and (b[1] > BOX[1] - FILL)):
                    continue
                state = 'ground'
            elif b[0] - a[0] > 300:
                continue
            elif not (BOX[0] <= c[0] <= BOX[2] and BOX[1] <= c[1] <= BOX[3]):
                if not (BOX[0] - BACK <= c[0] <= BOX[2] + BACK and BOX[1] - BACK <= c[1] <= BOX[3] + BACK):
                    continue
                if taken is None:
                    taken = self.target_footprints()
                lo_t, hi_t = (self.to_bh(np.array([a[0], a[1], 0.0]))[:2], self.to_bh(np.array([b[0], b[1], 0.0]))[:2])
                state = 'taken' if taken(lo_t, hi_t) else 'back'
            cands.append((name, rec, m, lo, hi, refs, a, b, state))
        if c_.whole_buildings:
            cands = self.whole_buildings(cands, log)
        seen_inst = set()
        for name, rec, m, lo, hi, refs, a, b, state in cands:
            if state == 'taken':
                skipped['backdrop: the target has something there'] += 1
                continue
            if state == 'back':
                skipped['backdrop (outside the box)'] += 1
            got = Ms.best(refs, lo, hi)
            if got is None:
                skipped['no mesh'] += 1
                continue
            if got[2] != 1.0:
                if not SKIN_OK.search(name):
                    skipped['skinned (left out)'] += 1
                    continue
                skipped['skinned (static, fitted to its box)'] += 1
            Mx_ = np.array(m, dtype=np.float64).reshape(4, 4)
            if c_.dedupe:
                key = (got[0], tuple(np.round(Mx_.ravel(), 2)))
                if key in seen_inst:
                    skipped['duplicate (same mesh, same place)'] += 1
                    continue
                seen_inst.add(key)
            out.append((name, rec, got[0], Mx_, got[2]))
        if log:
            log(f'SP objects in the box: {len(out)} (skipped {dict(skipped)})')
        return (out, A)

    @staticmethod
    def whole_buildings(cands, log=print, share=0.5):
        idx = [i for i, cnd in enumerate(cands) if cnd[8] != 'ground' and BUILDING_PART.search(cnd[0]) and (not NOT_PART.search(cnd[0]))]
        par = {i: i for i in idx}

        def find(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i
        B = np.array([[cands[i][6][0], cands[i][6][1], cands[i][7][0], cands[i][7][1]] for i in idx]).reshape(-1, 4)
        area = np.maximum((B[:, 2] - B[:, 0]) * (B[:, 3] - B[:, 1]), 1e-06)
        for k, i in enumerate(idx):
            ix = np.clip(np.minimum(B[:, 2], B[k, 2]) - np.maximum(B[:, 0], B[k, 0]), 0, None)
            iy = np.clip(np.minimum(B[:, 3], B[k, 3]) - np.maximum(B[:, 1], B[k, 1]), 0, None)
            for j in np.nonzero(ix * iy >= share * np.minimum(area, area[k]))[0]:
                if j != k:
                    a_, b_ = (find(i), find(idx[j]))
                    if a_ != b_:
                        par[a_] = b_
        groups = defaultdict(list)
        for i in idx:
            groups[find(i)].append(i)
        out = list(cands)
        n_in = n_out = 0
        for members in groups.values():
            states = {cands[i][8] for i in members}
            if 'in' in states:
                for i in members:
                    if cands[i][8] != 'in':
                        out[i] = cands[i][:8] + ('in',)
                        n_in += 1
            elif 'taken' in states:
                for i in members:
                    if cands[i][8] == 'back':
                        out[i] = cands[i][:8] + ('taken',)
                        n_out += 1
        if log:
            log(f'  whole buildings: {len(groups)} buildings of {len(idx)} parts; {n_in} parts brought in with their building, {n_out} left out with theirs')
        return out

    def removals(self, path, poly):
        from ac3editor import mapdata as M
        d = M.load(path, use_cache=False)
        in_modes = {e.uid for e in d.entities if e.record.startswith('DataBlock_')}
        ring = self.cfg.remove_ring
        out, seen = ([], set())
        parts = []
        for e in d.entities:
            name = e.name or ''
            layout = LAYOUT.search(name) or LAYOUT.search(e.group or '')
            if not e.record.startswith('Cell') or not name or e.uid in in_modes or (e.uid in seen):
                continue
            m = np.array(e.matrix, dtype=np.float64).reshape(4, 4)
            if self.keep.search(name):
                if ring and (not self.cfg.replace_target) and STANDIN.search(name) and (e.mesh in d.meshes):
                    v = d.meshes[e.mesh][1]
                    W = (np.c_[v, np.ones(len(v))] @ m)[:, :3]
                    if near_poly(W[:, :2], poly, -1.0).any() or any((W[:, 0].min() <= x <= W[:, 0].max() and W[:, 1].min() <= y <= W[:, 1].max() for x, y in poly)):
                        out.append((e.uid, e.name))
                        seen.add(e.uid)
                continue
            if SYSTEMS.search(name) and (not layout) or not (e.mesh or e.shapes or layout or ('Visual' in e.components)):
                continue
            if e.mesh and e.mesh in d.meshes:
                v = d.meshes[e.mesh][1]
                W = (np.c_[v, np.ones(len(v))] @ m)[:, :3]
                lo, hi = (W.min(0), W.max(0))
                if (hi - lo)[:2].max() > 300 and (not self.cfg.replace_target):
                    continue
                c = (lo + hi) / 2
            else:
                c = m[3, :3]
                lo, hi = (c - 0.5, c + 0.5)
            chosen = self.cfg.replace_target or inside(poly, (c[0], c[1])) or (ring > 0 and edge_dist(poly, (c[0], c[1])) <= ring)
            if ring and (not self.cfg.replace_target) and BUILDING_PART.search(name) and (not NOT_PART.search(name)):
                parts.append((e.uid, e.name, lo, hi, chosen))
                continue
            if chosen:
                out.append((e.uid, e.name))
                seen.add(e.uid)
        if parts:
            B = np.array([[p[2][0], p[2][1], p[3][0], p[3][1]] for p in parts])
            area = np.maximum((B[:, 2] - B[:, 0]) * (B[:, 3] - B[:, 1]), 1e-06)
            par = list(range(len(parts)))

            def find(i):
                while par[i] != i:
                    par[i] = par[par[i]]
                    i = par[i]
                return i
            for k in range(len(parts)):
                ix = np.clip(np.minimum(B[:, 2], B[k, 2]) - np.maximum(B[:, 0], B[k, 0]), 0, None)
                iy = np.clip(np.minimum(B[:, 3], B[k, 3]) - np.maximum(B[:, 1], B[k, 1]), 0, None)
                for j in np.nonzero(ix * iy >= 0.5 * np.minimum(area, area[k]))[0]:
                    a_, b_ = (find(k), find(int(j)))
                    if a_ != b_:
                        par[a_] = b_
            gone = {find(k) for k, p in enumerate(parts) if p[4]}
            for k, p in enumerate(parts):
                if find(k) in gone and p[0] not in seen:
                    out.append((p[0], p[1]))
                    seen.add(p[0])
        return out

    def target_footprints(self):
        from ac3editor import mapdata as M
        d = M.load(self.src, log=lambda *a: None)
        gone = {u for u, _n in self.removals(self.src, self.play_area())}
        boxes = []
        for e in d.entities:
            if e.uid in gone or e.mesh not in d.meshes or self.keep.search(e.name or ''):
                continue
            m = np.array(e.matrix, np.float64).reshape(4, 4)
            v = d.meshes[e.mesh][1]
            W = v.astype(np.float64) @ m[:3, :3] + m[3, :3]
            lo, hi = (W.min(0), W.max(0))
            if lo[2] < -50 or (hi - lo)[:2].max() > 60:
                continue
            boxes.append((lo[0], lo[1], hi[0], hi[1]))
        B_ = np.array(boxes) if boxes else np.zeros((0, 4))

        def taken(lo, hi):
            if not len(B_):
                return False
            ix = np.clip(np.minimum(B_[:, 2], hi[0]) - np.maximum(B_[:, 0], lo[0]), 0, None)
            iy = np.clip(np.minimum(B_[:, 3], hi[1]) - np.maximum(B_[:, 1], lo[1]), 0, None)
            area = max((hi[0] - lo[0]) * (hi[1] - lo[1]), 1e-06)
            return float((ix * iy).max()) > 0.2 * area
        return taken

    def target_floor_mask(self, poly, log=print, res=1.0):
        from ac3editor import mapdata as M
        AREA, FILL = (self.cfg.area, self.cfg.ground_fill)
        d = M.load(self.src, log=lambda *a: None)
        gone = {u for u, _n in self.removals(self.src, poly)}
        x0, y0 = (AREA[0] - FILL, AREA[1] - FILL)
        nx = int((AREA[2] - AREA[0] + 2 * FILL) / res) + 1
        ny = int((AREA[3] - AREA[1] + 2 * FILL) / res) + 1
        lo = np.full((ny, nx), np.inf)
        hi = np.full((ny, nx), -np.inf)
        for e in d.entities:
            if e.uid in gone or e.mesh not in d.meshes:
                continue
            m = np.array(e.matrix, np.float64).reshape(4, 4)
            v, t = (d.meshes[e.mesh][1], d.meshes[e.mesh][2])
            W = v.astype(np.float64) @ m[:3, :3] + m[3, :3]
            Tw = W[t]
            nz = np.cross(Tw[:, 1] - Tw[:, 0], Tw[:, 2] - Tw[:, 0])
            up = nz[:, 2] > 0.5 * np.linalg.norm(nz, axis=1)
            for tri in Tw[up]:
                if tri[:, 2].min() < -50:
                    continue
                i0, i1 = (int((tri[:, 0].min() - x0) / res), int((tri[:, 0].max() - x0) / res))
                j0, j1 = (int((tri[:, 1].min() - y0) / res), int((tri[:, 1].max() - y0) / res))
                if i1 < 0 or j1 < 0 or i0 >= nx or (j0 >= ny) or ((i1 - i0) * (j1 - j0) > 4000):
                    continue
                i0, j0, i1, j1 = (max(i0, 0), max(j0, 0), min(i1, nx - 1), min(j1, ny - 1))
                np.minimum(lo[j0:j1 + 1, i0:i1 + 1], tri[:, 2].min(), out=lo[j0:j1 + 1, i0:i1 + 1])
                np.maximum(hi[j0:j1 + 1, i0:i1 + 1], tri[:, 2].max(), out=hi[j0:j1 + 1, i0:i1 + 1])
        if log:
            log(f'  target floor kept around the play area: {np.isfinite(lo).mean():.0%} of the fill box')

        def has_floor(x, y, z):
            i = np.clip(((np.asarray(x) - x0) / res).astype(int), 0, nx - 1)
            j = np.clip(((np.asarray(y) - y0) / res).astype(int), 0, ny - 1)
            return (lo[j, i] - 1.5 <= z) & (z <= hi[j, i] + 1.5)
        return has_floor

    def mp_twins(self, objs, A, log=print, tol=0.35):
        from ac3editor import mapdata as M
        d = M.load(self.src, log=lambda *a: None)
        ok = {'Visual', 'GuidanceSystem', 'InertComponent', 'FXComponent'}
        cands = defaultdict(list)
        for e in d.entities:
            if e.mesh in d.meshes and e.name and e.shapes and e.record.startswith('Cell') and (set(e.components) <= ok):
                nm = re.sub('/[0-9]+$', '', e.name)
                nm = re.sub(' ?Group(_[0-9]+)*$', '', nm)
                k = re.sub(self.cfg.mp_prefix, '', re.sub('(_[0-9]+)+$', '', nm)).lower()
                v = d.meshes[e.mesh][1]
                cands[k].append((e.uid, v.min(0), v.max(0)))
        out = {}
        for i, (name, _rec, mu, _Mx, sc) in enumerate(objs):
            if sc != 1.0 or GROUND.search(name) or TWIN_SKIP.search(name):
                continue
            parts = A.mesh_parts(mu)
            if not parts:
                continue
            V = np.concatenate([p['verts'] for p in parts])
            lo, hi = (V.min(0), V.max(0))
            k = re.sub(self.cfg.sp_prefix, '', re.sub('(_[0-9]+)+$', '', name)).lower()
            best = None
            for kk in (k, k + 'a'):
                for uid, l2, h2 in cands.get(kk, []):
                    dd = max(float(np.abs(lo - l2).max()), float(np.abs(hi - h2).max()))
                    if best is None or dd < best[0]:
                        best = (dd, uid)
            if best and best[0] <= tol:
                out[i] = best[1]
        if log:
            log(f'  MP twins: {len(out)} SP objects have a target version of the same size')
        return out

    def mp_twin_data(self, uids):
        from ac3editor import climb as CL, mapdata as M
        S = M.schema()
        C = M.Codec(S)
        d = M.load(self.src, log=lambda *a: None)
        shape_of = {e.uid: e.shapes[0] for e in d.entities if e.uid in uids and e.shapes}
        out = {}
        for _rec, _name, t, body, _l in M.iter_objects_ex(self.src):
            if S.name_of(t) not in ('Entity', 'EntityGroup'):
                continue
            try:
                o = C.decode(body).obj
            except Exception:
                continue
            for e in [o] + [M._obj(r) for r in o.fields.get('Entities', [])]:
                if e is None or e.id not in uids or e.id in out or (shape_of.get(e.id) not in d.shapes):
                    continue
                edges = []
                for c in e.fields.get('Components', []):
                    cf = M._obj(c)
                    if cf is not None and S.name_of(cf.type_hash) == 'GuidanceSystem' and cf.fields.get('GuidanceObjects'):
                        pts, es = CL.read(cf.fields)
                        edges = [(pts[a], pts[b], n0, n1, st) for a, b, st, n0, n1 in es if np.linalg.norm(pts[b] - pts[a]) > 0.001 and np.linalg.norm(np.cross(n0, n1)) > 0.05]
                _n, v, tr = d.shapes[shape_of[e.id]]
                out[e.id] = (np.asarray(v, np.float64), np.asarray(tr, np.int64), edges)
        missing = set(uids) - set(out)
        if missing:
            raise SystemExit(f'MP twins without collision data: {len(missing)}')
        return out

    def mp_materials(self, path):
        from ac3editor import mapdata as M
        d = M.load(path, use_cache=False)
        used = {m for _u, (_uv, _p, mats) in d.mesh_extra.items() for m in mats if m}
        twins, base = ({}, None)
        for m, (name, _dds) in d.materials.items():
            if m in used:
                twins.setdefault(mat_key(name), m)
                if name.strip() == self.cfg.base_name:
                    base = m
        if base is None:
            raise SystemExit(f'{self.cfg.base_name} not in the map')
        return (twins, base)

    def ground_map(self):
        import json
        from PIL import Image
        c_ = self.cfg
        stem = os.path.splitext(c_.sp_map)[0].replace('DataPC_DX11_AC3_', '')
        meta = os.path.join(T.TOPVIEW, f'{stem}_{c_.height_label}.json')
        if not os.path.exists(meta):
            T.render(c_.sp_map, c_.height_region, c_.height_label, px_per_m=2)
        m = json.load(open(meta, encoding='utf-8'))
        Hm = np.array(Image.open(m['height_png']).convert('RGB')).astype(np.int64)
        z = (Hm[:, :, 0] * 65536 + Hm[:, :, 1] * 256 + Hm[:, :, 2]) / 100.0 - 1000.0
        z[Hm.sum(2) == 0] = np.nan
        k = m['px_per_m']
        SHIFT = c_.shift

        def win(x, y, r):
            sx, sy = (x - SHIFT[0], y - SHIFT[1])
            X0, X1 = (int((sx - r - m['x0']) * k), int((sx + r - m['x0']) * k) + 1)
            Y0, Y1 = (int((m['y1'] - sy - r) * k), int((m['y1'] - sy + r) * k) + 1)
            return z[max(Y0, 0):max(Y1, 0), max(X0, 0):max(X1, 0)]

        def top(x, y):
            w = win(x, y, 0.3)
            return None if w.size == 0 or np.isnan(w).all() else float(np.nanmax(w)) + SHIFT[2]

        def open_(x, y, r=1.2):
            w = win(x, y, r)
            if w.size == 0 or np.isnan(w).any():
                return False
            street = np.nanpercentile(win(x, y, 8.0), 10)
            return float(w.max() - w.min()) < 0.35 and float(w.max()) < street + 0.5
        return (top, open_)

    def by_hole(self, p, margin=3.0):
        return any((edge_dist(h, p) < margin or inside(h, p) for h in self.cfg.water_holes))

    def point_moves(self, path, poly, log=print):
        from ac3editor import mapdata as M
        d = M.load(path, use_cache=False)
        top, open_ = self.ground_map()
        moves, seen, kept = ([], set(), 0)
        cand = None
        for e in d.entities:
            if not e.record.startswith('DataBlock_') or NOT_POINTS.search(e.name) or e.uid in seen:
                continue
            seen.add(e.uid)
            m = np.array(e.matrix, dtype=np.float64).reshape(4, 4)
            x, y, zz = m[3, :3]
            if abs(x) + abs(y) < 0.01 or zz < -100 or (not inside(poly, (x, y))):
                continue
            if open_(x, y) and edge_dist(poly, (x, y)) >= 1.0 and (not self.by_hole((x, y))):
                kept += 1
                continue
            if cand is None:
                xs = [c[0] for c in poly]
                ys = [c[1] for c in poly]
                cand = [(gx, gy) for gx in np.arange(min(xs), max(xs), 1.0) for gy in np.arange(min(ys), max(ys), 1.0) if inside(poly, (gx, gy)) and edge_dist(poly, (gx, gy)) >= 3.0 and open_(gx, gy) and (not self.by_hole((gx, gy)))]
                cand = np.array(cand)
                if log:
                    log(f'  {len(cand)} open street spots (1 m grid)')
            dd = np.hypot(cand[:, 0] - x, cand[:, 1] - y)
            i = int(np.argmin(dd))
            gx, gy = cand[i]
            n = m.copy()
            n[3, :3] = (gx, gy, top(gx, gy) or 0.0)
            moves.append((e.uid, n, e.name, (round(x, 1), round(y, 1)), (round(float(gx), 1), round(float(gy), 1))))
        if log:
            log(f'  points: {kept} already on open ground, {len(moves)} to move')
        return moves

    def old_areas(self):
        from ac3editor import boundary
        own, main = ({}, None)
        for a in boundary.areas(self.src).values():
            if a['kind'] != 'oob' or not a.get('closed'):
                continue
            c = [tuple(map(float, p)) for p in a['corners']]
            if a['name'] == self.cfg.area_name:
                main = c
            for r in a['records']:
                own[r] = c
        return (own, main)

    def stretch(self):
        _own, old = self.old_areas()
        O, N = (np.array(old), np.array(self.play_area()))
        olo, ohi, nlo, nhi = (O.min(0), O.max(0), N.min(0), N.max(0))
        return lambda p: nlo + (np.asarray(p, np.float64) - olo) / (ohi - olo) * (nhi - nlo)

    def redraw_boundaries(self, src, dest, log=print):
        from ac3editor import boundary
        info = boundary.areas(src)
        f = self.stretch()
        polys, names = ({}, set())
        for uid, a in info.items():
            if a['kind'] == 'oob' and a['name'] in self.cfg.boundary:
                c = np.asarray(self.cfg.boundary[a['name']], np.float64)
                if boundary.signed_area(a['corners']) * boundary.signed_area(c) < 0:
                    c = c[::-1]
                polys[uid] = c
                names.add(a['name'])
            elif a['kind'] == 'zone':
                polys[uid] = [f(p) for p in a['polygons']]
        missing = set(self.cfg.boundary) - names
        if missing:
            raise SystemExit(f"Config.boundary names areas the map hasn't: {sorted(missing)}")
        for uid, p in polys.items():
            a = info[uid]
            sz = abs(boundary.signed_area(p)) if a['kind'] == 'oob' else sum((abs(boundary.signed_area(q)) for q in p))
            log(f"   {a['kind']} {a['name']} ({', '.join((r.replace('DataBlock_', '') for r in a['records'][:3]))}{('...' if len(a['records']) > 3 else '')}): {sz:.0f} m2")
        if not boundary.rebuild(src, dest, polys, log=lambda *_a: None):
            raise SystemExit("boundary.rebuild touched records it shouldn't")
        return len(polys)

    def scaled_point_moves(self, path, poly, log=print, apart=2.5):
        from ac3editor import boundary, mapdata as M
        d = M.load(path, use_cache=False)
        top, open_ = self.ground_map()
        old_own, old_main = self.old_areas()
        new_own = {}
        for a in boundary.areas(path).values():
            if a['kind'] == 'oob' and a.get('closed'):
                for r in a['records']:
                    new_own[r] = [tuple(map(float, c)) for c in a['corners']]
        blocks = defaultdict(set)
        for e in d.entities:
            if e.record.startswith('DataBlock_'):
                blocks[e.uid].add(e.record)
        size = lambda P_: float(np.prod(np.ptp(np.array(P_), 0)))
        spots_of = {}

        def spots(P_):
            k = tuple(P_)
            if k not in spots_of:
                A_ = np.array(P_)
                (x0, y0), (x1, y1) = (A_.min(0), A_.max(0))
                c = [(gx, gy) for gx in np.arange(x0, x1, 1.0) for gy in np.arange(y0, y1, 1.0) if inside(P_, (gx, gy)) and edge_dist(P_, (gx, gy)) >= 3.0 and (not self.by_hole((gx, gy))) and open_(gx, gy)]
                spots_of[k] = np.array(c)
                if log:
                    log(f'  {len(c)} open street spots in a {abs(boundary.signed_area(P_)):.0f} m2 area (1 m grid)')
            return spots_of[k]
        taken = defaultdict(list)
        moves, seen = ([], set())
        for e in d.entities:
            if not e.record.startswith('DataBlock_') or NOT_POINTS.search(e.name) or e.uid in seen:
                continue
            seen.add(e.uid)
            m = np.array(e.matrix, dtype=np.float64).reshape(4, 4)
            x, y, zz = m[3, :3]
            if abs(x) + abs(y) < 0.01 or zz < -100 or (not inside(old_main, (x, y))):
                continue
            rec = min(sorted(blocks[e.uid]), key=lambda r: size(new_own.get(r, poly)))
            O = np.array(old_own.get(rec, old_main))
            N_ = new_own.get(rec, poly)
            Nn = np.array(N_)
            want = Nn.min(0) + (np.array([x, y]) - O.min(0)) / np.ptp(O, 0) * np.ptp(Nn, 0)
            sp = spots(N_)
            dd = np.hypot(sp[:, 0] - want[0], sp[:, 1] - want[1])
            for prev in taken[rec]:
                dd[np.hypot(sp[:, 0] - prev[0], sp[:, 1] - prev[1]) < apart] = np.inf
            i = int(np.argmin(dd))
            taken[rec].append(sp[i])
            gx, gy = sp[i]
            n = m.copy()
            n[3, :3] = (gx, gy, top(gx, gy) or 0.0)
            moves.append((e.uid, n, e.name, (round(x, 1), round(y, 1)), (round(float(gx), 1), round(float(gy), 1))))
        if log:
            log(f'  points: {len(moves)} spread onto the new areas')
        return moves

    def root_cell(self):
        from ac3editor import mapdata as M
        names = {e.record for e in M.load(self.src, log=lambda *a: None).entities}
        return max((n for n in names if n.startswith('Cell') and n.endswith('_DataBlock') and n[4:9].isdigit()), key=lambda n: int(n[4:9]))

    def far_view(self, A, mats, log=print, tile=256.0, near=frozenset()):
        c_ = self.cfg
        d = T.index(c_.sp_map, log=None)
        Ms = T.Meshes(A.f, d['owner'])
        Ms.fit_slack = c_.mesh_fit_slack
        cx, cy = ((c_.box[0] + c_.box[2]) / 2, (c_.box[1] + c_.box[3]) / 2)
        root = self.root_cell()
        groups = defaultdict(lambda: [[], [], [], []])
        n_obj, skipped = (0, Counter())
        for name, _rec, m, lo, hi, refs in d['entities']:
            extra = c_.select_visible and FAR_EXTRA.search(name) is not None
            if not refs or (not extra and (SEL_SKIP if c_.select_visible else T.SKIP).search(name)):
                continue
            ground = re.search('_Ground_.*_LOD1_Chunk_', name, re.I) is not None
            if not ground and (not extra) and (not BUILDING_PART.search(name) or NOT_PART.search(name) or T.LOWER_LOD.search(name)):
                continue
            a, b = T.world_box(m, lo, hi)
            if b[2] < (c_.min_top if c_.min_top is not None else -1000000000.0):
                continue
            r_ = math.hypot((a[0] + b[0]) / 2 - cx, (a[1] + b[1]) / 2 - cy)
            if not extra and (r_ > c_.far_radius or (not ground and c_.far_lod_radius and (r_ > c_.far_lod_radius))):
                continue
            bh = self.to_bh(np.array([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0.0]))
            if c_.far_window and max(abs(bh[0]), abs(bh[1])) <= c_.far_window:
                skipped['inside the load window (full detail there)'] += 1
                continue
            if near and (name, tuple(np.round(np.array(m, dtype=np.float64).reshape(4, 4)[3, :3], 1))) in near:
                skipped['already in at full detail'] += 1
                continue
            g0 = self.cfg.grid
            in_grid = g0[0] <= bh[0] <= -g0[0] and g0[1] <= bh[1] <= -g0[1]
            got = coarsest_mesh(Ms, refs, lo, hi) if extra else lod_mesh(Ms, refs, lo, hi, c_.far_lod) if c_.far_lod and (not ground) else lod_mesh(Ms, refs, lo, hi, c_.far_grid_lod) if in_grid and c_.far_grid_lod else coarsest_mesh(Ms, refs, lo, hi)
            if got is None:
                skipped['no static mesh'] += 1
                continue
            parts = A.mesh_parts(got)
            if not parts:
                skipped['no parts'] += 1
                continue
            n_obj += 1
            Mx = np.array(m, dtype=np.float64).reshape(4, 4)
            for p in parts:
                if p['material'] not in mats:
                    tx = sp_texture(A, p['material'])
                    mo = A.obj(p['material']) if p['material'] else None
                    mats[p['material']] = (mo['name'] if mo else 'none', tx[1] if tx else None)
                    if c_.flat_multi and tx and mo and mo['name'].lower().endswith('_multi'):
                        mats[p['material']] = (mo['name'], bake_layers(A, p['material'], tx[1]))
                if DROP.search(mats[p['material']][0]) or not len(p['tris']):
                    continue
                W = self.to_bh((np.c_[p['verts'], np.ones(len(p['verts']))] @ Mx)[:, :3])
                tri = np.asarray(p['tris'])
                if ground and c_.far_ground_gap:
                    cen_ = W[tri].mean(1)
                    keep_ = ~near_poly(cen_[:, :2], self.play_area(), c_.ground_fill - c_.far_ground_gap)
                    if not keep_.any():
                        continue
                    used_, inv_ = np.unique(tri[keep_], return_inverse=True)
                    W, tri = (W[used_], inv_.reshape(-1, 3))
                    p = dict(p, uv=p['uv'][used_], color=p['color'][used_] if p.get('color') is not None else None, blend=p['blend'][used_] if p.get('blend') is not None else None, normals=p['normals'][used_] if p.get('normals') is not None else None)
                    W = W - np.array([0.0, 0.0, c_.far_ground_drop])
                elif ground:
                    W = W - np.array([0.0, 0.0, c_.far_erode])
                elif c_.far_erode <= 0:
                    pass
                else:
                    fn = np.cross(W[tri[:, 1]] - W[tri[:, 0]], W[tri[:, 2]] - W[tri[:, 0]])
                    vn = np.zeros_like(W)
                    for k in range(3):
                        np.add.at(vn, tri[:, k], fn)
                    ln = np.linalg.norm(vn, axis=1, keepdims=True)
                    nv = np.where(ln > 1e-09, vn / np.maximum(ln, 1e-09), 0.0)
                    W = W - np.c_[nv[:, 0] * c_.far_erode, nv[:, 1] * c_.far_erode, np.maximum(nv[:, 2], 0.0) * c_.far_erode * 0.5]
                c = W.mean(0)
                key = (int(math.floor(c[0] / tile)), int(math.floor(c[1] / tile)), p['material'])
                g = groups[key]
                g[1].append(tri + sum((len(v) for v in g[0])))
                g[0].append(W)
                g[2].append(p['uv'])
                g[3].append(np.c_[np.asarray(p['color'] if p.get('color') is not None else np.full((len(W), 4), 255, np.uint8), np.uint8)[:, :4], np.asarray(p['blend'] if p.get('blend') is not None else np.zeros(len(W), np.uint8), np.uint8), normal_bytes(p, Mx, len(W), c_.sp_normals)])
        out = []
        for (tx_, ty_, mat), (vs, ts, uvs, cols) in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], str(kv[0][2]))):
            V, Tr, U, Cl = (np.concatenate(vs), np.concatenate(ts), np.concatenate(uvs), np.concatenate(cols))
            o = np.array([(tx_ + 0.5) * tile, (ty_ + 0.5) * tile, 0.0])
            for chunk, (cv, ct, cu, cc) in enumerate(split(V - o, Tr, U, Cl)):
                out.append(dict(name=f'{c_.prefix}F_{tx_}_{ty_}_{mats[mat][0][:28]}_{chunk}', tile=None, sp_material=mat, matrix=at(o), verts=cv, tris=ct, uv=cu, color=cc, climb=False, facade='N', record=root))
        if log:
            log(f"  far view: {n_obj} SP objects at their far LOD (grid: LOD {c_.far_grid_lod or 'coarsest'}) within {c_.far_radius:.0f} m -> {len(out)} root-cell models ({root}), {sum((len(m_['tris']) for m_ in out))} triangles (skipped {dict(skipped)})")
        return out

    def tunnel_floor(self, models, solids, mats, poly, log=print, open_r=1.0):
        tris, ent = ([], [])
        for mdl in models:
            if NOSOLID.search(mats[mdl['sp_material']][0] or '') or mdl.get('facade') == 'T':
                continue
            tris.append((mdl['verts'] + mdl['matrix'][3, :3])[mdl['tris']])
            ent.append(np.full(len(mdl['tris']), len(ent)))
        for s in solids:
            M_ = s['matrix']
            tris.append((np.asarray(s['verts'], np.float64) @ M_[:3, :3] + M_[3, :3])[np.asarray(s['tris'])])
            ent.append(np.full(len(s['tris']), len(ent)))
        AREA = self.cfg.area
        t0 = time.time()
        g = walk_grid(np.concatenate(tris), np.concatenate(ent), AREA)
        piece = walk_pieces(g)
        sizes = np.bincount(piece) * 0.25
        main = piece == 0
        res = 0.5
        rr = int(round(open_r / res))
        offs = [(dx, dy) for dx in range(-rr, rr + 1) for dy in range(-rr, rr + 1) if dx * dx + dy * dy <= rr * rr]
        spots = []
        for i in np.nonzero(main)[0]:
            gx, gy, z = (int(g.gx[i]), int(g.gy[i]), g.z[i])
            if not all((any((abs(g.z[j] - z) <= 0.25 for j in g.at.get(g.key(gx + dx, gy + dy), ()))) for dx, dy in offs if g.g0[0] <= gx + dx < g.g0[0] + g.nx and g.g0[1] <= gy + dy < g.g0[1] + g.ny)):
                continue
            p = ((gx + 0.5) * res, (gy + 0.5) * res)
            if inside(poly, p) and edge_dist(poly, p) >= 3.0:
                spots.append((p[0], p[1], float(z)))
        spots = np.array(spots)
        if log:
            log(f'  tunnel floor: {g.n * 0.25:.0f} m2 walkable in {len(sizes)} pieces (largest {sizes[:5].tolist()} m2); main piece z {g.z[main].min():.1f}..{g.z[main].max():.1f}; {len(spots)} open spots for points ({time.time() - t0:.0f} s)')
        return dict(spots=spots, zlo=float(g.z.min()), zhi=float(g.z.max()), sizes=sizes, floor=np.c_[(g.gx[main] + 0.5) * res, (g.gy[main] + 0.5) * res, g.z[main]])

    def caps(self, poly, zlo, zhi, piece=16.0, thick=0.6):
        out = []
        for i in range(len(poly)):
            a, b = (np.array(poly[i - 1], np.float64), np.array(poly[i], np.float64))
            L = float(np.linalg.norm(b - a))
            if L < 0.05:
                continue
            n = max(1, int(math.ceil(L / piece)))
            d = (b - a) / L
            nrm = np.array([-d[1], d[0]])
            for k in range(n):
                p, q = (a + d * L * k / n, a + d * L * (k + 1) / n)
                c = (p + q) / 2
                t = self.tile_of(*c)
                o = self.tile_origin(t)[:2]
                corners = [p - nrm * thick / 2, q - nrm * thick / 2, q + nrm * thick / 2, p + nrm * thick / 2]
                v = np.array([[x - o[0], y - o[1], z] for z in (zlo, zhi) for x, y in corners])
                tr = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
                cen = v.mean(0)
                fixed = []
                for tri in tr:
                    q_ = v[tri]
                    fixed.append(tri if np.dot(np.cross(q_[1] - q_[0], q_[2] - q_[0]), q_.mean(0) - cen) >= 0 else tri[::-1])
                out.append(dict(name=f'{self.cfg.prefix}_Cap_{len(out)}', matrix=at(self.tile_origin(t)), verts=v, tris=np.array(fixed), uv=None, visible=False, climb=False))
        return out

    def tunnel_point_moves(self, path, poly, spots, log=print, apart=2.5):
        from ac3editor import boundary, mapdata as M
        d = M.load(path, use_cache=False)
        own = {}
        for a in boundary.areas(path).values():
            if a['kind'] == 'oob' and a.get('closed'):
                for r in a['records']:
                    own[r] = [tuple(map(float, c)) for c in a['corners']]
        frames = {}

        def frame(rec):
            bp = own.get(rec, poly)
            key = tuple(bp)
            if key not in frames:
                ok = np.array([inside(bp, (x, y)) and edge_dist(bp, (x, y)) >= 3.0 for x, y, _z in spots]) if bp != list(poly) else np.ones(len(spots), bool)
                sp = spots[ok] if ok.any() else spots
                B = np.array(bp)
                frames[key] = (sp, B.min(0), B.max(0), sp[:, :2].min(0), sp[:, :2].max(0))
            return frames[key]
        blocks = defaultdict(set)
        for e in d.entities:
            if e.record.startswith('DataBlock_'):
                blocks[e.uid].add(e.record)

        def area_size(rec):
            B = np.array(own.get(rec, poly))
            return float(np.prod(B.max(0) - B.min(0)))
        taken = defaultdict(list)
        moves, seen = ([], set())
        for e in d.entities:
            if not e.record.startswith('DataBlock_') or NOT_POINTS.search(e.name) or e.uid in seen:
                continue
            seen.add(e.uid)
            m = np.array(e.matrix, dtype=np.float64).reshape(4, 4)
            x, y, zz = m[3, :3]
            if abs(x) + abs(y) < 0.01 or zz < -100 or (not inside(poly, (x, y))):
                continue
            rec = min(sorted(blocks[e.uid]), key=area_size)
            spots_b, plo, phi, slo, shi = frame(rec)
            want = slo + (np.array([x, y]) - plo) / np.maximum(phi - plo, 1e-06) * (shi - slo)
            dd = np.hypot(spots_b[:, 0] - want[0], spots_b[:, 1] - want[1])
            for prev in taken[rec]:
                dd[np.hypot(spots_b[:, 0] - prev[0], spots_b[:, 1] - prev[1]) < apart] = np.inf
            i = int(np.argmin(dd))
            taken[rec].append(spots_b[i, :2])
            n = m.copy()
            n[3, :3] = spots_b[i]
            moves.append((e.uid, n, e.name, (round(x, 1), round(y, 1)), (round(float(spots_b[i, 0]), 1), round(float(spots_b[i, 1]), 1))))
        if log:
            log(f'  points: {len(moves)} moved into the tunnels')
        return moves

    def plan(self, log=print):
        c_ = self.cfg
        pre = c_.prefix
        to_bh, tile_of, tile_origin = (self.to_bh, self.tile_of, self.tile_origin)
        objs, A = self.sp_objects(log)
        twins = self.mp_twins(objs, A, log) if c_.mp_twins else {}
        twin_data = self.mp_twin_data(set(twins.values()))
        twin_solids = []
        perches_by_tile = defaultdict(list)
        interiors = defaultdict(lambda: [[], []])
        poly = np.array(self.play_area())
        floor = self.target_floor_mask(poly, log)
        groups = defaultdict(lambda: [[], [], [], []])
        layers2 = {}
        params2 = {}
        dropped = Counter()
        mats = {}
        holds_by_tile = defaultdict(list)
        building_walls = 0
        t0 = time.time()
        for k, (name, _rec, mu, Mx, scale) in enumerate(objs):
            twin = k in twins
            if twin:
                m_bh = Mx.copy()
                m_bh[3, :3] = to_bh(Mx[3, :3])
                cv, ct, ce = twin_data[twins[k]]
                twin_solids.append(dict(name=f'{pre}_Twin_{len(twin_solids)}_{name[:30]}', matrix=m_bh, verts=cv, tris=ct, uv=None, visible=False, climb=False, edges=ce))
            parts = A.mesh_parts(mu, skin_scale=scale if scale != 1.0 else None)
            if not parts:
                continue
            if c_.interiors and BUILDING.search(name) and (not NO_FACADE.search(name)):
                box = interior_box([(p['verts'], p['tris']) for p in parts])
                if box is not None:
                    bv, bt = box
                    W = to_bh((np.c_[bv, np.ones(len(bv))] @ Mx)[:, :3])
                    t = tile_of(*W.mean(0)[:2])
                    g = interiors[t]
                    g[1].append(bt + sum((len(v) for v in g[0])))
                    g[0].append(W - tile_origin(t))
            ground = GROUND.search(name) is not None
            if c_.prop_boxes and (not twin) and (scale == 1.0) and (not ground) and PROP_BOX.search(name) and (not BUILDING.search(name)) and (not ROOF.search(name)):
                V_ = np.concatenate([p['verts'] for p in parts])
                lo_, hi_ = (V_.min(0), V_.max(0))
                if (hi_ - lo_).max() <= PROP_MAX and (hi_ - lo_)[2] > 0.15:
                    bv, bt = slab(lo_[0], lo_[1], hi_[0], hi_[1], lo_[2], hi_[2])
                    m_bh = Mx.copy()
                    m_bh[3, :3] = to_bh(Mx[3, :3])
                    twin_solids.append(dict(name=f'{pre}_PropBox_{len(twin_solids)}_{name[:24]}', matrix=m_bh, verts=bv, tris=bt, uv=None, visible=False, climb=hi_[2] - lo_[2] > 0.6, edges=None))
                    twin = True
            facade = 'T' if twin else 'N' if FENCE.search(name) else 'W' if c_.tunnels and (not PROPS.search(name)) else bool(BUILDING.search(name) or ROOF.search(name)) and (not NO_FACADE.search(name))
            if WELL.search(name):
                V_ = np.concatenate([p['verts'] for p in parts])
                shaft = V_[V_[:, 2] < -0.5]
                if len(shaft):
                    lo_, hi_ = (shaft.min(0), shaft.max(0))
                    bv, bt = slab(lo_[0] - 0.05, lo_[1] - 0.05, hi_[0] + 0.05, hi_[1] + 0.05, -0.25, 0.0)
                    m_bh = Mx.copy()
                    m_bh[3, :3] = to_bh(Mx[3, :3])
                    twin_solids.append(dict(name=f'{pre}_WellFloor_{len(twin_solids)}', matrix=m_bh, verts=bv, tris=bt, uv=None, visible=False, climb=False, edges=None))
            if facade == 'N':
                for a_, b_ in fence_tops([(p['verts'], p['tris']) for p in parts]):
                    A_ = to_bh((np.r_[a_, 1.0] @ Mx)[:3])
                    B__ = to_bh((np.r_[b_, 1.0] @ Mx)[:3])
                    tt = tile_of(*((A_ + B__) / 2)[:2])
                    perches_by_tile[tt].append((A_ - tile_origin(tt), B__ - tile_origin(tt)))
            for p in parts:
                if p['material'] not in mats:
                    tx = sp_texture(A, p['material'])
                    mo = A.obj(p['material']) if p['material'] else None
                    mats[p['material']] = (mo['name'] if mo else 'none', tx[1] if tx else None)
                if DROP.search(mats[p['material']][0]):
                    dropped[mats[p['material']][0]] += len(p['tris'])
                    continue
                W = to_bh((np.c_[p['verts'], np.ones(len(p['verts']))] @ Mx)[:, :3])
                if ground:
                    cen = W[p['tris']].mean(1)
                    keep = near_poly(cen[:, :2], poly, GROUND_MARGIN) | near_poly(cen[:, :2], poly, c_.ground_fill) & ~floor(cen[:, 0], cen[:, 1], cen[:, 2])
                    if not keep.any():
                        continue
                    used, inv = np.unique(p['tris'][keep], return_inverse=True)
                    W, p = (W[used], dict(p, tris=inv.reshape(-1, 3), uv=p['uv'][used], color=p['color'][used] if p.get('color') is not None else None, blend=p['blend'][used] if p.get('blend') is not None else None, normals=p['normals'][used] if p.get('normals') is not None else None))
                c = W.mean(0)
                t = tile_of(c[0], c[1])
                key = (t, p['material'], facade)
                g = groups[key]
                o = tile_origin(t)
                g[1].append(np.asarray(p['tris']) + sum((len(v) for v in g[0])))
                g[0].append(W - o)
                g[2].append(p['uv'])
                if p.get('color') is None or WINDOW.search(mats[p['material']][0]):
                    g[3].append(np.full((len(W), 4), 255, np.uint8))
                else:
                    g[3].append(p['color'])
                bl = p.get('blend') if p.get('blend') is not None else np.zeros(len(W), np.uint8)
                g[3][-1] = np.c_[np.asarray(g[3][-1], np.uint8)[:, :4], np.asarray(bl, np.uint8), normal_bytes(p, Mx, len(W), c_.sp_normals)]
            if (BUILDING.search(name) or ROOF.search(name)) and (not twin):
                frame = [(np.c_[p['verts'], np.ones(len(p['verts']))][:, :3], p['tris']) for p in parts]
                holds = (SP1.mp_handholds(frame, gaps=c_.hold_gaps) if BUILDING.search(name) else []) + eave_holds(frame, min_z=2.0 if BUILDING.search(name) else -1.0)
                if holds:
                    building_walls += 1
                c = to_bh(Mx[3, :3])
                t = tile_of(c[0], c[1])
                o = tile_origin(t)
                for a, b, up, wall in holds:
                    R = Mx[:3, :3]
                    holds_by_tile[t].append((a @ R + Mx[3, :3] + c_.shift - o, b @ R + Mx[3, :3] + c_.shift - o, up @ R, wall @ R))
            if log and k % 300 == 0:
                log(f'  {k}/{len(objs)} objects, {len(groups)} groups, {time.time() - t0:.0f} s')
        by_name = {n: rgba for n, rgba in mats.values() if rgba is not None}
        for u, (n, rgba) in list(mats.items()):
            if rgba is None and c_.texture_from.get(n) in by_name:
                mats[u] = (n, by_name[c_.texture_from[n]])
        models = []
        for (t, mat, fac), (vs, ts, uvs, cols) in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]), str(kv[0][2]))):
            V, Tr, U, Cl = (np.concatenate(vs), np.concatenate(ts), np.concatenate(uvs), np.concatenate(cols))
            tag = 'T_' if fac == 'T' else 'W_' if fac == 'W' else 'F_' if fac else ''
            for chunk, (cv, ct, cu, cc) in enumerate(split(V, Tr, U, Cl)):
                models.append(dict(name=f'{pre}_{t[0]}_{t[1]}_{tag}{mats[mat][0][:28]}_{chunk}', tile=t, sp_material=mat, matrix=at(tile_origin(t)), verts=cv, tris=ct, uv=cu, color=cc, climb=fac != 'N', facade=fac))
        if log:
            log(f'dropped (triangles per material): {dict(dropped)}')
        stats = dict(objects=len(objs), models=len(models), tiles=len({m['tile'] for m in models}), materials=len(mats), verts=int(sum((len(m['verts']) for m in models))), tris=int(sum((len(m['tris']) for m in models))), buildings_with_holds=building_walls, holds=int(sum((len(h) for h in holds_by_tile.values()))))
        log(f'MP twins (their collision + climb edges under SP visuals): {len(twin_solids)}; interior boxes in {len(interiors)} tiles')
        log(f'fence perches: {sum((len(v) for v in perches_by_tile.values()))}')
        tunnel = self.tunnel_floor(models, twin_solids, mats, [tuple(c) for c in poly], log) if c_.tunnels else None
        if c_.far_radius:
            near = {(n_, tuple(np.round(M_[3, :3], 1))) for n_, _r, _u, M_, _s in objs}
            models += self.far_view(A, mats, log, near=near)
        if c_.two_layer:
            for u, (n_, _rgba) in mats.items():
                if u not in layers2 and n_.lower().endswith('_multi'):
                    second_layer(A, u, layers2)
                    params2[u] = A.material_params(u)
        return dict(models=models, textures=mats, layers2=layers2, params2=params2, holds=holds_by_tile, stats=stats, twin_solids=twin_solids, perches=dict(perches_by_tile), tunnel=tunnel, interiors={t: (np.concatenate(v), np.concatenate(tr)) for t, (v, tr) in interiors.items()})

    def build(self, P, log=print, removals_only=False):
        from ac3editor import climb, collision, materials, newmodel
        from ac3editor import mapdata as M
        c_ = self.cfg
        pre, OUT, src = (c_.prefix, self.out, self.src)
        tile_origin, tile_of = (self.tile_origin, self.tile_of)
        avoid = [paths.DEFAULT_DATAPC, paths.DEFAULT_EXTRA]
        poly = self.play_area()
        os.makedirs(os.path.dirname(OUT), exist_ok=True)

        def stage(n):
            return OUT + f'.{n}.forge'
        gone = self.removals(src, poly)
        log(f'1. removing {len(gone)} target objects from the play area')
        md_src = M.load(src, log=lambda *a: None)
        mat_of = {e.uid: np.array(e.matrix, np.float64).reshape(4, 4) for e in md_src.entities}
        ent_of = {e.uid: e for e in md_src.entities}
        sunk = []
        for u, _n in gone:
            m = mat_of[u].copy()
            drop = SINK
            e = ent_of.get(u)
            if e is not None and (not e.shapes) and (e.mesh in md_src.meshes):
                top = float((md_src.meshes[e.mesh][1].astype(np.float64) @ m[:3, :3] + m[3, :3])[:, 2].max())
                drop = min(max(SINK, top + 20.0), m[3, 2] + 480.0)
            m[3, 2] -= drop
            sunk.append((u, 'move', m))
        if c_.sea_z is not None:
            for e in md_src.entities:
                if e.record.startswith('Cell') and re.search('water', e.name or '', re.I) and (e.mesh in md_src.meshes):
                    m = mat_of[e.uid].copy()
                    zs = md_src.meshes[e.mesh][1].astype(np.float64) @ m[:3, :3] + m[3, :3]
                    if np.ptp(zs[:, 2]) < 0.5 and np.ptp(zs[:, 0]) > 500:
                        m[3, 2] += c_.sea_z - float(zs[:, 2].mean())
                        sunk.append((e.uid, 'move', m))
                        log(f'   sea plane {e.name}: z {zs[:, 2].mean():.1f} -> {c_.sea_z:.1f}')
        if not collision.edit_shapes(src, stage(1), sunk, id_avoid=avoid):
            raise SystemExit('removing failed')
        if removals_only:
            dest = os.path.join(os.path.dirname(os.path.dirname(OUT)), c_.name + '_R_removals_only', c_.archive)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.replace(stage(1), dest)
            log(f'-> {dest}')
            return []
        twins, base = self.mp_materials(stage(1))
        base_alpha = next((twins[mat_key(n)] for n in c_.alpha_bases if mat_key(n) in twins), None)
        if base_alpha is None:
            raise SystemExit(f'none of the alpha bases {c_.alpha_bases} is used by a mesh of the map')
        base_foliage = next((twins[mat_key(n)] for n in c_.foliage_bases if mat_key(n) in twins), base_alpha)
        base_cards = next((twins[mat_key(n)] for n in c_.card_bases if mat_key(n) in twins), base_foliage)
        base_multi = twins.get(mat_key(c_.multi_base))
        new, key_of, twin_of, base_of = ({}, {}, {}, {})
        two_mats = set()
        decal_b = tuple((twins.get(mat_key(n)) for n in c_.decal_bases)) if c_.decal_bases else ()
        if c_.decal_bases and None in decal_b:
            raise SystemExit(f'decal bases {c_.decal_bases} not all used by a mesh of the map (R-MAT-DECAL)')
        n_decal = n_cloth = n_own = 0
        for k, (u, (name, rgba)) in enumerate(P['textures'].items()):
            if c_.mat_twins and mat_key(name) in twins and (not NO_TWIN.search(name)):
                twin_of[u] = twins[mat_key(name)]
                continue
            if rgba is None:
                continue
            rgba = rgba.copy()
            if decal_b and DECAL.search(name):
                b = decal_b[0] if PUDDLE.search(name) else decal_b[1]
                new[f'm{k}'] = dict(name=('SP_' + name)[:60], base=b, rgba=rgba, alpha_test=False)
                key_of[u], base_of[u] = (f'm{k}', b)
                n_decal += 1
                continue
            cut = base_alpha is not None and CUTOUT.search(name) is not None and (float((rgba[:, :, 3] < 128).mean()) > 0.02)
            multi = not cut and base_multi is not None and name.endswith('_Multi') and (not c_.flat_multi)
            two = c_.two_layer and (not cut) and (base_multi is not None) and (u in P.get('layers2', {}))
            if not cut and (not multi):
                if c_.fill_alpha_holes:
                    fill_holes(rgba)
                rgba[:, :, 3] = 255
            b = (base_foliage if LEAVES.search(name) else base_cards if FOLIAGE.search(name) else base_alpha) if cut else base_multi if multi else base
            new[f'm{k}'] = dict(name=('SP_' + name)[:60], base=b, rgba=rgba)
            if two:
                new[f'm{k}'] = dict(name=('SP_' + name)[:60], base=base_multi, rgba=P['textures'][u][1], rgba2=P['layers2'][u])
                own = P.get('params2', {}).get(u) if c_.two_layer_own else None
                if own:
                    n_of = {'Layer0_Color': 3, 'Layer1_Color': 3, 'PixelBlenderScale': 1, 'PixelBlenderBias': 1, 'Layer0_UVScale': 2, 'Layer1_UVScale': 2}
                    new[f'm{k}']['params'] = {nm: tuple(own[nm][:c]) for nm, c in n_of.items() if nm in own}
                    n_own += 1
                elif c_.two_layer_blend is not None:
                    new[f'm{k}'].update(blend_scale=c_.two_layer_blend[0], blend_bias=c_.two_layer_blend[1])
                b = base_multi
                two_mats.add(u)
            if c_.two_sided_cloth and CLOTH.search(name):
                new[f'm{k}']['two_sided'] = True
                n_cloth += 1
            key_of[u] = f'm{k}'
            base_of[u] = b
        log(f"   {sum((1 for b in base_of.values() if b in (base_alpha, base_foliage, base_cards)))} new materials alpha-tested (bases {base_alpha.hex()} decals / posters, {base_foliage.hex()} leaves, {base_cards.hex()} weeds / nets / hay / flags); {sum((1 for b in base_of.values() if b == base_multi))} two-layer; {n_decal} blended decals; {n_cloth} two-sided cloth; {n_own} with SP's own tints / blend / UV scales")
        new['interior'] = dict(name=f'{pre}_Interior_Dark', base=base, rgba=np.tile(np.array([26, 24, 22, 255], np.uint8), (64, 64, 1)))
        made_mats, _same = materials.apply(stage(1), stage(2), new=new, id_avoid=avoid)
        log(f'2. {len(twin_of)} SP materials use their target twin, {len(made_mats)} new ones')
        specs = []
        for mdl in P['models']:
            k = key_of.get(mdl['sp_material'])
            mat = twin_of.get(mdl['sp_material']) or (made_mats[k] if k else base)
            solid = not NOSOLID.search(P['textures'].get(mdl['sp_material'], ('',))[0] or '') and mdl.get('facade') != 'T'
            no_ledges = mdl.get('facade') in ('N', 'W')
            extra = {} if solid else {'collision': UNDER}
            if solid and mdl.get('facade') == 'W':
                v, t = (mdl['verts'], np.asarray(mdl['tris']))
                n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
                wall = np.abs(n[:, 2]) < 0.5 * np.maximum(np.linalg.norm(n, axis=1), 1e-09)
                extra = dict(collision_materials=['Wood_Plank', 'FuzzyZone'], collision_material_index=wall.astype(np.int64).tolist())
            if mdl.get('record'):
                extra = dict(collision=UNDER, record=mdl['record'], lod_distances=[5000.0])
                solid, no_ledges = (False, True)
            if 'collision' in mdl:
                extra = dict(extra, collision=mdl['collision'])
                extra.pop('collision_material_index', None)
                extra.pop('collision_materials', None)
            col = mdl.get('color')
            if col is not None and np.asarray(col).shape[1] >= 5:
                if mdl['sp_material'] in two_mats:
                    extra = dict(extra, blend=np.asarray(col)[:, 4].astype(np.float64) / 255.0)
                if np.asarray(col).shape[1] >= 8 and c_.sp_normals:
                    extra = dict(extra, normals=(np.asarray(col)[:, 5:8].astype(np.float64) - 127.5) / 127.5)
                col = np.asarray(col)[:, :4]
            specs.append(dict(name=mdl['name'], matrix=mdl['matrix'], verts=mdl['verts'], tris=mdl['tris'], uv=mdl['uv'], color=col, material=mat, base_material=base_of.get(mdl['sp_material'], base), climb=solid and (not no_ledges), **extra))
        water = None if c_.tunnels else twins.get(mat_key(c_.water))
        if c_.tunnels or P.get('tunnel_top') is not None:
            tn = P['tunnel']
            zhi = tn['zhi'] + 12.0 if c_.tunnels else P['tunnel_top']
            cap = self.caps(poly, tn['zlo'] - 4.0, zhi)
            specs += [dict(s, material=base) for s in cap]
            log(f"   {len(cap)} boundary caps (z {tn['zlo'] - 4.0:.1f}..{zhi:.1f})")
        if c_.tunnels:
            log('   no water sheet')
        elif water is not None:
            AREA, FILL = (c_.area, c_.ground_fill)
            x0, y0, x1, y1 = (AREA[0] - FILL, AREA[1] - FILL, AREA[2] + FILL, AREA[3] + FILL)
            c = np.array([(x0 + x1) / 2, (y0 + y1) / 2, 0.0])
            t = tile_of(c[0], c[1])
            o = tile_origin(t)
            n = 8
            if c_.water_holes:
                n = 64
            xs, ys = (np.linspace(x0, x1, n + 1), np.linspace(y0, y1, n + 1))
            wv = np.array([[x - o[0], y - o[1], c_.water_z] for y in ys for x in xs])

            def open_quad(i, j):
                if not c_.water_holes:
                    return True
                q = [(xs[i], ys[j]), (xs[i + 1], ys[j]), (xs[i + 1], ys[j + 1]), (xs[i], ys[j + 1])]
                for h in c_.water_holes:
                    H = np.asarray(h, np.float64)
                    if not (q[0][0] > H[:, 0].max() + 0.5 or q[1][0] < H[:, 0].min() - 0.5 or q[0][1] > H[:, 1].max() + 0.5 or (q[2][1] < H[:, 1].min() - 0.5)):
                        return False
                return True
            quads = [(i, j) for j in range(n) for i in range(n) if open_quad(i, j)]
            wt = np.array([[j * (n + 1) + i, j * (n + 1) + i + 1, (j + 1) * (n + 1) + i + 1] for i, j in quads] + [[j * (n + 1) + i, (j + 1) * (n + 1) + i + 1, (j + 1) * (n + 1) + i] for i, j in quads])
            wuv = np.array([[i * 15.0 / n, j * 15.0 / n] for j in range(n + 1) for i in range(n + 1)])
            specs.append(dict(name=f'{pre}_Water', matrix=at(o), verts=wv, tris=wt, uv=wuv, material=water, base_material=water, climb=False, collision=UNDER))
            fv = wv.copy()
            fv[:, 2] = c_.water_z - 0.8
            specs.append(dict(name=f'{pre}_WaterFloor', matrix=at(o), verts=fv, tris=wt, uv=None, material=base, visible=False, climb=False))
        else:
            log(f'   no {c_.water} in the map: no water sheet')
        if c_.sea:
            sz, half, quad, sea_mat = c_.sea
            wm = twins.get(mat_key(sea_mat))
            if wm is None:
                raise SystemExit(f'{sea_mat} not used by a mesh of the map (R-SEA-OWN)')
            k = int(np.ceil(2 * half / quad))
            gx, gy = np.meshgrid(-half + quad * np.arange(k), -half + quad * np.arange(k))
            gx, gy = (gx.ravel(), gy.ravel())
            q = len(gx)
            wv = np.concatenate([np.c_[gx + dx * quad, gy + dy * quad, np.full(q, sz)] for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))])
            rr = quad / 10.0
            wuv = np.concatenate([np.tile([dx * rr, dy * rr], (q, 1)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))])
            ii = np.arange(q)
            wt = np.r_[np.c_[ii, ii + q, ii + 2 * q], np.c_[ii, ii + 2 * q, ii + 3 * q]]
            wc = np.full((len(wv), 4), 255, np.uint8)
            root = self.root_cell()
            for j, (cv, ct, cu, _cc) in enumerate(split(wv, wt, wuv, wc)):
                specs.append(dict(name=f'{pre}_Sea_{j}', matrix=at(np.zeros(3)), verts=cv, tris=ct, uv=cu, material=wm, base_material=wm, climb=False, collision=UNDER, record=root, lod_distances=[5000.0]))
            log(f'   sea plane of our own: z {sz}, {2 * half:.0f} m across, root cell')
        if not len(P['tunnel']['spots'] if c_.tunnels else [0]):
            raise SystemExit('no open tunnel floor for the game-mode points')
        for ts in P['twin_solids']:
            specs.append(dict(ts, material=base))
        for t, (iv, it) in sorted(P['interiors'].items()):
            specs.append(dict(name=f'{pre}_{t[0]}_{t[1]}_Interior', matrix=at(tile_origin(t)), verts=iv, tris=it, uv=None, material=made_mats['interior'], base_material=base, climb=False, collision=UNDER))
        for t in sorted(set(P['holds']) | set(P['perches'])):
            holds = P['holds'].get(t, [])
            bv, bt = climb.beams(holds) if holds else UNDER
            per = [dict(a=a_, b=b_, kind='beam') for a_, b_ in P['perches'].get(t, [])]
            specs.append(dict(name=f'{pre}_{t[0]}_{t[1]}_MPHandholds', matrix=at(tile_origin(t)), verts=bv, tris=np.asarray(bt), uv=None, material=base, visible=False, climb=bool(holds), perches=per))
        log(f'3. adding {len(specs)} new objects')
        made, _same = newmodel.add_models(stage(2), stage(3), specs, id_avoid=avoid, log=lambda *_a: None)
        last = stage(3)
        if c_.boundary:
            n_b = self.redraw_boundaries(stage(3), stage(4), log)
            log(f'3b. {n_b} boundaries / zones rebuilt')
            last = stage(4)
        moves = self.tunnel_point_moves(last, poly, P['tunnel']['spots'], log) if c_.tunnels else self.scaled_point_moves(last, poly, log) if c_.boundary else self.point_moves(last, poly, log)
        if moves:
            if not collision.edit_shapes(last, OUT, [(u, 'move', m) for u, m, *_r in moves], id_avoid=avoid):
                raise SystemExit('moving the points failed')
        else:
            os.replace(last, OUT)
        for n in (1, 2, 3, 4):
            if os.path.exists(stage(n)):
                os.remove(stage(n))
        after = {e.uid: e for e in M.load(OUT, use_cache=False).entities}
        ok = sum((1 for _n, u in made if u in after and after[u].shapes))
        log(f'{ok} of {len(made)} new objects read back with collision; {len(moves)} points moved -> {OUT}')
        bad = climb_roots(OUT)
        log(f'climb systems with a RootIndex past their nodes: {len(bad)} {bad[:5]}')
        if bad:
            raise SystemExit("a climb system's RootIndex names no node (the AC3MP+0x7F1C2E crash)")
        if c_.world_water_level is not None:
            set_water_level(OUT, c_.world_water_level, log)
        if c_.standins_off:
            sink_standins(OUT, log)
        if c_.view_distance:
            set_view_distance(OUT, c_.view_distance, log)
        if c_.world_lightmap is not None:
            set_world_lightmap(OUT, c_.world_lightmap, log)
        return moves

def run(cfg, argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--removals-only', action='store_true', help='write only step 1 (a bisecting control)')
    a = ap.parse_args(argv)
    cv = Convert(cfg)
    P = cv.plan()
    print('plan:', P['stats'])
    tiles = Counter((m['tile'] for m in P['models']))
    print('models per tile:', dict(sorted(tiles.items())))
    big = sorted(P['models'], key=lambda m: -len(m['verts']))[:5]
    print('biggest models:', [(m['name'], len(m['verts']), len(m['tris'])) for m in big])
    if a.plan:
        return 0
    moves = cv.build(P, removals_only=a.removals_only)
    for _u, _m, n, f, t in moves[:40]:
        print(f'  moved {n}: {f} -> {t}')
    return 0
