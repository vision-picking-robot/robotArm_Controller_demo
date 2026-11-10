# 19:00 실습실 빠른 시작 (Codex + Antigravity + RFID + Camera 통합본)

최신 설치·실물 검사·보정 위치는 [루트 README](README.md)를 따른다.
Ubuntu 24.04 / ROS 2 Jazzy / Python 3.12 및 Windows 10/11 호환 기준이다.

## 1. 노트북 유선 LAN 및 하드웨어 연결 확인
- **노트북 유선 LAN IPv4 설정**:
  - IP 주소: `110.120.1.11` (서브넷 마스크: `255.255.255.0`, 게이트웨이 비움)
- **두산 로봇 제어기 IP 후보**: `110.120.1.56` ~ `110.120.1.63` (DRFL 포트: `12345`, 노트북 HTTP API 포트: `8011`)
- **RFID 리더기 (ACS ACR1555 / ACR1552)**:
  - USB 또는 Bluetooth 연결 후 절전 모드를 비활성화하려면:
    `python -m robot_rfid.rfid_power --no-sleep`

## 2. 원클릭 실행 순서 (더블클릭 또는 터미널)
1. clone 후 venv를 만들고 `python -m pip install -r requirements.txt`를 실행한다.
2. **그리퍼 단독 진단**: `test_gripper.bat` 더블클릭 (또는 `python scripts/test_gripper.py --mode mock --action status`)
   - 실물 열기/닫기는 README의 ROS 플러그인 준비와 `--mode real --action open/close` 절차를 따른다.
3. **통합 런처 기동**: `start_lab_test.bat` 더블클릭 (또는 `python scripts/start_lab.py`)
   - 제어기 IP Ping 점검 후 MOCK 제어기(`8011` 포트)와 통합 웹 대시보드(`8000` 포트)를 함께 시작하고 브라우저(`http://127.0.0.1:8000`)를 자동으로 연다.
   - `Ctrl+C`로 이 런처가 만든 프로세스만 안전하게 종료한다.

## 3. 대시보드 제어: 음성 인식 + 수동 버튼 제어 + RFID 태그 인식 + 카메라
- **음성 인식 제어**: 상단 `음성 명령` → `대화 시작` 클릭 후 한국어 명령 발화 (`"시작"`, `"정지"`, `"집어"`, `"재고 조회"`, `"주문 조회"`).
- **음성 대체 수동 버튼 제어 (마이크 불가 시)**:
  - 메인 대시보드 우측 **[현재 주문 · 수동 버튼 및 RFID 제어]** 패널에서 마이크 없이 즉시 버튼 클릭으로 제어 가능:
    - `▶ 시작 (재개)` / `⏸ 정지` / `🦾 집어 (피킹)` / `📦 재고 조회` / `📋 주문 조회` / `🛒 예시 주문`
- **RFID 태그 인식 제어 (`robot_rfid` 통합)**:
  - `📡 RFID 리더기 스캔` 버튼 클릭 시 PC/SC 리더기(`ACR1555`)에서 태그를 읽고, 리더기 미연결 시에도 5종 프리셋 버튼(`🏷️ 빼빼로 0B71E55D`, `🏷️ 생수/콜라 0B71E75D`, `🏷️ 스팸 0B71E65D`, `🏷️ 물티슈 0B71ED5D`, `🏷️ 인형 0B71EC5D`)으로 즉시 태그 스캔 및 피킹 작업 큐 등록 가능.
- **카메라 및 3D Depth 측정 (`web/app/capture.py`, `camera_depth/`)**:
  - 웹 대시보드 좌측 **[탐지 카메라]** 패널에서 USB/DirectShow/V4L2 웹캠 및 Intel RealSense(`컬러`, `깊이 맵`, `적외선`, `RGBD`, `3D 포인트 클라우드`) 실시간 스트리밍 지원.
  - RealSense D455f 단독 3D 높이/거리 실측 도구는 `camera_depth/run_measure_height.bat` (`realsense_measure_height.py`) 및 `camera_depth/run_viewer.bat`로 즉시 실행 가능.

## 4. 안전 주의사항
- 핑 응답으로 실물 모드나 구동을 자동 선택하지 않는다. COM3로 연결됐다고 가정하지 않는다.
- 음성/버튼 정지는 데모 주문 일시정지이며 실물 로봇 비상정지(E-Stop)와 동일하지 않다.
- 현재 소프트웨어가 안내하는 “집어” 응답만으로 실물 피킹이 실행됐다고 판단하지 않는다.
