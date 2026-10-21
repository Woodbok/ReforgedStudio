// What the user changed: each map object's current placement, removals, copies, models placed from the asset
// library, Select Building tags; undo / redo as whole snapshots (a map's edit state is a few hundred KB); the project
// file, which is the engine's build request.
#pragma once
#include <map>
#include <string>
#include <unordered_map>
#include <vector>
#include "json.hpp"
#include "scene.h"

using json = nlohmann::json;

struct CopyObj {         // a new object: a copy of a map entity, or a model placed from the asset library
    int src;            // the entity it copies (-1: a library model)
    M4 matrix;
    bool removed = false;
    int asset = -1;     // index into Edits::assets (a library model or a primitive), -1: a copy
    bool solid = true;  // (library models) with collision
    int group = -1;     // index into Edits::groups: the placed user asset it is a part of (-1: none)
    std::string path;   // its part path in that asset: "p3", "p30/p0" (a part of a child asset); "+2": added to
                        // this placement only
    int climb = -1;     // CLIMB_NATIVE / _GENERATED / _NONE / _OUTSIDE; -1: its kind's default (default_climb)
};

// Climbing of a placed model: as in its own map (its own climb data; a copy keeps the object's), generated ledges +
// handhold rows, none, or climbable outside (as in its own map + handhold rows and ledges on its building's outer
// walls only, on an invisible layer - its own collision untouched)
enum { CLIMB_NATIVE = 0, CLIMB_GENERATED = 1, CLIMB_NONE = 2, CLIMB_OUTSIDE = 3 };
inline const char* climb_name(int c) {
    return c == CLIMB_GENERATED ? "generated" : c == CLIMB_NONE ? "none" : c == CLIMB_OUTSIDE ? "outside" : "native";
}
inline int climb_from(const std::string& s) {
    return s == "generated" ? CLIMB_GENERATED : s == "none" ? CLIMB_NONE : s == "outside" ? CLIMB_OUTSIDE
         : s == "native" ? CLIMB_NATIVE : -1;
}
inline int default_climb(const std::string& key) {      // models: their own map's; primitives: generated; ground: none
    if (key.find("DataPC_DX11_") != std::string::npos && key.rfind("prim:", 0) != 0 && key.rfind("ground:", 0) != 0)
        return CLIMB_GENERATED;                             // (SP models: SP climbs sill by sill, MP by handhold rows)
    return key.rfind("prim:", 0) == 0 ? CLIMB_GENERATED : key.rfind("ground:", 0) == 0 ? CLIMB_NONE : CLIMB_NATIVE;
}

struct GroupInfo {      // one placed user asset (its parts are copies / library models with its index)
    std::string name;   // "Barracks (turned, closed) #1"
    std::string asset;  // the asset's id (its file name in the user asset folder, without .json)
    std::map<std::string, std::string> part_names;   // part path -> name ("p30" -> "AC3MP_FORT_BuildingWindow_01A")
};

struct AssetRef {       // a model of the asset library (studio/engine/library.py)
    std::string key;    // "<archive>|<mesh uid hex>" - what the engine knows it by
    std::string name, map;
};

// An area drawn on the ground (its outline = the corners' convex hull): ground raised / lowered (engine plan.py
// "regions": dz, everything standing wholly inside goes with it), or a navmesh area ("nav_areas": the crowds'
// navmesh made again there from the collision, or crowds kept out of it)
enum { AREA_GROUND = 0, AREA_NAV_REMAKE = 1, AREA_NAV_BLOCK = 2, AREA_CLEAR = 10 };   // (AREA_CLEAR: never stored)
struct RegionEdit {
    std::string name;
    std::vector<V3> pts; // the corners clicked (x, y; z = the ground there, for drawing)
    float dz = 0;        // (ground)
    bool removed = false;
    int kind = AREA_GROUND;
    bool new_routes = false;   // (navmesh remake) new crowd routes along its open streets (navflows)
};

struct RouteEdit {       // a crowd route (CrowdFlow) changed ("routes")
    int active = -1;     // -1 as it is, 0 off, 1 on
    float width = -1;    // < 0: as it is
    bool operator==(const RouteEdit& o) const { return active == o.active && width == o.width; }
};

struct SculptStroke {     // a brush stroke on the ground (engine sculpt.py): the change at a point is the sum of
    float x = 0, y = 0;   // every stroke's h * a smooth bump falling to 0 at r from its middle
    float z = 0;          // (the ground there when it was made: for drawing)
    float r = 6, h = 1;
    bool operator==(const SculptStroke& o) const { return x == o.x && y == o.y && z == o.z && r == o.r && h == o.h; }
};

struct EditState {
    std::vector<M4> matrix;          // per entity
    std::vector<uint8_t> removed;    // per entity
    std::vector<int> building;       // per entity: index into Edits::building_keys, -1 none
    std::vector<uint8_t> climbable;  // per entity: made climbable - 1: generated ledges + handholds added; 2: its
                                     // building climbable outside (rows + ledges on the outer walls only)
    std::vector<CopyObj> copies;
    std::vector<M4> group_root;      // per placed user asset: its pivot's placement (moves with the whole asset)
    std::vector<RegionEdit> regions; // ground raised / lowered, navmesh areas
    std::map<std::string, RouteEdit> routes;   // crowd route uid (hex) -> its change
    json map = json::object();       // (mapedit.inc): "settings" (the request's map_settings: lighting,
                                     // kill height, view distance), "spawns" / "boundaries" (the request's keys)
    std::vector<SculptStroke> sculpts;         // the ground sculpted
};

// object ids: [0, entities) the map's entities, [entities, entities + copies) the copies and library models
struct Edits {
    const Scene* scene = nullptr;
    EditState cur;
    std::vector<EditState> history;
    int at = -1;                                  // history index of the last committed state
    int saved_at = -1;                            // history index saved to the project file
    std::vector<std::string> building_keys;
    std::unordered_map<uint32_t, int> by_uid;     // uid -> entity index (movable objects: not spawn markers)
    std::vector<AssetRef> assets;                 // library models used this session (append-only: not undone)
    int asset_index(const std::string& key, const std::string& name, const std::string& map);
    std::vector<GroupInfo> groups;                // placed user assets (append-only; cleared with the map)
    int add_group(const std::string& name, const std::string& asset, const M4& root);
    int group_by_name(const std::string& name) const;
    M4 root(int g) const { return g >= 0 && g < (int)cur.group_root.size() ? cur.group_root[g] : M4::identity(); }

    void reset(const Scene* s);
    void commit();                                // the current state becomes an undo step
    bool can_undo() const { return at > 0 || dirty_uncommitted(); }
    bool can_redo() const { return at + 1 < (int)history.size(); }
    void undo();
    void redo();
    bool dirty() const { return at != saved_at || dirty_uncommitted(); }
    bool dirty_uncommitted() const;

    int count() const { return scene ? (int)scene->entities.size() + (int)cur.copies.size() : 0; }
    bool is_copy(int id) const { return scene && id >= (int)scene->entities.size(); }
    int entity_of(int id) const { return is_copy(id) ? cur.copies[id - scene->entities.size()].src : id; }  // -1: a library model
    bool is_placed(int id) const { return is_copy(id) && cur.copies[id - scene->entities.size()].asset >= 0; }
    int asset_of(int id) const { return is_copy(id) ? cur.copies[id - scene->entities.size()].asset : -1; }
    M4& matrix(int id) { return is_copy(id) ? cur.copies[id - scene->entities.size()].matrix : cur.matrix[id]; }
    bool removed(int id) const { return is_copy(id) ? cur.copies[id - scene->entities.size()].removed : cur.removed[id] != 0; }
    void set_removed(int id, bool r);
    bool moved(int id) const;
    bool movable(int id) const;                   // spawn points and undecodable entities can't be moved here yet
    int building_key(const std::string& key);

    int n_moved() const, n_removed() const, n_copies() const, n_placed() const, n_regions() const, n_nav() const;
    json request(bool carry_ground) const;        // the engine's build request (also the project file's body)
    std::string apply_project(const json& j);     // -> problems ("" = all applied)
};
