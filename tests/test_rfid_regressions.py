from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from robot_rfid.api_server import create_app
from robot_rfid.database import SEEDS
from vision_picking.controller import ROOT

sys.path.insert(0, str(ROOT / 'web'))
from app.config import Settings
from app.main import create_app as create_web_app


def test_fresh_nested_database_is_created_at_startup(tmp_path):
    path = tmp_path / 'new' / 'state' / 'rfid.db'
    app = create_app(path)
    assert not path.exists()
    with TestClient(app) as client:
        assert client.get('/api/v1/tags').status_code == 200
        assert path.is_file()


def test_importing_api_does_not_create_a_database(tmp_path):
    result = subprocess.run([sys.executable, '-c', 'import robot_rfid.api_server'], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('dashboard', [False, True])
def test_reader_failure_does_not_invent_a_product_or_task(tmp_path, dashboard):
    app = create_web_app(Settings(camera_autostart=False), state_root=tmp_path) if dashboard else create_app(tmp_path/'rfid.db')
    route = '/api/packing/rfid-scan' if dashboard else '/api/v1/rfid/scan'
    with TestClient(app) as client, patch('robot_rfid.services.RFIDScanner.scan', side_effect=TimeoutError('No card')):
        response = client.post(route, json={})
        assert response.status_code == 503
        assert client.get('/api/v1/current-task').json()['task'] is None
        with app.state.database.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0


@pytest.mark.parametrize('dashboard', [False, True])
def test_duplicate_tag_returns_that_tags_task_not_fifo_head(tmp_path, dashboard):
    app = create_web_app(Settings(camera_autostart=False), state_root=tmp_path) if dashboard else create_app(tmp_path/'rfid.db')
    route = '/api/packing/rfid-scan' if dashboard else '/api/v1/rfid/scan'
    with TestClient(app) as client:
        first = client.post(route, json={'uid': '0B71E55D'}).json()
        second = client.post(route, json={'uid': '0B71E75D'}).json()
        duplicate = client.post(route, json={'uid': '0B71E75D'}).json()
        assert first['created'] and second['created']
        assert duplicate['created'] is False
        assert duplicate['task']['task_id'] == second['task']['task_id']
        assert duplicate['task']['tag_uid'] == duplicate['product']['tag_uid'] == '0B71E75D'
        assert client.get('/api/v1/current-task').json()['task']['task_id'] == first['task']['task_id']


def test_list_tags_includes_custom_registration_in_one_database_read(tmp_path):
    app = create_app(tmp_path/'rfid.db')
    with TestClient(app) as client:
        custom = ('000000A1', 'CUSTOM-SKU', *SEEDS[0][2:])
        with app.state.database.connect() as db:
            db.execute('''INSERT INTO product_tag_mappings
                (tag_uid,sku_code,product_name,category,target_class_id,yolo_class_name,inbound_at,expired_at,retail_price,target_box_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)''', custom)
        with patch.object(app.state.database, 'connect', wraps=app.state.database.connect) as connections:
            tags = client.get('/api/v1/tags').json()['tags']
            assert len(tags) == 6 and tags[-1]['tag_uid'] == '000000A1'
            assert connections.call_count == 1


def test_concurrent_scan_is_rejected_without_blocking_reads(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def scan(timeout):
        entered.set()
        if not release.wait(3): raise TimeoutError('test stalled')
        return '0B71E55D'
    with TestClient(create_app(tmp_path/'rfid.db')) as client, patch('robot_rfid.services.RFIDScanner.scan', side_effect=scan), ThreadPoolExecutor(1) as workers:
        first = workers.submit(client.post, '/api/v1/rfid/scan', json={})
        try:
            assert entered.wait(2)
            assert client.post('/api/v1/rfid/scan', json={}).status_code == 409
            assert client.get('/api/v1/current-task').json()['task'] is None
            assert not first.done()
        finally:
            release.set()
        assert first.result(timeout=3).status_code == 200


def test_reader_event_id_replay_and_conflict(tmp_path):
    with TestClient(create_app(tmp_path/'rfid.db')) as client:
        body = dict(uid='0B71E55D', event_id='reader-event-1', quantity=1)
        first = client.post('/api/v1/rfid/scan', json=body).json()
        assert client.post('/api/v1/rfid/scan', json=body).json()['task'] == first['task']
        assert client.post('/api/v1/rfid/scan', json={**body,'uid':'0B71E75D'}).status_code == 409


@pytest.mark.parametrize('quantity', [True, '1', 1.5])
def test_scan_does_not_coerce_quantity(tmp_path, quantity):
    with TestClient(create_app(tmp_path/'rfid.db')) as client:
        assert client.post('/api/v1/rfid/scan', json={'uid':'0B71E55D','quantity':quantity}).status_code == 422
