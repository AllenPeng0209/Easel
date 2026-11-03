import assert from 'node:assert/strict';
import {test} from 'node:test';
import {componentModule} from './agentMessagesHarness.mjs';
const user='navigation-actor';
const record={schemaVersion:1,actorId:user,projectId:'default',kind:'idea',id:'1'.repeat(12),title:'真实选题',summary:'摘要',
 status:{code:'idea_pending',label:'待做选题',source:'stored'},version:{revision:null,sha256:null},snapshotHash:'a'.repeat(64),
 observedAt:100,details:{},actions:[{kind:'open',target:'ideas',entityId:'1'.repeat(12),label:'查看选题'}]};
const load=(fetch=()=>{})=>componentModule('lib/businessObjectApi.ts',{user,globals:{Headers,URLSearchParams,fetch}});

test('business deep links preserve exact IP, kind and entity ID',()=>{
 const {parseBusinessHash}=load();
 assert.deepEqual(JSON.parse(JSON.stringify(parseBusinessHash('#/business/default/idea/'+record.id))),{kind:'idea',id:record.id});
 assert.equal(parseBusinessHash('#/business/default/idea/Legacy_idea-2026').id,'Legacy_idea-2026');
 assert.equal(parseBusinessHash('#/business/other/idea/'+record.id),null);
 for(const suffix of ['/draft/'+record.id,'/workshop/'+record.id,'/idea/../../private','/idea/%2fsecret','/idea/'+record.id+'?url=https://example.invalid']) {
  assert.equal(parseBusinessHash('#/business/default'+suffix),null);
 }
 assert.equal(parseBusinessHash('https://example.invalid'),null);
});

test('read-only navigation request uses the actual authenticated scope and a single inspected object',async()=>{
 const calls=[];
 const api=load(async(url,options)=>{calls.push({url,options});return{ok:true,status:200,json:async()=>({items:[record]})};});
 const value=await api.fetchBusinessObject('idea',record.id);
 assert.equal(value.id,record.id);assert.equal(calls.length,1);
 assert.match(calls[0].url,/chat\/business-objects\?action=inspect&kind=idea&id=111111111111/);
 assert.equal(calls[0].options.headers.get('X-Easel-User'),user);
 assert.equal(calls[0].options.cache,'no-store');
});

for(const change of [{actorId:'another-user'},{projectId:'another-ip'},{id:'2'.repeat(12)},{kind:'workshop'}]) {
 test('navigation refuses a changed response binding '+Object.keys(change)[0],async()=>{
  const api=load(async()=>({ok:true,status:200,json:async()=>({items:[{...record,...change}]})}));
  await assert.rejects(api.fetchBusinessObject('idea',record.id));
 });
}

test('a missing business object carries the real status to the historical card',async()=>{
 const api=load(async()=>({ok:false,status:404}));
 await assert.rejects(api.fetchBusinessObject('idea',record.id),error=>error.status===404);
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
