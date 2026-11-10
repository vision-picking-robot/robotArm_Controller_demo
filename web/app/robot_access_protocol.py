"""The browser confirms a specific SDK session and request revision."""
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field


LOCAL_ORIGINS = {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost", "[::1]")
                 for port in (8000, 8011, 8080)}


class ReclaimTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    revision: int = Field(strict=True, ge=0)

class ServoTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    servo_token: str = Field(pattern="^[0-9a-f]{32}$", min_length=32, max_length=32)


class ModeTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    mode_token: str = Field(pattern="^[0-9a-f]{32}$", min_length=32, max_length=32)
    target: Literal["AUTO", "MANUAL"]


def local_json(request: Request):
    origin = request.headers.get("origin")
    if (request.headers.get("sec-fetch-site") == "cross-site"
            or (origin and origin not in LOCAL_ORIGINS)
            or request.headers.get("content-type", "").partition(";")[0].strip() != "application/json"):
        raise HTTPException(403, "로컬 로봇 화면에서 회수 요청을 확인하세요.")
