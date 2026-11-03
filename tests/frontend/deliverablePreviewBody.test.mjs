import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const artifactId = 'a'.repeat(32);
const content = '/api/chat/artifacts/' + artifactId + '/content';
const file = (changes = {}) => ({
  artifactId, projectId: 'default', sessionId: 'session', turnId: 'turn',
  path: 'deliveries/report.png', name: 'report.png', mime: 'image/png', size: 120,
  description: '已交付', status: 'available', createdAt: 1, url: content,
  storageMode: 'snapshot', revision: 2, sha256: 'b'.repeat(64), ...changes,
});

test('shared preview routes supported formats and downloads by immutable delivery ID', () => {
  const Preview = componentModule('components/DeliverablePreviewBody.tsx').default;
  const render = value => renderToStaticMarkup(React.createElement(Preview, { file: value }));

  assert.ok(render(file()).includes('src="' + content + '"'));
  assert.match(render(file({ path: 'deliveries/report.zip', mime: 'application/zip' })), /正在读取 ZIP 内容/);
  const draft = render(file({ path: 'deliveries/report.md', mime: 'text/markdown' }));
  assert.match(draft, /收起稿件内容/);
  const fallback = render(file({ path: 'deliveries/report.bin', mime: 'application/octet-stream' }));
  assert.ok(fallback.includes('href="' + content + '?download=1"'));
  assert.doesNotMatch(fallback, /\/api\/media\//);

  for (const invalid of [file({ status: 'unavailable' }), file({ projectId: 'other' }),
    file({ url: 'https://example.test/report.png' }), file({ sha256: 'corrupt' })]) {
    assert.match(render(invalid), /暂不可用/);
    assert.doesNotMatch(render(invalid), /href=|src=/);
  }
});

test('the message card opens its exact delivery and passes the actual preview button', async () => {
  const { parseHTML } = require('linkedom');
  const { window } = parseHTML('<html><head></head><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  window.open = () => assert.fail('A side-panel callback must not open a browser tab');
  const keys = ['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'];
  const previous = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true, writable: true },
    document: { value: window.document, configurable: true, writable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true, writable: true },
  });
  const { createRoot } = require('react-dom/client');
  const root = createRoot(window.document.getElementById('root'));
  const openings = [];
  try {
    const Bubble = componentModule('components/MessageBubble.tsx', {
      externals: { dompurify: require('dompurify')(window) },
      globals: { window, document: window.document },
    }).default;
    const message = { role: 'assistant', content: '', events: [
      { id: 'artifact-event', kind: 'artifact', callId: 'declared', artifacts: [
        file(), file({ artifactId: 'd'.repeat(32), path: 'deliveries/missing.png',
          url: '/api/chat/artifacts/' + 'd'.repeat(32) + '/content', status: 'unavailable' }),
      ] },
    ] };
    const render = callback => root.render(React.createElement(Bubble, { message, onOpenDeliverable: callback }));
    const onOpen = (value, trigger) => openings.push([value.artifactId, value.revision, trigger]);
    await React.act(async () => render(onOpen));
    const preview = window.document.querySelector('[aria-label="查看文件：deliveries/report.png"]');
    const unavailable = window.document.querySelector('[aria-label="查看文件：deliveries/missing.png"]');
    assert.equal(unavailable.disabled, true);
    await React.act(async () => { unavailable.click(); preview.click(); });
    assert.equal(openings.length, 1);
    assert.deepEqual(openings[0].slice(0, 2), [artifactId, 2]);
    assert.ok(openings[0][2] === preview, 'Restore focus to the button that opened this preview');
    assert.equal(window.document.querySelector('.deliverable-preview'), null);

    await React.act(async () => render(undefined));
    await React.act(async () => preview.click());
    assert.equal(window.document.querySelector('.deliverable-preview img').getAttribute('src'), content);
  } finally {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
