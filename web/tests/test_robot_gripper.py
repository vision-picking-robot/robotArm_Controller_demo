import shutil
import subprocess
from pathlib import Path

import pytest

from app.robot_gripper import CalibrationInput, Gripper
from app.store import DomainError


@pytest.mark.parametrize("state,voltage,expected", [
    ("completed", " 241", 24.1),
    ("failed", " 240", 24.0),
    ("failed", " -1", None),
    ("completed", "", None),  # Previous SDK worker protocol.
])
def test_host_gripper_voltage_feedback(state, voltage, expected):
    import asyncio
    from app.robot_host_access import AccessServer

    async def receive():
        server = AccessServer.__new__(AccessServer)
        server.motion = asyncio.get_running_loop().create_future()
        result = server.motion
        server.hand_result(f"{state} ok 350 30 120 250 5 0{voltage}")
        response = await result
        assert response["feedback"]["input_voltage_v"] == expected
        assert response["state"] == state
        assert response["feedback"]["position"] == 350

    asyncio.run(receive())


@pytest.mark.parametrize("reason,expected", [
    ("response_timeout_296_0", "Modbus 응답 시간 초과 (레지스터 40297, 수신 0바이트)"),
    ("position_timeout", "목표 위치 도달 확인 시간 초과"),
])
def test_host_distinguishes_serial_timeout_from_position_timeout(reason, expected):
    import asyncio
    from app.robot_host_access import AccessServer

    async def receive():
        server = AccessServer.__new__(AccessServer)
        server.motion = asyncio.get_running_loop().create_future()
        result = server.motion
        server.hand_result(f"failed {reason} -1 0 0 0 0 0 -1")
        assert expected in (await result)["message"]

    asyncio.run(receive())


def test_serial_wire_format_and_readback(tmp_path):
    compiler = shutil.which("c++")
    if not compiler:
        pytest.skip("C++ compiler not installed")
    binary = tmp_path / "serial"
    subprocess.run([compiler,"-std=c++17",str(Path(__file__).with_name("rh_p12_serial.cpp")),"-o",binary],check=True)
    subprocess.run([binary],check=True,timeout=10)


def test_device_table_rounds_open_between_measured_points(tmp_path):
    hand = Gripper(tmp_path)
    points = [{"position":100,"width_mm":80}, {"position":200,"width_mm":49}, {"position":450,"width_mm":10}]
    hand.save(CalibrationInput(revision=None,points=points[::-1]))
    assert hand.position_for(50) == 196
    assert hand.width_at(196) >= 50 > hand.width_at(197)
    assert hand.position_for(49) == 200
    assert Gripper(tmp_path).table == hand.table
    with pytest.raises(DomainError,match="교시 범위"):
        hand.position_for(90)
    with pytest.raises(ValueError):
        CalibrationInput(revision=None,points=[points[0],points[0]])


def test_profile_contact_hold_and_empty_grip_serial(tmp_path):
    compiler=shutil.which("c++")
    if not compiler:pytest.skip("C++ compiler not installed")
    binary=tmp_path/'scenario-serial'
    subprocess.run([compiler,'-std=c++17',str(Path(__file__).with_name('rh_p12_scenario.cpp')),'-o',binary],check=True)
    subprocess.run([binary],check=True,timeout=10)


def test_async_worker_joint_only_stop_and_control_loss(tmp_path):
    compiler=shutil.which('c++')
    if not compiler:pytest.skip('C++ compiler not installed')
    binary=tmp_path/'scenario-worker'
    subprocess.run([compiler,'-std=c++17',str(Path(__file__).with_name('scenario_worker.cpp')),'-o',binary],check=True)
    subprocess.run([binary],check=True,timeout=10)


@pytest.fixture
def gripper_client(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from app.robot_gripper import router
    hand = Gripper(tmp_path)
    hand.save(CalibrationInput(revision=None, points=[
        {"position": 0, "width_mm": 80}, {"position": 700, "width_mm": 0}]))
    robot = SimpleNamespace(hand=hand, settings=SimpleNamespace(access_backend="mac-sdk"),
                            picks=SimpleNamespace(busy=False),
                            pose_tests=SimpleNamespace(snapshot=lambda: {"busy": False}))
    calls = []
    used = set()
    def host(op, settings, command):
        if command.motion_token in used:
            return 409, {"detail": "이미 사용한 토큰"}
        used.add(command.motion_token)
        calls.append((op, command))
        return 200, {"state": "completed", "message": "완료", "feedback": {"position": command.position}}
    monkeypatch.setattr("app.robot_gripper.request_host", host)
    app = FastAPI()
    app.state.robot = robot
    app.include_router(router, prefix="/api/robot")
    @app.exception_handler(DomainError)
    async def error(request: Request, exc: DomainError):
        return JSONResponse({"detail": str(exc)}, status_code=409)
    with TestClient(app) as client:
        yield client, robot, calls


def test_width_preview_is_read_only_and_approved_test_uses_saved_table(gripper_client):
    client, robot, calls = gripper_client
    body = {"revision": robot.hand.table["revision"], "width_mm": 50}
    preview = client.post('/api/robot/gripper/test/preview', json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["position"] == 262
    assert preview.json()["estimated_width_mm"] >= 50
    assert calls == []
    command = {**body, "session_id": "test", "motion_token": "a"*32}
    response = client.post('/api/robot/gripper/test', json=command)
    assert response.status_code == 200, response.text
    assert response.json()["actual_width_mm"] >= 50
    assert [(op, c.action, c.position) for op,c in calls] == [("gripper", "position", 262)]
    assert client.post('/api/robot/gripper/test', json=command).status_code == 409
    assert len(calls) == 1


def test_width_test_rejects_stale_out_of_range_and_busy_without_command(gripper_client):
    client, robot, calls = gripper_client
    body = {"revision": robot.hand.table["revision"], "width_mm": 50,
            "session_id": "test", "motion_token": "b"*32}
    for changed in ({"revision": "old"}, {"width_mm": 81}, {"width_mm": -1}):
        assert client.post('/api/robot/gripper/test', json={**body, **changed}).status_code in (409, 422)
    robot.picks.busy = True
    assert client.post('/api/robot/gripper/test', json=body).status_code == 409
    assert calls == []
