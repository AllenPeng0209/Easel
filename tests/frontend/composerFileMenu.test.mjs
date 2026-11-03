import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

test('file search debounces and cancels stale ownership, with explicit keyboard selection and Escape', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const timers = new Map();
  const searches = [];
  const picks = [];
  const pending = [];
  const load = (query, signal) => new Promise(resolve => searches.push({ query, signal, resolve }));
  const onPick = (option, trigger, signal) => new Promise(resolve => picks.push({ option, trigger, signal, resolve }));
  const { useComposerFileMenu } = componentModule('components/ComposerFileMenu.tsx', { globals: {
    setTimeout(callback, ms) { assert.equal(ms, 120); const key = Symbol(); timers.set(key, callback); return key; },
    clearTimeout(key) { timers.delete(key); },
  } });
  let menu;
  function Fixture({ query }) {
    menu = useComposerFileMenu({ trigger: query === undefined ? undefined
      : { key: 'node', start: 0, end: query.length + 1, raw: '@' + query, query },
      disabled: false, load, onPick, onSelecting: value => pending.push(value) });
    return menu.content;
  }
  const update = query => React.act(() => root.render(React.createElement(Fixture, { query })));
  const tick = () => React.act(() => {
    for (const [key, callback] of timers) { timers.delete(key); callback(); }
  });
  const key = value => React.act(() => assert.equal(menu.keyDown({ key: value, preventDefault() {} }), true));
  try {
    await update('');
    await update('o');
    assert.equal(searches.length, 0);
    await tick();
    assert.deepEqual(searches.map(item => item.query), ['o']);
    await update('other');
    assert.equal(searches[0].signal.aborted, true);
    await React.act(async () => searches[0].resolve({ files: [{ id: 'stale', path: '_inbox/old/stale.png', name: 'stale.png' }] }));
    assert.equal(document.querySelector('[role=option]'), null);
    await tick();
    await React.act(async () => searches[1].resolve({ files: [{ id: 'ready', name: 'ready.png', path: '_inbox/safe/ready.png' }],
      candidates: [{ id: 'a'.repeat(64), name: 'other file.png', mime: 'image/png', size: 8, context: '素材组' }] }));
    assert.equal(document.querySelectorAll('[role=option]').length, 2);
    assert.match(document.querySelector('[aria-label="引用 other file.png"]').textContent, /素材组/);
    assert.equal(picks.length, 0);
    await key('ArrowDown');
    await key('Enter');
    assert.equal(picks[0].option.kind, 'library');
    assert.equal(picks[0].trigger.query, 'other');
    assert.deepEqual(pending, [true]);
    await key('Escape');
    assert.equal(picks[0].signal.aborted, true);
    assert.deepEqual(pending, [true, false]);
    await React.act(async () => picks[0].resolve(true));
    assert.equal(document.querySelector('[role=listbox]'), null);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
