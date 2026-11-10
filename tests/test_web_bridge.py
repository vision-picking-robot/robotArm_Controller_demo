import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient
from vision_picking.controller import Controller, ROOT
from vision_picking.server import create_app

sys.path.insert(0, str(ROOT / 'web'))
from app.picking_integration import preview_target, approve_target
from app.robot_pick import PickTargets, Selection


def test_original_pick_approval_uses_demo_without_gripper_or_place(tmp_path):
    controller = Controller(journal=tmp_path/'controller.sqlite3')
    camera = controller.config['calibration']['camera']
    packet = dict(session_id=camera['session_id'], camera_id=camera['camera_id'], camera_generation=1,
                  frame_id=1, captured_at=datetime.now(timezone.utc).isoformat(), model_version='mock-model-v1',
                  source_size=dict(width=640,height=480), objects=[dict(class_id=0,label='snack_box',confidence=.95,xyxy=[280.,200.,360.,280.])])
    robot = SimpleNamespace(hand=SimpleNamespace(busy=False), pose_tests=SimpleNamespace(snapshot=lambda:dict(busy=False)))
    picks = PickTargets(robot,tmp_path)
    def reader(*args): return packet
    with TestClient(create_app(controller)) as client:
        def transport(action,payload=None):
            response = client.get('/api/v1/'+action) if payload is None else client.post('/api/v1/'+action,json=payload)
            assert response.status_code == 200, response.text
            return response.json()
        async def cycle():
            with patch('app.picking_integration.controller_request',transport):
                selected = Selection(session_id=camera['session_id'],frame_id=1,object_index=0)
                preview = await preview_target(picks,selected,reader)
                assert controller.backend.events == []
                assert 'MOCK' in preview['gripper']['reason']
                approve_target(picks,preview['approval_id'],reader)
                await picks.task
                assert picks.moves[preview['approval_id']]['state']=='completed'
                assert 'MOCK' in picks.moves[preview['approval_id']]['message']
                assert [e['action'] for e in controller.backend.events] == ['movel']
        asyncio.run(cycle())
