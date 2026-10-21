"""forgelib/ac4/build_tables.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
BUILDROW = 881535190
VALUE = {18: 5, 28: 6, 10: 4, 6: 4, 25: 4, 7: 4, 8: 4, 13: 16, 14: 16}

class Note(Exception):
    pass

def rows(b, ac3=False):
    out, i = ([], 0)
    while i + 8 <= len(b):
        if struct.unpack_from('<I', b, i + 4)[0] != BUILDROW or b[i + 8] != 1:
            i += 1
            continue
        f_end = i + 4 + 4 + 1 + 4 + 4
        cnt = f_end + (4 if ac3 else 0)
        n = struct.unpack_from('<I', b, cnt)[0]
        if n > 512:
            raise Note(f'row at {i}: {n} cells')
        p = cnt + 4
        for _ in range(n):
            kw = struct.unpack_from('<I', b, p + 8)[0]
            kind = kw >> 16 & 63
            if kind not in VALUE:
                raise Note(f'cell kind {kind:#x} at {p}')
            p += 12 + VALUE[kind]
        out.append((i, f_end, cnt, p))
        i = p
    return out

def convert(b, row_field=b'\xff\xff\xff\xff'):
    rs = rows(b)
    if not rs:
        return b
    last = rs[-1][3]
    if last + 4 > len(b):
        raise Note('no trailer u32 after the last row')
    out, at = (bytearray(), 0)
    for _i, f_end, _cnt, _end in rs:
        out += b[at:f_end] + row_field
        at = f_end
    out += b[at:last] + b[last + 4:]
    return bytes(out)

def main():
    import pickle
    import collections
    P = pickle.load(open('out/ac4/bt_pairs.pkl', 'rb'))
    c = collections.Counter()
    for u, (cls, n, a, b4, m) in P.items():
        if cls not in ('BuildTable', 'EntityBuilder'):
            continue
        try:
            got = convert(b4)
        except Note as e:
            c[cls, f'note {e}'] += 1
            continue
        if got == a:
            c[cls, 'identical'] += 1
        else:
            alt = bytes(a)
            same_len = len(got) == len(a)
            diff = [k for k in range(min(len(got), len(a))) if got[k] != a[k]]
            c[cls, 'same length, the row field only' if same_len and all((a[k] == 0 for k in diff)) else 'differs'] += 1
            if not (same_len and all((a[k] == 0 for k in diff))):
                print('  differs:', cls, n, len(a), len(got), diff[:6])
    print(dict(c))
if __name__ == '__main__':
    main()
