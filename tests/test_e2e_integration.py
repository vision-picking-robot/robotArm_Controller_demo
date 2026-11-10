"""Portable MOCK checks; UI screenshot validation lives in scripts/validate_dashboard.py."""
from datetime import datetime, timezone
import json
import sys
from fastapi.testclient import TestClient
from vision_picking.contracts import PickRequest
from vision_picking.controller import Controller, ROOT
from vision_picking.server import create_app

sys.path.insert(0, str(ROOT / 'web'))
from app.config import Settings
from app.main import create_app as create_web_app
from app.picking_integration import Selection, payload_from_packet


def test_detector_packet_to_validated_mock_controller(tmp_path):
    payload = json.loads((ROOT / 'config/payload.example.json').read_text(encoding='utf-8'))
    camera = payload['camera']
    packet = dict(session_id=camera['session_id'], camera_id=camera['camera_id'], camera_generation=1,
                  source_size=dict(width=640,height=480), frame_id=1, captured_at=datetime.now(timezone.utc).isoformat(),
                  model_version='mock-model-v1',simulation_only=True,
                  objects=[{key:payload['detection'][key] for key in ('class_id','label','confidence','xyxy')}])
    selected = Selection(session_id=camera['session_id'],frame_id=1,object_index=0,
                         request_id='portable-e2e-1',calibration_revision='mock-only-v1')
    body = payload_from_packet(packet,selected)
    controller = Controller(journal=tmp_path/'jobs.sqlite3')
    with TestClient(create_app(controller)) as client:
        assert client.post('/api/v1/plan',json=body).status_code==200
        result = client.post('/api/v1/pick',json=body).json()
        assert result['state']=='completed' and result['mock_mode'] is True
        assert len([event for event in controller.backend.events if event['action']=='movel'])==9


def test_voice_and_manual_commands_share_packing_processor(tmp_path):
    with TestClient(create_web_app(Settings(camera_autostart=False,voice_enabled=True),state_root=tmp_path)) as client:
        assert client.post('/api/packing/command',json={'action':'cmd-stop'}).status_code==200
        voice = client.post('/api/voice/wake').json()
        response = client.post('/api/voice/transcript',json=dict(session=voice['session'],turn=voice['turn'],text='피킹 재개해줘'))
        assert response.status_code==200
        assert client.app.state.pipeline.packing.order(123)['paused'] is False
        assert client.post('/api/packing/rfid-scan',json={'uid':'0B71E55D'}).json()['created'] is True


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-q']))
