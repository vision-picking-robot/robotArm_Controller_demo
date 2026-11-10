import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('endurance_test', ROOT / 'scripts/endurance_test.py')
endurance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(endurance)


def test_mock_cycles_move_arm_and_gripper(tmp_path):
    log = tmp_path / 'run.jsonl'
    assert endurance.main(['--cycles', '3', '--dwell', '0', '--log', str(log)]) == 0
    events = [json.loads(line)['event'] for line in log.read_text().splitlines()]
    assert events.count('cycle_ok') == 3
    assert events.count('arm_arrived') == 9 and events.count('gripper_cycled') == 3
    assert events[-2:] == ['move_stop_sent', 'end']


def test_real_mode_requires_confirmation():
    with pytest.raises(SystemExit):
        endurance.main(['--mode', 'real'])


def test_speed_over_limit_stops_without_motion(tmp_path):
    log = tmp_path / 'run.jsonl'
    assert endurance.main(['--cycles', '1', '--vel', '90', '--log', str(log)]) == 1
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(r['event'] == 'arm_arrived' for r in records)
    assert any(r['event'] == 'error' for r in records)


def test_drl_program_compiles_and_encodes_bytes():
    from vision_picking.drl_gripper import build_program
    for target in (-1, 0, 740):
        code = build_program(target, 42)
        compile(code, 'drl', 'exec')
        assert '_put(12, 42, 1)' in code and 'flange_serial_close()' in code
    with pytest.raises(ValueError):
        build_program(741, 1)


def test_project_cycle_mock_applies_tcp_offset(tmp_path):
    spec2 = importlib.util.spec_from_file_location('project_cycle', ROOT / 'scripts/project_cycle.py')
    cycle = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(cycle)
    log = tmp_path / 'pc.jsonl'
    assert cycle.main(['--cycles', '1', '--log', str(log)]) == 0
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert next(r for r in records if r['event'] == 'tcp_offset')['applied_offset'] == 120.0
    descend = next(r for r in records if r.get('name') == 'descend')
    assert descend['tcp'][2] == 120.0
    steps = {r['name']: r for r in records if r['event'] == 'step'}
    assert steps['lift']['tcp'][2] == 370.0 and steps['home_carry']['tcp'][2] == 385.0
    assert steps['box_place']['tcp'][2] == 185.0 and steps['box_retreat']['tcp'][2] == 385.0
    assert [r['event'] for r in records].count('cycle_ok') == 1


def test_remote_rfid_requires_api():
    spec2 = importlib.util.spec_from_file_location('project_cycle_r', ROOT / 'scripts/project_cycle.py')
    cycle = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(cycle)
    with pytest.raises(SystemExit):
        cycle.main(['--remote-rfid'])
