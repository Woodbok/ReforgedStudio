// The Python engine (studio/engine/worker.py) as a child process: JSON lines over pipes, replies handed to
// callbacks on the UI thread (poll() each frame).
#pragma once
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>
#include "json.hpp"

using json = nlohmann::json;

struct Engine {
    using Reply = std::function<void(bool ok, const json& result, const std::string& error)>;

    bool start(const std::string& python, const std::string& repo, std::string& error);
    void stop();
    bool running() const { return proc != nullptr; }
    int request(const std::string& cmd, json args, Reply reply);   // -> request id
    void poll();                                                    // dispatch replies and log lines
    bool busy() const { return !pending.empty(); }
    std::string busy_with() const;

    std::vector<std::string> log;                                   // everything the engine said
    std::function<void(const std::string&)> on_log;
    ~Engine() { stop(); }

private:
    void* proc = nullptr;
    void* in_w = nullptr;     // our end of its stdin
    void* out_r = nullptr;    // our end of its stdout
    std::thread reader;
    std::mutex mu;
    std::vector<std::string> lines;
    std::unordered_map<int, std::pair<std::string, Reply>> pending;
    int next_id = 1;
    bool exited = false;
};
