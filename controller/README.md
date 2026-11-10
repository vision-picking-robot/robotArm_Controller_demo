# 초기 초안 보존

이 디렉터리는 별도 작업에서 추가한 초기 초안을 이력 보존용으로 남긴 것이다.
실물 통신 구현·검증 대상이 아니다. gripper.py의 real 분기와 메모리 기반 피드백을
실제 Modbus 통신/파지 성공으로 사용하지 않는다.

정식 진입점은 `vision_picking.server:create_app`, 그리퍼 단독 검사는
`python scripts/test_gripper.py --mode mock --action status`다. 실물은 루트 README의
Jazzy shared-DRFL 플러그인 절차를 따른다.
