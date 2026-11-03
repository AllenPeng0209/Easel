import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const { normalizeTurnChangeComparison } = componentModule('lib/turnChangeComparison.ts', { globals: { TextEncoder } });
const target = { sessionId: 'chat-a', turnId: 'turn-a', path: 'script.md', kind: 'modified' };
const before = { size: 6, sha256: 'a'.repeat(64) };
const after = { size: 6, sha256: 'b'.repeat(64) };
const evidence = { sessionId: target.sessionId, turnId: target.turnId, path: target.path,
  diffStatus: 'text', identical: false, before, after, diff: '--- a/script.md\n+++ b/script.md\n@@ -1 +1 @@\n-older\n+newer\n' };

test('turn comparison accepts only the exact frozen pair and projects a readable patch', () => {
  assert.equal(normalizeTurnChangeComparison(evidence, target)?.projection.kind, 'unified');
  for (const change of [
    { sessionId: 'other' }, { turnId: 'later' }, { path: 'other.md' },
    { before: null }, { after: before }, { identical: true }, { diff: null },
  ]) assert.equal(normalizeTurnChangeComparison({ ...evidence, ...change }, target), null);
  assert.ok(normalizeTurnChangeComparison({ ...evidence, before: null }, { ...target, kind: 'added' }));
  const limited = normalizeTurnChangeComparison({ ...evidence, diffStatus: 'oversized', diff: undefined }, target);
  assert.equal(limited.kind, 'limited');
  assert.equal(limited.reason, 'oversized');
});
