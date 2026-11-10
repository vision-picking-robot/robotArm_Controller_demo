// The bitmap is drawn with object-fit: contain. Ignore clicks in its letterbox.
export function imagePixel(rect, size, clientX, clientY) {
  const scale = Math.min(rect.width / size.width, rect.height / size.height);
  if (!scale) return null;
  const x = (clientX - rect.left - (rect.width - size.width * scale) / 2) / scale;
  const y = (clientY - rect.top - (rect.height - size.height * scale) / 2) / scale;
  return x >= 0 && y >= 0 && x < size.width && y < size.height ? [x, y] : null;
}

export async function api(path, method = "GET", body, signal) {
  const response = await fetch("/api/robot/" + path, {method, signal, cache: "no-store",
    headers: body ? {"Content-Type": "application/json"} : {}, body: body && JSON.stringify(body)});
  let data;
  try { data = await response.json(); } catch { throw new Error("로봇 웹 서버에 연결할 수 없습니다."); }
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : "입력한 좌표와 연결 상태를 확인하세요.");
    error.status = response.status;
    throw error;
  }
  return data;
}

export function center(obj) {
  const [x1, y1, x2, y2] = obj.xyxy;
  return [(x1 + x2) / 2, (y1 + y2) / 2];
}

export function marker(ctx, x, y, r, color) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = "#142d29";
  ctx.lineWidth = 2;
  ctx.stroke();
}

export function sameInput(packet, original) {
  return packet && original && ["session_id", "model_version", "camera_id", "camera_generation"].every(k => packet[k] === original[k])
    && packet.source_size.width === original.source_size.width && packet.source_size.height === original.source_size.height;
}

export async function readCalibration(packet, signal,region='legacy') {
  const data = await api("pick/calibration"+(region==='legacy'?'':'?region='+region), "GET", undefined, signal);
  const camera = data.camera;
  if (camera.camera_id !== packet.camera_id || camera.session_id !== packet.session_id ||
      camera.camera_generation !== packet.camera_generation || camera.width !== packet.source_size.width ||
      camera.height !== packet.source_size.height) throw new Error("카메라 입력이 변경되었습니다. 현재 영상을 다시 확인하세요.");
  return data;
}
