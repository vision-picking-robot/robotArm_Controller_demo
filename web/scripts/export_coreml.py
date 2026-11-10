import argparse
from dataclasses import replace
from pathlib import Path

from app.config import ROOT, Settings
from app.detector import Detector
from .common import digest, environment, update_manifest


def main():
    parser = argparse.ArgumentParser(description="Export a raw-output FP16 YOLOv8 Core ML model")
    parser.add_argument("--imgsz", type=int, default=640)
    args = parser.parse_args()
    Settings(imgsz=args.imgsz)  # Validate before export.
    weights = ROOT / "models/yolov8n.pt"
    if not weights.exists():
        raise FileNotFoundError("Run python -m scripts.prepare first")
    from ultralytics import YOLO
    from ultralytics.cfg import DEFAULT_CFG_DICT
    # Ultralytics changed export precision options across releases. Record the actual API used.
    precision = {"quantize": 16} if "quantize" in DEFAULT_CFG_DICT else {"half": True}
    options = {"format": "coreml", "imgsz": args.imgsz, "batch": 1,
               "dynamic": False, "device": "cpu", **precision}
    # Omit nms: older versions default to False, newer versions to None (raw YOLOv8 output).
    artifact = Path(YOLO(str(weights), task="detect").export(**options)).resolve()
    detector = Detector(replace(Settings(), backend="coreml", model_path=artifact, imgsz=args.imgsz))
    try:
        detector.load()
        detector.warmup()  # Verify raw shape, labels, preprocessing, NMS and CPU_AND_NE loading.
        record = {"path": str(artifact.relative_to(ROOT)), "sha256": digest(artifact),
                  "source_sha256": digest(weights), "export_options": options,
                  "nms": "raw output (export default)", "runtime": detector.metadata,
                  "environment": environment()}
        update_manifest("coreml", record)
    finally:
        detector.close()
    print(f"Validated Core ML export: {artifact}")


if __name__ == "__main__":
    main()
