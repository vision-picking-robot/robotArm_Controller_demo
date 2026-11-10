"""Three operator-defined joint presets for one E0509 and its ROS namespace."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError, field_validator

from .robot_access_proxy import request_host
from .store import DomainError, atomic_json, now

# E0509 User Manual: the physical J3 and J5 travel limits are both ±155 degrees.
LIMITS = (360, 360, 155, 360, 155, 360)
SPEED = 10.0
ACCELERATION = 10.0


class PoseRevision(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    revision: str | None = Field(max_length=32)


class JointTarget(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    angles: list[FiniteFloat] = Field(min_length=6, max_length=6)

    @field_validator("angles")
    @classmethod
    def joint_limits(cls, values):
        for joint, (angle, limit) in enumerate(zip(values, LIMITS), 1):
            if abs(angle) > limit:
                raise ValueError(f"J{joint} 각도 범위는 {-limit}° ~ {limit}°입니다.")
        return values


class PoseInput(PoseRevision, JointTarget):
    pass


class PoseExecution(PoseRevision):
    session_id: str | None = Field(default=None, max_length=36)
    motion_token: str | None = Field(default=None, max_length=32)


class HostPoseCommand(JointTarget):
    session_id: str = Field(max_length=36)
    motion_token: str = Field(min_length=32, max_length=32)


class HostLineCommand(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    pose: list[FiniteFloat] = Field(min_length=6, max_length=6)
    session_id: str = Field(max_length=36)
    motion_token: str = Field(pattern="^[0-9a-f]{32}$")


class PoseTests:
    def __init__(self, monitor, state_dir):
        self.monitor = monitor
        config = monitor.settings
        self.path = Path(state_dir) / "robot" / config.ip / str(config.port) / config.namespace / "poses.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {
            "poses": [dict(slot=i, angles=None, revision=None) for i in range(1, 4)], "last": None}
        self.task = None
        last = self.data["last"]
        if last and last["state"] in {"checking", "moving"}:
            self.finish("unknown", "서버가 재시작되었습니다. 로봇의 실제 위치를 확인하세요.")

    def write(self, data):
        atomic_json(self.path, data)
        self.data = data

    def snapshot(self):
        return {**self.data, "limits": LIMITS, "speed": SPEED, "acceleration": ACCELERATION,
                "busy": self.task is not None and not self.task.done()}

    def require_slot(self, slot, revision):
        if not 1 <= slot <= 3:
            raise DomainError("자세 번호는 1~3입니다.", 404)
        pose = self.data["poses"][slot - 1]
        if pose["revision"] != revision:
            raise DomainError("다른 요청으로 자세가 변경되었습니다. 저장값을 다시 불러오세요.")
        if self.task is not None and not self.task.done():
            raise DomainError("다른 자세 명령의 응답을 기다리고 있습니다.")
        return pose

    def save(self, slot, body):
        self.require_slot(slot, body.revision)
        poses = list(self.data["poses"])
        poses[slot - 1] = dict(slot=slot, angles=body.angles, revision=uuid4().hex)
        self.write({**self.data, "poses": poses})
        return self.snapshot()

    def require_live(self, socket):
        monitor = self.monitor
        topic = monitor.snapshot()["topics"][0]
        names = (topic["message"] or {}).get("name", [])
        if (socket is None or socket is not monitor.socket or not monitor.connected or monitor.disconnected_event
                or monitor.error or topic["state"] != "live" or len(names) != 6
                or set(names) != {f"joint_{i}" for i in range(1, 7)}):
            raise DomainError("연결된 드라이버의 최신 E0509 6축 관절 데이터가 필요합니다.", 503)

        if monitor.teach_pending or monitor.teach_mode != "auto":
            raise DomainError("직접 교시를 끄고 드라이버의 자동 모드가 확인된 뒤 이동하세요.", 409)

    def execute(self, slot, revision, sdk_ticket=None):
        pose = self.require_slot(slot, revision)
        if pose["angles"] is None:
            raise DomainError("먼저 자세를 저장하세요.")
        try:
            angles = JointTarget(angles=pose["angles"]).angles
        except ValidationError:
            raise DomainError("저장한 6축 각도가 E0509 관절 범위와 맞지 않습니다. 자세를 수정하세요.", 422) from None
        socket = self.monitor.socket
        if sdk_ticket:
            move = HostPoseCommand(angles=angles, session_id=sdk_ticket.session_id, motion_token=sdk_ticket.motion_token)
        else:
            self.require_live(socket)
        # Consume the revision before sending anything. Retries, old dialogs and other tabs
        # cannot execute the same request twice, even after a server restart.
        poses = list(self.data["poses"])
        poses[slot - 1] = {**pose, "revision": uuid4().hex}
        command = dict(slot=slot, angles=pose["angles"], state="checking",
                       message="이동 전 로봇 상태를 확인하고 있습니다.", updated_at=now())
        self.write({"poses": poses, "last": command})
        self.task = asyncio.create_task(self.run_sdk(move) if sdk_ticket else self.run(socket, angles))
        return self.snapshot()

    def finish(self, state, message):
        self.write({**self.data, "last": {**self.data["last"], "state": state,
                                         "message": message, "updated_at": now()}})

    async def run_sdk(self, command):
        self.finish("moving", "Mac SDK의 자세 이동 응답을 기다립니다.")
        try:
            status, data = await asyncio.to_thread(request_host, "pose", self.monitor.settings, command)
            if 400 <= status < 500:
                self.finish("failed", data["detail"])
            elif status == 200 and data["state"] in {"completed", "failed", "unknown"}:
                self.finish(data["state"], data["message"])
            else:
                raise ConnectionError()
        except (Exception, asyncio.CancelledError):
            self.finish("unknown", "SDK 이동 결과를 확인할 수 없습니다. 로봇의 실제 위치를 확인하세요.")

    async def run(self, socket, angles):
        dispatched = False
        try:
            state = await self.monitor.call_service(socket, "system/get_robot_state", {}, timeout=5)
            if state.get("success") is not True or state.get("robot_state") != 1:
                self.finish("failed", "로봇이 STANDBY 상태가 아닙니다. 자세 명령을 보내지 않았습니다.")
                return
            self.require_live(socket)
            self.finish("moving", "이동 요청을 전송합니다. 드라이버의 완료 응답을 기다립니다.")
            dispatched = True
            args = dict(pos=angles, vel=SPEED, acc=ACCELERATION, time=0.0, radius=0.0,
                        mode=0, blend_type=0, sync_type=0)
            result = await self.monitor.call_service(socket, "motion/move_joint", args, timeout=145)
            if result.get("success") is True:
                self.finish("completed", "드라이버가 이동 완료를 응답했습니다.")
            elif result.get("success") is False:
                self.finish("failed", "드라이버가 이동 실패를 응답했습니다. 실제 위치를 확인하세요.")
            else:
                self.finish("unknown", "완료 여부를 확인할 수 없습니다. 로봇의 실제 위치를 확인하세요.")
        except (Exception, asyncio.CancelledError):
            if dispatched:
                self.finish("unknown", "명령 결과를 확인할 수 없습니다. 로봇의 실제 위치를 확인하세요.")
            else:
                self.finish("failed", "드라이버 대기 상태를 확인할 수 없어 이동을 요청하지 않았습니다.")
