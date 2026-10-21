"""forgelib/dir_check_build.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import glob
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths
from datafile import decompress
from forge import Forge
from id_census import payload_entries, unmapped_objects
from objects import ObjectError, directory_mismatches, identity_of, parse_object
PAYLOAD_MAGIC = b'3\xaa\xfbW'
FIRST = ('DataPC.forge', 'DataPC_extra.forge')
_names = {}

def class_name(c):
    if not _names:
        import exe_registry
        _names.update(exe_registry.load(exe_registry.AC3)[0])
    return _names.get(c, f'0x{c:08X}')

def archives(target):
    if os.path.isfile(target):
        return [target]
    found = {os.path.basename(p): p for p in glob.glob(os.path.join(target, '*.forge'))}
    return [found[a] for a in FIRST if a in found] + [found[a] for a in sorted(found) if a not in FIRST]

def check(path):
    arch = os.path.basename(path)
    pristine = os.path.join(paths.AC3_SOURCE, arch)
    checked = bad = unmapped = 0
    with Forge(path) as f:
        p = Forge(pristine) if os.path.exists(pristine) else None
        try:
            stock = {r.name: r for r in p.records} if p else {}
            for r in f.records:
                raw = f.read(r)
                s = stock.get(r.name)
                if s is not None and s.size == r.size and (p.read(s) == raw):
                    continue
                if raw[4:8] != PAYLOAD_MAGIC:
                    continue
                checked += 1
                plain = decompress(raw)
                try:
                    mm = directory_mismatches(plain)
                except ObjectError:
                    mm = [(i, d, identity_of(plain[o:o + size])) for i, (o, size, d) in enumerate(payload_entries(plain)[0]) if identity_of(plain[o:o + size]) not in (None, d)]
                if mm:
                    bad += 1
                    print('  MISMATCH', r.name, [(i, hex(a), hex(b)) for i, a, b in mm[:3]])
                hits = unmapped_objects(plain)
                if hits:
                    unmapped += 1
                    ents = payload_entries(plain)[0]
                    for pos, how, u, c in hits:
                        where = 'filter table' if pos is None else f'#{pos} {parse_object(plain, *ents[pos][:2])[1]}'
                        print(f'  UNMAPPED {r.name} {where} ({class_name(c)}): {how} id 0x{u:08X}')
        finally:
            if p:
                p.close()
    print(f'{arch}: {checked} changed/added payloads checked, {bad} with mismatches, {unmapped} with unmapped ids')
    return bad + unmapped

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    failed = 0
    for target in argv:
        found = archives(target)
        if not found:
            print(f'{target}: no .forge archives')
            return 2
        for path in found:
            failed += check(path)
    return 1 if failed else 0
if __name__ == '__main__':
    sys.exit(main())
