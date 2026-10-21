"""forgelib/crossmaps/acb_chasebreakers.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import re
import struct
import sys
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acb
acb.patch()
import mapwalk
import xrefs
from forge import Forge
PAIRED_MAPS = (('DataPC_AC2MP_Firenze.forge', 'DataPC_AC2MP_Firenze_dlc.forge'), ('DataPC_AC2MP_MtStMichel_dlc.forge', 'DataPC_AC2MP_MtStMichel.forge'), ('DataPC_ACR_Rome_Multi.forge', 'DataPC_ACR_Rome_Multi.forge'), ('DataPC_AC2MP_Siena.forge', 'DataPC_AC2MP_Siena_dlc.forge'), ('DataPC_AC2MP_SanDonato.forge', 'DataPC_AC2MP_SanDonato_dlc.forge'), ('DataPC_AC2MP_SanMarco.forge', 'DataPC_AC2MP_SanMarco.forge'), ('DataPC_AC2MP_Palazzio.forge', 'DataPC_AC2MP_Palazzio.forge'))

def family(name):
    return re.sub('_\\d+$', '', name or '')

def is_cb(o, game='ACR'):
    return xrefs.cls(game, o['type']) == 'EntityGroup' and 'chasebreaker' in (o['name'] or '').lower()

def groups(path):
    out = {}
    with Forge(path) as f:
        for r in f.records:
            if not r.size or r.name == 'GlobalMetaFile':
                continue
            try:
                p = mapwalk.Payload(r, f.read(r))
            except Exception:
                continue
            for o in p.objects:
                if is_cb(o):
                    out[o['name']] = (o['uid'], o['body'], r.name)
    return out

def pairs():
    out = defaultdict(list)
    acr_dir = os.path.dirname(mapwalk.archive_path('ACR', 'DataPC_AC2MP_Firenze_dlc.forge'))
    for a_arch, r_arch in PAIRED_MAPS:
        a, r = (groups(os.path.join(acb.ACB, a_arch)), groups(os.path.join(acr_dir, r_arch)))
        for n, (u, ab, _c) in a.items():
            if n in r and r[n][0] == u:
                out[family(n), len(ab), len(r[n][1])].append((r_arch, n, ab, r[n][1]))
    return out

def instance_positions(acr_bodies):
    b0 = acr_bodies[0]
    return sorted({i for b in acr_bodies[1:] for i in range(len(b0)) if b[i] != b0[i]})

def runs(pos):
    out = []
    for i in pos:
        if out and i <= out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [(s, e + 1) for s, e in out]

def value_map(a1, x):
    out = {}
    diff = [i for i in range(len(a1)) if a1[i] != x[i]]
    for w in (4, 2):
        for i in diff:
            for s in range(max(0, i - w + 1), min(i, len(a1) - w) + 1):
                k, v = (a1[s:s + w], x[s:s + w])
                if k != v:
                    out.setdefault((w, k), set()).add(v)
    return {k: next(iter(v)) for k, v in out.items() if len(v) == 1}

def alignment(a1, r1):
    import difflib
    import world
    sa, sr = (world.segments(a1), world.segments(r1))
    oa, orr = ([0], [0])
    for _n, b in sa:
        oa.append(oa[-1] + len(b))
    for _n, b in sr:
        orr.append(orr[-1] + len(b))
    sm = difflib.SequenceMatcher(a=[(n, len(b)) for n, b in sa], b=[(n, len(b)) for n, b in sr], autojunk=False)
    out = {}
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            for k in range(i2 - i1):
                for d in range(len(sa[i1 + k][1])):
                    out[orr[j1 + k] + d] = oa[i1 + k] + d
        elif op == 'replace' and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                (na, ba), (nr, br) = (sa[i1 + k], sr[j1 + k])
                if na == nr == 'FXCommand' and len(br) == len(ba) + 16:
                    for d in range(len(ba)):
                        out[orr[j1 + k] + (d if d < 20 else d + 16)] = oa[i1 + k] + d
    return out
REAL = None
FRESH_SPAN = 1024

def embedded(body):
    global REAL
    if REAL is None:
        import merchant_stalls
        REAL = merchant_stalls.REAL
    out = {}
    for j in range(0, len(body) - 8):
        if struct.unpack_from('<I', body, j + 4)[0] in REAL:
            v = struct.unpack_from('<I', body, j)[0]
            if v >= 65536 and v not in REAL:
                out.setdefault(v, j)
    return out

def is_header(body, p):
    return 0 <= p and p + 8 <= len(body) and (struct.unpack_from('<I', body, p + 4)[0] in REAL)

def defines(body, w):
    p = body.find(w)
    while p >= 0:
        if is_header(body, p):
            return True
        p = body.find(w, p + 1)
    return False

def instantiate(r1, a1, x, pos_runs, alloc, extra_uids=()):
    ea, er = (embedded(a1), embedded(r1))
    out = bytearray(r1)
    fresh, uidmap = ({}, {})
    for u in list(er) + [v for v in ea if v not in er and struct.pack('<I', v) in r1]:
        if u in ea:
            uidmap[u] = struct.unpack_from('<I', x, ea[u])[0]
        else:
            fresh[u] = uidmap[u] = alloc()
    for u in extra_uids:
        pat = struct.pack('<I', u)
        if u in uidmap or pat not in r1 or pat not in a1:
            continue
        vals, p = (set(), a1.find(pat))
        while p >= 0:
            vals.add(bytes(x[p:p + 4]))
            p = a1.find(pat, p + 1)
        if len(vals) == 1:
            uidmap[u] = struct.unpack('<I', vals.pop())[0]
    if fresh:
        lo, hi = (min(fresh) - FRESH_SPAN, max(fresh) + FRESH_SPAN)
        for k in range(len(r1) - 3):
            v = struct.unpack_from('<I', r1, k)[0]
            if lo <= v <= hi and v not in uidmap and (struct.pack('<I', v) not in a1):
                fresh[v] = uidmap[v] = alloc()
    refmap = {}
    for u, v in uidmap.items():
        pat, vals, p = (struct.pack('<I', u), set(), a1.find(struct.pack('<I', u)))
        while p >= 0:
            if not is_header(a1, p) and p + 4 <= len(x):
                vals.add(bytes(x[p:p + 4]))
            p = a1.find(pat, p + 1)
        if len(vals) == 1:
            w = vals.pop()
            if struct.unpack('<I', w)[0] != v and defines(x, w):
                refmap[u] = struct.unpack('<I', w)[0]
    for u, v in uidmap.items():
        pat = struct.pack('<I', u)
        k = bytes(out).find(pat)
        while k >= 0:
            out[k:k + 4] = struct.pack('<I', v if is_header(r1, k) else refmap.get(u, v))
            k = bytes(out).find(pat, k + 4)
    touched = {k + d for u in uidmap for k in range(len(r1) - 3) if r1[k:k + 4] == struct.pack('<I', u) for d in range(4)}
    al = alignment(a1, r1)
    inst = {j for s, e in pos_runs for j in range(s, e)}
    for k in sorted(inst):
        if k in touched or k + 3 >= len(r1):
            continue
        for k0 in range(max(0, k - 3), k + 1):
            if all((k0 + d in al and al[k0 + d] == al[k0] + d for d in range(4))) and (not any((k0 + d in touched for d in range(4)))) and (r1[k0:k0 + 4] != a1[al[k0]:al[k0] + 4]) and close_float(r1[k0:k0 + 4], a1[al[k0]:al[k0] + 4]):
                out[k0:k0 + 4] = x[al[k0]:al[k0] + 4]
                touched.update(range(k0, k0 + 4))
                break
    both = sorted((j for j, k in al.items() if j not in touched and r1[j] != a1[k] and (x[k] != a1[k])))
    for s0, e0 in runs(both):
        for f in range(e0 - 4, s0 - 4, -4):
            f = max(f, 0)
            if not all((f + d in al and al[f + d] == al[f] + d for d in range(4))):
                continue
            k = al[f]
            vr, vx = (bytes(r1[f:f + 4]), bytes(x[k:k + 4]))
            out[f:f + 4] = vx
            touched.update(range(f, f + 4))
            if struct.unpack('<I', vr)[0] >= 65536 and r1.count(vr) > 1:
                p = r1.find(vr)
                while p >= 0:
                    if not any((q in touched for q in range(p, p + 4))) or p == f:
                        out[p:p + 4] = vx
                        touched.update(range(p, p + 4))
                    p = r1.find(vr, p + 4)
    for s, e in pos_runs:
        for j in range(s, e):
            if j not in touched and j in al and (r1[j] == a1[al[j]]):
                out[j] = x[al[j]]
                touched.add(j)
            elif j not in touched and j in al:
                touched.add(j)
    for j, k in al.items():
        if j not in touched and r1[j] == a1[k] and (x[k] != a1[k]):
            out[j] = x[k]
    vm = value_map(a1, x)
    bad = []
    for s, e in pos_runs:
        left = [j for j in range(s, e) if j not in touched]
        if not left:
            continue
        done = set()
        k = max(0, left[0] - 3)
        while k < e:
            hit = None
            for w in (4, 2):
                key = (w, bytes(r1[k:k + w]))
                if k + w <= len(r1) and key in vm and (not any((j in touched for j in range(k, k + w)))) and any((j in left for j in range(k, k + w))):
                    hit = w
                    break
            if hit:
                val = vm[hit, bytes(r1[k:k + hit])]
                for d in range(hit):
                    if s <= k + d < e:
                        out[k + d] = val[d]
                        done.add(k + d)
                k += hit
            else:
                k += 1
        if any((j not in done for j in left)):
            bad.append(s)
    return (bytes(out), fresh, bad, set(uidmap.values()))
FLOAT_TOL = 0.001

def close_float(a, b):
    import math
    x, y = (struct.unpack('<f', a)[0], struct.unpack('<f', b)[0])
    return math.isfinite(x) and math.isfinite(y) and (abs(x - y) <= FLOAT_TOL) and (1e-06 < max(abs(x), abs(y)) < 1000000.0)

def same_but_fresh(got, want, fresh_new):
    if len(got) != len(want):
        return (False, 'length')
    ren, near = ({}, 0)
    new = {struct.pack('<I', v) for v in fresh_new}
    i = 0
    while i < len(got):
        if got[i] != want[i]:
            k = next((k for k in range(max(0, i - 3), i + 1) if got[k:k + 4] in new), None)
            if k is not None:
                a, b = (got[k:k + 4], want[k:k + 4])
                if ren.setdefault(a, b) != b:
                    return (False, f'fresh uid at {k} maps two ways')
                i = k + 4
                continue
            k = next((k for k in range(max(0, i - 3), i + 1) if k + 4 <= len(got) and close_float(got[k:k + 4], want[k:k + 4])), None)
            if k is None:
                return (False, f'byte {i}')
            near += 1
            i = k + 4
        else:
            i += 1
    return (len(set(ren.values())) == len(ren), f'{len(ren)} uids renamed, {near} floats within {FLOAT_TOL}')

class Fresh:

    def __init__(self, base, taken):
        self.next, self.taken = (base, set(taken))

    def __call__(self):
        while self.next in self.taken:
            self.next += 1
        self.taken.add(self.next)
        return self.next

def check(ref, targets, pos):
    _m, _n, a1, r1 = ref
    good, notes, last = (0, [], '')
    for m, n, ak, rk in targets:
        got, fresh, bad, written = instantiate(r1, a1, ak, pos, Fresh(2130706432, set()))
        ok, why = same_but_fresh(got, rk, written)
        if ok:
            good, last = (good + 1, why + (f"; {len(bad)} fields kept as the reference's" if bad else ''))
        else:
            notes.append(f"    {m} {n}: {why}; fields kept as the reference's {len(bad)}")
    return (good, notes, last)

def keeps_acb(items):
    score = {}
    for m in dict.fromkeys((i[0] for i in items)):
        _m, _n, a1, r1 = next((i for i in items if i[0] == m))
        al = alignment(a1, r1)
        score[m] = sum((1 for j, k in al.items() if r1[j] != a1[k] and (not close_float_at(r1, a1, j, k))))
    return (sorted(score, key=lambda m: (score[m], m)), score)

def close_float_at(r1, a1, j, k):
    return any((j - d >= 0 and k - d >= 0 and close_float(r1[j - d:j - d + 4], a1[k - d:k - d + 4]) for d in range(4)))

def gate():
    ok_all = True
    for (fam, la, lr), items in sorted(pairs().items()):
        pos = runs(instance_positions([r for _m2, _n2, _a, r in items]))
        maps = list(dict.fromkeys((i[0] for i in items)))
        lines, total, good_all = ([], 0, 0)
        for m in maps:
            mine = [i for i in items if i[0] == m]
            if len(mine) < 2:
                continue
            g, notes, last = check(mine[0], mine[1:], pos)
            total, good_all = (total + len(mine) - 1, good_all + g)
            lines += notes[:3]
        keep, score = keeps_acb(items)
        cross = ''
        if len(keep) >= 2:
            ref = next((i for i in items if i[0] == keep[0]))
            targets = [i for i in items if i[0] in keep[1:]]
            g, notes, last = check(ref, targets, pos)
            total, good_all = (total + len(targets), good_all + g)
            cross = f"; across maps {keep[0][11:]} -> {', '.join((k[11:] for k in keep[1:]))}: {g} / {len(targets)}"
            lines += notes[:3]
        if not total:
            print(f'  {fam} ({la} -> {lr}): {len(items)} pair(s) in {len(maps)} map(s), no gate')
            continue
        ok = good_all == total
        ok_all &= ok
        print(f"  {fam} ({la} -> {lr}): {len(items)} pairs, {len(maps)} maps (ACR-vs-ACB bytes changed: {', '.join((f'{m[11:-6]} {score[m]}' for m in score))}); reproduced {good_all} / {total}{cross}: {('OK' if ok else 'FAILED')}" + (f' (e.g. {last})' if last else ''))
        if lines:
            print('\n'.join(lines))
    return ok_all

def seg_offsets(segs):
    o = [0]
    for _n, b in segs:
        o.append(o[-1] + len(b))
    return o

def splice(a1, r1, x):
    import difflib
    import world
    sa, sx = (world.segments(a1), world.segments(x))
    oa, ox = (seg_offsets(sa), seg_offsets(sx))
    al = alignment(a1, r1)
    inv = {v: k for k, v in al.items()}
    acr_classes = {n for n, _b in world.segments(r1)}
    sm = difflib.SequenceMatcher(a=[(n, len(b)) for n, b in sa], b=[(n, len(b)) for n, b in sx], autojunk=False)
    edits = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            continue
        a_names = {n for n, _b in sa[i1:i2]}
        x_names = {n for n, _b in sx[j1:j2]}
        lo, hi = (oa[i1], oa[i2])
        if not (a_names | x_names) & acr_classes:
            edits.append((None, None, x[ox[j1]:ox[j2]], lo, hi))
            continue
        if i2 > i1:
            rs = [inv.get(k) for k in range(lo, hi)]
            if None in rs or rs != list(range(rs[0], rs[0] + len(rs))):
                raise ValueError(f'block {op} A1 segments {[(n, len(b)) for n, b in sa[i1:i2]]}: not kept whole by ACR')
            r_lo, r_hi = (rs[0], rs[-1] + 1)
        elif lo in inv:
            r_lo = r_hi = inv[lo]
        elif lo - 1 in inv:
            r_lo = r_hi = inv[lo - 1] + 1
        else:
            raise ValueError(f"insert of {[(n, len(b)) for n, b in sx[j1:j2]]}: its place isn't kept by ACR")
        edits.append((r_lo, r_hi, x[ox[j1]:ox[j2]], lo, hi))
    r2, a2, shift = (bytearray(), bytearray(), {})
    rp = 0
    for r_lo, r_hi, xb, _lo, _hi in sorted((e for e in edits if e[0] is not None), key=lambda e: e[0]):
        for k in range(rp, r_lo):
            shift[k] = len(r2) + (k - rp)
        r2 += r1[rp:r_lo] + xb
        rp = r_hi
    for k in range(rp, len(r1)):
        shift[k] = len(r2) + (k - rp)
    r2 += r1[rp:]
    ap = 0
    for _r_lo, _r_hi, xb, lo, hi in sorted(edits, key=lambda e: e[3]):
        a2 += a1[ap:lo] + xb
        ap = hi
    a2 += a1[ap:]
    if [(n, len(b)) for n, b in world.segments(bytes(a2))] != [(n, len(b)) for n, b in sx]:
        raise ValueError("A1 spliced doesn't have X's segment sequence")
    return (bytes(r2), bytes(a2), shift)

def cb_form(items):
    import world
    return 'ChaseBreakerComponent' in {nm for nm, _b in world.segments(items[0][3])}

def closest(x, P, n=3):
    import difflib
    import world
    xs = [nm for nm, _b in world.segments(x)]
    scored = []
    for (fam, _la, _lr), items in P.items():
        a = [nm for nm, _b in world.segments(items[0][2])]
        scored.append((difflib.SequenceMatcher(a=xs, b=a, autojunk=False).ratio(), fam, items))
    return [items for _r, _f, items in sorted(scored, key=lambda t: (-t[0], t[1]))[:n]]

def convert_spliced(items, x, alloc):
    keep, _score = keeps_acb(items)
    _m, ref_n, a1, r1 = next((i for i in items if i[0] == keep[0]))
    r2, a2, shift = splice(a1, r1, x)
    pos = sorted((shift[j] for j in instance_positions([r for *_y, r in items]) if j in shift))
    body, _fresh, bad, _written = instantiate(r2, a2, x, runs(pos), alloc, extra_uids=set(embedded(a1)))
    return (body, len(bad), ref_n)

def reference_body(items):
    keep, _score = keeps_acb(items)
    return next((i for i in items if i[0] == keep[0]))[3]
DOOR_LINE_TOL = 0.05
TZ_CENTRE = 129

def _frame(body, off):
    return (struct.unpack_from('<12f', body, off), struct.unpack_from('<3f', body, off + 48))

def door_line(body):
    import chase_breaker_check
    import world
    grot, gpos = _frame(body, 10)
    children, elements = chase_breaker_check.parse(body)
    leaves = {t for t, clips, _i in elements if clips.get('BlendPosClip')}
    xs = []
    for uid, eo, _comps, _g in children:
        if uid in leaves:
            _erot, epos = _frame(body, eo + 4)
            xs.append(sum(((epos[k] - gpos[k]) * grot[k] for k in range(3))))
    return sum(xs) / len(xs) if xs else None

def trigger_box(body):
    import world
    o = 0
    for nm, x in world.segments(body):
        if nm == 'TriggerZone' and len(x) >= TZ_CENTRE + 32 and (struct.unpack_from('<I', x, 9)[0] == 3):
            return (o + TZ_CENTRE, struct.unpack_from('<f', x, TZ_CENTRE)[0], struct.unpack_from('<f', x, TZ_CENTRE + 16)[0])
        o += len(x)
    return None

def align_trigger(body, ref):
    tb = trigger_box(body)
    db, dr = (door_line(body), door_line(ref))
    if tb is None or db is None or dr is None or (abs(db - dr) < DOOR_LINE_TOL):
        return body
    want = tb[1] + (db - dr)
    out = bytearray(body)
    struct.pack_into('<f', out, tb[0], want)
    return bytes(out)

def element_targets(body):
    import world
    return [f"{struct.unpack_from('<I', x, 5)[0]:08x}" for nm, x in world.segments(body) if nm == 'ChaseBreakerElement']

def gate_splice(src_fam, via_fam):
    P = pairs()
    src = [i for k, v in P.items() if k[0] == src_fam for i in v]
    via = next((v for k, v in P.items() if k[0] == via_fam))
    good = 0
    for m, n, ak, rk in src:
        try:
            got, kept, ref = convert_spliced(via, ak, Fresh(2130706432, set()))
        except ValueError as e:
            print(f'    {m} {n}: {e}')
            continue
        ok, why = same_but_fresh(got, rk, set((struct.unpack_from('<I', got, k)[0] for k in range(len(got) - 3) if 2130706432 <= struct.unpack_from('<I', got, k)[0] < 2130771968)) | set(embedded(got)))
        if element_targets(got) != element_targets(rk):
            ok, why = (False, f"element targets {element_targets(got)} where ACR's are {element_targets(rk)}")
        good += ok
        print(f"    {m[11:]} {n}: {('OK' if ok else 'DIFFERS')} ({why}; {kept} fields kept as {ref}'s)")
    print(f'  {src_fam} through {via_fam}: {good} / {len(src)}')
    return good == len(src)

def for_map(archive, alloc):
    P = pairs()
    by_type = {}
    for (fam, la, lr), items in P.items():
        by_type[fam, la] = items
    built, left = ([], [])
    for n, (u, x, cell) in sorted(groups(os.path.join(acb.ACB, archive)).items()):
        items = by_type.get((family(n), len(x)))
        if not items:
            body = None
            for via in sorted(closest(x, P), key=lambda items: not cb_form(items)):
                try:
                    body, kept, ref = convert_spliced(via, x, alloc)
                    break
                except ValueError:
                    continue
            if body is None or struct.unpack_from('<I', body, 2)[0] != u:
                left.append(n)
                continue
            body = align_trigger(body, reference_body(via))
            built.append((n, u, cell, body, f'{ref} (spliced)', kept))
            continue
        keep, _score = keeps_acb(items)
        _m, _n, a1, r1 = next((i for i in items if i[0] == keep[0]))
        pos = runs(instance_positions([r for _m2, _n2, _a, r in items]))
        body, _fresh, bad, _written = instantiate(r1, a1, x, pos, alloc)
        if struct.unpack_from('<I', body, 2)[0] != u or len(body) != len(r1):
            raise SystemExit(f'{n}: not rebuilt under its own uid')
        built.append((n, u, cell, body, keep[0], len(bad)))
    return (built, left)

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--gate', action='store_true')
    ap.add_argument('--map')
    ap.add_argument('--gate-splice', nargs=2, metavar=('TYPE', 'VIA'))
    a = ap.parse_args(argv)
    if a.gate:
        raise SystemExit(0 if gate() else 1)
    if a.gate_splice:
        raise SystemExit(0 if gate_splice(*a.gate_splice) else 1)
    if a.map:
        import acb_configs
        import inventory
        cfg = acb_configs.MAPS[a.map]
        taken = set(inventory.universe('ACR')) | set(inventory.universe('AC3'))
        built, left = for_map(cfg['archive'], Fresh(cfg['uid_base'] + 57344, taken))
        for n, u, cell, b, ref, kept in built:
            print(f'  {n} 0x{u:08X} {cell}: {len(b)} B (ACR form, reference {ref}, {kept} fields kept as its)')
        print(f'built {len(built)}, left out (no ACR template) {len(left)}: {left}')
if __name__ == '__main__':
    main(sys.argv[1:])
