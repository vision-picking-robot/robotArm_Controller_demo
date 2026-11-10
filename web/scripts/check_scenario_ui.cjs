const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'),assert=require('node:assert/strict'),path=require('node:path');
process.chdir(path.resolve(__dirname,'..'));
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader','--disable-background-networking']});
 try{
  const ctx=await browser.newContext({viewport:{width:1360,height:1000}}),p=await ctx.newPage();p.setDefaultTimeout(15000);
  const base=process.env.SCENARIO_QA_BASE||'http://127.0.0.1:8012',errors=[],commands=[];
  const qaURL=new URL(base);assert.ok(['localhost','127.0.0.1'].includes(qaURL.hostname)&&!['8000','8011','8080'].includes(qaURL.port),'Use an isolated loopback QA server');
  const initial=await(await ctx.request.get(base+'/api/robot/status')).json();assert.equal(initial.config.access_backend,'ros');assert.equal(initial.config.ros_pc_ip,'','QA server must have device communication disabled');let lastPreview=null,delayPreview=false;
  let config=await(await ctx.request.get(base+'/api/robot/pick-scenario')).json();config.waypoint1_joints=[-180,0,60,0,120,0];config.virtual.floor={center_base_mm:[-450,0,20],size_mm:[40,40,40]};config.virtual.shelf={center_base_mm:[-450,0,450],size_mm:[40,40,40]};
  for(const stage of Object.values(config.stages)){stage.motion=null;stage.via_pose=null;}
  assert.equal((await ctx.request.put(base+'/api/robot/pick-scenario',{data:config})).status(),200);
  const status=await(await ctx.request.get(base+'/api/robot/status')).json();assert.equal((await ctx.request.put(base+'/api/robot/work-points/box',{data:{revision:status.work_points.box.revision,pose:[450,0,120,0,180,0]}})).status(),200);
  p.on('pageerror',e=>{errors.push(e.message);console.log('PAGE ERROR',e.message);});
  await ctx.routeWebSocket('**/ws/detections*',s=>{const timer=setInterval(()=>s.send(JSON.stringify({event:'status',ready:false,state:'camera_off'})),500);timer.unref();s.onClose(()=>clearInterval(timer));});
  await ctx.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname;
   if(process.argv[2]!=='deployed'&&path==='/robot')return route.fulfill({path:'app/static/robot.html',contentType:'text/html'});
   if(process.argv[2]!=='deployed'&&path.startsWith('/static/')&&fs.existsSync('app'+path))return route.fulfill({path:'app'+path,contentType:path.endsWith('.js')?'text/javascript':path.endsWith('.css')?'text/css':undefined});
   if(path.startsWith('/api/robot/')){
    if(path.includes('/approve')||path.includes('/command')||path.includes('/operation')||/\/moves\//.test(path)){commands.push(path);return route.abort();}
    if(path==='/api/robot/pick/scenario/preview'&&delayPreview)await new Promise(r=>setTimeout(r,400));
    const response=await route.fetch({url:base+path+url.search});
    if(path==='/api/robot/pick/scenario/preview'&&response.ok())lastPreview=await response.json();
    return route.fulfill({response});
   }
   return route.continue();
  });
  await p.goto('http://localhost:8080/robot#work-points-title');
  const sc=p.locator('#scenario-editor'),canvas=p.locator('[data-arm=canvas]'),tab=name=>sc.getByRole('tab',{name,exact:true});
  await p.waitForFunction(()=>document.querySelector('#scenario-editor [data-sc=saved]')?.textContent==='저장됨');
  assert.equal(await sc.locator('.scenario-flow button').count(),5);assert.equal(await sc.locator('[data-stage]').count(),17);
  await sc.screenshot({path:'/tmp/scenario-settings-review.png'});
  const box=sc.locator('[aria-labelledby=work-point-box]');const z=box.locator('.pose-fields input').nth(2);
  await z.fill('130');await p.waitForFunction(()=>document.querySelector('[data-sc=above-box]')?.textContent.includes('230'));
  await p.waitForTimeout(400);assert.equal(lastPreview.steps[10].target[2],130);assert.equal(lastPreview.steps[9].target[2],230);
  assert.match(await sc.locator('[data-sc=draft-summary]').textContent(),/저장 전/);
  await box.locator('[data-reset]').click();await p.waitForTimeout(400);assert.equal(lastPreview.steps[10].target[2],120);
  await tab('이동 방식').click();assert.equal(await sc.locator('[data-config="stages.5.motion"]').isDisabled(),true);assert.equal(await sc.locator('[data-config="stages.5.motion"]').inputValue(),'movel');assert.equal(await sc.locator('[data-config="stages.9.motion"]').inputValue(),'movej');
  await sc.locator('[data-config="stages.3.motion"]').selectOption('movel');
  await sc.locator('[data-sc=save]').click();await p.waitForFunction(()=>document.querySelector('[data-sc=message]')?.textContent.includes('저장 완료'));
  await p.reload();await p.waitForFunction(()=>document.querySelector('#scenario-editor [data-sc=saved]')?.textContent==='저장됨');await tab('이동 방식').click();assert.equal(await sc.locator('[data-config="stages.3.motion"]').inputValue(),'movel');
  await sc.locator('[data-config="stages.3.motion"]').selectOption('movec');await p.waitForFunction(()=>document.querySelector('[data-sc=simulation-state]')?.textContent.includes('3단계 movec'));
  assert.equal(await sc.locator('[data-sc=play]').isDisabled(),true);await sc.locator('[data-config="stages.3.motion"]').selectOption('movej');
  await sc.locator('[data-config="stages.8.motion"]').selectOption('movec');const arcVia=[-421.975537,.000449,290.654122,44.999991,179.999802,0];
  for(let i=0;i<6;i++)await sc.locator('[data-config="stages.8.via_pose.'+i+'"]').fill(String(arcVia[i]));
  await sc.screenshot({path:'/tmp/scenario-motions-ui-review.png'});
  await tab('속도 설정').click();await sc.locator('[data-preset=medium] summary').click();
  await sc.locator('[data-config="profiles.medium.line.speed"]').fill('7');
  await sc.locator('[data-override="5"]').check();assert.equal(await sc.locator('[data-override-fields="5"]').isVisible(),true);
  await sc.locator('[data-config="stages.5.override.speed"]').fill('4');
  await sc.locator('[data-sc=save]').click();await p.waitForFunction(()=>document.querySelector('[data-sc=message]')?.textContent.includes('저장 완료'));
  await tab('시뮬레이션').click();await sc.locator('[data-sc=rate]').selectOption('20');
  await p.waitForFunction(()=>!document.querySelector('[data-sc=play]')?.disabled);
  await sc.locator('[data-sc=play]').click();await p.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='running');
  await p.locator('[data-arm=simulationPause]').click();await p.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='paused');
  const joints=await canvas.getAttribute('data-joints');await p.waitForTimeout(300);assert.equal(await canvas.getAttribute('data-joints'),joints);
  await p.locator('[data-arm=simulationPause]').click();
  await p.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='awaiting_grip',null,{timeout:30000});
  assert.equal(JSON.parse(await canvas.getAttribute('data-virtual-product')).holding,true);
  assert.match(await p.locator('[data-arm=simulationMetrics]').textContent(),/가상 벌림 40.0 mm · 상품 파지 중/);
  assert.equal(await p.locator('[data-arm=handPosition]').textContent(),'—');
  assert.equal(await sc.locator('[data-stage="6"]').getAttribute('data-current'),'true');
  await p.locator('.gripper-panel').screenshot({path:'/tmp/scenario-floor-ui-review.png'});
  await sc.locator('[data-sc=confirm]').click();await p.waitForFunction(()=>document.querySelector('[data-stage="8"]')?.dataset.current==='true',null,{timeout:30000});
  assert.match(await sc.locator('[data-stage="8"] small').textContent(),/movec/);await p.locator('[data-arm=simulationPause]').click();await p.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='paused');
  await p.locator('.gripper-panel').screenshot({path:'/tmp/scenario-movec-path-review.png'});await p.locator('[data-arm=simulationPause]').click();
  await p.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='awaiting_open',null,{timeout:30000});
  assert.equal(JSON.parse(await canvas.getAttribute('data-virtual-product')).holding,true);
  await sc.locator('[data-sc=confirm]').click();await p.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='complete',null,{timeout:30000});
  assert.equal(JSON.parse(await canvas.getAttribute('data-virtual-product')).released,true);assert.equal(await canvas.getAttribute('data-joints'),'0,0,90,0,90,0');assert.equal(Number(await canvas.getAttribute('data-simulated-opening')),80);
  // Step inspection must restore the manual gates even when seeking backwards.
  for(const [number,state]of [[6,'awaiting_grip'],[11,'awaiting_open'],[12,'paused'],[17,'complete']]){
   await sc.locator('[data-stage="'+number+'"]').click();await sc.locator('[data-sc=jump]').click();
   await p.waitForFunction(state=>document.querySelector('[data-arm=canvas]')?.dataset.simulation===state,state);assert.equal(await sc.locator('[data-sc=step-number]').textContent(),number+' / 17');
   if(number===12){assert.equal(JSON.parse(await canvas.getAttribute('data-virtual-product')).released,true);assert.ok(Math.abs(Number(await canvas.getAttribute('data-simulated-opening'))-50)<1e-6);}
  }
  await sc.locator('[data-sc=stop]').click();await tab('이동 방식').click();await sc.locator('[data-config="stages.8.motion"]').selectOption('movej');await tab('상품·파지 영역').click();await sc.locator('[data-sc=region]').selectOption('shelf');
  delayPreview=true;await sc.locator('[data-config="virtual.shelf.size_mm.0"]').fill('45');
  await tab('시뮬레이션').click();assert.equal(await sc.locator('[data-sc=play]').isDisabled(),true);delayPreview=false;
  await p.waitForFunction(()=>!document.querySelector('[data-sc=play]')?.disabled);
  await sc.locator('[data-sc=play]').click();await p.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='awaiting_grip',null,{timeout:30000});
  assert.equal(JSON.parse(await canvas.getAttribute('data-virtual-product')).holding,true);
  await sc.locator('[data-sc=confirm]').click();await p.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='awaiting_open',null,{timeout:30000});
  await sc.locator('[data-sc=confirm]').click();await p.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='complete',null,{timeout:30000});
  assert.equal(await canvas.getAttribute('data-joints'),'0,0,90,0,90,0');assert.equal(Number(await canvas.getAttribute('data-simulated-opening')),80);
  await p.setViewportSize({width:390,height:844});await sc.screenshot({path:'/tmp/scenario-mobile-ui-review.png'});
  await tab('이동 방식').click();await sc.screenshot({path:'/tmp/scenario-inline-joints-mobile.png'});assert.equal(await sc.locator('[data-motion-step="1"] .scenario-section-head input').count(),6);
  await tab('시뮬레이션').click();
  assert.equal(await p.locator('.plane-editor').evaluate(n=>n.scrollWidth>n.clientWidth+1),false);
  await sc.locator('[data-sc=stop]').click();await sc.locator('[data-sc=cancel]').click();
  await p.getByRole('tab',{name:'택배 박스 위치 설정',exact:true}).click();await p.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.virtualProduct==='');
  assert.deepEqual(commands,[]);assert.deepEqual(errors,[]);
  console.log('PASS: five phases / 17 step cards; movej/movel/movec selection and persistence; fixed constraints; missing arc blocked; arc through-point inputs and curved playback; box edit invalidation; profiles/units; floor and shelf full playback; pause/seek/gates; product and opening restoration; mobile; zero hardware calls/page errors.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
