# 납품 파일

1. [환경 확정 리포트](01-environment-report.md)
2. [추출 자산·웨이포인트·변환·결함 분석](02-legacy-assets.md), [기계 판독 명세](legacy-assets.json)
3. [웹/음성/페이로드 계약](03-pipeline-and-speech.md)
4. [실행 방법·검증·현장 인수인계](04-runbook-and-validation.md)

소스는 `../ryankim8815_yolov8/app/voice.py`, `app/static/browser-voice.js`,
`app/picking_integration.py`, `../robotArm_Controller_demo/vision_picking/`에 있다.
제어 모듈 사용법은 `../robotArm_Controller_demo/README.md`를 참조한다.

원본 커밋·빌드 설정은 source-audit.json, 최종 파일 조사는 source-audit.final.json,
이식 파일 출처는 ../robotArm_Controller_demo/provenance.json,
HTML/CSS 원형 해시는 ui-baseline.json에 보관한다.

기본값은 MOCK다. 실물 파지 성공·전체 회귀 통과를 의미하지 않는다.
