import sys
import time
import numpy as np
import cv2
import pyrealsense2 as rs

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def main():
    print("=" * 65)
    print(" [Intel RealSense D455f 전용 진단 및 뷰어]")
    print("=" * 65)

    # 1. 장치 확인
    ctx = rs.context()
    devices = ctx.devices
    if len(devices) == 0:
        print("[오류] RealSense 카메라가 감지되지 않았습니다.")
        return

    dev = devices[0]
    dev_name = dev.get_info(rs.camera_info.name)
    serial_num = dev.get_info(rs.camera_info.serial_number)
    usb_type = dev.get_info(rs.camera_info.usb_type_descriptor)
    is_usb2 = "2." in usb_type

    print(f"장치명: {dev_name} (S/N: {serial_num})")
    print(f"USB 연결 타입: {usb_type}")

    # 창을 먼저 즉시 띄웁니다 (사용자가 창이 안 뜨는 현상 방지)
    window_name = f"Intel RealSense D455f Viewer ({dev_name})"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)

    # 파이프라인 설정
    pipe = rs.pipeline()
    cfg = rs.config()
    cfg.enable_device(serial_num)

    # 센서 스트림 활성화 시도
    has_video = False
    has_motion = False

    try:
        if is_usb2:
            # USB 2.1 모드에서는 가장 낮은 대역폭 스트림 시도
            cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        else:
            cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
            cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        
        # 모션 센서(IMU)도 함께 활성화
        cfg.enable_stream(rs.stream.accel)
        cfg.enable_stream(rs.stream.gyro)
        
        pipe.start(cfg)
        print("RealSense 파이프라인 시작 성공!")
    except Exception as e:
        print(f"[알림] 통합 스트림 설정 실패({e}), 단독 모션/기본 스트림으로 시작합니다.")
        try:
            pipe.stop()
        except:
            pass
        cfg = rs.config()
        cfg.enable_device(serial_num)
        cfg.enable_stream(rs.stream.accel)
        cfg.enable_stream(rs.stream.gyro)
        try:
            pipe.start(cfg)
        except Exception as e2:
            print(f"[오류] 모션 스트림 시작 실패: {e2}")

    colorizer = rs.colorizer()
    prev_time = time.time()
    fps = 0.0

    accel_data = [0.0, 0.0, 0.0]
    gyro_data = [0.0, 0.0, 0.0]
    last_frame_time = time.time()
    frame_received = False

    while True:
        # 안전한 논블로킹 프레임 폴링 (타임아웃 20ms)
        ok, frames = pipe.try_wait_for_frames(20)
        
        depth_img = None
        color_img = None

        if ok and frames:
            # 가속도/자이로 센서 데이터
            try:
                accel = frames.first_or_default(rs.stream.accel)
                if accel:
                    accel_data = accel.as_motion_frame().get_motion_data()
                gyro = frames.first_or_default(rs.stream.gyro)
                if gyro:
                    gyro_data = gyro.as_motion_frame().get_motion_data()
            except Exception:
                pass

            # 비디오 프레임
            df = frames.get_depth_frame()
            if df:
                depth_color_frame = colorizer.colorize(df)
                depth_img = np.asanyarray(depth_color_frame.get_data())
                frame_received = True
                last_frame_time = time.time()

            cf = frames.get_color_frame()
            if cf:
                color_img = np.asanyarray(cf.get_data())
                frame_received = True
                last_frame_time = time.time()

        # 화면 구성
        if depth_img is not None and color_img is not None:
            display = np.hstack((color_img, depth_img))
        elif depth_img is not None:
            display = depth_img.copy()
        elif color_img is not None:
            display = color_img.copy()
        else:
            # 비디오 프레임이 대기 중일 때 실시간 진단 HUD 화면 렌더링
            display = np.zeros((480, 720, 3), dtype=np.uint8)
            cv2.rectangle(display, (20, 20), (700, 460), (40, 40, 40), -1)
            cv2.rectangle(display, (20, 20), (700, 460), (0, 200, 255), 2)

            cv2.putText(display, f"Intel RealSense D455F Live Status", 
                        (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.putText(display, f"Serial Number: {serial_num}", 
                        (40, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
            cv2.putText(display, f"USB Connection: {usb_type}", 
                        (40, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255) if is_usb2 else (0, 255, 0), 2)

            # IMU 실시간 데이터 표시 (센서 통신 증명)
            ax = getattr(accel_data, 'x', 0.0) if hasattr(accel_data, 'x') else accel_data[0]
            ay = getattr(accel_data, 'y', 0.0) if hasattr(accel_data, 'y') else accel_data[1]
            az = getattr(accel_data, 'z', 0.0) if hasattr(accel_data, 'z') else accel_data[2]
            gx = getattr(gyro_data, 'x', 0.0) if hasattr(gyro_data, 'x') else gyro_data[0]
            gy = getattr(gyro_data, 'y', 0.0) if hasattr(gyro_data, 'y') else gyro_data[1]
            gz = getattr(gyro_data, 'z', 0.0) if hasattr(gyro_data, 'z') else gyro_data[2]

            cv2.putText(display, f"[Real-time IMU Motion Sensor (Active)]", 
                        (40, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 0), 2)
            cv2.putText(display, f"Accel (m/s^2): X={ax:.2f}, Y={ay:.2f}, Z={az:.2f}", 
                        (40, 215), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
            cv2.putText(display, f"Gyro  (rad/s): X={gx:.2f}, Y={gy:.2f}, Z={gz:.2f}", 
                        (40, 245), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

            # 안내 문구
            if is_usb2:
                cv2.putText(display, "! USB 2.0 Port/Cable Detected !", 
                            (40, 310), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 140, 255), 2)
                cv2.putText(display, "RealSense D455 requires USB 3.0 for video stream.", 
                            (40, 345), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
                cv2.putText(display, "Please plug into a blue USB 3.0 (SS) port with a USB 3 cable.", 
                            (40, 375), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (100, 220, 255), 1)

            cv2.putText(display, "Press 'q' or 'ESC' to exit", 
                        (40, 435), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (150, 150, 150), 1)

        cv2.imshow(window_name, display)

        key = cv2.waitKey(15) & 0xFF
        if key == ord('q') or key == 27:
            break

        try:
            if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                break
        except Exception:
            break

    pipe.stop()
    cv2.destroyAllWindows()
    print("프로그램 종료")

if __name__ == "__main__":
    main()
