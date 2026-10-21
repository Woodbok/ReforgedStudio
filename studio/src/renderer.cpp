#include "renderer.h"
#include <d3dcompiler.h>
#include <cstdio>
#include <unordered_map>
#include <unordered_set>

#define RELEASE(x) do { if (x) { (x)->Release(); (x) = nullptr; } } while (0)

namespace {
const char* SHADERS = R"HLSL(
cbuffer Frame : register(b0) { row_major float4x4 viewProj; float4 camPos; float4 sun; };
cbuffer Mat : register(b1) { float4 base; float4 matp; };   // matp.x: has a texture, matp.y: alpha clip
Texture2D tex : register(t0);
SamplerState smp : register(s0);

struct VSMesh { float3 pos : POSITION; float2 uv : TEXCOORD0;
                float4 r0 : INST0; float4 r1 : INST1; float4 r2 : INST2; float4 r3 : INST3; float4 tint : COLOR0; };
struct VSLine { float3 pos : POSITION;
                float4 r0 : INST0; float4 r1 : INST1; float4 r2 : INST2; float4 r3 : INST3; float4 tint : COLOR0; };
struct VSFree { float3 pos : POSITION; float4 col : COLOR0; };
struct PSIn { float4 pos : SV_Position; float3 wp : WORLDPOS; float2 uv : TEXCOORD0; float4 tint : COLOR0; };

PSIn vs_mesh(VSMesh i) {
    float4x4 w = float4x4(i.r0, i.r1, i.r2, i.r3);
    float4 wp = mul(float4(i.pos, 1), w);
    PSIn o; o.wp = wp.xyz; o.pos = mul(wp, viewProj); o.uv = i.uv; o.tint = i.tint; return o;
}
PSIn vs_line(VSLine i) {
    float4x4 w = float4x4(i.r0, i.r1, i.r2, i.r3);
    float4 wp = mul(float4(i.pos, 1), w);
    PSIn o; o.wp = wp.xyz; o.pos = mul(wp, viewProj); o.uv = 0; o.tint = i.tint; return o;
}
PSIn vs_free(VSFree i) {
    PSIn o; o.wp = i.pos; o.pos = mul(float4(i.pos, 1), viewProj); o.uv = 0; o.tint = i.col; return o;
}
float3 shade(float3 albedo, float3 wp) {
    float3 n = normalize(cross(ddy(wp), ddx(wp)));
    float3 v = camPos.xyz - wp;
    if (dot(n, v) < 0) n = -n;
    float hemi = lerp(0.42, 0.78, saturate(n.z * 0.5 + 0.5));
    float d = saturate(dot(n, normalize(sun.xyz))) * 0.55;
    float3 c = albedo * (hemi + d);
    float fog = 1 - exp(-length(v) * 0.0012);
    return lerp(c, float3(0.30, 0.34, 0.40), fog * 0.6);
}
float4 ps_lit(PSIn i) : SV_Target {
    float4 a = base;
    if (matp.x > 0.5) {
        a = tex.Sample(smp, i.uv);
        if (matp.y > 0.5) clip(a.a - 0.3);
    }
    float3 c = shade(a.rgb, i.wp);
    return float4(lerp(c, i.tint.rgb, i.tint.a), 1);
}
float4 ps_flat(PSIn i) : SV_Target { return float4(i.tint.rgb, 1); }
float4 ps_free(PSIn i) : SV_Target { return i.tint; }
)HLSL";

struct FrameCB { M4 view_proj; float cam[4]; float sun[4]; };
struct MatCB { float base[4]; float matp[4]; };

bool compile(const char* entry, const char* target, ID3DBlob** out, std::string& error) {
    ID3DBlob* err = nullptr;
    HRESULT hr = D3DCompile(SHADERS, strlen(SHADERS), "studio.hlsl", nullptr, nullptr, entry, target,
                            D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, out, &err);
    if (FAILED(hr)) {
        error = std::string("shader ") + entry + ": " + (err ? (const char*)err->GetBufferPointer() : "failed");
        RELEASE(err);
        return false;
    }
    RELEASE(err);
    return true;
}

ID3D11Buffer* make_buffer(ID3D11Device* dev, const void* data, size_t bytes, UINT bind, bool dynamic = false) {
    if (!bytes) return nullptr;
    D3D11_BUFFER_DESC d{};
    d.ByteWidth = (UINT)bytes;
    d.Usage = dynamic ? D3D11_USAGE_DYNAMIC : D3D11_USAGE_IMMUTABLE;      // (dynamic: the character slot's, skinned on the CPU)
    d.CPUAccessFlags = dynamic ? D3D11_CPU_ACCESS_WRITE : 0;
    d.BindFlags = bind;
    D3D11_SUBRESOURCE_DATA s{data};
    ID3D11Buffer* b = nullptr;
    dev->CreateBuffer(&d, &s, &b);
    return b;
}

uint32_t hash_name(const std::string& s) {
    uint32_t h = 2166136261u;
    for (char c : s) h = (h ^ (uint8_t)c) * 16777619u;
    return h;
}
}  // namespace

ID3D11ShaderResourceView* load_dds(ID3D11Device* dev, const std::string& path, size_t* bytes, std::string& error) {
    int n = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, w.data(), n);
    FILE* f = _wfopen(w.c_str(), L"rb");
    if (!f) { error = "missing"; return nullptr; }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> d(size > 0 ? size : 0);
    size_t got = fread(d.data(), 1, d.size(), f);
    fclose(f);
    if (got < 128 || memcmp(d.data(), "DDS ", 4) != 0) { error = "not a DDS file"; return nullptr; }
    uint32_t h, wdt, mips, fourcc;
    memcpy(&h, &d[12], 4); memcpy(&wdt, &d[16], 4); memcpy(&mips, &d[28], 4); memcpy(&fourcc, &d[84], 4);
    DXGI_FORMAT fmt;
    UINT block;
    uint32_t pf_flags, bits, rmask;
    memcpy(&pf_flags, &d[80], 4); memcpy(&bits, &d[88], 4); memcpy(&rmask, &d[92], 4);
    if (!fourcc && (pf_flags & 0x40) && bits == 32 && rmask == 0x000000ff) {
        // (an uncompressed RGBA picture - the engine's previews - one level)
        if (!wdt || !h || 128 + (size_t)wdt * h * 4 > d.size()) { error = "no image data"; return nullptr; }
        D3D11_SUBRESOURCE_DATA sd{d.data() + 128, wdt * 4, 0};
        D3D11_TEXTURE2D_DESC td{};
        td.Width = wdt; td.Height = h; td.MipLevels = 1; td.ArraySize = 1; td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        td.SampleDesc.Count = 1; td.Usage = D3D11_USAGE_IMMUTABLE; td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        ID3D11Texture2D* t = nullptr;
        if (FAILED(dev->CreateTexture2D(&td, &sd, &t))) { error = "CreateTexture2D failed"; return nullptr; }
        ID3D11ShaderResourceView* v = nullptr;
        dev->CreateShaderResourceView(t, nullptr, &v);
        t->Release();
        if (bytes) *bytes += (size_t)wdt * h * 4;
        return v;
    }
    if (fourcc == MAKEFOURCC('D', 'X', 'T', '1')) { fmt = DXGI_FORMAT_BC1_UNORM; block = 8; }
    else if (fourcc == MAKEFOURCC('D', 'X', 'T', '3')) { fmt = DXGI_FORMAT_BC2_UNORM; block = 16; }
    else if (fourcc == MAKEFOURCC('D', 'X', 'T', '5')) { fmt = DXGI_FORMAT_BC3_UNORM; block = 16; }
    else { error = "format not DXT1/3/5"; return nullptr; }
    if (!wdt || !h || (wdt % 4) || (h % 4)) { error = "size not a multiple of 4"; return nullptr; }
    mips = std::max(1u, mips);
    std::vector<D3D11_SUBRESOURCE_DATA> sub;
    size_t off = 128;
    UINT mw = wdt, mh = h, kept = 0;
    for (UINT i = 0; i < mips; i++) {
        UINT pitch = std::max(1u, (mw + 3) / 4) * block;
        size_t lvl = (size_t)pitch * std::max(1u, (mh + 3) / 4);
        if (off + lvl > d.size()) break;
        sub.push_back({d.data() + off, pitch, 0});
        off += lvl;
        kept++;
        mw = std::max(1u, mw / 2);
        mh = std::max(1u, mh / 2);
    }
    if (!kept) { error = "no image data"; return nullptr; }
    D3D11_TEXTURE2D_DESC td{};
    td.Width = wdt; td.Height = h; td.MipLevels = kept; td.ArraySize = 1; td.Format = fmt;
    td.SampleDesc.Count = 1; td.Usage = D3D11_USAGE_IMMUTABLE; td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    ID3D11Texture2D* t = nullptr;
    if (FAILED(dev->CreateTexture2D(&td, sub.data(), &t))) { error = "CreateTexture2D failed"; return nullptr; }
    ID3D11ShaderResourceView* v = nullptr;
    dev->CreateShaderResourceView(t, nullptr, &v);
    t->Release();
    if (bytes) *bytes += off - 128;
    return v;
}

bool Renderer::init(ID3D11Device* d, ID3D11DeviceContext* c, std::string& error) {
    dev = d; ctx = c;
    ID3DBlob *b_vm, *b_vl, *b_vf, *b_pl, *b_pf, *b_pfr;
    if (!compile("vs_mesh", "vs_5_0", &b_vm, error) || !compile("vs_line", "vs_5_0", &b_vl, error) ||
        !compile("vs_free", "vs_5_0", &b_vf, error) || !compile("ps_lit", "ps_5_0", &b_pl, error) ||
        !compile("ps_flat", "ps_5_0", &b_pf, error) || !compile("ps_free", "ps_5_0", &b_pfr, error))
        return false;
    dev->CreateVertexShader(b_vm->GetBufferPointer(), b_vm->GetBufferSize(), nullptr, &vs_mesh);
    dev->CreateVertexShader(b_vl->GetBufferPointer(), b_vl->GetBufferSize(), nullptr, &vs_line);
    dev->CreateVertexShader(b_vf->GetBufferPointer(), b_vf->GetBufferSize(), nullptr, &vs_free);
    dev->CreatePixelShader(b_pl->GetBufferPointer(), b_pl->GetBufferSize(), nullptr, &ps_lit);
    dev->CreatePixelShader(b_pf->GetBufferPointer(), b_pf->GetBufferSize(), nullptr, &ps_flat);
    dev->CreatePixelShader(b_pfr->GetBufferPointer(), b_pfr->GetBufferSize(), nullptr, &ps_free);
    D3D11_INPUT_ELEMENT_DESC inst_el[] = {
        {"INST", 0, DXGI_FORMAT_R32G32B32A32_FLOAT, 1, 0, D3D11_INPUT_PER_INSTANCE_DATA, 1},
        {"INST", 1, DXGI_FORMAT_R32G32B32A32_FLOAT, 1, 16, D3D11_INPUT_PER_INSTANCE_DATA, 1},
        {"INST", 2, DXGI_FORMAT_R32G32B32A32_FLOAT, 1, 32, D3D11_INPUT_PER_INSTANCE_DATA, 1},
        {"INST", 3, DXGI_FORMAT_R32G32B32A32_FLOAT, 1, 48, D3D11_INPUT_PER_INSTANCE_DATA, 1},
        {"COLOR", 0, DXGI_FORMAT_R32G32B32A32_FLOAT, 1, 64, D3D11_INPUT_PER_INSTANCE_DATA, 1},
    };
    D3D11_INPUT_ELEMENT_DESC mesh_el[7] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 12, D3D11_INPUT_PER_VERTEX_DATA, 0},
    };
    for (int i = 0; i < 5; i++) mesh_el[2 + i] = inst_el[i];
    D3D11_INPUT_ELEMENT_DESC line_el[6] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
    };
    for (int i = 0; i < 5; i++) line_el[1 + i] = inst_el[i];
    D3D11_INPUT_ELEMENT_DESC free_el[] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 0, DXGI_FORMAT_R8G8B8A8_UNORM, 0, 12, D3D11_INPUT_PER_VERTEX_DATA, 0},
    };
    dev->CreateInputLayout(mesh_el, 7, b_vm->GetBufferPointer(), b_vm->GetBufferSize(), &il_mesh);
    dev->CreateInputLayout(line_el, 6, b_vl->GetBufferPointer(), b_vl->GetBufferSize(), &il_line);
    dev->CreateInputLayout(free_el, 2, b_vf->GetBufferPointer(), b_vf->GetBufferSize(), &il_free);
    for (auto* b : {b_vm, b_vl, b_vf, b_pl, b_pf, b_pfr}) b->Release();

    D3D11_BUFFER_DESC cb{};
    cb.Usage = D3D11_USAGE_DYNAMIC; cb.BindFlags = D3D11_BIND_CONSTANT_BUFFER; cb.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    cb.ByteWidth = sizeof(FrameCB);
    dev->CreateBuffer(&cb, nullptr, &cb_frame);
    cb.ByteWidth = sizeof(MatCB);
    dev->CreateBuffer(&cb, nullptr, &cb_mat);

    D3D11_RASTERIZER_DESC rd{};
    rd.FillMode = D3D11_FILL_SOLID; rd.CullMode = D3D11_CULL_NONE; rd.DepthClipEnable = TRUE;
    dev->CreateRasterizerState(&rd, &rs_solid);
    rd.FillMode = D3D11_FILL_WIREFRAME; rd.DepthBias = -200; rd.SlopeScaledDepthBias = -1.0f; rd.AntialiasedLineEnable = TRUE;
    dev->CreateRasterizerState(&rd, &rs_wire);
    D3D11_DEPTH_STENCIL_DESC dd{};
    dd.DepthEnable = TRUE; dd.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL; dd.DepthFunc = D3D11_COMPARISON_LESS_EQUAL;
    dev->CreateDepthStencilState(&dd, &ds_on);
    dd.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
    dev->CreateDepthStencilState(&dd, &ds_read);
    dd.DepthEnable = FALSE;
    dev->CreateDepthStencilState(&dd, &ds_off);
    D3D11_BLEND_DESC bd{};
    bd.RenderTarget[0].BlendEnable = TRUE;
    bd.RenderTarget[0].SrcBlend = D3D11_BLEND_SRC_ALPHA; bd.RenderTarget[0].DestBlend = D3D11_BLEND_INV_SRC_ALPHA;
    bd.RenderTarget[0].BlendOp = D3D11_BLEND_OP_ADD;
    bd.RenderTarget[0].SrcBlendAlpha = D3D11_BLEND_ONE; bd.RenderTarget[0].DestBlendAlpha = D3D11_BLEND_ZERO;
    bd.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
    bd.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    dev->CreateBlendState(&bd, &bs_alpha);
    D3D11_SAMPLER_DESC sd{};
    sd.Filter = D3D11_FILTER_ANISOTROPIC; sd.MaxAnisotropy = 8;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_WRAP;
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    dev->CreateSamplerState(&sd, &sampler);

    // the marker: an octahedron 1 m across (uv 0)
    float mv[] = {0, 0, 0.5f, 0, 0,  0.5f, 0, 0, 0, 0,  0, 0.5f, 0, 0, 0,  -0.5f, 0, 0, 0, 0,
                  0, -0.5f, 0, 0, 0,  0, 0, -0.5f, 0, 0};
    uint32_t mi[] = {0, 1, 2, 0, 2, 3, 0, 3, 4, 0, 4, 1, 5, 2, 1, 5, 3, 2, 5, 4, 3, 5, 1, 4};
    marker_vb = make_buffer(dev, mv, sizeof mv, D3D11_BIND_VERTEX_BUFFER);
    marker_ib = make_buffer(dev, mi, sizeof mi, D3D11_BIND_INDEX_BUFFER);
    return true;
}

void Renderer::release_scene(int slot) {
    SceneGPU& g = sets[slot];
    for (auto& m : g.mesh) { RELEASE(m.vb); RELEASE(m.ib); }
    for (auto& s : g.shape) { RELEASE(s.vb); RELEASE(s.ib); }
    for (auto*& t : g.tex) RELEASE(t);
    g = SceneGPU();
    if (slot == SLOT_MAP) { texture_bytes = 0; textures_loaded = textures_failed = 0; }
}

bool Renderer::upload(const Scene& s, std::string& error, int* progress, int slot) {
    std::vector<SceneGPU::MeshGPU> meshes(s.meshes.size());
    std::vector<SceneGPU::ShapeGPU> shapes(s.shapes.size());
    std::vector<ID3D11ShaderResourceView*> views(s.textures.size(), nullptr);
    size_t bytes = 0;
    int ok = 0, bad = 0;
    for (size_t i = 0; i < s.textures.size(); i++) {
        std::string err;
        views[i] = load_dds(dev, s.textures[i], &bytes, err);
        (views[i] ? ok : bad)++;
        if (progress) *progress = (int)(i * 80 / std::max<size_t>(1, s.textures.size()));
    }
    std::vector<float> inter;
    for (size_t i = 0; i < s.meshes.size(); i++) {
        const Mesh& m = s.meshes[i];
        inter.resize((size_t)m.nv * 5);
        for (uint32_t v = 0; v < m.nv; v++) {
            memcpy(&inter[v * 5], m.pos + v * 3, 12);
            memcpy(&inter[v * 5 + 3], m.uv + v * 2, 8);
        }
        meshes[i].vb = make_buffer(dev, inter.data(), inter.size() * 4, D3D11_BIND_VERTEX_BUFFER, slot == SLOT_CHAR);
        meshes[i].ib = make_buffer(dev, m.idx, (size_t)m.nt * 12, D3D11_BIND_INDEX_BUFFER);
        if (progress) *progress = 80 + (int)(i * 15 / std::max<size_t>(1, s.meshes.size()));
    }
    for (size_t i = 0; i < s.shapes.size(); i++) {
        const Shape& sh = s.shapes[i];
        std::unordered_set<uint64_t> seen;
        std::vector<uint32_t> edges;
        for (uint32_t t = 0; t < sh.nt; t++)
            for (int k = 0; k < 3; k++) {
                uint32_t a = sh.idx[t * 3 + k], b = sh.idx[t * 3 + (k + 1) % 3];
                uint64_t key = a < b ? ((uint64_t)a << 32) | b : ((uint64_t)b << 32) | a;
                if (seen.insert(key).second) { edges.push_back(a); edges.push_back(b); }
            }
        shapes[i].vb = make_buffer(dev, sh.pos, (size_t)sh.nv * 12, D3D11_BIND_VERTEX_BUFFER);
        shapes[i].ib = make_buffer(dev, edges.data(), edges.size() * 4, D3D11_BIND_INDEX_BUFFER);
        shapes[i].edges = (uint32_t)edges.size() / 2;
    }
    release_scene(slot);
    SceneGPU& g = sets[slot];
    g.mesh.swap(meshes);
    g.shape.swap(shapes);
    g.tex.swap(views);
    g.material_tex.resize(s.materials.size());
    g.material_tint.resize(s.materials.size());
    g.material_clip.resize(s.materials.size());
    for (size_t i = 0; i < s.materials.size(); i++) {
        int t = s.materials[i].texture;
        g.material_tex[i] = (t >= 0 && t < (int)g.tex.size() && g.tex[t]) ? t : -1;
        g.material_tint[i] = 0.45f + (hash_name(s.materials[i].name) % 100) / 400.0f;
        // AC3 keeps other things in most colour maps' alpha (blend masks on walls and ground): only materials named
        // as see-through cut it out
        std::string n = s.materials[i].name;
        for (auto& ch : n) ch = (char)tolower((unsigned char)ch);
        static const char* SEE_THROUGH[] = {"decal", "leaf", "leaves", "foliage", "branch", "grass", "ivy", "vine",
                                            "plant", "fern", "bush", "alpha", "fence", "chain", "net", "hair", "flag",
                                            "cloth", "rope", "web", "tree", "hay", "straw", "railing", "grid", "grate"};
        g.material_clip[i] = 0;
        for (const char* w : SEE_THROUGH)
            if (n.find(w) != std::string::npos) g.material_clip[i] = 1;
    }
    g.texture_bytes = bytes;
    g.textures_loaded = ok;
    g.textures_failed = bad;
    g.meshes = s.meshes.size();
    if (slot == SLOT_MAP) { texture_bytes = bytes; textures_loaded = ok; textures_failed = bad; }
    if (progress) *progress = 100;
    (void)error;
    return true;
}

void Renderer::resize(int w, int h, int target) {
    Target& t = targets[target];
    w = std::max(16, w); h = std::max(16, h);
    if (w == t.w && h == t.h && t.rt) return;
    RELEASE(t.srv); RELEASE(t.rtv); RELEASE(t.rt); RELEASE(t.dsv); RELEASE(t.depth);
    t.w = w; t.h = h;
    if (target == TARGET_VIEWPORT) { width = w; height = h; }
    D3D11_TEXTURE2D_DESC td{};
    td.Width = w; td.Height = h; td.MipLevels = 1; td.ArraySize = 1; td.SampleDesc.Count = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM; td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    dev->CreateTexture2D(&td, nullptr, &t.rt);
    dev->CreateRenderTargetView(t.rt, nullptr, &t.rtv);
    dev->CreateShaderResourceView(t.rt, nullptr, &t.srv);
    td.Format = DXGI_FORMAT_D32_FLOAT; td.BindFlags = D3D11_BIND_DEPTH_STENCIL;
    dev->CreateTexture2D(&td, nullptr, &t.depth);
    dev->CreateDepthStencilView(t.depth, nullptr, &t.dsv);
}

void Renderer::ensure_instances(size_t n) {
    if (n <= inst_cap && inst) return;
    RELEASE(inst);
    inst_cap = std::max<size_t>(n + n / 2, 4096);
    D3D11_BUFFER_DESC d{};
    d.ByteWidth = (UINT)(inst_cap * sizeof(Instance));
    d.Usage = D3D11_USAGE_DYNAMIC; d.BindFlags = D3D11_BIND_VERTEX_BUFFER; d.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    dev->CreateBuffer(&d, nullptr, &inst);
}

void Renderer::begin(const M4& view, const M4& proj, V3 cam, int target) {
    Target& t = targets[target];
    float clear[4] = {0.30f, 0.34f, 0.40f, 1.0f};
    if (target == TARGET_PREVIEW) { clear[0] = 0.16f; clear[1] = 0.17f; clear[2] = 0.19f; }
    ctx->OMSetRenderTargets(1, &t.rtv, t.dsv);
    ctx->ClearRenderTargetView(t.rtv, clear);
    ctx->ClearDepthStencilView(t.dsv, D3D11_CLEAR_DEPTH, 1.0f, 0);
    D3D11_VIEWPORT vp{0, 0, (float)t.w, (float)t.h, 0, 1};
    ctx->RSSetViewports(1, &vp);
    D3D11_MAPPED_SUBRESOURCE ms;
    if (SUCCEEDED(ctx->Map(cb_frame, 0, D3D11_MAP_WRITE_DISCARD, 0, &ms))) {
        FrameCB* f = (FrameCB*)ms.pData;
        f->view_proj = view * proj;
        f->cam[0] = cam.x; f->cam[1] = cam.y; f->cam[2] = cam.z; f->cam[3] = 1;
        f->sun[0] = 0.35f; f->sun[1] = 0.25f; f->sun[2] = 0.9f; f->sun[3] = 0;
        ctx->Unmap(cb_frame, 0);
    }
    ctx->VSSetConstantBuffers(0, 1, &cb_frame);
    ctx->PSSetConstantBuffers(0, 1, &cb_frame);
    ctx->PSSetConstantBuffers(1, 1, &cb_mat);
    ctx->PSSetSamplers(0, 1, &sampler);
    ctx->OMSetBlendState(nullptr, nullptr, 0xFFFFFFFF);
}

void Renderer::set_material(const SceneGPU& gs, int material, bool alpha_clip) {
    D3D11_MAPPED_SUBRESOURCE ms;
    ID3D11ShaderResourceView* v = nullptr;
    float g = 0.55f;
    if (material >= 0 && material < (int)gs.material_tex.size()) {
        if (gs.material_tex[material] >= 0) v = gs.tex[gs.material_tex[material]];
        g = gs.material_tint[material];
    }
    if (SUCCEEDED(ctx->Map(cb_mat, 0, D3D11_MAP_WRITE_DISCARD, 0, &ms))) {
        MatCB* m = (MatCB*)ms.pData;
        m->base[0] = g; m->base[1] = g; m->base[2] = g * 0.96f; m->base[3] = 1;
        bool clip = alpha_clip && material >= 0 && material < (int)gs.material_clip.size() && gs.material_clip[material];
        m->matp[0] = v ? 1.0f : 0.0f; m->matp[1] = clip ? 1.0f : 0.0f; m->matp[2] = m->matp[3] = 0;
        ctx->Unmap(cb_mat, 0);
    }
    ctx->PSSetShaderResources(0, 1, &v);
}

void Renderer::draw_scene(const Scene& s, const std::vector<DrawItem>& items, bool collision,
                          const std::vector<int>& col_ents, const std::vector<M4>& col_mats, bool alpha_clip, int slot) {
    const SceneGPU& g = sets[slot];
    const auto& mesh_gpu = g.mesh;
    const auto& shape_gpu = g.shape;
    // group placements by mesh: [solid per mesh][markers][outlines per mesh][volumes per mesh][collision per shape]
    size_t nm = std::min(s.meshes.size(), mesh_gpu.size());      // (a scene still uploading draws nothing)
    std::vector<std::vector<Instance>> solid(nm), outline(nm), wire(nm);
    std::vector<Instance> markers;
    for (const DrawItem& it : items) {
        Instance in{it.matrix, {it.color[0], it.color[1], it.color[2], it.color[3]}};
        if (it.mesh < 0 || it.mesh >= (int)nm || !mesh_gpu[it.mesh].vb) { markers.push_back(in); continue; }
        if (it.wire) wire[it.mesh].push_back(in);
        else solid[it.mesh].push_back(in);
        if (it.outline) {
            Instance o{it.matrix, {1.0f, 0.55f, 0.15f, 1}};
            outline[it.mesh].push_back(o);
        }
    }
    std::vector<std::vector<Instance>> shapes(std::min(s.shapes.size(), shape_gpu.size()));
    if (collision)
        for (size_t k = 0; k < col_ents.size(); k++) {
            const Entity& e = s.entities[col_ents[k]];
            for (int sh : e.shapes)
                if (sh >= 0 && sh < (int)shapes.size()) {
                    bool solidl = s.shapes[sh].layer == 1;
                    shapes[sh].push_back({col_mats[k], {solidl ? 0.2f : 0.9f, solidl ? 0.85f : 0.4f,
                                                        solidl ? 1.0f : 0.9f, 1}});
                }
        }
    size_t total = markers.size();
    for (size_t i = 0; i < nm; i++) total += solid[i].size() + outline[i].size() + wire[i].size();
    for (auto& v : shapes) total += v.size();
    if (!total) return;
    ensure_instances(total);
    D3D11_MAPPED_SUBRESOURCE ms;
    if (FAILED(ctx->Map(inst, 0, D3D11_MAP_WRITE_DISCARD, 0, &ms))) return;
    Instance* dst = (Instance*)ms.pData;
    size_t at = 0;
    auto put = [&](const std::vector<Instance>& v) { size_t start = at; memcpy(dst + at, v.data(), v.size() * sizeof(Instance)); at += v.size(); return start; };
    std::vector<size_t> s_solid(nm), s_outline(nm), s_wire(nm), s_shape(shapes.size());
    for (size_t i = 0; i < nm; i++) s_solid[i] = put(solid[i]);
    size_t s_mark = put(markers);
    for (size_t i = 0; i < nm; i++) s_outline[i] = put(outline[i]);
    for (size_t i = 0; i < nm; i++) s_wire[i] = put(wire[i]);
    for (size_t i = 0; i < shapes.size(); i++) s_shape[i] = put(shapes[i]);
    ctx->Unmap(inst, 0);

    UINT strides[2] = {20, sizeof(Instance)}, offs[2] = {0, 0};
    ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ctx->IASetInputLayout(il_mesh);
    ctx->VSSetShader(vs_mesh, nullptr, 0);
    ctx->RSSetState(rs_solid);
    ctx->OMSetDepthStencilState(ds_on, 0);
    ctx->PSSetShader(ps_lit, nullptr, 0);
    for (size_t i = 0; i < nm; i++) {
        if (solid[i].empty()) continue;
        ID3D11Buffer* bufs[2] = {mesh_gpu[i].vb, inst};
        ctx->IASetVertexBuffers(0, 2, bufs, strides, offs);
        ctx->IASetIndexBuffer(mesh_gpu[i].ib, DXGI_FORMAT_R32_UINT, 0);
        for (const Sub& sb : s.meshes[i].subs) {
            set_material(g, sb.material, alpha_clip);
            ctx->DrawIndexedInstanced(sb.count * 3, (UINT)solid[i].size(), sb.first * 3, 0, (UINT)s_solid[i]);
        }
    }
    if (!markers.empty()) {
        ID3D11Buffer* bufs[2] = {marker_vb, inst};
        ctx->IASetVertexBuffers(0, 2, bufs, strides, offs);
        ctx->IASetIndexBuffer(marker_ib, DXGI_FORMAT_R32_UINT, 0);
        set_material(g, -1, false);
        ctx->DrawIndexedInstanced(24, (UINT)markers.size(), 0, 0, (UINT)s_mark);
    }
    // wire passes: volumes, selection outlines (flat colour, depth tested, no depth writes)
    ctx->RSSetState(rs_wire);
    ctx->OMSetDepthStencilState(ds_read, 0);
    ctx->PSSetShader(ps_flat, nullptr, 0);
    for (int pass = 0; pass < 2; pass++)
        for (size_t i = 0; i < nm; i++) {
            auto& v = pass == 0 ? wire[i] : outline[i];
            if (v.empty()) continue;
            ID3D11Buffer* bufs[2] = {mesh_gpu[i].vb, inst};
            ctx->IASetVertexBuffers(0, 2, bufs, strides, offs);
            ctx->IASetIndexBuffer(mesh_gpu[i].ib, DXGI_FORMAT_R32_UINT, 0);
            ctx->DrawIndexedInstanced(s.meshes[i].nt * 3, (UINT)v.size(), 0, 0,
                                      (UINT)(pass == 0 ? s_wire[i] : s_outline[i]));
        }
    if (collision) {
        ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_LINELIST);
        ctx->IASetInputLayout(il_line);
        ctx->VSSetShader(vs_line, nullptr, 0);
        UINT ls[2] = {12, sizeof(Instance)};
        for (size_t i = 0; i < shapes.size(); i++) {
            if (shapes[i].empty() || !shape_gpu[i].vb || !shape_gpu[i].ib) continue;
            ID3D11Buffer* bufs[2] = {shape_gpu[i].vb, inst};
            ctx->IASetVertexBuffers(0, 2, bufs, ls, offs);
            ctx->IASetIndexBuffer(shape_gpu[i].ib, DXGI_FORMAT_R32_UINT, 0);
            ctx->DrawIndexedInstanced(shape_gpu[i].edges * 2, (UINT)shapes[i].size(), 0, 0, (UINT)s_shape[i]);
        }
    }
    ctx->RSSetState(rs_solid);
}

void Renderer::draw_lines(const std::vector<LineVert>& lines, bool on_top) { draw_free(lines, on_top, false); }
void Renderer::draw_tris(const std::vector<LineVert>& tris, bool on_top) { draw_free(tris, on_top, true); }

void Renderer::draw_free(const std::vector<LineVert>& lines, bool on_top, bool tris) {
    if (lines.empty()) return;
    if (lines.size() > free_cap || !free_vb) {
        RELEASE(free_vb);
        free_cap = std::max<size_t>(lines.size() * 2, 4096);
        D3D11_BUFFER_DESC d{};
        d.ByteWidth = (UINT)(free_cap * sizeof(LineVert));
        d.Usage = D3D11_USAGE_DYNAMIC; d.BindFlags = D3D11_BIND_VERTEX_BUFFER; d.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        dev->CreateBuffer(&d, nullptr, &free_vb);
    }
    D3D11_MAPPED_SUBRESOURCE ms;
    if (FAILED(ctx->Map(free_vb, 0, D3D11_MAP_WRITE_DISCARD, 0, &ms))) return;
    memcpy(ms.pData, lines.data(), lines.size() * sizeof(LineVert));
    ctx->Unmap(free_vb, 0);
    UINT stride = sizeof(LineVert), off = 0;
    ctx->IASetPrimitiveTopology(tris ? D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST : D3D11_PRIMITIVE_TOPOLOGY_LINELIST);
    ctx->IASetInputLayout(il_free);
    ctx->IASetVertexBuffers(0, 1, &free_vb, &stride, &off);
    ctx->VSSetShader(vs_free, nullptr, 0);
    ctx->PSSetShader(ps_free, nullptr, 0);
    ctx->RSSetState(rs_solid);
    ctx->OMSetDepthStencilState(on_top ? ds_off : ds_read, 0);
    ctx->OMSetBlendState(bs_alpha, nullptr, 0xFFFFFFFF);
    ctx->Draw((UINT)lines.size(), 0);
    ctx->OMSetBlendState(nullptr, nullptr, 0xFFFFFFFF);
}

void Renderer::end() {
    ID3D11RenderTargetView* none = nullptr;
    ctx->OMSetRenderTargets(1, &none, nullptr);
}

void Renderer::shutdown() {
    for (int s = 0; s < SLOTS; s++) release_scene(s);
    for (Target& t : targets) { RELEASE(t.srv); RELEASE(t.rtv); RELEASE(t.rt); RELEASE(t.dsv); RELEASE(t.depth); }
    RELEASE(vs_mesh); RELEASE(vs_line); RELEASE(vs_free); RELEASE(ps_lit); RELEASE(ps_flat); RELEASE(ps_free);
    RELEASE(il_mesh); RELEASE(il_line); RELEASE(il_free); RELEASE(cb_frame); RELEASE(cb_mat);
    RELEASE(inst); RELEASE(free_vb); RELEASE(marker_vb); RELEASE(marker_ib);
    RELEASE(rs_solid); RELEASE(rs_wire); RELEASE(ds_on); RELEASE(ds_read); RELEASE(ds_off);
    RELEASE(bs_alpha); RELEASE(sampler);
}

// a character mesh's vertices rewritten (x y z u v per vertex: the slot's interleaved layout) - skinned on the CPU
bool Renderer::update_vertices(int slot, int mesh, const float* xyzuv, size_t vertices) {
    if (slot != SLOT_CHAR || mesh < 0 || mesh >= (int)sets[slot].mesh.size() || !sets[slot].mesh[mesh].vb) return false;
    D3D11_MAPPED_SUBRESOURCE ms;
    if (FAILED(ctx->Map(sets[slot].mesh[mesh].vb, 0, D3D11_MAP_WRITE_DISCARD, 0, &ms))) return false;
    memcpy(ms.pData, xyzuv, vertices * 5 * sizeof(float));
    ctx->Unmap(sets[slot].mesh[mesh].vb, 0);
    return true;
}
