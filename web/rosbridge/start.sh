#!/usr/bin/env bash
set -eo pipefail
# On the ROS host, source the same driver workspace and DDS environment as the terminal.
if [[ -f /ros_ws/install/setup.bash ]]; then
  source /ros_ws/install/setup.bash
fi
ros2 interface show dsr_msgs2/srv/MoveJoint >/dev/null
namespace="${ROBOT_NAMESPACE:-dsr01}"
namespace="${namespace#/}"
namespace="${namespace%/}"
[[ "$namespace" =~ ^[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)*$ ]] || { echo 'Invalid ROBOT_NAMESPACE' >&2; exit 1; }
root="/$namespace"
exec ros2 launch rosbridge_server rosbridge_websocket_launch.xml \
  "address:=${ROBOT_BRIDGE_BIND_IP:-127.0.0.1}" "port:=${ROBOT_ROSBRIDGE_PORT:-9090}" \
  "topics_sub_glob:=\"['$root/joint_states','$root/monitor/joint_states','$root/error','$root/robot_disconnection']\"" \
  "topics_pub_glob:=\"['/__unused_pose_test_topic']\"" \
  "services_glob:=\"['/rosapi/publishers','/rosapi/service_type','$root/monitor/get_current_pose','$root/system/get_robot_state','$root/system/reclaim_control','$root/system/direct_teach_status','$root/system/set_direct_teach','$root/motion/move_joint']\"" \
  "call_services_in_new_thread:=true" "default_call_service_timeout:=150.0"
