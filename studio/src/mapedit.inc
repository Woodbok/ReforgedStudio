// Reforged Studio's Map panel and map-wide gameplay editing - App's own code, included once at the end of main.cpp
// (mapedit.h has the data). Settings: the map's lighting (its time-of-day table), kill height and view distance (engine
// mapsettings.py). The edits are EditState::map["settings"] = the build request's "map_settings". Gameplay: spawn points per
// mode (the markers themselves: Edits::request -> "spawns"), out-of-bounds corners (EditState::map["boundaries"]),
// gameplay objects picked whole and placed from Library > Gameplay (engine spawnview.py, gameplayview.py).

// ------------------------------------------------------------------------------------------------ settings --

// the settings as the edit has them (an empty object when none), and one key set / dropped - no empty "settings"
// is ever left in the edit state (undo / dirty compare it)
json App::ms_settings() const {
    auto it = edits.cur.map.find("settings");
    return it != edits.cur.map.end() && it->is_object() ? *it : json::object();
}

void App::ms_put(const std::string& key, const json& v) {
    json s = ms_settings();
    if (v.is_null()) s.erase(key); else s[key] = v;
    if (s.contains("tod") && s["tod"].empty()) s.erase("tod");
    if (s.empty()) edits.cur.map.erase("settings"); else edits.cur.map["settings"] = s;
}

// a lighting control's value in the edit (its neutral when not set), and whether it's applied to every key
float App::ms_value(const MsControl& c, bool* all) const {
    auto it = edits.cur.map.find("settings");
    if (all) *all = false;
    if (it == edits.cur.map.end() || !it->contains("tod") || !(*it)["tod"].contains(c.id)) return c.neutral;
    const json& v = (*it)["tod"][c.id];
    if (all) *all = v.value("all", false);
    return v.value("v", c.neutral);
}

void App::ms_set(const MsControl& c, float v, bool all) {
    json t = ms_settings().value("tod", json::object());
    if (std::fabs(v - c.neutral) < 1e-6f) t.erase(c.id);
    else t[c.id] = {{"v", v}, {"all", all}};
    ms_put("tod", t.empty() ? json() : t);
}

void App::load_mapsettings() {
    if (!scene || workspace || msv.loaded || msv.loading) return;
    msv.loading = true;
    std::string archive = scene->archive;
    engine.request("mapsettings", {{"archive", archive}}, [this, archive](bool ok, const json& r, const std::string& e) {
        msv.loading = false;
        if (!scene || scene->archive != archive) return;
        if (!ok) { msv.error = e; status = "Map settings: " + e; return; }
        MsView v;
        v.archive = archive;
        v.tod = r.value("tod", std::string());
        v.n_curves = r.value("n_curves", 0);
        for (auto& o : r.value("others", json::array())) v.others.push_back(o.get<std::string>());
        for (auto& c : r["controls"]) {
            MsControl k;
            k.id = c.value("id", std::string());
            k.group = c.value("group", std::string());
            k.label = c.value("label", std::string());
            k.op = c.value("op", std::string());
            k.fmt = c.value("fmt", std::string("%.2f"));
            k.tip = c.value("tip", std::string());
            k.lo = c.value("lo", 0.0f);
            k.hi = c.value("hi", 1.0f);
            k.neutral = c.value("neutral", 1.0f);
            k.missing = c.value("missing", 0);
            k.now = c.value("now", json::array());
            v.controls.push_back(k);
        }
        v.presets = r.value("presets", json::array());
        for (auto& c : r["curves"]) {
            MsCurve k;
            k.name = c.value("name", std::string());
            k.path = c.value("path", std::string());
            k.kind = c.value("kind", std::string());
            k.hours = c.value("hours", json::array());
            k.vals = c.value("vals", json::array());
            k.at = c.value("at", json::array());
            v.curve_at[k.path] = (int)v.curves.size();
            v.curves.push_back(k);
        }
        const json& w = r["world"];
        v.water = w.value("water_level", 0.0f);
        v.death = w.is_object() && w["death_height"].is_number() ? w["death_height"].get<float>() : -500.0f;
        v.view = w.value("view_distance", 63);
        v.load_dist = w.value("loading_distance", 95);
        v.stand_ins = w.value("stand_ins", 0);
        v.stand_ins_on = w.value("stand_ins_on", 0);
        v.loaded = true;
        v.preview_of = json();            // (asked for again: the edits may already hold settings - a project)
        msv = std::move(v);
        add_log("map settings: " + msv.tod + " (" + std::to_string(msv.n_curves) + " curves), kill height " +
                std::to_string(msv.water).substr(0, 6) + " m, view distance " + std::to_string(msv.view) + " m");
    });
}

// the preview of the current settings: the curves after, what would warn - asked for whenever they change
void App::ms_preview_tick() {
    if (!msv.loaded || msv.preview_busy || engine.busy() || ImGui::IsAnyItemActive()) return;
    json s = ms_settings();
    if (s == msv.preview_of) return;
    msv.preview_busy = true;
    std::string archive = msv.archive;
    engine.request("mapsettings_preview", {{"archive", archive}, {"settings", s}},
                   [this, archive, s](bool ok, const json& r, const std::string& e) {
        msv.preview_busy = false;
        if (!scene || msv.archive != archive) return;
        msv.preview_of = s;
        msv.errors.clear(); msv.warnings.clear(); msv.untouched.clear();
        for (auto& c : msv.curves) { c.after = json(); c.at_after = json(); }
        if (!ok) { msv.errors.push_back(e); return; }
        msv.changed = r.value("changed", 0);
        for (auto& x : r.value("errors", json::array())) msv.errors.push_back(x.get<std::string>());
        for (auto& x : r.value("problems", json::array())) msv.errors.push_back(x.get<std::string>());
        for (auto& x : r.value("warnings", json::array())) msv.warnings.push_back(x.get<std::string>());
        for (auto& x : r.value("untouched", json::array())) msv.untouched.push_back(x.get<std::string>());
        for (auto& c : r.value("curves", json::array())) {
            auto it = msv.curve_at.find(c.value("path", std::string()));
            if (it == msv.curve_at.end() || !c.contains("after")) continue;
            msv.curves[it->second].after = c["after"];
            msv.curves[it->second].at_after = c["at_after"];
        }
    });
}

void App::ms_apply_preset(const json& p) {
    json t = p.value("tod", json::object());
    ms_put("tod", t.empty() ? json() : t);
    edits.commit();
    status = "Lighting: " + p.value("label", std::string("preset")) + " - Build to see it in game (the Studio can't show AC3's lighting)";
}

int App::ms_n_changes() const {
    auto it = edits.cur.map.find("settings");
    if (it == edits.cur.map.end()) return 0;
    return (int)(it->contains("tod") ? (*it)["tod"].size() : 0) + (int)it->contains("water_level") +
           (int)it->contains("view_distance") + (int)it->contains("stand_ins");
}

static std::string ms_vec(const json& v) {
    if (!v.is_array() || v.empty()) return "-";
    char b[96];
    if (v.size() == 1) snprintf(b, sizeof b, "%.3g", v[0].get<double>());
    else if (v.size() >= 3) snprintf(b, sizeof b, "%.2f %.2f %.2f", v[0].get<double>(), v[1].get<double>(), v[2].get<double>());
    else snprintf(b, sizeof b, "%.3g %.3g", v[0].get<double>(), v[1].get<double>());
    return b;
}

void App::ui_map_panel() {
    // docked with the Inspector the first time (an older layout file doesn't know this window)
    if (ImGuiWindow* w = ImGui::FindWindowByName("Inspector"))
        if (w->DockId) ImGui::SetNextWindowDockID(w->DockId, ImGuiCond_FirstUseEver);
    ImGui::Begin("Map");
    if (!scene || workspace) {
        ImGui::TextDisabled(workspace ? "The asset editor is open (no map)." : "No map open.");
        ImGui::End();
        return;
    }
    load_mapsettings();
    ms_preview_tick();
    ImGui::TextColored(ImVec4(0.95f, 0.62f, 0.3f, 1), "%s", scene->title.c_str());
    if (!msv.loaded) {
        ImGui::TextDisabled(msv.loading ? "Reading the map's settings..." : msv.error.empty() ? "" : msv.error.c_str());
        ImGui::End();
        return;
    }
    if (ms_fold_lighting) { ImGui::SetNextItemOpen(false); ms_fold_lighting = false; }
    if (ImGui::CollapsingHeader("Lighting (time of day)", ImGuiTreeNodeFlags_DefaultOpen)) {
        ImGui::PushTextWrapPos(0);
        ImGui::TextColored(ImVec4(1, 0.75f, 0.35f, 1), "The Studio draws plain textures: it can't show AC3's sun, shade, fog or "
                           "colour grade. Build and look in game.");
        ImGui::TextDisabled("Table %s (%d curves%s). AC3's menu shows a map at Day 12:00, Evening 20:00 or Night 0:00.",
                            msv.tod.c_str(), msv.n_curves, msv.others.empty() ? "" : "; weather overlays left as they are");
        ImGui::PopTextWrapPos();
        ImGui::TextUnformatted("Presets:");
        for (auto& p : msv.presets) {
            ImGui::SameLine();
            if (ImGui::SmallButton(p.value("label", std::string("?")).c_str())) ms_apply_preset(p);
            if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s", p.value("tip", std::string()).c_str());
        }
        // every control on the day keys, or on every key (night too)
        bool any_all = false, any_day = false;
        for (auto& c : msv.controls) {
            bool all = false;
            if (std::fabs(ms_value(c, &all) - c.neutral) > 1e-6f) (all ? any_all : any_day) = true;
        }
        ImGui::TextDisabled("Each change applies to the day keys (4.5 - 19.5 h) unless its \"all\" box is ticked.");
        if (ImGui::SmallButton("All on day keys")) {
            for (auto& c : msv.controls) { float v = ms_value(c); if (std::fabs(v - c.neutral) > 1e-6f) ms_set(c, v, false); }
            edits.commit();
        }
        ImGui::SameLine();
        if (ImGui::SmallButton("All on every key")) {
            for (auto& c : msv.controls) { float v = ms_value(c); if (std::fabs(v - c.neutral) > 1e-6f) ms_set(c, v, true); }
            edits.commit();
        }
        (void)any_all; (void)any_day;
        std::string group;
        bool open = true;
        for (auto& c : msv.controls) {
            if (c.group != group) {
                group = c.group;
                open = ImGui::TreeNodeEx(group.c_str(), ImGuiTreeNodeFlags_DefaultOpen | ImGuiTreeNodeFlags_NoTreePushOnOpen);
            }
            if (!open) continue;
            ImGui::PushID(c.id.c_str());
            bool all = false;
            float v = ms_value(c, &all);
            bool changed = std::fabs(v - c.neutral) > 1e-6f;
            ImGui::BeginDisabled(c.now.empty());             // (none of its curves on this map)
            if (ImGui::Checkbox("##all", &all)) { ms_set(c, v, all); edits.commit(); }
            if (ImGui::IsItemHovered()) ImGui::SetTooltip("Every key (night too); unticked: the day keys only");
            ImGui::SameLine();
            ImGui::SetNextItemWidth(std::max(80.0f, ImGui::GetContentRegionAvail().x * 0.45f));
            if (ImGui::SliderFloat("##v", &v, c.lo, c.hi, c.fmt.c_str())) ms_set(c, v, all);
            if (ImGui::IsItemDeactivatedAfterEdit()) edits.commit();
            if (ImGui::IsItemHovered()) {
                std::string t = c.label + (c.tip.empty() ? "" : "\n" + c.tip) + "\nThe map now (night / day / evening): ";
                for (size_t k = 0; k < c.now.size(); k++) t += (k ? "  /  " : "") + ms_vec(c.now[k]);
                ImGui::SetTooltip("%s\nCtrl+click to type a value", t.c_str());
            }
            ImGui::SameLine();
            bool flat = std::find(msv.untouched.begin(), msv.untouched.end(), c.id) != msv.untouched.end();
            if (changed) ImGui::TextColored(flat ? ImVec4(1, 0.7f, 0.3f, 1) : ImVec4(0.5f, 1, 0.6f, 1), "%s%s", c.label.c_str(),
                                            flat ? " (no day key here: tick all)" : "");
            else ImGui::TextUnformatted(c.label.c_str());
            if (changed) {
                ImGui::SameLine();
                if (ImGui::SmallButton("x")) { ms_set(c, c.neutral, false); edits.commit(); }
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("Back to the map's own");
            }
            ImGui::EndDisabled();
            ImGui::PopID();
        }
        if (msv.preview_busy || msv.preview_of != ms_settings())
            ImGui::TextDisabled("(working out the change...)");
        else if (msv.changed) ImGui::TextColored(ImVec4(0.5f, 1, 0.6f, 1), "%d curve(s) change", msv.changed);
        for (auto& e : msv.errors) { ImGui::PushTextWrapPos(0); ImGui::TextColored(ImVec4(1, 0.35f, 0.3f, 1), "%s", e.c_str()); ImGui::PopTextWrapPos(); }
        if (ImGui::TreeNode("Curves by hour (read only)")) {
            input_text("##curvefilter", ms_curve_filter, "Find curve (sun, fog, ambient, gamma...)");
            ImGui::Checkbox("Only the ones that change", &ms_only_changed);
            if (ImGui::BeginTable("curves", 5, ImGuiTableFlags_RowBg | ImGuiTableFlags_Borders | ImGuiTableFlags_ScrollY |
                                                   ImGuiTableFlags_Resizable | ImGuiTableFlags_SizingStretchProp,
                                  ImVec2(0, 320))) {
                ImGui::TableSetupScrollFreeze(0, 1);
                ImGui::TableSetupColumn("Curve", 0, 2.6f);
                ImGui::TableSetupColumn("Night 0 h", 0, 1.2f);
                ImGui::TableSetupColumn("Day 12 h", 0, 1.2f);
                ImGui::TableSetupColumn("Evening 20 h", 0, 1.2f);
                ImGui::TableSetupColumn("Keys (h)", 0, 1.0f);
                ImGui::TableHeadersRow();
                for (auto& c : msv.curves) {
                    bool ch = !c.after.is_null();
                    if (ms_only_changed && !ch) continue;
                    if (!contains_ci(c.name + " " + c.path, ms_curve_filter)) continue;
                    ImGui::TableNextRow();
                    ImGui::TableNextColumn();
                    if (ch) ImGui::TextColored(ImVec4(0.5f, 1, 0.6f, 1), "%s", c.name.c_str());
                    else ImGui::TextUnformatted(c.name.c_str());
                    if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s (%s)", c.path.c_str(), c.kind.c_str());
                    for (int k = 0; k < 3; k++) {
                        ImGui::TableNextColumn();
                        std::string a = k < (int)c.at.size() ? ms_vec(c.at[k]) : "-";
                        if (ch && k < (int)c.at_after.size() && ms_vec(c.at_after[k]) != a)
                            ImGui::TextColored(ImVec4(0.5f, 1, 0.6f, 1), "%s -> %s", a.c_str(), ms_vec(c.at_after[k]).c_str());
                        else ImGui::TextUnformatted(a.c_str());
                    }
                    ImGui::TableNextColumn();
                    std::string hs;
                    for (auto& h : c.hours) { char b[16]; snprintf(b, sizeof b, "%g ", h.get<double>()); hs += b; }
                    ImGui::TextDisabled("%s", hs.c_str());
                }
                ImGui::EndTable();
            }
            ImGui::TreePop();
        }
    }
    if (ImGui::CollapsingHeader("Kill height and view distance", ImGuiTreeNodeFlags_DefaultOpen)) {
        json s = ms_settings();
        bool kh = s.contains("water_level");
        float wl = kh ? s["water_level"].get<float>() : msv.water;
        if (ImGui::Checkbox("Change the kill height", &kh)) { ms_put("water_level", kh ? json(msv.water) : json()); edits.commit(); }
        ImGui::BeginDisabled(!kh);
        ImGui::SetNextItemWidth(160);
        if (ImGui::DragFloat("Kill height (m)", &wl, 0.05f, -200.0f, 100.0f, "%.2f") && kh) ms_put("water_level", wl);
        if (ImGui::IsItemDeactivatedAfterEdit()) edits.commit();
        ImGui::EndDisabled();
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("The World's WaterLevel: a player below it dies. This map: %.2f m. (Its DeathHeight, %.0f m, "
                            "kills no one; falls never do.)", msv.water, msv.death);
        ImGui::PopTextWrapPos();
        bool vd = s.contains("view_distance");
        int view = vd ? s["view_distance"].get<int>() : msv.view;
        if (ImGui::Checkbox("Change the view distance", &vd)) { ms_put("view_distance", vd ? json(msv.view) : json()); edits.commit(); }
        ImGui::BeginDisabled(!vd);
        ImGui::SetNextItemWidth(160);
        if (ImGui::SliderInt("Full detail to (m)", &view, 16, 255) && vd) ms_put("view_distance", view);
        if (ImGui::IsItemDeactivatedAfterEdit()) edits.commit();
        ImGui::EndDisabled();
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("Stock %d m (the camera's cell and two round it); farther cells show the World's stand-in "
                            "tiles. 255 draws every loaded cell in full (heavier). Loading distance (%d m) stays.",
                            msv.view, msv.load_dist);
        ImGui::PopTextWrapPos();
        // the stand-in tiles still show the map as it was - off for a cleared / rebuilt map
        bool off = s.contains("stand_ins");
        if (ImGui::Checkbox("Far view off (the stand-in tiles)", &off)) { ms_put("stand_ins", off ? json(false) : json()); edits.commit(); }
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("%d tile(s), %d drawing. They draw every cell past the view distance as the map WAS: off, those "
                            "cells show sky - with view distance 255 that's past ~96 m. For a map cleared or rebuilt "
                            "(Edit > Clear the map).", msv.stand_ins, msv.stand_ins_on);
        ImGui::PopTextWrapPos();
        for (auto& w : msv.warnings) { ImGui::PushTextWrapPos(0); ImGui::TextColored(ImVec4(1, 0.75f, 0.35f, 1), "%s", w.c_str()); ImGui::PopTextWrapPos(); }
    }
    ui_map_panel_ms2();                               // spawn points, boundaries, gameplay objects
    ImGui::End();
}

// the Changes list's map-settings row (Project tab)
void App::ms_changes_rows() {
    auto it = edits.cur.map.find("settings");
    if (it == edits.cur.map.end() || it->empty()) return;
    std::string t = "map settings:";
    if (it->contains("tod")) t += " lighting (" + std::to_string((*it)["tod"].size()) + " control(s))";
    if (it->contains("water_level")) { char b[48]; snprintf(b, sizeof b, " kill height %.2f m", (*it)["water_level"].get<double>()); t += b; }
    if (it->contains("view_distance")) t += " view distance " + std::to_string((*it)["view_distance"].get<int>()) + " m";
    if (it->contains("stand_ins")) t += " far view off";
    if (ImGui::Selectable((t + "##mapsettings").c_str())) ImGui::SetWindowFocus("Map");
}

// -------------------------------------------------------------------------------------- spawns, boundaries --
// Spawn points: the markers of the shown mode move / turn with the gizmo, Delete takes one out of that mode, Ctrl+D
// adds a copy in that mode (Edits::request sends them as "spawns"). Boundaries: an out-of-bounds area's corners, each
// with a gizmo of its own (EditState::map["boundaries"]). The engine checks both as the build will (map_check).

std::string App::ms_mode_rec() const {
    return scene && mode_record >= 0 && mode_record < (int)scene->records.size() ? scene->records[mode_record].name : "";
}

void App::load_boundaries() {
    if (!scene || workspace || msg.areas_loaded || msg.areas_loading) return;
    msg.areas_loading = true;
    std::string archive = scene->archive;
    engine.request("boundaries", {{"archive", archive}}, [this, archive](bool ok, const json& r, const std::string& e) {
        msg.areas_loading = false;
        if (!scene || scene->archive != archive) return;
        if (!ok) { status = "Boundaries: " + e; return; }
        msg.areas.clear();
        for (auto& a : r["areas"]) {
            MsArea x;
            x.uid = a.value("uid", std::string());
            x.name = a.value("name", std::string());
            x.kind = a.value("kind", std::string());
            x.closed = a.value("closed", true);
            x.z = a.value("z", 0.0f);
            for (auto& s : a.value("records", json::array())) x.records.push_back(s.get<std::string>());
            for (auto& s : a.value("modes", json::array())) x.modes.push_back(s.get<std::string>());
            for (auto& c : a.value("corners", json::array())) x.corners.push_back(V3(c[0], c[1], x.z));
            for (auto& p : a.value("polygons", json::array())) {
                std::vector<V3> q;
                for (auto& c : p) q.push_back(V3(c[0], c[1], x.z));
                x.polygons.push_back(q);
            }
            msg.areas.push_back(x);
        }
        msg.areas_loaded = true;
        msg.check_of = json();
    });
}

void App::load_gameplay() {
    if (!scene || workspace || msg.things_loaded || msg.things_loading) return;
    msg.things_loading = true;
    std::string archive = scene->archive;
    engine.request("gameplay", {{"archive", archive}}, [this, archive](bool ok, const json& r, const std::string& e) {
        msg.things_loading = false;
        if (!scene || scene->archive != archive) return;
        if (!ok) { status = "Gameplay objects: " + e; return; }
        msg.things.clear();
        msg.thing_of.clear();
        msg.templates.clear();
        for (auto& t : r["things"]) {
            MsThing x;
            x.uid = uid_from_hex(t.value("uid", std::string("00000000")));
            x.name = t.value("name", std::string());
            x.kind = t.value("kind", std::string());
            x.label = t.value("label", std::string());
            x.map = t.value("map", std::string());
            x.move_only = t.value("move_only", false);
            for (auto& u : t.value("members", json::array())) x.members.push_back(uid_from_hex(u.get<std::string>()));
            for (auto& u : t.value("companions", json::array())) x.companions.push_back(uid_from_hex(u.get<std::string>()));
            int k = (int)msg.things.size();
            msg.thing_of[x.uid] = k;
            for (uint32_t u : x.members) msg.thing_of.emplace(u, k);
            for (uint32_t u : x.companions) msg.thing_of.emplace(u, k);
            msg.things.push_back(x);
        }
        for (auto& t : r["templates"]) {
            MsTemplate x;
            x.key = t.value("key", std::string());
            x.name = t.value("name", std::string());
            x.model = t.value("model", std::string());
            x.kind = t.value("kind", std::string());
            x.label = t.value("label", std::string());
            x.map = t.value("map", std::string());
            x.members = (int)t.value("members", json::array()).size();
            if (t["size"].is_array()) for (int i = 0; i < 3; i++) x.size[i] = t["size"][i].get<float>();
            auto m = t.value("matrix", json::array());
            if (m.size() == 16) for (int i = 0; i < 16; i++) x.matrix.m[i] = m[i].get<float>();
            msg.templates.push_back(x);
        }
        msg.things_loaded = true;
        add_log("gameplay objects: " + std::to_string(msg.things.size()) + " on this map, " +
                std::to_string(msg.templates.size()) + " stock ones to place (Library > Gameplay)");
    });
}

// an area's corners as the edit has them
std::vector<V3> App::ms_corners(int a) const {
    const MsArea& x = msg.areas[a];
    auto it = edits.cur.map.find("boundaries");
    if (it == edits.cur.map.end() || !it->contains(x.uid)) return x.corners;
    std::vector<V3> out;
    for (auto& c : (*it)[x.uid]) out.push_back(V3(c[0], c[1], x.z));
    return out;
}

void App::ms_set_corners(int a, const std::vector<V3>& pts) {
    const MsArea& x = msg.areas[a];
    json b = edits.cur.map.contains("boundaries") ? edits.cur.map["boundaries"] : json::object();
    bool same = pts.size() == x.corners.size();
    for (size_t i = 0; same && i < pts.size(); i++)
        same = std::fabs(pts[i].x - x.corners[i].x) < 1e-4f && std::fabs(pts[i].y - x.corners[i].y) < 1e-4f;
    if (same) b.erase(x.uid);
    else {
        json c = json::array();
        for (auto& p : pts) c.push_back({std::round(p.x * 1000) / 1000, std::round(p.y * 1000) / 1000});
        b[x.uid] = c;
    }
    if (b.empty()) edits.cur.map.erase("boundaries"); else edits.cur.map["boundaries"] = b;
}

bool App::ms_area_in_mode(const MsArea& a) const {
    std::string rec = ms_mode_rec();
    return !rec.empty() && std::find(a.records.begin(), a.records.end(), rec) != a.records.end();
}

// the spawn and boundary edits checked by the engine whenever they change
void App::ms_check_tick() {
    if (!scene || workspace || msg.check_busy || engine.busy() || ImGui::IsAnyItemActive() || dragging || ms_corner_drag) return;
    json req = edits.request(carry_ground);
    json want = {{"spawns", req.value("spawns", json())}, {"boundaries", req.value("boundaries", json())}};
    if (want == msg.check_of) return;
    msg.check_busy = true;
    std::string archive = scene->archive;
    engine.request("map_check", {{"archive", archive}, {"request", want}},
                   [this, archive, want](bool ok, const json& r, const std::string& e) {
        msg.check_busy = false;
        if (!scene || scene->archive != archive) return;
        msg.check_of = want;
        msg.errors.clear();
        msg.warnings.clear();
        if (!ok) { msg.errors.push_back(e); return; }
        for (auto& x : r.value("errors", json::array())) msg.errors.push_back(x.get<std::string>());
        for (auto& x : r.value("warnings", json::array())) msg.warnings.push_back(x.get<std::string>());
        msg.counts = r.value("counts", json::object());
    });
}

// a new spawn of the shown mode where the camera looks: a copy of one of its own markers of that kind
int App::ms_add_spawn(const std::string& kind, V3 at) {
    if (!scene || mode_record < 0) return -1;
    int src = -1;
    for (size_t i = 0; i < scene->entities.size() && src < 0; i++) {
        const Entity& e = scene->entities[i];
        if ((e.flags & F_SPAWN) && e.record == mode_record && e.note.substr(0, e.note.find(" - ")) == kind) src = (int)i;
    }
    if (src < 0) { status = "This mode has no " + kind + " to copy"; return -1; }
    M4 m = rot_z(cam.yaw - 1.5707963f);               // facing the way the camera looks (a spawn faces its local +y)
    m.set_pos(at);
    edits.cur.copies.push_back({src, m, false});
    edits.commit();
    int id = (int)scene->entities.size() + (int)edits.cur.copies.size() - 1;
    selection = {id};
    gizmo_op = ImGuizmo::TRANSLATE;
    status = "A new " + kind + " in " + scene->records[mode_record].label + " (a copy of " + scene->entities[src].name +
             ") - move / turn it with the gizmo";
    return id;
}

// ---- gameplay objects: one click picks the whole object (its head, members and FX companion)
void App::ms_expand_thing(int hit) {
    if (!scene || !msg.things_loaded || hit < 0) return;
    int src = edits.entity_of(hit);
    if (src < 0) return;
    auto it = msg.thing_of.find(scene->entities[src].uid);
    if (it == msg.thing_of.end()) return;
    const MsThing& t = msg.things[it->second];
    std::set<uint32_t> uids(t.members.begin(), t.members.end());
    uids.insert(t.uid);
    uids.insert(t.companions.begin(), t.companions.end());
    int rec = scene->entities[src].record;
    if (!edits.is_copy(hit)) {
        for (size_t i = 0; i < scene->entities.size(); i++) {
            const Entity& e = scene->entities[i];
            if (uids.count(e.uid) && e.record == rec && !edits.removed((int)i) && !selected((int)i)) selection.push_back((int)i);
        }
        return;
    }
    // a copy: the other copies made with it (the same move from their sources)
    M4 D = inverse(scene->entities[src].matrix) * edits.matrix(hit);
    for (size_t k = 0; k < edits.cur.copies.size(); k++) {
        const CopyObj& c = edits.cur.copies[k];
        int id = (int)(scene->entities.size() + k);
        if (c.removed || c.asset >= 0 || c.src < 0 || selected(id) || !uids.count(scene->entities[c.src].uid)) continue;
        if ((scene->entities[c.src].matrix * D).near_equal(c.matrix, 2e-3f)) selection.push_back(id);
    }
}

const MsThing* App::ms_thing_of(int id) const {
    if (!scene || !msg.things_loaded || id < 0 || id >= edits.count()) return nullptr;
    int src = edits.entity_of(id);
    if (src < 0) return nullptr;
    auto it = msg.thing_of.find(scene->entities[src].uid);
    return it == msg.thing_of.end() ? nullptr : &msg.things[it->second];
}

// artifacts, scoring zones and Domination points can only be moved
bool App::ms_refuse(bool removing) {
    for (int id : selection) {
        const MsThing* t = ms_thing_of(id);
        if (t && t->move_only) {
            status = t->label + ": " + (removing ? "can't be taken out" : "can't be copied") +
                     " (the game needs one artifact per team and three Domination points) - move it instead";
            return true;
        }
    }
    return false;
}

const MsTemplate* App::ms_template_of(int id) const {
    if (!edits.is_placed(id)) return nullptr;
    const std::string& k = edits.assets[edits.asset_of(id)].key;
    if (k.rfind("gp:", 0) != 0) return nullptr;
    for (auto& t : msg.templates) if (t.key == k) return &t;
    return nullptr;
}

void App::ms_inspector_note(int id) {
    const MsThing* t = ms_thing_of(id);
    const MsTemplate* tp = ms_template_of(id);
    bool spawn = !edits.is_placed(id) && edits.entity_of(id) >= 0 && (scene->entities[edits.entity_of(id)].flags & F_SPAWN);
    if (!t && !tp && !spawn) return;
    ImGui::PushTextWrapPos(0);
    if (spawn) {
        const Entity& e = scene->entities[edits.entity_of(id)];
        ImGui::TextColored(ImVec4(0.4f, 1, 0.5f, 1), "SPAWN POINT: %s", e.note.substr(0, e.note.find(" - ")).c_str());
        ImGui::TextDisabled("In %s. Moving or deleting it changes it in this mode only (a spawn other modes use too is split "
                            "off); Ctrl+D adds another here. The Map panel lists what the mode keeps and the checks.",
                            scene->records[e.record].label.c_str());
    } else if (t) {
        ImGui::TextColored(ImVec4(0.45f, 0.85f, 1, 1), "GAMEPLAY OBJECT: %s", t->label.c_str());
        ImGui::TextDisabled("%s - %d part(s)%s. One click picks it whole. Build moves it through the gameplay engine "
                            "(its stored state, FX and map icon go with it).%s", t->name.c_str(), (int)t->members.size(),
                            t->companions.empty() ? "" : " + its FX",
                            t->move_only ? (t->kind == "domination_point" ? " Move only: its zone goes with it." : " Move only.") : "");
    } else {
        ImGui::TextColored(ImVec4(0.45f, 0.85f, 1, 1), "GAMEPLAY OBJECT: %s", tp->label.c_str());
        ImGui::TextDisabled("From %s (%s). Build copies it in through the gameplay engine, with its models, textures, FX "
                            "and animations. The viewport draws the parts the map gives a model of its own (a hay cart's "
                            "body may be missing here; it comes with the build).", tp->map.c_str(), tp->name.c_str());
    }
    ImGui::PopTextWrapPos();
}

int App::place_gameplay(int t, V3 at) {
    if (!scene || t < 0 || t >= (int)msg.templates.size()) return -1;
    const MsTemplate& tp = msg.templates[t];
    M4 m = tp.matrix;
    m.set_pos(at);
    CopyObj o{-1, m, false, edits.asset_index(tp.key, tp.label + " (" + tp.map + ")", tp.map)};
    edits.cur.copies.push_back(o);
    edits.commit();
    request_assets();
    int id = (int)scene->entities.size() + (int)edits.cur.copies.size() - 1;
    selection = {id};
    gizmo_op = ImGuizmo::TRANSLATE;
    status = tp.label + " from " + tp.map + " placed - move / turn it with the gizmo; Build copies it in";
    add_log(status);
    return id;
}

void App::ui_gameplay_library() {
    if (!scene) { ImGui::TextDisabled("Open a map first."); return; }
    load_gameplay();
    if (!msg.things_loaded) { ImGui::TextDisabled("Reading the gameplay objects (the first time: every stock map's)..."); return; }
    ImGui::TextDisabled("%d gameplay objects on this map; %d stock ones to place", (int)msg.things.size(), (int)msg.templates.size());
    float side = std::min(330.0f, ImGui::GetContentRegionAvail().x * 0.4f);
    ImVec2 avail = ImGui::GetContentRegionAvail();
    if (ImGui::BeginTable("gptable", 4, ImGuiTableFlags_RowBg | ImGuiTableFlags_ScrollY | ImGuiTableFlags_Resizable |
                                        ImGuiTableFlags_BordersInnerV, ImVec2(avail.x - side - 8, avail.y))) {
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableSetupColumn("Object", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableSetupColumn("Kind", ImGuiTableColumnFlags_WidthFixed, 100);
        ImGui::TableSetupColumn("Map", ImGuiTableColumnFlags_WidthFixed, 110);
        ImGui::TableSetupColumn("Size (m)", ImGuiTableColumnFlags_WidthFixed, 110);
        ImGui::TableHeadersRow();
        for (int i = 0; i < (int)msg.templates.size(); i++) {
            const MsTemplate& t = msg.templates[i];
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            ImGui::PushID(i);
            if (ImGui::Selectable(t.model.c_str(), i == gp_sel, ImGuiSelectableFlags_SpanAllColumns | ImGuiSelectableFlags_AllowDoubleClick)) {
                gp_sel = i;
                if (ImGui::IsMouseDoubleClicked(0)) place_gameplay(i, placement_point());
            }
            if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s (%s)\nDouble-click: place it where the camera looks", t.name.c_str(), t.map.c_str());
            ImGui::PopID();
            ImGui::TableSetColumnIndex(1);
            ImGui::TextUnformatted(t.kind == "hiding_spot" ? "hiding spot" : t.kind == "chase_breaker" ? "chase breaker" : t.kind.c_str());
            ImGui::TableSetColumnIndex(2);
            ImGui::TextUnformatted(t.map.c_str());
            ImGui::TableSetColumnIndex(3);
            ImGui::Text("%.1f x %.1f x %.1f", t.size[0], t.size[1], t.size[2]);
        }
        ImGui::EndTable();
    }
    ImGui::SameLine();
    ImGui::BeginChild("gpside", ImVec2(side, avail.y));
    ImGui::PushTextWrapPos(0);
    if (gp_sel < 0 || gp_sel >= (int)msg.templates.size()) {
        ImGui::TextDisabled("AC3's own gameplay objects from its 7 stock maps: hiding spots (hay / leaves / cotton carts, fur "
                            "stacks), blend benches, chase breakers (doors, shutters, platforms, a chandelier) and rope lifts. "
                            "Double-click one to place it where the camera looks. The map's own are in the viewport: a click "
                            "picks one whole - move, turn, copy (Ctrl+D) or take it out (Delete). Artifacts, scoring zones "
                            "and Domination points (in their modes) can be moved only.");
    } else {
        const MsTemplate& t = msg.templates[gp_sel];
        ImGui::TextColored(ImVec4(0.45f, 0.85f, 1, 1), "%s", t.label.c_str());
        ImGui::Text("%s - %s", t.map.c_str(), t.name.c_str());
        ImGui::Text("%.1f x %.1f x %.1f m, %d part(s)", t.size[0], t.size[1], t.size[2], t.members);
        if (t.kind == "chase_breaker")
            ImGui::TextColored(ImVec4(1, 0.75f, 0.35f, 1), "Chase breakers are bare door leaves / shutters swinging on their hinges: "
                               "put one in a doorway or passage its width (in the open nobody reads it as a door).");
        if (t.kind == "lift")
            ImGui::TextColored(ImVec4(1, 0.75f, 0.35f, 1), "A lift goes against a building with a climb ledge at its top (8 or 12 m): "
                               "without one players fall straight back (GP1).");
        if (ImGui::Button("Place where the camera looks")) place_gameplay(gp_sel, placement_point());
    }
    ImGui::PopTextWrapPos();
    ImGui::EndChild();
}

// ---- the viewport: boundaries drawn, corners picked and dragged, labels
bool App::ms_click(ImVec2 mouse) {
    if (!scene || ms_area < 0 || ms_area >= (int)msg.areas.size()) return false;
    std::vector<V3> c = ms_corners(ms_area);
    M4 vpm = view_m * proj_m;
    int best = -1;
    float bd = 12.0f;
    for (size_t i = 0; i < c.size(); i++) {
        V3 p = c[i];
        float x = p.x * vpm.m[0] + p.y * vpm.m[4] + p.z * vpm.m[8] + vpm.m[12];
        float y = p.x * vpm.m[1] + p.y * vpm.m[5] + p.z * vpm.m[9] + vpm.m[13];
        float w = p.x * vpm.m[3] + p.y * vpm.m[7] + p.z * vpm.m[11] + vpm.m[15];
        if (w <= 0.1f) continue;
        ImVec2 s(vp_pos.x + (x / w * 0.5f + 0.5f) * vp_size.x, vp_pos.y + (0.5f - y / w * 0.5f) * vp_size.y);
        float d = std::sqrt((s.x - mouse.x) * (s.x - mouse.x) + (s.y - mouse.y) * (s.y - mouse.y));
        if (d < bd) { bd = d; best = (int)i; }
    }
    if (best < 0) return false;
    ms_corner = best;
    selection.clear();
    sel_region = -1;
    sel_route = -1;
    status = msg.areas[ms_area].name + ": corner " + std::to_string(best + 1) + " - drag it with the gizmo (Delete removes it)";
    return true;
}

bool App::ms_corner_gizmo() {
    if (!scene || ms_area < 0 || ms_area >= (int)msg.areas.size() || !selection.empty()) { ms_corner_drag = false; return false; }
    std::vector<V3> c = ms_corners(ms_area);
    if (ms_corner < 0 || ms_corner >= (int)c.size()) { ms_corner_drag = false; return false; }
    M4 m = translation(c[ms_corner]);
    float snapv[3] = {snap_move, snap_move, snap_move};
    ImGuizmo::PushID(7171);
    ImGuizmo::Manipulate(view_m.m, proj_m.m, (ImGuizmo::OPERATION)(ImGuizmo::TRANSLATE_X | ImGuizmo::TRANSLATE_Y),
                         ImGuizmo::WORLD, m.m, nullptr, snap ? snapv : nullptr);
    bool using_it = ImGuizmo::IsUsing();
    ImGuizmo::PopID();
    if (using_it) {
        c[ms_corner] = V3(m.m[12], m.m[13], c[ms_corner].z);
        ms_set_corners(ms_area, c);
        ms_corner_drag = true;
    } else if (ms_corner_drag) {
        ms_corner_drag = false;
        edits.commit();
    }
    return using_it || ImGuizmo::IsOver();
}

bool App::ms_shortcuts() {
    if (ms_area < 0 || ms_corner < 0 || !selection.empty()) return false;
    if (ImGui::IsKeyPressed(ImGuiKey_Escape)) { ms_corner = -1; return true; }
    if (ImGui::IsKeyPressed(ImGuiKey_Delete)) {
        std::vector<V3> c = ms_corners(ms_area);
        if ((int)c.size() > (msg.areas[ms_area].closed ? 3 : 2)) {
            c.erase(c.begin() + ms_corner);
            ms_set_corners(ms_area, c);
            edits.commit();
            ms_corner = std::min(ms_corner, (int)c.size() - 1);
        }
        return true;
    }
    return false;
}

void App::ms_labels(ImDrawList* dl) {
    if (!show_labels || mode_record < 0 || !msg.things_loaded) return;
    M4 vpm = view_m * proj_m;
    for (size_t i = 0; i < scene->entities.size(); i++) {
        const Entity& e = scene->entities[i];
        if (e.record != mode_record || !visible((int)i)) continue;
        auto it = msg.thing_of.find(e.uid);
        if (it == msg.thing_of.end() || msg.things[it->second].uid != e.uid || !msg.things[it->second].move_only) continue;
        V3 p = edits.cur.matrix[i].pos() + V3(0, 0, 2.4f);
        float x = p.x * vpm.m[0] + p.y * vpm.m[4] + p.z * vpm.m[8] + vpm.m[12];
        float y = p.x * vpm.m[1] + p.y * vpm.m[5] + p.z * vpm.m[9] + vpm.m[13];
        float w = p.x * vpm.m[3] + p.y * vpm.m[7] + p.z * vpm.m[11] + vpm.m[15];
        if (w <= 0.1f) continue;
        ImVec2 sp(vp_pos.x + (x / w * 0.5f + 0.5f) * vp_size.x, vp_pos.y + (0.5f - y / w * 0.5f) * vp_size.y);
        const std::string& lb = msg.things[it->second].label;
        ImVec2 ts = ImGui::CalcTextSize(lb.c_str());
        dl->AddRectFilled(ImVec2(sp.x - ts.x / 2 - 3, sp.y - 1), ImVec2(sp.x + ts.x / 2 + 3, sp.y + ts.y + 1), IM_COL32(10, 30, 50, 200), 3);
        dl->AddText(ImVec2(sp.x - ts.x / 2, sp.y), IM_COL32(120, 210, 255, 255), lb.c_str());
    }
}

void App::ms_draw() {
    if (!scene || workspace || !ms_show_bounds || !msg.areas_loaded || mode_record < 0) return;
    std::vector<LineVert> l;
    for (int a = 0; a < (int)msg.areas.size(); a++) {
        const MsArea& x = msg.areas[a];
        if (!ms_area_in_mode(x)) continue;
        bool edit = a == ms_area;
        if (x.kind == "oob") {
            std::vector<V3> c = ms_corners(a);
            uint32_t col = edit ? rgba(1, 0.45f, 0.2f, 1) : rgba(1, 0.3f, 0.25f, 0.85f);
            uint32_t top = edit ? rgba(1, 0.45f, 0.2f, 0.6f) : rgba(1, 0.3f, 0.25f, 0.35f);
            size_t n = x.closed ? c.size() : c.size() - 1;
            for (size_t i = 0; i < n && c.size() >= 2; i++) {
                V3 p = c[i], q = c[(i + 1) % c.size()];
                l.push_back({p + V3(0, 0, 0.1f), col}); l.push_back({q + V3(0, 0, 0.1f), col});
                l.push_back({p + V3(0, 0, 3.0f), top}); l.push_back({q + V3(0, 0, 3.0f), top});
            }
            for (size_t i = 0; i < c.size(); i++) {
                bool s = edit && (int)i == ms_corner;
                uint32_t pc = s ? rgba(1, 0.9f, 0.2f, 1) : col;
                l.push_back({c[i], pc}); l.push_back({c[i] + V3(0, 0, edit ? 4.0f : 3.0f), pc});
                if (edit) {                                 // a handle at each corner
                    float r = s ? 0.6f : 0.4f;
                    l.push_back({c[i] + V3(-r, 0, 0.1f), pc}); l.push_back({c[i] + V3(r, 0, 0.1f), pc});
                    l.push_back({c[i] + V3(0, -r, 0.1f), pc}); l.push_back({c[i] + V3(0, r, 0.1f), pc});
                }
            }
            // the original outline, faint, while it's changed
            if (edits.cur.map.contains("boundaries") && edits.cur.map["boundaries"].contains(x.uid))
                for (size_t i = 0; i < (x.closed ? x.corners.size() : x.corners.size() - 1); i++) {
                    l.push_back({x.corners[i] + V3(0, 0, 0.1f), rgba(0.7f, 0.7f, 0.7f, 0.4f)});
                    l.push_back({x.corners[(i + 1) % x.corners.size()] + V3(0, 0, 0.1f), rgba(0.7f, 0.7f, 0.7f, 0.4f)});
                }
        } else {
            uint32_t col = rgba(0.35f, 0.7f, 1, 0.8f);
            for (auto& p : x.polygons)
                for (size_t i = 0; i < p.size(); i++) {
                    l.push_back({p[i] + V3(0, 0, 0.15f), col}); l.push_back({p[(i + 1) % p.size()] + V3(0, 0, 0.15f), col});
                }
        }
    }
    renderer.draw_lines(l, true);
}

void App::ui_map_panel_ms2() {
    load_boundaries();
    load_gameplay();
    ms_check_tick();
    std::string rec = ms_mode_rec();
    std::string mode = mode_record >= 0 ? scene->records[mode_record].label : "";
    if (ImGui::CollapsingHeader(("Spawn points" + (mode.empty() ? std::string() : " - " + mode)).c_str(), ImGuiTreeNodeFlags_DefaultOpen)) {
        ImGui::PushTextWrapPos(0);
        if (rec.empty()) {
            ImGui::TextDisabled("Choose a game mode on the viewport's toolbar: its spawn points show, and can be moved (W / E), "
                                "taken out of it (Delete) or copied in it (Ctrl+D).");
        } else {
            std::map<std::string, int> kinds;
            for (auto& e : scene->entities) if ((e.flags & F_SPAWN) && e.record == mode_record) kinds[e.note.substr(0, e.note.find(" - "))]++;
            const json& c = msg.counts.contains(rec) ? msg.counts[rec] : json::object();
            if (kinds.empty()) ImGui::TextDisabled("This mode has no spawn points on this map.");
            for (auto& [k, n0] : kinds) {
                int n = c.contains(k) ? c[k].get<int>() : n0;
                ImGui::Text("%s: %d%s", k.c_str(), n, n != n0 ? (" (was " + std::to_string(n0) + ")").c_str() : "");
                ImGui::SameLine();
                if (ImGui::SmallButton(("+ add##" + k).c_str())) ms_add_spawn(k, placement_point());
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("A new %s where the camera looks, facing the way it looks", k.c_str());
            }
            ImGui::TextDisabled("Click a marker: W moves, E turns it; Delete takes it out of %s only; Ctrl+D copies it here.", mode.c_str());
        }
        ImGui::PopTextWrapPos();
    }
    if (ImGui::CollapsingHeader(("Boundaries" + (mode.empty() ? std::string() : " - " + mode)).c_str(), ImGuiTreeNodeFlags_DefaultOpen)) {
        ImGui::Checkbox("Show the shown mode's boundaries", &ms_show_bounds);
        if (rec.empty()) ImGui::TextDisabled("Choose a game mode on the viewport's toolbar.");
        else if (!msg.areas_loaded) ImGui::TextDisabled("Reading the boundaries...");
        for (int a = 0; a < (int)msg.areas.size() && !rec.empty(); a++) {
            const MsArea& x = msg.areas[a];
            if (!ms_area_in_mode(x)) continue;
            ImGui::PushID(a);
            bool changed = edits.cur.map.contains("boundaries") && edits.cur.map["boundaries"].contains(x.uid);
            if (x.kind == "oob") {
                bool on = ms_area == a;
                if (ImGui::Checkbox("##edit", &on)) { ms_area = on ? a : -1; ms_corner = -1; if (on) selection.clear(); }
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("Edit its corners: click a corner post in the viewport, drag it with the gizmo");
                ImGui::SameLine();
                ImGui::TextColored(changed ? ImVec4(0.5f, 1, 0.6f, 1) : ImVec4(1, 0.55f, 0.45f, 1), "out of bounds: %s (%d corners%s)%s",
                                   x.name.c_str(), (int)ms_corners(a).size(), x.closed ? "" : ", open", changed ? " - changed" : "");
                if (ImGui::IsItemHovered()) {
                    std::string m;
                    for (auto& s : x.modes) m += (m.empty() ? "" : ", ") + s;
                    ImGui::SetTooltip("Used by: %s - one outline for all of them", m.c_str());
                }
                if (on) {
                    std::vector<V3> c = ms_corners(a);
                    ImGui::Indent();
                    if (ms_corner >= 0 && ms_corner < (int)c.size()) {
                        float xy[2] = {c[ms_corner].x, c[ms_corner].y};
                        ImGui::SetNextItemWidth(170);
                        if (ImGui::DragFloat2(("Corner " + std::to_string(ms_corner + 1)).c_str(), xy, 0.05f, 0, 0, "%.2f")) {
                            c[ms_corner].x = xy[0]; c[ms_corner].y = xy[1];
                            ms_set_corners(a, c);
                        }
                        if (ImGui::IsItemDeactivatedAfterEdit()) edits.commit();
                        if (ImGui::SmallButton("Insert a corner after it")) {
                            V3 p = c[ms_corner], q = c[(ms_corner + 1) % c.size()];
                            c.insert(c.begin() + ms_corner + 1, (p + q) * 0.5f);
                            ms_set_corners(a, c);
                            edits.commit();
                            ms_corner++;
                        }
                        ImGui::SameLine();
                        ImGui::BeginDisabled((int)c.size() <= (x.closed ? 3 : 2));
                        if (ImGui::SmallButton("Remove it")) {
                            c.erase(c.begin() + ms_corner);
                            ms_set_corners(a, c);
                            edits.commit();
                            ms_corner = std::min(ms_corner, (int)c.size() - 1);
                        }
                        ImGui::EndDisabled();
                    } else {
                        ImGui::TextDisabled("Click a corner post in the viewport.");
                    }
                    if (changed && ImGui::SmallButton("Put the boundary back")) { ms_set_corners(a, x.corners); edits.commit(); ms_corner = -1; }
                    ImGui::Unindent();
                }
            } else {
                ImGui::TextColored(ImVec4(0.45f, 0.75f, 1, 1), "   zone: %s", x.name.c_str());
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("A gameplay zone (Domination area / Artifact Assault territory): drawn only.\n"
                                                              "A Domination zone moves with its point.");
            }
            ImGui::PopID();
        }
    }
    if (!msg.errors.empty() || !msg.warnings.empty() || msg.check_busy) {
        ImGui::SeparatorText("Checks (spawns, boundaries)");
        ImGui::PushTextWrapPos(0);
        if (msg.check_busy) ImGui::TextDisabled("(checking...)");
        for (auto& e : msg.errors) ImGui::TextColored(ImVec4(1, 0.35f, 0.3f, 1), "%s", e.c_str());
        for (auto& w : msg.warnings) ImGui::TextColored(ImVec4(1, 0.75f, 0.35f, 1), "%s", w.c_str());
        ImGui::PopTextWrapPos();
    }
    if (ImGui::CollapsingHeader("Gameplay objects")) {
        ImGui::PushTextWrapPos(0);
        if (!msg.things_loaded) ImGui::TextDisabled("Reading them...");
        else {
            std::map<std::string, int> n;
            for (auto& t : msg.things) n[t.kind]++;
            for (auto& [k, c] : n) ImGui::BulletText("%s: %d", k.c_str(), c);
            ImGui::TextDisabled("A click on one picks it whole (its parts and FX). Artifacts, scoring zones and Domination points "
                                "show in their modes and can only be moved (a Domination zone follows its point). Library > "
                                "Gameplay places the stock maps' hiding spots, benches, chase breakers and lifts.");
            if (ImGui::SmallButton("Open Library > Gameplay")) { lib_tab = 3; focus_library = true; }
        }
        ImGui::PopTextWrapPos();
    }
}

// ------------------------------------------------------------------------------------------------ self-tests --
// --ms1-test <archive> <folder>: the LT3 preset, the kill height -15 -> -1.5 m, the view distance 63 -> 127 m and a
// 6 m pit 3 m deep 14 m south of Wolcott's pillory (a player who drops in dies below -1.5 m); the preview, the project
// file round trip, then the build and its read-back (test_tick's steps 50 - 7)
bool App::ms_test_tick() {
    if (test_step >= 1800) return ms2_test_tick();
    auto shot = [&](const char* name) { shot_path = test_dir + "\\" + name; tlog(std::string("screenshot ") + name); };
    auto fail = [&](const std::string& why) { tlog("FAIL: " + why); test_exit = 1; test_step = 99; };
    switch (test_step) {
    case 1700:
        load_mapsettings();
        test_t0 = ImGui::GetTime();
        test_step = 1701;
        return true;
    case 1701: {
        if (!msv.loaded) {
            if (ImGui::GetTime() - test_t0 > 300 || (!msv.loading && !engine.busy())) fail("map settings not read: " + msv.error);
            return true;
        }
        tlog("map settings: " + msv.tod + " (" + std::to_string(msv.n_curves) + " curves), " +
             std::to_string(msv.controls.size()) + " controls, " + std::to_string(msv.presets.size()) + " presets; kill height " +
             std::to_string(msv.water).substr(0, 6) + " m, view distance " + std::to_string(msv.view) + " m");
        const json* lt3 = nullptr;
        for (auto& p : msv.presets) if (p.value("id", std::string()) == "LT3") lt3 = &p;
        if (!lt3) { fail("no LT3 preset"); return true; }
        ms_apply_preset(*lt3);
        ms_put("water_level", -1.5);
        ms_put("view_distance", 127);
        edits.commit();
        // the pit: 6 x 6 m round (2, -28) - 14 m south of the pillory, clear of blend groups and spawns -, 3 m down
        start_region();
        for (V3 o : {V3(-3, -3, 0), V3(3, -3, 0), V3(3, 3, 0), V3(-3, 3, 0)}) {
            float t = 0;
            V3 from = V3(2, -28, 0) + o + V3(0, 0, 30);
            if (pick(from, V3(0, 0, -1), &t) >= 0) draft.push_back(from + V3(0, 0, -t));
            else draft.push_back(V3(2, -28, 0) + o);
        }
        finish_region();
        if (sel_region < 0) { fail("the pit wasn't made: " + status); return true; }
        edits.cur.regions[sel_region].dz = -3.0f;
        edits.cur.regions[sel_region].name = "kill height test pit";
        edits.commit();
        json s = ms_settings();
        tlog("LT3 (" + std::to_string(s["tod"].size()) + " controls), kill height -1.5 m, view distance 127 m; a 6 m pit 3 m "
             "deep at (2, -28)");
        ImGui::SetWindowFocus("Map");
        cam.pos = V3(2, -28, 0) + V3(-10, -16, 12);
        V3 d = normalize(V3(2, -28, 0) - cam.pos);
        cam.yaw = std::atan2(d.y, d.x);
        cam.pitch = std::asin(d.z);
        test_t0 = ImGui::GetTime();
        test_step = 1702;
        return true;
    }
    case 1702: {
        if (msv.preview_busy || msv.preview_of != ms_settings()) {
            if (ImGui::GetTime() - test_t0 > 120) fail("no preview of the settings");
            return true;
        }
        std::string un;
        for (auto& u : msv.untouched) un += " " + u;
        tlog("preview: " + std::to_string(msv.changed) + " curve(s) change; no day key here:" + (un.empty() ? " none" : un) +
             "; " + std::to_string(msv.errors.size()) + " error(s), " + std::to_string(msv.warnings.size()) + " warning(s)");
        for (auto& w : msv.warnings) tlog("  warning: " + w);
        if (!msv.errors.empty()) { fail("the settings have errors: " + msv.errors[0]); return true; }
        if (msv.changed < 5) { fail("LT3 changes only " + std::to_string(msv.changed) + " curve(s)"); return true; }
        // the project file round trip: the settings and the pit come back as they were
        project_path = test_dir + "\\ms1.ac3proj";
        save_project(false);
        json j = read_json(project_path);
        bool same = j.value("map_settings", json()) == ms_settings() && j.value("regions", json::array()).size() == 1;
        Edits back;
        back.reset(scene.get());
        std::string pr = back.apply_project(j);
        same = same && pr.empty() && back.cur.map.value("settings", json()) == ms_settings() && back.cur.regions.size() == 1;
        tlog(std::string(same ? "ok" : "FAIL") + ": the project file keeps the settings and the pit (" + project_path + ")");
        if (!same) { fail("project round trip"); return true; }
        test_wait = 10;
        test_step = 1703;
        return true;
    }
    case 1703:
        shot("1_map_settings.png");
        test_step = 50;                               // save the project, build, read back (test_tick), done
        return true;
    }
    return false;
}
// --ms2-test <archive> <folder>: on Wolcott - Training's spawns (a respawn moved 3 m, one taken out, one added by Ctrl+D
// 5 m north-east of the pillory, a match start moved 2 m), its boundary's corner nearest the pillory pulled 6 m in, a
// hay cart picked whole by one click and moved 2 m, a bench copied, a window chase breaker taken out, Domination
// point B moved 2 m (its zone follows), CTF_2's team-1 artifact with its scoring zone moved 1 m, Boston Mill's leaves
// cart placed from Library > Gameplay; the engine's check, the project file round trip, then the build (steps 50 - 7)
bool App::ms2_test_tick() {
    auto shot = [&](const char* name) { shot_path = test_dir + "\\" + name; tlog(std::string("screenshot ") + name); };
    auto fail = [&](const std::string& why) { tlog("FAIL: " + why); test_exit = 1; test_step = 99; };
    const V3 pillory(4, -14, 0);
    static std::vector<int> cand;
    auto ground = [&](V3 p) {                         // p dropped onto whatever is under it (markers hidden)
        bool mk = show_markers;
        show_markers = false;
        float t = 0;
        if (pick(V3(p.x, p.y, p.z + 20), V3(0, 0, -1), &t) >= 0) p.z = p.z + 20 - t;
        show_markers = mk;
        return p;
    };
    auto find = [&](const std::string& n, const std::string& rec_label = "") {
        for (size_t i = 0; i < scene->entities.size(); i++)
            if (scene->entities[i].name == n && (rec_label.empty() || scene->records[scene->entities[i].record].label == rec_label))
                return (int)i;
        return -1;
    };
    auto move_sel = [&](V3 d) {
        std::vector<int> movers;
        for (int id : selection) if (edits.movable(id) && !edits.removed(id)) movers.push_back(id);
        std::vector<M4> start;
        for (int id : movers) start.push_back(edits.matrix(id));
        M4 p0 = pivot_of(movers);
        apply_pivot(movers, start, p0, translation(p0.pos() + d));
    };
    switch (test_step) {
    case 1800:
        for (size_t r = 0; r < scene->records.size(); r++) if (scene->records[r].label == "Training") mode_record = (int)r;
        if (mode_record < 0) { fail("no Training mode on this map"); return true; }
        load_boundaries();
        load_gameplay();
        test_t0 = ImGui::GetTime();
        test_step = 1801;
        return true;
    case 1801: {
        if (!msg.areas_loaded || !msg.things_loaded) {
            if (ImGui::GetTime() - test_t0 > 900) fail("boundaries / gameplay objects not read: " + status);
            return true;
        }
        tlog("boundaries: " + std::to_string(msg.areas.size()) + " areas; gameplay objects: " + std::to_string(msg.things.size()) +
             " on this map, " + std::to_string(msg.templates.size()) + " stock ones");
        std::vector<int> resp, start;
        for (size_t i = 0; i < scene->entities.size(); i++) {
            const Entity& e = scene->entities[i];
            if (!(e.flags & F_SPAWN) || e.record != mode_record) continue;
            (e.note.rfind("respawn", 0) == 0 ? resp : start).push_back((int)i);
        }
        auto by_name = [&](int a, int b) { return scene->entities[a].name < scene->entities[b].name; };
        std::sort(resp.begin(), resp.end(), by_name);
        std::sort(start.begin(), start.end(), by_name);
        if (resp.size() < 3 || start.empty()) { fail("Training hasn't 3 respawns and a start"); return true; }
        // a respawn moved 3 m east, one taken out, one copied (Ctrl+D) to 5 m north-east of the pillory, a start moved 2 m
        M4& m0 = edits.cur.matrix[resp[0]];
        m0.set_pos(ground(m0.pos() + V3(3, 0, 0)));
        edits.set_removed(resp[1], true);
        selection = {resp[2]};
        copy_selected();
        int cp = selection.empty() ? -1 : selection[0];
        if (cp < 0 || !edits.is_copy(cp)) { fail("Ctrl+D on a spawn made no copy: " + status); return true; }
        edits.matrix(cp).set_pos(ground(pillory + V3(5, 5, 0)));
        M4& s0 = edits.cur.matrix[start[0]];
        s0.set_pos(ground(s0.pos() + V3(0, 2, 0)));
        edits.commit();
        selection.clear();
        json r = edits.request(carry_ground);
        tlog("spawns: " + std::to_string(r["spawns"]["move"].size()) + " moved, " + std::to_string(r["spawns"]["remove"].size()) +
             " removed, " + std::to_string(r["spawns"]["add"].size()) + " added (Training)");
        // the boundary Training uses: its corners by distance from the pillory
        ms_area = -1;
        for (int a = 0; a < (int)msg.areas.size(); a++) if (msg.areas[a].kind == "oob" && ms_area_in_mode(msg.areas[a])) ms_area = a;
        if (ms_area < 0) { fail("no out-of-bounds area in Training"); return true; }
        cand.clear();
        for (int i = 0; i < (int)msg.areas[ms_area].corners.size(); i++) cand.push_back(i);
        std::sort(cand.begin(), cand.end(), [&](int a, int b) {
            V3 pa = msg.areas[ms_area].corners[a] - pillory, pb = msg.areas[ms_area].corners[b] - pillory;
            return pa.x * pa.x + pa.y * pa.y < pb.x * pb.x + pb.y * pb.y; });
        ms_test_k = 0;
        test_step = 1802;
        return true;
    }
    case 1802: {
        const MsArea& x = msg.areas[ms_area];
        std::vector<V3> c = x.corners;
        V3 mid;
        for (auto& p : c) mid += p * (1.0f / c.size());
        int i = cand[ms_test_k];
        V3 d = mid - c[i];
        d.z = 0;
        c[i] += normalize(d) * 6.0f;
        ms_corner = i;
        ms_set_corners(ms_area, c);
        edits.commit();
        test_t0 = ImGui::GetTime();
        test_step = 1803;
        return true;
    }
    case 1803: {
        json r = edits.request(carry_ground);
        json want = {{"spawns", r.value("spawns", json())}, {"boundaries", r.value("boundaries", json())}};
        if (msg.check_busy || msg.check_of != want) {
            if (ImGui::GetTime() - test_t0 > 300) fail("no check of the spawn / boundary edits");
            return true;
        }
        bool bound = false;
        for (auto& e : msg.errors) bound |= e.find("boundary") != std::string::npos || e.find("out of bounds") != std::string::npos;
        if (bound && ms_test_k + 1 < (int)cand.size()) {
            ms_set_corners(ms_area, msg.areas[ms_area].corners);
            ms_test_k++;
            test_step = 1802;
            return true;
        }
        if (!msg.errors.empty()) { fail("the spawn / boundary edits have errors: " + msg.errors[0]); return true; }
        tlog(msg.areas[ms_area].name + ": corner " + std::to_string(cand[ms_test_k] + 1) + " pulled 6 m in (" +
             std::to_string(ms_test_k + 1) + " tried); check: 0 errors, " + std::to_string(msg.warnings.size()) + " warning(s)");
        for (auto& w : msg.warnings) tlog("  warning: " + w);
        test_step = 1804;
        return true;
    }
    case 1804: {
        // gameplay: one click on a hay cart's part picks the whole cart
        int hay = -1;
        for (size_t i = 0; i < scene->entities.size() && hay < 0; i++)
            if (scene->entities[i].name.rfind("AC3MP_FORT_HayCart_01a_011/", 0) == 0 && scene->entities[i].mesh >= 0) hay = (int)i;
        if (hay < 0) { fail("no part of hay cart AC3MP_FORT_HayCart_01a_011"); return true; }
        selection.clear();
        scope_group = -1;
        click_pick(hay, false, false);
        int whole = (int)selection.size();
        tlog("a click on " + scene->entities[hay].name + " picks " + std::to_string(whole) + " (the cart's head and parts)");
        if (whole < 3) { fail("the hay cart wasn't picked whole"); return true; }
        move_sel(V3(2, 0, 0));
        // a bench copied 3 m along (onto the ground)
        int bench = find("AC3MP_FORT_Bench_01A_018");
        if (bench < 0) { fail("no bench AC3MP_FORT_Bench_01A_018"); return true; }
        selection.clear();
        click_pick(bench, false, false);
        copy_selected();
        if (selection.empty() || !edits.is_copy(selection[0])) { fail("the bench wasn't copied: " + status); return true; }
        V3 bp = edits.matrix(selection[0]).pos();
        move_sel(ground(V3(2, -27, 0)) - bp);         // (the open yard 13 m south of the pillory: nothing near it)
        // a window chase breaker taken out (whole: its leaves and FX)
        int win = -1;
        for (size_t i = 0; i < scene->entities.size() && win < 0; i++)
            if (scene->entities[i].name.rfind("AC3MP_FORT_Chasebreaker_Window_01A_003/", 0) == 0 && scene->entities[i].mesh >= 0) win = (int)i;
        selection.clear();
        click_pick(win, false, false);
        int nwin = (int)selection.size();
        remove_selected();
        // Domination point B 2 m north (its zone follows); taking it out is refused
        int dom = find("Domination_Chest_02", "Domination");
        selection.clear();
        click_pick(dom, false, false);
        remove_selected();
        bool refused = !edits.removed(dom);
        selection = {dom};
        move_sel(V3(0, 2, 0));
        // CTF_2's team-1 artifact with its scoring zone, 1 m east
        int flag = find("CTF_Flag_1", "CTF_2"), zone = find("CTF_ScoringZone_Team1", "CTF_2");
        if (dom < 0 || flag < 0 || zone < 0) { fail("Domination point B / CTF_2's artifact or scoring zone not found"); return true; }
        selection = {flag, zone};
        move_sel(V3(1, 0, 0));
        // Boston Mill's leaves cart from Library > Gameplay, 6 m west / 4 m north of the pillory
        int t = -1;
        for (int k = 0; k < (int)msg.templates.size() && t < 0; k++)
            if (msg.templates[k].map == "Boston_Mill" && msg.templates[k].kind == "hiding_spot") t = k;
        if (t < 0) { fail("no Boston Mill hiding spot in Library > Gameplay"); return true; }
        int placed = place_gameplay(t, ground(pillory + V3(-6, 4, 0)));
        edits.commit();
        tlog("hay cart moved 2 m; bench copied; window chase breaker taken out (" + std::to_string(nwin) + " parts); Domination "
             "point B moved 2 m" + (refused ? " (taking it out: refused)" : " (FAIL: taking it out wasn't refused)") +
             "; CTF_2 artifact + scoring zone moved 1 m; " + msg.templates[t].name + " placed");
        if (!refused) { fail("a Domination point was taken out"); return true; }
        selection = {placed};
        cam.pos = pillory + V3(-6, -46, 34);
        ms_fold_lighting = true;
        V3 d = normalize(pillory - cam.pos);
        cam.yaw = std::atan2(d.y, d.x);
        cam.pitch = std::asin(d.z);
        test_t0 = ImGui::GetTime();
        test_step = 1805;
        return true;
    }
    case 1805: {
        json r = edits.request(carry_ground);
        json want = {{"spawns", r.value("spawns", json())}, {"boundaries", r.value("boundaries", json())}};
        if (assets_loading || engine.busy() || msg.check_busy || msg.check_of != want) {
            if (ImGui::GetTime() - test_t0 > 600) fail("the asset scene / check didn't finish");
            return true;
        }
        if (!msg.errors.empty()) { fail("errors: " + msg.errors[0]); return true; }
        // the project file round trip
        project_path = test_dir + "\\ms2.ac3proj";
        save_project(false);
        json j = read_json(project_path);
        Edits back;
        back.reset(scene.get());
        std::string pr = back.apply_project(j);
        json r2 = back.request(carry_ground);
        bool same = pr.empty() && r2.value("spawns", json()) == r.value("spawns", json()) &&
                    r2.value("boundaries", json()) == r.value("boundaries", json()) && r2["places"].size() == r["places"].size() &&
                    r2["moves"].size() == r["moves"].size() && r2["removes"].size() == r["removes"].size();
        tlog(std::string(same ? "ok" : "FAIL") + ": the project file keeps the spawn, boundary and gameplay edits (" +
             std::to_string(r["moves"].size()) + " moves, " + std::to_string(r["removes"].size()) + " removes, " +
             std::to_string(r["copies"].size()) + " copies, " + std::to_string(r["places"].size()) + " placed)" + (pr.empty() ? "" : ": " + pr));
        if (!same) { fail("project round trip"); return true; }
        ImGui::SetWindowFocus("Map");
        test_wait = 10;
        test_step = 1806;
        return true;
    }
    case 1806:
        shot("1_ms2.png");
        test_step = 50;
        return true;
    }
    return false;
}
