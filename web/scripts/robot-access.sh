#!/usr/bin/env bash
# Runs on the Mac. Docker is optional once the cached runtime is prepared.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ "$(uname -sm)" == "Darwin arm64" ]] || { echo "Apple Silicon Mac이 필요합니다." >&2; exit 1; }
cache=.cache/e0509-host-sdk
[[ -x "$cache/blink" && -x "$cache/linux/usr/local/bin/e0509-access-worker" ]] || {
  echo "먼저 bash rosbridge/host-sdk/prepare.sh 를 실행하세요." >&2; exit 1;
}
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  driver=$(docker compose --profile hardware ps --status running -q doosan-driver)
  [[ -z "$driver" ]] || { echo "먼저 docker compose stop doosan-driver 로 Docker SDK를 종료하세요." >&2; exit 1; }
fi
echo "Mac SDK 상태: http://127.0.0.1:8011/api/robot/host-access/status (종료: Ctrl+C)"
exec bash scripts/python.sh coreml -m app.robot_host_access
