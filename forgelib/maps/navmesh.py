"""forgelib/maps/navmesh.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import json
import math
import os
import struct
import sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
MANAGER, NAVMESH, TRIANGLE, BV = (189587680, 1551585982, 3453086738, 1256993910)
METALINK, SAFEPOS, TRIREF, OBJWP, WPREF = (1715433517, 912201035, 815747378, 839453910, 1312337253)
NAVMESHREF, WPNETWORK, WAYPOINT = (2897755239, 2998092726, 2339050963)
CROWDFLOW, FLOWPOINT, METALINKREF = (1002359595, 3368171392, 1166885164)
ENTITY = 159662430
BORDER = {65508: 'wall', 65507: 'seam', 65506: 'ledge'}
OWN = 4
WP_METALINK = 2
_I, _H, _B, _F4, _BV6 = (struct.Struct('<I'), struct.Struct('<H'), struct.Struct('<B'), struct.Struct('<4f'), struct.Struct('<6f'))

class FormatError(Exception):
    pass

class _R:
    __slots__ = ('b', 'o')

    def __init__(self, b, o=0):
        self.b, self.o = (b, o)

    def u8(self):
        v = self.b[self.o]
        self.o += 1
        return v

    def u16(self):
        v = _H.unpack_from(self.b, self.o)[0]
        self.o += 2
        return v

    def u32(self):
        v = _I.unpack_from(self.b, self.o)[0]
        self.o += 4
        return v

    def u16s(self, n):
        v = struct.unpack_from(f'<{n}H', self.b, self.o)
        self.o += 2 * n
        return list(v)

    def f4(self):
        v = _F4.unpack_from(self.b, self.o)
        self.o += 16
        return v

    def raw(self, n):
        v = bytes(self.b[self.o:self.o + n])
        self.o += n
        return v

    def obj(self, cls):
        uid, c = struct.unpack_from('<II', self.b, self.o)
        if c != cls:
            raise FormatError(f'+{self.o + 4}: class {c:#010x}, expected {cls:#010x}')
        self.o += 8
        return uid

    def link(self):
        k, u = (self.b[self.o], _I.unpack_from(self.b, self.o + 1)[0])
        self.o += 5
        return (k, u)

def _link(l):
    return bytes((l[0],)) + _I.pack(l[1])

def _obj(uid, cls):
    return struct.pack('<II', uid, cls)

class AABV:
    __slots__ = ('uid', 'lo', 'hi', 't')

    def __init__(self, uid, lo, hi, t):
        self.uid, self.lo, self.hi, self.t = (uid, lo, hi, t)

    @classmethod
    def read(cls, r):
        uid = r.obj(BV)
        v = _BV6.unpack_from(r.b, r.o)
        r.o += 24
        return cls(uid, v[:3], v[3:], r.u32())

    def to_bytes(self):
        return _obj(self.uid, BV) + _BV6.pack(*self.lo, *self.hi) + _I.pack(self.t)

class Triangle:
    __slots__ = ('uid', 'index', 'nbr', 'counts', 'refs')

    def __init__(self, uid, index, nbr, counts, refs):
        self.uid, self.index, self.nbr, self.counts, self.refs = (uid, index, nbr, counts, refs)

    @classmethod
    def read(cls, r):
        uid = r.obj(TRIANGLE)
        idx = r.u16()
        nbr = r.u16s(3)
        counts = list(r.raw(4))
        return cls(uid, idx, nbr, counts, r.u16s(sum(counts)))

    def to_bytes(self):
        return _obj(self.uid, TRIANGLE) + struct.pack('<4H', self.index, *self.nbr) + bytes(self.counts) + struct.pack(f'<{len(self.refs)}H', *self.refs)

    def edge_refs(self, e):
        k = sum(self.counts[:e])
        return self.refs[k:k + self.counts[e]]

    @property
    def waypoints(self):
        return self.refs[sum(self.counts[:3]):]

class Piece:
    __slots__ = ('uid', 'verts', 'tris', 'idx', 'mopp', 'mopp_os', 'bv', 'source', 'zone', 'flags', 'component')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw[k])

    @classmethod
    def read(cls, r):
        uid = r.obj(NAVMESH)
        nv = r.u32()
        verts = [r.f4() for _ in range(nv)]
        nt = r.u32()
        tris = [Triangle.read(r) for _ in range(nt)]
        idx = r.u16s(r.u32())
        mopp = r.raw(r.u32())
        mopp_os = r.f4()
        bv = AABV.read(r)
        source, zone = (r.link(), r.link())
        flags = r.raw(3)
        return cls(uid=uid, verts=verts, tris=tris, idx=idx, mopp=mopp, mopp_os=mopp_os, bv=bv, source=source, zone=zone, flags=flags, component=r.u8())

    def to_bytes(self):
        out = [_obj(self.uid, NAVMESH), _I.pack(len(self.verts))]
        out += [_F4.pack(*v) for v in self.verts]
        out.append(_I.pack(len(self.tris)))
        out += [t.to_bytes() for t in self.tris]
        out.append(_I.pack(len(self.idx)) + struct.pack(f'<{len(self.idx)}H', *self.idx))
        out.append(_I.pack(len(self.mopp)) + self.mopp)
        out += [_F4.pack(*self.mopp_os), self.bv.to_bytes(), _link(self.source), _link(self.zone), self.flags, bytes((self.component,))]
        return b''.join(out)

    def corners(self, t):
        i = 3 * t
        return [self.verts[self.idx[i + k]][:3] for k in range(3)]

class MetaLink:
    __slots__ = ('uid', 'safe', 'tris', 'objwp', 'nav_object', 'next_start', 'b', 'type', 'f')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw[k])

    @classmethod
    def read(cls, r):
        uid = r.obj(METALINK)
        safe = []
        for _ in range(2):
            su = r.obj(SAFEPOS)
            safe.append((su, r.raw(3)))
        tris = []
        for _ in range(r.u32()):
            tu = r.obj(TRIREF)
            tris.append((tu, r.u16(), r.u16(), r.u16()))
        objwp = []
        for _ in range(r.u32()):
            ou = r.obj(OBJWP)
            wu = r.obj(WPREF)
            wr = (r.u16(), r.u16())
            k = r.u8()
            objwp.append((ou, wu, wr, [(r.u16(), r.u16()) for _ in range(k)]))
        nav = r.link()
        a, b = (r.u8(), r.u8())
        typ = r.u32()
        return cls(uid=uid, safe=safe, tris=tris, objwp=objwp, nav_object=nav, next_start=a, b=b, type=typ, f=r.raw(3))

    def to_bytes(self):
        out = [_obj(self.uid, METALINK)]
        out += [_obj(su, SAFEPOS) + sb for su, sb in self.safe]
        out.append(_I.pack(len(self.tris)))
        out += [_obj(tu, TRIREF) + struct.pack('<3H', t, p, c) for tu, t, p, c in self.tris]
        out.append(_I.pack(len(self.objwp)))
        for ou, wu, wr, links in self.objwp:
            out.append(_obj(ou, OBJWP) + _obj(wu, WPREF) + struct.pack('<2HB', *wr, len(links)) + b''.join((struct.pack('<2H', *l) for l in links)))
        out += [_link(self.nav_object), bytes((self.next_start, self.b)), _I.pack(self.type), self.f]
        return b''.join(out)

    @property
    def side_a(self):
        return self.tris[:self.next_start]

    @property
    def side_b(self):
        return self.tris[self.next_start:]

class WayPoint:
    __slots__ = ('uid', 'f', 'links', 'w')

    def __init__(self, uid, f, links, w):
        self.uid, self.f, self.links, self.w = (uid, f, links, w)

    @classmethod
    def read(cls, r):
        uid = r.obj(WAYPOINT)
        f = (r.u8(), r.u8())
        n = r.u8()
        links = [(r.u16(), r.u16()) for _ in range(n)]
        return cls(uid, f, links, r.u16s(7))

    def to_bytes(self):
        return _obj(self.uid, WAYPOINT) + bytes((*self.f, len(self.links))) + b''.join((struct.pack('<2H', *l) for l in self.links)) + struct.pack('<7H', *self.w)

    @property
    def z(self):
        return struct.unpack('<e', _H.pack(self.w[5]))[0]

    @staticmethod
    def cost(raw):
        return ((raw >> 2) / 128.0, raw & 3)

class Manager:
    __slots__ = ('head', 'table', 'one', 'uid', 'pieces', 'world', 'metalinks', 'navrefs', 'comp_start', 'comp_size', 'wpn_uid', 'waypoints', 'bv', 'cell', 'tail')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw[k])

    def to_bytes(self):
        out = [struct.pack('<II', self.head, len(self.table))]
        out += [struct.pack('<IHHI', *e) for e in self.table]
        out.append(bytes((self.one,)) + _obj(self.uid, MANAGER) + _I.pack(len(self.pieces)))
        out += [p.to_bytes() for p in self.pieces]
        out.append(_link(self.world) + _I.pack(len(self.metalinks)))
        out += [m.to_bytes() for m in self.metalinks]
        out.append(_I.pack(len(self.navrefs)))
        out += [_obj(u, NAVMESHREF) + struct.pack('<2H', p, c) for u, p, c in self.navrefs]
        for arr in (self.comp_start, self.comp_size):
            out.append(_I.pack(len(arr)) + struct.pack(f'<{len(arr)}H', *arr))
        out.append(_obj(self.wpn_uid, WPNETWORK) + _I.pack(len(self.waypoints)))
        out += [w.to_bytes() for w in self.waypoints]
        out += [self.bv.to_bytes(), _H.pack(self.cell), self.tail]
        return b''.join(out)

    def counts(self):
        P = self.pieces
        return {(NAVMESH, 2845): len(P), (0, 1688): sum((len(p.verts) for p in P)), (TRIANGLE, 2840): sum((len(p.tris) for p in P)), (0, 664): sum((len(p.idx) for p in P)), (0, 408): sum((len(p.mopp) for p in P)), (METALINK, 2845): len(self.metalinks), (TRIREF, 2845): sum((len(m.tris) for m in self.metalinks)), (OBJWP, 2845): sum((len(m.objwp) for m in self.metalinks)), (NAVMESHREF, 2845): len(self.navrefs), (WAYPOINT, 2845): len(self.waypoints)}

    def table_check(self):
        c = self.counts()
        return [(cl, k, n, c[cl, k]) for cl, _z, k, n in self.table if (cl, k) in c and c[cl, k] != n]

def parse_manager(body):
    r = _R(body)
    head, n = (r.u32(), r.u32())
    if n > 64:
        raise FormatError(f'type table of {n} entries')
    table = [struct.unpack_from('<IHHI', body, 8 + 12 * i) for i in range(n)]
    r.o = 8 + 12 * n
    one = r.u8()
    uid = r.obj(MANAGER)
    pieces = [Piece.read(r) for _ in range(r.u32())]
    world = r.link()
    metalinks = [MetaLink.read(r) for _ in range(r.u32())]
    navrefs = []
    for _ in range(r.u32()):
        u = r.obj(NAVMESHREF)
        navrefs.append((u, r.u16(), r.u16()))
    comp_start = r.u16s(r.u32())
    comp_size = r.u16s(r.u32())
    wpn = r.obj(WPNETWORK)
    waypoints = [WayPoint.read(r) for _ in range(r.u32())]
    bv = AABV.read(r)
    cell = r.u16()
    return Manager(head=head, table=table, one=one, uid=uid, pieces=pieces, world=world, metalinks=metalinks, navrefs=navrefs, comp_start=comp_start, comp_size=comp_size, wpn_uid=wpn, waypoints=waypoints, bv=bv, cell=cell, tail=bytes(body[r.o:]))

class Flow:
    __slots__ = ('uid', 'name', 'record', 'filter', 'bidir', 'flt', 'weight', 'local', 'points', 'width', 'head', 'tail', 'metalink', 'tris', 'wps', 'dynamic')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def length(self):
        return sum((math.dist(a[:2], b[:2]) for a, b in zip(self.points, self.points[1:])))

def parse_flow(body):
    k = body.find(_I.pack(CROWDFLOW))
    if k < 0 or _I.unpack_from(body, 6)[0] != ENTITY:
        return None
    m = struct.unpack_from('<16f', body, 10)
    k += 4
    out = dict(bidir=body[k + 1])
    k += 2
    out['flt'] = _I.unpack_from(body, k)[0]
    out['weight'] = struct.unpack_from('<f', body, k + 4)[0]
    k += 8
    n = _I.unpack_from(body, k)[0]
    k += 4
    local = []
    for _ in range(n):
        if _I.unpack_from(body, k + 4)[0] != FLOWPOINT:
            return None
        local.append(struct.unpack_from('<4f', body, k + 8))
        k += 24
    out['width'] = struct.unpack_from('<f', body, k)[0]
    k += 4
    lists = []
    for _ in range(2):
        c = _I.unpack_from(body, k)[0]
        k += 4
        L = []
        for _ in range(c):
            L.append(_I.unpack_from(body, k + 1)[0])
            k += 5
        lists.append(L)
    if _I.unpack_from(body, k + 4)[0] != METALINKREF:
        return None
    out['metalink'] = struct.unpack_from('<2H', body, k + 8)
    k += 12
    tris, wps = ([], [])
    k2 = k + 4
    nt = _I.unpack_from(body, k)[0]
    tris = []
    for _ in range(nt):
        tris.append(struct.unpack_from('<3H', body, k2 + 8))
        k2 += 14
    nw = _I.unpack_from(body, k2)[0]
    k2 += 4
    for _ in range(nw):
        wps.append(struct.unpack_from('<2H', body, k2 + 8))
        k2 += 12
    out['dynamic'] = body[k2]
    pts = [(p[0] * m[0] + p[1] * m[4] + p[2] * m[8] + m[12], p[0] * m[1] + p[1] * m[5] + p[2] * m[9] + m[13], p[0] * m[2] + p[1] * m[6] + p[2] * m[10] + m[14]) for p in local]
    out.update(local=local, points=pts, head=lists[0], tail=lists[1], tris=tris, wps=wps)
    return out

def _segd(a, b, p):
    dx, dy = (b[0] - a[0], b[1] - a[1])
    L = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) if L else 0.0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)

def _tri_dist(p, tri):
    d = [(tri[k][0] - p[0]) * (tri[(k + 1) % 3][1] - p[1]) - (tri[k][1] - p[1]) * (tri[(k + 1) % 3][0] - p[0]) for k in range(3)]
    if all((v >= 0 for v in d)) or all((v <= 0 for v in d)):
        return 0.0
    return min((_segd(tri[k], tri[(k + 1) % 3], p) for k in range(3)))

def managers(path, with_objects=False):
    import mapwalk
    from forge import Forge
    with Forge(path) as f:
        for rec in f.records:
            if rec.name == 'GlobalMetaFile' or not rec.size:
                continue
            try:
                p = mapwalk.Payload(rec, f.read(rec))
            except Exception:
                continue
            for i, o in enumerate(p.objects):
                if o['type'] == MANAGER:
                    m = parse_manager(o['body'])
                    yield ((rec.name, i, m, o) if with_objects else (rec.name, i, m))

class NavMap:

    def __init__(self, path):
        import mapwalk
        from forge import Forge
        self.path = path
        self.archive = os.path.basename(path)
        self.managers, self.records, self.flows = ({}, {}, {})
        with Forge(path) as f:
            for rec in f.records:
                if rec.name == 'GlobalMetaFile' or not rec.size:
                    continue
                try:
                    p = mapwalk.Payload(rec, f.read(rec))
                except Exception:
                    continue
                for i, o in enumerate(p.objects):
                    if o['type'] == MANAGER:
                        m = parse_manager(o['body'])
                        self.managers[m.cell] = m
                        self.records[m.cell] = (rec.name, i)
                    elif o['type'] == ENTITY and o.get('has_uid') and (_I.pack(CROWDFLOW) in o['body']):
                        d = parse_flow(o['body'])
                        if d:
                            self.flows[o['uid']] = Flow(uid=o['uid'], name=o.get('name'), record=rec.name, filter=o['filter'], **d)
        self._grid()

    @staticmethod
    def _solve(pos):
        best = (0, None, None, None, set())
        for w in range(1, 2049):
            groups = Counter(((c - i % w, r - i // w) for i, (c, r) in pos.items()))
            (q0, r0), n = groups.most_common(1)[0]
            if n > best[0]:
                best = (n, w, q0, r0, {i for i, (c, r) in pos.items() if (c - i % w, r - i // w) == (q0, r0)})
            if n == len(pos):
                break
        return best[1:]

    def _grid(self):
        ms = list(self.managers.values())
        self.size = self.width = self.col0 = self.row0 = None
        self.leaf_width = self.leaf_col0 = self.leaf_row0 = None
        self.off_grid = []
        if not ms:
            return
        s = self.size = round(ms[0].bv.hi[0] - ms[0].bv.lo[0], 3)
        cr = {m.cell: (round(m.bv.lo[0] / s), round(m.bv.lo[1] / s)) for m in ms}
        self.width, self.col0, self.row0, fit = self._solve(cr)
        self.off_grid = sorted(set(cr) - fit)
        leaves = {}
        for m in ms:
            if m.cell in fit:
                c = Counter((w.w[6] for w in m.waypoints if w.w[0] == OWN))
                if c:
                    leaves.setdefault(c.most_common(1)[0][0], cr[m.cell])
        if leaves:
            self.leaf_width, self.leaf_col0, self.leaf_row0, _f = self._solve(leaves)

    def cell_corner(self, cell):
        if self.width is None:
            return None
        row, col = divmod(cell, self.width)
        return ((col + self.col0) * self.size, (row + self.row0) * self.size)

    def leaf_corner(self, leaf):
        if self.leaf_width is None:
            return self.cell_corner(leaf)
        row, col = divmod(leaf, self.leaf_width)
        return ((col + self.leaf_col0) * self.size, (row + self.leaf_row0) * self.size)

    def leaf_cell(self, leaf):
        if self.leaf_width is None or self.width is None:
            return leaf
        row, col = divmod(leaf, self.leaf_width)
        col, row = (col + self.leaf_col0 - self.col0, row + self.leaf_row0 - self.row0)
        return row * self.width + col if 0 <= col < self.width else None

    def neighbour(self, cell, code):
        if code == OWN:
            return cell
        dx, dy = (code % 3 - 1, code // 3 - 1)
        return cell + dx + dy * self.width

    def flow_metalink(self, f):
        m = self.managers.get(self.leaf_cell(f.metalink[1]))
        return m.metalinks[f.metalink[0]] if m and f.metalink[0] < len(m.metalinks) else None

    def wp_pos(self, cell, i):
        w = self.managers[cell].waypoints[i]
        c = self.leaf_corner(w.w[6])
        return (c[0] + w.w[3] / 1024 * self.size, c[1] + w.w[4] / 1024 * self.size, w.z)

    def stats(self):
        P = [p for m in self.managers.values() for p in m.pieces]
        border = Counter()
        for p in P:
            for t in p.tris:
                for n in t.nbr:
                    if n >= 65504:
                        border[n] += 1
        return dict(archive=self.archive, managers=len(self.managers), pieces=len(P), vertices=sum((len(p.verts) for p in P)), triangles=sum((len(p.tris) for p in P)), isolated_pieces=sum((1 for p in P if p.component == 255)), mopp_bytes=sum((len(p.mopp) for p in P)), border_edges={f'{k:#06x}': v for k, v in sorted(border.items())}, metalinks=dict(sorted(Counter((ml.type for m in self.managers.values() for ml in m.metalinks)).items())), waypoints=sum((len(m.waypoints) for m in self.managers.values())), waypoint_links=sum((len(w.links) for m in self.managers.values() for w in m.waypoints)), flows=len(self.flows), flow_points=sum((len(f.points) for f in self.flows.values())), grid=dict(size=self.size, width=self.width, cells=sorted(self.managers), off_grid=self.off_grid, leaf_width=self.leaf_width))

    def check(self):
        c, bad = (Counter(), [])

        def chk(ok, what, detail='', note=False):
            c[what + (' ok' if ok else ' NOTE' if note else ' FAIL')] += 1
            if not ok and len(bad) < 40:
                bad.append(f"{('note' if note else 'FAIL')} {what}: {detail}")
        for cell in self.off_grid:
            chk(False, 'manager on the cell grid', f'cell {cell}', note=True)
        for cell, m in sorted(self.managers.items()):
            chk(not m.table_check(), 'allocation table', f'cell {cell} {m.table_check()}')
            if cell in self.off_grid:
                continue
            for pi, p in enumerate(m.pieces):
                chk(len(p.idx) == 3 * len(p.tris) and (not p.idx or max(p.idx) < len(p.verts)), 'piece indices', f'cell {cell} piece {pi}')
                for ti, t in enumerate(p.tris):
                    for n in t.nbr:
                        if n < 65504:
                            chk(n < len(p.tris) and ti in p.tris[n].nbr, 'neighbour reciprocal', f'cell {cell} {pi}/{ti}')
                    for e in range(3):
                        for r in t.edge_refs(e):
                            mm = self.managers.get(self.neighbour(cell, r & 15))
                            chk(mm is not None and r >> 4 < len(mm.metalinks), 'edge metalink ref', f'{cell} {pi}/{ti}')
                    for r in t.waypoints:
                        mm = self.managers.get(self.neighbour(cell, r & 15))
                        chk(mm is not None and r >> 4 < len(mm.waypoints), 'triangle waypoint ref', f'{cell} {pi}/{ti}')
            for i, w in enumerate(m.waypoints):
                for raw, tgt in w.links:
                    mm = self.managers.get(self.neighbour(cell, tgt & 15))
                    n = (len(mm.metalinks) if raw & WP_METALINK else len(mm.waypoints)) if mm else 0
                    chk(tgt >> 4 < n, 'waypoint link target', f'cell {cell} wp {i} -> {tgt:#x}')
                if w.w[0] != 15:
                    mm = self.managers.get(self.neighbour(cell, w.w[0]))
                    ok = mm is not None and w.w[1] < len(mm.pieces) and (w.w[2] < len(mm.pieces[w.w[1]].tris))
                    chk(ok, 'waypoint triangle', f'cell {cell} wp {i}')
                    if ok:
                        tri = mm.pieces[w.w[1]].corners(w.w[2])
                        chk(_tri_dist(self.wp_pos(cell, i), tri) < 1.5, 'waypoint near its triangle', f'cell {cell} wp {i}')
        for u, f in sorted(self.flows.items()):
            if f.metalink == (65535, 65535):
                chk(False, 'flow with a metalink', f'{u:#x}', note=True)
                ml = None
            else:
                ml = self.flow_metalink(f)
                chk(ml is not None and ml.type == 5 and (ml.nav_object[1] == u), 'flow metalink', f'{u:#x} {f.metalink}')
            chk(len(f.tris) == len(f.points) == len(f.wps), 'flow refs per point', f'{u:#x}')
            for (ti, pi, tc), (wi, wc) in zip(f.tris, f.wps):
                tm, wm = (self.managers.get(tc), self.managers.get(wc))
                chk(tm is not None and pi < len(tm.pieces) and (ti < len(tm.pieces[pi].tris)), 'flow triangle ref', f'{u:#x}')
                chk(wm is not None and wi < len(wm.waypoints), 'flow waypoint ref', f'{u:#x}')
            for x in f.head + f.tail:
                chk(x in self.flows, 'flow head / tail defined', f'{u:#x} -> {x:#x}', note=True)
        return (c, bad)

    def wp_graph(self):
        if getattr(self, '_g', None) is not None:
            return self._g
        g = {}
        tri_wps = {}
        for cell, m in self.managers.items():
            for pi, p in enumerate(m.pieces):
                for ti, t in enumerate(p.tris):
                    tri_wps[cell, pi, ti] = [(self.neighbour(cell, r & 15), r >> 4) for r in t.waypoints]
        pos = {}
        for cell, m in self.managers.items():
            for i in range(len(m.waypoints)):
                pos[cell, i] = self.wp_pos(cell, i)
        self.wp_xyz = pos
        for cell, m in self.managers.items():
            for i, w in enumerate(m.waypoints):
                out = g.setdefault((cell, i), [])
                for raw, tgt in w.links:
                    c, flags = WayPoint.cost(raw)
                    tc = self.neighbour(cell, tgt & 15)
                    if flags & WP_METALINK:
                        mm = self.managers.get(tc)
                        if not mm or tgt >> 4 >= len(mm.metalinks):
                            continue
                        ml = mm.metalinks[tgt >> 4]
                        if ml.type == 5:
                            continue
                        for _u, tt, tp, tcell in ml.tris:
                            for q in tri_wps.get((tcell, tp, tt), ()):
                                if q != (cell, i) and q in pos:
                                    out.append((q, math.dist(pos[cell, i], pos[q])))
                        for _ou, _wu, (wi, wc), _l in ml.objwp:
                            if (wc, wi) in pos:
                                out.append(((wc, wi), math.dist(pos[cell, i], pos[wc, wi])))
                    elif (tc, tgt >> 4) in pos:
                        out.append(((tc, tgt >> 4), c))
        for cell, m in self.managers.items():
            for ml in m.metalinks:
                if ml.type != 5:
                    continue
                for _ou, _wu, (wi, wc), near in ml.objwp:
                    a = (wc, wi)
                    if a not in pos:
                        continue
                    for ni, nc in near:
                        b = (nc, ni)
                        if b in pos:
                            d = math.dist(pos[a], pos[b])
                            g.setdefault(a, []).append((b, d))
                            g.setdefault(b, []).append((a, d))
        self._g = g
        return g

    def astar(self, a, b, limit=200000):
        import heapq
        g = self.wp_graph()
        pos = self.wp_xyz
        A = [q for q in (a if isinstance(a, list) else [a]) if q in pos]
        B = {q for q in (b if isinstance(b, list) else [b]) if q in pos}
        if not A or not B:
            return None
        h = lambda q: min((math.dist(pos[q], pos[t]) for t in B))
        best = {q: 0.0 for q in A}
        prev = {}
        heap = [(h(q), 0.0, q) for q in A]
        heapq.heapify(heap)
        n = 0
        while heap and n < limit:
            _f, d, q = heapq.heappop(heap)
            if q in B:
                path = [q]
                while q in prev:
                    q = prev[q]
                    path.append(q)
                return (d, path[::-1])
            if d > best.get(q, 1e+18):
                continue
            n += 1
            for t, c in g.get(q, ()):
                nd = d + c
                if nd < best.get(t, 1e+18):
                    best[t] = nd
                    prev[t] = q
                    heapq.heappush(heap, (nd + h(t), nd, t))
        return None

    def flow_end_wps(self, flow, end):
        f = self.flows[flow]
        out = []
        if f.wps:
            wi, wc = f.wps[end]
            out.append((wc, wi))
        if f.tris:
            ti, pi, tc = f.tris[end]
            m = self.managers.get(tc)
            if m and pi < len(m.pieces) and (ti < len(m.pieces[pi].tris)):
                out += [(self.neighbour(tc, r & 15), r >> 4) for r in m.pieces[pi].tris[ti].waypoints]
        return out

    def flow_wp(self, flow, end):
        f = self.flows[flow]
        if not f.wps:
            return None
        wi, wc = f.wps[end]
        return (wc, wi)

    def overlay(self):
        r2 = lambda v: round(v, 2)
        tris, borders = ([], {f'{k:#06x}': [] for k in BORDER})
        cent = {}
        for cell, m in sorted(self.managers.items()):
            for pi, p in enumerate(m.pieces):
                for ti, t in enumerate(p.tris):
                    c = p.corners(ti)
                    tris.append([r2(c[0][0]), r2(c[0][1]), r2(c[1][0]), r2(c[1][1]), r2(c[2][0]), r2(c[2][1]), r2(sum((v[2] for v in c)) / 3)])
                    cent[cell, pi, ti] = [sum((v[k] for v in c)) / 3 for k in range(3)]
                    for e, n in enumerate(t.nbr):
                        if n in BORDER:
                            a, b = (c[e], c[(e + 1) % 3])
                            borders[f'{n:#06x}'].append([r2(a[0]), r2(a[1]), r2(b[0]), r2(b[1])])
        links = {}
        for cell, m in sorted(self.managers.items()):
            for li, ml in enumerate(m.metalinks):
                sides = []
                for side in (ml.side_a, ml.side_b):
                    pts = [cent[c, p, t] for _u, t, p, c in side if (c, p, t) in cent]
                    if pts:
                        sides.append([r2(sum((q[0] for q in pts)) / len(pts)), r2(sum((q[1] for q in pts)) / len(pts))])
                if sides:
                    links.setdefault(str(ml.type), []).append(dict(cell=cell, i=li, sides=sides, n=len(ml.tris), obj=ml.nav_object[1]))
        g = self.wp_graph()
        wps, wl, seen = ([], [], set())
        index = {}
        for q, (x, y, z) in self.wp_xyz.items():
            index[q] = len(wps)
            wps.append([r2(x), r2(y), r2(z), q[0], q[1]])
        for q, out in g.items():
            for t, _c in out:
                key = (min(index[q], index[t]), max(index[q], index[t]))
                if key not in seen:
                    seen.add(key)
                    wl.append(list(key))
        flows = []
        for u, f in sorted(self.flows.items()):
            flows.append(dict(uid=f'{u:#010x}', name=f.name, pts=[[r2(p[0]), r2(p[1]), r2(p[2])] for p in f.points], head=[f'{x:#010x}' for x in f.head], tail=[f'{x:#010x}' for x in f.tail], width=r2(f.width), len=r2(f.length()), bidir=f.bidir, ml=list(f.metalink)))
        return dict(archive=self.archive, stats=self.stats(), tris=tris, borders=borders, metalinks=links, waypoints=wps, wplinks=wl, flows=flows)

def release_archives():
    sys.path.insert(0, os.path.join(ROOT, 'modes'))
    import dom_check
    return dom_check.release_archives()

def resolve(p):
    sys.path.insert(0, os.path.join(ROOT, 'modes'))
    import dom_check
    return dom_check.resolve(p)

def out_dir():
    sys.path.insert(0, os.path.join(ROOT, 'modes'))
    import dom_check
    return os.path.join(dom_check.main_checkout() or ROOT, 'out', 'navmesh')

def roundtrip(path):
    n = ok = 0
    bad = []
    for rname, i, m, o in managers(path, with_objects=True):
        n += 1
        b = m.to_bytes()
        if b == o['body']:
            ok += 1
        else:
            k = next((j for j in range(min(len(b), len(o['body']))) if b[j] != o['body'][j]), min(len(b), len(o['body'])))
            bad.append(f"{rname}#{i} cell {m.cell}: {len(o['body'])} -> {len(b)} bytes, first difference at +{k}")
    return (n, ok, bad)

def render_png(nm, out, px=6):
    from PIL import Image, ImageDraw
    xs = [v[0] for m in nm.managers.values() for p in m.pieces for v in p.verts]
    ys = [v[1] for m in nm.managers.values() for p in m.pieces for v in p.verts]
    x0, x1, y0, y1 = (min(xs) - 5, max(xs) + 5, min(ys) - 5, max(ys) + 5)
    W, H = (int((x1 - x0) * px), int((y1 - y0) * px))
    im = Image.new('RGB', (W, H), (20, 22, 24))
    d = ImageDraw.Draw(im)
    P = lambda x, y: ((x - x0) * px, (y1 - y) * px)
    ov = nm.overlay()
    for t in ov['tris']:
        d.polygon([P(t[0], t[1]), P(t[2], t[3]), P(t[4], t[5])], fill=(60, 120, 90), outline=(45, 80, 64))
    col = {'0xffe4': (235, 70, 70), '0xffe3': (70, 200, 255), '0xffe2': (255, 200, 40)}
    for k, segs in ov['borders'].items():
        for s in segs:
            d.line([P(s[0], s[1]), P(s[2], s[3])], fill=col[k], width=2)
    mcol = {'0': (200, 200, 200), '1': (255, 120, 255), '5': (255, 255, 255), '6': (255, 160, 60), '7': (120, 255, 120)}
    for typ, L in ov['metalinks'].items():
        if typ == '5':
            continue
        for l in L:
            if len(l['sides']) == 2:
                d.line([P(*l['sides'][0]), P(*l['sides'][1])], fill=mcol.get(typ, (180, 180, 255)), width=2)
    for f in ov['flows']:
        d.line([P(p[0], p[1]) for p in f['pts']], fill=(255, 255, 255), width=2)
        a, b = (f['pts'][-2], f['pts'][-1])
        d.ellipse([P(b[0], b[1])[0] - 3, P(b[0], b[1])[1] - 3, P(b[0], b[1])[0] + 3, P(b[0], b[1])[1] + 3], fill=(255, 80, 80))
    im.save(out)
    return (out, (x0, y0, x1, y1, px))

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('archives', nargs='*')
    ap.add_argument('--roundtrip', action='store_true')
    ap.add_argument('--release', action='store_true', help="every map archive of release_set.json + AC3's own maps")
    ap.add_argument('--summary', action='store_true')
    ap.add_argument('--png', action='store_true')
    ap.add_argument('--check', action='store_true', help="the decoding's self-checks (NavMap.check)")
    ap.add_argument('--out')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    todo = (release_archives() if a.release else []) + [resolve(p) for p in a.archives]
    if not todo:
        ap.error('give archives or --release')
    if a.roundtrip:
        tot = same = 0
        tables = Counter()
        fails = []
        for p in todo:
            n, ok, bad = roundtrip(p)
            tot += n
            same += ok
            fails += bad
            print(f"{('OK  ' if ok == n else 'FAIL')} {os.path.basename(p):44} managers {n:4}  identical {ok:4}", flush=True)
            for b in bad[:5]:
                print('     ', b)
        print(f'round trip: {same} / {tot} NavMeshManagers byte-identical on {len(todo)} archives')
        return 0 if same == tot and tot else 1
    rc = 0
    for p in todo:
        nm = NavMap(p)
        if a.check:
            c, bad = nm.check()
            fails = sum((v for k, v in c.items() if k.endswith('FAIL')))
            bad.sort(key=lambda b: not b.startswith('FAIL'))
            rc |= bool(fails)
            print(f"{('OK  ' if not fails else 'FAIL')} {nm.archive}: " + ', '.join((f'{k} {v}' for k, v in sorted(c.items()))))
            for b in bad[:10]:
                print('     ', b)
            continue
        if a.summary or not a.png:
            s = nm.stats()
            if a.json:
                print(json.dumps(s, indent=1))
            else:
                print(f"{s['archive']}: {s['managers']} managers (cells of {s['grid']['size']} m, {s['grid']['width']} per row), {s['pieces']} pieces ({s['isolated_pieces']} isolated), {s['vertices']} vertices, {s['triangles']} triangles, MOPP {s['mopp_bytes']} bytes")
                print(f"  border edges {s['border_edges']}")
                print(f"  metalinks by type {s['metalinks']}")
                print(f"  waypoints {s['waypoints']} ({s['waypoint_links']} links); CrowdFlows {s['flows']} ({s['flow_points']} points)")
        if a.png:
            out = a.out or os.path.join(out_dir(), os.path.basename(p).replace('.forge', '.png'))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            print('wrote', render_png(nm, out)[0])
    return rc
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
