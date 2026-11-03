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

test('super agent groups project tasks and submits only the exact pending question receipt', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true }, document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true },
  });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Panel = componentModule('components/SuperagentPanel.tsx', { globals: { window, document, Error } }).default;
    const waiting = task('waiting', 'ip-a', 'waiting_input', { question, steps: [
      { id: 'step-1', kind: 'prepare', status: 'completed', attempts: 1, retrySafe: true, receipt: { fileId: 'artifact-one' } },
    ] });
    const failed = task('failed', 'ip-b', 'failed', { steps: [
      { id: 'step-2', kind: 'publish', status: 'failed', attempts: 1, retrySafe: false, error: '已有外部提交待核对' },
    ] });
    const retryable = task('retryable', 'ip-b', 'failed', { steps: [
      { id: 'step-3', kind: 'prepare', status: 'failed', attempts: 1, retrySafe: true },
    ] });
    let settle;
    const answers = [];
    const retries = [];
    await React.act(async () => root.render(React.createElement(Panel, {
      projects: [{ id: 'ip-a', name: '品牌 A' }, { id: 'ip-b', name: '品牌 B' }],
      loadTasks: async () => [waiting, failed, retryable],
      createTask: async value => task('new', value.projectId, 'queued'),
      answerTask(id, value) { answers.push([id, value]); return new Promise(resolve => { settle = resolve; }); },
      stopTask: async id => task(id, 'ip-a', 'stopped'),
      retryTask: async (id, scope) => { retries.push([id, scope.projectId]); return task(id, 'ip-b', 'queued'); },
    })));
    assert.equal(document.querySelectorAll('[data-superagent-project]').length, 2);
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
    assert.deepEqual(retries, [['retryable', 'ip-b']]);
    assert.equal(document.querySelector('[data-superagent-task="retryable"]').dataset.superagentStatus, 'queued');
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
