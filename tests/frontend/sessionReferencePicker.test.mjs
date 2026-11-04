import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

test('summary and paged turn references require an explicitly reviewed exact scoped snapshot', async () => {
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
    const pages = [];
    const inspect = (id, signal, turnId) => new Promise(resolve => snapshots.set(turnId ? `${id}:${turnId}` : id, { signal, resolve }));
    const listTurns = async (id, before) => {
      pages.push([id, before]);
      return before ? { turns: [{ turnId: 'older-turn', label: '第 1 轮' }], before: null }
        : { turns: [{ turnId: 'newer-turn', label: '第 2 轮' }], before: 'before-newer' };
    };
    await React.act(() => root.render(React.createElement(Picker, {
      candidates: [{ id: 'one', title: '旧草稿' }, { id: 'two', title: '下条脚本' }], inspect, listTurns,
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
    await React.act(() => byLabel('预览 下条脚本').click());
    const pendingSummary = snapshots.get('two');
    await React.act(() => document.querySelector('[data-session-reference-mode="turn"]').click());
    assert.equal(pendingSummary.signal.aborted, true);
    await React.act(() => pendingSummary.resolve({ sessionId: 'two', label: '过期摘要',
      snapshotHash: 'b'.repeat(64), preview: [], truncated: false }));
    assert.doesNotMatch(document.body.textContent, /过期摘要/);
    assert.deepEqual(pages, [['two', undefined]]);
    assert.equal(insert(), null);
    await React.act(() => document.querySelector('[data-session-reference-more-turns]').click());
    assert.deepEqual(pages, [['two', undefined], ['two', 'before-newer']]);
    assert.equal(document.querySelectorAll('[data-session-reference-turn]').length, 2);
    const pickOlder = document.querySelector('[data-session-reference-turn="older-turn"]');
    await React.act(() => pickOlder.click());
    const selected = { sessionId: 'two', label: '下条脚本 · 第1轮', snapshotHash: 'c'.repeat(64),
      preview: [{ role: 'assistant', content: '仅此轮的快照' }], truncated: false };
    await React.act(() => snapshots.get('two:older-turn').resolve({ ...selected, turnId: 'wrong-turn' }));
    assert.equal(insert(), null);
    await React.act(() => pickOlder.click());
    await React.act(() => snapshots.get('two:older-turn').resolve({ ...selected, turnId: 'older-turn' }));
    assert.match(document.body.textContent, /当前 IP 的指定轮次.*预览正文/);
    await React.act(() => insert().click());
    assert.equal(inserts.at(-1).turnId, 'older-turn');

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
