import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const { renderToStaticMarkup } = require('react-dom/server');

const view = (fields, props = {}) => renderToStaticMarkup(React.createElement(
  componentModule('components/MessageBubble.tsx').default,
  { message: { role: 'assistant', content: '', ...fields }, ...props },
));

test('legacy untyped reasoning is excluded from both saved and live messages', () => {
  for (const html of [
    view({ thinking: 'private persisted reasoning' }),
    view({}, { isStreaming: true, thinking: 'private live reasoning' }),
    view({ reasoningSummary: { text: 'invalid summary envelope' }, thinking: 'private persisted reasoning' }),
  ]) {
    assert.doesNotMatch(html, /private|invalid summary envelope|data-reasoning-summary/);
  }
});

test('the typed truncated summary advertises its retained portion while collapsed', () => {
  const html = view({ reasoningSummary: { text: 'retained public summary', truncated: true } });
  const { document } = parseHTML(html);
  const summary = document.querySelector('[data-reasoning-summary]');
  assert.ok(summary);
  assert.equal(summary.querySelector('button').getAttribute('aria-expanded'), 'false');
  assert.match(summary.querySelector('[data-reasoning-summary-truncated]').textContent, /已截断/);
  assert.match(summary.querySelector('button').textContent, /查看已保存部分/);
  assert.doesNotMatch(html, /retained public summary/);
});

test('expanding reveals only safe retained text, separate from tool and final answer regions', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Bubble = componentModule('components/MessageBubble.tsx', {
      externals: { dompurify: require('dompurify')(window) },
      globals: { window, document, DOMParser: class {
        parseFromString(html) { return parseHTML(`<html><body>${html}</body></html>`).document; }
      } },
    }).default;
    const update = text => React.act(() => root.render(React.createElement(Bubble, {
      message: { role: 'assistant', content: '最终答复', reasoningSummary: { text, truncated: true },
        events: [{ id: 'tool', callId: 'tool', kind: 'tool', name: 'draft', status: 'completed' }] },
    })));
    await update('公开部分 <img src=x onerror=alert(1)>');
    const summary = document.querySelector('[data-reasoning-summary]');
    await React.act(() => summary.querySelector('button').dispatchEvent(new window.Event('click', { bubbles: true })));
    assert.match(summary.querySelector('[data-reasoning-summary-body]').textContent, /<img/);
    assert.equal(summary.querySelector('img'), null);
    assert.equal(summary.querySelector('[data-tool-call]'), null);
    assert.equal(summary.querySelector('[data-assistant-answer]'), null);
    assert.match(document.querySelector('[data-assistant-answer]').textContent, /最终答复/);
    assert.ok(document.querySelector('[data-tool-call]'));
    await update('后续公开部分');
    assert.equal(summary.querySelector('button').getAttribute('aria-expanded'), 'true');
    assert.equal(summary.querySelector('[data-reasoning-summary-body]').textContent, '后续公开部分');
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
