"""Query or change ACR1555 reader sleep settings through PC/SC SCardControl."""
import argparse

GET_SLEEP = [0xE0, 0x00, 0x00, 0x50, 0x00]
SLEEP_LABELS = {0: '60초', 1: '90초', 2: '120초', 3: '180초', 4: '절전 안 함',
                5: '10초', 6: '30초'}


def sleep_value(response):
    if len(response) != 6 or list(response[:5]) != [0xE1, 0, 0, 0, 1] or response[5] not in SLEEP_LABELS:
        raise RuntimeError(f'지원되는 절전 설정 응답이 아닙니다: {response}')
    return response[5]


def configure_sleep(control, target=None):
    if target not in (None, 0, 4):
        raise ValueError('조회, 기본 60초, 절전 해제만 지원합니다')
    before = sleep_value(control(GET_SLEEP))
    if target is None or before == target:
        return before, before
    # ACS reference manual: Set Sleep Mode has a five-byte command, without Lc.
    command = [0xE0, 0, 0, 0x48, target]
    if sleep_value(control(command)) != target:
        raise RuntimeError('설정 명령의 응답이 요청값과 다릅니다. 현재 설정을 다시 조회하세요')
    after = sleep_value(control(GET_SLEEP))
    if after != target:
        raise RuntimeError('절전 설정 재조회가 일치하지 않습니다. 적용 완료로 판단하지 않습니다')
    return before, after


def main(argv=None):
    parser = argparse.ArgumentParser(description='ACR1555 절전 설정 조회·해제 (태그·작업 DB 변경 없음)')
    parser.add_argument('--reader', help='PC/SC PICC 리더 이름 일부')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--no-sleep', action='store_true', help='절전 해제')
    action.add_argument('--restore-default', action='store_true', help='기본 60초 절전으로 복원')
    args = parser.parse_args(argv)
    context = card = None
    try:
        from smartcard.System import readers
        from smartcard.scard import (SCARD_SHARE_DIRECT, SCARD_PROTOCOL_UNDEFINED, SCARD_CTL_CODE,
                                     SCARD_SCOPE_USER, SCARD_S_SUCCESS, SCARD_LEAVE_CARD,
                                     SCardEstablishContext, SCardConnect, SCardControl,
                                     SCardDisconnect, SCardReleaseContext, SCardGetErrorMessage)
        matches = [r for r in readers() if 'acr1555' in str(r).lower() and 'picc' in str(r).lower()
                   and (not args.reader or args.reader.lower() in str(r).lower())]
        if len(matches) != 1:
            raise RuntimeError(f'리더기를 하나만 선택하세요 (--reader). 검색 결과: {list(map(str, matches))}')
        print(f'리더기: {matches[0]}', flush=True)

        def checked(result):
            if result != SCARD_S_SUCCESS:
                raise RuntimeError(f'{SCardGetErrorMessage(result)} (0x{result & 0xffffffff:08X})')

        result, context = SCardEstablishContext(SCARD_SCOPE_USER)
        checked(result)
        # PCSCCardConnection.connect replaces protocol=0 with its default mask.
        # Native SCardConnect preserves 0 for a card-independent direct connection.
        result, card, _ = SCardConnect(context, str(matches[0]), SCARD_SHARE_DIRECT, SCARD_PROTOCOL_UNDEFINED)
        checked(result)

        def control(command):
            result, response = SCardControl(card, SCARD_CTL_CODE(3500), command)
            checked(result)
            return response

        target = 4 if args.no_sleep else 0 if args.restore_default else None
        before, after = configure_sleep(control, target)
        print(f'기존 설정: {SLEEP_LABELS[before]} (0x{before:02X})')
        print(f'현재 설정: {SLEEP_LABELS[after]} (0x{after:02X})')
        if target is not None:
            print('설정값 재조회 확인 완료. 전원 재시작 후 유지 여부는 별도로 확인하세요.')
        return 0
    except Exception as exc:
        print(f'절전 설정 확인 실패: {exc}')
        print('리더기 전원과 USB/BLE 연결을 확인한 뒤 다시 실행하세요. 적용 완료로 판단하지 마세요.')
        return 1
    finally:
        if card:
            SCardDisconnect(card, SCARD_LEAVE_CARD)
        if context:
            SCardReleaseContext(context)


if __name__ == '__main__':
    raise SystemExit(main())
