"""Doosan ROS service transport for SafeMover."""
from dsr_msgs2.srv import Fkin, GetCurrentPosj, GetCurrentPosx, MoveJoint, MoveLine

from vision_picking.legacy.ros_calls import call_service


class RosMotionBackend:
    def __init__(self, node, executor, robot_name):
        self.executor = executor
        prefix = f'/{robot_name}'
        self.current_x = node.create_client(GetCurrentPosx, f'{prefix}/aux_control/get_current_posx')
        self.current_j = node.create_client(GetCurrentPosj, f'{prefix}/aux_control/get_current_posj')
        self.kinematics = node.create_client(Fkin, f'{prefix}/motion/fkin')
        self.joint = node.create_client(MoveJoint, f'{prefix}/motion/move_joint')
        self.line = node.create_client(MoveLine, f'{prefix}/motion/move_line')

    def get_current_posx(self, ref):
        result = call_service(self.executor, self.current_x, GetCurrentPosx.Request(ref=ref), 3.0)
        if not result.success or not result.task_pos_info:
            return None
        return list(result.task_pos_info[0].data[:6])

    def get_current_posj(self):
        result = call_service(self.executor, self.current_j, GetCurrentPosj.Request(), 3.0)
        return list(result.pos) if result.success else None

    def fkin(self, posj, ref):
        request = Fkin.Request(pos=[float(value) for value in posj], ref=ref)
        result = call_service(self.executor, self.kinematics, request, 3.0)
        return list(result.conv_posx) if result.success else None

    def movej(self, pos, vel, acc, mod, radius):
        request = MoveJoint.Request(
            pos=[float(value) for value in pos], vel=float(vel), acc=float(acc),
            radius=float(radius), mode=mod, sync_type=0,
        )
        result = call_service(self.executor, self.joint, request, 90.0)
        return 0 if result.success else -1

    def movel(self, pos, vel, acc, ref, mod, radius):
        request = MoveLine.Request(
            pos=[float(value) for value in pos], vel=[float(vel)] * 2, acc=[float(acc)] * 2,
            ref=ref, radius=float(radius), mode=mod, sync_type=0,
        )
        result = call_service(self.executor, self.line, request, 90.0)
        return 0 if result.success else -1
