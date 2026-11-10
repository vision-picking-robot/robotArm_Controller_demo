const assert=require('node:assert/strict');
const test=require('node:test');
const mod=import('../app/static/robot-arm.js');
test('parcel box uses a BASE bottom-face center with independent XYZ dimensions',async()=>{
  const {parcelBoxGeometry}=await mod;
  const box=parcelBoxGeometry({size_mm:[400,300,250],center_base_mm:[500,-20,-120],draft:true});
  assert.deepEqual(box.bottom,[500,-20,-120]);assert.deepEqual(box.top,[500,-20,130]);assert.equal(box.draft,true);
  assert.deepEqual(box.points,[[300,-170,-120],[700,-170,-120],[300,130,-120],[700,130,-120],
    [300,-170,130],[700,-170,130],[300,130,130],[700,130,130]]);
  assert.deepEqual(parcelBoxGeometry({size_mm:[1,2,3],center_base_mm:[0,0,0]}).bottom,[0,0,0]);
  for(const box of [null,{}, {size_mm:[0,2,3],center_base_mm:[0,0,0]}, {size_mm:[1,2,3],center_base_mm:[0,NaN,0]},
    {size_mm:[1,2,3],center_base_mm:[0,0]}, {size_mm:[1,'2',3],center_base_mm:[0,0,0]}])assert.equal(parcelBoxGeometry(box),null);
});
const near=(a,b,epsilon=1e-3)=>a.forEach((v,i)=>assert.ok(Math.abs(v-b[i])<epsilon,`${a} != ${b}`));
const sample=()=>({state:'monitoring',bridge_connected:true,config:{stale_seconds:3},display_joint_topic:'/dsr01/monitor/joint_states',topics:[{
  name:'/dsr01/monitor/joint_states',state:'live',age_seconds:.1,received_at:'2026-11-09T00:00:00Z',
  message:{name:['joint_1','joint_2','joint_3','joint_4','joint_5','joint_6'],position:[0,0,Math.PI/2,0,Math.PI/2,0]}
}]});

test('mounted hand uses measured position and the saved opening table rather than the command goal',async()=>{
  const {gripperVisualState}=await mod,now=Date.parse('2026-11-09T00:00:00Z');
  const access={connection:'connected',initialized:true,gripper_supported:true,gripper_target:700,
    gripper_feedback:{position:350,current_ma:14,mode:5,error:0,state:'completed',observed_at:new Date(now).toISOString()}};
  const table={points:[{position:0,width_mm:80},{position:700,width_mm:0}]};
  const state=gripperVisualState(access,table,{sdk:true,now});
  assert.equal(state.position,350);assert.equal(state.target,700);assert.equal(state.opening_mm,40);
  access.gripper_feedback.position=700;assert.equal(gripperVisualState(access,table,{sdk:true,now}).opening_mm,0);
  access.gripper_feedback.position=0;assert.equal(gripperVisualState(access,table,{sdk:true,now}).opening_mm,80);
  assert.equal(gripperVisualState(access,table,{sdk:true,now:now+11000}).position,null);
  assert.equal(gripperVisualState(access,table,{sdk:true,now:now+11000}).opening_mm,null);
  assert.equal(gripperVisualState(access,table,{sdk:true,now,busy:true}).opening_mm,null);
  access.gripper_feedback.error=2;assert.equal(gripperVisualState(access,table,{sdk:true,now}).opening_mm,null);
  access.connection='disconnected';assert.equal(gripperVisualState(access,table,{sdk:true,now}).opening_mm,null);
});
test('missing or out-of-range hand calibration keeps actual register values without inventing a width',async()=>{
  const {gripperVisualState}=await mod,now=Date.now();
  const a={connection:'connected',initialized:true,gripper_supported:true,gripper_feedback:{position:350,error:0,state:'completed',observed_at:new Date(now).toISOString()}};
  for(const table of [null,{points:[]},{points:[{position:0,width_mm:80},{position:200,width_mm:30}]},{points:[{position:0,width_mm:10},{position:700,width_mm:80}]}]){
    const state=gripperVisualState(a,table,{sdk:true,now});assert.equal(state.position,350);assert.equal(state.opening_mm,null);
  }
  a.gripper_feedback.position=701;assert.equal(gripperVisualState(a,null,{sdk:true,now}).position,null);
});
test('mounted jaws reflect the calibrated inner opening, and attach to the flange through its rotation',async()=>{
  const {mountedGripperParts,gripperMount,armFrames,defaultArmJoints,transformPoint,frameAxis}=await mod;
  for(const opening of [0,40,80]){
    const fingers=mountedGripperParts(opening).filter(p=>p.kind==='finger');assert.equal(fingers.length,2);
    const left=Math.max(...fingers[0].points.map(p=>p[0])),right=Math.min(...fingers[1].points.map(p=>p[0]));
    near([right-left],[opening],1e-10);assert.ok(fingers.every(p=>!p.wire));
  }
  assert.ok(mountedGripperParts(null).filter(p=>p.kind==='finger').every(p=>p.wire));
  const flange=armFrames(defaultArmJoints)[6],mount=gripperMount(flange);
  near(transformPoint(mount,[0,0,0]),transformPoint(flange,[0,0,0]));
  near(frameAxis(mount,2),frameAxis(flange,2));near(frameAxis(mount,0),frameAxis(flange,1));
});

test('E0509 fallback has the documented six angles and a downward flange at (373, 0, 405) mm',async()=>{
  const {armFrames,frameAxis,defaultArmJoints,transformPoint}=await mod;
  assert.deepEqual(defaultArmJoints,[0,0,90,0,90,0]);
  const frames=armFrames(defaultArmJoints);assert.equal(frames.length,7);
  near(transformPoint(frames[0],[0,0,0]),[0,0,0]);
  near(transformPoint(frames[6],[0,0,0]),[373,0,405],.01);
  near(frameAxis(frames[6],2),[0,0,-1],1e-4);
  for(const m of frames)for(let i=0;i<3;i++)for(let j=0;j<3;j++){
    const a=frameAxis(m,i),b=frameAxis(m,j);near([a.reduce((n,v,k)=>n+v*b[k],0)],[i===j?1:0],1e-10);
  }
});
test('J1 rotates the physical flange around BASE; J6 rotates its tool X/Y without moving its origin',async()=>{
  const {armFrames,frameAxis,transformPoint}=await mod;
  const pose=q=>armFrames(q)[6],base=pose([0,0,90,0,90,0]),r=pose([90,0,90,0,90,0]);
  near(transformPoint(r,[0,0,0]),[0,373,405],.01);
  const roll=pose([0,0,90,0,90,90]);
  near(transformPoint(roll,[0,0,0]),transformPoint(base,[0,0,0]));
  near(frameAxis(roll,2),frameAxis(base,2));near(frameAxis(roll,0),frameAxis(base,1));
});
test('flange ABC uses the same ZYZ convention as existing taught poses, including both singular poles',async()=>{
  const {framePose,poseFrame}=await mod;
  for(const pose of [[20,-30,400,35,75,-120],[0,0,0,40,0,20],[0,0,0,40,180,20]]){
    const m=poseFrame(pose);near(poseFrame(framePose(m)),m,1e-8);
  }
});
test('telemetry reorders six named radian joints and excludes gripper joints',async()=>{
  const {armTelemetry}=await mod,data=sample(),topic=data.topics[0];
  topic.message={name:['gripper_joint','joint_6','joint_2','joint_4','joint_1','joint_5','joint_3'],position:[999,6,2,4,1,5,3].map(v=>v*Math.PI/180)};
  const result=armTelemetry(data);assert.equal(result.live,true);near(result.angles,[1,2,3,4,5,6],1e-10);
  assert.equal(result.receivedAt,topic.received_at);
});
test('offline, delayed HTTP, stale topics and incomplete or duplicate joints use the exact fallback',async()=>{
  const {armTelemetry,defaultArmJoints}=await mod;
  const cases=[null,...['offline','waiting','robot_disconnected','stale'].map(state=>({...sample(),state}))];
  let data=sample();data.topics[0].state='stale';cases.push(data);
  data=sample();data.topics[0].message.position[2]=NaN;cases.push(data);
  data=sample();data.topics[0].message.name[0]='joint_2';cases.push(data);
  data=sample();data.topics[0].message.name[0]='unknown';cases.push(data);
  data=sample();data.topics[0].message.position.pop();cases.push(data);
  data=sample();data.bridge_connected=false;cases.push(data);
  for(const c of cases){const r=armTelemetry(c);assert.equal(r.live,false);assert.deepEqual(r.angles,defaultArmJoints);}
  assert.equal(armTelemetry(sample(),2).live,true);
  assert.equal(armTelemetry(sample(),3).live,false);
});
test('teaching anchors move the virtual guides without moving the physical robot',async()=>{
  const {cameraPointToBase,armFrames,defaultArmJoints}=await mod;
  const basis=[[0,1,0],[-1,0,0],[0,0,1]];
  const original=armFrames(defaultArmJoints);
  near(cameraPointToBase([-410,10,20],basis,[400,-50,100]),[390,-460,120]);
  near(cameraPointToBase([-410,10,20],basis,[450,40,170]),[440,-370,190]);
  assert.deepEqual(armFrames(defaultArmJoints),original);
});
test('the embedded CAD contains complete valid indexed geometry for the base and all six moving links',async()=>{
  const {e0509Model}=await import('../app/static/robot-e0509-model.js');
  assert.deepEqual([...new Set(e0509Model.parts.map(p=>p.link))],[0,1,2,3,4,5,6]);
  for(const p of e0509Model.parts){
    const v=Buffer.from(p.v,'base64'),f=Buffer.from(p.f,'base64'),n=Buffer.from(p.n,'base64');
    assert.equal(v.length%6,0);assert.equal(n.length,v.length/2);assert.equal(f.length%6,0);
    assert.ok(v.length>100&&f.length>100);
    for(let i=0;i<f.length;i+=2)assert.ok(f.readUInt16LE(i)<v.length/6);
  }
});
