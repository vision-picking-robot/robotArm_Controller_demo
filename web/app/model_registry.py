"""Display metadata and immutable version lineage for locally registered weights."""
import json
from importlib.metadata import distribution
from pathlib import Path

import yaml

from .config import ROOT
from .packing import Packing
from .store import DomainError, atomic_json

BUILTIN = "coco-yolov8n"


def coco_classes():
    # Read the same COCO mapping shipped with the pinned Ultralytics installation, or fallback.
    try:
        path = distribution("ultralytics").locate_file("ultralytics/cfg/datasets/coco.yaml")
        names = yaml.safe_load(path.read_text())["names"]
        return [{"id": index, "name": name} for index, name in sorted(names.items())]
    except Exception:
        coco_names = [
            "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
            "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
            "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
            "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
            "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
            "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
            "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
            "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
            "scissors", "teddy bear", "hair drier", "toothbrush"
        ]
        return [{"id": index, "name": name} for index, name in enumerate(coco_names)]


def json_report(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def attach_benchmarks(formats):
    dataset = json_report(ROOT / "models/manifest.json").get("dataset", {})
    if dataset.get("name") != "COCO128" or not dataset.get("sha256"):
        return
    for backend, fmt in formats.items():
        for mode, key in (("accuracy", "metrics"), ("latency", "latency")):
            report = json_report(ROOT / "artifacts" / f"{backend}-{mode}.json")
            if (isinstance(report.get("results"), dict)
                    and report.get("backend") == backend and report.get("mode") == mode
                    and report.get("dataset_sha256") == dataset["sha256"]
                    and report.get("model_sha256") == fmt["sha256"]):
                fmt[key] = report["results"]
                fmt["evaluation_dataset"] = "COCO128"


class ModelRegistry:
    def __init__(self, store):
        self.store = store

    def models(self):
        with self.store.lock:
            names = {a["model_id"]: a["name"] for a in self.store.list("model_alias")}
            return [{**model, "name": names.get(model["id"], model.get("name", model["id"]))}
                    for model in self.store.list("model")]

    def rename(self, identifier, name):
        name = name.strip()
        if not 1 <= len(name) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise DomainError("이름은 제어 문자 없이 1~80자로 입력하세요", 422)
        with self.store.write_transaction() as write:
            model = self.store.get("model", identifier)
            alias = self.store.db.execute("SELECT name FROM model_aliases WHERE model_id="
                                          "(SELECT id FROM models WHERE public_id=?)", (identifier,)).fetchone()
            old = alias[0] if alias else model.get("name") or identifier
            write("model_alias", {"id": "alias-" + identifier, "model_id": identifier, "name": name})
            if old != name:
                Packing.log(write, "모델", f"{old} → {name} · 모델 이름 변경")
        return {**model, "name": name}

    @staticmethod
    def _lineage(model, parent_id, models):
        if parent_id == BUILTIN:
            return {"parent_model_id": BUILTIN, "family_id": model["id"], "version_number": 1}
        parent = models.get(parent_id, {})
        family = parent.get("family_id")
        if family:
            latest = max(m["version_number"] for m in models.values() if m.get("family_id") == family)
            return {"parent_model_id": parent_id, "family_id": family, "version_number": latest + 1}
        return {"parent_model_id": parent_id, "family_id": None, "version_number": None}

    @staticmethod
    def _legacy_parent(model, job, models):
        digest = model.get("initial_sha256")
        if not digest or digest != job.get("initial_sha256") or job.get("model_id") != model["id"]:
            return None
        if model.get("initial_model_id") != job.get("initial_model_id"):
            return None
        parent_id = job.get("initial_model_id") or BUILTIN
        parent = models.get(parent_id, {})
        if parent.get("formats", {}).get("mps", {}).get("sha256") == digest:
            return parent_id
        return None

    def recover_versions(self):
        """Recover proven ancestry in parent order; leave incomplete records unnumbered."""
        with self.store.write_transaction() as write:
            models = {m["id"]: m for m in self.store.list("model")}
            pending = {i: m for i, m in models.items() if m.get("kind") == "custom" and not m.get("version_number")}
            if not pending:
                return
            # Load only referenced jobs; they carry the dataset counts at training time.
            parents = {}
            for identifier, model in pending.items():
                try:
                    job = self.store.get("job", model.get("job_id"))
                except DomainError:
                    job = {}
                parents[identifier] = self._legacy_parent(model, job, models)
                counts = job.get("dataset", {}).get("counts")
                if counts and model.get("dataset_counts") != counts:
                    model["dataset_counts"] = counts
                    write("model", model)
            while pending:
                resolved = [i for i in pending if parents[i] not in pending]
                if not resolved:  # Corrupted/cyclic links cannot establish a version.
                    break
                for identifier in resolved:
                    model = pending.pop(identifier)
                    lineage = self._lineage(model, parents[identifier], models)
                    if any(model.get(k) != v for k, v in lineage.items()):
                        model.update(lineage)
                        write("model", model)

    def publish(self, model, job):
        """Allocate a number only when the successful training result is committed."""
        with self.store.write_transaction() as write:
            models = {m["id"]: m for m in self.store.list("model")}
            if model["id"] in models:
                return  # Replaying a completed publication never consumes a number.
            parent_id = job.get("initial_model_id") or BUILTIN
            model.update(self._lineage(model, parent_id, models))
            model["dataset_counts"] = job["dataset"]["counts"]
            write("job", job)
            write("model", model)
            atomic_json(Path(job["directory"]) / "model.json", model)
