"""Explicit HTTP bridge from the existing detection stream to the demo controller."""
import asyncio
import json
import os
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request as HttpRequest, urlopen
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from .pick_plane import Input, CameraFrame
from .robot_access_protocol import local_json
from .store import DomainError

router = APIRouter(prefix='/api/integration')


class Selection(Input):
    session_id: str = Field(min_length=1, max_length=64)
    frame_id: int = Field(ge=1)
    object_index: int = Field(ge=0)
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{8,64}$')
    calibration_revision: str = Field(min_length=1, max_length=64)
    box_slot: int = Field(default=1, ge=1, le=4)


def payload_from_packet(packet, selection):
    if packet['session_id'] != selection.session_id or packet['frame_id'] != selection.frame_id:
        raise DomainError('요청한 탐지 프레임이 변경되었습니다.', 412)
    if selection.object_index >= len(packet['objects']):
        raise DomainError('선택한 객체가 없습니다.', 422)
    obj = packet['objects'][selection.object_index]
    x1, y1, x2, y2 = obj['xyxy']
    return dict(schema_version='1.0', request_id=selection.request_id,
                simulation_only=packet.get('simulation_only', True),
                camera=CameraFrame.from_packet(packet).model_dump(), frame_id=packet['frame_id'],
                captured_at=packet['captured_at'], model_version=str(packet['model_version']),
                object_index=selection.object_index,
                detection={**{key: obj[key] for key in ('class_id', 'label', 'confidence', 'xyxy')},
                           'center_px': [(x1+x2)/2, (y1+y2)/2]},
                calibration_revision=selection.calibration_revision, box_slot=selection.box_slot)


def controller_request(action, payload=None):
    base = os.getenv('YOLO_DEMO_CONTROLLER_URL', 'http://127.0.0.1:8010').rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/'):
        raise DomainError('YOLO_DEMO_CONTROLLER_URL 설정을 확인하세요.', 503)
    request = HttpRequest(base + '/api/v1/' + action, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Content-Type': 'application/json'}, method='POST' if payload is not None else 'GET')
    try:
        # Mutations are sent once. A timeout is an unknown outcome, never a retry.
        with urlopen(request, timeout=180 if action in ('pick', 'move-target') else 5) as response:
            return json.load(response)
    except HTTPError as exc:
        try: detail = json.load(exc).get('detail', str(exc))
        except (ValueError, AttributeError): detail = str(exc)
        raise DomainError(detail, exc.code) from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise DomainError('제어기 응답을 확인할 수 없습니다. 같은 request_id로 작업 상태를 조회하세요. 자동 재전송하지 않습니다.', 503) from exc


async def preview_target(picks, selection, reader):
    packet = await asyncio.to_thread(reader, selection.session_id, selection.frame_id)
    context = await asyncio.to_thread(controller_request, 'context')
    identifier = uuid4().hex
    body = Selection(**{key: getattr(selection, key) for key in ('session_id','frame_id','object_index')},
                     request_id=identifier, calibration_revision=context['calibration_revision'])
    payload = payload_from_packet(packet, body)
    plan = await asyncio.to_thread(controller_request, 'plan', payload)
    now = time.monotonic()
    picks.approvals = {key: value for key, value in picks.approvals.items() if value['deadline'] > now}
    if len(picks.approvals) >= 128: raise DomainError('대기 중인 승인이 많습니다.')
    picks.approvals[identifier] = dict(kind='demo', deadline=now+30, payload=payload, pose=plan['pick_pose'])
    return dict(approval_id=identifier, expires_in=30, pose=plan['pick_pose'], label=payload['detection']['label'],
                confidence=payload['detection']['confidence'], motion_reason=None, handling='manual', mode='pick', route=None,
                gripper=dict(reason='MOCK 모드 · 실물 동작 없음' if plan['mock_mode'] else '신규 ROS 제어기 · 수동 파지 위치 이동'),
                hand_position=None, hand_error=None)


def approve_target(picks, identifier, reader):
    target = picks.approvals.pop(identifier, None)
    if target is None or time.monotonic() >= target['deadline']: raise DomainError('승인이 만료되었습니다.')
    if picks.busy or picks.robot.hand.busy or picks.robot.pose_tests.snapshot()['busy']: raise DomainError('이전 작업이 진행 중입니다.')
    picks.record(identifier, 'checking', '신규 제어기로 파지 위치를 확인합니다.', mode='pick', route=None,
                 label=target['payload']['detection']['label'], handling='manual', last_reached=None)
    picks.task = asyncio.create_task(run_target(picks, identifier, target, reader))
    return picks.moves[identifier]


async def run_target(picks, identifier, target, reader):
    try:
        packet = await asyncio.to_thread(reader)
        old = target['payload']
        if CameraFrame.from_packet(packet).model_dump() != old['camera'] or str(packet['model_version']) != old['model_version']:
            raise DomainError('카메라 또는 모델이 변경되었습니다.')
        matches = []
        for index, obj in enumerate(packet['objects']):
            x1,y1,x2,y2 = obj['xyxy']
            if obj['class_id'] == old['detection']['class_id'] and obj['label'] == old['detection']['label'] and math.dist([(x1+x2)/2,(y1+y2)/2], old['detection']['center_px']) <= 5:
                matches.append(index)
        if len(matches) != 1: raise DomainError('선택한 대상이 이동했거나 모호합니다. 다시 선택하세요.')
        body = Selection(session_id=packet['session_id'], frame_id=packet['frame_id'], object_index=matches[0],
                         request_id=identifier, calibration_revision=old['calibration_revision'])
        payload = payload_from_packet(packet, body)
        # Preview the new frame again, but never send an unreviewed changed BASE target.
        plan = await asyncio.to_thread(controller_request, 'plan', payload)
        if math.dist(plan['pick_pose'][:3], target['pose'][:3]) > 10: raise DomainError('파지 위치가 10 mm 이상 변경되었습니다.')
        picks.record(identifier, 'moving', ('MOCK · ' if plan['mock_mode'] else '') + '승인한 파지 위치 이동 중입니다.')
        result = await asyncio.to_thread(controller_request, 'move-target', payload)
        state = 'completed' if result['state'] == 'completed' else 'unknown'
        picks.record(identifier, state, ('MOCK · 실물 동작 없음 · ' if result['mock_mode'] else '') +
                     ('파지 위치 이동 완료' if state == 'completed' else result.get('error','실제 위치 확인 필요')),
                     last_reached='pick' if state == 'completed' else None, controller_result=result)
    except (Exception, asyncio.CancelledError) as exc:
        picks.record(identifier, 'unknown', str(exc) or '제어기 실행 결과를 확인하세요. 자동 재전송하지 않습니다.')


async def selected_payload(body, request):
    packet = await asyncio.to_thread(request.app.state.pipeline.pick_frame, body.session_id, body.frame_id)
    return payload_from_packet(packet, body)


@router.post('/payload', dependencies=[Depends(local_json)])
async def payload(body: Selection, request: Request):
    return await selected_payload(body, request)


@router.post('/plan', dependencies=[Depends(local_json)])
async def plan(body: Selection, request: Request):
    return await asyncio.to_thread(controller_request, 'plan', await selected_payload(body, request))


@router.post('/pick', dependencies=[Depends(local_json)])
async def pick(body: Selection, request: Request):
    return await asyncio.to_thread(controller_request, 'pick', await selected_payload(body, request))
