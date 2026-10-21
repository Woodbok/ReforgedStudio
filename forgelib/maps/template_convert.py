"""forgelib/maps/template_convert.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import re
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import world
VERSION_AT = 11
ACR_VERSION, AC3_VERSION = (111, 124)
PERM_HEADER, PERM_INSERT = (29, 14)
SHADER = re.compile(b'\\x00\\x03[\\xfe\\xff]\\xff')

def convert(body):
    segs = world.segments(body)
    if b''.join((s for _n, s in segs)) != body:
        raise ValueError("sections don't cover the body")
    out, counts = ([], Counter())
    for name, s in segs:
        if name == 'CompiledMaterialTemplate':
            if s[VERSION_AT] != ACR_VERSION:
                raise ValueError(f"CompiledMaterialTemplate version 0x{s[VERSION_AT]:02X}, not ACR's")
            s = s[:VERSION_AT] + bytes([AC3_VERSION]) + s[VERSION_AT + 1:]
            counts['version'] += 1
        elif name == 'ShaderPermutation':
            k = 4 + PERM_INSERT
            if any(s[4 + PERM_INSERT:4 + 18]):
                raise ValueError('ShaderPermutation header bytes 14..17 not zero')
            s = s[:k] + b'\x00' + s[k:]
            counts['permutations'] += 1
        out.append(s)
    return (b''.join(out), counts)

def _masked(seg):
    out, k = (bytearray(), 0)
    for m in SHADER.finditer(seg):
        s = m.start()
        e = s + 4
        while e + 4 <= len(seg):
            t = struct.unpack_from('<I', seg, e)[0]
            if t == 65535:
                e += 4
                break
            e += 4 + (4 * (t >> 16 & 32767) if t & 65535 == 65534 else 0)
        out += seg[k:s - 4] + b'<SHADER>'
        k = e
    return bytes(out + seg[k:])

def main():
    import mapwalk
    import xrefs
    names = ('ACFE_Tex1_PixelLit', 'AC2_Tex1_PixelLit', 'AC2_Tex1_PixelLit_Specular_Cubemap', 'ACFE_SkyDome')
    got = {}
    for game, arcs in (('ACR', ('DataPC_ACFE_Souk.forge', 'DataPC_AC2MP_Whiteroom.forge')), ('AC3', ('DataPC_AC3MP_Plantation.forge', 'DataPC_AC2MP_Whiteroom.forge', 'DataPC_AC3MP_Animus.forge'))):
        for arc in arcs:
            for p in mapwalk.payloads(game, arc, skip_errors=True):
                for o in p.objects:
                    n = (o['name'] or '').strip()
                    if n in names and xrefs.cls(game, o['type']) == 'MaterialTemplate':
                        got.setdefault(n, {}).setdefault(game, o['body'])
    for n in names:
        conv, counts = convert(got[n]['ACR'])
        sa, sb = (world.segments(conv), world.segments(got[n]['AC3']))
        res = Counter()
        where = {'LightingShaderPermutation': set(), 'ShaderPermutation': set()}
        for (na, xa), (nb, xb) in zip(sa, sb):
            if na != nb:
                res['section order differs'] += 1
                continue
            ma, mb = (_masked(xa), _masked(xb))
            if ma == mb:
                res['same'] += 1
            elif len(ma) == len(mb):
                d = [i for i in range(len(ma)) if ma[i] != mb[i]]
                if na in ('LightingShaderPermutation', 'ShaderPermutation') and len(d) <= 8 and (d[-1] - d[0] < 8):
                    res['same bar the bytecode hash'] += 1
                    where[na].add(len(ma) - d[0])
                elif na == 'ShaderParameter' and len(d) <= 8 and (d[-1] - d[0] < 8):
                    res['same bar a parameter hash'] += 1
                else:
                    res[f'{na} differs ({len(d)} bytes)'] += 1
            else:
                res[f'{na} differs in length ({len(mb) - len(ma):+d})'] += 1
        print(f"{n}: {len(sa)} sections {dict(res)}  (converted: {dict(counts)}); hash offsets from the section's (masked) end: { {k: sorted(v) for k, v in where.items() if v}}")
if __name__ == '__main__':
    main()
