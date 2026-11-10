# E0509 통합 제어기

원본 demo 저장소는 커밋 없는 빈 저장소였다. ROS 없는 MOCK 서버와 별도 ROS 2 실물 어댑터를 구성했다.
실물 경로의 기준은 Ubuntu 24.04 / ROS 2 Jazzy, Python 3.12다. Windows·macOS는 MOCK/웹/API 검증이 가능하다.

```powershell
# 로컬 작업 루트 C:\Users\SCPOINT\Desktop\test dev 에서
.venv312/Scripts/python -X utf8 robotArm_Controller_demo/mock_demo.py
cd robotArm_Controller_demo
../.venv312/Scripts/python -m uvicorn vision_picking.server:create_app --factory --host 127.0.0.1 --port 8010 --workers 1
```

설치된 로컬 환경이 없으면 Python 3.12 venv에 상위 `requirements-cross-platform.txt`를 설치한다.
Linux는 같은 명령에서 `.venv312/Scripts/python`을 venv의 `bin/python`으로 바꾼다.
패키지는 `pip install -e .`로 소스 위치에서 사용한다. 설정 파일은 소스 루트 `config/`에 보관한다.

기본 `config/integration.json`과 `MOCK_MODE=True`는 실물 통신을 시작하지 않는다.
`GET /healthz`, `/docs`, `/api/v1/context`로 상태·스키마를 확인할 수 있다.
`config/payload.example.json`의 captured_at을 현재 UTC로 갱신해야 2초 유효성 검사를 통과한다.
`mock_demo.py`는 시각·request_id를 자동 생성하고 임시 DB로 한 사이클을 검증한다.

기존 웹의 단일 위치 승인 UI를 새 컨트롤러에 연결하려면 **웹을 시작하는 프로세스**에
`YOLO_DEMO_CONTROLLER_URL=http://127.0.0.1:8010`을 설정한다.
기존 단일 파지 위치 이동만 연결되며 전체 박스 이송·그리퍼 조작을 덧붙이지 않는다.
Docker 웹에서는 127.0.0.1이 웹 컨테이너 자신을 가리킨다. 동일 네트워크의 컨트롤러 이름
또는 host.docker.internal처럼 실제 연결 가능한 주소를 사용해야 한다.

전체 피킹 API는 `/api/v1/pick`, 위치만 이동하는 API는 `/api/v1/move-target`이다.
`/api/v1/plan`은 명령 없이 경로만 확인한다. `/api/v1/jobs/{request_id}`로 결과를 조회한다.
한 로봇·한 서버 프로세스(`--workers 1`)만 사용한다. 다른 제어기·DART 작업과 동시에 구동하지 않는다.

기존 웹에 저장한 작업면을 읽기 전용으로 가져올 수 있다:

```powershell
../.venv312/Scripts/python import_web_calibration.py --web http://127.0.0.1:8000 --region legacy
$env:PICKING_CONFIG = (Resolve-Path config/imported.mock.json)
```

가져오기 결과도 MOCK이며 실물 검증 플래그를 켜지 않는다. 카메라 세션이 바뀌면 재동기화해야 한다.
현재 통합 피킹은 수평 작업면과 바닥 기준 상품 집기 높이를 사용한다.
설정의 camera/session/resolution/revision은 실제 감지 스트림과 일치해야 한다.
config의 class_map은 현장 학습 모델의 라벨과 맞춰야 한다.

실물은 `MOCK_MODE=false`만으로 작동하지 않는다. 현장 보정·TCP·웨이포인트·작업 공간·그리퍼
검증 및 상품별 실측 grasp_feedback이 필요하다. 기본 파일의 합성 보정·파지값을 실물용으로 쓰지 않는다.
`ros/warehouse_gripper`는 최신 레거시 수정본의 독립 복사본이며 provenance.json에 출처가 있다.
이식 복사본과 원본 패키지를 같은 colcon 작업공간에 중복 등록하지 않는다.
기존 모션 노드를 실행할 필요 없이, Jazzy 두산 드라이버와 이 그리퍼 플러그인을 함께 빌드한다.

읽어볼 문서: 상위 `deliverables/01-environment-report.md`, `02-legacy-assets.md`,
`03-pipeline-and-speech.md`, `04-runbook-and-validation.md`.
