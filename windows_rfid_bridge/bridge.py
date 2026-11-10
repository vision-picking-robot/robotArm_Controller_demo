"""Windows RFID bridge: ACR1555U (Bluetooth PC/SC) -> robot PC web API.

Each tag tap is read once, matched against the robot PC's product DB (GET /api/v1/tags/{uid})
and queued as a pick task (POST /api/v1/scan-event). The robot PC runs
`scripts/project_cycle.py ... --remote-rfid`, which waits for these tasks.

A tag is sent again only after it has been removed from the reader (no double queuing while
the tag rests on the reader). Ctrl+C stops the bridge.

    python windows_rfid_bridge/bridge.py --api http://192.168.0.10:8000
    python windows_rfid_bridge/bridge.py --api http://192.168.0.10:8000 --check
"""
import argparse
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from robot_rfid.rfid_scanner import RFIDScanner  # noqa: E402


def say(msg):
    print(time.strftime('[%H:%M:%S] ') + msg, flush=True)


def list_readers():
    from smartcard.System import readers
    return [str(r) for r in readers()]


def check(api, reader):
    ok = True
    try:
        names = list_readers()
        say(f'PC/SC 리더 목록: {names or "없음"}')
        if reader:
            match = [n for n in names if reader.lower() in n.lower()]
        else:  # same default filter as RFIDScanner
            match = [n for n in names if ('acr1555' in n.lower() or 'acr1552' in n.lower()) and 'sam' not in n.lower()]
        if not match:
            say('X 리더기를 찾지 못했습니다. ACS Bluetooth PC/SC 드라이버 설치와 리더기 연결(파란 LED)을 확인하세요.')
            ok = False
        elif len(match) > 1:
            say(f'X 리더가 여러 개입니다. --reader 로 하나를 지정하세요: {match}')
            ok = False
        else:
            say(f'O 사용할 리더: {match[0]}')
    except Exception as error:  # noqa: BLE001
        say(f'X PC/SC 오류: {error}  (pyscard 설치/Smart Card 서비스 확인)')
        ok = False
    try:
        r = httpx.get(f'{api}/api/v1/current-task', timeout=5)
        r.raise_for_status()
        say(f'O 로봇 PC 웹 API 연결 성공: {api} (현재 작업: {r.json().get("task")})')
    except Exception as error:  # noqa: BLE001
        say(f'X 로봇 PC 웹 API 연결 실패: {api} -> {error}')
        say('  로봇 PC에서 start_lab.py --host 0.0.0.0 실행 여부, 같은 네트워크 여부, 방화벽(8000)을 확인하세요.')
        ok = False
    return ok


def submit(api, uid, quantity):
    info = None
    try:
        r = httpx.get(f'{api}/api/v1/tags/{uid}', timeout=5)
        if r.status_code == 200:
            info = r.json()
    except httpx.HTTPError:
        pass
    if info:
        say(f'  DB 매칭: {info.get("product_name")} ({info.get("yolo_class_name")}) -> {info.get("target_box_id")}')
    payload = dict(uid=uid, quantity=quantity, event_id=str(uuid4()))
    # Retry the same event_id after an uncertain outcome so the robot never gets a duplicate task.
    for attempt in range(5):
        try:
            r = httpx.post(f'{api}/api/v1/scan-event', json=payload, timeout=10)
            if r.is_error:
                say(f'X 등록 거부 {r.status_code}: {r.text[:200]}  (DB에 등록된 태그인지 확인)')
                return False
            task = r.json().get('task') or {}
            say(f'O 작업 등록: task={task.get("task_id", "?")[:8]} 상태={task.get("status")} -> 로봇이 집으러 갑니다')
            return True
        except httpx.TransportError as error:
            if attempt == 4:
                say(f'X 로봇 PC 전송 실패: {error}')
                return False
            time.sleep(min(2 ** attempt, 8))
    return False


def wait_removed(scanner, uid):
    while True:
        try:
            if scanner.scan(timeout=0.8) != uid:
                return
        except TimeoutError:
            return
        time.sleep(0.2)


def main(argv=None):
    parser = argparse.ArgumentParser(description='ACR1555U -> robot PC RFID bridge')
    parser.add_argument('--api', required=True, help='robot PC dashboard URL, e.g. http://192.168.0.10:8000')
    parser.add_argument('--reader', help='PC/SC reader name substring when several readers are listed')
    parser.add_argument('--quantity', type=int, default=1)
    parser.add_argument('--check', action='store_true', help='only check reader and robot PC connection')
    parser.add_argument('--read-only', action='store_true', help='print UIDs without sending them')
    args = parser.parse_args(argv)
    api = args.api.rstrip('/')
    if args.check:
        return 0 if check(api, args.reader) else 1
    if not args.read_only and not check(api, args.reader):
        say('점검 실패로 종료합니다. 위 X 항목을 해결한 뒤 다시 실행하세요.')
        return 1
    scanner = RFIDScanner(args.reader)
    say('태그를 리더기에 올려 주세요. (종료: Ctrl+C)')
    try:
        while True:
            try:
                uid = scanner.scan(timeout=3600)
            except TimeoutError:
                continue
            say(f'태그 UID: {uid}')
            if not args.read_only:
                submit(api, uid, args.quantity)
            wait_removed(scanner, uid)
            say('태그를 뗐습니다. 다음 태그를 올려 주세요.')
    except KeyboardInterrupt:
        say('종료합니다.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
