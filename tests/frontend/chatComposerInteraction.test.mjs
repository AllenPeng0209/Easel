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

function view({ onSend = () => true, onSteer, stream, initial = '', interruptedFiles = [], queueProps = {} } = {}) {
  const local = new Map(initial ? [['scope:easel_chat_draft:s1', JSON.stringify({ text: initial, attachments: [], interruptedFiles })]] : []);
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
        projectFetch: async () => ({ json: async () => ({ max_mb: 50 }) }) };
      if (name.endsWith('/api')) return { uploadFiles: async () => [], adoptOversize: async () => [] };
      if (name.includes('MessageBubble') || name.includes('QuestionCards') || name.includes('BrushEntry')) return { default: () => null };
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
    async ready() { render(); await settle(); return render(); } };
}

test('a rejected submission leaves the editable text and persisted draft intact', async () => {
  const ui = view({ onSend: () => false }); let nodes = await ui.ready();
  nodes.find(node => node.type === 'textarea').props.onChange({ target: { value: 'keep this draft' } });
  nodes = ui.render();
  await nodes.find(node => node.type === 'button' && node.props.title === '发送').props.onClick();
  nodes = await ui.ready();
  assert.equal(nodes.find(node => node.type === 'textarea').props.value, 'keep this draft');
});

test('an accepted submission clears its draft only after the admission promise resolves', async () => {
  let accept; const ui = view({ onSend: () => new Promise(resolve => { accept = resolve; }) });
  let nodes = await ui.ready(); nodes.find(node => node.type === 'textarea').props.onChange({ target: { value: 'pending admission' } });
  nodes = ui.render(); const result = nodes.find(node => node.type === 'button' && node.props.title === '发送').props.onClick();
  assert.equal(ui.render().find(node => node.type === 'textarea').props.value, 'pending admission');
  accept(true); await result; await settle();
  assert.equal(ui.render().find(node => node.type === 'textarea').props.value, '');
});

test('a running conversation can submit the next message to the queue without stopping', async () => {
  const sent = []; const ui = view({ stream: { content: 'working', thinking: '', activity: '', questions: [] },
    onSend: (...args) => { sent.push(args); return true; } });
  let nodes = await ui.ready(); nodes.find(node => node.type === 'textarea').props.onChange({ target: { value: 'next task' } });
  nodes = ui.render(); const queue = nodes.find(node => node.type === 'button' && node.props.title === '加入队列');
  assert.ok(queue, 'Streaming must keep a distinct next-message submission control');
  await queue.props.onClick();
  assert.equal(sent[0][0], 'next task');
});

test('opening the same conversation restores its unsent draft', async () => {
  const ui = view({ initial: 'saved draft' });
  const nodes = await ui.ready();
  assert.equal(nodes.find(node => node.type === 'textarea').props.value, 'saved draft');
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
  nodes.find(node => node.type === 'textarea').props.onChange({ target: { value: 'pending message' } });
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
const editor = nodes => nodes.find(node => node.type === 'textarea');

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
  editor(await ui.ready()).props.onKeyDown({ key: 'Enter', nativeEvent: {}, preventDefault() {} });
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
