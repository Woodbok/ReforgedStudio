"""forgelib/maps/realtree_convert.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import math
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import realtree
OUT = os.path.join(ROOT, 'out', 'maps')
AC3_TUNING_HEAD = struct.pack('<7f', 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0)
ACR_SEL = {0: (0, 1), 1: (1, 0), 2: (0, 0), 3: (1, 1)}
AC3_SEL = {(1, 1): 0, (0, 1): 1, (1, 0): 2, (0, 0): 3}
SELECTORS = 'remap'
CARD_LIFT = False

def al(n):
    return n + 15 & ~15

def f3(b, o):
    return struct.unpack_from('<3f', b, o)

def sub(a, b):
    return tuple((x - y for x, y in zip(a, b)))

def dot(a, b):
    return sum((x * y for x, y in zip(a, b)))

def mul(a, k):
    return tuple((x * k for x in a))

def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

def unit(a):
    n = math.sqrt(dot(a, a))
    return tuple((x / n for x in a)) if n > 1e-12 else None

def any_perp(a):
    return unit(cross(a, (0.0, 0.0, 1.0) if abs(a[2]) < 0.9 else (1.0, 0.0, 0.0)))

class Acr:

    def __init__(self, m):
        self.m = m
        arrays, end = realtree.layout(138, m)
        if end != len(m):
            raise ValueError('ACR main chunk not fully walked')
        self.at = {a[0]: a[1] for a in arrays}
        self.u32 = lambda o: struct.unpack_from('<I', m, o)[0]
        self.nodes, self.branches, self.fronds = (self.u32(16), self.u32(28), self.u32(40))
        self.shapes = self.u32(384)
        if self.u32(52) or struct.unpack_from('<I', m, self.at['blockA'] + 8)[0]:
            raise ValueError('elements / soft-body data: not handled (no shipped tree has any)')

def branch_ranks(first, count, parent):
    n = len(first)

    def owner(node):
        for b in range(n):
            if first[b] <= node <= first[b] + count[b]:
                return b
        return None
    kids = {b: [] for b in range(n)}
    roots = []
    for b in range(n):
        (roots if parent[b] == -1 else kids[owner(parent[b])]).append(b)
    order = []
    stack = sorted(roots, key=lambda c: first[c], reverse=True)
    while stack:
        b = stack.pop()
        order.append(b)
        stack.extend(sorted(kids[b], key=lambda c: parent[c], reverse=True))
    rank = [0] * n
    for i, b in enumerate(order):
        rank[b] = i
    return rank

def convert_main(m):
    A = Acr(m)
    NN, NB, NF, NS = (A.nodes, A.branches, A.fronds, A.shapes)
    root = bytearray(608)
    body = bytearray()
    cur = 608

    def place(size, align=True):
        nonlocal cur
        at = cur
        cur += al(size) if align else size
        return at
    o_block = place(48, align=False)
    o_r16c = place(56 * NS) if NS else None
    o_pairs = [place(8 * 3) for _ in range(NS)]
    o_r28a = place(40 * NB)
    o_r28b = place(44 * NB)
    o_r10a = place(88 * NN)
    o_r10b = place(108 * NN)
    o_r40 = place(64 * NF, align=False) if NF else None
    n16 = sum((max(struct.unpack_from('<h', m, A.at['r10.a'] + 76 * i + 46)[0], 0) for i in range(NN)))
    o_u16 = place(2 * n16)
    o_A10 = place(32 * NN, align=False)
    o_A18 = place(32 * NF, align=False)
    place(0)
    o_r40c = [place(12 * 3) for _ in range(NF)]
    out = bytearray(cur)
    del body

    def w(fmt, o, *v):
        struct.pack_into(fmt, out, o, *v)
    out[0:12] = m[0:12]
    w('<I', 16, NN)
    w('<I', 40, NB)
    w('<I', 64, NF)
    out[108:124] = m[72:88]
    w('<2f', 124, 1.0, 1.0)
    out[152:288] = m[96:232]
    out[288:316] = AC3_TUNING_HEAD
    out[316:364] = m[336:384]
    w('<I', 364, NS)
    w('<2f', 376, 128.0, 128.0)
    w('<f', 404, 1.0)
    out[408:544] = m[392:528]
    root_ok = bytes(out[:608])
    del root_ok
    w('<3I', o_block, NN, NF, 0)
    first, count, parent = ([], [], [])
    for b in range(NB):
        o = A.at['r1c.a'] + 40 * b
        f0, c0 = struct.unpack_from('<ii', m, o)
        first.append(f0)
        count.append(c0)
        parent.append(struct.unpack_from('<i', m, o + 12)[0])
    rank = branch_ranks(first, count, parent)
    for b in range(NB):
        o = A.at['r1c.a'] + 40 * b
        d = o_r28a + 40 * b
        out[d:d + 8] = m[o:o + 8]
        out[d + 8:d + 36] = m[o + 12:o + 40]
        w('<i', d + 36, rank[b])
        ob, db = (A.at['r1c.b'] + 44 * b, o_r28b + 44 * b)
        w('<f', db, struct.unpack_from('<f', m, ob)[0])
        for k in range(1, 11):
            w('<f', db + 4 * k, 1.0)
    owner_first = {}
    for b in range(NB):
        for nd in range(first[b], first[b] + count[b] + 1):
            owner_first[nd] = first[b]
    for i in range(NN):
        o, d = (A.at['r10.a'] + 76 * i, o_r10a + 88 * i)
        out[d:d + 48] = m[o:o + 48]
        out[d + 56:d + 72] = m[o + 52:o + 68]
        out[d + 72:d + 80] = m[o + 68:o + 76]
        w('<i', d + 80, i - owner_first.get(i, i))
        ob, db = (A.at['r10.b'] + 32 * i, o_r10b + 108 * i)
        radius, seg, mass = struct.unpack_from('<3f', m, ob + 8)
        w('<f', db + 12, radius)
        for k in range(18):
            w('<f', db + 16 + 4 * k, 1.0)
        w('<f', db + 88, seg)
        w('<f', db + 104, mass if mass > 0 else 1.0)
    src16 = A.at['r10.u16'] if 'r10.u16' in A.at else None
    if n16:
        out[o_u16:o_u16 + 2 * n16] = m[src16:src16 + 2 * n16]
    out[o_A10:o_A10 + 32 * NN] = m[A.at['B0c']:A.at['B0c'] + 32 * NN]
    out[o_A18:o_A18 + 32 * NF] = m[A.at['B10']:A.at['B10'] + 32 * NF]
    for k in range(NS):
        o, d = (A.at['r180'] + 52 * k, o_r16c + 56 * k)
        uv = [struct.unpack_from('<2f', m, o + 4 + 8 * c) for c in range(4)]
        out[d] = m[o]
        w('<2f', d + 4, *uv[0])
        w('<I', d + 24, 3)
        out[d + 28:d + 40] = m[o + 36:o + 48]
        out[d + 40:d + 44] = m[o + 48:o + 52]
        for j, c in enumerate((2, 1, 3)):
            w('<2f', o_pairs[k] + 8 * j, *uv[c])
    for j in range(NF):
        o, d = (A.at['r28'] + 92 * j, o_r40 + 64 * j)
        s = struct.unpack_from('<f', m, o + 8)[0]
        a = f3(m, o + 12)
        c1, c2, c3 = (f3(m, o + 48), f3(m, o + 60), f3(m, o + 72))
        lat = unit(sub(c2, mul(a, dot(c2, a)))) or unit(sub(mul(c3, -1), mul(a, -dot(c3, a)))) or any_perp(a)
        b2 = cross(a, lat)
        out[d:d + 8] = m[o:o + 8]
        w('<f', d + 8, s)
        w('<3f', d + 12, *a)
        w('<3f', d + 24, *b2)
        out[d + 36:d + 48] = m[o + 36:o + 48]
        out[d + 56] = 0
        out[d + 57:d + 60] = m[o + 84:o + 87]
        for c in range(4):
            sel = struct.unpack_from('<b', m, o + 87 + c)[0]
            if SELECTORS == 'acr':
                out[d + 60 + c] = sel if 0 <= sel <= 3 else 3
            else:
                out[d + 60 + c] = AC3_SEL[ACR_SEL.get(sel, (0, 0))] if sel in ACR_SEL else 3
        for t, cv in enumerate((c2, c1, c3)):
            w('<3f', o_r40c[j] + 12 * t, *cv)
    if CARD_LIFT and NF:
        cards = [j for j in range(NF) if out[o_r40 + 64 * j + 58] == 0]
        zs = sorted((struct.unpack_from('<f', out, o_A18 + 32 * j + 8)[0] for j in range(NF) if j not in cards))
        med = zs[len(zs) // 2] if zs else None
        for j in cards:
            za, zst = (o_A18 + 32 * j + 8, o_r40 + 64 * j + 44)
            old = struct.unpack_from('<f', out, za)[0]
            if med is not None and med > old:
                w('<f', za, med)
                w('<f', zst, struct.unpack_from('<f', out, zst)[0] + (med - old))
    return bytes(out)

def convert_blob(blob, materials):
    h = realtree.header(blob)
    if h['version'] != 138:
        raise ValueError(f"not an ACR tree (version {h['version']:#x})")
    m = blob[h['hlen'] + h['opt']:h['hlen'] + h['opt'] + h['main']]
    main = convert_main(m)
    hdr = bytearray(72 * 4)
    w1 = struct.unpack_from('<I', blob, 4)[0]
    struct.pack_into('<I', hdr, 0, 271)
    struct.pack_into('<I', hdr, 8, w1 & 65535 | w1 & 4294901760)
    struct.pack_into('<I', hdr, 20, len(main))
    name = h['name'].encode('latin1')[:255]
    hdr[24:24 + len(name)] = name
    tex = bytearray(struct.pack('<I', len(materials)))
    for part, uid in sorted(materials.items()):
        s = f'85C817C3,{uid:X}'.encode('latin1')
        tex += struct.pack('<II', part, len(s)) + s
    return bytes(hdr) + main + bytes(tex)

def verify(acr_blob, ac3_blob):
    probs = []
    h = realtree.header(ac3_blob)
    if h['version'] != 271:
        probs.append('version')
    m3 = ac3_blob[h['hlen'] + h['opt']:h['hlen'] + h['opt'] + h['main']]
    arrays, end = realtree.layout(271, m3)
    if end != len(m3):
        probs.append(f'AC3 walk ends at {end}, chunk {len(m3)}')
    tex, tail = realtree.textures(ac3_blob, h)
    if tail != len(ac3_blob):
        probs.append('texture table does not end the blob')
    ha = realtree.header(acr_blob)
    ma = acr_blob[ha['hlen'] + ha['opt']:ha['hlen'] + ha['opt'] + ha['main']]
    A = Acr(ma)
    at3 = {x[0]: x[1] for x in arrays}
    u = lambda b, o: struct.unpack_from('<I', b, o)[0]
    if (u(m3, 16), u(m3, 40), u(m3, 64)) != (A.nodes, A.branches, A.fronds):
        probs.append('counts')
    if m3[at3['A10']:at3['A10'] + 32 * A.nodes] != ma[A.at['B0c']:A.at['B0c'] + 32 * A.nodes]:
        probs.append('skeleton nodes')
    for j in range(A.fronds):
        o3 = at3['r40'] + 64 * j
        s = struct.unpack_from('<f', m3, o3 + 8)[0]
        a, b2 = (f3(m3, o3 + 12), f3(m3, o3 + 24))
        if abs(dot(a, b2)) > 0.0001:
            probs.append(f'frond {j}: normal not orthogonal')
            break
        side = mul(unit(cross(b2, a)), s)
        c2 = f3(ma, A.at['r28'] + 92 * j + 60)
        c2p = sub(c2, mul(a, dot(c2, a)))
        if dot(side, c2p) < 0.9 * dot(c2p, c2p) - 1e-06:
            probs.append(f"frond {j}: side corner does not follow ACR's corner 2")
            break
        if SELECTORS == 'acr':
            sa = [x if 0 <= x <= 3 else 3 for x in struct.unpack_from('<4b', ma, A.at['r28'] + 92 * j + 87)]
            if list(m3[o3 + 60:o3 + 64]) != sa:
                probs.append(f"frond {j}: corner selectors are not ACR's")
                break
    return probs

def materials_of(body):
    sub_id = struct.pack('<I', 4270640152)
    out = {}
    k = body.find(sub_id)
    while k >= 0:
        if body[k + 4:k + 6] == b'\x01\x00':
            mat, part = struct.unpack_from('<II', body, k + 6)
            out.setdefault(part, mat)
        k = body.find(sub_id, k + 4)
    return out

def convert_body(body):
    size_at, blob = realtree.blob_of(body)
    mats = materials_of(body)
    new = convert_blob(blob, mats)
    probs = verify(blob, new)
    if probs:
        raise ValueError('; '.join(probs))
    out = body[:size_at] + struct.pack('<I', len(new)) + new + body[size_at + 4 + len(blob):]
    return (out, f"{realtree.header(blob)['name']}: {len(blob)} -> {len(new)} B, materials {', '.join((f'{p}:{u:#x}' for p, u in sorted(mats.items())))}")

def refs(body):
    return [(i, struct.unpack_from('<I', body, i + 2)[0]) for i in range(len(body) - 5) if body[i] == 1 and body[i + 1] in (0, 1)]

def acr_colour(source, objs, cls):
    b = source['body']
    for off, u in refs(b):
        if u in objs and objs[u]['type'] == cls['TextureMap']:
            v = struct.unpack_from('<4f', b, off + 2 + 16)
            if not all((0.0 <= x <= 4.0 for x in v)):
                raise ValueError(f"{source['name']}: no colour after its first texture ({v})")
            return v
    raise ValueError(f"{source['name']}: no TextureMap reference")

def fresh_uid(name, taken):
    h = 2166136261
    for c in name.encode('latin1'):
        h = (h ^ c) * 16777619 & 4294967295
    while h in taken or h < 65536:
        h = h * 16777619 + 1 & 4294967295
    taken.add(h)
    return h

def clone_material(donor, source, objs, cls, new_uid, tint=None):
    body = bytearray(donor['body'])
    body[2:6] = struct.pack('<I', new_uid)
    if tint is not None:
        if len(body) != 209:
            raise ValueError(f"{donor['name']}: {len(body)} B, the colour offset is only known for 209-byte bodies")
        if tint == 'acr':
            struct.pack_into('<4f', body, len(body) - 16, *acr_colour(source, objs, cls))
        else:
            struct.pack_into('<3f', body, len(body) - 16, *tint)
    ts = cls['TextureSet']
    dset = next((u for _o, u in refs(donor['body']) if u in objs and objs[u]['type'] == ts))
    sset = next((u for _o, u in refs(source['body']) if u in objs and objs[u]['type'] == ts))
    dslots = [u for _o, u in refs(objs[dset]['body'])]
    sslots = [u for _o, u in refs(objs[sset]['body'])]
    kept = []
    for off, u in refs(donor['body']):
        if u == dset:
            struct.pack_into('<I', body, off + 2, sset)
        elif u in dslots:
            k = dslots.index(u)
            if k < len(sslots):
                struct.pack_into('<I', body, off + 2, sslots[k])
            else:
                kept.append(objs[u]['name'])
    return (source['name'] + '_AC3', bytes(body), kept)

def check_all():
    import pickle
    d = pickle.load(open(os.path.join(OUT, '_rt_acr_blobs.pkl'), 'rb'))
    bad = 0
    for name, blob in d.items():
        if isinstance(blob, (tuple, list)):
            blob = [x for x in blob if isinstance(x, (bytes, bytearray))][0]
        try:
            new = convert_blob(blob, {0: 286331153, 1: 572662306})
            probs = verify(blob, new)
        except Exception as e:
            probs = [repr(e)[:120]]
        bad += bool(probs)
        print(f"  {('OK ' if not probs else '!! ')}{name:34} {len(blob):7} -> {(len(new) if not probs else '-'):>7}  {'; '.join(probs)}")
    print(f'{len(d) - bad} / {len(d)} ACR trees convert and verify')
    return bad == 0

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--src')
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--prefix')
    ap.add_argument('--bark-donor', type=lambda v: int(v, 0), help='AC3 tree material cloned for part 0 (trunk)')
    ap.add_argument('--leaf-donor', type=lambda v: int(v, 0), help='AC3 tree material cloned for the other parts')
    ap.add_argument('--into', default='Cell01364_DataBlock', help='the root cell the new materials go into')
    ap.add_argument('--tint', type=lambda v: v if v == 'acr' else tuple((float(x) for x in v.split(','))), help="R,G,B for every clone's colour, or acr for each ACR material's own (default: the donor's)")
    a = ap.parse_args(argv)
    if a.check:
        raise SystemExit(0 if check_all() else 1)
    import mapwalk
    import convert
    from datafile import compress, decompress
    from forge import Forge
    from forge_write import ArchiveImage, set_payload, write
    cls = convert.class_ids()
    rtm = cls['RealTreeMesh']
    src = os.path.join(OUT, a.src, a.archive)
    image = ArchiveImage.read(src)
    report, changed, done = ([], {}, 0)
    with Forge(src) as f:
        raw_before = {r.index: f.read(r) for r in f.records}
        payloads = {}
        for r in f.records:
            if not r.size or r.name == 'GlobalMetaFile':
                continue
            try:
                payloads[r.index] = mapwalk.Payload(r, raw_before[r.index])
            except Exception:
                continue
    objs = {o['uid']: o for p in payloads.values() for o in p.objects if o.get('uid') is not None}
    donor_of = lambda part: a.bark_donor if part == 0 else a.leaf_donor
    new_mats, clones = ({}, [])
    if a.bark_donor or a.leaf_donor:
        import inventory
        taken = set(objs) | set(inventory.universe('ACR')) | set(inventory.universe('AC3'))
        for p in payloads.values():
            for o in p.objects:
                if o['type'] != rtm or realtree.header(realtree.blob_of(o['body'])[1])['version'] != 138:
                    continue
                for part, mu in sorted(materials_of(o['body']).items()):
                    if (mu, donor_of(part)) in new_mats:
                        continue
                    nu = fresh_uid(objs[mu]['name'] + '_AC3', taken)
                    name, body, kept = clone_material(objs[donor_of(part)], objs[mu], objs, cls, nu, a.tint)
                    new_mats[mu, donor_of(part)] = nu
                    clones.append(dict(objs[donor_of(part)], name=name, body=body, uid=nu, serial=nu))
                    report.append(f"material {name} ({nu:#010x}) = {objs[donor_of(part)]['name']} with {objs[mu]['name']}'s textures" + (f"; kept the donor's {kept}" if kept else ''))
    clone_uids = {c['uid'] for c in clones}
    sub_id = struct.pack('<I', 4270640152)
    for idx, p in payloads.items():
        objs_out, hit = ([], False)
        for o in p.objects:
            body = o['body']
            if o['type'] == rtm and realtree.header(realtree.blob_of(body)[1])['version'] == 138:
                if new_mats:
                    b = bytearray(body)
                    k = b.find(sub_id)
                    while k >= 0:
                        if b[k + 4] == 1 and b[k + 5] == 0:
                            mu, part = struct.unpack_from('<II', b, k + 6)
                            struct.pack_into('<I', b, k + 6, new_mats[mu, donor_of(part)])
                        k = b.find(sub_id, k + 4)
                    body = bytes(b)
                body, line = convert_body(body)
                report.append(f'{p.rec.name}: {line}')
                hit = True
                done += 1
            objs_out.append((o['serial'], mapwalk.object_bytes(o, body), o['filter']))
        if p.rec.name == a.into and clones:
            g = p.objects[0]['body']
            n = struct.unpack_from('<I', g, 10)[0]
            end = 14 + 6 * n
            add = b''.join((bytes([1, 0]) + struct.pack('<I', c['uid']) for c in clones))
            g = g[:10] + struct.pack('<I', n + len(clones)) + g[14:end] + add + g[end:]
            objs_out[0] = (p.objects[0]['serial'], mapwalk.object_bytes(p.objects[0], g), p.objects[0]['filter'])
            objs_out += [(c['serial'], mapwalk.object_bytes(c), None) for c in clones]
            hit = True
        if hit:
            d = mapwalk.rebuild(objs_out, p.filter_table)
            payload = compress(d, first_chunk=mapwalk.first_chunk(d))
            assert decompress(payload) == d
            set_payload(image, idx, payload)
            changed[idx] = True
    out_dir = os.path.join(OUT, f'{a.prefix}_' + a.src.split('_', 1)[1])
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, a.archive)
    size = write(dest, image)
    left = bad_mat = 0
    placed = {}
    with Forge(dest) as f:
        others = all((f.read(r) == raw_before[r.index] for r in f.records if r.index not in changed))
        for idx in changed:
            q = mapwalk.Payload(f.records[idx], f.read(f.records[idx]))
            for o in q.objects:
                if o.get('uid') in clone_uids:
                    placed.setdefault(o['uid'], []).append(f.records[idx].name)
                if o['type'] == rtm:
                    _, blob = realtree.blob_of(o['body'])
                    h = realtree.header(blob)
                    if h['version'] != 271:
                        left += 1
                    elif clones:
                        tex = dict(realtree.textures(blob, h)[0])
                        subs = materials_of(o['body'])
                        want = {pt: f'85C817C3,{u:X}' for pt, u in subs.items()}
                        bad_mat += any((u not in clone_uids for u in subs.values())) or tex != want
    once = all((placed.get(c['uid']) == [a.into] for c in clones))
    ok = done > 0 and left == 0 and others and (bad_mat == 0) and once
    report.append('corner selectors: ' + ("ACR's (checked in every frond)" if SELECTORS == 'acr' else 'remapped by byte pair') + ("; crown cards lifted to the crown's median frond height" if CARD_LIFT else ''))
    report.append(f"verify: {done} RealTreeMesh converted to 0x10F (each walked and checked); ACR trees left {left}; {len(clones)} AC3 tree materials, each defined once in {a.into} {once}; trees whose sub-meshes / texture table don't name them {bad_mat}; every other record byte-identical {others}: {('OK' if ok else 'FAILED')}")
    with open(os.path.join(out_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print(f'wrote {dest} ({size / 1000000.0:.1f} MB)')
    print('\n'.join(report[-6:]))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
