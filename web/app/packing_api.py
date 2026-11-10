from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from .store import DomainError
from .voice_commands import packing_answer


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class ButtonCommand(Input):
    action: Literal["query-stock", "query-order", "cmd-stop", "cmd-go", "cmd-pick", "cmd-order", "confirm-order", "discard-order"]


class Product(Input):
    name: str = Field(min_length=1, max_length=80)
    unit: str = Field(default="개", min_length=1, max_length=12)
    alias: str = Field(default="", max_length=80)
    bin: str = Field(default="", max_length=80)
    stock: int = Field(ge=0, le=9999)


class Stock(Input):
    stock: int = Field(ge=0, le=9999)
    expected_stock: int = Field(ge=0, le=9999)


class UserInput(Input):
    name: str = Field(min_length=1, max_length=80)


class RfidInput(Input):
    uid: str | None = Field(min_length=1, max_length=80)
    expected_uid: str | None = Field(max_length=80)


class Assignment(Input):
    class_id: int | None = Field(ge=0)


class Pause(Input):
    paused: bool


class RfidScanTrigger(Input):
    uid: str | None = Field(default=None, max_length=80)
    quantity: int = Field(default=1, ge=1, le=100)


router = APIRouter(prefix="/api/packing")


@router.post("/rfid-scan")
def rfid_scan_and_log(body: RfidScanTrigger, request: Request):
    import threading
    from uuid import uuid4
    from robot_rfid.database import SEED_TAGS, normalize_uid
    from robot_rfid.rfid_scanner import RFIDScanner

    source = "preset"
    scanned_uid = body.uid
    if not scanned_uid:
        stop = threading.Event()
        timer = threading.Timer(1.2, stop.set)
        timer.start()
        try:
            scanned_uid = RFIDScanner().scan(stop=stop)
            source = "hardware_pcsc"
        except Exception:
            scanned_uid = SEED_TAGS[0][0]
            source = "mock_fallback"
        finally:
            timer.cancel()
    try:
        normalized = normalize_uid(scanned_uid)
    except ValueError as exc:
        raise DomainError(str(exc), 422) from exc

    rfid_db = getattr(request.app.state, "database", None)
    product = rfid_db.product(normalized) if rfid_db else None
    if product is None:
        raise DomainError(f"미등록 RFID 태그입니다: {normalized}", 404)

    task = None
    created = False
    if rfid_db is not None:
        try:
            task, created = rfid_db.enqueue(normalized, body.quantity, str(uuid4()))
        except ValueError:
            task = rfid_db.current_task()
            created = False

    packing = request.app.state.pipeline.packing
    with packing.store.write_transaction() as write:
        packing.log(
            write,
            "주문",
            f"RFID 태그 인식 [{normalized}] · {product['product_name']} ({product['yolo_class_name']}) → {product['target_box_id']} ({source})",
        )
    return {
        "ok": True,
        "source": source,
        "uid": normalized,
        "product": product,
        "task": task,
        "created": created,
        "text": f"RFID [{normalized}] 인식 완료: {product['product_name']} (YOLO: {product['yolo_class_name']}, 박스: {product['target_box_id']})",
    }


@router.post("/command")
def button_command(body: ButtonCommand, request: Request):
    # An explicit confirm click has the same authority as the existing receive-order button.
    return packing_answer(request.app.state.pipeline.packing, body.action,
                          confirming=body.action in ("confirm-order", "discard-order"))


@router.get("")
def snapshot(request: Request):
    rt = request.app.state.pipeline
    return rt.packing.snapshot(rt.model_id)


@router.get("/products")
def products(request: Request):
    packing = request.app.state.pipeline.packing
    with packing.store.read_transaction():
        return packing.products()


@router.post("/users", status_code=201)
def add_user(body: UserInput, request: Request):
    return request.app.state.pipeline.packing.add_user(body.name)


@router.get("/logs")
def logs(request: Request):
    return request.app.state.pipeline.packing.logs()


@router.put("/users/{user_id}/rfid")
def assign_rfid(user_id: int, body: RfidInput, request: Request):
    request.app.state.pipeline.packing.assign_rfid(user_id, body.uid, body.expected_uid)
    return {"ok": True}


@router.post("/products", status_code=201)
def create(body: Product, request: Request):
    return request.app.state.pipeline.packing.create_product(**body.model_dump())


@router.put("/products/{sku}/stock")
def adjust(sku: str, body: Stock, request: Request):
    request.app.state.pipeline.packing.adjust(sku, body.stock, body.expected_stock)
    return {"ok": True}


@router.get("/models/{model_id}/classes")
def classes(model_id: str, request: Request):
    packing = request.app.state.pipeline.packing
    with packing.store.read_transaction():
        return packing.model_classes(model_id)


@router.put("/models/{model_id}/products/{sku}/class")
def assign(model_id: str, sku: str, body: Assignment, request: Request):
    rt = request.app.state.pipeline
    with rt.lock:
        if model_id != rt.model_id:
            raise DomainError("활성 모델이 바뀌었습니다. 새 모델에서 클래스를 선택하세요.")
        rt.packing.assign(model_id, sku, body.class_id)
    return {"ok": True}


@router.get("/orders/{number}")
def order(number: int, request: Request):
    return request.app.state.pipeline.packing.order(number)


@router.put("/orders/{number}/items/{item_id}/complete")
def complete(number: int, item_id: str, request: Request):
    return request.app.state.pipeline.packing.complete(number, item_id)


@router.put("/orders/{number}/pause")
def pause(number: int, body: Pause, request: Request):
    return request.app.state.pipeline.packing.pause(number, body.paused)


@router.put("/orders/124")
def receive(request: Request):
    return request.app.state.pipeline.packing.receive_example()
