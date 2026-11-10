import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.camera_views import SensorFrame, render_view
from app.main import create_app
from app.pipeline import Pipeline
from tests.fakes import Detector, settings, wait_until


def sensor_frame():
    color = np.full((48, 64, 3), (32, 64, 128), np.uint8)
    depth = np.full((48, 64), 1.25, np.float32)
    depth[:, :8] = 0
    ir = np.full((48, 64), 180, np.uint8)
    return SensorFrame(color, depth, ir, (60, 60, 32, 24))


def test_rgbd_uses_valid_metric_depth_and_retains_color_in_holes():
    frame = sensor_frame()
    boxes = [{"label": "part", "xyxy": [20, 15, 32, 27]}, {"label": "edge", "xyxy": [0, 0, 4, 4]}]
    mixed, annotations = render_view("rgbd", frame.color, frame, boxes)
    assert [a["label"] for a in annotations] == ["part 1.25 m", "edge"]
    assert np.array_equal(mixed[:, :8], frame.color[:, :8])
    assert SensorFrame(frame.color, None, frame.infrared, None).modes() == ["detections", "color", "infrared"]


def test_view_subscribers_share_color_inference_and_receive_distinct_jpegs(tmp_path):
    frame = sensor_frame()

    class Camera:
        def read(self):
            time.sleep(0.02)
            return frame

        def close(self):
            pass

    class ColorDetector(Detector):
        def predict(self, image):
            assert image is frame.color
            return [{"label": "part", "class_id": 0, "confidence": .95, "xyxy": [20, 15, 32, 27]}]

    def factory(config):
        return Pipeline(config, camera_factory=lambda _: Camera(), detector_factory=ColorDetector)

    with TestClient(create_app(settings(), factory, tmp_path)) as client:
        wait_until(lambda: client.get("/readyz").is_success)
        status = client.get("/api/status").json()
        assert set(status["camera"]["display_modes"]) == set(frame.modes())
        images = []
        for mode in frame.modes():
            with client.websocket_connect(f"/ws/detections?preview=true&display_mode={mode}") as ws:
                while True:
                    packet = ws.receive_json()
                    if packet["event"] == "detections":
                        break
                assert packet["display_mode"] == mode and packet["session_id"] == status["session_id"]
                assert packet["objects"][0]["label"] == "part"
                data = ws.receive_bytes()
                assert cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR).shape == frame.color.shape
                images.append(data)
        assert len(set(images)) == len(images)
