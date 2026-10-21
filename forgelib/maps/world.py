"""forgelib/maps/world.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import convert
import mapwalk
NOT_COMPONENTS = {'SSLv2', 'AES', 'SEED', 'CAMELLIA', '_nextafter', 'RC2', 'TLSv1', 'MD5', 'RC4'}

def world(game, archive):
    ids = convert.class_ids()
    for p in mapwalk.payloads(game, archive):
        if p.rec.index == 1:
            for i, o in enumerate(p.objects):
                if o['type'] == ids['World']:
                    return (p, i)
    raise LookupError(f'no World in {game}:{archive}')

def markers(body):
    ids = convert.class_ids()
    rev = {v: k for k, v in ids.items() if k not in NOT_COMPONENTS}
    out = []
    for k in range(0, len(body) - 3):
        n = rev.get(struct.unpack_from('<I', body, k)[0])
        if n:
            out.append((k, n))
    return out

def segments(body):
    m = markers(body)
    cuts = [0] + [k for k, _ in m] + [len(body)]
    names = ['(header)'] + [n for _, n in m]
    return [(names[i], body[cuts[i]:cuts[i + 1]]) for i in range(len(names))]
MAP_EVENT_MANAGER_EMPTY = struct.pack('<I', 0) + b'\x00'
AC3_WORLD_ENDING = bytes(8)
COUNT_BEFORE_NAV = 9
KEEP_ESCORT_LISTS = os.environ.get('AC3FORGE_ESCORT_LISTS', '1') != '0'
CHEST_LINK, PATH_CLASS, NODE_CLASS = (b'\x01\x00', 3559388932, 1105138129)

def escort_lists_at(body, first):

    def fits(k):
        try:
            n = struct.unpack_from('<I', body, k)[0]
            k += 4
            if n > 16383:
                return False
            for _ in range(n):
                if body[k:k + 2] != CHEST_LINK:
                    return False
                k += 6
            n = struct.unpack_from('<I', body, k)[0]
            k += 4
            if n > 16383:
                return False
            for _ in range(n):
                _u, cid, nn = struct.unpack_from('<III', body, k)
                k += 12
                if cid != PATH_CLASS:
                    return False
                for _ in range(nn):
                    _nu, nc, z = struct.unpack_from('<IIB', body, k)
                    if nc != NODE_CLASS or z != 0:
                        return False
                    k += 15
            return k == len(body)
        except struct.error:
            return False
    for k in range(first, len(body) - 7):
        if fits(k):
            return k
    return None

def free_uid(body, near, taken):
    u = near
    while u in taken or struct.pack('<I', u) in body:
        u = u + 1 & 4294967295
    return u
MODE_WEIGHT = 0.75

def add_modes(body, modes, taken):
    ids = convert.class_ids()
    m = markers(body)
    gmi = [k for k, n in m if n == 'GameModeInfo']
    sgm = [k for k, n in m if n == 'SupportedGameMode']
    if len(gmi) != 1 or not sgm:
        raise ValueError('no single GameModeInfo with SupportedGameModes')
    count = struct.unpack_from('<I', body, gmi[0] + 8)[0]
    if count != len(sgm):
        raise ValueError(f'GameModeInfo count {count} but {len(sgm)} SupportedGameModes')
    have = {struct.unpack_from('<I', body, k + 6)[0] for k in sgm}
    new = [u for u in modes if u not in have]
    if not new:
        return (body, [])
    at = max(sgm) + 4 + 2 + 4 + 4
    out = bytearray(body)
    blob = b''
    near = struct.unpack_from('<I', body, max(sgm) - 4)[0] + 64
    for u in new:
        uid = free_uid(bytes(out) + blob, near, taken)
        near = uid + 1
        blob += struct.pack('<IIHIf', uid, ids['SupportedGameMode'], 1, u, MODE_WEIGHT)
    out[at:at] = blob
    struct.pack_into('<I', out, gmi[0] + 8, count + len(new))
    return (bytes(out), new)

def tail_ending(body, cut, keep=None):
    strict = keep is True
    keep = KEEP_ESCORT_LISTS if keep is None else keep
    if keep:
        at = escort_lists_at(body, cut)
        if at is None:
            if strict:
                raise ValueError("KEEP_ESCORT_LISTS: no chest + path lists fit the World's tail")
            return (AC3_WORLD_ENDING, f"tail: no chest / Escort lists in this World (KEEP_ESCORT_LISTS on); dropped {len(body) - cut} bytes, wrote AC3's 8-byte ending")
        n = struct.unpack_from('<I', body, at)[0]
        p = struct.unpack_from('<I', body, at + 4 + 6 * n)[0]
        return (body[at:], f"tail: kept ACR's chest + Escort lists ({n} chests, {p} paths, {len(body) - at} bytes; KEEP_ESCORT_LISTS)" + (f', dropped {at - cut} bytes before them' if at > cut else ''))
    return (AC3_WORLD_ENDING, f"tail: dropped {len(body) - cut} bytes of chest/Escort lists, wrote AC3's 8-byte ending")

def convert_world(body, mode_remap, taken, keep_escort=None):
    ids = convert.class_ids()
    log = []
    m = markers(body)
    names = [n for _, n in m]
    for need in ('NavigationManager', 'GameplayCoordinatorWorldComponent', 'SupportedGameMode'):
        if need not in names:
            raise ValueError(f'World has no {need}: not a layout this converter knows')
    if 'MapEventManager' in names:
        raise ValueError('already has a MapEventManager - AC3 data?')
    out = bytearray(body)
    last_sgm = max((k for k, n in m if n == 'SupportedGameMode'))
    cut = last_sgm + 4 + 2 + 4 + 4
    ending, line = tail_ending(body, cut, keep_escort)
    log.append(line)
    out[cut:] = ending
    for k, n in m:
        if n == 'SupportedGameMode':
            old = struct.unpack_from('<I', out, k + 6)[0]
            new = mode_remap.get(old, old)
            if new != old:
                struct.pack_into('<I', out, k + 6, new)
                log.append(f'SupportedGameMode 0x{old:08X} -> 0x{new:08X}')
    gcwc = next((k for k, n in m if n == 'GameplayCoordinatorWorldComponent'))
    uid = free_uid(body, struct.unpack_from('<I', body, gcwc - 4)[0] + 256, taken)
    element = struct.pack('<II', uid, ids['MapEventManager']) + MAP_EVENT_MANAGER_EMPTY
    out[gcwc - 4:gcwc - 4] = element
    log.append(f'MapEventManager 0x{uid:08X} inserted before GameplayCoordinatorWorldComponent')
    nav = next((k for k, n in m if n == 'NavigationManager'))
    at = nav - COUNT_BEFORE_NAV
    count = struct.unpack_from('<I', out, at)[0]
    if not 8 <= count <= 32:
        raise ValueError(f'component count at -{COUNT_BEFORE_NAV} reads {count}: older World layout')
    struct.pack_into('<I', out, at, count + 1)
    log.append(f'component count {count} -> {count + 1}')
    return (bytes(out), log)

def mode_remap(game='ACR'):
    import inventory
    import xrefs
    ac3 = {}
    for u, homes in inventory.universe('AC3').items():
        for arc, f, t, n in homes:
            if f == 'Game Bootstrap Settings' and xrefs.cls('AC3', t) == 'UnlockableGameMode':
                ac3[n] = u
    out = {}
    for u, homes in inventory.universe(game).items():
        for arc, f, t, n in homes:
            if xrefs.cls(game, t) == 'UnlockableGameMode' and n in ac3 and (ac3[n] != u):
                out[u] = ac3[n]
    return out

def shape(body):
    return [(n, len(s)) for n, s in segments(body)]

def main(argv):
    for spec in argv:
        game, archive = spec.split(':', 1)
        p, i = world(game, archive)
        body = p.objects[i]['body']
        print(f'== {spec}  World {len(body)} bytes')
        for name, seg in segments(body):
            print(f"   {name:40} {len(seg):6}  {seg[:24].hex(' ')}")
if __name__ == '__main__':
    main(sys.argv[1:])
