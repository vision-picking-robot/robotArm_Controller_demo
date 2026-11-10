"""SQLite-backed inventory, class assignments and packing orders."""
from .store import DomainError, now, uid

SEED_PRODUCTS = (
    ("PK-001", "빼빼로", 10), ("PK-002", "생수", 5), ("PK-003", "스팸", 3),
    ("PK-004", "물티슈", 1), ("PK-005", "인형", 2),
)
ORDER_SKUS = ("PK-001", "PK-002", "PK-004")


class Packing:
    def __init__(self, store):
        self.store = store
        with store.write_transaction() as write:
            if not store.list("packing_order"):
                for sku, name, stock in SEED_PRODUCTS:
                    write("product", self.product(sku, name, "개", stock))
                write("packing_order", self.new_order(123, "packing"))
                self.log(write, "주문", "주문 #123 접수 · 빼빼로, 생수, 물티슈 각 1개 예약")
            db = self.store.db
            sample = db.execute("SELECT customer_id FROM orders JOIN customers ON customers.id=customer_id "
                                "WHERE orders.public_id='packing-123' AND customers.name='홍길동'").fetchone()
            if sample:
                db.execute("INSERT OR IGNORE INTO customer_rfid VALUES (?, 'E2-34-08-A1')", (sample[0],))
                second = db.execute("SELECT id FROM customers WHERE name='김서연' ORDER BY id LIMIT 1").fetchone()
                second_id = second[0] if second else db.execute("INSERT INTO customers(name) VALUES ('김서연')").lastrowid
                db.execute("INSERT OR IGNORE INTO customer_rfid VALUES (?, 'E2-34-08-B2')", (second_id,))

    @staticmethod
    def product(sku, name, unit, stock, alias="", bin=""):
        return {"id": sku, "sku": sku, "name": name, "unit": unit, "stock": stock,
                "alias": alias or name, "bin": bin}

    @staticmethod
    def new_order(number, state):
        return {"id": f"packing-{number}", "number": number, "customer": "홍길동", "paused": False,
                "state": state, "items": [{"id": f"{number}-{i}", "sku": sku, "done": False}
                                          for i, sku in enumerate(ORDER_SKUS, 1)]}

    @staticmethod
    def log(write, source, message):
        write("packing_event", {"id": uid("packing-event"), "time": now(), "source": source, "message": message})

    def products(self):
        return [dict(r) for r in self.store.db.execute(
            "SELECT sku AS id,sku,name,unit,stock,alias,bin,reserved,available FROM inventory ORDER BY inventory.id")]

    def users(self):
        return [dict(r) for r in self.store.db.execute("""
            SELECT c.id, c.name, r.uid,
                   EXISTS(SELECT 1 FROM orders WHERE customer_id=c.id AND state='packing') AS packing
            FROM customers c LEFT JOIN customer_rfid r ON r.customer_id=c.id ORDER BY c.id""")]

    def add_user(self, name):
        with self.store.write_transaction() as write:
            user_id = self.store.db.execute("INSERT INTO customers(name) VALUES (?)", (name,)).lastrowid
            self.log(write, "사용자", f"{name} · 사용자 등록")
            return {"id": user_id, "name": name, "uid": None}

    def assign_rfid(self, user_id, uid_value, expected_uid):
        db = self.store.db
        tag = uid_value.upper() if uid_value else None
        with self.store.write_transaction() as write:
            user = db.execute("SELECT c.name,r.uid FROM customers c LEFT JOIN customer_rfid r "
                              "ON r.customer_id=c.id WHERE c.id=?", (user_id,)).fetchone()
            if not user:
                raise DomainError("사용자를 찾을 수 없습니다.", 404)
            if tag == user["uid"]:
                return
            if expected_uid != user["uid"]:
                raise DomainError("다른 화면에서 RFID가 변경되었습니다. 새 값을 확인하세요.")
            if db.execute("SELECT 1 FROM orders WHERE customer_id=? AND state='packing'", (user_id,)).fetchone():
                raise DomainError("패킹 중인 사용자의 RFID는 작업 완료 후 변경할 수 있습니다.")
            if tag and db.execute("SELECT 1 FROM customer_rfid WHERE uid=?", (tag,)).fetchone():
                raise DomainError("이미 다른 사용자에게 할당된 RFID입니다.")
            db.execute("INSERT INTO customer_rfid(customer_id,uid) VALUES (?,?) ON CONFLICT(customer_id) "
                       "DO UPDATE SET uid=excluded.uid", (user_id, tag))
            self.log(write, "사용자", f"{user['name']} · RFID " + (f"{tag} 할당" if tag else "할당 해제"))

    def create_product(self, name, unit, stock, alias, bin):
        with self.store.write_transaction() as write:
            # SKU is an external business code; products.id is assigned by SQLite.
            sku = self.store.db.execute(
                "SELECT printf('PK-%03d',1+coalesce(max(CAST(substr(sku,4) AS INTEGER)),0)) "
                "FROM products WHERE sku GLOB 'PK-[0-9]*' AND substr(sku,4) NOT GLOB '*[^0-9]*'").fetchone()[0]
            product = self.product(sku, name, unit, stock, alias, bin)
            write("product", product)
            self.log(write, "재고", f"{name} 등록 · 보유 {stock}{unit}")
            return product

    def adjust(self, sku, stock, expected_stock):
        with self.store.write_transaction() as write:
            product = self.store.get("product", sku)
            reserved = self.store.db.execute("SELECT reserved FROM inventory WHERE sku=?", (sku,)).fetchone()[0]
            if stock < reserved:
                raise DomainError(f"예약 수량 {reserved}{product['unit']} 이상으로 입력하세요.")
            if product["stock"] != expected_stock:
                raise DomainError("보유 수량이 변경되었습니다. 최신 수량을 확인한 후 다시 조정하세요.")
            if stock != product["stock"]:
                write("product", {**product, "stock": stock})
                self.log(write, "재고", f"{product['name']} 보유 수량 조정 · {stock}{product['unit']}")

    def order(self, number):
        return self.store.get("packing_order", f"packing-{number}")

    def complete(self, number, item_id):
        with self.store.write_transaction() as write:
            order = self.order(number)
            item = next((i for i in order["items"] if i["id"] == item_id), None)
            if item is None:
                raise DomainError("주문에 없는 품목입니다.", 404)
            if item["done"]:
                return order
            if order["state"] != "packing" or order["paused"]:
                raise DomainError("현재 포장을 진행할 수 없습니다.")
            if next(i for i in order["items"] if not i["done"]) != item:
                raise DomainError("화면에 표시된 다음 품목부터 포장하세요.")
            product = self.store.get("product", item["sku"])
            if product["stock"] < 1:
                raise DomainError("보유 수량이 부족합니다.")
            order_key = self.store.relations.keys.local("orders", order["id"])
            self.store.db.execute("UPDATE order_items SET done=1 WHERE order_id=? AND public_id=? AND done=0",
                                  (order_key, item_id))
            self.store.db.execute("UPDATE products SET stock=stock-1 WHERE sku=?", (item["sku"],))
            self.log(write, "패킹", f"주문 #{number} · {product['name']} 1{product['unit']} 놓기 완료 · 출고 반영")
            if not self.store.db.execute("SELECT 1 FROM order_items WHERE order_id=? AND done=0 LIMIT 1",
                                         (order_key,)).fetchone():
                self.store.db.execute("UPDATE orders SET state='awaiting_dispatch' WHERE id=?", (order_key,))
                self.log(write, "배송", f"주문 #{number} 포장 완료 · 발송 대기")
            return self.order(number)

    def pause(self, number, paused):
        with self.store.write_transaction() as write:
            order = self.order(number)
            if order["state"] == "packing" and order["paused"] != paused:
                order["paused"] = paused
                self.store.db.execute("UPDATE orders SET paused=? WHERE public_id=?", (paused, order["id"]))
                self.log(write, "패킹", f"주문 #{number} 작업 " + ("정지" if paused else "재개"))
            return order

    def receive_example(self):
        with self.store.write_transaction() as write:
            if not any(o["number"] == 124 for o in self.store.list("packing_order")):
                write("packing_order", self.new_order(124, "received"))
                self.log(write, "주문", "주문 #124 접수 · 박스 판독 대기")
            return self.order(124)

    def model_classes(self, model_id):
        model = self.store.db.execute(
            "SELECT m.public_id AS id,coalesce(a.name,m.name,m.public_id) AS name FROM models m "
            "LEFT JOIN model_aliases a ON a.model_id=m.id WHERE m.public_id=?", (model_id,)).fetchone()
        if model is None:
            raise DomainError("model: 항목을 찾을 수 없습니다", 404)
        classes = self.store.db.execute(
            "SELECT c.class_id AS id,c.name,p.sku FROM model_classes c "
            "LEFT JOIN product_class_bindings b USING(model_id,class_id) "
            "LEFT JOIN products p ON p.id=b.product_id "
            "WHERE c.model_id=(SELECT id FROM models WHERE public_id=?) ORDER BY c.position", (model_id,))
        return {**dict(model), "classes": [dict(c) for c in classes]}

    def assign(self, model_id, sku, class_id):
        with self.store.write_transaction() as write:
            product = self.store.get("product", sku)
            classes = self.model_classes(model_id)["classes"]
            if class_id is not None:
                target = next((c for c in classes if c["id"] == class_id), None)
                if target is None:
                    raise DomainError("모델에 등록된 클래스를 선택하세요.", 422)
                if target["sku"] and target["sku"] != sku:
                    other = self.store.get("product", target["sku"])
                    raise DomainError(f"이미 {other['name']}에 연결된 클래스입니다.")
            previous = next((c["id"] for c in classes if c["sku"] == sku), None)
            if previous == class_id:
                return
            self.store.db.execute(
                "DELETE FROM product_class_bindings WHERE model_id=(SELECT id FROM models WHERE public_id=?) "
                "AND product_id=(SELECT id FROM products WHERE sku=?)", (model_id, sku))
            if class_id is not None:
                write("product_class", {"id": uid("binding"), "model_id": model_id, "class_id": class_id,
                                        "label": target["name"], "sku": sku})
            action = f"{target['name']} 연결" if class_id is not None else "클래스 연결 해제"
            self.log(write, "모델", f"{product['name']} ({sku}) · {action} · {model_id}")

    def snapshot(self, model_id):
        with self.store.read_transaction():
            orders = self.store.list("packing_order")
            owners = dict(self.store.db.execute("SELECT number,customer_id FROM orders"))
            for order in orders:
                order["customer_id"] = owners[order["number"]]
            return {"users": self.users(), "products": self.products(), "orders": orders, "model": self.model_classes(model_id),
                    "events": self.store.list("packing_event")}

    def logs(self):
        # Read historical training and deployment records alongside packing activity.
        with self.store.read_transaction():
            return [dict(row) for row in self.store.db.execute("""
                SELECT time,source,message FROM packing_events
                UNION ALL SELECT created_at,'모델',name || ' · 학습 클래스 등록' FROM training_classes
                UNION ALL SELECT created_at,'모델',public_id || ' · 학습 시작 요청' FROM training_jobs
                UNION ALL SELECT ended_at,'모델',public_id || ' · 학습 결과 · ' || state FROM training_jobs
                    WHERE ended_at IS NOT NULL
                UNION ALL SELECT created_at,'모델',name || ' · 학습 모델 등록' FROM models WHERE kind='custom'
                UNION ALL SELECT coalesce(o.ended_at,o.created_at),'모델',
                    coalesce(a.name,m.name,m.public_id) || ' · 모델 적용 · ' || o.state
                    FROM model_operations o JOIN models m ON m.id=o.model_id LEFT JOIN model_aliases a ON a.model_id=m.id
                ORDER BY time DESC""") if row["time"]]
