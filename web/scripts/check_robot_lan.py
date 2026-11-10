"""Check the Mac's wired connection to the external ROS 2 controller."""
import argparse
import ipaddress
import os
import socket
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.robot import RobotSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("rosbridge", "e0509"), default="rosbridge",
                        help="ROS 2 컨트롤러의 rosbridge(기본값) 또는 E0509 제어기의 TCP 포트")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    settings = RobotSettings.from_env()
    if args.target == "e0509":
        label, host, port = "E0509 제어기", settings.ip, settings.port
    else:
        label, host, port = "ROS 2 컨트롤러", settings.ros_pc_ip, settings.rosbridge_port
        if not host:
            print(".env의 ROBOT_ROS_PC_IP에 별도 ROS 2 컨트롤러의 유선 IP를 입력하세요.")
            return 1
    interface = os.getenv("ROBOT_LAN_INTERFACE", "").strip()
    if args.target == "rosbridge" and ipaddress.ip_address(host).is_loopback:
        interface = "lo0"
    if not interface:
        print(".env에서 ROBOT_LAN_INTERFACE에 Mac의 유선 인터페이스 이름을 설정하세요.")
        return 1
    if interface not in {name for _, name in socket.if_nameindex()}:
        print(f"{interface}: macOS에 유선 인터페이스가 없습니다. 어댑터 연결을 확인하세요.")
        return 1
    print(f"{label} TCP 테스트: {interface} → {host}:{port}", flush=True)
    # macOS nc -b pins the connection to this interface; -z opens and closes without payload.
    probe = subprocess.run(["/usr/bin/nc", "-n", "-z", "-v", "-G", "3", "-b", interface,
                            host, str(port)], capture_output=True, text=True)
    print((probe.stderr or probe.stdout).strip())
    return probe.returncode


if __name__ == "__main__":
    sys.exit(main())
