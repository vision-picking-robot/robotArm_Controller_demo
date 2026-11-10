import unittest
from contextlib import redirect_stdout
import io
import sys
import types
from unittest.mock import Mock, patch

try:
    from .rfid_power import GET_SLEEP, configure_sleep, main
except ImportError:
    from rfid_power import GET_SLEEP, configure_sleep, main


def response(value):
    return [0xE1, 0, 0, 0, 1, value]


class ReaderPowerTests(unittest.TestCase):
    def test_no_sleep_queries_before_writing_and_verifies_afterwards(self):
        connection = Mock()
        connection.control.side_effect = [response(0), response(4), response(4)]
        self.assertEqual(configure_sleep(connection.control, 4), (0, 4))
        self.assertEqual([call.args for call in connection.control.call_args_list], [
            (GET_SLEEP,), ([0xE0, 0, 0, 0x48, 4],), (GET_SLEEP,)])

    def test_default_invocation_and_already_disabled_do_not_write(self):
        for target in (None, 4):
            with self.subTest(target=target):
                connection = Mock()
                connection.control.return_value = response(4)
                self.assertEqual(configure_sleep(connection.control, target), (4, 4))
                connection.control.assert_called_once_with(GET_SLEEP)

    def test_unknown_response_prevents_setting_write(self):
        for invalid in ([], [0x90, 0], response(99), response(0) + [0]):
            with self.subTest(invalid=invalid):
                connection = Mock()
                connection.control.return_value = invalid
                with self.assertRaises(RuntimeError):
                    configure_sleep(connection.control, 4)
                self.assertEqual(connection.control.call_count, 1)

    def test_failed_acknowledgement_or_readback_is_not_success(self):
        for replies in ([response(0), response(0)], [response(0), response(4), response(0)]):
            with self.subTest(replies=replies):
                connection = Mock()
                connection.control.side_effect = replies
                with self.assertRaises(RuntimeError):
                    configure_sleep(connection.control, 4)

    def test_restore_default_and_reject_unexpected_targets(self):
        connection = Mock()
        connection.control.side_effect = [response(4), response(0), response(0)]
        self.assertEqual(configure_sleep(connection.control, 0), (4, 0))
        connection = Mock()
        with self.assertRaises(ValueError):
            configure_sleep(connection.control, 9)
        connection.control.assert_not_called()

    def test_cli_preserves_zero_protocol_and_releases_native_handles(self):
        scard = types.SimpleNamespace(
            SCARD_SHARE_DIRECT=3, SCARD_PROTOCOL_UNDEFINED=0, SCARD_CTL_CODE=lambda value: value,
            SCARD_SCOPE_USER=0, SCARD_S_SUCCESS=0, SCARD_LEAVE_CARD=0,
            SCardEstablishContext=Mock(return_value=(0, 11)),
            SCardConnect=Mock(return_value=(0, 22, 0)),
            SCardControl=Mock(side_effect=[(0, response(0)), (0, response(4)), (0, response(4))]),
            SCardDisconnect=Mock(return_value=0), SCardReleaseContext=Mock(return_value=0),
            SCardGetErrorMessage=Mock(return_value='failure'))
        modules = {'smartcard': types.ModuleType('smartcard'),
                   'smartcard.System': types.SimpleNamespace(readers=lambda: ['ACS ACR1555 1S CL Reader PICC 0']),
                   'smartcard.scard': scard}
        with patch.dict(sys.modules, modules), redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--no-sleep']), 0)
        scard.SCardConnect.assert_called_once_with(11, 'ACS ACR1555 1S CL Reader PICC 0', 3, 0)
        scard.SCardDisconnect.assert_called_once_with(22, 0)
        scard.SCardReleaseContext.assert_called_once_with(11)


if __name__ == '__main__':
    unittest.main()
