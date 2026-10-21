"""forgelib/loader/ac4mesh.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
STRIDES = (20, 24)

def layout(b):
    stride = b[46]
    assert stride in STRIDES, stride
    total = sum((struct.unpack_from('<I', b, 55 + 32 * k + 12)[0] for k in range(b[47]))) * stride
    found = None
    for at in range(55 + 32 * b[47], min(len(b) - 8, 2000), 4):
        size = struct.unpack_from('<I', b, at)[0]
        if not size or size % stride or at + 8 + size > len(b):
            continue
        isize = struct.unpack_from('<I', b, at + 4 + size)[0]
        if isize and isize % 2 == 0 and (at + 8 + size + isize <= len(b)):
            if size == total:
                return (stride, size // stride, at + 4)
            found = found or (stride, size // stride, at + 4)
    if found:
        return found
    raise AssertionError('no vertex block')

def downgrade(b):
    stride, count, data = layout(b)
    out = bytearray(b)
    assert out[45] in (5, 6), f'version {out[45]}'
    out[45] -= 2
    for v in range(count):
        p = data + v * stride
        x, y, z, w = struct.unpack_from('<4h', out, p)
        s = -1 if w < 0 else 1
        struct.pack_into('<4h', out, p, x * s, y * s, z * s, w * 8)
        assert out[p + 15] == 128, f'vertex {v}: byte 15 = {out[p + 15]}'
        out[p + 15] = 255
    end = data + stride * count
    isize = struct.unpack_from('<I', out, end)[0]
    zeros_at = end + 4 + isize + 8
    tail = bytes(out[:zeros_at]) + bytes(8) + bytes(out[zeros_at:])
    k = tail.rfind(bytes.fromhex('3300000007000000'))
    assert k > zeros_at, 'no 33 / 07 block'
    tail = tail[:k] + bytes.fromhex('2400000020000000') + tail[k + 12:]
    tail = tail[:-5] + tail[-4:]
    return tail

def sink(b, fraction):
    stride, count, data = layout(b)
    zs = [struct.unpack_from('<h', b, data + v * stride + 4)[0] for v in range(count)]
    drop = round((max(zs) - min(zs)) * fraction)
    assert min(zs) - drop >= -32768, 'the lowered mesh leaves the short range'
    out = bytearray(b)
    for v in range(count):
        struct.pack_into('<h', out, data + v * stride + 4, zs[v] - drop)
    return bytes(out)

def _real(v):
    x, y, z, w = v
    s = abs(w) / 8 / 32768
    return (x * s, y * s, z * s)

def fit_to(b, ref):
    sb, nb, db = layout(b)
    sr, nr, dr = layout(ref)
    vb = [struct.unpack_from('<4h', b, db + k * sb) for k in range(nb)]
    vr = [struct.unpack_from('<4h', ref, dr + k * sr) for k in range(nr)]
    rb = [_real(v) for v in vb]
    rr = [_real(v) for v in vr]
    rad_b = max((max(abs(p[0]), abs(p[1])) for p in rb))
    rad_r = max((max(abs(p[0]), abs(p[1])) for p in rr))
    zb0, zb1 = (min((p[2] for p in rb)), max((p[2] for p in rb)))
    zr0, zr1 = (min((p[2] for p in rr)), max((p[2] for p in rr)))
    out = bytearray(b)
    for k, (x, y, z, w) in enumerate(vb):
        s = abs(w) / 8 / 32768
        rx, ry, rz = (x * s * rad_r / rad_b, y * s * rad_r / rad_b, zr0 + (z * s - zb0) * (zr1 - zr0) / (zb1 - zb0))
        q = [round(c / s) for c in (rx, ry, rz)]
        assert all((-32768 <= c <= 32767 for c in q)), q
        struct.pack_into('<3h', out, db + k * sb, *q)
    return bytes(out)

def double_sided(b):
    stride, count, data = layout(b)
    assert b[47] == 1, 'one sub-mesh only'
    at = data + stride * count
    isize = struct.unpack_from('<I', b, at)[0]
    idx = struct.unpack_from(f'<{isize // 2}H', b, at + 4)
    tris = len(idx) // 3
    rev = [v for t in range(tris) for v in (idx[3 * t], idx[3 * t + 2], idx[3 * t + 1])]
    out = bytearray(b[:at] + struct.pack('<I', isize * 2) + b[at + 4:at + 4 + isize] + struct.pack(f'<{len(rev)}H', *rev) + b[at + 4 + isize:])
    counts = [k for k in (55 + 20, 55 + 32 + 24) if struct.unpack_from('<I', out, k)[0] == tris]
    assert len(counts) == 2, f'triangle counts at {counts}'
    for k in counts:
        struct.pack_into('<I', out, k, tris * 2)
    return bytes(out)

def check(o3, o4):
    checked, problems = (0, [])
    for u, o in o4.items():
        if u not in o3 or o['type'] != 1096652136:
            continue
        a, b = (o3[u]['body'], o['body'])
        try:
            la, lb = (layout(a), layout(b))
        except AssertionError:
            continue
        if la[:2] != lb[:2] or la[1] == 0:
            continue
        stride, count, data = la
        end = data + stride * count
        if a[end:end + 4 + struct.unpack_from('<I', a, end)[0]] != b[end:end + 4 + struct.unpack_from('<I', b, end)[0]]:
            continue
        c = downgrade(b)
        checked += 1
        if len(c) != len(a) or c[:data] != a[:data] or c[end:] != a[end:]:
            problems.append(f"{o3[u]['name']}: header / tail")
            continue
        off = [i for i in range(data, end) if abs(c[i] - a[i]) > 4 and abs(c[i] - a[i]) < 252]
        if off:
            problems.append(f"{o3[u]['name']}: {len(off)} vertex bytes off by more than 4")
    return (checked, problems)
