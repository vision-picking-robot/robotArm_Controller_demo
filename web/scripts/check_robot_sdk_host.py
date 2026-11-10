"""One-shot E0509 SDK check on the Mac host. Does not require Docker at runtime."""
import argparse
import ipaddress
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache/e0509-host-sdk"


def lan_settings():
    keys = ("ROBOT_IP", "ROBOT_PORT", "ROBOT_LAN_INTERFACE")
    cfg = {}
    # .env.example is an installation template, never a runtime setting source.
    # Load only the three LAN settings; do not source or forward the rest of .env.
    f = ROOT / ".env"
    if f.is_file():
        for line in f.read_text().splitlines():
            k, _, v = line.partition("=")
            if k in keys:
                cfg[k] = v.strip().strip("\"'")
    cfg.update({k: os.environ[k] for k in keys if k in os.environ})
    return cfg


def lan_route(cfg):
    ip = str(ipaddress.IPv4Address(cfg["ROBOT_IP"]))
    port = int(cfg["ROBOT_PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("ROBOT_PORT 값이 유효하지 않습니다.")
    route = subprocess.run(["/sbin/route", "-n", "get", ip], capture_output=True, text=True, check=True)
    iface = re.search(r"^\s*interface:\s*(\S+)", route.stdout, re.MULTILINE)
    if not iface or iface[1] != cfg["ROBOT_LAN_INTERFACE"]:
        raise ValueError(f"{ip} 경로: {iface[1] if iface else '미확인'} · 유선 {cfg['ROBOT_LAN_INTERFACE']} 연결을 확인하세요.")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((ip, port))  # Route lookup; does not send a datagram.
        return {"interface": iface[1], "source_ip": sock.getsockname()[0], "ip": ip, "port": port}


def sdk_environment(cfg):
    return dict(BLINK_OVERLAYS=str(CACHE / "linux"), LD_LIBRARY_PATH="/opt/doosan/lib:/lib/x86_64-linux-gnu",
                PATH="/usr/bin:/bin", LANG="C", **{k: cfg[k] for k in ("ROBOT_IP", "ROBOT_PORT")})


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--request-access", action="store_true", help="정지 상태에서 일반 제어권을 한 번 요청한 뒤 종료")
    mode.add_argument("--force-request-access", action="store_true",
                      help="정지 상태에서 제어권을 이 SDK 세션으로 강제 인계 요청하고 결과 확인 후 연결 종료")
    mode.add_argument("--sdk-version", action="store_true", help="로봇에 접속하지 않고 SDK 버전만 확인")
    args = parser.parse_args()
    if not (CACHE / "blink").is_file() or not (CACHE / "linux/usr/local/bin/check-e0509").is_file():
        sys.exit("먼저 bash rosbridge/host-sdk/prepare.sh 를 실행하세요.")
    cfg = lan_settings()
    opts = []
    if args.sdk_version:
        opts.append("--sdk-version")
    else:
        try:
            route = lan_route(cfg)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            sys.exit(str(exc))
        print("Mac {interface}: {source_ip} → {ip}:{port}".format(**route), flush=True)
        if args.request_access:
            opts.append("--request-access")
        elif args.force_request_access:
            opts.append("--force-request-access")
    env = sdk_environment(cfg)
    try:
        return subprocess.run([str(CACHE / "blink"), "-m", "-L", "/dev/null",
                               "/usr/local/bin/check-e0509", *opts], env=env, timeout=25).returncode
    except subprocess.TimeoutExpired:
        sys.exit("SDK 진단 시간 초과. 검사기를 종료했습니다.")


if __name__ == "__main__":
    sys.exit(main())
