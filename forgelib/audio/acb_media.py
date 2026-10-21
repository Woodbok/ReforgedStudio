"""forgelib/audio/acb_media.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import media
from media import UnsupportedWem, riff_chunks

class _Bits:

    def __init__(self, b):
        self.v, self.n, self.pos = (int.from_bytes(b, 'little'), len(b) * 8, 0)

    def read(self, n):
        if self.pos + n > self.n:
            raise UnsupportedWem('setup packet ends early')
        x = self.v >> self.pos & (1 << n) - 1
        self.pos += n
        return x

def _ilog(v):
    r = 0
    while v:
        r += 1
        v >>= 1
    return r

def setup_modes(payload, channels):
    r = _Bits(payload)
    ncb = r.read(8) + 1
    for _ in range(ncb):
        r.read(10)
    nfloor = r.read(6) + 1
    for _ in range(nfloor):
        parts = r.read(5)
        cls = [r.read(4) for _ in range(parts)]
        dims = []
        for _j in range(max(cls) + 1 if cls else 0):
            dims.append(r.read(3) + 1)
            sub = r.read(2)
            if sub:
                r.read(8)
            for _k in range(1 << sub):
                r.read(8)
        r.read(2)
        rb = r.read(4)
        for c in cls:
            for _k in range(dims[c]):
                r.read(rb)
    nres = r.read(6) + 1
    for _ in range(nres):
        r.read(2)
        (r.read(24), r.read(24), r.read(24))
        ncls = r.read(6) + 1
        r.read(8)
        casc = []
        for _j in range(ncls):
            low = r.read(3)
            high = r.read(5) if r.read(1) else 0
            casc.append(high * 8 + low)
        for c in casc:
            for k in range(8):
                if c & 1 << k:
                    r.read(8)
    nmap = r.read(6) + 1
    for _ in range(nmap):
        submaps = r.read(4) + 1 if r.read(1) else 1
        if r.read(1):
            steps = r.read(8) + 1
            for _j in range(steps):
                (r.read(_ilog(channels - 1)), r.read(_ilog(channels - 1)))
        if r.read(2):
            raise UnsupportedWem('mapping reserved field nonzero')
        if submaps > 1:
            for _j in range(channels):
                r.read(4)
        for _j in range(submaps):
            (r.read(8), r.read(8), r.read(8))
    nmode = r.read(6) + 1
    flags = []
    for _ in range(nmode):
        flags.append(r.read(1))
        r.read(8)
    if (r.pos + 7) // 8 != len(payload):
        raise UnsupportedWem(f'setup walk read {(r.pos + 7) // 8} of {len(payload)} bytes')
    return (flags, _ilog(nmode - 1))

def standard_to_modified(pkt, flags, mode_bits):
    x = int.from_bytes(pkt, 'little')
    if x & 1:
        raise UnsupportedWem('not an audio packet')
    y = x >> 1
    mode = y & (1 << mode_bits) - 1
    if mode >= len(flags):
        raise UnsupportedWem('mode number out of range')
    if flags[mode]:
        y = y & (1 << mode_bits) - 1 | y >> mode_bits + 2 << mode_bits
    return y.to_bytes(len(pkt), 'little')

def packets(data, off):
    out = []
    while off < len(data):
        if off + 6 > len(data):
            raise UnsupportedWem('packet header truncated')
        size, granule = struct.unpack_from('<HI', data, off)
        if off + 6 + size > len(data):
            raise UnsupportedWem('packet truncated')
        out.append((data[off + 6:off + 6 + size], granule))
        off += 6 + size
    return out

def to_acr(acb):
    cs = riff_chunks(acb)
    d = dict(cs)
    fmt, vorb, data = (d.get(b'fmt '), d.get(b'vorb'), d.get(b'data'))
    if fmt is None or len(fmt) != 24 or vorb is None or (len(vorb) != 52) or (data is None):
        raise UnsupportedWem(f'header layout {[(t, len(c)) for t, c in cs]}')
    if struct.unpack_from('<H', fmt, 0)[0] != 65535 or struct.unpack_from('<H', fmt, 16)[0] != 6:
        raise UnsupportedWem('not Wwise Vorbis with a 6-byte fmt extension')
    channels = struct.unpack_from('<H', fmt, 2)[0]
    samples = struct.unpack_from('<I', vorb, 0)[0]
    seek, audio = struct.unpack_from('<II', vorb, 24)
    last_extra = struct.unpack_from('<H', vorb, 34)[0]
    allocs = vorb[36:44]
    uid = vorb[44:48]
    bs = vorb[48:50]
    (setup, sg), = packets(data[:audio], seek)[:1]
    if sg != 0:
        raise UnsupportedWem('setup packet granule != 0')
    flags, mode_bits = setup_modes(setup, channels)
    pk = packets(data, audio)
    if bs[0] == bs[1]:
        out = [p for p, _g in pk]
    else:
        out = [standard_to_modified(p, flags, mode_bits) for p, _g in pk]
    setup_rec = struct.pack('<H', len(setup)) + setup
    body = setup_rec + b''.join((struct.pack('<H', len(p)) + p for p in out))
    maxpkt = max((len(p) for p in out)) if out else 0
    new_vorb = struct.pack('<IIIHH', samples, len(setup_rec), len(body), 0, last_extra) + struct.pack('<II', 0, len(setup_rec)) + struct.pack('<HH', maxpkt, last_extra) + allocs + uid + bs
    assert len(new_vorb) == 42
    chunks = [(b'fmt ', fmt), (b'vorb', new_vorb), (b'data', body)]
    riff = bytearray()
    for i, (tag, b) in enumerate(chunks):
        riff += tag + struct.pack('<I', len(b)) + b
        if len(b) & 1 and i < len(chunks) - 1:
            riff += b'\x00'
    return b'RIFF' + struct.pack('<I', len(riff) + 4) + b'WAVE' + bytes(riff)

def convert(acb):
    return media.convert(to_acr(acb))

def _pairs(limit):
    sys.path.insert(0, os.path.join(HERE, '..', 'crossmaps'))
    import akpk
    from _acb_audio import ACB
    acb = {}
    for p in akpk.packages(ACB):
        for e in p.streams:
            acb.setdefault(e.id, (p, e))
    out = []
    for p in akpk.packages(akpk.ACR_SOUND):
        for e in p.streams:
            if e.id in acb and len(out) < limit * 40:
                a = acb[e.id][0].read(acb[e.id][1])
                r = p.read(e)
                try:
                    ca, cr = (dict(riff_chunks(a)), dict(riff_chunks(r)))
                except UnsupportedWem:
                    continue
                if len(ca.get(b'vorb', b'')) == 52 and len(cr.get(b'vorb', b'')) == 42 and (ca[b'vorb'][:4] == cr[b'vorb'][:4]):
                    out.append((e.id, a, r))
    return out

def _check(limit=200):
    pairs = _pairs(limit)
    n = {'pairs': 0, 'setup same': 0, 'packets same': 0, 'packets differ': 0, 'pcm same': 0, 'pcm DIFFERENT': 0, 'vorb fields same': 0}
    diffs = []
    for i, a, r in pairs[:limit]:
        try:
            got = riff_chunks(to_acr(a))
        except UnsupportedWem:
            n['unsupported'] = n.get('unsupported', 0) + 1
            continue
        g, w = (dict(got), dict(riff_chunks(r)))
        n['pairs'] += 1
        seek = struct.unpack_from('<I', w[b'vorb'], 16)[0]
        L = struct.unpack_from('<I', g[b'vorb'], 4)[0]
        if g[b'data'][:L] == w[b'data'][seek:seek + L]:
            n['setup same'] += 1
        if g[b'data'] == w[b'data'][seek:]:
            n['packets same'] += 1
        else:
            n['packets differ'] += 1
            diffs.append(i)
        if media.decode(to_acr(a)) == media.decode(a):
            n['pcm same'] += 1
        else:
            n['pcm DIFFERENT'] += 1
    print(n)
    print('differ:', [hex(i) for i in diffs[:10]])
    return not n['pcm DIFFERENT']
if __name__ == '__main__':
    if sys.argv[1:2] == ['--check']:
        sys.exit(0 if _check(int(sys.argv[2]) if len(sys.argv) > 2 else 200) else 1)
    open(sys.argv[2], 'wb').write(convert(open(sys.argv[1], 'rb').read()))
