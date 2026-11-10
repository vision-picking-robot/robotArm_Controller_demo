"""Validate physical gripper replies before allowing the next arm movement."""
import time

from vision_picking.legacy.safe_move import MotionError


def parse_feedback(message):
    try:
        pairs = [part.split('=', 1) for part in message.split()]
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError('duplicate feedback field')
        moving, position, current = (
            int(fields[key]) for key in ('moving', 'position_pulses', 'current')
        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise MotionError('Malformed gripper feedback') from error
    if moving not in (0, 1):
        raise MotionError(
            'Invalid gripper moving flag; rebuild warehouse_gripper to separate '
            'Moving(570) from Moving Status(571)'
        )
    return moving, position, current


def wait_for_feedback(status, condition, description, timeout=4.0,
                      clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    confirmed = 0
    while clock() < deadline:
        moving, position, current = parse_feedback(status(deadline - clock()))
        if clock() >= deadline:
            break
        confirmed = confirmed + 1 if moving == 0 and condition(position, abs(current)) else 0
        if confirmed >= 2:
            return
        sleep(min(0.1, max(0.0, deadline - clock())))
    raise MotionError(f'Gripper did not confirm {description}')
