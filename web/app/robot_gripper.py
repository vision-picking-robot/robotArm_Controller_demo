"""RH-P12 device calibration and operator commands on the robot web owner."""
import asyncio
import json
import math
from fractions import Fraction
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import Field, FiniteFloat, model_validator

from .pick_plane import Input
from .robot_access_protocol import local_json
from .robot_access_proxy import request_host
from .store import DomainError, atomic_json


class Sample(Input):
    position: int = Field(ge=0, le=700)
    width_mm: FiniteFloat = Field(ge=0)


class CalibrationInput(Input):
    revision: str | None = Field(max_length=32)
    points: list[Sample] = Field(min_length=2, max_length=701)

    @model_validator(mode="after")
    def monotone(self):
        self.points.sort(key=lambda p: p.position)
        if any(a.position >= b.position or a.width_mm <= b.width_mm for a,b in zip(self.points, self.points[1:])):
            raise ValueError("위치가 증가할수록 실측 간격이 작아지는 서로 다른 교시점을 등록하세요.")
        return self


class HandCommand(Input):
    session_id: str = Field(min_length=1, max_length=36)
    motion_token: str = Field(pattern="^[0-9a-f]{32}$")
    action: Literal["read", "position", "open", "close"]
    position: int | None = Field(default=None, ge=0, le=700)

    @model_validator(mode="after")
    def check_position(self):
        if (self.action == "position") != (self.position is not None):
            raise ValueError("지정 위치 명령에만 0~700의 목표값이 필요합니다.")
        return self

    @property
    def target(self):
        return {"read":-1, "open":0, "close":700, "position":self.position}[self.action]


class WidthPreview(Input):
    width_mm: FiniteFloat = Field(ge=0)
    revision: str | None = Field(max_length=32)


class WidthTest(WidthPreview):
    session_id: str = Field(min_length=1, max_length=36)
    motion_token: str = Field(pattern="^[0-9a-f]{32}$")


class ContactInput(Input):
    revision: str | None = Field(max_length=32)
    threshold_ma: int | None = Field(ge=1,le=1984)


class Gripper:
    def __init__(self, directory):
        self.path = directory / "rh-p12-1.json"
        self.table = json.loads(self.path.read_text()) if self.path.exists() else {"revision":None,"points":[]}
        self.task = None
        self.contact_path=directory/"rh-p12-contact.json"
        self.contact=json.loads(self.contact_path.read_text()) if self.contact_path.exists() else {"revision":None,"threshold_ma":None}

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def save(self, body):
        if body.revision != self.table["revision"]:
            raise DomainError("벌림표가 다른 화면에서 변경되었습니다. 다시 불러오세요.")
        table = {"revision":uuid4().hex, "points":[p.model_dump() for p in body.points]}
        atomic_json(self.path, table)
        self.table = table
        return table

    def position_for(self, width):
        points = self.table["points"]
        if not points:
            raise DomainError("그리퍼 관리에서 실측 벌림표를 먼저 교시하세요.")
        for a,b in zip(points,points[1:]):
            if b["width_mm"] <= width <= a["width_mm"]:
                high, low, required = (Fraction(str(v)) for v in (a["width_mm"],b["width_mm"],width))
                return a["position"] + math.floor((high-required)*(b["position"]-a["position"])/(high-low))
        raise DomainError(f"필요 벌림 {width:g} mm가 교시 범위 {points[-1]['width_mm']:g}–{points[0]['width_mm']:g} mm 밖입니다.")

    def width_at(self, position):
        for a,b in zip(self.table["points"], self.table["points"][1:]):
            if a["position"] <= position <= b["position"]:
                t = (position-a["position"])/(b["position"]-a["position"])
                return (1-t)*a["width_mm"] + t*b["width_mm"]
        return None


router = APIRouter(prefix="/gripper")


@router.get("/contact")
async def contact(request:Request):return request.app.state.robot.hand.contact


@router.put("/contact",dependencies=[Depends(local_json)])
async def save_contact(body:ContactInput,request:Request):
    robot=request.app.state.robot
    if robot.picks.busy or robot.hand.busy or robot.pose_tests.snapshot()["busy"]:raise DomainError("로봇 작업이 끝난 뒤 접촉 설정을 저장하세요.")
    if body.revision!=robot.hand.contact["revision"]:raise DomainError("다른 화면에서 접촉 설정을 변경했습니다. 최신 값을 불러오세요.")
    value={**body.model_dump(),"revision":uuid4().hex};atomic_json(robot.hand.contact_path,value);robot.hand.contact=value
    robot.scenario.prepared=None
    return value


@router.get("/table")
async def table(request: Request):
    return request.app.state.robot.hand.table


@router.put("/table", dependencies=[Depends(local_json)])
async def save_table(body: CalibrationInput, request: Request):
    robot = request.app.state.robot
    if robot.picks.busy or robot.hand.busy:
        raise DomainError("진행 중인 작업이 끝난 뒤 벌림표를 저장하세요.")
    return robot.hand.save(body)


@router.post("/command", dependencies=[Depends(local_json)])
async def manual(body: HandCommand, request: Request):
    robot = request.app.state.robot
    if robot.settings.access_backend != "mac-sdk":
        raise DomainError("RH-P12 조작에는 Mac SDK가 필요합니다.")
    if robot.hand.busy or robot.picks.busy or robot.pose_tests.snapshot()["busy"]:
        raise DomainError("진행 중인 로봇 작업이 끝난 뒤 조작하세요.")
    robot.hand.task = asyncio.create_task(asyncio.to_thread(request_host, "gripper", robot.settings, body))
    code, result = await asyncio.shield(robot.hand.task)
    if code != 200:
        raise DomainError(result.get("detail") or "그리퍼 상태를 확인할 수 없습니다.", code)
    return {k:v for k,v in result.items() if k != "next_motion_token"}


def width_preview(hand, body):
    if body.revision != hand.table["revision"]:
        raise DomainError("벌림표가 변경되었습니다. 저장값을 다시 불러온 뒤 테스트하세요.")
    position = hand.position_for(body.width_mm)
    return {"revision": hand.table["revision"], "width_mm": body.width_mm,
            "position": position, "estimated_width_mm": hand.width_at(position)}


@router.post("/test/preview", dependencies=[Depends(local_json)])
async def preview_test(body: WidthPreview, request: Request):
    return width_preview(request.app.state.robot.hand, body)


@router.post("/test", dependencies=[Depends(local_json)])
async def test_width(body: WidthTest, request: Request):
    target = width_preview(request.app.state.robot.hand, body)
    result = await manual(HandCommand(session_id=body.session_id, motion_token=body.motion_token,
                                     action="position", position=target["position"]), request)
    feedback = result.get("feedback") or {}
    actual = feedback.get("position")
    return {**result, "target": target,
            "actual_width_mm": request.app.state.robot.hand.width_at(actual) if isinstance(actual, int) else None}
