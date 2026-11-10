import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.collection import Collector, normalized, valid_box
from app.store import Store
from tests.fakes import wait_until


@pytest.fixture
def collector(tmp_path):
    store = Store(tmp_path)
    store.put_many([("class", {"id": "class1", "name": "bottle"}), ("group", {"id": "group1", "name": "room"}),
                    ("model", {"id": "m1", "name": "teacher"})])
    store.put("collection", {"id": "c1", "state": "created", "class_id": "class1", "group_id": "group1",
                             "model_id": "m1", "model_hash": "hash"})
    worker = Collector(store)
    worker.start({"id": "c1", "class_id": "class1", "group_id": "group1", "model_id": "m1", "model_hash": "hash", "interval_ms": 250, "confidence": .5})
    yield worker
    worker.close()
    store.close()


def packet(number, objects=None, age=0, image=None):
    frame = np.full((100, 200, 3), 127, dtype=np.uint8) if image is None else image
    p = SimpleNamespace(frame_id=number, captured_ns=time.monotonic_ns()-int(age*1e9), captured_at="test", frame=frame)
    result = {"session_id": "session1", "frame_id": number,
              "objects": [{"class_id": 0, "label": "bottle", "confidence": .9, "xyxy": [50, 25, 150, 75]}] if objects is None else objects}
    return p, result


def feed_three(worker):
    for i in range(1, 4):
        worker.submit(*packet(i))


def test_box_validation_and_inverse_coordinates():
    assert valid_box([-5, 20, 230, 90], 200, 100) == [0, 20, 200, 90]
    for box in ([1, 2, 1, 3], [float("nan"), 0, 2, 2], [0, 0, float("inf"), 1]):
        assert valid_box(box, 200, 100) is None
    assert normalized([50, 25, 150, 75], 200, 100) == [.5, .5, .5, .5]


def test_three_distinct_frames_same_original_and_label(collector):
    first = packet(1)
    collector.submit(*first)
    collector.submit(*first)
    collector.submit(*packet(2))
    assert collector.snapshot()["queue_size"] == 0
    collector.submit(*packet(3))
    wait_until(lambda: collector.snapshot()["saved"] == 1)
    collector.stop()
    sample = collector.store.list("sample")[0]
    assert sample["frame_id"] == 3 and sample["class_id"] == "class1"
    assert (collector.store.root/sample["label"]).read_text().split() == ["0", "0.5000000000", "0.5000000000", "0.5000000000", "0.5000000000"]
    import cv2
    image = cv2.imread(str(collector.store.root/sample["image"]))
    assert image.shape == (100, 200, 3) and np.max(image)-np.min(image) == 0  # no rendered box
    assert collector.snapshot()["skipped"]["duplicate_frame"] == 1


@pytest.mark.parametrize("kind,reason", [("empty","no_detection"),("multi","ambiguous"),("outside","outside_center"),("low","low_confidence"),("invalid","invalid_box"),("stale","stale")])
def test_waiting_reasons_reset_confirmation(collector, kind, reason):
    collector.submit(*packet(1))
    p, result = packet(2, age=1 if kind == "stale" else 0)
    if kind == "empty": result["objects"] = []
    if kind == "multi": result["objects"] *= 2
    if kind == "outside": result["objects"][0]["xyxy"] = [0,0,20,20]
    if kind == "low": result["objects"][0]["confidence"] = .1
    if kind == "invalid": result["objects"][0]["xyxy"] = [0,0,0,0]
    collector.submit(p, result)
    assert collector.snapshot()["reason"] == reason
    collector.submit(*packet(3)); collector.submit(*packet(4))
    assert collector.snapshot()["queue_size"] == 0
    collector.submit(*packet(5))
    wait_until(lambda: collector.snapshot()["saved"] == 1)


def test_interval_duplicate_and_discontinuous_object(collector):
    feed_three(collector)
    wait_until(lambda: collector.snapshot()["saved"] == 1)
    collector.submit(*packet(4))
    assert collector.snapshot()["reason"] == "interval"
    p, result = packet(5)
    result["objects"][0]["xyxy"] = [90, 45, 110, 55]
    collector.submit(p,result)
    assert collector.snapshot()["reason"] == "stabilizing"


def test_queue_saturation_stop_barrier(collector, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = collector.write
    def blocked(*args):
        entered.set(); release.wait(3)
        return original(*args)
    collector.write = blocked
    base = time.monotonic_ns()
    virtual = [base]
    monkeypatch.setattr("app.collection.time.monotonic_ns", lambda: virtual[0])
    for i in range(1,4):
        virtual[0] += 250_000_000
        collector.submit(*packet(i))
    assert entered.wait(1)
    for i in range(4,15):
        virtual[0] += 250_000_000
        collector.submit(*packet(i))
    assert collector.snapshot()["queue_size"] == 8
    assert collector.snapshot()["skipped"]["queue_full"] >= 1
    stopped = threading.Event()
    task = threading.Thread(target=lambda: (collector.stop(),stopped.set()))
    task.start()
    wait_until(lambda: collector.active is None)
    assert not stopped.is_set()
    release.set(); task.join(2)
    assert stopped.is_set() and collector.snapshot()["saved"] == 1
    collector.submit(*packet(100))
    time.sleep(.15)
    assert len(collector.store.list("sample")) == 1


def test_disk_failure_never_registers_sample(collector):
    def fail(*_): raise OSError("disk full")
    collector.write = fail
    feed_three(collector)
    wait_until(lambda: collector.snapshot()["reason"] == "disk_error")
    assert collector.store.list("sample") == []
    assert collector.store.get("collection","c1")["error"] == "disk full"


def test_heartbeat_expiry_stops_saving(collector):
    collector.heartbeat_at -= 11
    wait_until(lambda: collector.active is None)
    assert collector.snapshot()["reason"] == "heartbeat_expired"
    assert collector.store.get("collection","c1")["state"] == "stopped"


def test_database_failure_stops_writer_without_ghost_sample(collector,monkeypatch):
    def fail(*_):raise OSError("database disk full")
    monkeypatch.setattr(collector.store,"put",fail)
    feed_three(collector)
    wait_until(lambda:collector.snapshot()["reason"]=="disk_error")
    assert collector.snapshot()["error"]=="database disk full"
    assert collector.active is None
    assert not list((collector.store.root/"collections").rglob("*.jpg"))


def test_unicode_render_preserves_raw_frame():
    import cv2
    from app.render import encode_jpeg
    frame=np.zeros((100,200,3),dtype=np.uint8)
    jpeg=encode_jpeg(frame,[{"xyxy":[50,30,150,80],"label":"내 물체","confidence":.9,"collection_candidate":True}],80)
    assert not frame.any()
    decoded=cv2.imdecode(np.frombuffer(jpeg,dtype=np.uint8),cv2.IMREAD_COLOR)
    assert decoded.shape==frame.shape and decoded.any()
