# Windows RFID 브리지 (ACR1555U 블루투스 → 로봇 PC)

리눅스에는 ACR1555U **블루투스** PC/SC 드라이버가 없다(ACS는 리눅스 USB 모드만 제공).
그래서 리더기는 **Windows PC**에 블루투스로 연결하고, 읽은 UID만 로봇 PC(Ubuntu)로 보낸다.
상품·박스 매칭(DB)과 로봇 제어는 모두 로봇 PC가 한다.

```
[Windows PC] ACR1555U --BLE--> bridge.py --HTTP POST /api/v1/scan-event {uid}-->
[로봇 PC]   웹(8000): UID → DB(상품/박스) → 작업 큐  →  project_cycle.py --remote-rfid: 집기 → 박스 → 완료 보고
```

태그를 올리면 한 번만 전송한다. 태그를 올려둔 채로는 다시 보내지 않으며, 뗐다가 다시 올리면 새 작업이 된다.

## 0. 준비물

- Windows 10/11 PC, 로봇 PC와 **같은 네트워크**(같은 Wi-Fi 또는 같은 공유기)
- Python 3.10 이상 — https://www.python.org/downloads/ 설치 시 **"Add python.exe to PATH" 체크**
- Git — https://git-scm.com/download/win
- ACR1555U 리더기 (충전된 상태)

## 1. ACS 블루투스 드라이버 설치 (최초 1회)

1. https://www.acs.com.hk/en/driver/622/acr1555u-secure-bluetooth%C2%AE-nfc-reader/ 에서
   **Windows용 Bluetooth PC/SC Driver Installer**를 받아 설치하고 재부팅한다.
2. 리더기 전원을 켜고, ACS 드라이버/유틸리티 안내에 따라 PC와 블루투스로 연결한다
   (Windows 블루투스 설정에서 일반 페어링만으로는 PC/SC 리더로 나타나지 않을 수 있다).
3. 연결되면 PC/SC 리더 이름에 `ACR1555` 가 포함되어 나타난다(아래 2단계 점검에서 확인).

## 2. 클론과 설치 (최초 1회)

명령 프롬프트(cmd)에서:

```bat
cd %USERPROFILE%\Desktop
git clone https://github.com/vision-picking-robot/robotArm_Controller_demo.git
cd robotArm_Controller_demo
windows_rfid_bridge\1_setup.bat
```

`.venv-win` 가상환경에 `pyscard`, `httpx`만 설치한다(로봇 제어용 패키지는 설치하지 않는다).
이미 클론했다면 `git pull` 후 같은 배치 파일을 다시 실행한다.

## 3. 로봇 PC 쪽 준비 (로봇 PC에서)

로봇 PC 담당자가 다음을 실행해 둔다.

```bash
# 대시보드를 같은 네트워크에 공개 (인증 없음: 신뢰할 수 있는 실습실 망에서만)
python scripts/start_lab.py --host 0.0.0.0
# 로봇이 원격 태그를 기다리게 함 (Ctrl+C로 종료)
python scripts/project_cycle.py --mode real --confirm-real --cycles 0 --api http://127.0.0.1:8000 --remote-rfid
```

로봇 PC의 IP는 로봇 PC에서 `ip -br addr`로 확인한다(Wi-Fi 인터페이스의 주소, 예: `192.168.204.52`).

## 4. 점검

```bat
windows_rfid_bridge\2_check.bat
```

처음 실행하면 로봇 PC 주소를 묻는다. 예: `http://192.168.204.52:8000`
(저장 위치 `windows_rfid_bridge\api_url.txt`, 바꾸려면 파일을 지우고 다시 실행)

| 결과 | 의미 / 조치 |
|---|---|
| `O 사용할 리더: ... ACR1555 ...` | 리더 정상 |
| `X 리더기를 찾지 못했습니다` | ACS 블루투스 드라이버 설치·연결 확인, 리더기 전원·충전 확인 |
| `X 리더가 여러 개입니다` | `2_check.bat --reader "이름 일부"` 처럼 하나 지정 |
| `O 로봇 PC 웹 API 연결 성공` | 네트워크 정상 |
| `X 로봇 PC 웹 API 연결 실패` | 로봇 PC에서 `--host 0.0.0.0` 실행 여부, 같은 네트워크 여부, 방화벽(8000) 확인 |

블루투스가 60초 후 끊기면 절전을 해제한다(리더 설정 변경, 태그·DB 변경 없음):

```bat
windows_rfid_bridge\3_no_sleep.bat
```

## 5. 실행

```bat
windows_rfid_bridge\4_run_bridge.bat
```

태그를 올리면 다음처럼 표시되고 로봇이 집으러 간다.

```
[22:45:01] 태그 UID: 0B71E55D
[22:45:01]   DB 매칭: 아몬드 빼빼로 (snack_pepero) -> BOX-01
[22:45:01] O 작업 등록: task=1f86a7f8 상태=QUEUED -> 로봇이 집으러 갑니다
[22:45:03] 태그를 뗐습니다. 다음 태그를 올려 주세요.
```

- 등록된 태그: `0B71E55D` 빼빼로/BOX-01, `0B71E75D` 생수·콜라/BOX-02, `0B71E65D` 스팸/BOX-03,
  `0B71ED5D` 물티슈/BOX-04, `0B71EC5D` 인형/BOX-05
- 로봇 PC로 보내지 않고 UID만 보려면 `windows_rfid_bridge\read_uid_only.bat`
- `X 등록 거부 4xx`: DB에 없는 태그이거나 상품 상태 문제. 대시보드에서 태그 등록을 확인한다.
- 작업 진행·완료는 로봇 PC 대시보드 `http://<로봇PC IP>:8000` 에서 Windows 브라우저로도 볼 수 있다.

## 주의

- 이 브리지는 로봇을 직접 움직이지 않는다. 로봇 동작·정지는 로봇 PC와 펜던트(E-Stop)에서 한다.
- 태그를 한 번 올릴 때마다 작업이 하나 생긴다. 잘못 올렸으면 로봇 PC 대시보드에서 작업을 정리한다.
- 박스 슬롯은 현재 1번만 교시되어 있어 BOX-02~05 상품도 슬롯 1에 놓인다(로봇 PC 로그에 `box_fallback`).
