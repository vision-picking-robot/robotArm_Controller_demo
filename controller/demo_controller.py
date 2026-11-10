"""Demo Robot Controller with extracted legacy configurations & E2E execution trace."""
import copy
from datetime import datetime
import json
import logging
from pathlib import Path
import threading
import time
import yaml

from .safe_move import DR_BASE, MotionError, SafeMover
from .gripper import GripperController

log = logging.getLogger("demo_controller.core")


class MockRobotBackend:
    """Simulates Doosan E0509 robot motion and tracks commands."""
    def __init__(self):
        self.commands = []
        self.current_posx = [373.0, 0.0, 285.0, 12.44, 180.0, 12.44]
        self.current_posj = [0.0, 0.0, 90.0, 0.0, 90.0, 0.0]

    def movel(self, pos, vel, acc, ref):
        self.commands.append({"kind": "movel", "target": list(pos), "vel": vel, "acc": acc, "ref": ref})
        self.current_posx = list(pos)
        time.sleep(0.05)
        return 0

    def movej(self, joints, vel, acc):
        self.commands.append({"kind": "movej", "target": list(joints), "vel": vel, "acc": acc})
        self.current_posj = list(joints)
        time.sleep(0.05)
        return 0

    def stop(self):
        self.commands.append({"kind": "stop"})
        return 0


class DemoRobotController:
    def __init__(self, config_dir=None):
        self.config_dir = Path(config_dir) if config_dir else Path(__file__).resolve().parents[1] / "config"
        self._load_configs()
        self.backend = MockRobotBackend()
        self.mover = SafeMover(self.floor_limit, self.backend, max_vel=30.0, max_acc=30.0)
        self.gripper = GripperController(mode="mock")
        self.trace = []
        self._lock = threading.Lock()
        self.busy = False
        log.info("DemoRobotController initialized. floor_z=%.1f, z_min=%.1f, home=%s",
                 self.floor_z, self.floor_limit, self.poses["home"]["posx"])

    def _load_configs(self):
        with open(self.config_dir / "poses.yaml", encoding="utf-8") as f:
            poses_data = yaml.safe_load(f)
        with open(self.config_dir / "products.yaml", encoding="utf-8") as f:
            products_data = yaml.safe_load(f)
        with open(self.config_dir / "gripper_profiles.yaml", encoding="utf-8") as f:
            grips_data = yaml.safe_load(f)

        self.poses = poses_data.get("poses", {})
        self.box_slots = poses_data.get("box_slots", {})
        self.floor_z = float(poses_data.get("floor", {}).get("floor_z", 0.0))
        self.safety_margin = float(poses_data.get("floor", {}).get("safety_margin", 10.0))
        self.nfc_protrusion = float(poses_data.get("floor", {}).get("nfc_protrusion", 0.0))
        self.floor_limit = self.floor_z + self.safety_margin + self.nfc_protrusion

        self.products = products_data.get("products", {})
        self.gripper_profiles = grips_data.get("profiles", {})

    def emit_event(self, phase, **fields):
        event = {
            "sequence": len(self.trace) + 1,
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "phase": phase,
            **fields
        }
        self.trace.append(event)
        log.info("[Pipeline Trace #%d] Phase=%s, Detail=%s", event["sequence"], phase, fields)
        return event

    def execute_pick_sequence(self, product_id, base_xy, slot_id=1, request_id="DEMO-E2E-01"):
        """Executes full pick-and-place pipeline with safety verification and E2E tracing."""
        with self._lock:
            self.busy = True
            try:
                log.info("Starting Pick Sequence: request_id=%s, product=%s, base_xy=%s, slot=%d",
                         request_id, product_id, base_xy, slot_id)
                self.emit_event("SEQUENCE_START", request_id=request_id, product_id=product_id, base_xy=base_xy, slot_id=slot_id)

                # 1. Product & Gripper Profile Lookup
                product_cfg = self.products.get(product_id, {}).get("real", {})
                if not product_cfg or not product_cfg.get("taught", True):
                    # Fallback to virtual or defaults
                    product_cfg = self.products.get(product_id, {}).get("virtual", {})
                grasp_height = product_cfg.get("grasp_height_from_floor", 50.0)
                grasp_z = self.floor_z + grasp_height

                grip_profile = self.gripper_profiles.get(product_id, {}).get("mock", {
                    "open_width_mm": 80, "target_width_mm": 20, "current_limit_ma": 120, "contact_current_ma": 30
                })

                # Target coordinates
                x, y = base_xy[0], base_xy[1]
                rz = product_cfg.get("rz", 0.0)
                grasp_pose = [x, y, grasp_z, 0.0, 180.0, rz]
                approach_pose = [x, y, grasp_z + 150.0, 0.0, 180.0, rz]

                # Safety check
                self.mover.validate_pose(grasp_pose)
                self.mover.validate_pose(approach_pose)

                # 2. Check Home
                home_pose = self.poses.get("home", {}).get("posx", [373.0, 0.0, 285.0, 12.44, 180.0, 12.44])
                self.emit_event("VERIFY_HOME", pose=home_pose)
                self.mover.movel(home_pose, vel=15.0, acc=15.0, ref=DR_BASE)

                # 3. Gripper Open
                self.emit_event("GRIPPER_PREPARE_OPEN", open_width_mm=grip_profile.get("open_width_mm", 80))
                self.gripper.open(width_mm=grip_profile.get("open_width_mm", 80))

                # 4. Approach to Pick Position (Z+150mm)
                self.emit_event("MOVE_APPROACH", pose=approach_pose)
                self.mover.movel(approach_pose, vel=20.0, acc=20.0, ref=DR_BASE)

                # 5. Descent to Grasp Height
                self.emit_event("MOVE_DESCENT", pose=grasp_pose, grasp_z=grasp_z, floor_limit=self.floor_limit)
                self.mover.movel(grasp_pose, vel=10.0, acc=10.0, ref=DR_BASE)

                # 6. Gripper Grip
                self.emit_event("GRIP_ACTION", target_width_mm=grip_profile.get("target_width_mm", 20))
                grip_result = self.gripper.close(width_mm=grip_profile.get("target_width_mm", 20),
                                                 current_limit_ma=grip_profile.get("current_limit_ma", 120))
                self.emit_event("GRIP_CONFIRMED", grip_result=grip_result)

                # 7. Lift to Clear Height (Z+150mm)
                self.emit_event("MOVE_LIFT", pose=approach_pose)
                self.mover.movel(approach_pose, vel=15.0, acc=15.0, ref=DR_BASE)

                # 8. Via Point to Parcel Box
                via_pose = self.poses.get("via_point", {}).get("posx", [0.0, 373.0, 285.0, 135.0, 180.0, 45.0])
                self.emit_event("MOVE_VIA_POINT", pose=via_pose)
                self.mover.movel(via_pose, vel=20.0, acc=20.0, ref=DR_BASE)

                # 9. Box Slot Approach & Place
                slot_info = self.box_slots.get(slot_id) or self.box_slots.get(1, {})
                slot_pose = slot_info.get("posx", [-373.0, 0.0, 85.0, 3.26, 180.0, -176.74])
                slot_approach = [slot_pose[0], slot_pose[1], slot_pose[2] + 150.0, slot_pose[3], slot_pose[4], slot_pose[5]]

                self.emit_event("MOVE_BOX_APPROACH", slot_id=slot_id, pose=slot_approach)
                self.mover.movel(slot_approach, vel=20.0, acc=20.0, ref=DR_BASE)

                self.emit_event("MOVE_BOX_PLACE", slot_id=slot_id, pose=slot_pose)
                self.mover.movel(slot_pose, vel=10.0, acc=10.0, ref=DR_BASE)

                # 10. Release Gripper
                self.emit_event("RELEASE_GRIPPER", slot_id=slot_id)
                self.gripper.open(width_mm=grip_profile.get("open_width_mm", 80))

                # 11. Retract from Box
                self.emit_event("MOVE_BOX_RETRACT", pose=slot_approach)
                self.mover.movel(slot_approach, vel=15.0, acc=15.0, ref=DR_BASE)

                # 12. Return Home
                self.emit_event("MOVE_RETURN_HOME", pose=home_pose)
                self.mover.movel(home_pose, vel=20.0, acc=20.0, ref=DR_BASE)

                self.emit_event("SEQUENCE_COMPLETED", status="SUCCESS", request_id=request_id)
                return {
                    "success": True,
                    "request_id": request_id,
                    "product_id": product_id,
                    "slot_id": slot_id,
                    "grasp_pose": grasp_pose,
                    "slot_pose": slot_pose,
                    "commands_count": len(self.backend.commands),
                    "trace_count": len(self.trace)
                }
            except Exception as e:
                self.emit_event("SEQUENCE_ERROR", error=str(e), request_id=request_id)
                log.error("Sequence error: %s", e)
                return {"success": False, "request_id": request_id, "error": str(e)}
            finally:
                self.busy = False
