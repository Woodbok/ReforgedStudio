// Library categories: every model of the asset library (and every user asset) is filed by its name (houses, walls,
// stairs, crates, barrels, trees ...), with the user's own filing on top: overrides and custom categories, kept in the
// user asset folder's categories.json ({"models": {model name: category}, "assets": {asset id: category}, "custom":
// [names]}).
#pragma once
#include <algorithm>
#include <cctype>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>
#include "json.hpp"

namespace cats {

// the automatic categories, first match wins (more specific first: "BuildingWindow" is a window, "StoneWall" a wall)
struct Rule { const char* cat; std::vector<const char*> words; };
inline const std::vector<Rule>& rules() {
    static const std::vector<Rule> r = {
        {"Far scenery", {"fakemesh", "fakecity", "fake_", "matte", "sphere", "backdrop", "skyline", "mountain"}},
        {"Ground & paths", {"ground", "terrain", "road", "path", "mudtrail", "puddle", "decal", "dirt_", "sand_", "floor_",
                            "pavement", "grd_"}},
        {"Water", {"water", "wet_", "dyepit", "pond"}},
        {"Stairs & ladders", {"stair", "step", "ladder", "ramp"}},
        {"Doors & windows", {"door", "window", "shutter", "_cb_", "chasebreaker", "gate"}},
        {"Roofs", {"roof", "tile"}},
        {"Walls & fences", {"wall", "fence", "rail", "palisade", "barrier", "bannister"}},
        {"Houses & buildings", {"house", "building", "barrack", "messhall", "meshall", "church", "tower", "facade", "shack",
                                "hut_", "pallace", "palace", "warehouse", "tradingpost", "store", "temple", "cathedral",
                                "brewer", "tavern", "castle", "bridge", "arch", "chapel", "cabin", "foundry", "belfry", "theater", "pergola", "modul",
                                "ceiling", "chimney", "terrace", "loggia", "rempart", "remp_", "abbey", "mosque", "cloister",
                                "basilica", "balcony", "hall_", "aile"}},
        {"Market & stalls", {"stall", "merchant", "market", "booth", "stand", "shop", "awning", "tent"}},
        {"Lifts & flags", {"elevator", "lift_", "flag"}},
        {"Climbing aids", {"starter", "climbwood"}},
        {"NPC props", {"cwl_", "cookerspoon", "fishingrod", "chopper", "_wc0", "_axe_", "lute"}},
        {"Hiding spots", {"haycart", "haystack", "hay_", "bale", "furstack", "cotton"}},
        {"Barrels", {"barrel", "keg", "cask"}},
        {"Crates & boxes", {"crate", "box", "case", "sack", "sac_", "bag", "chest"}},
        {"Carts & wagons", {"cart", "wagon", "carriage", "wheel", "chariot"}},
        {"Boats & docks", {"boat", "ship", "dock", "pier", "mast", "canoe", "anchor", "harbo", "wreck", "fishtrap", "fishnet", "crane"}},
        {"Fountains & statues", {"fountain", "well_", "puit", "statue", "monument"}},
        {"Ropes & chains", {"rope", "chain", "clothesline", "trussrod", "cable"}},
        {"Furniture", {"table", "chair", "bench", "bed_", "shelf", "desk", "stool", "cupboard", "closet"}},
        {"Lights & fire", {"lamp", "lantern", "torch", "candle", "fire", "brazier", "chandelier"}},
        {"Trees & plants", {"tree", "palm", "bush", "fern", "plant", "veget", "veg_", "leaves", "leaf", "vine", "grass",
                            "flower", "hedge", "banana", "ivy", "foliage", "trunk", "log_", "garden"}},
        {"Rocks & stones", {"rock", "stonepile", "boulder", "cliff", "pebble", "stone_"}},
        {"Beams & poles", {"beam", "pole", "plank", "post", "pillar", "column", "scaffold", "timber"}},
        {"Small clutter", {"birdshit", "pigeon", "seagull", "brokenitems", "straw", "brickpile", "woodstack", "basket",
                           "bottle", "tools", "cloth", "carpet", "painting", "pot_", "jar", "vase", "broken"}},
    };
    return r;
}

inline std::string lower(std::string s) {
    for (auto& c : s) c = (char)std::tolower((unsigned char)c);
    return s;
}

inline std::string automatic(const std::string& name, bool ground = false) {
    if (ground) return "Ground & paths";
    std::string n = lower(name);
    for (auto& r : rules())
        for (const char* w : r.words)
            if (n.find(w) != std::string::npos) return r.cat;
    return "Props";
}

// the user's own filing (categories.json in the user asset folder)
struct Store {
    std::string path;
    std::map<std::string, std::string> models, assets;
    std::set<std::string> custom;
    bool loaded = false;

    void load(const std::string& p) {
        path = p;
        loaded = true;
        models.clear(); assets.clear(); custom.clear();
        std::ifstream f(p, std::ios::binary);
        if (!f) return;
        std::stringstream ss;
        ss << f.rdbuf();
        nlohmann::json j = nlohmann::json::parse(ss.str(), nullptr, false);
        if (j.is_discarded()) return;
        const nlohmann::json m = j.value("models", nlohmann::json::object()), a = j.value("assets", nlohmann::json::object());
        for (auto& [k, v] : m.items()) models[k] = v.get<std::string>();
        for (auto& [k, v] : a.items()) assets[k] = v.get<std::string>();
        for (auto& c : j.value("custom", nlohmann::json::array())) custom.insert(c.get<std::string>());
    }
    bool save() const {
        nlohmann::json j = {{"models", models}, {"assets", assets}, {"custom", custom}};
        std::ofstream f(path, std::ios::binary);
        if (!f) return false;
        f << j.dump(1);
        return true;
    }
    std::string model(const std::string& name, bool ground) const {
        auto it = models.find(name);
        return it != models.end() ? it->second : automatic(name, ground);
    }
    std::string asset(const std::string& id, const std::string& name) const {
        auto it = assets.find(id);
        return it != assets.end() ? it->second : automatic(name);
    }
    // every category name: the automatic ones, Props, then the user's own
    std::vector<std::string> all() const {
        std::vector<std::string> out;
        for (auto& r : rules()) out.push_back(r.cat);
        out.push_back("Props");
        for (auto& c : custom)
            if (std::find(out.begin(), out.end(), c) == out.end()) out.push_back(c);
        for (auto& [k, v] : models)
            if (std::find(out.begin(), out.end(), v) == out.end()) out.push_back(v);
        for (auto& [k, v] : assets)
            if (std::find(out.begin(), out.end(), v) == out.end()) out.push_back(v);
        return out;
    }
};

}  // namespace cats
