import numpy as np
from fastapi.testclient import TestClient

from vision_picking.a2meas_extensions import (
    ProgressGate,
    align_gripper_yaw_deg,
    estimate_mask_orientation,
    fuse_height_and_depth,
    guard_pose_correction,
)
from vision_picking.controller import Controller
from vision_picking.server import create_app


def test_mask_orientation_and_gripper_yaw_alignment():
    mask = np.zeros((100, 100), dtype=np.uint8)
    # Horizontal elongated rectangle (width 50, height 8) -> ~0 deg
    mask[46:54, 25:75] = 1
    est = estimate_mask_orientation(mask)
    assert est is not None
    assert est.elongated is True
    assert abs(est.angle_deg) < 5.0
    adjusted = align_gripper_yaw_deg((0.0, 180.0, 10.0), est)
    assert abs(adjusted[2] - 10.0) < 5.0


def test_height_prior_and_depth_fusion_outlier_rejection():
    prior = (400.0, -50.0, 120.0)
    good_depth = (412.0, -45.0, 124.0)
    fused_good = fuse_height_and_depth(prior, good_depth)
    assert fused_good.used_depth_measurement is True
    assert fused_good.xyz_mm == good_depth

    outlier_depth = (650.0, -45.0, 124.0)
    fused_bad = fuse_height_and_depth(prior, outlier_depth)
    assert fused_bad.used_depth_measurement is False
    assert fused_bad.xyz_mm == prior


def test_guard_pose_correction_bounds():
    base = [400.0, 0.0, 120.0, 0.0, 180.0, 0.0]
    safe = [410.0, -5.0, 122.0, 0.0, 180.0, 5.0]
    pose, verdict = guard_pose_correction(base, safe)
    assert verdict["accepted"] is True
    assert pose == safe

    excessive = [460.0, 0.0, 120.0, 0.0, 180.0, 0.0]
    pose_rej, verdict_rej = guard_pose_correction(base, excessive)
    assert verdict_rej["accepted"] is False
    assert verdict_rej["reason"] == "action_geometry_outside_local_scope"
    assert pose_rej == base


def test_progress_gate_latches_and_rearms():
    gate = ProgressGate(max_unproductive_steps=16)
    review_fail = {
        "skill_id": "pick_snack",
        "execution_status": "stalled",
        "progress": "no_progress",
        "local_manipulation": True,
        "confidence": 0.9,
        "need_correction": True,
        "need_confidence": 0.85,
        "same_target_as_previous": True,
        "target": "snack_pepero",
        "rearm_reason": "none",
        "rearm_confidence": 0.0,
    }
    active, reason = gate.update(review_fail, step=10)
    assert active is True
    assert reason == "enter_visible_failure"

    # Exceed unproductive steps -> latches blocked_problem
    active, reason = gate.update(review_fail, step=30)
    assert active is False
    assert reason == "exit_no_progress"
    assert gate.blocked_problem is not None

    # Rearm on new target after 2 votes
    review_new = {
        **review_fail,
        "target": "beverage_pet",
        "execution_status": "normal",
        "progress": "progressing",
        "rearm_reason": "new_target",
        "rearm_confidence": 0.9,
    }
    assert gate.update(review_new, step=35) == (False, "skip_same_problem_latched")
    assert gate.update(review_new, step=40) == (False, "skip_rearmed_base")
    assert gate.blocked_problem is None


def test_a2meas_evaluate_pose_endpoint(tmp_path):
    controller = Controller(journal=tmp_path / "journal.sqlite3")
    with TestClient(create_app(controller)) as client:
        res = client.post(
            "/api/v1/a2meas/evaluate-pose",
            json={
                "base_pose": [400.0, 0.0, 120.0, 0.0, 180.0, 0.0],
                "proposed_pose": [408.0, 4.0, 120.0, 0.0, 180.0, 3.0],
                "prior_xyz_mm": [400.0, 0.0, 120.0],
                "measured_xyz_mm": [408.0, 4.0, 121.0],
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["used_depth_measurement"] is True
        assert data["guard"]["accepted"] is True
