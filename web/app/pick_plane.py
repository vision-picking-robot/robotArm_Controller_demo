"""Project detector pixels onto a taught planar quadrilateral in robot BASE."""
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

from .store import DomainError


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CameraFrame(Input):
    session_id: str = Field(min_length=1, max_length=64)
    camera_generation: int = Field(ge=1)
    camera_id: str = Field(min_length=1, max_length=512)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @classmethod
    def from_packet(cls, packet):
        if not packet.get("camera_id"):
            raise DomainError("카메라 장치를 식별할 수 없습니다. 입력 카메라를 선택하고 적용하세요.")
        return cls(session_id=packet["session_id"], camera_generation=packet["camera_generation"],
                   camera_id=packet["camera_id"], **packet["source_size"])


class Corner(Input):
    pixel: list[FiniteFloat] = Field(min_length=2, max_length=2)
    pose: list[FiniteFloat] = Field(min_length=6, max_length=6)


class Calibration(Input):
    camera: CameraFrame
    region: Literal["legacy","floor","shelf"] = "legacy"
    revision: str | None = Field(max_length=32)
    corners: list[Corner] = Field(min_length=4, max_length=4)
    clearance_per_side_mm: FiniteFloat = Field(default=5.0, ge=0)


class PreviewCorner(Corner):
    # Pixel → BASE projection needs XYZ even when the common ABC is still being taught.
    pose: list[FiniteFloat | None] = Field(min_length=6, max_length=6)

    @field_validator("pose")
    @classmethod
    def require_xyz(cls, pose):
        if None in pose[:3]:
            raise ValueError("네 모서리의 BASE XYZ를 입력하세요.")
        return pose


class PreviewCalibration(Calibration):
    corners: list[PreviewCorner] = Field(min_length=4, max_length=4)
    clearance_per_side_mm: FiniteFloat | None = Field(default=5.0, ge=0)


def area(a, b, c):
    """Signed double area of a triangle."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def convex(vertices):
    turns = [area(vertices[i-2], vertices[i-1], vertices[i]) for i in range(4)]
    span = max(math.dist(a, b) for a in vertices for b in vertices)
    epsilon = max(1e-6, span * span * 1e-6)
    if not (min(turns) > epsilon or max(turns) < -epsilon):
        raise DomainError("네 모서리를 서로 떨어진 위치에 둘레 순서대로 등록하세요.", 422)


def triangle_weights(vertices, point):
    a, b, c = vertices[:3]
    scale = area(a, b, c)
    return (area(point, b, c) / scale, area(a, point, c) / scale, area(a, b, point) / scale)


class PickPlane:
    def __init__(self, calibration):
        self.camera = camera = calibration.camera
        self.clearance = calibration.clearance_per_side_mm
        self.pixels = [p.pixel for p in calibration.corners]
        if any(not (0 <= u < camera.width and 0 <= v < camera.height) for u, v in self.pixels):
            raise DomainError("영상 안에서 네 모서리를 선택하세요.", 422)
        convex(self.pixels)
        self.xyz = [p.pose[:3] for p in calibration.corners]
        if any(abs(v) > 1e6 for p in calibration.corners for v in p.pose if v is not None):
            raise DomainError("BASE 좌표와 공구 방향의 단위를 확인하세요.", 422)
        self.abc = calibration.corners[0].pose[3:]
        a, b, c, d = self.xyz
        x, y, z = (v - u for v, u in zip(b, a))
        i, j, k = (v - u for v, u in zip(c, a))
        normal = (y*k - z*j, z*i - x*k, x*j - y*i)
        length = math.hypot(*normal)
        if length < 1e-6:
            raise DomainError("교시한 TCP 좌표가 한 직선 위에 있습니다.", 422)
        self.normal = tuple(n / length for n in normal)
        residual = abs(sum(n*(v-u) for n, v, u in zip(normal, d, a))) / length
        if residual > 2:
            raise DomainError("네 번째 TCP가 교시 평면에서 2 mm 넘게 벗어났습니다.", 422)
        # Drop the most normal axis for a well-conditioned 2D basis on the BASE plane.
        drop = max(range(3), key=lambda axis: abs(normal[axis]))
        plane = [[v for axis, v in enumerate(p) if axis != drop] for p in self.xyz]
        convex(plane)
        # In homogeneous barycentric coordinates the first three vertices are the
        # basis. The fourth correspondence fixes the three scales up to a common factor.
        self.scales = [b/a for a, b in zip(triangle_weights(self.pixels, self.pixels[3]),
                                            triangle_weights(plane, plane[3]))]

    def contains(self, pixel):
        turns = [area(self.pixels[i-1], self.pixels[i], pixel) for i in range(4)]
        return min(turns) >= -1e-7 or max(turns) <= 1e-7

    def project(self, pixel):
        weights = [s*w for s, w in zip(self.scales, triangle_weights(self.pixels, pixel))]
        total = sum(weights)
        if abs(total) <= 1e-12:
            return None
        return [sum(w*p[axis] for w, p in zip(weights, self.xyz)) / total for axis in range(3)]

    def target(self, pixel):
        xyz = self.project(pixel) if self.contains(pixel) else None
        if xyz is None:
            raise DomainError("선택한 중심이 보정한 작업 영역 밖에 있습니다.")
        return xyz + self.abc

    def gripper_estimate(self, xyxy, *, tcp=None):
        """Planar footprint about the pick TCP, and symmetric opening along tool X."""
        result = {"clearance_per_side_mm": self.clearance, "center_base_mm": None, "center_reason": None}

        def unavailable(reason):
            return {**result, "reason": reason}

        left, top, right, bottom = xyxy
        if not all(map(math.isfinite, xyxy)) or right <= left or bottom <= top:
            result["center_reason"] = "탐지 상자의 크기를 확인할 수 없습니다."
            return unavailable("탐지 상자의 크기를 확인할 수 없습니다.")
        pixel = ((left + right) / 2, (top + bottom) / 2)
        if self.contains(pixel):
            result["center_base_mm"] = self.project(pixel)
            if result["center_base_mm"] is None:
                result["center_reason"] = "탐지 중심을 작업면에 투영할 수 없습니다."
        else:
            result["center_reason"] = "선택한 중심이 보정한 작업 영역 밖에 있습니다."
        if left <= 0 or top <= 0 or right >= self.camera.width - 1 or bottom >= self.camera.height - 1:
            return unavailable("탐지 상자가 영상 가장자리에 닿아 전체 폭을 측정할 수 없습니다.")
        corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
        if not all(self.contains(p) for p in corners):
            return unavailable("탐지 상자의 일부가 교시 영역 밖에 있습니다.")

        center = tcp if tcp is not None else result["center_base_mm"]
        points = [self.project(p) for p in corners]
        if center is None or any(p is None for p in points):
            return unavailable("탐지 상자를 작업면에 투영할 수 없습니다.")
        footprint = [[x-y for x, y in zip(p, center)] for p in points]
        result["footprint_base_mm"] = footprint
        if None in self.abc or self.clearance is None:
            return unavailable("공구 방향과 한쪽 여유를 입력해야 벌림을 계산할 수 있습니다.")
        a, b, c = map(math.radians, self.abc)
        # First column of Rz(A) Ry(B) Rz(C), expressed in BASE.
        tool_x = (math.cos(a)*math.cos(b)*math.cos(c) - math.sin(a)*math.sin(c),
                  math.sin(a)*math.cos(b)*math.cos(c) + math.cos(a)*math.sin(c),
                  -math.sin(b)*math.cos(c))
        normal_component = sum(x*n for x, n in zip(tool_x, self.normal))
        planar_axis = [x - normal_component*n for x, n in zip(tool_x, self.normal)]
        length = math.hypot(*planar_axis)
        if length <= 1e-6:
            return unavailable("손가락 벌림축이 작업면에 수직이어서 폭을 측정할 수 없습니다.")
        direction = [v / length for v in planar_axis]
        extents = [sum(x*d for x, d in zip(p, direction)) for p in footprint]
        opening = 2 * (max(map(abs, extents)) + self.clearance)
        if not math.isfinite(opening):
            return unavailable("계산 가능한 벌림 범위를 초과했습니다.")
        return {**result, "reason": None, "width_mm": max(extents) - min(extents), "opening_mm": opening}
