"""forgelib/witcher3/cr2w.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
import sys

class Export:
    __slots__ = ('index', 'cls', 'flags', 'parent', 'size', 'offset', 'props', 'tail', 'tail_off', 'file')

    def __repr__(self):
        return '<#%d %s>' % (self.index, self.cls)

    def get(self, k, d=None):
        return self.props.get(k, d) if self.props is not None else d

    def __getitem__(self, k):
        return self.props[k]

class Handle(int):
    pass

class Raw(bytes):
    pass

def read_cstring(b, o):
    c = b[o]
    o += 1
    ascii_ = bool(c & 128)
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
    if ascii_:
        return (b[o:o + n].decode('latin-1'), o + n)
    return (b[o:o + 2 * n].decode('utf-16-le', 'replace'), o + 2 * n)
SIMPLE = {'Float': ('<f', 4), 'Double': ('<d', 8), 'Uint8': ('<B', 1), 'Int8': ('<b', 1), 'Uint16': ('<H', 2), 'Int16': ('<h', 2), 'Uint32': ('<I', 4), 'Int32': ('<i', 4), 'Uint64': ('<Q', 8), 'Int64': ('<q', 8), 'Bool': ('<?', 1), 'GameTime': ('<I', 4), 'LocalizedString': ('<I', 4), 'EngineQsTransform': None}

class CR2W:

    def __init__(self, data, name=''):
        self.data = b = data
        self.name = name
        if b[:4] != b'CR2W':
            raise ValueError('%s: not CR2W' % name)
        self.version, self.flags, self.timestamp, self.build, self.fsize, self.bsize, self.crc, self.nchunks = struct.unpack_from('<IIQIIIII', b, 4)
        self.tables = [struct.unpack_from('<3I', b, 40 + 12 * i) for i in range(10)]
        so, sn, _ = self.tables[0]
        self.strings = b[so:so + sn]
        no, nn, _ = self.tables[1]
        self.names = [self.str_at(struct.unpack_from('<I', b, no + 8 * i)[0]) for i in range(nn)]
        io, inn, _ = self.tables[2]
        self.imports = []
        for i in range(inn):
            p, c, f = struct.unpack_from('<IHH', b, io + 8 * i)
            self.imports.append((self.str_at(p), self.names[c], f))
        eo, en, _ = self.tables[4]
        self.exports = []
        for i in range(en):
            c, f, par, sz, off, tpl, crc = struct.unpack_from('<HHIIIII', b, eo + 24 * i)
            e = Export()
            e.index, e.cls, e.flags, e.parent, e.size, e.offset = (i + 1, self.names[c], f, par, sz, off)
            e.props = None
            e.tail = None
            e.tail_off = None
            e.file = self
            self.exports.append(e)
        bo, bn, _ = self.tables[5]
        self.buffers = [struct.unpack_from('<6I', b, bo + 24 * i) for i in range(bn)]
        mo, mn, _ = self.tables[6]
        self.embedded = []
        for i in range(mn):
            imp, path, ph, off, sz = struct.unpack_from('<IIQII', b, mo + 24 * i)
            self.embedded.append((imp, self.str_at(path), ph, off, sz))

    def str_at(self, o):
        e = self.strings.index(b'\x00', o)
        return self.strings[o:e].decode('latin-1')

    def parse(self, e):
        if e.props is None:
            b = self.data
            o = e.offset
            end = e.offset + e.size
            if o < end and b[o] == 0:
                o += 1
            props = {}
            try:
                o = self._props(b, o, end, props)
            except Exception as ex:
                props['__error__'] = repr(ex)
            e.props = props
            e.tail_off = o
            e.tail = b[o:end]
        return e

    def _props(self, b, o, end, out):
        while o + 2 <= end:
            ni = struct.unpack_from('<H', b, o)[0]
            if ni == 0:
                return o + 2
            ti, sz = struct.unpack_from('<HI', b, o + 2)
            name, typ = (self.names[ni], self.names[ti])
            vo = o + 8
            ve = o + 4 + sz
            try:
                out[name] = self.value(typ, b, vo, ve)
            except Exception as ex:
                out[name] = Raw(b[vo:ve])
            o = ve
        return o

    def value(self, typ, b, o, end):
        s = SIMPLE.get(typ)
        if s:
            return struct.unpack_from(s[0], b, o)[0]
        if typ == 'CName':
            return self.names[struct.unpack_from('<H', b, o)[0]]
        if typ == 'String' or typ == 'StringAnsi':
            return read_cstring(b, o)[0]
        if typ.startswith(('handle:', 'ptr:')):
            return Handle(struct.unpack_from('<i', b, o)[0])
        if typ.startswith('soft:'):
            i = struct.unpack_from('<H', b, o)[0]
            return self.imports[i - 1][0] if i else None
        if typ == 'CGUID':
            return b[o:o + 16].hex()
        if typ == 'EngineTransform':
            return read_engine_transform(b, o)
        if typ == 'TagList':
            n = b[o]
            return [self.names[struct.unpack_from('<H', b, o + 1 + 2 * i)[0]] for i in range(n)]
        if typ.startswith('array:'):
            inner = typ.split(',', 2)[2]
            n = struct.unpack_from('<I', b, o)[0]
            return self._array(inner, n, b, o + 4, end)
        if typ.startswith('['):
            k = typ.index(']')
            inner = typ[k + 1:]
            n = struct.unpack_from('<I', b, o)[0]
            return self._array(inner, n, b, o + 4, end)
        if typ in ('DataBuffer', 'SharedDataBuffer', 'DeferredDataBuffer', 'LongBitField', 'CDateTime', 'IdTag', 'EntityHandle', 'CVariant'):
            return Raw(b[o:end])
        if end - o == 2:
            return self.names[struct.unpack_from('<H', b, o)[0]]
        d = {}
        if b[o] == 0:
            o += 1
        self._props(b, o, end, d)
        return d

    def _array(self, inner, n, b, o, end):
        s = SIMPLE.get(inner)
        if s:
            return list(struct.unpack_from('<%d%s' % (n, s[0][1]), b, o))
        if inner == 'CName':
            return [self.names[x] for x in struct.unpack_from('<%dH' % n, b, o)]
        if inner.startswith(('handle:', 'ptr:')):
            return [Handle(x) for x in struct.unpack_from('<%di' % n, b, o)]
        if inner == 'String':
            r = []
            for _ in range(n):
                v, o = read_cstring(b, o)
                r.append(v)
            return r
        r = []
        for _ in range(n):
            d = {}
            if b[o] == 0:
                o += 1
            o = self._props(b, o, end, d)
            r.append(d)
        return r

    def handle(self, h):
        if not h:
            return None
        if h > 0:
            return self.parse(self.exports[h - 1])
        return self.imports[-h - 1][0]

    def by_class(self, cls):
        return [self.parse(e) for e in self.exports if e.cls == cls]

def read_engine_transform(b, o):
    f = b[o]
    o += 1
    pos, rot, scl = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    if f & 1:
        pos = struct.unpack_from('<3f', b, o)
        o += 12
    if f & 2:
        rot = struct.unpack_from('<3f', b, o)
        o += 12
    if f & 4:
        scl = struct.unpack_from('<3f', b, o)
        o += 12
    return {'pos': pos, 'rot': rot, 'scale': scl}

def load(name):
    from witcher3 import bundles
    return CR2W(bundles.read(name), name)

def dump(f, e, depth=3):
    f.parse(e)
    print('#%d %s parent %d size %d tail %d' % (e.index, e.cls, e.parent, e.size, len(e.tail)))
    for k, v in e.props.items():
        s = repr(v)
        print('   %-30s %s' % (k, s if len(s) < 300 else s[:300] + '...'))

def main(argv):
    import argparse
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap = argparse.ArgumentParser()
    ap.add_argument('name')
    ap.add_argument('--export', type=int)
    ap.add_argument('--cls')
    a = ap.parse_args(argv)
    f = load(a.name)
    print('version %d, %d names, %d imports, %d exports, %d buffers, %d embedded' % (f.version, len(f.names), len(f.imports), len(f.exports), len(f.buffers), len(f.embedded)))
    for i, imp in enumerate(f.imports[:60]):
        print('  import -%d %s %s' % (i + 1, imp[1], imp[0]))
    for b in f.buffers:
        print('  buffer', b)
    for m in f.embedded:
        print('  embedded', m)
    for e in f.exports:
        if a.export and e.index != a.export:
            continue
        if a.cls and e.cls != a.cls:
            continue
        dump(f, e)
if __name__ == '__main__':
    main(sys.argv[1:])
