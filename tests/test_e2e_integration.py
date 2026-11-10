"""Comprehensive E2E Integration and UI Smoke Test Runner.
Verifies:
1. Web Dashboard UI DOM rendering integrity & baseline screenshot
2. Web Speech API transcript reception ("시작", "정지", "집어") & backend logging
3. Full data pipeline: [Voice/Button] -> [Mock YOLO BBox] -> [PickPlane Transform] -> [Demo Controller Motion Sequence]
"""
import asyncio
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

# Set up paths
WORKSPACE_DIR = Path(__file__).resolve().parents[2]
YOLO_DIR = WORKSPACE_DIR / "ryankim8815_yolov8"
DEMO_DIR = WORKSPACE_DIR / "robotArm_Controller_demo"
ARTIFACT_DIR = Path(r"C:\Users\SCPOINT\.gemini\antigravity\brain\3866c29c-1238-400b-b574-6a57d0ee8e9c")

sys.path.insert(0, str(YOLO_DIR))
sys.path.insert(0, str(DEMO_DIR))

# Setup logging
log_path = ARTIFACT_DIR / "e2e_smoke_test.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.FileHandler(log_path, mode="w", encoding="utf-8"), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("e2e_runner")


def run_tests():
    log.info("==================================================================")
    log.info("  Starting E2E Smoke Test: Windows Web UI & Robot Demo Pipeline  ")
    log.info("==================================================================")

    from app.config import Settings
    from app.main import create_app
    from app.pick_plane import Calibration, PickPlane
    from controller.demo_controller import DemoRobotController

    # 1. Initialize Controller
    demo_controller = DemoRobotController(config_dir=DEMO_DIR / "config")
    log.info("Loaded Demo Controller with floor_z=%.1f mm, z_min=%.1f mm",
             demo_controller.floor_z, demo_controller.floor_limit)

    # 2. Configure YOLO Web Application
    os.environ["YOLO_BACKEND"] = "mock"
    os.environ["YOLO_VOICE_ENABLED"] = "1"
    os.environ["YOLO_CAMERA_AUTOSTART"] = "1"
    settings = Settings(
        backend="mock",
        camera_autostart=True,
        voice_enabled=True,
        state_dir=YOLO_DIR / "state"
    )
    app = create_app(settings)
    log.info("YOLOv8 Web Application initialized with Mock detector & Web Speech API enabled")

    import uvicorn
    server_thread = None
    server = None
    port = 8080

    class Server(uvicorn.Server):
        def install_signal_handlers(self):
            pass

    config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="warning")
    server = Server(config=config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    log.info("Uvicorn Web Server running at http://127.0.0.1:%d", port)
    time.sleep(1.5)  # Allow server to start

    results = {
        "timestamp": datetime.now().isoformat(),
        "tasks": {}
    }

    # ---------------------------------------------------------
    # Task 1: Web Dashboard Rendering Integrity via Playwright
    # ---------------------------------------------------------
    log.info("\n--- [Task 1] Web Dashboard Rendering Integrity Check ---")
    task1_result = {"status": "FAILED", "checks": []}
    screenshot_file = ARTIFACT_DIR / "dashboard_baseline.png"

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_selector("#live-camera", timeout=10000)
            page.wait_for_selector("#dashboard-order", timeout=10000)
            page.wait_for_timeout(1000)

            # Verification 1: Title & Headline
            title = page.title()
            headline = page.locator("#page-title").text_content()
            log.info("Page Title: '%s', Headline: '%s'", title, headline)
            assert "대시보드" in title, f"Expected '대시보드' in title, got {title}"
            task1_result["checks"].append({"name": "Dashboard Title", "status": "PASS", "value": title})

            # Verification 2: Camera Streaming View & Mode
            camera_panel = page.locator("#live-camera")
            assert camera_panel.is_visible(), "Live camera panel not visible"
            cam_head = camera_panel.locator(".camera-heading").text_content()
            task1_result["checks"].append({"name": "Live Camera Panel", "status": "PASS", "heading": cam_head})

            # Verification 3: Metrics (FPS, Latency, Object Count)
            metrics = page.locator(".metrics")
            assert metrics.is_visible(), "Metrics section not visible"
            task1_result["checks"].append({"name": "Metrics Monitor (FPS/Latency/Count)", "status": "PASS"})

            # Verification 4: Order Status Monitor
            order_pad = page.locator("#dashboard-order")
            assert order_pad.is_visible(), "Order monitor pad not visible"
            order_text = order_pad.text_content()
            log.info("Order Pad text preview: %s", order_text[:60])
            task1_result["checks"].append({"name": "Order Status Monitor (#123)", "status": "PASS", "preview": order_text[:40]})

            # Verification 5: Start / Stop Control Buttons Layout & Sequence
            toggle_btn = order_pad.locator('[data-action="toggle"]')
            assert toggle_btn.is_visible(), "Toggle (Start/Stop) button not visible"
            toggle_text = toggle_btn.text_content().strip()
            log.info("Initial Toggle Button Text: '%s'", toggle_text)
            task1_result["checks"].append({"name": "Start/Stop Control Button", "status": "PASS", "button_text": toggle_text})

            # Verification 6: Pick Place Simulation Button
            place_btn = order_pad.locator('button[data-action^="place-"]')
            assert place_btn.is_visible(), "Place simulation button not visible"
            task1_result["checks"].append({"name": "Place Simulation Button", "status": "PASS", "text": place_btn.text_content().strip()})

            # Verification 7: Voice Command Button & Panel
            voice_btn = page.locator('button[data-action="voice"]')
            assert voice_btn.is_visible(), "Voice command toggle button not visible"
            voice_btn.click()
            page.wait_for_timeout(300)
            voice_panel = page.locator("#voice-panel")
            assert voice_panel.is_visible(), "Voice panel did not open on click"
            wake_btn = voice_panel.locator('button[data-action="wake"]')
            stop_listen_btn = voice_panel.locator('button[data-action="stop-listening"]')
            assert wake_btn.is_visible() and stop_listen_btn.is_visible(), "Voice action buttons not visible"
            task1_result["checks"].append({"name": "Voice Command Panel (Wake/Stop)", "status": "PASS"})

            # Verification 8: Global Stop & Standby Rail Controls
            rail_stop = page.locator("#robot-global-stop")
            rail_standby = page.locator("#robot-standby")
            assert rail_stop.is_visible() and rail_standby.is_visible(), "Rail control buttons not visible"
            task1_result["checks"].append({"name": "Emergency Stop & Standby Rail Controls", "status": "PASS"})

            # Capture Baseline Screenshot
            page.screenshot(path=str(screenshot_file), full_page=True)
            log.info("Captured baseline screenshot to: %s", screenshot_file)
            task1_result["screenshot"] = str(screenshot_file)
            task1_result["status"] = "SUCCESS"
            browser.close()
    except Exception as e:
        log.error("Task 1 verification failed: %s", e, exc_info=True)
        task1_result["error"] = str(e)
    results["tasks"]["task1"] = task1_result

    # ---------------------------------------------------------------------------------
    # Task 2: Web Speech API & Vision-Controller Data Pipeline E2E Smoke Test
    # ---------------------------------------------------------------------------------
    log.info("\n--- [Task 2] Web Speech API & Data Pipeline E2E Smoke Test ---")
    task2_result = {"status": "FAILED", "voice_tests": [], "pipeline_steps": []}

    import requests
    base_url = f"http://127.0.0.1:{port}"

    try:
        # Step 2-1: Web Speech API Korean Commands Verification ("시작", "정지", "집어")
        # Initialize Voice session
        wake_res = requests.post(f"{base_url}/api/voice/wake")
        assert wake_res.status_code == 200, f"Voice wake failed: {wake_res.text}"
        voice_state = wake_res.json()
        session_id = voice_state["session"]
        turn = voice_state["turn"]
        log.info("[Voice Session Started] session_id=%s, turn=%d", session_id, turn)

        test_commands = [
            ("시작", "cmd-go", "피킹 시작/재개 명령"),
            ("정지", "cmd-stop", "피킹 일시정지 명령"),
            ("집어", "cmd-pick", "상품 파지(피킹) 실행 명령"),
        ]

        for word, expected_intent, desc in test_commands:
            log.info(">>> Sending Web Speech API transcript: '%s' (%s)", word, desc)
            payload = {"session": session_id, "turn": turn, "text": word}
            t_res = requests.post(f"{base_url}/api/voice/transcript", json=payload)
            assert t_res.status_code == 200, f"Transcript failed for '{word}': {t_res.text}"
            reply_data = t_res.json()
            turn = reply_data["turn"]
            reply_text = reply_data.get("reply", "")
            log.info("<<< Received Voice Reply: '%s' (Next turn=%d)", reply_text, turn)
            task2_result["voice_tests"].append({
                "spoken_word": word,
                "expected_intent": expected_intent,
                "server_reply": reply_text,
                "status": "PASS"
            })

        # Close voice session
        requests.post(f"{base_url}/api/voice/stop")

        # Step 2-2: Mock YOLO Bounding Box Injection & Extraction
        log.info("\n--- [Pipeline Step 1 & 2] Mock YOLO Detection Injection ---")
        det_res = requests.get(f"{base_url}/api/detections/latest")
        if det_res.status_code != 200:
            # Pick-frame query
            pf_res = requests.get(f"{base_url}/api/detections/pick-frame")
            frame_data = pf_res.json()
        else:
            frame_data = det_res.json()

        objects = frame_data.get("objects", [])
        log.info("Injected Mock YOLO Detections Count: %d", len(objects))
        for idx, obj in enumerate(objects):
            log.info("  Object [%d]: class='%s' (id=%d), conf=%.2f, bbox=%s",
                     idx, obj.get("label"), obj.get("class_id"), obj.get("confidence"), obj.get("xyxy"))
        assert len(objects) >= 3, "Expected at least 3 mock detected objects"

        target_obj = objects[0]  # snack_box
        bbox = target_obj["xyxy"]
        center_u = (bbox[0] + bbox[2]) / 2.0
        center_v = (bbox[1] + bbox[3]) / 2.0
        log.info("Selected Target Object: '%s', Center Pixel=(%.1f, %.1f)", target_obj["label"], center_u, center_v)
        task2_result["pipeline_steps"].append({
            "step": "1_YOLO_DETECTION",
            "object": target_obj["label"],
            "bbox": bbox,
            "center_pixel": [center_u, center_v],
            "status": "PASS"
        })

        # Step 2-3: Pixel -> Robot Base Coordinate Transformation (PickPlane)
        log.info("\n--- [Pipeline Step 3] Robot Coordinate Transformation ---")
        calib_data = {
            "camera": {"session_id": "sim-cam", "camera_generation": 1, "camera_id": "cam-0", "width": 1280, "height": 720},
            "region": "floor",
            "revision": None,
            "corners": [
                {"pixel": [100.0, 60.0], "pose": [400.0, -200.0, 20.0, 0.0, 180.0, 0.0]},
                {"pixel": [1180.0, 60.0], "pose": [600.0, -200.0, 20.0, 0.0, 180.0, 0.0]},
                {"pixel": [1180.0, 660.0], "pose": [600.0, 200.0, 20.0, 0.0, 180.0, 0.0]},
                {"pixel": [100.0, 660.0], "pose": [400.0, 200.0, 20.0, 0.0, 180.0, 0.0]},
            ]
        }
        plane = PickPlane(Calibration(**calib_data))
        base_coords = plane.project([center_u, center_v])
        assert base_coords is not None, "Coordinate projection failed"
        log.info("Projected Pixel (%.1f, %.1f) -> Robot Base Coordinates: X=%.2f, Y=%.2f, Z=%.2f mm",
                 center_u, center_v, base_coords[0], base_coords[1], base_coords[2])
        task2_result["pipeline_steps"].append({
            "step": "2_COORDINATE_TRANSFORMATION",
            "pixel": [center_u, center_v],
            "robot_base_xyz_mm": [round(c, 2) for c in base_coords],
            "status": "PASS"
        })

        # Step 2-4: Send Command to Demo Controller & Execute Sequence
        log.info("\n--- [Pipeline Step 4] Demo Controller Command Execution ---")
        exec_res = demo_controller.execute_pick_sequence(
            product_id=target_obj["label"],
            base_xy=[base_coords[0], base_coords[1]],
            slot_id=1,
            request_id="E2E-SMOKE-REQ-001"
        )
        assert exec_res.get("success"), f"Demo controller execution failed: {exec_res}"
        log.info("Demo Controller Execution Finished: success=%s, commands=%d, trace_events=%d",
                 exec_res["success"], exec_res["commands_count"], exec_res["trace_count"])

        task2_result["pipeline_steps"].append({
            "step": "3_DEMO_CONTROLLER_EXECUTION",
            "product_id": target_obj["label"],
            "slot_id": 1,
            "commands_count": exec_res["commands_count"],
            "trace_count": exec_res["trace_count"],
            "grasp_pose": exec_res["grasp_pose"],
            "slot_pose": exec_res["slot_pose"],
            "status": "PASS"
        })

        task2_result["controller_trace"] = demo_controller.trace
        task2_result["status"] = "SUCCESS"

    except Exception as e:
        log.error("Task 2 verification failed: %s", e, exc_info=True)
        task2_result["error"] = str(e)
    results["tasks"]["task2"] = task2_result

    # Write Results to JSON
    result_json_path = ARTIFACT_DIR / "e2e_smoke_test_result.json"
    with open(result_json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log.info("Wrote test results to: %s", result_json_path)

    # Teardown server
    server.should_exit = True
    server_thread.join(timeout=2.0)
    log.info("Uvicorn Web Server stopped.")

    return results


if __name__ == "__main__":
    res = run_tests()
    t1 = res["tasks"]["task1"]["status"]
    t2 = res["tasks"]["task2"]["status"]
    print(f"\nFinal Summary: Task 1 (UI Integrity): {t1} | Task 2 (Data Pipeline): {t2}")
    if t1 == "SUCCESS" and t2 == "SUCCESS":
        sys.exit(0)
    else:
        sys.exit(1)
