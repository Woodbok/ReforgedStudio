"""forgelib/repack.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
from concurrent.futures import ProcessPoolExecutor
import datafile
from datafile import decompress, compress, first_chunk_raw_size, CHUNK_HEADER_SIZE
from forge import Forge
from forge_write import ArchiveImage, set_payload, build
from paths import AC3_SOURCE
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
MIN_STORED = 4096

def stored_bytes(payload):
    stored = total = 0
    o = 4
    while o + CHUNK_HEADER_SIZE <= len(payload):
        q = o + CHUNK_HEADER_SIZE
        count = struct.unpack_from('<H', payload, q)[0]
        q += 2
        sizes = [struct.unpack_from('<I', payload, q + 4 * i)[0] for i in range(count)]
        q += 4 * count
        for v in sizes:
            raw, comp = (v & 65535, v >> 16)
            total += raw
            if raw == comp:
                stored += raw
            q += 4 + comp
        o = q
    return (stored, total)

def _repack(job):
    name, payload, pack = job
    plain = decompress(payload)
    new = compress(plain, first_chunk=first_chunk_raw_size(payload), pack=pack)
    if decompress(new) != plain or first_chunk_raw_size(new) != first_chunk_raw_size(payload):
        raise RuntimeError(f'{name}: re-emitted payload does not read back')
    return (name, new)

def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('build')
    p.add_argument('--src', required=True, help="the archive to repack (a build's DataPC.forge)")
    p.add_argument('--store', metavar='PATTERN', help='instead: store (uncompress) the files whose name contains PATTERN')
    p.add_argument('--pristine', default=os.path.join(AC3_SOURCE, 'DataPC.forge'), help="payloads identical to this archive's are left alone")
    a = p.parse_args()
    image = ArchiveImage.read(a.src)
    jobs = []
    with Forge(a.src) as f, Forge(a.pristine) as pf:
        pristine = {r.index: (r.name, r.offset, r.size) for r in pf.records}
        for r in f.records:
            raw = f.read(r)
            if raw[4:8] != struct.pack('<I', datafile.MAGIC):
                continue
            stored, total = stored_bytes(raw)
            if a.store:
                if a.store in (r.name or '') and stored < total:
                    jobs.append((r.index, r.name, raw, False))
            elif stored == total >= MIN_STORED:
                same = pristine.get(r.index)
                if same and same[0] == r.name and (same[2] == r.size) and (pf.read(pf.records[r.index]) == raw):
                    continue
                jobs.append((r.index, r.name, raw, True))
    print(f"{a.src}: {len(jobs)} payloads to {('store' if a.store else 'pack')}")
    index_of = {name: i for i, name, _r, _p in jobs}
    before = after = 0
    with ProcessPoolExecutor() as pool:
        for name, new in pool.map(_repack, [(n, raw, pk) for _i, n, raw, pk in jobs]):
            old = image.files[index_of[name]].payload
            before, after = (before + len(old), after + len(new))
            print(f'  {name}: {len(old):,} -> {len(new):,}')
            set_payload(image, index_of[name], new)
    print(f'  payloads {before:,} -> {after:,} bytes')
    data = build(image)
    dest_dir = os.path.join(OUT, a.build)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(a.src))
    with open(dest, 'wb') as fh:
        fh.write(data)
    print(f'  {os.path.getsize(a.src):,} -> {len(data):,} bytes -> {dest}')
    problems = 0
    repacked = {i for i, *_ in jobs}
    with Forge(a.src) as src, Forge(dest) as out:
        if len(src.records) != len(out.records):
            print('  PROBLEM file count changed')
            problems += 1
        for rs, ro in zip(src.records, out.records):
            if rs.name != ro.name:
                print(f'  PROBLEM #{rs.index} renamed')
                problems += 1
            elif rs.index in repacked:
                if decompress(src.read(rs)) != decompress(out.read(ro)):
                    print(f'  PROBLEM {rs.name} contents differ')
                    problems += 1
            elif src.read(rs) != out.read(ro):
                print(f'  PROBLEM {rs.name} changed but was not repacked')
                problems += 1
    print('OK' if not problems else 'PROBLEMS')
    return 1 if problems else 0
if __name__ == '__main__':
    sys.exit(main())
