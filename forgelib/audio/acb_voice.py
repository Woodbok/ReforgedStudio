"""forgelib/audio/acb_voice.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import akpk
try:
    import acb_media
    import acb_music
    import hirc_convert as hc
except ImportError:
    acb_media = acb_music = hc = None
from banks import chunks
from overlap import fnv1
BANKS = ('BNK_VO_MP', 'BNK_VO_MP_NonLocalised')
LANGUAGE = 'english(us)'

def event_id(acb_event):
    return fnv1(f'acb_forge_voice_{acb_event:08x}')

class AcbVoice:
    prefix = 'acb'

    def __init__(self):
        self.objs, self.didx, self._streams = ({}, {}, {})
        pkgs = akpk.packages(acb_music.ACB_SOUND)
        for p in sorted(pkgs, key=lambda p: LANGUAGE not in p.languages.values()):
            for e in p.streams:
                self._streams.setdefault(e.id, (p, e))
        for name in BANKS:
            bid = fnv1(name)
            cands = [(p, e) for p in pkgs for e in p.banks if e.id == bid]
            eng = [(p, e) for p, e in cands if p.languages.get(e.lang) == LANGUAGE]
            p, e = (eng or cands)[0]
            data = p.read(e)
            hc.set_version(acb_music.ACB_VERSION)
            _v, items = hc.parse_bank(data)
            bad = [oid for _t, oid, _tr, sk in items if sk]
            if bad:
                raise SystemExit(f'ACB {name}: {len(bad)} objects did not parse cleanly')
            for t, oid, tr, _sk in items:
                self.objs.setdefault(oid, (t, acb_music.convert48(t, tr)))
            ch = {t: (o, s) for t, o, s in chunks(data)}
            if 'DIDX' in ch:
                o, s = ch['DIDX']
                start = ch['DATA'][0]
                for k in range(s // 12):
                    mid, off, size = struct.unpack_from('<III', data, o + 12 * k)
                    self.didx[mid] = data[start + off:start + off + size]

    def has_stream(self, sid):
        return sid in self._streams

    def stream(self, sid):
        p, e = self._streams[sid]
        return p.read(e)

    @staticmethod
    def convert(wem):
        return acb_media.convert(wem)
if __name__ == '__main__':
    import character_voice as cv
    v = AcbVoice()
    print(len(v.objs), 'objects,', len(v.didx), 'embedded clips')
    for a in sys.argv[1:]:
        e = int(a, 0)
        t = v.objs.get(e)
        print(hex(e), "in ACB's banks" if t else 'NOT FOUND', 'clips:', len(cv.reachable_clips(v.objs, e)))
