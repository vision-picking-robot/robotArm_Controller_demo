# 추출 자산과 결함 격리 명세

수치 원본 전체는 `legacy-assets.json`, YAML 그대로의 이식본은
`robotArm_Controller_demo/config/poses.yaml`, `products.yaml`에 있다.
현재 소스의 `taught:true`와 실제 현장 재검증 완료는 구분한다. HANDOFF에는 과거 null 상태가
남아 있어 최신 YAML과 서로 다르며, 이번 추출은 현재 YAML을 기준으로 했다.

## 통신

로봇은 SDK/ROS 2 경유이며 임의 JSON이나 Modbus 패킷을 12345에 직접 보내지 않는다.
레거시 README의 host는 `<ROBOT_IP>` 자리표시자다. IP `110.120.1.63`은 YOLO 쪽 기본값이다.

| 인터페이스 | 서비스/토픽 | 타입·계약 |
|---|---|---|
| 관절/직선 모션 | `/dsr01/motion/move_joint`, `move_line` | dsr_msgs2/srv/MoveJoint, MoveLine; sync_type=0; 위치 mm/관절 deg |
| 기구학 | `/dsr01/motion/fkin` | Fkin, pos[6], ref → conv_posx[6] |
| 현재 좌표 | `/dsr01/aux_control/get_current_posx`, `get_current_posj` | GetCurrentPosx/GetCurrentPosj |
| 제어기 상태 | `/dsr01/system/get_robot_system`, `get_robot_mode`, `get_robot_state` | 실물 REAL=0, AUTO=1, STANDBY=1 확인 |
| 정지 | `/dsr01/motion/move_stop` | MoveStop; 신규 어댑터 stop_mode=1; 오류 시 요청하되 성공을 추정하지 않음 |
| 레거시 상위 동작 | `/warehouse/move_to_pose` | warehouse_interfaces/MoveToPose: pose_name → success,message |
| 레거시 집기 | `/warehouse/pick_place` | PickPlace: product_name,angle,box_slot → success,message |
| RFID | `/warehouse/rfid/uid`; `/warehouse/query_rfid_tag` | std_msgs/String; QueryRfidTag |
| 그리퍼 | `/dsr01/warehouse_gripper/{status,open,close}` | std_srvs/Trigger; 상태 문자열 position_pulses,current,moving,moving_status |
| 웹 관측 | `/dsr01/joint_states`, `/error`, `/robot_disconnection`, `/monitor/joint_states` | sensor_msgs/JointState, dsr_msgs2 타입; rosbridge 기본 9090 |

그리퍼는 RH-P12-RN-A, Tool Flange RS-485 Modbus RTU, 포트 1, ID 1, **57600/8N1**이다.
PC `/dev/ttyUSB*`나 Modbus TCP/502를 그리퍼 경로라고 가정하지 않는다.
플러그인은 두산 드라이버의 DRFL 연결을 공유한다. 새 TCP 소켓을 중복 생성하지 않는다.

| 명령 | 0-based Modbus 레지스터 | 검증된 소스의 패킷(hex, CRC 포함) |
|---|---:|---|
| 목표 전류 400 mA | FC06 275 | `01 06 01 13 01 90 78 0F` |
| 목표 열림 0 | FC16 282, 2워드 | `01 10 01 1A 00 02 04 00 00 00 00 7F 4C` |
| 목표 닫힘 740 | FC16 282, 2워드 | `01 10 01 1A 00 02 04 02 E4 00 00 3E C3` |
| 토크 ON | FC06 256 | `01 06 01 00 00 01 49 F6` |

쓰기 순서는 전류→목표 위치→토크 ON이다. 이전 목표를 먼저 실행하지 않도록 한다.
모드 5 및 하드웨어 오류 확인, CRC/슬레이브/기능/길이/에코 검증, 응답 타임아웃 1초,
포트 open 후 500 ms 대기, 최초 전압 읽기만 최대 3회 재시도, 구동 쓰기 자동 재시도 없음.
400 mA·740 펄스는 레거시 시험 설정이며 모든 상품에 적합하다는 뜻은 아니다.
웹 수동 그리퍼의 보수적 상한 700과 레거시 740을 같다고 취급하지 않는다.

## 기준 좌표·웨이포인트

기준 BASE, XYZ mm, ABC deg **ZYZ**. 아래 posx는 현재 YAML 기록 그대로다.

| 이름 | posx | posj(deg) |
|---|---|---|
| home | [373,0,285,12.44,180,12.44] | [0,0,90,0,90,0] |
| label_scan_approach | [-323,280,285,44.28,180,-135.72] | [139.08,8.40,80.99,0,90.61,-40.92] |
| label_scan | [-323,280,50,133.03,180,-46.97] | [139.08,19.13,105.95,0,54.91,-40.92] |
| via_point | [0,373,285,135,180,45] | [90,0,90,0,90,0] |
| box_slot 1 | [-373,0,85,3.26,180,-176.74] | [180,8.56,113.11,0,58.33,0] |
| box_slots 2–4 | null, taught:false | null |

상품 기준 XY/바닥 기준 집기높이: snack_box·snack_pepero [491.5,-5]/120,
pet_bottle·beverage_pet [450,50]/150, can·canned_meat [450,-50]/30,
wet_tissue·wet_wipes [450,-150]/30. doll 실물은 null/taught:false.
여러 항목 주석은 임시 좌표라고 명시하므로 `taught:true`만으로 실측 신뢰성을 확대하지 않는다.
비전 연결 후 신규 모듈은 XY를 보정된 영상 투영으로 대체하고 상품 높이만 재사용한다.

floor_z=0, samples=[-1.05], safety_margin=10, nfc_protrusion=0 → z_min=10 mm.
문서는 최소 4점 측정을 요구하지만 YAML 표본은 1개뿐이다. 현장 재측정 대상이다.
접근 오프셋 150 mm, 속도/가속도 코드 상한 30/30, 기본 blending radius=0.
관절 단위 deg/s·deg/s², 직선 단위 mm/s·mm/s²를 구분한다. 이는 프로젝트 제한값이며
제조사 관절 최대 스펙이 아니다. 신규 실행 기본값은 각각 10/10이다.

## 좌표 변환

레거시가 실제로 가진 것은 TOOL→BASE 상대이동용 회전식이다. `safe_move.py`의
`zyz_rotation`, `tool_offset_to_base`를 추출했다. `R=Rz(A) Ry(B) Rz(C)`이며:

```text
R = [ ca·cb·cc-sa·sc, -ca·cb·sc-sa·cc, ca·sb ]
    [ sa·cb·cc+ca·sc, -sa·cb·sc+ca·cc, sa·sb ]
    [       -sb·cc,          sb·sc,       cb ]
delta_base = R · delta_tool
```

카메라→BASE 실측 4×4 행렬 또는 호모그래피 계수는 검사한 저장소에 없다.
제품 YAML도 YOLO 호모그래피 연결 전 임시 XY라고 설명한다. 따라서 행렬 값을 발명하지 않았다.

YOLO `app/pick_plane.py`에는 4개 pixel↔BASE 대응점의 사영 변환 구현이 있다.
처음 세 점의 면적 기반 barycentric 가중치 w와 네 번째 점으로 정한 scale s를 사용해
`P_base = Σ(s_i w_i P_i) / Σ(s_i w_i)`로 투영한다. 볼록 영역·평면 잔차≤2 mm·카메라/해상도
일치를 검사하는 이 코드를 그대로 신규 모듈에 이식했다. 저장된 현장 대응점은 없다.

조직 참조 `rfid-pick-and-place/robot_rfid/vision.py`에는 RGB-D 식
`P_c=depth_m·K⁻¹[u,v,1]`, `P_b=T_b_c[P_c,1]`가 있다. 결과는 m, RPY는 rad다.
이를 Doosan mm/ZYZ deg로 직접 전달하면 안 된다. 실제 K/T가 없어 신규 실행 경로는
수평 작업면 4점 보정만 사용한다. 선반/손목 장착 카메라는 이 새 경로의 지원 범위 밖이다.

## 결함 격리와 확신 수준

1. **소스·기존 문서로 확인된 완료 판정 결함:** Modbus 285는 Moving(570)을 하위 바이트,
   Moving Status(571)를 상위 바이트에 함께 담는다. `0x3100` 전체를 moving으로 읽으면
   정지 상태를 12544로 보고 완료하지 못한다. 최신 레거시 `modbus.hpp`는 이미
   `moving=flags & 0xff`, `moving_status=flags >> 8`로 수정되어 있다. 새 모듈은 수정본만 이식했다.
2. **description 실행 코드 격리:** URDF·장치명·장착 방향만 참고하고 이 저장소의 그리퍼 노드는
   실행하지 않는다. `REG_PRESENT_POSITION=276`은 공식 현재 위치 580바이트/290워드와 다르다.
   해당 파서는 CRC/ID 및 온전한 32비트 위치를 검증하지 않고 0–700으로 clamp한다.
   쓰기 후 고정 sleep·토크 선행 순서도 새 제어 경로에서 제외했다.
3. 실물 실패가 전원·배선·포트·SDK 불일치·시리얼 점유 때문인지는 현장 로그 없이는 확정할 수 없다.
   `picking.launch.py`가 그리퍼 spawner를 시작하지 않는 사실과 `enable_motion:false` 기본값도
   '전혀 안 움직임'과 '완료 판정 실패'를 구분할 때 확인해야 한다.

공식 근거: [ROBOTIS 제어 테이블·장치 규격](https://emanual.robotis.com/docs/en/platform/rh_p12_rna/).
URDF `gripper_attach_joint`의 xyz=[0,0,0], rpy=[0,0,1.5708]은 장착 표현이다.
이 값을 실제 공구 TCP 오프셋이라고 사용하지 않았다. 외부 rh_p12_rn_a_description 의존 모델도
이 저장소만으로 전부 제공되지는 않는다.

이식 출처·SHA-256은 `robotArm_Controller_demo/provenance.json`에 있다. 레거시 제어 노드는 시작하지 않았다.
