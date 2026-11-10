"""Exercise the host HTTP API through a fake SDK worker, without robot networking."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.robot_host_access import AccessServer, create_host_app
from app.robot_web import create_robot_app

BASE = "/api/robot/host-access"
ROUTE = {"interface": "en7", "source_ip": "110.120.1.11", "ip": "110.120.1.56", "port": 12345}
# Real pipes and process exit, including asynchronous callbacks and unsolicited loss.
FAKE = """
import sys, time
print('E0509 sdk GL010117')
print('E0509 feature servo_on')
print('E0509 feature operation_mode')
print('E0509 feature line')
print('E0509 feature rh_p12')
print('E0509 connected GF02100300')
state = 'SAFE_OFF MANUAL REAL'
print('E0509 ready ' + state, flush=True)
for command in sys.stdin:
    with open(sys.argv[1], 'a') as log: log.write(command)
    time.sleep(.25)
    callback = open(sys.argv[2]).read()
    if command.startswith('mode '):
        _, _, token, target = command.split()
        if callback == 'FAIL':
            print('E0509 mode_result ' + token + ' failed', flush=True)
        else:
            state = 'STANDBY ' + target + ' REAL'
            print('E0509 mode_result ' + token + ' ' + target, flush=True)
            print('E0509 ready ' + state, flush=True)
        continue
    if command.startswith(('servo ', 'recover ')):
        token = command.split()[2]
        if callback == 'DROP': sys.exit(0)
        if callback == 'LOSS': print('E0509 callback LOSS', flush=True)
        print('E0509 servo ' + token + (' refused' if callback=='REFUSED' else ' accepted'), flush=True)
        if callback == 'STANDBY':
            time.sleep(.12)
            state='STANDBY MANUAL REAL'
            print('E0509 ready ' + state, flush=True)
        continue
    if command.startswith('hand '):
        _, grant, pos, contact = command.split()
        if callback == 'DROP': sys.exit(0)
        print('E0509 hand completed ok 349 14 150 300 5 0',flush=True)
        print('E0509 ready ' + state,flush=True)
        continue
    if command.startswith(('move ', 'line ')):
        if callback == 'DROP': sys.exit(0)
        print('E0509 pose completed', flush=True)
        print('E0509 ready ' + state, flush=True)
        continue
    if '|' in callback:
        callback, state = callback.split('|')
        print('E0509 ready ' + state, flush=True)
    print('E0509 callback ' + callback.split(',')[0], flush=True)
    if ',' in callback:
        time.sleep(.25)
        if callback.endswith('EXIT'): sys.exit(1)
        print('E0509 callback LOSS', flush=True)
"""


class HostAccessTests(unittest.TestCase):
    def setUp(self):
        self.log = self.enterContext(tempfile.NamedTemporaryFile())
        responses = self.enterContext(tempfile.NamedTemporaryFile())
        self.response_file = Path(responses.name)
        self.response_file.write_text("GRANT")
        with patch('app.robot_host_access.lan_settings',return_value={'ROBOT_IP':ROUTE['ip'],'ROBOT_PORT':str(ROUTE['port']),'ROBOT_LAN_INTERFACE':ROUTE['interface']}):
            self.owner = AccessServer(route_lookup=lambda _: ROUTE, spawn=self.spawn,
                                      retry_delay=.05, callback_timeout=.08)
        self.client = self.enterContext(TestClient(create_host_app(self.owner), base_url="http://127.0.0.1"))
        self.first = self.until(lambda s: s["can_reclaim"])
        self.ticket = {key: self.first[key] for key in ("session_id", "revision")}

    async def spawn(self, *_):
        return await asyncio.create_subprocess_exec(
            sys.executable, "-uc", FAKE, self.log.name, self.response_file,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)

    def until(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            status = self.client.get(BASE + "/status").json()
            if predicate(status):
                return status
            time.sleep(.01)
        self.fail(f"Status did not converge: {status}")

    def force(self, ticket=None):
        return self.client.post(BASE + "/reclaim", json=ticket or self.ticket)

    def test_two_tabs_timeout_late_grant_and_refresh(self):
        self.assertFalse(self.first["held"])
        self.assertEqual(self.log.read(), b"")
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: self.force().status_code, range(2)))
        self.assertEqual(codes, [200, 200])
        self.until(lambda s: s["result"] == "unknown" and not s["can_reclaim"] and not s["held"])
        self.force({**self.ticket, "revision": 1})
        held = self.until(lambda s: s["held"])
        self.assertEqual((held["session_id"], held["last_callback"]), (self.first["session_id"], "GRANT"))
        self.assertIsNotNone(held["callback_at"])
        # Another page load is a GET, and even confirming with the latest revision cannot re-force.
        self.assertEqual(self.force({**self.ticket, "revision": 1}).json()["session_id"], held["session_id"])
        self.assertEqual(self.log.read(), b"force\n")

    def test_deny_old_tab_cannot_retry_then_loss(self):
        self.response_file.write_text("DENY")
        self.force()
        denied = self.until(lambda s: s["result"] == "denied")
        self.assertEqual(denied["last_callback"], "DENY")
        self.force()  # dialog from another tab still references revision 0
        self.response_file.write_text("GRANT,LOSS")
        self.force({**self.ticket, "revision": 1})
        self.until(lambda s: s["held"])
        lost = self.until(lambda s: s["result"] == "lost")
        self.assertTrue(lost["can_reclaim"])
        self.assertFalse(lost["held"])
        self.assertEqual(self.log.read(), b"force\nforce\n")

    def test_disconnect_expires_grant_and_confirmation(self):
        self.response_file.write_text("GRANT,EXIT")
        self.force()
        self.until(lambda s: s["held"])
        fresh = self.until(lambda s: s["can_reclaim"] and s["session_id"] != self.ticket["session_id"])
        self.assertFalse(fresh["held"])
        self.assertIsNone(fresh["last_callback"])
        self.assertEqual(self.force().status_code, 409)
        self.assertEqual(self.log.read(), b"force\n")

    def test_target_mismatch_and_changed_lan_never_send_force(self):
        wrong = self.client.post(BASE + "/reclaim", json=self.ticket, headers={"X-Robot-Target": "127.0.0.9:12345"})
        self.assertEqual(wrong.status_code, 409)
        def wrong_route(_):
            raise ValueError("en0 instead of en7")
        self.owner.route_lookup = wrong_route
        self.assertEqual(self.force().status_code, 409)
        fail = self.until(lambda s: s["connection"] == "disconnected")
        self.assertFalse(fail["held"] or fail["can_reclaim"])
        self.assertEqual(self.log.read(), b"")

    def test_mode_confirmation_and_readback_gate_motion(self):
        self.force()
        off = self.until(lambda s: s["held"])
        self.assertIsNone(off["mode_change"]["token"])
        self.client.portal.call(self.owner.event, "ready", "STANDBY MANUAL REAL")
        ready = self.until(lambda s: s["mode_change"]["token"])
        def change(s):
            return self.client.post(BASE + "/mode", json={"session_id": s["session_id"],
                "mode_token": s["mode_change"]["token"], "target": "AUTO"})
        self.response_file.write_text("FAIL")
        self.assertEqual(change(ready).status_code, 202)
        self.assertEqual(change(ready).status_code, 409)
        failed = self.until(lambda s: s["mode_change"]["result"] == "failed")
        self.assertFalse(failed["can_move"])
        self.response_file.write_text("OK")
        self.assertEqual(change(failed).status_code, 202)
        auto = self.until(lambda s: s["mode_change"]["result"] == "confirmed")
        self.assertEqual(auto["operation_mode"], "AUTO")
        self.assertTrue(auto["can_move"])
        self.assertEqual(len(self.log.read().splitlines()), 3)

    def servo(self, status, headers=None):
        return self.client.post(BASE + "/servo-on", json={"session_id": status["session_id"],
            "servo_token": status["servo"]["token"]}, headers=headers)

    def test_servo_on_deduplicates_and_waits_for_observed_standby(self):
        self.assertIsNone(self.first["servo"]["token"])
        self.force()
        ready = self.until(lambda s: s["held"] and s["servo"]["token"])
        self.assertEqual(self.servo(ready, {"Origin": "http://other.test"}).status_code, 403)
        self.assertEqual(self.servo(ready, {"X-Robot-Target": "127.0.0.9:12345"}).status_code, 409)
        self.response_file.write_text("STANDBY")
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: self.servo(ready), range(2)))
        self.assertEqual(sorted(r.status_code for r in replies), [202, 409])
        delayed = self.until(lambda s: s["servo"]["result"] == "unknown")
        self.assertIsNone(delayed["servo"]["token"])
        self.assertEqual(self.servo(ready).status_code, 409)
        done = self.until(lambda s: s["servo"]["result"] == "confirmed")
        self.assertEqual((done["robot_state"], done["operation_mode"], done["can_move"]), ("STANDBY", "MANUAL", False))
        self.assertIsNone(done["servo"]["token"])
        self.assertEqual(self.log.read().splitlines(), [b"force", f"servo 1 {ready['servo']['token']}".encode()])

    def test_servo_ticket_expires_on_safety_state_and_grant_loss(self):
        self.force()
        ready = self.until(lambda s: s["servo"]["token"])
        for state in ("SAFE_STOP MANUAL REAL", "EMERGENCY_STOP MANUAL REAL", "SAFE_OFF MANUAL OTHER"):
            self.client.portal.call(self.owner.event, "ready", state)
            self.assertEqual(self.servo(ready).status_code, 409)
        self.client.portal.call(self.owner.event, "ready", "SAFE_OFF MANUAL REAL")
        self.assertEqual(self.servo(ready).status_code, 409)
        current = self.until(lambda s: s["servo"]["token"])
        self.response_file.write_text("LOSS")
        self.assertEqual(self.servo(current).status_code, 202)
        self.until(lambda s: not s["held"] and not s["servo"]["token"])
        self.client.portal.call(self.owner.event, "callback", "GRANT")
        self.assertEqual(self.servo(current).status_code, 409)
        next_grant = self.until(lambda s: s["servo"]["token"])
        self.response_file.write_text("REFUSED")
        self.servo(next_grant)
        refused = self.until(lambda s: s["servo"]["result"] == "failed")
        self.assertNotEqual(refused["servo"]["token"], next_grant["servo"]["token"])
        self.response_file.write_text("DROP")
        self.servo(refused)
        fresh = self.until(lambda s: s["can_reclaim"] and s["session_id"] != ready["session_id"])
        self.assertIsNone(fresh["servo"]["token"])
        self.assertEqual(self.servo(refused).status_code, 409)

    def test_safe_stop_recovery_is_feature_gated_state_bound_and_observed(self):
        self.force()
        off = self.until(lambda s: s["servo"]["token"])
        self.client.portal.call(self.owner.event, "ready", "SAFE_STOP MANUAL REAL")
        unsupported = self.client.get(BASE + "/status").json()
        self.assertIsNone(unsupported["servo"]["token"])
        self.client.portal.call(self.owner.event, "feature", "safe_stop_reset")
        ready = self.until(lambda s: s["servo"]["token"])
        self.assertNotEqual(ready["servo"]["token"], off["servo"]["token"])
        self.assertEqual(self.servo(off).status_code, 409)
        self.response_file.write_text("STANDBY")
        self.assertEqual(self.servo(ready).status_code, 202)
        self.assertEqual(self.servo(ready).status_code, 409)

        pending = self.until(lambda s: s["servo"]["result"] == "unknown")
        self.assertEqual(pending["robot_state"], "SAFE_STOP")
        self.assertIsNone(pending["servo"]["token"])
        done = self.until(lambda s: s["servo"]["result"] == "confirmed")
        self.assertEqual(done["robot_state"], "STANDBY")
        self.assertEqual(self.log.read().splitlines(), [b"force", f"recover 1 {ready['servo']['token']}".encode()])
        self.client.portal.call(self.owner.event, "ready", "EMERGENCY_STOP MANUAL REAL")
        emergency = self.client.get(BASE + "/status").json()
        self.assertIsNone(emergency["servo"]["token"])
        self.assertEqual(self.servo(ready).status_code, 409)

    def test_safe_stop_control_reclaim_requires_updated_worker(self):
        self.client.portal.call(self.owner.event, "ready", "SAFE_STOP AUTO REAL")
        self.assertFalse(self.client.get(BASE + "/status").json()["can_reclaim"])
        self.assertEqual(self.force().status_code, 409)
        self.client.portal.call(self.owner.event, "feature", "safe_stop_reset")
        self.until(lambda s: s["can_reclaim"])
        self.assertEqual(self.force().status_code, 200)
        held = self.until(lambda s: s["held"])
        self.assertIsNotNone(held["servo"]["token"])
        self.assertEqual(self.log.read().splitlines(), [b"force"])

    def move(self, status):
        return self.client.post(BASE + "/pose", json={"session_id": status["session_id"],
            "motion_token": status["motion_token"], "angles": [1, 2, 3, 4, 5, 6]},
            headers={"X-Robot-Target": f"{self.owner.cfg['ROBOT_IP']}:{self.owner.cfg['ROBOT_PORT']}"})

    def test_pose_requires_current_grant_and_ready_state(self):
        for state in ("SAFE_OFF AUTO REAL", "STANDBY MANUAL REAL", "STANDBY AUTO OTHER"):
            with self.subTest(state=state):
                # Actual worker heartbeat, then unsolicited callback from the controller.
                self.client.portal.call(self.owner.event, "ready", state)
                self.client.portal.call(self.owner.event, "callback", "GRANT")
                old = self.until(lambda s: s["held"])
                self.assertFalse(old["can_move"])
                self.assertEqual(self.move(old).status_code, 409)
        self.client.portal.call(self.owner.event, "callback", "LOSS")
        self.client.portal.call(self.owner.event, "ready", "STANDBY AUTO REAL")
        self.client.portal.call(self.owner.event, "callback", "GRANT")
        self.assertEqual(self.move(old).status_code, 409)
        self.assertEqual(self.log.read(), b"")

    def test_line_consumes_same_motion_token_as_pose(self):
        self.response_file.write_text("GRANT|STANDBY AUTO REAL")
        self.force()
        ready = self.until(lambda s: s["can_move"] and s["line_supported"])
        headers = {"X-Robot-Target": f"{self.owner.cfg['ROBOT_IP']}:{self.owner.cfg['ROBOT_PORT']}"}
        target = {"session_id": ready["session_id"], "motion_token": ready["motion_token"],
                  "pose": [300, -25, 150, 0, 90, 0]}
        self.assertEqual(self.client.post(BASE + "/line", json=target).status_code, 403)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: self.client.post(BASE + "/line", json=target, headers=headers), range(2)))
        self.assertEqual(sorted(r.status_code for r in replies), [200, 409])
        completed = next(r.json() for r in replies if r.is_success)
        self.assertEqual(completed["state"], "completed")
        token = completed["next_motion_token"]
        self.assertEqual(token, self.until(lambda s: s["can_move"])["motion_token"])
        self.assertNotEqual(token, target["motion_token"])
        self.assertEqual(self.move(ready).status_code, 409)
        self.client.portal.call(self.owner.event, "callback", "LOSS")
        self.client.portal.call(self.owner.event, "callback", "GRANT")
        self.assertEqual(self.client.post(BASE+"/line", json={**target,"motion_token":token}, headers=headers).status_code, 409)
        self.assertEqual(self.log.read(), b"force\nline 1 300 -25 150 0 90 0\n")

    def test_manual_gripper_and_arm_share_one_token_and_worker(self):
        self.response_file.write_text("GRANT|STANDBY AUTO REAL")
        self.force()
        ready = self.until(lambda s: s["can_move"] and s["gripper_supported"])
        headers = {"X-Robot-Target":f"{self.owner.cfg['ROBOT_IP']}:{self.owner.cfg['ROBOT_PORT']}"}
        body = {"action":"close","motion_token":ready["motion_token"],"session_id":ready["session_id"]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            hand = pool.submit(self.client.post, BASE+"/gripper", json=body, headers=headers)
            self.until(lambda s: not s["can_move"])
            self.assertEqual(self.move(ready).status_code,409)
            self.assertEqual(self.client.post(BASE+"/gripper",json=body,headers=headers).status_code,409)
            result = hand.result().json()
        self.assertEqual(result["state"],"completed")
        self.assertEqual(result["feedback"]["position"],349)
        self.assertEqual(result["feedback"]["current_ma"],14)
        self.assertEqual(self.until(lambda s:s["can_move"])["gripper_target"],700)
        self.assertEqual(self.log.read().splitlines(),[b"force",b"hand 1 700 1"])
        self.response_file.write_text("DROP")
        body.update(action="open",motion_token=result["next_motion_token"])
        self.assertEqual(self.client.post(BASE+"/gripper",json=body,headers=headers).json()["state"],"unknown")

    def test_two_confirmations_send_one_move_and_disconnect_is_unknown(self):
        self.response_file.write_text("GRANT|STANDBY AUTO REAL")
        self.force()
        ready = self.until(lambda s: s["can_move"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.move(ready), range(2)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        self.assertEqual(next(r for r in responses if r.status_code == 200).json()["state"], "completed")
        self.assertEqual(self.move(ready).status_code, 409)
        self.response_file.write_text("DROP")
        again = self.until(lambda s: s["can_move"])
        self.assertEqual(self.move(again).json()["state"], "unknown")
        fresh = self.until(lambda s: s["can_reclaim"] and s["session_id"] != ready["session_id"])
        self.assertFalse(fresh["can_move"])
        self.assertEqual(self.log.read().splitlines(), [b"force"] + [b"move 1 10 10 1 2 3 4 5 6"] * 2)

    def test_cross_origin_confirmation_is_rejected(self):
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "http://example.org"}):
            self.assertEqual(self.client.post(BASE + "/reclaim", json=self.ticket, headers=headers).status_code, 403)
        self.assertEqual(self.client.post(BASE + "/reclaim", json={}).status_code, 422)
        self.assertEqual(self.log.read(), b"")


class AccessRoutingTests(unittest.TestCase):
    def test_mutually_exclusive_backends(self):
        for mode in ("mac-sdk", "ros"):
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "ROBOT_ACCESS_BACKEND": mode, "ROBOT_STATE_DIR": directory, "ROBOT_ROS_PC_IP": ""
            }), TestClient(create_robot_app()) as client:
                if mode == "mac-sdk":
                    for method, url, body in [("post", "/control/reclaim", {}),
                                              ("put", "/control/direct-teach", {"enabled": True}),
                                              ("post", "/poses/1/execute", {"revision": None})]:
                        self.assertEqual(getattr(client, method)("/api/robot" + url, json=body).status_code, 409)
                else:
                    self.assertEqual(client.post(BASE + "/servo-on", json={
                        "session_id": "18a67ca2-093b-4aaa-a196-28d00cae1577", "servo_token": "a"*32}).status_code, 409)
                    self.assertEqual(client.get(BASE + "/status").status_code, 409)
                    self.assertEqual(client.post(BASE + "/reclaim", json={
                        "revision": 0, "session_id": "18a67ca2-093b-4aaa-a196-28d00cae1577"}).status_code, 409)
