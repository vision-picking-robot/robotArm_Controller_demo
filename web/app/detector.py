"""MPS uses Ultralytics; Core ML uses the same LetterBox, NMS and coordinate scaling.

Core ML is loaded explicitly with CPU_AND_NE to avoid relying on a wrapper's
version-dependent compute-unit default. No inference path downloads weights.
"""
import ast
import json
from pathlib import Path


class Detector:
    def __init__(self, settings):
        self.settings = settings
        self.model = None
        self.names = {}
        self.metadata = {}

    def load(self):
        s = self.settings
        if s.backend == "mock":
            self._init_mock()
            return
        if not s.model_path.exists():
            raise FileNotFoundError(f"모델이 없습니다: {s.model_path}. scripts/prepare.py를 먼저 실행하세요.")
        if s.backend == "coreml":
            self._load_coreml()
        else:
            try:
                import torch
                from ultralytics import YOLO
                if s.backend == "mps" and not torch.backends.mps.is_available():
                    raise RuntimeError("MPS를 사용할 수 없습니다. arm64 PyTorch와 macOS를 확인하세요. CPU 진단은 YOLO_BACKEND=cpu로 실행하세요.")
                self.model = YOLO(str(s.model_path), task="detect")
                self.names = self.model.names
                self.metadata = {"backend": f"pytorch-{s.backend}", "compute_units": s.backend}
            except Exception:
                raise  # A failed real model must never become synthetic detections.

    def _init_mock(self):
        self.names = {0: "snack_box", 1: "pet_bottle", 2: "can", 3: "wet_tissue", 4: "doll"}
        self.metadata = {"backend": "mock-yolo", "compute_units": "cpu-synthetic"}
        self.model = "mock"

    def _load_coreml(self):
        import coremltools as ct
        self.model = ct.models.MLModel(str(self.settings.model_path), compute_units=ct.ComputeUnit.CPU_AND_NE)
        spec = self.model.get_spec()
        inputs = [v for v in spec.description.input if v.type.WhichOneof("Type") == "imageType"]
        if len(inputs) != 1 or len(spec.description.output) != 1:
            raise ValueError("Expected a raw YOLOv8 Core ML model with one image input and one output; export without NMS.")
        image_type = inputs[0].type.imageType
        if (image_type.width, image_type.height) != (self.settings.imgsz, self.settings.imgsz):
            raise ValueError("YOLO_IMGSZ does not match the static Core ML input. Re-export this input size.")
        self.input_name = inputs[0].name
        self.output_name = spec.description.output[0].name
        names = self.model.user_defined_metadata.get("names")
        if not names:
            raise ValueError("Core ML model has no class-name metadata; use scripts/export_coreml.py")
        try:
            parsed = json.loads(names)
        except json.JSONDecodeError:
            parsed = ast.literal_eval(names)
        self.names = {int(k): v for k, v in (enumerate(parsed) if isinstance(parsed, list) else parsed.items())}
        self.metadata = {"backend": "coreml", "compute_units": str(self.model.compute_unit),
                         "input_name": self.input_name, "output_name": self.output_name,
                         "precision": "FP16", "embedded_nms": False}

    def warmup(self):
        import numpy as np
        for _ in range(self.settings.warmup):
            self.predict(np.zeros((self.settings.height, self.settings.width, 3), dtype=np.uint8))

    def predict(self, frame):
        if self.model is None:
            raise RuntimeError("Detector is not loaded")
        if self.model == "mock":
            return [
                {"class_id": 0, "label": "snack_box", "confidence": 0.94, "xyxy": [150.0, 120.0, 350.0, 400.0]},
                {"class_id": 1, "label": "pet_bottle", "confidence": 0.91, "xyxy": [450.0, 150.0, 600.0, 480.0]},
                {"class_id": 2, "label": "can", "confidence": 0.88, "xyxy": [700.0, 200.0, 880.0, 380.0]},
            ]
        s = self.settings
        if s.backend == "coreml":
            rows = self._predict_coreml(frame)
        else:
            result = self.model.predict(frame, device=s.backend, imgsz=s.imgsz, rect=False,
                                        conf=s.confidence, iou=s.iou, max_det=s.max_det,
                                        verbose=False, save=False)[0]
            rows = result.boxes.data.cpu().numpy()
            if s.backend == "mps":
                import torch
                torch.mps.synchronize()
        return [{"class_id": int(row[5]), "label": self.names[int(row[5])],
                 "confidence": float(row[4]), "xyxy": [float(x) for x in row[:4]]} for row in rows]

    def _predict_coreml(self, frame):
        import numpy as np
        import torch
        from PIL import Image
        from ultralytics.data.augment import LetterBox
        from ultralytics.utils import ops
        try:
            from ultralytics.utils.nms import non_max_suppression
        except ImportError:
            from ultralytics.utils.ops import non_max_suppression
        s = self.settings
        padded = LetterBox(new_shape=(s.imgsz, s.imgsz), auto=False, stride=32)(image=frame)
        # Exported Core ML ImageType contains 1/255 scaling; do not normalize twice.
        image = Image.fromarray(padded[:, :, ::-1].copy())
        outputs = self.model.predict({self.input_name: image})
        raw = np.asarray(outputs[self.output_name])
        if raw.ndim != 3 or raw.shape[0] != 1 or raw.shape[1] != 4 + len(self.names):
            raise ValueError(f"Unsupported raw YOLOv8 output shape: {raw.shape}")
        prediction = torch.from_numpy(raw.astype(np.float32, copy=True))
        rows = non_max_suppression(prediction, conf_thres=s.confidence, iou_thres=s.iou,
                                   max_det=s.max_det, nc=len(self.names))[0]
        if len(rows):
            rows[:, :4] = ops.scale_boxes(padded.shape[:2], rows[:, :4], frame.shape)
        return rows.numpy()

    def close(self):
        self.model = None
