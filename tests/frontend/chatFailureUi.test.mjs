import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const { renderToStaticMarkup } = require('react-dom/server');
const fixture = globals => componentModule('components/MessageBubble.tsx', {
  globals: { DOMParser: class { parseFromString(html) {
    return { body: { innerHTML: html }, querySelectorAll: () => [] };
  } }, ...globals },
  externals: { dompurify: { sanitize: text => text } },
}).default;
const failure = (code, phase = 'startup', retryable = false) => ({ code, phase, retryable });
const message = fields => ({ role: 'assistant', content: '', status: 'failed', ...fields });
const actions = { canModify: true, onCopy() {}, onRetry() {} };
const show = (fields, props = {}) => parseHTML(renderToStaticMarkup(React.createElement(fixture(), {
  message: message(fields), actions, ...props,
}))).document;

test('fixed failure copy and settings use typed classifications without exposing an authentication payload', () => {
  const raw = 'raw authorization payload';
  const doc = show({ content: '已输出内容\n\nError: ' + raw,
    failure: { ...failure('authentication'), message: raw } }, { onOpenFailureSettings() {} });
  const notice = doc.querySelector('[data-chat-failure=authentication]');
  assert.ok(notice);
  assert.equal(notice.dataset.failurePhase, 'startup');
  assert.match(notice.textContent, /认证/);
  assert.ok(notice.querySelector('[data-chat-failure-action=settings]'));
  assert.doesNotMatch(doc.toString(), new RegExp(raw));
  assert.match(doc.querySelector('[data-assistant-answer]').textContent, /已输出内容/);
  assert.equal(doc.querySelector('[data-chat-failure-action=retry]'), null);
});

test('only a current explicit startup failure can offer manual retry', () => {
  const retry = '[data-chat-failure-action=retry]';
  assert.ok(show({ failure: failure('network', 'startup', true) }).querySelector(retry));
  for (const phase of ['execution', 'finalization']) {
    assert.equal(show({ failure: failure('network', phase, true) }).querySelector(retry), null);
  }
  assert.equal(show({ failure: failure('network', 'startup', false) }).querySelector(retry), null);
  assert.equal(show({ failure: failure('network', 'startup', true) }, { actions: { ...actions, canModify: false } }).querySelector(retry), null);
  assert.equal(show({ failure: failure('network', 'startup', true) }, { actions: { ...actions, onRetry: undefined } }).querySelector(retry), null);
});

test('legacy or malformed failed data shows neutral guidance and no generic retry', () => {
  const raw = 'legacy raw provider payload';
  for (const fields of [{ content: 'Error:' + raw }, { content: '\n\nError: ' + raw,
    failure: { code: 'unsupported_code', phase: 'startup', retryable: true, message: raw } }]) {
    const doc = show(fields);
    assert.ok(doc.querySelector('[data-chat-failure=unknown]'));
    assert.doesNotMatch(doc.toString(), new RegExp(raw));
    assert.equal(doc.querySelector('[data-chat-failure-action]'), null);
    assert.equal(doc.querySelector('[data-assistant-answer]'), null);
    assert.equal(doc.querySelector('[title="复制"]'), null);
    assert.equal(doc.querySelector('[data-retry-last-turn]'), null);
  }
});


test('unconfirmed outcomes or existing publication observations never offer another execution', () => {
  const retry = '[data-chat-failure-action=retry], [data-retry-last-turn]';
  for (const fields of [
    { status: 'unconfirmed', content: '未确认结果' },
    { status: 'completed', content: '已发布',
      events: [{ id: 'receipt', kind: 'publication', publication: { id: 'existing' } }] },
    { status: 'unconfirmed', failure: failure('network', 'startup', true) },
    { failure: failure('network', 'startup', true),
      events: [{ id: 'receipt', kind: 'publication', publication: { id: 'existing' } }] },
  ]) assert.equal(show(fields).querySelector(retry), null);
  assert.ok(show({ status: 'completed', content: '完成' }).querySelector('[data-retry-last-turn]'));
});

test('settings is explicit and a retry click is latched against repeated activation', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Notice = componentModule('components/ChatFailureNotice.tsx').default;
    let settings = 0;
    let retries = 0;
    const update = (value, key) => React.act(() => root.render(React.createElement(Notice, { key,
      failure: value, onOpenSettings() { settings++; }, onRetry() { retries++; },
    })));
    await update(failure('authentication'), 'auth');
    assert.equal(settings, 0);
    await React.act(() => document.querySelector('[data-chat-failure-action=settings]').click());
    assert.equal(settings, 1);
    await update(failure('network', 'startup', true), 'network');
    assert.equal(retries, 0);
    const retry = document.querySelector('[data-chat-failure-action=retry]');
    await React.act(() => {
      retry.dispatchEvent(new window.Event('click', { bubbles: true }));
      retry.dispatchEvent(new window.Event('click', { bubbles: true }));
    });
    assert.equal(retries, 1);
    assert.equal(retry.hasAttribute('disabled'), true);

    const clipboard = [];
    const Bubble = fixture({ navigator: { clipboard: { writeText: async text => clipboard.push(text) } },
      window: { location: { pathname: '/', origin: 'http://127.0.0.1:7860' }, setTimeout }, document });
    await React.act(() => root.render(React.createElement(Bubble, {
      message: message({ content: '已保留正文\n\nError: raw provider material' }),
      actions: { ...actions, onCopy() { throw new Error('Legacy raw callback must stay unused'); } },
    })));
    await React.act(() => document.querySelector('[title="复制"]').click());
    assert.deepEqual(clipboard, ['已保留正文']);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
