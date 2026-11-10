// Shared by the web-facing mode and joint-motion services in dsr_controller2.
#pragma once
#include "DRFLEx.h"
#include <mutex>
#include <string>

extern bool sdk_has_authority();

class DirectTeach {
public:
    std::mutex commands;

    // All callers hold commands. A failed setter can leave the SDK getter
    // reporting the requested mode even though the controller rejected it.
    bool verify_mode(DRAFramework::CDRFLEx& sdk, ROBOT_MODE target, bool accepted) {
        confirmed_ = accepted && sdk.get_robot_mode() == target;
        return confirmed_;
    }

    bool auto_ready(DRAFramework::CDRFLEx& sdk) const {
        return confirmed_ && sdk_has_authority() && sdk.get_robot_mode() == ROBOT_MODE_AUTONOMOUS;
    }

    bool status(DRAFramework::CDRFLEx& sdk, std::string& message) {
        std::unique_lock<std::mutex> lock(commands, std::try_to_lock);
        if (!lock) { message = "드라이버가 명령을 처리 중입니다."; return false; }
        if (!sdk_has_authority()) { message = "드라이버의 제어권을 확인하세요."; return false; }
        if (!confirmed_) { message = "unconfirmed"; return true; }
        switch (sdk.get_robot_mode()) {
        case ROBOT_MODE_MANUAL: message = "manual"; return true;
        case ROBOT_MODE_AUTONOMOUS: message = "auto"; return true;
        default: message = "현재 운전 모드에서는 직접 교시를 전환할 수 없습니다."; return false;
        }
    }

    bool set_enabled(DRAFramework::CDRFLEx& sdk, bool enabled, std::string& message) {
        std::unique_lock<std::mutex> lock(commands, std::try_to_lock);
        if (!lock) { message = "다른 명령이 실행 중입니다."; return false; }
        if (!sdk_has_authority() || sdk.get_robot_system() != ROBOT_SYSTEM_REAL) {
            message = "실물 드라이버의 연결과 제어권을 확인하세요."; return false;
        }
        if (sdk.get_robot_state() != STATE_STANDBY) {
            message = "직접 교시 버튼을 놓고 로봇이 STANDBY일 때 전환하세요."; return false;
        }
        const auto current = sdk.get_robot_mode();
        if (current != ROBOT_MODE_AUTONOMOUS && current != ROBOT_MODE_MANUAL) {
            message = "수동 또는 자동 운전 모드에서만 전환할 수 있습니다."; return false;
        }
        const auto target = enabled ? ROBOT_MODE_MANUAL : ROBOT_MODE_AUTONOMOUS;
        if ((!confirmed_ || current != target) && !verify_mode(sdk, target, sdk.set_robot_mode(target))) {
            message = "운전 모드 변경을 확인하지 못했습니다. 제어기의 모드 선택 상태를 확인하세요."; return false;
        }
        if (!sdk_has_authority()) { message = "전환 중 제어권이 변경되었습니다."; return false; }
        message = enabled ? "수동 모드를 확인했습니다." : "자동 모드를 확인했습니다.";
        return true;
    }

private:
    bool confirmed_ = true;
};
