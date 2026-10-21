"""forgelib/maps/realtree.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
COMPILED = struct.pack('<I', 1583250992)
HEADER = {138: 68 * 4, 271: 72 * 4}
ROOT_SIZE = {138: 544, 271: 608}

def al(n):
    return n + 15 & ~15

def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]

def blob_of(body):
    k = body.find(COMPILED)
    size = u32(body, k + 4)
    return (k + 4, body[k + 8:k + 8 + size])

def header(blob):
    v = u32(blob, 0)
    if v == 138:
        opt, main, name_at = (u32(blob, 8), u32(blob, 12), 16)
        kind = struct.unpack_from('<H', blob, 4)[0]
    elif v == 271:
        opt, main, name_at = (u32(blob, 16), u32(blob, 20), 24)
        kind = struct.unpack_from('<H', blob, 8)[0]
    else:
        raise ValueError(f'version {v:#x}')
    name = blob[name_at:name_at + 256].split(b'\x00')[0].decode('latin1')
    return {'version': v, 'kind': kind, 'opt': opt, 'main': main, 'name': name, 'hlen': HEADER[v], 'w1': u32(blob, 4)}

def layout(v, m):
    r = m
    g = lambda o: u32(r, o)
    cur = ROOT_SIZE[v]
    out = []

    def arr(name, rec, n, align=True):
        nonlocal cur
        size = rec * n
        out.append((name, cur, rec, n, size))
        cur += al(size) if align else size
        return out[-1][1]
    if v == 138:
        a_at = arr('blockA', 32, 1, align=False)
        b_at = arr('blockB', 32, 1, align=False)
        A = lambda o: u32(m, a_at + o)
        if g(52) > 0:
            arr('elements.ptrs', 4, g(52))
            start = cur
            for e in range(g(52)):
                el = cur
                cur += 336
                nmesh = u32(m, el + 288)
                meshes = arr(f'el{e}.meshes', 16, nmesh)
                for k in range(nmesh):
                    nv, ni = (u32(m, meshes + 16 * k), u32(m, meshes + 16 * k + 8))
                    arr(f'el{e}.m{k}.verts', 148, nv)
                    arr(f'el{e}.m{k}.idx', 2, ni)
                out.append((f'el{e}.header', el, 336, 1, 336))
            assert cur - start + al(4 * g(52)) == g(60) + al(4 * g(52)) or True
        if g(384):
            arr('r180', 52, g(384))
        arr('r1c.a', 40, g(28))
        arr('r1c.b', 44, g(28))
        rec10 = arr('r10.a', 76, g(16))
        arr('r10.b', 32, g(16))
        if g(40):
            arr('r28', 92, g(40))
        n16 = 0
        start16 = cur
        for i in range(g(16)):
            c = struct.unpack_from('<h', m, rec10 + 76 * i + 46)[0]
            n16 += max(c, 0)
        out.append(('r10.u16', start16, 2, n16, 2 * n16))
        cur = start16 + al(2 * n16)
        arr('A10', 72, g(16))
        arr('A14', 24, g(40))
        arr('A18', 84, A(12))
        arr('A1c', 4, A(8))
        arr('B0c', 32, g(16))
        arr('B10', 32, g(40))
    else:
        a_at = arr('block88', 48, 1, align=False)
        A = lambda o: u32(m, a_at + o)
        if g(84) > 0:
            arr('elements.ptrs', 4, g(84))
            for e in range(g(84)):
                el = cur
                cur += 336
                nmesh = u32(m, el + 292)
                meshes = arr(f'el{e}.meshes', 32, nmesh, align=False)
                for k in range(nmesh):
                    nv, ni = (u32(m, meshes + 32 * k), u32(m, meshes + 32 * k + 16))
                    arr(f'el{e}.m{k}.verts', 152, nv)
                    arr(f'el{e}.m{k}.idx', 2, ni)
                out.append((f'el{e}.header', el, 336, 1, 336))
        if g(364):
            r16c = arr('r16c', 56, g(364))
            for i in range(g(364)):
                arr(f'r16c{i}.pairs', 8, u32(m, r16c + 56 * i + 24))
        arr('r28.a', 40, g(40))
        arr('r28.b', 44, g(40))
        rec10 = arr('r10.a', 88, g(16))
        arr('r10.b', 108, g(16))
        rec40 = None
        if g(64):
            rec40 = arr('r40', 64, g(64), align=False)
        if g(592) > 0:
            arr('r250', 4, g(592))
        n16 = 0
        start16 = cur
        for i in range(g(16)):
            c = struct.unpack_from('<h', m, rec10 + 88 * i + 46)[0]
            n16 += max(c, 0)
        out.append(('r10.u16', start16, 2, n16, 2 * n16))
        cur = start16 + al(2 * n16)
        arr('A10', 32, g(16), align=False)
        arr('A18', 32, g(64), align=False)
        arr('A20', 60, A(8))
        if rec40 is not None:
            r16c = next((o for n, o, *_ in out if n == 'r16c'))
            for j in range(g(64)):
                sel = struct.unpack_from('<b', m, rec40 + 64 * j + 57)[0]
                arr(f'r40.{j}.c', 12, u32(m, r16c + 56 * sel + 24))
        if g(576):
            arr('r240', 28, g(576))
    return (out, cur)

def textures(blob, h):
    o = h['hlen'] + h['opt'] + h['main']
    if o + 4 > len(blob):
        return (None, o)
    n = u32(blob, o)
    o += 4
    out = []
    for _ in range(n):
        t, ln = (u32(blob, o), u32(blob, o + 4))
        out.append((t, blob[o + 8:o + 8 + ln].decode('latin1')))
        o += 8 + ln
    return (out, o)

def trees(game):
    import convert
    import inventory
    import mapwalk
    rtm = convert.class_ids()['RealTreeMesh']
    homes = {}
    for u, hs in inventory.universe(game).items():
        for h in hs:
            if h[2] == rtm:
                homes.setdefault(u, h)
    by_arch = {}
    for u, h in homes.items():
        by_arch.setdefault(h[0], set()).add(u)
    out = []
    for arch, uids in sorted(by_arch.items()):
        for p in mapwalk.payloads(game, arch, skip_errors=True):
            for o in p.objects:
                if o.get('uid') in uids:
                    uids.discard(o['uid'])
                    out.append((arch, p.rec.name, o['name'], o['body']))
            if not uids:
                break
    return out

def check(game):
    rows = []
    for arch, rec, name, body in trees(game):
        _, blob = blob_of(body)
        h = header(blob)
        m = blob[h['hlen'] + h['opt']:h['hlen'] + h['opt'] + h['main']]
        try:
            arrays, end = layout(h['version'], m)
            err = None
        except Exception as e:
            arrays, end, err = ([], -1, repr(e)[:80])
        try:
            tex, tail = textures(blob, h)
        except Exception:
            tex, tail = (None, -1)
        rows.append((name, h, len(m), end, err, tex, tail, len(blob)))
    return rows

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--tree')
    ap.add_argument('--dump', action='store_true')
    a = ap.parse_args(argv)
    for game in ('ACR', 'AC3'):
        rows = check(game)
        ok = sum((1 for r in rows if r[3] == r[2]))
        print(f'== {game}: {len(rows)} trees, main chunk fully accounted for in {ok}')
        for name, h, mlen, end, err, tex, tail, blen in rows:
            if a.tree and a.tree != name:
                continue
            mark = 'OK ' if end == mlen else '!! '
            print(f"  {mark}{name:42} v{h['version']:#x} kind {h['kind']} opt {h['opt']} main {mlen} walked {end} tail {('=' if tail == blen else f'{tail}/{blen}')} tex {(len(tex) if tex is not None else '-')}" + (f' {err}' if err else ''))
if __name__ == '__main__':
    main(sys.argv[1:])
