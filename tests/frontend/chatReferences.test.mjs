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

const source = { start: 15, end: 20, sessionId: 'source-a', snapshotHash: 'a'.repeat(64), label: '计划🙂' };
const mixedDraft = { ...draft, text: '  @first.txt 与 @计划🙂  ',
  references: [{ start: 2, end: 12, attachmentId: 'one' }], sessionReferences: [source] };

test('session reference validation preserves the canonical label and strips it from the wire', () => {
  const model = load('chatReferences');
  assert.equal(typeof model.validateChatSessionReferences, 'function');
  assert.equal(model.sessionReferenceLabel(source.label), '@计划🙂');
  const checked = model.validateChatSessionReferences(mixedDraft.text, [source], mixedDraft.references);
  assert.deepEqual(plain(checked), [source]);
  const { label, ...wire } = source;
  assert.deepEqual(plain(model.toWireSessionReferences(checked)), [wire]);
  assert.deepEqual(plain(model.validateChatSessionReferenceSpans(mixedDraft.text, [wire], mixedDraft.references)), [wire]);
  assert.equal(model.toWireSessionReferences(undefined), undefined);
});

test('session references reject malformed IDs, split surrogate spans and overlap with files', () => {
  const model = load('chatReferences');
  assert.equal(typeof model.validateChatSessionReferences, 'function');
  const overlap = { ...source, start: 2, end: 12, label: 'first.txt' };
  assert.equal(model.validateChatSessionReferences(mixedDraft.text, [overlap], mixedDraft.references), null);
  const { label, ...wireOverlap } = overlap;
  assert.equal(model.validateChatSessionReferenceSpans(mixedDraft.text, [wireOverlap], mixedDraft.references), null);
  for (const invalid of [
    { ...source, end: 19, label: '计划\uD83D' },
    { ...source, label: '其他' },
    { ...source, sessionId: '\n' },
    { ...source, snapshotHash: 'A'.repeat(64) },
  ]) assert.equal(model.validateChatSessionReferences(mixedDraft.text, [invalid], mixedDraft.references), null);
  assert.equal(model.validateChatSessionReferences(mixedDraft.text, [source, source], mixedDraft.references), null);
  const longText = '@x @x @x @x';
  const four = [0, 3, 6, 9].map(start => ({ ...source, label: 'x', start, end: start + 2 }));
  assert.equal(model.validateChatSessionReferences(longText, four), null);
});

test('trimming and restoring a mixed draft preserves independent file and session offsets', () => {
  const model = load('chatReferences');
  assert.deepEqual(plain(model.trimChatReferenceText(mixedDraft.text, mixedDraft.references, [source])), {
    text: '@first.txt 与 @计划🙂',
    references: [{ start: 0, end: 10, attachmentId: 'one' }],
    sessionReferences: [{ ...source, start: 13, end: 18 }],
  });

  const local = storage();
  const store = load('chatDrafts', local);
  assert.equal(store.saveChatDraft('session', mixedDraft, 'replacement'), true);
  assert.deepEqual(plain(store.loadChatDraft('session').sessionReferences ?? []), [source]);
  assert.equal(store.consumeChatDraft('session', 'old'), true);
  assert.deepEqual(plain(store.loadChatDraft('session').sessionReferences ?? []), [source]);
  assert.equal(load('chatDrafts', local, 'user:b:ip:one').loadChatDraft('session').sessionReferences, undefined);

  const invalid = { ...mixedDraft, sessionReferences: [{ ...source, label: 'tampered' }] };
  local.setItem('user:a:ip:one:easel_chat_draft:session', JSON.stringify(invalid));
  const restored = store.loadChatDraft('session');
  assert.equal(restored.text, mixedDraft.text);
  assert.equal(restored.sessionReferences, undefined);
  assert.deepEqual(plain(restored.references), mixedDraft.references);
});

const command = { id: 'creative-copy', token: '/文案' };

test('command claims require an explicit leading token and a whitespace boundary', () => {
  const model = load('chatCommands');
  assert.equal(typeof model.validateChatCommand, 'function');
  for (const text of ['/文案', '/文案 介绍', '/文案\n介绍', '/文案　介绍']) {
    const result = model.validateChatCommand(text, command);
    assert.deepEqual(plain(result), command);
    assert.notEqual(result, command);
  }
  for (const text of [' /文案', '开头 /文案', '/文案模板', '/文案-模板', '/口播']) {
    assert.equal(model.validateChatCommand(text, command), null);
  }
  assert.equal(model.validateChatCommand('/文案 介绍', undefined), null);
});

test('draft command validation rejects malformed or extra fields and overlapping reference spans', () => {
  const model = load('chatCommands');
  assert.equal(typeof model.validateChatCommand, 'function');
  for (const invalid of [
    { ...command, id: 'bad id' }, { ...command, token: '/文案 ' },
    { ...command, token: '/文案\n' }, { ...command, token: '/文案/下一段' },
    { ...command, token: '/文案;shell' }, { ...command, run: true },
  ]) {
    assert.equal(model.validateChatCommand(invalid.token, invalid), null);
  }
  assert.equal(model.chatCommandOverlapsReferences(command, [{ start: 2, end: 7 }]), true);
  assert.equal(model.chatCommandOverlapsReferences(command, [], [{ start: 6, end: 9 }]), false);
});

test('only explicit valid command claims persist and a late receipt preserves a new owner', () => {
  const local = storage();
  const store = load('chatDrafts', local);
  const prefix = '/文案 ';
  const value = {
    ...mixedDraft, text: prefix + mixedDraft.text, command,
    references: mixedDraft.references.map(ref => ({ ...ref, start: ref.start + prefix.length, end: ref.end + prefix.length })),
    sessionReferences: mixedDraft.sessionReferences.map(ref => ({ ...ref, start: ref.start + prefix.length, end: ref.end + prefix.length })),
  };
  assert.equal(store.saveChatDraft('session', value, 'new'), true);
  assert.equal(store.consumeChatDraft('session', 'old'), true);
  const restored = store.loadChatDraft('session');
  assert.deepEqual(plain(restored.command ?? {}), command);
  assert.deepEqual(plain(restored.references), value.references);
  assert.deepEqual(plain(restored.sessionReferences), value.sessionReferences);
  assert.equal(load('chatDrafts', local, 'user:a:ip:two').loadChatDraft('session').command, undefined);

  const legacy = { text: '/文案 草稿', attachments: [], interruptedFiles: [] };
  assert.equal(store.saveChatDraft('session', legacy, 'new'), true);
  assert.equal(store.loadChatDraft('session').command, undefined);
  assert.equal(store.saveChatDraft('session', { ...legacy, text: ' /文案 草稿', command }, 'new'), true);
  assert.equal(store.loadChatDraft('session').text, ' /文案 草稿');
  assert.equal(store.loadChatDraft('session').command, undefined);
});

test('single-turn reference survives validation, draft restore and wire projection', () => {
  const local = storage();
  const model = load('chatReferences');
  const store = load('chatDrafts', local);
  const turn = { ...source, turnId: 'exact-turn_1' };
  const checked = model.validateChatSessionReferences(mixedDraft.text, [turn], mixedDraft.references);
  assert.deepEqual(plain(checked), [turn]);
  const { label, ...wire } = turn;
  assert.deepEqual(plain(model.toWireSessionReferences(checked)), [wire]);
  assert.deepEqual(plain(model.validateChatSessionReferenceSpans(mixedDraft.text, [wire], mixedDraft.references)), [wire]);
  assert.equal(store.saveChatDraft('target', { ...mixedDraft, sessionReferences: [turn] }), true);
  assert.deepEqual(plain(store.loadChatDraft('target').sessionReferences), [turn]);
  for (const invalid of [null, '', '../wrong', 'a'.repeat(161)]) {
    assert.equal(model.validateChatSessionReferences(mixedDraft.text, [{ ...turn, turnId: invalid }], mixedDraft.references), null);
  }
  assert.equal(model.validateChatSessionReferences(mixedDraft.text, [{ ...turn, other: 1 }], mixedDraft.references), null);
});
