import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const rangeAt = (startContainer, startOffset, endContainer, endOffset) => ({
  startContainer, startOffset, endContainer, endOffset,
  cloneRange() { return { ...this }; },
  getBoundingClientRect() { return { left: 20, top: 60, right: 80, bottom: 78, width: 60, height: 18 }; },
});
const selectionAt = range => ({ rangeCount: 1, isCollapsed: false, getRangeAt: () => range, removeAllRanges() {} });

test('only one answer range projects visible selected text, with block boundaries and chrome removed', () => {
  const { window, document } = parseHTML('<html><body><div data-text-interaction-source><p>前半段</p><button>不要引用</button><p>后半段</p></div><p>另一条消息</p></body></html>');
  window.getComputedStyle = () => ({ whiteSpace: 'normal' });
  const { readTextSelection, selectedText } = componentModule('components/answerTextSelection.ts', { globals: {
    Node: window.Node, Element: window.Element, HTMLElement: window.HTMLElement,
  } });
  const root = document.querySelector('[data-text-interaction-source]');
  const [first, second] = root.querySelectorAll('p');
  const selection = selectionAt(rangeAt(first.firstChild, 1, second.firstChild, 2));
  assert.equal(selectedText(readTextSelection(root, selection)), '半段\n后半');
  const foreign = document.body.lastElementChild;
  assert.equal(readTextSelection(root, selectionAt(rangeAt(first.firstChild, 0, foreign.firstChild, 2))), null);
  const chrome = root.querySelector('button').firstChild;
  const chromeRange = readTextSelection(root, selectionAt(rangeAt(chrome, 0, chrome, 2)));
  assert.equal(chromeRange === null ? null : selectedText(chromeRange), null);
});

test('a visible action keeps the range on pointerdown and clears only after explicit accepted quote', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.getComputedStyle = () => ({ whiteSpace: 'normal' });
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  let selected = null;
  let cleared = 0;
  document.getSelection = () => selected;
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const QuoteSelection = componentModule('components/AnswerQuoteSelection.tsx', { globals: {
      window, document, Node: window.Node, Element: window.Element, HTMLElement: window.HTMLElement,
    } }).default;
    let accepted = false;
    const quotes = [];
    await React.act(() => root.render(React.createElement(QuoteSelection, {
      html: '<p>可引用内容</p>', onQuoteAnswer: text => { quotes.push(text); return accepted; },
    })));
    const answer = document.querySelector('[data-assistant-answer]');
    const text = answer.querySelector('p').firstChild;
    selected = { ...selectionAt(rangeAt(text, 1, text, 4)), removeAllRanges() { cleared++; selected = null; } };
    await React.act(() => document.dispatchEvent(new window.Event('selectionchange')));
    const action = document.querySelector('[data-answer-quote-action]');
    assert.ok(action);
    const pointer = new window.Event('pointerdown', { bubbles: true, cancelable: true });
    await React.act(() => action.dispatchEvent(pointer));
    assert.equal(pointer.defaultPrevented, true);
    await React.act(() => action.click());
    assert.deepEqual(quotes, ['引用内']);
    assert.equal(cleared, 0);
    assert.ok(document.querySelector('[data-answer-quote-action]'));
    accepted = true;
    await React.act(() => action.click());
    assert.deepEqual(quotes, ['引用内', '引用内']);
    assert.equal(cleared, 1);
    assert.equal(document.querySelector('[data-answer-quote-action]'), null);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});

test('saved canonical quotes show literal text while preserving independently validated attachment spans', () => {
  const Bubble = componentModule('components/MessageBubble.tsx').default;
  const { renderToStaticMarkup } = require('react-dom/server');
  const file = { id: 'receipt', path: '_inbox/scope/batch/brief.md', name: 'brief.md' };
  const quoted = '>   缩进\n> <img src=x>';
  const content = quoted + '\n\n@brief.md';
  const show = (body, start) => parseHTML(renderToStaticMarkup(React.createElement(Bubble, {
    message: { role: 'user', content: body, attachments: [file],
      references: [{ start, end: start + 9, attachmentId: file.id }] },
  }))).document;
  const separate = show(content, quoted.length + 2);
  assert.equal(separate.querySelector('[data-chat-quote]').textContent, '  缩进\n<img src=x>');
  assert.equal(separate.querySelector('[data-chat-quote] img'), null);
  assert.ok(separate.querySelector('[data-chat-reference=receipt]'));
  const overlap = show('> @brief.md', 2);
  assert.equal(overlap.querySelector('[data-chat-quote]'), null);
  assert.ok(overlap.querySelector('[data-chat-reference=receipt]'));
});
