// RH-P12-RN-A, Modbus RTU slave 1. Register offsets are zero-based.
// 32-bit ROBOTIS values use the low register first; each register is big endian.
#pragma once
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace rhp12 {
using Bytes = std::vector<uint8_t>;
inline uint16_t crc(const Bytes& bytes) {
    uint16_t value = 0xffff;
    for (auto b : bytes) {
        value ^= b;
        for (int n = 0; n < 8; ++n)
            value = (value >> 1) ^ ((value & 1) ? 0xa001 : 0);
    }
    return value;
}
inline Bytes frame(Bytes bytes) {
    auto c = crc(bytes);
    bytes.push_back(c & 255); bytes.push_back(c >> 8);
    return bytes;
}
inline int word(const Bytes& bytes, size_t i) { return (bytes.at(i) << 8) | bytes.at(i+1); }

struct Status {
    int position = -1, current = 0, goal_current = 0, limit = 0, mode = 0, error = 0;
    int voltage_tenths = -1;
    int position_min = 0, position_max = 0;
    bool position_valid(int value) const {return value >= position_min && value <= position_max;}
    bool moving = false;
    int velocity_limit=0, acceleration_limit=0;
    bool torque=false, contact=false;
    int goal_position=-1;
};

template<class Port> class Hand {
    Port& port;
    Bytes exchange(Bytes payload, size_t expected, bool actuator_write = false) {
        port.check();
        if (actuator_write) attempted = true;
        port.write(frame(payload));
        Bytes reply;
        // Match Dart's flange_serial_read(1.0), including fragmented responses.
        const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(1);
        while (reply.size() < expected) {
            const float seconds = std::chrono::duration<float>(end - std::chrono::steady_clock::now()).count();
            if (seconds <= 0) throw std::runtime_error("response_timeout_"+
                std::to_string(word(payload,2))+"_"+std::to_string(reply.size()));
            auto chunk = port.read(seconds);
            if (chunk.empty()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
                continue;
            }
            reply.insert(reply.end(),chunk.begin(),chunk.end());
            if (reply.size() >= 2 && (reply[1] & 0x80)) expected = 5;
        }
        if (reply.size() != expected || crc(reply) != 0 || reply[0] != 1) throw std::runtime_error("bad_response");
        if (reply[1] == (payload[1] | 0x80)) throw std::runtime_error("device_exception");
        if (reply[1] != payload[1]) throw std::runtime_error("bad_response");
        return reply;
    }
    Bytes regs(int address, int count) {
        auto r = exchange({1,3,uint8_t(address>>8),uint8_t(address),0,uint8_t(count)}, 5+2*count);
        if (r[2] != count*2) throw std::runtime_error("bad_response");
        return r;
    }
    void write_ack(Bytes payload) {
        auto r = exchange(payload,8,true);
        if (!std::equal(r.begin(),r.begin()+6,payload.begin())) throw std::runtime_error("bad_response");
    }
    void goal(int position) {
        write_ack({1,16,1,26,0,2,4,uint8_t(position>>8),uint8_t(position),0,0});
    }
    void observe(Status& s) {
        auto values = regs(285,7); // moving, PWM, current, velocity (2), position (2)
        s.moving = (word(values,3) & 255) != 0;
        s.current = int16_t(word(values,7));
        uint32_t raw = uint32_t(word(values,13)) | (uint32_t(word(values,15)) << 16);
        s.position = static_cast<int32_t>(raw);
        s.error = word(regs(259,1),3) & 255;
    }
    void position_limits(Status& s) {
        auto r=regs(24,4); // 40025 max position, 40027 min position (low word first).
        s.position_max=static_cast<int32_t>(uint32_t(word(r,3))|(uint32_t(word(r,5))<<16));
        s.position_min=static_cast<int32_t>(uint32_t(word(r,7))|(uint32_t(word(r,9))<<16));
        if(s.position_min<0||s.position_max>1150||s.position_min>=s.position_max)
            throw std::runtime_error("position_limits");
    }
public:
    bool attempted = false;
    explicit Hand(Port& p): port(p) {}
    void read_voltage(Status& s) {
        s.voltage_tenths = -1;
        // 40297 -> zero-based 296 (0x0128): 01 03 01 28 00 01 + CRC.
        // A flange can acknowledge OPEN before it accepts the first TX frame.
        // Retry this read-only probe; never replay torque or goal writes.
        for(int attempt=0;attempt<3;++attempt) {
            try {s.voltage_tenths=word(regs(296,1),3);return;}
            catch(const std::runtime_error& error) {
                if(attempt==2||std::string(error.what()).rfind("response_timeout_",0)!=0)throw;
                port.check();
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        }
    }
    void inspect(Status& s) {
        read_voltage(s);
        if(word(regs(0,1),3)!=35074)throw std::runtime_error("wrong_model");
        s.mode=word(regs(5,1),3)>>8;s.limit=word(regs(19,1),3);s.goal_current=int16_t(word(regs(275,1),3));
        position_limits(s);
        auto v=regs(22,2),a=regs(20,2);
        s.velocity_limit=word(v,3)|(word(v,5)<<16);s.acceleration_limit=word(a,3)|(word(a,5)<<16);
        s.torque=(word(regs(256,1),3)&1)!=0;observe(s);
    }
    void profile_word(int address,int value) {
        write_ack({1,16,uint8_t(address>>8),uint8_t(address),0,2,4,uint8_t(value>>8),uint8_t(value),uint8_t(value>>24),uint8_t(value>>16)});
        auto r=regs(address,2);
        if((uint32_t(word(r,3))|(uint32_t(word(r,5))<<16))!=uint32_t(value))throw std::runtime_error("profile_verify");
    }
    int destination=0,contact_threshold=0,start_position=0,window_low=0,window_high=0;
    bool holding=false,halted=false;
    std::chrono::steady_clock::time_point stable_since{},expires{};
    void start(int target,int velocity,int acceleration,int threshold,int timeout,Status& s) {
        inspect(s);
        if(target<0||target>700||!s.position_valid(target)||s.mode!=5||s.error||s.moving||!s.position_valid(s.position)||
           s.goal_current<=0||s.goal_current>s.limit||velocity<=0||velocity>s.velocity_limit||
           acceleration<=0||acceleration>s.acceleration_limit||threshold<0||threshold>s.goal_current)
            throw std::runtime_error("current_config");
        profile_word(278,acceleration);profile_word(280,velocity);
        if(!s.torque){goal(s.position);write_ack({1,6,1,0,0,1});if(!(word(regs(256,1),3)&1))throw std::runtime_error("torque");}
        destination=target;contact_threshold=threshold;start_position=s.position;holding=halted=false;
        stable_since={};window_low=window_high=s.position;expires=std::chrono::steady_clock::now()+std::chrono::seconds(timeout);
        goal(target);s.goal_position=target;
    }
    void halt(Status& s) {observe(s);goal(s.position);destination=s.position;s.goal_position=s.position;contact_threshold=0;halted=true;holding=true;stable_since={};}
    bool tick(Status& s) {
        observe(s);const auto now=std::chrono::steady_clock::now();
        if(s.error||!s.position_valid(s.position))throw std::runtime_error("device_state");
        if(now>expires)throw std::runtime_error("position_timeout");
        const bool close_contact=contact_threshold>0&&!holding;
        if(close_contact&&s.position>=698)throw std::runtime_error("empty_grip");
        bool eligible=close_contact?s.position>start_position+2&&std::abs(s.current)>=contact_threshold:
            std::abs(s.position-destination)<=2;
        window_low=std::min(window_low,s.position);window_high=std::max(window_high,s.position);
        if(!eligible||window_high-window_low>1){stable_since={};window_low=window_high=s.position;}
        if(eligible){
            if(stable_since==std::chrono::steady_clock::time_point{})stable_since=now;
            if(now-stable_since>=std::chrono::milliseconds(300)){
                if(close_contact){goal(s.position);destination=s.position;s.goal_position=s.position;holding=true;s.contact=true;stable_since={};window_low=window_high=s.position;}
                else {s.torque=(word(regs(256,1),3)&1)!=0;if(!s.torque)throw std::runtime_error("torque");port.check();return true;}
            }
        }
        return false;
    }
    void run(int target, bool contact_allowed, Status& s) {
        read_voltage(s);
        if (word(regs(0,1),3) != 35074) throw std::runtime_error("wrong_model");
        s.mode = word(regs(5,1),3) >> 8;
        s.limit = word(regs(19,1),3);
        s.goal_current = int16_t(word(regs(275,1),3));
        position_limits(s);
        observe(s);
        if (target < 0) return; // Explicit read: no torque or goal writes.
        if (target > 700 || !s.position_valid(target) || s.mode != 5 || s.goal_current <= 0 || s.goal_current > s.limit)
            throw std::runtime_error("current_config");
        if (s.error || s.moving || !s.position_valid(s.position)) throw std::runtime_error("device_state");
        int initial = s.position;
        if (!(word(regs(256,1),3) & 1)) {
            goal(initial); // Do not energize the motor with a stale target.
            write_ack({1,6,1,0,0,1});
            if (!(word(regs(256,1),3) & 1)) throw std::runtime_error("torque");
        }
        goal(target);
        auto end = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        int previous = initial, stable = 0;
        while (std::chrono::steady_clock::now() < end) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            observe(s);
            if (s.error || !s.position_valid(s.position)) throw std::runtime_error("device_state");
            bool reached = std::abs(s.position-target) <= 2;
            bool stopped_on_object = contact_allowed && s.position > initial+2 && std::abs(s.current)>0;
            stable = (!s.moving && std::abs(s.position-previous)<=1 && (reached || stopped_on_object)) ? stable+1 : 0;
            if (stable >= 3) { port.check(); return; }
            previous = s.position;
        }
        throw std::runtime_error("position_timeout");
    }
};
} // namespace rhp12
