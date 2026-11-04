import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const id = 'a'.repeat(32);
const sha = 'b'.repeat(64);
const href = `/api/projects/studio/chat/artifacts/${id}/content`;
const file = {
  artifactId: id, projectId: 'studio', sessionId: 'session-a', turnId: 'turn-a',
  name: 'plan.docx', path: 'docs/plan.docx', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  status: 'available', size: 2048, createdAt: 1, storageMode: 'snapshot', revision: 2,
  sha256: sha, reviewStatus: 'unreviewed', url: `/api/chat/artifacts/${id}/content`,
};
const pdf = (hash = sha, fontStatus = 'checked') => new Response('%PDF-1.7\nexample', { headers: {
  'Content-Type': 'application/pdf', 'X-Easel-Office-Source-Sha256': hash,
  'X-Easel-Office-Font-Status': fontStatus, 'X-Easel-Office-Font-Source': fontStatus === 'checked' ? 'declared_inventory' : 'unknown',
  'X-Easel-Office-Missing-Font-Count': '2',
  'X-Easel-Office-Missing-Fonts': encodeURIComponent(JSON.stringify(['思源宋体'])),
} });

test('office capability is limited to fixed DOCX and PPTX rather than legacy live paths', () => {
  const { deliverablePreviewCapability } = componentModule('lib/deliverables.ts', { project: 'studio' });
  assert.equal(deliverablePreviewCapability(file), 'office');
  assert.equal(deliverablePreviewCapability({ ...file, name: 'plan.pptx', path: 'docs/plan.pptx',
    mime: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' }), 'office');
  assert.equal(deliverablePreviewCapability({ ...file, storageMode: 'legacy-current' }), null);
  assert.equal(deliverablePreviewCapability({ ...file, name: 'plan.doc', path: 'docs/plan.doc', mime: 'application/msword' }), null);
});

test('bound Office PDF shows checked fonts, retains original download, and rejects another snapshot hash', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const old = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true }, document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true },
  });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const requests = [];
  const revoked = [];
  let next = Response.json({ detail: { code: 'converter_unavailable', message: 'internal path' } }, { status: 503 });
  class PreviewURL extends URL {
    static createObjectURL() { return 'blob:fixed-office-pdf'; }
    static revokeObjectURL(value) { revoked.push(value); }
  }
  try {
    const Preview = componentModule('components/OfficeSnapshotPreview.tsx', { user: 'owner', project: 'studio',
      globals: { window, document, URL: PreviewURL, Response, Headers, fetch: async (url, options) => {
        requests.push([url, options]); return next;
      } },
    }).default;
    await React.act(async () => root.render(React.createElement(Preview, { file })));
    assert.match(document.querySelector('[role="alert"]').textContent, /转换服务.*未安装|转换服务.*不可用/);
    assert.doesNotMatch(document.body.textContent, /internal path/);
    assert.equal(document.querySelector('a[download]').getAttribute('href'), href + '?download=1');
    assert.ok(!document.querySelector('[data-office-pdf]'));

    const retry = () => document.querySelector('[data-office-preview-retry]');
    next = pdf('c'.repeat(64));
    await React.act(async () => retry().click());
    assert.match(document.querySelector('[role="alert"]').textContent, /不匹配/);
    assert.ok(!document.querySelector('[data-office-pdf]'));

    next = pdf();
    await React.act(async () => retry().click());
    assert.equal(document.querySelector('[data-office-pdf]').getAttribute('src'), 'blob:fixed-office-pdf');
    assert.match(document.querySelector('[data-office-font-status]').textContent, /本机未找到 2 种字体.*思源宋体.*显示其中 1 种/s);
    assert.deepEqual(requests.map(([url]) => url), Array(3).fill(href.replace('/content', '/office-preview')));
    assert.ok(requests.every(([, options]) => options.headers.get('X-Easel-User') === 'owner'));

    next = pdf(sha, 'unknown');
    await React.act(async () => retry().click());
    assert.match(document.querySelector('[data-office-font-status]').textContent, /未能核验/);
    assert.doesNotMatch(document.querySelector('[data-office-font-status]').textContent, /思源宋体/);
  } finally {
    await React.act(() => root.unmount());
    assert.ok(revoked.includes('blob:fixed-office-pdf'));
    for (const [name, descriptor] of Object.entries(old)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
