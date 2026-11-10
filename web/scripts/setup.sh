#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
backend="${1:-mps}"
case "$backend" in mps|coreml|train) ;; *) echo "Usage: bash scripts/setup.sh [mps|coreml|train]" >&2; exit 2;; esac
manager="${YOLO_CONDA_EXE:-}"
if [[ -z "$manager" ]]; then
  for candidate in mamba conda "$HOME/miniforge3/bin/mamba" "$HOME/miniforge3/bin/conda"; do
    if command -v "$candidate" >/dev/null 2>&1; then manager="$(command -v "$candidate")"; break; fi
  done
fi
if [[ -z "$manager" ]]; then echo "Install arm64 Miniforge, or set YOLO_CONDA_EXE to conda/mamba." >&2; exit 1; fi
prefix="$ROOT_DIR/.conda/$backend"
if [[ -d "$prefix/conda-meta" ]]; then
  echo "Environment already exists: $prefix. To update explicitly: $manager env update -p $prefix -f environment-$backend.yml" >&2
  exit 1
fi
"$manager" env create --prefix "$prefix" --file "$ROOT_DIR/environment-$backend.yml" --yes
"$prefix/bin/python" -m pip check
"$prefix/bin/python" -c 'import platform; assert platform.machine() == "arm64", "Use native arm64 Miniforge on Apple Silicon"'
echo "Environment ready. Run: bash scripts/python.sh $backend -m scripts.prepare"
