import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const { renderToStaticMarkup } = require('react-dom/server');
const loadPanel = options => componentModule('components/AgentEventPanel.tsx', options).default;
const renderPanel = (Panel, fields) => React.createElement(Panel, { expandedCallId: 'command', settled: false,
  events: [{ kind: 'tool', id: 'event', callId: 'command', name: 'bash', outputStream: 'merged',
    input: { command: 'printf hello', cwd: '/workspace/approved' }, status: 'completed', ...fields }] });
const markup = fields => renderToStaticMarkup(renderPanel(loadPanel(), fields));

test('verified command shows one merged output and observed exit and duration without inventing a signal', () => {
  const html = markup({ output: 'hello\nwarning', exitCode: 0, durationMs: 321 });
  assert.match(html, /data-terminal/);
  assert.match(html, /stdout \/ stderr（合并）/);
  assert.match(html, /printf hello/);
  assert.match(html, /hello\nwarning/);
  assert.match(html, /退出码：0/);
  assert.match(html, /321 ms/);
  assert.doesNotMatch(html, /终止信号|data-tool-value-kind/);
});

test('failed, interrupted and historical unknown exit states are kept distinct', () => {
  const nonzero = markup({ exitCode: 7, status: 'completed' });
  assert.match(nonzero, /data-tool-status="failed"/);
  assert.match(nonzero, /退出码：7/);
  const interrupted = markup({ exitCode: null, status: 'interrupted', signal: 'SIGTERM' });
  assert.match(interrupted, /data-tool-status="interrupted"/);
  assert.match(interrupted, /退出码：引擎未提供/);
  assert.match(interrupted, /终止信号：SIGTERM/);
  const running = markup({ status: 'running' });
  assert.doesNotMatch(running, /退出码：|耗时：|终止信号：/);
  assert.match(markup({}), /退出码：当时未记录/);
});

test('an unverified shell-shaped or malformed typed event falls back to the B07 inspector', () => {
  assert.match(markup({ outputStream: undefined, output: { stdout: 'maybe' } }), /data-tool-value-kind="json"/);
  assert.doesNotMatch(markup({ outputStream: undefined, output: { stdout: 'maybe' } }), /data-terminal/);
  assert.match(markup({ output: { stdout: 'maybe' } }), /data-tool-value-kind="json"/);
});

test('long diagnostics expand and copy only redacted bounded text', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const copied = [];
  const Panel = loadPanel({ globals: { navigator: { clipboard: { writeText: async text => { copied.push(text); } } },
    window: { location: { pathname: '/', origin: 'http://127.0.0.1:7860' }, setTimeout }, document } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const click = selector => React.act(() => document.querySelector(selector)
    .dispatchEvent(new window.Event('click', { bubbles: true })));
  try {
    const lines = Array.from({ length: 24 }, (_, i) => `line-${i + 1}`);
    lines[11] += ' Bearer private-\x1b[31mtoken\x1b[0m';
    await React.act(() => root.render(renderPanel(Panel, {
      input: { command: 'tool --api-key private-command-value' },
      output: lines.join('\n'), exitCode: 0, truncated: true,
    })));
    const text = () => document.querySelector('[data-terminal]').textContent;
    assert.match(text(), /line-1/);
    assert.match(text(), /line-24/);
    assert.doesNotMatch(text(), /line-12/);
    await click('[data-terminal-expand]');
    assert.match(text(), /line-12/);
    assert.doesNotMatch(text(), /private-token|private-command-value/);
    await click('[data-copy-terminal]');
    assert.equal(copied.length, 1);
    assert.match(copied[0], /line-12/);
    assert.match(copied[0], /引擎输出已截断/);
    assert.doesNotMatch(copied[0], /private-token|private-command-value|\x1b/);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
