#include "climbview.h"

using json = nlohmann::json;

static ClimbEdge edge_of(const json& e) {
    ClimbEdge c;
    c.a = V3(e[0].get<float>(), e[1].get<float>(), e[2].get<float>());
    c.b = V3(e[3].get<float>(), e[4].get<float>(), e[5].get<float>());
    c.kind = e[6].get<int>();
    if (e.size() >= 9) { c.hx = e[7].get<float>(); c.hy = e[8].get<float>(); }
    return c;
}

static std::vector<std::pair<int, int>> runs_of(const json& j) {
    std::vector<std::pair<int, int>> out;
    for (auto& r : j) out.push_back({r[0].get<int>(), r[1].get<int>()});
    return out;
}

static bool in_runs(const std::vector<std::pair<int, int>>& runs, int tri) {
    for (auto& [f, n] : runs)
        if (tri >= f && tri < f + n) return true;
    return false;
}

void ClimbView::read(const json& r, const Scene& s) {
    std::unordered_map<uint32_t, int> ent, shp;
    for (size_t i = 0; i < s.entities.size(); i++) ent.emplace(s.entities[i].uid, (int)i);
    for (size_t i = 0; i < s.shapes.size(); i++) shp.emplace(s.shapes[i].uid, (int)i);
    const json& es = r["edges"];
    const json& ow = r.value("owners", json::array());
    edges.clear();
    by_owner.clear();
    for (size_t k = 0; k < es.size(); k++) {
        ClimbEdge c = edge_of(es[k]);
        if (k < ow.size()) {
            auto it = ent.find(uid_from_hex(ow[k].get<std::string>()));
            if (it != ent.end()) c.owner = it->second;
        }
        if (c.owner >= 0) by_owner[c.owner].push_back((int)edges.size());
        edges.push_back(c);
    }
    noclimb.clear();
    const json nc = r.value("noclimb", json::object()), mem = r.value("members", json::object());
    for (auto& [h, runs] : nc.items()) {
        auto it = shp.find(uid_from_hex(h));
        if (it != shp.end()) noclimb[it->second] = runs_of(runs);
    }
    members.clear();
    for (auto& [h, sids] : mem.items()) {
        auto it = ent.find(uid_from_hex(h));
        if (it == ent.end()) continue;
        for (auto& x : sids) {
            auto is = shp.find(uid_from_hex(x.get<std::string>()));
            if (is != shp.end()) members[it->second].push_back(is->second);
        }
    }
    auto c = r.value("counts", json::object());
    n_noclimb = c.value("noclimb_triangles", 0);
    n_tris = c.value("collision_triangles", 0);
    loaded = true;
}

bool ClimbView::tri_noclimb(int shape, int tri) const {
    auto it = noclimb.find(shape);
    return it != noclimb.end() && in_runs(it->second, tri);
}

void AssetClimb::read(const json& c) {
    edges.clear();
    for (auto& e : c.value("edges", json::array())) edges.push_back(edge_of(e));
    noclimb = runs_of(c.value("noclimb", json::array()));
}

bool AssetClimb::tri_noclimb(int tri) const { return in_runs(noclimb, tri); }
