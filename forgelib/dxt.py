"""forgelib/dxt.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import zlib
BLOCK = {2: 8, 3: 8, 4: 16, 5: 16}
DXT3 = {4}

def header(body):
    w, h = struct.unpack_from('<II', body, 10)
    fmt = struct.unpack_from('<I', body, 22)[0]
    mips = struct.unpack_from('<I', body, 34)[0]
    size = struct.unpack_from('<I', body, 86)[0]
    return {'width': w, 'height': h, 'format': fmt, 'mips': mips, 'size': size}

def _rgb565(c):
    r, g, b = (c >> 11 & 31, c >> 5 & 63, c & 31)
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)

def _colour_block(data, p, dxt1):
    c0, c1, bits = struct.unpack_from('<HHI', data, p)
    a, b = (_rgb565(c0), _rgb565(c1))
    if c0 > c1 or not dxt1:
        pal = [a, b, tuple(((2 * x + y) // 3 for x, y in zip(a, b))), tuple(((x + 2 * y) // 3 for x, y in zip(a, b)))]
    else:
        pal = [a, b, tuple(((x + y) // 2 for x, y in zip(a, b))), (0, 0, 0)]
    return [pal[bits >> 2 * i & 3] for i in range(16)]

def _alpha_block(data, p):
    a0, a1 = (data[p], data[p + 1])
    bits = int.from_bytes(data[p + 2:p + 8], 'little')
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    return [pal[bits >> 3 * i & 7] for i in range(16)]

def _alpha_block_dxt3(data, p):
    bits = int.from_bytes(data[p:p + 8], 'little')
    return [(bits >> 4 * i & 15) * 17 for i in range(16)]

def decode(body):
    hd = header(body)
    w, h, fmt = (hd['width'], hd['height'], hd['format'])
    if fmt not in BLOCK:
        raise ValueError(f'texture format {fmt} not handled')
    bs, dxt1 = (BLOCK[fmt], BLOCK[fmt] == 8)
    out = bytearray(w * h * 4)
    p = 90
    for by in range(0, max(h, 4), 4):
        for bx in range(0, max(w, 4), 4):
            if dxt1:
                alpha, cols = ([255] * 16, _colour_block(body, p, True))
            else:
                alpha = _alpha_block_dxt3(body, p) if fmt in DXT3 else _alpha_block(body, p)
                cols = _colour_block(body, p + 8, False)
            p += bs
            for i in range(16):
                x, y = (bx + (i & 3), by + (i >> 2))
                if x < w and y < h:
                    q = (y * w + x) * 4
                    out[q:q + 3] = bytes(cols[i])
                    out[q + 3] = alpha[i]
    return (w, h, out)

def write_png(path, w, h, rgba, alpha=False):
    ch = 4 if alpha else 3
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        row = rgba[y * w * 4:(y + 1) * w * 4]
        rows += row if alpha else bytes((b for i, b in enumerate(row) if i % 4 != 3))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 4294967295)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6 if alpha else 2, 0, 0, 0)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(bytes(rows), 6)) + chunk(b'IEND', b''))
    return ch

def _pack565(c):
    r, g, b = (max(0, min(255, int(round(v)))) for v in c)
    return (r * 31 + 127) // 255 << 11 | (g * 63 + 127) // 255 << 5 | (b * 31 + 127) // 255

def _encode_block(pixels):
    lo = [min((p[k] for p in pixels)) for k in range(3)]
    hi = [max((p[k] for p in pixels)) for k in range(3)]
    axis = [h - l for h, l in zip(hi, lo)]
    proj = [sum((a * v for a, v in zip(axis, p))) for p in pixels]
    c0 = _pack565(pixels[proj.index(max(proj))])
    c1 = _pack565(pixels[proj.index(min(proj))])
    if c0 < c1:
        c0, c1 = (c1, c0)
    if c0 == c1:
        return struct.pack('<HHI', c0, c1, 0)
    a, b = (_rgb565(c0), _rgb565(c1))
    pal = [a, b, tuple(((2 * x + y) / 3 for x, y in zip(a, b))), tuple(((x + 2 * y) / 3 for x, y in zip(a, b)))]
    bits = 0
    for i, p in enumerate(pixels):
        d = [sum(((u - v) ** 2 for u, v in zip(p, q))) for q in pal]
        bits |= d.index(min(d)) << 2 * i
    return struct.pack('<HHI', c0, c1, bits)

def encode_dxt1(w, h, rgb):
    out = bytearray()
    for by in range(0, max(h, 1), 4):
        for bx in range(0, max(w, 1), 4):
            px = [rgb[min(by + (i >> 2), h - 1) * w + min(bx + (i & 3), w - 1)] for i in range(16)]
            out += _encode_block(px)
    return bytes(out)

def mip_chain_dxt1(w, h, rgb, levels):
    out = bytearray()
    for _ in range(levels):
        out += encode_dxt1(w, h, rgb)
        nw, nh = (max(1, w // 2), max(1, h // 2))
        rgb = [tuple((sum((rgb[min(2 * y + dy, h - 1) * w + min(2 * x + dx, w - 1)][k] for dy in (0, 1) for dx in (0, 1))) / 4 for k in range(3))) for y in range(nh) for x in range(nw)]
        w, h = (nw, nh)
    return bytes(out)

def _block_error(pixels, c0, c1):
    a, b = (_rgb565(c0), _rgb565(c1))
    pal = [a, b, tuple(((2 * x + y) / 3 for x, y in zip(a, b))), tuple(((x + 2 * y) / 3 for x, y in zip(a, b)))]
    err, bits = (0.0, 0)
    for i, p in enumerate(pixels):
        d = [sum(((u - v) ** 2 for u, v in zip(p, q))) for q in pal]
        k = d.index(min(d))
        err += d[k]
        bits |= k << 2 * i
    return (err, bits)

def _encode_block_fit(pixels):
    n = len(pixels)
    mean = [sum((p[k] for p in pixels)) / n for k in range(3)]
    cov = [[sum(((p[i] - mean[i]) * (p[j] - mean[j]) for p in pixels)) for j in range(3)] for i in range(3)]
    v = [cov[0][0] + 0.001, cov[1][1] + 0.001, cov[2][2] + 0.001]
    for _ in range(8):
        v = [sum((cov[i][j] * v[j] for j in range(3))) for i in range(3)]
        nv = max((abs(x) for x in v))
        if nv < 1e-09:
            break
        v = [x / nv for x in v]
    ln = sum((x * x for x in v)) ** 0.5
    candidates = []
    if ln > 1e-09:
        v = [x / ln for x in v]
        proj = [sum(((p[k] - mean[k]) * v[k] for k in range(3))) for p in pixels]
        e0 = [mean[k] + max(proj) * v[k] for k in range(3)]
        e1 = [mean[k] + min(proj) * v[k] for k in range(3)]
        for _it in range(3):
            c0, c1 = (_pack565(e0), _pack565(e1))
            candidates.append((c0, c1))
            if c0 == c1:
                break
            hi_, lo_ = (c0, c1) if c0 > c1 else (c1, c0)
            _err, bits = _block_error(pixels, hi_, lo_)
            ws = [(1.0, 0.0, 2 / 3, 1 / 3)[bits >> 2 * i & 3] for i in range(n)]
            if c0 < c1:
                ws = [1 - w for w in ws]
            aa = sum((w * w for w in ws))
            bb = sum(((1 - w) * (1 - w) for w in ws))
            ab = sum((w * (1 - w) for w in ws))
            det = aa * bb - ab * ab
            if abs(det) < 1e-09:
                break
            ne0, ne1 = ([], [])
            for k in range(3):
                x = sum((w * p[k] for w, p in zip(ws, pixels)))
                y = sum(((1 - w) * p[k] for w, p in zip(ws, pixels)))
                ne0.append((x * bb - y * ab) / det)
                ne1.append((y * aa - x * ab) / det)
            e0, e1 = (ne0, ne1)
    lo = [min((p[k] for p in pixels)) for k in range(3)]
    hi = [max((p[k] for p in pixels)) for k in range(3)]
    axis = [h - l for h, l in zip(hi, lo)]
    proj = [sum((a * q for a, q in zip(axis, p))) for p in pixels]
    candidates.append((_pack565(pixels[proj.index(max(proj))]), _pack565(pixels[proj.index(min(proj))])))
    best = None
    for c0, c1 in candidates:
        if c0 < c1:
            c0, c1 = (c1, c0)
        if c0 == c1:
            err = sum((sum(((u - q) ** 2 for u, q in zip(p, _rgb565(c0)))) for p in pixels))
            cand = (err, struct.pack('<HHI', c0, c1, 0))
        else:
            err, bits = _block_error(pixels, c0, c1)
            cand = (err, struct.pack('<HHI', c0, c1, bits))
        if best is None or cand[0] < best[0]:
            best = cand
    return best[1]

def mip_chain_dxt1_fit(w, h, rgb, levels):
    out = bytearray()
    for _ in range(levels):
        for by in range(0, max(h, 1), 4):
            for bx in range(0, max(w, 1), 4):
                out += _encode_block_fit([rgb[min(by + (i >> 2), h - 1) * w + min(bx + (i & 3), w - 1)] for i in range(16)])
        nw, nh = (max(1, w // 2), max(1, h // 2))
        rgb = [tuple((sum((rgb[min(2 * y + dy, h - 1) * w + min(2 * x + dx, w - 1)][k] for dy in (0, 1) for dx in (0, 1))) / 4 for k in range(3))) for y in range(nh) for x in range(nw)]
        w, h = (nw, nh)
    return bytes(out)

def pixels(rgba):
    return [tuple(rgba[i:i + 4]) for i in range(0, len(rgba), 4)]

def _encode_alpha_block(alphas):
    a0, a1 = (max(alphas), min(alphas))
    if a0 == a1:
        return bytes([a0, a1]) + bytes(6)
    pal = [a0, a1] + [((7 - i) * a0 + i * a1) / 7 for i in range(1, 7)]
    bits = 0
    for i, a in enumerate(alphas):
        d = [abs(a - q) for q in pal]
        bits |= d.index(min(d)) << 3 * i
    return bytes([a0, a1]) + bits.to_bytes(6, 'little')

def _encode_alpha_block_dxt3(alphas):
    bits = 0
    for i, a in enumerate(alphas):
        bits |= min(15, max(0, (int(round(a)) + 8) // 17)) << 4 * i
    return bits.to_bytes(8, 'little')

def _encode_colour_4(px):
    seen = [p[:3] for p in px if p[3]] or [p[:3] for p in px]
    block = _encode_block(seen + [seen[0]] * (16 - len(seen)))
    c0, c1 = struct.unpack_from('<HH', block)
    a, b = (_rgb565(c0), _rgb565(c1))
    pal = [a, b, tuple(((2 * x + y) / 3 for x, y in zip(a, b))), tuple(((x + 2 * y) / 3 for x, y in zip(a, b)))]
    bits = 0
    if c0 != c1:
        for i, p in enumerate(px):
            d = [sum(((u - v) ** 2 for u, v in zip(p[:3], q))) for q in pal]
            bits |= d.index(min(d)) << 2 * i
    return struct.pack('<HHI', c0, c1, bits)

def _encode_4(w, h, rgba, alpha_block):
    out = bytearray()
    for by in range(0, max(h, 1), 4):
        for bx in range(0, max(w, 1), 4):
            px = [rgba[min(by + (i >> 2), h - 1) * w + min(bx + (i & 3), w - 1)] for i in range(16)]
            out += alpha_block([int(round(p[3])) for p in px])
            out += _encode_colour_4(px)
    return bytes(out)

def encode_dxt5(w, h, rgba):
    return _encode_4(w, h, rgba, _encode_alpha_block)

def encode_dxt3(w, h, rgba):
    return _encode_4(w, h, rgba, _encode_alpha_block_dxt3)

def downscale_rgba(w, h, rgba):
    nw, nh = (max(1, w // 2), max(1, h // 2))
    out = []
    for y in range(nh):
        for x in range(nw):
            quad = [rgba[min(2 * y + dy, h - 1) * w + min(2 * x + dx, w - 1)] for dy in (0, 1) for dx in (0, 1)]
            wa = sum((p[3] for p in quad))
            if wa:
                col = [sum((p[k] * p[3] for p in quad)) / wa for k in range(3)]
            else:
                col = [sum((p[k] for p in quad)) / 4 for k in range(3)]
            out.append((*col, wa / 4))
    return (nw, nh, out)

def mip_chain_dxt5(w, h, rgba, levels, first=None):
    out = bytearray()
    for level in range(levels):
        out += first if level == 0 and first is not None else encode_dxt5(w, h, rgba)
        w, h, rgba = downscale_rgba(w, h, rgba)
    return bytes(out)

def mip_chain_dxt3(w, h, rgba, levels, first=None):
    out = bytearray()
    for level in range(levels):
        out += first if level == 0 and first is not None else encode_dxt3(w, h, rgba)
        w, h, rgba = downscale_rgba(w, h, rgba)
    return bytes(out)
