const get = id => document.getElementById(id);
const fields = [];
for (let i = 1; i <= 6; i++) {
  const label = document.createElement("label");
  label.textContent = `J${i} (°)`;
  const input = document.createElement("input");
  input.type = "number";
  input.step = "any";
  input.required = true;
  input.placeholder = "—";
  input.addEventListener("input", () => {
    drafts.set(slot, {revision: editingRevision, values: fields.map(field => field.value)});
    show();
  });
  label.append(input);
  get("pose-angles").append(label);
  fields.push(input);
}

let slot = 1;
let editingRevision = null;
let tests = null;
let config = null;
let live = false;
let host = null;
let pending = false;
let transferring = false;
let confirmation = null;
const drafts = new Map();

function feedback(message = "") {
  get("pose-feedback").textContent = message;
  get("pose-feedback").hidden = !message;
}

function show() {
  if (!tests) return;
  const pose = tests.poses[slot - 1];
  const draft = drafts.get(slot);
  editingRevision = draft ? draft.revision : pose.revision;
  fields.forEach((field, i) => {
    field.min = -tests.limits[i];
    field.max = tests.limits[i];
    // Leave native number inputs alone during typing, including an unfinished minus sign.
    if (!draft) field.value = pose.angles?.[i] ?? "";
    field.disabled = pending;
  });
  const conflict = draft && draft.revision !== pose.revision;
  get("pose-saved").textContent = conflict ? "저장값 변경됨 · 다시 불러오세요" :
    draft ? "편집 중" : pose.angles ? "저장됨" : "관절각 6개를 입력하세요.";
  get("pose-reload").hidden = !draft;
  get("pose-save").disabled = pending || tests.busy || !draft || conflict;
  get("pose-execute").disabled = transferring || pending || tests.busy || !live || !pose.angles || !!draft;
  const mac = config.access_backend === "mac-sdk";
  const stale = confirmation && (confirmation.backend !== config.access_backend ||
    confirmation.slot !== slot || confirmation.revision !== pose.revision ||
    (mac && (confirmation.session_id !== host?.session_id || confirmation.motion_token !== host?.motion_token)));
  get("pose-confirm").disabled = get("pose-execute").disabled || !confirmation || stale || (mac && !host?.can_move);
  get("pose-readiness").textContent = stale ? "세션 또는 저장값이 변경되었습니다. 창을 닫고 다시 확인하세요." :
    mac ? host?.motion_reason || (host?.can_move ? "자동 모드 · STANDBY" : "SDK 상태 확인 대기") : "";
  if (mac && live) {
    get("pose-bridge").textContent = draft ? "편집한 관절각을 먼저 저장하세요." :
      !pose.angles ? `자세 ${slot}에 관절각 6개를 먼저 저장하세요.` :
      host.motion_reason || "Mac SDK 제어권 보유 · 이동 준비 완료";
  }
  if (transferring) get("pose-bridge").textContent = "택배 박스 이송 작업 진행 중";
  for (const button of document.querySelectorAll("[data-pose]")) {
    button.disabled = pending;
    button.setAttribute("aria-pressed", String(Number(button.dataset.pose) === slot));
  }
  const last = tests.last;
  get("pose-last").textContent = last ? `자세 ${last.slot} · ${new Date(last.updated_at).toLocaleTimeString("ko-KR")} · ${last.message}` : "실행 기록이 없습니다.";
  get("pose-last").dataset.state = last?.state || "";
}

export function renderPoseTests(data, {paused, apiUnavailable, teachPending, hostAccess}) {
  tests = data.pose_tests;
  transferring = Boolean(data.transfer_active);
  config = data.config;
  host = hostAccess;
  if (config.access_backend === "mac-sdk") {
    live = !apiUnavailable && !paused && host?.connection === "connected" && host.initialized && host.held && !!host.motion_token;
    get("pose-bridge").textContent = paused ? "표시 일시정지" :
      host?.motion_reason || (host?.held ? "Mac SDK 작업자의 자세 이동 지원을 확인하세요." : "Mac SDK 연결 및 제어권 확인 대기");
    show();
    return;
  }
  const names = data.topics[0]?.message?.name || [];
  live = !apiUnavailable && !paused && !data.error && !teachPending && !data.direct_teach?.pending &&
    data.direct_teach?.mode === "auto" && data.state === "live" && names.length === 6 &&
    Array.from({length:6}, (_, i) => `joint_${i + 1}`).every(name => names.includes(name));
  const label = apiUnavailable ? "서버 응답 없음" : paused ? "표시 일시정지" :
    data.direct_teach?.mode === "manual" ? "직접 교시 허용 중 · 자세 이동 차단" :
    data.state === "monitoring" ? "읽기 전용 관절 수신 · 모션 드라이버 대기" :
    live ? "rosbridge 연결 · 6축 수신 중" : data.bridge_connected ? "rosbridge 연결 · 관절 상태 확인 필요" : "rosbridge 연결 대기";
  get("pose-bridge").textContent = label;
  show();
}

for (const button of document.querySelectorAll("[data-pose]")) {
  button.addEventListener("click", () => {
    slot = Number(button.dataset.pose);
    const draft = drafts.get(slot);
    if (draft) fields.forEach((field, i) => { field.value = draft.values[i]; });
    feedback();
    show();
  });
}
get("pose-reload").addEventListener("click", () => { drafts.delete(slot); feedback(); show(); });

async function submit(path, method, body, saving) {
  pending = true;
  feedback();
  show();
  try {
    const response = await fetch(path, {method, headers: {"Content-Type":"application/json"}, body: JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "관절각의 단위와 범위를 확인하세요.");
    tests = result;
    if (saving) {
      drafts.delete(slot);
      feedback(`자세 ${slot}을 저장했습니다.`);
    }
  } catch (error) {
    feedback(error.message || "서버 응답을 확인하세요.");
  } finally {
    pending = false;
    show();
  }
}

get("pose-form").addEventListener("submit", event => {
  event.preventDefault();
  if (get("pose-save").disabled || !get("pose-form").reportValidity()) return;
  void submit(`/api/robot/poses/${slot}`, "PUT", {revision: editingRevision, angles: fields.map(f => f.valueAsNumber)}, true);
});
get("pose-execute").addEventListener("click", () => {
  if (get("pose-execute").disabled) return;
  const pose = tests.poses[slot - 1];
  const mac = config.access_backend === "mac-sdk";
  confirmation = {slot, revision: pose.revision, backend: config.access_backend};
  if (mac) Object.assign(confirmation, {session_id: host.session_id, motion_token: host.motion_token});
  get("pose-confirm-title").textContent = `E0509을 자세 ${slot}로 이동할까요?`;
  get("pose-confirm-target").textContent = mac ?
    `Mac SDK · ${host.route.ip}:${host.route.port} · 세션 ${host.session_id.slice(0, 8)} · 절대 관절각` : `/${config.namespace}/motion/move_joint · 절대 관절각`;
  get("pose-confirm-angles").textContent = pose.angles.map((v, i) => `J${i + 1} ${v}°`).join("  ·  ");
  get("pose-confirm-speed").textContent = `속도 ${tests.speed} °/s · 가속도 ${tests.acceleration} °/s²`;
  show();
  get("pose-dialog").showModal();
});
get("pose-cancel").addEventListener("click", () => get("pose-dialog").close());
get("pose-confirm").addEventListener("click", () => {
  if (get("pose-confirm").disabled || !confirmation) return;
  const request = confirmation;
  get("pose-dialog").close();
  const {revision, session_id, motion_token} = request;
  void submit(`/api/robot/poses/${request.slot}/execute`, "POST", {revision, session_id, motion_token}, false);
});
get("pose-dialog").addEventListener("close", () => { confirmation = null; });
