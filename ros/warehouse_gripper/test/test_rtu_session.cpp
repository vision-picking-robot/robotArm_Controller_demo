#include "warehouse_gripper/rtu_session.hpp"

#include <iostream>

using namespace warehouse_gripper;

void require(bool condition, const char * message)
{
  if (!condition) {
    throw std::runtime_error(message);
  }
}

struct Port
{
  std::vector<Frame> requests;
  Frame reply;
  bool drop_first = false;
  bool drop_all = false;
  bool bad_crc = false;
  bool partial = false;
  int empty_reads = 0;

  void write(const Frame & request)
  {
    requests.push_back(request);
    reply = with_crc({1, 3, 2, 0, 241});
    if (bad_crc) {
      reply.back() ^= 1;
    }
    if (partial) {
      reply.resize(3);
    }
    if (drop_all || (drop_first && requests.size() == 1)) {
      reply.clear();
    }
  }

  Frame read(float timeout)
  {
    require(timeout > 0 && timeout <= 1, "Invalid serial timeout");
    if (!reply.empty() && empty_reads > 0) {
      --empty_reads;
      return {};
    }
    const auto count = std::min<size_t>(2, reply.size());
    Frame chunk(reply.begin(), reply.begin() + count);
    reply.erase(reply.begin(), reply.begin() + count);
    return chunk;
  }
};

int main()
{
  try {
    Port fragmented;
    fragmented.empty_reads = 3;
    RtuSession<Port> session(fragmented);
    require(read_voltage(session) == 241, "Fragmented voltage reply was lost");
    require(fragmented.requests == std::vector<Frame>({{1, 3, 1, 40, 0, 1, 5, 254}}),
      "Empty reads retransmitted a request");

    Port delayed;
    delayed.drop_first = true;
    RtuSession<Port> delayed_session(delayed);
    require(read_voltage(delayed_session) == 241 && delayed.requests.size() == 2,
      "Startup read was not retried once after a timeout");

    Port disconnected;
    disconnected.drop_all = true;
    RtuSession<Port> disconnected_session(disconnected);
    try {
      read_voltage(disconnected_session);
      throw std::logic_error("Disconnected device was accepted");
    } catch (const RtuTimeout & error) {
      require(disconnected.requests.size() == 3, "Read retries were not bounded");
      require(std::string(error.what()).find("register=40297 received_bytes=0") != std::string::npos,
        "Timeout lost diagnostic register or byte count");
    }

    Port corrupt;
    corrupt.bad_crc = true;
    RtuSession<Port> corrupt_session(corrupt);
    try {
      read_voltage(corrupt_session);
      throw std::logic_error("Corrupted response was accepted");
    } catch (const std::runtime_error &) {
      require(corrupt.requests.size() == 1, "CRC error incorrectly retried a request");
    }

    Port write_failure;
    write_failure.drop_all = true;
    RtuSession<Port> write_session(write_failure);
    try {
      write_session.exchange(write_register(1, 256, 1), 8);
      throw std::logic_error("Lost actuator acknowledgement was accepted");
    } catch (const RtuTimeout &) {
      require(write_failure.requests.size() == 1, "Actuator write was replayed");
    }

    Port incomplete;
    incomplete.partial = true;
    RtuSession<Port> incomplete_session(incomplete);
    try {
      incomplete_session.exchange(read_registers(1, 296, 1), 7);
      throw std::logic_error("Truncated response was accepted");
    } catch (const RtuTimeout & error) {
      require(std::string(error.what()).find("received_bytes=3") != std::string::npos,
        "Partial reply diagnostic lost its byte count");
    }
    std::cout << "PASS: fragmented/empty RX, bounded voltage retries, CRC rejection, no write replay\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
