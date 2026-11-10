#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend="${1:-mps}"
echo "Native camera server: stop with Ctrl+C or bash scripts/stop.sh. Docker alone does not own this process."
exec bash "$ROOT_DIR/scripts/python.sh" "$backend" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --timeout-graceful-shutdown 5
