import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const text = node => typeof node === 'string' || typeof node === 'number' ? String(node)
  : !node || typeof node !== 'object' ? '' : [node.props?.children].flat(Infinity).map(text).join('');
const settle = () => new Promise(resolve => setImmediate(resolve));

function view({ onSend = () => true, onSteer, stream, initial = '', interruptedFiles = [], queueProps = {}, sharedDrafts, validate } = {}) {
  const local = sharedDrafts ?? new Map(initial ? [['scope:easel_chat_draft:s1', JSON.stringify({ text: initial, attachments: [], interruptedFiles })]] : []);
  const localStorage = { getItem: key => local.get(key) ?? null, setItem: (key, value) => local.set(key, value), removeItem: key => local.delete(key) };
  const slots = []; let cursor = 0; let effects = []; let nodes = [];
  const hooks = {
    useState(initialValue) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initialValue === 'function' ? initialValue() : initialValue;
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useRef(initialValue) { const i = cursor++; if (!(i in slots)) slots[i] = { current: initialValue }; return slots[i]; },
    useEffect(effect, deps) { const i = cursor++; const old = slots[i];
      if (!old || !deps || deps.some((value, n) => !Object.is(value, old.deps?.[n]))) { slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); }); } },
    useMemo(factory, deps) { const i = cursor++; const old = slots[i];
      if (!old || !deps || deps.some((value, n) => !Object.is(value, old.deps?.[n]))) slots[i] = { deps, value: factory() };
      return slots[i].value; },
    useCallback(callback, deps) { return hooks.useMemo(() => callback, deps); },
  };
  hooks.useLayoutEffect = hooks.useEffect;
  const cache = new Map();
  const jsx = (type, props, key) => ({ type, props, key });
  const globals = { File, URL, AbortController, Date, Error, crypto, localStorage, sessionStorage: localStorage, console,
    queueMicrotask, setTimeout, clearTimeout, requestAnimationFrame: callback => setTimeout(callback, 0), cancelAnimationFrame: clearTimeout,
    window: { matchMedia: () => ({ matches: false }) }, navigator: { clipboard: { writeText: async () => {} } } };
  function load(path) {
    if (cache.has(path)) return cache.get(path);
    const exports = {}; cache.set(path, exports);
    const code = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText;
    runInNewContext(code, { ...globals, exports, require: name => {
      if (name === 'react') return hooks;
      if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
      if (name.endsWith('.css')) return {};
      if (name.endsWith('projectScope')) return { projectStorageKey: key => `scope:${key}`, projectUrl: value => value,
        projectFetch: async (url, options) => url.endsWith('/upload/validate') && validate
          ? validate(options) : ({ json: async () => ({ max_mb: 50 }) }) };
      if (name.endsWith('/api')) return { uploadFiles: async () => [], adoptOversize: async () => [], mediaUrl: path => `/api/media/${path}` };
      if (name === './StructuredComposerEditor') return { __esModule: true, default: 'structured-editor' };
      if (name === './MessageBubble') return { __esModule: true, default: 'message-bubble' };
      if (name.includes('QuestionCards') || name.includes('BrushEntry')) return { default: () => null };
      if (name === './AgentSubagentLineage') return { SubagentTranscript: () => null };
      if (name.endsWith('icons')) return new Proxy({}, { get: (_target, key) => String(key) });
      if (name.startsWith('.')) {
        const target = new URL(name, `file://${path}`);
        return load(/\.tsx?$/.test(target.pathname) ? target.pathname
          : existsSync(`${target.pathname}.ts`) ? `${target.pathname}.ts` : `${target.pathname}.tsx`);
      }
      return require(name);
    } });
    return exports;
  }
  const component = load(new URL('../../web/frontend/src/components/ChatPage.tsx', import.meta.url).pathname).default;
  const props = { apiDirect: false, session: { id: 's1', title: 'Draft', created: 1, messages: [] }, stream,
    onSend, onSteer, onStop() {}, onResend() {}, ...queueProps };
  const render = () => { cursor = 0; effects = []; nodes = [];
    const walk = node => { if (!node || typeof node !== 'object') return;
      // Child components are outside this test's scope; host nodes expose ChatPage behavior.
      if (typeof node.type === 'function') return;
      nodes.push(node); [node.props?.children].flat(Infinity).forEach(walk); };
    walk(component(props)); effects.splice(0).forEach(run => run()); return nodes; };
  return { render, get nodes() { return nodes; }, local,
    unmount() { for (const slot of slots) slot?.cleanup?.(); },
    async ready() { render(); await settle(); return render(); } };
}

test('a rejected submission leaves the editable text and persisted draft intact', async () => {
  const ui = view({ onSend: () => false }); let nodes = await ui.ready();
  nodes.find(node => node.type === 'structured-editor').props.onChange('keep this draft', []);
  nodes = ui.render();
  await nodes.find(node => node.type === 'button' && node.props.title === '发送').props.onClick();
  nodes = await ui.ready();
  assert.equal(nodes.find(node => node.type === 'structured-editor').props.value, 'keep this draft');
});

test('an accepted submission clears its draft only after the admission promise resolves', async () => {
  let accept; const ui = view({ onSend: () => new Promise(resolve => { accept = resolve; }) });
  let nodes = await ui.ready(); nodes.find(node => node.type === 'structured-editor').props.onChange('pending admission', []);
  nodes = ui.render(); const result = nodes.find(node => node.type === 'button' && node.props.title === '发送').props.onClick();
  assert.equal(ui.render().find(node => node.type === 'structured-editor').props.value, 'pending admission');
  accept(true); await result; await settle();
  assert.equal(ui.render().find(node => node.type === 'structured-editor').props.value, '');
});

test('a running conversation can submit the next message to the queue without stopping', async () => {
  const sent = []; const ui = view({ stream: { content: 'working', thinking: '', activity: '', questions: [] },
    onSend: (...args) => { sent.push(args); return true; } });
  let nodes = await ui.ready(); nodes.find(node => node.type === 'structured-editor').props.onChange('next task', []);
  nodes = ui.render(); const queue = nodes.find(node => node.type === 'button' && node.props.title === '加入队列');
  assert.ok(queue, 'Streaming must keep a distinct next-message submission control');
  await queue.props.onClick();
  assert.equal(sent[0][0], 'next task');
});

test('opening the same conversation restores its unsent draft', async () => {
  const ui = view({ initial: 'saved draft' });
  const nodes = await ui.ready();
  assert.equal(nodes.find(node => node.type === 'structured-editor').props.value, 'saved draft');
});

test('waiting for human input is distinguished from generation in the conversation header', async () => {
  const ui = view({ stream: { content: '', thinking: '', activity: '', questions: [], lifecycle: 'waiting_input' } });
  const nodes = await ui.ready();
  const status = nodes.find(node => node.props?.className?.startsWith('chat-session-status'));
  assert.equal(text(status), '等待补充信息');
});

test('pending admission freezes the attachment selection instead of silently clearing newly dropped files', async () => {
  let accept;
  const ui = view({ onSend: () => new Promise(resolve => { accept = resolve; }) });
  let nodes = await ui.ready();
  nodes.find(node => node.type === 'structured-editor').props.onChange('pending message', []);
  nodes = ui.render();
  const pending = nodes.find(node => node.type === 'button' && node.props.title === '发送').props.onClick();
  nodes = ui.render();
  nodes.find(node => node.props?.onDrop).props.onDrop({ preventDefault() {}, dataTransfer: { files: [new File(['x'], 'late.txt')] } });
  nodes = ui.render();
  assert.ok(nodes.some(node => node.props?.role === 'alert' && text(node).includes('接收中')));
  assert.ok(!nodes.some(node => node.props?.className?.startsWith('composer-file ')));
  accept(false); await pending;
});

const running = { content: 'working', thinking: '', activity: '', questions: [], lifecycle: 'running' };
const control = (nodes, title) => nodes.find(node => node.type === 'button' && node.props.title === title);
const editor = nodes => nodes.find(node => node.type === 'structured-editor');

test('late admission after remount cannot erase the new composer draft', async () => {
  let accept;
  const old = view({ initial: 'sent draft', onSend: () => new Promise(resolve => { accept = resolve; }) });
  const sent = control(await old.ready(), '发送').props.onClick();
  old.unmount();
  const current = view({ sharedDrafts: old.local });
  editor(await current.ready()).props.onChange('new draft after navigation', []);
  current.render();
  accept(true);
  await sent;
  const reopened = view({ sharedDrafts: old.local });
  assert.equal(editor(await reopened.ready()).props.value, 'new draft after navigation');
});

test('late admission can consume its original draft if no other composer has taken over', async () => {
  let accept;
  const old = view({ initial: 'sent draft', interruptedFiles: [{ name: 'interrupted.txt', size: 2, mime: 'text/plain' }],
    onSend: () => new Promise(resolve => { accept = resolve; }) });
  const sent = control(await old.ready(), '发送').props.onClick();
  old.unmount();
  accept(true);
  await sent;
  const restored = await view({ sharedDrafts: old.local }).ready();
  assert.equal(editor(restored).props.value, '');
  assert.ok(restored.some(node => node.props?.role === 'status' && text(node).includes('interrupted.txt')));
});

test('accepted cleanup in one mounted composer cannot overwrite a newer writer in another tab', async () => {
  let accept;
  const old = view({ initial: 'sent draft', onSend: () => new Promise(resolve => { accept = resolve; }) });
  const sent = control(await old.ready(), '发送').props.onClick();
  const current = view({ sharedDrafts: old.local });
  editor(await current.ready()).props.onChange('newer draft', []);
  current.render();
  accept(true);
  await sent;
  assert.equal(editor(old.render()).props.value, '');
  assert.equal(editor(await view({ sharedDrafts: old.local }).ready()).props.value, 'newer draft');
});

test('unrelated send clears text but preserves interrupted file reminders in UI and storage', async () => {
  const ui = view({ initial: 'unrelated text', interruptedFiles: [{ name: 'pending.txt', size: 12, mime: 'text/plain' }] });
  await control(await ui.ready(), '发送').props.onClick();
  const nodes = ui.render();
  assert.equal(editor(nodes).props.value, '');
  assert.ok(nodes.some(node => node.props?.role === 'status' && text(node).includes('pending.txt')));
  const restored = await view({ sharedDrafts: ui.local }).ready();
  assert.ok(restored.some(node => node.props?.role === 'status' && text(node).includes('pending.txt')));
});

test('explicit guidance sends only to the active turn and shares the queue admission lock', async () => {
  const calls = [];
  let accept;
  const ui = view({ stream: running, initial: '  revise it  ', onSend: () => { calls.push('queue'); return true; },
    onSteer: value => { calls.push(value); return new Promise(resolve => { accept = resolve; }); } });
  const nodes = await ui.ready();
  assert.ok(control(nodes, '立即指导'));
  const pending = control(nodes, '立即指导').props.onClick();
  await control(nodes, '加入队列').props.onClick();
  await control(nodes, '立即指导').props.onClick();
  assert.deepEqual(calls, ['revise it']);
  assert.equal(editor(ui.render()).props.value, '  revise it  ');
  accept(true);
  await pending;
  assert.equal(editor(ui.render()).props.value, '');
});

test('Enter queues even when guidance is available, and unavailable guidance stays hidden', async () => {
  const calls = [];
  const ui = view({ stream: running, initial: 'next', onSend: value => { calls.push(value); return true; },
    onSteer: () => { calls.push('steer'); return Promise.resolve(true); } });
  editor(await ui.ready()).props.onSubmit();
  await settle();
  assert.deepEqual(calls, ['next']);
  assert.ok(!control(await view({ stream: running }).ready(), '立即指导'));
  assert.ok(!control(await view({ onSteer: async () => true }).ready(), '立即指导'));
});

test('unconfirmed guidance and host errors preserve the draft and show feedback', async () => {
  for (const [onSteer, feedback] of [[async () => false, '草稿已保留'], [async () => { throw new Error('引擎尚未确认接收'); }, '引擎尚未确认接收']]) {
    const ui = view({ stream: running, initial: 'keep guidance', onSteer });
    const nodes = await ui.ready();
    assert.ok(control(nodes, '立即指导'));
    await control(nodes, '立即指导').props.onClick();
    assert.equal(editor(ui.render()).props.value, 'keep guidance');
    assert.ok(ui.nodes.some(node => node.props?.role === 'alert' && text(node).includes(feedback)));
    assert.equal(JSON.parse(ui.local.get('scope:easel_chat_draft:s1')).text, 'keep guidance');
  }
});

test('guidance cannot discard pending attachments', async () => {
  let steerCount = 0;
  const ui = view({ stream: running, initial: 'keep both', onSteer: async () => { steerCount++; return true; } });
  const nodes = await ui.ready();
  const button = control(nodes, '立即指导');
  assert.ok(button);
  nodes.find(node => node.props?.onDrop).props.onDrop({
    preventDefault() {}, dataTransfer: { files: [new File(['x'], 'pending.txt')] },
  });
  const withAttachment = control(ui.render(), '立即指导');
  assert.ok(!withAttachment || withAttachment.props.disabled);
  await button.props.onClick();
  assert.equal(steerCount, 0);
  const interrupted = view({ stream: running, initial: 'restore later', onSteer: async () => true,
    interruptedFiles: [{ name: 'previous.txt', size: 12, mime: 'text/plain' }] });
  assert.ok(!control(await interrupted.ready(), '立即指导'));
});

test('queue editor isolates the text edit and locks rows awaiting an admission receipt', async () => {
  const changes = [];
  const edits = [];
  const removals = [];
  let sends = 0;
  const ui = view({ onSend: () => { sends++; return true; }, queueProps: {
    queue: [
      { id: 'editable', text: '旧要求', created: 1, attachments: [{ name: 'reference.pdf' }] },
      { id: 'sending', text: '派发中', delivery: 'sending', created: 2 },
      { id: 'unconfirmed', text: '待核对', delivery: 'unconfirmed', created: 3 },
    ],
    onQueueEditChange: id => changes.push(id),
    onEditQueued: (id, draft) => { edits.push([id, draft]); return true; },
    onRemoveQueued: id => removals.push(id),
  } });
  let nodes = await ui.ready();
  const byLabel = label => nodes.find(node => node.props?.['aria-label'] === label);
  assert.equal(byLabel('移除第 2 条排队消息'), undefined);
  assert.equal(byLabel('编辑第 3 条排队消息'), undefined);
  assert.ok(nodes.some(node => node.props?.role === 'status' && text(node) === '等待服务端接收'));
  byLabel('编辑第 1 条排队消息').props.onClick();
  nodes = ui.render();
  let editor = byLabel('编辑第 1 条排队消息');
  assert.equal(editor.type, 'textarea');
  editor.props.onChange({ target: { value: '改为新要求' } });
  nodes = ui.render();
  editor = byLabel('编辑第 1 条排队消息');
  editor.props.onKeyDown({ key: 'Enter' });
  assert.equal(sends, 0);
  nodes.find(node => node.type === 'button' && text(node) === '保存').props.onClick();
  assert.deepEqual(edits, [['editable', '改为新要求']]);
  assert.deepEqual(changes, ['editable', null]);
  nodes = ui.render();
  assert.ok(nodes.some(node => node.type === 'small' && text(node) === 'reference.pdf'));
  byLabel('移除第 1 条排队消息').props.onClick();
  assert.deepEqual(removals, ['editable']);
});

test('restored files block sends until verified, and unavailable receipts must be removed', async () => {
  const sharedDrafts = new Map([['scope:easel_chat_draft:s1', JSON.stringify({
    text: 'restore draft',
    attachments: [{ key: 'gone', name: 'gone.txt', size: 1, mime: 'text/plain', status: 'ready',
      reference: { id: 'id', path: '_inbox/scope/batch/gone.txt', name: 'gone.txt' } }],
    interruptedFiles: [],
  })]]);
  let resolve;
  let sends = 0;
  const ui = view({ sharedDrafts, validate: () => new Promise(value => { resolve = value; }),
    onSend: () => { sends++; return true; } });
  let nodes = await ui.ready();
  assert.equal(control(nodes, '发送').props.disabled, true);
  await control(nodes, '发送').props.onClick();
  assert.equal(sends, 0);
  resolve({ ok: true, json: async () => ({ valid: [false] }) });
  nodes = await ui.ready();
  assert.equal(control(nodes, '发送').props.disabled, true);
  assert.ok(nodes.some(node => text(node) === '附件已不可用，请移除或重新选择文件。'));
  assert.ok(!nodes.some(node => node.props?.className === 'composer-file-retry'));
  nodes.find(node => node.props?.['aria-label'] === '移除 gone.txt').props.onClick();
  nodes = await ui.ready();
  assert.equal(control(nodes, '发送').props.disabled, false);
});

test('an uncertain earlier send can be reconciled without editing the current draft', async () => {
  let checks = 0;
  const ui = view({ initial: 'new text stays editable', queueProps: { admissionNotice: {
    message: '上一条发送未确认。', action: { label: '核对上一条发送', run: () => { checks++; } },
  } } });
  const nodes = await ui.ready();
  const action = nodes.find(node => node.type === 'button' && text(node) === '核对上一条发送');
  assert.ok(action);
  action.props.onClick();
  assert.equal(checks, 1);
  assert.equal(editor(ui.render()).props.value, 'new text stays editable');
});

test('validated inline references keep the draft and receipt offsets until admission resolves', async () => {
  const calls = [];
  let accept;
  const file = { key: 'file-key', name: 'brief.md', size: 1, mime: 'text/plain', status: 'ready',
    reference: { id: 'receipt', path: '_inbox/scope/batch/brief.md', name: 'brief.md' } };
  const sharedDrafts = new Map([['scope:easel_chat_draft:s1', JSON.stringify({
    text: '', attachments: [file], interruptedFiles: [],
  })]]);
  const ui = view({ sharedDrafts, validate: async () => ({ ok: true, json: async () => ({ valid: [true] }) }),
    onSend: (...values) => { calls.push(values); return new Promise(resolve => { accept = resolve; }); } });
  await ui.ready();
  const value = '  @brief.md  ';
  const references = [{ start: 2, end: 11, attachmentId: 'receipt' }];
  editor(ui.render()).props.onChange(value, references);
  const pending = control(ui.render(), '发送').props.onClick();
  assert.equal(calls[0][0], value);
  assert.equal(calls[0][1][0].id, 'receipt');
  assert.equal(JSON.stringify(calls[0][2]), JSON.stringify(references));
  assert.equal(editor(ui.render()).props.disabled, true);
  assert.equal(editor(ui.render()).props.value, value);
  accept(false);
  await pending;
  assert.equal(editor(ui.render()).props.disabled, false);
  assert.equal(JSON.stringify(editor(ui.render()).props.references), JSON.stringify(references));
  assert.equal(JSON.parse(sharedDrafts.get('scope:easel_chat_draft:s1')).references[0].attachmentId, 'receipt');
});

test('unknown inline IDs become ordinary text and cannot enter the submitted reference list', async () => {
  const calls = [];
  const ui = view({ onSend: (...values) => { calls.push(values); return false; } });
  editor(await ui.ready()).props.onChange('@foreign.txt', [{ start: 0, end: 12, attachmentId: 'unknown' }]);
  assert.equal(editor(ui.render()).props.references.length, 0);
  await control(ui.render(), '发送').props.onClick();
  assert.equal(calls[0][0], '@foreign.txt');
  assert.equal(calls[0].length, 2);
});

test('attachment insertion reports a full reference list without discarding its draft', async () => {
  const file = { key: 'file-key', name: 'brief.md', size: 1, mime: 'text/plain', status: 'ready',
    reference: { id: 'receipt', path: '_inbox/scope/batch/brief.md', name: 'brief.md' } };
  const textValue = '@brief.md '.repeat(100);
  const references = Array.from({ length: 100 }, (_, index) =>
    ({ start: index * 10, end: index * 10 + 9, attachmentId: 'receipt' }));
  const sharedDrafts = new Map([['scope:easel_chat_draft:s1', JSON.stringify({
    text: textValue, attachments: [file], interruptedFiles: [], references,
  })]]);
  const ui = view({ sharedDrafts, validate: async () => ({ ok: true, json: async () => ({ valid: [true] }) }) });
  const nodes = await ui.ready();
  nodes.find(node => node.props?.['aria-label'] === '插入正文 brief.md').props.onClick();
  const updated = ui.render();
  assert.equal(editor(updated).props.value, textValue);
  assert.equal(editor(updated).props.references.length, 100);
  assert.ok(updated.some(node => node.props?.role === 'alert' && /已达上限/.test(text(node))));
});


test('mixed session and file references stay separate through an unconfirmed send', async () => {
  const calls = [];
  const file = { key: 'file-key', name: 'brief.md', size: 1, mime: 'text/plain', status: 'ready',
    reference: { id: 'receipt', path: '_inbox/scope/batch/brief.md', name: 'brief.md' } };
  const sharedDrafts = new Map([['scope:easel_chat_draft:s1', JSON.stringify({
    text: '', attachments: [file], interruptedFiles: [],
  })]]);
  const ui = view({ sharedDrafts, validate: async () => ({ ok: true, json: async () => ({ valid: [true] }) }),
    onSend: (...values) => { calls.push(values); return false; } });
  const value = ' @旧计划 然后 @brief.md ';
  const references = [{ start: 9, end: 18, attachmentId: 'receipt' }];
  const sessionReferences = [{ start: 1, end: 5, sessionId: 'source-session', snapshotHash: 'b'.repeat(64), label: '旧计划' }];
  editor(await ui.ready()).props.onChange(value, references, sessionReferences);
  await control(ui.render(), '发送').props.onClick();
  assert.equal(calls[0][0], value);
  assert.equal(calls[0][1][0].id, 'receipt');
  assert.equal(JSON.stringify(calls[0][2]), JSON.stringify(references));
  assert.equal(JSON.stringify(calls[0][3]), JSON.stringify(sessionReferences));
  assert.equal(JSON.stringify(editor(ui.render()).props.sessionReferences), JSON.stringify(sessionReferences));
  assert.equal(JSON.parse(sharedDrafts.get('scope:easel_chat_draft:s1')).sessionReferences[0].snapshotHash, 'b'.repeat(64));
});

test('only a selected command reaches admission as an explicit sidecar', async () => {
  const calls = [];
  const ui = view({ onSend: (...values) => { calls.push(values); return false; } });
  const value = '/文案 写一条产品介绍';
  editor(await ui.ready()).props.onChange(value, []);
  await control(ui.render(), '发送').props.onClick();
  assert.equal(calls[0].length, 2);
  const command = { id: 'draft', token: '/文案' };
  editor(ui.render()).props.onChange(value, [], [], command);
  assert.equal(calls.length, 1, 'selecting does not send');
  await control(ui.render(), '发送').props.onClick();
  assert.equal(calls[1][0], value);
  assert.equal(JSON.stringify(calls[1][4]), JSON.stringify(command));
  assert.equal(JSON.stringify(editor(ui.render()).props.command), JSON.stringify(command));
});


test('adopting a scoped receipt makes its preview and exact file reference available without sending', async () => {
  const calls = [];
  const ui = view({ onSend: (...values) => { calls.push(values); return false; } });
  let input = editor(await ui.ready());
  input.props.onChange('@read', []);
  input = editor(ui.render());
  input.props.onFileSelectionPending(true);
  const pending = control(ui.render(), '发送');
  assert.equal(pending.props.disabled, true);
  await pending.props.onClick();
  assert.equal(calls.length, 0);
  const file = { id: 'adopted', name: 'ready.png', path: '_inbox/scope/batch/ready.png' };
  assert.equal(input.props.onAdoptFileReference(file), true);
  assert.equal(input.props.onAdoptFileReference(file), true, 'same receipt does not duplicate tray item');
  input.props.onChange('@ready.png ', [{ start: 0, end: 10, attachmentId: 'adopted' }]);
  input.props.onFileSelectionPending(false);
  const ready = ui.render();
  assert.equal(calls.length, 0);
  assert.equal(ready.filter(node => node.props?.['aria-label'] === '预览 ready.png').length, 1);
  assert.equal(control(ready, '发送').props.disabled, false);
  await control(ready, '发送').props.onClick();
  assert.equal(calls[0][1][0].id, 'adopted');
  assert.equal(calls[0][2][0].attachmentId, 'adopted');
});


test('quoting a settled answer inserts into the current draft without sending', async () => {
  const sends = [], quotes = [];
  const ui = view({ onSend: (...args) => { sends.push(args); return true; }, queueProps: {
    session: { id: 's1', title: 'Draft', created: 1, messages: [
      { role: 'user', content: '原提问' }, { role: 'assistant', content: '用于引用的回答' },
    ] },
  } });
  const nodes = await ui.ready();
  const editor = nodes.find(node => node.type === 'structured-editor');
  editor.props.ref.current = { insertAnswerQuote: text => { quotes.push(text); return true; } };
  const messages = nodes.filter(node => node.type === 'message-bubble');
  assert.equal(messages[0].props.onQuoteAnswer, undefined);
  assert.equal(messages[1].props.onQuoteAnswer('引用的文字'), true);
  assert.deepEqual(quotes, ['引用的文字']);
  assert.deepEqual(sends, []);
  editor.props.onFileSelectionPending(true);
  assert.equal(messages[1].props.onQuoteAnswer('插入被阻止'), false);
  assert.equal(quotes.length, 1);
});

test('work details preference controls persist and defer folding until the selected point', async () => {
  const drafts = new Map();
  const queueProps = { session: { id: 's1', title: 'Work details', created: 1, messages: [
    { role: 'user', content: 'first' }, { role: 'assistant', content: 'previous result' },
    { role: 'user', content: 'second' }, { role: 'assistant', content: 'latest result' },
  ] } };
  const ui = view({ queueProps, sharedDrafts: drafts });
  const select = (nodes, label) => nodes.find(node => node.type === 'select' && node.props['aria-label'] === label);
  let nodes = await ui.ready();
  assert.equal(select(nodes, '工作细节').props.value, 'compact');
  const assistantRows = nodes => nodes.filter(node => node.type === 'message-bubble' && node.props.message.role === 'assistant');
  assert.deepEqual(assistantRows(nodes).map(node => node.props.foldWorkDetails), [true, true]);
  select(nodes, '简洁模式自动折叠').props.onChange({ target: { value: 'next-input' } });
  nodes = ui.render();
  assert.deepEqual(assistantRows(nodes).map(node => node.props.foldWorkDetails), [true, false]);
  select(nodes, '工作细节').props.onChange({ target: { value: 'detailed' } });
  nodes = await view({ queueProps, sharedDrafts: drafts }).ready();
  assert.equal(select(nodes, '工作细节').props.value, 'detailed');
  assert.equal(select(nodes, '简洁模式自动折叠').props.value, 'next-input');
  assert.equal(assistantRows(nodes)[1].props.detailMode, 'detailed');
});


test('the composer shows the draft without a plan review entry point', async () => {
  const ui = view({ initial: 'ordinary draft', queueProps: {
    onExecutePlan: () => assert.fail('the composer must not dispatch a plan'),
  } });
  const nodes = await ui.ready();
  assert.ok(!nodes.some(node => node.type === 'button' && text(node) === '计划审阅'));
  assert.equal(nodes.find(node => node.type === 'structured-editor').props.value, 'ordinary draft');
});

test('background execution uses the existing composer and keeps the draft until the task is accepted', async () => {
  let accepted = false;
  const tasks = [];
  const ui = view({
    onSend: () => assert.fail('background execution entered the normal chat path'),
    queueProps: { onSendSuperagent: async text => { tasks.push(text); return accepted; } },
  });
  let nodes = await ui.ready();
  nodes.find(node => node.type === 'structured-editor').props.onChange('准备三平台草稿', []);
  nodes = ui.render();
  nodes.find(node => node.props['data-superagent-composer-toggle']).props.onClick();
  const send = () => ui.render().find(node => node.props['data-superagent-composer-send']).props.onClick();
  await send();
  assert.equal(ui.render().find(node => node.type === 'structured-editor').props.value, '准备三平台草稿');
  accepted = true;
  await send();
  assert.deepEqual(tasks, ['准备三平台草稿', '准备三平台草稿']);
  assert.equal(ui.render().find(node => node.type === 'structured-editor').props.value, '');
});

test('background execution preserves existing attachments and blocks both click and keyboard submission', async () => {
  const ui = view({
    onSend: () => assert.fail('ordinary send received the background task'),
    queueProps: { onSendSuperagent: async () => assert.fail('background task silently dropped an attachment') },
  });
  let input = editor(await ui.ready());
  input.props.onChange('检查现有文件', []);
  assert.equal(input.props.onAdoptFileReference({ id: 'file', name: 'notes.txt', path: '_inbox/scope/batch/notes.txt' }), true);
  ui.render().find(node => node.props['data-superagent-composer-toggle']).props.onClick();
  const nodes = ui.render();
  assert.equal(nodes.find(node => node.props['data-superagent-composer-send']).props.disabled, true);
  await editor(nodes).props.onSubmit();
  assert.ok(ui.render().some(node => node.props.role === 'alert' && text(node).includes('后台执行只接收文字')));
  assert.equal(editor(ui.render()).props.value, '检查现有文件');
  assert.ok(ui.render().some(node => text(node) === 'notes.txt'));
});

test('the composer omits command, conversation reference and keyboard shortcut prompts', async () => {
  const ui = view({ queueProps: { sessionReferenceCandidates: [] } });
  const nodes = await ui.ready();
  const editor = nodes.find(node => node.type === 'structured-editor');
  assert.doesNotMatch(editor.props.placeholder, /Enter/);
  for (const node of nodes) {
    assert.doesNotMatch(text(node), /查看命令|引用历史对话|Enter 发送/);
  }
});
