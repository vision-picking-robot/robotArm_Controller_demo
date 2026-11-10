#include <DRFLEx.h>
#include <controller_interface/controller_interface.hpp>
#include <pluginlib/class_list_macros.hpp>
#include <std_srvs/srv/trigger.hpp>
#include "warehouse_gripper/flange_serial.hpp"
#include "warehouse_gripper/modbus.hpp"
#include "warehouse_gripper/rtu_session.hpp"

#include <chrono>
#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

// Exported by dsr_hardware2. Use only with a single Doosan arm in this manager.
extern void * get_drfl(const char * robot_name);

namespace warehouse_gripper
{
namespace
{
using Trigger = std_srvs::srv::Trigger;
constexpr uint8_t slave_id = 1;
constexpr uint16_t feedback_register = 285;

class FlangePort
{
public:
  FlangePort(DRAFramework::CDRFLEx & robot, int port) : robot_(robot), port_(port)
  {
    if (!flange::open(
        robot_, port_, 57600, BYTE_SIZE_EIGHTBITS, PARITY_CHECK_NONE, STOPBITS_ONE, 0))
    {
      throw std::runtime_error("Could not open flange serial port " + std::to_string(port_) +
        "; stop the DART task/other serial clients and check the installed DRFL version");
    }
    // The flange can acknowledge OPEN before its UART accepts the first request.
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
  }

  ~FlangePort() { flange::close(robot_, port_, 0); }

  void write(Frame request)
  {
    if (!flange::write(robot_,
        static_cast<int>(request.size()), reinterpret_cast<char *>(request.data()), port_, 0))
    {
      throw std::runtime_error("Flange serial write failed");
    }
  }

  Frame read(float timeout)
  {
    const auto chunk = flange::read(robot_, timeout, port_, 0);
    if (!chunk || chunk->_iSize <= 0) {
      return {};
    }
    if (static_cast<size_t>(chunk->_iSize) > sizeof(chunk->_cRxd)) {
      throw std::runtime_error("Invalid flange serial reply size");
    }
    return {chunk->_cRxd, chunk->_cRxd + chunk->_iSize};
  }

private:
  DRAFramework::CDRFLEx & robot_;
  int port_;
};

std::string read_status(RtuSession<FlangePort> & serial)
{
  const auto voltage = read_voltage(serial);
  const auto reply = serial.exchange(read_registers(slave_id, feedback_register, 7), 19);
  const auto feedback = decode_feedback(reply);
  const auto fault = register_word(serial.exchange(read_registers(slave_id, 259, 1), 7), 0) & 0xff;
  if (fault != 0) {
    throw std::runtime_error("Gripper Hardware Error Status=" + std::to_string(fault));
  }
  return "position_pulses=" + std::to_string(feedback.position) +
    " current=" + std::to_string(feedback.current) +
    " moving=" + std::to_string(feedback.moving) +
    " moving_status=" + std::to_string(feedback.moving_status) +
    " input_voltage_tenths=" + std::to_string(voltage);
}
}  // namespace

class GripperController : public controller_interface::ControllerInterface
{
public:
  controller_interface::CallbackReturn on_init() override
  {
    auto_declare<int>("flange_port", 1);
    auto_declare<bool>("enable_motion", false);
    return controller_interface::CallbackReturn::SUCCESS;
  }

  controller_interface::InterfaceConfiguration command_interface_configuration() const override
  {
    return {controller_interface::interface_configuration_type::NONE, {}};
  }

  controller_interface::InterfaceConfiguration state_interface_configuration() const override
  {
    return {controller_interface::interface_configuration_type::NONE, {}};
  }

  controller_interface::CallbackReturn on_configure(const rclcpp_lifecycle::State &) override
  {
    const auto port = get_node()->get_parameter("flange_port").as_int();
    if (port != 1 && port != 2) {
      RCLCPP_ERROR(get_node()->get_logger(), "flange_port must be 1 or 2");
      return controller_interface::CallbackReturn::ERROR;
    }
    port_ = static_cast<int>(port);
    status_ = make_service("status", -1);
    open_ = make_service("open", 0);
    close_ = make_service("close", 740);
    RCLCPP_INFO(get_node()->get_logger(),
      "DART-compatible RH-P12-RN(A): port=%d, 57600 8N1, ID=1, 400mA, open=0, close=740; "
      "enable_motion defaults to false", port_);
    return controller_interface::CallbackReturn::SUCCESS;
  }

  controller_interface::CallbackReturn on_activate(const rclcpp_lifecycle::State &) override
  {
    std::lock_guard<std::mutex> lock(mutex_);
    active_ = true;
    return controller_interface::CallbackReturn::SUCCESS;
  }

  controller_interface::CallbackReturn on_deactivate(const rclcpp_lifecycle::State &) override
  {
    std::lock_guard<std::mutex> lock(mutex_);
    active_ = false;
    return controller_interface::CallbackReturn::SUCCESS;
  }

  controller_interface::CallbackReturn on_cleanup(const rclcpp_lifecycle::State &) override
  {
    status_.reset();
    open_.reset();
    close_.reset();
    return controller_interface::CallbackReturn::SUCCESS;
  }

  controller_interface::return_type update(const rclcpp::Time &, const rclcpp::Duration &) override
  {
    return controller_interface::return_type::OK;
  }

private:
  rclcpp::Service<Trigger>::SharedPtr make_service(const std::string & name, int target)
  {
    return get_node()->create_service<Trigger>(
      "~/" + name,
      [this, target](const Trigger::Request::SharedPtr, Trigger::Response::SharedPtr response) {
        std::unique_lock<std::mutex> lock(mutex_, std::try_to_lock);
        if (!lock || !active_) {
          response->message = "Gripper controller is inactive or busy";
          return;
        }
        try {
          auto robot = static_cast<DRAFramework::CDRFLEx *>(get_drfl(nullptr));
          if (!robot || robot->get_robot_system() != ROBOT_SYSTEM_REAL) {
            throw std::runtime_error("An active real Doosan connection is required");
          }
          if (target >= 0 &&
            (!get_node()->get_parameter("enable_motion").as_bool() ||
            robot->get_robot_state() != STATE_STANDBY ||
            robot->get_robot_mode() != ROBOT_MODE_AUTONOMOUS))
          {
            throw std::runtime_error("Motion blocked: enable_motion must be true and robot must "
              "be AUTO/STANDBY. Stop the DART task before ROS control");
          }

          FlangePort port(*robot, port_);
          RtuSession<FlangePort> serial(port);
          if (target < 0) {
            response->message = read_status(serial);
          } else {
            read_status(serial);
            command_position([&serial](Frame request, size_t size) {
                return serial.exchange(request, size);
              }, slave_id, target);
            response->message = "Target acknowledged; use status to verify movement";
          }
          response->success = true;
        } catch (const std::exception & error) {
          response->message = error.what();
        }
      });
  }

  std::mutex mutex_;
  bool active_ = false;
  int port_ = 1;
  rclcpp::Service<Trigger>::SharedPtr status_, open_, close_;
};
}  // namespace warehouse_gripper

PLUGINLIB_EXPORT_CLASS(warehouse_gripper::GripperController, controller_interface::ControllerInterface)
