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
