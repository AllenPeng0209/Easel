import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const { projectAgentTimeline } = componentModule('vendor/deepseek-harness/trajectory/timeline.ts');
const tool = (id, callId, status, extra = {}) => ({
  id, callId, kind: 'tool', runId: 'run-a', status, ...extra,
});
const decision = (id, status, extra = {}) => ({
  id, kind: 'decision', runId: 'run-a', questionId: 'question-a', status, ...extra,
});
const plain = value => JSON.parse(JSON.stringify(value));

test('preserves interleaving and input ordinals even with missing or out-of-order timestamps', () => {
  const events = [
    tool('root-running', 'root', 'running', { observedAt: 50 }),
    decision('question', 'pending'),
    tool('child-running', 'child', 'running', { observedAt: 20 }),
    decision('answer', 'resolved'),
    tool('child-failed', 'child', 'failed'),
  ];
  const { observations, summary } = projectAgentTimeline(events);
  assert.deepEqual(plain(observations), events.map((event, ordinal) => ({ event, ordinal })));
  assert.deepEqual(plain(summary), { tools: 2, failed: 1, waiting: 0 });
});

test('counts scoped calls once and keeps failure despite late nonterminal updates', () => {
  const { summary } = projectAgentTimeline([
    tool('failed', 'same', 'failed'),
    tool('stale', 'same', 'running'),
    tool('other-run', 'same', 'completed', { runId: 'run-b' }),
    tool('legacy', 'same', 'failed', { runId: 'legacy-a' }),
    tool('legacy-other', 'same', 'completed', { runId: 'legacy-b' }),
    decision('waiting', 'pending'),
    decision('foreign-resolution', 'resolved', { runId: 'run-b' }),
  ]);
  assert.deepEqual(plain(summary), { tools: 4, failed: 2, waiting: 1 });
});

test('does not manufacture earlier tool observations or mark active tools as waiting for the user', () => {
  const latest = tool('latest', 'snapshot', 'completed');
  const legacy = { id: 'legacy', kind: 'plan' };
  assert.deepEqual(plain(projectAgentTimeline([latest, legacy])), {
    observations: [{ event: latest, ordinal: 0 }, { event: legacy, ordinal: 1 }],
    summary: { tools: 1, failed: 0, waiting: 0 },
  });
  assert.deepEqual(plain(projectAgentTimeline([])), {
    observations: [], summary: { tools: 0, failed: 0, waiting: 0 },
  });
});
