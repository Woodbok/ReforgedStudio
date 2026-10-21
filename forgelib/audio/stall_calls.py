"""forgelib/audio/stall_calls.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, ROOT, os.path.join(ROOT, 'maps')]
import character_voice as cv
import clone
import exe_registry
import mapwalk
import own_entity
import paths
from datafile import compress, decompress
from forge import Forge
from forge_write import ArchiveImage, set_payload, write
OUT = os.path.join(ROOT, 'out', 'maps')
MILL = 'DataPC_AC3MP_Boston_Mill.forge'
TEMPLATE = 'AC3MP_GEN_Merchant_Meat_001'
COMPONENT = (79, 628)
COUNT_AT, FIRST_AT = (75, 79)
ACR_CALLS = (4267947254, 3366139933, 4187754366, 3367200889, 4048082564, 86687703, 2470376248, 94069227)

def template_component():
    classes, _ = exe_registry.load(exe_registry.AC3)
    for p in mapwalk.payloads('AC3', MILL, skip_errors=True):
        for o in p.objects:
            if o['name'] == TEMPLATE:
                b = o['body']
                comp = b[COMPONENT[0]:COMPONENT[1]]
                assert classes.get(struct.unpack_from('<I', comp, 6)[0]) == 'Ambient3dSoundComponent'
                assert classes.get(struct.unpack_from('<I', b, COMPONENT[1] + 6)[0]) == 'GameplayCoordinatorComponent'
                owner = [i for i in range(len(comp) - 3) if struct.unpack_from('<I', comp, i)[0] == o['uid']]
                calls = [p_ - COMPONENT[0] for p_, _e in cv.entries(b) if COMPONENT[0] <= p_ < COMPONENT[1] and _e]
                inline = [i for i in range(len(comp) - 7) if struct.unpack_from('<I', comp, i)[0] >= 65536 and own_entity.is_class_id(struct.unpack_from('<I', comp, i + 4)[0], classes)]
                assert len(owner) == 1 and len(calls) == 2 and (len(inline) == 10), (owner, calls, inline)
                return (comp, o['uid'], owner[0], calls, inline)
    raise SystemExit(f'{TEMPLATE} not in {MILL}')

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('build')
    ap.add_argument('--src', required=True, help="the map archive with AC3 merchant stalls (merchant_stalls.py's)")
    ap.add_argument('--prefix', default='ACFE_GEN_Merchant')
    ap.add_argument('--calls', type=lambda v: [int(x, 0) for x in v.split(',')], default=list(ACR_CALLS), help="the ACR events the entities call, round robin (default: the merchants' slot-83 calls; the orators: their voice sets' slot-82 speeches, HumanSoundSet_ACFE_OV1..4)")
    ap.add_argument('--acb', action='store_true', help="the --calls are Brotherhood's events (music_override.py --acb-voice: Forli's merchants and orators), ported under acb_forge_voice ids")
    ap.add_argument('--seed', type=lambda v: int(v, 0), default=1511115281)
    ap.add_argument('--uid-base', type=lambda v: int(v, 0), help="fresh ids from the map's range instead of --seed (Souk: 0x5AD4E000; the chain step maps/port/merchant_calls.py)")
    ap.add_argument('--uid-limit', type=lambda v: int(v, 0), default=2048)
    a = ap.parse_args(argv)
    a.prefix = tuple(a.prefix.split(','))
    classes, _ = exe_registry.load(exe_registry.AC3)
    comp, _tmpl_uid, owner_at, call_at, inline_at = template_component()
    if a.acb:
        import acb_voice
        calls = [acb_voice.event_id(e) for e in a.calls]
    else:
        calls = [cv.event_id(e) for e in a.calls]
    taken, stalls = (set(), [])
    with Forge(a.src) as f:
        recs = [r for r in f.records if r.name != 'GlobalMetaFile' and r.size]
        payloads = {}
        for r in recs:
            try:
                payloads[r.index] = (r, mapwalk.Payload(r, f.read(r)))
            except Exception:
                continue
    import npc_port
    valid = npc_port._valid_classes(classes)
    for r, p in payloads.values():
        for o in p.objects:
            if o['has_uid']:
                taken.add(o['uid'])
            b = o['body']
            for _i, u, _c in npc_port._header_pairs(b, valid):
                taken.add(u)
            if (o['name'] or '').startswith(a.prefix) and classes.get(o['type']) == 'Entity' and (classes.get(struct.unpack_from('<I', b, FIRST_AT + 6)[0]) == 'GameplayCoordinatorComponent'):
                stalls.append((o['name'], r.index))
    taken |= set(clone.definitions_index(paths.DEFAULT_DATAPC))
    if a.uid_base is None:
        fresh = clone.FreshUids(a.seed, taken, [])
    else:
        import npc_port
        fresh = npc_port.RangeUids(a.uid_base, a.uid_limit, taken, [p.data for _r, p in payloads.values()])
    stalls.sort()
    if not stalls:
        raise SystemExit(f"no {' / '.join((p + '*' for p in a.prefix))} stall entity with a GameplayCoordinatorComponent first in {a.src}")
    voice_of = {n: k % len(calls) for k, (n, _i) in enumerate(stalls)}
    image = ArchiveImage.read(a.src)
    done = []
    for idx in sorted({i for _n, i in stalls}):
        r, p = payloads[idx]
        objs = []
        for o in p.objects:
            body = o['body']
            if o['name'] in voice_of and classes.get(o['type']) == 'Entity':
                c = bytearray(comp)
                for i in inline_at:
                    struct.pack_into('<I', c, i, fresh.take())
                struct.pack_into('<I', c, owner_at, o['uid'])
                call = calls[voice_of[o['name']]]
                for i in call_at:
                    struct.pack_into('<I', c, i, call)
                n = struct.unpack_from('<I', body, COUNT_AT)[0]
                body = body[:COUNT_AT] + struct.pack('<I', n + 1) + body[COUNT_AT + 4:FIRST_AT] + bytes(c) + body[FIRST_AT:]
                done.append(f"{o['name']}: call {voice_of[o['name']] + 1} {call:#010x}")
            objs.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        d = mapwalk.rebuild(objs, p.filter_table)
        payload = compress(d, first_chunk=mapwalk.first_chunk(d))
        assert decompress(payload) == d
        set_payload(image, r.index, payload)
    dest_dir = os.path.join(OUT, a.build)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(a.src))
    size = write(dest, image)
    seen, problems = (set(), [])
    with Forge(dest) as f:
        for r in f.records:
            if r.index not in {i for _n, i in stalls}:
                continue
            for o in mapwalk.Payload(r, f.read(r)).objects:
                if o['name'] in voice_of and classes.get(o['type']) == 'Entity':
                    b = o['body']
                    c = b[FIRST_AT:FIRST_AT + len(comp)]
                    want = calls[voice_of[o['name']]]
                    if [struct.unpack_from('<I', c, i)[0] for i in call_at] != [want, want]:
                        problems.append(f"{o['name']}: calls")
                    if struct.unpack_from('<I', c, owner_at)[0] != o['uid']:
                        problems.append(f"{o['name']}: owner")
                    ids = [struct.unpack_from('<I', c, i)[0] for i in inline_at]
                    if seen & set(ids) or len(set(ids)) != len(ids):
                        problems.append(f"{o['name']}: an inline id used twice")
                    seen |= set(ids)
    for line in done:
        print('  ' + line)
    print(f"-> {dest} ({size:,} bytes): {len(done)} stalls call as you pass; {('OK' if not problems and len(done) == len(stalls) else problems)}")
    return 0 if not problems else 1
if __name__ == '__main__':
    sys.exit(main())
