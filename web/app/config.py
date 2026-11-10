"""Configuration is read once at startup; importing the app never opens a camera."""
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Keep Ultralytics settings inside this project and disable implicit dependency installs.
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".cache/ultralytics"))
os.environ.setdefault("YOLO_AUTOINSTALL", "false")


@dataclass(frozen=True)
class Settings:
    backend: str = "mps" if sys.platform == "darwin" else "cpu"
    model_path: Path = ROOT / "models/yolov8n.pt"
    camera_index: int = 0
    camera_id: str | None = None
    width: int = 1280
    height: int = 720
    camera_fps: float = 30.0
    imgsz: int = 640
    confidence: float = 0.25
    iou: float = 0.45
    max_det: int = 100
    preview_fps: float = 15.0
    jpeg_quality: int = 80
    stale_seconds: float = 1.0
    send_timeout: float = 5.0
    warmup: int = 3
    state_dir: Path = ROOT / "state"
    model_path_explicit: bool = False
    camera_autostart: bool = True
    voice_enabled: bool = False
    voice_barge_in: bool = False

    def __post_init__(self):
        if self.backend not in {"mps", "cpu", "coreml", "mock"}:
            raise ValueError("YOLO_BACKEND must be mps, coreml, cpu or mock")
        if self.camera_index < 0 or self.warmup < 0:
            raise ValueError("Camera index and warmup must be non-negative")
        if self.width <= 0 or self.height <= 0 or self.imgsz <= 0 or self.imgsz % 32:
            raise ValueError("Image dimensions must be positive; imgsz must be a multiple of 32")
        if not 0 < self.confidence < 1 or not 0 < self.iou < 1:
            raise ValueError("Confidence and IoU must be between 0 and 1")
        if self.max_det <= 0 or not 1 <= self.jpeg_quality <= 100:
            raise ValueError("Invalid max_det or JPEG quality")
        for value in (self.camera_fps, self.preview_fps, self.stale_seconds, self.send_timeout):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("FPS and timeouts must be finite and positive")

    @classmethod
    def from_env(cls):
        backend = os.getenv("YOLO_BACKEND", cls.backend)
        autostart = os.getenv("YOLO_CAMERA_AUTOSTART", "1")
        if autostart not in {"0", "1"}:
            raise ValueError("YOLO_CAMERA_AUTOSTART must be 0 or 1")
        voice = os.getenv("YOLO_VOICE_ENABLED", "0")
        if voice not in {"0", "1"}:
            raise ValueError("YOLO_VOICE_ENABLED must be 0 or 1")
        barge_in = os.getenv("YOLO_VOICE_BARGE_IN", "0")
        if barge_in not in {"0", "1"}:
            raise ValueError("YOLO_VOICE_BARGE_IN must be 0 or 1")
        default_model = "yolov8n.mlpackage" if backend == "coreml" else "yolov8n.pt"
        values = {
            "backend": backend,
            "voice_enabled": voice == "1",
            "voice_barge_in": barge_in == "1",
            "camera_autostart": autostart == "1",
            "state_dir": Path(os.getenv("YOLO_STATE_DIR", str(ROOT / "state"))).expanduser().resolve(),
            "model_path_explicit": "YOLO_MODEL_PATH" in os.environ,
            "model_path": Path(os.getenv("YOLO_MODEL_PATH", str(ROOT / "models" / default_model))).expanduser().resolve(),
        }
        fields = {
            "camera_index": ("CAMERA_INDEX", int), "width": ("WIDTH", int),
            "height": ("HEIGHT", int), "camera_fps": ("CAMERA_FPS", float),
            "imgsz": ("IMGSZ", int), "confidence": ("CONF", float), "iou": ("IOU", float),
            "max_det": ("MAX_DET", int), "preview_fps": ("PREVIEW_FPS", float),
            "jpeg_quality": ("JPEG_QUALITY", int), "stale_seconds": ("STALE_SECONDS", float),
            "send_timeout": ("SEND_TIMEOUT", float), "warmup": ("WARMUP", int),
        }
        for field, (env, cast) in fields.items():
            if "YOLO_" + env in os.environ:
                values[field] = cast(os.environ["YOLO_" + env])
        return cls(**values)
