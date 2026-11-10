"""Named packing locations in the E0509 BASE frame (XYZ mm, ABC degrees)."""
import json
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from .store import DomainError, atomic_json, now

PointName = Literal["waypoint", "box", "shipping_label"]
TRANSFER_POINTS = ("waypoint", "box")


class WorkPointInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    pose: list[FiniteFloat] = Field(min_length=6, max_length=6)
    revision: str | None = Field(max_length=32)


class FloorReferenceInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    z_mm: FiniteFloat
    revision: str | None = Field(max_length=32)


class ParcelBoxInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    size_mm: list[Annotated[FiniteFloat, Field(ge=0.001, le=1_000_000)]] = Field(min_length=3, max_length=3)
    center_base_mm: list[Annotated[FiniteFloat, Field(ge=-1_000_000, le=1_000_000)]] = Field(min_length=3, max_length=3)
    revision: str | None = Field(max_length=32)


class ParcelBox:
    """Display geometry; center is the bottom face in BASE, not a robot TCP goal."""
    def __init__(self, path):
        self.path = path
        self.value = json.loads(path.read_text()) if path.exists() else dict(
            revision=None, updated_at=None)
        if self.value.get("size_mm") is None:
            self.value["size_mm"] = [400, 300, 250]
        if self.value.get("center_base_mm") is None:
            self.value["center_base_mm"] = [500, 0, 0]

    def save(self, body):
        if body.revision != self.value["revision"]:
            raise DomainError("다른 화면에서 택배 박스 설정을 변경했습니다. 최신 설정을 불러오세요.")
        value = dict(size_mm=body.size_mm, center_base_mm=body.center_base_mm,
                     revision=uuid4().hex, updated_at=now())
        atomic_json(self.path, value)
        self.value = value
        return value


class FloorReference:
    """Horizontal BASE plane shared by all cameras on this robot."""
    def __init__(self, path):
        self.path = path
        self.value = json.loads(path.read_text()) if path.exists() else {"revision": None}
        if self.value.get("z_mm") is None:
            self.value["z_mm"] = 0.0

    def save(self, body):
        if body.revision != self.value["revision"]:
            raise DomainError("다른 화면에서 바닥 기준을 변경했습니다. 최신 바닥 기준을 불러오세요.")
        value = {"z_mm": body.z_mm, "revision": uuid4().hex}
        atomic_json(self.path, value)
        self.value = value
        return value


class WorkPoints:
    def __init__(self, path):
        self.path = path
        self.points = {name: dict(pose=None, revision=None, updated_at=None)
                       for name in (*TRANSFER_POINTS, "shipping_label")}
        if path.exists():
            self.points.update(json.loads(path.read_text()))

    def save(self, name, body):
        if self.points[name]["revision"] != body.revision:
            raise DomainError("다른 화면에서 이 좌표를 변경했습니다. 저장값을 다시 불러오세요.")
        point = dict(pose=body.pose, revision=uuid4().hex, updated_at=now())
        updated = {**self.points, name: point}
        atomic_json(self.path, updated)
        self.points = updated
        return point
