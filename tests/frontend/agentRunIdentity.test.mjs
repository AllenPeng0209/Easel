import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const tool = (runId, id, status, details = {}) => ({ runId, id, callId: 'same-call',
  kind: 'tool', name: 'commandExecution', status, ...details });

test('tool and event IDs can repeat across runs without sharing output or terminal state', () => {
  const { reduceAgentEvents } = componentModule('lib/agentEvents.ts');
  const first = tool('run-one', 'same-event', 'running', { input: 'first input' });
  const second = tool('run-two', 'same-event', 'running', { input: 'second input' });
  const done = tool('run-one', 'done', 'completed', { output: 'first result' });
  let events = [first, second, done].reduce(reduceAgentEvents, []);
  assert.equal(events.length, 2);
  assert.equal(events[0].output, 'first result');
  assert.equal(events[1].status, 'running');
  assert.equal(events[1].input, 'second input');
  assert.equal(reduceAgentEvents(events, done), events);
  assert.equal(reduceAgentEvents(events, first), events);
  events = reduceAgentEvents(events, tool(undefined, 'same-event', 'running', { input: 'legacy input' }));
  assert.equal(events.length, 3);
  assert.equal(events[2].input, 'legacy input');
});

test('subagent normalization preserves scope and run IDs must be valid strings', () => {
  const { normalizeAgentEvent, reduceAgentEvents } = componentModule('lib/agentEvents.ts');
  const event = { id: 'same-event', runId: 'run-one', kind: 'subagent', turnId: 'same-turn',
    subagent: { id: 'a'.repeat(32), parentId: null, rootSessionId: 'session', originTurnId: 'same-turn',
      title: '子 Agent', status: 'running', createdAt: 1, updatedAt: 2 } };
  assert.equal(normalizeAgentEvent(event).runId, 'run-one');
  const state = reduceAgentEvents([event], { ...event, runId: 'run-two' });
  assert.equal(state.length, 2);
  for (const runId of [null, '', '  ', 7, 'bad\nrun', 'a'.repeat(513)]) {
    assert.equal(normalizeAgentEvent({ ...event, runId }), null);
  }
});

test('business calls are run scoped while durable publication receipts still merge across runs', () => {
  const { reduceAgentEvents } = componentModule('lib/agentEvents.ts');
  const business = { id: 'business', callId: 'reused', runId: 'one', kind: 'business', name: 'easel_business_objects',
    business: { id: 'idea', actorId: 'actor', projectId: 'ip', kind: 'idea', observedAt: 1 } };
  const pair = [business, { ...business, runId: 'two' }].reduce(reduceAgentEvents, []);
  assert.equal(pair.length, 2);
  const receipt = { id: 'receipt', runId: 'one', kind: 'publication', completedAt: 1,
    publication: { id: 'approval', actorId: 'actor', projectId: 'ip', payloadHash: 'same', operationId: 'operation' } };
  const receipts = [receipt, { ...receipt, runId: 'two', completedAt: 2 }].reduce(reduceAgentEvents, []);
  assert.equal(receipts.length, 1);
  assert.equal(receipts[0].runId, 'two');
});

test('panel renders reused calls separately and expands only the specified run', () => {
  const Panel = componentModule('components/AgentEventPanel.tsx').default;
  const html = renderToStaticMarkup(React.createElement(Panel, { expandedCallId: 'same-call', expandedRunId: 'two',
    events: [
      tool('one', 'same-event', 'completed', { output: 'hidden first result' }),
      tool('two', 'same-event', 'running', { input: 'second input' }),
      tool('two', 'done', 'completed', { output: 'shown second result' }),
      { id: 'note', runId: 'one', kind: 'plan', text: 'unique note' },
      { id: 'note', runId: 'one', kind: 'plan', text: 'unique note' },
    ] }));
  assert.equal((html.match(/data-tool-call="same-call"/g) || []).length, 2);
  assert.doesNotMatch(html, /hidden first result/);
  assert.match(html, /shown second result/);
  assert.equal((html.match(/unique note/g) || []).length, 1);
});
