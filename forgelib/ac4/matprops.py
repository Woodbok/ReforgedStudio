"""forgelib/ac4/matprops.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import collections
import os
import struct
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
MATERIAL, MASK, MASK16 = (2244483011, 3747441678, 2461622132)
TEXTURE_SELECTOR, TIME_OSCILLATOR, UV_TRANSFORM = (2097694221, 3974486380, 3308134693)
KIND_SIZES = {0: 1, 1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 4, 7: 4, 10: 4, 17: 4, 25: 4, 8: 8, 9: 8, 11: 8, 12: 12, 13: 16, 14: 16, 15: 36, 16: 64}
INLINE = {'AC3': {TEXTURE_SELECTOR: 20, TIME_OSCILLATOR: 16, UV_TRANSFORM: 34}, 'AC4': {TEXTURE_SELECTOR: 24, TIME_OSCILLATOR: 20, UV_TRANSFORM: 34}}
INLINE_KIND = 19

class ParseError(Exception):
    pass

class Item:
    __slots__ = ('hash', 'cls', 'flags', 'value', 'prefix', 'body')

    def __init__(self, h, cls, flags, value=None, prefix=None, body=None):
        self.hash, self.cls, self.flags, self.value, self.prefix, self.body = (h, cls, flags, value, prefix, body)

    @property
    def kind(self):
        return self.flags >> 16 & 63

    def texture(self):
        if self.cls == TEXTURE_SELECTOR and self.body is not None and (len(self.body) >= 6) and (self.body[-6:-4] == b'\x01\x00'):
            return struct.unpack_from('<I', self.body, len(self.body) - 4)[0]
        return None

    def to_bytes(self):
        out = struct.pack('<III', self.hash, self.cls, self.flags)
        if self.kind == INLINE_KIND:
            return out + self.prefix + self.body
        return out + self.value

class Material:
    __slots__ = ('head', 'items')

    def __init__(self, head, items):
        self.head, self.items = (head, items)

    def to_bytes(self):
        return self.head + struct.pack('<I', len(self.items)) + b''.join((it.to_bytes() for it in self.items))

    def segments(self):
        h = self.head
        if len(h) < 6 + 20 + 4 or struct.unpack_from('<I', h, 6)[0] != MATERIAL or struct.unpack_from('<I', h, 26)[0] != MASK:
            raise ParseError(f'head is not header / Material / Mask: {h[:32].hex()}')
        m = h.find(struct.pack('<I', MASK16), 30)
        if m < 0:
            raise ParseError('no Mask16 in the head')
        return (h[:6], h[6:26], h[26:m], h[m:])

def parse(body, game='AC3', inline=None):
    inline = INLINE[game] if inline is None else inline
    m = body.find(struct.pack('<I', MASK16))
    if m < 0 or any((b > 1 for b in body[m + 4:m + 20])):
        raise ParseError('no Mask16 of the known layout')
    count = struct.unpack_from('<I', body, m + 24)[0]
    head = body[:m + 24]
    pos = m + 28
    items = []
    for i in range(count):
        if pos + 12 > len(body):
            raise ParseError(f'item {i} of {count}: header past the end at +{pos}')
        h, cls, flags = struct.unpack_from('<III', body, pos)
        kind = flags >> 16 & 63
        pos += 12
        if kind == INLINE_KIND:
            cid = struct.pack('<I', cls)
            if body[pos + 4:pos + 8] == cid:
                plen = 8
            elif body[pos + 5:pos + 9] == cid:
                plen = 9
            else:
                raise ParseError(f'item {i} ({h:08X}): inline {cls:08X} without its class id at +{pos}: {body[pos:pos + 12].hex()}')
            size = inline.get(cls)
            if size is None:
                raise ParseError(f'item {i} ({h:08X}): inline class {cls:08X} ({common.cls(cls, game)}) of unknown size')
            if pos + plen + size > len(body):
                raise ParseError(f'item {i} ({h:08X}): inline {common.cls(cls, game)} past the end')
            items.append(Item(h, cls, flags, prefix=body[pos:pos + plen], body=body[pos + plen:pos + plen + size]))
            pos += plen + size
        elif kind in KIND_SIZES:
            n = KIND_SIZES[kind]
            if pos + n > len(body):
                raise ParseError(f'item {i} ({h:08X}): value past the end')
            items.append(Item(h, cls, flags, value=body[pos:pos + n]))
            pos += n
        else:
            raise ParseError(f'item {i} ({h:08X}): kind {kind:#x} AC3 has no reader for, at +{pos - 12}')
    if pos != len(body):
        raise ParseError(f'{count} items end at +{pos}, body is {len(body)} bytes')
    return Material(head, items)

def main():
    L = []
    for game, arcs in (('AC3', ('DataPC_AC3MP_Plantation.forge', 'DataPC_AC3MP_Charlestown_dlc.forge', 'DataPC_AC3MP_Boston_Mill.forge', 'DataPC_AC3MP_Martinique_dlc.forge', 'DataPC_AC3MP_Boston_Harbou.forge', 'DataPC_AC2MP_Whiteroom.forge')), ('AC4', ('DataPC_AC3MP_MSettlement.forge', 'DataPC_AC3MP_Plantation_dlc.forge'))):
        res = collections.Counter()
        kinds = collections.Counter()
        fails = []
        for arc in arcs:
            path = common.ac3_archive(arc) if game == 'AC3' else common.ac4_archive(arc)
            for rec, d in common.payloads(path):
                for o in common.objects_in(d):
                    if common.cls(o['type'], game) != 'Material':
                        continue
                    try:
                        mat = parse(o['body'], game)
                    except ParseError as e:
                        res['fail'] += 1
                        if len(fails) < 20:
                            fails.append((arc[7:-6], o['name'], str(e)))
                        continue
                    res['parsed'] += 1
                    assert mat.to_bytes() == o['body'], o['name']
                    for it in mat.items:
                        kinds[it.kind] += 1
        L.append(f'{game}: {dict(res)}; kinds seen {dict(sorted(kinds.items()))}')
        for f in fails:
            L.append(f'   FAIL {f}')
    common.report('matprops_gate.txt', L)
    print('\n'.join(L))
if __name__ == '__main__':
    main()
