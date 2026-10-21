"""forgelib/audio/bank_build.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import hirc_convert as hc
import media
import rules
from akpk import ACR_SOUND, AC3_SOUND, Package, packages
from banks import chunks, load
from overlap import fnv1
BLOCK = 2048
OUT = os.path.join(ROOT, 'out', 'audio')
PACKAGE_NAME = 'sounds_acr_mp.pck'

def bank_bytes(bank_id, name, items):

    def chunk(tag, body):
        return tag + struct.pack('<I', len(body)) + body
    bkhd = struct.pack('<IIII', hc.DST_VERSION, bank_id, 0, 1) + bytes(8)
    hirc = struct.pack('<I', len(items)) + b''.join((struct.pack('<BI', t, len(b)) + b for t, b in items))
    nm = name.encode('latin-1')
    stid = struct.pack('<II', 1, 1) + struct.pack('<IB', bank_id, len(nm)) + nm
    return chunk(b'BKHD', bkhd) + chunk(b'HIRC', hirc) + chunk(b'STID', stid)
ALIGN = 16

def embed(data, bank_id, trees, media_in, nodes_before=(4,)):
    cs = {tag: data[o:o + s] for tag, o, s in chunks(data)}
    media_all = {}
    if 'DIDX' in cs:
        d, blob0 = (cs['DIDX'], cs.get('DATA', b''))
        for k in range(len(d) // 12):
            mid, off, size = struct.unpack_from('<III', d, 12 * k)
            media_all[mid] = blob0[off:off + size]
    for mid, w in sorted(media_in.items()):
        if mid in media_all and media_all[mid] != w:
            raise SystemExit(f'clip {mid:#x} is embedded already with other bytes')
        media_all[mid] = w
    blob, didx, where = (bytearray(), bytearray(), {})
    for mid in media_all:
        blob += bytes(-len(blob) % ALIGN)
        where[mid] = (len(blob), len(media_all[mid]))
        didx += struct.pack('<III', mid, len(blob), len(media_all[mid]))
        blob += media_all[mid]
    data_start = 8 + len(cs['BKHD']) + 8 + len(didx) + 8

    def point(tr):
        hit = False
        for n in tr.walk():
            if n.name == 'AkBankSourceData' and n.f('StreamType') == 0:
                mi = n.o('AkMediaInformation')
                if mi is not None and mi.f('sourceID') in where:
                    off, size = where[mi.f('sourceID')]
                    mi.set('uFileID', bank_id)
                    mi.set('uFileOffset', data_start + off)
                    mi.set('uInMemoryMediaSize', size)
                    hit = True
        return hit
    hc.set_version(hc.DST_VERSION)
    raw = _hirc_items(data)
    parsed_items = {oid: (t, tr) for t, oid, tr, _s in hc.parse_bank(data)[1]}
    items = []
    for t, oid, body in raw:
        if t in (2, 11) and oid in parsed_items and point(parsed_items[oid][1]):
            body, dropped = hc.write_item(t, parsed_items[oid][1])
            if dropped:
                raise SystemExit(f'{oid:#x} would drop {dropped}')
        items.append((t, body))
    have = {oid for _t, oid, _b in raw}
    first, nodes, actions, events = ([], [], [], [])
    for t, tr in trees:
        if t == 4 and isinstance(tr, (bytes, bytearray)):
            events.append((t, bytes(tr)))
            continue
        if t in (2, 11):
            point(tr)
        body, dropped = hc.write_item(t, tr)
        if dropped:
            raise SystemExit(f"{tr.f('ulID', 0):#x} would drop {dropped}")
        if t in (14, 1, 18, 19):
            oid = tr.f('ulStateID', tr.f('ulID', None)) if t == 1 else tr.f('ulID', None)
            if oid not in have:
                first.append((t, body))
        elif t == 4:
            events.append((t, body))
        elif t == 3 and 3 in nodes_before:
            actions.append((t, body))
        else:
            nodes.append((t, body))
    fn = next((k for k, (t, _b) in enumerate(items) if t in nodes_before), len(items))
    items = items[:fn] + nodes + items[fn:]
    fe = next((k for k, (t, _b) in enumerate(items) if t == 4), len(items))
    items = first + items[:fe] + actions + items[fe:] + events
    hirc = struct.pack('<I', len(items)) + b''.join((struct.pack('<BI', t, len(b)) + b for t, b in items))

    def chunk(tag, body):
        return tag.encode() + struct.pack('<I', len(body)) + body
    out = chunk('BKHD', cs['BKHD'])
    if media_all:
        out += chunk('DIDX', bytes(didx)) + chunk('DATA', bytes(blob))
    out += chunk('HIRC', hirc)
    for tag, body in cs.items():
        if tag not in ('BKHD', 'DIDX', 'DATA', 'HIRC'):
            out += chunk(tag, body)
    if media_all:
        assert out.index(b'DATA') + 8 == data_start
    return (out, where, data_start)

def _hirc_items(data):
    o, _s = next(((o, s) for t, o, s in chunks(data) if t == 'HIRC'))
    n = struct.unpack_from('<I', data, o)[0]
    p, out = (o + 4, [])
    for _ in range(n):
        t, size, oid = struct.unpack_from('<BII', data, p)
        out.append((t, oid, data[p + 5:p + 5 + size]))
        p += 5 + size
    return out

def check_embedded(data):
    cs = {t: (o, s) for t, o, s in chunks(data)}
    if 'DIDX' not in cs:
        return (True, 0)
    o, s = cs['DIDX']
    didx = {struct.unpack_from('<I', data, o + 12 * k)[0]: struct.unpack_from('<II', data, o + 12 * k + 4) for k in range(s // 12)}
    start = cs['DATA'][0]
    ok, n = (True, 0)
    hc.set_version(hc.DST_VERSION)
    for _t, _oid, tr, _s in hc.parse_bank(data)[1]:
        for x in tr.walk():
            if x.name == 'AkBankSourceData' and x.f('StreamType') == 0:
                mi = x.o('AkMediaInformation')
                sid = mi.f('sourceID') if mi is not None else None
                if sid not in didx:
                    continue
                off, size = (mi.f('uFileOffset'), mi.f('uInMemoryMediaSize'))
                good = start + didx[sid][0] == off and didx[sid][1] == size
                if good:
                    w = data[off:off + size]
                    c2 = dict(media.riff_chunks(w))
                    ex, dat = (c2[b'fmt '][24:], c2[b'data'])
                    so, ao = struct.unpack_from('<II', ex, 16)
                    good = struct.unpack_from('<I', ex, 36)[0] == media.setup_hash(dat[so + 2:ao])
                    try:
                        media.decode(w)
                    except Exception:
                        good = False
                ok &= good
                n += 1
    return (ok, n)

def _ac3_language_map():
    p = next((p for p in packages(AC3_SOUND) if p.name == 'sounds_sfx.pck'))
    with open(p.path, 'rb') as f:
        f.seek(8)
        h = f.read(p.header_size)
    lang_sz = struct.unpack_from('<I', h, 4)[0]
    return h[20:20 + lang_sz]

def package_bytes(banks, streams):
    lang = _ac3_language_map()

    def lut(n):
        return 4 + 20 * n
    header_size = 20 + len(lang) + lut(len(banks)) + lut(len(streams)) + 4
    pos = -(-(8 + header_size) // BLOCK) * BLOCK
    blob, table = (bytearray(), {})
    for kind, files in (('bank', banks), ('stream', streams)):
        rows = []
        for fid in sorted(files):
            data = files[fid]
            rows.append(struct.pack('<IIIII', fid, BLOCK, len(data), pos // BLOCK, 0))
            blob += data
            pad = -len(data) % BLOCK
            blob += bytes(pad)
            pos += len(data) + pad
        table[kind] = struct.pack('<I', len(rows)) + b''.join(rows)
    head = struct.pack('<IIIII', 1, len(lang), len(table['bank']), len(table['stream']), 4) + lang + table['bank'] + table['stream'] + struct.pack('<I', 0)
    assert len(head) == header_size
    first = -(-(8 + header_size) // BLOCK) * BLOCK
    return b'AKPK' + struct.pack('<I', header_size) + head + bytes(first - 8 - header_size) + bytes(blob)

def acr_streams():
    out = {}
    for p in packages(ACR_SOUND):
        for e in p.streams:
            out.setdefault(e.id, (p, e))
    return out

def acr_objects(ids, acr_banks, acr_pk):
    want, found = (set(ids), {})
    hc.set_version(hc.SRC_VERSION)
    while want - set(found):
        missing = want - set(found)
        bank = next((b for b in acr_banks if any((o in missing for _t, o, _r in b.hirc))), None)
        if bank is None:
            raise SystemExit(f'ACR objects {[hex(i) for i in missing]} are in no ACR bank')
        _v, items = hc.parse_bank(acr_pk[bank.pkg].read(bank.entry))
        for t_, oid, t, skipped in items:
            if oid in missing:
                if skipped:
                    raise SystemExit(f'object {oid:#x} did not parse cleanly')
                found[oid] = (t_, oid, t)
                if t_ == 4:
                    want |= {n.f('ulActionID') for n in t.walk() if n.name == 'Action'}
    return [found[i] for i in sorted(found)]

def convert_bank(name, acr_banks, acr_pk, streams_idx, report, extra=()):
    bid = fnv1(name)
    b = next((b for b in acr_banks if b.id == bid), None)
    if b is None:
        raise SystemExit(f'no ACR bank {name}')
    hc.set_version(hc.SRC_VERSION)
    _v, items = hc.parse_bank(acr_pk[b.pkg].read(b.entry))
    own = {oid for _t, oid, _t2, _s in items}
    added = [(t_, oid, t, False) for t_, oid, t in acr_objects(extra, acr_banks, acr_pk) if oid not in own]
    if added:
        report.append(f"{name}: + {len(added)} objects from other ACR banks ({', '.join((f'{o:#010x}' for _t, o, _x, _s in added))})")
    items = items + added
    hc.set_version(hc.DST_VERSION)
    out, media_ids = ([], set())
    for t_, oid, t, skipped in items:
        if skipped:
            raise SystemExit(f'{name}: object {oid:#x} did not parse cleanly at v53')
        tree = rules.convert(t_, t)
        body, dropped = hc.write_item(t_, tree)
        if dropped:
            raise SystemExit(f'{name}: object {oid:#x} would drop {dropped}')
        out.append((t_, body))
        for n in tree.walk():
            if n.name == 'AkMediaInformation':
                media_ids.add(n.f('sourceID'))
    clips = {}
    embedded_ids = {m for m, _sz in b.media}
    plugin_ids = {n.f('sourceID') for _t, _o, t, _s in items for n in t.walk() if n.name == 'AkMediaInformation' and n.f('uFileID') == 0}
    for mid in sorted(media_ids):
        if mid in embedded_ids or (mid in plugin_ids and mid not in streams_idx):
            continue
        if mid not in streams_idx:
            raise SystemExit(f"{name}: streamed clip {mid:#x} not found in ACR's packages")
        p, e = streams_idx[mid]
        clips[mid] = media.convert(p.read(e))
    data = bank_bytes(bid, name, out)
    embedded = {}
    if 'DIDX' in b.chunks:
        raw = acr_pk[b.pkg].read(b.entry)
        start = b.chunks['DATA'][0]
        o, sz = b.chunks['DIDX']
        for k in range(sz // 12):
            mid, off, size = struct.unpack_from('<III', raw, o + 12 * k)
            embedded[mid] = media.convert(raw[start + off:start + off + size])
        data, _where, _start = embed(data, bid, [], embedded)
    report.append(f'{name} ({bid:#010x}): {len(out)} objects converted, {len(clips)} streamed clips, {len(embedded)} embedded')
    return (bid, data, clips)

def verify(path, banks, clips, report):
    p = Package(path)
    ok = p.tables == 4 and {e.id for e in p.banks} == set(banks) and ({e.id for e in p.streams} == set(clips))
    ok &= all((p.read(e) == banks[e.id] for e in p.banks)) and all((p.read(e) == clips[e.id] for e in p.streams))
    report.append(f'verify package: 4 tables, every file reads back byte-identical: {ok}')
    hc.set_version(hc.DST_VERSION)
    for e in p.banks:
        v, items = hc.parse_bank(p.read(e))
        clean = v == hc.DST_VERSION and items and (not any((s for *_x, s in items)))
        ok &= bool(clean)
        report.append(f'verify bank {e.id:#010x}: wwiser parses it as v{v}, {len(items)} objects, all clean: {bool(clean)}')
    for e in p.banks:
        good, n = check_embedded(p.read(e))
        ok &= good
        if n:
            report.append(f'verify bank {e.id:#010x}: {n} embedded sources at their DIDX offsets, decoding, hashed: {good}')
    decoded = 0
    for e in p.streams:
        try:
            media.decode(p.read(e))
            decoded += 1
        except Exception as exc:
            report.append(f'  clip {e.id:#010x} does not decode: {exc}')
    ok &= decoded == len(p.streams)
    report.append(f'verify clips: {decoded} of {len(p.streams)} decode (vgmstream)')
    wrong = 0
    for e in p.streams:
        cs = dict(media.riff_chunks(p.read(e)))
        ex, data = (cs[b'fmt '][24:], cs[b'data'])
        s, a = struct.unpack_from('<II', ex, 16)
        wrong += struct.unpack_from('<I', ex, 36)[0] != media.setup_hash(data[s + 2:a])
    ok &= wrong == 0
    report.append(f'verify clips: setup hash (vorb +0x24) = FNV-1 of the setup in {len(p.streams) - wrong} of {len(p.streams)}')
    return ok

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('banks', nargs='+', help='ACR bank names, e.g. BNK_WRLD_MP_Souk')
    ap.add_argument('--name', default='A1_souk_ambience', help='build folder under out/audio/')
    ap.add_argument('--extra', action='append', default=[], metavar='BANK:ID', help="also put this ACR object (an event brings its actions) into BANK, e.g. BNK_WRLD_MP_Souk:0xf378dca4 (Souk's map-music event)")
    a = ap.parse_args(argv)
    pkgs, acr_banks = load(ACR_SOUND)
    acr_pk = {p.name: p for p in pkgs}
    streams_idx = acr_streams()
    report, banks, clips = ([], {}, {})
    extra = {}
    for spec in a.extra:
        bank, _, oid = spec.partition(':')
        extra.setdefault(bank, []).append(int(oid, 0))
    for name in a.banks:
        bid, data, c = convert_bank(name, acr_banks, acr_pk, streams_idx, report, extra.get(name, ()))
        banks[bid] = data
        clips.update(c)
    dest_dir = os.path.join(OUT, a.name)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, PACKAGE_NAME)
    with open(dest, 'wb') as fh:
        fh.write(package_bytes(banks, clips))
    report.append(f'-> {dest} ({os.path.getsize(dest):,} bytes)')
    ok = verify(dest, banks, clips, report)
    report.append('all OK' if ok else 'FAILED')
    with open(os.path.join(dest_dir, 'report.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')
    print('\n'.join(report))
    if not ok:
        raise SystemExit(1)
if __name__ == '__main__':
    main(sys.argv[1:])
