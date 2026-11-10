"""Receive E0509 telemetry through rosbridge on the external ROS 2 controller."""
import asyncio
import ipaddress
import json
import math
import os
import re
import time
from collections import deque
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .robot_access_protocol import ModeTicket, ReclaimTicket, ServoTicket, local_json
from .robot_access_proxy import forward_host, request_host
from .robot_poses import PoseExecution, PoseInput, PoseTests
from .robot_gripper import Gripper, router as hand_router
from .robot_pick import PickTargets, router as pick_router
from .robot_work_points import TRANSFER_POINTS, FloorReference, FloorReferenceInput, ParcelBox, ParcelBoxInput, PointName, WorkPointInput, WorkPoints
from .robot_scenario import Scenario, router as scenario_router
from .store import DomainError

RECLAIM_SERVICE = "system/reclaim_control"
TEACH_STATUS = "system/direct_teach_status"
TEACH_SET = "system/set_direct_teach"


class TeachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool

@dataclass(frozen=True)
class RobotSettings:
    ip: str = "110.120.1.63"
    port: int = 12345
    namespace: str = "dsr01"
    ros_pc_ip: str = ""
    rosbridge_port: int = 9090
    stale_seconds: float = 3.0

    access_backend: str = "ros"

    def __post_init__(self):
        if self.access_backend not in {"ros", "mac-sdk"}:
            raise ValueError("ROBOT_ACCESS_BACKEND must be ros or mac-sdk")
        ipaddress.ip_address(self.ip)
        if not 1 <= self.port <= 65535:
            raise ValueError("ROBOT_PORT must be between 1 and 65535")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)*", self.namespace):
            raise ValueError("ROBOT_NAMESPACE must be a ROS namespace such as dsr01")
        if not math.isfinite(self.stale_seconds) or self.stale_seconds <= 0:
            raise ValueError("ROBOT_STALE_SECONDS must be finite and positive")
        if not 1 <= self.rosbridge_port <= 65535:
            raise ValueError("ROBOT_ROSBRIDGE_PORT must be between 1 and 65535")
        if self.ros_pc_ip:
            try:
                address = ipaddress.ip_address(self.ros_pc_ip)
            except ValueError:
                raise ValueError("ROBOT_ROS_PC_IP must be an IP address") from None
            scope = getattr(address, "scope_id", None)
            if scope and not re.fullmatch(r"[A-Za-z0-9_.-]+", scope):
                raise ValueError("ROBOT_ROS_PC_IP has an invalid interface scope")

    @classmethod
    def from_env(cls):
        return cls(ip=os.getenv("ROBOT_IP", cls.ip),
                   port=int(os.getenv("ROBOT_PORT", "12345")),
                   namespace=os.getenv("ROBOT_NAMESPACE", "dsr01").strip("/"),
                   ros_pc_ip=os.getenv("ROBOT_ROS_PC_IP", "").strip(),
                   rosbridge_port=int(os.getenv("ROBOT_ROSBRIDGE_PORT", "9090")),
                   stale_seconds=float(os.getenv("ROBOT_STALE_SECONDS", "3")),
                   access_backend=os.getenv("ROBOT_ACCESS_BACKEND", "ros"))


class RobotMonitor:
    def __init__(self, settings, state_dir=None):
        self.settings = settings
        self.bridge_host = settings.ros_pc_ip
        if (os.getenv("ROBOT_BRIDGE_NETWORK") == "compose" and self.bridge_host
                and ipaddress.ip_address(self.bridge_host).is_loopback):
            self.bridge_host = "rosbridge"
        prefix = "/" + settings.namespace
        self.topics = [
            {"name": prefix + "/joint_states", "type": "sensor_msgs/msg/JointState", "kind": "joints"},
            {"name": prefix + "/error", "type": "dsr_msgs2/msg/RobotError", "kind": "event"},
            {"name": prefix + "/robot_disconnection", "type": "dsr_msgs2/msg/RobotDisconnection", "kind": "event"},
            {"name": prefix + "/monitor/joint_states", "type": "sensor_msgs/msg/JointState", "kind": "monitor_joints"},
        ]
        self.samples = {}
        self.arrivals = {topic["name"]: deque(maxlen=512) for topic in self.topics}
        self.connected = False
        self.disconnected_event = False
        self.error = None
        self.joint_publishers = None
        self.observer_subscription_failed = False
        self.task = None
        self.socket = None
        self.pending = {}
        self.reclaim_available = False
        self.reclaim_pending = False
        self.teach_mode = "unavailable"
        self.teach_message = ""
        self.teach_pending = False
        self.teach_lock = asyncio.Lock()
        self.pose_tests = PoseTests(self, state_dir) if state_dir is not None else None
        self.work_points = WorkPoints(self.pose_tests.path.with_name("work-points.json")) if self.pose_tests else None
        self.floor = FloorReference(self.pose_tests.path.with_name("floor-reference.json")) if self.pose_tests else None
        self.parcel_box = ParcelBox(self.pose_tests.path.with_name("parcel-box.json")) if self.pose_tests else None
        self.hand = Gripper(self.pose_tests.path.parent) if self.pose_tests else None
        self.picks = PickTargets(self, self.pose_tests.path.parent) if self.pose_tests else None
        self.scenario = Scenario(self,self.pose_tests.path.parent) if self.pose_tests else None

    def start(self):
        if self.settings.ros_pc_ip and self.task is None:
            self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.picks:
            await self.picks.stop()
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        if self.pose_tests and self.pose_tests.task:
            await self.pose_tests.task

    async def _run(self):
        host = self.bridge_host
        if ":" in host:
            host = f"[{host}]"
        endpoint = f"ws://{host}:{self.settings.rosbridge_port}"
        delay = 1
        while True:
            graph_task = None
            try:
                async with connect(endpoint, open_timeout=5, close_timeout=2,
                                   ping_interval=5, ping_timeout=5, max_size=262144, max_queue=4,
                                   proxy=None) as socket:
                    self.samples.clear()
                    for arrivals in self.arrivals.values():
                        arrivals.clear()
                    self.disconnected_event = False
                    self.error = None
                    self.observer_subscription_failed = False
                    for topic in self.topics:
                        await socket.send(json.dumps({"op": "subscribe", "id": topic["name"],
                            "topic": topic["name"], "type": topic["type"],
                            "throttle_rate": 0 if topic["kind"] == "event" else 100,
                            "queue_length": 1}))
                    self.connected = True
                    self.socket = socket
                    graph_task = asyncio.create_task(self._watch_joint_publishers(socket))
                    delay = 1
                    async for raw in socket:
                        self.receive(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep endpoint or remote payload details out of the UI and logs.
                self.error = f"rosbridge 연결 대기 · {endpoint}에 다시 연결하고 있습니다."
            finally:
                self.connected = False
                self.socket = None
                self.joint_publishers = None
                self.reclaim_available = False
                self.teach_mode, self.teach_message = "unavailable", ""
                if graph_task is not None:
                    graph_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await graph_task
                for _, future in self.pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("rosbridge disconnected"))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)

    async def _watch_joint_publishers(self, socket):
        # A subscription creates a ROS graph entry even when the driver is absent.
        # Only the publisher list can distinguish that case from a silent publisher.
        topic = self.topics[0]["name"]
        while socket is self.socket:
            try:
                result = await self.call_service(socket, "/rosapi/publishers", {"topic": topic}, timeout=3)
                nodes = result.get("publishers")
                self.joint_publishers = nodes if isinstance(nodes, list) and all(isinstance(n, str) for n in nodes) else None
                if self.disconnected_event and self.joint_publishers:
                    await self._check_driver_reconnected(socket)
            except (TimeoutError, RuntimeError, OSError, ConnectionClosed):
                self.joint_publishers = None
            if socket is self.socket and self.settings.access_backend == "ros":
                try:
                    info = await self.call_service(socket, "/rosapi/service_type",
                        {"service": f"/{self.settings.namespace}/{RECLAIM_SERVICE}"}, timeout=3)
                    self.reclaim_available = info.get("type") in ("std_srvs/srv/Trigger", "std_srvs/Trigger")
                except (TimeoutError, RuntimeError, OSError, ConnectionClosed):
                    self.reclaim_available = False
                if not self.teach_pending:
                    async with self.teach_lock:
                        await self.read_teach_mode(socket)
            await asyncio.sleep(5)

    async def read_teach_mode(self, socket):
        mode, message = "unavailable", "직접 교시 서비스 대기 · 실장비 드라이버 연결을 확인하세요."
        if self.reclaim_available and socket is self.socket and self.connected:
            try:
                result = await self.call_service(socket, TEACH_STATUS, {}, timeout=3)
                value = result.get("message")
                if result.get("success") is True and value in ("auto", "manual", "unconfirmed"):
                    mode, message = value, ""
                elif isinstance(value, str):
                    message = value
            except (TimeoutError, RuntimeError, OSError, ConnectionClosed):
                pass
        if socket is self.socket:
            self.teach_mode, self.teach_message = mode, message

    async def current_work_pose(self):
        if self.settings.access_backend=="mac-sdk" and self.scenario:
            try:
                _,snapshot=await self.scenario.inspect()
                pose=snapshot.get("current_tcp")
                if isinstance(pose,list) and len(pose)==6 and all(type(v) in (int,float) and math.isfinite(v) for v in pose):return {"pose":pose}
            except (DomainError,HTTPException):pass
        socket = self.socket
        if not self.connected or socket is None or self.snapshot()["topics"][3]["state"] != "live":
            raise DomainError("현재 위치를 읽으려면 제어기와 읽기 전용 관절 모니터가 연결되어야 합니다.", 503)
        try:
            result = await self.call_service(socket, "monitor/get_current_pose", {"space_type": 1}, timeout=4)
        except (TimeoutError, RuntimeError, OSError, ConnectionClosed):
            result = {}
        pose = result.get("pos")
        if (socket is not self.socket or not self.connected or result.get("success") is not True
                or not isinstance(pose, list) or len(pose) != 6
                or not all(type(v) in (float, int) and math.isfinite(v) for v in pose)):
            raise DomainError("제어기에서 현재 작업 좌표를 읽지 못했습니다. 좌표는 변경되지 않았습니다.", 503)
        return {"pose": pose}

    async def set_direct_teach(self, enabled):
        socket = self.socket
        if not self.connected or not socket or self.teach_mode == "unavailable":
            raise DomainError("직접 교시 서비스와 드라이버 제어권을 확인하세요.", 503)
        if self.teach_pending or self.reclaim_pending or (self.pose_tests and self.pose_tests.snapshot()["busy"]):
            raise DomainError("진행 중인 로봇 명령이 끝난 뒤 전환하세요.", 409)
        self.teach_pending = True
        try:
            async with self.teach_lock:
                result = await self.call_service(socket, TEACH_SET, {"data": enabled}, timeout=6)
                await self.read_teach_mode(socket)
                if result.get("success") is not True:
                    raise DomainError(str(result.get("message") or "운전 모드를 변경하지 못했습니다."), 409)
                if self.teach_mode != ("manual" if enabled else "auto") or self.socket is not socket:
                    raise DomainError("변경 후 운전 모드를 확인할 수 없습니다.", 503)
                return {"mode": self.teach_mode, "message": result["message"]}
        except (TimeoutError, RuntimeError, OSError, ConnectionClosed):
            self.teach_mode, self.teach_message = "unavailable", "운전 모드를 다시 확인하고 있습니다."
            raise DomainError("드라이버 응답이 없어 전환 결과를 확인할 수 없습니다.", 503) from None
        finally:
            self.teach_pending = False

    async def reclaim_control(self):
        if self.teach_pending:
            raise DomainError("직접 교시 모드 전환이 끝난 뒤 회수하세요.", 409)
        if self.reclaim_pending:
            raise DomainError("이미 제어권 회수를 요청했습니다. 응답을 기다려 주세요.", 409)
        if not self.connected or not self.socket or not self.reclaim_available:
            raise DomainError("제어권 회수 서비스를 찾을 수 없습니다. 실장비 드라이버 연결을 확인하세요.", 503)
        self.reclaim_pending = True
        try:
            result = await self.call_service(self.socket, RECLAIM_SERVICE, {}, timeout=15)
            if type(result.get("success")) is not bool or not isinstance(result.get("message"), str):
                raise RuntimeError("invalid reclaim response")
            return {"success": result["success"], "message": result["message"]}
        except (TimeoutError, RuntimeError, OSError, ConnectionClosed):
            raise DomainError("드라이버 응답을 받지 못해 제어권 회수 결과를 확인할 수 없습니다.", 503) from None
        finally:
            self.reclaim_pending = False

    async def _check_driver_reconnected(self, socket):
        lost = self.samples.get(self.topics[2]["name"])
        joints = self.samples.get(self.topics[0]["name"])
        if (not lost or not joints or joints["time"] <= lost["time"]
                or time.monotonic() - joints["time"] > self.settings.stale_seconds
                or len(joints["message"]["name"]) != 6
                or set(joints["message"]["name"]) != {f"joint_{i}" for i in range(1, 7)}):
            return
        state = await self.call_service(socket, "system/get_robot_state", {}, timeout=3)
        if (socket is self.socket and state.get("success") is True
                and self.samples.get(self.topics[2]["name"]) is lost):
            self.disconnected_event = False

    async def call_service(self, socket, suffix, args, timeout):
        if socket is not self.socket or not self.connected:
            raise ConnectionError("rosbridge disconnected")
        identifier = uuid4().hex
        service = suffix if suffix.startswith("/") else f"/{self.settings.namespace}/{suffix}"
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = (service, future)
        try:
            async with asyncio.timeout(timeout):
                await socket.send(json.dumps({"op": "call_service", "id": identifier, "service": service,
                                              "args": args, "timeout": timeout - 1}))
                return await future
        finally:
            self.pending.pop(identifier, None)
            if not future.cancelled() and future.done():
                future.exception()

    def receive(self, raw):
        try:
            packet = json.loads(raw)
        except (ValueError, TypeError, UnicodeError):
            return
        if not isinstance(packet, dict):
            return
        identifier = packet.get("id")
        if isinstance(identifier, str) and identifier in self.pending:
            service, future = self.pending[identifier]
            if not future.done():
                if packet.get("op") == "service_response" and packet.get("service") == service:
                    if packet.get("result") is True and isinstance(packet.get("values"), dict):
                        future.set_result(packet["values"])
                    else:
                        future.set_exception(RuntimeError("ROS service rejected request"))
                elif packet.get("op") == "status" and packet.get("level") == "error":
                    future.set_exception(RuntimeError("rosbridge service error"))
            return
        if packet.get("op") == "status" and packet.get("level") == "error":
            if identifier == self.topics[3]["name"]:
                self.observer_subscription_failed = True
                return
            self.error = "토픽 구독 오류입니다. 네임스페이스와 ROS 메시지 패키지를 확인하세요."
            return
        topic = packet.get("topic")
        if not isinstance(topic, str) or topic not in self.arrivals or packet.get("op") != "publish":
            return
        message = packet.get("msg")
        if not isinstance(message, dict):
            return
        # Reject non-finite values before exposing the message as JSON through FastAPI.
        try:
            json.dumps(message, allow_nan=False)
        except (ValueError, TypeError):
            return
        if topic in (self.topics[0]["name"], self.topics[3]["name"]):
            names, positions = message.get("name"), message.get("position")
            if (not isinstance(names, list) or not isinstance(positions, list) or not names
                    or len(names) != len(positions) or len(names) > 64
                    or any(not isinstance(name, str) for name in names)
                    or any(type(value) not in (int, float) for value in positions)):
                return
        now = time.monotonic()
        self.samples[topic] = {"message": message, "time": now,
                               "received_at": datetime.now(timezone.utc).isoformat()}
        self.arrivals[topic].append(now)
        if topic == self.topics[2]["name"]:
            self.disconnected_event = True

    def snapshot(self):
        now = time.monotonic()
        topics = []
        for topic in self.topics:
            sample = self.samples.get(topic["name"])
            age = now - sample["time"] if sample else None
            recent = [stamp for stamp in self.arrivals[topic["name"]] if now - stamp <= 5]
            state = "waiting"
            if self.connected:
                state = "listening" if topic["kind"] == "event" else "waiting"
                if sample:
                    state = "received" if topic["kind"] == "event" else (
                        "live" if age <= self.settings.stale_seconds else "stale")
                if topic["kind"] == "joints" and state != "live" and self.joint_publishers == []:
                    state = "no_publisher"
                if topic["kind"] == "monitor_joints" and self.observer_subscription_failed:
                    state = "unavailable"
            rate = None
            if state == "live" and len(recent) > 1 and recent[-1] > recent[0]:
                rate = round((len(recent) - 1) / (recent[-1] - recent[0]), 1)
            topics.append({**topic, "state": state, "hz": rate,
                           "age_seconds": round(age, 2) if age is not None else None,
                           "received_at": sample["received_at"] if sample else None,
                           "message": sample["message"] if sample else None})
        state = "unconfigured"
        driver, observer = topics[0], topics[3]
        displayed = driver
        observer_is_newer = observer["received_at"] and (
            not driver["received_at"] or observer["received_at"] > driver["received_at"])
        if self.settings.ros_pc_ip:
            state = "offline"
            if self.connected:
                state = driver["state"]
                if self.disconnected_event:
                    state = "robot_disconnected"
                if state != "live" and observer["state"] == "live":
                    state = "monitoring"
                    displayed = observer
                elif state != "live" and observer_is_newer:
                    displayed = observer
                    state = "stale"
            elif observer_is_newer:
                displayed = observer
        return {"config": {**asdict(self.settings), "rosbridge_host": self.bridge_host, "model": "e0509", "transport": "LAN"},
                "state": state, "bridge_connected": self.connected,
                "reclaim_available": self.connected and self.reclaim_available,
                "reclaim_pending": self.reclaim_pending,
                "direct_teach": {"mode": self.teach_mode, "message": self.teach_message, "pending": self.teach_pending},
                "error": self.error, "topics": topics, "joint_publishers": self.joint_publishers,
                "display_joint_topic": displayed["name"],
                "work_points": self.work_points.points if self.work_points else None,
                "parcel_box": self.parcel_box.value if self.parcel_box else None,
                "pick_active": bool(self.picks and self.picks.busy),
                "hand_busy": bool(self.hand and self.hand.busy),
                "transfer_active": bool(self.picks and self.picks.transfer_active),
                "pose_tests": self.pose_tests.snapshot() if self.pose_tests else None}


def require_robot_owner(request: Request):
    if request.app.state.robot is None:
        raise HTTPException(307, headers={"Location": "http://127.0.0.1:8080" + request.url.path})


router = APIRouter(prefix="/api/robot", dependencies=[Depends(require_robot_owner)])
router.include_router(pick_router)
router.include_router(scenario_router)
router.include_router(hand_router)


@router.get("/status")
async def status(request: Request):
    return JSONResponse(request.app.state.robot.snapshot(), headers={"Cache-Control": "no-store"})


def same_site_json(request: Request):
    if (request.headers.get("sec-fetch-site") == "cross-site"
            or request.headers.get("content-type", "").split(";")[0].strip() != "application/json"):
        raise DomainError("로컬 로봇 모니터링 화면에서 요청하세요.", 403)


def require_backend(request, expected):
    if request.app.state.robot.settings.access_backend != expected:
        raise HTTPException(409, "현재 제어권 모드에서 사용할 수 없는 작업입니다.")


@router.get("/host-access/status")
def host_status(request: Request):
    require_backend(request, "mac-sdk")
    return forward_host("status", request.app.state.robot.settings)


@router.post("/host-access/reclaim", dependencies=[Depends(local_json)])
def host_reclaim(ticket: ReclaimTicket, request: Request):
    require_backend(request, "mac-sdk")
    return forward_host("reclaim", request.app.state.robot.settings, ticket)


@router.post("/host-access/mode", dependencies=[Depends(local_json)])
def host_mode(ticket: ModeTicket, request: Request):
    require_backend(request, "mac-sdk")
    return forward_host("mode", request.app.state.robot.settings, ticket)


@router.post("/host-access/servo-on", dependencies=[Depends(local_json)])
def host_servo_on(ticket: ServoTicket, request: Request):
    require_backend(request, "mac-sdk")
    return forward_host("servo-on", request.app.state.robot.settings, ticket)


@router.post("/control/reclaim", dependencies=[Depends(same_site_json)])
async def reclaim_control(request: Request):
    require_backend(request, "ros")
    return await request.app.state.robot.reclaim_control()


@router.put("/control/direct-teach", dependencies=[Depends(same_site_json)])
async def set_direct_teach(body: TeachRequest, request: Request):
    require_backend(request, "ros")
    return await request.app.state.robot.set_direct_teach(body.enabled)


@router.put("/poses/{slot}", dependencies=[Depends(same_site_json)])
async def save_pose(slot: int, body: PoseInput, request: Request):
    return request.app.state.robot.pose_tests.save(slot, body)


@router.get("/work-points/current")
async def current_work_pose(request: Request):
    pose = await request.app.state.robot.current_work_pose()
    return JSONResponse(pose, headers={"Cache-Control": "no-store"})


@router.get("/floor-reference")
async def floor_reference(request: Request):
    return JSONResponse(request.app.state.robot.floor.value, headers={"Cache-Control": "no-store"})


@router.get("/parcel-box")
async def parcel_box(request: Request):
    return JSONResponse(request.app.state.robot.parcel_box.value, headers={"Cache-Control": "no-store"})


@router.put("/parcel-box", dependencies=[Depends(same_site_json)])
async def save_parcel_box(body: ParcelBoxInput, request: Request):
    return request.app.state.robot.parcel_box.save(body)


@router.put("/floor-reference", dependencies=[Depends(same_site_json)])
async def save_floor_reference(body: FloorReferenceInput, request: Request):
    robot=request.app.state.robot;value=robot.floor.save(body)
    if robot.scenario and robot.scenario.config.value["revision"] is None:
        robot.scenario.config.value["virtual"]["floor"]["center_base_mm"][2]=value["z_mm"]+20
        robot.scenario.config.value["virtual"]["shelf"]["center_base_mm"][2]=value["z_mm"]+300
    return value


@router.put("/work-points/{name}", dependencies=[Depends(same_site_json)])
async def save_work_point(name: PointName, body: WorkPointInput, request: Request):
    robot = request.app.state.robot
    if name in TRANSFER_POINTS and (robot.picks.transfer_active or robot.picks.busy and robot.picks.moves[robot.picks.active_id].get("scenario")):
        raise DomainError("박스 이송 작업이 끝난 뒤 경유점이나 박스 좌표를 변경하세요.")
    return robot.work_points.save(name, body)


@router.post("/poses/{slot}/execute", status_code=202, dependencies=[Depends(same_site_json)])
async def execute_pose(slot: int, body: PoseExecution, request: Request):
    robot = request.app.state.robot
    if robot.picks.busy or robot.hand.busy:
        raise DomainError("파지 위치 이동이 끝난 뒤 다시 시도하세요.")
    if robot.settings.access_backend == "mac-sdk":
        local_json(request)
        if not body.session_id or not body.motion_token:
            raise HTTPException(409, "현재 Mac SDK 세션에서 저장한 자세를 다시 확인하세요.")
        status, access = await asyncio.to_thread(request_host, "status", robot.settings)
        if status != 200:
            raise HTTPException(status, access.get("detail", "Mac SDK 상태 조회 실패"))
        if body.session_id != access["session_id"] or body.motion_token != access.get("motion_token"):
            raise HTTPException(409, "제어권이 변경되었습니다. 현재 세션에서 자세를 다시 확인하세요.")
        if not access.get("can_move"):
            raise HTTPException(409, access.get("motion_reason") or "SDK 이동 준비 상태를 확인하세요.")
        if robot.picks.busy or robot.hand.busy:
            raise DomainError("파지 위치 이동이 끝난 뒤 다시 시도하세요.")
        return robot.pose_tests.execute(slot, body.revision, body)
    if body.session_id or body.motion_token:
        raise HTTPException(409, "제어 방식이 변경되었습니다. 자세를 다시 확인하세요.")
    return robot.pose_tests.execute(slot, body.revision)
