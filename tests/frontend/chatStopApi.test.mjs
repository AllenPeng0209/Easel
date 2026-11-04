import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

test('stop sends the exact expected turn in the scoped request and preserves the mismatch receipt', async () => {
  const calls = [];
  const { stopChat } = componentModule('lib/api.ts', {
    user: 'owner', project: 'workspace', globals: { Headers, fetch: async (url, options) => {
      calls.push([url, options.method, options.headers.get('X-Easel-User'), JSON.parse(options.body)]);
      return Response.json({ stopped: false, status: 'turn_mismatch' });
    } },
  });
  assert.equal((await stopChat('session', 'turn-old')).status, 'turn_mismatch');
  assert.deepEqual(calls, [['/api/projects/workspace/chat/stop', 'POST', 'owner',
    { sessionId: 'session', expectedTurnId: 'turn-old' }]]);
});
