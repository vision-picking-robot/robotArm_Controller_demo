from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.model_registry import BUILTIN, ModelRegistry
from app.store import Store
from tests.fakes import settings, wait_until
from tests.test_runtime import factory


def by_id(models):
    return {model["id"]: model for model in models}


def test_names_survive_restart_and_reach_both_event_clients(tmp_path):
    config = settings()
    with TestClient(create_app(config, factory, tmp_path)) as client:
        wait_until(lambda: client.get("/readyz").status_code == 200)
        status = client.get("/api/status").json()
        external = status["model_version"]
        with client.websocket_connect("/ws/events") as left, client.websocket_connect("/ws/events") as right:
            left.receive_json()
            right.receive_json()
            for identifier in (external, BUILTIN):
                name = "  " + "시" * 80 + "  "
                response = client.patch("/api/models/" + identifier, json={"name": name})
                assert response.status_code == 200
                assert response.json()["name"] == name.strip()
            for channel in (left, right):
                for _ in range(3):
                    event = channel.receive_json()
                    view = by_id(event["models"])
                    if view[BUILTIN]["name"] == name.strip():
                        break
                assert view[external]["name"] == view[BUILTIN]["name"] == name.strip()
                assert event["status"]["session_id"] == status["session_id"]
                assert event["status"]["model_version"] == external
            assert view == by_id(client.get("/api/models").json()["models"])
            assert view[BUILTIN]["classes"][41] == {"id": 41, "name": "cup"}
            assert len(view[BUILTIN]["classes"]) == 80
        for invalid in (" ", "a"*81, "A\x07B"):
            assert client.patch("/api/models/" + external, json={"name": invalid}).status_code == 422
        assert client.patch("/api/models/absent", json={"name": "모델"}).status_code == 404
    with TestClient(create_app(replace(config, camera_autostart=False), factory, tmp_path)) as client:
        view = by_id(client.get("/api/models").json()["models"])
        assert view[external]["name"] == view[BUILTIN]["name"] == name.strip()
        assert client.get("/api/status").json()["state"] == "camera_off"


def trained(store, identifier, parent, initial, output):
    dataset = store.list("dataset")[0]
    model = {"id": identifier, "name": identifier, "kind": "custom", "job_id": "job-" + identifier,
             "dataset_id": dataset["id"], "initial_model_id": parent, "initial_sha256": initial, "formats": {"mps": {"sha256": output}}}
    directory = store.root / model["job_id"]
    directory.mkdir()
    job = {"id": model["job_id"], "model_id": identifier, "directory": str(directory), "state": "completed",
           "initial_model_id": parent, "initial_sha256": initial, "dataset_id": dataset["id"], "dataset": dataset}
    return model, job


def test_restore_lineage_and_allocate_sibling_versions_atomically(tmp_path):
    store = Store(tmp_path)
    other = Store(tmp_path)
    try:
        registry = ModelRegistry(store)
        store.put("model", {"id": BUILTIN, "formats": {"mps": {"sha256": "base"}}})
        from tests.test_datasets import populated
        from app.datasets import build_dataset
        populated(store)
        dataset = build_dataset(store)
        # Child inserted before its parent; an unrelated training run also starts from base.
        records = []
        for args in [("child", "first", "weights1", "weights2"), ("first", None, "base", "weights1"),
                     ("second", None, "base", "weights3"), ("unknown", None, "stale", "weights4")]:
            model, job = trained(store, *args)
            records.extend([("model", model), ("job", job)])
        store.put_many(records)
        registry.recover_versions()
        registry.recover_versions()
        models = by_id(registry.models())
        assert models["first"]["family_id"] == "first" and models["second"]["family_id"] == "second"
        assert models["first"]["version_number"] == models["second"]["version_number"] == 1
        assert models["child"]["family_id"] == "first" and models["child"]["version_number"] == 2
        assert models["first"]["parent_model_id"] == BUILTIN and models["child"]["parent_model_id"] == "first"
        assert models["child"]["dataset_counts"] == dataset["counts"]
        assert models["unknown"].get("version_number") is None
        candidates = [trained(store, name, "first", "weights1", name) for name in ("sibling1", "sibling2")]
        with ThreadPoolExecutor(2) as workers:
            futures = [workers.submit(reg.publish, *pair) for reg, pair in zip((registry, ModelRegistry(other)), candidates)]
            for future in futures:
                future.result()
        registry.publish(*candidates[0])
        models = by_id(registry.models())
        assert {models[key]["version_number"] for key in ("sibling1", "sibling2")} == {3, 4}
        assert models["sibling2"]["parent_model_id"] == "first"

        model, job = trained(store, "broken", "first", "weights1", "bad")
        import sqlite3
        store.db.execute("CREATE TRIGGER fail_job BEFORE INSERT ON training_jobs WHEN NEW.public_id='job-broken' "
                         "BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        with pytest.raises(sqlite3.IntegrityError):
            registry.publish(model, job)
        assert "broken" not in by_id(registry.models())
    finally:
        other.close()
        store.close()
