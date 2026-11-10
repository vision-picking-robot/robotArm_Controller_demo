import { armTelemetry, armFrames, framePose, gripperVisualState, planArmSimulation, planScenarioSimulation, sampleArmSimulation, poseFrame, multiply, inverseRigid, defaultArmJoints, simulationToolPose, exampleScenarioGuides } from "./robot-arm.js";
import { connectArmRenderer } from "./robot-arm-renderer.js";

export function connectRobotArm(el,signal,onOrbit) {
  const root=el.armView,ui=Object.fromEntries([...root.querySelectorAll("[data-arm]")].map(n=>[n.dataset.arm,n]));
  const renderer=connectArmRenderer(ui.canvas,ui.labels,ui.graphics,signal,onOrbit);
  let active=false,data=null,arrived=performance.now(),sample=armTelemetry(null),guides=null,parcelBox=null,selected=2,tcp=null,tcpAt=0;
  let pending=null,lastRead=0,source="",signature="",tcpMessage="수신 대기",compact;
  let simulation=null,simulationRate=1;
  let scenarioPreview=null,scenarioVisible=false;
  const exampleGuides=exampleScenarioGuides(),exampleTcp=framePose(multiply(armFrames(defaultArmJoints)[6],poseFrame(simulationToolPose)));
  const simulationObservers=new Set();
  const simulationState=()=>simulation?{state:simulation.state,progress:simulation.frame?.progress||0,message:simulation.message,route:simulation.route,step:(simulation.frame?.segment||0)+1}:
    {state:"idle",progress:0,message:"각 지점을 입력한 뒤 시뮬레이션을 시작하세요."};
  const notifySimulation=()=>{const state=simulationState();for(const fn of simulationObservers)fn(state);};
  function stopSimulation(){
    if(!simulation)return;
    simulation.controller.abort();cancelAnimationFrame(simulation.raf);simulation=null;
    notifySimulation();publish();
  }
  function animateSimulation(job) {
    job.last=performance.now();
    const step=now=>{
      if(simulation!==job||job.state!=="running")return;
      job.elapsed+=Math.min(.1,Math.max(0,(now-job.last)/1000))*simulationRate;job.last=now;
      const gate=job.plan.gates?.find(g=>!job.handled.has(g.action)&&job.elapsed>=g.time);
      if(gate){job.elapsed=gate.time;job.state=gate.action==="confirm-grip"?"awaiting_grip":"awaiting_open";}
      job.frame=sampleArmSimulation(job.plan,job.elapsed);
      const current=job.recipe?.steps[job.frame.segment],verb=current?.kind==='gripper'?'그리퍼 동작 중':current?.kind==='target'?'가상 대상 확인 중':'이동 중';
      job.message=`${job.frame.segment+1}/${job.plan.route.length} · ${job.plan.route[job.frame.segment].title} ${job.frame.complete?"도착 · 시뮬레이션 완료":verb}`;
      if(gate)job.message=gate.action==="confirm-grip"?"6/17 · 가상 접촉 · 파지 확인 후 이송을 누르세요.":"11/17 · 박스 도착 · 박스에서 열기를 누르세요.";
      if(job.frame.complete)job.state="complete";
      publish();
      if(now-(job.notified||0)>100||job.frame.complete||gate){job.notified=now;notifySimulation();}
      if(job.state==="running")job.raf=requestAnimationFrame(step);
    };
    job.raf=requestAnimationFrame(step);
  }
  async function simulate(route,toolPose,recipe=null) {
    if(!active||!attached)return;
    stopSimulation();
    const seed=[...(recipe?.example?defaultArmJoints:sample.angles)],tool=poseFrame(toolPose),controller=new AbortController();
    const job={state:"planning",controller,route,recipe,handled:new Set(),frame:{angles:seed,tcp:framePose(multiply(armFrames(seed)[6],tool)),progress:0,opening_mm:recipe?.idle_opening_mm??null},message:"시뮬레이션 경로와 공구 방향을 계산하고 있습니다.",elapsed:0};
    simulation=job;cancelTcp("시뮬레이션 중");notifySimulation();publish();
    try {
      const options={toolPose,signal:AbortSignal.any([signal,controller.signal])};
      const plan=recipe?await planScenarioSimulation(seed,recipe,options):await planArmSimulation(seed,route,options);
      if(simulation!==job||signal.aborted)return;
      job.plan=plan;job.route=plan.route;job.frame=sampleArmSimulation(plan,0);job.state=document.hidden?"paused":"running";
      job.message=(document.hidden?"시뮬레이션 일시정지 · ":"시뮬레이션 재생 중 · ")+route[0].title;
      notifySimulation();publish();
      if(job.state==="running"){
        animateSimulation(job);root.closest(".gripper-panel").scrollIntoView({block:"start",behavior:"smooth"});
      }
    }catch(error){
      if(simulation!==job||signal.aborted)return;
      job.state="error";job.frame=null;job.message=error.message;
      notifySimulation();publish();
    }
  }
  function simulationAction(action,value) {
    if(action==="rate"){if([.5,1,2,5,10,20].includes(value))simulationRate=value;return;}
    if(action==="stop"){stopSimulation();return;}
    const job=simulation;if(!job?.plan)return;
    if((action==="confirm-grip"&&job.state==="awaiting_grip")||(action==="open-at-box"&&job.state==="awaiting_open")){
      job.handled.add(action);job.state="running";animateSimulation(job);
    }else if(action==="pause"&&job.state==="running"){
      cancelAnimationFrame(job.raf);job.state="paused";job.message="시뮬레이션 일시정지 · "+job.plan.route[job.frame.segment].title;
    }else if(action==="resume"&&job.state==="paused"){
      job.state="running";animateSimulation(job);
    }else if((action==="seek"||action==="step")&&Number.isFinite(value)){
      const endpoint=action==='step'?job.plan.samples.findLast(s=>s.segment===value-1):null;
      if(action==='step'&&!endpoint)return;
      cancelAnimationFrame(job.raf);job.elapsed=endpoint?.time??Math.max(0,Math.min(1,value))*job.plan.duration;job.frame=sampleArmSimulation(job.plan,job.elapsed);
      const gate=job.plan.gates?.find(g=>Math.abs(g.time-job.elapsed)<1e-6);
      for(const g of job.plan.gates||[])if(g.time<job.elapsed-1e-6)job.handled.add(g.action);else job.handled.delete(g.action);
      job.state=job.frame.complete?"complete":gate?gate.action==='confirm-grip'?'awaiting_grip':'awaiting_open':"paused";
      job.message=gate?gate.action==='confirm-grip'?'6/17 · 가상 접촉 · 파지 확인 후 이송을 누르세요.':'11/17 · 박스 도착 · 박스에서 열기를 누르세요.':(job.frame.complete?"시뮬레이션 완료":"시뮬레이션 위치 확인 · ")+job.plan.route[job.frame.segment].title;
    }
    notifySimulation();publish();
  }
  ui.simulationPause.addEventListener("click",()=>simulationAction(simulation?.state==="paused"?"resume":"pause"),{signal});
  ui.simulationStop.addEventListener("click",()=>simulationAction("stop"),{signal});
  ui.simulationConfirm.addEventListener("click",()=>simulationAction(simulation?.state==="awaiting_grip"?"confirm-grip":"open-at-box"),{signal});
  let attached=false,handAccess=null,handStatus=null,table=null,tableRequest=null,lastTableRead=-Infinity,tableError="",handSignature="",readRequest=null,readNote="",readBaseline=null;
  const handState=()=>gripperVisualState(handAccess,table,{sdk:handStatus?.config?.access_backend==="mac-sdk",busy:Boolean(readRequest||handStatus?.hand_busy)});
  function cancelHandRequests(){tableRequest?.abort();tableRequest=null;readRequest?.abort();readRequest=null;}
  async function readTable(){
    if(!active||!attached||document.hidden||tableRequest||performance.now()-lastTableRead<5000)return;
    const controller=new AbortController();tableRequest=controller;lastTableRead=performance.now();
    const timeout=setTimeout(()=>controller.abort(),4000);
    try{
      const res=await fetch("/api/robot/gripper/table",{cache:"no-store",signal:AbortSignal.any([signal,controller.signal])});
      const value=await res.json();if(!res.ok)throw Error();
      if(tableRequest===controller){table=value;tableError="";}
    }catch{if(tableRequest===controller){table=null;tableError="실측 벌림표를 조회하지 못했습니다.";}}
    finally{clearTimeout(timeout);if(tableRequest===controller){tableRequest=null;publish();}}
  }
  function canReadHand(){return active&&attached&&!simulation?.frame&&!readRequest&&handStatus?.config?.access_backend==="mac-sdk"&&
    handAccess?.connection==="connected"&&handAccess.gripper_supported&&handAccess.can_move&&handAccess.session_id&&handAccess.motion_token&&
    !handStatus.pick_active&&!handStatus.hand_busy&&!handStatus.pose_tests?.busy;}
  ui.handRead.addEventListener("click",async()=>{
    if(!canReadHand())return;
    const controller=new AbortController();readRequest=controller;readBaseline=handAccess.gripper_feedback?.observed_at;readNote="상태를 읽고 있습니다.";publish();
    const timeout=setTimeout(()=>controller.abort(),12000);
    try{
      const res=await fetch("/api/robot/gripper/command",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({session_id:handAccess.session_id,motion_token:handAccess.motion_token,action:"read"}),signal:AbortSignal.any([signal,controller.signal])});
      const value=await res.json();if(!res.ok)throw Error(typeof value.detail==="string"?value.detail:"그리퍼 상태를 확인하지 못했습니다.");
      if(readRequest===controller)readNote=value.state==="completed"?"상태 읽기 완료 · 최신 피드백 수신을 기다립니다.":value.message||"그리퍼 결과 미확인";
    }catch(error){if(readRequest===controller)readNote=error instanceof TypeError||error.name==="AbortError"?"상태 읽기 응답을 확인하지 못했습니다.":error.message;}
    finally{clearTimeout(timeout);if(readRequest===controller){readRequest=null;publish();}}
  },{signal});
  const buttons=Array.from({length:6},(_,i)=>{
    const b=document.createElement("button"),name=document.createElement("span"),value=document.createElement("strong");
    b.type="button";name.textContent="J"+(i+1);b.append(name,value);
    b.addEventListener("click",()=>{selected=i;publish();},{signal});ui.joints.append(b);return b;
  });
  const poseFields={};
  for(const key of ["flange","tcp"])poseFields[key]=[..."XYZABC"].map(axis=>{
    const row=document.createElement("div"),dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=axis;dd.textContent="—";row.append(dt,dd);ui[key].append(row);return dd;
  });
  const mm=v=>Number.isFinite(v)?(Math.abs(v)<.05?"0.0":v.toFixed(1)):"—";
  function cancelTcp(reason) {pending?.abort();pending=null;tcp=null;tcpAt=0;tcpMessage=reason;}
  function publish() {
    if(!active)return;
    const preview=simulation?.frame,sceneRecipe=simulation?.recipe||(scenarioVisible?scenarioPreview:null),exampleContext=Boolean(sceneRecipe?.example),idleExample=exampleContext&&!preview;
    const display=preview?{angles:preview.angles,live:false,reason:"시뮬레이션 · 실제 기기 명령 없음"}:idleExample?{angles:defaultArmJoints,live:false,reason:'예시 기본 자세 · 실제 기기 명령 없음'}:sample;
    const shownTcp=preview?preview.tcp:idleExample?exampleTcp:tcp,displayBox=exampleContext?sceneRecipe.box:parcelBox;
    ui.canvas.dataset.example=exampleContext;
    ui.simulation.hidden=!preview;
    root.dataset.scenarioPlayback=Boolean(preview&&simulation.recipe);
    ui.simulationStage.textContent=preview?simulation.message:"";
    ui.simulationMetrics.hidden=!preview||!simulation.recipe;
    ui.simulationMetrics.textContent=preview&&simulation.recipe?`가상 벌림 ${mm(preview.opening_mm)} mm · 상품 ${preview.holding?'파지 중':preview.released?'박스에 놓임':'파지 전'}`:'';
    ui.simulationPause.disabled=!["running","paused"].includes(simulation?.state);
    ui.simulationPause.textContent=simulation?.state==="paused"?"계속 재생":"일시정지";
    ui.simulationConfirm.hidden=!["awaiting_grip","awaiting_open"].includes(simulation?.state);
    ui.simulationConfirm.textContent=simulation?.state==="awaiting_grip"?"가상 파지 확인 후 이송":"가상 박스에서 열기";
    ui.source.textContent=exampleContext?'예시 경로 · 실측 아님':preview?"시뮬레이션":sample.live?"실제 관절 수신 중":"기본 자세 · 실측 아님";ui.source.dataset.live=display.live;
    const hint=preview||idleExample?display.reason:sample.reason+(sample.live?" · BASE 원점 고정":" [0, 0, 90, 0, 90, 0]°");
    if(el.modelHint.textContent!==hint)el.modelHint.textContent=hint;
    buttons.forEach((b,i)=>{b.setAttribute("aria-pressed",String(i===selected));b.querySelector("strong").textContent=mm(display.angles[i])+"°";b.setAttribute("aria-label","J"+(i+1)+" 관절 "+mm(display.angles[i])+"도 강조");});
    ui.array.textContent="["+display.angles.map(v=>Number(v.toFixed(1))).join(", ")+"]°";
    ui.received.textContent=preview||idleExample?"시뮬레이션 관절각 · 실측 아님":sample.live&&sample.receivedAt?"관절 수신 · "+new Date(sample.receivedAt).toLocaleTimeString("ko-KR",{hour12:false}):"기본 관절각 · 실측 자세 아님";
    const flange=framePose(armFrames(display.angles)[6]);
    poseFields.flange.forEach((n,i)=>n.textContent=mm(flange[i]));poseFields.tcp.forEach((n,i)=>n.textContent=mm(shownTcp?.[i]));
    ui.tcpTitle.textContent=preview||idleExample?"시뮬레이션 TCP":"실측 TCP";
    ui.tcpState.textContent=preview||idleExample?"모형 계산값":tcp?"조회 "+new Date(tcpAt).toLocaleTimeString("ko-KR",{hour12:false}):tcpMessage;
    ui.canvas.setAttribute("aria-label","E0509 6축 매니퓰레이터. "+display.reason+". 관절각 "+display.angles.map(mm).join(", ")+"도. BASE 원점 고정. "+(preview||idleExample?"시뮬레이션 TCP와 공구 방향 표시.":tcp?"실측 TCP와 공구 방향 표시.":"TCP 수신 없음. 모델 플랜지 방향 표시."));
    ui.parcelBox.hidden=!displayBox;
    if(displayBox){
      ui.boxTitle.textContent=(exampleContext?'예시 택배 박스':"택배 박스")+(displayBox.draft?" · 저장 전":"");
      ui.boxSize.textContent="가로 × 세로 × 높이 · "+displayBox.size_mm.join(" × ")+" mm";
      ui.boxCenter.textContent="바닥면 중심 · BASE ("+displayBox.center_base_mm.join(", ")+") mm";
      ui.canvas.setAttribute("aria-label",ui.canvas.getAttribute("aria-label")+" "+ui.boxSize.textContent+". "+ui.boxCenter.textContent+".");
    }
    const actualHand=attached?handState():null;
    ui.hand.hidden=!attached||exampleContext;
    const hand=actualHand&&(preview&&simulation.recipe||idleExample)?{...actualHand,opening_mm:idleExample?sceneRecipe.idle_opening_mm:Number.isFinite(preview.opening_mm)?preview.opening_mm:null}:actualHand;
    if(actualHand){
      const hand=actualHand;
      if(ui.handStatus.textContent!==hand.reason)ui.handStatus.textContent=hand.reason;
      ui.handStatus.dataset.known=Number.isFinite(hand.opening_mm);
      ui.handTarget.textContent=hand.target??"—";ui.handPosition.textContent=hand.position??"—";
      ui.handOpening.textContent=Number.isFinite(hand.opening_mm)?hand.opening_mm.toFixed(1)+" mm":"—";
      ui.handObserved.textContent=hand.observedAt?"관측 "+new Date(hand.observedAt).toLocaleTimeString("ko-KR",{hour12:false})+" · "+hand.ageSeconds+"초 전":"실측 피드백 없음";
      ui.handNote.textContent=readNote||tableError;ui.handRead.disabled=!canReadHand();
      ui.canvas.setAttribute("aria-label",ui.canvas.getAttribute("aria-label")+" 사진 기준 그리퍼 장착. "+hand.reason+". "+(hand.opening_mm===null?"벌림 미확인.":"실측 벌림 "+hand.opening_mm.toFixed(1)+" mm."));
    }
    let product=null;const recipe=sceneRecipe;
    if(recipe){
      let frame=poseFrame([...recipe.product.center_base_mm,0,0,0]);
      if(preview?.holding){const relative=multiply(inverseRigid(poseFrame(recipe.pose)),frame);frame=multiply(poseFrame(preview.tcp),relative);}
      else if(preview?.released){frame=poseFrame([...(recipe.steps[10].target?.slice(0,3)||recipe.product.center_base_mm),0,0,0]);
        const box=recipe.box;if(box?.center_base_mm&&Math.abs(frame[12]-box.center_base_mm[0])<box.size_mm[0]/2&&Math.abs(frame[13]-box.center_base_mm[1])<box.size_mm[1]/2)frame[14]=box.center_base_mm[2]+recipe.product.size_mm[2]/2;}
      product={frame,size:recipe.product.size_mm,region:recipe.region,initial:recipe.product.center_base_mm,approach:recipe.approach,pick:recipe.pose,holding:Boolean(preview?.holding),released:Boolean(preview?.released)};
    }
    ui.canvas.dataset.simulatedOpening=preview&&simulation.recipe?preview.opening_mm??"":idleExample?sceneRecipe.idle_opening_mm:'';
    renderer.update({angles:display.angles,live:display.live,tcp:shownTcp,selected,guides:exampleContext?exampleGuides:guides,gripper:hand,parcelBox:displayBox,product,
      simulation:preview?{state:simulation.state,segment:preview.segment,...(simulation.plan||{})}:null});
  }
  async function readTcp() {
    if(!active||simulation?.frame||scenarioVisible&&scenarioPreview?.example||!sample.live||pending||document.hidden||performance.now()-lastRead<1800)return;
    const controller=new AbortController(),key=source;pending=controller;lastRead=performance.now();
    const timeout=setTimeout(()=>controller.abort(),4000);
    try {
      const response=await fetch("/api/robot/work-points/current",{cache:"no-store",signal:AbortSignal.any([signal,controller.signal])});
      const result=await response.json();
      if(!response.ok||!Array.isArray(result.pose)||result.pose.length!==6||!result.pose.every(Number.isFinite))throw Error("TCP unavailable");
      if(pending!==controller||signal.aborted||!active||simulation?.frame||key!==source||!armTelemetry(data,(performance.now()-arrived)/1000).live)return;
      tcp=result.pose;tcpAt=Date.now();tcpMessage="";
    } catch {
      if(pending===controller){tcp=null;tcpAt=0;tcpMessage="실측 TCP 조회 불가";}
    } finally {
      clearTimeout(timeout);if(pending===controller){pending=null;publish();}
    }
  }
  function tick() {
    if(!active)return;
    const next=armTelemetry(document.hidden?null:data,(performance.now()-arrived)/1000),changed=JSON.stringify(next)!==signature;
    sample=next;signature=JSON.stringify(next);
    if(!sample.live&& (tcp||pending||tcpMessage!=="수신 없음")){cancelTcp("수신 없음");publish();}
    if(tcp&&Date.now()-tcpAt>4500){tcp=null;tcpAt=0;tcpMessage="실측 TCP 수신 지연";publish();}
    if(changed)publish();
    if(attached){const next=JSON.stringify(handState());if(next!==handSignature){handSignature=next;publish();}void readTable();}
    void readTcp();
  }
  const interval=setInterval(tick,250);
  const observer=new ResizeObserver(()=>{
    const next=root.clientWidth<650||innerHeight<540;
    if(next!==compact){compact=next;ui.readings.open=!compact;}
  });observer.observe(root);
  document.addEventListener("visibilitychange",()=>{if(document.hidden){simulationAction("pause");data=null;handAccess=null;cancelHandRequests();cancelTcp("화면 비활성");tick();}},{signal});
  signal.addEventListener("abort",()=>{stopSimulation();clearInterval(interval);cancelHandRequests();cancelTcp("수신 없음");observer.disconnect();},{once:true});
  return {
    active(value){active=value;root.hidden=!value;if(!value){stopSimulation();cancelHandRequests();cancelTcp("수신 대기");}else{tick();publish();}},
    setAttached(value){attached=value;ui.hand.hidden=!value;if(!value){stopSimulation();cancelHandRequests();readNote="";}else lastTableRead=-Infinity;tick();publish();},
    simulate,simulationAction,
    simulateRecipe(recipe,toolPose){return simulate(recipe.steps.map(s=>({title:s.title})),toolPose,recipe);},
    setScenarioPreview(value){scenarioPreview=value;publish();},
    setScenarioVisible(value){scenarioVisible=value;if(!value&&simulation?.recipe)stopSimulation();publish();},
    observeSimulation(fn){simulationObservers.add(fn);fn(simulationState());return()=>simulationObservers.delete(fn);},
    gripperStatus(status,access){
      handStatus=status;handAccess=access;
      if(!readRequest&&readNote.startsWith("상태 읽기 완료")&&access?.gripper_feedback?.observed_at!==readBaseline&&access?.gripper_feedback?.state==="completed")readNote="";
      tick();if(attached)publish();
    },
    robotStatus(value){
      const key=JSON.stringify([value?.config?.ip,value?.config?.port,value?.config?.namespace,value?.display_joint_topic]);
      if(key!==source){cancelTcp("수신 대기");source=key;lastRead=0;}
      data=value;arrived=performance.now();tick();
    },
    setGuides(value){guides=value;publish();},
    setParcelBox(value){parcelBox=value;publish();},
    setView:renderer.setView,getView:renderer.getView
  };
}
