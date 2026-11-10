"""Run a deterministic mock cycle without camera, network, ROS or robot hardware."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from vision_picking.contracts import PickRequest
from vision_picking.controller import Controller, ROOT

payload = json.loads((ROOT / 'config/payload.example.json').read_text())
payload.update(request_id=uuid4().hex, captured_at=datetime.now(timezone.utc).isoformat())
with TemporaryDirectory() as directory:
    controller = Controller(journal=Path(directory) / 'jobs.sqlite3')
    result = controller.move_to_pick(PickRequest(**payload))
    print(json.dumps(dict(result=result, events=controller.backend.events), ensure_ascii=False, indent=2))
    assert result['state'] == 'completed' and result['mock_mode'] is True
