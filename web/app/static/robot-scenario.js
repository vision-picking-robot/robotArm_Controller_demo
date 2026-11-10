import {api} from './robot-camera-frame.js';
import {armTelemetry,simulationToolPose,poseFrame,endpointIK} from './robot-arm.js?v=joint-targets';

const stages=[['ready','대기 상태로 준비'],['target','YOLO 상품 요청'],['joint','상품 접근 위치'],['gripper','권장 폭으로 벌리기'],['line','파지점으로 접근'],['gripper','접촉 확인·파지 유지'],['line','접근 위치로 후퇴'],['joint','1차 경유점'],['joint','J1 회전·2차 경유점'],['joint','박스 위 위치'],['line','놓기 위치로 하강'],['gripper','파지 전 벌림으로 개방'],['line','박스 위로 상승'],['joint','2차 경유점 복귀'],['joint','J1 역회전·1차 경유점'],['joint','대기 자세 복귀'],['gripper','대기 벌림 복원']];
const phases=[['준비',1,2],['파지',3,6],['이송',7,10],['놓기',11,13],['복귀',14,17]];
const regionNames={floor:'바닥 · 수직 파지',shelf:'선반 · 수평 파지'};
const kinds={ready:'대기 준비',target:'대상 선택',joint:'관절 이동 · movej',line:'직선 이동 · movel',gripper:'그리퍼'};
const armSteps=new Set([1,3,5,7,8,9,10,11,13,14,15,16]),fixedMotions={5:'movel',7:'movel',9:'movej',11:'movel',13:'movel',15:'movej'};
const motionNames={movej:'관절 이동 · movej',movel:'직선 이동 · movel',movec:'원호 이동 · movec'};
const guidance={1:'실제 작업은 별도 승인으로 대기 자세와 벌림을 먼저 준비합니다.',2:'실제 상품은 대시보드의 YOLO 영상에서 선택합니다. 여기서는 가상 상품을 사용합니다.',3:'상품을 직선 이동만으로 파지할 수 있는 접근 위치로 이동합니다.',4:'상품 폭에 양쪽 여유를 더한 간격으로 벌립니다.',5:'바닥은 Z 방향으로 하강하고, 선반은 수평으로 접근합니다.',6:'가상 접촉 후 정지합니다. 파지 확인을 눌러야 다음 단계로 진행합니다.',7:'접근할 때 사용한 동일 위치로 직선 후퇴합니다.',8:'직접 교시한 1차 경유점으로 이동합니다.',9:'J2~J6를 유지하고 J1만 설정한 방향으로 180° 회전합니다.',10:'박스 놓기 TCP에서 Z에 상부 간격을 더한 위치입니다.',11:'X·Y와 공구 방향을 유지해 하강합니다. 박스에서 열기 확인을 기다립니다.',12:'4단계에서 사용한 벌림으로 개방하고 가상 상품을 놓습니다.',13:'개방을 확인한 뒤 같은 X·Y에서 박스 위로 상승합니다.',14:'2차 경유점으로 돌아갑니다.',15:'J1만 반대 방향으로 180° 회전해 1차 경유점으로 돌아갑니다.',16:'1단계의 대기 관절각으로 복귀합니다.',17:'대기 벌림을 복원하고 시뮬레이션을 마칩니다.'};
let saved=null,draft=null,dirty=false,conflict=false,recipe=null,boxPose=null,lastMove=null,serial=0;
let previewSource=null,exampleDefaults=null,exampleBox=null,exampleCache=null,ownCache=null;
const path=(object,key)=>key.split('.').reduce((v,k)=>v?.[k],object);
function put(object,key,value){const parts=key.split('.');let at=object;for(const p of parts.slice(0,-1))at=at[p]??=(/^\d+$/.test(parts.at(-1))?[]:{});at[parts.at(-1)]=value;}
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;};
const fmt=v=>Number.isFinite(v)?Number(v.toFixed(2)).toString():'—';
// Joint (°/s), Cartesian (mm/s), and gripper (rpm) speeds share the server's cap of 10.
const maximumSpeed=10;
const terminal=state=>['completed','failed','unknown','cancelled'].includes(state);
const activeSimulation=state=>['planning','running','paused','awaiting_grip','awaiting_open'].includes(state);

function fields(parent,labels,keys){
  const wrap=node('div',null,'pose-fields');
  labels.forEach((title,i)=>{const label=node('label',title),input=node('input'),key=keys[i];input.type='number';input.step='any';input.dataset.config=key;
    if(/size_mm|distance_mm|clearance_mm/.test(key))input.min='0.001';
    if(/joints\./.test(key)){const limit=[2,4].includes(Number(key.split('.').at(-1)))?155:360;input.min=-limit;input.max=limit;}
    if(/\.speed$/.test(key)){input.min='0.001';input.max=10;}
    if(/\.acceleration$/.test(key))input.min='0.001';
    label.append(input);wrap.append(label);
  });parent.append(wrap);return wrap;
}
function card(title,subtitle){const c=node('section',null,'scenario-card');c.append(node('h4',title));if(subtitle)c.append(node('p',subtitle,'scenario-caption'));return c;}

function build(root){
  root.innerHTML=`<div class="scenario-head"><div><p class="scenario-eyebrow">PICK & PLACE</p><h3>17단계 상품 이송</h3><p class="scenario-caption">상품과 공통 지점을 설정하면 접근부터 대기 복귀까지 하나의 경로로 연결합니다.</p></div><span data-sc="saved" class="scenario-badge">조회 중</span></div>
    <div class="scenario-flow" aria-label="전체 작업 흐름"></div>
    <div class="scenario-summary"><div><span>선택한 가상 상품</span><strong data-sc="product-summary">조회 중</strong></div><div><span>공통 경로</span><strong data-sc="route-summary">조회 중</strong></div><div><span>미리보기 기준</span><strong data-sc="draft-summary">저장된 설정</strong></div></div>
    <section data-sc-panel="products" role="tabpanel"><div class="scenario-section-head"><div><h4>어디에 놓인 상품을 집나요?</h4><p class="scenario-caption">가상 상품은 시뮬레이션 전용입니다. 실제 위치는 카메라의 파지 영역으로 계산합니다.</p></div><label>가상 상품 선택<select data-sc="region"><option value="floor">바닥 · 수직 파지</option><option value="shelf">선반 · 수평 파지</option></select></label></div><div data-sc="virtual-fields"></div><div class="scenario-regions"></div></section>
    <section data-sc-panel="points" role="tabpanel"><p class="scenario-caption">직접 정할 위치는 대기 자세·1차 경유점·박스 놓기 TCP입니다. 2차 경유점과 박스 위 위치는 자동 계산합니다.</p><div data-sc="joint-fields"></div><section class="scenario-card scenario-auto"><div class="scenario-card-top"><h4>2차 경유점</h4><span class="scenario-badge">자동 계산 · 9·14단계</span></div><label>J1 왕복 방향<select data-config="turn_degrees"><option value="180">갈 때 +180° / 돌아올 때 −180°</option><option value="-180">갈 때 −180° / 돌아올 때 +180°</option></select></label><p data-sc="waypoint2" class="scenario-coordinate"></p><p class="scenario-caption">1차 경유점의 J2~J6는 그대로 유지합니다.</p></section><section class="scenario-card"><div class="scenario-card-top"><h4>박스 놓기 위치와 상부 간격</h4><span class="scenario-badge">10–13단계</span></div><div data-sc="box-mount"></div><div class="pose-fields"><label>박스 위까지의 간격 (mm)<input data-config="box_clearance_mm" type="number" step="any" min="0.001"></label></div><p data-sc="above-box" class="scenario-coordinate"></p><p class="scenario-caption">박스 TCP는 아래 좌표 저장으로, 상부 간격은 시나리오 저장으로 저장합니다. 박스 외형은 택배 박스 위치 설정 탭에서 변경합니다.</p></section></section>
    <div data-motion-build><div data-sc="stage-motions"></div></div>
    <section id="scenario-simulation" aria-labelledby="scenario-simulation-title"><div class="scenario-section-head"><div><h4 id="scenario-simulation-title">가상 경로를 순서대로 확인하세요</h4><p class="scenario-caption">실제 장치 명령 없음 · 가상 접촉 표시 · 충돌 검증 아님</p></div><span data-sc="simulation-badge" class="scenario-badge">대기</span></div><div class="scenario-player"><div class="pose-actions"><button type="button" data-sc="play">시뮬레이션 시작</button><button type="button" data-sc="hardware-test">실제 매니퓰레이터 테스트</button><button type="button" data-sc="pause" disabled>일시정지</button><button type="button" data-sc="stop" disabled>종료</button><label>재생 배속<select data-sc="rate"><option value="1">1×</option><option value="2">2×</option><option value="5">5×</option><option value="10" selected>10×</option><option value="20">20×</option></select></label></div><label class="simulation-progress">전체 진행<input data-sc="progress" type="range" min="0" max="1000" value="0" disabled><output data-sc="percent">0%</output></label><p data-sc="simulation-state" role="status">시뮬레이션 대기</p><div class="scenario-confirm" data-sc="confirm-box" hidden><strong data-sc="confirm-title"></strong><p data-sc="confirm-help"></p><button type="button" data-sc="confirm">가상 파지 확인 후 이송</button></div><p data-sc="preview-warning" class="scenario-caption"></p></div><div class="scenario-inspector"><div data-sc="stages" class="scenario-stages" aria-label="17단계 경로"></div><aside class="scenario-step-detail" aria-label="선택한 단계 정보"><div class="scenario-card-top"><span data-sc="step-number" class="scenario-badge"></span><span data-sc="step-kind" class="scenario-kind"></span></div><h4 data-sc="step-title"></h4><p data-sc="step-help"></p><dl data-sc="step-values"></dl><div class="pose-actions"><button type="button" data-sc="jump" disabled>3D에서 이 단계 보기</button><button type="button" data-sc="previous" disabled>이전 단계</button><button type="button" data-sc="next" disabled>다음 단계</button></div><p class="scenario-caption">단계 보기와 진행 막대는 가상 위치를 확인하며 일시정지합니다. 실제 작업 승인과는 별개입니다.</p></aside></div></section>
    <div class="pose-actions scenario-save"><button type="button" data-sc="save" disabled>시나리오 저장</button><button type="button" data-sc="cancel" disabled>변경 취소</button><button type="button" data-sc="reload">최신 설정 불러오기</button></div><p data-sc="message" role="status"></p>
    <details class="scenario-real"><summary><strong>실제 로봇 작업</strong><span data-sc="ready">준비 상태 조회 중</span></summary><p>저장한 설정으로만 준비합니다. 실제 상품은 대시보드에서 선택하고 경로를 승인하세요.</p><div class="pose-actions"><button type="button" data-sc="prepare">대기 상태로 준비</button><a href="/#dashboard">대시보드에서 실제 상품 선택</a><a href="/robot#gripper">접촉 전류·실측 벌림표 설정</a></div><p data-sc="real-stage" role="status"></p><div class="pose-actions"><button type="button" data-sc="real-grip" hidden>파지 확인 후 이송</button><button type="button" data-sc="real-open" hidden>박스에서 열기</button><button type="button" data-sc="real-stop" hidden>실제 작업 중단</button></div></details>
    <dialog data-sc="dialog" aria-labelledby="scenario-prepare-title"><h3 id="scenario-prepare-title">대기 상태 준비 승인</h3><p data-sc="prepare-detail"></p><p data-sc="prepare-state" role="status"></p><div class="pose-actions"><button type="button" data-sc="prepare-cancel">취소</button><button type="button" data-sc="prepare-approve">승인 후 대기 준비</button></div></dialog>`;
  const hardwareDialog=node('dialog');
  hardwareDialog.dataset.sc='hardware-dialog';hardwareDialog.setAttribute('aria-labelledby','hardware-test-title');
  hardwareDialog.innerHTML='<h3 id="hardware-test-title">실제 17단계 테스트 승인</h3><p data-sc="hardware-description">이 창에 표시된 객체 좌표로 실제 로봇과 그리퍼가 움직입니다. 객체와 작업 공간을 확인하세요. 1~17단계를 연속 실행합니다.</p><p data-sc="hardware-summary"></p><pre data-sc="hardware-path" class="scenario-hardware-path"></pre><p data-sc="hardware-approval-state" role="status"></p><div class="pose-actions"><button type="button" data-sc="hardware-cancel">취소</button><button type="button" data-sc="hardware-approve" disabled>승인 후 실제 17단계 실행</button></div>';
  root.append(hardwareDialog);
  const player=root.querySelector('.scenario-player'),hardwareState=node('p');hardwareState.dataset.sc='hardware-state';hardwareState.role='status';player.append(hardwareState);const setup=document.createElement('p');setup.dataset.sc='hardware-setup';setup.hidden=true;
  setup.innerHTML='<a href="/robot#gripper-table">실측 벌림표 설정</a> · <a href="/robot#gripper">접촉 전류 설정</a>';player.append(setup);
  const real=root.querySelector('.scenario-real');
  player.append(real.querySelector('[data-sc="real-stage"]'),real.querySelector('[data-sc="real-grip"]').parentElement);
  const editMotion=node('button','이동 방식 설정');editMotion.type='button';editMotion.dataset.sc='edit-motion';root.querySelector('.scenario-step-detail .pose-actions').append(editMotion);
  const source=node('div',null,'scenario-source'),sourceLabel=node('label','시뮬레이션 경로'),sourceSelect=node('select');sourceSelect.dataset.sc='source';
  for(const [value,title]of [['example','예시 경로 · 설정 없이 바로 재생'],['saved','내 설정']]){const option=node('option',title);option.value=value;sourceSelect.append(option);}sourceLabel.append(sourceSelect);source.append(sourceLabel);const sourceNote=node('p',null,'scenario-caption');sourceNote.dataset.sc='source-note';source.append(sourceNote);root.querySelector('.scenario-head').after(source);
  const exampleFields=node('div',null,'scenario-example-box');exampleFields.dataset.sc='example-box';exampleFields.append(node('h5','예시 박스 놓기 TCP · 시뮬레이션 전용'));const group=node('div',null,'pose-fields');
  for(const [i,axis]of [...'XYZABC'].entries()){const label=node('label',axis+(i<3?' (mm)':' (°)')),input=node('input');input.type='number';input.step='any';input.required=true;input.dataset.exampleBoxAxis=i;label.append(input);group.append(label);}exampleFields.append(group);root.querySelector('[data-sc="box-mount"]').before(exampleFields);
  root.querySelector('[data-sc="box-mount"]').parentElement.querySelector('.scenario-caption').dataset.sc='box-hint';
  for(const [index,[title,first,last]]of phases.entries()){
    const button=node('button');button.type='button';button.dataset.phaseStart=first;button.append(node('span',String(index+1),'scenario-phase-number'),node('strong',title),node('small',`${first}–${last}단계`));root.querySelector('.scenario-flow').append(button);
    const group=node('section',null,'scenario-phase');group.dataset.phase=index;group.append(node('h5',title+' · '+first+'–'+last+'단계'));const list=node('ol');list.start=first;group.append(list);root.querySelector('[data-sc="stages"]').append(group);
    const motions=node('section',null,'scenario-speed-phase');motions.append(node('h4',title+' · '+first+'–'+last+'단계'));root.querySelector('[data-sc="stage-motions"]').append(motions);
    for(let number=first;number<=last;number++){
      const [kind,text]=stages[number-1],item=node('li'),step=node('button');step.type='button';step.dataset.stage=number;step.setAttribute('aria-pressed','false');step.append(node('span',String(number),'scenario-step-number'));
      const body=node('span',null,'scenario-step-text');body.append(node('strong',text),node('small',kinds[kind]+([6,11].includes(number)?' · 확인 대기':'')));step.append(body,node('span',null,'scenario-step-state'));item.append(step);list.append(item);
      const motionRow=node('section',null,'scenario-motion-row');motionRow.dataset.motionStep=number;const motionHead=node('div',null,'scenario-section-head');motionHead.append(node('strong',number+' · '+text));motionRow.append(motionHead);
      if(armSteps.has(number)){
        const label=node('label','이동 방식'),select=node('select');select.dataset.config=`stages.${number}.motion`;select.setAttribute('aria-label',number+'단계 이동 방식');for(const [value,title]of Object.entries(motionNames)){const option=node('option',title);option.value=value;select.append(option);}select.disabled=Boolean(fixedMotions[number]);label.append(select);motionHead.append(label);
        if(fixedMotions[number])motionRow.append(node('p',[9,15].includes(number)?'자동 계산은 J1만 ±180° 회전합니다. 직접 입력은 여섯 관절의 목표를 사용합니다.':'파지 방향 또는 박스 Z축을 따라 직선 접근·후퇴해야 하므로 movel로 고정합니다.','scenario-caption'));
        else{
          const via=node('div',null,'scenario-via');via.dataset.viaStep=number;via.hidden=true;via.append(node('h5','원호 경유 TCP · BASE'),node('p','이 원호 구간에서만 통과할 점입니다. 시작·경유·목표는 서로 달라야 하며 일직선에 놓일 수 없습니다.','scenario-caption'));
          fields(via,['X (mm)','Y (mm)','Z (mm)','A (°)','B (°)','C (°)'],[0,1,2,3,4,5].map(i=>`stages.${number}.via_pose.${i}`));const capture=node('button','현재 TCP를 원호 경유점으로 불러오기');capture.type='button';capture.dataset.captureVia=number;via.append(capture);motionRow.append(via);
        }
      }else motionHead.append(node('span',kind==='gripper'?'그리퍼 동작 · 팔 이동 없음':'대상 선택 · 팔 이동 없음','scenario-badge'));
      motions.append(motionRow);

    }
  }
  for(const region of ['floor','shelf']){
    const widthAxis=region==='floor'?0:1;
    const product=card(regionNames[region]+' 객체','중심은 BASE 좌표입니다. 폭 w는 파지 방향 길이, 높이 h는 BASE Z 방향 길이이며 단위는 mm입니다.');product.dataset.virtualRegion=region;
    fields(product,['중심 X','중심 Y','중심 Z','폭 w · '+(widthAxis===0?'X':'Y'),'높이 h · Z'],[0,1,2].map(i=>`virtual.${region}.center_base_mm.${i}`).concat([`virtual.${region}.size_mm.${widthAxis}`,`virtual.${region}.size_mm.2`]));
    const depth=node('details');depth.append(node('summary','객체 깊이'));
    fields(depth,['깊이 · '+(widthAxis===0?'Y':'X')+' (mm)'],[`virtual.${region}.size_mm.${1-widthAxis}`]);product.append(depth);root.querySelector('[data-sc="virtual-fields"]').append(product);
    const approach=card(regionNames[region],region==='floor'?'상품 위에서 내려가 파지하고 같은 위치로 올라옵니다.':'상품 앞에서 수평으로 파지하고 같은 위치로 물러납니다.');fields(approach,['접근·후퇴 간격 (mm)'],[`approaches.${region}.distance_mm`]);
    if(region==='shelf'){const l=node('label',null,'scenario-check'),check=node('input');check.type='checkbox';check.dataset.config='approaches.shelf.reverse';l.append(check,document.createTextNode('수평 접근 방향 반전'));approach.append(l);}
    const link=node('a',region==='floor'?'바닥 파지 평면 네 점 교시 →':'선반 파지 평면 네 점 교시 →');link.href='/robot?pick_region='+region+'#work-plane';approach.append(link);root.querySelector('.scenario-regions').append(approach);
  }
  for(const [key,title,usage]of [['standby_joints','대기 자세','1·16–17단계'],['waypoint1_joints','1차 경유점','8·15단계']]){
    const group=card(title),top=node('div',null,'scenario-card-top');top.append(group.firstChild,node('span','직접 설정 · '+usage,'scenario-badge'));group.append(top);fields(group,[1,2,3,4,5,6].map(i=>'J'+i+' (°)'),[0,1,2,3,4,5].map(i=>key+'.'+i));const read=node('button','현재 관절값 불러오기');read.type='button';read.dataset.capture=key;group.append(read);
    if(key==='standby_joints')fields(group,['대기 벌림 (mm) · 비우면 실측표 최대값'],['standby_opening_mm']);else group.append(node('p','상품을 든 상태로 박스 쪽으로 회전하기 전의 공통 자세입니다.','scenario-caption'));root.querySelector('[data-sc="joint-fields"]').append(group);
  }
  const rowAt=n=>root.querySelector(`[data-motion-step="${n}"]`);
  for(const n of armSteps){
    const head=rowAt(n).querySelector('.scenario-section-head');
    head.classList.add('scenario-target-columns');
    const joints=node('div',null,'pose-fields scenario-target-joints');joints.dataset.jointStep=n;
    for(let i=0;i<6;i++){
      const label=node('label',`J${i+1} (°)`),input=node('input');input.type='number';input.step='any';
      input.setAttribute('aria-label',`${n}단계 J${i+1} (°)`);
      const key=[1,16].includes(n)?'standby_joints':[8,15].includes(n)?'waypoint1_joints':null;
      if(key){input.dataset.config=`${key}.${i}`;input.min=-([2,4].includes(i)?155:360);input.max=-Number(input.min);}
      else {input.readOnly=true;input.dataset.computedJoint=i;input.placeholder='계산 대기';}
      label.append(input);joints.append(label);
    }
    head.insertBefore(joints,head.querySelector('label'));
    const help=node('p',[1,16].includes(n)?'자동 계산 시 1·16단계가 같은 대기 자세를 사용합니다.':
      [8,15].includes(n)?'자동 계산 시 8·15단계가 같은 1차 경유점을 사용합니다.':
      [9,14].includes(n)?'자동 계산 시 1차 경유점과 J1 왕복 방향으로 계산합니다.':
      '상품·접근·박스 TCP로 계산한 참고 관절각입니다. 목표 TCP를 수정하면 갱신되며 실제 관절 해는 SDK에서 확정합니다.','scenario-caption');rowAt(n).append(help);
    if([1,8].includes(n)){
      const key=n===1?'standby_joints':'waypoint1_joints';
      const old=root.querySelector(`[data-sc="joint-fields"] [data-config="${key}.0"]`).closest('.scenario-card');
      const options=node('div',null,'scenario-joint-options');options.append(old.querySelector('[data-capture]'));
      if(n===1)options.append(old.querySelectorAll('.pose-fields')[1]);
      rowAt(n).append(options);old.remove();
    }
  }
  const pointPanel=root.querySelector('[data-sc-panel="points"]');
  rowAt(9).append(pointPanel.querySelector('.scenario-auto'));
  rowAt(11).append(pointPanel.querySelector('.scenario-card'));
  pointPanel.remove();
  for(const number of armSteps){
    if([1,3,5,7,8].includes(number))continue;
    const group=rowAt(number).querySelector('[data-joint-step]');
    const label=node('label','관절 목표 '),select=node('select');
    select.dataset.jointMode=number;
    select.setAttribute('aria-label',number+'단계 관절 목표 입력 방식');
    for(const [value,title]of [['auto','자동 계산'],['manual','직접 입력']]){
      const option=node('option',title);option.value=value;select.append(option);
    }
    label.append(select);group.before(label);
    [...group.querySelectorAll('input')].forEach((input,i)=>{
      input.dataset.config=`stages.${number}.target_joints.${i}`;
      input.dataset.computedJoint=i;
      input.min=-([2,4].includes(i)?155:360);input.max=-Number(input.min);
    });
  }
  let poseStep=1;
  for(let number=1;number<=stages.length;number++){
    if(armSteps.has(number))poseStep=number;
    const item=root.querySelector(`[data-stage="${number}"]`).closest('li');
    const source=rowAt(poseStep).querySelector('[data-joint-step]');
    const joints=source.cloneNode(true);
    joints.classList.add('scenario-stage-joints');
    for(const input of joints.querySelectorAll('input')){
      input.setAttribute('aria-label',`${number}단계 J${[...joints.querySelectorAll('input')].indexOf(input)+1} (°)`);
      if(!armSteps.has(number)){input.readOnly=true;delete input.dataset.config;}
    }
    if(!armSteps.has(number)){
      [...joints.querySelectorAll('input')].forEach((input,i)=>{input.dataset.computedJoint=i;});
    }
    if(armSteps.has(number)&&![1,3,5,7,8].includes(number)){
      const mode=rowAt(number).querySelector('[data-joint-mode]').parentElement.cloneNode(true);
      item.append(mode);
    }
    item.append(joints);
    item.append(node('p',!armSteps.has(number)?'팔 자세 유지 · 이전 이동 단계의 관절각':
      number===1?'대기 자세':
      number===8?'1차 경유점':'자동 계산 또는 직접 입력 · J1~J6 (°)','scenario-caption scenario-stage-hint'));
  }
  const stageList=root.querySelector('[data-sc="stages"]');
  const table=node('table',null,'scenario-joint-table');
  table.append(node('caption','단계별 조인트 설정 · °'));
  stageList.before(node('p','이동 속도는 허용된 최대 속도를 100%로 표시합니다. (관절 10°/s · 직선 10 mm/s · 그리퍼 10 rpm)','scenario-caption'));
  const thead=node('thead'),header=node('tr');
  for(const title of ['단계','이동 방식','이동 속도','입력 방식','J1','J2','J3','J4','J5','J6']){
    const cell=node('th',title);cell.scope='col';header.append(cell);
  }
  thead.append(header);table.append(thead);
  for(const phase of stageList.querySelectorAll('.scenario-phase')){
    const body=node('tbody'),phaseRow=node('tr'),phaseTitle=node('th',phase.querySelector('h5').textContent);
    body.dataset.phase=phase.dataset.phase;
    phaseTitle.colSpan=10;phaseTitle.scope='rowgroup';phaseRow.className='scenario-table-phase';phaseRow.append(phaseTitle);body.append(phaseRow);
    for(const item of phase.querySelectorAll('li')){
      const joints=item.querySelector('[data-joint-step]'),row=node('tr',null,'scenario-stage-joints');
      row.dataset.jointStep=joints.dataset.jointStep;
      const title=node('th'),mode=node('td'),motion=node('td'),select=item.querySelector('[data-joint-mode]');
      const number=Number(item.querySelector('[data-stage]').dataset.stage);
      const motionRow=rowAt(number),motionSelect=motionRow.querySelector('[data-config$=".motion"]');
      if(motionSelect){
        const labels={movej:'movej · 관절',movel:'movel · 직선',movec:'movec · 원호'};
        for(const option of motionSelect.options)option.textContent=labels[option.value];
        motion.append(motionSelect);
      }
      else motion.append(node('span',stages[number-1][0]==='gripper'?'그리퍼':'대상 선택','scenario-caption'));
      motionRow.querySelector('.scenario-section-head').remove();
      motionRow.querySelector('[data-joint-mode]')?.parentElement.remove();
      for(const paragraph of motionRow.querySelectorAll(':scope > p'))paragraph.remove();
      title.scope='row';title.append(item.querySelector('[data-stage]'));
      const moveHere=node('button','이 위치로 이동');moveHere.type='button';moveHere.dataset.hardwareStep=number;
      moveHere.setAttribute('aria-label',number+'단계 위치로 실제 이동');title.append(moveHere);
      if(select)mode.append(select);
      else {
        const number=Number(title.querySelector('[data-stage]').dataset.stage);
        mode.append(node('span',number>=3&&number<=7?'객체 기준 자동':armSteps.has(number)?'직접 입력':'자세 유지','scenario-caption'));
      }
      const speedCell=node('td');
      if(number!==2){
        const speed=node('select');speed.dataset.speedPercent=number;speed.setAttribute('aria-label',number+'단계 이동 속도');
        for(let percent=10;percent<=100;percent+=10){const option=node('option',percent+'%');option.value=percent;speed.append(option);}
        speedCell.append(speed);
      }else speedCell.append(node('span','—','scenario-caption'));
      row.append(title,motion,speedCell,mode);
      for(const input of joints.querySelectorAll('input')){
        const cell=node('td');cell.append(input);row.append(cell);
      }
      body.append(row);
      if(motionRow.querySelector('input,button,select')){
        const optionsRow=node('tr',null,'scenario-stage-options'),optionsCell=node('td');
        optionsRow.dataset.stageOptions=number;optionsRow.hidden=true;optionsCell.colSpan=10;
        optionsCell.append(motionRow);optionsRow.append(optionsCell);body.append(optionsRow);
        const open=node('button','상세 설정');open.type='button';open.dataset.openStage=number;
        open.setAttribute('aria-expanded','false');open.setAttribute('aria-controls','scenario-stage-options-'+number);optionsRow.id='scenario-stage-options-'+number;motion.append(open);
      }
    }
    table.append(body);
  }
  stageList.replaceChildren(table);
  root.querySelector('[data-motion-build]').remove();
  const objectSettings=card('파지 객체 · 설정 좌표','중심과 폭·높이로 3~7단계를 자동 계산합니다. 실제 매니퓰레이터 테스트는 화면에 입력한 객체 좌표를 사용합니다.');
  objectSettings.classList.add('scenario-object-settings');
  objectSettings.append(root.querySelector('[data-sc="region"]').closest('label'),root.querySelector('[data-sc="virtual-fields"]'));
  stageList.before(objectSettings);

}

export function connectScenario(root,arm,signal){
  if(!root.dataset.built){build(root);root.dataset.built='true';}
  const ui=Object.fromEntries([...root.querySelectorAll('[data-sc]')].map(n=>[n.dataset.sc,n]));
  const productPanel=root.querySelector('[data-sc-panel="products"]');
  const inputs=[...root.querySelectorAll('[data-config]')];
  const on=(n,e,fn)=>{
    n.addEventListener(e,fn,{signal});
    // The product controls share this draft after moving to a sibling workspace tab.
    if(n===root)productPanel.addEventListener(e,fn,{signal});
  };
  productPanel.removeAttribute('role');productPanel.removeAttribute('data-sc-panel');
  const productMount=document.querySelector('[data-product-mount]');
  const productSource=ui.source.closest('.scenario-source').cloneNode(true);
  for(const control of productSource.querySelectorAll('[data-sc]')){control.dataset.productControl=control.dataset.sc;delete control.dataset.sc;}
  const productActions=node('div',null,'pose-actions'),productSave=node('button','시나리오 저장'),productCancel=node('button','변경 취소'),productLink=node('a','시뮬레이션에서 확인');
  productSave.type=productCancel.type='button';productLink.href='#work-points-title';
  productActions.append(productSave,productCancel,productLink);
  const productMessage=node('p');productMessage.setAttribute('role','status');
  productMount.replaceChildren(productSource,productPanel,productActions,productMessage);
  signal.addEventListener('abort',()=>{
    productPanel.dataset.scPanel='products';root.append(productPanel);
  },{once:true});
  let jointTargets=new Map();
  let hardwareTicket=null,hardwareVersion=0,hardwareMessage='',hardwareTimer,hardwareIssues=[];
  let epoch=0,previewTimer,pollTimer,pending=false,previewBusy=false,approval=null,previewController,realBusy=false,selectedStep=3,follow=true,simulation={state:'idle',step:0,progress:0};
  const boxForm=document.querySelector('[aria-labelledby="work-point-box"]');if(boxForm)root.querySelector('[data-sc="box-mount"]').append(boxForm);
  if(boxForm&&!boxForm.querySelector('.scenario-box-legacy')){
    const advanced=node('details',null,'scenario-box-legacy');advanced.append(node('summary','기존 단일 지점 미리보기 · 17단계와 별도'));
    advanced.append(boxForm.querySelector('[data-simulate-point]'),boxForm.querySelector('.route-toggle'));boxForm.append(advanced);
  }
  const isExample=()=>previewSource==='example';
  const boxFields=()=>isExample()?[...root.querySelectorAll('[data-example-box-axis]')]:[...boxForm?.querySelectorAll('.pose-fields input')||[]];
  const boxDirty=()=>!isExample()&&boxForm?.querySelector('[data-state]')?.textContent.startsWith('저장 전');
  const message=(text,error=false)=>{ui.message.textContent=productMessage.textContent=text;ui.message.dataset.error=productMessage.dataset.error=error;};
  function motionFor(number){return armSteps.has(number)?draft?.stages[number]?.motion||(stages[number-1][0]==='line'?'movel':'movej'):null;}
  function speedKind(number){const motion=motionFor(number);return motion==='movej'?'joint':motion?'line':stages[number-1][0];}
  function resolvedSpeed(number){const kind=speedKind(number),s=draft?.stages[number];return s&&(s.override||draft.profiles[s.profile][kind]);}
  function speedText(number){const kind=speedKind(number),speed=resolvedSpeed(number);if(!speed)return '속도 미설정';return `${fmt(speed.speed)} ${kind==='gripper'?'rpm':kind==='line'?'mm/s':'°/s'} · 가속도 ${fmt(speed.acceleration)} ${kind==='gripper'?'rpm²':kind==='line'?'mm/s²':'°/s²'}`;}
  function detail(){
    const number=selectedStep,[kind,title]=stages[number-1],step=recipe?.steps[number-1];ui['step-number'].textContent=number+' / 17';ui['step-kind'].textContent=motionNames[motionFor(number)]||kinds[kind];ui['step-title'].textContent=title;ui['step-help'].textContent=draft?.stages[number]?.target_joints?'직접 입력한 J1~J6를 이 단계의 목표로 사용합니다.':guidance[number];
    const rows=[];if(step?.target)rows.push([step.space==='joint'?'관절 목표':'TCP · BASE',step.target.map((v,i)=>step.space==='joint'?`J${i+1} ${fmt(v)}°`:`${'XYZABC'[i]} ${fmt(v)}${i<3?' mm':'°'}`).join(' · ')]);
    else if(number===1)rows.push(['대기 관절각 (°)',draft?.standby_joints.map(fmt).join(' · ')||'조회 중']);
    if([4,12,17].includes(number))rows.push(['가상 벌림',fmt(step?.opening_mm)+' mm']);
    if(number===1)rows.push(['대기 벌림',recipe?fmt(recipe.idle_opening_mm)+' mm':'실측 벌림표 최대값 또는 설정한 벌림']);
    if(number===6)rows.push(['가상 접촉 폭',fmt(recipe?.width_mm)+' mm']);
    if(motionFor(number)==='movec')rows.push(['원호 경유 TCP',step?.via_pose?step.via_pose.map((v,i)=>`${'XYZABC'[i]} ${fmt(v)}${i<3?' mm':'°'}`).join(' · '):'해당 행의 상세 설정에서 교시하세요.']);
    if(kind!=='target')rows.push(['속도 · 가속도',speedText(number)]);
    if([6,11].includes(number))rows.push(['계속 진행 조건',number===6?'가상 파지 확인 후 이송':'가상 박스에서 열기']);
    if(!step&&number!==1)rows.unshift(['목표','설정을 완성하면 자동 계산됩니다.']);
    ui['step-values'].replaceChildren(...rows.map(([title,text])=>{const row=node('div');row.append(node('dt',title),node('dd',text));return row;}));
    const playable=Boolean(simulation.route)&&!['idle','planning','error'].includes(simulation.state);ui.jump.disabled=!playable;ui.previous.disabled=!playable||selectedStep<=1;ui.next.disabled=!playable||selectedStep>=17;
    ui['edit-motion'].disabled=!armSteps.has(number);ui['edit-motion'].textContent='테이블에서 이동 방식 설정';
    for(const button of root.querySelectorAll('[data-stage]'))button.setAttribute('aria-pressed',String(Number(button.dataset.stage)===number));
  }
  function controls(){
    const hardwareBlocked=!draft||pending||realBusy||activeSimulation(simulation.state);
    for(const select of root.querySelectorAll('[data-speed-percent]'))select.disabled=!draft||pending||realBusy;
    ui['hardware-test'].disabled=Boolean(hardwareBlocked);
    for(const button of root.querySelectorAll('[data-hardware-step]')){button.disabled=Boolean(hardwareBlocked);}
    ui['hardware-approve'].disabled=Boolean(hardwareBlocked)||!hardwareTicket;
    const blockedReason=!draft?'설정 조회 중':pending?'요청 처리 중':realBusy?'실제 로봇 작업 진행 중':activeSimulation(simulation.state)?'시뮬레이션을 종료하면 실제 테스트를 확인할 수 있습니다.':'';
    ui['hardware-state'].textContent=blockedReason||hardwareMessage||(hardwareIssues.length?hardwareIssues.join(' '):'')||(isExample()?'예시 경로의 현재 입력값으로 실제 테스트할 수 있습니다. 실행 전 경로를 확인하고 승인하세요.':'현재 화면의 객체·경유점·박스 좌표로 실제 테스트합니다. 실행 전 승인 필요');
    ui['hardware-setup'].hidden=!hardwareIssues.length;
    ui['hardware-test'].title=blockedReason||'현재 입력값으로 실제 테스트 경로 확인';
    ui.source.disabled=pending||realBusy;
    productSource.querySelector('select').disabled=ui.source.disabled;
    ui.play.disabled=realBusy||pending||previewBusy||!recipe||recipe.errors.length>0||activeSimulation(simulation.state);
    ui.play.textContent=simulation.state==='complete'?'처음부터 다시 재생':'시뮬레이션 시작';ui.pause.disabled=!['running','paused'].includes(simulation.state);ui.pause.textContent=simulation.state==='paused'?'계속 재생':'일시정지';ui.stop.disabled=simulation.state==='idle';ui.progress.disabled=['idle','planning','error'].includes(simulation.state);
    ui.prepare.disabled=isExample()||pending||dirty||conflict||Boolean(boxDirty())||realBusy;
    const waiting=['awaiting_grip','awaiting_open'].includes(simulation.state);ui['confirm-box'].hidden=!waiting;ui.confirm.hidden=!waiting;ui.confirm.textContent=simulation.state==='awaiting_grip'?'가상 파지 확인 후 이송':'가상 박스에서 열기';
    ui['confirm-title'].textContent=simulation.state==='awaiting_grip'?'상품을 잡은 위치에서 정지했습니다.':'박스 놓기 위치에 도착했습니다.';ui['confirm-help'].textContent=simulation.state==='awaiting_grip'?'3D에서 상품과 손가락을 확인하고 다음 경로를 재생하세요.':'열기를 확인하면 상품을 놓고 대기 자세까지 복귀합니다.';
    ui['simulation-badge'].textContent=previewBusy?'설정 계산 중':({idle:'재생 준비',planning:'경로 계산 중',running:'재생 중',paused:'일시정지',awaiting_grip:'파지 확인 대기',awaiting_open:'열기 확인 대기',complete:'17단계 완료',error:'경로 확인 필요'}[simulation.state]||'대기');ui['simulation-badge'].dataset.state=simulation.state;
    ui['preview-warning'].textContent=isExample()?'시뮬레이션 전용 예시값입니다. 편집·재생해도 실제 좌표나 저장된 설정을 변경하지 않습니다.':dirty||boxDirty()?'저장 전 편집값으로 미리봅니다. 실제 작업에는 마지막으로 저장한 값이 사용됩니다.':'저장된 설정의 가상 미리보기입니다. 실제 로봇과 그리퍼는 움직이지 않습니다.';detail();
  }
  function update(){
    ui.saved.textContent=isExample()?dirty?'예시 편집 중':'예시값':conflict?'다른 화면에서 변경됨':dirty?'저장 전':saved?.revision?'저장됨':saved?'기본값':'조회 필요';ui.saved.dataset.state=dirty||conflict?'warning':'saved';ui.save.disabled=isExample()||pending||!draft||!dirty||conflict;ui.cancel.disabled=pending||!dirty;
    ui.source.value=previewSource||'saved';ui.source.disabled=pending||realBusy;ui['example-box'].hidden=!isExample();if(boxForm)boxForm.hidden=isExample();
    ui['source-note'].textContent=isExample()?'대기·경유·박스·벌림·속도가 모두 채워져 있습니다. 바로 시뮬레이션을 시작하세요. 실제 작업 설정은 내 설정에서 교시·저장합니다.':'저장된 설정과 편집값을 사용합니다. 예시 경로로 전환해도 내 설정은 유지됩니다.';
    productSource.querySelector('select').value=ui.source.value;
    productSource.querySelector('p').textContent=ui['source-note'].textContent;
    document.querySelector('[data-product-saved]').textContent=ui.saved.textContent;
    productSave.disabled=ui.save.disabled;productCancel.disabled=ui.cancel.disabled;
    ui['box-hint'].textContent=isExample()?'예시 박스와 TCP는 시뮬레이션에만 사용합니다. 실제 박스 좌표는 내 설정에서 별도로 교시하세요.':'박스 TCP는 좌표 저장으로, 상부 간격은 시나리오 저장으로 저장합니다. 박스 외형은 택배 박스 위치 설정 탭에서 변경합니다.';
    ui.reload.textContent=isExample()?'예시값 다시 채우기':'최신 설정 불러오기';
    for(const group of root.querySelectorAll('[data-virtual-region]'))group.hidden=group.dataset.virtualRegion!==ui.region.value;
    for(const group of root.querySelectorAll('[data-joint-step]')){
      const n=Number(group.dataset.jointStep),w=draft?.waypoint1_joints,manual=draft?.stages[n]?.target_joints;
      const q=manual??([9,14].includes(n)&&w?.every(Number.isFinite)?[w[0]+draft.turn_degrees,...w.slice(1)]:jointTargets.get(n));
      for(const input of group.querySelectorAll('input')){
        if(input.dataset.config?.includes('.target_joints.')){
          input.readOnly=!manual;
          input.required=Boolean(manual);
          if(input!==document.activeElement)input.value=manual?.[Number(input.dataset.computedJoint)]??(q?.[Number(input.dataset.computedJoint)]==null?'':Number(q[Number(input.dataset.computedJoint)].toFixed(2)));
        }else if(input.dataset.config){if(input!==document.activeElement)input.value=path(draft,input.dataset.config)??'';}
        else input.value=q?.[Number(input.dataset.computedJoint)]==null?'':Number(q[Number(input.dataset.computedJoint)].toFixed(2));
      }
    }
    for(const select of root.querySelectorAll('[data-joint-mode]'))select.value=draft?.stages[select.dataset.jointMode]?.target_joints?'manual':'auto';
    const w=draft?.waypoint1_joints,valid=w?.length===6&&w.every(Number.isFinite),second=valid?[w[0]+draft.turn_degrees,...w.slice(1)]:null;
    ui.waypoint2.textContent=second?'J1–J6 · [ '+second.map(fmt).join(', ')+' ]°':'먼저 1차 경유점의 여섯 관절각을 입력하세요.';ui.waypoint2.dataset.error=Boolean(second?.some((v,i)=>Math.abs(v)>([2,4].includes(i)?155:360)));
    const box=boxFields().map(i=>i.valueAsNumber);boxPose=box.length===6&&box.every(Number.isFinite)?box:null;
    ui['above-box'].textContent=boxPose?`박스 위 TCP · X ${fmt(boxPose[0])} / Y ${fmt(boxPose[1])} / Z ${fmt(boxPose[2]+(draft?.box_clearance_mm||0))} mm · 방향 유지`:'박스 놓기 TCP를 교시하면 박스 위 위치가 계산됩니다.';
    const product=draft?.virtual[ui.region.value];ui['product-summary'].textContent=product?regionNames[ui.region.value]+' · '+product.size_mm.map(fmt).join(' × ')+' mm':'조회 중';ui['route-summary'].textContent=!valid?'1차 경유점 설정 필요':!boxPose?'박스 TCP 설정 필요':isExample()?'예시 경로 준비됨':'공통 지점 설정됨';ui['draft-summary'].textContent=isExample()?'시뮬레이션 전용 예시':dirty||boxDirty()?'저장 전 편집값':'저장된 설정';
    if(draft){for(const input of inputs){const via=input.dataset.config.match(/^stages\.(\d+)\.via_pose\./);if(via)input.disabled=motionFor(Number(via[1]))!=='movec';input.required=!input.readOnly&&input.type!=='checkbox'&&input.dataset.config!=='standby_opening_mm'&&(!input.dataset.config.startsWith('waypoint1_joints.')||draft.waypoint1_joints!==null);}
      for(const select of root.querySelectorAll('[data-config$=".motion"]'))select.value=motionFor(Number(select.dataset.config.split('.')[1]));
      for(const group of root.querySelectorAll('[data-via-step]'))group.hidden=motionFor(Number(group.dataset.viaStep))!=='movec';
      for(const button of root.querySelectorAll('[data-stage]')){const n=Number(button.dataset.stage);button.querySelector('small').textContent=(motionNames[motionFor(n)]||kinds[stages[n-1][0]])+([6,11].includes(n)?' · 확인 대기':'');}
      for(const select of root.querySelectorAll('[data-speed-percent]')){
        const n=Number(select.dataset.speedPercent),speed=resolvedSpeed(n),percent=Number((speed.speed/maximumSpeed*100).toFixed(6));
        select.querySelector('[data-legacy-speed]')?.remove();
        if(percent<10||percent>100||percent%10!==0){
          const option=node('option',fmt(percent)+'% · 기존');option.value=percent;option.disabled=true;option.dataset.legacySpeed='';select.prepend(option);
        }
        select.value=String(percent);select.title=speedText(n);
      }}controls();
  }
  function fill(){if(!draft)return;for(const n of [3,5,7])draft.stages[n].target_joints=null;for(const input of inputs){const value=path(draft,input.dataset.config);if(input.type==='checkbox')input.checked=Boolean(value);else input.value=value??'';}
    for(const input of root.querySelectorAll('[data-example-box-axis]'))input.value=exampleBox?.[Number(input.dataset.exampleBoxAxis)]??'';
    update();}
  function stopAndInvalidate(){invalidateHardware();jointTargets.clear();clearTimeout(previewTimer);previewController?.abort();epoch++;previewBusy=true;recipe=null;arm.simulationAction('stop');arm.setScenarioPreview(null);controls();}
  async function preview(){
    if(!draft||signal.aborted)return;previewController?.abort();previewController=new AbortController();const current=++epoch;previewBusy=true;update();
    try{
      const box=boxFields().map(i=>i.valueAsNumber);if((isExample()||box.some(Number.isFinite))&&!box.every(Number.isFinite))throw Error('박스 놓기 TCP의 여섯 값을 모두 입력하세요.');
      for(const n of armSteps)if(motionFor(n)==='movec'&&(!draft.stages[n].via_pose||draft.stages[n].via_pose.length!==6||![...draft.stages[n].via_pose].every(Number.isFinite)))throw Error(n+'단계 movec의 원호 경유 TCP 여섯 값을 입력하세요.');
      const result=await api('pick/scenario/preview','POST',{region:ui.region.value,config:draft,box_pose:boxPose,example:isExample()},AbortSignal.any([signal,previewController.signal]));if(current!==epoch||signal.aborted)return;recipe=result;
      let seed=result.config.standby_joints;const tool=poseFrame(simulationToolPose);
      for(const step of result.steps){
        if(step.target?.length!==6||!step.target.every(Number.isFinite))continue;
        let q=step.space==='joint'?step.target:endpointIK(poseFrame(step.target),seed,tool,result.config.waypoint1_joints?.[0]);
        if(q){jointTargets.set(step.number,q);seed=q;}
      }
      update();arm.setScenarioPreview(result);
      if(simulation.state==='idle')ui['simulation-state'].textContent=result.errors.length?result.errors.join(' '):'준비 완료 · 시작하면 상단 3D에서 전체 경로를 재생합니다.';
    }catch(error){if(current===epoch&&error.name!=='AbortError'){recipe=null;arm.setScenarioPreview(null);ui['simulation-state'].textContent=error.message;}}
    finally{if(current===epoch){previewBusy=false;controls();}}
  }
  function changed(markDirty=true){if(markDirty){dirty=true;message('');}stopAndInvalidate();update();previewTimer=setTimeout(preview,250);}
  async function load(){pending=true;stopAndInvalidate();update();try{
    const [config,status,example]=await Promise.all([api('pick-scenario','GET',undefined,signal),api('status','GET',undefined,signal),api('pick/scenario/preview','POST',{example:true},signal)]);
    saved=config;exampleDefaults=example;previewSource??=(realBusy||status.pick_active)?'saved':(!config.waypoint1_joints||!status.work_points?.box?.pose)?'example':'saved';
    draft=structuredClone(isExample()?exampleDefaults.config:saved);exampleBox=structuredClone(exampleDefaults.box_pose);ownCache=exampleCache=null;dirty=conflict=false;fill();await preview();message(isExample()?'예시값을 채웠습니다. 저장이나 장치 연결 없이 바로 재생할 수 있습니다.':'시나리오를 불러왔습니다.');
  }catch(error){message(error.message,true);}finally{pending=false;update();}}
  async function switchSource(value,forced=false){if(pending||realBusy&&!forced||!exampleDefaults||value===previewSource)return;
    const cache={draft:structuredClone(draft),dirty,conflict,box:isExample()?boxFields().map(i=>Number.isFinite(i.valueAsNumber)?i.valueAsNumber:null):null};if(isExample())exampleCache=cache;else ownCache=cache;
    stopAndInvalidate();previewSource=value;const next=isExample()?exampleCache:ownCache;draft=structuredClone(next?.draft||(isExample()?exampleDefaults.config:saved));dirty=next?.dirty||false;conflict=next?.conflict||false;if(isExample())exampleBox=structuredClone(next?.box||exampleDefaults.box_pose);fill();await preview();message(isExample()?'예시 경로 · 실제 저장값은 유지됩니다.':'내 설정으로 돌아왔습니다.');
  }
  on(root,'change',event=>{
    const number=Number(event.target.dataset.jointMode);
    if(!number||!draft)return;
    draft.stages[number].target_joints=event.target.value==='manual'?[...(jointTargets.get(number)||Array(6).fill(null))]:null;
    fill();changed();
  });
  on(ui.source,'change',()=>void switchSource(ui.source.value));
  on(productSource.querySelector('select'),'change',event=>void switchSource(event.target.value));
  on(productSave,'click',()=>ui.save.click());on(productCancel,'click',()=>ui.cancel.click());
  on(root,'input',event=>{const input=event.target;if(input.dataset.exampleBoxAxis===undefined||!isExample())return;exampleBox=boxFields().map(i=>Number.isFinite(i.valueAsNumber)?i.valueAsNumber:null);changed();});
  on(root,'input',event=>{const input=event.target;if(!input.dataset.config||!draft)return;const value=input.type==='checkbox'?input.checked:input.tagName==='SELECT'?(input.dataset.config==='turn_degrees'?Number(input.value):input.value):Number.isFinite(input.valueAsNumber)?input.valueAsNumber:null;const previousSpeed=input.dataset.config.endsWith('.motion')?resolvedSpeed(Number(input.dataset.config.split('.')[1]))?.speed:null;if(input.dataset.config.startsWith('waypoint1_joints.')&&!draft.waypoint1_joints)draft.waypoint1_joints=Array(6).fill(null);put(draft,input.dataset.config,value);if(input.dataset.config.endsWith('.motion')){const n=input.dataset.config.split('.')[1];draft.stages[n].override={...resolvedSpeed(Number(n)),speed:previousSpeed};fill();if(input.value==='movec')showStageOptions(n,true);}changed();});
  on(root,'change',event=>{
    const input=event.target,number=Number(input.dataset.speedPercent),percent=Number(input.value);
    if(!draft||!number||pending||realBusy||percent<10||percent>100||percent%10!==0)return;
    draft.stages[number].override={...resolvedSpeed(number),speed:maximumSpeed*percent/100};changed();
  });
  on(root,'click',async event=>{const button=event.target.closest('[data-capture-via]');if(!button||!draft)return;try{draft.stages[button.dataset.captureVia].via_pose=(await api('work-points/current','GET',undefined,signal)).pose;fill();changed();message('현재 TCP를 원호 경유점으로 불러왔습니다. 확인 후 저장하세요.');}catch(error){message(error.message,true);}});
  on(root,'work-point-change',event=>{if(!isExample()&&boxForm?.contains(event.target)&&draft)changed(false);});
  on(ui.region,'change',()=>{stopAndInvalidate();update();void preview();});
  function restoreDraft(){draft=structuredClone(isExample()?exampleDefaults.config:saved);if(isExample())exampleBox=structuredClone(exampleDefaults.box_pose);dirty=conflict=false;stopAndInvalidate();fill();void preview();message(isExample()?'예시 기본값을 복원했습니다.':'저장값을 복원했습니다.');}
  on(ui.reload,'click',()=>{if(isExample())restoreDraft();else void load();});on(ui.cancel,'click',restoreDraft);
  on(ui.save,'click',async()=>{if(isExample()||pending||!draft)return;const invalid=inputs.find(i=>!i.disabled&&!i.validity.valid);if(invalid){const panel=invalid.closest('[data-manipulator-panel]');document.querySelector('.plane-tabs [aria-controls="'+panel.id+'"]')?.click();const options=invalid.closest('[data-stage-options]');if(options)showStageOptions(options.dataset.stageOptions,true);const collapsed=invalid.closest('details');if(collapsed)collapsed.open=true;invalid.reportValidity();invalid.focus();message('표시한 입력값과 허용 범위를 확인하세요.',true);return;}pending=true;update();try{saved=await api('pick-scenario','PUT',draft,signal);draft=structuredClone(saved);dirty=conflict=false;fill();message('시나리오 저장 완료.');}catch(error){if(error.status===409)conflict=true;message(error.message,true);}finally{pending=false;update();}});
  on(root,'click',async event=>{const button=event.target.closest('button');if(!button)return;if(button.dataset.phaseStart){selectedStep=Number(button.dataset.phaseStart);follow=false;detail();}if(button.dataset.stage){selectedStep=Number(button.dataset.stage);follow=false;detail();if(innerWidth<=620)root.querySelector('.scenario-step-detail').scrollIntoView({block:'nearest',behavior:'smooth'});}const key=button.dataset.capture;if(!key||!draft)return;try{const status=await api('status','GET',undefined,signal),sample=armTelemetry(status);draft[key]=sample.live?sample.angles:(await api('pick/scenario/current','GET',undefined,signal)).current_joints;fill();changed();message('현재 관절값을 불러왔습니다. 확인 후 저장하세요.');}catch(error){message(error.message,true);}});
  const unsubscribe=arm.observeSimulation(next=>{simulation=next;if(follow&&next.step)selectedStep=next.step;if(ui['simulation-state'].textContent!==next.message)ui['simulation-state'].textContent=next.message;ui.progress.value=Math.round((next.progress||0)*1000);ui.percent.textContent=Math.round((next.progress||0)*100)+'%';
    for(const button of root.querySelectorAll('[data-stage]')){const n=Number(button.dataset.stage),current=n===next.step&&next.state!=='idle';button.dataset.current=current;button.dataset.done=next.state==='complete'||Boolean(next.step&&n<next.step);button.querySelector('.scenario-step-state').textContent=current?['awaiting_grip','awaiting_open'].includes(next.state)?'확인':'현재':next.step&&n<next.step?'✓':'';}
    for(const [i,phase]of phases.entries()){const current=next.step>=phase[1]&&next.step<=phase[2]&&next.state!=='idle';root.querySelector(`[data-phase="${i}"]`).dataset.current=current;root.querySelector(`[data-phase-start="${phase[1]}"]`).dataset.current=current;}controls();});
  function invalidateHardware(){
    hardwareMessage='';hardwareVersion++;clearTimeout(hardwareTimer);
    const ticket=hardwareTicket;hardwareTicket=null;
    if(ui['hardware-dialog'].open)ui['hardware-dialog'].close();
    if(ticket)void api('pick/cancel','POST',{approval_id:ticket.approval_id}).catch(()=>{});
  }
  async function previewHardware(step=null){
    if(ui['hardware-test'].disabled)return;
    invalidateHardware();const version=hardwareVersion;
    pending=true;hardwareMessage='실제 장치와 화면 입력 경로를 확인하고 있습니다.';controls();
    try{
      const action=step?'hardware-step':'hardware-test';
      const box=boxFields().map(input=>input.valueAsNumber);
      const target=await api('pick/scenario/'+action+'/preview','POST',{region:ui.region.value,revision:saved.revision,config:structuredClone(draft),box_pose:box.length===6&&box.every(Number.isFinite)?box:null,...(step?{step}: {})},signal);
      target.action=action;
      if(version!==hardwareVersion||signal.aborted||root.closest('[data-manipulator-panel]').hidden){void api('pick/cancel','POST',{approval_id:target.approval_id}).catch(()=>{});return;}
      hardwareTicket=target;
      const gripperAction=step&&target.steps.some(action=>action.kind==='gripper');
      root.querySelector('#hardware-test-title').textContent=step?step+(gripperAction?'단계 이동·그리퍼 승인':'단계 위치 이동 승인'):'실제 17단계 테스트 승인';
      ui['hardware-description'].textContent=step?(gripperAction?'팔이 목표 위치에 도달한 후 그리퍼의 벌림·파지 명령을 실행합니다. 두 동작과 작업 공간을 확인하세요.':'팔이 선택한 단계의 위치로 이동합니다. 목표와 작업 공간을 확인하세요.'):'표시된 객체 좌표로 실제 로봇과 그리퍼가 움직입니다. 승인 후 1~17단계를 연속 실행합니다.';
      ui['hardware-approve'].textContent=step?(gripperAction?'승인 후 이동·그리퍼 실행':'승인 후 이 위치로 이동'):'승인 후 실제 17단계 실행';
      ui['hardware-summary'].textContent=(isExample()?'예시 경로 입력값':'내 설정 입력값')+' · '+regionNames[target.region]+(step?' · 현재 J1~J6 ['+target.current_joints.map(fmt).join(', ')+']°':' · 객체 중심 ['+target.pose.slice(0,3).join(', ')+'] mm · 파지 전 벌림 '+target.opening_mm+' mm')+' · 활성 TCP '+target.tcp_name;
      ui['hardware-path'].textContent=target.steps.map(step=>step.number+' · '+step.title+(step.target?' · '+step.motion+' · '+(step.space==='joint'?'J1~J6 (°)':'TCP XYZ mm / ABC °')+' ['+step.target.map(fmt).join(', ')+']':step.contact_ma!=null?' · 닫기 위치 '+step.position+' · 접촉 임계값 '+step.contact_ma+' mA':step.opening_mm!=null?' · 벌림 '+step.opening_mm+' mm'+(step.position!=null?' · 명령 위치 '+step.position:''):'')).join('\n');
      ui['hardware-approval-state'].textContent='30초 동안 한 번 승인할 수 있습니다.';
      hardwareMessage='실제 테스트 경로를 확인한 후 승인하세요.';
      ui['hardware-dialog'].showModal();
      hardwareTimer=setTimeout(()=>{invalidateHardware();hardwareMessage='승인 시간이 만료되었습니다. 다시 테스트 경로를 확인하세요.';controls();},target.expires_in*1000);
    }catch(error){hardwareMessage=error.message;}
    finally{pending=false;controls();}
  }
  on(window,'robot-global-stop',()=>{invalidateHardware();arm.simulationAction('stop');if(ui.dialog.open)ui.dialog.close();hardwareMessage='중단을 요청했습니다.';controls();});
  on(ui['hardware-test'],'click',()=>void previewHardware());
  on(root,'click',event=>{const button=event.target.closest('[data-hardware-step]');if(button&&!button.disabled)void previewHardware(Number(button.dataset.hardwareStep));});
  on(ui['hardware-cancel'],'click',()=>ui['hardware-dialog'].close());
  on(ui['hardware-dialog'],'close',()=>{invalidateHardware();controls();});
  on(ui['hardware-approve'],'click',async()=>{
    if(ui['hardware-approve'].disabled||!hardwareTicket)return;
    const ticket=hardwareTicket;hardwareTicket=null;clearTimeout(hardwareTimer);
    pending=true;controls();
    try{
      await api('pick/scenario/'+ticket.action+'/approve','POST',{approval_id:ticket.approval_id},signal);
      ui['hardware-dialog'].close();hardwareMessage=ticket.step?ticket.step+'단계 위치 이동을 승인했습니다.':'실제 17단계 테스트를 승인했습니다.';void poll();
    }catch(error){ui['hardware-approval-state'].textContent=error.message;hardwareMessage=error.message;}
    finally{pending=false;controls();}
  });
  on(ui.play,'click',()=>{if(!recipe||ui.play.disabled)return;follow=true;arm.simulationAction('rate',Number(ui.rate.value));void arm.simulateRecipe(recipe,simulationToolPose);});on(ui.pause,'click',()=>{follow=true;arm.simulationAction(simulation.state==='paused'?'resume':'pause');});on(ui.stop,'click',()=>arm.simulationAction('stop'));on(ui.confirm,'click',()=>{follow=true;arm.simulationAction(simulation.state==='awaiting_grip'?'confirm-grip':'open-at-box');});on(ui.progress,'input',()=>{follow=true;arm.simulationAction('seek',Number(ui.progress.value)/1000);});on(ui.rate,'change',()=>arm.simulationAction('rate',Number(ui.rate.value)));
  on(ui.jump,'click',()=>{follow=true;arm.simulationAction('step',selectedStep);});on(ui.previous,'click',()=>{follow=true;arm.simulationAction('step',selectedStep-1);});on(ui.next,'click',()=>{follow=true;arm.simulationAction('step',selectedStep+1);});
  function showStageOptions(number,open){
    const row=root.querySelector(`[data-stage-options="${number}"]`),button=root.querySelector(`[data-open-stage="${number}"]`);
    if(!row||!button)return;
    row.hidden=!open;button.setAttribute('aria-expanded',String(open));
  }
  on(root,'click',event=>{const button=event.target.closest('[data-open-stage]');if(button)showStageOptions(button.dataset.openStage,button.getAttribute('aria-expanded')!=='true');});
  on(ui['edit-motion'],'click',()=>{const row=root.querySelector(`[data-stage="${selectedStep}"]`).closest('tr');row.scrollIntoView({block:'center',behavior:'smooth'});const select=row.querySelector('[data-config$=".motion"]');if(select&&!select.disabled)select.focus();});
  const cancelApproval=()=>{if(approval){const id=approval;approval=null;void api('pick/cancel','POST',{approval_id:id}).catch(()=>{});}};
  on(ui.prepare,'click',async()=>{if(ui.prepare.disabled)return;arm.simulationAction('stop');pending=true;controls();try{cancelApproval();const target=await api('pick/prepare/preview','POST',{},signal);approval=target.approval_id;ui['prepare-detail'].textContent=`${target.motion} · 대기 J1~J6 [${target.joints.join(', ')}]° · 벌림 ${target.opening_mm} mm · 명령 위치 ${target.hand_position} · 속도 ${target.speed.speed}${target.motion==='movej'?'°/s':' mm/s'}${target.motion==='movec'?' · 원호 경유 TCP ['+target.via_pose.join(', ')+']':''}`;ui['prepare-state'].textContent='30초 일회용 승인 · 실제 로봇과 그리퍼가 움직입니다.';ui['prepare-approve'].disabled=false;ui.dialog.showModal();}catch(error){message(error.message,true);}finally{pending=false;controls();}});
  on(ui['prepare-cancel'],'click',()=>ui.dialog.close());on(ui.dialog,'close',cancelApproval);on(ui['prepare-approve'],'click',async()=>{if(!approval)return;const id=approval;approval=null;ui['prepare-approve'].disabled=true;try{await api('pick/prepare/approve','POST',{approval_id:id},signal);ui.dialog.close();void poll();}catch(error){ui['prepare-state'].textContent=error.message;}});
  async function act(action){if(!lastMove)return;try{lastMove=await api('pick/moves/'+lastMove.approval_id+'/'+action,'POST',{},signal);showMove(lastMove);}catch(error){message(error.message,true);}}
  on(ui['real-grip'],'click',()=>void act('confirm-grip'));on(ui['real-open'],'click',()=>void act('open-at-box'));on(ui['real-stop'],'click',()=>void act('stop'));
  function showMove(move){lastMove=move;realBusy=Boolean(move&&!terminal(move.state));if(realBusy){arm.simulationAction('stop');if(isExample()&&!move.hardware_test)void switchSource('saved',true);root.querySelector('.scenario-real').open=true;}ui['real-stage'].textContent=move?`${move.step||0}/17 · ${move.message}${move.hand_feedback?' · 실제 위치 '+move.hand_feedback.position:''}`:'진행 중인 실제 작업이 없습니다.';ui['real-grip'].hidden=(isExample()&&!move?.hardware_test)||move?.state!=='awaiting_grip';ui['real-open'].hidden=(isExample()&&!move?.hardware_test)||move?.state!=='awaiting_open';ui['real-stop'].hidden=!move||terminal(move.state);controls();}
  async function poll(){clearTimeout(pollTimer);try{const result=await api('pick/scenario/status','GET',undefined,signal);if(signal.aborted)return;hardwareIssues=result.hardware_test_issues||[];ui.ready.textContent=isExample()?'예시 모드 · 실제 작업 준비 안 함':result.prepared?'대기 준비됨':'대기 준비 필요';showMove(result.move);if(++serial%4===0){const next=await api('pick-scenario','GET',undefined,signal);if(saved&&next.revision!==saved.revision){if(isExample()){if(ownCache?.dirty)ownCache.conflict=true;saved=next;}else if(dirty){conflict=true;update();}else{stopAndInvalidate();saved=next;draft=structuredClone(next);fill();void preview();}}}}catch(error){if(error.name!=='AbortError')ui.ready.textContent=error.message;}finally{if(!signal.aborted)pollTimer=setTimeout(poll,1000);}}
  signal.addEventListener('abort',()=>{invalidateHardware();unsubscribe();clearTimeout(previewTimer);clearTimeout(pollTimer);previewController?.abort();cancelApproval();},{once:true});
  if(saved&&draft){fill();void preview();}else void load();void poll();
}
