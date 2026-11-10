# macOS 로봇 LAN 설정

프로젝트 루트에서 [en7-ipv4.sh](./en7-ipv4.sh)를 실행해 Mac의 유선 인터페이스 `en7`을 고정 IPv4 주소 또는 DHCP로 전환합니다. 설정을 변경할 때 `sudo`가 관리자 암호를 요청할 수 있습니다.

## 로봇에 직접 연결할 때

```bash
./scripts/network/en7-ipv4.sh static
```

Mac의 주소를 `110.120.1.11`, 서브넷 마스크를 `255.255.255.0`으로 설정합니다. 로봇 직결용이므로 게이트웨이는 비워 둡니다. 실행 후 출력에서 IP 주소와 서브넷 마스크를 확인하세요.

## DHCP로 돌아갈 때

```bash
./scripts/network/en7-ipv4.sh dhcp
```

자동 주소 할당을 사용합니다. DHCP 서버가 있는 네트워크에 연결하면 새 주소를 받을 수 있습니다.

## en7 서비스를 찾을 수 없을 때

```bash
networksetup -listnetworkserviceorder
```

유선 어댑터를 연결하고 출력에서 `Device: en7`이 표시된 서비스가 하나만 활성화되어 있는지 확인하세요. 스크립트는 이 목록에서 서비스 이름을 찾으므로 어댑터 이름을 직접 입력할 필요가 없습니다.
