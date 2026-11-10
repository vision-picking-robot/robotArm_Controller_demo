CREATE TABLE inference_backends (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE);
CREATE TABLE capture_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL UNIQUE);
CREATE TABLE skip_reasons (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE);
CREATE TABLE split_types (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE CHECK(code IN ('train','val')));
CREATE TABLE training_classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL UNIQUE, created_at TEXT
);
CREATE TABLE capture_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL UNIQUE, created_at TEXT
);
CREATE TABLE models (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, name TEXT, kind TEXT, created_at TEXT,
    job_id INTEGER UNIQUE REFERENCES training_jobs(id) DEFERRABLE INITIALLY DEFERRED,
    dataset_id INTEGER REFERENCES datasets(id),
    initial_model_id INTEGER REFERENCES models(id) DEFERRABLE INITIALLY DEFERRED, initial_sha256 TEXT,
    parent_model_id INTEGER REFERENCES models(id), family_id INTEGER REFERENCES models(id),
    version_number INTEGER CHECK(version_number > 0),
    training TEXT CHECK(training IS NULL OR json_valid(training)), environment TEXT CHECK(environment IS NULL OR json_valid(environment)),
    UNIQUE(family_id, version_number)
);
CREATE TABLE model_aliases (
    model_id INTEGER PRIMARY KEY REFERENCES models(id), public_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL
);
CREATE TABLE model_classes (
    model_id INTEGER NOT NULL REFERENCES models(id), class_id INTEGER NOT NULL, name TEXT NOT NULL,
    training_class_id INTEGER REFERENCES training_classes(id), position INTEGER NOT NULL,
    PRIMARY KEY(model_id, class_id)
);
CREATE TABLE model_formats (
    model_id INTEGER NOT NULL REFERENCES models(id), backend_id INTEGER NOT NULL REFERENCES inference_backends(id),
    state TEXT, path TEXT, sha256 TEXT, error TEXT, evaluation_dataset TEXT,
    nms TEXT, map_drop_percentage_points REAL, source_sha256 TEXT,
    metrics TEXT, latency TEXT, runtime TEXT, environment TEXT, evaluation TEXT, export_options TEXT,
    PRIMARY KEY(model_id, backend_id)
);
CREATE TABLE active_models (
    backend_id INTEGER PRIMARY KEY REFERENCES inference_backends(id), model_id INTEGER NOT NULL REFERENCES models(id)
);
CREATE TABLE model_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, model_id INTEGER NOT NULL REFERENCES models(id), state TEXT NOT NULL,
    created_at TEXT, ended_at TEXT, error TEXT, restored_model_id INTEGER REFERENCES models(id)
);
CREATE TABLE collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, class_id INTEGER NOT NULL REFERENCES training_classes(id),
    group_id INTEGER NOT NULL REFERENCES capture_groups(id), model_id INTEGER NOT NULL REFERENCES models(id),
    model_hash TEXT, interval_ms INTEGER, confidence REAL, state TEXT NOT NULL,
    created_at TEXT, ended_at TEXT, error TEXT, stop_reason TEXT
);
CREATE TABLE collection_skips (
    collection_id INTEGER NOT NULL REFERENCES collections(id), reason_id INTEGER NOT NULL REFERENCES skip_reasons(id), count INTEGER NOT NULL,
    PRIMARY KEY(collection_id, reason_id)
);
CREATE TABLE samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, collection_id INTEGER NOT NULL REFERENCES collections(id),
    session_id INTEGER NOT NULL REFERENCES capture_sessions(id), frame_id INTEGER NOT NULL, captured_at TEXT NOT NULL,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, created_at TEXT, status TEXT, split_policy TEXT, path TEXT,
    manifest_sha256 TEXT, yaml_sha256 TEXT
);
CREATE TABLE dataset_splits (
    dataset_id INTEGER NOT NULL REFERENCES datasets(id), split_id INTEGER NOT NULL REFERENCES split_types(id),
    sha256 TEXT NOT NULL, PRIMARY KEY(dataset_id, split_id)
);
CREATE TABLE dataset_classes (
    dataset_id INTEGER NOT NULL REFERENCES datasets(id), class_id INTEGER NOT NULL,
    training_class_id INTEGER NOT NULL REFERENCES training_classes(id), name TEXT NOT NULL,
    position INTEGER NOT NULL, PRIMARY KEY(dataset_id, class_id), UNIQUE(dataset_id, training_class_id)
);
CREATE TABLE dataset_items (
    dataset_id INTEGER NOT NULL REFERENCES datasets(id), position INTEGER NOT NULL,
    sample_id INTEGER NOT NULL REFERENCES samples(id), training_class_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL REFERENCES capture_groups(id), split_id INTEGER NOT NULL REFERENCES split_types(id),
    image TEXT NOT NULL, label TEXT NOT NULL, sha256 TEXT NOT NULL, label_sha256 TEXT NOT NULL,
    PRIMARY KEY(dataset_id, position), UNIQUE(dataset_id, sample_id),
    FOREIGN KEY(dataset_id, training_class_id) REFERENCES dataset_classes(dataset_id, training_class_id)
);
CREATE TABLE training_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    initial_model_id INTEGER REFERENCES models(id) DEFERRABLE INITIALLY DEFERRED, initial_weights TEXT, initial_sha256 TEXT,
    -- Allocated before training; cancelled jobs can have no published model.
    planned_model_public_id TEXT UNIQUE, directory TEXT, state TEXT NOT NULL, batch INTEGER, epochs INTEGER, patience INTEGER,
    created_at TEXT, ended_at TEXT, error TEXT, warning TEXT, progress TEXT
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL UNIQUE, name TEXT NOT NULL, unit TEXT NOT NULL,
    stock INTEGER NOT NULL CHECK(typeof(stock)='integer' AND stock >= 0), alias TEXT NOT NULL, bin TEXT NOT NULL
);
CREATE TABLE customers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL);
CREATE TABLE customer_rfid (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(id), uid TEXT COLLATE NOCASE UNIQUE
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, number INTEGER NOT NULL UNIQUE, customer_id INTEGER NOT NULL REFERENCES customers(id),
    paused INTEGER NOT NULL CHECK(paused IN (0,1)), state TEXT NOT NULL
);
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, order_id INTEGER NOT NULL REFERENCES orders(id), position INTEGER NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id), done INTEGER NOT NULL CHECK(done IN (0,1)), UNIQUE(order_id, position)
);
CREATE INDEX order_items_product ON order_items(product_id, done);
CREATE TABLE product_class_bindings (
    public_id TEXT NOT NULL UNIQUE, model_id INTEGER NOT NULL, class_id INTEGER NOT NULL, product_id INTEGER NOT NULL REFERENCES products(id),
    FOREIGN KEY(model_id,class_id) REFERENCES model_classes(model_id,class_id) ON DELETE CASCADE,
    PRIMARY KEY(model_id,class_id), UNIQUE(model_id,product_id)
);
CREATE TABLE packing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, time TEXT NOT NULL, source TEXT NOT NULL, message TEXT NOT NULL
);
CREATE VIEW inventory AS
    SELECT p.*, COALESCE(r.reserved,0) AS reserved, p.stock-COALESCE(r.reserved,0) AS available
    FROM products p LEFT JOIN (
        SELECT i.product_id,COUNT(*) AS reserved FROM order_items i JOIN orders o ON o.id=i.order_id
        WHERE o.state='packing' AND i.done=0 GROUP BY i.product_id
    ) r ON r.product_id=p.id;
