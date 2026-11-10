// E0509 forward kinematics from DoosanRobotics/doosan-robot2,
// dsr_description2/urdf/e0509.white.urdf (Apache-2.0). All lengths are mm.
export const defaultArmJoints = Object.freeze([0,0,90,0,90,0]);
export function exampleScenarioGuides(){
  const floor={points:[[-900,-650,0],[900,-650,0],[900,650,0],[-900,650,0]],lines:[],baseZ:0};
  const wall={points:[[-600,-650,0],[-600,650,0],[-600,650,850],[-600,-650,850]],lines:[]};
  for(let x=-900;x<=900;x+=100)floor.lines.push([[x,-650,0],[x,650,0]]);
  for(let y=-650;y<=650;y+=100){floor.lines.push([[-900,y,0],[900,y,0]]);wall.lines.push([[-600,y,0],[-600,y,850]]);}
  for(let z=0;z<=850;z+=100)wall.lines.push([[-600,-650,z],[-600,650,z]]);
  const x=[-700,400,-75],length=Math.hypot(...x),forward=x.map(v=>v/length),side=[-forward[1],forward[0],0],s=Math.hypot(...side);for(let i=0;i<3;i++)side[i]/=s;
  const up=[forward[1]*side[2]-forward[2]*side[1],forward[2]*side[0]-forward[0]*side[2],forward[0]*side[1]-forward[1]*side[0]];
  return {floor,wall,lens:[100,-400,500],camera:[forward,side,up],objectPoints:[],target:null};
}
const jointOrigins = [
  [[0,0,204.5],[0,0,0]], [[0,0,0],[0,-1.5708,-1.5708]],
  [[373,0,0],[0,0,1.5708]], [[0,-373,0],[1.5708,0,0]],
  [[0,0,0],[-1.5708,0,0]], [[0,-172.5,0],[1.5708,0,0]]
];
export const identity = () => [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1];
export function multiply(a,b) {
  const c = Array(16).fill(0);
  for (let j=0;j<4;j++) for (let i=0;i<4;i++) for (let k=0;k<4;k++) c[j*4+i] += a[k*4+i]*b[j*4+k];
  return c;
}
export const transformPoint = (m,p) => [0,1,2].map(i => m[i]*p[0]+m[4+i]*p[1]+m[8+i]*p[2]+m[12+i]);
export const frameAxis = (m,i) => [m[i*4],m[i*4+1],m[i*4+2]];
function rotation(i,a) {
  const m=identity(),c=Math.cos(a),s=Math.sin(a);
  if (i===0) {m[5]=c;m[6]=s;m[9]=-s;m[10]=c;}
  else if (i===1) {m[0]=c;m[2]=-s;m[8]=s;m[10]=c;}
  else {m[0]=c;m[1]=s;m[4]=-s;m[5]=c;}
  return m;
}
const origins = jointOrigins.map(([p,r]) => {
  const m = multiply(multiply(rotation(2,r[2]),rotation(1,r[1])),rotation(0,r[0]));
  p.forEach((v,i) => m[12+i]=v);
  return m;
});
export function armFrames(degrees) {
  if (!Array.isArray(degrees) || degrees.length!==6 || !degrees.every(Number.isFinite)) throw new TypeError("Six finite joint angles required");
  const frames=[identity()];
  degrees.forEach((a,i) => frames.push(multiply(multiply(frames.at(-1),origins[i]),rotation(2,a*Math.PI/180))));
  return frames;
}
export function framePose(m) {
  const b=Math.acos(Math.min(1,Math.max(-1,m[10]))),singular=Math.abs(Math.sin(b))<1e-5;
  const a=singular ? Math.atan2(m[1]*(b>Math.PI/2?-1:1),m[0]*(b>Math.PI/2?-1:1)) : Math.atan2(m[9],m[8]);
  const c=singular ? 0 : Math.atan2(m[6],-m[2]);
  return [...transformPoint(m,[0,0,0]),a*180/Math.PI,b*180/Math.PI,c*180/Math.PI];
}
export function poseFrame(pose) {
  const m=multiply(multiply(rotation(2,pose[3]*Math.PI/180),rotation(1,pose[4]*Math.PI/180)),rotation(2,pose[5]*Math.PI/180));
  pose.slice(0,3).forEach((v,i) => m[12+i]=v);
  return m;
}

// JointState positions are radians. Use names, never a gripper joint or the
// current topic picker order. Browser elapsed time also expires stalled HTTP.
export function armTelemetry(data, elapsedSeconds=0) {
  const offline = reason => ({live:false,angles:[...defaultArmJoints],reason,receivedAt:null});
  if (!data) return offline("로봇 상태 수신 대기 · 기본 자세");
  if (!["live","monitoring"].includes(data.state) || data.bridge_connected===false) return offline(
    data.state==="stale" ? "관절 수신 지연 · 기본 자세" : "로봇 미연결 · 기본 자세");
  const topic=data.topics?.find(t => t.name===data.display_joint_topic);
  const limit=Number.isFinite(data.config?.stale_seconds) && data.config.stale_seconds>0 ? data.config.stale_seconds : 3;
  if (topic?.state!=="live" || !Number.isFinite(topic.age_seconds) || topic.age_seconds<0 || topic.age_seconds+Math.max(0,elapsedSeconds)>limit)
    return offline("최신 관절 데이터 없음 · 기본 자세");
  const names=topic.message?.name,positions=topic.message?.position;
  if (!Array.isArray(names) || !Array.isArray(positions) || names.length!==positions.length) return offline("6축 관절 데이터 불완전 · 기본 자세");
  const angles=Array(6).fill(null);
  for (let i=0;i<names.length;i++) {
    const match=typeof names[i]==="string" && names[i].match(/(?:^|[/_])joint_?([1-6])$/i);
    if (!match) continue;
    const index=Number(match[1])-1;
    if (angles[index]!==null || !Number.isFinite(positions[i])) return offline("6축 관절 데이터 오류 · 기본 자세");
    angles[index]=positions[i]*180/Math.PI;
  }
  if (!angles.every(Number.isFinite)) return offline("6축 관절 데이터 불완전 · 기본 자세");
  return {live:true,angles,reason:"실제 관절 수신 중",receivedAt:topic.received_at ?? null};
}

// Existing camera preview points are relative to the selected teaching TCP.
// The physical arm remains at BASE zero regardless of the teaching selection.
export function cameraPointToBase(p,camera,anchor) {
  return [0,1,2].map(i => (anchor?.[i] ?? 0)+p.reduce((sum,v,j) => sum+v*camera[j][i],0));
}

// Display the measured register, never the goal or YOLO's recommended opening.
export function gripperVisualState(access,table,{sdk=false,busy=false,now=Date.now()}={}) {
  const f=access?.gripper_feedback;
  const result={position:null,opening_mm:null,target:Number.isInteger(access?.gripper_target)&&access.gripper_target>=0?access.gripper_target:null,
    observedAt:null,ageSeconds:null,current:null,mode:null,error:null,reason:"그리퍼 상태 미확인"};
  if(!sdk||access?.connection!=="connected"||!access.initialized||!access.gripper_supported)
    return {...result,reason:"RH-P12 SDK 연결 확인 필요"};
  if(busy)return {...result,reason:"그리퍼 응답 대기 중"};
  if(!f)return result;
  const observed=Date.parse(f.observed_at),age=(now-observed)/1000;
  if(!Number.isFinite(age)||age< -2||age>10)return {...result,observedAt:Number.isFinite(age)&&age>=0?f.observed_at:null,
    ageSeconds:Number.isFinite(age)&&age>=0?Math.floor(age):null,reason:"그리퍼 수신 지연 · 상태 읽기로 확인하세요."};
  result.observedAt=f.observed_at;result.ageSeconds=Math.max(0,Math.floor(age));
  result.error=Number.isFinite(f.error)?f.error:null;
  if(f.state!=="completed"||f.error!==0)return {...result,reason:f.error?"그리퍼 장치 오류 "+f.error:"그리퍼 결과 미확인"};
  if(!Number.isInteger(f.position)||f.position<0||f.position>700)return {...result,reason:"실제 위치 응답 오류"};
  result.position=f.position;result.current=Number.isFinite(f.current_ma)?f.current_ma:null;result.mode=Number.isFinite(f.mode)?f.mode:null;
  const stored=Array.isArray(table?.points)?table.points:[];
  if(stored.some(p=>!p||typeof p!=="object"))return {...result,reason:"실측 벌림표 오류"};
  const points=[...stored].sort((a,b)=>a.position-b.position);
  if(points.length<2)return {...result,reason:"실제 위치 확인 · 실측 벌림표를 등록하세요."};
  if(!points.every((p,i)=>Number.isInteger(p.position)&&p.position>=0&&p.position<=700&&Number.isFinite(p.width_mm)&&p.width_mm>=0&&
    (!i||(p.position>points[i-1].position&&p.width_mm<points[i-1].width_mm))))return {...result,reason:"실측 벌림표 오류"};
  for(let i=1;i<points.length;i++){
    const a=points[i-1],b=points[i];
    if(a.position<=f.position&&f.position<=b.position){
      const t=(f.position-a.position)/(b.position-a.position),width=(1-t)*a.width_mm+t*b.width_mm;
      return {...result,opening_mm:width,reason:"최근 실측 상태"};
    }
  }
  return {...result,reason:"실제 위치가 실측 벌림표 범위 밖입니다."};
}

// Existing e0509_simple flange attachment rotates the hand by +90° about Z.
export const gripperMount = flange => multiply(flange,poseFrame([0,0,0,90,0,0]));

// Photo-based illustrative housing and pads. Only the inner opening uses the
// device's calibrated millimetres. These meshes are never used as a TCP or path.
export function mountedGripperParts(opening) {
  const known=Number.isFinite(opening)&&opening>=0&&opening<=1e6,gap=known?opening:42,parts=[];
  function rings(bottom,top,color,kind,wire=false){
    const n=bottom.length,points=[...bottom,...top],faces=[Array.from({length:n},(_,i)=>n-1-i),Array.from({length:n},(_,i)=>n+i),
      ...Array.from({length:n},(_,i)=>[i,(i+1)%n,(i+1)%n+n,i+n])];
    parts.push({points,faces,color,kind,wire});
  }
  function circle(radius,z){return Array.from({length:24},(_,i)=>[radius*Math.cos(i*Math.PI/12),radius*Math.sin(i*Math.PI/12),z]);}
  function section(x,y,z,bevel){return [[-x+bevel,-y],[x-bevel,-y],[x,-y+bevel],[x,y-bevel],[x-bevel,y],[-x+bevel,y],[-x,y-bevel],[-x,-y+bevel]].map(([x,y])=>[x,y,z]);}
  function block(center,size,color,kind,bevel=2,wire=false){const [x,y,z]=center,[w,d,h]=size;
    rings(section(w/2,d/2,-h/2,bevel).map(p=>[p[0]+x,p[1]+y,p[2]+z]),
      section(w/2,d/2,h/2,bevel).map(p=>[p[0]+x,p[1]+y,p[2]+z]),color,kind,wire);
  }
  rings(circle(25,0),circle(25,12),[.39,.47,.49,1],"adapter");
  rings(circle(26,12),circle(26,18),[.16,.67,.78,1],"mount-ring");
  rings(section(32,25,18,6),section(44,30,79,7),[.76,.82,.82,1],"housing");
  block([0,0,83],[90,62,9],[.13,.17,.18,1],"base-plate",3);
  block([0,0,89],[Math.max(82,gap+24),20,6],[.4,.48,.5,1],"slide-rail",2);
  for(const sign of [-1,1]){
    block([sign*(gap/2+7),0,96],[14,25,12],[.49,.56,.57,1],"carriage",2,!known);
    block([sign*(gap/2+6),0,122],[12,25,42],[.09,.13,.14,1],"finger",2,!known);
  }
  return parts;
}
// Axis-aligned physical box. The supplied BASE point is the bottom, not volume center.
export function parcelBoxGeometry(box) {
  const size=box?.size_mm,center=box?.center_base_mm;
  if(!Array.isArray(size)||size.length!==3||!size.every(v=>Number.isFinite(v)&&v>=.001&&v<=1e6)||
    !Array.isArray(center)||center.length!==3||!center.every(v=>Number.isFinite(v)&&Math.abs(v)<=1e6))return null;
  const points=[];
  for(const z of [0,size[2]])for(const y of [-size[1]/2,size[1]/2])for(const x of [-size[0]/2,size[0]/2])
    points.push([center[0]+x,center[1]+y,center[2]+z]);
  return {points,bottom:[...center],top:[center[0],center[1],center[2]+size[2]],draft:Boolean(box.draft)};
}

// Browser-only kinematic preview. Limits come from the same E0509 URDF as the
// displayed mesh. This is deliberately independent of every robot command API.
export const simulationToolPose = Object.freeze([0,0,143,90,0,0]);
export const simulationJointLimits = Object.freeze([360,360,155,360,155,360]);
const vectorCross=([a,b,c],[x,y,z])=>[b*z-c*y,c*x-a*z,a*y-b*x];
function quaternion(m) {
  const t=m[0]+m[5]+m[10];let q;
  if(t>0){const s=Math.sqrt(t+1)*2;q=[(m[6]-m[9])/s,(m[8]-m[2])/s,(m[1]-m[4])/s,s/4];}
  else if(m[0]>m[5]&&m[0]>m[10]){const s=Math.sqrt(1+m[0]-m[5]-m[10])*2;q=[s/4,(m[4]+m[1])/s,(m[8]+m[2])/s,(m[6]-m[9])/s];}
  else if(m[5]>m[10]){const s=Math.sqrt(1+m[5]-m[0]-m[10])*2;q=[(m[4]+m[1])/s,s/4,(m[9]+m[6])/s,(m[8]-m[2])/s];}
  else{const s=Math.sqrt(1+m[10]-m[0]-m[5])*2;q=[(m[8]+m[2])/s,(m[9]+m[6])/s,s/4,(m[1]-m[4])/s];}
  const n=Math.hypot(...q);return q.map(v=>v/n);
}
function quaternionFrame([x,y,z,w]) {
  const m=identity();
  [m[0],m[1],m[2]]=[1-2*(y*y+z*z),2*(x*y+z*w),2*(x*z-y*w)];
  [m[4],m[5],m[6]]=[2*(x*y-z*w),1-2*(x*x+z*z),2*(y*z+x*w)];
  [m[8],m[9],m[10]]=[2*(x*z+y*w),2*(y*z-x*w),1-2*(x*x+y*y)];return m;
}
function rotationError(current,target) {
  const [x,y,z,w]=quaternion(target),[a,b,c,d]=quaternion(current);
  let q=[-w*a+x*d-y*c+z*b,-w*b+x*c+y*d-z*a,-w*c-x*b+y*a+z*d,w*d+x*a+y*b+z*c];
  if(q[3]<0)q=q.map(v=>-v);
  const n=Math.hypot(...q.slice(0,3)),angle=2*Math.atan2(n,Math.max(0,q[3]));
  return q.slice(0,3).map(v=>n>1e-12?v*angle/n:0);
}
export function interpolateArmFrame(a,b,t) {
  t=Math.max(0,Math.min(1,t));const qa=quaternion(a);let qb=quaternion(b),dot=qa.reduce((s,v,i)=>s+v*qb[i],0);
  if(dot<0){qb=qb.map(v=>-v);dot=-dot;}
  const theta=Math.acos(Math.min(1,dot)),s=Math.sin(theta);
  let q=qa.map((v,i)=>s<1e-5?v*(1-t)+qb[i]*t:(v*Math.sin((1-t)*theta)+qb[i]*Math.sin(t*theta))/s);
  const n=Math.hypot(...q);q=q.map(v=>v/n);const m=quaternionFrame(q);
  for(let i=12;i<15;i++)m[i]=a[i]*(1-t)+b[i]*t;return m;
}
function solveLinear(matrix,vector) {
  const rows=matrix.map((row,i)=>[...row,vector[i]]),n=vector.length;
  for(let i=0;i<n;i++){
    let pivot=i;for(let j=i+1;j<n;j++)if(Math.abs(rows[j][i])>Math.abs(rows[pivot][i]))pivot=j;
    if(Math.abs(rows[pivot][i])<1e-12)return null;
    [rows[i],rows[pivot]]=[rows[pivot],rows[i]];const divisor=rows[i][i];
    for(let k=i;k<=n;k++)rows[i][k]/=divisor;
    for(let j=0;j<n;j++)if(j!==i){const factor=rows[j][i];for(let k=i;k<=n;k++)rows[j][k]-=rows[i][k]*factor;}
  }
  return rows.map(row=>row[n]);
}
export function inverseArmFrame(target,seed,tool=identity()) {
  if(!Array.isArray(target)||target.length!==16||!target.every(Number.isFinite)||
    !Array.isArray(seed)||seed.length!==6||!seed.every((v,i)=>Number.isFinite(v)&&Math.abs(v)<=simulationJointLimits[i]+1e-5))return null;
  const reach=jointOrigins.reduce((s,[p])=>s+Math.hypot(...p),0)+Math.hypot(tool[12],tool[13],tool[14]);
  if(Math.hypot(target[12],target[13],target[14])>reach+1)return null;
  let angles=[...seed],damping=2;
  const error=current=>[...target.slice(12,15).map((v,i)=>v-current[12+i]),...rotationError(current,target).map(v=>v*100)];
  for(let iteration=0;iteration<180;iteration++){
    const frames=armFrames(angles),current=multiply(frames[6],tool),e=error(current),cost=Math.hypot(...e);
    if(Math.hypot(...e.slice(0,3))<.08&&Math.hypot(...e.slice(3))<.08)return angles;
    const columns=frames.slice(1).map(frame=>{
      const axis=frameAxis(frame,2),lever=current.slice(12,15).map((v,i)=>v-frame[12+i]);
      return [...vectorCross(axis,lever),...axis.map(v=>v*100)];
    });
    const j=Array.from({length:6},(_,i)=>columns.map(c=>c[i]));
    const matrix=j.map((row,i)=>j.map((other,k)=>row.reduce((s,v,n)=>s+v*other[n],0)+(i===k?damping*damping:0)));
    const solved=solveLinear(matrix,e);if(!solved)return null;
    const step=columns.map(c=>c.reduce((s,v,i)=>s+v*solved[i],0)),scale=Math.min(1,.15/Math.max(.001,...step.map(Math.abs)));
    const next=angles.map((v,i)=>Math.max(-simulationJointLimits[i],Math.min(simulationJointLimits[i],v+step[i]*scale*180/Math.PI)));
    if(Math.hypot(...error(multiply(armFrames(next)[6],tool)))<cost){angles=next;damping=Math.max(.15,damping*.7);}
    else{damping*=3;if(damping>10000)break;}
  }
  return null;
}
export async function planArmSimulation(startAngles,route,{toolPose=simulationToolPose,signal,yieldFrame=()=>new Promise(resolve=>setTimeout(resolve,0))}={}) {
  const finitePose=p=>Array.isArray(p)&&p.length===6&&p.every(v=>Number.isFinite(v)&&Math.abs(v)<=1e6);
  if(!finitePose(toolPose)||!route?.length||route.some(p=>!finitePose(p.pose)))throw Error("각 지점의 X·Y·Z·A·B·C를 모두 입력하세요.");
  if(!finitePose(startAngles)||startAngles.some((v,i)=>Math.abs(v)>simulationJointLimits[i]+1e-5))throw Error("시작 관절각이 모델의 관절 범위를 벗어납니다.");
  const tool=poseFrame(toolPose),samples=[{time:0,angles:[...startAngles],segment:0}],path=[],extents=[];
  let current=multiply(armFrames(startAngles)[6],tool),angles=[...startAngles],time=0;
  for(let segment=0;segment<route.length;segment++){
    const start=current,target=poseFrame(route[segment].pose),distance=Math.hypot(...target.slice(12,15).map((v,i)=>v-start[12+i])),turn=Math.hypot(...rotationError(start,target));
    const steps=Math.max(1,Math.ceil(Math.max(distance/5,turn/(Math.PI/60))));
    if(steps>500)throw Error(route[segment].title+": 표시 범위를 벗어난 경로입니다.");
    const duration=Math.max(.5,distance/100,turn/(Math.PI/6));
    for(let i=1;i<=steps;i++){
      if(signal?.aborted)throw new DOMException("Simulation cancelled","AbortError");
      const frame=interpolateArmFrame(start,target,i/steps),next=inverseArmFrame(frame,angles,tool);
      if(!next||next.some((v,k)=>Math.abs(v-angles[k])>45))throw Error(route[segment].title+": 현재 자세에서 이어지는 직선 경로의 해를 찾지 못했습니다. 좌표·방향 또는 시뮬레이션 TCP를 조정하세요.");
      time+=Math.max(duration/steps,...next.map((v,k)=>Math.abs(v-angles[k])/90));angles=next;
      samples.push({time,angles:[...angles],segment});
      if(i%6===0)await yieldFrame();
    }
    time+=.6;samples.push({time,angles:[...angles],segment});current=target;
  }
  for(const s of samples){const frames=armFrames(s.angles),tcp=multiply(frames[6],tool);path.push(tcp.slice(12,15));extents.push(...frames.map(f=>f.slice(12,15)),tcp.slice(12,15));}
  const low=[0,1,2].map(i=>Math.min(...extents.map(p=>p[i]))-100),high=[0,1,2].map(i=>Math.max(...extents.map(p=>p[i]))+100),bounds=[];
  for(const x of [low[0],high[0]])for(const y of [low[1],high[1]])for(const z of [low[2],high[2]])bounds.push([x,y,z]);
  return {samples,duration:time,tool,route:route.map(p=>({...p,pose:[...p.pose]})),path,bounds};
}
export function sampleArmSimulation(plan,seconds) {
  const time=Math.max(0,Math.min(plan.duration,seconds));let lo=0,hi=plan.samples.length-1;
  while(lo+1<hi){const mid=(lo+hi)>>1;if(plan.samples[mid].time<=time)lo=mid;else hi=mid;}
  const a=plan.samples[lo],b=plan.samples[hi],t=(time-a.time)/Math.max(1e-9,b.time-a.time);
  const angles=a.angles.map((v,i)=>v*(1-t)+b.angles[i]*t);
  const opening=Number.isFinite(a.opening_mm)?a.opening_mm*(1-t)+b.opening_mm*t:null;
  return {angles,tcp:framePose(multiply(armFrames(angles)[6],plan.tool)),segment:t<=1e-9?a.segment:b.segment,progress:time/plan.duration,complete:time>=plan.duration,
    opening_mm:opening,holding:Boolean(t>=1?b.holding:a.holding),released:Boolean(t>=1?b.released:a.released)};
}

export function inverseRigid(m){const r=identity();for(let i=0;i<3;i++)for(let j=0;j<3;j++)r[i*4+j]=m[j*4+i];for(let i=0;i<3;i++)r[12+i]=-[0,1,2].reduce((s,j)=>s+r[j*4+i]*m[12+j],0);return r;}
export function circularFrames(start,via,end){
  const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0),cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
  const p=start.slice(12,15),q=via.slice(12,15),r=end.slice(12,15),a=q.map((v,i)=>v-p[i]),b=r.map((v,i)=>v-p[i]),n=cross(a,b),n2=dot(n,n),a2=dot(a,a),b2=dot(b,b);
  if(![...p,...q,...r].every(Number.isFinite)||Math.min(Math.sqrt(a2),Math.sqrt(b2),Math.hypot(...q.map((v,i)=>v-r[i])))<.01||n2<=1e-8*a2*b2)throw Error('movec의 시작·경유·목표 위치는 서로 다르고 일직선에 놓이지 않아야 합니다.');
  const an=cross(a,n),bn=cross(b,n),center=p.map((v,i)=>v+(a2*bn[i]-b2*an[i])/(2*n2)),radius=Math.hypot(...p.map((v,i)=>v-center[i])),x=p.map((v,i)=>(v-center[i])/radius),y=cross(n.map(v=>v/Math.sqrt(n2)),x);
  const angle=v=>{const d=v.map((c,i)=>c-center[i]);return (Math.atan2(dot(d,y),dot(d,x))+2*Math.PI)%(2*Math.PI);};let middle=angle(q),sweep=angle(r);if(middle>sweep){middle-=2*Math.PI;sweep-=2*Math.PI;}
  const fraction=middle/sweep;
  return {length:Math.abs(sweep)*radius,fraction,at(t){t=Math.max(0,Math.min(1,t));const frame=t<=fraction?interpolateArmFrame(start,via,t/fraction):interpolateArmFrame(via,end,(t-fraction)/(1-fraction));for(let i=0;i<3;i++)frame[12+i]=center[i]+radius*(Math.cos(sweep*t)*x[i]+Math.sin(sweep*t)*y[i]);return frame;}};
}
export function endpointIK(frame,seed,tool,preferred){
  const bearing=Math.atan2(frame[13],frame[12])*180/Math.PI;
  const bases=[bearing,bearing-360,bearing+360].filter(v=>Math.abs(v)<=360).sort((a,b)=>Math.abs(a-(preferred??seed[0]))-Math.abs(b-(preferred??seed[0])));
  const tilt=Math.abs(frame[10])<.5?90:180;
  const seeds=[seed,...bases.flatMap(base=>[seed.map((v,i)=>i===0?base:v),...[-30,0,30,60].flatMap(shoulder=>[60,90,120,-90].map(elbow=>[base,shoulder,elbow,0,Math.max(-155,Math.min(155,tilt-shoulder-elbow)),0]))])];
  for(const q of seeds){const result=inverseArmFrame(frame,q,tool);if(result)return result;}return null;
}
export async function planScenarioSimulation(startAngles,recipe,{toolPose=simulationToolPose,signal,yieldFrame=()=>new Promise(resolve=>setTimeout(resolve,0))}={}){
  if(!recipe.simulation_only||recipe.errors?.length)throw Error(recipe.errors?.join(" ")||"가상 시나리오만 재생할 수 있습니다.");
  const tool=poseFrame(toolPose),samples=[],gates=[],route=[],config=recipe.config;
  let angles=[...startAngles],time=0,opening=recipe.idle_opening_mm,holding=false,released=false,counter=0;
  function push(segment){samples.push({time,angles:[...angles],segment,opening_mm:opening,holding,released});}
  push(0);
  async function checkpoint(){if(signal?.aborted)throw new DOMException("Cancelled","AbortError");if(++counter%8===0)await yieldFrame();}
  async function joint(goal,segment,speed){
    if(!goal||goal.some((v,i)=>!Number.isFinite(v)||Math.abs(v)>simulationJointLimits[i]))throw Error("관절 범위를 확인하세요.");
    const from=[...angles],distance=Math.max(...goal.map((v,i)=>Math.abs(v-from[i])));
    const peak=Math.min(speed.speed,Math.sqrt(distance*speed.acceleration)),ramp=peak/speed.acceleration,cruise=peak?Math.max(0,(distance-peak*ramp)/peak):0,duration=Math.max(.2,2*ramp+cruise);
    const count=Math.max(2,Math.ceil(Math.max(distance/2,duration*5)));
    for(let i=1;i<=count;i++){await checkpoint();const t=duration*i/count,d=t<ramp?.5*speed.acceleration*t*t:t<=ramp+cruise?.5*peak*ramp+peak*(t-ramp):distance-.5*speed.acceleration*(duration-t)**2,s=distance?Math.max(0,Math.min(1,d/distance)):1;angles=from.map((v,k)=>v+(goal[k]-v)*s);time+=duration/count;push(segment);}
  }
  async function line(goal,segment,speed){
    const from=multiply(armFrames(angles)[6],tool),target=poseFrame(goal),d=Math.hypot(...goal.slice(0,3).map((v,i)=>v-from[12+i]));
    const count=Math.max(2,Math.ceil(d/2));if(count>1000)throw Error("직선 경로가 너무 깁니다.");
    const duration=Math.max(.2,d/speed.speed+speed.speed/speed.acceleration);
    for(let i=1;i<=count;i++){await checkpoint();const q=inverseArmFrame(interpolateArmFrame(from,target,i/count),angles,tool);if(!q)throw Error("직선 경로의 자세를 계산할 수 없습니다.");angles=q;time+=duration/count;push(segment);}
  }
  async function circle(goal,via,segment,speed){
    if(!Array.isArray(via)||via.length!==6||!via.every(Number.isFinite))throw Error('movec의 원호 경유 TCP 여섯 값을 입력하세요.');
    const arc=circularFrames(multiply(armFrames(angles)[6],tool),poseFrame(via),poseFrame(goal)),count=Math.max(2,Math.ceil(arc.length/2)),duration=Math.max(.2,arc.length/speed.speed+speed.speed/speed.acceleration);
    if(count>3000)throw Error('원호 경로가 너무 깁니다. 경유 TCP를 확인하세요.');
    for(let i=1;i<=count;i++){await checkpoint();const q=inverseArmFrame(arc.at(i/count),angles,tool);if(!q)throw Error('원호 경로의 자세를 계산할 수 없습니다.');angles=q;time+=duration/count;push(segment);}
  }
  async function armMove(step,segment){
    const motion=step.motion||(step.kind==='joint'||step.kind==='ready'?'movej':step.kind==='circle'?'movec':'movel'),space=step.number===1?'joint':step.space,target=step.number===1?config.standby_joints:step.target;
    if(motion==='movej'){
      let q=space==='joint'?target:endpointIK(poseFrame(target),angles,tool,config.waypoint1_joints?.[0]);if(!q)throw Error('관절 목표의 해를 찾지 못했습니다.');
      if(step.j1_only){if(q.slice(1).some((v,i)=>Math.abs(v-angles[i+1])>.5)||Math.abs(Math.abs(q[0]-angles[0])-180)>.5)throw Error('J1 단독 회전의 시작 관절각이 교시값과 다릅니다. 이전 경유점 이동을 movej로 설정하세요.');q=[angles[0]+(q[0]>angles[0]?180:-180),...angles.slice(1)];}
      await joint(q,segment,step.speed);
    }else{
      const goal=space==='joint'?framePose(multiply(armFrames(target)[6],tool)):target;
      if(motion==='movel')await line(goal,segment,step.speed);else if(motion==='movec')await circle(goal,step.via_pose,segment,step.speed);else throw Error('지원하지 않는 이동 방식입니다.');
      if(space==='joint'&&angles.some((v,i)=>Math.abs(v-target[i])>.5))throw Error('교시한 관절 자세와 다른 해에 도달했습니다. 이 구간은 movej로 설정하세요.');
    }
  }
  async function hand(goal,segment,speed){
    const from=opening,duration=Math.max(.5,Math.abs(goal-from)/Math.max(1,speed.speed*2));
    for(let i=1;i<=20;i++){await checkpoint();opening=from+(goal-from)*i/20;time+=duration/20;push(segment);}
  }
  for(const step of recipe.steps){const n=step.number,segment=n-1;
    try{
      if(n===1){await armMove(step,segment);await hand(recipe.idle_opening_mm,segment,config.profiles[config.stages['1'].profile].gripper);}
      else if(n===2){time+=.4;push(segment);}
      else if(['joint','line','circle'].includes(step.kind))await armMove(step,segment);
      else if(step.kind==='gripper'){
        await hand(n===6?recipe.width_mm:step.opening_mm,segment,step.speed);
        if(n===6)holding=true;
        if(n===12){holding=false;released=true;}
      }
      time+=.3;push(segment);
      if(step.gate)gates.push({time,action:step.gate,step:n});
      route.push({title:step.title,pose:framePose(multiply(armFrames(angles)[6],tool)),marker:[3,5,8,9,10,11].includes(n),number:n,motion:step.motion,via_pose:step.via_pose});
    }catch(error){if(error.name==='AbortError')throw error;throw Error(`${n}단계 ${step.title}: ${error.message}`);}
  }
  const path=samples.map(s=>multiply(armFrames(s.angles)[6],tool).slice(12,15)),points=samples.flatMap(s=>armFrames(s.angles).map(f=>f.slice(12,15)));
  const lo=[0,1,2].map(i=>Math.min(...points.map(p=>p[i]))-120),hi=[0,1,2].map(i=>Math.max(...points.map(p=>p[i]))+120),bounds=[];
  for(const x of [lo[0],hi[0]])for(const y of [lo[1],hi[1]])for(const z of [lo[2],hi[2]])bounds.push([x,y,z]);
  return {samples,path,bounds,route,tool,gates,recipe,duration:time};
}
