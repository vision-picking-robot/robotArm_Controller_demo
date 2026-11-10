const assert=require('node:assert/strict'),test=require('node:test');
const mod=import('../app/static/robot-arm.js');
const close=(a,b,epsilon=.1)=>a.forEach((v,i)=>assert.ok(Math.abs(v-b[i])<epsilon,`${a} != ${b}`));
const yieldFrame=()=>Promise.resolve();

test('simulation IK respects all six pose axes, the flange TCP offset and E0509 joint limits',async()=>{
  const m=await mod,tool=m.poseFrame([12,-7,143,90,3,-4]),seed=[10,5,80,15,75,-10];
  const target=m.multiply(m.armFrames([15,12,75,22,85,-5])[6],tool);
  const angles=m.inverseArmFrame(target,seed,tool);assert.ok(angles);
  close(m.multiply(m.armFrames(angles)[6],tool),target,.08);
  angles.forEach((v,i)=>assert.ok(Math.abs(v)<=m.simulationJointLimits[i]));
  assert.equal(m.inverseArmFrame(target,[0,0,180,0,0,0],tool),null);
  const far=m.poseFrame([5000,0,0,0,90,0]);assert.equal(m.inverseArmFrame(far,seed,tool),null);
});
test('orientation interpolation follows the short rotation across Euler wrap and handles a half turn',async()=>{
  const m=await mod,a=m.poseFrame([0,0,0,179,0,0]),b=m.poseFrame([20,40,60,-179,0,0]);
  const mid=m.interpolateArmFrame(a,b,.5);close(mid.slice(12,15),[10,20,30],1e-9);assert.ok(mid[0]<-.999);
  const half=m.interpolateArmFrame(m.identity(),m.poseFrame([0,0,0,180,0,0]),.5);assert.ok(Math.abs(half[0])<1e-8);assert.ok(Math.abs(half[1])>.999);
});
test('a preview plans each straight segment in order, holds each point and reaches the requested TCP',async()=>{
  const m=await mod,start=[...m.defaultArmJoints],tool=m.poseFrame(m.simulationToolPose);
  const initial=m.framePose(m.multiply(m.armFrames(start)[6],tool));
  const route=[{title:'경유점',pose:[initial[0]+60,initial[1]+30,initial[2]+40,...initial.slice(3)]},
    {title:'박스',pose:[initial[0]+100,initial[1]+70,initial[2]+20,initial[3]+20,initial[4]-10,initial[5]]}];
  const plan=await m.planArmSimulation(start,route,{yieldFrame});
  assert.deepEqual(plan.samples[0].angles,start);assert.deepEqual(plan.route,route);
  assert.ok(plan.samples.some((s,i)=>i&&s.segment===0&&plan.samples[i-1].segment===0&&JSON.stringify(s.angles)===JSON.stringify(plan.samples[i-1].angles)));
  const final=m.sampleArmSimulation(plan,plan.duration+100);
  assert.equal(final.complete,true);assert.equal(final.progress,1);assert.equal(final.segment,1);
  close(m.poseFrame(final.tcp),m.poseFrame(route[1].pose),.08);
  assert.ok(plan.samples.some(s=>s.angles.some((v,i)=>Math.abs(v-start[i])>2)));
  const hold=m.sampleArmSimulation(plan,-1);close(hold.angles,start,1e-9);
  plan.samples.forEach(s=>s.angles.forEach((v,i)=>assert.ok(Number.isFinite(v)&&Math.abs(v)<=m.simulationJointLimits[i])));
  assert.equal(plan.bounds.length,8);assert.equal(plan.path.length,plan.samples.length);
  // Planning never mutates the editor's pose or the current telemetry seed.
  assert.deepEqual(start,m.defaultArmJoints);assert.deepEqual(plan.route,route);
});
test('invalid, unreachable and cancelled previews do not return a partial playable route',async()=>{
  const m=await mod;
  await assert.rejects(m.planArmSimulation(m.defaultArmJoints,[{title:'미입력',pose:[0,0,NaN,0,0,0]}]),/모두 입력/);
  await assert.rejects(m.planArmSimulation(m.defaultArmJoints,[{title:'도달 불가',pose:[2000,0,0,0,90,0]}],{yieldFrame}),/해를 찾지|표시 범위/);
  const controller=new AbortController();controller.abort();
  await assert.rejects(m.planArmSimulation(m.defaultArmJoints,[{title:'취소',pose:[450,40,280,-90,180,0]}],{signal:controller.signal}),{name:'AbortError'});
});
