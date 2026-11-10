import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from . import capture
from .datasets import components
from .store import DomainError


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Name(Input):
    name: str = Field(min_length=1, max_length=80)


class ModelName(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


class CollectionInput(Input):
    class_id: str
    group_id: str
    model_id: str
    interval_ms: int = Field(default=250, ge=100, le=2000)
    confidence: float = Field(default=.50, ge=.01, le=.99)


class CameraSource(Input):
    device_id: str = Field(min_length=1, max_length=1024)


class Review(Input):
    review: Literal["pending", "approved", "excluded"]


class BulkReview(Review):
    ids: list[str] = Field(min_length=1, max_length=1000)


class TrainingInput(Input):
    dataset_id: str
    initial_model_id: str | None = None
    batch: int = Field(default=4, ge=1, le=16)
    epochs: int = Field(default=50, ge=1, le=300)
    patience: int = Field(default=10, ge=1, le=100)


def public_job(job):
    return {key: value for key, value in job.items() if key not in {"dataset"}}


def router():
    api = APIRouter()
    def runtime(request):
        return request.app.state.pipeline

    @api.get("/api/classes")
    def classes(request: Request):
        return runtime(request).store.list("class")

    @api.post("/api/classes", status_code=201)
    def create_class(body: Name, request: Request):
        return runtime(request).store.named("class", body.name)

    @api.get("/api/capture-groups")
    def groups(request: Request):
        return runtime(request).store.list("group")

    @api.post("/api/capture-groups", status_code=201)
    def create_group(body: Name, request: Request):
        return runtime(request).store.named("group", body.name)

    @api.get("/api/collections")
    def collections(request: Request):
        return runtime(request).store.list("collection")

    @api.post("/api/collections", status_code=201)
    def create_collection(body: CollectionInput, request: Request):
        return runtime(request).create_collection(body.model_dump())

    @api.get("/api/collections/{identifier}")
    def collection(identifier: str, request: Request):
        rt = runtime(request)
        record = rt.store.get("collection", identifier)
        live = rt.collector.snapshot()
        return {**record, "live": live if live["id"] == identifier else None}

    @api.post("/api/collections/{identifier}/start")
    def start_collection(identifier: str, request: Request):
        return runtime(request).start_collection(identifier)

    @api.post("/api/collections/{identifier}/stop")
    def stop_collection(identifier: str, request: Request):
        return runtime(request).stop_collection(identifier)

    @api.post("/api/collections/{identifier}/heartbeat")
    def heartbeat(identifier: str, request: Request):
        return runtime(request).collector.heartbeat(identifier)

    @api.get("/api/collections/{identifier}/samples")
    def samples(identifier: str, request: Request):
        rt = runtime(request)
        rt.store.get("collection", identifier)
        records = [s for s in rt.store.list("sample") if s["collection_id"] == identifier]
        # Review similarity is independent of environment grouping used for splitting.
        review = [{**s, "group_id": s["id"]} for s in records]
        similar = {s["id"]: i for i, group in enumerate(components(review)) for s in group}
        return [{**s, "similar_group": similar[s["id"]], "image_url": f'/api/samples/{s["id"]}/thumbnail'} for s in records]

    @api.get("/api/samples/{identifier}/image")
    def sample_image(identifier: str, request: Request):
        rt = runtime(request)
        sample = rt.store.get("sample", identifier)
        path = rt.store.root / sample["image"]
        if not path.is_file():
            raise DomainError("사진 파일이 없습니다", 404)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})

    @api.get("/api/samples/{identifier}/thumbnail")
    def thumbnail(identifier: str, request: Request):
        import cv2
        rt = runtime(request)
        sample = rt.store.get("sample", identifier)
        frame = cv2.imread(str(rt.store.root / sample["image"]))
        if frame is None:
            raise DomainError("사진 파일이 없습니다", 404)
        h, w = frame.shape[:2]
        scale = min(1., 400/w)
        resized = cv2.resize(frame, (round(w*scale), round(h*scale)))
        ok, data = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise DomainError("썸네일 생성 실패", 500)
        return Response(data.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @api.patch("/api/samples/{identifier}")
    def review(identifier: str, body: Review, request: Request):
        return runtime(request).review_samples([identifier], body.review)

    @api.post("/api/samples/review")
    def bulk_review(body: BulkReview, request: Request):
        return runtime(request).review_samples(body.ids, body.review)

    @api.get("/api/datasets")
    def datasets(request: Request):
        return [{k: v for k, v in d.items() if k != "items"} for d in runtime(request).store.list("dataset")]

    @api.post("/api/datasets", status_code=201)
    def create_dataset(request: Request):
        return runtime(request).dataset()

    @api.get("/api/datasets/{identifier}")
    def dataset(identifier: str, request: Request):
        return runtime(request).store.get("dataset", identifier)

    @api.get("/api/training/jobs")
    def jobs(request: Request):
        return [public_job(j) for j in runtime(request).store.list("job")]

    @api.post("/api/training/jobs", status_code=202)
    def create_job(body: TrainingInput, request: Request):
        return public_job(runtime(request).create_job(body.model_dump()))

    @api.get("/api/training/jobs/{identifier}")
    def job(identifier: str, request: Request):
        return public_job(runtime(request).store.get("job", identifier))

    @api.post("/api/training/jobs/{identifier}/cancel", status_code=202)
    def cancel(identifier: str, request: Request):
        return public_job(runtime(request).cancel_job(identifier))

    @api.get("/api/training/jobs/{identifier}/log")
    def log(identifier: str, request: Request):
        from pathlib import Path
        job = runtime(request).store.get("job", identifier)
        output = {}
        for name in ("train", "export"):
            path = Path(job["directory"])/f"{name}.log"
            if path.exists():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size-16000))
                    output[name] = stream.read().decode("utf-8", errors="replace")
        return output

    @api.get("/api/models")
    def models(request: Request):
        rt = runtime(request)
        return {"active_model_id": rt.model_id, "backend": rt.settings.backend, "models": rt.registry.models()}

    @api.patch("/api/models/{identifier}")
    def rename_model(identifier: str, body: ModelName, request: Request):
        return runtime(request).registry.rename(identifier, body.name)

    @api.post("/api/models/{identifier}/activate", status_code=202)
    def activate(identifier: str, request: Request):
        return runtime(request).activate(identifier)

    @api.get("/api/camera/devices")
    def cameras(response: Response):
        response.headers["Cache-Control"] = "no-store"
        try:
            return capture.camera_devices()
        except RuntimeError as exc:
            raise DomainError(str(exc), 503) from exc

    @api.post("/api/camera/select")
    def select_camera(body: CameraSource, request: Request):
        return runtime(request).select_camera(body.device_id)

    @api.post("/api/camera/start")
    def camera_start(request: Request):
        return runtime(request).camera_start()

    @api.post("/api/camera/stop")
    def camera_stop(request: Request):
        return runtime(request).camera_stop()

    @api.get("/api/operations/{identifier}")
    def operation(identifier: str, request: Request):
        return runtime(request).store.get("operation", identifier)

    @api.websocket("/ws/events")
    async def events(websocket: WebSocket):
        await websocket.accept()
        rt = websocket.app.state.pipeline
        try:
            while not rt.stop_event.is_set():
                # No per-client queue: one current snapshot, bounded send timeout.
                snapshot = await asyncio.to_thread(rt.events)
                snapshot["jobs"] = [public_job(j) for j in snapshot["jobs"]]
                await asyncio.wait_for(websocket.send_json(snapshot), rt.settings.send_timeout)
                await asyncio.sleep(1)
        except (WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
            pass
        finally:
            try:
                await asyncio.wait_for(websocket.close(), rt.settings.send_timeout)
            except (WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
                pass
    return api
