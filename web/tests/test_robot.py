import asyncio
import itertools
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import create_app
from app.robot import RobotMonitor, RobotSettings
from app.robot_web import create_robot_app
from tests.fakes import settings
from tests.test_api import factory


class RobotTests(unittest.TestCase):
    def test_direct_teach_requires_explicit_boolean_and_confirmed_readback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "ROBOT_STATE_DIR": directory, "ROBOT_ROS_PC_IP": ""
        }), TestClient(create_robot_app()) as client:
            robot = client.app.state.robot
            url = "/api/robot/control/direct-teach"
            self.assertEqual(client.put(url, json={"enabled": True}).status_code, 503)
            robot.connected = robot.reclaim_available = True
            robot.socket, robot.teach_mode = object(), "auto"
            robot.call_service = AsyncMock()
            self.assertEqual(client.put(url, json={"enabled": "false"}).status_code, 422)
            self.assertEqual(client.put(url, json={"enabled": True}, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
            robot.call_service.assert_not_awaited()
            robot.call_service.side_effect = [{"success": True, "message": "OK"}, {"success": True, "message": "manual"}]
            self.assertEqual(client.put(url, json={"enabled": True}).json()["mode"], "manual")
            self.assertEqual(robot.call_service.await_args_list[0].args[1:], ("system/set_direct_teach", {"data": True}))
            robot.call_service.side_effect = [{"success": False, "message": "거절"}, {"success": True, "message": "unconfirmed"}]
            self.assertEqual(client.put(url, json={"enabled": False}).status_code, 409)
            self.assertEqual(client.get("/api/robot/status").json()["direct_teach"]["mode"], "unconfirmed")

    def test_websocket_target_for_existing_local_and_remote_configs(self):
        for network, configured, expected in [
            ("compose", "127.0.0.1", "rosbridge"), ("compose", "::1", "rosbridge"),
            ("compose", "192.168.1.2", "192.168.1.2"), ("compose", "", ""),
            ("host", "127.0.0.1", "127.0.0.1"), ("", "::1", "::1")
        ]:
            with self.subTest(network=network, configured=configured), patch.dict(os.environ, {
                "ROBOT_BRIDGE_NETWORK": network, "ROBOT_ROS_PC_IP": configured
            }):
                monitor = RobotMonitor(RobotSettings.from_env())
                self.assertEqual(monitor.bridge_host, expected)
                self.assertEqual(monitor.snapshot()["config"]["rosbridge_host"], expected)

    def test_reclaim_endpoint_requires_local_json_and_an_available_driver(self):
        with tempfile.TemporaryDirectory() as state_dir, patch.dict(os.environ, {
            "ROBOT_STATE_DIR": state_dir, "ROBOT_ROS_PC_IP": ""
        }), TestClient(create_robot_app()) as client:
            url = "/api/robot/control/reclaim"
            robot = client.app.state.robot
            robot.call_service = AsyncMock()
            self.assertEqual(client.post(url, json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
            self.assertEqual(client.post(url, json={}).status_code, 503)
            robot.call_service.assert_not_awaited()
            robot.connected = robot.reclaim_available = True
            robot.socket = object()
            robot.reclaim_pending = True
            self.assertEqual(client.post(url, json={}).status_code, 409)
            robot.call_service.assert_not_awaited()
            robot.reclaim_pending = False
            for success, message in [(False, "DENY"), (True, "GRANT")]:
                result = {"success": success, "message": message}
                robot.call_service.return_value = result
                self.assertEqual(client.post(url, json={}).json(), result)
            self.assertEqual(robot.call_service.await_count, 2)
            self.assertEqual(robot.call_service.await_args.args, (robot.socket, "system/reclaim_control", {}))
            robot.call_service.side_effect = TimeoutError()
            self.assertEqual(client.post(url, json={}).status_code, 503)
            self.assertFalse(robot.reclaim_pending)

    def test_compose_web_owns_persistent_poses_and_native_server_delegates(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "ROBOT_STATE_DIR": directory, "ROBOT_ROS_PC_IP": "", "YOLO_ROBOT_WEB_OWNER": "compose"
        }):
            with TestClient(create_robot_app()) as robot:
                self.assertEqual(robot.get("/healthz").json()["service"], "robot-web")
                self.assertEqual(robot.get("/robot").status_code, 200)
                self.assertEqual(robot.get("/static/robot-poses.js").status_code, 200)
                for asset in ("robot-arm.js", "robot-scenario.js", "robot.css"):
                    response = robot.get("/static/" + asset)
                    self.assertEqual(response.headers["cache-control"], "no-cache")
                    cached = robot.get("/static/" + asset, headers={"If-None-Match": response.headers["etag"]})
                    self.assertEqual(cached.status_code, 304)
                    self.assertEqual(cached.headers["cache-control"], "no-cache")
                pose = {"angles": [0, 0, 0, 0, 0, 0], "revision": None}
                saved = robot.put("/api/robot/poses/1", json=pose).json()["poses"][0]
                with TestClient(create_app(settings(), factory, directory), follow_redirects=False) as native:
                    self.assertIsNone(native.app.state.robot)
                    self.assertEqual(native.get("/robot").headers["location"], "http://127.0.0.1:8080/robot")
                    redirect = native.put("/api/robot/poses/1", json=pose)
                    self.assertEqual(redirect.status_code, 307)
                    self.assertTrue(redirect.headers["location"].endswith("/api/robot/poses/1"))
                with self.assertRaisesRegex(RuntimeError, "Robot state already in use"):
                    with TestClient(create_robot_app()):
                        pass
            with TestClient(create_robot_app()) as restarted:
                self.assertEqual(restarted.get("/api/robot/status").json()["pose_tests"]["poses"][0], saved)
                self.assertEqual(restarted.post("/api/robot/poses/1/execute",
                                                json={"revision": saved["revision"]}).status_code, 503)

    def test_configuration_and_real_routes_without_robot(self):
        with patch.dict(os.environ, {"ROBOT_ROS_PC_IP": "", "ROBOT_IP": "110.120.1.56"}), \
                tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(settings(), factory, directory)) as client:
                self.assertEqual(client.get('/robot').status_code, 200)
                self.assertIn('/static/robot.js', client.get('/robot').text)
                self.assertIn('id: "robot"', client.get('/static/station-nav.js').text)
                response = client.get('/api/robot/status')
                self.assertEqual(response.headers['cache-control'], 'no-store')
                data = response.json()
                self.assertEqual(data['state'], 'unconfigured')
                self.assertEqual(data['config']['model'], 'e0509')
                self.assertEqual(data['config']['ip'], '110.120.1.56')
                self.assertEqual(data['config']['ros_pc_ip'], '')
                self.assertEqual(data['config']['rosbridge_port'], 9090)
                self.assertIsNone(client.app.state.robot.task)
                self.assertTrue(all(topic['message'] is None for topic in data['topics']))
                self.assertNotIn('password', response.text.lower())
                self.assertEqual(client.post('/api/robot/status', json={}).status_code, 405)

    def test_explicit_environment_and_invalid_endpoints(self):
        with patch.dict(os.environ, {"ROBOT_IP": "192.168.1.100", "ROBOT_PORT": "12346",
                        "ROBOT_NAMESPACE": "/arm/e0509/", "ROBOT_ROS_PC_IP": "192.168.1.2",
                        "ROBOT_ROSBRIDGE_PORT": "9091"}):
            configured = RobotSettings.from_env()
            self.assertEqual(configured.ip, '192.168.1.100')
            self.assertEqual(configured.port, 12346)
            self.assertEqual(configured.namespace, 'arm/e0509')
            self.assertEqual(configured.ros_pc_ip, '192.168.1.2')
            self.assertEqual(configured.rosbridge_port, 9091)
        for host in ['192.168.300.1', 'ws://192.168.1.2:9090', '192.168.1.2:9090', 'fe80::1%en0@host']:
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, 'ROBOT_ROS_PC_IP'):
                RobotSettings(ros_pc_ip=host)
        for port in [0, 65536]:
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, 'ROBOT_ROSBRIDGE_PORT'):
                RobotSettings(rosbridge_port=port)
        with self.assertRaises(ValueError):
            RobotSettings(namespace='dsr01;echo')

    def test_joint_freshness_events_and_disconnect_are_distinct(self):
        monitor = RobotMonitor(RobotSettings(ros_pc_ip='127.0.0.1'))
        monitor.connected = True
        joints = monitor.topics[0]['name']
        message = {'name': ['joint_1'], 'position': [1.2]}
        with patch('app.robot.time.monotonic', return_value=10):
            monitor.receive(json.dumps({'op': 'publish', 'topic': joints, 'msg': message}))
        with patch('app.robot.time.monotonic', return_value=10.1):
            monitor.receive(json.dumps({'op': 'publish', 'topic': joints, 'msg': message}))
            data = monitor.snapshot()
            self.assertEqual(data['state'], 'live')
            self.assertAlmostEqual(data['topics'][0]['hz'], 10)
            self.assertEqual(data['topics'][1]['state'], 'listening')
        with patch('app.robot.time.monotonic', return_value=15):
            data = monitor.snapshot()
            self.assertEqual(data['state'], 'stale')
            self.assertIsNone(data['topics'][0]['hz'])
            self.assertEqual(data['topics'][0]['message'], message)
            monitor.receive(json.dumps({'op': 'publish', 'topic': monitor.topics[2]['name'], 'msg': {}}))
            self.assertEqual(monitor.snapshot()['state'], 'robot_disconnected')
            monitor.connected = False
            self.assertEqual(monitor.snapshot()['state'], 'offline')
            self.assertEqual(monitor.snapshot()['topics'][0]['message'], message)

    def test_malformed_messages_do_not_refresh_telemetry(self):
        monitor = RobotMonitor(RobotSettings())
        topic = monitor.topics[0]['name']
        for message in [{'name': ['joint_1'], 'position': [float('nan')]},
                        {'name': ['joint_1'], 'position': []},
                        {'name': ['joint_1'], 'position': ['1']}, {}]:
            monitor.receive(json.dumps({'op': 'publish', 'topic': topic, 'msg': message}))
        for packet in ['invalid', '[]', '{"topic": []}', '{"topic": "ignored", "op": "publish", "msg": {}}']:
            monitor.receive(packet)
        self.assertEqual(monitor.samples, {})


class RobotLifecycleTests(unittest.IsolatedAsyncioTestCase):
    @patch('app.robot.time.monotonic', side_effect=itertools.count(step=0.01))
    async def test_sdk_reconnect_requires_new_driver_joints_and_a_valid_state_response(self, _clock):
        monitor = RobotMonitor(RobotSettings(ros_pc_ip="127.0.0.1"))
        socket = monitor.socket = object()
        monitor.call_service = AsyncMock(return_value={"success": False})
        joints = {"name": [f"joint_{i}" for i in range(1, 7)], "position": [0] * 6}

        def publish(topic, message):
            monitor.receive(json.dumps({"op": "publish", "topic": monitor.topics[topic]["name"], "msg": message}))

        publish(0, joints)
        publish(2, {})
        publish(3, joints)
        await monitor._check_driver_reconnected(socket)
        monitor.call_service.assert_not_awaited()
        self.assertTrue(monitor.disconnected_event)
        publish(0, joints)
        await monitor._check_driver_reconnected(socket)
        self.assertTrue(monitor.disconnected_event)
        monitor.call_service.return_value = {"success": True, "robot_state": 1}
        await monitor._check_driver_reconnected(socket)
        self.assertFalse(monitor.disconnected_event)

    async def test_connection_subscribes_checks_publishers_and_clears_previous_session(self):
        sent = []
        connected = asyncio.Event()
        done = asyncio.Event()

        class Socket:
            async def send(self, message):
                sent.append(json.loads(message))
                if sent[-1]["op"] == "call_service":
                    probe = sent[-1]
                    values = {"publishers": []} if probe["service"] == "/rosapi/publishers" else (
                        {"type": "std_srvs/srv/Trigger"} if probe["service"] == "/rosapi/service_type"
                        else {"success": True, "message": "manual"})
                    monitor.receive(json.dumps({"op": "service_response", "id": probe["id"],
                        "service": probe["service"], "result": True, "values": values}))
                    if "success" in values:
                        connected.set()

            def __aiter__(self):
                return self

            async def __anext__(self):
                await done.wait()
                raise StopAsyncIteration

        @asynccontextmanager
        async def connection(endpoint, **kwargs):
            self.assertEqual(endpoint, 'ws://[::1]:9190')
            yield Socket()

        monitor = RobotMonitor(RobotSettings(ros_pc_ip='::1', rosbridge_port=9190))
        monitor.samples['old'] = {'message': {}}
        monitor.disconnected_event = True
        with patch('app.robot.connect', connection):
            monitor.start()
            await asyncio.wait_for(connected.wait(), 1)
            self.assertEqual([p['op'] for p in sent], ['subscribe'] * 4 + ['call_service'] * 3)
            self.assertEqual(sent[-3]['args'], {'topic': '/dsr01/joint_states'})
            self.assertEqual(sent[-2]['args'], {'service': '/dsr01/system/reclaim_control'})
            self.assertEqual(sent[-1]['service'], '/dsr01/system/direct_teach_status')
            self.assertEqual(monitor.snapshot()["direct_teach"]["mode"], "manual")
            self.assertTrue(monitor.snapshot()["reclaim_available"])
            self.assertEqual(monitor.snapshot()['state'], 'no_publisher')
            self.assertEqual(sent[0]['throttle_rate'], 100)
            self.assertEqual(monitor.samples, {})
            self.assertFalse(monitor.disconnected_event)
            self.assertTrue(monitor.connected)
            await monitor.stop()
            self.assertFalse(monitor.connected)
            self.assertIsNone(monitor.joint_publishers)
            self.assertFalse(monitor.snapshot()["reclaim_available"])
            self.assertIsNone(monitor.task)

    async def test_read_only_samples_restore_monitoring_and_never_authorize_motion(self):
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = RobotMonitor(RobotSettings(ros_pc_ip="127.0.0.1"), state_dir)
            monitor.connected = True
            monitor.socket = object()
            monitor.joint_publishers = []
            observer = "/dsr01/monitor/joint_states"
            driver = "/dsr01/joint_states"
            msg = {"name": [f"joint_{i}" for i in range(1, 7)], "position": [0., 0., 1.57, 0., 1.57, 0.]}
            def publish(topic):
                monitor.receive(json.dumps({"op":"publish", "topic":topic, "msg":msg}))
            self.assertEqual(monitor.snapshot()["state"], "no_publisher")
            publish(observer)
            self.assertEqual(monitor.snapshot()["state"], "monitoring")
            self.assertEqual(monitor.snapshot()["display_joint_topic"], observer)
            from app.store import DomainError
            with self.assertRaises(DomainError):
                monitor.pose_tests.require_live(monitor.socket)
            publish(driver)
            self.assertEqual(monitor.snapshot()["state"], "live")
            self.assertEqual(monitor.snapshot()["display_joint_topic"], driver)
            monitor.samples[driver]["time"] -= 10
            self.assertEqual(monitor.snapshot()["state"], "monitoring")
            monitor.samples[observer]["time"] -= 10
            self.assertNotIn(monitor.snapshot()["state"], {"monitoring", "live"})


if __name__ == '__main__':
    unittest.main()
