"""Validated pixel → BASE plan and serialized, journaled pick/place execution."""
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
from contextlib import contextmanager

from .backends import MockBackend, RosBackend
from .contracts import PickRequest
from .legacy import config_loader as legacy
from .legacy.gripper_feedback import parse_feedback, wait_for_feedback
from .legacy.safe_move import DR_BASE, MotionError, SafeMover
from .pick_plane import Calibration, PickPlane
from .store import DomainError
from .paths import ROOT, state_directory

MOCK_MODE = True


class Controller:
    def __init__(self, config=None, backend=None, journal=None):
        self.config = config or json.loads((ROOT / 'config/integration.json').read_text(encoding='utf-8'))
        self.mock_mode = self.config['mock_mode']
        if type(self.mock_mode) is not bool: raise ValueError('mock_mode must be a boolean')
        self.backend = backend or (MockBackend() if self.mock_mode else None)
        if self.backend is not None and self.backend.mock != self.mock_mode:
            raise ValueError('Mixed mock and real backends are prohibited')
        self.poses = legacy.load_yaml(ROOT / 'config/poses.yaml')
        self.products = legacy.load_yaml(ROOT / 'config/products.yaml')['products']
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.journal = Path(journal) if journal is not None else state_directory() / 'jobs.sqlite3'
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, digest TEXT, detection TEXT UNIQUE, result TEXT)')
            # A process restart never turns an ambiguous operation into a retry.
            for row in db.execute('SELECT id, result FROM jobs').fetchall():
                result = json.loads(row[1])
                if result['state'] == 'running':
                    result.update(state='unknown', error='Process restarted; inspect robot before recovery')
                    db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(result), row[0]))

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.journal, timeout=2)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def gate(self):
        if not self.mock_mode:
            required = ('calibration_verified', 'tcp_verified', 'waypoints_verified', 'gripper_verified', 'workspace_verified')
            missing = [key for key in required if self.config.get(key) is not True]
            if missing: raise DomainError('Real execution blocked: ' + ', '.join(missing), 412)

    def plan(self, request: PickRequest):
        request.check_fresh()
        self.gate()
        if not self.mock_mode and request.simulation_only:
            raise DomainError('Synthetic detections cannot drive physical hardware', 412)
        cfg = self.config
        camera = request.camera
        calibration = Calibration(**cfg['calibration'])
        if calibration.camera != camera or calibration.revision != request.calibration_revision:
            raise DomainError('Camera/session/resolution/calibration revision mismatch', 412)
        plane = PickPlane(calibration)
        if abs(plane.normal[2]) < .999:
            raise DomainError('This pick/place profile requires a horizontal support plane', 412)
        pose = plane.target(request.detection.center_px)
        product_name = cfg['class_map'].get(request.detection.label)
        if product_name not in self.products: raise DomainError('Unmapped class label', 422)
        product = self.products[product_name]['real']
        if product.get('taught') is not True or product.get('grasp_height_from_floor') is None:
            raise DomainError('Product has no taught grasp height', 412)
        if request.detection.confidence < cfg['min_confidence']: raise DomainError('Low confidence', 422)
        limits = legacy.floor_limit(self.poses, 'real')
        if any(abs(p[2] - limits.floor_z) > 2 for p in plane.xyz):
            raise DomainError('Calibration support plane differs from measured floor', 412)
        # Plane is the floor support plane; product-specific grasp height is explicit.
        pose[2] += product['grasp_height_from_floor']
        home = legacy.get_pose(self.poses, 'home', 'real')['posx']
        via = legacy.get_pose(self.poses, 'via_point', 'real')['posx']
        slot = legacy.get_box_slot(self.poses, request.box_slot, 'real')['posx']
        approach = [*pose[:2], pose[2] + self.poses['motion']['approach_offset_z'], *pose[3:]]
        place = [*slot[:3], *slot[3:]]
        place_high = [*place[:2], approach[2], *place[3:]]
        route = [('home', home), ('approach', approach), ('pick', pose), ('lift', approach),
                 ('via', [*via[:2], approach[2], *via[3:]]), ('place_approach', place_high),
                 ('place', place), ('retreat', place_high), ('home', home)]
        bounds = cfg['workspace_mm']
        for name, target in route:
            if len(target) != 6 or not all(math.isfinite(v) for v in target): raise DomainError('Invalid pose', 422)
            if target[2] < limits.z_min or any(not lo <= value <= hi for lo, value, hi in zip(bounds['min'], target[:3], bounds['max'])):
                raise DomainError(f'{name} exceeds configured workspace/floor', 422)
        if len(set(tuple(p.pose[3:]) for p in calibration.corners)) != 1:
            raise DomainError('Calibration corners require a common taught tool orientation', 422)
        feedback = cfg['grasp_feedback'].get(product_name)
        if not feedback or feedback.get('calibrated') is not True and not self.mock_mode:
            raise DomainError('Missing measured grasp feedback', 412)
        low, high, current = (feedback[k] for k in ('min_position_pulses', 'max_position_pulses', 'min_current'))
        if not all(type(v) is int for v in (low, high, current)) or not (0 < low <= high < 740 and current > 0):
            raise DomainError('Invalid grasp feedback range', 422)
        return dict(mock_mode=self.mock_mode, frame='robot_base', units=dict(position='mm', orientation='deg_ZYZ'),
                    product=product_name, pick_pose=pose, route=route, grasp_feedback=feedback)

    def ensure_backend(self):
        self.gate()
        if self.backend is None: self.backend = RosBackend(self.config['namespace'])
        self.backend.check_ready()

    def get_gripper_status(self, timeout=4):
        moving, position, current = parse_feedback(self.backend.gripper('status', timeout))
        return dict(moving=moving, position_pulses=position, current=current, mock_mode=self.mock_mode)

    def gripper_open(self):
        self.backend.gripper('open')
        wait_for_feedback(lambda remaining: self.backend.gripper('status', remaining), lambda p, c: abs(p) <= 10, 'open')

    def gripper_close(self, profile):
        self.backend.gripper('close')
        self.check_hold(profile)

    def check_hold(self, profile):
        wait_for_feedback(lambda remaining: self.backend.gripper('status', remaining),
                          lambda p, c: profile['min_position_pulses'] <= p <= profile['max_position_pulses'] and c >= profile['min_current'], 'grasp')

    def status(self, identifier):
        with self.db() as db:
            row = db.execute('SELECT result FROM jobs WHERE id=?', (identifier,)).fetchone()
        if row is None: raise DomainError('Unknown request_id', 404)
        return json.loads(row[0])

    def save(self, identifier, result):
        with self.db() as db: db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(result), identifier))

    def move_to_pick(self, request: PickRequest, *, target_only=False):
        digest = hashlib.sha256((str(target_only) + request.model_dump_json()).encode()).hexdigest()
        if not self.lock.acquire(blocking=False): raise DomainError('Robot busy', 409)
        try:
            with self.db() as db:
                row = db.execute('SELECT digest,result FROM jobs WHERE id=?', (request.request_id,)).fetchone()
                if row:
                    if row[0] != digest: raise DomainError('request_id reused with different content', 409)
                    return json.loads(row[1])
            plan = self.plan(request)
            if target_only:
                plan['route'] = [('pick', plan['pick_pose'])]
            detection_key = json.dumps([request.camera.model_dump(), request.frame_id, request.object_index], sort_keys=True)
            result = dict(request_id=request.request_id, state='running', mock_mode=self.mock_mode, completed_steps=[])
            try:
                with self.db() as db:
                    db.execute('INSERT INTO jobs VALUES (?,?,?,?)', (request.request_id, digest, detection_key, json.dumps(result)))
            except sqlite3.IntegrityError as exc:
                raise DomainError('Detection already consumed; obtain a new frame', 409) from exc
            self.cancel.clear()
            try:
                self.ensure_backend()
                limits = legacy.floor_limit(self.poses, 'real')
                motion = self.poses['motion']
                mover = SafeMover(limits.z_min, self.backend, motion['max_vel'], motion['max_acc'])
                if not target_only: self.get_gripper_status()
                for name, target in plan['route']:
                    if self.cancel.is_set(): raise MotionError('Cancelled; no automatic retreat or release')
                    mover.movel(target, vel=self.config.get('velocity', 10.0), acc=self.config.get('acceleration', 10.0), ref=DR_BASE, radius=0.0)
                    result['completed_steps'].append(name)
                    self.save(request.request_id, result)
                    if self.cancel.is_set(): raise MotionError('Cancelled; no automatic retreat or release')
                    if not target_only:
                        if name == 'approach': self.gripper_open()
                        if name == 'pick': self.gripper_close(plan['grasp_feedback'])
                        if name in ('lift', 'via', 'place_approach'): self.check_hold(plan['grasp_feedback'])
                        if name == 'place': self.gripper_open()
                result['state'] = 'completed'
            except Exception as exc:
                result.update(state='unknown', error=str(exc))
                if self.backend:
                    try: self.backend.stop()
                    except Exception as stop_error: result['stop_error'] = str(stop_error)
                # Ambiguous physical outcomes are never retried automatically.
            self.save(request.request_id, result)
            return result
        finally:
            self.lock.release()

    def close(self):
        if self.backend: self.backend.close()
