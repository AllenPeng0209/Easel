import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

const file = (id, revision, name, mime) => ({
  artifactId: id.repeat(32), projectId: 'default', sessionId: 'session-a', turnId: `turn-${revision}`,
  path: `files/${name}`, name, mime, size: 128 + revision, status: 'available', createdAt: revision,
  storageMode: 'snapshot', revision, sha256: id.repeat(64), reviewStatus: 'unreviewed',
  url: `/api/chat/artifacts/${id.repeat(32)}/content`,
});
const summary = ({ artifactId, revision, sha256, mime, size, url }, generation = null) =>
  ({ artifactId, revision, sha256, mime, size, url, generation });

test('image and video comparisons use only verified fixed IDs and identify unknown provenance', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true }, document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true },
  });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const requests = [];
    let response;
    const Compare = componentModule('components/FixedVersionComparison.tsx', { globals: {
      window, document, Response, fetch: async url => { requests.push(url); return Response.json(response); },
    } }).default;
    for (const [name, mime, element] of [['cover.png', 'image/png', 'img'], ['clip.mp4', 'video/mp4', 'video']]) {
      const before = file('a', 1, name, mime);
      const after = file('b', 2, name, mime);
      const generation = { source: 'native_workshop', workshopProjectId: 'c'.repeat(32), renderId: 'd'.repeat(32),
        taskId: 'e'.repeat(32), contentRevision: 3, engineRevision: 4 };
      response = { kind: 'media', path: before.path, identical: false, before: summary(before),
        after: summary(after, element === 'video' ? generation : null) };
      await React.act(async () => root.render(React.createElement(Compare, { key: name, anchor: before, versions: [before, after] })));
      const compare = [...document.querySelectorAll('button')].find(button => button.textContent === '比较固定版本');
      assert.ok(compare);
      await React.act(async () => compare.click());
      const result = document.querySelector('[data-fixed-version-comparison]');
      assert.equal(requests.at(-1), after.url.replace('/content', '/compare') + '?beforeId=' + before.artifactId);
      assert.deepEqual([...result.querySelectorAll(element)].map(node => node.getAttribute('src')), [before.url, after.url]);
      assert.match(result.textContent, /未记录生成参数/);
      if (element === 'video') assert.match(result.textContent, /原生工坊.*工程版本.*V3/s);

      response = { ...response, after: { ...response.after, url: 'https://outside.invalid/foreign' } };
      await React.act(async () => compare.click());
      assert.equal(document.querySelector(element), null);
      assert.match(document.querySelector('[role=alert]').textContent, /不匹配/);
    }
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
