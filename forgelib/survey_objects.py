"""forgelib/survey_objects.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import hashlib
import os
import pickle
import struct
import sys
from forge import Forge, UnsupportedForge
from datafile import decompress, DatafileError
from objects import walk, parse_object, ObjectError
import paths
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'survey')

def survey_archive(path):
    rows = []
    stats = {'files': 0, 'unreadable': 0, 'objects': 0}
    with Forge(path) as f:
        for rec in f.records:
            try:
                p = f.read(rec)
            except UnsupportedForge:
                stats['unreadable'] += 1
                continue
            if len(p) < 12 or p[4:8] != b'3\xaa\xfbW':
                stats['unreadable'] += 1
                continue
            try:
                d = decompress(p)
                entries = walk(d)
            except (DatafileError, ObjectError, IndexError, struct.error):
                stats['unreadable'] += 1
                continue
            stats['files'] += 1
            for pos, (off, size, _serial) in enumerate(entries):
                body = d[off:off + size]
                try:
                    type_id, name = parse_object(d, off, size)
                except ObjectError:
                    type_id, name = (0, '')
                rows.append((rec.name, pos, type_id, name, size, hashlib.sha1(body).hexdigest()))
                stats['objects'] += 1
    return (rows, stats)

def cached(game, archive, root):
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, f'{game}_{archive}.pkl')
    if os.path.exists(out):
        return pickle.load(open(out, 'rb'))
    rows, stats = survey_archive(os.path.join(root, archive))
    pickle.dump((rows, stats), open(out, 'wb'))
    print(f'  {game}/{archive}: {stats}', flush=True)
    return (rows, stats)

def main(argv):
    targets = argv or ['ACR:DataPC.forge', 'AC3:DataPC.forge', 'ACR:DataPC_extra.forge', 'AC3:DataPC_extra.forge']
    for t in targets:
        game, archive = t.split(':', 1)
        root = paths.ACR if game == 'ACR' else paths.AC3_PRISTINE
        rows, stats = cached(game, archive, root)
        print(f'{game}/{archive}: {len(rows)} objects, {stats}', flush=True)
if __name__ == '__main__':
    main(sys.argv[1:])
