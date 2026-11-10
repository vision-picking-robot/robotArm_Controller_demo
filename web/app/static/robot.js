import {connectWorkPlane} from "./robot-work-plane.js";
import {currentDestination} from "./station-nav.js";
import {loadHandTable,renderHand} from "./robot-gripper.js";
import {renderPoseTests} from "./robot-poses.js";
import {renderWorkPoints} from "./robot-work-points.js";

const get = id => document.getElementById(id);
const states = {
  unconfigured: "수신 설정 대기", offline: "브리지 연결 대기", waiting: "관절 수신 대기",
  no_publisher: "관절 발행자 없음", live: "관절 수신 중", monitoring: "관절 수신 중 · 읽기 전용",
  stale: "관절 수신 지연", robot_disconnected: "로봇 연결 끊김"
};
const topicStates = {waiting: "대기", no_publisher: "발행자 없음", unavailable: "구독 오류", live: "수신", stale: "지연", listening: "이벤트 대기", received: "이벤트 수신"};
let selectedTopic = null;
let topicChosen = false;
let paused = false;
let latest = null;
let requestController = null;
let stopped = false;
let apiUnavailable = false;
let reclaimPending = false;
let teachPending = false;
let servoSubmitting = false;
let servoTicket = null;
let hostAccess = null;
let hostError = "";
let confirmation = null;
const macMode = () => latest?.config.access_backend === "mac-sdk";
let rosView = new URLSearchParams(location.search).get("view") === "ros";
let configSignature = "";
let topicSignature = "";

const timeText = value => value ? new Date(value).toLocaleTimeString("ko-KR", {hour12: false}) : "—";
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function renderConfig(config) {
  const signature = JSON.stringify(config);
  if (signature === configSignature) return;
  configSignature = signature;
  const localBridge = config.ros_pc_ip.startsWith("127.") || config.ros_pc_ip === "::1";
  get("bridge-path").textContent = localBridge ? "로컬 · WS" : "LAN · WS";
  get("bridge-host").textContent = localBridge ? "로컬 ROS 2" : "ROS 2 컨트롤러";
  get("robot-endpoint").textContent = `E0509 제어기 · ${config.ip} · TCP ${config.port}`;
  get("bridge-port").textContent = config.rosbridge_port;
  const entries = [
    ["E0509 제어기 IP", config.ip, "ROBOT_IP"], ["E0509 제어기 TCP 포트", config.port, "ROBOT_PORT"],
    ["rosbridge 연결 호스트", config.rosbridge_host || config.ros_pc_ip || "미설정", "ROBOT_ROS_PC_IP"],
    ["rosbridge 수신 포트", config.rosbridge_port, "ROBOT_ROSBRIDGE_PORT"],
    ["ROS 2 네임스페이스", config.namespace, "ROBOT_NAMESPACE"]
  ];
  get("robot-config").replaceChildren(...entries.map(([label, value, env]) => {
    const row = element("div"), details = element("dd", value);
    details.append(element("code", env));
    row.append(element("dt", label), details);
    return row;
  }));
  get("robot-env").textContent = [
    `ROBOT_IP=${config.ip}`, `ROBOT_PORT=${config.port}`, `ROBOT_NAMESPACE=${config.namespace}`,
    `ROBOT_ROS_PC_IP=${config.ros_pc_ip}`, `ROBOT_ROSBRIDGE_PORT=${config.rosbridge_port}`,
    `ROBOT_STALE_SECONDS=${config.stale_seconds}`
  ].join("\n");
}

function renderTopics(topics) {
  const signature = topics.map(topic => topic.name).join("|");
  if (signature !== topicSignature) {
    topicSignature = signature;
    if (!topics.some(topic => topic.name === selectedTopic)) selectedTopic = topics[0]?.name;
    get("robot-topics").replaceChildren(...topics.map(topic => {
      const button = element("button", undefined, "robot-topic");
      button.type = "button";
      button.dataset.topic = topic.name;
      const identity = element("span");
      identity.append(element("b", topic.name), element("small", topic.type));
      button.append(identity, element("span", "—", "robot-topic-rate"), element("span", "대기", "robot-topic-status"));
      button.addEventListener("click", () => { topicChosen = true; selectedTopic = topic.name; render(latest); });
      return button;
    }));
  }
  const buttons = get("robot-topics").children;
  topics.forEach((topic, index) => {
    const button = buttons[index];
    button.setAttribute("aria-pressed", String(topic.name === selectedTopic));
    button.querySelector(".robot-topic-rate").textContent = topic.kind === "event" ? "이벤트" : topic.hz === null ? "—" : topic.hz.toFixed(1);
    const status = button.querySelector(".robot-topic-status");
    status.textContent = apiUnavailable ? "확인 불가" : topicStates[topic.state] || "대기";
    status.dataset.state = apiUnavailable ? "waiting" : topic.state;
    if (apiUnavailable && topic.kind !== "event") button.querySelector(".robot-topic-rate").textContent = "—";
  });
  const topic = topics.find(topic => topic.name === selectedTopic);
  get("selected-topic").textContent = topic?.name || "토픽 선택";
  get("message-time").textContent = timeText(topic?.received_at);
  get("robot-message").textContent = topic?.message ? JSON.stringify(topic.message, null, 2) : "메시지 수신 대기 중";
}

function render(data) {
  if (!data) return;
  renderControl();
  renderPoseTests(data, {paused, apiUnavailable, teachPending, hostAccess});
  renderWorkPoints(data, {paused, apiUnavailable});
  renderConfig(data.config);
  const joint = data.topics.find(topic => topic.name === data.display_joint_topic) || data.topics[0];
  if (!topicChosen) selectedTopic = joint.name;
  const names = joint.message?.name || [];
  const positions = joint.message?.position || [];
  const live = !apiUnavailable && ["live", "monitoring"].includes(data.state);
  get("robot-state").textContent = apiUnavailable ? "서버 응답 없음" : states[data.state] || "연결 상태 확인 중";
  get("robot-state").dataset.state = apiUnavailable ? "offline" : data.state;
  get("robot-hz").textContent = live && joint.hz !== null ? joint.hz.toFixed(1) : "—";
  get("robot-axis-count").textContent = live ? names.length : "—";
  get("robot-received").textContent = timeText(joint.received_at);
  get("joint-age").textContent = joint.received_at ?
    `${live ? "최근 수신" : "마지막 수신값"} · ${joint.age_seconds.toFixed(1)}초 전 · °` : "수신 대기 · °";
  get("robot-joints").replaceChildren(...Array.from({length: 6}, (_, index) => {
    const item = element("div", undefined, "robot-joint");
    item.append(element("span", names[index] || `J${index + 1}`));
    const value = positions[index];
    const number = element("strong", Number.isFinite(value) ? (value * 180 / Math.PI).toFixed(1) : "—");
    number.append(element("small", "°"));
    item.append(number);
    return item;
  }));
  const notices = {
    unconfigured: "Mac의 .env에 rosbridge 호스트 IP(ROBOT_ROS_PC_IP)를 설정하고 서버를 다시 시작하세요.",
    offline: "rosbridge 연결을 시도하고 있습니다. 호스트 IP와 rosbridge 실행 상태를 확인하세요.",
    waiting: data.joint_publishers?.length ? `관절 발행자 ${data.joint_publishers.join(", ")}에서 첫 메시지를 기다리고 있습니다.` :
      "rosbridge 연결 완료. 관절 토픽의 발행자를 확인하고 있습니다.",
    no_publisher: `rosbridge의 ROS 그래프에서 ${joint.name} 발행자가 0개입니다. 두산 드라이버, ROS 도메인과 네임스페이스를 확인하세요.`,
    live: "ROS 2 관절 데이터를 수신하고 있습니다.",
    monitoring: `E0509 제어기의 실제 관절각을 ${joint.name}에서 수신하고 있습니다. 자세 테스트는 매니퓰레이터에서 사용할 수 있습니다.`,
    stale: "관절 데이터 갱신이 멈췄습니다. 마지막 수신값을 표시합니다.",
    robot_disconnected: "드라이버에서 로봇 연결 끊김 이벤트를 수신했습니다. LAN 케이블과 컨트롤러를 확인하세요."
  };
  get("robot-notice").textContent = paused ? "표시를 일시정지했습니다. 재개하면 최신 수신값을 불러옵니다." :
    apiUnavailable ? "모니터링 서버의 응답을 기다리고 있습니다. 마지막 수신값을 표시합니다." :
    data.error || notices[data.state] || "연결 상태 확인 중";
  get("robot-notice").dataset.warning = String(Boolean(data.error) || ["no_publisher", "stale", "robot_disconnected"].includes(data.state));
  renderTopics(data.topics);
}

function renderHostControl() {
  const host = hostAccess, route = host?.route;
  const connected = host?.connection === "connected";
  const results = {
    pending: "강제 요청 전송 중 · SDK 콜백 대기",
    unknown: "결과 미확인 · 10초 내 응답이 없습니다. 늦은 콜백을 기다리며 중복 요청을 막고 있습니다.",
    denied: "요청 거절 (DENY) · 제어기가 사유를 제공하지 않았습니다.",
    lost: "보유권 상실 (LOSS) · 이 세션은 제어권을 보유하지 않습니다.",
    granted: "제어권 획득 (GRANT)",
    not_sent: "요청을 전송하지 못했습니다. 로봇 상태를 확인하세요."
  };
  const link = connected ? "연결됨" : host?.connection === "connecting" ? "연결 중" : host ? "연결 끊김" : "서버 상태 미확인";
  get("host-connection").textContent = [link, route && `${route.interface} → ${route.ip}:${route.port}`,
    host?.sdk_version && `SDK ${host.sdk_version}`, host?.controller_version && `DRCF ${host.controller_version}`,
    connected && (host.initialized ? "초기화 완료" : "초기화 대기"), host?.robot_state].filter(Boolean).join(" · ");
  get("host-held").textContent = connected ? host.held ? `보유 중 · ${host.held_seconds}초` : "미보유" : "미확인";
  get("host-callback").textContent = host?.last_callback ? `${host.last_callback} · ${timeText(host.callback_at)}` : "—";
  get("host-session").textContent = host?.session_id?.slice(0, 8) || "—";
  const quiet = "직접 교시는 ROS 드라이버 모드에서 사용할 수 있습니다.";
  const detail = {
    "open_connection failed": "제어기 SDK 연결 실패 · 제어기의 전원, 유선 IP와 포트를 확인하세요.",
    "No fresh SDK monitoring data": "제어기 상태 수신이 멈춰 연결을 종료했습니다.",
    "SDK disconnected": "SDK 연결이 끊겼습니다. 유선으로 다시 연결하고 있습니다."
  }[host?.detail] || host?.detail;
  get("control-availability").textContent = hostError || detail || results[host?.result] ||
    (host?.can_reclaim ? "정지 상태입니다. 이 Mac 세션으로 제어권을 요청할 수 있습니다." : "SDK 연결과 초기화 상태를 확인하세요.");
  const disabled = paused || apiUnavailable || reclaimPending || !host?.can_reclaim;
  get("control-reclaim").disabled = disabled;
  get("control-confirm").disabled = disabled || confirmation?.ticket?.session_id !== host?.session_id ||
    confirmation?.ticket?.revision !== host?.revision;
  get("direct-teach").checked = false;
  get("direct-teach").indeterminate = true;
  get("direct-teach").disabled = true;
  get("teach-status").textContent = quiet;
}

let modeTicket = null;
let modeSubmitting = false;
const modeNames = {MANUAL: "수동 운전 모드", AUTO: "자동 운전 모드"};
function renderOperationMode() {
  const host = hostAccess;
  const change = host?.mode_change;
  const visible = macMode() && !rosView;
  const available = visible && !paused && !apiUnavailable && !modeSubmitting && !!change?.token;
  const selector = get("operation-mode");
  get("operation-panel").hidden = !visible;
  selector.value = change?.verified && modeNames[host?.operation_mode] ? host.operation_mode : "";
  selector.disabled = !available;
  get("pose-mode-select").hidden = !available || (host.operation_mode === "AUTO" && change.verified);
  get("operation-confirm").disabled = !available || modeTicket?.session_id !== host?.session_id ||
    modeTicket?.mode_token !== change?.token;
  const results = {pending:"제어기의 모드 변경 응답 대기", unknown:"결과 미확인 · 제어기 응답 대기",
    failed:"변경을 확인하지 못했습니다. 제어기 알람과 모드 선택 상태를 확인하세요."};
  get("operation-status").textContent = results[change?.result] ||
    (host?.connection !== "connected" ? "SDK 연결 대기" : !host.held ? "Mac SDK 제어권이 필요합니다." :
    host.robot_state !== "STANDBY" ? "STANDBY에서 변경할 수 있습니다." :
    !change ? "Mac SDK 서버 업데이트가 필요합니다." :
    available ? `현재 ${modeNames[host.operation_mode] || host.operation_mode}` : "운전 모드 확인 대기");
}
get("pose-mode-select").addEventListener("click", () => {
  get("pose-dialog").close();
  get("operation-mode").focus();
});
get("operation-mode").addEventListener("change", event => {
  if (!hostAccess?.mode_change?.token) return;
  const target = event.target.value;
  modeTicket = {session_id:hostAccess.session_id, mode_token:hostAccess.mode_change.token, target};
  get("operation-description").textContent = `E0509 (${hostAccess.route.ip}) · 세션 ${hostAccess.session_id.slice(0,8)} · ${modeNames[target]}로 전환합니다.`;
  get("operation-error").hidden = true;
  renderOperationMode();
  get("operation-dialog").showModal();
});
get("operation-cancel").addEventListener("click", () => get("operation-dialog").close());
get("operation-dialog").addEventListener("close", () => { if (!get("operation-dialog").open) modeTicket = null; });
get("operation-confirm").addEventListener("click", async () => {
  if (get("operation-confirm").disabled || !modeTicket) return;
  const ticket = modeTicket;
  get("operation-dialog").close();
  modeSubmitting = true;
  renderOperationMode();
  try {
    const res = await fetch("/api/robot/host-access/mode", {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify(ticket), signal:AbortSignal.timeout(5000)});
    const state = await res.json();
    if (!res.ok) throw new Error(typeof state.detail === "string" ? state.detail : "모드 변경 요청 실패");
    if (hostAccess?.session_id === ticket.session_id) hostAccess = state;
  } catch (error) {
    get("operation-error").textContent = error.message;
    get("operation-error").hidden = false;
  } finally {
    modeSubmitting = false;
    renderControl();
    refresh();
  }
});

function renderServo() {
  const visible = macMode() && !rosView;
  const servo = hostAccess?.servo;
  const available = visible && !paused && !apiUnavailable && !servoSubmitting && !!servo?.token;
  get("servo-panel").hidden = !visible;
  get("servo-on").disabled = !available;
  const recovery = hostAccess?.robot_state === "SAFE_STOP";
  get("servo-on").textContent = recovery ? "STANDBY 복구" : "서보 켜기";
  get("pose-servo-on").textContent = recovery ? "STANDBY 복구" : "서보 켜기";
  get("pose-servo-on").hidden = !visible || !["SAFE_OFF", "SAFE_STOP"].includes(hostAccess?.robot_state);
  get("pose-servo-on").disabled = !available;
  get("servo-confirm").disabled = !available || servoTicket?.servo_token !== servo?.token ||
    servoTicket?.session_id !== hostAccess?.session_id;
  get("servo-status").textContent = hostError || servo?.message ||
    (hostAccess ? "서보 요청을 지원하는 Mac SDK 서버가 필요합니다." : "SDK 상태 확인 중");
}
function confirmServo() {
  if (get("servo-on").disabled) return;
  servoTicket = {session_id: hostAccess.session_id, servo_token: hostAccess.servo.token};
  if (get("pose-dialog").open) get("pose-dialog").close();
  get("servo-error").hidden = true;
  const recovery = hostAccess.robot_state === "SAFE_STOP";
  get("servo-title").textContent = recovery ? "STANDBY로 복구할까요?" : "서보를 켤까요?";
  get("servo-confirm").textContent = recovery ? "STANDBY 전환 요청" : "서보 ON 요청";
  get("servo-description").textContent = `E0509 (${hostAccess.route.ip}:${hostAccess.route.port}) · Mac 세션 ${hostAccess.session_id.slice(0, 8)}` + (recovery ? " · SAFE_STOP 해제 후 중단된 작업은 자동 재개하지 않습니다." : "");
  renderServo();
  get("servo-dialog").showModal();
}
get("servo-on").addEventListener("click", confirmServo);
get("pose-servo-on").addEventListener("click", confirmServo);
get("servo-cancel").addEventListener("click", () => get("servo-dialog").close());
get("servo-dialog").addEventListener("close", () => { servoTicket = null; });
get("servo-confirm").addEventListener("click", async () => {
  if (get("servo-confirm").disabled || !servoTicket) return;
  const ticket = servoTicket;
  get("servo-dialog").close();
  servoSubmitting = true;
  renderServo();
  try {
    const res = await fetch("/api/robot/host-access/servo-on", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(ticket), signal: AbortSignal.timeout(5000)
    });
    const state = await res.json();
    if (!res.ok) throw new Error(typeof state.detail === "string" ? state.detail : "서보 요청을 확인할 수 없습니다.");
    if (hostAccess?.session_id === ticket.session_id) hostAccess = state;
  } catch (error) {
    get("servo-error").textContent = error instanceof TypeError || error instanceof SyntaxError || error.name === "TimeoutError"
      ? "서보 요청 결과 미확인 · 현재 제어기 상태를 확인하세요." : error.message;
    get("servo-error").hidden = false;
  } finally {
    servoSubmitting = false;
    renderControl();
    refresh();
  }
});

function renderControl() {
  renderHand(latest, !paused && !apiUnavailable ? hostAccess : null);
  workPlane?.gripperStatus(!paused && !apiUnavailable ? latest : null, !paused && !apiUnavailable ? hostAccess : null);
  const overview = get("robot-overview");
  if (macMode()) {
    const state = hostAccess?.robot_state;
    const servo = state === "SAFE_OFF" ? "꺼짐" : ["STANDBY","MOVING"].includes(state) ? "켜짐" : "미확인";
    overview.textContent = "제어권 "+(hostAccess ? hostAccess.held ? "보유" : "미보유" : "미확인")+
      " · 서보 "+servo+" · 운전 모드 "+(hostAccess?.operation_mode || "미확인");
  } else overview.textContent = "ROS 드라이버 · 운전 모드 "+(latest?.direct_teach?.mode || "미확인")+" · 서보 미확인";
  renderOperationMode();
  renderServo();
  const view = get("control-view");
  view.disabled = !macMode();
  view.value = macMode() && !rosView ? "mac-sdk" : "ros";
  view.title = macMode() ? "표시만 변경하며 제어권을 요청하지 않습니다." : "Mac SDK 검증은 .env에서 ROBOT_ACCESS_BACKEND=mac-sdk를 선택한 후 사용할 수 있습니다.";
  get("host-access").hidden = !macMode() || rosView;
  get("control-source").textContent = macMode() ? "Mac SDK 제어권 검증" : "드라이버 제어권";
  if (macMode()) {
    renderHostControl();
    if (rosView) {
      get("control-source").textContent = "ROS 드라이버 · 읽기 전용";
      get("control-availability").textContent = "Mac SDK 검증 모드에서는 ROS 제어권을 요청할 수 없습니다.";
      get("control-reclaim").disabled = get("control-confirm").disabled = true;
    }
    return;
  }
  const pending = reclaimPending || latest?.reclaim_pending;
  const reason = paused ? "화면 표시를 재개하면 요청할 수 있습니다." :
    apiUnavailable ? "웹 상태 API 응답 대기" :
    !latest?.bridge_connected ? "rosbridge 연결 대기" :
    !latest.reclaim_available ? "회수 서비스 대기 · rosbridge 연결됨. doosan-driver의 SDK 연결과 초기화 상태를 확인하세요." :
    pending ? "드라이버의 제어권 응답을 기다리고 있습니다." : "드라이버에 제어권 회수를 요청할 수 있습니다.";
  get("control-availability").textContent = reason;
  const teach = latest?.direct_teach;
  const switching = teachPending || teach?.pending;
  const disabled = !!pending || switching || paused || apiUnavailable || !latest?.reclaim_available;
  get("control-reclaim").disabled = disabled;
  get("control-confirm").disabled = disabled;
  const toggle = get("direct-teach");
  const mode = apiUnavailable || !latest?.bridge_connected ? "unavailable" : teach?.mode;
  toggle.checked = mode === "manual";
  toggle.indeterminate = mode !== "manual" && mode !== "auto";
  toggle.disabled = paused || !!pending || !!switching || !!latest?.pose_tests?.busy ||
    !["auto", "manual", "unconfirmed"].includes(mode);
  get("teach-status").textContent = switching ? "제어기 운전 모드 전환 중…" :
    mode === "manual" ? "수동 모드 · 로봇팔의 직접 교시 버튼을 누른 동안 손으로 움직일 수 있습니다. 놓으면 멈춥니다." :
    mode === "auto" ? "자동 모드 · 켜면 수동 모드로 전환합니다." :
    mode === "unconfirmed" ? "모드 미확인 · 스위치를 누르면 수동 모드를 다시 요청합니다." :
    apiUnavailable ? "웹 상태 API 응답 대기" : teach?.message || "운전 모드 확인 대기";
}
get("control-view").addEventListener("change", () => {
  rosView = get("control-view").value === "ros";
  const url = new URL(location.href);
  if (rosView) url.searchParams.set("view", "ros"); else url.searchParams.delete("view");
  history.replaceState(null, "", url);
  renderControl();
});
get("direct-teach").addEventListener("change", async () => {
  const enabled = latest?.direct_teach?.mode !== "manual";
  teachPending = true;
  const output = get("teach-result");
  output.hidden = true;
  render(latest);
  try {
    const response = await fetch("/api/robot/control/direct-teach", {
      method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "운전 모드를 확인하지 못했습니다.");
    latest.direct_teach = {mode: data.mode, pending: false, message: ""};
    output.textContent = data.message;
  } catch (error) {
    latest.direct_teach = {mode: "unavailable", message: "현재 운전 모드를 다시 확인하고 있습니다."};
    output.textContent = error instanceof TypeError || error instanceof SyntaxError
      ? "서버 응답이 없어 전환 결과를 확인할 수 없습니다." : error.message;
  } finally {
    teachPending = false;
    output.hidden = false;
    render(latest);
    refresh();
  }
});
get("control-reclaim").addEventListener("click", () => {
  confirmation = macMode() ? {backend: "mac-sdk", ticket: {session_id: hostAccess.session_id, revision: hostAccess.revision}} : {backend: "ros"};
  get("control-description").textContent = macMode()
    ? `E0509 (${hostAccess.route.ip}:${hostAccess.route.port})의 제어권을 Mac 세션 ${hostAccess.session_id.slice(0, 8)}로 요청합니다. 승인되면 기존 보유자가 제어권을 잃습니다. 서보나 로봇 이동은 요청하지 않습니다.`
    : `E0509 (${latest.config.ip})의 제어권을 이 드라이버로 가져옵니다. 승인되면 기존 보유자는 제어권을 잃습니다.`;
  renderControl();
  get("control-dialog").showModal();
});
get("control-cancel").addEventListener("click", () => get("control-dialog").close());
get("control-confirm").addEventListener("click", async () => {
  if (get("control-confirm").disabled || confirmation?.backend !== latest?.config.access_backend) return;
  const mac = confirmation.backend === "mac-sdk";
  get("control-dialog").close();
  reclaimPending = true;
  const output = get("control-result");
  output.hidden = true;
  output.textContent = "";
  renderControl();
  try {
    const response = await fetch(mac ? "/api/robot/host-access/reclaim" : "/api/robot/control/reclaim", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(mac ? confirmation.ticket : {}), signal: AbortSignal.timeout(mac ? 5000 : 20000)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "회수 결과를 확인할 수 없습니다.");
    if (mac) {
      hostAccess = data;
      hostError = "";
      return;
    }
    output.dataset.success = String(data.success === true);
    output.textContent = `최근 요청 · ${data.message}`;
  } catch (error) {
    output.dataset.success = "false";
    output.textContent = error instanceof TypeError || error instanceof SyntaxError
      ? "서버 응답이 없어 회수 결과를 확인할 수 없습니다." : error.message;
  } finally {
    reclaimPending = false;
    output.hidden = mac && !output.textContent;
    renderControl();
    refresh();
  }
});

let workPlane = null;
async function refresh() {
  if (stopped || (paused && !workPlane) || document.hidden || requestController) return;
  const controller = new AbortController();
  requestController = controller;
  const timeout = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch("/api/robot/status", {cache: "no-store", signal: controller.signal});
    if (!response.ok) throw new Error("status unavailable");
    const data = await response.json();
    if (stopped) return;
    workPlane?.robotStatus(data);
    if (paused) return;
    latest = data;
    apiUnavailable = false;
    if (macMode()) {
      try {
        const res = await fetch("/api/robot/host-access/status", {cache: "no-store", signal: controller.signal});
        const value = await res.json();
        if (!res.ok) {
          hostAccess = null;
          hostError = typeof value.detail === "string" ? value.detail : "Mac SDK 상태 조회 실패";
        } else {
          hostAccess = value;
          hostError = "";
        }
        if (stopped || paused || document.hidden) { clearHostStatus(); return; }
      } catch {
        hostAccess = null;
        hostError = "Mac SDK 서버 응답 없음 · Mac에서 bash scripts/robot-access.sh 실행 여부를 확인하세요.";
      }
    } else {
      hostAccess = null;
      hostError = "";
    }
    get("robot-api-error").hidden = true;
    render(data);
  } catch {
    workPlane?.robotStatus(null);
    if (stopped || paused) return;
    apiUnavailable = true;
    hostAccess = null;
    hostError = "웹 상태 API 응답 없음 · 제어권 미확인";
    get("robot-api-error").textContent = "모니터링 서버의 응답이 없습니다. 표시된 값은 마지막 수신값입니다.";
    get("robot-api-error").hidden = false;
    if (latest) render(latest);
    else {
      get("robot-state").textContent = "서버 응답 없음";
      renderControl();
    }
  } finally {
    clearTimeout(timeout);
    if (requestController === controller) requestController = null;
  }
}
function routeRobot() {
  const page = currentDestination();
  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  document.querySelectorAll("[data-view]").forEach(el=>{el.hidden = !el.dataset.view.split(" ").includes(page.id);});
  document.querySelector("h1").textContent = page.label;
  document.title = page.label+" · Vision Lab";
  const plane = get("work-plane");
  if (!plane.hidden) {
    if (!workPlane) {
      workPlane = connectWorkPlane(plane);
      void refresh();
    }
    workPlane.showWorkspace(page.id);
    if (location.hash === "#gripper-table") requestAnimationFrame(() => get("gripper-table")?.scrollIntoView({block:"start"}));
  } else if (workPlane) {
    workPlane.dispose(); workPlane = null;
  }
  if (page.id === "gripper") void loadHandTable();
}
window.addEventListener("hashchange", routeRobot);
routeRobot();
get("connection-settings").addEventListener("click", () => {
  const panel = get("robot-settings");
  panel.hidden = !panel.hidden;
  get("connection-settings").setAttribute("aria-expanded", String(!panel.hidden));
});
get("robot-pause").addEventListener("click", () => {
  paused = !paused;
  get("robot-pause").textContent = paused ? "표시 재개" : "표시 일시정지";
  get("robot-pause").setAttribute("aria-pressed", String(paused));
  if (paused) { hostAccess = null; hostError = "표시 일시정지 · 제어권 미확인"; }
  if (paused) workPlane?.robotStatus(null);
  if (paused) get("robot-notice").textContent = "표시를 일시정지했습니다. 재개하면 최신 수신값을 불러옵니다.";
  else refresh();
  if (latest) renderPoseTests(latest, {paused, apiUnavailable, teachPending, hostAccess});
  if (latest) renderWorkPoints(latest, {paused, apiUnavailable});
  renderControl();
});
function clearHostStatus() {
  hostAccess = null;
  hostError = "";
  if (latest) {
    renderControl();
    renderPoseTests(latest, {paused, apiUnavailable, teachPending, hostAccess});
  }
}
window.addEventListener("pagehide", () => { stopped = true; workPlane?.dispose(); workPlane = null; clearHostStatus(); requestController?.abort(); });
window.addEventListener("pageshow", e => { stopped = false; if (e.persisted) routeRobot(); refresh(); });
document.addEventListener("visibilitychange", () => document.hidden ? clearHostStatus() : refresh());
setInterval(refresh, 1000);
refresh();
