"""Lightweight algorithms extracted and adapted from AutoLab-SAI-SJTU/A2MEAS (Apache-2.0).

This module ports three pure-Python/NumPy capabilities from A²-MEAS for Doosan E0509 + RH-P12-RN:
1. PCA-based object major-axis & gripper yaw estimation (`estimate_mask_orientation`, `align_gripper_yaw_deg`).
2. Progress & failure-latching gate (`ProgressGate`) and bounded residual pose guard (`guard_pose_correction`).
3. Class-height prior + measured depth/stereo outlier-rejected fusion (`fuse_height_and_depth`).
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np


@dataclass(frozen=True)
class OrientationEstimate:
    """Principal axis orientation extracted from a binary object mask or point cloud."""
    angle_rad: float
    angle_deg: float
    axis_ratio: float
    elongated: bool
    pixel_count: int


def estimate_mask_orientation(
    mask: np.ndarray,
    *,
    min_pixels: int = 20,
    elongation_threshold: float = 1.6,
) -> OrientationEstimate | None:
    """Compute the major axis angle of a segmented mask using covariance eigenvectors.

    Adapted from `a2meas.robotwin.rsi.recovery.detections`.
    """
    binary = np.squeeze(np.asarray(mask)) > 0.5
    if binary.ndim != 2:
        raise ValueError("Expected a 2D binary mask")
    xy = np.argwhere(binary)[:, [1, 0]].astype(np.float64)
    if len(xy) <= min_pixels:
        return None
    values, vectors = np.linalg.eigh(np.cov(xy.T))
    axis = vectors[:, -1]
    angle_rad = float(np.arctan2(axis[1], axis[0]))
    ratio = float(values[-1] / max(float(values[0]), 1e-6))
    return OrientationEstimate(
        angle_rad=angle_rad,
        angle_deg=float(np.rad2deg(angle_rad)),
        axis_ratio=ratio,
        elongated=ratio >= elongation_threshold,
        pixel_count=int(len(xy)),
    )


def align_gripper_yaw_deg(
    base_zyz_deg: tuple[float, float, float] | list[float],
    orientation: OrientationEstimate | None,
    *,
    max_yaw_delta_deg: float = 90.0,
) -> tuple[float, float, float]:
    """Adjust the taught ZYZ gripper C-angle (yaw) for elongated objects within [-max_yaw_delta_deg, +max_yaw_delta_deg]."""
    a, b, c = (float(base_zyz_deg[0]), float(base_zyz_deg[1]), float(base_zyz_deg[2]))
    if orientation is None or not orientation.elongated:
        return (a, b, c)
    # Normalize angle to [-90, 90] so a symmetric two-finger gripper takes the shortest rotation
    delta = ((orientation.angle_deg + 90.0) % 180.0) - 90.0
    delta = float(np.clip(delta, -max_yaw_delta_deg, max_yaw_delta_deg))
    return (a, b, round(c + delta, 3))


@dataclass(frozen=True)
class FusedPoint3D:
    """3D point in robot BASE frame combining class height prior and depth/stereo measurement."""
    xyz_mm: tuple[float, float, float]
    used_depth_measurement: bool
    xy_discrepancy_mm: float
    z_discrepancy_mm: float


def fuse_height_and_depth(
    prior_xyz_mm: tuple[float, float, float] | list[float],
    measured_xyz_mm: tuple[float, float, float] | list[float] | None,
    *,
    max_xy_deviation_mm: float = 100.0,
    max_z_deviation_mm: float = 60.0,
) -> FusedPoint3D:
    """Fuse planar class-height prior coordinates with RealSense/stereo 3D measurements.

    Adapted from `a2meas.robotwin.rsi.recovery._plan_regrasp`: only accepts the depth/stereo
    refinement when it agrees with the calibrated height-plane ray within `max_xy_deviation_mm`.
    """
    prior = np.asarray(prior_xyz_mm, dtype=np.float64)
    if prior.shape != (3,) or not np.isfinite(prior).all():
        raise ValueError("prior_xyz_mm must be a finite 3D vector")
    if measured_xyz_mm is None:
        return FusedPoint3D(
            xyz_mm=(float(prior[0]), float(prior[1]), float(prior[2])),
            used_depth_measurement=False,
            xy_discrepancy_mm=0.0,
            z_discrepancy_mm=0.0,
        )
    measured = np.asarray(measured_xyz_mm, dtype=np.float64)
    if measured.shape != (3,) or not np.isfinite(measured).all():
        return FusedPoint3D(
            xyz_mm=(float(prior[0]), float(prior[1]), float(prior[2])),
            used_depth_measurement=False,
            xy_discrepancy_mm=float("inf"),
            z_discrepancy_mm=float("inf"),
        )
    xy_diff = float(np.linalg.norm(measured[:2] - prior[:2]))
    z_diff = float(abs(measured[2] - prior[2]))
    if xy_diff <= max_xy_deviation_mm and z_diff <= max_z_deviation_mm:
        fused = (float(measured[0]), float(measured[1]), float(measured[2]))
        return FusedPoint3D(
            xyz_mm=fused,
            used_depth_measurement=True,
            xy_discrepancy_mm=round(xy_diff, 3),
            z_discrepancy_mm=round(z_diff, 3),
        )
    return FusedPoint3D(
        xyz_mm=(float(prior[0]), float(prior[1]), float(prior[2])),
        used_depth_measurement=False,
        xy_discrepancy_mm=round(xy_diff, 3),
        z_discrepancy_mm=round(z_diff, 3),
    )


def guard_pose_correction(
    base_pose_mm_deg: list[float] | tuple[float, ...],
    proposed_pose_mm_deg: list[float] | tuple[float, ...],
    *,
    max_translation_mm: float = 30.0,
    max_rotation_deg: float = 11.5,
    min_effective_delta_mm: float = 0.5,
) -> tuple[list[float], dict[str, Any]]:
    """Bound a proposed 6D TCP pose adjustment against the base planned pose.

    Adapted from `a2meas.behavior.correction.actions.guarded_actions` (which caps residual
    end-effector translation to 30 mm and rotation to 0.20 rad ≈ 11.46 deg).
    """
    base = np.asarray(base_pose_mm_deg, dtype=np.float64)
    prop = np.asarray(proposed_pose_mm_deg, dtype=np.float64)
    if base.shape != (6,) or prop.shape != (6,) or not np.isfinite(prop).all():
        raise ValueError("Expected finite 6D poses [x, y, z, a, b, c]")

    trans_delta = float(np.linalg.norm(prop[:3] - base[:3]))
    rot_diffs = ((prop[3:] - base[3:] + 180.0) % 360.0) - 180.0
    rot_delta = float(np.max(np.abs(rot_diffs)))

    if trans_delta > max_translation_mm or rot_delta > max_rotation_deg:
        return base.tolist(), {
            "accepted": False,
            "reason": "action_geometry_outside_local_scope",
            "ee_delta_mm": round(trans_delta, 3),
            "rotation_delta_deg": round(rot_delta, 3),
        }
    if trans_delta < min_effective_delta_mm and rot_delta < 0.1:
        return base.tolist(), {
            "accepted": False,
            "reason": "action_correction_negligible",
            "ee_delta_mm": round(trans_delta, 3),
            "rotation_delta_deg": round(rot_delta, 3),
        }
    return prop.tolist(), {
        "accepted": True,
        "reason": "observable_fit_and_local_bounds",
        "ee_delta_mm": round(trans_delta, 3),
        "rotation_delta_deg": round(rot_delta, 3),
    }


class ProgressGate:
    """Local need, progress, release and rearm state machine ported from A²-MEAS.

    Ported from `a2meas.behavior.correction.gate.ProgressGate` to supervise repeated
    picking attempts and latch stalled/failed targets until operator rearm or a new target.
    """

    def __init__(self, *, max_unproductive_steps: int = 16):
        self.active_skill: str | None = None
        self.no_progress_since: int | None = None
        self.pending_skill: str | None = None
        self.pending_since: int | None = None
        self.pending_kind: str | None = None
        self.pending_reviews: int = 0
        self.trial_entry: bool = False
        self.uncertain_reviews: int = 0
        self.last_step: int = -1
        self.max_unproductive_steps: int = max_unproductive_steps
        self.blocked_problem: dict[str, Any] | None = None
        self.rearm_votes: int = 0
        self.rearm_key: tuple[str, str] | None = None
        self.last_local_review: dict[str, Any] | None = None
        self.unproductive_steps: int = 0

    def reset_pending(self) -> None:
        self.pending_skill = self.pending_since = None
        self.pending_kind = None
        self.pending_reviews = 0

    def release(self, reason: str) -> tuple[bool, str]:
        self.active_skill = self.no_progress_since = None
        self.uncertain_reviews = 0
        self.trial_entry = False
        return (False, reason)

    def _update_evidence(self, d: dict[str, Any], step: int) -> tuple[bool, str]:
        self.last_step = step
        sid, state, progress = (d["skill_id"], d["execution_status"], d["progress"])
        prior = self.active_skill
        local = sid != "none" and d["local_manipulation"] and (d["confidence"] >= 0.7)
        same = d["same_target_as_previous"]
        if not local:
            self.reset_pending()
            return self.release(("exit_" if prior else "skip_") + "unidentified_local_skill")
        resolved = state in ("recovered", "completed") or (
            state == "normal" and progress == "progressing"
        )
        if resolved:
            self.reset_pending()
            return self.release(("exit_" if prior else "skip_") + state)
        possible_need = (
            progress == "uncertain"
            and (d["confidence"] >= 0.7)
            and (d["need_confidence"] >= 0.5)
        )
        kind = (
            "nonprogress"
            if progress == "no_progress"
            else "trial"
            if possible_need
            else None
        )
        if kind:
            if self.pending_skill != sid or not same or self.pending_kind != kind:
                self.pending_skill, self.pending_since = (sid, step)
                self.pending_kind = kind
                self.pending_reviews = 1
            else:
                self.pending_reviews += 1
        else:
            self.reset_pending()
        repeated = (
            self.pending_kind == "nonprogress"
            and self.pending_skill == sid
            and same
            and (self.pending_since is not None)
            and (step - self.pending_since >= 32)
        )
        trial = (
            self.pending_kind == "trial"
            and self.pending_reviews >= 3
            and (self.pending_skill == sid)
            and same
            and (self.pending_since is not None)
            and (step - self.pending_since >= 64)
        )
        visible_need = (
            d["need_correction"]
            and d["need_confidence"] >= 0.65
            and (state in ("failed", "stalled"))
        )
        if prior == sid and same:
            if progress == "uncertain":
                self.uncertain_reviews += 1
                if self.trial_entry or self.uncertain_reviews > 1:
                    self.reset_pending()
                    return self.release("exit_uncertain_progress")
                return (True, "continue_one_review_grace")
            self.trial_entry = False
            self.uncertain_reviews = 0
            if progress == "no_progress":
                if self.no_progress_since is None:
                    self.no_progress_since = step - 16
                if step - self.no_progress_since >= 96:
                    self.reset_pending()
                    return self.release("exit_no_progress")
                return (True, "continue_observed_local_problem")
            self.no_progress_since = None
            if state == "recovering" and d["need_correction"]:
                return (True, "continue_recovery")
            return self.release("exit_progress_resumed")
        self.active_skill = None
        self.no_progress_since = None
        self.uncertain_reviews = 0
        if visible_need or repeated or trial:
            self.active_skill = sid
            self.trial_entry = trial and (not (visible_need or repeated))
            return (
                True,
                "enter_visible_failure"
                if visible_need
                else "enter_repeated_local_nonprogress"
                if repeated
                else "enter_unconfirmed_local_trial",
            )
        return (False, "skip_awaiting_local_need_evidence")

    def block(self, d: dict[str, Any], step: int, reason: str) -> tuple[bool, str]:
        self.blocked_problem = {
            "step": step,
            "target": d["target"],
            "skill_id": d["skill_id"],
            "reason": reason,
        }
        self.rearm_votes = 0
        self.rearm_key = None
        self.reset_pending()
        self.unproductive_steps = 0
        return self.release(reason)

    def update(self, d: dict[str, Any], step: int) -> tuple[bool, str]:
        if step <= self.last_step:
            raise ValueError("Review steps must strictly increase")
        previous_step = self.last_step
        if d["local_manipulation"] and d["skill_id"] != "none":
            self.last_local_review = dict(d)
        if self.blocked_problem:
            self.last_step = step
            local = (
                d["local_manipulation"]
                and d["skill_id"] != "none"
                and (d["confidence"] >= 0.7)
            )
            new_target = (
                d["rearm_reason"] == "new_target"
                and local
                and (
                    d["target"].strip().lower()
                    != self.blocked_problem["target"].strip().lower()
                )
            )
            progress = (
                d["rearm_reason"] == "visible_progress"
                and d["progress"] == "progressing"
                and (d["execution_status"] in ("normal", "recovered", "completed"))
            )
            if (new_target or progress) and d["rearm_confidence"] >= 0.8:
                key = (d["rearm_reason"], d["target"].strip().lower())
                self.rearm_votes = self.rearm_votes + 1 if key == self.rearm_key else 1
                self.rearm_key = key
                if self.rearm_votes >= 2:
                    self.blocked_problem = None
                    self.reset_pending()
                    self.unproductive_steps = 0
                    self.release("skip_rearmed_base")
                    return (False, "skip_rearmed_base")
            else:
                self.rearm_votes = 0
                self.rearm_key = None
            return self.release("skip_same_problem_latched")
        if self.active_skill:
            if d["progress"] != "progressing":
                self.unproductive_steps += step - previous_step
                if self.unproductive_steps >= self.max_unproductive_steps:
                    self.last_step = step
                    return self.block(
                        self.last_local_review or d, step, "exit_no_progress"
                    )
            else:
                self.unproductive_steps = 0
        active, reason = self._update_evidence(d, step)
        if reason in ("exit_no_progress", "exit_uncertain_progress"):
            return self.block(d, step, reason)
        if d["progress"] == "progressing" and d["confidence"] >= 0.7:
            self.unproductive_steps = 0
        return (active, reason)

    def action_rejected(self, d: dict[str, Any], step: int, reason: str) -> tuple[bool, str]:
        return self.block(d, step, reason)
