"""Browser transcripts use the original packing processor on every OS."""
import asyncio
import logging
from uuid import uuid4
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from .store import DomainError
from .voice_commands import INTENTS, normalized, packing_answer

log = logging.getLogger("app.voice")
router = APIRouter(prefix='/api/voice')

class Transcript(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    session: str = Field(min_length=32, max_length=32)
    turn: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=500)

class Voice:
    def __init__(self, packing, enabled, *, barge_in=False):
        self.packing, self.enabled = packing, enabled
        self.state = 'idle' if enabled else 'disabled'
        self.error = self.transcript = self.reply = ''
        self.session = None
        self.turn = 0
        self.confirming = False
        self.lock = asyncio.Lock()

    def status(self):
        return dict(state=self.state, can_stop=self.session is not None, barge_in=False,
                    last_playback=None, error=self.error, transcript=self.transcript, reply=self.reply)

    async def start(self):
        pass  # Browser owns microphone permission; no server audio subprocess.

    async def stop(self, *, session_only=False):
        self.session = None
        self.confirming = False
        self.state = 'idle' if self.enabled else 'disabled'
        return self.status()

    async def wake(self):
        if not self.enabled:
            raise DomainError('YOLO_VOICE_ENABLED=1로 서버를 시작하세요.', 503)
        if self.session or self.lock.locked():
            raise DomainError('기존 음성 대화를 중단한 뒤 다시 시작하세요.', 409)
        self.session, self.turn = uuid4().hex, 1
        self.state = 'listening'
        self.error = self.transcript = self.reply = ''
        return dict(self.status(), session=self.session, turn=self.turn)

    async def accept(self, data):
        if self.lock.locked():
            raise DomainError('이전 음성 명령을 처리 중입니다.', 409)
        async with self.lock:
            if not self.session or (data.session, data.turn) != (self.session, self.turn):
                raise DomainError('만료되었거나 중복된 음성 명령입니다.', 409)
            self.turn += 1
            self.state, self.transcript = 'processing', data.text
            intent = INTENTS.get(normalized(data.text))
            log.info("[Web Speech API 수신] session=%s, turn=%s, text='%s', normalized='%s', intent=%s",
                     data.session, data.turn, data.text, normalized(data.text), intent)
            if intent == 'end':
                self.reply = '대화를 종료했습니다.'
                log.info("[Web Speech API 응답] session=%s, reply='%s'", data.session, self.reply)
                await self.stop()
            else:
                try:
                    answer = await asyncio.to_thread(packing_answer, self.packing, intent, confirming=self.confirming)
                except DomainError as exc:
                    if self.session == data.session:
                        self.reply, self.confirming = str(exc), False
                        log.warning("[Web Speech API 도메인 예외] %s", exc)
                except Exception as exc:
                    log.error("[Web Speech API 예외] %s", exc, exc_info=True)
                    await self.stop()
                    raise
                else:
                    if self.session == data.session:
                        self.reply, self.confirming = answer.text, answer.confirm
                        log.info("[Web Speech API 응답] session=%s, reply='%s', confirming=%s",
                                 data.session, self.reply, self.confirming)
                if self.session == data.session:
                    self.state = 'listening'
            return dict(self.status(), turn=self.turn)

def check_control_origin(request):
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise DomainError('로컬 대시보드에서 음성 대화를 제어하세요.', 403)
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise DomainError('동일 출처의 대시보드에서 제어하세요.', 403)

@router.get('/status')
async def status(request: Request):
    return request.app.state.voice.status()

@router.post('/wake')
async def wake(request: Request):
    check_control_origin(request)
    return await request.app.state.voice.wake()

@router.post('/transcript')
async def transcript(data: Transcript, request: Request):
    check_control_origin(request)
    return await request.app.state.voice.accept(data)

@router.post('/stop')
async def stop_listening(request: Request):
    check_control_origin(request)
    return await request.app.state.voice.stop(session_only=True)
