"""forgelib/witcher3/scene.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import pickle
import struct
import sys
from collections import Counter
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from witcher3 import bundles, cr2w

def fnv1a64(s):
    h = 14695981039346656037
    for c in s.encode('latin-1'):
        h = (h ^ c) * 1099511628211 & 18446744073709551615
    return h
_HASHES = None

def path_of_hash(h):
    global _HASHES
    if _HASHES is None:
        cp = os.path.join(bundles.CACHE, 'pathhash.pkl')
        n = len(bundles.index())
        if os.path.exists(cp):
            with open(cp, 'rb') as f:
                d = pickle.load(f)
            if d['n'] == n:
                _HASHES = d['h']
        if _HASHES is None:
            _HASHES = {fnv1a64(k): k for k in bundles.index() if not k.endswith('.buffer')}
            with open(cp, 'wb') as f:
                pickle.dump({'n': n, 'h': _HASHES}, f, protocol=4)
    return _HASHES.get(h)

def vlq(b, o):
    c = b[o]
    o += 1
    neg = c & 128
    n = c & 63
    if c & 64:
        sh = 6
        while True:
            c = b[o]
            o += 1
            n |= (c & 127) << sh
            sh += 7
            if not c & 128:
                break
    return (-n if neg else n, o)

def sector_data(tail):
    t = tail
    o = 5
    n, o = vlq(t, o)
    res = []
    for i in range(n):
        box = struct.unpack_from('<6f', t, o)
        h = struct.unpack_from('<Q', t, o + 24)[0]
        res.append((path_of_hash(h) if h else None, h, box))
        o += 32
    m, o = vlq(t, o)
    objs = []
    for i in range(m):
        ty, fl, rad, off = struct.unpack_from('<BBHQ', t, o)
        objs.append((ty, fl, rad, off, struct.unpack_from('<3f', t, o + 12)))
        o += 24
    bs, o = vlq(t, o)
    return (res, objs, t[o:o + bs])

class Placement:
    __slots__ = ('kind', 'path', 'matrix', 'pos', 'layer', 'flags', 'raw', 'box')

    def __repr__(self):
        return '<%s %s @ %s>' % (self.kind, self.path, np.round(self.pos, 2))

    def world_box(self):
        b = self.box
        c = np.array([[x, y, z] for x in (b[0], b[3]) for y in (b[1], b[4]) for z in (b[2], b[5])]) @ self.matrix
        c += self.pos
        return (c.min(0), c.max(0))

    def world(self, local):
        return np.asarray(local, np.float64) @ self.matrix + self.pos

def layer_placements(layer, kinds=(1, 2)):
    f = cr2w.load(layer)
    out = []
    for e in f.exports:
        if e.cls != 'CSectorData':
            continue
        f.parse(e)
        res, objs, bd = sector_data(e.tail)
        for ty, fl, rad, off, _p in objs:
            if ty not in kinds:
                continue
            m = np.array(struct.unpack_from('<9f', bd, off), np.float64).reshape(3, 3)
            pos = np.array(struct.unpack_from('<3f', bd, off + 36), np.float64)
            if ty in (1, 7):
                ri = struct.unpack_from('<I', bd, off + 56)[0]
                flags = struct.unpack_from('<H', bd, off + 50)[0]
            else:
                ri = struct.unpack_from('<I', bd, off + 56)[0]
                flags = 0
            p = Placement()
            p.kind = {1: 'mesh', 2: 'collision', 7: 'mesh'}[ty]
            p.path = res[ri][0] if ri < len(res) else None
            p.matrix, p.pos, p.layer, p.flags = (m, pos, layer, flags)
            p.raw = bd[off:off + 64]
            p.box = res[ri][2] if ri < len(res) else None
            out.append(p)
    return out

def object_types(layer):
    f = cr2w.load(layer)
    c = Counter()
    for e in f.exports:
        if e.cls == 'CSectorData':
            f.parse(e)
            _r, objs, _b = sector_data(e.tail)
            c.update((o[0] for o in objs))
    return c

def entities(layer):
    f = cr2w.load(layer)
    out = []
    for e in f.exports:
        if e.parent != 1 or e.cls in ('CSectorData',):
            continue
        f.parse(e)
        tr = e.get('transform')
        if tr is None:
            continue
        tpl = f.handle(e.get('template')) if e.get('template') else None
        meshes = []
        for c in f.exports:
            if c.parent == e.index:
                f.parse(c)
                mh = c.get('mesh')
                if mh:
                    meshes.append((c.cls, f.handle(mh), c.get('transform')))
        out.append((e.cls, tr, tpl, meshes))
    return out

def layer_infos(world_path):
    f = cr2w.load(world_path)
    hidden = []
    infos = {}
    for e in f.exports:
        if e.cls == 'CLayerGroup':
            f.parse(e)
            if e.get('isVisibleOnStart') is False:
                hidden.append(e.get('depotPath', '').lower())
        elif e.cls == 'CLayerInfo':
            f.parse(e)
            infos[e.get('depotFilePath', '').lower()] = {'tag': e.get('layerBuildTag', 'LBT_None'), 'type': e.get('layerType', 'LT_AutoStatic')}
    for k, v in infos.items():
        v['visible'] = not any((k.startswith(h) for h in hidden))
    return infos

def layers(prefix, ext='.w2l'):
    return bundles.find(prefix, ext)

def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('layer')
    ap.add_argument('--types', action='store_true')
    ap.add_argument('--entities', action='store_true')
    a = ap.parse_args(argv)
    names = [a.layer] if a.layer.endswith('.w2l') else layers(a.layer)
    tot = Counter()
    for n in names:
        if a.types:
            c = object_types(n)
            tot.update(c)
            print('%-100s %s' % (n, dict(c)))
            continue
        if a.entities:
            for cls, tr, tpl, meshes in entities(n):
                print(n.split('\\')[-1], cls, np.round(tr['pos'], 1), tpl, meshes)
            continue
        ps = layer_placements(n)
        tot.update((p.kind for p in ps))
        unk = sum((1 for p in ps if p.path is None))
        print('%-100s %5d meshes %5d collision %d unresolved' % (n, sum((p.kind == 'mesh' for p in ps)), sum((p.kind == 'collision' for p in ps)), unk))
    print(dict(tot))
if __name__ == '__main__':
    main(sys.argv[1:])
