import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

test('admitting a retry from an older client preserves the original turn', () => {
  const { applyPromptAdmission } = componentModule('lib/promptAdmission.ts');
  const messages = [{ role: 'user', content: 'Prompt' }, { role: 'assistant', content: 'Original' }];
  const attempt = { turnId: 'retry', truncateAt: 0, baseline: JSON.stringify(messages), message: messages[0] };
  const admitted = applyPromptAdmission({ messages }, attempt);
  assert.equal(admitted.messages.length, 3);
  assert.deepEqual(Array.from(admitted.messages.slice(0, 2)), messages);
  assert.equal(admitted.messages[2].clientTurnId, 'retry');
  assert.equal(applyPromptAdmission(admitted, attempt), admitted);
});
