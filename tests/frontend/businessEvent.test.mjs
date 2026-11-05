import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createRequire} from 'node:module';
import {componentModule} from './agentMessagesHarness.mjs';
const require=createRequire(new URL('../../web/frontend/package.json',import.meta.url));
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const user='event-actor';
const card=(at=10,title='真实工程')=>({schemaVersion:1,actorId:user,projectId:'default',kind:'workshop',id:'1'.repeat(32),
 title,summary:'真实保存的内容',status:{code:'doing',label:'制作进行中',source:'stored'},
 version:{revision:null,sha256:null},snapshotHash:'a'.repeat(64),observedAt:at,
 details:{project:{id:'1'.repeat(32),title,brief:'真实保存的内容',stage:'producing'}},
 actions:[{kind:'open',target:'workshop',entityId:'1'.repeat(32),label:'查看工坊'}]});
const event=(id,at=10,callId='native-call',title)=>({id,kind:'business',name:'easel_business_objects',callId,
 business:card(at,title),completedAt:at});
const render=events=>{
 const Panel=componentModule('components/AgentEventPanel.tsx',{user}).default;
 return renderToStaticMarkup(React.createElement(Panel,{events,settled:true}));
};

test('real business transport reaches its keyed view with the original native call identity',()=>{
 const {reduceAgentEvents}=componentModule('lib/agentEvents.ts',{user});
 const events=reduceAgentEvents([],event('first'));
 assert.equal(events.length,1);
 const html=render(events);
 assert.match(html,/data-business-object="11111111111111111111111111111111"/);
 assert.match(html,/真实工程/);assert.match(html,/制作进行中/);
});

test('same call/entity replays update once but independent calls retain their own observation',()=>{
 const {reduceAgentEvents}=componentModule('lib/agentEvents.ts',{user});
 let items=reduceAgentEvents([],event('one',10));
 items=reduceAgentEvents(items,event('two',20,'native-call','新观测'));
 items=reduceAgentEvents(items,event('old',5,'native-call','旧观测'));
 assert.equal(items.length,1);assert.equal(items[0].business.title,'新观测');
 items=reduceAgentEvents(items,event('new-call',30,'another-native-call','另一次调用'));
 assert.equal(items.length,2);
});

test('unregistered wire names and out-of-scope entities never expose a business body',()=>{
 const other={...event('one'),name:'model_invented_tool',business:card(10,'不应展示的业务正文')};
 assert.doesNotMatch(render([other]),/data-business-object=|不应展示的业务正文/);
 const wrong={...event('two'),business:{...card(10,'另一个用户的正文'),actorId:'another-actor'}};
 assert.doesNotMatch(render([wrong]),/data-business-object=|另一个用户的正文/);
});

test('denied and incomplete tool calls cannot become successful business cards',()=>{
 const html=render([{...event('denied'),kind:'tool',status:'denied',output:{items:[card()]}}]);
 assert.doesNotMatch(html,/data-business-object=/);
 assert.match(html,/未授权/);
});

test('the source workshop snapshot keeps its historical observation without a live refresh action', () => {
 const html=render([event('source',10,'host:workshop:turn')]);
 assert.match(html,/本轮引用的工坊快照/);
 assert.match(html,/查看工坊/);
 assert.doesNotMatch(html,/刷新业务对象/);
});
