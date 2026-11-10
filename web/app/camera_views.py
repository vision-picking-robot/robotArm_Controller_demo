"""Render dashboard views from the same capture as the detector input."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import numpy as np

DisplayMode = Literal["detections", "color", "depth", "infrared", "rgbd", "cloud"]
VIDEO_MODES = ("detections", "color")


@dataclass
class SensorFrame:
    color: np.ndarray
    depth: np.ndarray | None    # metres, aligned to color
    infrared: np.ndarray | None
    intrinsics: tuple | None   # fx, fy, ppx, ppy of the aligned image

    def modes(self):
        modes = list(VIDEO_MODES)
        if self.depth is not None:
            modes.extend(("depth", "rgbd"))
            if self.intrinsics:
                modes.append("cloud")
        if self.infrared is not None:
            modes.append("infrared")
        return modes


def point_cloud(frame):
    """Project valid depth samples into an oblique view around the scene centre."""
    import numpy as np

    height, width = frame.depth.shape
    y, x = np.mgrid[:height:2, :width:2]
    z = frame.depth[y, x]
    valid = z > 0
    x, y, z = x[valid], y[valid], z[valid]
    canvas = np.zeros_like(frame.color)
    if not z.size:
        return canvas
    fx, fy, cx, cy = frame.intrinsics
    pivot = np.median(z)
    px, py = (x - cx) * z / fx, (y - cy) * z / fy
    # Rotate around the vertical axis by 25 degrees.
    sideways = 0.9063 * px + 0.4226 * (z - pivot)
    distance = pivot - 0.4226 * px + 0.9063 * (z - pivot)
    visible = distance > 0.01
    x, y, sideways, py, distance = (a[visible] for a in (x, y, sideways, py, distance))
    u = np.rint(fx * sideways / distance + cx).astype(int)
    v = np.rint(fy * py / distance + cy).astype(int)
    inside = (u >= 0) & (u < width) & (v >= 0) & (v < height)
    order = np.flatnonzero(inside)[np.argsort(-distance[inside])]
    canvas[v[order], u[order]] = frame.color[y[order], x[order]]
    return canvas


def render_view(mode, frame, sensors, objects):
    if mode == "color":
        return frame, []
    if mode == "infrared":
        return sensors.infrared, []
    if mode == "cloud":
        return point_cloud(sensors), []
    import cv2
    import numpy as np

    # A fixed 0–6 m scale keeps the same distance the same color across frames.
    depth = sensors.depth
    valid = depth > 0
    colors = cv2.applyColorMap(np.uint8(255 * (1 - np.clip(depth / 6, 0, 1))), cv2.COLORMAP_TURBO)
    colors[~valid] = 0
    if mode == "depth":
        return colors, []
    mixed = cv2.addWeighted(frame, 0.55, colors, 0.45, 0)
    mixed[~valid] = frame[~valid]
    annotations = []
    for obj in objects:
        x1, y1, x2, y2 = obj["xyxy"]
        cx, cy = round((x1 + x2) / 2), round((y1 + y2) / 2)
        sample = depth[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3]
        samples = sample[sample > 0]
        label = obj["label"]
        if samples.size:
            label += f" {np.median(samples):.2f} m"
        annotations.append({**obj, "label": label})
    return mixed, annotations
