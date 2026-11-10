"""Check the live E0509 joint topic and service discovery without sending commands."""
import math
import os
import time

import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState


def main():
    root = "/" + os.environ["ROBOT_NAMESPACE"].strip("/")
    services = {root + "/system/get_robot_state": "dsr_msgs2/srv/GetRobotState",
                root + "/system/reclaim_control": "std_srvs/srv/Trigger",
                root + "/system/direct_teach_status": "std_srvs/srv/Trigger",
                root + "/system/set_direct_teach": "std_srvs/srv/SetBool",
                root + "/motion/move_joint": "dsr_msgs2/srv/MoveJoint"}
    joint_names = {f"joint_{i}" for i in range(1, 7)}
    received = False

    def on_joints(msg):
        nonlocal received
        received = (len(msg.name) == len(msg.position) == 6 and set(msg.name) == joint_names
                    and all(math.isfinite(angle) for angle in msg.position))

    rclpy.init()
    node = rclpy.create_node("driver_healthcheck")
    try:
        node.create_subscription(JointState, root + "/joint_states", on_joints, qos_profile_sensor_data)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.25)
            if received:
                discovered = dict(node.get_service_names_and_types())
                if all(service_type in discovered.get(name, []) for name, service_type in services.items()):
                    return 0
        print(f"{root}: waiting for driver joints and motion/state services", flush=True)
        return 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
