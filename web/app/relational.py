"""Translate the existing API documents to and from relational rows."""
import json

from .keys import Keys


TABLES = {
    "class": "training_classes", "group": "capture_groups", "model": "models",
    "model_alias": "model_aliases", "setting": "active_models", "operation": "model_operations",
    "collection": "collections", "sample": "samples", "dataset": "datasets", "job": "training_jobs",
    "product": "products", "packing_order": "orders", "product_class": "product_class_bindings",
    "packing_event": "packing_events",
}
REPORT_FIELDS = {"metrics", "latency", "runtime", "environment", "training", "evaluation",
                 "export_options", "progress"}
BOX = ("left_x", "top_y", "right_x", "bottom_y")
NORMALIZED_BOX = ("center_x", "center_y", "box_width", "box_height")

def unpack(row):
    value = dict(row)
    value.pop("_ordinal", None)
    for key in REPORT_FIELDS.intersection(value):
        if value[key] is not None:
            value[key] = json.loads(value[key])
    return {k: v for k, v in value.items() if v is not None}


class Relations:
    def __init__(self, db):
        self.db = db
        self.keys = Keys(db)
        sources = self.keys.sources
        self.reads = {kind: f"SELECT t.* FROM {sources[table]} t" for kind, table in TABLES.items()}
        self.reads.update({
            "setting": f"SELECT t.*, 'selected-'||t.backend AS id FROM {sources['active_models']} t",
            "product": f"SELECT t.*, t.sku AS id FROM {sources['products']} t",
            "collection": f"""SELECT t.*, c.name AS class_name,
                (SELECT count(*) FROM samples s WHERE s.collection_id=t._ordinal) AS saved
                FROM {sources['collections']} t JOIN training_classes c ON c.public_id=t.class_id""",
            "sample": f"""SELECT t.*, c.class_id,c.group_id,c.model_id AS teacher_model,c.model_hash AS teacher_hash
                FROM {sources['samples']} t JOIN {sources['collections']} c ON c.id=t.collection_id""",
            "packing_order": f"""SELECT t.*,c.name AS customer FROM {sources['orders']} t
                JOIN customers c ON c.id=t.customer_id""",
            "product_class": f"""SELECT t.*,c.name AS label FROM {sources['product_class_bindings']} t
                JOIN {sources['model_classes']} c ON c.model_id=t.model_id AND c.class_id=t.class_id"""
        })

    def save_row(self, table, value, keys=("id",)):
        encoded = dict(value)
        for key in REPORT_FIELDS.intersection(encoded):
            if encoded[key] is not None:
                encoded[key] = json.dumps(encoded[key], ensure_ascii=False, allow_nan=False)
        data = self.keys.physical_row(table, encoded)
        fields = self.keys.fields[table]
        identity = [fields[key].name for key in keys]
        where = " AND ".join(f"{column}=?" for column in identity)
        key_values = [data[k] for k in identity]
        # Update an existing row in place so frequent status writes do not consume IDs.
        changes = [k for k in data if k not in identity]
        updated = self.db.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in changes) +
                                  " WHERE " + where, [data[k] for k in changes] + key_values)
        if not updated.rowcount:
            self.db.execute(f"INSERT INTO {table} ({','.join(data)}) VALUES ({','.join('?' for _ in data)})",
                            list(data.values()))

    def write(self, kind, value):
        row = dict(value)
        identifier = row["id"]
        if kind == "product":
            if row.pop("id") != row["sku"]:
                raise ValueError("Product ID does not match SKU")
            self.save_row("products", row, ("sku",))
        elif kind == "setting":
            if not identifier.startswith("selected-") or not identifier[9:]:
                raise ValueError(f"Unsupported backend selection {identifier}")
            del row["id"]
            self.save_row("active_models", {**row, "backend": identifier[9:]}, ("backend",))
        elif kind == "product_class":
            label = row.pop("label")
            known = self.db.execute("SELECT name FROM model_classes WHERE model_id=? AND class_id=?",
                                    (self.keys.local("models", row["model_id"]), row["class_id"])).fetchone()
            if not known or known[0] != label:
                raise ValueError(f"{identifier}: unknown/changed class {label}")
            self.save_row("product_class_bindings", row)
        elif kind == "packing_order":
            customer = row.pop("customer")
            found = self.db.execute("SELECT id FROM customers WHERE name=? ORDER BY id LIMIT 1", (customer,)).fetchone()
            row["customer_id"] = found[0] if found else self.db.execute(
                "INSERT INTO customers(name) VALUES (?)", (customer,)).lastrowid
            items = row.pop("items")
            self.save_row("orders", row)
            self.write_items(identifier, items)
        elif kind == "collection":
            skipped = row.pop("skipped", {})
            row.pop("saved", None)
            row.pop("class_name", None)
            self.save_row("collections", row)
            self.db.execute("DELETE FROM collection_skips WHERE collection_id=?", (self.keys.local("collections", identifier),))
            for reason, count in skipped.items():
                self.save_row("collection_skips", {"collection_id": identifier, "reason": reason, "count": count},
                              ("collection_id", "reason"))
        elif kind == "sample":
            for key in ("class_id", "group_id", "teacher_model", "teacher_hash"):
                row.pop(key, None)
            prediction = dict(row.pop("prediction"))
            box = row.pop("box")
            if prediction.pop("xyxy") != box:
                raise ValueError(f"{identifier}: sample box disagrees with prediction")
            row.update(zip(BOX, box, strict=True))
            row.update(zip(NORMALIZED_BOX, row.pop("normalized_box"), strict=True))
            row.update(predicted_class_id=prediction.pop("class_id"), predicted_label=prediction.pop("label"),
                       confidence=prediction.pop("confidence"))
            if prediction:
                raise ValueError(f"{identifier}: unsupported prediction attributes {list(prediction)}")
            self.save_row("samples", row)
        elif kind == "dataset":
            self.write_dataset(row)
        elif kind == "model":
            self.write_model(row)
        elif kind == "job":
            snapshot = row.pop("dataset", None)
            if snapshot and snapshot.get("id") != row.get("dataset_id"):
                raise ValueError(f"{identifier}: inconsistent dataset reference")
            self.save_row("training_jobs", row)
        else:
            self.save_row(TABLES[kind], row)

    def write_items(self, order, items):
        order_id = self.keys.local("orders", order)
        existing = dict(self.db.execute("SELECT public_id,id FROM order_items WHERE order_id=?", (order_id,)))
        identifiers = [i["id"] for i in items]
        if len(set(identifiers)) != len(items):
            raise ValueError(f"{order}: duplicate item identifiers")
        # Free old positions for reordering without deleting/reissuing retained item IDs.
        self.db.execute("UPDATE order_items SET position=-position-1 WHERE order_id=?", (order_id,))
        for removed in existing.keys() - set(identifiers):
            self.db.execute("DELETE FROM order_items WHERE id=?", (existing[removed],))
        for position, item in enumerate(items):
            if item.keys() != {"id", "sku", "done"}:
                raise ValueError(f"{order}: invalid order item fields {list(item)}")
            try:
                product_id = self.keys.local("products", item["sku"])
            except ValueError as exc:
                raise ValueError(f"{order} item {item['id']}: {exc}") from exc
            if item["id"] in existing:
                self.db.execute("UPDATE order_items SET position=?, product_id=?, done=? WHERE id=?",
                                (position, product_id, item["done"], existing[item["id"]]))
            else:
                self.db.execute("INSERT INTO order_items(public_id,order_id,position,product_id,done) VALUES (?,?,?,?,?)",
                                (item["id"], order_id, position, product_id, item["done"]))

    def write_dataset(self, row):
        identifier = row["id"]
        # Dataset membership is frozen; existing rows may only be replayed identically.
        if self.db.execute("SELECT 1 FROM datasets WHERE public_id=?", (identifier,)).fetchone():
            if self.read("dataset", identifier)[0] != row:
                raise ValueError(f"{identifier}: cannot replace an immutable dataset")
            return
        items = row.pop("items")
        classes = row.pop("classes")
        splits = row.pop("split_sha256", {})
        counts = row.pop("counts")
        if counts != {s: sum(i["split"] == s for i in items) for s in ("train", "val")}:
            raise ValueError(f"{identifier}: inconsistent split counts")
        self.save_row("datasets", row)
        for position, c in enumerate(classes):
            fields = dict(c)
            fields["class_id"] = fields.pop("id")
            self.save_row("dataset_classes", {**fields, "dataset_id": identifier, "position": position},
                          ("dataset_id", "class_id"))
        for position, item in enumerate(items):
            self.save_row("dataset_items", {**item, "dataset_id": identifier, "position": position},
                          ("dataset_id", "position"))
        for split, digest in splits.items():
            self.save_row("dataset_splits", {"dataset_id": identifier, "split": split, "sha256": digest},
                          ("dataset_id", "split"))

    def write_model(self, row):
        identifier = row["id"]
        classes = row.pop("classes", [])
        formats = row.pop("formats", {})
        row.pop("dataset_counts", None)
        self.keys.reserve_model(identifier)
        self.save_row("models", row)
        model_id = self.keys.local("models", identifier)
        # Keep existing bindings only when the ID still denotes the same class.
        new_names = {c["id"]: c["name"] for c in classes}
        for c in self.db.execute("SELECT class_id,name FROM model_classes WHERE model_id=?", (model_id,)).fetchall():
            if new_names.get(c["class_id"]) != c["name"]:
                self.db.execute("DELETE FROM model_classes WHERE model_id=? AND class_id=?", (model_id, c["class_id"]))
        for position, c in enumerate(classes):
            fields = dict(c)
            fields["class_id"] = fields.pop("id")
            self.save_row("model_classes", {**fields, "model_id": identifier, "position": position},
                          ("model_id", "class_id"))
        self.db.execute("DELETE FROM model_formats WHERE model_id=?", (model_id,))
        for backend, fmt in formats.items():
            self.save_row("model_formats", {**fmt, "backend": backend, "model_id": identifier}, ("model_id", "backend"))

    def read(self, kind, identifier=None):
        query = self.reads[kind]
        if identifier is None:
            rows = self.db.execute(query + " ORDER BY t._ordinal")
        elif kind == "setting":
            rows = self.db.execute(query + " WHERE 'selected-'||t.backend=?", (identifier,))
        else:
            key = "sku" if kind == "product" else "id"
            rows = self.db.execute(query + f" WHERE t.{key}=?", (identifier,))
        return [self.expand(kind, unpack(r)) for r in rows]

    def children(self, table, relation, identifier, order="_ordinal"):
        parent = self.keys.fields[table][relation]
        parent_id = self.keys.local(parent.target, identifier)
        sort_column = "rowid" if order == "_ordinal" else order
        return [unpack(r) for r in self.db.execute(
            f"{self.keys.selects[table]} WHERE t.{parent.name}=? ORDER BY t.{sort_column}", (parent_id,))]

    def class_list(self, table, relation, identifier):
        result = []
        for c in self.children(table, relation, identifier, "position"):
            del c[relation], c["position"]
            c["id"] = c.pop("class_id")
            result.append(c)
        return result

    def counts(self, dataset_id):
        values = {"train": 0, "val": 0}
        values.update(self.db.execute("SELECT s.code,count(*) FROM dataset_items i JOIN split_types s ON s.id=i.split_id "
                                      "WHERE i.dataset_id=? GROUP BY s.id", (self.keys.local("datasets", dataset_id),)).fetchall())
        return values

    def expand(self, kind, row):
        identifier = row["id"]
        if kind == "setting":
            del row["backend"]
        elif kind == "packing_order":
            del row["customer_id"]
            row["paused"] = bool(row["paused"])
            items = self.children("order_items", "order_id", identifier, "position")
            for item in items:
                del item["order_id"], item["position"]
                item["done"] = bool(item["done"])
            row["items"] = items
        elif kind == "collection":
            row.setdefault("model_hash", None)
            row["skipped"] = {r["reason"]: r["count"] for r in self.children("collection_skips", "collection_id", identifier)}
            row.setdefault("error", None)
        elif kind == "sample":
            row.setdefault("teacher_hash", None)
            box = [row.pop(k) for k in BOX]
            row["normalized_box"] = [row.pop(k) for k in NORMALIZED_BOX]
            row["box"] = box
            row["prediction"] = {"class_id": row.pop("predicted_class_id"), "label": row.pop("predicted_label"),
                                 "confidence": row.pop("confidence"), "xyxy": list(box)}
        elif kind == "operation" and row.get("state") == "failed":
            row.setdefault("restored_model_id", None)
        elif kind == "dataset":
            row["classes"] = self.class_list("dataset_classes", "dataset_id", identifier)
            row["items"] = self.children("dataset_items", "dataset_id", identifier, "position")
            for item in row["items"]:
                del item["position"], item["dataset_id"]
            row["counts"] = self.counts(identifier)
            hashes = {r["split"]: r["sha256"] for r in self.children("dataset_splits", "dataset_id", identifier)}
            if hashes:
                row["split_sha256"] = hashes
        elif kind == "job":
            row["dataset"] = self.read("dataset", row["dataset_id"])[0]
            for key in ("error", "initial_model_id", "warning"):
                row.setdefault(key, None)
        elif kind == "model":
            row["classes"] = self.class_list("model_classes", "model_id", identifier)
            row["formats"] = {}
            for fmt in self.children("model_formats", "model_id", identifier):
                if row.get("kind") == "configured":
                    fmt.setdefault("sha256", None)
                del fmt["model_id"]
                row["formats"][fmt.pop("backend")] = fmt
            if row.get("kind") == "custom":
                for key in ("initial_model_id", "parent_model_id", "family_id", "version_number"):
                    row.setdefault(key, None)
            if row.get("dataset_id"):
                row["dataset_counts"] = self.counts(row["dataset_id"])
        return row
