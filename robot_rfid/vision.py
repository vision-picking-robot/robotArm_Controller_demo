from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class Detection:
    u: float
    v: float
    depth_m: float
    confidence: float


class Calibration:
    """Fixed overhead camera: T_base_camera, metres; RPY supplied in radians."""
    def __init__(self, intrinsics, base_from_camera, workspace_min, workspace_max, rpy):
        self.k = np.asarray(intrinsics, dtype=float)
        self.transform = np.asarray(base_from_camera, dtype=float)
        self.lower = np.asarray(workspace_min, dtype=float)
        self.upper = np.asarray(workspace_max, dtype=float)
        self.rpy = tuple(rpy)
        if self.k.shape != (3, 3) or self.transform.shape != (4, 4):
            raise ValueError('Expected 3x3 intrinsics and 4x4 transform')
        if not all(np.isfinite(x).all() for x in (self.k, self.transform, self.lower, self.upper)):
            raise ValueError('Calibration must be finite')
        if self.lower.shape != (3,) or self.upper.shape != (3,) or np.any(self.lower >= self.upper):
            raise ValueError('Invalid workspace bounds')
        rotation = self.transform[:3, :3]
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or not np.isclose(np.linalg.det(rotation), 1):
            raise ValueError('Transform rotation must be orthonormal')
        if not np.allclose(self.transform[3], [0, 0, 0, 1]) or self.k[0, 0] <= 0 or self.k[1, 1] <= 0:
            raise ValueError('Invalid calibration')
        if len(self.rpy) != 3 or not all(math.isfinite(v) for v in self.rpy):
            raise ValueError('Invalid gripper orientation')

    def pose(self, detection):
        if not all(math.isfinite(v) for v in (detection.u, detection.v, detection.depth_m)) or detection.depth_m <= 0:
            raise ValueError('Valid measured depth is required')
        ray = np.linalg.solve(self.k, [detection.u, detection.v, 1])
        point = (self.transform @ np.r_[ray * detection.depth_m, 1])[:3]
        if np.any(point < self.lower) or np.any(point > self.upper):
            raise ValueError('Pick point outside calibrated workspace')
        return (*point.tolist(), *self.rpy)


class YoloVision:
    def __init__(self, weights, frame_provider, depth_provider, calibration, confidence=0.6):
        from ultralytics import YOLO
        self.model = YOLO(weights)
        self.frame_provider = frame_provider
        self.depth_provider = depth_provider
        self.calibration = calibration
        self.confidence = confidence

    def detect(self, task):
        results = self.model.predict(self.frame_provider(), conf=self.confidence, verbose=False)
        candidates = []
        for result in results:
            for box in result.boxes:
                class_id = int(box.cls.item())
                if result.names[class_id] == task['yolo_class_name'] and class_id == task['target_class_id']:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    candidates.append(((x1 + x2) / 2, (y1 + y2) / 2, float(box.conf.item())))
        if not candidates:
            raise RuntimeError('Target object not detected')
        u, v, confidence = max(candidates, key=lambda item: item[2])
        return self.calibration.pose(Detection(u, v, self.depth_provider(u, v), confidence))
