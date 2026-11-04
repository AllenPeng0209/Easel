import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const now = Date.now();
const question = {
  id: 'question-1', prompt: '确认方案版本？', options: ['批准', '退回'],
  requiresHuman: true, deadlineAt: now + 60_000, createdAt: now, status: 'pending',
};
const task = (id, projectId, status, extra = {}) => ({
  id, actorId: 'owner', projectId, title: `任务 ${id}`, brief: '制作资料',
  status, createdAt: now, updatedAt: now, steps: [], ...extra,
});

test('super agent keeps only current IP tasks and submits the exact pending question receipt', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true }, document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true },
  });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Panel = componentModule('components/SuperagentPanel.tsx', { project: 'ip-a', globals: { window, document, Error } }).default;
    const waiting = task('waiting', 'ip-a', 'waiting_input', { question, steps: [
      { id: 'step-1', kind: 'prepare', status: 'completed', attempts: 1, retrySafe: true, receipt: { fileId: 'artifact-one' } },
    ] });
    const failed = task('failed', 'ip-a', 'failed', { steps: [
      { id: 'step-2', kind: 'publish', status: 'failed', attempts: 1, retrySafe: false, error: '已有外部提交待核对' },
    ] });
    const retryable = task('retryable', 'ip-a', 'failed', { steps: [
      { id: 'step-3', kind: 'prepare', status: 'failed', attempts: 1, retrySafe: true },
    ] });
    let settle;
    const answers = [];
    const retries = [];
    await React.act(async () => root.render(React.createElement(Panel, {
      projects: [{ id: 'ip-a', name: '品牌 A' }, { id: 'ip-b', name: '品牌 B' }],
      loadTasks: async () => [waiting, failed, retryable, task('foreign', 'ip-b', 'running')],
      createTask: async value => task('new', value.projectId, 'queued'),
      answerTask(id, value) { answers.push([id, value]); return new Promise(resolve => { settle = resolve; }); },
      stopTask: async id => task(id, 'ip-a', 'stopped'),
      retryTask: async (id, scope) => { retries.push([id, scope.projectId]); return task(id, 'ip-a', 'queued'); },
    })));
    assert.equal(document.querySelectorAll('[data-superagent-project]').length, 1);
    assert.equal(document.querySelector('[data-superagent-task="foreign"]'), null);
    assert.equal(document.querySelector('select'), null, 'The current IP does not need another project selector');
    assert.match(document.querySelector('[data-superagent-project="ip-a"]').textContent, /品牌 A.*待人工/s);
    assert.match(document.querySelector('[data-superagent-task="waiting"]').textContent, /artifact-one/);
    assert.ok(document.querySelector('[data-superagent-question-deadline]'));
    assert.equal(document.querySelector('[data-superagent-task="failed"] [data-superagent-retry]'), null);

    const approve = document.querySelector('[data-superagent-answer="批准"]');
    await React.act(async () => { approve.click(); approve.click(); });
    assert.deepEqual(JSON.parse(JSON.stringify(answers)), [['waiting', { projectId: 'ip-a', questionId: 'question-1', answer: '批准' }]]);
    assert.equal(approve.hasAttribute('disabled'), true);
    await React.act(async () => settle({ ...waiting, status: 'running',
      question: { ...question, status: 'answered', answer: '批准', resolvedBy: 'human' } }));
    assert.equal(document.querySelector('[data-superagent-answer="批准"]'), null);
    assert.match(document.querySelector('[data-superagent-question-resolution]').textContent, /人工.*批准/s);
    await React.act(async () => document.querySelector('[data-superagent-task="retryable"] [data-superagent-retry]').click());
    assert.deepEqual(retries, [['retryable', 'ip-a']]);
    assert.equal(document.querySelector('[data-superagent-task="retryable"]').dataset.superagentStatus, 'queued');
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});

test('the chat task adapter carries the current IP URL on both list and stop requests', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true }, document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true },
  });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const project = 'a'.repeat(16);
  const running = task('a'.repeat(32), project, 'running');
  const calls = [];
  try {
    const adapter = componentModule('components/SuperagentPage.tsx', { project, user: 'owner', globals: {
      window, document, Error, Headers,
      fetch: async (url, options) => {
        calls.push({ url, options });
        return { ok: true, json: async () => options.method === 'POST'
          ? { ...running, status: 'stopped' } : { tasks: [running] } };
      },
    } });
    function ChatTasks() { return adapter.useChatSuperagent('chat-a', true).tasks; }
    await React.act(async () => root.render(React.createElement(ChatTasks)));
    const stop = document.querySelector('[data-superagent-stop]');
    assert.ok(stop);
    await React.act(async () => stop.click());
    assert.equal(calls[0].url, `/api/projects/${project}/superagent/tasks`);
    const mutation = calls.find(call => call.options.method === 'POST');
    assert.equal(mutation.url, `/api/projects/${project}/superagent/tasks/${running.id}/stop`);
    assert.deepEqual(JSON.parse(mutation.options.body), { projectId: project });
    assert.equal(mutation.options.headers.get('X-Easel-User'), 'owner');
    assert.equal(document.querySelector('[data-superagent-task]').dataset.superagentStatus, 'stopped');
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});

test('new tasks use the current IP automatically and stale controls cannot survive a permission downgrade', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true }, document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true },
  });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const project = 'a'.repeat(16);
  const calls = [];
  const pending = task('a'.repeat(32), project, 'running');
  try {
    const Panel = componentModule('components/SuperagentPanel.tsx', { project, globals: { window, document, Error } }).default;
    const props = {
      projects: [{ id: project, name: '当前品牌' }, { id: 'b'.repeat(16), name: '其他品牌' }],
      loadTasks: async () => [pending],
      createTask: async request => { calls.push(request); return task('new', request.projectId, 'queued'); },
      answerTask: async () => pending,
      stopTask: async () => { calls.push('stop'); return { ...pending, status: 'stopped' }; },
      retryTask: async () => pending,
    };
    await React.act(async () => root.render(React.createElement(Panel, props)));
    const handler = (element, key) => element[Object.keys(element).find(name => name.startsWith('__reactProps'))][key];
    const form = document.querySelector('[data-superagent-create]');
    const title = form.querySelector('input');
    const brief = form.querySelector('textarea');
    await React.act(async () => {
      handler(title, 'onChange')({ target: { value: 'Own task' } });
      handler(brief, 'onChange')({ target: { value: 'Current IP output' } });
    });
    await React.act(async () => handler(form, 'onSubmit')({ preventDefault() {} }));
    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [{ projectId: project, title: 'Own task', brief: 'Current IP output' }]);
    const staleStop = handler(document.querySelector('[data-superagent-stop]'), 'onClick');
    await React.act(async () => root.render(React.createElement(Panel, {
      ...props, projects: [{ id: project, name: '当前品牌', canOperate: false }],
    })));
    await React.act(async () => staleStop());
    assert.equal(calls.includes('stop'), false);
    assert.equal(document.querySelector('select'), null);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
