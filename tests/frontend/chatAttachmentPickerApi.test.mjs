import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

test('picker searches and selects in the current IP without serializing a caller path', async () => {
  const calls = [];
  const api = componentModule('lib/api.ts', { user: 'actor', project: 'easel-ip', globals: {
    URLSearchParams, Headers,

    fetch: async (url, options) => {
      calls.push([url, options]);
      return { ok: true, json: async () => ({ files: [], candidates: [] }) };
    },
  } });
  const signal = new AbortController().signal;
  const candidate = 'a'.repeat(64);
  await api.searchChatAttachments('session &', '图 / #', signal);
  await api.selectChatAttachment('session &', candidate, signal);
  assert.equal(calls[0][0], '/api/projects/easel-ip/chat/attachment-search?sessionId=session+%26&query=%E5%9B%BE+%2F+%23');
  assert.equal(calls[0][1].signal, signal);
  assert.deepEqual(JSON.parse(calls[1][1].body), { sessionId: 'session &', candidateId: candidate });
  assert.equal(calls[1][0], '/api/projects/easel-ip/chat/attachment-select');
  assert.equal(calls[1][1].method, 'POST');
});
