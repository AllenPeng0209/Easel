import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { createRoot } = require('react-dom/client');
const { parseHTML } = require('linkedom');

test('sidebar gives each session status a text label and summarizes live attention in collapsed entries', async () => {
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>');
  const document = window.document;
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key =>
    [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = createRoot(document.getElementById('root'));
  const account={id:'operator',username:'operator',display_name:'Operator',is_admin:false,active:true};
  const originalContext=React.useContext;
  // This sidebar is mounted as an authenticated project operator; the actual
  // sidebar, permissions hook and session-attention renderer still run.
  const reactWithContext={...React,useContext:context=>originalContext(context) || {
    user:account,current:{id:'default',name:'项目',role:'operator'},projects:[],refresh:async()=>{}}};
  const Sidebar = componentModule('components/Sidebar.tsx', { globals: { window, document },externals:{react:reactWithContext} }).default;
  const states = { a: 'approval', b: 'question', c: 'running', d: 'stopping', e: 'stop_failed', f: 'failed', g: 'completed-unread', h: 'approval', aa: 'recovering',
    archived: 'failed', unrelated: 'approval' };
  const sessions = Object.keys(states).filter(id => id !== 'unrelated').map(id => ({
    id, title: id, created: 1, messages: [{ role: 'user', content: id }], archived: id === 'archived',
  }));
  const byLabel = prefix => [...document.querySelectorAll('button')].find(button => button.getAttribute('aria-label')?.startsWith(prefix));
  try {
    await React.act(async () => root.render(React.createElement(Sidebar, {
      currentPage: 'chat', sessions, sessionStates: states, onPageChange() {},
      personas: [], selectedPersona: '', onPersonaChange() {}, onNewProfile() {},
    })));
    const history = byLabel('历史对话');
    const counts = /待审批 2.*待回答 1.*停止失败 1.*执行失败 1.*待核对任务 1.*正在停止 1.*运行中 1.*完成未读 1/;
    assert.match(history.getAttribute('aria-description'), counts);
    await React.act(async () => history.click());
    const labels = [...document.querySelectorAll('.session-attention-label')].map(item => item.textContent);
    for (const label of ['待审批', '待回答', '待核对任务', '运行中', '正在停止', '停止失败', '执行失败', '完成未读']) {
      assert.ok(labels.includes(label), label);
    }
    await React.act(async () => byLabel('收起侧栏').click());
    const ai = byLabel('AI 对话');
    assert.match(ai.getAttribute('aria-description'), counts);
    assert.equal(ai.querySelector('.session-attention-count')?.textContent, '9');
  } finally {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  }
});
