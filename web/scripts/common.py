import hashlib
import importlib.metadata
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

from app.config import ROOT


def digest(path):
    path = Path(path)
    result = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    for item in files:
        if path.is_dir():
            result.update(item.relative_to(path).as_posix().encode())
            result.update(b"\0")
        with item.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                result.update(chunk)
    return result.hexdigest()


def environment():
    packages = {}
    for name in ("torch", "torchvision", "ultralytics", "coremltools", "numpy", "opencv-python", "fastapi"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return {"recorded_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
            "architecture": platform.machine(), "macos": platform.mac_ver()[0], "packages": packages}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def update_manifest(key, value):
    path = ROOT / "models/manifest.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[key] = value
    write_json(path, data)
