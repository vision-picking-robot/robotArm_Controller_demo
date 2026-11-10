# A²-MEAS (`AutoLab-SAI-SJTU/A2MEAS`) 분석 및 경량 이식 가이드

이 문서는 [`feat/a2meas-extensions`](https://github.com/vision-picking-robot/robotArm_Controller_demo/tree/feat/a2meas-extensions) 브랜치에 포함된 **A²-MEAS 경량 확장 모듈**의 기술 검토 결과와 도입 방법을 정리한 문서입니다.

---

## 1. 원본 레포지토리(`AutoLab-SAI-SJTU/A2MEAS`) 적합성 검토 요약

| 항목 | A²-MEAS 원본 스펙 | 우리 프로젝트 (`robotArm_Controller_demo`) | 검토 결과 |
| :--- | :--- | :--- | :--- |
| **라이선스** | Apache-2.0 | 자체 연구/실습 프로젝트 | ✅ 상업/연구용 코드 발췌 및 수정 완전 허용 |
| **전체 모델 구동** | Linux 전용, CUDA 12.8/13, **96 GiB VRAM**, 250 GB 스토리지 | 실습용 노트북 (Windows / Ubuntu 24.04) | ❌ 전체 VLA + SAM 3.1 직접 구동은 하드웨어 한계로 부적합 |
| **대상 로봇 팔** | 양팔 ALOHA-Agilex (`arx5`, 14-DoF) / R1Pro (61-D) | 단팔 **Doosan E0509 (6-DoF) + RH-P12-RN** | ❌ 사전 학습된 가중치(Weights) 직접 호환 불가 |
| **핵심 알고리즘 이식** | NumPy 기반 PCA 파지 각도 추정, `ProgressGate`, 잔차 포즈 가드, 높이 Prior + Depth 융합 | Python 3.12 + FastAPI (`vision_picking`) | ✅ **순수 Python/NumPy 모듈로 경량 추출 및 통합 완료** |

---

## 2. 이 브랜치(`feat/a2meas-extensions`)에 구현된 3대 핵심 기능

원본 A²-MEAS 레포지토리에서 무거운 GPU/시뮬레이터 의존성을 완전히 제거하고, 우리 제어기(`vision_picking`)에서 즉시 사용할 수 있도록 [`vision_picking/a2meas_extensions.py`](../vision_picking/a2meas_extensions.py)에 3가지 핵심 모듈을 이식했습니다.

### ① 마스크/윤곽선 PCA 기반 그리퍼 회전각(Yaw) 자동 정렬
- **원본 출처**: `a2meas/robotwin/rsi/recovery.py` (`detections` 내 공분산 고유벡터 계산)
- **구현 함수**: `estimate_mask_orientation(mask)`, `align_gripper_yaw_deg(base_zyz_deg, orientation)`
- **역할**: 빼빼로 상자, 스팸 캔, 물티슈 등 길쭉한(`axis_ratio >= 1.6`) 상품이 작업대 위에 비스듬히 놓여 있을 때 2D 마스크의 주축 각도를 계산하여 두산 로봇 그리퍼의 ZYZ `C` 회전각을 `[-90°, +90°]` 범위 내 최단 회전으로 자동 보정합니다.

### ② 클래스 높이 사전지식(Height Prior) + RealSense Depth 이상치 방어 융합
- **원본 출처**: `a2meas/robotwin/rsi/recovery.py` (`point_at_height` + `_plan_regrasp` 스테레오 융합 조건)
- **구현 함수**: `fuse_height_and_depth(prior_xyz_mm, measured_xyz_mm, max_xy_deviation_mm=100.0, max_z_deviation_mm=60.0)`
- **역할**: 4점 호모그래피(`PickPlane`)와 상품별 높이(`config/products.yaml`)로 계산한 1차 좌표(`prior_xyz_mm`)와 RealSense Depth 카메라 실측 좌표(`measured_xyz_mm`)를 비교하여, 반사광/노이즈로 인한 Depth 이상치(편차 100mm 초과)는 자동 거부하고 정상 범위일 때만 정밀 3D 좌표를 채택합니다.

### ③ 작업 정체/실패 래치 상태 머신(`ProgressGate`) 및 포즈 보정 안전 가드(`guard_pose_correction`)
- **원본 출처**: `a2meas/behavior/correction/gate.py` (`ProgressGate`), `a2meas/behavior/correction/actions.py` (`guarded_actions`)
- **구현 클래스/함수**: `ProgressGate`, `guard_pose_correction(base_pose_mm_deg, proposed_pose_mm_deg)`
- **역할**:
  - 보정된 피킹 포즈가 기본 계획 포즈 대비 이동량 `30mm` 또는 회전량 `11.5° (0.20 rad)`를 초과하면 즉시 거부(`action_geometry_outside_local_scope`)합니다.
  - 동일 타겟에 대해 피킹 실패/정체(`no_progress`)가 누적되면 해당 타겟을 자동 차단(`blocked_problem`)하고, 작업자가 재승인하거나 새로운 타겟(`new_target`)이 2회 이상 확인될 때만 잠금을 해제합니다.

---

## 3. API 엔드포인트 및 테스트 방법

### REST API 호출 예시 (`POST /api/v1/a2meas/evaluate-pose`)
제어기 서버(`8011` 포트)에 아래 엔드포인트가 추가되어 있습니다:
```bash
curl -X POST http://127.0.0.1:8011/api/v1/a2meas/evaluate-pose \
  -H "Content-Type: application/json" \
  -d '{
    "base_pose": [400.0, 0.0, 120.0, 0.0, 180.0, 0.0],
    "proposed_pose": [408.0, 4.0, 120.0, 0.0, 180.0, 3.0],
    "prior_xyz_mm": [400.0, 0.0, 120.0],
    "measured_xyz_mm": [408.0, 4.0, 121.0]
  }'
```

### 단위 및 통합 테스트 실행
```bash
pytest tests/test_a2meas_extensions.py -q
```

---

## 4. 추후 `main` 브랜치에 도입(Merge)하는 방법

19:00 실습 이후 언제든 이 기능을 본선(`main`)에 합치고 싶을 때 아래 명령으로 즉시 병합할 수 있습니다:

```bash
git checkout main
git merge feat/a2meas-extensions
git push origin main
```
