import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const body = '<pre><code class="language-python">def hello():\n    return "&lt;tag&gt;"\n</code></pre>' +
  '<pre><code class="language-imaginary">unknown plain\n</code></pre>' +
  '<pre><code>unlabelled plain\n</code></pre>';

async function eventually(check) {
  for (let attempt = 0; attempt < 25; attempt += 1) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.ok(check(), 'expected asynchronous highlighting to finish');
}

async function fixture(run, globals = {}) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Answer = componentModule('components/AnswerQuoteSelection.tsx', {
      globals: { window, document, Node: window.Node, Element: window.Element, HTMLElement: window.HTMLElement,
        TextEncoder, TextDecoder, ...globals },
    }).default;
    const update = (html, highlightCode) => React.act(async () => root.render(React.createElement(Answer, { html, highlightCode })));
    await run({ update, document });
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
}

test('settled known code renders token spans without losing literal markup or its final newline', async () => {
  await fixture(async ({ update, document }) => {
    await update(body, true);
    const [known, unknown, plain] = document.querySelectorAll('pre > code');
    await eventually(() => known.dataset.codeHighlighted === 'python');
    assert.ok(known.querySelector('[data-code-token]'));
    assert.equal(known.textContent, 'def hello():\n    return "<tag>"\n');
    assert.equal(known.querySelector('tag'), null);
    assert.equal(unknown.hasAttribute('data-code-highlighted'), false);
    assert.equal(unknown.textContent, 'unknown plain\n');
    assert.equal(plain.hasAttribute('data-code-highlighted'), false);
  });
});

test('streaming and actively selected answer code retain their original plain DOM', async () => {
  await fixture(async ({ update, document }) => {
    await update(body, false);
    const live = document.querySelector('.language-python');
    assert.equal(live.hasAttribute('data-code-highlighted'), false);
    assert.equal(live.children.length, 0);
    document.getSelection = () => ({ rangeCount: 1, isCollapsed: false,
      getRangeAt: () => ({ intersectsNode: () => true }) });
    await update(body, true);
    const selected = document.querySelector('.language-python');
    assert.equal(selected, live);
    assert.equal(selected.hasAttribute('data-code-highlighted'), false);
    assert.equal(selected.children.length, 0);
    document.getSelection = () => null;
    document.dispatchEvent(new document.defaultView.Event('selectionchange'));
    await eventually(() => selected.dataset.codeHighlighted === 'python');
    assert.equal(selected.textContent, 'def hello():\n    return "<tag>"\n');
  });
});


test('many known fences keep a bounded highlighted subset and all literal text', async () => {
  await fixture(async ({ update, document }) => {
    const html = Array.from({ length: 14 }, (_, index) =>
      `<pre><code class="language-json">{"i":${index}}\n</code></pre>`).join('');
    await update(html, true);
    await eventually(() => document.querySelectorAll('[data-code-highlighted]').length === 12);
    const blocks = [...document.querySelectorAll('pre > code')];
    assert.equal(blocks.length, 14);
    assert.equal(blocks[13].textContent, '{"i":13}\n');
    assert.equal(blocks[13].hasAttribute('data-code-highlighted'), false);
  });
});


test('an answer replaced before scheduled decoration leaves its detached code untouched', async () => {
  const scheduled = new Map();
  await fixture(async ({ update, document }) => {
    await update(body, true);
    const detached = document.querySelector('.language-python');
    await eventually(() => scheduled.size === 1);
    const callback = [...scheduled.values()][0];
    await update('<p>replacement</p>', true);
    assert.equal(scheduled.size, 0);
    callback();
    assert.equal(detached.hasAttribute('data-code-highlighted'), false);
    assert.equal(detached.textContent, 'def hello():\n    return "<tag>"\n');
    assert.match(document.querySelector('[data-assistant-answer]').textContent, /replacement/);
  }, {
    setTimeout(callback) { const id = Symbol(); scheduled.set(id, callback); return id; },
    clearTimeout(id) { scheduled.delete(id); },
  });
});
