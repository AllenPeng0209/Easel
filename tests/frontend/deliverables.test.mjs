import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const artifact = (overrides = {}) => ({ artifactId: 'a'.repeat(32), projectId: 'default', sessionId: 'session-one',
  turnId: 'turn-one', path: 'reports/运营方案.docx', name: '运营方案.docx', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  size: 1200, description: '可交付的运营方案', status: 'available', createdAt: 1000,
  url: `/api/chat/artifacts/${'a'.repeat(32)}/content`, ...overrides });
const event = (artifacts, id = 'present-one') => ({ id, kind: 'artifact', callId: id, artifacts });

function readers(options) { return componentModule('lib/deliverables.ts', options); }

test('artifact-only streaming messages display a real file card without closing prose or generic JSON', () => {
  const MessageBubble = componentModule('components/MessageBubble.tsx').default;
  const html = renderToStaticMarkup(React.createElement(MessageBubble, {
    message: { role: 'assistant', content: '' }, isStreaming: true, events: [event([artifact()])],
  }));
  assert.match(html, /data-presented-file/);
  assert.match(html, /运营方案\.docx/);
  assert.match(html, /下载/);
  assert.doesNotMatch(html, /交付物信息|application\/vnd/);
});

test('explicit declarations keep first path order and the latest description within one turn', () => {
  const { deliverablesFromEvents } = readers();
  const first = artifact();
  const second = artifact({ artifactId: 'b'.repeat(32), path: 'reports/cover.png', name: 'cover.png', mime: 'image/png', url: `/api/chat/artifacts/${'b'.repeat(32)}/content` });
  const updated = artifact({ artifactId: 'c'.repeat(32), description: '修订后的说明', url: `/api/chat/artifacts/${'c'.repeat(32)}/content` });
  const rows = deliverablesFromEvents([event([first, second]), event([updated], 'present-two')]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].path, 'reports/运营方案.docx');
  assert.equal(rows[0].description, '修订后的说明');
  assert.equal(rows[0].artifactId, 'c'.repeat(32));
  assert.equal(rows[1].path, 'reports/cover.png');
});

test('event replay is idempotent and separate turns never collapse the same relative path', () => {
  const { deliverablesFromEvents } = readers();
  const one = event([artifact()]);
  const rows = deliverablesFromEvents([one, one, event([artifact({ turnId: 'turn-two' })], 'present-two')]);
  assert.equal(rows.length, 2);
  assert.deepEqual(Array.from(rows, item => item.turnId), ['turn-one', 'turn-two']);
});

test('tool prose and failed present calls cannot manufacture declared delivery cards', () => {
  const { deliverablesFromEvents } = readers();
  assert.equal(deliverablesFromEvents([
    { id: 'read', kind: 'tool', name: 'read', output: 'reports/运营方案.docx' },
    { id: 'failed', kind: 'tool', name: 'present', status: 'failed', output: { artifacts: [artifact()] } },
  ]).length, 0);
});

test('delivery boundaries reject cross-IP, external URLs, absolute paths and encoded traversal', () => {
  const { deliverablesFromEvents } = readers({ user: 'u1', project: 'ip-one' });
  const valid = artifact({ projectId: 'ip-one' });
  for (const invalid of [
    { projectId: 'ip-two' }, { path: '/Users/private/secret.txt' }, { path: '../secret.txt' },
    { path: 'reports/../secret.txt' }, { path: 'reports/%2e%2e/secret.txt' }, { path: 'reports/a\\b.txt' },
    { path: 'reports/a\u0000.txt' }, { url: 'https://evil.test/download' },
    { url: `/api/projects/ip-two/chat/artifacts/${'a'.repeat(32)}/content` },
    { url: `/api/chat/artifacts/${'b'.repeat(32)}/content` }, { artifactId: '../secret' },
  ]) assert.equal(deliverablesFromEvents([event([{ ...valid, ...invalid }])]).length, 0, JSON.stringify(invalid));
  assert.equal(deliverablesFromEvents([event([valid])]).length, 1);
});

test('all formats retain an authenticated scoped download and descriptions remain inert text', () => {
  const DeliverableCards = componentModule('components/DeliverableCards.tsx', { user: 'u1', project: 'ip-one' }).default;
  const html = renderToStaticMarkup(React.createElement(DeliverableCards, { artifacts: [
    artifact({ projectId: 'ip-one', path: 'reports/data.custom', name: 'data.custom', mime: 'application/octet-stream', description: '<script>alert(1)</script>' }),
  ] }));
  assert.match(html, /\/api\/projects\/ip-one\/chat\/artifacts\/a{32}\/content\?download=1/);
  assert.match(html, /download="data.custom"/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /已保存版本|不可变/);
});

test('legacy media extracted from prose is deduplicated against the explicit delivery path', () => {
  const { omitDeclaredArtifacts } = readers();
  const legacy = [
    { url: '/api/media/reports/cover.png', name: 'cover.png', kind: 'image' },
    { url: '/api/media/reports/other.png', name: 'other.png', kind: 'image' },
  ];
  const kept = omitDeclaredArtifacts(legacy, [artifact({ path: 'reports/cover.png', name: 'cover.png' })]);
  assert.equal(kept.length, 1);
  assert.equal(kept[0].name, 'other.png');
});

test('settled artifact-only messages remain visible without parsing absent prose', () => {
  const MessageBubble = componentModule('components/MessageBubble.tsx').default;
  const html = renderToStaticMarkup(React.createElement(MessageBubble, {
    message: { role: 'assistant', content: '', events: [event([artifact()])] },
  }));
  assert.match(html, /data-presented-file/);
  assert.match(html, /运营方案\.docx/);
});

test('HTML preview fetches opaque artifact bytes while relative resources use the validated IP media directory', () => {
  const { deliverablePreview } = readers({ user: 'u1', project: 'ip-one' });
  const html = artifact({ projectId: 'ip-one', path: 'reports/品牌方案/index.html', name: 'index.html', mime: 'text/html' });
  const preview = deliverablePreview(html);
  assert.equal(preview.url, `/api/projects/ip-one/chat/artifacts/${'a'.repeat(32)}/content`);
  assert.equal(preview.baseUrl, '/api/projects/ip-one/media/reports/%E5%93%81%E7%89%8C%E6%96%B9%E6%A1%88/');
  const { previewBaseUrl } = componentModule('lib/artifacts.ts', { user: 'u1', project: 'ip-one' });
  const base = previewBaseUrl(preview, 'http://127.0.0.1:7860');
  assert.equal(new URL('assets/cover.png', base).href,
    'http://127.0.0.1:7860/api/projects/ip-one/media/reports/%E5%93%81%E7%89%8C%E6%96%B9%E6%A1%88/assets/cover.png');
});

test('preview bases reject external origins, cross-IP and hidden traversal rather than trusting metadata', () => {
  const { previewBaseUrl } = componentModule('lib/artifacts.ts', { user: 'u1', project: 'ip-one' });
  const url = `/api/projects/ip-one/chat/artifacts/${'a'.repeat(32)}/content`;
  for (const baseUrl of ['https://evil.test/', '/api/projects/ip-two/media/reports/',
    '/api/projects/ip-one/media/../private/', '/api/projects/ip-one/media/%2e%2e/private/',
    '/api/projects/ip-one/media/reports\\private/', '/api/media/reports/']) {
    assert.equal(previewBaseUrl({ url, baseUrl }, 'http://127.0.0.1:7860'),
      `http://127.0.0.1:7860/api/projects/ip-one/chat/artifacts/${'a'.repeat(32)}/`, baseUrl);
  }
});

test('unavailable declarations stay readable but cannot open or download a file', () => {
  const DeliverableCards = componentModule('components/DeliverableCards.tsx').default;
  const html = renderToStaticMarkup(React.createElement(DeliverableCards, { artifacts: [artifact({ status: 'unavailable' })] }));
  assert.match(html, /运营方案\.docx/);
  assert.match(html, /暂不可用/);
  assert.match(html, /<button[^>]*disabled=""/);
  assert.doesNotMatch(html, /download="/);
});

test('artifact links accept only the backend lowercase hex32 identifier contract', () => {
  const { safeDeliverableHref } = readers();
  for (const artifactId of ['a'.repeat(16), 'a'.repeat(33), 'g'.repeat(32), 'A'.repeat(32), 'abcdefgh']) {
    const file = artifact({ artifactId, url: `/api/chat/artifacts/${artifactId}/content` });
    assert.equal(safeDeliverableHref(file), null, artifactId);
  }
  assert.equal(safeDeliverableHref(artifact()), `/api/chat/artifacts/${'a'.repeat(32)}/content`);
});

test('a declared ZIP keeps the real legacy archive-index request and member gallery from its prose link', async () => {
  const { parseHTML, DOMParser } = require('linkedom');
  const { window } = parseHTML('<!doctype html><html><head></head><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperty(globalThis, 'window', { value: window, configurable: true, writable: true });
  Object.defineProperty(globalThis, 'document', { value: window.document, configurable: true, writable: true });
  Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { value: true, configurable: true, writable: true });
  const { createRoot } = require('react-dom/client');
  const { act } = React;
  const requests = [];
  const root = createRoot(window.document.getElementById('root'));
  try {
    const purifier = require('dompurify')(window);
    const MessageBubble = componentModule('components/MessageBubble.tsx', {
      externals: { dompurify: purifier },
      globals: { window, document: window.document, DOMParser, AbortController, Headers, Response, crypto,
        fetch: async url => {
          requests.push(url);
          return new Response(JSON.stringify({ paths: ['reports/bundle-members/cover.png'] }), {
            headers: { 'Content-Type': 'application/json' },
          });
        },
      },
    }).default;
    await act(async () => {
      root.render(React.createElement(MessageBubble, { message: {
        role: 'assistant', content: '[下载成品包](/api/media/reports/bundle.zip)',
        events: [event([artifact({ path: 'reports/bundle.zip', name: 'bundle.zip', mime: 'application/zip' })])],
      } }));
    });
    assert.deepEqual(requests, ['/api/artifacts/archive?path=reports%2Fbundle.zip']);
    const image = window.document.querySelector('.artifact-gallery img');
    assert.ok(image, 'the real gallery should render the indexed archive member');
    assert.equal(image.getAttribute('src'), '/api/media/reports/bundle-members/cover.png');
    assert.equal(window.document.querySelectorAll('[data-presented-file]').length, 1);
    assert.equal(window.document.querySelector('[download="bundle.zip"]').getAttribute('href'),
      `/api/chat/artifacts/${'a'.repeat(32)}/content?download=1`);
  } finally {
    await act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
