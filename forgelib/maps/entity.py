"""forgelib/maps/entity.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import convert
LIST_MARK = 74
FIRST = 79
FLAGS = (b'\x04\x00', b'\x00\x01')
ENTITY_TRAILER = convert.ENTITY_TRAILER
CLOSING = ENTITY_TRAILER + 1

def _is_component(name):
    return convert.descends(name, 'Component')

def components(body):
    ids = convert.class_ids()
    if body[6:10] != struct.pack('<I', ids['Entity']) or len(body) < FIRST + 10:
        return None
    if body[LIST_MARK] != 3:
        return None
    count = struct.unpack_from('<I', body, LIST_MARK + 1)[0]
    bv = body.rfind(struct.pack('<I', ids['BoundingVolume']))
    if bv < 0:
        return None
    end_of_list = bv - ENTITY_TRAILER
    rev = _rev(ids)
    starts = []
    for k in _component_flags(body, FIRST, end_of_list - 10):
        name = rev.get(struct.unpack_from('<I', body, k + 6)[0])
        if name and _is_component(name):
            starts.append((k, name))
    if not starts or starts[0][0] != FIRST:
        return None
    if len(starts) != count:
        return None
    out = []
    for i, (k, name) in enumerate(starts):
        nxt = starts[i + 1][0] if i + 1 < len(starts) else end_of_list
        out.append((k, nxt, name))
    return out
_REV = {}

def _rev(ids):
    if _REV.get('n') != len(ids):
        _REV.update(n=len(ids), rev={v: k for k, v in ids.items()})
    return _REV['rev']

def _component_flags(body, first, last):
    if not hasattr(body, 'find'):
        body = bytes(body)
    ks = []
    for flag in FLAGS:
        k = body.find(flag, first)
        while 0 <= k <= last:
            ks.append(k)
            k = body.find(flag, k + 1)
    ks.sort()
    return ks

def _component_flags_py(body, first, last):
    return [k for k in range(first, last + 1) if body[k:k + 2] in FLAGS]

def exact_end(comps, end):
    return end - 1 if end == comps[-1][1] else end

def strip(body, drop):
    comps = components(body)
    if comps is None:
        return (body, None)
    out = bytearray(body)
    removed = []
    for start, end, name in reversed(comps):
        if drop(name, body[start:end]):
            del out[start:exact_end(comps, end)]
            removed.append(name)
    if removed:
        count = struct.unpack_from('<I', out, LIST_MARK + 1)[0]
        struct.pack_into('<I', out, LIST_MARK + 1, count - len(removed))
    return (bytes(out), removed[::-1])

def main():
    import mapwalk
    import xrefs
    from collections import Counter
    for game in ('ACR', 'AC3'):
        ok = Counter()
        why = Counter()
        for archive in mapwalk.map_archives(game):
            for p in mapwalk.payloads(game, archive, skip_errors=True):
                for o in p.objects:
                    if xrefs.cls(game, o['type']) != 'Entity':
                        continue
                    c = components(o['body'])
                    ok[c is not None] += 1
                    if c is None:
                        b = o['body']
                        why['no list mark' if len(b) <= LIST_MARK or b[LIST_MARK] != 3 else 'chain != count'] += 1
        print(f'{game}: {ok[True]:,} Entities parse, {ok[False]:,} do not {dict(why)}')
if __name__ == '__main__':
    main()

def layout(element):
    import world
    return tuple(((n, len(s)) for n, s in world.segments(element)))

def element_layouts(body):
    comps = components(body)
    if comps is None:
        return None
    out = []
    for i, (s, e, name) in enumerate(comps):
        if i == len(comps) - 1:
            e -= 1
        out.append((name, layout(body[s:e])))
    return out

def ac3_layouts(classes):
    import os
    import pickle
    import mapwalk
    import xrefs
    cache = os.path.join(os.path.dirname(HERE), 'out', 'maps', 'ac3_component_layouts.pkl')
    have = pickle.load(open(cache, 'rb')) if os.path.exists(cache) else {}
    missing = [c for c in classes if c not in have]
    if missing:
        found = {c: set() for c in missing}
        for archive in mapwalk.map_archives('AC3'):
            for p in mapwalk.payloads('AC3', archive, skip_errors=True):
                for o in p.objects:
                    if xrefs.cls('AC3', o['type']) != 'Entity':
                        continue
                    for name, lay in element_layouts(o['body']) or []:
                        if name in found:
                            found[name].add(lay)
        have.update(found)
        pickle.dump(have, open(cache, 'wb'))
    return {c: have[c] for c in classes}
