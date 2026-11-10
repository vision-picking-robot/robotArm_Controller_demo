const assert=require('node:assert/strict'),test=require('node:test'),fs=require('node:fs');
const mod=import('../app/static/robot-arm.js');
const fixture=region=>JSON.parse(fs.readFileSync(__dirname+'/fixtures/scenario-'+region+'.json'));
for(const region of ['floor','shelf'])for(const turn of [180,-180])test('17-step '+region+' J1 '+turn+'° preview keeps single-axis turns, straight approaches, gates and exact opening restoration',async()=>{
 const m=await mod,recipe=fixture(region);recipe.config.turn_degrees=turn;
 for(const number of [9,14])recipe.steps[number-1].target[0]=recipe.config.waypoint1_joints[0]+turn;
 const plan=await m.planScenarioSimulation(m.defaultArmJoints,recipe,{yieldFrame:()=>Promise.resolve()});
 assert.deepEqual(plan.gates.map(g=>g.step),[6,11]);
 for(const number of [9,15]){
  const segment=plan.samples.filter(s=>s.segment===number-1);const previous=plan.samples.filter(s=>s.segment===number-2).at(-1);
  assert.equal(segment.at(-1).angles[0]-previous.angles[0],number===9?turn:-turn);
  for(const s of segment)for(let i=1;i<6;i++)assert.ok(Math.abs(s.angles[i]-previous.angles[i])<1e-8);
 }
 const points=plan.samples.filter(s=>s.segment===4).map(s=>m.multiply(m.armFrames(s.angles)[6],plan.tool).slice(12,15));
 for(const p of points){if(region==='floor')assert.ok(Math.abs(p[0]-recipe.pose[0])<.1&&Math.abs(p[1]-recipe.pose[1])<.1);else assert.ok(Math.abs(p[2]-recipe.pose[2])<.1);}
 const grip=m.sampleArmSimulation(plan,plan.gates[0].time);assert.equal(grip.holding,true);assert.equal(grip.segment,5);
 const release=plan.samples.filter(s=>s.segment===11).at(-1);assert.equal(release.released,true);assert.ok(Math.abs(release.opening_mm-recipe.opening_mm)<1e-8);
 const end=m.sampleArmSimulation(plan,plan.duration);assert.equal(end.complete,true);assert.deepEqual(end.angles,recipe.config.standby_joints);assert.equal(end.opening_mm,recipe.idle_opening_mm);
 for(const s of plan.samples)for(let i=0;i<6;i++)assert.ok(Math.abs(s.angles[i])<=m.simulationJointLimits[i]+1e-8);
});
test('unconfigured and non-virtual recipes cannot start browser playback',async()=>{
 const m=await mod;await assert.rejects(m.planScenarioSimulation(m.defaultArmJoints,{simulation_only:false}),/가상/);
 await assert.rejects(m.planScenarioSimulation(m.defaultArmJoints,{simulation_only:true,errors:['1차 경유점 미설정']}),/미설정/);
});
