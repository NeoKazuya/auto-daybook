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
  }
  append(...x) { this.children.push(...x.flat(Infinity)); }
  replaceChildren(...x) { this.children = x.flat(Infinity).filter(x => x != null && x !== false); }
  get textContent() { return this.children.map(x => x instanceof Element ? x.textContent : String(x)).join(' '); }
  set textContent(x) { this.children = [x]; }
  focus() {}
  addEventListener(name, fn) { this['on' + name] = fn; }
  querySelector() { return null; }
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
  const streams = [], channels = [], calls = [];
  const context = vm.createContext({
    URLSearchParams, Date, console, setTimeout, clearTimeout,
    document: { querySelector: () => ({content:'test-token'}), activeElement:null },
    location: {search:''}, history: {replaceState(){}},
    fetch: async (path, options) => { calls.push([path, options]); const result = await request(path, options); return {ok:true,json:async()=>result}; },
    EventSource: class { constructor() {this.listeners={}; streams.push(this);} addEventListener(n,f){this.listeners[n]=f;} close(){} },
    BroadcastChannel: class { constructor(){channels.push(this);} postMessage(x){this.sent=x;} close(){} },
  });
  const cache = new Map();
  const helpers = { h, icon:n=>h('i',n), btn:(text,attrs={})=>h('button',attrs,text),
    ribbon:(p,x)=>h('header',x), panel:(title,icon,tools,...body)=>h('section',title,tools,body),
    split:(a,b)=>h('div',a,b), emptyState:(i,...x)=>h('div',x), errorState:e=>h('div',e.message),
    skeletons:()=>h('div','loading'), clock:x=>x, duration:x=>String(x)+'s', folio:x=>x,
    busy:async(button,fn)=>fn(), confirmDialog:async()=>true, toast:()=>{} };
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
    const root=h('main'); const hooks=await m.namespace.default.render(root,new URLSearchParams(params));
    return {root,hooks,ask:async message=>{const input=walk(root).find(x=>x.spec.startsWith('input')); input.value=message; walk(root).find(x=>x.spec.startsWith('form')).onsubmit({preventDefault(){}}); await settle();}};
  }
  return {api:apiModule.namespace.api,subscribe:apiModule.namespace.subscribe,page,streams,channels,calls,setRequest:fn=>{request=fn;}};
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
