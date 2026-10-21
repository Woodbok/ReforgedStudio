"""forgelib/ac4/map_audit.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import collections
import os
import re
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT, os.path.join(ROOT, 'maps')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import common
import matprops
STATIC_DECAL = bytes.fromhex('6d7f3a2f')
LOD_SELECTOR_INSTANCE = bytes.fromhex('62744301')
MESH_INSTANCE_DATA = bytes.fromhex('3b966e53')
CMI = 'CompiledMeshInstance'

def texture_slots(body, game='AC3'):
    try:
        m = matprops.parse(body, game)
    except matprops.ParseError:
        return {}
    return {it.hash: it.texture() for it in m.items if it.kind == matprops.INLINE_KIND and it.texture()}

def foreign_texture_slots(mats, src_slots, universe, shared, ok_names=(), own_uids=()):
    out = []
    for rec, n, u, b in mats:
        own = src_slots.get(u, {})
        for h, tx in texture_slots(b).items():
            home = universe.get(tx)
            if home is None or home[0] in shared or own.get(h) == tx or (tx in own_uids) or (home[3] in ok_names):
                continue
            out.append(f'{n.strip()} @{rec}: slot {h:08X} -> {home[3]} ({home[0]})')
    return out

def inactive_block_check(bodies, blocks):
    import stage0
    blocks = set(blocks)
    if not blocks:
        return ([], collections.Counter())
    name_of = {u: (c, n) for rec, c, n, u, b in bodies if u is not None}
    placed_left = []
    for rec, c, n, u, b in bodies:
        if rec in blocks and c == 'GridCellDataBlock':
            cnt = stage0.u32(b, 10)
            m = stage0.u32(b, 14 + 6 * cnt)
            for j in range(m):
                v = stage0.u32(b, 14 + 6 * j + 2)
                placed_left.append(f"{name_of.get(v, ('?', hex(v)))[1].strip()} @{rec}")
    only = {u for rec, c, n, u, b in bodies if u is not None and rec in blocks}
    only -= {u for rec, c, n, u, b in bodies if u is not None and rec not in blocks}
    into = collections.Counter()
    for rec, c, n, u, b in bodies:
        if rec in blocks or c == 'Animation':
            continue
        for v in stage0.refs(b):
            if v in only:
                into[c, name_of[v][0]] += 1
    return (placed_left, into)

def decal_forms(body):
    out = collections.Counter()
    k = body.find(STATIC_DECAL)
    while k >= 0:
        if k >= 6 and body[k - 6:k - 4] == b'\x04\x00':
            nxt = body[k + 16:k + 20]
            out['LODSelector' if nxt == LOD_SELECTOR_INSTANCE else 'direct mesh' if nxt == MESH_INSTANCE_DATA else 'stub' if body[k + 4:k + 6] == b'\x01\x03' else 'other'] += 1
        k = body.find(STATIC_DECAL, k + 4)
    return out

def _objects(path):
    import mapwalk
    from forge import Forge
    with Forge(path) as f:
        for r in f.records:
            if r.name == 'GlobalMetaFile' or not r.size:
                continue
            try:
                p = mapwalk.Payload(r, f.read(r))
            except Exception:
                continue
            for o in p.objects:
                yield (r.name, o)

def audit(key, build, cfg, universe, shared, ok_names, w):
    import world
    src_path = common.ac4_archive(cfg['archive'])
    w(f'\n== {key}: {build} (source {os.path.basename(src_path)})')
    src_uids, src_mat_slots, forms, owners, ng, ng_cls, wps_src = (set(), {}, collections.Counter(), {}, collections.Counter(), collections.Counter(), [])
    for rec, o in _objects(src_path):
        c, b = (common.cls(o['type'], 'AC4'), o['body'])
        if o['has_uid']:
            src_uids.add(o['uid'])
        if c == 'Material' and o['has_uid']:
            src_mat_slots.setdefault(o['uid'], texture_slots(b, 'AC4'))
        f_ = decal_forms(b)
        forms.update(f_)
        if f_ and set(f_) != {'LODSelector'} and o['has_uid']:
            owners[o['uid']] = (o['name'].strip(), sum((1 for n_, _s in world.segments(b) if n_ == CMI)), dict(f_))
        if rec == 'DataBlock_Next_Gen' and o['has_uid'] and (c in ('Entity', 'EntityGroup')):
            ng_cls[c] += 1
            ng[re.sub('(_\\d+)+$', '', o['name'].strip())] += 1
        if o['name'].startswith('ACGAMP_FX_WPS_spawnrate'):
            wps_src.append(o['name'].strip())
    mats, decal_left, cmi_after, wps_build, ng_build = ([], [], {}, [], 0)
    for rec, o in _objects(build):
        c, b = (common.cls(o['type'], 'AC3'), o['body'])
        if c == 'Material' and o['has_uid'] and (o['uid'] in src_uids):
            mats.append((rec, o['name'], o['uid'], b))
        if STATIC_DECAL in b and c not in ('Mesh', 'TextureMap', 'MeshShape') and decal_forms(b):
            decal_left.append(o['name'].strip())
        if o['has_uid'] and o['uid'] in owners:
            cmi_after[o['uid']] = sum((1 for n_, _s in world.segments(b) if n_ == CMI))
        if o['name'].startswith('ACGAMP_FX_WPS_spawnrate'):
            wps_build.append(o['name'].strip())
        if rec == 'DataBlock_Next_Gen' and c in ('Entity', 'EntityGroup'):
            ng_build += 1
    fails = 0
    e1 = foreign_texture_slots(mats, src_mat_slots, universe, shared, ok_names | set(cfg.get('foreign_textures_ok', ())), src_uids)
    w(f"E1 AC4 materials sampling another AC3 map's texture: {len(e1)}" + ''.join((f'\n     {x}' for x in e1[:40])))
    fails += bool(e1)
    lost = {v[0]: (v[1], cmi_after.get(u)) for u, v in owners.items() if cmi_after.get(u) is not None and cmi_after[u] < v[1]}
    w(f"E2 decal heads in the source by form: {dict(forms) or 'none'}; non-LODSelector owners (mesh instances source -> build): { {v[0]: (v[1], cmi_after.get(u), v[2]) for u, v in owners.items()} or 'none'}; decal heads left in the build: {len(decal_left)} {decal_left[:4]}")
    if cfg.get('keep_decals') and lost:
        w(f'   FAIL: decals with a mesh lost while the map keeps decals: {lost}')
        fails += 1
    w(f"E4 DataBlock_Next_Gen (layer 0x1855C54F; cfg unlayer_next_gen {cfg.get('unlayer_next_gen', False)}): source {dict(ng_cls)}, build {ng_build}; by kind: " + ', '.join((f'{n} {k}' for n, k in ng.most_common())))
    w(f"E5 weather-FX controller: source {wps_src}, build {wps_build or 'DROPPED'}")
    return fails

def main(argv):
    import map_configs
    import map_sound
    import stage0
    keys = argv or list(map_sound.MAPS)
    src3 = stage0.AC3Source()
    lines = []

    def w(s):
        lines.append(s)
        print(s)
    fails = 0
    for key in keys:
        build, _banks = map_sound.MAPS[key]
        cfg = map_configs.MAPS['expedition_aa' if key == 'expedition' else key]
        fails += audit(key, os.path.join(ROOT, build), cfg, src3.universe, stage0.SHARED_ARCHIVES, stage0.FOREIGN_TEXTURES_OK, w)
    w(f"\n{('all OK' if not fails else f'FAILED: {fails}')}")
    os.makedirs(common.OUT, exist_ok=True)
    with open(os.path.join(common.OUT, 'map_audit.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    return 1 if fails else 0
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
