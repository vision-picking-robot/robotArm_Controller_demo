// Isolated browser check: every network request is intercepted; no device API is contacted.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
process.chdir(path.resolve(__dirname,'..'));
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader','--disable-background-networking']});
 try {
  const page=await browser.newPage({viewport:{width:1200,height:950}}),commands=[],errors=[];
  const status={config:{access_backend:'mac-sdk',ros_pc_ip:'127.0.0.1'},topics:[{name:'joint_states',state:'waiting',hz:null,message:null}]};
  const host={session_id:'fake',motion_token:'a'.repeat(32),can_move:true,gripper_supported:true,gripper_target:262,
   gripper_feedback:{state:'completed',position:262,observed_at:new Date().toISOString()}};
  let table={revision:'saved',points:[{position:0,width_mm:80},{position:700,width_mm:0}]};
  let contact={revision:null,threshold_ma:null};const contactWrites=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.routeWebSocket('**/ws/detections*',socket=>socket.send(JSON.stringify({event:'status',ready:false,state:'camera_off'})));
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url()),resource=url.pathname;
   if(resource==='/robot')return route.fulfill({path:'app/static/robot.html',contentType:'text/html'});
   if(resource.startsWith('/static/')&&fs.existsSync('app'+resource))return route.fulfill({path:'app'+resource,contentType:resource.endsWith('.js')?'text/javascript':resource.endsWith('.css')?'text/css':'image/svg+xml'});
   let value={};const body=req.postDataJSON();
   if(resource==='/api/robot/status')value=status;
   else if(resource==='/api/robot/host-access/status')value=host;
   else if(resource.endsWith('/parcel-box'))value={revision:null};
   else if(resource.endsWith('/floor-reference'))value={revision:null,z_mm:0};
   else if(resource.endsWith('/contact')){
    if(req.method()==='PUT'){contactWrites.push(body);contact={revision:'contact-saved',threshold_ma:body.threshold_ma};}
    value=contact;
   }
   else if(resource.endsWith('/table')){
    if(req.method()==='PUT')table={revision:'updated',points:body.points};
    value=table;
   }
   else if(resource.endsWith('/test/preview')) {
    if(body.width_mm>table.points[0].width_mm)return route.fulfill({status:409,json:{detail:'필요 벌림이 교시 범위 밖입니다.'}});
    value={...body,position:267,estimated_width_mm:50.104};
   }else if(resource.endsWith('/test')||resource.endsWith('/command')) {commands.push(body);value={state:'completed',message:'모의 장치 완료',feedback:{position:262}};}
   else throw Error('Unexpected request: '+resource);
   return route.fulfill({json:value});
  });
  await page.goto('http://gripper.test/robot#gripper');
  const tabs=page.getByRole('tablist',{name:'그리퍼',exact:true});
  const calibration=tabs.getByRole('tab',{name:'캘리브레이션'}),test=tabs.getByRole('tab',{name:'벌림 테스트'});
  await calibration.waitFor();
  assert.deepEqual(await tabs.getByRole('tab').allTextContents(),['캘리브레이션','벌림 테스트']);
  assert.equal(await page.locator('.plane-summary').isVisible(),false);
  assert.equal(await page.locator('.pick-footer').isVisible(),false);
  assert.equal(await page.locator('[data-pick=modelTitle]').textContent(),'그리퍼 방향');
  await page.getByRole('button',{name:'측면',exact:true}).click();
  assert.equal(await page.locator('[data-depth-view=side]').getAttribute('aria-pressed'),'true');
  await page.locator('[data-page=work-plane]').click();
  await page.getByRole('tab',{name:'예시 객체 설정'}).click();
  assert.equal(await page.locator('#plane-example-settings').isVisible(),true);
  await page.getByRole('tab',{name:'그리퍼 설정'}).click();
  assert.equal(await page.locator('[data-pick=anglePanel]').isVisible(),true);
  await page.goto('http://gripper.test/robot#gripper-table');
  await page.locator('#hand-samples input').first().waitFor();
  assert.equal(await calibration.getAttribute('aria-selected'),'true');
  const initialWidth=page.locator('#hand-samples [data-field=width_mm]').first();
  await initialWidth.fill('81');await page.locator('#hand-save').click();
  await page.waitForFunction(()=>document.querySelector('#hand-table-note').textContent.includes('저장했습니다'));
  assert.equal(table.points[0].width_mm,81);
  await calibration.press('ArrowRight');assert.match(page.url(),/#gripper-test$/);
  await page.reload();await test.waitFor();
  assert.equal(await test.getAttribute('aria-selected'),'true');
  await page.locator('#hand-test-width').fill('50');
  assert.equal(commands.length,0);
  await page.locator('#hand-test-preview').click();
  await page.locator('#hand-test-dialog').waitFor({state:'visible'});
  assert.match(await page.locator('#hand-test-target').textContent(),/267/);
  await page.locator('#hand-test-cancel').click();assert.equal(commands.length,0);
  await page.locator('#hand-test-preview').click();
  await page.locator('#hand-test-confirm').dblclick();
  await page.waitForFunction(()=>document.querySelector('#hand-test-result').textContent.includes('모의 장치 완료'));
  assert.equal(commands.length,1);assert.equal(commands[0].width_mm,50);assert.equal(commands[0].revision,'updated');
  assert.equal('position' in commands[0],false,'Server computes position from saved table');
  await page.waitForFunction(()=>document.querySelector('#hand-test-feedback').textContent.includes('50.7 mm'));
  await page.locator('#hand-test-width').fill('90');await page.locator('#hand-test-preview').click();
  await page.waitForFunction(()=>document.querySelector('#hand-test-result').textContent.includes('교시 범위'));
  assert.equal(await page.locator('#hand-test-dialog').isVisible(),false);
  await page.locator('#hand-test-width').fill('50');
  await calibration.click();
  await page.locator('#hand-samples input').first().fill('1');
  await test.click();
  assert.equal(await page.locator('#hand-test-preview').isDisabled(),true);
  await calibration.click();
  await page.locator('#hand-reload').click();
  await test.click();
  status.pick_active=true;
  await page.waitForFunction(()=>document.querySelector('#hand-test-read').disabled);
  await page.setViewportSize({width:390,height:844});
  for(const tab of [calibration,test]){
   await tab.click();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  }
  status.pick_active=false;table={revision:null,points:[]};
  await page.goto('http://gripper.test/robot#gripper-table');
  await page.reload();
  await page.locator('#hand-samples input').first().waitFor();
  assert.equal(await page.locator('.hand-sample').count(),2);
  assert.equal(await page.locator('#hand-save').isDisabled(),true);
  assert.match(await page.locator('#hand-table-validation').textContent(),/1번.*실제 위치/);
  await page.waitForFunction(()=>!document.querySelector('#hand-capture').disabled);
  await page.locator('#hand-capture').click();
  assert.equal(await page.locator('.hand-sample').count(),2);
  assert.equal(await page.locator('.hand-sample [data-field=position]').first().inputValue(),'262');
  const sample=number=>page.locator('.hand-sample').nth(number);
  await sample(0).locator('[data-field=position]').fill('0');
  await sample(0).locator('[data-field=width_mm]').fill('80');
  assert.equal(await page.locator('#hand-save').isDisabled(),true);
  await sample(1).locator('[data-field=position]').fill('700');
  await sample(1).locator('[data-field=width_mm]').fill('0');
  assert.equal(await page.locator('#hand-save').isEnabled(),true);
  await sample(1).locator('[data-field=position]').fill('0');
  assert.match(await page.locator('#hand-table-validation').textContent(),/중복/);
  await sample(1).locator('[data-field=position]').fill('700');
  await sample(1).locator('[data-field=width_mm]').fill('90');
  assert.match(await page.locator('#hand-table-validation').textContent(),/작아져야/);
  await sample(1).locator('[data-field=width_mm]').fill('0');
  await page.locator('#hand-save').click();
  await page.waitForFunction(()=>document.querySelector('#hand-table-note').textContent.includes('저장했습니다'));
  assert.deepEqual(table.points,[{position:0,width_mm:80},{position:700,width_mm:0}]);
  assert.equal(await page.locator('#hand-contact-current').inputValue(),'30');
  assert.equal(contactWrites.length,0);
  await page.locator('#hand-contact-save').click();
  await page.waitForFunction(()=>document.querySelector('#hand-contact-result').textContent.includes('저장 완료'));
  assert.equal(contactWrites[0].threshold_ma,30);
  contact={revision:'existing-contact',threshold_ma:45};
  await page.locator('#hand-contact-reload').click();
  await page.waitForFunction(()=>document.querySelector('#hand-contact-current').value==='45');
  assert.equal(commands.length,1);assert.deepEqual(errors,[]);
  console.log('PASS: preview/cancel 0 commands; approved double click 1; bounds, dirty table, lock, feedback and mobile checks');
 }finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
