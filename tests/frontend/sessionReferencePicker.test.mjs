import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

test('only an explicitly reviewed matching scoped session can be inserted', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const previous = Object.fromEntries(['window', 'document', 'HTMLElement', 'IS_REACT_ACT_ENVIRONMENT'].map(
    key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true }, document: { value: document, configurable: true },
    HTMLElement: { value: window.HTMLElement, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true },
  });
  const matches = window.HTMLElement.prototype.matches;
  const matchesDescriptor = Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, 'matches');
  Object.defineProperty(window.HTMLElement.prototype, 'matches', { configurable: true, value(selector) {
    return selector === ':focus' ? document.activeElement === this : matches.call(this, selector);
  } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Picker = componentModule('components/SessionReferencePicker.tsx', {
      globals: { document, window, HTMLElement: window.HTMLElement, TextEncoder },
    }).default;
    const snapshots = new Map();
    const inserts = [];
    const inspect = (id, signal) => new Promise(resolve => snapshots.set(id, { signal, resolve }));
    await React.act(() => root.render(React.createElement(Picker, {
      candidates: [{ id: 'one', title: '旧草稿' }, { id: 'two', title: '下条脚本' }], inspect,
      onInsert: snapshot => { inserts.push(snapshot); return true; }, onClose() {},
    })));
    const byLabel = label => document.querySelector(`[aria-label="${label}"]`);
    const insert = () => document.querySelector('[data-insert-session-reference]');
    assert.equal(insert(), null);
    await React.act(() => byLabel('预览 旧草稿').click());
    await React.act(() => byLabel('预览 下条脚本').click());
    assert.equal(snapshots.get('one').signal.aborted, true);
    await React.act(() => snapshots.get('one').resolve({
      sessionId: 'one', label: '旧草稿', snapshotHash: 'a'.repeat(64), preview: [{ role: 'user', content: '旧预览' }],
    }));
    assert.equal(insert(), null);
    await React.act(() => snapshots.get('two').resolve({
      sessionId: 'two', label: '下条脚本', snapshotHash: 'b'.repeat(64),
      preview: [{ role: 'assistant', content: '<script>内容</script>' }], truncated: true,
    }));
    assert.equal(document.querySelector('script'), null);
    assert.match(document.body.textContent, /预览正文 \d+ 字节/);
    assert.match(document.body.textContent, /截断/);
    assert.deepEqual(inserts, []);
    await React.act(() => insert().click());
    assert.deepEqual(inserts.map(item => [item.sessionId, item.snapshotHash, item.label]), [
      ['two', 'b'.repeat(64), '下条脚本'],
    ]);
  } finally {
    await React.act(() => root.unmount());
    if (matchesDescriptor) Object.defineProperty(window.HTMLElement.prototype, 'matches', matchesDescriptor);
    else delete window.HTMLElement.prototype.matches;
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
