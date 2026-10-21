"""Rewriting objects in a map archive: the shared record loop (decompress, walk, rebuild, compress, set_payload,
write) behind every edit, plus a byte-identical check of everything untouched.
"""
from . import mapdata as M
from datafile import compress, decompress  # noqa: E402  (forgelib)
from forge_write import ArchiveImage, set_payload, write  # noqa: E402  (forgelib)
from transplant import object_at  # noqa: E402  (forgelib)


def rewrite(src, dest, fn, log=print, insert=None, after_types=()):
    """fn(record name, object dict (forgelib's transplant.object_at)) -> new body bytes, or None to keep it.
    insert: {record name: [(serial, object bytes)]} - new objects, placed after the record's last object whose type is
    in `after_types` (else at its end); listing them (a GridCellDataBlock) is fn's job.
    -> (records changed, other records byte-identical)."""
    insert = insert or {}
    image = ArchiveImage.read(src)
    with M.Forge(src) as f:
        raw = {r.index: f.read(r) for r in f.records}
        recs = [r for r in f.records if r.name != "GlobalMetaFile" and len(raw[r.index]) > 8]   # empty records (some ported maps have one) carry no objects
    changed = set()
    for rec in recs:
        d = decompress(M.mapwalk.split_prefix(raw[rec.index])[1])
        entries, table = M.mapwalk.walk(d)
        objs, touched, last = [], rec.name in insert, -1
        for off, size, serial, filt in entries:
            o = object_at(d, off, size)
            new = fn(rec.name, o)
            if new is not None and new != o["body"]:
                touched = True
            if o["type"] in after_types:
                last = len(objs)
            objs.append((serial, M.mapwalk.object_bytes(o, o["body"] if new is None else new),
                         None if filt == M.mapwalk.NO_FILTER else filt))
        if rec.name in insert:
            at = last + 1 if last >= 0 else len(objs)
            objs[at:at] = [(serial, data, None) for serial, data in insert[rec.name]]
        if touched:
            nd = M.mapwalk.rebuild(objs, table)
            pl = compress(nd, first_chunk=M.mapwalk.first_chunk(nd))
            assert decompress(pl) == nd
            set_payload(image, rec.index, pl)
            changed.add(rec.index)
    write(dest, image)
    with M.Forge(dest) as f:
        same = all(f.read(r) == raw[r.index] for r in f.records if r.index not in changed)
    log(f"{dest}: {len(changed)} records rewritten; other records byte-identical: {'yes' if same else 'NO'}")
    return changed, same


def pack(path, original, log=print, workers=None):
    """Compress (LZO2A, as the game's own files) every record of build `path` that differs from `original` and is
    stored uncompressed - forgelib's datafile.compress writes stored blocks unless asked to pack, so every record an
    edit touches comes out at its raw size (Wolcott's Cell00070: 10.1 MB stock, 20.8 MB rewritten, 10.2 MB packed).
    Uses forgelib's repack worker (the same payload, re-emitted packed, read back), in parallel; rewrites `path`.
    -> (bytes before, bytes after)."""
    import os
    from concurrent.futures import ProcessPoolExecutor

    import repack  # noqa: E402  (forgelib)
    from forge_write import build  # noqa: E402  (forgelib)

    with M.Forge(original) as f:
        stock = {r.name: f.read(r) for r in f.records}
    image = ArchiveImage.read(path)
    jobs = []
    with M.Forge(path) as f:
        for r in f.records:
            raw = f.read(r)
            if r.name == "GlobalMetaFile" or len(raw) <= 8 or stock.get(r.name) == raw:
                continue
            stored, total = repack.stored_bytes(raw)
            if total and stored == total:
                jobs.append((r.index, r.name, raw))
    before = os.path.getsize(path)
    if jobs:
        index_of = {name: i for i, name, _r in jobs}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for name, new in pool.map(repack._repack, [(n, raw, True) for _i, n, raw in jobs]):
                set_payload(image, index_of[name], new)
        data = build(image)
        with open(path + ".packing", "wb") as fh:
            fh.write(data)
        with M.Forge(path) as a, M.Forge(path + ".packing") as b:
            packed = {i for i, _n, _r in jobs}
            for ra, rb in zip(a.records, b.records):
                same = (decompress(a.read(ra)) == decompress(b.read(rb))) if ra.index in packed else \
                    a.read(ra) == b.read(rb)
                if ra.name != rb.name or not same:
                    raise RuntimeError(f"packing changed {ra.name}")
        os.replace(path + ".packing", path)
    after = os.path.getsize(path)
    log(f"{path}: {len(jobs)} records packed, {before / 2**20:.0f} MB -> {after / 2**20:.0f} MB")
    return before, after
