import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import pytest
from fastapi import HTTPException

from app.robot_host_access import AccessServer
from app.robot_scenario_config import ScenarioCommand,KinematicsRequest,OperationStop


def owner():
    with patch('app.robot_host_access.lan_settings',return_value={'ROBOT_IP':'127.0.0.1','ROBOT_PORT':'12345','ROBOT_LAN_INTERFACE':'test'}):s=AccessServer()
    s.connection='connected';s.initialized=s.held=s.scenario_supported=True
    s.mode='AUTO';s.system='REAL';s.state='STANDBY';s.session='test';s.motion_token='1'*32;s.grant_number=2
    s.held_since=time.monotonic()
    writes=[];s.process=SimpleNamespace(stdin=SimpleNamespace(write=writes.append),pid=1)
    s.check_route=AsyncMock();return s,writes


def command(s):return ScenarioCommand(session_id=s.session,motion_token=s.motion_token,kind='joint',target=[0,0,90,0,90,0],speed=10,acceleration=10,tcp_name='tool',reference_tcp=[373,0,262,-90,180,0])


def test_async_operation_consumes_once_and_returns_a_new_token_only_after_verified_result():
    async def run():
        s,writes=owner();request=command(s);result=await s.begin_operation(request)
        assert result['state']=='running' and len(writes)==1
        assert not s.snapshot()['can_move']
        with pytest.raises(HTTPException):await s.begin_operation(request)
        assert len(writes)==1
        s.segment_result(dict(id=result['operation_id'],state='completed',message='verified'))
        done=s.operations[result['operation_id']];assert done['next_motion_token']!=request.motion_token
        assert s.motion is None
        s.state='STANDBY';second=await s.begin_operation(command(s))
        s.stop_operation(OperationStop(session_id=s.session,operation_id=second['operation_id']))
        s.stop_operation(OperationStop(session_id=s.session,operation_id=second['operation_id']))
        assert len([x for x in writes if x.startswith(b'halt ')])==1
        s.segment_result(dict(id=second['operation_id'],state='cancelled',message='stopped'))
        assert 'next_motion_token' not in s.operations[second['operation_id']]
    asyncio.run(run())


def test_readonly_inspection_is_correlated_and_cannot_interleave_a_move():
    async def run():
        s,writes=owner();task=asyncio.create_task(s.inspect_kinematics(KinematicsRequest(session_id=s.session)))
        await asyncio.sleep(0)
        identifier=s.inspection[0]
        with pytest.raises(HTTPException):await s.begin_operation(command(s))
        s.event('inspection',json.dumps(dict(id='wrong',current_joints=[0]*6)))
        assert not task.done()
        s.event('inspection',json.dumps(dict(id=identifier,current_joints=[0]*6)))
        result=await task;assert result['session_id']=='test' and len(writes)==1 and writes[0].startswith(b'inspect ')
    asyncio.run(run())


def test_session_loss_never_adopts_a_late_completed_operation():
    async def run():
        s,_=owner();result=await s.begin_operation(command(s));s.reset('disconnected','lost')
        s.segment_result(dict(id=result['operation_id'],state='completed',message='late'))
        assert s.operations[result['operation_id']]['state']=='unknown'
        assert 'next_motion_token' not in s.operations[result['operation_id']]
    asyncio.run(run())


def test_circle_capability_via_wire_and_joint_feedback_are_correlated():
    async def run():
        s,writes=owner();body=command(s).model_copy(update=dict(kind='circle',target=[20,0,0,0,0,0],via_pose=[10,10,0,0,0,0]))
        with pytest.raises(HTTPException):await s.begin_operation(body)
        assert not writes
        s.event('feature','circle');result=await s.begin_operation(body)
        assert len(writes)==1 and b' circle ' in writes[0] and writes[0].endswith(b'10 10 0 0 0 0\n')
        s.segment_result(dict(id=result['operation_id'],state='completed',message='done',current_joints=[0,0,90,0,90,0]))
        assert s.operations[result['operation_id']]['current_joints']==[0,0,90,0,90,0]
    asyncio.run(run())
