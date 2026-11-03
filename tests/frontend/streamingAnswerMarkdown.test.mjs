import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML, DOMParser } = require('linkedom');

async function fixture(run) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const create = document.createElement.bind(document);
  document.createElement = (name, ...args) => {
    const element = create(name, ...args);
    if (name.toLowerCase() === 'template') Object.defineProperty(element, 'content', { value: element });
    return element;
  };
  class BrowserParser extends DOMParser {
    parseFromString(html, type) { return super.parseFromString(/^(?:<!doctype|<html)/i.test(html.trim())
      ? html : `<html><head></head><body>${html}</body></html>`, type); }
  }
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Bubble = componentModule('components/MessageBubble.tsx', { externals: { dompurify: require('dompurify')(window) },
      globals: { window, document, DOMParser: BrowserParser, Node: window.Node, Element: window.Element,
        HTMLElement: window.HTMLElement, TextEncoder, TextDecoder } }).default;
    const update = (content, isStreaming, events = []) => React.act(async () => root.render(React.createElement(Bubble, {
      message: { role: 'assistant', content, events }, isStreaming, events,
    })));
    await run({ update, document });
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
}

test('growing tails preserve finished paragraph text nodes, and settling keeps unchanged prefix', async () => {
  await fixture(async ({ update, document }) => {
    const prefix = '第一段 **稳定**\n\n第二段\n\n第三段\n\n';
    await update(prefix + '尾段', true);
    const answer = document.querySelector('[data-assistant-answer]');
    const firstBlock = answer.querySelector('[data-answer-markdown-block]');
    assert.ok(firstBlock);
    const text = firstBlock.querySelector('p').firstChild;
    await update(prefix + '尾段新增\n\n```json\n{"ok":true}\n```', true);
    assert.equal(answer.querySelector('[data-answer-markdown-block]'), firstBlock);
    assert.equal(firstBlock.querySelector('p').firstChild, text);
    assert.equal(answer.querySelector('[data-code-copy-button]'), null);
    await update(prefix + '尾段新增\n\n```json\n{"ok":true}\n```', false);
    assert.equal(document.querySelector('[data-assistant-answer]'), answer);
    assert.equal(firstBlock.querySelector('p').firstChild, text);
    assert.equal(answer.querySelectorAll('[data-code-copy-button]').length, 1);
  });
});

test('midstream snapshot policy changes the affected link without recreating an unrelated block', async () => {
  await fixture(async ({ update, document }) => {
    const answerText = '稳定开头\n\n[交付物](/api/media/reports/cover.png)\n\n第三段\n\n末尾';
    await update(answerText, true);
    const answer = document.querySelector('[data-assistant-answer]');
    const first = answer.querySelector('[data-answer-markdown-block]');
    assert.ok(first);
    const id = 'a'.repeat(32);
    const artifact = { artifactId: id, projectId: 'default', sessionId: 'session', turnId: 'turn', path: 'reports/cover.png',
      name: 'cover.png', mime: 'image/png', size: 10, status: 'available', createdAt: 1, storageMode: 'snapshot',
      revision: 1, sha256: 'b'.repeat(64), bundleSha256: null, url: `/api/chat/artifacts/${id}/content` };
    await update(answerText, true, [{ id: 'artifact', kind: 'artifact', callId: 'present', artifacts: [artifact] }]);
    assert.equal(answer.querySelector('[data-answer-markdown-block]'), first);
    assert.equal(answer.querySelector('a').getAttribute('href'), artifact.url);
    await update(answerText, false, [{ id: 'artifact', kind: 'artifact', callId: 'present', artifacts: [artifact] }]);
    assert.equal(answer.querySelector('a').getAttribute('href'), artifact.url);
  });
});


test('settled path linkification discovers the legacy output while keeping an unchanged prefix', async () => {
  await fixture(async ({ update, document }) => {
    const source = '稳定开头\n\n其他段落\n\n交付物：outputs/reports/result.pdf';
    await update(source, true);
    const answer = document.querySelector('[data-assistant-answer]');
    const prefix = answer.querySelector('[data-answer-markdown-block]');
    assert.equal(answer.querySelector('a'), null);
    await update(source, false);
    assert.equal(answer.querySelector('[data-answer-markdown-block]'), prefix);
    assert.equal(answer.querySelector('a').getAttribute('href'), '/api/media/reports/result.pdf');
    assert.ok(document.querySelector('.artifact-gallery'));
  });
});
