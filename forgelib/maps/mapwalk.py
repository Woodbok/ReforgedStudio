"""forgelib/maps/mapwalk.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import paths
from datafile import decompress, MAGIC
from forge import Forge
from transplant import object_at
NO_FILTER = 65535
MAGIC_BYTES = struct.pack('<I', MAGIC)
ROOTS = {'ACR': paths.ACR, 'AC3': paths.AC3_SOURCE}

class MapWalkError(Exception):
    pass

def split_prefix(payload):
    n = struct.unpack_from('<I', payload, 0)[0]
    if n == 0:
        return ([], payload)
    at = 4 + n * 8
    if payload[at:at + 4] != MAGIC_BYTES:
        raise MapWalkError(f'payload+0 = {n} but no chunk at +{at}')
    pairs = [struct.unpack_from('<II', payload, 4 + i * 8) for i in range(n)]
    return (pairs, b'\x00\x00\x00\x00' + payload[at:])

def walk(d):
    count = struct.unpack_from('<H', d, 0)[0]
    o = 2
    dirs = []
    for _ in range(count):
        dirs.append(struct.unpack_from('<IIH', d, o))
        o += 10
    table_size = struct.unpack_from('<I', d, o)[0]
    o += 4
    table = bytes(d[o:o + table_size])
    o += table_size
    out = []
    for serial, size, filt in dirs:
        out.append((o, size, serial, filt))
        o += size
    if o != len(d):
        raise MapWalkError(f'directory accounts for {o} of {len(d)} bytes')
    return (out, table)

class Payload:

    def __init__(self, rec, raw):
        self.rec = rec
        self.prefix, body = split_prefix(raw)
        self.data = decompress(body)
        entries, self.filter_table = walk(self.data)
        self.objects = []
        for off, size, serial, filt in entries:
            o = object_at(self.data, off, size)
            o['serial'] = serial
            o['filter'] = None if filt == NO_FILTER else filt
            o['has_uid'] = o['body'][:2] == b'\x00\x01'
            self.objects.append(o)

def object_bytes(o, body=None):
    body = o['body'] if body is None else body
    size = o['body_size'] + len(body) - len(o['body'])
    name = o['name'].encode('latin-1')
    return struct.pack('<III', o['type'], size, len(name)) + name + body

def rebuild(objs, table=b''):
    out = bytearray(struct.pack('<H', len(objs)))
    for serial, data, filt in objs:
        out += struct.pack('<IIH', serial, len(data), NO_FILTER if filt is None else filt)
    out += struct.pack('<I', len(table)) + table
    for _s, data, _f in objs:
        out += data
    return bytes(out)

def first_chunk(d):
    count = struct.unpack_from('<H', d, 0)[0]
    o = 2 + count * 10
    return o + 4 + struct.unpack_from('<I', d, o)[0]

def archive_path(game, archive):
    return os.path.join(ROOTS[game], archive)

def payloads(game, archive, skip_errors=False):
    with Forge(archive_path(game, archive)) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile':
                continue
            try:
                yield Payload(rec, f.read(rec))
            except Exception as e:
                if not skip_errors:
                    raise MapWalkError(f'{archive} #{rec.index} {rec.name}: {e}') from e

def defined_uids(game, archive, skip_errors=True):
    import pickle
    path = archive_path(game, archive)
    st = os.stat(path)
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'out', 'maps', '_cache')
    cached = os.path.join(cache_dir, f'uids_{game}_{archive}_{st.st_size}_{int(st.st_mtime)}.pkl')
    if os.path.exists(cached):
        with open(cached, 'rb') as fh:
            return pickle.load(fh)
    out = set()
    for p in payloads(game, archive, skip_errors=skip_errors):
        for o in p.objects:
            if o.get('has_uid'):
                out.add(o['uid'])
    os.makedirs(cache_dir, exist_ok=True)
    with open(cached, 'wb') as fh:
        pickle.dump(out, fh)
    return out

def map_archives(game):
    shared = {'DataPC.forge', 'DataPC_extra.forge', 'DataPC_extraparams.forge', 'DataPC_CoreFX.forge', 'DataPC_Fire.forge', 'DataPC_localization.forge'}
    return sorted((a for a in os.listdir(ROOTS[game]) if a.endswith('.forge') and a not in shared and ('_skins_' not in a)))

def main(argv):
    games = argv or ['ACR', 'AC3']
    bad_total = 0
    for game in games:
        for archive in map_archives(game):
            files = objs = prefixed = filtered = named = bad = 0
            with Forge(archive_path(game, archive)) as f:
                for rec in f.records:
                    if rec.name == 'GlobalMetaFile':
                        continue
                    try:
                        p = Payload(rec, f.read(rec))
                    except Exception as e:
                        bad += 1
                        print(f'   FAIL #{rec.index} {rec.name}: {e}')
                        continue
                    files += 1
                    objs += len(p.objects)
                    prefixed += bool(p.prefix)
                    filtered += bool(p.filter_table)
                    named += p.objects[0]['name'] == rec.name
            bad_total += bad
            print(f'{game} {archive:40} files {files:5}  objects {objs:6}  prefixed {prefixed:3}  filtered {filtered:3}  name=index {named}/{files}  failures {bad}')
    print(f'total failures: {bad_total}')
    return 1 if bad_total else 0
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
