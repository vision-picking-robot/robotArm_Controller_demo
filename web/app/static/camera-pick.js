import { connectPickJob, renderRoute } from "./pick-job.js";
import { describeGripper } from "./gripper-measurement.js";
import { imagePixel, api, center, marker, sameInput, readCalibration } from "./robot-camera-frame.js";
export { imagePixel } from "./robot-camera-frame.js";

const axes = ["X", "Y", "Z", "A", "B", "C"];
const units = ["mm", "mm", "mm", "°", "°", "°"];

export function connectCameraPick(panel) {
  const el = Object.fromEntries([...panel.querySelectorAll("[data-pick]")].map(node => [node.dataset.pick, node]));
  const lifetime = new AbortController();
  const on = (node, type, handler) => node.addEventListener(type, handler, {signal: lifetime.signal});
  let live = null, frozen = null, selected = -1;
  let approval, expiryTimer, pending = false, disposed = false;
  const job = connectPickJob(el, api, discovered => {
    controls();
    if (discovered && el.dialog.open) el.dialog.close();
  }, lifetime.signal);
  function controls() {
    el.select.disabled = !live?.packet.objects.length || job.busy;
  }

  function changeMode(next) {
    el.title.textContent = next === "candidates" ? "파지 대상 선택" : "파지 위치 승인";
    for (const key of ["candidates", "confirmation"]) el[key].hidden = key !== next;
    el.preview.hidden = next !== "candidates";
    el.approve.hidden = next !== "confirmation";
    el.note.textContent = "";
  }

  function draw() {
    if (!frozen?.photo) return;
    const {packet, photo} = frozen, canvas = el.photo;
    canvas.width = packet.source_size.width; canvas.height = packet.source_size.height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(photo, 0, 0, canvas.width, canvas.height);
    const radius = Math.max(5, canvas.width / 150);
    if (selected < 0) return;
    const obj = packet.objects[selected], [x1,y1,x2,y2] = obj.xyxy;
    ctx.strokeStyle = "#80ffd0"; ctx.lineWidth = 4; ctx.strokeRect(x1,y1,x2-x1,y2-y1);
    marker(ctx,...center(obj),radius,"#ffcf61");
  }

  function candidates(indices) {
    el.choices.replaceChildren(...indices.map(index => {
      const obj = frozen.packet.objects[index];
      return new Option(obj.label + " · " + Math.round(100 * obj.confidence) + "% · 중심 " +
        center(obj).map(Math.round).join(", ") + " px", String(index));
    }));
    selected = Number(el.choices.value);
    draw();
  }

  async function preview(notice = "") {
    if (pending || !frozen) return;
    const snapshot = frozen;
    pending = true;
    clearTimeout(expiryTimer);
    if (approval) void cancelId(approval);
    approval = null;
    el.approve.disabled = el.handling.disabled = el.jobMode.disabled = el.preview.disabled = el.choices.disabled = true;
    el.note.textContent = "목표 좌표를 계산하고 있습니다.";
    try {
      const packet = snapshot.packet;
      const result = await api("pick/preview", "POST", {session_id: packet.session_id,
        frame_id: packet.frame_id, object_index: selected, mode:el.jobMode.value,
        handling:el.jobMode.value === "transfer" ? el.handling.value : "manual",
        scenario:el.jobMode.value === 'transfer'&&el.handling.value==='rh_p12'});
      if (frozen !== snapshot) { void cancelId(result.approval_id); return; }
      approval = result.approval_id;
      changeMode("confirmation");
      el.object.textContent = result.label + " · 신뢰도 " + Math.round(result.confidence * 100) + "%";
      el.confirmationSizing.textContent = describeGripper(result.gripper);
      el.target.replaceChildren(...result.pose.map((v, i) => {
        const cell = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
        dt.textContent = "BASE " + axes[i]; dd.textContent = v.toFixed(2) + " " + units[i];
        cell.append(dt, dd); return cell;
      }));
      renderRoute(el,result);
      el.handlingRow.hidden = result.mode !== "transfer";
      el.handLink.hidden = !result.hand_error;
      el.handTarget.textContent = result.handling === "rh_p12" ? "RH-P12 선행 벌림 명령 위치 · "+(result.hand_position ?? "계산 불가") : "";
      el.approve.disabled = Boolean(result.motion_reason);
      el.note.textContent = [notice, result.motion_reason || (result.scenario ? "17단계 경로입니다. 파지 확인과 박스에서 열기를 각각 승인해야 다음 단계로 진행합니다." : result.handling === "rh_p12" ? "권장 폭으로 벌린 후 대상 재확인 · 파지 위치 이동 · 닫기를 실행합니다." : "첫 이동은 파지 위치까지 10 mm/s입니다. 경로를 확인하세요.")].filter(Boolean).join(" ");
      expiryTimer = setTimeout(() => {
        el.approve.disabled = true; el.note.textContent = "승인이 만료되었습니다. 대상을 다시 선택하세요.";
      }, result.expires_in * 1000);
    } catch (error) {
      if (frozen === snapshot) el.note.textContent = error.message;
    } finally {
      if (frozen === snapshot) { pending = false; el.jobMode.disabled = el.handling.disabled = el.preview.disabled = el.choices.disabled = false; }
    }
  }

  async function open(next, indices = []) {
    if (!live || el.dialog.open || job.busy) return;
    const snapshot = frozen = {...live};
    el.handling.value = "manual"; el.jobMode.value = "pick"; pending = false; approval = null; selected = -1;
    changeMode(next);
    el.preview.disabled = true; el.choices.disabled = true; el.approve.disabled = true;
    el.photo.width = snapshot.packet.source_size.width; el.photo.height = snapshot.packet.source_size.height;
    el.dialog.showModal();
    const url = URL.createObjectURL(snapshot.jpeg);
    try {
      const photo = new Image(); photo.src = url; await photo.decode();
      if (frozen !== snapshot) return;
      snapshot.photo = photo;
      candidates(indices);
      el.choices.disabled = false; el.preview.disabled = false;
      if (indices.length === 1) await preview();
    } catch (error) {
      if (frozen === snapshot) el.note.textContent = error.message;
    } finally { URL.revokeObjectURL(url); }
  }

  on(el.select, "click", () => void open("candidates", live.packet.objects.map((_, i) => i)));
  on(el.overlay, "click", event => {
    if (!live || job.busy) return;
    const pixel = imagePixel(el.overlay.getBoundingClientRect(), live.packet.source_size, event.clientX, event.clientY);
    if (!pixel) return;
    const [x, y] = pixel;
    const hits = live.packet.objects.flatMap((obj, i) => {
      const [x1, y1, x2, y2] = obj.xyxy;
      return x >= x1 && x <= x2 && y >= y1 && y <= y2 ? [i] : [];
    });
    if (hits.length) void open("candidates", hits);
  });
  on(el.jobMode, "change", () => { el.handling.value="manual"; void preview(); });
  on(el.handling, "change", () => void preview());
  on(el.choices, "change", () => { selected = Number(el.choices.value); draw(); });
  on(el.preview, "click", () => void preview());

  async function cancelId(id) {
    try { await api("pick/cancel", "POST", {approval_id: id}); } catch { /* Also expires on the server. */ }
  }
  function resetDialog() {
    if (approval) void cancelId(approval);
    clearTimeout(expiryTimer); approval = null; frozen = null; pending = false;
  }
  on(el.cancel, "click", () => el.dialog.close());
  on(el.dialog, "close", resetDialog);

  on(el.approve, "click", async () => {
    if (!approval || el.approve.disabled || pending) return;
    const id = approval, snapshot = frozen;
    clearTimeout(expiryTimer);
    approval = null; pending = true;
    el.approve.disabled = el.handling.disabled = el.jobMode.disabled = true;
    job.reserve(id);
    el.note.textContent = "현재 영상에서 선택한 대상을 확인합니다.";
    try {
      await api("pick/approve", "POST", {approval_id: id});
    } catch (error) {
      if (disposed) return;
      if (error.status >= 400 && error.status < 500) {
        job.rejected();
        if (frozen === snapshot) {
          pending = false; el.jobMode.disabled = el.handling.disabled = false;
          if (error.status === 412) {
            await preview(error.message);
            return;
          }
          el.note.textContent = error.message;
        }
        el.status.textContent = error.message;
        return;
      }
      el.status.textContent = "이동 결과 미확인 · " + error.message;
    }
    if (disposed) return;
    if (el.dialog.open && frozen === snapshot) el.dialog.close();
    job.follow();
  });

  // Read the revision for the displayed input, never once per video frame.
  let badgeInput = null, nextLookup = 0, badgeRequest;
  function badge(label) {
    if (el.planeLink.textContent !== label) el.planeLink.textContent = label;
    el.planeLink.setAttribute("aria-label", "작업 평면 보정 · "+label);
  }
  function updateBadge(packet) {
    if (!packet?.camera_id) {
      badgeRequest?.abort(); badgeInput = null; nextLookup = 0;
      badge("영상 대기 · 작업 평면 보정");
      return;
    }
    if (!sameInput(packet,badgeInput)) {
      badgeRequest?.abort(); nextLookup = 0; badgeInput = packet;
      badge("보정 상태 확인 중");
    }
    if (Date.now() < nextLookup) return;
    nextLookup = Infinity;
    const request = badgeRequest = new AbortController();
    readCalibration(packet,request.signal).then(data => {
      if (!request.signal.aborted) badge(data.revision ? "보정됨 · 보정 보기" : "미보정 · 보정하기");
    }).catch(() => {
      if (!request.signal.aborted) badge("상태 확인 불가 · 보정 보기");
    }).finally(() => { if (!request.signal.aborted) nextLookup = Date.now()+10000; });
  }

  function update(packet, jpeg) {
    updateBadge(packet);
    live = packet?.display_mode === "detections" && jpeg ? {packet, jpeg} : null;
    if (frozen && !sameInput(live?.packet, frozen.packet)) {
      el.dialog.close(); resetDialog(); el.status.textContent = "카메라 입력이 변경되어 선택을 닫았습니다.";
    }
    el.overlay.hidden = !live;
    controls();
    if (!live) return;
    const canvas = el.overlay, {width, height} = packet.source_size;
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, width, height);
    for (const obj of packet.objects) marker(ctx, ...center(obj), Math.max(4, width / 220), "#a0ffcf");
    if (el.status.textContent === "탐지 영상 대기 중") el.status.textContent = "영상에서 파지할 객체를 선택하세요.";
  }
  return {update, dispose() {
    disposed = true; badgeRequest?.abort(); lifetime.abort();
    if (el.dialog.open) el.dialog.close();
    resetDialog();
  }};
}
