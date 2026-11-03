import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

test('slash suggestions expose the Host catalog and keyboard selection never submits', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const chosen = [];
  const { useComposerCommandMenu } = componentModule('components/ComposerCommandMenu.tsx');
  const load = async () => ({ commands: [
    { id: 'topic', token: '/选题', label: '选题', group: '创作', description: '推荐选题', argumentHint: '主题' },
    { id: 'draft', token: '/文案', label: '文案', group: '写作', description: '撰写文案', argumentHint: '平台和主题' },
    { id: 'untrusted', token: 'shell!', label: '伪造', group: '创作', description: '', argumentHint: '' },
  ] });
  let menu;
  function Fixture({ value, command }) {
    menu = useComposerCommandMenu({ value, command, disabled: false, load,
      onPick: (selected, trigger) => chosen.push([selected.id, selected.token, trigger]) });
    return menu.content;
  }
  const render = (value, command) => React.act(() => root.render(React.createElement(Fixture, { value, command })));
  const key = value => React.act(() => assert.equal(menu.keyDown({ key: value, preventDefault() {} }), true));
  try {
    await render('/');
    assert.equal(document.querySelectorAll('[role=option]').length, 2);
    assert.deepEqual([...document.querySelectorAll('.composer-command-group')].map(row => row.textContent), ['创作', '写作']);
    assert.match(document.querySelector('[role=option]').textContent, /参数：主题/);
    await key('ArrowDown');
    assert.match(document.querySelector('[aria-selected=true]').textContent, /\/文案/);
    await key('Enter');
    assert.deepEqual(chosen, [['draft', '/文案', '/']]);
    assert.equal(document.querySelector('[role=listbox]'), null);
    await render('/文');
    assert.equal(document.querySelectorAll('[role=option]').length, 1);
    await key('Escape');
    assert.equal(document.querySelector('[role=listbox]'), null);
    assert.deepEqual(chosen, [['draft', '/文案', '/']]);
    await render('/文案 这是参数');
    assert.equal(document.querySelector('[role=listbox]'), null);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
