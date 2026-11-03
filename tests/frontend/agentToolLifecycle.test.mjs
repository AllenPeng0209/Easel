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

test('first confirmed tool result stays frozen while a later attempt with its own identity remains visible', () => {
  const { reduceAgentEvents } = componentModule('lib/agentEvents.ts');
  const failed = tool('failure', 'failed', { output: 'frozen error', exitCode: 23, completedAt: 40 });
  const rows = [
    failed,
    tool('changed-result', 'completed', { output: 'conflicting completion', exitCode: 0, completedAt: 50, parentCallId: 'parent' }),
    tool('same-status-changed-result', 'failed', { output: 'replacement', exitCode: 1, completedAt: 55 }),
    tool('later-attempt', 'completed', { callId: 'retry', output: 'accepted later output' }),
  ].reduce(reduceAgentEvents, []);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].status, 'failed');
  assert.equal(rows[0].output, 'frozen error');
  assert.equal(rows[0].exitCode, 23);
  assert.equal(rows[0].completedAt, 40);
  assert.equal(rows[0].parentCallId, 'parent');
  assert.equal(rows[1].output, 'accepted later output');
});

test('a verified result can settle an unconfirmed call and exact native markers can enrich the frozen tool', () => {
  const { reduceAgentEvents } = componentModule('lib/agentEvents.ts');
  const rows = [
    tool('lost', 'unconfirmed', { timestamp: 50, name: 'easel_publish' }),
    tool('confirmed', 'failed', { timestamp: 40, name: 'easel_publish', output: 'failure' }),
    tool('enrichment', 'completed', { name: 'easel_publish', nativePublicationTool: true, output: 'overwrite' }),
  ].reduce(reduceAgentEvents, []);
  assert.equal(rows[0].status, 'failed');
  assert.equal(rows[0].nativePublicationTool, true);
  assert.equal(rows[0].output, 'failure');
});

test('timeline does not show contradictory same-call terminal as a new attempt', () => {
  const { observeAgentTimeline } = componentModule('lib/agentTimeline.ts');
  const rows = [tool('failed', 'failed'), tool('completed', 'completed'),
    tool('actual-new-call', 'completed', { callId: 'retry' })].reduce(observeAgentTimeline, []);
  assert.deepEqual(Array.from(rows, row => [row.callId, row.status]), [['stable', 'failed'], ['retry', 'completed']]);
});

test('timeline locates a confirmed command with nonzero exit as failure', () => {
  const { observeAgentTimeline } = componentModule('lib/agentTimeline.ts');
  const [entry] = observeAgentTimeline([], tool('command-result', 'completed', {
    name: 'bash', outputStream: 'merged', input: { command: 'fixture' }, output: 'failed', exitCode: 9,
  }));
  assert.equal(entry.status, 'failed');
});
