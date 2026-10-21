// Where a map can be climbed (engine climbview.py) - every climb edge with its owner, and per collision shape the
// triangles on a no-climb material (FuzzyZone, Z_Camonly: no climbing or wall-running there).
#pragma once
#include <string>
#include <unordered_map>
#include <vector>
#include "json.hpp"
#include "math3d.h"
#include "scene.h"

struct ClimbEdge {
    V3 a, b;
    int kind = 1;                  // 1 ledge, 4 pole, 14 beam (others as they come)
    float hx = 0, hy = 0;          // the side the climber hangs on (ground direction)
    int owner = -1;                // the map entity holding it (-1: none known)
};

struct ClimbView {
    bool loaded = false, loading = false;
    std::vector<ClimbEdge> edges;
    std::unordered_map<int, std::vector<int>> by_owner;          // entity index -> its edges
    std::unordered_map<int, std::vector<std::pair<int, int>>> noclimb;   // shape index -> [first, count] runs
    std::unordered_map<int, std::vector<int>> members;           // entity index -> its member shapes (merged pieces)
    int n_noclimb = 0, n_tris = 0;

    // from the engine's "climb" result; entity / shape indices resolved through the scene
    void read(const nlohmann::json& r, const Scene& s);
    bool tri_noclimb(int shape, int tri) const;
    static const char* kind_name(int k) { return k == 1 ? "ledge" : k == 4 ? "pole" : k == 14 ? "beam" : "edge"; }
};

// a model's own climbing (the asset scene's meta "climb": edges in the model's frame, no-climb triangle runs)
struct AssetClimb {
    std::vector<ClimbEdge> edges;
    std::vector<std::pair<int, int>> noclimb;
    void read(const nlohmann::json& c);
    bool tri_noclimb(int tri) const;
};
