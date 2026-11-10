# E0509 + RH-P12-RN(A) 비전 피킹 인수인계

**2026-11-10 오후 7시 실습실 기준:** Ubuntu 24.04 / ROS 2 Jazzy / Python 3.12.
기본값은 MOCK이며 로봇에 연결하지 않는다. 실물 보정·TCP·상품별 파지값 검증은 남아 있다.
이 저장소만 clone하면 제어기, 웹/음성 소스, 설정, 분석 문서가 함께 내려온다.

## 노트북에서 바로 시작

Ubuntu 24.04 터미널:

```bash
git clone https://github.com/vision-picking-robot/robotArm_Controller_demo.git
cd robotArm_Controller_demo
python3.12 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python mock_demo.py
python scripts/start_controller.py
```

`venv`가 없다면 `sudo apt install python3.12-venv`로 설치한다.
Windows는 Python 3.12 설치 후 `py -3.12 -m venv .venv`, `.venv\Scripts\Activate.ps1`을 사용하고,
이후 pip·mock_demo·start_controller 명령은 같다. 모델 가중치나 GPU 추론 환경은 포함하지 않는다.

기본 서버: [http://127.0.0.1:8011/docs](http://127.0.0.1:8011/docs).
`GET /healthz`의 `mock_mode:true`를 먼저 확인한다. 한 로봇에 서버 프로세스 하나만 실행한다.
검증된 진입점은 `vision_picking.server:create_app`이며 start_controller.py도 이 진입점을 사용한다.

## IP·포트: 용도를 구분

| 항목 | 기본값/후보 | 적용 위치 |
|---|---|---|
| 실습실 두산 제어기 IP | `110.120.1.56` ~ `110.120.1.63` 중 실제 장비 IP | 두산 드라이버 bringup의 `host`; `config/hardware.example.json:robot_ip`는 인수인계 메타데이터 |
| 노트북 통합 HTTP API | **8011** | `python scripts/start_controller.py` |
| 두산 DRFL 제어기 TCP | **12345** | 두산 드라이버 bringup의 `port:=12345` |
| ROS 네임스페이스 | `dsr01` | `config/*.json:namespace`, 두산 bringup namespace |
| 기존 rosbridge | `9090` | 기존 웹 ROS 관측 설정 |
| 웹 대시보드 | `8000` | 아래 웹 실행 명령 |
| 그리퍼 | Tool Flange RS-485, port 1, ID 1, 57600/8N1 | `ros/warehouse_gripper/config/gripper.yaml` 및 이식 프로토콜 |

**요청한 8011은 HTTP API 포트다. 이를 두산 DRFL의 12345 대신 사용하지 않는다.**
IP 범위는 후보이며 자동 스캔하거나 순서대로 제어권을 획득하지 않는다.
`robot_ip` 설정만 바꾸면 이미 실행 중인 ROS 드라이버의 연결 대상이 바뀌는 것도 아니다.
기존 Humble/GL010117 웹 연결 경로와 Jazzy 그리퍼 플러그인의 SDK/ABI를 섞지 않는다.

## 그리퍼 단독 테스트

로봇·ROS 없이 MOCK 상태/열기/닫기:

```bash
python scripts/test_gripper.py --mode mock --action status
python scripts/test_gripper.py --mode mock --action open
python scripts/test_gripper.py --mode mock --action close
```

실물은 **Ubuntu/Jazzy에서 기존 두산 드라이버의 DRFL 연결을 공유**한다. PC COM3 직결로 가정하지 않는다.
두산 드라이버와 아래 패키지를 같은 Jazzy 작업공간에 빌드한다. 원본 warehouse_gripper와 중복 등록하지 않는다.
SDK/드라이버 의존성이 준비된 작업공간에서:

```bash
source /opt/ros/jazzy/setup.bash
cd ~/ros2_ws
# src 아래 이 저장소를 clone했거나 ros/warehouse_gripper를 연결한 상태
source install/setup.bash
colcon build --packages-select warehouse_gripper
source install/setup.bash
# 기존에 장비 연결이 확인된 두산 bringup을 먼저 실행한 상태
ros2 launch warehouse_gripper gripper.launch.py
ros2 control list_controllers -c /dsr01/controller_manager
ros2 service call /dsr01/warehouse_gripper/status std_srvs/srv/Trigger '{}'
```

플러그인은 기본 `enable_motion:false`다. DART/다른 시리얼 클라이언트를 종료하고,
실물 상태와 작업 공간을 확인한 뒤 단독 명령을 한 개씩 실행한다. 팔을 움직이는 명령은 아래에 없다.

```bash
ros2 param set /dsr01/warehouse_gripper enable_motion true
# 저장소 루트로 돌아와 .venv를 활성화하고 ROS workspace도 source한 터미널에서
python scripts/test_gripper.py --mode real --namespace dsr01 --action status
python scripts/test_gripper.py --mode real --namespace dsr01 --action open
python scripts/test_gripper.py --mode real --namespace dsr01 --action close
python scripts/test_gripper.py --mode real --namespace dsr01 --action status
ros2 param set /dsr01/warehouse_gripper enable_motion false
```

열기/닫기는 실제 손가락을 움직인다. real 쓰기는 REAL/AUTO/STANDBY 확인과 플러그인 enable_motion을 요구한다.
close의 `command_acknowledged:true`는 쓰기 응답 확인이며 완전 닫힘·물체 파지 성공이 아니다.
`grasp_verified:false`를 유지한다. 상태의 실제 위치·전류·moving을 현물과 대조한다.
오류 시 MOCK로 자동 전환하지 않는다. 실물 통신/동작을 이번 Windows 작업에서 시험하지 않았다.

## 실측값을 넣는 정확한 위치

| 실측/교시 항목 | 파일·필드 |
|---|---|
| 카메라 4점 pixel↔BASE(mm, ZYZ deg), 장치/세션/해상도 | `config/hardware.local.json:calibration.camera`, `calibration.corners`, `calibration.revision` |
| 클래스 라벨→상품 | 같은 파일의 `class_map` |
| 상품별 파지 위치 범위·최소 전류 | 같은 파일의 `grasp_feedback.<상품>.min_position_pulses/max_position_pulses/min_current/calibrated` |
| 작업 공간·namespace | 같은 파일의 `workspace_mm`, `namespace` |
| 바닥·접근 높이·속도 한계·홈/경유/슬롯 | `config/poses.yaml:floor/motion/poses/box_slots` |
| 상품 바닥 기준 집기 높이·교시 여부 | `config/products.yaml:products.<상품>.real` |
| 실제 공구 TCP 이름·XYZABC 오프셋·Tool 설정 | **두산 펜던트/드라이버의 TCP·Tool 설정**. 현재 코드가 이 값을 자동 적용하지 않음 |
| TCP 등 현장 검증 완료 표시 | `hardware.local.json:tcp_verified`, `calibration_verified`, `waypoints_verified`, `gripper_verified`, `workspace_verified` |

TCP 오프셋과 그리퍼의 파지 피드백은 다른 값이다. URDF 장착 회전을 실제 TCP로 대신하지 않는다.
기본 integration.json의 보정/파지 수치는 MOCK 예시이며 실물용이 아니다. slots 2–4는 미교시다.

```bash
cp config/hardware.example.json config/hardware.local.json
# hardware.local.json에 실측값을 입력한다. local 파일은 Git에서 제외된다.
export PICKING_CONFIG="$PWD/config/hardware.local.json"
export MOCK_MODE=false
python scripts/start_controller.py
```

example의 보정은 비어 있고 검증 플래그는 false여서 그대로 실물 피킹을 시작할 수 없다.
위 명령은 실측·검증을 완료한 **후** 실행한다. `MOCK_MODE=false`만으로 구동 차단이 풀리지 않는다.
합성 프레임 `simulation_only:true`는 실물 모드에서 거부한다. 실물 경로는 현재 수평 작업면만 지원한다.

웹에서 저장한 보정을 읽기 전용으로 가져오려면:

```bash
python import_web_calibration.py --web http://127.0.0.1:8000 --region legacy
```

결과는 `config/imported.mock.json`이며 여전히 MOCK다. 카메라 세션이 바뀌면 재동기화한다.

## 보존된 웹 화면·한국어 음성

새 터미널에서 저장소 루트의 venv를 활성화한 뒤:

```bash
export YOLO_VOICE_ENABLED=1
export YOLO_CAMERA_AUTOSTART=0
export YOLO_DEMO_CONTROLLER_URL=http://127.0.0.1:8011
python -m uvicorn app.main:app --app-dir web --host 127.0.0.1 --port 8000
```

Windows PowerShell은 `export KEY=value` 대신 `$env:KEY='value'`를 사용한다.
[대시보드](http://127.0.0.1:8000)에서 기존 **음성 명령→대화 시작**을 누르고 브라우저 마이크를 허용한다.
Web Speech API의 `ko-KR` 전사를 기존 packing 처리기로 전달한다. Chrome/Edge localhost 또는 HTTPS가 필요하다.
실제 카메라는 별도 가중치/추론 의존성을 준비한 뒤 활성화한다. 명시적 mock backend만 합성 탐지를 허용한다.
기존 단일 파지 위치 승인 UI는 새 제어기로 연결된다. 기존 17단계 시나리오의 순서는 유지하며
그 실물 실행 경로까지 이 새 API로 대체했다고 간주하지 않는다.

## 수동 버튼 제어·RFID 태그 인식·카메라 통합

- **수동 버튼 제어 (음성 인식 대체 UI)**:
  - 실습실에서 마이크나 Web Speech API를 사용할 수 없는 경우, 메인 대시보드 우측 **[현재 주문 · 수동 버튼 및 RFID 제어]** 패널과 음성 패널 내 **버튼으로 실행** 영역에서 모든 명령(`▶ 시작 (재개)`, `⏸ 정지`, `🦾 집어 (피킹)`, `📦 재고 조회`, `📋 주문 조회`, `🛒 예시 주문`)을 클릭 한 번으로 동일하게 실행할 수 있다.
- **RFID 태그 인식 및 작업 큐 (`robot_rfid/`)**:
  - `https://github.com/vision-picking-robot/rfid-pick-and-place`의 PC/SC 리더기(`ACR1555`/`ACR1552`) 스캐너, 절전 해제 도구(`robot_rfid/rfid_power.py`), SQLite 작업 큐(`robot_rfid/database.py`), 오케스트레이터(`robot_rfid/orchestrator.py`), API 엔드포인트(`/api/v1/tags/{uid}`, `/api/v1/scan-event`, `/api/v1/rfid/scan`, `/api/v1/current-task`, `/api/v1/tasks/{task_id}`)가 `8000` 웹 서버와 `8011` 제어기 서버 양쪽에 통합되어 있다.
  - 등록된 5종 상품 태그: `0B71E55D` (빼빼로/BOX-01), `0B71E75D` (생수·콜라/BOX-02), `0B71E65D` (스팸/BOX-03), `0B71ED5D` (물티슈/BOX-04), `0B71EC5D` (인형/BOX-05).
- **카메라 영상 및 RealSense 3D 깊이/높이 측정 (`web/app/`, `camera_depth/`)**:
  - 웹 대시보드(`web/app/capture.py`, `web/app/realsense.py`, `web/app/pick_plane.py`)에 OpenCV(Linux V4L2 / Windows DirectShow / macOS AVFoundation) 실시간 카메라 스트리밍, Intel RealSense 깊이·적외선·RGBD·3D 포인트 클라우드 뷰, 4점 평면 호모그래피(`PickPlane`) 좌표 변환이 포함되어 있다.
  - 추가적으로 `camera_depth/` 폴더에 RealSense D455f 기반 실시간 3D 높이·거리 측정기(`camera_depth/realsense_measure_height.py`, `camera_depth/run_measure_height.bat`) 및 전용 뷰어(`camera_depth/realsense_viewer.py`, `camera_depth/run_viewer.bat`)가 함께 포함되어 있다.

## 문서·검증

- [OS/ROS 확정: Ubuntu 24.04 / ROS 2 Jazzy](docs/01-environment-report.md)
- [통신·웨이포인트·좌표 변환·그리퍼 결함 명세](docs/02-legacy-assets.md)
- [Web Speech API·페이로드·웹 연결 계약](docs/03-pipeline-and-speech.md)
- [현장 인수인계와 검증 한계](docs/04-runbook-and-validation.md)
- [추출 자산 JSON](docs/legacy-assets.json), [이식 출처](provenance.json), [Payload Schema](config/payload.schema.json)

문서 4종은 앞선 분석 기록이므로 과거 작업 폴더/8010 예시가 있다. **이 clone의 실행 포트·경로는 이 README의 8011 기준을 따른다.**
원본 소스 출처와 snapshot 범위는 [웹 출처](docs/web-source-provenance.json)에 기록했다.

```bash
python -m pytest tests -q
# Node.js가 있으면 보존된 UI 및 음성 JavaScript 검사
node --test web/tests/*.test.cjs
```

로컬 MOCK·API·선택 검사와 실물 파지 성공은 다르다. 전체 웹 회귀의 남은 실패와 미검증
C++/ROS 빌드·실제 마이크·실물 보정 사항은 현장 인수인계 문서에 기록했다.
