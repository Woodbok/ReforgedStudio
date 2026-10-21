"""forgelib/record_cache.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import hashlib
import os
import pickle
import sys
import time
HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, 'out', 'clone', 'records')

def digest(raw):
    return hashlib.sha1(raw).digest()[:16]

class Store:

    def __init__(self, kind):
        self.path = os.path.join(DIR, f'{kind}.pkl')
        self.data, self.dirty = ({}, False)
        if os.path.exists(self.path):
            try:
                with open(self.path, 'rb') as fh:
                    self.data = pickle.load(fh)
            except Exception:
                self.data = {}

    def __contains__(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def put(self, key, value):
        self.data[key] = value
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        os.makedirs(DIR, exist_ok=True)
        tmp = f'{self.path}.{os.getpid()}.tmp'
        with open(tmp, 'wb') as fh:
            pickle.dump(self.data, fh, protocol=pickle.HIGHEST_PROTOCOL)
        for attempt in range(120):
            try:
                os.replace(tmp, self.path)
                break
            except PermissionError:
                time.sleep(0.5)
        else:
            try:
                os.remove(tmp)
            except OSError:
                pass
            sys.stderr.write(f'record_cache: {self.path} stayed busy for a minute - not saved this time\n')
        self.dirty = False

def main(argv):
    import time
    import clone
    if not argv or argv[0] != '--check':
        print(__doc__)
        return 0
    archive, ids = (argv[1], {int(x, 16) for x in argv[2:]} or {881535190})
    ok = True
    for name, new, old in (('definitions_index', lambda: clone._definitions_index_records(archive), lambda: clone._definitions_index_full(archive)), ('nested_index', lambda: clone._nested_index_records(archive, ids), lambda: clone._nested_index_full(archive, ids))):
        t0 = time.time()
        a = new()
        t1 = time.time()
        b = old()
        t2 = time.time()
        same = a == b
        ok &= same
        print(f'{name}: per-record {t1 - t0:.0f} s, full scan {t2 - t1:.0f} s, identical {same} ({len(a)} keys)')
    return 0 if ok else 1
if __name__ == '__main__':
    sys.path.insert(0, HERE)
    sys.exit(main(sys.argv[1:]))
