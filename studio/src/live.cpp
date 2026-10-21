#include <winsock2.h>
#include <ws2tcpip.h>
#include "live.h"
#include <chrono>
#include "json.hpp"

#pragma comment(lib, "ws2_32.lib")

using json = nlohmann::json;

double LiveListener::now() const {
    using namespace std::chrono;
    return duration<double>(steady_clock::now().time_since_epoch()).count();
}

bool LiveListener::parse(const std::string& text, LiveFrame& f) {
    json j = json::parse(text, nullptr, false);
    if (j.is_discarded() || !j.is_object() || j.value("v", 0) != 1) return false;
    auto v3 = [](const json& a) { return V3(a[0].get<float>(), a[1].get<float>(), a[2].get<float>()); };
    auto fwd = [](const json& a) { return a.size() >= 5 ? V3(a[3].get<float>(), a[4].get<float>(), 0) : V3(1, 0, 0); };
    try {
        f.seq = j.value("seq", 0ul);
        f.world = j.value("world", std::string());
        f.has_me = j.contains("me") && j["me"].is_array() && j["me"].size() >= 3;
        if (f.has_me) { f.me = v3(j["me"]); f.me_fwd = fwd(j["me"]); }
        f.players.clear();
        f.players_fwd.clear();
        for (auto& p : j.value("players", json::array())) { f.players.push_back(v3(p)); f.players_fwd.push_back(fwd(p)); }
        f.npcs.clear();
        for (auto& p : j.value("npcs", json::array())) f.npcs.push_back(v3(p));
        f.marks.clear();
        for (auto& p : j.value("marks", json::array())) f.marks.push_back(v3(p));
        auto sc = j.value("scan", json::array());
        if (sc.size() >= 3) { f.scan_found = sc[0].get<int>(); f.scan_ms = sc[1].get<int>(); f.scan_age = sc[2].get<int>(); }
    } catch (...) {
        return false;
    }
    return true;
}

bool LiveListener::start(int port, std::string& error) {
    if (run_) return true;
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa)) { error = "WSAStartup failed"; return false; }
    SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s == INVALID_SOCKET) { error = "no UDP socket"; return false; }
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons((u_short)port);
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(s, (sockaddr*)&a, sizeof a) != 0) {
        error = "port " + std::to_string(port) + " is in use (another Studio listening?)";
        closesocket(s);
        return false;
    }
    DWORD tmo = 250;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tmo, sizeof tmo);
    sock_ = (uintptr_t)s;
    port_ = port;
    {
        std::lock_guard<std::mutex> g(mu_);
        frame_ = LiveFrame();
    }
    run_ = true;
    th_ = std::thread([this] { loop(); });
    return true;
}

void LiveListener::stop() {
    if (!run_) return;
    run_ = false;
    if (th_.joinable()) th_.join();
    closesocket((SOCKET)sock_);
    sock_ = ~(uintptr_t)0;
}

void LiveListener::loop() {
    std::string buf(65536, '\0');
    while (run_) {
        int n = recv((SOCKET)sock_, buf.data(), (int)buf.size(), 0);
        if (n <= 0) continue;
        LiveFrame f;
        bool ok = parse(buf.substr(0, n), f);
        std::lock_guard<std::mutex> g(mu_);
        if (!ok) { frame_.bad++; continue; }
        f.received = now();
        f.datagrams = frame_.datagrams + 1;
        f.bad = frame_.bad;
        frame_ = std::move(f);
    }
}

LiveFrame LiveListener::snapshot() {
    std::lock_guard<std::mutex> g(mu_);
    return frame_;
}
