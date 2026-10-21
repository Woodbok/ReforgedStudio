/* ac3lzo.c - datafile.py's payload decoder (LZO2A and LZO1X blocks) and its LZO2A encoder, in C.

   Built by native/build.py into native/ac3lzo.dll (x64, loaded through ctypes by datafile.py). Optional: without the
   DLL - or with AC3FORGE_NO_NATIVE=1 - datafile.py runs its pure-Python code, which stays the reference.

   Byte for byte the Python, by construction:
     * the decoder takes only the path on which every check of datafile._decompress_uncached passes (magic, version,
       codec, block table, every block producing exactly its raw size and consuming exactly its compressed size, no
       slice running past the payload). Anything else returns non-zero and datafile.py runs the Python decoder, which
       then gives its own result or its own error - so errors, odd payloads and slack keep the Python behaviour.
     * the LZO2A decoder is datafile._lzo2a (the reference: the same bit reads, the same lookbehind into everything
       decoded before the block); the LZO1X decoder is lzo1x.decompress (its five-state machine).
     * the encoder is datafile.lzo2a_compress: the same greedy choice. Its Python hash chains (dict of lists, only the
       last 32 entries ever looked at, trimmed to 32 past 128) are here a head-per-key table plus a link per position,
       walked newest first for at most 32 links - the same 32 candidates in the same order. Positions enter a chain at
       most once and in increasing order (the main position, then the ones a match covers), so the two agree.

   The formats are liblzo2's published LZO2A / LZO1X (lzo-2.10, GPLv2+, Markus F.X.J. Oberhumer), re-expressed for
   interoperability as datafile.py / lzo1x.py describe; this file is part of ac3-forge (GPLv3+). */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define EXPORT __declspec(dllexport)

#define MAGIC 0x57FBAA33u
#define VERSION 0x1004FA99u
#define CHUNK_HEADER_SIZE 0x0F
#define CODEC_LZO1X_1 0
#define CODEC_LZO2A 2

static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }

EXPORT int ac3lzo_version(void) { return 1; }

/* ---- LZO2A (datafile._lzo2a) ----
   Decodes src[0:n] onto out (out_len bytes already there: the lookbehind window), at most `raw` new bytes.
   Returns the new bytes count, or -1 on anything the Python would raise on or decode differently. *consumed = ip. */
static int64_t lzo2a_block(const uint8_t *src, int64_t n, uint8_t *out, int64_t out_len, int64_t raw, int64_t *consumed)
{
    int64_t ip = 0, op = out_len, start = out_len, lim = out_len + raw;
    uint32_t bb = 0, bc = 0;
#define NEED(j) do { if (bc < (j)) { if (ip >= n) return -1; bb |= (uint32_t)src[ip++] << bc; bc += 8; } } while (0)
    while (ip < n) {
        NEED(1);
        if ((bb & 1) == 0) {                        /* literal */
            bb >>= 1; bc -= 1;
            if (ip >= n) return -1;
            if (op >= lim) return -1;               /* Python lets literals run past raw; that payload goes to Python */
            out[op++] = src[ip++];
            continue;
        }
        bb >>= 1; bc -= 1;
        NEED(1);
        int64_t m_pos, length;
        if ((bb & 1) == 0) {                        /* short match: distance 1..256, length 2..5 */
            bb >>= 1; bc -= 1;
            NEED(2);
            length = 2 + (bb & 3);
            bb >>= 2; bc -= 2;
            if (ip >= n) return -1;
            m_pos = op - 1 - src[ip++];
        } else {
            bb >>= 1; bc -= 1;
            if (n - ip < 2) return -1;
            uint32_t b1 = src[ip], b2 = src[ip + 1];
            ip += 2;
            m_pos = op - (int64_t)((b1 & 31) | (b2 << 5));
            uint32_t cls = b1 >> 5;
            if (cls == 0) {
                length = 9;
                for (;;) {
                    if (ip >= n) return -1;
                    if (src[ip] != 0) break;
                    length += 255;
                    ip++;
                }
                length += src[ip++];
            } else {
                if (m_pos == op) { *consumed = ip; return op - start; }   /* end of stream */
                length = cls + 2;
            }
        }
        if (m_pos < 0 || m_pos >= op) return -1;    /* lookbehind overrun */
        if (op - start + length > raw) return -1;   /* output overrun */
        for (int64_t k = 0; k < length; k++) out[op + k] = out[m_pos + k];   /* forward: overlapping repeats */
        op += length;
    }
    return -1;                                      /* no end-of-stream marker */
#undef NEED
}

/* ---- LZO1X (lzo1x.decompress) ----
   Decodes src[0:n] into out (block-local: matches never reach before it), exactly `raw` bytes. */
enum { TOP, FIRST_LIT, MATCH, MATCH_DONE, MATCH_NEXT };

static int lzo1x_block(const uint8_t *src, int64_t n, uint8_t *out, int64_t raw, int64_t *consumed)
{
    int64_t ip = 0, op = 0;
    uint32_t t;
    int state;
#define U8(v) do { if (ip >= n) return -1; (v) = src[ip++]; } while (0)
#define LITS(c) do { int64_t c_ = (c); if (ip + c_ > n) return -1; if (op + c_ > raw) return -1; \
                     memcpy(out + op, src + ip, (size_t)c_); op += c_; ip += c_; } while (0)
#define MATCHCOPY(d, l) do { int64_t d_ = (d), l_ = (l); if (d_ <= 0 || d_ > op) return -1; if (op + l_ > raw) return -1; \
                     for (int64_t k_ = 0; k_ < l_; k_++) out[op + k_] = out[op - d_ + k_]; op += l_; } while (0)
#define LONGLEN(base, res) do { int64_t x_ = 0; uint32_t b_; for (;;) { U8(b_); if (b_ != 0) { (res) = (base) + x_ + b_; break; } x_ += 255; } } while (0)
    if (n == 0) return -1;
    t = src[ip];
    if (t > 17) {
        ip++;
        t -= 17;
        if (t < 4) state = MATCH_NEXT;
        else { LITS(t); state = FIRST_LIT; }
    } else state = TOP;
    for (;;) {
        switch (state) {
        case TOP: {
            U8(t);
            if (t >= 16) { state = MATCH; break; }
            int64_t c = t;
            if (t == 0) LONGLEN(15, c);
            LITS(c + 3);
            state = FIRST_LIT;
            break;
        }
        case FIRST_LIT: {
            U8(t);
            if (t >= 16) { state = MATCH; break; }
            uint32_t b; U8(b);
            MATCHCOPY(1 + 0x0800 + (t >> 2) + (b << 2), 3);
            state = MATCH_DONE;
            break;
        }
        case MATCH: {
            uint32_t lo, hi, b;
            if (t >= 64) {
                int64_t length = (t >> 5) + 1;
                U8(b);
                MATCHCOPY(1 + ((t >> 2) & 7) + (b << 3), length);
            } else if (t >= 32) {
                int64_t length = t & 31;
                if (length == 0) LONGLEN(31, length);
                U8(lo); U8(hi);
                MATCHCOPY(1 + ((lo | (hi << 8)) >> 2), length + 2);
            } else if (t >= 16) {
                int64_t high = (int64_t)(t & 8) << 11;
                int64_t length = t & 7;
                if (length == 0) LONGLEN(7, length);
                U8(lo); U8(hi);
                int64_t dist = high + ((lo | (hi << 8)) >> 2);
                if (dist == 0) {                    /* end of stream */
                    if (op != raw) return -1;
                    *consumed = ip;
                    return 0;
                }
                MATCHCOPY(dist + 0x4000, length + 2);
            } else {
                U8(b);
                MATCHCOPY(1 + (t >> 2) + (b << 2), 2);
            }
            state = MATCH_DONE;
            break;
        }
        case MATCH_DONE:
            t = src[ip - 2] & 3;
            state = t == 0 ? TOP : MATCH_NEXT;
            break;
        case MATCH_NEXT:
            LITS(t);
            U8(t);
            state = MATCH;
            break;
        }
    }
#undef U8
#undef LITS
#undef MATCHCOPY
#undef LONGLEN
}

/* The payload's decoded size from its block tables (the sum of every block's raw size), or -1 when its chunk layout
   is anything but the plain one (the Python then decodes it). */
EXPORT int64_t ac3lzo_payload_size(const uint8_t *p, int64_t n)
{
    int64_t o = 4, total = 0;
    while (o + CHUNK_HEADER_SIZE <= n) {
        if (rd32(p + o) != MAGIC || rd32(p + o + 4) != VERSION) return -1;
        uint8_t codec = p[o + 0x0A];
        if (codec != CODEC_LZO2A && codec != CODEC_LZO1X_1) return -1;
        if (!(rd32(p + o + 0x0B) & 0x80000000u)) return -1;
        int64_t q = o + CHUNK_HEADER_SIZE;
        if (q + 2 > n) return -1;
        uint32_t count = rd16(p + q);
        q += 2;
        if (q + 4 * (int64_t)count > n) return -1;
        int64_t body = q + 4 * (int64_t)count;
        for (uint32_t i = 0; i < count; i++) {
            uint32_t v = rd32(p + q + 4 * i);
            total += v & 0xFFFF;
            body += 4 + (v >> 16);
        }
        if (body > n) return -1;
        o = body;
    }
    return total;
}

/* Decodes the payload into out (cap bytes = ac3lzo_payload_size). 0 = done (*out_len bytes), non-zero = use Python. */
EXPORT int ac3lzo_payload_decode(const uint8_t *p, int64_t n, uint8_t *out, int64_t cap, int64_t *out_len)
{
    int64_t o = 4, op = 0;
    while (o + CHUNK_HEADER_SIZE <= n) {
        if (rd32(p + o) != MAGIC || rd32(p + o + 4) != VERSION) return 1;
        uint8_t codec = p[o + 0x0A];
        if (codec != CODEC_LZO2A && codec != CODEC_LZO1X_1) return 2;
        if (!(rd32(p + o + 0x0B) & 0x80000000u)) return 3;
        int64_t q = o + CHUNK_HEADER_SIZE;
        if (q + 2 > n) return 4;
        uint32_t count = rd16(p + q);
        q += 2;
        if (q + 4 * (int64_t)count > n) return 4;
        const uint8_t *table = p + q;
        q += 4 * (int64_t)count;
        for (uint32_t i = 0; i < count; i++) {
            uint32_t v = rd32(table + 4 * i);
            int64_t raw = v & 0xFFFF, comp = v >> 16;
            q += 4;
            if (q + comp > n) return 5;
            if (op + raw > cap) return 6;
            if (raw == comp) {
                memcpy(out + op, p + q, (size_t)comp);
                op += comp;
            } else if (codec == CODEC_LZO2A) {
                int64_t consumed = -1;
                int64_t made = lzo2a_block(p + q, comp, out, op, raw, &consumed);
                if (made != raw || consumed != comp) return 7;
                op += made;
            } else {
                int64_t consumed = -1;
                if (lzo1x_block(p + q, comp, out + op, raw, &consumed) != 0 || consumed != comp) return 8;
                op += raw;
            }
            q += comp;
        }
        o = q;
    }
    *out_len = op;
    return 0;
}

/* ---- the LZO2A encoder (datafile.lzo2a_compress) ---- */
#define WINDOW 8191
#define CHAIN 32
#define HBITS 17
#define HSIZE (1 << HBITS)

typedef struct {
    uint8_t *out;
    int64_t len, cap;
    int64_t *bit_bytes;
    int64_t nbit_bytes, bit_cap;
    int64_t held;        /* bits the decoder still holds */
    int64_t written;     /* bits written */
    int fail;
} Enc;

static void put_byte(Enc *e, uint8_t b)
{
    if (e->len >= e->cap) { e->fail = 1; return; }
    e->out[e->len++] = b;
}

static void put_bits(Enc *e, uint32_t value, int count)
{
    if (e->held < count) {                          /* the decoder fetches a bit byte here */
        if (e->nbit_bytes >= e->bit_cap) { e->fail = 1; return; }
        e->bit_bytes[e->nbit_bytes++] = e->len;
        put_byte(e, 0);
        if (e->fail) return;
        e->held += 8;
    }
    for (int k = 0; k < count; k++) {
        int64_t w = e->written;
        e->out[e->bit_bytes[w >> 3]] |= (uint8_t)((value & 1) << (w & 7));
        value >>= 1;
        e->written++;
    }
    e->held -= count;
}

static void long_code(Enc *e, int64_t dist, int cls)
{
    put_bits(e, 1, 1);
    put_bits(e, 1, 1);
    put_byte(e, (uint8_t)((dist & 31) | (cls << 5)));
    put_byte(e, (uint8_t)(dist >> 5));
}

/* the chain heads: open addressing on the exact 3-byte key */
static int32_t *head_slot(uint32_t *keys, int32_t *heads, uint32_t key)
{
    uint32_t h = (key * 2654435761u) >> (32 - HBITS);
    for (;;) {
        if (keys[h] == 0xFFFFFFFFu) { keys[h] = key; heads[h] = -1; return &heads[h]; }
        if (keys[h] == key) return &heads[h];
        h = (h + 1) & (HSIZE - 1);
    }
}

static uint32_t key3(const uint8_t *d) { return (uint32_t)d[0] | (uint32_t)d[1] << 8 | (uint32_t)d[2] << 16; }

/* Compresses data[0:n] (n <= 65535) into out (cap bytes). Returns the stream's length, or -1 (out of room / memory;
   datafile.py then uses the Python encoder). */
EXPORT int64_t ac3lzo_lzo2a_compress(const uint8_t *data, int64_t n, uint8_t *out, int64_t cap)
{
    if (n < 0 || n > 65535) return -1;
    uint32_t *keys = (uint32_t *)malloc(sizeof(uint32_t) * HSIZE);
    int32_t *heads = (int32_t *)malloc(sizeof(int32_t) * HSIZE);
    int32_t *prev = (int32_t *)malloc(sizeof(int32_t) * (size_t)(n + 1));
    int32_t *last_pair = (int32_t *)malloc(sizeof(int32_t) * 65536);
    Enc e = {0};
    e.out = out; e.cap = cap;
    e.bit_cap = n + 16;
    e.bit_bytes = (int64_t *)malloc(sizeof(int64_t) * (size_t)e.bit_cap);
    int64_t result = -1;
    if (!keys || !heads || !prev || !last_pair || !e.bit_bytes) goto done;
    memset(keys, 0xFF, sizeof(uint32_t) * HSIZE);
    memset(last_pair, 0xFF, sizeof(int32_t) * 65536);

    int64_t i = 0;
    while (i < n) {
        int64_t best_len = 0, best_dist = 0;
        if (i + 3 <= n) {
            int32_t *slot = head_slot(keys, heads, key3(data + i));
            int64_t limit = n - i;
            int32_t p = *slot;
            for (int c = 0; c < CHAIN && p >= 0; c++, p = prev[p]) {
                int64_t dist = i - p;
                if (dist > WINDOW) break;
                if (best_len && (best_len >= limit || data[p + best_len] != data[i + best_len])) continue;
                int64_t length = 3;
                while (length < limit && data[p + length] == data[i + length]) length++;
                if (length > best_len) {
                    best_len = length; best_dist = dist;
                    if (length >= limit) break;
                }
            }
            prev[i] = *slot;                         /* chain.append(i) / heads[key] = [i] */
            *slot = (int32_t)i;
        }
        if (i + 2 <= n) {                            /* a 2-byte match only as the short code */
            uint32_t pair = (uint32_t)data[i] | (uint32_t)data[i + 1] << 8;
            if (best_len < 3) {
                int32_t p = last_pair[pair];
                if (p >= 0 && i - p <= 256) { best_len = 2; best_dist = i - p; }
            }
            last_pair[pair] = (int32_t)i;
        }
        if (best_len >= 2 && best_dist <= 256 && best_len <= 5) {
            put_bits(&e, 1, 1);
            put_bits(&e, 0, 1);
            put_bits(&e, (uint32_t)(best_len - 2), 2);
            put_byte(&e, (uint8_t)(best_dist - 1));
        } else if (best_len >= 3) {
            if (best_len <= 9) long_code(&e, best_dist, (int)(best_len - 2));
            else {
                long_code(&e, best_dist, 0);
                int64_t extra = best_len - 9, zeros = (extra - 1) / 255;
                for (int64_t z = 0; z < zeros; z++) put_byte(&e, 0);
                put_byte(&e, (uint8_t)(extra - 255 * zeros));
            }
        } else {
            put_bits(&e, 0, 1);
            put_byte(&e, data[i]);
            i += 1;
            if (e.fail) goto done;
            continue;
        }
        if (e.fail) goto done;
        /* the positions a match covers go into the chains too (capped) */
        int64_t end = i + best_len;
        if (i + 64 < end) end = i + 64;
        if (n - 2 < end) end = n - 2;
        for (int64_t q = i + 1; q < end; q++) {
            int32_t *slot = head_slot(keys, heads, key3(data + q));
            prev[q] = *slot;
            *slot = (int32_t)q;
            last_pair[(uint32_t)data[q] | (uint32_t)data[q + 1] << 8] = (int32_t)q;
        }
        i += best_len;
    }
    long_code(&e, 0, 1);                             /* end of stream */
    if (!e.fail) result = e.len;
done:
    free(keys); free(heads); free(prev); free(last_pair); free(e.bit_bytes);
    return result;
}
