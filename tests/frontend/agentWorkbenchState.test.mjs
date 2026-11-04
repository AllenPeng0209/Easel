import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import { projectScope, runWithProjectScope } from './projectScopeHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load(name, context = {}) {
  const path = new URL(`../../web/frontend/src/lib/${name}.ts`, import.meta.url);
  const exports = {};
  if (!existsSync(path)) return exports;
  const { outputText } = ts.transpileModule(readFileSync(path, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  runWithProjectScope(outputText, { exports, Date, Math, require: () => projectScope, ...context });
  return exports;
}
const events = load('agentEvents');
test('tool snapshots pair by callId without duplicating replayed events or losing terminal evidence', () => {
  assert.equal(typeof events.reduceAgentEvents, 'function', 'structured execution reducer is required');
  let value = events.reduceAgentEvents([], { id: 'e1', kind: 'tool', callId: 'c1', name: 'shell', status: 'running', input: { command: 'pwd' } });
  value = events.reduceAgentEvents(value, { id: 'e2', kind: 'tool', callId: 'c1', name: 'shell', status: 'completed', output: '/workspace', exitCode: 0 });
  value = events.reduceAgentEvents(value, { id: 'e2', kind: 'tool', callId: 'c1', name: 'shell', status: 'completed', output: '/workspace', exitCode: 0 });
  value = events.reduceAgentEvents(value, { id: 'e1', kind: 'tool', callId: 'c1', name: 'shell', status: 'running' });
  assert.equal(value.length, 1);
  assert.equal(value[0].status, 'completed');
  assert.equal(value[0].input.command, 'pwd');
  assert.equal(value[0].output, '/workspace');
});
test('malformed events cannot crash or pollute execution history', () => {
  assert.equal(typeof events.normalizeAgentEvent, 'function');
  for (const invalid of [null, 'tool', [], { kind: 'tool' }, { id: 'x', kind: '__proto__' }]) {
    assert.equal(events.normalizeAgentEvent(invalid), null);
  }
});
test('server session merge keeps a newer local draft and recovers older remote sessions', () => {
  const sync = load('sessionSync');
  assert.equal(typeof sync.mergeSessionCatalog, 'function');
  const result = sync.mergeSessionCatalog(
    [{ id: 'local', title: 'new', created: 1, updated: 30, messages: [{ role: 'user', content: 'new' }] }],
    [{ id: 'local', title: 'old', created: 1, updated: 20, messages: [] }, { id: 'remote', title: 'restore', created: 2, updated: 10, messages: [] }],
  );
  assert.equal(result.find(s => s.id === 'local').title, 'new');
  assert.equal(result.find(s => s.id === 'remote').title, 'restore');
});
test('every session mutation advances its revision even within one millisecond or after clock skew', () => {
  const sync = load('sessionSync');
  assert.equal(typeof sync.nextSessionRevision, 'function');
  const future = Date.now() + 60000;
  assert.equal(sync.nextSessionRevision({ updated: future, created: 1 }), future + 1);
});
test('chat history is not silently discarded after one hundred conversations', () => {
  const saved = new Map();
  const localStorage = { getItem: k => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v), removeItem: k => saved.delete(k) };
  const store = load('store', { localStorage });
  const sessions = Array.from({ length: 125 }, (_, n) => ({ id: `s${n}`, title: `会话 ${n}`, created: n + 1, messages: [{ role: 'user', content: `${n}` }] }));
  store.saveSessions(sessions);
  assert.equal(store.loadSessions().length, 125);
  assert.ok(store.loadSessions().some(s => s.id === 's124'));
});
test('a full browser storage keeps the live history usable without silently pruning records', () => {
  const store = load('store', { localStorage: { getItem: () => null, setItem: () => { throw new Error('QuotaExceeded'); } } });
  const sessions = [{ id: 'keep', title: 'important', created: 1, messages: [{ role: 'user', content: '保留' }] }];
  assert.doesNotThrow(() => store.saveSessions(sessions));
  assert.equal(store.loadSessions()[0].id, 'keep');
});
test('server deletion tombstones remove stale browser entries instead of resurrecting them', () => {
  const sync = load('sessionSync');
  const result = sync.mergeSessionCatalog([{ id: 'deleted', created: 1, messages: [] }], [], ['deleted']);
  assert.equal(result.length, 0);
});

test('an unconfirmed backend cancellation never becomes a successful stop', async () => {
  const control = load('chatControl');
  assert.equal(typeof control.confirmChatStop, 'function');
  await assert.rejects(control.confirmChatStop(async () => ({ stopped: false, status: 'stopping' })), /停止/);
  assert.equal(await control.confirmChatStop(async () => ({ stopped: true, status: 'stopped' })), 'stopped');
  assert.equal(await control.confirmChatStop(async () => ({ stopped: false, status: 'already_finished' })), 'already_finished');
});
test('queue admission is idempotent and stopped sessions do not restart themselves', () => {
  const control = load('chatControl');
  assert.equal(typeof control.enqueueMessage, 'function');
  const session = { id: 's', title: 't', created: 1, messages: [] };
  const message = { id: 'q1', text: '第二项', created: 2 };
  const queued = control.enqueueMessage(control.enqueueMessage(session, message), message);
  assert.equal(queued.queued.length, 1);
  assert.equal(control.nextQueuedMessage({ ...queued, queuePaused: true }, false), null);
  assert.equal(control.nextQueuedMessage(queued, true), null);
  assert.equal(control.nextQueuedMessage(queued, false).id, 'q1');
});
