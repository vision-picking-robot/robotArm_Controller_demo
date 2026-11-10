"""Recipe and approval integration; every SDK/camera response is synthetic."""
import copy
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.robot_web import create_robot_app
from app.robot_scenario_config import ScenarioInput


@pytest.fixture
def scenario(tmp_path,monkeypatch):
    monkeypatch.setenv('ROBOT_STATE_DIR',str(tmp_path));monkeypatch.setenv('ROBOT_ROS_PC_IP','');monkeypatch.setenv('ROBOT_ACCESS_BACKEND','mac-sdk')
    frame=dict(session_id='camera',camera_generation=1,camera_id='fixture',frame_id=1,model_version='v1',source_size=dict(width=320,height=240),
               objects=[dict(class_id=0,label='상품',confidence=.9,xyxy=[60,60,100,100])])
    monkeypatch.setattr('app.robot_pick.read_camera',lambda *args:copy.deepcopy(frame))
    status=dict(session_id='session',motion_token='1'*32,held=True,can_move=True,scenario_supported=True,circle_supported=True,operation_mode='AUTO',robot_state='STANDBY')
    hand=dict(position=0,mode=5,error=0,torque_known=True,goal_current_ma=120,limit_ma=250,velocity_limit=2970,acceleration_limit=3447,contact=False)
    current=[0,0,90,0,90,0];operations=[];results={};fault={'step':None}
    def forward(q):return [373,0,262,-90,180,0] if q[2]==90 else [-450 if q[0]<0 else 450,0,400,0,180,0]
    def host(op,settings,body=None):
        if op=='status':return 200,copy.deepcopy(status)
        if op=='kinematics':return 200,dict(session_id='session',current_joints=current[:],current_tcp=[373,0,262,-90,180,0],tcp_name='tool',reference_tcp=[373,0,262,-90,180,0],
                solutions=[[0,0,70,0,110,0] for _ in body.poses],forward=[forward(q) for q in body.joints],hand=copy.deepcopy(hand))
        if op=='operation':
            operations.append(body);identifier=f'{len(operations):032x}';status['motion_token']=f'{len(operations)+1:032x}'
            if body.kind=='joint':current[:]=body.target
            if body.kind in {'line','circle'}:
                for q in [[0,0,90,0,90,0],[-180,0,60,0,120,0],[0,0,60,0,120,0]]:
                    if body.target==forward(q):current[:]=q
            if body.kind=='gripper':hand.update(position=430 if body.contact_ma else body.position,contact=bool(body.contact_ma))
            results[identifier]=dict(state='completed',message='done',next_motion_token=status['motion_token'],feedback=copy.deepcopy(hand),current_joints=current[:])
            if fault.get('wrong_joints')==len(operations):results[identifier]['current_joints']=[0]*6
            if fault['step']==len(operations):results[identifier]=dict(state='failed',message='injected failure')
            return 202,dict(operation_id=identifier,state='running')
        if op.startswith('operations/'):return 200,copy.deepcopy(results[op.split('/')[-1]])
        if op=='operation-stop':results[body.operation_id]=dict(state='cancelled',message='stopped');return 200,results[body.operation_id]
        raise AssertionError(op)
    monkeypatch.setattr('app.robot_scenario.request_host',host)
    with TestClient(create_robot_app()) as client:
        cfg=client.get('/api/robot/pick-scenario').json();cfg['waypoint1_joints']=[-180,0,60,0,120,0]
        assert client.put('/api/robot/pick-scenario',json=cfg).status_code==200
        assert client.put('/api/robot/work-points/box',json=dict(pose=[450,0,120,0,180,0],revision=None)).status_code==200
        assert client.put('/api/robot/gripper/table',json=dict(revision=None,points=[dict(position=0,width_mm=80),dict(position=700,width_mm=0)])).status_code==200
        assert client.put('/api/robot/gripper/contact',json=dict(revision=None,threshold_ma=30)).status_code==200
        points=[[-520,-70,20],[-380,-70,20],[-380,70,20],[-520,70,20]]
        calibration=dict(camera=dict(session_id='camera',camera_generation=1,camera_id='fixture',width=320,height=240),region='floor',revision=None,
            corners=[dict(pixel=p,pose=[*xyz,0,180,0]) for p,xyz in zip([[10,10],[150,10],[150,150],[10,150]],points)])
        assert client.put('/api/robot/pick/calibration',json=calibration).status_code==200
        yield client,operations,frame,status,hand,fault


def wait_state(client,id,states):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        move=client.get('/api/robot/pick/moves/'+id).json()
        if move['state'] in states:return move
        time.sleep(.02)
    raise AssertionError(move)


def prepare(client):
    result=client.post('/api/robot/pick/prepare/preview',json={});assert result.status_code==200,result.text
    ticket=result.json();result=client.post('/api/robot/pick/prepare/approve',json={'approval_id':ticket['approval_id']});assert result.status_code==202,result.text
    assert wait_state(client,ticket['approval_id'],{'completed','failed','unknown'})['state']=='completed'
    return ticket


def preview(client):
    result=client.post('/api/robot/pick/preview',json=dict(session_id='camera',frame_id=1,object_index=0,mode='transfer',handling='rh_p12',scenario=True));assert result.status_code==200,result.text
    return result.json()


def test_virtual_defaults_storage_and_never_issue_tickets(scenario):
    client,calls,*_=scenario
    cfg=client.get('/api/robot/pick-scenario').json()
    assert cfg['virtual']['floor']['center_base_mm']==[-450,0,20]
    assert cfg['virtual']['shelf']['center_base_mm']==[-450,0,300]
    for region in ['floor','shelf']:
        result=client.post('/api/robot/pick/scenario/preview',json={'region':region}).json()
        assert result['simulation_only'] and 'approval_id' not in result
        assert result['opening_mm']==pytest.approx(50) and len(result['steps'])==17
        assert result['steps'][6]['target']==result['steps'][2]['target']
        assert result['steps'][8]['target']==[0,0,60,0,120,0]
        assert result['steps'][10]['target'][:2]==result['steps'][9]['target'][:2]
    assert not calls
    assert client.put('/api/robot/pick-scenario',json={**cfg,'revision':None}).status_code==409
    invalid=copy.deepcopy(cfg);invalid['waypoint1_joints'][0]=300
    assert client.put('/api/robot/pick-scenario',json=invalid).status_code==422
    captured=client.get('/api/robot/work-points/current')
    assert captured.status_code==200 and captured.json()['pose']==[373,0,262,-90,180,0]
    assert not calls


def test_prepare_gates_one_shot_sequence_and_exact_gripper_restore(scenario):
    client,calls,*_=scenario
    blocked=preview(client);assert blocked['motion_reason'] and not calls
    assert client.post('/api/robot/pick/approve',json={'approval_id':blocked['approval_id']}).status_code==409
    prepare(client);assert len(calls)==2
    target=preview(client);assert target['motion_reason'] is None
    with ThreadPoolExecutor(2) as pool:
        codes=list(pool.map(lambda _:client.post('/api/robot/pick/approve',json={'approval_id':target['approval_id']}).status_code,range(2)))
    assert sorted(codes)==[202,409]
    assert wait_state(client,target['approval_id'],{'awaiting_grip','failed','unknown'})['step']==6
    assert len(calls)==6  # preparation 2 + approach, open, line, contact
    scenario[2]['objects']=[]  # The rest of transport does not depend on a camera.
    assert client.put('/api/robot/pick-scenario',json=client.get('/api/robot/pick-scenario').json()).status_code==409
    assert client.post('/api/robot/pick/moves/'+target['approval_id']+'/confirm-grip',json={}).status_code==200
    assert wait_state(client,target['approval_id'],{'awaiting_open','failed','unknown'})['step']==11
    assert len(calls)==11
    assert calls[-1].kind=='line'
    assert client.post('/api/robot/pick/moves/'+target['approval_id']+'/open-at-box',json={}).status_code==200
    assert wait_state(client,target['approval_id'],{'completed','failed','unknown'})['step']==17
    assert len(calls)==17
    assert calls[3].position==calls[11].position  # step4 and step12
    assert calls[1].position==calls[-1].position
    assert calls[8].j1_only and calls[14].j1_only
    assert calls[8].target[1:]==calls[14].target[1:]
    assert client.get('/api/robot/pick/scenario/status').json()['prepared']


@pytest.mark.parametrize('change',['lost','multiple','region','model','preopen'])
def test_revalidation_and_failure_never_send_following_commands(scenario,change):
    client,calls,frame,status,hand,fault=scenario;prepare(client);target=preview(client)
    if change=='lost':frame['objects']=[]
    elif change=='multiple':frame['objects']*=2
    elif change=='model':frame['model_version']='changed'
    elif change=='region':
        cfg=client.get('/api/robot/pick/calibration?region=floor').json();cfg['clearance_per_side_mm']=6;assert client.put('/api/robot/pick/calibration',json=cfg).status_code==200
    else:fault['step']=4
    assert client.post('/api/robot/pick/approve',json={'approval_id':target['approval_id']}).status_code==202
    move=wait_state(client,target['approval_id'],{'failed','unknown'})
    assert move['state'] in {'failed','unknown'}
    assert len(calls)<5  # no pick descent after failed opening, no move after stale target


def test_restart_does_not_resume_waiting_scenario(scenario):
    client,calls,*_=scenario;prepare(client);target=preview(client)
    client.post('/api/robot/pick/approve',json={'approval_id':target['approval_id']})
    wait_state(client,target['approval_id'],{'awaiting_grip'})
    from app.robot import RobotMonitor
    robot=client.app.state.robot
    restored=RobotMonitor(robot.settings,robot.pose_tests.path.parents[4])
    assert not restored.scenario.prepared
    assert all(m['state']=='unknown' for m in restored.picks.moves.values())


def test_two_taught_regions_select_uniquely_and_never_fall_back_to_legacy(scenario):
    client,calls,frame,*_=scenario
    floor=client.get('/api/robot/pick/calibration?region=floor').json()
    shelf=copy.deepcopy(floor);shelf.update(region='shelf',revision=None)
    for corner,xyz in zip(shelf['corners'],[[-450,-70,200],[-450,70,200],[-450,70,340],[-450,-70,340]]):corner['pose']=[*xyz,180,90,90]
    assert client.put('/api/robot/pick/calibration',json=shelf).status_code==200
    result=client.post('/api/robot/pick/preview',json=dict(session_id='camera',frame_id=1,object_index=0,mode='transfer',handling='rh_p12',scenario=True))
    assert result.status_code==409 and '겹칩니다' in result.text
    shelf=client.get('/api/robot/pick/calibration?region=shelf').json()
    for corner in shelf['corners']:corner['pixel'][0]+=155
    assert client.put('/api/robot/pick/calibration',json=shelf).status_code==200
    assert preview(client)['region']=='floor'
    frame['objects'][0]['xyxy']=[215,60,255,100]
    assert preview(client)['region']=='shelf'
    frame['objects'][0]['xyxy']=[120,180,140,210]
    result=client.post('/api/robot/pick/preview',json=dict(session_id='camera',frame_id=1,object_index=0,mode='transfer',handling='rh_p12',scenario=True))
    assert result.status_code==409 and not calls


def test_expired_preparation_cross_endpoint_and_configuration_changes_send_nothing(scenario):
    client,calls,*_=scenario
    ticket=client.post('/api/robot/pick/prepare/preview',json={}).json()
    assert client.post('/api/robot/pick/approve',json={'approval_id':ticket['approval_id']}).status_code==409
    ticket=client.post('/api/robot/pick/prepare/preview',json={}).json()
    client.app.state.robot.picks.approvals[ticket['approval_id']]['deadline']=0
    assert client.post('/api/robot/pick/prepare/approve',json={'approval_id':ticket['approval_id']}).status_code==409
    ticket=client.post('/api/robot/pick/prepare/preview',json={}).json()
    cfg=client.get('/api/robot/pick-scenario').json();cfg['profiles']['high']['joint']['speed']=9
    assert client.put('/api/robot/pick-scenario',json=cfg).status_code==200
    assert client.post('/api/robot/pick/prepare/approve',json={'approval_id':ticket['approval_id']}).status_code==412
    assert not calls


@pytest.mark.parametrize('phase',['status','dispatch'])
@pytest.mark.parametrize('global_stop',[False,True])
def test_stop_during_dispatch_or_status_keeps_lock_until_safe(scenario,monkeypatch,phase,global_stop):
    import importlib
    from threading import Event
    module=importlib.import_module('app.robot_scenario')
    client,calls,*_=scenario;prepare(client);target=preview(client)
    original=module.request_host;entered=Event();release=Event();stops=[];reads=0
    def delayed(op,settings,body=None):
        nonlocal reads
        if op=='status':reads+=1
        if phase=='status' and op=='status' and reads==2:
            entered.set();assert release.wait(5)
        result=original(op,settings,body)
        if phase=='dispatch' and op=='operation':
            entered.set();assert release.wait(5)
        if op=='operation-stop':stops.append(body.operation_id)
        return result
    monkeypatch.setattr(module,'request_host',delayed)
    try:
        assert client.post('/api/robot/pick/approve',json={'approval_id':target['approval_id']}).status_code==202
        assert entered.wait(5)
        for _ in range(2):
            assert client.post('/api/robot/stop' if global_stop else '/api/robot/pick/moves/'+target['approval_id']+'/stop',json={}).status_code==200
        assert client.get('/api/robot/pick/scenario/status').json()['move']['state']=='scenario_stopping'
        assert client.post('/api/robot/pick/prepare/preview',json={}).status_code==409
    finally:release.set()
    assert wait_state(client,target['approval_id'],{'cancelled','failed','unknown'})['state']=='cancelled'
    assert len(calls)==(3 if phase=='dispatch' else 2)
    assert len(stops)==(1 if phase=='dispatch' else 0)


@pytest.mark.parametrize('failed_step',[6,7,9,11,12,13])
def test_failed_contact_route_or_release_never_sends_the_next_step(scenario,failed_step):
    client,calls,_,_,_,fault=scenario;prepare(client);target=preview(client)
    fault['step']=failed_step
    assert client.post('/api/robot/pick/approve',json={'approval_id':target['approval_id']}).status_code==202
    if failed_step>6:
        assert wait_state(client,target['approval_id'],{'awaiting_grip','failed','unknown'})['state']=='awaiting_grip'
        assert len(calls)==6
        assert client.post('/api/robot/pick/moves/'+target['approval_id']+'/confirm-grip',json={}).status_code==200
    if failed_step>11:
        assert wait_state(client,target['approval_id'],{'awaiting_open','failed','unknown'})['state']=='awaiting_open'
        assert len(calls)==11
        assert client.post('/api/robot/pick/moves/'+target['approval_id']+'/open-at-box',json={}).status_code==200
    move=wait_state(client,target['approval_id'],{'failed','unknown'})
    assert move['step']==failed_step and len(calls)==failed_step
    assert client.post('/api/robot/pick/moves/'+target['approval_id']+'/confirm-grip',json={}).status_code==409
    assert client.post('/api/robot/pick/moves/'+target['approval_id']+'/open-at-box',json={}).status_code==409
    assert len(calls)==failed_step


def test_duplicate_gate_approvals_and_status_refresh_do_not_repeat_commands(scenario):
    client,calls,*_=scenario;prepare(client);target=preview(client)
    client.post('/api/robot/pick/approve',json={'approval_id':target['approval_id']})
    for state,action,next_state,count in [('awaiting_grip','confirm-grip','awaiting_open',11),('awaiting_open','open-at-box','completed',17)]:
        assert wait_state(client,target['approval_id'],{state})['state']==state
        before=len(calls)
        for _ in range(3):
            assert client.get('/api/robot/pick/scenario/status').status_code==200
            assert client.get('/api/robot/pick/active').status_code==200
        assert len(calls)==before
        with ThreadPoolExecutor(2) as pool:
            codes=list(pool.map(lambda _:client.post('/api/robot/pick/moves/'+target['approval_id']+'/'+action,json={}).status_code,range(2)))
        assert sorted(codes)==[200,409]
        assert wait_state(client,target['approval_id'],{next_state,'failed','unknown'})['state']==next_state
        assert len(calls)==count


def test_wrong_plane_orientation_is_rejected_without_replacing_taught_floor(scenario):
    client,calls,*_=scenario
    floor=client.get('/api/robot/pick/calibration?region=floor').json()
    bad=copy.deepcopy(floor);bad.update(region='shelf',revision=None)
    assert client.put('/api/robot/pick/calibration',json=bad).status_code==422
    for corner,xyz in zip(bad['corners'],[[-450,-70,200],[-450,70,200],[-450,70,340],[-450,-70,340]]):corner['pose']=[*xyz,180,90,90]
    bad.update(region='floor',revision=floor['revision'])
    assert client.put('/api/robot/pick/calibration',json=bad).status_code==422
    assert client.get('/api/robot/pick/calibration?region=floor').json()==floor
    assert not calls


@pytest.mark.parametrize('invalid',['contact','mode','current','velocity','acceleration'])
def test_invalid_hand_configuration_blocks_preparation_before_motion(scenario,invalid):
    client,calls,_,_,hand,_=scenario
    if invalid=='contact':
        contact=client.get('/api/robot/gripper/contact').json();contact['threshold_ma']=None
        assert client.put('/api/robot/gripper/contact',json=contact).status_code==200
    elif invalid=='mode':hand['mode']=3
    elif invalid=='current':hand['goal_current_ma']=hand['limit_ma']+1
    elif invalid=='velocity':hand['velocity_limit']=999
    else:hand['acceleration_limit']=2999
    assert client.post('/api/robot/pick/prepare/preview',json={}).status_code==409
    assert not calls


def test_stage_joint_targets_saved_previewed_and_resolved(scenario):
    client, calls, *_ = scenario
    config = client.get('/api/robot/pick-scenario').json()
    for number in [3, 5, 7, 9, 10, 11, 13, 14, 15, 16]:
        config['stages'][str(number)]['target_joints'] = [number, 0, 60, 0, 120, 0]
    saved = client.put('/api/robot/pick-scenario', json=config)
    assert saved.status_code == 200, saved.text
    result = client.post('/api/robot/pick/scenario/preview', json={'region': 'floor'}).json()
    for step in result['steps']:
        number = step['number']
        if number in [9, 10, 11, 13, 14, 15, 16]:
            assert step['space'] == 'joint'
            assert step['target'] == [number, 0, 60, 0, 120, 0]
    assert not calls
    prepare(client)
    actual = preview(client)
    assert actual['motion_reason'] is None, actual
    assert client.get('/api/robot/pick-scenario').json()['stages']['5']['target_joints'] is None
    invalid = client.get('/api/robot/pick-scenario').json()
    invalid['stages']['9']['target_joints'][2] = 156
    assert client.put('/api/robot/pick-scenario', json=invalid).status_code == 422


def test_object_coordinates_drive_automatic_grasp_steps(scenario):
    client, calls, *_ = scenario
    config = client.get('/api/robot/pick-scenario').json()
    config['virtual']['floor'] = dict(center_base_mm=[-420, 15, 60], size_mm=[50, 40, 120])
    for number in [3, 5, 7]:
        config['stages'][str(number)]['target_joints'] = [1, 2, 3, 4, 5, 6]
    response = client.post('/api/robot/pick/scenario/preview', json={'region': 'floor', 'config': config})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['steps'][4]['target'][:3] == [-420, 15, 60]
    assert result['steps'][2]['target'][:3] == [-420, 15, 160]
    assert result['steps'][6]['target'] == result['steps'][2]['target']
    assert result['steps'][3]['opening_mm'] == pytest.approx(60)
    for number in [3, 5, 7]:
        assert result['steps'][number - 1]['space'] == 'task'
        assert result['config']['stages'][str(number)]['target_joints'] is None
    assert not calls


def test_hardware_test_preview_approval_and_full_path_without_camera(scenario):
    client, calls, frame, *_ = scenario
    frame['objects'] = []
    config = client.get('/api/robot/pick-scenario').json()
    response = client.post('/api/robot/pick/scenario/hardware-test/preview',
                           json={'region': 'floor', 'revision': config['revision']})
    assert response.status_code == 200, response.text
    target = response.json()
    assert len(target['steps']) == 17
    assert target['steps'][1]['title'] == '설정한 객체 좌표 확인'
    assert calls == []
    identifier = target['approval_id']
    with ThreadPoolExecutor(2) as pool:
        codes = list(pool.map(lambda _: client.post('/api/robot/pick/scenario/hardware-test/approve',
                                                   json={'approval_id': identifier}).status_code, range(2)))
    assert sorted(codes) == [202, 409]
    assert wait_state(client, identifier, {'completed', 'failed', 'unknown'})['state'] == 'completed'
    assert len(calls) == 17


def test_hardware_test_rejects_changed_settings_and_wrong_approval_route(scenario):
    client, calls, *_ = scenario
    config = client.get('/api/robot/pick-scenario').json()
    endpoint = '/api/robot/pick/scenario/hardware-test/'
    assert client.post(endpoint+'preview', json={'revision': 'outdated'}).status_code == 412
    target = client.post(endpoint+'preview', json={'revision': config['revision']}).json()
    assert client.post('/api/robot/pick/prepare/approve', json={'approval_id': target['approval_id']}).status_code == 409
    target = client.post(endpoint+'preview', json={'revision': config['revision']}).json()
    assert client.put('/api/robot/pick-scenario', json=config).status_code == 200
    assert client.post(endpoint+'approve', json={'approval_id': target['approval_id']}).status_code == 412
    assert calls == []


def test_hardware_test_rejects_settings_changed_during_inspection(scenario, monkeypatch):
    client, calls, *_ = scenario
    engine = client.app.state.robot.scenario
    original = engine.inspect_steps

    async def changed(*args):
        result = await original(*args)
        engine.config.save(ScenarioInput.model_validate(engine.config.value))
        return result

    monkeypatch.setattr(engine, 'inspect_steps', changed)
    response = client.post('/api/robot/pick/scenario/hardware-test/preview',
                           json={'revision': engine.config.value['revision']})
    assert response.status_code == 412
    assert calls == []


def test_hardware_test_accepts_displayed_example_draft_without_saving(scenario):
    client, calls, frame, *_ = scenario
    original = client.get('/api/robot/pick-scenario').json()
    example = client.post('/api/robot/pick/scenario/preview', json={'example': True}).json()
    example['config']['virtual']['floor']['center_base_mm'] = [-430, 0, 30]
    endpoint = '/api/robot/pick/scenario/hardware-test/'
    response = client.post(endpoint+'preview', json={'revision': original['revision'], 'config': example['config'],
                                                    'box_pose': example['box_pose']})
    assert response.status_code == 200, response.text
    assert response.json()['pose'][:3] == [-430, 0, 30]
    assert not calls
    identifier = response.json()['approval_id']
    frame['objects'] = []
    assert client.post(endpoint+'approve', json={'approval_id': identifier}).status_code == 202
    assert wait_state(client, identifier, {'completed', 'failed', 'unknown'})['state'] == 'completed'
    assert calls[4].target[:3] == [-430, 0, 30]
    assert client.get('/api/robot/pick-scenario').json() == original
    assert not client.get('/api/robot/pick/scenario/status').json()['prepared']


@pytest.mark.parametrize('number', range(1, 18))
def test_single_stage_preview_and_approval_execute_selected_actions(scenario, number):
    client, calls, _, _, hand, *_ = scenario
    has_gripper = number in {1, 4, 6, 12, 17}
    if not has_gripper:hand['goal_current_ma'] = 0
    config = client.get('/api/robot/pick-scenario').json()
    endpoint = '/api/robot/pick/scenario/hardware-step/'
    response = client.post(endpoint+'preview', json={'revision': config['revision'], 'step': number})
    assert response.status_code == 200, response.text
    target = response.json()
    assert len(target['steps']) == (2 if has_gripper else 1)
    assert target['step'] == number
    assert not calls
    identifier = target['approval_id']
    assert client.post(endpoint+'approve', json={'approval_id': identifier}).status_code == 202
    move = wait_state(client, identifier, {'completed', 'failed', 'unknown'})
    assert move['state'] == 'completed', move
    assert len(calls) == (2 if has_gripper else 1)
    assert calls[0].kind in {'joint', 'line', 'circle'}
    assert not calls[0].j1_only
    if has_gripper:
        assert calls[1].kind == 'gripper'
        assert calls[1].position == target['steps'][1]['position']
        assert bool(calls[1].contact_ma) == (number == 6)
    assert client.post(endpoint+'approve', json={'approval_id': identifier}).status_code == 409


@pytest.mark.parametrize('failed_operation', [1, 2])
def test_single_gripper_stage_stops_after_failed_operation(scenario, failed_operation):
    client, calls, _, _, _, fault = scenario
    config = client.get('/api/robot/pick-scenario').json()
    endpoint = '/api/robot/pick/scenario/hardware-step/'
    target = client.post(endpoint+'preview', json={'revision': config['revision'], 'step': 4}).json()
    assert not calls
    fault['step'] = failed_operation
    assert client.post(endpoint+'approve', json={'approval_id': target['approval_id']}).status_code == 202
    move = wait_state(client, target['approval_id'], {'completed', 'failed', 'unknown'})
    assert move['state'] != 'completed'
    assert len(calls) == failed_operation
    assert calls[0].kind != 'gripper'
    if failed_operation == 2:assert calls[1].kind == 'gripper'


def test_opening_stage_allows_unset_contact_but_grasp_requires_it(scenario):
    client, calls, *_ = scenario
    contact = client.get('/api/robot/gripper/contact').json()
    contact['threshold_ma'] = None
    assert client.put('/api/robot/gripper/contact', json=contact).status_code == 200
    config = client.get('/api/robot/pick-scenario').json()
    endpoint = '/api/robot/pick/scenario/hardware-step/preview'
    assert client.post(endpoint, json={'revision': config['revision'], 'step': 4}).status_code == 200
    assert client.post(endpoint, json={'revision': config['revision'], 'step': 6}).status_code == 409
    assert not calls


def test_global_stop_invalidates_pending_approvals_without_motion(scenario):
    client, calls, *_ = scenario
    config = client.get('/api/robot/pick-scenario').json()
    target = client.post('/api/robot/pick/scenario/hardware-test/preview', json={'revision': config['revision']}).json()
    response = client.post('/api/robot/stop', json={})
    assert response.status_code == 200
    assert response.json()['state'] == 'idle'
    assert client.post('/api/robot/pick/scenario/hardware-test/approve', json={'approval_id': target['approval_id']}).status_code == 409
    assert not calls


def test_global_stop_cancels_inflight_test_preview(scenario, monkeypatch):
    client, calls, *_ = scenario
    engine = client.app.state.robot.scenario
    original = engine.inspect_steps

    async def stopped(*args):
        result = await original(*args)
        engine.stop_all()
        return result

    monkeypatch.setattr(engine, 'inspect_steps', stopped)
    response = client.post('/api/robot/pick/scenario/hardware-test/preview',
                           json={'revision': engine.config.value['revision']})
    assert response.status_code == 412
    assert not calls
