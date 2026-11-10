import asyncio
import os
import threading
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, field_validator

try:
    from .database import Database, SEED_TAGS, normalize_uid
    from .rfid_scanner import RFIDScanner
except ImportError:
    from database import Database, SEED_TAGS, normalize_uid
    from rfid_scanner import RFIDScanner


class ScanEvent(BaseModel):
    uid: str
    quantity: int = Field(default=1, ge=1, le=10000)
    event_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)

    @field_validator('uid')
    @classmethod
    def valid_uid(cls, value):
        return normalize_uid(value)


class Progress(BaseModel):
    completed: int = Field(ge=0)
    status: str
    error: str | None = None


class HardwareOrMockScanRequest(BaseModel):
    uid: str | None = None
    quantity: int = Field(default=1, ge=1, le=10000)
    timeout_s: float = Field(default=1.5, gt=0.1, le=10.0)


def install_rfid_routes(app: FastAPI, db_path=None):
    db = Database(db_path or os.getenv('RFID_DB', 'rfid.db'))
    db.initialize()
    app.state.database = db
    clients: set[asyncio.Queue] = set()

    def broadcast(packet):
        for queue in tuple(clients):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(packet)

    @app.get('/api/v1/tags')
    async def list_tags():
        tags = []
        for row in SEED_TAGS:
            uid = row[0]
            product = await asyncio.to_thread(db.product, uid)
            if product:
                tags.append(product)
        return {'tags': tags}

    @app.get('/api/v1/tags/{uid}')
    async def get_tag(uid: str):
        try:
            product = await asyncio.to_thread(db.product, uid)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if product is None:
            raise HTTPException(404, 'Unknown tag')
        return product

    @app.post('/api/v1/scan-event')
    async def scan(event: ScanEvent):
        try:
            task, created = await asyncio.to_thread(db.enqueue, event.uid, event.quantity, event.event_id)
        except KeyError as exc:
            raise HTTPException(404, 'Unknown tag') from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        packet = {'type': 'scan', 'task': task, 'product': await asyncio.to_thread(db.product, event.uid)}
        if created:
            broadcast(packet)
        return {**packet, 'created': created}

    @app.post('/api/v1/rfid/scan')
    async def scan_reader_or_preset(body: HardwareOrMockScanRequest):
        source = 'preset'
        scanned_uid = body.uid
        if not scanned_uid:
            stop = threading.Event()
            timer = threading.Timer(body.timeout_s, stop.set)
            timer.start()
            try:
                scanned_uid = await asyncio.to_thread(RFIDScanner().scan, stop=stop)
                source = 'hardware_pcsc'
            except Exception:
                scanned_uid = SEED_TAGS[0][0]
                source = 'mock_fallback'
            finally:
                timer.cancel()
        try:
            normalized = normalize_uid(scanned_uid)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            task, created = await asyncio.to_thread(db.enqueue, normalized, body.quantity, str(uuid4()))
        except KeyError as exc:
            raise HTTPException(404, 'Unknown tag') from exc
        except ValueError as exc:
            # If the same UID is already queued/picking, return its current task info gracefully
            current = await asyncio.to_thread(db.current_task)
            product = await asyncio.to_thread(db.product, normalized)
            return {
                'type': 'scan',
                'source': source,
                'uid': normalized,
                'task': current,
                'product': product,
                'created': False,
                'note': str(exc),
            }
        product = await asyncio.to_thread(db.product, normalized)
        packet = {'type': 'scan', 'source': source, 'uid': normalized, 'task': task, 'product': product}
        if created:
            broadcast(packet)
        return {**packet, 'created': created}

    @app.get('/api/v1/current-task')
    async def current_task():
        return {'task': await asyncio.to_thread(db.current_task)}

    @app.patch('/api/v1/tasks/{task_id}')
    async def progress(task_id: str, body: Progress):
        try:
            await asyncio.to_thread(db.progress, task_id, body.completed, body.status, body.error)
        except KeyError as exc:
            raise HTTPException(404, 'Unknown task') from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        packet = {'type': 'task_progress', 'task_id': task_id, **body.model_dump()}
        broadcast(packet)
        return packet

    @app.websocket('/ws')
    async def websocket(socket: WebSocket):
        await socket.accept()
        queue = asyncio.Queue(maxsize=100)
        clients.add(queue)

        async def sender():
            while True:
                await socket.send_json(await queue.get())

        writer = asyncio.create_task(sender())
        try:
            await socket.send_json({'type': 'snapshot', 'task': await asyncio.to_thread(db.current_task)})
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(queue)
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)

    return db


def create_app(db_path=None):
    app = FastAPI(title='RFID Pick-and-Place')
    install_rfid_routes(app, db_path=db_path)
    return app


app = create_app()
