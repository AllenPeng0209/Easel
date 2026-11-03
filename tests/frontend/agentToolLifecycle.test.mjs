import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { parseHTML } = require('linkedom');

const tool = (id, status, details = {}) => ({ id, kind: 'tool', callId: 'stable', runId: 'r1',
  name: 'native_tool', status, ...details });
const documentFor = (events, settled = false) => parseHTML(renderToStaticMarkup(React.createElement(
  componentModule('components/AgentEventPanel.tsx').default, { events, settled, expandedCallId: 'stable' },
))).document;
const row = document => document.querySelector('[data-tool-call=stable]');

test('preparing, running and terminal result keep the same anchor with independent phase evidence', () => {
  const preparing = tool('p', 'preparing', { input: { value: 'arg' } });
  const running = tool('r', 'running');
  const done = tool('d', 'completed', { output: 'result' });
  const initial = row(documentFor([preparing]));
  const active = row(documentFor([preparing, running]));
  const terminal = row(documentFor([preparing, running, done]));
  assert.equal(initial.dataset.toolPhase, 'preparing');
  assert.match(initial.textContent, /准备参数/);
  assert.doesNotMatch(initial.textContent, /已完成/);
  assert.equal(active.dataset.toolPhase, 'running');
  assert.match(active.textContent, /执行中/);
  assert.equal(terminal.dataset.toolPhase, 'terminal');
  assert.match(terminal.textContent, /已完成/);
  assert.equal(initial.dataset.chatAnchorKey, terminal.dataset.chatAnchorKey);
});

test('sparse, unknown and unfinished settled reports are unconfirmed without leaking unknown status strings', () => {
  const unexpected = row(documentFor([tool('x', 'unrecognized_vendor_status')]));
  assert.equal(unexpected.dataset.toolStatus, 'unconfirmed');
  assert.equal(unexpected.dataset.toolPhase, 'unconfirmed');
  assert.match(unexpected.textContent, /未确认/);
  assert.doesNotMatch(unexpected.textContent, /unrecognized_vendor_status/);
  const absent = row(documentFor([tool('none', undefined)]));
  assert.equal(absent.dataset.toolStatus, 'unconfirmed');
  const settled = row(documentFor([tool('p', 'preparing')], true));
  assert.equal(settled.dataset.toolPhase, 'unconfirmed');
  assert.match(settled.textContent, /未收到工具终态/);
});

test('stale preparing, older terminal, and late running observations never revive or overwrite a terminal', () => {
  const { reduceAgentEvents } = componentModule('lib/agentEvents.ts');
  const running = tool('run', 'running', { timestamp: 20, input: 'new inputs' });
  const stale = tool('prepare', 'preparing', { timestamp: 10, input: 'old inputs' });
  const failed = tool('failed', 'failed', { timestamp: 40, output: 'actual failure' });
  const oldDone = tool('old-done', 'completed', { timestamp: 30, output: 'wrong output' });
  const afterStart = [running, stale].reduce(reduceAgentEvents, []);
  assert.equal(afterStart[0].status, 'running');
  assert.equal(afterStart[0].input, 'new inputs');
  const final = [running, stale, failed, oldDone, tool('late', 'running', { timestamp: 60 })].reduce(reduceAgentEvents, []);
  assert.equal(final[0].status, 'failed');
  assert.equal(final[0].output, 'actual failure');
  const unknownTerminal = [running, tool('lost', 'unconfirmed', { timestamp: 40 }),
    tool('stale-run', 'running', { timestamp: 50 })].reduce(reduceAgentEvents, []);
  assert.equal(unknownTerminal[0].status, 'unconfirmed');
});

test('a completed native publication tool uses receipt wording without claiming platform success', () => {
  const result = row(documentFor([tool('pub', 'completed', { name: 'easel_publish', output: { success: true } })]));
  assert.equal(result.dataset.toolPhase, 'terminal');
  assert.match(result.textContent, /调用已结束/);
  assert.match(result.textContent, /发布回执/);
  assert.doesNotMatch(result.textContent, /已发布|发布成功/);
});
