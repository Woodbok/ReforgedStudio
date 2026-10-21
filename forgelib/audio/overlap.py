"""forgelib/audio/overlap.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import hashlib
import struct
from collections import Counter
from akpk import ACR_SOUND, AC3_SOUND, packages
from banks import HIRC_TYPES, load, chunks

def fnv1(name):
    h = 2166136261
    for c in name.lower().encode('ascii'):
        h = h * 16777619 & 4294967295
        h ^= c
    return h

def media_index(root, langs=('sfx', 'eng')):
    by_hash, by_id, shapes = ({}, {}, Counter())
    for p in packages(root):
        if not any((l in p.name for l in langs)):
            continue
        with open(p.path, 'rb') as f:
            for e in p.streams + p.externals:
                f.seek(e.offset)
                b = f.read(e.size)
                h = hashlib.sha1(b).hexdigest()
                by_hash.setdefault(h, []).append((p.name, e.id))
                by_id[e.id] = h
                shapes[wem_shape(b)] += 1
    return (by_hash, by_id, shapes)

def wem_shape(b):
    cs = chunks(b[12:4096])
    fmt = next((s for t, _, s in cs if t == 'fmt '), None)
    return (fmt, tuple((t for t, _, _ in cs if t != 'data')))
if __name__ == '__main__':
    _, acr = load(ACR_SOUND)
    _, ac3 = load(AC3_SOUND)

    def names(bs):
        return {v for b in bs for v in b.names.values()}

    def ids(bs, t):
        return {oid for b in bs for tt, oid, _ in b.hirc if tt == t}
    an, cn = (names(acr), names(ac3))
    print(f'bank names: ACR {len(an)}  AC3 {len(cn)}  shared {len(an & cn)}')
    print('  shared:', sorted(an & cn)[:40])
    for t in (4, 8, 1):
        a, c = (ids(acr, t), ids(ac3, t))
        print(f'{HIRC_TYPES[t]:6} ids: ACR {len(a)}  AC3 {len(c)}  shared {len(a & c)}')
    ah, aid, ashape = media_index(ACR_SOUND)
    ch, cid, cshape = media_index(AC3_SOUND)
    print(f'streamed media: ACR {len(aid)} ids / {len(ah)} distinct files; AC3 {len(cid)} ids / {len(ch)} distinct files')
    same_id = set(aid) & set(cid)
    print(f'  ids in both: {len(same_id)}, of which byte-identical: {sum((aid[i] == cid[i] for i in same_id))}')
    print(f'  byte-identical files (any id): {len(set(ah) & set(ch))}')
    print('  ACR wem header shapes:', dict(ashape))
    print('  AC3 wem header shapes:', dict(cshape))
    emb_a = {m for b in acr for m, _ in b.media}
    emb_c = {m for b in ac3 for m, _ in b.media}
    print(f'embedded media ids: ACR {len(emb_a)}  AC3 {len(emb_c)}  shared {len(emb_a & emb_c)}')
