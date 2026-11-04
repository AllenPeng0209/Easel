import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const history = [
  { role: 'user', content: '旧问题' },
  { role: 'assistant', content: '旧答复', status: 'completed', clientTurnId: 'turn-old' },
  { role: 'user', content: '当前问题' },
  { role: 'assistant', content: '当前答复', status: 'completed', clientTurnId: 'turn-current' },
];

test('historical revision uses its exact session and index, guards repeat and displays failure', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  window.getComputedStyle = element => element.style;
  const old = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT']
    .map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true }, document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true },
  });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const lexical = require('lexical');
  const createEditor = options => {
    const editor = lexical.createEditor(options);
    editor.setRootElement = () => {};
    return editor;
  };
  try {
    const Page = componentModule('components/ChatPage.tsx', { externals: {
      lexical: { ...lexical, createEditor }, dompurify: { sanitize: text => text },
    }, globals: { window, document, DOMParser: class {
      parseFromString(html) { return parseHTML(`<html><body>${html}</body></html>`).document; }
    }, navigator: { platform: 'MacIntel' },
      crypto, queueMicrotask, Error, fetch: async () => new Response(JSON.stringify({ max_mb: 50 })), Response,
    } }).default;
    let reject;
    const edits = [], retries = [];
    const props = {
      apiDirect: true, session: { id: 'origin', title: '原会话', created: 1, messages: history },
      onSend: () => true, onStop() {}, onResend(index) { retries.push(index); },
      onEditHistory(id, index) { edits.push([id, index]); return new Promise((_resolve, fail) => { reject = fail; }); },
    };
    const render = fields => React.act(async () => root.render(React.createElement(Page, { ...props, ...fields })));
    await render();
    const edit = document.querySelector('[data-edit-history]');
    assert.ok(edit);
    assert.equal(document.querySelectorAll('[data-edit-history]').length, 1);
    await React.act(async () => { edit.click(); edit.click(); });
    assert.deepEqual(edits, [['origin', 0]]);
    assert.match(edit.textContent, /正在创建分支/);
    await React.act(async () => reject(new Error('没有可验证的历史截止点')));
    assert.match(document.querySelector('[data-edit-history-error]').textContent, /没有可验证的历史截止点/);
    assert.match(document.querySelector('.chat-messages').textContent, /旧问题.*旧答复/s);
    await React.act(async () => document.querySelector('[data-retry-last-turn]').click());
    assert.deepEqual(retries, [2]);
    await React.act(async () => edit.click());
    assert.equal(edits.length, 2);
    await render({ session: { ...props.session, id: 'other' } });
    await React.act(async () => reject(new Error('旧请求不应覆盖新会话')));
    assert.equal(document.querySelector('[data-edit-history-error]'), null);
    const inherited = { ...props.session, fork: { sourceTitle: '上游会话', cutoffIndex: 1 } };
    await render({ session: inherited });
    assert.equal(document.querySelector('[data-edit-history]'), null);
    const published = [...history.slice(0, -1), { ...history.at(-1), events: [{ id: 'receipt', kind: 'publication', publication: {} }] }];
    await render({ session: { ...props.session, messages: published } });
    assert.equal(document.querySelector('[data-retry-last-turn]'), null);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(old)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
