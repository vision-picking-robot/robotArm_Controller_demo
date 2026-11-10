from dataclasses import replace
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app


def test_browser_transcript_reuses_packing_and_rejects_duplicate(tmp_path):
    with TestClient(create_app(Settings(camera_autostart=False, voice_enabled=True), state_root=tmp_path)) as client:
        with patch('asyncio.create_subprocess_exec', side_effect=AssertionError('No native speech helper')):
            wake = client.post('/api/voice/wake').json()
            assert wake['state'] == 'listening'
            assert 'session' not in client.get('/api/voice/status').json()
            def send(text, turn):
                return client.post('/api/voice/transcript', json=dict(session=wake['session'], turn=turn, text=text))
            assert '생수' in send('재고 알려줘', 1).json()['reply']
            assert send('멈춰', 1).status_code == 409
            assert not client.app.state.voice.packing.order(123)['paused']
            assert send('멈춰', 2).status_code == 200
            assert client.app.state.voice.packing.order(123)['paused']
            assert '주문할까요' in send('예시 주문해줘', 3).json()['reply']
            assert '접수했습니다' in send('주문 확정', 4).json()['reply']
            assert send('주문 확정', 4).status_code == 409
            assert send('대화 종료', 5).json()['state'] == 'idle'
            assert send('멈춰', 6).status_code == 409


def test_stop_invalidates_old_session_and_cross_origin_is_blocked(tmp_path):
    with TestClient(create_app(Settings(camera_autostart=False, voice_enabled=True), state_root=tmp_path)) as client:
        old = client.post('/api/voice/wake').json()
        assert client.post('/api/voice/wake').status_code == 409
        assert client.post('/api/voice/stop', headers={'Origin':'https://other.example'}).status_code == 403
        assert client.post('/api/voice/stop').json()['state'] == 'idle'
        client.post('/api/voice/wake')
        body = dict(session=old['session'], turn=old['turn'], text='멈춰')
        assert client.post('/api/voice/transcript', json=body).status_code == 409
        assert client.post('/api/voice/transcript', json={**body, 'extra':1}).status_code == 422


def test_disabled_voice_does_not_disable_dashboard(tmp_path):
    with TestClient(create_app(Settings(camera_autostart=False), state_root=tmp_path)) as client:
        assert client.get('/api/voice/status').json()['state'] == 'disabled'
        assert client.post('/api/voice/stop').json()['state'] == 'disabled'
        assert client.post('/api/voice/wake').status_code == 503
        assert client.post('/api/packing/command', json={'action':'query-stock'}).status_code == 200
