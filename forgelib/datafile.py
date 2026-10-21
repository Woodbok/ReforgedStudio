"""forgelib/datafile.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import ctypes
import os
import struct
import zlib
import lzo1x
MAGIC = 1476110899
VERSION = 268761753
CODEC_LZO1X_1 = 0
CODEC_LZO2A = 2
CHUNK_HEADER_SIZE = 15

class DatafileError(Exception):
    pass

def checksum(data):
    return zlib.adler32(data, 0)

def _checksum_py(data):
    a = b = 0
    for byte in data:
        a += byte
        if a >= 65521:
            a %= 65521
        b += a
        if b >= 65521:
            b %= 65521
    return b << 16 | a

def first_chunk_raw_size(payload):
    o = 4
    if len(payload) < o + CHUNK_HEADER_SIZE:
        raise DatafileError('payload too short for a chunk header')
    if struct.unpack_from('<I', payload, o)[0] != MAGIC:
        raise DatafileError('no chunk magic')
    q = o + CHUNK_HEADER_SIZE
    count = struct.unpack_from('<H', payload, q)[0]
    q += 2
    return sum((struct.unpack_from('<I', payload, q + i * 4)[0] & 65535 for i in range(count)))
_WINDOW = 8191
_CHAIN = 32

def lzo2a_compress(block):
    lib = _native()
    if lib is not None and len(block) <= 65535:
        data = bytes(block)
        cap = 2 * len(data) + 64
        buf = ctypes.create_string_buffer(cap)
        n = lib.ac3lzo_lzo2a_compress(data, len(data), buf, cap)
        if n >= 0:
            return buf.raw[:n]
    return _lzo2a_compress_py(block)

def _lzo2a_compress_py(block):
    data = bytes(block)
    n = len(data)
    out = bytearray()
    bit_bytes = []
    state = [0, 0]

    def put_bits(value, count):
        if state[0] < count:
            bit_bytes.append(len(out))
            out.append(0)
            state[0] += 8
        for _ in range(count):
            w = state[1]
            out[bit_bytes[w >> 3]] |= (value & 1) << (w & 7)
            value >>= 1
            state[1] += 1
        state[0] -= count

    def long_code(dist, length_class):
        put_bits(1, 1)
        put_bits(1, 1)
        out.append(dist & 31 | length_class << 5)
        out.append(dist >> 5)
    heads = {}
    last_pair = {}
    i = 0
    while i < n:
        best_len = best_dist = 0
        if i + 3 <= n:
            key = data[i:i + 3]
            chain = heads.get(key)
            if chain:
                limit = n - i
                for p in reversed(chain[-_CHAIN:]):
                    dist = i - p
                    if dist > _WINDOW:
                        break
                    if best_len and (best_len >= limit or data[p + best_len] != data[i + best_len]):
                        continue
                    length = 3
                    step = 64
                    while length < limit:
                        s = min(step, limit - length)
                        if data[p + length:p + length + s] == data[i + length:i + length + s]:
                            length += s
                        elif s > 1:
                            step = max(1, s // 4)
                        else:
                            break
                    if length > best_len:
                        best_len, best_dist = (length, dist)
                        if length >= limit:
                            break
                chain.append(i)
                if len(chain) > 4 * _CHAIN:
                    del chain[:-_CHAIN]
            else:
                heads[key] = [i]
        pair = data[i:i + 2]
        if best_len < 3 and len(pair) == 2:
            p = last_pair.get(pair)
            if p is not None and i - p <= 256:
                best_len, best_dist = (2, i - p)
        last_pair[pair] = i
        if best_len >= 2 and best_dist <= 256 and (best_len <= 5):
            put_bits(1, 1)
            put_bits(0, 1)
            put_bits(best_len - 2, 2)
            out.append(best_dist - 1)
        elif best_len >= 3:
            if best_len <= 9:
                long_code(best_dist, best_len - 2)
            else:
                long_code(best_dist, 0)
                extra = best_len - 9
                zeros = (extra - 1) // 255
                out += bytes(zeros)
                out.append(extra - 255 * zeros)
        else:
            put_bits(0, 1)
            out.append(data[i])
            i += 1
            continue
        for q in range(i + 1, min(i + best_len, i + 64, n - 2)):
            heads.setdefault(data[q:q + 3], []).append(q)
            last_pair[data[q:q + 2]] = q
        i += best_len
    long_code(0, 1)
    return bytes(out)
PACK = False
COMP_CACHE_MIN_BLOCK = 4096

def _lzo2a_compress_cached(block):
    import os
    if len(block) < COMP_CACHE_MIN_BLOCK or os.environ.get('AC3FORGE_NO_DECOMP_CACHE') or _native() is not None:
        return lzo2a_compress(block)
    import hashlib
    import zlib
    key = hashlib.sha1(block).hexdigest()
    path = os.path.join(COMP_CACHE, key[:2], key + '.bin')
    try:
        with open(path, 'rb') as fh:
            blob = fh.read()
        n, crc = struct.unpack_from('<II', blob, 0)
        body = blob[8:]
        if len(body) == n and zlib.crc32(body) == crc:
            _cache_touch(path)
            return body
    except (OSError, struct.error):
        pass
    body = lzo2a_compress(block)
    _cache_write(path, body, COMP_CACHE_CAP_GB)
    return body

def _cache_write(path, data, cap_gb):
    import os
    import zlib
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f'{path}.{os.getpid()}.tmp'
        with open(tmp, 'wb') as fh:
            fh.write(struct.pack('<II', len(data), zlib.crc32(data)))
            fh.write(data)
        os.replace(tmp, path)
    except OSError:
        return
    _cache_trim(os.path.dirname(path), cap_gb)
DECOMP_CACHE_CAP_GB = float(__import__('os').environ.get('AC3FORGE_CACHE_CAP_GB') or 25)
COMP_CACHE_CAP_GB = 5.0
CACHE_SHARDS = 256

def _cache_touch(path):
    import os
    try:
        os.utime(path, None)
    except OSError:
        pass

def _cache_trim(shard, cap_gb):
    import os
    import time
    limit = cap_gb * 1000000000.0 / CACHE_SHARDS
    entries, total, now = ([], 0, time.time())
    try:
        with os.scandir(shard) as it:
            for e in it:
                try:
                    st = e.stat()
                except OSError:
                    continue
                if e.name.endswith('.tmp'):
                    if now - st.st_mtime > 3600:
                        try:
                            os.remove(e.path)
                        except OSError:
                            pass
                    continue
                entries.append((st.st_mtime, st.st_size, e.path))
                total += st.st_size
    except OSError:
        return
    if total <= limit:
        return
    entries.sort()
    for _mtime, size, p in entries[:-1]:
        if total <= limit:
            break
        try:
            os.remove(p)
            total -= size
        except OSError:
            pass

def _emit_chunk(data, block_size, pack=False):
    blocks = [data[i:i + block_size] for i in range(0, len(data), block_size)]
    if not blocks:
        blocks = [b'']
    bodies = []
    for block in blocks:
        body = _lzo2a_compress_cached(block) if pack and block else b''
        bodies.append(body if body and len(body) < len(block) else bytes(block))
    out = bytearray()
    out += struct.pack('<I', MAGIC)
    out += struct.pack('<I', VERSION)
    out += struct.pack('<H', 1)
    out += bytes([CODEC_LZO2A])
    out += struct.pack('<I', block_size | 2147483648)
    out += struct.pack('<H', len(blocks))
    for block, body in zip(blocks, bodies):
        out += struct.pack('<I', len(block) | len(body) << 16)
    for block, body in zip(blocks, bodies):
        out += struct.pack('<I', checksum(body))
        out += body
    return bytes(out)

def compress(data, block_size=32768, first_chunk=None, pack=None):
    if not 0 < block_size <= 65535:
        raise DatafileError(f'block size {block_size} must be 1..65535')
    if first_chunk is None:
        first_chunk = len(data)
    if not 0 <= first_chunk <= len(data):
        raise DatafileError(f'first chunk of {first_chunk} does not fit {len(data)} bytes')
    if pack is None:
        pack = PACK
    out = bytearray()
    out += struct.pack('<I', 0)
    out += _emit_chunk(data[:first_chunk], block_size, pack)
    if first_chunk < len(data):
        out += _emit_chunk(data[first_chunk:], block_size, pack)
    return bytes(out)

def _lzo2a(data, expected_size, window):
    ip, ip_end = (0, len(data))
    out = window
    start = len(out)
    bit_buf = bit_count = 0

    def need_bits(j):
        nonlocal ip, bit_buf, bit_count
        if bit_count < j:
            if ip >= ip_end:
                raise DatafileError('input overrun refilling bit buffer')
            bit_buf |= data[ip] << bit_count
            ip += 1
            bit_count += 8

    def copy_match(m_pos, length):
        if m_pos < 0 or m_pos >= len(out):
            raise DatafileError('lookbehind overrun')
        if len(out) - start + length > expected_size:
            raise DatafileError('output overrun')
        for _ in range(length):
            out.append(out[m_pos])
            m_pos += 1
    while ip < ip_end:
        need_bits(1)
        if bit_buf & 1 == 0:
            bit_buf >>= 1
            bit_count -= 1
            if ip >= ip_end:
                raise DatafileError('input overrun on literal')
            out.append(data[ip])
            ip += 1
            continue
        bit_buf >>= 1
        bit_count -= 1
        need_bits(1)
        if bit_buf & 1 == 0:
            bit_buf >>= 1
            bit_count -= 1
            need_bits(2)
            length = 2 + (bit_buf & 3)
            bit_buf >>= 2
            bit_count -= 2
            if ip >= ip_end:
                raise DatafileError('input overrun on M1 offset')
            offset = data[ip]
            ip += 1
            copy_match(len(out) - 1 - offset, length)
            continue
        bit_buf >>= 1
        bit_count -= 1
        if ip_end - ip < 2:
            raise DatafileError('input overrun on match header')
        byte1, byte2 = (data[ip], data[ip + 1])
        ip += 2
        m_pos = len(out) - (byte1 & 31 | byte2 << 5)
        length_class = byte1 >> 5
        if length_class == 0:
            length = 9
            while True:
                if ip >= ip_end:
                    raise DatafileError('input overrun on extended length')
                if data[ip] != 0:
                    break
                length += 255
                ip += 1
            length += data[ip]
            ip += 1
        else:
            if m_pos == len(out):
                return ip
            length = length_class + 2
        copy_match(m_pos, length)
    raise DatafileError('no end-of-stream marker')

def _lzo2a_fast(data, expected_size, window):
    ip, ip_end = (0, len(data))
    out = window
    start = len(out)
    extend = out.extend
    bb = bc = 0

    def copy(m_pos, length):
        n = len(out)
        if m_pos < 0 or m_pos >= n:
            raise DatafileError('lookbehind overrun')
        if n - start + length > expected_size:
            raise DatafileError('output overrun')
        if m_pos + length <= n:
            extend(out[m_pos:m_pos + length])
        else:
            pat = out[m_pos:n]
            extend((pat * (length // len(pat) + 1))[:length])
    while ip < ip_end:
        if bc == 0:
            bb = data[ip]
            ip += 1
            bc = 8
        if bb & 1 == 0:
            k = (bb & -bb).bit_length() - 1 if bb else bc
            if k > bc:
                k = bc
            if ip + k > ip_end:
                extend(data[ip:ip_end])
                raise DatafileError('input overrun on literal')
            extend(data[ip:ip + k])
            ip += k
            bb >>= k
            bc -= k
            continue
        bb >>= 1
        bc -= 1
        if bc == 0:
            if ip >= ip_end:
                raise DatafileError('input overrun refilling bit buffer')
            bb = data[ip]
            ip += 1
            bc = 8
        if bb & 1 == 0:
            bb >>= 1
            bc -= 1
            if bc < 2:
                if ip >= ip_end:
                    raise DatafileError('input overrun refilling bit buffer')
                bb |= data[ip] << bc
                ip += 1
                bc += 8
            length = 2 + (bb & 3)
            bb >>= 2
            bc -= 2
            if ip >= ip_end:
                raise DatafileError('input overrun on M1 offset')
            offset = data[ip]
            ip += 1
            copy(len(out) - 1 - offset, length)
            continue
        bb >>= 1
        bc -= 1
        if ip_end - ip < 2:
            raise DatafileError('input overrun on match header')
        byte1, byte2 = (data[ip], data[ip + 1])
        ip += 2
        m_pos = len(out) - (byte1 & 31 | byte2 << 5)
        length_class = byte1 >> 5
        if length_class == 0:
            length = 9
            while True:
                if ip >= ip_end:
                    raise DatafileError('input overrun on extended length')
                if data[ip] != 0:
                    break
                length += 255
                ip += 1
            length += data[ip]
            ip += 1
        else:
            if m_pos == len(out):
                return ip
            length = length_class + 2
        copy(m_pos, length)
    raise DatafileError('no end-of-stream marker')
LZO2A = _lzo2a_fast
NATIVE_DLL = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'native', 'ac3lzo.dll')
_NATIVE = None

def _native():
    global _NATIVE
    if _NATIVE is None:
        _NATIVE = False
        if not os.environ.get('AC3FORGE_NO_NATIVE') and os.path.exists(NATIVE_DLL):
            try:
                lib = ctypes.CDLL(NATIVE_DLL)
                if lib.ac3lzo_version() == 1:
                    i64, p = (ctypes.c_int64, ctypes.c_void_p)
                    lib.ac3lzo_payload_size.argtypes = (ctypes.c_char_p, i64)
                    lib.ac3lzo_payload_size.restype = i64
                    lib.ac3lzo_payload_decode.argtypes = (ctypes.c_char_p, i64, p, i64, ctypes.POINTER(i64))
                    lib.ac3lzo_payload_decode.restype = ctypes.c_int
                    lib.ac3lzo_lzo2a_compress.argtypes = (ctypes.c_char_p, i64, p, i64)
                    lib.ac3lzo_lzo2a_compress.restype = i64
                    _NATIVE = lib
            except (OSError, AttributeError):
                pass
    return _NATIVE or None
DECOMP_CACHE = __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), 'out', 'cache', 'decomp')
COMP_CACHE = __import__('os').path.join(__import__('os').path.dirname(DECOMP_CACHE), 'comp')
CACHE_MIN_PAYLOAD = 16 * 1024
CACHE_MIN_SECONDS = 0.05

def decompress(payload):
    if len(payload) < CACHE_MIN_PAYLOAD or os.environ.get('AC3FORGE_NO_DECOMP_CACHE') or _native() is not None:
        return _decompress_uncached(payload)
    import hashlib
    import time
    import zlib
    key = hashlib.sha1(payload).hexdigest()
    path = os.path.join(DECOMP_CACHE, key[:2], key + '.bin')
    try:
        with open(path, 'rb') as fh:
            blob = fh.read()
        n, crc = struct.unpack_from('<II', blob, 0)
        data = blob[8:]
        if len(data) == n and zlib.crc32(data) == crc:
            _cache_touch(path)
            return data
    except (OSError, struct.error):
        pass
    t0 = time.perf_counter()
    data = _decompress_uncached(payload)
    if time.perf_counter() - t0 >= CACHE_MIN_SECONDS:
        _cache_write(path, data, DECOMP_CACHE_CAP_GB)
    return data

def _decompress_uncached(payload):
    lib = _native()
    if lib is not None:
        data = _decompress_native(lib, payload)
        if data is not None:
            return data
    return _decompress_python(payload)

def _decompress_native(lib, payload):
    if not isinstance(payload, bytes):
        payload = bytes(payload)
    n = lib.ac3lzo_payload_size(payload, len(payload))
    if n <= 0:
        return None
    buf = bytearray(n)
    got = ctypes.c_int64(0)
    rc = lib.ac3lzo_payload_decode(payload, len(payload), (ctypes.c_char * n).from_buffer(buf), n, ctypes.byref(got))
    if rc or got.value != n:
        return None
    return bytes(buf)

def _decompress_python(payload):
    out = bytearray()
    o = 4
    while o + CHUNK_HEADER_SIZE <= len(payload):
        magic, version = struct.unpack_from('<II', payload, o)
        if magic != MAGIC:
            raise DatafileError(f'bad magic 0x{magic:08X} at {o}')
        if version != VERSION:
            raise DatafileError(f'bad version 0x{version:08X} at {o}')
        codec = payload[o + 10]
        if codec not in (CODEC_LZO2A, CODEC_LZO1X_1):
            raise DatafileError(f'codec {codec} at {o}, only LZO2A and LZO1X are supported')
        block_size = struct.unpack_from('<I', payload, o + 11)[0]
        q = o + CHUNK_HEADER_SIZE
        if not block_size & 2147483648:
            raise DatafileError(f'chunk at {o} has no block table')
        count = struct.unpack_from('<H', payload, q)[0]
        q += 2
        blocks = []
        for i in range(count):
            v = struct.unpack_from('<I', payload, q + i * 4)[0]
            blocks.append((v & 65535, v >> 16))
        q += count * 4
        for raw, comp in blocks:
            q += 4
            if raw == comp:
                out += payload[q:q + comp]
            elif codec == CODEC_LZO2A:
                consumed = LZO2A(payload[q:q + comp], raw, out)
                if consumed != comp:
                    raise DatafileError(f'block at {q} consumed {consumed} of {comp} bytes')
            else:
                decoded, consumed = lzo1x.decompress(payload[q:q + comp], raw)
                if consumed != comp:
                    raise DatafileError(f'block at {q} consumed {consumed} of {comp} bytes')
                out += decoded
            q += comp
        o = q
    return bytes(out)

def _check_lzo(archives):
    import sys
    import time
    from forge import Forge
    global LZO2A
    ref = archives[:1] == ['--ref']
    archives = archives[1:] if ref else archives
    lib = _native()
    print(f"native: {(NATIVE_DLL if lib is not None else 'not loaded - comparing the Python with itself')}")
    bad = 0
    for path in archives:
        n = native = t_new = t_py = t_ref = 0
        with Forge(path) as f:
            for r in f.records:
                raw = f.read(r)
                res = []
                for kind in ('new', 'py') + (('ref',) if ref else ()):
                    LZO2A = _lzo2a if kind == 'ref' else _lzo2a_fast
                    t0 = time.perf_counter()
                    try:
                        if kind == 'new':
                            got = _decompress_native(lib, raw) if lib is not None else None
                            native += got is not None
                            res.append(('ok', got if got is not None else _decompress_python(raw)))
                        else:
                            res.append(('ok', _decompress_python(raw)))
                    except Exception as e:
                        res.append(('err', f'{type(e).__name__}: {e}'))
                    dt = time.perf_counter() - t0
                    if kind == 'new':
                        t_new += dt
                    elif kind == 'py':
                        t_py += dt
                    else:
                        t_ref += dt
                LZO2A = _lzo2a_fast
                n += 1
                if any((x != res[0] for x in res[1:])):
                    bad += 1
                    print(f"  DIFFERS: {path} {r.name}: {' / '.join((x[0] for x in res))}")
        print(f'{path}: {n} records, identical {n - bad} (native decoded {native}, Python fallback {n - native}); native {t_new:.1f} s, Python {t_py:.0f} s' + (f', original {t_ref:.0f} s' if ref else ''), flush=True)
    sys.exit(1 if bad else 0)

def _check_pack(archives):
    import sys
    import time
    from forge import Forge
    every = 1
    if archives[:1] == ['--every']:
        every, archives = (int(archives[1]), archives[2:])
    lib = _native()
    print(f"native: {(NATIVE_DLL if lib is not None else 'not loaded - comparing the Python with itself')}")
    bad = 0
    for path in archives:
        n = blocks = 0
        t_new = t_py = 0.0
        with Forge(path) as f:
            for k, r in enumerate(f.records):
                if k % every:
                    continue
                try:
                    payload = f.read(r)
                    data = decompress(payload)
                    first = first_chunk_raw_size(payload)
                except Exception:
                    continue
                n += 1
                for part in (data[:first], data[first:]):
                    for i in range(0, len(part), 32768):
                        block = part[i:i + 32768]
                        blocks += 1
                        t0 = time.perf_counter()
                        a = lzo2a_compress(block)
                        t1 = time.perf_counter()
                        b = _lzo2a_compress_py(block)
                        t2 = time.perf_counter()
                        t_new += t1 - t0
                        t_py += t2 - t1
                        if a != b or checksum(block) != _checksum_py(block) or checksum(a) != _checksum_py(a):
                            bad += 1
                            print(f'  DIFFERS: {path} {r.name} block at {i}')
        print(f'{path}: {n} records, {blocks} blocks, identical {blocks - bad}; native {t_new:.1f} s, Python {t_py:.0f} s', flush=True)
    sys.exit(1 if bad else 0)
if __name__ == '__main__':
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == '--check-lzo':
        _check_lzo(sys.argv[2:])
    if len(sys.argv) > 2 and sys.argv[1] == '--check-pack':
        _check_pack(sys.argv[2:])

def _check_cache(archives):
    import sys
    import time
    from forge import Forge
    bad = 0
    for path in archives:
        n, t_cached, t_fresh = (0, 0.0, 0.0)
        with Forge(path) as f:
            for r in f.records:
                raw = f.read(r)
                t0 = time.time()
                try:
                    a = ('ok', decompress(raw))
                except Exception as e:
                    a = ('err', str(e))
                t1 = time.time()
                try:
                    b = ('ok', _decompress_uncached(raw))
                except Exception as e:
                    b = ('err', str(e))
                t2 = time.time()
                t_cached += t1 - t0
                t_fresh += t2 - t1
                n += 1
                if a != b:
                    bad += 1
                    print(f'  DIFFERS: {path} {r.name}')
        print(f'{path}: {n} records, identical {n - bad}; decompress() {t_cached:.0f} s, fresh decode {t_fresh:.0f} s', flush=True)
    sys.exit(1 if bad else 0)
if __name__ == '__main__' and len(__import__('sys').argv) > 2 and (__import__('sys').argv[1] == '--check-cache'):
    _check_cache(__import__('sys').argv[2:])
