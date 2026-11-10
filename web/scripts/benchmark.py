"""Reproducible offline latency and COCO128 conversion comparisons (no camera recording)."""
import argparse
import time
from dataclasses import replace
from pathlib import Path

from app.config import ROOT, Settings
from app.detector import Detector
from app.pipeline import percentile
from .common import digest, environment, write_json


def image_paths():
    paths = sorted((ROOT / "data/coco128/images/train2017").glob("*.jpg"))
    if len(paths) != 128:
        raise FileNotFoundError("Run python -m scripts.prepare to obtain all 128 COCO128 images")
    return paths


def latency(detector, paths, iterations, warmup):
    import cv2
    for i in range(warmup):
        detector.predict(cv2.imread(str(paths[i % len(paths)])))
    samples = []
    total_start = time.perf_counter()
    for i in range(iterations):
        frame = cv2.imread(str(paths[i % len(paths)]))
        start = time.perf_counter_ns()
        detector.predict(frame)  # Includes materialization of CPU detection results.
        samples.append((time.perf_counter_ns() - start) / 1e6)
        if (i + 1) % 100 == 0:
            print(f"{i + 1}/{iterations}", flush=True)
    duration = time.perf_counter() - total_start
    return {"iterations": iterations, "warmup": warmup,
            "predict_ms": {"p50": percentile(samples, .5), "p95": percentile(samples, .95),
                           "mean": sum(samples) / len(samples)},
            "offline_fps_including_image_decode": iterations / duration,
            "duration_seconds": duration,
            "note": "Offline images, not camera or browser latency. Warmup excluded."}


def accuracy(detector, paths, label_paths=None, note=None):
    import cv2
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    truth = {"info": {"description": "COCO128 YOLO labels converted for export parity"},
             "images": [], "annotations": [],
             "categories": [{"id": int(k) + 1, "name": name} for k, name in detector.names.items()]}
    predictions = []
    for image_id, path in enumerate(paths, 1):
        frame = cv2.imread(str(path))
        h, w = frame.shape[:2]
        truth["images"].append({"id": image_id, "width": w, "height": h, "file_name": path.name})
        label_path = label_paths[str(path)] if label_paths is not None else ROOT / "data/coco128/labels/train2017" / (path.stem + ".txt")
        # An absent/empty label file represents a negative image in YOLO format.
        lines = label_path.read_text().splitlines() if label_path.exists() else []
        for line in lines:
            if not line.strip():
                continue
            class_id, cx, cy, bw, bh = map(float, line.split())
            bbox = [(cx - bw / 2) * w, (cy - bh / 2) * h, bw * w, bh * h]
            truth["annotations"].append({"id": len(truth["annotations"]) + 1, "image_id": image_id,
                                         "category_id": int(class_id) + 1, "bbox": bbox,
                                         "area": bbox[2] * bbox[3], "iscrowd": 0})
        for obj in detector.predict(frame):
            x1, y1, x2, y2 = obj["xyxy"]
            predictions.append({"image_id": image_id, "category_id": obj["class_id"] + 1,
                                "bbox": [x1, y1, x2 - x1, y2 - y1], "score": obj["confidence"]})
    ground_truth = COCO()
    ground_truth.dataset = truth
    ground_truth.createIndex()
    if predictions:
        detected = ground_truth.loadRes(predictions)
    else:
        detected = COCO()
        detected.dataset = {**truth, "annotations": []}
        detected.createIndex()
    evaluation = COCOeval(ground_truth, detected, "bbox")
    evaluation.params.imgIds = list(range(1, len(paths) + 1))
    evaluation.params.maxDets = [1, 10, detector.settings.max_det]
    evaluation.evaluate()
    evaluation.accumulate()
    evaluation.summarize()
    # Operational precision/recall at confidence .25 and IoU .50, separate from low-conf mAP.
    from app.collection import box_iou
    diagnostics = []
    operational = {}
    for category in truth["categories"]:
        cid = category["id"]
        tp = fp = fn = 0
        for info in truth["images"]:
            image_id = info["id"]
            boxes = [a["bbox"] for a in truth["annotations"] if a["image_id"] == image_id and a["category_id"] == cid]
            targets = [[x, y, x+w, y+h] for x, y, w, h in boxes]
            used = set()
            guesses = sorted([p for p in predictions if p["image_id"] == image_id and p["category_id"] == cid and p["score"] >= .25], key=lambda p: -p["score"])
            image_fp = 0
            for pred in guesses:
                x, y, w, h = pred["bbox"]
                matches = [(box_iou([x,y,x+w,y+h], box), i) for i,box in enumerate(targets) if i not in used]
                best = max(matches, default=(0, -1))
                if best[0] >= .5:
                    tp += 1
                    used.add(best[1])
                else:
                    fp += 1
                    image_fp += 1
            missing = len(targets)-len(used)
            fn += missing
            if (image_fp or missing) and len(diagnostics) < 20:
                diagnostics.append({"image": info["file_name"], "class_id": cid-1, "false_positives": image_fp, "false_negatives": missing})
        operational[cid] = {"precision_at_025_iou50": tp/(tp+fp) if tp+fp else 0.,
                            "recall_at_025_iou50": tp/(tp+fn) if tp+fn else 0., "tp": tp, "fp": fp, "fn": fn}
    per_class = []
    for index, category in enumerate(truth["categories"]):
        precision = evaluation.eval["precision"][:, :, index, 0, -1]
        recall = evaluation.eval["recall"][:, index, 0, -1]
        valid = precision[precision >= 0]
        per_class.append({**operational[category["id"]], "class_id": category["id"] - 1, "name": category["name"],
                          "map50_95": float(valid.mean()) if valid.size else None,
                          "recall": float(recall[recall >= 0].mean()) if (recall >= 0).any() else None})
    return {"images": len(paths), "detections": len(predictions), "per_class": per_class, "error_examples": diagnostics,
            "map50_95": float(evaluation.stats[0]), "map50": float(evaluation.stats[1]),
            "recall_at_max_det": float(evaluation.stats[8]),
            "note": note or "COCO128 train-derived sample; conversion sanity check, not held-out deployment accuracy."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["mps", "cpu", "coreml"], required=True)
    parser.add_argument("--mode", choices=["latency", "accuracy"], default="latency")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.iterations <= 0 or args.warmup < 0:
        parser.error("iterations must be positive and warmup non-negative")
    model = ROOT / "models" / ("yolov8n.mlpackage" if args.backend == "coreml" else "yolov8n.pt")
    config = replace(Settings(), backend=args.backend, model_path=model, imgsz=args.imgsz,
                     confidence=.001 if args.mode == "accuracy" else .25, warmup=0)
    detector = Detector(config)
    started = time.perf_counter()
    try:
        detector.load()
        load_seconds = time.perf_counter() - started
        paths = image_paths()
        result = latency(detector, paths, args.iterations, args.warmup) if args.mode == "latency" else accuracy(detector, paths)
        record = {"mode": args.mode, "backend": args.backend, "environment": environment(),
                  "runtime": detector.metadata, "model_sha256": digest(model),
                  "dataset_sha256": digest(ROOT / "data/coco128"),
                  "settings": {"imgsz": config.imgsz, "rect": False, "confidence": config.confidence,
                               "iou": config.iou, "max_det": config.max_det, "batch": 1},
                  "load_seconds": load_seconds, "results": result}
        output = args.output or ROOT / f"artifacts/{args.backend}-{args.mode}.json"
        write_json(output, record)
        print(f"Saved: {output}")
    finally:
        detector.close()


if __name__ == "__main__":
    main()
