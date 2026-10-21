"""forgelib/witcher3/meshes.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from witcher3 import bundles, cr2w
RC = 37

def render_chunks(rc):
    rc = bytes(rc)
    n = rc[0]
    out = []
    for i in range(n):
        o = 1 + RC * i
        a = rc[o]
        streams = struct.unpack_from('<5I', rc, o + 1)
        b, ioff, vtype, nv, ni, c1, c2, c3, lod = struct.unpack_from('<BIBHIBBBB', rc, o + 21)
        out.append({'streams': streams, 'index_offset': ioff, 'vtype': vtype, 'nv': nv, 'ni': ni, 'a': a, 'b': b, 'c': (c1, c2, c3), 'lod': lod})
    return out

def material_params(f, e):
    f.parse(e)
    t = e.tail
    out = {}
    if not t or len(t) < 4:
        return out
    n = struct.unpack_from('<I', t, 0)[0]
    o = 4
    for _ in range(n):
        sz, ni, ti = struct.unpack_from('<IHH', t, o)
        name, typ = (f.names[ni], f.names[ti])
        try:
            v = f.value(typ, t, o + 8, o + sz)
            if typ.startswith('handle:') and v:
                v = f.handle(v)
        except Exception:
            v = None
        out[name] = v
        o += sz
    return out
_MI = {}

def load(path):
    if str(path).lower().endswith('.redcloth'):
        from witcher3 import cloth
        return cloth.ClothMesh(path)
    return Mesh(path)

def resolve_material(base, params):
    params = dict(params or {})
    seen = 0
    while isinstance(base, str) and base.lower().endswith('.w2mi') and (seen < 8):
        seen += 1
        if base not in _MI:
            try:
                f = cr2w.load(base)
                e = next((x for x in f.exports if x.cls == 'CMaterialInstance'))
                f.parse(e)
                _MI[base] = (f.handle(e.get('baseMaterial')), material_params(f, e))
            except Exception:
                _MI[base] = (None, {})
        nb, np_ = _MI[base]
        for k, v in np_.items():
            params.setdefault(k, v)
        base = nb
    return (base, params)

class Mesh:

    def __init__(self, name):
        self.name = name
        self.f = f = cr2w.load(name)
        self.cm = cm = f.parse(f.exports[0])
        if cm.cls not in ('CMesh',):
            raise ValueError('%s: %s, not CMesh' % (name, cm.cls))
        cd = cm.get('cookedData', {})
        self.cooked = cd
        self.lods = cd.get('renderLODs', [0.0])
        self.qs = [cd.get('quantizationScale', {}).get(k, 1.0) for k in 'XYZ']
        self.qo = [cd.get('quantizationOffset', {}).get(k, 0.0) for k in 'XYZ']
        self.ib_off = cd.get('indexBufferOffset', 0)
        self.chunks = [dict({'vertexType': 0, 'materialID': 0, 'numVertices': 0, 'numIndices': 0, 'firstVertex': 0, 'firstIndex': 0}, **c) for c in cm.get('chunks', [])]
        self.rchunks = render_chunks(cd.get('renderChunks', [0]))
        self.materials = []
        for h in cm.get('materials', []):
            m = f.handle(h)
            if isinstance(m, cr2w.Export):
                base, params = resolve_material(f.handle(m.get('baseMaterial')), material_params(f, m))
            else:
                base, params = resolve_material(m, {})
            self.materials.append({'base': base, 'params': params})
        bb = cm.get('boundingBox', {})
        self.box = ([bb.get('Min', {}).get(k, 0.0) for k in 'XYZ'], [bb.get('Max', {}).get(k, 0.0) for k in 'XYZ'])
        self._buf = None

    @property
    def buf(self):
        if self._buf is None:
            self._buf = bundles.read(self.name + '.1.buffer')
        return self._buf

    def chunk_lod(self, i):
        m = self.rchunks[i]['lod'] if i < len(self.rchunks) else 1
        k = 0
        while m and (not m & 1):
            m >>= 1
            k += 1
        return k

    def chunk(self, i):
        r = self.rchunks[i]
        nv, ni = (r['nv'], r['ni'])
        b = self.buf
        so = r['streams']
        stride = 8
        if i < len(self.chunks) and 'Skinned' in str(self.chunks[i].get('vertexType', '')):
            nb = int(self.chunks[i].get('numBonesPerVertex', 4) or 4)
            stride = 8 + 2 * nb
            if so[1] > so[0] and nv and ((so[1] - so[0]) % nv == 0) and ((so[1] - so[0]) // nv >= 8):
                stride = (so[1] - so[0]) // nv
        raw = np.frombuffer(b, dtype=np.uint8, count=nv * stride, offset=so[0]).reshape(nv, stride)
        p = raw[:, :8].copy().view('<u2').reshape(nv, 4)[:, :3].astype(np.float32)
        pos = np.asarray(self.qo, np.float32) + p / 65535.0 * np.asarray(self.qs, np.float32)
        uv = np.frombuffer(b, dtype='<f2', count=nv * 2, offset=so[1]).reshape(-1, 2).astype(np.float32) if so[1] or i == 0 else np.zeros((nv, 2), np.float32)
        nt = np.frombuffer(b, dtype='<u4', count=nv * 2, offset=so[2]).reshape(-1, 2)[:, 0]
        nrm = np.stack([nt >> s & 1023 for s in (0, 10, 20)], 1).astype(np.float32) / 1023.0 * 2 - 1
        idx = np.frombuffer(b, dtype='<u2', count=ni, offset=self.ib_off + r['index_offset']).astype(np.int32)
        return (pos, uv, nrm, idx.reshape(-1, 3))

    def lod_chunks(self, lod=0):
        return [i for i in range(len(self.chunks)) if self.chunk_lod(i) == lod]

def main(argv):
    m = Mesh(argv[0])
    print(m.name)
    print('  box', m.box, 'lods', m.lods, 'q', m.qs, m.qo)
    for i, c in enumerate(m.chunks):
        r = m.rchunks[i]
        print('  chunk %d mat %d v %d i %d lod %d | rc %s' % (i, c['materialID'], c['numVertices'], c['numIndices'], m.chunk_lod(i), r))
    for r in m.rchunks[len(m.chunks):]:
        print('  extra rc', r)
    for i, mt in enumerate(m.materials):
        print('  mat %d %s' % (i, mt['base']))
        for k, v in mt['params'].items():
            print('      %-22s %s' % (k, v))
    for i in range(len(m.chunks)):
        pos, uv, nrm, idx = m.chunk(i)
        print('  chunk %d: idx max %d / %d verts, |n| %.3f, uv %s..%s' % (i, idx.max(), len(pos), np.linalg.norm(nrm, axis=1).mean(), uv.min(0), uv.max(0)))
if __name__ == '__main__':
    main(sys.argv[1:])
