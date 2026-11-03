import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');

// Execute the actual application callbacks, not copies of their algorithms.
// Host I/O (SSE, storage and tab messages) stays at the boundary; state changes,
// queue consumption, identity selection and session merge use production code.
function containsCall(node, name) {
  if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === name) return true;
  return Boolean(ts.forEachChild(node, child => containsCall(child, name)));
}

function callbackNode(name) {
  for (const statement of app.body.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (ts.isIdentifier(declaration.name) && declaration.name.text === name) {
        const call = declaration.initializer;
        if (ts.isCallExpression(call) && call.expression.getText(ast) === 'useCallback') return call.arguments[0];
      }
    }
  }
  throw new Error(`Application callback not found: ${name}`);
}

function effectNode(ownedCall) {
  const effect = app.body.statements.find(statement => ts.isExpressionStatement(statement)
    && ts.isCallExpression(statement.expression) && statement.expression.expression.getText(ast) === 'useEffect'
    && containsCall(statement.expression.arguments[0], ownedCall));
  if (!effect) throw new Error(`Application effect not found: ${ownedCall}`);
  return effect.expression.arguments[0];
}

function compileExpression(node, context) {
  const code = ts.transpileModule(`(${node.getText(ast)});`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  }).outputText;
  return vm.runInContext(code, context);
}

function helper(name) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL(`../../web/frontend/src/lib/${name}.ts`, import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(code, { exports, Date, Math });
  return exports;
}

const { nextSessionRevision, mergeSessionCatalog } = helper('sessionSync');
const { nextQueuedMessage, confirmChatStop } = helper('chatControl');
const plain = value => JSON.parse(JSON.stringify(value));
const settle = () => new Promise(resolve => setImmediate(resolve));

function appBoundary(initial, overrides = {}) {
  const requests = [];
  const saved = [];
  const context = vm.createContext({
    Date, Math, Error, Boolean, Array, undefined, nextSessionRevision, mergeSessionCatalog, nextQueuedMessage,
    sessions: plain(initial), sessionsRef: { current: plain(initial) }, streams: {},
    streamCtl: { current: {} }, streamAcc: { current: {} }, typingBuf: { current: {} },
    selectedPersona: 'another-brand-persona', historyReady: true, canOperate: true,
    projectStorageKey: value => `scope:${value}`, sessionStorage: { setItem() {} },
    saveSessions: value => saved.push(plain(value)), updateSessionTitle() {},
    startTypingPump() {}, ensureTypingPump() {}, finalizeStream() {},
    receiveQuestion() {}, receiveAgentEvent() {}, closeQuestion() {}, resumePendingTurn() {},
    setUnreadSessions() {},
    streamChat: (...args) => { requests.push(args); return new AbortController(); },
    ...overrides,
  });
  context.setSessions = update => {
    context.sessions = typeof update === 'function' ? update(context.sessions) : update;
    // React updates sessionsRef in a later effect, not in the same callback
    // stack that schedules setSessions (important for initial hydration).
  };
  context.setStreams = update => {
    context.streams = typeof update === 'function' ? update(context.streams) : update;
  };
  context.startStream = compileExpression(callbackNode('startStream'), context);
  context.sendUserAndStream = compileExpression(callbackNode('sendUserAndStream'), context);
  return { context, requests, saved,
    callback: name => compileExpression(callbackNode(name), context),
    effect: name => compileExpression(effectNode(name), context),
  };
}

for (const persona of [undefined, '']) {
  test(`a saved general conversation (${persona === '' ? 'empty' : 'unset'} persona) never borrows another active brand`, () => {
    const ui = appBoundary([{ id: 'general', title: 'General', persona, created: 1, messages: [] }]);
    assert.equal(ui.context.sendUserAndStream('general', 'continue the draft'), true);
    assert.equal(ui.requests.length, 1);
    assert.equal(ui.requests[0][1], persona);
    assert.equal(ui.context.sessions[0].messages[0].content, 'continue the draft');
    assert.notEqual(ui.requests[0][1], 'another-brand-persona');
  });
}

test('a background queued conversation preserves its own explicit persona', () => {
  const ui = appBoundary([{ id: 'education', title: 'Education', persona: 'education-brand', created: 1, messages: [],
    queued: [{ id: 'q1', text: 'write lesson one', created: 2 }] }]);
  ui.effect('nextQueuedMessage')();
  assert.equal(ui.requests[0][1], 'education-brand');
  assert.equal(ui.context.sessions[0].messages[0].content, 'write lesson one');
});

test('the same persisted queue entry has one stable transport identity in two browser instances', () => {
  const initial = [{ id: 's1', title: 'Draft', created: 1, messages: [], queued: [{
    id: 'persisted-q7', text: 'next task', created: 2,
    attachments: [{ id: 'file1', path: '_inbox/s1/b/source.pdf', name: 'source.pdf' }],
  }] }];
  const first = appBoundary(initial);
  const second = appBoundary(initial);
  first.effect('nextQueuedMessage')();
  second.effect('nextQueuedMessage')();
  assert.equal(first.requests.length, 1);
  assert.equal(second.requests.length, 1);
  assert.equal(first.requests[0][9], 'queue-persisted-q7');
  assert.equal(second.requests[0][9], 'queue-persisted-q7');
  assert.equal(first.context.sessions[0].pendingTurnId, 'queue-persisted-q7');
  assert.equal(first.context.sessions[0].queued.length, 0);
  assert.equal(first.context.sessions[0].messages[0].attachments[0].name, 'source.pdf');
  // The active in-memory owner prevents an effect rerun from submitting again.
  first.effect('nextQueuedMessage')();
  assert.equal(first.requests.length, 1);
});

test('paused queues and a prior pending turn do not silently start a new request', () => {
  const ui = appBoundary([
    { id: 'paused', title: 'Paused', created: 1, messages: [], queuePaused: true,
      queued: [{ id: 'q-paused', text: 'must wait', created: 2 }] },
    { id: 'recover', title: 'Recover', created: 1, pendingTurnId: 'prior-turn',
      messages: [{ role: 'user', content: 'already submitted' }], queued: [{ id: 'q-recover', text: 'after prior', created: 2 }] },
  ]);
  ui.effect('nextQueuedMessage')();
  assert.equal(ui.requests.length, 0);
  assert.equal(ui.context.sessions[0].queued[0].id, 'q-paused');
  assert.equal(ui.context.sessions[1].queued[0].id, 'q-recover');
});

test('a late closure from an old turn updates history without clearing the new waiting-input state', () => {
  const ui = appBoundary([{ id: 's1', title: 'Draft', created: 1, messages: [{ role: 'assistant', content: '',
    questions: [{ id: 'old-question', status: 'pending', questions: [] }] }] }]);
  ui.context.streamAcc.current.s1 = { questions: [{ id: 'new-question', status: 'pending', questions: [] }] };
  ui.context.streams.s1 = { lifecycle: 'waiting_input', questions: plain(ui.context.streamAcc.current.s1.questions) };
  ui.callback('closeQuestion')('s1', { id: 'old-question', status: 'resolved', answers: { tone: ['calm'] } });
  assert.equal(ui.context.streams.s1.lifecycle, 'waiting_input');
  assert.equal(ui.context.streams.s1.questions[0].id, 'new-question');
  assert.equal(ui.context.streams.s1.questions[0].status, 'pending');
  assert.equal(ui.context.sessions[0].messages[0].questions[0].status, 'resolved');
});

test('closing one question keeps another pending question visible and never overrides stopping', () => {
  const ui = appBoundary([{ id: 's1', title: 'Draft', created: 1, messages: [] }]);
  ui.context.streamAcc.current.s1 = { questions: [
    { id: 'one', status: 'pending', questions: [] }, { id: 'two', status: 'pending', questions: [] },
  ] };
  ui.context.streams.s1 = { lifecycle: 'waiting_input' };
  const close = ui.callback('closeQuestion');
  close('s1', { id: 'one', status: 'resolved' });
  assert.equal(ui.context.streams.s1.lifecycle, 'waiting_input');
  ui.context.streams.s1.lifecycle = 'stopping';
  close('s1', { id: 'two', status: 'resolved' });
  assert.equal(ui.context.streams.s1.lifecycle, 'stopping');
});

test('confirmed stop persists a turn containing only tool events and an input card', async () => {
  const ui = appBoundary([{ id: 's1', title: 'Draft', created: 1, pendingTurnId: 'active-turn',
    messages: [{ role: 'user', content: 'prepare a draft' }] }], {
    confirmChatStop, stopChat: async () => ({ stopped: true, status: 'stopped' }),
    finalizers: { current: new Map() }, activeIdRef: { current: 's1' }, flushTyping() {},
  });
  const nativeEvent = { id: 'event1', kind: 'tool', callId: 'render1', name: 'render', status: 'running',
    output: 'Submitted task-123' };
  ui.context.streamAcc.current.s1 = {
    content: '', thinking: '', steps: [], events: [nativeEvent],
    questions: [{ id: 'question1', status: 'pending', questions: [] }],
  };
  ui.context.streamCtl.current.s1 = new AbortController();
  ui.context.streams.s1 = { content: '', thinking: '', activity: '', lifecycle: 'waiting_input' };
  ui.context.appendAssistant = ui.callback('appendAssistant');
  ui.context.clearStream = ui.callback('clearStream');
  await ui.callback('handleStopStream')('s1');
  const saved = ui.context.sessions[0];
  assert.equal(saved.messages.length, 2);
  assert.equal(saved.messages[1].status, 'stopped');
  assert.equal(saved.messages[1].events[0].callId, 'render1');
  assert.equal(saved.messages[1].events[0].output, 'Submitted task-123');
  assert.equal(saved.messages[1].questions[0].id, 'question1');
  assert.equal(saved.pendingTurnId, undefined);
  assert.equal(saved.queuePaused, true);
  assert.equal(ui.context.streams.s1, undefined);
  assert.ok(ui.saved.some(catalog => catalog[0].messages[1]?.events?.[0]?.callId === 'render1'));
});

test('fast server hydration waits for delayed tab ownership before restoring remote history', async () => {
  const timers = new Map();
  let clock = 0;
  let fetches = 0;
  const local = [{ id: 'local-empty', title: 'Local', created: 1, updated: 1, messages: [] }];
  const remote = [{ id: 'remote-history', title: 'Recovered', created: 2, updated: 2,
    persona: 'remote-persona', messages: [{ role: 'user', content: 'saved on another browser' }] }];
  class Channel {
    addEventListener() {}
    removeEventListener() {}
    postMessage() {}
    close() {}
  }
  const ui = appBoundary(local, {
    navigationReady: false, BroadcastChannel: Channel,
    TAB_SESSION_KEY: 'scope:easel_tab_session', activeIdRef: { current: null },
    loadSessions: () => plain(local), loadActiveId: () => 'local-empty',
    sessionStorage: { getItem: () => null, setItem() {} },
    setTimeout: (callback, milliseconds) => { const id = ++clock; timers.set(id, { callback, milliseconds }); return id; },
    clearTimeout: id => timers.delete(id),
    fetchChatSessions: async () => { fetches += 1; return { sessions: plain(remote), deletedIds: [] }; },
    setHistoryNotice() {}, setHistoryReady: value => { ui.context.historyReady = value; },
    createSession: () => { throw new Error('The unclaimed local session should be reused'); },
  });
  ui.context.setNavigationReady = value => { ui.context.navigationReady = value; };
  ui.context.setActiveSessionId = value => { ui.context.activeSessionId = value; };
  ui.context.setSelectedPersona = value => { ui.context.selectedPersona = value; };
  const cleanup = ui.effect('loadSessions')();
  ui.effect('fetchChatSessions')();
  await settle();
  assert.equal(fetches, 0);
  assert.equal(ui.context.navigationReady, false);
  assert.equal(ui.context.sessions.length, 1);
  const pending = [...timers.entries()].find(([, value]) => value.milliseconds === 250);
  assert.ok(pending);
  timers.delete(pending[0]);
  pending[1].callback();
  assert.equal(ui.context.navigationReady, true);
  const cleanupHydration = ui.effect('fetchChatSessions')();
  await settle();
  assert.equal(fetches, 1);
  assert.ok(ui.context.sessions.some(session => session.id === 'remote-history'));
  assert.equal(ui.context.activeSessionId, 'remote-history');
  assert.equal(ui.context.selectedPersona, 'remote-persona');
  // No stale tab-probe callback remains to replace the merged remote catalog.
  assert.equal(timers.size, 0);
  assert.equal(ui.context.historyReady, true);
  cleanupHydration?.();
  cleanup?.();
});

test('saved idea starts a conversation with the exact inspected snapshot and retries with the same reference', async () => {
  const idea = { id: 'legacy-idea', title: '已保存的选题', snapshotHash: 'a'.repeat(64) };
  const ui = appBoundary([], {
    businessNavigation: { current: 0 },
    setBusinessNotice() {}, setActiveSessionId() {}, setCurrentPage() {},
    createSession: () => ({ id: 'topic-chat', title: 'New Chat', messages: [], created: 1 }),
    fetchBusinessObject: async (kind, id) => {
      assert.equal(kind, 'idea');
      assert.equal(id, idea.id);
      return idea;
    },
  });
  await ui.callback('handleUseSavedIdea')(idea.id);
  const ref = { id: idea.id, snapshotHash: idea.snapshotHash };
  assert.deepEqual(plain(ui.requests[0].at(-2)), ref);
  assert.deepEqual(plain(ui.saved.at(-1)[0].messages[0].topicRef), ref);
  assert.equal(ui.context.sessions[0].title, idea.title);

  ui.context.sessionsRef.current = plain(ui.context.sessions);
  ui.context.streamAcc.current = {};
  ui.context.streamCtl.current = {};
  ui.callback('handleResend')('topic-chat', 0, '重新起草');
  assert.deepEqual(plain(ui.requests[1].at(-2)), ref);
});

test('workshop handoff sends the selected delivery into its originating chat and keeps it on retry', () => {
  const reference = { id: 'a'.repeat(32), revision: 9, artifactId: 'b'.repeat(32), artifactSha256: 'c'.repeat(64) };
  const notices = [];
  const ui = appBoundary([
    { id: 'origin', title: 'Origin', created: 1, messages: [] },
    { id: 'other', title: 'Other', created: 2, messages: [] },
  ], { setBusinessNotice: value => notices.push(value), setWorkshopPanel() {}, setCurrentPage() {} });
  const handoff = ui.callback('handleUseWorkshop');
  ui.context.sessionsRef.current[0].pendingTurnId = 'running';
  handoff(reference, 'origin');
  assert.equal(ui.requests.length, 0);
  assert.match(notices.at(-1), /原对话/);
  delete ui.context.sessionsRef.current[0].pendingTurnId;
  handoff(reference, 'origin');
  assert.equal(ui.requests[0][2], 'origin');
  assert.deepEqual(plain(ui.requests[0].at(-1)), reference);
  assert.deepEqual(plain(ui.saved.at(-1)[0].messages[0].workshopRef), reference);
  assert.deepEqual(plain(ui.saved.at(-1)[1].messages), []);

  ui.context.sessionsRef.current = plain(ui.context.sessions);
  ui.context.streamAcc.current = {};
  ui.context.streamCtl.current = {};
  ui.callback('handleResend')('origin', 0, '核对这个版本');
  assert.deepEqual(plain(ui.requests[1].at(-1)), reference);
});

test('completed results outside the visible chat persist unread without mislabeling failures', () => {
  const ui = appBoundary([{ id: 'task', title: 'Task', messages: [], created: 1 }], {
    chatOrigin: { current: null },
    document: { visibilityState: 'visible' },
  });
  const append = ui.callback('appendAssistant');
  append('task', { role: 'assistant', content: '完成', status: 'completed' });
  assert.equal(ui.saved.at(-1)[0].unreadCompletion, true);
  append('task', { role: 'assistant', content: '失败', status: 'failed' });
  assert.equal(ui.saved.at(-1)[0].unreadCompletion, false);
  ui.context.chatOrigin.current = 'task';
  ui.context.document.visibilityState = 'hidden';
  append('task', { role: 'assistant', content: '后台完成', status: 'completed' });
  assert.equal(ui.saved.at(-1)[0].unreadCompletion, true);
  ui.context.document.visibilityState = 'visible';
  append('task', { role: 'assistant', content: '可见完成', status: 'completed' });
  assert.equal(ui.saved.at(-1)[0].unreadCompletion, false);
});

test('unconfirmed terminal events preserve output without marking it completed unread', () => {
  const ui = appBoundary([{ id: 's1', title: 'Draft', created: 1, messages: [] }], {
    finalizers: { current: new Map() }, chatOrigin: { current: null },
    document: { visibilityState: 'hidden' }, flushTyping() {},
  });
  const owner = { content: '部分输出', thinking: '', steps: [], events: [], questions: [] };
  ui.context.streamAcc.current.s1 = owner;
  ui.context.appendAssistant = ui.callback('appendAssistant');
  ui.context.clearStream = ui.callback('clearStream');
  ui.callback('finalizeStream')('s1', owner, 'key', undefined, 'unconfirmed');
  const saved = ui.saved.at(-1)[0];
  assert.equal(saved.messages.at(-1).content, '部分输出');
  assert.equal(saved.messages.at(-1).status, undefined);
  assert.equal(saved.unreadCompletion, false);
});

test('opening an unread chat in a hidden tab defers the read acknowledgement', () => {
  const item = { id: 's1', title: 'Draft', created: 1, messages: [], unreadCompletion: true };
  const events = new Map();
  const ui = appBoundary([item], {
    currentPage: 'chat', activeSessionId: 's1', activeSession: item, chatOrigin: { current: 's1' },
    document: { visibilityState: 'hidden',
      addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) },
  });
  const cleanup = ui.effect('markSeen')();
  assert.equal(ui.saved.length, 0);
  ui.context.document.visibilityState = 'visible';
  events.get('visibilitychange')();
  assert.equal(ui.saved.at(-1)[0].unreadCompletion, false);
  cleanup();
});

test('Steer reuses the same request after an uncertain reply and binds to the running turn', async () => {
  const sent = [];
  const stored = new Map();
  const ui = appBoundary([{ id: 'chat', title: 'Chat', created: 1, messages: [] }], {
    crypto: { randomUUID: () => 'steer-request' },
    sessionStorage: { getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key) },
    steerChat: async (...args) => {
      sent.push(args);
      return { status: sent.length === 1 ? 'unconfirmed' : 'accepted' };
    },
  });
  const owner = { turnId: 'active-turn', questions: [], events: [] };
  ui.context.streamAcc.current.chat = owner;
  ui.context.streams.chat = { lifecycle: 'running', steerTurnId: 'previous-turn' };
  const steer = ui.callback('handleSteer');
  await assert.rejects(steer('chat', '调整方向'), /无法接收指导/);
  assert.equal(sent.length, 0);
  ui.context.streams.chat.steerTurnId = 'active-turn';
  await assert.rejects(steer('chat', '调整方向'), /回执待核实/);
  owner.steerAttempt = undefined;
  await assert.rejects(steer('chat', '不同的指导'), /上一条指导/);
  assert.equal(await steer('chat', '调整方向'), true);
  assert.deepEqual(sent, [
    ['chat', 'active-turn', 'steer-request', '调整方向'],
    ['chat', 'active-turn', 'steer-request', '调整方向'],
  ]);
  owner.steerAttempt = undefined;
  assert.equal(await steer('chat', '调整方向'), true);
  assert.deepEqual(sent.at(-1), ['chat', 'active-turn', 'steer-request', '调整方向']);
  assert.equal(ui.requests.length, 0);
});
