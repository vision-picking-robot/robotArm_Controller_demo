from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from .store import DomainError
from .voice_commands import packing_answer


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class ButtonCommand(Input):
    action: Literal["query-stock", "query-order", "cmd-stop", "cmd-go", "cmd-order", "confirm-order", "discard-order"]


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


router = APIRouter(prefix="/api/packing")


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
