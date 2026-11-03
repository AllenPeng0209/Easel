import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

test('an unconfirmed prompt attempt cannot be reused with a different model', () => {
  const { samePromptAttempt } = componentModule('lib/promptAdmission.ts');
  const attempt = { wireText: 'one request', composer: true, message: { role: 'user', content: 'one request' } };
  assert.equal(samePromptAttempt({ ...attempt, modelOverride: 'first' }, { ...attempt, modelOverride: 'second' }), false);
  assert.equal(samePromptAttempt({ ...attempt, modelOverride: 'first' }, { ...attempt, modelOverride: 'first' }), true);
  assert.equal(samePromptAttempt(attempt, { ...attempt, modelOverride: 'first' }), false);
});
