import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from app.packing import Packing
from app.store import Store


def make_v1(root):
    with closing(sqlite3.connect(root / "metadata.sqlite3")) as db, db:
        db.executescript((Path(__file__).parent / "fixtures/schema_v1.sql").read_text())
        db.execute("INSERT INTO customers VALUES (7,'구매자')")
        db.execute("INSERT INTO products VALUES ('sku-x','생수','개',8,'물','A')")
        db.execute("INSERT INTO orders VALUES ('old-order',123,7,0,'packing')")
        db.executemany("INSERT INTO order_items VALUES (?,'old-order',?,'sku-x',?)",
                       [('packed', 0, 1), ('pending', 1, 0)])


def test_v1_keys_preserve_order_and_stock(tmp_path):
    make_v1(tmp_path)
    with closing(Store(tmp_path)) as store:
        order = store.get("packing_order", "old-order")
        assert order["customer"] == "구매자"
        assert [(i["id"], i["done"]) for i in order["items"]] == [("packed", True), ("pending", False)]
        db = store.db
        assert tuple(db.execute("SELECT stock,reserved,available FROM inventory").fetchone()) == (8, 1, 7)
        assert db.execute("SELECT customer_id FROM orders").fetchone()[0] == 7
        links = db.execute("SELECT i.id,i.order_id,i.product_id FROM order_items i "
                           "JOIN orders o ON o.id=i.order_id JOIN products p ON p.id=i.product_id "
                           "WHERE o.public_id='old-order' AND p.sku='sku-x'").fetchall()
        assert len(links) == 2 and all(type(n) is int for row in links for n in row)
        item_ids = dict(db.execute("SELECT public_id,id FROM order_items"))
        order["items"].reverse()
        store.put("packing_order", order)
        assert dict(db.execute("SELECT public_id,id FROM order_items")) == item_ids
        assert store.get("packing_order", "old-order")["items"] == order["items"]
    with closing(Store(tmp_path)) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert dict(store.db.execute("SELECT public_id,id FROM order_items")) == item_ids
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    backups = list((tmp_path / "backups").glob("metadata-v1-*.sqlite3"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as original:
        assert original.execute("SELECT id FROM order_items ORDER BY position").fetchall() == [("packed",), ("pending",)]


def test_v1_migration_retries_after_rollback(tmp_path):
    make_v1(tmp_path)
    with closing(sqlite3.connect(tmp_path / "metadata.sqlite3")) as db, db:
        db.execute("ALTER TABLE products ADD COLUMN unexpected TEXT")
    with pytest.raises(ValueError, match="products.*unexpected"):
        Store(tmp_path)
    with closing(sqlite3.connect(tmp_path / "metadata.sqlite3")) as db, db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT sku FROM order_items WHERE id='pending'").fetchone()[0] == "sku-x"
        db.execute("ALTER TABLE products DROP COLUMN unexpected")
    with closing(Store(tmp_path)) as store:
        assert store.get("packing_order", "old-order")["number"] == 123


def test_v2_upgrade_preserves_existing_order_customer_and_backs_up(tmp_path):
    with closing(Store(tmp_path)) as store:
        store.put("product", legacy_product())
        store.put("packing_order", {"id":"sample","number":55,"customer":"기존 사용자","state":"received",
                                    "paused":False,"items":[]})
        with store.db:
            store.db.execute("DROP TABLE customer_rfid")
            store.db.execute("PRAGMA user_version=2")
    with closing(Store(tmp_path)) as store:
        assert store.get("packing_order", "sample")["customer"] == "기존 사용자"
        assert store.db.execute("SELECT count(*) FROM customer_rfid").fetchone()[0] == 0
        assert len(list((tmp_path / "backups").glob("metadata-v2-*.sqlite3"))) == 1


def test_generated_keys_and_integer_references(tmp_path):
    with closing(Store(tmp_path)) as store:
        db = store.db
        for table, ddl in db.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            columns = {r["name"]: r["type"] for r in db.execute(f"PRAGMA table_info({table})")}
            if "id" in columns:
                assert "id INTEGER PRIMARY KEY AUTOINCREMENT" in ddl, table
            for fk in db.execute(f"PRAGMA foreign_key_list({table})"):
                assert columns[fk["from"]] == "INTEGER", (table, fk["from"])
        store.put("product", legacy_product())
        first = db.execute("SELECT id FROM products").fetchone()[0]
        store.update("product", "sku-x", stock=7)
        assert db.execute("SELECT id FROM products").fetchone()[0] == first
        with db:
            db.execute("DELETE FROM products WHERE id=?", (first,))

    def create(sku):
        with closing(Store(tmp_path)) as store:
            store.put("product", {**legacy_product(), "id": sku, "sku": sku})
            return store.db.execute("SELECT id FROM products WHERE sku=?", (sku,)).fetchone()[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        generated = list(pool.map(create, ["sku-y", "sku-z"]))
    assert len(set(generated)) == 2 and min(generated) > first


def legacy_product():
    return {"id": "sku-x", "sku": "sku-x", "unit": "개", "name": "생수", "stock": 8, "alias": "물", "bin": "A"}


def make_legacy(root, records):
    db = sqlite3.connect(root / "metadata.sqlite3")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE records(kind TEXT NOT NULL, id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    db.executemany("INSERT INTO records VALUES (?,?,?)", [(k, v["id"], json.dumps(v)) for k, v in records])
    db.commit()
    return db


def test_migration_wal_backup_and_restart(tmp_path):
    product = legacy_product()
    order = Packing.new_order(123, "packing")
    order["items"] = [{"id": "second", "sku": product["sku"], "done": True},
                      {"id": "first", "sku": product["sku"], "done": False}]
    model = {"id": "model-a", "name": "A", "classes": [{"id": 2, "name": "bottle"}], "formats": {}}
    binding = {"id": "binding-a", "model_id": "model-a", "class_id": 2, "label": "bottle", "sku": product["sku"]}
    records = [("product_class", binding), ("packing_order", order), ("product", product), ("model", model)]
    old = make_legacy(tmp_path, records)
    try:
        store = Store(tmp_path)
        try:
            assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3
            assert not store.db.execute("SELECT 1 FROM sqlite_schema WHERE name='records'").fetchone()
            assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
            assert store.get("packing_order", order["id"]) == order
            assert store.get("product_class", binding["id"]) == binding
            assert store.db.execute("SELECT available FROM inventory").fetchone()[0] == 7
            with pytest.raises(sqlite3.IntegrityError), store.db:
                store.db.execute("INSERT INTO order_items(public_id,order_id,position,product_id,done) "
                                 "SELECT 'bad',id,99,-1,0 FROM orders")
            with pytest.raises(sqlite3.IntegrityError), store.db:
                store.db.execute("INSERT INTO product_class_bindings(public_id,model_id,class_id,product_id) "
                                 "SELECT 'duplicate',model_id,class_id,product_id FROM product_class_bindings")
        finally:
            store.close()
    finally:
        old.close()
    store = Store(tmp_path)
    try:
        assert store.list("product") == [product]
        assert store.get("packing_order", order["id"])["items"] == order["items"]
    finally:
        store.close()
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert [(kind, json.loads(data)) for kind, data in db.execute("SELECT kind,data FROM records ORDER BY rowid")] == records


@pytest.mark.parametrize("problem", ["field", "reference"])
def test_migration_failure_rolls_back_and_retries(tmp_path, problem):
    product = legacy_product()
    order = Packing.new_order(123, "packing")
    order["items"] = [{"id":"line", "sku":product["sku"], "done":False}]
    if problem == "field":
        product["unexpected"] = 42
    else:
        order["items"][0]["sku"] = "missing"
    old = make_legacy(tmp_path, [("product", product), ("packing_order", order)])
    try:
        with pytest.raises(ValueError, match="sku-x|line"):
            Store(tmp_path)
        assert old.execute("PRAGMA user_version").fetchone()[0] == 0
        assert old.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall() == [("records",)]
        assert old.execute("SELECT count(*) FROM records").fetchone()[0] == 2
        order["items"][0]["sku"] = product["sku"]
        with old:
            old.execute("UPDATE records SET data=? WHERE kind='product'", (json.dumps(legacy_product()),))
            old.execute("UPDATE records SET data=? WHERE kind='packing_order'", (json.dumps(order),))
    finally:
        old.close()
    store = Store(tmp_path)
    try:
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store.get("packing_order", order["id"]) == order
    finally:
        store.close()
