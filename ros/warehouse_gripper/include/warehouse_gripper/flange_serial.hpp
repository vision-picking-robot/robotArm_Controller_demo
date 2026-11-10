#pragma once

#include <stdexcept>

namespace warehouse_gripper::flange
{
// DRFL headers exist with both port-aware and legacy, single-port signatures.
template<typename Robot, typename Bytes, typename Parity, typename Stops>
auto open(Robot & robot, int port, int baud, Bytes bytes, Parity parity, Stops stops, int)
-> decltype(robot.flange_serial_open(port, baud, bytes, parity, stops))
{
  return robot.flange_serial_open(port, baud, bytes, parity, stops);
}

template<typename Robot, typename Bytes, typename Parity, typename Stops>
auto open(Robot & robot, int port, int baud, Bytes bytes, Parity parity, Stops stops, long)
-> decltype(robot.flange_serial_open(baud, bytes, parity, stops))
{
  if (port != 1) {
    throw std::runtime_error("This legacy DRFL supports flange_port=1 only");
  }
  return robot.flange_serial_open(baud, bytes, parity, stops);
}

template<typename Robot>
auto close(Robot & robot, int port, int) -> decltype(robot.flange_serial_close(port))
{
  return robot.flange_serial_close(port);
}

template<typename Robot>
auto close(Robot & robot, int, long) -> decltype(robot.flange_serial_close())
{
  return robot.flange_serial_close();
}

template<typename Robot>
auto write(Robot & robot, int size, char * data, int port, int)
-> decltype(robot.flange_serial_write(size, data, port))
{
  return robot.flange_serial_write(size, data, port);
}

template<typename Robot>
auto write(Robot & robot, int size, char * data, int, long)
-> decltype(robot.flange_serial_write(size, data))
{
  return robot.flange_serial_write(size, data);
}

template<typename Robot>
auto read(Robot & robot, float timeout, int port, int)
-> decltype(robot.flange_serial_read(timeout, port))
{
  return robot.flange_serial_read(timeout, port);
}

template<typename Robot>
auto read(Robot & robot, float timeout, int, long)
-> decltype(robot.flange_serial_read(timeout))
{
  return robot.flange_serial_read(timeout);
}
}  // namespace warehouse_gripper::flange
