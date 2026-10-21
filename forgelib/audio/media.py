"""forgelib/audio/media.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import subprocess
import sys
import tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from codebooks import Bits, standard_to_aotuv
VGMSTREAM = os.path.join(HERE, 'tools', 'vgmstream', 'vgmstream-cli.exe')
_MAP = None

class UnsupportedWem(Exception):
    pass

def _map():
    global _MAP
    if _MAP is None:
        _MAP = standard_to_aotuv()[0]
    return _MAP

def riff_chunks(b):
    if b[:4] != b'RIFF' or b[8:12] != b'WAVE':
        raise UnsupportedWem('not a RIFF/WAVE file')
    pos, out = (12, [])
    while pos + 8 <= len(b):
        tag, size = (b[pos:pos + 4], struct.unpack_from('<I', b, pos + 4)[0])
        out.append((tag, b[pos + 8:pos + 8 + size]))
        pos += 8 + size + (size & 1)
    return out

def _write_bits(buf, bitpos, n, value):
    for i in range(n):
        byte, bit = divmod(bitpos + i, 8)
        if value >> i & 1:
            buf[byte] |= 1 << bit
        else:
            buf[byte] &= ~(1 << bit) & 255

def remap_setup(data, setup_off):
    m = _map()
    start = (setup_off + 2) * 8
    r = Bits(data, setup_off + 2)
    n = r.read(8) + 1
    ids = [r.read(10) for _ in range(n)]
    missing = [i for i in ids if i not in m]
    if missing:
        raise UnsupportedWem(f'codebooks not in the aoTuV library: {missing}')
    for k, i in enumerate(ids):
        _write_bits(data, start + 8 + 10 * k, 10, m[i])
    return ids

def setup_hash(setup):
    h = 2166136261
    for c in setup:
        h = h * 16777619 & 4294967295
        h ^= c
    return h

def convert(acr):
    cs = riff_chunks(acr)
    tags = [t for t, _ in cs]
    fmt = dict(cs).get(b'fmt ')
    vorb = dict(cs).get(b'vorb')
    if fmt is None or len(fmt) != 24 or vorb is None or (len(vorb) != 42):
        raise UnsupportedWem(f'header layout {[(t, len(c)) for t, c in cs]}')
    if struct.unpack_from('<H', fmt, 0)[0] != 65535 or struct.unpack_from('<H', fmt, 16)[0] != 6:
        raise UnsupportedWem('not Wwise Vorbis with a 6-byte fmt extension')
    setup_off, audio_off = struct.unpack_from('<II', vorb, 16)
    data = bytearray(dict(cs)[b'data'])
    remap_setup(data, setup_off)
    data = bytes(data)
    new_vorb = vorb[:36] + struct.pack('<I', setup_hash(data[setup_off + 2:audio_off])) + vorb[40:]
    new_fmt = fmt[:16] + struct.pack('<H', 48) + fmt[18:24] + new_vorb
    out = []
    for tag, body in cs:
        if tag == b'vorb':
            continue
        if tag == b'fmt ':
            body = new_fmt
        elif tag == b'data':
            body = data
        out.append((tag, body))
    riff = bytearray()
    for i, (tag, body) in enumerate(out):
        riff += tag + struct.pack('<I', len(body)) + body
        if len(body) & 1 and i < len(out) - 1:
            riff += b'\x00'
    assert tags.index(b'data') > tags.index(b'fmt ')
    return b'RIFF' + struct.pack('<I', len(riff) + 4) + b'WAVE' + bytes(riff)

def decode(wem_bytes):
    with tempfile.TemporaryDirectory() as d:
        src, dst = (os.path.join(d, 'in.wem'), os.path.join(d, 'out.wav'))
        open(src, 'wb').write(wem_bytes)
        r = subprocess.run([VGMSTREAM, '-o', dst, src], capture_output=True, text=True)
        if r.returncode or not os.path.exists(dst):
            raise RuntimeError(f'vgmstream failed: {r.stdout[-300:]} {r.stderr[-300:]}')
        wav = open(dst, 'rb').read()
    d = dict(riff_chunks(wav))
    return d[b'data']

def _check(limit=300):
    import random
    from akpk import packages, ACR_SOUND, AC3_SOUND
    from codebooks import library, setup_codebook_ids, STANDARD, AOTUV
    std, aot = (library(STANDARD), library(AOTUV))
    ac3 = {e.id: (p, e) for p in packages(AC3_SOUND) if 'sfx' in p.name for e in p.streams}
    entries = [(p, e) for p in packages(ACR_SOUND) if 'sfx' in p.name for e in p.streams]
    random.Random(1).shuffle(entries)
    n = {'structure ok': 0, 'structure BAD': 0, 'pcm same': 0, 'pcm DIFFERENT': 0, 'unsupported': 0, 'ac3 list same': 0, 'ac3 list differs': 0}
    for p, e in entries[:limit]:
        src = p.read(e)
        try:
            dst = convert(src)
        except UnsupportedWem as exc:
            n['unsupported'] += 1
            print(f'  {e.id:#010x} unsupported: {exc}')
            continue
        ids_src, ids_dst = (setup_codebook_ids(src), setup_codebook_ids(dst))
        good = len(ids_src) == len(ids_dst) and all((aot[b] == std[a] for a, b in zip(ids_src, ids_dst)))
        n['structure ok' if good else 'structure BAD'] += 1
        same = decode(src) == decode(dst)
        n['pcm same' if same else 'pcm DIFFERENT'] += 1
        if e.id in ac3:
            q, f = ac3[e.id]
            n['ac3 list same' if setup_codebook_ids(q.read(f, 4096)) == ids_dst else 'ac3 list differs'] += 1
    print(n)
    return not (n['structure BAD'] or n['pcm DIFFERENT'])
if __name__ == '__main__':
    if sys.argv[1:2] == ['--check']:
        sys.exit(0 if _check(int(sys.argv[2]) if len(sys.argv) > 2 else 300) else 1)
    src, dst = (sys.argv[1], sys.argv[2])
    open(dst, 'wb').write(convert(open(src, 'rb').read()))
