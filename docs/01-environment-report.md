# OS/ROS 환경 확정 리포트

작성일: 2026-11-10 (한국 시간). 정적 소스 판정과 로컬 MOCK 실행 결과를 구분한다.

## 결정

**신규 실물 통합 제어기의 기준은 Ubuntu 24.04 + ROS 2 Jazzy + Python 3.12로 정한다.**
수정된 그리퍼 플러그인이 사용하는 드라이버·SDK 링크 방식과 레거시의 명시된 CI 빌드 기준을
따른 결정이다. 20.04/Noetic이나 22.04/Humble 중 하나만 택하면 전체 소스의 실제 상태를 잘못 설명한다.
`ament`만으로 ROS 2의 세부 배포판을 알아낼 수는 없다.

| 범위 | 코드 근거 | 판정 |
|---|---|---|
| 레거시 warehouse_interfaces | `package.xml`: `buildtool_depend=ament_cmake`; `CMakeLists.txt`: rosidl 생성 | ROS 2 |
| 레거시 warehouse_gripper | `package.xml`: `ament_cmake`; CMake의 `dsr_common2::drfl`, `dsr_hardware2`, controller_interface 링크 | 새 드라이버 ABI 의존 |
| 레거시 warehouse_motion | `package.xml`: **buildtool_depend 태그는 없음**, export/build_type=`ament_python`; `setup.py` console_scripts | ROS 2 Python 패키지 |
| 레거시 CI | `.github/workflows/ubuntu-jazzy.yml`: ubuntu-24.04, ros:jazzy-ros-base, Python 3.12, 드라이버 `dade65d3…` 고정 | Jazzy 기준 빌드 절차가 존재. 이 세션에서 CI 실행 성공 여부까지 확인한 것은 아님 |
| YOLO rosbridge·driver | 두 `rosbridge/*Dockerfile`: `FROM ros:humble-ros-base`; Compose의 이미지 `humble-gl010117` | **기존 웹 연결 경로는 Ubuntu 22.04 / Humble 계열** |
| YOLO SDK | Compose의 API-DRFL `ddf59fad…`, 구 드라이버 `1634069…`; GL010117 해시 확인 | 최신 Jazzy 플러그인과 헤더/라이브러리 혼용 금지 |
| 그리퍼 description | `package.xml`: `ament_cmake`, `rclcpp`, `dsr_msgs2`; CMake C++17; 일부 xacro는 ign_ros2_control | ROS 2. 태그만으로 Humble/Jazzy 단독 확정 불가. Gazebo 전체의 Jazzy 호환성은 별도 미검증 |
| 웹 앱 | pyproject Python `>=3.11,<3.13`; robot-web Docker Python 3.11; 기존 MPS/CoreML 잠금 파일은 macOS 전용 | Windows/Ubuntu MOCK 및 API 검사는 별도 Python 3.12 환경 사용 |
| demo 원본 | clone 성공, 커밋 없음 | 기존 데모 로직 없음. 이번 작업에서 패키지를 새로 구성 |

두산 SDK의 `library/Linux/64bits/20.04/` 복사 경로는 **라이브러리 배포 디렉터리**이다.
그 문자열 때문에 `FROM ros:humble-ros-base` 컨테이너를 Ubuntu 20.04로 판정하면 안 된다.
검사 대상에서 catkin/ROS 1 빌드 근거는 발견하지 못했다.

## 기존 부분 구동 경로 보존

사용자가 어제 부분 구동했다고 알려준 YOLO 저장소의 Humble/GL010117 경로는 유지한다.
신규 Python 서비스는 HTTP로 연결하므로 웹 OS와 ROS 제어기 OS를 분리할 수 있다.
기존 Humble Docker 이미지를 새 Jazzy 플러그인용 드라이버라고 간주하지 않는다.
새 그리퍼의 실물 경로는 동일 Jazzy 작업공간에서 두산 드라이버와 추출 플러그인을 함께 빌드해야 한다.
서로 다른 배포판의 DDS 인터페이스·SDK 바이너리 호환은 이번 작업에서 보증하지 않는다.

기본 연결 정보는 `app/robot.py:RobotSettings`, `docker-compose.yml`에서 확인한
`110.120.1.63:12345`, `dsr01`, rosbridge `9090`이다. 소스의 기본값이며 현재 현장 IP 실측값은 아니다.

## 전수 조사 범위와 재현성

원본 4개 저장소에서 package.xml, CMakeLists.txt, setup.py, requirements 계열,
Dockerfile 계열 및 pyproject를 수집했다. 추가로 조직의 robot_depth_camera,
rfid-pick-and-place를 읽었다. 조직 공개 목록은 `organization-repositories.json`에 보관했다.
`.github` 조직 소개 저장소는 로봇 런타임 소스 대상으로 복제하지 않았다.

`source-audit.json`은 변경 전 18개 관련 파일의 원문·라인·SHA-256과 커밋을 보관한다.
`source-audit.final.json`은 추가 참조와 신규 모듈까지 포함한 25개 관련 파일을 보관한다.
Dockerignore 등 이름상 부가 파일도 수집되므로 숫자는 ROS 패키지 개수가 아니다.

| 저장소 | 원본 커밋 |
|---|---|
| ryankim8815/yolov8 | c265ffb0424447dcef8ce67ef72c2172a3eac6ad |
| vision-picking-robot/robotArm_Controller | 22db586b3020e0a6a6d0b9b4f9dc896facd033cb |
| fhekwn549/e0509_gripper_description | ccee97a4c70cac475380593d62a1f3f44d993511 |

공식 플랫폼 근거: [ROS 2 Jazzy 설치 원문](https://raw.githubusercontent.com/ros2/ros2_documentation/jazzy/source/Installation/Ubuntu-Install-Debs.rst),
[ROS 2 Humble 설치 원문](https://raw.githubusercontent.com/ros2/ros2_documentation/humble/source/Installation/Ubuntu-Install-Debs.rst).
