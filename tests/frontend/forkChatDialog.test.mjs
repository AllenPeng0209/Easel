import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

test('fork creation previews the exact cutoff and keeps the original until explicit open', async () => {
  const { window, document } = parseHTML('<html><body><p id="source">原答复</p><div id="root"></div></body></html>');
  const matches = window.HTMLElement.prototype.matches;
  const originalMatches = Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, 'matches');
  Object.defineProperty(window.HTMLElement.prototype, 'matches', { configurable: true, value(selector) {
    return selector === ':focus' ? document.activeElement === this : matches.call(this, selector);
  } });
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Dialog = componentModule('components/ForkChatDialog.tsx', { globals: {
      window, document, HTMLElement: window.HTMLElement,
    } }).default;
    const calls = [];
    let settle;
    await React.act(async () => root.render(React.createElement(Dialog, {
      sourceTitle: '来源对话', answerText: '截止答复 <script>原文</script>', messageCount: 4,
      onFork() { calls.push('create'); return new Promise(resolve => { settle = resolve; }); },
      onOpen(id) { calls.push(id); }, onClose() { calls.push('close'); },
    })));
    const dialog = document.querySelector('[data-fork-chat-dialog]');
    assert.match(dialog.textContent, /来源对话.*4 条.*独立.*引擎/s);
    assert.match(dialog.textContent, /截止答复 <script>原文<\/script>/);
    assert.equal(dialog.querySelector('script'), null);
    assert.deepEqual(calls, []);
    const create = dialog.querySelector('[data-fork-chat-create]');
    await React.act(async () => { create.click(); create.click(); });
    assert.deepEqual(calls, ['create']);
    assert.equal(create.hasAttribute('disabled'), true);
    await React.act(async () => settle({ id: 'child', title: '新分支' }));
    assert.deepEqual(calls, ['create']);
    assert.equal(document.getElementById('source').textContent, '原答复');
    assert.match(dialog.textContent, /新分支/);
    await React.act(async () => dialog.querySelector('[data-fork-chat-open]').click());
    assert.deepEqual(calls, ['create', 'child', 'close']);
  } finally {
    await React.act(() => root.unmount());
    if (originalMatches) Object.defineProperty(window.HTMLElement.prototype, 'matches', originalMatches);
    else delete window.HTMLElement.prototype.matches;
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});

test('only a completed assistant with an exact saved turn identity exposes fork', () => {
  const { renderToStaticMarkup } = require('react-dom/server');
  const Bubble = componentModule('components/MessageBubble.tsx').default;
  const show = fields => renderToStaticMarkup(React.createElement(Bubble, {
    message: { role: 'assistant', content: '', ...fields },
    actions: { onCopy() {}, onFork() {}, canModify: true },
  }));
  assert.match(show({ status: 'completed', clientTurnId: 'recorded-turn' }), /data-fork-answer/);
  assert.doesNotMatch(show({ status: 'completed' }), /data-fork-answer/);
  assert.doesNotMatch(show({ status: 'completed', clientTurnId: 'invalid\nturn' }), /data-fork-answer/);
  assert.doesNotMatch(show({ status: 'failed', clientTurnId: 'recorded-turn' }), /data-fork-answer/);
  assert.doesNotMatch(show({ answerCompletion: { itemId: 'answer' }, clientTurnId: 'recorded-turn' }), /data-fork-answer/);
});

test('inherited assistant history preserves literal text without deriving links or artifact previews', () => {
  const { renderToStaticMarkup } = require('react-dom/server');
  const Bubble = componentModule('components/MessageBubble.tsx', {
    globals: { DOMParser: class { parseFromString(html) {
      return { body: { innerHTML: html }, querySelectorAll: () => [] };
    } } },
    externals: { dompurify: { sanitize: text => text } },
  }).default;
  const content = '原始 outputs/draft.png\n  <img src="/api/media/private.png">\n末尾';
  const doc = parseHTML(renderToStaticMarkup(React.createElement(Bubble, {
    message: { role: 'assistant', content, status: 'completed', clientTurnId: 'source-turn' }, inheritedForkText: true,
    actions: { onCopy() {}, onFork() {}, canModify: true },
  }))).document;
  assert.equal(doc.querySelector('[data-inherited-fork-text]').textContent, content);
  assert.equal(doc.querySelector('img, a, .artifact-gallery, [data-fork-answer]'), null);
});
