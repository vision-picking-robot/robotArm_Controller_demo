#pragma once

#include "warehouse_gripper/modbus.hpp"

#include <chrono>
#include <thread>

namespace warehouse_gripper
{
class RtuTimeout : public std::runtime_error
{
public:
  RtuTimeout(const Frame & request, size_t received)
  : std::runtime_error("Modbus response timeout: register=" +
      std::to_string(40001 + (request.at(2) << 8) + request.at(3)) +
      " received_bytes=" + std::to_string(received) +
      "; check DART serial ownership, flange port and DRFL/controller compatibility. "
      "Do not retry movement automatically") {}
};

template<typename Port>
class RtuSession
{
public:
  explicit RtuSession(Port & port) : port_(port) {}

  Frame exchange(Frame request, size_t expected_size)
  {
    for (int i = 0; i < 8; ++i) {
      if (port_.read(0.02f).empty()) {
        break;
      }
      if (i == 7) {
        throw std::runtime_error("Flange receive buffer did not clear");
      }
    }
    port_.write(with_crc(request));
    Frame reply;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(1);
    while (std::chrono::steady_clock::now() < deadline) {
      const float remaining = std::chrono::duration<float>(
        deadline - std::chrono::steady_clock::now()).count();
      if (remaining <= 0) {
        break;
      }
      // Pass the whole remaining window like the working DART/SDK code
      // (flange_serial_read(1.0)); 50 ms slices returned no bytes on the E0509 flange.
      const auto chunk = port_.read(std::min(1.0f, remaining));
      if (chunk.empty()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        continue;
      }
      reply.insert(reply.end(), chunk.begin(), chunk.end());
      const bool exception = reply.size() >= 2 && reply[1] == (request[1] | 0x80);
      if (reply.size() >= (exception ? 5 : expected_size)) {
        validate_reply(request, reply, expected_size);
        return reply;
      }
    }
    throw RtuTimeout(request, reply.size());
  }

private:
  Port & port_;
};

template<typename Session>
int read_voltage(Session & serial)
{
  // Only this read-only startup probe may be retried after an OPEN/TX race.
  for (int attempt = 0; ; ++attempt) {
    try {
      return register_word(serial.exchange(read_registers(1, 296, 1), 7), 0);
    } catch (const RtuTimeout &) {
      if (attempt == 2) {
        throw;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
  }
}
}  // namespace warehouse_gripper
