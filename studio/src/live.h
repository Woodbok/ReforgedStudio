// The live match view: the game loader's livemap.c (lever live_map) sends the player, the crowd NPCs, the other
// players and F9 marks as one UDP datagram (JSON) ten times a second to 127.0.0.1:47710; this listens.
#pragma once
#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include "math3d.h"

struct LiveFrame {
    bool has_me = false;
    V3 me, me_fwd;                       // position, facing (x, y; z 0)
    std::vector<V3> players, players_fwd;
    std::vector<V3> npcs;
    std::vector<V3> marks;
    std::string world;                   // the World's address (hex): a new one = another map / round
    unsigned long seq = 0;
    int scan_found = 0, scan_ms = 0, scan_age = 0;
    double received = 0;                 // when (seconds, the listener's clock); 0 = never
    int datagrams = 0, bad = 0;
};

class LiveListener {
public:
    ~LiveListener() { stop(); }
    bool start(int port, std::string& error);
    void stop();
    bool running() const { return run_; }
    LiveFrame snapshot();                // the latest frame (and the counters)
    double now() const;                  // the clock `received` is on
    int port() const { return port_; }
    // (for tests) parse one datagram into a frame -> ok
    static bool parse(const std::string& text, LiveFrame& f);

private:
    void loop();
    std::thread th_;
    std::atomic<bool> run_{false};
    std::mutex mu_;
    LiveFrame frame_;
    uintptr_t sock_ = ~(uintptr_t)0;
    int port_ = 0;
};
