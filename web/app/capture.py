"""The capture worker is the sole owner of VideoCapture."""
import logging
import os
import sys
from pathlib import Path


def camera_devices():
    """List Linux V4L devices without opening them; expose configured index elsewhere."""
    devices = []
    if sys.platform.startswith('linux'):
        for path in sorted(Path('/sys/class/video4linux').glob('video*')):
            index = int(path.name.removeprefix('video'))
            name = (path / 'name').read_text().strip() if (path / 'name').exists() else path.name
            devices.append(dict(id=str(Path('/dev') / path.name), index=index, name=name))
    if not devices:
        index = int(os.getenv('YOLO_CAMERA_INDEX', '0'))
        devices = [dict(id=f'cam-{index}', index=index, name=f'Camera {index}')]
    return devices


def camera_device(device_id):
    for device in camera_devices():
        if device["id"] == device_id:
            return device
    raise LookupError("선택한 카메라가 연결되어 있지 않습니다. 카메라 목록을 새로고침하세요.")


def open_camera(settings):
    # Preserve the configured index when device discovery is unavailable.
    try:
        devices = camera_devices()
    except RuntimeError:
        devices = []
    key = "id" if settings.camera_id else "index"
    value = settings.camera_id or settings.camera_index
    selected = next((d for d in devices if d[key] == value), None)
    real_devices = [d for d in devices if "realsense" in d["name"].lower()]
    note = None
    if selected and selected in real_devices:
        from .realsense import RealSenseCamera
        if len(real_devices) == 1:
            try:
                camera = RealSenseCamera(selected["index"])
                camera.device_id = selected["id"]
                return camera
            except (OSError, RuntimeError) as exc:
                logging.getLogger(__name__).warning("RealSense sensor capture unavailable: %s", exc)
                if "No module named 'pyrealsense2'" in str(exc):
                    note = "깊이·적외선 표시에는 RealSense SDK가 필요합니다."
                else:
                    note = "깊이·적외선 스트림을 열 수 없습니다. USB 연결과 접근 권한을 확인하세요."
        else:
            note = "RealSense 장치가 여러 개여서 깊이 센서를 식별할 수 없습니다."
    camera = OpenCVCamera(settings)
    camera.device_id = selected["id"] if selected else settings.camera_id
    camera.mode_note = note
    return camera


class OpenCVCamera:
    def __init__(self, settings):
        import cv2
        self._cv2 = cv2
        # Resolve again on reconnect: plugging in another device can shift indices.
        self.index = camera_device(settings.camera_id)["index"] if settings.camera_id else settings.camera_index
        backend = cv2.CAP_AVFOUNDATION if sys.platform == 'darwin' else (cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_V4L2)
        self._camera = cv2.VideoCapture(self.index, backend)
        self.settings = settings
        self._synthetic = not self._camera.isOpened()
        if self._synthetic:
            self._camera.release()
            if settings.backend != 'mock':
                raise RuntimeError('카메라를 열 수 없습니다. 합성 영상은 YOLO_BACKEND=mock에서만 사용합니다.')
            self._frame_count = 0
        else:
            self._camera.set(cv2.CAP_PROP_FRAME_WIDTH, settings.width)
            self._camera.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.height)
            self._camera.set(cv2.CAP_PROP_FPS, settings.camera_fps)

    def read(self):
        if self._synthetic:
            import numpy as np
            import time
            self._frame_count += 1
            frame = np.full((self.settings.height, self.settings.width, 3), (240, 242, 245), dtype=np.uint8)
            self._cv2.rectangle(frame, (100, 60), (1180, 660), (200, 200, 200), 2)
            self._cv2.rectangle(frame, (150, 120), (350, 400), (180, 220, 180), -1)
            self._cv2.putText(frame, "Snack Box (120mm)", (160, 260), self._cv2.FONT_HERSHEY_SIMPLEX, 0.6, (40, 90, 40), 2)
            self._cv2.rectangle(frame, (450, 150), (600, 480), (220, 200, 180), -1)
            self._cv2.putText(frame, "Pet Bottle (150mm)", (460, 310), self._cv2.FONT_HERSHEY_SIMPLEX, 0.6, (90, 60, 40), 2)
            self._cv2.rectangle(frame, (700, 200), (880, 380), (180, 200, 230), -1)
            self._cv2.putText(frame, "Can (30mm)", (720, 290), self._cv2.FONT_HERSHEY_SIMPLEX, 0.6, (40, 50, 110), 2)
            self._cv2.putText(frame, f"Vision Lab [Mock Video Stream] Frame #{self._frame_count}", (30, 40),
                              self._cv2.FONT_HERSHEY_SIMPLEX, 0.8, (50, 50, 50), 2)
            time.sleep(1.0 / max(1.0, self.settings.camera_fps))
            return frame
        ok, frame = self._camera.read()
        if not ok or frame is None:
            raise RuntimeError("카메라 프레임 수신이 중단되었습니다.")
        return frame

    def close(self):
        self._camera.release()
