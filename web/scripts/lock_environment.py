"""Record resolved conda and pip dependencies after installation and tests."""
import argparse
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from app.config import ROOT


def portable_freeze(output):
    lines = []
    for line in output.splitlines():
        if " @ file:" in line:
            # conda distributions can retain the builder's private filesystem URL.
            # The explicit conda spec installs them; pip should only constrain version.
            name = line.split(" @ ", 1)[0]
            line = f"{name}=={version(name)}"
        if line.startswith("-e "):
            raise RuntimeError("Editable dependencies cannot be included in a portable lock")
        lines.append(line)
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["mps", "coreml", "train"], required=True)
    args = parser.parse_args()
    if not (Path(sys.prefix) / "conda-meta").is_dir():
        raise RuntimeError("Run inside the project's conda environment")
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, check=True)
    output = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    (ROOT / f"requirements-{args.backend}.lock.txt").write_text(portable_freeze(output))
    conda = shutil.which("conda") or str(Path.home() / "miniforge3/bin/conda")
    explicit = subprocess.check_output([conda, "list", "--prefix", sys.prefix, "--explicit"], text=True)
    (ROOT / f"conda-{args.backend}.osx-arm64.lock.txt").write_text(explicit)
    print("Wrote conda explicit spec and pip freeze. Hardware validation is reported separately.")


if __name__ == "__main__":
    main()
