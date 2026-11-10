"""Small, transactional metadata repository. User-facing names never become paths."""
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .migration import initialize


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return f"{prefix}-{uuid.uuid4().hex}"


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


class DomainError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "metadata.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.relations = initialize(self.db, self.root)
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def write_transaction(self):
        """Allow read/allocate/write under the same SQLite writer reservation."""
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            yield self._write

    def _write(self, kind, value):
        self.relations.write(kind, value)

    def put_many(self, records):
        with self.write_transaction() as write:
            # Batches may contain child models before their parents, and mutually
            # linked jobs/models. Reserve only models actually declared in this batch.
            records = list(records)
            for kind, value in records:
                if kind == "model":
                    self.relations.keys.reserve_model(value["id"])
            # Class bindings require the model's actual class rows as well.
            priority = {"model": 1, "product_class": 2}
            for kind, value in sorted(records, key=lambda record: priority.get(record[0], 0)):
                write(kind, value)

    def put(self, kind, value):
        self.put_many([(kind, value)])
        return value

    @contextmanager
    def read_transaction(self):
        """Keep the parent and child SELECTs in one SQLite snapshot across connections."""
        with self.lock:
            if self.db.in_transaction:
                yield
            else:
                with self.db:
                    self.db.execute("BEGIN")
                    yield

    def get(self, kind, identifier):
        with self.read_transaction():
            if identifier is not None:
                rows = self.relations.read(kind, identifier)
                if rows:
                    return rows[0]
        raise DomainError(f"{kind}: 항목을 찾을 수 없습니다", 404)

    def list(self, kind):
        with self.read_transaction():
            return self.relations.read(kind)

    def update(self, kind, identifier, **changes):
        with self.write_transaction() as write:
            value = self.get(kind, identifier)
            value.update(changes)
            write(kind, value)
            return value

    def named(self, kind, name):
        name = name.strip()
        if not name or len(name) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise DomainError("이름은 제어 문자 없이 1~80자로 입력하세요", 422)
        with self.write_transaction() as write:
            if any(v["name"].casefold() == name.casefold() for v in self.list(kind)):
                raise DomainError("같은 이름이 있습니다. 기존 항목을 선택하세요")
            value = {"id": uid(kind), "name": name, "created_at": now()}
            write(kind, value)
            return value

    def recover(self):
        for kind in ("collection", "job", "operation"):
            for value in self.list(kind):
                if value.get("state") in {"collecting", "stopping", "queued", "preparing", "training", "evaluating", "exporting", "running", "cancelling"}:
                    self.update(kind, value["id"], state="interrupted", error="서버 재시작으로 중단됨. 자동 재실행하지 않습니다.", ended_at=now())
        # Only DB-complete samples can be reviewed or exported. Remove interrupted pairs.
        known = {str((self.root / s[key]).resolve()) for s in self.list("sample") for key in ("image", "label")}
        directory = self.root / "collections"
        if directory.exists():
            for path in directory.rglob("*"):
                if (path.is_file() and re.fullmatch(r"sample-[0-9a-f]{32}\.(jpg|txt)(\.tmp)?", path.name)
                        and str(path.resolve()) not in known):
                    path.unlink()

    def close(self):
        with self.lock:
            self.db.close()
