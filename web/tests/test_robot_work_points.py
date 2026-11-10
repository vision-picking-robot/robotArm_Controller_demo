import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.robot import RobotMonitor, RobotSettings
from app.robot_web import create_robot_app


class WorkPointTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.enterContext(patch.dict(os.environ, {"ROBOT_STATE_DIR": self.root, "ROBOT_ROS_PC_IP": ""}))
        self.app = create_robot_app()
        self.client = self.enterContext(TestClient(self.app))
        self.robot = self.app.state.robot
        self.url = "/api/robot/work-points/"
        self.pose = [200, -150.5, 189.7, -179, 90, 0]

    def test_independent_persistent_base_poses_without_a_connection(self):
        self.robot.call_service = AsyncMock()
        points = self.client.get("/api/robot/status").json()["work_points"]
        self.assertEqual({k: p["pose"] for k, p in points.items()}, {"waypoint": None, "box": None, "shipping_label": None})
        saved = {}
        for name, pose in [("waypoint", [50,10,100,0,90,5]), ("box", self.pose), ("shipping_label", [0]*6)]:
            response = self.client.put(self.url + name, json={"pose": pose, "revision": None})
            self.assertEqual(response.status_code, 200, response.text)
            saved[name] = response.json()
        # Stale edits don't clobber another tab; updating the label leaves the box intact.
        body = {"pose": [1]*6, "revision": None}
        self.assertEqual(self.client.put(self.url + "shipping_label", json=body).status_code, 409)
        body["revision"] = saved["shipping_label"]["revision"]
        saved["shipping_label"] = self.client.put(self.url + "shipping_label", json=body).json()
        self.assertEqual(saved["shipping_label"]["pose"], [1]*6)
        self.assertEqual(RobotMonitor(self.robot.settings, self.root).work_points.points, saved)
        self.assertIsNone(RobotMonitor(RobotSettings(namespace="other"), self.root).work_points.points["box"]["pose"])
        self.assertFalse(self.robot.pose_tests.path.exists())
        self.robot.call_service.assert_not_awaited()

    def test_legacy_file_adds_an_unconfigured_waypoint_without_changing_other_revisions(self):
        existing = {name: {"pose": self.pose, "revision": name, "updated_at": "2026-01-01"}
                    for name in ("box", "shipping_label")}
        self.robot.work_points.path.write_text(json.dumps(existing))
        points = RobotMonitor(self.robot.settings, self.root).work_points
        self.assertIsNone(points.points["waypoint"]["pose"])
        from app.robot_work_points import WorkPointInput
        points.save("waypoint", WorkPointInput(pose=self.pose, revision=None))
        for name, point in existing.items():
            self.assertEqual(points.points[name], point)

    def test_floor_is_shared_without_camera_and_rejects_stale_writes(self):
        url = "/api/robot/floor-reference"
        self.robot.call_service = AsyncMock()
        with patch("app.robot_pick.read_camera") as camera, patch("app.robot_pick.request_host") as arm, \
                patch("app.robot_gripper.request_host") as hand:
            self.assertEqual(self.client.get(url).json(), {"revision": None, "z_mm": 0})
            self.assertFalse(self.robot.floor.path.exists())
            zero = self.client.put(url, json={"z_mm": 0, "revision": None}).json()
            self.assertEqual(zero["z_mm"], 0)
            self.assertTrue(zero["revision"])
            stale = self.client.put(url, json={"z_mm": 10, "revision": None})
            self.assertEqual(stale.status_code, 409)
            negative = self.client.put(url, json={**zero, "z_mm": -120}).json()
            self.assertEqual(negative["z_mm"], -120)
            self.assertNotEqual(negative["revision"], zero["revision"])
            self.assertEqual(RobotMonitor(self.robot.settings, self.root).floor.value, negative)
            self.assertEqual(RobotMonitor(RobotSettings(namespace="other"), self.root).floor.value["z_mm"], 0)
            self.assertEqual(self.client.put(url, json={**negative, "z_mm": None}).status_code, 422)
            self.assertEqual(self.client.put(url, json=negative, headers={"Sec-Fetch-Site":"cross-site"}).status_code, 403)
            self.assertEqual(self.client.get(url).json(), negative)
            self.assertEqual(self.robot.picks.approvals, {})
            camera.assert_not_called()
            arm.assert_not_called()
            hand.assert_not_called()
            self.robot.call_service.assert_not_awaited()

    def test_legacy_unset_floor_uses_zero_without_rewriting_the_file(self):
        old = {"revision": None, "z_mm": None}
        path = self.robot.floor.path
        path.write_text(json.dumps(old))
        self.assertEqual(RobotMonitor(self.robot.settings, self.root).floor.value, {"revision": None, "z_mm": 0})
        self.assertEqual(json.loads(path.read_text()), old)

    def test_parcel_box_geometry_is_persistent_and_independent_of_tcp_and_motion(self):
        url = "/api/robot/parcel-box"
        self.robot.call_service = AsyncMock()
        tcp = self.client.put(self.url + "box", json={"pose": self.pose, "revision": None}).json()
        with patch("app.robot_pick.read_camera") as camera, patch("app.robot_pick.request_host") as arm, \
                patch("app.robot_gripper.request_host") as hand:
            initial = self.client.get(url)
            self.assertEqual(initial.headers["Cache-Control"], "no-store")
            self.assertEqual(initial.json(), dict(size_mm=[400,300,250], center_base_mm=[500,0,0], revision=None, updated_at=None))
            self.assertFalse(self.robot.parcel_box.path.exists())
            body = dict(size_mm=[400,300,250], center_base_mm=[500,0,-120], revision=None)
            response = self.client.put(url, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            saved = response.json()
            self.assertEqual(saved["center_base_mm"], [500,0,-120])
            self.assertTrue(saved["revision"])
            self.assertEqual(self.client.get("/api/robot/status").json()["parcel_box"], saved)
            self.assertEqual(RobotMonitor(self.robot.settings, self.root).parcel_box.value, saved)
            self.assertEqual(RobotMonitor(RobotSettings(namespace="other"), self.root).parcel_box.value["size_mm"], [400,300,250])
            self.assertEqual(self.client.put(url, json=body).status_code, 409)
            changed = self.client.put(url, json={**body,"revision":saved["revision"],"size_mm":[450,350,300]}).json()
            self.assertNotEqual(changed["revision"], saved["revision"])
            self.assertEqual(self.robot.work_points.points["box"], tcp)
            self.assertEqual(self.robot.picks.approvals, {})
            camera.assert_not_called()
            arm.assert_not_called()
            hand.assert_not_called()
            self.robot.call_service.assert_not_awaited()

    def test_parcel_box_defaults_can_be_saved_and_legacy_nulls_use_defaults(self):
        url = "/api/robot/parcel-box"
        old = dict(size_mm=None, center_base_mm=None, revision=None, updated_at=None)
        self.robot.parcel_box.path.parent.mkdir(parents=True, exist_ok=True)
        self.robot.parcel_box.path.write_text(json.dumps(old))
        restored = RobotMonitor(self.robot.settings, self.root).parcel_box.value
        self.assertEqual(restored["size_mm"], [400,300,250])
        self.assertEqual(restored["center_base_mm"], [500,0,0])
        self.assertEqual(json.loads(self.robot.parcel_box.path.read_text()), old)
        response = self.client.put(url,json={k:restored[k] for k in ("size_mm","center_base_mm","revision")})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()["revision"])
        self.assertEqual(RobotMonitor(self.robot.settings, self.root).parcel_box.value,response.json())

    def test_parcel_box_rejects_invalid_dimensions_and_cross_site_writes(self):
        url = "/api/robot/parcel-box"
        body = dict(size_mm=[400,300,250], center_base_mm=[0,0,0], revision=None)
        for field, values in [("size_mm",[0,300,250]),("size_mm",[-1,300,250]),
                              ("size_mm",[400,300]),("size_mm",[True,300,250]),
                              ("size_mm",["400",300,250]),("size_mm",[1e7,300,250]),
                              ("center_base_mm",[0,None,0]),("center_base_mm",[False,0,0]),
                              ("center_base_mm",[0,0,1e7])]:
            self.assertEqual(self.client.put(url,json={**body,field:values}).status_code,422)
        self.assertEqual(self.client.put(url,json={**body,"pose":[0]*6}).status_code,422)
        self.assertEqual(self.client.put(url,json=body,headers={"Sec-Fetch-Site":"cross-site"}).status_code,403)
        self.assertFalse(self.robot.parcel_box.path.exists())

    def test_rejects_invalid_input_and_cross_site_writes(self):
        for pose in ([0]*3, [0]*7, [0, 0, 0, 0, 0, False], ["1"]*6):
            response = self.client.put(self.url + "box", json={"pose": pose, "revision": None})
            self.assertEqual(response.status_code, 422)
        body = {"pose": self.pose, "revision": None}
        self.assertEqual(self.client.put(self.url + "unknown", json=body).status_code, 422)
        self.assertEqual(self.client.put(self.url + "box", json=body, headers={"Sec-Fetch-Site":"cross-site"}).status_code, 403)
        self.assertFalse(self.robot.work_points.path.exists())

    def test_capture_requires_fresh_monitor_and_valid_task_pose_and_never_saves(self):
        self.robot.call_service = AsyncMock(return_value={"success": True, "pos": self.pose})
        url = self.url + "current"
        self.assertEqual(self.client.get(url).status_code, 503)
        self.robot.connected, self.robot.socket = True, object()
        topic = self.robot.topics[3]["name"]
        self.robot.receive(json.dumps({"op":"publish", "topic":topic, "msg":{"name":["joint_1"], "position":[1]}}))
        response = self.client.get(url)
        self.assertEqual(response.json(), {"pose": self.pose})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.robot.call_service.assert_awaited_once_with(self.robot.socket, "monitor/get_current_pose", {"space_type":1}, timeout=4)
        for values in ({"success": False, "pos": [0]*6}, {"success": True, "pos": [0]*5},
                       {"success": True, "pos": [float("nan")]*6}):
            self.robot.call_service.return_value = values
            self.assertEqual(self.client.get(url).status_code, 503)
        self.robot.call_service.side_effect = TimeoutError()
        self.assertEqual(self.client.get(url).status_code, 503)
        self.robot.samples[topic]["time"] -= 30
        self.robot.call_service.reset_mock()
        self.assertEqual(self.client.get(url).status_code, 503)
        self.robot.call_service.assert_not_awaited()
        self.assertFalse(self.robot.work_points.path.exists())
