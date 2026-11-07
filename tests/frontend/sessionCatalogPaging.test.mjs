import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const session = (id, updated = 1) => ({ id, title: id, updated, created: 1, messages: [] });

test('fresh catalog fetch collects every scoped page and applies final deletion facts before sorting', async () => {
  const calls = [];
  const pages = [
    { sessions: [session('early', 4), session('deleted', 8)], deletedIds: [], nextCursor: 'after one' },
    { sessions: [session('recent', 7)], deletedIds: ['deleted'], nextCursor: null },
  ];
  const { fetchChatSessions } = componentModule('lib/api.ts', {
    user: 'owner', project: 'workspace', globals: { Headers, fetch: async (url, options) => {
      calls.push({ url, signal: options.signal, actor: options.headers.get('X-Easel-User') });
      return Response.json(pages.shift());
    } },
  });
  const signal = new AbortController().signal;
  const result = await fetchChatSessions(signal);
  assert.deepEqual(calls.map(call => call.url), ['/api/projects/workspace/chat/sessions?limit=50',
    '/api/projects/workspace/chat/sessions?limit=50&cursor=after%20one']);
  assert.ok(calls.every(call => call.signal === signal && call.actor === 'owner'));
  assert.deepEqual(Array.from(result.sessions, value => value.id), ['recent', 'early']);
  assert.deepEqual(Array.from(result.deletedIds), ['deleted']);
});

test('invalid or repeated cursor rejects the incomplete catalog', async () => {
  let calls = 0;
  const { fetchChatSessions } = componentModule('lib/api.ts', { globals: { fetch: async () => {
    assert.ok(++calls <= 2, 'Repeated cursor should not issue another request');
    return Response.json({ sessions: [session('one')], nextCursor: 'one' });
  } } });
  await assert.rejects(fetchChatSessions());
  assert.equal(calls, 2);
});

test('a legacy host response without a cursor still restores history', async () => {
  const { fetchChatSessions } = componentModule('lib/api.ts', { globals: { fetch: async () =>
    Response.json({ sessions: [session('legacy')], deletedIds: [] }) } });
  assert.equal((await fetchChatSessions()).sessions[0].id, 'legacy');
});

test('compact first-page restoration does not wait for older catalog pages', async () => {
  const calls = [];
  const { fetchChatSessions } = componentModule('lib/api.ts', { globals: { fetch: async url => {
    calls.push(url);
    return Response.json({ sessions: [session('first')], deletedIds: ['retired'], nextCursor: 'first-page-end' });
  } } });
  const result = await fetchChatSessions(undefined, true, true);
  assert.equal(calls.length, 1);
  assert.equal(result.nextCursor, 'first-page-end');
  assert.equal(result.sessions[0].id, 'first');
});

test('a compact saved projection replaces an identical heavy browser snapshot', () => {
  const { mergeSessionCatalog } = componentModule('lib/sessionSync.ts');
  const window = { start: 0, base: 'a'.repeat(64), before: null };
  const saved = { ...session('first', 3), historyWindow: window,
    messages: [{ role: 'assistant', content: 'ready', events: [{ id: 'event', detailRef: 'b'.repeat(64) }] }] };
  const local = { ...saved, messages: [{ role: 'assistant', content: 'ready', events: [{ id: 'event', output: 'x'.repeat(100000) }] }] };
  assert.equal(mergeSessionCatalog([local], [saved])[0], saved);
  const running = { ...local, pendingTurnId: 'ongoing' };
  assert.equal(mergeSessionCatalog([running], [saved])[0], running);
  const legacy = { ...local }; delete legacy.historyWindow;
  assert.equal(mergeSessionCatalog([legacy], [saved])[0] === saved, true);
  const edited = { ...legacy, messages: [{ role: 'assistant', content: 'local unsaved text' }] };
  assert.equal(mergeSessionCatalog([edited], [saved])[0] === edited, true);
});
