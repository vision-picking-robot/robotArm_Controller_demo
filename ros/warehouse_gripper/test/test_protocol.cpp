#include "warehouse_gripper/flange_serial.hpp"
#include "warehouse_gripper/modbus.hpp"

#include <functional>
#include <iostream>
#include <stdexcept>

using namespace warehouse_gripper;

void require(bool value, const char * message)
{
  if (!value) {
    throw std::runtime_error(message);
  }
}

void rejects(const std::function<void()> & action)
{
  try {
    action();
  } catch (const std::runtime_error &) {
    return;
  }
  throw std::runtime_error("Expected invalid input to be rejected");
}

enum Bytes {eight};
enum Parity {none};
enum Stops {one};

struct LegacyRobot
{
  int baud = 0;
  int closed = 0;
  bool flange_serial_open(int rate, Bytes, Parity, Stops) {baud = rate; return true;}
  bool flange_serial_close() {++closed; return true;}
  bool flange_serial_write(int size, char *) {return size == 8;}
  int flange_serial_read(float) {return 7;}
};

struct PortRobot
{
  int baud = 0;
  int opened_port = 0;
  int closed_port = 0;
  bool flange_serial_open(int port, int rate, Bytes, Parity, Stops)
  {opened_port = port; baud = rate; return true;}
  bool flange_serial_close(int port) {closed_port = port; return true;}
  bool flange_serial_write(int size, char *, int port) {return size == 8 && port == opened_port;}
  int flange_serial_read(float, int port) {return port == opened_port ? 7 : -1;}
};

void dart_packets()
{
  require(with_crc(write_register(1, 256, 1)) ==
    Frame({1, 6, 1, 0, 0, 1, 0x49, 0xf6}), "DART torque packet mismatch");
  require(with_crc(write_register(1, 275, 400)) ==
    Frame({1, 6, 1, 19, 1, 144, 0x78, 0x0f}), "DART current packet mismatch");
  require(with_crc(goal_position(1, 740)) ==
    Frame({1, 16, 1, 26, 0, 2, 4, 2, 228, 0, 0, 0x3e, 0xc3}), "DART close packet mismatch");
  require(with_crc(goal_position(1, 0)) ==
    Frame({1, 16, 1, 26, 0, 2, 4, 0, 0, 0, 0, 0x7f, 0x4c}), "DART open packet mismatch");
  rejects([] {goal_position(1, -1);});
  rejects([] {goal_position(1, 741);});
}

void acknowledgements()
{
  const auto write = write_register(1, 275, 400);
  validate_reply(write, with_crc(write), 8);
  const auto goal = goal_position(1, 740);
  const auto reply = with_crc({1, 16, 1, 26, 0, 2});
  validate_reply(goal, reply, 8);
  rejects([&] {validate_reply(goal, with_crc({1, 16, 1, 27, 0, 2}), 8);});
  rejects([&] {validate_reply(write, with_crc({1, 0x86, 2}), 8);});
  auto corrupt = reply;
  corrupt.back() ^= 1;
  rejects([&] {validate_reply(goal, corrupt, 8);});
  corrupt = reply;
  corrupt.pop_back();
  rejects([&] {validate_reply(goal, corrupt, 8);});
  rejects([&] {validate_reply(goal, with_crc({2, 16, 1, 26, 0, 2}), 8);});
  rejects([&] {validate_reply(goal, with_crc({1, 6, 1, 26, 0, 2}), 8);});
}

void packed_feedback()
{
  // Moving=0, Moving Status=0x31, Current=-400mA, Position=740.
  auto reply = with_crc({1, 3, 14, 0x31, 0, 0, 0, 0xfe, 0x70,
      0, 0, 0, 0, 2, 0xe4, 0, 0});
  const auto request = read_registers(1, 285, 7);
  validate_reply(request, reply, 19);
  auto feedback = decode_feedback(reply);
  require(feedback.moving == 0, "Moving Status was incorrectly included in Moving");
  require(feedback.moving_status == 0x31, "Moving Status lost");
  require(feedback.current == -400, "Signed current decoded incorrectly");
  require(feedback.position == 740, "32-bit position word order incorrect");
  reply[4] = 1;
  require(decode_feedback(reply).moving == 1, "Moving byte lost");
  reply[4] = 2;
  rejects([&] {decode_feedback(reply);});
  reply[4] = 0;
  reply[13] = 0xff; reply[14] = 0xff; reply[15] = 0xff; reply[16] = 0xff;
  require(decode_feedback(reply).position == -1, "Negative position decoded incorrectly");
  reply[13] = 0; reply[14] = 2; reply[15] = 0; reply[16] = 1;
  require(decode_feedback(reply).position == 65538, "High position word lost");
  rejects([&] {validate_reply(request, with_crc({1, 3, 2, 0, 0}), 19);});
}

void drfl_versions()
{
  char buffer[8] = {};
  LegacyRobot old;
  require(flange::open(old, 1, 57600, eight, none, one, 0), "Legacy open failed");
  require(old.baud == 57600, "Legacy API interpreted port as baud");
  require(flange::write(old, 8, buffer, 1, 0), "Legacy write failed");
  require(flange::read(old, 0.1f, 1, 0) == 7, "Legacy read failed");
  flange::close(old, 1, 0);
  require(old.closed == 1, "Legacy close failed");
  rejects([&] {flange::open(old, 2, 57600, eight, none, one, 0);});

  PortRobot current;
  require(flange::open(current, 2, 57600, eight, none, one, 0), "Port-aware open failed");
  require(current.baud == 57600 && current.opened_port == 2, "Port-aware arguments swapped");
  require(flange::write(current, 8, buffer, 2, 0), "Port-aware write lost port");
  require(flange::read(current, 0.1f, 2, 0) == 7, "Port-aware read lost port");
  flange::close(current, 2, 0);
  require(current.closed_port == 2, "Port-aware close lost port");
}

void command_sequence()
{
  std::vector<Frame> sent;
  auto exchange = [&sent](const Frame & request, size_t) {
      sent.push_back(request);
      return request[1] == 3 ? with_crc({1, 3, 2, 5, 0}) : Frame{};
    };
  command_position(exchange, 1, 740);
  require(sent == std::vector<Frame>({read_registers(1, 5, 1),
      write_register(1, 275, 400), goal_position(1, 740), write_register(1, 256, 1)}),
    "Wrong command order; old goal could execute at torque enable");
  sent.clear();
  rejects([&] {command_position(exchange, 1, 741);});
  require(sent.empty(), "Invalid target sent a command");

  int calls = 0;
  rejects([&] {
      command_position([&calls](const Frame &, size_t) {
          ++calls;
          return with_crc({1, 3, 2, 0, 0});
        }, 1, 740);
    });
  require(calls == 1, "Wrong operating mode still wrote a motion command");

  calls = 0;
  rejects([&] {
      command_position([&calls](const Frame &, size_t) {
          ++calls;
          if (calls == 3) {
            throw std::runtime_error("Goal write acknowledgement lost");
          }
          return with_crc({1, 3, 2, 5, 0});
        }, 1, 740);
    });
  require(calls == 3, "Lost acknowledgement caused a retry or torque enable");
}

int main()
{
  try {
    dart_packets();
    acknowledgements();
    packed_feedback();
    drfl_versions();
    command_sequence();
    std::cout << "PASS: DART packets, reply validation, feedback bytes, both DRFL APIs, command order\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
