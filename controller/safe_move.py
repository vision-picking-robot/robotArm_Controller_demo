"""Safe motion verification layer for Doosan E0509 controller."""
import math
import logging

log = logging.getLogger("demo_controller.safe_move")

DR_BASE = 0
DR_TOOL = 1


class MotionError(RuntimeError):
    pass


class SafeMover:
    def __init__(self, z_min, backend, max_vel=30.0, max_acc=30.0):
        self.z_min = float(z_min)
        self.backend = backend
        self.max_vel = float(max_vel)
        self.max_acc = float(max_acc)

    def validate_pose(self, pose):
        if len(pose) != 6:
            raise MotionError(f"Pose must have 6 elements [x, y, z, rx, ry, rz], got {len(pose)}")
        for val in pose:
            if not math.isfinite(val):
                raise MotionError("Pose contains NaN or infinite value")
        z = pose[2]
        if z < self.z_min - 0.001:
            raise MotionError(f"Z_MIN_VIOLATION: Target z={z:.2f}mm is below safety limit z_min={self.z_min:.2f}mm")

    def validate_speed(self, vel, acc):
        if vel <= 0 or vel > self.max_vel:
            raise MotionError(f"Velocity {vel} out of safe range (0, {self.max_vel}]")
        if acc <= 0 or acc > self.max_acc:
            raise MotionError(f"Acceleration {acc} out of safe range (0, {self.max_acc}]")

    def movel(self, pos, vel=10.0, acc=10.0, ref=DR_BASE):
        if ref != DR_BASE:
            raise MotionError("Only DR_BASE reference frame is supported for safe absolute Cartesian moves")
        self.validate_pose(pos)
        self.validate_speed(vel, acc)
        return self.backend.movel(pos, vel, acc, ref)

    def movej(self, joints, vel=10.0, acc=10.0):
        if len(joints) != 6:
            raise MotionError(f"Joints must have 6 angles, got {len(joints)}")
        for val in joints:
            if not math.isfinite(val):
                raise MotionError("Joints contain NaN or infinite value")
        self.validate_speed(vel, acc)
        return self.backend.movej(joints, vel, acc)
