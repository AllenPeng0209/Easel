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

function helper(name, context = {}) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL(`../../web/frontend/src/lib/${name}.ts`, import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(code, { exports, Date, Math, ...context });
  return exports;
}

const { nextSessionRevision, mergeSessionCatalog } = helper('sessionSync');
const { nextQueuedMessage, confirmChatStop } = helper('chatControl');
const chatReferences = helper('chatReferences');
const chatCommands = helper('chatCommands');
const plain = value => JSON.parse(JSON.stringify(value));
const settle = () => new Promise(resolve => setImmediate(resolve));

test('actual live timeline preserves ordered phases without retaining private output', () => {
  const events = helper('agentEvents');
  const timeline = helper('agentTimeline', { require: () => events });
  const owner = { turnId: 'turn', events: [], timeline: [] };
  const closed = [];
  const ui = appBoundary([], { ...events, ...timeline, closeQuestion: (_, question) => closed.push(question.id) });
  ui.context.streamAcc.current.chat = owner;
  ui.context.streams.chat = { events: [] };
  const receive = ui.callback('receiveAgentEvent');
  const send = (id, kind, fields) => receive('chat', owner, { id, runId: 'run', kind, ...fields });
  send('prepare', 'tool', { callId: 'tool', name: 'bash', status: 'preparing', output: 'private output' });
  send('question', 'decision', { questionId: 'q', status: 'pending' });
  send('decision', 'decision', { questionId: 'q', status: 'resolved', answers: { q: ['private answer'] } });
  send('run', 'tool', { callId: 'tool', name: 'bash', status: 'running' });
  send('progress', 'tool', { callId: 'tool', status: 'running', output: 'more private output' });
  send('done', 'tool', { callId: 'tool', name: 'bash', status: 'completed' });
  send('late', 'tool', { callId: 'tool', name: 'bash', status: 'running' });
  assert.deepEqual(plain(ui.context.streams.chat.timeline.map(row => row.id)), ['prepare', 'question', 'decision', 'run', 'done']);
  assert.doesNotMatch(JSON.stringify(ui.context.streams.chat.timeline), /private|answers|output/);
  assert.equal(owner.events.filter(event => event.kind === 'tool').length, 1);
  assert.equal(owner.events.some(event => event.status === 'pending'), false);
  assert.deepEqual(closed, ['q']);
});

test('timeline distinguishes reused tool IDs when native events only have turn scope', () => {
  const events = helper('agentEvents');
  const { recoverAgentTimeline } = helper('agentTimeline', { require: () => events });
  const wire = ['turn-a', 'turn-b'].map(turnId => ({
    id: 'same-event', callId: 'same-call', turnId, kind: 'tool', status: 'running',
  }));
  assert.deepEqual(plain(recoverAgentTimeline(wire).map(row => row.turnId)), ['turn-a', 'turn-b']);
});

test('timeline clipping is explicit and full recovery does not re-add clipped rows', () => {
  const events = helper('agentEvents');
  const { recoverAgentTimeline } = helper('agentTimeline', { require: () => events });
  const wire = Array.from({ length: 260 }, (_, index) => ({ id: String(index), runId: 'r', kind: 'context' }));
  const live = recoverAgentTimeline(wire.slice(0, 256));
  const restored = recoverAgentTimeline(wire, live);
  assert.equal(restored.length, 256);
  assert.equal(restored[0].id, '4');
  assert.equal(restored[0].earlierObservationsOmitted, true);
  assert.equal(restored.at(-1).id, '259');
});

function appBoundary(initial, overrides = {}) {
  const requests = [];
  const saved = [];
  const storage = new Map();
  const localStorage = overrides.localStorage || {
    getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key),
  };
  const projectStorageKey = value => `scope:${value}`;
  const prompt = helper('promptAdmission', { localStorage, require: name => name === './chatReferences' ? chatReferences : { projectStorageKey } });
  const context = vm.createContext({
    Date, Math, Error, Boolean, Array, AbortController, undefined, nextSessionRevision, mergeSessionCatalog, nextQueuedMessage,
    sessions: plain(initial), sessionsRef: { current: plain(initial) }, streams: {},
    streamCtl: { current: {} }, streamAcc: { current: {} }, typingBuf: { current: {} }, queueEdits: { current: {} },
    selectedPersona: 'another-brand-persona', historyReady: true, canOperate: true,
    projectStorageKey, localStorage, sessionStorage: { setItem() {}, getItem() { return null; }, removeItem() {} },
    ...prompt, ...chatReferences, ...chatCommands, admissionFeedback: {},
    loadChatDraft: () => ({ text: '', attachments: [] }), chatDraftOwner: () => 'composer',
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
  context.setAdmissionFeedback = update => { context.admissionFeedback = update(context.admissionFeedback); };
  context.clearStream ??= id => {
    delete context.streamCtl.current[id];
    delete context.streamAcc.current[id];
  };
  context.confirmPromptAdmission = compileExpression(callbackNode('confirmPromptAdmission'), context);
  context.failPromptAdmission = compileExpression(callbackNode('failPromptAdmission'), context);
  context.confirmQueuedMessage = compileExpression(callbackNode('confirmQueuedMessage'), context);
  context.failQueuedAdmission = compileExpression(callbackNode('failQueuedAdmission'), context);
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
    assert.equal(ui.context.sessions[0].messages.length, 0);
    ui.requests[0][19]();
    assert.equal(ui.context.sessions[0].messages[0].content, 'continue the draft');
    assert.notEqual(ui.requests[0][1], 'another-brand-persona');
  });
}

test('ordinary composer waits for matching admission before committing one echo', async () => {
  const ui = appBoundary([{ id: 'ordinary', title: 'Ordinary', created: 1, messages: [] }]);
  const send = ui.callback('handleSendMessage')('ordinary', 'wait for host', []);
  assert.equal(typeof send?.then, 'function');
  assert.equal(ui.context.sessions[0].messages.length, 0);
  assert.equal(ui.requests[0][20], true);
  ui.requests[0][19]();
  ui.requests[0][19]();
  assert.equal(await send, true);
  assert.equal(ui.context.sessions[0].messages.length, 1);
  assert.equal(ui.context.sessions[0].messages[0].content, 'wait for host');
});

test('pre-admission rejection preserves the previous transcript', async () => {
  const previous = [{ role: 'user', content: 'old user' }, { role: 'assistant', content: 'old answer' }];
  const ui = appBoundary([{ id: 'ordinary', title: 'Ordinary', created: 1, messages: previous }]);
  const send = ui.callback('handleSendMessage')('ordinary', 'rejected draft', []);
  ui.requests[0][4](Object.assign(new Error('Bad draft'), { status: 400 }));
  assert.equal(await send, false);
  assert.deepEqual(plain(ui.context.sessions[0].messages), previous);
});

test('uncertain send persists exact attachment and turn identity across refresh and changed draft', async () => {
  const files = [{ id: 'file1', name: 'brief.txt', path: '_inbox/s/b/brief.txt' }];
  const ui = appBoundary([{ id: 's', title: 'Draft', persona: 'original persona', created: 1, messages: [] }]);
  const pending = ui.callback('handleSendMessage')('s', 'frozen text', files);
  const first = ui.requests[0];
  first[4](new Error('receipt lost'));
  assert.equal(await pending, false);
  const refreshed = appBoundary(ui.context.sessions, { localStorage: ui.context.localStorage });
  const rejected = refreshed.callback('handleSendMessage')('s', 'newer text', []);
  assert.equal(rejected, false);
  assert.equal(refreshed.requests.length, 0);
  refreshed.callback('reconcilePromptAttempt')('s');
  const retry = refreshed.requests[0];
  assert.deepEqual(plain([retry[0], retry[1], retry[9], retry[12]]),
    plain([first[0], first[1], first[9], first[12]]));
  retry[19]();
  assert.equal(refreshed.context.sessions[0].messages[0].content, 'frozen text');
  assert.equal(refreshed.context.sessions[0].messages.length, 1);
});

test('cold admitted echo does not resend an identical restored composer draft', () => {
  const ui = appBoundary([{ id: 's', title: 'Draft', created: 1, messages: [] }]);
  ui.callback('handleSendMessage')('s', 'already accepted', []);
  const restored = appBoundary(ui.context.sessions, {
    localStorage: ui.context.localStorage,
    loadChatDraft: () => ({ text: 'already accepted', attachments: [] }),
  });
  restored.callback('resumePendingTurn')('s');
  assert.equal(restored.requests[0][10], true);
  restored.requests[0][19]();
  assert.equal(restored.context.sessions[0].messages[0].content, 'already accepted');
  assert.equal(restored.callback('handleSendMessage')('s', 'already accepted', []), true);
  assert.equal(restored.requests.length, 1);
  assert.equal(restored.context.sessions[0].messages.length, 1);
});

test('regeneration only truncates the original suffix after admission', () => {
  const transcript = [{ role: 'user', content: 'first' }, { role: 'assistant', content: 'old reply' }];
  const ui = appBoundary([{ id: 's', title: 'Draft', created: 1, messages: transcript }]);
  ui.context.sendUserAndStream('s', 'regenerated', [], undefined, 0);
  assert.deepEqual(plain(ui.context.sessions[0].messages), transcript);
  ui.context.sessions[0].messages.push({ role: 'assistant', content: 'newer tab reply' });
  ui.requests[0][19]();
  assert.deepEqual(plain(ui.context.sessions[0].messages.map(item => item.content)),
    ['first', 'old reply', 'newer tab reply', 'regenerated']);
  const unchanged = appBoundary([{ id: 's', title: 'Draft', created: 1, messages: transcript }]);
  unchanged.context.sendUserAndStream('s', 'regenerated', [], undefined, 0);
  unchanged.requests[0][19]();
  assert.deepEqual(plain(unchanged.context.sessions[0].messages.map(item => item.content)), ['regenerated']);
});

test('a background queued conversation preserves its own explicit persona', () => {
  const ui = appBoundary([{ id: 'education', title: 'Education', persona: 'education-brand', created: 1, messages: [],
    queued: [{ id: 'q1', text: 'write lesson one', created: 2 }] }]);
  ui.effect('nextQueuedMessage')();
  assert.equal(ui.requests[0][1], 'education-brand');
  assert.equal(ui.context.sessions[0].messages.length, 0);
  ui.requests[0][19]({ sessionId: 'education', turnId: 'queue-q1' });
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
  assert.equal(first.context.sessions[0].queued[0].delivery, 'sending');
  assert.equal(first.context.sessions[0].messages.length, 0);
  first.requests[0][19]();
  first.requests[0][19]();
  assert.equal(first.context.sessions[0].queued.length, 0);
  assert.equal(first.context.sessions[0].messages.length, 1);
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
  const agentEvents = helper('agentEvents');
  const timeline = helper('agentTimeline', { require: () => agentEvents });
  const nativeEvent = { id: 'event1', kind: 'tool', callId: 'render1', name: 'render', status: 'running',
    output: 'Submitted task-123' };
  const stopped = { ...nativeEvent, id: 'event2', status: 'interrupted' };
  const ui = appBoundary([{ id: 's1', title: 'Draft', created: 1, pendingTurnId: 'active-turn',
    messages: [{ role: 'user', content: 'prepare a draft' }] }], {
    ...agentEvents, ...timeline, confirmChatStop, AbortSignal,
    fetchLastTurn: async () => ({ status: 'done', turn_id: 'active-turn', outcome: 'stopped', text: '',
      events: [nativeEvent, stopped] }),
    stopChat: async () => ({ stopped: true, status: 'stopped' }),
    finalizers: { current: new Map() }, activeIdRef: { current: 's1' }, flushTyping() {},
  });
  ui.context.streamAcc.current.s1 = {
    turnId: 'active-turn', content: '', thinking: '', steps: [], events: [nativeEvent],
    timeline: timeline.observeAgentTimeline([], nativeEvent),
    questions: [{ id: 'question1', status: 'pending', questions: [] }],
  };
  ui.context.streamCtl.current.s1 = new AbortController();
  ui.context.streams.s1 = { content: '', thinking: '', activity: '', lifecycle: 'waiting_input' };
  ui.context.appendAssistant = ui.callback('appendAssistant');
  ui.context.clearStream = ui.callback('clearStream');
  await ui.callback('handleStopStream')('s1', ui.context.streamAcc.current.s1);
  const saved = ui.context.sessions[0];
  assert.equal(saved.messages.length, 2);
  assert.equal(saved.messages[1].status, 'stopped');
  assert.equal(saved.messages[1].events[0].callId, 'render1');
  assert.equal(saved.messages[1].events[0].output, 'Submitted task-123');
  assert.deepEqual(plain(saved.messages[1].timeline.map(row => row.status)), ['running', 'interrupted']);
  assert.equal(saved.messages[1].questions[0].id, 'question1');
  assert.equal(saved.pendingTurnId, undefined);
  assert.equal(saved.queuePaused, true);
  assert.equal(ui.context.streams.s1, undefined);
  assert.ok(ui.saved.some(catalog => catalog[0].messages[1]?.events?.[0]?.callId === 'render1'));
  assert.ok(ui.saved.some(catalog => catalog[0].messages[1]?.timeline?.length === 2));
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
  assert.deepEqual(plain(ui.requests[0][17]), ref);
  ui.requests[0][19]();
  assert.deepEqual(plain(ui.saved.at(-1)[0].messages[0].topicRef), ref);
  assert.equal(ui.context.sessions[0].title, idea.title);

  delete ui.context.sessions[0].pendingTurnId;
  ui.context.sessionsRef.current = plain(ui.context.sessions);
  ui.context.streamAcc.current = {};
  ui.context.streamCtl.current = {};
  ui.callback('handleResend')('topic-chat', 0, '重新起草');
  assert.deepEqual(plain(ui.requests[1][17]), ref);
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
  assert.deepEqual(plain(ui.requests[0][18]), reference);
  ui.requests[0][19]();
  assert.deepEqual(plain(ui.saved.at(-1)[0].messages[0].workshopRef), reference);
  assert.deepEqual(plain(ui.saved.at(-1)[1].messages), []);

  delete ui.context.sessions[0].pendingTurnId;
  ui.context.sessionsRef.current = plain(ui.context.sessions);
  ui.context.streamAcc.current = {};
  ui.context.streamCtl.current = {};
  ui.callback('handleResend')('origin', 0, '核对这个版本');
  assert.deepEqual(plain(ui.requests[1][18]), reference);
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
  assert.equal(saved.messages.at(-1).status, 'unconfirmed');
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

test('queue dispatch waits for editing and retains an unconfirmed row for exact retry', () => {
  const item = { id: 'q1', text: 'next task', created: 2 };
  const ui = appBoundary([{ id: 's1', title: 'Draft', created: 1, messages: [], queued: [item] }], {
    clearStream(id) {
      delete ui.context.streamAcc.current[id];
      delete ui.context.streamCtl.current[id];
    },
  });
  ui.context.queueEdits.current.s1 = 'q1';
  ui.effect('nextQueuedMessage')();
  assert.equal(ui.requests.length, 0);
  delete ui.context.queueEdits.current.s1;
  ui.effect('nextQueuedMessage')();
  const owner = ui.context.streamAcc.current.s1;
  assert.equal(ui.context.failQueuedAdmission('s1', owner, new Error('connection lost')), true);
  assert.equal(ui.context.sessions[0].queued[0].delivery, 'unconfirmed');
  assert.equal(ui.context.sessions[0].queuePaused, true);
  assert.equal(ui.context.sessions[0].messages.length, 0);
  ui.context.sessionsRef.current = plain(ui.context.sessions);
  assert.equal(ui.callback('editQueued')('s1', 'q1', 'changed'), false);
  ui.callback('resumeQueue')('s1');
  ui.context.sessionsRef.current = plain(ui.context.sessions);
  ui.effect('nextQueuedMessage')();
  assert.equal(ui.requests[1][9], ui.requests[0][9]);
  assert.equal(ui.requests[1][0], ui.requests[0][0]);
});

test('inline references rebase through trimming and persist with the matching user echo', async () => {
  const ui = appBoundary([{ id: 'file-chat', title: 'Chat', created: 1, messages: [] }]);
  const file = { id: 'one', name: 'a.txt', path: '_inbox/user/batch/a.txt' };
  const text = '  🖼 @a.txt  ';
  const start = text.indexOf('@');
  const sent = ui.callback('handleSendMessage')('file-chat', text, [file],
    [{ start, end: start + 6, attachmentId: 'one' }]);
  const payload = ui.requests[0];
  assert.equal(payload[0], '🖼 @a.txt');
  assert.deepEqual(plain(payload[21]), [{ start: 3, end: 9, attachmentId: 'one' }]);
  assert.equal(ui.context.sessions[0].messages.length, 0);
  payload[19]();
  assert.equal(await sent, true);
  assert.deepEqual(plain(ui.context.sessions[0].messages[0].references), plain(payload[21]));
});

test('mixed draft sends rebased session references without trusting the local label', async () => {
  const ui = appBoundary([{ id: 'target', title: 'Chat', created: 1, messages: [] }]);
  const text = '  🖼 @研究记录  ';
  const start = text.indexOf('@');
  const sessionReference = { start, end: start + '@研究记录'.length, sessionId: 'source',
    snapshotHash: 'a'.repeat(64), label: '研究记录' };
  const sent = ui.callback('handleSendMessage')('target', text, [], undefined, [sessionReference]);
  const payload = ui.requests[0];
  assert.equal(payload[0], '🖼 @研究记录');
  const expected = [{ start: 3, end: 8, sessionId: 'source', snapshotHash: 'a'.repeat(64) }];
  assert.deepEqual(plain(payload[22]), expected);
  assert.equal(ui.context.sessions[0].messages.length, 0);
  payload[19]();
  assert.equal(await sent, true);
  assert.deepEqual(plain(ui.context.sessions[0].messages[0].sessionReferences), expected);
});

test('an explicit command claim follows the accepted turn and ordinary text stays unclaimed', async () => {
  const ui = appBoundary([{ id: 'target', title: 'Chat', created: 1, messages: [] }]);
  const command = { id: 'social-draft', token: '/文案' };
  const sent = ui.callback('handleSendMessage')('target', '/文案 教育', [], undefined, undefined, command);
  const payload = ui.requests[0];
  assert.equal(payload[0], '/文案 教育');
  assert.deepEqual(plain(payload[23]), command);
  payload[19]();
  assert.equal(await sent, true);
  assert.deepEqual(plain(ui.context.sessions[0].messages[0].command), command);
  const plainSend = appBoundary([{ id: 'plain', title: 'Chat', created: 1, messages: [] }]);
  plainSend.callback('handleSendMessage')('plain', '/文案 教育');
  assert.equal(plainSend.requests[0][23], undefined);
});

test('live answer receipt updates only the exact active turn and does not finish it', () => {
  const ui = appBoundary([{ id: 's', title: 'Answer', created: 1, messages: [] }]);
  ui.callback('handleSendMessage')('s', 'model answer', []);
  const request = ui.requests[0];
  request[19]();
  request[24]({ itemId: 'answer-one' });
  assert.equal(ui.context.streams.s.answerCompletion.itemId, 'answer-one');
  assert.ok(ui.context.streamAcc.current.s);
  assert.equal(ui.context.sessions[0].messages.filter(row => row.role === 'assistant').length, 0);
  ui.context.streamAcc.current.s = { turnId: 'next-turn' };
  request[24]({ itemId: 'late-answer' });
  assert.equal(ui.context.streams.s.answerCompletion.itemId, 'answer-one');
  assert.equal(ui.context.streamAcc.current.s.answerCompletion, undefined);
});

test('cold recovery preserves explicit answer evidence and labels missing turn outcome unconfirmed', async () => {
  const captured = [];
  const agentEvents = helper('agentEvents');
  const ui = appBoundary([{ id: 's', title: 'Recovered', created: 1, pendingTurnId: 'turn',
    messages: [{ role: 'user', content: 'question' }] }], {
    fetchLastTurn: async () => ({ status: 'done', turn_id: 'turn', text: 'saved reply',
      answer_completed: { itemId: 'saved-answer' } }),
    recoverAgentTimeline: helper('agentTimeline', { require: () => agentEvents }).recoverAgentTimeline,
    appendAssistant: (_, message) => captured.push(message),
  });
  ui.callback('resumePendingTurn')('s');
  ui.requests[0][11]();
  await settle();
  assert.equal(captured[0].answerCompletion.itemId, 'saved-answer');
  assert.equal(captured[0].status, 'unconfirmed');
});

test('unsafe failed or unconfirmed turns cannot be replayed, and a safe retry retains the previous failure', () => {
  const create = (status, retryable, events) => appBoundary([{ id: 's', title: 'Failure', created: 1, messages: [
    { role: 'user', content: 'original' },
    { role: 'assistant', content: '', status, events, failure: { code: 'network', retryable, phase: 'startup' } },
  ] }]);
  for (const [status, retryable] of [['failed', false], ['unconfirmed', true]]) {
    const ui = create(status, retryable);
    ui.callback('handleResend')('s', 0, 'original');
    assert.equal(ui.requests.length, 0);
  }
  const withPublication = create('failed', true, [{ kind: 'publication', publication: { state: 'submitted' } }]);
  withPublication.callback('handleResend')('s', 0, 'original');
  assert.equal(withPublication.requests.length, 0);

  const ui = create('failed', true);
  ui.callback('handleResend')('s', 0, 'original');
  assert.equal(ui.requests.length, 1);
  ui.requests[0][19]();
  assert.deepEqual(plain(ui.context.sessions[0].messages.map(row => row.role)), ['user', 'assistant', 'user']);
  assert.equal(ui.context.sessions[0].messages[1].failure.code, 'network');
});

test('approved plan execution keeps its exact reference through chat admission', async () => {
  const ui = appBoundary([{ id: 's', title: 'Plan', created: 1, messages: [] }], { canOperate: true });
  const plan = { planId: 'a'.repeat(32), revision: 2, digest: 'b'.repeat(64), text: 'Approved plan content' };
  const accepted = ui.callback('handleExecutePlan')('s', plan);
  assert.equal(ui.requests.length, 1);
  const request = ui.requests[0];
  assert.equal(request[9], `plan-${plan.planId}-2`);
  assert.deepEqual(plain(request[25]), { planId: plan.planId, revision: 2, digest: plan.digest });
  request[19]();
  assert.equal(await accepted, true);
  assert.equal(ui.context.sessions[0].messages[0].planRef.digest, plan.digest);
  assert.equal(ui.context.sessions[0].messages[0].content, plan.text);
});
