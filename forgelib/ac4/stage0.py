"""forgelib/ac4/stage0.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import collections
import difflib
import hashlib
import os
import pickle
import re
import struct
import sys
import time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT, os.path.join(ROOT, 'maps'), os.path.join(ROOT, 'loader'), os.path.join(ROOT, 'ac4chars')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import common
import downgrade
import extra_rules
import fuzzy_walls
import matprops
import world_rules
import ac4mesh
import mapwalk
import particles
import build_tables
import paths
import world
from datafile import compress
from datafile import decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
from transplant import build_object, remap
import skinmesh
skinmesh.narrow.__defaults__ = (None,)
OUT = os.path.join(ROOT, 'out', 'maps')
ANIM = 262342271
BAKE_COLOUR = bytes([255, 255, 255, 255])
PTR = bytes.fromhex('0100')
OBJ_HEAD = bytes.fromhex('0001')
ONE_F_ = struct.pack('<f', 1.0)
SHARED_ARCHIVES = {'DataPC', 'DataPC_extra', 'DataPC_CoreFX', 'DataPC_Fire', 'DataPC_extraparams'}
NEXT_GEN_BLOCK = 'DataBlock_Next_Gen'
DATA_BLOCK_SOURCES = ('DataPC_AC3MP_Plantation', 'DataPC_AC3MP_Boston_Mill', 'DataPC_AC3MP_Boston_Harbou', 'DataPC_AC3MP_Boston_Brewer', 'DataPC_AC3MP_Fort_Wolcott', 'DataPC_AC3MP_Iceberg', 'DataPC_AC3MP_Animus', 'DataPC_AC2MP_Whiteroom')
BULK = {'Mesh', 'TextureMap', 'MaterialTemplate', 'Animation', 'MeshShape', 'GridCellDataBlock', 'NavMeshManager', 'RealTreeMesh', 'FakeEntities', 'World', 'WorldDataLayerManager', 'GridPartition'}
CASCADE = {'Entity', 'EntityGroup', 'LODSelector', '0x824A23BA', 'CustomActionPack', 'CustomAction', 'ObjectPack', 'SoundAmbienceSet', 'AssassinAmbienceRegionCellData', 'WorldParticleData', 'Material'}
import map_configs
MAP = map_configs.TAMPA

def u32(b, at):
    return struct.unpack_from('<I', b, at)[0]

def refs(body):
    n = len(body)
    if n < 64:
        return _refs_py(body)
    import numpy as np
    a = np.frombuffer(body, dtype=np.uint8)
    j = np.flatnonzero((a[6:n - 5] == 1) & (a[7:n - 4] <= 2)) + 6
    v = a[j + 2].astype(np.uint32) | a[j + 3].astype(np.uint32) << 8 | a[j + 4].astype(np.uint32) << 16 | a[j + 5].astype(np.uint32) << 24
    return set(v.tolist())

def _refs_py(body):
    out = set()
    for j in range(6, len(body) - 5):
        if body[j] == 1 and body[j + 1] in (0, 1, 2):
            out.add(u32(body, j + 2))
    return out

def clip_id(body):
    n = u32(body, 4)
    return u32(body, 9 + 12 * n) if 13 + 12 * n <= len(body) else None

def body_of(obj_bytes):
    return obj_bytes[12 + u32(obj_bytes, 8):]
DLF, DLA = (3676127342, 3809842145)

def _filter_at(body, k):
    if k < 0 or k + 8 > len(body) or u32(body, k) != DLF:
        raise ValueError('no DataLayerFilter')
    n = u32(body, k + 4)
    if n > 64 or (n and u32(body, k + 8) != 0):
        raise ValueError('not a DataLayerFilter')
    o = k + 12 if n else k + 8
    acts = []
    for i in range(n):
        if not (u32(body, o) == DLA and body[o + 4] == 0 and (u32(body, o + 9) == 0)):
            raise ValueError('not a DataLayerAction: ' + body[o:o + 17].hex(' '))
        acts.append(u32(body, o + 5))
        o += 13
        if i < n - 1:
            if u32(body, o) != 0:
                raise ValueError('DataLayerAction separator')
            o += 4
    return (acts, o)

def _filter_bytes(keep):
    if not keep:
        return struct.pack('<I', DLF) + bytes(4)
    return struct.pack('<II', DLF, len(keep)) + bytes(4) + bytes(4).join((struct.pack('<I', DLA) + b'\x00' + struct.pack('<I', a) + bytes(4) for a in keep))

def refilter_layers(body, known):
    k = body.rfind(struct.pack('<I', DLF))
    try:
        acts, o = _filter_at(body, k)
    except ValueError as e:
        raise AssertionError(f"{e}: {body[k:].hex(' ')}")
    assert body[o:] == b'\x03', body[k:].hex(' ')
    keep = [a for a in acts if a in known]
    if len(keep) == len(acts):
        return (body, [])
    return (body[:k] + _filter_bytes(keep) + b'\x03', [a for a in acts if a not in known])

def refilter_all(body, known):
    pat = struct.pack('<I', DLF)
    ks, k = ([], body.find(pat, 6))
    while k >= 0:
        ks.append(k)
        k = body.find(pat, k + 4)
    out, gone = (body, [])
    for k in reversed(ks):
        try:
            acts, o = _filter_at(out, k)
        except (ValueError, struct.error):
            continue
        keep = [a for a in acts if a in known]
        if len(keep) != len(acts):
            out = out[:k] + _filter_bytes(keep) + out[o:]
            gone += [a for a in acts if a not in known]
    return (out, gone)

def filter_layers_all(body):
    pat = struct.pack('<I', DLF)
    out, k = ([], body.find(pat, 6))
    while k >= 0:
        try:
            out += _filter_at(body, k)[0]
        except (ValueError, struct.error):
            pass
        k = body.find(pat, k + 4)
    return out

def placed_pos(body):
    return struct.unpack_from('<3f', body, 10 + 48)

def refilter_count(body):
    k = body.rfind(struct.pack('<I', DLF))
    return [u32(body, k + 12 + 17 * i + 5) for i in range(u32(body, k + 4))]

def list_edit(body, instances=(), remove=()):
    n = u32(body, 10)
    m = u32(body, 14 + 6 * n)
    ent = [body[14 + 6 * j:20 + 6 * j] for j in range(n)]
    inst = [e for e in ent[:m] if u32(e, 2) not in remove] + [PTR + struct.pack('<I', u) for u in instances]
    rest = [e for e in ent[m:] if u32(e, 2) not in remove]
    return body[:10] + struct.pack('<I', len(inst) + len(rest)) + b''.join(inst + rest) + struct.pack('<I', len(inst)) + body[18 + 6 * n:]
STOCK_MAPS = ('DataPC_AC3MP_Plantation.forge', 'DataPC_AC3MP_Charlestown_dlc.forge', 'DataPC_AC3MP_Boston_Mill.forge')
LOCAL_ID_CLASSES = {'NavMeshManager', 'Animation'}

def stock_common(maps=STOCK_MAPS):
    cache = os.path.join(common.OUT, 'stock_common.pkl')
    if os.path.exists(cache):
        return pickle.load(open(cache, 'rb'))
    sets, first = ([], {})
    for arc in maps:
        seen = {}
        for rec, d in common.payloads(common.ac3_archive(arc)):
            for o in common.objects_in(d):
                c = common.cls(o['type'], 'AC3')
                if common.has_uid(o) and o['uid'] >= 16777216 and (c not in LOCAL_ID_CLASSES):
                    seen.setdefault(o['uid'], (c, o['name'], rec.name))
        sets.append(set(seen))
        if not first:
            first = seen
    out = {u: first[u] for u in set.intersection(*sets)}
    pickle.dump(out, open(cache, 'wb'))
    return out
AC3_MAPS_ALL = ('DataPC_AC3MP_Plantation.forge', 'DataPC_AC3MP_Charlestown_dlc.forge', 'DataPC_AC3MP_Boston_Mill.forge', 'DataPC_AC3MP_Boston_Brewer.forge', 'DataPC_AC3MP_Boston_Harbou.forge', 'DataPC_AC3MP_Fort_Wolcott.forge', 'DataPC_AC3MP_Iceberg.forge', 'DataPC_AC3MP_Animus.forge', 'DataPC_AC3MP_Martinique_dlc.forge')
TEX2_BLEND_RANGE = {934887009: (1.0, 8.0), 106256753: (-0.5, 0.0917)}
TEX2_ALPHA_ONE = {3208777094}
NEUTRAL_TEXTURES = (('NormalMap', 2558539690), ('SpecularMap', 823796765), ('DiffuseMap', 823796765))
NEUTRAL_KEEP = re.compile('(?i)LUT|lightmap|cube')
SLOT_KINDS = {1560926545: 'DiffuseMap', 821756150: 'NormalMap', 2954313459: 'SpecularMap'}
FOREIGN_TEXTURES_OK = {'AC3MP_BRE_TOD_EXT_LUT_DiffuseMap', 'GEN_WindowTOD_1D_LUT_01A_DiffuseMap', 'GEN_WindowLightmap_DiffuseMap', 'AC3MP_BRE_windowlightmap_DiffuseMap', 'AC3MP_BOS_Cubemap_01A_Map'}

def template_props(min_materials=2, share=0.95):
    key_ = hashlib.md5(repr(sorted(MAP['extra_sources'].items())).encode()).hexdigest()[:8]
    cache = os.path.join(common.OUT, f'template_props3_{key_}.pkl')
    if os.path.exists(cache):
        return pickle.load(open(cache, 'rb'))
    names, mats = ({}, [])
    sources = []
    for a in AC3_MAPS_ALL:
        try:
            sources.append((common.ac3_archive(a), None))
        except FileNotFoundError:
            pass
    extra_paths = {os.path.join(ROOT, p_): set(n_) for p_, n_ in MAP['extra_sources'].items()}
    sources += [(p_, None) for p_ in extra_paths]
    sources += [(os.path.join(paths.AC3_SOURCE, 'DataPC_CoreFX.forge'), None), (os.path.join(paths.AC3_SOURCE, 'DataPC.forge'), lambda n: n.startswith('AC3MP') or 'Bootstrap' in n)]
    for path, limit in sources:
        with Forge(path) as f:
            for r in f.records:
                if not r.size or r.name == 'GlobalMetaFile' or (limit and (not limit(r.name))):
                    continue
                try:
                    objs = mapwalk.Payload(r, f.read(r)).objects
                except Exception:
                    continue
                for o in objs:
                    c = common.cls(o['type'], 'AC3')
                    if common.has_uid(o):
                        names[o['uid']] = (c, o['name'])
                    if c == 'Material':
                        mats.append((o['body'], extra_paths.get(path)))
    per = collections.defaultdict(list)
    for body, only in mats:
        t = material_template(body, names)
        if t is None or (only is not None and t not in only):
            continue
        try:
            m = matprops.parse(body, 'AC3')
        except matprops.ParseError:
            continue
        per[t].append({it.hash: it.to_bytes() for it in m.items})
    out = {}
    for t, lst in per.items():
        if len(lst) < min_materials:
            continue
        cnt = collections.Counter((h for d in lst for h in d))
        always = [h for h, c in cnt.items() if c >= share * len(lst)]
        out[t] = {'always': {h: collections.Counter((d[h] for d in lst if h in d)).most_common(1)[0][0] for h in always}, 'hashes': set(cnt)}
    pickle.dump(out, open(cache, 'wb'))
    return out

def material_template(body, names):
    for j in range(10, min(len(body), 80) - 5):
        if body[j] == 1 and body[j + 1] == 0:
            u = struct.unpack_from('<I', body, j + 2)[0]
            if u in names and names[u][0] == 'MaterialTemplate':
                return names[u][1]
    return None

class AC3Source:

    def __init__(self):
        self.universe = {}
        uid_dir = os.path.join(ROOT, 'out', 'uid')
        for fn in sorted(os.listdir(uid_dir)):
            if fn.startswith('AC3_') and fn.endswith('.pkl'):
                key = fn[4:-10]
                for u, (r, t, n) in pickle.load(open(os.path.join(uid_dir, fn), 'rb'))[0].items():
                    self.universe.setdefault(u, (key, r, common.cls(t, 'AC3'), n))
        self.templates = {n: u for u, (k, r, c, n) in self.universe.items() if c == 'MaterialTemplate'}
        self.by_name = {}
        for u, (k, r, c, n) in self.universe.items():
            self.by_name.setdefault((c, n), u)
        self._records = {}
        self._forges = {}

    def _forge(self, key):
        if key not in self._forges:
            self._forges[key] = Forge(os.path.join(paths.AC3_SOURCE, key + '.forge'))
        return self._forges[key]

    def record_objects(self, key, rec_name):
        f = self._forge(key)
        rec = next((r for r in f.records if r.name == rec_name), None)
        return common.objects_in(decompress(f.read(rec))) if rec is not None else []

    def record(self, key, rec_name):
        k = (key, rec_name)
        if k not in self._records:
            objs = {}
            for o in self.record_objects(key, rec_name):
                if common.has_uid(o):
                    objs.setdefault(o['uid'], o)
            self._records[k] = objs
        return self._records[k]
    MAIN_COPY_SOURCES = (('DataPC_AC3MP_Plantation', ('AC3MP_Plantation', 'Cell00020_DataBlock')), ('DataPC_AC3MP_Boston_Mill', ('AC3MP_Boston_Mill', 'Cell00084_DataBlock')), ('DataPC_AC3MP_Boston_Harbou', ('AC3MP_Boston_Harbour', 'Cell00084_DataBlock')), ('DataPC_AC3MP_Boston_Brewer', ('AC3MP_Boston_Brewery', 'Cell00084_DataBlock')), ('DataPC_AC3MP_Fort_Wolcott', ('AC3MP_Fort_Wolcott', 'Cell00084_DataBlock')), ('DataPC_AC3MP_Iceberg', ('AC3MP_Iceberg', 'Cell00084_DataBlock')), ('DataPC_AC3MP_Animus', ('AC3MP_Animus', 'Cell00084_DataBlock')))

    def main_copy(self, uid):
        for key, recs in self.MAIN_COPY_SOURCES:
            for rec_name in recs:
                o = self.record(key, rec_name).get(uid)
                if o is not None:
                    return (o, f'{key}:{rec_name}')
        return (None, None)

    def add_extra(self, path, names):
        if not hasattr(self, '_extra'):
            self._extra = {}
        objs = {}
        with Forge(path) as f:
            for r in f.records:
                if not r.size or r.name == 'GlobalMetaFile':
                    continue
                try:
                    for o in mapwalk.Payload(r, f.read(r)).objects:
                        if o['has_uid']:
                            objs.setdefault(o['uid'], o)
                except Exception:
                    continue
        found = {}
        queue = [u for u, o in objs.items() if o['name'].strip() in names]
        while queue:
            u = queue.pop()
            if u in self._extra or u in self.universe:
                continue
            o = objs[u]
            self._extra[u] = o
            c = common.cls(o['type'], 'AC3')
            self.universe[u] = ('extra', os.path.basename(path), c, o['name'].strip())
            self.by_name.setdefault((c, o['name'].strip()), u)
            if c == 'MaterialTemplate':
                self.templates.setdefault(o['name'].strip(), u)
            if o['name'].strip() in names:
                found[o['name'].strip()] = u
            queue.extend((v for v in refs(o['body']) if v in objs))
        return found

    def get(self, uid):
        if uid in getattr(self, '_extra', {}):
            return self._extra[uid]
        home = self.universe.get(uid)
        if home is None:
            return None
        if home[2] == 'MaterialTemplate' and home[0] not in SHARED_ARCHIVES:
            o, _where = self.main_copy(uid)
            if o is not None:
                return o
        return self.record(home[0], home[1]).get(uid)

    def shared_archive(self, uid):
        home = self.universe.get(uid)
        return home is not None and home[0] in SHARED_ARCHIVES

    def index_records(self, key, names):
        try:
            f = self._forge(key)
        except FileNotFoundError:
            return 0
        have = {r.name for r in f.records}
        n = 0
        for rec_name in names:
            if rec_name not in have:
                continue
            for u, o in self.record(key, rec_name).items():
                if u not in self.universe:
                    self.universe[u] = (key, rec_name, common.cls(o['type'], 'AC3'), o['name'])
                    n += 1
        return n

    def closure(self, uids, stop, extra=None):
        extra = extra or {}
        out, seen, queue = ([], set(), list(uids))
        while queue:
            u = queue.pop()
            if u in seen or stop(u):
                continue
            seen.add(u)
            o = extra.get(u) or self.get(u)
            if o is None:
                continue
            out.append(o)
            queue.extend((v for v in refs(o['body']) if (v in self.universe or v in extra) and v not in seen))
        return out

    def animations(self, wanted):
        found = {}
        names = {n for n, _c in wanted}
        inv_dir = os.path.join(ROOT, 'out', 'maps', 'inv')
        todo = collections.defaultdict(set)
        for fn in sorted(os.listdir(inv_dir)):
            if not fn.startswith('AC3_'):
                continue
            key = fn[4:-10]
            for row in pickle.load(open(os.path.join(inv_dir, fn), 'rb'))['rows']:
                if row[2] == ANIM and row[3] in names:
                    todo[key, row[0]].add(row[3])
        rows, _meta = pickle.load(open(os.path.join(ROOT, 'out', 'survey', 'AC3_DataPC.forge.pkl'), 'rb'))
        for r in rows:
            if r[2] == ANIM and r[3] in names:
                todo['DataPC', r[0]].add(r[3])
        for (key, rec_name), ns in sorted(todo.items(), key=lambda kv: -len(kv[1])):
            missing = {k for k in wanted if k not in found}
            if not missing:
                break
            if not ns & {k[0] for k in missing}:
                continue
            for o in self.record_objects(key, rec_name):
                if o['type'] == ANIM:
                    k = (o['name'], clip_id(o['body']))
                    if k in wanted and k not in found:
                        found[k] = o
        return found

    def close(self):
        for f in self._forges.values():
            f.close()

class Build:

    def __init__(self, cfg, name, dry):
        self.cfg, self.name, self.dry = (cfg, name, dry)
        self.report = []
        self.src = common.ac4_archive(cfg['archive'])
        self.ac3 = AC3Source()
        for p_, names_ in cfg.get('extra_sources', {}).items():
            got_ = self.ac3.add_extra(os.path.join(ROOT, p_), set(names_))
            self.log(f"extra source {p_}: {', '.join((f'{n} {u:08X}' for n, u in got_.items()))}")
        self.layers = pickle.load(open(os.path.join(common.OUT, '_ac3_layers.pkl'), 'rb'))['names']
        self.notes = collections.Counter()
        self.noted = []
        self.current_rec = None
        self.retargeted = 0
        self.tag_totals = collections.Counter()
        self.imports_needed = set()
        self.keep_ac3 = {}
        self.retargeted_entries = 0

    def log(self, line):
        self.report.append(line)
        print(line, flush=True)

    def load(self):
        t = time.time()
        self.image = ArchiveImage.read(self.src)
        self.image.chunk0_flag = 1
        self.payloads = {}
        with Forge(self.src) as f:
            for rec in f.records:
                if rec.name == 'GlobalMetaFile' or not rec.size:
                    continue
                self.payloads[rec.index] = mapwalk.Payload(rec, f.read(rec))
        self.by_uid = {}
        self.cls = {}
        self.names = {}
        self.rec_by_name = {}
        for idx, p in self.payloads.items():
            self.rec_by_name[p.rec.name] = idx
            for pos, o in enumerate(p.objects):
                if o['has_uid']:
                    self.by_uid.setdefault(o['uid'], []).append((idx, pos))
                    self.cls[o['uid']] = common.cls(o['type'], 'AC4')
                    self.names[o['uid']] = o['name']
        self.log(f'read {len(self.payloads)} records, {sum((len(p.objects) for p in self.payloads.values()))} objects, {len(self.by_uid)} uids in {time.time() - t:.0f} s')

    def first(self, uid):
        idx, pos = self.by_uid[uid][0]
        return self.payloads[idx].objects[pos]
    STILL_WATER_TEMPLATE = 'ACFE_Tex1_PixelLit_Specular_Cubemap'
    STILL_WATER_NAME = re.compile('(?i)water')
    FALLING_WATER_NAME = re.compile('(?i)fall|cascade')

    def default_still_water(self):
        anim = {u for u, c in self.cls.items() if c == 'MaterialTemplate' and self.names[u].strip() == 'ACGA_Tex1_PixelLit_Cubemap_Anim'}
        if not anim:
            return
        mt = dict(self.cfg.get('material_templates', {}))
        added = []
        for u, c in self.cls.items():
            n = self.names[u].strip()
            if c != 'Material' or n in mt or (not self.STILL_WATER_NAME.search(n)) or self.FALLING_WATER_NAME.search(n):
                continue
            b = self.first(u)['body']
            if len(b) > 16 and b[10:12] == PTR and (u32(b, 12) in anim):
                mt[n] = self.STILL_WATER_TEMPLATE
                added.append(n)
        self.cfg = dict(self.cfg, material_templates=mt)
        self.log(f"SW1: flat water on AC4's Cubemap_Anim -> {self.STILL_WATER_TEMPLATE}: {sorted(added) or 'none new'}")

    def plan_fuzzy_walls(self):
        self.fuzzy_plan = {}
        if not self.cfg.get('fuzzy_walls', True):
            return
        bodies = [(self.names[u].strip(), self.first(u)['body']) for u, c in self.cls.items() if c in ('Entity', 'EntityGroup') and u not in self.drop]

        def shape(u):
            if self.cls.get(u) != 'MeshShape' or u in self.drop or u in self.shared:
                return None
            return self.first(u)['body']
        force = {u: tl for name, tl in self.cfg.get('fuzzy_walls_force', {}).items() for u, c in self.cls.items() if c == 'MeshShape' and self.names[u].strip() == name}
        self.fuzzy_plan, st = fuzzy_walls.plan(bodies, shape, lambda u: (self.names.get(u) or '?').strip(), self.cfg.get('fuzzy_walls_keep', fuzzy_walls.KEEP_DEFAULT), force)
        self.log(f'FZ1: unguided surface walls -> FuzzyZone: {dict(st)}; shapes: ' + ', '.join((f'{self.names[u].strip()} {len(t)}' for u, t in sorted(self.fuzzy_plan.items(), key=lambda kv: -len(kv[1]))[:12])))

    def decide(self):
        self.default_still_water()
        cfg = self.cfg
        block_names = [p.rec.name for p in self.payloads.values() if not p.rec.name.startswith('Cell') and p.rec.name != cfg['world_rec'] and (not p.rec.name.endswith('_Fakes'))]
        keys = [cfg['donor_archive']] + [k for k in DATA_BLOCK_SOURCES if k != cfg['donor_archive']]
        added = {k: self.ac3.index_records(k, block_names) for k in keys}
        self.log(f"AC3's layer data blocks indexed by name ({len(block_names)} record names): " + ', '.join((f'{k} +{n}' for k, n in added.items() if n)))
        self.shared = {}
        for u, c in self.cls.items():
            home = self.ac3.universe.get(u)
            if home and home[2] == c:
                self.shared[u] = home
        self.log(f"AC3 defines {len(self.shared)} of the archive's uids under the same class (AC3's bytes)")
        drop = {}
        pats = [re.compile(p) for p in cfg['drop_names']]
        self.uid_subst, self.skinned = ({}, set())
        for u, c in self.cls.items():
            if u in self.shared:
                continue
            n = self.names[u]
            if c in cfg.get('class_substitutes', {}):
                t_ = self.ac3.by_name.get((c, cfg['class_substitutes'][c]))
                if t_ is None:
                    raise SystemExit(f"class_substitutes: AC3 has no {c} {cfg['class_substitutes'][c]}")
                drop[u] = f"substituted by AC3's {cfg['class_substitutes'][c]}"
                self.uid_subst[u] = t_
                self.imports_needed.add(t_)
            elif c in cfg['drop_classes'] or c in downgrade.DROP or common.ac4_only(c):
                drop[u] = f'class {c}'
            elif any((p.search(n) for p in pats)):
                drop[u] = 'name'
            elif u in cfg.get('drop_uids', ()):
                drop[u] = 'uid'
            elif c == 'ParticleSystem' and (not self.cfg.get('convert_particles')):
                drop[u] = 'AC4 particle system'
            elif c == 'ParticleSystem':
                try:
                    particles.convert(self.first(u)['body'])
                except (particles.Note, struct.error, IndexError) as e:
                    drop[u] = f'particle system the rule cannot convert ({e})'
            elif c == 'MaterialTemplate':
                drop[u] = 'AC4 template'
            elif c == 'Mesh' and n not in cfg['mesh_substitutes']:
                try:
                    downgrade.mesh(self.first(u)['body'])
                except downgrade.Note as e:
                    try:
                        problems_ = skinmesh.verify(skinmesh.downgrade(downgrade.skinned_recentred(self.first(u)['body'])))
                    except Exception as e2:
                        problems_ = [f'{e2}']
                    if problems_:
                        drop[u] = f'mesh the rule cannot convert ({e}; skinned: {problems_[0]})'
                    else:
                        self.skinned.add(u)
        for (c, n), _donor in cfg['donors'].items():
            for u in [u for u, cc in self.cls.items() if cc == c and self.names[u] == n]:
                drop[u] = "replaced by Plantation's"
        templates = {u for u, c in self.cls.items() if c == 'MaterialTemplate'}
        fx_classes = {'0x824A23BA', 'ParticleSystem'}
        geometry = {'LODSelector', 'MeshShape'}
        downgrade.STRIP_FX_OWNERS.clear()
        _b = cfg.get('uid_block', 1526333440)
        downgrade.OBJECTAI_IDS = iter(range(_b + 24576, _b + 28672))
        downgrade.KEEP_DECALS = bool(cfg.get('keep_decals', False))
        downgrade.DECAL_STATS.clear()
        changed = True
        while changed:
            changed = False
            dropped_now = set(drop) - templates - set(self.uid_subst)
            for u, c in self.cls.items():
                if u in drop or u in self.shared or c not in CASCADE:
                    continue
                r_ = refs(self.first(u)['body'])
                hit = r_ & dropped_now
                if hit and c in ('Entity', 'EntityGroup') and all((self.cls.get(h) in fx_classes for h in hit)) and any((self.cls.get(v) in geometry and v not in drop for v in r_)) and (b'\xa6\x93\xbf\xd2' in self.first(u)['body']):
                    if u not in downgrade.STRIP_FX_OWNERS:
                        downgrade.STRIP_FX_OWNERS.add(u)
                        self.log(f"   kept without its FX: {c} {self.names[u]} (pointed at {', '.join((self.names[h] for h in hit))})")
                    continue
                if hit:
                    h = next(iter(hit))
                    drop[u] = f'points at dropped {self.cls[h]} {self.names[h]}'
                    changed = True
        referrers = collections.Counter()
        for u, c in self.cls.items():
            if u in drop or (c in BULK and (not (c == 'Mesh' and u in self.skinned))):
                continue
            for v in refs(self.first(u)['body']):
                referrers[v] += 1
        for u, c in self.cls.items():
            if c == 'Material' and u not in drop and (u not in self.shared) and (not referrers[u]):
                drop[u] = 'orphan material'
        self.drop = drop
        self.plan_fuzzy_walls()
        by_reason = collections.Counter((' '.join(r.split(' ')[:2]) if r.startswith(('class', 'points')) else r for r in drop.values()))
        self.log(f'dropping {len(drop)} uids: ' + ', '.join((f'{k} {v}' for k, v in by_reason.most_common())))
        by_cls = collections.Counter((self.cls[u] for u in drop))
        self.log('   by class: ' + ', '.join((f'{k} {v}' for k, v in by_cls.most_common())))
        fam = collections.Counter()
        for u, r in drop.items():
            if self.cls[u] in ('Entity', 'EntityGroup'):
                fam[re.sub('[ _]?(Group_)?\\d+$', '', self.names[u])] += 1
        self.log('   placed objects dropped, by family: ' + ', '.join((f'{k} {v}' for k, v in fam.most_common(60))))
        odd = [(self.cls[u], self.names[u], r) for u, r in drop.items() if self.cls[u] in ('Mesh', '0x824A23BA', 'PathAnimationCamera', 'ObjectPack', 'CustomActionPack', 'RegionLayout') and (not r.startswith('name'))]
        self.log('   other drops worth a look: ' + '; '.join((f'{c} {n} ({r})' for c, n, r in odd[:40])))
        self.template_map = {}
        ac4dp = {}
        for fn in ('AC4_DataPC.forge.pkl', 'AC4_DataPC_extra.forge.pkl', 'AC4_DataPC_CoreFX.forge.pkl'):
            for u, (r, t, n) in pickle.load(open(os.path.join(ROOT, 'out', 'uid', fn), 'rb'))[0].items():
                ac4dp.setdefault(u, n)
        used = collections.Counter()
        for u, c in self.cls.items():
            if c == 'Material' and u not in drop:
                b = self.first(u)['body']
                if len(b) > 16 and b[10:12] == PTR:
                    used[u32(b, 12)] += 1
        for t, k in used.items():
            if t in self.shared or self.ac3.shared_archive(t):
                continue
            n = self.names.get(t) or ac4dp.get(t, f'{t:08X}')
            std = cfg['template_standins'].get(n)
            if std is None:
                for pre in ('ACGAMP_', 'ACGA_'):
                    if n.startswith(pre):
                        for cand in ('AC3MP_' + n[len(pre):], 'AC3MP_' + n[len(pre):] + '_WIP'):
                            if cand in self.ac3.templates:
                                std = cand
                                break
                if std is None and n in self.ac3.templates:
                    std = n
            if std is None or std not in self.ac3.templates:
                std = cfg['template_fallback']
            self.template_map[t] = self.ac3.templates[std]
            self.names.setdefault(t, n)
        self.log('templates: ' + ', '.join((f'{self.names[u]} -> {self.ac3.universe[t][3]}' for u, t in sorted(self.template_map.items(), key=lambda kv: self.names[kv[0]]))))
        wanted = set()
        for p in self.payloads.values():
            for o in p.objects:
                if o['type'] == ANIM:
                    wanted.add((o['name'], clip_id(o['body'])))
        t = time.time()
        self.anims = self.ac3.animations(wanted)
        missing = sorted({n for n, c in wanted if (n, c) not in self.anims})
        self.log(f"animations: {len(wanted)} (name, clip) pairs; AC3's copies for {len(self.anims)} in {time.time() - t:.0f} s; kept as AC4's: {len(missing)}: {', '.join(missing[:12])}{(' ...' if len(missing) > 12 else '')}")
        self.plan_clutter()

    def plan_clutter(self):
        self.clutter_plan, self.clutter_map, self.clutter_src = ({}, {}, None)
        cl = self.cfg.get('clutter')
        if not cl:
            return
        import clutter

        def one(name):
            us = [u for u, c in self.cls.items() if c == 'Entity' and self.names[u].strip() == name]
            if len(us) != 1:
                raise SystemExit(f'clutter: {len(us)} entities named {name}')
            return us[0]
        self.clutter_src = self.first(one(cl['entity']))['body']
        names_ = cl['hosts']
        if isinstance(names_, dict):
            import clutter
            root_i = self.rec_by_name[self.cfg['root']]
            cand = {}
            for o_ in self.payloads[root_i].objects:
                n_ = o_['name'].strip()
                if not o_['has_uid'] or self.cls.get(o_['uid']) != 'Entity' or o_['uid'] in self.drop or (o_['uid'] in self.shared) or (b')\x8de\xec' not in o_['body']):
                    continue
                if clutter.entity_pos(o_['body'])[1]:
                    cand[n_] = o_['uid']
            main_ = sorted((n_ for n_ in cand if re.search(names_['pattern'], n_) and (not re.search(names_['overflow'], n_))))
            over_ = sorted((n_ for n_ in cand if re.search(names_['overflow'], n_)))
            if not main_ or not over_:
                raise SystemExit(f'clutter hosts by pattern: {main_} + overflow {over_}')
            names_ = main_ + over_
        hosts = []
        for n in names_:
            u = one(n)
            if u in self.drop or u in self.shared:
                raise SystemExit(f"clutter host {n}: {('dropped' if u in self.drop else 'AC3 bytes')}")
            hosts.append((u, n, self.first(u)['body']))
        key, rec = cl['template_rec']
        tpl = next((o for o in self.ac3.record(key, rec).values() if o['name'].strip() == cl['template']), None)
        if tpl is None:
            raise SystemExit(f"clutter template {cl['template']} not in {key}:{rec}")
        tpl_chunk = clutter.extract(tpl['body'])[0]
        self.clutter_objs, self.clutter_copies = ([], [])
        pc = cl.get('piece')
        if pc:
            objs = self.ac3.record(key, rec)
            by_n = {o['name'].strip(): o for o in objs.values()}
            t_mesh, t_mat = (by_n[pc['mesh']], by_n[pc['material']])
            swaps = {}
            for old_n, new_n in pc['swaps'].items():
                nu = [u for u, c in self.cls.items() if self.names[u].strip() == new_n and u not in self.drop]
                if len(nu) != 1:
                    raise SystemExit(f'clutter piece: {len(nu)} kept objects named {new_n}')
                swaps[by_n[old_n]['uid']] = nu[0]
            self.clutter_copies = sorted(swaps.values())
            mesh_uid, mat_uid = (pc['fresh'], pc['fresh'] + 1)
            fresh_ = iter(range(pc['fresh'] + 16, pc['fresh'] + 256))
            mat_body, m_ = clutter.clone_object(t_mat['body'], mat_uid, swaps)
            piece = clutter.ac4_piece(clutter.decode_ac4(self.clutter_src))
            mesh_body = clutter.author_mesh(t_mesh['body'], mesh_uid, (pc['fresh'] + 2, pc['fresh'] + 3), mat_uid, piece)
            self.clutter_objs = [(mesh_uid, build_object(t_mesh['type'], len(mesh_body) - 1, pc['names'][0], mesh_body)), (mat_uid, build_object(t_mat['type'], len(mat_body) - 1, pc['names'][1], mat_body))]
            tpl_chunk = dict(tpl_chunk, settings=tpl_chunk['settings'][:28] + struct.pack('<I', mesh_uid) + tpl_chunk['settings'][32:])
            ids_ = [mesh_uid, mat_uid, pc['fresh'] + 2, pc['fresh'] + 3]
            clash = [i for i in ids_ if i in self.by_uid or i in self.ac3.universe]
            if clash:
                raise SystemExit(f'clutter piece ids already objects: {[hex(i) for i in clash]}')
            self.log(f"   clutter piece (CL2): AC4's piece ({piece['copies']} copies in its buffer) = {len(piece['pos'])} vertices, {len(piece['idx']) // 3} triangles -> {pc['names'][0]} {mesh_uid:#x} ({len(mesh_body)} B), material {pc['names'][1]} {mat_uid:#x} (a copy of {pc['material']}, its property hashes kept, textures -> {', '.join((f'{self.names[v].strip()}' for v in swaps.values()))})")
        self.clutter_plan, self.clutter_map, lines, nxt = clutter.plan(self.clutter_src, hosts, tpl_chunk, cl['fresh'], S=cl.get('S'))
        if pc and nxt > pc['fresh']:
            raise SystemExit(f"clutter ids run into the piece's block ({nxt:#x} > {pc['fresh']:#x})")
        taken = [i for i in range(cl['fresh'], nxt) if i in self.by_uid or i in self.ac3.universe]
        if taken:
            raise SystemExit(f"clutter ids {cl['fresh']:#x}-{nxt - 1:#x}: {len(taken)} already objects ({taken[0]:#x})")
        for ln in lines:
            self.log('   ' + ln)

    def convert_object(self, o):
        d, tag = self._convert_object(o)
        if d is not None and o.get('uid') in getattr(self, 'fuzzy_plan', {}) and (common.cls(o['type'], 'AC4') == 'MeshShape'):
            nb = fuzzy_walls.apply(body_of(d), self.fuzzy_plan[o['uid']])
            d = build_object(o['type'], len(nb) - 1, o['name'], nb)
            self.fuzzy_done = getattr(self, 'fuzzy_done', 0) + 1
        if d is not None and common.cls(o['type'], 'AC4') in ('Entity', 'EntityGroup'):
            body, n = downgrade.no_cell_index(body_of(d))
            if n:
                d = d[:12 + u32(d, 8)] + body
                self.cells_cleared += n
        return (d, tag)

    def _convert_object(self, o):
        c = common.cls(o['type'], 'AC4')
        if o['type'] == ANIM:
            a = self.anims.get((o['name'], clip_id(o['body'])))
            if a is not None:
                return (build_object(a['type'], a['body_size'], a['name'], a['body']), "animation: AC3's")
            return (mapwalk.object_bytes(o), "animation: AC4's")
        if not o['has_uid']:
            return (mapwalk.object_bytes(o), 'no uid: kept')
        u = o['uid']
        if u in self.drop:
            return (None, 'dropped')
        if c == 'PropertyControllerData' and o['name'].strip() in self.cfg.get('tod_rule', {}):
            import tod
            want = self.cfg['tod_rule'][o['name'].strip()]
            t_ = next((x for x in self.ac3.record(self.cfg['donor_archive'], self.cfg['donor_rec']).values() if x['name'].strip() == want))
            body, log_ = tod.convert(t_['body'], o['body'], uid=u, night_ac3=o['name'].strip() in self.cfg.get('tod_night_ac3', ()), sun_scale=self.sun_scale(o))
            self.log(f"   time of day: {o['name'].strip()} = TR1 over {self.cfg['tod_rule'][o['name'].strip()]}: {log_}")
            return (build_object(o['type'], len(body) - 1, o['name'], body), 'time of day: TR1')
        if u in self.shared:
            a = self.ac3.get(u)
            if a is not None:
                b_ = a['body']
                own_t = self.cfg.get('material_templates', {}).get(o['name'].strip()) if c == 'Material' else None
                if own_t is not None and len(b_) > 16 and (b_[10:12] == PTR):
                    b_ = b_[:12] + struct.pack('<I', self.ac3.templates[own_t]) + b_[16:]
                    self.log(f"   material {o['name'].strip()} (AC3's bytes) -> template {own_t}")
                return (build_object(a['type'], a['body_size'], a['name'], b_), "AC3's bytes")
            if self.ac3.shared_archive(u):
                return (None, "AC3's shared archive defines it")
        if c == 'Mesh' and o['name'] in self.cfg['mesh_substitutes']:
            name = self.cfg['mesh_substitutes'][o['name']]
            du = self.ac3.by_name.get(('Mesh', name))
            a = self.ac3.get(du) if du else None
            if a is None:
                raise SystemExit(f"mesh substitute {name} not found in AC3's maps")
            body = a['body'][:2] + struct.pack('<I', u) + a['body'][6:]
            body = self.substitute_materials(o, body)
            self.imports_needed.update((v for v in refs(body) if v in self.ac3.universe))
            return (build_object(a['type'], a['body_size'], o['name'], body), 'mesh: AC3 substitute')
        if c == 'Mesh' and u in self.skinned:
            return (mapwalk.object_bytes(o, skinmesh.downgrade(downgrade.skinned_recentred(o['body']))), 'converted')
        if c == 'ParticleSystem':
            body = particles.convert(o['body'])
            tints = self.cfg.get('particle_tints', {}).get(o['name'].strip())
            if tints:
                body = particles.tint(body, tints)
            return (mapwalk.object_bytes(o, body), 'converted')
        src_ = o['body']
        if c == 'TextureSet' and len(src_) >= 82 and (src_[76] == 1):
            src_ = src_[:76] + downgrade.EMPTY_SLOT + src_[82:]
            self.notes['TextureSet RVB mask slot emptied (crowd characters)'] += 1
        body, done = downgrade.convert(c, src_)
        body, note = extra_rules.convert(c, body)
        if c in ('BuildTable', 'EntityBuilder'):
            body = build_tables.convert(body)
        if c == 'EntityGroup' and re.match(self.cfg.get('pigeon_prefix', 'AC3MP_GEN_AnimalAlarm_Pigeon'), o['name'].strip()) and self.cfg.get('pigeon_template'):
            body = self.pigeon(body)
        if c == 'Entity' and any((re.search(p_, o['name'].strip()) for p_ in self.cfg.get('night_lights', ()))):
            body = downgrade.night_light(body)
            self.notes['placed lights made Night-only runtime lights'] += 1
        if getattr(self, 'uid_subst', None):
            body, n_s = remap(body, self.uid_subst)
            if sum(n_s.values()) if hasattr(n_s, 'values') else n_s:
                self.notes['references to substituted objects (class_substitutes)'] += sum(n_s.values()) if hasattr(n_s, 'values') else n_s
        if c in ('Entity', 'EntityGroup'):
            body, n_ = self.retarget_instances(body)
            self.retargeted_entries += n_
        if c == 'Mesh' and o['name'].strip() in self.cfg.get('mesh_vertex_rgb_white', ()):
            stride_, count_, at_ = ac4mesh.layout(body)
            out_ = bytearray(body)
            floor_ = self.cfg.get('mesh_vertex_rgb_floor', {}).get(o['name'].strip())
            ref_ = self.cfg.get('mesh_vertex_alpha_from_rgb', {}).get(o['name'].strip())
            for v_ in range(count_):
                k_ = at_ + v_ * stride_ + 16
                if ref_ is not None:
                    a_ = min(out_[k_ + 3], min(255, round(255 * max(out_[k_:k_ + 3]) / ref_)))
                    g_ = 255
                    nz_ = (out_[at_ + v_ * stride_ + 10] - 127.5) / 127.5
                    for min_nz_, rgb_, scale_, cap_ in self.cfg.get('mesh_vertex_regions', {}).get(o['name'].strip(), ()):
                        if nz_ >= min_nz_:
                            g_, a_ = (rgb_, min(cap_, round(a_ * scale_)))
                            if isinstance(rgb_, tuple):
                                a_ = cap_
                            break
                    rgb3_ = g_ if isinstance(g_, tuple) else (g_, g_, g_)
                    out_[k_:k_ + 4] = bytes(rgb3_) + bytes((a_,))
                elif floor_ is None:
                    out_[k_:k_ + 3] = bytes((255, 255, 255))
                else:
                    out_[k_:k_ + 3] = bytes((max(c_, floor_) for c_ in out_[k_:k_ + 3]))
            uvs_ = self.cfg.get('mesh_uv_scale', {}).get(o['name'].strip())
            if uvs_ is not None:
                min_nz_, f_ = (uvs_[0], uvs_[1])
                fs_ = {20: f_, 22: uvs_[2] if len(uvs_) > 2 else f_}
                sel_ = [v_ for v_ in range(count_) if (out_[at_ + v_ * stride_ + 10] - 127.5) / 127.5 >= min_nz_]
                for j_ in (20, 22):
                    f_ = fs_[j_]
                    vals_ = [struct.unpack_from('<h', out_, at_ + v_ * stride_ + j_)[0] for v_ in sel_]
                    m_ = sum(vals_) / len(vals_)
                    for v_, x_ in zip(sel_, vals_):
                        struct.pack_into('<h', out_, at_ + v_ * stride_ + j_, round(m_ + (x_ - m_) * f_))
                self.log(f"   mesh {o['name'].strip()}: UVs of {len(sel_)} vertices (normal z >= {min_nz_}) scaled x{f_}")
            body = bytes(out_)
            self.log(f"   mesh {o['name'].strip()}: vertex RGB " + (f"by region {self.cfg.get('mesh_vertex_regions', {}).get(o['name'].strip())}, alpha from AC4's RGB" if ref_ is not None else 'white' if floor_ is None else f'floored at {floor_:#04x}') + f' on {count_} vertices (alpha kept)')
        if c == 'Entity' and u in getattr(self, 'clutter_plan', {}):
            import clutter
            chunk_, cc_ = self.clutter_plan[u]
            body = clutter.insert(body, chunk_, cc_)
            self.notes['ground clutter chunks inserted (CL1t)'] += 1
        tag = 'unruled' if 'unruled' in done and note is None and (c not in extra_rules.RULES) else 'converted'
        for k_ in done:
            if ':' in k_:
                self.notes[k_] += 1
                if 'stand-in' not in k_:
                    self.noted.append((c, o['name'], self.current_rec, k_))
        if note:
            self.notes[f'{c}: {note}'] += 1
            self.noted.append((c, o['name'], self.current_rec, f'{c}: {note}'))
        if c == 'Material' and self.template_map:
            body, n = remap(body, self.template_map)
            if n:
                self.retargeted += sum(n.values())
            own_t = self.cfg.get('material_templates', {}).get(o['name'].strip())
            if own_t is not None and len(body) > 16 and (body[10:12] == PTR):
                body = body[:12] + struct.pack('<I', self.ac3.templates[own_t]) + body[16:]
            body = self.complete_material(body, o['name'])
        return (mapwalk.object_bytes(o, body), tag)

    def sun_scale(self, o):
        import tod
        n = o['name'].strip()
        if n in self.cfg.get('tod_sun_scale', {}):
            return self.cfg['tod_sun_scale'][n]
        if n in self.cfg.get('tod_sun_target', {}):
            cur = tod.parse(o['body'])[tod.LS, 4228852420]
            noon = tod._at(cur, 12 * tod.KEYS_PER_HOUR)[0]
            self.log(f"   time of day: {n} - the day sun {noon:.3f} at noon -> {self.cfg['tod_sun_target'][n]}")
            return self.cfg['tod_sun_target'][n] / noon
        return 1.0

    def complete_material(self, body, name):
        if not hasattr(self, '_tprops'):
            self._tprops = template_props()
            self._tnames = {u: ('MaterialTemplate', n) for n, u in self.ac3.templates.items()}
            self.completed = collections.Counter()
            self.completed_items = collections.Counter()
            self.stripped = collections.Counter()
            self.stripped_hashes = collections.Counter()
            self.clamped = collections.Counter()
        t = material_template(body, self._tnames)
        table = self._tprops.get(t)
        if not table:
            return body
        try:
            m = matprops.parse(body, 'AC3')
        except matprops.ParseError:
            return body
        changed = False
        items = []
        for it in m.items:
            if it.hash not in table['hashes']:
                self.stripped[t] += 1
                self.stripped_hashes[t, f'{it.hash:08X}'] += 1
                changed = True
                continue
            if t == 'AC3MP_Tex2_PixelLit' and it.kind == 10 and (it.hash in TEX2_BLEND_RANGE):
                lo, hi = TEX2_BLEND_RANGE[it.hash]
                v = struct.unpack('<f', it.value)[0]
                if v < lo or v > hi:
                    it = matprops.Item(it.hash, it.cls, it.flags, value=struct.pack('<f', min(max(v, lo), hi)))
                    self.clamped[t, f'{it.hash:08X}'] += 1
                    changed = True
            if t == 'AC3MP_Tex2_PixelLit' and it.kind == 13 and (it.hash in TEX2_ALPHA_ONE) and (it.value[12:16] != struct.pack('<f', 1.0)):
                it = matprops.Item(it.hash, it.cls, it.flags, value=it.value[:12] + struct.pack('<f', 1.0))
                self.clamped[t, f'{it.hash:08X}'] += 1
                changed = True
            items.append(it)
        have = {it.hash for it in items}
        extra = [self.neutral_texture(t, name, table['always'][h]) for h in table['always'] if h not in have]
        if extra:
            self.completed[t] += 1
            self.completed_items[t] += len(extra)
        if not extra and (not changed):
            return body
        return m.head + struct.pack('<I', len(items) + len(extra)) + b''.join((it.to_bytes() for it in items)) + b''.join(extra)

    def neutral_texture(self, t, name, item):
        if not hasattr(self, 'neutralised'):
            self.neutralised, self.neutralised_names = (collections.Counter(), set())
        h_, cls_, flags_ = struct.unpack_from('<III', item, 0)
        if flags_ >> 16 & 63 != matprops.INLINE_KIND or cls_ != matprops.TEXTURE_SELECTOR or item[-6:-4] != PTR:
            return item
        tex = u32(item, len(item) - 4)
        home = self.ac3.universe.get(tex)
        if home is not None and home[0] in SHARED_ARCHIVES:
            return item
        tname = home[3] if home is not None else SLOT_KINDS.get(h_, '')
        if NEUTRAL_KEEP.search(tname):
            self.neutralised[t, f'{h_:08X}', f'kept {tname}'] += 1
            return item
        new = next((v for suffix, v in NEUTRAL_TEXTURES if tname.endswith(suffix)), None)
        if new is None:
            return item
        self.neutralised[t, f'{h_:08X}', f'{tname or hex(tex)} -> {self.ac3.universe[new][3]}'] += 1
        self.neutralised_names.add(name.strip())
        return item[:-4] + struct.pack('<I', new)
    SUPPORTED_MODE = 4256899118

    def supported_modes(self, body):
        import pickle
        if not hasattr(self, '_mode_idx'):
            ld = lambda g: pickle.load(open(os.path.join(ROOT, 'out', 'uid', f'{g}_DataPC.forge.pkl'), 'rb'))[0]
            self._mode_idx = (ld('AC3'), ld('AC4'))
        ac3, ac4 = self._mode_idx
        ac3_by_name = {n: u for u, (r, t, n) in ac3.items() if n.startswith('GameMode ')}
        out = bytearray(body)
        cls = struct.pack('<I', self.SUPPORTED_MODE)
        k = out.find(cls)
        while k >= 0:
            if out[k + 4:k + 6] == b'\x01\x00':
                u = struct.unpack_from('<I', out, k + 6)[0]
                if u not in ac3:
                    name = ac4.get(u, (None, None, None))[2]
                    v = ac3_by_name.get(name)
                    if v is None:
                        raise SystemExit(f"World: supported game mode {u:#x} ({name}) - AC3's DataPC has no such mode")
                    struct.pack_into('<I', out, k + 6, v)
                    self.log(f"   World: supported game mode {u:#x} ({name}, AC4 only) -> AC3's {v:#x}")
            k = out.find(cls, k + 4)
        return bytes(out)
    DLC_WORLD = struct.pack('<I', 3112919362)

    def dlc_world_refs(self, body):
        k = body.find(self.DLC_WORLD)
        if k < 0:
            return body
        n = struct.unpack_from('<I', body, k + 4)[0]
        if not 0 < n < 64:
            return body
        entries = [body[k + 8 + 6 * i:k + 14 + 6 * i] for i in range(n)]
        if any((e[:2] != b'\x01\x00' for e in entries)):
            raise SystemExit(f"DLCWorldComponent: entries not in the `01 00 uid` form: {[e.hex(' ') for e in entries]}")
        keep = [e for e in entries if struct.unpack_from('<I', e, 2)[0] not in self.drop]
        if len(keep) == n:
            return body
        gone = [hex(struct.unpack_from('<I', e, 2)[0]) for e in entries if e not in keep]
        self.log(f'   World: DLCWorldComponent - {n - len(keep)} addon(s) naming dropped objects removed {gone}')
        return body[:k + 4] + struct.pack('<I', len(keep)) + b''.join(keep) + body[k + 8 + 6 * n:]

    def world_file(self, p):
        cfg = self.cfg
        objs = p.objects
        self.current_rec = p.rec.name
        W = objs[0]
        assert common.cls(W['type'], 'AC4') == 'World', 'the World is not the first object of its record'
        dropped_layouts = {u for u in self.drop if self.cls[u] == 'RegionLayout'}
        layer_ok = lambda layer, elem: layer in self.layers and elem != 0
        body, grid, layers, log = world_rules.convert_world(W['body'], keep_wps=True, keep_events=False, fakes=False, drop_layouts=dropped_layouts, layer_filter=layer_ok)
        for ln in log:
            self.log('   World: ' + ln)
        body = self.supported_modes(body)
        body = self.dlc_world_refs(body)
        donor_objs = self.ac3.record(cfg['donor_archive'], cfg['donor_rec'])
        donor_by_name = {o['name']: o for o in donor_objs.values()}
        uid_map = {}
        for (c, n), dn in cfg['donors'].items():
            host = [u for u, cc in self.cls.items() if cc == c and self.names[u] == n]
            d = donor_by_name.get(dn)
            if not host or d is None:
                raise SystemExit(f'donor {dn} / host {n} not found')
            uid_map[host[0]] = d['uid']
        body, n = remap(body, uid_map)
        self.log(f"   World: {sum(n.values())} references moved to Plantation's ambiance / sky / time of day")
        keep_uids = {u for u in self.cls if u not in self.drop}
        stop = lambda u: self.ac3.shared_archive(u) or u in keep_uids
        closure = self.ac3.closure(list(uid_map.values()), stop, extra=donor_objs)
        self.log(f"   World: {len(closure)} objects imported from Plantation's World file: " + ', '.join((f"{o['name']}[{common.cls(o['type'], 'AC3')}]" for o in closure)))
        events = []
        for ev in cfg.get('weather_events', ()):
            d_ = donor_by_name[ev['donor']]
            eb = d_['body'][:2] + struct.pack('<I', ev['uid']) + d_['body'][6:]
            for dn, hn in ev.get('remap', {}).items():
                du = donor_by_name[dn]['uid']
                hu = next((u for u, c_ in self.cls.items() if self.names[u] == hn))
                assert eb.count(struct.pack('<I', du)) == 1, (dn, eb.count(struct.pack('<I', du)))
                eb = eb.replace(struct.pack('<I', du), struct.pack('<I', hu))
            if ev.get('wwise') is not None:
                segs_ = world.segments(eb)
                at, n_w = (0, 0)
                parts = []
                for sn, sg in segs_:
                    if sn == 'WwiseID' and u32(sg, 4) == 0:
                        sg = sg[:4] + struct.pack('<I', ev['wwise']) + sg[8:]
                        n_w += 1
                    parts.append(sg)
                assert n_w == 1, n_w
                eb = b''.join(parts)
            events.append(ev['uid'])
            self.weather_objs = getattr(self, 'weather_objs', []) + [(ev['uid'], build_object(d_['type'], len(eb) - 1, d_['name'], eb))]
            self.log(f"   World: weather event {d_['name']} as {ev['uid']:#x} (TOD -> {list(ev.get('remap', {}).values())}, sound {ev.get('wwise', 0):#x})")
        rain_cfg = cfg.get('rain')
        self.rain_wpds, self.rain_cube, rain_closure_roots = ({}, None, [])
        if rain_cfg:
            import rain as rain_mod
            d_ev = donor_by_name[rain_cfg['event']]
            eb = d_ev['body'][:2] + struct.pack('<I', rain_cfg['event_uid']) + d_ev['body'][6:]
            self.weather_objs = getattr(self, 'weather_objs', []) + [(rain_cfg['event_uid'], build_object(d_ev['type'], len(eb) - 1, d_ev['name'], eb))]
            events.append(rain_cfg['event_uid'])
            segs_w = world.segments(body)
            owner, ripple_sets = (None, [])
            for sn, sg in segs_w:
                if sn != 'WorldParticleSystem':
                    continue
                own_, regs_, proto_, fx_, tail_ = rain_mod.parse_wps(sg)
                owner = own_
                if not self.names.get(proto_, '').startswith('ACGAMP_Fx_WPS_ripples'):
                    continue
                cells_ = {}
                for r_ in regs_:
                    if r_ in self.drop or r_ not in self.by_uid:
                        continue
                    rec_ = self.payloads[self.by_uid[r_][0][0]].rec.name
                    _box, pts_ = rain_mod.wpd_points(self.first(r_)['body'])
                    cells_.setdefault(rec_, []).extend(pts_)
                ripple_sets.append(cells_)
            means = [sum((p[2] for ps in d.values() for p in ps)) / max(1, sum((len(ps) for ps in d.values()))) for d in ripple_sets]
            roles = rain_mod.plan(ripple_sets, means.index(max(means)))
            d_world = next((o for o in donor_objs.values() if common.cls(o['type'], 'AC3') == 'World'))
            p_wpd, p_cube = ({}, None)
            with Forge(common.ac3_archive(cfg['donor_archive'] + '.forge')) as f_:
                cells_r = {r.name for r in f_.records if r.name.startswith('Cell')}
            for rec_, d_ in common.payloads(common.ac3_archive(cfg['donor_archive'] + '.forge'), cells_r):
                for o_ in common.objects_in(d_):
                    if common.cls(o_['type'], 'AC3') == 'WorldParticleData':
                        p_wpd[o_['uid']] = o_['body']
                    elif o_['name'].strip() == rain_cfg['cube']:
                        p_cube = o_
            p_sys = []
            for sn, sg in world.segments(d_world['body']):
                if sn == 'WorldParticleSystem':
                    own_, regs_, proto_, fx_, tail_ = rain_mod.parse_wps(sg)
                    pn = next((o['name'].strip() for o in donor_objs.values() if o['uid'] == proto_), '')
                    if 'Rain' in pn:
                        box_, pts_ = rain_mod.wpd_points(next((p_wpd[r_] for r_ in regs_ if r_ in p_wpd)))
                        p_sys.append((pn, proto_, fx_, rain_mod.pad_of(box_, pts_)))
            if len(p_sys) != 5:
                raise SystemExit(f"rain: Plantation's rain systems {[x[0] for x in p_sys]}")
            nxt = rain_cfg['uid_base']
            new_sys, n_pts = ([], 0)
            for (role, cells_), (pn, proto_, fx_, pad_) in zip(roles, p_sys):
                own_uid, nxt = (nxt, nxt + 1)
                wpd_uids = []
                for rec_, pts_ in sorted(cells_.items()):
                    if not pts_:
                        continue
                    wb = rain_mod.wpd_body(nxt, pts_, pad_)
                    self.rain_wpds.setdefault(rec_, []).append((nxt, build_object(rain_mod.WPD_CLASS, len(wb) - 1, f"CellID={rec_[4:9].lstrip('0') or '0'}", wb)))
                    wpd_uids.append(nxt)
                    nxt += 2
                    n_pts += len(pts_)
                new_sys.append((own_uid, wpd_uids, proto_, fx_))
                rain_closure_roots.append(proto_)
                self.log(f'   World: rain system {pn} ({role}) as {own_uid:#x}: {len(wpd_uids)} cells')
            body = rain_mod.extend_world(world.segments(body), owner, new_sys)
            rain_closure_roots += [v for v in set(refs(eb)) if any((o['uid'] == v for o in donor_objs.values()))]
            self.rain_closure_roots = rain_closure_roots
            cobj = p_cube
            cu = cobj['uid']
            cb = bytearray(cobj['body'])
            segs_c = world.segments(bytes(cb))
            at_ = sum((len(sg) for _, sg in segs_c[:1])) + 4 + 48
            struct.pack_into('<3f', cb, at_, *rain_cfg['cube_pos'])
            cbody, n_ci = downgrade.no_cell_index(bytes(cb))
            self.rain_cube = (cu, build_object(cobj['type'], cobj['body_size'], cobj['name'], cbody))
            self.log(f"   World: rain - {len(new_sys)} systems, {n_pts} points in {len(self.rain_wpds)} cells; event {rain_cfg['event']} as {rain_cfg['event_uid']:#x}; cube {rain_cfg['cube']} at {rain_cfg['cube_pos']}")
        if events:
            parts, done_ = ([], 0)
            for sn, sg in world.segments(body):
                if sn == 'MapEventManager':
                    assert u32(sg, 4) == 0 and len(sg) == 13, sg.hex()
                    sg = sg[:4] + struct.pack('<I', len(events)) + b''.join((bytes((1, 0)) + struct.pack('<I', e_) for e_ in events)) + sg[8:]
                    done_ += 1
                parts.append(sg)
            assert done_ == 1
            body = b''.join(parts)
        sky = cfg.get('sky_clouds')
        sky_objs, sky_drop = ([], set())
        if sky:
            import pigeons
            by_n = {o['name'].strip(): o for o in closure}
            amb = by_n.get(sky['ambiance']) or donor_by_name[sky['ambiance']]
            ls, mat, old_tex = (by_n[sky['layered']], by_n[sky['material']], by_n[sky['old_texture']])
            tex = next((u for u, c_ in self.cls.items() if self.names[u] == sky['texture'] and u not in self.drop))
            nxt = sky['fresh']

            def renew(o_, top, extra):
                import clutter
                b_, m_ = clutter.clone_object(o_['body'], top, extra)
                return (build_object(o_['type'], len(b_) - 1, o_['name'], b_), m_)
            new_mat, nm_ = (sky['fresh'], None)
            nxt = new_mat
            mat_bytes, nm_ = renew(mat, new_mat, {old_tex['uid']: tex})
            nxt += 1
            new_ls = nxt
            ls_bytes, nl_ = renew(ls, new_ls, {mat['uid']: new_mat})
            nxt += 1
            new_amb = nxt
            amb_bytes, na_ = renew(amb, new_amb, {ls['uid']: new_ls})
            assert body.count(struct.pack('<I', amb['uid'])) == 1, body.count(struct.pack('<I', amb['uid']))
            body = body.replace(struct.pack('<I', amb['uid']), struct.pack('<I', new_amb))
            sky_objs = [(new_mat, mat_bytes), (new_ls, ls_bytes), (new_amb, amb_bytes)]
            sky_drop = {amb['uid'], ls['uid'], mat['uid'], old_tex['uid']}
            self.log(f"   World: sky - {sky['ambiance']} as {new_amb:#x}, {sky['layered']} as {new_ls:#x}, {sky['material']} as {new_mat:#x} (copies: own uid + the references between them only, property hashes kept), clouds {sky['old_texture']} -> {sky['texture']}")
        closure = [o for o in closure if o['uid'] not in sky_drop]
        out = [(W['serial'], mapwalk.object_bytes(W, body), W['filter'])]
        for e_uid, e_bytes in sky_objs:
            out.append((e_uid, e_bytes, None))
        for e_uid, e_bytes in getattr(self, 'weather_objs', []):
            out.append((e_uid, e_bytes, None))
        rest = objs[1:]
        for o in [o for o in rest if common.cls(o['type'], 'AC4') == 'WorldArea']:
            out.append((o['serial'], mapwalk.object_bytes(o), o['filter']))
        rest = [o for o in rest if common.cls(o['type'], 'AC4') != 'WorldArea']
        out.append((u32(grid, 2), build_object(world_rules.IDS['GridPartition'], len(grid) - 1, f"Grid - {cfg['world_rec']}", grid), None))
        out.append((u32(layers, 2), build_object(world_rules.IDS['WorldDataLayerManager'], len(layers) - 1, '(null)_WorldDataLayerManager', layers), None))
        tags = collections.Counter()
        for o in rest:
            data, tag = self.convert_object(o)
            tags[tag] += 1
            if data is not None:
                out.append((o['serial'], data, o['filter']))
        if getattr(self, 'rain_closure_roots', None):
            keep_uids_ = {u for u in self.cls if u not in self.drop}
            cube_ = self.rain_cube[0] if self.rain_cube else None
            stop_r = lambda u: self.ac3.shared_archive(u) or u in keep_uids_ or u == cube_
            r_cl = self.ac3.closure(sorted(set(self.rain_closure_roots)), stop_r, extra=donor_objs)
            closure = list(closure) + [o for o in r_cl if o['uid'] not in {c_['uid'] for c_ in closure}]
            self.log(f'   World: rain closure {len(r_cl)} objects: ' + ', '.join((f"{o['name']}[{common.cls(o['type'], 'AC3')}]" for o in r_cl))[:600])
        present = self.uids_of(out)
        added = 0
        for o in closure:
            if o['uid'] not in present:
                out.append((o['uid'], build_object(o['type'], o['body_size'], o['name'], o['body']), None))
                present.add(o['uid'])
                added += 1
        self.world_templates = []
        for t in sorted(set(self.template_map.values())):
            if t in present or self.ac3.shared_archive(t):
                continue
            self.world_templates.append(t)
            a = self.ac3.get(t)
            out.append((t, build_object(a['type'], a['body_size'], a['name'], a['body']), None))
            present.add(t)
            added += 1
        self.log(f'   World file: {len(objs)} objects -> {len(out)} ({dict(tags)}; {added} imported)')
        return out

    def cell(self, p):
        objs = p.objects
        self.current_rec = p.rec.name
        if p.rec.name in self.cfg.get('empty_blocks', ()):
            g = objs[0]
            assert common.cls(g['type'], 'AC4') == 'GridCellDataBlock'
            self.tag_totals['emptied block objects'] += len(objs) - 1
            return ([None], g)
        out = []
        gcdb = None
        for pos, o in enumerate(objs):
            if pos == 0 and common.cls(o['type'], 'AC4') == 'GridCellDataBlock':
                gcdb = o
                out.append(None)
                continue
            data, tag = self.convert_object(o)
            self.tag_totals[tag] += 1
            if data is not None:
                out.append((o['serial'], data, o['filter']))
        return (out, gcdb)

    @staticmethod
    def uids_of(out):
        present = set()
        for item in out:
            if item is None:
                continue
            b = body_of(item[1])
            if b[:2] == OBJ_HEAD:
                present.add(u32(b, 2))
        return present

    @staticmethod
    def clean_cell(body, keep_uid, added):
        n = u32(body, 10)
        end = 14 + 6 * n
        m = u32(body, end)
        inst, pieces, listed = ([], [], set())
        for i, k in enumerate(range(14, end, 6)):
            uid = u32(body, k + 2)
            if not keep_uid(uid):
                continue
            (inst if i < m else pieces).append(body[k:k + 6])
            listed.add(uid)
        for uid in added:
            if uid not in listed:
                pieces.append(PTR + struct.pack('<I', uid))
                listed.add(uid)
        keep = inst + pieces
        return body[:10] + struct.pack('<I', len(keep)) + b''.join(keep) + struct.pack('<I', len(inst)) + body[end + 4:]
    ROOT_CLOSURE_SKIP = {'Animation', 'GridCellDataBlock', 'World', 'NavMeshManager', 'WorldDataLayerManager', 'GridPartition'}

    def root_closure(self, lists, world_idx, root_idx, dead_idx):
        defined_in = collections.defaultdict(set)
        item_of = {}
        cls_of = {}
        for idx, (out, g) in lists.items():
            if idx in dead_idx:
                continue
            for item in out:
                if item is None:
                    continue
                b = body_of(item[1])
                if b[:2] != OBJ_HEAD:
                    continue
                u = u32(b, 2)
                defined_in[u].add(idx)
                if u not in item_of:
                    item_of[u] = item
                    cls_of[u] = common.cls(u32(item[1], 0), 'AC3')
        always = {world_idx, root_idx}
        needed, first = (set(), collections.Counter())
        sn1_root = set(getattr(self, 'sn1_places', {}).get(root_idx, ()))
        for idx, (out, g) in lists.items():
            if idx in dead_idx or (idx in always and (not (idx == root_idx and sn1_root))):
                continue
            for item in out:
                if item is None:
                    continue
                if idx in always and (body_of(item[1])[:2] != OBJ_HEAD or u32(body_of(item[1]), 2) not in sn1_root):
                    continue
                c = common.cls(u32(item[1], 0), 'AC3')
                if c in self.ROOT_CLOSURE_SKIP:
                    continue
                for v in refs(body_of(item[1])):
                    if v in item_of and cls_of[v] not in self.ROOT_CLOSURE_SKIP and (not defined_in[v] & (always | {idx})):
                        if v not in needed:
                            needed.add(v)
                            first[c, cls_of[v]] += 1
        queue = list(needed)
        while queue:
            v = queue.pop()
            for w in refs(body_of(item_of[v][1])):
                if w in item_of and cls_of[w] not in self.ROOT_CLOSURE_SKIP and (not defined_in[w] & always) and (w not in needed):
                    needed.add(w)
                    first['closure ' + cls_of[v], cls_of[w]] += 1
                    queue.append(w)
        root_out = lists[root_idx][0]
        by_cls, nbytes = (collections.Counter(), 0)
        for u in sorted(needed):
            _serial, data, _filt = item_of[u]
            root_out.append((u, data, None))
            by_cls[cls_of[u]] += 1
            nbytes += len(data)
        self.log(f"root closure: {len(needed)} objects ({nbytes / 1000000.0:.1f} MB) that live cells point at but neither they, the World record nor the root cell define, copied into {self.cfg['root']}: {dict(by_cls.most_common(10))}; first references {dict(first.most_common(8))}")
        return sorted(needed)

    def bake_vertex_light(self, lists):
        vc = {}
        for idx, (out, g) in lists.items():
            for item in out:
                if item is None:
                    continue
                d = item[1]
                if common.cls(u32(d, 0), 'AC3') != 'Mesh':
                    continue
                b = body_of(d)
                if b[:2] != OBJ_HEAD:
                    continue
                try:
                    vc[u32(b, 2)] = ac4mesh.layout(b)[1]
                except Exception:
                    pass
        filled = ents = unknown = 0
        for idx, (out, g) in lists.items():
            for k, item in enumerate(out):
                if item is None:
                    continue
                serial, d, filt = item
                if common.cls(u32(d, 0), 'AC3') not in ('Entity', 'EntityGroup'):
                    continue
                body = body_of(d)
                if body[:2] != OBJ_HEAD or u32(body, 2) in self.shared:
                    continue
                segs = world.segments(body)
                new, changed, prev = ([], False, None)
                for sn, sg in segs:
                    if sn == 'CompiledMeshInstance' and len(sg) >= 14 and (sg[9] == 0) and (prev is not None) and (prev[0] == 'MeshInstanceData') and (len(prev[1]) >= 9):
                        n = vc.get(u32(prev[1], 5))
                        if n:
                            sg = sg[:9] + b'\x01' + struct.pack('<I', 4 * n) + BAKE_COLOUR * n + sg[14:]
                            filled += 1
                            changed = True
                        else:
                            unknown += 1
                    new.append(sg)
                    prev = (sn, sg)
                if changed:
                    nb = b''.join(new)
                    name = d[12:12 + u32(d, 8)].decode('latin-1')
                    out[k] = (serial, build_object(u32(d, 0), u32(d, 4) + len(nb) - len(body), name, nb), filt)
                    ents += 1
        self.baked = (filled, ents, unknown)
        self.log(f'baked per-vertex lighting (CompiledMeshInstance HAS 0 -> 1, 4 bytes per vertex, white): {filled} instances on {ents} entities; {unknown} instances whose mesh the build does not define stay without one')

    def never_loaded(self, lists, world_idx):
        grid = layers = None
        for item in lists[world_idx][0]:
            if item is None:
                continue
            c = common.cls(u32(item[1], 0), 'AC3')
            if c == 'GridPartition':
                grid = body_of(item[1])
            elif c == 'WorldDataLayerManager':
                layers = body_of(item[1])
        named = set()
        for i in range(u32(layers, 10)):
            named.add(u32(layers, 14 + 18 * i + 14))
        for i in range(u32(grid, 14)):
            named.add(u32(grid, 18 + 17 * i + 9))
        dead = set()
        for idx, (out, g) in lists.items():
            if idx == world_idx:
                continue
            first = g['uid'] if g is not None else None
            if first is None:
                for item in out:
                    if item is not None:
                        b = body_of(item[1])
                        first = u32(b, 2) if b[:2] == OBJ_HEAD else None
                        break
            if first not in named:
                dead.add(idx)
        return dead

    def ac3_world_names(self):
        if getattr(self, '_world_names', None) is None:
            self._world_names = {}
            for arch in ('DataPC_AC3MP_Plantation.forge', 'DataPC_AC3MP_Boston_Mill.forge', 'DataPC_AC3MP_Boston_Harbou.forge', 'DataPC_AC3MP_Boston_Brewer.forge', 'DataPC_AC3MP_Fort_Wolcott.forge', 'DataPC_AC3MP_Iceberg.forge', 'DataPC_AC3MP_Animus.forge'):
                for _r, d in common.payloads(common.ac3_archive(arch), '^AC3MP_[A-Za-z_]+$'):
                    for ob in common.objects_in(d):
                        if common.has_uid(ob):
                            self._world_names.setdefault(ob['uid'], ob['name'].strip())
        return self._world_names

    def substitute_materials(self, o, body):

        def stem(n):
            n = re.sub('^(AC3MP|ACGA|ACGAMP|AC2MP)_(MILL|MSET|ANI|PLAN|GEN|FORT)_', '', n.strip())
            return n.lower().replace('_', '')
        ac4_mats = [v for v in refs(o['body']) if self.cls.get(v) == 'Material']
        ac3_mats = []
        for c_ in range(1, 65):
            at_ = len(body) - 14 - 6 * c_
            if at_ >= 4 and u32(body, at_ - 4) == c_ and all((body[at_ + 6 * k:at_ + 6 * k + 2] == bytes((1, 0)) for k in range(c_))):
                for k in range(c_):
                    v = u32(body, at_ + 6 * k + 2)
                    if v not in ac3_mats:
                        ac3_mats.append(v)
                break
        if not ac4_mats or not ac3_mats:
            return body
        out = bytearray(body)
        lines = []
        keep = o['name'] in self.cfg.get('substitute_keep_ac3', ())
        for m in ac3_mats:
            n3 = self.ac3.universe[m][3] if m in self.ac3.universe else self.ac3_world_names().get(m, f'{m:08X}')
            if len(ac4_mats) == 1:
                best = ac4_mats[0]
            else:
                best = max(ac4_mats, key=lambda v: difflib.SequenceMatcher(None, stem(n3), stem(self.names[v])).ratio())
            if keep:
                self.keep_ac3.setdefault(o['uid'], {})[best] = m
                lines.append(f'{n3} kept (instances: {self.names[best]} -> it)')
                continue
            key = struct.pack('<I', m)
            at = [j for j in range(len(out) - 3) if out[j:j + 4] == key]
            if len(at) < 2 or len(at) % 2 or min(at) < len(out) - 4096:
                raise SystemExit(f"substitute {o['name']}: material {n3} at {at} - not sub-mesh records + list entries in the tail")
            for j in at:
                struct.pack_into('<I', out, j, best)
            lines.append(f'{n3} -> {self.names[best]}')
        self.log(f"   substitute {o['name']}: materials " + '; '.join(lines))
        return bytes(out)

    def retarget_instances(self, body):
        if not self.keep_ac3:
            return (body, 0)
        segs = world.segments(body)
        if b''.join((s for _n, s in segs)) != body:
            return (body, 0)
        out, pos, cur, n = (bytearray(body), 0, None, 0)
        for sn, sg in segs:
            if sn in ('MeshInstanceData', 'LODSelectorInstance'):
                cur = None
                if sn == 'MeshInstanceData':
                    for v in refs(bytes(6) + sg):
                        if v in self.keep_ac3:
                            cur = self.keep_ac3[v]
                    if cur is None and len(sg) >= 9 and (sg[4] == 1) and (u32(sg, 5) in self.keep_ac3):
                        cur = self.keep_ac3[u32(sg, 5)]
            elif sn == 'MeshInstanceMaterialInfo' and cur and (len(sg) >= 20):
                for off in (10, 16):
                    if u32(sg, off) in cur:
                        struct.pack_into('<I', out, pos + off, cur[u32(sg, off)])
                        n += 1
            pos += len(sg)
        return (bytes(out), n)

    def import_ac4_shared(self, lists, root_idx):
        ac4sh = {}
        for fn in ('AC4_DataPC.forge.pkl', 'AC4_DataPC_extra.forge.pkl', 'AC4_DataPC_CoreFX.forge.pkl'):
            for u, v in pickle.load(open(os.path.join(ROOT, 'out', 'uid', fn), 'rb'))[0].items():
                ac4sh.setdefault(u, (fn[4:-4],) + tuple(v))
        defined = set()
        for out, g in lists.values():
            defined |= self.uids_of(out)
        want = set()
        for out, g in lists.values():
            for item in out:
                if item is None or common.cls(u32(item[1], 0), 'AC3') not in ('Material', 'TextureSet'):
                    continue
                for v in refs(body_of(item[1])):
                    if v >= 65536 and v not in defined and (v not in self.ac3.universe) and (v in ac4sh):
                        want.add(v)
        if not want:
            self.log('AC4 shared-archive objects the live materials need: none')
            return []
        got, queue = ({}, sorted(want))
        while queue:
            need = {u for u in queue if u not in got}
            queue = []
            for arch in sorted({ac4sh[u][0] for u in need}):
                with Forge(os.path.join(common.AC4, arch)) as f:
                    for r in f.records:
                        if not need:
                            break
                        try:
                            pl = mapwalk.Payload(r, f.read(r))
                        except Exception:
                            continue
                        for o in pl.objects:
                            if o['has_uid'] and o['uid'] in need:
                                got[o['uid']] = o
                                need.discard(o['uid'])
                                for v in refs(o['body']):
                                    if v >= 65536 and v not in defined and (v not in got) and (v in ac4sh) and (v not in self.ac3.universe) and (common.cls(ac4sh[v][2], 'AC4') in ('TextureMap', 'TextureSet')):
                                        queue.append(v)
        root_out = lists[root_idx][0]
        added = []
        for u, o in sorted(got.items()):
            self.cls.setdefault(u, common.cls(o['type'], 'AC4'))
            self.names.setdefault(u, o['name'])
            d, tag = self.convert_object(o)
            if d is None:
                raise SystemExit(f"AC4 shared object {o['name']} could not be converted ({tag})")
            root_out.append((u, d, None))
            added.append(u)
        self.log(f'AC4 shared-archive objects imported into the root cell ({len(added)}): ' + ', '.join((f"{got[u]['name'].strip()}[{common.cls(got[u]['type'], 'AC4')} @{ac4sh[u][0]}]" for u in added)))
        return added

    def clone_materials(self, lists):
        clones = self.cfg.get('material_clones', {})
        if not clones:
            return
        donors = {}
        for out, g in lists.values():
            for item in out:
                if item is None or common.cls(u32(item[1], 0), 'AC3') != 'Material':
                    continue
                nm = item[1][12:12 + u32(item[1], 8)].decode('latin-1').strip()
                if nm in clones.values() and nm not in donors:
                    donors[nm] = body_of(item[1])
        n = 0
        for out, g in lists.values():
            for k, item in enumerate(out):
                if item is None or common.cls(u32(item[1], 0), 'AC3') != 'Material':
                    continue
                nm = item[1][12:12 + u32(item[1], 8)].decode('latin-1')
                d = clones.get(nm.strip())
                if d is None:
                    continue
                if d not in donors:
                    raise SystemExit(f'material_clones: donor {d} not in the build')
                own = body_of(item[1])
                body = own[:6] + donors[d][6:]
                if nm.strip() in self.cfg.get('material_clones_flip_v', ()):
                    m_ = matprops.parse(body, 'AC3')
                    for it in m_.items:
                        if it.cls == matprops.UV_TRANSFORM and len(it.body) >= 24:
                            it.body = it.body[:20] + struct.pack('<f', -struct.unpack_from('<f', it.body, 20)[0]) + it.body[24:]
                    body = m_.to_bytes()
                out[k] = (item[0], build_object(u32(item[1], 0), len(body) - 1, nm, body), item[2])
                n += 1
        self.log(f"material clones: {n} copies replaced ({', '.join((f'{a} <- {b}' for a, b in clones.items()))})")

    def home_templates(self, lists, world_idx, root_idx):
        wanted = set(getattr(self, 'world_templates', []))
        world_out = lists[world_idx][0]
        world_refs = collections.Counter()
        for item in world_out:
            if item is not None:
                for v in refs(body_of(item[1])):
                    if v != item[0]:
                        world_refs[v] += 1
        moving = {}
        for item in world_out[1:]:
            if item is not None and (item[0] in wanted or not world_refs.get(item[0])):
                moving[item[0]] = item
        users = collections.defaultdict(collections.Counter)
        for idx, (out, g) in lists.items():
            for item in out:
                if item is None:
                    continue
                c_ = common.cls(u32(item[1], 0), 'AC3')
                for v in set(refs(body_of(item[1]))) & set(moving):
                    if v != item[0] and (c_ == 'Material' or v not in wanted):
                        users[v][idx] += 1
        homes, lines = (collections.defaultdict(list), [])
        for u, item in sorted(moving.items()):
            c = users[u]
            if c.get(world_idx):
                continue
            if not c:
                if u not in wanted:
                    continue
                host = root_idx
            elif c.get(root_idx):
                host = root_idx
            else:
                host = max(c, key=lambda i: (c[i], -i))
            world_out.remove(item)
            if u in self.uids_of(lists[host][0]):
                lines.append(f'{hex(u)} dropped from the World record (a copy in {self.payloads[host].rec.name})')
                continue
            lists[host][0].append((u, item[1], None))
            homes[host].append(u)
            lines.append(f"{item[1][12:12 + u32(item[1], 8)].decode('latin-1').strip()} -> {self.payloads[host].rec.name} ({c.get(host, 0)} local users, {sum(c.values())} in all)")
        self.log(f'World-record objects (stand-in templates, MS30 any unreferenced one) moved to a record that uses them ({len(lines)}): ' + '; '.join(lines))
        return dict(homes)
    WWISE_ID = struct.pack('<I', 2659669886)

    def remap_wwise(self, lists):
        remap = self.cfg.get('wwise_remap') or {}
        if not remap:
            return
        hits, objs = (collections.Counter(), 0)
        for idx, (out, g) in lists.items():
            for k, item in enumerate(out):
                if item is None or self.WWISE_ID not in item[1]:
                    continue
                d = bytearray(item[1])
                j, changed = (d.find(self.WWISE_ID), False)
                while j >= 0:
                    if j + 8 <= len(d):
                        v = u32(d, j + 4)
                        if v in remap:
                            struct.pack_into('<I', d, j + 4, remap[v])
                            hits[v] += 1
                            changed = True
                    j = d.find(self.WWISE_ID, j + 4)
                if changed:
                    out[k] = (item[0], bytes(d), item[2])
                    objs += 1
        missing = sorted((hex(v) for v in remap if not hits[v]))
        self.log(f'Wwise ids remapped (cfg wwise_remap): {sum(hits.values())} elements in {objs} objects, {len(hits)} of {len(remap)} ids found; not found: {missing}')

    def home_root_templates(self, lists, root_idx, added):
        root_out = lists[root_idx][0]
        cands = {item[0]: item for item in root_out if item is not None and item[0] in set(added) and (common.cls(u32(item[1], 0), 'AC3') == 'MaterialTemplate')}
        if not cands:
            return {}
        users = collections.defaultdict(collections.Counter)
        for idx, (out, g) in lists.items():
            for item in out:
                if item is None or common.cls(u32(item[1], 0), 'AC3') != 'Material':
                    continue
                b = body_of(item[1])
                if len(b) >= 16 and b[10:12] == PTR and (u32(b, 12) in cands):
                    users[u32(b, 12)][idx] += 1
        homes, lines = (collections.defaultdict(list), [])
        for u, item in cands.items():
            c = users[u]
            if c.get(root_idx) or not c:
                continue
            host = max(c, key=lambda i: (c[i], -i))
            root_out.remove(item)
            added.remove(u)
            name_ = item[1][12:12 + u32(item[1], 8)].decode('latin-1').strip()
            if u in self.uids_of(lists[host][0]):
                lines.append(f'{name_} dropped from the root cell (a copy in {self.payloads[host].rec.name})')
                continue
            lists[host][0].append(item)
            homes[host].append(u)
            lines.append(f'{name_} -> {self.payloads[host].rec.name} ({c[host]} local users, {sum(c.values())} in all)')
        if lines:
            self.log(f"root-cell templates no root material uses, moved to their users' record ({len(lines)}): " + '; '.join(lines))
        return dict(homes)

    def home_particle_textures(self, lists, world_idx):
        world_tex = {}
        for item in lists[world_idx][0]:
            if item is not None and common.cls(u32(item[1], 0), 'AC3') == 'TextureMap':
                world_tex[item[0]] = item
        homes, lines = (collections.defaultdict(list), [])
        for idx, (out, g) in lists.items():
            if idx == world_idx:
                continue
            want = set()
            for item in out:
                if item is not None and common.cls(u32(item[1], 0), 'AC3') == 'ParticleSystem':
                    want |= set(refs(body_of(item[1]))) & set(world_tex)
            have = self.uids_of(out)
            for u in sorted(want - have):
                out.append((u, world_tex[u][1], None))
                homes[idx].append(u)
                lines.append(f'{hex(u)} -> {self.payloads[idx].rec.name}')
        self.log(f'World-record particle textures copied into the cells whose particle systems use them ({len(lines)}): ' + '; '.join(lines))
        return dict(homes)

    def pigeon(self, target_conv):
        import pigeons
        if not hasattr(self, '_pigeon_tpl'):
            t = self.cfg['pigeon_template']
            a3, a4 = common.SHARED_MAPS[t['map']]

            def find(path, game):
                for rec_, d_ in common.payloads(path):
                    for o_ in common.objects_in(d_):
                        if o_['uid'] == t['uid'] and common.cls(o_['type'], game) == 'EntityGroup':
                            return o_['body']
                raise SystemExit(f"pigeon template {t['uid']:#x} not in {path}")
            self._pigeon_tpl = (find(common.ac3_archive(a3), 'AC3'), downgrade.convert('EntityGroup', find(os.path.join(common.AC4, a4), 'AC4'))[0])
            self._pigeon_fresh = iter(range(t['fresh'], t['fresh'] + 4096))
            self.pigeons_rebuilt = 0
        target_conv, n_f = pigeons.fuse_audio(target_conv)
        if n_f:
            self.notes["pigeon groups' split audio region fused (AC4 copies' layout)"] += 1
        try:
            body, back = pigeons.rebuild(self._pigeon_tpl[0], self._pigeon_tpl[1], target_conv, self._pigeon_fresh)
        except ValueError:
            sig = lambda b_: tuple(((n_, len(x_)) for n_, x_ in pigeons._segments(b_)))
            tpl = next((p_ for p_ in self.pigeon_pool() if sig(p_[1]) == sig(target_conv)), None)
            if tpl is None:
                raise SystemExit(f"pigeons: no shared-map template has this group's layout ({len(target_conv)} B)")
            body, back = pigeons.rebuild(tpl[0], tpl[1], target_conv, self._pigeon_fresh)
            self.notes['pigeon alarms rebuilt on another shared-map template (their layout)'] += 1
        self.pigeons_rebuilt += 1
        self.notes["pigeon alarms rebuilt in AC3's form (PG1)"] += 1
        return body

    def pigeon_pool(self):
        if not hasattr(self, '_pigeon_pool'):
            pool = []
            for key in ('Charlestown', 'Martinique', 'FortMathieu'):
                a3, a4 = common.SHARED_MAPS[key]

                def objs(path, game):
                    out = {}
                    for rec_, d_ in common.payloads(path):
                        for o_ in common.objects_in(d_):
                            if 'AnimalAlarm_Pigeon' in o_['name'] and common.cls(o_['type'], game) == 'EntityGroup':
                                out[o_['uid']] = o_['body']
                    return out
                A, B = (objs(common.ac3_archive(a3), 'AC3'), objs(os.path.join(common.AC4, a4), 'AC4'))
                pool += [(A[u], downgrade.convert('EntityGroup', B[u])[0]) for u in sorted(set(A) & set(B))]
            self._pigeon_pool = pool
        return self._pigeon_pool

    def unlayer_blocks(self, lists):
        import clutter
        names = tuple(self.cfg.get('unlayer_blocks', ()))
        if self.cfg.get('unlayer_next_gen'):
            if NEXT_GEN_BLOCK in self.rec_by_name and NEXT_GEN_BLOCK not in names:
                names += (NEXT_GEN_BLOCK,)
            else:
                self.log(f'SN1: unlayer_next_gen on, but no {NEXT_GEN_BLOCK} record (or already listed)')
        self.unlayered = tuple((n for n in names if n in self.rec_by_name))
        if not names:
            return ({}, {})
        anchors = []
        for idx, (out, g) in lists.items():
            if g is None or not self.payloads[idx].rec.name.startswith('Cell'):
                continue
            gb = g['body']
            m = u32(gb, 14 + 6 * u32(gb, 10))
            placed = {u32(gb, 14 + 6 * j + 2) for j in range(m)}
            for item in out:
                if item is None or common.cls(u32(item[1], 0), 'AC3') != 'Entity':
                    continue
                b = body_of(item[1])
                if b[:2] == OBJ_HEAD and u32(b, 2) in placed:
                    anchors.append((clutter.entity_pos(b)[0], idx))
        places, moved, lines = (collections.defaultdict(list), collections.defaultdict(set), [])
        kinds = collections.Counter()
        for name in names:
            src = self.rec_by_name[name]
            out, g = lists[src]
            gb_ = g['body'] if g is not None else None
            placed_ = {u32(gb_, 14 + 6 * j + 2) for j in range(u32(gb_, 14 + 6 * u32(gb_, 10)))} if gb_ is not None else None
            keep_out = []
            unplaced = collections.Counter()
            for item in out:
                c_ = common.cls(u32(item[1], 0), 'AC3') if item is not None else None
                if c_ not in ('Entity', 'EntityGroup'):
                    keep_out.append(item)
                    continue
                d = item[1]
                b = body_of(d)
                if placed_ is not None and (b[:2] != OBJ_HEAD or u32(b, 2) not in placed_):
                    unplaced[c_] += 1
                    keep_out.append(item)
                    continue
                if c_ == 'Entity':
                    nb, gone = refilter_layers(b, self.layers)
                    pos = clutter.entity_pos(nb)[0]
                else:
                    nb, gone = refilter_all(b, self.layers)
                    pos = placed_pos(nb)
                tgt = min(anchors, key=lambda a: sum(((a[0][j] - pos[j]) ** 2 for j in range(3))))[1]
                nm = d[12:12 + u32(d, 8)]
                tgt_out = lists[tgt][0]
                r_ = refs(nb)
                at_ = next((j for j in range(1, len(tgt_out)) if tgt_out[j] is not None and body_of(tgt_out[j][1])[:2] == OBJ_HEAD and (u32(body_of(tgt_out[j][1]), 2) in r_)), len(tgt_out))
                tgt_out.insert(at_, (u32(nb, 2), build_object(u32(d, 0), len(nb) - 1, nm.decode('latin-1'), nb), None))
                places[tgt].append(u32(nb, 2))
                moved[src].add(u32(nb, 2))
                kinds[c_] += 1
                left_ = filter_layers_all(nb) if c_ == 'EntityGroup' else refilter_count(nb)
                lines.append(f"{nm.decode('latin-1').strip()} -> {self.payloads[tgt].rec.name} (layers out: {', '.join((hex(x) for x in gone)) or 'none'}; filter now {len(left_)} layer(s))")
            lists[src] = (keep_out, g)
            if unplaced:
                self.log(f'SN1: {name}: {dict(unplaced)} defined on the block but not placed by its list - left there')
        self.log(f"SN1: {sum((len(v) for v in places.values()))} placed objects ({dict(kinds)}) out of {', '.join(names)} into grid cells: " + '; '.join(lines))
        return (dict(places), dict(moved))
    TRUNK_BACK_OFFSET = 30720
    TRUNK_BACKS_DEFAULT = {'trunk': '(?i)(tree|trunk)(?!.*(cann?opy|leaves|vine|liana|root))', 'branch': '(?i)branch'}

    def trunk_backs(self, lists):
        rule = self.cfg.get('trunk_backs', self.TRUNK_BACKS_DEFAULT)
        if not rule:
            return
        import numpy as np
        tre, bre = (re.compile(rule['trunk']), re.compile(rule['branch']))
        ents = [(u, self.names[u].strip()) for u, c in self.cls.items() if c in ('Entity', 'EntityGroup') and u not in self.drop]
        branches = []
        for u, n in ents:
            if bre.search(n):
                segs_ = list(world.segments(self.first(u)['body']))
                pts = fuzzy_walls.guidance_points(segs_)
                pos_ = next((fuzzy_walls._matrix(s_, 4)[3, :3] for x_, s_ in segs_ if x_ in ('Entity', 'EntityGroup') and len(s_) >= 68), None)
                if pts and pos_ is not None:
                    branches.append((n, pos_, np.mean(np.array(pts), axis=0)))
        index = {}
        for idx, (out, g) in lists.items():
            for j, item in enumerate(out):
                if item is not None and body_of(item[1])[:2] == OBJ_HEAD:
                    index.setdefault(u32(body_of(item[1]), 2), (idx, j))
        base = self.cfg.get('uid_block', 1526333440) + self.TRUNK_BACK_OFFSET
        made, lines = (0, [])
        for u, n in ents:
            if not tre.search(n) or u not in index:
                continue
            segs = list(world.segments(self.first(u)['body']))
            pl = fuzzy_walls.placements(segs)
            if len(pl) != 1:
                continue
            m, su, sm = pl[0]
            near = [b for b in branches if np.linalg.norm(b[1][:2] - m[3, :2]) < 1.0]
            if not near or su not in index:
                continue
            dirs = []
            for bn_, _bp, bc_ in near:
                d_ = bc_[:2] - m[3, :2]
                if np.linalg.norm(d_) >= 0.05:
                    dirs.append((bn_, d_ / np.linalg.norm(d_)))
            if not dirs:
                continue
            sidx, sj = index[su]
            sitem = lists[sidx][0][sj]
            sbody = body_of(sitem[1])
            V, I, _ma, M, T, _q = fuzzy_walls.parse_shape(sbody)
            Mw = sm @ m
            W = V[I] @ Mw[:3, :3] + Mw[3, :3]
            N = np.cross(W[:, 1] - W[:, 0], W[:, 2] - W[:, 0])
            L = np.linalg.norm(N, axis=1)
            back = [t for t in range(len(W)) if L[t] > 1e-09 and abs(N[t, 2]) < 0.6 * L[t] and all((N[t, 0] * d_[0] + N[t, 1] * d_[1] < 0 for _b, d_ in dirs))]
            if not back:
                continue
            nu = base + made
            while nu in self.cls or nu in index:
                nu += 1
            nb = fuzzy_walls.apply(sbody, back)
            nb = nb[:2] + struct.pack('<I', nu) + nb[6:]
            nm = sitem[1][12:12 + u32(sitem[1], 8)].decode('latin-1')
            eidx, ej = index[u]
            lists[eidx][0].append((nu, build_object(u32(sitem[1], 0), len(nb) - 1, nm, nb), None))
            self.rain_homes[eidx].append(nu)
            eitem = lists[eidx][0][ej]
            ref_old, ref_new = (b'\x01\x00' + struct.pack('<I', su), b'\x01\x00' + struct.pack('<I', nu))
            if eitem[1].count(ref_old) != 1:
                raise SystemExit(f'TC6: {n} names its trunk shape {eitem[1].count(ref_old)} times')
            lists[eidx][0][ej] = (eitem[0], eitem[1].replace(ref_old, ref_new), eitem[2])
            index[nu] = (eidx, len(lists[eidx][0]) - 1)
            made += 1
            lines.append(f'{n} (' + ', '.join((f'{b_} at {np.degrees(np.arctan2(d_[1], d_[0])):.0f} deg' for b_, d_ in dirs)) + f'): {len(back)} faces -> {nu:#010x}')
        self.trunk_back_uids = set(range(base, base + made + 64)) & set(index)
        self.log(f'TC6: {made} trunk shape copies, the far side FuzzyZone: ' + '; '.join(lines))

    def inject_action_blocks(self, lists):
        recs = self.cfg.get('datapc_action_blocks', ())
        if not recs:
            return {}
        import anim4
        blocks = []
        for rec, d in common.payloads(os.path.join(common.AC4, 'DataPC.forge'), set(recs)):
            ab, clips = (None, [])
            for o in common.objects_in(d):
                if common.cls(o['type'], 'AC3') == 'ActionBlock':
                    ab = o
                elif o['type'] == ANIM:
                    body = anim4.to_ac3(o['body'], keep_sound=self.cfg.get('crowd_clip_sound', True))
                    clips.append((clip_id(body), build_object(ANIM, len(body) - 8 - 12 * u32(body, 4), o['name'], body)))
            if ab is None:
                raise SystemExit(f"datapc_action_blocks: no ActionBlock in AC4's {rec.name}")
            actions = {u32(seg, 4) for n_, seg in world.segments(ab['body']) if n_ == 'Action'}
            blocks.append((rec.name, ab, clips, actions))
        homes, lines = (collections.defaultdict(list), [])
        for idx, (out, g) in lists.items():
            names_here = set()
            for item in out:
                if item is not None:
                    names_here |= set(refs(body_of(item[1])))
            have = self.uids_of(out)
            for rname, ab, clips, actions in blocks:
                if not names_here & actions or ab['uid'] in have:
                    continue
                out.append((ab['uid'], build_object(ab['type'], len(ab['body']) - 1, ab['name'], ab['body']), None))
                out.extend(((cid, data, None) for cid, data in clips))
                homes[idx].append(ab['uid'])
                lines.append(f'{rname} ({len(clips)} clips) -> {self.payloads[idx].rec.name}')
        self.log(f'AC4 crowd ActionBlocks + clips copied into the cells using them ({len(lines)}): ' + '; '.join(lines))
        return dict(homes)

    def relocate_never_loaded(self, lists, dead_idx, root_idx):
        dead_objs = {}
        for idx in dead_idx:
            for item in lists[idx][0]:
                if item is None:
                    continue
                b = body_of(item[1])
                if b[:2] == OBJ_HEAD:
                    dead_objs.setdefault(u32(b, 2), item)
        live_defined = set()
        for idx, (out, g) in lists.items():
            if idx not in dead_idx:
                live_defined |= self.uids_of(out)
                if g is not None:
                    live_defined.add(g['uid'])
        only_dead = {u for u in dead_objs if u not in live_defined}
        needed, queue, users = (set(), [], collections.Counter())
        for idx, (out, g) in lists.items():
            if idx in dead_idx:
                continue
            for item in out:
                if item is None:
                    continue
                for v in refs(body_of(item[1])):
                    if v in only_dead:
                        users[common.cls(u32(item[1], 0), 'AC3')] += 1
                        if v not in needed:
                            needed.add(v)
                            queue.append(v)
        while queue:
            u = queue.pop()
            for v in refs(body_of(dead_objs[u][1])):
                if v in only_dead and v not in needed:
                    needed.add(v)
                    queue.append(v)
        root_out = lists[root_idx][0]
        by_cls = collections.Counter()
        for u in sorted(needed):
            _serial, data, _filt = dead_objs[u]
            root_out.append((u, data, None))
            by_cls[common.cls(u32(data, 0), 'AC3')] += 1
        names = sorted((self.payloads[idx].rec.name for idx in dead_idx))
        self.log(f"never-loaded records {names}: {len(only_dead)} objects defined only there, {len(needed)} reached from live records ({sum(users.values())} references, from {dict(users)}) copied into {self.cfg['root']}: {dict(by_cls)}")
        return sorted(needed)

    def run(self):
        self.load()
        self.decide()
        downgrade.NO_CELL_INDEX = True
        self.cells_cleared = 0
        if self.dry:
            self.log('dry run: nothing written')
            return None
        t = time.time()
        for u, c_ in self.cls.items():
            if c_ == 'Mesh' and self.names[u] in self.cfg.get('substitute_keep_ac3', ()) and (u not in self.drop):
                o_ = self.first(u)
                a_ = self.ac3.get(self.ac3.by_name.get(('Mesh', self.cfg['mesh_substitutes'][self.names[u]])))
                self.substitute_materials(dict(o_, uid=u, name=self.names[u]), a_['body'])
        world_idx = self.rec_by_name[self.cfg['world_rec']]
        root_idx = self.rec_by_name[self.cfg['root']]
        lists = {}
        for idx, p in self.payloads.items():
            if idx == world_idx:
                lists[idx] = (self.world_file(p), None)
            else:
                lists[idx] = self.cell(p)
        self.rain_homes = collections.defaultdict(list)
        for rec_, objs_ in getattr(self, 'rain_wpds', {}).items():
            idx_ = self.rec_by_name[rec_]
            for u_, d_ in objs_:
                lists[idx_][0].append((u_, d_, None))
                self.rain_homes[idx_].append(u_)
        if getattr(self, 'rain_cube', None):
            lists[root_idx][0].append((self.rain_cube[0], self.rain_cube[1], None))
        if getattr(self, 'clutter_objs', None):
            have_ = self.uids_of(lists[root_idx][0])
            for u_, d_ in self.clutter_objs:
                lists[root_idx][0].append((u_, d_, None))
                self.rain_homes[root_idx].append(u_)
            for u_ in self.clutter_copies:
                if u_ in have_:
                    continue
                item_ = next((it for out_, g_ in lists.values() for it in out_ if it is not None and body_of(it[1])[:2] == OBJ_HEAD and (u32(body_of(it[1]), 2) == u_)))
                lists[root_idx][0].append((u_, item_[1], None))
                self.rain_homes[root_idx].append(u_)
            self.log(f"clutter piece: {len(self.clutter_objs)} objects + {len(self.clutter_copies)} AC4 texture objects in {self.cfg['root']}")
        self.sn1_places, self.sn1_moved = self.unlayer_blocks(lists)
        self.trunk_backs(lists)
        defined = set()
        for out, g in lists.values():
            defined |= self.uids_of(out)
            if g is not None:
                defined.add(g['uid'])
        dead_idx = self.never_loaded(lists, world_idx)
        relocated = self.relocate_never_loaded(lists, dead_idx, root_idx)
        relocated += self.root_closure(lists, world_idx, root_idx, dead_idx)
        relocated += self.import_ac4_shared(lists, root_idx)
        self.bake_vertex_light(lists)
        self.remap_wwise(lists)
        shared_uids = {u for u, h in self.ac3.universe.items() if h[0] in SHARED_ARCHIVES}
        wanted = set(self.imports_needed)
        for idx, (out, g) in lists.items():
            for item in out:
                if item is None:
                    continue
                d = item[1]
                if u32(d, 0) == ANIM or common.cls(u32(d, 0), 'AC3') in BULK:
                    continue
                for v in refs(body_of(d)):
                    if v not in defined and v not in shared_uids and (v in self.ac3.universe):
                        wanted.add(v)
        contract = stock_common() if self.cfg.get('stock_contract') else {}
        place = {}
        for u, (c_, n_, rec_) in contract.items():
            if u not in defined and u in self.ac3.universe:
                wanted.add(u)
                place[u] = rec_
        stop = lambda u: self.ac3.shared_archive(u) and u not in place or u in defined
        imported = self.ac3.closure(sorted(wanted), stop)
        root_out, _root_g = lists[root_idx]
        added, by_rec = ([], collections.Counter())
        for a in imported:
            if a['uid'] in defined:
                continue
            home = place.get(a['uid'])
            idx = self.rec_by_name.get(home) if home and (not home.startswith('Cell')) else None
            target = lists[idx][0] if idx is not None else root_out
            abody = a['body']
            if common.cls(a['type'], 'AC3') in ('Entity', 'EntityGroup'):
                abody, n_ = downgrade.no_cell_index(abody)
                self.cells_cleared += n_
            target.append((a['uid'], build_object(a['type'], a['body_size'], a['name'], abody), None))
            defined.add(a['uid'])
            added.append(a['uid'])
            by_rec[home if idx is not None else self.cfg['root']] += 1
        root_has = self.uids_of(root_out)
        root_has |= self.uids_of(lists[world_idx][0])
        for a in imported:
            if a['uid'] not in added or common.cls(a['type'], 'AC3') != 'Material' or len(a['body']) < 16 or (a['body'][10:12] != PTR):
                continue
            if place.get(a['uid'], 'Cell').startswith('Cell') is False:
                continue
            t_ = u32(a['body'], 12)
            if t_ in root_has or self.ac3.shared_archive(t_):
                continue
            ta = self.ac3.get(t_)
            if ta is None:
                continue
            root_out.append((t_, build_object(ta['type'], ta['body_size'], ta['name'], ta['body']), None))
            root_has.add(t_)
            added.append(t_)
            self.log(f"   root cell: template {ta['name'].strip()} copied in for the imported material {a['name'].strip()}")
        self.log(f"root cell: {len(added)} objects imported from AC3's maps: " + ', '.join((f"{a['name']}[{common.cls(a['type'], 'AC3')} @{self.ac3.universe[a['uid']][0]}]" for a in imported if a['uid'] in added))[:1500])
        self.log(f"cell indices -> 0xffff on every entity-ending trailer (no stand-in tiles: downgrade.NO_CELL_INDEX; components() clears the converted ones, the post-step {self.cells_cleared} more - AC3's own bytes and imports; the verify counts them)")
        if getattr(self, 'completed', None):
            self.log(f'materials completed (the properties every AC3 material on the template carries): {sum(self.completed.values())} materials, {sum(self.completed_items.values())} items: {dict(self.completed_items)}')
            self.log(f'material properties stripped (no AC3 material on the template carries the hash): {sum(self.stripped.values())} items: ' + ', '.join((f"{t.replace('AC3MP_', '')} {h} x{k}" for (t, h), k in self.stripped_hashes.most_common(40))))
            self.log(f"Tex2 blend parameters brought into AC3's range (TEX2_BLEND_RANGE / TEX2_ALPHA_ONE): " + ', '.join((f'{h} x{k}' for (t, h), k in self.clamped.most_common())))
            nt_ = getattr(self, 'neutralised', None) or collections.Counter()
            self.log("completed texture slots on a neutral texture (E1, audit 2026-10-09; not another map's): " + (', '.join((f"{t.replace('AC3MP_', '')} {h} {w} x{k}" for (t, h, w), k in nt_.most_common())) or 'none'))
        if contract:
            got = [u for u in place if u in defined]
            self.log(f'stock contract: {len(contract)} objects every stock map carries; the build lacked {len(place)}, imported {len(got)} ({dict(collections.Counter((contract[u][0] for u in got)).most_common(14))}) into {dict(by_rec)}')
        n_gone = 0
        serial_ids = {item[0] for out_, _g in lists.values() for item in out_ if item is not None and body_of(item[1])[:2] != OBJ_HEAD and (common.cls(u32(item[1], 0), 'AC3') in ('NavMeshManager', 'Animation', 'ProxyTextures'))}
        keep_uid = lambda u: u in defined or u in shared_uids or u in serial_ids
        restored = collections.Counter()
        for out_, g_ in lists.values():
            if g_ is not None:
                for j in range(u32(g_['body'], 10)):
                    v = u32(g_['body'], 14 + 6 * j + 2)
                    if v in serial_ids and v not in defined and (v not in shared_uids):
                        restored[next((common.cls(u32(it[1], 0), 'AC3') for it in out_ if it is not None and it[0] == v), '?')] += 1
        self.log(f'cell-list entries kept by directory id (objects without a uid header): {dict(restored)}')
        self.clone_materials(lists)
        homes = self.home_templates(lists, world_idx, root_idx)
        for idx_, us_ in self.home_root_templates(lists, root_idx, added).items():
            homes.setdefault(idx_, []).extend(us_)
        for idx_, us_ in self.home_particle_textures(lists, world_idx).items():
            homes.setdefault(idx_, []).extend(us_)
        for idx_, us_ in self.inject_action_blocks(lists).items():
            homes.setdefault(idx_, []).extend(us_)
        for idx_, us_ in getattr(self, 'rain_homes', {}).items():
            homes.setdefault(idx_, []).extend(us_)
        self.root_added = set(added) | set(relocated) | set(homes.get(root_idx, []))
        for idx, (out, g) in lists.items():
            p = self.payloads[idx]
            if g is not None:
                before = u32(g['body'], 10)
                extra = (added + relocated if idx == root_idx else []) + homes.get(idx, [])
                body = self.clean_cell(g['body'], keep_uid, extra)
                if idx == root_idx and getattr(self, 'rain_cube', None):
                    n_ = u32(body, 10)
                    m_ = u32(body, 14 + 6 * n_)
                    ent_ = bytes((1, 0)) + struct.pack('<I', self.rain_cube[0])
                    body = body[:10] + struct.pack('<I', n_ + 1) + body[14:14 + 6 * m_] + ent_ + body[14 + 6 * m_:14 + 6 * n_] + struct.pack('<I', m_ + 1) + body[18 + 6 * n_:]
                n_gone += before - (u32(body, 10) - len(extra))
                if idx in self.sn1_places or idx in self.sn1_moved:
                    body = list_edit(body, self.sn1_places.get(idx, ()), self.sn1_moved.get(idx, ()))
                out[0] = (g['serial'], mapwalk.object_bytes(g, body), g['filter'])
            out = [x for x in out if x is not None]
            d = mapwalk.rebuild(out, p.filter_table)
            set_payload(self.image, idx, compress(d, first_chunk=mapwalk.first_chunk(d)))
        self.log(f'keep-AC3 substitutes: {len(self.keep_ac3)} meshes, {self.retargeted_entries} instance material entries retargeted')
        self.log(f'converted: {dict(self.tag_totals)}; {n_gone} list entries removed; {self.retargeted} template references retargeted ({time.time() - t:.0f} s)')
        if self.notes:
            self.log('rule notes: ' + ', '.join((f'{k} x{v}' for k, v in self.notes.most_common(30))))
        out_dir = os.path.join(OUT, f"{self.name}_{self.cfg['tag']}")
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, self.cfg['archive'])
        size = write(dest, self.image)
        self.log(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
        if self.cfg.get('sound_package'):
            import shutil
            shutil.copyfile(os.path.join(ROOT, self.cfg['sound_package']), os.path.join(out_dir, 'sounds_zz_acr_mp.pck'))
            self.log(f"sound package {self.cfg['sound_package']} copied beside it")
        self.verify(dest)
        if getattr(self, 'clutter_plan', None):
            self.verify_clutter(dest)
        with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(self.report) + '\n')
        return dest

    def verify_clutter(self, dest):
        import clutter
        bodies, defined, listed = ({}, set(), set())
        for rec, d in common.payloads(dest):
            for o in common.objects_in(d):
                if o.get('uid') is not None:
                    defined.add(o['uid'])
                if o.get('uid') in self.clutter_map:
                    bodies[o['uid']] = o['body']
                if common.cls(o['type'], 'AC3') == 'GridCellDataBlock':
                    b = o['body']
                    m = u32(b, 14 + 6 * u32(b, 10))
                    listed |= {u32(b, 14 + 6 * j + 2) for j in range(m)}
        ok, lines = clutter.gate_g2(bodies, self.clutter_map, self.clutter_src, tol=(self.cfg.get('clutter') or {}).get('g2_tol', 0.002))
        for ln in lines:
            self.log('verify: ' + ln)
        meshes = {u32(c[0]['settings'], 28) for c in self.clutter_plan.values()}
        missing = [hex(m) for m in meshes if m not in defined]
        for u_, _d in getattr(self, 'clutter_objs', [])[:1]:
            body_ = next((o['body'] for rec, d in common.payloads(dest, {self.cfg['root']}) for o in common.objects_in(d) if o.get('uid') == u_))
            m_ = clutter.parse_mesh(body_)
            ok_m = clutter.encode_mesh(m_) == body_ and m_['sub'][0]['mat'] in defined and (set(meshes) == {u_})
            self.log(f"verify: clutter piece {u_:#x}: {len(m_['vertices']) // 20} vertices, {len(m_['indices']) // 3} triangles, compiled = its source, material defined, every host names it: {ok_m}")
            ok = ok and ok_m
        unplaced = [self.names[u] for u in self.clutter_map if u not in listed]
        self.log(f"verify: clutter source mesh {', '.join((hex(m) for m in meshes))} defined in the build: {not missing}; hosts not placed as instances: {unplaced}")
        if not ok or missing or unplaced:
            raise SystemExit('clutter (CL1t) verify failed')

    def verify(self, dest):
        t = time.time()
        uids = set()
        serials, nav_of_rec = (set(), {})
        bodies = []
        size_bad = collections.Counter()
        shapes = collections.defaultdict(collections.Counter)
        rec_first = {}
        with Forge(dest) as f:
            for r in f.records:
                if r.name == 'GlobalMetaFile' or not r.size:
                    continue
                p = mapwalk.Payload(r, f.read(r))
                if p.objects and p.objects[0]['has_uid']:
                    rec_first[p.objects[0]['uid']] = r.name
                for o in p.objects:
                    c = common.cls(o['type'], 'AC3')
                    if o['has_uid']:
                        uids.add(o['uid'])
                        if len(o['body']) - o['body_size'] != 1 and c != 'NavMeshManager':
                            size_bad[c] += 1
                    else:
                        serials.add(o['serial'])
                    if c == 'NavMeshManager':
                        nav_of_rec[r.name] = o['serial']
                    if c in ('Animation', 'NavMeshManager') and len(o['body']) - o['body_size'] != 8 + 12 * u32(o['body'], 4):
                        size_bad[c + ' (type-table size rule)'] += 1
                    bodies.append((r.name, c, o['name'], o.get('uid'), o['body']))
        W = next((b for rec, c, n, u, b in bodies if c == 'World'))
        layers = next((b for rec, c, n, u, b in bodies if c == 'WorldDataLayerManager'))
        grid = next((b for rec, c, n, u, b in bodies if c == 'GridPartition'))
        live = {self.cfg['world_rec']}
        for i in range(u32(layers, 10)):
            live.add(rec_first.get(u32(layers, 14 + 18 * i + 14), '?'))
        for i in range(u32(grid, 14)):
            live.add(rec_first.get(u32(grid, 18 + 17 * i + 9), '?'))
        live.discard('?')
        dead = {rec for rec, c, n, u, b in bodies} - live
        list_dangling = 0
        nav_listed = 0
        for rec, c, n, u, b in bodies:
            if c == 'GridCellDataBlock':
                m_ = u32(b, 14 + 6 * u32(b, 10))
                for j in range(u32(b, 10)):
                    v = u32(b, 14 + 6 * j + 2)
                    if v not in uids and v not in serials:
                        list_dangling += 1
                    if rec in nav_of_rec and v == nav_of_rec[rec] and (j < m_):
                        nav_listed += 1
        if any(('type-table' in k for k in size_bad)):
            raise SystemExit(f"Animation / NavMeshManager header size off the len - 8 - 12 n rule: {dict(size_bad)} (MS58's load crash)")
        skin_mat_missing = collections.Counter()
        shared_uids = {u for u, h in self.ac3.universe.items() if h[0] in SHARED_ARCHIVES}
        for rec, c, n, u, b in bodies:
            if c == 'Mesh' and u in self.skinned and (rec not in dead):
                for v in refs(b):
                    if self.cls.get(v) == 'Material' and v not in uids and (v not in shared_uids):
                        skin_mat_missing[self.names.get(v, hex(v))] += 1
        self.log(f'verify: skinned meshes naming a material the build lacks: {sum(skin_mat_missing.values())}')
        if skin_mat_missing:
            raise SystemExit(f'skinned meshes name missing materials (invisible characters): {dict(skin_mat_missing.most_common(8))}')
        live_nav = {rec for rec in nav_of_rec if rec in live}
        self.log(f'verify: navmesh managers listed as instances of their own cell: {nav_listed} of {len(live_nav)} live')
        if nav_listed != len(live_nav):
            raise SystemExit("a live cell's NavMeshManager is not an instance of its cell list: the garbage sweep frees it, no crowd (MS58)")
        real = set(self.by_uid) | set(self.ac3.universe)
        shared_uids = {u for u, h in self.ac3.universe.items() if h[0] in SHARED_ARCHIVES}
        dangling = collections.Counter()
        examples = {}
        for rec, c, n, u, b in bodies:
            if c in BULK or c == 'Animation' or rec in dead:
                continue
            for v in refs(b):
                if v in real and v not in uids and (v not in shared_uids):
                    dangling[c, n] += 1
                    examples.setdefault((c, n), (rec, f'{v:08X}', self.names.get(v) or self.ac3.universe.get(v, ('', '', '', '?'))[3], self.drop.get(v, "not dropped: another map's" if v in self.ac3.universe else '?')))
        dead_only = {u for rec, c, n, u, b in bodies if u is not None and rec in dead}
        dead_only -= {u for rec, c, n, u, b in bodies if u is not None and rec not in dead}
        dead_name = {u: (c, n) for rec, c, n, u, b in bodies if u in dead_only}
        dead_refs = collections.Counter()
        dead_ex = {}
        for rec, c, n, u, b in bodies:
            if rec in dead or c == 'Animation':
                continue
            for v in refs(b):
                if v in dead_only:
                    k = (c, dead_name[v][0])
                    dead_refs[k] += 1
                    dead_ex.setdefault(k, f'{n[:30]} @{rec} -> {dead_name[v][1][:36]}')
        import map_audit
        blocks_ = getattr(self, 'unlayered', ())
        placed_left, into = map_audit.inactive_block_check(bodies, blocks_)
        self.log(f'verify: unlayered blocks {list(blocks_)}: placed objects left {len(placed_left)} {placed_left[:6]}; references from other records into objects only they define {sum(into.values())} {dict(into.most_common(6))}')
        if placed_left or into:
            raise SystemExit('an unlayered block (AC4-only layer, never loaded in AC3) still places objects or is pointed into (E4b)')
        rec_of = collections.defaultdict(set)
        cls_of_uid = {}
        for rec, c, n, u, b in bodies:
            if u is not None:
                rec_of[u].add(rec)
                cls_of_uid.setdefault(u, c)
        cross = collections.Counter()
        for rec, c, n, u, b in bodies:
            if rec in dead or rec in (self.cfg['world_rec'], self.cfg['root']) or c in self.ROOT_CLOSURE_SKIP:
                continue
            for v in refs(b):
                if v in rec_of and (not rec_of[v] & {rec, self.cfg['world_rec'], self.cfg['root']}) and (cls_of_uid.get(v) not in self.ROOT_CLOSURE_SKIP):
                    cross[c, next(iter(rec_of[v]))] += 1
        cmi = collections.Counter()
        for rec, c, n, u, b in bodies:
            if c in ('Entity', 'EntityGroup') and rec not in dead:
                for sn, sg in world.segments(b):
                    if sn == 'CompiledMeshInstance' and len(sg) >= 10:
                        cmi[sg[9]] += 1
        tmpl = {u for rec, c, n, u, b in bodies if c == 'MaterialTemplate'}
        mat_bad = collections.Counter()
        for rec, c, n, u, b in bodies:
            if c == 'Material' and len(b) > 16 and (rec not in dead):
                tt = u32(b, 12)
                if tt not in tmpl and tt not in shared_uids:
                    mat_bad[f'{tt:08X}', self.names.get(tt, self.ac3.universe.get(tt, ('', '', '', '?'))[3])] += 1
        count_bad = []
        ac4_trailer = collections.Counter()
        for rec, c, n, u, b in bodies:
            if c not in ('Entity', 'EntityGroup') or rec in dead:
                continue
            sg_ = world.segments(b)
            for i_ in range(len(sg_) - 1):
                if sg_[i_ + 1][0] != 'BoundingVolume':
                    continue
                t_ = sg_[i_][1]
                ac3_form = len(t_) >= 39 and t_[-8:-4] == ONE_F_ and all((x <= 1 for x in t_[-39:-19])) and all((x <= 1 for x in t_[-15:-10]))
                ac4_form = len(t_) >= 41 and t_[-8:-4] == ONE_F_ and all((x <= 1 for x in t_[-41:-21])) and (t_[-21] <= 1) and all((x <= 1 for x in t_[-16:-11])) and (t_[-11] == 1)
                if ac4_form and (not ac3_form):
                    ac4_trailer[sg_[i_][0]] += 1
        self.log(f"verify: entity trailers still in AC4's form: {dict(ac4_trailer)}")
        if ac4_trailer:
            raise SystemExit(f"entities ending in AC4's trailer form (a load crash): {dict(ac4_trailer)}")
        merged_missing = []
        for rec, c, n, u, b in bodies:
            if c != 'Entity' or n.strip() or b'W\xee\xbe\x8b' not in b or (rec in dead):
                continue
            for nm_, s_ in world.segments(b):
                if nm_ == 'MultiMeshShape' and len(s_) >= 8:
                    cnt_ = struct.unpack_from('<I', s_, 4)[0]
                    if 8 + 5 * cnt_ <= len(s_):
                        merged_missing += [(rec, hex(struct.unpack_from('<I', s_, 9 + 5 * i_)[0])) for i_ in range(cnt_) if struct.unpack_from('<I', s_, 9 + 5 * i_)[0] not in uids]
        self.log(f'verify: merged-collision members the build does not define: {len(merged_missing)} {merged_missing[:6]}')
        if merged_missing:
            raise SystemExit('a merged collision lists entities the build dropped: a crash on load (EX5b; keep them, as stubs)')
        sub_point = [(rec, n.strip()) for rec, c, n, u, b in bodies if rec not in dead and downgrade.SUB_OBJECT_POINT in b and (c not in ('Mesh', 'TextureMap', 'MeshShape'))]
        self.log(f'verify: AC4 SubObjectPoints left (AC3 cannot create the class): {len(sub_point)} {sub_point[:6]}')
        if sub_point:
            raise SystemExit("AC4 SubObjectPoints left in live objects: a crash in the object reader (EX5's vines)")
        decal_left = [(rec, n.strip()) for rec, c, n, u, b in bodies if rec not in dead and downgrade.STATIC_DECAL in b and (c not in ('Mesh', 'TextureMap', 'MeshShape'))]
        self.log(f'verify: AC4 StaticDecalComponents left: {len(decal_left)} {decal_left[:6]}')
        if decal_left:
            raise SystemExit('AC4 StaticDecalComponents left in live objects: a crash in the object reader (SB5)')
        seen_, shape_before, rec_ = (set(), [], None)
        for rec, c, n, u, b in bodies:
            if rec != rec_:
                seen_, rec_ = (set(), rec)
            if c in ('Entity', 'EntityGroup') and rec not in dead:
                for nm_, s_ in world.segments(b):
                    if nm_ == 'CollisionFilterInfo' and len(s_) >= 18 and (s_[12:14] == b'\x01\x00') and (struct.unpack_from('<I', s_, 14)[0] in seen_):
                        shape_before.append((rec, n.strip()))
            if u is not None:
                seen_.add(u)
        self.log(f'verify: entities after their own collision shape in a record: {len(shape_before)} {shape_before[:6]}')
        if shape_before:
            raise SystemExit('an entity comes after its collision shape in the same record: never built, a crash on load (SO1)')
        fz_plan, fz_done = (len(getattr(self, 'fuzzy_plan', {})), getattr(self, 'fuzzy_done', 0))
        self.log(f'verify: FZ1 shapes planned {fz_plan}, rewritten {fz_done}')
        if fz_done < fz_plan:
            raise SystemExit(f'FZ1: {fz_plan - fz_done} planned MeshShape(s) not rewritten')
        g_types, g_bad = (collections.Counter(), [])
        for rec, c, n, u, b in bodies:
            if rec not in dead and c in ('Entity', 'EntityGroup'):
                t_ = downgrade.guidance_types(b)
                g_types.update(t_)
                if any((k > downgrade.AC3_GUIDANCE_TYPES_MAX for k in t_)):
                    g_bad.append((rec, n.strip(), sorted((k for k in t_ if k > downgrade.AC3_GUIDANCE_TYPES_MAX))))
        self.log(f'verify: climb guidance types {dict(sorted(g_types.items()))}; AC4-only types left: {len(g_bad)} {g_bad[:6]}')
        if g_bad:
            raise SystemExit('climb guidance of an AC4-only type left (downgrade.GUIDANCE_TYPE_AC4_TO_AC3): AC3 ignores it - a railing / wall top it cannot climb onto (RG1)')
        ds = dict(downgrade.DECAL_STATS)
        self.log(f"verify: decal heads converted / stripped (downgrade.DECAL_STATS): {ds or 'none'}")
        if downgrade.KEEP_DECALS and ds.get('mesh stripped'):
            raise SystemExit(f"keep_decals: {ds['mesh stripped']} decal(s) with a mesh stripped instead of kept as Visuals - a head form downgrade._decals_as_visuals does not know (E2)")
        hash_bad = []
        for rec, c, n, u, b in bodies:
            if c == 'Material' and rec not in dead:
                try:
                    m_ = matprops.parse(b, 'AC3')
                    hash_bad += [f'{n} @{rec}: {it.hash:#010x}' for it in m_.items if it.hash >> 16 == self.cfg.get('uid_block', 1526333440) >> 16 or it.hash in uids]
                except matprops.ParseError as e:
                    count_bad.append(f'{n} @{rec}: {e}')
        self.log(f"verify: material property hashes that are object ids / this thread's uid block: {len(hash_bad)} {hash_bad[:6]}")
        if hash_bad:
            raise SystemExit("material property hashes renamed as ids (MS76-MS79's spawn crash)")
        kept = collections.defaultdict(list)
        for c, n, rec, why in self.noted:
            if rec in live:
                kept[c, why].append(n)
        hdr_bad = []
        for rec, c, n, u, b in bodies:
            if c in ('Entity', 'EntityGroup') and rec not in dead:
                for sn, sg in world.segments(b):
                    if sn in ('Entity', 'EntityGroup') and len(sg) >= 79:
                        cnt = u32(sg, 69)
                        ok = cnt >= 1 and len(sg) == 79 and (sg[-6:-4] in (bytes.fromhex('0400'), bytes.fromhex('0001'))) or (cnt == 0 and len(sg) == 112)
                        if not ok:
                            hdr_bad.append(f'{c} {n} @{rec}: {sn} header {len(sg)} B says {cnt} components')
        trailer_bad = []
        AC3_MIMI_END = {(0, 69), (0, 77), (2, 71), (3, 72), (3, 80), (4, 73)}
        AC3_MIMI_END.add((1, 70))
        for rec, c, n, u, b in bodies:
            if c in ('Entity', 'EntityGroup') and rec not in dead:
                sg = world.segments(b)
                for i in range(len(sg) - 1):
                    if sg[i + 1][0] == 'BoundingVolume' and sg[i][0] == 'MeshInstanceMaterialInfo':
                        s_ = sg[i][1]
                        k = 0
                        while 20 + k < len(s_) and s_[20 + k] == 3:
                            k += 1
                        if (k, len(s_)) not in AC3_MIMI_END:
                            trailer_bad.append(f'{c} {n} @{rec}: MeshInstanceMaterialInfo {len(s_)} B with {k} 03s ends the entity')
        flag_bad, flag_new = ([], collections.Counter())
        for rec, c, n, u, b in bodies:
            if c in ('Entity', 'EntityGroup') and rec not in dead:
                sg = world.segments(b)
                for i in range(len(sg) - 1):
                    if sg[i + 1][0] == 'BoundingVolume':
                        s_, cls_ = (sg[i][1], sg[i][0])
                        if len(s_) >= 39 and s_[-8:-4] == struct.pack('<f', 1.0) and all((x <= 1 for x in s_[-39:-19])):
                            if cls_ not in downgrade.AC3_FLAG15_SET and cls_ not in downgrade.AC3_FLAG15_CLEAR:
                                flag_new[cls_, s_[-24]] += 1
                            elif bool(s_[-24]) != (cls_ in downgrade.AC3_FLAG15_SET):
                                flag_bad.append(f"{c} {n} @{rec}: {cls_} ends the entity with flag 15 {('set' if s_[-24] else 'clear')} (AC3: {('set' if cls_ in downgrade.AC3_FLAG15_SET else 'clear')})")
        segs = world.segments(W)
        names = [n for n, _ in segs]
        fh = names.index('FloorHeightData')
        count = u32(segs[fh][1], len(segs[fh][1]) - world.COUNT_BEFORE_NAV)
        top = sum((1 for n in names[fh + 1:] if n in world_rules.TOP_LEVEL))
        ref = pickle.load(open(os.path.join(common.OUT, '_ac3_shapes.pkl'), 'rb'))
        where = collections.defaultdict(collections.Counter)
        for rec, c, n, u, b in bodies:
            if c in BULK or c == 'Animation' or rec in dead or (c == 'PropertyControllerData'):
                continue
            for n_, s in world.segments(b):
                shapes[n_][len(s)] += 1
                if len(s) not in ref.get(n_, {}):
                    where[n_][f'{c} {n}'] += 1
        unseen = {}
        for n_, cnt in shapes.items():
            if n_ == '(header)':
                continue
            new = {ln: k for ln, k in cnt.items() if ln not in ref.get(n_, {})}
            if new:
                unseen[n_] = (sum(new.values()), sum(cnt.values()), sorted(new)[:6], where[n_].most_common(2))
        if 'GameplayCoordinatorOutput' in unseen:
            raise SystemExit(f"GameplayCoordinatorOutput in a form AC3's maps never show: {unseen['GameplayCoordinatorOutput']}")
        fixed_bad = {n_: v for n_, v in unseen.items() if len(ref.get(n_, {})) == 1 and sum(ref[n_].values()) >= 20 and (n_ not in ('ObjectPack', 'CrowdFraction'))}
        self.log(f'verify: fixed-length AC3 classes at another length: {len(fixed_bad)} ' + ', '.join((f'{n_} {sorted(ref[n_])} -> {v[2]} in {v[3]}' for n_, v in fixed_bad.items())))
        if fixed_bad:
            raise SystemExit("an element of a fixed-length AC3 class at another length: a misread on load (PH5's ObjectAI)")
        tex_bad = []
        for rec, c, n, u, b in bodies:
            if c != 'Material' or rec in dead:
                continue
            try:
                m_ = matprops.parse(b)
            except matprops.ParseError:
                continue
            for it in m_.items:
                tx = it.texture() if it.kind == matprops.INLINE_KIND else None
                if tx and tx not in uids and (not self.ac3.shared_archive(tx)):
                    tex_bad.append(f'{n} @{rec}: slot {it.hash:08X} -> {tx:#010x}')
        self.log(f'verify: material texture slots naming a texture nothing defines: {len(tex_bad)} {tex_bad[:8]}')
        if tex_bad:
            raise SystemExit('a material names a texture nothing defines (draws magenta)')
        import map_audit
        own_mats = [(rec, n, u, b) for rec, c, n, u, b in bodies if c == 'Material' and rec not in dead and (u in self.by_uid)]
        src_slots = {u: map_audit.texture_slots(self.first(u)['body'], 'AC4') for rec, n, u, b in own_mats}
        foreign = map_audit.foreign_texture_slots(own_mats, src_slots, self.ac3.universe, SHARED_ARCHIVES, FOREIGN_TEXTURES_OK | set(self.cfg.get('foreign_textures_ok', ())), self.by_uid)
        self.log(f'verify: AC4 materials sampling a texture only another AC3 map defines: {len(foreign)} {foreign[:8]}')
        if foreign:
            raise SystemExit("an AC4 material samples another AC3 map's texture (E1: a completed slot - stage0.NEUTRAL_TEXTURES - or allow-list it in cfg foreign_textures_ok)")
        self.log(f"verify ({time.time() - t:.0f} s): {len(uids)} uids; live records {len(live)}, never loaded {len(dead)} {sorted(dead)}; body size != len - 1: {dict(size_bad) or 'none'}; listed uids that exist nowhere: {list_dangling}; dangling pointers (live records): {sum(dangling.values())} in {len(dangling)} objects; live references into never-loaded records: {sum(dead_refs.values())} (objects only they define: {len(dead_only)}); mesh instances with baked vertex lighting: {cmi.get(1, 0)}, without: {cmi.get(0, 0)}; cell references to objects defined only in other cells: {sum(cross.values())}; materials on a missing template: {sum(mat_bad.values())}; materials AC3 cannot read (matprops): {len(count_bad)}; World count {count} vs {top} top-level elements; element shapes AC3's maps never show (live records): {len(unseen)} classes")
        for (c, n), k in dangling.most_common(25):
            rec, v, vn, why = examples[c, n]
            self.log(f'   dangling: {c} {n} @{rec}: {k} x, e.g. -> {v} {vn} ({why})')
        for (tt, tn), k in mat_bad.most_common(12):
            self.log(f'   missing template {tt} {tn}: {k} materials')
        for x in count_bad[:12]:
            self.log(f'   UNREADABLE MATERIAL (a load crash): {x}')
        cell_bad = []
        if downgrade.NO_CELL_INDEX:
            for rec, c, n, u, b in bodies:
                if c in ('Entity', 'EntityGroup') and rec not in dead:
                    sg = world.segments(b)
                    for i in range(len(sg) - 1):
                        s_ = sg[i][1]
                        if sg[i + 1][0] == 'BoundingVolume' and len(s_) >= 39 and (s_[-8:-4] == struct.pack('<f', 1.0)) and all((x <= 1 for x in s_[-39:-19])) and (s_[-10:-8] != downgrade.NO_CELL):
                            cell_bad.append(f"{c} {n} @{rec}: {sg[i][0]} ends the entity with cell index {int.from_bytes(s_[-10:-8], 'little')}")
        if downgrade.NO_CELL_INDEX:
            n_ffff = sum((1 for rec, c, n, u, b in bodies if c in ('Entity', 'EntityGroup') and rec not in dead for i, sg_ in [(0, world.segments(b))] for j in range(len(sg_) - 1) if sg_[j + 1][0] == 'BoundingVolume' and len(sg_[j][1]) >= 39 and (sg_[j][1][-8:-4] == struct.pack('<f', 1.0)) and (sg_[j][1][-10:-8] == downgrade.NO_CELL)))
            self.log(f'cell indices: {n_ffff} entity-ending trailers at 0xffff, {len(cell_bad)} with an index')
        if cell_bad:
            self.log(f'CELL INDICES IN A WORLD WITHOUT STAND-IN TILES: {len(cell_bad)}')
            for x in cell_bad[:8]:
                self.log(f'   {x}')
            raise SystemExit('entities carry cell indices but the World has no FakeEntities: the first frame indexes a table it does not have (0x004A609A)')
        cls_by_uid = {u: c for rec, c, n, u, b in bodies if u is not None}
        exempt = getattr(self, 'root_added', set())
        past_m, root_nm = ([], None)
        for rec, c, n, u, b in bodies:
            if c == 'GridCellDataBlock' and rec not in dead:
                nn = u32(b, 10)
                mm = u32(b, 14 + 6 * nn)
                placed = 0
                for j in range(nn):
                    v = u32(b, 14 + 6 * j + 2)
                    if cls_by_uid.get(v) in ('Entity', 'EntityGroup'):
                        placed += 1
                        if j >= mm and (not (rec == self.cfg['root'] and v in exempt)):
                            past_m.append(f"{rec}: entry {j} of {nn} (M {mm}) {cls_by_uid[v]} {self.names.get(v, f'{v:08X}')}")
                if rec == self.cfg['root']:
                    root_nm = (nn, mm, placed)
        self.log(f'cell lists: placed objects listed at or after M (loaded, never drawn): {len(past_m)}; root cell N / M / placed: {root_nm}')
        if past_m:
            for x in past_m[:10]:
                self.log(f'   {x}')
            raise SystemExit("entities listed at or after their cell list's M: AC3 never registers their visuals (clean_cell)")
        tmpl_bad, tmpl_ok = ([], 0)
        for rec, c, n, u, b in bodies:
            if c == 'MaterialTemplate' and rec not in dead and (not self.ac3.shared_archive(u)):
                o, where = self.ac3.main_copy(u)
                if o is None:
                    continue
                if o['body'] != b:
                    diff = [i for i in range(min(len(b), len(o['body']))) if b[i] != o['body'][i]][:4]
                    tmpl_bad.append(f"{n.strip()} @{rec}: differs from AC3's main copy ({where}) at {[hex(i) for i in diff]}")
                else:
                    tmpl_ok += 1
        self.log(f"templates: {tmpl_ok} live copies are AC3's main copies, {len(tmpl_bad)} differ")
        listed_any, local_of = (set(), collections.defaultdict(set))
        for rec, c, n, u, b in bodies:
            if u is not None:
                local_of[rec].add(u)
            if c == 'GridCellDataBlock' and rec not in dead:
                listed_any.update((u32(b, 14 + 6 * j + 2) for j in range(u32(b, 10))))
        unloadable = collections.Counter()
        for rec, c, n, u, b in bodies:
            if c == 'Material' and rec not in dead and (len(b) > 16):
                t = u32(b, 12)
                if t not in local_of[rec] and t not in listed_any and (not self.ac3.shared_archive(t)):
                    unloadable[self.names.get(t) or self.ac3.universe.get(t, ('', '', '', f'{t:08X}'))[3]] += 1
        self.log(f"material templates no list names and no local copy holds: {dict(unloadable) or 'none'}")
        if unloadable:
            raise SystemExit('materials whose template no cell list names: they build no shader (the see-through Tex2 ground)')
        mat_tmpl = collections.Counter(((rec, u32(b, 12)) for rec, c, n, u, b in bodies if c == 'Material' and len(b) > 16))
        used_anywhere = {t for _r, t in mat_tmpl}
        no_local = [f'{n.strip()} @{rec}' for rec, c, n, u, b in bodies if c == 'MaterialTemplate' and rec not in dead and (not mat_tmpl.get((rec, u))) and (u in used_anywhere)]
        self.log(f"templates with no material of their own record using them (users elsewhere only): {no_local or 'none'}")
        if no_local:
            raise SystemExit("templates no material of their own record uses: AC3 keeps no such object (MS28's no VS / no PS)")
        if tmpl_bad:
            for x in tmpl_bad[:10]:
                self.log(f'   {x}')
            raise SystemExit("templates that are not AC3's main copies: a secondary copy draws nothing (MS26's Tex2)")
        contract = stock_common() if self.cfg.get('stock_contract') else {}
        contract_missing = [u for u in contract if u not in uids]
        if contract_missing:
            self.log(f'STOCK CONTRACT BROKEN: {len(contract_missing)} objects every stock map carries are missing: ' + ', '.join((f'{contract[u][0]} {contract[u][1][:30]} (stock @{contract[u][2]})' for u in contract_missing[:8])))
            raise SystemExit('objects every stock map carries are missing (the civilian kit, the podium cameras): the character spawn crashed on them (MS16)')
        if flag_new:
            self.log(f"entity flag 15 on classes AC3's maps never end an entity with (not judged): {dict(flag_new)}")
        if flag_bad:
            self.log(f"ENTITY FLAG 15 UNLIKE AC3'S (by the class ending the entity): {len(flag_bad)}")
            for x in flag_bad[:8]:
                self.log(f'   {x}')
            raise SystemExit("entity flag 15 unlike AC3's: set on a static entity, the renderer crashes on the first frame (MS14)")
        if trailer_bad:
            self.log(f"ENTITY TRAILERS IN AC4'S FORM (a MeshInstanceMaterialInfo ending the entity with a length AC3 never has): {len(trailer_bad)}")
            for x in trailer_bad[:10]:
                self.log(f'   {x}')
            raise SystemExit("entity trailers in AC4's form: a load crash (MS13's Leaves_02A_002)")
        if hdr_bad:
            self.log(f'ENTITY HEADERS OUT OF STEP (component count vs what follows): {len(hdr_bad)}')
            for x in hdr_bad[:10]:
                self.log(f'   {x}')
            raise SystemExit("entity headers whose component count does not match their form: a load crash (MS12's Belfry group)")
        if kept:
            self.log(f"KEPT IN AC4'S LAYOUT by a rule's note, live records: {sum((len(v) for v in kept.values()))} objects - AC3 reads them as AC4 wrote them (MS3 crashed on 50 such meshes):")
            for (c, why), ns in sorted(kept.items(), key=lambda kv: -len(kv[1])):
                self.log(f"   {why}: {len(ns)} x, e.g. {', '.join(sorted(set(ns))[:5])}")
            raise SystemExit("objects left in AC4's layout are in live records: fix the rule or drop them (every one so far was a load crash: MS3's meshes, MS11's merged-collision entities)")
        if dead_refs:
            self.log(f"LIVE REFERENCES INTO NEVER-LOADED RECORDS {sorted(dead)}: {sum(dead_refs.values())} - AC3 resolves none of them (Plantation has 0); a material it cannot resolve draws in its failed-material magenta (MS18 / MS19's crates, carriage, sacks)")
            for k, v in dead_refs.most_common(10):
                self.log(f'   {v} x {k[0]} -> {k[1]}: e.g. {dead_ex[k]}')
            raise SystemExit('live records point at objects only a never-loaded record defines: relocate_never_loaded should have copied them into the root cell')
        for n_, (k, tot, lens, who) in sorted(unseen.items(), key=lambda kv: -kv[1][0])[:45]:
            self.log(f'   unseen shape: {n_} {k} of {tot} elements, lengths {lens}; in {who}')

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True, help="build prefix: out/maps/<name>_<the map's tag>/")
    ap.add_argument('--map', default='tampa', choices=sorted(map_configs.MAPS), help="the map's configuration (ac4/map_configs.py)")
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--tint-fountain-cap', action='store_true', help="diagnostic: the fountain's top cap red at full alpha")
    ap.add_argument('--tint-fountain-fx', action='store_true', help="diagnostic: the fountain's particle emitters tinted (green / magenta / red-or-blue / black)")
    a = ap.parse_args(argv)
    global MAP
    MAP = map_configs.MAPS[a.map]
    if a.tint_fountain_cap:
        MAP['mesh_vertex_regions'] = {'ACGAMP_St_fountain_dress_Mset_LOD0': ((0.85, (255, 0, 0), 1.0, 255), (-1.0, 192, 0.3, 255))}
    if a.tint_fountain_fx:
        G, M, R, K = ((0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 0))
        MAP['particle_tints'] = {'ACGAMP_Fx_water_falling_flow': {0: G, 1: M}, 'ACGAMP_Fx_water_flow_rotating': {0: G, 1: M}, 'ACGAMP_Fx_water_splash': {2: K, 3: R}, 'ACGAMP_Fx_water_splash_rotating': {0: R, 2: K}}
    import map_survey
    _src = common.ac4_archive(MAP['archive'])
    with Forge(_src) as _f:
        _cells = {r.name for r in _f.records if r.name.startswith('Cell')}
    _last = map_survey.grid_last_node(_src, _cells)
    if MAP['root'] != _last:
        raise SystemExit(f"config root {MAP['root']} is not the grid's last node {_last}")
    b = Build(MAP, a.name, a.dry_run)
    try:
        b.run()
    finally:
        b.ac3.close()
if __name__ == '__main__':
    main(sys.argv[1:])
