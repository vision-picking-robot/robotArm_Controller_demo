"""Upgrade document and relational metadata transactionally to database-generated integer keys."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .relational import Relations, TABLES

VERSION = 3
# Preserve v0 insertion order within each entity. Resolve class names before bindings.
ORDER = ("class", "group", "collection", "sample", "dataset", "job", "model", "setting",
         "model_alias", "operation", "product", "packing_order", "packing_event", "product_class")


def check_database(db):
    for table, rowid, parent, constraint in db.execute("PRAGMA foreign_key_check"):
        row = dict(db.execute(f'SELECT * FROM "{table}" WHERE rowid=?', (rowid,)).fetchone())
        raise ValueError(f"{table} row {row.get('id') or row}: missing {parent} reference (FK {constraint})")
    result = [r[0] for r in db.execute("PRAGMA integrity_check")]
    if result != ["ok"]:
        raise ValueError(f"SQLite integrity_check: {result}")


def backup(root, db, version):
    directory = root / "backups"
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"metadata-v{version}-{stamp}-{uuid4().hex[:8]}.sqlite3"
    # db already owns the writer reservation. A separate reader can back up committed WAL pages.
    source = sqlite3.connect((root / "metadata.sqlite3").as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(path)
    try:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError(f"Invalid backup: {path}")
        if target.execute("PRAGMA user_version").fetchone()[0] != version:
            raise ValueError(f"Unexpected schema in backup: {path}")
        if version == 0:
            original = db.execute("SELECT kind,id,data FROM records ORDER BY rowid")
            saved = target.execute("SELECT kind,id,data FROM records ORDER BY rowid")
            if any(tuple(a) != b for a, b in zip(original, saved, strict=True)):
                raise ValueError(f"Backup differs from source: {path}")
    finally:
        source.close()
        target.close()
    return path


def migrate_records(db, repository):
    kinds = {r[0] for r in db.execute("SELECT DISTINCT kind FROM records")}
    unknown = kinds - set(TABLES)
    if unknown:
        kind = sorted(unknown)[0]
        identifier = db.execute("SELECT id FROM records WHERE kind=? LIMIT 1", (kind,)).fetchone()[0]
        raise ValueError(f"Unsupported legacy record {kind} {identifier}")
    db.execute("PRAGMA defer_foreign_keys=ON")
    for (identifier,) in db.execute("SELECT id FROM records WHERE kind='model' ORDER BY rowid"):
        repository.keys.reserve_model(identifier)
    for kind in ORDER:
        for identifier, data in db.execute("SELECT id,data FROM records WHERE kind=? ORDER BY rowid", (kind,)):
            try:
                doc = json.loads(data)
                if doc["id"] != identifier:
                    raise ValueError("payload ID differs from primary key")
                repository.write(kind, doc)
            except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                raise ValueError(f"Cannot migrate {kind} {identifier}: {exc}") from exc
    check_database(db)
    for kind in ORDER:
        old = db.execute("SELECT id,data FROM records WHERE kind=? ORDER BY rowid", (kind,))
        # Reconstruct every legacy response including group membership, array order,
        # hashes, job snapshots and report contents before removing the source table.
        restored = {doc["id"]: doc for doc in repository.read(kind)}
        for identifier, raw in old:
            doc = restored.pop(identifier)
            expected = json.loads(raw)
            for key, value in expected.items():
                if key not in doc or doc[key] != value:
                    raise ValueError(f"Migration differs at {kind} {identifier}.{key}")
        if restored:
            raise ValueError(f"Unexpected {kind} IDs after migration: {sorted(restored)}")


V1_TABLES = (
    "customers", "products", "training_classes", "capture_groups", "collections",
    "samples", "collection_skips", "datasets", "dataset_splits", "dataset_classes",
    "dataset_items", "training_jobs", "models", "model_classes", "model_formats",
    "model_operations", "model_aliases", "active_models", "orders", "order_items",
    "product_class_bindings", "packing_events",
)


def install_schema(db):
    statement = ""
    for line in Path(__file__).with_name("schema.sql").read_text().splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("Incomplete schema definition")
    return Relations(db)


def migrate_v1(db, old):
    old.row_factory = sqlite3.Row
    check_database(old)
    tables = {t for t, in old.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables != set(V1_TABLES):
        raise ValueError(f"Unexpected v1 tables: {tables.symmetric_difference(V1_TABLES)}")
    expected_objects = {"inventory", "samples_collection", "samples_review", "order_items_sku"}
    objects = {n for n, in old.execute("SELECT name FROM sqlite_schema WHERE type != 'table' AND sql IS NOT NULL")}
    if objects - expected_objects:
        raise ValueError(f"Custom schema objects need explicit migration: {objects - expected_objects}")
    db.execute("DROP VIEW inventory")
    for table in reversed(V1_TABLES):
        db.execute(f"DROP TABLE {table}")
    repository = install_schema(db)
    # A model can be its own family and refer to the job that publishes it.
    for (public_id,) in old.execute("SELECT id FROM models ORDER BY rowid"):
        repository.keys.reserve_model(public_id)
    for table in V1_TABLES:
        old_columns = {r["name"] for r in old.execute(f"PRAGMA table_info({table})")}
        differences = old_columns.symmetric_difference(repository.keys.fields[table])
        if differences:
            raise ValueError(f"Unexpected v1 columns in {table}: {sorted(differences)}")
        for row in old.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            try:
                data = repository.keys.physical_row(table, dict(row))
                if table == "models":
                    public = data.pop("public_id")
                    db.execute("UPDATE models SET " + ",".join(f"{k}=?" for k in data) + " WHERE public_id=?",
                               [*data.values(), public])
                else:
                    db.execute(f"INSERT INTO {table} ({','.join(data)}) VALUES ({','.join('?' for _ in data)})",
                               list(data.values()))
            except (ValueError, sqlite3.Error) as exc:
                raise ValueError(f"Cannot migrate {table} row {tuple(row)[:2]}: {exc}") from exc

    # Compare every v1 column through the reverse mapping, including exact report text and file paths.
    for table in V1_TABLES:
        info = list(old.execute(f"PRAGMA table_info({table})"))
        columns = ",".join(r["name"] for r in info)
        order = ",".join(r["name"] for r in sorted(info, key=lambda r: r["pk"]) if r["pk"])
        source = old.execute(f"SELECT {columns} FROM {table} ORDER BY {order}")
        restored = db.execute(f"SELECT {columns} FROM {repository.keys.sources[table]} ORDER BY {order}")
        for left, right in zip(source, restored, strict=True):
            if tuple(left) != tuple(right):
                raise ValueError(f"Migration differs at {table} {tuple(left)[:2]}")
    return repository


def initialize(db, root):
    if db.execute("PRAGMA user_version").fetchone()[0] == VERSION:
        return Relations(db)
    # Rebuilding cyclic FK tables requires disabling enforcement for this connection
    # before BEGIN. Validate the full graph before commit, then enable enforcement again.
    db.execute("PRAGMA foreign_keys=OFF")
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == VERSION:
                return Relations(db)
            if version == 2:
                backup(root, db, version)
                db.execute("CREATE TABLE customer_rfid (customer_id INTEGER PRIMARY KEY REFERENCES customers(id), "
                           "uid TEXT COLLATE NOCASE UNIQUE)")
                repository = Relations(db)
            elif version == 1:
                path = backup(root, db, version)
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as old:
                    repository = migrate_v1(db, old)
            elif version == 0:
                tables = {t for t, in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if tables and tables != {"records"}:
                    raise ValueError(f"Unrecognized v0 schema: {sorted(tables)}")
                if tables:
                    backup(root, db, version)
                repository = install_schema(db)
                if tables:
                    migrate_records(db, repository)
                    db.execute("DROP TABLE records")
            else:
                raise ValueError(f"Unsupported metadata schema version: {version}")
            check_database(db)
            db.execute(f"PRAGMA user_version={VERSION}")
            return repository
    finally:
        db.execute("PRAGMA foreign_keys=ON")
