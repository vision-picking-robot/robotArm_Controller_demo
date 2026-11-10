import argparse
from enum import Enum
import logging
from uuid import uuid4

import httpx

log = logging.getLogger(__name__)


class State(str, Enum):
    IDLE = 'IDLE'
    WAIT_BOX_TRIGGER = 'WAIT_BOX_TRIGGER'
    SCAN_RFID = 'SCAN_RFID'
    QUERY_TARGET = 'QUERY_TARGET'
    VISION_DETECT = 'VISION_DETECT'
    EXECUTE_PICK = 'EXECUTE_PICK'
    EXECUTE_PLACE = 'EXECUTE_PLACE'
    CHECK_REMAINING = 'CHECK_REMAINING'
    RETURN_HOME = 'RETURN_HOME'
    ERROR = 'ERROR'


class Orchestrator:
    """Single controller. Robot methods block until motion and sensor checks succeed."""
    def __init__(self, api, robot, scanner, vision):
        self.api, self.robot, self.scanner, self.vision = api, robot, scanner, vision
        self.state = State.IDLE

    def transition(self, state):
        self.state = state
        log.info('state=%s', state.value)

    def run_box(self, wait_trigger, quantity=1):
        task = None
        completed = 0
        try:
            if quantity < 1:
                raise ValueError('quantity must be positive')
            self.transition(State.WAIT_BOX_TRIGGER)
            wait_trigger()
            self.transition(State.SCAN_RFID)
            self.robot.pre_scan()
            uid = self.scanner.scan()
            self.transition(State.QUERY_TARGET)
            task = self.api.enqueue(uid, quantity, str(uuid4()))['task']
            # Drain queued tasks in FIFO order before returning home.
            while (task := self.api.current()) is not None:
                completed = task['completed']
                if task['status'] == 'PICKING':
                    raise RuntimeError('Interrupted PICKING task requires operator reconciliation')
                self.api.progress(task['task_id'], completed, 'PICKING')
                while completed < task['quantity']:
                    self.transition(State.VISION_DETECT)
                    pose = self.vision.detect(task)
                    self.transition(State.EXECUTE_PICK)
                    self.robot.approach(pose)
                    self.robot.grip()  # Must verify object presence before returning.
                    self.robot.retract()
                    self.transition(State.EXECUTE_PLACE)
                    self.robot.place(task['target_box_id'])  # Must verify release.
                    completed += 1
                    self.api.progress(task['task_id'], completed, 'PICKING')
                    self.transition(State.CHECK_REMAINING)
                self.api.progress(task['task_id'], completed, 'COMPLETED')
                task = None
            self.transition(State.RETURN_HOME)
            self.robot.home()
            self.transition(State.IDLE)
        except Exception as exc:
            self.transition(State.ERROR)
            try:
                self.robot.stop()
            finally:
                if task is not None:
                    try:
                        self.api.progress(task['task_id'], completed, 'DEFECTIVE', str(exc))
                    except Exception:
                        log.exception('Task fault could not be persisted; reconciliation required')
            raise


class APIClient:
    def __init__(self, url):
        self.client = httpx.Client(base_url=url, timeout=10)

    def enqueue(self, uid, quantity, event_id):
        response = self.client.post('/api/v1/scan-event', json=dict(uid=uid, quantity=quantity, event_id=event_id))
        response.raise_for_status()
        return response.json()

    def current(self):
        response = self.client.get('/api/v1/current-task')
        response.raise_for_status()
        return response.json()['task']

    def progress(self, task_id, completed, status, error=None):
        response = self.client.patch(f'/api/v1/tasks/{task_id}', json=dict(completed=completed, status=status, error=error))
        response.raise_for_status()


class SimRobot:
    def pre_scan(self): log.info('SIM pre-scan')
    def approach(self, pose): log.info('SIM approach %s', pose)
    def grip(self): log.info('SIM grip verified')
    def retract(self): log.info('SIM retract')
    def place(self, box): log.info('SIM place/release %s', box)
    def home(self): log.info('SIM home')
    def stop(self): log.info('SIM stop')


class SimScanner:
    def scan(self): return '0B71E55D'


class SimVision:
    def detect(self, task): return (0.3, 0.1, 0.05, 3.14, 0, 0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Simulation only; no robot driver configured')
    parser.add_argument('--api', default='http://127.0.0.1:8000')
    parser.add_argument('--quantity', type=int, default=2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    api = APIClient(args.api)
    try:
        Orchestrator(api, SimRobot(), SimScanner(), SimVision()).run_box(lambda: None, args.quantity)
    finally:
        api.client.close()
