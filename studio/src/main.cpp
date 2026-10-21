// Reforged Studio - a desktop map and character editor for AC3 multiplayer: open a map, look, select, move / turn /
// copy / remove, Select Building, build a test archive and install it; the asset library (any model of any stock map
// placed in the open one); editable assets (walls / boxes / slabs, a selection saved as a user asset, the asset
// editor); the Characters workspace. The Python engine (studio/engine) reads and writes the archives; this program
// draws and edits. README.md has the controls.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commdlg.h>
#include <shellapi.h>
#include <tlhelp32.h>
#include <d3d11.h>
#include <algorithm>
#include <atomic>
#include <cctype>
#include <functional>
#include <cstdio>
#include <fstream>
#include <memory>
#include <random>
#include <set>
#include <sstream>
#include <unordered_map>
#include <thread>
#include "imgui.h"
#include "imgui_internal.h"
#include "backends/imgui_impl_dx11.h"
#include "backends/imgui_impl_win32.h"
#include "ImGuizmo.h"
#include "edits.h"
#include "live.h"
#include "mapedit.h"
#include "climbview.h"
#include "categories.h"
#include <array>
#include "engine.h"
#include "renderer.h"
#include "scene.h"

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

static const char* APP = "Reforged Studio";
static const char* VERSION = "1.1 (grouped toolbar, shortcuts, settings kept)";

// ------------------------------------------------------------------------------------------------ helpers --

static std::wstring widen(const std::string& s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), n);
    if (!w.empty() && w.back() == 0) w.pop_back();
    return w;
}

static std::string narrow(const std::wstring& w) {
    int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, nullptr, 0, nullptr, nullptr);
    std::string s(n, 0);
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, s.data(), n, nullptr, nullptr);
    if (!s.empty() && s.back() == 0) s.pop_back();
    return s;
}

static void make_dirs(const std::wstring& path) {
    for (size_t k = path.find_first_of(L"\\/", 3); ; k = path.find_first_of(L"\\/", k + 1)) {
        CreateDirectoryW(path.substr(0, k).c_str(), nullptr);
        if (k == std::wstring::npos) break;
    }
}

static bool file_exists(const std::string& p) { return GetFileAttributesW(widen(p).c_str()) != INVALID_FILE_ATTRIBUTES; }

static std::string base_name(const std::string& p) {
    size_t k = p.find_last_of("\\/");
    return k == std::string::npos ? p : p.substr(k + 1);
}

static std::string find_repo() {
    wchar_t buf[MAX_PATH];
    GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::string dir = narrow(buf);
    for (int i = 0; i < 6; i++) {
        size_t k = dir.find_last_of("\\/");
        if (k == std::string::npos) break;
        dir = dir.substr(0, k);
        if (file_exists(dir + "\\studio\\engine\\worker.py")) return dir;
    }
    return "";
}

static std::string find_python(const std::string& repo = "") {
    char env[1024];
    if (GetEnvironmentVariableA("REFORGED_STUDIO_PYTHON", env, sizeof env)) return env;
    if (GetEnvironmentVariableA("AC3STUDIO_PYTHON", env, sizeof env)) return env;
    // a Python shipped with the program (python\, or the setup script's .venv)
    for (const char* sub : {"\\python\\python.exe", "\\.venv\\Scripts\\python.exe"})
        if (!repo.empty() && GetFileAttributesA((repo + sub).c_str()) != INVALID_FILE_ATTRIBUTES) return repo + sub;
    wchar_t buf[MAX_PATH];
    if (SearchPathW(nullptr, L"python.exe", nullptr, MAX_PATH, buf, nullptr)) {
        std::string p = narrow(buf);
        if (p.find("WindowsApps") == std::string::npos) return p;   // (the Store's stub opens the Store)
    }
    return "python";
}

static std::string file_dialog(HWND hwnd, bool save, const std::string& initial, const wchar_t* filter = nullptr,
                               const wchar_t* ext = nullptr, const std::string& folder = "") {
    wchar_t file[MAX_PATH] = L"";
    if (!initial.empty()) wcsncpy_s(file, widen(initial).c_str(), MAX_PATH - 1);
    std::wstring wfolder = widen(folder);
    OPENFILENAMEW of{sizeof of};
    of.hwndOwner = hwnd;
    of.lpstrFilter = filter ? filter : L"Reforged Studio project (*.ac3proj)\0*.ac3proj\0All files\0*.*\0";
    of.lpstrFile = file;
    of.nMaxFile = MAX_PATH;
    of.lpstrDefExt = ext ? ext : L"ac3proj";
    if (!folder.empty()) of.lpstrInitialDir = wfolder.c_str();
    of.Flags = save ? OFN_OVERWRITEPROMPT | OFN_NOCHANGEDIR : OFN_FILEMUSTEXIST | OFN_NOCHANGEDIR;
    BOOL ok = save ? GetSaveFileNameW(&of) : GetOpenFileNameW(&of);
    return ok ? narrow(file) : "";
}

static uint32_t rgba(float r, float g, float b, float a = 1) {
    auto c = [](float x) { return (uint32_t)(std::clamp(x, 0.0f, 1.0f) * 255.0f + 0.5f); };
    return c(r) | c(g) << 8 | c(b) << 16 | c(a) << 24;
}

// ------------------------------------------------------------------------------------------------ the app --

struct MapEntry { std::string path, title, source, kind; };

struct Camera {
    V3 pos{0, -40, 30};
    float yaw = 1.5708f, pitch = -0.6f;     // yaw 0 = +x
    float speed = 12.0f;                    // m/s
    V3 forward() const { return {std::cos(pitch) * std::cos(yaw), std::cos(pitch) * std::sin(yaw), std::sin(pitch)}; }
    V3 right() const { return normalize(cross(forward(), V3(0, 0, 1))); }
    M4 view() const { return look_at(pos, pos + forward(), V3(0, 0, 1)); }
};

struct App {
    HWND hwnd = nullptr;
    ID3D11Device* dev = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    IDXGISwapChain* swap = nullptr;
    ID3D11RenderTargetView* back_rtv = nullptr;

    std::string repo, python;
    Engine engine;
    Renderer renderer;
    std::unique_ptr<Scene> scene;
    Edits edits;
    Camera cam;

    std::vector<MapEntry> maps;
    std::string map_filter, outliner_filter;
    std::string open_archive;                 // the map being opened / open
    std::string status = "Starting the engine...";
    std::string project_path;
    json pending_project;                     // applied once its map has loaded

    // loading
    std::thread loader;
    std::atomic<int> load_state{0};           // 0 idle, 1 engine, 2 reading / uploading, 3 done, 4 failed
    std::atomic<int> load_progress{0};
    std::unique_ptr<Scene> loaded;
    std::string load_error;

    // view
    std::vector<uint8_t> record_hidden;
    int mode_record = -1;                     // the game mode shown (-1: none)
    bool show_collision = false, show_markers = true, show_volumes = false, show_removed = false;
    bool alpha_clip = true, show_labels = true;
    float fov = 60.0f;

    // selection and the gizmo
    std::vector<int> selection;
    int hovered = -1;
    ImGuizmo::OPERATION gizmo_op = ImGuizmo::TRANSLATE;
    bool gizmo_local = false, snap = false, all_axes = false;
    float snap_move = 0.5f, snap_turn = 15.0f;
    bool dragging = false;
    M4 drag_pivot0;
    std::vector<M4> drag_start;
    M4 pivot = M4::identity();
    bool carry_ground = true, show_holes = true;
    std::vector<V3> holes_vis, holes_col;     // the last build's holes (engine holes.py)
    std::vector<LineVert> plug_tris;          // the holes the last build plugged (green: a slab, blue: collision only)

    // build
    int built_at = -100, built_at_pending = -100;     // (the edit history step the last build was made from)
    json last_build;
    std::string last_dest, live_state, release_file;
    bool from_release = true, ask_install = false;
    bool build_ok = false;
    std::vector<std::string> log_lines;
    bool log_scroll = false;

    // modal: unsaved changes before opening another map
    std::string ask_open;
    bool want_quit = false, quit_now = false;

    M4 view_m, proj_m;
    ImVec2 vp_pos, vp_size;
    bool focus_build = false;

    // the asset library (studio/engine/library.py): every model of every stock map, indexed in the background
    struct LibEntry {
        std::string key, archive, mesh, name, map, rep, collision, materials;
        float size[3] = {0, 0, 0}, lo[3] = {0, 0, 0};
        int nt = 0, uses = 0, n_materials = 0, textured = 0;
        bool copyable = false, ground = false, sp = false;   // sp: an SP model
        std::string cat;                              // its category (automatic, or the user's filing)
    };
    std::vector<LibEntry> lib;
    std::set<std::string> lib_read;           // index files read
    json lib_maps = json::array();            // the engine's index state, per map
    bool lib_busy = false, lib_asked = false, lib_hide_ground = true, lib_dirty = true, focus_library = false;
    double lib_next_poll = 0;
    double maps_next_poll = 0;                // the game folder's map list, re-read every few seconds (maps_watch)
    std::string lib_filter, lib_map, lib_prefer, lib_sel_key;
    std::set<std::string> sp_wanted;          // the SP archives asked for (indexed; kept in the settings)
    std::vector<int> lib_view;                // the rows shown (filtered, sorted)
    int lib_sort_col = 0;
    bool lib_sort_asc = true;
    float preview_turn = 0.6f;
    // the asset scene: the library models in use (placed, or in the preview) as one scene cache, drawn from SLOT_ASSETS
    std::unique_ptr<Scene> assets_scene;
    std::unordered_map<std::string, int> assets_at;   // key -> mesh / shape / entity index in assets_scene
    std::vector<json> asset_meta;                     // per assets_scene index (library.py's asset.json)
    bool assets_loading = false, assets_again = false;
    std::string preview_key;
    mutable Entity placeholder;                       // a library model whose asset scene isn't loaded yet
    std::vector<std::string> asset_keys_all;          // every key the asset scene holds (append-only, both workspaces)
    std::vector<std::string> assets_keys_loaded;      // the loaded asset scene's keys, in its order
    std::string assets_error;                         // the last asset scene request's failure

    // primitives (walls, boxes, floor slabs), user assets (My assets), the asset editor
    std::string prim_m, prim_mk, prim_mn;             // the material new primitives get: model key, material key, name
    float prim_edit[3] = {0, 0, 0};
    int prim_edit_asset = -1;
    struct UserAsset { std::string path, name, made_on; int parts = 0; json j; };
    std::string user_dir;                             // ac3-editor\assets (--test: <test folder>\assets)
    std::vector<UserAsset> user_assets;
    bool user_scanned = false;
    int user_sel = -1, lib_tab = 0;
    bool ask_name = false, ask_close = false;
    int ask_name_for = 0;                             // 0: save the selection as an asset, 1: save the editor's as new
    char name_buf[128] = "";
    // nested assets: a placed asset's parts carry its group and their part path; a click picks the whole asset, a
    // double-click goes one level in (a child asset, then its parts); Esc comes back out
    int scope_group = -1, scope_depth = 0, plus_counter = 0;
    std::vector<int> drag_groups;
    std::vector<M4> drag_roots;
    // the asset editor: the map (its scene, edits, camera, selection) set aside while an asset is edited on a grid
    bool workspace = false;
    json ws_asset;                                    // the asset being edited (its definition as opened / saved)
    int ws_group = -1;                                // its parts' group in the editor
    std::string ws_name, ws_path, ws_made_on, stash_project;
    std::unique_ptr<Scene> stash_scene;
    Edits stash_edits;
    Camera stash_cam;
    std::vector<int> stash_sel;
    std::vector<uint8_t> stash_hidden;
    int stash_mode = -1;

    // --test <archive> <folder>: open, Select Building, turn it, save the project, build; then place a model of
    // another map from the library, build again; a screenshot per step
    std::string test_archive, test_dir, shot_path, test_piece;   // (piece: a name prefix, optional)
    std::string test_asset, test_asset_map;                      // (--asset <name prefix> <map title>)
    double test_t0 = 0;
    std::vector<int> test_building;                              // (the asset steps: the barracks' pieces)
    int test_wall = -1, test_parts = 0;
    std::string test_asset_path, test_nested_path;
    json test_ground;
    bool test_ground_done = false;
    int test_nested_total = 0;
    bool region_test = false;                     // --region-test: a square round Wolcott's pillory raised 1 m, built
    bool nav_test = false;                        // --nav-test: a navmesh block area + two crowd route edits, built
    int draw_kind = AREA_GROUND;                  // what the area being drawn becomes (ground / navmesh remake / block)
    // the crowds' navmesh of the open map (engine navview.nav_data), drawn on request
    struct NavRoute { std::string uid, name; float width = 4; bool active = true; std::vector<V3> pts; int head = 0, tail = 0; };
    struct NavView {
        bool loaded = false, loading = false;
        std::vector<V3> ea, eb;
        std::vector<int> ekind;                   // 0 inner, 1 seam, 2 drop, 3 wall
        std::vector<V3> wps;
        std::vector<std::pair<int, int>> links;
        std::vector<NavRoute> routes;
        int cells = 0, tris = 0;
    } navv;
    bool show_nav = false, show_waypoints = false, show_routes = false;
    int sel_route = -1;
    // blend groups - the static NPC groups players blend into (engine blendview.blend_data): the map's, and the
    // stock maps' to place (Library > Blend groups). Their NPC slots are drawn round each group, moving with it
    struct BlendSlot { V3 p, f; };                // position and facing, relative to the group
    struct BlendPiece {                           // a piece of the set the group stands at (a merchant's stall)
        std::string piece, name, key, map;        // key: a library key "<archive>|<mesh hex>#<entity hex>"
        M4 matrix = M4::identity();               // relative to the group's position (the group keeps its turn)
    };
    struct BlendInfo {
        uint32_t uid = 0;
        std::string key, name, label, map, props; // key: "blend:<archive>|<uid hex>" (templates)
        std::string missing;                      // (templates) props its NPCs hold that the open map hasn't
        M4 matrix = M4::identity();
        std::vector<BlendSlot> slots;
        std::vector<BlendPiece> set;              // (templates) its stall / counter / table, as its own map has it
    };
    struct BlendView {
        bool loaded = false, loading = false;
        std::vector<BlendInfo> groups, templates;
        std::unordered_map<uint32_t, int> by_uid;
        std::unordered_map<std::string, int> by_key;
    } blendv;
    std::unordered_map<std::string, BlendInfo> blend_assets;   // placed blend groups' slots, from the asset scene's metas
    bool show_blend = true;
    // climbing - the map's climb edges and no-climb collision (engine climbview.py), each library model's own
    ClimbView climbv;
    bool show_climb = false;
    // box select (a drag from empty space), the self-test
    bool marquee_on = false, marquee_add = false, sa_test = false;
    // library categories (categories.h): the filter shown, the user's filing, a new category's name being typed
    cats::Store catstore;
    std::string lib_cat, my_cat;
    char newcat_buf[64] = "";
    bool lc_test = false;
    // the shortcuts sheet, the Outliner's filter, settings kept between sessions (%LOCALAPPDATA%\ReforgedStudio)
    bool show_help = false, ui_test = false, setup_test = false, maps_watch_test = false;
    int test_frames = 0;                              // (frames waited in a test step)
    // Test in game - 0 idle, 1 building, 2 installing, 3 waiting for the game, 4 the game running, 5 game gone
    // (restoring in a while); the game's exe from the engine's status; dry: a self-test (nothing installed or launched)
    int tg_state = 0;
    // a bare map - every map object drawn with a model taken out (Edit > Clear the map), or those in an area
    bool bare_keep_ground = true, bare_keep_gameplay = false, bare_keep_blend = false, bare_far_off = true;
    bool bare_ask = false, ba_test = false;
    bool spl_test = false;                            // --spl-test (an SP Boston model placed and built)
    // File > New map from a singleplayer area - an SP map's overview, an area dragged on it, its detailed
    // preview and street level, the host; the conversion (spmaps' converter, minutes), then the new map opened
    struct SpaImage { ID3D11ShaderResourceView* srv = nullptr; json meta; bool busy = false; };
    // View > Characters - a persona's default look (its parts by slot), its face and default weapon, drawn
    // from the default set's DataPC (engine charview.py); orbit with the left mouse, zoom with the wheel
    struct CharUI {
        bool open = false, asked = false, loading = false, test = false;
        json personas = json::array(), info;
        std::string filter, error;
        int sel = -1, loaded_id = -1;
        std::unique_ptr<Scene> scene;
        std::vector<char> show;
        float yaw = -1.9f, pitch = 0.15f, dist = 3.2f;     // (characters face -y: the front, a little from the side)
        double t0 = 0;
        int test_n = 0;
        json rows = json::object();                   // the look per slot asked for ({} = the defaults)
        // the clip it plays - skinning data per part (by scene entity), the bones' poses per frame
        struct Infl { uint16_t bone, pad; float w, loc[3]; };
        struct SkinPart { int entity = -1; std::vector<Infl> infl; };     // 4 per corner vertex
        std::vector<SkinPart> skin;
        std::vector<float> frames;                    // nframes x nbones x 12 (3 x 3 rotation row-major, position)
        int nbones = 0, nframes = 0;
        float fps = 30, t = 0;
        bool playing = true, anim_loading = false, animated = false;
        json anim, clips = json::array();
        std::string clip;
        std::vector<float> work;
        // colour edits ({scheme: {region: [r, g, b]}}), previewed once the picking stops; test builds of them
        json colours = json::object();
        double colour_dirty = 0;                      // when an unpreviewed edit was made (0 = none)
        bool show_unused = false, building = false, builds_asked = false;
        json builds = json::array();
        std::string installed, build_msg;
        char build_name[64] = "";
        // parts drawn from their edited glTF ({part: {glb, lods}}), what each part's export said
        json meshes = json::object(), exports = json::object();
        std::string mesh_busy;                        // the part being exported / opened
        // items drawn with another model of their class ({item id: weapon file}); the persona's items and models
        json weapons = json::object(), weapon_info;
        bool weapons_loading = false, weapon_only = false;
        char weapon_find[64] = "";
        // material values ({part: {material uid: {property hash: value}}}); the part being edited, its materials,
        // the lit preview (engine charmat.render)
        json materials = json::object(), mat_list;
        std::string mat_part, mat_error;
        int mat_sel = 0;
        SpaImage mat_img;
        bool mat_busy = false, mat_highlight = true;
        double mat_dirty = 0;
        float mat_yaw = -1.9f, mat_pitch = 0.15f;
        // edits added as NEW looks / weapon variants instead of replacing the stock ones (part / item id -> texts)
        struct NewText { char name[64] = ""; char desc[192] = ""; };
        std::map<std::string, NewText> new_looks, new_weapons;
        // the Characters workspace (charui.inc) - what is picked (the Inspector shows it, the view outlines it),
        // the undo stack (a snapshot of the edits before each change), the edits the last good build holds
        enum { PICK_PERSONA = 0, PICK_PART, PICK_MATERIAL, PICK_WEAPON, PICK_COLOURS };
        int pick = PICK_PERSONA;
        std::string pick_part;
        json mat_cache = json::object();              // part -> its materials (char_materials), read when wanted
        std::set<std::string> mat_asked;
        struct Snap { json colours, meshes, weapons, materials; std::map<std::string, NewText> new_looks, new_weapons; };
        std::vector<Snap> undo, redo;
        std::string undo_key;
        double undo_t = 0;
        std::string built_key, built_name;
        int ask_switch = -1, load_after_build = -1;   // (a persona asked for while edits aren't built; one to open after a build)
        int bottom_req = -1;                          // (0 Changes, 1 Test builds: brought to the front)
        bool layout_done = false, press_on_view = false;
        int hover = -1;
        std::string pending_mat;                      // (a colour column's material whose part is being found)
        std::map<int, std::vector<float>> posed;      // mesh -> its posed positions (picking while a clip plays)
        V3 eye, centre;
        float vw = 0, vh = 0;
    } chr;
    struct SpaUI {
        bool open = false, asked = false, building = false, test = false;
        json maps = json::array();
        std::vector<std::string> hosts;
        int map = -1, host = 0;
        SpaImage ov, pv;
        float box[4] = {0, 0, 0, 0}, drag0[2] = {0, 0}, street_z = 0, zoom = 1, pan[2] = {0, 0};
        bool has_box = false, dragging = false;
        char name[64] = "";
        json result;
        std::string error;
        double t0 = 0;
    } spa;
    // File > Import a map from another game - the other games' installs (File > Settings), their maps with an import
    // recipe; the recipe run (minutes), then the new map in the Maps list and opened
    struct ImpUI {
        bool open = false, asked = false, building = false, test = false;
        json cat;                                     // {games: [{game, title, found, mp_dir, maps: [...]}], target}
        int game = 0, map = -1;
        char name[64] = "";
        json result;
        std::string error, test_pick;                 // (--import-test: "<GAME>:<map key>")
        double t0 = 0;
        std::string test_dest;                        // (--import-test: the first import, its import.json's write time)
        uint64_t test_stamp = 0;
        bool pick_tab = false;                        // (imp.game was set in code: its tab is selected on the next frame)
    } imp;
    // scatter brush - copies of a palette (the selection's objects, or the Library's model) painted over the
    // ground: turned at random, on the ground (lowest of five samples), clear of each other and of other objects
    struct ScatterSrc { int id = -1, lib = -1; M4 m0 = M4::identity(); V3 lo, hi; std::string name; };
    std::vector<ScatterSrc> scatter_pal;
    bool scattering = false, scatter_painting = false, scatter_changed = false, sc_test = false;
    float scatter_r = 6, scatter_density = 4, scatter_gap = 0.3f, scatter_slope = 25;
    bool scatter_turn = true, scatter_ground = true, scatter_clear = true;
    V3 scatter_last;
    int scatter_made = 0;
    std::mt19937 scatter_rng{20261017u};
    // walk mode - a capsule (0.35 m round, 1.8 m tall) on the map's collision, the camera behind it
    struct WalkTri { V3 a, b, c, n; int owner; };
    struct WalkInput { float fwd = 0, side = 0; bool run = false, slow = false, jump = false; };
    bool walking = false, walk_ground = false, walk_test = false;
    V3 walk_p, walk_v, walk_start;
    float walk_fall = 0, walk_top = 0;                // (the last fall's height; the highest point of it)
    std::vector<WalkTri> walk_tris;
    std::unordered_map<int64_t, std::vector<int>> walk_grid;   // 4 m cells (x, y) -> triangles
    std::vector<ClimbEdge> walk_edges;
    int walk_built_at = -100;
    std::string walk_msg;
    double walk_msg_t = 0;
    bool tg_restore = true, tg_dry = false, tg_test = false;
    bool tg_chr = false;                              // (the test is a character build: its DataPC goes in and back)
    std::string game_exe;
    std::wstring tg_proc = L"AC3MP.exe";
    double tg_t = 0, tg_poll = 0, tg_gone = 0;
    bool tg_restored = false;
    int outliner_show = 0;                            // 0 every object, 1 changed, 2 selected, 3 blend groups, 4 new
    std::string last_map, settings_saved;
    double settings_next = 0;
    const std::vector<int>* pick_skip = nullptr;      // (drop to ground: what the ray passes through)
    bool vp_hovered = false;                          // (the viewport under the mouse: the sculpt brush's ring)
    // the sculpt brush - click raises (Shift lowers), dragging paints; strokes are edits.cur.sculpts
    bool sculpting = false, sculpt_painting = false, gs_test = false;
    float brush_r = 6.0f, brush_h = 1.0f;
    V3 sculpt_last;
    std::vector<LineVert> sculpt_prev_lines, sculpt_prev_tris;   // the bent ground's preview (rebuilt when strokes change)
    std::vector<SculptStroke> sculpt_prev_of;
    float attach_span = 25.0f;                        // Select Attached: the largest structure it grows to (m across)
    ImVec2 marquee_a;
    std::vector<AssetClimb> asset_climb;              // per asset scene index (asset_meta[k]["climb"])
    int blend_sel = -1;                           // Library > Blend groups: the template selected
    bool blend_with_set = true;                   // place a group with its stall (its set), as one placed group
    bool blend_set_test = false;                  // --blend-set-test: a stock merchant group placed with its stall, built
    bool blend_test = false;                      // --blend-test: blend groups moved, taken out, copied, placed, built
    bool climb_test = false;                      // --climb-test: the climbing view on two objects, an object moved
    // the live match view (live.h): the game's player, crowd NPCs, other players and F9 marks
    LiveListener live;
    LiveFrame livef;
    bool live_follow = false, live_show_npcs = true, live_place_pending = false, live_test = false;
    // the Map panel (mapedit.inc) - the map's lighting, kill height, view distance; spawn points, boundaries
    MsView msv;
    std::string ms_curve_filter;
    bool ms_only_changed = false, ms1_test = false;
    json ms_settings() const;
    void ms_put(const std::string& key, const json& v);
    float ms_value(const MsControl& c, bool* all = nullptr) const;
    void ms_set(const MsControl& c, float v, bool all);
    void load_mapsettings();
    void ms_preview_tick();
    void ms_apply_preset(const json& p);
    int ms_n_changes() const;
    void ms_changes_rows();
    void ui_map_panel();
    bool ms_test_tick();
    bool ms2_test_tick();
    MsGame msg;
    int ms_area = -1, ms_corner = -1, gp_sel = -1, ms_test_k = 0;
    bool ms_show_bounds = true, ms_corner_drag = false, ms2_test = false, ms_fold_lighting = false;
    std::string ms_mode_rec() const;
    void load_boundaries();
    void load_gameplay();
    std::vector<V3> ms_corners(int a) const;
    void ms_set_corners(int a, const std::vector<V3>& pts);
    bool ms_area_in_mode(const MsArea& a) const;
    void ms_check_tick();
    int ms_add_spawn(const std::string& kind, V3 at);
    void ms_expand_thing(int hit);
    const MsThing* ms_thing_of(int id) const;
    bool ms_refuse(bool removing);
    const MsTemplate* ms_template_of(int id) const;
    void ms_inspector_note(int id);
    int place_gameplay(int t, V3 at);
    void ui_gameplay_library();
    bool ms_click(ImVec2 mouse);
    bool ms_corner_gizmo();
    bool ms_shortcuts();
    void ms_labels(ImDrawList* dl);
    void ms_draw();
    void ui_map_panel_ms2();
    float live_follow_dist = 14.0f;
    V3 live_place_at;
    double live_test_t0 = 0;
    bool drawing_region = false;                  // + Ground: clicking an area's corners on the ground
    std::vector<V3> draft;                        // its corners so far
    int sel_region = -1;                          // the ground area shown in the Inspector (edits.cur.regions)
    bool shots_mode = false, ground_only = false;   // ground_only: draw only ground objects (holes show); shots_mode: --shots <archive> <folder> x y z dist, a ring of views round a point
    V3 shots_at; float shots_dist = 30; int shot_k = 0;
    std::string pending_name;
    int test_step = -1, test_wait = 0, test_exit = 0, test_placed = -1, test_watch_n = 0;
    std::string test_watch_file;
    std::ofstream test_log, full_log;                            // (full_log: --test's log.txt, everything the Log shows)
    void test_tick();
    void tlog(const std::string& s) { add_log("[test] " + s); if (test_log) { test_log << s << "\n"; test_log.flush(); } }

    void add_log(const std::string& s) {
        if (full_log) { full_log << s << "\n"; full_log.flush(); }
        log_lines.push_back(s);
        if (log_lines.size() > 4000) log_lines.erase(log_lines.begin(), log_lines.begin() + 1000);
        log_scroll = true;
    }

    // ---------------------------------------------------------------------------------------- engine --
    void start_engine() {
        repo = find_repo();
        python = find_python(repo);
        if (repo.empty()) { status = "Can't find the ac3-editor folder (studio\\engine\\worker.py) above the program"; return; }
        if (setup_needed()) { setup_open(); status = "Set up Reforged Studio: point it at your game"; return; }
        engine.on_log = [this](const std::string& s) { add_log(s); };
        if (user_dir.empty()) {
            // a release tree (the packaged program) keeps your assets in your workspace (the program folder is replaced by updates)
            json c = is_release_tree() ? read_config() : json();
            std::string work = c.is_object() ? c.value("workspace", std::string()) : "";
            user_dir = work.empty() ? repo + "\\assets" : work + "\\assets";
        }
        std::string err;
        if (!engine.start(python, repo, err)) { status = err; add_log(err); return; }
        engine.request("hello", json::object(), [this](bool ok, const json& r, const std::string& e) {
            status = ok ? "Engine ready (Python " + r.value("python", std::string("?")) + ")" : "Engine: " + e;
        });
        engine.request("maps", json::object(), [this](bool ok, const json& r, const std::string& e) {
            if (!ok) { add_log("map list: " + e); return; }
            maps.clear();
            for (auto& m : r["maps"]) maps.push_back({m["path"], m["title"], m["source"], m.value("kind", std::string())});
        });
    }

    // ---------------------------------------------------------------------------------------- loading --
    void open_map(const std::string& path) {
        if (load_state == 1 || load_state == 2) return;
        if (workspace) { status = "Close the asset editor first (its Close button on the viewport's toolbar)"; return; }
        if (loader.joinable()) loader.join();
        open_archive = path;
        load_state = 1;
        load_progress = 0;
        status = "Reading " + base_name(path) + " (the first time takes a little while)...";
        engine.request("open", {{"archive", path}}, [this](bool ok, const json& r, const std::string& e) {
            if (!ok) { load_state = 4; load_error = e; status = "Couldn't open the map: " + e; return; }
            std::string cache = r["cache"], archive = r["archive"], title = r["title"];
            // the old scene goes first (its GPU resources too), then a thread reads and uploads the new one
            selection.clear();
            hovered = -1;
            edits.reset(nullptr);
            renderer.release_scene();
            scene.reset();
            load_state = 2;
            loader = std::thread([this, cache, archive, title] {
                auto s = std::make_unique<Scene>();
                std::string err;
                if (!s->load(cache, err)) { load_error = err; load_state = 4; return; }
                s->archive = archive;
                s->title = title;
                int prog = 0;
                renderer.upload(*s, err, &prog);
                loaded = std::move(s);
                load_state = 3;
            });
        });
    }

    void finish_loading() {
        if (load_state == 4) {
            if (loader.joinable()) loader.join();
            status = "Couldn't open the map: " + load_error;
            add_log(status);
            load_state = 0;
            return;
        }
        if (load_state != 3) return;
        if (loader.joinable()) loader.join();
        scene = std::move(loaded);
        edits.reset(scene.get());
        sel_region = -1;
        drawing_region = scattering = false;
        scatter_pal.clear();
        draft.clear();
        navv = NavView();
        sel_route = -1;
        if (show_nav || show_waypoints || show_routes) load_nav();
        blendv = BlendView();
        climbv = ClimbView();
        if (show_climb) load_climb();
        if (show_blend) load_blend();
        msv = MsView();
        msg = MsGame();
        ms_area = ms_corner = gp_sel = -1;
        record_hidden.assign(scene->records.size(), 0);
        mode_record = -1;
        for (size_t i = 0; i < scene->records.size(); i++)
            if (!scene->records[i].label.empty() && scene->records[i].label.find("Manhunt") != std::string::npos) {
                mode_record = (int)i;
                break;
            }
        frame_all();
        project_path.clear();
        last_dest.clear();
        last_build = json();
        holes_vis.clear();
        holes_col.clear();
        plug_tris.clear();
        status = scene->title + ": " + std::to_string(scene->entities.size()) + " objects, " +
                 std::to_string(scene->meshes.size()) + " meshes, " + std::to_string(renderer.textures_loaded) +
                 " textures (" + std::to_string(renderer.texture_bytes >> 20) + " MB)";
        if (renderer.textures_failed) status += ", " + std::to_string(renderer.textures_failed) + " textures not loaded";
        add_log(status);
        load_state = 0;
        refresh_live();
        if (!pending_project.is_null()) {
            std::string p = edits.apply_project(pending_project);
            project_path = pending_project.value("_path", std::string());
            pending_project = json();
            add_log(p.empty() ? "project applied" : "project applied with problems: " + p);
            request_assets();
        }
    }

    void refresh_live() {
        if (!scene || workspace) return;
        engine.request("status", {{"archive", scene->archive}}, [this](bool ok, const json& r, const std::string& e) {
            if (!ok) { live_state = "game: " + e; return; }
            from_release = r.value("from_release", true);
            game_exe = r.value("exe", std::string());
            release_file = r.value("release", std::string());
            live_state = !r.value("exists", false) ? "the game has no such file"
                         : r.value("original", false) ? "the game has the release's map (original)"
                                                       : "the game has a changed map (a test build is installed)";
        });
    }

    // ---------------------------------------------------------------------------------------- objects --
    // an object id's entity, model and scene: the map's, or (library models) the asset scene's
    int asset_slot(int id) const {
        int a = edits.asset_of(id);
        if (a < 0 || !assets_scene) return -1;
        auto it = assets_at.find(edits.assets[a].key);
        return it == assets_at.end() ? -1 : it->second;
    }
    const Scene* scene_of(int id) const { return edits.is_placed(id) ? assets_scene.get() : scene.get(); }
    const Entity& ent(int id) const {
        if (!edits.is_placed(id)) return scene->entities[edits.entity_of(id)];
        int k = asset_slot(id);
        if (k >= 0) return assets_scene->entities[k];
        placeholder = Entity();
        placeholder.name = edits.assets[edits.asset_of(id)].name;
        return placeholder;
    }
    const Mesh* mesh_of(int id) const {
        const Entity& e = ent(id);
        const Scene* s = scene_of(id);
        return (s && e.mesh >= 0 && e.mesh < (int)s->meshes.size()) ? &s->meshes[e.mesh] : nullptr;
    }
    std::string label_of(int id) const {
        if (edits.is_placed(id)) {
            const AssetRef& a = edits.assets[edits.asset_of(id)];
            return "[library] " + a.name + " (" + a.map + ")";
        }
        return (edits.is_copy(id) ? "[copy] " : "") + ent(id).name;
    }

    bool visible(int id) const {
        if (edits.is_placed(id)) return !edits.removed(id) || show_removed;
        const Entity& e = scene->entities[edits.entity_of(id)];
        if (ground_only && e.name.find("Ground") == std::string::npos) return false;
        if (edits.removed(id) && !show_removed) return false;
        if (e.record >= 0 && record_hidden[e.record]) return false;
        if (e.flags & F_MODE) {
            if (mode_record != e.record) return false;
        }
        if ((e.flags & F_VOLUME) && !show_volumes) return false;
        if (e.mesh < 0 && !show_markers) return false;
        return true;
    }

    float marker_size(const Entity& e) const {
        if (e.flags & F_START) return 1.4f;
        if (e.flags & F_SPAWN) return 0.9f;
        return 0.35f;
    }

    void world_box(int id, V3& lo, V3& hi) {
        const Entity& e = ent(id);
        const M4& m = edits.matrix(id);
        if (const Mesh* mp = mesh_of(id)) {
            const Mesh& me = *mp;
            lo = V3(1e30f, 1e30f, 1e30f); hi = V3(-1e30f, -1e30f, -1e30f);
            for (int c = 0; c < 8; c++) {
                V3 p(c & 1 ? me.hi.x : me.lo.x, c & 2 ? me.hi.y : me.lo.y, c & 4 ? me.hi.z : me.lo.z);
                V3 w = m.point(p);
                lo = vmin(lo, w); hi = vmax(hi, w);
            }
        } else {
            float s = marker_size(e) * 0.5f;
            lo = m.pos() - V3(s, s, s); hi = m.pos() + V3(s, s, s);
        }
    }

    void frame_box(V3 lo, V3 hi) {
        V3 c = (lo + hi) * 0.5f;
        float r = std::max(2.0f, length(hi - lo) * 0.5f);
        float d = r / std::tan(fov * 3.14159f / 360.0f) * 1.1f;
        cam.pos = c - cam.forward() * d;
    }

    void frame_all() {
        if (workspace) {                              // the asset editor: every part, or the grid's middle
            V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
            bool any = false;
            for (int id = 0; id < edits.count(); id++)
                if (!edits.removed(id)) { V3 a, b; world_box(id, a, b); lo = vmin(lo, a); hi = vmax(hi, b); any = true; }
            if (!any) { lo = V3(-5, -5, 0); hi = V3(5, 5, 3); }
            cam.yaw = 0.95f;
            cam.pitch = -0.45f;
            frame_box(lo, hi);
            cam.speed = std::clamp(length(hi - lo) * 0.15f, 4.0f, 40.0f);
            return;
        }
        // the map's middle half (ocean planes and far backdrops would make "everything" tiny)
        std::vector<float> xs, ys, zs;
        for (size_t i = 0; i < scene->entities.size(); i++) {
            const Entity& e = scene->entities[i];
            if (e.mesh < 0 || (e.flags & (F_MODE | F_VOLUME))) continue;
            V3 p = e.matrix.pos();
            xs.push_back(p.x); ys.push_back(p.y); zs.push_back(p.z);
        }
        if (xs.empty()) return;
        auto q = [](std::vector<float>& v, float f) {
            std::nth_element(v.begin(), v.begin() + (size_t)(f * (v.size() - 1)), v.end());
            return v[(size_t)(f * (v.size() - 1))];
        };
        V3 lo(q(xs, 0.1f), q(ys, 0.1f), q(zs, 0.1f)), hi(q(xs, 0.9f), q(ys, 0.9f), q(zs, 0.9f));
        cam.yaw = 1.5708f;
        cam.pitch = -0.75f;
        frame_box(lo, hi);
        cam.speed = std::clamp(length(hi - lo) * 0.15f, 8.0f, 60.0f);
    }

    void frame_selection() {
        if (selection.empty()) return;
        V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
        for (int id : selection) {
            V3 a, b;
            world_box(id, a, b);
            lo = vmin(lo, a); hi = vmax(hi, b);
        }
        frame_box(lo, hi);
    }

    // ---- ground areas raised / lowered (engine plan.py "regions": the corners' convex hull goes up / down by dz)
    static std::vector<V3> hull2d(std::vector<V3> p) {
        std::sort(p.begin(), p.end(), [](const V3& a, const V3& b) { return a.x < b.x || (a.x == b.x && a.y < b.y); });
        if (p.size() < 3) return p;
        auto turn = [](const V3& o, const V3& a, const V3& b) { return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x); };
        std::vector<V3> h(2 * p.size());
        size_t k = 0;
        for (size_t i = 0; i < p.size(); i++) {
            while (k >= 2 && turn(h[k - 2], h[k - 1], p[i]) <= 0) k--;
            h[k++] = p[i];
        }
        for (size_t i = p.size() - 1, t = k + 1; i-- > 0;) {
            while (k >= t && turn(h[k - 2], h[k - 1], p[i]) <= 0) k--;
            h[k++] = p[i];
        }
        h.resize(k - 1);
        return h;
    }
    static float area2d(const std::vector<V3>& h) {
        float a = 0;
        for (size_t i = 0; i < h.size(); i++) a += h[i].x * h[(i + 1) % h.size()].y - h[(i + 1) % h.size()].x * h[i].y;
        return std::fabs(a) / 2;
    }
    bool region_ok(int k) const {
        return scene && k >= 0 && k < (int)edits.cur.regions.size() && !edits.cur.regions[k].removed;
    }
    void load_nav() {
        if (!scene || navv.loaded || navv.loading) return;
        navv.loading = true;
        std::string archive = scene->archive;
        engine.request("nav", {{"archive", archive}}, [this, archive](bool ok, const json& r, const std::string& e) {
            navv.loading = false;
            if (!scene || scene->archive != archive) return;
            if (!ok) { status = "Navmesh: " + e; return; }
            NavView v;
            for (auto& x : r["edges"]) {
                v.ea.push_back(V3(x[0], x[1], x[2]));
                v.eb.push_back(V3(x[3], x[4], x[5]));
                v.ekind.push_back(x[6].get<int>());
            }
            for (auto& x : r["wps"]) v.wps.push_back(V3(x[0], x[1], x[2]));
            for (auto& x : r["links"]) v.links.push_back({x[0].get<int>(), x[1].get<int>()});
            for (auto& f : r["flows"]) {
                NavRoute rt;
                rt.uid = f.value("uid", std::string());
                rt.name = f.value("name", std::string());
                rt.width = f.value("width", 4.0f);
                rt.active = f.value("active", true);
                for (auto& p : f["points"]) rt.pts.push_back(V3(p[0], p[1], p[2]));
                rt.head = (int)f["head"].size();
                rt.tail = (int)f["tail"].size();
                v.routes.push_back(rt);
            }
            v.cells = r.value("cells", 0);
            v.tris = r.value("triangles", 0);
            v.loaded = true;
            navv = std::move(v);
            status = "Navmesh: " + std::to_string(navv.tris) + " triangles in " + std::to_string(navv.cells) + " cells, " +
                     std::to_string(navv.wps.size()) + " waypoints, " + std::to_string(navv.routes.size()) + " crowd routes";
        });
    }
    bool route_ok(int k) const { return navv.loaded && k >= 0 && k < (int)navv.routes.size(); }
    bool route_active(int k) const {
        auto it = edits.cur.routes.find(navv.routes[k].uid);
        return it != edits.cur.routes.end() && it->second.active >= 0 ? it->second.active == 1 : navv.routes[k].active;
    }
    float route_width(int k) const {
        auto it = edits.cur.routes.find(navv.routes[k].uid);
        return it != edits.cur.routes.end() && it->second.width >= 0 ? it->second.width : navv.routes[k].width;
    }
    void set_route(int k, int active, float width) {             // (as the map has it: the edit dropped)
        RouteEdit e = edits.cur.routes.count(navv.routes[k].uid) ? edits.cur.routes[navv.routes[k].uid] : RouteEdit();
        if (active >= 0) e.active = (active == 1) == navv.routes[k].active ? -1 : active;
        if (width >= 0) e.width = std::fabs(width - navv.routes[k].width) < 1e-3f ? -1.0f : width;
        if (e.active < 0 && e.width < 0) edits.cur.routes.erase(navv.routes[k].uid);
        else edits.cur.routes[navv.routes[k].uid] = e;
    }
    // ---- drop to the ground, settings
    // the selection lowered (or raised) as one so its bottom sits on what is under its middle - the selection itself
    // not counted (a crate floating after its table moved, a copied structure placed in the air)
    void drop_to_ground() {
        if (!scene || selection.empty()) return;
        std::vector<int> movers;
        for (int id : selection)
            if (edits.movable(id) && !edits.removed(id)) movers.push_back(id);
        if (movers.empty()) return;
        V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
        for (int id : movers) { V3 a, b; world_box(id, a, b); lo = vmin(lo, a); hi = vmax(hi, b); }
        V3 c((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, hi.z + 0.5f);
        float t = 0;
        pick_skip = &movers;
        int hit = pick(c, V3(0, 0, -1), &t);
        pick_skip = nullptr;
        if (hit < 0 || t > 1000) { status = "Drop to the ground: nothing under the selection's middle"; return; }
        float dz = (c.z - t) - lo.z;
        for (int id : movers) edits.matrix(id).set_pos(edits.matrix(id).pos() + V3(0, 0, dz));
        edits.commit();
        status = std::to_string(movers.size()) + " object(s) " + (dz < 0 ? "lowered " : "raised ") + std::to_string(std::fabs(dz)).substr(0, 4) +
                 " m onto " + label_of(hit);
    }
    std::string settings_path() const {
        wchar_t buf[MAX_PATH];
        DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", buf, MAX_PATH);
        if (!n || n >= MAX_PATH) return "";
        std::string cur = narrow(buf) + "\\ReforgedStudio\\settings.json", old = narrow(buf) + "\\AC3Studio\\settings.json";
        return file_exists(old) && !file_exists(cur) ? old : cur;  // (settings under the older file name are still read)
    }
    json settings_json() const {
        return {{"show_collision", show_collision}, {"show_markers", show_markers}, {"show_volumes", show_volumes},
                {"show_labels", show_labels}, {"show_removed", show_removed}, {"alpha_clip", alpha_clip},
                {"show_climb", show_climb}, {"show_blend", show_blend}, {"show_nav", show_nav},
                {"show_waypoints", show_waypoints}, {"show_routes", show_routes}, {"fov", fov}, {"cam_speed", cam.speed},
                {"brush_r", brush_r}, {"brush_h", brush_h}, {"attach_span", attach_span}, {"carry_ground", carry_ground},
                {"snap", snap}, {"snap_move", snap_move}, {"snap_turn", snap_turn}, {"gizmo_local", gizmo_local},
                {"all_axes", all_axes}, {"live_follow_dist", live_follow_dist}, {"lib_hide_ground", lib_hide_ground},
                {"last_map", last_map}, {"tg_restore", tg_restore},
                {"sp_wanted", std::vector<std::string>(sp_wanted.begin(), sp_wanted.end())},
                {"scatter_r", scatter_r}, {"scatter_density", scatter_density}, {"scatter_gap", scatter_gap},
                {"scatter_slope", scatter_slope}, {"scatter_turn", scatter_turn}, {"scatter_ground", scatter_ground},
                {"scatter_clear", scatter_clear}};
    }
    void load_settings() {
        std::string p = settings_path();
        if (p.empty()) return;
        std::ifstream f(widen(p), std::ios::binary);
        if (!f) return;
        std::stringstream ss;
        ss << f.rdbuf();
        json j = json::parse(ss.str(), nullptr, false);
        if (j.is_discarded() || !j.is_object()) return;
        auto b = [&](const char* k, bool& v) { if (j.contains(k) && j[k].is_boolean()) v = j[k].get<bool>(); };
        auto fl = [&](const char* k, float& v) { if (j.contains(k) && j[k].is_number()) v = j[k].get<float>(); };
        b("show_collision", show_collision); b("show_markers", show_markers); b("show_volumes", show_volumes);
        b("show_labels", show_labels); b("show_removed", show_removed); b("alpha_clip", alpha_clip);
        b("show_climb", show_climb); b("show_blend", show_blend); b("show_nav", show_nav);
        b("show_waypoints", show_waypoints); b("show_routes", show_routes); b("carry_ground", carry_ground);
        b("tg_restore", tg_restore);
        b("snap", snap); b("gizmo_local", gizmo_local); b("all_axes", all_axes); b("lib_hide_ground", lib_hide_ground);
        fl("fov", fov); fl("cam_speed", cam.speed); fl("brush_r", brush_r); fl("brush_h", brush_h);
        fl("attach_span", attach_span); fl("snap_move", snap_move); fl("snap_turn", snap_turn);
        fl("live_follow_dist", live_follow_dist);
        fl("scatter_r", scatter_r); fl("scatter_density", scatter_density); fl("scatter_gap", scatter_gap);
        fl("scatter_slope", scatter_slope); b("scatter_turn", scatter_turn); b("scatter_ground", scatter_ground);
        b("scatter_clear", scatter_clear);
        last_map = j.value("last_map", std::string());
        for (auto& a : j.value("sp_wanted", json::array())) if (a.is_string()) sp_wanted.insert(a.get<std::string>());
        settings_saved = settings_json().dump();
    }
    // every 2 s: written when something changed (and at exit)
    void save_settings(bool now = false) {
        if (!test_dir.empty()) return;                // (self-tests don't touch the user's settings)
        if (!now && ImGui::GetTime() < settings_next) return;
        settings_next = ImGui::GetTime() + 2.0;
        if (scene && !workspace) last_map = scene->archive;
        std::string d = settings_json().dump(1);
        if (d == settings_saved) return;
        std::string p = settings_path();
        if (p.empty()) return;
        make_dirs(widen(p.substr(0, p.rfind('\\'))));
        std::ofstream f(widen(p), std::ios::binary);
        if (f) { f << d; settings_saved = d; }
    }

    // ---- sculpting
    static float sculpt_falloff(float t) { t = std::clamp(t, 0.0f, 1.0f); return 0.5f * (1.0f + std::cos(3.14159265f * t)); }
    float sculpt_dz(float x, float y) const {
        float d = 0;
        for (auto& st : edits.cur.sculpts) d += st.h * sculpt_falloff(std::sqrt((x - st.x) * (x - st.x) + (y - st.y) * (y - st.y)) / st.r);
        return d;
    }
    void start_sculpt() {
        if (!scene || workspace) return;
        sculpting = !sculpting;
        drawing_region = scattering = false;
        status = sculpting ? "Sculpt: click the ground to raise it (Shift+click lowers), drag to paint; radius and height in "
                             "the Inspector; Backspace takes the last stroke back, Esc ends"
                           : "Sculpt ended - Build bends the ground (and what stands on it) as drawn";
    }
    void add_stroke(V3 at, float h) {
        SculptStroke st;
        st.x = at.x; st.y = at.y; st.z = at.z; st.r = brush_r; st.h = h;
        edits.cur.sculpts.push_back(st);
        sculpt_last = at;
    }
    // the ground under the mouse (the first thing it meets), or false
    bool mouse_hit(V3& out) {
        ImVec2 mp = ImGui::GetIO().MousePos;
        float nx = (mp.x - vp_pos.x) / vp_size.x * 2 - 1, ny = 1 - (mp.y - vp_pos.y) / vp_size.y * 2;
        M4 inv = inverse(view_m * proj_m);
        auto unproj = [&](float z) {
            float x = nx * inv.m[0] + ny * inv.m[4] + z * inv.m[8] + inv.m[12];
            float y = nx * inv.m[1] + ny * inv.m[5] + z * inv.m[9] + inv.m[13];
            float zz = nx * inv.m[2] + ny * inv.m[6] + z * inv.m[10] + inv.m[14];
            float w = nx * inv.m[3] + ny * inv.m[7] + z * inv.m[11] + inv.m[15];
            return V3(x / w, y / w, zz / w);
        };
        V3 a = unproj(0), b = unproj(1);
        float t = 0;
        if (pick(a, normalize(b - a), &t) < 0 || t > 2000) return false;
        out = a + normalize(b - a) * t;
        return true;
    }
    // the bent ground's preview: the map's ground pieces under the strokes, split to 1.5 m, raised / lowered by dz
    void sculpt_preview() {
        if (edits.cur.sculpts == sculpt_prev_of) return;
        sculpt_prev_of = edits.cur.sculpts;
        sculpt_prev_lines.clear();
        sculpt_prev_tris.clear();
        if (edits.cur.sculpts.empty() || !scene) return;
        V3 lo(1e30f, 1e30f, 0), hi(-1e30f, -1e30f, 0);
        for (auto& st : edits.cur.sculpts) {
            lo.x = std::min(lo.x, st.x - st.r); lo.y = std::min(lo.y, st.y - st.r);
            hi.x = std::max(hi.x, st.x + st.r); hi.y = std::max(hi.y, st.y + st.r);
        }
        std::vector<std::array<V3, 3>> work;
        for (size_t i = 0; i < scene->entities.size(); i++) {
            const Entity& e = scene->entities[i];
            if (e.mesh < 0 || e.name.find("Ground") == std::string::npos || edits.removed((int)i)) continue;
            std::string up = e.name;
            for (auto& c : up) c = (char)toupper((unsigned char)c);
            if (up.find("WATER") != std::string::npos) continue;
            const Mesh& me = scene->meshes[e.mesh];
            const M4& m = edits.matrix((int)i);
            for (uint32_t t = 0; t < me.nt; t++) {
                std::array<V3, 3> tri;
                bool close_by = false;
                for (int j = 0; j < 3; j++) {
                    uint32_t ix = me.idx[t * 3 + j];
                    tri[j] = m.point(V3(me.pos[ix * 3], me.pos[ix * 3 + 1], me.pos[ix * 3 + 2]));
                }
                V3 c = (tri[0] + tri[1] + tri[2]) * (1.0f / 3);
                float rad = std::max({length(tri[0] - c), length(tri[1] - c), length(tri[2] - c)});
                close_by = c.x + rad >= lo.x && c.x - rad <= hi.x && c.y + rad >= lo.y && c.y - rad <= hi.y;
                if (close_by) work.push_back(tri);
            }
        }
        for (size_t k = 0; k < work.size() && work.size() < 200000; k++) {    // longest-edge halving to 1.5 m
            auto& t = work[k];
            int li = 0;
            float best = 0;
            for (int j = 0; j < 3; j++) {
                V3 d = t[(j + 1) % 3] - t[j];
                float l = std::sqrt(d.x * d.x + d.y * d.y);
                if (l > best) { best = l; li = j; }
            }
            if (best <= 1.5f) continue;
            V3 a = t[li], b = t[(li + 1) % 3], c = t[(li + 2) % 3], mid = (a + b) * 0.5f;
            t = {a, mid, c};
            work.push_back({mid, b, c});
            k--;                                       // (this one again: it may still be too long)
        }
        for (auto& t : work) {
            float d[3];
            bool any = false;
            for (int j = 0; j < 3; j++) { d[j] = sculpt_dz(t[j].x, t[j].y); any |= std::fabs(d[j]) > 0.01f; }
            if (!any) continue;
            V3 q[3];
            for (int j = 0; j < 3; j++) q[j] = t[j] + V3(0, 0, d[j] + 0.03f);
            float avg = (d[0] + d[1] + d[2]) / 3;
            uint32_t fill = avg >= 0 ? rgba(0.35f, 0.95f, 0.45f, 0.28f) : rgba(0.3f, 0.6f, 1.0f, 0.28f);
            uint32_t wire = avg >= 0 ? rgba(0.45f, 1.0f, 0.55f, 0.55f) : rgba(0.45f, 0.7f, 1.0f, 0.55f);
            for (int j = 0; j < 3; j++) sculpt_prev_tris.push_back({q[j], fill});
            for (int j = 0; j < 3; j++) { sculpt_prev_lines.push_back({q[j], wire}); sculpt_prev_lines.push_back({q[(j + 1) % 3], wire}); }
        }
    }

    // ---- climbing
    void load_climb() {
        if (!scene || workspace || climbv.loaded || climbv.loading) return;
        climbv.loading = true;
        std::string archive = scene->archive;
        engine.request("climb", {{"archive", archive}}, [this, archive](bool ok, const json& r, const std::string& e) {
            climbv.loading = false;
            if (!scene || scene->archive != archive) return;
            if (!ok) { status = "Climbing: " + e; return; }
            climbv.read(r, *scene);
            status = "Climbing: " + std::to_string(climbv.edges.size()) + " climb edges; " +
                     std::to_string(climbv.n_noclimb) + " of " + std::to_string(climbv.n_tris) +
                     " collision triangles are no-climb (red when selected)";
        });
    }
    // the climb edges an object holds, where it stands now: a map object's own (moved with it), a copy's (its source's,
    // at the copy), a library model's own (when its climbing is "as in its own map" / outside)
    void climb_edges_of(int id, std::vector<ClimbEdge>& out) {
        auto put = [&](ClimbEdge e, const M4& m) {
            e.a = m.point(e.a);
            e.b = m.point(e.b);
            V3 h = m.dir(V3(e.hx, e.hy, 0));
            e.hx = h.x;
            e.hy = h.y;
            out.push_back(e);
        };
        if (edits.is_placed(id)) {
            int k = asset_slot(id);
            if (k < 0 || k >= (int)asset_climb.size()) return;
            const CopyObj& co = edits.cur.copies[id - scene->entities.size()];
            int cl = co.climb >= 0 ? co.climb : default_climb(edits.assets[co.asset].key);
            if (cl != CLIMB_NATIVE && cl != CLIMB_OUTSIDE) return;
            for (auto& e : asset_climb[k].edges) put(e, edits.matrix(id));
            return;
        }
        int src = edits.entity_of(id);
        if (src < 0 || !climbv.loaded) return;
        auto it = climbv.by_owner.find(src);
        if (it == climbv.by_owner.end()) return;
        M4 d = inverse(scene->entities[src].matrix) * edits.matrix(id);    // (row vectors: as the map has it -> now)
        for (int k : it->second) put(climbv.edges[k], d);
    }
    // the climb edges on / at an object: its own and those within 0.3 m of its box (a building's pieces' edges are
    // held by the cell's merged object)
    int climb_edges_at(int id, int* poles = nullptr) {
        std::vector<ClimbEdge> es;
        climb_edges_of(id, es);
        if (!edits.is_placed(id) && climbv.loaded) {
            V3 lo, hi;
            world_box(id, lo, hi);
            lo = lo - V3(0.3f, 0.3f, 0.3f);
            hi = hi + V3(0.3f, 0.3f, 0.3f);
            int src = edits.entity_of(id);
            for (auto& e : climbv.edges) {
                if (e.owner == src) continue;
                if (e.owner >= 0 && (edits.removed(e.owner) || edits.moved(e.owner))) continue;
                V3 m = (e.a + e.b) * 0.5f;
                if (m.x >= lo.x && m.x <= hi.x && m.y >= lo.y && m.y <= hi.y && m.z >= lo.z && m.z <= hi.z) es.push_back(e);
            }
        }
        if (poles) {
            *poles = 0;
            for (auto& e : es) *poles += e.kind != 1;
        }
        return (int)es.size();
    }
    // an object's solid collision triangles, each with whether it is no-climb (FuzzyZone, Z_Camonly): -> its own
    // shapes, else (a building's merged piece) its member shapes in the cell's merged object; world space
    template <class F> void collision_tris(int id, F fn) {
        const Scene* s = scene_of(id);
        const Entity& e = ent(id);
        const M4& m = edits.matrix(id);
        int k = edits.is_placed(id) ? asset_slot(id) : -1;
        std::vector<int> shapes;
        for (int sh : e.shapes)
            if (sh >= 0 && sh < (int)s->shapes.size() && s->shapes[sh].layer == 1) shapes.push_back(sh);
        if (shapes.empty() && !edits.is_placed(id) && climbv.loaded) {
            auto it = climbv.members.find(edits.entity_of(id));
            if (it != climbv.members.end())
                for (int sh : it->second)
                    if (sh >= 0 && sh < (int)s->shapes.size() && s->shapes[sh].layer == 1) shapes.push_back(sh);
        }
        for (int sh : shapes) {
            const Shape& S = s->shapes[sh];
            for (uint32_t t = 0; t < S.nt; t++) {
                bool no = edits.is_placed(id) ? (k >= 0 && k < (int)asset_climb.size() && asset_climb[k].tri_noclimb((int)t))
                                              : climbv.tri_noclimb(sh, (int)t);
                V3 p[3];
                for (int j = 0; j < 3; j++) {
                    uint32_t ix = S.idx[t * 3 + j];
                    p[j] = m.point(V3(S.pos[ix * 3], S.pos[ix * 3 + 1], S.pos[ix * 3 + 2]));
                }
                fn(p, no);
            }
        }
    }
    struct ClimbSummary { int edges = 0, poles = 0; float area = 0, noclimb = 0; bool collision = false; };
    ClimbSummary climb_summary(int id) {
        ClimbSummary c;
        c.edges = climb_edges_at(id, &c.poles);
        collision_tris(id, [&](const V3* p, bool no) {
            float a = length(cross(p[1] - p[0], p[2] - p[0])) * 0.5f;
            c.area += a;
            if (no) c.noclimb += a;
            c.collision = true;
        });
        return c;
    }

    // ---- the live match view
    void live_toggle() {
        if (live.running()) { live.stop(); live_follow = false; status = "Live: stopped"; return; }
        std::string err;
        if (!live.start(47710, err)) { status = "Live: " + err; add_log(status); return; }
        status = "Live: listening on 127.0.0.1:47710 - start the game with the live_map lever";
        add_log(status);
    }
    bool live_fresh() const { return live.running() && livef.received > 0 && live.now() - livef.received < 2.0; }
    void live_tick() {
        if (!live.running()) return;
        livef = live.snapshot();
        if (live_follow && live_fresh() && livef.has_me && !workspace) {
            // the camera keeps its heading and pitch, looking at the player from live_follow_dist
            V3 target = livef.me + V3(0, 0, 1.2f);
            cam.pos = target - cam.forward() * live_follow_dist;
        }
    }
    void live_frame_mark(int k) {
        if (k < 0 || k >= (int)livef.marks.size()) return;
        V3 p = livef.marks[k];
        frame_box(p - V3(2, 2, 0), p + V3(2, 2, 2));
    }

    // ---- blend groups
    void load_blend() {
        if (!scene || workspace || blendv.loaded || blendv.loading) return;
        blendv.loading = true;
        std::string archive = scene->archive;
        auto read = [](const json& r) {
            BlendInfo b;
            b.uid = uid_from_hex(r.value("uid", std::string("00000000")));
            b.key = r.value("key", std::string());
            b.name = r.value("name", std::string());
            b.label = r.value("label", std::string());
            b.map = r.value("map", std::string());
            for (auto& p : r.value("props", json::array())) b.props += (b.props.empty() ? "" : ", ") + p.get<std::string>();
            for (auto& p : r.value("missing", json::array())) b.missing += (b.missing.empty() ? "" : ", ") + p.get<std::string>();
            auto m = r.value("matrix", json::array());
            if (m.size() == 16) for (int i = 0; i < 16; i++) b.matrix.m[i] = m[i].get<float>();
            for (auto& s : r.value("slots", json::array()))
                b.slots.push_back({V3(s[0], s[1], s[2]), V3(s[3], s[4], s[5])});
            for (auto& q : r.value("set", json::array())) {
                BlendPiece pc;
                pc.piece = q.value("piece", std::string());
                pc.name = q.value("name", std::string());
                pc.key = q.value("key", std::string());
                pc.map = q.value("map", std::string());
                auto pm = q.value("matrix", json::array());
                if (pm.size() == 16) for (int i = 0; i < 16; i++) pc.matrix.m[i] = pm[i].get<float>();
                b.set.push_back(pc);
            }
            return b;
        };
        engine.request("blend", {{"archive", archive}}, [this, archive, read](bool ok, const json& r, const std::string& e) {
            blendv.loading = false;
            if (!scene || scene->archive != archive) return;
            if (!ok) { status = "Blend groups: " + e; return; }
            BlendView v;
            for (auto& g : r["groups"]) { v.by_uid[uid_from_hex(g["uid"].get<std::string>())] = (int)v.groups.size(); v.groups.push_back(read(g)); }
            for (auto& t : r["templates"]) { v.by_key[t["key"].get<std::string>()] = (int)v.templates.size(); v.templates.push_back(read(t)); }
            v.loaded = true;
            blendv = std::move(v);
            add_log("blend groups: " + std::to_string(blendv.groups.size()) + " on this map, " +
                    std::to_string(blendv.templates.size()) + " stock ones to place (Library > Blend groups)");
        });
    }
    static bool is_blend_key(const std::string& k) { return k.rfind("blend:", 0) == 0; }
    // the blend group an object is (the map's, a copy of one, a placed stock one), or null
    const BlendInfo* blend_of(int id) const {
        if (!blendv.loaded || !scene || id < 0 || id >= edits.count()) return nullptr;
        if (edits.is_placed(id)) {
            const std::string& k = edits.assets[edits.asset_of(id)].key;
            if (!is_blend_key(k)) return nullptr;
            auto it = blendv.by_key.find(k);
            if (it != blendv.by_key.end()) return &blendv.templates[it->second];
            auto ia = blend_assets.find(k);
            return ia == blend_assets.end() ? nullptr : &ia->second;
        }
        int src = edits.entity_of(id);
        if (src < 0) return nullptr;
        auto it = blendv.by_uid.find(scene->entities[src].uid);
        return it == blendv.by_uid.end() ? nullptr : &blendv.groups[it->second];
    }
    // the open map's own object a stock one is (same uid, same name / model, in a cell), or -1
    int own_copy_source(uint32_t uid, const std::string& name, uint32_t mesh_uid) const {
        auto it = edits.by_uid.find(uid);
        if (it == edits.by_uid.end()) return -1;
        const Entity& e = scene->entities[it->second];
        if (scene->records[e.record].name.rfind("Cell", 0) != 0) return -1;
        if (!name.empty() && e.name != name) return -1;
        if (mesh_uid && (e.mesh < 0 || scene->meshes[e.mesh].uid != mesh_uid)) return -1;
        return it->second;
    }
    // a stock blend group with its set (a merchant's stall...) at `at`, all one placed group - a click picks the
    // whole, a double-click one piece. The group is part "p0", the pieces "p1".. (as a user asset's parts): this map's
    // own objects are copies (their own collision and climb data), another map's are library models
    std::vector<int> place_blend_set(int t, V3 at) {
        std::vector<int> made;
        const BlendInfo& b = blendv.templates[t];
        M4 T = translation(at);
        int g = edits.add_group(b.label + " with its " + b.set[0].piece + " #" + std::to_string(edits.groups.size() + 1), "", T);
        auto add = [&](CopyObj o, const std::string& path, const std::string& name) {
            o.group = g;
            o.path = path;
            edits.groups[g].part_names[path] = name;
            edits.cur.copies.push_back(o);
            made.push_back((int)scene->entities.size() + (int)edits.cur.copies.size() - 1);
        };
        M4 m = b.matrix;
        m.set_pos(at);
        int own = workspace ? -1 : own_copy_source(b.uid, b.name, 0);
        if (own >= 0) add({own, m, false}, "p0", b.name);
        else add({-1, m, false, edits.asset_index(b.key, b.label + " (blend group)", b.map)}, "p0", b.name);
        int copies = own >= 0, models = own < 0;
        for (size_t k = 0; k < b.set.size(); k++) {
            const BlendPiece& pc = b.set[k];
            M4 pm = pc.matrix * T;
            std::string mesh = pc.key.substr(pc.key.rfind('|') + 1), ent;
            if (size_t h = mesh.find('#'); h != std::string::npos) { ent = mesh.substr(h + 1); mesh = mesh.substr(0, h); }
            int src = workspace || ent.empty() ? -1 : own_copy_source(uid_from_hex(ent), pc.name, uid_from_hex(mesh));
            std::string path = "p" + std::to_string(k + 1);
            if (src >= 0) { add({src, pm, false}, path, pc.name); copies++; }
            else { add({-1, pm, false, edits.asset_index(pc.key, pc.name, pc.map)}, path, pc.name); models++; }
        }
        edits.commit();
        selection = made;
        scope_group = g;
        scope_depth = 0;
        gizmo_op = ImGuizmo::TRANSLATE;
        request_assets();
        std::string pieces;
        for (size_t k = 0; k < b.set.size(); k++)
            pieces += (k == 0 ? "" : k + 1 == b.set.size() ? " and " : ", ") + b.set[k].piece;
        status = b.label + " from " + b.map + " placed with its " + pieces + " (" + std::to_string(copies) +
                 " copies of this map's objects, " + std::to_string(models) + " from " + b.map + ") - one group: a click "
                 "picks the whole, a double-click one piece; Build copies the group in" +
                 (b.props.empty() ? std::string() : " (the " + b.props + " its NPCs hold)");
        add_log(status);
        return made;
    }
    // a stock blend group into the open map at `at` (as it stands in its own map, turned to the camera's heading):
    // this map's own group -> a copy of it; another map's -> a placed group (the build copies it with fresh ids).
    // with its set (blend_with_set, default on) when its kind has one - the group and its stall as one group
    int place_blend(int t, V3 at) {
        if (!scene || t < 0 || t >= (int)blendv.templates.size()) return -1;
        const BlendInfo& b = blendv.templates[t];
        if (blend_with_set && !b.set.empty()) {
            std::vector<int> made = place_blend_set(t, at);
            return made.empty() ? -1 : made[0];
        }
        M4 m = b.matrix;
        m.set_pos(at);
        auto it = edits.by_uid.find(b.uid);
        int made;
        if (it != edits.by_uid.end() && scene->entities[it->second].name == b.name &&
            scene->records[scene->entities[it->second].record].name.rfind("Cell", 0) == 0) {
            edits.cur.copies.push_back({it->second, m, false});
            status = b.label + ": this map's own blend group - a copy of " + b.name;
        } else {
            CopyObj o{-1, m, false, edits.asset_index(b.key, b.label + " (blend group)", b.map)};
            edits.cur.copies.push_back(o);
            request_assets();
            status = b.label + " from " + b.map + " placed - move / turn it with the gizmo; Build copies it in (its NPC "
                     "slots, their specs" + (b.props.empty() ? std::string() : ", the " + b.props + " they hold") + ")";
        }
        made = (int)scene->entities.size() + (int)edits.cur.copies.size() - 1;
        edits.commit();
        selection = {made};
        gizmo_op = ImGuizmo::TRANSLATE;
        add_log(status);
        return made;
    }

    static const char* area_word(int kind) {
        return kind == AREA_NAV_REMAKE ? "navmesh remake" : kind == AREA_NAV_BLOCK ? "no crowds" : "ground";
    }
    void start_region(int kind = AREA_GROUND) {
        drawing_region = true;
        draw_kind = kind;
        draft.clear();
        selection.clear();
        sel_region = -1;
        sel_route = -1;
        status = std::string(kind == AREA_GROUND ? "Raise / lower ground" : kind == AREA_NAV_REMAKE ? "Remake the navmesh in an area"
                             : kind == AREA_CLEAR ? "Clear an area" : "Keep crowds out of an area") +
                 ": click the area's corners on the ground, Enter (or double-click) to finish, Backspace drops the last "
                 "corner, Esc cancels";
    }
    void cancel_region() {
        drawing_region = false;
        draft.clear();
        status = "Ground area cancelled";
    }
    void finish_region() {
        if (hull2d(draft).size() < 3 || area2d(hull2d(draft)) < 1.0f) {
            status = "A ground area needs at least 3 corners round 1 m2 or more - keep clicking, or Esc";
            return;
        }
        if (draw_kind == AREA_CLEAR) {                 // not a stored area - what's in it goes
            std::vector<V3> h = hull2d(draft);
            drawing_region = false;
            draft.clear();
            bare_clear(&h);
            return;
        }
        RegionEdit r;
        r.kind = draw_kind;
        r.name = std::string(draw_kind == AREA_GROUND ? "ground " : draw_kind == AREA_NAV_REMAKE ? "navmesh " : "no crowds ") +
                 std::to_string(edits.cur.regions.size() + 1);
        r.pts = draft;
        r.dz = draw_kind == AREA_GROUND ? 1.0f : 0.0f;
        edits.cur.regions.push_back(r);
        edits.commit();
        sel_region = (int)edits.cur.regions.size() - 1;
        drawing_region = false;
        draft.clear();
        status = draw_kind == AREA_GROUND ? r.name + ": 1 m up for now - set the height in the Inspector; Build raises it with what stands on it"
                 : draw_kind == AREA_NAV_REMAKE ? r.name + ": Build makes the crowds' navmesh here again from the collision"
                                                : r.name + ": Build keeps the crowds out of it (routes through it are switched off)";
    }

    int pick(V3 o, V3 d, float* dist = nullptr) {
        float best = 1e30f;
        int hit = -1;
        for (int id = 0; id < edits.count(); id++) {
            if (!visible(id) || edits.removed(id)) continue;
            if (pick_skip && std::find(pick_skip->begin(), pick_skip->end(), id) != pick_skip->end()) continue;
            const Entity& e = ent(id);
            const M4& m = edits.matrix(id);
            const Mesh* mp = mesh_of(id);
            if (!mp) {
                V3 c = m.pos();
                float r = marker_size(e) * 0.6f;
                V3 oc = o - c;
                float b = dot(oc, d), cc = dot(oc, oc) - r * r, disc = b * b - cc;
                if (disc < 0) continue;
                float t = -b - std::sqrt(disc);
                if (t > 0 && t < best) { best = t; hit = id; }
                continue;
            }
            const Mesh& me = *mp;
            M4 inv = inverse(m);
            V3 lo = inv.point(o), ld = inv.dir(d);
            float tb = ray_box(lo, ld, me.lo, me.hi);
            if (tb < 0 || tb > best) continue;
            for (uint32_t t = 0; t < me.nt; t++) {
                const uint32_t* ix = me.idx + t * 3;
                V3 a(me.pos[ix[0] * 3], me.pos[ix[0] * 3 + 1], me.pos[ix[0] * 3 + 2]);
                V3 b(me.pos[ix[1] * 3], me.pos[ix[1] * 3 + 1], me.pos[ix[1] * 3 + 2]);
                V3 c(me.pos[ix[2] * 3], me.pos[ix[2] * 3 + 1], me.pos[ix[2] * 3 + 2]);
                float tt = ray_tri(lo, ld, a, b, c);
                if (tt > 0 && tt < best) { best = tt; hit = id; }
            }
        }
        if (dist) *dist = best;
        return hit;
    }

    // one object's model against a ray (pick's test for one id) -> hit, t
    bool ray_one(int id, V3 o, V3 d, float& best) {
        const Mesh* mp = mesh_of(id);
        if (!mp) return false;
        const Mesh& me = *mp;
        M4 inv = inverse(edits.matrix(id));
        V3 lo = inv.point(o), ld = inv.dir(d);
        if (ray_box(lo, ld, me.lo, me.hi) < 0) return false;
        best = 1e30f;
        for (uint32_t t = 0; t < me.nt; t++) {
            const uint32_t* ix = me.idx + t * 3;
            float tt = ray_tri(lo, ld, V3(me.pos[ix[0] * 3], me.pos[ix[0] * 3 + 1], me.pos[ix[0] * 3 + 2]),
                               V3(me.pos[ix[1] * 3], me.pos[ix[1] * 3 + 1], me.pos[ix[1] * 3 + 2]),
                               V3(me.pos[ix[2] * 3], me.pos[ix[2] * 3 + 1], me.pos[ix[2] * 3 + 2]));
            if (tt > 0 && tt < best) best = tt;
        }
        return best < 1e29f;
    }

    bool selected(int id) const { return std::find(selection.begin(), selection.end(), id) != selection.end(); }

    void select(int id, bool add) {
        if (!add) selection.clear();
        if (id < 0) return;
        auto it = std::find(selection.begin(), selection.end(), id);
        if (it != selection.end()) { if (add) selection.erase(it); }
        else selection.push_back(id);
    }

    // --------------------------------------------------------------------------------------- editing --
    // the gizmo's change: every mover keeps its place relative to the pivot (row vectors: M' = M * P0^-1 * P1)
    void apply_pivot(const std::vector<int>& movers, const std::vector<M4>& start, const M4& p0, const M4& p1) {
        M4 delta = inverse(p0) * p1;
        for (size_t k = 0; k < movers.size(); k++) edits.matrix(movers[k]) = start[k] * delta;
    }

    // the pivot the gizmo shows for these objects: the one object's own (Local), else the bottom middle of their box -
    // of a building's own group pieces when the selection is a building (its staircases and porch beams stick out,
    // so the whole selection's middle isn't the building's - turned 180 degrees about that, it lands 1.2 m off its plot)
    M4 pivot_of(const std::vector<int>& movers) {
        if (movers.size() == 1 && gizmo_local) return edits.matrix(movers[0]);
        std::vector<int> core;
        for (int id : movers) {
            if (edits.is_copy(id)) continue;
            int bk = edits.cur.building[id];
            if (bk >= 0 && scene->entities[id].group == edits.building_keys[bk]) core.push_back(id);
        }
        const std::vector<int>& use = core.size() >= 2 ? core : movers;
        V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
        for (int id : use) { V3 a, b; world_box(id, a, b); lo = vmin(lo, a); hi = vmax(hi, b); }
        if (&use != &movers)
            for (int id : movers) { V3 a, b; world_box(id, a, b); lo.z = std::min(lo.z, a.z); }
        return translation(V3((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z));
    }

    void remove_selected() {
        if (ms_refuse(true)) return;                  // artifacts, scoring zones, Domination points: move only
        int n = 0;
        for (int id : selection)
            if (edits.movable(id) && !edits.removed(id)) { edits.set_removed(id, true); n++; }
        if (n) { edits.commit(); status = std::to_string(n) + " object(s) removed (Put Back undoes it)"; }
        selection.clear();
    }

    void put_back_selected() {
        int n = 0;
        for (int id : selection) {
            if (edits.is_copy(id)) continue;
            if (edits.removed(id) || edits.moved(id)) {
                edits.set_removed(id, false);
                edits.cur.matrix[id] = scene->entities[id].matrix;
                n++;
            }
        }
        if (n) { edits.commit(); status = std::to_string(n) + " object(s) put back as the map has them"; }
    }

    void copy_selected() {
        if (ms_refuse(false)) return;
        std::vector<int> made;
        for (int id : selection) {
            if (!edits.movable(id) || edits.removed(id)) continue;
            int src = edits.entity_of(id);
            if (src < 0) {                                   // a library model: another placement of it
                CopyObj o = edits.cur.copies[id - scene->entities.size()];
                if (o.group >= 0) o.path = "+" + std::to_string(++plus_counter);   // (added to that placement)
                edits.cur.copies.push_back(o);
                made.push_back((int)scene->entities.size() + (int)edits.cur.copies.size() - 1);
                continue;
            }
            if (scene->records[scene->entities[src].record].name.rfind("Cell", 0) != 0 && !(scene->entities[src].flags & F_SPAWN)) {
                add_log(scene->entities[src].name + ": only objects in the map cells can be copied yet");
                continue;
            }
            edits.cur.copies.push_back({src, edits.matrix(id), false});
            made.push_back((int)scene->entities.size() + (int)edits.cur.copies.size() - 1);
        }
        if (made.empty()) { status = "Nothing to copy (game-mode and World objects other than spawn points can't be copied yet)"; return; }
        edits.commit();
        selection = made;
        gizmo_op = ImGuizmo::TRANSLATE;
        status = std::to_string(made.size()) + " copy(ies) made in the same place - move them with the gizmo";
    }

    // Select Attached - the pieces touching the selection: their boxes share a face with a selected one's (each
    // axis overlapping within 5 cm, two of them by a quarter of the smaller piece - not a mere corner or edge), stacked
    // or side by side; not the ground (named Ground, slabs over 20 m across), not decals lying at a selected piece's
    // base, nothing over 40 m across, nothing over 4x the selection's volume so far (the size limit grows with it), not
    // spawn points / game-mode objects / anything that can't move. One ring a press; `full`: until nothing more touches
    // (a multi-part tower from one piece). -> how many were added
    void box_select(ImVec2 r0, ImVec2 r1, bool add) {
        if (!add) selection.clear();
        M4 vpm = view_m * proj_m;
        int n = 0;
        for (int id = 0; id < edits.count(); id++) {
            if (!visible(id) || edits.removed(id) || selected(id) || !edits.movable(id)) continue;
            if (!edits.is_placed(id) && ent(id).mesh < 0) continue;        // (FX, sound and other markers: not picked)
            V3 lo, hi;
            world_box(id, lo, hi);
            bool inside = true;
            for (int c = 0; c < 8 && inside; c++) {
                V3 p(c & 1 ? hi.x : lo.x, c & 2 ? hi.y : lo.y, c & 4 ? hi.z : lo.z);
                float x = p.x * vpm.m[0] + p.y * vpm.m[4] + p.z * vpm.m[8] + vpm.m[12];
                float y = p.x * vpm.m[1] + p.y * vpm.m[5] + p.z * vpm.m[9] + vpm.m[13];
                float w = p.x * vpm.m[3] + p.y * vpm.m[7] + p.z * vpm.m[11] + vpm.m[15];
                if (w <= 0.1f) { inside = false; break; }
                float sx = vp_pos.x + (x / w * 0.5f + 0.5f) * vp_size.x, sy = vp_pos.y + (0.5f - y / w * 0.5f) * vp_size.y;
                inside = sx >= r0.x && sx <= r1.x && sy >= r0.y && sy <= r1.y;
            }
            if (inside) { selection.push_back(id); n++; }
        }
        sel_region = sel_route = -1;
        status = std::to_string(n) + " object(s) inside the box" + (add ? " added" : "") + ": " + std::to_string(selection.size()) +
                 " selected (G grows it to what touches them)";
    }

    int select_attached(bool full) {
        if (!scene || selection.empty()) { status = "Select a piece first (then G: what touches it; Shift+G: the whole structure)"; return 0; }
        int n = edits.count();
        std::vector<V3> lo(n), hi(n);
        std::vector<uint8_t> ok(n, 0), in(n, 0);
        for (int id = 0; id < n; id++) {
            if (edits.removed(id) || !visible(id) || !edits.movable(id)) continue;
            if (!edits.is_copy(id)) {
                const Entity& e = scene->entities[id];
                if (e.flags & (F_SPAWN | F_MODE | F_VOLUME)) continue;
                if (e.record < 0 || scene->records[e.record].name.rfind("Cell", 0) != 0) continue;
                if (e.name.find("Ground") != std::string::npos) continue;
            }
            world_box(id, lo[id], hi[id]);
            V3 sz = hi[id] - lo[id];
            if (std::max(sz.x, sz.y) > 40.0f || (std::max(sz.x, sz.y) > 20.0f && sz.z < 2.0f)) continue;
            ok[id] = 1;
        }
        std::vector<int> cur;
        for (int id : selection)
            if (id >= 0 && id < n) {
                in[id] = 1;
                if (!ok[id]) world_box(id, lo[id], hi[id]);
                cur.push_back(id);
            }
        auto vol = [&](int id) { V3 d = hi[id] - lo[id]; return std::max(d.x, 0.1f) * std::max(d.y, 0.1f) * std::max(d.z, 0.1f); };
        // the selection's footprint so far: a piece that would stretch it past attach_span (or past what it already
        // spans, when that's more) stays out - Fort Wolcott's buildings share walls: unbounded, one rail would grow to 167 m
        V3 L(1e30f, 1e30f, 1e30f), H(-1e30f, -1e30f, -1e30f);
        for (int id : cur) { L = vmin(L, lo[id]); H = vmax(H, hi[id]); }
        float limit = std::max(attach_span, std::max(H.x - L.x, H.y - L.y));
        int capped = 0;
        auto touch = [&](int a, int b) {
            const float m = 0.05f;
            int shared = 0;
            for (int k = 0; k < 3; k++) {
                float ov = std::min(hi[a][k], hi[b][k]) - std::max(lo[a][k], lo[b][k]);
                if (ov < -m) return false;
                float smaller = std::min(hi[a][k] - lo[a][k], hi[b][k] - lo[b][k]);
                shared += ov >= 0.25f * std::max(smaller, 0.05f);
            }
            return shared >= 2;
        };
        int added = 0;
        for (int round = 0; round < 60; round++) {
            float total = 1.0f;
            for (int id : cur) total += vol(id);
            std::vector<int> ring;
            for (int id = 0; id < n; id++) {
                if (!ok[id] || in[id] || vol(id) > 4.0f * total) continue;
                V3 sz = hi[id] - lo[id];
                for (int s : cur) {
                    if (!touch(id, s)) continue;
                    if (sz.z < 0.15f && hi[id].z < hi[s].z - 0.5f) break;          // a decal on the ground at its base
                    ring.push_back(id);
                    break;
                }
            }
            std::vector<int> kept;
            for (int id : ring) {
                V3 l2 = vmin(L, lo[id]), h2 = vmax(H, hi[id]);
                if (std::max(h2.x - l2.x, h2.y - l2.y) > limit + 0.01f) { capped++; continue; }
                L = l2;
                H = h2;
                kept.push_back(id);
            }
            ring = kept;
            for (int id : ring) { in[id] = 1; cur.push_back(id); }
            added += (int)ring.size();
            if (!full || ring.empty() || cur.size() > 3000) break;
        }
        for (int id : cur)
            if (!selected(id)) selection.push_back(id);
        status = (added ? std::to_string(added) + " attached piece(s) added: " + std::to_string(selection.size()) + " selected" +
                              (full ? "" : " (G again grows it a ring more; Shift+G: the whole structure)") +
                              " - Ctrl+D copies them, Library > My assets saves them as an asset"
                        : std::string("Nothing more touches the selection")) +
                 (capped ? "; " + std::to_string(capped) + " touching piece(s) left out: past the largest structure (" +
                               std::to_string((int)limit) + " m, Attached menu)" : std::string());
        return added;
    }

    void select_building() {
        // a placed user asset: every part of the groups the selection touches
        std::set<int> grp;
        for (int id : selection)
            if (edits.is_copy(id) && edits.cur.copies[id - scene->entities.size()].group >= 0)
                grp.insert(edits.cur.copies[id - scene->entities.size()].group);
        for (size_t k = 0; k < edits.cur.copies.size() && !grp.empty(); k++) {
            int id = (int)(scene->entities.size() + k);
            if (grp.count(edits.cur.copies[k].group) && !edits.cur.copies[k].removed && !selected(id)) selection.push_back(id);
        }
        std::vector<int> ents;
        for (int id : selection)
            if (!edits.is_copy(id)) ents.push_back(id);
        if (ents.empty()) {
            status = grp.empty() ? "Select a piece of a building first"
                                 : std::to_string(selection.size()) + " parts of " + edits.groups[*grp.begin()].name + " selected - they move as one";
            return;
        }
        json uids = json::array(), current = json::object(), removed = json::array();
        for (int id : ents) uids.push_back(uid_hex(scene->entities[id].uid));
        for (size_t i = 0; i < scene->entities.size(); i++) {
            if (edits.cur.removed[i]) removed.push_back(uid_hex(scene->entities[i].uid));
            else if (edits.moved((int)i)) {
                json m = json::array();
                for (float f : edits.cur.matrix[i].m) m.push_back(f);
                current[uid_hex(scene->entities[i].uid)] = m;
            }
        }
        status = "Finding the building...";
        engine.request("select_building", {{"archive", scene->archive}, {"uids", uids}, {"current", current},
                                           {"removed", removed}},
                       [this, ents](bool ok, const json& r, const std::string& e) {
                           if (!ok) { status = "Select Building: " + e; return; }
                           if (!scene) return;
                           int key = edits.building_key(r.value("key", std::string("building")));
                           std::vector<int> all = ents;
                           for (auto& h : r["uids"]) {
                               auto it = edits.by_uid.find(uid_from_hex(h.get<std::string>()));
                               if (it != edits.by_uid.end() && !selected(it->second)) all.push_back(it->second);
                           }
                           for (int id : all) edits.cur.building[id] = key;
                           edits.commit();
                           for (int id : all)
                               if (!selected(id)) selection.push_back(id);
                           status = "Building: " + std::to_string(all.size()) + " pieces selected (" +
                                    edits.building_keys[key] + ") - moved as one, it carries its ground";
                       });
    }

    bool build() {                                // -> a build was started
        if (!scene) return false;
        if (engine.busy()) { status = "The engine is busy (" + engine.busy_with() + ") - build again in a moment"; return false; }
        if (workspace) { status = "This is the asset editor: Save the asset, Close, then place it in a map (Library > My assets) and build"; return false; }
        json req = edits.request(carry_ground);
        if (req["moves"].empty() && req["removes"].empty() && req["copies"].empty() && req["places"].empty() &&
            req["regions"].empty() && req["nav_areas"].empty() && req["routes"].empty() &&
            req["climbable"].empty() && req["climbable_outside"].empty() && req["sculpts"].empty() &&
            !req.contains("map_settings") && !req.contains("spawns") && !req.contains("boundaries")) {
            status = "Nothing to build: no moved, removed, copied or placed objects, no ground raised / lowered, "
                     "no navmesh or crowd route edits, no map settings";
            return false;
        }
        status = req["places"].empty() && req["regions"].empty() ? "Building a test map..."
                 : !req["regions"].empty() ? "Building a test map (ground raised / lowered: the navmesh under it too - "
                                             "a minute or two)..."
                                       : "Building a test map (library models: their materials, collision, climb edges "
                                         "and the navmesh under them - a few minutes)...";
        add_log("---- build: " + std::to_string(req["moves"].size()) + " moved, " +
                std::to_string(req["removes"].size()) + " removed, " + std::to_string(req["copies"].size()) +
                " copied, " + std::to_string(req["places"].size()) + " placed from the library");
        last_build = json();
        engine.request("build", {{"request", req}}, [this](bool ok, const json& r, const std::string& e) {
            if (!ok) { status = "Build failed: " + e; build_ok = false; last_build = {{"error", e}}; if (tg_state == 1) tg_state = 0; return; }
            last_build = r;
            last_dest = r.value("dest", std::string());
            build_ok = r.value("verified", false);
            holes_vis.clear();
            holes_col.clear();
            plug_tris.clear();
            for (auto& pl : r.value("plugs", json::array())) {
                float z = pl.value("z", 0.0f) + 0.08f;
                std::string kind = pl.value("kind", std::string("slab"));
                uint32_t col = kind == "collision" ? rgba(0.3f, 0.6f, 1, 0.35f) : kind == "seen" ? rgba(1, 0.9f, 0.3f, 0.35f)
                                                                                                 : rgba(0.3f, 1, 0.4f, 0.35f);
                for (auto& c : pl["cells"]) {
                    float x = c[0], y = c[1];
                    V3 a(x - 0.25f, y - 0.25f, z), b(x + 0.25f, y - 0.25f, z), cc(x + 0.25f, y + 0.25f, z), d(x - 0.25f, y + 0.25f, z);
                    for (V3 q : {a, b, cc, a, cc, d}) plug_tris.push_back({q, col});
                }
            }
            if (!plug_tris.empty()) show_holes = true;
            if (r.contains("holes")) {
                for (auto& p : r["holes"]["visible"]) holes_vis.push_back(V3(p[0], p[1], p[2]));
                for (auto& p : r["holes"]["collision"]) holes_col.push_back(V3(p[0], p[1], p[2]));
                show_holes = true;
            }
            status = build_ok ? "Built and checked: " + last_dest : "Built, but the read-back check failed (see Build)";
            if (tg_state == 1) tg_after_build();
        });
        return true;
    }

    // ---- the character viewer
    void chr_open() {                                // (the Characters workspace - the menu bar's Maps | Characters)
        if (!chr.layout_done) chr.bottom_req = 0;    // (the first time: Changes in front at the bottom)
        chr.open = true;
        if (chr.asked) return;
        chr.asked = true;
        engine.request("personas", json::object(), [this](bool ok, const json& r, const std::string& e) {
            if (!ok) { chr.error = e; return; }
            chr.personas = r.value("personas", json::array());
        });
    }
    // the clip - its .ac3anim read; each frame the parts skinned into the character slot's dynamic buffers
    bool chr_read_anim(const std::string& path) {
        std::ifstream f(widen(path), std::ios::binary);
        if (!f) return false;
        std::vector<char> b((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
        if (b.size() < 24 || memcmp(b.data(), "AC3A", 4) != 0) return false;
        uint32_t ver, nb, nf, np;
        float fps;
        memcpy(&ver, &b[4], 4); memcpy(&nb, &b[8], 4); memcpy(&nf, &b[12], 4); memcpy(&fps, &b[16], 4); memcpy(&np, &b[20], 4);
        size_t p = 24;
        chr.skin.clear();
        for (uint32_t i = 0; i < np; i++) {
            if (p + 8 > b.size()) return false;
            uint32_t k, nc;
            memcpy(&k, &b[p], 4); memcpy(&nc, &b[p + 4], 4);
            p += 8;
            CharUI::SkinPart sp;
            sp.entity = (int)k;
            sp.infl.resize((size_t)nc * 4);
            if (p + sp.infl.size() * 20 > b.size()) return false;
            memcpy(sp.infl.data(), &b[p], sp.infl.size() * 20);
            p += sp.infl.size() * 20;
            chr.skin.push_back(std::move(sp));
        }
        size_t nfl = (size_t)nf * nb * 12;
        if (p + nfl * 4 > b.size()) return false;
        chr.frames.assign((const float*)&b[p], (const float*)&b[p] + nfl);
        chr.nbones = (int)nb; chr.nframes = (int)nf; chr.fps = fps;
        return true;
    }
    void chr_load_anim(const std::string& clip = "") {
        if (chr.anim_loading || chr.loaded_id < 0) return;
        chr.anim_loading = true;
        engine.request("animation", {{"persona", chr.loaded_id}, {"rows", chr.rows}, {"clip", clip}, {"meshes", chr.meshes},
                                     {"materials", chr.materials}},
                       [this](bool ok, const json& r, const std::string& e) {
            chr.anim_loading = false;
            if (!ok) { chr.error = "animation: " + e; return; }
            if (!chr_read_anim(r.value("file", std::string()))) { chr.error = "can't read the animation"; return; }
            chr.anim = r;
            chr.clip = r.value("clip", std::string());
            chr.clips = r.value("clips", json::array());
            chr.t = 0;
            if (!chr.animated) chr.yaw = 0.35f;        // (the skeleton's frame: the character faces +x)
            chr.animated = true;
        });
    }
    // the parts posed at time t: posed = sum w (R_b loc + P_b), uv from the scene mesh (its corners, in order)
    void chr_pose() {
        if (!chr.animated || !chr.scene || chr.nframes < 1) return;
        int f = (int)(chr.t * chr.fps) % chr.nframes;
        const float* F = &chr.frames[(size_t)f * chr.nbones * 12];
        for (auto& sp : chr.skin) {
            if (sp.entity < 0 || sp.entity >= (int)chr.scene->entities.size()) continue;
            int m = chr.scene->entities[sp.entity].mesh;
            if (m < 0) continue;
            const Mesh& me = chr.scene->meshes[m];
            size_t nc = sp.infl.size() / 4;
            if (nc != me.nv) continue;
            chr.work.resize(nc * 5);
            for (size_t c = 0; c < nc; c++) {
                float x = 0, y = 0, z = 0;
                for (int j = 0; j < 4; j++) {
                    const auto& in = sp.infl[c * 4 + j];
                    if (in.w <= 0 || in.bone >= chr.nbones) continue;
                    const float* B = F + (size_t)in.bone * 12;
                    x += in.w * (B[0] * in.loc[0] + B[1] * in.loc[1] + B[2] * in.loc[2] + B[9]);
                    y += in.w * (B[3] * in.loc[0] + B[4] * in.loc[1] + B[5] * in.loc[2] + B[10]);
                    z += in.w * (B[6] * in.loc[0] + B[7] * in.loc[1] + B[8] * in.loc[2] + B[11]);
                }
                float* o = &chr.work[c * 5];
                o[0] = x; o[1] = y; o[2] = z;
                o[3] = me.uv[c * 2]; o[4] = me.uv[c * 2 + 1];
            }
            renderer.update_vertices(SLOT_CHAR, m, chr.work.data(), nc);
            auto& pz = chr.posed[m];                   // (picking a posed part)
            pz.resize(nc * 3);
            for (size_t c = 0; c < nc; c++) { pz[c * 3] = chr.work[c * 5]; pz[c * 3 + 1] = chr.work[c * 5 + 1]; pz[c * 3 + 2] = chr.work[c * 5 + 2]; }
        }
    }
    void chr_load(int id, const json& rows = json::object()) {
        if (chr.loading) {                            // (an edit during a read - read again once it's done)
            if (id == chr.sel && id == chr.loaded_id) { chr.rows = rows; chr.colour_dirty = ImGui::GetTime() - 0.5; }
            return;
        }
        chr.loading = true;
        if (id != chr.loaded_id) {
            chr.colours = json::object(); chr.colour_dirty = 0; chr.build_name[0] = 0;
            chr.meshes = json::object(); chr.exports = json::object();
            chr.weapons = json::object(); chr.weapon_info = json();
            chr.materials = json::object(); chr.mat_part.clear(); chr.mat_list = json();
            chr.new_looks.clear(); chr.new_weapons.clear();
            chr.undo.clear(); chr.redo.clear(); chr.undo_key.clear();
            chr.built_key.clear(); chr.built_name.clear();
            chr.pick = CharUI::PICK_PERSONA; chr.pick_part.clear();
        }
        chr.sel = id;
        chr.rows = rows;
        chr.t0 = ImGui::GetTime();
        engine.request("character", {{"persona", id}, {"rows", rows}, {"colours", chr.colours}, {"meshes", chr.meshes}, {"weapons", chr.weapons},
                                     {"materials", chr.materials}},
                       [this, id](bool ok, const json& r, const std::string& e) {
            chr.loading = false;
            if (!ok) { chr.error = e; status = "Characters: " + e; return; }
            auto s = std::make_unique<Scene>();
            std::string err;
            if (!s->load(r.value("scene", std::string()), err)) { chr.error = "can't read the character: " + err; return; }
            renderer.release_scene(SLOT_CHAR);
            if (!renderer.upload(*s, err, nullptr, SLOT_CHAR)) { chr.error = "can't draw the character: " + err; return; }
            chr.scene = std::move(s);
            chr.posed.clear();
            chr.info = r;
            chr.loaded_id = id;
            chr.show.assign(chr.scene->entities.size(), 1);
            chr_weapon_only();
            if (chr.animated) { chr.animated = false; chr_load_anim(chr.clip); }
            // an edited .glb already beside a part's export (saved from Blender, or made by a script)
            // offers "Use the edit" without exporting the part again
            for (size_t k = 0; k < r.value("part_names", json::array()).size(); k++) {
                std::string part = r["part_names"][k].get<std::string>();
                if (r["part_slots"][k].get<std::string>() == "Weapon" || chr.exports.contains(part)) continue;
                engine.request("char_edited", {{"part", part}}, [this, part, id](bool ok2, const json& e2, const std::string&) {
                    if (ok2 && id == chr.loaded_id && e2["edited"].is_string() && !chr.exports.contains(part)) chr.exports[part] = e2;
                });
            }
            chr.error.clear();
            status = "Characters: " + std::to_string(id) + " - " + std::to_string(r.value("triangles", 0)) + " triangles, " +
                     std::to_string(r.value("textures", 0)) + " texture(s)";
        });
    }
    // ---- a persona's colour schemes edited, previewed, built into a test DataPC
    int chr_edit_count() const {
        int n = (int)chr.meshes.size() + (int)chr.weapons.size();
        for (auto& [_p, mats] : chr.materials.items())
            for (auto& [_m, vals] : mats.items()) n += (int)vals.size();
        for (auto& [_s, regs] : chr.colours.items()) n += (int)regs.size();
        return n;
    }
    // ---- a part out to Blender and back
    void chr_export(const std::string& part, bool open_blender) {
        if (!chr.mesh_busy.empty()) return;
        chr.mesh_busy = part;
        engine.request("char_export", {{"persona", chr.loaded_id}, {"part", part}}, [this, part, open_blender](bool ok, const json& r, const std::string& e) {
            chr.mesh_busy.clear();
            if (!ok) { chr.build_msg = "Couldn't export " + part + ": " + e; return; }
            chr.exports[part] = r;
            if (open_blender) {
                engine.request("char_blender", {{"glb", r.value("glb", std::string())}}, [this, part](bool ok2, const json& r2, const std::string& e2) {
                    chr.build_msg = ok2 ? "Blender is opening " + part + " (" + r2.value("blend", std::string()) + "). Edit it, then File > Export > glTF 2.0 (.glb) as " + part + "_edit.glb beside it."
                                        : "Couldn't open Blender: " + e2;
                });
            }
        });
    }
    // ---- weapons
    void chr_weapons_refresh() {
        if (chr.weapons_loading || chr.loaded_id < 0) return;
        chr.weapons_loading = true;
        int id = chr.loaded_id;
        engine.request("char_weapons", {{"persona", id}}, [this, id](bool ok, const json& r, const std::string& e) {
            chr.weapons_loading = false;
            if (id != chr.loaded_id) return;
            if (!ok) { chr.weapon_info = {{"error", e}}; return; }
            chr.weapon_info = r;
        });
    }
    void chr_weapon_only() {                         // (the drawn weapon alone - the camera frames what's shown)
        auto slots = chr.info.value("part_slots", json::array());
        for (size_t k = 0; k < chr.show.size() && k < slots.size(); k++)
            chr.show[k] = !chr.weapon_only || slots[k].get<std::string>() == "Weapon";
    }
    void chr_draw_item(int item) {
        json rows = chr.rows.is_object() ? chr.rows : json::object();
        rows["Item"] = item;
        chr_load(chr.loaded_id, rows);
    }
    // ---- material values
    void chr_mat_open(const std::string& part) { chr_pick(CharUI::PICK_MATERIAL, part, 0); }
    void chr_mat_preview() {
        if (chr.mat_busy || chr.mat_part.empty() || !chr.mat_list.is_array() || chr.mat_list.empty()) return;
        chr.mat_busy = true;
        chr.mat_dirty = 0;
        std::string part = chr.mat_part;
        json a = {{"part", part}, {"yaw", chr.mat_yaw}, {"pitch", chr.mat_pitch},
                  {"edits", chr.materials.value(part, json::object())}};
        if (chr.mat_highlight && chr.mat_list.size() > 1)
            a["highlight"] = chr.mat_list[std::min<size_t>(chr.mat_sel, chr.mat_list.size() - 1)].value("uid", std::string());
        if (chr.meshes.contains(part)) a["mesh"] = chr.meshes[part];
        engine.request("char_material_preview", a, [this, part](bool ok, const json& r, const std::string& e) {
            chr.mat_busy = false;
            if (part != chr.mat_part) return;
            if (!ok) { chr.mat_error = "preview: " + e; return; }
            chr.mat_error.clear();
            if (chr.mat_img.srv) { chr.mat_img.srv->Release(); chr.mat_img.srv = nullptr; }
            chr.mat_img.meta = r;
            std::string err;
            chr.mat_img.srv = load_dds(dev, r.value("image", std::string()), nullptr, err);
            if (!chr.mat_img.srv) chr.mat_error = "can't draw the preview: " + err;
        });
    }
    void chr_use_mesh(const std::string& part, const std::string& glb) {
        chr_touch("");
        if (glb.empty()) chr.meshes.erase(part);
        else chr.meshes[part] = {{"glb", glb}, {"lods", "all"}};
        chr_load(chr.loaded_id, chr.rows);
    }
    void chr_builds_refresh() {
        chr.builds_asked = true;
        engine.request("char_builds", json::object(), [this](bool ok, const json& r, const std::string& e) {
            if (!ok) { chr.build_msg = "builds: " + e; return; }
            chr.builds = r.value("builds", json::array());
            chr.installed = r.value("installed", json()).is_string() ? r["installed"].get<std::string>() : "";
        });
    }
    void chr_build() {
        if (chr.building || chr.loaded_id < 0) return;
        if (!chr.build_name[0]) { std::string nm = chr_auto_name(); strncpy_s(chr.build_name, nm.c_str(), sizeof chr.build_name - 1); }
        chr.building = true;
        chr.build_msg = "Building " + std::string(chr.build_name) + "...";
        std::string name = chr.build_name, key = chr_state_key();
        json meshes = chr.meshes, materials = chr.materials, weapons = chr.weapons;
        json looks = json::array(), new_w = json::array();
        auto slots = chr.info.value("part_slots", json::array());
        auto names = chr.info.value("part_names", json::array());
        for (auto& [part, t] : chr.new_looks) {
            std::string slot;
            for (size_t k = 0; k < names.size() && k < slots.size(); k++) if (names[k].get<std::string>() == part) slot = slots[k].get<std::string>();
            json spec = {{"slot", slot}, {"row", chr.rows.is_object() ? chr.rows.value(slot, 0) : 0}, {"part", part},
                         {"name", std::string(t.name)}, {"desc", std::string(t.desc)}};
            if (meshes.contains(part)) { spec["mesh"] = meshes[part]; meshes.erase(part); }
            if (materials.contains(part)) { spec["materials"] = materials[part]; materials.erase(part); }
            looks.push_back(spec);
        }
        for (auto& [item, t] : chr.new_weapons) {
            if (!weapons.contains(item)) continue;
            new_w.push_back({{"template", std::stoi(item)}, {"model", weapons[item]}, {"name", std::string(t.name)}, {"desc", std::string(t.desc)}});
            weapons.erase(item);
        }
        engine.request("char_build", {{"persona", chr.loaded_id}, {"colours", chr.colours}, {"name", name}, {"meshes", meshes},
                                      {"weapons", weapons}, {"materials", materials}, {"new_looks", looks}, {"new_weapons", new_w}},
                       [this, name, key](bool ok, const json& r, const std::string& e) {
            chr.building = false;
            chr.build_msg = ok ? "Built " + name + ": " + r.value("dest", std::string()) + " (checked: the edits read back, every other file the default set's)"
                               : "The build failed: " + e;
            status = "Characters: " + chr.build_msg;
            if (ok) { chr.build_name[0] = 0; chr.built_key = key; chr.built_name = name; }
            chr_builds_refresh();
            if (tg_chr && tg_state == 1) {               // (Test in game goes on: install, start AC3)
                if (ok && key == chr_state_key()) chr_tg_install();
                else { tg_state = 0; status = ok ? "Test in game stopped: the changes moved on during the build" : "Test in game stopped: " + chr.build_msg; }
            }
            int next = chr.load_after_build;
            chr.load_after_build = -1;
            if (ok && next >= 0) chr_load(next);
        });
    }
    std::string persona_name(int id) const {
        for (auto& p : chr.personas) if (p.value("id", 0) == id) return p.value("name", std::string());
        return "";
    }

    // ---- a singleplayer area to MP
    void spa_set(SpaImage& im, const json& r) {
        if (im.srv) { im.srv->Release(); im.srv = nullptr; }
        im.meta = r;
        std::string err;
        im.srv = load_dds(dev, r.value("image", std::string()), nullptr, err);
        if (!im.srv) add_log("SP area: can't draw " + r.value("image", std::string()) + ": " + err);
    }
    std::string spa_map() const {
        return spa.map >= 0 && spa.map < (int)spa.maps.size() ? spa.maps[spa.map].value("path", std::string()) : std::string();
    }
    void spa_open() {
        spa.open = true;
        if (spa.asked) return;
        spa.asked = true;
        engine.request("sp_maps", json::object(), [this](bool ok, const json& r, const std::string& e) {
            if (!ok) { spa.error = e; return; }
            spa.maps = r.value("maps", json::array());
            spa.hosts.clear();
            for (auto& h : r.value("hosts", json::array())) spa.hosts.push_back(h.get<std::string>());
        });
    }
    void spa_choose(int k) {
        spa.map = k;
        spa.has_box = false;
        spa.zoom = 1; spa.pan[0] = spa.pan[1] = 0;
        if (spa.pv.srv) { spa.pv.srv->Release(); spa.pv.srv = nullptr; }
        spa.pv.meta = json();
        spa.ov.busy = true;
        status = "SP area: reading " + spa.maps[k].value("title", std::string()) + "'s overview (the first time: its index, minutes for the big maps)...";
        engine.request("sp_overview", {{"sp_map", spa_map()}}, [this](bool ok, const json& r, const std::string& e) {
            spa.ov.busy = false;
            if (!ok) { spa.error = e; status = "SP area: " + e; return; }
            spa_set(spa.ov, r);
            status = "SP area: drag a box on " + r.value("title", std::string()) + " (20 - 160 m a side; right-drag pans, wheel zooms)";
        });
    }
    void spa_preview() {
        spa.pv.busy = true;
        json box = {spa.box[0], spa.box[1], spa.box[2], spa.box[3]};
        engine.request("sp_preview", {{"sp_map", spa_map()}, {"box", box}}, [this](bool ok, const json& r, const std::string& e) {
            spa.pv.busy = false;
            if (!ok) { spa.error = e; status = "SP area: " + e; return; }
            spa_set(spa.pv, r);
            spa.street_z = r.value("street_z", 0.0f);
            spa.error.clear();
        });
    }
    void spa_build() {
        if (!spa.has_box || spa_map().empty() || spa.building) return;
        json req = {{"sp_map", spa_map()}, {"box", {spa.box[0], spa.box[1], spa.box[2], spa.box[3]}},
                    {"street_z", spa.street_z}, {"host", spa.hosts.empty() ? std::string("Boston Harbour") : spa.hosts[spa.host]}};
        if (spa.name[0]) req["name"] = std::string(spa.name);
        spa.building = true;
        spa.result = json();
        spa.t0 = ImGui::GetTime();
        status = "SP area: converting (spmaps' converter - this takes minutes; the Log shows its steps)...";
        add_log(status);
        engine.request("sp_build", {{"request", req}}, [this](bool ok, const json& r, const std::string& e) {
            spa.building = false;
            if (!ok) { spa.error = e; status = "SP area: " + e; add_log(status); return; }
            spa.result = r;
            status = "SP area built: " + r.value("dest", std::string()) + " (" + std::to_string(r.value("seconds", 0)) + " s) - open it from here or the Maps list";
            add_log(status);
            engine.request("maps", json::object(), [this](bool ok2, const json& r2, const std::string&) {
                if (!ok2) return;
                maps.clear();
                for (auto& m : r2["maps"]) maps.push_back({m["path"], m["title"], m["source"], m.value("kind", std::string())});
            });
        });
    }
    void ui_spa() {
        if (!spa.open) return;
        ImGui::SetNextWindowSize(ImVec2(1100, 760), ImGuiCond_FirstUseEver);
        if (!ImGui::Begin("New map from a singleplayer area", &spa.open)) { ImGui::End(); return; }
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("Pick an SP map, drag the area on it, check the preview, Convert. The SP map converter "
                            "(spmaps, every rule it learned in game) lays the area onto the host: the host's own layout replaced, "
                            "SP's materials and ground, MP handhold climbing, the boundaries redrawn round it, the game-mode "
                            "points moved onto open street. The new map opens here to touch up; Build / Install as usual.");
        ImGui::PopTextWrapPos();
        ImGui::SetNextItemWidth(220);
        std::string cur = spa.map >= 0 ? spa.maps[spa.map].value("title", std::string()) : std::string("Choose an SP map...");
        if (ImGui::BeginCombo("##spamap", cur.c_str())) {
            for (int k = 0; k < (int)spa.maps.size(); k++)
                if (ImGui::Selectable(spa.maps[k].value("title", std::string()).c_str(), k == spa.map)) spa_choose(k);
            ImGui::EndCombo();
        }
        ImGui::SameLine();
        ImGui::SetNextItemWidth(160);
        if (!spa.hosts.empty() && ImGui::BeginCombo("Host", spa.hosts[spa.host].c_str())) {
            for (int k = 0; k < (int)spa.hosts.size(); k++)
                if (ImGui::Selectable(spa.hosts[k].c_str(), k == spa.host)) spa.host = k;
            ImGui::EndCombo();
        }
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("The MP map the area replaces (its systems, sky and modes stay):\n"
                                                      "the hosts whose materials and boundaries the converter knows");
        ImGui::SameLine();
        ImGui::SetNextItemWidth(200);
        ImGui::InputTextWithHint("##spaname", "name (optional)", spa.name, sizeof spa.name);
        if (spa.ov.busy) ImGui::TextColored(ImVec4(0.95f, 0.6f, 0.25f, 1), "Reading the map...");
        if (!spa.error.empty()) ImGui::TextColored(ImVec4(1, 0.45f, 0.4f, 1), "%s", spa.error.c_str());
        float side = ImGui::GetContentRegionAvail().y - 120;
        // the overview: drag a box (metres), right-drag pans, wheel zooms
        if (spa.ov.srv) {
            const json& m = spa.ov.meta;
            float iw = m.value("w", 1.0f), ih = m.value("h", 1.0f);
            float x0 = m.value("x0", 0.0f), x1 = m.value("x1", 1.0f), y0 = m.value("y0", 0.0f), y1 = m.value("y1", 1.0f);
            ImVec2 avail(ImGui::GetContentRegionAvail().x * 0.58f, side);
            ImGui::BeginChild("##spaov", avail, true, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
            ImVec2 o = ImGui::GetCursorScreenPos();
            float fit = std::min(avail.x / iw, avail.y / ih) * spa.zoom;
            ImVec2 ip(o.x + spa.pan[0], o.y + spa.pan[1]);
            ImDrawList* dl = ImGui::GetWindowDrawList();
            dl->AddImage((ImTextureID)(intptr_t)spa.ov.srv, ip, ImVec2(ip.x + iw * fit, ip.y + ih * fit));
            auto to_m = [&](ImVec2 p) { return ImVec2(x0 + (p.x - ip.x) / (iw * fit) * (x1 - x0), y1 - (p.y - ip.y) / (ih * fit) * (y1 - y0)); };
            auto to_s = [&](float x, float y) { return ImVec2(ip.x + (x - x0) / (x1 - x0) * iw * fit, ip.y + (y1 - y) / (y1 - y0) * ih * fit); };
            ImGui::InvisibleButton("##spaovb", avail, ImGuiButtonFlags_MouseButtonLeft | ImGuiButtonFlags_MouseButtonRight);
            ImGuiIO& io = ImGui::GetIO();
            if (ImGui::IsItemHovered()) {
                if (io.MouseWheel != 0) {
                    ImVec2 mp = io.MousePos;
                    float z = std::clamp(spa.zoom * (io.MouseWheel > 0 ? 1.25f : 0.8f), 1.0f, 16.0f);
                    spa.pan[0] = (mp.x - o.x) - ((mp.x - o.x) - spa.pan[0]) * z / spa.zoom;
                    spa.pan[1] = (mp.y - o.y) - ((mp.y - o.y) - spa.pan[1]) * z / spa.zoom;
                    spa.zoom = z;
                }
                if (ImGui::IsMouseDragging(ImGuiMouseButton_Right)) { spa.pan[0] += io.MouseDelta.x; spa.pan[1] += io.MouseDelta.y; }
                if (ImGui::IsMouseClicked(ImGuiMouseButton_Left)) {
                    ImVec2 a = to_m(io.MousePos);
                    spa.drag0[0] = a.x; spa.drag0[1] = a.y;
                    spa.dragging = true;
                }
                ImVec2 mm = to_m(io.MousePos);
                ImGui::SetTooltip("x %.0f, y %.0f", mm.x, mm.y);
            }
            if (spa.dragging) {
                ImVec2 b = to_m(io.MousePos);
                float bx0 = std::min(spa.drag0[0], b.x), bx1 = std::max(spa.drag0[0], b.x);
                float by0 = std::min(spa.drag0[1], b.y), by1 = std::max(spa.drag0[1], b.y);
                bx1 = std::min(bx1, bx0 + 160.0f); by1 = std::min(by1, by0 + 160.0f);
                spa.box[0] = std::round(bx0); spa.box[1] = std::round(by0); spa.box[2] = std::round(bx1); spa.box[3] = std::round(by1);
                spa.has_box = spa.box[2] - spa.box[0] >= 20 && spa.box[3] - spa.box[1] >= 20;
                if (!ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
                    spa.dragging = false;
                    if (spa.has_box) spa_preview();
                }
            }
            if (spa.has_box || spa.dragging) {
                ImVec2 a = to_s(spa.box[0], spa.box[3]), b = to_s(spa.box[2], spa.box[1]);
                dl->AddRectFilled(a, b, IM_COL32(255, 160, 40, 40));
                dl->AddRect(a, b, IM_COL32(255, 170, 60, 255), 0.0f, ImDrawFlags_None, 2.0f);
            }
            ImGui::EndChild();
            ImGui::SameLine();
        }
        // the preview, the street level, convert
        ImGui::BeginChild("##spapv", ImVec2(0, side), true);
        if (spa.has_box) {
            ImGui::Text("Area: x %.0f .. %.0f, y %.0f .. %.0f  (%.0f x %.0f m)", spa.box[0], spa.box[2], spa.box[1], spa.box[3],
                        spa.box[2] - spa.box[0], spa.box[3] - spa.box[1]);
            if (spa.pv.busy) ImGui::TextColored(ImVec4(0.95f, 0.6f, 0.25f, 1), "Drawing the area (its triangles; up to a minute)...");
            else if (spa.pv.srv) {
                const json& m = spa.pv.meta;
                float iw = m.value("w", 1.0f), ih = m.value("h", 1.0f);
                float k = std::min(ImGui::GetContentRegionAvail().x / iw, (side - 110) / ih);
                ImVec2 p = ImGui::GetCursorScreenPos();
                ImGui::Image((ImTextureID)(intptr_t)spa.pv.srv, ImVec2(iw * k, ih * k));
                float x0 = m.value("x0", 0.0f), x1 = m.value("x1", 1.0f), y0 = m.value("y0", 0.0f), y1 = m.value("y1", 1.0f);
                auto s = [&](float x, float y) { return ImVec2(p.x + (x - x0) / (x1 - x0) * iw * k, p.y + (y1 - y) / (y1 - y0) * ih * k); };
                ImGui::GetWindowDrawList()->AddRect(s(spa.box[0], spa.box[3]), s(spa.box[2], spa.box[1]), IM_COL32(255, 170, 60, 255), 0.0f, ImDrawFlags_None, 2.0f);
                ImGui::TextDisabled("%s triangles in and round it (the orange box goes in; 30 m round it comes in as the "
                                    "backdrop)", std::to_string(m.value("triangles", 0)).c_str());
                float water = m.value("open_water", 0.0f);
                if (water > 0.01f)
                    ImGui::TextColored(ImVec4(1, 0.7f, 0.3f, 1), "%.0f%% of the area is open water or empty: no floor there after the "
                                       "conversion (players fall to the kill height) - move the box inland", water * 100);
            }
            ImGui::SetNextItemWidth(120);
            ImGui::DragFloat("Street level (SP m)", &spa.street_z, 0.05f, -20.0f, 60.0f, "%.2f");
            if (ImGui::IsItemHovered()) ImGui::SetTooltip("The area's street height in the SP map: it goes to the host's 0\n"
                                                          "(found from the area's open ground; New York's Wall Street 4.5, Boston ~0)");
        } else ImGui::TextDisabled(spa.ov.srv ? "Drag a box on the map (20 - 160 m a side)." : "Choose an SP map.");
        ImGui::EndChild();
        ImGui::BeginDisabled(!spa.has_box || spa.building || spa.pv.busy || engine.busy());
        if (ImGui::Button(spa.building ? "Converting..." : "Convert")) spa_build();
        ImGui::EndDisabled();
        if (spa.building) { ImGui::SameLine(); ImGui::TextDisabled("%.0f s - the Log shows its steps", ImGui::GetTime() - spa.t0); }
        if (spa.result.is_object()) {
            ImGui::SameLine();
            if (ImGui::Button("Open the new map")) open_map(spa.result.value("dest", std::string()));
            ImGui::TextDisabled("%s - %d game-mode point(s) moved onto open street, %d s",
                                spa.result.value("dest", std::string()).c_str(), spa.result.value("points_moved", 0), spa.result.value("seconds", 0));
            // spmaps' read-back (sp_verify): every failure known from in-game tests
            auto fails = spa.result.value("fails", json::array());
            if (fails.empty()) ImGui::TextColored(ImVec4(0.5f, 1, 0.6f, 1), "spmaps' checks: all %d passed", (int)spa.result.value("checks", json::array()).size());
            for (auto& f : fails) ImGui::TextColored(ImVec4(1, 0.45f, 0.4f, 1), "%s", f.get<std::string>().c_str());
        }
        ImGui::End();
    }

    // ---- a map imported from another game
    const json* imp_game() const {
        if (!imp.cat.is_object()) return nullptr;
        const json& g = imp.cat["games"];
        return imp.game >= 0 && imp.game < (int)g.size() ? &g[imp.game] : nullptr;
    }
    const json* imp_map() const {
        const json* g = imp_game();
        if (!g) return nullptr;
        const json& m = (*g)["maps"];
        return imp.map >= 0 && imp.map < (int)m.size() ? &m[imp.map] : nullptr;
    }
    void imp_refresh() {
        imp.error.clear();
        // (the map picked stays picked: its game and key)
        std::string g0 = imp_game() ? imp_game()->value("game", std::string()) : "", k0 = imp_map() ? imp_map()->value("key", std::string()) : "";
        engine.request("import_maps", json::object(), [this, g0, k0](bool ok, const json& r, const std::string& e) {
            if (!ok) { imp.error = e; return; }
            imp.cat = r;
            imp.map = -1;
            const json& gs = imp.cat["games"];
            if (g0.empty()) {
                for (int k = 0; k < (int)gs.size(); k++) if (gs[k].value("found", false)) { imp.game = k; imp.pick_tab = true; break; }
                return;
            }
            for (int k = 0; k < (int)gs.size(); k++) {
                if (gs[k].value("game", std::string()) != g0) continue;
                imp.game = k;
                imp.pick_tab = true;
                for (int i = 0; i < (int)gs[k]["maps"].size(); i++) if (gs[k]["maps"][i].value("key", std::string()) == k0) imp.map = i;
            }
        });
    }
    // the map's earlier import ({dest, name, imported, seconds, source_changed}), null when it has none
    json imp_existing() const {
        const json* m = imp_map();
        return m && m->contains("existing") && (*m)["existing"].is_object() ? (*m)["existing"] : json();
    }
    void imp_open() {
        imp.open = true;
        if (imp.asked) return;
        imp.asked = true;
        imp_refresh();
    }
    // again: convert it once more even when it was imported before (the engine otherwise hands back that import)
    void imp_build(bool again = false) {
        const json* g = imp_game();
        const json* m = imp_map();
        if (!g || !m || !m->value("available", false) || imp.building) return;
        json req = {{"game", g->value("game", std::string())}, {"key", m->value("key", std::string())}};
        if (again) req["again"] = true;
        if (imp.name[0]) req["name"] = std::string(imp.name);
        imp.building = true;
        imp.result = json();
        imp.error.clear();
        imp.t0 = ImGui::GetTime();
        status = "Importing " + m->value("title", std::string()) + " from " + g->value("title", std::string()) +
                 " (its conversion recipe - this takes minutes; the Log shows its steps)...";
        add_log(status);
        engine.request("import_build", {{"request", req}}, [this](bool ok, const json& r, const std::string& e) {
            imp.building = false;
            if (!ok) { imp.error = e; status = "Import: " + e; add_log(status); return; }
            imp.result = r;
            if (r.value("reused", false)) {               // (imported before: that import, opened)
                status = "Opened the earlier import (" + r.value("imported", std::string()) + "): " + r.value("dest", std::string());
                add_log(status);
                open_map(r.value("dest", std::string()));
                return;
            }
            status = "Imported: " + r.value("dest", std::string()) + " (" + std::to_string(r.value("seconds", 0)) + " s) - open it from here or the Maps list";
            add_log(status);
            imp_refresh();
            engine.request("maps", json::object(), [this](bool ok2, const json& r2, const std::string&) {
                if (!ok2) return;
                maps.clear();
                for (auto& m : r2["maps"]) maps.push_back({m["path"], m["title"], m["source"], m.value("kind", std::string())});
            });
        });
    }
    void ui_imp() {
        if (!imp.open) return;
        ImGui::SetNextWindowSize(ImVec2(820, 600), ImGuiCond_FirstUseEver);
        if (!ImGui::Begin("Import a map from another game", &imp.open)) { ImGui::End(); return; }
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("Pick a game and one of its multiplayer maps. Its conversion recipe (every rule learned porting "
                            "that map) turns the game's own archive, read from your install, into an AC3 map: the new map "
                            "lands in the Maps list, to open, edit, build and install like any other.");
        ImGui::PopTextWrapPos();
        if (!imp.cat.is_object()) {
            ImGui::TextDisabled(imp.error.empty() ? "Reading the recipes and your installs..." : "");
            if (!imp.error.empty()) ImGui::TextColored(ImVec4(1, 0.45f, 0.4f, 1), "%s", imp.error.c_str());
            ImGui::End();
            return;
        }
        const json& gs = imp.cat["games"];
        if (ImGui::BeginTabBar("##impgames")) {
            for (int k = 0; k < (int)gs.size(); k++) {
                std::string tab = gs[k].value("title", std::string()) + (gs[k].value("found", false) ? "" : " (not set)");
                bool pick = imp.pick_tab && k == imp.game;
                if (ImGui::BeginTabItem(tab.c_str(), nullptr, pick ? ImGuiTabItemFlags_SetSelected : 0)) {
                    if (imp.game != k && !imp.pick_tab) { imp.game = k; imp.map = -1; }
                    ImGui::EndTabItem();
                }
            }
            ImGui::EndTabBar();
            imp.pick_tab = false;
        }
        const json* g = imp_game();
        if (g) {
            if (!g->value("found", false))
                ImGui::TextColored(ImVec4(1, 0.7f, 0.3f, 1), "No install of this game found: set its folder in File > Settings.");
            else ImGui::TextDisabled("Install: %s", g->value("mp_dir", std::string()).c_str());
            ImGui::BeginChild("##impmaps", ImVec2(0, ImGui::GetContentRegionAvail().y - 150), true);
            const json& ms = (*g)["maps"];
            if (ImGui::BeginTable("##impt", 3, ImGuiTableFlags_RowBg | ImGuiTableFlags_SizingStretchProp)) {
                ImGui::TableSetupColumn("Map", ImGuiTableColumnFlags_WidthStretch, 1.0f);
                ImGui::TableSetupColumn("Archive", ImGuiTableColumnFlags_WidthStretch, 1.4f);
                ImGui::TableSetupColumn("Time", ImGuiTableColumnFlags_WidthFixed, 90);
                ImGui::TableHeadersRow();
                for (int i = 0; i < (int)ms.size(); i++) {
                    const json& m = ms[i];
                    bool av = m.value("available", false);
                    ImGui::TableNextRow();
                    ImGui::TableNextColumn();
                    ImGui::BeginDisabled(!av);
                    std::string label = m.value("title", std::string()) + "##imp" + std::to_string(i);
                    if (ImGui::Selectable(label.c_str(), imp.map == i, ImGuiSelectableFlags_SpanAllColumns)) imp.map = i;
                    ImGui::EndDisabled();
                    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled) && !av)
                        ImGui::SetTooltip("%s", m.value("reason", std::string()).c_str());
                    ImGui::TableNextColumn();
                    ImGui::TextDisabled("%s", m.value("file", std::string()).c_str());
                    ImGui::TableNextColumn();
                    if (m.contains("existing") && m["existing"].is_object())
                        ImGui::TextColored(m["existing"].value("source_changed", false) ? ImVec4(1, 0.7f, 0.3f, 1) : ImVec4(0.5f, 0.85f, 0.5f, 1),
                                           m["existing"].value("source_changed", false) ? "imported (source changed)" : "imported");
                    else if (av) ImGui::TextDisabled("~%d min", m.value("minutes", 0));
                    else ImGui::TextDisabled("not offered");
                }
                ImGui::EndTable();
            }
            ImGui::EndChild();
        }
        const json* m = imp_map();
        json ex = imp_existing();
        ImGui::PushTextWrapPos(0);
        if (m) ImGui::TextDisabled("%s", m->value("note", std::string()).c_str());
        if (ex.is_object()) {
            ImGui::TextColored(ImVec4(0.5f, 0.85f, 0.5f, 1), "Imported %s, %d s: %s", ex.value("imported", std::string()).c_str(),
                               ex.value("seconds", 0), ex.value("dest", std::string()).c_str());
            if (ex.value("source_changed", false))
                ImGui::TextColored(ImVec4(1, 0.7f, 0.3f, 1), "The source archive has changed since this import: Import again to convert the new one.");
        }
        ImGui::PopTextWrapPos();
        bool can = m && m->value("available", false) && !imp.building && !engine.busy();
        if (ex.is_object()) {
            // imported before: the main button opens that import; converting again is a separate, confirmed step
            ImGui::BeginDisabled(load_state == 1 || load_state == 2);
            if (ImGui::Button("Open")) open_map(ex.value("dest", std::string()));
            ImGui::EndDisabled();
            ImGui::SameLine();
            ImGui::BeginDisabled(!can);
            if (ImGui::Button(imp.building ? "Importing..." : ex.value("source_changed", false) ? "Import again (the source changed)..." : "Import again..."))
                ImGui::OpenPopup("Import again?");
            ImGui::EndDisabled();
            if (ImGui::BeginPopupModal("Import again?", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
                ImGui::Text("Convert %s again? Its recipe runs again (~%d min) and replaces", m ? m->value("title", std::string()).c_str() : "",
                            m ? m->value("minutes", 0) : 0);
                ImGui::TextDisabled("%s", ex.value("dest", std::string()).c_str());
                ImGui::TextDisabled("(edits saved in a project stay; a build made from the old import stays in your workspace)");
                if (ImGui::Button("Import again", ImVec2(140, 0))) { imp_build(true); ImGui::CloseCurrentPopup(); }
                ImGui::SameLine();
                if (ImGui::Button("Cancel")) ImGui::CloseCurrentPopup();
                ImGui::EndPopup();
            }
        } else {
            ImGui::SetNextItemWidth(220);
            ImGui::InputTextWithHint("##impname", "name (optional)", imp.name, sizeof imp.name);
            ImGui::SameLine();
            ImGui::BeginDisabled(!can);
            if (ImGui::Button(imp.building ? "Importing..." : "Import")) imp_build();
            ImGui::EndDisabled();
        }
        if (imp.building) { ImGui::SameLine(); ImGui::TextDisabled("%.0f s - the Log shows its steps", ImGui::GetTime() - imp.t0); }
        if (!imp.error.empty()) ImGui::TextColored(ImVec4(1, 0.45f, 0.4f, 1), "%s", imp.error.c_str());
        if (imp.result.is_object() && !ex.is_object() && !imp.result.value("reused", false)) {
            if (ImGui::Button("Open the new map")) open_map(imp.result.value("dest", std::string()));
            ImGui::SameLine();
            ImGui::TextDisabled("%s (%d s; the recipe's checks passed)", imp.result.value("dest", std::string()).c_str(),
                                imp.result.value("seconds", 0));
        }
        ImGui::End();
    }

    // ---- Clear the map
    static bool is_water_name(const std::string& n) {
        std::string up = n;
        for (auto& c : up) c = (char)toupper((unsigned char)c);
        return up.find("WATER") != std::string::npos || up.find("OCEAN") != std::string::npos || up.find("SEA_") != std::string::npos;
    }
    // what Clear takes out (inside `hull` when given: by the middle of each box): every map object drawn with a model
    // but the ground (when kept), the sea, spawn points / game-mode objects / artifacts / scoring zones / Domination
    // points, and - when kept - gameplay objects and blend groups. Markers with no model (lights, sound, FX, volumes,
    // the World's systems) aren't touched; nor are the copies and library models already placed
    std::vector<int> bare_targets(const std::vector<V3>* hull) {
        std::vector<int> out;
        for (int id = 0; id < (int)scene->entities.size(); id++) {
            const Entity& e = scene->entities[id];
            if (edits.removed(id) || !edits.movable(id) || e.mesh < 0 || (e.flags & (F_MODE | F_SPAWN | F_START))) continue;
            if (is_water_name(e.name) || (bare_keep_ground && is_terrain(id))) continue;
            if (const MsThing* t = ms_thing_of(id)) {
                if (t->move_only || (t->kind == "blend_group" ? bare_keep_blend : bare_keep_gameplay)) continue;
            } else if (bare_keep_blend && is_blend_marker(e)) continue;
            if (hull) {
                V3 a, b;
                world_box(id, a, b);
                std::vector<V3> c = {V3((a.x + b.x) / 2, (a.y + b.y) / 2, 0)};
                if (!inside_hull(*hull, c[0])) continue;
            }
            out.push_back(id);
        }
        return out;
    }
    static bool inside_hull(const std::vector<V3>& h, V3 p) {        // (convex, either winding)
        int sgn = 0;
        for (size_t i = 0; i < h.size(); i++) {
            V3 a = h[i], b = h[(i + 1) % h.size()];
            float c = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x);
            int s = c > 0 ? 1 : c < 0 ? -1 : 0;
            if (s && sgn && s != sgn) return false;
            if (s) sgn = s;
        }
        return true;
    }
    // the ground Clear keeps: Ground-named and 8 m or more across (Wolcott's Ground_Hay / _Coal / _Planks / GroundGarbage /
    // GroundVeget decals are props lying on it - ground.ground_material's rule)
    bool is_terrain(int id) {
        if (!ground_like(id)) return false;
        V3 a, b;
        world_box(id, a, b);
        return std::max(b.x - a.x, b.y - a.y) >= 8.0f;
    }
    // -> how many taken out (one undo step). Ground pieces left floating more than 2.5 m over what's under them (a
    // visual-only upper floor - Wolcott's Ground_BarraksMesshall_Upstairs - over a building that went) go too
    int bare_clear(const std::vector<V3>* hull) {
        if (!scene || workspace) return 0;
        std::vector<int> ids = bare_targets(hull);
        for (int id : ids) edits.set_removed(id, true);
        // a gameplay object goes whole: its head, members and FX companions (else the build warns per part)
        for (size_t k = 0, n0 = ids.size(); k < n0; k++)
            if (const MsThing* t = ms_thing_of(ids[k])) {
                std::vector<uint32_t> all = t->members;
                all.insert(all.end(), t->companions.begin(), t->companions.end());
                all.push_back(t->uid);
                for (uint32_t u : all) {
                    auto it = edits.by_uid.find(u);
                    if (it != edits.by_uid.end() && !edits.removed(it->second) && edits.movable(it->second)) {
                        edits.set_removed(it->second, true);
                        ids.push_back(it->second);
                    }
                }
            }
        int floating = 0;
        std::string floated;
        for (int pass = 0, more = 1; bare_keep_ground && more && pass < 4; pass++) {       // (until none more: a slab's decals after it)
            more = 0;
            for (int id = 0; id < (int)scene->entities.size(); id++) {
                const Entity& e = scene->entities[id];
                if (edits.removed(id) || !edits.movable(id) || e.mesh < 0 || !ground_like(id)) continue;
                V3 a, b;
                world_box(id, a, b);
                if (hull && !inside_hull(*hull, V3((a.x + b.x) / 2, (a.y + b.y) / 2, 0))) continue;
                if (b.x - a.x > 60 || b.y - a.y > 60 || b.z - a.z > 3.0f) continue;   // (the map's main ground; ground with levels)
                // at points across it: its own surface (from above), then what's under that - floating where other
                // ground (not the sea) lies more than 1.5 m below, at every point it has one
                std::vector<int> skip = {id};
                int under = 0, high = 0;
                for (int k = 0; k < 9; k++) {
                    float fx = k == 0 ? 0.5f : 0.2f + 0.3f * ((k - 1) % 3), fy = k == 0 ? 0.5f : 0.2f + 0.3f * ((k - 1) / 3 % 3);
                    V3 top(a.x + fx * (b.x - a.x), a.y + fy * (b.y - a.y), b.z + 1.0f);
                    float ts = 0;
                    if (!ray_one(id, top, V3(0, 0, -1), ts)) continue;
                    V3 o = top - V3(0, 0, ts + 0.05f);
                    pick_skip = &skip;
                    float t = 0;
                    int hit = pick(o, V3(0, 0, -1), &t);
                    pick_skip = nullptr;
                    if (hit < 0 || t > 200 || !ground_like(hit)) continue;
                    under++;
                    high += t > 1.5f;
                }
                pick_skip = nullptr;
                if (under >= 3 && high == under) {
                    edits.set_removed(id, true);
                    ids.push_back(id);
                    floating++;
                    more = 1;
                    if (floated.size() < 300) floated += (floated.empty() ? "" : ", ") + e.name;
                }
            }
        }
        if (floating) add_log("ground left floating (taken out too): " + floated);
        if (ids.empty()) { status = "Clear: nothing to take out"; return 0; }
        if (bare_far_off && !hull) {
            ms_put("stand_ins", false);
            ms_put("view_distance", 255);
        }
        edits.commit();
        selection.clear();
        status = std::to_string(ids.size()) + " object(s) taken out" + (floating ? " (" + std::to_string(floating) + " ground piece(s) left floating among them)" : "") +
                 (bare_far_off && !hull ? "; far view off, every loaded cell drawn (Map panel)" : "") +
                 " - Ctrl+Z puts them back; Build plugs any hole they leave in the ground";
        add_log(status);
        return (int)ids.size();
    }
    void ui_bare() {
        if (bare_ask) { ImGui::OpenPopup("Clear the map"); bare_ask = false; load_gameplay(); }
        ImGui::SetNextWindowSize(ImVec2(560, 0), ImGuiCond_Appearing);
        if (!ImGui::BeginPopupModal("Clear the map", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) return;
        ImGui::PushTextWrapPos(540);
        ImGui::TextUnformatted("Takes out every map object drawn with a model - buildings, props, trees, the far scenery - "
                               "so you can lay out something new with library models, copies and the scatter brush. "
                               "Kept: the invisible systems (the World, game modes, spawn points, out-of-bounds, crowds' "
                               "navmesh and routes, lights, sound and FX markers), artifacts / scoring zones / Domination "
                               "points, the sea. One undo step.");
        ImGui::PopTextWrapPos();
        ImGui::Separator();
        ImGui::Checkbox("Keep the ground", &bare_keep_ground);
        ImGui::Checkbox("Keep the gameplay objects (hiding spots, benches, doors, lifts)", &bare_keep_gameplay);
        ImGui::Checkbox("Keep the blend groups (NPCs posing)", &bare_keep_blend);
        ImGui::Checkbox("Far view off, every loaded cell drawn (stand-in tiles off, view distance 255)", &bare_far_off);
        bool ready = msg.things_loaded;
        if (!ready) ImGui::TextDisabled("Reading the gameplay objects...");
        else ImGui::Text("%d object(s) would go", (int)bare_targets(nullptr).size());
        ImGui::PushTextWrapPos(540);
        ImGui::TextDisabled("Build plugs every hole they leave in the ground (an object that was the only thing to stand "
                            "on: a slab in the ground's material, or collision only where the ground still shows).");
        ImGui::PopTextWrapPos();
        ImGui::BeginDisabled(!ready);
        if (ImGui::Button("Clear the whole map")) { bare_clear(nullptr); ImGui::CloseCurrentPopup(); }
        ImGui::SameLine();
        if (ImGui::Button("Clear inside an area...")) { start_region(AREA_CLEAR); ImGui::CloseCurrentPopup(); }
        ImGui::EndDisabled();
        ImGui::SameLine();
        if (ImGui::Button("Cancel")) ImGui::CloseCurrentPopup();
        ImGui::EndPopup();
    }

    // ---- scatter brush
    bool ground_like(int id) const {
        std::string n;
        if (!edits.is_copy(id)) n = scene->entities[id].name;
        else if (edits.is_placed(id)) {
            const AssetRef& a = edits.assets[edits.asset_of(id)];
            if (is_ground_key(a.key)) return true;
            n = a.name;
        } else if (edits.entity_of(id) >= 0) n = scene->entities[edits.entity_of(id)].name;
        for (auto& c : n) c = (char)toupper((unsigned char)c);
        return (n.find("GROUND") != std::string::npos || n.find("TERRAIN") != std::string::npos || n.find("ED_SCULPT") != std::string::npos) &&
               n.find("WATER") == std::string::npos;
    }
    static void box_of(const M4& m, V3 lo, V3 hi, V3& a, V3& b) {
        a = V3(1e30f, 1e30f, 1e30f); b = V3(-1e30f, -1e30f, -1e30f);
        for (int c = 0; c < 8; c++) {
            V3 w = m.point(V3(c & 1 ? hi.x : lo.x, c & 2 ? hi.y : lo.y, c & 4 ? hi.z : lo.z));
            a = vmin(a, w); b = vmax(b, w);
        }
    }
    // the palette: the selection's objects (map objects in the cells, placed library models), else the Library's model
    bool scatter_palette() {
        scatter_pal.clear();
        for (int id : selection) {
            if (!edits.movable(id) || edits.removed(id)) continue;
            const Mesh* mp = mesh_of(id);
            if (!mp) continue;
            int src = edits.entity_of(id);
            if (src >= 0 && scene->records[scene->entities[src].record].name.rfind("Cell", 0) != 0) continue;
            ScatterSrc q;
            q.id = id; q.m0 = edits.matrix(id); q.lo = mp->lo; q.hi = mp->hi; q.name = label_of(id);
            scatter_pal.push_back(q);
        }
        if (scatter_pal.empty() && !lib_sel_key.empty()) {
            int i = lib_find(lib_sel_key);
            if (i >= 0 && !is_special(lib[i].key)) {
                ScatterSrc q;
                q.lib = i;
                q.lo = V3(lib[i].lo[0], lib[i].lo[1], lib[i].lo[2]);
                q.hi = q.lo + V3(lib[i].size[0], lib[i].size[1], lib[i].size[2]);
                q.name = lib[i].name + " (" + lib[i].map + ")";
                scatter_pal.push_back(q);
            }
        }
        return !scatter_pal.empty();
    }
    void start_scatter() {
        if (!scene || workspace) return;
        if (scattering) { scattering = false; status = "Scatter ended"; return; }
        if (!scatter_palette()) { status = "Scatter: select the objects to scatter (or a model in the Library) first"; return; }
        scattering = true;
        sculpting = drawing_region = walking = false;
        selection.clear();
        scatter_made = 0;
        status = "Scatter: click / drag over the ground to paint " + std::to_string(scatter_pal.size()) +
                 " model(s), Shift+drag takes them away; settings in the Inspector; Esc ends";
    }
    // a scattered copy: one of the palette's models (the same source / library model), not removed
    bool scatter_mine(int id) const {
        if (!edits.is_copy(id) || edits.removed(id)) return false;
        int src = edits.entity_of(id), a = edits.asset_of(id);
        for (auto& q : scatter_pal) {
            if (q.lib >= 0) { if (a >= 0 && edits.assets[a].key == lib[q.lib].key) return true; continue; }
            if (src >= 0 ? edits.entity_of(q.id) == src : a >= 0 && edits.asset_of(q.id) == a) return true;
        }
        return false;
    }
    V3 foot_of(int id) {                              // the middle of its box's bottom
        V3 a, b;
        world_box(id, a, b);
        return V3((a.x + b.x) / 2, (a.y + b.y) / 2, a.z);
    }
    int scatter_erase(V3 c) {
        int n = 0;
        for (int id = (int)scene->entities.size(); id < edits.count(); id++) {
            if (!scatter_mine(id)) continue;
            V3 p = foot_of(id);
            if (length(V3(p.x - c.x, p.y - c.y, 0)) <= scatter_r && std::fabs(p.z - c.z) < 6) { edits.set_removed(id, true); n++; }
        }
        if (n) scatter_changed = true;
        return n;
    }
    // one dab: copies until the circle holds `density` per 100 m2 (painting over a spot again adds nothing)
    int scatter_dab(V3 c) {
        if (scatter_pal.empty()) return 0;
        const float R = scatter_r;
        int have = 0;
        struct Box { V3 a, b; };
        std::vector<Box> obst;
        for (int id = 0; id < edits.count(); id++) {
            if (edits.removed(id) || !visible(id) || !mesh_of(id)) continue;
            V3 a, b;
            world_box(id, a, b);
            if (b.x < c.x - R - 10 || a.x > c.x + R + 10 || b.y < c.y - R - 10 || a.y > c.y + R + 10) continue;
            bool mine = scatter_mine(id);
            if (mine) {
                V3 p((a.x + b.x) / 2, (a.y + b.y) / 2, a.z);
                if (length(V3(p.x - c.x, p.y - c.y, 0)) <= R) have++;
            } else {
                if (!scatter_clear) continue;
                if (ground_like(id) || b.z - a.z < 0.15f || b.x - a.x > 40 || b.y - a.y > 40) continue;   // (ground, decals, slabs)
            }
            obst.push_back({a, b});
        }
        int want = (int)std::lround(scatter_density * 3.14159265f * R * R / 100.0f) - have;
        if (want <= 0) return 0;
        std::uniform_real_distribution<float> U(0.0f, 1.0f);
        int made = 0;
        bool assets = false;
        for (int tries = 0; tries < want * 15 && made < want; tries++) {
            float rr = R * std::sqrt(U(scatter_rng)), an = 6.2831853f * U(scatter_rng);
            V3 at(c.x + std::cos(an) * rr, c.y + std::sin(an) * rr, 0);
            const ScatterSrc& q = scatter_pal[std::min((int)scatter_pal.size() - 1, (int)(U(scatter_rng) * scatter_pal.size()))];
            float yaw = scatter_turn ? 6.2831853f * U(scatter_rng) : 0.0f;
            V3 wa, wb;
            box_of(q.m0, q.lo, q.hi, wa, wb);
            V3 pivot((wa.x + wb.x) / 2, (wa.y + wb.y) / 2, wa.z);
            M4 m = q.m0 * translation(V3() - pivot) * rot_z(yaw) * translation(at);
            V3 fa, fb;
            box_of(m, q.lo, q.hi, fa, fb);
            // the ground: under the middle and four points of the footprint; the lowest is its foot, the spread its slope
            float lo = 1e30f, hi = -1e30f;
            bool ok = true;
            float top = c.z + 3 + R * 0.5f;
            for (int k = 0; k < 5 && ok; k++) {
                float sx = k == 0 ? 0.0f : (k & 1 ? 0.35f : -0.35f), sy = k == 0 ? 0.0f : (k & 2 ? 0.35f : -0.35f);
                V3 o(at.x + sx * (fb.x - fa.x), at.y + sy * (fb.y - fa.y), top);
                float t = 0;
                int hit = pick(o, V3(0, 0, -1), &t);
                if (hit < 0 || t > 60 || (scatter_ground && !ground_like(hit))) { ok = false; break; }
                lo = std::min(lo, top - t);
                hi = std::max(hi, top - t);
            }
            if (!ok) continue;
            float span = std::max(0.5f, 0.7f * std::max(fb.x - fa.x, fb.y - fa.y));
            if ((hi - lo) / span > std::tan(scatter_slope * 0.0174533f)) continue;
            at.z = lo - 0.03f;
            m = q.m0 * translation(V3() - pivot) * rot_z(yaw) * translation(at);
            box_of(m, q.lo, q.hi, fa, fb);
            V3 ga = fa - V3(scatter_gap, scatter_gap, 0) * 0.5f, gb = fb + V3(scatter_gap, scatter_gap, 0) * 0.5f;
            ga.z += 0.1f;
            bool clear = true;
            for (auto& o : obst)
                if (ga.x < o.b.x && gb.x > o.a.x && ga.y < o.b.y && gb.y > o.a.y && ga.z < o.b.z && gb.z > o.a.z) { clear = false; break; }
            if (!clear) continue;
            if (q.lib >= 0) {
                const LibEntry& L = lib[q.lib];
                edits.cur.copies.push_back(CopyObj{-1, m, false, edits.asset_index(L.key, L.name, L.map)});
                assets = true;
            } else if (edits.entity_of(q.id) >= 0) edits.cur.copies.push_back({edits.entity_of(q.id), m, false});
            else {
                CopyObj o = edits.cur.copies[q.id - scene->entities.size()];
                o.matrix = m;
                o.group = -1;
                o.path.clear();
                o.removed = false;
                edits.cur.copies.push_back(o);
            }
            obst.push_back({fa, fb});
            made++;
        }
        if (assets) request_assets();
        if (made) { scatter_changed = true; scatter_made += made; }
        return made;
    }

    // ---- walk mode
    static int64_t walk_key(int gx, int gy) { return ((int64_t)gx << 32) ^ (uint32_t)gy; }
    // every solid collision triangle of the map as it is now (the edits' places, placed assets, merged pieces' member
    // shapes - collision_tris), in 4 m cells; every climb edge where its object stands now
    void walk_build() {
        walk_tris.clear();
        walk_grid.clear();
        walk_edges.clear();
        for (int id = 0; id < edits.count(); id++) {
            if (edits.removed(id)) continue;
            if (!edits.is_copy(id)) {
                const Entity& e = scene->entities[id];
                if (e.record >= 0 && record_hidden[e.record]) continue;
                if ((e.flags & F_MODE) && mode_record != e.record) continue;
            }
            collision_tris(id, [&](const V3* q, bool) {
                V3 n = cross(q[1] - q[0], q[2] - q[0]);
                float l = length(n);
                if (l < 1e-6f) return;
                walk_tris.push_back({q[0], q[1], q[2], n * (1.0f / l), id});
            });
            climb_edges_of(id, walk_edges);
        }
        for (int k = 0; k < (int)walk_tris.size(); k++) {
            const WalkTri& t = walk_tris[k];
            V3 lo = vmin(vmin(t.a, t.b), t.c), hi = vmax(vmax(t.a, t.b), t.c);
            if (hi.x - lo.x > 400 || hi.y - lo.y > 400) continue;      // (a sea plane: never under a player)
            for (int gx = (int)std::floor(lo.x / 4); gx <= (int)std::floor(hi.x / 4); gx++)
                for (int gy = (int)std::floor(lo.y / 4); gy <= (int)std::floor(hi.y / 4); gy++) walk_grid[walk_key(gx, gy)].push_back(k);
        }
        walk_built_at = edits.at;
    }
    template <class F> void walk_near(V3 p, float r, F fn) {
        std::vector<int> seen;
        for (int gx = (int)std::floor((p.x - r) / 4); gx <= (int)std::floor((p.x + r) / 4); gx++)
            for (int gy = (int)std::floor((p.y - r) / 4); gy <= (int)std::floor((p.y + r) / 4); gy++) {
                auto it = walk_grid.find(walk_key(gx, gy));
                if (it == walk_grid.end()) continue;
                for (int k : it->second)
                    if (std::find(seen.begin(), seen.end(), k) == seen.end()) { seen.push_back(k); fn(walk_tris[k]); }
            }
    }
    static V3 closest_on_tri(V3 p, V3 a, V3 b, V3 c) {      // (Ericson, Real-Time Collision Detection 5.1.5)
        V3 ab = b - a, ac = c - a, ap = p - a;
        float d1 = dot(ab, ap), d2 = dot(ac, ap);
        if (d1 <= 0 && d2 <= 0) return a;
        V3 bp = p - b;
        float d3 = dot(ab, bp), d4 = dot(ac, bp);
        if (d3 >= 0 && d4 <= d3) return b;
        float vc = d1 * d4 - d3 * d2;
        if (vc <= 0 && d1 >= 0 && d3 <= 0) return a + ab * (d1 / (d1 - d3));
        V3 cp = p - c;
        float d5 = dot(ab, cp), d6 = dot(ac, cp);
        if (d6 >= 0 && d5 <= d6) return c;
        float vb = d5 * d2 - d1 * d6;
        if (vb <= 0 && d2 >= 0 && d6 <= 0) return a + ac * (d2 / (d2 - d6));
        float va = d3 * d6 - d5 * d4;
        if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) return b + (c - b) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)));
        float den = 1.0f / (va + vb + vc);
        return a + ab * (vb * den) + ac * (vc * den);
    }
    // the highest walkable surface (slope under 50 degrees) under (x, y) between lo and hi, or false
    bool walk_floor(float x, float y, float lo, float hi, float& z) {
        bool got = false;
        walk_near(V3(x, y, 0), 0.1f, [&](const WalkTri& t) {
            if (t.n.z < 0.64f && t.n.z > -0.64f) return;
            // the vertical line through (x, y) against the triangle (barycentric in x, y)
            float d = (t.b.y - t.c.y) * (t.a.x - t.c.x) + (t.c.x - t.b.x) * (t.a.y - t.c.y);
            if (std::fabs(d) < 1e-9f) return;
            float u = ((t.b.y - t.c.y) * (x - t.c.x) + (t.c.x - t.b.x) * (y - t.c.y)) / d;
            float v = ((t.c.y - t.a.y) * (x - t.c.x) + (t.a.x - t.c.x) * (y - t.c.y)) / d;
            if (u < 0 || v < 0 || u + v > 1) return;
            float h = u * t.a.z + v * t.b.z + (1 - u - v) * t.c.z;
            if (h >= lo && h <= hi && (!got || h > z)) { z = h; got = true; }
        });
        return got;
    }
    void walk_say(const std::string& m) { walk_msg = m; walk_msg_t = ImGui::GetTime(); }
    void walk_enter(V3 at) {
        if (!scene || workspace) return;
        if (!climbv.loaded) load_climb();
        walk_build();
        float z;
        walk_p = walk_floor(at.x, at.y, at.z - 50, at.z + 3, z) ? V3(at.x, at.y, z) : at;
        walk_start = walk_p;
        walk_v = V3();
        walk_ground = false;
        walk_top = walk_p.z;
        walking = true;
        sculpting = drawing_region = scattering = false;
        selection.clear();
        status = "Walk: W A S D move, Shift runs, Ctrl walks, Space jumps (or climbs a ledge in reach), right mouse looks, "
                 "R back to the start, V / Esc leaves";
    }
    // one step of the walker: input -> velocity -> moved, pushed out of walls and ceilings, onto the floor
    void walk_step(float dt, const WalkInput& in) {
        const float R = 0.35f, H = 1.8f, STEP = 0.45f, G = 18.0f;
        if (walk_built_at != edits.at) walk_build();
        float speed = in.slow ? 1.6f : in.run ? 7.5f : 5.0f;
        V3 f(std::cos(cam.yaw), std::sin(cam.yaw), 0), r(std::sin(cam.yaw), -std::cos(cam.yaw), 0);
        V3 want = f * in.fwd + r * in.side;
        if (length(want) > 1e-3f) want = normalize(want) * speed;
        float k = walk_ground ? 1.0f : std::min(1.0f, dt * 2.0f);              // (a little steering in the air)
        walk_v.x += (want.x - walk_v.x) * k;
        walk_v.y += (want.y - walk_v.y) * k;
        if (in.jump && walk_ground && !walk_climb()) { walk_v.z = 5.0f; walk_ground = false; }
        else if (in.jump && !walk_ground) walk_climb();
        for (int sub = 0; sub < 4; sub++) {
            float h = dt / 4;
            walk_v.z -= G * h;
            walk_p = walk_p + walk_v * h;
            // walls and ceilings: three spheres up the capsule (the lowest above the step height), pushed out
            for (int pass = 0; pass < 2; pass++)
                for (float cz : {STEP + R, H * 0.5f + 0.2f, H - R}) {
                    V3 c = walk_p + V3(0, 0, cz);
                    walk_near(c, R + 0.5f, [&](const WalkTri& t) {
                        if (t.n.z >= 0.64f) return;                        // (floors: the floor test below)
                        V3 q = closest_on_tri(c, t.a, t.b, t.c);
                        V3 d = c - q;
                        float l = length(d);
                        if (l >= R || l < 1e-6f) return;
                        V3 push = d * ((R - l) / l);
                        if (t.n.z <= -0.64f) {                              // a ceiling: stop rising
                            walk_p.z += std::min(0.0f, push.z);
                            walk_v.z = std::min(walk_v.z, 0.0f);
                        } else {                                            // a wall: out sideways
                            push.z = 0;
                            walk_p = walk_p + push;
                            c = c + push;
                        }
                    });
                }
            // the floor: the highest walkable surface from a step above the feet to just below them
            float z;
            float below = walk_ground ? 0.35f : 0.02f;
            if (walk_v.z <= 0 && walk_floor(walk_p.x, walk_p.y, walk_p.z - below, walk_p.z + STEP, z)) {
                if (!walk_ground && walk_top - z > 2.0f) { walk_fall = walk_top - z; walk_say("fell " + std::to_string(walk_fall).substr(0, 4) + " m"); }
                walk_p.z = z;
                walk_v.z = 0;
                walk_ground = true;
                walk_top = z;
            } else {
                walk_ground = false;
                walk_top = std::max(walk_top, walk_p.z);
            }
        }
        if (walk_p.z < walk_start.z - 200) { walk_p = walk_start; walk_v = V3(); walk_say("fell out of the world - back to the start"); }
    }
    // a climb edge in reach (in front, within 0.9 m of the capsule, its top 0.3 - 2.4 m over the feet): up onto it
    // (a way up a wall with ledges / handhold rows is Space at each - stock ledges stack <= 1.35 m apart)
    bool walk_climb() {
        V3 f(std::cos(cam.yaw), std::sin(cam.yaw), 0);
        int best = -1;
        float bd = 1e9f;
        for (int k = 0; k < (int)walk_edges.size(); k++) {
            const ClimbEdge& e = walk_edges[k];
            V3 ab = e.b - e.a;
            float t = std::clamp(dot(walk_p - e.a, ab) / std::max(dot(ab, ab), 1e-6f), 0.0f, 1.0f);
            V3 q = e.a + ab * t;
            V3 d(q.x - walk_p.x, q.y - walk_p.y, 0);
            float dh = length(d), up = q.z - walk_p.z;
            if (dh > 0.35f + 0.9f || up < 0.3f || up > 2.4f) continue;
            if (dh > 0.2f && dot(normalize(d), f) < 0.3f) continue;             // (in front)
            if (dh + up * 0.3f < bd) { bd = dh + up * 0.3f; best = k; }
        }
        if (best < 0) return false;
        const ClimbEdge& e = walk_edges[best];
        V3 ab = e.b - e.a;
        float t = std::clamp(dot(walk_p - e.a, ab) / std::max(dot(ab, ab), 1e-6f), 0.0f, 1.0f);
        V3 q = e.a + ab * t;
        V3 in(-e.hx, -e.hy, 0);                                                  // (away from the side you hang on)
        if (length(in) < 0.1f) in = V3(q.x - walk_p.x, q.y - walk_p.y, 0);
        in = length(in) > 1e-4f ? normalize(in) : f;
        float z;
        V3 top = q + in * 0.45f;
        if (walk_floor(top.x, top.y, q.z - 0.4f, q.z + 0.6f, z)) { walk_p = V3(top.x, top.y, z); walk_ground = true; walk_say("climbed onto a " + std::string(ClimbView::kind_name(e.kind))); }
        else { walk_p = V3(q.x - in.x * 0.4f, q.y - in.y * 0.4f, q.z - 1.6f); walk_ground = false; walk_say("hanging on a " + std::string(ClimbView::kind_name(e.kind)) + " - Space again to climb on"); }
        walk_v = V3();
        walk_top = walk_p.z;
        return true;
    }
    // the camera behind the walker; its input from the keys
    void walk_tick(bool hovered) {
        if (!walking) return;
        ImGuiIO& io = ImGui::GetIO();
        WalkInput in;
        if (!io.WantTextInput) {
            in.fwd = (ImGui::IsKeyDown(ImGuiKey_W) ? 1.0f : 0.0f) - (ImGui::IsKeyDown(ImGuiKey_S) ? 1.0f : 0.0f);
            in.side = (ImGui::IsKeyDown(ImGuiKey_D) ? 1.0f : 0.0f) - (ImGui::IsKeyDown(ImGuiKey_A) ? 1.0f : 0.0f);
            in.run = io.KeyShift;
            in.slow = io.KeyCtrl;
            in.jump = ImGui::IsKeyPressed(ImGuiKey_Space, false);
            if (ImGui::IsKeyPressed(ImGuiKey_R, false)) { walk_p = walk_start; walk_v = V3(); walk_say("back to the start"); }
        }
        if (hovered && ImGui::IsMouseDown(ImGuiMouseButton_Right)) {
            cam.yaw -= io.MouseDelta.x * 0.0035f;
            cam.pitch = std::clamp(cam.pitch - io.MouseDelta.y * 0.0035f, -1.4f, 1.2f);
        }
        walk_step(std::min(io.DeltaTime, 0.05f), in);
        cam.pos = walk_p + V3(0, 0, 1.7f) - cam.forward() * 3.2f;
    }

    // ---- Test in game
    static bool process_running(const std::wstring& exe) {
        HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if (snap == INVALID_HANDLE_VALUE) return false;
        PROCESSENTRY32W pe{};
        pe.dwSize = sizeof pe;
        bool found = false;
        for (BOOL ok = Process32FirstW(snap, &pe); ok && !found; ok = Process32NextW(snap, &pe))
            found = _wcsicmp(pe.szExeFile, exe.c_str()) == 0;
        CloseHandle(snap);
        return found;
    }
    // one click: build (when anything changed since the last build), install, start AC3; when it has run and then
    // been gone 20 s (its Uplay check exits and is relaunched twice at every start), the original goes back
    void test_in_game() {
        if (!scene || workspace) { status = "Open a map first"; return; }
        if (tg_state) { status = "Already testing - quit AC3 (the original comes back) or Build > Stop testing"; return; }
        if (!tg_dry && process_running(L"AC3MP.exe")) {
            status = "AC3 is running - close it first (a map can't be swapped under a running game)";
            return;
        }
        bool fresh = !last_dest.empty() && build_ok && edits.at == built_at;
        tg_restored = false;
        tg_chr = false;
        if (fresh) { tg_state = 2; tg_after_build(); return; }
        tg_state = 1;
        built_at_pending = edits.at;
        if (!build()) tg_state = 0;                    // (nothing to build, or the engine busy: build() said why)
    }
    void tg_after_build() {
        if (!build_ok) { tg_state = 0; status = "Test in game stopped: the build didn't check out (see Build)"; return; }
        built_at = built_at_pending;
        tg_state = 2;
        if (tg_dry) { tg_launch(); return; }
        if (!from_release) {
            tg_state = 0;
            status = "Test in game: this map isn't the release's (AC3's original) - use Build > Install to put it in on purpose";
            return;
        }
        engine.request("install", {{"dest", last_dest}}, [this](bool ok, const json& r, const std::string& e) {
            if (!ok) { tg_state = 0; status = "Test in game: install failed: " + e; add_log(status); return; }
            add_log("installed into the game: " + r.value("live", std::string()));
            refresh_live();
            tg_launch();
        });
    }
    void tg_launch() {
        tg_t = ImGui::GetTime();
        tg_gone = 0;
        tg_state = 3;
        if (tg_dry) {                                 // (self-test: a hidden stand-in that runs ~6 s)
            ShellExecuteW(nullptr, L"open", L"ping.exe", L"-n 7 127.0.0.1", nullptr, SW_HIDE);
            status = "Test in game (dry run): waiting for the stand-in";
            return;
        }
        if (game_exe.empty() || !file_exists(game_exe)) {
            status = "Test in game: installed, but AC3MP.exe wasn't found - start the game yourself";
            add_log(status);
            return;
        }
        std::wstring exe = widen(game_exe), dir = exe.substr(0, exe.find_last_of(L"\\/"));
        HINSTANCE h = ShellExecuteW(nullptr, L"open", exe.c_str(), nullptr, dir.c_str(), SW_SHOWNORMAL);
        std::string where = tg_chr ? "the customisation menu > " + persona_name(chr.loaded_id) : "Training > " + (scene ? scene->title : std::string());
        status = (INT_PTR)h > 32 ? "Test in game: AC3 is starting - in the game: " + where +
                                       (tg_restore ? (tg_chr ? ". The original DataPC comes back when you quit AC3." : ". The original comes back when you quit AC3.") : ".")
                                 : "Test in game: AC3 didn't start (" + std::to_string((INT_PTR)h) + ") - start it yourself";
        add_log(status);
    }
    void tg_tick() {
        if (tg_state < 3 || ImGui::GetTime() < tg_poll) return;
        tg_poll = ImGui::GetTime() + 1.0;
        bool on = process_running(tg_proc);
        double now = ImGui::GetTime();
        if (tg_state == 3) {
            if (on) { tg_state = 4; add_log("test in game: the game is running"); }
            else if (now - tg_t > 300) { tg_state = 0; status = "Test in game: the game didn't start in 5 minutes - stopped watching"; }
            return;
        }
        if (on) { tg_gone = 0; return; }
        if (!tg_gone) { tg_gone = now; return; }
        if (now - tg_gone < (tg_dry ? 3.0 : 20.0)) return;         // (Uplay relaunches it twice at every start)
        tg_state = 0;
        if (!tg_restore) { status = "Test in game: the game closed (your build is still installed)"; return; }
        tg_restored = true;
        if (tg_dry) { status = "Test in game (dry run): the stand-in closed - the original would go back now"; add_log(status); return; }
        if (tg_chr) { chr_restore_release(); status = "Test in game: the game closed - the original DataPC is going back"; return; }
        restore_original();
        status = "Test in game: the game closed - the original map is back";
    }
    void install_build(bool confirmed = false) {
        if (last_dest.empty() || engine.busy()) return;
        if (!from_release && !confirmed) { ask_install = true; return; }
        engine.request("install", {{"dest", last_dest}}, [this](bool ok, const json& r, const std::string& e) {
            status = ok ? "Installed into the game: " + r.value("live", std::string()) : "Install failed: " + e;
            add_log(status);
            refresh_live();
        });
    }

    void restore_original() {
        if (!scene || engine.busy()) return;
        engine.request("restore", {{"archive", scene->archive}}, [this](bool ok, const json& r, const std::string& e) {
            status = ok ? "The game's map is the original again (" + base_name(r.value("original", std::string())) + ")"
                        : "Restore failed: " + e;
            add_log(status);
            refresh_live();
        });
    }

    // ---------------------------------------------------------------------------------- asset library --
    // the engine's index state (it indexes missing maps in the background); every new index file is read here
    // A map archive added to (or removed from, or replaced in) the game folder shows up by itself: the map list is read
    // again every 5 s while the engine is idle, and a change re-lists the Library, which indexes a new map in the
    // background (its index is keyed by the file's name, size and time, so a replaced file is indexed again).
    void maps_watch() {
        if (!engine.running() || engine.busy() || (test_step >= 0 && !maps_watch_test) || ImGui::GetTime() < maps_next_poll) return;
        maps_next_poll = ImGui::GetTime() + 5.0;
        engine.request("maps", json::object(), [this](bool ok, const json& r, const std::string&) {
            if (!ok) return;
            std::vector<MapEntry> now;
            for (auto& m : r["maps"]) now.push_back({m["path"], m["title"], m["source"], m.value("kind", std::string())});
            auto same = [](const MapEntry& a, const MapEntry& b) { return a.path == b.path && a.title == b.title && a.source == b.source; };
            if (now.size() == maps.size() && std::equal(now.begin(), now.end(), maps.begin(), same)) return;
            int added = 0;
            for (auto& m : now)
                if (std::none_of(maps.begin(), maps.end(), [&](const MapEntry& o) { return o.path == m.path; })) added++;
            maps = now;
            add_log("maps: the game folder changed - " + std::to_string(maps.size()) + " map(s), " + std::to_string(added) + " new");
            if (lib_asked) lib_poll(true);
        });
    }

    void lib_poll(bool force = false) {
        if (!engine.running() || (!force && (ImGui::GetTime() < lib_next_poll || engine.busy()))) return;
        lib_next_poll = ImGui::GetTime() + 2.0;
        lib_asked = true;
        json args = json::object();
        if (!lib_prefer.empty()) args["prefer"] = lib_prefer;
        args["sp_wanted"] = json(std::vector<std::string>(sp_wanted.begin(), sp_wanted.end()));
        engine.request("library", args, [this](bool ok, const json& r, const std::string& e) {
            if (!ok) { add_log("library: " + e); lib_busy = false; return; }
            lib_maps = r.value("maps", json::array());
            lib_busy = r.value("busy", false);
            for (auto& m : lib_maps)
                if (m["index"].is_string() && !lib_read.count(m["index"].get<std::string>()))
                    lib_read_index(m["index"].get<std::string>());
        });
    }

    void lib_read_index(const std::string& path) {
        lib_read.insert(path);
        std::ifstream f(widen(path), std::ios::binary);
        std::stringstream ss;
        ss << f.rdbuf();
        json j = json::parse(ss.str(), nullptr, false);
        if (j.is_discarded()) { add_log("library: can't read " + path); return; }
        std::string archive = j.value("archive", std::string()), title = j.value("title", std::string());
        for (auto& e : j["entries"]) {
            LibEntry L;
            L.archive = archive;
            L.map = title;
            L.mesh = e.value("mesh", std::string());
            L.key = archive + "|" + L.mesh;
            L.name = e.value("name", std::string());
            L.rep = e.value("rep", std::string());
            L.collision = e.value("collision", std::string());
            for (int k = 0; k < 3; k++) { L.size[k] = e["size"][k].get<float>(); L.lo[k] = e["lo"][k].get<float>(); }
            L.nt = e.value("nt", 0);
            L.uses = e.value("uses", 0);
            L.n_materials = e.value("n_materials", 0);
            L.textured = e.value("textured", 0);
            L.copyable = e.value("copyable", false);
            L.ground = e.value("ground", false);
            L.sp = e.value("sp", false);
            for (auto& m : e["materials"]) L.materials += (L.materials.empty() ? "" : ", ") + m.get<std::string>();
            lib.push_back(L);
        }
        lib_dirty = true;
    }

    int lib_find(const std::string& key) const {
        for (size_t i = 0; i < lib.size(); i++)
            if (lib[i].key == key) return (int)i;
        return -1;
    }

    // the asset scene: every library model the edits use, and the preview's; loaded when one is missing
    void request_assets() {
        if (!engine.running()) return;
        if (assets_loading) { assets_again = true; return; }
        auto want = [&](const std::string& k) {
            if (!k.empty() && std::find(asset_keys_all.begin(), asset_keys_all.end(), k) == asset_keys_all.end())
                asset_keys_all.push_back(k);
        };
        for (auto& a : edits.assets) want(a.key);
        for (auto& a : stash_edits.assets) want(a.key);
        want(preview_key);
        std::vector<std::string> keys = asset_keys_all;
        bool have = true;
        for (auto& k : keys) have &= assets_at.count(k) > 0;
        if (keys.empty() || have) return;
        assets_loading = true;
        engine.request("assets", {{"keys", keys}}, [this, keys](bool ok, const json& r, const std::string& e) {
            assets_loading = false;
            if (!ok) {
                status = "Library: " + e;
                assets_error = e;
                add_log(status);
                if (!preview_key.empty() && !assets_at.count(preview_key)) preview_key.clear();   // (don't retry it)
                asset_keys_all.erase(std::remove_if(asset_keys_all.begin(), asset_keys_all.end(),
                                                    [&](const std::string& k) { return !assets_at.count(k); }),
                                     asset_keys_all.end());
            } else {
                assets_error.clear();
                auto sc = std::make_unique<Scene>();
                std::string err;
                if (!sc->load(r["cache"].get<std::string>(), err)) add_log("asset scene: " + err);
                else {
                    renderer.upload(*sc, err, nullptr, SLOT_ASSETS);
                    assets_scene = std::move(sc);
                    assets_at.clear();
                    asset_meta.clear();
                    for (size_t k = 0; k < keys.size(); k++) assets_at[keys[k]] = (int)k;
                    assets_keys_loaded = keys;
                    for (auto& m : r["assets"]) asset_meta.push_back(m);
                    asset_climb.assign(asset_meta.size(), AssetClimb());
                    for (size_t k = 0; k < asset_meta.size(); k++)
                        if (asset_meta[k].contains("climb")) asset_climb[k].read(asset_meta[k]["climb"]);
                    for (size_t k = 0; k < keys.size() && k < asset_meta.size(); k++)
                        if (asset_meta[k].contains("blend")) {      // (a blend group's slots)
                            const json& b = asset_meta[k]["blend"];
                            BlendInfo bi;
                            bi.key = keys[k];
                            bi.name = b.value("name", std::string());
                            bi.label = b.value("label", std::string());
                            bi.map = b.value("map", std::string());
                            for (auto& q : b.value("slots", json::array())) bi.slots.push_back({V3(q[0], q[1], q[2]), V3(q[3], q[4], q[5])});
                            blend_assets[keys[k]] = bi;
                        }
                }
            }
            if (assets_again) { assets_again = false; request_assets(); }
        });
    }

    // where a placed model goes: what the camera looks at (within 400 m), else 25 m ahead
    V3 placement_point() {
        if (live_place_pending) {                     // a mark chosen as the next place ("Place here")
            live_place_pending = false;
            return live_place_at;
        }
        float d = 0;
        int hit = pick(cam.pos, cam.forward(), &d);
        if (hit >= 0 && d < 400) return cam.pos + cam.forward() * d;
        V3 f = cam.forward();
        if (workspace) {                              // the grid (z = 0)
            if (f.z < -0.02f) return cam.pos + f * (-cam.pos.z / f.z);
            V3 p = cam.pos + f * 15.0f;
            return V3(p.x, p.y, 0);
        }
        return cam.pos + f * 25.0f;
    }

    // a library model into the open map, its bottom middle at `at`: this map's own model -> a copy of the object it
    // was read from (its own collision and climb data); another map's -> a library model (the build brings it in)
    int place_entry(int i, V3 at) {
        if (!scene || i < 0 || i >= (int)lib.size()) return -1;
        const LibEntry& L = lib[i];
        int made = -1;
        if (L.archive == scene->archive && L.copyable) {
            auto it = edits.by_uid.find(uid_from_hex(L.rep));
            if (it != edits.by_uid.end() &&
                scene->records[scene->entities[it->second].record].name.rfind("Cell", 0) == 0) {
                int src = it->second;
                V3 lo, hi;
                world_box(src, lo, hi);
                M4 m = edits.matrix(src);
                m.set_pos(at + (m.pos() - V3((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)));
                edits.cur.copies.push_back({src, m, false});
                made = (int)scene->entities.size() + (int)edits.cur.copies.size() - 1;
                status = L.name + ": this map's own model - a copy of " + scene->entities[src].name +
                         " (its collision and climb data too); move it with the gizmo";
            }
        }
        if (made < 0) {
            int a = edits.asset_index(L.key, L.name, L.map);
            M4 m = M4::identity();
            // (an SP model stands at its origin - Boston's houses carry a 3 m foundation below it, under SP's ground)
            m.set_pos(at - V3(L.lo[0] + L.size[0] / 2, L.lo[1] + L.size[1] / 2, L.sp && L.lo[2] < -0.3f ? 0.0f : L.lo[2]));
            CopyObj o{-1, m, false, a};
            attach_to_scope(o);
            edits.cur.copies.push_back(o);
            made = (int)scene->entities.size() + (int)edits.cur.copies.size() - 1;
            request_assets();
            status = L.name + " from " + L.map + " placed - move / turn it with the gizmo; Build brings it in "
                     "(its textures, collision" + (L.collision == "own" ? " (its own)" : " (its triangles)") +
                     ", climb edges)";
        }
        edits.commit();
        selection = {made};
        gizmo_op = ImGuizmo::TRANSLATE;
        add_log(status);
        return made;
    }

    // ---------------------------------------------------------- primitives, user assets, the asset editor --
    static bool is_prim_key(const std::string& k) { return k.rfind("prim:", 0) == 0; }
    static bool is_ground_key(const std::string& k) { return k.rfind("ground:", 0) == 0; }
    static bool is_special(const std::string& k) { return is_prim_key(k) || is_ground_key(k) || k.rfind("blend:", 0) == 0; }   // (no plain source model)
    // a blend group's marker: the disc that poses a static NPC group (GameplayCoordinatorComponent, no collision -
    // AC3MP_GEN_Static_Group_Posing_*). Placed as a plain model it draws as a solid octagon, so a blend group
    // goes into assets as a "blend" part (built by the gameplay engine). A bench whose coordinator seats NPCs has
    // collision and stays a plain bench.
    static bool is_blend_marker(const Entity& e) {
        return e.components.find("GameplayCoordinatorComponent") != std::string::npos &&
               e.components.find("InertComponent") == std::string::npos;
    }
    int blend_left_out = 0;                           // (the last save / Make asset from building)
    static json prim_spec(const std::string& k) { return json::parse(k.substr(5), nullptr, false); }
    static double r2(double v) { return std::round(std::max(0.05, v) * 100.0) / 100.0; }
    static std::string prim_name(const json& sp) {
        std::string kind = sp.value("kind", std::string("box"));
        std::string label = kind == "wall" ? "Wall" : kind == "slab" ? "Floor slab" : "Box";
        char b[96];
        snprintf(b, sizeof b, " %g x %g x %g m", sp["w"].get<double>(), sp["d"].get<double>(), sp["h"].get<double>());
        return label + b;
    }
    static bool mat_from(const json& a, M4& m) {
        if (!a.is_array() || a.size() != 16) return false;
        for (int i = 0; i < 16; i++) m.m[i] = a[i].get<float>();
        return true;
    }

    // the material a new primitive gets: the one chosen ("Use for new walls..."), else the Library selection's first
    bool pick_prim_material() {
        if (!prim_m.empty()) return true;
        auto it = assets_at.find(lib_sel_key);
        if (it != assets_at.end() && it->second < (int)asset_meta.size())
            for (auto& mt : asset_meta[it->second].value("materials", json::array()))
                if (mt.value("textured", false)) {
                    prim_m = lib_sel_key;
                    prim_mk = mt["key"].get<std::string>();
                    prim_mn = mt["name"].get<std::string>();
                    return true;
                }
        return false;
    }

    // a wall / box / floor slab (bottom middle at `at`, turned `yaw` about the vertical), built through the library
    // path: its picture a new material, the box its collision, climb edges on its top edges (climbing: generated)
    int add_primitive(const std::string& kind, V3 at, float yaw, double w, double d, double h) {
        if (!scene) { status = "Open a map (or the asset editor) first"; return -1; }
        if (!pick_prim_material()) {
            status = "Pick a model in the Library first: its material textures walls, boxes and slabs "
                     "(or choose one under its preview)";
            focus_library = true;
            return -1;
        }
        json sp = {{"kind", kind}, {"w", r2(w)}, {"d", r2(d)}, {"h", r2(h)}, {"m", prim_m}, {"mk", prim_mk}, {"mn", prim_mn}};
        int a = edits.asset_index("prim:" + sp.dump(), prim_name(sp), prim_mn);
        M4 m = rot_z(yaw);
        m.set_pos(at);
        CopyObj o{-1, m, false, a};
        attach_to_scope(o);
        edits.cur.copies.push_back(o);
        int id = (int)scene->entities.size() + (int)edits.cur.copies.size() - 1;
        edits.commit();
        selection = {id};
        gizmo_op = ImGuizmo::TRANSLATE;
        request_assets();
        status = prim_name(sp) + " in " + prim_mn + " - size it in the Inspector, move it with the gizmo (solid, climbable on top)";
        add_log(status);
        return id;
    }
    int add_primitive(const std::string& kind) {
        double w = kind == "box" ? 1 : 4, d = kind == "wall" ? 0.3 : kind == "slab" ? 4 : 1, h = kind == "wall" ? 3 : kind == "slab" ? 0.3 : 1;
        return add_primitive(kind, placement_point(), cam.yaw - 1.5707963f, w, d, h);
    }
    void set_prim(CopyObj& co, const json& sp) {
        co.asset = edits.asset_index("prim:" + sp.dump(), prim_name(sp), sp.value("mn", std::string()));
        edits.commit();
        request_assets();
    }

    // user asset parts: map file names and model uids (resolved to the installed game's current files), matrices
    // relative to the asset's pivot - no game data
    std::string archive_for(const std::string& stored) const {
        std::string b = base_name(stored);
        for (auto& m : maps)
            if (m.kind == "release" && base_name(m.path) == b) return m.path;
        return stored;
    }
    json portable(const std::string& key) const {
        if (is_blend_key(key)) {                      // a blend group: its map and its entity
            size_t k = key.rfind('|');
            std::string a = key.substr(6, k - 6);
            return {{"blend", {{"archive", a}, {"map", base_name(a)}, {"entity", key.substr(k + 1)}}}};
        }
        if (is_ground_key(key)) {
            json g = json::parse(key.substr(7), nullptr, false);
            std::string a = g.value("a", std::string());
            return {{"ground", {{"archive", a}, {"map", base_name(a)}, {"footprint", g["fp"]}, {"exclude", g["ex"]}}}};
        }
        if (is_prim_key(key)) {
            json sp = prim_spec(key);
            std::string mk = sp.value("m", std::string());
            size_t k = mk.rfind('|');
            json mat = {{"archive", mk.substr(0, k)}, {"map", base_name(mk.substr(0, k))}, {"mesh", mk.substr(k + 1)},
                        {"key", sp["mk"]}, {"name", sp["mn"]}};
            return {{"prim", {{"kind", sp["kind"]}, {"w", sp["w"]}, {"d", sp["d"]}, {"h", sp["h"]}, {"material", mat}}}};
        }
        size_t k = key.rfind('|');
        std::string mesh = key.substr(k + 1), ent;
        if (size_t h = mesh.find('#'); h != std::string::npos) { ent = mesh.substr(h + 1); mesh = mesh.substr(0, h); }
        json m = {{"archive", key.substr(0, k)}, {"map", base_name(key.substr(0, k))}, {"mesh", mesh}};
        if (!ent.empty()) m["entity"] = ent;          // (as that object has it: its own collision and climb data)
        return {{"model", m}};
    }
    std::string ground_key(const std::string& archive, const json& fp, const json& ex) const {
        return "ground:" + json({{"a", archive_for(archive)}, {"fp", fp}, {"ex", ex}}).dump();
    }
    std::string key_of_part(const json& p) const {
        if (p.contains("blend")) {
            const json& b = p["blend"];
            return "blend:" + archive_for(b["archive"].get<std::string>()) + "|" + b["entity"].get<std::string>();
        }
        if (p.contains("ground")) {
            const json& g = p["ground"];
            return ground_key(g["archive"].get<std::string>(), g["footprint"], g.value("exclude", json::array()));
        }
        if (p.contains("prim")) {
            const json& q = p["prim"];
            const json& mt = q["material"];
            json sp = {{"kind", q["kind"]}, {"w", q["w"]}, {"d", q["d"]}, {"h", q["h"]},
                       {"m", archive_for(mt["archive"].get<std::string>()) + "|" + mt["mesh"].get<std::string>()},
                       {"mk", mt["key"]}, {"mn", mt["name"]}};
            return "prim:" + sp.dump();
        }
        const json& q = p["model"];
        return archive_for(q["archive"].get<std::string>()) + "|" + q["mesh"].get<std::string>() +
               (q.contains("entity") ? "#" + q["entity"].get<std::string>() : std::string());
    }
    // a map object's model as a library key, as THAT object has it (its own collision and climb data: the
    // barracks' stair prop's landing collision is its own, another object with the same model has none)
    std::string blend_key(const Entity& e) const { return "blend:" + scene->archive + "|" + uid_hex(e.uid); }
    std::string entity_key(const Entity& e) const {
        return scene->archive + "|" + uid_hex(scene->meshes[e.mesh].uid) + "#" + uid_hex(e.uid);
    }
    M4 bottom_middle(const std::vector<int>& ids) {
        V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
        for (int id : ids) { V3 a, b; world_box(id, a, b); lo = vmin(lo, a); hi = vmax(hi, b); }
        return translation(V3((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z));
    }
    // ---- user assets (format ac3studio-asset/2, assets\README.md): parts are models, primitives or other assets
    // (nested, by id = file name), each with an id ("p3") and a matrix relative to the asset's pivot
    static std::string stem(const std::string& path) {
        std::string b = base_name(path);
        return b.size() > 5 && b.substr(b.size() - 5) == ".json" ? b.substr(0, b.size() - 5) : b;
    }
    const UserAsset* asset_by_id(const std::string& id) const {
        for (auto& u : user_assets)
            if (stem(u.path) == id) return &u;
        return nullptr;
    }
    static std::string part_id(const json& p, size_t k) { return p.value("id", "p" + std::to_string(k)); }
    static json mat_json(const M4& m) {
        json a = json::array();
        for (float f : m.m) a.push_back(f);
        return a;
    }
    struct Leaf { std::string path, key, name; M4 m; bool solid = true; int climb = -1; };
    // an asset's leaves (models, primitives) with their part paths and matrices (`root` = its pivot's placement)
    void flatten(const json& a, const M4& root, const std::string& prefix, std::vector<Leaf>& out,
                 std::map<std::string, std::string>* names, int depth, std::string& problems) const {
        if (depth > 8) { problems += "assets nested more than 8 deep (a loop?); "; return; }
        size_t k = 0;
        for (auto& p : a.value("parts", json::array())) {
            std::string path = prefix.empty() ? part_id(p, k) : prefix + "/" + part_id(p, k);
            k++;
            M4 rel;
            if (!mat_from(p["matrix"], rel)) continue;
            if (p.contains("asset")) {
                const UserAsset* c = asset_by_id(p["asset"].get<std::string>());
                if (!c) { problems += "no asset " + p["asset"].get<std::string>() + "; "; continue; }
                if (names) (*names)[path] = c->name;
                flatten(c->j, rel * root, path, out, names, depth + 1, problems);
                continue;
            }
            std::string key = key_of_part(p);
            std::string name = p.value("name", is_prim_key(key) ? prim_name(prim_spec(key)) : std::string("part"));
            if (names) (*names)[path] = name;
            int cl = climb_from(p.value("climb", std::string()));
            out.push_back({path, key, name, rel * root, p.value("solid", true), cl == default_climb(key) ? -1 : cl});
        }
    }
    // the parts of `ids` as asset parts (pivot = the asset's): whole placed assets among them become child-asset
    // parts, everything else model / primitive parts
    json parts_of(const std::vector<int>& ids, const M4& pivot, int* skipped) {
        json parts = json::array();
        M4 inv = inverse(pivot);
        int skip = 0;
        std::set<int> whole;                          // placed assets every (left) part of which is in `ids`
        {
            std::map<int, int> in_ids, all;
            for (int id : ids)
                if (edits.is_copy(id) && !edits.removed(id)) in_ids[edits.cur.copies[id - scene->entities.size()].group]++;
            for (auto& c : edits.cur.copies)
                if (!c.removed && c.group >= 0) all[c.group]++;
            for (auto& [g, n] : in_ids)
                if (g >= 0 && n == all[g] && !edits.groups[g].asset.empty() && asset_by_id(edits.groups[g].asset)) whole.insert(g);
        }
        for (int g : whole)
            parts.push_back({{"asset", edits.groups[g].asset}, {"name", edits.groups[g].name},
                             {"matrix", mat_json(edits.root(g) * inv)}});
        for (int id : ids) {
            if (edits.removed(id)) continue;
            std::string key, name;
            bool solid = true;
            if (edits.is_copy(id) && whole.count(edits.cur.copies[id - scene->entities.size()].group)) continue;
            if (edits.is_placed(id)) {
                const CopyObj& co = edits.cur.copies[id - scene->entities.size()];
                key = edits.assets[co.asset].key;
                name = edits.assets[co.asset].name;
                solid = co.solid;
            } else {
                const Entity& e = scene->entities[edits.entity_of(id)];
                if (e.mesh < 0 || scene->archive.empty()) { skip++; continue; }
                if (is_blend_marker(e) && !blend_of(id)) { blend_left_out++; continue; }   // (not a CrowdLife group)
                key = blend_of(id) ? blend_key(e) : entity_key(e);
                name = e.name;
            }
            json p = portable(key);
            p["name"] = name;
            p["solid"] = solid;
            int cl = edits.is_copy(id) ? edits.cur.copies[id - scene->entities.size()].climb : -1;
            if (cl >= 0) p["climb"] = climb_name(cl);
            p["matrix"] = mat_json(edits.matrix(id) * inv);
            parts.push_back(p);
        }
        for (size_t k = 0; k < parts.size(); k++) parts[k]["id"] = "p" + std::to_string(k);
        if (skipped) *skipped = skip;
        return parts;
    }
    static std::string slug_of(const std::string& name) {
        std::string slug;
        for (char c : name) slug += std::isalnum((unsigned char)c) ? (char)std::tolower((unsigned char)c) : '_';
        return slug;
    }
    std::string write_asset(json j, std::string path) {
        if (path.empty()) {
            std::string slug = slug_of(j.value("name", std::string("asset")));
            make_dirs(widen(user_dir));
            path = user_dir + "\\" + slug + ".json";
            for (int n = 2; file_exists(path); n++) path = user_dir + "\\" + slug + "_" + std::to_string(n) + ".json";
        }
        SYSTEMTIME t;
        GetLocalTime(&t);
        char day[32];
        snprintf(day, sizeof day, "%04d-%02d-%02d", t.wYear, t.wMonth, t.wDay);
        j["format"] = "ac3studio-asset/2";
        j["saved"] = day;
        std::ofstream f(widen(path), std::ios::binary);
        f << j.dump(1);
        if (!f) return "";
        f.close();
        scan_user_assets();
        return path;
    }
    std::string write_user_asset(const std::string& name, const json& parts, std::string path, const std::string& made_on) {
        return write_asset({{"name", name}, {"made_on", made_on}, {"parts", parts}}, path);
    }
    // ---- categories
    cats::Store& categories() {
        std::string want = user_dir + "\\categories.json";
        if (!catstore.loaded || catstore.path != want) catstore.load(want);
        return catstore;
    }
    // a "file under" menu: every category (the current one ticked), a new one, back to automatic -> the choice
    // ("" = automatic), or nothing chosen
    bool category_menu(const std::string& current, const std::string& automatic_cat, std::string& chosen) {
        bool done = false;
        for (auto& c : categories().all())
            if (ImGui::MenuItem(c.c_str(), nullptr, c == current)) { chosen = c; done = true; }
        ImGui::Separator();
        if (ImGui::MenuItem(("Automatic (" + automatic_cat + ")").c_str())) { chosen = ""; done = true; }
        ImGui::SetNextItemWidth(160);
        bool enter = ImGui::InputTextWithHint("##newcat", "New category", newcat_buf, sizeof newcat_buf,
                                              ImGuiInputTextFlags_EnterReturnsTrue);
        ImGui::SameLine();
        if ((ImGui::SmallButton("Add") || enter) && newcat_buf[0]) {
            chosen = newcat_buf;
            categories().custom.insert(chosen);
            newcat_buf[0] = 0;
            done = true;
            ImGui::CloseCurrentPopup();
        }
        return done;
    }
    void file_model(const std::string& name, bool ground, const std::string& cat) {
        auto& st = categories();
        if (cat.empty() || cat == cats::automatic(name, ground)) st.models.erase(name); else st.models[name] = cat;
        st.save();
        lib_dirty = true;
        status = name + ": filed under " + st.model(name, ground);
    }
    void file_asset(const std::string& id, const std::string& name, const std::string& cat) {
        auto& st = categories();
        if (cat.empty() || cat == cats::automatic(name)) st.assets.erase(id); else st.assets[id] = cat;
        st.save();
        status = name + ": filed under " + st.asset(id, name);
    }

    void scan_user_assets() {
        user_assets.clear();
        user_scanned = true;
        WIN32_FIND_DATAW fd;
        HANDLE h = FindFirstFileW(widen(user_dir + "\\*.json").c_str(), &fd);
        if (h == INVALID_HANDLE_VALUE) return;
        do {
            std::string path = user_dir + "\\" + narrow(fd.cFileName);
            std::ifstream f(widen(path), std::ios::binary);
            std::stringstream ss;
            ss << f.rdbuf();
            json j = json::parse(ss.str(), nullptr, false);
            if (j.is_discarded() || j.value("format", std::string()).rfind("ac3studio-asset/", 0) != 0 || !j["parts"].is_array()) continue;
            user_assets.push_back({path, j.value("name", narrow(fd.cFileName)), j.value("made_on", std::string()),
                                   (int)j["parts"].size(), j});
        } while (FindNextFileW(h, &fd));
        FindClose(h);
        std::sort(user_assets.begin(), user_assets.end(), [](const UserAsset& a, const UserAsset& b) { return a.name < b.name; });
    }
    std::string save_selection_asset(const std::string& name) {
        if (!scene || selection.empty()) { status = "Select what the asset is made of first"; return ""; }
        int skipped = 0;
        blend_left_out = 0;
        json parts = parts_of(selection, bottom_middle(selection), &skipped);
        if (parts.empty()) { status = "Nothing in the selection has a model to save"; return ""; }
        std::string p = write_user_asset(name, parts, "", workspace ? ws_made_on : scene->title);
        status = p.empty() ? "Couldn't write the asset into " + user_dir
                           : "Saved \"" + name + "\": " + std::to_string(parts.size()) + " part(s)" +
                                 (skipped ? " (" + std::to_string(skipped) + " marker(s) without a model left out)" : "") +
                                 (blend_left_out ? "; " + std::to_string(blend_left_out) + " blend marker(s) left out (not a crowd group, or the blend groups not read yet)" : "") +
                                 " - " + p;
        add_log(status);
        return p;
    }
    // Make asset from building: the building's EntityGroup pieces (its shell) stay parts of its own; every other map
    // object of the selection (windows, doors, benches, candles...) becomes a child asset, one per model - the same
    // model, the same child asset (written once, reused if it exists)
    // the engine's ground under the selection's building (footprint at its original place, its own objects to
    // leave out, the move since) -> `done(reply or null)`
    void request_building_ground(std::function<void(const json&)> done) {
        json uids = json::array(), current = json::object();
        for (int id : selection)
            if (!edits.is_copy(id) && !edits.removed(id)) {
                uids.push_back(uid_hex(scene->entities[id].uid));
                if (edits.moved(id)) current[uid_hex(scene->entities[id].uid)] = mat_json(edits.matrix(id));
            }
        if (uids.empty()) { done(json()); return; }
        status = "Finding the ground under the building...";
        engine.request("building_ground", {{"archive", scene->archive}, {"uids", uids}, {"current", current}},
                       [this, done](bool ok, const json& r, const std::string& e) {
                           if (!ok) add_log("the building's ground isn't included: " + e);
                           done(ok ? r : json());
                       });
    }
    void make_building_asset_async(const std::string& name) {
        std::vector<int> sel = selection;
        request_building_ground([this, name, sel](const json& g) {
            if (!scene || workspace) return;
            std::vector<int> keep = selection;
            selection = sel;
            make_building_asset(name, nullptr, nullptr, g);
            selection = keep;
        });
    }
    std::string make_building_asset(const std::string& name, int* n_children = nullptr, int* n_kinds = nullptr,
                                    const json& ground = json()) {
        if (!scene || selection.empty() || workspace) { status = "Select a building first (B)"; return ""; }
        std::map<std::string, int> groups_seen;
        for (int id : selection)
            if (!edits.is_copy(id) && !scene->entities[id].group.empty()) groups_seen[scene->entities[id].group]++;
        std::string shell;
        int best = 0;
        for (auto& [g, n] : groups_seen)
            if (n > best) { best = n; shell = g; }
        M4 pivot = bottom_middle(selection);
        M4 inv = inverse(pivot);
        json parts = json::array();
        std::set<std::string> kinds;
        int children = 0;
        std::vector<int> rest;
        blend_left_out = 0;
        for (int id : selection) {
            if (edits.removed(id)) continue;
            if (edits.is_copy(id)) { rest.push_back(id); continue; }
            const Entity& e = scene->entities[id];
            if (e.mesh < 0) continue;
            if (is_blend_marker(e)) {
                if (!blend_of(id)) { blend_left_out++; continue; }   // (not a crowd group, or not read yet)
                json p = portable(blend_key(e));         // a blend group: a part of the building asset itself
                p["name"] = e.name;
                p["matrix"] = mat_json(edits.matrix(id) * inv);
                parts.push_back(p);
                continue;
            }
            std::string key = entity_key(e);
            if (!shell.empty() && e.group == shell) {
                json p = portable(key);
                p["name"] = e.name;
                p["solid"] = true;
                p["matrix"] = mat_json(edits.matrix(id) * inv);
                parts.push_back(p);
                continue;
            }
            std::string model = scene->meshes[e.mesh].name;
            if (model.size() > 5 && model.substr(model.size() - 5) == "_LOD0") model = model.substr(0, model.size() - 5);
            std::string cid = slug_of(model);
            if (!asset_by_id(cid)) {
                json cp = portable(key);
                cp["id"] = "p0";
                cp["name"] = model;
                cp["solid"] = true;
                cp["matrix"] = mat_json(M4::identity());
                make_dirs(widen(user_dir));
                write_asset({{"name", model}, {"made_on", scene->title}, {"parts", json::array({cp})}}, user_dir + "\\" + cid + ".json");
            }
            kinds.insert(cid);
            children++;
            parts.push_back({{"asset", cid}, {"name", e.name}, {"matrix", mat_json(edits.matrix(id) * inv)}});
        }
        if (!rest.empty())                            // walls, library models, copies, placed assets in the selection
            for (auto& p : parts_of(rest, pivot, nullptr)) parts.push_back(p);
        // the ground it stands on (its floors and plot: the upper floor the barracks shares with the mess hall), cut
        // to its footprint from its map when used - the source map's world, moved as the building was
        bool with_ground = ground.is_object() && ground.contains("footprint") && ground["footprint"].size() >= 3;
        if (with_ground) {
            M4 T;
            if (!mat_from(ground["T"], T)) T = M4::identity();
            json gp = portable(ground_key(scene->archive, ground["footprint"], ground["exclude"]));
            gp["name"] = "Ground under " + (shell.empty() ? name : shell);
            gp["solid"] = true;
            gp["matrix"] = mat_json(T * inv);
            parts.push_back(gp);
        }
        for (size_t k = 0; k < parts.size(); k++) parts[k]["id"] = "p" + std::to_string(k);
        std::string p = write_user_asset(name, parts, "", scene->title);
        if (n_children) *n_children = children;
        if (n_kinds) *n_kinds = (int)kinds.size();
        status = p.empty() ? "Couldn't write the asset"
                           : "Saved \"" + name + "\": " + std::to_string(parts.size() - children - with_ground) + " shell part(s) (" +
                                 (shell.empty() ? std::string("no EntityGroup") : shell) + ") + " + std::to_string(children) +
                                 " child asset(s) of " + std::to_string(kinds.size()) + " kind(s)" +
                                 (with_ground ? " + the ground under it" : "") +
                                 (blend_left_out ? "; " + std::to_string(blend_left_out) + " blend marker(s) left out (not a crowd group, or the blend groups not read yet)" : "") +
                                 " - " + p;
        add_log(status);
        return p;
    }
    int entity_with_mesh(uint32_t mesh_uid) const {
        for (size_t i = 0; i < scene->entities.size(); i++) {
            const Entity& e = scene->entities[i];
            if (e.mesh >= 0 && scene->meshes[e.mesh].uid == mesh_uid && !(e.flags & (F_FIXED | F_SPAWN | F_MODE)) &&
                e.record >= 0 && scene->records[e.record].name.rfind("Cell", 0) == 0)
                return (int)i;
        }
        return -1;
    }
    // a user asset into the open map (or the asset editor), its pivot at `at`: its tree flattened to parts (copies
    // of the open map's own models - their own collision and climb data - or library models), all in one group
    std::vector<int> place_user_asset(const json& j, V3 at, const std::string& id = "") {
        std::vector<int> made;
        if (!scene) return made;
        M4 T = translation(at);
        std::vector<Leaf> leaves;
        std::map<std::string, std::string> names;
        std::string problems;
        flatten(j, T, "", leaves, &names, 0, problems);
        int g = edits.add_group(j.value("name", std::string("asset")) + " #" + std::to_string(edits.groups.size() + 1), id, T);
        edits.groups[g].part_names = names;
        int copies = 0, models = 0;
        for (auto& L : leaves) {
            int src = -1;
            // this map's own model, climbing as in its map (or generated on top): a copy, its own collision and climb
            if (!workspace && !is_special(L.key) && L.climb != CLIMB_NONE) {
                size_t k = L.key.rfind('|');
                std::string mesh = L.key.substr(k + 1), ent;
                if (size_t h = mesh.find('#'); h != std::string::npos) { ent = mesh.substr(h + 1); mesh = mesh.substr(0, h); }
                if (L.key.substr(0, k) == scene->archive) {
                    auto it = ent.empty() ? edits.by_uid.end() : edits.by_uid.find(uid_from_hex(ent));
                    const Entity* same = it != edits.by_uid.end() ? &scene->entities[it->second] : nullptr;
                    src = same && same->mesh >= 0 && scene->meshes[same->mesh].uid == uid_from_hex(mesh) &&
                                  scene->records[same->record].name.rfind("Cell", 0) == 0 ? it->second
                                                                                          : entity_with_mesh(uid_from_hex(mesh));
                }
            }
            if (!workspace && is_blend_key(L.key) && L.key.substr(6, L.key.rfind('|') - 6) == scene->archive) {
                auto it = edits.by_uid.find(uid_from_hex(L.key.substr(L.key.rfind('|') + 1)));
                if (it != edits.by_uid.end() && scene->records[scene->entities[it->second].record].name.rfind("Cell", 0) == 0)
                    src = it->second;                    // (a blend group of this map: a copy of it)
            }
            CopyObj o{src, L.m, false};
            o.climb = L.climb;
            if (src < 0) {
                int li = is_special(L.key) ? -1 : lib_find(L.key.substr(0, L.key.find('#')));
                std::string map = is_prim_key(L.key) ? prim_spec(L.key).value("mn", std::string())
                                  : is_ground_key(L.key) ? std::string("ground")
                                  : is_blend_key(L.key) ? base_name(L.key.substr(6, L.key.rfind('|') - 6))
                                  : li >= 0 ? lib[li].map : base_name(L.key.substr(0, L.key.rfind('|')));
                o.asset = edits.asset_index(L.key, L.name, map);
                o.solid = L.solid;
                models++;
            } else {
                copies++;
            }
            o.group = g;
            o.path = L.path;
            edits.cur.copies.push_back(o);
            made.push_back((int)scene->entities.size() + (int)edits.cur.copies.size() - 1);
        }
        edits.commit();
        selection = made;
        scope_group = g;
        scope_depth = workspace ? 1 : 0;
        gizmo_op = ImGuizmo::TRANSLATE;
        request_assets();
        status = j.value("name", std::string("asset")) + ": " + std::to_string(made.size()) + " part(s) placed (" +
                 std::to_string(copies) + " copies of this map's objects, " + std::to_string(models) +
                 " library models) - click picks the whole asset, double-click goes inside" + (problems.empty() ? "" : "; " + problems);
        add_log(status);
        return made;
    }
    // a new object joins the placed asset the selection is inside (an "add" on that placement)
    void attach_to_scope(CopyObj& o) {
        if (workspace || scope_group < 0 || scope_group >= (int)edits.groups.size()) return;
        o.group = scope_group;
        o.path = "+" + std::to_string(++plus_counter);
    }

    // the selection levels of a placed asset
    static int segments(const std::string& path) { return path.empty() ? 0 : 1 + (int)std::count(path.begin(), path.end(), '/'); }
    static std::string prefix_of(const std::string& path, int depth) {
        size_t at = 0;
        for (int k = 0; k < depth; k++) {
            size_t n = path.find('/', at);
            if (n == std::string::npos) return path;
            at = n + 1;
        }
        return depth == 0 ? "" : path.substr(0, at - 1);
    }
    std::vector<int> leaves_under(int g, const std::string& pre) const {
        std::vector<int> out;
        for (size_t k = 0; k < edits.cur.copies.size(); k++) {
            const CopyObj& c = edits.cur.copies[k];
            if (c.group != g || c.removed) continue;
            if (pre.empty() || c.path == pre || c.path.rfind(pre + "/", 0) == 0) out.push_back((int)(scene->entities.size() + k));
        }
        return out;
    }
    int min_depth(int g) const { return workspace && g == ws_group ? 1 : 0; }
    // a click (dbl: a double-click) on object `hit`
    void click_pick(int hit, bool add, bool dbl) {
        if (hit < 0 || !edits.is_copy(hit) || edits.cur.copies[hit - scene->entities.size()].group < 0) {
            if (!add) scope_group = -1;
            select(hit, add);
            if (!dbl && hit >= 0 && selected(hit)) ms_expand_thing(hit);   // a gameplay object's head, parts and FX
            if (hit >= 0) ms_corner = -1;
            return;
        }
        const CopyObj& c = edits.cur.copies[hit - scene->entities.size()];
        int g = c.group, segs = segments(c.path), lo = std::min(min_depth(g), segs);
        int depth = lo;
        if (g == scope_group) depth = dbl ? scope_depth + 1 : std::max(scope_depth, lo);
        depth = std::clamp(depth, lo, segs);
        std::vector<int> ids = leaves_under(g, prefix_of(c.path, depth));
        scope_group = g;
        scope_depth = depth;
        if (!add) selection.clear();
        for (int id : ids)
            if (!selected(id)) selection.push_back(id);
    }
    void scope_up() {
        if (scope_group < 0 || selection.empty() || scope_depth <= min_depth(scope_group)) {
            selection.clear();
            scope_group = -1;
            return;
        }
        int one = selection[0];
        scope_depth--;
        const CopyObj& c = edits.cur.copies[one - scene->entities.size()];
        selection = leaves_under(scope_group, prefix_of(c.path, scope_depth));
    }
    std::string scope_label() const {
        if (scope_group < 0 || scope_group >= (int)edits.groups.size() || selection.empty() || !edits.is_copy(selection[0])) return "";
        const GroupInfo& gi = edits.groups[scope_group];
        std::string pre = prefix_of(edits.cur.copies[selection[0] - scene->entities.size()].path, scope_depth);
        if (pre.empty()) return gi.name;
        auto it = gi.part_names.find(pre);
        return gi.name + " > " + (it != gi.part_names.end() ? it->second : pre);
    }

    // a placed asset's state written back into an asset definition: each part's place from its parts' places now
    // (relative to `R`, the asset's pivot), parts whose every object was removed dropped, objects added to the
    // placement ("+n" paths, `extras`) and other placed assets (`others`) as new parts. A child asset's inside
    // changed in this placement can't be written into the parent: `problems` says so (edit that asset instead).
    json apply_group(const json& a, int g, const M4& R, const std::vector<int>& extras, const std::vector<int>& others,
                     std::vector<std::string>& problems) {
        json out = a;
        out["parts"] = json::array();
        M4 invR = inverse(R);
        std::vector<Leaf> defaults;                   // the asset's own leaves, pivot at the origin
        std::string prob;
        flatten(a, M4::identity(), "", defaults, nullptr, 0, prob);
        std::map<std::string, M4> dflt;
        for (auto& L : defaults) dflt[L.path] = L.m;
        std::map<std::string, std::vector<int>> by_path;
        int next = 0;
        for (size_t k = 0; k < edits.cur.copies.size(); k++) {
            const CopyObj& c = edits.cur.copies[k];
            if (c.group == g && !c.removed) by_path[c.path].push_back((int)(scene->entities.size() + k));
        }
        auto leaves_of = [&](const std::string& id) {
            std::vector<int> v;
            for (auto& [path, ids] : by_path)
                if (path == id || path.rfind(id + "/", 0) == 0) v.insert(v.end(), ids.begin(), ids.end());
            return v;
        };
        size_t k = 0;
        for (auto& p : a.value("parts", json::array())) {
            std::string id = part_id(p, k++);
            if (id.size() > 1 && id[0] == 'p') next = std::max(next, std::atoi(id.c_str() + 1) + 1);
            std::vector<int> ids = leaves_of(id);
            int had = 0;
            for (auto& [path, m] : dflt) had += path == id || path.rfind(id + "/", 0) == 0;
            json np = p;
            np["id"] = id;
            if (ids.empty()) {
                if (had) continue;                    // removed from this placement: dropped from the asset
                out["parts"].push_back(np);           // (a part with nothing to show: kept as it is)
                continue;
            }
            if (!p.contains("asset")) {
                int id0 = ids[0];
                const CopyObj* co = edits.is_copy(id0) ? &edits.cur.copies[id0 - scene->entities.size()] : nullptr;
                np["matrix"] = mat_json(edits.matrix(id0) * invR);
                if (co && co->asset >= 0 && is_prim_key(edits.assets[co->asset].key)) np["prim"] = portable(edits.assets[co->asset].key)["prim"];
                if (co) np["solid"] = co->solid;
                if (co && co->climb >= 0) np["climb"] = climb_name(co->climb);
                else if (co) np.erase("climb");
            } else {
                M4 P;
                mat_from(p["matrix"], P);
                M4 first;
                bool same = (int)ids.size() == had, have = false;
                for (int i : ids) {
                    const std::string& path = edits.cur.copies[i - scene->entities.size()].path;
                    auto d = dflt.find(path);
                    if (d == dflt.end()) { same = false; continue; }
                    M4 inner = d->second * inverse(P);                  // the part within its child asset
                    M4 c = inverse(inner) * (edits.matrix(i) * invR);   // the child asset's place it implies
                    if (!have) { first = c; have = true; }
                    else if (!c.near_equal(first, 2e-3f)) same = false;
                }
                if (have) np["matrix"] = mat_json(first);
                if (!same) problems.push_back(p.value("name", id) + ": its inside changed in this placement - open " +
                                              p["asset"].get<std::string>() + " in the asset editor to change it");
            }
            out["parts"].push_back(np);
        }
        auto add_leaf = [&](int i) {
            const CopyObj* co = edits.is_copy(i) ? &edits.cur.copies[i - scene->entities.size()] : nullptr;
            std::string key, name;
            if (co && co->asset >= 0) { key = edits.assets[co->asset].key; name = edits.assets[co->asset].name; }
            else {
                const Entity& e = scene->entities[edits.entity_of(i)];
                if (e.mesh < 0 || scene->archive.empty()) return;
                key = entity_key(e);
                name = e.name;
            }
            json np = portable(key);
            np["id"] = "p" + std::to_string(next++);
            np["name"] = name;
            np["solid"] = co ? co->solid : true;
            if (co && co->climb >= 0) np["climb"] = climb_name(co->climb);
            np["matrix"] = mat_json(edits.matrix(i) * invR);
            out["parts"].push_back(np);
        };
        for (auto& [path, ids] : by_path)
            if (!path.empty() && path[0] == '+')
                for (int i : ids) add_leaf(i);
        for (int i : extras) add_leaf(i);
        for (int h : others)
            if (h >= 0 && h < (int)edits.groups.size() && asset_by_id(edits.groups[h].asset))
                out["parts"].push_back({{"id", "p" + std::to_string(next++)}, {"asset", edits.groups[h].asset},
                                        {"name", edits.groups[h].name}, {"matrix", mat_json(edits.root(h) * invR)}});
        return out;
    }
    // "Apply to asset": a placement's changes (moves, removals, additions) written into the asset's file
    std::string apply_to_asset(int g, std::vector<std::string>& problems) {
        if (g < 0 || g >= (int)edits.groups.size()) return "";
        const UserAsset* u = asset_by_id(edits.groups[g].asset);
        if (!u) { status = "This placement's asset file isn't in My assets any more"; return ""; }
        std::string path = u->path;
        json out = apply_group(u->j, g, edits.root(g), {}, {}, problems);
        std::string p = write_asset(out, path);
        status = p.empty() ? "Couldn't write " + path
                           : "Applied to \"" + out.value("name", std::string()) + "\": " + std::to_string(out["parts"].size()) +
                                 " part(s)" + (problems.empty() ? "" : " - " + std::to_string(problems.size()) + " child change(s) not written (see Log)");
        add_log(status);
        for (auto& q : problems) add_log("  " + q);
        return p;
    }

    // the asset editor
    void open_workspace(const std::string& name, const std::string& path, const json& asset, int lib_index) {
        if (workspace || load_state == 1 || load_state == 2) return;
        stash_scene = std::move(scene);
        stash_edits = std::move(edits);
        edits = Edits();
        stash_cam = cam;
        stash_sel = selection;
        stash_hidden = record_hidden;
        stash_mode = mode_record;
        stash_project = project_path;
        selection.clear();
        record_hidden.clear();
        mode_record = -1;
        dragging = false;
        scene = std::make_unique<Scene>();
        scene->title = "Asset: " + name;
        edits.reset(scene.get());
        workspace = true;
        ws_name = name;
        ws_path = path;
        if (asset.is_object()) {
            ws_asset = asset;
            ws_made_on = asset.value("made_on", std::string());
        } else {                                      // a library model: a one-part asset, its bottom middle at the origin
            const LibEntry& L = lib[lib_index];
            json p = portable(L.key);
            p["id"] = "p0";
            p["name"] = L.name;
            p["solid"] = true;
            p["matrix"] = mat_json(translation(V3(-(L.lo[0] + L.size[0] / 2), -(L.lo[1] + L.size[1] / 2), -L.lo[2])));
            ws_asset = {{"name", name}, {"made_on", L.map}, {"parts", json::array({p})}};
            ws_made_on = L.map;
        }
        place_user_asset(ws_asset, V3(0, 0, 0), stem(path));
        ws_group = (int)edits.groups.size() - 1;
        edits.history.clear();                        // (as opened: nothing to undo, nothing changed)
        edits.history.push_back(edits.cur);
        edits.at = edits.saved_at = 0;
        selection.clear();
        scope_group = -1;
        frame_all();
        status = "Asset editor: " + name + " - click a part (double-click goes into a child asset), move / turn / copy / "
                 "remove, add walls (toolbar); Save on the toolbar";
        add_log(status);
    }
    void close_workspace() {
        if (!workspace) return;
        selection.clear();
        dragging = false;
        scene = std::move(stash_scene);
        edits = std::move(stash_edits);
        stash_edits = Edits();
        cam = stash_cam;
        selection = stash_sel;
        record_hidden = stash_hidden;
        mode_record = stash_mode;
        project_path = stash_project;
        workspace = false;
        ws_path.clear();
        ws_name.clear();
        ws_group = -1;
        scope_group = -1;
        status = scene ? "Back to " + scene->title : "Asset editor closed";
        request_assets();
    }
    bool save_workspace(bool as_new, const std::string& new_name = "") {
        if (!workspace) return false;
        if (ws_path.empty() && !as_new) { ask_name_for = 1; ask_name = true; return false; }
        std::vector<int> extras, others;
        std::set<int> other_groups;
        for (int id = 0; id < edits.count(); id++) {
            if (edits.removed(id)) continue;
            int g = edits.cur.copies[id - scene->entities.size()].group;
            if (g < 0) extras.push_back(id);
            else if (g != ws_group) other_groups.insert(g);
        }
        others.assign(other_groups.begin(), other_groups.end());
        std::vector<std::string> problems;
        json out = apply_group(ws_asset, ws_group, M4::identity(), extras, others, problems);   // (the grid's origin = its pivot)
        if (out["parts"].empty()) { status = "The asset has no parts"; return false; }
        std::string name = as_new ? new_name : ws_name;
        out["name"] = name;
        out["made_on"] = ws_made_on;
        std::string p = write_asset(out, as_new ? "" : ws_path);
        if (p.empty()) { status = "Couldn't write the asset"; return false; }
        // what is saved is what is edited from now on
        ws_asset = read_json(p);
        ws_path = p;
        ws_name = name;
        scene->title = "Asset: " + name;
        edits.commit();
        edits.saved_at = edits.at;
        status = "Saved \"" + name + "\": " + std::to_string(out["parts"].size()) + " part(s) - " + p;
        add_log(status);
        for (auto& q : problems) add_log("  " + q);
        return true;
    }
    static json read_json(const std::string& path) {
        std::ifstream f(widen(path), std::ios::binary);
        std::stringstream ss;
        ss << f.rdbuf();
        return json::parse(ss.str(), nullptr, false);
    }

    // climbing of placed models / copies, and "Make climbable" for map objects - for every one of `ids`
    void ui_climb(const std::vector<int>& ids) {
        std::vector<int> objs, ents;
        for (int id : ids) {
            if (edits.removed(id)) continue;
            if (edits.is_copy(id)) objs.push_back(id);
            else if (edits.movable(id) && scene->entities[id].mesh >= 0) ents.push_back(id);
        }
        static const char* NAMES[] = {"As in its own map", "Climbable everywhere (generated)", "Not climbable",
                                      "Climbable outside (rows + ledges on outer walls)"};
        if (!objs.empty()) {
            int shown = -2;
            bool copy = false;
            for (int id : objs) {
                const CopyObj& c = edits.cur.copies[id - scene->entities.size()];
                int eff = c.climb >= 0 ? c.climb : c.asset >= 0 ? default_climb(edits.assets[c.asset].key) : CLIMB_NATIVE;
                shown = shown == -2 || shown == eff ? eff : -1;
                copy |= c.asset < 0;
            }
            ImGui::SetNextItemWidth(-1);
            if (ImGui::BeginCombo("##climb", shown >= 0 ? (std::string("Climbing: ") + NAMES[shown]).c_str() : "Climbing: (mixed)")) {
                for (int k = 0; k < 4; k++) {
                    bool off = copy && k == CLIMB_NONE;         // (a copy keeps its object's climb data)
                    if (ImGui::Selectable(NAMES[k], shown == k, off ? ImGuiSelectableFlags_Disabled : 0)) {
                        for (int id : objs) {
                            CopyObj& c = edits.cur.copies[id - scene->entities.size()];
                            int dflt = c.asset >= 0 ? default_climb(edits.assets[c.asset].key) : CLIMB_NATIVE;
                            c.climb = k == dflt ? -1 : k;
                        }
                        edits.commit();
                    }
                }
                ImGui::EndCombo();
            }
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip("As in its own map: the climb data it has where it comes from (none if it had none).\n"
                                  "Climbable everywhere: ledges on its top edges and handhold rows up big walls.\n"
                                  "Not climbable: no climb data (not for copies of this map's objects).\n"
                                  "Climbable outside: as in its own map, plus handhold rows and ledges on the outer\n"
                                  "walls of the building it is part of (an invisible layer; inside walls get nothing,\n"
                                  "its own collision - FuzzyZone walls too - stays as it is). Choose it for the whole\n"
                                  "placed asset: the outer walls are found from every part set to it.");
        }
        if (!ents.empty()) {
            bool all = true;
            for (int id : ents) all &= edits.cur.climbable[id] != 0;
            bool v = all;
            std::string label = ents.size() == 1 ? "Make climbable (ledges + handholds added)"
                                                 : "Make these " + std::to_string(ents.size()) + " climbable (ledges + handholds added)";
            if (ImGui::Checkbox(label.c_str(), &v)) {
                for (int id : ents) edits.cur.climbable[id] = v ? 1 : 0;
                edits.commit();
            }
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip("Build adds an invisible copy of its collision with generated ledges and handhold\n"
                                  "beams - a building that can't be climbed becomes climbable. The object is untouched.");
            bool all_out = true;
            for (int id : ents) all_out &= edits.cur.climbable[id] == 2;
            bool o = all && all_out;
            if (ImGui::Checkbox("Only its building's outer walls", &o)) {
                for (int id : ents) edits.cur.climbable[id] = o ? 2 : 1;
                edits.commit();
            }
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip("Climbable outside: handhold rows and ledges on the outer walls of its whole building\n"
                                  "(what Select Building picks from this piece), on an invisible layer. Inside walls get\n"
                                  "nothing; its own collision - FuzzyZone walls too - stays as it is.");
        }
    }

    void ui_prim(CopyObj& co) {
        json sp = prim_spec(edits.assets[co.asset].key);
        if (sp.is_discarded()) return;
        if (prim_edit_asset != co.asset) {
            prim_edit[0] = sp["w"].get<float>(); prim_edit[1] = sp["d"].get<float>(); prim_edit[2] = sp["h"].get<float>();
            prim_edit_asset = co.asset;
        }
        ImGui::SeparatorText("Primitive");
        ImGui::DragFloat3("Size (m)", prim_edit, 0.05f, 0.05f, 200.0f, "%.2f");
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Width (x), thickness / depth (y), height (z) - its bottom middle stays put");
        if (ImGui::IsItemDeactivatedAfterEdit()) {
            sp["w"] = r2(prim_edit[0]); sp["d"] = r2(prim_edit[1]); sp["h"] = r2(prim_edit[2]);
            set_prim(co, sp);
            prim_edit_asset = co.asset;
        }
        ImGui::Text("Material: %s", sp.value("mn", std::string()).c_str());
        ImGui::SetNextItemWidth(-1);
        if (ImGui::BeginCombo("##primmat", "Another material (from the models read so far)...")) {
            for (size_t k = 0; k < asset_meta.size() && k < assets_keys_loaded.size(); k++) {
                const json& m = asset_meta[k];
                if (m.contains("primitive")) continue;
                for (auto& mt : m.value("materials", json::array())) {
                    if (!mt.value("textured", false)) continue;
                    std::string label = mt["name"].get<std::string>() + "   (" + m.value("name", std::string()) + ")##" +
                                        assets_keys_loaded[k] + mt["key"].get<std::string>();
                    if (ImGui::Selectable(label.c_str())) {
                        sp["m"] = assets_keys_loaded[k]; sp["mk"] = mt["key"]; sp["mn"] = mt["name"];
                        set_prim(co, sp);
                    }
                }
            }
            ImGui::EndCombo();
        }
        if (ImGui::Button("Use it for new walls / boxes / slabs")) {
            prim_m = sp["m"].get<std::string>(); prim_mk = sp["mk"].get<std::string>(); prim_mn = sp["mn"].get<std::string>();
        }
    }

    // --------------------------------------------------------------------------------------- projects --
    bool save_project(bool as) {
        if (!scene) return false;
        if (workspace) return save_workspace(false);
        std::string path = project_path;
        if (as || path.empty()) {
            path = file_dialog(hwnd, true, scene->title + ".ac3proj");
            if (path.empty()) return false;
        }
        json j = edits.request(carry_ground);
        j["studio_project"] = 1;
        j["title"] = scene->title;
        std::ofstream f(widen(path), std::ios::binary);
        f << j.dump(1);
        if (!f) { status = "Couldn't write " + path; return false; }
        project_path = path;
        edits.commit();
        edits.saved_at = edits.at;
        status = "Saved " + path;
        return true;
    }

    void open_project() {
        std::string path = file_dialog(hwnd, false, "");
        if (path.empty()) return;
        std::ifstream f(widen(path), std::ios::binary);
        std::stringstream ss;
        ss << f.rdbuf();
        json j = json::parse(ss.str(), nullptr, false);
        if (j.is_discarded() || !j.contains("archive")) { status = "Not an Reforged Studio project: " + path; return; }
        j["_path"] = path;
        carry_ground = j.value("carry_ground", true);
        if (scene && scene->archive == j["archive"].get<std::string>()) {
            edits.reset(scene.get());
            std::string p = edits.apply_project(j);
            project_path = path;
            status = p.empty() ? "Opened " + path : "Opened with problems: " + p;
            request_assets();
        } else {
            pending_project = j;
            open_map(j["archive"]);
        }
    }

    // --------------------------------------------------------------------------------------------- UI --
    void ui();
    void ui_menu();
    // the first-run setup, File > Settings... (setup.inc)
    struct SetupUI {
        bool open = false, ingesting = false;
        char game[512] = "", sp[512] = "", work[512] = "", blender[512] = "";
        std::string error, ingest_msg;
        // the other games' installs (read only: map imports), from their profiles (studio\games\<GAME>_MP.json)
        struct Source { std::string game, title, exe, subdir; std::vector<std::string> guesses; char dir[512] = ""; };
        std::vector<Source> sources;
        std::string target_title = "Assassin's Creed III multiplayer";
    } setup;
    void setup_profiles();
    std::string setup_mp_dir(const SetupUI::Source& s, const std::string& folder) const;
    bool is_release_tree() const;
    json read_config() const;
    bool setup_needed() const;
    void setup_open();
    void setup_save();
    void ingest();
    void ui_setup();
    void ui_maps();
    void ui_outliner();
    void ui_viewport();
    void ui_inspector();
    void ui_project();
    void ui_library();
    void ui_my_assets();
    void ui_blend_library();
    void ui_help();
    void ui_log(const char* title = "Log");
    // the Characters workspace (charui.inc)
    void chr_layout(ImGuiID id);
    void ui_chr_workspace();
    void ui_chr_personas();
    void ui_chr_tree();
    void ui_chr_view();
    void ui_chr_inspector();
    void ui_chr_look(const json& in);
    void ui_chr_part(const json& in);
    void ui_chr_colours(const json& in, const std::string& only_part = "");
    void ui_chr_material(const json& in);
    void ui_chr_weapons(const json& in);
    void ui_chr_changes();
    void ui_chr_builds();
    void ui_chr_modals();
    bool chr_row_combo(const json& in, const std::string& slot, json& want, float width);
    void chr_touch(const std::string& key);
    void chr_restore_snap(const CharUI::Snap& s);
    void chr_undo();
    void chr_redo();
    void chr_undo_all();
    std::string chr_state_key() const;
    bool chr_unbuilt() const;
    void chr_ask_load(int id);
    void chr_pick(int kind, const std::string& part = "", int mat = -1);
    void chr_mat_read(const std::string& part);
    int chr_pick_ray(float px, float py);
    void chr_pick_entity(int k);
    std::string chr_part_of(const std::string& mat0x);
    void chr_pick_material_part(const std::string& mat0x);
    void chr_test_in_game();
    void chr_tg_install();
    void chr_restore_release();
    std::string chr_auto_name() const;
    bool chr_shortcuts();
    void dock_layout(ImGuiID id);
    void shortcuts();
    void draw_viewport();
};

// ------------------------------------------------------------------------------------------------- style --

static void style() {
    ImGuiStyle& s = ImGui::GetStyle();
    ImGui::StyleColorsDark();
    s.WindowRounding = 3; s.FrameRounding = 3; s.TabRounding = 3; s.GrabRounding = 3; s.ScrollbarRounding = 3;
    s.WindowBorderSize = 1; s.FrameBorderSize = 0; s.WindowPadding = ImVec2(8, 8); s.ItemSpacing = ImVec2(7, 5);
    ImVec4* c = s.Colors;
    ImVec4 bg(0.105f, 0.107f, 0.115f, 1), panel(0.135f, 0.137f, 0.147f, 1), frame(0.18f, 0.182f, 0.195f, 1);
    ImVec4 accent(0.86f, 0.42f, 0.16f, 1), accent_dim(0.55f, 0.27f, 0.12f, 1), accent_hi(0.95f, 0.52f, 0.22f, 1);
    c[ImGuiCol_WindowBg] = panel; c[ImGuiCol_ChildBg] = panel; c[ImGuiCol_PopupBg] = ImVec4(0.12f, 0.12f, 0.13f, 0.98f);
    c[ImGuiCol_MenuBarBg] = bg; c[ImGuiCol_TitleBg] = bg; c[ImGuiCol_TitleBgActive] = bg;
    c[ImGuiCol_Border] = ImVec4(0.22f, 0.22f, 0.24f, 1);
    c[ImGuiCol_FrameBg] = frame; c[ImGuiCol_FrameBgHovered] = ImVec4(0.23f, 0.23f, 0.25f, 1);
    c[ImGuiCol_FrameBgActive] = ImVec4(0.26f, 0.26f, 0.28f, 1);
    c[ImGuiCol_Button] = frame; c[ImGuiCol_ButtonHovered] = accent_dim; c[ImGuiCol_ButtonActive] = accent;
    c[ImGuiCol_Header] = accent_dim; c[ImGuiCol_HeaderHovered] = ImVec4(0.42f, 0.22f, 0.11f, 1);
    c[ImGuiCol_HeaderActive] = accent;
    c[ImGuiCol_Tab] = bg; c[ImGuiCol_TabHovered] = accent_dim; c[ImGuiCol_TabSelected] = panel;
    c[ImGuiCol_TabSelectedOverline] = accent; c[ImGuiCol_TabDimmed] = bg; c[ImGuiCol_TabDimmedSelected] = panel;
    c[ImGuiCol_CheckMark] = accent_hi; c[ImGuiCol_SliderGrab] = accent; c[ImGuiCol_SliderGrabActive] = accent_hi;
    c[ImGuiCol_DockingPreview] = ImVec4(0.86f, 0.42f, 0.16f, 0.5f);
    c[ImGuiCol_SeparatorHovered] = accent_dim; c[ImGuiCol_SeparatorActive] = accent;
    c[ImGuiCol_ResizeGripHovered] = accent_dim; c[ImGuiCol_ResizeGripActive] = accent;
    c[ImGuiCol_TextSelectedBg] = ImVec4(0.86f, 0.42f, 0.16f, 0.35f);
    c[ImGuiCol_NavCursor] = accent;
}

// ----------------------------------------------------------------------------------------------- the UI --

void App::dock_layout(ImGuiID id) {
    ImGui::DockBuilderRemoveNode(id);
    ImGui::DockBuilderAddNode(id, ImGuiDockNodeFlags_DockSpace);
    ImGui::DockBuilderSetNodeSize(id, ImGui::GetMainViewport()->WorkSize);
    ImGuiID left, rest, right, centre, bottom;
    ImGui::DockBuilderSplitNode(id, ImGuiDir_Left, 0.17f, &left, &rest);
    ImGui::DockBuilderSplitNode(rest, ImGuiDir_Right, 0.24f, &right, &centre);
    ImGui::DockBuilderSplitNode(centre, ImGuiDir_Down, 0.26f, &bottom, &centre);
    ImGui::DockBuilderDockWindow("Maps", left);
    ImGui::DockBuilderDockWindow("Outliner", left);
    ImGui::DockBuilderDockWindow("Viewport", centre);
    ImGui::DockBuilderDockWindow("Inspector", right);
    ImGui::DockBuilderDockWindow("Map", right);
    ImGui::DockBuilderDockWindow("Project", bottom);
    ImGui::DockBuilderDockWindow("Library", bottom);
    ImGui::DockBuilderDockWindow("Log", bottom);
    ImGui::DockBuilderFinish(id);
}

void App::ui() {
    ImGuiViewport* vp = ImGui::GetMainViewport();
    live_tick();
    maps_watch();
    ui_menu();
    ui_setup();
    // status bar
    float bar = ImGui::GetFrameHeight();
    ImGui::SetNextWindowPos(ImVec2(vp->WorkPos.x, vp->WorkPos.y + vp->WorkSize.y - bar));
    ImGui::SetNextWindowSize(ImVec2(vp->WorkSize.x, bar));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(8, 3));
    ImGui::Begin("##status", nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoDocking |
                                          ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing);
    if (engine.busy()) ImGui::TextColored(ImVec4(0.95f, 0.6f, 0.25f, 1), "[engine: %s]", engine.busy_with().c_str());
    else ImGui::TextDisabled(engine.running() ? "[engine ready]" : "[engine stopped]");
    ImGui::SameLine();
    ImGui::TextUnformatted(status.c_str());
    ImGui::End();
    ImGui::PopStyleVar();

    ImGuiID dock = ImGui::GetID("MainDock"), cdock = ImGui::GetID("CharDock");
    static bool first = true;
    if (first) {
        first = false;
        if (!ImGui::DockBuilderGetNode(dock) || !ImGui::DockBuilderGetNode(dock)->IsSplitNode()) dock_layout(dock);
    }
    if (chr.open && !chr.layout_done) {               // (the Characters workspace's own layout, made the first time)
        chr.layout_done = true;
        if (!ImGui::DockBuilderGetNode(cdock) || !ImGui::DockBuilderGetNode(cdock)->IsSplitNode()) chr_layout(cdock);
    }
    ImGui::SetNextWindowPos(vp->WorkPos);
    ImGui::SetNextWindowSize(ImVec2(vp->WorkSize.x, vp->WorkSize.y - bar));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(0, 0));
    ImGui::Begin("##host", nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoDocking |
                                        ImGuiWindowFlags_NoBringToFrontOnFocus | ImGuiWindowFlags_NoSavedSettings |
                                        ImGuiWindowFlags_NoBackground);
    ImGui::PopStyleVar();
    // two workspaces (the menu bar's Maps | Characters), each its own dockspace; the hidden one is kept alive so
    // its windows stay docked
    ImGui::DockSpace(dock, ImVec2(0, 0), chr.open ? ImGuiDockNodeFlags_KeepAliveOnly : ImGuiDockNodeFlags_PassthruCentralNode);
    ImGui::DockSpace(cdock, ImVec2(0, 0), chr.open ? ImGuiDockNodeFlags_None : ImGuiDockNodeFlags_KeepAliveOnly);
    ImGui::End();

    if (chr.open) ui_chr_workspace();
    else {
        ui_maps();
        ui_outliner();
        ui_viewport();
    }
    ui_help();
    ui_bare();
    ui_spa();
    ui_imp();
    save_settings();
    tg_tick();
    if (!chr.open) {
        ui_inspector();
        ui_map_panel();
        ui_project();
        ui_library();
        ui_log();
    }

    if (ask_name) { ImGui::OpenPopup("Name the asset"); ask_name = false; }
    if (ImGui::BeginPopupModal("Name the asset", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        ImGui::TextUnformatted(ask_name_for == 0 ? "The selection becomes an asset of the library (Library > My assets):"
                               : ask_name_for == 2 ? "The building becomes a nested asset: its EntityGroup pieces its shell, each window,\n"
                                                     "door, bench... a child asset (one per model, shared):"
                                                   : "Save the asset being edited as a new asset:");
        ImGui::SetNextItemWidth(360);
        if (ImGui::IsWindowAppearing()) ImGui::SetKeyboardFocusHere();
        bool enter = ImGui::InputText("##assetname", name_buf, sizeof name_buf, ImGuiInputTextFlags_EnterReturnsTrue);
        ImGui::TextDisabled("Saved in %s", user_dir.c_str());
        ImGui::BeginDisabled(!name_buf[0]);
        if (ImGui::Button("Save") || (enter && name_buf[0])) {
            if (ask_name_for == 0) save_selection_asset(name_buf);
            else if (ask_name_for == 2) make_building_asset_async(name_buf);
            else save_workspace(true, name_buf);
            lib_tab = 1;
            ImGui::CloseCurrentPopup();
        }
        ImGui::EndDisabled();
        ImGui::SameLine();
        if (ImGui::Button("Cancel")) ImGui::CloseCurrentPopup();
        ImGui::EndPopup();
    }
    if (ask_close) { ImGui::OpenPopup("Close the asset editor?"); ask_close = false; }
    if (ImGui::BeginPopupModal("Close the asset editor?", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        ImGui::TextUnformatted("The asset has changes that aren't saved.");
        if (ImGui::Button("Save first")) { if (save_workspace(false)) close_workspace(); ImGui::CloseCurrentPopup(); }
        ImGui::SameLine();
        if (ImGui::Button("Discard them")) { close_workspace(); ImGui::CloseCurrentPopup(); }
        ImGui::SameLine();
        if (ImGui::Button("Cancel")) ImGui::CloseCurrentPopup();
        ImGui::EndPopup();
    }
    if (ask_install) { ImGui::OpenPopup("Install over the release's map?"); ask_install = false; }
    if (ImGui::BeginPopupModal("Install over the release's map?", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        ImGui::TextUnformatted("This build was made from a different file than the one the release installs:");
        ImGui::BulletText("built from: %s", scene ? scene->archive.c_str() : "");
        ImGui::BulletText("the release's: %s", release_file.c_str());
        ImGui::TextColored(ImVec4(1, 0.7f, 0.3f, 1), "Installing it drops whatever the release changed in this map\n"
                                                     "(AC3's own maps: the Artifact Assault Switched layers and more).\n"
                                                     "Open this map's \"release\" entry in Maps and build from that instead.");
        if (ImGui::Button("Install anyway")) { install_build(true); ImGui::CloseCurrentPopup(); }
        ImGui::SameLine();
        if (ImGui::Button("Cancel")) ImGui::CloseCurrentPopup();
        ImGui::EndPopup();
    }
    // unsaved changes before opening another map / quitting
    if (!ask_open.empty() || want_quit) ImGui::OpenPopup("Unsaved changes");
    if (ImGui::BeginPopupModal("Unsaved changes", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        bool map_dirty = scene && edits.dirty();
        if (map_dirty) ImGui::TextUnformatted("This map has changes that aren't saved in a project file.");
        if (want_quit && chr_unbuilt())
            ImGui::Text("%d change(s) to %d %s aren't in a build yet: quitting drops them.", chr_edit_count(), chr.loaded_id,
                        persona_name(chr.loaded_id).c_str());
        if (map_dirty && ImGui::Button("Save first")) {
            if (save_project(false)) {
                if (want_quit) quit_now = true; else open_map(ask_open);
                ask_open.clear(); want_quit = false;
                ImGui::CloseCurrentPopup();
            }
        }
        ImGui::SameLine();
        if (ImGui::Button("Discard them")) {
            if (want_quit) quit_now = true; else open_map(ask_open);
            ask_open.clear(); want_quit = false;
            ImGui::CloseCurrentPopup();
        }
        ImGui::SameLine();
        if (ImGui::Button("Cancel")) { ask_open.clear(); want_quit = false; ImGui::CloseCurrentPopup(); }
        ImGui::EndPopup();
    }
}

void App::ui_menu() {
    if (!ImGui::BeginMainMenuBar()) return;
    ImGui::TextColored(ImVec4(0.95f, 0.55f, 0.2f, 1), "AC3");
    ImGui::SameLine(0, 4);
    ImGui::TextUnformatted("STUDIO");
    ImGui::SameLine();
    ImGui::TextDisabled("%s", VERSION);
    ImGui::Separator();
    // the workspaces
    for (int k = 0; k < 2; k++) {
        const char* nm = k ? "Characters" : "Maps";
        bool on = k ? chr.open : !chr.open;
        if (on) ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(1, 0.78f, 0.55f, 1));
        if (ImGui::Selectable(nm, on, 0, ImVec2(ImGui::CalcTextSize(nm).x + 4, 0)) && !on) { if (k) chr_open(); else chr.open = false; }
        if (on) ImGui::PopStyleColor();
        if (ImGui::IsItemHovered()) ImGui::SetTooltip(k ? "The character editor: personas, their parts, colours, materials, weapons"
                                                        : "The map editor");
    }
    ImGui::Separator();
    if (chr.open) {
        if (ImGui::BeginMenu("Edit")) {
            if (ImGui::MenuItem("Undo", "Ctrl+Z", false, !chr.undo.empty())) chr_undo();
            if (ImGui::MenuItem("Redo", "Ctrl+Y", false, !chr.redo.empty())) chr_redo();
            if (ImGui::MenuItem("Undo every change", nullptr, false, chr_edit_count() > 0)) chr_undo_all();
            ImGui::Separator();
            if (ImGui::MenuItem("Pick the whole look", "Esc", false, chr.scene != nullptr)) chr_pick(CharUI::PICK_PERSONA);
            if (ImGui::MenuItem("The colour scheme", nullptr, false, chr.scene != nullptr)) chr_pick(CharUI::PICK_COLOURS);
            if (ImGui::MenuItem("The weapons", nullptr, false, chr.scene != nullptr)) chr_pick(CharUI::PICK_WEAPON);
            ImGui::EndMenu();
        }
        if (ImGui::BeginMenu("View")) {
            if (ImGui::MenuItem("Maps (the map editor)")) chr.open = false;
            ImGui::MenuItem("Alpha cut-outs (hair, lace)", nullptr, &alpha_clip);
            if (ImGui::MenuItem("Frame the character", "F")) chr.dist = chr.weapon_only ? 1.2f : 3.2f;
            ImGui::EndMenu();
        }
        if (ImGui::BeginMenu("Build")) {
            if (ImGui::MenuItem("Test in game", "F5", false, chr_edit_count() > 0 && !tg_state)) chr_test_in_game();
            if (ImGui::MenuItem("Stop testing", nullptr, false, tg_state != 0)) { tg_state = 0; status = "Stopped watching the game"; }
            ImGui::MenuItem("Put the original DataPC back when the game closes", nullptr, &tg_restore);
            ImGui::Separator();
            if (ImGui::MenuItem("Build these changes", "Ctrl+B", false, chr_edit_count() > 0 && !chr.building)) chr_build();
            if (ImGui::MenuItem("Put the original DataPC back")) chr_restore_release();
            ImGui::EndMenu();
        }
        if (ImGui::BeginMenu("Help")) {
            if (ImGui::MenuItem("Keyboard shortcuts", "F1")) show_help = true;
            ImGui::EndMenu();
        }
        if (chr.loaded_id >= 0) {
            ImGui::Separator();
            ImGui::TextDisabled("%d %s%s", chr.loaded_id, persona_name(chr.loaded_id).c_str(), chr_unbuilt() ? "  (changed)" : "");
        }
        ImGui::EndMainMenuBar();
        return;
    }
    if (ImGui::BeginMenu("File")) {
        if (ImGui::MenuItem("Open project...", "Ctrl+O")) open_project();
        if (is_release_tree() && ImGui::MenuItem("Settings (the game folders, workspace, Blender)...")) setup_open();
        if (ImGui::MenuItem("New map from a singleplayer area...")) spa_open();
        if (ImGui::MenuItem("Import a map from another game...")) imp_open();
        if (ImGui::MenuItem("Save project", "Ctrl+S", false, scene != nullptr)) save_project(false);
        if (ImGui::MenuItem("Save project as...", nullptr, false, scene != nullptr)) save_project(true);
        ImGui::Separator();
        if (ImGui::MenuItem("Quit")) PostMessageW(hwnd, WM_CLOSE, 0, 0);
        ImGui::EndMenu();
    }
    if (ImGui::BeginMenu("Edit")) {
        if (ImGui::MenuItem("Undo", "Ctrl+Z", false, scene && edits.can_undo())) edits.undo();
        if (ImGui::MenuItem("Redo", "Ctrl+Y", false, scene && edits.can_redo())) edits.redo();
        ImGui::Separator();
        bool sel = scene && !selection.empty();
        if (ImGui::MenuItem("Select Building", "B", false, sel)) select_building();
        if (ImGui::MenuItem("Copy (duplicate)", "Ctrl+D", false, sel)) copy_selected();
        if (ImGui::MenuItem("Remove from map", "Delete", false, sel)) remove_selected();
        if (ImGui::MenuItem("Put back", "Ctrl+R", false, sel)) put_back_selected();
        if (ImGui::MenuItem("Drop to the ground", "End", false, sel)) drop_to_ground();
        if (ImGui::MenuItem("Select Attached (a ring / the whole structure)", "G / Shift+G", false, sel)) select_attached(ImGui::GetIO().KeyShift);
        if (ImGui::MenuItem("Place from the library...", "L", false, scene != nullptr)) focus_library = true;
        if (ImGui::MenuItem("Add a wall", nullptr, false, scene != nullptr)) add_primitive("wall");
        if (ImGui::MenuItem("Add a box", nullptr, false, scene != nullptr)) add_primitive("box");
        if (ImGui::MenuItem("Add a floor slab", nullptr, false, scene != nullptr)) add_primitive("slab");
        if (ImGui::MenuItem("Raise / lower ground...", nullptr, false, scene && !workspace)) start_region();
        if (ImGui::MenuItem("Remake the navmesh in an area...", nullptr, false, scene && !workspace)) start_region(AREA_NAV_REMAKE);
        if (ImGui::MenuItem("Keep crowds out of an area...", nullptr, false, scene && !workspace)) start_region(AREA_NAV_BLOCK);
        if (ImGui::MenuItem("Clear the map (start from bare)...", nullptr, false, scene && !workspace)) bare_ask = true;
        if (ImGui::MenuItem("Clear an area...", nullptr, false, scene && !workspace)) { load_gameplay(); start_region(AREA_CLEAR); }
        if (ImGui::MenuItem("Save selection as an asset...", nullptr, false, sel)) { ask_name_for = 0; ask_name = true; name_buf[0] = 0; }
        if (ImGui::MenuItem("Make asset from building...", nullptr, false, sel && !workspace)) { ask_name_for = 2; ask_name = true; name_buf[0] = 0; }
        ImGui::Separator();
        if (ImGui::MenuItem("Select none", "Esc", false, sel)) selection.clear();
        ImGui::EndMenu();
    }
    if (ImGui::BeginMenu("View")) {
        if (ImGui::MenuItem("Characters (the workspace)")) chr_open();
        ImGui::Separator();
        ImGui::MenuItem("Collision (wireframe)", "C", &show_collision);
        ImGui::MenuItem("Markers (spawns, lights, sounds...)", nullptr, &show_markers);
        ImGui::MenuItem("Gameplay volumes (out of bounds, zones)", nullptr, &show_volumes);
        ImGui::MenuItem("Spawn labels", nullptr, &show_labels);
        ImGui::MenuItem("Removed objects (red)", nullptr, &show_removed);
        ImGui::MenuItem("Alpha cut-outs (leaves, fences)", nullptr, &alpha_clip);
        ImGui::Separator();
        if (ImGui::MenuItem("Whole map", "Home", false, scene != nullptr)) frame_all();
        if (ImGui::MenuItem("Frame selection", "F", false, !selection.empty())) frame_selection();
        if (ImGui::MenuItem("Look straight down", "T")) cam.pitch = -1.5607f;
        ImGui::SliderFloat("Field of view", &fov, 30, 100, "%.0f deg");
        ImGui::EndMenu();
    }
    if (ImGui::BeginMenu("Build")) {
        if (ImGui::MenuItem("Test in game", "F5", false, scene && !workspace && !engine.busy() && !tg_state)) test_in_game();
        if (ImGui::MenuItem("Stop testing", nullptr, false, tg_state != 0)) { tg_state = 0; status = "Stopped watching the game"; }
        ImGui::MenuItem("Put the original back when the game closes", nullptr, &tg_restore);
        ImGui::Separator();
        if (ImGui::MenuItem("Build test map", "Ctrl+B", false, scene && !engine.busy())) build();
        if (ImGui::MenuItem("Install into the game", nullptr, false, !last_dest.empty() && !engine.busy())) install_build();
        if (ImGui::MenuItem("Restore the original", nullptr, false, scene && !engine.busy())) restore_original();
        ImGui::Separator();
        ImGui::MenuItem("Buildings carry their ground", nullptr, &carry_ground);
        ImGui::EndMenu();
    }
    if (ImGui::BeginMenu("Help")) {
        if (ImGui::MenuItem("Keyboard shortcuts", "F1")) show_help = true;
        ImGui::Separator();
        ImGui::Text("Engine: %s", python.c_str());
        ImGui::Text("ac3-editor: %s", repo.c_str());
        ImGui::TextDisabled("Settings: %s", settings_path().c_str());
        ImGui::EndMenu();
    }
    if (scene) {
        ImGui::Separator();
        ImGui::TextDisabled("%s%s", scene->title.c_str(), edits.dirty() ? "  (changed)" : "");
    }
    ImGui::EndMainMenuBar();
}

static bool contains_ci(const std::string& hay, const std::string& needle) {
    if (needle.empty()) return true;
    auto it = std::search(hay.begin(), hay.end(), needle.begin(), needle.end(),
                          [](char a, char b) { return std::tolower((unsigned char)a) == std::tolower((unsigned char)b); });
    return it != hay.end();
}

static bool input_text(const char* label, std::string& s, const char* hint) {
    char buf[256];
    strncpy_s(buf, s.c_str(), sizeof buf - 1);
    ImGui::SetNextItemWidth(-1);
    bool r = ImGui::InputTextWithHint(label, hint, buf, sizeof buf);
    if (r) s = buf;
    return r;
}

void App::ui_maps() {
    ImGui::Begin("Maps");
    input_text("##mapfilter", map_filter, "Find map");
    ImGui::BeginChild("maplist");
    if (maps.empty()) ImGui::TextDisabled(engine.running() ? "Asking the engine for the maps..." : "No engine.");
    std::string last_kind;
    for (auto& m : maps) {
        if (!contains_ci(m.title + " " + m.source, map_filter)) continue;
        std::string section = m.kind == "AC3" ? "AC3 originals (untouched - a reference)"
                              : m.kind == "sparea" ? "From singleplayer areas"
                              : m.kind == "import" ? "Imported from other games" : "Release (what the game runs)";
        if (section != last_kind) {
            ImGui::SeparatorText(section.c_str());
            last_kind = section;
        }
        bool is_open = scene && scene->archive == m.path;
        bool loading = (load_state == 1 || load_state == 2) && open_archive == m.path;
        std::string label = m.title + (loading ? "  (opening...)" : "") + "##" + m.path;
        if (ImGui::Selectable(label.c_str(), is_open, ImGuiSelectableFlags_AllowDoubleClick)) {
            if (ImGui::IsMouseDoubleClicked(0) && !is_open) {
                if (scene && edits.dirty()) ask_open = m.path;
                else open_map(m.path);
            }
        }
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s\n%s\nDouble-click to open", m.source.c_str(), m.path.c_str());
        std::string tag = m.kind == "AC3" ? "original" : m.kind == "sparea" ? "SP area" : m.kind == "import" ? "imported"
                                                                                                         : m.source.substr(m.source.find('(') + 1);
        if (!tag.empty() && tag.back() == ')') tag.pop_back();
        ImGui::SameLine(std::max(120.0f, ImGui::GetContentRegionAvail().x - ImGui::CalcTextSize(tag.c_str()).x));
        ImGui::TextDisabled("%s", tag.c_str());
    }
    ImGui::EndChild();
    ImGui::End();
}

void App::ui_outliner() {
    ImGui::Begin("Outliner");
    if (!scene) { ImGui::TextDisabled("Open a map (Maps tab)."); ImGui::End(); return; }
    ImGui::SetNextItemWidth(130);
    const char* views[] = {"Every object", "Changed", "Selected", "Blend groups", "New objects"};
    ImGui::Combo("##outshow", &outliner_show, views, IM_ARRAYSIZE(views));
    if (ImGui::IsItemHovered()) ImGui::SetTooltip("Changed: moved, removed and new objects - what Build will write");
    ImGui::SameLine();
    input_text("##objfilter", outliner_filter, "Find object (name or uid)");
    ImGui::BeginChild("objects");
    auto row = [&](int id) {
        std::string name = label_of(id);
        if (edits.removed(id)) name += "  (removed)";
        else if (edits.moved(id)) name += "  (moved)";
        ImGui::PushID(id);
        if (ImGui::Selectable(name.c_str(), selected(id))) {
            bool add = ImGui::GetIO().KeyCtrl || ImGui::GetIO().KeyShift;
            select(id, add);
        }
        if (ImGui::IsItemHovered() && ImGui::IsMouseDoubleClicked(0)) { select(id, false); frame_selection(); }
        ImGui::PopID();
    };
    if (!outliner_filter.empty() || outliner_show) {
        std::vector<int> hits;
        for (int id = 0; id < edits.count(); id++) {
            const Entity& e = ent(id);
            bool kind = outliner_show == 0 || (outliner_show == 1 && (edits.removed(id) || edits.moved(id) || edits.is_copy(id))) ||
                        (outliner_show == 2 && selected(id)) || (outliner_show == 3 && blend_of(id)) ||
                        (outliner_show == 4 && edits.is_copy(id));
            if (!kind) continue;
            if (outliner_filter.empty() || contains_ci(label_of(id), outliner_filter) || uid_hex(e.uid) == outliner_filter) hits.push_back(id);
        }
        ImGui::TextDisabled("%d match(es)", (int)hits.size());
        ImGuiListClipper clip;
        clip.Begin((int)hits.size());
        while (clip.Step())
            for (int i = clip.DisplayStart; i < clip.DisplayEnd; i++) row(hits[i]);
    } else {
        // by record: the World, the cells, each game mode; the eye hides a record
        std::vector<std::vector<int>> by_rec(scene->records.size());
        for (int id = 0; id < (int)scene->entities.size(); id++) by_rec[scene->entities[id].record].push_back(id);
        if (!edits.cur.copies.empty() && ImGui::TreeNodeEx("New objects (copies, library models, placed assets)", ImGuiTreeNodeFlags_DefaultOpen)) {
            // placed assets as trees (a node per child asset: click selects it, the arrow opens it), the rest flat
            std::function<void(int, const std::string&, int)> node = [&](int g, const std::string& pre, int depth) {
                std::map<std::string, std::vector<int>> kids;           // next part path -> its objects
                for (int id : leaves_under(g, pre)) {
                    const std::string& p = edits.cur.copies[id - scene->entities.size()].path;
                    kids[prefix_of(p, depth + 1)].push_back(id);
                }
                for (auto& [cp, ids] : kids) {
                    if (ids.size() == 1 && edits.cur.copies[ids[0] - scene->entities.size()].path == cp) { row(ids[0]); continue; }
                    auto it = edits.groups[g].part_names.find(cp);
                    std::string label = (it != edits.groups[g].part_names.end() ? it->second : cp) + "  (" + std::to_string(ids.size()) + ")##" + cp;
                    bool open = ImGui::TreeNodeEx(label.c_str(), ImGuiTreeNodeFlags_OpenOnArrow);
                    if (ImGui::IsItemClicked() && !ImGui::IsItemToggledOpen()) { selection = ids; scope_group = g; scope_depth = depth + 1; }
                    if (open) { node(g, cp, depth + 1); ImGui::TreePop(); }
                }
            };
            for (size_t g = 0; g < edits.groups.size(); g++) {
                std::vector<int> all = leaves_under((int)g, "");
                if (all.empty()) continue;
                ImGui::PushID((int)g + 100000);
                bool open = ImGui::TreeNodeEx((edits.groups[g].name + "  (" + std::to_string(all.size()) + " parts)").c_str(),
                                              ImGuiTreeNodeFlags_OpenOnArrow);
                if (ImGui::IsItemClicked() && !ImGui::IsItemToggledOpen()) { selection = all; scope_group = (int)g; scope_depth = 0; }
                if (open) { node((int)g, "", 0); ImGui::TreePop(); }
                ImGui::PopID();
            }
            for (size_t k = 0; k < edits.cur.copies.size(); k++)
                if (edits.cur.copies[k].group < 0) row((int)(scene->entities.size() + k));
            ImGui::TreePop();
        }
        for (int pass = 0; pass < 3; pass++) {
            const char* head = pass == 0 ? "World and other records" : pass == 1 ? "Map cells" : "Game modes";
            if (!ImGui::TreeNodeEx(head, pass == 1 ? ImGuiTreeNodeFlags_DefaultOpen : 0)) continue;
            for (size_t r = 0; r < scene->records.size(); r++) {
                const Record& rc = scene->records[r];
                bool cell = rc.name.rfind("Cell", 0) == 0, mode = !rc.label.empty();
                if ((pass == 0 && (cell || mode)) || (pass == 1 && !cell) || (pass == 2 && !mode)) continue;
                ImGui::PushID((int)r);
                bool shown = !record_hidden[r];
                if (ImGui::Checkbox("##eye", &shown)) record_hidden[r] = !shown;
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("Show / hide this record's objects");
                ImGui::SameLine();
                std::string label = (mode ? rc.label : rc.name) + " (" + std::to_string(by_rec[r].size()) + ")";
                if (mode && (int)r == mode_record) label += "  - shown";
                if (ImGui::TreeNode(label.c_str())) {
                    ImGuiListClipper clip;
                    clip.Begin((int)by_rec[r].size());
                    while (clip.Step())
                        for (int i = clip.DisplayStart; i < clip.DisplayEnd; i++) row(by_rec[r][i]);
                    ImGui::TreePop();
                }
                ImGui::PopID();
            }
            ImGui::TreePop();
        }
    }
    ImGui::EndChild();
    ImGui::End();
}

void App::ui_inspector() {
    ImGui::Begin("Inspector");
    if (scene && drawing_region) {
        ImGui::TextColored(ImVec4(1, 0.85f, 0.3f, 1), "%s", draw_kind == AREA_GROUND ? "RAISE / LOWER GROUND"
                           : draw_kind == AREA_NAV_REMAKE ? "REMAKE THE NAVMESH IN AN AREA"
                           : draw_kind == AREA_CLEAR ? "CLEAR AN AREA (everything in it with a model goes, as Clear the map)"
                                                     : "KEEP CROWDS OUT OF AN AREA");
        ImGui::PushTextWrapPos(0);
        ImGui::TextUnformatted("Click the area's corners on the ground (any order: its outline is the convex shape round "
                               "them). Enter or double-click finishes, Backspace drops the last corner, Esc cancels.");
        ImGui::PopTextWrapPos();
        ImGui::Text("%d corner(s)", (int)draft.size());
        if (hull2d(draft).size() >= 3) ImGui::Text("%.0f m2", area2d(hull2d(draft)));
        ImGui::End();
        return;
    }
    if (scene && scattering) {
        ImGui::TextColored(ImVec4(1, 0.8f, 0.3f, 1), "SCATTER");
        ImGui::PushTextWrapPos(0);
        ImGui::TextUnformatted("Click or drag over the ground to paint copies of these models, each turned at random and "
                               "set down on the ground; Shift+drag takes them away. Painting over a spot again only fills it "
                               "up to the density. Ctrl+Z undoes a whole drag.");
        ImGui::PopTextWrapPos();
        ImGui::SeparatorText("Models");
        for (int k = 0; k < (int)scatter_pal.size(); k++) {
            ImGui::PushID(k);
            if (ImGui::SmallButton("x") && scatter_pal.size() > 1) { scatter_pal.erase(scatter_pal.begin() + k); ImGui::PopID(); break; }
            ImGui::SameLine();
            ImGui::TextUnformatted(scatter_pal[k].name.c_str());
            ImGui::PopID();
        }
        if (!lib_sel_key.empty() && ImGui::Button("Add the Library's model")) {
            int i = lib_find(lib_sel_key);
            if (i >= 0 && !is_special(lib[i].key)) {
                ScatterSrc q;
                q.lib = i;
                q.lo = V3(lib[i].lo[0], lib[i].lo[1], lib[i].lo[2]);
                q.hi = q.lo + V3(lib[i].size[0], lib[i].size[1], lib[i].size[2]);
                q.name = lib[i].name + " (" + lib[i].map + ")";
                scatter_pal.push_back(q);
            }
        }
        ImGui::SeparatorText("Brush");
        ImGui::SliderFloat("Radius (m)", &scatter_r, 1.0f, 30.0f, "%.1f");
        ImGui::SliderFloat("Density (per 100 m2)", &scatter_density, 0.2f, 40.0f, "%.1f", ImGuiSliderFlags_Logarithmic);
        ImGui::SliderFloat("Gap (m)", &scatter_gap, 0.0f, 4.0f, "%.2f");
        ImGui::SliderFloat("Steepest ground (deg)", &scatter_slope, 0.0f, 45.0f, "%.0f");
        ImGui::Checkbox("Turn each at random", &scatter_turn);
        ImGui::Checkbox("Only on the ground (not roofs, crates)", &scatter_ground);
        ImGui::Checkbox("Keep clear of other objects", &scatter_clear);
        int n = 0;
        for (int id = (int)scene->entities.size(); id < edits.count(); id++) n += scatter_mine(id);
        ImGui::Text("%d on the map now, %d painted this time", n, scatter_made);
        if (ImGui::Button("Done (Esc)")) start_scatter();
        ImGui::End();
        return;
    }
    if (scene && sculpting) {
        ImGui::TextColored(ImVec4(0.5f, 1, 0.6f, 1), "SCULPT THE GROUND");
        ImGui::PushTextWrapPos(0);
        ImGui::TextUnformatted("Click the ground to raise it, Shift+click to lower it; hold and drag to paint (each stroke a "
                               "third of the height). Strokes add up into smooth hills and hollows, falling to nothing at "
                               "their edge so the ground meets the ground round it.");
        ImGui::PopTextWrapPos();
        ImGui::SliderFloat("Radius (m)", &brush_r, 1.0f, 30.0f, "%.1f");
        ImGui::SliderFloat("Height (m)", &brush_h, 0.1f, 8.0f, "%.2f");
        float lo = 1e30f, hi = -1e30f;
        for (auto& st : edits.cur.sculpts) { lo = std::min(lo, st.h); hi = std::max(hi, st.h); }
        ImGui::Text("%d stroke(s)", (int)edits.cur.sculpts.size());
        if (!edits.cur.sculpts.empty()) {
            float peak = 0, pit = 0;
            for (auto& st : edits.cur.sculpts) { float d = sculpt_dz(st.x, st.y); peak = std::max(peak, d); pit = std::min(pit, d); }
            ImGui::TextDisabled("highest %+.2f m, lowest %+.2f m", peak, pit);
            if (ImGui::Button("Take the last one back (Backspace)")) { edits.cur.sculpts.pop_back(); edits.commit(); }
            ImGui::SameLine();
            if (ImGui::Button("Clear all")) { edits.cur.sculpts.clear(); edits.commit(); }
        }
        if (ImGui::Button("Done (Esc)")) start_sculpt();
        ImGui::Separator();
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("Build: the ground under the strokes is cut out, split into 1 m triangles and bent (its collision "
                            "too, textures and two-layer blend kept); objects and spawn points standing on it move with it; "
                            "the crowds' navmesh is made again there. Blend groups on curved ground get a warning per slot.");
        ImGui::PopTextWrapPos();
        ImGui::End();
        return;
    }
    if (selection.empty() && route_ok(sel_route)) {
        const NavRoute& rt = navv.routes[sel_route];
        ImGui::TextColored(ImVec4(1, 0.8f, 0.3f, 1), "CROWD ROUTE");
        ImGui::Text("%s", rt.name.c_str());
        ImGui::TextDisabled("uid %s - %d points, joins %d before / %d after", rt.uid.c_str(), (int)rt.pts.size(), rt.head, rt.tail);
        float len = 0;
        for (size_t i = 0; i + 1 < rt.pts.size(); i++) len += length(rt.pts[i + 1] - rt.pts[i]);
        ImGui::Text("%.0f m long", len);
        bool on = route_active(sel_route);
        if (ImGui::Checkbox("Crowds walk it (on)", &on)) { set_route(sel_route, on ? 1 : 0, -1); edits.commit(); }
        float w = route_width(sel_route);
        if (ImGui::DragFloat("Width (m)", &w, 0.05f, 0.5f, 12.0f, "%.2f")) set_route(sel_route, -1, w);
        if (ImGui::IsItemDeactivatedAfterEdit()) edits.commit();
        if (edits.cur.routes.count(rt.uid) && ImGui::Button("Put back (as the map has it)")) {
            edits.cur.routes.erase(rt.uid);
            edits.commit();
        }
        if (ImGui::Button("Frame")) {
            V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
            for (auto& p : rt.pts) { lo = vmin(lo, p); hi = vmax(hi, p + V3(0, 0, 2)); }
            frame_box(lo, hi);
        }
        ImGui::Separator();
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("Ambient crowds walk along their routes; wider lets them spread across more of the street. "
                            "Build writes the change (navmodel) and reads it back.");
        ImGui::PopTextWrapPos();
        ImGui::End();
        return;
    }
    if (selection.empty() && region_ok(sel_region) && edits.cur.regions[sel_region].kind != AREA_GROUND) {
        RegionEdit& r = edits.cur.regions[sel_region];
        std::vector<V3> h = hull2d(r.pts);
        ImGui::TextColored(ImVec4(0.5f, 0.7f, 1, 1), "NAVMESH AREA");
        char nb[64];
        strncpy_s(nb, r.name.c_str(), sizeof nb - 1);
        if (ImGui::InputText("Name", nb, sizeof nb)) r.name = nb;
        if (ImGui::IsItemDeactivatedAfterEdit()) edits.commit();
        ImGui::Text("%d corners, %.0f m2", (int)h.size(), area2d(h));
        if (ImGui::RadioButton("Remake the navmesh here", r.kind == AREA_NAV_REMAKE)) { r.kind = AREA_NAV_REMAKE; edits.commit(); }
        if (ImGui::RadioButton("Keep crowds out", r.kind == AREA_NAV_BLOCK)) { r.kind = AREA_NAV_BLOCK; edits.commit(); }
        if (r.kind == AREA_NAV_REMAKE && ImGui::Checkbox("New crowd routes along its open streets", &r.new_routes)) edits.commit();
        if (ImGui::Button("Frame")) {
            V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
            for (auto& p : h) { lo = vmin(lo, p); hi = vmax(hi, p + V3(0, 0, 3)); }
            frame_box(lo, hi);
        }
        ImGui::SameLine();
        if (ImGui::Button("Remove this area")) { r.removed = true; edits.commit(); sel_region = -1; ImGui::End(); return; }
        ImGui::Separator();
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled(r.kind == AREA_NAV_REMAKE
            ? "Build makes the crowds' navmesh here again from the collision (navgen): crowds walk on and round what stands "
              "there now - use it after moving or adding things. Routes are re-tied; with new routes, new ones are laid "
              "along its open streets and walked end to end before they're kept."
            : "Build keeps the crowds out of this area (navcut): the navmesh under it, from the ground to 3 m up, is cut "
              "off, and crowd routes through it are switched off.");
        ImGui::PopTextWrapPos();
        ImGui::End();
        return;
    }
    if (selection.empty() && region_ok(sel_region)) {
        RegionEdit& r = edits.cur.regions[sel_region];
        std::vector<V3> h = hull2d(r.pts);
        ImGui::TextColored(ImVec4(0.45f, 1, 0.65f, 1), "GROUND AREA");
        char nb[64];
        strncpy_s(nb, r.name.c_str(), sizeof nb - 1);
        if (ImGui::InputText("Name", nb, sizeof nb)) r.name = nb;
        if (ImGui::IsItemDeactivatedAfterEdit()) edits.commit();
        ImGui::Text("%d corners, %.0f m2", (int)h.size(), area2d(h));
        ImGui::DragFloat("Height change (m)", &r.dz, 0.05f, -20.0f, 20.0f, "%+.2f");
        if (ImGui::IsItemDeactivatedAfterEdit()) edits.commit();
        for (float step : {-1.0f, -0.25f, 0.25f, 1.0f}) {
            char lb[16];
            snprintf(lb, sizeof lb, "%+.2f", step);
            if (ImGui::Button(lb)) { r.dz = std::clamp(r.dz + step, -20.0f, 20.0f); edits.commit(); }
            ImGui::SameLine();
        }
        ImGui::NewLine();
        if (ImGui::Button("Frame")) {
            V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
            for (auto& p : h) { lo = vmin(lo, p); hi = vmax(hi, p + V3(0, 0, std::max(0.0f, r.dz))); }
            frame_box(lo, hi);
        }
        ImGui::SameLine();
        if (ImGui::Button("Remove this area")) { r.removed = true; edits.commit(); sel_region = -1; ImGui::End(); return; }
        ImGui::Separator();
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("Build: the ground inside the outline is cut and put back %s; everything standing wholly "
                            "inside goes with it (props, carts, small buildings, spawn points); walls close the edge "
                            "where it meets the ground around; the crowds' navmesh is made again under it. Objects "
                            "across the edge stay (the build lists them).", r.dz >= 0 ? "higher" : "lower");
        ImGui::PopTextWrapPos();
        ImGui::End();
        return;
    }
    if (!scene || selection.empty()) {
        ImGui::TextDisabled(scene ? "Click an object in the viewport or the Outliner." : "No map open.");
        ImGui::End();
        return;
    }
    if (selection.size() > 1) {
        ImGui::Text("%d objects selected", (int)selection.size());
        ms_inspector_note(selection[0]);
        if (!scope_label().empty()) {
            ImGui::PushTextWrapPos(0);
            ImGui::TextColored(ImVec4(0.6f, 0.8f, 1, 1), "%s", scope_label().c_str());
            ImGui::PopTextWrapPos();
            ImGui::TextDisabled("Double-click a part in the viewport to go inside, Esc to come back out");
            int g = scope_group;
            if (!workspace && g >= 0 && !edits.groups[g].asset.empty() &&
                ImGui::Button("Apply this placement's changes to the asset")) { std::vector<std::string> pr; apply_to_asset(g, pr); }
        }
        ui_climb(selection);
        int moved = 0, rem = 0, copies = 0;
        for (int id : selection) { moved += edits.moved(id); rem += edits.removed(id); copies += edits.is_copy(id); }
        ImGui::TextDisabled("%d moved, %d removed, %d copies", moved, rem, copies);
        ImGui::Separator();
        ImGui::BeginChild("sel");
        for (int id : selection) {
            ImGui::PushID(id);
            if (ImGui::Selectable(label_of(id).c_str())) selection = {id};
            ImGui::PopID();
        }
        ImGui::EndChild();
        ImGui::End();
        return;
    }
    int id = selection[0];
    const Entity& e = ent(id);
    const Scene* es = scene_of(id);
    bool placed = edits.is_placed(id);
    ImGui::PushTextWrapPos(0);
    ImGui::TextColored(ImVec4(0.95f, 0.62f, 0.3f, 1), "%s", label_of(id).c_str());
    ImGui::PopTextWrapPos();
    ms_inspector_note(id);
    const BlendInfo* blend = blend_of(id);
    bool blend_key_placed = placed && is_blend_key(edits.assets[edits.asset_of(id)].key);
    if (blend || blend_key_placed) {
        // a blend group - the map's, a copy of one, or a stock map's placed here
        ImGui::TextColored(ImVec4(0.95f, 0.45f, 0.95f, 1), "BLEND GROUP%s", blend ? (": " + blend->label).c_str() : "");
        ImGui::PushTextWrapPos(0);
        if (blend) {
            ImGui::Text("%d NPC slot(s)%s%s", (int)blend->slots.size(), blend->props.empty() ? "" : " - they hold: ",
                        blend->props.c_str());
            if (!blend->map.empty() && placed) ImGui::Text("From %s (%s)", blend->map.c_str(), blend->name.c_str());
        } else {
            ImGui::TextColored(ImVec4(1, 0.7f, 0.4f, 1), "Reading it from its map...");
        }
        ImGui::TextDisabled("Its NPCs stand on the pink rings (facing the ticks) - they move and turn with it. Move / turn "
                            "it with the gizmo, Ctrl+D copies it, Delete takes it out (the build deletes it from its cell). "
                            "Build checks each slot stands on the ground with room for an NPC.");
        ImGui::PopTextWrapPos();
        if (placed) {
            CopyObj& co = edits.cur.copies[id - scene->entities.size()];
            if (co.group >= 0) ImGui::TextColored(ImVec4(0.6f, 0.8f, 1, 1), "Part of: %s  [%s]", edits.groups[co.group].name.c_str(), co.path.c_str());
        }
    } else if (placed) {
        CopyObj& co = edits.cur.copies[id - scene->entities.size()];
        const AssetRef& a = edits.assets[co.asset];
        ImGui::Text("From the library: %s", a.map.c_str());
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("%s", a.key.c_str());
        int k = asset_slot(id);
        if (k >= 0 && k < (int)asset_meta.size()) {
            const json& mt = asset_meta[k];
            ImGui::TextColored(ImVec4(0.5f, 0.9f, 0.5f, 1), "Build brings it in: %d triangles; collision: %s (%d triangles); "
                               "climbing as chosen below (its own map's: %d climb edge(s)); each texture a new material on "
                               "this map's base material.",
                               mt.value("nt", 0), mt.value("collision", std::string()) == "own" ? "its map's own" : "its triangles",
                               mt.value("collision_tris", 0), mt.value("native_edges", 0));
        } else {
            ImGui::TextColored(ImVec4(1, 0.7f, 0.4f, 1), "Reading the model from its map...");
        }
        ImGui::PopTextWrapPos();
        bool solid = co.solid;
        if (ImGui::Checkbox("Solid (collision and climb edges)", &solid)) { co.solid = solid; edits.commit(); }
        ui_climb({id});
        if (co.group >= 0) {
            ImGui::TextColored(ImVec4(0.6f, 0.8f, 1, 1), "Part of: %s  [%s]", edits.groups[co.group].name.c_str(), co.path.c_str());
            if (!workspace && !edits.groups[co.group].asset.empty()) {
                if (ImGui::Button("Apply this placement's changes to the asset")) { std::vector<std::string> pr; apply_to_asset(co.group, pr); }
                if (ImGui::IsItemHovered())
                    ImGui::SetTooltip("Moves, removals and additions made to this placed asset become the asset's own:\n"
                                      "later placements get them (this one stays as it is). A child asset's own inside\n"
                                      "is changed by opening that child asset in the asset editor.");
            }
        }
        if (is_prim_key(a.key)) ui_prim(co);
    }
    if (!placed) {
        ImGui::TextDisabled("uid %s", uid_hex(e.uid).c_str());
        const Record& rc = scene->records[e.record];
        ImGui::Text("Record: %s", rc.name.c_str());
        if (!rc.label.empty()) ImGui::Text("Game mode: %s", rc.label.c_str());
        if (!e.group.empty()) ImGui::Text("Group: %s", e.group.c_str());
        if (e.layer >= 0) ImGui::Text("Data-layer filter: %d", e.layer);
        if (!e.note.empty()) { ImGui::PushTextWrapPos(0); ImGui::TextColored(ImVec4(0.5f, 0.9f, 0.5f, 1), "%s", e.note.c_str()); ImGui::PopTextWrapPos(); }
        if (edits.movable(id) && e.mesh >= 0 && !blend) ui_climb({id});
    }
    // its climbing
    if (!blend && (e.mesh >= 0 || placed)) {
        ImGui::SeparatorText("Climbing");
        if (!placed && !climbv.loaded) {
            if (climbv.loading) ImGui::TextDisabled("Reading the map's climb data...");
            else if (ImGui::SmallButton("Show climbing")) { show_climb = true; load_climb(); }
        } else {
            ClimbSummary c = climb_summary(id);
            ImGui::PushTextWrapPos(0);
            if (c.edges > 0)
                ImGui::TextColored(ImVec4(1, 0.75f, 0.25f, 1), "Climbable: %d climb edge(s)%s", c.edges,
                                   c.poles ? (" (" + std::to_string(c.poles) + " poles / beams)").c_str() : "");
            else if (c.collision)
                ImGui::TextColored(ImVec4(0.8f, 0.8f, 0.8f, 1), "No climb edges: you can stand on it, nothing to grab");
            else
                ImGui::TextDisabled("No collision: you pass through it");
            if (c.collision) {
                ImGui::Text("Collision %.0f m2: %.0f m2 climbable, %.0f m2 no-climb", c.area, c.area - c.noclimb, c.noclimb);
                if (c.noclimb > 0.5f * c.area)
                    ImGui::TextColored(ImVec4(1, 0.45f, 0.4f, 1), "Mostly no-climb (FuzzyZone): no climbing or wall-running on those faces");
            }
            if (placed) {
                const CopyObj& co = edits.cur.copies[id - scene->entities.size()];
                int cm = co.climb >= 0 ? co.climb : default_climb(edits.assets[co.asset].key);
                if (cm == CLIMB_GENERATED || cm == CLIMB_OUTSIDE)
                    ImGui::TextDisabled("Generated ledges / handhold rows are made by Build - not shown here.");
            }
            if (!show_climb && ImGui::SmallButton("Show the edges in the viewport")) { show_climb = true; load_climb(); }
            ImGui::PopTextWrapPos();
        }
    }
    int bk = edits.is_copy(id) ? -1 : edits.cur.building[id];
    if (bk >= 0) ImGui::TextColored(ImVec4(0.6f, 0.8f, 1, 1), "Building: %s", edits.building_keys[bk].c_str());
    std::string state = edits.removed(id) ? "removed" : placed ? "a new library model" : edits.is_copy(id) ? "a new copy"
                        : edits.moved(id) ? "moved" : "as the map has it";
    ImGui::Text("State: %s", state.c_str());
    if (!edits.movable(id))
        ImGui::TextColored(ImVec4(1, 0.7f, 0.4f, 1), (e.flags & F_SPAWN) ? "Spawn points follow the buildings they stand in;\n"
                                                                         "moving them by hand comes in a later stage."
                                                                       : "Its entity doesn't decode: it can't be edited.");

    ImGui::SeparatorText("Placement");
    M4& m = edits.matrix(id);
    V3 p = m.pos();
    float pos[3] = {p.x, p.y, p.z};
    ImGui::BeginDisabled(!edits.movable(id) || edits.removed(id));
    if (ImGui::DragFloat3("Position (m)", pos, 0.05f, 0, 0, "%.2f")) m.set_pos(V3(pos[0], pos[1], pos[2]));
    if (ImGui::IsItemDeactivatedAfterEdit()) edits.commit();
    float yaw = std::atan2(m.m[1], m.m[0]) * 57.29578f;
    float y0 = yaw;
    if (ImGui::DragFloat("Turn (deg)", &yaw, 0.5f, -360, 360, "%.1f")) {
        M4 r = rot_z((yaw - y0) / 57.29578f);
        V3 keep = m.pos();
        m.set_pos(V3());
        m = m * r;
        m.set_pos(keep);
    }
    if (ImGui::IsItemDeactivatedAfterEdit()) edits.commit();
    ImGui::EndDisabled();
    V3 lo, hi;
    world_box(id, lo, hi);
    ImGui::TextDisabled("Size %.2f x %.2f x %.2f m", hi.x - lo.x, hi.y - lo.y, hi.z - lo.z);
    if (ImGui::Button("Frame (F)")) frame_selection();
    ImGui::SameLine();
    ImGui::BeginDisabled(!edits.movable(id));
    if (ImGui::Button("Select Building (B)")) select_building();
    ImGui::SameLine();
    if (ImGui::Button("Put back")) put_back_selected();
    ImGui::EndDisabled();

    if (ImGui::CollapsingHeader("Model", ImGuiTreeNodeFlags_DefaultOpen)) {
        if (const Mesh* mp = mesh_of(id)) {
            const Mesh& me = *mp;
            ImGui::PushTextWrapPos(0);
            ImGui::Text("%s", me.name.c_str());
            ImGui::PopTextWrapPos();
            ImGui::TextDisabled("uid %s - %u vertices, %u triangles", uid_hex(me.uid).c_str(), me.nv, me.nt);
            for (const Sub& s : me.subs) {
                if (s.material < 0) { ImGui::BulletText("(material outside this archive) - %u tris", s.count); continue; }
                const Material& mt = es->materials[s.material];
                ImGui::BulletText("%s - %u tris", mt.name.c_str(), s.count);
                if (mt.texture >= 0 && ImGui::IsItemHovered()) ImGui::SetTooltip("%s", es->textures[mt.texture].c_str());
            }
        } else {
            ImGui::TextDisabled("No model in this archive (a marker).");
        }
    }
    if (ImGui::CollapsingHeader("Collision")) {
        if (e.shapes.empty()) ImGui::TextDisabled("None.");
        for (int s : e.shapes) {
            const Shape& sh = es->shapes[s];
            ImGui::BulletText("%s - %u tris, layer %u%s", sh.name.c_str(), sh.nt, sh.layer, sh.layer == 1 ? " (solid)" : "");
        }
    }
    if (ImGui::CollapsingHeader("Components")) {
        std::string c = e.components;
        size_t a = 0;
        while (a < c.size()) {
            size_t b = c.find(", ", a);
            ImGui::BulletText("%s", c.substr(a, b == std::string::npos ? std::string::npos : b - a).c_str());
            if (b == std::string::npos) break;
            a = b + 2;
        }
    }
    ImGui::End();
}

void App::ui_project() {
    ImGui::Begin("Project");
    if (!scene) { ImGui::TextDisabled("No map open."); ImGui::End(); return; }
    if (ImGui::BeginTabBar("proj")) {
        if (ImGui::BeginTabItem("Changes")) {
            ImGui::Text("%d moved, %d removed, %d copies, %d library models", edits.n_moved(), edits.n_removed(),
                        edits.n_copies(), edits.n_placed());
            ImGui::SameLine();
            ImGui::TextDisabled(project_path.empty() ? "(no project file - File > Save project)" : "%s", project_path.c_str());
            ImGui::BeginChild("changes");
            ms_changes_rows();
            for (size_t k = 0; k < edits.cur.regions.size(); k++) {
                const RegionEdit& r = edits.cur.regions[k];
                if (r.removed) continue;
                char lb[160];
                if (r.kind == AREA_GROUND)
                    snprintf(lb, sizeof lb, "ground  %s  %+.2f m  (%.0f m2)##rg%d", r.name.c_str(), r.dz, area2d(hull2d(r.pts)), (int)k);
                else
                    snprintf(lb, sizeof lb, "%s  %s  (%.0f m2)##rg%d", area_word(r.kind), r.name.c_str(), area2d(hull2d(r.pts)), (int)k);
                if (ImGui::Selectable(lb, sel_region == (int)k && selection.empty())) { selection.clear(); sel_region = (int)k; sel_route = -1; }
            }
            for (auto& [uid, e] : edits.cur.routes) {
                int k = -1;
                for (size_t i = 0; i < navv.routes.size(); i++)
                    if (navv.routes[i].uid == uid) k = (int)i;
                std::string what = std::string("crowd route  ") + (k >= 0 ? navv.routes[k].name : uid) +
                                   (e.active >= 0 ? (e.active ? "  on" : "  off") : "") +
                                   (e.width >= 0 ? "  width " + std::to_string(e.width).substr(0, 4) + " m" : "");
                if (ImGui::Selectable((what + "##rt" + uid).c_str(), k >= 0 && k == sel_route && selection.empty()) && k >= 0) {
                    selection.clear();
                    sel_region = -1;
                    sel_route = k;
                }
            }
            for (int id = 0; id < edits.count(); id++) {
                bool c = edits.is_copy(id);
                if (c ? edits.removed(id) : !(edits.removed(id) || edits.moved(id))) continue;
                std::string what = edits.is_placed(id) ? "library" : c ? "copy" : edits.removed(id) ? "removed" : "moved";
                int bk = c ? -1 : edits.cur.building[id];
                std::string name = edits.is_placed(id) ? label_of(id).substr(10) : ent(id).name;
                std::string label = what + "  " + name + (bk >= 0 ? "   [" + edits.building_keys[bk] + "]" : "");
                ImGui::PushID(id);
                if (ImGui::Selectable(label.c_str(), selected(id))) select(id, ImGui::GetIO().KeyCtrl || ImGui::GetIO().KeyShift);
                if (ImGui::IsItemHovered() && ImGui::IsMouseDoubleClicked(0)) { select(id, false); frame_selection(); }
                ImGui::PopID();
            }
            ImGui::EndChild();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("Build", nullptr, focus_build ? ImGuiTabItemFlags_SetSelected : 0)) {
            focus_build = false;
            ImGui::BeginDisabled(engine.busy());
            if (ImGui::Button("Build test map  (Ctrl+B)")) build();
            ImGui::SameLine();
            ImGui::Checkbox("Buildings carry their ground", &carry_ground);
            ImGui::EndDisabled();
            if (!holes_vis.empty() || !holes_col.empty() || !plug_tris.empty()) {
                ImGui::SameLine();
                ImGui::Checkbox("Show holes", &show_holes);
                if (ImGui::IsItemHovered())
                    ImGui::SetTooltip("Red posts: fall through, blue posts: see through (the water shows) - where the last build\n"
                                      "lost a floor or ground level a moved building had. Patch them, or move it differently.\n"
                                      "Green tiles: holes removed / moved objects left, plugged by the build with a slab in the\n"
                                      "ground's material; light blue: in the collision only (the ground still shows); yellow: drawn only\n"
                                      "(you saw through, but the ground's collision ran on under it).");
            }
            ImGui::TextDisabled("Builds from the map's original archive into ac3-editor\\out\\edits\\ - the game isn't touched.");
            if (last_build.contains("error")) {
                ImGui::TextColored(ImVec4(1, 0.4f, 0.35f, 1), "Failed: %s", last_build["error"].get<std::string>().c_str());
            } else if (!last_build.is_null()) {
                ImGui::TextColored(build_ok ? ImVec4(0.45f, 0.9f, 0.45f, 1) : ImVec4(1, 0.5f, 0.3f, 1),
                                   build_ok ? "Built and read back: every edit is in the archive." : "Built, but the read-back check failed:");
                ImGui::TextDisabled("%s", last_dest.c_str());
                for (auto& l : last_build.value("report", json::array())) ImGui::BulletText("%s", l.get<std::string>().c_str());
                for (auto& l : last_build.value("checks", json::array())) ImGui::BulletText("%s", l.get<std::string>().c_str());
                for (auto& l : last_build.value("warnings", json::array()))
                    ImGui::TextColored(ImVec4(1, 0.75f, 0.3f, 1), "WARNING: %s", l.get<std::string>().c_str());
            }
            ImGui::SeparatorText("The game");
            ImGui::TextUnformatted(live_state.c_str());
            if (!from_release)
                ImGui::TextColored(ImVec4(1, 0.7f, 0.3f, 1), "This map isn't the release's file (an untouched AC3 original?) -\n"
                                                             "installing a build of it drops the release's changes to it.");
            ImGui::BeginDisabled(last_dest.empty() || engine.busy());
            if (ImGui::Button("Install into the game")) install_build();
            ImGui::EndDisabled();
            ImGui::SameLine();
            ImGui::BeginDisabled(engine.busy());
            if (ImGui::Button("Restore the original")) restore_original();
            ImGui::EndDisabled();
            ImGui::TextDisabled("Install copies the last build over the game's map; Restore puts the release's file back.");
            ImGui::EndTabItem();
        }
        ImGui::EndTabBar();
    }
    ImGui::End();
}

void App::ui_library() {
    if (focus_library) {
        ImGui::SetWindowFocus("Library");
        focus_library = false;
    }
    if (ImGuiWindow* pw = ImGui::FindWindowByName("Project"))          // (layouts saved without the Library: dock it by Project)
        if (pw->DockId) ImGui::SetNextWindowDockID(pw->DockId, ImGuiCond_FirstUseEver);
    if (!ImGui::Begin("Library")) { ImGui::End(); return; }
    lib_poll(!lib_asked);
    if (lib_busy || !lib_asked) lib_poll();
    if (ImGui::BeginTabBar("libtabs")) {
        if (ImGui::BeginTabItem("Models", nullptr, 0)) { lib_tab = 0; ImGui::EndTabItem(); }
        if (ImGui::BeginTabItem("My assets", nullptr, lib_tab == 1 ? ImGuiTabItemFlags_SetSelected : 0)) { lib_tab = 1; ImGui::EndTabItem(); }
        if (ImGui::BeginTabItem("Blend groups", nullptr, lib_tab == 2 ? ImGuiTabItemFlags_SetSelected : 0)) { lib_tab = 2; ImGui::EndTabItem(); }
        if (ImGui::BeginTabItem("Gameplay", nullptr, lib_tab == 3 ? ImGuiTabItemFlags_SetSelected : 0)) { lib_tab = 3; ImGui::EndTabItem(); }
        ImGui::EndTabBar();
    }
    if (lib_tab == 1) { ui_my_assets(); ImGui::End(); return; }
    if (lib_tab == 2) { ui_blend_library(); ImGui::End(); return; }
    if (lib_tab == 3) { ui_gameplay_library(); ImGui::End(); return; }
    // the index state
    int ready = 0, failed = 0;
    std::string indexing;
    int wanted = 0;
    for (auto& m : lib_maps) {
        if (!m.value("wanted", true)) continue;
        wanted++;
        ready += m["index"].is_string();
        failed += m["error"].is_string();
        if (m.value("indexing", false)) indexing = m.value("title", std::string());
    }
    // filters
    ImGui::SetNextItemWidth(260);
    char buf[256];
    strncpy_s(buf, lib_filter.c_str(), sizeof buf - 1);
    if (ImGui::InputTextWithHint("##libfind", "Find model (name or material)", buf, sizeof buf)) { lib_filter = buf; lib_dirty = true; }
    ImGui::SameLine();
    ImGui::SetNextItemWidth(170);
    if (ImGui::BeginCombo("##libmap", lib_map.empty() ? "Every map" : lib_map.c_str())) {
        if (ImGui::Selectable("Every map", lib_map.empty())) { lib_map.clear(); lib_dirty = true; }
        for (auto& m : lib_maps) {
            std::string t = m.value("title", std::string());
            if (m.value("sp", false)) continue;
            if (m["index"].is_string() && ImGui::Selectable((t + "##" + m.value("archive", std::string())).c_str(), lib_map == t)) {
                lib_map = t;
                lib_dirty = true;
            }
        }
        // the install's singleplayer maps - read when chosen (Boston: ~6 minutes the first time), then kept
        bool any_sp = false;
        for (auto& m : lib_maps) any_sp |= m.value("sp", false);
        if (any_sp) ImGui::SeparatorText("Singleplayer (from the install)");
        for (auto& m : lib_maps) {
            if (!m.value("sp", false)) continue;
            std::string t = m.value("title", std::string()), a = m.value("archive", std::string());
            bool have = m["index"].is_string();
            std::string label = t + (have ? "" : m.value("indexing", false) ? "  (reading...)" : m["error"].is_string() ? "  (failed)"
                                                : sp_wanted.count(a) ? "  (waiting)" : "  (read it)");
            if (ImGui::Selectable((label + "##" + a).c_str(), lib_map == t)) {
                lib_map = t;
                lib_dirty = true;
                if (!have && !sp_wanted.count(a)) {
                    sp_wanted.insert(a);
                    add_log("library: reading " + t + " from the game install (its models, once; big maps take minutes)");
                    lib_poll(true);
                }
            }
        }
        ImGui::EndCombo();
    }
    ImGui::SameLine();
    if (ImGui::Checkbox("Hide ground pieces", &lib_hide_ground)) lib_dirty = true;
    ImGui::SameLine();
    ImGui::SetNextItemWidth(170);
    if (ImGui::BeginCombo("##libcat", lib_cat.empty() ? "Every category" : lib_cat.c_str())) {
        std::map<std::string, int> count;
        for (auto& L : lib) count[L.cat]++;
        if (ImGui::Selectable("Every category", lib_cat.empty())) { lib_cat.clear(); lib_dirty = true; }
        for (auto& c : categories().all()) {
            std::string label = c + "  (" + std::to_string(count[c]) + ")";
            if (ImGui::Selectable(label.c_str(), lib_cat == c)) { lib_cat = c; lib_dirty = true; }
        }
        ImGui::EndCombo();
    }
    if (ImGui::IsItemHovered()) ImGui::SetTooltip("Models filed by their names; right-click a model to file it elsewhere\n"
                                                  "(or under a category of your own)");
    ImGui::SameLine();
    if (lib_busy || ready + failed < wanted)
        ImGui::TextColored(ImVec4(0.95f, 0.6f, 0.25f, 1), "Indexing maps: %d of %d ready%s%s", ready, wanted,
                           indexing.empty() ? "" : " - now ", indexing.c_str());
    else
        ImGui::TextDisabled("%d models in %d maps%s", (int)lib.size(), ready, failed ? " (some maps failed: see Log)" : "");

    if (lib_dirty) {
        lib_dirty = false;
        lib_view.clear();
        for (size_t i = 0; i < lib.size(); i++) {
            LibEntry& L = lib[i];
            L.cat = categories().model(L.name, L.ground);
            if (!lib_cat.empty() && L.cat != lib_cat) continue;
            if (lib_hide_ground && L.ground && lib_cat != "Ground & paths") continue;
            if (!lib_map.empty() && L.map != lib_map) continue;
            if (!contains_ci(L.name + " " + L.materials, lib_filter)) continue;
            lib_view.push_back((int)i);
        }
        auto by = [&](int a, int b) {
            const LibEntry &x = lib[a], &y = lib[b];
            float fx = std::max({x.size[0], x.size[1], x.size[2]}), fy = std::max({y.size[0], y.size[1], y.size[2]});
            switch (lib_sort_col) {
            case 1: return x.map != y.map ? x.map < y.map : x.name < y.name;
            case 2: return fx < fy;
            case 3: return x.nt < y.nt;
            case 4: return x.uses < y.uses;
            case 5: return x.cat != y.cat ? x.cat < y.cat : x.name < y.name;
            default: return x.name != y.name ? x.name < y.name : x.map < y.map;
            }
        };
        std::stable_sort(lib_view.begin(), lib_view.end(), [&](int a, int b) { return lib_sort_asc ? by(a, b) : by(b, a); });
    }

    float side = std::min(330.0f, ImGui::GetContentRegionAvail().x * 0.4f);
    ImVec2 avail = ImGui::GetContentRegionAvail();
    int sel = lib_sel_key.empty() ? -1 : lib_find(lib_sel_key);
    if (ImGui::BeginTable("libtable", 6, ImGuiTableFlags_RowBg | ImGuiTableFlags_ScrollY | ImGuiTableFlags_Resizable |
                                         ImGuiTableFlags_BordersInnerV | ImGuiTableFlags_Sortable,
                          ImVec2(avail.x - side - 8, avail.y))) {
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableSetupColumn("Model", ImGuiTableColumnFlags_WidthStretch | ImGuiTableColumnFlags_DefaultSort);
        ImGui::TableSetupColumn("Map", ImGuiTableColumnFlags_WidthFixed, 120);
        ImGui::TableSetupColumn("Size (m)", ImGuiTableColumnFlags_WidthFixed, 130);
        ImGui::TableSetupColumn("Triangles", ImGuiTableColumnFlags_WidthFixed, 70);
        ImGui::TableSetupColumn("Shown", ImGuiTableColumnFlags_WidthFixed, 50);
        ImGui::TableSetupColumn("Category", ImGuiTableColumnFlags_WidthFixed, 130);
        ImGui::TableHeadersRow();
        if (ImGuiTableSortSpecs* sp = ImGui::TableGetSortSpecs())
            if (sp->SpecsDirty && sp->SpecsCount > 0) {
                lib_sort_col = sp->Specs[0].ColumnIndex;
                lib_sort_asc = sp->Specs[0].SortDirection != ImGuiSortDirection_Descending;
                sp->SpecsDirty = false;
                lib_dirty = true;
            }
        ImGuiListClipper clip;
        clip.Begin((int)lib_view.size());
        while (clip.Step())
            for (int r = clip.DisplayStart; r < clip.DisplayEnd; r++) {
                int i = lib_view[r];
                const LibEntry& L = lib[i];
                ImGui::TableNextRow();
                ImGui::TableSetColumnIndex(0);
                ImGui::PushID(i);
                if (ImGui::Selectable(L.name.c_str(), i == sel, ImGuiSelectableFlags_SpanAllColumns | ImGuiSelectableFlags_AllowDoubleClick)) {
                    lib_sel_key = L.key;
                    sel = i;
                    preview_key = L.key;
                    request_assets();
                    if (ImGui::IsMouseDoubleClicked(0) && scene) place_entry(i, placement_point());
                }
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s\n%s\nmaterials: %s\nDouble-click: place it where the camera looks\n"
                                                              "Right-click: file it under another category",
                                                              L.name.c_str(), L.map.c_str(), L.materials.c_str());
                if (ImGui::BeginPopupContextItem("filecat")) {
                    ImGui::TextDisabled("File %s under", L.name.c_str());
                    std::string chosen;
                    if (category_menu(L.cat, cats::automatic(L.name, L.ground), chosen)) file_model(L.name, L.ground, chosen);
                    ImGui::EndPopup();
                }
                ImGui::PopID();
                ImGui::TableSetColumnIndex(1);
                ImGui::TextUnformatted(L.map.c_str());
                ImGui::TableSetColumnIndex(2);
                ImGui::Text("%.1f x %.1f x %.1f", L.size[0], L.size[1], L.size[2]);
                ImGui::TableSetColumnIndex(3);
                ImGui::Text("%d", L.nt);
                ImGui::TableSetColumnIndex(4);
                ImGui::Text("%d", L.uses);
                ImGui::TableSetColumnIndex(5);
                ImGui::TextUnformatted(L.cat.c_str());
            }
        ImGui::EndTable();
    }
    ImGui::SameLine();
    ImGui::BeginChild("libside", ImVec2(side, avail.y));
    if (sel < 0) {
        ImGui::TextDisabled(lib.empty() ? "The library is being indexed (each map once,\nabout a minute a map the first time)."
                                        : "Click a model to see it.\nDouble-click to place it where the\ncamera looks.");
    } else {
        const LibEntry& L = lib[sel];
        float pw = side, ph = std::min(side * 0.75f, std::max(120.0f, avail.y - 150));
        auto it = assets_at.find(L.key);
        if (it != assets_at.end() && assets_scene && it->second < (int)assets_scene->meshes.size()) {
            // the preview: the model alone, turning slowly
            int k = it->second;
            const Mesh& me = assets_scene->meshes[k];
            V3 c = (me.lo + me.hi) * 0.5f;
            float r = std::max(0.5f, length(me.hi - me.lo) * 0.5f);
            preview_turn += ImGui::GetIO().DeltaTime * 0.35f;
            float d = r * 2.4f;
            V3 eye = c + V3(std::cos(preview_turn) * d, std::sin(preview_turn) * d, r * 0.75f);
            renderer.resize((int)pw, (int)ph, TARGET_PREVIEW);
            renderer.begin(look_at(eye, c, V3(0, 0, 1)), perspective(0.75f, pw / ph, std::max(0.02f, r * 0.02f), r * 12 + 10),
                           eye, TARGET_PREVIEW);
            std::vector<DrawItem> one{{assets_scene->entities[k].mesh, M4::identity(), {0, 0, 0, 0}, false, false}};
            renderer.draw_scene(*assets_scene, one, false, {}, {}, alpha_clip, SLOT_ASSETS);
            renderer.end();
            ImGui::Image((ImTextureID)(intptr_t)renderer.output(TARGET_PREVIEW), ImVec2(pw, ph));
        } else {
            ImGui::Dummy(ImVec2(pw, ph));
            ImVec2 a = ImGui::GetItemRectMin(), b = ImGui::GetItemRectMax();
            ImGui::GetWindowDrawList()->AddRectFilled(a, b, IM_COL32(40, 42, 48, 255));
            const char* msg = assets_loading ? "Reading the model from its map..." : "(no preview)";
            ImVec2 ts = ImGui::CalcTextSize(msg);
            ImGui::GetWindowDrawList()->AddText(ImVec2((a.x + b.x - ts.x) / 2, (a.y + b.y) / 2 - 8), IM_COL32(200, 200, 200, 255), msg);
        }
        ImGui::PushTextWrapPos(0);
        ImGui::TextColored(ImVec4(0.95f, 0.62f, 0.3f, 1), "%s", L.name.c_str());
        if (L.sp) ImGui::Text("%s - %.1f x %.1f x %.1f m (triangles and materials: read when placed)", L.map.c_str(), L.size[0], L.size[1], L.size[2]);
        else {
            ImGui::Text("%s - %.1f x %.1f x %.1f m, %d triangles", L.map.c_str(), L.size[0], L.size[1], L.size[2], L.nt);
            ImGui::TextDisabled("%d material(s): %s", L.n_materials, L.materials.c_str());
        }
        bool same = scene && L.archive == scene->archive && L.copyable;
        if (L.sp)
            ImGui::TextDisabled(L.collision == "none" ? "Singleplayer model (foliage): no collision; nothing to climb."
                                                      : "Singleplayer model: collision = its visible triangles; climbing = MP handhold "
                                                        "rows (SP climbs sill by sill - Inspector, once placed).");
        else ImGui::TextDisabled(same ? "This map's own: placed as a copy (its collision and climb data)."
                                 : L.collision == "own" ? "Collision: its map's own. Climbing: as in its map (Inspector, once placed)."
                                                        : "Collision: its visible triangles. Climbing: as in its map (Inspector, once placed).");
        ImGui::PopTextWrapPos();
        ImGui::BeginDisabled(!scene);
        if (ImGui::Button("Place where the camera looks")) place_entry(sel, placement_point());
        ImGui::EndDisabled();
        ImGui::SameLine();
        ImGui::BeginDisabled(workspace);
        if (ImGui::Button("Open in the asset editor")) open_workspace(L.name, "", json(), sel);
        ImGui::EndDisabled();
        // its materials, for walls / boxes / slabs
        if (it != assets_at.end() && it->second < (int)asset_meta.size()) {
            ImGui::SeparatorText("Its materials (for walls, boxes, slabs)");
            int n = 0;
            for (auto& mt : asset_meta[it->second].value("materials", json::array())) {
                if (!mt.value("textured", false)) continue;
                bool cur = prim_m == L.key && prim_mk == mt["key"].get<std::string>();
                ImGui::PushID(n++);
                if (ImGui::SmallButton(cur ? "in use" : "Use")) { prim_m = L.key; prim_mk = mt["key"].get<std::string>(); prim_mn = mt["name"].get<std::string>(); }
                ImGui::SameLine();
                ImGui::TextUnformatted(mt["name"].get<std::string>().c_str());
                ImGui::PopID();
            }
        }
    }
    ImGui::EndChild();
    ImGui::End();
}

// the stock maps' blend groups (one per kind per map), to place in the open map
void App::ui_blend_library() {
    if (!scene) { ImGui::TextDisabled("Open a map first."); return; }
    if (!blendv.loaded) {
        if (!blendv.loading) load_blend();
        ImGui::TextDisabled("Reading the blend groups (the first time: every stock map's, a minute or two)...");
        return;
    }
    if (ImGui::Checkbox("Show NPC slots in the viewport", &show_blend)) {}
    ImGui::SameLine();
    ImGui::TextDisabled("%d blend groups on this map; %d stock ones to place", (int)blendv.groups.size(), (int)blendv.templates.size());
    float side = std::min(330.0f, ImGui::GetContentRegionAvail().x * 0.4f);
    ImVec2 avail = ImGui::GetContentRegionAvail();
    if (blend_sel >= (int)blendv.templates.size()) blend_sel = -1;
    if (ImGui::BeginTable("blendtable", 5, ImGuiTableFlags_RowBg | ImGuiTableFlags_ScrollY | ImGuiTableFlags_Resizable |
                                           ImGuiTableFlags_BordersInnerV, ImVec2(avail.x - side - 8, avail.y))) {
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableSetupColumn("Blend group", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableSetupColumn("Map", ImGuiTableColumnFlags_WidthFixed, 110);
        ImGui::TableSetupColumn("NPCs", ImGuiTableColumnFlags_WidthFixed, 40);
        ImGui::TableSetupColumn("They hold", ImGuiTableColumnFlags_WidthFixed, 150);
        ImGui::TableSetupColumn("Stands at", ImGuiTableColumnFlags_WidthFixed, 90);
        ImGui::TableHeadersRow();
        for (int i = 0; i < (int)blendv.templates.size(); i++) {
            const BlendInfo& b = blendv.templates[i];
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            ImGui::PushID(i);
            if (!b.missing.empty()) ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.55f, 0.55f, 0.55f, 1));
            if (ImGui::Selectable(b.label.c_str(), i == blend_sel, ImGuiSelectableFlags_SpanAllColumns | ImGuiSelectableFlags_AllowDoubleClick)) {
                blend_sel = i;
                preview_key = b.key;
                request_assets();
                if (ImGui::IsMouseDoubleClicked(0) && b.missing.empty()) place_blend(i, placement_point());
            }
            if (!b.missing.empty()) ImGui::PopStyleColor();
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip("%s (%s)\n%s", b.name.c_str(), b.map.c_str(),
                                  b.missing.empty() ? "Double-click: place it where the camera looks"
                                                    : ("This map has no " + b.missing + " for its NPCs to hold").c_str());
            ImGui::PopID();
            ImGui::TableSetColumnIndex(1);
            ImGui::TextUnformatted(b.map.c_str());
            ImGui::TableSetColumnIndex(2);
            ImGui::Text("%d", (int)b.slots.size());
            ImGui::TableSetColumnIndex(3);
            ImGui::TextUnformatted(b.props.c_str());
            ImGui::TableSetColumnIndex(4);
            if (!b.set.empty()) ImGui::TextUnformatted(b.set[0].piece.c_str());
        }
        ImGui::EndTable();
    }
    ImGui::SameLine();
    ImGui::BeginChild("blendside", ImVec2(side, avail.y));
    if (blend_sel < 0) {
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("The static NPC groups players blend into - standing and posing groups, wall leaners, drinkers, "
                            "merchants - as AC3's own maps have them. Click one to see it, double-click to place it where "
                            "the camera looks. The map's own groups are in the viewport: select one to move, turn, copy "
                            "(Ctrl+D) or take it out (Delete).");
        ImGui::PopTextWrapPos();
    } else {
        const BlendInfo& b = blendv.templates[blend_sel];
        // a plan of its slots: where each NPC stands and which way it faces (the group's own frame, 1 m grid)
        float pw = side, ph = std::min(side * 0.75f, std::max(120.0f, avail.y - 150));
        ImGui::Dummy(ImVec2(pw, ph));
        ImVec2 a = ImGui::GetItemRectMin(), z = ImGui::GetItemRectMax();
        ImDrawList* dl = ImGui::GetWindowDrawList();
        dl->AddRectFilled(a, z, IM_COL32(36, 38, 44, 255));
        float r = 1.5f;
        for (auto& s : b.slots) r = std::max(r, std::max(std::fabs(s.p.x), std::fabs(s.p.y)) + 0.8f);
        float sc = std::min(pw, ph) * 0.5f / r;
        ImVec2 c((a.x + z.x) / 2, (a.y + z.y) / 2);
        for (int g = -(int)r; g <= (int)r; g++) {
            dl->AddLine(ImVec2(c.x + g * sc, a.y), ImVec2(c.x + g * sc, z.y), IM_COL32(70, 72, 80, 255));
            dl->AddLine(ImVec2(a.x, c.y - g * sc), ImVec2(z.x, c.y - g * sc), IM_COL32(70, 72, 80, 255));
        }
        dl->AddCircle(c, 0.12f * sc + 2, IM_COL32(200, 200, 210, 255));
        for (auto& s : b.slots) {
            ImVec2 p(c.x + s.p.x * sc, c.y - s.p.y * sc);
            dl->AddCircle(p, 0.28f * sc, IM_COL32(235, 90, 235, 255), 16, 2);
            dl->AddLine(p, ImVec2(p.x + s.f.x * 0.6f * sc, p.y - s.f.y * 0.6f * sc), IM_COL32(235, 90, 235, 255), 2);
        }
        ImGui::PushTextWrapPos(0);
        ImGui::TextColored(ImVec4(0.95f, 0.45f, 0.95f, 1), "%s", b.label.c_str());
        ImGui::Text("%s - %s", b.map.c_str(), b.name.c_str());
        ImGui::Text("%d NPC slot(s)%s%s", (int)b.slots.size(), b.props.empty() ? "" : "; they hold: ", b.props.c_str());
        if (!b.missing.empty())
            ImGui::TextColored(ImVec4(1, 0.6f, 0.35f, 1), "This map has no %s for its NPCs to hold: it can't go here.", b.missing.c_str());
        if (!b.set.empty()) {
            // the set it stands at in its own map (a merchant's stall...), placed with it as one group
            ImGui::Checkbox(("With its " + b.set[0].piece).c_str(), &blend_with_set);
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip("Place the group with what it stands at in %s, where it stands there, all as one group\n"
                                  "(a click picks the whole; double-click one piece to move or delete it)", b.map.c_str());
            for (auto& pc : b.set) ImGui::TextDisabled("  %s: %s", pc.piece.c_str(), pc.name.c_str());
        }
        ImGui::PopTextWrapPos();
        ImGui::BeginDisabled(!b.missing.empty());
        if (ImGui::Button("Place where the camera looks")) place_blend(blend_sel, placement_point());
        ImGui::EndDisabled();
    }
    ImGui::EndChild();
}

// every shortcut on one sheet (F1)
void App::ui_help() {
    if (!show_help) return;
    ImGui::SetNextWindowSize(ImVec2(640, 560), ImGuiCond_FirstUseEver);
    ImGuiViewport* mv = ImGui::GetMainViewport();
    ImGui::SetNextWindowPos(ImVec2(mv->WorkPos.x + mv->WorkSize.x * 0.5f, mv->WorkPos.y + mv->WorkSize.y * 0.5f), ImGuiCond_FirstUseEver, ImVec2(0.5f, 0.5f));
    if (!ImGui::Begin("Keyboard shortcuts", &show_help)) { ImGui::End(); return; }
    struct K { const char* keys; const char* what; };
    auto table = [](const char* title, std::initializer_list<K> rows) {
        ImGui::SeparatorText(title);
        if (ImGui::BeginTable(title, 2, ImGuiTableFlags_RowBg)) {
            ImGui::TableSetupColumn("k", ImGuiTableColumnFlags_WidthFixed, 190);
            for (auto& r : rows) {
                ImGui::TableNextRow();
                ImGui::TableSetColumnIndex(0);
                ImGui::TextColored(ImVec4(0.95f, 0.7f, 0.35f, 1), "%s", r.keys);
                ImGui::TableSetColumnIndex(1);
                ImGui::TextUnformatted(r.what);
            }
            ImGui::EndTable();
        }
    };
    table("Camera", {{"Right mouse + W A S D", "fly (Q / E down / up, Shift faster)"}, {"Right mouse + wheel", "flying speed"},
                     {"Wheel", "forwards / back"}, {"Middle mouse drag", "pan"}, {"Alt + left drag", "orbit round the selection"},
                     {"F", "frame the selection"}, {"Home", "the whole map"}, {"T", "look straight down"}});
    table("Select", {{"Left click", "select (Shift / Ctrl adds)"}, {"Drag from empty space", "box select (Shift adds)"},
                     {"B", "Select Building"}, {"G / Shift+G", "Select Attached: a ring / the whole structure"},
                     {"Double-click", "into a placed asset's parts"}, {"Esc", "select none / back out of an asset"}});
    table("Edit", {{"W / E", "move / turn (Tilt on the toolbar: every axis)"}, {"Ctrl+D", "copy"}, {"Delete", "remove from the map"},
                   {"Ctrl+R", "put back as the map has it"}, {"End", "drop to the ground"}, {"Ctrl+Z / Ctrl+Y", "undo / redo"},
                   {"L", "the Library (double-click a model to place it)"}, {"C", "collision on / off"}});
    table("Ground", {{"Ground > Sculpt", "click raises, Shift+click lowers, drag paints"}, {"Backspace", "the last stroke / corner back"},
                     {"Enter", "finish an area"}, {"Esc", "end sculpting / cancel an area"}});
    table("Scatter (P)", {{"Click / drag", "paint copies of the selection (or the Library's model) over the ground"},
                          {"Shift+drag", "take them away"}, {"Esc", "end"}});
    table("Walk (V)", {{"W A S D", "move (Shift runs, Ctrl walks)"}, {"Space", "jump, or climb a ledge in reach (again: onto it)"},
                       {"Right mouse", "look"}, {"R", "back to where you started"}, {"V / Esc", "leave"}});
    table("Project and build", {{"Ctrl+S / Ctrl+O", "save / open a project"}, {"Ctrl+B", "build a test map"},
                                {"F5", "test in game: build, install, start AC3; the original back when you quit"},
                                {"F1", "this sheet"}});
    table("Characters (the menu bar's Characters)", {{"Click a part", "pick it: the Inspector edits it (Parts lists them too)"},
                                {"Left drag / wheel", "turn / zoom"}, {"Esc", "the whole look"}, {"F", "frame the character"},
                                {"Space", "play / pause the clip"}, {"Ctrl+Z / Ctrl+Y", "undo / redo (every change is in Changes)"},
                                {"Ctrl+B", "build the changes into a test DataPC"},
                                {"F5", "test in game: build, install, start AC3; the original DataPC back when you quit"}});
    ImGui::End();
}

void App::ui_my_assets() {
    if (!user_scanned) scan_user_assets();
    if (ImGui::Button("Refresh")) scan_user_assets();
    ImGui::SameLine();
    ImGui::BeginDisabled(!scene || selection.empty());
    if (ImGui::Button("Save the selection as an asset...")) { ask_name_for = 0; ask_name = true; name_buf[0] = 0; }
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::TextDisabled("%d asset(s) in %s", (int)user_assets.size(), user_dir.c_str());
    ImGui::SameLine();
    ImGui::SetNextItemWidth(170);
    if (ImGui::BeginCombo("##mycat", my_cat.empty() ? "Every category" : my_cat.c_str())) {
        if (ImGui::Selectable("Every category", my_cat.empty())) my_cat.clear();
        for (auto& c : categories().all())
            if (ImGui::Selectable(c.c_str(), my_cat == c)) my_cat = c;
        ImGui::EndCombo();
    }
    float side = std::min(360.0f, ImGui::GetContentRegionAvail().x * 0.4f);
    ImVec2 avail = ImGui::GetContentRegionAvail();
    if (user_sel >= (int)user_assets.size()) user_sel = -1;
    if (ImGui::BeginTable("mytable", 4, ImGuiTableFlags_RowBg | ImGuiTableFlags_ScrollY | ImGuiTableFlags_Resizable |
                                        ImGuiTableFlags_BordersInnerV, ImVec2(avail.x - side - 8, avail.y))) {
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableSetupColumn("Asset", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableSetupColumn("Parts", ImGuiTableColumnFlags_WidthFixed, 50);
        ImGui::TableSetupColumn("Made on", ImGuiTableColumnFlags_WidthFixed, 130);
        ImGui::TableSetupColumn("Category", ImGuiTableColumnFlags_WidthFixed, 130);
        ImGui::TableHeadersRow();
        for (int i = 0; i < (int)user_assets.size(); i++) {
            const UserAsset& u = user_assets[i];
            std::string ucat = categories().asset(stem(u.path), u.name);
            if (!my_cat.empty() && ucat != my_cat) continue;
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            ImGui::PushID(i);
            if (ImGui::Selectable(u.name.c_str(), i == user_sel, ImGuiSelectableFlags_SpanAllColumns | ImGuiSelectableFlags_AllowDoubleClick)) {
                user_sel = i;
                if (ImGui::IsMouseDoubleClicked(0) && scene) place_user_asset(u.j, placement_point(), stem(u.path));
            }
            if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s\nDouble-click: place it where the camera looks\nRight-click: file it under a category", u.path.c_str());
            if (ImGui::BeginPopupContextItem("filecat")) {
                ImGui::TextDisabled("File %s under", u.name.c_str());
                std::string chosen;
                if (category_menu(ucat, cats::automatic(u.name), chosen)) file_asset(stem(u.path), u.name, chosen);
                ImGui::EndPopup();
            }
            ImGui::PopID();
            ImGui::TableSetColumnIndex(1);
            ImGui::Text("%d", u.parts);
            ImGui::TableSetColumnIndex(2);
            ImGui::TextUnformatted(u.made_on.c_str());
            ImGui::TableSetColumnIndex(3);
            ImGui::TextUnformatted(ucat.c_str());
        }
        ImGui::EndTable();
    }
    ImGui::SameLine();
    ImGui::BeginChild("myside", ImVec2(side, avail.y));
    if (user_sel < 0) {
        ImGui::PushTextWrapPos(0);
        ImGui::TextDisabled("Your own assets: select objects (any mix of map objects, copies, library models, walls), then "
                            "Edit > Save selection as an asset. Double-click one to place it; Open it in the asset editor "
                            "to change it.");
        ImGui::PopTextWrapPos();
    } else {
        const UserAsset& u = user_assets[user_sel];
        ImGui::PushTextWrapPos(0);
        ImGui::TextColored(ImVec4(0.95f, 0.62f, 0.3f, 1), "%s", u.name.c_str());
        ImGui::Text("%d part(s), made on %s, saved %s", u.parts, u.made_on.c_str(), u.j.value("saved", std::string()).c_str());
        ImGui::TextDisabled("%s", u.path.c_str());
        ImGui::PopTextWrapPos();
        ImGui::BeginDisabled(!scene);
        if (ImGui::Button("Place where the camera looks")) place_user_asset(u.j, placement_point(), stem(u.path));
        ImGui::EndDisabled();
        ImGui::SameLine();
        ImGui::BeginDisabled(workspace);
        if (ImGui::Button("Open in the asset editor")) { UserAsset c = u; open_workspace(c.name, c.path, c.j, -1); }
        ImGui::EndDisabled();
        ImGui::SeparatorText("Parts");
        ImGui::BeginChild("myparts");
        for (auto& p : u.j["parts"]) {
            std::string what = p.contains("prim") ? "primitive" : p.contains("asset") ? "asset " + p["asset"].get<std::string>()
                                                                 : p["model"].value("map", std::string());
            ImGui::BulletText("%s  (%s)", p.value("name", std::string("part")).c_str(), what.c_str());
        }
        ImGui::EndChild();
    }
    ImGui::EndChild();
}

void App::ui_log(const char* title) {
    ImGui::Begin(title);
    if (ImGui::Button("Clear")) log_lines.clear();
    ImGui::SameLine();
    if (ImGui::Button("Copy all")) {
        std::string all;
        for (auto& l : log_lines) all += l + "\n";
        ImGui::SetClipboardText(all.c_str());
    }
    ImGui::BeginChild("loglines", ImVec2(0, 0), ImGuiChildFlags_None, ImGuiWindowFlags_HorizontalScrollbar);
    ImGuiListClipper clip;
    clip.Begin((int)log_lines.size());
    while (clip.Step())
        for (int i = clip.DisplayStart; i < clip.DisplayEnd; i++) ImGui::TextUnformatted(log_lines[i].c_str());
    if (log_scroll) { ImGui::SetScrollHereY(1.0f); log_scroll = false; }
    ImGui::EndChild();
    ImGui::End();
}

void App::ui_viewport() {
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(2, 2));
    ImGui::Begin("Viewport", nullptr, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    ImGui::PopStyleVar();
    // toolbar
    auto tool = [&](const char* label, bool on, const char* tip) {
        if (on) ImGui::PushStyleColor(ImGuiCol_Button, ImGui::GetStyleColorVec4(ImGuiCol_ButtonActive));
        bool r = ImGui::Button(label);
        if (on) ImGui::PopStyleColor();
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s", tip);
        ImGui::SameLine();
        return r;
    };
    if (workspace) {
        ImGui::TextColored(ImVec4(0.45f, 0.85f, 1, 1), "ASSET EDITOR: %s%s", ws_name.c_str(), edits.dirty() ? " (changed)" : "");
        ImGui::SameLine();
        if (tool("Save", false, ws_path.empty() ? "Save as a new asset (My assets)" : "Save over this asset (Ctrl+S)")) save_workspace(false);
        if (tool("Save as new...", false, "Save as another asset, keeping this one")) { ask_name_for = 1; ask_name = true; name_buf[0] = 0; }
        if (tool("Close", false, "Back to the map")) { if (edits.dirty()) ask_close = true; else close_workspace(); }
        ImGui::TextDisabled("|");
        ImGui::SameLine();
    }
    if (tool("Move", gizmo_op == ImGuizmo::TRANSLATE, "Move the selection (W)")) gizmo_op = ImGuizmo::TRANSLATE;
    if (tool("Turn", gizmo_op != ImGuizmo::TRANSLATE, "Turn the selection (E)")) gizmo_op = all_axes ? ImGuizmo::ROTATE : ImGuizmo::ROTATE_Z;
    if (tool(all_axes ? "Tilt (on)" : "Tilt", all_axes,
             "Turning about every axis, not only the vertical: lean or tip things over. Their collision and climb data\n"
             "tilt with them; a tilted ledge may not grab as you expect - test it in game.")) {
        all_axes = !all_axes;
        if (gizmo_op != ImGuizmo::TRANSLATE) gizmo_op = all_axes ? ImGuizmo::ROTATE : ImGuizmo::ROTATE_Z;
    }
    if (tool(gizmo_local ? "Local" : "World", false, "Gizmo axes: the object's own or the world's")) gizmo_local = !gizmo_local;
    ImGui::Checkbox("Snap", &snap);
    ImGui::SameLine();
    if (snap) {
        ImGui::SetNextItemWidth(70);
        ImGui::DragFloat("##sm", gizmo_op == ImGuizmo::TRANSLATE ? &snap_move : &snap_turn, 0.1f, 0.05f, 90.0f,
                         gizmo_op == ImGuizmo::TRANSLATE ? "%.2f m" : "%.0f deg");
        ImGui::SameLine();
    }
    ImGui::TextDisabled("|");
    ImGui::SameLine();
    // the rest in four menus - Select, Add, Ground, Show (each a button opening its menu)
    bool sel = scene && !selection.empty();
    if (tool("Select", false, "Select Building, Select Attached (the pieces touching the selection), select none.\n"
                              "Box select: drag a rectangle from empty space (Shift adds).")) ImGui::OpenPopup("selmenu");
    if (ImGui::BeginPopup("selmenu")) {
        if (ImGui::MenuItem("Select Building", "B", false, sel && !workspace)) select_building();
        if (ImGui::MenuItem("Grow to what touches it (a ring)", "G", false, sel)) select_attached(false);
        if (ImGui::MenuItem("Grow to the whole structure", "Shift+G", false, sel)) select_attached(true);
        ImGui::SetNextItemWidth(160);
        ImGui::SliderFloat("Largest structure (m)", &attach_span, 5.0f, 120.0f, "%.0f m");
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Joined buildings share walls: this stops Select Attached running along a whole street");
        ImGui::Separator();
        if (ImGui::MenuItem("Drop to the ground", "End", false, sel)) drop_to_ground();
        if (ImGui::MenuItem("Frame the selection", "F", false, sel)) frame_selection();
        if (ImGui::MenuItem("Select none", "Esc", false, sel)) selection.clear();
        ImGui::TextDisabled("Box select: drag a rectangle from empty space");
        ImGui::EndPopup();
    }
    if (tool("Add", false, "Walls, boxes, floor slabs; models, blend groups and gameplay objects from the Library;\n"
                           "your own assets")) ImGui::OpenPopup("addmenu");
    if (ImGui::BeginPopup("addmenu")) {
        if (ImGui::MenuItem("Wall (4 x 0.3 x 3 m)", nullptr, false, scene != nullptr)) add_primitive("wall");
        if (ImGui::MenuItem("Box (1 m)", nullptr, false, scene != nullptr)) add_primitive("box");
        if (ImGui::MenuItem("Floor slab (4 x 4 x 0.3 m)", nullptr, false, scene != nullptr)) add_primitive("slab");
        ImGui::TextDisabled("  (in the material chosen in the Library: a model's materials, under its preview)");
        ImGui::Separator();
        if (ImGui::MenuItem("A model from the Library...", "L", false, scene != nullptr)) { lib_tab = 0; focus_library = true; }
        if (ImGui::MenuItem("One of my assets...", nullptr, false, scene != nullptr)) { lib_tab = 1; focus_library = true; }
        if (ImGui::MenuItem("A blend group...", nullptr, false, scene && !workspace)) { lib_tab = 2; focus_library = true; load_blend(); }
        if (ImGui::MenuItem("A gameplay object (hay cart, bench, door, lift)...", nullptr, false, scene && !workspace)) { lib_tab = 3; focus_library = true; }
        ImGui::Separator();
        if (ImGui::MenuItem("Scatter the selection (a brush)", "P", scattering, scene && !workspace)) start_scatter();
        ImGui::Separator();
        if (ImGui::MenuItem("Save the selection as an asset...", nullptr, false, sel)) { ask_name_for = 0; ask_name = true; name_buf[0] = 0; }
        if (ImGui::MenuItem("Make an asset from the building...", nullptr, false, sel && !workspace)) { ask_name_for = 2; ask_name = true; name_buf[0] = 0; }
        ImGui::EndPopup();
    }
    if (scene && !workspace) {
        const char* gl = sculpting ? "Ground (sculpting)" : drawing_region ? "Ground (drawing)" : "Ground";
        if (tool(gl, sculpting || drawing_region, "The ground: sculpt hills and hollows, raise / lower an area; the crowds'\n"
                                                  "navmesh: remake it in an area, keep crowds out of an area")) ImGui::OpenPopup("groundmenu");
        if (ImGui::BeginPopup("groundmenu")) {
            if (ImGui::MenuItem("Sculpt (a soft brush: click raises, Shift+click lowers)", nullptr, sculpting)) start_sculpt();
            if (ImGui::MenuItem("Raise / lower an area (its corners, Enter)...", nullptr, drawing_region && draw_kind == AREA_GROUND))
                { if (drawing_region) finish_region(); else start_region(); }
            ImGui::Separator();
            if (ImGui::MenuItem("Remake the navmesh in an area...")) start_region(AREA_NAV_REMAKE);
            if (ImGui::MenuItem("Keep crowds out of an area...")) start_region(AREA_NAV_BLOCK);
            if (drawing_region) {
                ImGui::Separator();
                if (ImGui::MenuItem("Finish the area", "Enter")) finish_region();
                if (ImGui::MenuItem("Cancel the area", "Esc")) cancel_region();
            }
            ImGui::EndPopup();
        }
    }
    bool shown = show_collision || show_climb || show_nav || show_waypoints || show_routes || show_volumes;
    if (tool("Show", shown, "What the viewport draws: collision, climbing, blend groups' slots, the navmesh, markers...")) ImGui::OpenPopup("showmenu");
    if (ImGui::BeginPopup("showmenu")) {
        ImGui::MenuItem("Collision (wireframe)", "C", &show_collision);
        if (ImGui::MenuItem("Climbing (edges; the selection green / red)", nullptr, &show_climb) && show_climb) load_climb();
        if (scene && !workspace) {
            if (ImGui::MenuItem("Blend groups' NPC slots", nullptr, &show_blend) && show_blend) load_blend();
            if (ImGui::MenuItem("The crowds' navmesh", nullptr, &show_nav) && show_nav) load_nav();
            if (ImGui::MenuItem("Navmesh waypoints and links", nullptr, &show_waypoints) && show_waypoints) load_nav();
            if (ImGui::MenuItem("Crowd routes (click one to edit it)", nullptr, &show_routes) && show_routes) load_nav();
        }
        ImGui::Separator();
        ImGui::MenuItem("Markers (spawns, lights, sounds...)", nullptr, &show_markers);
        ImGui::MenuItem("Gameplay volumes (out of bounds, zones)", nullptr, &show_volumes);
        ImGui::MenuItem("Spawn labels", nullptr, &show_labels);
        ImGui::MenuItem("Removed objects (red)", nullptr, &show_removed);
        ImGui::MenuItem("Alpha cut-outs (leaves, fences)", nullptr, &alpha_clip);
        if (navv.loaded) ImGui::TextDisabled("navmesh: %d triangles, %d waypoints, %d routes", navv.tris, (int)navv.wps.size(), (int)navv.routes.size());
        if (blendv.loaded) ImGui::TextDisabled("blend groups: %d on this map", (int)blendv.groups.size());
        ImGui::EndPopup();
    }
    if (scene && !workspace) {
        // walk mode
        if (tool(walking ? "Walking (V / Esc)" : "Walk", walking, "Walk the map as a player (V): W A S D, Shift runs, Space jumps or climbs a\n"
                                                                  "ledge in reach, right mouse looks, R back to where you started")) {
            if (walking) walking = false; else walk_enter(placement_point());
        }
        // Test in game
        const char* tl = tg_state == 1 ? "Testing: building..." : tg_state == 2 ? "Testing: installing..." : tg_state == 3 ? "Testing: starting AC3..."
                       : tg_state == 4 ? "Testing (AC3 running)" : "Test in game";
        if (tool(tl, tg_state != 0, "Build (if anything changed), install into the game, start AC3 (F5); when you quit it the\n"
                                    "original map goes back (Build menu: on / off)")) { if (!tg_state) test_in_game(); }
        // the live match view
        bool fresh = live_fresh();
        if (tool(!live.running() ? "Live" : fresh ? "Live (on)" : "Live (waiting)", live.running(),
                 "The live match view: you, the crowd NPCs, other players and your F9 marks from the running game\n"
                 "(the loader's live_map lever) drawn on this map, ten times a second")) ImGui::OpenPopup("livemenu");
        if (ImGui::BeginPopup("livemenu")) {
            bool on = live.running();
            if (ImGui::Checkbox("Listen to the game", &on)) live_toggle();
            ImGui::BeginDisabled(!live.running());
            ImGui::Checkbox("Follow me (the camera keeps you in view)", &live_follow);
            ImGui::SetNextItemWidth(120);
            ImGui::SliderFloat("Follow distance (m)", &live_follow_dist, 4.0f, 60.0f, "%.0f");
            ImGui::Checkbox("Show the crowd NPCs", &live_show_npcs);
            if (ImGui::MenuItem("Frame me", nullptr, false, livef.has_me)) frame_box(livef.me - V3(3, 3, 0), livef.me + V3(3, 3, 2));
            ImGui::EndDisabled();
            if (live.running()) {
                ImGui::Separator();
                if (!livef.received) ImGui::TextDisabled("Nothing received yet: start AC3 with the live_map lever.");
                else {
                    ImGui::Text("%s - %d datagram(s)%s", fresh ? "live" : "no data for a while (game paused, menu or closed?)",
                                livef.datagrams, livef.bad ? " (some unreadable)" : "");
                    ImGui::TextDisabled("World %s; me %s; %d other player(s); %d NPC(s) (scan: %d entities, %d ms, %d s ago)",
                                        livef.world.c_str(), livef.has_me ? "yes" : "no", (int)livef.players.size(),
                                        (int)livef.npcs.size(), livef.scan_found, livef.scan_ms, livef.scan_age);
                }
                ImGui::SeparatorText("Marks (F9 in the game)");
                if (livef.marks.empty()) ImGui::TextDisabled("None yet - press F9 in the game where you stand.");
                for (int k = 0; k < (int)livef.marks.size(); k++) {
                    const V3& p = livef.marks[k];
                    ImGui::PushID(k);
                    ImGui::Text("M%d  (%.1f, %.1f, %.1f)", k + 1, p.x, p.y, p.z);
                    ImGui::SameLine();
                    if (ImGui::SmallButton("Go")) live_frame_mark(k);
                    ImGui::SameLine();
                    if (ImGui::SmallButton("Place here")) {
                        live_place_pending = true;
                        live_place_at = p;
                        status = "M" + std::to_string(k + 1) + ": the next thing placed (Library double-click / Place, + Wall / Box / "
                                 "Slab) goes here";
                    }
                    if (ImGui::IsItemHovered()) ImGui::SetTooltip("The next placement goes on this mark instead of where the camera looks");
                    ImGui::SameLine();
                    if (ImGui::SmallButton("Copy")) {
                        char b[96];
                        snprintf(b, sizeof b, "%.2f, %.2f, %.2f", p.x, p.y, p.z);
                        ImGui::SetClipboardText(b);
                    }
                    ImGui::PopID();
                }
            }
            ImGui::EndPopup();
        }
    }
    if (scene) {
        ImGui::SetNextItemWidth(220);
        const char* cur = mode_record >= 0 ? scene->records[mode_record].label.c_str() : "No game mode";
        if (ImGui::BeginCombo("##mode", cur)) {
            if (ImGui::Selectable("No game mode", mode_record < 0)) mode_record = -1;
            for (size_t r = 0; r < scene->records.size(); r++)
                if (!scene->records[r].label.empty() &&
                    ImGui::Selectable((scene->records[r].label + "##" + scene->records[r].name).c_str(), mode_record == (int)r))
                    mode_record = (int)r;
            ImGui::EndCombo();
        }
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Show one game mode's objects: its spawn points and gameplay objects");
        ImGui::SameLine();
    }
    ImGui::SetNextItemWidth(90);
    ImGui::DragFloat("##speed", &cam.speed, 0.2f, 1, 200, "%.0f m/s");
    if (ImGui::IsItemHovered()) ImGui::SetTooltip("Camera speed (also: mouse wheel while holding the right button)");
    ImGui::NewLine();

    ImVec2 avail = ImGui::GetContentRegionAvail();
    vp_pos = ImGui::GetCursorScreenPos();
    vp_size = ImVec2(std::max(16.0f, avail.x), std::max(16.0f, avail.y));
    if (!scene) {
        ImGui::Dummy(vp_size);
        const char* msg = load_state == 1 ? "Reading the map (the engine is decoding it - Galata takes ~10 s the first time)..."
                          : load_state == 2 ? "Loading models and textures..."
                                            : "Double-click a map in Maps to open it.";
        ImVec2 ts = ImGui::CalcTextSize(msg);
        ImGui::GetWindowDrawList()->AddText(ImVec2(vp_pos.x + (vp_size.x - ts.x) / 2, vp_pos.y + vp_size.y / 2),
                                            IM_COL32(200, 200, 200, 255), msg);
        if (load_state == 0 && !last_map.empty() && file_exists(last_map)) {      // back where you left off
            std::string t = base_name(last_map);
            for (auto& m : maps) if (m.path == last_map) t = m.title;
            std::string label = "Reopen " + t;
            ImGui::SetCursorScreenPos(ImVec2(vp_pos.x + (vp_size.x - ImGui::CalcTextSize(label.c_str()).x) / 2 - 8, vp_pos.y + vp_size.y / 2 + 28));
            if (ImGui::Button(label.c_str())) open_map(last_map);
            ImGui::SetCursorScreenPos(ImVec2(vp_pos.x + (vp_size.x - 260) / 2, vp_pos.y + vp_size.y / 2 + 60));
            ImGui::TextDisabled("F1: every keyboard shortcut");
        }
        ImGui::End();
        return;
    }
    renderer.resize((int)vp_size.x, (int)vp_size.y);
    draw_viewport();
    ImGui::Image((ImTextureID)(intptr_t)renderer.output(), vp_size);
    bool hovered_vp = ImGui::IsItemHovered();
    vp_hovered = hovered_vp;
    ImDrawList* dl = ImGui::GetWindowDrawList();
    ImGuiIO& io = ImGui::GetIO();

    // gizmo
    ImGuizmo::SetOrthographic(false);
    ImGuizmo::SetDrawlist(dl);
    ImGuizmo::SetRect(vp_pos.x, vp_pos.y, vp_size.x, vp_size.y);
    std::vector<int> movers;
    for (int id : selection)
        if (edits.movable(id) && !edits.removed(id)) movers.push_back(id);
    bool using_gizmo = false;
    if (!movers.empty()) {
        if (!dragging) pivot = pivot_of(movers);
        float snapv[3] = {snap_move, snap_move, snap_move};
        if (gizmo_op != ImGuizmo::TRANSLATE) snapv[0] = snapv[1] = snapv[2] = snap_turn;
        ImGuizmo::OPERATION op = gizmo_op == ImGuizmo::TRANSLATE ? ImGuizmo::TRANSLATE
                                 : all_axes ? ImGuizmo::ROTATE : ImGuizmo::ROTATE_Z;
        M4 before = pivot;                                // (Manipulate changes the pivot on the frame a drag starts)
        ImGuizmo::Manipulate(view_m.m, proj_m.m, op, gizmo_local ? ImGuizmo::LOCAL : ImGuizmo::WORLD, pivot.m,
                             nullptr, snap ? snapv : nullptr);
        using_gizmo = ImGuizmo::IsUsing();
        if (using_gizmo && !dragging) {
            dragging = true;
            drag_pivot0 = before;
            drag_start.clear();
            for (int id : movers) drag_start.push_back(edits.matrix(id));
            // placed assets moved whole: their pivot moves with them (Apply to asset is relative to it)
            drag_groups.clear();
            drag_roots.clear();
            std::map<int, int> n_in, n_all;
            for (int id : movers)
                if (edits.is_copy(id)) n_in[edits.cur.copies[id - scene->entities.size()].group]++;
            for (auto& c : edits.cur.copies)
                if (!c.removed && c.group >= 0) n_all[c.group]++;
            for (auto& [g, n] : n_in)
                if (g >= 0 && n == n_all[g]) { drag_groups.push_back(g); drag_roots.push_back(edits.root(g)); }
        }
    }
    // the drag: every mover keeps its place relative to the pivot (row vectors: M' = M * P0^-1 * P)
    if (dragging && using_gizmo && drag_start.size() == movers.size()) {
        apply_pivot(movers, drag_start, drag_pivot0, pivot);
        for (size_t k = 0; k < drag_groups.size(); k++)
            if (drag_groups[k] < (int)edits.cur.group_root.size())
                edits.cur.group_root[drag_groups[k]] = drag_roots[k] * (inverse(drag_pivot0) * pivot);
    }
    if (dragging && !using_gizmo) {
        dragging = false;
        edits.commit();
    }
    if (movers.empty() && ms_corner_gizmo()) using_gizmo = true;   // a boundary corner being dragged

    // spawn labels for the shown mode
    if (show_labels && mode_record >= 0) {
        M4 vpm = view_m * proj_m;
        for (size_t i = 0; i < scene->entities.size(); i++) {
            const Entity& e = scene->entities[i];
            if (!(e.flags & F_SPAWN) || e.record != mode_record || !visible((int)i)) continue;
            V3 p = edits.cur.matrix[i].pos() + V3(0, 0, 1.6f);
            if (length(p - cam.pos) > 150) continue;
            float x = p.x * vpm.m[0] + p.y * vpm.m[4] + p.z * vpm.m[8] + vpm.m[12];
            float y = p.x * vpm.m[1] + p.y * vpm.m[5] + p.z * vpm.m[9] + vpm.m[13];
            float w = p.x * vpm.m[3] + p.y * vpm.m[7] + p.z * vpm.m[11] + vpm.m[15];
            if (w <= 0.1f) continue;
            ImVec2 sp(vp_pos.x + (x / w * 0.5f + 0.5f) * vp_size.x, vp_pos.y + (0.5f - y / w * 0.5f) * vp_size.y);
            std::string kind = e.note.substr(0, e.note.find(" - "));
            ImVec2 ts = ImGui::CalcTextSize(kind.c_str());
            dl->AddRectFilled(ImVec2(sp.x - ts.x / 2 - 3, sp.y - 1), ImVec2(sp.x + ts.x / 2 + 3, sp.y + ts.y + 1),
                              IM_COL32(15, 40, 15, 190), 3);
            dl->AddText(ImVec2(sp.x - ts.x / 2, sp.y), (e.flags & F_START) ? IM_COL32(150, 255, 120, 255) : IM_COL32(170, 230, 170, 255),
                        kind.c_str());
        }
    }

    ms_labels(dl);                                     // artifacts, scoring zones, Domination points of the mode

    // the walker's line - where it is, the last fall, what just happened
    if (walking) {
        char b[200];
        snprintf(b, sizeof b, "WALK  (%.1f, %.1f, %.1f)  %s  %.1f m/s%s", walk_p.x, walk_p.y, walk_p.z, walk_ground ? "on the ground" : "in the air",
                 std::sqrt(walk_v.x * walk_v.x + walk_v.y * walk_v.y), walk_fall > 0 ? ("  last fall " + std::to_string(walk_fall).substr(0, 4) + " m").c_str() : "");
        dl->AddRectFilled(ImVec2(vp_pos.x + 8, vp_pos.y + 8), ImVec2(vp_pos.x + 16 + ImGui::CalcTextSize(b).x, vp_pos.y + 30), IM_COL32(10, 30, 10, 200), 4);
        dl->AddText(ImVec2(vp_pos.x + 12, vp_pos.y + 11), IM_COL32(140, 255, 150, 255), b);
        if (!walk_msg.empty() && ImGui::GetTime() - walk_msg_t < 3)
            dl->AddText(ImVec2(vp_pos.x + 12, vp_pos.y + 34), IM_COL32(255, 200, 90, 255), walk_msg.c_str());
        dl->AddText(ImVec2(vp_pos.x + 12, vp_pos.y + vp_size.y - 24), IM_COL32(200, 200, 200, 200),
                    "W A S D move   Shift run   Ctrl walk   Space jump / climb   right mouse look   R start   V / Esc leave");
    }
    // the marks' labels (M1, M2 ...) over their diamonds
    if (live.running() && !livef.marks.empty()) {
        M4 vpm = view_m * proj_m;
        for (size_t k = 0; k < livef.marks.size(); k++) {
            V3 p = livef.marks[k] + V3(0, 0, 3.1f);
            float x = p.x * vpm.m[0] + p.y * vpm.m[4] + p.z * vpm.m[8] + vpm.m[12];
            float y = p.x * vpm.m[1] + p.y * vpm.m[5] + p.z * vpm.m[9] + vpm.m[13];
            float w = p.x * vpm.m[3] + p.y * vpm.m[7] + p.z * vpm.m[11] + vpm.m[15];
            if (w <= 0.1f) continue;
            ImVec2 sp(vp_pos.x + (x / w * 0.5f + 0.5f) * vp_size.x, vp_pos.y + (0.5f - y / w * 0.5f) * vp_size.y);
            std::string lb = "M" + std::to_string(k + 1);
            ImVec2 ts = ImGui::CalcTextSize(lb.c_str());
            dl->AddRectFilled(ImVec2(sp.x - ts.x / 2 - 3, sp.y - 1), ImVec2(sp.x + ts.x / 2 + 3, sp.y + ts.y + 1), IM_COL32(60, 30, 5, 200), 3);
            dl->AddText(ImVec2(sp.x - ts.x / 2, sp.y), IM_COL32(255, 170, 80, 255), lb.c_str());
        }
    }

    // scatter painting - while the button is held, a dab every half radius
    if (scattering && scatter_painting) {
        if (ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            V3 at;
            if (mouse_hit(at) && length(V3(at.x - scatter_last.x, at.y - scatter_last.y, 0)) >= scatter_r * 0.5f) {
                if (io.KeyShift) scatter_erase(at); else scatter_dab(at);
                scatter_last = at;
                status = std::to_string(scatter_made) + " scattered so far";
            }
        } else {
            scatter_painting = false;
            if (scatter_changed) edits.commit();
            scatter_changed = false;
        }
    }
    // sculpt painting - while the button is held, a stroke every half radius (a third of the height each)
    if (sculpting && sculpt_painting) {
        if (ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            V3 at;
            if (mouse_hit(at) && length(V3(at.x - sculpt_last.x, at.y - sculpt_last.y, 0)) >= brush_r * 0.5f)
                add_stroke(at, (io.KeyShift ? -brush_h : brush_h) * 0.35f);
        } else {
            sculpt_painting = false;
            edits.commit();
        }
    }

    // box select - drawn while dragging from empty space; on release, every object whose box is wholly inside
    if (marquee_on) {
        ImVec2 b = io.MousePos;
        ImVec2 r0(std::min(marquee_a.x, b.x), std::min(marquee_a.y, b.y)), r1(std::max(marquee_a.x, b.x), std::max(marquee_a.y, b.y));
        bool big = r1.x - r0.x > 6 || r1.y - r0.y > 6;
        if (ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            if (big) {
                dl->AddRectFilled(r0, r1, IM_COL32(255, 150, 40, 30));
                dl->AddRect(r0, r1, IM_COL32(255, 150, 40, 200));
            }
        } else {
            marquee_on = false;
            if (big) box_select(r0, r1, marquee_add);
        }
    }

    // camera and picking
    bool over_gizmo = ImGuizmo::IsOver() || using_gizmo;
    walk_tick(hovered_vp);                            // (the walker drives the camera)
    if (hovered_vp && !walking) {
        float dt = io.DeltaTime;
        V3 f = cam.forward(), r = cam.right(), up(0, 0, 1);
        if (ImGui::IsMouseDown(ImGuiMouseButton_Right)) {
            cam.yaw -= io.MouseDelta.x * 0.0035f;
            cam.pitch = std::clamp(cam.pitch - io.MouseDelta.y * 0.0035f, -1.56f, 1.56f);
            float sp = cam.speed * (io.KeyShift ? 4.0f : 1.0f) * dt;
            if (ImGui::IsKeyDown(ImGuiKey_W)) cam.pos += f * sp;
            if (ImGui::IsKeyDown(ImGuiKey_S)) cam.pos += f * -sp;
            if (ImGui::IsKeyDown(ImGuiKey_D)) cam.pos += r * sp;
            if (ImGui::IsKeyDown(ImGuiKey_A)) cam.pos += r * -sp;
            if (ImGui::IsKeyDown(ImGuiKey_E)) cam.pos += up * sp;
            if (ImGui::IsKeyDown(ImGuiKey_Q)) cam.pos += up * -sp;
            if (io.MouseWheel != 0) cam.speed = std::clamp(cam.speed * (io.MouseWheel > 0 ? 1.25f : 0.8f), 1.0f, 300.0f);
        } else if (io.MouseWheel != 0) {
            cam.pos += f * (io.MouseWheel * std::max(1.0f, cam.speed * 0.4f));
        }
        if (ImGui::IsMouseDown(ImGuiMouseButton_Middle)) {
            float k = cam.speed * 0.006f;
            V3 camup = cross(r, f);
            cam.pos += r * (-io.MouseDelta.x * k) + camup * (io.MouseDelta.y * k);
        }
        if (io.KeyAlt && ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            // orbit round the selection, or a point 25 m ahead
            V3 c = cam.pos + f * 25.0f;
            if (!selection.empty()) { V3 a, b; world_box(selection[0], a, b); c = (a + b) * 0.5f; }
            float dist = length(cam.pos - c);
            cam.yaw -= io.MouseDelta.x * 0.005f;
            cam.pitch = std::clamp(cam.pitch - io.MouseDelta.y * 0.005f, -1.56f, 1.56f);
            cam.pos = c - cam.forward() * dist;
        } else if (ImGui::IsMouseClicked(ImGuiMouseButton_Left) && !over_gizmo) {
            ImVec2 mp = io.MousePos;
            float nx = (mp.x - vp_pos.x) / vp_size.x * 2 - 1, ny = 1 - (mp.y - vp_pos.y) / vp_size.y * 2;
            M4 inv = inverse(view_m * proj_m);
            auto unproj = [&](float z) {
                float x = nx * inv.m[0] + ny * inv.m[4] + z * inv.m[8] + inv.m[12];
                float y = nx * inv.m[1] + ny * inv.m[5] + z * inv.m[9] + inv.m[13];
                float zz = nx * inv.m[2] + ny * inv.m[6] + z * inv.m[10] + inv.m[14];
                float w = nx * inv.m[3] + ny * inv.m[7] + z * inv.m[11] + inv.m[15];
                return V3(x / w, y / w, zz / w);
            };
            V3 a = unproj(0), b = unproj(1);
            if (scattering) {
                V3 at;
                if (mouse_hit(at)) {
                    int n = io.KeyShift ? scatter_erase(at) : scatter_dab(at);
                    scatter_painting = true;
                    scatter_last = at;
                    status = io.KeyShift ? std::to_string(n) + " taken away - drag to take more" :
                             std::to_string(scatter_made) + " scattered so far - drag to paint, Shift+drag takes away, Esc ends";
                } else status = "Scatter: nothing there - click on the ground";
            } else if (sculpting) {
                V3 at;
                if (mouse_hit(at)) {
                    add_stroke(at, io.KeyShift ? -brush_h : brush_h);
                    sculpt_painting = true;
                    status = std::to_string(edits.cur.sculpts.size()) + " stroke(s) - drag to paint, Backspace takes one back, Esc ends";
                } else status = "Sculpt: nothing there - click on the ground";
            } else if (drawing_region) {
                float t = 0;
                int hit = pick(a, normalize(b - a), &t);
                if (ImGui::IsMouseDoubleClicked(ImGuiMouseButton_Left)) finish_region();
                else if (hit >= 0) {
                    draft.push_back(a + normalize(b - a) * t);
                    status = std::to_string(draft.size()) + " corner(s) - Enter or double-click finishes, Backspace drops one";
                } else status = "Nothing there to stand on - click on the ground";
            } else if (ms_click(io.MousePos)) {
                // a boundary corner picked (the area being edited)
            } else {
                int route = -1;
                if (show_routes && navv.loaded) {                 // the route whose line passes within 10 px of the click
                    M4 vpm = view_m * proj_m;
                    auto scr = [&](V3 p, ImVec2& o) {
                        float x = p.x * vpm.m[0] + p.y * vpm.m[4] + p.z * vpm.m[8] + vpm.m[12];
                        float y = p.x * vpm.m[1] + p.y * vpm.m[5] + p.z * vpm.m[9] + vpm.m[13];
                        float w = p.x * vpm.m[3] + p.y * vpm.m[7] + p.z * vpm.m[11] + vpm.m[15];
                        if (w <= 0.1f) return false;
                        o = ImVec2(vp_pos.x + (x / w * 0.5f + 0.5f) * vp_size.x, vp_pos.y + (0.5f - y / w * 0.5f) * vp_size.y);
                        return true;
                    };
                    float best = 10.0f;
                    for (size_t k = 0; k < navv.routes.size(); k++)
                        for (size_t i = 0; i + 1 < navv.routes[k].pts.size(); i++) {
                            ImVec2 p, q;
                            if (!scr(navv.routes[k].pts[i] + V3(0, 0, 0.15f), p) || !scr(navv.routes[k].pts[i + 1] + V3(0, 0, 0.15f), q)) continue;
                            float dx = q.x - p.x, dy = q.y - p.y, l2 = dx * dx + dy * dy;
                            float t = l2 > 0 ? std::clamp(((io.MousePos.x - p.x) * dx + (io.MousePos.y - p.y) * dy) / l2, 0.0f, 1.0f) : 0;
                            float ex = p.x + t * dx - io.MousePos.x, ey = p.y + t * dy - io.MousePos.y;
                            float d = std::sqrt(ex * ex + ey * ey);
                            if (d < best) { best = d; route = (int)k; }
                        }
                }
                if (route >= 0) {
                    sel_route = route;
                    selection.clear();
                    sel_region = -1;
                    status = "Crowd route " + navv.routes[route].name + " - switch it on / off or change its width in the Inspector";
                } else {
                    int hit = pick(a, normalize(b - a));
                    if (hit < 0 && !ImGui::IsMouseDoubleClicked(ImGuiMouseButton_Left)) {   // maybe a box select
                        marquee_on = true;
                        marquee_add = io.KeyShift || io.KeyCtrl;
                        marquee_a = io.MousePos;
                    }
                    click_pick(hit, io.KeyShift || io.KeyCtrl, ImGui::IsMouseDoubleClicked(ImGuiMouseButton_Left));
                    if (!selection.empty()) { sel_region = -1; sel_route = -1; }
                }
            }
        }
    }
    // the selection's name in a corner
    if (!selection.empty()) {
        std::string s = !scope_label().empty() ? scope_label() + "  (" + std::to_string(selection.size()) + " objects)"
                        : selection.size() == 1 ? label_of(selection[0]) : std::to_string(selection.size()) + " objects";
        dl->AddText(ImVec2(vp_pos.x + 10, vp_pos.y + vp_size.y - 24), IM_COL32(255, 170, 90, 255), s.c_str());
    }
    char buf[96];
    snprintf(buf, sizeof buf, "%.1f, %.1f, %.1f   %.0f fps", cam.pos.x, cam.pos.y, cam.pos.z, io.Framerate);
    ImVec2 ts = ImGui::CalcTextSize(buf);
    dl->AddText(ImVec2(vp_pos.x + vp_size.x - ts.x - 10, vp_pos.y + vp_size.y - 24), IM_COL32(200, 200, 200, 200), buf);
    ImGui::End();
}

void App::draw_viewport() {
    view_m = cam.view();
    proj_m = perspective(fov * 3.14159265f / 180.0f, vp_size.x / vp_size.y, 0.25f, 6000.0f);
    renderer.begin(view_m, proj_m, cam.pos);
    std::vector<DrawItem> items;
    std::vector<int> col_ents;
    std::vector<M4> col_mats;
    items.reserve(edits.count());
    std::vector<LineVert> lines;
    std::vector<DrawItem> asset_items;                 // library models: drawn from the asset scene
    std::vector<int> asset_col;
    std::vector<M4> asset_col_mats;
    for (int id = 0; id < edits.count(); id++) {
        if (!visible(id)) continue;
        if (edits.is_placed(id)) {
            int k = asset_slot(id);
            if (k < 0) continue;                       // (its model is still being read: the selection box shows it)
            DrawItem it{assets_scene->entities[k].mesh, edits.matrix(id), {0, 0, 0, 0}, selected(id), false};
            if (edits.removed(id)) { it.color[0] = 0.9f; it.color[1] = 0.15f; it.color[2] = 0.1f; it.color[3] = 0.6f; }
            asset_items.push_back(it);
            if (show_collision && !edits.removed(id)) { asset_col.push_back(k); asset_col_mats.push_back(edits.matrix(id)); }
            continue;
        }
        const Entity& e = scene->entities[edits.entity_of(id)];
        bool sel = selected(id);
        DrawItem it{e.mesh, edits.matrix(id), {0, 0, 0, 0}, sel, (e.flags & F_VOLUME) != 0};
        if (edits.removed(id)) { it.color[0] = 0.9f; it.color[1] = 0.15f; it.color[2] = 0.1f; it.color[3] = 0.6f; }
        if (it.wire) { it.color[0] = 1.0f; it.color[1] = 0.6f; it.color[2] = 0.1f; it.color[3] = 1; }
        if (e.mesh < 0) {
            float s = marker_size(e);
            M4 sc = M4::identity();
            sc.m[0] = sc.m[5] = sc.m[10] = s;
            M4 m = edits.matrix(id);
            // markers upright at their place (an entity's own scale / tilt doesn't matter for a marker)
            M4 up = rot_z(std::atan2(m.m[1], m.m[0]));
            up.set_pos(m.pos());
            it.matrix = sc * up;
            float c[3] = {0.55f, 0.75f, 1.0f};
            if (e.flags & F_SPAWN) { c[0] = 0.25f; c[1] = 0.95f; c[2] = 0.35f; }
            if (e.flags & F_START) { c[0] = 0.75f; c[1] = 1.0f; c[2] = 0.2f; }
            if (sel) { c[0] = 1.0f; c[1] = 0.6f; c[2] = 0.15f; }
            it.color[0] = c[0]; it.color[1] = c[1]; it.color[2] = c[2]; it.color[3] = 1;
            if (e.flags & F_SPAWN) {
                // facing: local +y
                V3 fwd = normalize(m.dir(V3(0, 1, 0)));
                V3 a = m.pos() + V3(0, 0, 0.1f), b = a + fwd * 1.6f;
                uint32_t col = rgba(c[0], c[1], c[2]);
                lines.push_back({a, col}); lines.push_back({b, col});
                V3 side = normalize(cross(fwd, V3(0, 0, 1))) * 0.35f;
                lines.push_back({b, col}); lines.push_back({b - fwd * 0.45f + side, col});
                lines.push_back({b, col}); lines.push_back({b - fwd * 0.45f - side, col});
            }
        }
        items.push_back(it);
        if (show_collision && !e.shapes.empty() && !edits.removed(id)) {
            col_ents.push_back(edits.entity_of(id));
            col_mats.push_back(edits.matrix(id));
        }
    }
    renderer.draw_scene(*scene, items, show_collision, col_ents, col_mats, alpha_clip);
    if (assets_scene && !asset_items.empty())
        renderer.draw_scene(*assets_scene, asset_items, show_collision, asset_col, asset_col_mats, alpha_clip, SLOT_ASSETS);
    // the selection's box
    for (int id : selection) {
        if (!visible(id)) continue;
        V3 lo, hi;
        world_box(id, lo, hi);
        uint32_t col = rgba(1.0f, 0.6f, 0.15f, 0.55f);
        V3 c[8];
        for (int k = 0; k < 8; k++) c[k] = V3(k & 1 ? hi.x : lo.x, k & 2 ? hi.y : lo.y, k & 4 ? hi.z : lo.z);
        int edges[12][2] = {{0, 1}, {2, 3}, {4, 5}, {6, 7}, {0, 2}, {1, 3}, {4, 6}, {5, 7}, {0, 4}, {1, 5}, {2, 6}, {3, 7}};
        for (auto& ed : edges) { lines.push_back({c[ed[0]], col}); lines.push_back({c[ed[1]], col}); }
    }
    if (workspace)                                    // the asset editor's ground: a 1 m grid, the pivot's axes
        for (int k = -30; k <= 30; k++) {
            uint32_t cx = k == 0 ? rgba(0.95f, 0.35f, 0.3f, 0.9f) : rgba(0.55f, 0.57f, 0.62f, k % 5 ? 0.25f : 0.5f);
            uint32_t cy = k == 0 ? rgba(0.4f, 0.9f, 0.4f, 0.9f) : cx;
            lines.push_back({V3(-30, (float)k, 0), cx}); lines.push_back({V3(30, (float)k, 0), cx});
            lines.push_back({V3((float)k, -30, 0), cy}); lines.push_back({V3((float)k, 30, 0), cy});
        }
    renderer.draw_lines(lines, false);
    // the crowds' navmesh - its edges (walls red, drops orange), waypoints and links, crowd routes (on: yellow,
    // off: grey, the selected one orange; their width at the sides; an arrow on each stretch)
    if (navv.loaded && (show_nav || show_waypoints || show_routes)) {
        std::vector<LineVert> nl;
        const V3 up(0, 0, 0.06f);
        if (show_nav)
            for (size_t i = 0; i < navv.ea.size(); i++) {
                int k = navv.ekind[i];
                uint32_t c = k == 3 ? rgba(1, 0.25f, 0.25f, 0.95f) : k == 2 ? rgba(1, 0.6f, 0.15f, 0.95f)
                           : k == 1 ? rgba(0.4f, 0.9f, 0.5f, 0.55f) : rgba(0.35f, 0.85f, 0.45f, 0.3f);
                nl.push_back({navv.ea[i] + up, c});
                nl.push_back({navv.eb[i] + up, c});
            }
        if (show_waypoints) {
            for (auto& [i, j] : navv.links) {
                nl.push_back({navv.wps[i] + V3(0, 0, 0.3f), rgba(0.3f, 0.8f, 1, 0.3f)});
                nl.push_back({navv.wps[j] + V3(0, 0, 0.3f), rgba(0.3f, 0.8f, 1, 0.3f)});
            }
            for (auto& p : navv.wps) {
                nl.push_back({p, rgba(0.3f, 0.85f, 1, 0.95f)});
                nl.push_back({p + V3(0, 0, 0.9f), rgba(0.3f, 0.85f, 1, 0.95f)});
            }
        }
        if (show_routes)
            for (size_t k = 0; k < navv.routes.size(); k++) {
                const NavRoute& rt = navv.routes[k];
                bool on = route_active((int)k), sel = (int)k == sel_route;
                uint32_t c = sel ? rgba(1, 0.55f, 0.15f, 1) : on ? rgba(1, 0.9f, 0.2f, 0.95f) : rgba(0.6f, 0.6f, 0.6f, 0.8f);
                uint32_t cs = sel ? rgba(1, 0.55f, 0.15f, 0.6f) : on ? rgba(1, 0.9f, 0.2f, 0.35f) : rgba(0.6f, 0.6f, 0.6f, 0.3f);
                float hw = route_width((int)k) / 2;
                for (size_t i = 0; i + 1 < rt.pts.size(); i++) {
                    V3 a = rt.pts[i] + V3(0, 0, 0.15f), b = rt.pts[i + 1] + V3(0, 0, 0.15f);
                    V3 d = b - a;
                    d.z = 0;
                    V3 n = normalize(cross(normalize(d), V3(0, 0, 1))) * hw;
                    nl.push_back({a, c}); nl.push_back({b, c});
                    nl.push_back({a + n, cs}); nl.push_back({b + n, cs});
                    nl.push_back({a - n, cs}); nl.push_back({b - n, cs});
                    V3 m = (a + b) * 0.5f, f = normalize(d) * 0.6f, s2 = normalize(cross(f, V3(0, 0, 1))) * 0.35f;
                    nl.push_back({m + f, c}); nl.push_back({m - f * 0.0f - f + s2, c});
                    nl.push_back({m + f, c}); nl.push_back({m - f + (s2 * -1.0f), c});
                }
            }
        renderer.draw_lines(nl, false);
    }
    // blend groups' NPC slots - a ring where each NPC stands, a tick for its facing and a faint post its height
    // (the selected group's brighter); they move and turn with their group
    if (show_blend && blendv.loaded) {
        std::vector<LineVert> bl;
        for (int id = 0; id < edits.count(); id++) {
            if (!visible(id) || edits.removed(id)) continue;
            const BlendInfo* b = blend_of(id);
            if (!b) continue;
            const M4& m = edits.matrix(id);
            bool sel = selected(id);
            uint32_t c = sel ? rgba(1, 0.6f, 1, 1) : rgba(0.92f, 0.35f, 0.92f, 0.9f);
            uint32_t cp = sel ? rgba(1, 0.6f, 1, 0.55f) : rgba(0.92f, 0.35f, 0.92f, 0.3f);
            for (auto& s : b->slots) {
                V3 p = m.point(s.p) + V3(0, 0, 0.06f);
                V3 f = m.dir(s.f);
                f.z = 0;
                f = length(f) > 1e-4f ? normalize(f) : V3(1, 0, 0);
                const int n = 12;
                for (int k = 0; k < n; k++) {
                    float a0 = 6.2831853f * k / n, a1 = 6.2831853f * (k + 1) / n;
                    bl.push_back({p + V3(std::cos(a0), std::sin(a0), 0) * 0.28f, c});
                    bl.push_back({p + V3(std::cos(a1), std::sin(a1), 0) * 0.28f, c});
                }
                bl.push_back({p, c});
                bl.push_back({p + f * 0.6f, c});
                bl.push_back({p, cp});
                bl.push_back({p + V3(0, 0, 1.75f), cp});
            }
        }
        renderer.draw_lines(bl, false);
    }
    // climbing - every climb edge (ledges amber, poles cyan, beams violet; a tick on the side the climber hangs
    // on) where its object stands now; the selection's collision: climbable faces green, no-climb faces red
    if (show_climb) {
        std::vector<LineVert> cl;
        std::vector<LineVert> ct;                          // the climbable / no-climb faces of the selection
        auto put = [&](const ClimbEdge& e, float a) {
            uint32_t c = e.kind == 1 ? rgba(1, 0.72f, 0.1f, a) : e.kind == 4 ? rgba(0.2f, 0.9f, 1, a)
                       : e.kind == 14 ? rgba(0.75f, 0.45f, 1, a) : rgba(1, 1, 1, a);
            V3 up(0, 0, 0.04f), h(e.hx, e.hy, 0);
            // the edge doubled 3 cm apart (lines are a pixel wide), a short tick down every 0.5 m (a "fence"
            // you can see from afar), and a tick out on the side the climber hangs on
            V3 d = e.b - e.a;
            float len = length(d);
            V3 side = len > 1e-4f ? normalize(cross(d, V3(0, 0, 1))) * 0.03f : V3(0.03f, 0, 0);
            for (V3 o : {V3(0, 0, 0), side, side * -1.0f}) { cl.push_back({e.a + up + o, c}); cl.push_back({e.b + up + o, c}); }
            int n = std::min(40, (int)(len / 0.5f));
            for (int k = 0; k <= n; k++) {
                V3 p = e.a + d * (n ? (float)k / n : 0.5f) + up;
                cl.push_back({p, c});
                cl.push_back({p - V3(0, 0, 0.18f), c});
            }
            if (length(h) > 0.1f) {
                V3 m = (e.a + e.b) * 0.5f + up;
                cl.push_back({m, c});
                cl.push_back({m + normalize(h) * 0.4f, c});
            }
        };
        std::vector<ClimbEdge> tmp;
        for (int id = 0; id < edits.count(); id++) {
            if (edits.removed(id)) continue;
            if (!edits.is_copy(id)) {
                const Entity& e = scene->entities[id];
                if (e.record >= 0 && record_hidden[e.record]) continue;
                if ((e.flags & F_MODE) && mode_record != e.record) continue;
            }
            tmp.clear();
            climb_edges_of(id, tmp);
            float a = selected(id) ? 1.0f : 0.85f;
            for (auto& e : tmp) put(e, a);
        }
        for (auto& e : climbv.edges)
            if (e.owner < 0) put(e, 0.85f);
        size_t budget = 0;
        for (int id : selection)
            collision_tris(id, [&](const V3* p, bool no) {
                if (++budget > 60000) return;
                uint32_t c = no ? rgba(1, 0.2f, 0.15f, 0.33f) : rgba(0.25f, 0.95f, 0.4f, 0.22f);
                uint32_t w = no ? rgba(1, 0.3f, 0.2f, 0.75f) : rgba(0.35f, 1, 0.5f, 0.5f);
                for (int j = 0; j < 3; j++) ct.push_back({p[j], c});
                for (int j = 0; j < 3; j++) { cl.push_back({p[j], w}); cl.push_back({p[(j + 1) % 3], w}); }
            });
        renderer.draw_tris(ct, false);
        renderer.draw_lines(cl, false);
    }
    // the scatter brush's ring (amber; red with Shift: taking away), the palette's copies marked
    if (scattering) {
        std::vector<LineVert> scl;
        V3 at;
        if (vp_hovered && mouse_hit(at)) {
            uint32_t col = ImGui::GetIO().KeyShift ? rgba(1, 0.35f, 0.3f, 1) : rgba(1, 0.75f, 0.2f, 1);
            for (int k = 0; k < 48; k++) {
                float a0 = 6.2831853f * k / 48, a1 = 6.2831853f * (k + 1) / 48;
                scl.push_back({at + V3(std::cos(a0) * scatter_r, std::sin(a0) * scatter_r, 0.08f), col});
                scl.push_back({at + V3(std::cos(a1) * scatter_r, std::sin(a1) * scatter_r, 0.08f), col});
            }
        }
        for (int id = (int)scene->entities.size(); id < edits.count(); id++)
            if (scatter_mine(id)) {
                V3 p = foot_of(id);
                scl.push_back({p, rgba(1, 0.75f, 0.2f, 0.8f)});
                scl.push_back({p + V3(0, 0, 0.6f), rgba(1, 0.75f, 0.2f, 0.8f)});
            }
        renderer.draw_lines(scl, false);
    }
    // the walker - a capsule's rings and posts, green on the ground, amber in the air; climb edges in reach
    if (walking) {
        std::vector<LineVert> wl;
        uint32_t c = walk_ground ? rgba(0.35f, 1, 0.45f, 1) : rgba(1, 0.75f, 0.2f, 1);
        for (float z : {0.05f, 0.9f, 1.75f})
            for (int k = 0; k < 16; k++) {
                float a0 = 6.2831853f * k / 16, a1 = 6.2831853f * (k + 1) / 16;
                wl.push_back({walk_p + V3(std::cos(a0) * 0.35f, std::sin(a0) * 0.35f, z), c});
                wl.push_back({walk_p + V3(std::cos(a1) * 0.35f, std::sin(a1) * 0.35f, z), c});
            }
        for (int k = 0; k < 4; k++) {
            float a = 1.5707963f * k;
            V3 o(std::cos(a) * 0.35f, std::sin(a) * 0.35f, 0);
            wl.push_back({walk_p + o + V3(0, 0, 0.05f), c});
            wl.push_back({walk_p + o + V3(0, 0, 1.75f), c});
        }
        V3 f(std::cos(cam.yaw), std::sin(cam.yaw), 0);
        wl.push_back({walk_p + V3(0, 0, 1.0f), c});
        wl.push_back({walk_p + V3(0, 0, 1.0f) + f * 0.7f, c});
        for (auto& e : walk_edges) {                                  // (in reach: amber, thick)
            V3 m = (e.a + e.b) * 0.5f;
            if (length(V3(m.x - walk_p.x, m.y - walk_p.y, 0)) > 4 || m.z - walk_p.z > 3 || m.z < walk_p.z - 0.5f) continue;
            for (float o : {0.0f, 0.03f, 0.06f}) { wl.push_back({e.a + V3(0, 0, o), rgba(1, 0.75f, 0.1f, 1)}); wl.push_back({e.b + V3(0, 0, o), rgba(1, 0.75f, 0.1f, 1)}); }
        }
        renderer.draw_lines(wl, true);
    }
    // the sculpt - the bent ground's preview (green up, blue down), each stroke's ring, the brush under the mouse
    if (!edits.cur.sculpts.empty() || sculpting) {
        sculpt_preview();
        renderer.draw_tris(sculpt_prev_tris, false);
        std::vector<LineVert> sl = sculpt_prev_lines;
        auto ring = [&](V3 c, float r, uint32_t col) {
            for (int k = 0; k < 36; k++) {
                float a0 = 6.2831853f * k / 36, a1 = 6.2831853f * (k + 1) / 36;
                V3 p0 = c + V3(std::cos(a0) * r, std::sin(a0) * r, 0), p1 = c + V3(std::cos(a1) * r, std::sin(a1) * r, 0);
                p0.z += sculpt_dz(p0.x, p0.y) + 0.05f;
                p1.z += sculpt_dz(p1.x, p1.y) + 0.05f;
                sl.push_back({p0, col});
                sl.push_back({p1, col});
            }
        };
        for (auto& st : edits.cur.sculpts) ring(V3(st.x, st.y, st.z), st.r, st.h >= 0 ? rgba(0.4f, 1, 0.5f, 0.5f) : rgba(0.4f, 0.65f, 1, 0.5f));
        V3 at;
        if (sculpting && vp_hovered && mouse_hit(at)) {
            bool lower = ImGui::GetIO().KeyShift;
            ring(at, brush_r, lower ? rgba(0.4f, 0.7f, 1, 1) : rgba(1, 0.85f, 0.3f, 1));
            V3 top = at + V3(0, 0, sculpt_dz(at.x, at.y) + 0.05f);
            sl.push_back({top, rgba(1, 1, 1, 0.9f)});
            sl.push_back({top + V3(0, 0, lower ? -brush_h : brush_h), rgba(1, 1, 1, 0.9f)});
        }
        renderer.draw_lines(sl, false);
    }
    // the live match - you (green arrow + post), other players (blue), crowd NPCs (yellow posts), F9 marks (orange
    // diamonds), seen through everything; greyed when no data has come for 2 s
    if (live.running() && livef.received > 0) {
        std::vector<LineVert> ll;
        float a = live_fresh() ? 1.0f : 0.35f;
        auto post = [&](V3 p, float h, uint32_t c) { ll.push_back({p, c}); ll.push_back({p + V3(0, 0, h), c}); };
        auto ring = [&](V3 p, float r, uint32_t c) {
            for (int k = 0; k < 10; k++) {
                float a0 = 6.2831853f * k / 10, a1 = 6.2831853f * (k + 1) / 10;
                ll.push_back({p + V3(std::cos(a0), std::sin(a0), 0) * r, c});
                ll.push_back({p + V3(std::cos(a1), std::sin(a1), 0) * r, c});
            }
        };
        auto arrow = [&](V3 p, V3 f, uint32_t c) {
            f.z = 0;
            f = length(f) > 1e-4f ? normalize(f) : V3(1, 0, 0);
            V3 s = normalize(cross(f, V3(0, 0, 1))) * 0.5f, tip = p + f * 1.2f + V3(0, 0, 0.1f), b = p + V3(0, 0, 0.1f);
            ll.push_back({b - s, c}); ll.push_back({tip, c});
            ll.push_back({b + s, c}); ll.push_back({tip, c});
            ll.push_back({b - s, c}); ll.push_back({b + s, c});
        };
        if (live_show_npcs)
            for (auto& p : livef.npcs) { post(p, 1.75f, rgba(1, 0.85f, 0.2f, 0.85f * a)); ring(p + V3(0, 0, 0.05f), 0.3f, rgba(1, 0.85f, 0.2f, 0.85f * a)); }
        for (size_t k = 0; k < livef.players.size(); k++) {
            post(livef.players[k], 1.9f, rgba(0.35f, 0.6f, 1, a));
            arrow(livef.players[k], livef.players_fwd[k], rgba(0.35f, 0.6f, 1, a));
        }
        if (livef.has_me) {
            post(livef.me, 2.2f, rgba(0.3f, 1, 0.4f, a));
            ring(livef.me + V3(0, 0, 0.05f), 0.45f, rgba(0.3f, 1, 0.4f, a));
            arrow(livef.me, livef.me_fwd, rgba(0.3f, 1, 0.4f, a));
        }
        for (auto& p : livef.marks) {
            uint32_t c = rgba(1, 0.5f, 0.1f, a);
            V3 t = p + V3(0, 0, 2.6f);
            for (V3 d : {V3(0.35f, 0, 0), V3(0, 0.35f, 0), V3(-0.35f, 0, 0), V3(0, -0.35f, 0)}) {
                ll.push_back({t + V3(0, 0, 0.35f), c}); ll.push_back({t + d, c});
                ll.push_back({t - V3(0, 0, 0.35f), c}); ll.push_back({t + d, c});
            }
            post(p, 2.25f, c);
        }
        renderer.draw_lines(ll, true);
    }
    // areas: ground - the outline where the ground is (cyan) and where it goes (green); navmesh remake - blue;
    // no crowds - red, with its prism's top 3 m up; posts at the corners
    {
        std::vector<LineVert> rl;
        auto ring = [&](const std::vector<V3>& h, float dz, uint32_t col) {
            for (size_t i = 0; i < h.size(); i++) {
                V3 p = h[i], q = h[(i + 1) % h.size()];
                rl.push_back({V3(p.x, p.y, p.z + dz + 0.05f), col});
                rl.push_back({V3(q.x, q.y, q.z + dz + 0.05f), col});
            }
        };
        for (size_t k = 0; k < edits.cur.regions.size(); k++) {
            const RegionEdit& r = edits.cur.regions[k];
            if (r.removed) continue;
            bool s = (int)k == sel_region;
            std::vector<V3> h = hull2d(r.pts);
            if (r.kind == AREA_GROUND) {
                ring(h, 0, rgba(0.3f, 0.85f, 1.0f, s ? 1.0f : 0.55f));
                ring(h, r.dz, rgba(0.35f, 1.0f, 0.55f, s ? 1.0f : 0.6f));
            } else {
                uint32_t c = r.kind == AREA_NAV_BLOCK ? rgba(1, 0.3f, 0.3f, s ? 1.0f : 0.6f) : rgba(0.35f, 0.55f, 1, s ? 1.0f : 0.6f);
                ring(h, 0, c);
                if (r.kind == AREA_NAV_BLOCK) ring(h, 3.0f, c);
            }
            float top = r.kind == AREA_GROUND ? r.dz : r.kind == AREA_NAV_BLOCK ? 3.0f : 1.0f;
            for (auto& p : h) {
                rl.push_back({V3(p.x, p.y, p.z + 0.05f), rgba(0.6f, 0.9f, 0.8f, 0.8f)});
                rl.push_back({V3(p.x, p.y, p.z + top + 0.05f), rgba(0.6f, 0.9f, 0.8f, 0.8f)});
            }
        }
        if (drawing_region && !draft.empty()) {
            for (size_t i = 0; i < draft.size(); i++) {           // the corners, and the hull they make so far
                V3 p = draft[i];
                rl.push_back({p, rgba(1, 0.9f, 0.2f)});
                rl.push_back({p + V3(0, 0, 1.2f), rgba(1, 0.9f, 0.2f)});
            }
            if (draft.size() >= 2) ring(hull2d(draft), 0, rgba(1, 0.85f, 0.2f, 0.9f));
        }
        renderer.draw_lines(rl, true);
    }
    ms_draw();                                        // the shown mode's boundaries and zones
    // the last build's holes: a short post at each, seen through everything
    if (show_holes && !plug_tris.empty()) renderer.draw_tris(plug_tris, false);   // the plugged holes
    if (show_holes && (!holes_vis.empty() || !holes_col.empty())) {
        std::vector<LineVert> posts;
        for (auto& p : holes_vis) { posts.push_back({p, rgba(0.3f, 0.55f, 1)}); posts.push_back({p + V3(0, 0, 0.6f), rgba(0.3f, 0.55f, 1)}); }
        for (auto& p : holes_col) { posts.push_back({p, rgba(1, 0.2f, 0.2f)}); posts.push_back({p + V3(0, 0, 1.0f), rgba(1, 0.2f, 0.2f)}); }
        renderer.draw_lines(posts, true);
    }
    renderer.end();
}

void App::shortcuts() {
    if (chr.open) { chr_shortcuts(); return; }            // (the Characters workspace has its own keys)
    ImGuiIO& io = ImGui::GetIO();
    if (io.WantTextInput) return;
    bool ctrl = io.KeyCtrl;
    if (ctrl && ImGui::IsKeyPressed(ImGuiKey_O)) open_project();
    if (ImGui::IsKeyPressed(ImGuiKey_F1)) show_help = !show_help;
    if (walking) {                                    // (the walker has the keys)
        if (ImGui::IsKeyPressed(ImGuiKey_Escape) || ImGui::IsKeyPressed(ImGuiKey_V)) { walking = false; status = "Walk ended"; }
        return;
    }
    if (scene && !workspace && ImGui::IsKeyPressed(ImGuiKey_V) && !ImGui::IsMouseDown(ImGuiMouseButton_Right)) walk_enter(placement_point());
    if (ImGui::IsKeyPressed(ImGuiKey_F5) && scene && !workspace && !tg_state && !engine.busy()) test_in_game();
    if (!scene) return;
    if (scattering && ImGui::IsKeyPressed(ImGuiKey_Escape)) { start_scatter(); return; }
    if (!workspace && !ctrl && ImGui::IsKeyPressed(ImGuiKey_P)) { start_scatter(); return; }
    if (sculpting) {
        if (ImGui::IsKeyPressed(ImGuiKey_Escape)) { start_sculpt(); return; }
        if (ImGui::IsKeyPressed(ImGuiKey_Backspace) && !edits.cur.sculpts.empty()) { edits.cur.sculpts.pop_back(); edits.commit(); }
    }
    if (drawing_region) {
        if (ImGui::IsKeyPressed(ImGuiKey_Enter) || ImGui::IsKeyPressed(ImGuiKey_KeypadEnter)) finish_region();
        if (ImGui::IsKeyPressed(ImGuiKey_Escape)) { cancel_region(); return; }
        if (ImGui::IsKeyPressed(ImGuiKey_Backspace) && !draft.empty()) draft.pop_back();
    }
    if (ms_shortcuts()) return;                       // a boundary corner (Delete removes it, Esc lets go)
    if (!drawing_region && selection.empty() && region_ok(sel_region) && ImGui::IsKeyPressed(ImGuiKey_Delete)) {
        edits.cur.regions[sel_region].removed = true;
        edits.commit();
        status = "Ground area removed (Ctrl+Z brings it back)";
        sel_region = -1;
        return;
    }
    if (ctrl && ImGui::IsKeyPressed(ImGuiKey_S)) { if (workspace) save_workspace(false); else save_project(false); }
    if (ctrl && ImGui::IsKeyPressed(ImGuiKey_Z)) edits.undo();
    if (ctrl && ImGui::IsKeyPressed(ImGuiKey_Y)) edits.redo();
    if (ctrl && ImGui::IsKeyPressed(ImGuiKey_D)) copy_selected();
    if (ctrl && ImGui::IsKeyPressed(ImGuiKey_R)) put_back_selected();
    if (ctrl && ImGui::IsKeyPressed(ImGuiKey_B)) build();
    if (ImGui::IsMouseDown(ImGuiMouseButton_Right) || ctrl) return;      // (W A S D E Q fly the camera)
    if (ImGui::IsKeyPressed(ImGuiKey_W)) gizmo_op = ImGuizmo::TRANSLATE;
    if (ImGui::IsKeyPressed(ImGuiKey_E)) gizmo_op = all_axes ? ImGuizmo::ROTATE : ImGuizmo::ROTATE_Z;
    if (ImGui::IsKeyPressed(ImGuiKey_F)) frame_selection();
    if (ImGui::IsKeyPressed(ImGuiKey_Home)) frame_all();
    if (ImGui::IsKeyPressed(ImGuiKey_T)) cam.pitch = -1.5607f;
    if (ImGui::IsKeyPressed(ImGuiKey_C)) show_collision = !show_collision;
    if (ImGui::IsKeyPressed(ImGuiKey_B)) select_building();
    if (ImGui::IsKeyPressed(ImGuiKey_G)) select_attached(io.KeyShift);
    if (ImGui::IsKeyPressed(ImGuiKey_End)) drop_to_ground();
    if (ImGui::IsKeyPressed(ImGuiKey_L)) focus_library = true;
    if (ImGui::IsKeyPressed(ImGuiKey_Delete)) remove_selected();
    if (ImGui::IsKeyPressed(ImGuiKey_Escape)) { if (sel_route >= 0) sel_route = -1; else scope_up(); }
}

// ----------------------------------------------------------------------------------------------- self-test --

void App::test_tick() {
    if (test_step < 0) return;
    if (test_wait > 0) { test_wait--; return; }
    auto shot = [&](const char* name) { shot_path = test_dir + "\\" + name; tlog(std::string("screenshot ") + name); };
    if (test_step >= 1700 && test_step < 1900) { if (ms_test_tick()) return; }
    switch (test_step) {
    case 3000:                                          // the setup opens by itself on a release tree's first start
        if (!setup.open) {
            if (++test_frames > 120) { tlog("FAIL: the setup didn't open (not a release tree? forgelib\\forge.py)"); test_exit = 1; test_step = 99; }
            return;
        }
        if (++test_frames < 30) return;                 // (the panels and the window are drawn from the next frames on)
        tlog("ok: the first-run setup opened (game folder guessed: " + std::string(setup.game[0] ? setup.game : "-") + ")");
        for (auto& s : setup.sources)
            tlog("  other game " + s.game + ": " + (s.dir[0] ? std::string(s.dir) + (setup_mp_dir(s, s.dir).empty() ? " (no multiplayer folder)" : " (found)")
                                                            : std::string("not found")));
        shot("1_setup.png");
        test_wait = 6;
        test_step = 3001;
        return;
    case 3001:
        strncpy_s(setup.game, test_archive.c_str(), sizeof setup.game - 1);
        strncpy_s(setup.work, (test_dir + "\\work").c_str(), sizeof setup.work - 1);
        setup_save();
        if (setup.open) { tlog("FAIL: the setup didn't save: " + setup.error); test_exit = 1; test_step = 99; return; }
        {
            // config version 2 with the first version's keys beside it
            json c = read_config();
            std::string mp = c.is_object() && c.contains("installs") ? c["installs"].value("AC3", json::object()).value("mp_dir", std::string()) : "";
            bool v2 = c.is_object() && c.value("version", 0) == 2 && !mp.empty() && mp == c.value("game_dir", std::string());
            std::string games;
            if (c.is_object() && c.contains("installs"))
                for (auto& [g, v] : c["installs"].items()) games += " " + g;
            tlog(std::string(v2 ? "ok" : "FAIL") + ": config version 2 written (installs:" + games + "; game_dir kept for older readers)");
            if (!v2) test_exit = 1;
        }
        test_t0 = ImGui::GetTime();
        test_step = 3002;
        return;
    case 3002:
        if (setup.ingesting) {
            if (ImGui::GetTime() - test_t0 > 3600) { tlog("FAIL: reading the game never finished"); test_exit = 1; test_step = 99; }
            return;
        }
        if (engine.busy()) return;
        {
            bool ok = setup.ingest_msg.rfind("Ready", 0) == 0 && !maps.empty();
            tlog(std::string(ok ? "ok: " : "FAIL: ") + setup.ingest_msg + " - " + std::to_string(maps.size()) + " map(s) listed (" +
                 std::to_string((int)(ImGui::GetTime() - test_t0)) + " s)");
            if (!ok) test_exit = 1;
        }
        test_wait = 6;
        test_step = 3003;
        return;
    case 3003:
        shot("2_ready.png");
        test_wait = 3;
        test_step = 99;
        return;
    case 3100: {                                        // --maps-watch-test: a map added to the game folder while open
        if (maps.empty() || engine.busy()) return;
        json c = read_config();
        std::string game = c.is_object() ? c.value("game_dir", std::string()) : "";
        if (game.empty() || !file_exists(game + "\\studio_test_folder.txt")) {
            tlog("FAIL: the config's game folder isn't a test folder (it needs studio_test_folder.txt): " + game);
            test_exit = 1; test_step = 99; return;
        }
        test_watch_file = game + "\\DataPC_ZZ_WatchTest.forge";
        test_watch_n = (int)maps.size();
        lib_poll(true);
        DeleteFileW(widen(test_watch_file).c_str());
        if (!CreateHardLinkW(widen(test_watch_file).c_str(), widen(test_archive).c_str(), nullptr) &&
            !CopyFileW(widen(test_archive).c_str(), widen(test_watch_file).c_str(), FALSE)) {
            tlog("FAIL: couldn't add " + test_watch_file); test_exit = 1; test_step = 99; return;
        }
        tlog("ok: " + std::to_string(test_watch_n) + " map(s) listed; added " + test_watch_file + " while open");
        test_t0 = ImGui::GetTime();
        test_step = 3101;
        return;
    }
    case 3101: {
        bool listed = std::any_of(maps.begin(), maps.end(), [&](const MapEntry& m) { return m.path == test_watch_file ||
            m.path.size() >= 25 && m.path.compare(m.path.size() - 25, 25, "DataPC_ZZ_WatchTest.forge") == 0; });
        if (!listed) {
            if (ImGui::GetTime() - test_t0 > 30) { tlog("FAIL: the new map never appeared in the Maps list"); test_exit = 1; test_step = 3103; }
            return;
        }
        tlog("ok: the Maps list picked it up by itself in " + std::to_string((int)(ImGui::GetTime() - test_t0)) + " s (" +
             std::to_string(maps.size()) + " maps)");
        test_t0 = ImGui::GetTime();
        test_step = 3102;
        return;
    }
    case 3102: {
        lib_poll();
        bool indexed = false;
        for (auto& m : lib_maps) {
            std::string a = m.value("archive", std::string());
            if (a.size() >= 25 && a.compare(a.size() - 25, 25, "DataPC_ZZ_WatchTest.forge") == 0 && m["index"].is_string()) indexed = true;
        }
        if (!indexed) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: the Library never indexed the new map"); test_exit = 1; test_step = 3103; }
            return;
        }
        tlog("ok: the Library indexed it in the background in " + std::to_string((int)(ImGui::GetTime() - test_t0)) + " s");
        test_step = 3103;
        return;
    }
    case 3103:
        if (engine.busy()) return;
        DeleteFileW(widen(test_watch_file).c_str());
        test_t0 = ImGui::GetTime();
        test_step = 3104;
        return;
    case 3104: {
        if ((int)maps.size() != test_watch_n) {
            if (ImGui::GetTime() - test_t0 > 30) { tlog("FAIL: the removed map stayed in the Maps list"); test_exit = 1; test_step = 99; }
            return;
        }
        tlog("ok: removed again - the list is back to " + std::to_string(test_watch_n) + " map(s)");
        test_step = 99;
        return;
    }
    case 0:
        if (maps.empty() && engine.busy()) return;
        tlog("opening " + test_archive);
        open_map(test_archive);
        test_step = 1;
        return;
    case 1:
        if (load_state == 4 || (load_state == 0 && !scene)) { tlog("FAIL: the map didn't open: " + status); test_exit = 1; test_step = 99; return; }
        if (!scene || load_state != 0) return;
        tlog(status);
        test_wait = 10;
        test_step = 2;
        return;
    case 2:
        if (shots_mode) { test_step = 200; return; }
        if (ms1_test) { test_step = 1700; return; }
        if (ms2_test) { test_step = 1800; return; }
        if (region_test) { test_step = 300; return; }
        if (nav_test) { test_step = 400; return; }
        if (blend_test) { test_step = 500; return; }
        if (live_test) { test_step = 600; return; }
        if (climb_test) { test_step = 700; return; }
        if (sa_test) { test_step = 800; return; }
        if (lc_test) { test_step = 900; return; }
        if (gs_test) { test_step = 1000; return; }
        if (ui_test) { test_step = 1100; return; }
        if (tg_test) { test_step = 1200; return; }
        if (walk_test) { test_step = 1300; return; }
        if (sc_test) { test_step = 1400; return; }
        if (ba_test) { test_step = 1500; return; }
        if (spl_test) { test_step = 1600; return; }
        if (spa.test) { test_step = 2100; return; }
        if (chr.test) { test_step = 2200; return; }
        if (imp.test) { test_step = 2300; return; }
        shot("1_map.png");
        test_step = 20;
        return;
    case 400:
        // the navmesh shown and loaded
        show_nav = show_routes = true;
        load_nav();
        test_step = 401;
        return;
    case 401: {
        if (!navv.loaded) return;
        tlog("navmesh: " + std::to_string(navv.tris) + " triangles, " + std::to_string(navv.wps.size()) + " waypoints, " +
             std::to_string(navv.routes.size()) + " crowd routes");
        // a "no crowds" area: 10 m round the middle point of the longest route that is on; two route edits
        int longest = -1;
        float best = 0;
        for (size_t k = 0; k < navv.routes.size(); k++) {
            if (!navv.routes[k].active || navv.routes[k].pts.size() < 3) continue;
            float len = 0;
            for (size_t i = 0; i + 1 < navv.routes[k].pts.size(); i++) len += length(navv.routes[k].pts[i + 1] - navv.routes[k].pts[i]);
            if (len > best) { best = len; longest = (int)k; }
        }
        if (longest < 0) { tlog("FAIL: no crowd route to test on"); test_exit = 1; test_step = 99; return; }
        V3 c = navv.routes[longest].pts[navv.routes[longest].pts.size() / 2];
        start_region(AREA_NAV_BLOCK);
        for (V3 o : {V3(-5, -5, 0), V3(5, -5, 0), V3(5, 5, 0), V3(-5, 5, 0)}) {
            float t = 0;
            V3 from = c + o + V3(0, 0, 20);
            if (pick(from, V3(0, 0, -1), &t) >= 0) draft.push_back(from + V3(0, 0, -t));
            else draft.push_back(c + o);
        }
        finish_region();
        int off = -1, wide = -1;
        for (size_t k = 0; k < navv.routes.size() && (off < 0 || wide < 0); k++) {
            if ((int)k == longest || !navv.routes[k].active) continue;
            if (off < 0) off = (int)k; else wide = (int)k;
        }
        set_route(off, 0, -1);
        set_route(wide, -1, navv.routes[wide].width + 1.0f);
        edits.commit();
        sel_route = wide;
        tlog("no-crowds area round route " + navv.routes[longest].name + "; route " + navv.routes[off].name + " off; route " +
             navv.routes[wide].name + " widened to " + std::to_string(route_width(wide)).substr(0, 4) + " m");
        cam.pos = c + V3(-14, -18, 16);
        {
            V3 d = normalize(c - cam.pos);
            cam.yaw = std::atan2(d.y, d.x);
            cam.pitch = std::asin(d.z);
        }
        test_wait = 8;
        test_step = 402;
        return;
    }
    case 2100:
        // File > New map from a singleplayer area - New York's Wall Street (a known surface box), its preview and
        // street level, the conversion with spmaps' checks, the new map opened
        spa_open();
        test_t0 = ImGui::GetTime();
        test_step = 2101;
        return;
    case 2101: {
        if (spa.maps.empty()) {
            if (ImGui::GetTime() - test_t0 > 120) { tlog("FAIL: no SP maps: " + spa.error); test_exit = 1; test_step = 99; }
            return;
        }
        int k = -1;
        for (int i = 0; i < (int)spa.maps.size(); i++) if (spa.maps[i].value("title", std::string()) == "SP: New York") k = i;
        if (k < 0) { tlog("FAIL: no SP New York"); test_exit = 1; test_step = 99; return; }
        tlog("ok: " + std::to_string(spa.maps.size()) + " SP maps, " + std::to_string(spa.hosts.size()) + " host(s)");
        spa_choose(k);
        test_t0 = ImGui::GetTime();
        test_step = 2102;
        return;
    }
    case 2102:
        if (spa.ov.busy || engine.busy()) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: the overview never came"); test_exit = 1; test_step = 99; }
            return;
        }
        if (!spa.ov.srv) { tlog("FAIL: no overview: " + spa.error); test_exit = 1; test_step = 99; return; }
        tlog("ok: overview " + std::to_string(spa.ov.meta.value("w", 0)) + " x " + std::to_string(spa.ov.meta.value("h", 0)) + " px");
        spa.box[0] = -160; spa.box[1] = -128; spa.box[2] = -35; spa.box[3] = 0;
        spa.has_box = true;
        snprintf(spa.name, sizeof spa.name, "SPA1_test_wallstreet");
        spa_preview();
        test_t0 = ImGui::GetTime();
        test_step = 2103;
        return;
    case 2103: {
        if (spa.pv.busy || engine.busy()) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: the preview never came"); test_exit = 1; test_step = 99; }
            return;
        }
        if (!spa.pv.srv) { tlog("FAIL: no preview: " + spa.error); test_exit = 1; test_step = 99; return; }
        bool z_ok = std::fabs(spa.street_z - 4.5f) < 0.6f;
        tlog(std::string(z_ok ? "ok" : "FAIL") + ": preview drawn; street level " + std::to_string(spa.street_z).substr(0, 5) +
             " m (expected 4.5); open water " + std::to_string(spa.pv.meta.value("open_water", 0.0f)).substr(0, 5));
        if (!z_ok) test_exit = 1;
        test_wait = 10;
        test_step = 2104;
        return;
    }
    case 2104:
        shot("1_sp_area.png");
        test_wait = 3;
        test_step = 2105;
        return;
    case 2105:
        spa_build();
        test_t0 = ImGui::GetTime();
        test_step = 2106;
        return;
    case 2106: {
        if (spa.building) {
            if (ImGui::GetTime() - test_t0 > 3600) { tlog("FAIL: the conversion took over an hour"); test_exit = 1; test_step = 99; }
            return;
        }
        if (!spa.result.is_object()) { tlog("FAIL: the conversion failed: " + spa.error); test_exit = 1; test_step = 99; return; }
        tlog("converted in " + std::to_string(spa.result.value("seconds", 0)) + " s: " + spa.result.value("dest", std::string()));
        for (auto& c : spa.result.value("checks", json::array())) tlog("  " + c.get<std::string>());
        bool ok = spa.result.value("verified", false);
        tlog(std::string(ok ? "ok" : "FAIL") + ": spmaps' checks on the new map");
        if (!ok) test_exit = 1;
        open_map(spa.result.value("dest", std::string()));
        test_t0 = ImGui::GetTime();
        test_step = 2107;
        return;
    }
    case 2107:
        if (load_state == 4) { tlog("FAIL: the new map didn't open: " + status); test_exit = 1; test_step = 99; return; }
        if (!scene || load_state != 0 || scene->archive != spa.result.value("dest", std::string())) {
            if (ImGui::GetTime() - test_t0 > 600) { tlog("FAIL: the new map never opened"); test_exit = 1; test_step = 99; }
            return;
        }
        tlog("ok: opened in the Studio: " + status);
        spa.open = false;
        frame_all();
        test_wait = 12;
        test_step = 2108;
        return;
    case 2108:
        shot("2_opened.png");
        test_wait = 3;
        test_step = 99;
        return;
    case 2300:
        // File > Import a map from another game - the installs and recipes listed, one map imported (its recipe's
        // checks), the new map in the Maps list and opened
        imp_open();
        test_t0 = ImGui::GetTime();
        test_step = 2301;
        return;
    case 2301: {
        if (!imp.cat.is_object()) {
            if (!imp.error.empty() || ImGui::GetTime() - test_t0 > 300) { tlog("FAIL: no import catalogue: " + imp.error); test_exit = 1; test_step = 99; }
            return;
        }
        std::string want_game = imp.test_pick.substr(0, imp.test_pick.find(':')), want_key = imp.test_pick.substr(imp.test_pick.find(':') + 1);
        const json& gs = imp.cat["games"];
        imp.map = -1;
        for (int k = 0; k < (int)gs.size(); k++) {
            int avail = 0;
            for (auto& m : gs[k]["maps"]) avail += m.value("available", false) ? 1 : 0;
            tlog("  " + gs[k].value("game", std::string()) + ": " + (gs[k].value("found", false) ? "install found" : "no install") + ", " +
                 std::to_string(gs[k]["maps"].size()) + " map(s) with a recipe, " + std::to_string(avail) + " offered");
            if (gs[k].value("game", std::string()) != want_game) continue;
            for (int i = 0; i < (int)gs[k]["maps"].size(); i++)
                if (gs[k]["maps"][i].value("key", std::string()) == want_key) { imp.game = k; imp.map = i; imp.pick_tab = true; }
        }
        const json* m = imp_map();
        if (!m) { tlog("FAIL: no recipe " + imp.test_pick); test_exit = 1; test_step = 99; return; }
        if (!m->value("available", false)) { tlog("FAIL: " + imp.test_pick + " not offered: " + m->value("reason", std::string())); test_exit = 1; test_step = 99; return; }
        tlog("ok: import catalogue read; importing " + m->value("title", std::string()) + " (" + m->value("source", std::string()) + ")");
        snprintf(imp.name, sizeof imp.name, "IMPORT_test_%s", want_key.c_str());
        test_wait = 12;
        test_step = 2302;
        return;
    }
    case 2302:
        shot("1_import.png");
        test_wait = 3;
        test_step = 2303;
        return;
    case 2303:
        imp_build();
        test_t0 = ImGui::GetTime();
        test_step = 2304;
        return;
    case 2304: {
        if (imp.building) {
            if (ImGui::GetTime() - test_t0 > 3 * 3600) { tlog("FAIL: the import took over three hours"); test_exit = 1; test_step = 99; }
            return;
        }
        if (!imp.result.is_object()) { tlog("FAIL: the import failed: " + imp.error); test_exit = 1; test_step = 99; return; }
        std::string dest = imp.result.value("dest", std::string());
        tlog("imported in " + std::to_string(imp.result.value("seconds", 0)) + " s: " + dest);
        for (auto& c : imp.result.value("checks", json::array())) tlog("  " + c.get<std::string>());
        bool ok = imp.result.value("verified", false) && file_exists(dest);
        tlog(std::string(ok ? "ok" : "FAIL") + ": the recipe's checks on the new map");
        if (!ok) test_exit = 1;
        test_t0 = ImGui::GetTime();
        test_step = 2305;
        return;
    }
    case 2305: {
        std::string dest = imp.result.value("dest", std::string());
        auto it = std::find_if(maps.begin(), maps.end(), [&](const MapEntry& m) { return m.path == dest; });
        if (it == maps.end()) {
            if (ImGui::GetTime() - test_t0 > 60) { tlog("FAIL: the new map isn't in the Maps list"); test_exit = 1; test_step = 99; }
            return;
        }
        tlog(std::string(it->kind == "import" ? "ok" : "FAIL") + ": in the Maps list as \"" + it->title + "\" (" + it->source + ")");
        if (it->kind != "import") test_exit = 1;
        open_map(dest);
        test_t0 = ImGui::GetTime();
        test_step = 2306;
        return;
    }
    case 2306:
        if (load_state == 4) { tlog("FAIL: the new map didn't open: " + status); test_exit = 1; test_step = 99; return; }
        if (!scene || load_state != 0 || scene->archive != imp.result.value("dest", std::string())) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: the new map never opened"); test_exit = 1; test_step = 99; }
            return;
        }
        tlog("ok: opened in the Studio: " + status + " - " + std::to_string(scene->entities.size()) + " objects");
        if (scene->entities.size() < 100) { tlog("FAIL: too few objects for a whole map"); test_exit = 1; }
        imp.open = false;
        frame_all();
        test_wait = 12;
        test_step = 2307;
        return;
    case 2307:
        shot("2_opened.png");
        test_wait = 3;
        test_step = 2310;
        return;
    case 2310: {
        // the same map asked for again: the dialog shows the import, the request opens it and converts nothing
        auto stamp = [](const std::string& p) {
            WIN32_FILE_ATTRIBUTE_DATA a{};
            if (!GetFileAttributesExW(widen(p).c_str(), GetFileExInfoStandard, &a)) return (uint64_t)0;
            return ((uint64_t)a.ftLastWriteTime.dwHighDateTime << 32) | a.ftLastWriteTime.dwLowDateTime;
        };
        imp.test_dest = imp.result.value("dest", std::string());
        imp.test_stamp = stamp(imp.test_dest.substr(0, imp.test_dest.find_last_of("\\/")) + "\\import.json");
        if (!imp.test_stamp) { tlog("FAIL: no import.json beside " + imp.test_dest); test_exit = 1; test_step = 99; return; }
        open_map(test_archive);                         // (away from it, so opening it again shows)
        imp.result = json();
        imp_refresh();                                  // (keeps the map picked; its answer replaces the catalogue)
        imp.cat = json();
        imp.open = true;
        test_t0 = ImGui::GetTime();
        test_step = 2311;
        return;
    }
    case 2311: {
        if (!imp.cat.is_object() || !scene || load_state != 0 || scene->archive == imp.test_dest) {
            if (ImGui::GetTime() - test_t0 > 300) { tlog("FAIL: the catalogue / the other map never came"); test_exit = 1; test_step = 99; }
            return;
        }
        json ex = imp_existing();
        bool ok = ex.is_object() && ex.value("dest", std::string()) == imp.test_dest && !ex.value("source_changed", true);
        tlog(std::string(ok ? "ok" : "FAIL") + ": the dialog shows the earlier import: " +
             (ex.is_object() ? "Imported " + ex.value("imported", std::string()) + ", " + std::to_string(ex.value("seconds", 0)) + " s" : std::string("none")));
        if (!ok) { test_exit = 1; test_step = 99; return; }
        test_wait = 12;
        test_step = 2312;
        return;
    }
    case 2312:
        shot("3_already_imported.png");
        imp_build();                                    // (a second request, not "again")
        test_t0 = ImGui::GetTime();
        test_step = 2313;
        return;
    case 2313: {
        if (imp.building) {
            if (ImGui::GetTime() - test_t0 > 120) { tlog("FAIL: a second request for an imported map is converting it again"); test_exit = 1; test_step = 99; }
            return;
        }
        WIN32_FILE_ATTRIBUTE_DATA a{};
        std::string res = imp.test_dest.substr(0, imp.test_dest.find_last_of("\\/")) + "\\import.json";
        uint64_t now = GetFileAttributesExW(widen(res).c_str(), GetFileExInfoStandard, &a)
                           ? ((uint64_t)a.ftLastWriteTime.dwHighDateTime << 32) | a.ftLastWriteTime.dwLowDateTime : 0;
        bool ok = imp.result.is_object() && imp.result.value("reused", false) && imp.result.value("dest", std::string()) == imp.test_dest &&
                  now == imp.test_stamp;
        tlog(std::string(ok ? "ok" : "FAIL") + ": a second request opened the earlier import and ran no recipe (" +
             std::to_string((int)(ImGui::GetTime() - test_t0)) + " s; import.json " + (now == imp.test_stamp ? "untouched" : "rewritten") + ")");
        if (!ok) { test_exit = 1; test_step = 99; return; }
        test_t0 = ImGui::GetTime();
        test_step = 2314;
        return;
    }
    case 2314:
        if (load_state == 4) { tlog("FAIL: the earlier import didn't open: " + status); test_exit = 1; test_step = 99; return; }
        if (!scene || load_state != 0 || scene->archive != imp.test_dest) {
            if (ImGui::GetTime() - test_t0 > 600) { tlog("FAIL: the earlier import never opened"); test_exit = 1; test_step = 99; }
            return;
        }
        tlog("ok: the earlier import opened: " + status);
        imp.open = false;
        test_wait = 12;
        test_step = 2315;
        return;
    case 2315:
        shot("4_reopened.png");
        test_wait = 3;
        test_step = 99;
        return;
    case 2200:
        // the viewer opened, the personas listed, four drawn (AC3's own, a port, a costume, AC4's)
        chr_open();
        test_t0 = ImGui::GetTime();
        test_step = 2201;
        return;
    case 2201:
        if (chr.personas.empty()) {
            if (ImGui::GetTime() - test_t0 > 300) { tlog("FAIL: no personas: " + chr.error); test_exit = 1; test_step = 99; }
            return;
        }
        tlog("ok: " + std::to_string(chr.personas.size()) + " personas listed");
        chr.test_n = 0;
        test_step = 2202;
        return;
    case 2202: {
        static const int IDS[] = {79, 75, 101, 164};
        if (chr.test_n >= 4) { test_step = 2210; return; }
        chr_load(IDS[chr.test_n]);
        test_t0 = ImGui::GetTime();
        test_step = 2203;
        return;
    }
    case 2203: {
        static const int IDS[] = {79, 75, 101, 164};
        if (chr.loading) {
            if (ImGui::GetTime() - test_t0 > 300) { tlog("FAIL: persona " + std::to_string(IDS[chr.test_n]) + " never came"); test_exit = 1; test_step = 99; }
            return;
        }
        bool ok = chr.loaded_id == IDS[chr.test_n] && chr.scene && chr.info.value("triangles", 0) > 1000;
        tlog(std::string(ok ? "ok" : "FAIL") + ": " + std::to_string(IDS[chr.test_n]) + " " + persona_name(IDS[chr.test_n]) + ": " +
             std::to_string(chr.info.value("part_names", json::array()).size()) + " part(s), " + std::to_string(chr.info.value("triangles", 0)) +
             " triangles, " + std::to_string(chr.info.value("textures", 0)) + " texture(s); weapon " +
             (chr.info["weapon"].is_object() ? chr.info["weapon"].value("file", std::string()) : std::string("none")) + (chr.error.empty() ? "" : "; " + chr.error));
        if (!ok) test_exit = 1;
        test_wait = 12;
        test_step = 2204;
        return;
    }
    case 2204:
        if (chr.loaded_id == 75 && chr.pick == CharUI::PICK_PERSONA) {
            // a click in the view's middle picks the part under it (the Inspector shows that part, the view outlines it)
            int k = chr_pick_ray(chr.vw * 0.5f, chr.vh * 0.4f);
            chr_pick_entity(k);
            auto names = chr.info.value("part_names", json::array()), slots = chr.info.value("part_slots", json::array());
            std::string what = k >= 0 && k < (int)names.size() ? slots[k].get<std::string>() + " " + names[k].get<std::string>() : "nothing";
            bool ok = k >= 0 && chr.pick == CharUI::PICK_PART && slots[k].get<std::string>() != "Weapon";
            tlog(std::string(ok ? "ok" : "FAIL") + ": a click in the view's middle picked " + what);
            if (!ok) test_exit = 1;
            test_wait = 10;
            return;
        }
        shot(("char_" + std::to_string(chr.loaded_id) + ".png").c_str());
        chr.test_n++;
        test_wait = 3;
        test_step = 2202;
        return;
    case 2210:
        // Lady Maverick - her forearms (the Weather table's skin), scheme 2, then her second costume
        chr_pick(CharUI::PICK_PERSONA);
        chr_load(75, {{"Colours", 1}});
        test_t0 = ImGui::GetTime();
        test_step = 2211;
        return;
    case 2211: {
        if (chr.loading || chr.colour_dirty > 0) return;
        bool skin = false;
        for (auto& n : chr.info.value("part_names", json::array())) skin |= n.get<std::string>() == "AC3MP_ID75_Skin";
        int tinted = (int)chr.info.value("tinted", json::array()).size();
        bool ok = skin && tinted >= 2 && chr.info["rows"].value("Colours", -1) == 1;
        tlog(std::string(ok ? "ok" : "FAIL") + ": 75 scheme 2: her skin part drawn (" + (skin ? "yes" : "no") + "), " +
             std::to_string(tinted) + " garment texture(s) tinted");
        if (!ok) test_exit = 1;
        test_wait = 12;
        test_step = 2212;
        return;
    }
    case 2212:
        shot("char_75_scheme2.png");
        chr_load(75, {{"Costume", 2}});
        test_step = 2213;
        return;
    case 2213: {
        if (chr.loading || chr.colour_dirty > 0) return;
        bool style = false, costume = false;
        auto slots = chr.info.value("part_slots", json::array());
        for (auto& sl : slots) { style |= sl.get<std::string>() == "Main"; costume |= sl.get<std::string>() == "Costume"; }
        bool ok = costume && !style;
        tlog(std::string(ok ? "ok" : "FAIL") + ": 75 costume 2: costume parts drawn, the style left out (" +
             std::to_string(slots.size()) + " parts)");
        if (!ok) test_exit = 1;
        test_wait = 12;
        test_step = 2214;
        return;
    }
    case 2214:
        shot("char_75_costume2.png");
        chr_load(75);
        test_step = 2215;
        return;
    case 2215:
        if (chr.loading || chr.colour_dirty > 0) return;
        chr_load_anim();
        test_t0 = ImGui::GetTime();
        test_step = 2216;
        return;
    case 2216: {
        if (chr.anim_loading) {
            if (ImGui::GetTime() - test_t0 > 300) { tlog("FAIL: the clip never came"); test_exit = 1; test_step = 99; }
            return;
        }
        bool ok = chr.animated && chr.nframes > 10 && chr.nbones > 70 && chr.anim.value("missing_bones", 1) == 0 && !chr.skin.empty();
        tlog(std::string(ok ? "ok" : "FAIL") + ": 75 plays " + chr.clip + ": " + std::to_string(chr.nframes) + " frames, " +
             std::to_string(chr.nbones) + " bones, " + std::to_string(chr.skin.size()) + " skinned part(s)" + (chr.error.empty() ? "" : "; " + chr.error));
        if (!ok) test_exit = 1;
        chr.playing = false;
        chr.t = 0;
        test_wait = 12;
        test_step = 2217;
        return;
    }
    case 2217:
        shot("char_75_idle_f0.png");
        chr.t = (chr.nframes / 2) / chr.fps;
        test_wait = 6;
        test_step = 2218;
        return;
    case 2218:
        shot("char_75_idle_mid.png");
        // two garment colours of scheme 1 edited and previewed, built into a test DataPC, listed, deleted
        chr.colours = json::parse(R"({"0": {"1": [0.1, 0.3, 0.9], "3": [0.9, 0.8, 0.1]}})");
        chr_pick(CharUI::PICK_COLOURS);
        chr_load(75, {{"Colours", 0}});
        test_t0 = ImGui::GetTime();
        test_step = 2219;
        return;
    case 2219: {
        if (chr.loading || chr.colour_dirty > 0) return;
        auto ce = chr.info.value("colour_edit", json());
        bool got = false;
        for (auto& c : ce.value("cells", json::array()))
            if (c.value("region", -1) == 1) got = std::fabs(c["rgb"][2].get<float>() - 0.9f) < 0.01f && std::fabs(c["rgb"][0].get<float>() - 0.1f) < 0.01f;
        int tinted = (int)chr.info.value("tinted", json::array()).size();
        bool ok = got && tinted >= 2 && ce.value("columns", json::array()).size() >= 9;
        tlog(std::string(ok ? "ok" : "FAIL") + ": 75's scheme 1 edited (main blue, secondary yellow): preview read " +
             (got ? "the edit" : "NOT the edit") + ", " + std::to_string(tinted) + " texture(s) tinted, " +
             std::to_string(ce.value("columns", json::array()).size()) + " column(s)");
        if (!ok) test_exit = 1;
        chr.animated = false;
        {
            std::string err;
            renderer.release_scene(SLOT_CHAR);
            renderer.upload(*chr.scene, err, nullptr, SLOT_CHAR);
        }
        chr.yaw = -1.9f;
        // with these changes not built, asking for another persona asks first (nothing is read)
        chr_ask_load(79);
        bool asked = chr.ask_switch == 79 && chr.loaded_id == 75 && !chr.loading && chr_unbuilt();
        tlog(std::string(asked ? "ok" : "FAIL") + ": " + std::to_string(chr_edit_count()) + " unbuilt change(s): opening 79 asks first (75 kept)");
        if (!asked) test_exit = 1;
        test_wait = 12;
        test_step = 2270;
        return;
    }
    case 2270:
        shot("char_ux1_ask_switch.png");
        chr.ask_switch = -1;                            // (Cancel)
        test_wait = 6;
        test_step = 2220;
        return;
    case 2220:
        shot("char_75_ce1_colours.png");
        engine.request("char_delete", {{"name", "CE1_selftest"}}, [this](bool, const json&, const std::string&) { test_step = 2221; });
        test_step = 2299;                               // (waiting for the delete: there may be none)
        return;
    case 2221:
        strncpy_s(chr.build_name, "CE1_selftest", sizeof chr.build_name - 1);
        chr.bottom_req = 1;
        chr_build();
        test_t0 = ImGui::GetTime();
        test_step = 2222;
        return;
    case 2222: {
        if (chr.building) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: the build never finished"); test_exit = 1; test_step = 99; }
            return;
        }
        bool ok = chr.build_msg.rfind("Built ", 0) == 0;
        tlog(std::string(ok ? "ok" : "FAIL") + ": " + chr.build_msg + " (" + std::to_string((int)(ImGui::GetTime() - test_t0)) + " s)");
        if (!ok) test_exit = 1;
        test_wait = 30;
        test_step = 2223;
        return;
    }
    case 2223: {
        bool listed = false;
        for (auto& b : chr.builds) if (b.value("name", std::string()) == "CE1_selftest" && b.value("exists", false)) listed = true;
        tlog(std::string(listed ? "ok" : "FAIL") + ": CE1_selftest listed among " + std::to_string(chr.builds.size()) + " build(s)");
        if (!listed) test_exit = 1;
        bool built = !chr_unbuilt() && chr.built_name == "CE1_selftest";
        tlog(std::string(built ? "ok" : "FAIL") + ": the changes are marked as in " + chr.built_name);
        if (!built) test_exit = 1;
        // Test in game, a dry run (nothing installed; a stand-in process watched): no second build
        tg_dry = true;
        tg_proc = L"PING.EXE";
        chr_test_in_game();
        bool straight = tg_chr && tg_state >= 2 && !chr.building;
        tlog(std::string(straight ? "ok" : "FAIL") + ": Test in game went straight to the install (state " + std::to_string(tg_state) + ")");
        if (!straight) { test_exit = 1; test_step = 99; return; }
        test_t0 = ImGui::GetTime();
        test_step = 2271;
        return;
    }
    case 2271:
        if (tg_state != 0) {
            if (ImGui::GetTime() - test_t0 > 120) { tlog("FAIL: Test in game stuck in state " + std::to_string(tg_state)); test_exit = 1; test_step = 99; }
            return;
        }
        tlog(std::string(tg_restored ? "ok" : "FAIL") + ": Test in game watched the stand-in to its end: " + status);
        if (!tg_restored) test_exit = 1;
        tg_dry = false;
        tg_proc = L"AC3MP.exe";
        tg_chr = false;
        chr.bottom_req = 1;
        test_wait = 6;
        test_step = 2224;
        return;
    case 2224:
        shot("char_ce1_builds.png");
        engine.request("char_delete", {{"name", "CE1_selftest"}}, [this](bool ok, const json&, const std::string& e) {
            tlog(std::string(ok ? "ok" : "FAIL") + ": CE1_selftest deleted" + (ok ? "" : ": " + e));
            if (!ok) test_exit = 1;
            test_step = 2225;
        });
        test_step = 2299;
        return;
    case 2225: {
        // Undo all clears the two colours, Ctrl+Z brings them back, Ctrl+Y clears them again
        auto ncol = [&]() { int n = 0; for (auto& [_s, r] : chr.colours.items()) n += (int)r.size(); return n; };
        chr.undo.clear();
        chr.redo.clear();
        int n0 = ncol();
        chr_undo_all();
        int n1 = ncol();
        chr_undo();
        int n2 = ncol();
        chr_redo();
        int n3 = ncol();
        bool ok = n0 == 2 && n1 == 0 && n2 == 2 && n3 == 0;
        tlog(std::string(ok ? "ok" : "FAIL") + ": undo all / undo / redo: " + std::to_string(n0) + " -> " + std::to_string(n1) + " -> " +
             std::to_string(n2) + " -> " + std::to_string(n3) + " colour(s)");
        if (!ok) test_exit = 1;
        chr.colour_dirty = 0;
    }
        chr.colours = json::object();
        chr_pick(CharUI::PICK_PART, "AC3MP_ID75_MainPart_V1");
        chr_load(75);
        test_step = 2226;
        return;
    case 2226:
        if (chr.loading || chr.colour_dirty > 0) return;
        chr_export("AC3MP_ID75_MainPart_V1", false);
        test_t0 = ImGui::GetTime();
        test_step = 2227;
        return;
    case 2227: {
        if (!chr.mesh_busy.empty()) {
            if (ImGui::GetTime() - test_t0 > 300) { tlog("FAIL: the export never finished"); test_exit = 1; test_step = 99; }
            return;
        }
        auto ex = chr.exports.value("AC3MP_ID75_MainPart_V1", json());
        bool ok = ex.is_object() && ex.value("vertices", 0) > 1000 && ex.value("bones", 0) > 100;
        tlog(std::string(ok ? "ok" : "FAIL") + ": AC3MP_ID75_MainPart_V1 exported: " + (ex.is_object() ? ex.value("glb", std::string()) : chr.build_msg) +
             " (" + std::to_string(ex.is_object() ? ex.value("vertices", 0) : 0) + " vertices, " + std::to_string(ex.is_object() ? ex.value("bones", 0) : 0) + " bones)");
        if (!ok) { test_exit = 1; test_step = 99; return; }
        // the export itself as the edit: the import path end to end (a real Blender edit is checked by hand)
        chr_pick(CharUI::PICK_PART, "AC3MP_ID75_MainPart_V1");
        chr_use_mesh("AC3MP_ID75_MainPart_V1", ex.value("glb", std::string()));
        test_step = 2228;
        return;
    }
    case 2228: {
        if (chr.loading || chr.colour_dirty > 0) return;
        bool used = chr.info.value("meshes", json::object()).contains("AC3MP_ID75_MainPart_V1");
        auto probs = chr.info.value("mesh_problems", json::array());
        bool ok = used && probs.empty() && chr.info.value("triangles", 0) == 12457;
        tlog(std::string(ok ? "ok" : "FAIL") + ": 75 drawn with the imported main part: " + std::to_string(chr.info.value("triangles", 0)) +
             " triangles" + (probs.empty() ? "" : ", " + probs[0].get<std::string>()));
        if (!ok) test_exit = 1;
        chr_pick(CharUI::PICK_PART, "AC3MP_ID75_MainPart_V1");
        test_wait = 10;
        test_step = 2229;
        return;
    }
    case 2229:
        shot("char_75_ce2_parts.png");
        chr_load_anim();
        test_t0 = ImGui::GetTime();
        test_step = 2230;
        return;
    case 2230: {
        if (chr.anim_loading) {
            if (ImGui::GetTime() - test_t0 > 300) { tlog("FAIL: the clip never came"); test_exit = 1; test_step = 99; }
            return;
        }
        bool ok = chr.animated && !chr.skin.empty() && chr.anim.value("missing_bones", 1) == 0;
        tlog(std::string(ok ? "ok" : "FAIL") + ": 75 plays its idle with the imported part (" + std::to_string(chr.skin.size()) + " skinned parts)");
        if (!ok) test_exit = 1;
        chr.playing = false;
        chr.t = (chr.nframes / 2) / chr.fps;
        test_wait = 10;
        test_step = 2231;
        return;
    }
    case 2231:
        shot("char_75_ce2_idle.png");
        engine.request("char_delete", {{"name", "CE2_selftest"}}, [this](bool, const json&, const std::string&) { test_step = 2232; });
        test_step = 2299;
        return;
    case 2232:
        strncpy_s(chr.build_name, "CE2_selftest", sizeof chr.build_name - 1);
        chr_build();
        test_t0 = ImGui::GetTime();
        test_step = 2233;
        return;
    case 2233: {
        if (chr.building) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: the build never finished"); test_exit = 1; test_step = 99; }
            return;
        }
        bool ok = chr.build_msg.rfind("Built ", 0) == 0;
        tlog(std::string(ok ? "ok" : "FAIL") + ": " + chr.build_msg + " (" + std::to_string((int)(ImGui::GetTime() - test_t0)) + " s)");
        if (!ok) test_exit = 1;
        chr.bottom_req = 1;
        test_wait = 30;
        test_step = 2234;
        return;
    }
    case 2234: {
        bool listed = false;
        for (auto& b : chr.builds)
            if (b.value("name", std::string()) == "CE2_selftest" && b.value("meshes", json::object()).size() == 1) listed = true;
        tlog(std::string(listed ? "ok" : "FAIL") + ": CE2_selftest listed with its mesh");
        if (!listed) test_exit = 1;
        chr.bottom_req = 1;
        test_wait = 6;
        test_step = 2235;
        return;
    }
    case 2235:
        shot("char_ce2_builds.png");
        engine.request("char_delete", {{"name", "CE2_selftest"}}, [this](bool ok, const json&, const std::string& e) {
            tlog(std::string(ok ? "ok" : "FAIL") + ": CE2_selftest deleted" + (ok ? "" : ": " + e));
            if (!ok) test_exit = 1;
            test_step = 2236;
        });
        test_step = 2299;
        return;
    case 2236:
        chr.meshes = json::object();
        // Lady Maverick's default item drawn with the Coyote Man's class-1 weapon, previewed, built, deleted
        chr_pick(CharUI::PICK_WEAPON);
        chr_weapons_refresh();
        test_t0 = ImGui::GetTime();
        test_step = 2237;
        return;
    case 2237: {
        if (chr.weapons_loading) {
            if (ImGui::GetTime() - test_t0 > 300) { tlog("FAIL: the weapons never came"); test_exit = 1; test_step = 99; }
            return;
        }
        auto items = chr.weapon_info.value("items", json::array());
        auto c1 = chr.weapon_info.value("candidates", json::object()).value("1", json::array());
        bool ok = items.size() >= 6 && c1.size() > 20 && chr.weapon_info.value("default", 0) == 750101;
        tlog(std::string(ok ? "ok" : "FAIL") + ": 75's weapons: " + std::to_string(items.size()) + " item(s), " + std::to_string(c1.size()) +
             " class-1 model(s)");
        if (!ok) test_exit = 1;
        chr.weapons = {{"750101", "AC3MP_WB870101"}};
        chr_pick(CharUI::PICK_WEAPON);
        chr_draw_item(750101);
        test_step = 2238;
        return;
    }
    case 2238: {
        if (chr.loading || chr.colour_dirty > 0) return;
        auto w = chr.info.value("weapon", json());
        bool ok = w.is_object() && w.value("file", std::string()) == "AC3MP_WB870101" && w.value("edited", false) && w.value("triangles", 0) > 0;
        tlog(std::string(ok ? "ok" : "FAIL") + ": item 750101 drawn as " + (w.is_object() ? w.value("file", std::string()) : "-") +
             " (" + std::to_string(w.is_object() ? w.value("triangles", 0) : 0) + " triangles)");
        if (!ok) test_exit = 1;
        chr_pick(CharUI::PICK_WEAPON);
        chr.weapon_only = true;
        chr_weapon_only();
        chr.dist = 1.2f;
        test_wait = 10;
        test_step = 2239;
        return;
    }
    case 2239:
        shot("char_75_cw1_weapons.png");
        engine.request("char_delete", {{"name", "CW1_selftest"}}, [this](bool, const json&, const std::string&) { test_step = 2240; });
        test_step = 2299;
        return;
    case 2240:
        strncpy_s(chr.build_name, "CW1_selftest", sizeof chr.build_name - 1);
        chr_build();
        test_t0 = ImGui::GetTime();
        test_step = 2241;
        return;
    case 2241: {
        if (chr.building) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: the build never finished"); test_exit = 1; test_step = 99; }
            return;
        }
        bool ok = chr.build_msg.rfind("Built ", 0) == 0;
        tlog(std::string(ok ? "ok" : "FAIL") + ": " + chr.build_msg + " (" + std::to_string((int)(ImGui::GetTime() - test_t0)) + " s)");
        if (!ok) test_exit = 1;
        test_wait = 30;
        test_step = 2242;
        return;
    }
    case 2242: {
        bool listed = false;
        for (auto& b : chr.builds)
            if (b.value("name", std::string()) == "CW1_selftest" && b.value("weapons", json::object()).size() == 1) listed = true;
        tlog(std::string(listed ? "ok" : "FAIL") + ": CW1_selftest listed with its weapon");
        if (!listed) test_exit = 1;
        engine.request("char_delete", {{"name", "CW1_selftest"}}, [this](bool ok, const json&, const std::string& e) {
            tlog(std::string(ok ? "ok" : "FAIL") + ": CW1_selftest deleted" + (ok ? "" : ": " + e));
            if (!ok) test_exit = 1;
            test_step = 2243;
        });
        test_step = 2299;
        return;
    }
    case 2243:
        chr.weapons = json::object();
        chr.weapon_only = false;
        chr.dist = 3.2f;
        // the main part's material - its values listed and named, an edit previewed, built, deleted
        chr_load(75);
        test_step = 2244;
        return;
    case 2244:
        if (chr.loading || chr.colour_dirty > 0) return;
        chr_mat_open("AC3MP_ID75_MainPart_V1");
        test_t0 = ImGui::GetTime();
        test_step = 2245;
        return;
    case 2245: {
        if (!chr.mat_list.is_array() || chr.mat_busy || !chr.mat_img.srv) {
            if (ImGui::GetTime() - test_t0 > 300) { tlog("FAIL: the materials / preview never came: " + chr.mat_error); test_exit = 1; test_step = 99; }
            return;
        }
        int props = 0, unnamed = 0;
        for (auto& p : chr.mat_list[0].value("props", json::array())) {
            props++;
            if (p.value("name", std::string()).rfind("0x", 0) == 0) unnamed++;
        }
        bool ok = props >= 15 && unnamed == 0;
        tlog(std::string(ok ? "ok" : "FAIL") + ": " + chr.mat_list[0].value("name", std::string()) + ": " + std::to_string(props) +
             " values, " + std::to_string(unnamed) + " unnamed; stock preview drawn");
        if (!ok) test_exit = 1;
        // shinier clothes, a gold sheen, a stronger rim
        chr.materials = json::parse(R"({"AC3MP_ID75_MainPart_V1": {"8CC8ED21": {"02042667": 4.0, "DF996150": 30.0, "199DF5E9": [1.0, 0.8, 0.4], "B3DEB19A": 2.0}}})");
        chr.mat_dirty = ImGui::GetTime() - 1;
        chr.pick = CharUI::PICK_MATERIAL;
        test_t0 = ImGui::GetTime();
        test_step = 2246;
        return;
    }
    case 2246:
        if (chr.mat_dirty > 0 || chr.mat_busy) {
            if (ImGui::GetTime() - test_t0 > 120) { tlog("FAIL: the edited preview never came"); test_exit = 1; test_step = 99; }
            return;
        }
        tlog(std::string(chr.mat_error.empty() ? "ok" : "FAIL") + ": the edited preview drawn (" +
             std::to_string(chr.mat_img.meta.is_object() ? chr.mat_img.meta.value("seconds", 0.0) : 0.0).substr(0, 4) + " s)" + chr.mat_error);
        if (!chr.mat_error.empty()) test_exit = 1;
        chr.pick = CharUI::PICK_MATERIAL;
        test_wait = 10;
        test_step = 2247;
        return;
    case 2247:
        shot("char_75_cm1_material.png");
        engine.request("char_delete", {{"name", "CM1_selftest"}}, [this](bool, const json&, const std::string&) { test_step = 2248; });
        test_step = 2299;
        return;
    case 2248:
        strncpy_s(chr.build_name, "CM1_selftest", sizeof chr.build_name - 1);
        chr_build();
        test_t0 = ImGui::GetTime();
        test_step = 2249;
        return;
    case 2249: {
        if (chr.building) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: the build never finished"); test_exit = 1; test_step = 99; }
            return;
        }
        bool ok = chr.build_msg.rfind("Built ", 0) == 0;
        tlog(std::string(ok ? "ok" : "FAIL") + ": " + chr.build_msg + " (" + std::to_string((int)(ImGui::GetTime() - test_t0)) + " s)");
        if (!ok) test_exit = 1;
        test_wait = 30;
        test_step = 2250;
        return;
    }
    case 2250: {
        bool listed = false;
        for (auto& b : chr.builds)
            if (b.value("name", std::string()) == "CM1_selftest" && b.value("materials", json::object()).size() == 1) listed = true;
        tlog(std::string(listed ? "ok" : "FAIL") + ": CM1_selftest listed with its material values");
        if (!listed) test_exit = 1;
        engine.request("char_delete", {{"name", "CM1_selftest"}}, [this](bool ok, const json&, const std::string& e) {
            tlog(std::string(ok ? "ok" : "FAIL") + ": CM1_selftest deleted" + (ok ? "" : ": " + e));
            if (!ok) test_exit = 1;
            test_step = 2251;
        });
        test_step = 2299;
        return;
    }
    case 2251:
        chr.materials = json::object();
        // the weapon in the hand, posed by the class's weapon-selection clip; then in the holster
        chr_pick(CharUI::PICK_WEAPON);
        chr_load(75);
        test_step = 2252;
        return;
    case 2252:
        if (chr.loading || chr.anim_loading || chr.colour_dirty > 0) return;
        chr_load_anim("@weapon");
        test_t0 = ImGui::GetTime();
        test_step = 2253;
        return;
    case 2253: {
        if (chr.anim_loading) {
            if (ImGui::GetTime() - test_t0 > 300) { tlog("FAIL: the weapon pose never came"); test_exit = 1; test_step = 99; }
            return;
        }
        auto w = chr.info.value("weapon", json());
        bool in_hand = w.is_object() && w.value("bone", std::string()) == "3FB256E5";
        bool skinned = false;
        auto slots = chr.info.value("part_slots", json::array());
        for (auto& sp : chr.skin)
            if (sp.entity >= 0 && sp.entity < (int)slots.size() && slots[sp.entity].get<std::string>() == "Weapon") skinned = true;
        bool ok = chr.animated && in_hand && skinned && chr.clip.find("WB01_Menu_Custom_Weapon_Selection_female") != std::string::npos;
        tlog(std::string(ok ? "ok" : "FAIL") + ": 75's weapon in her right hand (bone " + (w.is_object() ? w.value("bone", std::string()) : "-") +
             "), posed by " + chr.clip + (skinned ? "" : " - the weapon not skinned"));
        if (!ok) test_exit = 1;
        chr.playing = false;
        chr.t = 40 / chr.fps;
        chr.yaw = 0.35f;
        test_wait = 12;
        test_step = 2254;
        return;
    }
    case 2254:
        shot("char_75_wh1_hand.png");
        {
            json rows = chr.rows.is_object() ? chr.rows : json::object();
            rows["WeaponAt"] = 1;
            chr_load(75, rows);
        }
        test_step = 2255;
        return;
    case 2255: {
        if (chr.loading || chr.anim_loading || chr.colour_dirty > 0) return;
        auto w = chr.info.value("weapon", json());
        bool ok = w.is_object() && w.value("bone", std::string()) == "5D2A1DA3" && w.value("rig", std::string()) == "AC3MP_ID75_WB01";
        tlog(std::string(ok ? "ok" : "FAIL") + ": 75's weapon in her holster (" + (w.is_object() ? w.value("rig", std::string()) : "-") + ")");
        if (!ok) test_exit = 1;
        test_wait = 12;
        test_step = 2256;
        return;
    }
    case 2256:
        shot("char_75_wh1_holster.png");
        // the glossy main part as a NEW look, the Coyote Man's knife as a NEW weapon variant; built, listed, deleted
        chr_load(75);
        test_step = 2257;
        return;
    case 2257:
        if (chr.loading || chr.anim_loading || chr.colour_dirty > 0) return;
        chr.materials = json::parse(R"({"AC3MP_ID75_MainPart_V1": {"8CC8ED21": {"02042667": 4.0, "DF996150": 30.0, "199DF5E9": [1.0, 0.8, 0.4]}}})");
        strncpy_s(chr.new_looks["AC3MP_ID75_MainPart_V1"].name, "GILDED CORSET", 63);
        strncpy_s(chr.new_looks["AC3MP_ID75_MainPart_V1"].desc, "A glossy corset with a golden sheen, made in Reforged Studio.", 191);
        chr.weapons = {{"750101", "AC3MP_WB870101"}};
        strncpy_s(chr.new_weapons["750101"].name, "HUNTING KNIFE", 63);
        strncpy_s(chr.new_weapons["750101"].desc, "The Coyote Man's knife, for THE LADY MAVERICK.", 191);
        chr.bottom_req = 0;
        chr_pick(CharUI::PICK_PART, "AC3MP_ID75_MainPart_V1");
        test_wait = 10;
        test_step = 2258;
        return;
    case 2258:
        shot("char_75_nl1_parts.png");
        engine.request("char_delete", {{"name", "NL1_selftest"}}, [this](bool, const json&, const std::string&) { test_step = 2259; });
        test_step = 2299;
        return;
    case 2259:
        strncpy_s(chr.build_name, "NL1_selftest", sizeof chr.build_name - 1);
        chr_build();
        test_t0 = ImGui::GetTime();
        test_step = 2260;
        return;
    case 2260: {
        if (chr.building) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: the build never finished"); test_exit = 1; test_step = 99; }
            return;
        }
        bool ok = chr.build_msg.rfind("Built ", 0) == 0;
        tlog(std::string(ok ? "ok" : "FAIL") + ": " + chr.build_msg + " (" + std::to_string((int)(ImGui::GetTime() - test_t0)) + " s)");
        if (!ok) test_exit = 1;
        chr.bottom_req = 1;
        test_wait = 30;
        test_step = 2261;
        return;
    }
    case 2261: {
        bool listed = false;
        std::string what;
        for (auto& b : chr.builds)
            if (b.value("name", std::string()) == "NL1_selftest") {
                auto nr = b.value("report", json::object()).value("new", json::array());
                listed = nr.size() == 2 && b.value("materials", json::object()).empty() && b.value("weapons", json::object()).empty();
                for (auto& r : nr) what += r.value("item", std::string()) + " (unlock " + std::to_string(r.value("unlock", 0)) + ", \"" + r.value("name", std::string()) + "\") ";
            }
        tlog(std::string(listed ? "ok" : "FAIL") + ": NL1_selftest added " + what + "- the stock look and weapon not replaced");
        if (!listed) test_exit = 1;
        test_wait = 6;
        test_step = 2262;
        return;
    }
    case 2262:
        shot("char_nl1_builds.png");
        engine.request("char_delete", {{"name", "NL1_selftest"}}, [this](bool ok, const json&, const std::string& e) {
            tlog(std::string(ok ? "ok" : "FAIL") + ": NL1_selftest deleted" + (ok ? "" : ": " + e));
            if (!ok) test_exit = 1;
            test_step = 2263;
        });
        test_step = 2299;
        return;
    case 2263:
        chr.materials = json::object();
        chr.weapons = json::object();
        chr.new_looks.clear();
        chr.new_weapons.clear();
        test_step = 99;
        return;
    case 2299:                                          // (an engine answer moves the test on)
        return;
    case 1600:
        // SP Boston read from the install on request, a model of it placed (the library test's steps 80-86)
        lib_poll(true);
        test_t0 = ImGui::GetTime();
        test_step = 1601;
        return;
    case 1601: {
        std::string a;
        for (auto& m : lib_maps) if (m.value("title", std::string()) == "SP: Boston") a = m.value("archive", std::string());
        if (a.empty()) {
            lib_poll();
            if (ImGui::GetTime() - test_t0 > 120) { tlog("FAIL: no SP Boston in the library's maps (game folder?)"); test_exit = 1; test_step = 99; }
            return;
        }
        int sp_maps = 0;
        for (auto& m : lib_maps) sp_maps += m.value("sp", false);
        tlog("ok: " + std::to_string(sp_maps) + " SP maps listed from the install; asking for SP Boston (" + a + ")");
        sp_wanted.insert(a);
        project_path = test_dir + "\\test.ac3proj";      // (case 84 saves the project there)
        if (test_asset.empty()) { test_asset = "BOS_ALL_StoneWall_3m_01A"; test_asset_map = "SP: Boston"; }   // (--asset overrides)
        test_step = 80;
        return;
    }
    case 1500:
        // the whole of Wolcott cleared (ground kept), built: every hole plugged, the far view off
        load_gameplay();
        test_t0 = ImGui::GetTime();
        test_step = 1501;
        return;
    case 1501: {
        if (!msg.things_loaded) {
            if (ImGui::GetTime() - test_t0 > 600) { tlog("FAIL: the gameplay objects never came"); test_exit = 1; test_step = 99; }
            return;
        }
        int models = 0;
        for (auto& e : scene->entities) models += e.mesh >= 0;
        int n = bare_clear(nullptr);
        int left = 0, ground = 0, kept_other = 0;
        for (int id = 0; id < (int)scene->entities.size(); id++) {
            if (edits.removed(id) || scene->entities[id].mesh < 0) continue;
            left++;
            if (ground_like(id) || is_water_name(scene->entities[id].name)) ground++;
            else kept_other++;
        }
        tlog(std::string(n > 500 ? "ok" : "FAIL") + ": cleared " + std::to_string(n) + " of " + std::to_string(models) +
             " objects with a model; left " + std::to_string(left) + " (" + std::to_string(ground) + " ground / sea, " +
             std::to_string(kept_other) + " game-mode objects, spawn markers and objects that can't move)");
        if (n <= 500) test_exit = 1;
        json s = ms_settings();
        bool far_ok = s.value("stand_ins", true) == false && s.value("view_distance", 0) == 255;
        tlog(std::string(far_ok ? "ok" : "FAIL") + ": far_ok view off, view distance 255 in the map settings");
        if (!far_ok) test_exit = 1;
        edits.undo();
        int back = 0;
        for (int id = 0; id < (int)scene->entities.size(); id++) back += !edits.removed(id) && scene->entities[id].mesh >= 0;
        tlog(std::string(back == models ? "ok" : "FAIL") + ": Ctrl+Z: " + std::to_string(back) + " back");
        if (back != models) test_exit = 1;
        edits.redo();
        cam.pos = V3(-60, -95, 45);
        V3 d = normalize(V3(0, -20, 0) - cam.pos);
        cam.yaw = std::atan2(d.y, d.x);
        cam.pitch = std::asin(d.z);
        test_wait = 10;
        test_step = 1502;
        return;
    }
    case 1502:
        shot("1_bare.png");
        test_wait = 3;
        test_step = 50;                               // save the project, build (as --test), screenshot, done
        return;
    case 1400: {
        // a small prop (a barrel, a bucket) scattered on the open ground south of the fort; checked; painted again (nothing more); taken
        // away; undone; built (the copies through the engine and read back)
        int crate = -1;
        for (size_t i = 0; i < scene->entities.size() && crate < 0; i++)
            if (scene->entities[i].name.find("AC3MP_") == 0 && edits.movable((int)i) && scene->entities[i].mesh >= 0 && !ground_like((int)i) &&
                !(scene->entities[i].flags & F_MODE) && scene->entities[i].components.find("InertComponent") != std::string::npos &&
                scene->records[scene->entities[i].record].name.rfind("Cell", 0) == 0) {
                V3 a, b;
                world_box((int)i, a, b);
                if (b.z - a.z < 1.5f && b.x - a.x < 1.5f && b.y - a.y < 1.5f && b.z - a.z > 0.5f && b.x - a.x > 0.4f) crate = (int)i;
            }
        if (crate < 0) { tlog("FAIL: no small prop on the map"); test_exit = 1; test_step = 99; return; }
        selection = {crate};
        start_scatter();
        if (!scattering || scatter_pal.size() != 1) { tlog("FAIL: scatter didn't start: " + status); test_exit = 1; test_step = 99; return; }
        tlog("ok: scatter started with " + scatter_pal[0].name);
        float t = 0;
        V3 from(-2, -41, 60);
        V3 c(-2, -41, pick(from, V3(0, 0, -1), &t) >= 0 ? 60 - t : 0);
        scatter_r = 7;
        scatter_density = 6;
        int n = scatter_dab(c);
        edits.commit();
        std::vector<int> made;
        for (int id = (int)scene->entities.size(); id < edits.count(); id++) if (scatter_mine(id)) made.push_back(id);
        int target = (int)std::lround(6 * 3.14159265f * 49 / 100);
        bool ok = n == (int)made.size() && n >= target - 2;
        tlog(std::string(ok ? "ok" : "FAIL") + ": one dab (7 m, 6 per 100 m2 -> " + std::to_string(target) + "): " + std::to_string(n) + " copies");
        if (!ok) test_exit = 1;
        // each on the ground, none in another or in anything else
        int bad_ground = 0, bad_overlap = 0;
        for (int id : made) {
            V3 a, b;
            world_box(id, a, b);
            std::vector<int> skip = {id};
            pick_skip = &skip;
            float tt = 0;
            V3 o((a.x + b.x) / 2, (a.y + b.y) / 2, b.z + 2);
            int hit = pick(o, V3(0, 0, -1), &tt);
            pick_skip = nullptr;
            float g = o.z - tt;
            if (hit < 0 || !ground_like(hit) || a.z > g + 0.02f || a.z < g - 0.6f) bad_ground++;
            for (int other = 0; other < edits.count(); other++) {
                if (other == id || edits.removed(other) || !visible(other) || !mesh_of(other) || ground_like(other)) continue;
                V3 oa, ob;
                world_box(other, oa, ob);
                if (ob.z - oa.z < 0.15f || ob.x - oa.x > 40 || ob.y - oa.y > 40) continue;
                if (a.x + 0.02f < ob.x && b.x - 0.02f > oa.x && a.y + 0.02f < ob.y && b.y - 0.02f > oa.y && a.z + 0.12f < ob.z && b.z > oa.z) {
                    bad_overlap++;
                    tlog("   overlaps " + label_of(other));
                }
            }
        }
        tlog(std::string(bad_ground == 0 && bad_overlap == 0 ? "ok" : "FAIL") + ": every copy on the ground (" + std::to_string(bad_ground) +
             " not), none in another or in anything else (" + std::to_string(bad_overlap) + " overlaps)");
        if (bad_ground || bad_overlap) test_exit = 1;
        int again = scatter_dab(c);
        tlog(std::string(again == 0 ? "ok" : "FAIL") + ": the same spot painted again: " + std::to_string(again) + " more (full)");
        if (again) test_exit = 1;
        cam.pos = c + V3(-9, -9, 7);
        V3 d = normalize(c - cam.pos);
        cam.yaw = std::atan2(d.y, d.x);
        cam.pitch = std::asin(d.z);
        test_wait = 10;
        test_step = 1401;
        return;
    }
    case 1401:
        shot("1_scatter.png");
        test_wait = 3;
        test_step = 1402;
        return;
    case 1402: {
        float t = 0;
        V3 from(-2, -41, 60);
        V3 c(-2, -41, pick(from, V3(0, 0, -1), &t) >= 0 ? 60 - t : 0);
        int before = 0;
        for (int id = (int)scene->entities.size(); id < edits.count(); id++) before += scatter_mine(id);
        int gone = scatter_erase(c);
        edits.commit();
        int after = 0;
        for (int id = (int)scene->entities.size(); id < edits.count(); id++) after += scatter_mine(id);
        tlog(std::string(gone == before && after == 0 ? "ok" : "FAIL") + ": Shift: " + std::to_string(gone) + " of " + std::to_string(before) + " taken away");
        if (gone != before || after) test_exit = 1;
        edits.undo();
        int back = 0;
        for (int id = (int)scene->entities.size(); id < edits.count(); id++) back += scatter_mine(id);
        tlog(std::string(back == before ? "ok" : "FAIL") + ": undone: " + std::to_string(back) + " back");
        if (back != before) test_exit = 1;
        scattering = false;
        test_step = 50;                               // save the project, build (as --test), screenshot, done
        return;
    }
    case 1300:
        // the climb data read (merged pieces' collision), then the walker on Wolcott
        load_climb();
        test_step = 1301;
        return;
    case 1301: {
        if (!climbv.loaded || engine.busy()) return;
        // 1. dropped onto the open ground south of the fort: it lands and stands
        walk_enter(V3(-2, -41, 20));
        WalkInput none, fwd;
        fwd.fwd = 1;
        for (int i = 0; i < 120; i++) walk_step(1.0f / 60, none);
        float z;
        bool ground_ok = walk_ground && walk_floor(walk_p.x, walk_p.y, walk_p.z - 0.1f, walk_p.z + 0.1f, z);
        tlog(std::string(ground_ok ? "ok" : "FAIL") + ": dropped from 20 m, landed and stands at z " + std::to_string(walk_p.z).substr(0, 5) +
             " (" + std::to_string(walk_tris.size()) + " collision triangles, " + std::to_string(walk_edges.size()) + " climb edges)");
        if (!ground_ok) test_exit = 1;
        // 2. walked 6 s straight at the barracks (north): stopped by a wall, still on the ground
        cam.yaw = 1.5707963f;
        walk_p = V3(34, -36, 0);
        walk_enter(walk_p + V3(0, 0, 2));
        V3 p0 = walk_p;
        for (int i = 0; i < 360; i++) walk_step(1.0f / 60, fwd);
        float went = walk_p.y - p0.y;
        bool wall_ok = went < 25.0f && went > 0.5f && walk_ground;
        tlog(std::string(wall_ok ? "ok" : "FAIL") + ": ran north 6 s at 5 m/s (30 m free): stopped after " + std::to_string(went).substr(0, 5) +
             " m at (" + std::to_string(walk_p.x).substr(0, 5) + ", " + std::to_string(walk_p.y).substr(0, 5) + ", " + std::to_string(walk_p.z).substr(0, 4) + ")");
        if (!wall_ok) test_exit = 1;
        // 3. a jump: up and back down to the same floor
        float z0 = walk_p.z, peak = z0;
        WalkInput j;
        j.jump = true;
        walk_step(1.0f / 60, j);
        for (int i = 0; i < 90; i++) { walk_step(1.0f / 60, none); peak = std::max(peak, walk_p.z); }
        bool jump_ok = peak - z0 > 0.5f && peak - z0 < 0.9f && std::fabs(walk_p.z - z0) < 0.05f;
        tlog(std::string(jump_ok ? "ok" : "FAIL") + ": jumped " + std::to_string(peak - z0).substr(0, 4) + " m and landed back");
        if (!jump_ok) test_exit = 1;
        // 4. a ledge: stood under the climb edge nearest the ground (0.6 - 2.2 m up, facing it), Space climbs onto it
        int best = -1;
        float bz = 1e9f;
        for (int k = 0; k < (int)walk_edges.size(); k++) {
            const ClimbEdge& e = walk_edges[k];
            if (length(e.b - e.a) < 1.0f || std::fabs(e.a.z - e.b.z) > 0.1f || length(V3(e.hx, e.hy, 0)) < 0.5f) continue;
            V3 m = (e.a + e.b) * 0.5f, out(e.hx, e.hy, 0);
            V3 stand = m + normalize(out) * 0.7f;
            float g;
            if (!walk_floor(stand.x, stand.y, m.z - 2.2f, m.z - 0.6f, g)) continue;
            if (m.z - g >= 1.2f && m.z - g < bz) { bz = m.z - g; best = k; }      // (a real climb, not a step)
        }
        bool climb_ok = false;
        if (best >= 0) {
            const ClimbEdge& e = walk_edges[best];
            V3 m = (e.a + e.b) * 0.5f, out = normalize(V3(e.hx, e.hy, 0));
            walk_enter(m + out * 0.7f);
            cam.yaw = std::atan2(-out.y, -out.x);
            for (int i = 0; i < 30; i++) walk_step(1.0f / 60, none);
            float before = walk_p.z;
            walk_step(1.0f / 60, j);
            for (int i = 0; i < 30; i++) walk_step(1.0f / 60, none);
            climb_ok = walk_p.z > before + 0.5f && walk_ground;
            tlog(std::string(climb_ok ? "ok" : "FAIL") + ": under a " + ClimbView::kind_name(e.kind) + " " + std::to_string(bz).substr(0, 4) +
                 " m up, Space: from z " + std::to_string(before).substr(0, 5) + " onto z " + std::to_string(walk_p.z).substr(0, 5) + " (" + walk_msg + ")");
        } else tlog("FAIL: no ledge in reach of the ground found");
        if (!climb_ok) test_exit = 1;
        test_wait = 10;
        test_step = 1302;
        return;
    }
    case 1302:
        shot("1_walk.png");
        test_wait = 3;
        test_step = 1303;
        return;
    case 1303:
        walking = false;
        test_step = 99;
        return;
    case 1200: {
        if (engine.busy()) return;                    // (the map's own reads first)
        // Test in game (dry: nothing installed or launched - a hidden stand-in process plays the game): an edit, F5, the build,
        // the stand-in watched until it's gone, the original "back"; then F5 again with nothing changed: no new build
        tg_dry = true;
        tg_proc = L"PING.EXE";
        int crate = -1;
        for (size_t i = 0; i < scene->entities.size() && crate < 0; i++)
            if (scene->entities[i].name.find("Crate") != std::string::npos && edits.movable((int)i) && scene->entities[i].mesh >= 0) crate = (int)i;
        edits.matrix(crate).set_pos(edits.matrix(crate).pos() + V3(1, 0, 0));
        edits.commit();
        test_in_game();
        tlog("F5: " + status);
        test_t0 = ImGui::GetTime();
        test_step = 1201;
        return;
    }
    case 1201:
        if (tg_state != 0) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: stuck in state " + std::to_string(tg_state)); test_exit = 1; test_step = 99; }
            return;
        }
        tlog(std::string(tg_restored && build_ok ? "ok" : "FAIL") + ": built, the stand-in ran and closed, the original back: " + status);
        if (!tg_restored || !build_ok) { test_exit = 1; test_step = 99; return; }
        {
            std::string before = last_dest;
            test_in_game();                            // nothing changed: the same build, no new one
            bool same = last_dest == before && tg_state == 3;
            tlog(std::string(same ? "ok" : "FAIL") + ": F5 again with nothing changed - no new build (" + status + ")");
            if (!same) test_exit = 1;
        }
        test_t0 = ImGui::GetTime();
        test_step = 1202;
        return;
    case 1202:
        if (tg_state != 0 && ImGui::GetTime() - test_t0 < 120) return;
        tlog(std::string(tg_state == 0 && tg_restored ? "ok" : "FAIL") + ": the second run watched to the end");
        if (tg_state != 0 || !tg_restored) test_exit = 1;
        test_step = 99;
        return;
    case 1100: {
        // a crate raised 3 m, dropped back to the ground; the Outliner's "Changed" view; the shortcuts sheet
        int crate = -1;
        for (size_t i = 0; i < scene->entities.size() && crate < 0; i++)
            if (scene->entities[i].name.find("Crate") != std::string::npos && edits.movable((int)i) && scene->entities[i].mesh >= 0 &&
                scene->records[scene->entities[i].record].name.rfind("Cell", 0) == 0) crate = (int)i;
        if (crate < 0) { tlog("FAIL: no crate"); test_exit = 1; test_step = 99; return; }
        selection = {crate};
        drop_to_ground();                              // where its bottom meets the ground under its middle
        float z0 = edits.matrix(crate).pos().z;
        edits.matrix(crate).set_pos(edits.matrix(crate).pos() + V3(0, 0, 3));
        edits.commit();
        drop_to_ground();                              // raised 3 m and dropped again: the same place
        float z1 = edits.matrix(crate).pos().z;
        bool ok = std::fabs(z1 - z0) < 0.05f;
        tlog(std::string(ok ? "ok" : "FAIL") + ": " + scene->entities[crate].name + " dropped to the ground, raised 3 m, dropped again: " +
             std::to_string(z1 - z0).substr(0, 5) + " m apart (" + status + ")");
        if (!ok) test_exit = 1;
        edits.matrix(crate).set_pos(edits.matrix(crate).pos() + V3(1, 0, 0));
        edits.commit();
        int changed = 0;
        for (int id = 0; id < edits.count(); id++) changed += edits.removed(id) || edits.moved(id) || edits.is_copy(id);
        outliner_show = 1;
        tlog(std::string(changed == 1 ? "ok" : "FAIL") + ": the Outliner's Changed view lists " + std::to_string(changed) + " object(s)");
        if (changed != 1) test_exit = 1;
        show_help = true;
        cam.yaw = 0.9f;
        cam.pitch = -0.5f;
        frame_selection();
        test_wait = 10;
        test_step = 1101;
        return;
    }
    case 1101:
        shot("1_ui.png");
        test_wait = 3;
        test_step = 99;
        return;
    case 1000: {
        // the hill and hollow of studio/tools/sculpt_check.py, as strokes made by the brush, previewed and built
        struct S { float x, y, r, h; };
        for (S st : {S{-2, -41, 7, 2.0f}, S{2, -37, 5, 1.5f}, S{4, -4, 6, -1.5f}}) {
            float t = 0;
            V3 from(st.x, st.y, 60);
            float z = pick(from, V3(0, 0, -1), &t) >= 0 ? 60 - t : 0;
            brush_r = st.r;
            add_stroke(V3(st.x, st.y, z), st.h);
        }
        edits.commit();
        sculpting = true;
        sculpt_preview();
        tlog("sculpt: " + std::to_string(edits.cur.sculpts.size()) + " strokes, the preview " + std::to_string(sculpt_prev_tris.size() / 3) +
             " bent triangles; peak " + std::to_string(sculpt_dz(-2, -41)).substr(0, 4) + " m, hollow " + std::to_string(sculpt_dz(4, -4)).substr(0, 5) + " m");
        if (sculpt_prev_tris.empty()) { tlog("FAIL: no preview"); test_exit = 1; }
        cam.pos = V3(-22, -62, 14);
        {
            V3 d = normalize(V3(0, -39, 0) - cam.pos);
            cam.yaw = std::atan2(d.y, d.x);
            cam.pitch = std::asin(d.z);
        }
        test_wait = 10;
        test_step = 1001;
        return;
    }
    case 1001:
        shot("1_sculpt_preview.png");
        test_wait = 3;
        test_step = 1002;
        return;
    case 1002:
        sculpting = false;
        test_step = 50;                               // save the project, build (as --test), screenshot, done
        return;
    case 900:
        // the library indexed (the stock maps' index files), every model filed
        user_dir = test_dir + "\\assets";
        make_dirs(widen(user_dir));
        lib_poll(true);
        focus_library = true;
        lib_tab = 0;
        test_t0 = ImGui::GetTime();
        test_step = 901;
        return;
    case 901: {
        lib_poll();
        if (lib.size() < 500) {
            if (ImGui::GetTime() - test_t0 > 900) { tlog("FAIL: the library has " + std::to_string(lib.size()) + " models"); test_exit = 1; test_step = 99; }
            return;
        }
        std::map<std::string, int> count;
        for (auto& L : lib) count[categories().model(L.name, L.ground)]++;
        std::string line;
        for (auto& [c, n] : count) line += (line.empty() ? "" : ", ") + c + " " + std::to_string(n);
        tlog("library: " + std::to_string(lib.size()) + " models filed - " + line);
        int props = count["Props"];
        bool ok = count.size() >= 12 && props < (int)lib.size() / 2;
        tlog(std::string(ok ? "ok" : "FAIL") + ": the automatic categories cover most models (" + std::to_string(props) + " left as Props)");
        if (!ok) test_exit = 1;
        // filed by hand, into a category of its own, saved, read back
        std::string name = lib[0].name;
        categories().custom.insert("Test favourites");
        file_model(name, lib[0].ground, "Test favourites");
        cats::Store back;
        back.load(user_dir + "\\categories.json");
        bool ok2 = back.model(name, lib[0].ground) == "Test favourites";
        tlog(std::string(ok2 ? "ok" : "FAIL") + ": " + name + " filed under a category of his own, saved and read back");
        if (!ok2) test_exit = 1;
        lib_cat = "Houses & buildings";
        lib_dirty = true;
        test_wait = 10;
        test_step = 902;
        return;
    }
    case 902:
        shot("1_library_houses.png");
        test_wait = 3;
        test_step = 99;
        return;
    case 800: {
        // one piece of a multi-part structure -> G (a ring), Shift+G (the whole structure); a box select
        int seed = -1;
        for (size_t i = 0; i < scene->entities.size() && seed < 0; i++)
            if (scene->entities[i].name == (test_piece.empty() ? std::string("AC3MP_FORT_MessHall_BrokenRail_01A_002") : test_piece)) seed = (int)i;
        if (seed < 0) { tlog("FAIL: no seed piece"); test_exit = 1; test_step = 99; return; }
        selection = {seed};
        int ring = select_attached(false);
        size_t after_ring = selection.size();
        int more = select_attached(true);
        std::string names;
        for (size_t k = 0; k < selection.size() && k < 12; k++) names += (k ? ", " : "") + label_of(selection[k]);
        bool no_ground = true;
        float span = 0;
        V3 L(1e30f, 1e30f, 1e30f), H(-1e30f, -1e30f, -1e30f);
        for (int id : selection) {
            no_ground &= label_of(id).find("Ground") == std::string::npos;
            V3 a, b;
            world_box(id, a, b);
            L = vmin(L, a);
            H = vmax(H, b);
        }
        span = std::max(H.x - L.x, H.y - L.y);
        tlog("seed " + scene->entities[seed].name + ": G added " + std::to_string(ring) + " (" + std::to_string(after_ring) +
             " selected), Shift+G " + std::to_string(more) + " more: " + std::to_string(selection.size()) + " pieces, " +
             std::to_string((int)span) + " m across - " + names + (selection.size() > 12 ? " ..." : ""));
        bool ok = ring > 0 && selection.size() > after_ring && no_ground && span <= attach_span + 0.5f;
        tlog(std::string(ok ? "ok" : "FAIL") + ": Select Attached grew from one piece to a structure, without the ground");
        if (!ok) test_exit = 1;
        cam.yaw = 0.9f;
        cam.pitch = -0.5f;
        frame_selection();
        test_wait = 10;
        test_step = 8010;
        return;
    }
    case 8010:
        shot("1_attached.png");
        test_wait = 5;
        test_step = 801;
        return;
    case 801: {
        // copied as one: Ctrl+D, every piece a copy
        size_t n = selection.size(), copies0 = edits.cur.copies.size();
        copy_selected();
        bool ok = edits.cur.copies.size() - copies0 == n && selection.size() == n;
        tlog(std::string(ok ? "ok" : "FAIL") + ": Ctrl+D copied all " + std::to_string(n) + " pieces");
        if (!ok) test_exit = 1;
        edits.undo();
        // box select: a rectangle round the middle of the view
        selection.clear();
        ImVec2 c(vp_pos.x + vp_size.x * 0.5f, vp_pos.y + vp_size.y * 0.5f);
        box_select(ImVec2(c.x - vp_size.x * 0.3f, c.y - vp_size.y * 0.3f), ImVec2(c.x + vp_size.x * 0.3f, c.y + vp_size.y * 0.3f), false);
        bool ok2 = !selection.empty();
        tlog(std::string(ok2 ? "ok" : "FAIL") + ": box select - " + std::to_string(selection.size()) + " object(s) wholly inside the box");
        if (!ok2) test_exit = 1;
        test_wait = 6;
        test_step = 802;
        return;
    }
    case 802:
        shot("2_box_select.png");
        test_wait = 3;
        test_step = 99;
        return;
    case 700:
        // the climb data read
        show_climb = true;
        load_climb();
        test_t0 = ImGui::GetTime();
        test_step = 701;
        return;
    case 701: {
        if (!climbv.loaded) {
            if (ImGui::GetTime() - test_t0 > 300 || (!climbv.loading && !engine.busy())) { tlog("FAIL: no climb data: " + status); test_exit = 1; test_step = 99; }
            return;
        }
        tlog("climbing: " + std::to_string(climbv.edges.size()) + " climb edges, " + std::to_string(climbv.n_noclimb) + " of " +
             std::to_string(climbv.n_tris) + " collision triangles no-climb, " + std::to_string(climbv.members.size()) + " merged pieces");
        // the movable cell object holding the most climb edges
        int best = -1;
        size_t most = 0;
        for (auto& [ent_i, es] : climbv.by_owner) {
            if (!edits.movable(ent_i) || scene->entities[ent_i].mesh < 0) continue;
            if (scene->records[scene->entities[ent_i].record].name.rfind("Cell", 0) != 0) continue;
            if (es.size() > most) { most = es.size(); best = ent_i; }
        }
        if (best < 0) { tlog("FAIL: no movable object holds climb edges"); test_exit = 1; test_step = 99; return; }
        selection = {best};
        ClimbSummary c = climb_summary(best);
        tlog(std::string(c.edges > 0 ? "ok" : "FAIL") + ": " + scene->entities[best].name + " - " + std::to_string(c.edges) +
             " climb edges at it (" + std::to_string(c.poles) + " poles / beams), collision " + std::to_string((int)c.area) +
             " m2, no-climb " + std::to_string((int)c.noclimb) + " m2");
        if (c.edges <= 0) test_exit = 1;
        cam.yaw = 0.9f;
        cam.pitch = -0.5f;
        frame_selection();
        cam.pos = cam.pos - cam.forward() * 6.0f;          // (a little wider: the edges round it too)
        test_wait = 10;
        test_step = 7020;
        return;
    }
    case 7020:
        shot("1_climb_edges.png");
        test_wait = 5;
        test_step = 702;
        return;
    case 702: {
        // an object whose collision is mostly no-climb (the barracks' FuzzyZone shell, Wolcott's roofs)
        int best = -1;
        float most = 0;
        for (int i = 0; i < (int)scene->entities.size(); i++) {
            const Entity& e = scene->entities[i];
            if (e.mesh < 0 || !edits.movable(i) || e.record < 0 || scene->records[e.record].name.rfind("Cell", 0) != 0) continue;
            bool any = false;
            for (int sh : e.shapes) any |= climbv.noclimb.count(sh) > 0;
            if (!any) continue;
            ClimbSummary c = climb_summary(i);
            if (c.noclimb > most) { most = c.noclimb; best = i; }
        }
        if (best < 0) { tlog("FAIL: no object with no-climb collision"); test_exit = 1; test_step = 99; return; }
        selection = {best};
        ClimbSummary c = climb_summary(best);
        tlog(std::string(c.noclimb > 0 ? "ok" : "FAIL") + ": " + scene->entities[best].name + " - collision " +
             std::to_string((int)c.area) + " m2, no-climb " + std::to_string((int)c.noclimb) + " m2 (drawn red)");
        if (c.noclimb <= 0) test_exit = 1;
        cam.yaw = 2.3f;
        cam.pitch = -0.45f;
        frame_selection();
        cam.pos = cam.pos - cam.forward() * 10.0f;
        test_wait = 10;
        test_step = 7030;
        return;
    }
    case 7030:
        shot("2_noclimb.png");
        test_wait = 5;
        test_step = 703;
        return;
    case 703: {
        // moved 5 m east: its edges go with it
        int best = -1;
        size_t most = 0;
        for (auto& [ent_i, es] : climbv.by_owner)
            if (edits.movable(ent_i) && scene->entities[ent_i].mesh >= 0 && es.size() > most) { most = es.size(); best = ent_i; }
        std::vector<ClimbEdge> before, after;
        climb_edges_of(best, before);
        edits.matrix(best).set_pos(edits.matrix(best).pos() + V3(5, 0, 0));
        climb_edges_of(best, after);
        bool ok = !before.empty() && before.size() == after.size() && length(after[0].a - (before[0].a + V3(5, 0, 0))) < 1e-3f;
        tlog(std::string(ok ? "ok" : "FAIL") + ": moved 5 m east - its " + std::to_string(after.size()) + " climb edges moved with it");
        if (!ok) test_exit = 1;
        edits.matrix(best) = scene->entities[best].matrix;
        test_wait = 3;
        test_step = 99;
        return;
    }
    case 600:
        // listen; studio/tools/live_check.py's fake game sends the player, NPCs, another player and two marks
        live_toggle();
        live_test_t0 = ImGui::GetTime();
        test_step = 601;
        return;
    case 601: {
        bool ready = livef.has_me && !livef.npcs.empty() && livef.marks.size() >= 2 && !livef.players.empty();
        if (!ready) {
            if (ImGui::GetTime() - live_test_t0 > 60) {
                tlog("FAIL: nothing (or not everything) received from the fake game in 60 s: " + std::to_string(livef.datagrams) +
                     " datagram(s)");
                test_exit = 1;
                test_step = 99;
            }
            return;
        }
        tlog("live: " + std::to_string(livef.datagrams) + " datagram(s); me at (" + std::to_string((int)livef.me.x) + ", " +
             std::to_string((int)livef.me.y) + "); " + std::to_string(livef.npcs.size()) + " NPCs, " +
             std::to_string(livef.players.size()) + " other player(s), " + std::to_string(livef.marks.size()) + " marks");
        live_follow = true;
        cam.yaw = 0.9f;
        cam.pitch = -0.6f;
        test_wait = 10;
        test_step = 602;
        return;
    }
    case 602: {
        shot("1_live.png");
        V3 target = livef.me + V3(0, 0, 1.2f);
        float d = length(cam.pos - target);
        bool follow_ok = std::fabs(d - live_follow_dist) < 0.5f;
        live_place_pending = true;
        live_place_at = livef.marks[1];
        V3 p = placement_point();
        bool place_ok = length(p - livef.marks[1]) < 1e-3f && !live_place_pending;
        bool fresh = live_fresh();
        tlog(std::string(follow_ok ? "ok" : "FAIL") + ": Follow me - the camera " + std::to_string(d).substr(0, 5) + " m from the player");
        tlog(std::string(place_ok ? "ok" : "FAIL") + ": Place here - the next placement on mark M2");
        tlog(std::string(fresh ? "ok" : "FAIL") + ": the data is fresh (under 2 s old)");
        test_exit = follow_ok && place_ok && fresh ? 0 : 1;
        test_wait = 3;
        test_step = 99;
        return;
    }
    case 500:
        // the blend groups read (the map's and the stock maps')
        show_blend = true;
        load_blend();
        test_t0 = ImGui::GetTime();
        test_step = 501;
        return;
    case 501: {
        if (!blendv.loaded) {
            if (ImGui::GetTime() - test_t0 > 1200 || (!blendv.loading && !engine.busy())) { tlog("FAIL: blend groups not read: " + status); test_exit = 1; test_step = 99; }
            return;
        }
        tlog("blend groups: " + std::to_string(blendv.groups.size()) + " on this map, " + std::to_string(blendv.templates.size()) +
             " stock ones to place");
        if (blend_set_test) { test_step = 510; return; }
        auto find = [&](const std::string& n) {
            for (size_t i = 0; i < scene->entities.size(); i++) if (scene->entities[i].name == n) return (int)i;
            return -1;
        };
        int mv = find("AC3MP_GEN_Static_Group_079"), rm = find("AC3MP_GEN_Wall_Static_005"), cp = find("AC3MP_GEN_Static_Group_Posing_001");
        if (mv < 0 || rm < 0 || cp < 0 || !blend_of(mv) || !blend_of(rm) || !blend_of(cp)) {
            tlog("FAIL: Wolcott's test blend groups not found as blend groups"); test_exit = 1; test_step = 99; return;
        }
        edits.matrix(mv).set_pos(edits.matrix(mv).pos() + V3(3, 0, 0));
        edits.set_removed(rm, true);
        M4 c = edits.matrix(cp);
        c.set_pos(c.pos() + V3(4, 6, 0));
        edits.cur.copies.push_back({cp, c, false});
        int t = -1;
        for (size_t k = 0; k < blendv.templates.size(); k++)
            if (blendv.templates[k].map == "Boston_Mill" && blendv.templates[k].label == "Merchant Meat") t = (int)k;
        if (t < 0) { tlog("FAIL: no Boston Mill meat merchant to place"); test_exit = 1; test_step = 99; return; }
        V3 at(2, -8, 30);
        float d = 0;
        if (pick(at, V3(0, 0, -1), &d) >= 0) at.z -= d; else at.z = 0;
        int placed_id = place_blend(t, at);
        edits.commit();
        tlog("moved " + scene->entities[mv].name + " 3 m east; took out " + scene->entities[rm].name + "; copied " +
             scene->entities[cp].name + " 4 m east / 6 m north; placed " + blendv.templates[t].label + " (" +
             blendv.templates[t].map + ") at (2, -8): " + (blend_of(placed_id) ? "its slots shown" : "its slots not shown yet"));
        V3 mid = edits.matrix(mv).pos();
        cam.pos = mid + V3(-10, -26, 18);
        {
            V3 dd = normalize(V3(6, -4, 0) - cam.pos);
            cam.yaw = std::atan2(dd.y, dd.x);
            cam.pitch = std::asin(dd.z);
        }
        selection = {mv};
        test_t0 = ImGui::GetTime();
        test_step = 502;
        return;
    }
    case 510: {
        // Boston Mill's meat merchant placed WITH its stall (the default) 6 m north of Wolcott's pillory, where
        // --blend-test places it without: the group and the stall's two pieces as one placed group
        int t = -1, with_set = 0;
        for (size_t k = 0; k < blendv.templates.size(); k++) {
            if (!blendv.templates[k].set.empty()) with_set++;
            if (blendv.templates[k].map == "Boston_Mill" && blendv.templates[k].label == "Merchant Meat") t = (int)k;
        }
        tlog(std::to_string(with_set) + " stock blend group(s) come with a set");
        if (t < 0 || blendv.templates[t].set.size() != 2) {
            tlog("FAIL: no Boston Mill meat merchant with its stall (2 pieces) to place"); test_exit = 1; test_step = 99; return;
        }
        const BlendInfo& b = blendv.templates[t];
        for (auto& pc : b.set) tlog("  its " + pc.piece + ": " + pc.name);
        V3 at(2, -8, 30);
        float d = 0;
        if (pick(at, V3(0, 0, -1), &d) >= 0) at.z -= d; else at.z = 0;
        int n0 = (int)edits.cur.copies.size();
        blend_with_set = true;
        int first = place_blend(t, at);
        int made = (int)edits.cur.copies.size() - n0;
        int g = first >= 0 && edits.is_copy(first) ? edits.cur.copies[first - scene->entities.size()].group : -1;
        bool ok = made == 3 && g >= 0 && blend_of(first) != nullptr;
        for (int k = n0; k < (int)edits.cur.copies.size(); k++)
            if (edits.cur.copies[k].group != g) ok = false;
        // the stall stands where Mill has it relative to the group (its position + the piece's offset)
        for (size_t k = 0; k < b.set.size() && ok; k++) {
            V3 want = (b.set[k].matrix * translation(at)).pos();
            V3 got = edits.cur.copies[n0 + 1 + k].matrix.pos();
            if (length(got - want) > 1e-3f) { ok = false; tlog("FAIL: " + b.set[k].name + " not where its offset puts it"); }
        }
        tlog(std::string(ok ? "placed" : "FAIL: placed") + " " + b.label + " (" + b.map + ") with its stall at (2, -8): " +
             std::to_string(made) + " part(s) in one group (" + (g >= 0 ? edits.groups[g].name : std::string("none")) + ")");
        if (!ok) { test_exit = 1; test_step = 99; return; }
        // a click on the stall picks the whole group; a double-click goes in to the stall alone
        int stall = (int)scene->entities.size() + n0 + 1;
        selection.clear();
        scope_group = -1;
        click_pick(stall, false, false);
        int whole = (int)selection.size();
        click_pick(stall, false, true);
        int inside = (int)selection.size();
        tlog("a click on the stall picks " + std::to_string(whole) + " (the group and its stall), a double-click " +
             std::to_string(inside));
        if (whole != 3 || inside != 1) { tlog("FAIL: picking the placed group"); test_exit = 1; test_step = 99; return; }
        // the whole moved 1 m east and turned 90 degrees: the stall keeps its place by the group
        selection.clear();
        scope_group = -1;
        click_pick(stall, false, false);
        std::vector<int> movers = selection;
        std::vector<M4> start;
        for (int id : movers) start.push_back(edits.matrix(id));
        M4 p0 = pivot_of(movers), p1 = rot_z(1.5707963f) * translation(p0.pos() + V3(1, 0, 0));
        apply_pivot(movers, start, p0, p1);
        edits.cur.group_root[g] = edits.cur.group_root[g] * (inverse(p0) * p1);   // (as a gizmo drag of the whole)
        edits.commit();
        V3 gp = edits.matrix(first).pos(), sp = edits.matrix(stall).pos();
        tlog("moved the whole 1 m east, turned 90 degrees: the stall " + std::to_string(length(sp - gp)) + " m from the group "
             "(was " + std::to_string(length(b.set[0].matrix.pos())) + ")");
        if (std::fabs(length(sp - gp) - length(b.set[0].matrix.pos())) > 1e-3f) {
            tlog("FAIL: the stall didn't move with the group"); test_exit = 1; test_step = 99; return;
        }
        cam.pos = gp + V3(-9, -15, 12);                // (from the pillory's side, above the courtyard)
        {
            V3 dd = normalize(gp - cam.pos);
            cam.yaw = std::atan2(dd.y, dd.x);
            cam.pitch = std::asin(dd.z);
        }
        test_t0 = ImGui::GetTime();
        test_step = 502;
        return;
    }
    case 502:
        // the placed group's disc (its model read from Boston Mill into the asset scene)
        if ((assets_loading || engine.busy()) && ImGui::GetTime() - test_t0 < 600) return;
        test_wait = 8;
        test_step = 503;
        return;
    case 503:
        shot("1_blend_groups.png");
        test_step = 50;                               // save the project, build (as --test), screenshot, done
        return;
    case 402:
        shot("1_navmesh.png");
        test_step = 50;                               // save the project, build (as --test), screenshot, done
        return;
    case 300: {
        // the ground under Wolcott's pillory (else 12 m in the middle of the view) raised 1 m, as + Ground makes it
        // (its whole footprint + 2 m: a 12 m square centred on its origin - a corner - would cover only part of it)
        V3 c = cam.pos + cam.forward() * 20.0f, half(6, 6, 0);
        for (size_t i = 0; i < scene->entities.size(); i++)
            if (scene->entities[i].name == "AC3MP_FORT_Pillory_01A_001") {
                V3 lo, hi;
                world_box((int)i, lo, hi);
                c = (lo + hi) * 0.5f;
                c.z = lo.z;
                half = V3((hi.x - lo.x) / 2 + 2, (hi.y - lo.y) / 2 + 2, 0);
            }
        start_region();
        for (V3 o : {V3(-half.x, -half.y, 0), V3(half.x, -half.y, 0), V3(half.x, half.y, 0), V3(-half.x, half.y, 0)}) {
            float t = 0;
            V3 from = c + o + V3(0, 0, 30);
            if (pick(from, V3(0, 0, -1), &t) >= 0) draft.push_back(from + V3(0, 0, -t));
            else draft.push_back(c + o);
        }
        finish_region();
        tlog(region_ok(sel_region) ? "ground area: " + std::to_string(edits.cur.regions.size()) + " region(s), " +
                                         std::to_string((int)area2d(hull2d(edits.cur.regions[sel_region].pts))) + " m2, +1 m"
                                   : "FAIL: the ground area wasn't made: " + status);
        cam.pos = c + V3(-12, -16, 14);
        {
            V3 d = normalize(c - cam.pos);
            cam.yaw = std::atan2(d.y, d.x);
            cam.pitch = std::asin(d.z);
        }
        test_wait = 8;
        test_step = 301;
        return;
    }
    case 301:
        shot("1_ground_area.png");
        test_step = 50;                               // save the project, build (as --test), screenshot, done
        return;
    case 200: {
        // 8 views round the point from 12 m up, 8 from 2 m (under eaves and floors), one from above
        if (shot_k >= 17) { test_step = 99; return; }
        int k = shot_k % 8;
        float h = shot_k < 8 ? 12.0f : 2.0f;
        float a = k * 0.785398f;
        if (shot_k == 16) { cam.pos = shots_at + V3(0, -0.01f, shots_dist); cam.yaw = 1.5708f; cam.pitch = -1.56f; }
        else {
            cam.pos = shots_at + V3(std::cos(a) * shots_dist, std::sin(a) * shots_dist, h);
            V3 d = normalize(shots_at - cam.pos);
            cam.yaw = std::atan2(d.y, d.x);
            cam.pitch = std::asin(d.z);
        }
        char name[32];
        snprintf(name, sizeof name, "view_%02d.png", shot_k);
        pending_name = name;
        test_wait = 2;
        test_step = 201;
        return;
    }
    case 201:
        shot(pending_name.c_str());
        shot_k++;
        test_step = 200;
        return;
    case 20: {
        // a piece of the barracks (Fort Wolcott), else the first grouped model in a cell
        int piece = -1;
        std::string want = test_piece.empty() ? "AC3MP_FORT_Barracks_building_01A_001/14" : test_piece;
        for (size_t i = 0; i < scene->entities.size() && piece < 0; i++)
            if (scene->entities[i].name.rfind(want, 0) == 0 && scene->entities[i].mesh >= 0) piece = (int)i;
        for (size_t i = 0; i < scene->entities.size() && piece < 0; i++) {
            const Entity& e = scene->entities[i];
            if (!e.group.empty() && e.mesh >= 0 && scene->records[e.record].name.rfind("Cell", 0) == 0) piece = (int)i;
        }
        if (piece < 0) { tlog("FAIL: no building piece"); test_exit = 1; test_step = 99; return; }
        // picking: a ray from the camera through the piece's middle must hit it (or something in front of it)
        V3 lo, hi;
        world_box(piece, lo, hi);
        V3 c = (lo + hi) * 0.5f;
        cam.pos = c + V3(0, -6, 3) + V3(0, -(hi.y - lo.y), 0);
        V3 d = normalize(c - cam.pos);
        int hit = pick(cam.pos, d);
        tlog("pick from 6 m: " + (hit >= 0 ? scene->entities[edits.entity_of(hit)].name : std::string("nothing")) +
             (hit == piece ? " (the piece itself)" : ""));
        selection = {piece};
        tlog("selected " + scene->entities[piece].name + "; Select Building...");
        select_building();
        test_step = 3;
        return;
    }
    case 3:
        if (engine.busy()) return;
        tlog("building: " + std::to_string(selection.size()) + " pieces selected");
        cam.yaw = 1.2f;
        cam.pitch = -0.55f;
        frame_selection();
        test_wait = 8;
        test_step = 4;
        return;
    case 4:
        shot("2_building.png");
        test_step = 40;
        return;
    case 40: {
        std::vector<int> movers;
        for (int id : selection)
            if (edits.movable(id)) movers.push_back(id);
        std::vector<M4> start;
        for (int id : movers) start.push_back(edits.matrix(id));
        gizmo_local = false;
        M4 p0 = pivot_of(movers), p1 = rot_z(3.14159265f);
        p1.set_pos(p0.pos());
        apply_pivot(movers, start, p0, p1);
        edits.commit();
        tlog("turned " + std::to_string(movers.size()) + " pieces 180 degrees about the building's middle");
        test_wait = 8;
        test_step = 5;
        return;
    }
    case 5:
        shot("3_turned.png");
        test_step = 50;
        return;
    case 50: {
        if (engine.busy()) return;
        project_path = test_dir + "\\test.ac3proj";
        save_project(false);
        tlog("saved " + project_path + "; building...");
        build();
        focus_build = true;
        test_step = 6;
        return;
    }
    case 6:
        if (engine.busy()) return;
        focus_build = true;
        if (!last_build.is_object()) { tlog("FAIL: no build ran: " + status); test_exit = 1; }
        else if (last_build.contains("error")) { tlog("FAIL: build: " + last_build["error"].get<std::string>()); test_exit = 1; }
        else {
            tlog(std::string(build_ok ? "PASS" : "FAIL") + ": built " + last_dest);
            for (auto& l : last_build.value("report", json::array())) tlog("  " + l.get<std::string>());
            for (auto& l : last_build.value("checks", json::array())) tlog("  " + l.get<std::string>());
            for (auto& l : last_build.value("warnings", json::array())) tlog("  WARNING " + l.get<std::string>());
            test_exit = build_ok ? 0 : 1;
        }
        test_wait = 6;
        test_step = 7;
        return;
    case 7:
        shot("4_built.png");
        test_wait = 3;
        test_step = region_test || nav_test || blend_test || gs_test || ms1_test || ms2_test || sc_test || ba_test ? 99 : 80;   // (--region-test / --nav-test / --blend-test / --gs-test / --ms1-test / --ms2-test / --scatter-test end here)
        return;
    case 80: {
        // the library: a model of another map (default: Galata's fountain; on Galata, Fort Wolcott's pillory)
        if (test_asset.empty()) {
            bool galata = scene->title == "Galata";
            test_asset = galata ? "AC3MP_FORT_Pillory_01A" : "ACFE_CONS_Fountain_02A";
            test_asset_map = galata ? "Fort Wolcott" : "Galata";
        }
        lib_prefer = test_asset_map;
        lib_poll(true);
        focus_library = true;
        test_t0 = ImGui::GetTime();
        tlog("library: indexing the release's maps (" + test_asset_map + " first), looking for " + test_asset);
        test_step = 81;
        return;
    }
    case 81: {
        int found = -1;
        for (size_t i = 0; i < lib.size() && found < 0; i++)
            if (lib[i].name.rfind(test_asset, 0) == 0 && lib[i].map == test_asset_map && lib[i].archive != scene->archive)
                found = (int)i;
        if (found < 0) {
            lib_poll();
            bool done_indexing = lib_asked && !lib_busy && !lib_maps.empty() && !engine.busy() && lib_read.size() > 0;
            for (auto& m : lib_maps) done_indexing &= !m["index"].is_null() || m["error"].is_string();
            if (ImGui::GetTime() - test_t0 > 2400 || done_indexing) {
                tlog("FAIL: no model " + test_asset + " of " + test_asset_map + " in the library (" + std::to_string(lib.size()) + " models)");
                test_exit = 1;
                test_step = 99;
            }
            return;
        }
        tlog("library: " + std::to_string(lib.size()) + " models so far; " + lib[found].name + " (" + lib[found].map + ", " +
             std::to_string(lib[found].nt) + " triangles)");
        lib_sel_key = preview_key = lib[found].key;
        // where: 12 m north of the first pillory (Fort Wolcott's courtyard), else the map's middle; on what is there
        V3 at = cam.pos + cam.forward() * 20.0f;
        bool spot = false;
        for (size_t i = 0; i < scene->entities.size() && !spot; i++)
            if (scene->entities[i].name.rfind("AC3MP_FORT_Pillory_01A", 0) == 0) { at = scene->entities[i].matrix.pos() + V3(0, 12, 0); spot = true; }
        if (!spot) { frame_all(); at = placement_point(); }
        float d = 0;
        if (pick(V3(at.x, at.y, at.z + 300), V3(0, 0, -1), &d) >= 0 && d < 1000) at.z = at.z + 300 - d;
        test_placed = place_entry(found, at);
        tlog("placed it at " + std::to_string(at.x) + ", " + std::to_string(at.y) + ", " + std::to_string(at.z) + ": " + status);
        test_t0 = ImGui::GetTime();
        test_step = 82;
        return;
    }
    case 82:
        if (asset_slot(test_placed) < 0) {
            if (ImGui::GetTime() - test_t0 > 600 || (!assets_loading && !engine.busy() && ImGui::GetTime() - test_t0 > 5 && preview_key.empty())) {
                tlog("FAIL: the model wasn't read from its map: " + status);
                test_exit = 1;
                test_step = 99;
            }
            return;
        }
        {
            const Mesh* me = mesh_of(test_placed);
            tlog("drawn from the asset scene: " + std::to_string(me ? me->nt : 0) + " triangles, " +
                 std::to_string(assets_scene->textures.size()) + " texture(s)");
            V3 lo, hi;
            world_box(test_placed, lo, hi);
            tlog("its box: " + std::to_string(hi.x - lo.x).substr(0, 5) + " x " + std::to_string(hi.y - lo.y).substr(0, 5) + " x " +
                 std::to_string(hi.z - lo.z).substr(0, 5) + " m, bottom at z " + std::to_string(lo.z).substr(0, 5));
            V3 c = (lo + hi) * 0.5f;
            cam.pos = c + V3(-0.6f, -1.0f, 0.55f) * std::max(6.0f, length(hi - lo) * 1.6f);
            V3 f = normalize(c - cam.pos);
            cam.yaw = std::atan2(f.y, f.x);
            cam.pitch = std::asin(f.z);
            focus_library = true;
        }
        test_wait = 12;
        test_step = 83;
        return;
    case 83:
        shot("5_placed.png");
        test_step = 84;
        return;
    case 84: {
        if (engine.busy()) return;
        save_project(false);
        std::ifstream f(widen(project_path), std::ios::binary);
        std::stringstream ss;
        ss << f.rdbuf();
        json j = json::parse(ss.str(), nullptr, false);
        int n = j.is_discarded() ? -1 : (int)j.value("places", json::array()).size();
        Edits back;
        back.reset(scene.get());
        std::string p = j.is_discarded() ? "unreadable" : back.apply_project(j);
        bool same = n == 1 && p.empty() && back.n_placed() == 1 && back.assets.size() == 1 &&
                    back.assets[0].key == edits.assets[edits.asset_of(test_placed)].key &&
                    back.cur.copies.back().matrix.near_equal(edits.matrix(test_placed), 1e-4f);
        tlog(std::string(same ? "ok" : "FAIL") + ": the project file keeps the library model (" + std::to_string(n) +
             " place(s)" + (p.empty() ? "" : "; " + p) + ")");
        if (!same) test_exit = 1;
        tlog("building with the library model...");
        build();
        focus_build = true;
        test_step = 85;
        return;
    }
    case 85:
        if (engine.busy()) return;
        focus_build = true;
        if (!last_build.is_object()) { tlog("FAIL: no build ran: " + status); test_exit = 1; }
        else if (last_build.contains("error")) { tlog("FAIL: build: " + last_build["error"].get<std::string>()); test_exit = 1; }
        else {
            bool lib_ok = false;
            for (auto& l : last_build.value("checks", json::array()))
                lib_ok |= l.get<std::string>().rfind("ok: 1 library model", 0) == 0;
            tlog(std::string(build_ok && lib_ok ? "PASS" : "FAIL") + ": built with the library model " + last_dest);
            for (auto& l : last_build.value("report", json::array())) tlog("  " + l.get<std::string>());
            for (auto& l : last_build.value("checks", json::array())) tlog("  " + l.get<std::string>());
            for (auto& l : last_build.value("warnings", json::array())) tlog("  WARNING " + l.get<std::string>());
            if (!(build_ok && lib_ok)) test_exit = 1;
        }
        test_wait = 6;
        test_step = 86;
        return;
    case 86:
        shot("6_built.png");
        test_wait = 3;
        test_step = spl_test ? 99 : 100;
        return;
    case 100: {
        // assets: the turned barracks closed with a wall, saved as an asset, edited, placed on another map
        test_building.clear();
        for (size_t i = 0; i < scene->entities.size(); i++)
            if (edits.cur.building[i] >= 0 && !edits.cur.removed[i] && scene->entities[i].mesh >= 0) test_building.push_back((int)i);
        if (test_building.empty()) { tlog("FAIL: no building pieces to save"); test_exit = 1; test_step = 99; return; }
        int piece = test_building[0];
        for (int id : test_building)
            if (scene->entities[id].name.rfind("AC3MP_FORT_Barracks_building", 0) == 0) { piece = id; break; }
        // the wall's material: the barracks' own (its model, read through the library)
        lib_sel_key = preview_key = scene->archive + "|" + uid_hex(scene->meshes[scene->entities[piece].mesh].uid);
        prim_m.clear();
        request_assets();
        tlog("stage 3: " + std::to_string(test_building.size()) + " barracks pieces; its wall material from " + scene->entities[piece].name);
        test_t0 = ImGui::GetTime();
        test_step = 101;
        return;
    }
    case 101: {
        auto it = assets_at.find(preview_key);
        if (it == assets_at.end() || it->second >= (int)asset_meta.size()) {
            if (ImGui::GetTime() - test_t0 > 600 || (!assets_error.empty() && !assets_loading)) {
                tlog("FAIL: the barracks' model wasn't read: " + assets_error); test_exit = 1; test_step = 99;
            }
            return;
        }
        if (!pick_prim_material()) { tlog("FAIL: the barracks' model has no textured material"); test_exit = 1; test_step = 99; return; }
        V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
        for (int id : test_building) { V3 a, b; world_box(id, a, b); lo = vmin(lo, a); hi = vmax(hi, b); }
        // along the building's north (+y) side, as long as the building, 6 m up from the ground there (its box goes
        // down to the foundations: the ground is found by a ray down 2.5 m out, clear of the eaves)
        float d = 0, base = lo.z;
        V3 probe((lo.x + hi.x) / 2, hi.y + 2.5f, hi.z + 50);
        if (pick(probe, V3(0, 0, -1), &d) >= 0 && d < 200) base = probe.z - d;
        test_wall = add_primitive("wall", V3((lo.x + hi.x) / 2, hi.y + 0.15f, base), 0.0f, hi.x - lo.x, 0.3, 6.0);
        if (test_wall < 0) { tlog("FAIL: no wall: " + status); test_exit = 1; test_step = 99; return; }
        tlog("added " + edits.assets[edits.asset_of(test_wall)].name + " in " + prim_mn + " along the barracks' north side");
        selection = test_building;
        selection.push_back(test_wall);
        test_t0 = ImGui::GetTime();
        test_step = 102;
        return;
    }
    case 102:
        if (asset_slot(test_wall) < 0) {
            if (ImGui::GetTime() - test_t0 > 300 || (!assets_error.empty() && !assets_loading)) {
                tlog("FAIL: the wall wasn't made: " + assets_error); test_exit = 1; test_step = 99;
            }
            return;
        }
        {
            V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
            for (int id : test_building) { V3 a, b; world_box(id, a, b); lo = vmin(lo, a); hi = vmax(hi, b); }
            cam.yaw = -1.25f;
            cam.pitch = -0.35f;
            frame_box(lo, hi);
            selection = {test_wall};
        }
        test_wait = 10;
        test_step = 103;
        return;
    case 103:
        shot("7_closed.png");
        project_path = test_dir + "\\test_closed.ac3proj";
        save_project(false);
        tlog("saved " + project_path + " (the turned barracks closed with the wall)");
        test_step = 104;
        return;
    case 104: {
        user_dir = test_dir + "\\assets";
        selection = test_building;
        selection.push_back(test_wall);
        test_asset_path = save_selection_asset("Barracks (turned, closed)");
        json j = read_json(test_asset_path);
        test_parts = j.is_discarded() ? -1 : (int)j["parts"].size();
        int prims = 0;
        if (!j.is_discarded())
            for (auto& p : j["parts"]) prims += p.contains("prim");
        bool ok = test_parts == (int)test_building.size() + 1 - blend_left_out && prims == 1;     // (blend markers left out)
        tlog(std::string(ok ? "ok" : "FAIL") + ": saved the selection as \"Barracks (turned, closed)\": " +
             std::to_string(test_parts) + " parts (" + std::to_string(prims) + " wall) - " + test_asset_path);
        if (!ok) { test_exit = 1; test_step = 99; return; }
        open_workspace("Barracks (turned, closed)", test_asset_path, j, -1);
        test_t0 = ImGui::GetTime();
        test_step = 105;
        return;
    }
    case 105: {
        int ready = 0;
        for (int id = 0; id < edits.count(); id++) ready += asset_slot(id) >= 0;
        if (ready < edits.count()) {
            if (ImGui::GetTime() - test_t0 > 900 || (!assets_error.empty() && !assets_loading && !engine.busy())) {
                tlog("FAIL: the asset's parts weren't read: " + assets_error); test_exit = 1; test_step = 99;
            }
            return;
        }
        frame_all();
        tlog("asset editor: " + std::to_string(edits.count()) + " parts drawn from the asset scene");
        test_wait = 10;
        test_step = 106;
        return;
    }
    case 106:
        shot("8_asset_editor.png");
        test_step = 120;
        return;
    case 120: {
        // an edit: a box beside it, saved; undone, saved again - the file follows
        V3 lo(1e30f, 1e30f, 1e30f), hi(-1e30f, -1e30f, -1e30f);
        for (int id = 0; id < edits.count(); id++) { V3 a, b; world_box(id, a, b); lo = vmin(lo, a); hi = vmax(hi, b); }
        add_primitive("box", V3(hi.x + 1.5f, 0, 0), 0.0f, 1, 1, 1);
        save_workspace(false);
        int with_box = (int)read_json(test_asset_path)["parts"].size();
        edits.undo();
        save_workspace(false);
        int after = (int)read_json(test_asset_path)["parts"].size();
        bool ok = with_box == test_parts + 1 && after == test_parts;
        tlog(std::string(ok ? "ok" : "FAIL") + ": asset editor - a box added and saved (" + std::to_string(with_box) +
             " parts), undone and saved (" + std::to_string(after) + ")");
        if (!ok) test_exit = 1;
        close_workspace();
        // nested: the barracks as an asset of assets - its EntityGroup pieces + a child asset per window / bench / ...
        // + the ground it stands on (the engine finds its footprint first)
        selection = test_building;
        selection.push_back(test_wall);
        test_ground_done = false;
        request_building_ground([this](const json& g) { test_ground = g; test_ground_done = true; });
        test_step = 121;
        return;
    }
    case 121: {
        if (!test_ground_done) return;
        if (!test_ground.is_object()) { tlog("FAIL: no ground under the barracks: " + status); test_exit = 1; test_step = 99; return; }
        tlog("the barracks' ground: a footprint of " + std::to_string(test_ground["footprint"].size()) + " corners, " +
             std::to_string(test_ground["exclude"].size()) + " of its own objects left out");
        int n_children = 0, n_kinds = 0;
        test_nested_path = make_building_asset("Barracks (nested)", &n_children, &n_kinds, test_ground);
        {
            json nj = read_json(test_nested_path);
            int shell = 0, kids = 0;
            if (!nj.is_discarded())
                for (auto& p : nj["parts"]) (p.contains("asset") ? kids : shell)++;
            std::vector<Leaf> flat;
            std::string prob;
            if (!nj.is_discarded()) flatten(nj, M4::identity(), "", flat, nullptr, 0, prob);
            int grounds = 0;
            for (auto& L : flat) grounds += is_ground_key(L.key);
            test_nested_total = (int)flat.size();
            int blends = blend_left_out, blend_parts = 0;
            for (auto& L : flat) blend_parts += is_blend_key(L.key);
            bool ok2 = kids == n_children && kids > 0 && n_kinds > 0 && grounds == 1 && blends + blend_parts >= 1 &&
                       (int)flat.size() == test_parts + 1 && prob.empty();
            tlog(std::to_string(blend_parts) + " blend group(s) in the asset as blend parts, " +
                 std::to_string(blends) + " left out (the blend groups not read yet)");
            tlog(std::string(ok2 ? "ok" : "FAIL") + ": Make asset from building - \"Barracks (nested)\": " + std::to_string(shell) +
                 " shell parts (with the ground) + " + std::to_string(kids) + " child assets of " + std::to_string(n_kinds) + " kinds; flattened " +
                 std::to_string(flat.size()) + " parts" + (prob.empty() ? "" : " (" + prob + ")"));
            if (!ok2) test_exit = 1;
        }
        // another map: Galata (from Galata: Fort Wolcott)
        std::string want = scene->title == "Galata" ? "Fort Wolcott" : "Galata", path;
        for (auto& m : maps)
            if (m.kind == "release" && m.title == want) path = m.path;
        if (path.empty()) { tlog("FAIL: no release map " + want); test_exit = 1; test_step = 99; return; }
        tlog("opening " + want + " to place the asset there");
        open_map(path);
        test_step = 107;
        return;
    }
    case 107:
        if (load_state == 4) { tlog("FAIL: the map didn't open: " + status); test_exit = 1; test_step = 99; return; }
        if (!scene || load_state != 0) return;
        {
            frame_all();
            V3 at = placement_point();
            float d = 0;
            if (pick(V3(at.x, at.y, at.z + 300), V3(0, 0, -1), &d) >= 0 && d < 1000) at.z = at.z + 300 - d;
            scan_user_assets();
            int k = -1;
            for (size_t i = 0; i < user_assets.size(); i++)
                if (user_assets[i].path == test_nested_path) k = (int)i;
            if (k < 0) { tlog("FAIL: the nested asset isn't in My assets"); test_exit = 1; test_step = 99; return; }
            std::vector<int> made = place_user_asset(user_assets[k].j, at, stem(user_assets[k].path));
            tlog("placed it on " + scene->title + " at " + std::to_string(at.x) + ", " + std::to_string(at.y) + ", " +
                 std::to_string(at.z) + ": " + status);
            test_t0 = ImGui::GetTime();
            test_step = 108;
        }
        return;
    case 108: {
        int ready = 0;
        for (int id : selection) ready += asset_slot(id) >= 0 || !edits.is_placed(id);
        if (ready < (int)selection.size()) {
            if (ImGui::GetTime() - test_t0 > 900 || (!assets_error.empty() && !assets_loading && !engine.busy())) {
                tlog("FAIL: the parts weren't drawn: " + assets_error); test_exit = 1; test_step = 99;
            }
            return;
        }
        cam.yaw = 0.8f;
        cam.pitch = -0.5f;
        frame_selection();
        test_wait = 10;
        test_step = 109;
        return;
    }
    case 109: {
        if (engine.busy()) return;                    // (build() refuses while the engine answers something else)
        shot("9_placed_elsewhere.png");
        {
            int g = (int)edits.groups.size() - 1;
            const GroupInfo& gi = edits.groups[g];
            auto part_named = [&](const char* word) {           // a child asset part whose name has `word`
                for (auto& [path, name] : gi.part_names)
                    if (segments(path) == 1 && contains_ci(name, word) && leaves_under(g, path).size() > 0) return path;
                return std::string();
            };
            std::string bench = part_named("Bench"), window = part_named("BuildingWindow");
            if (bench.empty() || window.empty()) { tlog("FAIL: no bench / window child in the placed asset"); test_exit = 1; test_step = 99; return; }
            // two-level selection: a click picks the whole asset, a double-click the window
            int leaf = leaves_under(g, window)[0];
            click_pick(leaf, false, false);
            size_t whole = selection.size();
            click_pick(leaf, false, true);
            size_t inner = selection.size();
            std::string lvl = scope_label();
            bool ok1 = (int)whole == test_nested_total && inner == leaves_under(g, window).size();
            tlog(std::string(ok1 ? "ok" : "FAIL") + ": click picks the asset (" + std::to_string(whole) + " parts), double-click goes in: " +
                 lvl + " (" + std::to_string(inner) + ")");
            if (!ok1) test_exit = 1;
            // overrides on this placement: the window moved 0.6 m along x, one bench removed
            std::vector<int> movers = selection;
            std::vector<M4> start;
            for (int id : movers) start.push_back(edits.matrix(id));
            M4 p0 = pivot_of(movers), p1 = p0;
            p1.set_pos(p0.pos() + V3(0.6f, 0, 0));
            apply_pivot(movers, start, p0, p1);
            edits.commit();
            selection = leaves_under(g, bench);
            int bench_parts = (int)selection.size();
            remove_selected();
            scope_up();
            tlog("overrides: " + gi.part_names.at(window) + " (" + window + ") moved 0.6 m, " + gi.part_names.at(bench) + " (" + bench +
                 ") removed - " + std::to_string(bench_parts) + " part(s)");
            // Esc goes back up to the whole asset; B selects it too
            click_pick(leaf, false, false);
            size_t now = selection.size();
            bool ok3 = (int)now == test_nested_total - bench_parts;
            tlog(std::string(ok3 ? "ok" : "FAIL") + ": the placed asset now has " + std::to_string(now) + " parts");
            if (!ok3) test_exit = 1;
            // Apply to asset: the bench gone from the definition, the window's part moved 0.6 m
            json before = read_json(test_nested_path);
            std::vector<std::string> problems;
            apply_to_asset(g, problems);
            json after = read_json(test_nested_path);
            M4 wb, wa;
            for (auto& p : before["parts"]) if (p.value("id", std::string()) == window) mat_from(p["matrix"], wb);
            for (auto& p : after["parts"]) if (p.value("id", std::string()) == window) mat_from(p["matrix"], wa);
            float moved = length(wa.pos() - wb.pos());
            bool bench_gone = true;
            for (auto& p : after["parts"]) bench_gone &= p.value("id", std::string()) != bench;
            bool ok4 = after["parts"].size() + 1 == before["parts"].size() && bench_gone && std::fabs(moved - 0.6f) < 0.01f && problems.empty();
            tlog(std::string(ok4 ? "ok" : "FAIL") + ": Apply to asset - " + std::to_string(before["parts"].size()) + " -> " +
                 std::to_string(after["parts"].size()) + " parts, the window's part moved " + std::to_string(moved) + " m" +
                 (problems.empty() ? "" : ", problems: " + problems[0]));
            if (!ok4) test_exit = 1;
            // climbing: each part as in its own map (Fort Wolcott's climb data), the wall generated, the ground none;
            // and one of this map's own buildings made climbable
            int by[4] = {0, 0, 0, 0};
            for (int id : leaves_under(g, "")) {
                const CopyObj& c = edits.cur.copies[id - scene->entities.size()];
                by[c.climb >= 0 ? c.climb : c.asset >= 0 ? default_climb(edits.assets[c.asset].key) : 0]++;
            }
            int best = -1;
            size_t most = 0;
            for (size_t i = 0; i < scene->entities.size(); i++) {
                const Entity& e = scene->entities[i];
                if (e.mesh < 0 || e.shapes.empty() || (e.flags & (F_FIXED | F_SPAWN | F_MODE)) || e.name.find("Wall") == std::string::npos ||
                    scene->records[e.record].name.rfind("Cell", 0) != 0) continue;
                if (scene->meshes[e.mesh].nt > most) { most = scene->meshes[e.mesh].nt; best = (int)i; }
            }
            if (best >= 0) edits.cur.climbable[best] = 1;
            tlog("climbing: " + std::to_string(by[0]) + " part(s) as in their own map, " + std::to_string(by[1]) + " generated, " +
                 std::to_string(by[2]) + " none; " + (best >= 0 ? scene->entities[best].name + " (this map's) made climbable" : std::string("no wall to make climbable")));
            if (best < 0 || by[1] < 1 || by[2] < 1) test_exit = 1;
            // the barracks climbable outside - every part but the ground and the "none" ones; and one of this
            // map's own buildings (an EntityGroup's biggest piece) climbable outside in place
            int outside = 0;
            for (int id : leaves_under(g, "")) {
                CopyObj& c = edits.cur.copies[id - scene->entities.size()];
                int eff = c.climb >= 0 ? c.climb : c.asset >= 0 ? default_climb(edits.assets[c.asset].key) : 0;
                if (eff == CLIMB_NONE) continue;
                c.climb = CLIMB_OUTSIDE;
                outside++;
            }
            int bld = -1;       // the biggest house (Galata's EntityGroups are backdrops; its houses are single objects)
            most = 0;
            for (size_t i = 0; i < scene->entities.size(); i++) {
                const Entity& e = scene->entities[i];
                if ((int)i == best || e.mesh < 0 || e.shapes.empty() || e.name.find("House") == std::string::npos || (e.flags & (F_FIXED | F_SPAWN | F_MODE)) ||
                    scene->records[e.record].name.rfind("Cell", 0) != 0) continue;
                if (scene->meshes[e.mesh].nt > most) { most = scene->meshes[e.mesh].nt; bld = (int)i; }
            }
            if (bld >= 0) edits.cur.climbable[bld] = 2;
            edits.commit();
            tlog("climbable outside: " + std::to_string(outside) + " part(s) of the barracks; " +
                 (bld >= 0 ? scene->entities[bld].name + " (this map's) in place"
                           : std::string("no building of this map")));
            if (outside < 1 || bld < 0) test_exit = 1;
        }
        project_path = test_dir + "\\test_elsewhere.ac3proj";
        save_project(false);
        tlog("building " + scene->title + " with the asset...");
        build();
        focus_build = true;
        test_step = 110;
        return;
    }
    case 110:
        if (engine.busy()) return;
        focus_build = true;
        if (!last_build.is_object()) { tlog("FAIL: no build ran: " + status); test_exit = 1; }
        else if (last_build.contains("error")) { tlog("FAIL: build: " + last_build["error"].get<std::string>()); test_exit = 1; }
        else {
            bool lib_ok = false;
            for (auto& l : last_build.value("checks", json::array()))
                lib_ok |= l.get<std::string>().rfind("ok: " + std::to_string(edits.n_placed()) + " library model", 0) == 0;
            bool climb_ok = false;
            for (auto& l : last_build.value("checks", json::array()))
                climb_ok |= l.get<std::string>().find("made climbable:") != std::string::npos;
            lib_ok &= climb_ok;
            int outside_layers = 0;                    // the barracks' and the map building's
            for (auto& l : last_build.value("checks", json::array()))
                outside_layers += l.get<std::string>().rfind("ok: climbable outside ", 0) == 0;
            if (outside_layers < 2) { tlog("FAIL: " + std::to_string(outside_layers) + " climbable-outside layer(s) checked, not 2"); lib_ok = false; }
            tlog(std::string(build_ok && lib_ok ? "PASS" : "FAIL") + ": built " + scene->title + " with the asset " + last_dest);
            for (auto& l : last_build.value("report", json::array())) tlog("  " + l.get<std::string>());
            for (auto& l : last_build.value("checks", json::array())) tlog("  " + l.get<std::string>());
            for (auto& l : last_build.value("warnings", json::array())) tlog("  WARNING " + l.get<std::string>());
            if (!(build_ok && lib_ok)) test_exit = 1;
        }
        test_wait = 6;
        test_step = 111;
        return;
    case 111:
        shot("10_elsewhere_built.png");
        test_wait = 3;
        test_step = 99;
        return;
    case 99:
        tlog(test_exit ? "TEST FAILED" : "TEST PASSED");
        test_step = -1;
        quit_now = true;
        return;
    }
}

// a PNG with stored (uncompressed) deflate blocks: no library needed
static void write_png(const std::string& path, int w, int h, const uint8_t* rgba, int pitch) {
    std::vector<uint8_t> raw;
    raw.reserve((size_t)(w * 4 + 1) * h);
    for (int y = 0; y < h; y++) {
        raw.push_back(0);
        for (int x = 0; x < w; x++) {
            const uint8_t* p = rgba + y * pitch + x * 4;
            raw.insert(raw.end(), {p[0], p[1], p[2], 255});
        }
    }
    auto crc = [](const uint8_t* d, size_t n, uint32_t c = 0xFFFFFFFFu) {
        for (size_t i = 0; i < n; i++) {
            c ^= d[i];
            for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1)));
        }
        return c;
    };
    std::vector<uint8_t> z = {0x78, 0x01};
    uint32_t a = 1, b = 0;
    for (uint8_t v : raw) { a = (a + v) % 65521; b = (b + a) % 65521; }
    for (size_t off = 0; off < raw.size(); off += 65535) {
        uint16_t n = (uint16_t)std::min<size_t>(65535, raw.size() - off);
        z.push_back(off + n >= raw.size() ? 1 : 0);
        z.insert(z.end(), {(uint8_t)n, (uint8_t)(n >> 8), (uint8_t)~n, (uint8_t)(~n >> 8)});
        z.insert(z.end(), raw.begin() + off, raw.begin() + off + n);
    }
    uint32_t ad = (b << 16) | a;
    z.insert(z.end(), {(uint8_t)(ad >> 24), (uint8_t)(ad >> 16), (uint8_t)(ad >> 8), (uint8_t)ad});
    std::ofstream f(widen(path), std::ios::binary);
    auto be = [&](uint32_t v) { uint8_t q[4] = {(uint8_t)(v >> 24), (uint8_t)(v >> 16), (uint8_t)(v >> 8), (uint8_t)v}; f.write((char*)q, 4); };
    auto chunk = [&](const char* type, const std::vector<uint8_t>& d) {
        be((uint32_t)d.size());
        std::vector<uint8_t> t(type, type + 4);
        t.insert(t.end(), d.begin(), d.end());
        f.write((char*)t.data(), t.size());
        be(crc(t.data(), t.size()) ^ 0xFFFFFFFFu);
    };
    f.write("\x89PNG\r\n\x1a\n", 8);
    std::vector<uint8_t> ihdr = {(uint8_t)(w >> 24), (uint8_t)(w >> 16), (uint8_t)(w >> 8), (uint8_t)w,
                                 (uint8_t)(h >> 24), (uint8_t)(h >> 16), (uint8_t)(h >> 8), (uint8_t)h, 8, 6, 0, 0, 0};
    chunk("IHDR", ihdr);
    chunk("IDAT", z);
    chunk("IEND", {});
}

static void save_backbuffer(App& a, const std::string& path) {
    ID3D11Texture2D* bb = nullptr;
    a.swap->GetBuffer(0, IID_PPV_ARGS(&bb));
    D3D11_TEXTURE2D_DESC d;
    bb->GetDesc(&d);
    d.Usage = D3D11_USAGE_STAGING; d.BindFlags = 0; d.CPUAccessFlags = D3D11_CPU_ACCESS_READ; d.MiscFlags = 0;
    ID3D11Texture2D* st = nullptr;
    if (SUCCEEDED(a.dev->CreateTexture2D(&d, nullptr, &st))) {
        a.ctx->CopyResource(st, bb);
        D3D11_MAPPED_SUBRESOURCE ms;
        if (SUCCEEDED(a.ctx->Map(st, 0, D3D11_MAP_READ, 0, &ms))) {
            write_png(path, d.Width, d.Height, (const uint8_t*)ms.pData, ms.RowPitch);
            a.ctx->Unmap(st, 0);
        }
        st->Release();
    }
    bb->Release();
}

// -------------------------------------------------------------------------------------------- Win32 / D3D --

static App* g_app = nullptr;

static void create_back_rtv(App& a) {
    ID3D11Texture2D* bb = nullptr;
    a.swap->GetBuffer(0, IID_PPV_ARGS(&bb));
    a.dev->CreateRenderTargetView(bb, nullptr, &a.back_rtv);
    bb->Release();
}

static LRESULT WINAPI wnd_proc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    if (ImGui_ImplWin32_WndProcHandler(h, msg, wp, lp)) return true;
    switch (msg) {
    case WM_SIZE:
        if (g_app && g_app->dev && wp != SIZE_MINIMIZED) {
            if (g_app->back_rtv) { g_app->back_rtv->Release(); g_app->back_rtv = nullptr; }
            g_app->swap->ResizeBuffers(0, LOWORD(lp), HIWORD(lp), DXGI_FORMAT_UNKNOWN, 0);
            create_back_rtv(*g_app);
        }
        return 0;
    case WM_CLOSE:
        if (g_app && ((g_app->scene && g_app->edits.dirty()) || g_app->chr_unbuilt()) && !g_app->quit_now) {
            g_app->want_quit = true;
            return 0;
        }
        break;
    case WM_SYSCOMMAND:
        if ((wp & 0xfff0) == SC_KEYMENU) return 0;      // (Alt alone mustn't open the window menu: Alt + drag orbits)
        break;
    case WM_DESTROY:
        if (g_app) g_app->save_settings(true);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(h, msg, wp, lp);
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, PWSTR, int) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    App app;
    g_app = &app;
    WNDCLASSEXW wc{sizeof wc, CS_CLASSDC, wnd_proc, 0, 0, inst, LoadIconW(inst, MAKEINTRESOURCEW(1)), LoadCursorW(nullptr, MAKEINTRESOURCEW(32512)),
                   nullptr, nullptr, L"ReforgedStudio", nullptr};
    RegisterClassExW(&wc);
    app.hwnd = CreateWindowW(wc.lpszClassName, L"Reforged Studio", WS_OVERLAPPEDWINDOW, 80, 60, 1600, 960, nullptr, nullptr, inst, nullptr);

    DXGI_SWAP_CHAIN_DESC sd{};
    sd.BufferCount = 2;
    sd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.OutputWindow = app.hwnd;
    sd.SampleDesc.Count = 1;
    sd.Windowed = TRUE;
    sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    D3D_FEATURE_LEVEL fl;
    const D3D_FEATURE_LEVEL want[] = {D3D_FEATURE_LEVEL_11_0};
    if (FAILED(D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, want, 1, D3D11_SDK_VERSION,
                                             &sd, &app.swap, &app.dev, &fl, &app.ctx))) {
        MessageBoxW(nullptr, L"Direct3D 11 isn't available on this PC.", L"Reforged Studio", MB_ICONERROR);
        return 1;
    }
    create_back_rtv(app);
    ShowWindow(app.hwnd, SW_SHOWMAXIMIZED);
    UpdateWindow(app.hwnd);

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.ConfigFlags |= ImGuiConfigFlags_DockingEnable | ImGuiConfigFlags_NavEnableKeyboard;
    static std::string ini;
    {
        wchar_t buf[MAX_PATH];
        GetModuleFileNameW(nullptr, buf, MAX_PATH);
        std::string exe = narrow(buf);
        ini = exe.substr(0, exe.find_last_of("\\/")) + "\\studio_layout.ini";
        io.IniFilename = ini.c_str();
    }
    float dpi = (float)GetDpiForWindow(app.hwnd) / 96.0f;
    if (file_exists("C:\\Windows\\Fonts\\segoeui.ttf")) io.Fonts->AddFontFromFileTTF("C:\\Windows\\Fonts\\segoeui.ttf", 16.0f * dpi);
    style();
    ImGui::GetStyle().ScaleAllSizes(dpi);
    ImGui_ImplWin32_Init(app.hwnd);
    ImGui_ImplDX11_Init(app.dev, app.ctx);

    std::string err;
    if (!app.renderer.init(app.dev, app.ctx, err)) {
        MessageBoxW(nullptr, widen(err).c_str(), L"Reforged Studio", MB_ICONERROR);
        return 1;
    }
    app.load_settings();
    app.start_engine();
    {
        int argc = 0;
        wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
        for (int i = 1; i < argc; i++)
            if (std::wstring(argv[i]) == L"--asset" && i + 2 < argc) {
                app.test_asset = narrow(argv[i + 1]);
                app.test_asset_map = narrow(argv[i + 2]);
                i += 2;
            } else if (std::wstring(argv[i]) == L"--test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                if (i + 3 < argc && std::wstring(argv[i + 3]).rfind(L"--", 0) != 0) app.test_piece = narrow(argv[i + 3]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "\\test.txt"));
                app.full_log.open(widen(app.test_dir + "\\log.txt"));
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--nav-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "\\test.txt"));
                app.nav_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--chars-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.chr.test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--spa-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.spa.test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--import-test" && i + 2 < argc) {
                // <a map to open first> <out> [<GAME>:<map key>, default ACR:souk]
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                app.imp.test_pick = i + 3 < argc && std::wstring(argv[i + 3]).rfind(L"--", 0) != 0 ? narrow(argv[i + 3]) : "ACR:souk";
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.imp.test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--spl-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.spl_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--bare-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.ba_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--scatter-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.sc_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--walk-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.walk_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--tg-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.tg_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--setup-test" && i + 2 < argc) {
                // the first start of a release tree, on a fresh config of the test's own
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                std::wstring cfg = std::wstring(argv[i + 2]) + L"\\config.json";
                DeleteFileW(cfg.c_str());
                SetEnvironmentVariableW(L"REFORGED_STUDIO_CONFIG", cfg.c_str());
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.setup_test = true;
                app.test_step = 3000;
            }
            else if (std::wstring(argv[i]) == L"--maps-watch-test" && i + 2 < argc) {
                // <a map archive to add> <out>: the config's game folder must be a test folder (studio_test_folder.txt)
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.maps_watch_test = true;
                app.test_step = 3100;
            }
            else if (std::wstring(argv[i]) == L"--ui-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.ui_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--sculpt-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.gs_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--lc-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.lc_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--sa-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                if (i + 3 < argc && std::wstring(argv[i + 3]).rfind(L"--", 0) != 0) app.test_piece = narrow(argv[i + 3]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.sa_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--climb-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.climb_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--live-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "/test.txt"));
                app.full_log.open(widen(app.test_dir + "/log.txt"));
                app.live_test = true;
                app.test_step = 0;
            }
            else if ((std::wstring(argv[i]) == L"--blend-test" || std::wstring(argv[i]) == L"--blend-set-test") && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "\\test.txt"));
                app.full_log.open(widen(app.test_dir + "\\log.txt"));
                app.blend_test = true;
                app.blend_set_test = std::wstring(argv[i]) == L"--blend-set-test";
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--ms2-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "\\test.txt"));
                app.full_log.open(widen(app.test_dir + "\\log.txt"));
                app.ms2_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--ms1-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "\\test.txt"));
                app.full_log.open(widen(app.test_dir + "\\log.txt"));
                app.ms1_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--region-test" && i + 2 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "\\test.txt"));
                app.region_test = true;
                app.test_step = 0;
            }
            else if (std::wstring(argv[i]) == L"--shots" && i + 6 < argc) {
                app.test_archive = narrow(argv[i + 1]);
                app.test_dir = narrow(argv[i + 2]);
                make_dirs(argv[i + 2]);
                app.test_log.open(widen(app.test_dir + "\\test.txt"));
                app.shots_at = V3((float)_wtof(argv[i + 3]), (float)_wtof(argv[i + 4]), (float)_wtof(argv[i + 5]));
                app.shots_dist = (float)_wtof(argv[i + 6]);
                app.shots_mode = true;
                if (i + 7 < argc && std::wstring(argv[i + 7]) == L"--ground-only") app.ground_only = true;
                app.test_step = 0;
            }
        LocalFree(argv);
    }

    bool done = false;
    while (!done) {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
            if (msg.message == WM_QUIT) done = true;
        }
        if (done) break;
        if (app.quit_now) { DestroyWindow(app.hwnd); app.quit_now = false; continue; }
        if (IsIconic(app.hwnd)) {
            // a self-test needs frames (engine replies, screenshots): minimised, it would wait for ever - back up,
            // without taking the keyboard
            if (app.test_step >= 0) ShowWindow(app.hwnd, SW_SHOWNOACTIVATE);
            Sleep(30);
            continue;
        }
        // (an unexpected error is written to the Log - and --test's log.txt - before the program stops on it)
        const char* where = "the engine's replies";
        try {
            app.engine.poll();
            app.finish_loading();

            ImGui_ImplDX11_NewFrame();
            ImGui_ImplWin32_NewFrame();
            ImGui::NewFrame();
            ImGuizmo::BeginFrame();
            where = "the frame";
            app.shortcuts();
            app.test_tick();
            app.ui();
        } catch (const std::exception& e) {
            app.add_log(std::string("ERROR in ") + where + ": " + e.what());
            app.tlog(std::string("FAIL: error in ") + where + ": " + e.what());
            throw;
        }
        ImGui::Render();
        float clear[4] = {0.07f, 0.07f, 0.08f, 1};
        app.ctx->OMSetRenderTargets(1, &app.back_rtv, nullptr);
        app.ctx->ClearRenderTargetView(app.back_rtv, clear);
        ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
        if (!app.shot_path.empty()) { save_backbuffer(app, app.shot_path); app.shot_path.clear(); }
        app.swap->Present(1, 0);
    }
    if (app.loader.joinable()) app.loader.join();
    app.engine.stop();
    app.renderer.shutdown();
    ImGui_ImplDX11_Shutdown();
    ImGui_ImplWin32_Shutdown();
    ImGui::DestroyContext();
    if (app.back_rtv) app.back_rtv->Release();
    app.swap->Release();
    app.ctx->Release();
    app.dev->Release();
    return app.test_exit;
}

// the Map panel and the map-wide gameplay edits (App's own code)
#include "mapedit.inc"
// the Characters workspace
#include "charui.inc"
// the first-run setup
#include "setup.inc"
