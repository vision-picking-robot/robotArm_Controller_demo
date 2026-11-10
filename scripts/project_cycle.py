"""Full project cycle on taught poses (RFID scenario order, no camera).

  home -> pre_scan (label_scan_approach -> label_scan -> up) -> RFID tag -> product/box
  -> gripper open -> approach (taught product x,y, +approach_offset_z) -> descend to grasp height
  -> gripper close -> lift -> via_point -> box slot (high -> place) -> release -> retreat -> home

TCP: taught posx values are fingertip (TCP) coordinates. At start the script measures
fkin(home.posj).z - home.posx.z. If the controller has no TCP active this is the tool length
(120 mm on this cell) and every Cartesian target is converted TCP -> flange by adding it to z
(valid because every target points straight down, ry=180). If a TCP is active it measures 0.
The SafeMover floor check always runs on TCP z.

Real hardware needs `--mode real --confirm-real`. Errors or Ctrl+C send move_stop; no retries.
"""
import argparse
import json
import math
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vision_picking.legacy.safe_move import DR_BASE, SafeMover  # noqa: E402

TAGS = {'0B71E55D': ('snack_pepero', 1), '0B71E75D': ('beverage_pet', 2), '0B71E65D': ('canned_meat', 3),
        '0B71ED5D': ('wet_tissue', 4), '0B71EC5D': ('doll', 5)}
OPEN_MAX = 10
JOINT_TOL = 1.0
POS_TOL = 2.0


class Logger:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.file = path, path.open('a', encoding='utf-8')

    def __call__(self, event, **fields):
        line = json.dumps(dict(time=datetime.now().isoformat(timespec='milliseconds'), event=event, **fields),
                          ensure_ascii=False)
        print(line, flush=True)
        self.file.write(line + '\n')
        self.file.flush()

    def error(self, msg):
        self('safety_block', message=msg)


class FlangeOffset:
    """SafeMover backend that receives TCP targets and drives the flange (z + offset)."""

    def __init__(self, raw, offset):
        self.raw, self.offset = raw, offset

    def _shift(self, pose, sign):
        return [pose[0], pose[1], pose[2] + sign * self.offset, *pose[3:]]

    def get_current_posx(self, ref):
        cur = self.raw.get_current_posx(ref)
        return None if cur is None else self._shift(cur, -1)

    def get_current_posj(self):
        return self.raw.get_current_posj()

    def fkin(self, posj, ref):
        pose = self.raw.fkin(posj, ref)
        return None if pose is None else self._shift(pose, -1)

    def movel(self, pos, vel, acc, ref, mod, radius):
        return self.raw.movel(self._shift(pos, +1), vel, acc, ref, mod, radius)

    def movej(self, pos, vel, acc, mod, radius):
        return self.raw.movej(pos, vel, acc, mod, radius)


class MockRaw:
    """Flange-level mock with a 120 mm tool, straight-down orientation only."""
    mock = True

    def __init__(self, poses):
        self.table = {tuple(v['posj']): [*v['posx'][:2], v['posx'][2] + 120.0, *v['posx'][3:]] for v in poses}
        self.posj = [0.0, 0.0, 90.0, 0.0, 90.0, 0.0]
        self.posx = self.fkin(self.posj, 0)
        self.position = 0

    def check_ready(self):
        return None

    def fkin(self, posj, ref):
        if tuple(posj) in self.table:
            return list(self.table[tuple(posj)])
        a = math.radians(posj[0])  # J1 rotation of the home pose
        return [373.0 * math.cos(a), 373.0 * math.sin(a), 405.0, 0.0, 180.0, 0.0]

    def get_current_posj(self):
        return list(self.posj)

    def get_current_posx(self, ref):
        return list(self.posx)

    def movej(self, pos, vel, acc, mod, radius):
        self.posj, self.posx = list(pos), self.fkin(pos, 0)
        return 0

    def movel(self, pos, vel, acc, ref, mod, radius):
        self.posx = list(pos)
        return 0

    def grip(self, target):
        if target >= 0:
            self.position = target
        return dict(code=1, result='ok', voltage=24.1, position_pulses=self.position, current=0, moving=0)

    def scan(self):
        return '0B71E55D'

    def stop(self):
        pass

    def close(self):
        pass


class RealRaw:
    mock = False

    def __init__(self, namespace):
        from vision_picking.backends import RosBackend
        from vision_picking.drl_gripper import DrlGripper
        self.ros = RosBackend(namespace)
        self.hand = DrlGripper(self.ros.node, self.ros.executor, namespace)
        self.scan_timeout = 5.0

    def check_ready(self):
        self.ros.check_ready()

    def fkin(self, posj, ref):
        return self.ros.motion.fkin(posj, ref)

    def get_current_posj(self):
        return self.ros.motion.get_current_posj()

    def get_current_posx(self, ref):
        return self.ros.motion.get_current_posx(ref)

    def movej(self, pos, vel, acc, mod, radius):
        return self.ros.motion.movej(pos, vel, acc, mod, radius)

    def movel(self, pos, vel, acc, ref, mod, radius):
        return self.ros.motion.movel(pos, vel, acc, ref, mod, radius)

    def grip(self, target):
        return self.hand.run(target)

    def scan(self):
        try:
            from robot_rfid.rfid_scanner import RFIDScanner
            return RFIDScanner().scan(timeout=self.scan_timeout)
        except Exception:
            return None

    def stop(self):
        self.ros.stop()

    def close(self):
        self.ros.close()


def load_config(product):
    poses = yaml.safe_load((ROOT / 'config/poses.yaml').read_text(encoding='utf-8'))
    products = yaml.safe_load((ROOT / 'config/products.yaml').read_text(encoding='utf-8'))['products']
    for name in ('home', 'label_scan_approach', 'label_scan', 'via_point'):
        if not poses['poses'][name].get('taught'):
            raise SystemExit(f'{name} is not taught')
    return poses, products


class Cycle:
    def __init__(self, raw, poses, products, args, log):
        self.raw, self.poses, self.products, self.args, self.log = raw, poses, products, args, log
        floor = poses['floor']
        self.z_min = floor['floor_z'] + floor['safety_margin'] + floor['nfc_protrusion']
        home = poses['poses']['home']
        flange = raw.fkin(home['posj'], DR_BASE)
        if flange is None:
            raise RuntimeError('fkin failed; cannot determine TCP offset')
        dx, dy, dz = (flange[i] - home['posx'][i] for i in range(3))
        if abs(dx) > POS_TOL or abs(dy) > POS_TOL or not (-POS_TOL <= dz <= 250):
            raise RuntimeError(f'Taught home posx does not match fkin(posj): d=({dx:.1f},{dy:.1f},{dz:.1f})')
        self.offset = 0.0 if abs(dz) <= POS_TOL else round(dz, 1)
        self.mover = SafeMover(self.z_min, FlangeOffset(raw, self.offset), poses['motion']['max_vel'],
                               poses['motion']['max_acc'], logger=log)
        log('tcp_offset', flange_minus_taught_z=round(dz, 2), applied_offset=self.offset, z_min=self.z_min)

    def j(self, name, posj):
        t0 = time.monotonic()
        self.mover.movej(posj, self.args.jvel, self.args.jvel)
        cur = self.raw.get_current_posj()
        err = max(abs(a - b) for a, b in zip(cur, posj))
        if err > JOINT_TOL:
            raise RuntimeError(f'{name}: joint arrival error {err:.2f} deg')
        self.log('step', name=name, kind='movej', seconds=round(time.monotonic() - t0, 1))

    def l(self, name, pose, vel=None):
        t0 = time.monotonic()
        self.mover.movel(pose, vel or self.args.lvel, vel or self.args.lvel, DR_BASE)
        cur = FlangeOffset(self.raw, self.offset).get_current_posx(DR_BASE)
        err = max(abs(a - b) for a, b in zip(cur[:3], pose[:3]))
        if err > POS_TOL:
            raise RuntimeError(f'{name}: TCP arrival error {err:.1f} mm')
        self.log('step', name=name, kind='movel', tcp=[round(v, 1) for v in cur[:3]],
                 seconds=round(time.monotonic() - t0, 1))

    def grip(self, name, target, ok):
        fb = self.raw.grip(target)
        if fb['code'] != 1 or fb['moving'] or not ok(fb['position_pulses']):
            raise RuntimeError(f"{name}: gripper not confirmed ({fb['result']}, pos={fb['position_pulses']})")
        self.log('step', name=name, kind='gripper', position=fb['position_pulses'], current=fb['current'])
        return fb

    # ---- Steps matching robot_rfid.orchestrator.Orchestrator's robot interface ----
    def pre_scan(self):
        P = self.poses['poses']
        self.raw.check_ready()
        # Recovery: if an earlier stop left the tool low, rise straight up before any joint move.
        cur = FlangeOffset(self.raw, self.offset).get_current_posx(DR_BASE)
        safe_z = P['home']['posx'][2]
        if cur is not None and cur[2] < safe_z - POS_TOL:
            self.log('recovery', tcp=[round(v, 1) for v in cur[:3]], rise_to=safe_z)
            self.l('recovery_rise', [cur[0], cur[1], safe_z, *cur[3:6]], self.args.dvel)
        self.j('home', P['home']['posj'])
        self.j('label_scan_approach', P['label_scan_approach']['posj'])
        scan_hi = P['label_scan_approach']['posx']
        self.l('label_scan', [*scan_hi[:2], P['label_scan']['posx'][2], *scan_hi[3:]], self.args.dvel)

    def scan(self):
        P = self.poses['poses']
        uid = self.raw.scan() or self.args.tag
        self.log('rfid', uid=uid, reader='real' if uid != self.args.tag else 'preset')
        self.l('label_scan_retract', P['label_scan_approach']['posx'])
        self.j('home_after_scan', P['home']['posj'])
        return uid

    def detect(self, task):
        product = task.get('yolo_class_name') if isinstance(task, dict) else task
        if product not in self.products or not self.products[product]['real'].get('taught'):
            self.log('vision_fallback', requested=product, used=self.args.product)
            product = self.args.product
        item = self.products[product]['real']
        grasp_z = self.poses['floor']['floor_z'] + item['grasp_height_from_floor']
        self.log('vision', source='taught product pose (no camera)', product=product)
        return dict(product=product, pose=[item['x'], item['y'], grasp_z, 0.0, 180.0, item.get('rz', 0.0)])

    def approach(self, target):
        up = self.poses['motion']['approach_offset_z']
        grasp = target['pose']
        self._above = [*grasp[:2], grasp[2] + up, *grasp[3:]]
        self._product = target['product']
        self.grip('gripper_open', 0, lambda p: p <= OPEN_MAX)
        self.l('approach', self._above)
        self.l('descend', grasp, self.args.dvel)

    def grip_object(self):
        closed = self.grip('grip', 740, lambda p: p >= 0)
        self.holding = closed['position_pulses'] < 700
        self.log('grasp', product=self._product, holding=self.holding, position=closed['position_pulses'],
                 current=closed['current'], note='object detected' if self.holding else 'empty close (no object)')
        if self.args.require_object and not self.holding:
            raise RuntimeError('No object in the gripper (empty close)')

    def retract(self):
        self.l('lift', self._above)
        self.l('home_after_lift', self.poses['poses']['home']['posx'])

    def place(self, box_id=1):
        P, up = self.poses['poses'], self.poses['motion']['approach_offset_z']
        slot = int(str(box_id).split('-')[-1]) if box_id else 1
        if not (self.poses['box_slots'].get(slot) or {}).get('taught'):
            self.log('box_fallback', requested=box_id, used_slot=1, reason='slot not taught')
            slot = 1
        slot_pose = self.poses['box_slots'][slot]['posx']
        self.j('via_point', P['via_point']['posj'])
        self.j('box_high', [P['home']['posj'][0] + 180.0, *P['home']['posj'][1:]])
        self.l('box_place', list(slot_pose), self.args.dvel)
        self.grip('release', 0, lambda p: p <= OPEN_MAX)
        self.l('box_retreat', [*slot_pose[:2], slot_pose[2] + up * 4 / 3, *slot_pose[3:]])
        self.j('via_return', P['via_point']['posj'])
        self.last_slot = slot

    def home(self):
        self.j('home_return', self.poses['poses']['home']['posj'])

    def stop(self):
        self.raw.stop()

    def run(self, cycle):
        self.pre_scan()
        uid = self.scan()
        product, box = TAGS.get(uid, (self.args.product, 1))
        target = self.detect(product)
        self.approach(target)
        self.grip_object()
        self.retract()
        self.place(box)
        self.home()
        self.log('cycle_ok', cycle=cycle, product=target['product'], box_slot=self.last_slot, holding=self.holding)


class OrchestratorRobot:
    """Adapter: Orchestrator robot/scanner/vision interfaces -> Cycle steps."""

    def __init__(self, cycle):
        self.c = cycle

    def pre_scan(self): self.c.pre_scan()
    def scan(self): return self.c.scan()
    def detect(self, task): return self.c.detect(task)
    def approach(self, pose): self.c.approach(pose)
    def grip(self): self.c.grip_object()
    def retract(self): self.c.retract()
    def place(self, box): self.c.place(box)
    def home(self): self.c.home()
    def stop(self): self.c.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=['mock', 'real'], default='mock')
    parser.add_argument('--confirm-real', action='store_true')
    parser.add_argument('--namespace', default='dsr01')
    parser.add_argument('--cycles', type=int, default=1, help='0 = until Ctrl+C or first error')
    parser.add_argument('--product', default='snack_pepero', help='fallback when no RFID reader is attached')
    parser.add_argument('--tag', default='0B71E55D', help='preset tag used when no RFID reader is attached')
    parser.add_argument('--jvel', type=float, default=10.0, help='deg/s')
    parser.add_argument('--lvel', type=float, default=30.0, help='mm/s for transfer moves')
    parser.add_argument('--dvel', type=float, default=15.0, help='mm/s for descents')
    parser.add_argument('--log', type=Path)
    parser.add_argument('--require-object', action='store_true', help='fail when the gripper closes empty')
    parser.add_argument('--api', help='run the repo RFID Orchestrator against this web API, e.g. http://127.0.0.1:8000')
    args = parser.parse_args(argv)
    if args.mode == 'real' and not args.confirm_real:
        parser.error('--mode real requires --confirm-real')
    poses, products = load_config(args.product)
    log = Logger(args.log or ROOT / 'state' / 'project_cycle' / f'{args.mode}-{datetime.now():%Y%m%d-%H%M%S}.jsonl')
    taught = [*poses['poses'].values(), poses['box_slots'][1]]
    raw = MockRaw(taught) if args.mode == 'mock' else RealRaw(args.namespace)

    def on_signal(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    log('start', mode=args.mode, cycles=args.cycles or 'infinite', product=args.product, log=str(log.path))
    cycle, code = 0, 0
    try:
        runner = Cycle(raw, poses, products, args, log)
        while args.cycles == 0 or cycle < args.cycles:
            cycle += 1
            if args.api:
                import logging
                from robot_rfid.orchestrator import APIClient, Orchestrator
                logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
                adapter = OrchestratorRobot(runner)
                task = Orchestrator(APIClient(args.api), adapter, adapter, adapter).run_box(lambda: None)
                log('cycle_ok', cycle=cycle, scenario='orchestrator', task=task)
            else:
                runner.run(cycle)
    except KeyboardInterrupt:
        log('interrupted', cycle=cycle)
        code = 130
    except Exception as error:  # noqa: BLE001 - stop on anything, never retry motion
        log('error', cycle=cycle, type=type(error).__name__, message=str(error))
        code = 1
    finally:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        try:
            raw.stop()
            log('move_stop_sent')
        except Exception as error:  # noqa: BLE001
            log('move_stop_failed', message=str(error))
        raw.close()
        log('end', completed_cycles=cycle - (1 if code else 0), exit_code=code)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
