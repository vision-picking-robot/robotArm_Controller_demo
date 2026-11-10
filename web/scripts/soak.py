"""Observe an already-running local server without storing frames."""
import argparse
import json
import threading
import time
import urllib.request
from pathlib import Path

from .common import ROOT, environment, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--seconds", type=float, default=1800)
    parser.add_argument("--clients", type=int, default=1)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/soak.json")
    args = parser.parse_args()
    if not 0 < args.seconds <= 86400 or not 0 <= args.clients <= 10:
        parser.error("seconds: (0, 86400], clients: [0, 10]")
    stop = threading.Event()
    connections = []
    failures = []
    samples = []
    def consume():
        try:
            with urllib.request.urlopen(args.url + "/stream.mjpg", timeout=10) as response:
                connections.append(response)
                while not stop.is_set():
                    if not response.read(65536):
                        break
        except Exception as exc:
            if not stop.is_set():
                failures.append(str(exc))
    for _ in range(args.clients):
        threading.Thread(target=consume, daemon=True).start()
    process = None
    if args.pid:
        import psutil
        process = psutil.Process(args.pid)
    start = time.monotonic()
    try:
        while time.monotonic() - start < args.seconds:
            try:
                with urllib.request.urlopen(args.url + "/api/status", timeout=5) as response:
                    sample = json.load(response)
                sample["elapsed_seconds"] = time.monotonic() - start
                if process:
                    sample["rss_bytes"] = process.memory_info().rss
                samples.append(sample)
            except Exception as exc:
                samples.append({"elapsed_seconds": time.monotonic() - start, "error": str(exc)})
            stop.wait(min(5, max(0, args.seconds - (time.monotonic() - start))))
    finally:
        stop.set()
        for response in connections:
            response.close()
        ready = [s for s in samples if s.get("ready")]
        write_json(args.output, {"environment": environment(), "requested_seconds": args.seconds,
                                "observed_seconds": time.monotonic() - start, "clients": args.clients,
                                "samples": samples, "stream_errors": failures,
                                "ready_samples": len(ready), "total_samples": len(samples),
                                "all_samples_ready": bool(samples) and len(ready) == len(samples),
                                "note": "Measures server metrics and JPEG consumption, not browser display latency."})
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
