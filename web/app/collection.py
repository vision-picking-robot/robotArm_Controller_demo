"""Frame-aligned collection: a bounded writer, a stop barrier and a browser lease."""
import math
import logging
import os
import queue
import threading
import time
from collections import Counter

from .store import DomainError, now, sha256, uid

log = logging.getLogger(__name__)


def box_iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return intersection / max(1e-9, (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection)


def valid_box(box, width, height):
    if len(box) != 4 or not all(math.isfinite(float(x)) for x in box):
        return None
    x1, y1, x2, y2 = box
    box = [max(0., min(float(x1), width)), max(0., min(float(y1), height)),
           max(0., min(float(x2), width)), max(0., min(float(y2), height))]
    return box if box[2] > box[0] and box[3] > box[1] else None


def normalized(box, width, height):
    x1, y1, x2, y2 = box
    return [(x1+x2)/2/width, (y1+y2)/2/height, (x2-x1)/width, (y2-y1)/height]


def dhash(frame):
    import cv2
    small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (9, 8))
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return f"{sum(int(bit) << i for i, bit in enumerate(bits)):016x}"


class Collector:
    def __init__(self, store, write=None):
        self.store = store
        self.condition = threading.Condition(threading.RLock())
        self.queue = queue.Queue(maxsize=8)
        self.shutdown = threading.Event()
        self.active = None
        self.stopping = None
        self.inflight = False
        self.write = write or self._write
        self.thread = threading.Thread(target=self._run, name="yolo-photo-writer", daemon=True)
        self.thread.start()

    def start(self, collection):
        with self.condition:
            if self.active or self.stopping or self.inflight or self.shutdown.is_set():
                raise DomainError("사진 수집 또는 저장 마무리 중입니다")
            saved = len([s for s in self.store.list("sample") if s["collection_id"] == collection["id"]])
            # Do not allow any frame to be saved if starting the session fails.
            self.store.update("collection", collection["id"], state="collecting", error=None)
            self.config = {**collection, "run_token": uid("run")}
            self.heartbeat_at = time.monotonic()
            self.last_seen = None
            self.seen_session = None
            self.highest_frame = -1
            self.last_box = None
            self.last_class = None
            self.streak = 0
            self.last_saved_at = 0.
            self.last_frame_at = 0.
            self.reason = "stabilizing"
            self.error = None
            self.counts = Counter()
            self.saved = saved
            self.active = collection["id"]

    def heartbeat(self, identifier):
        with self.condition:
            if identifier != self.active:
                raise DomainError("촬영이 중지되었습니다. 시작 버튼으로 다시 시작하세요")
            self.heartbeat_at = time.monotonic()
            return self.snapshot()

    def snapshot(self):
        with self.condition:
            return {"id": self.active or self.stopping,
                    "state": "stopping" if self.stopping else ("collecting" if self.active else "stopped"),
                    "reason": getattr(self, "reason", "stopped"), "saved": getattr(self, "saved", 0),
                    "error": getattr(self, "error", None),
                    "skipped": dict(getattr(self, "counts", {})), "queue_size": self.queue.qsize(),
                    "inflight": self.inflight}

    def _stop_status(self, identifier, **changes):
        try:
            self.store.update("collection", identifier, **changes)
        except Exception as exc:
            # Releasing the camera and finishing the in-flight write must not depend
            # on a successful SQLite commit (e.g. disk full during shutdown).
            self.error = str(exc)
            log.exception("Could not persist collection stop status")

    def stop(self, reason="user_stopped", expected_run=None):
        with self.condition:
            if expected_run is not None and (not self.active or self.config["run_token"] != expected_run
                                             or time.monotonic() - self.heartbeat_at <= 10):
                return self.snapshot()
            if self.stopping:
                self.condition.wait_for(lambda: self.stopping is None)
                return self.snapshot()
            identifier = self.active
            self.active = None  # barrier: no producer or writer can begin another sample
            self.stopping = identifier
            self.reason = reason
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            if identifier:
                self._stop_status(identifier, state="stopping", stop_reason=reason)
            self.condition.wait_for(lambda: not self.inflight)
            if identifier:
                self._stop_status(identifier, state="stopped", saved=self.saved,
                                  skipped=dict(self.counts), stop_reason=reason, ended_at=now(), error=self.error)
            self.stopping = None
            self.condition.notify_all()
            return self.snapshot()

    def submit(self, packet, result):
        with self.condition:
            if not self.active:
                return
            key = (result["session_id"], result["frame_id"])
            if key[0] != self.seen_session:
                self.seen_session, self.highest_frame = key[0], -1
                self.streak, self.last_box = 0, None
            if key[1] <= self.highest_frame:
                self.counts["duplicate_frame"] += 1
                return
            self.last_seen = key
            self.highest_frame = key[1]
            age = (time.monotonic_ns() - packet.captured_ns) / 1e9
            reason, candidates = None, []
            width, height = packet.frame.shape[1], packet.frame.shape[0]
            for obj in result["objects"]:
                box = valid_box(obj["xyxy"], width, height)
                if box is None or not math.isfinite(obj["confidence"]):
                    reason = "invalid_box"
                    continue
                if obj["confidence"] < self.config["confidence"]:
                    reason = reason or "low_confidence"
                    continue
                x1, y1, x2, y2 = box
                if .3*width <= (x1+x2)/2 <= .7*width and .3*height <= (y1+y2)/2 <= .7*height and x1 <= width/2 <= x2 and y1 <= height/2 <= y2:
                    candidates.append({**obj, "xyxy": box})
                else:
                    reason = reason or "outside_center"
            if age < 0 or age > .5:
                reason = "stale"
            elif len(candidates) > 1:
                reason = "ambiguous"
            elif len(candidates) == 1:
                reason = None
            else:
                reason = reason or "no_detection"
            if reason:
                self.streak, self.last_box = 0, None
                self.reason = reason
                self.counts[reason] += 1
                return
            candidate = candidates[0]
            for original in result["objects"]:
                if original["class_id"] == candidate["class_id"] and valid_box(original["xyxy"], width, height) == candidate["xyxy"]:
                    original["collection_candidate"] = True
            captured = packet.captured_ns / 1e9
            consistent = (self.last_box is not None and self.last_class == candidate["class_id"] and
                          box_iou(self.last_box, candidate["xyxy"]) >= .5 and
                          0 < captured - self.last_frame_at <= .5)
            self.streak = self.streak + 1 if consistent else 1
            self.last_box, self.last_class = candidate["xyxy"], candidate["class_id"]
            self.last_frame_at = captured
            if self.streak < 3:
                self.reason = "stabilizing"
                return
            if captured - self.last_saved_at < self.config["interval_ms"] / 1000:
                self.reason = "interval"
                return
            try:
                # Inference frame is immutable; the renderer draws on its own copy.
                self.queue.put_nowait((dict(self.config), packet, result, candidate))
                self.last_saved_at = captured
                self.reason = "saving"
            except queue.Full:
                self.reason = "queue_full"
                self.counts["queue_full"] += 1

    def _write(self, collection, packet, result, obj):
        import cv2
        identifier = uid("sample")
        directory = self.store.root / "collections" / collection["id"]
        directory.mkdir(parents=True, exist_ok=True)
        image, label = directory / f"{identifier}.jpg", directory / f"{identifier}.txt"
        temp_image, temp_label = image.with_suffix(".jpg.tmp"), label.with_suffix(".txt.tmp")
        h, w = packet.frame.shape[:2]
        values = normalized(obj["xyxy"], w, h)
        try:
            ok, encoded = cv2.imencode(".jpg", packet.frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not ok:
                raise OSError("JPEG encoding failed")
            for path, data in ((temp_image, encoded.tobytes()), (temp_label, ("0 " + " ".join(f"{v:.10f}" for v in values) + "\n").encode())):
                with path.open("wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            temp_image.replace(image)
            temp_label.replace(label)
            return self.store.put("sample", {"id": identifier, "collection_id": collection["id"],
                "class_id": collection["class_id"], "group_id": collection["group_id"],
                "teacher_model": collection["model_id"], "teacher_hash": collection["model_hash"],
                "session_id": result["session_id"], "frame_id": packet.frame_id, "captured_at": packet.captured_at,
                "width": w, "height": h, "box": obj["xyxy"], "normalized_box": values,
                "prediction": obj, "review": "pending", "sha256": sha256(image), "label_sha256": sha256(label),
                "dhash": dhash(packet.frame), "image": str(image.relative_to(self.store.root)),
                "label": str(label.relative_to(self.store.root)), "created_at": now()})
        except Exception:
            for path in (temp_image, temp_label, image, label):
                path.unlink(missing_ok=True)
            raise

    def _run(self):
        while not self.shutdown.is_set():
            expired = None
            with self.condition:
                if self.active and time.monotonic() - self.heartbeat_at > 10:
                    expired = self.config["run_token"]
            if expired:
                self.stop("heartbeat_expired", expected_run=expired)
                continue
            try:
                item = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            with self.condition:
                if self.active != item[0]["id"] or self.config["run_token"] != item[0].get("run_token"):
                    continue
                if (time.monotonic_ns() - item[1].captured_ns) / 1e9 > .5:
                    self.reason = "stale"
                    self.counts["stale"] += 1
                    continue
                self.inflight = True
            error = None
            try:
                self.write(*item)
                with self.condition:
                    self.saved += 1
                    self.store.update("collection", item[0]["id"], saved=self.saved)
            except Exception as exc:
                error = str(exc)
            finally:
                with self.condition:
                    if error:
                        self.error, self.reason = error, "disk_error"
                        self.active = None
                        while True:
                            try:
                                self.queue.get_nowait()
                            except queue.Empty:
                                break
                        try:
                            self.store.update("collection", item[0]["id"], error=error, state="stopped",
                                              saved=self.saved, stop_reason="disk_error", ended_at=now())
                        except Exception:
                            # A full/read-only disk can also prevent metadata updates. Live state
                            # still stops collection; restart recovery handles the old DB record.
                            pass
                    self.inflight = False
                    self.condition.notify_all()

    def close(self):
        try:
            self.stop("server_shutdown")
        finally:
            self.shutdown.set()
            self.thread.join()
