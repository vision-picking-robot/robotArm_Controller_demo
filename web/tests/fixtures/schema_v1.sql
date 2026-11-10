-- Frozen pre-integer-key schema for migration and rollback tests.
CREATE TABLE training_classes (
    id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL UNIQUE, created_at TEXT
);
CREATE TABLE capture_groups (
    id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL UNIQUE, created_at TEXT
);
CREATE TABLE models (
    id TEXT PRIMARY KEY NOT NULL, name TEXT, kind TEXT, created_at TEXT,
    job_id TEXT UNIQUE REFERENCES training_jobs(id) DEFERRABLE INITIALLY DEFERRED,
    dataset_id TEXT REFERENCES datasets(id),
    initial_model_id TEXT REFERENCES models(id) DEFERRABLE INITIALLY DEFERRED, initial_sha256 TEXT,
    parent_model_id TEXT REFERENCES models(id), family_id TEXT REFERENCES models(id),
    version_number INTEGER CHECK(version_number > 0),
    training TEXT CHECK(training IS NULL OR json_valid(training)), environment TEXT CHECK(environment IS NULL OR json_valid(environment)),
    UNIQUE(family_id, version_number)
);
CREATE TABLE model_aliases (
    id TEXT PRIMARY KEY NOT NULL, model_id TEXT NOT NULL UNIQUE REFERENCES models(id), name TEXT NOT NULL
);
CREATE TABLE model_classes (
    model_id TEXT NOT NULL REFERENCES models(id), class_id INTEGER NOT NULL, name TEXT NOT NULL,
    uuid TEXT REFERENCES training_classes(id), position INTEGER NOT NULL,
    PRIMARY KEY(model_id, class_id)
);
CREATE TABLE model_formats (
    model_id TEXT NOT NULL REFERENCES models(id), backend TEXT NOT NULL,
    state TEXT, path TEXT, sha256 TEXT, error TEXT, evaluation_dataset TEXT,
    nms TEXT, map_drop_percentage_points REAL, source_sha256 TEXT,
    metrics TEXT, latency TEXT, runtime TEXT, environment TEXT, evaluation TEXT, export_options TEXT,
    PRIMARY KEY(model_id, backend)
);
CREATE TABLE active_models (
    backend TEXT PRIMARY KEY NOT NULL, model_id TEXT NOT NULL REFERENCES models(id)
);
CREATE TABLE model_operations (
    id TEXT PRIMARY KEY NOT NULL, model_id TEXT NOT NULL REFERENCES models(id), state TEXT NOT NULL,
    created_at TEXT, ended_at TEXT, error TEXT, restored_model_id TEXT REFERENCES models(id)
);
CREATE TABLE collections (
    id TEXT PRIMARY KEY NOT NULL, class_id TEXT NOT NULL REFERENCES training_classes(id),
    group_id TEXT NOT NULL REFERENCES capture_groups(id), model_id TEXT NOT NULL REFERENCES models(id),
    model_hash TEXT, interval_ms INTEGER, confidence REAL, state TEXT NOT NULL,
    created_at TEXT, ended_at TEXT, error TEXT, stop_reason TEXT
);
CREATE TABLE collection_skips (
    collection_id TEXT NOT NULL REFERENCES collections(id), reason TEXT NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY(collection_id, reason)
);
CREATE TABLE samples (
    id TEXT PRIMARY KEY NOT NULL, collection_id TEXT NOT NULL REFERENCES collections(id),
    session_id TEXT NOT NULL, frame_id INTEGER NOT NULL, captured_at TEXT NOT NULL,
    width INTEGER NOT NULL, height INTEGER NOT NULL, left_x REAL NOT NULL, top_y REAL NOT NULL,
    right_x REAL NOT NULL, bottom_y REAL NOT NULL, center_x REAL NOT NULL, center_y REAL NOT NULL,
    box_width REAL NOT NULL, box_height REAL NOT NULL, predicted_class_id INTEGER NOT NULL,
    predicted_label TEXT NOT NULL, confidence REAL NOT NULL, review TEXT NOT NULL,
    sha256 TEXT NOT NULL, label_sha256 TEXT NOT NULL, dhash TEXT NOT NULL, image TEXT NOT NULL,
    label TEXT NOT NULL, created_at TEXT NOT NULL, reviewed_at TEXT
);
CREATE INDEX samples_collection ON samples(collection_id);
CREATE INDEX samples_review ON samples(review);
CREATE TABLE datasets (
    id TEXT PRIMARY KEY NOT NULL, created_at TEXT, status TEXT, split_policy TEXT, path TEXT,
    manifest_sha256 TEXT, yaml_sha256 TEXT
);
CREATE TABLE dataset_splits (
    dataset_id TEXT NOT NULL REFERENCES datasets(id), split TEXT NOT NULL CHECK(split IN ('train','val')),
    sha256 TEXT NOT NULL, PRIMARY KEY(dataset_id, split)
);
CREATE TABLE dataset_classes (
    dataset_id TEXT NOT NULL REFERENCES datasets(id), class_id INTEGER NOT NULL,
    uuid TEXT NOT NULL REFERENCES training_classes(id), name TEXT NOT NULL,
    position INTEGER NOT NULL, PRIMARY KEY(dataset_id, class_id), UNIQUE(dataset_id, uuid)
);
CREATE TABLE dataset_items (
    dataset_id TEXT NOT NULL REFERENCES datasets(id), position INTEGER NOT NULL,
    sample_id TEXT NOT NULL REFERENCES samples(id), class_uuid TEXT NOT NULL,
    group_id TEXT NOT NULL REFERENCES capture_groups(id), split TEXT NOT NULL CHECK(split IN ('train','val')),
    image TEXT NOT NULL, label TEXT NOT NULL, sha256 TEXT NOT NULL, label_sha256 TEXT NOT NULL,
    PRIMARY KEY(dataset_id, position), UNIQUE(dataset_id, sample_id),
    FOREIGN KEY(dataset_id, class_uuid) REFERENCES dataset_classes(dataset_id, uuid)
);
CREATE TABLE training_jobs (
    id TEXT PRIMARY KEY NOT NULL, dataset_id TEXT NOT NULL REFERENCES datasets(id),
    initial_model_id TEXT REFERENCES models(id) DEFERRABLE INITIALLY DEFERRED, initial_weights TEXT, initial_sha256 TEXT,
    -- Allocated before training; cancelled jobs can have no published model.
    model_id TEXT UNIQUE, directory TEXT, state TEXT NOT NULL, batch INTEGER, epochs INTEGER, patience INTEGER,
    created_at TEXT, ended_at TEXT, error TEXT, warning TEXT, progress TEXT
);
CREATE TABLE products (
    sku TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, unit TEXT NOT NULL,
    stock INTEGER NOT NULL CHECK(typeof(stock)='integer' AND stock >= 0), alias TEXT NOT NULL, bin TEXT NOT NULL
);
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE orders (
    id TEXT PRIMARY KEY NOT NULL, number INTEGER NOT NULL UNIQUE, customer_id INTEGER NOT NULL REFERENCES customers(id),
    paused INTEGER NOT NULL CHECK(paused IN (0,1)), state TEXT NOT NULL
);
CREATE TABLE order_items (
    id TEXT PRIMARY KEY NOT NULL, order_id TEXT NOT NULL REFERENCES orders(id), position INTEGER NOT NULL,
    sku TEXT NOT NULL REFERENCES products(sku), done INTEGER NOT NULL CHECK(done IN (0,1)), UNIQUE(order_id, position)
);
CREATE INDEX order_items_sku ON order_items(sku, done);
CREATE TABLE product_class_bindings (
    id TEXT PRIMARY KEY NOT NULL, model_id TEXT NOT NULL, class_id INTEGER NOT NULL, sku TEXT NOT NULL REFERENCES products(sku),
    FOREIGN KEY(model_id,class_id) REFERENCES model_classes(model_id,class_id) ON DELETE CASCADE,
    UNIQUE(model_id,class_id), UNIQUE(model_id,sku)
);
CREATE TABLE packing_events (
    id TEXT PRIMARY KEY NOT NULL, time TEXT NOT NULL, source TEXT NOT NULL, message TEXT NOT NULL
);
CREATE VIEW inventory AS
    SELECT p.*, COALESCE(r.reserved,0) AS reserved, p.stock-COALESCE(r.reserved,0) AS available
    FROM products p LEFT JOIN (
        SELECT i.sku,COUNT(*) AS reserved FROM order_items i JOIN orders o ON o.id=i.order_id
        WHERE o.state='packing' AND i.done=0 GROUP BY i.sku
    ) r ON r.sku=p.sku;
PRAGMA user_version=1;
