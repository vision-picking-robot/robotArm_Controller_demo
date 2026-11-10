const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
process.chdir(path.resolve(__dirname,'..'));
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader','--disable-background-networking']});
 try{
  const ctx=await browser.newContext({viewport:{width:1360,height:1000}}),page=await ctx.newPage();page.setDefaultTimeout(15000);
  const base=process.env.SCENARIO_QA_BASE||'http://127.0.0.1:8012',url=new URL(base),writes=[],errors=[];let latest=null,activeMove=null;
  assert.ok(['localhost','127.0.0.1'].includes(url.hostname)&&!['8000','8011','8080'].includes(url.port));
  const get=async resource=>(await ctx.request.get(base+'/api/robot/'+resource)).json();
  const status=await get('status');assert.equal(status.config.access_backend,'ros');assert.equal(status.config.ros_pc_ip,'');
  const original={config:await get('pick-scenario'),points:status.work_points,box:await get('parcel-box'),floor:await get('floor-reference'),table:await get('gripper/table')};
  assert.equal(original.config.waypoint1_joints,null,'Use a fresh QA state directory');assert.equal(original.points.box.pose,null,'Use a fresh QA state directory');
  page.on('pageerror',e=>errors.push(e.message));
  await ctx.routeWebSocket('**/ws/detections*',s=>{const t=setInterval(()=>s.send(JSON.stringify({event:'status',ready:false,state:'camera_off'})),500);t.unref();s.onClose(()=>clearInterval(t));});
  await ctx.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url()),resource=url.pathname;
   if(process.argv[2]!=='deployed'&&resource==='/robot')return route.fulfill({path:'app/static/robot.html',contentType:'text/html'});
   if(process.argv[2]!=='deployed'&&resource.startsWith('/static/')&&fs.existsSync('app'+resource))return route.fulfill({path:'app'+resource,contentType:resource.endsWith('.js')?'text/javascript':resource.endsWith('.css')?'text/css':undefined});
   if(resource.startsWith('/api/robot/')){
    if(resource==='/api/robot/pick/scenario/status'&&activeMove)return route.fulfill({json:{prepared:false,move:activeMove}});
    if(request.method()!=='GET'&&!(resource==='/api/robot/pick/scenario/preview'&&request.method()==='POST')){writes.push(resource);return route.abort();}
    const response=await route.fetch({url:base+resource+url.search});if(resource==='/api/robot/pick/scenario/preview'&&response.ok())latest=await response.json();return route.fulfill({response});
   }
   return route.continue();
  });
  await page.goto('http://localhost:8080/robot#work-points-title');
  const editor=page.locator('#scenario-editor'),canvas=page.locator('[data-arm=canvas]'),tab=name=>editor.getByRole('tab',{name,exact:true});
  await page.waitForFunction(()=>document.querySelector('[data-sc=source]')?.value==='example'&&!document.querySelector('[data-sc=play]')?.disabled);
  assert.equal(await tab('시뮬레이션').getAttribute('aria-selected'),'true');assert.equal(await editor.locator('[data-motion-step="8"] [data-config="waypoint1_joints.0"]').inputValue(),'-180');
  assert.equal(await canvas.getAttribute('data-example'),'true');assert.equal(Number(await canvas.getAttribute('data-simulated-opening')),80);
  assert.equal(await editor.locator('[data-sc=save]').isDisabled(),true);assert.equal(await editor.locator('[data-sc=prepare]').isDisabled(),true);
  await editor.screenshot({path:'/tmp/scenario-example-ready.png'});
  await editor.locator('[data-sc=rate]').selectOption('20');
  for(const region of ['floor','shelf']){
   if(region==='shelf'){await editor.locator('[data-sc=stop]').click();await tab('상품·파지 영역').click();await editor.locator('[data-sc=region]').selectOption(region);assert.equal(await editor.locator('[data-config="virtual.shelf.center_base_mm.2"]').inputValue(),'450');await tab('시뮬레이션').click();}
   await page.waitForFunction(()=>!document.querySelector('[data-sc=play]')?.disabled);await editor.locator('[data-sc=play]').click();
   await page.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='awaiting_grip',null,{timeout:30000});assert.equal(JSON.parse(await canvas.getAttribute('data-virtual-product')).holding,true);
   if(region==='floor')await page.locator('.gripper-panel').screenshot({path:'/tmp/scenario-example-floor.png'});
   await editor.locator('[data-sc=confirm]').click();await page.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='awaiting_open',null,{timeout:30000});
   await editor.locator('[data-sc=confirm]').click();await page.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.simulation==='complete',null,{timeout:30000});
   assert.equal(await canvas.getAttribute('data-joints'),'0,0,90,0,90,0');assert.equal(Number(await canvas.getAttribute('data-simulated-opening')),80);assert.equal(JSON.parse(await canvas.getAttribute('data-virtual-product')).released,true);
  }
  await editor.locator('[data-sc=stop]').click();await tab('이동 방식').click();assert.equal(await tab('대기·경유·박스').count(),0);
  assert.equal(await editor.locator('[data-motion-step="1"] .scenario-section-head input').count(),6);
  assert.equal(await editor.locator('[data-motion-step="8"] .scenario-section-head input').count(),6);
  assert.equal(await editor.locator('[data-motion-step="9"] .scenario-section-head input').count(),6);
  await editor.locator('[data-motion-step="8"] [data-config="waypoint1_joints.0"]').fill('-170');await editor.locator('[data-example-box-axis="0"]').fill('470');assert.equal(await editor.locator('[data-motion-step="15"] [data-config="waypoint1_joints.0"]').inputValue(),'-170');
  assert.equal(await editor.locator('[data-motion-step="9"] [data-computed-joint="0"]').inputValue(),'10');
  await editor.locator('[data-motion-step="16"] [data-config="standby_joints.0"]').fill('12');
  assert.equal(await editor.locator('[data-motion-step="1"] [data-config="standby_joints.0"]').inputValue(),'12');

  await page.waitForTimeout(400);assert.equal(latest.steps[10].target[0],470);assert.equal(await editor.locator('[data-sc=save]').isDisabled(),true);
  await editor.locator('[data-sc=source]').selectOption('saved');await page.waitForFunction(()=>document.querySelector('[data-sc=source]')?.value==='saved');
  assert.equal(await editor.locator('[data-motion-step="8"] [data-config="waypoint1_joints.0"]').inputValue(),'');assert.equal(await editor.locator('[aria-labelledby=work-point-box]').isVisible(),true);
  await editor.locator('[data-sc=source]').selectOption('example');await page.waitForFunction(()=>document.querySelector('[data-config="waypoint1_joints.0"]')?.value==='-170');assert.equal(await editor.locator('[data-example-box-axis="0"]').inputValue(),'470');
  await editor.locator('[data-sc=cancel]').click();assert.equal(await editor.locator('[data-motion-step="8"] [data-config="waypoint1_joints.0"]').inputValue(),'-180');assert.equal(await editor.locator('[data-example-box-axis="0"]').inputValue(),'450');
  await editor.locator('[data-sc=source]').selectOption('saved');await editor.locator('[data-motion-step="1"] [data-config="standby_joints.0"]').fill('15');await editor.locator('[data-sc=source]').selectOption('example');await editor.locator('[data-sc=reload]').click();await editor.locator('[data-sc=source]').selectOption('saved');assert.equal(await editor.locator('[data-motion-step="1"] [data-config="standby_joints.0"]').inputValue(),'15');
  await page.reload();await page.waitForFunction(()=>document.querySelector('[data-sc=source]')?.value==='example'&&!document.querySelector('[data-sc=play]')?.disabled);
  activeMove={approval_id:'fake-job',scenario:true,mode:'transfer',step:6,state:'awaiting_grip',message:'synthetic active job'};
  await page.waitForFunction(()=>document.querySelector('[data-sc=source]')?.value==='saved');assert.equal(await editor.locator('[data-sc=play]').isDisabled(),true);await page.waitForFunction(()=>document.querySelector('[data-arm=canvas]')?.dataset.example==='false');
  activeMove=null;await page.waitForFunction(()=>!document.querySelector('[data-sc=source]')?.disabled);await editor.locator('[data-sc=source]').selectOption('example');await page.waitForFunction(()=>!document.querySelector('[data-sc=play]')?.disabled);
  await page.setViewportSize({width:390,height:844});await editor.screenshot({path:'/tmp/scenario-example-mobile.png'});assert.equal(await page.locator('.plane-editor').evaluate(n=>n.scrollWidth>n.clientWidth+1),false);
  assert.deepEqual(await get('pick-scenario'),original.config);assert.deepEqual((await get('status')).work_points,original.points);assert.deepEqual(await get('parcel-box'),original.box);assert.deepEqual(await get('floor-reference'),original.floor);assert.deepEqual(await get('gripper/table'),original.table);
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
  console.log('PASS: fresh empty state auto-loads complete example; floor/shelf finish all 17 steps; edits and source switching preserve own settings; reload/reset/mobile; zero writes/approvals/hardware calls/page errors.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
