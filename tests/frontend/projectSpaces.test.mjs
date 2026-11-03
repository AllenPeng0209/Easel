import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const storage = initial => {
  const values = new Map(Object.entries(initial));
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key) };
};
function load(project, localStorage = storage({}), userId = '') {
  const sessionStorage = storage(userId ? { easel_authenticated_user: userId,
    [`easel_user:${userId}:easel_project`]: project } : { easel_project: project });
  const modules = {};
  const environment = { localStorage, sessionStorage, Date, Math,
    window: { location: { pathname: '/', reload() {} } } };
  function read(name) {
    if (modules[name]) return modules[name];
    const url = new URL(`../../web/frontend/src/lib/${name}.ts`, import.meta.url);
    const source = existsSync(url) ? readFileSync(url, 'utf8') : '';
    const code = ts.transpileModule(source, { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    } }).outputText;
    const exports = modules[name] = {};
    runInNewContext(code, { ...environment, exports, require: name => read(name.replace('./', '')) });
    return exports;
  }
  return { scope: read('projectScope'), store: read('store'), prompt: read('promptAdmission'), localStorage, sessionStorage };
}

test('identical session IDs and publish drafts stay in their own project and survive reload', () => {
  const entries = storage({ easel_sessions: JSON.stringify([{ id: 'legacy', messages: [] }]) });
  const a = load('aaaaaaaaaaaaaaaa', entries);
  assert.equal(a.store.loadSessions().length, 0);
  a.store.saveSessions([{ id: 'same', title: 'A', messages: [{ role: 'user', content: 'A only' }] }]);
  a.store.savePublishDraft({ title: 'A draft' });
  a.store.saveActiveId('same');
  const b = load('bbbbbbbbbbbbbbbb', entries);
  assert.equal(b.store.loadSessions().length, 0);
  assert.equal(b.store.loadPublishDraft().title, '');
  assert.equal(b.store.loadActiveId(), null);
  const restored = load('aaaaaaaaaaaaaaaa', entries);
  assert.equal(restored.store.loadSessions()[0].messages[0].content, 'A only');
  assert.equal(restored.store.loadPublishDraft().title, 'A draft');
  assert.equal(load('default', entries).store.loadSessions()[0].id, 'legacy');
});

test('API and native media URLs carry the project; a pending request keeps its original scope', () => {
  const a = load('aaaaaaaaaaaaaaaa');
  assert.equal(typeof a.scope.projectUrl, 'function');
  assert.equal(a.scope.projectUrl('/api/accounts'), '/api/projects/aaaaaaaaaaaaaaaa/accounts');
  assert.equal(a.scope.projectUrl('/api/media/topic/a.png?v=1'), '/api/projects/aaaaaaaaaaaaaaaa/media/topic/a.png?v=1');
  a.sessionStorage.setItem('easel_project', 'bbbbbbbbbbbbbbbb');
  assert.equal(a.scope.projectUrl('/api/chat/stream'), '/api/projects/aaaaaaaaaaaaaaaa/chat/stream');
  assert.equal(load('default').scope.projectUrl('/api/accounts'), '/api/accounts');
});

test('two users in the same IP never share conversation history or publication drafts', () => {
  const entries = storage({});
  const a = load('default', entries, 'alice');
  a.store.saveSessions([{ id: 'same', title: 'private', messages: [{ role: 'user', content: 'Alice only' }] }]);
  a.store.savePublishDraft({title:'Alice draft'});
  const b = load('default', entries, 'bob');
  assert.equal(b.store.loadSessions().length, 0);
  assert.equal(b.store.loadPublishDraft().title, '');
  assert.equal(load('default', entries, 'alice').store.loadSessions()[0].messages[0].content, 'Alice only');
});

test('a blank conversation with a pending admission survives alongside newer blank sessions', () => {
  const space = load('default', storage({}), 'alice');
  const pending = { id: 'pending', title: 'Sending', messages: [], pendingTurnId: 'send-1' };
  const blank = { id: 'blank', title: 'New Chat', messages: [] };
  space.store.saveSessions([blank, pending]);
  assert.deepEqual(Array.from(space.store.loadSessions(), session => session.id), ['blank', 'pending']);
});

test('unconfirmed blank conversations survive locally without leaking into another account', () => {
  const entries = storage({});
  const owner = load('default', entries, 'alice');
  const blank = { id: 'blank', title: 'New Chat', messages: [] };
  const waiting = { id: 'waiting', title: 'Uncertain send', messages: [] };
  owner.prompt.savePromptAttempt('waiting', { turnId: 'send-2', state: 'unconfirmed',
    wireText: 'first try', message: { role: 'user', content: 'first try' }, composer: true });
  owner.store.saveSessions([blank, waiting]);
  assert.deepEqual(Array.from(load('default', entries, 'alice').store.loadSessions(), session => session.id), ['blank', 'waiting']);
  assert.equal(load('default', entries, 'bob').prompt.loadPromptAttempt('waiting'), undefined);
});
