import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const user = 'receipt-actor';
const operationId = 'd'.repeat(32);
const payloadHash = 'c'.repeat(64);
const cardPath = new URL('../../web/frontend/src/components/PublicationReceiptCard.tsx', import.meta.url);
const subject = overrides => ({ schemaVersion:1, project:{id:'default',name:'教育 IP'},
  platform:{id:'douyin',name:'抖音'}, account:{id:'real-account-one',name:'教育账号'},
  mode:'publish_now', scheduledAt:null, audience:'public', title:'给家长的一句话', body:'先听孩子说完。',
  tags:'亲子沟通', persona:'温和', media:[{artifactId:'a'.repeat(32),path:'cards/cover.png',name:'cover.png',mime:'image/png',
    revision:3,sha256:'b'.repeat(64),bundleSha256:null,size:123,url:`/api/chat/artifacts/${'a'.repeat(32)}/content`}], ...overrides });
const result = (status, overrides = {}) => ({ status, phase:'readback', reason:'public_readback_confirmed',
  externalId:'post-one', publicationState:'public', evidenceMethod:'authenticated_readback',
  accountId:'real-account-one', payloadHash, operationId, platform:'douyin',mode:'publish_now', ...overrides });
const record = overrides => ({id:'receipt-one',payloadHash,subject:subject(),state:'approved',actorId:user,projectId:'default',
  createdAt:1000,expiresAt:2000,operationId,result:result('confirmed'), ...overrides});
function load(globals = {}, options = {}) {
  // A missing feature renders no card, so the first RED asserts the missing
  // user-visible receipt rather than failing on an unrelated module import.
  return existsSync(cardPath) ? componentModule('components/PublicationReceiptCard.tsx', {
    user, globals, ...options,
  }).default : () => null;
}
const html = value => renderToStaticMarkup(React.createElement(load(), {record:value}));

async function cardDOM(props, callback, options = {}) {
  const {parseHTML} = require('linkedom');
  const {window} = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = {pathname:'/',origin:'http://127.0.0.1:7860'};
  let active = window.document.body;
  Object.defineProperty(window.document,'activeElement',{get:()=>active,configurable:true});
  const focus = window.HTMLElement.prototype.focus;
  window.HTMLElement.prototype.focus = function () { active = this; };
  const previous = Object.fromEntries(['window','document','IS_REACT_ACT_ENVIRONMENT'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  Object.defineProperties(globalThis,{window:{value:window,configurable:true,writable:true},
    document:{value:window.document,configurable:true,writable:true},
    IS_REACT_ACT_ENVIRONMENT:{value:true,configurable:true,writable:true}});
  const root = require('react-dom/client').createRoot(window.document.getElementById('root'));
  try {
    const Component = load({window,document:window.document},options);
    const render = async next => React.act(async()=>root.render(React.createElement(Component,next)));
    await render(props);
    const card = () => window.document.querySelector('[data-publication-receipt]');
    const refresh = () => window.document.querySelector('[aria-label="刷新发布回执"]');
    await callback({window,render,card,refresh});
  } finally {
    await React.act(async()=>root.unmount());
    window.HTMLElement.prototype.focus = focus;
    for(const [key,descriptor] of Object.entries(previous)) {
      if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
    }
  }
}

for(const [status,label,overrides] of [
  ['executing','正在执行',{phase:'unknown',externalId:undefined,publicationState:undefined,evidenceMethod:undefined}],
  ['submitted','已提交，等待公开回执',{phase:'platform_ack',reason:'provider_submission_acknowledged',publicationState:'processing',evidenceMethod:'provider_response'}],
  ['confirmed','平台已确认公开发布',{}],
  ['unknown','结果待核实',{phase:'submit_attempted',reason:'submission_not_acknowledged',publicationState:undefined,evidenceMethod:undefined}],
  ['failed','执行失败',{phase:'not_started',reason:'validation_failed',notStarted:true,externalId:undefined}],
]) {
  test(`the ${status} receipt has its exact business state without trusting a success message`,()=>{
    const rendered=html(record({result:result(status,{...overrides,message:'发布成功！已经公开！'})}));
    assert.ok(rendered.includes(label),label);
    for(const metadata of ['教育 IP','抖音','教育账号','给家长的一句话'])assert.ok(rendered.includes(metadata));
    assert.ok(!rendered.includes('发布成功！已经公开！'));
    assert.equal(rendered.includes('data-public-confirmed="true"'),status==='confirmed');
  });
}

for(const [processState,label,tone] of [
  ['stopping','正在停止，等待进程结束','running'],
  ['termination_unconfirmed','停止未确认，账号仍锁定','warning'],
]) {
  test(`the executing runner's ${processState} state stays locked and exposes its exact receipt metadata`,async()=>{
    const value=record({result:result('executing',{processState,terminationConfirmed:false,
      phase:'submit_attempted',reason:'timeout_after_submission',publicationState:'unknown',
      externalId:undefined,evidenceMethod:undefined,message:'进程已停止，可安全重发'})});
    await cardDOM({record:value},async({window,card})=>{
      assert.equal(card().getAttribute('data-state'),processState);
      assert.equal(card().getAttribute('data-tone'),tone);
      assert.match(window.document.body.textContent,new RegExp(label));
      for(const metadata of ['教育 IP','抖音','教育账号','给家长的一句话',operationId,payloadHash,
        'submit_attempted','timeout_after_submission','V3','b'.repeat(64)]) {
        assert.ok(window.document.body.textContent.includes(metadata),metadata);
      }
      assert.match(window.document.body.textContent,/账号仍保持锁定/);
      assert.match(window.document.body.textContent,/请勿重复提交/);
      assert.equal(card().getAttribute('data-public-confirmed'),null);
      assert.doesNotMatch(window.document.body.textContent,/可安全重发|进程已停止|平台已确认公开发布/);
      assert.equal(window.document.querySelectorAll('[data-decision]').length,0);
    });
  });
}

test('readonly refresh can expose stopping and unconfirmed termination without changing the underlying operation',async()=>{
  const initial=record({result:result('executing',{phase:'submit_attempted',reason:'timeout_after_submission',
    externalId:undefined,publicationState:'unknown',evidenceMethod:undefined})});
  const states=['stopping','termination_unconfirmed'];
  const calls=[];
  await cardDOM({record:initial,onRefresh:async id=>{
    calls.push(id);return {...initial,result:{...initial.result,processState:states.shift(),terminationConfirmed:false}};
  }},async({window,card,refresh})=>{
    await React.act(async()=>refresh().click());
    assert.equal(card().getAttribute('data-state'),'stopping');
    assert.match(window.document.body.textContent,/正在停止，等待进程结束/);
    await React.act(async()=>refresh().click());
    assert.equal(card().getAttribute('data-state'),'termination_unconfirmed');
    assert.match(window.document.body.textContent,/停止未确认，账号仍锁定/);
    assert.ok(window.document.body.textContent.includes(operationId));
    assert.ok(window.document.body.textContent.includes(payloadHash));
    assert.equal(window.document.querySelector('[role="alert"]'),null);
    assert.deepEqual(calls,['receipt-one','receipt-one']);
    assert.equal(card().getAttribute('data-public-confirmed'),null);
  });
});

test('an unknown process-state enum remains executing, while terminal failed and unknown receipts keep their own meaning',()=>{
  for(const processState of ['future-stop-state','stopped',null,{},undefined]) {
    const rendered=html(record({result:result('executing',{processState,terminationConfirmed:false})}));
    assert.match(rendered,/data-state="executing"/);
    assert.match(rendered,/正在执行/);
    assert.ok(!rendered.includes('data-public-confirmed="true"'));
  }
  for(const [status,label] of [['failed','执行失败'],['unknown','结果待核实']]) {
    const rendered=html(record({result:result(status,{processState:'stopping',terminationConfirmed:false})}));
    assert.ok(rendered.includes(label));
    assert.ok(!rendered.includes('正在停止，等待进程结束'));
  }
});

for(const [mode,status,audience,label] of [
  ['create_draft','draft_created','platform_draft','草稿已创建，尚未公开'],
  ['manual_export','exported','manual','发布包已导出，尚未发布'],
  ['scheduled','scheduled','public','定时任务已安排，尚未确认公开'],
]) {
  test(`${status} never becomes a public success`,()=>{
    const rendered=html(record({subject:subject({mode,audience,scheduledAt:mode==='scheduled'?'2026-11-04T10:00:00Z':null}),
      result:result(status,{mode,publicationState:status==='draft_created'?'draft':'scheduled',evidenceMethod:'provider_response'})}));
    assert.ok(rendered.includes(label));
    assert.ok(!rendered.includes('data-public-confirmed="true"'));
    assert.ok(!rendered.includes('平台已确认公开发布'));
  });
}

test('an approval alone, missing status, missing operation or unapproved record never shows public confirmation',()=>{
  for(const value of [record({result:null,operationId:null}),record({result:{message:'发布成功'}}),
    record({operationId:null}),...['pending','rejected','expired','invalidated'].map(state=>record({state}))]) {
    const rendered=html(value);
    assert.ok(rendered.includes('data-publication-receipt'));
    assert.ok(!rendered.includes('data-public-confirmed="true"'));
    assert.ok(!rendered.includes('平台已确认公开发布'));
  }
  assert.match(html(record({result:null,operationId:null})),/已批准，尚未执行/);
});

test('an unapproved record that nevertheless contains an operation does not invent non-execution',()=>{
  const rendered=html(record({state:'expired',result:result('submitted')}));
  assert.match(rendered,/执行状态待核实/);
  assert.ok(!rendered.includes('未执行'));
  assert.ok(!rendered.includes('data-public-confirmed="true"'));
});

test('correlation or visibility mismatches fail closed despite a confirmed result status',()=>{
  for(const patch of [{operationId:'f'.repeat(32)},{payloadHash:'f'.repeat(64)},{accountId:'other-account'},
    {platform:'zhihu'},{mode:'create_draft'},{phase:'platform_ack'},{publicationState:'private'},
    {evidenceMethod:'ui_signal'},{reason:'account_mismatch'},{identityVerification:'account_mismatch'},
    {source:'stdout'},{externalId:undefined},{operationId:undefined},{payloadHash:undefined}]) {
    const rendered=html(record({result:result('confirmed',patch)}));
    assert.ok(!rendered.includes('data-public-confirmed="true"'),JSON.stringify(patch));
    assert.ok(!rendered.includes('平台已确认公开发布'),JSON.stringify(patch));
  }
});

test('an explicit human declaration is labeled as such and is not platform verification',()=>{
  const rendered=html(record({result:result('confirmed',{source:'human',evidenceMethod:'human_declaration'})}));
  assert.match(rendered,/人工声明已发布/);
  assert.match(rendered,/未经平台自动验证/);
  assert.ok(!rendered.includes('data-public-confirmed="true"'));
});

test('foreign actor or IP receipts expose no subject metadata and cannot refresh',async()=>{
  for(const value of [record({actorId:'other-user'}),record({projectId:'other-ip',subject:subject({project:{id:'other-ip',name:'别人的 IP'}})})]) {
    const called=[];
    await cardDOM({record:value,onRefresh:async id=>{called.push(id);return value;}},async({window,refresh})=>{
      assert.match(window.document.body.textContent,/当前用户或 IP/);
      assert.ok(!window.document.body.textContent.includes('教育账号'));
      assert.equal(refresh(),null);assert.deepEqual(called,[]);
    });
  }
});

test('expanded receipt details identify fixed payload and media versions without untrusted links',async()=>{
  const value=record({result:result('unknown',{phase:'submit_attempted',reason:'timeout_after_submission',url:'https://evil.test/fake-post',message:'已公开发布'})});
  await cardDOM({record:value},async({window,card})=>{
    assert.ok(card());
    for(const expected of [payloadHash,operationId,'V3','b'.repeat(64),'submit_attempted','timeout_after_submission','post-one'])assert.ok(window.document.body.textContent.includes(expected),expected);
    assert.equal(window.document.querySelectorAll('a').length,0);
    assert.equal(window.document.querySelectorAll('img,iframe').length,0);
    assert.equal(window.document.querySelectorAll('[data-decision]').length,0);
    assert.ok(!window.document.body.textContent.includes('https://evil.test'));
    assert.ok(!window.document.body.textContent.includes('已公开发布'));
  });
});

test('refresh is a duplicate-locked readonly lookup of the current receipt and failure can retry',async()=>{
  const calls=[];let reject;
  const value=record({result:result('unknown')});
  const props={record:value,onRefresh:id=>{calls.push(id);return new Promise((_resolve,fail)=>{reject=fail;});}};
  await cardDOM(props,async({window,refresh})=>{
    assert.ok(refresh());
    await React.act(async()=>{refresh().click();refresh().click();});
    assert.deepEqual(calls,['receipt-one']);assert.equal(refresh().disabled,true);
    await React.act(async()=>reject(new Error('retryable')));
    assert.equal(refresh().disabled,false);assert.ok(window.document.querySelector('[role="alert"]'));
    await React.act(async()=>refresh().click());assert.equal(calls.length,2);
    await React.act(async()=>reject(new Error('retryable')));
  });
});

function unresolvedMode(mode) {
  const draft=mode==='create_draft';
  const platform=draft?{id:'wechat-oa',name:'微信公众号'}:{id:'douyin',name:'抖音'};
  return record({subject:subject({mode,platform,audience:draft?'platform_draft':mode==='manual_export'?'manual':'public',
    scheduledAt:mode==='scheduled'?'2026-11-04T10:00:00Z':null}),
  result:result('unknown',{mode,platform:platform.id,phase:'submit_attempted',reason:'submission_not_acknowledged',
    externalId:undefined,publicationState:'unknown',evidenceMethod:undefined})});
}

for(const [mode,status,label,observation] of [
  ['publish_now','submitted','已提交，等待公开回执',{phase:'platform_ack',reason:'provider_submission_acknowledged',evidenceMethod:'provider_response',publicationState:'processing',externalId:'late-post'}],
  ['create_draft','draft_created','草稿已创建，尚未公开',{phase:'platform_ack',reason:'provider_draft_created',evidenceMethod:'provider_response',publicationState:'draft',externalId:'late-draft',identityVerification:'host_dispatch_only'}],
  ['publish_now','failed','执行失败',{phase:'not_started',reason:'validation_failed',notStarted:true,externalId:undefined,publicationState:'unknown',evidenceMethod:undefined}],
  ['publish_now','failed','执行失败',{phase:'readback',reason:'provider_rejected',notStarted:false,externalId:'rejected-post',publicationState:'rejected',evidenceMethod:'authenticated_readback'}],
  ['scheduled','submitted','已提交，等待公开回执',{phase:'platform_ack',reason:'provider_submission_acknowledged',evidenceMethod:'provider_response',publicationState:'processing',externalId:'late-post'}],
  ['scheduled','scheduled','定时任务已安排，尚未确认公开',{phase:'platform_ack',reason:'provider_submission_acknowledged',evidenceMethod:'provider_response',publicationState:'scheduled',externalId:'late-scheduled-post'}],
  ['manual_export','exported','发布包已导出，尚未发布',{phase:'unknown',reason:undefined,externalId:undefined,publicationState:undefined,evidenceMethod:undefined}],
]) {
  test(`an unknown receipt accepts the Host's later ${mode}/${status} observation`,async()=>{
    const initial=unresolvedMode(mode);
    const next={...initial,result:result(status,{mode,platform:initial.subject.platform.id,...observation})};
    const calls=[];
    await cardDOM({record:initial,onRefresh:async id=>{calls.push(id);return next;}},async({window,refresh,card})=>{
      assert.equal(card().getAttribute('data-state'),'unknown');
      await React.act(async()=>refresh().click());
      assert.deepEqual(calls,['receipt-one']);
      assert.equal(card().getAttribute('data-state'),status);
      assert.ok(window.document.body.textContent.includes(label));
      assert.equal(window.document.querySelector('[role="alert"]'),null);
      assert.equal(refresh().disabled,false);
      assert.equal(card().getAttribute('data-public-confirmed'),null);
    });
  });
}

test('an unknown receipt rejects cross-mode effects and cannot move back to runner execution',async()=>{
  for(const [mode,status] of [['publish_now','draft_created'],['publish_now','exported'],['publish_now','scheduled'],
    ['create_draft','submitted'],['create_draft','confirmed'],['create_draft','scheduled'],['create_draft','exported'],
    ['manual_export','submitted'],['manual_export','confirmed'],['manual_export','scheduled'],['manual_export','draft_created'],
    ['scheduled','draft_created'],['scheduled','exported'],['publish_now','executing']]) {
    const initial=unresolvedMode(mode);
    await cardDOM({record:initial,onRefresh:async()=>({...initial,result:result(status,{mode,platform:initial.subject.platform.id})})},async({window,refresh,card})=>{
      await React.act(async()=>refresh().click());
      assert.equal(card().getAttribute('data-state'),'unknown',`${mode}/${status}`);
      assert.ok(window.document.querySelector('[role="alert"]'),`${mode}/${status}`);
      assert.equal(refresh().disabled,false);
    });
  }
});

test('a pending refresh cannot replace a newer prop record or unlock its own pending refresh',async()=>{
  const pending=[];
  const onRefresh=id=>new Promise(resolve=>pending.push({id,resolve}));
  await cardDOM({record:record({result:result('unknown')}),onRefresh},async({window,render,refresh})=>{
    await React.act(async()=>refresh().click());
    const next=record({id:'receipt-new',payloadHash:'e'.repeat(64),subject:subject({title:'新版内容'}),
      result:result('unknown',{payloadHash:'e'.repeat(64)})});
    await render({record:next,onRefresh});
    assert.equal(refresh().disabled,false);await React.act(async()=>refresh().click());
    await React.act(async()=>pending[0].resolve(record()));
    assert.equal(refresh().disabled,true);assert.match(window.document.body.textContent,/新版内容/);
    assert.ok(!window.document.body.textContent.includes('平台已确认公开发布'));
    await React.act(async()=>pending[1].resolve({...next,result:result('confirmed',{payloadHash:'e'.repeat(64)})}));
    assert.match(window.document.body.textContent,/平台已确认公开发布/);
  });
});

test('refresh rejects foreign actor/IP/hash/operation and rewritten frozen subject data',async()=>{
  for(const patch of [{actorId:'foreign'},{projectId:'foreign'}, {payloadHash:'e'.repeat(64)},
    {operationId:'e'.repeat(32)}, {subject:subject({title:'非法替换文案'})}]) {
    const value=record({result:result('unknown')});
    await cardDOM({record:value,onRefresh:async()=>({...record(),...patch})},async({window,refresh})=>{
      await React.act(async()=>refresh().click());
      assert.equal(refresh().disabled,false);assert.ok(window.document.querySelector('[role="alert"]'));
      assert.ok(!window.document.body.textContent.includes('平台已确认公开发布'));
      assert.ok(!window.document.body.textContent.includes('非法替换文案'));
    });
  }
});

test('readonly refresh cannot rewrite the frozen AI declaration under the same approval hash', async () => {
  const initial = record({ subject: subject({ aiGenerated: true }), result: result('unknown') });
  await cardDOM({ record: initial, onRefresh: async () => record({ subject: subject({ aiGenerated: false }) }) }, async ({ window, refresh, card }) => {
    await React.act(async () => refresh().click());
    assert.equal(card().getAttribute('data-state'), 'unknown');
    assert.ok(window.document.querySelector('[role="alert"]'));
  });
});

test('a late read cannot roll back a newer Host result for the same approval id and hash',async()=>{
  let finish;
  const onRefresh=()=>new Promise(resolve=>{finish=resolve;});
  await cardDOM({record:record({result:result('unknown')}),onRefresh},async({window,render,refresh})=>{
    await React.act(async()=>refresh().click());
    await render({record:record(),onRefresh});
    assert.match(window.document.body.textContent,/平台已确认公开发布/);
    await React.act(async()=>finish(record({result:result('unknown')})));
    assert.match(window.document.body.textContent,/平台已确认公开发布/);
    assert.equal(window.document.querySelector('[role="alert"]'),null);
  });
});

test('a terminal confirmed receipt cannot regress through a later refresh',async()=>{
  await cardDOM({record:record(),onRefresh:async()=>record({result:result('submitted')})},async({window,refresh})=>{
    await React.act(async()=>refresh().click());
    assert.match(window.document.body.textContent,/平台已确认公开发布/);
    assert.ok(window.document.querySelector('[role="alert"]'));
    assert.equal(refresh().disabled,false);
  });
});

test('the Host can invalidate an approved version before any operation has started',async()=>{
  const initial=record({operationId:null,result:null});
  await cardDOM({record:initial,onRefresh:async()=>({...initial,state:'invalidated'})},async({window,refresh})=>{
    await React.act(async()=>refresh().click());
    assert.match(window.document.body.textContent,/原批准已失效/);
    assert.ok(!window.document.body.textContent.includes('已批准，尚未执行'));
    assert.equal(window.document.querySelector('[role="alert"]'),null);
  });
});

test('workflow disclosure preserves running choices, opens on failure and defers completion collapse while focused',async()=>{
  await cardDOM({record:record({result:result('executing')})},async({window,render,card})=>{
    const disclosure=()=>card().querySelector('[data-disclosure-row]');
    assert.equal(disclosure().getAttribute('aria-expanded'),'true');
    await React.act(async()=>disclosure().click());
    await render({record:record({result:result('executing',{phase:'submit_attempted'})})});
    assert.equal(disclosure().getAttribute('aria-expanded'),'false');
    await render({record:record({result:result('failed',{phase:'not_started',reason:'validation_failed'})})});
    assert.equal(disclosure().getAttribute('aria-expanded'),'true');
    const code=card().querySelector('code');assert.ok(code);code.focus();
    await render({record:record()});
    assert.equal(disclosure().getAttribute('aria-expanded'),'true');
    const blur=new window.Event('focusout',{bubbles:true});Object.defineProperty(blur,'relatedTarget',{value:window.document.body});
    await React.act(async()=>code.dispatchEvent(blur));
    assert.equal(disclosure().getAttribute('aria-expanded'),'false');
  });
});
