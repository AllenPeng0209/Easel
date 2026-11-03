import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const base = overrides => ({ artifactId: 'a'.repeat(32), projectId: 'default', sessionId: 'session-one', turnId: 'turn-one',
  path: 'reports/result.txt', name: 'result.txt', mime: 'text/plain', size: 12, description: '任务交付',
  status: 'available', createdAt: 1000, url: `/api/chat/artifacts/${'a'.repeat(32)}/content`, ...overrides });
const snapshot = overrides => base({ storageMode: 'snapshot', revision: 1, sha256: 'b'.repeat(64), bundleSha256: null,
  reviewStatus: 'unreviewed', ...overrides });
const events = files => [{ id: 'present', kind: 'artifact', callId: 'call-one', artifacts: files }];
const library = options => componentModule('lib/deliverables.ts', options);
// Linkedom clones HTMLTemplateElement.content and otherwise leaves innerHTML stale.
// Alias its inert test tree so mutation/serialization follows the native browser
// template contract exercised by production; no application component is mocked.
function templateDocument(document) {
  const create = document.createElement.bind(document);
  document.createElement = (name, ...args) => {
    const element = create(name, ...args);
    if (name.toLowerCase() === 'template') Object.defineProperty(element, 'content', { value: element });
    return element;
  };
  return document;
}
function cards(files) {
  const Cards = componentModule('components/DeliverableCards.tsx').default;
  return renderToStaticMarkup(React.createElement(Cards, { artifacts: files }));
}

test('snapshot cards identify the delivered revision and full hashes without fabricating approval', () => {
  const html = cards([snapshot({ bundleSha256: 'c'.repeat(64) })]);
  assert.match(html, /本卡交付 V1/);
  assert.match(html, /未审核/);
  assert.match(html, /未关联人工批准/);
  assert.match(html, /b{64}/);
  assert.match(html, /c{64}/);
  assert.doesNotMatch(html, /已批准|已审核|当前文件/);
});

test('missing and legacy version fields never invent a revision, digest or review record', () => {
  const { normalizeDeliverable } = library();
  for (const value of [base(), base({ storageMode: 'legacy-current', revision: 99, sha256: 'f'.repeat(64), reviewStatus: 'approved' })]) {
    const result = normalizeDeliverable(value);
    assert.equal(result.storageMode, 'legacy-current');
    assert.equal(result.revision, null);
    assert.equal(result.sha256, null);
    const html = cards([value]);
    assert.match(html, /旧交付/);
    assert.match(html, /当前文件/);
    assert.match(html, /未固定历史/);
    assert.match(html, /未关联审核/);
    assert.doesNotMatch(html, /V99|f{64}|已批准/);
  }
});

test('malformed explicit snapshots fail closed instead of silently becoming current-file declarations', () => {
  const { normalizeDeliverable } = library();
  for (const invalid of [{ revision: 0 }, { revision: '1' }, { sha256: null }, { sha256: 'not-a-hash' },
    { bundleSha256: 'broken' }, { revision: undefined }]) assert.equal(normalizeDeliverable(snapshot(invalid)), null);
  assert.equal(normalizeDeliverable(snapshot({ reviewStatus: 'approved' })).reviewStatus, 'unreviewed');
});

test('historical same-path deliveries keep their own ID, version and digest across turns', () => {
  const { deliverablesFromEvents } = library();
  const first = snapshot();
  const second = snapshot({ artifactId: 'd'.repeat(32), turnId: 'turn-two', revision: 2, sha256: 'e'.repeat(64),
    url: `/api/chat/artifacts/${'d'.repeat(32)}/content` });
  const result = deliverablesFromEvents(events([first, second]));
  assert.equal(result.length, 2);
  const html = cards(result);
  assert.match(html, /本卡交付 V1/);
  assert.match(html, /本卡交付 V2/);
  assert.match(html, /b{64}/);
  assert.match(html, /e{64}/);
});

test('snapshot HTML keeps immutable source coordinates and never carries a current-media base', () => {
  const { deliverablePreview } = library();
  const preview = deliverablePreview(snapshot({ path: 'reports/page.html', name: 'page.html', mime: 'text/html', bundleSha256: 'c'.repeat(64) }));
  assert.equal(preview.url, `/api/chat/artifacts/${'a'.repeat(32)}/content`);
  assert.equal(preview.storageMode, 'snapshot');
  assert.equal(preview.sourcePath, 'reports/page.html');
  assert.equal(preview.baseUrl, undefined);
});

test('only snapshot ZIP prose indexes are suppressed; legacy archive behavior stays available', () => {
  const { legacyArchivesForDeliverables } = library();
  const paths = ['reports/fixed.zip', 'reports/old.zip', 'reports/other.zip'];
  const result = legacyArchivesForDeliverables(paths, [snapshot({ path: paths[0] }), base({ path: paths[1] })]);
  assert.deepEqual(Array.from(result), ['reports/old.zip', 'reports/other.zip']);
});

test('snapshot prose links bind exact current-IP paths to fixed IDs while preserving download and fragments', () => {
  const { pinSnapshotLinks } = library();
  const { DOMParser, parseHTML } = require('linkedom');
  const WrappedParser = class extends DOMParser {
    parseFromString(html, type) { return super.parseFromString(`<html><head></head><body>${html}</body></html>`, type); }
  };
  const { pinSnapshotLinks: pin } = componentModule('lib/deliverables.ts', { globals: { DOMParser: WrappedParser, document: templateDocument(parseHTML('<html><body></body></html>').document) } });
  assert.equal(typeof pinSnapshotLinks, 'function');
  const html = pin('<a href="/api/media/reports/result.txt?download=1#part">下载</a><a href="/api/media/other/result.txt">别的</a><a href="https://other.test/reports/result.txt">外部</a>', [snapshot()]);
  const document = new WrappedParser().parseFromString(html, 'text/html');
  const hrefs = Array.from(document.querySelectorAll('a'), link => link.getAttribute('href'));
  assert.deepEqual(hrefs, [`/api/chat/artifacts/${'a'.repeat(32)}/content?download=1#part`, '/api/media/other/result.txt', 'https://other.test/reports/result.txt']);
});

test('history candidates must match the original card IP, session and exact path', () => {
  const { normalizeDeliverableVersions } = library();
  const rows = normalizeDeliverableVersions(snapshot(), [snapshot(),
    snapshot({ artifactId: 'd'.repeat(32), revision: 2, url: `/api/chat/artifacts/${'d'.repeat(32)}/content` }),
    snapshot({ sessionId: 'another' }), snapshot({ path: 'other/result.txt' }), snapshot({ projectId: 'other' }),
  ]);
  assert.equal(rows.length, 2);
  assert.ok(rows.every(row => row.sessionId === 'session-one' && row.path === 'reports/result.txt'));
});

test('malformed snapshot paths block mutable prose and gallery fallbacks', () => {
  const { rejectedSnapshotPaths, omitDeclaredArtifacts, legacyArchivesForDeliverables } = library();
  const rejected = rejectedSnapshotPaths(events([snapshot({ sha256: null })]));
  assert.deepEqual(Array.from(rejected), ['reports/result.txt']);
  assert.equal(omitDeclaredArtifacts([{ url: '/api/media/reports/result.txt', name: 'result.txt', kind: 'html' }], [], rejected).length, 0);
  assert.equal(legacyArchivesForDeliverables(['reports/result.txt'], [], rejected).length, 0);
});

test('loading history shows newer deliveries without replacing the original card download', async () => {
  const { parseHTML } = require('linkedom');
  const { window } = parseHTML('<html><head></head><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  templateDocument(window.document);
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true, writable: true },
    document: { value: window.document, configurable: true, writable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true, writable: true } });
  const { createRoot } = require('react-dom/client');
  const requests = [];
  const root = createRoot(window.document.getElementById('root'));
  try {
    const Cards = componentModule('components/DeliverableCards.tsx', { globals: { window, document: window.document,
      AbortController, AbortSignal, Headers, Response,
      fetch: async url => { requests.push(url); return new Response(JSON.stringify({ versions: [snapshot(),
        snapshot({ artifactId: 'd'.repeat(32), revision: 2, sha256: 'e'.repeat(64), turnId: 'turn-two', url: `/api/chat/artifacts/${'d'.repeat(32)}/content` }),
      ] }), { headers: { 'Content-Type': 'application/json' } }); },
    } }).default;
    await React.act(async () => root.render(React.createElement(Cards, { artifacts: [snapshot()] })));
    assert.equal(requests.length, 0);
    const button = window.document.querySelector('[aria-label="查看历史交付版本：result.txt"]');
    assert.ok(button);
    await React.act(async () => button.click());
    assert.deepEqual(requests, [`/api/chat/artifacts/${'a'.repeat(32)}/versions`]);
    assert.match(window.document.body.textContent, /该路径最新交付/);
    assert.match(window.document.body.textContent, /V2/);
    assert.equal(window.document.querySelector('.deliverable-download').getAttribute('href'), `/api/chat/artifacts/${'a'.repeat(32)}/content?download=1`);
    assert.doesNotMatch(window.document.body.textContent, /当前文件/);
  } finally {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  }
});

test('snapshot ZIP cards and prose use fixed member indexes without calling the legacy current-file index', async () => {
  const { parseHTML, DOMParser } = require('linkedom');
  const { window } = parseHTML('<html><head></head><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  templateDocument(window.document);
  class BrowserParser extends DOMParser {
    parseFromString(html, type) { return super.parseFromString(/^\s*(?:<!doctype|<html)/i.test(html) ? html : `<html><head></head><body>${html}</body></html>`, type); }
  }
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true, writable: true }, document: { value: window.document, configurable: true, writable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true, writable: true } });
  const { createRoot } = require('react-dom/client');
  const root = createRoot(window.document.getElementById('root'));
  const requests = [];
  try {
    const MessageBubble = componentModule('components/MessageBubble.tsx', { externals: { dompurify: require('dompurify')(window) },
      globals: { window, document: window.document, DOMParser: BrowserParser, Headers, Response, crypto,
        fetch: async url => {
          requests.push(url);
          return new Response(JSON.stringify({ files: [
            { index: 4, name: 'cover.png', path: 'cover.png', mime: 'image/png', size: 10, kind: 'image', url: `/api/chat/artifacts/${'a'.repeat(32)}/archive/4/content` },
            { index: 9, name: 'notes.txt', path: 'notes.txt', mime: 'text/plain', size: 12, kind: 'file', url: `/api/chat/artifacts/${'a'.repeat(32)}/archive/9/content` },
          ] }), { headers: { 'Content-Type': 'application/json' } });
        },
      },
    }).default;
    await React.act(async () => root.render(React.createElement(MessageBubble, { message: {
      role: 'assistant', content: '[成品包](/api/media/reports/fixed.zip)', events: events([snapshot({ path: 'reports/fixed.zip', name: 'fixed.zip', mime: 'application/zip' })]),
    } })));
    assert.equal(requests.length, 0);
    const prose = Array.from(window.document.querySelectorAll('a')).find(link => link.textContent === '成品包');
    assert.equal(prose.getAttribute('href'), `/api/chat/artifacts/${'a'.repeat(32)}/content`);
    const card = window.document.querySelector('[aria-label="查看文件：reports/fixed.zip"]');
    await React.act(async () => card.click());
    assert.deepEqual(requests, [`/api/chat/artifacts/${'a'.repeat(32)}/archive`]);
    assert.equal(window.document.querySelector('.artifact-gallery img').getAttribute('src'), `/api/chat/artifacts/${'a'.repeat(32)}/archive/4/content`);
    assert.equal(window.document.querySelector('[data-archive-member="9"] a').getAttribute('href'), `/api/chat/artifacts/${'a'.repeat(32)}/archive/9/content?download=1`);
  } finally {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
});

async function versionDOM(fetch, callback) {
  const { parseHTML, DOMParser } = require('linkedom');
  const { window } = parseHTML('<html><head></head><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  templateDocument(window.document);
  class BrowserParser extends DOMParser { parseFromString(html, type) {
    return super.parseFromString(/^\s*(?:<!doctype|<html)/i.test(html) ? html : `<html><head></head><body>${html}</body></html>`, type);
  } }
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true, writable: true }, document: { value: window.document, configurable: true, writable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true, writable: true } });
  const root = require('react-dom/client').createRoot(window.document.getElementById('root'));
  try {
    const load = file => componentModule(file, { externals: { dompurify: require('dompurify')(window) },
      globals: { window, document: window.document, DOMParser: BrowserParser, Headers, Response, TextEncoder, TextDecoder, crypto, fetch } });
    await callback({ window, root, load });
  } finally {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
}

test('StrictMode history failures can be retried without replacing the pinned delivery', async () => {
  let calls = 0;
  await versionDOM(async () => { calls++; return new Response(JSON.stringify({ versions: [snapshot()] }), {
    status: calls === 1 ? 503 : 200, headers: { 'Content-Type': 'application/json' },
  }); }, async ({ window, root, load }) => {
    const Cards = load('components/DeliverableCards.tsx').default;
    await React.act(async () => root.render(React.createElement(React.StrictMode, {}, React.createElement(Cards, { artifacts: [snapshot()] }))));
    await React.act(async () => window.document.querySelector('[aria-label="查看历史交付版本：result.txt"]').click());
    assert.match(window.document.querySelector('[role="alert"]').textContent, /读取失败/);
    const retry = Array.from(window.document.querySelectorAll('button')).find(button => button.textContent === '重试');
    await React.act(async () => retry.click());
    assert.equal(calls, 2);
    assert.equal(window.document.querySelector('[role="alert"]'), null);
    assert.equal(window.document.querySelector('.deliverable-download').getAttribute('href'), `/api/chat/artifacts/${'a'.repeat(32)}/content?download=1`);
  });
});

test('a card replacement cancels its old history read and does not publish the late version list', async () => {
  let finish;
  let signal;
  await versionDOM(async (_url, options) => { signal = options.signal; return new Promise(resolve => { finish = resolve; }); },
    async ({ window, root, load }) => {
      const Cards = load('components/DeliverableCards.tsx').default;
      await React.act(async () => root.render(React.createElement(Cards, { artifacts: [snapshot()] })));
      await React.act(async () => window.document.querySelector('[aria-label="查看历史交付版本：result.txt"]').click());
      const next = snapshot({ artifactId: 'd'.repeat(32), revision: 2, sha256: 'e'.repeat(64), url: `/api/chat/artifacts/${'d'.repeat(32)}/content` });
      await React.act(async () => root.render(React.createElement(Cards, { artifacts: [next] })));
      assert.equal(signal.aborted, true);
      await React.act(async () => finish(new Response(JSON.stringify({ versions: [snapshot({ revision: 99 })] }), { headers: { 'Content-Type': 'application/json' } })));
      assert.equal(window.document.querySelector('[aria-label="查看历史交付版本：result.txt"]').getAttribute('aria-expanded'), 'false');
      assert.doesNotMatch(window.document.body.textContent, /V99/);
      assert.equal(window.document.querySelector('.deliverable-download').getAttribute('href'), `/api/chat/artifacts/${'d'.repeat(32)}/content?download=1`);
    });
});

test('invalid snapshot metadata stops exact mutable prose links and shows a visible failure', async () => {
  const requests = [];
  await versionDOM(async url => { requests.push(url); throw new Error('must not fetch'); }, async ({ window, root, load }) => {
    const MessageBubble = load('components/MessageBubble.tsx').default;
    await React.act(async () => root.render(React.createElement(MessageBubble, { message: { role: 'assistant',
      content: '[result](/api/media/reports/result.txt)', events: events([snapshot({ sha256: null })]),
    } })));
    assert.match(window.document.querySelector('[role="alert"]').textContent, /版本信息不完整/);
    const link = Array.from(window.document.querySelectorAll('a')).find(item => item.textContent === 'result');
    assert.equal(link.getAttribute('href'), null);
    assert.equal(link.getAttribute('aria-disabled'), 'true');
    assert.equal(requests.length, 0);
  });
});

test('streaming snapshot Markdown images never load the mutable current-media path', async () => {
  await versionDOM(async () => { throw new Error('no index request expected'); }, async ({ window, root, load }) => {
    const MessageBubble = load('components/MessageBubble.tsx').default;
    const cover = snapshot({ path: 'reports/cover.png', name: 'cover.png', mime: 'image/png' });
    await React.act(async () => root.render(React.createElement(MessageBubble, { isStreaming: true,
      message: { role: 'assistant', content: '![封面](/api/media/reports/cover.png)' }, events: events([cover]),
    })));
    assert.equal(window.document.querySelector('img').getAttribute('src'), `/api/chat/artifacts/${'a'.repeat(32)}/content`);
    await React.act(async () => root.render(React.createElement(MessageBubble, { isStreaming: true,
      message: { role: 'assistant', content: '<img src="/api/media/reports/cover.png" srcset="/api/media/reports/cover.png 2x">' },
      events: events([cover]),
    })));
    assert.equal(window.document.querySelector('img').getAttribute('srcset'), null);
    await React.act(async () => root.render(React.createElement(MessageBubble, { isStreaming: true,
      message: { role: 'assistant', content: '![封面](/api/media/reports/cover.png)' }, events: events([snapshot({ ...cover, sha256: null })]),
    })));
    assert.equal(window.document.querySelector('img').getAttribute('src'), null);
  });
});

test('settled linked snapshot images keep one valid pinned anchor instead of nesting links', () => {
  const { parseHTML } = require('linkedom');
  const document = templateDocument(parseHTML('<html><body></body></html>').document);
  const { pinSnapshotLinks } = componentModule('lib/deliverables.ts', { globals: { document } });
  const cover = snapshot({ path: 'reports/cover.png', name: 'cover.png', mime: 'image/png' });
  const html = pinSnapshotLinks('<a href="/api/media/reports/cover.png"><img src="/api/media/reports/cover.png" alt="封面"></a>', [cover], [], true);
  const template = document.createElement('template'); template.innerHTML = html;
  assert.equal(template.content.querySelectorAll('a').length, 1);
  assert.equal(template.content.querySelectorAll('img').length, 0);
  assert.equal(template.content.querySelector('a').getAttribute('href'), `/api/chat/artifacts/${'a'.repeat(32)}/content`);
});

const mediaSnapshots = () => [
  snapshot({ path: 'reports/video.mp4', name: 'video.mp4', mime: 'video/mp4' }),
  snapshot({ artifactId: 'd'.repeat(32), path: 'reports/voice.mp3', name: 'voice.mp3', mime: 'audio/mpeg', url: `/api/chat/artifacts/${'d'.repeat(32)}/content` }),
  snapshot({ artifactId: 'f'.repeat(32), path: 'reports/cover.png', name: 'cover.png', mime: 'image/png', url: `/api/chat/artifacts/${'f'.repeat(32)}/content` }),
];

async function renderMedia(content, inspect, files = mediaSnapshots()) {
  await versionDOM(async () => { throw new Error('no read expected'); }, async ({ window, root, load }) => {
    const MessageBubble = load('components/MessageBubble.tsx').default;
    for (const isStreaming of [true, false]) {
      await React.act(async () => root.render(React.createElement(MessageBubble, { isStreaming,
        message: { role: 'assistant', content, events: events(files) }, events: events(files),
      })));
      inspect(window.document, isStreaming);
    }
  });
}

test('video and audio src plus nested media source bind exact snapshot bytes in streaming and settled messages', async () => {
  await renderMedia('<video src="/api/media/reports/video.mp4"><source src="/api/media/reports/video.mp4"></video><audio src="/api/media/reports/voice.mp3"><source src="/api/media/reports/voice.mp3"></audio>', document => {
    assert.equal(document.querySelector('video').getAttribute('src'), `/api/chat/artifacts/${'a'.repeat(32)}/content`);
    assert.equal(document.querySelector('video source').getAttribute('src'), `/api/chat/artifacts/${'a'.repeat(32)}/content`);
    assert.equal(document.querySelector('audio').getAttribute('src'), `/api/chat/artifacts/${'d'.repeat(32)}/content`);
    assert.equal(document.querySelector('audio source').getAttribute('src'), `/api/chat/artifacts/${'d'.repeat(32)}/content`);
  });
});

test('video poster uses its exact image snapshot without guessing another media path', async () => {
  await renderMedia('<video src="/api/media/other/video.mp4" poster="/api/media/reports/cover.png"></video>', document => {
    assert.equal(document.querySelector('video').getAttribute('poster'), `/api/chat/artifacts/${'f'.repeat(32)}/content`);
    assert.equal(document.querySelector('video').getAttribute('src'), '/api/media/other/video.mp4');
  });
});

test('picture source srcset pins declared candidates and preserves unrelated external candidates', async () => {
  await renderMedia('<picture><source srcset="/api/media/reports/cover.png 1x, https://cdn.test/large.png 2x"><img src="/api/media/reports/cover.png"></picture>', document => {
    assert.equal(document.querySelector('picture source').getAttribute('srcset'), `/api/chat/artifacts/${'f'.repeat(32)}/content 1x, https://cdn.test/large.png 2x`);
  });
});

test('allowed SVG image href and xlink href bind to exact snapshot bytes', async () => {
  await renderMedia('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><image href="/api/media/reports/cover.png" xlink:href="/api/media/reports/cover.png"></image></svg>', document => {
    const image = document.querySelector('svg image');
    assert.ok(image, 'the sanitized SVG image is retained by the actual rendering path');
    assert.equal(image.getAttribute('href'), `/api/chat/artifacts/${'f'.repeat(32)}/content`);
    assert.equal(image.getAttributeNS('http://www.w3.org/1999/xlink', 'href') || image.getAttribute('xlink:href'), `/api/chat/artifacts/${'f'.repeat(32)}/content`);
  });
});

test('invalid snapshot metadata disables media src, source srcset, poster and SVG references instead of reading current files', async () => {
  await renderMedia('<video src="/api/media/reports/video.mp4" poster="/api/media/reports/cover.png"><source src="/api/media/reports/video.mp4"></video><audio src="/api/media/reports/voice.mp3"></audio><picture><source srcset="/api/media/reports/cover.png 1x"><img src="/api/media/reports/cover.png"></picture><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><image href="/api/media/reports/cover.png" xlink:href="/api/media/reports/cover.png"></image></svg>', document => {
    assert.equal(document.querySelector('video').getAttribute('src'), null);
    assert.equal(document.querySelector('video').getAttribute('poster'), null);
    assert.equal(document.querySelector('video source').getAttribute('src'), null);
    assert.equal(document.querySelector('audio').getAttribute('src'), null);
    assert.equal(document.querySelector('picture source').getAttribute('srcset'), null);
    assert.equal(document.querySelector('svg image').getAttribute('href'), null);
    assert.equal(document.querySelector('svg image').getAttribute('xlink:href'), null);
  }, mediaSnapshots().map(file => ({ ...file, sha256: null })));
});

test('compares two fixed snapshots and pins both comparison downloads to the verified versions', async () => {
  const first = snapshot();
  const second = snapshot({ artifactId: 'd'.repeat(32), revision: 2, sha256: 'e'.repeat(64), turnId: 'turn-two',
    url: `/api/chat/artifacts/${'d'.repeat(32)}/content` });
  const cross = snapshot({ artifactId: 'f'.repeat(32), revision: 3, sha256: '1'.repeat(64), sessionId: 'other',
    url: `/api/chat/artifacts/${'f'.repeat(32)}/content` });
  const requests = [];
  const summarize = ({ artifactId, revision, sha256 }) => ({ artifactId, revision, sha256 });
  await versionDOM(async url => {
    requests.push(String(url));
    if (String(url).endsWith('/versions')) return Response.json({ versions: [first, second, cross,
      base({ artifactId: '2'.repeat(32), url: `/api/chat/artifacts/${'2'.repeat(32)}/content` }),
    ] });
    return Response.json({ path: first.path, before: summarize(first), after: summarize(second),
      identical: false, diff: '@@ -1 +1 @@\n-old\n+new\n' });
  }, async ({ window, root, load }) => {
    const Cards = load('components/DeliverableCards.tsx').default;
    await React.act(() => root.render(React.createElement(Cards, { artifacts: [first] })));
    const document = window.document;
    await React.act(() => document.querySelector('[aria-label="查看历史交付版本：result.txt"]').click());
    assert.equal(document.querySelectorAll('[aria-label="比较起始版本"] option').length, 2);
    assert.equal(requests.length, 1);
    await React.act(() => [...document.querySelectorAll('button')].find(button => button.textContent === '比较固定版本').click());
    assert.deepEqual(requests, [
      `/api/chat/artifacts/${first.artifactId}/versions`,
      `/api/chat/artifacts/${second.artifactId}/compare?beforeId=${first.artifactId}`,
    ]);
    const result = document.querySelector('[data-fixed-version-comparison]');
    assert.equal(result.dataset.beforeArtifact, first.artifactId);
    assert.equal(result.dataset.afterArtifact, second.artifactId);
    assert.match(result.textContent, /b{64}/);
    assert.match(result.textContent, /e{64}/);
    assert.match(result.querySelector('[data-diff-line=add]').textContent, /new/);
    assert.deepEqual([...result.querySelectorAll('a')].map(link => [link.textContent, link.getAttribute('href')]), [
      ['下载比较起始版本 V1', first.url + '?download=1'], ['下载比较目标版本 V2', second.url + '?download=1'],
    ]);
    assert.equal(document.querySelector('.deliverable-download').getAttribute('href'), first.url + '?download=1');
  });
});

test('a mismatched comparison receipt stays unavailable while equal bytes and no text hunks are distinct', async () => {
  const first = snapshot();
  const second = snapshot({ artifactId: 'd'.repeat(32), revision: 2, turnId: 'turn-two',
    url: `/api/chat/artifacts/${'d'.repeat(32)}/content` });
  const different = snapshot({ artifactId: 'e'.repeat(32), revision: 2, sha256: 'a'.repeat(64), turnId: 'turn-two',
    url: `/api/chat/artifacts/${'e'.repeat(32)}/content` });
  const summarize = ({ artifactId, revision, sha256 }) => ({ artifactId, revision, sha256 });
  let mode = 'mismatch';
  let calls = 0;
  await versionDOM(async url => {
    calls++;
    assert.match(String(url), /\/compare\?beforeId=/);
    return Response.json({
      path: first.path, before: summarize(first),
      after: mode === 'mismatch' ? { ...summarize(second), sha256: '0'.repeat(64) }
        : summarize(mode === 'equal' ? second : different),
      identical: mode !== 'different', diff: '',
    });
  }, async ({ window, root, load }) => {
    const Compare = load('components/FixedVersionComparison.tsx').default;
    const document = window.document;
    const render = value => React.act(() => root.render(React.createElement(Compare,
      { key: value.artifactId, anchor: first, versions: [first, value] })));
    const click = () => React.act(() => [...document.querySelectorAll('button')]
      .find(button => button.textContent === '比较固定版本').click());
    await render(second);
    await click();
    assert.match(document.querySelector('[role=alert]').textContent, /不匹配/);
    assert.equal(document.querySelector('[data-fixed-version-comparison]'), null);
    mode = 'equal';
    await click();
    assert.match(document.querySelector('[data-fixed-version-comparison]').textContent, /字节完全相同/);
    mode = 'different';
    await render(different);
    await click();
    assert.match(document.querySelector('[data-fixed-version-comparison]').textContent, /文件字节不同/);
    assert.equal(document.querySelector('[data-diff-line]'), null);
    assert.equal(calls, 3);
  });
});

test('closing history aborts the comparison request and never opens a current file', async () => {
  const first = snapshot();
  const second = snapshot({ artifactId: 'd'.repeat(32), revision: 2, sha256: 'e'.repeat(64), turnId: 'turn-two',
    url: `/api/chat/artifacts/${'d'.repeat(32)}/content` });
  const requests = [];
  let pending;
  await versionDOM(async (url, options) => {
    requests.push(String(url));
    if (String(url).endsWith('/versions')) return Response.json({ versions: [first, second] });
    return new Promise(resolve => { pending = { resolve, signal: options.signal }; });
  }, async ({ window, root, load }) => {
    const Cards = load('components/DeliverableCards.tsx').default;
    const document = window.document;
    await React.act(() => root.render(React.createElement(Cards, { artifacts: [first] })));
    const history = document.querySelector('[aria-label="查看历史交付版本：result.txt"]');
    await React.act(() => history.click());
    await React.act(() => [...document.querySelectorAll('button')].find(button => button.textContent === '比较固定版本').click());
    assert.ok(pending);
    await React.act(() => history.click());
    assert.equal(pending.signal.aborted, true);
    await React.act(() => pending.resolve(Response.json({}, { status: 409 })));
    assert.equal(document.querySelector('[data-fixed-version-comparison]'), null);
    assert.equal(document.querySelector('[role=alert]'), null);
    assert.equal(requests.length, 2);
    assert.ok(requests.every(url => url.startsWith('/api/chat/artifacts/')));
  });
});
