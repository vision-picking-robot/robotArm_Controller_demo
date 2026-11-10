"""One controller owns the camera, model transitions, collection and child jobs."""
import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

from .capture import camera_device
from .collection import Collector
from .config import ROOT
from .datasets import build_dataset, require_training_split, verify_dataset
from .packing import Packing
from .model_registry import BUILTIN, ModelRegistry, attach_benchmarks, coco_classes
from .store import DomainError, Store, atomic_json, now, uid

TERMINAL = {"completed", "completed_with_warnings", "failed", "cancelled", "interrupted"}


def artifact_hash(path):
    from scripts.common import digest
    return digest(Path(path)) if Path(path).exists() else None


class Runtime:
    def __init__(self, settings, pipeline_factory, state_root):
        self.settings = settings
        self.factory = pipeline_factory
        self.train_python = ROOT/".conda/train/bin/python"
        self.store = Store(state_root)
        self.registry = ModelRegistry(self.store)
        self.lock = threading.RLock()
        self.video_lock = threading.Lock()
        self.shutdown_lock = threading.Lock()
        self.closed = False
        self.stop_event = threading.Event()
        self.pipeline = None
        self.camera_desired = settings.camera_autostart
        self.busy = None
        self.error = None
        self.subscribers = 0
        self.process = None
        self.cancel_event = threading.Event()
        self.tasks = []
        self.active_job = None
        try:
            self.store.recover()
            self._register_defaults()
            self.registry.recover_versions()
            self.packing = Packing(self.store)
            self.collector = Collector(self.store)
        except Exception:
            self.store.close()
            raise

    @property
    def threads(self):
        return ([] if self.pipeline is None else self.pipeline.threads) + self.tasks + [self.collector.thread]

    def _register_defaults(self):
        formats = {}
        for backend in ("mps", "cpu", "coreml"):
            path = ROOT / "models" / ("yolov8n.mlpackage" if backend == "coreml" else "yolov8n.pt")
            if path.exists():
                formats[backend] = {"state": "ready", "path": str(path), "sha256": artifact_hash(path)}
        attach_benchmarks(formats)
        self.store.put("model", {"id": BUILTIN, "name": "COCO80 · YOLOv8n", "classes": coco_classes(),
                                 "kind": "builtin", "formats": formats, "created_at": "builtin"})
        current = self.settings.model_path.resolve()
        default = ROOT / "models" / ("yolov8n.mlpackage" if self.settings.backend == "coreml" else "yolov8n.pt")
        self.model_id = BUILTIN
        if current != default.resolve():
            self.model_id = "configured-" + __import__("hashlib").sha256(str(current).encode()).hexdigest()[:12]
            digest = artifact_hash(current)
            try:
                previous = self.store.get("model", self.model_id)
            except DomainError:
                previous = {}
            # Paths can be reused for different weights. Cache labels by content.
            same = digest and previous.get("formats", {}).get(self.settings.backend, {}).get("sha256") == digest
            self.store.put("model", {"id": self.model_id, "name": current.name, "kind": "configured",
                                     "classes": previous.get("classes", []) if same else [],
                                     "formats": {self.settings.backend: {"state": "ready" if digest else "unavailable",
                                                 "path": str(current), "sha256": digest}},
                                     "created_at": previous.get("created_at") or now()})
        if not self.settings.model_path_explicit:
            try:
                saved = self.store.get("setting", "selected-" + self.settings.backend)
                model = self.store.get("model", saved["model_id"])
                fmt = model["formats"].get(self.settings.backend, {})
                if fmt.get("state") == "ready" and Path(fmt["path"]).exists():
                    if fmt.get("sha256") and artifact_hash(fmt["path"]) != fmt["sha256"]:
                        self.error = "기억된 모델 파일의 해시가 변경되어 기본 모델로 시작합니다"
                    else:
                        self.model_id = model["id"]
                        self.settings = replace(self.settings, model_path=Path(fmt["path"]))
            except DomainError:
                pass

    def _new_pipeline(self, settings, model_id, camera=True):
        pipeline = self.factory(settings)
        pipeline.model_version = model_id
        pipeline.capture_enabled = camera
        pipeline.result_callback = self.collector.submit
        if model_id.startswith("configured-"):
            def save_labels(names):
                with self.store.lock:
                    model = self.store.get("model", model_id)
                    model["formats"][settings.backend]["state"] = "ready"
                    model["classes"] = [{"id": int(i), "name": name} for i, name in sorted(names.items())]
                    self.store.put("model", model)
            pipeline.labels_callback = save_labels
        with self.video_lock:
            pipeline.subscribe_video(self.subscribers)
            self.pipeline = pipeline
        pipeline.start()
        return pipeline

    def start(self):
        with self.lock:
            if self.camera_desired:
                self._new_pipeline(self.settings, self.model_id)

    def _stop_pipeline(self):
        if self.pipeline:
            self.pipeline.stop()
            if any(t.is_alive() for t in self.pipeline.threads):
                raise DomainError("기존 카메라/모델 작업자가 종료되지 않았습니다. 서버를 재시작하세요.")
            with self.video_lock:
                self.pipeline = None

    def _require_idle(self, allow_collection=False):
        if self.stop_event.is_set():
            raise DomainError("서버가 종료 중입니다")
        if self.busy:
            raise DomainError(f"{self.busy} 작업 중입니다")
        if not allow_collection and self.collector.snapshot()["id"]:
            raise DomainError("사진 저장을 먼저 중지하세요")

    def status(self):
        # Avoid blocking the event loop on a model stop/load or disk operation.
        pipeline = self.pipeline
        current = pipeline.status() if pipeline else {
            "schema_version": 1, "event": "status", "session_id": None, "process_id": os.getpid(),
            "state": "camera_off", "ready": False,
            "camera": {"online": False, "index": self.settings.camera_index, "id": self.settings.camera_id},
            "last_error": None}
        state = self.busy or ("camera_off" if not self.camera_desired else current["state"])
        if self.stop_event.is_set():
            state = "stopped"
        return {**current, "state": state, "ready": state == "ready" and current["ready"],
                "backend": self.settings.backend, "model": self.settings.model_path.name,
                "model_version": self.model_id, "camera_enabled": self.camera_desired,
                "active_job": self.active_job, "collection": self.collector.snapshot(),
                "last_error": self.error or current.get("last_error")}

    def latest_result(self):
        pipeline = self.pipeline
        return pipeline.latest_result() if pipeline and not self.busy and self.camera_desired else None

    def pick_frame(self, session_id=None, frame_id=None):
        with self.lock:
            if self.busy or not self.pipeline or not self.camera_desired or self.stop_event.is_set():
                raise DomainError("카메라가 탐지 중일 때 사용할 수 있습니다.", 503)
            return self.pipeline.pick_frame(session_id, frame_id)

    def latest_jpeg(self):
        pipeline = self.pipeline
        return pipeline.latest_jpeg() if pipeline and not self.busy and self.camera_desired else None

    def latest_preview(self, mode="detections", previous=None):
        pipeline = self.pipeline
        return pipeline.latest_preview(mode, previous) if pipeline and not self.busy and self.camera_desired else None

    def video_packet(self):
        pipeline = self.pipeline
        if not pipeline or self.busy or not self.camera_desired:
            return None
        jpeg = pipeline.latest_jpeg()
        return (pipeline.session_id, pipeline.model_version, *jpeg) if jpeg else None

    def subscribe_video(self, delta):
        # Stream subscription must never wait for camera/model teardown on self.lock.
        with self.video_lock:
            self.subscribers = max(0, self.subscribers + delta)
            if self.pipeline:
                self.pipeline.subscribe_video(delta)

    def camera_start(self):
        with self.lock:
            self._require_idle(allow_collection=True)
            self.camera_desired = True
            if self.pipeline and self.pipeline.fatal_error:
                self._stop_pipeline()
            if not self.pipeline:
                self._new_pipeline(self.settings, self.model_id)
            return self.status()

    def select_camera(self, device_id):
        with self.lock:
            self._require_idle()
            try:
                device = camera_device(device_id)
            except LookupError as exc:
                raise DomainError(str(exc), 409) from exc
            except RuntimeError as exc:
                raise DomainError(str(exc), 503) from exc
            if self.settings.camera_id == device_id and self.pipeline and not self.pipeline.stop_event.is_set():
                return self.camera_start()
            self.busy = "switching_camera"
            try:
                self._stop_pipeline()
                self.settings = replace(self.settings, camera_id=device_id, camera_index=device["index"])
                self.camera_desired = True
                self._new_pipeline(self.settings, self.model_id)
            finally:
                self.busy = None
            return self.status()

    def camera_stop(self):
        with self.lock:
            self.camera_desired = False  # cancels job completion auto-resume
            self.collector.stop("camera_off")
            self._stop_pipeline()
            return self.status()

    def create_collection(self, values):
        with self.lock:
            self._require_idle()
            cls = self.store.get("class", values["class_id"])
            self.store.get("group", values["group_id"])
            model = self.store.get("model", values["model_id"])
            fmt = model["formats"].get(self.settings.backend, {})
            if fmt.get("state") != "ready":
                raise DomainError("현재 백엔드에서 사용할 수 없는 수집 모델입니다", 422)
            if values["confidence"] < self.settings.confidence:
                raise DomainError("수집 confidence는 서버의 탐지 confidence 이상이어야 합니다", 422)
            return self.store.put("collection", {**values, "id": uid("collection"), "class_name": cls["name"],
                "model_hash": fmt.get("sha256"), "state": "created", "saved": 0, "created_at": now()})

    def start_collection(self, identifier):
        with self.lock:
            self._require_idle()
            collection = self.store.get("collection", identifier)
            if collection["model_id"] != self.model_id:
                raise DomainError("선택한 수집 모델을 먼저 적용하세요")
            if not self.status()["ready"]:
                raise DomainError("카메라와 탐지가 준비된 뒤 촬영을 시작하세요")
            self.collector.start(collection)
            return self.collector.snapshot()

    def stop_collection(self, identifier):
        with self.lock:
            value = self.store.get("collection", identifier)
            if self.collector.snapshot()["id"] == identifier:
                self.collector.stop()
            return self.store.get("collection", value["id"])

    def review_samples(self, identifiers, review):
        with self.lock, self.store.lock:
            # Resolve all first, so a bad id cannot partially apply a bulk review.
            samples = [self.store.get("sample", identifier) for identifier in identifiers]
            reviewed_at = now()
            self.store.put_many([("sample", {**sample, "review": review, "reviewed_at": reviewed_at})
                                 for sample in samples])
            return {"updated": len(samples), "review": review}

    def dataset(self):
        with self.lock:
            self._require_idle()
            return build_dataset(self.store)

    def _spawn(self, target, *args):
        thread = threading.Thread(target=target, args=args, daemon=True)
        self.tasks = [t for t in self.tasks if t.is_alive()]
        self.tasks.append(thread)
        thread.start()

    def activate(self, identifier):
        with self.lock:
            self._require_idle()
            model = self.store.get("model", identifier)
            fmt = model["formats"].get(self.settings.backend, {})
            if fmt.get("state") != "ready" or not Path(fmt.get("path", "")).exists():
                raise DomainError("현재 백엔드용 검증된 모델 형식이 없습니다", 422)
            if fmt.get("sha256") and artifact_hash(fmt["path"]) != fmt["sha256"]:
                raise DomainError("등록된 모델 파일의 해시가 변경되었습니다", 422)
            operation = self.store.put("operation", {"id": uid("operation"), "state": "running", "model_id": identifier,
                                                      "created_at": now()})
            self.busy = "switching_model"
            self._spawn(self._switch, operation, Path(fmt["path"]))
            return operation

    def _wait_model(self, pipeline):
        deadline = time.monotonic() + 90
        while not pipeline.model_ready:
            if pipeline.fatal_error:
                raise RuntimeError(pipeline.fatal_error)
            if self.stop_event.is_set() or pipeline.stop_event.is_set():
                raise RuntimeError("모델 적용이 중단되었습니다")
            if time.monotonic() >= deadline:
                raise TimeoutError("모델 초기화 시간 초과")
            time.sleep(.05)

    def _switch(self, operation, path):
        old_settings, old_model = self.settings, self.model_id
        try:
            with self.lock:
                self._stop_pipeline()
                pipeline = self._new_pipeline(replace(self.settings, model_path=path), operation["model_id"], self.camera_desired)
            self._wait_model(pipeline)
            with self.lock:
                if self.stop_event.is_set():
                    raise RuntimeError("서버 종료 중")
                if not self.camera_desired:
                    self._stop_pipeline()
                # Selection and successful operation must commit together. Otherwise
                # a failed switch can roll back live inference but load the new model
                # after a restart.
                self.store.put_many([
                    ("setting", {"id": "selected-"+pipeline.settings.backend, "model_id": operation["model_id"]}),
                    ("operation", {**self.store.get("operation", operation["id"]),
                                   "state": "completed", "ended_at": now()}),
                ])
                self.settings, self.model_id = pipeline.settings, operation["model_id"]
                self.error = None
        except Exception as exc:
            self.error = f"모델 적용 실패: {exc}"
            restored_model_id = None
            try:
                with self.lock:
                    self._stop_pipeline()
                    self.settings, self.model_id = old_settings, old_model
                    rollback = self._new_pipeline(old_settings, old_model) if self.camera_desired and not self.stop_event.is_set() else None
                if rollback:
                    self._wait_model(rollback)
                if not self.stop_event.is_set():
                    restored_model_id = old_model
            except Exception as recovery:
                self.error += f"; 이전 모델 복구 실패: {recovery}"
            self.store.update("operation", operation["id"], state="failed", error=self.error,
                              restored_model_id=restored_model_id, ended_at=now())
        finally:
            with self.lock:
                self.busy = None

    def create_job(self, values):
        with self.lock:
            self._require_idle()
            dataset = self.store.get("dataset", values["dataset_id"])
            require_training_split(dataset)
            try:
                verify_dataset(dataset)
            except (OSError, ValueError) as exc:
                raise DomainError(f"데이터셋 스냅샷 검증 실패: {exc}", 422) from exc
            initial = ROOT/"models/yolov8n.pt"
            if values.get("initial_model_id"):
                parent = self.store.get("model", values["initial_model_id"])
                if parent["classes"] != dataset["classes"] or parent["formats"].get("mps", {}).get("state") != "ready":
                    raise DomainError("같은 클래스 매핑을 가진 사용자 PT 모델만 초기 모델로 선택할 수 있습니다", 422)
                initial = Path(parent["formats"]["mps"]["path"])
                expected_hash = parent["formats"]["mps"].get("sha256")
                if expected_hash and artifact_hash(initial) != expected_hash:
                    raise DomainError("초기 사용자 모델 파일의 해시가 변경되었습니다", 422)
            if not initial.is_file():
                raise DomainError("로컬 초기 가중치가 없습니다. scripts.prepare를 실행하세요", 422)
            python = self.train_python
            if not python.is_file():
                raise DomainError("학습 환경이 없습니다. bash scripts/setup.sh train을 실행하세요", 422)
            job = {**values, "id": uid("job"), "state": "queued", "created_at": now(),
                   "initial_weights": str(initial), "initial_sha256": artifact_hash(initial),
                   "dataset": dataset, "model_id": uid("custom"), "error": None}
            job["directory"] = str(self.store.root/"jobs"/job["id"])
            Path(job["directory"]).mkdir(parents=True)
            atomic_json(Path(job["directory"])/"request.json", job)
            self.store.put("job", job)
            self.busy, self.active_job = "training", job["id"]
            self.cancel_event.clear()
            self._spawn(self._job, job, python)
            return job

    def _run_child(self, python, job, mode):
        directory = Path(job["directory"])
        # Clear the prior child's progress before polling, so export cannot briefly
        # regress to the training/evaluation stage while importing its runtime.
        atomic_json(directory/"progress.json", {"stage": "preparing" if mode == "train" else "exporting"})
        last_progress = None
        # EOF watchdog does not depend on PID reuse or graceful parent shutdown.
        read_fd, write_fd = os.pipe()
        process = None
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTORCH_ENABLE_MPS_FALLBACK": "0", "YOLO_AUTOINSTALL": "false", "YOLO_OFFLINE": "true"}
        try:
            with (directory/f"{mode}.log").open("w") as output:
                process = subprocess.Popen([str(python), "-m", "scripts.training_worker", "--request", str(directory/"request.json"),
                    "--mode", mode, "--parent-fd", str(read_fd)], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(read_fd,))
                with self.lock:
                    self.process = process
                os.close(read_fd)
                read_fd = None
                while process.poll() is None:
                    if self.cancel_event.wait(.2) or self.stop_event.is_set():
                        self._terminate(process)
                        raise InterruptedError("사용자가 작업을 취소했습니다")
                    progress = directory/"progress.json"
                    if progress.exists():
                        try:
                            data = json.loads(progress.read_text())
                            if data != last_progress:
                                self.store.update("job", job["id"], state=data["stage"],
                                                  progress={k: v for k, v in data.items() if k != "stage"})
                                last_progress = data
                        except (OSError, ValueError):
                            pass
                if self.cancel_event.is_set() or self.stop_event.is_set():
                    raise InterruptedError("작업 취소")
                if process.returncode:
                    detail = (directory/"error.json")
                    message = json.loads(detail.read_text())["error"] if detail.exists() else f"자식 프로세스 종료 코드 {process.returncode}"
                    raise RuntimeError(message)
        finally:
            try:
                # Includes errors in parent-side progress/DB handling, before the
                # child has necessarily installed its EOF watchdog.
                if process is not None:
                    self._terminate(process)
            finally:
                if read_fd is not None:
                    os.close(read_fd)
                os.close(write_fd)
                with self.lock:
                    self.process = None

    @staticmethod
    def _terminate(process):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        except ProcessLookupError:
            pass

    def _job(self, job, python):
        model = None
        try:
            with self.lock:
                self.collector.stop("training")
                self._stop_pipeline()
            self.store.update("job", job["id"], state="preparing")
            self._run_child(python, job, "train")
            result = json.loads((Path(job["directory"])/"pt-result.json").read_text())
            model = {"id": job["model_id"], "name": "사용자 모델 · " + job["created_at"][:19], "kind": "custom",
                     "created_at": now(), "classes": job["dataset"]["classes"], "dataset_id": job["dataset_id"],
                     "initial_model_id": job.get("initial_model_id"), "initial_sha256": job["initial_sha256"],
                     "job_id": job["id"], "training": result["training"], "environment": result["environment"],
                     "formats": {"mps": result["format"], "cpu": result["format"]}}
            # PT is kept even if export fails. Cancellation never registers a new version.
            warning = None
            self.store.update("job", job["id"], state="exporting")
            try:
                self._run_child(ROOT/".conda/coreml/bin/python", job, "export")
                exported = json.loads((Path(job["directory"])/"coreml-result.json").read_text())
                model["formats"]["coreml"] = exported
                if exported["state"] != "ready":
                    warning = exported["error"]
            except InterruptedError:
                raise
            except Exception as exc:
                warning = str(exc)
                model["formats"]["coreml"] = {"state": "unavailable", "error": warning}
            with self.lock:
                if self.cancel_event.is_set() or self.stop_event.is_set():
                    raise InterruptedError("작업 취소")
                completed = {**self.store.get("job", job["id"]), "state": "completed_with_warnings" if warning else "completed",
                             "warning": warning, "ended_at": now()}
                self.registry.publish(model, completed)
        except InterruptedError as exc:
            self.store.update("job", job["id"], state="cancelled", error=str(exc), ended_at=now())
        except Exception as exc:
            self.store.update("job", job["id"], state="failed", error=str(exc), ended_at=now())
        finally:
            with self.lock:
                self.busy, self.active_job = None, None
                if self.camera_desired and not self.stop_event.is_set() and self.pipeline is None:
                    self._new_pipeline(self.settings, self.model_id)

    def cancel_job(self, identifier):
        with self.lock:
            job = self.store.get("job", identifier)
            if job["state"] in TERMINAL:
                return job
            if identifier != self.active_job:
                raise DomainError("실행 중인 작업이 아닙니다")
            self.cancel_event.set()
            return self.store.update("job", identifier, state="cancelling")

    def events(self):
        return {"event": "snapshot", "status": self.status(), "collection": self.collector.snapshot(),
                "jobs": self.store.list("job"), "models": self.registry.models(),
                "operations": self.store.list("operation")}

    def stop(self):
        with self.shutdown_lock:
            if self.closed:
                return
            self.stop_event.set()
            self.cancel_event.set()
            errors = []
            with self.lock:
                self.camera_desired = False
                for cleanup in (self.collector.close, self._stop_pipeline):
                    try:
                        cleanup()
                    except Exception as exc:
                        errors.append(str(exc))
            for task in self.tasks:
                task.join(timeout=15)
            if any(task.is_alive() for task in self.tasks):
                errors.append("작업 종료 실패")
            else:
                self.store.close()
                self.closed = True
            if errors:
                raise RuntimeError("; ".join(errors))
