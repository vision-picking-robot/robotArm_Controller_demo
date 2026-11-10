import threading
import time
import unittest
from unittest.mock import patch

from app.buffers import LatestSlot
from app.config import Settings
from app.pipeline import Pipeline
from tests.fakes import Camera, Detector, encode, settings, wait_until


class BufferTests(unittest.TestCase):
    def test_overwrite_returns_newest_and_counts_discard(self):
        slot = LatestSlot()
        slot.put(1)
        slot.put(2)
        slot.put(3)
        self.assertEqual(slot.take(), 3)
        self.assertEqual(slot.dropped, 2)
        self.assertIsNone(slot.take(timeout=0))

    def test_close_wakes_waiter_and_rejects_input(self):
        slot = LatestSlot()
        output = []
        thread = threading.Thread(target=lambda: output.append(slot.take(timeout=10)))
        thread.start()
        slot.close()
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(output, [None])
        self.assertFalse(slot.put(1))


class ConfigTests(unittest.TestCase):
    def test_invalid_configuration(self):
        for options in ({"backend": "cuda"}, {"imgsz": 641}, {"confidence": 2},
                        {"preview_fps": float("nan")}, {"camera_index": -1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                Settings(**options)

    def test_coreml_environment_path(self):
        with patch.dict("os.environ", {"YOLO_BACKEND": "coreml", "YOLO_IMGSZ": "512"}, clear=True):
            config = Settings.from_env()
        self.assertEqual(config.model_path.name, "yolov8n.mlpackage")
        self.assertEqual(config.imgsz, 512)


class PipelineTests(unittest.TestCase):
    def make_pipeline(self, **kwargs):
        camera, detector = Camera(None), Detector(None)
        self.camera, self.detector = camera, detector
        config = kwargs.pop("config", settings())
        pipeline = Pipeline(config, camera_factory=kwargs.pop("camera_factory", lambda _: camera),
                            detector_factory=kwargs.pop("detector_factory", lambda _: detector),
                            encoder=encode, retry_delays=(0.01,), **kwargs)
        self.addCleanup(pipeline.stop)
        return pipeline

    def test_latest_only_and_resource_cleanup(self):
        pipeline = self.make_pipeline()
        pipeline.start()
        wait_until(lambda: pipeline.status()["counts"]["detected"] >= 5)
        pipeline.stop()
        self.assertTrue(self.camera.closed)
        self.assertTrue(self.detector.closed)
        self.assertGreater(pipeline.input_slot.dropped, 0)
        self.assertEqual(len(self.detector.seen), len(set(self.detector.seen)))
        self.assertGreater(max(b - a for a, b in zip(self.detector.seen, self.detector.seen[1:])), 1)
        self.assertEqual(pipeline.status()["state"], "stopped")
        self.assertTrue(all(not t.is_alive() for t in pipeline.threads))

    def test_no_encoding_without_viewers_and_shared_jpeg(self):
        pipeline = self.make_pipeline()
        pipeline.start()
        wait_until(lambda: pipeline.status()["ready"])
        self.assertEqual(pipeline.status()["counts"]["encoded"], 0)
        pipeline.subscribe_video(2)
        wait_until(lambda: pipeline.latest_jpeg() is not None)
        self.assertEqual(pipeline.status()["video_subscribers"], 2)
        first = pipeline.latest_jpeg()
        self.assertIs(pipeline.latest_jpeg(), first)
        pipeline.subscribe_video(-2)
        time.sleep(.1)
        count = pipeline.status()["counts"]["encoded"]
        time.sleep(.12)
        self.assertEqual(count, pipeline.status()["counts"]["encoded"])

    def test_stale_result_is_not_ready(self):
        pipeline = self.make_pipeline(config=settings(stale_seconds=0.02))
        with pipeline.lock:
            pipeline.model_ready = pipeline.camera_online = True
            pipeline.result_ns = time.monotonic_ns() - 100_000_000
            pipeline.result_generation = pipeline.generation
        self.assertEqual(pipeline.status()["state"], "stale")

    def test_reconnect_does_not_republish_old_generation(self):
        pipeline = self.make_pipeline()
        with pipeline.lock:
            pipeline.model_ready = pipeline.camera_online = True
            pipeline.result_ns = time.monotonic_ns()
            pipeline.result_generation = 1
            pipeline.generation = 2
        self.assertFalse(pipeline.status()["ready"])

    def test_camera_open_failure_recovers(self):
        attempts = []
        camera = Camera(None)
        def factory(_):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("camera temporarily unavailable")
            return camera
        pipeline = self.make_pipeline(camera_factory=factory)
        pipeline.start()
        wait_until(lambda: pipeline.status()["ready"])
        self.assertEqual(len(attempts), 2)
        self.assertIsNone(pipeline.status()["last_error"])

    def test_model_failure_remains_observable(self):
        class Broken(Detector):
            def load(self):
                raise RuntimeError("model missing")
        broken = Broken(None)
        pipeline = self.make_pipeline(detector_factory=lambda _: broken)
        pipeline.start()
        wait_until(lambda: pipeline.status()["state"] == "error")
        self.assertIn("model missing", pipeline.status()["last_error"])
        self.assertTrue(broken.closed)

    def test_result_contract_and_empty_detections(self):
        pipeline = self.make_pipeline()
        pipeline.start()
        wait_until(lambda: pipeline.status()["ready"])
        result = pipeline.latest_result()
        self.assertEqual(result["objects"], [])
        self.assertEqual(result["source_size"], {"width": 1280, "height": 720})
        self.assertEqual(result["session_id"], pipeline.session_id)
        self.assertGreaterEqual(result["timing_ms"]["capture_to_result"], result["timing_ms"]["predict"])


if __name__ == "__main__":
    unittest.main()
