"""Mac-only HTTP owner of one persistent Blink/DRFL connection; no ROS dependency."""
import asyncio
import logging
import json
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from scripts.check_robot_sdk_host import CACHE, lan_route, lan_settings, sdk_environment
from .robot_access_protocol import ModeTicket, ReclaimTicket, ServoTicket, local_json
from .robot_gripper import HandCommand
from .robot_poses import ACCELERATION, SPEED, HostLineCommand, HostPoseCommand
from .robot_scenario_config import KinematicsRequest, ScenarioCommand, OperationStop

WORKER = "/usr/local/bin/e0509-access-worker"
LOG = logging.getLogger("uvicorn.error")


async def spawn_worker(cfg):
    return await asyncio.create_subprocess_exec(
        str(CACHE / "blink"), "-m", "-L", "/dev/null", WORKER, env=sdk_environment(cfg),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)


class AccessServer:
    def __init__(self, *, route_lookup=lan_route, spawn=spawn_worker, callback_timeout=10, retry_delay=3):
        self.cfg = lan_settings()
        self.route_lookup, self.spawn = route_lookup, spawn
        self.callback_timeout, self.retry_delay = callback_timeout, retry_delay
        self.route = None
        self.process = None
        self.task = None
        self.motion = None
        self.operations={}
        self.active_operation=None
        self.inspection=None
        self.reset("disconnected", "SDK 연결 대기")

    def reset(self, connection, detail):
        if self.active_operation:
            self.operations[self.active_operation["id"]].update(state="unknown",message="SDK 세션 단절 · 자동 재개하지 않습니다.")
            self.active_operation=None
        if self.inspection:
            _,future=self.inspection
            if not future.done():future.set_exception(HTTPException(503,"SDK 좌표 조회 연결이 끊겼습니다."))
            self.inspection=None
        self.finish_motion("unknown", "SDK 연결이 끊겨 자세 이동 결과를 확인할 수 없습니다.")
        self.scenario_supported=False
        self.circle_supported=False
        self.mode_supported = False
        self.gripper_supported = False
        self.hand_feedback = None
        self.hand_target = None
        self.line_supported = False
        self.mode_token = self.mode_request = self.mode_target = None
        self.mode_result = "idle"
        self.mode_deadline = 0
        self.mode_verified = True
        self.servo_supported = False
        self.safe_stop_reset_supported = False
        self.servo_token = self.servo_request = None
        self.servo_result = "idle"
        self.servo_deadline = 0
        self.servo_accepted = False
        self.grant_number = 0
        self.motion_token = None
        self.mode = self.system = None
        self.session = None
        self.connection, self.detail = connection, detail
        self.sdk = self.controller = self.state = None
        self.flange = None
        self.initialized = self.held = False
        self.callback = self.callback_at = self.held_since = None
        self.result, self.deadline, self.revision = "idle", None, 0

    def snapshot(self):
        if self.result == "pending" and time.monotonic() >= self.deadline:
            # The SDK callback carries no request ID. Don't risk a second force while
            # the outcome is unknown; a late callback still belongs to this session.
            self.result = "unknown"
        connected = self.connection == "connected"
        held = connected and self.held
        reason = self.motion_reason()
        return dict(mode_change=self.mode_status(), servo=self.servo_status(), motion_token=self.motion_token, can_move=reason is None, motion_reason=reason,
                    gripper_supported=self.gripper_supported, gripper_feedback=self.hand_feedback, gripper_target=self.hand_target,
                    line_supported=self.line_supported, transfer_supported=self.line_supported,scenario_supported=self.scenario_supported,circle_supported=self.circle_supported,
                    operation_mode=self.mode, connection=self.connection, route=self.route, detail=self.detail,
                    sdk_version=self.sdk, controller_version=self.controller, flange_version=self.flange, robot_state=self.state,
                    initialized=self.initialized, session_id=self.session, revision=self.revision,
                    worker_pid=self.process.pid if self.process and self.session else None,
                    held=held, held_seconds=int(time.monotonic() - self.held_since) if held else None,
                    last_callback=self.callback, callback_at=self.callback_at, result=self.result,
                    can_reclaim=connected and self.initialized and (self.state in {"SAFE_OFF", "STANDBY"}
                    or self.state == "SAFE_STOP" and self.safe_stop_reset_supported)
                    and not held and self.result not in {"pending", "unknown"})

    def mode_status(self):
        if self.mode_request and time.monotonic() > self.mode_deadline:
            self.mode_result = "unknown"
        eligible = (self.mode_supported and self.connection == "connected" and self.initialized and self.held
                    and self.system == "REAL" and self.state == "STANDBY" and self.mode in {"MANUAL", "AUTO"}
                    and not self.servo_request and not self.motion and not self.mode_request)
        if not eligible:
            self.mode_token = None
        elif not self.mode_token:
            self.mode_token = uuid4().hex
        return dict(token=self.mode_token, result=self.mode_result, target=self.mode_target, verified=self.mode_verified)

    def require_mode(self, ticket):
        if str(ticket.session_id) != self.session or ticket.mode_token != self.mode_status()["token"]:
            raise HTTPException(409, "제어권 또는 로봇 상태가 변경되었습니다. 운전 모드를 다시 확인하세요.")
        if self.mode_verified and self.mode == ticket.target:
            raise HTTPException(409, "이미 선택한 운전 모드입니다.")

    async def set_mode(self, ticket):
        self.require_mode(ticket)
        await self.check_route(str(ticket.session_id))
        self.require_mode(ticket)
        self.mode_request, self.mode_token = ticket.mode_token, None
        self.mode_target, self.mode_result = ticket.target, "pending"
        self.mode_verified = False
        self.mode_deadline = time.monotonic() + self.callback_timeout
        self.motion_token = uuid4().hex
        try:
            self.process.stdin.write(f"mode {self.grant_number} {ticket.mode_token} {ticket.target}\n".encode())
        except (OSError, RuntimeError):
            self.drop_worker("SDK 작업자 연결이 끊겼습니다.")
            raise HTTPException(503, self.detail) from None
        return self.snapshot()

    def servo_status(self):
        if self.servo_request and time.monotonic() >= self.servo_deadline:
            self.servo_result = "unknown"
        if self.connection != "connected" or not self.initialized:
            reason = "SDK 연결 및 초기화 대기"
        elif not self.servo_supported:
            reason = "SDK 작업자를 업데이트해야 합니다."
        elif not self.held:
            reason = "Mac SDK 세션의 제어권이 필요합니다. 매니퓰레이터의 제어권 보기에서 회수하세요."
        elif self.servo_request:
            reason = ("결과 미확인 · 제어기 상태와 알람을 확인하세요." if self.servo_result == "unknown"
                      else "STANDBY 전환 요청 중 · 제어기 응답 대기")
        elif self.state == "STANDBY":
            reason = "서보 켜짐 · STANDBY"
        elif self.state == "SAFE_STOP" and not self.safe_stop_reset_supported:
            reason = "SAFE_STOP 복구를 지원하는 SDK 작업자로 업데이트해야 합니다."
        elif self.state not in {"SAFE_OFF", "SAFE_STOP"} or self.mode_request or self.motion or self.active_operation or self.inspection or self.system != "REAL" or self.mode not in {"AUTO", "MANUAL"}:
            reason = "진행 중인 작업이 없는 실물 로봇의 SAFE_STOP·SAFE_OFF 상태에서 요청할 수 있습니다."
        else:
            reason = None
        if reason:
            self.servo_token = None
        elif not self.servo_token:
            self.servo_token = uuid4().hex
        message = reason or ("SDK가 STANDBY 전환을 거절했습니다. 제어기 알람과 정지 원인을 확인하세요."
                             if self.servo_result == "failed" else
                             "SAFE_STOP · 정지 원인을 해소한 후 STANDBY로 복구하세요." if self.state == "SAFE_STOP" else "서보 꺼짐 · SAFE_OFF")
        return dict(token=self.servo_token, result=self.servo_result, message=message)

    def require_servo(self, ticket):
        status = self.servo_status()
        if str(ticket.session_id) != self.session or ticket.servo_token != status["token"]:
            raise HTTPException(409, "세션 또는 서보 상태가 변경되었습니다. 현재 상태를 다시 확인하세요.")

    async def servo_on(self, ticket):
        self.require_servo(ticket)
        await self.check_route(str(ticket.session_id))
        self.require_servo(ticket)
        self.servo_request, self.servo_token = ticket.servo_token, None
        self.servo_result, self.servo_accepted = "pending", False
        self.servo_deadline = time.monotonic() + self.callback_timeout
        # Invalidate any pose confirmation opened before servo activation.
        self.motion_token = uuid4().hex
        try:
            action = "recover" if self.state == "SAFE_STOP" else "servo"
            self.process.stdin.write(f"{action} {self.grant_number} {self.servo_request}\n".encode())
        except (OSError, RuntimeError):
            self.drop_worker("SDK 작업자 연결이 끊겼습니다.")
            raise HTTPException(503, self.detail) from None
        return self.snapshot()

    def motion_reason(self):
        if self.connection != "connected" or not self.initialized:
            return "Mac SDK 연결 및 초기화 대기"
        if not self.held:
            return "Mac SDK 세션에서 제어권을 먼저 획득하세요."
        if self.servo_request:
            return "서보 ON 결과 확인 대기"
        if self.motion:
            return "이전 SDK 이동의 완료 응답을 기다리고 있습니다."
        if self.state == "SAFE_OFF":
            return "SAFE_OFF · 서보가 꺼져 있습니다. STANDBY·자동 모드에서 이동할 수 있습니다."
        if not self.state:
            return "제어기 대기 상태를 확인하고 있습니다."
        if self.state == "SAFE_STOP":
            return "현재 SAFE_STOP · 정지 원인을 해소한 후 좌측 하단의 STANDBY 복구를 요청하세요."
        if self.state != "STANDBY":
            return f"현재 {self.state} · 제어기에서 STANDBY 상태를 확인하세요."
        if self.mode_request or not self.mode_verified:
            return "운전 모드 전환 결과를 확인하세요."
        if self.mode != "AUTO":
            return f"현재 {self.mode or '모드 미확인'} · 자동 운전 모드가 필요합니다."
        if self.system != "REAL":
            return "SDK에서 실물 제어기 상태를 확인하세요."
        return None

    def finish_motion(self, state, message):
        if self.motion and not self.motion.done():
            self.motion.set_result(dict(state=state, message=message))
        self.motion = None

    def require_motion(self, command):
        if self.inspection:raise HTTPException(409,"SDK 좌표 조회가 끝나면 다시 시도하세요.")
        if command.session_id != self.session or command.motion_token != self.motion_token:
            raise HTTPException(409, "SDK 세션 또는 제어권이 변경되었습니다. 자세를 다시 확인하세요.")
        reason = self.motion_reason()
        if reason:
            raise HTTPException(409, reason)

    async def inspect_kinematics(self,body):
        if not self.scenario_supported:raise HTTPException(409,"17단계 SDK 작업자를 다시 빌드하고 시작하세요.")
        if body.session_id!=self.session or self.motion_reason() or self.inspection:
            raise HTTPException(409,self.motion_reason() or "SDK 세션 또는 좌표 조회 상태가 변경되었습니다.")
        await self.check_route(body.session_id)
        if body.session_id!=self.session or self.motion_reason() or self.inspection:raise HTTPException(409,"SDK 상태가 변경되었습니다.")
        identifier=uuid4().hex;future=asyncio.get_running_loop().create_future();self.inspection=(identifier,future)
        values=" ".join(format(v,".9g") for pose in [*body.poses,*body.joints] for v in pose)
        try:
            self.process.stdin.write(f"inspect {self.grant_number} {identifier} {len(body.poses)} {len(body.joints)} {values}\n".encode())
            result=await asyncio.wait_for(future,8)
            if body.session_id!=self.session:raise HTTPException(409,"SDK 세션이 변경되었습니다.")
            if result.get("error"):raise HTTPException(409,"SDK 좌표·그리퍼 조회 실패: "+result["error"])
            return {**result,"session_id":self.session}
        except (TimeoutError,OSError,RuntimeError):raise HTTPException(503,"SDK 좌표 조회 응답 미확인")
        finally:
            if self.inspection and self.inspection[0]==identifier:self.inspection=None

    async def begin_operation(self,command):
        if not self.scenario_supported:raise HTTPException(409,"17단계 SDK 작업자가 필요합니다.")
        if command.kind=="circle" and not self.circle_supported:raise HTTPException(409,"movec 지원 SDK 작업자를 다시 시작하세요.")
        self.require_motion(command);await self.check_route(command.session_id);self.require_motion(command)
        identifier=uuid4().hex;self.motion_token=uuid4().hex;self.state=None;self.mode_token=None
        self.motion=asyncio.get_running_loop().create_future()
        self.active_operation=dict(id=identifier,session=self.session,grant=self.grant_number,token=self.motion_token,stop=False)
        result=dict(operation_id=identifier,state="running",message="SDK 구간 실행 중")
        self.operations[identifier]=result
        if command.kind=="gripper":self.hand_target=command.position;self.hand_feedback=None
        if len(self.operations)>128:self.operations.pop(next(iter(self.operations)))
        name=command.tcp_name.encode().hex() or "-"
        target=command.target or [0]*6
        numbers=[command.speed,command.acceleration,command.position if command.position is not None else -1,
                 command.contact_ma or 0,int(command.j1_only),int(command.timeout_s)]
        values=" ".join(format(v,".9g") for v in [*target,*command.reference_tcp,*(command.via_pose or [])])
        params=" ".join(format(v,".9g") for v in numbers)
        try:self.process.stdin.write(f"segment {self.grant_number} {identifier} {command.kind} {params} {name} {values}\n".encode())
        except (OSError,RuntimeError):self.drop_worker("SDK 구간 전송 응답 미확인")
        return result

    def segment_result(self,data):
        op=self.active_operation
        if not op or data.get("id")!=op["id"]:return
        state=data.get("state")
        if state not in {"completed","failed","cancelled","unknown"}:state="unknown"
        if state=="completed" and (not self.held or self.session!=op["session"] or self.grant_number!=op["grant"] or self.mode!="AUTO" or not self.mode_verified):state="unknown"
        result=self.operations[op["id"]];result.update(state=state,message=data.get("message") or "구간 응답 미확인")
        if isinstance(data.get("current_joints"),list):result["current_joints"]=data["current_joints"]
        if state=="completed" and not op["stop"]:result["next_motion_token"]=op["token"]
        elif op["stop"] and state=="completed":result["state"]="cancelled"
        if isinstance(data.get("feedback"),dict):
            self.hand_feedback={**data["feedback"],"state":state,"observed_at":datetime.now(timezone.utc).isoformat()}
            result["feedback"]=self.hand_feedback
            if type(self.hand_feedback.get("goal_position")) is int and self.hand_feedback["goal_position"]>=0:self.hand_target=self.hand_feedback["goal_position"]
        self.active_operation=None;self.finish_motion(result["state"],result["message"])

    def stop_operation(self,body):
        op=self.active_operation
        if body.session_id!=self.session:raise HTTPException(409,"SDK 세션이 변경되었습니다.")
        if not op or op["id"]!=body.operation_id:raise HTTPException(409,"현재 실행 중인 구간이 아닙니다.")
        if not op["stop"]:
            op["stop"]=True;self.operations[op["id"]].update(state="stopping",message="감속 정지 확인 중")
            try:self.process.stdin.write(f"halt {op['grant']} {op['id']}\n".encode())
            except (OSError,RuntimeError):self.drop_worker("정지 요청 결과 미확인")
        return self.operations[op["id"]]

    async def move_pose(self, command):
        hand = isinstance(command, HandCommand)
        if hand and not self.gripper_supported:
            raise HTTPException(409, "RH-P12 지원 SDK 작업자가 필요합니다.")
        line = isinstance(command, HostLineCommand)
        if line and not self.line_supported:
            raise HTTPException(409, "직선 이동을 지원하는 SDK 작업자를 시작하세요.")
        self.require_motion(command)
        await self.check_route(command.session_id)
        self.require_motion(command)
        self.mode_token = None
        self.state = None  # The last STANDBY sample is no longer current during a move.
        self.motion_token = uuid4().hex  # Consume the confirmation before writing to the worker.
        continuation_token = self.motion_token
        future = self.motion = asyncio.get_running_loop().create_future()
        if hand:
            operation, parameters = "hand", [command.target, int(command.action == "close")]
            self.hand_feedback = None
            if command.action != "read":
                self.hand_target = command.target
        elif line:
            operation, parameters = "line", command.pose
        else:
            operation, parameters = "move", (SPEED, ACCELERATION, *command.angles)
        values = " ".join(format(v, ".9g") for v in parameters)
        try:
            self.process.stdin.write(f"{operation} {self.grant_number} {values}\n".encode())
        except (OSError, RuntimeError):
            self.drop_worker("SDK 작업자 연결이 끊겼습니다.")
        try:
            result = await asyncio.wait_for(asyncio.shield(future), 145)
            if (line or hand) and result["state"] == "completed":
                # Bind the next leg to this grant. Loss/regrant, mode changes or
                # intervening commands invalidate this token even in the same session.
                return {**result, "next_motion_token": continuation_token}
            return result
        except asyncio.TimeoutError:
            # Keep the worker busy until it actually returns or the connection ends.
            return dict(state="unknown", message="SDK 이동 응답 시간 초과 · 로봇의 실제 위치를 확인하세요.")

    def eligible(self, ticket):
        """All requests and worker events share one asyncio loop."""
        if self.connection != "connected" or str(ticket.session_id) != self.session:
            raise HTTPException(409, "SDK 세션이 변경되었습니다. 현재 상태를 다시 확인하세요.")
        status = self.snapshot()
        if ticket.revision != self.revision or self.held or self.result in {"pending", "unknown"}:
            return False
        if not status["can_reclaim"]:
            raise HTTPException(409, "초기화 완료 및 로봇 정지 상태(SAFE_OFF/SAFE_STOP/STANDBY)를 확인하세요.")
        return True

    async def reclaim(self, ticket):
        if not self.eligible(ticket):
            return self.snapshot()
        await self.check_route(str(ticket.session_id))
        # Recheck after the route lookup, then write atomically with respect to other tabs.
        if not self.eligible(ticket):
            return self.snapshot()
        try:
            self.process.stdin.write(b"force\n")
        except (OSError, RuntimeError):
            self.drop_worker("SDK 작업자 연결이 끊겼습니다.")
            raise HTTPException(503, self.detail) from None
        self.revision += 1
        self.deadline = time.monotonic() + self.callback_timeout
        self.result = "pending"
        self.detail = None
        return self.snapshot()

    async def check_route(self, session_id):
        try:
            route = await asyncio.to_thread(self.route_lookup, self.cfg)
            if route != self.route:
                raise ValueError("SDK 연결 후 로봇 유선 경로가 변경되었습니다.")
        except Exception as exc:
            if self.session == session_id:
                # Retire the old connection before accepting another request.
                self.drop_worker(str(exc))
            raise HTTPException(409, "유선 경로를 다시 확인하세요. 요청을 전송하지 않았습니다.") from None

    def drop_worker(self, detail):
        self.reset("disconnected", detail)
        if self.process:
            with suppress(ProcessLookupError):
                self.process.kill()

    def hand_result(self, value):
        if self.motion is None or self.motion.done():
            self.finish_motion("unknown", "그리퍼 결과 미확인")
            return
        reasons = {"wrong_model":"장치 1의 모델 번호가 RH-P12-RN-A와 다릅니다.", "control":"제어권과 로봇 정지 상태를 확인하세요.", "current_config":"장치의 위치 제어 모드와 목표 전류·한계를 확인하세요.",
                   "device_state":"장치 오류·동작 상태 또는 위치 범위를 확인하세요.", "timeout":"응답 또는 목표 위치 확인 시간 초과",
                   "position_timeout":"그리퍼 목표 위치 도달 확인 시간 초과",
                   "position_limits":"그리퍼 장치의 최소·최대 위치 한계를 확인하세요.",
                   "bad_response":"Modbus 응답 형식 또는 CRC 오류", "device_exception":"Modbus 장치 예외",
                   "serial_open":"플랜지 시리얼 열기 실패 (57600 bps, 8N1)",
                   "serial_write":"플랜지 시리얼 요청 전송 실패", "serial_close":"플랜지 시리얼 닫기 실패"}
        try:
            state, reason, *numbers = value.split()
            # Accept the previous worker format until the SDK worker is rebuilt.
            if len(numbers) not in (6, 7):
                raise ValueError()
            pos, current, goal, limit, mode, error = map(int,numbers[:6])
            voltage = int(numbers[6]) if len(numbers) == 7 else -1
            if state not in {"completed","failed","unknown"}:
                raise ValueError()
        except ValueError:
            self.finish_motion("unknown", "그리퍼 응답을 해석할 수 없습니다.")
            return
        self.hand_feedback = {"position":pos if pos >= 0 else None, "current_ma":current, "goal_current_ma":goal,
                              "limit_ma":limit, "mode":mode, "error":error, "state":state,
                              "input_voltage_v":voltage / 10.0 if voltage >= 0 else None,
                              "observed_at":datetime.now(timezone.utc).isoformat()}
        message = "장치 응답 확인" if state == "completed" else reasons.get(reason, "플랜지 직렬통신 실패 · " + reason)
        if state != "completed" and reason.startswith("response_timeout_"):
            parts = reason.split("_")
            if len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
                message = f"그리퍼 Modbus 응답 시간 초과 (레지스터 {40001 + int(parts[2])}, 수신 {int(parts[3])}바이트)"
        self.motion.set_result({"state":state, "message":message, "feedback":self.hand_feedback})
        self.motion = None

    def event(self, key, value):
        if key == "flange":
            self.flange = value
            LOG.info("SDK flange: %s", value)
            return
        if key=="feature" and value=="scenario17":self.scenario_supported=True
        elif key=="feature" and value=="circle":self.circle_supported=True
        elif key=="inspection":
            try:
                data=json.loads(value)
                if self.inspection and self.inspection[0]==data.get("id") and not self.inspection[1].done():self.inspection[1].set_result(data)
            except (ValueError,TypeError):pass
        elif key=="segment":
            try:self.segment_result(json.loads(value))
            except (ValueError,TypeError,AttributeError):
                if self.active_operation:self.segment_result(dict(id=self.active_operation["id"],state="unknown",message="구간 응답 형식 오류"))
        elif key == "sdk":
            self.sdk = value
        elif key == "feature" and value == "rh_p12":
            self.gripper_supported = True
        elif key == "hand":
            self.hand_result(value)
        elif key == "feature" and value == "line":
            self.line_supported = True
        elif key == "feature" and value == "operation_mode":
            self.mode_supported = True
        elif key == "mode_result":
            token, _, observed = value.partition(" ")
            if token == self.mode_request:
                self.mode_request = None
                self.mode_verified = observed == self.mode_target
                self.mode_result = "confirmed" if self.mode_verified else "failed"
                if self.mode_verified:
                    self.mode = observed
        elif key == "feature" and value == "servo_on":
            self.servo_supported = True
        elif key == "feature" and value == "safe_stop_reset":
            self.safe_stop_reset_supported = True
        elif key == "servo":
            token, _, result = value.partition(" ")
            if token == self.servo_request:
                if result == "accepted":
                    self.servo_accepted = True
                elif result == "refused":
                    self.servo_request, self.servo_result = None, "failed"
        elif key == "connected":
            self.connection, self.controller, self.detail = "connected", value, None
            LOG.info("SDK connected: %s · %s · DRCF %s", self.route, self.session, value)
        elif key in {"ready", "initializing"}:
            parts = value.split()
            previous_state = self.state
            self.initialized, self.state = key == "ready", parts[0]
            previous_mode = self.mode
            self.mode, self.system = parts[1:] if len(parts) == 3 else (None, None)
            if self.mode != previous_mode or not self.initialized or self.system != "REAL" or self.state != "STANDBY":
                self.mode_token = None
            if self.mode != previous_mode and self.held:
                self.motion_token = uuid4().hex
            if self.servo_request and self.servo_accepted and self.initialized and self.state == "STANDBY":
                self.servo_request, self.servo_result = None, "confirmed"
            if self.state != previous_state or self.state not in {"SAFE_OFF", "SAFE_STOP"} or not self.initialized or self.system != "REAL" or self.mode not in {"AUTO", "MANUAL"}:
                self.servo_token = None
            if self.servo_result == "confirmed" and self.state != "STANDBY":
                self.servo_result = "idle"
        elif key == "pose":
            outcomes = {"completed": ("completed", "SDK가 자세 이동 완료를 응답했습니다."),
                        "failed": ("failed", "SDK가 이동 실패를 응답했습니다. 실제 위치를 확인하세요."),
                        "refused": ("failed", "로봇 상태 또는 제어권이 변경되어 이동을 보내지 않았습니다.")}
            if value in outcomes:
                self.finish_motion(*outcomes[value])
        elif key == "callback" and value in {"GRANT", "DENY", "LOSS", "REQUEST"}:
            LOG.info("SDK access: %s · %s", self.session, value)
            self.callback, self.callback_at = value, datetime.now(timezone.utc).isoformat()
            if value != "REQUEST":
                if value == "GRANT" and not self.held:
                    self.held_since = time.monotonic()
                    self.grant_number += 1
                    self.motion_token = uuid4().hex
                elif value != "GRANT":
                    self.mode_token = self.mode_request = None
                    self.mode_result = "idle"
                    self.servo_token = self.servo_request = None
                    self.servo_result = "idle"
                    self.motion_token = None
                    if self.motion and not self.motion.done():
                        self.motion.set_result(dict(state="unknown", message="이동 중 제어권을 잃었습니다. 실제 위치를 확인하세요."))
                self.held = value == "GRANT"
                self.result = {"GRANT": "granted", "DENY": "denied", "LOSS": "lost"}[value]
                self.detail = None
        elif key == "refused" and self.result in {"pending", "unknown"}:
            self.result, self.detail = "not_sent", value
        # 'already' follows an actual GRANT callback and does not renew its timestamp.

    async def supervise(self):
        last_error = None
        while True:
            proc = None
            try:
                self.reset("connecting", "유선 경로와 SDK 연결 확인 중")
                self.route = None
                self.route = await asyncio.to_thread(self.route_lookup, self.cfg)
                self.session = str(uuid4())
                proc = self.process = await self.spawn(self.cfg)
                while True:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=12)
                    if not self.session:
                        break  # invalidated during a request; ignore buffered old callbacks
                    if not raw:
                        raise ConnectionError("SDK 작업자가 종료되었습니다.")
                    if raw.startswith(b"E0509 "):
                        key, _, value = raw[6:].decode(errors="replace").strip().partition(" ")
                        if key == "disconnect":
                            raise ConnectionError(value)
                        self.event(key, value)
                        if key == "connected":
                            last_error = None
            except asyncio.TimeoutError:
                self.reset("disconnected", "SDK 작업자 응답 시간 초과")
            except Exception as exc:
                self.reset("disconnected", str(exc))
            finally:
                if self.session:
                    self.reset("disconnected", "SDK 연결 종료")
                self.process = None
                if proc is not None:
                    cleanup = asyncio.create_task(self.close_worker(proc))
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                        raise
            if last_error != self.detail:
                LOG.warning("SDK disconnected: %s", self.detail)
                last_error = self.detail
            await asyncio.sleep(self.retry_delay)

    @staticmethod
    async def close_worker(proc):
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    async def stop(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task


def create_host_app(server=None):
    owner = server or AccessServer()

    @asynccontextmanager
    async def lifespan(app):
        owner.task = asyncio.create_task(owner.supervise())
        try:
            yield
        finally:
            await owner.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

    def same_target(request: Request):
        target = request.headers.get("x-robot-target")
        actual = f"{owner.cfg['ROBOT_IP']}:{owner.cfg['ROBOT_PORT']}"
        if target is not None and target != actual:
            raise HTTPException(409, f"웹 제어기 {target}와 Mac SDK 제어기 {actual}가 다릅니다. 설정을 맞추고 서버를 다시 시작하세요.")

    @app.get("/api/robot/host-access/status", dependencies=[Depends(same_target)])
    async def status():
        return JSONResponse(owner.snapshot(), headers={"Cache-Control": "no-store"})

    @app.post("/api/robot/host-access/reclaim", dependencies=[Depends(same_target), Depends(local_json)])
    async def reclaim(ticket: ReclaimTicket):
        return JSONResponse(await owner.reclaim(ticket), headers={"Cache-Control": "no-store"})

    @app.post("/api/robot/host-access/mode", dependencies=[Depends(same_target), Depends(local_json)])
    async def set_mode(ticket: ModeTicket):
        return JSONResponse(await owner.set_mode(ticket), status_code=202, headers={"Cache-Control": "no-store"})

    @app.post("/api/robot/host-access/servo-on", dependencies=[Depends(same_target), Depends(local_json)])
    async def servo_on(ticket: ServoTicket):
        return JSONResponse(await owner.servo_on(ticket), status_code=202, headers={"Cache-Control": "no-store"})

    @app.post("/api/robot/host-access/pose", dependencies=[Depends(same_target), Depends(local_json)])
    async def pose(command: HostPoseCommand, request: Request):
        if not request.headers.get("x-robot-target"):
            raise HTTPException(403, "로봇 화면의 저장된 자세를 사용하세요.")
        return await owner.move_pose(command)

    @app.post("/api/robot/host-access/line", dependencies=[Depends(same_target), Depends(local_json)])
    async def line(command: HostLineCommand, request: Request):
        if not request.headers.get("x-robot-target"):
            raise HTTPException(403, "탐지 카메라에서 이동할 대상을 승인하세요.")
        return await owner.move_pose(command)

    @app.post("/api/robot/host-access/gripper", dependencies=[Depends(same_target), Depends(local_json)])
    async def gripper(command: HandCommand, request: Request):
        if not request.headers.get("x-robot-target"):
            raise HTTPException(403, "로봇 관리 또는 승인된 이송 작업을 사용하세요.")
        return await owner.move_pose(command)

    def internal(request):
        if not request.headers.get("x-robot-target"):raise HTTPException(403,"승인된 로봇 웹 서버 요청만 사용할 수 있습니다.")

    @app.post("/api/robot/host-access/kinematics",dependencies=[Depends(same_target),Depends(local_json)])
    async def inspect(body:KinematicsRequest,request:Request):
        internal(request);return await owner.inspect_kinematics(body)

    @app.post("/api/robot/host-access/operation",status_code=202,dependencies=[Depends(same_target),Depends(local_json)])
    async def operation(body:ScenarioCommand,request:Request):
        internal(request);return await owner.begin_operation(body)

    @app.get("/api/robot/host-access/operations/{identifier}",dependencies=[Depends(same_target)])
    async def operation_status(identifier:str,request:Request):
        internal(request)
        if identifier not in owner.operations:raise HTTPException(404,"구간 결과가 없습니다.")
        return owner.operations[identifier]

    @app.post("/api/robot/host-access/operation-stop",dependencies=[Depends(same_target),Depends(local_json)])
    async def operation_stop(body:OperationStop,request:Request):
        internal(request);return owner.stop_operation(body)

    return app


if __name__ == "__main__":
    import socket
    import uvicorn

    # Reserve the one local endpoint before starting a lifespan/SDK worker.
    with socket.create_server(("127.0.0.1", 8011)) as listener:
        server = uvicorn.Server(uvicorn.Config(create_host_app(), host="127.0.0.1", port=8011, access_log=False))
        with suppress(KeyboardInterrupt):
            asyncio.run(server.serve(sockets=[listener]))
