#include "edits.h"

static bool same_state(const EditState& a, const EditState& b) {
    if (a.removed != b.removed || a.building != b.building || a.climbable != b.climbable ||
        a.copies.size() != b.copies.size()) return false;
    for (size_t i = 0; i < a.matrix.size(); i++)
        if (memcmp(a.matrix[i].m, b.matrix[i].m, 64) != 0) return false;
    if (a.group_root.size() != b.group_root.size()) return false;
    for (size_t i = 0; i < a.group_root.size(); i++)
        if (memcmp(a.group_root[i].m, b.group_root[i].m, 64) != 0) return false;
    if (a.regions.size() != b.regions.size()) return false;
    for (size_t i = 0; i < a.regions.size(); i++) {
        const RegionEdit &x = a.regions[i], &y = b.regions[i];
        if (x.name != y.name || x.dz != y.dz || x.removed != y.removed || x.pts.size() != y.pts.size() ||
            x.kind != y.kind || x.new_routes != y.new_routes) return false;
        for (size_t k = 0; k < x.pts.size(); k++)
            if (x.pts[k].x != y.pts[k].x || x.pts[k].y != y.pts[k].y || x.pts[k].z != y.pts[k].z) return false;
    }
    if (a.routes != b.routes) return false;
    if (a.map != b.map) return false;
    if (a.sculpts != b.sculpts) return false;
    for (size_t i = 0; i < a.copies.size(); i++)
        if (a.copies[i].src != b.copies[i].src || a.copies[i].removed != b.copies[i].removed ||
            a.copies[i].asset != b.copies[i].asset || a.copies[i].solid != b.copies[i].solid ||
            a.copies[i].group != b.copies[i].group || a.copies[i].path != b.copies[i].path ||
            a.copies[i].climb != b.copies[i].climb ||
            memcmp(a.copies[i].matrix.m, b.copies[i].matrix.m, 64) != 0)
            return false;
    return true;
}

void Edits::reset(const Scene* s) {
    scene = s;
    cur = EditState();
    history.clear();
    building_keys.clear();
    by_uid.clear();
    groups.clear();
    at = saved_at = -1;
    if (!s) return;
    size_t n = s->entities.size();
    cur.matrix.resize(n);
    cur.removed.assign(n, 0);
    cur.building.assign(n, -1);
    cur.climbable.assign(n, 0);
    for (size_t i = 0; i < n; i++) {
        cur.matrix[i] = s->entities[i].matrix;
        if (!(s->entities[i].flags & F_SPAWN)) by_uid.emplace(s->entities[i].uid, (int)i);
    }
    history.push_back(cur);
    at = saved_at = 0;
}

bool Edits::dirty_uncommitted() const {
    return at >= 0 && at < (int)history.size() && !same_state(cur, history[at]);
}

void Edits::commit() {
    if (at >= 0 && same_state(cur, history[at])) return;
    history.resize(at + 1);
    history.push_back(cur);
    at = (int)history.size() - 1;
    const int cap = 200;
    if ((int)history.size() > cap) {
        int drop = (int)history.size() - cap;
        history.erase(history.begin(), history.begin() + drop);
        at -= drop;
        saved_at = saved_at >= drop ? saved_at - drop : -2;
    }
}

void Edits::undo() {
    if (dirty_uncommitted()) { cur = history[at]; return; }
    if (at > 0) cur = history[--at];
}

void Edits::redo() {
    if (at + 1 < (int)history.size()) cur = history[++at];
}

void Edits::set_removed(int id, bool r) {
    if (is_copy(id)) cur.copies[id - scene->entities.size()].removed = r;
    else cur.removed[id] = r ? 1 : 0;
}

bool Edits::moved(int id) const {
    if (is_copy(id)) return false;
    return !cur.matrix[id].near_equal(scene->entities[id].matrix, 1e-5f);
}

bool Edits::movable(int id) const {
    if (is_placed(id)) return true;
    const Entity& e = scene->entities[entity_of(id)];
    if (e.flags & F_SPAWN) return true;           // spawn points per mode (match starts too: spawns.py copies their raw bytes)
    return !(e.flags & F_FIXED);
}

int Edits::asset_index(const std::string& key, const std::string& name, const std::string& map) {
    for (size_t i = 0; i < assets.size(); i++)
        if (assets[i].key == key) return (int)i;
    assets.push_back({key, name, map});
    return (int)assets.size() - 1;
}

int Edits::add_group(const std::string& name, const std::string& asset, const M4& root) {
    groups.push_back({name, asset, {}});
    cur.group_root.resize(groups.size(), M4::identity());
    cur.group_root.back() = root;
    return (int)groups.size() - 1;
}

int Edits::group_by_name(const std::string& name) const {
    for (size_t i = 0; i < groups.size(); i++)
        if (groups[i].name == name) return (int)i;
    return -1;
}

int Edits::building_key(const std::string& key) {
    for (size_t i = 0; i < building_keys.size(); i++)
        if (building_keys[i] == key) return (int)i;
    building_keys.push_back(key);
    return (int)building_keys.size() - 1;
}

int Edits::n_moved() const {
    int n = 0;
    for (size_t i = 0; i < cur.matrix.size(); i++) n += !cur.removed[i] && moved((int)i);
    return n;
}

int Edits::n_removed() const {
    int n = 0;
    for (auto r : cur.removed) n += r != 0;
    return n;
}

int Edits::n_copies() const {
    int n = 0;
    for (auto& c : cur.copies) n += !c.removed && c.asset < 0;
    return n;
}

int Edits::n_nav() const {                       // navmesh areas + crowd route edits
    int n = (int)cur.routes.size();
    for (auto& r : cur.regions) n += !r.removed && r.kind != AREA_GROUND;
    return n;
}

int Edits::n_regions() const {
    int n = 0;
    for (auto& r : cur.regions) n += !r.removed && std::fabs(r.dz) >= 0.01f;
    return n;
}

int Edits::n_placed() const {
    int n = 0;
    for (auto& c : cur.copies) n += !c.removed && c.asset >= 0;
    return n;
}

static json mat_json(const M4& m) {
    json a = json::array();
    for (float f : m.m) a.push_back(f);
    return a;
}

json Edits::request(bool carry_ground) const {
    json moves = json::array(), removes = json::array(), copies = json::array(), places = json::array();
    // a spawn point is changed in the mode block it is shown in (engine spawnview.py: the request's "spawns")
    json sp_move = json::array(), sp_remove = json::array(), sp_add = json::array();
    for (size_t i = 0; i < cur.matrix.size(); i++) {
        const Entity& e = scene->entities[i];
        if (e.flags & F_SPAWN) {
            const std::string& rec = scene->records[e.record].name;
            if (cur.removed[i]) sp_remove.push_back({{"uid", uid_hex(e.uid)}, {"record", rec}, {"name", e.name}});
            else if (moved((int)i))
                sp_move.push_back({{"uid", uid_hex(e.uid)}, {"record", rec}, {"matrix", mat_json(cur.matrix[i])}, {"name", e.name}});
            continue;
        }
        if (cur.removed[i]) {
            removes.push_back(uid_hex(e.uid));
            continue;
        }
        if (!moved((int)i)) continue;
        json mv = {{"uid", uid_hex(e.uid)}, {"matrix", mat_json(cur.matrix[i])}, {"name", e.name}};
        mv["building"] = cur.building[i] >= 0 ? json(building_keys[cur.building[i]]) : json(nullptr);
        moves.push_back(mv);
    }
    for (auto& c : cur.copies) {
        if (c.removed) continue;
        if (c.asset >= 0) {
            const AssetRef& a = assets[c.asset];
            places.push_back({{"key", a.key}, {"matrix", mat_json(c.matrix)}, {"name", a.name}, {"map", a.map},
                              {"solid", c.solid}, {"climb", climb_name(c.climb >= 0 ? c.climb : default_climb(a.key))}});
            if (c.group >= 0) { places.back()["group"] = groups[c.group].name; places.back()["path"] = c.path; }
        } else if (scene->entities[c.src].flags & F_SPAWN) {
            const Entity& e = scene->entities[c.src];
            sp_add.push_back({{"template", uid_hex(e.uid)}, {"records", json::array({scene->records[e.record].name})},
                              {"matrix", mat_json(c.matrix)}, {"name", e.name}});
        } else {
            copies.push_back({{"uid", uid_hex(scene->entities[c.src].uid)}, {"matrix", mat_json(c.matrix)},
                              {"name", scene->entities[c.src].name}});
            if (c.climb == CLIMB_GENERATED || c.climb == CLIMB_OUTSIDE) copies.back()["climb"] = climb_name(c.climb);
            if (c.group >= 0) { copies.back()["group"] = groups[c.group].name; copies.back()["path"] = c.path; }
        }
    }
    json grp = json::array();                     // placed user assets (the engine ignores them: it builds the parts)
    for (size_t g = 0; g < groups.size(); g++)
        grp.push_back({{"name", groups[g].name}, {"asset", groups[g].asset}, {"root", mat_json(root((int)g))},
                       {"part_names", groups[g].part_names}});
    json regions = json::array();                 // ground raised / lowered (plan.py: the outline's convex hull)
    json nav_areas = json::array(), routes = json::array();
    for (auto& r : cur.regions) {
        if (r.removed || r.pts.size() < 3) continue;
        json poly = json::array(), zs = json::array();
        for (auto& p : r.pts) { poly.push_back({p.x, p.y}); zs.push_back(p.z); }
        if (r.kind == AREA_GROUND) {
            if (std::fabs(r.dz) >= 0.01f) regions.push_back({{"name", r.name}, {"poly", poly}, {"dz", r.dz}, {"z", zs}});
        } else {
            nav_areas.push_back({{"name", r.name}, {"poly", poly}, {"z", zs},
                                 {"mode", r.kind == AREA_NAV_BLOCK ? "block" : "remake"}, {"new_routes", r.new_routes}});
        }
    }
    for (auto& [uid, e] : cur.routes) {
        json o = {{"uid", uid}};
        if (e.active >= 0) o["active"] = e.active == 1;
        if (e.width >= 0) o["width"] = e.width;
        if (o.size() > 1) routes.push_back(o);
    }
    json climbable = json::array(), outside = json::array();   // map objects made climbable / buildings outside
    for (size_t i = 0; i < cur.climbable.size(); i++)
        if (cur.climbable[i] && !cur.removed[i]) (cur.climbable[i] == 2 ? outside : climbable).push_back(uid_hex(scene->entities[i].uid));
    json sculpts = json::array();                // brush strokes on the ground
    for (auto& st : cur.sculpts) sculpts.push_back({{"x", st.x}, {"y", st.y}, {"z", st.z}, {"r", st.r}, {"h", st.h}});
    json req = {{"archive", scene->archive}, {"moves", moves}, {"removes", removes}, {"copies", copies}, {"sculpts", sculpts},
                {"places", places}, {"groups", grp}, {"regions", regions}, {"nav_areas", nav_areas}, {"routes", routes},
                {"climbable", climbable},
                {"climbable_outside", outside}, {"carry_ground", carry_ground}};
    if (!sp_move.empty() || !sp_remove.empty() || !sp_add.empty())
        req["spawns"] = {{"move", sp_move}, {"remove", sp_remove}, {"add", sp_add}};
    // the Map panel's settings and boundary edits (only what was changed)
    for (const char* k : {"settings", "boundaries"}) {
        auto it = cur.map.find(k);
        if (it != cur.map.end() && !it->is_null() && !it->empty())
            req[std::string(k) == "settings" ? "map_settings" : k] = *it;
    }
    return req;
}

static bool read_mat(const json& a, M4& m) {
    if (!a.is_array() || a.size() != 16) return false;
    for (int i = 0; i < 16; i++) m.m[i] = a[i].get<float>();
    return true;
}

std::string Edits::apply_project(const json& j) {
    std::string problems;
    auto find = [&](const json& h) -> int {
        auto it = by_uid.find(uid_from_hex(h.get<std::string>()));
        if (it == by_uid.end()) { problems += "no object " + h.get<std::string>() + " on this map; "; return -1; }
        return it->second;
    };
    for (auto& g : j.value("groups", json::array())) {
        M4 r = M4::identity();
        if (g.contains("root")) read_mat(g["root"], r);
        int k = add_group(g.value("name", std::string("asset")), g.value("asset", std::string()), r);
        const json pn = g.value("part_names", json::object());     // (not .items() of a temporary: it dangles)
        for (auto& [path, name] : pn.items()) groups[k].part_names[path] = name.get<std::string>();
    }
    auto group_of = [&](const json& o) {
        if (!o.contains("group")) return -1;
        int g = group_by_name(o["group"].get<std::string>());
        return g >= 0 ? g : add_group(o["group"].get<std::string>(), "", M4::identity());
    };
    for (auto& mv : j.value("moves", json::array())) {
        int i = find(mv["uid"]);
        M4 m;
        if (i < 0 || !read_mat(mv["matrix"], m)) continue;
        cur.matrix[i] = m;
        if (mv.contains("building") && mv["building"].is_string()) cur.building[i] = building_key(mv["building"]);
    }
    for (auto& h : j.value("climbable", json::array())) {
        int i = find(h);
        if (i >= 0) cur.climbable[i] = 1;
    }
    for (auto& h : j.value("climbable_outside", json::array())) {
        int i = find(h);
        if (i >= 0) cur.climbable[i] = 2;
    }
    for (auto& h : j.value("removes", json::array())) {
        int i = find(h);
        if (i >= 0) cur.removed[i] = 1;
    }
    for (auto& c : j.value("copies", json::array())) {
        int i = find(c["uid"]);
        M4 m;
        if (i >= 0 && read_mat(c["matrix"], m)) {
            cur.copies.push_back({i, m, false});
            cur.copies.back().group = group_of(c);
            cur.copies.back().path = c.value("path", std::string());
            int cl = climb_from(c.value("climb", std::string()));
            if (cl == CLIMB_GENERATED || cl == CLIMB_OUTSIDE) cur.copies.back().climb = cl;
        }
    }
    for (auto& p : j.value("places", json::array())) {
        M4 m;
        if (!p.contains("key") || !read_mat(p["matrix"], m)) { problems += "a library model without its key / place; "; continue; }
        int a = asset_index(p["key"].get<std::string>(), p.value("name", std::string("model")), p.value("map", std::string()));
        CopyObj o{-1, m, false, a};
        o.solid = p.value("solid", true);
        o.group = group_of(p);
        o.path = p.value("path", std::string());
        int cl = climb_from(p.value("climb", std::string()));
        if (cl >= 0 && cl != default_climb(p["key"].get<std::string>())) o.climb = cl;
        cur.copies.push_back(o);
    }
    for (auto& r : j.value("regions", json::array())) {
        RegionEdit g;
        g.name = r.value("name", std::string("ground"));
        g.dz = r.value("dz", 0.0f);
        auto zs = r.value("z", json::array());
        auto poly = r.value("poly", json::array());
        for (size_t k = 0; k < poly.size(); k++)
            g.pts.push_back(V3(poly[k][0].get<float>(), poly[k][1].get<float>(), k < zs.size() ? zs[k].get<float>() : 0.0f));
        if (g.pts.size() >= 3) cur.regions.push_back(g);
        else problems += "a ground region with under 3 corners; ";
    }
    for (auto& r : j.value("nav_areas", json::array())) {
        RegionEdit g;
        g.name = r.value("name", std::string("navmesh"));
        g.kind = r.value("mode", std::string()) == "block" ? AREA_NAV_BLOCK : AREA_NAV_REMAKE;
        g.new_routes = r.value("new_routes", false);
        auto zs = r.value("z", json::array());
        auto poly = r.value("poly", json::array());
        for (size_t k = 0; k < poly.size(); k++)
            g.pts.push_back(V3(poly[k][0].get<float>(), poly[k][1].get<float>(), k < zs.size() ? zs[k].get<float>() : 0.0f));
        if (g.pts.size() >= 3) cur.regions.push_back(g);
        else problems += "a navmesh area with under 3 corners; ";
    }
    for (auto& st : j.value("sculpts", json::array())) {
        SculptStroke s;
        s.x = st.value("x", 0.0f); s.y = st.value("y", 0.0f); s.z = st.value("z", 0.0f);
        s.r = st.value("r", 6.0f); s.h = st.value("h", 1.0f);
        cur.sculpts.push_back(s);
    }
    for (auto& r : j.value("routes", json::array())) {
        RouteEdit e;
        if (r.contains("active")) e.active = r["active"].get<bool>() ? 1 : 0;
        if (r.contains("width")) e.width = r["width"].get<float>();
        cur.routes[r.value("uid", std::string())] = e;
    }
    if (j.contains("map_settings") && j["map_settings"].is_object()) cur.map["settings"] = j["map_settings"];
    if (j.contains("boundaries") && j["boundaries"].is_object()) cur.map["boundaries"] = j["boundaries"];
    // spawn edits back onto the mode's spawn markers (uid + record)
    if (j.contains("spawns") && j["spawns"].is_object()) {
        auto marker = [&](const std::string& uid, const std::string& rec) -> int {
            uint32_t u = uid_from_hex(uid);
            for (size_t i = 0; i < scene->entities.size(); i++) {
                const Entity& e = scene->entities[i];
                if ((e.flags & F_SPAWN) && e.uid == u && scene->records[e.record].name == rec) return (int)i;
            }
            problems += "no spawn point " + uid + " in " + rec + "; ";
            return -1;
        };
        const json& sp = j["spawns"];
        for (auto& m : sp.value("move", json::array())) {
            int i = marker(m.value("uid", std::string()), m.value("record", std::string()));
            M4 mm;
            if (i >= 0 && read_mat(m["matrix"], mm)) cur.matrix[i] = mm;
        }
        for (auto& m : sp.value("remove", json::array())) {
            int i = marker(m.value("uid", std::string()), m.value("record", std::string()));
            if (i >= 0) cur.removed[i] = 1;
        }
        for (auto& m : sp.value("add", json::array())) {
            auto recs = m.value("records", json::array());
            int i = recs.empty() ? -1 : marker(m.value("template", std::string()), recs[0].get<std::string>());
            M4 mm;
            if (i >= 0 && read_mat(m["matrix"], mm)) cur.copies.push_back({i, mm, false});
        }
    }
    commit();
    saved_at = at;
    return problems;
}
