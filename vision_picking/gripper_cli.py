"""Standalone status/open/close against MOCK or the shared Doosan ROS flange plugin."""
import argparse
import json
from .backends import MockBackend, RosBackend
from .legacy.gripper_feedback import parse_feedback, wait_for_feedback


def main(argv=None):
    parser = argparse.ArgumentParser(description='RH-P12-RN(A) standalone ROS/Mock check; never moves the arm')
    parser.add_argument('--mode', choices=['mock', 'real'], default='mock')
    parser.add_argument('--namespace', default='dsr01')
    parser.add_argument('--action', choices=['status', 'open', 'close'], default='status')
    args = parser.parse_args(argv)
    backend = MockBackend() if args.mode == 'mock' else RosBackend(args.namespace.strip('/'))
    try:
        if args.action != 'status':
            backend.check_ready()
            backend.gripper(args.action)
            if args.action == 'open':
                wait_for_feedback(lambda remaining: backend.gripper('status', remaining), lambda p,c: abs(p)<=10, 'open')
        moving, position, current = parse_feedback(backend.gripper('status'))
        print(json.dumps(dict(mock_mode=backend.mock, action=args.action, command_acknowledged=True,
                              moving=moving, position_pulses=position, current=current,
                              grasp_verified=False), ensure_ascii=False, indent=2))
        # A close write acknowledgement is not a claim of full stroke or object grasp.
        return 0
    finally:
        backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
