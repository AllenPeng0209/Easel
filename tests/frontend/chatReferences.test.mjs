import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const root = new URL('../../web/frontend/src/lib/', import.meta.url);

function load(name, localStorage, scope = 'user:a:ip:one') {
  const exports = {};
  const path = new URL(name + '.ts', root);
  if (!existsSync(path)) return exports;
  const source = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  runInNewContext(source, { exports, localStorage, require: name => name === './projectScope'
    ? { projectStorageKey: key => scope + ':' + key }
    : load(name.replace(/^\.\//, ''), localStorage, scope) });
  return exports;
}

function storage() {
  const entries = new Map();
  return { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value),
    removeItem: key => entries.delete(key) };
}

const plain = value => JSON.parse(JSON.stringify(value));
const first = { id: 'one', name: 'first.txt', path: '_inbox/session/batch/first.txt' };
const second = { id: 'two', name: 'second.png', path: '_inbox/session/batch/second.png' };
const references = [
  { start: 3, end: 13, attachmentId: 'one' },
  { start: 16, end: 27, attachmentId: 'two' },
];

test('structured references identify exact ordered receipts across UTF-16 text', () => {
  const model = load('chatReferences');
  assert.equal(typeof model.validateChatReferences, 'function');
  assert.equal(model.attachmentReferenceLabel(first), '@first.txt');
  const checked = model.validateChatReferences('🙂 @first.txt 与 @second.png', references, [second, first]);
  assert.deepEqual(plain(checked), references);
  assert.notEqual(checked[0], references[0]);
  assert.deepEqual(plain(model.validateChatReferences('@first.txt', undefined, [first])), []);
});

test('reference validation rejects forged, ambiguous and overlapping occurrences', () => {
  const model = load('chatReferences');
  assert.equal(typeof model.validateChatReferences, 'function');
  const text = '🙂 @first.txt 与 @second.png';
  for (const value of [
    null, {}, [references[1], references[0]], [references[0], references[0]],
    [{ ...references[0], start: 2 }], [{ ...references[0], end: 500 }],
    [{ ...references[0], attachmentId: 'unknown' }], [{ ...references[0], label: '@first.txt' }],
  ]) assert.equal(model.validateChatReferences(text, value, [first, second]), null);
  assert.equal(model.validateChatReferences(text, references, [first, { ...first, name: 'other.txt' }, second]), null);
});

test('trimming shifts UTF-16 offsets and keeps whitespace inside a receipt label', () => {
  const model = load('chatReferences');
  assert.equal(typeof model.trimChatReferenceText, 'function');
  assert.deepEqual(plain(model.trimChatReferenceText('  @end.txt  ', [
    { start: 2, end: 11, attachmentId: 'end' },
  ])), { text: '@end.txt ', references: [{ start: 0, end: 9, attachmentId: 'end' }] });
  assert.deepEqual(plain(model.trimChatReferenceText(' plain ')), { text: 'plain' });
});

const upload = { key: 'upload-one', name: first.name, size: 4, mime: 'text/plain', status: 'ready', reference: first };
const draft = { text: 'keep @first.txt', references: [{ start: 5, end: 15, attachmentId: 'one' }],
  attachments: [upload], interruptedFiles: [] };

test('draft restore retains valid references only within the current user, IP and session', () => {
  const local = storage();
  const own = load('chatDrafts', local);
  assert.equal(own.saveChatDraft('session', draft, 'owner'), true);
  assert.deepEqual(plain(load('chatDrafts', local).loadChatDraft('session').references ?? []), draft.references);
  for (const scope of ['user:b:ip:one', 'user:a:ip:two']) {
    assert.equal(load('chatDrafts', local, scope).loadChatDraft('session').references, undefined);
  }
  assert.equal(own.loadChatDraft('another').references, undefined);
});

test('invalid stored references remain plain text and an old admission cannot erase a replacement', () => {
  const local = storage();
  const store = load('chatDrafts', local);
  const key = 'user:a:ip:one:easel_chat_draft:session';
  local.setItem(key, JSON.stringify({ ...draft, references: [{ ...draft.references[0], attachmentId: 'forged' }] }));
  assert.equal(store.loadChatDraft('session').text, draft.text);
  assert.equal(store.loadChatDraft('session').references, undefined);

  const reminders = [{ name: 'pending.txt', size: 4, mime: 'text/plain' }];
  assert.equal(store.saveChatDraft('session', { ...draft, interruptedFiles: reminders }, 'replacement'), true);
  assert.equal(store.consumeChatDraft('session', 'old'), true);
  assert.deepEqual(plain(store.loadChatDraft('session').references ?? []), draft.references);
  assert.equal(store.consumeChatDraft('session', 'replacement'), true);
  assert.equal(store.loadChatDraft('session').references, undefined);
  assert.deepEqual(plain(store.loadChatDraft('session').interruptedFiles), reminders);
});
