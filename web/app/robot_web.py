"""The robot dashboard and API, usable without the camera or a macOS Python process."""
try:
    import fcntl
except ImportError:
    fcntl = None
import os
from contextlib import asynccontextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .robot import RobotMonitor, RobotSettings, router
from .store import DomainError

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def robot_runtime(application, state_dir, *, native=False):
    application.state.robot = None
    if native and os.environ.get("YOLO_ROBOT_WEB_OWNER") == "compose":
        yield
        return

    # A native server and a container can see the same presets. Only one may consume
    # execution revisions or recover an interrupted command for this state directory.
    directory = Path(state_dir) / "robot"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".owner.lock").open("a+b") as lock:
        try:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif os.name == "nt":
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b'\0')
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError):
            if not native:
                raise RuntimeError("Robot state already in use; stop the standalone native robot server.")
            yield
            return
        robot = RobotMonitor(RobotSettings.from_env(), state_dir)
        application.state.robot = robot
        robot.start()
        try:
            yield
        finally:
            await robot.stop()


def install_robot_routes(application):
    application.include_router(router)

    @application.get("/robot", include_in_schema=False)
    async def robot_workspace(request: Request):
        if request.app.state.robot is None:
            return RedirectResponse("http://127.0.0.1:8080/robot")
        return FileResponse(STATIC / "robot.html", headers={"Cache-Control": "no-store"})


def create_robot_app():
    @asynccontextmanager
    async def lifespan(application):
        async with robot_runtime(application, os.getenv("ROBOT_STATE_DIR", "/var/lib/vision")):
            yield

    application = FastAPI(title="Vision Lab · E0509", lifespan=lifespan)

    @application.middleware("http")
    async def revalidate_frontend(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/") and request.url.path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    install_robot_routes(application)
    application.mount("/static", StaticFiles(directory=STATIC), name="static")

    @application.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @application.get("/healthz")
    async def health():
        return {"status": "ok", "service": "robot-web"}

    return application
