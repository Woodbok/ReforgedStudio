"""forgelib/ac4/common.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import collections
import gc
import glob
import os
import pickle
import re
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import paths
from forge import Forge, UnsupportedForge
from datafile import decompress, DatafileError
from objects import walk, ObjectError
from transplant import object_at
AC4 = os.path.join(os.path.dirname(os.path.dirname(paths.AC3)), "Assassin's Creed IV Black Flag", 'multi')
AC4_SOUND = os.path.join(AC4, 'sounddata', 'pc')
AC3 = paths.AC3_SOURCE
AC3_SOUND = os.path.join(paths.AC3_SOURCE, 'sounddata', 'Pc')
OUT = os.path.join(ROOT, 'out', 'ac4')
AC4_MAPS = {'Havana': 'DataPC_ACGA_Havana.forge', 'Prison': 'DataPC_ACGA_Prison.forge', 'FisherVillage': 'DataPC_ACGA_FisherVillage.forge', 'Expedition': 'DataPC_ACGA_Expedition.forge', 'PirateHQ': 'DataPC_ACGA_Pirate_Headqua.forge', 'SecretPirate': 'DataPC_ACGA_Secret_Pirate_.forge', 'Kingston': 'DataPC_ACGA_KINGSTON_dlc.forge', 'Tutorial': 'DataPC_ACGAMP_Tutorial_Map.forge'}
MSETTLEMENT = 'DataPC_AC3MP_MSettlement.forge'
SHARED_MAPS = {'Whiteroom': ('DataPC_AC2MP_Whiteroom.forge', 'DataPC_AC2MP_Whiteroom.forge'), 'Plantation': ('DataPC_AC3MP_Plantation.forge', 'DataPC_AC3MP_Plantation_dlc.forge'), 'Charlestown': ('DataPC_AC3MP_Charlestown_dlc.forge', 'DataPC_AC3MP_Charlestown_dlc.forge'), 'FortMathieu': ('DataPC_AC3MP_FortMathieu_dlc.forge', 'DataPC_AC3MP_FortMathieu_dlc.forge'), 'Martinique': ('DataPC_AC3MP_Martinique_dlc.forge', 'DataPC_AC3MP_Martinique.forge')}
AC3_COPY = os.path.join(os.path.dirname(paths.AC3) + ' - Copy', 'multi')
AC3_FOLDERS = (AC3, AC3_COPY, paths.AC3) + tuple(sorted(glob.glob(os.path.join(ROOT, 'out', 'dlcmaps', '*'))))

def ac3_archive(name):
    for folder in AC3_FOLDERS:
        for candidate in (name, name.replace('_dlc.forge', '.forge')):
            p = os.path.join(folder, candidate)
            if os.path.isfile(p):
                return p
    raise FileNotFoundError(f'no AC3 copy of {name} in {AC3_FOLDERS}')

def ac4_archive(name):
    p = name if os.path.isabs(name) else os.path.join(AC4, name)
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    return p
REG = pickle.load(open(os.path.join(ROOT, 'crossmaps', '_reg.pkl'), 'rb'))
AC3_CLASSES = set(REG['AC3'].values())

def cls(type_id, game='AC4'):
    return REG[game].get(type_id) or REG['AC3'].get(type_id) or f'0x{type_id:08X}'

def ac4_only(name):
    return name not in AC3_CLASSES and (not name.startswith('0x'))

def _wanted(rec_name, names):
    if names is None:
        return True
    if isinstance(names, (set, frozenset, list, tuple)):
        return rec_name in names
    return re.search(names, rec_name) is not None

def record_names(path):
    with Forge(path) as f:
        return [r.name for r in f.records]

def payloads(path, names=None):
    with Forge(path) as f:
        for rec in f.records:
            if not _wanted(rec.name, names):
                continue
            try:
                d = decompress(f.read(rec))
            except (DatafileError, UnsupportedForge, struct.error, IndexError, ValueError):
                continue
            yield (rec, d)

def walk_any(d):
    try:
        return [(off, size) for off, size, _serial in walk(d)]
    except ObjectError:
        pass
    try:
        count = struct.unpack_from('<H', d, 0)[0]
        o, sizes = (2, [])
        for _ in range(count):
            sizes.append(struct.unpack_from('<I', d, o + 4)[0])
            o += 10
        o += 4 + struct.unpack_from('<I', d, o)[0]
    except struct.error:
        return []
    out = []
    for size in sizes:
        out.append((o, size))
        o += size
    return out if count and o == len(d) else []

def objects_in(d):
    try:
        return [object_at(d, off, size) for off, size in walk_any(d)]
    except (struct.error, IndexError):
        return []
UID_PREFIX = b'\x00\x01'

def has_uid(o):
    return o['body'][:2] == UID_PREFIX

def load(path, names=None, keep=None):
    out, dups = ({}, 0)
    for rec, d in payloads(path, names):
        counter = collections.Counter()
        for o in objects_in(d):
            if keep is not None and (not keep(o)):
                continue
            if has_uid(o):
                k = o['uid']
            else:
                tn = (o['type'], o['name'])
                k = ('noid', rec.name, o['type'], o['name'], counter[tn])
                counter[tn] += 1
            if k in out:
                dups += 1
                continue
            o.pop('offset', None)
            o.pop('body_at', None)
            out[k] = (rec.name, o)
        del d
    gc.collect()
    return (out, dups)

def differing_bytes(x, y):
    xb = (int.from_bytes(x, 'little') ^ int.from_bytes(y, 'little')).to_bytes(len(x), 'little')
    n = len(x) - xb.count(0)
    pos = [m.start() for m in re.finditer(b'[^\\x00]', xb)] if n <= 16 else []
    return (n, pos)

def report(name, lines):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name)
    with open(p, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('wrote', p)
    return p

def hexs(b, n=16):
    return b[:n].hex(' ')
