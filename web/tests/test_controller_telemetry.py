import math
import struct
import unittest
from unittest.mock import Mock

from rosbridge.read_joints import read_positions, read_task_pose


class ControllerTelemetryTests(unittest.TestCase):
    def test_base_task_pose_uses_task_registers_and_keeps_angles_in_degrees(self):
        stream = Mock()
        stream.recv.side_effect = [struct.pack(">HHHB", 9, 0, 15, 255),
                                   struct.pack(">BB6h", 3, 12, 2135, -907, 4500, -1555, 900, 23)]
        self.assertEqual(read_task_pose(stream, 9), [213.5, -90.7, 450, -155.5, 90, 2.3])
        stream.sendall.assert_called_once_with(struct.pack(">HHHBBHH", 9, 0, 6, 255, 3, 400, 6))

    def test_reads_only_the_six_position_registers_and_converts_signed_tenths(self):
        header = struct.pack(">HHHB", 42, 0, 15, 255)
        payload = struct.pack(">BB6h", 3, 12, 0, -900, 900, -1, 1550, 1234)
        stream = Mock()
        stream.recv.side_effect = [header[:2], header[2:], payload[:4], payload[4:]]
        positions = read_positions(stream, 42)
        self.assertEqual(positions, [math.radians(v) for v in (0, -90, 90, -0.1, 155, 123.4)])
        stream.sendall.assert_called_once_with(struct.pack(">HHHBBHH", 42, 0, 6, 255, 3, 270, 6))

    def test_stale_response_modbus_exception_or_truncation_cannot_publish_a_sample(self):
        for fragments in ([struct.pack(">HHHB", 41, 0, 15, 255)],
                          [struct.pack(">HHHB", 42, 0, 3, 255)],
                          [struct.pack(">HHHB", 42, 0, 15, 255), b""]):
            stream = Mock()
            stream.recv.side_effect = fragments
            with self.subTest(fragments=fragments), self.assertRaises((ConnectionError, ValueError)):
                read_positions(stream, 42)
