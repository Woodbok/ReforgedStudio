"""forgelib/part_icons.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import re
import struct
import sys
import zlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dxt
import make_jester
from datafile import decompress
from forge import Forge
from paths import ACR, AC3_SOURCE
OUT = os.path.join(HERE, 'out', 'tex', 'icons')
SLOTS = {'Head': 'Head', 'MainPart': 'Main', 'SecondPart': 'Secondary', 'Legs': 'Legs', 'Belt': 'Belt'}

def texture(archive, fname):
    with Forge(archive) as f:
        r = next((r for r in f.records if r.name == fname))
        tex = next((o for o in make_jester.objects_of(decompress(f.read(r))) if o['type'] == make_jester.TEXTURE))
    return dxt.decode(tex['body'])

def flip(w, h, px):
    return b''.join((px[(h - 1 - y) * w * 4:(h - y) * w * 4] for y in range(h)))

def crop_scaled(w, h, px, box, size=64):
    x0, y0, x1, y1 = box
    out = bytearray()
    for ty in range(size):
        for tx in range(size):
            sx0, sx1 = (x0 + (x1 - x0) * tx / size, x0 + (x1 - x0) * (tx + 1) / size)
            sy0, sy1 = (y0 + (y1 - y0) * ty / size, y0 + (y1 - y0) * (ty + 1) / size)
            acc, n = ([0.0, 0.0, 0.0], 0.0)
            for sy in range(int(sy0), max(int(sy0) + 1, int(-(-sy1 // 1)))):
                for sx in range(int(sx0), max(int(sx0) + 1, int(-(-sx1 // 1)))):
                    wx = min(sx + 1, sx1) - max(sx, sx0)
                    wy = min(sy + 1, sy1) - max(sy, sy0)
                    if wx <= 0 or wy <= 0 or (not (0 <= sx < w and 0 <= sy < h)):
                        continue
                    q = (sy * w + sx) * 4
                    a = px[q + 3] / 255.0
                    for k in range(3):
                        acc[k] += wx * wy * (px[q + k] * a + 255 * (1 - a))
                    n += wx * wy
            out += bytes((int(round(v / n)) if n else 255 for v in acc)) + b'\xff'
    return bytes(out)

def slot_badge(part, persona=None):
    rx = re.compile(f'AC3MP_ID(\\d\\d)_{part}_V1_MapDesc')
    icons, own = ([], None)
    with Forge(os.path.join(AC3_SOURCE, 'DataPC_extra.forge')) as f:
        for r in f.records:
            m = r.name and rx.fullmatch(r.name)
            if m:
                tex = next((o for o in make_jester.objects_of(decompress(f.read(r))) if o['type'] == make_jester.TEXTURE))
                w, h, px = dxt.decode(tex['body'])
                if (w, h) == (64, 64):
                    icons.append(px)
                    if persona and int(m.group(1)) == persona:
                        own = px
    zone = None
    if persona:
        assert own is not None, (part, persona)
        gmask, _rgba, _n = slot_badge(part)
        xs = [i % 64 for i in range(64 * 64) if gmask[i]]
        ys = [i // 64 for i in range(64 * 64) if gmask[i]]
        zone = (min(xs) - 3, min(ys) - 3, max(xs) + 3, max(ys) + 3)
        icons = [own]
    assert len(icons) >= (1 if persona else 8), (part, len(icons))
    cand, mean = (bytearray(64 * 64), [0.0] * (64 * 64))
    for i in range(64 * 64):
        x, y = (i % 64, i // 64)
        sums = [sum(p[i * 4:i * 4 + 3]) for p in icons]
        mean[i] = sum(sums) / len(sums)
        if x >= 36 and y >= 24 and (zone is None or (zone[0] <= x <= zone[2] and zone[1] <= y <= zone[3])):
            cand[i] = sum((1 for s in sums if s < 200)) >= 0.75 * len(icons)
    glyph, seen = (bytearray(64 * 64), set())
    for start in range(64 * 64):
        if not cand[start] or start in seen:
            continue
        comp, stack = ([], [start])
        seen.add(start)
        while stack:
            i = stack.pop()
            comp.append(i)
            x, y = (i % 64, i // 64)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    j = (y + dy) * 64 + x + dx
                    if 0 <= x + dx < 64 and 0 <= y + dy < 64 and cand[j] and (j not in seen):
                        seen.add(j)
                        stack.append(j)
        rim = {(y + dy) * 64 + x + dx for i in comp for x, y in [(i % 64, i // 64)] for dx in (-1, 0, 1) for dy in (-1, 0, 1) if 0 <= x + dx < 64 and 0 <= y + dy < 64 and (not cand[(y + dy) * 64 + x + dx])}
        if len(comp) >= 8 and rim and (sum((mean[j] for j in rim)) / len(rim) >= 290):
            for i in comp:
                glyph[i] = 1
    mask, rgba = (bytearray(64 * 64), bytearray(b'\xff' * (64 * 64 * 4)))
    for i in range(64 * 64):
        x, y = (i % 64, i // 64)
        if glyph[i]:
            mask[i] = 1
            rgba[i * 4:i * 4 + 3] = b'\x00\x00\x00'
        elif any((glyph[(y + dy) * 64 + x + dx] for dx in (-1, 0, 1) for dy in (-1, 0, 1) if 0 <= x + dx < 64 and 0 <= y + dy < 64)):
            mask[i] = 1
    assert sum(glyph) > 20, (part, sum(glyph))
    return (bytes(mask), bytes(rgba), len(icons))

def badges_of(c):
    b = c.part_icons.badges_from
    return c.donor if b == 'donor' else b

def icon(art, box, part, badge_from=None):
    w, h, px = art
    upright = flip(w, h, px)
    pic = flip(64, 64, crop_scaled(w, h, upright, box))
    mask, med, _n = slot_badge(part, badge_from)
    out = bytearray(pic)
    for i in range(64 * 64):
        if mask[i]:
            out[i * 4:i * 4 + 4] = med[i * 4:i * 4 + 4]
    return bytes(out)

def acr_art(spec):
    return texture(os.path.join(ACR, 'DataPC_extra.forge'), spec[0])

def png(path, w, h, rgba):
    rgb = bytearray()
    for i in range(0, len(rgba), 4):
        rgb += rgba[i:i + 3]
    raw = b''.join((b'\x00' + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h)))
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 4294967295)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))

def preview(c):
    art = acr_art(c.part_icons.art)
    rows = []
    for who in ('donor', 'his'):
        row = []
        for suffix, part in SLOTS.items():
            if who == 'donor':
                w, h, px = texture(os.path.join(AC3_SOURCE, 'DataPC_extra.forge'), f'AC3MP_ID{c.donor}_{part}_V1_MapDesc')
            else:
                px = icon(art, c.part_icons.boxes[suffix], part, badges_of(c))
            row.append(flip(64, 64, px))
        rows.append(row)
    S, W = (4, 64 * 4 * len(SLOTS))
    img = bytearray(W * 64 * S * len(rows) * 4)
    for r, row in enumerate(rows):
        for col, px in enumerate(row):
            for y in range(64 * S):
                for x in range(64 * S):
                    q = (y // S * 64 + x // S) * 4
                    o = ((r * 64 * S + y) * W + col * 64 * S + x) * 4
                    img[o:o + 4] = px[q:q + 4]
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f'{c.key}_parts.png')
    png(path, W, 64 * S * len(rows), bytes(img))
    return path
if __name__ == '__main__':
    import characters
    print(preview(characters.CHARACTERS[sys.argv[1]]))
