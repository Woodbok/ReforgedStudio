"""forgelib/uid_index.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import pickle
import struct
import sys
from forge import Forge, UnsupportedForge
from datafile import decompress, DatafileError
from objects import walk, parse_object, ObjectError
import paths
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'uid')
UID_OFFSET_IN_BODY = 2
UID_PREFIX = bytes((0, 1))

def scan(path):
    out = {}
    collisions = 0
    with Forge(path) as f:
        for rec in f.records:
            try:
                d = decompress(f.read(rec))
                entries = walk(d)
            except (DatafileError, ObjectError, IndexError, struct.error, UnsupportedForge):
                continue
            for off, size, _serial in entries:
                try:
                    type_id, name = parse_object(d, off, size)
                except ObjectError:
                    continue
                body_at = off + 12 + len(name)
                if body_at + 6 > off + size:
                    continue
                if bytes(d[body_at:body_at + 2]) != UID_PREFIX:
                    continue
                uid = struct.unpack_from('<I', d, body_at + UID_OFFSET_IN_BODY)[0]
                if uid in out and out[uid][2] != name:
                    collisions += 1
                out[uid] = (rec.name, type_id, name)
    return (out, collisions)

def load(game, archive='DataPC.forge'):
    os.makedirs(CACHE, exist_ok=True)
    cached = os.path.join(CACHE, f'{game}_{archive}.pkl')
    if os.path.exists(cached):
        return pickle.load(open(cached, 'rb'))
    root = paths.ACR if game == 'ACR' else paths.AC3_SOURCE
    out, collisions = scan(os.path.join(root, archive))
    pickle.dump((out, collisions), open(cached, 'wb'))
    return (out, collisions)

def main():
    for game in sys.argv[1:] or ['ACR', 'AC3']:
        index, collisions = load(game)
        print(f'{game}: {len(index)} uids, {collisions} reused by a different object')
if __name__ == '__main__':
    main()
