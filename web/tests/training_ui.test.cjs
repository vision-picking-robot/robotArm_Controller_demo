// Exercise async button intent without a camera, network, DOM library or build step.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness(gatePath, failStop=false, datasets=[], failOnce=null, initialHash='') {
  const elements = new Map(), calls = [], intervals = [];
  let release, cameraOn = false, collecting = false;
  const gate = new Promise(resolve => release = resolve);
  class Element {
    constructor() {this.value='';this.children=[];this.dataset={};this.options=[];this.handlers={};this.classList={toggle(){}};this.style={};}
    setAttribute(name, value) {this[name]=value;}
    addEventListener(name, handler) {this.handlers[name] = handler;}
    showModal() {this.open=true;}
    close() {this.open=false;}
    replaceChildren(...items) {this.children=[];this.options=[];this.value='';this.append(...items);}
    append(...items) {this.children.push(...items);for(const item of items){if(item instanceof Option){this.options.push(item);if(this.options.length===1)this.value=item.value;}}}
  }
  class Option {constructor(text,value){this.text=text;this.value=value;}}
  const el = id => {if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const tabs = ['live','collect','jobs','models'].map(name=>{const link=new Element();link.dataset.tab=name;return link;});
  const listeners = {};
  let hash = initialHash;
  const location = {
    protocol: 'http:', host: 'localhost',
    get hash() { return hash; },
    set hash(value) {
      if (hash === value) return;
      hash = value;
      listeners.hashchange();
    }
  };
  const model = {id:'base',name:'base',classes:[],formats:{mps:{state:'ready'}}};
  async function fetch(url, options={}) {
    calls.push({url,method:options.method||'GET',body:options.body && JSON.parse(options.body)});
    if(url===failOnce){failOnce=null;return {ok:false,json:async()=>({detail:'test read error'})};}
    if(url===gatePath)await gate;
    if(url==='/api/camera/start')cameraOn=true;
    if(url==='/api/camera/stop'){cameraOn=false;collecting=false;}
    if(url==='/api/collections/c1/start')collecting=true;
    if(url==='/api/collections/c1/stop'){
      if(failStop)return {ok:false,json:async()=>({detail:'stop failed'})};
      collecting=false;
    }
    let data={};
    if(url==='/api/classes')data=[{id:'class1',name:'class'}];
    if(url==='/api/capture-groups')data=[{id:'group1',name:'group'}];
    if(url==='/api/collections')data=options.method==='POST'?{id:'c1'}:[];
    if(url==='/api/datasets')data=options.method==='POST'?datasets.at(-1):datasets.map(({items,...summary})=>summary);
    if(url.startsWith('/api/datasets/'))data=datasets.find(d=>d.id===url.slice('/api/datasets/'.length));
    if(url==='/api/training/jobs')data=options.method==='POST'?{id:'test-job'}:[];
    if(url==='/api/models')data={backend:'mps',models:[model]};
    if(url==='/api/status')data={ready:true,state:'ready'};
    return {ok:true,json:async()=>data};
  }
  const context=vm.createContext({
    document:{getElementById:el,createElement:()=>new Element(),createTextNode:text=>text,
      querySelectorAll:selector=>selector==='[data-tab]'?tabs:[]},
    window:{addEventListener(name, handler){listeners[name]=handler;},scrollTo(){}},Option,fetch,console,
    location,WebSocket:class {},
    setInterval(fn){intervals.push(fn);},setTimeout:(fn)=>setImmediate(fn),
    currentStatus:{model_version:'base',collection:{id:null}}
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/model-manager.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/dataset-picker.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/training.js'),'utf8'),context);
  const click=id=>el(id).handlers.click();
  return {calls,release,click,el,tabs,location,leave:()=>listeners.pagehide(),heartbeat:()=>intervals.forEach(fn=>fn()),state:()=>({cameraOn,collecting}),async ready(){
    await tick();location.hash='#collect';await tick();
    el('interval').value='250';el('collection-conf').value='.5';
  }};
}

test('training deep links select the intended tab and returning to live detection stops photo collection',async()=>{
  const h=harness(null,false,[],null,'#models');
  assert.equal(h.tabs.find(link=>link.dataset.tab==='models')['aria-current'],'page');
  assert.equal(h.el('models-page').hidden,false);
  await h.ready();
  await h.click('collect-start');
  h.location.hash='#live';
  await tick();
  assert.equal(h.tabs.find(link=>link.dataset.tab==='live')['aria-current'],'page');
  assert.equal(h.el('vision').hidden,false);
  assert.equal(h.state().collecting,false);
  assert.equal(h.state().cameraOn,true);
});

test('leaving the real workspace while collection starts does not keep saving photos',async()=>{
  const startPath='/api/collections/c1/start';
  const h=harness(startPath);
  await h.ready();
  const starting=h.click('collect-start');
  for(let i=0;i<20&&!h.calls.some(c=>c.url===startPath);i++)await tick();
  assert.ok(h.calls.some(c=>c.url===startPath));
  h.leave();
  assert.ok(h.calls.some(c=>c.url==='/api/collections/c1/stop'));
  h.release();
  await starting;
  h.heartbeat();
  assert.equal(h.state().collecting,false);
  assert.ok(!h.calls.some(c=>c.url==='/api/collections/c1/heartbeat'));
});

for(const scenario of ['photo stop while preparing','camera off while preparing','photo stop while start request is pending']) {
  test(scenario,async()=>{
    const gatePath=scenario.endsWith('pending')?'/api/collections/c1/start':'/api/camera/start';
    const h=harness(gatePath);await h.ready();
    const starting=h.click('collect-start');
    for(let i=0;i<20&&!h.calls.some(c=>c.url===gatePath);i++)await tick();
    assert.ok(h.calls.some(c=>c.url===gatePath));
    const stopping=h.click(scenario.startsWith('camera')?'camera-off':'collect-stop');
    await tick();h.release();await Promise.all([starting,stopping]);
    assert.equal(h.state().collecting,false,'A completed stop must not be followed by a late photo start');
    if(scenario.startsWith('camera'))assert.equal(h.state().cameraOn,false,'A late start must not reopen the camera');
    if(!scenario.endsWith('pending'))assert.equal(h.calls.filter(c=>c.url==='/api/collections/c1/start').length,0);
  });
}

test('failed cancellation is never presented as a successful stop',async()=>{
  const h=harness('/api/collections/c1/start',true);await h.ready();
  const starting=h.click('collect-start');
  for(let i=0;i<20&&!h.calls.some(c=>c.url==='/api/collections/c1/start');i++)await tick();
  const stopping=h.click('collect-stop');await tick();h.release();
  await Promise.all([starting,stopping]);
  assert.equal(h.el('action-message').textContent,'stop failed');
});

test('failed stop does not keep renewing the photo lease',async()=>{
  const h=harness(null,true);await h.ready();
  await h.click('collect-start');
  h.heartbeat();await tick();
  const count=()=>h.calls.filter(c=>c.url==='/api/collections/c1/heartbeat').length;
  assert.equal(count(),1);
  await h.click('collect-stop');
  h.heartbeat();await tick();
  assert.equal(count(),1,'A failed stop must allow the server lease to expire');
  assert.equal(h.el('action-message').textContent,'stop failed');
});

// Two classes deliberately share a display name across snapshots but use frozen UUIDs and IDs.
function snapshot(id, specs, status) {
  const items = [];
  const classes = specs.map(([name, train, val], index) => {
    const uuid = 'label-' + index;
    for (const [split, count] of [['train',train], ['val',val]]) {
      for (let n=0; n<count; n++) items.push({class_uuid:uuid, group_id:split, split});
    }
    return {id:index, uuid, name};
  });
  return {id, status, created_at:'2026-11-06T0'+id.length+':00:00Z', classes, items,
    counts:{train:items.filter(i=>i.split==='train').length,val:items.filter(i=>i.split==='val').length}};
}
const two = snapshot('newest',[['시계',18,11],['사이타마',9,12]], 'ready');
const one = snapshot('older',[['시계',18,11]]); // Legacy record before status existed.
const single = snapshot('one',[['시계',18,0]], 'awaiting_validation');
const review = (h,id) => h.el('ds-list').children.find(b=>b.dataset.id===id).handlers.click();
const textCells = e => e.children.map(row=>row.children.map(cell=>cell.textContent));

test('training posts only a confirmed snapshot, replacing the complete class map', async()=>{
  const h=harness(null,false,[single,two,one]);
  await h.ready();
  assert.equal(h.el('train-start').disabled,true);
  await h.click('dataset-open');
  assert.deepEqual(h.el('ds-list').children.map(b=>b.dataset.id),[two.id,one.id,single.id]);
  assert.deepEqual(textCells(h.el('ds-rows')),[['#0','시계','18장','11장'],['#1','사이타마','9장','12장']]);
  assert.equal(h.el('ds-groups').textContent,'2개');
  await h.click('ds-confirm');
  assert.equal(h.el('dataset-selected-count').textContent,'2개');
  await h.click('dataset-open'); await review(h,one.id); await h.click('ds-cancel');
  await h.click('train-start');
  assert.equal(h.calls.find(c=>c.url==='/api/training/jobs' && c.method==='POST').body.dataset_id,two.id);
  await h.click('dataset-open');
  assert.equal(h.el('ds-id').textContent,two.id);
  await review(h,one.id); await h.click('ds-confirm');
  assert.equal(h.el('dataset-selected-classes').children.length,1);
  assert.equal(h.el('dataset-selected-title').textContent,'시계');
  assert.equal(h.calls.filter(c=>c.url==='/api/datasets/'+two.id).length,1);
  await h.click('train-start');
  assert.equal(h.calls.filter(c=>c.url==='/api/training/jobs' && c.method==='POST').at(-1).body.dataset_id,one.id);
});

test('a slow detail response cannot replace a later candidate, and failed reads can retry',async()=>{
  const h=harness('/api/datasets/'+two.id,false,[one,two],'/api/datasets/'+one.id);
  const opening=h.click('dataset-open'); await tick();
  await review(h,one.id);
  assert.equal(h.el('ds-confirm').disabled,true);
  assert.match(h.el('ds-feedback').textContent,/test read error/);
  await h.click('ds-retry');
  h.release();await opening;
  assert.equal(h.el('ds-id').textContent,one.id);
  assert.equal(h.el('ds-confirm').disabled,false);
  h.el('ds-search').value='사이타마';
  await h.el('ds-search').handlers.input();
  assert.deepEqual(h.el('ds-list').children.map(b=>b.dataset.id),[two.id]);
  h.el('ds-search').value='absent';
  await h.el('ds-search').handlers.input();
  assert.match(h.el('ds-feedback').textContent,/일치하는 데이터셋이 없습니다/);
  assert.equal(h.el('ds-confirm').disabled,true);
});

test('just-created snapshot is selected; saved single-environment data can be inspected but cannot start a job',async()=>{
  const h=harness(null,false,[two,single]);
  await h.ready(); await h.click('build-dataset');
  assert.equal(h.el('jobs-page').hidden,false);
  assert.equal(h.el('dataset-selected-count').textContent,'1개');
  assert.match(h.el('dataset-note').textContent,/검증 자료 대기/);
  assert.equal(h.el('train-start').disabled,true);
  await h.click('dataset-open');
  assert.equal(h.el('ds-id').textContent,single.id);
  assert.equal(h.el('ds-warning').hidden,false);
  await h.click('ds-confirm');
  await h.click('train-start');
  assert.ok(!h.calls.some(c=>c.url==='/api/training/jobs' && c.method==='POST'));
});

test('empty library and list request failure provide a recoverable, non-selectable state',async()=>{
  const h=harness(null,false,[],'/api/datasets');
  await h.click('dataset-open');
  assert.equal(h.el('ds-retry').hidden,false);
  await h.click('ds-retry');
  assert.match(h.el('ds-feedback').textContent,/저장된 데이터셋이 없습니다/);
  assert.equal(h.el('ds-confirm').disabled,true);
});
