import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const planId = 'a'.repeat(32);
const plan = (revision = 1, state = 'proposed', text = '已保存方案') => ({
  planId, revision, digest: String(revision).repeat(64), text, state,
  actorId: 'owner', projectId: 'scoped', sessionId: 'session', proposedTurnId: 'proposal', callId: 'call', createdAt: 10,
});

async function fixture(run, canOperate = true) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const requests = [];
  const executions = [];
  try {
    const Panel = componentModule('components/ChatPlanPanel.tsx', { user: 'owner', project: 'scoped', globals: {
      window, document, Headers, Response, fetch: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })),
    } }).default;
    await React.act(() => root.render(React.createElement(Panel, {
      sessionId: 'session', canOperate, onExecute: value => { executions.push(value); return false; },
    })));
    const resolve = (index, data, status = 200) => React.act(async () => requests[index].resolve(Response.json(data, { status })));
    const click = selector => React.act(async () => document.querySelector(selector).click());
    const input = (node, value) => React.act(async () => {
      let prototype = Object.getPrototypeOf(node);
      while (prototype && !Object.getOwnPropertyDescriptor(prototype, 'value')?.set) prototype = Object.getPrototypeOf(prototype);
      const setter = prototype && Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
      if (setter) setter.call(node, value); else node.value = value;
      node.dispatchEvent(new window.Event('input', { bubbles: true }));
      node.dispatchEvent(new window.Event('change', { bubbles: true }));
    });
    const submit = () => React.act(async () => document.querySelector('form').dispatchEvent(
      new window.Event('submit', { bubbles: true, cancelable: true }),
    ));
    await run({ document, requests, executions, resolve, click, input, submit });
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
}

test('view, exact approve and explicit execute are independent actions on the same version', async () => {
  await fixture(async ({ document, requests, executions, resolve, click }) => {
    const proposed = plan();
    await resolve(0, { plans: [proposed], latest: proposed });
    assert.equal(document.querySelector('[data-approve-chat-plan]'), null);
    await click('[data-chat-plan-preview]');
    assert.equal(requests.length, 1);
    assert.match(document.querySelector('[data-chat-plan-body]').textContent, /已保存方案/);
    await click('[data-approve-chat-plan]');
    assert.equal(requests[1].url, '/api/projects/scoped/chat/plans/session/approve');
    assert.deepEqual(JSON.parse(requests[1].options.body), { planId, revision: 1, digest: proposed.digest });
    const approved = plan(1, 'approved');
    await resolve(1, approved);
    assert.deepEqual(executions, []);
    await click('[data-execute-chat-plan]');
    assert.equal(executions.length, 1);
    assert.equal(executions[0].digest, proposed.digest);
    assert.match(document.querySelector('[data-chat-plan-notice]').textContent, /未确认/);
    assert.equal(document.querySelector('[data-execute-chat-plan]')?.hasAttribute('disabled') ?? true, true);
  });
});

test('read-only view never offers edit, approve, or execute', async () => {
  await fixture(async ({ document, requests, resolve, click }) => {
    const approved = plan(1, 'approved');
    await resolve(0, { plans: [approved], latest: approved });
    await click('[data-chat-plan-preview]');
    assert.match(document.querySelector('[data-chat-plan-body]').textContent, /已保存方案/);
    assert.equal(document.querySelector('[data-approve-chat-plan], [data-execute-chat-plan], [data-edit-chat-plan]'), null);
    assert.equal(requests.length, 1);
  }, false);
});


test('editing sends the exact base version and requires a fresh preview and approval for the revision', async () => {
  await fixture(async ({ document, requests, resolve, click, input, submit }) => {
    const approved = plan(1, 'approved');
    await resolve(0, { plans: [approved], latest: approved });
    await click('[data-chat-plan-preview]');
    await click('[data-edit-chat-plan]');
    const field = document.querySelector('textarea');
    await input(field, '修改后的完整方案');
    await submit();
    assert.deepEqual(JSON.parse(requests[1].options.body), { text: '修改后的完整方案', planId,
      revision: 1, digest: approved.digest });
    const next = plan(2, 'proposed', '修改后的完整方案');
    await resolve(1, next);
    assert.equal(document.querySelector('[data-approve-chat-plan], [data-execute-chat-plan]'), null);
    await resolve(2, { plans: [plan(1, 'superseded'), next], latest: next });
    await click('[data-chat-plan-preview="1"]');
    assert.equal(document.querySelector('[data-approve-chat-plan], [data-execute-chat-plan]'), null);
    await click('[data-chat-plan-preview="2"]');
    assert.ok(document.querySelector('[data-approve-chat-plan]'));
    assert.equal(document.querySelector('[data-execute-chat-plan]'), null);
  });
});

test('initial brief generation creates a proposal only, and a stale edit keeps the draft visible', async () => {
  await fixture(async ({ document, requests, resolve, click, input, submit }) => {
    await resolve(0, { plans: [], latest: null });
    await input(document.querySelector('textarea'), '制作三条内容');
    await submit();
    assert.deepEqual(JSON.parse(requests[1].options.body), { brief: '制作三条内容' });
    const proposed = plan();
    await resolve(1, proposed);
    await resolve(2, { plans: [proposed], latest: proposed });
    await click('[data-chat-plan-preview]');
    await click('[data-edit-chat-plan]');
    await input(document.querySelector('textarea'), '我的草稿');
    await submit();
    await resolve(3, {}, 409);
    assert.match(document.querySelector('[data-chat-plan-error]').textContent, /版本已更新/);
    assert.equal(document.querySelector('textarea').value, '我的草稿');
    assert.equal(document.querySelector('[data-save-chat-plan]').hasAttribute('disabled'), true);
  });
});

test('a conflicting latest read cannot approve text that differs from the displayed revision', async () => {
  await fixture(async ({ document, requests, resolve }) => {
    const visible = plan();
    await resolve(0, { plans: [visible], latest: { ...visible, text: '不同的最新内容' } });
    assert.ok(document.querySelector('[data-chat-plan-error]'));
    assert.equal(document.querySelector('[data-chat-plan-preview], [data-approve-chat-plan]'), null);
    assert.equal(requests.length, 1);
  });
});
