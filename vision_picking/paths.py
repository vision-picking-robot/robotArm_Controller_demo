"""Resolve editable source and wheel-installed assets without writing into site-packages."""
import os
from pathlib import Path
import sys

_source = Path(__file__).resolve().parents[1]
ROOT = _source if (_source / 'config/integration.json').is_file() else Path(sys.prefix) / 'share/e0509-vision-picking-demo'


def state_directory():
    return Path(os.getenv('PICKING_STATE_DIR', str(Path.cwd() / 'state')))
