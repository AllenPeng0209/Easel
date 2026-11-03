import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const { renderToStaticMarkup } = require('react-dom/server');
const panel = options => componentModule('components/AgentEventPanel.tsx', {
  ...options, globals: { TextEncoder, ...options?.globals },
}).default;
const diff = '@@ -4,2 +4,2 @@\n old context\n-secret=private-old\n+<script>new value</script>\n';
const change = { path: 'notes/draft.md', kind: { type: 'update', move_path: 'notes/final.md' }, diff };
const event = (fields = {}) => ({ id: 'event', kind: 'tool', callId: 'file', name: 'apply_patch', status: 'completed',
  fileChangeApplied: true, input: { changes: [change] }, ...fields });
const show = (Panel, fields) => React.createElement(Panel, { expandedCallId: 'file', events: [event(fields)] });
const markup = fields => renderToStaticMarkup(show(panel(), fields));

test('engine-applied patch shows provided filename, move destination, and actual numbered changed lines', () => {
  const html = markup();
  const { document } = parseHTML(html);
  const card = document.querySelector('[data-file-diff-state="applied"]');
  assert.ok(card);
  assert.match(card.textContent, /notes\/draft\.md.*notes\/final\.md/s);
  assert.match(card.textContent, /引擎报告已应用/);
  assert.equal(card.querySelector('[data-diff-line=del]').dataset.oldLine, '5');
  assert.equal(card.querySelector('[data-diff-line=add]').dataset.newLine, '5');
  assert.match(card.querySelector('[data-diff-line=add]').textContent, /<script>new value<\/script>/);
  assert.equal(document.querySelector('script'), null);
  assert.doesNotMatch(html, /private-old/);
});

test('proposed, failed, and missing patches never claim applied or synthesize change lines', () => {
  const attempted = markup({ status: 'running', fileChangeApplied: false });
  assert.match(attempted, /data-file-diff-state="attempted"/);
  assert.doesNotMatch(attempted, /引擎报告已应用/);
  const failed = markup({ status: 'failed', fileChangeApplied: false });
  assert.match(failed, /data-file-diff-state="failed"/);
  assert.doesNotMatch(failed, /引擎报告已应用/);
  const missing = markup({ input: { changes: [{ path: 'README.md', kind: { type: 'add' } }] } });
  assert.match(missing, /未提供差异/);
  assert.doesNotMatch(missing, /data-diff-line/);
  const legacy = markup({ fileChangeApplied: undefined });
  assert.doesNotMatch(legacy, /data-file-diff-state/);
  assert.match(legacy, /data-tool-value-kind/);
});

test('malformed unified text falls back without asserting exact change counts', () => {
  const raw = markup({ input: { changes: [{ ...change, diff: '@@ -1,3 +1,1 @@\n-secret=raw-secret\n+value' }] } });
  assert.match(raw, /data-file-diff-format="raw"/);
  assert.doesNotMatch(raw, /data-diff-line|raw-secret/);
});

test('large hunks fold and clipboard writes only sanitized patch text', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const copied = [];
  const Panel = panel({ globals: { navigator: { clipboard: { writeText: async text => { copied.push(text); } } },
    window: { location: { pathname: '/', origin: 'http://127.0.0.1:7860' }, setTimeout }, document } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const patch = '@@ -1,0 +1,26 @@\n' + Array.from({ length: 26 }, (_, index) =>
      index === 3 ? '+token=sensitive-fixture' : `+line-${index}`).join('\n') + '\n';
    await React.act(() => root.render(show(Panel, { input: { changes: [{ ...change, diff: patch }] } })));
    assert.equal(document.querySelector('[data-file-diff-added]').dataset.fileDiffAdded, '26');
    assert.equal(document.querySelector('[data-diff-hunk-header]').parentElement.hasAttribute('open'), false);
    await React.act(() => document.querySelector('[data-copy-file-diff]')
      .dispatchEvent(new window.Event('click', { bubbles: true })));
    assert.equal(copied.length, 1);
    assert.match(copied[0], /@@ -1,0 \+1,26 @@/);
    assert.match(copied[0], /\+line-25/);
    assert.doesNotMatch(copied[0], /sensitive-fixture/);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});

test('an unconfirmed terminal file report survives a stale replayed running snapshot', () => {
  const { reduceAgentEvents } = componentModule('lib/agentEvents.ts');
  const start = event({ id: 'start', status: 'running', fileChangeApplied: false });
  const unknown = event({ id: 'unknown', status: 'unconfirmed', fileChangeApplied: false, input: { changes: [] } });
  const final = [start, unknown, start].reduce(reduceAgentEvents, []);
  assert.equal(final[0].status, 'unconfirmed');
  assert.equal(final[0].input.changes.length, 0);
});
