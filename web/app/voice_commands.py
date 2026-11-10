"""Packing actions shared by dashboard buttons and final Korean transcripts."""
import re
from dataclasses import dataclass
from unicodedata import normalize

from .packing import ORDER_SKUS

PHRASES = {
    "query-stock": ("재고 파악해줘", "재고 알려줘", "재고도 알려줘", "재고 조회", "재고 조회해줘"),
    "query-order": ("주문 내역 알려줘", "주문 내역도 알려줘", "주문 조회", "주문 조회해줘"),
    "cmd-stop": ("멈춰", "피킹 멈춰", "피킹 일시정지", "피킹 정지해줘", "정지"),
    "cmd-go": ("피킹 시작해줘", "피킹 재개해줘", "피킹 재개", "시작", "피킹 시작"),
    "cmd-pick": ("집어", "피킹", "상품 집어", "물건 집어", "집기"),
    "cmd-order": ("빼빼로 생수 물티슈 하나씩 주문해줘", "예시 주문해줘", "예시 주문"),
    "confirm-order": ("주문 확정", "네 주문해줘"),
    "discard-order": ("취소", "주문 취소"),
    "end": ("대화 종료", "대화 끝"),
}


def normalized(text):
    return re.sub(r"[^가-힣a-z0-9]", "", normalize("NFKC", text).lower())


INTENTS = {normalized(phrase): intent for intent, phrases in PHRASES.items() for phrase in phrases}
# A spoken acknowledgment commonly starts the next query after Yuna finishes.
for query in ("query-stock", "query-order"):
    for phrase in PHRASES[query]:
        for acknowledgment in ("응", "네", "어"):
            INTENTS[normalized(acknowledgment + phrase)] = query


@dataclass(frozen=True)
class Answer:
    text: str
    confirm: bool = False


def packing_answer(packing, intent, *, confirming=False):
    if intent == "confirm-order":
        if not confirming:
            return Answer("확인할 주문이 없습니다.")
        packing.receive_example()
        return Answer("124번 주문을 접수했습니다.")
    if intent == "discard-order":
        return Answer("추가 주문을 취소했습니다." if confirming else "취소할 주문이 없습니다.")
    if intent in ("cmd-stop", "cmd-go"):
        order = packing.pause(123, intent == "cmd-stop")
        if order["state"] == "awaiting_dispatch":
            return Answer("123번 주문은 발송 대기 중입니다.")
        return Answer("123번 주문의 피킹을 정지했습니다." if order["paused"] else "123번 주문의 피킹을 재개합니다.")
    if intent == "cmd-pick":
        with packing.store.read_transaction():
            products = {product["sku"]: product for product in packing.products()}
            order = packing.order(123)
            next_item = next((i for i in order["items"] if not i["done"]), None)
            if next_item:
                pname = products.get(next_item["sku"], {}).get("name", next_item["sku"])
                return Answer(f"123번 주문의 {pname} 상품을 집어(피킹) 작업을 시작합니다.")
            return Answer("123번 주문에서 더 이상 집을 상품이 없습니다.")
    if intent not in ("query-stock", "query-order", "cmd-order", "cmd-pick"):
        return Answer("재고 조회, 주문 조회, 피킹 일시정지나 재개, 예시 주문 중에서 말씀해 주세요.")
    with packing.store.read_transaction():
        products = {product["sku"]: product for product in packing.products()}
        if intent == "query-stock":
            quantities = [f"{p['name']} {p['available']}{p['unit']}" for p in products.values()]
            return Answer(", ".join(quantities) + " 주문 가능합니다.")
        order = packing.order(123)
        received = any(o["number"] == 124 for o in packing.store.list("packing_order"))
        if intent == "cmd-order":
            if received:
                return Answer("같은 예시 주문이 124번으로 접수되어 있습니다.")
            names = ", ".join(products[sku]["name"] + " 한 개" for sku in ORDER_SKUS)
            return Answer(f"{order['customer']}님, {names}를 주문할까요? 주문 확정 또는 취소라고 말씀해 주세요.", True)
        names = ", ".join(products[i["sku"]]["name"] + " 한 개" for i in order["items"])
        done = sum(i["done"] for i in order["items"])
        status = "발송 대기" if order["state"] == "awaiting_dispatch" else ("일시정지" if order["paused"] else "피킹 중")
        extra = " 추가 주문 124번은 접수 상태입니다." if received else ""
        return Answer(f"{order['customer']}님의 123번 주문은 {names}입니다. 현재 {done}개를 담았습니다. {status} 상태입니다." + extra)
