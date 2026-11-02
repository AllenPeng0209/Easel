import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML, DOMParser } = require('linkedom');
const id = 'a'.repeat(32);
const other = 'b'.repeat(32);
const snapshot = (overrides = {}) => ({ artifactId: id, projectId: 'ip-one', sessionId: 'session-one', turnId: 'turn-one',
  path: 'reports/bundle.zip', name: 'bundle.zip', mime: 'application/zip', size: 500, status: 'available',
  createdAt: 1, url: `/api/chat/artifacts/${id}/content`, storageMode: 'snapshot', revision: 1, ...overrides });
const member = (index, path, kind = 'image') => ({ index, name: path.split('/').at(-1), path,
  mime: kind === 'html' ? 'text/html' : kind === 'file' ? 'text/plain' : 'image/png', size: 25, kind,
  url: `/api/chat/artifacts/${id}/archive/${index}/content` });
const globals = { AbortController, AbortSignal, Headers, Response, URLSearchParams, TextEncoder, TextDecoder,
  Uint8Array, ArrayBuffer, Blob, btoa, atob, crypto, DOMParser };
function library(options = {}) { return componentModule('lib/snapshotArchive.ts', { user: 'owner', project: 'ip-one', globals, ...options }); }

test('snapshot archive listing preserves sparse original indexes, duplicate basenames and download-only members', () => {
  const { normalizeSnapshotArchiveFiles } = library();
  const files = normalizeSnapshotArchiveFiles(snapshot(), { files: [member(7, 'first/cover.png'), member(19, 'second/cover.png'), member(24, 'README.txt', 'file')] });
  assert.deepEqual(Array.from(files, file => [file.index, file.name, file.url]), [
    [7, 'cover.png', `/api/projects/ip-one/chat/artifacts/${id}/archive/7/content`],
    [19, 'cover.png', `/api/projects/ip-one/chat/artifacts/${id}/archive/19/content`],
    [24, 'README.txt', `/api/projects/ip-one/chat/artifacts/${id}/archive/24/content`],
  ]);
  assert.equal(files[0].preview.storageMode, 'snapshot');
  assert.equal(files[0].preview.sourcePath, 'first/cover.png');
  assert.equal(files[0].preview.baseUrl, undefined);
  assert.equal(files[2].preview, null);
});

test('archive member URLs must match their parent ID and canonical original index in the current IP', () => {
  const { normalizeSnapshotArchiveFiles } = library();
  for (const url of [`https://evil.test/api/chat/artifacts/${id}/archive/7/content`,
    `/api/projects/ip-two/chat/artifacts/${id}/archive/7/content`, `/api/chat/artifacts/${other}/archive/7/content`,
    `/api/chat/artifacts/${id}/archive/0/content`, `/api/chat/artifacts/${id}/archive/07/content`,
    `/api/chat/artifacts/${id}/archive/7/../7/content`, `/api/chat/artifacts/${id}/archive/%37/content`,
    `/api/chat/artifacts/${id}/archive/7/content?path=other`, `/api/media/cover.png`]) {
    assert.throws(() => normalizeSnapshotArchiveFiles(snapshot(), { files: [{ ...member(7, 'cover.png'), url }] }), undefined, url);
  }
  for (const index of [-1, 1.5, Number.MAX_SAFE_INTEGER + 1, '7', true]) {
    assert.throws(() => normalizeSnapshotArchiveFiles(snapshot(), { files: [{ ...member(7, 'cover.png'), index }] }));
  }
  for (const path of ['../cover.png', '/cover.png', 'dir/../cover.png', 'dir\\cover.png', 'dir/\u0000cover.png']) {
    assert.throws(() => normalizeSnapshotArchiveFiles(snapshot(), { files: [{ ...member(7, 'cover.png'), path }] }));
  }
  assert.throws(() => normalizeSnapshotArchiveFiles(snapshot(), { files: [member(7, 'a.png'), member(7, 'b.png')] }));
});

test('archive requests only the fixed current-IP ZIP ID and reports HTTP failures without legacy fallback', async () => {
  const requests = [];
  const { loadSnapshotArchive } = library({ globals: { ...globals, fetch: async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({ error: 'ZIP CRC 校验失败' }), { status: 422, headers: { 'Content-Type': 'application/json' } });
  } } });
  await assert.rejects(loadSnapshotArchive(snapshot(), new AbortController().signal), /ZIP CRC 校验失败/);
  assert.equal(requests[0].url, `/api/projects/ip-one/chat/artifacts/${id}/archive`);
  assert.equal(requests[0].options.redirect, 'error');
  assert.equal(requests[0].options.headers.get('X-Easel-User'), 'owner');
  await assert.rejects(loadSnapshotArchive(snapshot({ url: '/api/media/reports/bundle.zip' }), new AbortController().signal));
  assert.equal(requests.length, 1);
});

async function mountArchive({ files = [], responseStatus = 200, error, relatedStatus = 200, galleryArtifact, artifact = snapshot(), html = '<!doctype html><html><head></head><body><img src="cover.png"></body></html>' } = {}) {
  const { window } = parseHTML('<!doctype html><html><head></head><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({ window, document: window.document, IS_REACT_ACT_ENVIRONMENT: true })) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  const { createRoot } = require('react-dom/client');
  const root = createRoot(window.document.getElementById('root'));
  const requests = [];
  const Component = componentModule(galleryArtifact ? 'components/ArtifactGallery.tsx' : 'components/SnapshotArchivePreview.tsx', { user: 'owner', project: 'ip-one', globals: {
    ...globals, window, document: window.document, ResizeObserver: class { observe() {} disconnect() {} },
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (url.endsWith('/archive')) return new Response(JSON.stringify(error ? { error } : { files }), { status: responseStatus, headers: { 'Content-Type': 'application/json' } });
      if (url.endsWith('/content')) return new Response(html, { headers: { 'Content-Type': 'text/html' } });
      if (url.includes('/related?')) return new Response(new Uint8Array([137, 80, 78, 71]), { status: relatedStatus });
      throw new Error(`unexpected request ${url}`);
    },
  } }).default;
  const render = async next => { await React.act(async () => { root.render(React.createElement(Component, galleryArtifact ? { artifacts: [next] } : { artifact: next })); }); };
  await render(galleryArtifact || artifact);
  return { window, requests, render, async close() {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  } };
}

test('snapshot ZIP DOM previews supported members while every member retains its original download link', async () => {
  const view = await mountArchive({ files: [member(7, 'first/cover.png'), member(19, 'second/cover.png'), member(24, 'README.txt', 'file')] });
  try {
    const { document } = view.window;
    assert.equal(document.querySelector('.artifact-gallery img').getAttribute('src'), `/api/projects/ip-one/chat/artifacts/${id}/archive/7/content`);
    const downloads = Array.from(document.querySelectorAll('[data-archive-member] a[download]'), anchor => anchor.getAttribute('href'));
    assert.deepEqual(downloads, [7, 19, 24].map(index => `/api/projects/ip-one/chat/artifacts/${id}/archive/${index}/content?download=1`));
    await React.act(async () => document.querySelector('[aria-label="下一个产物"]').click());
    assert.equal(document.querySelector('.artifact-gallery img').getAttribute('src'), `/api/projects/ip-one/chat/artifacts/${id}/archive/19/content`);
    assert.ok(document.querySelector(`[download="bundle.zip"]`).getAttribute('href').includes(`/artifacts/${id}/content`));
    assert.ok(view.requests.every(({ url }) => !url.includes('/media/') && !url.includes('/api/artifacts/archive')));
  } finally { await view.close(); }
});

test('a ZIP index failure remains visible alongside the fixed original ZIP download', async () => {
  const view = await mountArchive({ responseStatus: 422, error: 'ZIP CRC 校验失败' });
  try {
    assert.match(view.window.document.querySelector('[role="alert"]').textContent, /ZIP CRC 校验失败/);
    assert.equal(view.window.document.querySelector('[download="bundle.zip"]').getAttribute('href'), `/api/projects/ip-one/chat/artifacts/${id}/content?download=1`);
    assert.equal(view.window.document.querySelector('.artifact-gallery'), null);
  } finally { await view.close(); }
});

test('snapshot HTML member DOM carries logical source metadata into only the bound immutable related reader', async () => {
  const view = await mountArchive({ files: [member(12, 'site/index.html', 'html')] });
  try {
    const frame = view.window.document.querySelector('.artifact-gallery iframe');
    assert.ok(frame, 'HTML should pack into the real opaque preview frame');
    assert.equal(frame.getAttribute('sandbox'), 'allow-scripts');
    assert.ok(view.requests.some(({ url }) => url === `/api/projects/ip-one/chat/artifacts/${id}/archive/12/related?path=cover.png`));
    assert.ok(view.requests.every(({ url }) => !url.includes('/media/') && !url.includes('/api/artifacts/archive')));
  } finally { await view.close(); }
});

function htmlEnvironment() {
  const { document } = parseHTML('<!doctype html><html><head></head><body></body></html>');
  const requests = [];
  const options = { user: 'owner', project: 'ip-one', globals: { ...globals, document,
    window: { location: { pathname: '/', origin: 'http://127.0.0.1:7860' }, dispatchEvent() {} },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return new Response(url.endsWith('/content') ? '<html><head></head><body></body></html>' : new Uint8Array([1]));
    },
  } };
  return { requests, options };
}

test('snapshot root and ZIP HTML bind related assets to the exact immutable route and decode references once', async () => {
  const env = htmlEnvironment();
  const { createReadHtmlRelative } = componentModule('lib/htmlPreview.ts', env.options);
  const signal = new AbortController().signal;
  await createReadHtmlRelative({ url: `/api/chat/artifacts/${id}/content`, storageMode: 'snapshot', sourcePath: 'reports/site/index.html' }, signal)('../shared/chart%20one.png', signal);
  await createReadHtmlRelative({ url: `/api/chat/artifacts/${id}/archive/19/content`, storageMode: 'snapshot', sourcePath: 'site/index.html' }, signal)('literal%2520name.png', signal);
  assert.equal(env.requests[0].url, `/api/projects/ip-one/chat/artifacts/${id}/related?path=..%2Fshared%2Fchart+one.png`);
  assert.equal(env.requests[1].url, `/api/projects/ip-one/chat/artifacts/${id}/archive/19/related?path=literal%2520name.png`);
});

test('snapshot HTML accepts session-bound inbox sources but rejects explicit private references and logical root escapes', async () => {
  const env = htmlEnvironment();
  const { createReadHtmlRelative } = componentModule('lib/htmlPreview.ts', env.options);
  const signal = new AbortController().signal;
  const read = createReadHtmlRelative({ url: `/api/chat/artifacts/${id}/content`, storageMode: 'snapshot', sourcePath: '_inbox/session/site/index.html' }, signal);
  await read('cover.png', signal);
  assert.equal(env.requests.length, 1);
  for (const path of ['../../../_sessions/secret.js', '../_login/secret.png', '../../../_inbox/other/secret.png', '../../../../escape.png', 'file:///tmp/a.png']) await assert.rejects(read(path, signal));
  assert.equal(env.requests.length, 1);
});

test('snapshot source validation never converts external, cross-IP, noncanonical-index or media sources into authenticated reads', async () => {
  const env = htmlEnvironment();
  const { loadPackedHtmlArtifact } = componentModule('lib/htmlPreview.ts', env.options);
  for (const url of [`https://evil.test/api/chat/artifacts/${id}/content`,
    `/api/projects/ip-two/chat/artifacts/${id}/content`, `/api/chat/artifacts/${id}/archive/00/content`,
    `/api/chat/artifacts/${id}/archive/-1/content`, `/api/chat/artifacts/${id}/archive/9007199254740992/content`,
    `/api/chat/artifacts/${id}/archive/1/../2/content`, '/api/media/reports/site/index.html']) {
    await assert.rejects(loadPackedHtmlArtifact({ url, storageMode: 'snapshot', sourcePath: 'reports/site/index.html', baseUrl: '/api/projects/ip-one/media/reports/site/' }, 'token', new AbortController().signal));
  }
  assert.equal(env.requests.length, 0);
});

test('snapshot HTML bootstrap ignores media bases and uses the immutable ID directory', async () => {
  const env = htmlEnvironment();
  const { loadPackedHtmlArtifact } = componentModule('lib/htmlPreview.ts', env.options);
  const html = await loadPackedHtmlArtifact({ url: `/api/chat/artifacts/${id}/content`, storageMode: 'snapshot', sourcePath: 'reports/site/index.html', baseUrl: '/api/projects/ip-one/media/reports/site/' }, 'token', new AbortController().signal);
  const outer = new DOMParser().parseFromString(html, 'text/html');
  const script = outer.querySelector('script').textContent;
  const payload = script.match(/const bundle=JSON.parse\(text\("([A-Za-z0-9+/=]+)"\)\)/)[1];
  const bundle = JSON.parse(Buffer.from(payload, 'base64').toString('utf8'));
  assert.equal(bundle.preview.baseUrl, `http://127.0.0.1:7860/api/projects/ip-one/chat/artifacts/${id}/`);
});


test('a failed ZIP HTML member read exposes its HTTP failure while keeping the immutable ZIP download', async () => {
  const view = await mountArchive({ files: [member(12, 'site/index.html', 'html')], relatedStatus: 409 });
  try {
    assert.match(view.window.document.querySelector('.artifact-html-stage').textContent, /409/);
    assert.equal(view.window.document.querySelector('[download="bundle.zip"]').getAttribute('href'), `/api/projects/ip-one/chat/artifacts/${id}/content?download=1`);
    assert.equal(view.window.document.querySelector('.artifact-gallery iframe'), null);
  } finally { await view.close(); }
});

test('HTML effect reloads when immutable source coordinates or storage mode change without a URL change', async () => {
  const first = { url: `/api/chat/artifacts/${id}/content`, name: 'index.html', kind: 'html',
    storageMode: 'snapshot', sourcePath: 'site/index.html' };
  const view = await mountArchive({ galleryArtifact: first });
  try {
    assert.ok(view.window.document.querySelector('iframe'));
    await view.render({ ...first, sourcePath: 'index.html' });
    assert.equal(view.requests.filter(({ url }) => url.endsWith('/content')).length, 2);
    await view.render({ ...first, sourcePath: 'site/index.html', storageMode: 'legacy-current' });
    assert.equal(view.requests.filter(({ url }) => url.endsWith('/content')).length, 3);
    assert.equal(view.window.document.querySelector('iframe'), null, 'legacy source without its current-media base must fail closed');
  } finally { await view.close(); }
});

test('malformed ZIP listings never become a partially successful gallery', () => {
  const { normalizeSnapshotArchiveFiles } = library();
  for (const payload of [null, {}, { paths: ['a.png'] }, { files: [member(7, 'a.png'), { ...member(19, 'b.png'), size: -1 }] }]) {
    assert.throws(() => normalizeSnapshotArchiveFiles(snapshot(), payload));
  }
});

test('a fixed ZIP member source cannot retain a current-media base when its storage flag is absent or legacy', async () => {
  const env = htmlEnvironment();
  const { loadPackedHtmlArtifact } = componentModule('lib/htmlPreview.ts', env.options);
  for (const storageMode of [undefined, 'legacy-current']) {
    const html = await loadPackedHtmlArtifact({ url: `/api/chat/artifacts/${id}/archive/7/content`, storageMode,
      sourcePath: 'site/index.html', baseUrl: '/api/projects/ip-one/media/site/' }, 'token', new AbortController().signal);
    const script = new DOMParser().parseFromString(html, 'text/html').querySelector('script').textContent;
    const payload = script.match(/const bundle=JSON.parse\(text\("([A-Za-z0-9+/=]+)"\)\)/)[1];
    assert.equal(JSON.parse(Buffer.from(payload, 'base64').toString('utf8')).preview.baseUrl,
      `http://127.0.0.1:7860/api/projects/ip-one/chat/artifacts/${id}/archive/7/`);
  }
});

test('captured root HTML may use ordinary nested underscore assets without granting private system references', async () => {
  const env = htmlEnvironment();
  const { createReadHtmlRelative } = componentModule('lib/htmlPreview.ts', env.options);
  const signal = new AbortController().signal;
  const read = createReadHtmlRelative({ url: `/api/chat/artifacts/${id}/content`, storageMode: 'snapshot', sourcePath: 'reports/site/index.html' }, signal);
  await read('_assets/app.js', signal);
  await read('assets/_cover.png', signal);
  assert.equal(env.requests.length, 2);
  await assert.rejects(read('_sessions/state.js', signal));
  await assert.rejects(read('../../_other/cover.png', signal));
  assert.equal(env.requests.length, 2);
});
