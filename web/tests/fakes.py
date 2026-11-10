import time
from pathlib import Path

from app.config import Settings


def settings(**overrides):
    return Settings(backend="cpu", model_path=Path("fake.pt"), warmup=0, **overrides)


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for condition")


class Frame:
    shape = (720, 1280, 3)
    def __init__(self, number):
        self.number = number


class Camera:
    def __init__(self, settings):
        self.number = 0
        self.closed = False

    def read(self):
        time.sleep(0.003)
        self.number += 1
        return Frame(self.number)

    def close(self):
        self.closed = True


class Detector:
    def __init__(self, settings):
        self.seen = []
        self.closed = False
        self.metadata = {"backend": "fake"}

    def load(self):
        pass

    def warmup(self):
        pass

    def predict(self, frame):
        time.sleep(0.015)
        self.seen.append(frame.number)
        return []

    def close(self):
        self.closed = True


def encode(frame, objects, quality):
    return b"fake-jpeg-" + str(frame.number).encode()
