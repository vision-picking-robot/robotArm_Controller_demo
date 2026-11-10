"""Arm + gripper endurance loop (home <-> via_point, gripper open/close).

Default is MOCK. Real hardware requires `--mode real --confirm-real` and the robot in
REAL / AUTO / STANDBY with warehouse_gripper active and enable_motion=true.

Safety rules:
  * Only taught joint poses from config/poses.yaml are used (home, via_point by default).
  * Every movej goes through SafeMover (fkin z-floor check, vel/acc caps).
  * Readiness is re-checked before every cycle; arrival is verified against get_current_posj.
  * Any error or Ctrl+C sends move_stop and ends the loop. It never retries motion on its own.
  * This is NOT an emergency stop. Keep the pendant E-Stop within reach.

Examples:
  python scripts/endurance_test.py --cycles 20                 # MOCK, 20 cycles
  python scripts/endurance_test.py --mode real --confirm-real  # REAL, until Ctrl+C
"""
import argparse
import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vision_picking.legacy.gripper_feedback import parse_feedback, wait_for_feedback  # noqa: E402
from vision_picking.legacy.safe_move import SafeMover  # noqa: E402

JOINT_TOLERANCE_DEG = 1.0
OPEN_MAX_PULSES = 10
CLOSED_MIN_PULSES = 600  # empty close target is 740


class MockArm:
    """Joint-space mock with the same surface the ROS backend exposes."""
    mock = True

    def __init__(self, start):
        self.posj = list(start)
        self.position = 0
        self.events = []

    def check_ready(self):
        return None

    def fkin(self, posj, ref):
        # Rough planar stand-in: keeps z at the taught 285 mm for the test poses.
        return [373.0, 0.0, 285.0, 0.0, 180.0, 0.0]

    def get_current_posj(self):
        return list(self.posj)

    def movej(self, pos, vel, acc, mod, radius):
        self.events.append(dict(action='movej', target=list(pos), vel=vel, acc=acc))
        self.posj = list(pos)
        return 0

    def gripper(self, action, timeout=8):
        self.events.append(dict(action='gripper_' + action))
        if action == 'open':
            self.position = 0
        elif action == 'close':
            self.position = 740
        return f'position_pulses={self.position} current=0 moving=0 moving_status=0'

    def stop(self):
        self.events.append(dict(action='stop'))

    def close(self):
        pass


class RealArm:
    """Thin adapter over the existing RosBackend (+ its RosMotionBackend)."""
    mock = False

    def __init__(self, namespace):
        from vision_picking.backends import RosBackend
        self.ros = RosBackend(namespace)

    def check_ready(self):
        self.ros.check_ready()

    def fkin(self, posj, ref):
        return self.ros.motion.fkin(posj, ref)

    def get_current_posj(self):
        return self.ros.motion.get_current_posj()

    def movej(self, pos, vel, acc, mod, radius):
        return self.ros.motion.movej(pos, vel, acc, mod, radius)

    def gripper(self, action, timeout=8):
        return self.ros.gripper(action, timeout)

    def stop(self):
        self.ros.stop()

    def close(self):
        self.ros.close()


def load_poses(names):
    data = yaml.safe_load((ROOT / 'config/poses.yaml').read_text(encoding='utf-8'))
    floor = data.get('floor', {})
    z_min = float(floor['floor_z']) + float(floor['safety_margin']) + float(floor['nfc_protrusion'])
    motion = data.get('motion', {})
    poses = {}
    for name in names:
        pose = data['poses'].get(name)
        if not pose or not pose.get('taught') or not pose.get('posj'):
            raise SystemExit(f'Pose {name!r} is missing or not taught in config/poses.yaml')
        poses[name] = [float(v) for v in pose['posj']]
    return poses, z_min, float(motion.get('max_vel', 30.0)), float(motion.get('max_acc', 30.0))


class Logger:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open('a', encoding='utf-8')
        self.path = path

    def __call__(self, event, **fields):
        record = dict(time=datetime.now().isoformat(timespec='milliseconds'), event=event, **fields)
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)
        self.file.write(line + '\n')
        self.file.flush()

    def error(self, msg):  # SafeMover logger interface
        self('safety_block', message=msg)


def move_and_verify(mover, arm, name, target, vel, acc, log):
    t0 = time.monotonic()
    mover.movej(target, vel, acc)
    actual = arm.get_current_posj()
    if actual is None:
        raise RuntimeError(f'Could not read posj after moving to {name}')
    err = max(abs(a - b) for a, b in zip(actual, target))
    if err > JOINT_TOLERANCE_DEG:
        raise RuntimeError(f'Arrival check failed at {name}: max joint error {err:.2f} deg')
    log('arm_arrived', pose=name, seconds=round(time.monotonic() - t0, 2), max_joint_error=round(err, 3))


def gripper_cycle(arm, log):
    status = lambda remaining: arm.gripper('status', max(remaining, 1.0))  # noqa: E731
    t0 = time.monotonic()
    arm.gripper('open')
    wait_for_feedback(status, lambda p, c: abs(p) <= OPEN_MAX_PULSES, 'open', timeout=6.0)
    arm.gripper('close')
    wait_for_feedback(status, lambda p, c: p >= CLOSED_MIN_PULSES, 'close (empty)', timeout=6.0)
    _, closed_pos, closed_cur = parse_feedback(arm.gripper('status'))
    arm.gripper('open')
    wait_for_feedback(status, lambda p, c: abs(p) <= OPEN_MAX_PULSES, 'reopen', timeout=6.0)
    log('gripper_cycled', seconds=round(time.monotonic() - t0, 2),
        closed_position=closed_pos, closed_current=closed_cur)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=['mock', 'real'], default='mock')
    parser.add_argument('--confirm-real', action='store_true',
                        help='required with --mode real: area is clear and the E-Stop is in reach')
    parser.add_argument('--namespace', default='dsr01')
    parser.add_argument('--cycles', type=int, default=0, help='0 = run until Ctrl+C or first error')
    parser.add_argument('--poses', default='home,via_point', help='comma separated taught poses')
    parser.add_argument('--vel', type=float, default=10.0, help='joint deg/s (capped by poses.yaml max_vel)')
    parser.add_argument('--acc', type=float, default=10.0)
    parser.add_argument('--dwell', type=float, default=1.0, help='seconds between cycles')
    parser.add_argument('--no-gripper', action='store_true')
    parser.add_argument('--no-arm', action='store_true')
    parser.add_argument('--log', type=Path, default=None)
    args = parser.parse_args(argv)

    if args.mode == 'real' and not args.confirm_real:
        parser.error('--mode real requires --confirm-real')
    names = [n.strip() for n in args.poses.split(',') if n.strip()]
    poses, z_min, max_vel, max_acc = load_poses(names)
    log = Logger(args.log or ROOT / 'state' / 'endurance' /
                 f'{args.mode}-{datetime.now():%Y%m%d-%H%M%S}.jsonl')

    arm = MockArm(poses[names[0]]) if args.mode == 'mock' else RealArm(args.namespace)
    mover = SafeMover(z_min, arm, max_vel, max_acc, logger=log)

    stop_requested = False

    def on_signal(signum, frame):
        nonlocal stop_requested
        stop_requested = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    log('start', mode=args.mode, poses=names, vel=args.vel, acc=args.acc, z_min=z_min,
        cycles=args.cycles or 'infinite', log=str(log.path))
    cycle = 0
    exit_code = 0
    started = time.monotonic()
    try:
        while not stop_requested and (args.cycles == 0 or cycle < args.cycles):
            cycle += 1
            arm.check_ready()
            if not args.no_arm:
                for name in names + names[:1]:
                    move_and_verify(mover, arm, name, poses[name], args.vel, args.acc, log)
            if not args.no_gripper:
                gripper_cycle(arm, log)
            log('cycle_ok', cycle=cycle, elapsed_min=round((time.monotonic() - started) / 60, 2))
            time.sleep(args.dwell)
    except KeyboardInterrupt:
        log('interrupted', cycle=cycle)
        exit_code = 130
    except Exception as error:  # noqa: BLE001 - stop on anything, never auto-retry motion
        log('error', cycle=cycle, type=type(error).__name__, message=str(error))
        exit_code = 1
    finally:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        try:
            if not args.no_arm:
                arm.stop()
                log('move_stop_sent')
        except Exception as error:  # noqa: BLE001
            log('move_stop_failed', message=str(error))
        arm.close()
        log('end', completed_cycles=cycle - (1 if exit_code else 0), exit_code=exit_code)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
