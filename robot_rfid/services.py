"""Shared reader and enqueue policy for the RFID API and dashboard buttons."""
import threading

try:
    from .database import ActiveTaskError, normalize_uid
    from .rfid_scanner import RFIDScanner
except ImportError:
    from database import ActiveTaskError, normalize_uid
    from rfid_scanner import RFIDScanner

_reader_lock = threading.Lock()


class ReaderBusy(RuntimeError):
    pass


class ReaderUnavailable(RuntimeError):
    pass


def scan_uid(uid=None, timeout_s=1.5):
    if uid is not None:
        return normalize_uid(uid), 'preset'
    if not _reader_lock.acquire(blocking=False):
        raise ReaderBusy('RFID reader is already scanning')
    try:
        try:
            return normalize_uid(RFIDScanner().scan(timeout=timeout_s)), 'hardware_pcsc'
        except Exception as exc:
            # A disconnected reader must not invent a product or enqueue work.
            raise ReaderUnavailable('RFID reader unavailable or scan timed out; use an explicit preset if intended') from exc
    finally:
        _reader_lock.release()


def enqueue_scan(db, uid, quantity, event_id):
    try:
        return db.enqueue(uid, quantity, event_id)
    except ActiveTaskError as exc:
        return exc.task, False
