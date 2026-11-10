"""Failure-path regressions found during the post-implementation review."""
import asyncio
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.main import create_app
from app.runtime import Runtime, artifact_hash
from app.store import DomainError
from tests.fakes import settings, wait_until
from tests.test_collection import collector, feed_three
from tests.test_runtime import runtime, factory, register, fake_job


def test_collection_start_db_error_does_not_enable_saving(collector, monkeypatch):
    config = dict(collector.config)
    collector.stop()
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise sqlite3.OperationalError("disk full")
        patch.setattr(collector.store, "update", fail)
        with pytest.raises(sqlite3.OperationalError):
            collector.start(config)
        assert collector.active is None


def test_collection_cannot_restart_until_inflight_write_finishes(collector):
    entered, release = threading.Event(), threading.Event()
    original = collector.write
    config = dict(collector.config)
    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    collector.write = blocked
    feed_three(collector)
    assert entered.wait(1)
    stopper = threading.Thread(target=collector.stop)
    stopper.start()
    try:
        wait_until(lambda: collector.active is None)
        with pytest.raises(DomainError):
            collector.start(config)
        assert collector.snapshot()["state"] == "stopping"
    finally:
        release.set()
        stopper.join(3)
    assert collector.snapshot()["saved"] == 1


def test_stop_finishes_inflight_write_even_when_status_db_write_fails(collector, monkeypatch):
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    original = collector.write
    errors = []
    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    collector.write = blocked
    feed_three(collector)
    assert entered.wait(1)
    update = collector.store.update
    def broken_status(kind, identifier, **changes):
        if changes.get("state") in {"stopping", "stopped"}:
            raise sqlite3.OperationalError("disk full")
        return update(kind, identifier, **changes)
    with monkeypatch.context() as patch:
        patch.setattr(collector.store, "update", broken_status)
        def stop():
            try:
                collector.stop()
            except Exception as exc:
                errors.append(exc)
            finally:
                stopped.set()
        stopper = threading.Thread(target=stop)
        stopper.start()
        try:
            wait_until(lambda: collector.active is None)
            assert not stopped.is_set()
        finally:
            release.set()
            stopper.join(3)
        assert not errors
        assert collector.snapshot()["state"] == "stopped"
        assert collector.snapshot()["error"] == "disk full"


def test_fast_switch_sends_session_status_before_new_detections(tmp_path):
    app = create_app(settings(), factory, state_root=tmp_path)
    stop = threading.Event()
    generation = [1]
    packets = []
    class Live:
        stop_event = stop
        def status(self):
            return {"event": "status", "state": "ready", "ready": True,
                    "session_id": str(generation[0]), "model_version": str(generation[0])}
        def latest_result(self):
            return {"event": "detections", "session_id": str(generation[0]), "frame_id": 1}
    class Socket:
        async def accept(self): pass
        async def close(self): pass
        async def send_json(self, packet):
            packets.append(packet)
            if packet["event"] == "detections":
                if generation[0] == 1:
                    generation[0] = 2
                else:
                    stop.set()
    app.state.pipeline = Live()
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/ws/detections")
    asyncio.run(endpoint(Socket()))
    assert [(p["event"],p["session_id"]) for p in packets] == [
        ("status","1"),("detections","1"),("status","2"),("detections","2")]


def test_slow_status_lock_does_not_block_http_event_loop(tmp_path):
    app = create_app(settings(), factory, state_root=tmp_path)
    entered, release = threading.Event(), threading.Event()
    def slow_status():
        entered.set()
        release.wait(.5)
        return {"state": "ready", "ready": True}
    app.state.pipeline = SimpleNamespace(status=slow_status)
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/status")
    async def check():
        task = asyncio.create_task(endpoint())
        try:
            await asyncio.to_thread(entered.wait, 1)
            assert not task.done(), "Status read held the entire HTTP event loop"
        finally:
            release.set()
            await task
    asyncio.run(check())


def test_child_is_reaped_before_returning_after_metadata_failure(runtime, monkeypatch):
    directory = runtime.store.root / "worker-failure"
    directory.mkdir()
    job = {"id": "worker-failure", "directory": str(directory)}
    spawned = []
    popen = subprocess.Popen
    def start(*args, **kwargs):
        child = popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        spawned.append(child)
        return child
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("database full")
    with monkeypatch.context() as patch:
        patch.setattr("app.runtime.subprocess.Popen", start)
        patch.setattr(runtime.store, "update", fail)
        try:
            with pytest.raises(sqlite3.OperationalError):
                runtime._run_child(Path(sys.executable), job, "train")
            assert spawned[0].poll() is not None, "Owned GPU worker could overlap resumed inference"
            assert runtime.process is None
        finally:
            for process in spawned:
                Runtime._terminate(process)


def test_shutdown_releases_pipeline_even_if_collection_cleanup_raises(runtime, monkeypatch):
    pipeline = runtime.pipeline
    close = runtime.collector.close
    def fail_after_close():
        close()
        raise OSError("collection cleanup failed")
    with monkeypatch.context() as patch:
        patch.setattr(runtime.collector, "close", fail_after_close)
        with pytest.raises(Exception, match="collection cleanup failed"):
            runtime.stop()
        assert all(not t.is_alive() for t in pipeline.threads)


def test_saved_selection_does_not_load_modified_weights(tmp_path):
    first = Runtime(settings(), factory, tmp_path)
    model = register(first, "v2")
    path = Path(model["formats"]["cpu"]["path"])
    model["formats"]["cpu"]["sha256"] = artifact_hash(path)
    first.store.put("model", model)
    first.store.put("setting", {"id":"selected-cpu", "model_id":"v2"})
    first.stop()
    path.write_bytes(b"modified")
    second = Runtime(settings(), factory, tmp_path)
    try:
        assert second.model_id != "v2"
        assert second.error
    finally:
        second.stop()


def test_initial_user_model_hash_is_checked_before_training(runtime, monkeypatch):
    values = fake_job(runtime)
    model = runtime.store.get("model", "initial")
    path = Path(model["formats"]["mps"]["path"])
    model["formats"]["mps"]["sha256"] = artifact_hash(path)
    runtime.store.put("model", model)
    path.write_bytes(b"changed weights")
    def cancelled(*args):
        raise InterruptedError("test cleanup")
    monkeypatch.setattr(runtime, "_run_child", cancelled)
    with pytest.raises(DomainError, match="해시"):
        runtime.create_job(values)


def test_bulk_review_db_failure_rolls_back_all_samples(runtime):
    from tests.test_datasets import add_sample
    cls = runtime.store.named("class", "대상")["id"]
    ids = [add_sample(runtime.store, cls, "room", n, review="pending")["id"] for n in (1, 2)]
    runtime.store.db.execute(f"""CREATE TRIGGER reject_second BEFORE UPDATE ON samples WHEN NEW.public_id='{ids[1]}'
                                 BEGIN SELECT RAISE(ABORT, 'disk full'); END""")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            runtime.review_samples(ids, "approved")
        assert all(runtime.store.get("sample", i)["review"] == "pending" for i in ids)
    finally:
        runtime.store.db.execute("DROP TRIGGER reject_second")


def test_failed_runtime_initialization_does_not_leak_photo_writer(tmp_path, monkeypatch):
    before = {t.ident for t in threading.enumerate() if t.name == 'yolo-photo-writer'}
    def fail(*_):
        raise OSError('model metadata unreadable')
    monkeypatch.setattr(Runtime, '_register_defaults', fail)
    with pytest.raises(OSError):
        Runtime(settings(), factory, tmp_path)
    assert {t.ident for t in threading.enumerate() if t.name == 'yolo-photo-writer'} == before


def test_stream_subscription_does_not_wait_for_model_teardown_lock(runtime):
    entered, release, subscribed = threading.Event(), threading.Event(), threading.Event()
    def hold_transition():
        with runtime.lock:
            entered.set()
            release.wait(2)
    holder = threading.Thread(target=hold_transition)
    holder.start()
    assert entered.wait(1)
    def subscribe():
        runtime.subscribe_video(1)
        subscribed.set()
    subscriber = threading.Thread(target=subscribe)
    subscriber.start()
    try:
        assert subscribed.wait(.5), 'MJPEG subscription waited on model teardown'
    finally:
        release.set()
        holder.join(2)
        subscriber.join(2)
        runtime.subscribe_video(-1)


def test_many_classes_with_independent_groups_can_form_valid_split():
    from app.datasets import split_components
    # Two independent capture environments per class. A valid split is obvious,
    # but drawing every group at p=.2 almost never covers twenty classes.
    groups = [[{"id":f"sample-{cls}-{scene}", "class_id":f"class-{cls}"}]
              for cls in range(20) for scene in range(2)]
    assigned = split_components(groups)
    for cls in range(20):
        assert {split for sample, split in assigned if sample["class_id"]==f"class-{cls}"} == {"train", "val"}


def test_failed_switch_does_not_persist_new_selection(runtime):
    old = runtime.model_id
    runtime.store.put("setting", {"id": "selected-cpu", "model_id": old})
    register(runtime, "v2")
    runtime.store.db.execute("""CREATE TRIGGER reject_switch_commit BEFORE UPDATE ON model_operations
        WHEN NEW.state='completed'
        BEGIN SELECT RAISE(ABORT, 'operation commit failed'); END""")
    try:
        operation = runtime.activate("v2")
        wait_until(lambda: runtime.store.get("operation", operation["id"])["state"] == "failed")
        wait_until(lambda: runtime.busy is None)
        assert runtime.model_id == old
        assert runtime.store.get("setting", "selected-cpu")["model_id"] == old
    finally:
        runtime.store.db.execute("DROP TRIGGER reject_switch_commit")
