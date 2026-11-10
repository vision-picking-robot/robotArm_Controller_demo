# RH-P12-RN-A 그리퍼 ROS 2 제어

두산 E0509 한 대를 연결한 `controller_manager`에 그리퍼 컨트롤러를 로드합니다.
컨트롤러는 두산 하드웨어 인터페이스의 기존 DRFL 연결을 공유하며, 플랜지 시리얼을 통해
Modbus RTU로 통신합니다. 팔 관절 인터페이스는 점유하지 않습니다.
여러 두산 팔이 같은 프로세스에 연결된 구성에서는 사용하지 않습니다.

Ubuntu 24.04·ROS 2 Jazzy에서의 갱신·빌드·상태 조회·단독 시험은
[우분투 실습 순서](../docs/ubuntu-jazzy-gripper.md)를 따릅니다.

## DART 성공 코드와의 차이

Task Writer에서 성공한 코드는 컨트롤러 안의 DRL `flange_serial_*` 함수를 사용합니다.
이 ROS 패키지는 PC의 C++ 플러그인이 기존 두산 연결을 통해 DRFL `flange_serial_*`를
호출합니다. DART에서 함수가 실행됐다는 사실만으로 ROS 플러그인까지 로드·활성화되지는
않습니다. ROS 제어 시에는 DART 작업과 다른 시리얼 클라이언트를 먼저 종료합니다.

사용자가 실물에서 확인한 설정과 아래 패킷을 그대로 사용합니다.

| 항목 | DART 성공 코드 | ROS 컨트롤러 |
| --- | --- | --- |
| 통신 | 57,600 bps, 8N1, ID 1 | 동일 |
| 토크 | FC06, 주소 256, 값 1 | 동일 |
| 목표 전류 | FC06, 주소 275, 값 400 | 동일, 400 mA |
| 목표 위치 | FC16, 주소 282, 2워드 `[stroke, 0]` | 동일, 열기 0 / 닫기 740 |
| 완료 확인 | `wait(1)` | 쓰기 응답 확인 후 위치·전류·이동 상태 조회 |

ROS에서는 **전류 → 새 목표 위치 → 토크 켜기** 순으로 보냅니다. 토크가 꺼져 있던 경우
이전에 남은 목표를 먼저 실행하지 않도록 하는 순서입니다. 운영 모드 5(전류 기반 위치
제어)가 아니거나 하드웨어 오류가 있으면 명령을 거부합니다. 모드·프로토콜·EEPROM을
자동 변경하지 않습니다. 400 mA는 이번 블록 시험 설정이며 상품별 적정 파지력은 별도로
검증해야 합니다.

기존 소스에는 `Moving(570)`과 `Moving Status(571)`가 함께 든 Modbus 레지스터 285를
그대로 `moving`으로 반환하는 오류가 있었습니다. 예를 들어 정지 상태가 `0x3100`이면
기존 코드는 `moving=12544`로 반환해 피킹 코드가 완료를 기다리다 실패했습니다.
지금은 `moving=0 moving_status=49`로 분리합니다. 이는 **완료 판정 오류**이며, 처음부터
손가락이 전혀 움직이지 않는 경우에는 아래 로드·활성화·통신 진단도 필요합니다.

DRFL의 구형 단일 포트 함수와 신형 포트 인자 함수는 빌드 시 설치된 헤더에 맞춰
선택합니다. 구형 API는 포트 1만 허용합니다. 헤더·라이브러리·컨트롤러 펌웨어의 실제
호환성과 실물 포트는 현장 확인 대상입니다.

## 빌드와 로드

기존 두산 드라이버가 빌드된 워크스페이스에서 실행합니다.

```bash
cd ~/ros2_ws
source install/setup.bash
colcon build --packages-select warehouse_gripper
source install/setup.bash
```

이 환경에서 기존 두산 real bringup을 시작한 뒤 다른 터미널에서 아래 명령을 실행합니다.
예시의 로봇 네임스페이스는 `dsr01`입니다.

```bash
source ~/ros2_ws/install/setup.bash
ros2 run controller_manager spawner warehouse_gripper \
  -c /dsr01/controller_manager \
  --param-file "$(ros2 pkg prefix --share warehouse_gripper)/config/gripper.yaml"
ros2 service call /dsr01/warehouse_gripper/status std_srvs/srv/Trigger '{}'
```

같은 로드 작업을 `ros2 launch warehouse_gripper gripper.launch.py`로 실행할 수 있습니다.
기존 두산 드라이버의 controller manager를 사용하고, 기본 설정은 동작 차단 상태입니다.

`picking.launch.py`는 그리퍼 플러그인을 로드하지 않습니다. 위 spawner를 별도로 실행하고
아래 조회에서 `warehouse_gripper`가 `active`인지 확인한 후 피킹을 시작합니다.

```bash
ros2 control list_controllers -c /dsr01/controller_manager
ros2 param get /dsr01/warehouse_gripper enable_motion
ros2 service call /dsr01/system/get_robot_state dsr_msgs2/srv/GetRobotState '{}'
```

`status`는 위치(펄스), 전류 레지스터 값, 이동 플래그를 읽습니다. 상태 조회는 그리퍼의
위치·전류·토크 레지스터에 값을 쓰지 않습니다. 기본 포트 번호는 1이며 실물 포트와
일치하는지 확인해야 합니다. 포트 설정은 컨트롤러 구성 시 읽습니다.
시리얼 설정은 57,600 bps, 8N1, Modbus Slave ID 1입니다.
상태 응답 예시는 `position_pulses=0 current=0 moving=0 moving_status=49 input_voltage_tenths=241`입니다.
`moving_status` 값은 프로파일 상태에 따라 달라집니다.
전압 값은 0.1V 단위입니다. 위 241은 설명용 가상 값이며 실측값이 아닙니다.
시리얼 개방 후 500ms를 기다리고 응답을 최대 1초 동안 모읍니다. 첫 전압 읽기(40297)가
타임아웃된 경우에만 최대 3회 시도하며, 구동 쓰기는 재전송하지 않습니다.

## 열기와 닫기

먼저 상태 조회에서 정상 응답을 확인합니다. 실물 그리퍼만 시험할 수 있는 상태에서
`enable_motion`을 켜고 서비스 한 개씩 호출합니다. 이동 명령은 로봇이 STANDBY일 때만
전송하며 자동 모드도 확인합니다.

```bash
ros2 param set /dsr01/warehouse_gripper enable_motion true
ros2 service call /dsr01/warehouse_gripper/open std_srvs/srv/Trigger '{}'
ros2 service call /dsr01/warehouse_gripper/status std_srvs/srv/Trigger '{}'
ros2 service call /dsr01/warehouse_gripper/close std_srvs/srv/Trigger '{}'
ros2 service call /dsr01/warehouse_gripper/status std_srvs/srv/Trigger '{}'
ros2 param set /dsr01/warehouse_gripper enable_motion false
```

기존 DART 소스에서 확인한 목표 위치 0(열기), 740(닫기), 목표 전류 400 mA를 사용합니다.
서비스 성공은 Modbus 쓰기 응답을 확인했다는 뜻입니다. 손가락 이동 완료와 상품 파지
성공은 위치·전류·이동 플래그 및 실물 상태로 따로 확인합니다. 응답이 끊긴 경우
이미 처리된 쓰기가 있을 수 있으므로 상태를 먼저 조회합니다.
`enable_motion=false`는 이후 서비스 명령을 차단하며 그리퍼 토크를 해제하지 않습니다.

## 응답별 확인 사항

| 증상·응답 | 확인할 내용 |
| --- | --- |
| 서비스가 없음 | `warehouse_gripper` 빌드·source·spawner·로봇 네임스페이스 |
| inactive or busy | 컨트롤러 `active` 여부, 중복 호출 여부 |
| Motion blocked | `enable_motion=true`, 로봇 AUTO·STANDBY, DART 작업 종료 |
| Could not open / Modbus response timeout | DART의 시리얼 점유, 실물 포트, 전원·배선, DRFL/컨트롤러 호환성 |
| Invalid Modbus reply | CRC·ID·수신 길이, 다른 시리얼 명령과의 충돌 |
| Operating Mode must be 5 | DART에서 성공한 장치 설정과 비교. 자동 모드 변경 없음 |
| Hardware Error Status | 그리퍼 오류 확인·복구 후 재조회. 자동 재시도 없음 |
| 쓰기 성공 후 파지 미확인 | 실제 위치·전류 확인, 상품별 `real.grasp_feedback` 설정 |
| moving이 0/1 이외 값 | 이전 플러그인이 실행 중일 수 있음. 재빌드 후 기존 드라이버 프로세스 종료·재시작 |

서비스 실패·타임아웃 뒤에는 명령이 이미 전달됐을 수 있습니다. 상태 조회와 실물 확인 후
복구하며 열기·닫기를 자동 반복하지 않습니다. 피킹 서버는 정지 상태와 위치·전류 조건이
연속 2회 확인돼야 다음 모션으로 진행합니다. `products.yaml`에 상품별 실측
`grasp_feedback`이 없으면 자동 피킹을 시작하지 않습니다. 단독 그리퍼 시험은 이 값 없이
`open`, `close`, `status`로 할 수 있습니다.

## 로봇 없이 검증

저장소 루트에서 ROS 없이 C++17 컴파일러로 패킷·응답 검증 테스트를 실행할 수 있습니다.

```bash
c++ -std=c++17 -Wall -Wextra -Werror -I warehouse_gripper/include \
  warehouse_gripper/test/test_protocol.cpp -o /tmp/gripper_protocol_test
/tmp/gripper_protocol_test
c++ -std=c++17 -Wall -Wextra -Werror -pthread -I warehouse_gripper/include \
  warehouse_gripper/test/test_rtu_session.cpp -o /tmp/gripper_rtu_test
/tmp/gripper_rtu_test
python3 -m pytest warehouse_motion/test -q
```

ROS 환경에서는 `colcon test --packages-select warehouse_gripper warehouse_motion`과
`colcon test-result --verbose`로 실행합니다. 테스트는 DART와 동일한 명령 바이트,
CRC·오류 응답 거부, 정지·이동 상태 바이트 분리, 구형·신형 DRFL 호출 형태, 전송 실패 시
후속 쓰기 중단과 피드백 완료 조건을 확인합니다. 실물 RS-485 통신·ROS 플러그인 로드
성공을 대신하지 않습니다.

레지스터 기준: [ROBOTIS RH-P12-RN(A) 공식 매뉴얼](https://emanual.robotis.com/docs/en/platform/rh_p12_rna/).
