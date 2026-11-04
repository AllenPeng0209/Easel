import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const root = new URL('../../web/frontend/src/', import.meta.url);
const tick = () => new Promise(resolve => setImmediate(resolve));
const storage = () => {
  const data = new Map();
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
};
function load(relative, extras = {}) {
  const file = new URL(relative, root);
  if (!existsSync(file)) return {};
  const exports = {};
  const code = ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  runInNewContext(code, { exports, AbortController, File, URL, Date, crypto, console, setTimeout, clearTimeout,
    require: name => name === 'react' ? require('react') : name.endsWith('projectScope')
      ? { projectStorageKey: key => `user:u1:ip:a:${key}` }
      : name === './chatReferences' ? load('lib/chatReferences.ts')
      : name === './chatCommands' ? load('lib/chatCommands.ts') : require(name), ...extras });
  return exports;
}

test('concurrent upload batches cannot unlock sending while another upload is pending', async () => {
  const { AttachmentUploadQueue } = load('lib/uploadQueue.ts');
  assert.equal(typeof AttachmentUploadQueue, 'function', 'An attachment queue must own concurrent upload admission');
  const deferred = [];
  const queue = new AttachmentUploadQueue((file, signal) => new Promise((resolve, reject) => deferred.push({ file, signal, resolve, reject })), () => {});
  queue.add([new File(['a'], 'first.png', { type: 'image/png' })]);
  queue.add([new File(['b'], 'second.txt')]);
  assert.equal(deferred.length, 2);
  deferred[0].resolve({ id: 'first', name: 'first.png', path: 'outputs/first.png' });
  await tick();
  assert.equal(queue.busy, true);
  deferred[1].resolve({ id: 'second', name: 'second.txt', path: 'outputs/second.txt' });
  await tick();
  assert.equal(queue.busy, false);
  assert.deepEqual(Array.from(queue.ready, value => value.name), ['first.png', 'second.txt']);
  queue.dispose();
});

test('uploads preserve selection order even when the second file completes first', async () => {
  const { AttachmentUploadQueue } = load('lib/uploadQueue.ts');
  assert.equal(typeof AttachmentUploadQueue, 'function');
  const deferred = [];
  const queue = new AttachmentUploadQueue(file => new Promise(resolve => deferred.push({ file, resolve })), () => {});
  queue.add([new File(['a'], 'large.mov'), new File(['b'], 'small.txt')]);
  deferred[1].resolve({ id: 'small', name: 'small.txt', path: 'outputs/small.txt' });
  await tick();
  deferred[0].resolve({ id: 'large', name: 'large.mov', path: 'outputs/large.mov' });
  await tick();
  assert.deepEqual(Array.from(queue.ready, value => value.name), ['large.mov', 'small.txt']);
  queue.dispose();
});

test('removing an in-flight upload aborts transport and a late completion cannot restore it', async () => {
  const { AttachmentUploadQueue } = load('lib/uploadQueue.ts');
  assert.equal(typeof AttachmentUploadQueue, 'function');
  let pending;
  const queue = new AttachmentUploadQueue((_file, signal) => new Promise(resolve => { pending = { signal, resolve }; }), () => {});
  queue.add([new File(['a'], 'cancel.txt')]);
  queue.remove(queue.snapshot[0].key);
  assert.equal(pending.signal.aborted, true);
  pending.resolve({ id: 'late', name: 'cancel.txt', path: 'outputs/cancel.txt' });
  await tick();
  assert.equal(queue.snapshot.length, 0);
  assert.equal(queue.ready.length, 0);
  queue.dispose();
});

test('failed uploads stay visible and only that file is retried', async () => {
  const { AttachmentUploadQueue } = load('lib/uploadQueue.ts');
  assert.equal(typeof AttachmentUploadQueue, 'function');
  const attempts = [];
  const queue = new AttachmentUploadQueue(async file => {
    attempts.push(file.name);
    if (attempts.length === 1) throw new Error('network failed');
    return { id: 'retry', name: file.name, path: `outputs/${file.name}` };
  }, () => {});
  queue.add([new File(['a'], 'retry.txt')]);
  await tick();
  assert.equal(queue.snapshot[0].status, 'failed');
  assert.equal(queue.snapshot[0].error, 'network failed');
  queue.retry(queue.snapshot[0].key);
  await tick();
  assert.equal(queue.snapshot[0].status, 'ready');
  assert.deepEqual(attempts, ['retry.txt', 'retry.txt']);
  queue.dispose();
});

test('chat drafts survive remounts without storing raw File bytes and stay session scoped', () => {
  const localStorage = storage();
  const draft = load('lib/chatDrafts.ts', { localStorage });
  assert.equal(typeof draft.saveChatDraft, 'function', 'Chat draft persistence must exist');
  draft.saveChatDraft('one', { text: 'unfinished', attachments: [{ key: 'a', name: 'image.png', size: 1, mime: 'image/png', status: 'ready', reference: { id: 'r', name: 'image.png', path: 'outputs/image.png' } }], interruptedFiles: [] });
  const restored = load('lib/chatDrafts.ts', { localStorage }).loadChatDraft('one');
  assert.equal(restored.text, 'unfinished');
  assert.equal(restored.attachments[0].reference.id, 'r');
  assert.equal(draft.loadChatDraft('two').text, '');
  draft.clearChatDraft('one');
  assert.equal(draft.loadChatDraft('one').text, '');
});

test('refreshing an unfinished upload retains a reselect notice without pretending it is ready', () => {
  const localStorage = storage();
  const draft = load('lib/chatDrafts.ts', { localStorage });
  assert.equal(typeof draft.saveChatDraft, 'function');
  draft.saveChatDraft('one', { text: 'prompt', attachments: [{ key: 'a', name: 'pending.png', size: 22, mime: 'image/png', status: 'uploading' }], interruptedFiles: [] });
  const restored = draft.loadChatDraft('one');
  assert.equal(restored.attachments.length, 0);
  assert.equal(restored.interruptedFiles[0].name, 'pending.png');
  assert.equal(restored.text, 'prompt');
});

test('reader movement releases follow while content growth alone retains it', () => {
  const { ScrollFollow } = load('vendor/deepseek-harness/composer/use-scroll-follow.ts');
  assert.equal(typeof ScrollFollow, 'function', 'The upstream scroll-follow controller must be migrated');
  const follow = new ScrollFollow(true, 25);
  follow.sample({ top: 200, floor: 400, height: 300 }, false);
  assert.equal(follow.active, true);
  follow.sample({ top: 180, floor: 400, height: 300 }, true);
  assert.equal(follow.active, false);
  follow.sample({ top: 400, floor: 400, height: 300 }, true);
  assert.equal(follow.active, true);
});

test('a back-to-bottom request can restore following after reading earlier messages', () => {
  const { ScrollFollow } = load('vendor/deepseek-harness/composer/use-scroll-follow.ts');
  assert.equal(typeof ScrollFollow, 'function');
  const follow = new ScrollFollow(false, 25);
  const element = { scrollTop: 80, scrollTo({ top }) { this.scrollTop = top; } };
  follow.toBottom(element, { top: 80, floor: 600, height: 300 }, 'instant');
  assert.equal(element.scrollTop, 600);
  assert.equal(follow.active, true);
});

test('the migrated image preview keeps Tab inside, closes on Escape and restores the opener', () => {
  const effects = [], listeners = new Map();
  class Element { focused = 0; focus() { this.focused += 1; } closest() { return null; } }
  const opener = new Element(), close = new Element();
  let closed = 0;
  const jsx = (type, props) => ({ type, props });
  const { ImageLightbox } = load('vendor/deepseek-harness/composer/ImageLightbox.tsx', {
    HTMLElement: Element, document: { activeElement: opener, body: {} },
    window: { addEventListener: (name, listener) => listeners.set(name, listener), removeEventListener: name => listeners.delete(name) },
    require: name => name === 'react' ? { useRef: () => ({ current: null }), useEffect: effect => effects.push(effect) }
      : name === 'react-dom' ? { createPortal: node => node }
      : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : { default: {} },
  });
  assert.equal(typeof ImageLightbox, 'function', 'The image preview should use the upstream accessible lightbox');
  const tree = ImageLightbox({ src: 'blob:image', alt: 'image', labels: { dialog: 'preview', close: 'close' }, onClose: () => { closed += 1; } });
  const button = tree.props.children.props.children.find(node => node.type === 'button');
  button.props.ref.current = close;
  const cleanup = effects[0]();
  assert.equal(close.focused, 1);
  let prevented = false;
  listeners.get('keydown')({ key: 'Tab', preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(close.focused, 2);
  listeners.get('keydown')({ key: 'Escape', stopPropagation() {} });
  assert.equal(closed, 1);
  cleanup();
  assert.equal(opener.focused, 1);
  assert.equal(listeners.size, 0);
});
