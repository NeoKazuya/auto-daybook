import assert from 'node:assert/strict';
import { test } from 'node:test';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';

// DOM and transport are boundaries; API and page modules are the shipped source.
class Element {
  constructor(spec, attrs = {}, children = []) {
    this.classList = {add(){},remove(){}};
    this.spec = spec; this.attrs = attrs; Object.assign(this, attrs); this.children = children.flat(Infinity).filter(x => x != null && x !== false);
    if (spec.startsWith('textarea')) this.value = this.textContent;
    if (spec.startsWith('input')) this.value ??= '';
    if (spec.startsWith('select')) this.value ??= this.children.find(x => x instanceof Element && x.spec.startsWith('option'))?.value ?? '';
    for (const child of this.children) if (child instanceof Element) child.parent = this;
  }
  append(...x) { for (const child of x.flat(Infinity)) { if (child instanceof Element) child.parent = this; this.children.push(child); } }
  replaceChildren(...x) { this.children = []; this.append(...x.filter(x => x != null && x !== false)); }
  get textContent() { return this.children.map(x => x instanceof Element ? x.textContent : String(x)).join(' '); }
  set textContent(x) { this.children = [x]; }
  focus() { let root = this; while (root.parent) root = root.parent; if (root.document) root.document.activeElement = this; }
  getAttribute(name) { return this.attrs[name] ?? null; }
  scrollIntoView() {}
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  contains(n) { return walk(this).includes(n); }
  matches(selector) {
    return selector.split(',').some(raw => {
      const [_, tag, cls, attr, value] = raw.trim().match(/^([a-z]+)?(?:\.([\w-]+))?(?:\[([\w-]+)(?:="([^"]*)")?\])?$/) ?? [];
      return Boolean(_) && (!tag || this.spec.split('.')[0] === tag)
        && (!cls || this.spec.split('.').includes(cls))
        && (!attr || (value == null ? this[attr] != null : this[attr] === value));
    });
  }
  addEventListener(name, fn) { this['on' + name] = fn; }
  closest(selector) { let n = this; while (n) { if (n.matches(selector)) return n; n = n.parent; } return null; }
  querySelectorAll(selector) {
    const parts = selector.split(' ');
    return walk(this).slice(1).filter(n => {
      if (!n.matches(parts.at(-1))) return false;
      if (parts.length === 1) return true;
      let parent = n.parent;
      while (parent && parent !== this) { if (parent.matches(parts[0])) return true; parent = parent.parent; }
      return false;
    });
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
}
function h(spec, attrs, ...children) {
  if (!attrs || typeof attrs !== 'object' || attrs instanceof Element || Array.isArray(attrs)) return new Element(spec, {}, [attrs, ...children]);
  return new Element(spec, attrs, children);
}
const walk = n => n instanceof Element ? [n, ...n.children.flatMap(walk)] : [];
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };
const defer = () => { let resolve,reject; const promise = new Promise((yes,no) => {resolve=yes;reject=no;}); return {promise,resolve,reject}; };
const answer = { type: 'answer', text: 'You spent 4 minutes on journal tests', cites: ['s1'], window: { from: '2026-10-11T08:00:00Z', to: '2026-10-11T09:00:00Z' } };
const draft = { type: 'draft', summary: 'Stand after meetings', entry: { id: 'stand-after-meeting' } };
async function setup({ developer = false, manualTimers = false } = {}) {
  let request = async path => path.startsWith('/api/timeline') ? { segments: [{id:'s1', title:'Old title'}] } : {};
  const streams = [], channels = [], calls = [], toasts = [], intervals = [], timers = [];
  const context = vm.createContext({
    URLSearchParams, Date, console,
    setTimeout:manualTimers?(fn,delay)=>{const timer={fn,delay};timers.push(timer);return timer;}:setTimeout,
    clearTimeout:manualTimers?timer=>{if(timer)timer.cancelled=true;}:clearTimeout,
    setInterval:fn=>intervals.push(fn),
    document: { querySelector: () => ({content:'test-token'}), activeElement:null },
    location: {search:''}, history: {replaceState(){}},
    fetch: async (path, options) => { calls.push([path, options]); const result = await request(path, options); return result instanceof Response ? result : {ok:true,json:async()=>result}; },
    EventSource: class { constructor() {this.listeners={}; streams.push(this);} addEventListener(n,f){this.listeners[n]=f;} close(){} },
    BroadcastChannel: class { constructor(){channels.push(this);} postMessage(x){this.sent=x;} close(){} },
  });
  const cache = new Map();
  const helpers = { h, icon:n=>h('i',n), btn:(text,attrs={})=>h('button',attrs,text),
    ribbon:(p,x)=>h('header',x), panel:(title,icon,tools,...body)=>h('section',title,tools,body),
    split:(a,b)=>h('div',a,b), emptyState:(i,...x)=>h('div',x), errorState:e=>h('div',e.message),
    skeletons:()=>h('div','loading'), stat:(i,...x)=>h('div',x), longDay:()=>'', dayStamp:()=>'', clock:x=>x, duration:x=>String(x)+'s', folio:x=>x,
    busy:async(button,fn)=>fn(), confirmDialog:async()=>true, toast:(...args)=>toasts.push(args) };
  async function load(path) {
    if(cache.has(path)) return cache.get(path);
    let mod;
    if(path.endsWith('/ui.js') || path.endsWith('/prefs.js')) {
      const exports = path.endsWith('/prefs.js') ? {getPref:key=>key === 'developer' ? developer : false,setPref:()=>{},applyPrefs:()=>{}} : helpers;
      mod = new vm.SyntheticModule(Object.keys(exports), function(){for(const [k,v] of Object.entries(exports)) this.setExport(k,v);},{context});
    } else mod = new vm.SourceTextModule(await readFile(path,'utf8'), {context,identifier:path});
    cache.set(path,mod);
    await mod.link(spec=>load(resolve(dirname(path),spec)));
    return mod;
  }
  const base = resolve('src/daybook/web/static/js');
  const apiModule = await load(base+'/api.js'); await apiModule.evaluate();
  async function page(name, params='') {
    const m=await load(base+'/pages/'+name+'.js'); await m.evaluate();
    const root=h('main'); root.document=context.document; const hooks=await m.namespace.default.render(root,new URLSearchParams(params));
    return {root,hooks,ask:async message=>{const input=walk(root).find(x=>x.spec.startsWith('input')); input.value=message; walk(root).find(x=>x.spec.startsWith('form')).onsubmit({preventDefault(){}}); await settle();}};
  }
  async function app({ route = 'daily', waitForBoot = true } = {}) {
    const surfaces=Object.fromEntries(['page','nav','footer','sidebar-foot'].map(id=>[id,h('div')]));
    const pip={dataset:{}};
    for(const element of Object.values(surfaces))element.document=context.document;
    context.document.getElementById=id=>surfaces[id];
    context.document.querySelectorAll=selector=>selector==='.state-pip'?[pip]:[];
    const listeners={};
    context.window={scrollTo(){},addEventListener:(name,fn)=>{listeners[name]=fn;}};
    context.location.hash='#/'+route;
    const module=await load(base+'/app.js'),ready=module.evaluate();
    if(waitForBoot)await ready;
    return {root:surfaces.page,footer:surfaces.footer,pip,ready,refresh:()=>intervals.at(-1)(),
      navigate:route=>{context.location.hash='#/'+route;return listeners.hashchange();}};
  }
  return {api:apiModule.namespace.api,subscribe:apiModule.namespace.subscribe,page,app,streams,channels,calls,toasts,
    flushEvents:()=>{for(const timer of timers.filter(t=>!t.cancelled&&!t.ran&&t.delay===250)){timer.ran=true;timer.fn();}},
    runNextTimer:()=>{const timer=timers.find(t=>!t.cancelled&&!t.ran);assert.ok(timer,'a debounce is pending');assert.equal(timer.delay,350);timer.ran=true;return timer.fn();},
    setRequest:fn=>{request=fn;}};
}
test('forget clears cached recall while keeping draft across navigation', async()=>{
  const x=await setup();
  x.setRequest(async path=>path==='/api/chat'?draft:{});
  const p=await x.page('board'); await p.ask('draft a skill');
  x.setRequest(async path=>path==='/api/chat'?answer:path.startsWith('/api/timeline')?{segments:[{id:'s1',title:'Old title'}]}:{});
  await p.ask('recall'); assert.match(p.root.textContent,/4 minutes/);
  p.hooks.cleanup?.(); await x.api.forgetAll();
  const next=await x.page('board');
  assert.doesNotMatch(next.root.textContent,/4 minutes|Old title/);
  assert.match(next.root.textContent,/Stand after meetings/);
  assert.equal(walk(next.root).find(n=>n.href?.startsWith('#/skills')).href,'#/skills?id=stand-after-meeting');
});
test('forget fences a delayed recall response and a delayed citation lookup', async()=>{
  for(const stage of ['chat','timeline']) {
    const x=await setup(), pending=defer();
    x.setRequest(path=>path==='/api/chat'?(stage==='chat'?pending.promise:answer):path.startsWith('/api/timeline')?pending.promise:{});
    const p=await x.page('board'); await p.ask('recall'); await x.api.forgetAll();
    pending.resolve(stage==='chat'?answer:{segments:[{id:'s1',title:'Old title'}]}); await settle();
    assert.doesNotMatch(p.root.textContent,/4 minutes|Old title/);
  }
});
test('delayed skill draft survives a note edit', async()=>{
  const x=await setup(), pending=defer(); x.setRequest(path=>path==='/api/chat'?pending.promise:{});
  const p=await x.page('board'); await p.ask('draft'); await x.api.editSegment('s1',{title:'Edited'});
  pending.resolve(draft); await settle(); assert.match(p.root.textContent,/Stand after meetings/);
});
test('ordinary journal updates keep recall; note invalidation, other tab and reconnect clear it', async()=>{
  for(const source of ['journal','tab','reconnect']) {
    const x=await setup(); x.setRequest(path=>path==='/api/chat'?answer:{segments:[]});
    x.subscribe(()=>{}); const s=x.streams[0]; s.onopen();
    const p=await x.page('board'); await p.ask('recall');
    s.listeners.ObservationAdded({data:'{}'}); s.listeners.SegmentChanged({data:'{}'});
    s.listeners.StatusChanged({data:JSON.stringify({component:'journal',status:'notes_changed'})});
    assert.match(p.root.textContent,/4 minutes/);
    if(source==='journal') s.listeners.StatusChanged({data:JSON.stringify({component:'journal',status:'notes_invalidated'})});
    if(source==='tab') x.channels[0]?.onmessage({data:{type:'notes_changed'}});
    if(source==='reconnect') s.onopen();
    assert.doesNotMatch(p.root.textContent,/4 minutes/);
  }
});
test('API unwraps index wire shapes and null segment search result is not a link', async()=>{
  const x=await setup(); x.setRequest(path=>path.startsWith('/api/search')?{hits:[{segment_id:null,observation_id:'o1',at:'2026-10-11T08:00:00Z',kind:null,title:'Pending note',snippet:'hello'}]}:{totals:[{label:'Code',active:55}]});
  assert.equal((await x.api.search('hello'))[0].observation_id,'o1');
  assert.equal((await x.api.totals('app'))[0].label,'Code');
  const p=await x.page('tags','q=hello'); await settle();
  assert.match(p.root.textContent,/Pending note/); assert.match(p.root.textContent,/Code/);
  assert.equal(walk(p.root).some(n=>n.href?.includes('seg=null')),false);
});
test('review shows questions and permissions without developer mode and names bubble layout',async()=>{
  const x=await setup();
  x.setRequest(()=>({entries:[{id:'stand',name:'Stand',goal:'Stand after meetings.',status:'needs_review',origin:'user',hash:'hash',signals:[{ask:'Is a meeting visible?'}],allow:['overlay.show'],rules:[{when:'in_meeting becomes false',steps:[{action:'overlay.show',arguments:{title:'Stand',layout:'bubble'}}]}]}],catalog:[{name:'overlay.show',description:'Show a reminder on your desktop'}]}));
  const p=await x.page('skills','id=stand');
  assert.match(p.root.textContent,/Is a meeting visible/); assert.match(p.root.textContent,/Show a reminder on your desktop/);
  assert.match(p.root.textContent,/bubble/); assert.doesNotMatch(p.root.textContent,/full-screen|Skill file/);
});

test('every successful note mutation clears recall and broadcasts without note contents',async()=>{
  for(const [method,args] of [['editSegment',['s1',{title:'secret'}]],['deleteSegment',['s1']],['editObservation',['o1',{seen:'secret'}]],['deleteObservation',['o1']],['forget',['from','to']],['forgetAll',[]]]) {
    const x=await setup(); x.setRequest(path=>path==='/api/chat'?answer:{segments:[]});
    const p=await x.page('board'); await p.ask('recall'); await x.api[method](...args);
    assert.doesNotMatch(p.root.textContent,/4 minutes/);
    assert.equal(JSON.stringify(x.channels[0].sent),'{"type":"notes_changed"}');
  }
});
test('failed mutation retains existing recall',async()=>{
  const x=await setup(); x.setRequest(path=>path==='/api/chat'?answer:{segments:[]});
  const p=await x.page('board'); await p.ask('recall');
  x.setRequest(()=>{throw Error('offline');}); await assert.rejects(x.api.forgetAll());
  assert.match(p.root.textContent,/4 minutes/);
  assert.equal(x.channels[0].sent,undefined);
});
test('search finishing after forget cannot paint deleted results',async()=>{
  const x=await setup(), pending=defer(); let searches=0;
  x.setRequest(path=>path.startsWith('/api/search')?(++searches===1?pending.promise:{hits:[]}):{totals:[]});
  const p=await x.page('tags','q=secret'); await settle(); await x.api.forgetAll(); await settle();
  pending.resolve({hits:[{segment_id:'s1',at:'2026-10-11T08:00:00Z',kind:'work',title:'Deleted secret',snippet:'secret'}]}); await settle();
  assert.doesNotMatch(p.root.textContent,/Deleted secret/);
  assert.match(p.root.textContent,/No matches/);
  p.hooks.cleanup();
});

test('local demo returns the same search and totals envelopes',async()=>{
  const context=vm.createContext({Date, URL, setInterval:()=>{}, setTimeout:fn=>fn()});
  const module=new vm.SourceTextModule(await readFile('src/daybook/web/static/js/demo.js','utf8'),{context});
  await module.link(()=>{throw Error('unexpected import');}); await module.evaluate();
  const search=await module.namespace.handle('GET','/api/search?q=architecture&limit=1');
  assert.equal(search.hits.length,1); assert.equal(typeof search.hits[0].snippet,'string');
  const totals=await module.namespace.handle('GET','/api/totals?by=app');
  assert.ok(totals.totals.length); assert.equal(typeof totals.totals[0].label,'string');
  assert.equal(typeof totals.totals[0].active,'number'); assert.equal(totals.totals[0].key,undefined);
});

test('pending reply renders on the current board after navigation',async()=>{
  const x=await setup(), pending=defer(); x.setRequest(path=>path==='/api/chat'?pending.promise:{segments:[]});
  const old=await x.page('board'); await old.ask('draft'); old.hooks.cleanup?.();
  const current=await x.page('board'); pending.resolve(draft); await settle();
  assert.match(current.root.textContent,/Stand after meetings/);
  assert.doesNotMatch(current.root.textContent,/Reading your notes/);
});

test('Title has always stores literal substring globs, including punctuation',async()=>{
  for(const [literal,near] of [['Budget [Q4]','Budget Q'],['Why?','Whyx'],['a*b','aZZb'],['report]','report'],['[?*]','x'],['plain','other']]) {
    const x=await setup(); let privacy={paused:false,exclude_apps:[],exclude_titles:[],screenshots_stored:'none'};
    x.setRequest((path,options)=>{
      if(path==='/api/privacy') {if(options.method==='PUT') privacy=JSON.parse(options.body); return privacy;}
      if(path==='/api/status') return {gateway:{},music:{},capture:'running'};
      return {totals:[]};
    });
    const p=await x.page('settings');
    const form=walk(p.root).find(n=>n.spec==='form.excl-form');
    assert.ok(form,p.root.textContent);
    walk(form).find(n=>n.spec==='select').value='title';
    walk(form).find(n=>n.spec.startsWith('input')).value=literal;
    form.onsubmit({preventDefault(){}}); await settle();
    assert.equal(privacy.exclude_titles.length,1);
    const result=spawnSync('python3',['-c',
      'import fnmatch,json,sys; p,l,n=json.loads(sys.argv[1]); assert fnmatch.fnmatchcase(("Window — "+l+" — Browser").lower(),p.lower()); assert not fnmatch.fnmatchcase(("Window — "+n+" — Browser").lower(),p.lower())',
      JSON.stringify([privacy.exclude_titles[0],literal,near])],{encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);
    assert.match(p.root.textContent,new RegExp(literal.replace(/[.*+?^$()|[\]\\]/g,'\\$&')));
  }
});
test('unsaved skill edits cannot approve or enable the saved version; Save refreshes exact hash',async()=>{
  for(const status of ['needs_review','disabled']) {
    const x=await setup({developer:true});
    let skill={id:'stand',name:'Stand',goal:'Stand.',status,origin:'user',hash:'old-hash',text:'original skill',signals:[],allow:[],rules:[],errors:[]};
    const writes=[];
    x.setRequest((path,options)=>{
      if(options.method!=='GET') writes.push([path,options.body]);
      if(path==='/api/skills/stand'&&options.method==='PUT') skill={...skill,text:JSON.parse(options.body).text,hash:'new-hash',status:'needs_review'};
      return path==='/api/skills'?{entries:[skill],catalog:[]}:{errors:[]};
    });
    const p=await x.page('skills','id=stand');
    const editor=walk(p.root).find(n=>n.spec.startsWith('textarea'));
    const approval=walk(p.root).find(n=>n.textContent===(status==='disabled'?'Enable':'Approve & enable'));
    editor.value='changed skill'; editor.oninput();
    assert.equal(approval.disabled,true);
    await approval.onclick({currentTarget:approval}); await settle();
    assert.equal(writes.some(([path])=>path.endsWith('/approve')||path.endsWith('/enable')),false);
    editor.value='original skill'; editor.oninput();
    assert.equal(approval.disabled,false);
    editor.value='changed skill'; editor.oninput();
    assert.equal(approval.disabled,true);
    await p.hooks.onEvent(new Set(['SkillsChanged'])); await settle();
    assert.equal(walk(p.root).find(n=>n.spec.startsWith('textarea')).value,'changed skill');
    const save=walk(p.root).find(n=>n.textContent==='Save edits');
    await save.onclick({currentTarget:save}); await settle();
    const current=walk(p.root).find(n=>n.textContent==='Approve & enable');
    assert.ok(!current.disabled);
    await current.onclick({currentTarget:current}); await settle();
    assert.deepEqual(JSON.parse(writes.find(([path])=>path.endsWith('/approve'))[1]),{hash:'new-hash'});
  }
});

test('failed skill Save keeps unsaved text and remains retryable',async()=>{
  for (const failure of ['network','forbidden']) {
    const x=await setup({developer:true}); let fail=true;
    let skill={id:'stand',name:'Stand',goal:'Stand.',status:'needs_review',origin:'user',hash:'old',text:'saved version',signals:[],allow:[],rules:[],errors:[]};
    x.setRequest((path,options)=>{
      if(path==='/api/skills/stand'&&options.method==='PUT') {
        if(fail) { if(failure==='network') throw Error('offline'); return new Response(JSON.stringify({detail:'forbidden'}),{status:403}); }
        skill={...skill,text:JSON.parse(options.body).text,hash:'new'};
      }
      return path==='/api/skills'?{entries:[skill],catalog:[]}:{errors:[]};
    });
    const p=await x.page('skills','id=stand');
    let editor=walk(p.root).find(n=>n.spec.startsWith('textarea'));
    editor.value='unsaved work'; editor.oninput();
    let save=walk(p.root).find(n=>n.textContent==='Save edits');
    await save.onclick({currentTarget:save}); await settle();
    editor=walk(p.root).find(n=>n.spec.startsWith('textarea'));
    assert.equal(editor.value,'unsaved work',failure);
    assert.equal(walk(p.root).find(n=>n.textContent==='Approve & enable').disabled,true);
    fail=false; save=walk(p.root).find(n=>n.textContent==='Save edits');
    await save.onclick({currentTarget:save}); await settle();
    assert.equal(skill.text,'unsaved work'); assert.equal(skill.hash,'new');
    assert.ok(!walk(p.root).find(n=>n.textContent==='Approve & enable').disabled);
  }
});
test('Index totals are summaries, not full-text filters for app or kind labels',async()=>{
  const x=await setup();
  x.setRequest(path=>path.startsWith('/api/totals?by=app')?{totals:[{label:'Firefox',active:100}]}:path.startsWith('/api/totals')?{totals:[{label:'coding',active:50}]}:{hits:[]});
  const p=await x.page('tags');
  assert.match(p.root.textContent,/Firefox/); assert.match(p.root.textContent,/coding/);
  const totals=walk(p.root).filter(n=>n.matches('.tag')&&/Firefox|coding/.test(n.textContent));
  assert.equal(totals.length,2);
  assert.ok(totals.every(n=>!n.onclick&&!n.spec.startsWith('button')&&!n.href));
  assert.equal(x.calls.some(([path])=>path.startsWith('/api/search')),false);
});
test('demo recall works after deleting down to two, one and zero chapters',async()=>{
  const context=vm.createContext({Date,URL,setInterval:()=>{},setTimeout:fn=>fn()});
  const module=new vm.SourceTextModule(await readFile('src/daybook/web/static/js/demo.js','utf8'),{context});
  await module.link(()=>{throw Error('unexpected import');}); await module.evaluate();
  const call=module.namespace.handle;
  const initial=(await call('GET','/api/timeline')).segments;
  for(let count=2;count>=0;count--) {
    const current=(await call('GET','/api/timeline')).segments;
    for(const s of current.slice(count)) await call('DELETE','/api/segments/'+s.id);
    const response=await call('POST','/api/chat',{message:'/ask What did I do?'});
    assert.equal(response.type,'answer'); assert.equal(typeof response.text,'string');
    const ids=new Set((await call('GET','/api/timeline')).segments.map(s=>s.id));
    assert.ok(response.cites.every(id=>ids.has(id)));
    assert.equal(response.cites.length,count);
    if(!count) assert.match(response.text,/no notes|don't have notes/i);
  }
  assert.ok(initial.length>=3);
  await call('POST','/api/forget-all',{});
  assert.equal((await call('POST','/api/chat',{message:'/ask What did I do?'})).cites.length,0);
});
test('Contents shows privacy gaps even before the first chapter',async()=>{
  for(const [state,label] of [['paused','Paused'],['locked','Screen locked'],['away','Away'],['withheld','Private window'],['overlay','Overlay on screen']]) {
    const x=await setup();
    x.setRequest(path=>path.startsWith('/api/timeline')?{segments:[],capture_spans:[{state,start:'2026-10-11T08:00:00Z',end:'2026-10-11T08:01:00Z'}]}:path==='/api/skills'?{entries:[]}:path==='/api/status'?{capture:state,gateway:{reachable:true,calls_per_minute:0}}:{totals:[]});
    const p=await x.page('contents');
    assert.match(p.root.textContent,new RegExp(label+' — no notes'));
    assert.match(p.root.textContent,/60s/); assert.doesNotMatch(p.root.textContent,/No notes yet/);
  }
});
test('live privacy event updates exclusions and keeps pending form text, match and focus',async()=>{
  const x=await setup(); let privacy={paused:false,exclude_apps:[],exclude_titles:[],screenshots_stored:'none'};
  x.setRequest(path=>path==='/api/privacy'?structuredClone(privacy):path==='/api/status'?{gateway:{},music:{},capture:'running'}:{totals:[]});
  const p=await x.page('settings');
  let form=walk(p.root).find(n=>n.spec==='form.excl-form');
  form.querySelector('select').value='title';
  let input=form.querySelector('input'); input.list={id:'apps-blocked'}; input.value='Budget [Q4]'; input.setSelectionRange(3,8); input.focus();
  privacy={...privacy,exclude_apps:['KeePassXC']};
  await p.hooks.onEvent(new Set(['StatusChanged']),[{type:'StatusChanged',component:'sense',status:'privacy_changed'}]); await settle();
  assert.match(p.root.textContent,/KeePassXC/);
  form=walk(p.root).find(n=>n.spec==='form.excl-form'); input=form.querySelector('input');
  assert.equal(input.value,'Budget [Q4]'); assert.equal(form.querySelector('select').value,'title');
  assert.equal(p.root.document.activeElement,input); assert.equal(input.selectionStart,3); assert.equal(input.selectionEnd,8);
  const before=x.calls.length;
  await p.hooks.onEvent(new Set(['StatusChanged']),[{type:'StatusChanged',component:'sense',status:'observed'}]); await settle();
  assert.equal(x.calls.length,before,'ordinary capture polls do not refresh Settings');
});
test('queued privacy writes read the current resource when remote events are delayed',async()=>{
  const x=await setup(); let privacy={paused:false,exclude_apps:[],exclude_titles:[],screenshots_stored:'none'}; const writes=[];
  x.setRequest((path,options)=>{
    if(path==='/api/privacy') { if(options.method==='PUT') {privacy=JSON.parse(options.body);writes.push(structuredClone(privacy));} return structuredClone(privacy); }
    if(path==='/api/status')return {gateway:{},music:{},capture:'running'};
    return {totals:[]};
  });
  const p=await x.page('settings');
  privacy={...privacy,exclude_apps:['KeePassXC'],exclude_titles:['*secret*']};
  const monitoring=walk(p.root).find(n=>n['aria-label']==='Monitoring'); monitoring.checked=false;
  await monitoring.onchange(); await settle();
  assert.equal(privacy.paused,true); assert.deepEqual(privacy.exclude_apps,['KeePassXC']); assert.deepEqual(privacy.exclude_titles,['*secret*']);
  privacy={...privacy,exclude_apps:['KeePassXC','Firefox']};
  const form=walk(p.root).find(n=>n.spec==='form.excl-form');
  form.querySelector('input').value='Code'; form.onsubmit({preventDefault(){}}); await settle();
  assert.deepEqual(privacy.exclude_apps,['KeePassXC','Firefox','Code']); assert.deepEqual(privacy.exclude_titles,['*secret*']);
  assert.equal(writes.length,2);
});

test('clean stale approval still reloads the current skill and retries its reviewed hash',async()=>{
  const x=await setup({developer:true}); let stale=true;
  let skill={id:'stand',name:'Stand',goal:'Stand.',status:'needs_review',origin:'user',hash:'old',text:'old version',signals:[],allow:[],rules:[],errors:[]};
  const hashes=[];
  x.setRequest((path,options)=>{
    if(path.endsWith('/approve')) {
      hashes.push(JSON.parse(options.body).hash);
      if(stale) { stale=false; skill={...skill,hash:'new',text:'new server version'}; return new Response(JSON.stringify({detail:'stale_approval'}),{status:409}); }
      skill={...skill,status:'active'};
    }
    return path==='/api/skills'?{entries:[skill],catalog:[]}:{};
  });
  const p=await x.page('skills','id=stand');
  let approve=walk(p.root).find(n=>n.textContent==='Approve & enable');
  await approve.onclick({currentTarget:approve}); await settle();
  assert.equal(walk(p.root).find(n=>n.spec.startsWith('textarea')).value,'new server version');
  approve=walk(p.root).find(n=>n.textContent==='Approve & enable');
  await approve.onclick({currentTarget:approve}); await settle();
  assert.deepEqual(hashes,['old','new']); assert.equal(skill.status,'active');
});

test('Skills refresh that started clean preserves text typed while GET is pending',async()=>{
  const x=await setup({developer:true}), pending=defer();
  const skill={id:'stand',name:'Stand',goal:'Stand.',status:'needs_review',origin:'user',hash:'old',text:'server version',signals:[],allow:[],rules:[],errors:[]};
  x.setRequest(path=>path==='/api/skills'?{entries:[skill],catalog:[]}:{errors:[]});
  const p=await x.page('skills','id=stand');
  x.setRequest(path=>path==='/api/skills'?pending.promise:{errors:[]});
  p.hooks.onEvent(new Set(['Reconnected'])); await settle();
  const editor=walk(p.root).find(n=>n.spec.startsWith('textarea'));
  editor.value='typed while refreshing'; editor.oninput(); editor.focus(); editor.setSelectionRange(2,7);
  pending.resolve({entries:[{...skill,hash:'remote-new',text:'changed remotely'}],catalog:[]}); await settle();
  const current=walk(p.root).find(n=>n.spec.startsWith('textarea'));
  assert.equal(current.value,'typed while refreshing');
  assert.equal(walk(p.root).find(n=>n.textContent==='Approve & enable').disabled,true);
  assert.equal(p.root.document.activeElement,current); assert.equal(current.selectionStart,2); assert.equal(current.selectionEnd,7);
});
test('successful Save preserves newer text entered while PUT is pending',async()=>{
  const x=await setup({developer:true}), pending=defer(); let first=true;
  let skill={id:'stand',name:'Stand',goal:'Stand.',status:'needs_review',origin:'user',hash:'old',text:'server version',signals:[],allow:[],rules:[],errors:[]};
  x.setRequest((path,options)=>{
    if(path==='/api/skills/stand'&&options.method==='PUT') {
      skill={...skill,text:JSON.parse(options.body).text,hash:first?'saved-first':'saved-second'};
      if(first) {first=false;return pending.promise;}
    }
    return path==='/api/skills'?{entries:[skill],catalog:[]}:{errors:[]};
  });
  const p=await x.page('skills','id=stand');
  const editor=walk(p.root).find(n=>n.spec.startsWith('textarea'));
  editor.value='submitted edit'; editor.oninput();
  let save=walk(p.root).find(n=>n.textContent==='Save edits');
  const saving=save.onclick({currentTarget:save}); await settle();
  editor.value='newer unsaved edit'; editor.oninput();
  pending.resolve({}); await saving; await settle();
  assert.equal(skill.text,'submitted edit');
  assert.equal(walk(p.root).find(n=>n.spec.startsWith('textarea')).value,'newer unsaved edit');
  assert.equal(walk(p.root).find(n=>n.textContent==='Approve & enable').disabled,true);
  save=walk(p.root).find(n=>n.textContent==='Save edits');
  await save.onclick({currentTarget:save}); await settle();
  assert.equal(skill.text,'newer unsaved edit'); assert.equal(skill.hash,'saved-second');
  assert.ok(!walk(p.root).find(n=>n.textContent==='Approve & enable').disabled);
});

test('selected skill row keeps the editor; another row cannot discard unsaved edits',async()=>{
  const x=await setup({developer:true});
  const base={goal:'Goal.',status:'needs_review',origin:'user',signals:[],allow:[],rules:[],errors:[]};
  x.setRequest(()=>({entries:[{...base,id:'stand',name:'Stand',hash:'one',text:'original'},{...base,id:'stretch',name:'Stretch',hash:'two',text:'other skill'}],catalog:[]}));
  const p=await x.page('skills','id=stand');
  const editor=walk(p.root).find(n=>n.spec.startsWith('textarea'));
  editor.value='unsaved draft'; editor.oninput(); editor.focus(); editor.setSelectionRange(3,6);
  const row=name=>walk(p.root).find(n=>n.role==='button'&&n.textContent.includes(name));
  row('Stand').onclick();
  assert.equal(walk(p.root).find(n=>n.spec.startsWith('textarea')).value,'unsaved draft');
  assert.equal(walk(p.root).find(n=>n.spec.startsWith('textarea')),editor);
  assert.equal(editor.value,'unsaved draft'); assert.equal(editor.selectionStart,3);
  row('Stretch').onclick();
  assert.equal(walk(p.root).find(n=>n.spec.startsWith('textarea')).value,'unsaved draft');
  assert.equal(walk(p.root).find(n=>n.textContent==='Approve & enable').disabled,true);
  assert.ok(x.toasts.some(([message])=>/save.*edit.*before.*skill/i.test(message)));
  editor.value='original'; editor.oninput(); row('Stretch').onclick();
  assert.equal(walk(p.root).find(n=>n.spec.startsWith('textarea')).value,'other skill');
});
test('Contents names the actual stopped capture state in empty and populated views',async()=>{
  for(const [state,label] of [['paused','Paused'],['locked','Screen locked'],['away','Away'],['withheld','Private window'],['overlay','Overlay']]) {
    for(const populated of [false,true]) {
      const x=await setup();
      x.setRequest(path=>path.startsWith('/api/timeline')?{segments:populated?[{id:'s1',title:'Work',kind:'coding',active:60,started_at:'2026-10-11T08:00:00Z'}]:[],capture_spans:[]}:path==='/api/skills'?{entries:[]}:path==='/api/status'?{capture:state,gateway:{reachable:true,calls_per_minute:0}}:{totals:[]});
      const p=await x.page('contents');
      assert.match(p.root.textContent,new RegExp(label,'i'));
      if(state!=='paused') assert.doesNotMatch(p.root.textContent,/Capture is paused/);
    }
  }
});

test('interleaved browser privacy edits retry the original operation without losing exclusions',async()=>{
  const a=await setup(),b=await setup(), reads=defer();
  let privacy={paused:false,exclude_apps:[],exclude_titles:[]}, interleave=false, arrivals=0, conflicts=0;
  const writes=[];
  const request=async(path,options)=>{
    if(path==='/api/privacy') {
      if(options.method==='GET') {
        const snapshot=structuredClone(privacy);
        if(interleave&&++arrivals<=2) { if(arrivals===2) reads.resolve(); await reads.promise; }
        return {...snapshot,keep_frames:false,screenshots_stored:'none',deletion_pending:false};
      }
      const body=JSON.parse(options.body); writes.push(body);
      if(body.expected&&JSON.stringify(body.expected)!==JSON.stringify(privacy)) {
        conflicts++; return new Response(JSON.stringify({detail:'privacy_conflict'}),{status:409});
      }
      privacy={paused:body.paused,exclude_apps:body.exclude_apps,exclude_titles:body.exclude_titles};
      return structuredClone(privacy);
    }
    if(path==='/api/status')return {gateway:{},music:{},capture:'running'};
    return {totals:[]};
  };
  a.setRequest(request);b.setRequest(request);
  const pa=await a.page('settings'),pb=await b.page('settings'); interleave=true;
  const add=(p,name)=>{const form=walk(p.root).find(n=>n.spec==='form.excl-form');form.querySelector('input').value=name;form.onsubmit({preventDefault(){}});};
  add(pa,'Code');add(pb,'Firefox');await settle();
  assert.deepEqual(privacy.exclude_apps,['Code','Firefox']);
  assert.equal(conflicts,1);assert.equal(writes.length,3);
  assert.ok(writes.every(body=>Object.keys(body.expected).sort().join(',')==='exclude_apps,exclude_titles,paused'));
  assert.deepEqual(writes[2].expected.exclude_apps,['Code']);
});
test('privacy conflicts stop after three attempts, preserving the pending input; other failures do not retry',async()=>{
  for(const [code,status,attempts] of [['privacy_conflict',409,3],['invalid_token_or_origin',403,1]]) {
    const x=await setup();let writes=0,gets=0,privacy={paused:false,exclude_apps:['KeePassXC'],exclude_titles:[]};
    x.setRequest((path,options)=>{
      if(path==='/api/privacy') {
        if(options.method==='GET') {gets++;return structuredClone(privacy);}
        writes++;privacy={...privacy,exclude_apps:['KeePassXC','Remote-'+writes]};
        return new Response(JSON.stringify({detail:code}),{status});
      }
      if(path==='/api/status')return {gateway:{},music:{},capture:'running'};
      return {totals:[]};
    });
    const p=await x.page('settings'),form=walk(p.root).find(n=>n.spec==='form.excl-form');
    form.querySelector('input').value='Code';form.onsubmit({preventDefault(){}});await settle();
    assert.equal(writes,attempts);assert.equal(gets,attempts+1);
    assert.equal(walk(p.root).find(n=>n.spec==='form.excl-form').querySelector('input').value,'Code');
    assert.ok(!privacy.exclude_apps.includes('Code'));
    assert.ok(x.toasts.some(([message])=>/Couldn't save/.test(message)));
  }
});
test('demo conditional privacy writes reject stale snapshots without storing expected',async()=>{
  const context=vm.createContext({Date,URL,setInterval:()=>{},setTimeout:fn=>fn()});
  const module=new vm.SourceTextModule(await readFile('src/daybook/web/static/js/demo.js','utf8'),{context});
  await module.link(()=>{throw Error('unexpected import');});await module.evaluate();
  const call=module.namespace.handle,current=await call('GET','/api/privacy');
  const values=p=>({paused:p.paused,exclude_apps:[...p.exclude_apps],exclude_titles:[...p.exclude_titles]});
  const expected=values(current);
  const saved=await call('PUT','/api/privacy',{...values(current),paused:true,expected});
  assert.equal(saved.paused,true);assert.equal(Object.hasOwn(saved,'expected'),false);
  await assert.rejects(call('PUT','/api/privacy',{...values(saved),paused:false,expected}),e=>e.status===409&&e.detail==='privacy_conflict');
  assert.equal((await call('GET','/api/privacy')).paused,true);
});

async function dailyFixture() {
  const x=await setup();
  const resource={segments:[{id:'s1',title:'Original title',intent:'Original intent',kind:'coding',active:60,started_at:'2026-10-11T08:00:00Z',ended_at:'2026-10-11T08:01:00Z',observations:[{id:'o1',seen:'Original note',visible_text:'Original screen text',started_at:'2026-10-11T08:00:00Z',window:{app:'Code',title:'app.py'}}]}],capture_spans:[]};
  const request=(path,options)=>{
    if(path.startsWith('/api/timeline'))return structuredClone(resource);
    if(options.method==='PATCH') {
      const target=path.includes('/observations/')?resource.segments[0].observations[0]:resource.segments[0];
      Object.assign(target,JSON.parse(options.body));return structuredClone(target);
    }
    return {};
  };
  x.setRequest(request);const p=await x.page('daily');
  walk(p.root).find(n=>n.spec==='div.seg-head').onclick();
  const open=kind=>walk(p.root).find(n=>kind==='segment'?n.spec.startsWith('button')&&n.textContent==='Edit segment':n.title==='Edit this note').onclick();
  const field=kind=>walk(p.root).find(n=>n['aria-label']===(kind==='segment'?'Title':'What was on screen'));
  return {x,p,resource,request,open,field};
}
for(const kind of ['segment','observation']) test('Daily pending GET keeps a newly opened '+kind+' editor; Cancel and Save still work',async()=>{
  const f=await dailyFixture(),pending=defer();
  f.x.setRequest(path=>path.startsWith('/api/timeline')?pending.promise:{});
  f.p.hooks.onEvent();await settle();f.open(kind);
  const editor=f.field(kind);editor.value='Unsaved draft';editor.focus();editor.setSelectionRange(2,5);
  f.resource.segments[0].title='Remote title';f.resource.segments[0].observations[0].seen='Remote note';
  pending.resolve(structuredClone(f.resource));await settle();
  assert.equal(f.field(kind)?.value,'Unsaved draft');
  assert.equal(f.field(kind),editor);assert.equal(editor.selectionStart,2);
  await walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Cancel').onclick();
  assert.equal(f.field(kind),undefined);assert.match(f.p.root.textContent,/Remote title/);assert.match(f.p.root.textContent,/Remote note/);
  f.x.setRequest(f.request);f.open(kind);f.field(kind).value='Saved after Cancel';
  await walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();await settle();
  assert.equal(f.field(kind),undefined);assert.match(f.p.root.textContent,/Saved after Cancel/);
  assert.ok(f.x.calls.some(([path,options])=>options.method==='PATCH'&&path.includes(kind==='segment'?'/segments/':'/observations/')));
});
test('Daily failed refresh does not replace a newly opened editor',async()=>{
  for(const kind of ['segment','observation']) {
    const f=await dailyFixture(),pending=defer();
    f.x.setRequest(path=>path.startsWith('/api/timeline')?pending.promise:{});
    f.p.hooks.onEvent();await settle();f.open(kind);f.field(kind).value='Draft through outage';
    pending.reject(Error('offline'));await settle();
    assert.equal(f.field(kind)?.value,'Draft through outage');
    assert.ok(f.x.toasts.some(([message])=>/refresh/i.test(message)));
  }
});
test('Daily redraw actions cannot discard an active editor',async()=>{
  for(const action of ['toggle','same-editor','other-editor','delete']) {
    const f=await dailyFixture();f.open('segment');f.field('segment').value='Keep this draft';
    const control=walk(f.p.root).find(n=>action==='toggle'?n.spec==='div.seg-head':action==='same-editor'?n.spec.startsWith('button')&&n.textContent==='Edit segment':action==='other-editor'?n.title==='Edit this note':n.spec.startsWith('button')&&n.textContent==='Delete segment');
    await control.onclick();await settle();
    assert.equal(f.field('segment')?.value,'Keep this draft',action);
    assert.ok(f.x.toasts.some(([message])=>/save.*cancel/i.test(message)));
    assert.equal(f.x.calls.some(([,options])=>options.method==='DELETE'),false);
  }
});
test('Daily Save freezes its fields and Cancel until success or failure',async()=>{
  for(const kind of ['segment','observation']) {
    for(const fail of [false,true]) {
      const f=await dailyFixture(),pending=defer();f.open(kind);f.field(kind).value='Submitted';
      f.x.setRequest((path,options)=>options.method==='PATCH'?pending.promise:f.request(path,options));
      const editor=f.field(kind),cancel=walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Cancel');
      const saving=walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();await settle();
      assert.equal(editor.disabled,true);assert.equal(cancel.disabled,true);
      if(fail) {
        pending.reject(Error('offline'));await assert.rejects(saving);await settle();
        assert.equal(f.field(kind),editor);assert.equal(editor.disabled,false);assert.equal(cancel.disabled,false);assert.equal(editor.value,'Submitted');
      } else {
        const target=kind==='segment'?f.resource.segments[0]:f.resource.segments[0].observations[0];
        target[kind==='segment'?'title':'seen']='Submitted';
        pending.resolve(structuredClone(target));await saving;await settle();
        assert.equal(f.field(kind),undefined);assert.match(f.p.root.textContent,/Submitted/);
      }
    }
  }
});
test('successful exclusion Add keeps newer pending input and clears only an unchanged submission',async()=>{
  const x=await setup(),pending=defer();let wait=true;
  let privacy={paused:false,exclude_apps:[],exclude_titles:[],screenshots_stored:'none'};
  x.setRequest((path,options)=>{
    if(path==='/api/privacy') {
      if(options.method==='PUT') {
        const body=JSON.parse(options.body);privacy={...privacy,paused:body.paused,exclude_apps:body.exclude_apps,exclude_titles:body.exclude_titles};
        if(wait) {wait=false;return pending.promise;}
      }
      return structuredClone(privacy);
    }
    if(path==='/api/status')return {gateway:{},music:{},capture:'running'};
    return {totals:[]};
  });
  const p=await x.page('settings');
  let form=walk(p.root).find(n=>n.spec==='form.excl-form'),input=form.querySelector('input');
  input.value='Code';form.onsubmit({preventDefault(){}});await settle();
  assert.ok(x.calls.some(([path,options])=>path==='/api/privacy'&&options.method==='PUT'));
  input.value='Budget [Q4]';form.querySelector('select').value='title';input.focus();input.setSelectionRange(3,8);
  pending.resolve(structuredClone(privacy));await settle();
  form=walk(p.root).find(n=>n.spec==='form.excl-form');input=form.querySelector('input');
  assert.equal(input.value,'Budget [Q4]');assert.equal(form.querySelector('select').value,'title');
  assert.equal(p.root.document.activeElement,input);assert.equal(input.selectionStart,3);
  assert.deepEqual(privacy.exclude_apps,['Code']);
  form.onsubmit({preventDefault(){}});await settle();
  assert.equal(privacy.exclude_titles.length,1);
  assert.equal(walk(p.root).find(n=>n.spec==='form.excl-form').querySelector('input').value,'');
});


for(const kind of ['segment','observation']) {
  for(const fail of [false,true]) test('older Daily '+(fail?'error':'response')+' cannot replace the newer post-save '+kind+' view',async()=>{
    const f=await dailyFixture(),pending=defer();let gets=0;
    const old=structuredClone(f.resource);
    f.x.setRequest((path,options)=>path.startsWith('/api/timeline')&&++gets===1?pending.promise:f.request(path,options));
    f.p.hooks.onEvent();await settle();f.open(kind);f.field(kind).value='Saved latest';
    await walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();await settle();
    assert.match(f.p.root.textContent,/Saved latest/);
    const notices=f.x.toasts.length;
    if(fail) pending.reject(Error('Old request failed'));
    else pending.resolve(old);
    await settle();
    assert.match(f.p.root.textContent,/Saved latest/);
    assert.equal(f.x.toasts.length,notices);
  });
}


for(const kind of ['segment','observation']) {
  test('Daily '+kind+' Save owns its editor until the post-save GET completes',async()=>{
    const f=await dailyFixture(),pending=defer();f.open(kind);f.field(kind).value='Saved latest';
    f.x.setRequest((path,options)=>path.startsWith('/api/timeline')?pending.promise:f.request(path,options));
    const editor=f.field(kind);
    const saving=walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();await settle();
    assert.equal(editor.disabled,true);
    f.open('segment');await settle();
    assert.equal(f.field(kind),editor);
    assert.ok(f.x.toasts.some(([message])=>/save.*cancel/i.test(message)));
    pending.resolve(structuredClone(f.resource));await saving;await settle();
    assert.equal(f.field(kind),undefined);assert.match(f.p.root.textContent,/Saved latest/);
    f.open(kind);assert.equal(f.field(kind).value,'Saved latest');
  });
  test('Daily '+kind+' Save retains its confirmed record when the post-save GET fails',async()=>{
    const f=await dailyFixture(),pending=defer(),older=defer();let gets=0;
    const old=structuredClone(f.resource);
    f.x.setRequest((path,options)=>path.startsWith('/api/timeline')?(++gets===1?older.promise:pending.promise):f.request(path,options));
    f.p.hooks.onEvent();await settle();f.open(kind);f.field(kind).value='Saved latest';
    const saving=walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();await settle();
    assert.equal(gets,2);older.resolve(old);await settle();
    pending.reject(Error('Refresh offline'));await saving;await settle();
    assert.equal(f.field(kind),undefined);assert.match(f.p.root.textContent,/Saved latest/);
    assert.ok(f.x.toasts.some(([message])=>/refresh/i.test(message)));
    f.open(kind);assert.equal(f.field(kind).value,'Saved latest');
    const secondary=walk(f.p.root).find(n=>n['aria-label']===(kind==='segment'?'Intent':'Captured on-screen text'));
    secondary.value='Later detail';f.x.setRequest(f.request);
    await walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();await settle();
    const target=kind==='segment'?f.resource.segments[0]:f.resource.segments[0].observations[0];
    assert.equal(target[kind==='segment'?'title':'seen'],'Saved latest');
    assert.equal(target[kind==='segment'?'intent':'visible_text'],'Later detail');
  });
}


for(const operation of ['forget','edit']) {
  for(const fail of [false,true]) test('Contents ignores an older '+(fail?'error':'response')+' after '+operation,async()=>{
    const x=await setup(),pending=defer();let gets=0;
    let timeline={segments:[{id:'s1',title:'Deleted private title',kind:'coding',active:60,started_at:'2026-10-11T08:00:00Z'}],capture_spans:[]};
    const old=structuredClone(timeline);
    x.setRequest((path,options)=>{
      if(path.startsWith('/api/timeline'))return structuredClone(timeline);
      if(path==='/api/forget-all') {timeline={segments:[],capture_spans:[]};return {ok:true};}
      if(path==='/api/segments/s1') {Object.assign(timeline.segments[0],JSON.parse(options.body));return structuredClone(timeline.segments[0]);}
      if(path==='/api/skills')return {entries:[]};
      if(path==='/api/status')return {capture:'running',gateway:{reachable:true,calls_per_minute:0}};
      return {totals:[]};
    });
    const p=await x.page('contents'),normal=x.calls.length;
    const handler=(path,options)=>{
      if(path.startsWith('/api/timeline'))return ++gets===1?pending.promise:structuredClone(timeline);
      if(path==='/api/forget-all') {timeline={segments:[],capture_spans:[]};return {ok:true};}
      if(path==='/api/segments/s1') {Object.assign(timeline.segments[0],JSON.parse(options.body));return structuredClone(timeline.segments[0]);}
      if(path==='/api/skills')return {entries:[]};
      if(path==='/api/status')return {capture:'running',gateway:{reachable:true,calls_per_minute:0}};
      return {totals:[]};
    };
    x.setRequest(handler);const older=p.hooks.onEvent();await settle();
    if(operation==='forget')await x.api.forgetAll();
    else await x.api.editSegment('s1',{title:'Updated wording'});
    await p.hooks.onEvent();
    assert.doesNotMatch(p.root.textContent,/Deleted private title/);
    if(fail)pending.reject(Error('Old request failed'));else pending.resolve(old);
    await older;await settle();
    assert.doesNotMatch(p.root.textContent,/Deleted private title|isn.t responding/);
    assert.match(p.root.textContent,operation==='forget'?/No notes yet/:/Updated wording/);
    assert.ok(x.calls.length>normal);
  });
}
async function standaloneDemo() {
  const context=vm.createContext({Date,URL,setInterval:()=>{},setTimeout:fn=>fn()});
  const module=new vm.SourceTextModule(await readFile('src/daybook/web/static/js/demo.js','utf8'),{context});
  await module.link(()=>{throw Error('unexpected import');});await module.evaluate();
  return module.namespace.handle;
}
for(const range of ['inside','boundaries','empty']) test('demo Forget '+range+' range matches live overlap and summary removal',async()=>{
  const call=await standaloneDemo(),initial=await call('GET','/api/timeline');
  const notes=initial.segments.flatMap(s=>s.observations),first=notes[0],second=notes[1];
  const inside=new Date(new Date(first.started_at).getTime()+60000).toISOString();
  const from=range==='boundaries'?first.last_seen_at:inside;
  const to=range==='boundaries'?second.last_seen_at:range==='empty'?inside:new Date(new Date(inside).getTime()+60000).toISOString();
  await call('POST','/api/forget',{from,to});
  const current=await call('GET','/api/timeline'),remaining=current.segments.flatMap(s=>s.observations);
  const removed=range==='empty'?[]:[range==='inside'?first.id:second.id];
  assert.deepEqual(Array.from(remaining,o=>o.id),Array.from(notes.filter(o=>!removed.includes(o.id)),o=>o.id));
  const segment=current.segments.find(s=>s.id===initial.segments[0].id);
  if(range==='empty')assert.equal(segment.title,initial.segments[0].title);
  else {assert.equal(segment.title,'(summary removed after forget)');assert.equal(segment.intent,'(summary removed after forget)');assert.equal(segment.kind,'other');}
  assert.equal(JSON.stringify(current.capture_spans),JSON.stringify(initial.capture_spans));
});
test('demo note deletion clears affected inferred summaries and preserves user wording',async()=>{
  for(const authored of ['model','user']) {
    const call=await standaloneDemo(),initial=(await call('GET','/api/timeline')).segments;
    const target=initial.find(s=>s.authored===authored&&s.observations.length>1);
    await call('DELETE','/api/observations/'+target.observations[0].id);
    const current=(await call('GET','/api/timeline')).segments.find(s=>s.id===target.id);
    assert.equal(current.observations.length,target.observations.length-1);
    assert.equal(current.title,authored==='model'?'(summary removed after forget)':target.title);
    assert.equal(current.intent,authored==='model'?'(summary removed after forget)':target.intent);
    assert.equal(current.kind,authored==='model'?'other':target.kind);
  }
});
for(const stage of ['PUT','GET']) test('Skill Save preserves a newer reversion to the original text during '+stage,async()=>{
  const x=await setup({developer:true}),pending=defer();let first=true,waitingGet=false;
  let skill={id:'stand',name:'Stand',goal:'Stand.',status:'needs_review',origin:'user',hash:'old',text:'original version',signals:[],allow:[],rules:[],errors:[]};
  x.setRequest((path,options)=>{
    if(path==='/api/skills/stand'&&options.method==='PUT') {
      skill={...skill,text:JSON.parse(options.body).text,hash:first?'saved-first':'saved-second'};
      if(first) {first=false;if(stage==='PUT')return pending.promise;waitingGet=true;}
      return structuredClone(skill);
    }
    if(path==='/api/skills') {
      if(waitingGet) {waitingGet=false;return pending.promise;}
      return {entries:[structuredClone(skill)],catalog:[]};
    }
    return {errors:[]};
  });
  const p=await x.page('skills','id=stand'),editor=walk(p.root).find(n=>n.spec.startsWith('textarea'));
  editor.value='submitted version';editor.oninput();
  let save=walk(p.root).find(n=>n.textContent==='Save edits');
  const saving=save.onclick({currentTarget:save});await settle();
  editor.value='original version';editor.oninput();editor.focus();editor.setSelectionRange(2,6);
  pending.resolve(stage==='PUT'?structuredClone(skill):{entries:[structuredClone(skill)],catalog:[]});await saving;await settle();
  const current=walk(p.root).find(n=>n.spec.startsWith('textarea'));
  assert.equal(current.value,'original version');assert.equal(p.root.document.activeElement,current);assert.equal(current.selectionStart,2);
  assert.equal(walk(p.root).find(n=>n.textContent==='Approve & enable').disabled,true);
  save=walk(p.root).find(n=>n.textContent==='Save edits');await save.onclick({currentTarget:save});await settle();
  assert.equal(skill.text,'original version');assert.equal(skill.hash,'saved-second');
  assert.ok(!walk(p.root).find(n=>n.textContent==='Approve & enable').disabled);
});


for(const kind of ['segment','observation']) {
  for(const fail of [false,true]) test('Daily '+kind+' Cancel never restores forgotten notes when fresh GET '+(fail?'fails':'completes'),async()=>{
    const f=await dailyFixture(),older=defer(),fresh=defer();let gets=0;
    const old=structuredClone(f.resource);
    f.x.setRequest((path,options)=>{
      if(path.startsWith('/api/timeline'))return ++gets===1?older.promise:fresh.promise;
      if(path==='/api/forget-all') {f.resource.segments=[];return {ok:true};}
      return f.request(path,options);
    });
    f.p.hooks.onEvent();await settle();f.open(kind);f.field(kind).value='User draft';
    await f.x.api.forgetAll();f.p.hooks.onEvent();await settle();
    const cancel=walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Cancel').onclick();
    await settle();assert.equal(gets,2);
    assert.doesNotMatch(f.p.root.textContent,/Original title|Original note/);
    older.resolve(old);await settle();
    assert.doesNotMatch(f.p.root.textContent,/Original title|Original note/);
    if(fail)fresh.reject(Error('Fresh GET unavailable'));else fresh.resolve(structuredClone(f.resource));
    await cancel;await settle();
    assert.doesNotMatch(f.p.root.textContent,/Original title|Original note/);
    assert.match(f.p.root.textContent,fail?/isn.t responding/:/No notes yet/);
  });
}
for(const fail of [false,true]) test('Index totals ignore older '+(fail?'errors':'responses')+' after Forget',async()=>{
  const x=await setup(),pending={kind:defer(),app:defer()};let forgotten=false;
  const old={kind:[{label:'coding',active:240}],app:[{label:'Private Code',active:240}]};
  x.setRequest(path=>path.startsWith('/api/totals')?{totals:path.includes('by=app')?old.app:old.kind}:{ok:true});
  const p=await x.page('tags'),gets={kind:0,app:0};
  x.setRequest((path,options)=>{
    if(path.startsWith('/api/totals')) {
      const by=path.includes('by=app')?'app':'kind';
      return ++gets[by]===1?pending[by].promise:{totals:forgotten?[]:old[by]};
    }
    if(path==='/api/forget-all') {forgotten=true;return {ok:true};}
    return {};
  });
  const older=p.hooks.onEvent();await settle();await x.api.forgetAll();await settle();
  assert.match(p.root.textContent,/Nothing indexed yet/);
  for(const by of ['kind','app']) {
    if(fail)pending[by].reject(Error('Old totals unavailable'));else pending[by].resolve({totals:old[by]});
  }
  await older;await settle();
  assert.doesNotMatch(p.root.textContent,/coding|Private Code|isn.t responding/);
  assert.match(p.root.textContent,/Nothing indexed yet/);p.hooks.cleanup();
});


for(const mutation of ['local','external']) {
  for(const fail of [false,true]) test('Settings ignores an old '+(fail?'error':'response')+' after '+mutation+' privacy removal',async()=>{
    const x=await setup(),pending=defer();let hold=false,held=false;
    let privacy={paused:false,exclude_apps:['KeePassXC'],exclude_titles:[],screenshots_stored:'none'};
    const old=structuredClone(privacy);
    x.setRequest((path,options)=>{
      if(path==='/api/privacy') {
        if(options.method==='GET') {if(hold&&!held){held=true;return pending.promise;}return structuredClone(privacy);}
        const body=JSON.parse(options.body);
        privacy={...privacy,paused:body.paused,exclude_apps:body.exclude_apps,exclude_titles:body.exclude_titles};
        return structuredClone(privacy);
      }
      if(path==='/api/status')return {gateway:{},music:{},capture:privacy.paused?'paused':'running'};
      return {totals:[]};
    });
    const p=await x.page('settings');hold=true;
    const older=p.hooks.onEvent(new Set(['Reconnected']));await settle();
    if(mutation==='local') {
      const remove=walk(p.root).find(n=>n.title==='Stop blocking KeePassXC');
      await remove.onclick({currentTarget:remove});
    } else {
      await x.api.setPrivacy({paused:false,exclude_apps:[],exclude_titles:[]});
      await p.hooks.onEvent(new Set(['StatusChanged']),[{type:'StatusChanged',component:'sense',status:'privacy_changed'}]);
    }
    assert.doesNotMatch(p.root.textContent,/KeePassXC/);
    const form=walk(p.root).find(n=>n.spec==='form.excl-form'),input=form.querySelector('input');
    input.value='Pending title';form.querySelector('select').value='title';input.focus();input.setSelectionRange(2,5);
    const notices=x.toasts.length;
    if(fail)pending.reject(Error('Older privacy failed'));else pending.resolve(old);
    await older;await settle();
    assert.doesNotMatch(p.root.textContent,/KeePassXC/);
    assert.equal(x.toasts.length,notices);
    assert.equal(walk(p.root).find(n=>n.spec==='form.excl-form').querySelector('input'),input);
    assert.equal(input.value,'Pending title');assert.equal(input.selectionStart,2);
  });
}
for(const mutation of ['delete','save','approve']) {
  for(const fail of [false,true]) test('Skills ignores an old '+(fail?'error':'response')+' after '+mutation,async()=>{
    const x=await setup({developer:mutation==='save'}),pending=defer();let hold=false,held=false;
    let entries=[{id:'stand',name:'Stand',goal:'Stand.',status:'needs_review',origin:'user',hash:'old',text:'original',signals:[],allow:[],rules:[],errors:[]}];
    const old={entries:structuredClone(entries),catalog:[]};
    x.setRequest((path,options)=>{
      if(path==='/api/skills') {if(hold&&!held){held=true;return pending.promise;}return {entries:structuredClone(entries),catalog:[]};}
      if(path==='/api/skills/stand'&&options.method==='DELETE') {entries=[];return {ok:true};}
      if(path==='/api/skills/stand'&&options.method==='PUT') {entries[0]={...entries[0],text:JSON.parse(options.body).text,hash:'saved'};return structuredClone(entries[0]);}
      if(path.endsWith('/approve')) {entries[0]={...entries[0],status:'active'};return structuredClone(entries[0]);}
      return {errors:[]};
    });
    const p=await x.page('skills','id=stand');hold=true;p.hooks.onEvent(new Set(['SkillsChanged']));await settle();
    if(mutation==='save') {const editor=walk(p.root).find(n=>n.spec.startsWith('textarea'));editor.value='Saved current';editor.oninput();}
    const action=walk(p.root).find(n=>n.textContent===(mutation==='delete'?'Delete':mutation==='save'?'Save edits':'Approve & enable'));
    await action.onclick({currentTarget:action});await settle();
    const check=()=>{
      assert.doesNotMatch(p.root.textContent,/isn.t responding/);
      if(mutation==='delete') {assert.match(p.root.textContent,/No skills yet/);assert.doesNotMatch(p.root.textContent,/Stand\./);}
      else if(mutation==='save')assert.equal(walk(p.root).find(n=>n.spec.startsWith('textarea')).value,'Saved current');
      else assert.ok(walk(p.root).some(n=>n.textContent==='Active'));
    };
    check();if(fail)pending.reject(Error('Older skill GET failed'));else pending.resolve(old);
    await settle();check();
  });
}
for(const fail of [false,true]) test('global footer ignores older '+(fail?'errors':'responses')+' after public pause',async()=>{
  const x=await setup(),pending=defer();let paused=false,hold=false,held=false;
  const status=()=>({capture:paused?'paused':'running',gateway:{model:'test-model',reachable:true,calls_per_minute:0},music:{}});
  const old=status();
  x.setRequest((path,options)=>{
    if(path==='/api/status') {if(hold&&!held){held=true;return pending.promise;}return status();}
    if(path==='/api/skills')return {entries:[],catalog:[]};
    if(path==='/api/privacy') {if(options.method==='PUT')paused=JSON.parse(options.body).paused;return {paused,exclude_apps:[],exclude_titles:[]};}
    return {segments:[],capture_spans:[]};
  });
  const app=await x.app();hold=true;const older=app.refresh();await settle();
  await x.api.setPrivacy({paused:true,exclude_apps:[],exclude_titles:[]});
  x.streams[0].listeners.CaptureStateChanged({data:JSON.stringify({state:'paused'})});await settle();
  assert.match(app.footer.textContent,/Paused/);assert.equal(app.pip.dataset.state,'paused');
  if(fail)pending.reject(Error('Older status failed'));else pending.resolve(old);
  await older;await settle();
  assert.match(app.footer.textContent,/Paused/);assert.doesNotMatch(app.footer.textContent,/Recording|Status unavailable/);
  assert.equal(app.pip.dataset.state,'paused');
});


test('Skills late Save refresh cannot restore the card after public Delete',async()=>{
  const x=await setup({developer:true}),pending=defer();let hold=false,held=false;
  let entries=[{id:'stand',name:'Stand',goal:'Stand.',status:'needs_review',origin:'user',hash:'old',text:'original',signals:[],allow:[],rules:[],errors:[]}];
  let saved;
  x.setRequest((path,options)=>{
    if(path==='/api/skills') {
      if(hold&&!held){held=true;saved={entries:structuredClone(entries),catalog:[]};return pending.promise;}
      return {entries:structuredClone(entries),catalog:[]};
    }
    if(path==='/api/skills/stand'&&options.method==='PUT') {entries[0]={...entries[0],text:JSON.parse(options.body).text,hash:'saved'};return structuredClone(entries[0]);}
    if(path==='/api/skills/stand'&&options.method==='DELETE') {entries=[];return {ok:true};}
    return {errors:[]};
  });
  const p=await x.page('skills','id=stand'),editor=walk(p.root).find(n=>n.spec.startsWith('textarea'));
  editor.value='Saved current';editor.oninput();hold=true;
  const save=walk(p.root).find(n=>n.textContent==='Save edits'),saving=save.onclick({currentTarget:save});await settle();
  const remove=walk(p.root).find(n=>n.textContent==='Delete');await remove.onclick({currentTarget:remove});await settle();
  assert.match(p.root.textContent,/No skills yet/);
  pending.resolve(saved);await saving;await settle();
  assert.match(p.root.textContent,/No skills yet/);assert.doesNotMatch(p.root.textContent,/Stand\.|Review card/);
});


test('Settings delayed own PUT acknowledgement cannot replace a newer external privacy update',async()=>{
  const x=await setup(),pending=defer();let first=true,ack;
  let privacy={paused:false,exclude_apps:['Code'],exclude_titles:[],screenshots_stored:'none'};
  x.setRequest((path,options)=>{
    if(path==='/api/privacy') {
      if(options.method==='GET')return structuredClone(privacy);
      const body=JSON.parse(options.body);
      privacy={...privacy,paused:body.paused,exclude_apps:body.exclude_apps,exclude_titles:body.exclude_titles};
      if(first) {first=false;ack=structuredClone(privacy);return pending.promise;}
      return structuredClone(privacy);
    }
    if(path==='/api/status')return {gateway:{},music:{},capture:privacy.paused?'paused':'running'};
    return {totals:[]};
  });
  const p=await x.page('settings'),monitoring=walk(p.root).find(n=>n['aria-label']==='Monitoring');
  monitoring.checked=false;const saving=monitoring.onchange();await settle();
  assert.equal(privacy.paused,true);
  await x.api.setPrivacy({...privacy,exclude_apps:['Code','Firefox']},privacy);
  await p.hooks.onEvent(new Set(['StatusChanged']),[{type:'StatusChanged',component:'sense',status:'privacy_changed'}]);
  assert.match(p.root.textContent,/Firefox/);
  const form=walk(p.root).find(n=>n.spec==='form.excl-form'),input=form.querySelector('input');
  input.value='Next private title';form.querySelector('select').value='title';input.focus();input.setSelectionRange(2,8);
  pending.resolve(ack);await saving;await settle();
  assert.match(p.root.textContent,/Firefox/);assert.equal(privacy.exclude_apps.length,2);
  const current=walk(p.root).find(n=>n.spec==='form.excl-form');
  assert.equal(current.querySelector('input').value,'Next private title');assert.equal(current.querySelector('select').value,'title');
  assert.equal(p.root.document.activeElement,current.querySelector('input'));assert.equal(current.querySelector('input').selectionStart,2);
});


test('Daily running capture without segments shows the empty state',async()=>{
  const x=await setup();
  x.setRequest(()=>({segments:[],capture_spans:[{state:'running',start:'2026-10-11T08:00:00Z',end:null}]}));
  const p=await x.page('daily');
  assert.match(p.root.textContent,/No notes yet/);
  assert.match(p.root.textContent,/0 segments/);
  assert.equal(walk(p.root).some(n=>n.spec==='div.leaf'),false);
});
test('Daily keeps renderable privacy gaps beside a running span without segments',async()=>{
  for(const [state,label] of [['paused','Paused'],['locked','Locked'],['away','Away'],['withheld','Private'],['overlay','Overlay']]) {
    const x=await setup();
    x.setRequest(()=>({segments:[],capture_spans:[
      {state:'running',start:'2026-10-11T08:00:00Z',end:'2026-10-11T08:01:00Z'},
      {state,start:'2026-10-11T08:01:00Z',end:null},
    ]}));
    const p=await x.page('daily');
    assert.match(p.root.textContent,new RegExp(label));
    assert.doesNotMatch(p.root.textContent,/No notes yet/);
    assert.equal(walk(p.root).filter(n=>n.spec==='div.entry.gap').length,1);
  }
});
for(const stage of ['debouncing','checked']) {
  for(const fail of [false,true]) test('Skills stale parse '+(fail?'error':'success')+' cannot replace newer '+stage+' input',async()=>{
    const x=await setup({developer:true,manualTimers:true}),old=defer();
    const skill={id:'stand',name:'Stand',goal:'Stand.',status:'needs_review',origin:'user',hash:'old',text:'server version',signals:[],allow:[],rules:[],errors:[{location:'initial',message:'Existing check'}]};
    const submitted=[];
    x.setRequest((path,options)=>{
      if(path==='/api/skills')return {entries:[skill],catalog:[]};
      if(path==='/api/skills/parse') {
        const text=JSON.parse(options.body).text;submitted.push(text);
        return text==='older edit'?old.promise:{errors:[{location:'new',message:'Current text error'}]};
      }
      return {};
    });
    const p=await x.page('skills','id=stand');
    const editor=walk(p.root).find(n=>n['aria-label']==='Skill file');
    const errors=walk(p.root).find(n=>n.spec==='div.errors');
    editor.value='older edit';editor.oninput();const checking=x.runNextTimer();await settle();
    assert.deepEqual(submitted,['older edit']);
    editor.value='newer edit';editor.oninput();
    if(stage==='checked')await x.runNextTimer();
    if(fail)old.reject(Error('Old transport failure'));
    else old.resolve({errors:[]});
    await checking;await settle();
    assert.equal(editor.value,'newer edit');
    assert.match(errors.textContent,stage==='checked'?/Current text error/:/Existing check/);
    assert.doesNotMatch(errors.textContent,/file parses|Old transport failure/);
    if(stage==='debouncing')await x.runNextTimer();
    assert.deepEqual(submitted,['older edit','newer edit']);
    assert.match(errors.textContent,/Current text error/);
    assert.equal(walk(p.root).find(n=>n.textContent==='Approve & enable').disabled,true);
  });
}


const notesInvalidated = {type:'StatusChanged',component:'journal',status:'notes_invalidated'};
const deliverNotesInvalidated = p => p.hooks.onEvent(new Set(['StatusChanged']),[notesInvalidated]);

for(const kind of ['segment','observation']) {
  for(const fail of [false,true]) test('Daily explicit invalidation clears a '+kind+' draft even when its fresh GET '+(fail?'fails':'succeeds'),async()=>{
    const f=await dailyFixture(),older=defer(),fresh=defer(),old=structuredClone(f.resource);
    let gets=0;
    f.x.setRequest((path,options)=>{
      if(path==='/api/forget-all'){f.resource.segments=[];return {ok:true};}
      if(path.startsWith('/api/timeline'))return ++gets===1?older.promise:fresh.promise;
      return f.request(path,options);
    });
    f.p.hooks.onEvent();await settle();f.open(kind);
    f.field(kind).value='Private unsaved draft';
    await f.x.api.forgetAll();deliverNotesInvalidated(f.p);
    assert.equal(f.field(kind),undefined);
    assert.doesNotMatch(f.p.root.textContent,/Original title|Original note|Original screen text/);
    assert.match(f.p.root.textContent,/loading/);
    assert.ok(f.x.toasts.some(([message])=>/notes changed/i.test(message)));
    older.resolve(old);await settle();
    assert.doesNotMatch(f.p.root.textContent,/Original title|Original note/);
    if(fail)fresh.reject(Error('offline'));else fresh.resolve(structuredClone(f.resource));
    await settle();
    assert.equal(gets,2);assert.equal(f.field(kind),undefined);
    assert.doesNotMatch(f.p.root.textContent,/Original title|Original note|Original screen text/);
    if(!fail)assert.match(f.p.root.textContent,/No notes yet/);
  });
  test('Daily ordinary updates preserve a '+kind+' editor and selection',async()=>{
    const f=await dailyFixture();f.open(kind);
    const editor=f.field(kind);editor.value='Keep this draft';editor.focus();editor.setSelectionRange(2,6);
    const before=f.x.calls.length;
    for(const event of [
      {type:'CaptureStateChanged',state:'running'}, {type:'TickRecorded'}, {type:'SegmentChanged'},
      {type:'StatusChanged',component:'sense',status:'privacy_changed'},
    ])f.p.hooks.onEvent(new Set([event.type]),[event]);
    await settle();
    assert.equal(f.field(kind),editor);assert.equal(editor.value,'Keep this draft');
    assert.equal(editor.selectionStart,2);assert.equal(editor.selectionEnd,6);
    assert.equal(f.x.calls.length,before);
  });
  for(const stage of ['PATCH','post-save GET'])test('Daily forgotten '+kind+' cannot return from a delayed '+stage+' Save continuation',async()=>{
    const f=await dailyFixture(),pending=defer();f.open(kind);f.field(kind).value='Private submitted wording';
    let saved,held=false;
    f.x.setRequest((path,options)=>{
      if(path==='/api/forget-all'){f.resource.segments=[];return {ok:true};}
      if(options.method==='PATCH'){
        saved=f.request(path,options);return stage==='PATCH'?pending.promise:saved;
      }
      if(path.startsWith('/api/timeline')&&stage==='post-save GET'&&!held){held=true;return pending.promise;}
      return f.request(path,options);
    });
    const saving=walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();
    await settle();assert.ok(saved);
    const old=structuredClone(f.resource);
    await f.x.api.forgetAll();deliverNotesInvalidated(f.p);await settle();
    assert.equal(f.field(kind),undefined);
    assert.doesNotMatch(f.p.root.textContent,/Private submitted wording|Original title|Original note/);
    pending.resolve(stage==='PATCH'?saved:old);await saving;await settle();
    assert.match(f.p.root.textContent,/No notes yet/);
    assert.doesNotMatch(f.p.root.textContent,/Private submitted wording|Original title|Original note/);
  });
  test('Daily '+kind+' Save clears a deleted editor on 404 even after a missed event',async()=>{
    const f=await dailyFixture();f.open(kind);f.field(kind).value='Private failed draft';
    f.x.setRequest((path,options)=>{
      if(path==='/api/forget-all'){f.resource.segments=[];return {ok:true};}
      if(options.method==='PATCH')return new Response(JSON.stringify({detail:'not_found'}),{status:404});
      return f.request(path,options);
    });
    await f.x.api.forgetAll();
    await assert.rejects(walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick(),err=>err.status===404);
    await settle();
    assert.equal(f.field(kind),undefined);assert.match(f.p.root.textContent,/No notes yet/);
    assert.doesNotMatch(f.p.root.textContent,/Original title|Original note|Original screen text/);
  });
}

for(const stage of ['boot','navigation'])test('app replays privacy events received during '+stage+' Settings rendering',async()=>{
  const x=await setup({manualTimers:true}),pending=defer();
  let privacy={paused:false,exclude_apps:['PrivateApp'],exclude_titles:[]},hold=false,held=false,privacyGets=0;
  const status={capture:'running',gateway:{model:'test',reachable:true,calls_per_minute:0},music:{}};
  x.setRequest((path,options)=>{
    if(path==='/api/privacy'){
      if(options.method==='PUT')privacy={...privacy,...JSON.parse(options.body)};
      else privacyGets++;
      return structuredClone(privacy);
    }
    if(path==='/api/status'){
      if(hold&&!held){held=true;return pending.promise;}
      return status;
    }
    if(path==='/api/skills')return {entries:[],catalog:[]};
    if(path.startsWith('/api/totals'))return {totals:[]};
    return {segments:[],capture_spans:[]};
  });
  let app,loading;
  if(stage==='boot'){
    // The footer owns the first status GET. Hold only Settings' own request.
    let statusGets=0;
    x.setRequest((path,options)=>{
      if(path==='/api/status'&&++statusGets===2){held=true;return pending.promise;}
      if(path==='/api/privacy'){
        if(options.method==='PUT')privacy={...privacy,...JSON.parse(options.body)};else privacyGets++;
        return structuredClone(privacy);
      }
      if(path==='/api/status')return status;
      if(path==='/api/skills')return {entries:[],catalog:[]};
      if(path.startsWith('/api/totals'))return {totals:[]};
      return {segments:[],capture_spans:[]};
    });
    app=await x.app({route:'settings',waitForBoot:false});loading=app.ready;await settle();
  }else{
    app=await x.app();hold=true;loading=app.navigate('settings');await settle();
  }
  assert.equal(held,true);assert.match(app.root.textContent,/loading/);
  assert.ok(x.streams.length,'live subscription must exist during initial rendering');
  await x.api.setPrivacy({paused:false,exclude_apps:[],exclude_titles:[]});
  x.streams[0].listeners.StatusChanged({data:JSON.stringify({component:'sense',status:'privacy_changed'})});
  x.flushEvents();await settle();
  const before=privacyGets;
  pending.resolve(status);await loading;await settle();x.flushEvents();await settle();
  assert.ok(privacyGets>before,'Settings must reread privacy after its queued event');
  assert.doesNotMatch(app.root.textContent,/PrivateApp/);
});

test('app abandons a loading navigation and its queued events without refreshing the new page',async()=>{
  const x=await setup({manualTimers:true}),pending=defer();
  let hold=false,held=false,timelineGets=0;
  const status={capture:'running',gateway:{model:'test',reachable:true,calls_per_minute:0},music:{}};
  x.setRequest(path=>{
    if(path==='/api/status'){if(hold&&!held){held=true;return pending.promise;}return status;}
    if(path==='/api/privacy')return {paused:false,exclude_apps:['AbandonedApp'],exclude_titles:[]};
    if(path==='/api/skills')return {entries:[],catalog:[]};
    if(path.startsWith('/api/totals'))return {totals:[]};
    if(path.startsWith('/api/timeline'))timelineGets++;
    return {segments:[],capture_spans:[]};
  });
  const app=await x.app();hold=true;
  const abandoned=app.navigate('settings');await settle();assert.equal(held,true);
  x.streams[0].listeners.StatusChanged({data:JSON.stringify({component:'sense',status:'privacy_changed'})});
  await app.navigate('daily');await settle();const before=timelineGets;
  x.flushEvents();await settle();
  assert.equal(timelineGets,before,'the abandoned page batch cannot refresh the new page');
  pending.resolve(status);await abandoned;await settle();
  assert.equal(timelineGets,before);assert.match(app.root.textContent,/No notes yet/);
  assert.doesNotMatch(app.root.textContent,/AbandonedApp/);
});


test('Daily reconnect clears editors after a missed external deletion',async()=>{
  for(const kind of ['segment','observation']){
    const f=await dailyFixture();f.open(kind);f.field(kind).value='Private disconnected draft';
    f.x.setRequest((path,options)=>{
      if(path==='/api/forget-all'){f.resource.segments=[];return {ok:true};}
      return f.request(path,options);
    });
    await f.x.api.forgetAll();
    f.p.hooks.onEvent(new Set(['Reconnected']),[{type:'Reconnected'}]);
    assert.equal(f.field(kind),undefined);
    assert.doesNotMatch(f.p.root.textContent,/Original title|Original note|Original screen text/);
    await settle();assert.match(f.p.root.textContent,/No notes yet/);
  }
});


test('app first stream open revalidates deletions missed during or after its initial snapshot',async()=>{
  for(const stage of ['loading','rendered']){
    const x=await setup({manualTimers:true}),pending=defer();
    let held=false,timeline={segments:[{id:'s1',title:'Startup private title',intent:'Startup intent',kind:'coding',active:60,started_at:'2026-10-11T08:00:00Z',observations:[]}],capture_spans:[]};
    const old=structuredClone(timeline);
    x.setRequest((path,options)=>{
      if(path==='/api/status')return {capture:'running',gateway:{model:'test',reachable:true,calls_per_minute:0},music:{}};
      if(path==='/api/privacy')return {paused:false,exclude_apps:[],exclude_titles:[]};
      if(path==='/api/skills')return {entries:[],catalog:[]};
      if(path==='/api/forget-all'){timeline={segments:[],capture_spans:[]};return {ok:true};}
      if(path.startsWith('/api/timeline')){if(!held){held=true;return pending.promise;}return structuredClone(timeline);}
      return {};
    });
    const app=await x.app({waitForBoot:false});await settle();assert.equal(held,true);
    if(stage==='rendered'){
      pending.resolve(old);await app.ready;await settle();
      assert.match(app.root.textContent,/Startup private title/);
    }
    await x.api.forgetAll();
    x.streams[0].onopen();x.flushEvents();await settle();
    if(stage==='loading'){pending.resolve(old);await app.ready;await settle();}
    assert.match(app.root.textContent,/No notes yet/,stage);
    assert.doesNotMatch(app.root.textContent,/Startup private title/,stage);
    assert.equal(x.calls.filter(([path])=>path.startsWith('/api/timeline')).length,2);
  }
});

test('Daily own Save still renders saved wording when its invalidation precedes the ACK or followup GET',async()=>{
  for(const kind of ['segment','observation'])for(const stage of ['PATCH','post-save GET']){
    const f=await dailyFixture(),pending=defer();f.open(kind);f.field(kind).value='My saved wording';
    let saved,held=false;
    f.x.setRequest((path,options)=>{
      if(options.method==='PATCH'){
        saved=f.request(path,options);return stage==='PATCH'?pending.promise:saved;
      }
      if(path.startsWith('/api/timeline')&&stage==='post-save GET'&&!held){held=true;return pending.promise;}
      return f.request(path,options);
    });
    const saving=walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();
    await settle();deliverNotesInvalidated(f.p);await settle();
    pending.resolve(stage==='PATCH'?saved:structuredClone(f.resource));await saving;await settle();
    assert.equal(f.field(kind),undefined);assert.match(f.p.root.textContent,/My saved wording/);
    f.open(kind);assert.equal(f.field(kind).value,'My saved wording');
  }
});


const dailySecondary = (f,kind) => walk(f.p.root).find(n=>n['aria-label']===(kind==='segment'?'Intent':'Captured on-screen text'));
const editedDailyRecord = (f,kind) => kind==='segment'?f.resource.segments[0]:f.resource.segments[0].observations[0];

for(const kind of ['segment','observation']){
  for(const event of ['unrelated mutation','reconnect'])test('Daily verifies and restores an unchanged '+kind+' draft after '+event,async()=>{
    const f=await dailyFixture(),pending=defer();f.open(kind);
    const field=f.field(kind);field.value='My unfinished wording';
    dailySecondary(f,kind).value='My unfinished detail';field.focus();field.setSelectionRange(2,7);
    if(event==='unrelated mutation'){
      if(kind==='segment')await f.x.api.editObservation('o1',{seen:'A separately corrected observation'});
      else await f.x.api.editSegment('s1',{title:'A separately corrected segment'});
    }
    f.resource.segments[0].active=120;
    f.resource.segments[0].observations[0].last_seen_at='2026-10-11T08:02:00Z';
    f.x.setRequest(path=>path.startsWith('/api/timeline')?pending.promise:{});
    const verification=event==='reconnect'
      ? f.p.hooks.onEvent(new Set(['Reconnected']),[{type:'Reconnected'}])
      : deliverNotesInvalidated(f.p);
    assert.equal(f.field(kind),undefined);
    assert.doesNotMatch(f.p.root.textContent,/Original title|Original note|Original screen text/);
    pending.resolve(structuredClone(f.resource));await verification;await settle();
    const restored=f.field(kind);
    assert.equal(restored?.value,'My unfinished wording');
    assert.equal(dailySecondary(f,kind).value,'My unfinished detail');
    assert.equal(f.p.root.document.activeElement,restored);
    assert.equal(restored.selectionStart,2);assert.equal(restored.selectionEnd,7);
    assert.equal(restored.disabled,false);
    f.x.setRequest(f.request);
    await walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();await settle();
    const record=editedDailyRecord(f,kind);
    assert.equal(record[kind==='segment'?'title':'seen'],'My unfinished wording');
    assert.equal(record[kind==='segment'?'intent':'visible_text'],'My unfinished detail');
  });

  test('Daily changed '+kind+' source fields close its draft without restoring it',async()=>{
    for(const key of kind==='segment'?['title','intent']:['seen','visible_text']){
      const f=await dailyFixture(),pending=defer();f.open(kind);f.field(kind).value='Outdated private draft';
      const patch={[key]:'Current remote wording'};
      if(kind==='segment')await f.x.api.editSegment('s1',patch);else await f.x.api.editObservation('o1',patch);
      f.x.setRequest(path=>path.startsWith('/api/timeline')?pending.promise:{});
      const verification=deliverNotesInvalidated(f.p);assert.equal(f.field(kind),undefined);
      pending.resolve(structuredClone(f.resource));await verification;await settle();
      assert.equal(f.field(kind),undefined);assert.match(f.p.root.textContent,/Current remote wording/);
      assert.equal(walk(f.p.root).some(n=>n.value==='Outdated private draft'),false);
      f.open(kind);assert.notEqual(f.field(kind).value,'Outdated private draft');
    }
  });

  test('Daily failed '+kind+' verification discards its draft rather than restoring it on a later poll',async()=>{
    const f=await dailyFixture(),pending=defer();f.open(kind);f.field(kind).value='Unverified private draft';
    f.x.setRequest(path=>path.startsWith('/api/timeline')?pending.promise:{});
    const verification=deliverNotesInvalidated(f.p);assert.equal(f.field(kind),undefined);
    pending.reject(Error('verification offline'));await verification;await settle();
    assert.equal(f.field(kind),undefined);
    assert.doesNotMatch(f.p.root.textContent,/Original title|Original note|Original screen text/);
    f.x.setRequest(f.request);await f.p.hooks.onEvent();await settle();
    assert.equal(f.field(kind),undefined);
    f.open(kind);assert.notEqual(f.field(kind).value,'Unverified private draft');
  });

  test('Daily older '+kind+' verification cannot revive a draft after a later deletion',async()=>{
    for(const fail of [false,true]){
      const f=await dailyFixture(),older=defer(),fresh=defer(),old=structuredClone(f.resource);let gets=0;
      f.open(kind);f.field(kind).value='Deleted overlap draft';
      f.x.setRequest((path,options)=>{
        if(path==='/api/forget-all'){f.resource.segments=[];return {ok:true};}
        if(path.startsWith('/api/timeline'))return ++gets===1?older.promise:fresh.promise;
        return f.request(path,options);
      });
      const first=deliverNotesInvalidated(f.p);await settle();
      await f.x.api.forgetAll();const second=deliverNotesInvalidated(f.p);
      fresh.resolve(structuredClone(f.resource));await second;await settle();
      if(fail)older.reject(Error('older verification failed'));else older.resolve(old);
      await first;await settle();
      assert.equal(f.field(kind),undefined);assert.match(f.p.root.textContent,/No notes yet/);
      assert.doesNotMatch(f.p.root.textContent,/Original title|Original note|Original screen text/);
    }
  });

  test('Daily overlapping checks retain an unchanged '+kind+' draft and ignore a superseded response',async()=>{
    const f=await dailyFixture(),older=defer(),fresh=defer(),old=structuredClone(f.resource);let gets=0;
    f.open(kind);f.field(kind).value='Draft through two checks';
    f.x.setRequest(path=>path.startsWith('/api/timeline')?(++gets===1?older.promise:fresh.promise):{});
    const first=deliverNotesInvalidated(f.p),second=f.p.hooks.onEvent(new Set(['Reconnected']),[{type:'Reconnected'}]);
    fresh.resolve(structuredClone(f.resource));await second;await settle();
    assert.equal(f.field(kind)?.value,'Draft through two checks');
    f.field(kind).value='Newer resumed draft';
    const target=kind==='segment'?old.segments[0]:old.segments[0].observations[0];
    target[kind==='segment'?'title':'seen']='Superseded remote wording';
    older.resolve(old);await first;await settle();
    assert.equal(f.field(kind)?.value,'Newer resumed draft');
    assert.doesNotMatch(f.p.root.textContent,/Superseded remote wording/);
  });

  test('Daily failed in-flight '+kind+' Save cannot discard its revalidated draft',async()=>{
    const f=await dailyFixture(),pending=defer();f.open(kind);f.field(kind).value='Pending save draft';
    f.x.setRequest((path,options)=>options.method==='PATCH'?pending.promise:f.request(path,options));
    const saving=walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();await settle();
    await deliverNotesInvalidated(f.p);await settle();
    assert.equal(f.field(kind)?.value,'Pending save draft');
    assert.equal(f.field(kind).disabled,false);
    f.field(kind).value='Newer resumed wording';
    pending.reject(Error('old Save failed'));await saving;await settle();
    assert.equal(f.field(kind)?.value,'Newer resumed wording');
    f.x.setRequest(f.request);
    await walk(f.p.root).find(n=>n.spec.startsWith('button')&&n.textContent==='Save').onclick();await settle();
    assert.equal(editedDailyRecord(f,kind)[kind==='segment'?'title':'seen'],'Newer resumed wording');
  });
}
