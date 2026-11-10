"""Translate stable API identifiers at the database boundary.

All persisted relationships use SQLite integers. A public identifier is only
looked up at an input/output boundary, including when importing a v1 row.
"""
from dataclasses import dataclass

LOOKUPS = {"inference_backends": "code", "capture_sessions": "token", "skip_reasons": "code", "split_types": "code"}
PUBLIC_KEYS = {**LOOKUPS, "products": "sku"}
TARGETS = {
    "model_id": "models", "parent_model_id": "models", "initial_model_id": "models",
    "restored_model_id": "models", "family_id": "models", "job_id": "training_jobs",
    "dataset_id": "datasets", "order_id": "orders", "product_id": "products",
    "collection_id": "collections", "sample_id": "samples", "group_id": "capture_groups",
    "training_class_id": "training_classes", "session_id": "capture_sessions",
    "backend_id": "inference_backends", "reason_id": "skip_reasons", "split_id": "split_types",
}
WIRE_NAMES = {"public_id": "id", "product_id": "sku", "backend_id": "backend", "split_id": "split",
              "reason_id": "reason", "planned_model_public_id": "model_id"}


@dataclass(frozen=True)
class Column:
    name: str
    wire: str
    target: str | None


class Keys:
    def __init__(self, db):
        self.db = db
        self.fields = {}
        self.selects = {}
        self.sources = {}
        for (table,) in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            names = [r["name"] for r in db.execute(f'PRAGMA table_info("{table}")')]
            fields = {}
            expressions = []
            for name in names:
                if name == "id" and ("public_id" in names or table == "products"):
                    continue
                target = TARGETS.get(name)
                wire = WIRE_NAMES.get(name, name)
                if name == "training_class_id":
                    wire = "class_uuid" if table == "dataset_items" else "uuid"
                if table == "collections" and name == "class_id":
                    target = "training_classes"
                fields[wire] = Column(name, wire, target)
                expression = f't.{name}'
                if target:
                    public = PUBLIC_KEYS.get(target, "public_id")
                    expression = f'(SELECT {public} FROM {target} WHERE id=t.{name})'
                expressions.append(f'{expression} AS "{wire}"')
            self.fields[table] = fields
            self.selects[table] = f'SELECT {", ".join(expressions)}, t.rowid AS _ordinal FROM {table} t'
            self.sources[table] = f'({self.selects[table]})'

    def local(self, table, value, create=False):
        if value is None:
            return None
        public = PUBLIC_KEYS.get(table, "public_id")
        found = self.db.execute(f"SELECT id FROM {table} WHERE {public}=?", (value,)).fetchone()
        if found:
            return found[0]
        if create and table in LOOKUPS:
            return self.db.execute(f"INSERT INTO {table}({public}) VALUES (?)", (value,)).lastrowid
        raise ValueError(f"Unknown {table}.{public}: {value}")

    def reserve_model(self, public_id):
        # Lineage can reference the model itself or another model from this import batch.
        self.db.execute("INSERT INTO models(public_id) SELECT ? WHERE NOT EXISTS "
                        "(SELECT 1 FROM models WHERE public_id=?)", (public_id, public_id))

    def physical_row(self, table, row):
        fields = self.fields[table]
        unknown = row.keys() - fields.keys()
        if unknown:
            raise ValueError(f"{table}: unsupported columns {sorted(unknown)}")
        result = {}
        for wire, col in fields.items():
            value = row.get(wire)
            result[col.name] = self.local(col.target, value, create=True) if col.target else value
        return result
