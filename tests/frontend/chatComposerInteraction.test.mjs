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

function view({ onSend = () => true, stream, initial = '' } = {}) {
  const local = new Map(initial ? [['scope:easel_chat_draft:s1', JSON.stringify({ text: initial, attachments: [], interruptedFiles: [] })]] : []);
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
  const globals = { File, URL, AbortController, Date, crypto, localStorage, sessionStorage: localStorage, console,
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
    onSend, onStop() {}, onResend() {} };
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
