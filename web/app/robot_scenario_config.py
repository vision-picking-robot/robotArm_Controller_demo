"""Versioned, per-robot recipe. Virtual targets are never execution tickets."""
import copy
import json
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, FiniteFloat, model_validator

from .pick_plane import Input
from .robot_poses import LIMITS
from .store import DomainError, atomic_json

Six = Annotated[list[FiniteFloat], Field(min_length=6, max_length=6)]
Three = Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)]
Level = Literal["high", "medium", "low"]
REGIONS = ("floor", "shelf")
STAGES = [
    (1,"ready","대기 상태로 준비"),(2,"target","YOLO 상품 요청"),
    (3,"joint","상품 접근 위치"),(4,"gripper","권장 폭으로 벌리기"),
    (5,"line","파지점으로 직선 접근"),(6,"gripper","접촉 확인 후 파지 유지"),
    (7,"line","접근 위치로 후퇴"),(8,"joint","1차 경유점"),
    (9,"joint","J1 회전 · 2차 경유점"),(10,"joint","박스 위 위치"),
    (11,"line","박스 놓기 위치로 하강"),(12,"gripper","파지 전 벌림으로 개방"),
    (13,"line","박스 위로 상승"),(14,"joint","2차 경유점 복귀"),
    (15,"joint","J1 역회전 · 1차 경유점"),(16,"joint","대기 자세 복귀"),
    (17,"gripper","대기 벌림 복원")]
LOW_STAGES = {5,6,7,11,12,13}
ARM_STAGES = {n for n,k,_ in STAGES if k in {"ready","joint","line"}}
FIXED_MOTIONS = {5:"movel",7:"movel",9:"movej",11:"movel",13:"movel",15:"movej"}
MOTION_KINDS = {"movej":"joint","movel":"line","movec":"circle"}


def motion_for(config,number):
    kind=next(k for n,k,_ in STAGES if n==number)
    if number not in ARM_STAGES:return None
    return config["stages"][str(number)].get("motion") or ("movel" if kind=="line" else "movej")


def check_joints(values):
    if values is not None and any(abs(v)>limit for v,limit in zip(values,LIMITS)):
        raise ValueError("관절 범위는 J1·J2·J4·J6 ±360°, J3·J5 ±155°입니다.")
    return values


class ArmSpeed(Input):
    speed: FiniteFloat = Field(gt=0, le=10)
    acceleration: FiniteFloat = Field(gt=0, le=10)


class HandSpeed(Input):
    speed: FiniteFloat = Field(gt=0, le=10)  # rpm; never zero (unlimited)
    acceleration: FiniteFloat = Field(gt=0, le=1_378_788)


class Preset(Input):
    joint: ArmSpeed
    line: ArmSpeed
    gripper: HandSpeed


def default_profiles():
    return {name:dict(joint=dict(speed=arm,acceleration=arm),line=dict(speed=arm,acceleration=arm),
                      gripper=dict(speed=hand,acceleration=3000))
            for name,arm,hand in [("high",10,10),("medium",7.5,5),("low",5,2)]}


class StageSpeed(Input):
    profile: Level
    override: HandSpeed | None = None
    motion: Literal["movej","movel","movec"] | None = None
    via_pose: Six | None = None
    target_joints: Six | None = None


class VirtualProduct(Input):
    center_base_mm: Three
    size_mm: Three

    @model_validator(mode="after")
    def dimensions(self):
        if any(v<=0 or v>1000 for v in self.size_mm) or any(abs(v)>1e6 for v in self.center_base_mm):
            raise ValueError("가상 상품의 크기와 BASE 좌표를 확인하세요.")
        return self


class Approach(Input):
    distance_mm: FiniteFloat = Field(default=100,gt=0,le=1000)
    reverse: bool = False


class ScenarioInput(Input):
    model_config = {**Input.model_config, "validate_default": True}
    revision: str | None = Field(default=None,max_length=32)
    standby_joints: Six = Field(default_factory=lambda:[0,0,90,0,90,0])
    standby_opening_mm: FiniteFloat | None = Field(default=None,gt=0,le=1000)
    waypoint1_joints: Six | None = None
    turn_degrees: Literal[180,-180] = 180
    box_clearance_mm: FiniteFloat = Field(default=100,gt=0,le=1000)
    approaches: dict[Literal["floor","shelf"],Approach] = Field(default_factory=lambda:{r:Approach() for r in REGIONS})
    profiles: dict[Level,Preset] = Field(default_factory=lambda:default_profiles())
    stages: dict[str,StageSpeed] = Field(default_factory=lambda:{str(n):dict(profile="low" if n in LOW_STAGES else "high") for n,k,_ in STAGES if k!="target"})
    virtual: dict[Literal["floor","shelf"],VirtualProduct] = Field(default_factory=lambda:{
        "floor":dict(center_base_mm=[-450,0,20],size_mm=[40,40,40]),
        "shelf":dict(center_base_mm=[-450,0,300],size_mm=[40,40,40])})

    @model_validator(mode="after")
    def recipe(self):
        check_joints(self.standby_joints);check_joints(self.waypoint1_joints)
        if self.waypoint1_joints:
            second=[self.waypoint1_joints[0]+self.turn_degrees,*self.waypoint1_joints[1:]]
            check_joints(second)
        if set(self.approaches)!=set(REGIONS) or set(self.virtual)!=set(REGIONS) or set(self.profiles)!={"high","medium","low"}:
            raise ValueError("바닥·선반 설정과 상·중·하 프로파일이 모두 필요합니다.")
        for kind in ("joint","line","gripper"):
            speeds=[getattr(self.profiles[level],kind).speed for level in ("high","medium","low")]
            if speeds!=sorted(speeds,reverse=True):raise ValueError("속도 프로파일은 상 ≥ 중 ≥ 하 순서로 설정하세요.")
        if set(self.stages)!={str(n) for n,k,_ in STAGES if k!="target"}:
            raise ValueError("동작 단계별 속도 설정을 모두 제공하세요.")
        for n,k,_ in STAGES:
            if k=="target":continue
            stage=self.stages[str(n)]
            if n in {3,5,7}:
                stage.target_joints=None
            check_joints(stage.target_joints)
            if n == 1 and stage.target_joints is not None:
                raise ValueError("1단계의 관절각은 대기 자세에서 설정하세요.")
            if n not in ARM_STAGES and (stage.motion is not None or stage.via_pose is not None or stage.target_joints is not None):
                raise ValueError(f"{n}단계는 팔 이동이 없는 단계입니다.")
            if n in FIXED_MOTIONS and stage.motion not in {None,FIXED_MOTIONS[n]}:
                raise ValueError(f"{n}단계는 파지 방향·Z축 이동 또는 J1 단독 회전을 유지해야 하므로 {FIXED_MOTIONS[n]}만 사용할 수 있습니다.")
            if k!="gripper" and stage.override and stage.override.acceleration>10:
                raise ValueError("팔의 가속도 상한은 10입니다.")
        return self


class ScenarioConfig:
    def __init__(self,path,floor_z=0):
        self.path=path
        defaults=ScenarioInput().model_dump()
        defaults["virtual"]["floor"]["center_base_mm"][2]+=floor_z
        defaults["virtual"]["shelf"]["center_base_mm"][2]+=floor_z
        self.value=ScenarioInput.model_validate(json.loads(path.read_text()) if path.exists() else defaults).model_dump()

    def save(self,body):
        if body.revision!=self.value["revision"]:
            raise DomainError("다른 화면에서 시나리오를 변경했습니다. 최신 설정을 불러오세요.")
        value=body.model_dump();value["revision"]=uuid4().hex
        atomic_json(self.path,value);self.value=value
        return value


def example_scenario():
    """A complete, repeatable browser-only recipe; never persisted as robot teaching."""
    config=ScenarioInput().model_dump()
    config.update(waypoint1_joints=[-180,0,60,0,120,0],standby_opening_mm=80,box_clearance_mm=200)
    config["virtual"]["shelf"]["center_base_mm"][2]=450
    config["stages"]["8"]["via_pose"]=[-421.975537,.000449,290.654122,44.999991,179.999802,0]
    return dict(config=config,box_pose=[450,0,120,0,180,0],
                box=dict(size_mm=[400,300,250],center_base_mm=[450,0,0]),floor_z_mm=0)


def speed_for(config,number,kind):
    setting=config["stages"][str(number)]
    motion=motion_for(config,number)
    speed_kind="joint" if motion=="movej" else "line" if motion in {"movel","movec"} else kind
    return copy.deepcopy(setting["override"] or config["profiles"][setting["profile"]][speed_kind])


class KinematicsRequest(Input):
    session_id: str = Field(min_length=1,max_length=36)
    poses: list[Six] = Field(default_factory=list,max_length=4)
    joints: list[Six] = Field(default_factory=list,max_length=5)


class ScenarioCommand(Input):
    session_id: str = Field(min_length=1,max_length=36)
    motion_token: str = Field(pattern="^[0-9a-f]{32}$")
    kind: Literal["joint","line","circle","gripper"]
    target: Six | None = None
    via_pose: Six | None = None
    position: int | None = Field(default=None,ge=0,le=700)
    speed: FiniteFloat = Field(gt=0,le=10)
    acceleration: FiniteFloat = Field(gt=0,le=1_378_788)
    contact_ma: int | None = Field(default=None,gt=0,le=1984)
    tcp_name: str = Field(max_length=128)
    reference_tcp: Six
    j1_only: bool = False
    timeout_s: FiniteFloat = Field(default=120,ge=5,le=600)

    @model_validator(mode="after")
    def parameters(self):
        if self.kind=="gripper":
            if self.position is None or self.target is not None or self.j1_only:
                raise ValueError("그리퍼 명령에는 위치값이 필요합니다.")
            if self.contact_ma is not None and self.position!=700:
                raise ValueError("접촉 검출은 닫기 명령에만 사용할 수 있습니다.")
        elif self.target is None or self.position is not None or self.contact_ma is not None or self.acceleration>10:
            raise ValueError("팔 이동의 목표·가속도를 확인하세요.")
        if self.kind=="joint":check_joints(self.target)
        if (self.kind=="circle")!=(self.via_pose is not None):raise ValueError("원호 이동에는 경유 TCP 여섯 좌표가 필요합니다.")
        if self.j1_only and self.kind!="joint":raise ValueError("J1 단독 이동은 관절 이동입니다.")
        return self


class OperationStop(Input):
    session_id: str = Field(min_length=1,max_length=36)
    operation_id: str = Field(pattern="^[0-9a-f]{32}$")
