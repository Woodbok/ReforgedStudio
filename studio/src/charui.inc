// The Characters workspace, laid out as the map editor is. Included once at the end of main.cpp.
//
//   Personas (left, top)     the game's personas, by where they come from (as Maps lists the maps)
//   Parts (left, bottom)     the outliner: the look, the colour scheme, slot > part > material, the weapons
//   Character (centre)       the 3D view: click a part to pick it, drag turns, the wheel zooms; the clip and the
//                            weapon's place on its toolbar, Test in game (F5)
//   Inspector (right)        what is picked: the look, a part (mesh, colours, materials, as a new look), a material's
//                            values, the colour scheme, the weapons
//   Changes / Test builds / Log (bottom)
//                            every edit in one list (click: pick it, x: undo it; Ctrl+Z / Ctrl+Y), Build (Ctrl+B),
//                            Test in game; the builds made, Install / Delete / the original DataPC back
//
// The edits stay in CharUI (colours, meshes, weapons, materials, new_looks, new_weapons) as the engine's requests
// want them; every change goes through chr_touch first, which keeps the undo stack.

static const ImVec4 CHR_EDITED(0.95f, 0.75f, 0.3f, 1), CHR_ERR(1, 0.45f, 0.4f, 1), CHR_NEW(0.5f, 0.85f, 0.5f, 1);

static std::string chr_lower(std::string s) {
    for (auto& ch : s) ch = (char)tolower((unsigned char)ch);
    return s;
}
// j[key] when it's an object, else {} (the engine may answer null for a key - colour_edit when a persona has no table)
static json chr_obj(const json& j, const char* key) {
    auto it = j.is_object() ? j.find(key) : j.end();
    return it != j.end() && it->is_object() ? *it : json::object();
}
static bool chr_style_slot(const std::string& s) {
    return s == "Main" || s == "Secondary" || s == "Head" || s == "Belt" || s == "Legs";
}

void App::chr_layout(ImGuiID id) {
    ImGui::DockBuilderRemoveNode(id);
    ImGui::DockBuilderAddNode(id, ImGuiDockNodeFlags_DockSpace);
    ImGui::DockBuilderSetNodeSize(id, ImGui::GetMainViewport()->WorkSize);
    ImGuiID left, rest, right, centre, bottom, ltop, lbot;
    ImGui::DockBuilderSplitNode(id, ImGuiDir_Left, 0.17f, &left, &rest);
    ImGui::DockBuilderSplitNode(rest, ImGuiDir_Right, 0.28f, &right, &centre);
    ImGui::DockBuilderSplitNode(centre, ImGuiDir_Down, 0.27f, &bottom, &centre);
    ImGui::DockBuilderSplitNode(left, ImGuiDir_Up, 0.42f, &ltop, &lbot);
    ImGui::DockBuilderDockWindow("Personas###chr_personas", ltop);
    ImGui::DockBuilderDockWindow("Parts###chr_parts", lbot);
    ImGui::DockBuilderDockWindow("Character###chr_view", centre);
    ImGui::DockBuilderDockWindow("Inspector###chr_inspector", right);
    ImGui::DockBuilderDockWindow("Changes###chr_changes", bottom);
    ImGui::DockBuilderDockWindow("Test builds###chr_builds", bottom);
    ImGui::DockBuilderDockWindow("Log###chr_log", bottom);
    ImGui::DockBuilderFinish(id);
}

void App::ui_chr_workspace() {
    // an edit previewed once the picking has stopped for a moment (colours, a tint, an undo)
    if (chr.colour_dirty > 0 && ImGui::GetTime() - chr.colour_dirty > 0.6 && !chr.loading && chr.loaded_id >= 0) {
        chr.colour_dirty = 0;
        chr_load(chr.loaded_id, chr.rows);
    }
    if (!chr.builds_asked) chr_builds_refresh();
    if (!chr.pending_mat.empty()) {                   // (a colour column clicked: its part once the materials are read)
        std::string part = chr_part_of(chr.pending_mat);
        if (!part.empty()) { chr.pending_mat.clear(); chr_pick(CharUI::PICK_PART, part); }
        else if (chr.mat_asked.empty()) chr.pending_mat.clear();
    }
    // (an error in a window names that window in the Log before the frame stops on it)
#define CHR_TRY(f) try { f; } catch (const std::exception& e) { add_log(std::string("ERROR in " #f ": ") + e.what()); throw; }
    CHR_TRY(ui_chr_personas());
    CHR_TRY(ui_chr_tree());
    CHR_TRY(ui_chr_view());
    CHR_TRY(ui_chr_inspector());
    if (chr.bottom_req == 0) ImGui::SetNextWindowFocus();
    CHR_TRY(ui_chr_changes());
#undef CHR_TRY
    if (chr.bottom_req == 1) ImGui::SetNextWindowFocus();
    ui_chr_builds();
    chr.bottom_req = -1;
    ui_log("Log###chr_log");
    ui_chr_modals();
}

// ---- the undo stack: a snapshot of the edits before each change (a drag or a typed name is one change)
void App::chr_touch(const std::string& key) {
    double now = ImGui::GetTime();
    if (key.empty() || key != chr.undo_key || now - chr.undo_t > 1.0) {
        chr.undo.push_back({chr.colours, chr.meshes, chr.weapons, chr.materials, chr.new_looks, chr.new_weapons});
        if (chr.undo.size() > 200) chr.undo.erase(chr.undo.begin());
        chr.redo.clear();
    }
    chr.undo_key = key;
    chr.undo_t = now;
}
void App::chr_restore_snap(const CharUI::Snap& s) {
    bool meshes_or_weapons = s.meshes != chr.meshes || s.weapons != chr.weapons;
    chr.colours = s.colours; chr.meshes = s.meshes; chr.weapons = s.weapons; chr.materials = s.materials;
    chr.new_looks = s.new_looks; chr.new_weapons = s.new_weapons;
    chr.undo_key.clear();
    chr.mat_dirty = ImGui::GetTime() - 1;
    chr.colour_dirty = ImGui::GetTime() - (meshes_or_weapons ? 1.0 : 0.0);      // (the view reads the persona again)
}
void App::chr_undo() {
    if (chr.undo.empty()) return;
    chr.redo.push_back({chr.colours, chr.meshes, chr.weapons, chr.materials, chr.new_looks, chr.new_weapons});
    CharUI::Snap s = chr.undo.back();
    chr.undo.pop_back();
    chr_restore_snap(s);
    status = "Characters: undone (" + std::to_string(chr.undo.size()) + " more to undo)";
}
void App::chr_redo() {
    if (chr.redo.empty()) return;
    chr.undo.push_back({chr.colours, chr.meshes, chr.weapons, chr.materials, chr.new_looks, chr.new_weapons});
    CharUI::Snap s = chr.redo.back();
    chr.redo.pop_back();
    chr_restore_snap(s);
    status = "Characters: redone";
}
void App::chr_undo_all() {
    if (!chr_edit_count() && chr.new_looks.empty() && chr.new_weapons.empty()) return;
    chr_touch("");
    chr_restore_snap({json::object(), json::object(), json::object(), json::object(), {}, {}});
    status = "Characters: every change undone (Ctrl+Z brings them back)";
}
std::string App::chr_state_key() const {
    json j = {{"c", chr.colours}, {"m", chr.meshes}, {"w", chr.weapons}, {"t", chr.materials}};
    for (auto& [p, t] : chr.new_looks) j["nl"][p] = {std::string(t.name), std::string(t.desc)};
    for (auto& [i, t] : chr.new_weapons) j["nw"][i] = {std::string(t.name), std::string(t.desc)};
    return j.dump();
}
bool App::chr_unbuilt() const {
    return chr.loaded_id >= 0 && chr_edit_count() > 0 && chr_state_key() != chr.built_key;
}
void App::chr_ask_load(int id) {
    if (chr.loading || id == chr.loaded_id) return;
    if (chr_unbuilt()) { chr.ask_switch = id; return; }
    chr_load(id);
}

// ---- picking: the Inspector shows what is picked; the view outlines it
void App::chr_pick(int kind, const std::string& part, int mat) {
    chr.pick = kind;
    chr.pick_part = part;
    if (kind == CharUI::PICK_PART) chr_mat_read(part);
    if (kind == CharUI::PICK_WEAPON && chr.weapon_info.is_null()) chr_weapons_refresh();
    if (kind != CharUI::PICK_MATERIAL) return;
    if (part != chr.mat_part && chr.mat_img.srv) { chr.mat_img.srv->Release(); chr.mat_img.srv = nullptr; chr.mat_img.meta = json(); }
    chr.mat_part = part;
    chr.mat_sel = std::max(0, mat);
    chr.mat_error.clear();
    if (chr.mat_cache.contains(part)) {
        chr.mat_list = chr.mat_cache[part];
        chr.mat_dirty = ImGui::GetTime() - 1;
    } else {
        chr.mat_list = json();
        chr_mat_read(part);
    }
}
void App::chr_mat_read(const std::string& part) {
    if (part.empty() || chr.mat_cache.contains(part) || chr.mat_asked.count(part)) return;
    chr.mat_asked.insert(part);
    engine.request("char_materials", {{"part", part}}, [this, part](bool ok, const json& r, const std::string& e) {
        chr.mat_asked.erase(part);
        if (!ok) { if (part == chr.mat_part) chr.mat_error = e; return; }
        chr.mat_cache[part] = r.value("materials", json::array());
        if (part == chr.mat_part && chr.pick == CharUI::PICK_MATERIAL) {
            chr.mat_list = chr.mat_cache[part];
            chr.mat_dirty = ImGui::GetTime() - 1;               // (the first preview now)
        }
    });
}
// the entity under a point of the view (pixels from its top left), -1 for none: the drawn triangles, posed when a clip plays
int App::chr_pick_ray(float px, float py) {
    if (!chr.scene || chr.vw < 2 || chr.vh < 2) return -1;
    V3 fwd = normalize(chr.centre - chr.eye), right = normalize(cross(fwd, V3(0, 0, 1))), up = cross(right, fwd);
    float th = std::tan(0.3f), x = 2 * px / chr.vw - 1, y = 1 - 2 * py / chr.vh;
    V3 d = normalize(fwd + right * (x * th * chr.vw / chr.vh) + up * (y * th));
    int best = -1;
    float bt = 1e30f;
    for (size_t k = 0; k < chr.scene->entities.size(); k++) {
        if (k >= chr.show.size() || !chr.show[k]) continue;
        int m = chr.scene->entities[k].mesh;
        if (m < 0) continue;
        const Mesh& me = chr.scene->meshes[m];
        const float* P = me.pos;
        auto pz = chr.posed.find(m);
        bool posed = chr.animated && pz != chr.posed.end() && pz->second.size() >= (size_t)me.nv * 3;
        if (posed) P = pz->second.data();
        else if (ray_box(chr.eye, d, me.lo, me.hi) < 0) continue;
        if (!P || !me.idx) continue;
        for (uint32_t t = 0; t < me.nt; t++) {
            const uint32_t* ix = me.idx + t * 3;
            if (ix[0] >= me.nv || ix[1] >= me.nv || ix[2] >= me.nv) continue;
            float tt = ray_tri(chr.eye, d, V3(P[ix[0] * 3], P[ix[0] * 3 + 1], P[ix[0] * 3 + 2]),
                               V3(P[ix[1] * 3], P[ix[1] * 3 + 1], P[ix[1] * 3 + 2]), V3(P[ix[2] * 3], P[ix[2] * 3 + 1], P[ix[2] * 3 + 2]));
            if (tt > 0 && tt < bt) { bt = tt; best = (int)k; }
        }
    }
    return best;
}
std::string App::chr_part_of(const std::string& mat0x) {
    auto names = chr.info.value("part_names", json::array());
    for (auto& n : names) {
        std::string part = n.get<std::string>();
        if (!chr.mat_cache.contains(part)) continue;
        for (auto& m : chr.mat_cache[part]) if ("0x" + m.value("uid", std::string()) == mat0x) return part;
    }
    return "";
}
void App::chr_pick_material_part(const std::string& mat0x) {
    std::string part = chr_part_of(mat0x);
    if (!part.empty()) { chr_pick(CharUI::PICK_PART, part); return; }
    chr.pending_mat = mat0x;
    auto names = chr.info.value("part_names", json::array()), slots = chr.info.value("part_slots", json::array());
    for (size_t k = 0; k < names.size() && k < slots.size(); k++)
        if (slots[k].get<std::string>() != "Weapon") chr_mat_read(names[k].get<std::string>());
}
void App::chr_pick_entity(int k) {
    auto names = chr.info.value("part_names", json::array());
    auto slots = chr.info.value("part_slots", json::array());
    if (k < 0 || k >= (int)names.size() || k >= (int)slots.size()) { chr_pick(CharUI::PICK_PERSONA); return; }
    if (slots[k].get<std::string>() == "Weapon") chr_pick(CharUI::PICK_WEAPON);
    else chr_pick(CharUI::PICK_PART, names[k].get<std::string>());
}

// ---- Test in game: build (unless the last build holds these edits), install, start AC3; the original DataPC back
// when it closes (the map editor's Test in game states; tg_chr says it's a character build)
void App::chr_test_in_game() {
    if (tg_state) { status = "Already testing - quit AC3 (the original DataPC comes back) or Build > Stop testing"; return; }
    if (chr.loaded_id < 0) { status = "Characters: choose a persona first"; return; }
    if (chr_edit_count() == 0) { status = "Characters: nothing to test - change a colour, a material, a weapon or a mesh first"; return; }
    if (!tg_dry && process_running(L"AC3MP.exe")) { status = "AC3 is running - close it first (the DataPC can't be swapped under a running game)"; return; }
    if (chr.building || chr.colour_dirty > 0 || chr.loading) { status = "Characters: wait for the preview / build to finish"; return; }
    tg_chr = true;
    tg_restored = false;
    bool fresh = false;
    for (auto& b : chr.builds)
        if (!chr.built_name.empty() && b.value("name", std::string()) == chr.built_name && b.value("exists", false)) fresh = true;
    if (fresh && chr_state_key() == chr.built_key) { chr_tg_install(); return; }
    tg_state = 1;
    chr_build();
    if (!chr.building) tg_state = 0;
}
void App::chr_tg_install() {
    tg_state = 2;
    std::string name = chr.built_name;
    if (tg_dry) { add_log("test in game (dry run): " + name + " would go into the game now"); tg_launch(); return; }
    chr.build_msg = "Copying " + name + "'s DataPC into the game...";
    engine.request("char_install", {{"name", name}}, [this, name](bool ok, const json& r, const std::string& e) {
        if (!ok) { tg_state = 0; status = "Test in game: install failed: " + e; chr.build_msg = status; add_log(status); return; }
        chr.build_msg = name + " is in the game (" + r.value("live", std::string()) + ")";
        add_log("installed into the game: " + r.value("live", std::string()));
        chr_builds_refresh();
        tg_launch();
    });
}
void App::chr_restore_release() {
    chr.build_msg = "Copying the original DataPC into the game...";
    engine.request("char_restore", json::object(), [this](bool ok, const json& r, const std::string& e) {
        chr.build_msg = ok ? "The game has its original DataPC again (" + r.value("original", std::string()) + ")" : "Couldn't put it back: " + e;
        status = chr.build_msg;
        add_log(status);
        chr_builds_refresh();
    });
}
std::string App::chr_auto_name() const {
    bool colours_only = chr.meshes.empty() && chr.weapons.empty() && chr.materials.empty();
    for (int k = 1; k < 1000; k++) {
        std::string nm = std::to_string(chr.loaded_id) + (colours_only ? "_colours_" : "_edit_") + std::to_string(k);
        bool taken = false;
        for (auto& b : chr.builds) if (b.value("name", std::string()) == nm) taken = true;
        if (!taken) return nm;
    }
    return std::to_string(chr.loaded_id) + "_edit";
}

bool App::chr_shortcuts() {
    ImGuiIO& io = ImGui::GetIO();
    if (io.WantTextInput) return true;
    bool ctrl = io.KeyCtrl;
    if (ImGui::IsKeyPressed(ImGuiKey_F1)) show_help = !show_help;
    if (ctrl && ImGui::IsKeyPressed(ImGuiKey_Z)) chr_undo();
    if (ctrl && ImGui::IsKeyPressed(ImGuiKey_Y)) chr_redo();
    if (ctrl && ImGui::IsKeyPressed(ImGuiKey_B) && chr_edit_count() && !chr.building) chr_build();
    if (ImGui::IsKeyPressed(ImGuiKey_F5) && !tg_state) chr_test_in_game();
    if (ctrl) return true;
    if (ImGui::IsKeyPressed(ImGuiKey_Escape)) chr_pick(CharUI::PICK_PERSONA);
    if (ImGui::IsKeyPressed(ImGuiKey_F)) chr.dist = chr.weapon_only ? 1.2f : 3.2f;
    if (ImGui::IsKeyPressed(ImGuiKey_Space) && chr.animated) chr.playing = !chr.playing;
    return true;
}

// ---- Personas: as Maps - sections by where they come from; a click opens one (asking first if edits aren't built)
void App::ui_chr_personas() {
    ImGui::Begin("Personas###chr_personas");
    input_text("##chrfind", chr.filter, "Find persona (name or id)");
    ImGui::BeginChild("##chrlist");
    if (chr.personas.empty()) ImGui::TextDisabled(chr.error.empty() ? "Reading the personas..." : chr.error.c_str());
    auto group = [](int id) {
        if (id == 79 || id == 89 || id == 92 || id == 97 || id == 98) return 1;
        if (id >= 101 && id <= 117) return 2;
        if (id >= 122 && id <= 142) return 3;
        if (id == 118 || (id >= 160 && id <= 179)) return 4;
        return id > 99 ? 5 : 0;
    };
    static const char* G[] = {"AC3", "Earlier ports (the Jester, Ezio...)", "Revelations ports", "Brotherhood ports",
                              "Black Flag ports", "Other"};
    bool unbuilt = chr_unbuilt();
    for (int g = 0; g < 6; g++) {
        bool header = false;
        for (auto& p : chr.personas) {
            int id = p.value("id", 0);
            if (group(id) != g) continue;
            std::string label = std::to_string(id) + "  " + p.value("name", std::string());
            if (!contains_ci(label, chr.filter)) continue;
            if (!header) { ImGui::SeparatorText(G[g]); header = true; }
            bool here = chr.loaded_id == id;
            if (here && unbuilt) label += "  *";
            if (chr.loading && chr.sel == id) label += "  (reading...)";
            if (ImGui::Selectable((label + "##p" + std::to_string(id)).c_str(), chr.sel == id)) chr_ask_load(id);
            if (here && unbuilt && ImGui::IsItemHovered()) ImGui::SetTooltip("Changed: %d change(s) not in a build yet (Changes, below)", chr_edit_count());
        }
    }
    ImGui::EndChild();
    ImGui::End();
}

// ---- Parts: the outliner - the look, the colour scheme, slot > part > material, the weapons
void App::ui_chr_tree() {
    ImGui::Begin("Parts###chr_parts");
    if (!chr.scene) { ImGui::TextDisabled(chr.loading ? "Reading the persona..." : "Choose a persona above."); ImGui::End(); return; }
    const json& in = chr.info;
    auto names = in.value("part_names", json::array());
    auto slots = in.value("part_slots", json::array());
    ImGui::BeginChild("##chrtree");
    std::string root = std::to_string(chr.loaded_id) + "  " + persona_name(chr.loaded_id) + "  (its look)";
    if (ImGui::Selectable(root.c_str(), chr.pick == CharUI::PICK_PERSONA)) chr_pick(CharUI::PICK_PERSONA);
    if (ImGui::IsItemHovered()) ImGui::SetTooltip("The look: a row per slot, the costume, the weather, the colour scheme");
    if (in.value("colour_edit", json()).is_object()) {
        int ncol = 0;
        for (auto& [_s, regs] : chr.colours.items()) ncol += (int)regs.size();
        std::string cl = "Colour scheme " + std::to_string(chr_obj(in, "rows").value("Colours", 0) + 1) +
                         (ncol ? "  (" + std::to_string(ncol) + " edited)" : "");
        if (ncol) ImGui::PushStyleColor(ImGuiCol_Text, CHR_EDITED);
        if (ImGui::Selectable(cl.c_str(), chr.pick == CharUI::PICK_COLOURS)) chr_pick(CharUI::PICK_COLOURS);
        if (ncol) ImGui::PopStyleColor();
    }
    // the slots in the order the parts come
    std::vector<std::string> order;
    for (auto& s : slots) if (std::find(order.begin(), order.end(), s.get<std::string>()) == order.end()) order.push_back(s.get<std::string>());
    int hov = -1;
    for (auto& slot : order) {
        if (!ImGui::TreeNodeEx(slot.c_str(), ImGuiTreeNodeFlags_DefaultOpen | ImGuiTreeNodeFlags_SpanAvailWidth)) continue;
        for (size_t k = 0; k < names.size() && k < slots.size() && k < chr.show.size(); k++) {
            if (slots[k].get<std::string>() != slot) continue;
            std::string nm = names[k].get<std::string>();
            bool weapon = slot == "Weapon";
            ImGui::PushID((int)k);
            bool on = chr.show[k] != 0;
            ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(1, 1));
            if (ImGui::Checkbox("##on", &on)) chr.show[k] = on;
            ImGui::PopStyleVar();
            if (ImGui::IsItemHovered()) ImGui::SetTooltip("Shown in the view");
            ImGui::SameLine();
            bool edited = weapon ? !chr.weapons.empty()
                                 : chr.meshes.contains(nm) || (chr.materials.contains(nm) && !chr.materials[nm].empty());
            bool picked = weapon ? chr.pick == CharUI::PICK_WEAPON : (chr.pick == CharUI::PICK_PART && chr.pick_part == nm);
            ImGuiTreeNodeFlags f = ImGuiTreeNodeFlags_OpenOnArrow | ImGuiTreeNodeFlags_SpanAvailWidth;
            if (weapon) f |= ImGuiTreeNodeFlags_Leaf | ImGuiTreeNodeFlags_NoTreePushOnOpen;
            if (picked) f |= ImGuiTreeNodeFlags_Selected;
            if (edited) ImGui::PushStyleColor(ImGuiCol_Text, CHR_EDITED);
            std::string label = nm + (edited ? "  *" : "") + (chr.new_looks.count(nm) ? "  (new look)" : "");
            bool open = ImGui::TreeNodeEx("##part", f, "%s", label.c_str());
            if (edited) ImGui::PopStyleColor();
            if (ImGui::IsItemHovered()) hov = (int)k;
            if (ImGui::IsItemClicked() && !ImGui::IsItemToggledOpen()) chr_pick_entity((int)k);
            if (open && !weapon) {
                chr_mat_read(nm);
                if (!chr.mat_cache.contains(nm)) ImGui::TextDisabled("reading its materials...");
                else {
                    int i = 0;
                    for (auto& m : chr.mat_cache[nm]) {
                        std::string uid = m.value("uid", std::string());
                        bool med = chr.materials.contains(nm) && chr.materials[nm].contains(uid);
                        bool mp = chr.pick == CharUI::PICK_MATERIAL && chr.mat_part == nm && chr.mat_sel == i;
                        if (med) ImGui::PushStyleColor(ImGuiCol_Text, CHR_EDITED);
                        ImGui::TreeNodeEx((void*)(intptr_t)(i + 1), ImGuiTreeNodeFlags_Leaf | ImGuiTreeNodeFlags_NoTreePushOnOpen |
                                          ImGuiTreeNodeFlags_SpanAvailWidth | (mp ? ImGuiTreeNodeFlags_Selected : 0),
                                          "material  %s%s", m.value("name", std::string()).c_str(), med ? "  *" : "");
                        if (med) ImGui::PopStyleColor();
                        if (ImGui::IsItemClicked()) chr_pick(CharUI::PICK_MATERIAL, nm, i);
                        i++;
                    }
                }
                ImGui::TreePop();
            }
            ImGui::PopID();
        }
        ImGui::TreePop();
    }
    if (std::find(order.begin(), order.end(), "Weapon") == order.end())
        if (ImGui::Selectable("Weapons (its items)", chr.pick == CharUI::PICK_WEAPON)) chr_pick(CharUI::PICK_WEAPON);
    chr.hover = hov;
    ImGui::EndChild();
    ImGui::End();
}

// ---- the 3D view and its toolbar
void App::ui_chr_view() {
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(2, 2));
    ImGui::Begin("Character###chr_view", nullptr, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    ImGui::PopStyleVar();
    auto tool = [&](const char* label, bool on, const char* tip) {
        if (on) ImGui::PushStyleColor(ImGuiCol_Button, ImGui::GetStyleColorVec4(ImGuiCol_ButtonActive));
        bool r = ImGui::Button(label);
        if (on) ImGui::PopStyleColor();
        if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled)) ImGui::SetTooltip("%s", tip);
        ImGui::SameLine();
        return r;
    };
    bool have = chr.scene && renderer.scene_ready(SLOT_CHAR);
    ImGui::BeginDisabled(!have);
    // the clip
    if (!chr.animated) {
        if (tool(chr.anim_loading ? "Reading the clip..." : "Play idle", false,
                 "Play the persona's menu idle (it's drawn in its bind pose - arms out - until then)") && !chr.anim_loading) chr_load_anim();
    } else {
        if (tool(chr.playing ? "Pause" : "Play", chr.playing, "Play / pause the clip (Space)")) chr.playing = !chr.playing;
        int fr = chr.nframes ? (int)(chr.t * chr.fps) % chr.nframes : 0;
        ImGui::SetNextItemWidth(130);
        if (ImGui::SliderInt("##frame", &fr, 0, std::max(0, chr.nframes - 1), "frame %d")) { chr.t = fr / chr.fps; chr.playing = false; }
        ImGui::SameLine();
        ImGui::SetNextItemWidth(260);
        if (ImGui::BeginCombo("##clip", chr.clip.c_str(), ImGuiComboFlags_HeightLarge)) {
            static char cf[64] = "";
            ImGui::SetNextItemWidth(-1);
            if (ImGui::IsWindowAppearing()) ImGui::SetKeyboardFocusHere();
            ImGui::InputTextWithHint("##clipfind", "find a clip (idle, breaker, weapon...)", cf, sizeof cf);
            for (auto& c : chr.clips) {
                std::string n = c.get<std::string>();
                if (!contains_ci(n, cf)) continue;
                if (ImGui::Selectable(n.c_str(), n == chr.clip) && n != chr.clip && !chr.anim_loading) chr_load_anim(n);
            }
            ImGui::EndCombo();
        }
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Any menu clip (%d): idles, breakers, team-select grips...", (int)chr.clips.size());
        ImGui::SameLine();
        if (tool("Bind pose", false, "Stop the clip: the parts as stored (arms out)")) {
            chr.animated = false;
            std::string err;                     // (the scene's own vertices back: upload again)
            renderer.release_scene(SLOT_CHAR);
            renderer.upload(*chr.scene, err, nullptr, SLOT_CHAR);
        }
    }
    ImGui::TextDisabled("|");
    ImGui::SameLine();
    // the weapon's place, its pose, the weapon alone
    {
        static const char* AT[] = {"Weapon in the hand", "Weapon in the holster", "Weapon beside the body"};
        int at = chr.rows.is_object() ? chr.rows.value("WeaponAt", 0) : 0;
        ImGui::SetNextItemWidth(190);
        if (ImGui::Combo("##wat", &at, AT, 3) && !chr.loading) {
            json rows = chr.rows.is_object() ? chr.rows : json::object();
            rows["WeaponAt"] = at;
            chr_load(chr.loaded_id, rows);
        }
        if (ImGui::IsItemHovered())
            ImGui::SetTooltip("In the hand: on the right hand's prop bone (the bone every clip moves the weapon with).\n"
                              "In the holster: on the class's holster rig (WeaponRef). Beside: upright, for a look.");
        ImGui::SameLine();
        if (tool("Weapon pose", chr.clip.find("Weapon_Selection") != std::string::npos,
                 "Play the customisation menu's weapon-selection pose for this weapon's class") && !chr.anim_loading) chr_load_anim("@weapon");
        if (tool("Only the weapon", chr.weapon_only, "Hide the body: the camera frames the weapon")) { chr.weapon_only = !chr.weapon_only; chr_weapon_only(); }
    }
    ImGui::EndDisabled();
    ImGui::TextDisabled("|");
    ImGui::SameLine();
    {
        const char* tl = tg_chr && tg_state == 1 ? "Testing: building..." : tg_chr && tg_state == 2 ? "Testing: installing..."
                       : tg_chr && tg_state == 3 ? "Testing: starting AC3..." : tg_chr && tg_state == 4 ? "Testing (AC3 running)" : "Test in game";
        ImGui::BeginDisabled(!have || (tg_state && !tg_chr));
        if (tool(tl, tg_state != 0, "Build these changes (unless the last build has them), install the build's DataPC, start AC3 (F5);\n"
                                    "when you quit it the original DataPC goes back (Build menu: on / off)")) { if (!tg_state) chr_test_in_game(); }
        ImGui::EndDisabled();
    }
    ImGui::NewLine();

    ImVec2 avail = ImGui::GetContentRegionAvail();
    ImVec2 p0 = ImGui::GetCursorScreenPos();
    if (!have) {
        ImGui::Dummy(avail);
        const char* msg = chr.loading ? "Reading the persona (a few seconds)..." : !chr.error.empty() ? chr.error.c_str()
                        : "Choose a persona in Personas (left).";
        ImVec2 ts = ImGui::CalcTextSize(msg);
        ImGui::GetWindowDrawList()->AddText(ImVec2(p0.x + (avail.x - ts.x) * 0.5f, p0.y + avail.y * 0.45f),
                                            ImGui::GetColorU32(chr.error.empty() ? ImGuiCol_TextDisabled : ImGuiCol_Text), msg);
        ImGui::End();
        return;
    }
    const json& in = chr.info;
    auto names = in.value("part_names", json::array());
    auto slots = in.value("part_slots", json::array());
    // the picked part (or the weapon) outlined; the visible parts' box: what the camera turns round
    V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
    std::vector<DrawItem> items;
    for (size_t k = 0; k < chr.scene->entities.size(); k++) {
        if (!chr.show[k]) continue;
        int m = chr.scene->entities[k].mesh;
        if (m < 0) continue;
        lo = vmin(lo, chr.scene->meshes[m].lo);
        hi = vmax(hi, chr.scene->meshes[m].hi);
        bool picked = false;
        if (k < names.size() && k < slots.size()) {
            std::string nm = names[k].get<std::string>(), sl = slots[k].get<std::string>();
            picked = (sl == "Weapon" && chr.pick == CharUI::PICK_WEAPON) ||
                     (sl != "Weapon" && (chr.pick == CharUI::PICK_PART || chr.pick == CharUI::PICK_MATERIAL) && chr.pick_part == nm);
            if (chr.pick == CharUI::PICK_MATERIAL) picked = sl != "Weapon" && chr.mat_part == nm;
        }
        items.push_back({m, M4::identity(), {0, 0, 0, 0}, picked, false});
    }
    if (items.empty()) { lo = V3(-0.5f, -0.5f, 0); hi = V3(0.5f, 0.5f, 1.8f); }
    if (chr.animated) {                         // (posed: the skeleton's frame, feet at 0 - the bind-pose box doesn't apply)
        if (chr.playing) chr.t += ImGui::GetIO().DeltaTime;
        chr_pose();
        if (!chr.weapon_only) { lo = V3(-0.6f, -0.6f, 0); hi = V3(0.6f, 0.6f, 1.85f); }
    }
    V3 c = (lo + hi) * 0.5f;
    float w = std::max(16.0f, avail.x), h = std::max(16.0f, avail.y);
    V3 eye = c + V3(std::cos(chr.yaw) * std::cos(chr.pitch), std::sin(chr.yaw) * std::cos(chr.pitch), std::sin(chr.pitch)) * chr.dist;
    chr.eye = eye; chr.centre = c; chr.vw = w; chr.vh = h;
    renderer.resize((int)w, (int)h, TARGET_CHAR);
    renderer.begin(look_at(eye, c, V3(0, 0, 1)), perspective(0.6f, w / h, 0.02f, 100.0f), eye, TARGET_CHAR);
    renderer.draw_scene(*chr.scene, items, false, {}, {}, alpha_clip, SLOT_CHAR);
    renderer.end();
    ImGui::Image((ImTextureID)(intptr_t)renderer.output(TARGET_CHAR), ImVec2(w, h));
    bool hovered = ImGui::IsItemHovered();
    ImGuiIO& io = ImGui::GetIO();
    // left click picks (a press and release without a drag), a drag turns, the wheel zooms
    if (hovered && ImGui::IsMouseClicked(ImGuiMouseButton_Left)) chr.press_on_view = true;
    if (chr.press_on_view && ImGui::IsMouseDragging(ImGuiMouseButton_Left)) {
        chr.yaw -= io.MouseDelta.x * 0.01f;
        chr.pitch = std::clamp(chr.pitch + io.MouseDelta.y * 0.01f, -1.2f, 1.4f);
    }
    if (chr.press_on_view && ImGui::IsMouseReleased(ImGuiMouseButton_Left)) {
        ImVec2 dd = ImGui::GetMouseDragDelta(ImGuiMouseButton_Left, 0);
        if (hovered && dd.x * dd.x + dd.y * dd.y < 16) chr_pick_entity(chr_pick_ray(io.MousePos.x - p0.x, io.MousePos.y - p0.y));
        chr.press_on_view = false;
    }
    if (hovered && io.MouseWheel != 0) chr.dist = std::clamp(chr.dist * (io.MouseWheel > 0 ? 0.85f : 1.18f), 0.4f, 12.0f);
    if (hovered && !ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
        int k = chr_pick_ray(io.MousePos.x - p0.x, io.MousePos.y - p0.y);
        if (k >= 0 && k < (int)names.size())
            ImGui::SetTooltip("%s  %s\nclick to pick it", k < (int)slots.size() ? slots[k].get<std::string>().c_str() : "", names[k].get<std::string>().c_str());
    }
    // the corner: the persona and what is picked, as the map viewport's corner says the selection's level
    ImDrawList* dl = ImGui::GetWindowDrawList();
    std::string what = std::to_string(chr.loaded_id) + "  " + persona_name(chr.loaded_id);
    if (chr.pick == CharUI::PICK_PART) what += "  >  " + chr.pick_part;
    if (chr.pick == CharUI::PICK_MATERIAL && chr.mat_list.is_array() && chr.mat_sel < (int)chr.mat_list.size())
        what += "  >  " + chr.mat_part + "  >  " + chr.mat_list[chr.mat_sel].value("name", std::string());
    if (chr.pick == CharUI::PICK_WEAPON) what += "  >  the weapons";
    if (chr.pick == CharUI::PICK_COLOURS) what += "  >  the colour scheme";
    dl->AddText(ImVec2(p0.x + 8, p0.y + 6), IM_COL32(235, 235, 235, 230), what.c_str());
    char sub[200];
    snprintf(sub, sizeof sub, "%d triangles, %d texture(s)%s", in.value("triangles", 0), in.value("textures", 0),
             chr.animated ? (", " + chr.clip).c_str() : ", bind pose");
    dl->AddText(ImVec2(p0.x + 8, p0.y + 6 + ImGui::GetTextLineHeight()), IM_COL32(170, 170, 170, 200), sub);
    if (chr.loading) dl->AddText(ImVec2(p0.x + 8, p0.y + 6 + 2 * ImGui::GetTextLineHeight()), IM_COL32(242, 153, 64, 255), "reading...");
    const char* hint = "click a part to pick it  -  drag to turn  -  wheel to zoom  -  Esc: the whole look";
    dl->AddText(ImVec2(p0.x + 8, p0.y + h - ImGui::GetTextLineHeight() - 6), IM_COL32(170, 170, 170, 170), hint);
    ImGui::End();
}

// ---- the Inspector: what is picked
void App::ui_chr_inspector() {
    ImGui::Begin("Inspector###chr_inspector");
    if (!chr.scene) { ImGui::TextDisabled("Choose a persona (Personas)."); ImGui::End(); return; }
    const json& in = chr.info;
    switch (chr.pick) {
    case CharUI::PICK_PART: ui_chr_part(in); break;
    case CharUI::PICK_MATERIAL: ui_chr_material(in); break;
    case CharUI::PICK_WEAPON: ui_chr_weapons(in); break;
    case CharUI::PICK_COLOURS: {
        ImGui::TextColored(ImVec4(0.95f, 0.55f, 0.2f, 1), "COLOUR SCHEME");
        json want = chr_obj(in, "rows");
        if (chr_row_combo(in, "Colours", want, -1) && !chr.loading) chr_load(chr.loaded_id, want);
        ui_chr_colours(in);
        break;
    }
    default: ui_chr_look(in); break;
    }
    ImGui::End();
}
// one slot's row picker; true when another row was chosen (want holds it)
bool App::chr_row_combo(const json& in, const std::string& slot, json& want, float width) {
    json tables = chr_obj(in, "tables");
    if (!tables.contains(slot)) return false;
    auto rows = tables[slot];
    int cur = chr_obj(in, "rows").value(slot, 0);
    std::string shown = "row " + std::to_string(cur);
    auto label_of = [&](const json& rw) {
        int k = rw.value("row", 0);
        if (slot == "Weather") return std::to_string(k) + (k == 0 ? ": ordinary" : ": winter") + " - " + rw.value("label", std::string());
        if (slot == "Colours") return "scheme " + std::to_string(k + 1);
        return std::to_string(k) + ": " + rw.value("label", std::string());
    };
    for (auto& rw : rows) if (rw.value("row", -1) == cur) shown = label_of(rw);
    bool changed = false;
    ImGui::SetNextItemWidth(width);
    if (ImGui::BeginCombo(("##look" + slot).c_str(), shown.c_str(), ImGuiComboFlags_HeightLarge)) {
        for (auto& rw : rows) {
            int k = rw.value("row", 0);
            if (ImGui::Selectable(label_of(rw).c_str(), k == cur) && k != cur) { want[slot] = k; changed = true; }
        }
        ImGui::EndCombo();
    }
    if (slot == "Colours" && ImGui::IsItemHovered())
        ImGui::SetTooltip("The colour scheme (the kit's Custom_color1 table): the game tints a garment by it\n"
                          "where its RVBMask's alpha says (diffuse x lerp(1, colour, mask alpha)) - baked here.\n"
                          "Tinted: %d texture(s)", (int)in.value("tinted", json::array()).size());
    if (slot == "Costume" && ImGui::IsItemHovered())
        ImGui::SetTooltip(in.value("costume_adds", false)
            ? "This persona's costumes add to the style (the exe flags it: the AC4 ports).\nRow 0 is its base outfit."
            : "A costume replaces the style in game: the style slots are left out while one is on.\nRow 0 = none.");
    return changed;
}
void App::ui_chr_look(const json& in) {
    ImGui::TextColored(ImVec4(0.95f, 0.55f, 0.2f, 1), "%d  %s", chr.loaded_id, persona_name(chr.loaded_id).c_str());
    ImGui::TextDisabled("%d parts, %d triangles, %d texture(s) - read in %.1f s from %s", (int)in.value("part_names", json::array()).size(),
                        in.value("triangles", 0), in.value("textures", 0), in.value("seconds", 0.0), in.value("build", std::string()).c_str());
    ImGui::SeparatorText("Look");
    json want = chr_obj(in, "rows");
    bool changed = false;
    static const char* ORDER[] = {"Main", "Secondary", "Head", "Belt", "Legs", "Costume", "Weather", "Colours"};
    if (ImGui::BeginTable("##look", 2, ImGuiTableFlags_SizingStretchProp)) {
        ImGui::TableSetupColumn("slot", ImGuiTableColumnFlags_WidthFixed, 90);
        ImGui::TableSetupColumn("row", ImGuiTableColumnFlags_WidthStretch);
        for (const char* slot : ORDER) {
            if (!chr_obj(in, "tables").contains(slot)) continue;
            ImGui::TableNextRow();
            ImGui::TableNextColumn();
            ImGui::AlignTextToFramePadding();
            ImGui::TextUnformatted(std::string(slot) == "Colours" ? "Colour scheme" : slot);
            ImGui::TableNextColumn();
            changed |= chr_row_combo(in, slot, want, -1);
        }
        ImGui::EndTable();
    }
    if (changed && !chr.loading) chr_load(chr.loaded_id, want);
    ImGui::SeparatorText("Edit");
    ImGui::PushTextWrapPos(0);
    ImGui::TextDisabled("Click a part in the view or in Parts: its mesh (Blender), materials and colours. Every change goes "
                        "into Changes (below); Build or Test in game puts them into a test DataPC.");
    ImGui::PopTextWrapPos();
    if (in.value("colour_edit", json()).is_object() && ImGui::Button("The colour scheme's colours...")) chr_pick(CharUI::PICK_COLOURS);
    ImGui::SameLine();
    if (ImGui::Button("The weapons...")) chr_pick(CharUI::PICK_WEAPON);
    if (in.value("face", json()).is_string()) ImGui::TextDisabled("Face maps: %s", in["face"].get<std::string>().c_str());
    for (auto& p : in.value("mesh_problems", json::array()))
        ImGui::TextColored(CHR_ERR, "Not imported: %s", p.get<std::string>().c_str());
}

// ---- a part: its look row, colours, materials, mesh (Blender), as a new look
void App::ui_chr_part(const json& in) {
    const std::string part = chr.pick_part;
    auto names = in.value("part_names", json::array());
    auto slots = in.value("part_slots", json::array());
    int k = -1;
    for (size_t i = 0; i < names.size() && i < slots.size(); i++) if (names[i].get<std::string>() == part) k = (int)i;
    if (k < 0) {
        ImGui::TextDisabled("%s isn't in the look shown now.", part.c_str());
        if (ImGui::Button("Back to the look")) chr_pick(CharUI::PICK_PERSONA);
        return;
    }
    std::string slot = slots[k].get<std::string>();
    ImGui::TextColored(ImVec4(0.95f, 0.55f, 0.2f, 1), "%s", part.c_str());
    ImGui::TextDisabled("%s part of %d %s", slot.c_str(), chr.loaded_id, persona_name(chr.loaded_id).c_str());
    bool on = k < (int)chr.show.size() && chr.show[k];
    if (ImGui::Checkbox("Shown in the view", &on) && k < (int)chr.show.size()) chr.show[k] = on;
    std::string tex;
    for (auto& p : in.value("parts", json::array()))
        if (p.value("file", std::string()) == part)
            for (auto& t : p.value("textures", json::array())) tex += (tex.empty() ? "" : ", ") + t.get<std::string>();
    if (!tex.empty()) { ImGui::PushTextWrapPos(0); ImGui::TextDisabled("Textures: %s", tex.c_str()); ImGui::PopTextWrapPos(); }
    if (chr.exports.contains(part)) {
        auto worn = chr.exports[part].value("worn_by", json::array());
        std::string wb;
        for (auto& x : worn) if (x.get<int>() != chr.loaded_id) wb += (wb.empty() ? "" : ", ") + std::to_string(x.get<int>()) + " " + persona_name(x.get<int>());
        if (!wb.empty()) ImGui::TextColored(ImVec4(1, 0.6f, 0.3f, 1), "Also worn by %s: a change here changes theirs too", wb.c_str());
    }
    if (chr_obj(in, "tables").contains(slot)) {
        ImGui::SeparatorText(("Look (" + slot + " row)").c_str());
        json want = chr_obj(in, "rows");
        if (chr_row_combo(in, slot, want, -1) && !chr.loading) chr_load(chr.loaded_id, want);
    }
    // its colours in the scheme (the columns its file holds)
    if (in.value("colour_edit", json()).is_object()) {
        ImGui::SeparatorText(("Colours (scheme " + std::to_string(chr_obj(in, "rows").value("Colours", 0) + 1) + ")").c_str());
        ui_chr_colours(in, part);
    }
    // its materials
    ImGui::SeparatorText("Materials");
    chr_mat_read(part);
    if (!chr.mat_cache.contains(part)) ImGui::TextDisabled("Reading its materials...");
    else {
        int i = 0;
        for (auto& m : chr.mat_cache[part]) {
            std::string uid = m.value("uid", std::string());
            int ned = chr.materials.contains(part) && chr.materials[part].contains(uid) ? (int)chr.materials[part][uid].size() : 0;
            std::string lb = m.value("name", std::string()) + "   (" + std::to_string(m.value("props", json::array()).size()) + " values" +
                             (ned ? ", " + std::to_string(ned) + " edited" : "") + ")##m" + std::to_string(i);
            if (ned) ImGui::PushStyleColor(ImGuiCol_Text, CHR_EDITED);
            if (ImGui::Selectable(lb.c_str())) chr_pick(CharUI::PICK_MATERIAL, part, i);
            if (ned) ImGui::PopStyleColor();
            if (ImGui::IsItemHovered()) ImGui::SetTooltip("Edit its values (specular, fresnel, tint, saturation, normal strength...) with a lit preview");
            i++;
        }
    }
    // the mesh
    ImGui::SeparatorText("Mesh");
    bool busy = chr.mesh_busy == part;
    ImGui::BeginDisabled(!chr.mesh_busy.empty() || chr.loading);
    if (ImGui::Button(busy ? "Exporting..." : "Edit in Blender")) chr_export(part, true);
    ImGui::EndDisabled();
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
        ImGui::SetTooltip("Export this part's mesh (LOD0: its bones, weights, UVs, colour map) as glTF and open it in Blender.\n"
                          "Edit it, then File > Export > glTF 2.0 (.glb) - keep Skinning on - as <part>_edit.glb beside it,\n"
                          "and press 'Use the edit' here. Bones and materials stay the part's own; up to 4 weights a vertex.");
    ImGui::SameLine();
    if (ImGui::Button("Import a .glb...")) {
        std::string folder = chr.exports.contains(part) ? chr.exports[part].value("folder", std::string()) : "";
        std::string p = file_dialog(hwnd, false, "", L"glTF binary (*.glb)\0*.glb\0All files\0*.*\0", L"glb", folder);
        if (!p.empty()) chr_use_mesh(part, p);
    }
    if (chr.exports.contains(part) && chr.exports[part]["edited"].is_string() && !chr.meshes.contains(part)) {
        ImGui::SameLine();
        if (ImGui::Button("Use the edit")) chr_use_mesh(part, chr.exports[part]["edited"].get<std::string>());
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s (%s)", chr.exports[part]["edited"].get<std::string>().c_str(),
                                                      chr.exports[part].value("edited_time", std::string()).c_str());
    }
    if (chr.meshes.contains(part)) {
        std::string glb = chr.meshes[part].value("glb", std::string());
        ImGui::TextColored(CHR_EDITED, "Drawn from %s", base_name(glb).c_str());
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s", glb.c_str());
        if (ImGui::Button("Reload")) chr_use_mesh(part, glb);
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Read the .glb again (after another export from Blender)");
        ImGui::SameLine();
        if (ImGui::Button("Back to stock")) chr_use_mesh(part, "");
        ImGui::SameLine();
        bool all = chr.meshes[part].value("lods", std::string("all")) == "all";
        if (ImGui::Checkbox("every LOD", &all)) {
            chr_touch("");
            chr.meshes[part]["lods"] = all ? "all" : "lod0";
            chr_load(chr.loaded_id, chr.rows);
        }
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("On: the far LODs get the edit too (else the old shape pops back at distance).\n"
                                                      "Off: LOD0 only - the stock far LODs stay.");
    }
    if (!chr.build_msg.empty() && busy) ImGui::TextDisabled("Exporting %s...", part.c_str());
    // an edited style part can go in as a NEW look
    bool edited_part = chr.meshes.contains(part) || (chr.materials.contains(part) && !chr.materials[part].empty());
    if (chr_style_slot(slot)) {
        ImGui::SeparatorText("As a new look");
        if (!edited_part) ImGui::TextDisabled("Edit its mesh or materials first: the edit can then go in next to the stock look.");
        else {
            bool as_new = chr.new_looks.count(part) > 0;
            if (ImGui::Checkbox("Add it as a new look (keep the stock one)", &as_new)) {
                chr_touch("");
                if (as_new) {
                    auto& t = chr.new_looks[part];
                    std::string def = "NEW " + slot;
                    for (auto& ch : def) ch = (char)toupper((unsigned char)ch);
                    strncpy_s(t.name, def.c_str(), sizeof t.name - 1);
                    strncpy_s(t.desc, "Made in Reforged Studio.", sizeof t.desc - 1);
                } else chr.new_looks.erase(part);
            }
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip("On: the build adds this edit as a NEW %s look - its own table row (a copy of the row shown, with\n"
                                  "this part's edited copy), menu item, unlock index, name and icon - and leaves the stock look as it is.\n"
                                  "Off: the edit replaces the stock part.", slot.c_str());
            if (as_new) {
                auto& t = chr.new_looks[part];
                ImGui::SetNextItemWidth(-90);
                char before[64];
                memcpy(before, t.name, sizeof before);
                if (ImGui::InputText("name##nl", t.name, sizeof t.name)) {
                    char now[64];
                    memcpy(now, t.name, sizeof now);
                    memcpy(t.name, before, sizeof before);           // (the snapshot holds the text before the typing)
                    chr_touch("nlname:" + part);
                    memcpy(chr.new_looks[part].name, now, sizeof now);
                }
                auto& t2 = chr.new_looks[part];
                char dbefore[192];
                memcpy(dbefore, t2.desc, sizeof dbefore);
                ImGui::SetNextItemWidth(-90);
                if (ImGui::InputText("description##nl", t2.desc, sizeof t2.desc)) {
                    char now[192];
                    memcpy(now, t2.desc, sizeof now);
                    memcpy(t2.desc, dbefore, sizeof dbefore);
                    chr_touch("nldesc:" + part);
                    memcpy(chr.new_looks[part].desc, now, sizeof now);
                }
            }
        }
    }
}

// ---- the colour scheme's columns; only_part: just the columns that part's file holds
void App::ui_chr_colours(const json& in, const std::string& only_part) {
    const json ce = in.value("colour_edit", json());
    if (!ce.is_object()) { ImGui::TextDisabled("This persona has no colour table (kit +132)."); return; }
    int sch = ce.value("scheme", 0);
    std::string sk = std::to_string(sch);
    std::map<int, std::array<float, 3>> cell;
    for (auto& c : ce.value("cells", json::array())) {
        auto v = c.value("rgb", json::array());
        if (v.size() >= 3) cell[c.value("region", -1)] = {v[0].get<float>(), v[1].get<float>(), v[2].get<float>()};
    }
    if (chr.colours.contains(sk))
        for (auto& [rk, v] : chr.colours[sk].items()) cell[std::stoi(rk)] = {v[0].get<float>(), v[1].get<float>(), v[2].get<float>()};
    auto set = [&](int region, const float* rgb, const std::string& key) {
        chr_touch(key);
        chr.colours[sk][std::to_string(region)] = {rgb[0], rgb[1], rgb[2]};
        chr.colour_dirty = ImGui::GetTime();
    };
    auto cols = ce.value("columns", json::array());
    if (only_part.empty()) {
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("%s: a column per material it colours, a row per scheme the menu offers. The game's schemes give every "
                            "garment one colour; here each can have its own. The view shows the garment colours (baked through the "
                            "mask's alpha); metal colours are built but not drawn here.", ce.value("table", std::string()).c_str());
        ImGui::PopTextWrapPos();
        float all[3] = {0.5f, 0.5f, 0.5f};
        for (auto& c : cols)
            if (c.value("what", std::string()) == "colour" && cell.count(c.value("region", -1))) {
                auto& v = cell[c.value("region", -1)];
                all[0] = v[0]; all[1] = v[1]; all[2] = v[2];
                break;
            }
        if (ImGui::ColorEdit3("Every garment colour", all, ImGuiColorEditFlags_Float))
            for (auto& c : cols)
                if (c.value("what", std::string()) == "colour" && cell.count(c.value("region", -1))) set(c.value("region", -1), all, "colour:all");
        ImGui::Checkbox("Show the unused columns", &chr.show_unused);
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Columns naming a material no file of the build defines (left over in Ubisoft's data).");
        if (chr.colours.contains(sk)) {
            ImGui::SameLine();
            if (ImGui::SmallButton("Undo this scheme's colours")) { chr_touch(""); chr.colours.erase(sk); chr.colour_dirty = ImGui::GetTime(); }
        }
        if (chr.colour_dirty > 0 || chr.loading) { ImGui::SameLine(); ImGui::TextDisabled("(previewing...)"); }
        ImGui::Separator();
    }
    std::set<std::string> part_mats;                  // (a part's own columns: those naming one of its materials)
    if (!only_part.empty()) {
        chr_mat_read(only_part);
        if (!chr.mat_cache.contains(only_part)) { ImGui::TextDisabled("Reading its materials..."); return; }
        for (auto& m : chr.mat_cache[only_part]) part_mats.insert("0x" + m.value("uid", std::string()));
    }
    int k = 0, shown = 0;
    for (auto& c : cols) {
        int region = c.value("region", -1);
        std::string name = c.value("name", std::string()), what = c.value("what", std::string());
        bool unused = name.empty();
        if (!only_part.empty() && !part_mats.count(c.value("material", std::string()))) continue;
        if (unused && !chr.show_unused) continue;
        ImGui::PushID(k++);
        shown++;
        auto it = cell.find(region);
        if (it == cell.end()) {
            ImGui::TextDisabled("(not in this scheme)  %s - %s", what.c_str(), unused ? "unused" : name.c_str());
            ImGui::PopID();
            continue;
        }
        float rgb[3] = {it->second[0], it->second[1], it->second[2]};
        bool edited = chr.colours.contains(sk) && chr.colours[sk].contains(std::to_string(region));
        if (ImGui::ColorEdit3("##c", rgb, ImGuiColorEditFlags_NoInputs | ImGuiColorEditFlags_Float))
            set(region, rgb, "colour:" + sk + ":" + std::to_string(region));
        ImGui::SameLine();
        ImGui::Text("%s", what.c_str());
        if (!only_part.empty()) { ImGui::SameLine(); ImGui::TextDisabled("%s", name.c_str()); }
        if (only_part.empty()) {
            ImGui::SameLine();
            if (unused) ImGui::TextDisabled("(no such material in the build: unused)");
            else {
                bool drawn = c.value("drawn", false);
                if (!drawn) ImGui::PushStyleColor(ImGuiCol_Text, ImGui::GetStyleColorVec4(ImGuiCol_TextDisabled));
                if (ImGui::Selectable(name.c_str(), false, 0, ImVec2(ImGui::CalcTextSize(name.c_str()).x, 0)) && drawn)
                    chr_pick_material_part(c.value("material", std::string()));
                if (!drawn) ImGui::PopStyleColor();
                if (ImGui::IsItemHovered()) ImGui::SetTooltip(drawn ? "Pick this part" : "Another look's part (not drawn now)");
            }
        }
        if (edited) {
            ImGui::SameLine();
            ImGui::TextColored(CHR_EDITED, "edited");
            ImGui::SameLine();
            if (ImGui::SmallButton("undo")) {
                chr_touch("");
                chr.colours[sk].erase(std::to_string(region));
                if (chr.colours[sk].empty()) chr.colours.erase(sk);
                chr.colour_dirty = ImGui::GetTime();
            }
        }
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("region %d, material %s", region, c.value("material", std::string()).c_str());
        ImGui::PopID();
    }
    if (!only_part.empty() && !shown) ImGui::TextDisabled("The colour scheme doesn't colour this part.");
}

// ---- a material's values, with the lit preview
void App::ui_chr_material(const json& in) {
    if (chr.mat_part.empty()) { chr_pick(CharUI::PICK_PERSONA); return; }
    if (chr.mat_dirty > 0 && ImGui::GetTime() - chr.mat_dirty > 0.15) chr_mat_preview();
    if (ImGui::SmallButton(("< " + chr.mat_part).c_str())) { chr_pick(CharUI::PICK_PART, chr.mat_part); return; }
    if (ImGui::IsItemHovered()) ImGui::SetTooltip("Back to the part");
    if (!chr.mat_error.empty()) ImGui::TextColored(CHR_ERR, "%s", chr.mat_error.c_str());
    if (!chr.mat_list.is_array()) { ImGui::TextDisabled("Reading its materials..."); return; }
    if (chr.mat_list.empty()) { ImGui::TextDisabled("No material in this part."); return; }
    if (chr.mat_sel >= (int)chr.mat_list.size()) chr.mat_sel = 0;
    const json mat = chr.mat_list[chr.mat_sel];
    std::string uid = mat.value("uid", std::string());
    ImGui::TextColored(ImVec4(0.95f, 0.55f, 0.2f, 1), "MATERIAL");
    ImGui::SameLine();
    ImGui::SetNextItemWidth(-1);
    if (ImGui::BeginCombo("##mat", mat.value("name", std::string()).c_str())) {
        for (int k = 0; k < (int)chr.mat_list.size(); k++)
            if (ImGui::Selectable(chr.mat_list[k].value("name", std::string()).c_str(), k == chr.mat_sel)) { chr.mat_sel = k; chr.mat_dirty = ImGui::GetTime(); }
        ImGui::EndCombo();
    }
    // the preview (drag to turn), as wide as the panel allows
    float pw = std::clamp(ImGui::GetContentRegionAvail().x * 0.62f, 150.0f, 360.0f), ph = pw * 520.0f / 360.0f;
    ImGui::BeginGroup();
    if (chr.mat_img.srv) {
        ImGui::Image((ImTextureID)(intptr_t)chr.mat_img.srv, ImVec2(pw, ph));
        if (ImGui::IsItemHovered() && ImGui::IsMouseDragging(ImGuiMouseButton_Left)) {
            ImGuiIO& io = ImGui::GetIO();
            chr.mat_yaw -= io.MouseDelta.x * 0.01f;
            chr.mat_pitch = std::clamp(chr.mat_pitch + io.MouseDelta.y * 0.01f, -1.2f, 1.4f);
            chr.mat_dirty = ImGui::GetTime();
        }
    } else ImGui::Dummy(ImVec2(pw, ph));
    ImGui::EndGroup();
    ImGui::SameLine();
    ImGui::BeginGroup();
    ImGui::PushTextWrapPos(0);
    ImGui::TextDisabled(chr.mat_busy ? "drawing..." : "%.2f s - drag to turn", chr.mat_img.meta.is_object() ? chr.mat_img.meta.value("seconds", 0.0) : 0.0);
    if (chr.mat_list.size() > 1 && ImGui::Checkbox("highlight it", &chr.mat_highlight)) chr.mat_dirty = ImGui::GetTime();
    std::string ml;
    const json maps = chr_obj(mat, "maps");
    for (auto& [r, n] : maps.items()) ml += (ml.empty() ? "" : ", ") + r;
    ImGui::TextDisabled("maps: %s", ml.empty() ? "none" : ml.c_str());
    ImGui::TextDisabled("Lit on the CPU with its maps and these values - AC3's character shading terms, approximate: "
                        "how a change moves the look, not the exact amount.");
    ImGui::PopTextWrapPos();
    ImGui::EndGroup();
    // a material the colour scheme binds: its Custom_Color_1 comes from the scheme in game
    bool bound = false;
    if (in.contains("colour_edit") && in["colour_edit"].is_object())
        for (auto& c : in["colour_edit"].value("columns", json::array()))
            if (c.value("what", std::string()) == "colour" && c.value("material", std::string()) == "0x" + uid) bound = true;
    std::string part = chr.mat_part;
    auto set = [&](const std::string& h, const json& v, bool tint) {
        chr_touch("mat:" + part + ":" + uid + ":" + h);
        chr.materials[part][uid][h] = v;
        chr.mat_dirty = ImGui::GetTime();
        if (tint) chr.colour_dirty = ImGui::GetTime();      // (the 3D view bakes the tint: read it again)
    };
    ImGui::SeparatorText("Values");
    if (ImGui::BeginTable("##matvals", 2, ImGuiTableFlags_SizingStretchProp | ImGuiTableFlags_RowBg)) {
        ImGui::TableSetupColumn("value", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableSetupColumn("name", ImGuiTableColumnFlags_WidthStretch);
        int k = 0;
        for (auto& p : mat.value("props", json::array())) {
            std::string h = p.value("hash", std::string()), name = p.value("name", std::string()), kind = p.value("kind", std::string());
            bool edited = chr.materials.contains(part) && chr.materials[part].contains(uid) && chr.materials[part][uid].contains(h);
            json v = edited ? chr.materials[part][uid][h] : p["value"];
            ImGui::PushID(k++);
            ImGui::TableNextRow();
            ImGui::TableNextColumn();
            bool tint = name == "Custom_Color_1";
            ImGui::SetNextItemWidth(-1);
            if (kind == "float3") {
                float c[3] = {v[0].get<float>(), v[1].get<float>(), v[2].get<float>()};
                if (ImGui::ColorEdit3("##v", c, ImGuiColorEditFlags_Float | ImGuiColorEditFlags_HDR)) set(h, {c[0], c[1], c[2]}, tint);
            } else {
                float f = v.get<float>();
                float speed = std::max(0.005f, std::fabs(f) * 0.01f);
                if (ImGui::DragFloat("##v", &f, speed, -1000, 1000, "%.3f")) set(h, f, false);
            }
            ImGui::TableNextColumn();
            ImGui::AlignTextToFramePadding();
            if (edited) ImGui::TextColored(CHR_EDITED, "%s", name.c_str()); else ImGui::TextUnformatted(name.c_str());
            if (tint && bound) {
                ImGui::SameLine();
                ImGui::TextDisabled("(the scheme sets it)");
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("The colour scheme binds this material: in game its colour comes from the scheme.");
            }
            if (edited) {
                ImGui::SameLine();
                if (ImGui::SmallButton("undo")) {
                    chr_touch("");
                    chr.materials[part][uid].erase(h);
                    if (chr.materials[part][uid].empty()) chr.materials[part].erase(uid);
                    if (chr.materials[part].empty()) chr.materials.erase(part);
                    chr.mat_dirty = ImGui::GetTime();
                    if (tint) chr.colour_dirty = ImGui::GetTime();
                }
            }
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
    if (chr.materials.contains(part) && !chr.materials[part].empty()) {
        if (ImGui::Button("Undo this part's material edits")) {
            chr_touch("");
            chr.materials.erase(part);
            chr.mat_dirty = ImGui::GetTime();
            chr.colour_dirty = ImGui::GetTime();
        }
    }
}

// ---- the weapons: its items - a click draws one; the drawn item's model, as a new weapon
void App::ui_chr_weapons(const json& in) {
    ImGui::TextColored(ImVec4(0.95f, 0.55f, 0.2f, 1), "WEAPONS");
    if (chr.weapon_info.is_null()) chr_weapons_refresh();
    if (chr.weapons_loading || chr.weapon_info.is_null()) { ImGui::TextDisabled("Reading the persona's weapons (a few seconds)..."); return; }
    if (chr.weapon_info.contains("error")) { ImGui::TextColored(CHR_ERR, "%s", chr.weapon_info["error"].get<std::string>().c_str()); return; }
    int deflt = chr.weapon_info.value("default", 0);
    json w = in.value("weapon", json());
    int drawn = w.is_object() ? w.value("id", 0) : 0;
    ImGui::PushTextWrapPos(0);
    ImGui::TextDisabled("An own item can draw another weapon of its class (kills, cameras and holster go by the class, so they "
                        "stay); only the model changes. Shared items (held by other personas too) aren't changed. The menu keeps "
                        "the old weapon's icon.");
    ImGui::PopTextWrapPos();
    if (w.is_object())
        ImGui::TextDisabled("Drawn %s%s", w.value("at_name", std::string("beside the body")).c_str(),
                            w.contains("error") ? (" - " + w["error"].get<std::string>()).c_str() : "");
    ImGui::SeparatorText("Its items (click one to draw it)");
    const json items = chr.weapon_info.value("items", json::array());
    const json* sel = nullptr;
    int k = 0;
    for (auto& it : items) {
        int iid = it.value("id", 0);
        std::string key = std::to_string(iid);
        bool edited = chr.weapons.contains(key);
        std::string cur = edited ? chr.weapons[key].get<std::string>() : it.value("file", std::string());
        char lb[256];
        snprintf(lb, sizeof lb, "%d%s   WB%02d   %s%s%s##wi%d", iid, iid == deflt ? " (default)" : "", it.value("cls", 0), cur.c_str(),
                 it.value("own", false) ? "" : "   (shared)", edited ? "  *" : "", k++);
        if (edited) ImGui::PushStyleColor(ImGuiCol_Text, CHR_EDITED);
        if (ImGui::Selectable(lb, drawn == iid) && drawn != iid && !chr.loading) chr_draw_item(iid);
        if (edited) ImGui::PopStyleColor();
        if (drawn == iid) sel = &it;
    }
    if (!sel) return;
    const json& it = *sel;
    int iid = it.value("id", 0), cls = it.value("cls", 0);
    std::string key = std::to_string(iid), stock = it.value("file", std::string());
    bool edited = chr.weapons.contains(key);
    std::string cur = edited ? chr.weapons[key].get<std::string>() : stock;
    ImGui::SeparatorText(("Item " + key + "'s model").c_str());
    if (!it.value("own", false)) {
        ImGui::TextDisabled("%s - shared: %d holders, so it isn't changed here", cur.c_str(), it.value("holders", 0));
        return;
    }
    ImGui::Text("%s", cur.c_str());
    if (edited) {
        ImGui::SameLine();
        if (ImGui::SmallButton("back to stock") && !chr.loading) { chr_touch(""); chr.weapons.erase(key); chr.new_weapons.erase(key); chr_draw_item(iid); }
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Back to %s", stock.c_str());
    }
    ImGui::SetNextItemWidth(-1);
    ImGui::InputTextWithHint("##wfind", "find another model of its class (file or persona)", chr.weapon_find, sizeof chr.weapon_find);
    auto cands = chr_obj(chr.weapon_info, "candidates").value(std::to_string(cls), json::array());
    float lh = std::clamp(ImGui::GetContentRegionAvail().y - (edited ? 110.0f : 10.0f), 120.0f, 420.0f);
    ImGui::BeginChild("##wmodels", ImVec2(0, lh), ImGuiChildFlags_Borders);
    for (auto& c : cands) {
        std::string f = c.value("file", std::string()), label = f, who;
        for (auto& h : c.value("holders", json::array())) {
            std::string nm = persona_name(h.get<int>());
            if (!nm.empty()) who += (who.empty() ? "" : ", ") + nm;
        }
        if (!who.empty()) label += "   - " + who;
        if (!contains_ci(label, chr.weapon_find)) continue;
        if (ImGui::Selectable(label.c_str(), f == cur) && f != cur && !chr.loading) {
            chr_touch("");
            if (f == stock) chr.weapons.erase(key); else chr.weapons[key] = f;
            chr_draw_item(iid);
        }
    }
    ImGui::EndChild();
    if (edited) {                               // the swap as a NEW variant of this item's family
        bool as_new = chr.new_weapons.count(key) > 0;
        if (ImGui::Checkbox("Add it as a new weapon (keep the stock one)", &as_new)) {
            chr_touch("");
            if (as_new) {
                auto& t = chr.new_weapons[key];
                strncpy_s(t.name, "NEW WEAPON", sizeof t.name - 1);
                strncpy_s(t.desc, "Made in Reforged Studio.", sizeof t.desc - 1);
            } else chr.new_weapons.erase(key);
        }
        if (ImGui::IsItemHovered())
            ImGui::SetTooltip("On: the build adds this model as a NEW weapon (an inventory item of the family's next variant,\n"
                              "its menu item, unlock index, name and icon) and leaves %d as it is. Off: the model replaces %d's.", iid, iid);
        if (as_new) {
            auto& t = chr.new_weapons[key];
            char before[64];
            memcpy(before, t.name, sizeof before);
            ImGui::SetNextItemWidth(-90);
            if (ImGui::InputText("name##wn", t.name, sizeof t.name)) {
                char now[64];
                memcpy(now, t.name, sizeof now);
                memcpy(t.name, before, sizeof before);
                chr_touch("nwname:" + key);
                memcpy(chr.new_weapons[key].name, now, sizeof now);
            }
            auto& t2 = chr.new_weapons[key];
            char dbefore[192];
            memcpy(dbefore, t2.desc, sizeof dbefore);
            ImGui::SetNextItemWidth(-90);
            if (ImGui::InputText("description##wd", t2.desc, sizeof t2.desc)) {
                char now[192];
                memcpy(now, t2.desc, sizeof now);
                memcpy(t2.desc, dbefore, sizeof dbefore);
                chr_touch("nwdesc:" + key);
                memcpy(chr.new_weapons[key].desc, now, sizeof now);
            }
        }
    }
}

// ---- Changes: every edit in one list, undo, Build, Test in game (the map editor's Project > Changes + Build)
void App::ui_chr_changes() {
    ImGui::Begin("Changes###chr_changes");
    if (chr.loaded_id < 0) { ImGui::TextDisabled("Choose a persona (Personas)."); ImGui::End(); return; }
    const json& in = chr.info;
    int n = chr_edit_count();
    bool unbuilt = chr_unbuilt();
    // the toolbar: undo / redo / undo all, then the build's name, Build, Test in game
    ImGui::BeginDisabled(chr.undo.empty());
    if (ImGui::Button("Undo")) chr_undo();
    ImGui::EndDisabled();
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled)) ImGui::SetTooltip("Ctrl+Z");
    ImGui::SameLine();
    ImGui::BeginDisabled(chr.redo.empty());
    if (ImGui::Button("Redo")) chr_redo();
    ImGui::EndDisabled();
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled)) ImGui::SetTooltip("Ctrl+Y");
    ImGui::SameLine();
    ImGui::BeginDisabled(n == 0);
    if (ImGui::Button("Undo all")) chr_undo_all();
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::TextDisabled("|");
    ImGui::SameLine();
    if (!chr.build_name[0]) {
        std::string nm = chr_auto_name();
        strncpy_s(chr.build_name, nm.c_str(), sizeof chr.build_name - 1);
    }
    ImGui::SetNextItemWidth(200);
    ImGui::InputText("##chrbuildname", chr.build_name, sizeof chr.build_name);
    if (ImGui::IsItemHovered()) ImGui::SetTooltip("The build\'s name: <workspace>\\characters\\<name>");
    ImGui::SameLine();
    ImGui::BeginDisabled(chr.building || n == 0 || chr.colour_dirty > 0 || chr.loading);
    if (ImGui::Button(chr.building ? "Building..." : "Build (Ctrl+B)")) chr_build();
    ImGui::EndDisabled();
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
        ImGui::SetTooltip("The original DataPC.forge (%s) with these changes, written to <workspace>\\characters\\<name> in a few\n"
                          "seconds (6 GB; DataPC_extra too where a part or an icon lives there), then checked: the edits read back,\n"
                          "every other file byte for byte the original's. AC3MP.exe stays as it is.",
                          in.value("build", std::string()).c_str());
    ImGui::SameLine();
    ImGui::BeginDisabled(n == 0 || (tg_state && !tg_chr));
    const char* tl = tg_chr && tg_state ? (tg_state == 4 ? "Testing (AC3 running)" : "Testing...") : "Test in game (F5)";
    if (ImGui::Button(tl) && !tg_state) chr_test_in_game();
    ImGui::EndDisabled();
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
        ImGui::SetTooltip("Build (unless the last build has these changes), install it, start AC3;\nthe original DataPC goes back when you quit it");
    ImGui::SameLine();
    if (n == 0) ImGui::TextDisabled("no changes");
    else if (unbuilt) ImGui::TextColored(CHR_EDITED, "%d change(s), not built yet", n);
    else ImGui::TextColored(CHR_NEW, "%d change(s), in %s", n, chr.built_name.c_str());
    if (!chr.build_msg.empty()) {
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("%s", chr.build_msg.c_str());
        ImGui::PopTextWrapPos();
    }
    ImGui::Separator();
    // the list: click picks the thing, x undoes it
    ImGui::BeginChild("##chrchanges");
    if (n == 0) ImGui::TextDisabled("No changes to %d %s yet. Pick a part (click it in the view) and change its colours, materials or mesh;\n"
                                    "or a weapon's model (Parts > Weapon).", chr.loaded_id, persona_name(chr.loaded_id).c_str());
    int row = 0;
    auto line = [&](const std::string& kind, const std::string& text, bool picked, const float* rgb) -> int {
        // -> 1 clicked, 2 undo
        int r = 0;
        ImGui::PushID(row++);
        if (ImGui::SmallButton("x")) r = 2;
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Undo this change");
        ImGui::SameLine();
        ImGui::TextColored(CHR_EDITED, "%-9s", kind.c_str());
        ImGui::SameLine();
        if (rgb) {
            ImGui::ColorButton("##sw", ImVec4(rgb[0], rgb[1], rgb[2], 1), ImGuiColorEditFlags_NoTooltip, ImVec2(14, 14));
            ImGui::SameLine();
        }
        if (ImGui::Selectable(text.c_str(), picked)) r = r ? r : 1;
        ImGui::PopID();
        return r;
    };
    // colours: scheme > region (named by the column of the scheme shown; another scheme's are the same columns)
    // (copies: an undo below changes the edits while they're listed; a temporary's items() would dangle)
    const json colours = chr.colours, meshes = chr.meshes, materials = chr.materials, weapons = chr.weapons;
    std::map<int, json> colmap;
    for (auto& c : chr_obj(in, "colour_edit").value("columns", json::array())) colmap[c.value("region", -1)] = c;
    for (auto& [sk, regs] : colours.items()) {
        for (auto& [rk, v] : regs.items()) {
            int region = std::stoi(rk);
            json col = colmap.count(region) ? colmap[region] : json::object();
            std::string nm = col.value("name", std::string());
            float rgb[3] = {v[0].get<float>(), v[1].get<float>(), v[2].get<float>()};
            std::string text = "scheme " + std::to_string(std::stoi(sk) + 1) + "  " + col.value("what", std::string("colour")) + "  " +
                               (nm.empty() ? "region " + rk : nm);
            int r = line("colour", text, false, rgb);
            if (r == 1) {
                json want = chr.rows.is_object() ? chr.rows : json::object();
                if (want.value("Colours", 0) != std::stoi(sk)) { want["Colours"] = std::stoi(sk); chr_load(chr.loaded_id, want); }
                if (!nm.empty() && col.value("drawn", false)) chr_pick_material_part(col.value("material", std::string()));
                else chr_pick(CharUI::PICK_COLOURS);
            }
            if (r == 2) {
                chr_touch("");
                chr.colours[sk].erase(rk);
                if (chr.colours[sk].empty()) chr.colours.erase(sk);
                chr.colour_dirty = ImGui::GetTime();
            }
        }
    }
    for (auto& [part, spec] : meshes.items()) {
        std::string text = part + "  drawn from " + base_name(spec.value("glb", std::string())) +
                           (spec.value("lods", std::string("all")) == "all" ? "" : "  (LOD0 only)") +
                           (chr.new_looks.count(part) ? "  - as the new look \"" + std::string(chr.new_looks[part].name) + "\"" : "");
        int r = line("mesh", text, chr.pick == CharUI::PICK_PART && chr.pick_part == part, nullptr);
        if (r == 1) chr_pick(CharUI::PICK_PART, part);
        if (r == 2) chr_use_mesh(part, "");
    }
    for (auto& [part, mats] : materials.items()) {
        chr_mat_read(part);
        for (auto& [uid, vals] : mats.items()) {
            json mat;
            int mi = 0, idx = 0;
            for (auto& m : chr.mat_cache.value(part, json::array())) { if (m.value("uid", std::string()) == uid) { mat = m; idx = mi; } mi++; }
            for (auto& [h, v] : vals.items()) {
                std::string pname = "0x" + h;
                for (auto& p : mat.value("props", json::array())) if (p.value("hash", std::string()) == h) pname = p.value("name", pname);
                std::string val = v.is_array() ? "(" + std::to_string(v[0].get<float>()).substr(0, 5) + ", " + std::to_string(v[1].get<float>()).substr(0, 5) +
                                                     ", " + std::to_string(v[2].get<float>()).substr(0, 5) + ")"
                                               : std::to_string(v.get<float>()).substr(0, 6);
                std::string text = part + " / " + mat.value("name", uid) + "  " + pname + " = " + val +
                                   (chr.new_looks.count(part) ? "  - in the new look \"" + std::string(chr.new_looks[part].name) + "\"" : "");
                int r = line("material", text, chr.pick == CharUI::PICK_MATERIAL && chr.mat_part == part && chr.mat_sel == idx, nullptr);
                if (r == 1) chr_pick(CharUI::PICK_MATERIAL, part, idx);
                if (r == 2) {
                    chr_touch("");
                    chr.materials[part][uid].erase(h);
                    if (chr.materials[part][uid].empty()) chr.materials[part].erase(uid);
                    if (chr.materials[part].empty()) chr.materials.erase(part);
                    chr.mat_dirty = ImGui::GetTime();
                    chr.colour_dirty = ImGui::GetTime();
                }
            }
        }
    }
    for (auto& [item, f] : weapons.items()) {
        std::string text = "item " + item + " drawn as " + f.get<std::string>() +
                           (chr.new_weapons.count(item) ? "  - as the new weapon \"" + std::string(chr.new_weapons[item].name) + "\"" : "");
        int r = line("weapon", text, chr.pick == CharUI::PICK_WEAPON, nullptr);
        if (r == 1) { chr_pick(CharUI::PICK_WEAPON); if (!chr.loading) chr_draw_item(std::stoi(item)); }
        if (r == 2 && !chr.loading) { chr_touch(""); chr.weapons.erase(item); chr.new_weapons.erase(item); chr_draw_item(std::stoi(item)); }
    }
    ImGui::EndChild();
    ImGui::End();
}

// ---- Test builds: the builds made (any persona's), Install / Delete, the original DataPC back
void App::ui_chr_builds() {
    std::string title = std::string("Test builds") + (chr.installed.empty() ? "" : " (one in the game)") + "###chr_builds";
    ImGui::Begin(title.c_str());
    if (!chr.installed.empty()) {
        ImGui::TextColored(CHR_EDITED, "In the game now: %s", chr.installed.c_str());
        ImGui::SameLine();
    }
    if (ImGui::Button("Put the original DataPC back")) chr_restore_release();
    if (ImGui::IsItemHovered()) ImGui::SetTooltip("Copies the original DataPC.forge back into the game\n"
                                                  "(and its DataPC_extra).");
    ImGui::SameLine();
    if (ImGui::Button("Refresh")) chr_builds_refresh();
    ImGui::SameLine();
    ImGui::TextDisabled("<workspace>\\characters - 6 GB each: delete the ones you\'re done with");
    if (!chr.build_msg.empty()) {
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("%s", chr.build_msg.c_str());
        ImGui::PopTextWrapPos();
    }
    if (chr.builds.empty()) { ImGui::TextDisabled("No character builds yet."); ImGui::End(); return; }
    if (ImGui::BeginTable("##chrbuildlist", 5, ImGuiTableFlags_RowBg | ImGuiTableFlags_BordersInnerH | ImGuiTableFlags_ScrollY)) {
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableSetupColumn("build");
        ImGui::TableSetupColumn("persona");
        ImGui::TableSetupColumn("edits");
        ImGui::TableSetupColumn("made");
        ImGui::TableSetupColumn("", ImGuiTableColumnFlags_WidthFixed, 110);
        ImGui::TableHeadersRow();
        int k = 0;
        for (auto& b : chr.builds) {
            std::string name = b.value("name", std::string());
            int edits = 0, nmesh = (int)b.value("meshes", json::object()).size(), nweap = (int)b.value("weapons", json::object()).size();
            const json bmats = chr_obj(b, "materials"), bedits = chr_obj(b, "edits");
            for (auto& [_p, ms] : bmats.items())
                for (auto& [_m, vals] : ms.items()) edits += (int)vals.size();
            for (auto& [_s, regs] : bedits.items()) edits += (int)regs.size();
            ImGui::PushID(k++);
            ImGui::TableNextRow();
            ImGui::TableNextColumn();
            if (name == chr.installed) ImGui::TextColored(CHR_EDITED, "%s (in the game)", name.c_str());
            else ImGui::Text("%s", name.c_str());
            ImGui::TableNextColumn();
            int pid = b.value("persona", 0);
            ImGui::Text("%d %s", pid, persona_name(pid).c_str());
            ImGui::TableNextColumn();
            int nnew = (int)b.value("new_looks", json::array()).size() + (int)b.value("new_weapons", json::array()).size();
            if (nnew) ImGui::Text("%d value(s), %d mesh(es), %d weapon(s), %d new", edits, nmesh, nweap, nnew);
            else ImGui::Text("%d value(s), %d mesh(es), %d weapon(s)", edits, nmesh, nweap);
            if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s\n%s\n%s", b.value("edits", json::object()).dump(1).c_str(),
                                                          b.value("meshes", json::object()).dump(1).c_str(),
                                                          b.value("weapons", json::object()).dump(1).c_str());
            ImGui::TableNextColumn();
            ImGui::Text("%s", b.value("made", std::string()).c_str());
            ImGui::TableNextColumn();
            ImGui::BeginDisabled(!b.value("exists", false) || name == chr.installed);
            if (ImGui::SmallButton("Install")) {
                chr.build_msg = "Copying " + name + "'s DataPC into the game...";
                engine.request("char_install", {{"name", name}}, [this, name](bool ok, const json& r, const std::string& e) {
                    chr.build_msg = ok ? name + " is in the game (" + r.value("live", std::string()) + "). Put the original back when you're done."
                                       : "Couldn't install it: " + e;
                    chr_builds_refresh();
                });
            }
            ImGui::EndDisabled();
            if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
                ImGui::SetTooltip("Copies its DataPC.forge over the game's (6 GB). Put the original back first\n"
                                  "if another test build is installed.");
            ImGui::SameLine();
            ImGui::BeginDisabled(name == chr.installed);
            if (ImGui::SmallButton("Delete")) {
                engine.request("char_delete", {{"name", name}}, [this](bool ok, const json&, const std::string& e) {
                    if (!ok) chr.build_msg = "Couldn't delete it: " + e;
                    chr_builds_refresh();
                });
            }
            ImGui::EndDisabled();
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
    ImGui::End();
}

// ---- switching persona with changes not in a build
void App::ui_chr_modals() {
    if (chr.ask_switch >= 0 && !ImGui::IsPopupOpen("Unbuilt character changes")) ImGui::OpenPopup("Unbuilt character changes");
    if (ImGui::BeginPopupModal("Unbuilt character changes", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        int to = chr.ask_switch;
        if (to < 0) { ImGui::CloseCurrentPopup(); ImGui::EndPopup(); return; }
        ImGui::Text("%d change(s) to %d %s aren't in a build yet.", chr_edit_count(), chr.loaded_id, persona_name(chr.loaded_id).c_str());
        ImGui::Text("Opening %d %s drops them.", to, persona_name(to).c_str());
        if (ImGui::Button("Build them first")) {
            chr.load_after_build = to;
            chr_build();
            chr.ask_switch = -1;
            ImGui::CloseCurrentPopup();
        }
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Build them (Test builds keeps the build), then open %s", persona_name(to).c_str());
        ImGui::SameLine();
        if (ImGui::Button("Discard them")) { chr.ask_switch = -1; chr_load(to); ImGui::CloseCurrentPopup(); }
        ImGui::SameLine();
        if (ImGui::Button("Cancel") || ImGui::IsKeyPressed(ImGuiKey_Escape)) { chr.ask_switch = -1; ImGui::CloseCurrentPopup(); }
        ImGui::EndPopup();
    }
}
