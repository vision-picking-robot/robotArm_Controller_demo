# 19:00 실습실 빠른 시작

최신 설치·실물 검사·보정 위치는 [루트 README](README.md)를 따른다.
Ubuntu 24.04 / ROS 2 Jazzy / Python 3.12 기준이다.

1. clone 후 venv를 만들고 `python -m pip install -r requirements.txt`를 실행한다.
2. `python mock_demo.py`로 MOCK를 확인한다.
3. `python scripts/start_lab.py`로 MOCK 제어기 8011과 웹 8000을 함께 시작한다.
   Windows에서는 `start_lab_test.bat`도 같다. Ctrl+C로 이 런처가 만든 프로세스만 종료한다.
4. 단독 상태 검사는 `python scripts/test_gripper.py --mode mock --action status`다.
   실제 열기/닫기는 README의 ROS 플러그인 준비와 `--mode real --action open/close` 절차를 따른다.

IP 후보는 110.120.1.56~110.120.1.63이다. 8011은 노트북 HTTP이며 두산 DRFL은 12345다.
핑 응답으로 실물 모드나 구동을 자동 선택하지 않는다. COM3로 연결됐다고 가정하지 않는다.
음성 정지는 기존 데모 주문 일시정지이며 실물 긴급 정지와 동일하지 않다.
현재 소프트웨어가 안내하는 “집어” 응답만으로 실물 피킹이 실행됐다고 판단하지 않는다.
