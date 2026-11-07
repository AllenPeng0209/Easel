import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const { renderToStaticMarkup } = require('react-dom/server');

const tool = (callId, status) => ({ id: callId, callId, runId: 'run', kind: 'tool', name: callId, status,
  input: { note: `detail ${callId}` } });
const events = [tool('normal', 'completed'), tool('failed', 'failed')];
const timeline = events.map(({ id, callId, runId, kind, status }) => ({ id, callId, runId, kind, status }));
const message = { role: 'assistant', content: '', events, timeline, reasoningSummary: { text: '公开详情', truncated: false } };

test('compact keeps optional and failed details folded, while detailed opens defaults', () => {
  const Bubble = componentModule('components/MessageBubble.tsx').default;
  const show = detailMode => parseHTML(renderToStaticMarkup(React.createElement(Bubble, {
    message, detailMode, foldWorkDetails: true,
  }))).document;
  const compact = show('compact');
  assert.equal(compact.querySelector('[data-agent-timeline]').hasAttribute('open'), false);
  assert.equal(compact.querySelector('[data-reasoning-summary-body]'), null);
  assert.equal(compact.querySelector('[data-tool-call=normal] .agent-tool__details'), null);
  assert.equal(compact.querySelector('[data-tool-call=failed] .agent-tool__details'), null);
  assert.match(compact.querySelector('[data-tool-call=failed]').textContent, /失败/);
  assert.match(compact.querySelector('[data-agent-timeline] summary').textContent, /1 个失败/);
  const detailed = show('detailed');
  assert.equal(detailed.querySelector('[data-agent-timeline]').hasAttribute('open'), true);
  assert.ok(detailed.querySelector('[data-reasoning-summary-body]'));
  assert.ok(detailed.querySelector('[data-tool-call=normal] .agent-tool__details'));
});

test('manual choices survive completion and mode changes without reopening untouched compact details', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Bubble = componentModule('components/MessageBubble.tsx', { globals: { window, document } }).default;
    const update = (detailMode, foldWorkDetails, isStreaming = false, key = 'manual') => React.act(() =>
      root.render(React.createElement(Bubble, {
        key, message, events, timeline, reasoningSummary: message.reasoningSummary, detailMode, foldWorkDetails, isStreaming,
      })));
    const click = selector => React.act(() => document.querySelector(selector)
      .dispatchEvent(new window.Event('click', { bubbles: true, cancelable: true })));
    await update('compact', false, true);
    assert.equal(document.querySelector('[data-reasoning-summary-body]'), null);
    assert.equal(document.querySelector('[data-tool-call=normal] .agent-tool__details'), null);
    assert.equal(document.querySelector('[data-tool-call=failed] .agent-tool__details'), null);
    await click('[data-reasoning-summary] button');
    await click('[data-tool-call=normal] [data-disclosure-row]');
    await click('[data-agent-timeline] summary');
    await update('compact', false);
    assert.equal(document.querySelector('[data-agent-timeline]').hasAttribute('open'), true);
    await update('compact', true);
    assert.equal(document.querySelector('[data-agent-timeline]').hasAttribute('open'), true);
    assert.ok(document.querySelector('[data-reasoning-summary-body]'));
    assert.ok(document.querySelector('[data-tool-call=normal] .agent-tool__details'));
    await click('[data-reasoning-summary] button');
    await click('[data-tool-call=normal] [data-disclosure-row]');
    await update('detailed', false);
    assert.equal(document.querySelector('[data-reasoning-summary-body]'), null);
    assert.equal(document.querySelector('[data-tool-call=normal] .agent-tool__details'), null);
    assert.ok(document.querySelector('[data-agent-timeline]').hasAttribute('open'));
    await update('compact', true);
    assert.ok(document.querySelector('[data-agent-timeline]').hasAttribute('open'));
    assert.equal(document.querySelector('[data-tool-call=failed] .agent-tool__details'), null);

    await update('detailed', false, true, 'focus');
    const focusedAction = document.querySelector('[data-locate-failed-tool]');
    await React.act(() => focusedAction.dispatchEvent(new window.Event('focusin', { bubbles: true })));
    await update('compact', true, false, 'focus');
    assert.equal(document.querySelector('[data-agent-timeline]').hasAttribute('open'), true,
      'automatic fold waits while focus is in the details');
    const blur = new window.Event('focusout', { bubbles: true });
    Object.defineProperty(blur, 'relatedTarget', { value: document.body });
    await React.act(() => focusedAction.dispatchEvent(blur));
    assert.equal(document.querySelector('[data-agent-timeline]').hasAttribute('open'), false);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
