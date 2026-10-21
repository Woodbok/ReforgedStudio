#include "scene.h"
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>

namespace {
struct Reader {
    const uint8_t* p;
    const uint8_t* end;
    bool bad = false;
    bool need(size_t n) {
        if ((size_t)(end - p) < n) { bad = true; return false; }
        return true;
    }
    uint32_t u32() { if (!need(4)) return 0; uint32_t v; memcpy(&v, p, 4); p += 4; return v; }
    int32_t i32() { return (int32_t)u32(); }
    std::string str() {
        if (!need(2)) return {};
        uint16_t n; memcpy(&n, p, 2); p += 2;
        if (!need(n)) return {};
        std::string s((const char*)p, n); p += n; return s;
    }
    template <class T> const T* arr(size_t n) {
        if (!need(n * sizeof(T))) return nullptr;
        const T* r = (const T*)p; p += n * sizeof(T); return r;
    }
};

std::wstring widen(const std::string& s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), n);
    if (!w.empty() && w.back() == 0) w.pop_back();
    return w;
}
}  // namespace

std::string uid_hex(uint32_t uid) {
    char b[9];
    snprintf(b, sizeof b, "%02x%02x%02x%02x", uid & 0xFF, (uid >> 8) & 0xFF, (uid >> 16) & 0xFF, uid >> 24);
    return b;
}

uint32_t uid_from_hex(const std::string& h) {
    if (h.size() != 8) return 0;
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) v |= (uint32_t)strtoul(h.substr(i * 2, 2).c_str(), nullptr, 16) << (8 * i);
    return v;
}

void Scene::close() {
    if (data) UnmapViewOfFile(data);
    if (mapping) CloseHandle(mapping);
    if (file && file != INVALID_HANDLE_VALUE) CloseHandle(file);
    data = nullptr; mapping = nullptr; file = nullptr; size = 0;
}

bool Scene::load(const std::string& cache_path, std::string& error) {
    close();
    path = cache_path;
    file = CreateFileW(widen(cache_path).c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) { error = "can't open " + cache_path; file = nullptr; return false; }
    LARGE_INTEGER sz;
    GetFileSizeEx(file, &sz);
    size = (size_t)sz.QuadPart;
    mapping = CreateFileMappingW(file, nullptr, PAGE_READONLY, 0, 0, nullptr);
    if (!mapping) { error = "can't map " + cache_path; return false; }
    data = (const uint8_t*)MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, 0);
    if (!data) { error = "can't map " + cache_path; return false; }

    Reader r{data, data + size};
    if (size < 8 || memcmp(data, "AC3S", 4) != 0) { error = "not a scene cache"; return false; }
    r.p += 4;
    uint32_t version = r.u32();
    if (version != 1) { error = "scene cache version " + std::to_string(version) + " (the app reads 1)"; return false; }
    textures.resize(r.u32());
    for (auto& t : textures) t = r.str();
    materials.resize(r.u32());
    for (auto& m : materials) { m.uid = r.u32(); m.name = r.str(); m.texture = r.i32(); }
    meshes.resize(r.u32());
    for (auto& m : meshes) {
        m.uid = r.u32(); m.name = r.str();
        m.nv = r.u32(); m.nt = r.u32();
        uint32_t nsub = r.u32();
        m.pos = r.arr<float>((size_t)m.nv * 3);
        m.uv = r.arr<float>((size_t)m.nv * 2);
        m.idx = r.arr<uint32_t>((size_t)m.nt * 3);
        m.subs.resize(nsub);
        for (auto& s : m.subs) { s.material = r.i32(); s.first = r.u32(); s.count = r.u32(); }
        if (r.bad) break;
        V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
        for (uint32_t i = 0; i < m.nv; i++) {
            V3 p(m.pos[i * 3], m.pos[i * 3 + 1], m.pos[i * 3 + 2]);
            lo = vmin(lo, p); hi = vmax(hi, p);
        }
        if (!m.nv) lo = hi = V3();
        m.lo = lo; m.hi = hi;
    }
    shapes.resize(r.u32());
    for (auto& s : shapes) {
        s.uid = r.u32(); s.name = r.str();
        s.layer = r.u32(); s.nv = r.u32(); s.nt = r.u32();
        s.pos = r.arr<float>((size_t)s.nv * 3);
        s.idx = r.arr<uint32_t>((size_t)s.nt * 3);
        if (r.bad) break;
    }
    records.resize(r.u32());
    for (auto& rc : records) { rc.name = r.str(); rc.label = r.str(); }
    entities.resize(r.u32());
    for (auto& e : entities) {
        e.uid = r.u32(); e.name = r.str();
        const float* m = r.arr<float>(16);
        if (m) memcpy(e.matrix.m, m, 64);
        e.mesh = r.i32();
        uint32_t ns = r.u32();
        e.shapes.resize(ns);
        for (auto& s : e.shapes) s = r.i32();
        e.record = r.i32(); e.layer = r.i32();
        e.group = r.str(); e.components = r.str();
        e.flags = r.u32(); e.note = r.str();
        if (r.bad) break;
    }
    if (r.bad) { error = "scene cache is cut short"; return false; }
    return true;
}
