import argparse
import logging
import threading
import time
from uuid import uuid4

import httpx

try:
    from .database import normalize_uid
except ImportError:
    from database import normalize_uid

GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]
log = logging.getLogger(__name__)


class RFIDScanner:
    def __init__(self, reader_filter=None):
        self.reader_filter = reader_filter

    def scan(self, timeout=30, stop=None):
        from smartcard.System import readers
        from smartcard.Exceptions import CardConnectionException, NoCardException

        stop = stop or threading.Event()
        deadline = time.monotonic() + timeout
        delay = 0.25
        while time.monotonic() < deadline and not stop.is_set():
            connection = None
            try:
                available = readers()
                matches = [r for r in available if (
                    self.reader_filter.lower() in str(r).lower() if self.reader_filter else
                    any(name in str(r).lower() for name in ('acr1552', 'acr1555'))
                    and 'sam' not in str(r).lower())]
                if len(matches) > 1:
                    raise RuntimeError('Multiple matching readers; specify --reader')
                if not matches:
                    raise ConnectionError('No matching PC/SC reader')
                connection = matches[0].createConnection()
                connection.connect()
                data, sw1, sw2 = connection.transmit(GET_UID)
                if (sw1, sw2) != (0x90, 0x00):
                    raise RuntimeError(f'Get UID rejected: {sw1:02X}{sw2:02X}')
                return normalize_uid(''.join(f'{byte:02X}' for byte in data))
            except NoCardException:
                delay = 0.25
            except (CardConnectionException, ConnectionError) as exc:
                log.warning('RFID reconnect: %s', exc)
                delay = min(delay * 2, 8)
            except Exception as exc:
                # PC/SC service and reader enumeration can fail before a connection exists.
                if isinstance(exc, (ValueError, RuntimeError)):
                    raise
                log.warning('PC/SC unavailable: %s', exc)
                delay = min(delay * 2, 8)
            finally:
                if connection is not None:
                    try:
                        connection.disconnect()
                    except Exception:
                        log.debug('Reader disconnected during cleanup', exc_info=True)
            stop.wait(max(0, min(delay, deadline - time.monotonic())))
        raise TimeoutError('RFID scan cancelled or timed out')


def main():
    parser = argparse.ArgumentParser(description='Scan one triggered box and submit a task')
    parser.add_argument('--reader', help='PC/SC reader name substring, including virtual BLE reader')
    parser.add_argument('--api', default='http://127.0.0.1:8000')
    parser.add_argument('--quantity', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--read-only', action='store_true', help='Print UID without submitting a task')
    args = parser.parse_args()
    if args.quantity < 1 or args.timeout <= 0:
        parser.error('quantity and timeout must be positive')
    logging.basicConfig(level=logging.INFO)
    uid = RFIDScanner(args.reader).scan(args.timeout)
    print(f'UID: {uid}', flush=True)
    if args.read_only:
        return
    payload = {'uid': uid, 'quantity': args.quantity, 'event_id': str(uuid4())}
    # Retry the same event ID after an uncertain HTTP outcome to avoid duplicate work.
    for attempt in range(5):
        try:
            response = httpx.post(f'{args.api}/api/v1/scan-event', json=payload, timeout=10)
            if response.is_error:
                parser.exit(1, f'API {response.status_code}: {response.text}\nUID {uid}: check product registration before retrying.\n')
            response.raise_for_status()
            print(response.json())
            return
        except httpx.TransportError:
            if attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 8))


if __name__ == '__main__':
    main()
