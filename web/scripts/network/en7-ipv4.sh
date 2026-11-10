#!/usr/bin/env bash
# macOS 로봇 LAN 주소 전환. 변경할 때만 sudo를 사용합니다.
set -euo pipefail
export LC_ALL=C

if [[ $# -ne 1 || ( $1 != static && $1 != dhcp ) ]]; then
  echo "사용법: $0 static|dhcp" >&2
  echo "  static  en7에 110.120.1.11/24 설정" >&2
  echo "  dhcp    en7에 DHCP 설정" >&2
  exit 2
fi

# networksetup은 장치 이름 대신 현재 네트워크 서비스 이름을 받습니다.
service=$(networksetup -listnetworkserviceorder | awk '
  /, Device: en7\)$/ && previous ~ /^\([0-9]+\) / {
    sub(/^\([0-9]+\) /, "", previous)
    print previous
  }
  { previous = $0 }
')
if [[ -z "$service" || "$service" == *$'\n'* ]]; then
  echo "en7의 활성 네트워크 서비스를 하나로 확인할 수 없습니다. networksetup -listnetworkserviceorder로 확인하세요." >&2
  exit 1
fi

if [[ $1 == static ]]; then
  # 로봇 직결 LAN이므로 라우터 주소는 비워 둡니다.
  sudo /usr/sbin/networksetup -setmanual "$service" 110.120.1.11 255.255.255.0 ""
else
  sudo /usr/sbin/networksetup -setdhcp "$service"
fi
networksetup -getinfo "$service"
