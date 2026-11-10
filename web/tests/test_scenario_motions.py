import copy
import pytest
from app.robot_arc import validate_arc
from app.robot_scenario_config import ScenarioInput
from app.store import DomainError
from tests.test_robot_scenario import scenario,prepare,preview,wait_state


def test_saved_legacy_stage_settings_keep_existing_motion_defaults():
    old=ScenarioInput().model_dump()
    for s in old['stages'].values():s.pop('motion');s.pop('via_pose')
    restored=ScenarioInput.model_validate(old).model_dump()
    assert all(s['motion'] is None for s in restored['stages'].values())
    for n,method in [(5,'movej'),(9,'movec'),(11,'movec'),(15,'movel'),(4,'movej')]:
        bad=copy.deepcopy(old);bad['stages'][str(n)]['motion']=method
        with pytest.raises(ValueError):ScenarioInput.model_validate(bad)


def test_incomplete_arc_is_saved_but_readonly_preview_cannot_play_or_issue_ticket(scenario):
    client,calls,*_=scenario;cfg=client.get('/api/robot/pick-scenario').json();cfg['stages']['8']['motion']='movec'
    assert client.put('/api/robot/pick-scenario',json=cfg).status_code==200
    result=client.post('/api/robot/pick/scenario/preview',json={}).json()
    assert result['errors'] and '8단계' in ' '.join(result['errors']) and 'approval_id' not in result and not calls


@pytest.mark.parametrize('method',['movel','movec'])
def test_selected_method_speed_and_arc_reach_sdk_once_and_keep_gates(scenario,method):
    client,calls,*_=scenario;cfg=client.get('/api/robot/pick-scenario').json()
    cfg['stages']['3'].update(motion=method,via_pose=[-100,100,300,0,180,0] if method=='movec' else None)
    cfg['profiles']['high']['line'].update(speed=9,acceleration=8)
    assert client.put('/api/robot/pick-scenario',json=cfg).status_code==200
    prepare(client);ticket=preview(client);assert ticket['motion_reason'] is None
    step=ticket['steps'][2];assert step['motion']==method and step['speed']==dict(speed=9,acceleration=8)
    client.post('/api/robot/pick/approve',json={'approval_id':ticket['approval_id']})
    assert wait_state(client,ticket['approval_id'],{'awaiting_grip','failed','unknown'})['state']=='awaiting_grip'
    assert len(calls)==6 and calls[2].kind==('circle' if method=='movec' else 'line')
    assert calls[2].target==ticket['approach'] and calls[2].via_pose==cfg['stages']['3']['via_pose']


def test_cartesian_joint_waypoint_uses_controller_tcp_and_refuses_wrong_joint_branch(scenario):
    client,calls,_,_,_,fault=scenario;cfg=client.get('/api/robot/pick-scenario').json();cfg['stages']['8']['motion']='movel'
    assert client.put('/api/robot/pick-scenario',json=cfg).status_code==200
    prepare(client);ticket=preview(client);assert ticket['motion_reason'] is None
    step=ticket['steps'][7];assert step['target']==[-450,0,400,0,180,0] and step['space']=='task'
    assert step['expected_joints']==cfg['waypoint1_joints']
    fault['wrong_joints']=8
    client.post('/api/robot/pick/approve',json={'approval_id':ticket['approval_id']})
    wait_state(client,ticket['approval_id'],{'awaiting_grip'})
    client.post('/api/robot/pick/moves/'+ticket['approval_id']+'/confirm-grip',json={})
    move=wait_state(client,ticket['approval_id'],{'failed','unknown'})
    assert move['state']=='unknown' and len(calls)==8 and '관절 자세' in move['message']


def test_old_sdk_or_invalid_arc_blocks_before_first_pick_motion(scenario):
    client,calls,_,status,*_=scenario;cfg=client.get('/api/robot/pick-scenario').json();cfg['stages']['3'].update(motion='movec',via_pose=[0,50,200,0,180,0])
    assert client.put('/api/robot/pick-scenario',json=cfg).status_code==200
    prepare(client);status['circle_supported']=False
    ticket=preview(client);assert 'SDK' in ticket['motion_reason']
    assert client.post('/api/robot/pick/approve',json={'approval_id':ticket['approval_id']}).status_code==409 and len(calls)==2
    status['circle_supported']=True
    cfg=client.get('/api/robot/pick-scenario').json();cfg['stages']['3']['via_pose']=[373,0,262,-90,180,0]
    client.put('/api/robot/pick-scenario',json=cfg);prepare(client)
    ticket=preview(client);assert '일직선' in ticket['motion_reason'] and len(calls)==4


@pytest.mark.parametrize('via,end',[([0,0,0],[10,0,0]),([5,0,0],[10,0,0]),([5,.00001,0],[10,0,0])])
def test_coincident_and_nearly_collinear_arcs_are_rejected(via,end):
    with pytest.raises(DomainError):validate_arc([0]*6,via+[0]*3,end+[0]*3)
