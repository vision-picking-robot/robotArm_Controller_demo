"""Owned subprocess entry point. No camera access, implicit downloads or CPU fallback."""
import argparse
import json
import math
import os
import signal
import threading
import time
from dataclasses import replace
from pathlib import Path

from app.config import Settings
from app.datasets import require_training_split, verify_dataset
from app.detector import Detector
from app.store import atomic_json, sha256
from scripts.common import digest, environment
from scripts.benchmark import accuracy, latency


def watch_parent(fd):
    def watch():
        try:
            while os.read(fd, 1):
                pass
        finally:
            # The launcher gives this worker its own process group.
            os.killpg(os.getpgrp(), signal.SIGTERM)
    threading.Thread(target=watch, name="parent-watchdog", daemon=True).start()


def progress(directory, stage, **values):
    atomic_json(directory/"progress.json", {"stage": stage, "updated_at": time.time(), **values})


def evaluate(job, model_path, backend):
    import cv2
    dataset = job["dataset"]
    root = Path(dataset["path"])
    paths = [root/i["image"] for i in dataset["items"] if i["split"] == "val"]
    labels = {str(root/i["image"]): root/i["label"] for i in dataset["items"] if i["split"] == "val"}
    detector = Detector(replace(Settings(), backend=backend, model_path=model_path, confidence=.001,
                                imgsz=640, warmup=3))
    try:
        detector.load()
        expected = {c["id"]: c["name"] for c in dataset["classes"]}
        if detector.names != expected:
            raise ValueError(f"Class mapping mismatch: {detector.names} != {expected}")
        detector.warmup()
        # Check every validation output, including empty predictions and original-image bounds.
        for path in paths:
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError("Unreadable validation image")
            h, w = frame.shape[:2]
            for obj in detector.predict(frame):
                if obj["class_id"] not in expected or not 0 <= obj["confidence"] <= 1 or not all(math.isfinite(v) for v in obj["xyxy"]):
                    raise ValueError("Invalid detection output")
                x1, y1, x2, y2 = obj["xyxy"]
                if not (0 <= x1 <= x2 <= w and 0 <= y1 <= y2 <= h):
                    raise ValueError("Detection is not in original-image coordinates")
        metrics = accuracy(detector, paths, label_paths=labels, note="User validation snapshot; not COCO128. Review label quality before interpreting accuracy.")
        detector.settings = replace(detector.settings, confidence=.25)
        speed = latency(detector, paths, iterations=50, warmup=10)
        return {"state": "ready", "path": str(model_path), "sha256": digest(model_path), "metrics": metrics,
                "latency": speed, "runtime": detector.metadata, "environment": environment(),
                "evaluation": {"confidence": .001, "iou": .45, "imgsz": 640, "max_det": 100, "rect": False}}
    finally:
        detector.close()


def fixed_batch_start(trainer, expected):
    # Disable 8.4.202's first-epoch OOM batch halving before forward/backward.
    trainer._oom_retries = 3
    if trainer.args.batch != expected or trainer.batch_size != expected:
        raise RuntimeError("Batch changed unexpectedly; retry explicitly with a smaller batch")


def fixed_batch_end(trainer):
    trainer._oom_retries = 0
    if any(not math.isfinite(float(v)) for v in trainer.loss_items.values()):
        raise ValueError("Non-finite loss; no automatic training retry")


def train(job, directory):
    import torch
    import ultralytics
    from ultralytics import YOLO
    if ultralytics.__version__ != "8.4.202":
        raise RuntimeError("학습 제어는 ultralytics==8.4.202에서 검증했습니다. 환경 고정 파일을 사용하세요.")
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS를 사용할 수 없습니다. CPU로 자동 전환하지 않습니다.")
    require_training_split(job["dataset"])
    verify_dataset(job["dataset"])
    if sha256(job["initial_weights"]) != job["initial_sha256"]:
        raise ValueError("Initial weights changed")
    progress(directory, "preparing")
    from ultralytics.data import utils as data_utils
    # plots=False; avoid the dataset loader downloading Unicode plot fonts.
    data_utils.check_font = lambda *args, **kwargs: None
    model = YOLO(job["initial_weights"], task="detect")
    options = {"data": str(Path(job["dataset"]["path"])/"data.yaml"), "device": "mps", "imgsz": 640,
               "batch": job["batch"], "epochs": job["epochs"], "patience": job["patience"], "workers": 0,
               "cache": False, "seed": 42, "deterministic": True, "amp": False,
               "project": str(directory), "name": "run", "exist_ok": False, "plots": False,
               "save": True, "val": True, "close_mosaic": 0, "optimizer": "AdamW"}
    def epoch_end(trainer):
        losses = {str(k): float(v) for k, v in (trainer.tloss or {}).items()}
        metrics = {str(k): float(v) for k, v in trainer.metrics.items()}
        progress(directory, "training", epoch=trainer.epoch+1, epochs=job["epochs"], losses=losses, metrics=metrics,
                 batch=trainer.args.batch)
    model.add_callback("on_train_start", lambda t: progress(directory, "training", epoch=0, epochs=job["epochs"], batch=t.args.batch))
    model.add_callback("on_train_batch_start", lambda t: fixed_batch_start(t, options["batch"]))
    model.add_callback("on_train_batch_end", fixed_batch_end)
    model.add_callback("on_fit_epoch_end", epoch_end)
    model.train(**options)
    best = Path(model.trainer.best)
    if not best.is_file() or best.stat().st_size == 0:
        raise RuntimeError("Completed best.pt was not produced")
    actual = dict(vars(model.trainer.args))
    del model
    torch.mps.empty_cache()
    progress(directory, "evaluating")
    result = evaluate(job, best, "mps")
    atomic_json(directory/"pt-result.json", {"format": result, "training": {"requested": options, "actual": actual,
               "oom_policy": "fail; no batch reduction or CPU fallback"}, "environment": environment()})


def conversion_accepted(pt_map, coreml_map):
    return math.isfinite(pt_map) and math.isfinite(coreml_map) and pt_map - coreml_map <= .01 + 1e-12


def export(job, directory):
    from ultralytics import YOLO
    from ultralytics.cfg import DEFAULT_CFG_DICT
    require_training_split(job["dataset"])
    verify_dataset(job["dataset"])
    previous = json.loads((directory/"pt-result.json").read_text())
    weights = Path(previous["format"]["path"])
    if digest(weights) != previous["format"]["sha256"]:
        raise ValueError("PT artifact changed before conversion")
    progress(directory, "exporting")
    precision = {"quantize": 16} if "quantize" in DEFAULT_CFG_DICT else {"half": True}
    options = {"format": "coreml", "imgsz": 640, "batch": 1, "dynamic": False, "device": "cpu", **precision}
    # Pinned Ultralytics emits raw output when nms is omitted; Detector verifies its dimensions.
    artifact = Path(YOLO(str(weights), task="detect").export(**options)).resolve()
    result = evaluate(job, artifact, "coreml")
    loss = previous["format"]["metrics"]["map50_95"] - result["metrics"]["map50_95"]
    result.update(export_options=options, nms="raw output + common Ultralytics NMS", map_drop_percentage_points=loss*100,
                  source_sha256=previous["format"]["sha256"])
    if not conversion_accepted(previous["format"]["metrics"]["map50_95"], result["metrics"]["map50_95"]):
        result.update(state="unavailable", error=f"Core ML mAP50–95 감소 {loss*100:.3f}%p가 1%p를 초과했습니다")
    atomic_json(directory/"coreml-result.json", result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--mode", choices=("train", "export"), required=True)
    parser.add_argument("--parent-fd", type=int, required=True)
    args = parser.parse_args()
    watch_parent(args.parent_fd)
    job = json.loads(args.request.read_text())
    directory = args.request.parent
    (directory/"error.json").unlink(missing_ok=True)
    try:
        (train if args.mode == "train" else export)(job, directory)
    except Exception as exc:
        message = str(exc)
        if "out of memory" in message.lower():
            message += " / 메모리 부족: batch를 2 또는 1로 줄여 새 작업으로 재시도하세요."
        atomic_json(directory/"error.json", {"error": message})
        raise


if __name__ == "__main__":
    main()
