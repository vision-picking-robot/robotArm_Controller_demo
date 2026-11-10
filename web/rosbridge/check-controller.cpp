// Read only by default. Either access flag requests control once, without servo or motion calls.
#include "DRFLEx.h"
#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <mutex>
#include <optional>
#include <string>
#include <thread>

static std::atomic<bool> disconnected{false};
static std::atomic<bool> pendant_ready{false};
static std::atomic<int> access_event{-1};
static std::mutex joint_mutex;
static std::array<float, 6> joints{};
static bool fresh_joints = false;

static std::string state_label(ROBOT_STATE state) {
    switch (state) {
        case STATE_STANDBY: return "STANDBY";
        case STATE_SAFE_OFF: return "SAFE_OFF";
        case STATE_MOVING: return "MOVING";
        case STATE_SAFE_STOP: return "SAFE_STOP";
        case STATE_EMERGENCY_STOP: return "EMERGENCY_STOP";
        default: return std::to_string(static_cast<int>(state));
    }
}

static std::string mode_label(ROBOT_MODE mode) {
    switch (mode) {
        case ROBOT_MODE_MANUAL: return "MANUAL";
        case ROBOT_MODE_AUTONOMOUS: return "AUTONOMOUS";
        case ROBOT_MODE_RECOVERY: return "RECOVERY";
        case ROBOT_MODE_BACKDRIVE: return "BACKDRIVE";
        case ROBOT_MODE_MEASURE: return "MEASURE";
        case ROBOT_MODE_INITIALIZE: return "INITIALIZE";
        default: return std::to_string(static_cast<int>(mode));
    }
}

static int check_access(DRAFramework::CDRFLEx& controller, MANAGE_ACCESS_CONTROL request) {
    using namespace std::chrono_literals;
    const auto init_deadline = std::chrono::steady_clock::now() + 4s;
    while (!pendant_ready && !disconnected && std::chrono::steady_clock::now() < init_deadline)
        std::this_thread::sleep_for(20ms);
    const auto state = controller.get_robot_state();
    if (disconnected || !pendant_ready || (state != STATE_STANDBY && state != STATE_SAFE_OFF)) {
        std::cerr << "Access request not sent: pendant ready=" << pendant_ready
                  << ", robot state=" << state << ", disconnected=" << disconnected << "\n";
        return 5;
    }
    if (access_event.exchange(-1) == MONITORING_ACCESS_CONTROL_GRANT) {
        std::cout << "Access callback: GRANT (session already holds control)\n";
        return 0;
    }
    const char* kind = request == MANAGE_ACCESS_CONTROL_FORCE_REQUEST ? "Forced" : "Normal";
    if (!controller.manage_access_control(request)) {
        std::cerr << kind << " access request not sent.\n";
        return 5;
    }
    std::cout << kind << " access request sent; waiting for callback." << std::endl;
    const auto deadline = std::chrono::steady_clock::now() + 10s;
    while (!disconnected && std::chrono::steady_clock::now() < deadline) {
        const int event = access_event.load();
        if (event == MONITORING_ACCESS_CONTROL_GRANT) {
            std::cout << "Access callback: GRANT\n";
            return 0;
        }
        if (event == MONITORING_ACCESS_CONTROL_DENY || event == MONITORING_ACCESS_CONTROL_LOSS) {
            std::cerr << "Access callback: " << (event == MONITORING_ACCESS_CONTROL_DENY ? "DENY" : "LOSS") << "\n";
            if (event == MONITORING_ACCESS_CONTROL_DENY)
                std::cerr << "DENY does not identify the holder or the reason.\n";
            return 5;
        }
        std::this_thread::sleep_for(20ms);
    }
    std::cerr << (disconnected ? "SDK disconnected" : "No GRANT callback within 10s") << "\n";
    return 5;
}

int main(int argc, char **argv) {
    DRAFramework::CDRFLEx controller;
    if (argc == 2 && std::strcmp(argv[1], "--sdk-version") == 0) {
        std::cout << controller.get_library_version() << "\n";
        return 0;
    }
    std::optional<MANAGE_ACCESS_CONTROL> request;
    if (argc == 2 && std::strcmp(argv[1], "--request-access") == 0)
        request = MANAGE_ACCESS_CONTROL_REQUEST;
    else if (argc == 2 && std::strcmp(argv[1], "--force-request-access") == 0)
        request = MANAGE_ACCESS_CONTROL_FORCE_REQUEST;
    else if (argc != 1) {
        std::cerr << "Usage: check-e0509 [--sdk-version | --request-access | --force-request-access]\n";
        return 1;
    }
    std::cout << "DRFL SDK: " << controller.get_library_version() << std::endl;
    const char* host = std::getenv("ROBOT_IP");
    const char* port_text = std::getenv("ROBOT_PORT");
    if (!host || !*host || !port_text) {
        std::cerr << "Set ROBOT_IP and ROBOT_PORT.\n";
        return 1;
    }
    unsigned int port = 0;
    const char* end = port_text + std::strlen(port_text);
    const auto parsed = std::from_chars(port_text, end, port);
    if (parsed.ec != std::errc{} || parsed.ptr != end || port == 0 || port > 65535) {
        std::cerr << "Invalid ROBOT_PORT.\n";
        return 1;
    }
    controller.set_on_disconnected([] { disconnected = true; });
    controller.set_on_monitoring_data([](const LPMONITORING_DATA data) {
        if (!data) return;
        std::lock_guard<std::mutex> lock(joint_mutex);
        for (const float angle : data->_tCtrl._tJoint._fActualPos)
            if (!std::isfinite(angle)) return;
        for (size_t i = 0; i < joints.size(); ++i) joints[i] = data->_tCtrl._tJoint._fActualPos[i];
        fresh_joints = true;
    });
    if (request) {
        controller.set_on_tp_initializing_completed([] { pendant_ready = true; });
        controller.set_on_monitoring_access_control([](const MONITORING_ACCESS_CONTROL event) {
            if (event != MONITORING_ACCESS_CONTROL_REQUEST) access_event = event;
        });
    }
    const bool opened = controller.open_connection(host, port);
    if (disconnected) {
        std::cerr << "SDK disconnected while opening " << host << ":" << port << "\n";
        return 2;
    }
    if (!opened) {
        std::cerr << "DRFL open_connection failed for " << host << ":" << port << "\n";
        return 2;
    }
    SYSTEM_VERSION version{};
    if (!controller.get_system_version(&version) || !version._szController[0] || disconnected) {
        std::cerr << "DRCF version unavailable.\n";
        controller.close_connection();
        return 3;
    }
    std::cout << "DRCF version: " << version._szController << "\n";
    // The cached position getter can return six zeros before the first controller frame.
    const auto sample_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    std::array<float, 6> sample;
    bool received = false;
    while (!disconnected && std::chrono::steady_clock::now() < sample_deadline) {
        {
            std::lock_guard<std::mutex> lock(joint_mutex);
            if (fresh_joints) { sample = joints; received = true; break; }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    if (!received || disconnected) {
        std::cerr << "No valid joint monitoring frame for this session.\n";
        controller.close_connection();
        return 4;
    }
    std::cout << "Joint positions (degrees):";
    for (const float angle : sample) std::cout << " " << angle;
    std::cout << "\n";
    std::cout << "Robot state: " << state_label(controller.get_robot_state())
              << ", operation mode: " << mode_label(controller.get_robot_mode()) << std::endl;
    const int result = request ? check_access(controller, *request) : 0;
    controller.close_connection();
    return result;
}
