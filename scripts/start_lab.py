"""Launch only the two documented MOCK services from a standalone clone."""
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = dict(os.environ, MOCK_MODE='true', YOLO_BACKEND='mock', YOLO_CAMERA_AUTOSTART='0',
               YOLO_VOICE_ENABLED='1', YOLO_DEMO_CONTROLLER_URL='http://127.0.0.1:8011', ROBOT_ROS_PC_IP='')
    children = []
    commands = [
        [sys.executable, '-m', 'uvicorn', 'vision_picking.server:create_app', '--factory', '--host', '127.0.0.1', '--port', '8011', '--workers', '1'],
        [sys.executable, '-m', 'uvicorn', 'app.main:app', '--app-dir', 'web', '--host', '127.0.0.1', '--port', '8000'],
    ]
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=ROOT, env=env))
        print('MOCK only. Dashboard: http://127.0.0.1:8000 | Controller: http://127.0.0.1:8011/docs', flush=True)
        print('Ctrl+C stops only these child services. See README for separate real gripper tests.', flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(.5)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None: child.terminate()
        for child in children:
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
