import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.robot import RobotMonitor, RobotSettings
from app.robot_poses import PoseInput
from app.store import DomainError
from tests.fakes import settings
from tests.test_api import factory


class PoseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.monitor = RobotMonitor(RobotSettings(ros_pc_ip="127.0.0.1"), self.temp.name)
        self.tests = self.monitor.pose_tests
        self.angles = [1, 2, 3, 4, 5, 6]
        self.monitor.connected = True
        self.monitor.teach_mode = "auto"
        self.monitor.socket = SimpleNamespace(send=self.transmit)
        self.requests = []
        self.standby = True
        self.drop_motion = False
        self.joints()

    def joints(self):
        topic = self.monitor.topics[0]["name"]
        self.monitor.receive(json.dumps({"op":"publish", "topic":topic, "msg":{
            "name": [f"joint_{i}" for i in range(1, 7)], "position": [0.0]*6}}))

    async def transmit(self, raw):
        request = json.loads(raw)
        self.requests.append(request)
        if request["service"].endswith("get_robot_state"):
            values = {"success": True, "robot_state": 1 if self.standby else 2}
        else:
            if self.drop_motion:
                raise ConnectionError("lost after dispatch")
            values = {"success": True}
        self.monitor.receive(json.dumps({"op":"service_response", "service":request["service"],
            "result":True, "id":request["id"], "values":values}))

    def save(self, slot=1):
        pose = self.tests.save(slot, PoseInput(angles=self.angles, revision=None))
        return pose["poses"][slot-1]["revision"]

    async def test_saved_command_is_single_use_and_survives_restart(self):
        version = self.save()
        self.tests.execute(1, version)
        with self.assertRaises(DomainError):
            self.tests.execute(1, version)
        with self.assertRaises(DomainError):
            self.save(slot=2)
        await self.tests.task
        self.assertEqual(self.tests.snapshot()["last"]["state"], "completed")
        self.assertEqual([r["service"] for r in self.requests],
            ["/dsr01/system/get_robot_state", "/dsr01/motion/move_joint"])
        self.assertEqual(self.requests[1]["args"], dict(pos=self.angles, vel=10.0, acc=10.0,
            time=0.0, radius=0.0, mode=0, blend_type=0, sync_type=0))
        reloaded = RobotMonitor(self.monitor.settings, self.temp.name).pose_tests
        self.assertEqual(reloaded.data, self.tests.data)
        with self.assertRaises(DomainError):
            reloaded.execute(1, version)

    async def test_motion_is_not_sent_if_preflight_fails(self):
        self.standby = False
        self.tests.execute(1, self.save())
        await self.tests.task
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.tests.data["last"]["state"], "failed")

    def test_teaching_or_unknown_mode_blocks_pose_without_sending(self):
        revision = self.save()
        for mode in ("manual", "unconfirmed", "unavailable"):
            self.monitor.teach_mode = mode
            with self.assertRaisesRegex(DomainError, "자동 모드"):
                self.tests.execute(1, revision)
        self.assertEqual(self.requests, [])

    async def test_disconnect_during_motion_leaves_unknown_result_without_retry(self):
        self.drop_motion = True
        self.tests.execute(1, self.save())
        await self.tests.task
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.tests.data["last"]["state"], "unknown")
        self.assertFalse(self.monitor.pending)

    def test_unsaved_stale_and_out_of_range_positions(self):
        version = self.save()
        with self.assertRaises(DomainError):
            self.tests.execute(3, None)
        topic = self.monitor.topics[0]["name"]
        self.monitor.samples[topic]["time"] -= 30
        with self.assertRaises(DomainError):
            self.tests.execute(1, version)
        for bad in [[0]*5, [0, 0, 156, 0, 0, 0], [0, 0, 0, 0, 156, 0], [float("nan")]*6, [False]*6]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                PoseInput(revision=None, angles=bad)

    def test_older_saved_pose_outside_physical_j5_limit_requires_an_edit(self):
        version = self.save()
        self.tests.data["poses"][0]["angles"][4] = 200
        self.tests.write(self.tests.data)
        reloaded = RobotMonitor(self.monitor.settings, self.temp.name).pose_tests
        with self.assertRaisesRegex(DomainError, "관절 범위"):
            reloaded.execute(1, version)

    def test_api_save_works_offline_but_execution_does_not(self):
        with patch.dict("os.environ", {"ROBOT_ROS_PC_IP": ""}):
            with TestClient(create_app(settings(), factory, self.temp.name)) as client:
                status = client.get("/api/robot/status").json()
                self.assertEqual(len(status["pose_tests"]["poses"]), 3)
                body = {"angles":self.angles, "revision":None}
                self.assertEqual(client.put("/api/robot/poses/2",json=body,headers={"Sec-Fetch-Site":"cross-site"}).status_code,403)
                result = client.put("/api/robot/poses/2",json=body)
                self.assertEqual(result.status_code,200)
                version = result.json()["poses"][1]["revision"]
                self.assertEqual(client.post("/api/robot/poses/2/execute",json={"revision":version}).status_code,503)
                self.assertEqual(client.put("/api/robot/poses/2",json=body).status_code,409)
