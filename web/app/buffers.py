"""Bounded latest-only handoff. No queue grows with processing or client lag."""
from threading import Condition


class LatestSlot:
    def __init__(self):
        self._condition = Condition()
        self._value = None
        self._closed = False
        self.dropped = 0

    def put(self, value):
        with self._condition:
            if self._closed:
                return False
            if self._value is not None:
                self.dropped += 1
            self._value = value
            self._condition.notify()
            return True

    def take(self, timeout=0.25):
        with self._condition:
            self._condition.wait_for(lambda: self._value is not None or self._closed, timeout)
            value, self._value = self._value, None
            return value

    def close(self):
        with self._condition:
            self._closed = True
            self._value = None
            self._condition.notify_all()
