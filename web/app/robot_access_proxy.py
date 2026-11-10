"""HTTP transport to the SDK owner on the Mac."""
import json
import os
from contextlib import closing
from http.client import HTTPConnection, HTTPException as TransportError

from fastapi import HTTPException
from fastapi.responses import JSONResponse


def request_host(operation, settings, ticket=None):
    target = "host.docker.internal" if os.getenv("ROBOT_BRIDGE_NETWORK") == "compose" else "127.0.0.1"
    try:
        with closing(HTTPConnection(target, 8011, timeout=155 if operation in {"pose", "line", "gripper"} else 10 if operation=="kinematics" else 2)) as conn:
            conn.request("POST" if ticket else "GET", "/api/robot/host-access/" + operation,
                         body=ticket.model_dump_json() if ticket else None,
                         headers={"Host": "127.0.0.1:8011", "Content-Type": "application/json",
                                  "X-Robot-Target": f"{settings.ip}:{settings.port}"})
            response = conn.getresponse()
            data = json.loads(response.read(32768))
            return response.status, data
    except (OSError, TransportError, ValueError):
        raise HTTPException(503, "Mac SDK 서버에 연결할 수 없습니다. Mac에서 bash scripts/robot-access.sh 실행 여부를 확인하세요.") from None

def forward_host(operation, settings, ticket=None):
    status, data = request_host(operation, settings, ticket)
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})
