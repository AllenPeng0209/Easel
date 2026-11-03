import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

const file = (key, name, mime, description, status = 'available') => ({
  artifactId: key.repeat(32), projectId: 'default', sessionId: 'session-a', turnId: 'turn-a',
  path: `report/${name}`, name, mime, size: 1536, description, status, createdAt: 100,
  url: `/api/chat/artifacts/${key.repeat(32)}/content`,
  storageMode: 'snapshot', revision: 1, sha256: '0'.repeat(64), reviewStatus: 'unreviewed',
});

test('four mixed deliveries are scannable and preserve exact preview, download and version controls', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true }, document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true },
  });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const artifacts = [
    file('a', 'cover.png', 'image/png', '封面图'),
    file('b', 'outline.json', 'application/json', '结构化大纲'),
    file('c', 'captions.srt', 'application/x-subrip', '剪辑字幕'),
    file('d', 'review.docx', 'application/octet-stream', '审阅文件', 'unavailable'),
  ];
  try {
    const requests = [];
    const Cards = componentModule('components/DeliverableCards.tsx', { globals: { window, document, Response,
      fetch: async url => {
        requests.push(url);
        return requests.length === 2 ? new Response('', { status: 503 })
          : new Response(JSON.stringify({ versions: [artifacts[1]] }));
      },
    } }).default;
    const opened = [];
    await React.act(async () => root.render(React.createElement(Cards, {
      artifacts, onOpenDeliverable: (item, trigger) => opened.push([item.artifactId, trigger]),
    })));
    const overview = document.querySelector('[data-deliverable-overview]');
    assert.equal(overview.querySelectorAll('[data-deliverable-overview-id]').length, 4);
    const row = item => overview.querySelector(`[data-deliverable-overview-id="${item.artifactId}"]`);
    for (const item of artifacts) {
      assert.ok(row(item).textContent.includes(item.name));
      assert.ok(row(item).textContent.includes(item.description));
      assert.match(row(item).textContent, /1\.5KB/);
      if (item.status === 'available') {
        assert.equal(row(item).querySelector('a').getAttribute('href'), item.url + '?download=1');
      } else {
        assert.equal(row(item).querySelector('a, button'), null);
        assert.match(row(item).textContent, /暂不可用/);
      }
    }
    assert.match(row(artifacts[2]).textContent, /SRT.*不支持预览/);
    assert.equal(row(artifacts[2]).querySelector('[data-deliverable-overview-preview]'), null);
    const preview = row(artifacts[1]).querySelector('[data-deliverable-overview-preview]');
    await React.act(async () => preview.click());
    assert.deepEqual(opened, [[artifacts[1].artifactId, preview]]);

    const toggle = document.querySelector('[data-deliverable-details-toggle]');
    const details = document.querySelector('[data-deliverable-detail-cards]');
    assert.equal(details.hasAttribute('hidden'), true);
    await React.act(async () => toggle.click());
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(details.hasAttribute('hidden'), false);
    const version = details.querySelector('[aria-label="查看历史交付版本：outline.json"]');
    assert.ok(version);
    await React.act(async () => version.click());
    assert.equal(version.getAttribute('aria-expanded'), 'true');
    assert.ok(details.querySelector(`[data-history-artifact="${artifacts[1].artifactId}"]`));
    await React.act(async () => toggle.click());
    await React.act(async () => toggle.click());
    assert.equal(details.querySelector('[aria-label="查看历史交付版本：outline.json"]'), version);
    assert.equal(version.getAttribute('aria-expanded'), 'true');
    assert.deepEqual(requests, [`/api/chat/artifacts/${artifacts[1].artifactId}/versions`]);
    const refresh = [...details.querySelectorAll('button')].find(item => item.textContent === '刷新历史列表');
    await React.act(async () => refresh.click());
    assert.match(details.querySelector('[role=alert]').textContent, /读取失败/);
    assert.match(details.textContent, /保留上次.*记录/);
    assert.ok(details.querySelector(`[data-history-artifact="${artifacts[1].artifactId}"]`));

  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
