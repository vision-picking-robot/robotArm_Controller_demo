"""Mock default, ROS imports only when explicitly constructing real transport."""
from .legacy.safe_move import MotionError


class MockBackend:
    mock = True
    def __init__(self):
        self.events = []
        self.position = 0
        self.current = 0
        self.pose = [373., 0., 285., 12.44, 180., 12.44]
    def movel(self, pos, vel, acc, ref, mod, radius):
        self.events.append(dict(action='movel', pose=list(pos), vel=vel, acc=acc, ref=ref, radius=radius))
        self.pose = list(pos)
        return 0
    def gripper(self, action, timeout=4):
        self.events.append(dict(action='gripper_' + action))
        if action == 'open': self.position, self.current = 0, 0
        if action == 'close': self.position, self.current = 350, 150
        return f'position_pulses={self.position} current={self.current} moving=0 moving_status=49'
    def check_ready(self):
        return None
    def stop(self):
        self.events.append(dict(action='stop'))
    def close(self):
        pass


class RosBackend:
    mock = False
    def __init__(self, namespace='dsr01'):
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from std_srvs.srv import Trigger
        from dsr_msgs2.srv import GetRobotSystem, GetRobotMode, GetRobotState, MoveStop
        from .legacy.ros_motion_backend import RosMotionBackend
        self.context = Context()
        rclpy.init(context=self.context)
        self.node = Node('vision_picking_demo_io', context=self.context)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.motion = RosMotionBackend(self.node, self.executor, namespace)
        self.trigger = Trigger
        self.hand = {name: self.node.create_client(Trigger, f'/{namespace}/warehouse_gripper/{name}') for name in ('status', 'open', 'close')}
        self.ready = [(self.node.create_client(kind, f'/{namespace}/system/{name}'), kind, field, expected)
                      for kind, name, field, expected in [(GetRobotSystem, 'get_robot_system', 'robot_system', 0),
                                                       (GetRobotMode, 'get_robot_mode', 'robot_mode', 1),
                                                       (GetRobotState, 'get_robot_state', 'robot_state', 1)]]
        self.stop_type = MoveStop
        self.stop_client = self.node.create_client(MoveStop, f'/{namespace}/motion/move_stop')
    def check_ready(self):
        from .legacy.ros_calls import call_service
        for client, kind, field, expected in self.ready:
            result = call_service(self.executor, client, kind.Request(), 3)
            if not result.success or getattr(result, field) != expected:
                raise MotionError('Requires real robot, AUTO mode and STANDBY state')
    def movel(self, *args, **kwargs):
        return self.motion.movel(*args, **kwargs)
    def gripper(self, action, timeout=8):
        from .legacy.ros_calls import call_service
        result = call_service(self.executor, self.hand[action], self.trigger.Request(), timeout)
        if not result.success: raise MotionError(result.message)
        return result.message
    def stop(self):
        from .legacy.ros_calls import call_service
        result = call_service(self.executor, self.stop_client, self.stop_type.Request(stop_mode=1), 3)
        if not result.success: raise MotionError('Stop not acknowledged')
    def close(self):
        self.executor.shutdown()
        self.node.destroy_node()
        self.context.shutdown()
