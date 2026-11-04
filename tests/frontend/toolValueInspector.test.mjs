import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const { renderToStaticMarkup } = require('react-dom/server');
const panel = options => componentModule('components/AgentEventPanel.tsx', options).default;
const element = (Panel, event) => React.createElement(Panel, { expandedCallId: 'tool', settled: true,
  events: [{ id: 'event', kind: 'tool', callId: 'tool', status: 'completed', ...event }] });

test('inspector distinguishes missing input from explicit null and marks engine truncation', () => {
  const markup = renderToStaticMarkup(element(panel(), { output: null, truncated: true }));
  const { document } = parseHTML(markup);
  assert.match(document.querySelector('[data-tool-value-kind="missing"]').textContent, /当时未记录/);
  assert.match(document.querySelector('[data-tool-value-kind="json"]').textContent, /null/);
  assert.match(document.querySelector('.agent-tool__details').textContent, /引擎输出已截断/);
  assert.equal(document.querySelector('[data-tool-value-kind="missing"] button'), null);
});

test('structured values recursively expand with typed task IDs and safe plain text fallback', () => {
  const markup = renderToStaticMarkup(element(panel(), {
    input: '{"job":{"tasks":[{"taskId":"task_123","api_key":"sensitive-fixture"}]}}',
    output: '<img src=x onerror=alert(1)>',
  }));
  const { document } = parseHTML(markup);
  const tree = document.querySelector('[data-tool-value-kind="json"] [data-json-tree]');
  assert.ok(tree);
  assert.ok(tree.querySelector('details details'));
  assert.match(document.querySelector('[data-tool-value-kind="text"] pre').textContent, /<img src=x/);
  assert.equal(document.querySelector('img'), null);
  assert.match(markup, /task_123/);
  assert.doesNotMatch(markup, /sensitive-fixture/);
});

test('copy writes only sanitized section, nested field and exact task ID values', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const copied = [];
  let clipboardDenied = false;
  const Panel = panel({ globals: { navigator: { clipboard: { writeText: async text => {
    if (clipboardDenied) throw new Error('clipboard denied');
    copied.push(text);
  } } },
    window: { location: { pathname: '/', origin: 'http://127.0.0.1:7860' }, setTimeout }, document } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const click = selector => React.act(() => document.querySelector(selector)
    .dispatchEvent(new window.Event('click', { bubbles: true })));
  try {
    await React.act(() => root.render(element(Panel, { output: { job: {
      taskId: 'task_123', authorization: 'secret-original', note: 'Bearer private-token' } } })));
    await click('[data-copy-tool-value]');
    await click('[aria-label="复制字段 job"]');
    await click('[data-copy-tool-task-id]');
    assert.equal(copied.length, 3);
    for (const text of copied.slice(0, 2)) {
      assert.doesNotMatch(text, /secret-original|private-token/);
      assert.match(text, /task_123/);
    }
    assert.equal(copied[2], 'task_123');
    assert.equal(document.querySelector('[data-copy-tool-value]').dataset.copyState, 'copied');
    clipboardDenied = true;
    await click('[data-copy-tool-value]');
    assert.equal(document.querySelector('[data-copy-tool-value]').dataset.copyState, 'failed');
    assert.match(document.querySelector('[data-copy-tool-value]').textContent, /复制失败/);
    assert.equal(copied.length, 3);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});


test('a locally bounded payload shows its own truncation notice', () => {
  const markup = renderToStaticMarkup(element(panel(), { output: 'x'.repeat(20000) }));
  const { document } = parseHTML(markup);
  assert.match(document.querySelector('[data-tool-value-kind="text"]').textContent, /详情已截断/);
  assert.ok(document.querySelector('pre').textContent.length <= 16000);
  assert.doesNotMatch(markup, /引擎输出已截断/);
});
