#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
backend="${1:-mps}"
case "$backend" in mps|coreml|cpu|train) ;; *) echo "Usage: bash scripts/python.sh [mps|coreml|cpu|train] [Python arguments]" >&2; exit 2;; esac
if [[ $# -gt 0 ]]; then shift; fi
env_backend="$backend"
if [[ "$backend" == cpu ]]; then env_backend=mps; fi
prefix="$ROOT_DIR/.conda/$env_backend"
if [[ ! -x "$prefix/bin/python" ]]; then echo "Create environment first: bash scripts/setup.sh $env_backend" >&2; exit 1; fi
if [[ "$backend" != train ]]; then export YOLO_BACKEND="$backend"; fi
export YOLO_CONFIG_DIR="$ROOT_DIR/.cache/ultralytics"
export YOLO_AUTOINSTALL=false
exec "$prefix/bin/python" "$@"
