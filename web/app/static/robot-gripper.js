const get = id => document.getElementById(id);
const samples = get("hand-samples");
const initialContactCurrent=30;
let contact=null,contactPending=false;
async function loadContact(){try{contact=await api('contact');get('hand-contact-current').value=contact.threshold_ma??initialContactCurrent;get('hand-contact-result').textContent=contact.threshold_ma==null?'초기 제안값 · '+initialContactCurrent+' mA · 접촉 설정 저장 후 적용됩니다.':'저장된 임계값 · '+contact.threshold_ma+' mA';}catch(error){get('hand-contact-result').textContent=error.message;}finally{controls();}}
get('hand-contact-reload').onclick=()=>void loadContact();
get('hand-contact-save').onclick=async()=>{if(contactPending||!contact||locked)return;const value=get('hand-contact-current').valueAsNumber;if(!Number.isInteger(value)||value<1||value>1984){get('hand-contact-result').textContent='1~1984 mA의 정수 임계값을 입력하세요.';return;}contactPending=true;controls();try{contact=await api('contact','PUT',{revision:contact.revision,threshold_ma:value});get('hand-contact-result').textContent='접촉 설정 저장 완료.';}catch(error){get('hand-contact-result').textContent=error.message;}finally{contactPending=false;controls();}};
void loadContact();
let table = null, dirty = false, saving = false, busy = false, host = null, locked = false, sdkMode = false;
let loading = 0;
let testPending=false, testTarget=null, testVersion=0, testExpiry=null;
async function api(path, method = "GET", body) {
  const res = await fetch("/api/robot/gripper/"+path, {method,cache:"no-store",
    headers:body ? {"Content-Type":"application/json"} : {}, body:body && JSON.stringify(body)});
  const value = await res.json();
  if (!res.ok) throw Error(typeof value.detail === "string" ? value.detail : "입력값과 로봇 상태를 확인하세요.");
  return value;
}
const rows = () => [...samples.children].map(row => ({
  position:row.querySelector('[data-field="position"]').valueAsNumber,
  width_mm:row.querySelector('[data-field="width_mm"]').valueAsNumber
}));
function calibrationError(points) {
  if(points.length<2)return "실제 위치와 안쪽 간격을 두 점 이상 입력하세요.";
  if(points.length>701)return "교시점은 최대 701개까지 등록할 수 있습니다.";
  for(const [index,point] of points.entries()){
    if(!Number.isInteger(point.position)||point.position<0||point.position>700)return (index+1)+"번 교시점의 실제 위치를 0~700 정수로 입력하세요.";
    if(!Number.isFinite(point.width_mm)||point.width_mm<0)return (index+1)+"번 교시점의 안쪽 간격을 0 이상 mm로 입력하세요.";
  }
  const sorted = [...points].sort((a,b)=>a.position-b.position);
  for(let i=1;i<sorted.length;i++){
    if(sorted[i-1].position===sorted[i].position)return "실제 위치 "+sorted[i].position+"가 중복됩니다. 서로 다른 위치를 입력하세요.";
    if(sorted[i-1].width_mm<=sorted[i].width_mm)return "실제 위치가 커질수록 안쪽 간격은 작아져야 합니다.";
  }
  return "";
}
const valid=points=>!calibrationError(points);
function controls() {
  get('hand-contact-save').disabled=contactPending||locked||!contact;
  get('hand-contact-current').disabled=contactPending||locked;
  const ready = host?.gripper_supported && host.can_move && !locked && !busy;
  for (const button of document.querySelectorAll("[data-hand]")) button.disabled = !ready;
  get("hand-ready").textContent = !sdkMode ? "RH-P12 제어에는 Mac SDK가 필요합니다." :
    locked ? "로봇 작업 진행 중" : !host?.gripper_supported ? host?.detail || "RH-P12 지원 SDK 작업자 연결 대기" :
    host.motion_reason || (!host.gripper_feedback ? "SDK 준비 · 상태 읽기로 장치 연결을 확인하세요." :
      host.gripper_feedback.state === "completed" ? "RH-P12 최근 응답 확인 · 자동 운전" : "RH-P12 응답 또는 장치 상태를 확인하세요.");
  const reason=saving?"벌림표 저장 중입니다.":locked?"로봇 작업이 끝나면 저장할 수 있습니다.":!table?"벌림표를 불러오는 중입니다. 조회 실패 시 저장값 불러오기를 누르세요.":calibrationError(rows())||(!dirty?"저장된 값입니다. 값을 변경하면 저장할 수 있습니다.":"");
  get("hand-save").disabled = Boolean(reason);
  get("hand-save").title=reason||"입력한 실측 벌림표 저장";
  get("hand-table-validation").textContent=reason||"저장할 수 있습니다. 측정값을 확인한 뒤 벌림표 저장을 누르세요.";
  get("hand-add").disabled = saving || !table;
  get("hand-capture").disabled = saving || !host?.gripper_supported || host.gripper_feedback?.state !== "completed" ||
    !Number.isInteger(host.gripper_feedback.position) || !table;
  get("hand-reload").disabled = saving;
  testControls();
}
function row(position="", width="") {
  const item = document.createElement("div"); item.className="hand-sample";
  for (const [key,name,val] of [["position","실제 위치",position],["width_mm","안쪽 간격 (mm)",width]]) {
    const label=document.createElement("label"), input=document.createElement("input");
    input.type="number"; input.min=0; input.step=key==="position"?"1":"any";
    input.placeholder=key==="position"?"0–700":"실측 mm";
    if(key==="position") input.max=700;
    input.dataset.field=key; input.value=val; label.append(name+" ",input);item.append(label);
  }
  const remove=document.createElement("button"); remove.type="button";remove.textContent="삭제";
  remove.onclick=()=>{item.remove();dirty=true;controls();};
  item.append(remove);samples.append(item);
}
function fill(data) {
  invalidateTest();
  table=data;dirty=false;samples.replaceChildren();
  for(const p of data.points) row(p.position,p.width_mm);
  if(!data.points.length){row();row();}
  controls();
}
export async function loadHandTable(force=false) {
  if (dirty && !force) return;
  const version=++loading;
  try {
    const result=await api("table");
    if(version!==loading || (dirty && !force)) return;
    fill(result);
    get("hand-table-note").textContent = result.points.length ? "저장된 교시점 "+result.points.length+"개" : "등록된 교시점이 없습니다.";
  } catch(error) { get("hand-table-note").textContent=error.message; controls(); }
}
export function renderHand(status, access) {
  host=access; sdkMode=status?.config.access_backend==="mac-sdk";
  locked=Boolean(status?.pick_active || status?.pose_tests?.busy || status?.hand_busy);
  const f=host?.gripper_feedback;
  get("hand-target").textContent=host?.gripper_target ?? "—";
  get("hand-actual").textContent=Number.isInteger(f?.position) ? f.position+" · "+new Date(f.observed_at).toLocaleTimeString("ko-KR") : "—";
  get("hand-current").textContent=f?.position != null ? [f.current_ma,f.goal_current_ma,f.limit_ma].join(" / ")+" mA" : "—";
  get("hand-mode").textContent=f?.position != null ? f.mode+" · 오류 "+f.error : "—";
  get("hand-voltage").textContent=Number.isFinite(f?.input_voltage_v) ? f.input_voltage_v.toFixed(1)+" V" : "—";
  controls();
}
samples.addEventListener("input",()=>{dirty=true; controls();});
get("hand-add").onclick=()=>{row();dirty=true;controls();};
get("hand-capture").onclick=()=>{
  const empty=[...samples.children].find(item=>[...item.querySelectorAll('input')].every(input=>input.value===""));
  if(empty)empty.querySelector('[data-field="position"]').value=host.gripper_feedback.position;
  else row(host.gripper_feedback.position);
  dirty=true;controls();
};
get("hand-reload").onclick=()=>void loadHandTable(true);
get("hand-save").onclick=async()=>{
  const points=rows();
  if(get("hand-save").disabled || !valid(points) || saving || locked || !table) return;
  saving=true; samples.inert=true; controls();
  try { fill(await api("table","PUT",{revision:table.revision,points})); get("hand-table-note").textContent="벌림표를 저장했습니다."; }
  catch(error) { get("hand-table-note").textContent=error.message; }
  finally { saving=false; samples.inert=false; controls(); }
};
for(const button of document.querySelectorAll("[data-hand]")) button.onclick=async()=>{
  if(button.disabled || busy) return;
  const action=button.dataset.hand;
  const body={session_id:host.session_id,motion_token:host.motion_token,action};
  if(action==="position") {
    body.position=get("hand-position").valueAsNumber;
    if(!Number.isInteger(body.position) || body.position<0 || body.position>700) {
      get("hand-result").textContent="0~700의 정수 위치를 입력하세요.";return;
    }
  }
  invalidateTest();busy=true;controls();get("hand-result").textContent="장치 응답 대기 중";
  get("hand-test-result").textContent="장치 응답 대기 중";
  try {
    const result=await api("command","POST",body);
    get("hand-result").textContent=(result.state==="unknown"?"결과 미확인 · ":"")+result.message;
  } catch(error) { get("hand-result").textContent="명령 상태 확인 필요 · "+error.message; }
  finally { get("hand-test-result").textContent=get("hand-result").textContent; busy=false;host=null;controls(); }
};


function invalidateTest() {
  testVersion++; testTarget=null; clearTimeout(testExpiry);
  if(get("hand-test-dialog").open) get("hand-test-dialog").close();
}
function feedbackWidth(position) {
  for(let i=1;i<(table?.points.length ?? 0);i++) {
    const a=table.points[i-1],b=table.points[i];
    if(a.position<=position && position<=b.position)
      return a.width_mm+(b.width_mm-a.width_mm)*(position-a.position)/(b.position-a.position);
  }
  return null;
}
function testControls() {
  const calibrated=table && valid(table.points);
  const ready=sdkMode && host?.gripper_supported && host.can_move && !locked && !busy && !testPending;
  const width=get("hand-test-width").valueAsNumber;
  get("hand-test-preview").disabled=!calibrated || dirty || saving || busy || testPending || !Number.isFinite(width) || width<0;
  get("hand-test-read").disabled=!ready;
  get("hand-test-range").textContent=calibrated ? `저장된 교시 범위 ${table.points.at(-1).width_mm}–${table.points[0].width_mm} mm` : "벌림표를 두 점 이상 교시하고 저장하세요.";
  const reason=dirty ? "벌림표 변경사항을 저장하거나 저장값을 불러온 뒤 테스트하세요." :
    !ready ? get("hand-ready").textContent || "장치 준비 상태 확인 중" : "실제 구동 가능 · 미리보기 후 승인 필요";
  get("hand-test-state").textContent=reason;
  get("hand-test-confirm-state").textContent=ready ? "" : reason;
  get("hand-test-confirm").disabled=!ready || !testTarget || dirty || saving ||
    testTarget.session_id!==host?.session_id || testTarget.motion_token!==host?.motion_token || Date.now()>testTarget.expires;
  const f=host?.gripper_feedback;
  const measured=Number.isInteger(f?.position) && f.state==="completed" ? feedbackWidth(f.position) : null;
  get("hand-test-feedback").textContent=Number.isInteger(f?.position) ?
    `명령 위치 ${host.gripper_target ?? '—'} / 실제 위치 ${f.position} · 벌림표 기준 추정 ${measured==null?'계산 불가':measured.toFixed(1)+' mm'} · ${new Date(f.observed_at).toLocaleTimeString('ko-KR')}` : "실제 위치 응답 없음 · 상태 읽기로 확인하세요.";
}
get("hand-test-width").oninput=()=>{invalidateTest();get("hand-test-result").textContent="";controls();};
get("hand-test-preview").onclick=async()=>{
  if(get("hand-test-preview").disabled)return;
  invalidateTest(); const version=testVersion;
  const body={width_mm:get("hand-test-width").valueAsNumber,revision:table.revision};
  testPending=true;controls();get("hand-test-result").textContent="벌림 계산 중";
  try {
    const target=await api("test/preview","POST",body);
    if(version!==testVersion || dirty || get("plane-hand-test").hidden)return;
    testTarget={...body,session_id:host?.session_id,motion_token:host?.motion_token,expires:Date.now()+30000};
    get("hand-test-target").textContent=`요청 벌림 ${target.width_mm} mm → 명령 위치 ${target.position} · 예상 벌림 ${target.estimated_width_mm.toFixed(1)} mm (더 열린 쪽으로 반올림)`;
    get("hand-test-result").textContent=get("hand-test-target").textContent;
    get("hand-test-dialog").showModal();
    testExpiry=setTimeout(()=>{invalidateTest();get("hand-test-result").textContent="승인 시간이 만료되었습니다. 다시 미리보기하세요.";controls();},30000);
  } catch(error) {get("hand-test-result").textContent=error.message;}
  finally {testPending=false;controls();}
};
get("hand-test-cancel").onclick=()=>get("hand-test-dialog").close();
get("hand-test-dialog").addEventListener("close",()=>{testTarget=null;clearTimeout(testExpiry);});
get("hand-test-confirm").onclick=async()=>{
  if(get("hand-test-confirm").disabled || !testTarget)return;
  const {expires,...body}=testTarget;
  invalidateTest(); busy=true;controls();get("hand-test-result").textContent="실제 그리퍼 응답 대기 중";
  try {
    const result=await api("test","POST",body);
    get("hand-test-result").textContent=(result.state==="unknown"?"결과 미확인 · ":"")+result.message;
  } catch(error) {get("hand-test-result").textContent="명령 결과 확인 필요 · "+error.message;}
  finally {busy=false;host=null;controls();}
};
get("hand-test-read").onclick=()=>document.querySelector('[data-hand="read"]').click();
