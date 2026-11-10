import { simulationToolPose } from "./robot-arm.js";
const axes = ["X (mm)", "Y (mm)", "Z (mm)", "A (°)", "B (°)", "C (°)"];
let canRead = false;
let serverAvailable = false;
let simulationBusy = false;

// One persistent editor survives workspace remounts, just like the TCP editors.
const parcelListeners = new Set();
let parcelPreview = null;
export function subscribeParcelBox(listener) {
  parcelListeners.add(listener); listener(parcelPreview);
  return () => parcelListeners.delete(listener);
}
function createParcelBoxEditor() {
  const form = document.getElementById("parcel-box-form");
  const inputs = ["width","depth","height","x","y","z"].map(name => form.elements.namedItem(name));
  const state = document.getElementById("parcel-box-state"), hint = document.getElementById("parcel-box-preview");
  const feedback = document.getElementById("parcel-box-feedback"), save = document.getElementById("parcel-box-save");
  const cancel = document.getElementById("parcel-box-cancel"), reload = document.getElementById("parcel-box-reload");
  let saved = null, dirty = false, revision = null, pending = false, rejected = false, online = false, signature;
  function value() {
    const numbers = inputs.map(input => input.valueAsNumber);
    if (!inputs.every(input => input.validity.valid) || !numbers.every(v => Number.isFinite(v) && Math.abs(v)<=1e6) || numbers.slice(0,3).some(v => v < .001)) return null;
    return {size_mm:numbers.slice(0,3), center_base_mm:numbers.slice(3)};
  }
  function fill() {inputs.forEach((input,i) => {input.value = [...(saved?.size_mm ?? [null,null,null]),...(saved?.center_base_mm ?? [null,null,null])][i] ?? "";});}
  function message(text, error = false) {feedback.textContent=text;feedback.dataset.error=error;}
  function update() {
    const box=value(), conflict=rejected || dirty && revision !== saved?.revision;
    state.textContent = pending ? "처리 중" : conflict ? "다른 화면에서 변경됨" : dirty ? "저장 전" : saved?.revision ? "저장됨" : saved ? "기본값" : "조회 필요";
    inputs.forEach(input => {input.disabled=pending;});
    save.disabled=pending || !online || !saved || (!dirty && saved.revision !== null) || conflict || !box;
    cancel.disabled=pending || !dirty;
    reload.hidden=!conflict && Boolean(saved) && online;reload.disabled=pending;
    hint.textContent=box ? `바닥면 중심 (${box.center_base_mm.join(", ")}) mm · 윗면 Z ${Number((box.center_base_mm[2]+box.size_mm[2]).toFixed(3))} mm · ${dirty?"저장 전 미리보기":saved?.revision?"저장된 박스":"기본 박스"}`
      : "크기는 0보다 큰 값, 위치는 유효한 BASE 좌표로 모두 입력하세요. 입력이 완성되면 박스가 표시됩니다.";
    const next=box ? {...box,draft:dirty} : null, key=JSON.stringify(next);
    if(key!==signature){signature=key;parcelPreview=next;for(const listener of parcelListeners)listener(next);}
  }
  function receive(next) {
    if(next && (!saved || (next.updated_at || "") >= (saved.updated_at || "")) && (!saved || next.revision !== saved.revision)) {
      saved=next;if(!dirty&&!pending)fill();
    }
    update();
  }
  async function request(saving) {
    if(pending || saving && save.disabled)return;
    const body=saving ? {...value(),revision} : null;
    pending=true;message(saving?"박스 설정 저장 중…":"최신 박스 설정 조회 중…");update();
    try {
      const response=await fetch("/api/robot/parcel-box",saving ? {
        method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:AbortSignal.timeout(6000)
      } : {cache:"no-store",signal:AbortSignal.timeout(6000)});
      const data=await response.json();
      if(!response.ok){if(response.status===409)rejected=true;throw Error(typeof data.detail==="string"?data.detail:"박스 크기와 위치 입력값을 확인하세요.");}
      saved=data;online=true;dirty=false;rejected=false;revision=saved.revision;fill();
      message(saving?"박스 설정을 저장했습니다.":"최신 박스 설정을 불러왔습니다.");
    } catch(error) {
      if(error instanceof TypeError || error instanceof SyntaxError || error.name==="TimeoutError") {
        online=false;message("서버 응답을 확인하지 못했습니다. 입력값을 유지합니다. 최신 설정을 다시 불러오세요.",true);
      }else message(error.message,true);
    } finally {pending=false;update();}
  }
  form.addEventListener("input",()=>{if(!dirty)revision=saved?.revision ?? null;dirty=true;message("");update();});
  form.addEventListener("submit",event=>{event.preventDefault();if(form.reportValidity())void request(true);});
  cancel.addEventListener("click",()=>{if(rejected){void request(false);return;}dirty=false;revision=saved?.revision;fill();message("");update();});
  reload.addEventListener("click",()=>void request(false));
  void request(false);
  return (data,available)=>{online=available;receive(data);};
}
const renderParcelBox=createParcelBoxEditor();

function createEditor(name, title) {
  const form = document.getElementById("work-point-template").content.firstElementChild.cloneNode(true);
  const heading = form.querySelector("h3");
  heading.id = "work-point-" + name;
  heading.textContent = title;
  form.setAttribute("aria-labelledby", heading.id);
  const read = form.querySelector("[data-read]");
  const reset = form.querySelector("[data-reset]");
  const save = form.querySelector('[type="submit"]');
  const state = form.querySelector("[data-state]");
  const feedback = form.querySelector('[role="status"]');
  const simulate = form.querySelector("[data-simulate-point]"), include = form.querySelector("[data-route-point]");
  simulate.dataset.simulatePoint = include.dataset.routePoint = name;
  simulate.setAttribute("aria-label",title+" 시뮬레이션");
  include.checked=name!=="shipping_label";
  const fields = axes.map(axis => {
    const label = document.createElement("label");
    label.textContent = axis;
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.required = true;
    input.placeholder = "—";
    label.append(input);
    form.querySelector(".pose-fields").append(label);
    return input;
  });
  document.getElementById("work-points").append(form);

  let point = null, dirty = false, revision = null, pending = false, locked = false, poseSignature;
  const draft = () => fields.map(input=>Number.isFinite(input.valueAsNumber)?input.valueAsNumber:null);
  function update() {
    const conflict = dirty && revision !== point?.revision;
    state.textContent = !point ? "조회 중" : locked ? "박스 이송 작업 중" : conflict ? "다른 화면에서 변경됨" : dirty ? "저장 전" :
      point.pose ? "저장됨 · " + new Date(point.updated_at).toLocaleString("ko-KR") : "미설정";
    fields.forEach(input => { input.disabled = !point || pending || locked; });
    read.disabled = !point || !canRead || pending || conflict || locked;
    reset.hidden = !dirty;
    reset.disabled = pending;
    save.disabled = !serverAvailable || !dirty || pending || conflict || locked;
    simulate.disabled = pending || simulationBusy || !draft().every(Number.isFinite);
    const key=JSON.stringify([draft(),point?.revision]);
    if(key!==poseSignature){poseSignature=key;form.dispatchEvent(new CustomEvent("work-point-change",{bubbles:true}));}
  }
  function fill(values) {
    fields.forEach((input, i) => { input.value = values?.[i] ?? ""; });
  }
  function edit() {
    if (!dirty) revision = point.revision;
    dirty = true;
    feedback.textContent = "";
    update();
  }
  form.addEventListener("input", event=>{if(fields.includes(event.target))edit();});
  reset.addEventListener("click", () => {
    dirty = false;
    fill(point.pose);
    feedback.textContent = "";
    update();
  });

  async function request(capture) {
    if ((capture ? read : save).disabled) return;
    if (capture && !dirty) revision = point.revision;
    pending = true;
    feedback.textContent = capture ? "제어기에서 현재 위치를 읽는 중…" : "좌표 저장 중…";
    update();
    try {
      const url = "/api/robot/work-points/" + (capture ? "current" : name);
      const result = await fetch(url, capture ? {cache: "no-store"} : {
        method: "PUT", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({revision, pose: fields.map(input => input.valueAsNumber)})
      });
      const data = await result.json();
      if (!result.ok) throw new Error(typeof data.detail === "string" ? data.detail : "여섯 좌표의 입력값을 확인하세요.");
      if (capture) {
        dirty = true;
        fill(data.pose);
        feedback.textContent = "현재 위치를 불러왔습니다. 값을 확인하고 좌표 저장을 누르세요.";
      } else {
        point = data;
        dirty = false;
        fill(point.pose);
        feedback.textContent = title + " 저장 완료.";
      }
    } catch (error) {
      feedback.textContent = error instanceof TypeError || error instanceof SyntaxError
        ? "웹 서버에 연결할 수 없습니다. 입력값을 유지합니다." : error.message;
    } finally {
      pending = false;
      if (!dirty) fill(point.pose);
      update();
    }
  }
  read.addEventListener("click", () => void request(true));
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (form.reportValidity()) void request(false);
  });
  update();
  function render(next, inUse) {
    locked = inUse;
    // A poll started before a successful save may deliver an older revision.
    if (next && (!point || (next.updated_at || "") >= (point.updated_at || "")) &&
        (!point || point.revision !== next.revision)) {
      point = next;
      if (!dirty && !pending) fill(point.pose);
    }
    update();
  }
  render.refresh=update;
  render.snapshot=()=>({id:name,title,pose:draft(),included:include.checked});
  return render;
}

const editors = {
  waypoint: createEditor("waypoint", "박스 이송 경유점"),
  box: createEditor("box", "택배 박스 위치"),
  shipping_label: createEditor("shipping_label", "택배 박스의 송장 위치 (RFID 스캔)")
};

const simulationToolInputs=axes.map((axis,i)=>{
  const label=document.createElement("label"),input=document.createElement("input");label.textContent=axis;
  input.type="number";input.step="any";input.value=simulationToolPose[i];input.required=true;
  label.append(input);document.getElementById("simulation-tool-fields").append(label);return input;
});
export function connectWaypointSimulation(panel,arm,signal) {
  const get=id=>panel.querySelector("#simulation-"+id),start=get("start"),pause=get("pause"),stop=get("stop"),progress=get("progress"),status=get("status");
  let state="idle";
  function showRoute(route=Object.values(editors).map(e=>e.snapshot()).filter(p=>p.included)) {
    get("route").textContent=route.length?"방문 순서 · 시작 자세 → "+route.map((p,i)=>(i+1)+". "+p.title).join(" → "):"경로에 포함할 지점을 선택하세요.";
  }
  const on=(node,type,fn)=>node.addEventListener(type,fn,{signal});
  const unsubscribe=arm.observeSimulation(next=>{
    state=next.state;simulationBusy=["planning","running","paused"].includes(state);
    start.disabled=simulationBusy;pause.disabled=!["running","paused"].includes(state);
    pause.textContent=state==="paused"?"계속 재생":"일시정지";
    stop.disabled=state==="idle";progress.disabled=["idle","planning","error"].includes(state);
    progress.value=Math.round((next.progress||0)*1000);get("percent").textContent=Math.round((next.progress||0)*100)+"%";
    status.textContent=next.message;status.dataset.state=state;
    if(next.route)showRoute(next.route);else if(state==="idle")showRoute();
    for(const editor of Object.values(editors))editor.refresh();
  });
  signal.addEventListener("abort",()=>{unsubscribe();simulationBusy=false;for(const editor of Object.values(editors))editor.refresh();},{once:true});
  function run(id) {
    if(simulationBusy)return;
    const route=Object.values(editors).map(editor=>editor.snapshot()).filter(p=>id?p.id===id:p.included);
    if(!route.length){status.textContent="시뮬레이션할 지점을 하나 이상 선택하세요.";return;}
    const invalid=route.find(p=>!p.pose.every(Number.isFinite));
    if(invalid){status.textContent=invalid.title+"의 여섯 좌표를 모두 입력하세요.";return;}
    const toolPose=simulationToolInputs.map(input=>input.valueAsNumber);
    if(!toolPose.every(Number.isFinite)){status.textContent="시뮬레이션 TCP 오프셋의 여섯 값을 모두 입력하세요.";return;}
    arm.simulationAction("rate",Number(get("rate").value));
    showRoute(route);
    void arm.simulate(route,toolPose);
  }
  on(start,"click",()=>run());
  on(panel,"click",event=>{const button=event.target.closest("[data-simulate-point]");if(button&&!button.disabled)run(button.dataset.simulatePoint);});
  on(pause,"click",()=>arm.simulationAction(state==="paused"?"resume":"pause"));
  on(stop,"click",()=>arm.simulationAction("stop"));
  on(progress,"input",()=>arm.simulationAction("seek",Number(progress.value)/1000));
  on(get("rate"),"change",()=>arm.simulationAction("rate",Number(get("rate").value)));
  const invalidate=()=>{if(!["idle","error"].includes(state))arm.simulationAction("stop");};
  on(panel,"work-point-change",invalidate);
  on(panel,"input",event=>{if(event.target.matches("[data-route-point],#simulation-tool-fields input")){invalidate();showRoute();}});
}

export function renderWorkPoints(data, {paused, apiUnavailable}) {
  serverAvailable = !apiUnavailable;
  renderParcelBox(data.parcel_box, serverAvailable);
  canRead = serverAvailable && !paused && data.bridge_connected &&
    data.topics.some(topic => topic.kind === "monitor_joints" && topic.state === "live");
  document.getElementById("work-point-source").textContent = canRead
    ? "현재 위치를 불러올 수 있습니다. 공구를 원하는 위치에 멈춘 뒤 불러오세요."
    : apiUnavailable ? "웹 서버 응답 대기 중입니다." : paused ? "현재 위치를 불러오려면 화면 표시를 재개하세요."
    : "현재 위치를 불러오려면 읽기 전용 관절 모니터 연결이 필요합니다. 수동 입력은 가능합니다.";
  for (const [key, render] of Object.entries(editors)) render(data.work_points?.[key], key !== "shipping_label" && data.transfer_active);
}
