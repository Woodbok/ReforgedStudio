"""forgelib/audio/banks.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
from collections import Counter
from akpk import ACR_SOUND, AC3_SOUND, packages
HIRC_TYPES = {1: 'State', 2: 'Sound', 3: 'Action', 4: 'Event', 5: 'RanSeqCntr', 6: 'SwitchCntr', 7: 'ActorMixer', 8: 'Bus', 9: 'LayerCntr', 10: 'MusicSegment', 11: 'MusicTrack', 12: 'MusicSwitch', 13: 'MusicRanSeq', 14: 'Attenuation', 15: 'DialogueEvent', 16: 'FeedbackBus', 17: 'FeedbackNode', 18: 'FxShareSet', 19: 'FxCustom', 20: 'AuxBus', 21: 'LFO', 22: 'Envelope'}
CODECS = {1: 'PCM', 2: 'ADPCM', 358: 'XMA2', 65534: 'PCM(ext)', 65535: 'Vorbis', 12345: 'PTADPCM', 12352: 'Opus'}

def chunks(b):
    pos, out = (0, [])
    while pos + 8 <= len(b):
        tag, size = (b[pos:pos + 4], struct.unpack_from('<I', b, pos + 4)[0])
        out.append((tag.decode('latin-1'), pos + 8, size))
        pos += 8 + size
    return out

class Bank:

    def __init__(self, pkg, entry, data):
        self.pkg, self.entry = (pkg.name, entry)
        self.chunks = {t: (o, s) for t, o, s in chunks(data)}
        o, _ = self.chunks['BKHD']
        self.version, self.id = struct.unpack_from('<II', data, o)
        self.hirc = []
        if 'HIRC' in self.chunks:
            o, s = self.chunks['HIRC']
            n, = struct.unpack_from('<I', data, o)
            p = o + 4
            for _ in range(n):
                t, size, oid = struct.unpack_from('<BII', data, p)
                self.hirc.append((t, oid, data[p + 5:p + 5 + size]))
                p += 5 + size
        self.media = []
        if 'DIDX' in self.chunks:
            o, s = self.chunks['DIDX']
            for i in range(s // 12):
                wid, _, sz = struct.unpack_from('<III', data, o + 12 * i)
                self.media.append((wid, sz))
        self.names = {}
        if 'STID' in self.chunks:
            o, s = self.chunks['STID']
            _, n = struct.unpack_from('<II', data, o)
            p = o + 8
            for _ in range(n):
                bid, ln = struct.unpack_from('<IB', data, p)
                self.names[bid] = data[p + 5:p + 5 + ln].decode('latin-1')
                p += 5 + ln

def wem_codec(head):
    if head[:4] not in (b'RIFF', b'RIFX'):
        return f'?{head[:4]!r}'
    for tag, o, s in chunks(head[12:]):
        if tag == 'fmt ':
            fmt, = struct.unpack_from('<H', head, 12 + o)
            return CODECS.get(fmt, f'{fmt:#06x}')
    return 'no-fmt'

def load(root):
    banks, pkgs = ([], packages(root))
    for p in pkgs:
        with open(p.path, 'rb') as f:
            for e in p.banks:
                f.seek(e.offset)
                banks.append(Bank(p, e, f.read(e.size)))
    return (pkgs, banks)
if __name__ == '__main__':
    for game, root in (('ACR', ACR_SOUND), ('AC3', AC3_SOUND)):
        pkgs, banks = load(root)
        print(f'== {game}: {len(banks)} banks')
        print('  BKHD versions:', dict(Counter((b.version for b in banks))))
        print('  chunk tags:   ', dict(Counter((t for b in banks for t in b.chunks))))
        ht = Counter((HIRC_TYPES.get(t, t) for b in banks for t, _, _ in b.hirc))
        print('  HIRC objects: ', dict(ht.most_common()))
        codecs = Counter()
        for p in pkgs:
            if 'sfx' not in p.name and 'eng' not in p.name:
                continue
            with open(p.path, 'rb') as f:
                for e in p.streams + p.externals:
                    f.seek(e.offset)
                    codecs[p.name, wem_codec(f.read(64))] += 1
        for (n, c), k in sorted(codecs.items()):
            print(f'  streamed {n:24} {c:10} {k}')
