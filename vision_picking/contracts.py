from datetime import datetime, timezone
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator
from .pick_plane import CameraFrame


class Detection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    class_id: int = Field(ge=0)
    label: str = Field(min_length=1, max_length=128)
    confidence: FiniteFloat = Field(ge=0, le=1)
    xyxy: list[FiniteFloat] = Field(min_length=4, max_length=4)
    center_px: list[FiniteFloat] = Field(min_length=2, max_length=2)

    @model_validator(mode='after')
    def center(self):
        x1, y1, x2, y2 = self.xyxy
        if x2 <= x1 or y2 <= y1 or not all(math.isclose(a, b, abs_tol=1e-5) for a, b in zip(self.center_px, [(x1+x2)/2, (y1+y2)/2])):
            raise ValueError('Invalid bbox or inconsistent pixel center')
        return self


class PickRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    schema_version: Literal['1.0'] = '1.0'
    simulation_only: bool
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{8,64}$')
    camera: CameraFrame
    frame_id: int = Field(ge=1)
    captured_at: str
    model_version: str = Field(min_length=1, max_length=256)
    object_index: int = Field(ge=0)
    detection: Detection
    calibration_revision: str = Field(min_length=1, max_length=64)
    box_slot: int = Field(default=1, ge=1, le=4)

    @model_validator(mode='after')
    def frame(self):
        stamp = datetime.fromisoformat(self.captured_at.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('captured_at requires timezone')
        x1, y1, x2, y2 = self.detection.xyxy
        if not (0 <= x1 < x2 <= self.camera.width and 0 <= y1 < y2 <= self.camera.height):
            raise ValueError('bbox outside original source_size')
        return self

    def check_fresh(self, max_age=2.0):
        stamp = datetime.fromisoformat(self.captured_at.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if not -0.5 <= age <= max_age:
            raise ValueError('Stale frame or unsynchronized clocks')
