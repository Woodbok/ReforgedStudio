"""forgelib/loader/ac4tex.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
TEX = 2729961751
HEADER = 86
AC3_MARK, AC4_MARK = (bytes.fromhex('0200000020000000'), bytes.fromhex('0100000007000000'))

def split4(b):
    assert b[HEADER:HEADER + 8] == AC4_MARK, b[HEADER:HEADER + 8].hex()
    for d in range(40, 120):
        at = HEADER + 8 + d + 1
        if at + 4 <= len(b) and struct.unpack_from('<I', b, at)[0] == len(b) - at - 4:
            return (b[:HEADER], b[HEADER + 8:HEADER + 8 + d], b[HEADER + 8 + d:at], b[at + 4:])
    raise AssertionError('no pixel block')

def split3(b):
    size = struct.unpack_from('<I', b, HEADER)[0]
    p = HEADER + 4 + size
    assert b[p:p + 8] == AC3_MARK, b[p:p + 8].hex()
    return (b[:HEADER], b[p + 8:-1], b[-1:], b[HEADER + 4:p])

def downgrade(b):
    head, desc, last, pixels = split4(b)
    head, desc = (bytearray(head), bytearray(desc))
    _three(head, 50)
    _three(desc, 40)
    return bytes(head) + struct.pack('<I', len(pixels)) + pixels + AC3_MARK + bytes(desc) + b'\x00'

def _three(buf, at):
    if struct.unpack_from('<3I', buf, at) == (3, 3, 3):
        struct.pack_into('<3I', buf, at, 1, 1, 1)
SETTINGS = (66, 72, 73)

def prove(o3, o4):
    checked, ok, bad = (0, 0, [])
    for u, o in o4.items():
        if o['type'] != TEX or u not in o3 or o['body'][10:38] != o3[u]['body'][10:38]:
            continue
        checked += 1
        a, d = (bytearray(o3[u]['body']), bytearray(downgrade(o['body'])))
        for i in SETTINGS:
            d[i] = a[i]
        h3, de3, l3, p3 = split3(bytes(a))
        h4, de4, l4, p4 = split3(bytes(d))
        if (h3, de3, l3, len(p3)) == (h4, de4, l4, len(p4)):
            ok += 1
        else:
            bad.append(o3[u]['name'])
    return (checked, ok, bad)

def check(o3, o4):
    from collections import Counter
    diffs, n, pixels_same = (Counter(), 0, 0)
    for u, o in o4.items():
        if o['type'] != TEX or u not in o3:
            continue
        a, b = (o3[u]['body'], o['body'])
        h3, d3, l3, p3 = split3(a)
        h4, d4, l4, p4 = split4(b)
        n += 1
        pixels_same += p3 == p4
        for i in range(HEADER):
            if h3[i] != h4[i]:
                diffs['head', i, h4[i], h3[i]] += 1
        assert len(d3) == len(d4), (o3[u]['name'], len(d3), len(d4))
        for i in range(len(d3)):
            if d3[i] != d4[i]:
                diffs['desc', i, d4[i], d3[i]] += 1
        if l3 != l4:
            diffs['last', 0, l4[0], l3[0]] += 1
    return (n, pixels_same, diffs)
