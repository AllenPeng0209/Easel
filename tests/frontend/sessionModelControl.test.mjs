import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { parseHTML } = require('linkedom');
const capability = { runtime: 'codex', model: 'runtime-default', canSelect: true,
  options: [{ id: 'model-a', label: '模型 A' }, { id: 'model-b', label: '模型 B' }] };

test('shows a compact next-turn choice and keeps the running model in its tooltip', () => {
  const Control = componentModule('components/SessionModelControl.tsx').default;
  const output = renderToStaticMarkup(React.createElement(Control, {
    capability, nextModel: 'model-b', turnModel: 'model-a', running: true, onChangeModel() {},
  }));
  const document = parseHTML(output).document;
  assert.match(document.querySelector('[data-session-model-control]').getAttribute('title') || '', /Codex.*本轮请求模型.*模型 A/);
  const next = document.querySelector('[data-next-session-model]');
  assert.equal(next.querySelector('option[selected]').getAttribute('value'), 'model-b');
  assert.equal(next.getAttribute('aria-label'), '下一轮模型');
  assert.doesNotMatch(document.querySelector('[data-session-model-control]').textContent, /本轮请求模型|下一轮/);
});

test('does not offer switching for an unsupported engine and explains why', () => {
  const Control = componentModule('components/SessionModelControl.tsx').default;
  const output = renderToStaticMarkup(React.createElement(Control, {
    capability: { runtime: 'openclaw', model: null, options: [], canSelect: false, reason: '请新建对话切换模型。' },
    nextModel: 'retired-model', onChangeModel() {},
  }));
  assert.doesNotMatch(output, /<select/);
  assert.match(output, /OpenClaw.*模型未确认/);
  assert.match(output, /请新建对话切换模型/);
  assert.match(output, /data-reset-session-model.*清除原选择/s);
});

test('keeps a missing saved choice distinct from the default', () => {
  const Control = componentModule('components/SessionModelControl.tsx').default;
  const output = renderToStaticMarkup(React.createElement(Control, {
    capability, nextModel: 'retired-model-id', onChangeModel() {},
  }));
  assert.doesNotMatch(output, /retired-model-id/);
  assert.match(output, /之前选择的模型不可用/);
  const document = parseHTML(output).document;
  assert.equal(document.querySelector('option[selected]').hasAttribute('disabled'), true);
  assert.ok(document.querySelector('option[value=""]'));
});

test('a selection changes the next request only and never accepts an unknown option', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true }, document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true },
  });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Control = componentModule('components/SessionModelControl.tsx').default;
    const requested = [];
    await React.act(async () => root.render(React.createElement(Control, {
      capability, running: true, turnModel: 'model-a', onChangeModel: model => requested.push(model),
    })));
    const select = document.querySelector('[data-next-session-model]');
    for (const value of ['forged-model', 'model-b', '']) {
      Object.defineProperty(select, 'value', { configurable: true, value });
      await React.act(async () => select.dispatchEvent(new window.Event('change', { bubbles: true })));
    }
    assert.deepEqual(requested, ['model-b', null]);
    assert.match(document.querySelector('[data-session-model-control]').getAttribute('title'), /本轮请求模型.*模型 A/);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
