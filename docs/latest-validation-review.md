# Latest commit validation and optimization review

Baseline: `5baa41d7bac00032ec8fd723400fd9a1e1f34788`.

This review preserves all files under `web/app/static/`, including the manual/RFID buttons newly added in the baseline. It does not revert those upstream UI changes or change the 17-stage workflow.

Confirmed and repaired:

- Fresh state directories could prevent the embedded RFID database from opening. Initialization now creates parent directories during application startup, and importing the standalone RFID API does not create a database.
- Reader failure silently substituted the first product tag and enqueued work. Automatic scans now return 503 without creating a task; explicit preset buttons remain available.
- Duplicate-tag scans could return the FIFO head for another product. They now return the matching task snapshot captured in the enqueue transaction.
- Concurrent requests could contend for the same PC/SC reader. One reader operation per process is accepted at a time; other requests return 409, while asynchronous API reads remain responsive.
- Listing tags performed five separate DB connections and omitted custom registrations. One query returns all registered products, preserving insertion order.
- Packaged wheels omitted the runtime `httpx` dependency and all default configuration assets. Wheels now carry an explicit allowlist of default assets and use a writable state directory outside site-packages.
- Web/controller default RFID paths were inconsistent. The lab launcher shares `RFID_DB`; the controller places it beside its journal, and the default web entrypoint uses the repository state directory.
- The old E2E runner referenced another checkout and an absolute Windows artifact path. Portable tests now exercise the validated `vision_picking` module.
- The bundled web tests omitted their rosbridge source dependencies. Those files were restored from the existing pinned upstream revision, with provenance recorded.
- Existing scenario fixtures lacked newly serialized nullable `target_joints` fields. Only fixture metadata was synchronized; motion steps and production scenario code were preserved. A telemetry test now uses a deterministic clock.

The review branch adds Ubuntu 24.04 Python/C++ and ROS 2 Jazzy build jobs. These jobs never run a robot bringup or connect to hardware. Local Windows checks and CI results are reported separately; passing MOCK tests does not certify real hardware or camera calibration.

## 결과 — 2026-11-10

**기준 Ubuntu 24.04 / ROS 2 Jazzy에서 실제 빌드·검증을 통과했다.**
[GitHub Actions 실행 결과](https://github.com/vision-picking-robot/robotArm_Controller_demo/actions/runs/42747052195)는
구현 커밋 `5e7532d0a652c6c764ad1a4ecbb2cf2c11d473e0`를 검증했다. 이후 커밋은 이 보고서와
로컬에서 실행한 UI 검증 도구·증거 파일만 추가한다. 현재 main의 UI/작업 순서는 변경하지 않았다.

| 검사 | 결과 |
|---|---|
| Ubuntu 24.04 / Python 3.12 제어기·RFID·회귀 | 45 passed, 8 subtests passed |
| Ubuntu 24.04 웹 전체 회귀 | **265 passed**, 28 subtests passed |
| JavaScript UI·음성·17단계 동작 | **50 passed** |
| C++ 패킷·분할 수신 프로토콜 | 두 실행 파일 컴파일·실행 통과; CRC, 구/신 DRFL 함수 형태, 쓰기 순서·재전송 금지 검사 |
| Jazzy 고정 드라이버 | dsr_msgs2 / dsr_common2 / dsr_hardware2 colcon 빌드 통과 |
| Jazzy 그리퍼 플러그인 | warehouse_gripper 빌드·동적 링크·launch 인자·colcon 테스트 통과 |
| wheel 배포 | 빌드, 새 venv에 설치, 소스 폴더 밖의 단독 기동 통과 |
| 설치된 wheel의 MOCK API | RFID 5개 조회 및 피킹 한 사이클 completed |
| 실제 브라우저 | Chromium 1440×900, 390×844에서 수동 정지/재개·RFID 프리셋·음성 패널 검사 통과 |
| RealSense 깊이 보조 함수 | 가짜 깊이로 이상치 중앙값·영상 가장자리·깊이 없음 3건 통과 |

Windows에서 수행한 최초 웹 전체 검사는 31 failed / 231 passed / 3 skipped였다.
PC/SC 테스트의 열린 임시파일, POSIX pass_fds 등 플랫폼 의존 조건이 포함된다.
목표 Ubuntu에서 위 전체 검사를 실제 실행해 모두 통과했으며, Windows 전체 지원을 보증하는 결과로 확대하지 않는다.

## UI 보존 확인

- 최신 커밋 5baa41d의 `web/app/static/` 전체 파일 해시가 동일하다.
- 두 화면 크기에서 카메라·현재 주문·이벤트 패널의 좌표와 크기가 수정 전과 정확히 같다.
- 현재 주문 버튼 14개와 17단계 순서를 유지했다.
- 수정 전후 스크린샷의 차이는 시작할 때 생성되는 작업 기록의 실제 시각뿐이었다.
  그 시각 영역을 제외하면 두 해상도 모두 픽셀이 일치한다. 시각을 포함한 이미지 전체가 같다고 주장하지 않는다.
- 최신 커밋 자체가 이전 버전에 수동/RFID 버튼과 제목을 추가한 사실은 별도다. 이번 작업에서 그 변경을 되돌리거나 확대하지 않았다.

증거: [원본 UI 해시](validation/latest/ui-source-baseline.json),
[검증 후 화면 정보](validation/latest/ui-after.json),
[데스크톱 화면](validation/latest/dashboard-1440.png),
[모바일 화면](validation/latest/dashboard-390.png),
[CI 상세 상태](validation/latest/ci-status.json).

## 최적화 판정

코드 전체가 완전히 최적화되었다고 판단하지 않는다. 실제 확인된 중복 DB I/O와 리더 접근 경쟁을
줄이고, 오류를 정상 데이터로 바꾸던 동작 및 패키징 결함을 수정했다.

- 태그 5개 목록: DB 연결 **5회 → 1회(80% 감소)**. 단일 쿼리로 사용자 추가 태그도 조회한다.
- 리더기 요청별 타이머 스레드를 제거하고 스캐너의 기존 timeout을 사용한다. 같은 프로세스의 중복 스캔은 즉시 409다.
- Windows의 로컬 TestClient 100회 측정: 중앙값 **2.020 ms**, p95 **2.297 ms**.
  이는 개선 후 가상 HTTP/DB 경로의 측정값이며 실제 네트워크·RFID 리더기·YOLO 추론 지연이 아니다.
- 제어 속도, 좌표 변환식, 실제 모션 순서, 파지 확인 조건은 최적화 명목으로 변경하지 않았다.

실제 YOLO FPS, D455 스트림 지연, PC/SC 장치 응답, 실물 팔·그리퍼 동작은 장비 없이 측정할 수 없다.
장기간 작업 저널 누적·실제 부하에서의 p95 지연과 센서 보정은 현장 프로파일링 대상으로 남긴다.
`controller/`는 이전 초안이며 검증된 실물 진입점은 `vision_picking`이다.

## 적용 및 재현

수정은 `review/5baa41d-validation` 브랜치에 있다. main을 자동 병합하지 않았다.

```bash
git fetch origin
git switch review/5baa41d-validation
git pull --ff-only
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest tests robot_rfid -q
(cd web && python -m pytest tests -q)
node --test web/tests/*.test.cjs
```

브라우저 검증 도구는 추가 개발 의존성을 설치해 실행한다.

```bash
python -m pip install playwright
python -m playwright install --with-deps chromium
python scripts/validate_dashboard.py
```

UI 스킬 `ui-ux-pro-max`의 비동기 중복 제출 차단·레이아웃 안정성 지침을 참고했다.
레이아웃·색상·컴포넌트 디자인 변경은 적용하지 않았다. 실물 연결은 기존 README의 현장 검증 절차를 따른다.
