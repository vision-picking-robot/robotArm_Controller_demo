# 웹 화면 보존·음성·페이로드 계약

## 화면과 실행 순서

기존 HTML/CSS 10개 파일을 변경 전 `ui-baseline.json`과 SHA-256으로 대조했다.
일치 결과는 코드·스타일 원형 보존의 근거이며 실제 브라우저 픽셀 스크린샷 검사는 수행하지 않았다.
기존 대시보드·마이크 버튼·음성 패널·명령 처리기·카메라 선택→미리보기→승인 순서를 유지했다.
기존 17단계 시나리오의 순서·교시 구조·작업자 확인 지점도 변경하지 않았다.

새 제어기는 두 경로로 연결한다.

1. **기존 단일 파지 위치 UI:** 웹 프로세스에 `YOLO_DEMO_CONTROLLER_URL=http://127.0.0.1:8010`을
   설정하면 기존 `pick/preview`→`pick/approve`→작업 조회가 신규 HTTP 제어기를 사용한다.
   `mode=pick`, `handling=manual`, `scenario=false`에만 적용된다. 기존 의미대로 **파지 위치 이동만**
   실행하며 그리퍼나 적치를 자동 추가하지 않는다. MOCK 여부를 기존 상태 텍스트로 표시한다.
2. **신규 전체 피킹 API:** `POST /api/integration/pick`가 감지 프레임을 가져와 신규 제어기의
   `/api/v1/pick`에 전달한다. 홈→접근→열기→픽→닫기/파지 확인→상승/유지 확인→경유→적치 접근→
   적치→열기 확인→퇴피→홈의 이식 동작이다. 이 API는 기존 17단계 화면을 대체하지 않는다.
   기존 RH-P12 17단계 실물 시나리오는 종전 실행 경로를 유지한다.

프리뷰 후 승인 시 최신 프레임에서 카메라/모델·동일 클래스·5px 이내 유일 후보를 재확인하고,
새 BASE 목표가 승인 목표에서 10mm 이내인지 확인한다. 카메라 세션·해상도·보정 버전이 바뀌면 거부한다.
웹/컨트롤러 타임아웃 시 구동 POST를 자동 재전송하지 않는다. 작업 상태로 결과를 확인한다.

## Web Speech API

클라이언트: `app/static/browser-voice.js`, 기존 버튼 바인딩: `app/static/packing.js`.
서버: `app/voice.py`, 기존 처리기: `app/voice_commands.py:packing_answer`.

`SpeechRecognition || webkitSpeechRecognition`, lang=`ko-KR`, interimResults=false,
continuous=false로 최종 인식 결과만 전송한다. 응답은 `speechSynthesis`로 읽을 수 있다.
낭독이 끝난 후 다음 청취를 시작한다. 권한 거부·미지원·보안 출처 오류는 기존 패널에서 표시한다.
중단 시 recognition.abort, synthesis.cancel, 세션 무효화를 수행한다.

| API | 계약 |
|---|---|
| GET /api/voice/status | 기존 상태 필드 유지; session 토큰은 조회에 노출하지 않음 |
| POST /api/voice/wake | 기존 상태 + session(32 hex), turn(1부터) |
| POST /api/voice/transcript | `{session,turn,text}`. 기존 INTENTS/packing_answer로 전달 |
| POST /api/voice/stop | 세션 무효화·기존 idle/disabled 상태 |

중복 turn, 지난 세션, 동시 명령은 409. text는 1–500자. 교차 출처 제어는 403.
예시 주문 확인 상태와 기존 처리 순서를 유지한다. 수락한 패킹 작업이 이미 시작됐다면 음성 중단이
그 DB 작업을 취소한다는 의미는 아니다. 음성 “멈춰”는 기존 패킹 주문 일시정지다.
실물 로봇 긴급 정지와 연결했다고 표시하지 않는다.

macOS Swift 음성 헬퍼·빌드 스크립트·음성 단축어 절차와 카메라 열거용 AppleScript를 제거했다.
카메라는 Windows DirectShow, Linux V4L2, macOS AVFoundation을 사용한다. Linux는 sysfs 장치를
열거하며 그 외는 설정된 OpenCV 인덱스를 노출한다. Mac의 예전 장치 uniqueID는 새 인덱스로
다시 선택해야 한다. Windows/Ubuntu 기본 추론 backend는 cpu, macOS는 기존 mps를 유지한다.

Chrome/Edge, localhost 또는 HTTPS에서 마이크 권한이 필요하다. 브라우저 지원·인식 품질은
OS와 서비스에 따라 다르며 일부 구현은 오디오를 외부 인식 서비스로 전송한다.
실제 마이크 인식 시험은 수행하지 않았다.
근거: [SpeechRecognition API](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition).

## 감지→제어 페이로드

원본 관측 API `/api/detections/latest`, `/api/detections/pick-frame`, `/ws/detections`를 유지한다.
xyxy는 리사이즈된 UI 좌표가 아닌 **원본 source_size의 픽셀**이다.
bridge는 bbox 중심 `[(x1+x2)/2,(y1+y2)/2]`을 계산한다.
단일 선택을 새 POST 스키마로 변환하며 WebSocket 관측만으로 로봇 명령을 만들지 않는다.

```json
{
  "schema_version": "1.0",
  "simulation_only": true,
  "request_id": "mock-pick-001",
  "camera": {
    "session_id": "mock-session", "camera_generation": 1,
    "camera_id": "mock-camera", "width": 640, "height": 480
  },
  "frame_id": 1,
  "captured_at": "2026-11-10T00:00:00+00:00",
  "model_version": "mock-model-v1", "object_index": 0,
  "detection": {
    "class_id": 0, "label": "snack_box", "confidence": 0.95,
    "xyxy": [280,200,360,280], "center_px": [320,240]
  },
  "calibration_revision": "mock-only-v1", "box_slot": 1
}
```

예시 시각은 설명용이며 실제 요청은 2초 이내 UTC 시각이어야 한다. JSON Schema:
`robotArm_Controller_demo/config/payload.schema.json`. Pydantic 입력은 엄격 모드,
추가 필드·NaN·Inf·잘못된 bbox·일치하지 않는 중심·낮은 신뢰도·영역 밖 좌표를 거부한다.
임의의 BASE 좌표/단위나 클라이언트 제공 변환행렬로 검증을 우회하지 못한다.
변환 보정과 단위는 컨트롤러 설정의 신뢰된 값만 사용한다.

| 웹 API | 신규 제어기 |
|---|---|
| POST /api/integration/payload | Selection→위 JSON만 생성; 구동 없음 |
| POST /api/integration/plan | POST /api/v1/plan; 변환·경로 검증, 구동 없음 |
| POST /api/integration/pick | POST /api/v1/pick; 전체 이식 피킹 |
| 기존 단일 위치 승인 | POST /api/v1/move-target; 위치 이동만 |
| 결과 조회 | GET /api/v1/jobs/{request_id} |
| 카메라 계약 조회 | GET /api/v1/context |

웹 Selection은 `session_id,frame_id,object_index,request_id,calibration_revision,box_slot`이다.
실제 packet은 서버 소유 카메라에서 읽으므로 브라우저가 클래스/픽셀을 임의 조작해 보내지 않는다.
`simulation_only`는 합성 카메라/모델 여부를 표시한다. 플래그가 없는 구형 packet은
새 bridge에서 보수적으로 simulation_only=true로 취급한다. 실물 신규 제어기는 이를 거부한다.
모델/카메라 오류 시 합성 입력으로 조용히 바꾸지 않고, 명시적 `YOLO_BACKEND=mock`에서만 허용한다.

실행은 단일 워커·로봇 한 대 기준이다. SQLite에 명령 전 running을 기록하고 동일 request_id의
동일 요청은 저장된 결과를 반환한다. 다른 내용의 ID 재사용과 같은 탐지의 새 ID 실행은 거부한다.
프로세스 재시작·타임아웃·부분 실행은 unknown으로 남기며 자동 재시작·해제·복귀하지 않는다.
주문 완료/재고 차감까지 실물 성공과 자동 결합하지 않았다.
