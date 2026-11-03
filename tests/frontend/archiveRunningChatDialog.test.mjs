import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

async function fixture(run) {
  const { window, document } = parseHTML('<html><body><button id="trigger">归档</button><div id="root"></div></body></html>');
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
    const Dialog = componentModule('components/ArchiveRunningChatDialog.tsx', {
      globals: { window, document, HTMLElement: window.HTMLElement },
    }).default;
    const update = (open, props = {}) => React.act(async () => root.render(open ? React.createElement(Dialog, {
      sessionTitle: '运营方案', onContinue() { calls.push('continue'); return true; },
      onStopAndArchive() { calls.push('stop'); return true; }, onClose() { calls.push('close'); }, ...props,
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

test('opening and dismissing the archive explanation never changes the chat and returns focus', async () => {
  await fixture(async ({ document, update, click, trigger, calls }) => {
    await update(true);
    const dialog = document.querySelector('[data-archive-running-dialog]');
    assert.match(dialog.textContent, /运营方案.*继续.*后台.*停止/s);
    assert.deepEqual(calls, []);
    assert.equal(document.activeElement, dialog.querySelector('[data-archive-running-dismiss]'));
    await click('[data-archive-running-dismiss]');
    assert.deepEqual(calls, ['close']);
    await update(false);
    assert.equal(document.activeElement, trigger);
  });
});

test('an unconfirmed stop stays visible, locks duplicate choices, and a separate continue is explicit', async () => {
  await fixture(async ({ document, window, update, click, calls }) => {
    let complete;
    await update(true, { onStopAndArchive() {
      calls.push('stop');
      return new Promise(resolve => { complete = resolve; });
    } });
    const stop = document.querySelector('[data-archive-running-action=stop]');
    await React.act(async () => { stop.click(); stop.click(); });
    assert.deepEqual(calls, ['stop']);
    assert.equal(stop.hasAttribute('disabled'), true);
    assert.equal(document.querySelector('[data-archive-running-action=continue]').hasAttribute('disabled'), true);
    assert.equal(document.querySelector('[data-archive-running-dismiss]').hasAttribute('disabled'), true);
    const escape = new window.Event('keydown', { bubbles: true, cancelable: true });
    Object.defineProperty(escape, 'key', { value: 'Escape' });
    await React.act(async () => document.dispatchEvent(escape));
    assert.deepEqual(calls, ['stop']);
    await React.act(async () => complete(false));
    assert.match(document.querySelector('[data-archive-running-error]').textContent, /未确认.*保留/);
    assert.deepEqual(calls, ['stop']);
    await click('[data-archive-running-action=continue]');
    assert.deepEqual(calls, ['stop', 'continue', 'close']);
  });
});

test('a late completion from an old archive dialog cannot dismiss its replacement', async () => {
  await fixture(async ({ document, update, click, calls }) => {
    let complete;
    await update(true, { onStopAndArchive() {
      calls.push('old-stop');
      return new Promise(resolve => { complete = resolve; });
    } });
    await click('[data-archive-running-action=stop]');
    await update(false);
    await update(true, { sessionTitle: '新对话' });
    await React.act(async () => complete(true));
    assert.deepEqual(calls, ['old-stop']);
    assert.match(document.querySelector('[data-archive-running-dialog]').textContent, /新对话/);
    await click('[data-archive-running-action=stop]');
    assert.deepEqual(calls, ['old-stop', 'stop', 'close']);
  });
});
