import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from robot_rfid.api_server import install_rfid_routes

from .config import Settings
from .camera_views import DisplayMode
from .packing_api import router as packing_router
from .pipeline import Pipeline
from .runtime import Runtime
from .robot_web import install_robot_routes, robot_runtime
from .store import DomainError
from .training_api import router
from .voice import Voice, router as voice_router
from .picking_integration import router as picking_integration_router

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)


class TimedStreamingResponse(StreamingResponse):
    """Bound each ASGI send, including backpressure after a client stops reading."""
    def __init__(self, content, timeout):
        super().__init__(content, media_type="multipart/x-mixed-replace; boundary=frame",
                         headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        self.timeout = timeout

    async def __call__(self, scope, receive, send):
        async def timed_send(message):
            await asyncio.wait_for(send(message), timeout=self.timeout)
        try:
            await super().__call__(scope, receive, timed_send)
        except (TimeoutError, ClientDisconnect, OSError):
            log.info("MJPEG subscriber disconnected or exceeded send timeout")
        finally:
            await self.body_iterator.aclose()


async def jpeg_frames(pipeline):
    pipeline.subscribe_video(1)
    previous_id = None
    try:
        while not pipeline.stop_event.is_set():
            if (await asyncio.to_thread(pipeline.status))["ready"]:
                if hasattr(pipeline, "video_packet"):
                    video = pipeline.video_packet()
                else:
                    jpeg = pipeline.latest_jpeg()
                    video = (pipeline.session_id, pipeline.model_version, *jpeg) if jpeg else None
                if video is not None:
                    session_id, model_version, frame_id, captured_ns, data = video
                    fresh = (time.monotonic_ns() - captured_ns) / 1e9 <= pipeline.settings.stale_seconds
                    if fresh and (session_id, frame_id) != previous_id:
                        previous_id = (session_id, frame_id)
                        header = (f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n"
                                  f"X-Frame-Id: {frame_id}\r\nX-Session-Id: {session_id}\r\nX-Model-Version: {model_version}\r\n\r\n").encode("ascii")
                        yield header + data + b"\r\n"
            await asyncio.sleep(0.03)
    finally:
        pipeline.subscribe_video(-1)


def create_app(settings=None, pipeline_factory=Pipeline, state_root=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application):
        pipeline = await asyncio.to_thread(Runtime, settings, pipeline_factory, state_root or settings.state_dir)
        application.state.pipeline = pipeline
        voice = Voice(pipeline.packing, settings.voice_enabled, barge_in=settings.voice_barge_in)
        application.state.voice = voice
        try:
            pipeline.start()
            async with robot_runtime(application, state_root or settings.state_dir, native=True):
                await voice.start()
                yield
        finally:
            try:
                await voice.stop()
            finally:
                await asyncio.to_thread(pipeline.stop)

    application = FastAPI(title="YOLOv8 Local Camera", version="0.1.0", lifespan=lifespan)
    application.include_router(router())
    application.include_router(packing_router)
    application.include_router(voice_router)
    application.include_router(picking_integration_router)
    install_robot_routes(application)
    rfid_root = Path(state_root) if state_root is not None else REPO_ROOT / 'state'
    rfid_root.mkdir(parents=True, exist_ok=True)
    install_rfid_routes(application, db_path=os.getenv("RFID_DB", str(rfid_root / "rfid.db")))

    @application.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    application.mount("/static", StaticFiles(directory=STATIC), name="static")

    @application.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @application.get("/training", include_in_schema=False)
    async def training_workspace():
        return FileResponse(STATIC / "training.html", headers={"Cache-Control": "no-store"})

    @application.get("/healthz")
    async def health():
        return {"status": "ok"}

    @application.get("/api/status")
    async def status():
        current = await asyncio.to_thread(application.state.pipeline.status)
        return JSONResponse(current, headers={"Cache-Control": "no-store"})

    @application.get("/readyz")
    async def ready():
        current = await asyncio.to_thread(application.state.pipeline.status)
        return JSONResponse(current, status_code=200 if current["ready"] else 503,
                            headers={"Cache-Control": "no-store"})

    @application.get("/api/detections/latest")
    async def latest():
        pipeline = application.state.pipeline
        current = await asyncio.to_thread(pipeline.status)
        result = pipeline.latest_result()
        if not current["ready"] or result is None or result["session_id"] != current["session_id"]:
            return JSONResponse({"error": current["state"], "status": current}, status_code=503,
                                headers={"Cache-Control": "no-store"})
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @application.get("/stream.mjpg")
    async def stream():
        return TimedStreamingResponse(jpeg_frames(application.state.pipeline), settings.send_timeout)

    @application.get("/api/detections/pick-frame")
    def pick_frame(session_id: str | None = None, frame_id: int | None = None):
        frame = application.state.pipeline.pick_frame(session_id, frame_id)
        return JSONResponse(frame, headers={"Cache-Control": "no-store"})

    @application.websocket("/ws/detections")
    async def detections(websocket: WebSocket, preview: bool = False, display_mode: DisplayMode = "detections"):
        await websocket.accept()
        pipeline = application.state.pipeline
        last_id = None
        last_state = None
        last_status_time = 0.0
        if preview:
            pipeline.subscribe_video(1)
        try:
            while not pipeline.stop_event.is_set():
                current = await asyncio.to_thread(pipeline.status)
                state_key = (current["state"], current.get("session_id"), current.get("model_version"))
                if state_key != last_state or time.monotonic() - last_status_time >= 1:
                    await asyncio.wait_for(websocket.send_json(current), timeout=settings.send_timeout)
                    last_state = state_key
                    last_status_time = time.monotonic()
                if current["ready"]:
                    jpeg = None
                    if preview:
                        frame = await asyncio.to_thread(pipeline.latest_preview, display_mode, last_id)
                        result = None
                        if frame and time.monotonic_ns() - frame[0] <= settings.stale_seconds * 1e9:
                            _, result, jpeg = frame
                    else:
                        result = pipeline.latest_result()
                    if (result and result["session_id"] == current["session_id"]
                            and (result["session_id"], result["frame_id"]) != last_id):
                        await asyncio.wait_for(websocket.send_json(result), timeout=settings.send_timeout)
                        if jpeg is not None:
                            await asyncio.wait_for(websocket.send_bytes(jpeg), timeout=settings.send_timeout)
                        last_id = (result["session_id"], result["frame_id"])
                await asyncio.sleep(0.03)
        except (WebSocketDisconnect, TimeoutError, OSError, RuntimeError):
            log.debug("WebSocket subscriber disconnected", exc_info=True)
        finally:
            if preview:
                pipeline.subscribe_video(-1)
            try:
                await asyncio.wait_for(websocket.close(), timeout=settings.send_timeout)
            except (WebSocketDisconnect, TimeoutError, OSError, RuntimeError):
                pass

    return application


app = create_app()
