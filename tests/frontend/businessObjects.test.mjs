import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';

const require=createRequire(new URL('../../web/frontend/package.json',import.meta.url));
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const actor='business-actor';
const digest='c'.repeat(64);
const workshopId='7'.repeat(32);
const cardPath=new URL('../../web/frontend/src/components/BusinessObjectCard.tsx',import.meta.url);
const libraryPath=new URL('../../web/frontend/src/lib/businessObjects.ts',import.meta.url);
const artifact=changes=>({artifactId:'a'.repeat(32),projectId:'default',sessionId:'session-one',turnId:'turn-one',
  path:'reports/draft.md',name:'draft.md',mime:'text/markdown',size:32,description:'固定稿件说明',status:'available',createdAt:1000,
  url:`/api/chat/artifacts/${'a'.repeat(32)}/content`,storageMode:'snapshot',revision:2,sha256:'b'.repeat(64),bundleSha256:null,
  reviewStatus:'unreviewed',...changes});
const object=changes=>({schemaVersion:1,actorId:actor,projectId:'default',kind:'idea',id:'idea-one',title:'真实选题标题',
  summary:'来自选题库中的真实备注',status:{code:'doing',label:'正在推进',source:'stored'},version:{revision:null,sha256:null},
  snapshotHash:digest,observedAt:1_793_664_000_000,details:{idea:{id:'idea-one',note:'先解释政策再讲案例',source:'选题库',status:'doing',created:1000}},
  actions:[{kind:'open',target:'ideas',entityId:'idea-one',label:'打开此选题'}],...changes});
const draft=changes=>object({kind:'draft',id:'a'.repeat(32),title:'文字稿件',summary:'固定交付记录',status:{code:'draft_file',label:'草稿文件（固定版本）',source:'computed'},
  version:{revision:2,sha256:'b'.repeat(64)},details:{artifact:artifact()},actions:[{kind:'open',target:'artifact',entityId:'a'.repeat(32),label:'打开稿件'}],...changes});
const workshop=changes=>object({kind:'workshop',id:workshopId,title:'课程介绍视频',summary:'已有制作工程',
  status:{code:'producing',label:'制作中',source:'computed'},version:{revision:4,sha256:'d'.repeat(64)},
  details:{engineVerification:'not_checked',publishEligibility:'unverified',deliveryVerification:'stale_revision',
    publicationEvidence:'stored_records_only',canvasBinding:'verified',project:{id:workshopId,revision:4,stage:'simulated_reviewed',
    deliverables:[{id:'delivery-one',revision:3,sha256:'d'.repeat(64),path:'videos/final.mp4'}],
    renders:[{id:'render-one',operation_id:'render-operation',task_id:'render-task',content_revision:4,engine_revision:7,status:'running',progress:0.4}],
    reviews:[{id:'review-sim',kind:'simulated',decision:'approved',revision:3,artifact_id:'delivery-one',artifact_sha256:'d'.repeat(64),notes:'模拟预审'},
      {id:'review-human',kind:'human',decision:'changes_requested',revision:4,artifact_id:'delivery-two',artifact_sha256:'e'.repeat(64),reviewer_id:'reviewer-from-host',notes:'补充字幕'}],
    publications:[{id:'publication-one',revision:3,platform:'抖音',artifact_id:'delivery-one',artifact_sha256:'d'.repeat(64),url:'https://evil.test/not-a-trusted-link'}]}},
  actions:[{kind:'open',target:'workshop',entityId:workshopId,label:'打开视频工程'}],...changes});
function load(options={}) {
  return existsSync(cardPath)?componentModule('components/BusinessObjectCard.tsx',{user:actor,...options}).default:()=>null;
}
function library(options={}) {
  return existsSync(libraryPath)?componentModule('lib/businessObjects.ts',{user:actor,...options}):{normalizeBusinessObject:()=>null};
}
const render=value=>renderToStaticMarkup(React.createElement(load(),{record:value}));
async function cardDOM(props,callback,options={}) {
  const {parseHTML}=require('linkedom');
  const {window}=parseHTML('<html><body><div id="root"></div></body></html>');
  window.location={pathname:'/',origin:'http://127.0.0.1:7860'};
  let active=window.document.body;
  Object.defineProperty(window.document,'activeElement',{get:()=>active,configurable:true});
  const focus=window.HTMLElement.prototype.focus;
  window.HTMLElement.prototype.focus=function(){active=this;};
  const previous=Object.fromEntries(['window','document','IS_REACT_ACT_ENVIRONMENT'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  Object.defineProperties(globalThis,{window:{value:window,configurable:true,writable:true},
    document:{value:window.document,configurable:true,writable:true},IS_REACT_ACT_ENVIRONMENT:{value:true,configurable:true,writable:true}});
  const root=require('react-dom/client').createRoot(window.document.getElementById('root'));
  try {
    const Component=load({...options,globals:{window,document:window.document,Headers,Response,TextDecoder,...options.globals}});
    const render=async value=>React.act(async()=>root.render(React.createElement(Component,value)));
    await render(props);
    const card=()=>window.document.querySelector('[data-business-object]');
    const refresh=()=>window.document.querySelector('[aria-label="刷新业务对象"]');
    const expand=async()=>{const row=card()?.querySelector('[data-disclosure-row]');assert.ok(row);if(row.getAttribute('aria-expanded')!=='true')await React.act(async()=>row.click());};
    await callback({window,render,card,refresh,expand});
  } finally {
    await React.act(async()=>root.unmount());window.HTMLElement.prototype.focus=focus;
    for(const [key,descriptor] of Object.entries(previous)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
  }
}

test('a real idea card exposes exact identity, stored status and observation digest without inventing a formal version',()=>{
  const html=render(object());
  for(const text of ['真实选题标题','idea-one','doing','正在推进',digest,String(object().observedAt)])assert.ok(html.includes(text),text);
  assert.match(html,/data-business-object="idea-one"/);assert.match(html,/data-kind="idea"/);
  assert.match(html,/无正式稿件版本/);assert.match(html,/观测摘要/);assert.match(html,/已保存状态/);
  assert.doesNotMatch(html,/V1|已发布|审核通过|✓/);
});

test('normalization rejects invalid or foreign envelopes and strips backend configuration from visible business details',()=>{
  const {normalizeBusinessObject}=library();
  const valid=normalizeBusinessObject(object({details:{idea:{note:'真实内容',source:'真实来源',config:{apiKey:'SECRET'}},
    model_provider:'SECRET',runtime_url:'https://backend.invalid/private',password:'SECRET'}}));
  assert.ok(valid);
  assert.ok(!JSON.stringify(valid).includes('SECRET'));
  for(const change of [{actorId:'other'}, {projectId:'other'}, {kind:'unknown'}, {schemaVersion:2}, {observedAt:0}, {observedAt:Number.MAX_SAFE_INTEGER},
    {snapshotHash:'bad'}, {version:{revision:null,sha256:'bad'}}, {version:{revision:1,sha256:null}},
    {status:{code:'doing',label:'正在推进',source:'agent_message'}}, {id:'../../private'}, {id:'idea%2fsecret'},
    {id:'https://evil.test/'}, {id:'not.a.host.idea'}]) {
    assert.equal(normalizeBusinessObject(object(change)),null,JSON.stringify(change));
  }
  assert.equal(normalizeBusinessObject(workshop({id:'legacy-workshop'})),null);
  assert.equal(normalizeBusinessObject(draft({id:'legacy-draft'})),null);
});

test('Host-bounded Unicode titles and historical CRLF text remain readable in the real card',async()=>{
  const title='📝'.repeat(1024);
  const value=object({title,summary:'第一行\r\n第二行',details:{idea:{note:'旧备注\r\n保留换行',source:'历史选题库'}}});
  assert.equal(library().normalizeBusinessObject(value)?.summary,'第一行\r\n第二行');
  await cardDOM({record:value},async({window,card,expand})=>{
    assert.equal(card().getAttribute('data-business-object'),'idea-one');
    await expand();
    for(const text of ['📝','第一行','第二行','旧备注','保留换行','历史选题库'])assert.ok(window.document.body.textContent.includes(text),text);
  });
});

test('only exact local open actions survive and no server href can redirect navigation',async()=>{
  const value=object({actions:[...object().actions,{kind:'open',target:'ideas',entityId:'idea-one',label:'打开此选题',href:'https://evil.test/redirect'},
    {kind:'publish',target:'ideas',entityId:'idea-one',label:'危险发布'},{kind:'open',target:'workshop',entityId:'idea-one',label:'错类型'},
    {kind:'open',target:'ideas',entityId:'another',label:'错对象'}]});
  await cardDOM({record:value},async({window})=>{
    const links=[...window.document.querySelectorAll('a')];
    assert.deepEqual(links.map(link=>link.getAttribute('href')),['#/business/default/idea/idea-one']);
    assert.doesNotMatch(window.document.body.textContent,/危险发布|错类型|错对象/);
    assert.equal(window.document.querySelectorAll('[data-decision]').length,0);
  });
});

test('existing opaque idea IDs can navigate without being forced into the new hex-only ID shape',async()=>{
  const id='_legacy-idea_2025';
  const value=object({id,actions:[{kind:'open',target:'ideas',entityId:id,label:'查看旧选题'}]});
  await cardDOM({record:value},async({window,card})=>{
    assert.equal(card().getAttribute('data-business-object'),id);
    assert.equal(window.document.querySelector('a').getAttribute('href'),`#/business/default/idea/${id}`);
  });
});

test('workshop details show actual renders, historical artifacts, simulated and human reviews without inventing an actor',async()=>{
  await cardDOM({record:workshop()},async({window,expand,card})=>{
    assert.equal(card().getAttribute('data-kind'),'workshop');
    assert.equal(card().getAttribute('data-status'),'producing');
    await expand();
    for(const text of [workshopId,'V4','render-one','render-operation','render-task','running','40%','delivery-one','V3','review-sim','review-human',
      '模拟','人工','changes_requested','审核人未记录','reviewer-from-host','历史版本','发布登记','非平台自动验证',
      '最新成片摘要','引擎未核验','发布条件待核对'])assert.ok(window.document.body.textContent.includes(text),text);
    assert.equal(window.document.querySelectorAll('iframe,video').length,0);
    assert.deepEqual([...window.document.querySelectorAll('a')].map(link=>link.getAttribute('href')),[`#/business/default/workshop/${workshopId}`]);
    assert.doesNotMatch(window.document.body.textContent,/平台已确认公开发布|https:\/\/evil/);
  });
});

test('drafts reuse the real fixed-snapshot deliverable card and preview route, never mutable current files',async()=>{
  await cardDOM({record:draft()},async({window,expand})=>{
    const opened=[];window.open=(...args)=>opened.push(args);
    await expand();
    assert.ok(window.document.querySelector('[data-presented-file]'));
    assert.match(window.document.body.textContent,/本卡交付 V2/);
    assert.equal(window.document.querySelector('.deliverable-download').getAttribute('href'),`/api/chat/artifacts/${'a'.repeat(32)}/content?download=1`);
    await React.act(async()=>window.document.querySelector('[aria-label="查看文件：reports/draft.md"]').click());
    assert.deepEqual(opened,[[`/api/chat/artifacts/${'a'.repeat(32)}/content`,'_blank','noopener,noreferrer']]);
    assert.ok(![...window.document.querySelectorAll('a')].some(link=>(link.getAttribute('href')||'').includes('/api/media/')));
    assert.ok(![...window.document.querySelectorAll('a')].some(link=>(link.getAttribute('href')||'').startsWith('#/business/')));
  });
});

test('查看稿件内容 reads the authenticated fixed Markdown snapshot inside the same card',async()=>{
  const requests=[];
  const markdown='# 固定稿件 V1\n\n<script>不执行</script>\n![外部图片](https://evil.test/image.png)';
  await cardDOM({record:draft({version:{revision:1,sha256:'b'.repeat(64)},details:{artifact:artifact({revision:1})}})},async({window,expand,card})=>{
    await expand();
    const button=[...card().querySelectorAll('button')].find(item=>item.textContent==='查看稿件内容');
    assert.ok(button);
    assert.deepEqual(requests,[]);
    await React.act(async()=>{button.click();button.click();});
    assert.deepEqual(requests,[[`/api/chat/artifacts/${'a'.repeat(32)}/content`,actor]]);
    assert.equal(card().querySelector('pre').textContent,markdown);
    assert.equal(card().querySelector('script,img,iframe'),null);
  },{globals:{fetch:async(url,options)=>{
    requests.push([url,options.headers.get('X-Easel-User')]);return new Response(markdown);
  }}});
});

test('draft content 404 offers retry and fixed download, and a new observation drops a late read',async()=>{
  let attempt=0,finish,signal;
  const initial=draft();
  await cardDOM({record:initial},async({window,expand,card,render})=>{
    await expand();
    const button=[...card().querySelectorAll('button')].find(item=>item.textContent==='查看稿件内容');
    assert.ok(button);
    await React.act(async()=>button.click());
    const failure=card().querySelector('[role="alert"]');
    assert.match(failure.textContent,/暂不可用/);
    assert.equal(failure.querySelector('a').getAttribute('href'),`/api/chat/artifacts/${'a'.repeat(32)}/content?download=1`);
    await React.act(async()=>failure.querySelector('button').click());
    assert.equal(attempt,2);
    await render({record:{...initial,observedAt:initial.observedAt+1}});
    assert.equal(signal.aborted,true);
    await React.act(async()=>finish(new Response('迟到的稿件内容')));
    assert.doesNotMatch(window.document.body.textContent,/迟到的稿件内容/);
  },{globals:{fetch:async(_url,options)=>{
    if(attempt++===0)return new Response('',{status:404});
    signal=options.signal;return new Promise(resolve=>{finish=resolve;});
  }}});
});

test('legacy, mismatched and unsafe draft metadata cannot fall through to a current-file preview',async()=>{
  for(const entry of [artifact({storageMode:'legacy-current',revision:null,sha256:null}),artifact({projectId:'other'}),
    artifact({artifactId:'e'.repeat(32),url:`/api/chat/artifacts/${'e'.repeat(32)}/content`}),
    artifact({revision:3}),artifact({sha256:'e'.repeat(64)}),artifact({url:'https://evil.test/preview'})]) {
    const value=draft({details:{artifact:entry},...(entry.storageMode==='legacy-current'?{version:{revision:null,sha256:null}}:{})});
    await cardDOM({record:value},async({window,expand})=>{
      await expand();
      assert.equal(window.document.querySelector('[data-presented-file]'),null);
      assert.equal(window.document.querySelectorAll('a,img,iframe').length,0);
      assert.match(window.document.body.textContent,/固定快照.*不可验证|未固定历史版本/);
    });
  }
});

test('foreign actor/IP and malformed records show only an unavailable placeholder and never refresh',async()=>{
  for(const value of [object({actorId:'foreign'}),object({projectId:'foreign'}),object({kind:'unsupported'})]) {
    const requests=[];
    await cardDOM({record:value,onRefresh:async(...args)=>{requests.push(args);return value;}},async({window,refresh})=>{
      assert.match(window.document.body.textContent,/当前用户或 IP|对象信息不可用/);
      assert.doesNotMatch(window.document.body.textContent,/真实选题标题|idea-one/);
      assert.equal(refresh(),null);assert.deepEqual(requests,[]);
    });
  }
});

test('refresh only inspects kind/id, duplicate-locks immediately and displays a genuinely newer workshop version',async()=>{
  const initial=workshop();const calls=[];let finish;
  await cardDOM({record:initial,onRefresh:(...args)=>{calls.push(args);return new Promise(resolve=>{finish=resolve;});}},async({window,card,refresh,expand})=>{
    await React.act(async()=>{refresh().click();refresh().click();});
    assert.deepEqual(calls,[['workshop',workshopId]]);assert.equal(refresh().disabled,true);
    await React.act(async()=>finish(workshop({version:{revision:5,sha256:'e'.repeat(64)},snapshotHash:'f'.repeat(64),observedAt:initial.observedAt+100,
      status:{code:'review_pending',label:'等待人工审核',source:'computed'},details:{project:{id:workshopId,revision:5,stage:'ready_for_review'}}})));
    assert.equal(refresh().disabled,false);assert.equal(card().getAttribute('data-status'),'review_pending');
    assert.equal(card().getAttribute('data-observed-at'),String(initial.observedAt+100));
    await expand();assert.match(window.document.body.textContent,/V5/);assert.ok(window.document.body.textContent.includes('e'.repeat(64)));
    assert.ok(window.document.body.textContent.includes('f'.repeat(64)));
    assert.equal(window.document.querySelector('[role="alert"]'),null);
  });
});

test('refresh rejects cross identity, same/older observations and malformed replacement versions',async()=>{
  const initial=object();
  for(const change of [{actorId:'foreign'},{projectId:'foreign'},{kind:'workshop'},{id:'other-id'},
    {observedAt:initial.observedAt},{observedAt:initial.observedAt-1},{snapshotHash:'invalid'},
    {version:{revision:1,sha256:null}}]) {
    await cardDOM({record:initial,onRefresh:async()=>object({observedAt:initial.observedAt+100,title:'不得替换的内容',...change})},async({window,refresh})=>{
      await React.act(async()=>refresh().click());
      assert.match(window.document.body.textContent,/真实选题标题/);assert.doesNotMatch(window.document.body.textContent,/不得替换/);
      assert.ok(window.document.querySelector('[role="alert"]'));assert.equal(refresh().disabled,false);
    });
  }
});

test('a newer valid response for another kind cannot replace an idea with the same opaque ID',async()=>{
  const initial=object({id:workshopId,actions:[{kind:'open',target:'ideas',entityId:workshopId,label:'查看选题'}]});
  await cardDOM({record:initial,onRefresh:async()=>workshop({observedAt:initial.observedAt+100})},async({window,card,refresh})=>{
    await React.act(async()=>refresh().click());
    assert.equal(card().getAttribute('data-kind'),'idea');
    assert.ok(window.document.querySelector('[role="alert"]'));
    assert.doesNotMatch(window.document.body.textContent,/课程介绍视频/);
  });
});

test('deletion/404 retains the prior snapshot, labels its history and remains retryable',async()=>{
  let attempt=0;const initial=object();
  await cardDOM({record:initial,onRefresh:async()=>{if(attempt++===0)throw Object.assign(new Error('server-secret-config'),{status:404});return object({observedAt:initial.observedAt+100});}},async({window,card,refresh})=>{
    await React.act(async()=>refresh().click());
    assert.match(window.document.body.textContent,/历史观测/);assert.match(window.document.body.textContent,/无法刷新/);
    assert.match(window.document.body.textContent,/真实选题标题/);assert.ok(!window.document.body.textContent.includes('server-secret-config'));
    assert.equal(refresh().disabled,false);
    await React.act(async()=>refresh().click());
    assert.equal(card().getAttribute('data-observed-at'),String(initial.observedAt+100));
    assert.equal(window.document.querySelector('[role="alert"]'),null);
  });
});

test('late refreshes cannot replace newer props or clear another object refresh, including same-id updates',async()=>{
  const pending=[];const onRefresh=(kind,id)=>new Promise(resolve=>pending.push({kind,id,resolve}));
  const first=object();
  await cardDOM({record:first,onRefresh},async({window,render,card,refresh})=>{
    await React.act(async()=>refresh().click());
    const newer=object({title:'Host 最新观测',snapshotHash:'d'.repeat(64),observedAt:first.observedAt+200});
    await render({record:newer,onRefresh});await React.act(async()=>refresh().click());
    await React.act(async()=>pending[0].resolve(object({title:'较旧响应',observedAt:first.observedAt+100})));
    assert.equal(refresh().disabled,true);assert.match(window.document.body.textContent,/Host 最新观测/);
    await React.act(async()=>pending[1].resolve({...newer,title:'再次更新的观测',observedAt:first.observedAt+300}));
    assert.equal(card().getAttribute('data-observed-at'),String(first.observedAt+300));
    assert.match(window.document.body.textContent,/再次更新的观测/);assert.doesNotMatch(window.document.body.textContent,/较旧响应/);
  });
});

test('a refresh response after unmount cannot restore a removed card or affect another card',async()=>{
  let finish;const first=object();
  await cardDOM({record:first,onRefresh:()=>new Promise(resolve=>{finish=resolve;})},async({window,render,card,refresh})=>{
    await React.act(async()=>refresh().click());
    await render({record:object({actorId:'foreign'}),onRefresh:async()=>first});
    await React.act(async()=>finish(object({observedAt:first.observedAt+100})));
    assert.equal(card(),null);assert.doesNotMatch(window.document.body.textContent,/真实选题标题/);
  });
});

test('unknown status and historical observation remain explicit when the chat turn is marked settled',async()=>{
  const value=workshop({status:{code:'unknown',label:'尚未确认制作状态',source:'unknown'},version:{revision:null,sha256:null}});
  await cardDOM({record:value,settled:false},async({window,render,card})=>{
    await render({record:value,settled:true});
    assert.equal(card().getAttribute('data-status'),'unknown');assert.match(window.document.body.textContent,/状态来源未知|来源未确认/);
    assert.match(window.document.body.textContent,/版本未记录/);assert.doesNotMatch(window.document.body.textContent,/✓|已完成|平台已确认/);
  });
});

test('stable event keys preserve exact identity and independent observation facts',()=>{
  const module=library();assert.equal(typeof module.businessObjectIdentity,'function');
  const initial=object();const next=object({observedAt:initial.observedAt+1,snapshotHash:'d'.repeat(64)});
  assert.equal(module.businessObjectIdentity(initial),module.businessObjectIdentity(next));
  assert.equal(module.businessObjectEventKey(initial),module.businessObjectEventKey({...initial}));
  assert.notEqual(module.businessObjectEventKey(initial),module.businessObjectEventKey(next));
  assert.notEqual(module.businessObjectIdentity(initial),module.businessObjectIdentity({...initial,projectId:'foreign'}));
});
