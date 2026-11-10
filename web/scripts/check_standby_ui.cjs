// All requests are mocked; this check never contacts a device API.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking']});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:900}}),commands=[],errors=[];
  let host={session_id:'mock-session',robot_state:'SAFE_STOP',servo:{token:'a'.repeat(32),result:'idle'}},polls=0;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url()),request=route.request();
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><html><head><link rel="stylesheet" href="/static/station-shell.css"></head><body data-workspace="robot"><aside id="station-rail" class="station-rail"></aside><script type="module" src="/static/station-nav.js"></script></body></html>'});
   if(url.pathname.startsWith('/static/'))return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':url.pathname.endsWith('.css')?'text/css':'image/svg+xml',body:fs.readFileSync(path.join(__dirname,'../app',url.pathname))});
   if(url.pathname==='/api/robot/host-access/servo-on'){
    commands.push(request.postDataJSON());host={...host,servo:{token:null,result:'pending'}};polls=0;
    return route.fulfill({status:202,json:host});
   }
   if(url.pathname==='/api/robot/host-access/status'){
    if(host.servo.result==='pending'&&++polls>=2)host={...host,robot_state:'STANDBY',servo:{token:null,result:'confirmed'}};
    return route.fulfill({json:host});
   }
   if(url.pathname==='/api/robot/stop')return route.fulfill({json:{message:'중단 완료'}});
   throw Error('Unexpected request: '+url);
  });
  await page.goto('http://standby.test/');
  const button=page.locator('#robot-standby'),dialog=page.locator('#robot-standby-dialog'),state=page.locator('.rail-standby-state');
  await button.click();await dialog.waitFor({state:'visible'});
  assert.equal(commands.length,0,'Opening confirmation must not actuate');
  await dialog.getByRole('button',{name:'취소',exact:true}).click();
  assert.equal(commands.length,0);
  await button.click();await page.locator('#robot-standby-confirm').click();
  assert.equal(await button.isDisabled(),true);
  await page.waitForFunction(()=>document.querySelector('.rail-standby-state').textContent.includes('복구 완료'));
  assert.deepEqual(commands,[{session_id:'mock-session',servo_token:'a'.repeat(32)}]);
  await button.click();await page.waitForFunction(()=>document.querySelector('.rail-standby-state').textContent.includes('현재 STANDBY'));assert.equal(commands.length,1);
  host={...host,robot_state:'EMERGENCY_STOP',servo:{token:null,result:'idle',message:'비상 정지 해제 필요'}};
  await button.click();await page.waitForFunction(()=>document.querySelector('.rail-standby-state').textContent.includes('비상 정지'));assert.equal(await dialog.isVisible(),false);
  await page.setViewportSize({width:390,height:844});
  const bounds=await button.boundingBox();assert.ok(bounds.x>=0&&bounds.y>=0&&bounds.x+bounds.width<=390&&bounds.y+bounds.height<=844);
  await page.locator('#robot-global-stop').click();await page.waitForFunction(()=>document.querySelector('.rail-stop-state').textContent==='중단 완료');
  assert.deepEqual(errors,[]);console.log('STANDBY recovery confirmation, polling, emergency rejection and mobile controls passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
