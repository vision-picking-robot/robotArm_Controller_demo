"""Explicit downloads only; serving never invokes this tool."""
import argparse
import stat
import urllib.request
import zipfile

from app.config import ROOT
from .common import digest, environment, update_manifest

MODEL_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
DATA_URL = "https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip"


def download(url, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "yolov8-local-server/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def extract(archive, destination):
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (root / member.filename).resolve()
            if not target.is_relative_to(root) or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Unsafe dataset archive entry")
        bundle.extractall(root)


def main():
    parser = argparse.ArgumentParser(description="Download YOLOv8n and COCO128 into the project")
    parser.add_argument("--model-only", action="store_true")
    args = parser.parse_args()
    model = ROOT / "models/yolov8n.pt"
    if not model.exists():
        download(MODEL_URL, model)
    update_manifest("pytorch", {"path": str(model.relative_to(ROOT)), "sha256": digest(model),
                                "source": MODEL_URL, "environment": environment()})
    print(f"Model ready: {model}")
    if not args.model_only:
        data = ROOT / "data"
        labels = data / "coco128/labels/train2017"
        images = data / "coco128/images/train2017"
        if len(list(images.glob("*.jpg"))) != 128 or not labels.exists():
            archive = data / "coco128.zip"
            if not archive.exists():
                download(DATA_URL, archive)
            extract(archive, data)
        if len(list(images.glob("*.jpg"))) != 128:
            raise RuntimeError("Expected all 128 COCO128 JPEG images")
        update_manifest("dataset", {"name": "COCO128", "source": DATA_URL,
                                    "sha256": digest(data / "coco128"), "image_count": 128})
        print(f"Dataset ready: {data / 'coco128'}")


if __name__ == "__main__":
    main()
