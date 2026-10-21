// Direct3D 11 drawing of a Scene: instanced meshes (one draw per mesh piece for all its placements), DXT textures
// as stored, collision wireframes, markers, selection outlines, and free lines. Draws into its own render targets
// (the viewport's, the asset library's preview), which the windows show. Two scenes can be on the GPU at once: the
// open map (SLOT_MAP) and the asset library's models (SLOT_ASSETS).
#pragma once
#include <d3d11.h>
#include <string>
#include <vector>
#include "scene.h"

struct Instance {          // per-placement data (vertex buffer slot 1)
    M4 matrix;
    float color[4];        // rgb tint, a = how much of it (0 = the texture as it is)
};

struct DrawItem {          // one placement to draw this frame
    int mesh;              // -1: a marker
    M4 matrix;
    float color[4];
    bool outline;          // selected: an orange wire over it
    bool wire;             // a gameplay volume: wire only
};

struct LineVert { V3 p; uint32_t rgba; };

enum { SLOT_MAP = 0, SLOT_ASSETS = 1, SLOT_CHAR = 2, SLOTS = 3 };   // (SLOT_CHAR: the character viewer)              // scenes on the GPU
enum { TARGET_VIEWPORT = 0, TARGET_PREVIEW = 1, TARGET_CHAR = 2, TARGETS = 3 };  // render targets

struct SceneGPU {
    struct MeshGPU { ID3D11Buffer* vb = nullptr; ID3D11Buffer* ib = nullptr; };
    struct ShapeGPU { ID3D11Buffer* vb = nullptr; ID3D11Buffer* ib = nullptr; uint32_t edges = 0; };
    std::vector<MeshGPU> mesh;
    std::vector<ShapeGPU> shape;
    std::vector<ID3D11ShaderResourceView*> tex;
    std::vector<int> material_tex;      // material -> texture view index or -1
    std::vector<float> material_tint;   // material -> grey level for untextured ones
    std::vector<uint8_t> material_clip; // material -> its colour map's alpha is see-through (decals, leaves...)
    size_t meshes = 0, texture_bytes = 0;
    int textures_loaded = 0, textures_failed = 0;
};

struct Renderer {
    ID3D11Device* dev = nullptr;
    ID3D11DeviceContext* ctx = nullptr;

    bool init(ID3D11Device* d, ID3D11DeviceContext* c, std::string& error);
    void shutdown();

    // scene resources (upload may run on a worker thread: device calls only)
    bool upload(const Scene& s, std::string& error, int* progress = nullptr, int slot = SLOT_MAP);
    void release_scene(int slot = SLOT_MAP);
    bool scene_ready(int slot = SLOT_MAP) const { return !sets[slot].mesh.empty() || sets[slot].meshes == 0; }
    size_t texture_bytes = 0;                    // (the map's: SLOT_MAP)
    int textures_loaded = 0, textures_failed = 0;

    // the frame (or the library's preview: target TARGET_PREVIEW)
    void resize(int w, int h, int target = TARGET_VIEWPORT);
    void begin(const M4& view, const M4& proj, V3 cam, int target = TARGET_VIEWPORT);
    void draw_scene(const Scene& s, const std::vector<DrawItem>& items, bool collision, const std::vector<int>& col_ents,
                    const std::vector<M4>& col_mats, bool alpha_clip, int slot = SLOT_MAP);
    void draw_lines(const std::vector<LineVert>& lines, bool on_top);
    void draw_tris(const std::vector<LineVert>& tris, bool on_top);      // flat (translucent) triangles, 3 vertices each
    void draw_free(const std::vector<LineVert>& v, bool on_top, bool tris);
    void end();
    bool update_vertices(int slot, int mesh, const float* xyzuv, size_t vertices);   // (SLOT_CHAR only)
    ID3D11ShaderResourceView* output(int target = TARGET_VIEWPORT) const { return targets[target].srv; }
    int width = 0, height = 0;                   // (the viewport's)

private:
    SceneGPU sets[SLOTS];
    struct Target {
        ID3D11Texture2D* rt = nullptr; ID3D11RenderTargetView* rtv = nullptr; ID3D11ShaderResourceView* srv = nullptr;
        ID3D11Texture2D* depth = nullptr; ID3D11DepthStencilView* dsv = nullptr;
        int w = 0, h = 0;
    };
    Target targets[TARGETS];
    ID3D11VertexShader* vs_mesh = nullptr; ID3D11VertexShader* vs_line = nullptr; ID3D11VertexShader* vs_free = nullptr;
    ID3D11PixelShader* ps_lit = nullptr; ID3D11PixelShader* ps_flat = nullptr; ID3D11PixelShader* ps_free = nullptr;
    ID3D11InputLayout* il_mesh = nullptr; ID3D11InputLayout* il_line = nullptr; ID3D11InputLayout* il_free = nullptr;
    ID3D11Buffer* cb_frame = nullptr; ID3D11Buffer* cb_mat = nullptr;
    ID3D11Buffer* inst = nullptr; size_t inst_cap = 0;
    ID3D11Buffer* free_vb = nullptr; size_t free_cap = 0;
    ID3D11Buffer* marker_vb = nullptr; ID3D11Buffer* marker_ib = nullptr;
    ID3D11RasterizerState* rs_solid = nullptr; ID3D11RasterizerState* rs_wire = nullptr;
    ID3D11DepthStencilState* ds_on = nullptr; ID3D11DepthStencilState* ds_read = nullptr; ID3D11DepthStencilState* ds_off = nullptr;
    ID3D11BlendState* bs_alpha = nullptr;
    ID3D11SamplerState* sampler = nullptr;
    void ensure_instances(size_t n);
    void set_material(const SceneGPU& g, int material, bool alpha_clip);
};

ID3D11ShaderResourceView* load_dds(ID3D11Device* dev, const std::string& path, size_t* bytes, std::string& error);
