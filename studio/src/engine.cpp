#include "engine.h"
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

static std::wstring widen(const std::string& s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), n);
    if (!w.empty() && w.back() == 0) w.pop_back();
    return w;
}

bool Engine::start(const std::string& python, const std::string& repo, std::string& error) {
    SECURITY_ATTRIBUTES sa{sizeof sa, nullptr, TRUE};
    HANDLE in_r, out_w, iw, orr;
    if (!CreatePipe(&in_r, &iw, &sa, 0) || !CreatePipe(&orr, &out_w, &sa, 0)) {
        error = "can't make pipes";
        return false;
    }
    SetHandleInformation(iw, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(orr, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOW si{sizeof si};
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = in_r;
    si.hStdOutput = out_w;
    si.hStdError = out_w;        // tracebacks the worker can't catch still reach the log
    PROCESS_INFORMATION pi{};
    std::wstring cmd = L"\"" + widen(python) + L"\" -u \"" + widen(repo) + L"\\studio\\engine\\worker.py\"";
    // UTF-8 both ways, whatever the console code page
    SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    BOOL ok = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr,
                             widen(repo).c_str(), &si, &pi);
    CloseHandle(in_r);
    CloseHandle(out_w);
    if (!ok) {
        CloseHandle(iw);
        CloseHandle(orr);
        error = "can't start the engine: " + python + " (set AC3STUDIO_PYTHON to your python.exe)";
        return false;
    }
    CloseHandle(pi.hThread);
    proc = pi.hProcess;
    in_w = iw;
    out_r = orr;
    exited = false;
    reader = std::thread([this] {
        std::string buf;
        char chunk[65536];
        DWORD got;
        while (ReadFile((HANDLE)out_r, chunk, sizeof chunk, &got, nullptr) && got) {
            buf.append(chunk, got);
            size_t nl;
            while ((nl = buf.find('\n')) != std::string::npos) {
                std::string line = buf.substr(0, nl);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                buf.erase(0, nl + 1);
                std::lock_guard<std::mutex> g(mu);
                lines.push_back(std::move(line));
            }
        }
        std::lock_guard<std::mutex> g(mu);
        exited = true;
    });
    return true;
}

void Engine::stop() {
    if (!proc) return;
    if (in_w) {
        const char* q = "{\"id\":0,\"cmd\":\"quit\"}\n";
        DWORD w;
        WriteFile((HANDLE)in_w, q, (DWORD)strlen(q), &w, nullptr);
        CloseHandle((HANDLE)in_w);
        in_w = nullptr;
    }
    if (WaitForSingleObject((HANDLE)proc, 3000) != WAIT_OBJECT_0) TerminateProcess((HANDLE)proc, 1);
    if (reader.joinable()) reader.join();
    CloseHandle((HANDLE)out_r);
    CloseHandle((HANDLE)proc);
    out_r = nullptr;
    proc = nullptr;
}

int Engine::request(const std::string& cmd, json args, Reply reply) {
    int id = next_id++;
    args["id"] = id;
    args["cmd"] = cmd;
    std::string line = args.dump() + "\n";
    pending[id] = {cmd, std::move(reply)};
    DWORD w;
    if (!in_w || !WriteFile((HANDLE)in_w, line.data(), (DWORD)line.size(), &w, nullptr)) {
        auto r = std::move(pending[id].second);
        pending.erase(id);
        if (r) r(false, json(), "the engine isn't running");
    }
    return id;
}

std::string Engine::busy_with() const {
    std::string s;
    for (auto& [id, p] : pending) s += (s.empty() ? "" : ", ") + p.first;
    return s;
}

void Engine::poll() {
    std::vector<std::string> got;
    bool dead;
    {
        std::lock_guard<std::mutex> g(mu);
        got.swap(lines);
        dead = exited;
    }
    for (auto& line : got) {
        json j = json::parse(line, nullptr, false);
        if (j.is_discarded() || !j.is_object()) {
            log.push_back(line);
            if (on_log) on_log(line);
            continue;
        }
        if (j.contains("log")) {
            std::string s = j["log"].get<std::string>();
            log.push_back(s);
            if (on_log) on_log(s);
            continue;
        }
        if (!j.contains("id") || !j["id"].is_number_integer()) continue;
        int id = j["id"].get<int>();
        auto it = pending.find(id);
        if (it == pending.end()) continue;
        Reply r = std::move(it->second.second);
        pending.erase(it);
        bool ok = j.value("ok", false);
        if (r) r(ok, ok ? j["result"] : json(), ok ? "" : j.value("error", std::string("failed")));
    }
    if (dead && !pending.empty()) {
        auto p = std::move(pending);
        pending.clear();
        for (auto& [id, pr] : p)
            if (pr.second) pr.second(false, json(), "the engine stopped (see the log)");
    }
}
