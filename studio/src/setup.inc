// The first-run setup (a release tree - forgelib\ beside studio\ - with no config yet; File > Settings... any time):
// where the game is, where the Studio keeps its work, Blender. Writes the config the engine's paths read
// (REFORGED_STUDIO_CONFIG or AC3STUDIO_CONFIG, else %APPDATA%\Reforged Studio\config.json), then starts the engine and reads the game once (ingest).
#include <shobjidl.h>

static std::string config_file() {
    char env[1024];
    if (GetEnvironmentVariableA("REFORGED_STUDIO_CONFIG", env, sizeof env)) return env;
    if (GetEnvironmentVariableA("AC3STUDIO_CONFIG", env, sizeof env)) return env;
    char app[MAX_PATH];
    if (!GetEnvironmentVariableA("APPDATA", app, sizeof app)) return "config.json";
    std::string cur = std::string(app) + "\\Reforged Studio\\config.json", old = std::string(app) + "\\AC3 Studio\\config.json";
    return file_exists(old) && !file_exists(cur) ? old : cur;      // (a config under the older name, AC3 Studio, is still read)
}

static std::string folder_dialog(HWND hwnd, const wchar_t* title, const std::string& start) {
    std::string out;
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    IFileOpenDialog* d = nullptr;
    if (SUCCEEDED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&d)))) {
        DWORD opts = 0;
        d->GetOptions(&opts);
        d->SetOptions(opts | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM);
        d->SetTitle(title);
        if (!start.empty()) {
            IShellItem* si = nullptr;
            if (SUCCEEDED(SHCreateItemFromParsingName(widen(start).c_str(), nullptr, IID_PPV_ARGS(&si)))) { d->SetFolder(si); si->Release(); }
        }
        if (SUCCEEDED(d->Show(hwnd))) {
            IShellItem* r = nullptr;
            if (SUCCEEDED(d->GetResult(&r))) {
                PWSTR p = nullptr;
                if (SUCCEEDED(r->GetDisplayName(SIGDN_FILESYSPATH, &p))) { out = narrow(p); CoTaskMemFree(p); }
                r->Release();
            }
        }
        d->Release();
    }
    return out;
}

bool App::is_release_tree() const { return !repo.empty() && file_exists(repo + "\\forgelib\\forge.py"); }

json App::read_config() const {
    std::ifstream f(widen(config_file()));
    if (!f) return json();
    try { return json::parse(f); } catch (...) { return json(); }
}

// a value of the config's AC3 install: installs.AC3.<key> (config version 2), else the first version's key
static std::string config_ac3(const json& c, const char* key, const char* first_name) {
    if (!c.is_object()) return "";
    if (c.contains("installs") && c["installs"].is_object() && c["installs"].contains("AC3") && c["installs"]["AC3"].is_object()) {
        std::string v = c["installs"]["AC3"].value(key, std::string());
        if (!v.empty()) return v;
    }
    return c.value(first_name, std::string());
}

static std::string expand_pf(std::string p) {
    char pf86[MAX_PATH] = "C:\\Program Files (x86)", pf[MAX_PATH] = "C:\\Program Files";
    GetEnvironmentVariableA("ProgramFiles(x86)", pf86, sizeof pf86);
    GetEnvironmentVariableA("ProgramFiles", pf, sizeof pf);
    for (auto [k, v] : {std::pair<std::string, std::string>{"%PF86%", pf86}, {"%PF%", pf}}) {
        size_t i = p.find(k);
        if (i != std::string::npos) p.replace(i, k.size(), v);
    }
    return p;
}

void App::setup_profiles() {
    if (!setup.sources.empty()) return;
    std::string dir = repo + "\\studio\\games\\";
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(widen(dir + "*_MP.json").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    std::vector<std::string> files;
    do files.push_back(narrow(fd.cFileName)); while (FindNextFileW(h, &fd));
    FindClose(h);
    std::sort(files.begin(), files.end());
    for (const char* g : {"ACR", "ACB", "AC4"})               // (the chain's order)
        for (auto& f : files) {
            if (f != std::string(g) + "_MP.json") continue;
            std::ifstream in(widen(dir + f));
            json p;
            try { p = json::parse(in); } catch (...) { continue; }
            SetupUI::Source s;
            s.game = p.value("game", std::string(g));
            s.title = p.value("title", s.game);
            auto inst = p.value("install", json::object());
            s.exe = inst.value("exe", std::string());
            s.subdir = inst.value("mp_subdir", std::string("multi"));
            for (auto& x : inst.value("guesses", json::array())) s.guesses.push_back(expand_pf(x.get<std::string>()));
            setup.sources.push_back(s);
        }
}

// a folder the user picked (the game folder or its multiplayer folder) -> the multiplayer folder, "" when neither is
std::string App::setup_mp_dir(const SetupUI::Source& s, const std::string& folder) const {
    std::string f = folder;
    while (!f.empty() && (f.back() == '\\' || f.back() == '/')) f.pop_back();
    if (f.empty()) return "";
    // (the multiplayer subfolder first: the game folder holds singleplayer archives under the same names)
    if (file_exists(f + "\\" + s.subdir + "\\DataPC.forge")) return f + "\\" + s.subdir;
    size_t cut = f.find_last_of("\\/");
    if (file_exists(f + "\\DataPC.forge") && cut != std::string::npos && file_exists(f.substr(0, cut) + "\\" + s.exe)) return f;
    return "";
}

bool App::setup_needed() const {
    if (!is_release_tree()) return false;                 // (a development checkout, without forgelib\, needs no setup)
    json c = read_config();
    return !c.is_object() || !file_exists(config_ac3(c, "mp_dir", "game_dir") + "\\DataPC.forge");
}

void App::setup_open() {
    json c = read_config();
    auto put = [](char* buf, size_t n, const std::string& v) { strncpy_s(buf, n, v.c_str(), n - 1); };
    setup_profiles();
    for (auto& s : setup.sources) {                       // the configured install, else the first usual place found
        std::string have;
        if (c.is_object() && c.contains("installs") && c["installs"].is_object() && c["installs"].contains(s.game) &&
            c["installs"][s.game].is_object()) {
            const json& i = c["installs"][s.game];
            have = i.value("root", std::string());
            if (have.empty()) have = i.value("mp_dir", std::string());
        }
        if (have.empty() && s.game == "ACR" && c.is_object()) have = c.value("acr_dir", std::string());
        if (have.empty())
            for (auto& g : s.guesses) if (!setup_mp_dir(s, g).empty()) { have = g; break; }
        put(s.dir, sizeof s.dir, have);
    }
    std::string game = config_ac3(c, "mp_dir", "game_dir");
    if (game.empty()) {                                     // the launcher's usual place
        std::string guess = "C:\\Program Files (x86)\\Ubisoft\\Ubisoft Game Launcher\\games\\Assassin's Creed III\\multi";
        if (file_exists(guess + "\\DataPC.forge")) game = guess;
    }
    put(setup.game, sizeof setup.game, game);
    put(setup.sp, sizeof setup.sp, config_ac3(c, "sp_dir", "sp_dir"));
    std::string work = c.is_object() ? c.value("workspace", std::string()) : "";
    if (work.empty()) {
        char up[MAX_PATH];
        if (GetEnvironmentVariableA("USERPROFILE", up, sizeof up)) work = std::string(up) + "\\Documents\\Reforged Studio";
    }
    put(setup.work, sizeof setup.work, work);
    std::string bl = c.is_object() ? c.value("blender", std::string()) : "";
    if (bl.empty()) {
        WIN32_FIND_DATAW fd;
        HANDLE h = FindFirstFileW(L"C:\\Program Files\\Blender Foundation\\Blender *", &fd);
        std::string best;
        if (h != INVALID_HANDLE_VALUE) {
            do {
                std::string p = "C:\\Program Files\\Blender Foundation\\" + narrow(fd.cFileName) + "\\blender.exe";
                if (file_exists(p) && p > best) best = p;
            } while (FindNextFileW(h, &fd));
            FindClose(h);
        }
        bl = best;
    }
    put(setup.blender, sizeof setup.blender, bl);
    setup.error.clear();
    setup.open = true;
}

void App::setup_save() {
    std::string game = setup.game;
    while (!game.empty() && (game.back() == '\\' || game.back() == '/')) game.pop_back();
    if (!file_exists(game + "\\DataPC.forge") && file_exists(game + "\\multi\\DataPC.forge")) game += "\\multi";
    if (!file_exists(game + "\\DataPC.forge")) {
        setup.error = "No DataPC.forge in that folder: pick the game's multiplayer folder (...\\Assassin's Creed III\\multi).";
        return;
    }
    std::vector<std::pair<SetupUI::Source*, std::string>> picked;   // (each other game's multiplayer folder, "" = none)
    for (auto& s : setup.sources) {
        std::string mp = setup_mp_dir(s, s.dir);
        if (s.dir[0] && mp.empty()) {
            setup.error = "No " + s.title + " there: pick its game folder (with " + s.exe + " and the multi folder), or clear the field.";
            return;
        }
        picked.push_back({&s, mp});
    }
    json c = read_config();
    if (!c.is_object()) c = json::object();
    // config version 2: the target and every game's install; the first version's keys are kept beside them, so an
    // older Studio reading the same file still finds the game
    c["version"] = 2;
    c["target"] = {{"game", "AC3"}, {"edition", "MP"}};
    if (!c.contains("installs") || !c["installs"].is_object()) c["installs"] = json::object();
    json& ac3 = c["installs"]["AC3"];
    if (!ac3.is_object()) ac3 = json::object();
    ac3["mp_dir"] = game;
    ac3["root"] = game.substr(0, game.find_last_of("\\/"));
    for (const char* k : {"release_set", "pristine_dir"})
        if (!ac3.contains(k) && c.contains(k)) ac3[k] = c[k];
    c["game_dir"] = game;
    c["workspace"] = std::string(setup.work);
    if (setup.sp[0]) { c["sp_dir"] = std::string(setup.sp); ac3["sp_dir"] = std::string(setup.sp); }
    else { c.erase("sp_dir"); ac3.erase("sp_dir"); }
    for (auto& [s, mp] : picked) {
        if (mp.empty()) { c["installs"].erase(s->game); if (s->game == "ACR") c.erase("acr_dir"); continue; }
        c["installs"][s->game] = {{"root", mp.substr(0, mp.find_last_of("\\/"))}, {"mp_dir", mp}};
        if (s->game == "ACR") c["acr_dir"] = mp;
    }
    if (setup.blender[0]) c["blender"] = std::string(setup.blender); else c.erase("blender");
    std::string path = config_file();
    std::wstring wdir = widen(path.substr(0, path.find_last_of("\\/")));
    CreateDirectoryW(wdir.c_str(), nullptr);
    {
        std::ofstream f(widen(path));
        if (!f) { setup.error = "Can't write " + path; return; }
        f << c.dump(1);
    }
    CreateDirectoryW(widen(setup.work).c_str(), nullptr);
    setup.open = false;
    // the engine reads the config when it starts: (re)start it, then read the game once
    if (engine.running()) engine.stop();
    start_engine();
    ingest();
}

void App::ingest() {
    setup.ingesting = true;
    setup.ingest_msg = "Reading your game (once): the maps, the personas, the ids in use - a few minutes the first time...";
    status = setup.ingest_msg;
    engine.request("ingest", json::object(), [this](bool ok, const json& r, const std::string& e) {
        setup.ingesting = false;
        setup.ingest_msg = ok ? "Ready: " + std::to_string(r.value("maps", 0)) + " maps, " + std::to_string(r.value("personas", 0)) +
                                    " personas, " + std::to_string(r.value("ids", 0)) + " ids read from " + r.value("game", std::string())
                              : "Reading the game failed: " + e;
        status = setup.ingest_msg;
        for (auto& p : r.value("problems", json::array())) add_log("setup: " + p.get<std::string>());
    });
}

void App::ui_setup() {
    if (!setup.open) return;
    // (opened once: opening it every frame re-opens it, and an auto-sized window is hidden on the frame it opens)
    if (!ImGui::IsPopupOpen("Set up Reforged Studio")) ImGui::OpenPopup("Set up Reforged Studio");
    ImGui::SetNextWindowPos(ImGui::GetMainViewport()->GetCenter(), ImGuiCond_Always, ImVec2(0.5f, 0.5f));
    if (!ImGui::BeginPopupModal("Set up Reforged Studio", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) return;
    ImGui::TextWrapped("Reforged Studio ships no game files: it reads yours. Point it at your Assassin's Creed III install - "
                       "the multiplayer folder that holds DataPC.forge (an acmpreforged install has every persona and map "
                       "the Studio knows). It never changes a game file until you press Install, and it keeps the game's "
                       "own copy first, so Put back always works.");
    ImGui::Text("Game: %s", setup.target_title.c_str());
    ImGui::Separator();
    auto row = [&](const char* label, char* buf, size_t n, const char* hint, bool folder, const wchar_t* title) {
        ImGui::PushID(label);
        ImGui::TextUnformatted(label);
        ImGui::SetNextItemWidth(640);
        ImGui::InputTextWithHint("##v", hint, buf, n);
        ImGui::SameLine();
        if (ImGui::Button("Browse...")) {
            std::string p = folder ? folder_dialog(hwnd, title, buf)
                                   : file_dialog(hwnd, false, "", L"blender.exe\0blender.exe\0Programs (*.exe)\0*.exe\0", L"exe");
            if (!p.empty()) strncpy_s(buf, n, p.c_str(), n - 1);
        }
        ImGui::PopID();
    };
    row("The game's multiplayer folder (required)", setup.game, sizeof setup.game,
        "...\\Assassin's Creed III\\multi", true, L"The game's multiplayer folder (with DataPC.forge)");
    bool ok_game = file_exists(std::string(setup.game) + "\\DataPC.forge") || file_exists(std::string(setup.game) + "\\multi\\DataPC.forge");
    ImGui::TextColored(ok_game ? ImVec4(0.5f, 0.85f, 0.5f, 1) : ImVec4(1, 0.6f, 0.3f, 1), ok_game ? "DataPC.forge found" : "no DataPC.forge there yet");
    row("The singleplayer install (optional: singleplayer models and areas)", setup.sp, sizeof setup.sp,
        "default: the multiplayer folder's parent", true, L"The singleplayer install (with DataPC_DX11.forge)");
    if (!setup.sources.empty()) {
        ImGui::Spacing();
        ImGui::TextDisabled("Other games (optional, read only - File > Import a map from another game). Found ones are filled in;");
        ImGui::TextDisabled("clear a field to leave that game out.");
        for (auto& s : setup.sources) {
            std::string name = s.title.size() > 12 && s.title.compare(s.title.size() - 12, 12, " multiplayer") == 0
                                   ? s.title.substr(0, s.title.size() - 12) : s.title;
            std::string label = name + " - its game folder";
            row(label.c_str(), s.dir, sizeof s.dir, ("...\\" + name + " (with " + s.exe + ")").c_str(),
                true, widen("The game folder of " + name).c_str());
            if (s.dir[0]) {
                bool ok = !setup_mp_dir(s, s.dir).empty();
                ImGui::TextColored(ok ? ImVec4(0.5f, 0.85f, 0.5f, 1) : ImVec4(1, 0.6f, 0.3f, 1),
                                   ok ? "multiplayer folder found" : "no multiplayer folder (multi\\DataPC.forge) there");
            }
        }
        ImGui::Spacing();
    }
    row("Your workspace (builds, caches, the game's originals the Studio keeps - can grow to tens of GB)", setup.work,
        sizeof setup.work, "Documents\\Reforged Studio", true, L"Where Reforged Studio keeps its work");
    row("Blender (optional: editing character meshes)", setup.blender, sizeof setup.blender, "blender.exe", false, nullptr);
    if (!setup.error.empty()) ImGui::TextColored(ImVec4(1, 0.45f, 0.4f, 1), "%s", setup.error.c_str());
    ImGui::Separator();
    ImGui::BeginDisabled(!ok_game || !setup.work[0]);
    if (ImGui::Button("Save and read the game", ImVec2(220, 0))) { setup_save(); if (!setup.open) ImGui::CloseCurrentPopup(); }
    ImGui::EndDisabled();
    ImGui::SameLine();
    bool can_cancel = !setup_needed();
    ImGui::BeginDisabled(!can_cancel);
    if (ImGui::Button("Cancel")) { setup.open = false; ImGui::CloseCurrentPopup(); }
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::TextDisabled("Settings: %s", config_file().c_str());
    ImGui::EndPopup();
}
