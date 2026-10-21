// The Map panel's data (the map's lighting, kill height and view distance - engine mapsettings.py) and the
// map's gameplay editing (spawn points per mode, out-of-bounds boundaries, gameplay objects - engine
// spawnview.py / gameplayview.py). The code is App's own (mapedit.inc, included at the end of main.cpp); the edits
// themselves live in EditState::map (undo / redo, the project file = the build request's "map_settings",
// "spawns", "boundaries").
#pragma once
#include <string>
#include <unordered_map>
#include <vector>
#include "json.hpp"
#include "math3d.h"

using json = nlohmann::json;

struct MsControl {                       // one curated lighting control (mapsettings.CONTROLS)
    std::string id, group, label, op, fmt, tip;
    float lo = 0, hi = 1, neutral = 1;
    int missing = 0;                     // its curves this map's table hasn't
    json now;                            // its first curve's value at Night 0 / Day 12 / Evening 20
};

struct MsCurve {                         // a curve for the read-only view
    std::string name, path, kind;
    json hours, vals, at, after, at_after;
};

struct MsView {                          // the engine's mapsettings.read (+ preview)
    bool loaded = false, loading = false;
    std::string archive, tod, error;
    int n_curves = 0;
    std::vector<std::string> others;
    std::vector<MsControl> controls;
    json presets = json::array();
    std::vector<MsCurve> curves;
    float water = 0, death = -500;
    int view = 63, load_dist = 95;
    int stand_ins = 0, stand_ins_on = 0;   // the World's far-view tiles (how many still draw)
    // the preview of the current settings
    json preview_of;                     // the settings it was asked for
    bool preview_busy = false;
    int changed = 0;
    std::vector<std::string> untouched, errors, warnings;
    std::unordered_map<std::string, int> curve_at;   // path -> curves index
};

// the map's out-of-bounds areas and gameplay zones (engine spawnview.boundary_data)
struct MsArea {
    std::string uid, name, kind;         // kind: "oob" (editable corners) or "zone" (drawn only)
    std::vector<std::string> records, modes;
    bool closed = true;
    std::vector<V3> corners;             // world x, y; z = the area's own height
    std::vector<std::vector<V3>> polygons;
    float z = 0;
};

// a gameplay object of the map (engine gameplayview.data): its head entity, members and FX companions are
// selected together and move as one
struct MsThing {
    uint32_t uid = 0;
    std::string name, kind, label, map;
    std::vector<uint32_t> members, companions;
    bool move_only = false;              // artifacts, scoring zones, Domination points
};

struct MsTemplate {                      // a stock map's hiding spot / bench / chase breaker / lift (Library > Gameplay)
    std::string key, name, model, kind, label, map;
    float size[3] = {0, 0, 0};
    int members = 0;
    M4 matrix = M4::identity();
};

struct MsGame {
    bool areas_loaded = false, areas_loading = false, things_loaded = false, things_loading = false;
    std::vector<MsArea> areas;
    std::vector<MsThing> things;
    std::unordered_map<uint32_t, int> thing_of;      // head / member / companion uid -> things index
    std::vector<MsTemplate> templates;
    // the engine's check of the spawn and boundary edits (map_check)
    json check_of;
    bool check_busy = false;
    std::vector<std::string> errors, warnings;
    json counts = json::object();                     // mode record -> {kind label: n} after the edits
};
