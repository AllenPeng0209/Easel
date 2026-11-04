import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const user='event-actor';
const record = status => ({id:'a'.repeat(32),actorId:user,projectId:'default',payloadHash:'b'.repeat(64),
  state:'approved',createdAt:1000,expiresAt:2000,operationId:'c'.repeat(32),
  subject:{schemaVersion:1,project:{id:'default',name:'测试IP'},platform:{id:'zhihu',name:'知乎'},
    account:{id:'id:account-one',name:'发布账号'},mode:'publish_now',scheduledAt:null,audience:'public',
    title:'真实回执标题',body:'原版本正文',tags:'',persona:'',media:[]},
  result:{status,phase:'submit_attempted',reason:'submission_not_acknowledged',operationId:'c'.repeat(32),
    payloadHash:'b'.repeat(64),accountId:'id:account-one',platform:'zhihu',mode:'publish_now'}});
const event = (id,status,at=10) => ({id,kind:'publication',publication:record(status),completedAt:at});

test('publication event survives the real transport reducer and renders a typed receipt',()=>{
  const {reduceAgentEvents}=componentModule('lib/agentEvents.ts',{user});
  const events=reduceAgentEvents([],event('one','unknown'));
  assert.equal(events.length,1);
  const Panel=componentModule('components/AgentEventPanel.tsx',{user}).default;
  const html=renderToStaticMarkup(React.createElement(Panel,{events,settled:true}));
  assert.match(html,/真实回执标题/);assert.match(html,/待核实/);
  assert.doesNotMatch(html,/平台公开确认/);
});

test('new receipt snapshots replace the same operation card and stale replay cannot revive it',()=>{
  const {reduceAgentEvents}=componentModule('lib/agentEvents.ts',{user});
  const first=reduceAgentEvents([],event('old','executing',10));
  const done=reduceAgentEvents(first,event('new','unknown',20));
  assert.equal(done.length,1);assert.equal(done[0].publication.result.status,'unknown');
  const replay=reduceAgentEvents(done,event('stale','executing',5));
  assert.equal(replay[0].publication.result.status,'unknown');
});
