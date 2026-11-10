// Requires an isolated robot-web on port 8012 with a fresh ROBOT_STATE_DIR.
// Only configuration saves and virtual previews are allowed; device commands are blocked.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader','--disable-background-networking']});
 try{
  const ctx=await browser.newContext({viewport:{width:1360,height:1000}}),page=await ctx.newPage(),errors=[],writes=[];
  const base='http://127.0.0.1:8012';let latest=null;
  const get=async resource=>(await ctx.request.get(base+'/api/robot/'+resource)).json();
  const status=await get('status');assert.equal(status.config.access_backend,'ros');assert.equal(status.config.ros_pc_ip,'');
  const config=await get('pick-scenario');assert.equal(config.waypoint1_joints,null,'Use an isolated QA state directory');
  for(const stage of Object.values(config.stages))stage.override=null;
  Object.assign(config.stages['8'],{profile:'medium',motion:null});
  assert.ok((await ctx.request.put(base+'/api/robot/pick-scenario',{data:config})).ok());
  page.on('pageerror',error=>errors.push(error.message));
  await ctx.routeWebSocket('**/ws/detections*',socket=>socket.close());
  await ctx.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());assert.equal(url.origin,base);
   if(url.pathname.startsWith('/api/robot/')&&request.method()!=='GET'){
    writes.push(url.pathname);
    assert.ok((url.pathname==='/api/robot/pick-scenario'&&request.method()==='PUT')||(url.pathname==='/api/robot/pick/scenario/preview'&&request.method()==='POST'),'Device command attempted: '+url.pathname);
   }
   const response=await route.fetch({headers:{...request.headers(),origin:'http://localhost:8080'}});
   if(url.pathname==='/api/robot/pick/scenario/preview'&&response.ok())latest=await response.json();
   return route.fulfill({response});
  });
  await page.goto(base+'/robot#work-points-title');
  const editor=page.locator('#scenario-editor'),speed=n=>editor.locator('[data-speed-percent="'+n+'"]');
  await page.waitForFunction(()=>!document.querySelector('[data-sc=play]')?.disabled);
  assert.equal(await editor.locator('[role=tablist], [role=tab], [role=tabpanel]').count(),0);
  assert.equal(await editor.locator('#scenario-simulation').isVisible(),true);
  assert.deepEqual(await editor.locator('thead th').allTextContents(),['단계','이동 방식','이동 속도','입력 방식','J1','J2','J3','J4','J5','J6']);
  assert.equal(await editor.locator('[data-speed-percent]').count(),16);assert.equal(await speed(2).count(),0);
  for(const n of [1,5,6])assert.deepEqual(await speed(n).locator('option:not([disabled])').allTextContents(),Array.from({length:10},(_,i)=>(i+1)*10+'%'));
  for(const [number,percent]of [[1,30],[5,40],[6,70]]){
   await speed(number).selectOption(String(percent));
   await page.waitForFunction(([n,p])=>document.querySelector('[data-speed-percent="'+n+'"]').title.startsWith(p/10+' '),[number,percent]);
   await page.waitForTimeout(350);assert.equal(latest.steps[number-1].speed.speed,percent/10);
  }
  for(let percent=10;percent<=100;percent+=10){await speed(1).selectOption(String(percent));assert.equal(await speed(1).inputValue(),String(percent));}
  await editor.locator('[data-sc=source]').selectOption('saved');
  await page.waitForFunction(()=>document.querySelector('[data-speed-percent="8"]')?.value==='75');
  assert.equal(await speed(8).locator('option:checked').isDisabled(),true,'Legacy speed is preserved until explicitly edited');
  assert.equal(await speed(8).locator('option:not([disabled])').count(),10);
  await speed(8).selectOption('30');
  await editor.locator('[data-config="stages.8.motion"]').selectOption('movel');
  assert.equal(await speed(8).inputValue(),'30','Motion changes preserve percentage');
  await speed(5).selectOption('60');await speed(6).selectOption('80');
  await editor.locator('[data-sc=save]').click();
  await page.waitForFunction(()=>document.querySelector('[data-sc=message]')?.textContent.includes('저장 완료'));
  const saved=await get('pick-scenario');
  assert.equal(saved.stages['8'].override.speed,3);assert.equal(saved.stages['8'].override.acceleration,7.5);
  assert.equal(saved.stages['5'].override.speed,6);assert.equal(saved.stages['6'].override.speed,8);
  await page.reload();await editor.locator('[data-sc=source]').selectOption('saved');
  await page.waitForFunction(()=>document.querySelector('[data-speed-percent="8"]')?.value==='30');
  assert.equal(await speed(5).inputValue(),'60');assert.equal(await speed(6).inputValue(),'80');
  const products=page.locator('#manipulator-products'),productTab=page.locator('#manipulator-products-tab');
  assert.deepEqual(await page.locator('.plane-tabs [data-manipulator-tab]').allTextContents(),['로봇 제어','경유 지점 설정','택배 박스 위치 설정','상품·파지 영역','자세 테스트']);
  await productTab.click();assert.ok(page.url().endsWith('#product-grasp-title'));
  assert.equal(await products.isVisible(),true);assert.equal(await editor.isVisible(),false);
  assert.equal(await products.locator('[data-product-control=source]').inputValue(),'saved');
  const distance=products.locator('[data-config="approaches.floor.distance_mm"]');
  await distance.fill('0');await products.getByRole('button',{name:'시나리오 저장',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-product-mount] [role=status]').textContent.includes('허용 범위'));
  assert.equal(await products.isVisible(),true,'Validation uses the upper tab without nested tab navigation');
  await distance.fill('155');
  await page.locator('#manipulator-points-tab').click();await productTab.click();
  assert.equal(await distance.inputValue(),'155','Tab changes retain the shared draft');
  await products.getByRole('button',{name:'시나리오 저장',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-product-mount] [role=status]').textContent.includes('저장 완료'));
  assert.equal((await get('pick-scenario')).approaches.floor.distance_mm,155);
  await distance.fill('165');await products.getByRole('button',{name:'변경 취소',exact:true}).click();
  assert.equal(await distance.inputValue(),'155');
  await productTab.press('ArrowRight');assert.equal(await page.locator('#manipulator-poses-tab').getAttribute('aria-selected'),'true');
  await page.goto(base+'/robot#product-grasp-title');
  await page.waitForFunction(()=>document.querySelector('[data-product-control=source]')?.disabled===false);
  await products.locator('[data-product-control=source]').selectOption('saved');
  assert.equal(await distance.inputValue(),'155','Direct links load the new top-level tab');
  await page.evaluate(()=>{location.hash='#unknown';});
  await page.waitForFunction(()=>document.querySelector('#manipulator-products').hidden);
  await page.evaluate(()=>{location.hash='#product-grasp-title';});
  await page.waitForFunction(()=>document.querySelector('#manipulator-products [data-config="approaches.floor.distance_mm"]')?.value==='155');
  await distance.fill('175');await products.getByRole('button',{name:'변경 취소',exact:true}).click();assert.equal(await distance.inputValue(),'155');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('.plane-editor').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
  assert.equal(await editor.locator('thead th').count(),10);
  assert.deepEqual(errors,[]);assert.ok(writes.length>0);
  console.log('PASS: percentage speed controls; product tab order, shared edits/save/cancel, keyboard, direct links and workspace reconnection; mobile; no hardware commands.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
