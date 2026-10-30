const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname,'../telecom_support/web/static/call.js'),'utf8');

function harness({denied = false, pending = false} = {}) {
  const elements = new Map(), requests = [], intervals = new Map(), rooms = [];
  let nextTimer = 0, status = 'starting_agent', captures = 0, resolveCapture;
  function element(id) {
    if (!elements.has(id)) elements.set(id,{value:'',hidden:false,disabled:false,textContent:'',dataset:{},attrs:{},handlers:{},children:[],
      addEventListener(name,fn){this.handlers[name]=fn;},setAttribute(name,value){this.attrs[name]=value;},
      append(value){this.children.push(value);},replaceChildren(){this.children=[];},querySelectorAll(){return this.children;}});
    return elements.get(id);
  }
  const keys = [...'123456789*0#'].map(key => Object.assign(element('key-'+key),{dataset:{key}}));
  const mic={stopped:false,muted:false,stop(){this.stopped=true;},async mute(){this.muted=true;},async unmute(){this.muted=false;}};
  class Room {
    constructor(){this.handlers={};this.canPlaybackAudio=true;this.localParticipant={publishTrack:async track=>{this.published=track;}};rooms.push(this);}
    on(name,fn){this.handlers[name]=fn;}
    async startAudio(){}
    async connect(){this.connected=true;}
    async disconnect(){this.connected=false;if(this.handlers.disconnected)this.handlers.disconnected();}
  }
  const client={Room,RoomEvent:{TrackSubscribed:'track',AudioPlaybackStatusChanged:'playback',Disconnected:'disconnected'},
    Track:{Kind:{Audio:'audio'},Source:{Microphone:'microphone'}},
    async createLocalAudioTrack(){
      captures++;
      if(pending)return new Promise(resolve=>{resolveCapture=resolve;});
      if(denied){denied=false;const error=new Error('Denied');error.name='NotAllowedError';throw error;}
      return mic;
    }};
  const window={isSecureContext:true,LivekitClient:client,addEventListener(){}};
  const context={window,LivekitClient:client,navigator:{mediaDevices:{}},performance:{now:()=>1000},
    document:{getElementById:element,querySelectorAll:()=>keys},
    setInterval(fn){const id=++nextTimer;intervals.set(id,fn);return id;},clearInterval(id){intervals.delete(id);},
    async fetch(url,options={}){
      requests.push({url,method:options.method||'GET'});
      const body=options.method==='POST'?{session_id:'fixture',url:'wss://fixture',token:'fixture-token'}:options.method==='DELETE'?{status:'ended'}:{status};
      return {ok:true,json:async()=>body};
    }};
  vm.runInNewContext(script,context);
  return {element,requests,rooms,mic,keys,captures:()=>captures,intervals,
    async click(id){return element(id).handlers.click();},
    async tick(){for(const fn of [...intervals.values()])await fn();},
    status(value){status=value;},resolveCapture(){resolveCapture(mic);}};
}

test('keypad edits the private code without starting a session',async()=>{
  const h=harness();
  await h.click('key-2');await h.click('key-0');await h.click('key-8');
  assert.equal(h.element('code').value,'208');
  await h.click('erase');assert.equal(h.element('code').value,'20');
  assert.equal(h.requests.length,0);
});

test('empty call does not ask for microphone permission',async()=>{
  const h=harness();await h.click('start');
  assert.equal(h.captures(),0);assert.equal(h.requests.length,0);
  assert.match(h.element('dial-error').textContent,/code/);
});

test('permission denial keeps retry visible and creates no paid room',async()=>{
  const h=harness({denied:true});h.element('code').value='12345678';
  await h.click('start');
  assert.equal(h.element('device').dataset.phase,'connecting');
  assert.equal(h.element('permission').hidden,false);
  assert.equal(h.element('retry-mic').disabled,false);
  assert.equal(h.requests.length,0);
  await h.click('retry-mic');
  assert.equal(h.captures(),2);assert.equal(h.element('device').dataset.phase,'ringing');
  assert.equal(h.requests.filter(r=>r.method==='POST').length,1);
});

test('ringing becomes connected only after backend agent readiness',async()=>{
  const h=harness();h.element('code').value='12345678';await h.click('start');
  assert.equal(h.element('device').dataset.phase,'ringing');
  assert.equal(h.element('timer').hidden,true);
  h.status('connected');await h.tick();
  assert.equal(h.element('device').dataset.phase,'connected');
  assert.equal(h.element('timer').hidden,false);
  assert.equal(h.rooms[0].published,h.mic);
  await h.click('mute');assert.equal(h.mic.muted,true);
  await h.click('end');assert.equal(h.mic.stopped,true);
  assert.equal(h.element('device').dataset.phase,'ended');
  assert.ok(h.requests.some(r=>r.method==='DELETE'));
});

test('cancelling while the permission dialog is open prevents a late call',async()=>{
  const h=harness({pending:true});h.element('code').value='12345678';
  const start=h.click('start');await h.click('end');
  h.resolveCapture();await start;
  assert.equal(h.mic.stopped,true);assert.equal(h.requests.length,0);
  assert.equal(h.element('device').dataset.phase,'ended');
});

test('repeated call clicks do not duplicate pending permission requests',async()=>{
  const h=harness({pending:true});h.element('code').value='12345678';
  const start=h.click('start');await h.click('start');await h.click('retry-mic');
  assert.equal(h.captures(),1);
  h.resolveCapture();await start;
  assert.equal(h.requests.filter(r=>r.method==='POST').length,1);
});
