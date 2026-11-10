"""Read an existing taught web calibration; output stays in MOCK mode."""
import argparse
import json
from pathlib import Path
from urllib.request import urlopen
from vision_picking.controller import ROOT
from vision_picking.pick_plane import Calibration, PickPlane

parser = argparse.ArgumentParser()
parser.add_argument('--web', default='http://127.0.0.1:8000')
parser.add_argument('--region', choices=['legacy', 'floor'], default='legacy')
parser.add_argument('--output', type=Path, default=ROOT/'config/imported.mock.json')
args = parser.parse_args()
with urlopen(args.web.rstrip('/') + '/api/robot/pick/calibration?region=' + args.region, timeout=5) as response:
    calibration = Calibration(**json.load(response))
if not calibration.revision:
    raise ValueError('The web camera has no saved calibration revision')
PickPlane(calibration)
config = json.loads((ROOT/'config/integration.json').read_text(encoding='utf-8'))
config['calibration'] = calibration.model_dump()
config['mock_mode'] = True
args.output.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'Imported camera calibration into MOCK configuration: {args.output}')
