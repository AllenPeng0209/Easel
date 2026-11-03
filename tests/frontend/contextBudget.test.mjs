import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const { readContextBudget } = componentModule('lib/contextBudget.ts');
const usage = (usedTokens, capacityTokens, source = 'codex') => ({
  id: 'native-usage', kind: 'usage',
  usage: { total: { totalTokens: 999999 }, last: { totalTokens: usedTokens } },
  contextUsage: { source, usedTokens, capacityTokens },
});
const turn = (...events) => ({ role: 'assistant', content: 'reply', events });
const plain = (value) => JSON.parse(JSON.stringify(value));

test('context occupancy uses one native observation and never cumulative or inferred tokens', () => {
  assert.deepEqual(plain(readContextBudget([turn(usage(82, 100))])), {
    source: 'codex', usedTokens: 82, contextWindow: 100, percent: 82, freshness: 'settled',
  });
  assert.deepEqual(plain(readContextBudget([], [usage(0, 10, 'acp')])), {
    source: 'acp', usedTokens: 0, contextWindow: 10, percent: 0, freshness: 'live',
  });
  assert.deepEqual(plain(readContextBudget([], [{ id: 'legacy', kind: 'usage', usage: { totalTokens: 700 }, capacity: 100 }])), {});
});

test('missing fields remain unknown rather than borrowing capacity or old turn data', () => {
  assert.deepEqual(plain(readContextBudget([turn(usage(45, 80), usage(23, null))])), {
    source: 'codex', usedTokens: 23, freshness: 'settled',
  });
  assert.deepEqual(plain(readContextBudget([turn(usage(45, 80)), turn()])), {});
  assert.deepEqual(plain(readContextBudget([turn(usage(45, 80))], [])), {});
  assert.deepEqual(plain(readContextBudget([], [usage(-2, 0)])), {
    source: 'codex', freshness: 'live',
  });
  assert.deepEqual(plain(readContextBudget([], [usage(80, 100, 'unverified')])), {});
});

test('compaction clears earlier readings until a fresh provider report', () => {
  const compact = { id: 'compact', kind: 'context', source: 'codex', contextReset: true, status: 'completed' };
  assert.deepEqual(plain(readContextBudget([], [usage(90, 100), compact])), {});
  assert.equal(readContextBudget([], [usage(90, 100), compact, usage(120, 100)]).percent, 100);
});
