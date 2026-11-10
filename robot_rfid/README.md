# RFID · Vision Pick-and-Place

Python 3.11 이상. 기본 실행은 **시뮬레이션**이며 실제 로봇 드라이버는 포함하지 않습니다.

## 실행

```powershell
python -m pip install -r requirements.txt
python -m uvicorn api_server:app --host 127.0.0.1 --port 8000 --workers 1
```

다른 터미널에서:

```powershell
python orchestrator.py --quantity 3
python -m unittest -v
```

시뮬레이션은 1번 태그의 빼빼로를 지정 수량만큼 피킹하며 큐를 소진한 뒤 홈으로 복귀합니다. API 최초 시작 시 `rfid.db`에 제공된 5종 데이터를 등록합니다. 기존 행은 덮어쓰지 않습니다. `RFID_DB` 환경 변수로 DB 경로를 변경할 수 있습니다.

## API

로컬 Swagger: http://127.0.0.1:8000/docs

| 경로 | 용도 |
|---|---|
| GET `/api/v1/tags/{uid}` | 제품 상세; 콜론 유무 허용, 4/7바이트 검증 |
| POST `/api/v1/scan-event` | `uid`, `quantity`(기본 1), `event_id`(선택)로 작업 생성 |
| GET `/api/v1/current-task` | FIFO 선두 작업과 YOLO 클래스/목적지; 비어 있으면 `task: null` |
| PATCH `/api/v1/tasks/{task_id}` | `completed`, `status`, 선택 `error` 보고 |
| WS `/ws` | 연결 시 snapshot, 이후 scan/task_progress 실시간 이벤트 |

같은 `event_id`와 같은 내용의 재전송은 기존 작업을 반환합니다. 동일 태그의 활성 작업이 있거나 같은 이벤트 ID에 다른 내용을 보내면 409입니다. 미등록 태그는 404, UID 형식 오류는 422입니다. UID는 대문자 연속 HEX로 저장하므로 선행 0을 보존합니다. 기본 키 인덱스 조회이며 SQLite B-tree의 복잡도는 O(log N)입니다.

WebSocket은 실시간 알림이며 영구 이벤트 로그가 아닙니다. 느린 클라이언트는 오래된 알림이 생략될 수 있으므로 REST로 현재 상태를 조회합니다. 인메모리 방송이므로 서버는 **단일 worker**, 로봇 컨트롤러는 **단일 인스턴스**로 운영합니다. 인증이 없는 로컬 개발 API이므로 외부 공개 전 인증과 접근 제어가 필요합니다.

## 리더기 절전 설정

일정 시간 뒤 태그가 감지되지 않으면 리더기의 절전 여부를 확인합니다.
`rfid_power.py`는 지원 대상 리더기의 PC/SC 제어 명령으로 절전 설정을 조회·해제·복원합니다.
해당 기능을 지원하는 펌웨어 1.02.04 이상에서 사용하며, 기종은 코드의 리더 필터로 제한합니다.
Windows에서 검증했습니다.

리더기 전원을 켜고 USB 또는 BLE PC/SC 연결이 정상인 상태에서 실행합니다.
API 서버는 필요하지 않습니다. 아래 명령은 `robot_rfid` 폴더 기준입니다.

```powershell
# 현재 설정 조회
.\.venv\Scripts\python.exe rfid_power.py

# 절전 해제: 절전 안 함(0x04)
.\.venv\Scripts\python.exe rfid_power.py --no-sleep
```

`현재 설정: 절전 안 함 (0x04)`가 표시되는지 확인합니다.
변경 명령의 응답과 재조회 값이 일치해야 적용 완료로 표시하며, 이미 같은 값이면 다시 쓰지 않습니다.
다시 기본 60초 절전으로 돌리려면 다음 명령을 사용합니다.

```powershell
.\.venv\Scripts\python.exe rfid_power.py --restore-default
```

USB와 BLE 가상 리더가 함께 표시되면 `--reader "PICC 리더 이름 일부"`를 추가해 하나만 선택합니다.
SAM 인터페이스는 선택하지 않습니다. 리더기 목록에 이름이 있어도 실제 연결이 끊겨 있으면 명령을 보낼 수 없습니다.
절전 상태라면 먼저 전원을 켜고 연결을 복구한 뒤 실행합니다.

이 도구는 리더기 설정만 바꾸며 태그 메모리·UID·제품 매핑·작업 큐를 변경하지 않습니다.
`SCardConnect`에 직접 연결 모드와 프로토콜 0을 그대로 전달합니다.
상위 연결 함수가 프로토콜 0을 기본값으로 바꾸는 환경에서도 카드 삽입 없이 설정을 조회할 수 있도록 했습니다.
절전 해제 후에도 태그를 읽으려면 `rfid_scanner.py`를 별도로 실행해야 합니다.

**검증 범위**

- 실제 BLE 연결에서 60초 절전 → 절전 안 함(0x04) 설정과 재조회 확인.
- 이후 약 95초 동안 별도 설정 명령을 보내지 않은 뒤에도 0x04 재조회 확인.
- 전원 재시작 후 설정 유지 여부와 장시간 연속 운용은 아직 미검증.
- 자동 테스트는 가짜 PC/SC 응답을 사용하며 실제 리더기 설정을 변경하지 않음.

```powershell
.\.venv\Scripts\python.exe -m unittest test_rfid_power test_system -v
```

## 실제 로봇 및 비전 연결

`Orchestrator(api, robot, scanner, vision)`에 현장 어댑터를 전달합니다.

- `robot.pre_scan()`: 정의된 리딩 포즈까지 이동 완료 대기.
- `robot.approach(pose)`, `grip()`, `retract()`: 접근 경로 실행, 파지 센서 확인, 안전 리프트.
- `robot.place(box_id)`: 보정된 목적지 접근, 적치, 릴리스 확인 및 퇴피 완료 대기.
- `robot.home()`, `stop()`: 안전 홈 및 즉시 정지 요청. 각 메서드는 실패 시 예외를 발생시켜야 합니다.
- `wait_trigger()`: 작업자/진입 센서 신호를 기다리는 블로킹 콜백.

오류 시 ERROR 및 정지를 요청하고 작업을 DEFECTIVE로 기록합니다. 오류 상황에서는 자동 홈 이동을 하지 않습니다. 중단된 PICKING 작업은 실제 적치 수량을 확인한 뒤 운영자가 정리해야 하며 자동 재개하지 않습니다. 실제 적치와 DB 기록은 원자적일 수 없으므로 통신/전원 장애 후 현물 대조가 필요합니다.

`YoloVision(weights, frame_provider, depth_provider, calibration)`는 학습된 YOLOv8 모델의 클래스 이름과 ID가 모두 일치하는 최고 신뢰도 후보를 선택합니다. 매 피킹마다 새 프레임을 추론합니다. 기본 사전학습 모델에는 이 프로젝트의 커스텀 5개 라벨이 없으므로 전용 학습 가중치가 필요합니다. bbox 중심은 기하학적 후보이며 물체별 파지 가능성은 현장에서 검증해야 합니다.

`Calibration(K, T_base_camera, workspace_min, workspace_max, rpy)`는 고정 상단 카메라 기준입니다. 깊이는 카메라 optical Z의 **미터**, 로봇 위치도 **미터**, RPY는 **라디안**입니다. 입력 프레임은 K에 맞게 왜곡 보정되어야 합니다. RGB-D 깊이는 RGB와 정렬된 유효 깊이를 전달합니다. RGB 단독 사용 시 측정된 작업 평면과 광선 교차를 별도로 구현해 optical Z를 제공해야 하며 임의 상수를 실기에 사용하지 않습니다. 카메라가 로봇에 부착되면 현재 TCP 변환까지 합성해야 합니다.

관절 제한, 충돌 검사, 안전 접근 높이, 속도 제한, 그리퍼 센서, E-stop/안전 PLC는 실제 로봇 어댑터와 장비에서 구현/검증해야 합니다. Python 상태 머신은 인증된 안전 제어기가 아닙니다.

## 데이터 모델과 범위

요청한 제품 테이블과 별도의 `tasks` 수량/이벤트/진행 테이블을 사용합니다. UID 재기록 가능 태그는 인증 수단이 아니며 현장 UID 중복을 관리해야 합니다. SKU UNIQUE는 요청 스키마를 따르므로 한 SKU에 여러 물리 태그를 등록하려면 제품과 태그 테이블 분리가 필요합니다. 소비기한은 제공된 메타데이터로 저장하며 자동 출고 차단 정책은 적용하지 않습니다. 현재 구현은 SQLite입니다.

## 저장된 제품 정보 조회

DB Browser for SQLite에서 `rfid.db`의 `product_tag_mappings`를 수정한 뒤 적용과 변경사항 저장하기를 눌러 저장합니다. `database.py`의 SEEDS는 새 DB의 초기값이므로 기존 DB를 덮어쓰지 않습니다. UID를 읽은 후 `GET /api/v1/tags/{uid}`로 현재 저장값을 조회합니다. 이 조회는 작업을 생성하지 않습니다. `--read-only`는 UID만 출력하며 기본 스캔 명령은 작업을 생성하므로 활성 작업이 있으면 409를 반환합니다.
