import argparse
import json
import subprocess

from app.capture import OpenCVCamera
from app.config import Settings
from .common import ROOT, environment, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", action="store_true")
    args = parser.parse_args()
    import torch
    result = environment()
    result["mps_built"] = torch.backends.mps.is_built()
    result["mps_available"] = torch.backends.mps.is_available()
    for key in ("machdep.cpu.brand_string", "hw.memsize"):
        probe = subprocess.run(["/usr/sbin/sysctl", "-n", key], capture_output=True, text=True)
        result[key] = probe.stdout.strip() if probe.returncode == 0 else "unavailable"
    if args.camera:
        camera = None
        try:
            camera = OpenCVCamera(Settings())
            frame = camera.read()
            result["camera"] = {"available": True, "shape": list(frame.shape)}
        except Exception as exc:
            result["camera"] = {"available": False, "error": str(exc)}
        finally:
            if camera:
                camera.close()
    write_json(ROOT / "artifacts/device-probe.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
