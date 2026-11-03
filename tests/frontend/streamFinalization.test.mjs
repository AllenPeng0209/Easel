import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const { reduceAgentEvents } = componentModule('lib/agentEvents.ts');
const { recoverAgentTimeline } = componentModule('lib/agentTimeline.ts');
const source = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declarations = new Map();
function visit(node) {
  if (ts.isVariableDeclaration(node)) declarations.set(node.name.getText(parsed), node.getText(parsed));
  ts.forEachChild(node, visit);
}
visit(parsed);

function setup(stopReceipt, readFinal = async () => undefined, deleteReceipt) {
  const owner = { turnId: 'turn-old', content: 'shown', thinking: 'thinking', steps: ['step'], questions: [{ id: 'question' }], events: [], timeline: [] };
  const streamAcc = { current: { session: owner } };
  const finalizers = { current: new Map() };
  const typingBuf = { current: { session: ' buffered' } };
  const callbacks = [];
  const messages = [];
  const stops = [];
  let streams = { session: { lifecycle: 'running' } };
  let aborted = 0;
  let sessions = [{ id: 'session', pendingTurnId: owner.turnId, sessionKey: 'native' }];
  const deletes = [];
  const notices = [];
  const control = {};
  runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/chatControl.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports: control, Date, Error });
  const context = {
    useCallback: callback => callback, finalizers, streamAcc, typingBuf, AbortSignal, reduceAgentEvents, recoverAgentTimeline, fetchLastTurn: readFinal,
    streamCtl: { current: { session: { abort() { aborted += 1; } } } },
    document: { visibilityState: 'visible' },
    setTimeout: callback => { callbacks.push(callback); return callbacks.length; },
    clearTimeout() {}, sessionStorage: { removeItem() {} },
    setSessions: update => { sessions = update(sessions); }, saveSessions() {},
    sessionsRef: { current: sessions }, nextSessionRevision: () => 1, projectStorageKey: key => key,
    activeIdRef: { current: 'other' }, setHistoryNotice: note => notices.push(note),
    deleteChatSession: async id => { deletes.push(['catalog', id]); return deleteReceipt?.(); },
    clearChatDraft() {}, loadPromptAttempt: () => undefined, forgetPromptAttempt() {},
    deleteRemoteSession: async id => { deletes.push(['remote', id]); },
    setStreams: update => { streams = update(streams); }, confirmChatStop: control.confirmChatStop,
    stopChat: (session, turn) => { stops.push([session, turn]); return stopReceipt?.() || Promise.resolve({ stopped: true, status: 'stopped' }); },
    flushTyping: session => {
      const current = streamAcc.current[session];
      if (current) current.content += typingBuf.current[session] || '';
      typingBuf.current[session] = '';
    },
    appendAssistant: (session, message) => messages.push({ session, message }),
    clearStream: session => { finalizers.current.delete(session); delete streamAcc.current[session]; },
  };
  const output = ts.transpileModule(
    `const ${declarations.get('confirmQueuedMessage')}; const ${declarations.get('failQueuedAdmission')};
     const ${declarations.get('finalizeStream')}; const ${declarations.get('handleStopStream')}; const ${declarations.get('executeSessionDelete')}; ({finalizeStream,handleStopStream,executeSessionDelete});`,
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } },
  ).outputText;
  return { ...runInNewContext(output, context), owner, streamAcc, typingBuf, finalizers,
    callbacks, messages, stops, deletes, notices, get aborted() { return aborted; }, get streams() { return streams; }, document: context.document };
}

test('done then stop finalizes complete reply exactly once, without backend cancellation', () => {
  const state = setup();
  state.finalizeStream('session', state.owner, 'native');
  assert.equal(state.messages.length, 0);
  state.handleStopStream('session', state.owner);
  state.callbacks.forEach(callback => callback());
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].message.content, 'shown buffered');
  assert.equal(state.messages[0].message.questions[0].id, 'question');
  assert.equal(state.stops.length, 0);
});

test('a delayed callback and delayed terminal event cannot erase the following turn', () => {
  const state = setup();
  state.finalizeStream('session', state.owner);
  state.handleStopStream('session', state.owner);
  const next = { content: 'next reply', thinking: '', steps: [], questions: [] };
  state.streamAcc.current.session = next;
  state.typingBuf.current.session = ' more';
  state.callbacks.forEach(callback => callback());
  state.finalizeStream('session', state.owner);
  assert.equal(state.messages.length, 1);
  assert.equal(state.streamAcc.current.session, next);
  assert.equal(state.typingBuf.current.session, ' more');
});

test('hidden-tab completion flushes all buffered text immediately and ignores duplicate done', () => {
  const state = setup();
  state.document.visibilityState = 'hidden';
  state.finalizeStream('session', state.owner);
  state.finalizeStream('session', state.owner);
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].message.content, 'shown buffered');
  assert.equal(state.callbacks.length, 0);
});

test('error completion preserves buffered text and activity only once', () => {
  const state = setup();
  const error = Object.assign(new Error('private diagnostic'), {
    failure: { code: 'authentication', retryable: false, phase: 'execution' },
  });
  state.finalizeStream('session', state.owner, undefined, error);
  state.handleStopStream('session', state.owner);
  state.callbacks.forEach(callback => callback());
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].message.content, 'shown buffered');
  assert.equal(state.messages[0].message.failure?.code, 'authentication');
  assert.equal(state.messages[0].message.activity, 'step');
});

test('a failed stop keeps the live stream and does not fabricate a stopped message', async () => {
  const state = setup(async () => ({ stopped: false, status: 'stopping' }));
  await state.handleStopStream('session', state.owner);
  assert.equal(state.aborted, 0);
  assert.equal(state.messages.length, 0);
  assert.equal(state.streamAcc.current.session, state.owner);
});

test('done racing with a confirmed stop records exactly one stopped outcome', async () => {
  let confirm;
  const state = setup(() => new Promise(resolve => { confirm = resolve; }));
  state.document.visibilityState = 'hidden';
  const pending = state.handleStopStream('session', state.owner);
  state.finalizeStream('session', state.owner);
  confirm({ stopped: true, status: 'stopped' });
  await pending;
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].message.status, 'stopped');
});
test('stopping a tools-only turn persists its execution evidence without requiring text', async () => {
  const state = setup();
  Object.assign(state.owner, { content: '', thinking: '', steps: [], questions: [],
    events: [{ id: 'event', kind: 'tool', callId: 'call', status: 'running' }] });
  state.typingBuf.current.session = '';
  await state.handleStopStream('session', state.owner);
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].message.events[0].callId, 'call');
});

test('mismatch keeps the old turn recoverable and a stale retry cannot target its replacement', async () => {
  const state = setup(async () => ({ stopped: false, status: 'turn_mismatch' }));
  await state.handleStopStream('session', state.owner);
  assert.deepEqual(state.stops, [['session', 'turn-old']]);
  assert.equal(state.aborted, 0);
  assert.equal(state.messages.length, 0);
  assert.equal(state.streams.session.stopFeedback.kind, 'unconfirmed');
  state.streamAcc.current.session = { turnId: 'turn-new' };
  await state.handleStopStream('session', state.owner);
  assert.equal(state.stops.length, 1);
});

test('an already-finished receipt without a finalizer keeps streaming for the terminal result', async () => {
  const state = setup(async () => ({ stopped: false, status: 'already_finished' }));
  await state.handleStopStream('session', state.owner);
  assert.equal(state.aborted, 0);
  assert.equal(state.messages.length, 0);
  assert.equal(state.streams.session.stopFeedback.kind, 'unconfirmed');
});

test('a network failure permits retry against the same turn with distinct feedback', async () => {
  let attempts = 0;
  const state = setup(async () => {
    if (attempts++ === 0) throw new Error('network failure');
    return { stopped: true, status: 'stopped' };
  });
  await state.handleStopStream('session', state.owner);
  assert.equal(state.streams.session.stopFeedback.kind, 'failed');
  await state.handleStopStream('session', state.owner);
  assert.deepEqual(state.stops, [['session', 'turn-old'], ['session', 'turn-old']]);
  assert.equal(state.messages[0].message.status, 'stopped');
});

test('deleting a running session waits for the exact stop and catalog receipt only', async () => {
  let confirm;
  const state = setup(() => new Promise(resolve => { confirm = resolve; }),
    async () => ({ status: 'done', turn_id: 'turn-old', outcome: 'stopped' }));
  const deletion = state.executeSessionDelete('session');
  assert.deepEqual(state.stops, [['session', 'turn-old']]);
  assert.deepEqual(state.deletes, []);
  confirm({ stopped: true, status: 'stopped' });
  assert.equal(await deletion, true);
  assert.deepEqual(state.deletes, [['catalog', 'session']]);
});

test('a mismatched stop leaves both histories and live stream intact on delete', async () => {
  const state = setup(async () => ({ stopped: false, status: 'turn_mismatch' }));
  await state.executeSessionDelete('session');
  assert.deepEqual(state.stops, [['session', 'turn-old']]);
  assert.deepEqual(state.deletes, []);
  assert.equal(state.notices.length, 1);
  assert.equal(state.aborted, 0);
});

test('legacy recovered turns without an identity never send an unbound stop', async () => {
  const state = setup();
  state.owner.turnId = undefined;
  await state.handleStopStream('session', state.owner);
  await state.executeSessionDelete('session');
  assert.deepEqual(state.stops, []);
  assert.deepEqual(state.deletes, []);
  assert.equal(state.streams.session.stopFeedback.kind, 'unconfirmed');
  assert.equal(state.aborted, 0);
});

test('a late confirmed stop cannot mutate the following local turn', async () => {
  let confirm;
  const state = setup(() => new Promise(resolve => { confirm = resolve; }));
  const request = state.handleStopStream('session', state.owner);
  const nextOwner = { turnId: 'turn-new' };
  state.streamAcc.current.session = nextOwner;
  confirm({ stopped: true, status: 'stopped' });
  await request;
  assert.deepEqual(state.stops, [['session', 'turn-old']]);
  assert.equal(state.streamAcc.current.session, nextOwner);
  assert.equal(state.aborted, 0);
  assert.equal(state.messages.length, 0);
});

test('a confirmed stop recovers final output that has not arrived on the live stream', async () => {
  const reads = [];
  const state = setup(undefined, async (session, turn) => {
    reads.push([session, turn]);
    return { status: 'done', outcome: 'stopped', turn_id: turn, text: 'complete stopped output',
      reasoning_summary: { text: '停止前最后的公开摘要', truncated: true },
      events: [{ id: 'final-tool', kind: 'tool', callId: 'call', status: 'completed' }] };
  });
  await state.handleStopStream('session', state.owner);
  assert.deepEqual(reads, [['session', 'turn-old']]);
  assert.equal(state.messages[0].message.content, 'complete stopped output\n\n_（已停止）_');
  assert.equal(state.messages[0].message.events[0].id, 'final-tool');
  assert.equal(state.messages[0].message.reasoningSummary?.text, '停止前最后的公开摘要');
});

test('finalized turn persists its public reasoning summary without legacy thinking', () => {
  const state = setup();
  state.document.visibilityState = 'hidden';
  state.owner.reasoningSummary = { text: '公开摘要', truncated: true };
  state.finalizeStream('session', state.owner, 'native');
  state.callbacks.forEach(callback => callback());
  assert.equal(state.messages[0].message.reasoningSummary?.text, '公开摘要');
  assert.equal(state.messages[0].message.reasoningSummary?.truncated, true);
  assert.equal(state.messages[0].message.thinking, undefined);
});

test('only a completed model item leaves answer evidence in a finalized turn', () => {
  const state = setup();
  state.document.visibilityState = 'hidden';
  state.finalizeStream('session', state.owner, 'native');
  assert.equal(state.messages[0].message.status, 'completed');
  assert.equal(state.messages[0].message.answerCompletion, undefined);

  const withEvidence = setup();
  withEvidence.document.visibilityState = 'hidden';
  withEvidence.owner.answerCompletion = { itemId: 'model-final-1' };
  withEvidence.finalizeStream('session', withEvidence.owner, 'native');
  assert.equal(withEvidence.messages[0].message.answerCompletion?.itemId, 'model-final-1');
});

test('a lost journal is an unconfirmed outcome even when the UI has a typed error', () => {
  const state = setup();
  state.document.visibilityState = 'hidden';
  state.finalizeStream('session', state.owner, undefined, Object.assign(new Error('lost journal'), {
    failure: { code: 'network', retryable: false, phase: 'finalization' }, unconfirmed: true,
  }));
  assert.equal(state.messages[0].message.status, 'unconfirmed');
  assert.equal(state.messages[0].message.failure?.code, 'network');
});


test('a failed directory deletion keeps the session and its runtime binding visible', async () => {
  const state = setup(undefined, async () => ({ status: 'done', turn_id: 'turn-old', outcome: 'stopped' }),
    async () => { throw new Error('network'); });
  assert.equal(await state.executeSessionDelete('session'), false);
  assert.deepEqual(state.deletes, [['catalog', 'session']]);
  assert.equal(state.streamAcc.current.session, state.owner);
  assert.equal(state.aborted, 0);
});
