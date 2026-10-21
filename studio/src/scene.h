// A map's scene cache (studio/engine/scene_cache.py writes it), memory-mapped: geometry arrays point into the file.
#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include "math3d.h"

enum EntityFlags : uint32_t {
    F_VOLUME = 1, F_SPAWN = 2, F_START = 4, F_FIXED = 8, F_MODE = 16, F_GROUP_HEAD = 32,
};

struct Sub { int material; uint32_t first, count; };            // a run of triangles in one material

struct Mesh {
    uint32_t uid;
    std::string name;
    uint32_t nv = 0, nt = 0;
    const float* pos = nullptr;      // nv * 3
    const float* uv = nullptr;       // nv * 2
    const uint32_t* idx = nullptr;   // nt * 3
    std::vector<Sub> subs;
    V3 lo, hi;                       // local bounds
};

struct Shape {
    uint32_t uid;
    std::string name;
    uint32_t layer = 0, nv = 0, nt = 0;
    const float* pos = nullptr;
    const uint32_t* idx = nullptr;
};

struct Material { uint32_t uid; std::string name; int texture; };

struct Record { std::string name, label; };                       // label: the game mode of a DataBlock record

struct Entity {
    uint32_t uid;
    std::string name;
    M4 matrix;                       // as in the archive
    int mesh = -1;
    std::vector<int> shapes;
    int record = -1, layer = -1;
    std::string group, components, note;
    uint32_t flags = 0;
};

struct Scene {
    std::string path, archive, title;
    std::vector<std::string> textures;
    std::vector<Material> materials;
    std::vector<Mesh> meshes;
    std::vector<Shape> shapes;
    std::vector<Record> records;
    std::vector<Entity> entities;
    void* file = nullptr;
    void* mapping = nullptr;
    const uint8_t* data = nullptr;
    size_t size = 0;

    bool load(const std::string& cache_path, std::string& error);
    void close();
    ~Scene() { close(); }
};

std::string uid_hex(uint32_t uid);          // the uid as ac3-editor writes it (bytes.hex(): little-endian bytes)
uint32_t uid_from_hex(const std::string& h);
