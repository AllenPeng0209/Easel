import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const initial = { content: '当前回复', thinking: '', activity: '', questions: [], events: [], lifecycle: 'running' };
const messages = [{ role: 'user', content: '请开始' }];

async function chatDOM(run) {
  const { window } = require('linkedom').parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  window.getComputedStyle = element => element.style;
  const metrics = new WeakMap();
  const metricNames = ['scrollHeight', 'clientHeight', 'scrollTop'];
  const oldMetrics = Object.fromEntries(metricNames.map(key => [key, Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, key)]));
  Object.defineProperties(window.HTMLElement.prototype, {
    scrollHeight: { configurable: true, get() { return metrics.get(this)?.height ?? (this.classList.contains('chat-messages') ? 1000 : 0); } },
    clientHeight: { configurable: true, get() { return metrics.get(this)?.viewport ?? (this.classList.contains('chat-messages') ? 200 : 0); } },
    scrollTop: { configurable: true, get() { return metrics.get(this)?.top ?? 0; }, set(top) { metrics.set(this, { ...metrics.get(this), top }); } },
  });
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true, writable: true },
    document: { value: window.document, configurable: true, writable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true, writable: true } });
  const root = require('react-dom/client').createRoot(window.document.getElementById('root'));
  try {
    // Linkedom has no native caret or selection support. Keep the real editor
    // model while detaching its DOM runtime for these reading/preview tests.
    const lexical = require('lexical');
    const createEditor = options => {
      const editor = lexical.createEditor(options);
      editor.setRootElement = () => {};
      return editor;
    };
    const ChatPage = componentModule('components/ChatPage.tsx', { externals: { lexical: { ...lexical, createEditor } }, globals: { window, document: window.document, DOMParser: window.DOMParser,
      navigator: { platform: 'MacIntel', userAgent: 'test' }, crypto, queueMicrotask,
      fetch: async url => new Response(String(url).endsWith('/content') ? '固定版本内容' : JSON.stringify({ max_mb: 50 })), Response, TextDecoder,
    } }).default;
    const render = (stream, history = messages) => React.act(async () => root.render(React.createElement(ChatPage, {
      apiDirect: true, session: { id: 'reading-one', title: '测试会话', created: 1, messages: history },
      stream, onSend: () => true, onStop() {}, onResend() {},
    })));
    await render(initial);
    const scroller = window.document.querySelector('.chat-messages');
    const jump = () => window.document.querySelector('.chat-back-to-bottom');
    const wheel = (target, deltaY) => React.act(async () => {
      const event = new window.Event('wheel', { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'deltaY', { value: deltaY });
      target.dispatchEvent(event);
    });
    const scroll = top => React.act(async () => {
      scroller.scrollTop = top;
      scroller.dispatchEvent(new window.Event('scroll'));
    });
    await run({ window, render, scroller, jump, wheel, scroll, metrics });
  } finally {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(oldMetrics)) {
      if (descriptor) Object.defineProperty(window.HTMLElement.prototype, key, descriptor);
      else delete window.HTMLElement.prototype[key];
    }
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
}

test('away reader gets no unread badge from heartbeat, status, or an unchanged final reply', async () => {
  await chatDOM(async ({ render, jump, scroll }) => {
    await scroll(150);
    assert.ok(jump());
    await render({ ...initial, stillWorking: '仍在执行', activity: '等待中', lifecycle: 'waiting_input' });
    assert.doesNotMatch(jump().textContent, /有新内容/);
    await render(undefined, [...messages, { role: 'assistant', content: initial.content, status: 'completed' }]);
    assert.doesNotMatch(jump().textContent, /有新内容/);
  });
});

test('new text, tool, question and answer updates each mark content unread while away', async () => {
  await chatDOM(async ({ render, jump, scroll, scroller }) => {
    let state = initial;
    const question = { id: 'ask-one', questions: [{ questionId: 'q1', question: '选择风格', options: [{ label: '简洁' }] }] };
    for (const change of [{ content: '新增正文' },
      { events: [{ id: 'tool-1', callId: 'call-1', kind: 'tool', name: 'read', status: 'completed', output: '结果' }] },
      { questions: [question] }, { questions: [{ ...question, status: 'answered', answers: { q1: ['简洁'] } }] }]) {
      await scroll(150);
      state = { ...state, ...change };
      await render(state);
      assert.match(jump().textContent, /有新内容/);
      await React.act(async () => jump().click());
      assert.equal(scroller.scrollTop, 800);
      assert.ok(!jump());
    }
    await scroll(150);
    await render({ ...state, events: state.events.map(event => ({ ...event, id: 'replay', timestamp: 300 })) });
    assert.doesNotMatch(jump().textContent, /有新内容/);
  });
});

test('nested wheel keeps outer follow while consumed, then releases at a chaining edge', async () => {
  await chatDOM(async ({ window, render, scroller, wheel, jump, metrics }) => {
    const inner = window.document.createElement('div');
    const child = window.document.createElement('code');
    inner.style.overflowY = 'auto';
    inner.append(child); scroller.append(inner);
    metrics.set(inner, { top: 40, height: 200, viewport: 80 });
    await wheel(child, -20);
    assert.ok(!jump());
    metrics.set(scroller, { top: 800, height: 1200, viewport: 200 });
    await render({ ...initial, content: '新内容向下延伸' });
    assert.equal(scroller.scrollTop, 1000);
    inner.scrollTop = 0;
    inner.style.overscrollBehaviorY = 'contain';
    await wheel(child, -20);
    assert.ok(!jump());
    inner.style.overscrollBehaviorY = 'auto';
    await wheel(child, -20);
    assert.ok(jump());
  });
});

test('opening a delivery keeps the original chat and returns focus to its card', async () => {
  await chatDOM(async ({ window, render, scroll }) => {
    const id = 'a'.repeat(32);
    const file = { artifactId: id, projectId: 'default', sessionId: 'reading-one', turnId: 'turn',
      path: 'report.txt', name: 'report.txt', mime: 'text/plain', size: 18, createdAt: 1,
      storageMode: 'snapshot', revision: 2, sha256: 'b'.repeat(64), status: 'available',
      url: `/api/chat/artifacts/${id}/content` };
    await render(undefined, [...messages, { role: 'assistant', content: '', events: [
      { id: 'delivery', callId: 'present', kind: 'artifact', artifacts: [file] },
    ] }]);
    await scroll(150);
    const document = window.document;
    const composer = document.querySelector('.chat-input');
    const chat = document.querySelector('.chat-messages');
    const trigger = document.querySelector('[aria-label="查看文件：report.txt"]');
    let restored = false;
    trigger.focus = options => { restored = options?.preventScroll === true; };
    await React.act(async () => trigger.click());
    const pane = document.querySelector('.deliverable-preview-pane');
    assert.ok(pane);
    assert.match(pane.textContent, /固定版本内容/);
    assert.equal(document.querySelector('.chat-input'), composer);
    assert.equal(document.querySelector('.chat-messages'), chat);
    assert.equal(chat.scrollTop, 150);
    await React.act(async () => pane.querySelector('[aria-label="关闭预览"]').click());
    assert.equal(document.querySelector('.deliverable-preview-pane'), null);
    assert.equal(document.querySelector('.chat-input'), composer);
    assert.equal(chat.scrollTop, 150);
    assert.equal(restored, true);
  });
});
