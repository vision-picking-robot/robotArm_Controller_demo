import re
import sqlite3
from pathlib import Path
from contextlib import contextmanager, closing
from datetime import datetime
from uuid import uuid4


def normalize_uid(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r'(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{14}|[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){3}|[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){6})', value):
        raise ValueError('UID must contain exactly 4 or 7 hexadecimal bytes')
    return value.replace(':', '').upper()


SEEDS = [
    ('0B71E55D', 'LOTTE-PEPERO-01', '아몬드 빼빼로', 'CONFECTIONERY', 0, 'snack_pepero', '2026-11-01 13:45:00', '2027-12-25 19:00:00', 2500, 'BOX-01'),
    ('0B71E75D', 'BEV-PET-500ML', '제로 콜라 500ml', 'BEVERAGE', 1, 'beverage_pet', '2026-11-05 09:30:00', '2027-05-04 23:59:59', 1600, 'BOX-02'),
    ('0B71E65D', 'CAN-SPAM-200G', '스팸 클래식 200g', 'CANNED', 2, 'canned_meat', '2026-10-15 10:00:00', '2029-10-14 23:59:59', 4800, 'BOX-03'),
    ('0B71ED5D', 'SAN-WIPES-100', '물티슈', 'SANITARY', 3, 'wet_wipes', '2026-11-02 16:20:00', '2028-11-01 23:59:59', 2200, 'BOX-04'),
    ('0B71EC5D', 'TOY-PLUSH-MINI', '곰인형', 'TOY', 4, 'plush_toy', '2026-09-01 11:00:00', '9999-12-31 23:59:59', 8900, 'BOX-05'),
]
SEED_TAGS = SEEDS


class ActiveTaskError(ValueError):
    """Return the matching task snapshot, never an unrelated queue head."""
    def __init__(self, task):
        super().__init__('tag already has an active task')
        self.task = task


class Database:
    def __init__(self, path='rfid.db'):
        self.path = str(path)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            yield db

    def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS product_tag_mappings (
                    tag_uid VARCHAR(32) PRIMARY KEY,
                    sku_code VARCHAR(50) NOT NULL UNIQUE,
                    product_name VARCHAR(100) NOT NULL,
                    category VARCHAR(50) NOT NULL,
                    target_class_id INT NOT NULL,
                    yolo_class_name VARCHAR(50) NOT NULL,
                    inbound_at TIMESTAMP NOT NULL,
                    expired_at TIMESTAMP NOT NULL,
                    retail_price INT NOT NULL CHECK(retail_price >= 0),
                    target_box_id VARCHAR(50) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'READY'
                        CHECK(status IN ('READY','PICKING','COMPLETED','DEFECTIVE'))
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    tag_uid TEXT NOT NULL REFERENCES product_tag_mappings(tag_uid),
                    quantity INT NOT NULL CHECK(quantity > 0),
                    completed INT NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'READY',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    CHECK(completed >= 0 AND completed <= quantity)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_tag ON tasks(tag_uid)
                    WHERE status IN ('READY','PICKING');
            ''')
            db.executemany('''INSERT OR IGNORE INTO product_tag_mappings
                (tag_uid,sku_code,product_name,category,target_class_id,yolo_class_name,
                 inbound_at,expired_at,retail_price,target_box_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)''', SEEDS)

    def product(self, uid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM product_tag_mappings WHERE tag_uid=?', (normalize_uid(uid),)).fetchone()
            return dict(row) if row else None

    def products(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM product_tag_mappings ORDER BY rowid')]

    def enqueue(self, uid, quantity, event_id):
        uid = normalize_uid(uid)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM tasks WHERE event_id=?', (event_id,)).fetchone()
            if old:
                if old['tag_uid'] != uid or old['quantity'] != quantity:
                    raise ValueError('event_id already used with different payload')
                return dict(old), False
            if not db.execute('SELECT 1 FROM product_tag_mappings WHERE tag_uid=?', (uid,)).fetchone():
                raise KeyError(uid)
            old = db.execute("SELECT * FROM tasks WHERE tag_uid=? AND status IN ('READY','PICKING')", (uid,)).fetchone()
            if old:
                raise ActiveTaskError(dict(old))
            task_id = str(uuid4())
            db.execute('INSERT INTO tasks(task_id,event_id,tag_uid,quantity,created_at) VALUES (?,?,?,?,?)',
                       (task_id, event_id, uid, quantity, datetime.now().astimezone().isoformat()))
            db.execute("UPDATE product_tag_mappings SET status='READY' WHERE tag_uid=?", (uid,))
            return dict(db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()), True

    def current_task(self):
        with self.connect() as db:
            row = db.execute('''SELECT t.*, p.target_class_id, p.yolo_class_name, p.target_box_id,
                p.product_name FROM tasks t JOIN product_tag_mappings p USING(tag_uid)
                WHERE t.status IN ('READY','PICKING') ORDER BY t.rowid LIMIT 1''').fetchone()
            return dict(row) if row else None

    def progress(self, task_id, completed, status, error=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            if not task:
                raise KeyError(task_id)
            if task['status'] not in ('READY', 'PICKING'):
                raise ValueError('task is already terminal')
            if completed < task['completed'] or completed > task['quantity']:
                raise ValueError('invalid completed quantity')
            if status not in ('PICKING', 'COMPLETED', 'DEFECTIVE'):
                raise ValueError('invalid status')
            if status == 'COMPLETED' and completed != task['quantity']:
                raise ValueError('task is incomplete')
            db.execute('UPDATE tasks SET completed=?,status=?,error=? WHERE task_id=?', (completed,status,error,task_id))
            db.execute('UPDATE product_tag_mappings SET status=? WHERE tag_uid=?', (status,task['tag_uid']))
