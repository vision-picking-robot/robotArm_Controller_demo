export const destinations = [
  { id: "dashboard", label: "대시보드", icon: "layout-dashboard" },
  { id: "orders", label: "주문·배송", icon: "package-check" },
  { id: "robot", label: "로봇 모니터링", icon: "robot-arm" },
  { id: "people", label: "사용자 관리", icon: "contact" },
  { id: "stock", label: "재고 관리", icon: "boxes" },
  { id: "model-management", label: "모델 관리", icon: "scan-line" },
  { id: "logs", label: "로그", icon: "scroll-text" }
];

const robotPages = [
  {id:"manipulator", label:"매니퓰레이터", icon:"robot-arm"},
  {id:"gripper", label:"그리퍼", icon:"settings"},
  {id:"work-plane", label:"작업 평면 보정", icon:"scan-line"}
];

export function icon(name) {
  return '<svg class="station-icon" aria-hidden="true"><use href="/static/station-icons.svg#' + name + '"></use></svg>';
}

const training = document.body.dataset.workspace === "training";

export function currentDestination() {
  if (document.body.dataset.workspace === "robot") {
    const id = location.hash.slice(1);
    if (["work-points-title","parcel-box-title","product-grasp-title","pose-title"].includes(id)) return robotPages[0];
    if (["gripper-table","gripper-test"].includes(id)) return robotPages.find(page => page.id === "gripper");
    return robotPages.find(page => page.id === id) || destinations.find(page => page.id === "robot");
  }
  if (training) return destinations.find(item => item.id === "model-management");
  const route = location.hash.slice(1);
  return destinations.find(item => item.id === route) || destinations[0];
}

function highlightDestination() {
  const current = currentDestination();
  if (robotPages.some(page => page.id === current.id)) expand(true);
  for (const link of document.querySelectorAll(".station-nav a")) {
    if (link.dataset.page === current.id) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

const rail = document.getElementById("station-rail");
const nav = document.createElement("nav");
nav.className = "station-nav";
nav.setAttribute("aria-label", "주 메뉴");
const toggle = document.createElement("button"), children = document.createElement("div");
toggle.type = "button"; toggle.className = "station-disclosure";
toggle.innerHTML = icon("robot-arm") + "로봇 관리";
children.id = "robot-nav-children"; children.className = "station-children";
toggle.setAttribute("aria-controls",children.id);
function expand(open) {
  toggle.setAttribute("aria-expanded", String(open));
  children.hidden = !open;
}
expand(false);
toggle.addEventListener("click", () => expand(children.hidden));
for (const page of robotPages) {
  const link = document.createElement("a");
  link.dataset.page = page.id; link.href = "/robot#" + page.id; link.textContent = page.label;
  children.append(link);
}
for (const item of destinations) {
  if (item.id === "dashboard" || item.id === "people") {
    const group = document.createElement("div");
    group.className = "nav-group";
    group.textContent = item.id === "dashboard" ? "운영" : "관리";
    nav.append(group);
  }
  if (item.id === "people") nav.append(toggle,children);
  const link = document.createElement("a");
  link.href = item.id === "robot" ? "/robot" : "/#" + item.id;
  link.dataset.page = item.id;
  link.innerHTML = icon(item.icon) + item.label;
  nav.append(link);
}
rail.innerHTML = '<div class="station-brand"><span class="brand-mark">' + icon("scan-line") +
  '</span><div>Vision Lab<small>LOCAL WORKSPACE</small></div></div>';
rail.append(nav);
const mode = document.createElement("div");
mode.className = "rail-status";
mode.textContent = training ? "로컬 모델 학습" : document.body.dataset.workspace === "robot" ? "E0509 · 유선 LAN" : "패킹 시뮬레이션";
const controls=document.createElement('div');controls.className='rail-controls';
const stop=document.createElement('button');stop.type='button';stop.id='robot-global-stop';stop.className='rail-stop';stop.textContent='■ 중단';stop.setAttribute('aria-label','로봇 작업 중단');
const stopState=document.createElement('p');stopState.className='rail-stop-state';stopState.setAttribute('role','status');stopState.hidden=true;
const standby=document.createElement('button');standby.type='button';standby.id='robot-standby';standby.className='rail-standby';standby.textContent='STANDBY 복구';
const standbyState=document.createElement('p');standbyState.className='rail-standby-state';standbyState.setAttribute('role','status');standbyState.hidden=true;
const recoveryDialog=document.createElement('dialog');recoveryDialog.id='robot-standby-dialog';recoveryDialog.className='rail-recovery-dialog';recoveryDialog.setAttribute('aria-labelledby','robot-standby-title');
recoveryDialog.innerHTML='<h2 id="robot-standby-title">STANDBY로 복구할까요?</h2><p id="robot-standby-description"></p><p>정지 원인을 해소하고 작업 공간과 제어기 알람을 확인하세요. 중단된 작업은 자동 재개하지 않습니다.</p><form method="dialog"><button value="cancel" autofocus>취소</button><button id="robot-standby-confirm" value="confirm">STANDBY 전환 요청</button></form>';
recoveryDialog.setAttribute('aria-describedby','robot-standby-description');document.body.append(recoveryDialog);
controls.append(standby,standbyState,stop,stopState,mode);rail.append(controls);
let recoveryTicket=null;
const recoveryMessage=message=>{standbyState.hidden=false;standbyState.textContent=message;};
async function accessState(){
  const response=await fetch('/api/robot/host-access/status',{cache:'no-store',signal:AbortSignal.timeout(5000)});
  const state=await response.json();
  if(!response.ok)throw Error(typeof state.detail==='string'?state.detail:'SDK 상태를 확인할 수 없습니다.');
  return state;
}
standby.addEventListener('click',async()=>{
  if(standby.disabled)return;
  standby.disabled=true;recoveryMessage('제어기 상태 확인 중…');
  try{
    const state=await accessState();
    if(state.robot_state==='STANDBY'){recoveryMessage('현재 STANDBY · 준비 완료');return;}
    if(!state.servo?.token)throw Error(state.servo?.message||'STANDBY 전환을 요청할 수 없습니다.');
    recoveryTicket={session_id:state.session_id,servo_token:state.servo.token};
    document.getElementById('robot-standby-description').textContent=state.robot_state==='SAFE_STOP'
      ? '현재 SAFE_STOP · 안전 정지를 해제하고 STANDBY 전환을 요청합니다.'
      : '현재 SAFE_OFF · 관절 서보 전원을 켜고 STANDBY 전환을 요청합니다.';
    recoveryDialog.returnValue='cancel';recoveryDialog.showModal();
  }catch(error){recoveryMessage(error.message);}
  finally{if(!recoveryDialog.open)standby.disabled=false;}
});
recoveryDialog.addEventListener('close',async()=>{
  const ticket=recoveryTicket;recoveryTicket=null;
  if(recoveryDialog.returnValue!=='confirm'||!ticket){standby.disabled=false;recoveryMessage('STANDBY 전환 요청을 취소했습니다.');return;}
  recoveryMessage('STANDBY 전환 요청 중…');
  try{
    const response=await fetch('/api/robot/host-access/servo-on',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(ticket),signal:AbortSignal.timeout(5000)});
    let state=await response.json();
    if(!response.ok)throw Error(typeof state.detail==='string'?state.detail:'STANDBY 전환 요청에 실패했습니다.');
    for(let attempt=0;attempt<30;attempt++){
      if(state.session_id!==ticket.session_id)throw Error('SDK 세션이 변경되었습니다. 현재 제어기 상태를 확인하세요.');
      if(state.robot_state==='STANDBY'&&state.servo?.result==='confirmed'){recoveryMessage('STANDBY 복구 완료 · 실행 전 운전 모드를 확인하세요.');return;}
      if(['failed','unknown'].includes(state.servo?.result))throw Error(state.servo.message);
      await new Promise(resolve=>setTimeout(resolve,500));state=await accessState();
    }
    throw Error('STANDBY 전환 결과 미확인 · 제어기 상태와 알람을 확인하세요.');
  }catch(error){recoveryMessage(error.message||'STANDBY 전환 결과를 확인할 수 없습니다.');}
  finally{standby.disabled=false;}
});
window.addEventListener('robot-global-stop',()=>{if(recoveryDialog.open){recoveryDialog.returnValue='cancel';recoveryDialog.close();}});
stop.addEventListener('click',async()=>{
  if(stop.disabled)return;
  stop.disabled=true;stopState.hidden=false;stopState.textContent='중단 요청 중…';
  window.dispatchEvent(new Event('robot-global-stop'));
  try{
    const response=await fetch('/api/robot/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(5000)});
    const result=await response.json();
    if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'중단 요청에 실패했습니다.');
    stopState.textContent=result.message||'중단을 요청했습니다.';
  }catch(error){stopState.textContent=error.message||'중단 응답을 확인할 수 없습니다.';}
  finally{stop.disabled=false;}
});
highlightDestination();
window.addEventListener("hashchange", highlightDestination);
