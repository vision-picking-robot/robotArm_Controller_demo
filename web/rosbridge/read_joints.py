"""Publish E0509 joint telemetry and serve on-demand task coordinates using read-only Modbus."""
from itertools import count
import math
import os
import socket
import struct
import time

def receive_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        chunk = stream.recv(size - len(result))
        if not chunk:
            raise ConnectionError("controller closed the connection")
        result.extend(chunk)
    return result


def read_six(stream, transaction, address):
    stream.sendall(struct.pack(">HHHBBHH", transaction, 0, 6, 255, 3, address, 6))
    xid, protocol, length, unit = struct.unpack(">HHHB", receive_exact(stream, 7))
    if (xid, protocol, length, unit) != (transaction, 0, 15, 255):
        raise ValueError("unexpected Modbus response header")
    payload = receive_exact(stream, length - 1)
    if payload[:2] != bytes((3, 12)):
        raise ValueError("unexpected Modbus function or byte count")
    return [value / 10.0 for value in struct.unpack(">6h", payload[2:])]


def read_positions(stream, transaction):
    # Joint registers 270..275: tenths of degrees, converted to ROS radians.
    return list(map(math.radians, read_six(stream, transaction, 270)))


def read_task_pose(stream, transaction):
    # Task registers 400..405: BASE XYZ (mm), ABC (Euler ZYZ, degrees).
    return read_six(stream, transaction, 400)


def main():
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import JointState
    from dsr_msgs2.srv import GetCurrentPose

    rclpy.init()
    namespace = os.environ["ROBOT_NAMESPACE"].strip("/")
    node = Node("controller_joint_monitor", namespace="/" + namespace)
    output = node.create_publisher(JointState, "monitor/joint_states", 1)
    address = (os.environ["ROBOT_IP"], int(os.getenv("ROBOT_MODBUS_PORT", "502")))
    names = [f"joint_{n}" for n in range(1, 7)]
    transactions = count()
    stream = None

    def current_pose(request, response):
        if stream is not None and request.space_type == 1:
            try:
                response.pos = read_task_pose(stream, next(transactions) % 65536)
                response.success = True
            except (OSError, ValueError) as exc:
                # Discard a potentially partial frame before resuming joint telemetry.
                stream.close()
                node.get_logger().warning(f"Controller task pose read failed: {exc}")
        return response

    node.create_service(GetCurrentPose, "monitor/get_current_pose", current_pose)
    try:
        while rclpy.ok():
            try:
                with socket.create_connection(address, timeout=1.5) as stream:
                    while rclpy.ok():
                        positions = read_positions(stream, next(transactions) % 65536)
                        message = JointState()
                        message.header.stamp = node.get_clock().now().to_msg()
                        message.name, message.position = names, positions
                        output.publish(message)
                        rclpy.spin_once(node, timeout_sec=0)
                        time.sleep(0.1)
            except (OSError, ValueError) as exc:
                stream = None
                rclpy.spin_once(node, timeout_sec=0)
                node.get_logger().warning(f"Controller joint read failed: {exc}", throttle_duration_sec=10)
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
