from types import SimpleNamespace
from unittest.mock import patch
import pytest
from app.capture import OpenCVCamera
from app.config import Settings
from app.detector import Detector


def test_missing_real_weights_never_fabricates_detections(tmp_path):
    detector = Detector(Settings(backend='cpu',model_path=tmp_path/'missing.pt'))
    with pytest.raises(FileNotFoundError): detector.load()
    assert detector.model is None


def test_camera_failure_requires_explicit_mock_backend():
    camera = SimpleNamespace(isOpened=lambda:False,release=lambda:None)
    with patch('cv2.VideoCapture',return_value=camera):
        with pytest.raises(RuntimeError,match='YOLO_BACKEND=mock'):
            OpenCVCamera(Settings(backend='cpu'))
        assert OpenCVCamera(Settings(backend='mock'))._synthetic is True
