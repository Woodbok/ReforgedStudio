"""forgelib/witcher3/bundles.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import pickle
import struct
import sys
import zlib
from collections import Counter
W3_DIR = os.environ.get('W3_DIR', 'L:\\SteamLibrary\\steamapps\\common\\The Witcher 3')
CONTENT = os.path.join(W3_DIR, 'content', 'content0')
BUNDLES = os.path.join(CONTENT, 'bundles')
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'out', 'witcher3')
CACHE = os.path.join(OUT, 'cache')
ENTRY = 304

class Entry(tuple):
    __slots__ = ()
    bundle = property(lambda s: s[0])
    offset = property(lambda s: s[1])
    size = property(lambda s: s[2])
    zsize = property(lambda s: s[3])
    comp = property(lambda s: s[4])

def read_toc(path):
    with open(path, 'rb') as f:
        head = f.read(32)
        if head[:8] != b'POTATO70':
            raise ValueError('%s: not a POTATO70 bundle' % path)
        toc_size = struct.unpack_from('<I', head, 16)[0]
        toc = f.read(toc_size)
    out = []
    for i in range(0, toc_size - ENTRY + 1, ENTRY):
        name = toc[i:i + 256].split(b'\x00', 1)[0].decode('latin-1').lower()
        if not name:
            continue
        off, _z, size, zsize, crc, comp = struct.unpack_from('<6I', toc, i + 272)
        out.append((name, off, size, zsize, comp))
    return out

def _stamp():
    st = []
    for n in sorted(os.listdir(BUNDLES)):
        if n.endswith('.bundle'):
            s = os.stat(os.path.join(BUNDLES, n))
            st.append((n, s.st_size, int(s.st_mtime)))
    return st
_INDEX = None

def index():
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    stamp = _stamp()
    cp = os.path.join(CACHE, 'index.pkl')
    if os.path.exists(cp):
        with open(cp, 'rb') as f:
            d = pickle.load(f)
        if d.get('stamp') == stamp:
            _INDEX = {k: Entry(v) for k, v in d['index'].items()}
            return _INDEX
    idx = {}
    for bn, _s, _m in stamp:
        for name, off, size, zsize, comp in read_toc(os.path.join(BUNDLES, bn)):
            idx[name] = (bn, off, size, zsize, comp)
    os.makedirs(CACHE, exist_ok=True)
    with open(cp, 'wb') as f:
        pickle.dump({'stamp': stamp, 'index': idx}, f, protocol=4)
    _INDEX = {k: Entry(v) for k, v in idx.items()}
    return _INDEX
_FH = {}

def _fh(bundle):
    f = _FH.get(bundle)
    if f is None:
        f = _FH[bundle] = open(os.path.join(BUNDLES, bundle), 'rb')
    return f

def read(name):
    e = index()[name.lower().replace('/', '\\')]
    f = _fh(e.bundle)
    f.seek(e.offset)
    data = f.read(e.zsize)
    if e.comp == 0:
        return data[:e.size]
    if e.comp == 1:
        out = zlib.decompress(data)
        if len(out) != e.size:
            raise ValueError('%s: %d bytes, expected %d' % (name, len(out), e.size))
        return out
    raise ValueError('%s: compression %d not supported' % (name, e.comp))

def exists(name):
    return name.lower().replace('/', '\\') in index()

def find(sub, ext=None):
    sub = sub.lower().replace('/', '\\')
    return sorted((n for n in index() if sub in n and (ext is None or n.endswith(ext))))

def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--find')
    ap.add_argument('--ext')
    ap.add_argument('--get')
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    idx = index()
    if a.get:
        data = read(a.get)
        if a.out:
            with open(a.out, 'wb') as f:
                f.write(data)
        print(a.get, idx[a.get.lower()], len(data), data[:16])
        return
    if a.find is not None:
        for n in find(a.find, a.ext):
            e = idx[n]
            print('%-100s %-34s %10d' % (n, e.bundle, e.size))
        return
    per = Counter((e.bundle for e in idx.values()))
    for b, c in sorted(per.items()):
        print('%-40s %8d' % (b, c))
    ext = Counter((os.path.splitext(n)[1] for n in idx))
    print(len(idx), 'unique names')
    for x, c in ext.most_common(40):
        print('  %-14s %8d' % (x, c))
if __name__ == '__main__':
    main(sys.argv[1:])
