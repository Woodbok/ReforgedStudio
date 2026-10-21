"""forgelib/bootstrap_build.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
from forge import Forge
from datafile import decompress, compress, first_chunk_raw_size
from objects import walk, directory_size
from transplant import object_at
from forge_write import ArchiveImage, set_payload, build
from paths import AC3_SOURCE
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
BOOTSTRAP = 'Game Bootstrap Settings'
ARCHIVE = 'DataPC.forge'

class Bootstrap:

    def __init__(self, archive=None):
        self.archive = archive or os.path.join(AC3_SOURCE, ARCHIVE)
        with Forge(self.archive) as f:
            rec = next((r for r in f.records if r.name == BOOTSTRAP))
            self.index = rec.index
            self.original = f.read(rec)
        self.plain = decompress(self.original)
        self.objects = [object_at(self.plain, o, s) for o, s, _ in walk(self.plain)]
        if first_chunk_raw_size(self.original) != directory_size(self.plain):
            raise SystemExit("this payload's first chunk is not just its directory; the 41-byte-preamble case is not handled")

    def one(self, type_id=None, name=None):
        hits = [o for o in self.objects if (type_id is None or o['type'] == type_id) and (name is None or o['name'] == name)]
        if len(hits) != 1:
            what = 'any' if type_id is None else f'0x{type_id:08X}'
            raise SystemExit(f'{len(hits)} objects match type {what} name {name!r}')
        return hits[0]

    def position(self, obj):
        return next((i for i, o in enumerate(self.objects) if o is obj))

    def emit(self, new_plain, build_name):
        first = directory_size(new_plain)
        count = struct.unpack_from('<H', new_plain, 0)[0]
        print(f'  {len(self.objects)} -> {count} objects, first chunk {directory_size(self.plain)} -> {first}, raw {len(self.plain):,} -> {len(new_plain):,} bytes')
        payload = compress(new_plain, first_chunk=first)
        if decompress(payload) != new_plain:
            raise SystemExit('re-encoded payload does not decompress to its input')
        image = ArchiveImage.read(self.archive)
        set_payload(image, self.index, payload)
        data = build(image)
        dest_dir = os.path.join(OUT, build_name)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, ARCHIVE)
        with open(dest, 'wb') as fh:
            fh.write(data)
        print(f'  {os.path.getsize(self.archive):,} -> {len(data):,} bytes -> {dest}')
        with Forge(dest) as f:
            rec = next((r for r in f.records if r.name == BOOTSTRAP))
            if decompress(f.read(rec)) != new_plain:
                raise SystemExit('read back: bootstrap differs from what was written')
            bad = 0
            for r in f.records:
                p = f.read(r)
                if len(p) >= 12 and p[4:8] == b'3\xaa\xfbW':
                    try:
                        decompress(p)
                    except Exception:
                        bad += 1
            print(f'  read back: bootstrap identical, {len(f)} files, {bad} that no longer decompress')
            if bad:
                raise SystemExit('payloads broken')
        return dest

def read_back_objects(dest):
    with Forge(dest) as f:
        rec = next((r for r in f.records if r.name == BOOTSTRAP))
        plain = decompress(f.read(rec))
    return (plain, [object_at(plain, o, s) for o, s, _ in walk(plain)])
