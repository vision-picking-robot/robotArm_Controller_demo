const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../app/static/browser-voice.js'),'utf8').replace('export class BrowserVoice','globalThis.BrowserVoice = class BrowserVoice');

function setup() {
  const instances = [], calls = [], updates = [];
  class Recognition {
    constructor() { instances.push(this); }
    start() { this.started = true; }
    stop() {}
    abort() { this.aborted = true; }
  }
  const scope = {webkitSpeechRecognition:Recognition,isSecureContext:true,
    async fetch(url, options) {
      calls.push({url, options});
      return {ok:true, async json() {
        return url.endsWith('/wake') ? {session:'s'.repeat(32),turn:1,state:'listening',can_stop:true} :
          url.endsWith('/stop') ? {state:'idle',can_stop:false} : {turn:2,state:'listening',can_stop:true,reply:'완료'};
      }};
    }};
  const context = vm.createContext({}); vm.runInContext(source, context);
  const voice = new context.BrowserVoice(status=>updates.push(status),scope);
  return {voice,scope,instances,calls,updates};
}

test('Korean final is sent once; stop discards queued results', async()=>{
  const {voice,instances,calls} = setup();
  await voice.start();
  const recognition=instances[0];
  assert.equal(recognition.lang,'ko-KR');
  const event={resultIndex:0,results:[Object.assign([{transcript:'재고 알려줘'}],{isFinal:true})]};
  await recognition.onresult(event);
  await recognition.onresult(event);
  assert.equal(calls.filter(c=>c.url.endsWith('/transcript')).length,1);
  const body=JSON.parse(calls.find(c=>c.url.endsWith('/transcript')).options.body);
  assert.equal(body.text,'재고 알려줘'); assert.equal(body.turn,1);
  const latest=instances.at(-1); await voice.stop();
  await latest.onresult(event);
  assert.equal(latest.aborted,true);
  assert.equal(calls.filter(c=>c.url.endsWith('/transcript')).length,1);
});

test('unsupported browser and insecure origin never wake the server',async()=>{
  const {voice,scope,calls}=setup();
  scope.webkitSpeechRecognition=undefined;
  await assert.rejects(()=>voice.start(),/지원하지/);
  assert.equal(calls.length,0);
  scope.webkitSpeechRecognition=class {};
  scope.isSecureContext=false;
  await assert.rejects(()=>voice.start(),/HTTPS/);
});

test('microphone denial stops server session and reports error',async()=>{
  const {voice,instances,calls,updates}=setup(); await voice.start();
  instances[0].onerror({error:'not-allowed'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(voice.active,false);
  assert.equal(updates.at(-1).state,'error');
  assert.ok(calls.some(c=>c.url.endsWith('/stop')));
});
