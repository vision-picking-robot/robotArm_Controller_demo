const motionKey = "vision-last-camera-pick";
const labels = {pick:"파지 위치", waypoint:"박스 이송 경유점", box:"택배 박스 위치"};
const terminal = new Set(["completed", "cancelled", "failed", "unknown"]);

export function renderRoute(el, preview) {
  const transfer = preview.mode === "transfer";
  el.target.hidden = transfer;
  el.route.hidden = !transfer;
  el.settingsLink.hidden = !transfer || Boolean(!preview.motion_reason && (preview.scenario || preview.route?.waypoint?.pose && preview.route?.box?.pose));
  if (!transfer) return;
  if(preview.scenario){
    el.route.replaceChildren(...preview.steps.map(step=>{const row=document.createElement('li'),values=document.createElement('small');row.append(step.number+'. '+step.title);
      values.textContent=[step.motion||'',step.target?'목표 ['+step.target.map(v=>v.toFixed(2)).join(', ')+']':'',step.via_pose?'원호 경유 TCP ['+step.via_pose.map(v=>v.toFixed(2)).join(', ')+']':'',step.joints?'관절 목표 ['+step.joints.map(v=>v.toFixed(2)).join(', ')+']°':'',step.speed?'속도 '+step.speed.speed+(step.kind==='gripper'?' rpm':['line','circle'].includes(step.kind)||['movel','movec'].includes(step.motion)?' mm/s':'°/s')+' · 가속도 '+step.speed.acceleration:'',step.opening_mm!=null?'벌림 '+step.opening_mm+' mm':'',step.gate?'작업자 승인 대기':''].filter(Boolean).join(' · ');row.append(values);return row;}));return;
  }
  el.route.replaceChildren(...Object.entries(labels).map(([name,label]) => {
    const pose = name === "pick" ? preview.pose : preview.route[name].pose;
    const row = document.createElement("li"), values = document.createElement("small");
    values.textContent = pose ? pose.map((n,i) => "XYZABC"[i]+" "+n.toFixed(2)+(i<3?" mm":"°")).join(" · ") : "미설정";
    row.append(label, values);
    return row;
  }));
}

// Monitor the server job even after the camera stops and across page reloads.
// Only a button click issues an action. Polling never starts or resumes motion.
export function connectPickJob(el, api, changed, signal) {
  let id = sessionStorage.getItem(motionKey), timer, epoch = 0;
  function invalidate() {
    clearTimeout(timer);
    return ++epoch;
  }
  function remember(next, discovered = false) {
    id = next;
    if (next) sessionStorage.setItem(motionKey,next);
    else sessionStorage.removeItem(motionKey);
    changed(discovered);
  }
  function show(move) {
    el.status.textContent = move.message;
    el.job.hidden = move.mode !== "transfer";
    el.grip.hidden = move.state !== "awaiting_grip";
    el.stopTransfer.hidden = move.scenario?terminal.has(move.state):move.state!=='awaiting_grip';
    el.stopTransfer.textContent=move.scenario?'실제 작업 중단':'이송 취소';
    el.openBox.hidden = move.state !== "awaiting_open";
    el.release.hidden = move.state !== "awaiting_release";
    for (const button of [el.grip,el.stopTransfer,el.openBox,el.release]) button.disabled = false;
    if (!el.job.hidden) {
      el.jobTitle.textContent = move.label + " · 택배 박스 이송";
      const feedback = move.hand_feedback;
      el.jobStage.textContent = move.message + (feedback ? " · RH-P12 명령 "+move.hand_command+" / 실제 "+feedback.position : "");
      el.jobReached.textContent = "마지막 도착 확인 · " + (labels[move.last_reached] || move.last_reached || "도착 전");
    }
    if (terminal.has(move.state)) remember(null);
  }
  async function poll() {
    const version = invalidate();
    try {
      const move = id ? await api("pick/moves/"+id) : (await api("pick/active")).move;
      if (version !== epoch || signal.aborted) return;
      if (move) {
        if (!id) remember(move.approval_id, true);
        show(move);
      }
    } catch (error) {
      if (version !== epoch || signal.aborted) return;
      if (id) {
        el.status.textContent = "작업 상태 확인 중 · " + error.message;
        for (const button of [el.grip,el.stopTransfer,el.openBox,el.release]) button.disabled = true;
        if (error.status === 404) { el.job.hidden = true; remember(null); }
      }
    }
    if (version === epoch && !signal.aborted) timer = setTimeout(poll, id ? 700 : 3500);
  }
  async function act(action) {
    if (!id) return;
    const version = invalidate();
    for (const button of [el.grip,el.stopTransfer,el.openBox,el.release]) button.disabled = true;
    try {
      const move = await api("pick/moves/"+id+"/"+action, "POST", {});
      if (version === epoch && !signal.aborted) show(move);
    } catch (error) {
      if (version === epoch && !signal.aborted) el.status.textContent = error.message;
    }
    if (version === epoch && !signal.aborted) void poll();
  }
  for (const [button,action] of [[el.grip,"confirm-grip"],[el.stopTransfer,"cancel-transfer"],[el.openBox,"open-at-box"],[el.release,"confirm-release"]]) {
    button.addEventListener("click",() => void act(action),{signal});
  }
  signal.addEventListener("abort",invalidate,{once:true});
  timer = setTimeout(poll,0);
  return {
    get busy() { return Boolean(id); },
    reserve(next) { invalidate(); remember(next); el.job.hidden = true; },
    follow() { if (!signal.aborted) void poll(); },
    rejected() { remember(null); if (!signal.aborted) void poll(); }
  };
}
