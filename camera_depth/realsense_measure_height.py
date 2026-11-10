import sys
import time
import math
import numpy as np
import cv2
import pyrealsense2 as rs

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 마우스 상호작용 전역 변수
pt_top = [320, 190]
pt_bottom = [320, 290]
selected_point = None

def mouse_callback(event, x, y, flags, param):
    global pt_top, pt_bottom, selected_point
    
    if event == cv2.EVENT_LBUTTONDOWN:
        # 상단 점 (초록색) 이동
        pt_top = [x, y]
    elif event == cv2.EVENT_RBUTTONDOWN:
        # 하단 점 (하늘색) 이동
        pt_bottom = [x, y]
    elif event == cv2.EVENT_MOUSEMOVE and (flags & cv2.EVENT_FLAG_LBUTTON):
        pt_top = [x, y]
    elif event == cv2.EVENT_MOUSEMOVE and (flags & cv2.EVENT_FLAG_RBUTTON):
        pt_bottom = [x, y]

def get_robust_distance(depth_frame, x, y, radius=2):
    """경계면 노이즈 방지를 위해 주변 영역의 유효 깊이 중앙값 추출"""
    w = depth_frame.get_width()
    h = depth_frame.get_height()
    dists = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                d = depth_frame.get_distance(nx, ny)
                if d > 0:
                    dists.append(d)
    if dists:
        return float(np.median(dists))
    return depth_frame.get_distance(x, y)

def main():
    print("=" * 65)
    print(" [Intel RealSense D455f 실시간 3D 높이 측정 프로그램]")
    print("=" * 65)

    ctx = rs.context()
    devices = ctx.devices
    if len(devices) == 0:
        print("[오류] 연결된 RealSense 카메라를 찾을 수 없습니다.")
        return

    dev = devices[0]
    dev_name = dev.get_info(rs.camera_info.name)
    serial_num = dev.get_info(rs.camera_info.serial_number)
    usb_type = dev.get_info(rs.camera_info.usb_type_descriptor)
    print(f"장치명: {dev_name} (S/N: {serial_num}, USB: {usb_type})")

    # USB 대역폭에 따른 해상도 자동 설정
    if "2." in usb_type:
        width, height, fps = 640, 480, 30
        print(f"[안내] USB 2.0 연결 감지 -> {width}x{height}@{fps}fps 로 최적화합니다.")
    else:
        width, height, fps = 848, 480, 30
        print(f"[안내] USB 3.0 연결 감지 -> {width}x{height}@{fps}fps 로 시작합니다.")

    # 기본 측정점 위치 초기화 (화면 중앙 상하)
    global pt_top, pt_bottom
    pt_top = [width // 2, height // 2 - 50]
    pt_bottom = [width // 2, height // 2 + 50]

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(serial_num)
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)

    profile = pipeline.start(config)

    # 카메라 고유 내부 파라미터(Intrinsics) 추출
    depth_stream = profile.get_stream(rs.stream.depth)
    intrinsics = depth_stream.as_video_stream_profile().get_intrinsics()
    print(f"카메라 Intrinsics 추출 완료 (fx={intrinsics.fx:.1f}, fy={intrinsics.fy:.1f})")

    colorizer = rs.colorizer()
    
    # 후처리 필터 (측정 정확도 및 노이즈 제거)
    spatial = rs.spatial_filter()
    temporal = rs.temporal_filter()

    window_name = f"RealSense 3D Height Measurement ({dev_name})"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(window_name, mouse_callback)

    print("\n[안내]")
    print(" - 마우스 [좌클릭]: 상단 측정점(초록) 이동")
    print(" - 마우스 [우클릭]: 하단 측정점(하늘색) 이동")
    print(" - 'q' 또는 'ESC' 키를 누르면 종료됩니다.\n")

    try:
        while True:
            ok, frames = pipeline.try_wait_for_frames(1000)
            if not ok or not frames:
                continue

            depth_frame = frames.get_depth_frame()
            if not depth_frame:
                continue

            # 필터 적용 및 depth_frame 변환
            filtered = spatial.process(depth_frame)
            filtered = temporal.process(filtered)
            filtered_depth = filtered.as_depth_frame()

            # 화면 렌더링용 컬러라이즈 이미지
            colorized_frame = colorizer.colorize(filtered_depth)
            depth_image = np.asanyarray(colorized_frame.get_data())

            u_top, v_top = pt_top[0], pt_top[1]
            u_bottom, v_bottom = pt_bottom[0], pt_bottom[1]

            # 깊이 거리값 취득 (노이즈 보정 필터링)
            dist_top = get_robust_distance(filtered_depth, u_top, v_top)
            dist_bottom = get_robust_distance(filtered_depth, u_bottom, v_bottom)

            height_y_cm = 0.0
            dist_3d_cm = 0.0
            measured = False

            if dist_top > 0 and dist_bottom > 0:
                # 2D 픽셀 + 깊이거리 -> 3차원 실좌표 (X, Y, Z) deproject
                point_top = rs.rs2_deproject_pixel_to_point(intrinsics, [u_top, v_top], dist_top)
                point_bottom = rs.rs2_deproject_pixel_to_point(intrinsics, [u_bottom, v_bottom], dist_bottom)

                # 1) 순수 수직 높이 (|Y축 차이|)
                height_y_cm = abs(point_bottom[1] - point_top[1]) * 100

                # 2) 3차원 직선 유클리드 거리 (기울기 반영)
                dx = point_bottom[0] - point_top[0]
                dy = point_bottom[1] - point_top[1]
                dz = point_bottom[2] - point_top[2]
                dist_3d_cm = math.sqrt(dx*dx + dy*dy + dz*dz) * 100
                measured = True

            # ---------------- 시각화 드로잉 ----------------
            # 두 점을 잇는 측정선
            cv2.line(depth_image, (u_top, v_top), (u_bottom, v_bottom), (0, 255, 255), 2)

            # 상단 점 (초록색)
            cv2.circle(depth_image, (u_top, v_top), 7, (0, 255, 0), -1)
            cv2.circle(depth_image, (u_top, v_top), 10, (255, 255, 255), 1)
            cv2.putText(depth_image, f"TOP ({dist_top:.2f}m)", (u_top + 15, v_top - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)

            # 하단 점 (하늘색)
            cv2.circle(depth_image, (u_bottom, v_bottom), 7, (255, 200, 0), -1)
            cv2.circle(depth_image, (u_bottom, v_bottom), 10, (255, 255, 255), 1)
            cv2.putText(depth_image, f"BOTTOM ({dist_bottom:.2f}m)", (u_bottom + 15, v_bottom + 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 200, 0), 2)

            # 상단 HUD 정보창
            h, w, _ = depth_image.shape
            overlay = depth_image.copy()
            cv2.rectangle(overlay, (0, 0), (w, 85), (25, 25, 25), -1)
            cv2.addWeighted(overlay, 0.7, depth_image, 0.3, 0, depth_image)

            if measured:
                # 메인 측정값
                main_val = f"Height: {height_y_cm:.1f} cm"
                cv2.putText(depth_image, main_val, (20, 42),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 0), 3)

                sub_val = f"3D Distance: {dist_3d_cm:.1f} cm | Top Z: {dist_top:.2f}m | Bottom Z: {dist_bottom:.2f}m"
                cv2.putText(depth_image, sub_val, (20, 72),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.52, (220, 220, 220), 1)
            else:
                cv2.putText(depth_image, "Height: Out of range / Shadow (Click points on object)",
                            (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 100, 255), 2)

            # 하단 조작 가이드
            guide = "Left Click: Move TOP | Right Click: Move BOTTOM | 'q': Exit"
            cv2.putText(depth_image, guide, (15, h - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200, 200, 200), 1)

            cv2.imshow(window_name, depth_image)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:
                break

            try:
                if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except Exception:
                break

    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
        print("[종료] 측정이 완료되었습니다.")

if __name__ == "__main__":
    main()
