"""forgelib/lzo1x.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
M2_MAX_OFFSET = 2048

class Lzo1xError(Exception):
    pass
TOP, FIRST_LIT, MATCH, MATCH_DONE, MATCH_NEXT = range(5)

def decompress(src, expected_size=-1):
    out = bytearray()
    ip = 0
    n = len(src)

    def u8():
        nonlocal ip
        if ip >= n:
            raise Lzo1xError(f'input overrun at {ip} of {n}')
        b = src[ip]
        ip += 1
        return b

    def literals(count):
        nonlocal ip
        if ip + count > n:
            raise Lzo1xError(f'literal run of {count} overruns input at {ip}')
        out.extend(src[ip:ip + count])
        ip += count

    def match(dist, length):
        if dist <= 0 or dist > len(out):
            raise Lzo1xError(f'distance {dist} with only {len(out)} bytes out')
        start = len(out) - dist
        for i in range(length):
            out.append(out[start + i])

    def long_length(base):
        extra = 0
        while True:
            b = u8()
            if b != 0:
                return base + extra + b
            extra += 255
    if n == 0:
        if expected_size > 0:
            raise Lzo1xError(f'empty input, expected {expected_size} bytes')
        return (b'', 0)
    t = src[ip]
    if t > 17:
        ip += 1
        t -= 17
        if t < 4:
            state = MATCH_NEXT
        else:
            literals(t)
            state = FIRST_LIT
    else:
        state = TOP
    while True:
        if state == TOP:
            t = u8()
            if t >= 16:
                state = MATCH
                continue
            if t == 0:
                t = long_length(15)
            literals(t + 3)
            state = FIRST_LIT
            continue
        if state == FIRST_LIT:
            t = u8()
            if t >= 16:
                state = MATCH
                continue
            dist = 1 + M2_MAX_OFFSET + (t >> 2) + (u8() << 2)
            match(dist, 3)
            state = MATCH_DONE
            continue
        if state == MATCH:
            if t >= 64:
                length = (t >> 5) + 1
                dist = 1 + (t >> 2 & 7) + (u8() << 3)
                match(dist, length)
            elif t >= 32:
                length = t & 31
                if length == 0:
                    length = long_length(31)
                lo, hi = (u8(), u8())
                dist = 1 + ((lo | hi << 8) >> 2)
                match(dist, length + 2)
            elif t >= 16:
                high_bit = (t & 8) << 11
                length = t & 7
                if length == 0:
                    length = long_length(7)
                lo, hi = (u8(), u8())
                dist = high_bit + ((lo | hi << 8) >> 2)
                if dist == 0:
                    break
                match(dist + 16384, length + 2)
            else:
                dist = 1 + (t >> 2) + (u8() << 2)
                match(dist, 2)
            state = MATCH_DONE
            continue
        if state == MATCH_DONE:
            t = src[ip - 2] & 3
            state = TOP if t == 0 else MATCH_NEXT
            continue
        if state == MATCH_NEXT:
            literals(t)
            t = u8()
            state = MATCH
            continue
    if expected_size >= 0 and len(out) != expected_size:
        raise Lzo1xError(f'decoded {len(out)} bytes, expected {expected_size}')
    return (bytes(out), ip)
