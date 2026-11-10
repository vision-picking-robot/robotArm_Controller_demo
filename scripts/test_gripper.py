"""Standalone Gripper Diagnostic Script.
Verifies RH-P12-RN communication and physical motion in < 5 seconds.
"""
import argparse
import sys
import os
import json
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controller.gripper import GripperController


def main():
    parser = argparse.ArgumentParser(description="ROBOTIS RH-P12-RN Gripper 5-second Diagnostics")
    parser.add_argument("--mode", choices=["real", "mock"], default="mock", help="Communication mode (default: mock)")
    parser.add_argument("--port", default="COM3", help="Serial COM port for real hardware (default: COM3)")
    parser.add_argument("--baud", type=int, default=57600, help="Baud rate (default: 57600)")
    parser.add_argument("--timeout", type=float, default=5.0, help="Maximum timeout in seconds (default: 5.0)")
    args = parser.parse_args()

    print("=" * 65)
    print("  [Vision Lab] RH-P12-RN 그리퍼 단독 물리 동작 및 통신 검증 (5초 루틴)")
    print("=" * 65)
    print(f" * 실행 모드      : {args.mode.upper()}")
    print(f" * 통신 포트      : {args.port} @ {args.baud} bps")
    print(f" * 제한 시간      : {args.timeout}초")
    print("-" * 65)

    controller = GripperController(mode=args.mode, port=args.port, baudrate=args.baud)
    result = controller.run_self_test(timeout_sec=args.timeout)

    print("\n[진행 결과 단계별 요약]")
    for st in result.get("stages", []):
        detail = []
        if "position" in st:
            detail.append(f"위치: {st['position']} pulses")
        if "current_ma" in st:
            detail.append(f"전류: {st['current_ma']} mA")
        detail_str = f" ({', '.join(detail)})" if detail else ""
        print(f"  - [{st['stage']:<15}] 소요: {st['elapsed_s']:>4.2f}s | 상태: {st['status']}{detail_str}")

    print("-" * 65)
    if result.get("success"):
        print(f"  >>> SUCCESS: {result['message']}")
        print("=" * 65)
        return 0
    else:
        print(f"  >>> FAILURE: {result.get('error', 'Unknown error')}")
        print("=" * 65)
        return 1


if __name__ == "__main__":
    sys.exit(main())
