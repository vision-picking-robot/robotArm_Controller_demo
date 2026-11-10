const assert=require('node:assert/strict'),test=require('node:test');
const mod=import('../app/static/robot-arm.js');
test('movec passes the through point and follows both a short arc and a long arc',async()=>{
 const m=await mod,start=m.poseFrame([1,0,0,0,0,0]);
 for(const [via,end,length]of [[[0,1,0],[-1,0,0],Math.PI],[[0,-1,0],[0,1,0],1.5*Math.PI]]){
  const arc=m.circularFrames(start,m.poseFrame([...via,0,0,0]),m.poseFrame([...end,0,0,0]));
  assert.ok(Math.abs(arc.length-length)<1e-8);
  assert.ok(Math.hypot(...arc.at(arc.fraction).slice(12,15).map((v,i)=>v-via[i]))<1e-8);
  for(let t=0;t<=1;t+=.05)assert.ok(Math.abs(Math.hypot(...arc.at(t).slice(12,15))-1)<1e-8);
 }
});
test('movec rejects coincident, collinear and almost collinear points',async()=>{
 const m=await mod,p=v=>m.poseFrame([...v,0,0,0]);
 for(const via of [[0,0,0],[5,0,0],[5,.00001,0]])assert.throws(()=>m.circularFrames(p([0,0,0]),p(via),p([10,0,0])),/서로 다르고/);
});
test('changing a joint-taught goal to movel or movec changes the simulated Cartesian path and reaches the same joint pose',async()=>{
 const m=await mod,tool=m.poseFrame(m.simulationToolPose),start=[...m.defaultArmJoints],goal=[10,0,90,0,90,0],via=m.framePose(m.multiply(m.armFrames([5,0,90,0,90,0])[6],tool));
 const make=motion=>({simulation_only:true,errors:[],idle_opening_mm:80,config:{waypoint1_joints:goal},steps:[{number:8,kind:motion==='movec'?'circle':'line',motion,space:'joint',target:goal,via_pose:motion==='movec'?via:null,speed:{speed:10,acceleration:10}}]});
 const line=await m.planScenarioSimulation(start,make('movel'),{yieldFrame:()=>Promise.resolve()}),arc=await m.planScenarioSimulation(start,make('movec'),{yieldFrame:()=>Promise.resolve()});
 for(const plan of [line,arc])for(let i=0;i<6;i++)assert.ok(Math.abs(plan.samples.at(-1).angles[i]-goal[i])<.5);
 const point=s=>m.multiply(m.armFrames(s.angles)[6],tool).slice(12,15),a=point(line.samples[0]),b=point(line.samples.at(-1)),mid=a.map((v,i)=>(v+b[i])/2);
 const lineMid=point(line.samples[Math.floor(line.samples.length/2)]),arcMid=point(arc.samples[Math.floor(arc.samples.length/2)]);
 assert.ok(Math.abs(Math.hypot(...arcMid.slice(0,2))-Math.hypot(...a.slice(0,2)))<.1);
 assert.ok(Math.hypot(...arcMid.map((v,i)=>v-mid[i]))>1);
 assert.ok(Math.abs(lineMid[2]-a[2])<.1);
});
