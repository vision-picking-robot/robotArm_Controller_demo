"use strict";
const $ = (id) => document.getElementById(id);
let currentStatus = null;

function clearObjects(message) {
  $("objects").replaceChildren();
  const li = document.createElement("li"); li.className = "empty"; li.textContent = message;
  $("objects").append(li); $("count").textContent = "—";
}
function updateStatus(status, label, reset) {
  if (reset) {
    clearObjects(status.ready ? "새 모델 결과 대기 중" : "현재 유효한 탐지 결과가 없습니다.");
    $("frame-id").textContent = "프레임 대기 중";
    $("placeholder").hidden = false;
    $("placeholder-title").textContent = status.ready ? "영상 수신 대기" : label;
    $("placeholder-detail").textContent = status.last_error || "유효한 카메라 프레임을 기다리고 있습니다.";
  }
  currentStatus = status;
  $("state").textContent = label;
  $("status-pill").classList.toggle("ready", Boolean(status.ready));
  $("error").hidden = !status.last_error; $("error").textContent = status.last_error || "";
  if (status.backend) $("backend").textContent = status.backend.toUpperCase();
  if (status.model) $("model").textContent = status.model;
  if($("active-version")) $("active-version").textContent = status.model_version || "";
  window.dispatchEvent(new CustomEvent("vision-status", {detail:status}));
  if (status.configuration) {
    const c=status.configuration;
    $("settings-values").textContent=`입력 ${c.imgsz} × ${c.imgsz} · 최소 confidence ${c.confidence} · NMS IoU ${c.iou} · 최대 ${c.max_det}개 객체 · 미리보기 최대 ${c.preview_fps} FPS`;
  }
  $("fps").textContent = status.fps?.detected?.toFixed(1) ?? "—";
  $("capture-fps").textContent = `카메라 ${status.fps?.captured?.toFixed(1) ?? "—"} / 미리보기 ${status.fps?.encoded?.toFixed(1) ?? "—"}`;
  $("latency").textContent = status.capture_to_result_ms?.p95?.toFixed(1) ?? "—";
  $("drops").textContent = `생략한 입력 ${status.input_dropped ?? 0}`;
  $("frame-age").textContent = status.frame_age_ms != null ? `최근 결과 입력 ${status.frame_age_ms.toFixed(0)} ms 전` : "결과 수신 대기";
  const size = status.camera?.actual_size;
  if(size) document.querySelector(".viewport").style.aspectRatio=`${size.width}/${size.height}`;
  $("resolution").textContent = size ? `${size.width} × ${size.height}` : "입력 대기 중";
}
function updateDetections(packet) {
  window.dispatchEvent(new CustomEvent("vision-detections", {detail:packet}));
  $("frame-id").textContent = `프레임 #${packet.frame_id}`;
  $("objects").replaceChildren();
  if (!packet.objects.length) clearObjects("감지된 객체가 없습니다.");
  $("count").textContent = String(packet.objects.length);
  for (const object of packet.objects) {
    const li = document.createElement("li"); li.className = "object";
    const details = document.createElement("div"); const label = document.createElement("strong");
    label.textContent = object.label;
    const coordinates = document.createElement("small"); coordinates.textContent = object.xyxy.map(Math.round).join(" · ");
    details.append(label, coordinates);
    const score = document.createElement("span"); score.className = "confidence"; score.textContent = `${(object.confidence * 100).toFixed(1)}%`;
    li.append(details, score); $("objects").append(li);
  }
}
import("./detection-feed.js").then(({ watchDetections }) => {
  function start() {
    return watchDetections({
      image: $("preview"), onStatus: updateStatus, onDetections: updateDetections,
      onPreview(visible) {
        $("placeholder").hidden = visible;
        if (!visible) $("placeholder-title").textContent = "영상 연결을 다시 시도합니다";
      }
    });
  }
  let stop = start();
  window.addEventListener("pagehide", () => stop());
  window.addEventListener("pageshow", event => { if (event.persisted) stop = start(); });
});
