"""A map's whole navmesh as linked objects, renumbered on writing: edit freely, every index and packed ref follows.

    w = navmodel.NavWorld(archive)          every NavMeshManager (by cell) and every CrowdFlow, linked
    ...                                     edit: drop pieces, add pieces / triangles / metalinks / waypoints
    w.write(src, dest)                      renumber, recount the allocation tables, re-tree changed MOPPs

The format is the one forgelib's maps/navmesh.py reads (imported read-only through navcut.navmesh_module).
Every index in the format becomes an object reference here:

* NTri: `nbr` (an NTri or a border code >= 0xFFE0), `edge` (per edge, the NLinks its edge refs name), `wps` (the
  NWps its waypoint list names);
* NLink (a metalink): `tris` [(uid, NTri)] side A then side B, `objwp` [(uid, wpref uid, NWp, [NWp])];
* NWp (a waypoint): `tri` (None for a CrowdFlow's own waypoint, cellcode 15), `links` [(raw cost, NWp or NLink)],
  `leaf`, `q` (qx, qy, half z as stored);
* NFlow: a CrowdFlow entity's TriangleRefs / WayPointRefs (one per point) and MetaLinkRef, rewritten in its body.

New CrowdFlows (`add_flow`): a stock flow entity cloned with fresh ids (never 0xF0000000 and up), its points, width and
Head / Tail lists (Handles to the joined flows' uids; `set_joins` edits any flow's), one flow waypoint per point
(cellcode 15, raw_tri (4095, 65535), one flag-2 link to its own type-5 metalink, placed at the point with the point's
leaf) and one type-5 metalink (nav object = the flow, one ObjectWayPoint per point naming that waypoint - its ground
ties and the TriangleRefs are the caller's: `set_flow_tris`). On writing, the entity goes into its link manager's cell
record after the record's last Entity and into the GridCellDataBlock's activated list (as 94 / 96 stock Wolcott flows
sit), FakeCellIndex copied from the record's own entities.

Writing assigns positions (list order) and re-derives every packed ref `(index << 4) | cellcode`, every absolute
(index, cell) pair, the type table (`table_counts`: the 0x0005 pool = triangle refs + 2 x waypoint links + 2 x
ObjectWayPoint ties, exact on all 811 stock and ported managers), the map-wide component table
(pieces joined by type-0 seams; one piece alone = 0xFF) and, for pieces marked `dirty`, MOPP + AABVs. Stock maps
load and write back byte for byte (`roundtrip`).
"""
import math
import os
import struct
import copy
from collections import Counter, defaultdict

import numpy as np

from . import mopp
from . import navcut

OWN = 4
FLOW_CODE = 15
SEAM, DROP, FLOW = 0, 1, 5

# the allocation table in stock order: (class, kind)
NAVMESH, TRIANGLE, METALINK = 0x5C7B52BE, 0xCDD1EC12, 0x663F702D
TRIREF, OBJWP, NAVMESHREF, WAYPOINT = 0x309F5132, 0x32090CD6, 0xACB83C67, 0x8B6B15D3
TABLE_ORDER = [(NAVMESH, 0xB1D), (0, 0x698), (TRIANGLE, 0xB18), (0, 0x5), (0, 0x298), (0, 0x198), (METALINK, 0xB1D),
               (TRIREF, 0xB1D), (OBJWP, 0xB1D), (NAVMESHREF, 0xB1D), (0, 0x29D), (WAYPOINT, 0xB1D)]


class NTri:
    __slots__ = ("uid", "piece", "nbr", "edge", "wps", "counts_tail")

    def __init__(self, uid, piece):
        self.uid, self.piece = uid, piece
        self.nbr, self.edge, self.wps = [0xFFE4] * 3, [[], [], []], []


class NPiece:
    __slots__ = ("uid", "mgr", "verts", "idx", "tris", "mopp", "mopp_os", "bv", "source", "zone", "flags", "component",
                 "dirty", "_key", "_ti")

    def corners(self, t):
        i = 3 * t
        return [self.verts[self.idx[i + k]][:3] for k in range(3)]


class NLink:
    __slots__ = ("uid", "mgr", "safe", "tris", "next_start", "objwp", "nav_object", "b", "type", "f")


class NWp:
    __slots__ = ("uid", "mgr", "f", "links", "tri", "flowcode", "q", "leaf", "raw_tri")


class NMgr:
    __slots__ = ("cell", "head", "table", "one", "uid", "pieces", "world", "links", "navref_uids", "wpn_uid", "wps",
                 "bv", "tail", "record", "index", "body", "stock_navrefs", "stock_comp")


class NFlow:
    __slots__ = ("uid", "name", "record", "index", "body", "points", "width", "head", "tail", "ml_off", "link",
                 "ml_leaf", "tri_offs", "tris", "wp_offs", "wps", "dirty", "_key", "_ti", "new")


def _flow_layout(body):
    """A CrowdFlow entity body -> dict of its points and the byte offsets of its refs, or None (navmesh.parse_flow's
    walk, keeping offsets)."""
    nav = navcut.navmesh_module()
    I = struct.Struct("<I")
    k = body.find(I.pack(nav.CROWDFLOW))
    if k < 0 or I.unpack_from(body, 6)[0] != nav.ENTITY:
        return None
    m = struct.unpack_from("<16f", body, 10)
    k += 4 + 2 + 8
    n = I.unpack_from(body, k)[0]
    k += 4
    local = []
    for _ in range(n):
        if I.unpack_from(body, k + 4)[0] != nav.FLOWPOINT:
            return None
        local.append(struct.unpack_from("<4f", body, k + 8))
        k += 24
    width = struct.unpack_from("<f", body, k)[0]
    k += 4
    lists = []
    for _ in range(2):
        c = I.unpack_from(body, k)[0]
        k += 4
        lists.append([I.unpack_from(body, k + 5 * j + 1)[0] for j in range(c)])
        k += 5 * c
    if I.unpack_from(body, k + 4)[0] != nav.METALINKREF:
        return None
    ml_off = k + 8
    k += 12
    nt = I.unpack_from(body, k)[0]
    k += 4
    tri_offs = [k + 14 * j + 8 for j in range(nt)]
    k += 14 * nt
    nw = I.unpack_from(body, k)[0]
    k += 4
    wp_offs = [k + 12 * j + 8 for j in range(nw)]
    pts = [(p[0] * m[0] + p[1] * m[4] + p[2] * m[8] + m[12], p[0] * m[1] + p[1] * m[5] + p[2] * m[9] + m[13],
            p[0] * m[2] + p[1] * m[6] + p[2] * m[10] + m[14]) for p in local]
    return dict(points=pts, width=width, head=lists[0], tail=lists[1], ml_off=ml_off, tri_offs=tri_offs,
                wp_offs=wp_offs)


def half(z):
    return struct.unpack("<H", struct.pack("<e", z))[0]


def unhalf(h):
    return struct.unpack("<e", struct.pack("<H", h))[0]


class NavWorld:
    """Every NavMeshManager and CrowdFlow of an archive, linked (module docstring)."""

    def __init__(self, path):
        nav = self.nav = navcut.navmesh_module()
        self.path = path
        nm = self.nm = nav.NavMap(path)          # the grid (cells, leaves) and the library's own view
        self.size, self.width = nm.size, nm.width
        self.off_grid = set(nm.off_grid)
        raw_m, raw_f = {}, {}
        self.uids = set()                       # every identity in the archive (fresh entity ids avoid them)
        self.rec_ents = defaultdict(list)        # record -> a few of its Entity bodies (new flows copy FakeCellIndex)
        for rec, i, m, o in nav.managers(path, with_objects=True):
            if m.cell in self.off_grid or nm.records.get(m.cell) != (rec, i):
                continue
            raw_m[m.cell] = (rec, i, m, o["body"])
        import mapwalk
        from forge import Forge
        with Forge(path) as f:
            for rec in f.records:
                if rec.name == "GlobalMetaFile" or not rec.size:
                    continue
                try:
                    p = mapwalk.Payload(rec, f.read(rec))
                except Exception:  # noqa: BLE001
                    continue
                for i, o in enumerate(p.objects):
                    if o.get("has_uid"):
                        self.uids.add(o["uid"])
                    if o["type"] == nav.ENTITY and len(self.rec_ents[rec.name]) < 4:
                        self.rec_ents[rec.name].append(o["body"])
                    if o["type"] == nav.ENTITY and o.get("has_uid") and o["uid"] in nm.flows:
                        raw_f[o["uid"]] = (rec.name, i, o["body"], o.get("name"))
        self.managers = {}
        # objects first, then the links between them
        for cell, (rec, i, m, body) in raw_m.items():
            g = NMgr()
            g.cell, g.head, g.table, g.one, g.uid = cell, m.head, list(m.table), m.one, m.uid
            g.world, g.wpn_uid, g.bv, g.tail = m.world, m.wpn_uid, m.bv, m.tail
            g.record, g.index, g.body = rec, i, body
            g.navref_uids = [u for u, _p, _c in m.navrefs]
            g.stock_comp = (list(m.comp_start), list(m.comp_size))
            g.pieces, g.links, g.wps = [], [], []
            for p in m.pieces:
                q = NPiece()
                q.uid, q.mgr, q.verts, q.idx = p.uid, g, [tuple(v) for v in p.verts], list(p.idx)
                q.mopp, q.mopp_os, q.bv, q.source, q.zone = p.mopp, tuple(p.mopp_os), p.bv, p.source, p.zone
                q.flags, q.component, q.dirty = p.flags, p.component, False
                q._key = q._ti = None
                q.tris = [NTri(t.uid, q) for t in p.tris]
                g.pieces.append(q)
            for ml in m.metalinks:
                L = NLink()
                L.uid, L.mgr, L.safe, L.nav_object, L.next_start = ml.uid, g, ml.safe, ml.nav_object, ml.next_start
                L.b, L.type, L.f = ml.b, ml.type, ml.f
                g.links.append(L)
            for w in m.waypoints:
                W = NWp()
                W.uid, W.mgr, W.f, W.leaf = w.uid, g, w.f, w.w[6]
                W.q = (w.w[3], w.w[4], w.w[5])
                W.flowcode = w.w[0] == FLOW_CODE
                W.raw_tri = (w.w[1], w.w[2]) if W.flowcode else None
                g.wps.append(W)
            self.managers[cell] = g
        for cell, (rec, i, m, body) in raw_m.items():
            g = self.managers[cell]
            for p, q in zip(m.pieces, g.pieces):
                for t, T in zip(p.tris, q.tris):
                    T.nbr = [q.tris[n] if n < 0xFFE0 else n for n in t.nbr]
                    T.edge = [[self._ref_link(cell, r) for r in t.edge_refs(e)] for e in range(3)]
                    T.wps = [self._ref_wp(cell, r) for r in t.waypoints]
            g.stock_navrefs = [(u, self.managers[c].pieces[p]) for u, p, c in m.navrefs]
            for ml, L in zip(m.metalinks, g.links):
                L.tris = [(u, self.managers[c].pieces[p].tris[t]) for u, t, p, c in ml.tris]
                L.objwp = [(ou, wu, self.managers[wc].wps[wi], [self.managers[nm.leaf_cell(nc)].wps[ni] for ni, nc in near])
                           for ou, wu, (wi, wc), near in ml.objwp]
            for w, W in zip(m.waypoints, g.wps):
                W.links = []
                for raw, tgt in w.links:
                    W.links.append((raw, self._ref_link(cell, tgt) if raw & nav.WP_METALINK else self._ref_wp(cell, tgt)))
                if not W.flowcode:
                    tc = nm.neighbour(cell, w.w[0])
                    W.tri = self.managers[tc].pieces[w.w[1]].tris[w.w[2]]
                else:
                    W.tri = None
        self.flows = {}
        for u, f in nm.flows.items():
            if u not in raw_f:
                continue
            rec, i, body, name = raw_f[u]
            lay = _flow_layout(body)
            F = NFlow()
            F.uid, F.name, F.record, F.index, F.body = u, name, rec, i, body
            F.points, F.width, F.head, F.tail = lay["points"], lay["width"], lay["head"], lay["tail"]
            F.ml_off, F.tri_offs, F.wp_offs, F.dirty = lay["ml_off"], lay["tri_offs"], lay["wp_offs"], False
            F.new = False
            mi, leaf = struct.unpack_from("<2H", body, F.ml_off)
            F.ml_leaf = leaf
            if (mi, leaf) == (0xFFFF, 0xFFFF):
                F.link = None
            else:
                mc = nm.leaf_cell(leaf)
                F.link = self.managers[mc].links[mi] if mc in self.managers else ("raw", mi, leaf)
            F.tris, F.wps = [], []
            for off in F.tri_offs:
                t, p, c = struct.unpack_from("<3H", body, off)
                F.tris.append(self.managers[c].pieces[p].tris[t] if c in self.managers else ("raw", t, p, c))
            for off in F.wp_offs:
                wi, wc = struct.unpack_from("<2H", body, off)
                F.wps.append(self.managers[wc].wps[wi] if wc in self.managers else ("raw", wi, wc))
            self.flows[u] = F
        # the component table: one map-wide list of (piece) refs; keep the uids of the NavMeshRef objects in order
        self.comp_uids = {cell: list(g.navref_uids) for cell, g in self.managers.items()}

    # ---- reading refs
    def _ref_link(self, cell, r):
        return self.managers[self.nm.neighbour(cell, r & 15)].links[r >> 4]

    def _ref_wp(self, cell, r):
        return self.managers[self.nm.neighbour(cell, r & 15)].wps[r >> 4]

    # ---- the grid
    def code(self, frm, to):
        """The cellcode of cell `to` seen from cell `frm` (a 3 x 3 neighbourhood)."""
        if frm == to:
            return OWN
        dx = (to % self.width) - (frm % self.width)
        dy = (to // self.width) - (frm // self.width)
        if abs(dx) > 1 or abs(dy) > 1:
            raise ValueError(f"cell {to} is not next to cell {frm}")
        return (dy + 1) * 3 + (dx + 1)

    def near(self, a, b):
        """Can a packed ref in cell a's manager name something in cell b's (the same cell or one of its 8 around)?"""
        return abs((a % self.width) - (b % self.width)) <= 1 and abs((a // self.width) - (b // self.width)) <= 1

    def cell_corner(self, cell):
        return self.nm.cell_corner(cell)

    def cell_at(self, x, y):
        """The cell id of world point (x, y) on the managers' grid (it may have no manager)."""
        col = math.floor(x / self.size) - self.nm.col0
        row = math.floor(y / self.size) - self.nm.row0
        if not (0 <= col < self.width) or row < 0:
            return None
        return row * self.width + col

    def leaf_of(self, cell):
        """The leaf id waypoints in `cell` carry (the same as the cell on AC3's maps)."""
        nm = self.nm
        if nm.leaf_width is None:
            return cell
        x0, y0 = nm.cell_corner(cell)
        col = round(x0 / nm.size) - nm.leaf_col0
        row = round(y0 / nm.size) - nm.leaf_row0
        return row * nm.leaf_width + col

    def wp_pos(self, w):
        nm = self.nm
        c = nm.leaf_corner(w.leaf)
        return (c[0] + w.q[0] / 1024 * nm.size, c[1] + w.q[1] / 1024 * nm.size, unhalf(w.q[2]))

    def set_wp_pos(self, w, p, leaf=None):
        """Place waypoint w at world point p in its manager's cell (leaf and quantised position); a flow waypoint
        carries the leaf of its point (stock: stored in the flow's link manager, its leaf where it stands)."""
        w.leaf = self.leaf_of(w.mgr.cell) if leaf is None else leaf
        c = self.nm.leaf_corner(w.leaf)
        qx = int(round((p[0] - c[0]) / self.size * 1024))
        qy = int(round((p[1] - c[1]) / self.size * 1024))
        w.q = (max(0, min(0xFFFF, qx)), max(0, min(0xFFFF, qy)), half(float(p[2])))

    # ---- editing
    def drop_pieces(self, pieces):
        """Remove pieces and everything hanging off them: their triangles' ground waypoints, every metalink naming
        one of their triangles (a flow's type-5 link only loses those triangles), refs to all of that from triangles,
        waypoints and ties. A flow point on a dropped triangle keeps a None TriangleRef (anchor it again). -> counts."""
        gone_p = {id(p) for p in pieces}
        gone_t = {id(t) for p in pieces for t in p.tris}
        for p in pieces:
            p.mgr.pieces = [q for q in p.mgr.pieces if id(q) not in gone_p]
        gone_l = set()
        for g in self.managers.values():
            keep = []
            for L in g.links:
                if any(id(t) in gone_t for _u, t in L.tris):
                    if L.type == FLOW:
                        L.tris = [(u, t) for u, t in L.tris if id(t) not in gone_t]
                        L.next_start = min(L.next_start, len(L.tris))
                    else:
                        gone_l.add(id(L))
                        continue
                keep.append(L)
            g.links = keep
        gone_w = set()
        for g in self.managers.values():
            gone_w.update(id(w) for w in g.wps if w.tri is not None and id(w.tri) in gone_t)
            g.wps = [w for w in g.wps if id(w) not in gone_w]
        self._scrub(gone_t, gone_l, gone_w)
        for F in self.flows.values():
            F.tris = [None if isinstance(t, NTri) and id(t) in gone_t else t for t in F.tris]
        return dict(pieces=len(gone_p), triangles=len(gone_t), metalinks=len(gone_l), waypoints=len(gone_w))

    def drop_links(self, links):
        gone = {id(L) for L in links}
        for g in self.managers.values():
            g.links = [L for L in g.links if id(L) not in gone]
        self._scrub(set(), gone, set())

    def _scrub(self, gone_t, gone_l, gone_w):
        """Refs to removed triangles / metalinks / waypoints, out of everything left."""
        for g in self.managers.values():
            for p in g.pieces:
                for t in p.tris:
                    for e in range(3):
                        n = t.nbr[e]
                        if isinstance(n, NTri) and id(n) in gone_t:
                            t.nbr[e] = 0xFFE4
                        if t.edge[e] and any(id(L) in gone_l for L in t.edge[e]):
                            t.edge[e] = [L for L in t.edge[e] if id(L) not in gone_l]
                            if not t.edge[e] and t.nbr[e] == 0xFFE3:
                                t.nbr[e] = 0xFFE4
                    if t.wps and any(id(w) in gone_w for w in t.wps):
                        t.wps = [w for w in t.wps if id(w) not in gone_w]
            for L in g.links:
                if L.objwp:
                    L.objwp = [(ou, wu, w, [n for n in near if id(n) not in gone_w]) for ou, wu, w, near in L.objwp
                               if id(w) not in gone_w]
            for w in g.wps:
                if any(id(x) in gone_w or id(x) in gone_l for _r, x in w.links):
                    w.links = [(r, x) for r, x in w.links if id(x) not in gone_w and id(x) not in gone_l]
        for F in self.flows.values():
            if isinstance(F.link, NLink) and id(F.link) in gone_l:
                F.link = None

    def add_piece(self, mgr, verts, idx, source, flags=b"\x00\x00\x01", zone=(0, 0)):
        """A new piece in manager `mgr`: world vertices (x, y, z), triangle corner indices; its triangles' links are
        set by the caller (nbr / edge / wps); MOPP and AABV at writing."""
        nav = self.nav
        p = NPiece()
        p.uid, p.mgr = self.new_uid(), mgr
        p.verts = [(float(v[0]), float(v[1]), float(v[2]), 0.0) for v in verts]
        p.idx = [int(i) for i in idx]
        p.tris = [NTri(self.new_uid(), p) for _ in range(len(p.idx) // 3)]
        p.mopp, p.mopp_os = b"", (0.0, 0.0, 0.0, 1.0)
        p.bv = nav.AABV(self.new_uid(), (0.0,) * 3, (0.0,) * 3, 0)
        p.source, p.zone, p.flags, p.component, p.dirty = (0, source), zone, flags, 0xFF, True
        p._key = p._ti = None
        mgr.pieces.append(p)
        return p

    def add_link(self, mgr, typ, side_a, side_b, b=31, safe_bytes=b"\x03\xff\x3f"):
        """A new metalink in `mgr`: triangles of side A then side B."""
        L = NLink()
        L.uid, L.mgr, L.type, L.b, L.f = self.new_uid(), mgr, typ, b, b"\x00\x00\x00"
        L.safe = [(self.new_uid(), safe_bytes), (self.new_uid(), safe_bytes)]
        L.tris = [(self.new_uid(), t) for t in list(side_a) + list(side_b)]
        L.next_start = len(side_a)
        L.objwp, L.nav_object = [], (0, 0)
        mgr.links.append(L)
        return L

    def add_wp(self, mgr, tri, pos):
        """A new ground waypoint in `mgr` on triangle `tri` at world point `pos`."""
        w = NWp()
        w.uid, w.mgr, w.f, w.links, w.tri, w.flowcode, w.raw_tri = self.new_uid(), mgr, (1, 0), [], tri, False, None
        self.set_wp_pos(w, pos)
        mgr.wps.append(w)
        return w

    @staticmethod
    def cost(d):
        """A waypoint link's raw u16 for a walk of d metres (stock: cost / distance ~ 1.05; low 2 bits flags)."""
        return min(0x3FFF, int(round(d * 1.05 * 128))) << 2

    # ---- new CrowdFlows
    def _codec(self):
        if getattr(self, "_C", None) is None:
            from . import mapdata as M
            self._S = M.schema()
            self._C = M.Codec(self._S)
        return self._S, self._C

    def _fresh_id(self):
        """A fresh entity-level id (u32): not an identity of the archive or the game's DataPC / DataPC_extra /
        CoreFX / Fire, not a navmesh uid given out here, below 0xF0000000; drawn from a generator seeded by the
        archive, so the same build gives the same ids."""
        if getattr(self, "_fresh", None) is None:
            from . import duplicate
            avoid = set(self.uids)
            try:
                from . import gameplay
                avoid |= {u for u in gameplay._global_ids() if isinstance(u, int)}
            except Exception:  # noqa: BLE001 - no live install here: the archive's own ids still avoided
                pass
            self.new_uid()                       # the navmesh uid counter starts (and is then avoided)
            self._fresh = duplicate.FreshIds(avoid, duplicate.seed(self.path, b"FLOW", 0))
            self._taken = set()
        while True:
            u = struct.unpack("<I", self._fresh.take())[0]
            if not (self._uid0 <= u < self._next_uid + 0x100000):
                self._taken.add(u)
                return u

    def flow_template(self):
        """The stock flow new flows are cloned from: the map's most common Filter / bidirectional pair, unrotated."""
        if getattr(self, "_tpl", None) is None:
            _S, C = self._codec()
            from . import mapdata as M
            cands = []
            for F in self.flows.values():
                if F.new:
                    continue
                e = C.decode(F.body).obj
                f = M._obj(e.fields["Components"][0]).fields
                m = np.frombuffer(e.fields["GlobalMatrix"], "<f4").reshape(4, 4)
                cands.append(((f["Filter"], f["BiDirectionalFlag"]), np.allclose(m[:3, :3], np.eye(3)), F))
            if not cands:
                raise ValueError("this map has no CrowdFlow to copy")
            common = Counter(k for k, _r, _F in cands).most_common(1)[0][0]
            self._tpl = next((F for k, r, F in cands if k == common and r),
                             next(F for k, _r, F in cands if k == common))
        return self._tpl

    def _fake_cell_index(self, record):
        """FakeCellIndex for a new entity in `record`: the most common among the record's own flows / entities."""
        _S, C = self._codec()
        vals = Counter()
        bodies = [F.body for F in self.flows.values() if F.record == record and not F.new] or self.rec_ents[record]
        for b in bodies[:8]:
            try:
                v = C.decode(b).obj.fields.get("FakeCellIndex")
            except Exception:  # noqa: BLE001 - an entity the codec can't read: skipped
                continue
            if v is not None:
                vals[v] += 1
        return vals.most_common(1)[0][0] if vals else None

    def add_flow(self, points, mgr, width=4.0, head=(), tail=(), name=None, bidir=None):
        """A new CrowdFlow at world `points` (2 or more), its type-5 metalink and flow waypoints in manager `mgr` (the
        cell record it goes into). head / tail: uids of the flows joined at its first / last point (edit both sides
        with set_joins). TriangleRefs and ground ties: the caller's (set_flow_tris; F.link.objwp near lists). -> NFlow."""
        from ac3codec import Handle
        from . import duplicate
        from . import mapdata as M
        if len(points) < 2:
            raise ValueError("a flow needs 2 points or more")
        _S, C = self._codec()
        tpl = self.flow_template()
        root = C.decode(tpl.body)
        e = root.obj
        mapping = {i: struct.pack("<I", self._fresh_id()) for i in dict.fromkeys(duplicate.object_ids(e))}

        def renew(x):
            if hasattr(x, "id") and x.id in mapping:
                x.id = mapping[x.id]
        duplicate.walk_values(e, renew)
        f = M._obj(e.fields["Components"][0]).fields
        P = np.asarray(points, np.float64)
        origin = np.array([(P[:, 0].min() + P[:, 0].max()) / 2, (P[:, 1].min() + P[:, 1].max()) / 2, P[:, 2].min()])
        m = np.eye(4, dtype="<f4")
        m[3, :3] = origin
        e.fields["GlobalMatrix"] = m.tobytes()
        proto = f["NavFlowPointsLocal"][0]
        pts = []
        for p in P:
            o = copy.deepcopy(proto)
            o.id = struct.pack("<I", self._fresh_id())
            o.fields["FlowPosition"] = struct.pack("<4f", *(p - origin), 0.0)
            pts.append(o)
        f["NavFlowPointsLocal"] = pts
        f["FlowWidth"] = struct.pack("<f", float(width))
        if bidir is not None:
            f["BiDirectionalFlag"] = b"\x01" if bidir else b"\x00"
        f["HeadConnectingNavFlows"] = [Handle(0, struct.pack("<I", u)) for u in head]
        f["TailConnectingNavFlows"] = [Handle(0, struct.pack("<I", u)) for u in tail]
        f["TriangleArray"] = [copy.deepcopy(f["TriangleArray"][0]) for _ in P]
        f["WayPointArray"] = [copy.deepcopy(f["WayPointArray"][0]) for _ in P]
        bv = e.fields["BoundingVolume"].fields
        radius = float(np.linalg.norm(P - origin, axis=1).max())
        bv["Min"], bv["Max"] = struct.pack("<3f", 0.0, 0.0, 0.0), struct.pack("<3f", radius, 1.0, 1.0)
        fci = self._fake_cell_index(mgr.record)
        if fci is not None:
            e.fields["FakeCellIndex"] = fci
        names = {F.name for F in self.flows.values()}
        k = sum(1 for F in self.flows.values() if F.new)
        while name is None or name in names:
            k += 1
            name = f"CF_New_{k:03d}"
        F = NFlow()
        F.uid, F.name, F.record, F.index, F.new, F.dirty = struct.unpack("<I", e.id)[0], name, mgr.record, None, True, True
        F.points = [tuple(float(x) for x in p) for p in P]
        F.width = float(width)
        F._key = F._ti = None
        self._encode_flow(F, root)
        # the type-5 metalink and the flow's own waypoints
        L = self.add_link(mgr, FLOW, [], [])
        L.nav_object = (0, F.uid)
        F.link, F.ml_leaf = L, None
        F.wps = []
        for p in F.points:
            W = NWp()
            W.uid, W.mgr, W.f, W.links, W.tri, W.flowcode, W.raw_tri = self.new_uid(), mgr, (1, 0), \
                [(self.nav.WP_METALINK, L)], None, True, (4095, 65535)
            self.set_wp_pos(W, p, leaf=self.leaf_of(self.cell_at(p[0], p[1])))
            mgr.wps.append(W)
            F.wps.append(W)
        L.objwp = [(self.new_uid(), W.uid, W, []) for W in F.wps]
        F.tris = [None] * len(F.points)
        self.flows[F.uid] = F
        return F

    def _encode_flow(self, F, root):
        _S, C = self._codec()
        F.body = C.encode(root)
        lay = _flow_layout(F.body)
        F.ml_off, F.tri_offs, F.wp_offs = lay["ml_off"], lay["tri_offs"], lay["wp_offs"]
        F.head, F.tail = lay["head"], lay["tail"]

    def set_flow_tris(self, F, tris):
        """A flow's TriangleRefs (one NTri per point) and its type-5 link's triangles (the same, in order)."""
        F.tris = list(tris)
        if isinstance(F.link, NLink):
            uids = [u for u, _t in F.link.tris] + [self.new_uid() for _ in range(max(0, len(tris) - len(F.link.tris)))]
            F.link.tris = [(u, t) for u, t in zip(uids, tris)]
            F.link.next_start = 0

    def set_flow_points(self, F, points, keep):
        """Give flow F new world `points` (a rerouted route): keep[j] = the old point index new point j continues (its
        point object, flow waypoint and ObjectWayPoint kept, moved to the new place), or None for an inserted point
        (fresh point object; a new flow waypoint stored next to the flow's others, linked to its type-5 metalink; a new
        ObjectWayPoint). TriangleRefs: None for inserted points (set_flow_tris); ground ties: the caller's."""
        from . import mapdata as M
        L = F.link
        if not isinstance(L, NLink) or not all(isinstance(W, NWp) for W in F.wps):
            raise ValueError(f"{F.name}: its metalink / waypoints aren't in loaded managers")
        _S, C = self._codec()
        root = C.decode(F.body)
        e = root.obj
        f = M._obj(e.fields["Components"][0]).fields
        m = np.frombuffer(e.fields["GlobalMatrix"], "<f4").reshape(4, 4).astype(np.float64)
        inv = np.linalg.inv(m)
        P = np.asarray(points, np.float64)
        local = np.c_[P, np.ones(len(P))] @ inv
        old_objs = f["NavFlowPointsLocal"]
        objs = []
        for j, k in enumerate(keep):
            if k is None:
                o = copy.deepcopy(old_objs[0])
                o.id = struct.pack("<I", self._fresh_id())
            else:
                o = old_objs[k]
            o.fields["FlowPosition"] = struct.pack("<4f", *local[j, :3], 0.0)
            objs.append(o)
        f["NavFlowPointsLocal"] = objs
        f["TriangleArray"] = [copy.deepcopy(f["TriangleArray"][0]) for _ in P]
        f["WayPointArray"] = [copy.deepcopy(f["WayPointArray"][0]) for _ in P]
        bv = e.fields["BoundingVolume"].fields
        if struct.unpack("<I", bv["Type"])[0] == 1:
            r = float(np.linalg.norm(local[:, :3], axis=1).max())
            mx = struct.unpack("<3f", bv["Max"])
            bv["Max"] = struct.pack("<3f", max(r, mx[0]), mx[1], mx[2])
        # flow waypoints and ObjectWayPoints, in point order; new ones stored after the flow's previous one
        old_wps, old_ow = list(F.wps), {id(o[2]): o for o in L.objwp}
        mgr = L.mgr
        wps, objwp, prev = [], [], None
        for j, k in enumerate(keep):
            if k is None:
                W = NWp()
                W.uid, W.mgr, W.f, W.links, W.tri, W.flowcode, W.raw_tri = self.new_uid(), mgr, (1, 0),                     [(self.nav.WP_METALINK, L)], None, True, (4095, 65535)
                at = mgr.wps.index(prev) + 1 if prev is not None and prev in mgr.wps else                     (mgr.wps.index(old_wps[0]) if old_wps[0] in mgr.wps else len(mgr.wps))
                mgr.wps.insert(at, W)
                ow = (self.new_uid(), W.uid, W, [])
            else:
                W = old_wps[k]
                ow = old_ow.get(id(W), (self.new_uid(), W.uid, W, []))
            self.set_wp_pos(W, P[j], leaf=self.leaf_of(self.cell_at(P[j][0], P[j][1])))
            wps.append(W)
            objwp.append(ow)
            prev = W
        gone = {id(W) for W in old_wps} - {id(W) for W in wps}
        if gone:
            mgr.wps = [W for W in mgr.wps if id(W) not in gone]
            self._scrub(set(), set(), gone)
        L.objwp = objwp
        F.wps = wps
        F.tris = [F.tris[k] if k is not None and k < len(F.tris) else None for k in keep]
        F.points = [tuple(float(x) for x in p) for p in P]
        self._encode_flow(F, root)
        F.dirty = True

    def set_flow_width(self, F, width):
        """A flow's FlowWidth (m: crowds spread across it)."""
        from . import mapdata as M
        _S, C = self._codec()
        root = C.decode(F.body)
        M._obj(root.obj.fields["Components"][0]).fields["FlowWidth"] = struct.pack("<f", float(width))
        self._encode_flow(F, root)
        F.width = float(width)
        F.dirty = True

    def flow_active(self, F):
        from . import mapdata as M
        _S, C = self._codec()
        return bytes(M._obj(C.decode(F.body).obj.fields["Components"][0]).fields["Active"]) != b"\x00"

    def set_flow_active(self, F, active):
        """A flow's Active flag (every stock flow on AC3's 7 maps: 1); 0 = crowds don't use it."""
        from . import mapdata as M
        _S, C = self._codec()
        root = C.decode(F.body)
        M._obj(root.obj.fields["Components"][0]).fields["Active"] = b"\x01" if active else b"\x00"
        self._encode_flow(F, root)
        F.dirty = True

    def set_joins(self, F, head=None, tail=None):
        """Replace a flow's Head (first point) / Tail (last point) list of joined flow uids (None = keep)."""
        from ac3codec import Handle
        from . import mapdata as M
        _S, C = self._codec()
        root = C.decode(F.body)
        f = M._obj(root.obj.fields["Components"][0]).fields
        if head is not None:
            f["HeadConnectingNavFlows"] = [Handle(0, struct.pack("<I", u)) for u in head]
        if tail is not None:
            f["TailConnectingNavFlows"] = [Handle(0, struct.pack("<I", u)) for u in tail]
        self._encode_flow(F, root)
        F.dirty = True

    # ---- writing
    def components(self):
        """{NPiece: component} - pieces joined by type-0 seams; groups of two or more numbered by size (largest 0, as
        stock), a piece alone 0xFF; and the groups in order."""
        par = {}

        def f(x):
            par.setdefault(x, x)
            while par[x] is not x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        pieces = [p for g in self.managers.values() for p in g.pieces]
        for p in pieces:
            f(p)
        for g in self.managers.values():
            for L in g.links:
                if L.type == SEAM:
                    ps = list({id(t.piece): t.piece for _u, t in L.tris}.values())
                    for q in ps[1:]:
                        a, b = f(ps[0]), f(q)
                        if a is not b:
                            par[a] = b
        groups = defaultdict(list)
        for p in pieces:
            groups[id(f(p))].append(p)
        big = sorted((v for v in groups.values() if len(v) > 1), key=len, reverse=True)
        comp = {p: 0xFF for p in pieces}
        for k, v in enumerate(big):
            for p in v:
                comp[p] = k
        return comp, big

    def serialize(self, keep_components=False):
        """-> ({cell: manager body}, {flow uid: entity body}) for everything (unchanged ones byte-identical)."""
        nav = self.nav
        pos_piece, pos_tri, pos_link, pos_wp = {}, {}, {}, {}
        for cell, g in self.managers.items():
            for pi, p in enumerate(g.pieces):
                pos_piece[id(p)] = pi
                for ti, t in enumerate(p.tris):
                    pos_tri[id(t)] = (ti, pi, cell)
            for li, L in enumerate(g.links):
                pos_link[id(L)] = (li, cell)
            for wi, w in enumerate(g.wps):
                pos_wp[id(w)] = (wi, cell)

        def packed(frm, pos):
            i, c = pos
            if i >= 4096:
                raise ValueError(f"index {i} in cell {c} does not fit a packed ref (12 bits)")
            return (i << 4) | self.code(frm, c)

        if keep_components:
            comp, groups = None, None
        else:
            comp, groups = self.components()
        out = {}
        for cell, g in self.managers.items():
            m = nav.Manager.__new__(nav.Manager)
            m.head, m.one, m.uid, m.world, m.wpn_uid, m.cell, m.tail = g.head, g.one, g.uid, g.world, g.wpn_uid, \
                g.cell, g.tail
            m.pieces = []
            for p in g.pieces:
                if p.dirty:
                    self._retree(p)
                tris = []
                for ti, t in enumerate(p.tris):
                    nbr = [pos_tri[id(n)][0] if isinstance(n, NTri) else n for n in t.nbr]
                    refs = []
                    counts = []
                    for e in range(3):
                        counts.append(len(t.edge[e]))
                        refs += [packed(cell, pos_link[id(L)]) for L in t.edge[e]]
                    counts.append(len(t.wps))
                    refs += [packed(cell, pos_wp[id(w)]) for w in t.wps]
                    tris.append(nav.Triangle(t.uid, ti, nbr, counts, refs))
                comp_v = p.component if comp is None else comp[p]
                m.pieces.append(nav.Piece(uid=p.uid, verts=p.verts, tris=tris, idx=p.idx, mopp=p.mopp,
                                          mopp_os=p.mopp_os, bv=p.bv, source=p.source, zone=p.zone, flags=p.flags,
                                          component=comp_v))
            m.metalinks = []
            for L in g.links:
                tris = [(u,) + pos_tri[id(t)] for u, t in L.tris]
                objwp = [(ou, wu, pos_wp[id(w)], [(pos_wp[id(n)][0], self.leaf_of(pos_wp[id(n)][1])) for n in near])
                         for ou, wu, w, near in L.objwp]
                m.metalinks.append(nav.MetaLink(uid=L.uid, safe=L.safe, tris=tris, objwp=objwp,
                                                nav_object=L.nav_object, next_start=L.next_start, b=L.b, type=L.type,
                                                f=L.f))
            m.waypoints = []
            for w in g.wps:
                links = []
                for raw, tgt in w.links:
                    links.append((raw, packed(cell, pos_link[id(tgt)] if isinstance(tgt, NLink) else pos_wp[id(tgt)])))
                if w.flowcode:
                    ww = (FLOW_CODE,) + w.raw_tri
                else:
                    ti, pi, tc = pos_tri[id(w.tri)]
                    ww = (self.code(cell, tc), pi, ti)
                m.waypoints.append(nav.WayPoint(w.uid, w.f, links, ww + w.q + (w.leaf,)))
            m.bv = g.bv
            # the component table
            if comp is None:
                m.navrefs = [(u, pos_piece[id(p)], p.mgr.cell) for u, p in g.stock_navrefs]
                m.comp_start, m.comp_size = g.stock_comp
            else:
                mine = max([comp[p] for p in g.pieces if comp[p] != 0xFF], default=0)
                n_sets = min(len(groups), mine + 1) if groups else 0
                navrefs, starts, sizes = [], [], []
                uids = self.comp_uids.setdefault(cell, [])
                for k in range(n_sets):
                    members = sorted(groups[k], key=lambda p: (pos_piece[id(p)], p.mgr.cell))
                    starts.append(len(navrefs))
                    sizes.append(len(members))
                    for p in members:
                        navrefs.append((pos_piece[id(p)], p.mgr.cell))
                while len(uids) < len(navrefs):
                    uids.append(self.new_uid())
                m.navrefs = [(uids[j], pi, c) for j, (pi, c) in enumerate(navrefs)]
                m.comp_start, m.comp_size = starts, sizes
            m.table = table_for(g.table, m)
            out[cell] = m.to_bytes()
        flows = {}
        for u, F in self.flows.items():
            b = bytearray(F.body)
            if F.link is not None and not isinstance(F.link, tuple):
                li, lc = pos_link[id(F.link)]
                struct.pack_into("<2H", b, F.ml_off, li, self.leaf_of(lc))
            for off, t in zip(F.tri_offs, F.tris):
                if isinstance(t, NTri):
                    struct.pack_into("<3H", b, off, *pos_tri[id(t)])
            for off, w in zip(F.wp_offs, F.wps):
                if isinstance(w, NWp):
                    struct.pack_into("<2H", b, off, *pos_wp[id(w)])
            flows[u] = bytes(b)
        return out, flows

    def _retree(self, p):
        """A changed piece: MOPP over its triangles, its offset / scale, its AABV."""
        v = np.array([x[:3] for x in p.verts], np.float32)
        tri = np.array(p.idx, np.int64).reshape(-1, 3)
        code, os_ = mopp.build(v, tri)
        reached, _h, _s = mopp.walk(code)
        assert sorted(t for t, _p in reached) == list(range(len(tri))), "navmesh MOPP misses triangles"
        p.mopp, p.mopp_os = bytes(code), tuple(os_)
        lo, hi = v.min(0), v.max(0)
        p.bv = self.nav.AABV(p.bv.uid, tuple(float(x) for x in lo), tuple(float(x) for x in hi), p.bv.t)
        p.dirty = False

    def new_uid(self):
        """A fresh object uid for new navmesh objects (above every uid in the archive's managers and flows)."""
        if not hasattr(self, "_next_uid"):
            hi = 0
            for g in self.managers.values():
                hi = max([hi, g.uid, g.wpn_uid] + [u for u in g.navref_uids]
                         + [p.uid for p in g.pieces] + [t.uid for p in g.pieces for t in p.tris]
                         + [L.uid for L in g.links] + [w.uid for w in g.wps])
            self._next_uid = self._uid0 = hi + 1
            if self._next_uid >= 0xF0000000:
                raise ValueError("no room for fresh uids")
        while self._next_uid in getattr(self, "_taken", ()):
            self._next_uid += 1
        u = self._next_uid
        self._next_uid += 1
        return u

    def write(self, src, dest, log=print, keep_components=False):
        """Write every manager and flow (those that changed) into a copy of `src`. -> (changed records, same)."""
        from . import archive
        from . import duplicate
        mgr_bodies, flow_bodies = self.serialize(keep_components=keep_components)
        nav = self.nav
        by_rec = {(g.record, g.index): mgr_bodies[c] for c, g in self.managers.items()}
        fl_rec = {(F.record, F.index): flow_bodies[u] for u, F in self.flows.items() if not F.new}
        insert, listed = defaultdict(list), defaultdict(list)
        for u, F in self.flows.items():
            if F.new:
                insert[F.record].append((u, duplicate.entity_record_bytes(nav.ENTITY, F.name, flow_bodies[u])))
                listed[F.record].append(struct.pack("<I", u))
        gcdb = C = None
        if insert:
            S, C = self._codec()
            gcdb = next(h for h in S.types_by_hash if S.name_of(h) == "GridCellDataBlock")
        counter = defaultdict(int)

        def fn(rec_name, o):
            i = counter[rec_name]
            counter[rec_name] += 1
            if o["type"] == nav.MANAGER:
                return by_rec.get((rec_name, i))
            if o["type"] == gcdb and rec_name in listed:
                return duplicate.list_in_cell(C, o["body"], listed.pop(rec_name))
            return fl_rec.get((rec_name, i))
        out = archive.rewrite(src, dest, fn, log=log, insert=dict(insert), after_types=(nav.ENTITY,))
        if listed:
            raise ValueError(f"records with no GridCellDataBlock can't take new flows: {sorted(listed)}")
        return out


def table_for(old, m):
    """The allocation table for Manager m: the manager's own entries in their own order, each count recomputed; an
    entry it lacks added (in stock order) when its count is not 0; a new manager (old = None) in stock order, zero
    counts left out (as stock leaves out the ObjectWayPoint entry of a manager without ties)."""
    P = m.pieces
    c = {(NAVMESH, 0xB1D): len(P),
         (0, 0x698): sum(len(p.verts) for p in P),
         (TRIANGLE, 0xB18): sum(len(p.tris) for p in P),
         (0, 0x5): sum(len(t.refs) for p in P for t in p.tris) + 2 * sum(len(w.links) for w in m.waypoints)
         + 2 * sum(len(o[3]) for ml in m.metalinks for o in ml.objwp),
         (0, 0x298): sum(len(p.idx) for p in P),
         (0, 0x198): sum(len(p.mopp) for p in P),
         (METALINK, 0xB1D): len(m.metalinks),
         (TRIREF, 0xB1D): sum(len(ml.tris) for ml in m.metalinks),
         (OBJWP, 0xB1D): sum(len(ml.objwp) for ml in m.metalinks),
         (NAVMESHREF, 0xB1D): len(m.navrefs),
         (0, 0x29D): len(m.comp_start) + len(m.comp_size),
         (WAYPOINT, 0xB1D): len(m.waypoints)}
    old = list(old or [])
    have = {(cl, k) for cl, _z, k, _n in old}
    out = [(cl, z, k, c[(cl, k)]) for cl, z, k, _n in old]
    for j, (cl, k) in enumerate(TABLE_ORDER):
        if (cl, k) not in have and c[(cl, k)]:
            before = {e for e in TABLE_ORDER[:j]}
            at = max([i + 1 for i, e in enumerate(out) if (e[0], e[2]) in before], default=0)
            out.insert(at, (cl, 0, k, c[(cl, k)]))
            have.add((cl, k))
    return out
