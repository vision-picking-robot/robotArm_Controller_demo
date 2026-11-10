"""Operator-confirmed camera targets. State is owned by the robot web process."""
import asyncio
import json
import math
import os
import time
from contextlib import closing, suppress
from http.client import HTTPConnection, HTTPException as HTTPError
from typing import Literal
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, FiniteFloat

from .pick_plane import Calibration, CameraFrame, Input, PickPlane, PreviewCalibration
from .robot_access_protocol import local_json
from .robot_access_proxy import request_host
from .robot_gripper import HandCommand
from .robot_poses import HostLineCommand
from .robot_work_points import TRANSFER_POINTS
from .store import DomainError, atomic_json


class Selection(Input):
    session_id: str = Field(min_length=1, max_length=64)
    frame_id: int = Field(ge=1)
    object_index: int = Field(ge=0)
    mode: Literal["pick", "transfer"] = "pick"
    handling: Literal["manual", "rh_p12"] = "manual"
    scenario: bool = False


class Approval(Input):
    approval_id: str = Field(pattern="^[a-f0-9]{32}$")


class GripperPreview(Input):
    calibration: PreviewCalibration
    xyxy: list[FiniteFloat] = Field(min_length=4, max_length=4)


def read_camera(session_id=None, frame_id=None):
    # The Compose robot service does not own or open the Mac camera.
    query = urlencode({k: v for k, v in {"session_id": session_id, "frame_id": frame_id}.items() if v is not None})
    try:
        with closing(HTTPConnection("host.docker.internal", 8000, timeout=2)) as conn:
            conn.request("GET", "/api/detections/pick-frame?" + query)
            response = conn.getresponse()
            frame = json.loads(response.read())
            if response.status != 200:
                raise DomainError(frame.get("detail", "탐지 프레임을 읽지 못했습니다."), response.status)
            return frame
    except (OSError, HTTPError, ValueError) as exc:
        raise DomainError("탐지 서버에서 최신 영상을 받을 수 없습니다.", 503) from exc


def frame_reader(request):
    runtime = getattr(request.app.state, "pipeline", None)
    return runtime.pick_frame if runtime is not None else read_camera


def camera_key(camera):
    return json.dumps([camera.camera_id, camera.width, camera.height])


def center(obj):
    left, top, right, bottom = obj["xyxy"]
    return ((left + right) / 2, (top + bottom) / 2)


ACTIVE_STATES = {"checking", "moving", "awaiting_grip", "to_waypoint", "to_box", "awaiting_release",
                 "preopening", "closing", "awaiting_open", "opening", "scenario_running", "scenario_stopping"}
POINT_LABELS = {"pick": "파지 위치", "waypoint": "박스 이송 경유점", "box": "택배 박스 위치"}


class PickTargets:
    def __init__(self, robot, directory):
        self.robot = robot
        self.path = directory / "pick-planes.json"
        self.last_path = directory / "pick-move.json"
        self.planes = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.approvals = {}
        self.moves = {}
        self.task = None
        self.active_id = None
        self.continuation = None
        if self.last_path.exists():
            last = json.loads(self.last_path.read_text())
            self.moves[last["approval_id"]] = last
            if last["state"] in ACTIVE_STATES:
                self.record(last["approval_id"], "unknown", "서버가 재시작되었습니다. 실제 위치를 확인하세요.")

    @property
    def busy(self):
        return self.active_id is not None

    @property
    def transfer_active(self):
        return self.busy and self.moves[self.active_id]["mode"] == "transfer"

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self.busy:
            self.record(self.active_id, "unknown", "서버가 종료되었습니다. 실제 위치를 확인하세요.")

    def record(self, identifier, state, message, **fields):
        last = {**self.moves.get(identifier, {}), **fields, "approval_id":identifier, "state":state, "message":message}
        atomic_json(self.last_path, last)
        self.moves[identifier] = last
        if state in ACTIVE_STATES:
            self.active_id = identifier
        elif self.active_id == identifier:
            self.active_id = self.continuation = None
        if len(self.moves) > 128:
            del self.moves[next(iter(self.moves))]
        return last

    def calibration(self, camera, region="legacy"):
        entry = self.planes.get(camera_key(camera)+(":"+region if region!="legacy" else ""), {})
        return dict(camera=camera, revision=entry.get("revision"), corners=entry.get("corners", []),
                    clearance_per_side_mm=entry.get("clearance_per_side_mm", 5.0),region=region)

    def region_for(self,camera,pixel):
        matches=[]
        for region in ("floor","shelf"):
            calibration=self.calibration(camera,region)
            if not calibration["revision"]:continue
            plane=PickPlane(Calibration(**calibration))
            if plane.contains(pixel):matches.append((region,calibration,plane))
        if len(matches)!=1:
            raise DomainError("바닥·선반 영역이 겹칩니다. 보정 영역을 조정하세요." if matches else "상품이 교시한 바닥·선반 영역 밖이거나 보정이 누락되었습니다.")
        return matches[0]

    def transfer_route(self):
        return {name: self.robot.work_points.points[name] for name in TRANSFER_POINTS}

    def save(self, body, active):
        if self.busy or self.robot.pose_tests.snapshot()["busy"] or self.robot.hand.busy:
            raise DomainError("로봇 작업이 끝난 뒤 다시 보정하세요.")
        if active != body.camera:
            raise DomainError("카메라 입력이 변경되었습니다. 보정을 다시 시작하세요.")
        current = self.calibration(active,body.region)
        if current["revision"] != body.revision:
            raise DomainError("다른 화면에서 작업 평면을 수정했습니다. 보정을 다시 시작하세요.")
        if "clearance_per_side_mm" not in body.model_fields_set:
            body = body.model_copy(update={"clearance_per_side_mm": current["clearance_per_side_mm"]})
        plane=PickPlane(body)
        if body.region=="floor" and abs(plane.normal[2])<math.cos(math.radians(3)):
            raise DomainError("바닥 파지 평면은 BASE XY와 평행해야 합니다(허용 3°).",422)
        if body.region=="shelf" and abs(plane.normal[2])>math.sin(math.radians(3)):
            raise DomainError("선반 파지 평면은 수평 접근할 수 있는 수직 평면이어야 합니다(허용 3°).",422)
        entry = {**body.model_dump(), "revision": uuid4().hex}
        updated = {**self.planes, camera_key(active)+(":"+body.region if body.region!="legacy" else ""): entry}
        atomic_json(self.path, updated)
        self.planes = updated
        return self.calibration(active,body.region)

    async def preview(self, selection, reader):
        if os.getenv('YOLO_DEMO_CONTROLLER_URL') and selection.mode == 'pick' and selection.handling == 'manual' and not selection.scenario:
            from .picking_integration import preview_target
            return await preview_target(self, selection, reader)
        if selection.scenario or selection.handling=="rh_p12":
            if selection.mode!="transfer" or selection.handling!="rh_p12":raise DomainError("17단계 시나리오는 RH-P12 박스 이송에서 사용하세요.",422)
            return await self.robot.scenario.preview_real(selection,reader)
        packet = await asyncio.to_thread(reader, selection.session_id, selection.frame_id)
        if packet.get('simulation_only'):
            raise DomainError('합성 탐지 프레임은 실물 로봇 명령에 사용할 수 없습니다.', 412)
        camera = CameraFrame.from_packet(packet)
        calibration = self.calibration(camera)
        if not calibration["revision"]:
            raise DomainError("먼저 이 카메라와 해상도의 작업 평면을 보정하세요.")
        if not 0 <= selection.object_index < len(packet["objects"]):
            raise DomainError("선택한 객체가 이 프레임에 없습니다.")
        obj = packet["objects"][selection.object_index]
        plane = PickPlane(Calibration(**calibration))
        pose = plane.target(center(obj))
        route = self.transfer_route() if selection.mode == "transfer" else None
        missing = [POINT_LABELS[name] for name, point in (route or {}).items() if point["pose"] is None]
        automatic = selection.handling == "rh_p12"
        if automatic and not route:
            raise DomainError("RH-P12 사용은 택배 박스 이송에서 선택하세요.", 422)
        estimate = plane.gripper_estimate(obj["xyxy"])
        hand_revision = self.robot.hand.table["revision"] if automatic else None
        position = hand_error = None
        if automatic:
            if estimate["reason"]:
                hand_error = estimate["reason"]
            else:
                try:
                    position = self.robot.hand.position_for(estimate["opening_mm"])
                except DomainError as exc:
                    hand_error = str(exc)
        sdk = None
        reason = "파지 위치 이동에는 Mac SDK 제어 모드가 필요합니다."
        if self.robot.settings.access_backend == "mac-sdk":
            try:
                code, host = await asyncio.to_thread(request_host, "status", self.robot.settings)
                reason = host.get("detail") or "Mac SDK 상태를 확인할 수 없습니다."
                if code == 200:
                    if not host.get("line_supported"):
                        reason = "직선 이동을 지원하는 Mac SDK 작업자를 시작하세요."
                    elif route and not host.get("transfer_supported"):
                        reason = "박스 이송을 지원하는 Mac SDK 서버를 시작하세요."
                    elif automatic and not host.get("gripper_supported"):
                        reason = "RH-P12 지원 SDK 작업자를 시작하세요."
                    elif host.get("can_move") and host.get("motion_token"):
                        sdk = HostLineCommand(pose=pose, session_id=host["session_id"], motion_token=host["motion_token"])
                        reason = None
                    else:
                        reason = host.get("motion_reason") or reason
            except HTTPException as exc:
                reason = str(exc.detail)
        if hand_error:
            sdk, reason = None, hand_error
        if missing:
            reason = "경유 지점 설정에서 " + ", ".join(missing) + " 좌표를 저장하세요."
            sdk = None
        current = time.monotonic()
        self.approvals = {k: v for k, v in self.approvals.items() if v["deadline"] > current}
        if len(self.approvals) >= 128:
            raise DomainError("대기 중인 승인이 많습니다. 잠시 뒤 다시 선택하세요.")
        identifier = uuid4().hex
        self.approvals[identifier] = dict(deadline=current + 30, camera=camera, revision=calibration["revision"],
                                          model=packet["model_version"], class_id=obj["class_id"], plane=plane,
                                          pose=pose, command=sdk, reason=reason, mode=selection.mode, route=route,
                                          label=obj["label"], handling=selection.handling, position=position,
                                          hand_revision=hand_revision, opening=estimate.get("opening_mm"))
        return dict(approval_id=identifier, expires_in=30, pose=pose, label=obj["label"],
                    confidence=obj["confidence"], motion_reason=reason, gripper=estimate, handling=selection.handling,
                    hand_position=position, hand_error=hand_error,
                    mode=selection.mode, route=route)

    def approve(self, identifier, reader):
        if self.approvals.get(identifier, {}).get('kind') == 'demo':
            from .picking_integration import approve_target
            return approve_target(self, identifier, reader)
        if self.approvals.get(identifier,{}).get("kind") in {"scenario","prepare"}:
            return self.robot.scenario.approve(identifier,reader)
        # Single event loop, with no await between consuming and reserving the move.
        target = self.approvals.pop(identifier, None)
        if target is None or time.monotonic() >= target["deadline"]:
            raise DomainError("승인이 만료되었거나 이미 사용되었습니다. 다시 선택하세요.")
        if target["route"] is not None and target["route"] != self.transfer_route():
            raise DomainError("경유점 또는 박스 좌표가 변경되었습니다. 새 경로를 확인하고 다시 승인하세요.", 412)
        if target["handling"] == "rh_p12" and target["hand_revision"] != self.robot.hand.table["revision"]:
            raise DomainError("벌림표가 변경되었습니다. 새 명령값을 확인하고 다시 승인하세요.", 412)
        if target["command"] is None:
            raise DomainError(target["reason"])
        if self.busy or self.robot.hand.busy or self.robot.pose_tests.snapshot()["busy"]:
            raise DomainError("이전 로봇 이동이 끝난 뒤 다시 선택하세요.")
        state = self.record(identifier, "checking", "현재 영상에서 선택한 대상을 확인하고 있습니다.",
                            mode=target["mode"], route=target["route"], pick_pose=target["pose"],
                            label=target["label"], last_reached=None, handling=target["handling"])
        self.task = asyncio.create_task(self.run(identifier, target, reader))
        return state

    def revalidate(self, target, frame):
        if frame.get('simulation_only'):
            raise DomainError('합성 탐지 프레임은 실물 로봇 명령에 사용할 수 없습니다.', 412)
        camera = CameraFrame.from_packet(frame)
        if (target["camera"] != camera or target["model"] != frame["model_version"] or
                self.calibration(camera,target.get("region","legacy"))["revision"] != target["revision"]):
            raise DomainError("카메라·모델 또는 작업 평면이 변경되었습니다. 다시 선택하세요.")
        plane = target["plane"]
        matches = []
        for obj in frame["objects"]:
            if obj["class_id"] != target["class_id"]:
                continue
            pixel = center(obj)
            xyz = plane.project(pixel)
            if xyz is not None and math.dist(xyz, target["pose"][:3]) <= 10.0 + 1e-8:
                if target.get("region") and self.region_for(camera,pixel)[0]!=target["region"]:
                    raise DomainError("상품의 파지 영역이 변경되었습니다.")
                matches.append(obj)
        if not matches:
            raise DomainError("선택한 중심에서 10 mm 이내에 같은 클래스의 객체가 없습니다.")
        if len(matches) != 1:
            raise DomainError("10 mm 이내에 같은 클래스의 객체가 여러 개입니다. 다시 선택하세요.")
        if not plane.contains(center(matches[0])):
            raise DomainError("현재 객체가 작업 영역 밖에 있습니다.")
        if not target.get("approved") and time.monotonic() >= target["deadline"]:
            raise DomainError("승인이 만료되었습니다. 다시 선택하세요.")

        return matches[0]

    async def send_operation(self, identifier, operation, command):
        """Every call dispatches once. An ambiguous response ends the whole job."""
        try:
            code, data = await asyncio.to_thread(request_host, operation, self.robot.settings, command)
            if code == 200 and data.get("state") == "completed":
                return data
            if 400 <= code < 500 or (code == 200 and data.get("state") == "failed"):
                self.record(identifier, "failed", data.get("message") or data.get("detail") or "SDK가 이동을 거절했습니다.")
            else:
                self.record(identifier, "unknown", data.get("message") or "명령 결과 미확인 · 실제 상태를 확인하세요.")
        except (Exception, asyncio.CancelledError):
            self.record(identifier, "unknown", "이동 결과 미확인 · 실제 위치를 확인하세요.")
        return None

    def set_continuation(self, identifier, command, response, reached):
        token = response.get("next_motion_token")
        if not isinstance(token, str) or len(token) != 32 or token == command.motion_token:
            self.record(identifier, "failed", "SDK에서 다음 이동의 제어권을 확인할 수 없습니다.", last_reached=reached)
            return False
        self.continuation = (command.session_id, token)
        return True

    async def command_hand(self, identifier, ticket, action, reached, position=None):
        command = HandCommand(**ticket, action=action, position=position)
        result = await self.send_operation(identifier, "gripper", command)
        if result is None or not self.set_continuation(identifier, command, result, reached):
            return None
        feedback = result.get("feedback") or {}
        actual = feedback.get("position")
        if (type(actual) is not int or not 0 <= actual <= 700 or feedback.get("error") != 0
                or feedback.get("state") != "completed" or (action == "open" and actual > 2)):
            self.record(identifier, "unknown", "그리퍼의 실제 위치를 확인할 수 없습니다.", last_reached=reached)
            return None
        self.record(identifier, self.moves[identifier]["state"], self.moves[identifier]["message"],
                    hand_feedback=feedback, hand_command=command.target)
        return actual

    async def run(self, identifier, target, reader):
        try:
            frame = await asyncio.to_thread(reader, target["camera"].session_id)
            self.revalidate(target, frame)
            command = target["command"]
            if target["handling"] == "rh_p12":
                self.record(identifier, "preopening", "RH-P12를 권장 폭으로 벌리는 중입니다.")
                ticket = dict(session_id=command.session_id, motion_token=command.motion_token)
                actual = await self.command_hand(identifier, ticket, "position", None, target["position"])
                if actual is None:
                    return
                width = self.robot.hand.width_at(actual)
                if width is None or width + 1e-8 < target["opening"]:
                    raise DomainError("실측 벌림이 승인한 폭에 도달하지 않았습니다.")
                command = HostLineCommand(**await self.next_ticket(), pose=target["pose"])
                frame = await asyncio.to_thread(reader, target["camera"].session_id)
                obj = self.revalidate(target, frame)
                fresh = target["plane"].gripper_estimate(obj["xyxy"], tcp=target["pose"][:3])
                if fresh["reason"] or width + 1e-8 < fresh["opening_mm"]:
                    raise DomainError("현재 탐지 상자에 필요한 벌림을 확인할 수 없습니다. 대상을 다시 선택하세요.")
        except DomainError as exc:
            self.record(identifier, "failed", str(exc))
            return
        except (Exception, asyncio.CancelledError):
            self.record(identifier, "failed", "최신 영상을 확인할 수 없습니다.")
            return
        self.record(identifier, "moving", "파지 위치로 이동 중입니다.")
        result = await self.send_operation(identifier, "line", command)
        if result is None:
            return
        if target["mode"] == "pick":
            self.record(identifier, "completed", result["message"], last_reached="pick")
        elif self.set_continuation(identifier, command, result, "pick"):
            if target["handling"] == "rh_p12":
                self.record(identifier, "closing", "파지 위치 도착 · 그리퍼를 닫는 중입니다.", last_reached="pick")
                actual = await self.hand_after_stop(identifier, "close", "pick")
                if actual is None:
                    return
            self.record(identifier, "awaiting_grip", "파지 위치에 도착했습니다. 파지 상태와 이동 경로를 확인하세요.",
                        last_reached="pick")

    async def hand_after_stop(self, identifier, action, reached):
        try:
            ticket = await self.next_ticket()
        except (Exception, asyncio.CancelledError) as exc:
            self.record(identifier, "failed", str(exc) or "그리퍼 제어권을 확인할 수 없습니다.")
            return None
        return await self.command_hand(identifier, ticket, action, reached)

    async def next_ticket(self):
        session, token = self.continuation
        # The synchronous line may finish just before the next STANDBY sample.
        # Poll status only; never re-send a motion or adopt a different grant.
        deadline = time.monotonic() + 3
        while True:
            code, status = await asyncio.to_thread(request_host, "status", self.robot.settings)
            if code != 200 or not status.get("held") or status.get("session_id") != session or status.get("motion_token") != token:
                raise DomainError("SDK 연결·세션 또는 제어권이 변경되어 이송을 중단했습니다.")
            if status.get("can_move") and status.get("robot_state") == "STANDBY" and status.get("operation_mode") == "AUTO":
                return dict(session_id=session, motion_token=token)
            if (status.get("robot_state") not in (None, "MOVING") or status.get("operation_mode") != "AUTO"
                    or time.monotonic() >= deadline):
                raise DomainError(status.get("motion_reason") or "SDK 자동 모드와 STANDBY를 확인하세요.")
            await asyncio.sleep(.1)

    async def transport(self, identifier):
        route = self.moves[identifier]["route"]
        for name in TRANSFER_POINTS:
            try:
                command = HostLineCommand(pose=route[name]["pose"], **await self.next_ticket())
            except DomainError as exc:
                self.record(identifier, "failed", str(exc))
                return
            except (Exception, asyncio.CancelledError):
                self.record(identifier, "failed", "다음 구간의 SDK 상태를 확인할 수 없습니다.")
                return
            result = await self.send_operation(identifier, "line", command)
            if result is None:
                return
            if name == "waypoint":
                if not self.set_continuation(identifier, command, result, name):
                    return
                self.record(identifier, "to_box", "경유점 도착 · 택배 박스로 이송 중입니다.", last_reached=name)
            elif self.moves[identifier]["handling"] == "rh_p12":
                if self.set_continuation(identifier, command, result, name):
                    self.record(identifier, "awaiting_open", "박스 도착 · 물체를 놓을 준비가 되면 열기를 승인하세요.", last_reached=name)
            else:
                self.record(identifier, "awaiting_release", "박스에 도착했습니다. 물체를 놓은 뒤 완료를 확인하세요.",
                            last_reached=name)

    async def open_at_box(self, identifier):
        if await self.hand_after_stop(identifier, "open", "box") is not None:
            self.record(identifier, "awaiting_release", "그리퍼 열림 확인 · 물체를 놓았는지 확인하세요.")

    def operator_action(self, identifier, action):
        if self.moves.get(identifier,{}).get("scenario"):
            return self.robot.scenario.action(identifier,action)
        if self.active_id != identifier:
            raise DomainError("이 작업은 종료되었거나 현재 작업이 아닙니다.")
        state = self.moves[identifier]["state"]
        if state == "awaiting_grip":
            if action == "cancel-transfer":
                return self.record(identifier, "cancelled", "파지 위치에서 이송을 취소했습니다.")
            if action == "confirm-grip":
                move = self.record(identifier, "to_waypoint", "파지 확인 · 박스 이송 경유점으로 이동 중입니다.")
                self.task = asyncio.create_task(self.transport(identifier))
                return move
        if state == "awaiting_open" and action == "open-at-box":
            move = self.record(identifier, "opening", "박스에서 그리퍼를 여는 중입니다.")
            self.task = asyncio.create_task(self.open_at_box(identifier))
            return move
        if state == "awaiting_release" and action == "confirm-release":
            return self.record(identifier, "completed", "물체 놓기 확인 · 택배 박스 이송을 완료했습니다.")
        raise DomainError("이미 처리했거나 현재 단계에서 사용할 수 없는 동작입니다.")


router = APIRouter(prefix="/pick")


@router.get("/calibration")
async def calibration(request: Request,region:Literal["legacy","floor","shelf"]="legacy"):
    packet = await asyncio.to_thread(frame_reader(request))
    return request.app.state.robot.picks.calibration(CameraFrame.from_packet(packet),region)


@router.put("/calibration", dependencies=[Depends(local_json)])
async def save_calibration(body: Calibration, request: Request):
    packet = await asyncio.to_thread(frame_reader(request))
    return request.app.state.robot.picks.save(body, CameraFrame.from_packet(packet))


@router.post("/preview", dependencies=[Depends(local_json)])
async def preview(body: Selection, request: Request):
    return await request.app.state.robot.picks.preview(body, frame_reader(request))


@router.post("/gripper-preview", dependencies=[Depends(local_json)])
async def gripper_preview(body: GripperPreview):
    # The draft is for visualization only. It never issues an approval or contacts the SDK.
    return PickPlane(body.calibration).gripper_estimate(body.xyxy)


@router.post("/cancel", dependencies=[Depends(local_json)])
async def cancel(body: Approval, request: Request):
    request.app.state.robot.picks.approvals.pop(body.approval_id, None)
    return {"ok": True}


@router.post("/approve", status_code=202, dependencies=[Depends(local_json)])
async def approve(body: Approval, request: Request):
    return request.app.state.robot.picks.approve(body.approval_id, frame_reader(request))


@router.get("/active")
async def active_pick(request: Request):
    picks = request.app.state.robot.picks
    return {"move": picks.moves.get(picks.active_id)}


@router.post("/moves/{identifier}/{action}", dependencies=[Depends(local_json)])
async def operator_action(identifier: str, action: Literal["confirm-grip", "confirm-release", "cancel-transfer", "open-at-box", "stop"], request: Request):
    return request.app.state.robot.picks.operator_action(identifier, action)


@router.get("/moves/{identifier}")
async def move_status(identifier: str, request: Request):
    move = request.app.state.robot.picks.moves.get(identifier)
    if move is None:
        raise DomainError("이 이동의 기록을 찾을 수 없습니다. 실제 위치를 확인하세요.", 404)
    return move
