"""forgelib/ac4chars/skinmesh.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
MESH = 1096652136
MESHBONE = 2666588065
SUBMESH = 2775812079
ENTRY = 40
PALETTE_AT = 23
AC3_RANGE = 10.0
BIND_SCALE = 8
TAIL4 = bytes.fromhex('3300000007000000')
TAIL3 = bytes.fromhex('2400000020000000')
FIELD = 21

class MeshError(ValueError):
    pass

def u32(b, at):
    return struct.unpack_from('<I', b, at)[0]

def layout(b):
    if u32(b, 6) != MESH:
        raise MeshError('not a Mesh')
    n = u32(b, 19)
    post = PALETTE_AT + ENTRY * n
    if post + 32 > len(b):
        raise MeshError(f'palette count {n} past the body')
    for k in range(n):
        if u32(b, PALETTE_AT + ENTRY * k + 4) != MESHBONE:
            raise MeshError(f'palette entry {k} is not a MeshBone')
    version, stride, subs = (b[post + 22], b[post + 23], b[post + 24])
    if not stride or stride % 4:
        raise MeshError(f'stride {stride}')
    rec = post + 32
    total = 0
    for k in range(subs):
        if u32(b, rec + 32 * k) != SUBMESH:
            raise MeshError(f'sub-mesh record {k}')
        total += u32(b, rec + 32 * k + 12)
    at = rec + 32 * subs
    while at < min(len(b) - 8, rec + 32 * subs + 4096):
        size = u32(b, at)
        if size == total * stride and at + 8 + size <= len(b):
            isize = u32(b, at + 4 + size)
            if isize % 2 == 0 and at + 8 + size + isize <= len(b):
                return {'count': n, 'post': post, 'version': version, 'stride': stride, 'subs': subs, 'vb_size_at': at, 'vb': at + 4, 'nv': total, 'ib_size_at': at + 4 + size, 'ib': at + 8 + size, 'ni': isize // 2, 'end': at + 8 + size + isize}
        at += 1
    raise MeshError('no vertex block')

def entries(b, lay=None):
    lay = lay or layout(b)
    return [(PALETTE_AT + ENTRY * k, u32(b, PALETTE_AT + ENTRY * k + 36)) for k in range(lay['count'])]

def unpack_bind(u):
    out = []
    for k in range(3):
        x = u >> FIELD * k & (1 << FIELD) - 1
        out.append(x - (1 << FIELD) if x & 1 << FIELD - 1 else x)
    return out

def pack_bind(xyz, top=0):
    u = 0
    for k, x in enumerate(xyz):
        if not -(1 << FIELD - 1) <= x < 1 << FIELD - 1:
            raise MeshError(f'bind position field {x} out of 21 bits')
        u |= (x & (1 << FIELD) - 1) << FIELD * k
    return u | top << 63

def position_range(b, lay=None):
    lay = lay or layout(b)
    k = b.rfind(TAIL4)
    if k <= lay['end']:
        raise MeshError('no 33 / 07 block')
    return struct.unpack_from('<f', b, k + 8)[0]

def downgrade(b, new_uid=None):
    lay = layout(b)
    if not lay['count']:
        raise MeshError('a rigid mesh: loader/ac4mesh.py')
    f = position_range(b, lay)
    if not 0 < f <= AC3_RANGE:
        raise MeshError(f"position range {f} m outside AC3's +-{AC3_RANGE} m")
    out = bytearray(b)
    for k, (at, _bone) in enumerate(entries(b, lay)):
        if new_uid is not None and u32(out, at) == 0:
            struct.pack_into('<I', out, at, new_uid(k))
        u = struct.unpack_from('<Q', out, at + 4 + 24)[0]
        xyz = [x * BIND_SCALE for x in unpack_bind(u)]
        struct.pack_into('<Q', out, at + 4 + 24, pack_bind(xyz, u >> 63))
    s, vb = (lay['stride'], lay['vb'])
    for v in range(lay['nv']):
        p = vb + s * v
        xyz = struct.unpack_from('<3h', out, p)
        struct.pack_into('<3h', out, p, *(max(-32768, min(32767, round(c * f / AC3_RANGE))) for c in xyz))
    t = tail_ac4(b, lay)
    out = bytes(out[:lay['end']]) + write_tail_ac3(b, lay, t)
    if s == 36:
        try:
            out = narrow(out)
        except MeshError:
            out = narrow(out, None)
    return out

def _lists(b, p, n, lay, zeros_before):
    raw = []
    for k in range(n):
        if k:
            if u32(b, p):
                raise MeshError('tail: the 4 bytes between two lists')
            p += 4
        if u32(b, p) != SUBLIST:
            raise MeshError(f'tail: list {k} class')
        m = b[p + 5]
        if not 1 <= m <= LIST_SLOTS or any(b[p + 15 + m:p + 15 + LIST_SLOTS]):
            raise MeshError(f'tail: list {k} size {m}')
        if max(b[p + 15:p + 15 + m]) >= lay['count']:
            raise MeshError(f"tail: list {k} names palette entry {max(b[p + 15:p + 15 + m])} of {lay['count']}")
        raw.append(b[p:p + 15 + LIST_SLOTS])
        p += 15 + LIST_SLOTS
    return (raw, p)

def tail_ac4(b, lay=None):
    lay = lay or layout(b)
    p = lay['end']
    if b[p:p + 8] != bytes(8):
        raise MeshError('AC4 tail: 8 zero bytes')
    n = u32(b, p + 8)
    if not 1 <= n <= 16 or u32(b, p + 12):
        raise MeshError(f'AC4 tail: list count {n}')
    raw, p = _lists(b, p + 16, n, lay, 8)
    if b[p:p + 8] != TAIL4 or u32(b, p + 12) != n:
        raise MeshError(f"AC4 tail: block {b[p:p + 16].hex(' ')}")
    rng = struct.unpack_from('<f', b, p + 8)[0]
    p += 16
    refs = []
    for k in range(n):
        if b[p:p + 2] != b'\x01\x00':
            raise MeshError(f'AC4 tail: reference {k}')
        refs.append(u32(b, p + 2))
        p += 6
    if p + 15 != len(b):
        raise MeshError(f"AC4 tail: end ({len(b) - p} bytes: {b[p:].hex(' ')})")
    flag, cloth = (b[p], None)
    if flag == 0 and b[p + 1:p + 3] == bytes(2):
        pass
    elif flag == 1 and b[p + 1:p + 3] == bytes(2):
        cloth = (u32(b, p + 3), u32(b, p + 7))
        if cloth != (lay['nv'], lay['ni']):
            raise MeshError(f"AC4 tail: cloth counts {cloth} for a mesh of {lay['nv']} vertices, {lay['ni']} indices")
    else:
        raise MeshError(f"AC4 tail: end {b[p:].hex(' ')}")
    return {'lists': raw, 'range': rng, 'refs': refs, 'cloth': cloth, 'last': u32(b, p + 11)}

def write_tail_ac3(b, lay, t):
    n = len(t['lists'])
    out = bytes(16) + struct.pack('<II', n, 0) + bytes(4).join(t['lists']) + TAIL3 + struct.pack('<I', n)
    out += b''.join((b'\x01\x00' + struct.pack('<I', r) for r in t['refs']))
    if t['cloth'] is None:
        out += bytes(10)
    else:
        nv, ni = t['cloth']
        if nv > 65535 or ni > 65535:
            raise MeshError(f"cloth counts {nv} / {ni} past AC3's u16")
        out += b'\x01' + bytes(5) + struct.pack('<HH', nv, ni)
    return out + struct.pack('<I', t['last'])
SUBLIST = 186463425
LIST_SLOTS = 42

def tail_ac3(b, lay=None):
    lay = lay or layout(b)
    p = lay['end']
    if b[p:p + 16] != bytes(16):
        raise MeshError('tail: 16 zero bytes')
    n = u32(b, p + 16)
    if not 1 <= n <= 16 or u32(b, p + 20):
        raise MeshError(f'tail: list count {n}')
    p += 24
    lists = []
    for k in range(n):
        if k:
            if u32(b, p):
                raise MeshError('tail: the 4 bytes between two lists')
            p += 4
        if u32(b, p) != SUBLIST:
            raise MeshError(f'tail: list {k} class')
        m = b[p + 5]
        if not 1 <= m <= LIST_SLOTS or any(b[p + 15 + m:p + 15 + LIST_SLOTS]):
            raise MeshError(f'tail: list {k} size {m}')
        idx = list(b[p + 15:p + 15 + m])
        if max(idx) >= lay['count']:
            raise MeshError(f"tail: list {k} names palette entry {max(idx)} of {lay['count']}")
        lists.append(idx)
        p += 15 + LIST_SLOTS
    if b[p:p + 8] != TAIL3 or u32(b, p + 8) != n:
        raise MeshError(f"tail: block {b[p:p + 12].hex(' ')}")
    p += 12
    refs = []
    for k in range(n):
        if b[p:p + 2] != b'\x01\x00':
            raise MeshError(f'tail: reference {k}')
        refs.append(u32(b, p + 2))
        p += 6
    if p + 14 != len(b):
        raise MeshError(f"tail: end ({len(b) - p} bytes: {b[p:].hex(' ')})")
    cloth = None
    if b[p] == 1 and b[p + 1:p + 6] == bytes(5):
        cloth = struct.unpack_from('<HH', b, p + 6)
    elif b[p:p + 10] != bytes(10):
        raise MeshError(f"tail: end ({b[p:].hex(' ')})")
    return {'lists': lists, 'refs': refs, 'cloth': cloth, 'last': u32(b, p + 10)}

def verify(b):
    out = []
    try:
        lay = layout(b)
        t = tail_ac3(b, lay)
    except MeshError as e:
        return [str(e)]
    if (lay['stride'], lay['version']) != (32, 0):
        out.append(f"vertex format stride {lay['stride']} version {lay['version']}")
    if t['cloth'] is not None and (t['cloth'][1] != lay['ni'] or not 0 <= t['cloth'][0] - lay['nv'] <= 1):
        out.append(f"cloth counts {t['cloth']} for {lay['nv']} vertices, {lay['ni']} indices")
    rec = lay['post'] + 32
    ranges = [(u32(b, rec + 32 * k + 4), u32(b, rec + 32 * k + 12)) for k in range(lay['subs'])]
    if len(ranges) != len(t['lists']):
        out.append(f"{len(ranges)} sub-meshes, {len(t['lists'])} bone lists")
        return out
    bad_idx = bad_w = 0
    for (first, count), lst in zip(ranges, t['lists']):
        for v in range(first, first + count):
            p = lay['vb'] + lay['stride'] * v
            ws = b[p + 28:p + 32]
            if sum(ws) not in (254, 255, 256):
                bad_w += 1
            if any((w and i >= len(lst) for i, w in zip(b[p + 24:p + 28], ws))):
                bad_idx += 1
    if bad_idx:
        out.append(f'{bad_idx} vertices with a bone index past their list')
    if bad_w:
        out.append(f'{bad_w} vertices whose weights do not sum to 255')
    return out

def bone_entry(b, bone):
    for at, bid in entries(b):
        if bid == bone:
            return b[at + 4:at + 40]
    return None

def rigid_on(b, entry):
    lay = layout(b)
    if not lay['count'] or len(entry) != 36 or u32(entry, 0) != MESHBONE:
        raise MeshError('rigid_on: a skinned mesh and a MeshBone entry')
    out = bytearray(b)
    out[PALETTE_AT + 4:PALETTE_AT + 40] = entry
    s, vb = (lay['stride'], lay['vb'])
    for v in range(lay['nv']):
        p = vb + s * v
        out[p + 24:p + 28] = b'\x00\x00\x00\x00'
        out[p + 28:p + 32] = b'\xff\x00\x00\x00'
    p = lay['end'] + 24
    for k in range(u32(b, lay['end'] + 16)):
        if k:
            p += 4
        out[p + 15] = 0
        p += 15 + LIST_SLOTS
    out = bytes(out)
    probs = verify(out)
    if probs:
        raise MeshError(f'rigid_on: {probs}')
    return out

def narrow(b, colour=b'\xff\xff\xff\xff'):
    lay = layout(b)
    if (lay['stride'], lay['version']) != (36, 2):
        raise MeshError(f"stride {lay['stride']} version {lay['version']}: not AC4's coloured skinned layout")
    vb, nv = (lay['vb'], lay['nv'])
    verts = [b[vb + 36 * v:vb + 36 * v + 32] for v in range(nv)]
    if colour is not None:
        odd = sum((any((abs(x - y) > 1 for x, y in zip(b[vb + 36 * v + 32:vb + 36 * v + 36], colour))) for v in range(nv)))
        uniform = len({b[vb + 36 * v + 32:vb + 36 * v + 36] for v in range(nv)}) == 1
        if odd and (not uniform):
            raise MeshError(f'{odd} vertices with a colour other than {colour.hex()}')
    out = bytearray(b[:lay['vb_size_at']]) + struct.pack('<I', 32 * nv) + b''.join(verts) + b[lay['ib_size_at']:]
    out[lay['post'] + 22] = 0
    out[lay['post'] + 23] = 32
    return bytes(out)
