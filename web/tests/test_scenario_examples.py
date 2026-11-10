import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.robot_web import create_robot_app


@pytest.mark.parametrize('region',['floor','shelf'])
def test_zero_setup_example_is_complete_without_sdk_camera_or_teaching_writes(tmp_path,monkeypatch,region):
    monkeypatch.setenv('ROBOT_STATE_DIR',str(tmp_path));monkeypatch.setenv('ROBOT_ROS_PC_IP','');monkeypatch.setenv('ROBOT_ACCESS_BACKEND','ros')
    def forbidden(*args,**kwargs):raise AssertionError('Examples must not contact hardware or cameras')
    monkeypatch.setattr('app.robot_scenario.request_host',forbidden);monkeypatch.setattr('app.robot_pick.read_camera',forbidden)
    with TestClient(create_robot_app()) as client:
        # The complete example is independent of a previously taught physical floor.
        assert client.put('/api/robot/floor-reference',json={'revision':None,'z_mm':1000}).status_code==200
        before=client.get('/api/robot/pick-scenario').json();points=client.get('/api/robot/status').json()['work_points']
        parcel=client.get('/api/robot/parcel-box').json();files={p:p.read_bytes() for p in tmp_path.rglob('*.json')}
        assert before['waypoint1_joints'] is None and points['box']['pose'] is None
        response=client.post('/api/robot/pick/scenario/preview',json={'example':True,'region':region});assert response.status_code==200,response.text
        example=response.json();assert example['simulation_only'] and example['example'] and not example['errors']
        assert 'approval_id' not in example and example['floor_z_mm']==0 and len(example['steps'])==17
        assert example['config']['waypoint1_joints']==[-180,0,60,0,120,0]
        assert example['steps'][9]['target'][2]==320 and example['steps'][10]['target']==[450,0,120,0,180,0]
        assert example['opening_mm']==pytest.approx(50) and example['idle_opening_mm']==80
        assert example['box']['center_base_mm']==[450,0,0]
        assert client.get('/api/robot/pick-scenario').json()==before
        assert client.get('/api/robot/status').json()['work_points']==points
        assert client.get('/api/robot/parcel-box').json()==parcel
        assert {p:p.read_bytes() for p in tmp_path.rglob('*.json')}==files
        assert client.post('/api/robot/pick/prepare/approve',json={'approval_id':'example'}).status_code==409
        assert not client.app.state.robot.picks.busy
        fixture=json.loads((Path(__file__).parent/'fixtures'/f'scenario-{region}.json').read_text())
        assert example==fixture  # These exact response fixtures are replayed by the JS kinematics tests.
