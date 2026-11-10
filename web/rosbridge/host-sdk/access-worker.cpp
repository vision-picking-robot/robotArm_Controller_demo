// One SDK session owns access and operator-confirmed joint and Cartesian moves.
#include "DRFLEx.h"
#include "rh_p12.hpp"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <mutex>
#include <poll.h>
#include <unistd.h>

using namespace std::chrono;
static std::atomic<bool> down{false}, initialized{false}, held{false};
static std::atomic<long long> last_frame{0};
static std::atomic<unsigned> grant_number{0};
static std::atomic<bool> in_move{false};
static std::atomic<long long> motion_pulse{0};
static std::mutex output;

static long long now_ms() {
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}
static void event(const char* key, const char* value) {
    std::lock_guard<std::mutex> lock(output);
    std::printf("E0509 %s %s\n", key, value);
    std::fflush(stdout);
}
static const char* state_name(ROBOT_STATE s) {
    switch (s) {
        case STATE_STANDBY: return "STANDBY";
        case STATE_SAFE_OFF: return "SAFE_OFF";
        case STATE_MOVING: return "MOVING";
        case STATE_SAFE_STOP: return "SAFE_STOP";
        case STATE_EMERGENCY_STOP: return "EMERGENCY_STOP";
        default: return "OTHER";
    }
}
static void servo_on(DRAFramework::CDRFLEx& sdk, const char* command) {
    unsigned grant;
    char token[33], extra;
    const bool recovery = !std::strncmp(command, "recover ", 8);
    if (std::sscanf(command, recovery ? "recover %u %32[0123456789abcdef] %c" : "servo %u %32[0123456789abcdef] %c", &grant, token, &extra) != 2 ||
        std::strlen(token) != 32) return;
    const auto mode = sdk.get_robot_mode();
    const bool ready = (mode == ROBOT_MODE_MANUAL || mode == ROBOT_MODE_AUTONOMOUS) &&
                       sdk.get_robot_system() == ROBOT_SYSTEM_REAL &&
                       sdk.get_robot_state() == (recovery ? STATE_SAFE_STOP : STATE_SAFE_OFF) &&
                       initialized && !down && held && grant == grant_number.load() && !in_move &&
                       last_frame.load() && now_ms() - last_frame.load() <= 2000;
    // Reset to standby without resuming the interrupted controller program.
    const bool accepted = ready && (recovery
        ? sdk.set_safe_stop_reset_type(SAFE_STOP_RESET_TYPE_DEFAULT) && sdk.set_robot_control(CONTROL_RESET_SAFET_STOP)
        : sdk.set_robot_control(CONTROL_SERVO_ON));
    char result[48];
    std::snprintf(result, sizeof(result), "%s %s", token, accepted ? "accepted" : "refused");
    event("servo", result);
}

// A failed setter can leave the cached getter showing the requested mode.
static bool mode_verified = true;
static void set_operation_mode(DRAFramework::CDRFLEx& sdk, const char* command) {
    unsigned grant;
    char token[33], target[8], extra;
    if (std::sscanf(command, "mode %u %32[0123456789abcdef] %7s %c", &grant, token, target, &extra) != 3 ||
        std::strlen(token) != 32 || (std::strcmp(target,"AUTO") && std::strcmp(target,"MANUAL"))) return;
    const auto desired = !std::strcmp(target,"AUTO") ? ROBOT_MODE_AUTONOMOUS : ROBOT_MODE_MANUAL;
    const auto current = sdk.get_robot_mode();
    auto authorized = [&] { return initialized && !down && held && grant == grant_number.load() &&
                                  last_frame.load() && now_ms() - last_frame.load() <= 2000; };
    bool ready = (current == ROBOT_MODE_MANUAL || current == ROBOT_MODE_AUTONOMOUS) &&
                 sdk.get_robot_system() == ROBOT_SYSTEM_REAL && sdk.get_robot_state() == STATE_STANDBY &&
                 !in_move && authorized();
    mode_verified = ready && sdk.set_robot_mode(desired) && sdk.get_robot_mode() == desired && authorized();
    char result[48];
    std::snprintf(result, sizeof(result), "%s %s", token, mode_verified ? target : "failed");
    event("mode_result", result);
}

static bool can_move(DRAFramework::CDRFLEx& sdk, unsigned grant) {
    return mode_verified && sdk.get_robot_state() == STATE_STANDBY &&
           sdk.get_robot_mode() == ROBOT_MODE_AUTONOMOUS && sdk.get_robot_system() == ROBOT_SYSTEM_REAL &&
           !in_move && !down && initialized && held && grant == grant_number.load() &&
           last_frame.load() && now_ms() - last_frame.load() <= 2000;
}

static void move_line(DRAFramework::CDRFLEx& sdk, const char* command) {
    unsigned grant;
    float pos[6];
    char extra;
    if (std::sscanf(command, "line %u %f %f %f %f %f %f %c", &grant,
                    &pos[0], &pos[1], &pos[2], &pos[3], &pos[4], &pos[5], &extra) != 7) {
        event("pose", "refused"); return;
    }
    for (float v : pos) {
        if (!std::isfinite(v)) { event("pose", "refused"); return; }
    }
    if (!can_move(sdk, grant)) { event("pose", "refused"); return; }
    float velocity[2] = {10, 10}, acceleration[2] = {10, 10};
    in_move = true;
    const bool completed = sdk.movel(pos, velocity, acceleration, 0, MOVE_MODE_ABSOLUTE, MOVE_REFERENCE_BASE, 0);
    in_move = false;
    event("pose", completed ? "completed" : "failed");
}

static void move_pose(DRAFramework::CDRFLEx& sdk, const char* command) {
    unsigned grant;
    float speed, acceleration, joints[6];
    char extra;
    if (std::sscanf(command, "move %u %f %f %f %f %f %f %f %f %c", &grant, &speed, &acceleration,
                    &joints[0], &joints[1], &joints[2], &joints[3], &joints[4], &joints[5], &extra) != 9 ||
        !(speed > 0 && speed <= 10 && acceleration > 0 && acceleration <= 10)) {
        event("pose", "refused"); return;
    }
    const float limits[] = {360, 360, 155, 360, 155, 360};
    for (int i = 0; i < 6; ++i) {
        if (!std::isfinite(joints[i]) || std::abs(joints[i]) > limits[i]) {
            event("pose", "refused"); return;
        }
    }
    if (!can_move(sdk, grant)) {
        event("pose", "refused"); return;
    }
    in_move = true;
    const bool completed = sdk.movej(joints, speed, acceleration, 0, MOVE_MODE_ABSOLUTE, 0);
    in_move = false;
    event("pose", completed ? "completed" : "failed");
}

// One open serial port per explicit operation. No hardware I/O on feature discovery.
struct FlangePort {
    DRAFramework::CDRFLEx& sdk;
    unsigned grant;
    bool open() {
        const bool opened=sdk.flange_serial_open(57600,BYTE_SIZE_EIGHTBITS,PARITY_CHECK_NONE,STOPBITS_ONE);
        // The SDK acknowledges configuration before the flange is ready for TX.
        if(opened)std::this_thread::sleep_for(std::chrono::milliseconds(500));
        return opened;
    }
    void check() {
        if (!mode_verified || !held || down || !initialized || grant != grant_number.load() ||
            !last_frame.load() || now_ms()-last_frame.load()>2000 ||
            sdk.get_robot_state()!=STATE_STANDBY || sdk.get_robot_mode()!=ROBOT_MODE_AUTONOMOUS ||
            sdk.get_robot_system()!=ROBOT_SYSTEM_REAL) throw std::runtime_error("control");
    }
    void write(rhp12::Bytes bytes) {
        if (!sdk.flange_serial_write(bytes.size(), reinterpret_cast<char*>(bytes.data())))
            throw std::runtime_error("serial_write");
    }
    rhp12::Bytes read(float timeout) {
        auto rx = sdk.flange_serial_read(timeout);
        if (!rx || rx->_iSize <= 0 || rx->_iSize > 256) return {};
        return {rx->_cRxd, rx->_cRxd + rx->_iSize};
    }
};

static void gripper(DRAFramework::CDRFLEx& sdk, const char* command) {
    unsigned grant;
    int target, contact;
    char extra;
    if (std::sscanf(command, "hand %u %d %d %c", &grant,&target,&contact,&extra) != 3 ||
        target < -1 || target > 700 || (contact && target != 700) || !can_move(sdk,grant)) {
        event("hand","failed control -1 0 0 0 0 0"); return;
    }
    FlangePort port{sdk,grant};
    rhp12::Hand<FlangePort> hand(port);
    rhp12::Status feedback;
    std::string state = "completed", reason = "ok";
    bool opened = false;
    in_move = true;
    try {
        port.check();
        opened = port.open();
        if (!opened) throw std::runtime_error("serial_open");
        hand.run(target, contact, feedback);
        port.check();
    } catch (const std::exception& e) {
        state = hand.attempted ? "unknown" : "failed"; reason = e.what();
    }
    if (opened && !sdk.flange_serial_close()) {
        state = hand.attempted ? "unknown" : "failed"; reason = "serial_close";
    }
    in_move = false;
    char result[200];
    std::snprintf(result,sizeof(result),"%s %s %d %d %d %d %d %d %d",state.c_str(),reason.c_str(),
                  feedback.position,feedback.current,feedback.goal_current,feedback.limit,feedback.mode,feedback.error,feedback.voltage_tenths);
    event("hand",result);
}

#include "scenario_worker.hpp"

int main() {
    DRAFramework::CDRFLEx sdk;
    const char *version = sdk.get_library_version(), *ip = std::getenv("ROBOT_IP"), *port = std::getenv("ROBOT_PORT");
    event("sdk", version);
    if (std::strcmp(version, "GL010117") || !ip || !port) return 1;
    event("feature", "servo_on");
    event("feature", "safe_stop_reset");
    event("feature", "operation_mode");
    event("feature", "line");
    event("feature", "rh_p12");
    event("feature", "scenario17");
    event("feature", "circle");
    sdk.set_on_disconnected([] { down = true; event("disconnect", "SDK disconnected"); });
    sdk.set_on_monitoring_data([](const LPMONITORING_DATA data) {
        if (!data) return;
        for (const auto angle : data->_tCtrl._tJoint._fActualPos) if (!std::isfinite(angle)) return;
        const auto now = now_ms();
        last_frame = now;
        // A synchronous movej can outlast the supervisor timeout. Only real monitoring
        // frames keep the session alive while the main thread waits for its result.
        if (in_move && now - motion_pulse.load() >= 500) {
            motion_pulse = now;
            event("motion_frame", "live");
        }
    });
    sdk.set_on_tp_initializing_completed([] { initialized = true; });
    sdk.set_on_monitoring_access_control([](const MONITORING_ACCESS_CONTROL access) {
        switch (access) {
            case MONITORING_ACCESS_CONTROL_GRANT:
                if (!held.exchange(true)) ++grant_number;
                event("callback", "GRANT"); break;
            case MONITORING_ACCESS_CONTROL_DENY: held = false; event("callback", "DENY"); break;
            case MONITORING_ACCESS_CONTROL_LOSS: held = false; event("callback", "LOSS"); break;
            case MONITORING_ACCESS_CONTROL_REQUEST: event("callback", "REQUEST"); break;
            default: break;
        }
    });
    if (!sdk.open_connection(ip, std::atoi(port)) || down) {
        event("disconnect", "open_connection failed"); return 2;
    }
    SYSTEM_VERSION system{};
    if (!sdk.get_system_version(&system) || !system._szController[0] || down) {
        event("disconnect", "DRCF version unavailable"); sdk.close_connection(); return 3;
    }
    event("connected", system._szController);
    event("flange", system._szFlangeBoard);
    const auto opened = now_ms();
    long long heartbeat = 0;
    while (!down) {
        scenario_run.poll(sdk);
        auto now = now_ms(), frame = last_frame.load();
        if (now - (frame ? frame : opened) > 6000) {
            event("disconnect", "No fresh SDK monitoring data"); break;
        }
        if (now - heartbeat >= 500) {
            const auto mode = sdk.get_robot_mode();
            const char* mode_name = mode == ROBOT_MODE_AUTONOMOUS ? "AUTO" : mode == ROBOT_MODE_MANUAL ? "MANUAL" : "OTHER";
            char status[96];
            std::snprintf(status, sizeof(status), "%s %s %s", state_name(sdk.get_robot_state()), mode_name,
                          sdk.get_robot_system() == ROBOT_SYSTEM_REAL ? "REAL" : "OTHER");
            event(initialized && frame && now - frame <= 2000 ? "ready" : "initializing", status);
            heartbeat = now;
        }
        pollfd input{STDIN_FILENO, POLLIN, 0};
        if (poll(&input, 1, 100) <= 0) continue;
        char command[4096];
        if (!std::fgets(command, sizeof(command), stdin)) break;
        if (!std::strncmp(command,"halt ",5)) {
            std::istringstream input(command);std::string op,id;unsigned grant;input>>op>>grant>>id;
            if(scenario_run.active&&id==scenario_run.id&&grant==scenario_run.grant){try{scenario_run.stop(sdk);}catch(const std::exception& e){scenario_run.finish(sdk,"unknown",e.what());}}continue;
        }
        if(scenario_run.active)continue;
        if (!std::strncmp(command,"inspect ",8)){inspect_scenario(sdk,command);continue;}
        if (!std::strncmp(command,"segment ",8)){scenario_run.begin(sdk,command);continue;}
        if (!std::strncmp(command, "mode ", 5)) { set_operation_mode(sdk, command); continue; }
        if (!std::strncmp(command, "servo ", 6)) { servo_on(sdk, command); continue; }
        if (!std::strncmp(command, "recover ", 8)) { servo_on(sdk, command); continue; }
        if (!std::strncmp(command, "move ", 5)) { move_pose(sdk, command); continue; }
        if (!std::strncmp(command, "hand ", 5)) { gripper(sdk, command); continue; }
        if (!std::strncmp(command, "line ", 5)) { move_line(sdk, command); continue; }
        if (std::strcmp(command, "force\n")) continue;
        const auto state = sdk.get_robot_state();
        if (held) { event("already", "GRANT"); continue; }
        if (down || !initialized || !frame || now_ms() - frame > 2000 ||
            (state != STATE_STANDBY && state != STATE_SAFE_OFF && state != STATE_SAFE_STOP)) {
            event("refused", "SDK not ready or robot not stopped"); continue;
        }
        if (!sdk.manage_access_control(MANAGE_ACCESS_CONTROL_FORCE_REQUEST))
            event("refused", "SDK did not send the request");
    }
    if(scenario_run.active){try{scenario_run.stop(sdk);}catch(...) {}}
    sdk.close_connection();
}
