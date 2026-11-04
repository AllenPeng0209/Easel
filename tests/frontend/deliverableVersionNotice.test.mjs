import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

function file(id, revision) {
  const artifactId = id.repeat(32);
  return { artifactId, projectId: 'default', sessionId: 's', turnId: 't' + id, path: 'report.json',
    name: 'report.json', mime: 'application/json', size: 9, status: 'available', createdAt: 1,
    storageMode: 'snapshot', revision, sha256: id.repeat(64), url: '/api/chat/artifacts/' + artifactId + '/content' };
}

test('checking newer fixed versions keeps the exact selected ID and prior result during a failed recheck', async () => {
  const { window, document } = parseHTML('<div id="root"></div>');
  const keys = ['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'];
  const old = keys.map(key => Object.getOwnPropertyDescriptor(globalThis, key));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const requests = [];
    let settle;
    const selected = file('a', 1);
    const newer = file('b', 2);
    const Notice = componentModule('components/DeliverableVersionNotice.tsx', { globals: {
      window, document, fetch: async url => {
        requests.push(url);
        return requests.length === 1 ? Response.json({ versions: [selected, newer, { ...file('c', 9), sessionId: 'other' }] })
          : new Promise(resolve => { settle = resolve; });
      },
    } }).default;
    await React.act(async () => root.render(React.createElement(Notice, { file: selected })));
    const button = document.querySelector('[data-check-delivery-versions]');
    await React.act(async () => button.click());
    assert.match(document.body.textContent + document.getElementById('root').textContent, /V2/);
    assert.doesNotMatch(document.getElementById('root').textContent, /V9/);
    await React.act(async () => button.click());
    assert.match(document.getElementById('root').textContent, /V2/);
    await React.act(async () => settle(new Response('', { status: 500 })));
    assert.match(document.getElementById('root').textContent, /V1.*V2.*失败/s);
    assert.deepEqual(requests, Array(2).fill(selected.url.replace('/content', '/versions')));
  } finally {
    await React.act(() => root.unmount());
    keys.forEach((key, index) => { if (old[index]) Object.defineProperty(globalThis, key, old[index]); else delete globalThis[key]; });
  }
});
