# 실행·검증·현장 인수인계

## 로컬 실행

작업 루트: `C:\Users\SCPOINT\Desktop\test dev`.
Python 3.12.16을 작업 폴더 `.python/`에 설치하고 `.venv312/`에 웹/API 테스트 의존성을 설치했다.
초기 호스트 Python 3.14는 YOLO 프로젝트의 `<3.13` 요구와 달라 최종 검증은 3.12로 수행했다.
학습 모델 가중치·PyTorch/Ultralytics GPU 환경·ROS·로봇 SDK는 이 환경에 설치/검증한 것으로 취급하지 않는다.

작업 중 별도 변경으로 추가된 `robotArm_Controller_demo/controller/`, `scripts/`는 원위치에 보존했다.
이 보고서의 신규 모듈·검증·실행 진입점은 **vision_picking.server:create_app**이다.
별도 controller 패키지는 검증한 납품 소스 묶음에 포함하지 않았다.

```powershell
# 터미널 A: 카메라·로봇 없이 완결되는 MOCK 피킹
.venv312/Scripts/python -X utf8 robotArm_Controller_demo/mock_demo.py

# 터미널 B: 신규 제어 서버 (기본 MOCK)
cd robotArm_Controller_demo
../.venv312/Scripts/python -m uvicorn vision_picking.server:create_app --factory --host 127.0.0.1 --port 8010 --workers 1

# 터미널 C: 기존 웹 (저장소 루트에서)
$env:YOLO_VOICE_ENABLED = '1'
$env:YOLO_DEMO_CONTROLLER_URL = 'http://127.0.0.1:8010'
$env:YOLO_CAMERA_AUTOSTART = '0'
../.venv312/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

세 번째 명령의 현재 폴더는 `ryankim8815_yolov8`이다. 웹은 [대시보드](http://127.0.0.1:8000),
컨트롤러는 [API 문서](http://127.0.0.1:8010/docs)에서 확인한다. 서버가 실제 실행 중일 때만 열린다.
작업 완료 후 서버를 상시 실행해 놓았다는 뜻은 아니다.

화면 음성은 카메라 없이도 시험할 수 있다. 실제 영상은 카메라·현장 가중치가 준비된 뒤
`YOLO_CAMERA_AUTOSTART=1`, `YOLO_BACKEND=cpu` 또는 지원되는 backend로 시작한다.
독립 합성 미리보기는 명시적 `YOLO_BACKEND=mock`에서만 가능하다. 합성 입력은 실물 명령으로 전달하지 않는다.

Dockerfile은 Jazzy MOCK 서버용으로 제공했다. 이 세션에서 Docker 빌드는 실행하지 않았다.
예시: `docker build -t e0509-demo robotArm_Controller_demo`,
`docker run --rm -p 127.0.0.1:8010:8010 e0509-demo`.
실물 장비의 SDK/드라이버를 이 MOCK 이미지가 자동 설치·연결하지 않는다.

## 테스트

실행 명령은 저장소별 pytest 설정이 적용되도록 각 저장소에서 별도로 실행한다.
서로 다른 pytest 루트의 테스트를 한 명령에 섞으면 레거시 pythonpath가 적용되지 않는다.

```powershell
# robotArm_Controller_demo 에서
../.venv312/Scripts/python -X utf8 -m pytest tests -q
# robotArm_Controller/warehouse_motion 에서
../../.venv312/Scripts/python -X utf8 -m pytest test -q
# ryankim8815_yolov8 에서
../.venv312/Scripts/python -X utf8 -m pytest tests/test_api.py tests/test_browser_voice.py tests/test_voice.py tests/test_camera_views.py tests/test_camera_pick.py tests/test_robot_scenario.py tests/test_portable_capture.py -q
node --test tests/*.test.cjs
```

JUnit 결과는 `demo-tests.xml`, `legacy-tests.xml`, `web-targeted-tests.xml`에 보관한다.
최종 선택 검사 결과는 신규 통합 **18 passed**, 레거시 **136 passed**, 웹 관련 **107 passed**,
JavaScript **50 passed**다. 이 숫자는 아래의 전체 웹 회귀 실패와 별도로 보고한다.
실제 localhost HTTP를 열어 웹 bridge→신규 제어기 plan/pick/동일 요청 재조회까지 MOCK로 확인했고
임시 서버는 검사 후 종료했다. 결과는 `http-smoke.json`이다.
확인 항목은 중복 요청/프레임 재실행 차단, 카메라·시각·보정 불일치 거부, 미설정 실물 차단,
합성 데이터 실물 차단, 파지 실패 후 후속 이동/자동 해제 금지, 재시작 unknown 복구,
기존 UI 승인→신규 컨트롤러 위치 이동 연결, 한국어 최종 전사→기존 패킹 처리기 연결이다.

전체 웹 Python 회귀의 최초 실행은 **228 passed / 32 failed / 3 skipped**였다.
Windows 3.12의 임시파일 공유·POSIX pass_fds, 한국어 기본 CP949, 빠른 mock 프레임의
동일 monotonic 시각 등 플랫폼 의존 문제가 포함되었다. 결과 일부는 `windows-failures.log`에 있다.
Windows 소유권 잠금에서 충돌을 삼키던 부분은 차단하도록 수정했다.
UTF-8로 확장 재검사한 집합은 **115 passed / 3 failed**였으며 SDK 재연결 상태 검사 1개,
기존 17단계 예제와 고정 fixture 차이 2개가 남았다. 이들을 신규 연결부 성공으로 덮지 않는다.
새 연결부 중심 검사는 별도 통과 결과를 기록한다. 전체 웹 회귀가 모두 통과했다고 선언하지 않는다.

C++ 컴파일러, ROS, Docker가 없는 Windows에서 C++ 테스트·colcon 빌드·Gazebo·실물 RS-485는 미검증이다.
마이크 권한/실제 발화는 브라우저 실기 미검증이며 JS에서는 가짜 SpeechRecognition으로
최종 전사·중복 이벤트·중단·권한 오류를 검증했다. UI는 HTML/CSS 해시 및 기존 JS 테스트로
보존을 확인했고 실제 브라우저의 픽셀 비교를 수행하지 않았다.

## 19:00 실물 테스트에 남은 확인

1. **환경 일치:** 현장 Ubuntu/ROS·E0509 펌웨어·DRFL 헤더/라이브러리 버전을 확인한다.
   어제 동작한 Humble/GL010117과 새 Jazzy 플러그인을 검증 없이 혼합하지 않는다.
   Jazzy 드라이버 commit `dade65d3…`는 원본 CI 기준이며 실제 장비 호환 보장은 아니다.
2. **통신 조회:** 로봇 IP·dsr01 namespace·TCP/Tool 이름·실물 REAL/AUTO/STANDBY 및 제어권을 조회한다.
   레거시 팔 모션 서버·DART·다른 SDK 클라이언트를 동시에 구동하지 않는다.
3. **그리퍼 단독:** 수정 플러그인을 드라이버와 같은 작업공간에 빌드한다. 기본 enable_motion=false로
   status부터 확인하고 Moving/Moving Status 분리, 위치·전류·전압을 실제 장비와 대조한다.
   단독 열기/닫기 시험 후 상품별 파지 위치·최소 전류 범위를 측정한다.
4. **보정:** 실제 카메라 세션/장치/해상도의 작업면 4점과 floor_z를 측정한다. 현재 YAML의 floor_samples는
   1개여서 문서의 4점 기준을 충족하지 않는다. 보정면은 수평·floor_z 오차 2mm 이내여야 한다.
   TOOL TCP 오프셋·장착 방향·충돌 공간·박스 슬롯 1을 재검증한다. 2–4는 미교시다.
5. **새 설정:** config 사본에 현장 보정·작업 공간·상품 클래스·grasp_feedback을 기록한다.
   검증이 끝난 항목만 *_verified=true와 calibrated=true로 바꾸고, 그 후 MOCK_MODE=false를 사용한다.
   simulation_only=true 프레임은 설정 플래그와 관계없이 거부된다.
6. **첫 실행:** 10mm/s·10mm/s², blending=0의 단일 위치부터 확인한다. 경로 충돌·팔 전체·주변 NFC
   장치 충돌은 SafeMover의 TCP 끝점 z 검사로 보증되지 않는다. 펜던트 안전 영역과 현장 감독 아래
   시험한다. 타임아웃은 unknown이며 결과 확인 없이 재명령하지 않는다.

실물 연결·파지 성공·무충돌을 이 작업에서 확인한 적은 없다. 따라서 '무결점 이식' 또는
'19:00 직결 성공 보장'으로 인수인계하지 않는다. 로컬 소스·MOCK 통합과 실물 준비 조건을 제공한다.
