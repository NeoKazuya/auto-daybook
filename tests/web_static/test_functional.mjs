import assert from 'node:assert/strict';
import { test } from 'node:test';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';

// DOM and transport are boundaries; API and page modules are the shipped source.
class Element {
  constructor(spec, attrs = {}, children = []) {
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
const defer = () => { let resolve; const promise = new Promise(r => resolve = r); return { promise, resolve }; };
const answer = { type: 'answer', text: 'You spent 4 minutes on journal tests', cites: ['s1'], window: { from: '2026-10-11T08:00:00Z', to: '2026-10-11T09:00:00Z' } };
const draft = { type: 'draft', summary: 'Stand after meetings', entry: { id: 'stand-after-meeting' } };
async function setup({ developer = false } = {}) {
  let request = async path => path.startsWith('/api/timeline') ? { segments: [{id:'s1', title:'Old title'}] } : {};
  const streams = [], channels = [], calls = [], toasts = [];
  const context = vm.createContext({
    URLSearchParams, Date, console, setTimeout, clearTimeout,
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
      const exports = path.endsWith('/prefs.js') ? {getPref:key=>key === 'developer' ? developer : false,setPref:()=>{}} : helpers;
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
  return {api:apiModule.namespace.api,subscribe:apiModule.namespace.subscribe,page,streams,channels,calls,toasts,setRequest:fn=>{request=fn;}};
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
    x.subscribe(()=>{}); const p=await x.page('board'); await p.ask('recall');
    const s=x.streams[0]; s.onopen();
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
