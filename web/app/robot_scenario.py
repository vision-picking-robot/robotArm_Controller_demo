"""17-step recipes with versioned approvals for detected objects and configured hardware tests."""
import asyncio
import copy
import math
import time
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .pick_plane import CameraFrame, Input, PickPlane
from .robot_access_protocol import local_json
from .robot_access_proxy import request_host
from .robot_scenario_config import (ScenarioConfig, ScenarioInput, ScenarioCommand, OperationStop,
                                    KinematicsRequest, Six, STAGES, speed_for, motion_for, MOTION_KINDS, example_scenario)
from .robot_arc import validate_arc
from .store import DomainError


class SimulationPreview(Input):
    region: Literal["floor","shelf"] = "floor"
    config: ScenarioInput | None = None
    box_pose: Six | None = None
    example: bool = False


class HardwareTest(Input):
    region: Literal["floor","shelf"] = "floor"
    revision: str | None = None
    config: ScenarioInput | None = None
    box_pose: Six | None = None


class HardwareStep(HardwareTest):
    step: int = Field(ge=1,le=17)


class Identifier(Input):
    approval_id: str


def checked_response(code,data):
    if code!=200:raise DomainError(data.get("detail") or "SDK 상태를 확인할 수 없습니다.",503)
    return data


def approach_pose(config,region,pose,normal=None):
    direction=[0,0,1]
    if region=="shelf":
        direction=list(normal or [-1,0,0]);direction[2]=0
        length=math.hypot(*direction)
        if length<.95:raise DomainError("선반 파지면의 법선이 수평이어야 합니다.")
        direction=[v/length for v in direction]
        if sum(v*p for v,p in zip(direction,pose[:3]))>0:direction=[-v for v in direction]
        if config["approaches"][region]["reverse"]:direction=[-v for v in direction]
    distance=config["approaches"][region]["distance_mm"]
    return [pose[i]+direction[i]*distance for i in range(3)]+pose[3:]


def compile_steps(config,pose,approach,box,opening,idle):
    w1=config["waypoint1_joints"]
    w2=[w1[0]+config["turn_degrees"],*w1[1:]] if w1 else None
    above=[box[0],box[1],box[2]+config["box_clearance_mm"],*box[3:]] if box else None
    targets={1:config["standby_joints"],3:approach,5:pose,7:approach,8:w1,9:w2,10:above,11:box,13:above,14:w2,15:w1,16:config["standby_joints"]}
    result=[]
    for number,kind,title in STAGES:
        motion=motion_for(config,number)
        step=dict(number=number,kind=MOTION_KINDS[motion] if motion and kind!="ready" else kind,title=title,motion=motion)
        if motion=="movec":step["via_pose"]=copy.deepcopy(config["stages"][str(number)].get("via_pose"))
        if kind!="target":
            step["speed"]=speed_for(config,number,kind)
            step["profile"]=config["stages"][str(number)]["profile"]
        if number in targets:
            step["target"]=copy.deepcopy(targets[number]);step["space"]="joint" if number in {1,8,9,14,15,16} else "task"
            override=config["stages"][str(number)].get("target_joints") if number not in {3,5,7} else None
            if override is not None:
                step.update(target=copy.deepcopy(override),space="joint",joint_override=True)
        if number in {4,12,17}:step["opening_mm"]=idle if number==17 else opening
        if number==6:step["contact"]=True
        if number in {9,15} and not step.get("joint_override") and not config["stages"][str(8 if number==9 else 14)].get("target_joints"):step["j1_only"]=True
        if number in {6,11}:step["gate"]="confirm-grip" if number==6 else "open-at-box"
        result.append(step)
    return result


def resolve_controller_steps(steps,kin):
    """Resolve stored joint poses with this active TCP; never guess a BASE pose."""
    fk_index={1:0,8:1,9:2,14:2,15:1,16:0}
    for index,step in enumerate(s for s in steps if s.get("joint_override")):
        fk_index[step["number"]]=3+index
    previous=kin["forward"][0]
    for step in steps:
        number=step["number"]
        if not step["motion"]:continue
        task=kin["forward"][fk_index[number]] if step["space"]=="joint" else step["target"]
        if number==1:continue  # Preparation has its own start position and approval.
        if step["motion"]=="movej" and step["space"]=="task":step["joints"]=kin["solutions"][0 if number==3 else 1]
        if step["motion"]!="movej":
            if step["space"]=="joint":step["expected_joints"]=copy.deepcopy(step["target"])
            step.update(target=copy.deepcopy(task),space="task")
        if step["motion"]=="movec":
            try:validate_arc(previous,step.get("via_pose"),task)
            except DomainError as exc:raise DomainError(f"{number}단계: {exc}") from exc
        previous=task


class Scenario:
    def __init__(self,robot,directory):
        self.robot=robot
        self.config=ScenarioConfig(directory/"pick-scenario.json",robot.floor.value["z_mm"])
        self.prepared=None
        self.runtimes={}
        self.stop_generation=0

    @property
    def picks(self):return self.robot.picks

    def require_idle(self):
        if self.picks.busy or self.robot.hand.busy or self.robot.pose_tests.snapshot()["busy"]:
            raise DomainError("진행 중인 로봇 작업이 끝난 뒤 설정하거나 준비하세요.")

    def versions(self):
        return dict(config=self.config.value["revision"],box=self.robot.work_points.points["box"]["revision"],
                    hand=self.robot.hand.table["revision"],contact=self.robot.hand.contact["revision"],stop=self.stop_generation)

    def idle_width(self,config,virtual=False):
        width=config["standby_opening_mm"]
        if width is None:
            points=self.robot.hand.table["points"]
            width=max((p["width_mm"] for p in points),default=80 if virtual else 0)
        if width<=0:raise DomainError("그리퍼 실측 벌림표를 먼저 등록하세요.")
        return width

    def virtual_preview(self,body):
        demo=example_scenario() if body.example else None
        config=body.config.model_dump() if body.config else copy.deepcopy(demo["config"] if demo else self.config.value)
        product=config["virtual"][body.region];pose=[*product["center_base_mm"],*( [0,180,0] if body.region=="floor" else [180,90,90])]
        a,b,c=map(math.radians,pose[3:]);tool_x=[math.cos(a)*math.cos(b)*math.cos(c)-math.sin(a)*math.sin(c),
            math.sin(a)*math.cos(b)*math.cos(c)+math.cos(a)*math.sin(c),-math.sin(b)*math.cos(c)]
        width=sum(abs(x)*s for x,s in zip(tool_x,product["size_mm"]))
        opening=width+10
        approach=approach_pose(config,body.region,pose)
        box=body.box_pose or (demo["box_pose"] if demo else self.robot.work_points.points["box"]["pose"])
        errors=[]
        if config["waypoint1_joints"] is None:errors.append("1차 경유점의 관절각을 설정하세요.")
        if box is None:errors.append("택배 박스 놓기 TCP를 설정하세요.")
        for n,_,_ in STAGES:
            if motion_for(config,n)=="movec" and config["stages"][str(n)].get("via_pose") is None:
                errors.append(f"{n}단계 movec의 원호 경유 TCP를 설정하세요.")
        return dict(simulation_only=True,example=body.example,region=body.region,product=product,pose=pose,approach=approach,config=config,
                    box=demo["box"] if demo else self.robot.parcel_box.value,box_pose=box,floor_z_mm=0 if demo else self.robot.floor.value["z_mm"],
                    opening_mm=opening,width_mm=width,clearance_per_side_mm=5,
                    idle_opening_mm=self.idle_width(config,True),errors=errors,
                    steps=compile_steps(config,pose,approach,box,opening,self.idle_width(config,True)))

    async def host(self,operation,body=None):
        code,data=await asyncio.to_thread(request_host,operation,self.robot.settings,body)
        return checked_response(code,data)

    async def inspect(self,poses=None,joints=None):
        if self.robot.settings.access_backend!="mac-sdk":raise DomainError("17단계 실제 이송에는 Mac SDK가 필요합니다.")
        status=await self.host("status")
        if not status.get("scenario_supported"):raise DomainError("17단계·중단·좌표 조회를 지원하는 SDK 작업자를 시작하세요.")
        if not status.get("can_move"):raise DomainError(status.get("motion_reason") or "SDK 제어권·자동 모드·STANDBY를 확인하세요.")
        result=await self.host("kinematics",KinematicsRequest(session_id=status["session_id"],poses=poses or [],joints=joints or []))
        if result.get("session_id")!=status["session_id"]:raise DomainError("SDK 세션이 변경되었습니다.")
        return status,result

    def validate_hand(self,kin,config,stage=None,require_contact=True):
        hand=kin.get("hand") or {}
        if hand.get("mode")!=5 or hand.get("error")!=0 or not hand.get("torque_known",False) or hand.get("moving") or type(hand.get("position")) is not int or not 0<=hand["position"]<=700:
            raise DomainError("RH-P12 전류 기반 위치 모드와 장치 상태를 확인하세요.")
        if not 0<hand.get("goal_current_ma",0)<=hand.get("limit_ma",0):raise DomainError("그리퍼 목표 전류와 전류 한계를 확인하세요.")
        threshold=self.robot.hand.contact["threshold_ma"]
        if require_contact and (threshold is None or threshold>hand["goal_current_ma"]):raise DomainError("그리퍼 관리에서 접촉 전류 임계값을 목표 전류 이내로 설정하세요.")
        profiles=[(n,speed_for(config,n,k)) for n,k,_ in STAGES if k=="gripper"]
        profiles.append((1,config["profiles"][config["stages"]["1"]["profile"]]["gripper"]))
        if stage is not None:profiles=[(n,speed) for n,speed in profiles if n==stage]
        for n,speed in profiles:
            if speed["speed"]*100>hand.get("velocity_limit",0) or speed["acceleration"]>hand.get("acceleration_limit",0):
                raise DomainError(f"{n}단계 그리퍼 속도·가속도가 장치의 한계를 넘습니다.")
        return threshold

    def ticket(self,data):
        self.picks.approvals={k:v for k,v in self.picks.approvals.items() if v["deadline"]>time.monotonic()}
        if len(self.picks.approvals)>=128:raise DomainError("대기 중인 승인이 많습니다.")
        identifier=uuid4().hex
        self.picks.approvals[identifier]={**data,"deadline":time.monotonic()+30,"versions":self.versions()}
        return identifier

    async def prepare_preview(self):
        generation=self.stop_generation
        self.require_idle();config=copy.deepcopy(self.config.value)
        opening=self.idle_width(config);position=self.robot.hand.position_for(opening)
        status,kin=await self.inspect(joints=[config["standby_joints"]]);self.validate_hand(kin,config)
        motion=motion_for(config,1);via=config["stages"]["1"].get("via_pose")
        if motion!="movej" and not status.get("circle_supported"):raise DomainError("단계별 이동 방식을 지원하는 SDK 작업자로 갱신하세요.")
        goal=config["standby_joints"] if motion=="movej" else kin["forward"][0]
        if motion=="movec":
            if not status.get("circle_supported"):raise DomainError("movec 지원 SDK 작업자로 갱신하세요.")
            validate_arc(kin["current_tcp"],via,goal)
        target=dict(kind="prepare",config=config,status=status,kin=kin,idle_position=position,opening=opening,prepare_target=goal)
        if generation!=self.stop_generation:raise DomainError("중단 요청으로 준비 승인을 취소했습니다.")
        identifier=self.ticket(target)
        return dict(approval_id=identifier,expires_in=30,joints=config["standby_joints"],opening_mm=opening,
                    hand_position=position,speed=speed_for(config,1,"ready"),motion=motion,via_pose=via if motion=="movec" else None,
                    target=goal,gripper_speed=config["profiles"][config["stages"]["1"]["profile"]]["gripper"])

    async def inspect_steps(self,config,steps,approach,box):
        waypoint=config["waypoint1_joints"]
        above=[box[0],box[1],box[2]+config["box_clearance_mm"],*box[3:]]
        status,kin=await self.inspect(poses=[approach,above],joints=[config["standby_joints"],waypoint,[waypoint[0]+config["turn_degrees"],*waypoint[1:]]])
        overrides=[s["target"] for s in steps if s.get("joint_override")]
        for start in range(0,len(overrides),5):
            _,extra=await self.inspect(joints=overrides[start:start+5])
            if any(extra.get(key)!=kin.get(key) for key in ("session_id","tcp_name","reference_tcp")):
                raise DomainError("SDK 세션 또는 TCP가 변경되었습니다. 다시 미리보기하세요.")
            kin["forward"].extend(extra["forward"])
        if any(s["motion"]=="movec" for s in steps) and not status.get("circle_supported"):
            raise DomainError("movec 지원 SDK 작업자로 갱신하세요.")
        if any(s.get("space")=="joint" and s["motion"]!="movej" for s in steps) and not status.get("circle_supported"):
            raise DomainError("관절 교시점의 TCP 이동 결과를 확인할 수 있는 SDK 작업자로 갱신하세요.")
        return status,kin

    async def preview_real(self,selection,reader):
        generation=self.stop_generation
        packet=await asyncio.to_thread(reader,selection.session_id,selection.frame_id)
        if packet.get('simulation_only'):raise DomainError('합성 탐지 프레임은 실물 시나리오에 사용할 수 없습니다.',412)
        camera=CameraFrame.from_packet(packet)
        if not 0<=selection.object_index<len(packet["objects"]):raise DomainError("선택한 객체가 없습니다.")
        obj=packet["objects"][selection.object_index]
        region,calibration,plane=self.picks.region_for(camera,((obj["xyxy"][0]+obj["xyxy"][2])/2,(obj["xyxy"][1]+obj["xyxy"][3])/2))
        pose=plane.target(((obj["xyxy"][0]+obj["xyxy"][2])/2,(obj["xyxy"][1]+obj["xyxy"][3])/2))
        config=copy.deepcopy(self.config.value);box=copy.deepcopy(self.robot.work_points.points["box"])
        approach=approach_pose(config,region,pose,plane.normal)
        estimate=plane.gripper_estimate(obj["xyxy"]);opening=estimate.get("opening_mm")
        reason=None;status=kin=None;position=idle_position=threshold=None;idle=80
        steps=compile_steps(config,pose,approach,box["pose"],opening,idle)
        try:
            if config["waypoint1_joints"] is None:raise DomainError("1차 경유점의 관절각을 설정하세요.")
            if box["pose"] is None:raise DomainError("택배 박스 놓기 TCP를 설정하세요.")
            if estimate.get("reason"):raise DomainError(estimate["reason"])
            position=self.robot.hand.position_for(opening);idle=self.idle_width(config);idle_position=self.robot.hand.position_for(idle)
            steps=compile_steps(config,pose,approach,box["pose"],opening,idle)
            status,kin=await self.inspect_steps(config,steps,approach,box["pose"])
            threshold=self.validate_hand(kin,config)
            if not self.prepared or self.prepared!={"session":status["session_id"],"versions":self.versions(),"tcp":kin["tcp_name"],"reference":kin["reference_tcp"]}:
                raise DomainError("매니퓰레이터에서 ‘대기 상태로 준비’를 먼저 승인하세요.")
            if any(abs(a-b)>.5 for a,b in zip(kin["current_joints"],config["standby_joints"])) or abs(kin["hand"]["position"]-idle_position)>2:
                self.prepared=None;raise DomainError("대기 자세 또는 벌림이 변경되었습니다. 대기 상태를 다시 준비하세요.")
            resolve_controller_steps(steps,kin)
        except DomainError as exc:reason=str(exc)
        target=dict(kind="scenario",config=config,status=status,kin=kin,steps=steps,region=region,camera=camera,
                    revision=calibration["revision"],model=packet["model_version"],class_id=obj["class_id"],plane=plane,
                    pose=pose,opening=opening,position=position,idle_position=idle_position,threshold=threshold,
                    label=obj["label"],reason=reason,route={"box":box},handling="rh_p12",mode="transfer")
        if generation!=self.stop_generation:raise DomainError("중단 요청으로 이동 승인을 취소했습니다.")
        identifier=self.ticket(target)
        return dict(approval_id=identifier,expires_in=30,scenario=True,region=region,pose=pose,approach=approach,
                    label=obj["label"],confidence=obj["confidence"],motion_reason=reason,gripper=estimate,
                    hand_position=position,handling="rh_p12",mode="transfer",route={"box":box},steps=steps,tcp_name=(kin or {}).get("tcp_name"))

    async def preview_hardware_test(self,body):
        self.require_idle()
        versions=self.versions()
        config=body.config.model_dump() if body.config else copy.deepcopy(self.config.value)
        if body.revision!=self.config.value["revision"]:
            raise DomainError("시나리오가 변경되었습니다. 최신 설정을 불러오세요.",412)
        preview=self.virtual_preview(SimulationPreview(region=body.region,config=ScenarioInput.model_validate(config),box_pose=body.box_pose))
        if preview["errors"]:
            raise DomainError(" ".join(preview["errors"]))
        steps=preview["steps"]
        idle=self.idle_width(config)
        position=self.robot.hand.position_for(preview["opening_mm"])
        idle_position=self.robot.hand.position_for(idle)
        status,kin=await self.inspect_steps(config,steps,preview["approach"],preview["box_pose"])
        threshold=self.validate_hand(kin,config)
        resolve_controller_steps(steps,kin)
        motion=motion_for(config,1)
        goal=config["standby_joints"] if motion=="movej" else kin["forward"][0]
        if motion=="movec":
            validate_arc(kin["current_tcp"],config["stages"]["1"].get("via_pose"),goal)
        steps[1]["title"]="설정한 객체 좌표 확인"
        for step in steps:step.pop("gate",None)
        target=dict(kind="hardware-test",config=config,status=status,kin=kin,steps=steps,
                    region=body.region,pose=preview["pose"],opening=preview["opening_mm"],
                    position=position,idle_position=idle_position,threshold=threshold,
                    prepare_target=goal,test_box_pose=preview["box_pose"],label="설정 좌표 · 실제 17단계 테스트")
        if versions!=self.versions():
            raise DomainError("테스트 경로를 확인하는 동안 설정이 변경되었습니다. 다시 확인하세요.",412)
        identifier=self.ticket(target)
        return dict(approval_id=identifier,expires_in=30,region=body.region,pose=preview["pose"],
                    opening_mm=preview["opening_mm"],idle_opening_mm=idle,steps=steps,
                    tcp_name=kin["tcp_name"],standby_joints=config["standby_joints"])

    async def preview_hardware_step(self,body):
        self.require_idle()
        versions=self.versions()
        if body.revision!=self.config.value["revision"]:
            raise DomainError("시나리오가 변경되었습니다. 최신 설정을 불러오세요.",412)
        config=body.config.model_dump() if body.config else copy.deepcopy(self.config.value)
        preview=self.virtual_preview(SimulationPreview(region=body.region,config=ScenarioInput.model_validate(config),box_pose=body.box_pose))
        number=max(step["number"] for step in preview["steps"][:body.step] if step.get("motion"))
        step=copy.deepcopy(preview["steps"][number-1])
        if step["target"] is None:
            raise DomainError("선택한 단계의 목표 위치를 먼저 설정하세요.")
        motion=step["motion"]
        status,kin=await self.inspect(poses=[step["target"]] if step["space"]=="task" else [],
                                      joints=[step["target"]] if step["space"]=="joint" else [])
        task=kin["forward"][0] if step["space"]=="joint" else step["target"]
        expected=step["target"] if step["space"]=="joint" and motion!="movej" else None
        goal=(step["target"] if step["space"]=="joint" else kin["solutions"][0]) if motion=="movej" else task
        if motion=="movec":
            if not status.get("circle_supported"):raise DomainError("movec 지원 SDK 작업자가 필요합니다.")
            validate_arc(kin["current_tcp"],step.get("via_pose"),goal)
        if expected is not None and not status.get("circle_supported"):
            raise DomainError("TCP 이동 결과를 확인할 수 있는 SDK 작업자가 필요합니다.")
        command=dict(kind=MOTION_KINDS[motion],target=goal,speed=step["speed"],
                     via_pose=step.get("via_pose") if motion=="movec" else None,expected_joints=expected)
        hand_command=None
        if body.step in {1,4,6,12,17}:
            threshold=self.validate_hand(kin,config,stage=body.step,require_contact=body.step==6)
            width=self.idle_width(config) if body.step in {1,17} else preview["opening_mm"]
            position=700 if body.step==6 else self.robot.hand.position_for(width)
            speed=config["profiles"][config["stages"]["1"]["profile"]]["gripper"] if body.step==1 else speed_for(config,body.step,"gripper")
            hand_command=dict(position=position,contact=threshold if body.step==6 else None,speed=speed)
        if versions!=self.versions():raise DomainError("위치를 확인하는 동안 설정이 변경되었습니다. 다시 확인하세요.",412)
        target=dict(kind="hardware-step",config=config,status=status,kin=kin,command=command,hand_command=hand_command,
                    step=body.step,label=f"{body.step}단계 이동"+("·그리퍼 동작" if hand_command else ""))
        identifier=self.ticket(target)
        shown=copy.deepcopy(step);shown.update(number=body.step,target=goal,space="joint" if motion=="movej" else "task")
        shown_steps=[shown]
        if hand_command:
            shown_steps.append(dict(number=body.step,kind="gripper",title="접촉 확인·파지 유지" if body.step==6 else "그리퍼 벌림",
                                    position=position,opening_mm=None if body.step==6 else width,contact_ma=hand_command["contact"],speed=speed))
        return dict(approval_id=identifier,expires_in=30,step=body.step,target_step=number,region=body.region,
                    steps=shown_steps,tcp_name=kin["tcp_name"],current_joints=kin["current_joints"])

    def approve(self,identifier,reader=None,prepare=False,hardware_test=False,hardware_step=False):
        target=self.picks.approvals.pop(identifier,None)
        if target is None or target["deadline"]<=time.monotonic():raise DomainError("승인이 만료되었거나 이미 사용되었습니다.")
        if target.get("kind")!=("hardware-step" if hardware_step else "hardware-test" if hardware_test else "prepare" if prepare else "scenario"):raise DomainError("이 승인 ID는 해당 동작에 사용할 수 없습니다.")
        if target["versions"]!=self.versions():raise DomainError("시나리오·박스·그리퍼 설정이 변경되었습니다. 다시 승인하세요.",412)
        if target.get("reason"):raise DomainError(target["reason"])
        self.require_idle();self.prepared=None
        target["approved"]=True;target["cancelled"]=False
        target["session_id"]=target["status"]["session_id"];target["token"]=target["status"]["motion_token"]
        target["reader"]=reader;self.runtimes[identifier]=target
        move=self.picks.record(identifier,"scenario_running",target["label"] if hardware_step else "대기 상태 준비 중" if prepare or hardware_test else "YOLO 대상 재확인 중",
            mode="prepare" if prepare else "transfer",scenario=True,step=target["step"] if hardware_step else 1 if prepare or hardware_test else 2,label=target.get("label","대기 상태 준비"),
            steps=target.get("steps",[]),last_reached=None,handling="rh_p12",hardware_test=hardware_test or hardware_step)
        self.picks.task=asyncio.create_task(self.run(identifier,1 if prepare or hardware_test else 3))
        return move

    async def check_context(self,target):
        if target["cancelled"]:raise DomainError("작업을 중단했습니다.")
        if target["versions"]!=self.versions():raise DomainError("작업 설정 버전이 변경되었습니다.")
        status=await self.host("status")
        if status.get("session_id")!=target["session_id"] or not status.get("held") or status.get("motion_token")!=target["token"]:
            raise DomainError("SDK 세션 또는 제어권이 변경되었습니다.")
        deadline=time.monotonic()+3
        while not status.get("can_move"):
            if time.monotonic()>deadline or status.get("operation_mode")!="AUTO":raise DomainError("SDK 자동 모드·STANDBY를 확인하세요.")
            await asyncio.sleep(.1);status=await self.host("status")
            if status.get("session_id")!=target["session_id"] or status.get("motion_token")!=target["token"] or not status.get("held"):
                raise DomainError("SDK 세션 또는 제어권이 변경되었습니다.")
        # A stop can arrive while the read-only status request is in flight.
        if target["cancelled"]:raise DomainError("작업을 중단했습니다.")

    async def stop_segment(self,target):
        identifier=target.get("operation_id")
        if identifier and target.get("stop_requested")!=identifier:
            target["stop_requested"]=identifier
            await self.host("operation-stop",OperationStop(session_id=target["session_id"],operation_id=identifier))

    async def operation(self,identifier,kind,speed,target_values=None,position=None,contact=None,j1_only=False,via_pose=None,expected_joints=None):
        target=self.runtimes[identifier];await self.check_context(target)
        command=ScenarioCommand(session_id=target["session_id"],motion_token=target["token"],kind=kind,
            speed=speed["speed"],acceleration=speed["acceleration"],target=target_values,via_pose=via_pose,position=position,contact_ma=contact,
            tcp_name=target["kin"]["tcp_name"],reference_tcp=target["kin"]["reference_tcp"],j1_only=j1_only,timeout_s=600 if kind!="gripper" else 120)
        # Keep the job lock until the dispatch response identifies the segment.
        # Otherwise a simultaneous stop could release it before a move is sent.
        target["dispatching"]=True
        try:
            code,result=await asyncio.to_thread(request_host,"operation",self.robot.settings,command)
            if code==202:target["operation_id"]=result["operation_id"]
        finally:target["dispatching"]=False
        if code!=202:raise DomainError(result.get("detail") or "SDK가 구간을 거절했습니다.")
        operation_id=result["operation_id"]
        if target["cancelled"]:await self.stop_segment(target)
        deadline=time.monotonic()+command.timeout_s+5
        while result.get("state") in {"running","stopping"}:
            if time.monotonic()>deadline:raise RuntimeError("구간 완료 응답 시간 초과")
            await asyncio.sleep(.1)
            result=await self.host("operations/"+operation_id)
        target["operation_id"]=None
        if target["cancelled"]:raise DomainError("작업 중단 · 자동 개방·복귀하지 않습니다.")
        if result.get("state")!="completed":raise RuntimeError(result.get("message") or "구간 결과 미확인")
        token=result.get("next_motion_token")
        if not token or token==target["token"]:raise RuntimeError("다음 구간의 실행 토큰이 없습니다.")
        target["token"]=token
        if expected_joints is not None:
            actual=result.get("current_joints")
            if not isinstance(actual,list) or len(actual)!=6 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in actual) or any(abs(a-b)>.5 for a,b in zip(actual,expected_joints)):
                raise RuntimeError("TCP는 도달했지만 교시한 관절 자세와 일치하지 않습니다. 이 구간을 movej로 설정해 다시 확인하세요.")
        if kind=="gripper":
            feedback=result.get("feedback") or {}
            if feedback.get("error")!=0 or type(feedback.get("position")) is not int:raise RuntimeError("그리퍼 실제 위치 미확인")
            if contact is None and abs(feedback["position"]-position)>2:raise RuntimeError("그리퍼 목표 벌림 미도달")
            if contact is not None and not feedback.get("contact"):raise RuntimeError("파지 접촉 미확인")
            self.picks.record(identifier,"scenario_running",self.picks.moves[identifier]["message"],hand_feedback=feedback,hand_command=position)
            return feedback
        return result

    async def run(self,identifier,start):
        target=self.runtimes[identifier];config=target["config"]
        try:
            if target["kind"]=="hardware-step":
                command=target["command"]
                await self.operation(identifier,command["kind"],command["speed"],command["target"],
                                     via_pose=command["via_pose"],expected_joints=command["expected_joints"])
                hand=target["hand_command"]
                if hand:
                    self.picks.record(identifier,"scenario_running",f"{target['step']}단계 팔 이동 완료 · 그리퍼 응답 대기",step=target["step"])
                    await self.operation(identifier,"gripper",hand["speed"],position=hand["position"],contact=hand["contact"])
                self.picks.record(identifier,"completed",target["label"]+" 완료",step=target["step"],last_reached=target["label"])
                return
            if target["kind"]=="prepare" or (target["kind"]=="hardware-test" and start==1):
                motion=motion_for(config,1)
                await self.operation(identifier,MOTION_KINDS[motion],speed_for(config,1,"ready"),target["prepare_target"],
                                     via_pose=config["stages"]["1"].get("via_pose") if motion=="movec" else None,
                                     expected_joints=config["standby_joints"] if motion!="movej" else None)
                profile=config["profiles"][config["stages"]["1"]["profile"]]["gripper"]
                await self.operation(identifier,"gripper",profile,position=target["idle_position"])
                self.mark_prepared(target)
                self.picks.record(identifier,"completed" if target["kind"]=="prepare" else "scenario_running",
                                  "대기 상태 준비 완료",step=1,last_reached="대기 상태")
                if target["kind"]=="prepare":return
                start=3
            if start==3:
                _,current=await self.inspect()
                if (current["session_id"]!=target["session_id"] or current["tcp_name"]!=target["kin"]["tcp_name"] or current["reference_tcp"]!=target["kin"]["reference_tcp"] or
                    any(abs(a-b)>.5 for a,b in zip(current["current_joints"],config["standby_joints"])) or abs(current["hand"]["position"]-target["idle_position"])>2):
                    raise DomainError("대기 상태 또는 활성 TCP가 변경되었습니다. 대기 준비부터 다시 진행하세요.")
                if target["kind"]!="hardware-test":
                    frame=await asyncio.to_thread(target["reader"],target["camera"].session_id);self.picks.revalidate(target,frame)
            for step in target["steps"][start-1:]:
                number=step["number"]
                self.picks.record(identifier,"scenario_running",f"{number}/17 · {step['title']}",step=number)
                if number==5 and target["kind"]!="hardware-test":
                    frame=await asyncio.to_thread(target["reader"],target["camera"].session_id);obj=self.picks.revalidate(target,frame)
                    estimate=target["plane"].gripper_estimate(obj["xyxy"],tcp=target["pose"][:3])
                    if estimate.get("reason") or target["actual_opening"]+1e-8<estimate["opening_mm"]:raise DomainError("최신 대상에 필요한 벌림을 확인할 수 없습니다.")
                kind=step["kind"]
                if kind=="gripper":
                    pos=target["position"] if number in {4,12} else target["idle_position"] if number==17 else 700
                    feedback=await self.operation(identifier,kind,step["speed"],position=pos,contact=target["threshold"] if number==6 else None)
                    if number==4:
                        target["actual_opening"]=self.robot.hand.width_at(feedback["position"])
                        if target["actual_opening"] is None or target["actual_opening"]+1e-8<target["opening"]:raise DomainError("권장 벌림에 도달하지 못했습니다.")
                else:await self.operation(identifier,kind,step["speed"],step.get("joints") or step["target"],j1_only=step.get("j1_only",False),via_pose=step.get("via_pose"),expected_joints=step.get("expected_joints"))
                self.picks.record(identifier,"scenario_running",f"{number}/17 완료 · {step['title']}",last_reached=step["title"],step=number)
                if number in {6,11} and target["kind"]!="hardware-test":
                    self.picks.record(identifier,"awaiting_grip" if number==6 else "awaiting_open",
                        "파지와 작업 공간을 확인하고 이송을 승인하세요." if number==6 else "박스에서 상품을 놓을 준비가 되면 열기를 승인하세요.")
                    return
            self.mark_prepared(target);self.picks.record(identifier,"completed","17단계 완료 · 대기 자세와 벌림 복원",step=17)
        except asyncio.CancelledError:
            self.picks.record(identifier,"unknown","서버가 중단되었습니다. 자동 재개하지 않습니다.");raise
        except Exception as exc:
            state="cancelled" if target["cancelled"] else "failed" if isinstance(exc,DomainError) else "unknown"
            self.prepared=None;self.picks.record(identifier,state,str(exc) or "작업 결과 미확인")

    def mark_prepared(self,target):
        if target["kind"]=="hardware-test" and (target["config"]!=self.config.value or target["test_box_pose"]!=self.robot.work_points.points["box"]["pose"]):
            self.prepared=None
            return
        self.prepared=dict(session=target["session_id"],versions=target["versions"],tcp=target["kin"]["tcp_name"],reference=target["kin"]["reference_tcp"])

    def action(self,identifier,action):
        target=self.runtimes.get(identifier)
        if not target or self.picks.active_id!=identifier:raise DomainError("진행 중인 시나리오가 아닙니다.")
        state=self.picks.moves[identifier]["state"]
        if action in {"cancel-transfer","stop"}:
            if target["cancelled"]:return self.picks.moves[identifier]
            target["cancelled"]=True;self.prepared=None
            move=self.picks.record(identifier,"scenario_stopping","작업 중단 요청 · 실제 정지 확인 중")
            asyncio.create_task(self.cancel(identifier));return move
        next_step=7 if action=="confirm-grip" and state=="awaiting_grip" else 12 if action=="open-at-box" and state=="awaiting_open" else None
        if next_step is None:raise DomainError("이미 처리했거나 현재 단계에서 사용할 수 없는 동작입니다.")
        move=self.picks.record(identifier,"scenario_running",f"{next_step}/17 계속 진행")
        self.picks.task=asyncio.create_task(self.run(identifier,next_step));return move

    async def cancel(self,identifier):
        target=self.runtimes[identifier]
        try:
            while target.get("dispatching"):await asyncio.sleep(.01)
            if target.get("operation_id"):await self.stop_segment(target)
            elif self.picks.active_id==identifier and (not self.picks.task or self.picks.task.done()):
                self.picks.record(identifier,"cancelled","작업 중단 · 자동 개방·복귀하지 않습니다.")
        except Exception:
            # The running task still owns the lock and monitors the segment.
            # Releasing it here would allow another command before stop is known.
            if not self.picks.task or self.picks.task.done():
                self.picks.record(identifier,"unknown","중단 응답 미확인 · 실제 장치 상태를 확인하세요.")

    def stop_all(self):
        self.stop_generation+=1
        self.prepared=None
        self.picks.approvals.clear()
        identifier=self.picks.active_id
        if identifier is None:
            if self.robot.hand.busy or self.robot.pose_tests.snapshot()["busy"]:
                raise DomainError("기존 수동 조작이 진행 중입니다. 제어기의 정지 버튼으로 중단하세요.")
            return dict(state="idle",message="진행 중인 로봇 작업이 없습니다. 대기 중인 승인을 취소했습니다.")
        if self.picks.moves[identifier].get("scenario"):
            return self.action(identifier,"stop")
        raise DomainError("기존 수동 이송이 진행 중입니다. 제어기의 정지 버튼으로 중단하세요.")


router=APIRouter()

@router.get("/pick-scenario")
async def get_config(request:Request):return request.app.state.robot.scenario.config.value

@router.put("/pick-scenario",dependencies=[Depends(local_json)])
async def put_config(body:ScenarioInput,request:Request):
    engine=request.app.state.robot.scenario;engine.require_idle();result=engine.config.save(body);engine.prepared=None;return result

@router.post("/pick/scenario/preview",dependencies=[Depends(local_json)])
async def virtual_preview(body:SimulationPreview,request:Request):return request.app.state.robot.scenario.virtual_preview(body)

@router.post("/pick/prepare/preview",dependencies=[Depends(local_json)])
async def prepare_preview(request:Request):return await request.app.state.robot.scenario.prepare_preview()

@router.post("/pick/prepare/approve",status_code=202,dependencies=[Depends(local_json)])
async def prepare_approve(body:Identifier,request:Request):return request.app.state.robot.scenario.approve(body.approval_id,prepare=True)

@router.post("/pick/scenario/hardware-test/preview",dependencies=[Depends(local_json)])
async def hardware_test_preview(body:HardwareTest,request:Request):
    return await request.app.state.robot.scenario.preview_hardware_test(body)

@router.post("/pick/scenario/hardware-test/approve",status_code=202,dependencies=[Depends(local_json)])
async def hardware_test_approve(body:Identifier,request:Request):
    return request.app.state.robot.scenario.approve(body.approval_id,hardware_test=True)

@router.post("/pick/scenario/hardware-step/preview",dependencies=[Depends(local_json)])
async def hardware_step_preview(body:HardwareStep,request:Request):
    return await request.app.state.robot.scenario.preview_hardware_step(body)

@router.post("/pick/scenario/hardware-step/approve",status_code=202,dependencies=[Depends(local_json)])
async def hardware_step_approve(body:Identifier,request:Request):
    return request.app.state.robot.scenario.approve(body.approval_id,hardware_step=True)

@router.get("/pick/scenario/status")
async def scenario_status(request:Request):
    robot=request.app.state.robot;p=robot.picks
    move=p.moves.get(p.active_id) or next((v for v in reversed(p.moves.values()) if v.get("scenario")),None)
    issues=[]
    if len(robot.hand.table["points"])<2:issues.append("실측 벌림표를 두 점 이상 등록하세요.")
    if robot.hand.contact["threshold_ma"] is None:issues.append("접촉 전류 임계값을 설정하세요.")
    return dict(prepared=bool(robot.scenario.prepared),move=move,hardware_test_issues=issues)

@router.post("/stop",dependencies=[Depends(local_json)])
async def global_stop(request:Request):
    return request.app.state.robot.scenario.stop_all()

@router.get("/pick/scenario/current")
async def current_joints(request:Request):
    _,kin=await request.app.state.robot.scenario.inspect();return kin
