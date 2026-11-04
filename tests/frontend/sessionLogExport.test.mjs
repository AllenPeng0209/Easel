import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

test('fetches ZIP for exact scoped session with expected actor, rejecting HTML and denying raw error text', async () => {
  const requests = [];
  let response = new Response(new Uint8Array([80, 75, 3, 4]), { headers: {
    'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="easel-session-deadbeef.zip"',
  } });
  const { fetchSessionLogZip } = componentModule('lib/api.ts', { user: 'admin', project: 'studio', globals: {
    Headers, Response, fetch: async (url, options) => { requests.push([url, options]); return response; },
  } });
  const signal = new AbortController().signal;
  const archive = await fetchSessionLogZip('session A', signal);
  assert.equal(archive.filename, 'easel-session-deadbeef.zip');
  assert.deepEqual([...new Uint8Array(await archive.blob.arrayBuffer())], [80, 75, 3, 4]);
  assert.equal(requests[0][0], '/api/projects/studio/chat/sessions/session%20A/export/logs');
  assert.equal(requests[0][1].headers.get('X-Easel-User'), 'admin');
  assert.equal(requests[0][1].signal, signal);
  response = new Response('<html>login</html>', { headers: { 'Content-Type': 'text/html' } });
  await assert.rejects(fetchSessionLogZip('session A', signal), /ZIP/);
  response = new Response('private upstream data', { status: 403 });
  await assert.rejects(fetchSessionLogZip('session A', signal), error => {
    assert.match(error.message, /权限/);
    assert.doesNotMatch(error.message, /private upstream/);
    return true;
  });
});

test('the technical export modal explains scope, performs one confirmed download and can retry', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const matches = window.HTMLElement.prototype.matches;
  const oldMatches = Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, 'matches');
  Object.defineProperty(window.HTMLElement.prototype, 'matches', { configurable: true, value(selector) {
    return selector === ':focus' ? document.activeElement === this : matches.call(this, selector);
  } });
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Dialog = componentModule('components/SessionLogExportDialog.tsx', { globals: {
      window, document, HTMLElement: window.HTMLElement, Error,
    } }).default;
    let settle, fail;
    const downloads = [];
    await React.act(async () => root.render(React.createElement(Dialog, {
      sessionId: 'exact-session', sessionTitle: '会话 <script>原名</script>',
      onDownload(id, signal) {
        downloads.push([id, signal]);
        return new Promise((resolve, reject) => { settle = resolve; fail = reject; });
      },
      onClose() {},
    })));
    const dialog = document.querySelector('[data-session-log-export-dialog]');
    assert.match(dialog.textContent, /平台管理员.*执行事件.*排除.*引擎/s);
    assert.match(dialog.textContent, /会话 <script>原名<\/script>/);
    assert.equal(dialog.querySelector('script'), null);
    assert.equal(downloads.length, 0);
    const download = dialog.querySelector('[data-download-session-logs]');
    await React.act(async () => { download.click(); download.click(); });
    assert.deepEqual(downloads.map(([id]) => id), ['exact-session']);
    assert.equal(download.hasAttribute('disabled'), true);
    await React.act(async () => fail(new Error('当前没有技术日志导出权限。')));
    assert.match(dialog.querySelector('[role=alert]').textContent, /权限/);
    await React.act(async () => download.click());
    assert.equal(downloads.length, 2);
    await React.act(async () => settle());
    assert.match(dialog.querySelector('[role=status]').textContent, /已交给浏览器/);
  } finally {
    await React.act(() => root.unmount());
    if (oldMatches) Object.defineProperty(window.HTMLElement.prototype, 'matches', oldMatches);
    else delete window.HTMLElement.prototype.matches;
    for (const [name, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
