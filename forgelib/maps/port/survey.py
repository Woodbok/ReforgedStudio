"""forgelib/maps/port/survey.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import array
import json
import os
import pickle
import re
import struct
import sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (os.path.join(ROOT, 'crossmaps'), HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
if os.path.join(ROOT, 'audio') not in sys.path:
    sys.path.append(os.path.join(ROOT, 'audio'))
import build_static
import convert
import entity
import mapwalk
import paths
import xrefs
from forge import Forge
from merge_cells import cell_list
from summary import BULK
CACHE = os.path.join(ROOT, 'out', 'maps', '_cache')
RECIPES = {'STOP': "a class no confirmed port holds, no verdict: read both exes' readers before any build", 'impossible': 'dropped for good (build_static --skip-entity, drop_entities after the lifts)', 'performers': 'scene + spawner of an NPC: performers.py (rule 8, props, carried files), npc_port --npc performer per builder', 'pieces': 'rule 8 / a class to strip: dropped in stage 1, back through performers.py --entities at the end', 'spots': 'trigger-only crowd-gathering spots: AC3MP_GEN_Wall_Reader audiences at their marker planes (audience_groups.py)', 'merchants': "ACR's stalls on AC3's merchant, Brotherhood's Italian merchant, ACR's own calls (ac2mp_npc_steps)", 'orators': "AC3's stall shape with ACR's herald and his speeches (ac2mp_npc_steps orators=True); a crowd onto its plane where that lies elsewhere (orator_planes)", 'lone': 'top-level entities with a sound emitter no lift takes: lift_dropped --entities --proven Ambient3dSoundComponent', 'tree prefixes': "tree entities the chain's AC2_GEN_RT_ lift doesn't name: one more lift each", 'trees': "the chain's tree steps (lift, Mill's donors, realtree_convert)", 'stage 0': "ac2mp_benches.py: benches as AC3's sittable bench, elevators / merged members without their creak emitter", 'blend groups': "rebuilt on AC3's crowd life (lift_dropped --crowdlife, acb_blend)", 'ropes': "the chain's rope lift", 'markers': "a merchant's / spot's marker plane: kept with merchants, dropped after audiences", 'by design': "replaced by AC3's own (aa_zones.py, mode_spawns.py)", 'dead': 'in a file no cell lists and no performer carries: never loaded', 'plain': "the chain's own steps"}
KNOWN_FAMILIES = [('blend groups', '^AC2MP_GEN_Static_Group'), ('ropes', '^AC2_GEN_Rope_'), ('markers', '^AC2MP_Merchant_Orator_plane')]
BY_DESIGN = ['^ctf_border', '^ctf_spawn', '^spawn_ctf']
TEMPLATES = '^(Generic_BaseEntity|AC2MP_Generic_OratorEntity)'
KNOWN_NOTES = [('(?i)fake_forest', 'fake forests: Rhodes dropped its 123 (always-drawn plain meshes, part of its flicker: RH6a, configs._rhodes skip_entities); these are tree entities, lifted as trees - drop them if they flicker')]
KNOWN_VERDICTS = [(('AC2MP_ENTITY_Cavalry', 'AC2MP_cavalery_patrol', 'AC2MP_cavaley_spawn', 'AC2MP_Cavalery_scene'), 'impossible', "Siena's Palio cavalry: spawned and driven by GcLSiegeTroopWaves, which AC3MP.exe lacks (README 'The cavalry cannot come as data'; a loader mover is the future route)")]
KNOWN_CLASSES = {'WeaponComponent': ('strip', "ACR's reads two more Entity handles than AC3's; a prop needs no weapon data (SI17: the fiddle)"), 'WeaponStats': ('strip', 'inside the WeaponComponent'), 'CTFWallComponent': ('by design', "ACR's CTF border: aa_zones.py replaces it"), 'CTFWallSection': ('by design', "ACR's CTF border: aa_zones.py replaces it"), 'Reflector': ('impossible', "ACR's Reflector reads a Mesh + ReflectorCullingZone, AC3's a material set (reader_ops.py): dropped"), 'CameraZone': ('same', 'the same reader in both exes (reader_ops.py)'), 'NavigationZone': ('same', 'the same reader in both exes (reader_ops.py)'), 'NavigationZoneListener': ('same', 'reads nothing in either exe (reader_ops.py)'), 'SoftBodyComponent': ('same', 'the same reader in both exes (reader_ops.py)'), 'SoftBodyPlane': ('same', 'reads nothing in either exe (reader_ops.py)'), 'ReflectorCullingZone': ('impossible', 'only inside a Reflector')}
PROVEN_LIFT = ('Ambient3dSoundComponent', 'SoundActor')
SKIP_CLASSES = ('ObjectPack', 'PatrolPath')
NOISE = {'SSLv2', 'TLSv1', 'SEED', 'AES', 'CAMELLIA', 'SFX_MenuWS_Money_Selected'}
CONFIRMED_PORTS = ['out/maps/SI19_sie/DataPC_AC2MP_Siena.forge', 'out/maps/PA12_pal/DataPC_AC2MP_Palazzio.forge']
TREE_LIFT = 'AC2_GEN_RT_'
ROPE_LIFT = 'AC2_GEN_Rope_'
GENERIC_SKY = ('3D_TimeOfDay_', 'GEN_SkyDome_', 'Sky_Cycle_')

def _registries():
    xrefs.cls('ACR', 0)
    xrefs.cls('AC3', 0)
    return ({k: v for k, v in xrefs._REG['ACR'].items() if k >= 1 << 24}, {k: v for k, v in xrefs._REG['AC3'].items() if k >= 1 << 24})
ACR_REG, AC3_REG = _registries()
ALL_REG = {**ACR_REG, **AC3_REG}
ALL_KEYS = set(ALL_REG)
ACR_ONLY = {k for k in ACR_REG if k not in AC3_REG}

def class_ids_in(b):
    out = set()
    for off in range(4):
        n = (len(b) - off) // 4
        if n > 0:
            a = array.array('I')
            a.frombytes(b[off:off + 4 * n])
            out |= ALL_KEYS.intersection(a)
    return out

def classes_in(b):
    return {ALL_REG[c] for c in class_ids_in(b)} - NOISE
HEADER_FLAGS = (b'\x04\x00', b'\x00\x01', b'\x00\x00')

def framed(b, names):
    out = set()
    placed = b[6:10] in _PLACED_IDS
    for c in class_ids_in(b):
        if ALL_REG[c] not in names:
            continue
        nb = struct.pack('<I', c)
        k = b.find(nb, 6)
        while k >= 6:
            if b[k - 6:k - 4] in HEADER_FLAGS and (not (placed and 10 <= k < 74)):
                out.add(ALL_REG[c])
                break
            k = b.find(nb, k + 1)
    return out
_PLACED_IDS = {struct.pack('<I', convert.class_ids()[n]) for n in ('Entity', 'EntityGroup')}

def framed_acr_only(b):
    out = set()
    for c in class_ids_in(b) & ACR_ONLY:
        nb = struct.pack('<I', c)
        k = b.find(nb, 6)
        while k >= 6:
            if b[k - 6:k - 4] in (b'\x04\x00', b'\x00\x01'):
                out.add(ACR_REG[c])
                break
            k = b.find(nb, k + 1)
    return out

def refs(body):
    out = set()
    k = body.find(b'\x01')
    while 0 <= k < len(body) - 5:
        if body[k + 1] in (0, 1, 2):
            out.add(struct.unpack_from('<I', body, k + 2)[0])
        k = body.find(b'\x01', k + 1)
    return out

def family(name):
    return re.sub('(_\\d+[A-Za-z]?)+$|\\s*\\d+$', '', (name or '').strip())

def matches(name, prefixes):
    return any(((name or '').startswith(p) for p in prefixes))

class Map:

    def __init__(self, path):
        self.path = path
        self.files = {}
        with Forge(path) as f:
            for r in f.records:
                if r.size and r.name != 'GlobalMetaFile':
                    self.files[r.index] = mapwalk.Payload(r, f.read(r))
        self.home, self.obj = ({}, {})
        self.listed = defaultdict(set)
        self.kind = {}
        for idx, p in self.files.items():
            first = xrefs.cls('ACR', p.objects[0]['type'])
            self.kind[idx] = 'cell' if first == 'GridCellDataBlock' else 'block' if p.rec.name.startswith('DataBlock') else 'world' if first == 'World' else 'file'
            if first == 'GridCellDataBlock':
                _n, e, _m, _ = cell_list(p.objects[0]['body'])
                for x in e:
                    self.listed[struct.unpack_from('<I', x, 2)[0]].add(idx)
            for i, o in enumerate(p.objects):
                if o['has_uid']:
                    self.home.setdefault(o['uid'], (idx, i))
                    self.obj.setdefault(o['uid'], o)

    def name(self, idx):
        return self.files[idx].rec.name

    def objects(self):
        for idx, p in self.files.items():
            for i, o in enumerate(p.objects):
                yield (idx, i, o)

def confirmed_ports():
    with open(os.path.join(ROOT, 'release_set.json'), encoding='utf-8') as fh:
        rel = [f['src'] for f in json.load(fh)['files'] if re.search('DataPC_(ACFE|AC2MP)_', f['dest'])]
    return [p if os.path.isabs(p) else os.path.join(ROOT, p) for p in rel + CONFIRMED_PORTS]

def reference_classes():
    os.makedirs(CACHE, exist_ok=True)
    cache = os.path.join(CACHE, 'survey_ref_classes.pkl')
    have = pickle.load(open(cache, 'rb')) if os.path.exists(cache) else {}
    out, dirty = (set(), False)
    for path in confirmed_ports():
        if not os.path.exists(path):
            continue
        key = (os.path.normcase(path), os.path.getsize(path), int(os.path.getmtime(path)))
        if key not in have:
            s = set()
            with Forge(path) as f:
                for r in f.records:
                    if r.size and r.name != 'GlobalMetaFile':
                        try:
                            for o in mapwalk.Payload(r, f.read(r)).objects:
                                s |= class_ids_in(o['body'])
                        except Exception:
                            pass
            have[key], dirty = (s, True)
        out |= {ALL_REG[c] for c in have[key]}
    if dirty:
        pickle.dump(have, open(cache, 'wb'))
    return out - NOISE

def stage0(m, report):
    import acb_stage0
    import ac2mp_benches
    import inventory
    ac2mp_benches.patch_benches()
    taken = set(inventory.universe('ACR')) | set(inventory.universe('AC3'))
    _changed, expect = ac2mp_benches.acr_entities(m.files, report, acb_stage0.Uids(1526661120, taken))
    return set(expect)

def stage1(m, skip_classes=(), skip_entities=()):
    import changed_classes
    import coverage
    import world as world_mod
    unknown_in = build_static.unknown_checker(stage1=True)
    unknown_classes = {n for _t, (n, _a, _b) in changed_classes.changed().items()} - coverage.PROVEN_SAME - build_static.STAGE1_RULED - {'Animation', 'World', 'MaterialTemplate', 'LayeredSky'}
    remap = world_mod.mode_remap('ACR')
    tree_id = struct.pack('<I', convert.class_ids()['RealTreeMeshInstanceData'])
    dropped = {}
    for _idx, _i, o in m.objects():
        cls = xrefs.cls('ACR', o['type'])
        if cls in BULK or cls in ('MaterialTemplate', 'Animation', 'World'):
            continue
        name = o['name'] or ''
        if cls == 'UnlockableGameMode' and o['uid'] in remap:
            dropped[o['uid']] = 'stale UnlockableGameMode'
        elif cls in skip_classes:
            dropped[o['uid']] = f'--skip-class {cls}'
        elif cls in ('Entity', 'EntityGroup') and matches(name, skip_entities):
            dropped[o['uid']] = '--skip-entity'
        elif cls in ('Entity', 'EntityGroup') and tree_id in o['body']:
            dropped[o['uid']] = 'places a RealTreeMesh'
        else:
            bad = unknown_in(o['body'])
            if not bad:
                continue
            if cls == 'Entity':
                lays = entity.element_layouts(o['body'])
                if lays is not None:
                    flagged = [(n, lay) for n, lay in lays if {c for c, _l in lay} & unknown_classes]
                    known = entity.ac3_layouts(sorted({n for n, _ in flagged})) if flagged else {}
                    if all((lay in known[n] for n, lay in flagged)):
                        continue
                    dropped[o['uid']] = 'layout not in AC3: ' + ', '.join(sorted({n for n, lay in flagged if lay not in known[n]}))
                    continue
            dropped[o['uid']] = ', '.join(sorted(bad))
    firsts = {p.objects[0]['uid'] for p in m.files.values() if p.objects[0]['has_uid']}
    for u in firsts:
        dropped.pop(u, None)
    named = {}
    for _idx, _i, o in m.objects():
        cls = xrefs.cls('ACR', o['type'])
        if not o['has_uid'] or o['uid'] in firsts or cls in BULK or (cls in ('GridCellDataBlock', 'World', 'MaterialTemplate', 'Animation')):
            continue
        named[o['uid']] = (refs(o['body']) if cls != 'RegionLayout' else None, o['body'])
    while True:
        gone, grew = (set(dropped), False)
        for u, (r, body) in named.items():
            if u not in dropped and (any((struct.pack('<I', d) in body[6:] for d in gone)) if r is None else r & gone):
                dropped[u] = 'pointed at dropped objects'
                grew = True
        if not grew:
            return dropped

def generic_lifts(m, dropped, trees, drop_after=()):
    import lift_dropped
    import tree_stubs
    targets = convert.targets('ACR')
    unknown_group = build_static.unknown_checker(extra=PROVEN_LIFT)
    unknown_rope = build_static.unknown_checker(extra=('Ambient3dSoundComponent',))
    unknown_tree = build_static.unknown_checker()
    defined = set(m.obj) - set(dropped)
    lifted = {}
    for _idx, _i, o in m.objects():
        u = o['uid'] if o['has_uid'] else None
        if u not in dropped:
            continue
        cls, name = (xrefs.cls('ACR', o['type']), o['name'] or '')
        if cls == 'EntityGroup':
            raw, how, nkept, _skipped = lift_dropped.group_raw(o['body'], targets, unknown_group, defined)
            if raw is not None:
                lifted[u] = f'group lift ({how}, {nkept} children)'
        elif cls == 'Entity' and name.startswith(ROPE_LIFT):
            if not unknown_rope(convert.convert(o['body'], targets)[0]):
                lifted[u] = 'rope lift'
        elif cls == 'Entity' and trees and name.startswith(TREE_LIFT):
            if not unknown_tree(convert.convert(o['body'], targets)[0]):
                lifted[u] = 'tree lift'
        elif cls == 'Entity' and name.startswith('AC2MP_GEN_Static_Group'):
            lifted[u] = 'blend-group rebuild'
        elif cls == 'CrowdDutyRegion':
            lifted[u] = 'crowd-region lift'
    multi = struct.pack('<I', convert.class_ids()['MultiInertComponent'])
    for u in list(dropped):
        o = m.obj.get(u)
        if o is not None and u not in lifted and (xrefs.cls('ACR', o['type']) == 'Entity'):
            mem = tree_stubs.merged_members(o['body'], multi)
            if mem and all((x not in dropped or x in lifted for x in mem)):
                lifted[u] = 'merged_collision lift'
    for u in list(lifted):
        if matches(m.obj[u]['name'], drop_after) and xrefs.cls('ACR', m.obj[u]['type']) in ('Entity', 'EntityGroup'):
            del lifted[u]
    return lifted

def needs_rule8(body):
    targets = convert.targets('ACR')
    plain = convert.convert(body, targets)[0]
    with8 = convert.convert(body, targets, convert.RULES + [('scene mid byte', convert.EXTRA_RULES['scene mid byte'])])[0]
    return plain != with8

def classify(m, s0):
    ref = reference_classes()
    ids = convert.class_ids()
    ssc, npc = (struct.pack('<I', ids['SceneSpawningComponent']), struct.pack('<I', ids['SpawnNPCParams']))
    gcc = struct.pack('<I', ids['GameplayCoordinatorComponent'])
    tree_id = struct.pack('<I', ids['RealTreeMeshInstanceData'])
    strip = {c for c, (v, _w) in KNOWN_CLASSES.items() if v == 'strip'}
    impossible = {c for c, (v, _w) in KNOWN_CLASSES.items() if v == 'impossible'}
    ents = {}
    for idx, i, o in m.objects():
        if i > 0 and o['has_uid'] and (xrefs.cls('ACR', o['type']) in ('Entity', 'EntityGroup')):
            ents[o['uid']] = (idx, o)
    fams = defaultdict(list)
    for u, (_idx, o) in ents.items():
        fams[family(o['name'])].append(u)
    rec, notes, drop = ({}, defaultdict(list), [])
    for fam, uids in fams.items():
        bodies = [ents[u][1]['body'] for u in uids]
        in_files = all((m.kind[ents[u][0]] == 'file' for u in uids))
        groups = any((xrefs.cls('ACR', ents[u][1]['type']) == 'EntityGroup' for u in uids))
        cls_all = set().union(*(classes_in(b) for b in bodies))
        acr_only = set().union(*(framed_acr_only(b) for b in bodies))
        unseen_all = {c for c in cls_all - ref if c not in KNOWN_CLASSES}
        unseen = set().union(*(framed(b, unseen_all) for b in bodies)) if unseen_all else set()
        if unseen_all - unseen:
            notes[fam].append(f"bare class-id hits (data, not objects): {', '.join(sorted(unseen_all - unseen))}")
        verdict = next(((pre, r, w) for pre, r, w in KNOWN_VERDICTS if matches(fam, pre)), None)
        if verdict:
            rec[fam] = (verdict[1], verdict[2])
            drop += [p for p in verdict[0] if fam.startswith(p)]
            continue
        if re.search(TEMPLATES, fam):
            rec[fam] = ('dead', 'an NPC template entity (the builders name it), not placed')
            continue
        if in_files and (not groups):
            rec[fam] = ('dead', '')
            continue
        if any((re.search(pat, fam.lower()) for pat in BY_DESIGN)):
            rec[fam] = ('by design', '')
        elif acr_only:
            rec[fam] = ('impossible', f"holds {', '.join(sorted(acr_only))}: AC3MP.exe has no such class")
        elif set().union(*(framed(b, impossible) for b in bodies)):
            rec[fam] = ('impossible', '; '.join((KNOWN_CLASSES[c][1] for c in sorted(set().union(*(framed(b, impossible) for b in bodies))))))
        elif fam.startswith(('AC2MP_GEN_AMerchant', 'AC2MP_GEN_AOrator')):
            kind = 'merchants' if fam.startswith('AC2MP_GEN_AMerchant') else 'orators'
            real = sum((1 for b in bodies if gcc in b))
            rec[fam] = (kind, 'with their GameplayCoordinator') if real == len(bodies) else ('spots', 'trigger only (AttractorZone / InterestZone), no coordinator') if not real else (kind, f'{real} of {len(bodies)} with a coordinator; the rest audience spots (orator_names / spots)') if kind == 'orators' else ('STOP', f'{real} of {len(bodies)} with a coordinator: merchants and audience spots in one family')
        elif any((re.search(pat, fam) for _r, pat in KNOWN_FAMILIES)):
            rec[fam] = (next((r for r, pat in KNOWN_FAMILIES if re.search(pat, fam))), '')
        elif all((ssc in b and npc in b for b in bodies)):
            rec[fam] = ('performers', '')
        elif any((u in s0 for u in uids)):
            rec[fam] = ('stage 0', '')
        elif unseen:
            rec[fam] = ('STOP', f"classes no confirmed port holds: {', '.join(sorted(unseen))}")
        elif any((needs_rule8(b) for b in bodies)):
            rec[fam] = ('pieces', "a Scene that is not its entity's last component (rule 8)")
        elif cls_all & strip:
            rec[fam] = ('pieces', 'a class to strip: ' + ', '.join(sorted(cls_all & strip)))
        elif all((tree_id in b for b in bodies)):
            rec[fam] = ('trees', '')
        else:
            rec[fam] = ('plain', '')
        if in_files and rec[fam][0] in ('plain', 'trees'):
            rec[fam] = ('dead', 'groups in a file no cell lists')
        if rec[fam][0] in ('performers', 'pieces', 'impossible'):
            drop.append(fam)
        notes[fam] += [n for pat, n in KNOWN_NOTES if re.search(pat, fam)]
        if unseen and rec[fam][0] not in ('STOP', 'impossible', 'by design', 'dead'):
            notes[fam].append(f"classes no confirmed port holds, which its recipe handles: {', '.join(sorted(unseen))}")
    return (rec, fams, ents, notes, sorted(set(drop)))

def perf_details(m, perf_uids, ents, lost):
    builders, packs, carry, props, names = ([], [], [], [], [])
    for u in sorted(perf_uids, key=lambda x: m.home[x]):
        o = ents[u][1]
        names.append(o['name'])
        body = o['body']
        for _off, r in sorted(((body.find(struct.pack('<I', r)), r) for r in refs(body) if r in m.home and r != u)):
            idx, _i = m.home[r]
            t = m.obj[r]
            cls = xrefs.cls('ACR', t['type'])
            if cls == 'EntityBuilder':
                if r not in builders:
                    builders.append(r)
            elif m.kind[idx] == 'file':
                if m.name(idx) not in carry:
                    carry.append(m.name(idx))
            elif m.kind[idx] == 'cell' and r not in m.listed and (r in lost) and (t['name'] not in packs):
                packs.append(t['name'])
            if cls == 'ObjectPack' and m.kind[idx] == 'cell':
                for h in refs(t['body']):
                    if h in ents and m.kind[ents[h][0]] == 'cell' and (family(m.obj[h]['name']) not in props):
                        props.append(family(m.obj[h]['name']))
    return {'performers': names, 'builders': builders, 'packs': packs, 'carry': carry, 'prop_families': props}

def survey(path):
    report = []
    m = Map(path)
    s0 = stage0(m, report)
    rec, fams, ents, notes, drop = classify(m, s0)
    tree_id = struct.pack('<I', convert.class_ids()['RealTreeMeshInstanceData'])
    trees = any((tree_id in o['body'] for _idx, o in ents.values() if m.kind[_idx] != 'file'))
    dropped = stage1(m, SKIP_CLASSES, drop)
    lifted = generic_lifts(m, dropped, trees, drop)
    lost = {u: w for u, w in dropped.items() if u not in lifted}
    perf_uids = [u for fam, (r, _w) in rec.items() if r == 'performers' for u in fams[fam]]
    perf = perf_details(m, perf_uids, ents, lost)
    for fam in perf['prop_families']:
        if rec.get(fam, ('',))[0] == 'pieces':
            rec[fam] = ('pieces', "a performer's prop (its pack hands it out); " + rec[fam][1])
    lone, tree_prefixes, by_fam = ([], [], defaultdict(list))
    for u, why in lost.items():
        if u in ents and m.kind[ents[u][0]] != 'file':
            by_fam[family(ents[u][1]['name'])].append((u, why))
    for fam, items in sorted(by_fam.items()):
        if rec[fam][0] not in ('plain', 'trees', 'stage 0'):
            continue
        objs = [ents[u][1] for u, _w in items]
        kinds = {xrefs.cls('ACR', o['type']) for o in objs}
        if kinds == {'Entity'} and all((tree_id in o['body'] for o in objs)):
            rec[fam] = ('tree prefixes', '')
            tree_prefixes.append(fam)
        elif kinds == {'Entity'} and all(('Ambient3dSoundComponent' in classes_in(o['body']) for o in objs)):
            rec[fam] = ('lone', '')
            lone.append(fam)
        else:
            rec[fam] = ('STOP', f'{len(items)} lost after the lifts: {sorted(set((w for _u, w in items)))[:3]}')
    import tree_stubs
    multi = struct.pack('<I', convert.class_ids()['MultiInertComponent'])
    members = {x for _i, _k, o in m.objects() if xrefs.cls('ACR', o['type']) == 'Entity' for x in tree_stubs.merged_members(o['body'], multi)}
    for fam in fams:
        k = sum((1 for u in fams[fam] if u in members and u in lost))
        if k:
            notes[fam].append(f"{k} merged-collision member(s) stay out: their cell's collision goes too unless they come back")
    return {'map': m, 'rec': rec, 'fams': fams, 'ents': ents, 'notes': notes, 'drop': drop, 'trees': trees, 'dropped': dropped, 'lifted': lifted, 'lost': lost, 'lone': lone, 'tree_prefixes': tree_prefixes, 'perf': perf, 'stage0': report}

def facts(m, archive):
    ids = convert.class_ids()
    out = {'map': archive[len('DataPC_'):-len('.forge')], 'dlc': archive.endswith('_dlc.forge')}
    out['install_as'] = archive.replace('_dlc.forge', '.forge') if out['dlc'] else None
    wfile = next((p for p in m.files.values() if any((o['type'] == ids['World'] for o in p.objects))))
    w = next((o for o in wfile.objects if o['type'] == ids['World']))
    out['world'], out['world_uid'] = (w['name'], w['uid'])
    gp = [o for _i, _k, o in m.objects() if xrefs.cls('ACR', o['type']) == 'GridPartition']
    nodes = struct.unpack_from('<I', gp[0]['body'], 14)[0] if len(gp) == 1 else None
    side = {85: 8, 341: 16, 1365: 32, 21: 4}.get(nodes)
    out['grid'] = f'{side} x {side}' if side else f'? ({nodes} nodes)'
    out['root'] = f'Cell{nodes - 1:05d}_DataBlock' if nodes and any((p.rec.name == f'Cell{nodes - 1:05d}_DataBlock' for p in m.files.values())) else None
    out['layouts'] = sorted({o['name'] for _i, _k, o in m.objects() if xrefs.cls('ACR', o['type']) == 'RegionLayout' and 'crowd' in (o['name'] or '').lower()})
    banks = os.path.join(ROOT, 'out', 'audio', 'acr_maps.json')
    b = json.load(open(banks, encoding='utf-8')).get(archive, {}) if os.path.exists(banks) else {}
    out['bank'] = (b.get('bank'), b.get('bank_id'))
    out['missing_emitters'] = sorted((e for e, (_n, st) in b.get('emitters', {}).items() if st != 'intact'))
    ctf, pts = (Counter(), [])
    for p in m.files.values():
        if p.rec.name != 'DataBlock_ACFE_CTF_2':
            continue
        for o in p.objects:
            names = {n for _s, _e, n in entity.components(o['body']) or []} if xrefs.cls('ACR', o['type']) == 'Entity' else set()
            for c in ('CTFWallComponent', 'OutOfBoundsComponent', 'FlagComponent', 'MultiSpawnPlayerComponent'):
                ctf[c] += c in names
            if names & {'FlagComponent', 'MultiSpawnPlayerComponent'}:
                pts.append(struct.unpack_from('<3f', o['body'], 58)[:2])
    out['ctf'] = dict(ctf)
    if pts and (not ctf['OutOfBoundsComponent']):
        xs, ys = ([q[0] for q in pts], [q[1] for q in pts])
        out['area_box'] = f'{min(xs) - 12:.0f},{min(ys) - 6:.0f},{max(xs) + 12:.0f},{max(ys) + 6:.0f}'
    out['sky'] = sky(wfile)
    fadein = Counter()
    for _i, _k, o in m.objects():
        if o['type'] == ids['Material'] and (o['name'] or '').strip().endswith('FadeIn'):
            fadein[next((m.obj[x]['name'] for x in refs(o['body']) if x in m.obj and xrefs.cls('ACR', m.obj[x]['type']) == 'MaterialTemplate'), '?')] += 1
    out['fadein'] = dict(fadein)
    out['acr_menu'] = acr_menu(out['world_uid'])
    out['pictures'] = pictures(out['map'], [u for e in out['acr_menu'] for u in e['pictures']])
    out['next_string'] = next_string()
    return out

def sky(wfile):
    import dxt
    import sky_color
    cands = [o for o in wfile.objects if xrefs.cls('ACR', o['type']) == 'TextureMap' and re.search('sky', o['name'] or '', re.I) and (not (o['name'] or '').startswith(GENERIC_SKY))]
    if not cands:
        return {'texture': None, 'note': 'no sky texture of its own: the standard dome (Palazzio)'}
    t = min(cands, key=lambda o: len(o['body']))
    try:
        h = dxt.header(t['body'])
        wd, ht = (h['width'], h['height'])
        if h['format'] in dxt.BLOCK:
            _w, _h, rgba = dxt.decode(t['body'])
        else:
            raw = t['body'][sky_color.DATA:sky_color.DATA + wd * ht * 4]
            rgba = bytes((raw[i + k] for i in range(0, len(raw), 4) for k in (2, 1, 0, 3)))
        cols = [sky_color.column(wd, ht, rgba, hr) for hr in range(24)]
        spread = max((abs(a[k] - b[k]) for c in cols for a, b in zip(c, cols[0]) for k in range(3)))
        return {'texture': t['name'], 'size': f'{wd} x {ht}', 'flat': spread < 8, 'spread': round(spread, 1)}
    except Exception as e:
        return {'texture': t['name'], 'error': str(e)[:80]}

def acr_menu(world_uid):
    from persona import UISTRING
    cls = struct.pack('<I', UISTRING)
    with Forge(mapwalk.archive_path('ACR', 'DataPC.forge')) as f:
        rec = next((r for r in f.records if r.name == 'Game Bootstrap Settings'))
        p = mapwalk.Payload(rec, f.read(rec))
    out = []
    for o in p.objects:
        b = o['body']
        if xrefs.cls('ACR', o['type']) != 'MpWorld' or struct.unpack_from('<I', b, 11)[0] != world_uid:
            continue
        sids, k = ([], b.find(cls))
        while k >= 4:
            sids.append(struct.unpack_from('<I', b, k + 4)[0])
            k = b.find(cls, k + 1)
        pics = [struct.unpack_from('<I', b, len(b) - k)[0] for k in (9, 4)] if b[-10] == 0 and b[-5] == 0 else []
        out.append({'name': o['name'], 'hour': round(struct.unpack_from('<f', b, 15)[0], 2), 'strings': [(f'0x{s:X}', acr_text(s) if s != 4294967295 else None, acr_text(s, 'ACB') if s != 4294967295 and (not acr_text(s)) else None) for s in sids[:2]], 'pictures': [f'0x{u:08X}' for u in dict.fromkeys(pics)]})
    return out
_LOC = {}

def acr_text(sid, game='ACR'):
    if game not in _LOC:
        sys.path.insert(0, os.path.join(ROOT, 'strings'))
        import locpack
        if game == 'ACR':
            path = os.path.join(paths.ACR, 'DataPC_localization.forge')
        else:
            import acb
            path = os.path.join(acb.ACB, 'DataPC.forge')
        try:
            _LOC[game] = locpack.LocData(locpack.package_data('English', path))
        except (OSError, StopIteration):
            _LOC[game] = None
    return _LOC[game].lookup(sid) if _LOC[game] else None

def _sets_under(m, root, through=('EntityBuilder', 'BuildTable')):
    out, seen, stack = (set(), set(), [root])
    while stack:
        x = stack.pop()
        if x in seen or x not in m.obj:
            continue
        seen.add(x)
        c = xrefs.cls('ACR', m.obj[x]['type'])
        if c in ('HumanSoundSet', 'MultiHumanSoundSet'):
            out.add(x)
        elif c in through or x == root:
            stack += [r for r in refs(m.obj[x]['body']) if r in m.obj]
    return out

def _slot_events(m, sets, slot):
    import character_voice as cv
    _ac3, acr_ev = cv.event_banks()
    out = []
    for s in sorted(sets, key=lambda u: m.obj[u]['name']):
        ents = cv.entries(m.obj[s]['body'])
        e = ents[slot][1] if len(ents) > slot else 0
        if e and e in acr_ev and (e not in out):
            out.append(e)
    return out

def npc_check(m, rec, fams, ents):
    import configs
    import npc_port
    lines, stops, npc = ([], [], {'merchant': None, 'herald_heads': None, 'calls': None, 'speeches': None})
    cls = lambda u: xrefs.cls('ACR', m.obj[u]['type']) if u in m.obj else None
    for kind in ('merchants', 'orators'):
        named = Counter()
        for fam, (r, _w) in rec.items():
            if r == kind:
                for u in fams[fam]:
                    named.update((x for x in refs(ents[u][1]['body']) if cls(x) == 'EntityBuilder'))
        if not named:
            continue
        lines.append(f'{kind} name ' + ', '.join((f"0x{u:08X} {m.obj[u]['name']} x{c}" for u, c in named.most_common())))
        if len(named) > 1:
            stops.append(f'(the map) its {kind} name {len(named)} builders - one per kind expected')
            continue
        root = next(iter(named))
        if kind == 'merchants':
            subs = [x for x in refs(m.obj[root]['body']) if cls(x) == 'EntityBuilder']
            a = next((x for x in subs if '_m_a' in m.obj[x]['name'].lower()), None)
            b = next((x for x in subs if '_m_b' in m.obj[x]['name'].lower()), None)
            if a is None or b is None:
                stops.append(f"(the map) the merchants' selector 0x{root:08X} {m.obj[root]['name']}: no _m_A / _M_B builders among {[m.obj[x]['name'] for x in subs]}")
                continue
            if (root, a, b) != tuple(configs.AC2MP_MERCHANT):
                npc['merchant'] = (root, a, b)
                lines.append(f"ac2mp_entry(merchant=(0x{root:08X}, 0x{a:08X}, 0x{b:08X}))   # {m.obj[root]['name']}, {m.obj[a]['name']}, {m.obj[b]['name']}")
            tables = {x for builder in (a, b) for x in refs(m.obj[builder]['body']) if cls(x) == 'BuildTable' and 'voices' in m.obj[x]['name'].lower()}
            rows = [x for t in tables for x in refs(m.obj[t]['body']) if cls(x) == 'BuildTable']
            civil = [x for x in rows if 'civilianmale' in m.obj[x]['name'].lower()] or rows
            calls = _slot_events(m, set().union(*(_sets_under(m, x) for x in civil)) if civil else _sets_under(m, a), 83)
            if [f'0x{e:08X}' for e in calls] != configs.AC2MP_MERCHANT_CALLS.split(','):
                npc['calls'] = [f'0x{e:08X}' for e in calls]
            lines.append(f"merchants' calls (slot 83 of {', '.join(sorted({m.obj[x]['name'] for x in civil}))}): " + ','.join((f'0x{e:08X}' for e in calls)) + ('' if npc['calls'] else ' = AC2MP_MERCHANT_CALLS'))
        else:
            heads = next((x for x in refs(m.obj[root]['body']) if cls(x) == 'BuildTable' and m.obj[x]['name'].lower().startswith('ac2mp_m_head_weak')), None)
            need = {'herald body': npc_port.HERALD_BODY, 'herald colours': npc_port.HERALD_COLORS}
            missing = [f'{k} 0x{u:08X}' for k, u in need.items() if u not in m.obj] + ([] if heads else ['a head table AC2MP_M_Head_Weak*'])
            if missing:
                stops.append(f"(the map) the herald's tables are not in the archive: {missing} (npc_port --npc herald)")
                continue
            if heads != configs.AC2MP_HERALD_HEADS:
                npc['herald_heads'] = heads
                lines.append(f"ac2mp_entry(herald_heads=0x{heads:08X})   # {m.obj[heads]['name']}")
            speeches = _slot_events(m, _sets_under(m, root), 82)
            if [f'0x{e:08X}' for e in speeches] != configs.AC2MP_ORATOR_SPEECHES.split(','):
                npc['speeches'] = [f'0x{e:08X}' for e in speeches]
            lines.append(f"the herald {m.obj[root]['name']}: body / colours / heads {m.obj[heads]['name']} in the archive; speeches (slot 82): " + ','.join((f'0x{e:08X}' for e in speeches)) + ('' if npc['speeches'] else ' = AC2MP_ORATOR_SPEECHES'))
    return (lines, stops, npc)
NIGHT_DONOR = 'DataPC_AC2MP_Firenze_dlc.forge'
SKY_FLAG = (617819384, 518963919)
SUN_COLOUR = (617819384, 3796431409, 3232351376)

def night_check(m):
    import tod_evening
    import tod_snow
    tod = next((o for _i, _k, o in m.objects() if o['type'] == tod_snow.TOD), None)
    if tod is None:
        return ('no time of day found', None)
    ents = {path: tod['body'][s_:e_] for s_, e_, path in tod_snow.entries(tod['body'])}
    flag = tod_evening.curve(ents[SKY_FLAG]) if SKY_FLAG in ents else None
    sun = tod_evening.curve(ents[SUN_COLOUR]) if SUN_COLOUR in ents else None
    if not flag:
        return (f"{tod['name']}: no sky flag curve", None)
    _k, _c, keys, vals, _e = flag
    flags = sorted({v[0] for v in vals})
    sun_at = (lambda h: '/'.join((f'{x:.2f}' for x in tod_evening.evaluate(sun[1], sun[2], sun[3], h * tod_evening.HOUR)[:3]))) if sun else lambda h: '?'
    line = f"{tod['name']}: sky flag {flags} over the day, sun colour at 0:00 {sun_at(0)} / 12:00 {sun_at(12)}"
    if flags == [1]:
        return (line + f" - NO authored night (day at every hour): FEATURES night = {NIGHT_DONOR}'s 0:00", NIGHT_DONOR)
    return (line, None)

def tree_users(m):
    import zfight_probe
    ids = convert.class_ids()
    tree_id = struct.pack('<I', ids['RealTreeMeshInstanceData'])
    mats = {o['uid'] for _i, _k, o in m.objects() if o['has_uid'] and xrefs.cls('ACR', o['type']) == 'Material'}
    tree_mats = set()
    for _i, _k, o in m.objects():
        if xrefs.cls('ACR', o['type']) == 'RealTreeMesh':
            b = o['body']
            tree_mats |= {struct.unpack_from('<I', b, k + 2)[0] for k in range(len(b) - 5) if b[k] == 1 and b[k + 1] in (0, 1)}
    tree_mats &= mats
    out = set()
    for _i, _k, o in m.objects():
        c, b = (xrefs.cls('ACR', o['type']), o['body'])
        if c not in ('Mesh', 'Entity', 'EntityGroup') or (c in ('Entity', 'EntityGroup') and tree_id in b):
            continue
        for x in tree_mats:
            pat = struct.pack('<I', x)
            ks = [k for k in range(len(b) - 3) if b[k:k + 4] == pat] if pat in b else []
            if ks and c == 'Mesh':
                try:
                    st, vb, nv, ib, ni = zfight_probe.buffers_any(b)
                    ks = [k for k in ks if not (vb <= k < vb + st * nv or ib <= k < ib + 2 * ni)]
                except ValueError:
                    pass
            if ks:
                out.add(o['name'])
                break
    return sorted(out)
LAYER_VISUAL = ('Mesh', 'Material', 'TextureSet', 'TextureMap', 'LODSelector')

def layer_gaps(m, root):
    where = defaultdict(set)
    for idx, _i, o in m.objects():
        if o['has_uid']:
            where[o['uid']].add(idx)
    names = {idx: m.name(idx) for idx in m.files}
    world = next((idx for idx, k in m.kind.items() if k == 'world'), None)
    rootish = {idx for idx, n in names.items() if n == root or idx == world}
    out = defaultdict(set)
    for idx, _i, o in m.objects():
        packed = m.kind[idx] == 'file' and xrefs.cls('ACR', o['type']) == 'Mesh'
        if not (idx in rootish or packed):
            continue
        for x in refs(o['body']):
            if x not in m.obj or x == o.get('uid') or xrefs.cls('ACR', m.obj[x]['type']) not in LAYER_VISUAL:
                continue
            holders = {names[h] for h in where[x]}
            if holders and all((h.startswith(('Cell', 'DataBlock')) for h in holders)) and any((h.startswith('DataBlock') for h in holders)) and (root not in holders):
                out[m.obj[x]['name']].add(o['name'])
    return {k: sorted(v) for k, v in out.items()}
TINY = 65536

def tiny_kinds(m):
    import re
    targets = convert.targets('ACR')
    pat = re.compile(b'(?=\\x01[\\x01\\x02]..\\x00\\x00)', re.S)
    out = {}
    for _i, _k, o in m.objects():
        b = o['body']
        for mt in pat.finditer(b, 2):
            k = mt.start()
            uid = struct.unpack_from('<I', b, k + 2)[0]
            if uid < TINY and convert.kind_of(targets.get(uid)) == b[k + 1]:
                out.setdefault(xrefs.cls('ACR', o['type']), []).append((o['name'], k, uid))
    return out

def orator_planes(m, rec, fams, ents):
    import orator_plane
    planes = [m.obj[u] for u in catch(m, [orator_plane.MARKER], ('Entity',))]
    lines, out = ([], [])
    for fam, (r, _w) in sorted(rec.items()):
        if r != 'orators':
            continue
        for u in sorted(fams[fam], key=lambda x: ents[x][1]['name']):
            o = ents[u][1]
            pl, d = orator_plane.plane_for(o['body'], planes)
            if pl is None:
                lines.append(f"orator {o['name']}: no marker plane in front of him within {orator_plane.REACH} m - the layout as built")
                continue
            split = orator_plane.layout_split(o['body'], pl['body'])
            dz = orator_plane.position(pl['body'])[2] - orator_plane.position(o['body'])[2]
            move = split > orator_plane.SPLIT
            if move:
                out.append(o['name'])
            lines.append(f"orator {o['name']}: plane {pl['name']} {d:.2f} m in front of him (dz {dz:+.2f}), {split:.2f} m from the layout's crowd - " + ('its crowd moves onto it (orator_planes)' if move else 'the layout as built'))
    return (lines, out)

def pictures(map_name, acr_own=()):
    short = re.sub('^(AC2MP|ACR)_|_dlc$|_Multi$', '', map_name).lower()
    short = {'mtstmichel': 'michel', 'sanmarco': 'marco', 'sandonato': 'donato'}.get(short, short)
    os.makedirs(CACHE, exist_ok=True)
    cache = os.path.join(CACHE, 'survey_extra_pictures.pkl')
    key = (paths.DEFAULT_EXTRA, os.path.getsize(paths.DEFAULT_EXTRA))
    have = pickle.load(open(cache, 'rb')) if os.path.exists(cache) else {}
    if key not in have:
        names = []
        with Forge(paths.DEFAULT_EXTRA) as f:
            for r in f.records:
                if r.size and re.search('mapdesc|_img_|topview', r.name, re.I):
                    try:
                        names += [(o['name'], o['uid'], xrefs.cls('AC3', o['type'])) for o in mapwalk.Payload(r, f.read(r)).objects if o['has_uid'] and re.search('mapdesc', o['name'] or '', re.I)]
                    except Exception:
                        pass
        have[key] = names
        pickle.dump(have, open(cache, 'wb'))
    by = {f'0x{u:08X}': n for n, u, _c in have[key]}
    own = [(by[u], u, "ACR's menu") for u in acr_own if u in by]
    return own + [(n, f'0x{u:08X}', '') for n, u, _c in have[key] if short in n.lower() and f'0x{u:08X}' not in acr_own and (not re.search('normal|specular', n, re.I))]

def next_string():
    with open(os.path.join(ROOT, 'strings', 'ids.json'), encoding='utf-8') as fh:
        d = json.load(fh)
    lo, hi = (int(v, 16) for v in d['ranges']['maps'])
    used = [int(k, 16) for k in d['assigned'] if lo <= int(k, 16) <= hi]
    return f'0x{max(used) + 1:08X}' if used else f'0x{lo:08X}'

def mixed_spots(s):
    import math
    m, gcc = (s['map'], struct.pack('<I', convert.class_ids()['GameplayCoordinatorComponent']))
    real, spots = ([], [])
    for fam, (r, _w) in s['rec'].items():
        if r == 'orators' and fam.startswith('AC2MP_GEN_AOrator'):
            members = [s['ents'][u][1] for u in s['fams'][fam]]
            if any((gcc not in o['body'] for o in members)):
                real += sorted((o['name'] for o in members if gcc in o['body']))
                spots += sorted((o['name'] for o in members if gcc not in o['body']))
    planes, used = ([], set())
    if spots:
        pos = lambda o: struct.unpack_from('<3f', o['body'], 58)
        cands = [o for _i, _k, o in m.objects() if o['has_uid'] and xrefs.cls('ACR', o['type']) == 'Entity' and (o['name'] or '').startswith('AC2MP_Merchant_Orator_plane') and (len(o['body']) >= 74)]
        spot_objs = {o['name']: o for _i, _k, o in m.objects() if (o['name'] or '') in spots and xrefs.cls('ACR', o['type']) == 'Entity'}
        for n in spots:
            sp = pos(spot_objs[n])
            near = sorted(((math.hypot(pos(c)[0] - sp[0], pos(c)[1] - sp[1]), c['name']) for c in cands if c['name'] not in used))
            if near and near[0][0] <= 4.0:
                used.add(near[0][1])
                planes.append(near[0][1])
    return (real, spots, planes)

def features(s, fx):
    by = defaultdict(list)
    for fam, (r, _w) in s['rec'].items():
        by[r].append(fam)
    perf, props = (s['perf'], set(s['perf']['prop_families']))
    pieces = sorted(set(by['pieces']) - props)
    proven = sorted({c for f in pieces for u in s['fams'][f] for c in build_static.unknown_checker()(s['ents'][u][1]['body']) if c in PROVEN_LIFT})
    return {'trees': s['trees'], 'tree_prefixes': sorted(s['tree_prefixes']), 'fadein': 'ACFE_Realtree' in fx['fadein'], 'drop': s['drop'], 'lone': sorted(s['lone']), 'spots': sorted({'AC2MP_GEN_AMerchant' if f.startswith('AC2MP_GEN_AMerchant') else 'AC2MP_GEN_AOrator' for f in by['spots']} | set(mixed_spots(s)[1])), 'merchants': bool(by['merchants']), 'orators': bool(by['orators']), 'performers': {'entities': sorted(set(by['performers']) | props & set(by['pieces'])), 'packs': perf['packs'], 'carry': perf['carry'], 'builders': [f'0x{b:08X}' for b in perf['builders']]} if by['performers'] else None, 'pieces': {'entities': pieces, 'proven': proven} if pieces else None, 'sky_evening': fx['sky'].get('texture') if fx['sky'].get('flat') else None, 'area_box': fx.get('area_box')}

def print_report(s, fx, archive):
    rec, fams, perf = (s['rec'], s['fams'], s['perf'])
    print(f"== {archive}: {s['map'].path}")
    print(f"   World {fx['world']} 0x{fx['world_uid']:08X}; grid {fx['grid']}, root {fx['root']}; crowd layout {fx['layouts']}; bank {fx['bank'][0]} {fx['bank'][1]}; " + (f"DLC: installs as {fx['install_as']}" if fx['dlc'] else 'not DLC'))
    print(f"   CTF block {fx['ctf']}" + (f"; NO AA play area: --area-box={fx['area_box']} stands in (flags / spawns + 12 / 6 m)" if fx.get('area_box') else ''))
    print(f"   sky {fx['sky']}; FadeIn tree materials {fx['fadein'] or 'none'}; sound emitters missing in AC3 {fx['missing_emitters']}")
    for e in fx['acr_menu']:
        print(f"   ACR menu {e['name']} at {e['hour']}: {e['strings']}")
    print(f"   AC3 pictures {fx['pictures']}; next maps string id {fx['next_string']}")
    print(f"   stage 0: {' | '.join(s['stage0']) or 'nothing'}")
    print(f"   stage 1 drops {len(s['dropped'])} objects, the chain's lifts bring back {len(s['lifted'])}, {sum((1 for u in s['lost'] if u in s['ents'] and s['map'].kind[s['ents'][u][0]] != 'file'))} placed ones stay out")
    by = defaultdict(list)
    for fam, (r, w) in rec.items():
        by[r].append((fam, w))
    for r in RECIPES:
        if not by[r] or r in ('plain', 'dead'):
            continue
        print(f'   [{r}] {RECIPES[r]}')
        for fam, w in sorted(by[r]):
            uids = fams[fam]
            out = sum((1 for u in uids if u in s['lost']))
            print(f"      {len(uids):4d} {fam[:52]:52s} {('out ' + str(out) if out else ''):7s} {w}")
            for n in s['notes'].get(fam, []):
                print(f'           note: {n}')
    if perf['performers']:
        print(f"   performers: builders {[f'0x{b:08X}' for b in perf['builders']]}; packs {perf['packs']}; carry {perf['carry']}; props {perf['prop_families']}")
    print(f"   plain families {len(by['plain'])}, dead (files no cell lists) {len(by['dead'])}")

def catch(m, prefixes, kinds=('Entity', 'EntityGroup'), among=None):
    return {o['uid'] for _i, _k, o in m.objects() if o['has_uid'] and xrefs.cls('ACR', o['type']) in kinds and matches(o['name'], prefixes) and (among is None or o['uid'] in among)}

def compare(s, feat, want):
    m, out, notes = (s['map'], [], [])
    if not want:
        return (['the config has no FEATURES'], notes)
    placed = {u for u, (idx, _o) in s['ents'].items() if m.kind[idx] != 'file'}

    def missing(f):
        d = stage1(m, SKIP_CLASSES, f.get('drop') or ())
        lf = generic_lifts(m, d, f['trees'], f.get('drop') or ())
        lost = {u for u in d if u not in lf}
        back = set()
        for grp in ('performers', 'pieces'):
            g = f.get(grp) or {}
            back |= catch(m, g.get('entities', []))
            back |= {u for u in lost if m.obj[u]['name'] in set(g.get('packs', []))}
        back |= catch(m, list(f.get('lone') or ()) + list(f.get('tree_prefixes') or ()), ('Entity',), lost)
        return (lost - back & placed, lost)
    (a, lost_a), (b, lost_b) = (missing(feat), missing(want))
    if a != b:
        out.append(f"the build would lack {len(a)} placed objects with the survey's features, {len(b)} with the config's: {sorted((m.obj[u]['name'] for u in a - b))[:5]} / {sorted((m.obj[u]['name'] for u in b - a))[:5]}")
    elif lost_a != lost_b:
        notes.append(f"the same build by another route: the config's drop list leaves out {len(lost_b - lost_a)} objects the survey's lets the lifts keep, and its recipes bring them back ({sorted((m.obj[u]['name'] for u in lost_b - lost_a))[:6]})")
    for grp in ('performers', 'pieces'):
        fa, fb = (feat.get(grp) or {}, want.get(grp) or {})
        for k in ('carry', 'builders'):
            x, y = ({str(v).upper() for v in fa.get(k, [])}, {str(v).upper() for v in fb.get(k, [])})
            if x != y:
                out.append(f'{grp}.{k}: survey {sorted(x - y)}, config {sorted(y - x)}')
    if set(feat['spots']) != set(want.get('spots') or []):
        out.append(f"spots: survey {feat['spots']}, config {want.get('spots')}")
    if sorted(feat.get('orator_planes') or []) != sorted(want.get('orator_planes') or []):
        out.append(f"orator_planes: survey {feat.get('orator_planes')}, config {want.get('orator_planes')}")
    if feat.get('grid') != want.get('grid'):
        out.append(f"grid: survey {feat.get('grid')!r}, config {want.get('grid')!r}")
    if feat.get('night') != want.get('night'):
        out.append(f"night: survey {feat.get('night')!r}, config {want.get('night')!r}")
    for k in ('orator_names', 'spot_planes', 'shift', 'root_layers'):
        if (feat.get(k) or None) != (want.get(k) or None):
            out.append(f'{k}: survey {feat.get(k)!r}, config {want.get(k)!r}')
    kf, kf_cfg = (feat.get('kind_floor') or None, want.get('kind_floor') or None)
    if kf != kf_cfg:
        if kf is None:
            notes.append(f"kind_floor {kf_cfg:#x} where no mesh is hit: rule 1's false matches left as ACR has them")
        else:
            out.append(f'kind_floor: survey {kf!r}, config {kf_cfg!r}')
    if bool(feat.get('tree_users')) != bool(want.get('tree_users')):
        out.append(f"tree_users: survey {feat.get('tree_users')!r}, config {want.get('tree_users')!r}")
    for k in ('trees', 'fadein', 'merchants', 'orators', 'sky_evening'):
        if feat[k] != want.get(k):
            out.append(f'{k}: survey {feat[k]!r}, config {want.get(k)!r}')
    if bool(feat['area_box']) != bool(want.get('area_box')):
        out.append(f"area_box: survey {feat['area_box']!r}, config {want.get('area_box')!r}")
    elif feat['area_box'] != want.get('area_box'):
        notes.append(f"area box: the config's {want.get('area_box')} (the survey proposes {feat['area_box']})")
    return (out, notes)

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('map', help='a configs key or an ACR map archive name')
    ap.add_argument('--check', action='store_true', help="compare with the config's FEATURES; exit 1 on a difference")
    ap.add_argument('--json', help='write the features and facts here')
    a = ap.parse_args(argv)
    import configs
    archive = configs.MAPS[a.map]['archive'] if a.map in configs.MAPS else a.map
    s = survey(mapwalk.archive_path('ACR', archive))
    fx = facts(s['map'], archive)
    print_report(s, fx, archive)
    feat = features(s, fx)
    npc_lines, npc_stops, npc = npc_check(s['map'], s['rec'], s['fams'], s['ents'])
    for k in ('calls', 'speeches'):
        if npc[k]:
            feat[k] = npc[k]
    op_lines, op = orator_planes(s['map'], s['rec'], s['fams'], s['ents'])
    if op:
        feat['orator_planes'] = op
    for line in npc_lines + op_lines:
        print(f'   npc: {line}')
    import grid_window
    import inventory
    shared = {u for u, h in inventory.universe('AC3').items() if any((x[0] in inventory.SHARED for x in h))}
    gw = grid_window.check({s['map'].name(i): p for i, p in s['map'].files.items()}, lambda t: xrefs.cls('ACR', t), shared)
    print('   grid: ' + grid_window.summary(gw))
    if fx['grid'] != '8 x 8' and gw['ok']:
        feat['grid'] = fx['grid']
    elif fx['grid'] != '8 x 8':
        files_ = {s['map'].name(i): p_ for i, p_ in s['map'].files.items()}
        best = None
        for dx in (-64, -32, 0, 32, 64):
            for dy in (-64, -32, 0, 32, 64):
                if (dx, dy) == (0, 0):
                    continue
                r_ = grid_window.check(files_, lambda t: xrefs.cls('ACR', t), shared, center=(-dx, -dy))
                if r_['ok']:
                    off_objs = sum((1 for n in r_['off'] for o in files_[n].objects[1:] if o['has_uid'] and xrefs.cls('ACR', o['type']) in ('Entity', 'EntityGroup')))
                    key = (off_objs, abs(dx) + abs(dy))
                    if best is None or key < best[0]:
                        best = (key, dx, dy)
        if best:
            feat['grid'], feat['shift'] = (fx['grid'], [best[1], best[2]])
            print(f'   shift: the map and its grid by ({best[1]}, {best[2]}) - the window clean, {best[0][0]} placed objects left in never-loaded cells (FEATURES shift: shift_map.py + grid_shift.py last, the AA territories after them)')
    real, spot_names, spot_planes = mixed_spots(s)
    if spot_names:
        feat['orator_names'], feat['spot_planes'] = (real, spot_planes)
        print(f'   orators: {real} real, {spot_names} audience spot(s) on {spot_planes} (FEATURES orator_names / spots / spot_planes)')
    night_line, night = night_check(s['map'])
    print('   night: ' + night_line)
    if night:
        feat['night'] = night
    users = tree_users(s['map']) if feat['trees'] else []
    print('   tree materials on static objects: ' + (', '.join(users) + ' -> FEATURES tree_users (tree_material_users.py)' if users else 'none'))
    if users:
        feat['tree_users'] = True
    tiny = tiny_kinds(s['map'])
    print('   rule 1 on built-in uids: ' + ('; '.join((f"{c} {len(v)} ({', '.join(sorted({n for n, _k, _u in v}))[:120]})" for c, v in sorted(tiny.items()))) or 'none') + (" -> FEATURES kind_floor (a mesh's raw bytes: its buffer sizes)" if 'Mesh' in tiny else ''))
    if 'Mesh' in tiny:
        feat['kind_floor'] = TINY
    gaps = layer_gaps(s['map'], fx['root'])
    print('   root names what only cells and data layers hold: ' + ('; '.join((f'{k} <- {v[:3]}' for k, v in sorted(gaps.items()))) + ' -> FEATURES root_layers (root_closure.py --layers)' if gaps else 'none'))
    if gaps:
        feat['root_layers'] = True
    print('   FEATURES = ' + json.dumps(feat))
    stops = sorted((f for f, (r, _w) in s['rec'].items() if r == 'STOP'))
    if fx['grid'] != '8 x 8' and (not gw['ok']) and (not feat.get('shift')):
        stops.insert(0, f"(the map) grid {fx['grid']}: its play area or what it touches lies outside AC3's +-96 m load window (grid_window.py) - a shift / regrid plan first")
    stops = [x for x in stops if x.startswith('(the map)')] + npc_stops + [x for x in stops if not x.startswith('(the map)')]
    if stops:
        print(f"   STOP: {len(stops)} thing{('' if len(stops) == 1 else 's')} to look at before a build: {stops}")
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump({'archive': archive, 'features': feat, 'facts': fx, 'npc': npc_lines, 'stops': stops}, fh, indent=1, default=str)
    if a.check:
        diff, notes = compare(s, feat, configs.MAPS[a.map].get('FEATURES') if a.map in configs.MAPS else None)
        print('   check: ' + ("the config's features give the build the survey's would" if not diff else f'{len(diff)} difference(s)'))
        for d in diff:
            print('      ' + d)
        for n in notes:
            print('      note: ' + n)
        if diff:
            raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
