import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from vision_picking.backends import MockBackend
from vision_picking.contracts import PickRequest
from vision_picking.controller import Controller, ROOT
from vision_picking.server import create_app
from vision_picking.store import DomainError


@pytest.fixture
def payload():
    value = json.loads((ROOT / 'config/payload.example.json').read_text())
    value['captured_at'] = datetime.now(timezone.utc).isoformat()
    return value


@pytest.fixture
def controller(tmp_path):
    return Controller(journal=tmp_path / 'journal.sqlite3')


def test_http_full_cycle_and_replay_never_repeats_motion(controller, payload):
    with TestClient(create_app(controller)) as client:
        plan = client.post('/api/v1/plan', json=payload)
        assert plan.status_code == 200 and plan.json()['pick_pose'][2] == 120
        assert controller.backend.events == []
        first = client.post('/api/v1/pick', json=payload)
        assert first.status_code == 200 and first.json()['state'] == 'completed'
        events = list(controller.backend.events)
        assert client.post('/api/v1/pick', json=payload).json() == first.json()
        assert controller.backend.events == events
        assert all(e['vel'] == 10 and e['ref'] == 0 and e['radius'] == 0 for e in events if e['action'] == 'movel')
        actions = [e['action'] for e in events]
        assert actions.count('gripper_open') == 2 and actions.count('gripper_close') == 1
        assert client.get('/api/v1/jobs/' + payload['request_id']).json()['state'] == 'completed'


@pytest.mark.parametrize('change', ['stale', 'future', 'center', 'nan', 'camera', 'revision', 'class', 'confidence', 'bounds', 'slot'])
def test_bad_detection_rejected_before_any_command(controller, payload, change):
    if change == 'stale': payload['captured_at'] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    if change == 'future': payload['captured_at'] = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    if change == 'center': payload['detection']['center_px'][0] = 1.
    if change == 'nan': payload['detection']['confidence'] = float('nan')
    if change == 'camera': payload['camera']['camera_generation'] = 2
    if change == 'revision': payload['calibration_revision'] = 'wrong'
    if change == 'class': payload['detection']['label'] = 'unknown'
    if change == 'confidence': payload['detection']['confidence'] = .1
    if change == 'bounds': controller.config['workspace_mm']['max'][0] = 450.
    if change == 'slot': payload['box_slot'] = 2
    with pytest.raises(Exception): controller.move_to_pick(PickRequest(**payload))
    assert controller.backend.events == []


def test_real_mode_is_blocked_before_importing_ros(tmp_path, payload):
    config = json.loads((ROOT / 'config/integration.json').read_text())
    config['mock_mode'] = False
    controller = Controller(config, journal=tmp_path / 'real.sqlite3')
    with patch('vision_picking.controller.RosBackend', side_effect=AssertionError('Must not connect')):
        with pytest.raises(DomainError, match='Real execution blocked'):
            controller.move_to_pick(PickRequest(**payload))


def test_synthetic_frames_remain_blocked_even_with_real_flags_set(tmp_path, payload):
    config = json.loads((ROOT / 'config/integration.json').read_text())
    config['mock_mode'] = False
    for key in ('calibration_verified','tcp_verified','waypoints_verified','gripper_verified','workspace_verified'):
        config[key] = True
    controller = Controller(config, journal=tmp_path / 'synthetic.sqlite3')
    with patch('vision_picking.controller.RosBackend', side_effect=AssertionError('Must not connect')):
        with pytest.raises(DomainError, match='Synthetic detections'):
            controller.move_to_pick(PickRequest(**payload))


def test_fault_stops_and_never_releases_or_retries(controller, payload):
    original = controller.backend.movel
    def fail_at_lift(pos, *args, **kwargs):
        if controller.backend.position == 350: raise TimeoutError('outcome unknown')
        return original(pos, *args, **kwargs)
    controller.backend.movel = fail_at_lift
    request = PickRequest(**payload)
    result = controller.move_to_pick(request)
    assert result['state'] == 'unknown'
    assert controller.backend.events[-1] == dict(action='stop')
    assert controller.backend.position == 350
    count = len(controller.backend.events)
    assert controller.move_to_pick(request) == result
    assert len(controller.backend.events) == count


def test_restart_preserves_ambiguous_outcome(controller, payload):
    digest = 'irrelevant'
    result = dict(request_id=payload['request_id'], state='running')
    with controller.db() as db:
        db.execute('INSERT INTO jobs VALUES (?,?,?,?)', (payload['request_id'], digest, 'frame', json.dumps(result)))
    recovered = Controller(journal=controller.journal)
    assert recovered.status(payload['request_id'])['state'] == 'unknown'


def test_same_detection_different_request_rejected(controller, payload):
    controller.move_to_pick(PickRequest(**payload))
    payload['request_id'] = 'second-request'
    payload['captured_at'] = datetime.now(timezone.utc).isoformat()
    with pytest.raises(DomainError, match='Detection already consumed'):
        controller.move_to_pick(PickRequest(**payload))


def test_original_web_packet_roundtrip_to_controller(controller, payload):
    sys.path.insert(0, str(ROOT / 'web'))
    from app.picking_integration import Selection, payload_from_packet
    camera = payload['camera']
    selection = Selection(session_id=camera['session_id'], frame_id=1, object_index=0,
                          request_id=payload['request_id'], calibration_revision=payload['calibration_revision'])
    packet = dict(session_id=camera['session_id'], camera_id=camera['camera_id'], camera_generation=1,
                  frame_id=1, captured_at=payload['captured_at'], model_version='mock-model-v1',
                  source_size=dict(width=640, height=480), objects=[{key: payload['detection'][key] for key in ('class_id','label','confidence','xyxy')}])
    request = PickRequest(**payload_from_packet(packet, selection))
    assert controller.move_to_pick(request)['state'] == 'completed'


def test_rfid_routes_and_button_commands_integrated(controller, tmp_path):
    with TestClient(create_app(controller)) as client:
        tags = client.get('/api/v1/tags')
        assert tags.status_code == 200
        assert len(tags.json()['tags']) == 5
        tag = client.get('/api/v1/tags/0B71E55D')
        assert tag.status_code == 200
        assert tag.json()['yolo_class_name'] == 'snack_pepero'
        scan = client.post('/api/v1/rfid/scan', json={'uid': '0B:71:E5:5D', 'quantity': 1})
        assert scan.status_code == 200
        assert scan.json()['uid'] == '0B71E55D'
        assert scan.json()['product']['target_box_id'] == 'BOX-01'
