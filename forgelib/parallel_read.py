"""forgelib/parallel_read.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import pickle
import subprocess
import sys
import threading
HERE = os.path.dirname(os.path.abspath(__file__))
MIN_BYTES = 256 << 20
CHUNK_BYTES = 32 << 20

def workers():
    v = os.environ.get('AC3FORGE_WORKERS')
    if v is not None and v.strip():
        return max(0, int(v))
    return max(1, min(6, (os.cpu_count() or 2) - 2))

def map_records(archive, records, func, extras=None, min_bytes=MIN_BYTES):
    records = list(records)
    extras = [()] * len(records) if extras is None else [tuple(x) for x in extras]
    n = workers()
    total = sum((r.size for r in records))
    mod = getattr(func, '__module__', None)
    if n <= 1 or total < min_bytes or len(records) < 2 or (not mod) or (mod == '__main__'):
        return _local(archive, records, func, extras)
    chunk = min(CHUNK_BYTES, max(1 << 20, total // (8 * n)))
    chunks, cur, size = ([], [], 0)
    for r, x in zip(records, extras):
        cur.append((r, x))
        size += r.size
        if size >= chunk:
            chunks.append(cur)
            cur, size = ([], 0)
    if cur:
        chunks.append(cur)
    n = min(n, len(chunks))
    try:
        procs = [_start() for _ in range(n)]
    except OSError:
        return _local(archive, records, func, extras)
    results = [None] * len(chunks)
    errors = []
    lock = threading.Lock()
    todo = list(range(len(chunks)))[::-1]
    head = (list(sys.path), mod, func.__qualname__, os.path.abspath(archive))

    def feed(p):
        try:
            _send(p.stdin, ('init', head))
            while True:
                with lock:
                    if not todo or errors:
                        break
                    k = todo.pop()
                _send(p.stdin, ('chunk', chunks[k]))
                kind, value = pickle.load(p.stdout)
                if kind != 'ok':
                    raise RuntimeError(f'parallel_read worker: {value}')
                results[k] = value
        except Exception as e:
            with lock:
                errors.append(e)
        finally:
            try:
                _send(p.stdin, ('stop', None))
                p.stdin.close()
            except OSError:
                pass
    threads = [threading.Thread(target=feed, args=(p,), daemon=True) for p in procs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for p in procs:
        try:
            p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
    if errors:
        raise errors[0]
    return [x for part in results for x in part]

def _local(archive, records, func, extras):
    from forge import Forge
    with Forge(archive) as f:
        return [func(f.read(r), *x) for r, x in zip(records, extras)]

def _start():
    return subprocess.Popen([sys.executable, os.path.abspath(__file__), '--worker'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=os.getcwd())

def _send(fh, obj):
    pickle.dump(obj, fh, protocol=pickle.HIGHEST_PROTOCOL)
    fh.flush()

def _worker():
    import importlib
    inp, out = (sys.stdin.buffer, sys.stdout.buffer)
    sys.stdout = sys.stderr
    func = fh = init_error = None
    while True:
        try:
            kind, value = pickle.load(inp)
        except EOFError:
            break
        if kind == 'stop':
            break
        try:
            if kind == 'init':
                init_error = 'no init'
                path, mod, qual, archive = value
                sys.path[:] = path
                obj = importlib.import_module(mod)
                for part in qual.split('.'):
                    obj = getattr(obj, part)
                func = obj
                if fh is not None:
                    fh.close()
                fh = open(archive, 'rb')
                init_error = None
                continue
            if init_error:
                raise RuntimeError(init_error)
            res = [func(_read(fh, r), *x) for r, x in value]
            pickle.dump(('ok', res), out, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as e:
            import traceback
            if kind == 'init':
                init_error = f'{type(e).__name__}: {e}\n{traceback.format_exc()}'
                continue
            pickle.dump(('err', f'{type(e).__name__}: {e}\n{traceback.format_exc()}'), out)
        out.flush()

def _read(fh, record):
    from forge import PAYLOAD_HEADER_SIZE, UnsupportedForge
    fh.seek(record.offset)
    head = fh.read(PAYLOAD_HEADER_SIZE)
    if head[:8] != b'FILEDATA':
        raise UnsupportedForge(f'no FILEDATA header at 0x{record.offset:X} for {record.index} ({record.name!r})')
    return fh.read(record.size)
if __name__ == '__main__' and sys.argv[1:] == ['--worker']:
    sys.path.insert(0, HERE)
    _worker()
