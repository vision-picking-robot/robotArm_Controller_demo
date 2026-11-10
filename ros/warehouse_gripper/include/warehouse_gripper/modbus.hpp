#pragma once

#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace warehouse_gripper
{
using Frame = std::vector<uint8_t>;

inline uint16_t crc16(const Frame & frame, size_t size)
{
  uint16_t crc = 0xffff;
  for (size_t i = 0; i < size; ++i) {
    crc ^= frame[i];
    for (int bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ ((crc & 1) ? 0xa001 : 0);
    }
  }
  return crc;
}

inline Frame with_crc(Frame frame)
{
  const auto crc = crc16(frame, frame.size());
  frame.push_back(static_cast<uint8_t>(crc));
  frame.push_back(static_cast<uint8_t>(crc >> 8));
  return frame;
}

inline Frame read_registers(uint8_t id, uint16_t address, uint16_t count)
{
  return {id, 3, static_cast<uint8_t>(address >> 8), static_cast<uint8_t>(address),
    static_cast<uint8_t>(count >> 8), static_cast<uint8_t>(count)};
}

inline Frame write_register(uint8_t id, uint16_t address, uint16_t value)
{
  return {id, 6, static_cast<uint8_t>(address >> 8), static_cast<uint8_t>(address),
    static_cast<uint8_t>(value >> 8), static_cast<uint8_t>(value)};
}

inline Frame goal_position(uint8_t id, int target)
{
  if (target < 0 || target > 740) {
    throw std::runtime_error("Gripper target must be between 0 and 740 pulses");
  }
  // Same word order as the working DART modbus_fc16(282, 2, [stroke, 0]).
  return {id, 16, 1, 26, 0, 2, 4,
    static_cast<uint8_t>(target >> 8), static_cast<uint8_t>(target), 0, 0};
}

inline void validate_reply(const Frame & request, const Frame & reply, size_t expected_size)
{
  const bool exception = reply.size() >= 2 && reply[1] == (request.at(1) | 0x80);
  const size_t size = exception ? 5 : expected_size;
  if (size < 5 || reply.size() != size || reply[0] != request.at(0) ||
    crc16(reply, size - 2) != (reply[size - 2] | (reply[size - 1] << 8)))
  {
    throw std::runtime_error("Invalid Modbus RTU reply (size, slave ID or CRC)");
  }
  if (exception) {
    throw std::runtime_error("Modbus exception " + std::to_string(reply[2]));
  }
  if (reply[1] != request[1] ||
    (request[1] == 3 ? reply[2] != size - 5 :
    !std::equal(reply.begin(), reply.begin() + 6, request.begin())))
  {
    throw std::runtime_error("Modbus RTU reply does not match request");
  }
}

inline uint16_t register_word(const Frame & reply, size_t index)
{
  return (reply.at(3 + 2 * index) << 8) | reply.at(4 + 2 * index);
}

template<typename Exchange>
void command_position(Exchange exchange, uint8_t id, int target)
{
  const auto goal = goal_position(id, target);
  const auto mode = register_word(exchange(read_registers(id, 5, 1), 7), 0) >> 8;
  if (mode != 5) {
    throw std::runtime_error("Gripper Operating Mode must be 5 (current-based position). "
      "This controller does not rewrite EEPROM mode/protocol settings");
  }
  exchange(write_register(id, 275, 400), 8);
  exchange(goal, 8);
  // Arm the requested goal before enabling torque, never a retained old goal.
  exchange(write_register(id, 256, 1), 8);
}

struct Feedback
{
  int64_t position;
  int current;
  int moving;
  int moving_status;
};

inline Feedback decode_feedback(const Frame & reply)
{
  const uint32_t raw_position = register_word(reply, 5) |
    (static_cast<uint32_t>(register_word(reply, 6)) << 16);
  const auto position = static_cast<int64_t>(raw_position) -
    ((raw_position & 0x80000000u) ? 0x100000000LL : 0);
  const int raw_current = register_word(reply, 2);
  const int current = raw_current - ((raw_current & 0x8000) ? 65536 : 0);
  const auto flags = register_word(reply, 0);
  const int moving = flags & 0xff;
  if (moving > 1) {
    throw std::runtime_error("Invalid gripper Moving byte");
  }
  // Register 285 packs Moving(570) LOW and Moving Status(571) HIGH.
  return {position, current, moving, flags >> 8};
}
}  // namespace warehouse_gripper
