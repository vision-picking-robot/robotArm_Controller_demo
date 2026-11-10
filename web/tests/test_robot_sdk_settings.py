"""Runtime LAN configuration must never fall back to the installation template."""
import pytest
from scripts import check_robot_sdk_host as sdk


@pytest.fixture
def settings_root(tmp_path,monkeypatch):
    monkeypatch.setattr(sdk,'ROOT',tmp_path)
    for key in ('ROBOT_IP','ROBOT_PORT','ROBOT_LAN_INTERFACE'):monkeypatch.delenv(key,raising=False)
    return tmp_path


def test_env_file_is_used_without_reading_template(settings_root):
    # Invalid UTF-8 would fail if the template were opened at all.
    (settings_root/'.env.example').write_bytes(b'\xff')
    (settings_root/'.env').write_text('# ROBOT_IP=110.120.1.63\nROBOT_IP="110.120.1.56"\nROBOT_PORT=12345\nROBOT_LAN_INTERFACE=\'en7\'\nUNRELATED_SECRET=ignored\n')
    assert sdk.lan_settings()=={'ROBOT_IP':'110.120.1.56','ROBOT_PORT':'12345','ROBOT_LAN_INTERFACE':'en7'}


def test_template_does_not_supply_missing_env_file(settings_root):
    (settings_root/'.env.example').write_text('ROBOT_IP=110.120.1.63\nROBOT_PORT=12345\nROBOT_LAN_INTERFACE=en7\n')
    assert sdk.lan_settings()=={}


def test_template_does_not_supply_missing_keys(settings_root):
    (settings_root/'.env.example').write_text('ROBOT_IP=110.120.1.63\nROBOT_PORT=12345\nROBOT_LAN_INTERFACE=en7\n')
    (settings_root/'.env').write_text('ROBOT_IP=110.120.1.56\n')
    assert sdk.lan_settings()=={'ROBOT_IP':'110.120.1.56'}


def test_process_environment_overrides_env_file_and_can_run_without_it(settings_root,monkeypatch):
    (settings_root/'.env').write_text('ROBOT_IP=110.120.1.63\nROBOT_PORT=12345\nROBOT_LAN_INTERFACE=en7\n')
    expected={'ROBOT_IP':'110.120.1.56','ROBOT_PORT':'23456','ROBOT_LAN_INTERFACE':'en8'}
    for key,value in expected.items():monkeypatch.setenv(key,value)
    assert sdk.lan_settings()==expected
    (settings_root/'.env').unlink()
    assert sdk.lan_settings()==expected
