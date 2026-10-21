// Small vector / matrix helpers. Matrices are AC3's (and D3D's, and ImGuizmo's) row-vector convention:
// 16 floats row-major, p' = p * M, translation in m[12..14]. A * B applies A first, then B. The world is Z up.
#pragma once
#include <cmath>
#include <cstring>
#include <algorithm>

struct V3 {
    float x = 0, y = 0, z = 0;
    V3() = default;
    V3(float x_, float y_, float z_) : x(x_), y(y_), z(z_) {}
    V3 operator+(const V3& o) const { return {x + o.x, y + o.y, z + o.z}; }
    V3 operator-(const V3& o) const { return {x - o.x, y - o.y, z - o.z}; }
    V3 operator*(float s) const { return {x * s, y * s, z * s}; }
    V3& operator+=(const V3& o) { x += o.x; y += o.y; z += o.z; return *this; }
    float& operator[](int i) { return (&x)[i]; }
    float operator[](int i) const { return (&x)[i]; }
};
inline float dot(V3 a, V3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline V3 cross(V3 a, V3 b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
inline float length(V3 a) { return std::sqrt(dot(a, a)); }
inline V3 normalize(V3 a) { float l = length(a); return l > 1e-12f ? a * (1.0f / l) : V3(0, 0, 1); }
inline V3 vmin(V3 a, V3 b) { return {std::min(a.x, b.x), std::min(a.y, b.y), std::min(a.z, b.z)}; }
inline V3 vmax(V3 a, V3 b) { return {std::max(a.x, b.x), std::max(a.y, b.y), std::max(a.z, b.z)}; }

struct M4 {
    float m[16];
    static M4 identity() { M4 r{}; r.m[0] = r.m[5] = r.m[10] = r.m[15] = 1; return r; }
    M4 operator*(const M4& b) const {
        M4 r{};
        for (int i = 0; i < 4; i++)
            for (int j = 0; j < 4; j++) {
                float s = 0;
                for (int k = 0; k < 4; k++) s += m[i * 4 + k] * b.m[k * 4 + j];
                r.m[i * 4 + j] = s;
            }
        return r;
    }
    V3 point(V3 p) const {
        return {p.x * m[0] + p.y * m[4] + p.z * m[8] + m[12], p.x * m[1] + p.y * m[5] + p.z * m[9] + m[13],
                p.x * m[2] + p.y * m[6] + p.z * m[10] + m[14]};
    }
    V3 dir(V3 p) const {
        return {p.x * m[0] + p.y * m[4] + p.z * m[8], p.x * m[1] + p.y * m[5] + p.z * m[9],
                p.x * m[2] + p.y * m[6] + p.z * m[10]};
    }
    V3 pos() const { return {m[12], m[13], m[14]}; }
    void set_pos(V3 p) { m[12] = p.x; m[13] = p.y; m[14] = p.z; }
    bool near_equal(const M4& o, float tol = 1e-4f) const {
        for (int i = 0; i < 16; i++)
            if (std::fabs(m[i] - o.m[i]) > tol) return false;
        return true;
    }
};

inline M4 inverse(const M4& a) {
    // general 4x4 inverse (cofactors)
    const float* m = a.m;
    float inv[16];
    inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10];
    inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10];
    inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9];
    inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9];
    inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10];
    inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10];
    inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9];
    inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9];
    inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6];
    inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6];
    inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5];
    inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5];
    inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6];
    inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6];
    inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5];
    inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5];
    float det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
    M4 r{};
    if (std::fabs(det) < 1e-20f) return M4::identity();
    det = 1.0f / det;
    for (int i = 0; i < 16; i++) r.m[i] = inv[i] * det;
    return r;
}

inline M4 translation(V3 t) { M4 r = M4::identity(); r.set_pos(t); return r; }

inline M4 rot_z(float rad) {
    M4 r = M4::identity();
    float c = std::cos(rad), s = std::sin(rad);
    r.m[0] = c; r.m[1] = s; r.m[4] = -s; r.m[5] = c;
    return r;
}

// right-handed look-at, Z up
inline M4 look_at(V3 eye, V3 target, V3 up) {
    V3 z = normalize(eye - target), x = normalize(cross(up, z)), y = cross(z, x);
    M4 r = M4::identity();
    r.m[0] = x.x; r.m[1] = y.x; r.m[2] = z.x;
    r.m[4] = x.y; r.m[5] = y.y; r.m[6] = z.y;
    r.m[8] = x.z; r.m[9] = y.z; r.m[10] = z.z;
    r.m[12] = -dot(x, eye); r.m[13] = -dot(y, eye); r.m[14] = -dot(z, eye);
    return r;
}

// right-handed perspective, depth 0..1 (D3D)
inline M4 perspective(float fovy, float aspect, float zn, float zf) {
    float ys = 1.0f / std::tan(fovy * 0.5f), xs = ys / aspect;
    M4 r{};
    r.m[0] = xs; r.m[5] = ys;
    r.m[10] = zf / (zn - zf); r.m[11] = -1;
    r.m[14] = zn * zf / (zn - zf);
    return r;
}

// ray / triangle (Moller-Trumbore), two-sided; -> distance or -1
inline float ray_tri(V3 o, V3 d, V3 a, V3 b, V3 c) {
    V3 e1 = b - a, e2 = c - a, p = cross(d, e2);
    float det = dot(e1, p);
    if (std::fabs(det) < 1e-10f) return -1;
    float inv = 1.0f / det;
    V3 t = o - a;
    float u = dot(t, p) * inv;
    if (u < 0 || u > 1) return -1;
    V3 q = cross(t, e1);
    float v = dot(d, q) * inv;
    if (v < 0 || u + v > 1) return -1;
    float s = dot(e2, q) * inv;
    return s > 0 ? s : -1;
}

// ray / box slab test; -> entry distance or -1
inline float ray_box(V3 o, V3 d, V3 lo, V3 hi) {
    float t0 = 0, t1 = 1e30f;
    for (int i = 0; i < 3; i++) {
        if (std::fabs(d[i]) < 1e-12f) {
            if (o[i] < lo[i] || o[i] > hi[i]) return -1;
            continue;
        }
        float a = (lo[i] - o[i]) / d[i], b = (hi[i] - o[i]) / d[i];
        if (a > b) std::swap(a, b);
        t0 = std::max(t0, a);
        t1 = std::min(t1, b);
        if (t0 > t1) return -1;
    }
    return t0;
}
