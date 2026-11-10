"""Capture synchronized RealSense streams in a separate process.

The macOS SDK can abort during USB discovery. Keep that failure isolated from
the model server so it can continue with the selected AVFoundation camera.
"""
import multiprocessing
from multiprocessing.connection import wait

from .camera_views import SensorFrame


def capture_streams(output, stop):
    pipeline = None
    try:
        import numpy as np
        import pyrealsense2 as rs

        context = rs.context()
        devices = context.query_devices()
        if len(devices) != 1:
            raise RuntimeError("SDK에서 입력 카메라를 하나로 식별할 수 없습니다.")
        serial = devices[0].get_info(rs.camera_info.serial_number)
        # At USB 2 speeds the D455f supports simultaneous VGA streams at 15 FPS.
        # Fall back to a pair if the device cannot stream the full combination.
        for want_depth, want_ir in ((True, True), (True, False), (False, True)):
            candidate = rs.pipeline(context)
            config = rs.config()
            config.enable_device(serial)
            config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 15)
            if want_depth:
                config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 15)
            if want_ir:
                config.enable_stream(rs.stream.infrared, 1, 640, 480, rs.format.y8, 15)
            try:
                profile = candidate.start(config)
                pipeline = candidate
                break
            except RuntimeError:
                if not want_depth:
                    raise
        if want_depth:
            align = rs.align(rs.stream.color)
            scale = profile.get_device().first_depth_sensor().get_depth_scale()
            intr = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
            intrinsics = (intr.fx, intr.fy, intr.ppx, intr.ppy)
        while not stop.is_set():
            frames = pipeline.wait_for_frames(2000)
            color = frames.get_color_frame()
            ir = frames.get_infrared_frame(1) if want_ir else None
            depth = align.process(frames).get_depth_frame() if want_depth else None
            if not color or (want_ir and not ir) or (want_depth and not depth):
                continue
            output.send(SensorFrame(
                np.asanyarray(color.get_data()),
                np.asanyarray(depth.get_data()).astype(np.float32) * scale if depth else None,
                np.asanyarray(ir.get_data()) if ir else None,
                intrinsics if depth else None))
    except Exception as exc:
        try:
            output.send(str(exc))
        except (OSError, EOFError):
            pass
    finally:
        if pipeline is not None:
            pipeline.stop()
        output.close()


class RealSenseCamera:
    def __init__(self, index):
        self.index = index
        ctx = multiprocessing.get_context("spawn")
        self.reader, writer = ctx.Pipe(duplex=False)
        self.stop = ctx.Event()
        self.worker = ctx.Process(target=capture_streams, args=(writer, self.stop), daemon=True)
        self.worker.start()
        writer.close()
        try:
            self.first = self.receive(timeout=3)
        except Exception:
            self.close()
            raise

    def receive(self, timeout):
        if self.reader not in wait([self.reader, self.worker.sentinel], timeout=timeout):
            raise RuntimeError("RealSense 센서의 영상 수신 시간이 초과되었습니다.")
        try:
            message = self.reader.recv()
        except EOFError as exc:
            raise RuntimeError("RealSense SDK의 장치 연결이 종료되었습니다.") from exc
        if isinstance(message, str):
            raise RuntimeError(message)
        return message

    def read(self):
        if self.first is not None:
            frame, self.first = self.first, None
            return frame
        return self.receive(timeout=2.5)

    def close(self):
        self.stop.set()
        self.reader.close()
        self.worker.join(timeout=0.4)
        if self.worker.is_alive():
            self.worker.terminate()
            self.worker.join(timeout=0.4)
        if self.worker.is_alive():
            self.worker.kill()
            self.worker.join()
        self.worker.close()
