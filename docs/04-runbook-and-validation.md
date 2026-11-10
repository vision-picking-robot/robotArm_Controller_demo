# Ubuntu 실습실 최종 실행·검증·인수인계

이 문서는 main을 git pull한 노트북에서 따라가는 현재 실행 절차다.
README와 동일한 1–7단계를 사용한다. 이전 Windows 경로·8010 포트 예시는 현재 절차에 적용하지 않는다.
대시보드 UI 파일 40개와 기존 17단계 작업 순서는 유지한다.

## 최종 실행 절차: main 갱신부터 실물 연결까지

**전제:** Ubuntu 24.04 / ROS 2 Jazzy / Python 3.12를 사용하며, 장비와 호환되는 두산 드라이버가
`~/ros2_ws`에 빌드되어 있어야 한다. 패키지 조회·빌드·상태 확인이 실패하면 해당 단계에서 해결한 뒤 진행한다.
저장소 경로가 다르면 `~/vision_lab/robotArm_Controller_demo`를 실제 경로로 바꾼다.

| 용도 | 주소·포트 |
|---|---|
| 두산 제어기 | 펜던트에서 확인한 실제 IP `110.120.1.56`~`110.120.1.63` 중 하나, DRFL **12345** |
| 노트북 제어 API | `http://127.0.0.1:8011/docs` |
| 웹 대시보드 | `http://127.0.0.1:8000` |
| ROS 네임스페이스 | `dsr01` |
| 그리퍼 통신 | 두산 Tool Flange RS-485, port 1, ID 1, 57600/8N1 |

8011은 두산 DRFL 포트가 아니다. IP 후보를 자동 스캔하거나 PC COM3 직결로 가정하지 않는다.
실제 로봇·그리퍼 동작은 아래에 표시한 실물 단계에서만 수행한다.

### 1. main 갱신 및 Python 환경 준비 — 터미널 A

기존 서버는 해당 터미널에서 `Ctrl+C`로 종료한다. 처음 사용하는 노트북이라면 다음 명령으로 clone한다.

```bash
sudo apt update
sudo apt install -y git python3.12-venv
mkdir -p ~/vision_lab
cd ~/vision_lab
git clone https://github.com/vision-picking-robot/robotArm_Controller_demo.git
```

이미 clone한 노트북은 위 clone 명령을 생략하고 다음부터 실행한다.

```bash
cd ~/vision_lab/robotArm_Controller_demo
git switch main
git pull --ff-only origin main
git log -1 --oneline

source /opt/ros/jazzy/setup.bash
echo "$ROS_DISTRO"
[ -d .venv ] || python3.12 -m venv --system-site-packages .venv
source .venv/bin/activate
python --version
python -m pip install -r requirements.txt
```

`jazzy`, `Python 3.12.x`를 확인한다. 현장 전용 `config/hardware.local.json`은 Git 제외 대상이므로
노트북에서 별도로 보관·입력한다. 모델 가중치와 GPU 추론 환경은 저장소에 포함하지 않는다.

### 2. 로봇 없이 MOCK 확인 — 터미널 A

```bash
unset PICKING_CONFIG
export MOCK_MODE=true

python mock_demo.py
python scripts/test_gripper.py --mode mock --action status
python scripts/test_gripper.py --mode mock --action open
python scripts/test_gripper.py --mode mock --action close
python -m pytest tests robot_rfid -q
```

MOCK 피킹의 `state: completed`, 그리퍼의 `mock_mode: true`, 테스트의 `45 passed`를 확인한다.
이 단계는 실제 장비를 움직이지 않는다.

웹까지 확인하려면 다음 런처를 실행한다.

```bash
python scripts/start_lab.py
```

대시보드 `http://127.0.0.1:8000`, API `http://127.0.0.1:8011/docs`를 연다.
런처는 MOCK와 공유 RFID DB를 사용한다. 확인 후 **터미널 A에서 `Ctrl+C`로 종료**하고 다음 단계로 간다.
음성은 Chrome/Edge의 localhost/HTTPS에서 기존 **음성 명령→대화 시작**과 브라우저 권한 허용으로 확인한다.

### 3. 두산 드라이버 확인 및 그리퍼 빌드 — 터미널 A

```bash
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash

ros2 pkg prefix dsr_bringup2
ros2 pkg prefix dsr_common2
ros2 pkg prefix dsr_hardware2
```

패키지 경로가 나오지 않으면 먼저 두산 Jazzy 드라이버를 준비한다. 기존 Humble/GL010117 바이너리를
새 Jazzy 플러그인과 검증 없이 혼합하지 않는다. 정상 조회 후 이번 저장소의 플러그인을 빌드한다.

```bash
cd ~/ros2_ws
colcon build   --base-paths ~/vision_lab/robotArm_Controller_demo/ros   --packages-select warehouse_gripper
source install/setup.bash
ros2 pkg prefix warehouse_gripper
```

같은 `warehouse_gripper` 소스를 중복 등록하지 않는다. 위 `--base-paths`는 이번 저장소의 복사본을 지정한다.

### 4. 실물 두산 제어기에 연결 — 터미널 A

DART 작업과 다른 로봇 제어 프로그램을 종료한다. 아래 IP는 실제 장비 주소로 변경한다.

```bash
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash

# 펜던트의 실제 주소가 .63이면 이 값을 변경
export ROBOT_IP=110.120.1.56
ping -c 3 "$ROBOT_IP"

ros2 launch dsr_bringup2 dsr_bringup2_rviz.launch.py   name:=dsr01   mode:=real   host:="$ROBOT_IP"   port:=12345   model:=e0509
```

**터미널 A는 실행 상태로 유지한다.** `hardware.local.json`의 robot_ip 메타데이터를 바꾸는 것만으로
이미 실행 중인 ROS 드라이버의 연결 대상이 바뀌지는 않는다.

### 5. 로봇 상태 확인 및 그리퍼 로드 — 새 터미널 B

```bash
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash

ros2 service call   /dsr01/system/get_robot_system   dsr_msgs2/srv/GetRobotSystem '{}'
ros2 service call   /dsr01/system/get_robot_mode   dsr_msgs2/srv/GetRobotMode '{}'
ros2 service call   /dsr01/system/get_robot_state   dsr_msgs2/srv/GetRobotState '{}'
```

각 응답의 `success: true`와 다음 값을 확인한다.

| 필드 | 필요한 값 |
|---|---|
| robot_system | `0` — REAL |
| robot_mode | `1` — AUTO |
| robot_state | `1` — STANDBY |

```bash
ros2 launch warehouse_gripper gripper.launch.py
```

**터미널 B도 실행 상태로 유지한다.** 플러그인은 기본 `enable_motion:false`다.

### 6. 그리퍼 실물 단독 시험 — 새 터미널 C

먼저 상태만 조회한다.

```bash
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash
cd ~/vision_lab/robotArm_Controller_demo
source .venv/bin/activate

ros2 control list_controllers   -c /dsr01/controller_manager
python scripts/test_gripper.py   --mode real --namespace dsr01 --action status
```

`warehouse_gripper`가 `active`이고 실제 위치·전류·moving이 읽히는지 확인한다.

**아래부터 실제 손가락이 움직인다.** 주변을 비우고 상태가 정상일 때 한 줄씩 실행한다.
팔 이동 명령은 아래에 없다.

```bash
ros2 param set   /dsr01/warehouse_gripper enable_motion true
python scripts/test_gripper.py   --mode real --namespace dsr01 --action open
python scripts/test_gripper.py   --mode real --namespace dsr01 --action status
python scripts/test_gripper.py   --mode real --namespace dsr01 --action close
python scripts/test_gripper.py   --mode real --namespace dsr01 --action status

# 단독 시험 종료: 이후 신규 구동 명령 차단
ros2 param set   /dsr01/warehouse_gripper enable_motion false
```

`command_acknowledged:true`는 명령 응답 확인이며 완전 닫힘·물체 파지 성공이 아니다.
`grasp_verified:false`와 실제 위치·전류를 구분해 확인한다. 오류·타임아웃이면 반복 명령 대신
현물과 상태부터 확인한다. enable_motion=false는 토크 해제나 긴급 정지를 뜻하지 않는다.

### 7. 실측값 입력 후 실물 통합 API 실행 — 터미널 C

```bash
cd ~/vision_lab/robotArm_Controller_demo
# 최초 한 번만 복사: 기존 현장 파일을 덮어쓰지 않음
cp -n config/hardware.example.json config/hardware.local.json
nano config/hardware.local.json
```

| 실측·교시 항목 | 적용 위치 |
|---|---|
| 카메라 장치·세션·해상도, 4점 pixel↔BASE, 보정 버전 | `hardware.local.json:calibration.camera/corners/revision` |
| 클래스 라벨→상품 | 같은 파일의 `class_map` |
| 상품별 파지 위치 범위·최소 전류 | `grasp_feedback.<상품>.min_position_pulses/max_position_pulses/min_current/calibrated` |
| 작업 공간·ROS namespace | `workspace_mm`, `namespace` |
| 바닥·홈·접근·경유·박스 좌표 | `config/poses.yaml` |
| 상품별 바닥 기준 집기 높이 | `config/products.yaml` |
| 실제 공구 TCP 이름·오프셋·Tool | **두산 펜던트/드라이버 설정** |

TCP와 상품 파지 피드백은 별개다. 기본 MOCK 보정값을 실물에 사용하지 않는다.
수평 작업면만 지원하며, 슬롯 2–4는 미교시다. 현재 바닥 표본은 1개이므로 현장에서 4점 이상 재측정한다.
실측·검증을 끝낸 항목만 다음 표시를 true로 설정한다.

```text
calibration_verified
tcp_verified
waypoints_verified
gripper_verified
workspace_verified
grasp_feedback.<상품>.calibrated
```

기존 웹의 보정값을 읽기 전용으로 가져오려면 아래 명령을 사용할 수 있다.
결과 `config/imported.mock.json`은 여전히 MOCK이며 카메라 세션 변경 시 재동기화한다.

```bash
python import_web_calibration.py --web http://127.0.0.1:8000 --region legacy
```

모든 현장 확인을 마친 후 별도 실물 API를 실행한다. MOCK 런처는 이미 종료되어 있어야 한다.

```bash
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash
cd ~/vision_lab/robotArm_Controller_demo
source .venv/bin/activate

export PICKING_CONFIG="$PWD/config/hardware.local.json"
export MOCK_MODE=false
python scripts/start_controller.py
```

다른 터미널에서 확인한다.

```bash
curl http://127.0.0.1:8011/healthz
```

`mock_mode:false`는 서버 모드 확인이다. 실제 로봇 연결 상태는 4–6단계 조회로 확인한다.
서버 기동만으로 팔이나 피킹이 시작되지 않는다. 자동 피킹에는 유효한 탐지 프레임·보정·파지 설정과
플러그인 구동 허용이 필요하다. 6단계 종료 때 차단한 enable_motion은 실제 작업 직전 준비를 확인한 뒤
다시 true로 설정해야 한다. 실물 동작을 위해 MOCK 전용 start_lab.py를 사용하지 않는다.

작업 종료 시 동작 완료를 확인하고 신규 그리퍼 명령을 차단한 뒤 각 서버 터미널을 `Ctrl+C`로 종료한다.

```bash
ros2 param set /dsr01/warehouse_gripper enable_motion false
```

## 최종 확인된 main CI

검증 커밋: `d09503f7489929ea6d2f3657048ea38976dce1c4`.
[GitHub Actions 최종 성공 결과](https://github.com/vision-picking-robot/robotArm_Controller_demo/actions/runs/42749314998).
이번 실행 절차 문서 갱신은 제어 코드나 UI를 변경하지 않는다.

| 검증 | 결과 |
|---|---|
| Ubuntu 24.04 제어기·RFID | 45개 통과 |
| Ubuntu 24.04 웹 전체 | 265개 통과 |
| JavaScript UI·음성·작업 순서 | 50개 통과 |
| C++ 프로토콜·wheel 설치 | 통과 |
| ROS 2 Jazzy 두산 드라이버·그리퍼 | 빌드·링크 통과 |
| Jazzy 그리퍼 테스트 | 2개 통과, 오류·실패·건너뜀 0 |

재검증 명령은 저장소 루트의 venv에서 실행한다.

```bash
python -m pytest tests robot_rfid -q
(cd web && python -m pytest tests -q)
node --test web/tests/*.test.cjs
```

## 현장에서 이어 확인할 사항

- 실제 E0509 펌웨어와 Jazzy 드라이버/DRFL 버전의 호환성, 제어권, TCP/Tool을 확인한다.
- 그리퍼의 실제 Moving/Moving Status, 위치·전류·전압 및 상품별 파지를 확인한다.
- 카메라 4점 보정, 바닥 높이, 접근 경로, 박스 슬롯과 주변 장치 충돌 여유를 재측정한다.
- 실제 마이크 인식·RealSense 스트림·YOLO FPS·실물 파지는 CI가 보증하지 않는다.
- 오류·타임아웃은 결과 미확정으로 다루고 자동 재시작·해제·홈 복귀하지 않는다.
- 소프트웨어는 TCP 목표점의 z를 검사한다. 팔 전체 경로·그리퍼 주변의 충돌은 별도 확인한다.
- 음성/버튼 정지는 기존 데모 주문 일시정지이며 장비 긴급 정지와 동일하지 않다.

이전 Windows 검증 기록과 최신 수정 근거는 [최신 검증 리포트](latest-validation-review.md),
소스 출처는 [추출 자산 명세](02-legacy-assets.md), [이식 출처](../provenance.json)에 남아 있다.
