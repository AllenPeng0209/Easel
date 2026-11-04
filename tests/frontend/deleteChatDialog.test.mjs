import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

async function fixture(run) {
  const { window, document } = parseHTML('<html><body><button id="trigger">删除</button><div id="root"></div></body></html>');
  const trigger = document.getElementById('trigger');
  let focused = trigger;
  Object.defineProperty(document, 'activeElement', { get: () => focused });
  const prototype = window.HTMLElement.prototype;
  const originals = Object.fromEntries(['focus', 'matches'].map(name => [name, Object.getOwnPropertyDescriptor(prototype, name)]));
  const matches = prototype.matches;
  Object.defineProperties(prototype, {
    focus: { value() { focused = this; }, configurable: true },
    matches: { value(selector) { return selector === ':focus' ? focused === this : matches.call(this, selector); }, configurable: true },
  });
  const globals = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const calls = [];
  try {
    const Dialog = componentModule('components/DeleteChatDialog.tsx', {
      globals: { window, document, HTMLElement: window.HTMLElement },
    }).default;
    const update = (open, props = {}) => React.act(async () => root.render(open ? React.createElement(Dialog, {
      sessionTitle: '有消息的对话', onDelete() { calls.push('delete'); return true; },
      onArchive() { calls.push('archive'); return true; }, onClose() { calls.push('close'); }, ...props,
    }) : null));
    const click = selector => React.act(async () => document.querySelector(selector).click());
    await run({ document, window, update, click, trigger, calls });
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(originals)) {
      if (descriptor) Object.defineProperty(prototype, name, descriptor);
      else delete prototype[name];
    }
    for (const [name, descriptor] of Object.entries(globals)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
}

test('delete confirmation lists exact scope and dismissal leaves the conversation untouched', async () => {
  await fixture(async ({ document, update, click, trigger, calls }) => {
    await update(true);
    const dialog = document.querySelector('[data-delete-chat-dialog]');
    assert.match(dialog.textContent, /有消息的对话.*浏览器.*Easel.*正文.*删除标记.*原生历史.*保留/s);
    assert.deepEqual(calls, []);
    assert.equal(document.activeElement, dialog.querySelector('[data-delete-chat-dismiss]'));
    await click('[data-delete-chat-dismiss]');
    assert.deepEqual(calls, ['close']);
    await update(false);
    assert.equal(document.activeElement, trigger);
  });
});

test('delete waits for an exact result, locks duplicates and preserves explicit archive fallback on failure', async () => {
  await fixture(async ({ document, window, update, click, calls }) => {
    let complete;
    await update(true, { onDelete() { calls.push('delete'); return new Promise(resolve => { complete = resolve; }); } });
    const action = document.querySelector('[data-delete-chat-action=delete]');
    await React.act(async () => { action.click(); action.click(); });
    assert.deepEqual(calls, ['delete']);
    assert.equal(action.hasAttribute('disabled'), true);
    assert.equal(document.querySelector('[data-delete-chat-dismiss]').hasAttribute('disabled'), true);
    const escape = new window.Event('keydown', { bubbles: true, cancelable: true });
    Object.defineProperty(escape, 'key', { value: 'Escape' });
    await React.act(async () => document.dispatchEvent(escape));
    assert.deepEqual(calls, ['delete']);
    await React.act(async () => complete(false));
    assert.match(document.querySelector('[data-delete-chat-error]').textContent, /未确认.*保留/);
    assert.equal(action.textContent, '重试删除');
    assert.deepEqual(calls, ['delete']);
    await click('[data-delete-chat-action=archive]');
    assert.deepEqual(calls, ['delete', 'archive', 'close']);
  });
});

test('retry is explicit and a late result cannot close a replacement dialog', async () => {
  await fixture(async ({ document, update, click, calls }) => {
    let complete;
    await update(true, { onDelete() {
      calls.push('attempt');
      if (calls.length === 1) return false;
      return new Promise(resolve => { complete = resolve; });
    } });
    await click('[data-delete-chat-action=delete]');
    assert.deepEqual(calls, ['attempt']);
    assert.match(document.querySelector('[data-delete-chat-error]').textContent, /未确认/);
    await click('[data-delete-chat-action=delete]');
    assert.deepEqual(calls, ['attempt', 'attempt']);
    await update(false);
    await update(true, { sessionTitle: '另一条对话' });
    await React.act(async () => complete(true));
    assert.deepEqual(calls, ['attempt', 'attempt']);
    assert.match(document.querySelector('[data-delete-chat-dialog]').textContent, /另一条对话/);
    await click('[data-delete-chat-action=delete]');
    assert.deepEqual(calls, ['attempt', 'attempt', 'delete', 'close']);
  });
});
