import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const tool = (id, callId, runId, fields = {}) => ({ id, callId, runId, kind: 'tool', name: 'safe_tool', status: 'failed', ...fields });

async function fixture(run) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const originalFocus = Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, 'focus');
  const focused = [];
  Object.defineProperty(window.HTMLElement.prototype, 'focus', { value() { focused.push(this); }, configurable: true });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Bubble = componentModule('components/MessageBubble.tsx', { globals: { window, document } }).default;
    const render = (...messages) => React.act(() => root.render(React.createElement(React.Fragment, {},
      messages.map((data, index) => React.createElement('div', { key: index, 'data-message-fixture': index },
        React.createElement(Bubble, { message: { role: 'assistant', content: '', ...data } }))))));
    const click = target => React.act(() => target.dispatchEvent(new window.Event('click', { bubbles: true })));
    await run({ render, click, document, focused });
  } finally {
    await React.act(() => root.unmount());
    if (originalFocus) Object.defineProperty(window.HTMLElement.prototype, 'focus', originalFocus);
    else delete window.HTMLElement.prototype.focus;
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
}

test('locating a failed row opens its ancestors and focuses only its exact message, run and call', async () => {
  await fixture(async ({ render, click, document, focused }) => {
    const parent = tool('parent', 'parent', 'run-two', { status: 'completed' });
    const target = tool('right', 'reused', 'run-two', { parentCallId: 'parent', input: { target: 'matched' } });
    const wrongRun = tool('wrong', 'reused', 'run-one', { input: { target: 'wrong run' } });
    await render({ events: [parent, target, wrongRun], timeline: [target] }, { events: [target] });
    const local = document.querySelector('[data-message-fixture="0"]');
    const other = document.querySelector('[data-message-fixture="1"]');
    const parentButton = local.querySelector('[data-tool-call=parent] [data-disclosure-row]');
    assert.ok(parentButton);
    assert.equal(local.querySelector('[data-run-id=run-two][data-tool-call=reused]'), null);
    await click(local.querySelector('[data-agent-timeline] summary'));
    await click(parentButton);
    assert.ok(local.querySelector('[data-run-id=run-two][data-tool-call=reused]'));
    await click(parentButton);
    assert.equal(local.querySelector('[data-run-id=run-two][data-tool-call=reused]'), null);
    await click(local.querySelector('[data-locate-failed-tool]'));
    const match = local.querySelector('[data-run-id=run-two][data-tool-call=reused]');
    assert.ok(match);
    assert.equal(match.hasAttribute('data-tool-located'), true);
    assert.equal(focused.at(-1), match.querySelector('[data-disclosure-row]'));
    assert.match(match.textContent, /matched/);
    assert.equal(local.querySelector('[data-run-id=run-one][data-tool-call=reused]').hasAttribute('data-tool-located'), false);
    assert.equal(other.querySelector('[data-tool-call=reused]').hasAttribute('data-tool-located'), false);
  });
});

test('a missing local match never jumps to the same run and call in another message', async () => {
  await fixture(async ({ render, click, document, focused }) => {
    const missing = tool('missing', 'duplicate', 'run');
    const success = tool('success', 'complete', 'run', { status: 'completed' });
    await render({ timeline: [missing, success] }, { events: [missing] });
    const local = document.querySelector('[data-message-fixture="0"]');
    assert.equal(local.querySelectorAll('[data-locate-failed-tool]').length, 1);
    await click(local.querySelector('[data-agent-timeline] summary'));
    await click(local.querySelector('[data-locate-failed-tool]'));
    assert.match(local.querySelector('[data-tool-locate-missing]').textContent, /没有.*工具详情/);
    assert.equal(focused.length, 0);
    assert.equal(document.querySelector('[data-tool-located]'), null);
  });
});

test('an old failed observation without a run identity cannot guess a local tool row', async () => {
  await fixture(async ({ render, click, document, focused }) => {
    const local = tool('present', 'shared', 'current-run');
    await render({ events: [local], timeline: [tool('unscoped', 'shared', undefined)] });
    await click(document.querySelector('[data-agent-timeline] summary'));
    await click(document.querySelector('[data-locate-failed-tool]'));
    assert.ok(document.querySelector('[data-tool-locate-missing]'));
    assert.equal(document.querySelector('[data-tool-located]'), null);
    assert.equal(focused.length, 0);
  });
});
