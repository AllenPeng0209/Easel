import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

const source = '<pre><code class="language-python">  first(&quot;&lt;x&gt;&quot;)\n\n</code></pre>' +
  '<pre><code class="language-unknown">\tother\n</code></pre><p>outside</p>';

async function fixture(run) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const writes = [];
  try {
    const Answer = componentModule('components/AnswerQuoteSelection.tsx', { globals: {
      window, document, navigator: { clipboard: { writeText: text => new Promise((resolve, reject) => {
        writes.push({ text, resolve, reject });
      }) } },
    } }).default;
    const update = (html, settled = true, highlightCode = false) =>
      React.act(async () => root.render(React.createElement(Answer, { html, settled, highlightCode })));
    const click = button => React.act(async () => button.click());
    await run({ update, document, click, writes });
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
}

test('every code fence has independent copy state and exact literal source', async () => {
  await fixture(async ({ update, document, click, writes }) => {
    await update(source);
    const buttons = [...document.querySelectorAll('[data-code-copy-button]')];
    assert.equal(buttons.length, 2);
    const [first, second] = buttons;
    assert.ok(first.closest('[data-text-interaction-exclude]'));
    assert.equal(first.closest('pre, code'), null);
    await click(first);
    assert.equal(writes[0].text, '  first("<x>")\n\n');
    assert.equal(first.dataset.codeCopyState, 'pending');
    assert.doesNotMatch(first.textContent, /已复制/);
    assert.equal(second.dataset.codeCopyState, 'idle');
    await click(second);
    assert.equal(writes[1].text, '\tother\n');
    await React.act(async () => writes[1].resolve());
    assert.equal(second.dataset.codeCopyState, 'copied');
    assert.match(second.textContent, /已复制/);
    assert.equal(second.getAttribute('aria-label'), '复制代码块 2');
    assert.equal(second.parentElement.querySelector('[role=status]').textContent, '已复制');
    assert.equal(first.dataset.codeCopyState, 'pending');
    await React.act(async () => writes[0].reject(new Error('denied')));
    assert.equal(first.dataset.codeCopyState, 'failed');
    assert.match(first.textContent, /复制失败/);
    assert.equal(second.dataset.codeCopyState, 'copied');
  });
});

test('late clipboard completion does not update a replaced answer control', async () => {
  await fixture(async ({ update, document, click, writes }) => {
    await update(source);
    const old = document.querySelector('[data-code-copy-button]');
    await click(old);
    await update('<pre><code>new source</code></pre>');
    const fresh = document.querySelector('[data-code-copy-button]');
    await React.act(async () => writes[0].resolve());
    assert.notEqual(fresh, old);
    assert.equal(fresh.dataset.codeCopyState, 'idle');
    assert.equal(old.isConnected, false);
    assert.notEqual(old.dataset.codeCopyState, 'copied');
  });
});


test('streaming code remains plain until its answer settles', async () => {
  await fixture(async ({ update, document }) => {
    await update(source, false);
    const firstCode = document.querySelector('pre > code');
    assert.equal(document.querySelector('[data-code-copy-button]'), null);
    await update(source, true);
    assert.equal(document.querySelector('pre > code'), firstCode);
    assert.equal(document.querySelectorAll('[data-code-copy-button]').length, 2);
  });
});


test('copy reads the unchanged displayed text after grammar token decoration', async () => {
  await fixture(async ({ update, document, click, writes }) => {
    await update(source, true, true);
    const code = document.querySelector('.language-python');
    for (let attempt = 0; attempt < 25 && !code.dataset.codeHighlighted; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(code.dataset.codeHighlighted, 'python');
    await click(document.querySelector('[data-code-copy-button]'));
    assert.equal(writes[0].text, '  first("<x>")\n\n');
    await React.act(async () => writes[0].resolve());
  });
});
