import assert from 'node:assert/strict';
import {test} from 'node:test';
import {componentModule} from './agentMessagesHarness.mjs';
const user='navigation-actor';
const record={schemaVersion:1,actorId:user,projectId:'default',kind:'workshop',id:'1'.repeat(32),title:'真实工程',summary:'摘要',
 status:{code:'producing',label:'制作中',source:'stored'},version:{revision:null,sha256:null},snapshotHash:'a'.repeat(64),
 observedAt:100,details:{},actions:[{kind:'open',target:'workshop',entityId:'1'.repeat(32),label:'查看工坊'}]};
const load=(fetch=()=>{})=>componentModule('lib/businessObjectApi.ts',{user,globals:{Headers,URLSearchParams,fetch}});

test('retired planning links cannot reopen an idea workspace',()=>{
 const {parseBusinessHash}=load();
 assert.equal(parseBusinessHash('#/business/default/idea/legacy-idea'),null);
});

test('business deep links preserve exact IP, kind and entity ID',()=>{
 const {parseBusinessHash}=load();
 assert.deepEqual(JSON.parse(JSON.stringify(parseBusinessHash('#/business/default/workshop/'+record.id))),{kind:'workshop',id:record.id});
 assert.equal(parseBusinessHash('#/business/other/idea/'+record.id),null);
 for(const suffix of ['/draft/'+record.id,'/workshop/not-a-hex-id','/idea/../../private','/idea/%2fsecret','/idea/'+record.id+'?url=https://example.invalid']) {
  assert.equal(parseBusinessHash('#/business/default'+suffix),null);
 }
 assert.equal(parseBusinessHash('https://example.invalid'),null);
});

test('read-only navigation request uses the actual authenticated scope and a single inspected object',async()=>{
 const calls=[];
 const api=load(async(url,options)=>{calls.push({url,options});return{ok:true,status:200,json:async()=>({items:[record]})};});
 const value=await api.fetchBusinessObject('workshop',record.id);
 assert.equal(value.id,record.id);assert.equal(calls.length,1);
 assert.match(calls[0].url,/chat\/business-objects\?action=inspect&kind=workshop&id=11111111111111111111111111111111/);
 assert.equal(calls[0].options.headers.get('X-Easel-User'),user);
 assert.equal(calls[0].options.cache,'no-store');
});

for(const change of [{actorId:'another-user'},{projectId:'another-ip'},{id:'2'.repeat(32)},{kind:'draft'}]) {
 test('navigation refuses a changed response binding '+Object.keys(change)[0],async()=>{
  const api=load(async()=>({ok:true,status:200,json:async()=>({items:[{...record,...change}]})}));
  await assert.rejects(api.fetchBusinessObject('workshop',record.id));
 });
}

test('a missing business object carries the real status to the historical card',async()=>{
 const api=load(async()=>({ok:false,status:404}));
 await assert.rejects(api.fetchBusinessObject('workshop',record.id),error=>error.status===404);
});

test('workshop deep links retain only a valid delivery ID in the current IP', () => {
 const {parseBusinessHash}=load();
 const project='a'.repeat(32), artifact='b'.repeat(32);
 assert.deepEqual(JSON.parse(JSON.stringify(parseBusinessHash(`#/business/default/workshop/${project}/artifact/${artifact}`))),
  {kind:'workshop',id:project,artifactId:artifact});
 assert.equal(parseBusinessHash(`#/business/default/workshop/${project}/artifact/unknown`),null);
 assert.equal(parseBusinessHash(`#/business/default/idea/idea-1/artifact/${artifact}`),null);
 assert.equal(parseBusinessHash(`#/business/other/workshop/${project}/artifact/${artifact}`),null);
});
